"""What a URDF file names, found and checked without ROS. Standard library only.

A URDF names meshes as ``package://<pkg>/<path>`` or as paths relative to itself. Both are resolved
inside one project folder: an approved root, a pack, or a snapshot of either. Nothing outside it is
ever a candidate, and no ROS lookup, xacro or other program runs.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
from urllib.parse import unquote
import xml.etree.ElementTree as ET

from .project_assets import AssetError, refuse, relative

MAX_XML = 64 * 1024**2
MAX_PACKAGE_XML = 1024**2
WALK_ENTRIES = 200_000
WALK_SECONDS = 30
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".tox"}
PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
XACRO_HELP = (
    "assets__import_path expands xacro files itself (format auto; pass xacro_args for its $(arg ...) values) and never runs the "
    "xacro program, because a xacro file can run code."
)
# What a mesh file can name. Other formats are copied, and their own files are not followed.
TEXT_MESHES = {".dae", ".obj", ".mtl", ".gltf", ".glb"}
OPAQUE_MESHES = {".fbx"}
MTL_KEYS = {"map_ka", "map_kd", "map_ks", "map_ke", "map_ns", "map_d", "map_bump", "bump", "disp", "decal", "norm"}


def is_urdf(path) -> bool:
    return PurePosixPath(str(path)).suffix.lower() == ".urdf"


def is_xacro(path) -> bool:
    return PurePosixPath(str(path)).suffix.lower() == ".xacro"


def check_urdf_path(path) -> None:
    """Refuse anything but a ``.urdf`` file name, and say what to do with a xacro one."""
    if is_xacro(path):
        refuse("XACRO", f"{path} is a xacro file, not a URDF. {XACRO_HELP}")
    if not is_urdf(path):
        refuse("UNSUPPORTED_FORMAT", f"Expected a .urdf file, not {path}.")


def _comment_parser():
    return ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))


def parse(data: bytes, name: str = "URDF") -> ET.Element:
    """The ``<robot>`` element, keeping comments, or a refusal naming what is wrong."""
    if len(data) > MAX_XML:
        refuse("LIMIT", f"{name} exceeds 64 MiB.")
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        refuse("URDF_XML", f"{name}: DTDs and entities are not supported.")
    try:
        tree = ET.fromstring(data, parser=_comment_parser())
    except ET.ParseError as exc:
        if b"xacro:" in data:
            refuse("XACRO", f"{name} has xacro content. {XACRO_HELP}")
        refuse("URDF_XML", f"{name} is not valid XML: {exc}")
    if tree.tag != "robot":
        refuse("URDF_XML", f"{name}: the root element must be robot, not {str(tree.tag)[:64]}.")
    for element in tree.iter():
        tag = element.tag
        if isinstance(tag, str) and tag.startswith("{") and tag.split("}")[0].endswith("xacro"):
            refuse("XACRO", f"{name} has xacro elements ({tag.split('}')[1]}). {XACRO_HELP}")
        for value in element.attrib.values():
            if "${" in value or "$(" in value:
                refuse("XACRO", f"{name} has an unexpanded xacro expression ({value[:80]}). {XACRO_HELP}")
    return tree


def references(tree: ET.Element) -> list[ET.Element]:
    """The elements that name a file the robot needs. A Gazebo plugin's filename is not one."""
    found = []
    for section in [*tree.findall("link"), *tree.findall("material")]:
        for element in section.iter():
            if element.tag in ("mesh", "texture"):
                found.append(element)
    return found


class Project:
    """One folder's file names and package names: an index, so nothing outside it can be found."""

    def __init__(self, files, read, name: str = "", symlinks=()):
        self.files = set(files)
        self.read = read
        self.name = name
        self.symlinks = set(symlinks)
        self._named = None

    def _unsafe(self, rel: str) -> bool:
        parts = rel.split("/")
        return any("/".join(parts[:depth]) in self.symlinks for depth in range(1, len(parts) + 1))

    def named_packages(self) -> dict[str, list[str]]:
        """Package name -> folders whose package.xml declares it. A bad package.xml declares nothing."""
        if self._named is None:
            self._named = {}
            for rel in sorted(self.files):
                if PurePosixPath(rel).name != "package.xml":
                    continue
                try:
                    data = self.read(rel)
                    if len(data) > MAX_PACKAGE_XML or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                        continue
                    name = ET.fromstring(data).findtext("name")
                except (OSError, ET.ParseError, AssetError):
                    continue
                if name and name.strip():
                    self._named.setdefault(name.strip(), []).append(_parent(rel))
        return self._named

    def folders_named(self, name: str) -> list[str]:
        folders = set()
        for rel in self.files:
            parts = rel.split("/")[:-1]
            for depth, part in enumerate(parts):
                if part == name:
                    folders.add("/".join(parts[: depth + 1]))
        return sorted(folders)


def _parent(rel: str) -> str:
    return "/".join(rel.split("/")[:-1])


def _join(folder: str, rel: str) -> str:
    return f"{folder}/{rel}" if folder else rel


def project_from_manifest(project_dir, files, name: str = "") -> Project:
    """A verified snapshot: the manifest is the file list, so a late change cannot add a package."""
    project_dir = Path(project_dir)

    def read(rel):
        return (project_dir / rel).read_bytes()

    return Project(files, read, name)


def project_from_folder(root) -> Project:
    """Index a real folder without following a symlink, bounded, skipping version-control folders."""
    root = Path(root)
    files, symlinks = set(), set()
    budget, deadline = [0], time.monotonic() + WALK_SECONDS

    def visit(folder: Path, prefix: str):
        with os.scandir(folder) as entries:
            for entry in entries:
                budget[0] += 1
                if budget[0] > WALK_ENTRIES or time.monotonic() > deadline:
                    refuse("LIMIT", f"{root} is too large to search for ROS packages. Pass a smaller layout_root.")
                rel = prefix + entry.name
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    symlinks.add(rel)
                elif stat.S_ISDIR(mode):
                    if entry.name not in SKIP_DIRS:
                        visit(Path(entry.path), rel + "/")
                elif stat.S_ISREG(mode):
                    files.add(rel)

    visit(root, "")

    def read(rel):
        fd = os.open(root / rel, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            return handle.read(MAX_XML + 1)

    return Project(files, read, root.name, symlinks)


def _problem(kind, code, reference, reason):
    return dict(kind=kind, code=code, reference=reference, reason=reason)


def split_reference(value):
    """``(package, path)`` for a package URI, ``(None, path)`` for a relative one, or a problem dict."""
    if not isinstance(value, str) or not value:
        return _problem("unresolved", "URDF_XML", str(value), "a mesh or texture has no filename")
    if value.startswith("package://"):
        package, _, path = value[len("package://"):].partition("/")
        if not PACKAGE_NAME.fullmatch(package) or set(package) == {"."} or ".." in package:
            return _problem("unresolved", "PACKAGE_NAME", value, f"{package!r} is not a usable package name")
        try:
            return package, relative(path)
        except AssetError as exc:
            return _problem("outside", exc.code, value, f"the path inside the package is refused: {exc}")
    if SCHEME.match(value):
        return _problem("remote", "UNSUPPORTED_URI", value, "only package:// and relative paths are supported")
    if value.startswith("/") or "\\" in value or any(ord(c) < 32 for c in value):
        return _problem("outside", "INVALID_PATH", value, "absolute paths and backslashes are not supported")
    return None, value


def normal_path(folder: str, value: str):
    """*value*, relative to *folder*, as a path from the root, or None if it climbs out of the root."""
    parts = [p for p in folder.split("/") if p]
    for part in value.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _pick(candidates, urdf_folder):
    """Candidate folders, with one above the URDF first: that one breaks a tie."""
    return sorted(candidates, key=lambda c: (not (c == "" or urdf_folder == c or urdf_folder.startswith(c + "/")), c))


def _choose_package(package, paths, project, urdf_folder):
    """``(folder, how, problem)`` for one package, trying package.xml, then folder names, then the root's.

    A folder that holds every wanted path wins. When none holds them all, the folder holding the most
    is still the package folder: the caller maps the files it has and reports only the missing ones,
    instead of condemning every reference of a package that merely lacks one mesh.
    """
    named = project.named_packages().get(package, [])
    tiers = [
        ("package.xml", named),
        ("folder name", project.folders_named(package)),
        ("root folder name", [""] if project.name == package else []),
    ]
    partial = []  # (present paths, tier order, folder, how)
    for order, (how, candidates) in enumerate(tiers):
        complete = [c for c in candidates if all(_join(c, p) in project.files for p in paths)]
        if not complete:
            partial += [(sum(1 for p in paths if _join(c, p) in project.files), order, c, how)
                        for c in candidates]
            continue
        ordered = _pick(complete, urdf_folder)
        first = ordered[0]
        above = first == "" or urdf_folder == first or urdf_folder.startswith(first + "/")
        if len(ordered) > 1 and not above:
            return None, how, ("PACKAGE_AMBIGUOUS", f"package {package} is in several folders: {ordered[:5]}")
        return first, how, None
    if partial:
        present, _, folder, how = max(partial, key=lambda item: (item[0], -item[1]))
        return folder, how, None
    return None, None, (
        "PACKAGE_UNRESOLVED",
        f"no package.xml names {package} and no folder is called {package} inside this folder, and nothing outside "
        f"it is searched. Copy the package in, or make the root a folder that holds it.",
    )


def resolve(entry: str, tree: ET.Element, project: Project) -> dict:
    """Map every file *tree* names to a file in *project*.

    Returns ``mapped`` (reference -> relative file), ``packages`` (package -> folder, how),
    ``problems`` (each with kind, code, reference, reason) and ``package_files`` (the package.xml
    files that make those packages findable in a copy). Refuses nothing, so a caller can report all.
    """
    urdf_folder = _parent(entry)
    mapped, problems, wanted = {}, [], {}
    for element in references(tree):
        value = element.attrib.get("filename", "")
        if value in mapped or value in wanted:
            continue
        split = split_reference(value)
        if isinstance(split, dict):
            if not any(p["reference"] == split["reference"] for p in problems):
                problems.append(split)
            continue
        package, path = split
        if package is not None:
            wanted.setdefault(package, {})[value] = path
            continue
        rel = normal_path(urdf_folder, path)
        if rel is None or rel == "":
            problems.append(_problem("outside", "OUTSIDE_ROOT", value, "the path climbs out of this folder"))
        elif project._unsafe(rel):
            problems.append(_problem("outside", "UNSAFE_ENTRY", value, f"{rel} is a symlink, which is refused"))
        elif rel not in project.files:
            problems.append(_problem("unresolved", "MESH_MISSING", value, f"{rel} is not in this folder"))
        else:
            mapped[value] = rel
    packages, package_files = {}, []
    for package, uris in sorted(wanted.items()):
        paths = list(uris.values())
        unsafe = [p for p in paths if any(project._unsafe(_join(f, p)) for f in _candidates(package, project))]
        if unsafe:
            for uri, path in uris.items():
                if path in unsafe:
                    problems.append(_problem("outside", "UNSAFE_ENTRY", uri, "a symlink on this path is refused"))
            continue
        folder, how, problem = _choose_package(package, paths, project, urdf_folder)
        if problem:
            for uri in uris:
                problems.append(_problem("unresolved", problem[0], uri, problem[1]))
            continue
        packages[package] = dict(folder=folder or ".", found_by=how)
        missing = []
        for uri, path in uris.items():
            rel = _join(folder, path)
            if rel in project.files:
                mapped[uri] = rel
            else:
                missing.append(rel)
                problems.append(_problem(
                    "unresolved", "MESH_MISSING", uri,
                    f"package {package} is at {folder or 'the root'}, but {rel} is not in this folder"))
        if how == "package.xml":
            package_files.append(_join(folder, "package.xml"))
    return dict(mapped=mapped, packages=packages, problems=problems, package_files=package_files)


def _candidates(package, project):
    return [*project.named_packages().get(package, []), *project.folders_named(package), *([""] if project.name == package else [])]


_URI_MESHES = {".gltf", ".glb", ".dae"}


def mesh_reference(referrer: str, name: str) -> str | None:
    """The folder-relative file a mesh's reference means, or None if it leaves the folder.

    glTF and DAE names are URIs and a loader percent-decodes them, so the decoded form is what gets checked: ``%2e%2e/x.bin`` is ``../x.bin``.
    A ``%`` left after decoding could be decoded again by something further along, so it is refused too. OBJ and MTL names are plain
    paths, where ``100%.png`` is an ordinary file; one whose decoded form would leave the folder is still refused, in case a loader decodes it.
    """
    value = name[7:] if name.startswith("file://") else name
    decoded = unquote(value)

    def leaves(text):
        return SCHEME.match(text) or text.startswith("/") or "\\" in text or any(ord(c) < 32 for c in text) or normal_path(_parent(referrer), text) is None

    if leaves(value) or leaves(decoded):
        return None
    if PurePosixPath(referrer).suffix.lower() in _URI_MESHES:
        return None if "%" in decoded else normal_path(_parent(referrer), decoded)
    return normal_path(_parent(referrer), value)


def gltf_uris(suffix: str, data: bytes):
    """The external files a glTF or GLB names (buffers and images that are not embedded), or None if its JSON cannot be read."""
    import json
    import struct

    body = data
    if suffix == ".glb":
        if data[:4] != b"glTF" or len(data) < 20 or data[16:20] != b"JSON":
            return None
        length = struct.unpack_from("<I", data, 12)[0]
        body = data[20:20 + length]
    try:
        document = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(document, dict):
        return None
    names = []
    for key in ("buffers", "images"):
        for item in document.get(key, []) if isinstance(document.get(key), list) else []:
            uri = item.get("uri") if isinstance(item, dict) else None
            if isinstance(uri, str) and not uri.startswith("data:"):
                names.append(uri)
    return names


def mesh_files(rel: str, data: bytes) -> list[str]:
    """Paths a mesh file names as written, for DAE, OBJ and MTL. Unreadable text names nothing."""
    suffix = PurePosixPath(rel).suffix.lower()
    if suffix in (".gltf", ".glb"):
        return gltf_uris(suffix, data) or []
    if suffix == ".dae":
        upper = data[:4096].upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in data.upper():
            return []
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return []
        names = []
        for element in root.iter():
            if isinstance(element.tag, str) and element.tag.split("}")[-1] == "init_from" and element.text:
                names.append(element.text.strip())
        # Effects use init_from for a surface too, naming an image id instead of a file.
        return [n for n in names if "." in PurePosixPath(n).name]
    try:
        text = data.decode("utf-8", "replace")
    except ValueError:
        return []
    names = []
    for line in text.splitlines():
        words = line.split()
        if len(words) < 2:
            continue
        key = words[0].lower()
        if suffix == ".obj" and key == "mtllib":
            names.append(" ".join(words[1:]))
        elif suffix == ".mtl" and key in MTL_KEYS:
            names.append(words[-1])
    return names


def follow_mesh_files(project: Project, meshes) -> dict:
    """The files meshes name, inside the project. What is missing is reported and does not block."""
    found, missing, unfollowable, seen = set(), [], [], set()
    queue = list(meshes)
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        suffix = PurePosixPath(rel).suffix.lower()
        if suffix in OPAQUE_MESHES:
            unfollowable.append(dict(file=rel, reason=f"{suffix} files are copied, and what they name is not followed"))
            continue
        if suffix not in TEXT_MESHES:
            continue
        try:
            data = project.read(rel)
        except OSError as exc:
            unfollowable.append(dict(file=rel, reason=str(exc)[:200]))
            continue
        for name in mesh_files(rel, data):
            target = mesh_reference(rel, name)
            if target and target in project.files and not project._unsafe(target):
                found.add(target)
                queue.append(target)
            else:
                missing.append(dict(reference=name, referenced_by=rel))
    return dict(found=sorted(found), missing=missing, unfollowable=unfollowable)


def closure_result(path, project: Project, folder) -> dict:
    """One URDF's dependencies, in the shape asset_packs gets from its USD helper."""
    path = Path(path)
    folder = Path(folder)
    entry = path.relative_to(folder).as_posix()
    result = dict(path=str(path), layers=[], assets=[], unresolved=[], runtime=[], referenced_by={}, urdf=True)
    try:
        check_urdf_path(path)
        tree = parse(project.read(entry), entry)
    except (AssetError, OSError) as exc:
        result["error"] = f"{getattr(exc, 'code', 'FILESYSTEM')}: {exc}"
        return result
    done = resolve(entry, tree, project)
    followed = follow_mesh_files(project, sorted(set(done["mapped"].values())))
    used = {entry, *done["mapped"].values(), *done["package_files"], *followed["found"]}
    result.update(
        assets=[str(folder / rel) for rel in sorted(used)],
        problems=done["problems"],
        packages=done["packages"],
        missing_textures=followed["missing"],
        unfollowable=followed["unfollowable"],
        root_folder_package=any(p["found_by"] == "root folder name" for p in done["packages"].values()),
    )
    return result


def refusal_for(problems: list[dict], entry: str):
    """Refuse with the first problem's code and the whole list."""
    first = problems[0]
    listed = "; ".join(f"{p['reference']}: {p['reason']}" for p in problems[:5])
    more = f"; and {len(problems) - 5} more" if len(problems) > 5 else ""
    refuse(first["code"], f"{entry} names files that cannot be used. {listed}{more}", problems=problems[:50])


def dae_without_unit(data: bytes) -> bool:
    """A COLLADA file with no unit element. The format says that means metres."""
    head = data[:65536]
    return b"<asset" in head and b"<unit" not in head


def dae_with_unit(data: bytes) -> bytes:
    """The same mesh, stating the default unit. The 5.x importer's converter crashes without it."""
    return re.sub(rb"(<asset\b[^>]*>)", rb'\1<unit name="meter" meter="1"/>', data, count=1)


def rewritten(tree: ET.Element, mapped: dict, project_dir) -> bytes:
    """The URDF with each file it names as an absolute path into *project_dir*. Comments stay: the
    importer reads drive notes from them. For the private snapshot only; sources are never changed."""
    project_dir = Path(project_dir)
    for element in references(tree):
        value = element.attrib.get("filename")
        if value in mapped:
            element.set("filename", str(project_dir / mapped[value]))
    return ET.tostring(tree, encoding="utf-8")

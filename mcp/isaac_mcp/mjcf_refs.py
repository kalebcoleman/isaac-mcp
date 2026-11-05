"""What a MuJoCo MJCF file names, found and checked without running MuJoCo. Standard library only.

An MJCF model reads other files: ``<include>`` files, meshes, textures, height fields, skins, sub-models and
flex files, found relative to the model's own folder (and to ``<compiler meshdir/texturedir/assetdir>``). They are
resolved inside one project folder: an approved root, a pack, or a snapshot of either. Nothing outside it is ever
a candidate: an absolute path, a URI, a backslash or a path that climbs out of the folder is a problem, so a model
cannot make the importer read a file you did not approve.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

from .project_assets import AssetError, refuse
from .urdf_refs import MAX_XML, SCHEME, Project, _parent, _problem, normal_path, refusal_for  # noqa: F401 (re-exported)

MAX_INCLUDE_DEPTH = 16
MAX_FILES = 256
# Elements whose file attribute is relative to the model folder as it is.
_PLAIN = {"include": ("file",), "model": ("file",), "flexcomp": ("file",)}
# Elements whose files are found through <compiler meshdir> or <compiler texturedir>.
_MESH_DIR = {"mesh": ("file",), "hfield": ("file",), "skin": ("file",)}
_TEXTURE_DIR = {"texture": ("file", "fileup", "filedown", "fileleft", "fileright", "filefront", "fileback")}


def is_mjcf(path) -> bool:
    return PurePosixPath(str(path)).suffix.lower() == ".xml"


def check_mjcf_path(path) -> None:
    """Refuse anything but an ``.xml`` file name, and say what to do with a URDF or xacro one."""
    suffix = PurePosixPath(str(path)).suffix.lower()
    if suffix in (".urdf", ".xacro"):
        refuse("UNSUPPORTED_FORMAT", f"{path} is a URDF, not an MJCF: use assets__import_urdf.")
    if not is_mjcf(path):
        refuse("UNSUPPORTED_FORMAT", f"Expected a MuJoCo .xml file, not {path}.")
    if PurePosixPath(str(path)).name == "package.xml":
        refuse("UNSUPPORTED_FORMAT", f"{path} is a ROS package description, not a MuJoCo model.")


def parse(data: bytes, name: str = "MJCF") -> ET.Element:
    """The ``<mujoco>`` element, or a refusal naming what is wrong."""
    if len(data) > MAX_XML:
        refuse("LIMIT", f"{name} exceeds 64 MiB.")
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        refuse("MJCF_XML", f"{name}: DTDs and entities are not supported.")
    try:
        tree = ET.fromstring(data)
    except ET.ParseError as exc:
        refuse("MJCF_XML", f"{name} is not valid XML: {exc}")
    if tree.tag != "mujoco":
        hint = " It looks like a URDF: use assets__import_urdf." if tree.tag == "robot" else ""
        refuse("MJCF_XML", f"{name}: the root element must be mujoco, not {str(tree.tag)[:64]}.{hint}")
    return tree


def _local(tag) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def compiler_dirs(tree: ET.Element) -> dict:
    """The ``<compiler>`` folders a model sets: ``meshdir`` and ``texturedir``, each falling back to ``assetdir``."""
    found = {}
    for element in tree.iter():
        if _local(element.tag) == "compiler":
            for key in ("assetdir", "meshdir", "texturedir"):
                if key in element.attrib and key not in found:
                    found[key] = element.attrib[key]
    return {
        "meshdir": found.get("meshdir", found.get("assetdir", "")),
        "texturedir": found.get("texturedir", found.get("assetdir", "")),
    }


def references(tree: ET.Element) -> list[tuple[str, str, str]]:
    """``(kind, element, file)`` for every file the model names. ``kind`` is include, mesh or texture."""
    found = []
    for element in tree.iter():
        name = _local(element.tag)
        for table, kind in ((_PLAIN, "include"), (_MESH_DIR, "mesh"), (_TEXTURE_DIR, "texture")):
            for attribute in table.get(name, ()):
                if attribute in element.attrib:
                    kind_name = "include" if name in ("include", "model", "flexcomp") else kind
                    found.append((kind_name, name, element.attrib[attribute]))
    return found


def _bad_value(value: str):
    """A problem for a name that must not be followed at all, else None."""
    if not isinstance(value, str) or not value:
        return _problem("unresolved", "MJCF_XML", str(value), "a file reference is empty")
    if SCHEME.match(value):
        return _problem("remote", "UNSUPPORTED_URI", value, "only paths inside the approved folder are supported")
    if value.startswith("/") or "\\" in value or any(ord(c) < 32 for c in value):
        return _problem("outside", "INVALID_PATH", value, "absolute paths and backslashes are not supported")
    return None


def resolve(entry: str, project: Project, read=None) -> dict:
    """Map every file *entry* names, through its includes, to a file in *project*.

    Returns ``mapped`` (reference -> relative file), ``files`` (every file used, the entry and its includes
    included), ``includes``, ``compiler`` (the folder settings) and ``problems`` (each with kind, code,
    reference, reason). Refuses nothing about the references, so a caller can report all of them.
    """
    base = _parent(entry)
    read = read or project.read
    problems, mapped, files, includes = [], {}, {entry}, []
    queue, seen, compiler = [(entry, 0)], set(), None
    while queue:
        current, depth = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        if depth > MAX_INCLUDE_DEPTH or len(seen) > MAX_FILES:
            problems.append(_problem("unresolved", "LIMIT", current, "too many nested or included files"))
            break
        try:
            tree = parse(read(current), current)
        except (AssetError, OSError) as exc:
            problems.append(_problem("unresolved", getattr(exc, "code", "FILESYSTEM"), current, str(exc)[:200]))
            continue
        # The first model file that sets a folder decides it, as the entry file does in MuJoCo's own order.
        found = compiler_dirs(tree)
        if compiler is None:
            compiler = {"meshdir": "", "texturedir": ""}
        for key in compiler:
            if not compiler[key] and found[key]:
                compiler[key] = found[key]
        for kind, element, value in references(tree):
            bad = _bad_value(value)
            if bad:
                if not any(p["reference"] == bad["reference"] for p in problems):
                    problems.append(bad)
                continue
            if kind == "include":
                candidates = [base, _parent(current)] if _parent(current) != base else [base]
            else:
                directory = (compiler or {}).get("meshdir" if kind == "mesh" else "texturedir", "")
                bad = _bad_value(directory) if directory else None
                if bad:
                    problems.append(_problem("outside", bad["code"], directory, f"<compiler> folder: {bad['reason']}"))
                    continue
                candidates = [normal_path(base, directory) if directory else base]
            chosen = None
            for folder in candidates:
                rel = normal_path(folder, value) if folder is not None else None
                if rel is None or rel == "":
                    continue
                if project._unsafe(rel):
                    problems.append(_problem("outside", "UNSAFE_ENTRY", value, f"{rel} is a symlink, which is refused"))
                    chosen = False
                    break
                if rel in project.files:
                    chosen = rel
                    break
            if chosen is False:
                continue
            if chosen is None:
                escapes = all((normal_path(f, value) is None) for f in candidates if f is not None)
                code, text = (("OUTSIDE_ROOT", "the path climbs out of this folder") if escapes
                              else ("MESH_MISSING", "is not in this folder"))
                problems.append(_problem("outside" if escapes else "unresolved", code, value,
                                         text if escapes else f"{value} {text} (looked relative to {candidates[0] or 'the root'})"))
                continue
            mapped[value if kind == "include" else f"{element}:{value}"] = chosen
            files.add(chosen)
            if kind == "include":
                includes.append(chosen)
                if is_mjcf(chosen):
                    queue.append((chosen, depth + 1))
    return dict(mapped=mapped, files=sorted(files), includes=includes, compiler=compiler or {}, problems=problems)


def closure_result(path, project: Project, folder) -> dict:
    """One MJCF's dependencies, in the shape asset_packs gets from its USD helper."""
    path = Path(path)
    folder = Path(folder)
    entry = path.relative_to(folder).as_posix()
    result = dict(path=str(path), layers=[], assets=[], unresolved=[], runtime=[], referenced_by={}, mjcf=True)
    try:
        check_mjcf_path(path)
        found = resolve(entry, project)
    except (AssetError, OSError) as exc:
        result["error"] = f"{getattr(exc, 'code', 'FILESYSTEM')}: {exc}"
        return result
    result.update(
        assets=[str(folder / rel) for rel in found["files"]],
        problems=found["problems"],
        includes=found["includes"],
        compiler=found["compiler"],
    )
    return result

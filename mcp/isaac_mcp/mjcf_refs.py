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
MAX_ELEMENTS = 1_000_000  # XML elements walked in one resolve() call, repeats included
MAX_EXPANSIONS = 512  # every file read, counting repeats and sub-models, for one resolve() call
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
    if PurePosixPath(str(path)).name.lower() == "package.xml":
        refuse("UNSUPPORTED_FORMAT", f"{path} is a ROS package description, not a MuJoCo model.")


def parse(data: bytes, name: str = "MJCF", included: bool = False) -> ET.Element:
    """The ``<mujoco>`` element, or a refusal naming what is wrong.

    An included file may have any root element (MuJoCo's own include files use ``<mujocoinclude>``): only the model
    you start from must be ``<mujoco>``.
    """
    if len(data) > MAX_XML:
        refuse("LIMIT", f"{name} exceeds 64 MiB.")
    if b"\x00" in data:  # UTF-16 or UTF-32 would hide a DTD from the byte check below
        refuse("MJCF_XML", f"{name}: only UTF-8 text is supported.")
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        refuse("MJCF_XML", f"{name}: DTDs and entities are not supported.")
    try:
        tree = ET.fromstring(data)
    except ET.ParseError as exc:
        refuse("MJCF_XML", f"{name} is not valid XML: {exc}")
    if tree.tag != "mujoco" and not included:
        hint = " It looks like a URDF: use assets__import_urdf." if tree.tag == "robot" else ""
        refuse("MJCF_XML", f"{name}: the root element must be mujoco, not {str(tree.tag)[:64]}.{hint}")
    return tree


def _local(tag) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def references(tree: ET.Element) -> list[tuple[str, str, str]]:
    """``(kind, element, file)`` for every file a tree names, in document order. ``kind`` is include, mesh or texture."""
    found = []
    for element in tree.iter():
        name = _local(element.tag)
        for table, kind in ((_PLAIN, "include"), (_MESH_DIR, "mesh"), (_TEXTURE_DIR, "texture")):
            for attribute in table.get(name, ()):
                if attribute in element.attrib:
                    found.append(("include" if name in ("include", "model", "flexcomp") else kind, name, element.attrib[attribute]))
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


def resolve(entry: str, project: Project, read=None, _depth: int = 0, _budget=None, _models=()) -> dict:
    """Map every file *entry* names, through its includes and sub-models, to a file in *project*.

    MuJoCo expands an ``<include>`` where it stands, and a later ``<compiler>`` setting overrides an earlier one for the
    whole model, so the model is walked in document order with includes expanded in place, and every mesh and texture is
    resolved against the final folders. Every ``<compiler>`` folder anywhere is checked, even one that is overridden, and
    every included file is scanned whatever its name.

    Returns ``mapped`` (reference -> relative file), ``files`` (every file used), ``includes``, ``trees`` (each parsed
    file, by name), ``compiler`` (the final folder settings) and ``problems`` (each with kind, code, reference, reason).
    Refuses nothing about the references, so a caller can report all of them.
    """
    base = _parent(entry)
    budget = _budget if _budget is not None else {"n": 0, "elements": 0, "parsed": {}}  # shared with every sub-model, so repeats cannot multiply
    read = read or project.read
    problems, files, includes, trees = [], {entry}, [], {}
    seen = set()  # (reference, code) of each problem, so adding one is constant time
    state = {"assetdir": "", "meshdir": None, "texturedir": None}
    wanted = []  # (kind, element, value) in document order, resolved after the walk
    stack = []

    def note(problem):
        key = (problem["reference"], problem["code"])
        if key not in seen:
            seen.add(key)
            problems.append(problem)

    def check_folder(value, key):
        """A ``<compiler>`` folder must be a relative path that stays inside the approved folder."""
        if value == "":
            return
        bad = _bad_value(value)
        if bad:
            note(_problem("outside", bad["code"], value, f"<compiler {key}>: {bad['reason']}"))
        elif normal_path(base, value) is None:
            note(_problem("outside", "OUTSIDE_ROOT", value, f"<compiler {key}> climbs out of this folder"))

    def locate(value, folders):
        """The first existing project file for *value* under one of *folders*; False for a symlink, None for none."""
        for folder in folders:
            rel = normal_path(folder, value) if folder is not None else None
            if rel is None or rel == "":
                continue
            if project._unsafe(rel):
                note(_problem("outside", "UNSAFE_ENTRY", value, f"{rel} is a symlink, which is refused"))
                return False
            if rel in project.files:
                return rel
        return None

    def missing(value, folders):
        escapes = all(normal_path(f, value) is None for f in folders if f is not None)
        if escapes:
            note(_problem("outside", "OUTSIDE_ROOT", value, "the path climbs out of this folder"))
        else:
            note(_problem("unresolved", "MESH_MISSING", value, f"{value} is not in this folder (looked relative to {folders[0] or 'the root'})"))

    def visit(root, current, depth):
        """Walk a tree in document order without recursion, so a deeply nested file cannot exhaust the stack."""
        pending = [iter([root])]
        while pending:
            element = next(pending[-1], None)
            if element is None:
                pending.pop()
                continue
            budget["elements"] += 1
            if budget["elements"] > MAX_ELEMENTS:
                note(_problem("unresolved", "LIMIT", current, "the model names too many elements"))
                return
            name = _local(element.tag)
            if name == "compiler":
                for key in ("assetdir", "meshdir", "texturedir"):
                    if key in element.attrib:
                        check_folder(element.attrib[key], key)
                        state[key] = element.attrib[key]
            for attribute in _PLAIN.get(name, ()):
                if attribute in element.attrib:
                    follow(name, element.attrib[attribute], current, depth)
            for attribute in _MESH_DIR.get(name, ()):
                if attribute in element.attrib:
                    wanted.append(("mesh", name, element.attrib[attribute]))
            for attribute in _TEXTURE_DIR.get(name, ()):
                if attribute in element.attrib:
                    wanted.append(("texture", name, element.attrib[attribute]))
            pending.append(iter(list(element)))

    def follow(name, value, current, depth):
        bad = _bad_value(value)
        if bad:
            note(bad)
            return
        folders = [base, _parent(current)] if _parent(current) != base else [base]
        chosen = locate(value, folders)
        if chosen is False:
            return
        if chosen is None:
            missing(value, folders)
            return
        mapped[value] = chosen
        files.add(chosen)
        if name == "model":
            if chosen in _models or chosen == entry:  # a model that contains itself
                note(_problem("unresolved", "LIMIT", chosen, "a model includes itself"))
                return
            sub = resolve(chosen, project, read, _depth + 1, budget, (*_models, entry))  # own folders and includes
            for key, rel in sub["mapped"].items():
                mapped[f"{chosen}:{key}"] = rel
            files.update(sub["files"])
            includes.extend(sub["includes"])
            trees.update(sub["trees"])
            for problem in sub["problems"]:
                note(problem)
        elif name == "include":
            includes.append(chosen)
            expand(chosen, depth + 1)

    def expand(rel, depth):
        if rel in stack:  # an include cycle ends here
            return
        budget["n"] += 1
        if depth > MAX_INCLUDE_DEPTH or len(trees) > MAX_FILES or budget["n"] > MAX_EXPANSIONS:
            note(_problem("unresolved", "LIMIT", rel, "too many nested or included files"))
            return
        try:
            as_include = rel != entry  # an entry must be <mujoco>; an include may have any root, so they are cached apart
            tree = budget["parsed"].get((rel, as_include))  # a file included again is parsed once
            if tree is None:
                tree = budget["parsed"][(rel, as_include)] = parse(read(rel), rel, included=as_include)
        except (AssetError, OSError) as exc:
            note(_problem("unresolved", getattr(exc, "code", "FILESYSTEM"), rel, str(exc)[:200]))
            return
        trees[rel] = tree
        stack.append(rel)
        try:
            visit(tree, rel, depth)
        finally:
            stack.pop()

    mapped = {}
    if _depth > MAX_INCLUDE_DEPTH:
        return dict(mapped={}, files=[entry], includes=[], trees={}, compiler={}, problems=[
            _problem("unresolved", "LIMIT", entry, "sub-models are nested too deeply")])
    expand(entry, 0)
    final = {
        "meshdir": state["meshdir"] if state["meshdir"] is not None else state["assetdir"],
        "texturedir": state["texturedir"] if state["texturedir"] is not None else state["assetdir"],
    }
    for kind, element, value in wanted:
        bad = _bad_value(value)
        if bad:
            note(bad)
            continue
        directory = final["meshdir" if kind == "mesh" else "texturedir"]
        folder = normal_path(base, directory) if directory and _bad_value(directory) is None else base
        if folder is None:
            continue  # the folder itself was reported
        chosen = locate(value, [folder])
        if chosen is False:
            continue
        if chosen is None:
            missing(value, [folder])
            continue
        mapped[f"{element}:{value}"] = chosen
        files.add(chosen)
    return dict(mapped=mapped, files=sorted(files), includes=includes, trees=trees, compiler=final, problems=problems)


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

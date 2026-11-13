"""Which file formats the import knows, how to tell them apart, and how a robot bundle names its entry file.

``assets__import_path`` is the one import entry. Its ``format`` is ``auto`` by default: this module reads the file name (and,
for a folder, what it holds) and says which importer applies. The converters that produce a URDF (xacro, SDF) run on the host
over the verified copy of the approved folder and add the generated file to that copy, so the Kit importer only ever reads a
checked URDF. Standard library only.
"""

from __future__ import annotations

from pathlib import PurePosixPath
import re
import xml.etree.ElementTree as ET

from .project_assets import AssetError, refuse

USD_SUFFIXES = {".usd", ".usda", ".usdc", ".usdz"}
MESH_SUFFIXES = {".gltf", ".glb", ".obj", ".stl", ".fbx", ".dae", ".ply"}  # .dae is recognised so the refusal can explain it
SUFFIX_FORMAT = {**{s: "usd" for s in USD_SUFFIXES}, ".urdf": "urdf", ".xacro": "xacro", ".sdf": "sdf", ".world": "sdf", ".yaml": "map", ".yml": "map",
                 **{s: "mesh" for s in MESH_SUFFIXES}}
FORMATS = ("auto", "usd", "urdf", "xacro", "sdf", "mjcf", "mesh", "map")
GENERATED = ".generated.urdf"
_MACRO_NAMES = re.compile(r"(macro|common|inc|materials|utils|defs|properties|inertia)", re.I)
_SKIP_FOLDERS = {".git", "node_modules", "__pycache__", ".venv"}
_MAX_CONFIG = 1024**2
MAX_SNAPSHOT_READ = 256 * 1024**2


def format_of(path) -> str | None:
    """The format a file name belongs to; an ``.xml`` is MJCF (what isn't is refused later with a reason)."""
    name = PurePosixPath(str(path)).name.lower()
    if name == "package.xml" or name == "model.config":
        return None
    suffix = PurePosixPath(name).suffix
    if suffix == ".xml":
        return "mjcf"
    return SUFFIX_FORMAT.get(suffix)


def check_path(path, kind) -> None:
    suffix = PurePosixPath(str(path)).suffix.lower()
    if kind == "xacro":
        if suffix not in (".xacro", ".urdf"):  # some packages keep xacro in a file named .urdf
            refuse("UNSUPPORTED_FORMAT", f"Expected a .xacro file, not {path}.")
    elif kind == "sdf":
        if suffix not in (".sdf", ".world"):
            refuse("UNSUPPORTED_FORMAT", f"Expected a .sdf or .world file, not {path}.")
    elif kind == "map":
        if suffix not in (".yaml", ".yml"):
            refuse("UNSUPPORTED_FORMAT", f"Expected a map .yaml file, not {path}.")
    else:
        refuse("UNSUPPORTED_FORMAT", f"{kind} is not a converted format.")


def generated_name(path: str, extra: str = "") -> str:
    folder, _, name = path.rpartition("/")
    stem = re.sub(r"(\.urdf)?\.(xacro|sdf|world)$|\.urdf$", "", name, flags=re.I)
    made = f"{stem}{extra}{GENERATED}"
    return f"{folder}/{made}" if folder else made


def xacro_generator(args, root_name):
    """A ``Broker.snapshot`` generator that expands a xacro file with the restricted expander."""
    from . import xacro_lite
    from .urdf_refs import Project

    def generate(path, files, read):
        project = Project(files.keys(), read, root_name)
        result = xacro_lite.expand(path, project, args)
        return dict(entry=generated_name(path), data=result["urdf"].encode(),
                    info=dict(tool="xacro_lite", args={k: v for k, v in sorted(result["args"].items())}))

    return generate


def sdf_generator(model, root_name):
    """A ``Broker.snapshot`` generator that writes one SDF model as a URDF."""
    from . import sdf_urdf
    from .urdf_refs import Project

    holder = {}

    def generate(path, files, read):
        project = Project(files.keys(), read, root_name)
        result = sdf_urdf.convert(path, project, model)
        holder.update(result)
        return dict(entry=generated_name(path, f".{re.sub(r'[^A-Za-z0-9_-]', '_', result['model'])[:48]}"), data=result["urdf"].encode(),
                    info=dict(tool="sdf_urdf", model=result["model"], static=result["static"],
                              warnings=result["warnings"][:20], dropped=result["dropped"]))

    generate.result = holder
    return generate


# ---------------------------------------------------------------- bundles

def _is_mujoco(read, rel):
    try:
        head = read(rel)[:4096]
    except (OSError, AssertionError, AssetError):
        return False
    return b"<mujoco" in head and b"<mujocoinclude" not in head


def candidates(files, read, folder="", root_name="") -> list[dict]:
    """The importable entry files a folder holds, best first: ``{"entry", "format", "why", "score"}`` (entry relative to the root).
    *root_name* is the approved root's own folder name, the folder's name when the folder is the root itself."""
    prefix = folder + "/" if folder else ""
    folder_name = (PurePosixPath(folder).name if folder else root_name).lower()
    inner_read, spent = read, [0]

    def read(rel):  # looking through a folder reads no more than one snapshot could hold; past that, files are not candidates
        if spent[0] > MAX_SNAPSHOT_READ:
            refuse("LIMIT", "That folder holds too much to look through.")
        data = inner_read(rel)
        spent[0] += len(data)
        return data

    inside = sorted(f for f in files if f.startswith(prefix) and not set(f.split("/")) & _SKIP_FOLDERS)
    found = []

    def add(entry, fmt, why, score):
        found.append(dict(entry=entry, format=fmt, why=why, score=score))

    for rel in inside:
        name = PurePosixPath(rel).name
        lower = name.lower()
        if lower == "model.config":  # a Gazebo model folder names its SDF
            sdf = None
            try:
                data = read(rel)
                if len(data) <= _MAX_CONFIG and b"<!DOCTYPE" not in data.upper() and b"<!ENTITY" not in data.upper():
                    for node in ET.fromstring(data).iter("sdf"):
                        if node.text and node.text.strip():
                            sdf = node.text.strip()
            except (ET.ParseError, OSError, AssetError):
                pass
            target = f"{PurePosixPath(rel).parent.as_posix()}/{sdf or 'model.sdf'}".lstrip("./")
            target = target if target in files else f"{PurePosixPath(rel).parent.as_posix()}/model.sdf".lstrip("./")
            if target in files:
                add(target, "sdf", "a Gazebo model folder (model.config)", 100)
    seen_sdf = {c["entry"] for c in found}
    includes = set()
    mujoco = []
    for rel in inside:
        fmt = format_of(rel)
        lower = PurePosixPath(rel).name.lower()
        if fmt == "urdf":
            add(rel, "urdf", "a URDF", 60)
        elif fmt == "xacro":
            if _MACRO_NAMES.search(lower) and not lower.endswith(".urdf.xacro"):
                continue
            add(rel, "xacro", "a xacro robot" + (" (a .urdf.xacro entry)" if lower.endswith(".urdf.xacro") else ""), 55 if lower.endswith(".urdf.xacro") else 40)
        elif fmt == "sdf" and rel not in seen_sdf:
            add(rel, "sdf", "an SDF file", 50 if lower == "model.sdf" else 45)
        elif fmt == "mjcf" and _is_mujoco(read, rel):
            mujoco.append(rel)
            try:
                text = read(rel).decode("utf-8", "ignore")
                includes.update(re.findall(r'<include\s+file="([^"]+)"', text))
            except (OSError, AssetError):
                pass
        elif fmt == "usd":
            top = PurePosixPath(rel).stem.lower()
            hinted = top in ("scene", "main", "stage") or (bool(folder_name) and top == folder_name)
            add(rel, "usd", "a USD file", 35 if hinted else 25)
        elif fmt == "mesh":
            add(rel, "mesh", "a mesh file", 20)
        elif fmt == "map":
            try:
                head = read(rel)[:2048]
            except (OSError, AssetError):
                continue
            if b"image:" in head and b"resolution:" in head:
                add(rel, "map", "a ROS map (image, resolution, origin)", 30)
    for rel in mujoco:
        name = PurePosixPath(rel).name
        is_scene = name.lower().startswith("scene")
        included_by_other = any(PurePosixPath(i).name == name for i in includes)
        if is_scene:
            add(rel, "mjcf", "a MuJoCo scene (the robot plus a floor and lights)", 45)
        elif included_by_other:
            add(rel, "mjcf", "a MuJoCo robot model (a scene file includes it)", 58)
        else:
            add(rel, "mjcf", "a MuJoCo model", 50)
    # A mesh is a candidate only when nothing else is: a robot folder's meshes belong to the robot file, they are not alternatives.
    if any(c["format"] != "mesh" for c in found):
        found = [c for c in found if c["format"] != "mesh"]
    # Variants made for another simulator (MuJoCo MJX) are alternatives, never the first choice.
    for c in found:
        if re.search(r"(^|[_-])mjx([_.-]|$)", PurePosixPath(c["entry"]).name.lower()):
            c["score"] -= 3
            c["why"] += " (MJX variant)"
    found.sort(key=lambda c: (-c["score"], c["entry"]))
    return found


def choose(found) -> dict:
    """The one best candidate, or a refusal that lists them (never a guess between equals)."""
    if not found:
        refuse("NOT_FOUND", "No importable file was found in that folder (looked for model.config, .urdf, .xacro, .sdf, MuJoCo .xml, USD and mesh files).")
    top = [c for c in found if c["score"] == found[0]["score"]]
    if len(top) == 1:
        return found[0]
    listing = "; ".join(f"{c['entry']} ({c['format']}: {c['why']})" for c in found[:8])
    raise AssetError("AMBIGUOUS_BUNDLE", f"Several files in that folder could be the robot: {listing}. Pass source_path with the one you want.",
                     candidates=[dict(entry=c["entry"], format=c["format"], why=c["why"]) for c in found[:12]])


def closure_result(path, project, folder, fmt) -> dict:
    """One file's dependencies, in the shape ``assets__dependencies`` gets from its USD helper, for xacro, SDF, meshes and maps."""
    from pathlib import Path

    from . import mesh_refs, urdf_refs

    path, folder = Path(path), Path(folder)
    entry = path.relative_to(folder).as_posix()
    result = dict(path=str(path), layers=[], assets=[], unresolved=[], runtime=[], referenced_by={}, **{fmt: True})
    try:
        if fmt == "mesh":
            found = mesh_refs.check(entry, project)
            used = set(found["files"])
            result.update(absent=found["absent"], materials=found["materials"])
        elif fmt == "map":
            from . import xacro_lite

            meta = xacro_lite.load_yaml_text(project.read(entry).decode("utf-8", "replace"))
            image = urdf_refs.normal_path(urdf_refs._parent(entry), str(meta.get("image", ""))) if isinstance(meta, dict) else None
            if not image or image not in project.files:
                raise AssetError("MAP_IMAGE", f"The map's image {str(meta.get('image', ''))[:80]!r} is not inside the folder.")
            used = {entry, image}
        else:
            if fmt == "xacro":
                from . import xacro_lite

                made = xacro_lite.expand(entry, project, {})
                text, used = made["urdf"], set(made["files"])
                result["declared_args"] = made["declared_args"]
            else:
                from . import sdf_urdf

                made = sdf_urdf.convert(entry, project, None)
                text, used = made["urdf"], set(made["files"])
                result.update(sdf_model=made["model"], sdf_models=made["models"], sdf_warnings=made["warnings"])
            tree = urdf_refs.parse(text.encode(), entry)
            done = urdf_refs.resolve(entry, tree, project)
            followed = urdf_refs.follow_mesh_files(project, sorted(set(done["mapped"].values())))
            used |= {*done["mapped"].values(), *done["package_files"], *followed["found"]}
            result.update(problems=done["problems"], packages=done["packages"], missing_textures=followed["missing"])
    except (AssetError, OSError) as exc:
        hint = " Give the file's $(arg ...) values with xacro_args on assets__import_path." if getattr(exc, "code", "") == "XACRO_ARG" else ""
        result["error"] = f"{getattr(exc, 'code', 'FILESYSTEM')}: {exc}{hint}"
        return result
    result["assets"] = [str(folder / rel) for rel in sorted(used)]
    return result

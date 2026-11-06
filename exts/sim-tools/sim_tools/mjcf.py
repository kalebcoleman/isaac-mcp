"""Convert a MuJoCo MJCF model with the importer this Kit ships, for ``assets__import_mjcf``.

The host has already checked every file the model names and copied the approved folder to a private snapshot, so the
importer only ever reads files inside it. This module runs the importer into a private output folder and returns the
generated USD, which ``prepare_generated_physics`` (shared with the URDF path) then makes physics-ready.
"""

import os
from pathlib import Path

from isaac_mcp.project_assets import refuse

CONVERSION_VERSION = 3  # bump when what is written into a conversion changes
EXTENSION = "isaacsim.asset.importer.mjcf"


def _enabled():
    """Enable the importer extension when this Kit has not loaded it."""
    try:
        import omni.kit.app

        omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(EXTENSION, True)
    except Exception as exc:  # noqa: BLE001 - an extension that cannot load can fail in many ways
        refuse("IMPORTER_UNAVAILABLE", f"This Kit could not load its MJCF importer ({type(exc).__name__}). Enable {EXTENSION}.")


def _legacy_input(copy_root, entry, found):
    """Adapt verified MJCF for the 5.1 importer in a private copy, never the snapshot.

    That importer handles one top-level default and writes converted meshes beside their
    inputs using only the stem. Merge only independent named default classes and give
    same-stem mesh files distinct names. Refuse unsupported layouts rather than guess.
    """
    import copy
    import shutil
    import xml.etree.ElementTree as ET
    from pathlib import PurePosixPath

    trees = {name: copy.deepcopy(tree) for name, tree in found["trees"].items()}
    dirty = set()
    for name, root in trees.items():
        defaults = root.findall("default")
        if len(defaults) > 1:
            classes = [child.get("class") for block in defaults for child in block]
            if (any(block.attrib for block in defaults)
                or any(child.tag != "default" or not child.get("class") for block in defaults for child in block)
                or len(classes) != len(set(classes))):
                refuse("MJCF_51_DEFAULTS", "Isaac Sim 5.1 cannot safely combine these top-level MJCF defaults. Use a model with one default block or Sim 6.2.")
            for block in defaults[1:]:
                defaults[0].extend(list(block))
                root.remove(block)
            dirty.add(name)

    # Work out whether an ambiguous include actually needs rewriting. If its meshes
    # do not collide in any scope, leave it alone instead of rejecting a valid model.
    stem_sources = {}
    for refs in found["mesh_sources"].values():
        for paths in refs.values():
            for rel in paths:
                source = copy_root / rel
                stem_sources.setdefault((source.parent, source.stem.lower()), set()).add(rel)
    collisions = {rel for paths in stem_sources.values() if len(paths) > 1 for rel in paths}
    # The importer also creates a USD file from the stem, so avoid *any* existing
    # file's stem, not just an existing name with the same mesh extension.
    occupied = {(p.parent, p.stem.lower()) for p in copy_root.rglob("*") if p.is_file()}
    used_stems, renamed = {}, {}
    for name, root in trees.items():
        for mesh in root.iter("mesh"):
            value = mesh.get("file")
            if not value:
                continue
            sources = found["mesh_sources"].get(name, {}).get(value, [])
            if len(sources) != 1 and any(rel in collisions for rel in sources):
                # An included XML file can be reused under different compiler scopes.
                refuse("MJCF_51_MODEL", "Isaac Sim 5.1 cannot safely normalize an MJCF mesh used in multiple model scopes; use Sim 6.2.")
            if len(sources) != 1:
                continue
            rel = sources[0]
            source = copy_root / rel
            key = (source.parent, source.stem.lower())
            previous = used_stems.setdefault(key, rel)
            if previous == rel:
                continue
            if rel not in renamed:
                n = 1
                while True:
                    target = source.with_name(f"{source.stem}__isaac_mcp_{n}{source.suffix}")
                    stem = (target.parent, target.stem.lower())
                    if stem not in occupied:
                        break
                    n += 1
                with source.open("rb") as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
                occupied.add(stem)
                renamed[rel] = target.name
            if not mesh.get("name"):
                mesh.set("name", PurePosixPath(value).stem)
            mesh.set("file", str(PurePosixPath(value).with_name(renamed[rel])))
            dirty.add(name)

    for name in dirty:
        (copy_root / name).write_bytes(ET.tostring(trees[name], encoding="utf-8", xml_declaration=True))

    # Only infer a floating base for a top-level free joint in the entry itself.
    root = trees[entry]
    return any(body.find("freejoint") is not None or
               any(j.get("type") == "free" for j in body.findall("joint"))
               for body in root.findall("./worldbody/body"))


def _convert_with_commands(mjcf, output, fixed_base, project_dir, found):
    """The 5.x importer: an ImportConfig and a Kit command. No Physics variants: the output has physics directly.

    It writes converted meshes beside the source meshes, which would change the verified snapshot, so it works on a copy
    inside its own private output folder.
    """
    import re
    import shutil

    import omni.kit.commands
    from isaacsim.asset.importer.mjcf import _mjcf

    from .urdf import _no_python_log_listener

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if project_dir is not None:
        copy = output / "input"
        shutil.copytree(project_dir, copy)
        for folder, _, names in os.walk(copy):
            os.chmod(folder, 0o700)
            for name in names:
                os.chmod(os.path.join(folder, name), 0o600)
        entry = Path(mjcf).relative_to(project_dir).as_posix()
        mjcf = copy / entry
        floating = _legacy_input(copy, entry, found)
    else:
        floating = False
    config = _mjcf.ImportConfig()
    if fixed_base is not None:
        config.set_fix_base(bool(fixed_base))
    elif floating:
        config.set_fix_base(False)
    config.set_make_default_prim(True)
    config.set_create_physics_scene(False)
    config.set_import_inertia_tensor(True)
    config.set_self_collision(False)
    config.set_distance_scale(1.0)
    config.set_density(0.0)
    config.set_import_sites(False)
    target = output / f"{Path(mjcf).stem}.usd"
    prim = "/" + re.sub(r"[^A-Za-z0-9_]", "_", Path(mjcf).stem)  # an empty path makes the importer spin forever
    with _no_python_log_listener():
        ok, _ = omni.kit.commands.execute("MJCFCreateAsset", mjcf_path=str(mjcf), import_config=config, prim_path=prim, dest_path=str(target))
    if not ok or not target.is_file():
        refuse("IMPORT_FAILED", f"The MJCF importer did not write {target.name}.")
    return target


def convert(mjcf, output, fixed_base, project_dir=None, found=None):
    """Run the installed importer on *mjcf*, writing below *output*. Returns the generated USD file.

    *fixed_base* is True, False, or None to keep what the model says (a model with a free joint floats).
    *project_dir* is the verified copy of the approved folder that holds *mjcf*.
    """
    try:
        from isaacsim.asset.importer.mjcf import MJCFImporter, MJCFImporterConfig
    except ImportError:
        _enabled()
        try:
            from isaacsim.asset.importer.mjcf import MJCFImporter, MJCFImporterConfig
        except ImportError:
            try:
                from isaacsim.asset.importer.mjcf import _mjcf  # noqa: F401 - the 5.x API
            except ImportError as exc:
                refuse("IMPORTER_UNAVAILABLE", f"This Kit has no MJCF importer ({type(exc).__name__}). Isaac Sim 5.0 and earlier do not ship one we can use.")
            return _convert_with_commands(mjcf, output, fixed_base, project_dir, found)
    config = MJCFImporterConfig(mjcf_path=str(mjcf), usd_path=str(output), fix_base=fixed_base)
    generated = Path(MJCFImporter(config).import_mjcf())
    return generated if generated.is_absolute() else Path(output) / generated


def importer_identity():
    """Which importer this Kit has. One staging folder can serve several builds, and output from one is not valid for
    another, so a stored conversion is only reused by the importer that made it."""
    extension = "unknown"
    try:
        import omni.kit.app

        extension = omni.kit.app.get_app().get_extension_manager().get_enabled_extension_id(EXTENSION) or "unknown"
    except (ImportError, AttributeError):
        pass
    return f"mjcf/{extension}/{CONVERSION_VERSION}"


def movable_joint_count(trees) -> int:
    """Hinge and slide joints the model declares as children of a body: the converted robot must keep at least that many.

    ``joint`` is also a tag inside ``<equality>``, ``<tendon>`` and ``<default>``; those are not joints of the robot.
    """
    count = 0
    for tree in trees:
        for body in tree.iter("body"):
            for joint in body.findall("joint"):
                if joint.attrib.get("type", "hinge") in ("hinge", "slide"):
                    count += 1
    return count


REVOLUTE_STIFFNESS, REVOLUTE_DAMPING = 100.0, 10.0  # N*m per rad, N*m*s per rad
PRISMATIC_STIFFNESS, PRISMATIC_DAMPING = 1000.0, 100.0  # N per m, N*s per m


def mjcf_default_gains(trees):
    """A generic holding drive for each named hinge and slide joint, for ``default_drives=true``.

    MuJoCo models drive their joints with actuators, often torque motors, which become no joint drive at all. These gains are
    only a starting point to keep a pose; a joint that already has a stiffness keeps its own.
    """
    gains = {}
    for tree in trees:
        for body in tree.iter("body"):
            for joint in body.findall("joint"):
                name, kind = joint.get("name"), joint.get("type", "hinge")
                if not name or kind not in ("hinge", "slide"):
                    continue
                linear = kind == "slide"
                gains[name] = dict(stiffness=PRISMATIC_STIFFNESS if linear else REVOLUTE_STIFFNESS,
                                   damping=PRISMATIC_DAMPING if linear else REVOLUTE_DAMPING, linear=linear)
    return gains

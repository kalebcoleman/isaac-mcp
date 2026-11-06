"""Convert a MuJoCo MJCF model with the importer this Kit ships, for ``assets__import_mjcf``.

The host has already checked every file the model names and copied the approved folder to a private snapshot, so the
importer only ever reads files inside it. This module runs the importer into a private output folder and returns the
generated USD, which ``prepare_generated_physics`` (shared with the URDF path) then makes physics-ready.
"""

import os
from pathlib import Path

from isaac_mcp.project_assets import refuse

CONVERSION_VERSION = 2  # bump when what is written into a conversion changes
EXTENSION = "isaacsim.asset.importer.mjcf"


def _enabled():
    """Enable the importer extension when this Kit has not loaded it."""
    try:
        import omni.kit.app

        omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(EXTENSION, True)
    except Exception as exc:  # noqa: BLE001 - an extension that cannot load can fail in many ways
        refuse("IMPORTER_UNAVAILABLE", f"This Kit could not load its MJCF importer ({type(exc).__name__}). Enable {EXTENSION}.")


def _convert_with_commands(mjcf, output, fixed_base, project_dir):
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
        mjcf = copy / Path(mjcf).relative_to(project_dir)
    config = _mjcf.ImportConfig()
    if fixed_base is not None:  # otherwise keep what the importer does with the model: a free joint floats, a world-attached body stays
        config.set_fix_base(bool(fixed_base))
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


def convert(mjcf, output, fixed_base, project_dir=None):
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
            return _convert_with_commands(mjcf, output, fixed_base, project_dir)
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

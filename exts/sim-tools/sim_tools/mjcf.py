"""Convert a MuJoCo MJCF model with the importer this Kit ships, for ``assets__import_mjcf``.

The host has already checked every file the model names and copied the approved folder to a private snapshot, so the
importer only ever reads files inside it. This module runs the importer into a private output folder and returns the
generated USD, which ``prepare_generated_physics`` (shared with the URDF path) then makes physics-ready.
"""

from pathlib import Path

from isaac_mcp.project_assets import refuse

CONVERSION_VERSION = 2  # bump when what is written into a conversion changes
EXTENSION = "isaacsim.asset.importer.mjcf"


def _importer():
    """The importer classes, enabling the extension first when this Kit has not loaded it."""
    try:
        from isaacsim.asset.importer.mjcf import MJCFImporter, MJCFImporterConfig

        return MJCFImporter, MJCFImporterConfig
    except ImportError:
        pass
    try:
        import omni.kit.app

        omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(EXTENSION, True)
        from isaacsim.asset.importer.mjcf import MJCFImporter, MJCFImporterConfig

        return MJCFImporter, MJCFImporterConfig
    except Exception as exc:  # noqa: BLE001 - an extension that cannot load can fail in many ways
        refuse("IMPORTER_UNAVAILABLE", f"This Kit has no MJCF importer ({type(exc).__name__}). Enable {EXTENSION}. "
               "Isaac Sim 5.0 and earlier do not ship one.")


def convert(mjcf, output, fixed_base):
    """Run the installed importer on *mjcf*, writing below *output*. Returns the generated USD file.

    *fixed_base* is True, False, or None to keep what the model says (a model with a free joint floats).
    """
    MJCFImporter, MJCFImporterConfig = _importer()
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

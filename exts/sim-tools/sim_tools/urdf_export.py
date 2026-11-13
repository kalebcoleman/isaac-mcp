"""Write a robot in the scene back out as a URDF with its meshes, using Isaac Sim's own exporter.

``sim__stage__save format=urdf`` writes a folder ``<name>/`` holding ``<name>.urdf`` and ``meshes/`` (OBJ) in the output root, or in
``directory`` under the home folder. It never overwrites. What a URDF cannot hold is reported, not hidden: drives and gains, USD
materials beyond colour and texture, sensors, and anything that is not a link, joint or mesh.
"""

from __future__ import annotations

import re
import shutil
import xml.etree.ElementTree as ET

from twinsmith.project_assets import refuse

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,59}")
LOST = ("Not in a URDF, so not exported: drive gains and targets, physics materials, sensors and cameras, lights, and USD "
        "material graphs (colours and textures are kept). A velocity or position controller must be set again after an import.")


def _writer(stage, robot_path):
    """A function that writes the URDF: the 6.x converter when this Kit has it, else the 5.x one the same extension ships."""
    try:
        import omni.kit.app

        omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate("isaacsim.asset.exporter.urdf", True)
    except Exception as exc:  # noqa: BLE001 - an extension that cannot load can fail in many ways
        refuse("EXPORTER_UNAVAILABLE", f"This Kit could not load its URDF exporter ({type(exc).__name__}). Enable isaacsim.asset.exporter.urdf.")
    try:
        from isaacsim.asset.exporter.urdf import UsdToUrdfConverter

        return lambda target: UsdToUrdfConverter(stage, root_prim_path=robot_path, mesh_dir_name="meshes", mesh_path_prefix="./").convert(str(target))
    except ImportError:
        pass
    try:
        from nvidia.srl.from_usd.to_urdf import UsdToUrdf
    except ImportError as exc:
        refuse("EXPORTER_UNAVAILABLE", f"This Kit has no URDF exporter ({type(exc).__name__}). Enable isaacsim.asset.exporter.urdf.")

    def write(target):
        converter = UsdToUrdf(stage, root=robot_path) if robot_path else UsdToUrdf(stage)
        return converter.save_to_file(urdf_output_path=target, mesh_dir=target.parent / "meshes", mesh_path_prefix="./", use_uri_file_prefix=False)

    return write


def _subtree(prim):
    from pxr import Usd

    return Usd.PrimRange(prim, Usd.TraverseInstanceProxies())


def _moving_joints(prim):
    from pxr import UsdPhysics

    return [p for p in _subtree(prim) if p.IsA(UsdPhysics.Joint) and not p.IsA(UsdPhysics.FixedJoint)]


def robot_root(stage, robot_path):
    """The prim the exporter needs: the top of one robot. Its articulation root is usually a link with no joints under it, from which the
    exporter writes one link and silently loses the robot; above several robots it writes the first it finds. Refused rather than guessed."""
    from pxr import UsdPhysics

    prim = stage.GetPrimAtPath(robot_path) if robot_path else stage.GetDefaultPrim()
    if not prim or not prim.IsValid():
        refuse("NOT_FOUND", "This stage has no default prim. Pass robot_path with the robot's articulation root or its top prim.")
    roots = [p for p in _subtree(prim) if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
    if len(roots) > 1:
        refuse("AMBIGUOUS_ROBOT", f"{prim.GetPath()} holds {len(roots)} robots: {', '.join(str(p.GetPath()) for p in roots[:8])}. Pass robot_path with the one to export.",
               candidates=[str(p.GetPath()) for p in roots[:32]])
    if _moving_joints(prim):
        return str(prim.GetPath())
    if not any(p.HasAPI(UsdPhysics.RigidBodyAPI) for p in _subtree(prim)):
        refuse("NOT_A_ROBOT", f"{prim.GetPath()} has no rigid bodies, so it is not a robot. Pass robot_path with a robot's articulation root or its top prim.")
    # Inside a robot, or a prop with no moving joint: go up only to the joints that join this body.
    body = prim.GetPath()
    parent = prim.GetParent()
    while parent and parent.IsValid() and not parent.IsPseudoRoot():
        for joint in _moving_joints(parent):
            api = UsdPhysics.Joint(joint)
            ends = list(api.GetBody0Rel().GetTargets()) + list(api.GetBody1Rel().GetTargets())
            if any(end == body or end.HasPrefix(body) or body.HasPrefix(end) for end in ends):
                # The joint can live above several robots. Do not hand that ambiguous
                # ancestor to the exporter, which silently chooses its first robot.
                candidates = [p for p in _subtree(parent) if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
                if len(candidates) > 1:
                    refuse("AMBIGUOUS_ROBOT", f"The joints above {body} share {parent.GetPath()} with multiple robots; pass a top prim that contains only the intended robot.",
                           candidates=[str(p.GetPath()) for p in candidates[:32]])
                return str(parent.GetPath())
        parent = parent.GetParent()
    return str(prim.GetPath())


def _lost_its_structure(stage, robot_path, urdf) -> bool:
    """Did the exporter lose joints that exist? A floating base is its own addition. A prop fixed to the world really has none: one body and no moving joint."""
    from pxr import UsdPhysics

    root = stage.GetPrimAtPath(robot_path) if robot_path else stage.GetDefaultPrim()
    written = [j for j in urdf.findall("joint") if j.get("type") != "floating"]
    if not root or written:
        return False
    return bool(_moving_joints(root)) or sum(p.HasAPI(UsdPhysics.RigidBodyAPI) for p in _subtree(root)) > len(urdf.findall("link"))


def export(scene, stage, filename: str, directory, robot_path) -> dict:
    if scene.timeline.is_playing():
        refuse("TIMELINE_ACTIVE", "Pause or stop before exporting a stable copy.")
    if not isinstance(filename, str) or not NAME.fullmatch(filename):
        refuse("INVALID_OUTPUT", "Use a simple name of letters, digits, _ and - (at most 60 characters), no extension: it names the folder and the .urdf.")
    if robot_path is not None:
        prim = stage.GetPrimAtPath(robot_path) if isinstance(robot_path, str) else None
        if prim is None or not prim.IsValid():
            refuse("NOT_FOUND", f"robot_path {robot_path!r} is not a prim in this stage. Give the robot's articulation root or its top prim.")
    robot_path = robot_root(stage, robot_path)
    write = _writer(stage, robot_path)
    folder = scene.save_directory(directory) / filename
    try:
        folder.mkdir(mode=0o700)
    except FileExistsError:
        refuse("EXISTS", f"{folder} already exists and is never overwritten; choose another name.")
    target = folder / f"{filename}.urdf"
    try:
        written = write(target)
    except Exception as exc:  # noqa: BLE001 - the exporter raises ValueError for a missing limit and others for bad structure
        shutil.rmtree(folder, ignore_errors=True)
        refuse("EXPORT_FAILED", f"The URDF exporter refused this robot: {str(exc)[:700]}")
    root = ET.parse(written).getroot()
    if _lost_its_structure(stage, robot_path, root):
        # The 5.x converter can finish without finding the articulation: an empty robot must not read as a success.
        shutil.rmtree(folder, ignore_errors=True)
        refuse("EXPORT_FAILED", "The URDF exporter wrote no joints for a robot that has them. On Isaac Sim 5.1 its converter does not find a robot under a "
               "sub-prim; URDF export is tested on 6.2.")
    files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    return dict(
        format="urdf", path=str(written), folder=str(folder), robot_path=robot_path, files=files[:200], file_count=len(files),
        links=len(root.findall("link")), joints=len(root.findall("joint")),
        joint_types=sorted({j.get("type") for j in root.findall("joint")}),
        lost=LOST,
        note="Import it again with assets__import_path (approve the folder first) to check the round trip.",
    )

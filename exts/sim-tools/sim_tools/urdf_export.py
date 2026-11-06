"""Write a robot in the scene back out as a URDF with its meshes, using Isaac Sim's own exporter.

``sim__stage__save format=urdf`` writes a folder ``<name>/`` holding ``<name>.urdf`` and ``meshes/`` (OBJ) in the output root, or in
``directory`` under the home folder. It never overwrites. What a URDF cannot hold is reported, not hidden: drives and gains, USD
materials beyond colour and texture, sensors, and anything that is not a link, joint or mesh.
"""

from __future__ import annotations

import re
import shutil
import xml.etree.ElementTree as ET

from isaac_mcp.project_assets import refuse

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


def _has_joints(stage, robot_path) -> bool:
    from pxr import Usd, UsdPhysics

    root = stage.GetPrimAtPath(robot_path) if robot_path else stage.GetDefaultPrim()
    return bool(root) and any(p.IsA(UsdPhysics.Joint) and not p.IsA(UsdPhysics.FixedJoint) for p in Usd.PrimRange(root, Usd.TraverseInstanceProxies()))  # a prop fixed to the world has none to lose


def export(scene, stage, filename: str, directory, robot_path) -> dict:
    if scene.timeline.is_playing():
        refuse("TIMELINE_ACTIVE", "Pause or stop before exporting a stable copy.")
    if not isinstance(filename, str) or not NAME.fullmatch(filename):
        refuse("INVALID_OUTPUT", "Use a simple name of letters, digits, _ and - (at most 60 characters), no extension: it names the folder and the .urdf.")
    if robot_path is not None:
        prim = stage.GetPrimAtPath(robot_path) if isinstance(robot_path, str) else None
        if prim is None or not prim.IsValid():
            refuse("NOT_FOUND", f"robot_path {robot_path!r} is not a prim in this stage. Give the robot's articulation root or its top prim.")
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
    if not root.findall("joint") and _has_joints(stage, robot_path):
        # The 5.x converter can finish without finding the articulation: an empty robot must not read as a success.
        shutil.rmtree(folder, ignore_errors=True)
        refuse("EXPORT_FAILED", "The URDF exporter wrote no joints for a robot that has them. On Isaac Sim 5.1 its converter does not find a robot under a "
               "sub-prim; URDF export is tested on 6.2.")
    files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    return dict(
        format="urdf", path=str(written), folder=str(folder), files=files[:200], file_count=len(files),
        links=len(root.findall("link")), joints=len(root.findall("joint")),
        joint_types=sorted({j.get("type") for j in root.findall("joint")}),
        lost=LOST,
        note="Import it again with assets__import_path (approve the folder first) to check the round trip.",
    )

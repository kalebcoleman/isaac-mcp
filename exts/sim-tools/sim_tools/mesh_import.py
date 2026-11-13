"""Import a loose mesh (glTF, GLB, OBJ, STL, FBX, DAE, PLY) as a prop, through ``assets__import_path``.

The host made a verified snapshot of the approved folder. This module checks that every file the mesh names is inside it,
converts the mesh to USD with Kit's asset converter (the converter reads only the snapshot), and references the result into the
scene under a wrapper prim. Options: ``scale`` (the file's unit to metres), ``up_axis`` (the file's own up axis), ``as_collider``
(none, convex_hull, convex_decomposition) and ``mass`` (makes a dynamic rigid body). Nothing here runs code from the file.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import uuid
from pathlib import Path, PurePosixPath

from twinsmith.project_assets import AssetError, refuse

CONVERSION_VERSION = 1
COLLIDERS = ("none", "convex_hull", "convex_decomposition")
UP_AXES = ("auto", "y", "z")
# What the converter should treat as Y-up: glTF says so itself, FBX and OBJ conventionally are, a CAD STL or PLY is Z-up.
Y_UP_BY_DEFAULT = {".gltf", ".glb", ".fbx", ".obj", ".dae"}


def _context(materials: bool, y_up: bool):
    from omni.kit.asset_converter import AssetConverterContext

    ctx = AssetConverterContext()
    ctx.ignore_materials = not materials
    ctx.ignore_animations = True
    ctx.ignore_camera = True
    ctx.ignore_light = True
    ctx.use_meter_as_world_unit = True
    ctx.export_preview_surface = True
    ctx.convert_stage_up_z = y_up  # a Y-up file becomes Z-up, as the scene is
    ctx.single_mesh = False
    return ctx


def identity() -> str:
    version = "unknown"
    try:
        import omni.kit.app

        version = omni.kit.app.get_app().get_extension_manager().get_enabled_extension_id("omni.kit.asset_converter") or "unknown"
    except Exception:  # noqa: BLE001
        pass
    return f"mesh/{version}/{CONVERSION_VERSION}"


def _set_z_up(path: Path) -> None:
    """The converter marks an OBJ or STL as Y-up. When the file is Z-up, say so, so the scene accepts it (no geometry changes)."""
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.GetRootLayer().Save()


async def convert(source: Path, output: Path, materials: bool, y_up: bool) -> Path:
    """Run the asset converter on *source*, writing ``mesh.usd`` in *output*."""
    try:
        import omni.kit.app

        manager = omni.kit.app.get_app().get_extension_manager()
        if not manager.is_extension_enabled("omni.kit.asset_converter"):
            manager.set_extension_enabled_immediate("omni.kit.asset_converter", True)
        import omni.kit.asset_converter as converter
    except Exception as exc:  # noqa: BLE001
        refuse("IMPORTER_UNAVAILABLE", f"This Kit has no asset converter ({type(exc).__name__}). Enable omni.kit.asset_converter.")
    output.mkdir(parents=True, exist_ok=True)
    target = output / "mesh.usd"
    task = converter.get_instance().create_converter_task(str(source), str(target), None, _context(materials, y_up))
    ok = await task.wait_until_finished()
    if not ok or not target.is_file():
        refuse("IMPORT_FAILED", f"The asset converter failed on {source.name}: {str(task.get_error_message())[:600]} (status {task.get_status()}).")
    if not y_up:
        _set_z_up(target)
    return target


def _number(value, name, low, high):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low < value <= high:
        refuse("INVALID_VALUE", f"{name} must be a number above {low} and at most {high}.")
    return float(value)


async def run(ext, args: dict) -> dict:
    from twinsmith import mesh_refs, urdf_refs
    from twinsmith.project_assets import Broker, open_dir, signature
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    s = ext.scene
    args = dict(args)
    args.pop("format", None)
    snapshot_id, digest, prim_path = args.pop("snapshot_id"), args.pop("manifest_sha256"), args.pop("prim_path")
    scale = _number(args.pop("scale", None), "scale", 0.0, 1e6) or 1.0
    up_axis = args.pop("up_axis", None) or "auto"
    given = set(args)
    collider = args.pop("as_collider", None) or "none"
    mass = _number(args.pop("mass", None), "mass", 0.0, 1e6)
    for key in ("session_id", "stage_id", "xacro_args", "sdf_model", "fixed_base", "default_drives"):
        args.pop(key, None)
    if up_axis not in UP_AXES:
        refuse("INVALID_VALUE", f"up_axis must be one of {', '.join(UP_AXES)}.")
    if collider not in COLLIDERS:
        refuse("INVALID_VALUE", f"as_collider must be one of {', '.join(COLLIDERS)}.")
    no_collider_asked = mass is not None and collider == "none" and "as_collider" in given
    if mass is not None and collider == "none":
        collider = "convex_hull"  # a dynamic body needs a convex collider
    stage = s.get_stage()
    s.new_path(stage, prim_path)
    filename, manifest = s.broker.verify(snapshot_id, digest, kind="mesh")
    entry = manifest["entry"]
    mesh_refs.check_mesh_path(entry)
    project_dir = filename.parents[len(Path(entry).parts) - 1]
    project = urdf_refs.project_from_manifest(project_dir, manifest["files"], manifest.get("root_name", ""))
    checked = mesh_refs.check(entry, project)
    suffix = PurePosixPath(entry).suffix.lower()
    y_up = (suffix in Y_UP_BY_DEFAULT) if up_axis == "auto" else up_axis == "y"
    workspace = s.broker.staging / snapshot_id
    key = hashlib.sha256(json.dumps([identity(), y_up, checked["materials"], entry]).encode()).hexdigest()[:24]
    output = workspace / f"mesh-converted-{key}"
    done = output / "mesh.usd"
    if not done.is_file():
        pending = workspace / f".pending-{uuid.uuid4().hex}"
        try:
            await convert(filename, pending, checked["materials"], y_up)
            pending.rename(output)
        except BaseException:
            shutil.rmtree(pending, ignore_errors=True)
            raise
    derived = Broker("")
    derived.staging = s.broker.staging
    fd = open_dir(output)
    try:
        ident = signature(os.fstat(fd))[:2]
    finally:
        os.close(fd)
    derived.roots = {"converted": (output, ident)}
    snap = derived.snapshot("converted", "mesh.usd")
    result = s.import_asset(stage, snap["snapshot_id"], snap["manifest_sha256"], prim_path, **args)
    wrapper = stage.GetPrimAtPath(prim_path)
    warnings = ["as_collider=none with a mass would fall through the ground, so it got a convex hull."] if no_collider_asked else []
    meshes = [p for p in Usd.PrimRange(wrapper, Usd.TraverseInstanceProxies()) if p.IsA(UsdGeom.Mesh)]
    if not meshes:
        warnings.append("The converted file holds no mesh.")
    # The session layer, where the wrapper's own transform is: in a weaker layer its xformOpOrder wins and a scale is ignored.
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        if scale != 1.0:
            UsdGeom.Xformable(wrapper).AddScaleOp().Set(Gf.Vec3f(scale, scale, scale))
        if collider != "none":
            approximation = {"convex_hull": "convexHull", "convex_decomposition": "convexDecomposition"}[collider]
            for mesh in meshes:
                if mesh.IsInstanceProxy():
                    continue
                UsdPhysics.CollisionAPI.Apply(mesh)
                UsdPhysics.MeshCollisionAPI.Apply(mesh).CreateApproximationAttr(approximation)
        if mass is not None:
            UsdPhysics.RigidBodyAPI.Apply(wrapper)
            UsdPhysics.MassAPI.Apply(wrapper).CreateMassAttr(mass)
    if checked["absent"]:
        warnings.append(f"Files the mesh names are not beside it ({', '.join(checked['absent'][:4])}), so it was imported without materials.")
    elif not checked["materials"]:
        warnings.append("This format's texture paths cannot be checked, so it was imported without materials.")
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    box = cache.ComputeWorldBound(wrapper).ComputeAlignedRange()
    size = [round(float(box.GetMax()[i] - box.GetMin()[i]), 6) for i in range(3)] if not box.IsEmpty() else None
    from .validate import validate

    result.update(
        validation=validate(stage, prim_path, expect_mesh=True),
        mesh_snapshot_id=snapshot_id,
        meshes=len(meshes),
        files=checked["files"],
        size_m=size,
        scale=scale,
        up_axis="y" if y_up else "z",
        collider=collider,
        dynamic=mass is not None,
        warnings=warnings,
        note=("Units: metres (use scale for a file in other units). Up axis: the file is read as "
              f"{'Y-up and rotated to Z-up' if y_up else 'Z-up'}; pass up_axis to override."),
    )
    return result

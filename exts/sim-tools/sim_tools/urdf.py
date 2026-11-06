"""Convert a URDF, and validate and finish the private generated USD before it is composed.

Some 6.x importers author Physics=physx while offering only physics/none;
others leave the selection empty. A successful conversion then has geometry
but no composed mass, bodies or joints. Select the shared physics description
on our generated derivative only. Default drives are authored there too, as
overrides, because a URDF has no position gains. Input files are never changed.
This is not a runtime physics qualification.
"""

import contextlib
import math
from pathlib import Path
import sys

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

from .compat import composition_errors

from isaac_mcp.project_assets import refuse
from isaac_mcp.urdf_physics import usd_gains, usd_name

GROUND_CLEARANCE_M = 0.005
LOWEST_TOLERANCE_M = 0.001
MAX_AUTO_LIFT_M = 3.0  # a bigger "lowest point" is stray geometry, not a robot standing in the floor: report it, do not move the robot
MAX_GROUND_SEARCH = 50000
# 6.x offers a shared "physics" description; the 5.x importer's only one is called "PhysX".
SHARED_PHYSICS_VARIANTS = ("physics", "PhysX")
CONVERSION_VERSION = 2  # bump when what is written into a conversion changes, such as the gains (2: continuous joints are free)


def convert(urdf, output, fixed_base):
    """Run the installed importer on *urdf*, writing below *output*. Returns the generated file."""
    urdf, output = str(urdf), Path(output)
    try:
        from isaacsim.asset.importer.urdf import URDFImporter, URDFImporterConfig
    except ImportError:
        return _convert_with_commands(urdf, output, fixed_base)
    extra = {}
    if "fix_base" in getattr(URDFImporterConfig, "__dataclass_fields__", {}):
        extra["fix_base"] = fixed_base
    generated = Path(URDFImporter(URDFImporterConfig(urdf_path=urdf, usd_path=str(output), **extra)).import_urdf())
    return generated if generated.is_absolute() else output / generated


def _no_python_log_listener():
    """The 5.x importer holds the GIL while it waits for worker threads. If a Python log listener is
    installed, a worker that logs waits for the GIL and Kit hangs forever. The twin's panel has one."""
    extension = getattr(sys.modules.get("ur5e_dt.extension"), "_EXT_INSTANCE", None)
    pause = getattr(extension, "without_carb_log_capture", None)
    return pause() if pause else contextlib.nullcontext()


def _convert_with_commands(urdf, output, fixed_base):
    """The 5.x importer: an ImportConfig and a Kit command, with the same meaning as the 6.x options."""
    try:
        import omni.kit.commands
        from isaacsim.asset.importer.urdf import _urdf
    except ImportError:
        refuse("IMPORTER_UNAVAILABLE", "This Kit has no URDF importer. Enable isaacsim.asset.importer.urdf.")
    config = _urdf.ImportConfig()
    config.set_merge_fixed_joints(False)
    config.set_fix_base(bool(fixed_base))
    config.set_import_inertia_tensor(True)
    config.set_distance_scale(1.0)
    config.set_density(0.0)
    config.set_self_collision(False)
    config.set_make_default_prim(True)
    config.set_create_physics_scene(False)
    stem = Path(urdf).stem
    (output / stem).mkdir(parents=True)
    generated = output / stem / f"{stem}.usd"
    with _no_python_log_listener():
        done, path = omni.kit.commands.execute(
            "URDFParseAndImportFile", urdf_path=urdf, import_config=config, dest_path=str(generated)
        )
    if not done or not path or not generated.is_file():
        refuse("IMPORT_FAILED", f"The URDF importer returned nothing for {stem}. See sim__logs__tail.")
    return generated


def _remove_dangling_references(asset, output_root):
    """The 5.x importer references a visuals prim for each link, even a link with no visuals, and so
    leaves references to prims it never wrote. Only the private generated files are edited."""
    removed = []
    predicate = Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)
    for node in list(Usd.PrimRange.Stage(asset, predicate)):
        if not any("UnresolvedPrimPath" in type(error).__name__ for error in node.GetPrimIndex().localErrors):
            continue
        for spec in node.GetPrimStack():
            if not Path(spec.layer.realPath or "/").is_relative_to(output_root):
                continue
            edits = spec.referenceList
            for items in (edits.prependedItems, edits.appendedItems, edits.addedItems, edits.explicitItems):
                for item in list(items):
                    target = Sdf.Layer.FindOrOpenRelativeToLayer(spec.layer, item.assetPath) if item.assetPath else spec.layer
                    if target is not None and item.primPath and not target.GetPrimAtPath(item.primPath):
                        items.remove(item)
                        removed.append(str(node.GetPath()))
            spec.layer.Save()
    return removed


def uses_legacy_importer():
    """True when this Kit has the 5.x importer API."""
    try:
        from isaacsim.asset.importer.urdf import URDFImporter  # noqa: F401
    except ImportError:
        return True
    return False


def importer_identity():
    """Which importer this Kit has. One staging folder can serve several builds, and output from one is
    not valid for another, so a stored conversion is only reused by the importer that made it."""
    extension = "unknown"
    try:
        import omni.kit.app

        manager = omni.kit.app.get_app().get_extension_manager()
        extension = manager.get_enabled_extension_id("isaacsim.asset.importer.urdf") or "unknown"
    except (ImportError, AttributeError):
        pass
    return f"{'5.x' if uses_legacy_importer() else '6.x'}/{extension}/{CONVERSION_VERSION}"


def repair_meshes_for_legacy_importer(mapped, project, folder):
    """The 5.x converter crashes Kit on a COLLADA file with no unit element, and the format says that
    means metres. Point such meshes at a private copy that says so, with what they name beside it.
    Returns the new mapping and the list of files repaired."""
    from isaac_mcp import urdf_refs

    result, repaired = {}, []
    for reference, rel in mapped.items():
        result[reference] = rel
        if not rel.lower().endswith(".dae"):
            continue
        data = project.read(rel)
        if not urdf_refs.dae_without_unit(data):
            continue
        copy = Path(folder) / rel
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(urdf_refs.dae_with_unit(data))
        for name in urdf_refs.follow_mesh_files(project, [rel])["found"]:
            beside = Path(folder) / name
            beside.parent.mkdir(parents=True, exist_ok=True)
            beside.write_bytes(project.read(name))
        result[reference] = str(copy)
        repaired.append(rel)
    return result, sorted(set(repaired))


def _world_anchors(prim):
    """Joints that tie a body to the world: the other side is empty or is not a rigid body."""
    stage = prim.GetStage()
    anchors = []
    for node in Usd.PrimRange(prim, Usd.TraverseInstanceProxies()):
        if not node.IsA(UsdPhysics.Joint):
            continue
        joint = UsdPhysics.Joint(node)
        ends = [joint.GetBody0Rel().GetTargets(), joint.GetBody1Rel().GetTargets()]
        rigid = [bool(t) and stage.GetPrimAtPath(t[0]).HasAPI(UsdPhysics.RigidBodyAPI) for t in ends]
        if rigid[0] != rigid[1]:
            anchors.append(node)
    return anchors


def _unlimited(node) -> bool:
    """A revolute joint with no angle limits (a URDF ``continuous`` joint)."""
    joint = UsdPhysics.RevoluteJoint(node)
    lower, upper = joint.GetLowerLimitAttr().Get(), joint.GetUpperLimitAttr().Get()
    return lower is not None and upper is not None and not (math.isfinite(lower) and math.isfinite(upper))


def _author_drives(prim, gains, replace):
    """Give each joint the default gains, as an opinion in the edit target. A stiffness already there is
    kept unless *replace*: the 5.x importer invents its own, which can be too weak to lift a heavy link."""
    authored, kept, zero_force = {}, [], []
    by_name = {usd_name(name): (name, gain) for name, gain in gains.items()}
    for node in Usd.PrimRange(prim, Usd.TraverseInstanceProxies()):
        angular = node.IsA(UsdPhysics.RevoluteJoint)
        if not angular and not node.IsA(UsdPhysics.PrismaticJoint):
            continue
        name, gain = by_name.get(node.GetName(), (None, None))
        if gain is None:
            continue
        drive = UsdPhysics.DriveAPI.Apply(node, "angular" if angular else "linear")
        if angular and _unlimited(node):
            # A continuous joint (a wheel, a caster) has no pose to hold: a holding stiffness fights a velocity command and
            # stops the wheel. Keep it free and damped; command it by velocity (sim__joints__command velocities).
            stiffness, damping = usd_gains(gain)
            drive.CreateTypeAttr("force")
            drive.CreateStiffnessAttr(0.0)
            drive.CreateDampingAttr(max(damping, drive.GetDampingAttr().Get() or 0.0))
            if (drive.GetMaxForceAttr().Get() or 0.0) <= 0:
                drive.CreateMaxForceAttr(math.inf)
            authored[name] = dict(stiffness=0.0, damping=round(max(damping, drive.GetDampingAttr().Get() or 0.0), 6), continuous=True)
            continue
        if not replace and (drive.GetStiffnessAttr().Get() or 0.0) > 0:
            kept.append(name)
            continue
        stiffness, damping = usd_gains(gain)
        # The gains are forces per unit of error. The 5.x importer authors an acceleration drive,
        # which would scale them by each link's inertia.
        drive.CreateTypeAttr("force")
        drive.CreateStiffnessAttr(stiffness)
        drive.CreateDampingAttr(max(damping, drive.GetDampingAttr().Get() or 0.0))
        force = drive.GetMaxForceAttr().Get()
        if force is not None and force <= 0:
            # A zero effort limit would make the drive do nothing, while the URDF means "no limit given".
            drive.CreateMaxForceAttr(math.inf)
            zero_force.append(name)
        authored[name] = dict(stiffness=round(stiffness, 6), damping=round(damping, 6))
    return authored, kept, zero_force


def ground_height(stage):
    """The height of the highest collision plane on a Z-up stage, or None if it has none."""
    highest = None
    for count, prim in enumerate(stage.Traverse(), 1):
        if count > MAX_GROUND_SEARCH:
            break
        if prim.IsA(UsdGeom.Plane) and prim.HasAPI(UsdPhysics.CollisionAPI):
            height = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()[2]
            highest = height if highest is None else max(highest, height)
    return highest


def lowest_point(stage, path):
    """The lowest point of a prim on a Z-up stage, colliders included, or None if it has no bounds."""
    purposes = [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy, UsdGeom.Tokens.guide]
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), purposes, useExtentsHint=False).ComputeWorldBound(
        stage.GetPrimAtPath(path)).ComputeAlignedRange()
    return None if box.IsEmpty() else box.GetMin()[2]


def _raise_prim(prim, step):
    """Move *prim* by *step* (a vector in its parent's frame) in the current edit target, whatever its first transform op is."""
    xform = UsdGeom.Xformable(prim)
    ops = xform.GetOrderedXformOps()
    first = ops[0] if ops else None
    if first is not None and first.GetOpType() == UsdGeom.XformOp.TypeTransform and not first.IsInverseOp():
        matrix = Gf.Matrix4d(first.Get())
        matrix.SetTranslateOnly(matrix.ExtractTranslation() + step)
        first.Set(matrix)
    elif first is not None and first.GetOpType() == UsdGeom.XformOp.TypeTranslate and not first.IsInverseOp():
        moved = Gf.Vec3d(first.Get() or (0, 0, 0)) + step
        first.Set(Gf.Vec3f(moved) if first.GetPrecision() == UsdGeom.XformOp.PrecisionFloat else moved)
    else:  # none, or a rotation or scale first: a translate placed first acts in the parent's frame
        lift = xform.AddTranslateOp(opSuffix="lift")
        lift.Set(step)
        xform.SetXformOpOrder([lift, *ops], resetXformStack=xform.GetResetXformStack())


def place_above_ground(stage, path, position_given, max_lift=None):
    """A robot whose origin is its body starts partly under the floor at the origin, and the contact
    solver then throws it apart. Lift it onto the ground, or warn if the caller chose the position."""
    ground, lowest = ground_height(stage), lowest_point(stage, path)
    report = dict(ground_height=ground, lowest_point=lowest, lifted_by=0.0)
    if ground is None or lowest is None or lowest >= ground - LOWEST_TOLERANCE_M:
        return report
    if position_given:
        lift = ground - lowest + GROUND_CLEARANCE_M
        report["warning"] = (f"Part of the robot is {ground - lowest:.3f} below the ground plane. Starting inside a collider "
                             f"throws the joints far past their limits: raise position by at least {lift:.3f}.")
        return report
    lift = ground - lowest + GROUND_CLEARANCE_M
    if max_lift is not None and lift > max_lift:
        report["warning"] = (f"The robot's lowest point reads {ground - lowest:.1f} m below the ground plane, more than the {max_lift:g} m "
                             "an automatic lift allows (stray geometry in the file is the usual cause), so it was not moved.")
        return report
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        prim = stage.GetPrimAtPath(path)
        parent = UsdGeom.Xformable(prim.GetParent()).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        step = parent.GetInverse().TransformDir(Gf.Vec3d(0, 0, lift))
        _raise_prim(prim, step)
    report["lifted_by"] = lift
    report["note"] = (f"The robot's lowest point was {ground - lowest:.3f} under the ground plane, so it was raised by "
                      f"{lift:.3f}. Pass position to choose the height yourself.")
    return report


def prepare_generated_physics(filename, output_root, movable_joints=0, gains=None, fixed_base=None, kind="URDF"):
    tag = kind.upper()
    filename, output_root = Path(filename), Path(output_root)
    try:
        resolved = filename.resolve(strict=True)
        resolved.relative_to(output_root.resolve(strict=True))
    except (OSError, ValueError):
        refuse(
            f"{tag}_OUTPUT", "Native converter output is outside its private directory."
        )
    if filename.is_symlink() or not resolved.is_file():
        refuse(
            f"{tag}_OUTPUT", "Native converter must return a regular generated USD file."
        )
    asset = Usd.Stage.Open(str(resolved), load=Usd.Stage.LoadAll)
    if not asset or not asset.GetDefaultPrim():
        refuse(f"{tag}_PHYSICS", f"Converted {kind} has no valid default prim.")
    prim = asset.GetDefaultPrim()
    variants = prim.GetVariantSets()
    variant = (
        variants.GetVariantSet("Physics") if variants.HasVariantSet("Physics") else None
    )
    selected_before = variant.GetVariantSelection() if variant else None
    names = variant.GetVariantNames() if variant else []
    chosen = None
    if variant:
        chosen = next((name for name in SHARED_PHYSICS_VARIANTS if name in names), None)
        if len(names) > 64 or chosen is None:
            refuse(
                f"{tag}_PHYSICS",
                f"Converted {kind} has no shared 'physics' variant; no backend-specific or geometry-only fallback.",
                available_physics_variants=names[:64],
            )
        # Validate in this isolated stage's session first, without changing files.
        with Usd.EditContext(asset, asset.GetSessionLayer()):
            if not variant.SetVariantSelection(chosen):
                refuse(f"{tag}_PHYSICS", "Cannot compose the converted physics variant.")
    dangling = _remove_dangling_references(asset, output_root.resolve())
    errors = composition_errors(asset)
    if errors:
        refuse(
            f"{tag}_PHYSICS",
            "Converted physics has composition errors: " + str(errors)[:1200],
        )
    counts = dict(
        rigid_bodies=0,
        collisions=0,
        joints=0,
        movable_joints=0,
        articulations=0,
        authored_masses=0,
    )
    for count, node in enumerate(Usd.PrimRange(prim, Usd.TraverseInstanceProxies()), 1):
        if count > 10000:
            refuse("LIMIT", f"Converted {kind} exceeds 10000 composed prims.")
        counts["rigid_bodies"] += int(node.HasAPI(UsdPhysics.RigidBodyAPI))
        counts["collisions"] += int(node.HasAPI(UsdPhysics.CollisionAPI))
        counts["joints"] += int(node.IsA(UsdPhysics.Joint))
        counts["movable_joints"] += int(
            node.IsA(UsdPhysics.RevoluteJoint) or node.IsA(UsdPhysics.PrismaticJoint)
        )
        counts["articulations"] += int(node.HasAPI(UsdPhysics.ArticulationRootAPI))
        mass = node.GetAttribute("physics:mass")
        counts["authored_masses"] += int(bool(mass and mass.HasAuthoredValueOpinion()))
    if not counts["rigid_bodies"] or counts["movable_joints"] < movable_joints:
        refuse(
            f"{tag}_PHYSICS",
            f"Converted {kind} lacks expected rigid bodies or revolute/prismatic joints; active stage was not imported.",
            authored_physics=counts,
            expected_movable_joints=movable_joints,
        )
    authored, kept, zero_force = {}, [], []
    anchors = _world_anchors(prim)
    change = bool(variant or gains or (fixed_base is False and anchors))
    if change:
        # Only the service-owned generated interface is written. Input URDF,
        # dependencies, native physics layers and the selected Kit stage stay intact.
        with Usd.EditContext(asset, asset.GetRootLayer()):
            if variant and not variant.SetVariantSelection(chosen):
                refuse(f"{tag}_PHYSICS", "Cannot persist the private generated physics selection.")
            if fixed_base is False:
                # An importer without a base option keeps its world joint; a mobile robot must not.
                for node in anchors:
                    node.SetActive(False)
                anchors = _world_anchors(prim)
            if gains:
                authored, kept, zero_force = _author_drives(prim, gains, uses_legacy_importer())
            if not asset.GetRootLayer().Save():
                refuse(f"{tag}_PHYSICS", "Cannot persist the private generated physics selection.")
    report = dict(
        variant_set="Physics" if variant else None,
        selected_before=selected_before,
        selected=chosen,
        available=names,
        authored=counts,
        base="fixed" if anchors else "floating",
        **({"empty_references_removed": dangling} if dangling else {}),
        runtime_readiness="unknown; initialize and observe on the selected backend",
    )
    if gains is not None:
        report["drives"] = dict(authored=authored, kept_from_importer=kept, zero_force_limit_removed=zero_force)
    return report


def undriven_joints(stage, path):
    """Names of the movable joints under *path* whose drive has no stiffness and no damping (nothing holds them or slows them)."""
    names = []
    for node in Usd.PrimRange(stage.GetPrimAtPath(path), Usd.TraverseInstanceProxies()):
        angular = node.IsA(UsdPhysics.RevoluteJoint)
        if not angular and not node.IsA(UsdPhysics.PrismaticJoint):
            continue
        drive = UsdPhysics.DriveAPI.Get(node, "angular" if angular else "linear")
        stiffness = drive.GetStiffnessAttr().Get() if drive else None
        damping = drive.GetDampingAttr().Get() if drive else None
        if not stiffness and not damping:
            names.append(node.GetName())
    return names

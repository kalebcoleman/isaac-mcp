"""Checks on what an import produced, reported in every import's result as ``validation``. Read-only.

A converter can succeed and still hand back something unusable: a model a thousand times too big, with no rigid body, with no
joint, or with a position that is not a number. These are the cheap checks that catch that before an agent builds on it.
"""

from __future__ import annotations

import math

from pxr import Usd, UsdGeom, UsdPhysics

SIZE_MIN_M, SIZE_MAX_M = 0.005, 500.0


def validate(stage, path, expect_robot: bool = False, expect_mesh: bool = False) -> dict:
    root = stage.GetPrimAtPath(path)
    checks = []

    def add(name, ok, detail=""):
        checks.append(dict(name=name, ok=bool(ok), **({"detail": detail} if detail else {})))

    prims = list(Usd.PrimRange(root, Usd.TraverseInstanceProxies()))
    meshes = sum(p.IsA(UsdGeom.Gprim) for p in prims)
    add("has_geometry", meshes > 0, f"{meshes} shapes" if meshes else "no mesh or shape was found under the prim")
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy])
    box = cache.ComputeWorldBound(root).ComputeAlignedRange()
    if box.IsEmpty():
        add("size", False, "the bounding box is empty")
    else:
        size = [float(box.GetMax()[i] - box.GetMin()[i]) for i in range(3)]
        finite = all(math.isfinite(v) for v in size) and all(math.isfinite(float(v)) for v in (*box.GetMin(), *box.GetMax()))
        largest = max(size) if finite else math.nan
        add("finite_bounds", finite, "a coordinate is not a number" if not finite else "")
        if finite:
            add("sane_size", SIZE_MIN_M <= largest <= SIZE_MAX_M, f"largest side {largest:.4g} m"
                + ("" if SIZE_MIN_M <= largest <= SIZE_MAX_M else " (wrong unit? pass scale, e.g. 0.001 for millimetres)"))
    bodies = sum(p.HasAPI(UsdPhysics.RigidBodyAPI) for p in prims)
    joints = sum(p.IsA(UsdPhysics.Joint) for p in prims)
    articulations = sum(p.HasAPI(UsdPhysics.ArticulationRootAPI) for p in prims)
    if expect_robot:
        add("rigid_bodies", bodies > 0, f"{bodies} rigid bodies")
        add("articulation_root", articulations > 0, f"{articulations} articulation roots")
        add("joints", joints > 0, f"{joints} joints" if joints else "no joints: the model is a single rigid body")
    if expect_mesh:
        collision = sum(p.HasAPI(UsdPhysics.CollisionAPI) for p in prims)
        add("collision_shapes", collision > 0 or bodies == 0, f"{collision} collision shapes (none: a visual prop only)" if not collision else f"{collision} collision shapes")
    return dict(ok=all(c["ok"] for c in checks), checks=checks)


def joint_summary(stage, path, limit: int = 60) -> list:
    """Each movable joint under *path*: name, kind, limits (radians or stage units, None when unlimited) and its drive gains."""
    rows = []
    for node in Usd.PrimRange(stage.GetPrimAtPath(path), Usd.TraverseInstanceProxies()):
        angular = node.IsA(UsdPhysics.RevoluteJoint)
        if not angular and not node.IsA(UsdPhysics.PrismaticJoint):
            continue
        joint = UsdPhysics.RevoluteJoint(node) if angular else UsdPhysics.PrismaticJoint(node)
        lower, upper = joint.GetLowerLimitAttr().Get(), joint.GetUpperLimitAttr().Get()
        unlimited = lower is None or upper is None or not (math.isfinite(lower) and math.isfinite(upper))
        scale = math.pi / 180.0 if angular else 1.0
        drive = UsdPhysics.DriveAPI.Get(node, "angular" if angular else "linear")
        rows.append(dict(
            name=node.GetName(), kind=("continuous" if unlimited else "revolute") if angular else "prismatic",
            lower=None if unlimited else round(lower * scale, 6), upper=None if unlimited else round(upper * scale, 6),
            stiffness=round(drive.GetStiffnessAttr().Get() or 0.0, 6) if drive else 0.0,
            damping=round(drive.GetDampingAttr().Get() or 0.0, 6) if drive else 0.0,
        ))
        if len(rows) >= limit:
            break
    return rows


def apply_joint_targets(stage, path, targets) -> dict:
    """Author drive targets (radians, or stage units for a prismatic joint) so the robot starts in a pose. Unknown names are reported."""
    if not isinstance(targets, dict) or not targets or len(targets) > 200 or not all(
            isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for k, v in targets.items()):
        from isaac_mcp.project_assets import refuse

        refuse("INVALID_VALUE", "joint_targets is an object of joint names to finite numbers (radians, or stage units for a sliding joint).")
    applied, nodes, beyond = {}, {}, {}
    for node in Usd.PrimRange(stage.GetPrimAtPath(path), Usd.TraverseInstanceProxies()):
        if node.IsA(UsdPhysics.RevoluteJoint) or node.IsA(UsdPhysics.PrismaticJoint):
            nodes[node.GetName()] = node
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for name, value in targets.items():
            node = nodes.get(name)
            if node is None:
                continue
            angular = node.IsA(UsdPhysics.RevoluteJoint)
            joint = UsdPhysics.RevoluteJoint(node) if angular else UsdPhysics.PrismaticJoint(node)
            lower, upper = joint.GetLowerLimitAttr().Get(), joint.GetUpperLimitAttr().Get()
            lower = math.radians(lower) if angular and lower is not None and math.isfinite(lower) else (lower if lower is not None and math.isfinite(lower) else None)
            upper = math.radians(upper) if angular and upper is not None and math.isfinite(upper) else (upper if upper is not None and math.isfinite(upper) else None)
            used = max(value, lower) if lower is not None else value
            used = min(used, upper) if upper is not None else used
            if used != value:
                beyond[name] = dict(asked=value, used=used)
            drive = UsdPhysics.DriveAPI.Apply(node, "angular" if angular else "linear")
            drive.CreateTargetPositionAttr(math.degrees(used) if angular else float(used))
            applied[name] = used
    unknown = sorted(set(targets) - set(applied))
    return dict(applied=applied, unknown=unknown, valid=sorted(nodes)[:60] if unknown else [], beyond_limits=beyond)

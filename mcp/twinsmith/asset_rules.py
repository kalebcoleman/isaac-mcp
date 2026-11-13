"""Rules that say whether a USD asset will load on Newton (MuJoCo-Warp) or PhysX, read from the file alone.

``RULES`` is the table: each rule has an id, its level per engine, what it checks, and the evidence it
came from. ``validate`` opens the file read-only with ``pxr`` (the ``mesh`` extra) and runs every rule.
It runs in ``mesh_worker``, never in the server.
"""

from __future__ import annotations

import math

# Collision triangles a file may carry as triangle-mesh colliders before Newton's contact buffer is at risk.
# The work-cell bench's 1.63 M triangles (385 colliders) asked for 7,500,052 triangle pairs against a buffer
# of 1,000,000 (Isaac Sim 6.2, Newton 1.6.1). 200,000 is a starting value: re-measure it with pack v2.
TRIANGLE_BUDGET = 200_000
MJ_MINVAL = 1e-15            # MuJoCo's mjMINVAL: moving bodies need mass and inertia above it
DEFAULT_RATE_HZ = 500.0      # physics rate the drive-gain rule assumes
REFERENCE_INERTIA = 0.01     # kg m^2 (or kg for a linear drive) when the driven body authors none

RULES = {
    "UNITS": dict(newton="error", physx="error", rule="metersPerUnit and upAxis are authored", evidence="Import contract"),
    "TRI_COLLIDER": dict(newton="error", physx="info",
                         rule=f"Triangle-mesh colliders stay under the budget (default {TRIANGLE_BUDGET:,} triangles)",
                         evidence="The bench's 1.63 M collider triangles needed 7.5 M triangle pairs; the buffer holds 1 M"),
    "COLLIDER_RENDERED": dict(newton="warning", physx="warning",
                              rule="A collision-only shape has purpose \"guide\"", evidence="Split contract"),
    "DRIVE_ZERO_FORCE": dict(newton="error", physx="info", rule="No joint drive has maxForce 0",
                             evidence="MuJoCo: \"actfrcrange[0] should be smaller than actfrcrange[1]\""),
    "NESTED_ARTICULATION": dict(newton="error", physx="info",
                                rule="No articulation root lies inside another articulation or is jointed into one",
                                evidence="Newton's importer mis-lists the bodies; the weld target is unresolved"),
    "MASS_MIN": dict(newton="error", physx="info", rule="Every moving body has a mass source above mjMINVAL",
                     evidence="MuJoCo: \"mass and inertia of moving bodies must be larger than mjMINVAL\""),
    "DRIVE_GAIN": dict(newton="warning", physx="info",
                       rule="sqrt(k / I) <= 2 / dt at the physics rate (k in SI per radian or metre; I from the driven body, "
                            f"else {REFERENCE_INERTIA})", evidence="9400 N m per degree went non-finite at 500 Hz"),
    "LOOP_JOINT": dict(newton="warning", physx="info",
                       rule="A joint excluded from its articulation carries a mimic or equality constraint",
                       evidence="The RG2 fingers did not move"),
}
FIXES = {
    "UNITS": "Author metersPerUnit and upAxis on the root layer.",
    "TRI_COLLIDER": "assets__mesh action=split with collision={\"method\": \"box\"} (or \"auto\") adds explicit shapes.",
    "COLLIDER_RENDERED": "Set purpose = \"guide\" on the shape, or let assets__mesh split author it.",
    "DRIVE_ZERO_FORCE": "Remove the drive, or give it a positive maxForce.",
    "NESTED_ARTICULATION": "Make one articulation: remove the inner ArticulationRootAPI and joint the bodies into the outer tree.",
    "MASS_MIN": "Author a positive mass and inertia (MassAPI), or give the body a collider so mass is computed.",
    "DRIVE_GAIN": "Lower the stiffness, raise the physics rate, or author the driven body's inertia.",
    "LOOP_JOINT": "Add a mimic joint (or MuJoCo equality) for the excluded joint, or drive it directly.",
}


def _schemas(prim, prefix):
    return [n for n in prim.GetAppliedSchemas() if n.startswith(prefix)]


def _authored(prim, name):
    attr = prim.GetAttribute(name)
    return attr.Get() if attr and attr.HasAuthoredValue() else None


def _check(rule, engine, ok, prim=None, detail="", level=None):
    return dict(id=rule, level=level or RULES[rule][engine], ok=bool(ok), prim=prim, detail=detail,
                fix=None if ok else FIXES[rule])


def validate(path, engine="newton", *, triangle_budget=TRIANGLE_BUDGET, rate_hz=DEFAULT_RATE_HZ) -> dict:
    from pxr import Usd, UsdGeom, UsdPhysics

    from . import mesh_usd

    if engine not in ("newton", "physx"):
        raise ValueError("engine must be newton or physx")
    stage = mesh_usd.open_stage(path)
    root = stage.GetRootLayer()
    checks = []
    missing = [k for k in ("metersPerUnit", "upAxis") if not root.pseudoRoot.HasInfo(k)]
    checks.append(_check("UNITS", engine, not missing, detail="missing " + ", ".join(missing) if missing else "authored"))

    prims = list(stage.Traverse())
    tri_total, tri_prims = 0, []
    for prim, _points, faces in mesh_usd.meshes(stage):
        if mesh_usd.approximation(prim) == "none":
            tri_total += len(faces)
            tri_prims.append(str(prim.GetPath()))
    checks.append(_check("TRI_COLLIDER", engine, tri_total <= triangle_budget,
                         prim=tri_prims[0] if len(tri_prims) == 1 else None,
                         detail=f"{tri_total:,} triangles in {len(tri_prims)} triangle-mesh colliders; budget {triangle_budget:,}"))

    for prim in prims:
        if not mesh_usd.collision_schemas(prim) or not prim.IsA(UsdGeom.Imageable):
            continue
        names = [str(p.name) for p in prim.GetPath().GetPrefixes()]
        if any(n.endswith("_collision") or n in ("collisions", "collision") for n in names[:-1]) or names[-1].endswith("_collision"):
            purpose = UsdGeom.Imageable(prim).ComputePurpose()
            if purpose != UsdGeom.Tokens.guide:
                checks.append(_check("COLLIDER_RENDERED", engine, False, str(prim.GetPath()), f"purpose {purpose}"))

    roots = [p for p in prims if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
    bodies = {str(p.GetPath()): p for p in prims if p.HasAPI(UsdPhysics.RigidBodyAPI)}

    def owner(path):
        """The articulation root at or above ``path``."""
        best = None
        for r in roots:
            rp = str(r.GetPath())
            if (path == rp or path.startswith(rp + "/")) and (best is None or len(rp) > len(best)):
                best = rp
        return best

    for r in roots:
        outer = owner(str(r.GetPath().GetParentPath()))
        if outer:
            checks.append(_check("NESTED_ARTICULATION", engine, False, str(r.GetPath()), f"inside the articulation at {outer}"))

    for prim in prims:
        if not prim.IsA(UsdPhysics.Joint):
            continue
        joint, path = UsdPhysics.Joint(prim), str(prim.GetPath())
        targets = [str(t) for rel in (joint.GetBody0Rel(), joint.GetBody1Rel()) for t in rel.GetTargets()]
        owners = {owner(t) for t in targets} - {None}
        excluded = bool(_authored(prim, "physics:excludeFromArticulation"))
        if len(owners) > 1 and not excluded:
            checks.append(_check("NESTED_ARTICULATION", engine, False, path, "joins the articulations at " + ", ".join(sorted(owners))))
        for drive in _schemas(prim, "PhysicsDriveAPI:"):
            axis = drive.split(":", 1)[1]
            force = _authored(prim, f"drive:{axis}:physics:maxForce")
            if force is not None and float(force) == 0:
                checks.append(_check("DRIVE_ZERO_FORCE", engine, False, path, f"drive {axis} has maxForce 0"))
            k = _authored(prim, f"drive:{axis}:physics:stiffness")
            if k:
                angular = axis in ("angular", "rotX", "rotY", "rotZ")
                k_si = float(k) * (180 / math.pi if angular else 1)
                body = bodies.get(targets[-1]) if targets else None
                inertia = _authored(body, "physics:diagonalInertia" if angular else "physics:mass") if body else None
                ref = (max(inertia) if angular and inertia else float(inertia) if inertia else 0) or REFERENCE_INERTIA
                omega, limit = math.sqrt(k_si / ref), 2 * rate_hz
                if omega > limit:
                    checks.append(_check("DRIVE_GAIN", engine, False, path,
                                         f"sqrt({k_si:.4g} / {ref:.4g}) = {omega:.4g} rad/s > 2 / dt = {limit:.4g}"))
        if excluded and not (_schemas(prim, "PhysxMimicJointAPI") or _schemas(prim, "NewtonMimic")
                             or any("Equality" in n for n in prim.GetAppliedSchemas())):
            checks.append(_check("LOOP_JOINT", engine, False, path, "excluded from its articulation with no mimic or equality"))

    for path, prim in bodies.items():
        if _authored(prim, "physics:kinematicEnabled") or _authored(prim, "physics:rigidBodyEnabled") is False:
            continue
        mass = _authored(prim, "physics:mass")
        inertia = _authored(prim, "physics:diagonalInertia")
        has_collider = any(mesh_usd.collision_schemas(p) for p in Usd.PrimRange(prim))
        tiny = [v for v in ([mass] if mass else []) + (list(inertia) if inertia and any(inertia) else []) if 0 < float(v) <= MJ_MINVAL]
        if tiny or (not mass and not has_collider):
            checks.append(_check("MASS_MIN", engine, False, path,
                                 f"mass or inertia {tiny[0]} at or below {MJ_MINVAL}" if tiny else "no mass and no collider to compute one"))

    for rule in RULES:                     # one passing line per rule that found nothing
        if not any(c["id"] == rule for c in checks):
            checks.append(_check(rule, engine, True, detail="no findings"))
    failed = [c for c in checks if not c["ok"]]
    totals = dict(errors=sum(c["level"] == "error" for c in failed), warnings=sum(c["level"] == "warning" for c in failed),
                  info=sum(c["level"] == "info" for c in failed), checks=len(checks))
    return dict(ok=totals["errors"] == 0, engine=engine, file=str(path), checks=checks, totals=totals,
                triangle_budget=triangle_budget)

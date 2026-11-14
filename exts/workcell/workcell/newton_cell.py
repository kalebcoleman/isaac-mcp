"""The work cell on Newton (MuJoCo-Warp): the solver settings and the per-engine arm drives.

PhysX keeps the pack's authored drives (stiff, implicit springs). MuJoCo-Warp integrates a drive's stiffness
explicitly at 500 Hz, so the pack's 9400 N m per degree diverges there. On a Newton session the build
authors the ``newton`` section of the config instead: per joint, stiffness and damping in SI per radian and
an armature, written in USD's per-degree units on the stage (never the pack). The scene gets the settings
the generic tower qualified (rate, elliptic cone, solver iterations, 4 mm contact gap) plus the cell's
``impratio``, and contact buffers sized for every body in it.
"""

import math

DRIVE_KEYS = {"stiffness_nm_per_rad", "damping_nms_per_rad", "armature_kgm2"}


def _drives(config):
    newton = config.get("newton")
    if not newton or "drives" not in newton:
        raise ValueError("config has no newton.drives: the work cell's Newton gains are per engine and must be configured")
    drives = newton["drives"]
    for joint, values in drives.items():
        if set(values) != DRIVE_KEYS:
            raise ValueError(f"newton.drives.{joint} needs exactly {sorted(DRIVE_KEYS)}")
        for key, value in values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"newton.drives.{joint}.{key} must be finite and not negative")
    return drives


def stability(drives, rate_hz, inertia_floor):
    """``dt^2 k / I`` per joint with ``I`` the armature plus ``inertia_floor`` (the smallest inertia the joint
    carries), against the explicit-spring limit ``4 + 2 dt c / I``. Below 1 leaves a margin of four."""
    dt = 1.0 / rate_hz
    out = {}
    for joint, d in drives.items():
        inertia = d["armature_kgm2"] + inertia_floor.get(joint, 0.0)
        if inertia <= 0:
            raise ValueError(f"{joint}: armature plus inertia must be positive")
        out[joint] = dict(ratio=round(dt * dt * d["stiffness_nm_per_rad"] / inertia, 4),
                          limit=round(4 + 2 * dt * d["damping_nms_per_rad"] / inertia, 4))
    return out


def prepare(stage, config, assembly_path, timeline):
    """Author the Newton scene settings, every collider's contact gap and the arm's Newton drives; size the
    contact buffers. Returns what was authored. Raises ValueError for a joint the config names and the
    assembly lacks."""
    from pxr import Sdf, Usd, UsdPhysics
    from sim_tools.newton_support import apply_buffers, collision_settings, prepare_buffers, scene_settings

    drives = _drives(config)
    newton = config["newton"]
    scene = stage.GetPrimAtPath("/World/PhysicsScene")
    settings = scene_settings(scene, newton["physics_hz"], tower=True)
    impratio = scene.GetAttribute("mjc:option:impratio")
    if impratio:
        impratio.Set(float(newton["impratio"]))
        settings["mjc:option:impratio"] = float(newton["impratio"])
    colliders = 0
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            collision_settings(prim)
            colliders += 1
    joints = {prim.GetName(): prim for prim in Usd.PrimRange(stage.GetPrimAtPath(assembly_path)) if prim.IsA(UsdPhysics.Joint)}
    authored = {}
    for name, d in drives.items():
        prim = joints.get(name)
        if prim is None or "PhysicsDriveAPI:angular" not in prim.GetAppliedSchemas():
            raise ValueError(f"newton.drives names {name}, which is not a driven joint of the assembly")
        per_degree = math.pi / 180.0
        drive = UsdPhysics.DriveAPI(prim, "angular")
        drive.GetStiffnessAttr().Set(d["stiffness_nm_per_rad"] * per_degree)
        drive.GetDampingAttr().Set(d["damping_nms_per_rad"] * per_degree)
        for attr in ("newton:armature", "physxJoint:armature"):   # both, so no resolver default decides it
            prim.CreateAttribute(attr, Sdf.ValueTypeNames.Float).Set(float(d["armature_kgm2"]))
        authored[name] = dict(d)
    buffers_config, buffers = prepare_buffers(stage, scene, timeline)
    apply_buffers(buffers_config)
    return dict(settings=settings, colliders_with_contact_gap=colliders, drives=authored, buffers=buffers,
                stability=stability(drives, newton["physics_hz"], newton.get("inertia_floor_kgm2", {})))

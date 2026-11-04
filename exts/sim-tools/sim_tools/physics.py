"""Backend-specific runtime observations. Never substitute authored USD state."""

from isaac_mcp.project_assets import AssetError, refuse
from .core import vector

# Qualified by the disposable two-cycle motion/joint/timing/export trials on all
# five recorded releases. Build and loaded extension/Warp drift still refuses.
NEWTON_QUALIFIED = True


def body_path(args):
    paths = [args[key] for key in ("path", "prim", "prim_path") if args.get(key) is not None]
    if not paths or any(not isinstance(path, str) or not path for path in paths):
        refuse("INVALID_VALUE", "Supply path (or its alias prim or prim_path) as an absolute rigid body path.")
    if len(set(paths)) != 1:
        refuse("INVALID_VALUE", "path, prim and prim_path refer to the same body; supply only one path or matching values.")
    return paths[0]


def manager():
    try:
        from isaacsim.core.simulation_manager import SimulationManager as sm
    except ImportError:
        refuse(
            "PHYSICS_API_UNAVAILABLE",
            "Enable the installed isaacsim.core.simulation_manager extension.",
        )
    from .compat import adapt_simulation_manager

    sm = adapt_simulation_manager(sm)
    engine = sm.get_active_physics_engine()
    if engine not in ("physx", "newton") or (
        engine == "newton" and not NEWTON_QUALIFIED
    ):
        refuse(
            "BACKEND_UNVERIFIED",
            f"Runtime adapter for {engine!r} is not qualified; no PhysX or USD fallback.",
        )
    if engine == "newton":
        from .runtime import runtime_identity
        from isaac_mcp.sim_versions import newton_stack_issues

        issues = newton_stack_issues(runtime_identity())
        if issues:
            refuse(
                "BACKEND_BUILD_UNVERIFIED",
                "Cannot select a qualified Newton adapter: " + "; ".join(issues),
            )
    return sm


def array(value):
    """Observed tensor data as NumPy, whatever frontend this build's view returned."""
    if hasattr(value, "detach"):  # torch
        return value.detach().cpu().numpy()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return value


def _frontend_data(view, row):
    """One (1, DOFs) row in the same frontend and on the same device as the view's own arrays."""
    module = type(view.get_dof_positions()).__module__
    if module.startswith("warp"):
        import warp as wp

        return wp.array(row, dtype=wp.float32, device=view.get_dof_positions().device)
    if module.startswith("torch"):
        import torch

        return torch.from_numpy(row).to(device=view.get_dof_positions().device)
    return row


def _frontend_indices(view):
    """The articulation index in the view's own frontend."""
    import numpy as np

    module = type(view.get_dof_positions()).__module__
    if module.startswith("warp"):
        import warp as wp

        return wp.array(np.array([0], dtype=np.uint32), dtype=wp.uint32,
                        device=view.get_dof_positions().device)
    if module.startswith("torch"):
        import torch

        return torch.tensor([0], dtype=torch.int32, device=view.get_dof_positions().device)
    return np.array([0], dtype=np.uint32)


def dof_names(view):
    return list(view.shared_metatype.dof_names)


def dof_units(view):
    """Per-DOF position units: 'rad' for a revolute DOF, stage units for a prismatic one."""
    names = dof_names(view)
    try:
        from omni.physics.tensors import DofType

        types = list(view.shared_metatype.dof_types)
        return ["stage units" if t == DofType.Translation else "rad" for t in types]
    except Exception:  # noqa: BLE001 - informational only; the value is still valid
        return ["rad, or stage units for a prismatic DOF"] * len(names)


def dof_limits(view):
    """[[low, high], ...] for one articulation, from either build's return shape.

    The tensor view used here returns one ``(1, DOFs, 2)`` array on both Isaac Sim 5.1 and 6.x;
    the experimental wrapper on some builds returns ``(lower, upper)`` instead.
    """
    value = view.get_dof_limits()
    if isinstance(value, tuple):
        if len(value) != 2:
            refuse("PHYSICS_FORMAT", "Runtime DOF limits must be a pair of bounds.")
        lower, upper = (array(part).reshape(-1).tolist() for part in value)
        return [[float(lo), float(hi)] for lo, hi in zip(lower, upper)]
    data = array(value)
    if getattr(data, "ndim", None) != 3 or data.shape[0] != 1 or data.shape[2] != 2:
        refuse(
            "PHYSICS_FORMAT",
            "Runtime DOF limits have an unexpected shape on this build; no targets were changed.",
        )
    return [[float(lo), float(hi)] for lo, hi in data[0].tolist()]


def dof_gains(view):
    """(stiffnesses, dampings) for one articulation, from either build's gain API."""
    try:
        stiffnesses, dampings = view.get_dof_stiffnesses(), view.get_dof_dampings()
    except AttributeError:
        value = view.get_dof_gains()
        if not isinstance(value, tuple) or len(value) != 2:
            refuse(
                "PHYSICS_API_UNAVAILABLE",
                "This build exposes neither get_dof_stiffnesses/get_dof_dampings nor a gain pair; no gains were changed.",
            )
        stiffnesses, dampings = value
    return (
        array(stiffnesses).reshape(-1).tolist(),
        array(dampings).reshape(-1).tolist(),
    )


def dof_max_forces(view):
    """Drive caps, under this build's own name for them."""
    try:
        value = view.get_dof_max_forces()
    except AttributeError:
        try:
            value = view.get_dof_max_efforts()
        except AttributeError:
            refuse(
                "PHYSICS_API_UNAVAILABLE",
                "This build exposes neither get_dof_max_forces nor get_dof_max_efforts; no gains were changed.",
            )
    return array(value).reshape(-1).tolist()


def dof_row(view, value):
    """One articulation's DOF row as the frontend this build uses (warp on 6.x, NumPy/other below)."""
    import numpy as np

    return np.array(array(value), dtype=np.float32, copy=True).reshape(1, -1)


def dof_write(view, setter, row):
    """Call a tensor setter with the (1, DOFs) row and this view's own frontend/device."""
    getattr(view, setter)(_frontend_data(view, row), _frontend_indices(view))


def dof_set(view, getter, setter, values):
    """Write only the given per-DOF values, leaving the rest of the row untouched."""
    row = dof_row(view, getattr(view, getter)())
    for index, value in values.items():
        row[0, index] = value
    dof_write(view, setter, row)


def runtime_vector(value, count, *, path, field, backend):
    """Validate observed solver data without reporting it as a bad user pose.

    Do not return nonfinite numbers or replace them with authored USD values.
    This is an observation failure, not proof that resetting or retrying is safe.
    """
    try:
        return vector(value, count)
    except AssetError as exc:
        refuse(
            "PHYSICS_STATE_INVALID",
            f"{backend} returned invalid {field} for {path}: {exc}",
            path=path,
            field=field,
            backend=backend,
            effects="Runtime state is invalid. Inspect solver diagnostics before further stepping; no USD fallback or automatic reset was performed.",
        )


def prepare(stage):
    """Prepare only the already-selected engine, before first Play.

    6.0.0.0 uses the Python Newton tensor adapter; later releases ship a
    separate native tensor extension which the base Python app does not enable.
    This never switches engines, chooses a solver or alters a user's USD units.
    """
    sm = manager()
    if sm.get_active_physics_engine() != "newton":
        return
    from pxr import UsdGeom
    from .runtime import runtime_identity

    identity = runtime_identity()
    if not identity["qualified_build"]:
        refuse(
            "BACKEND_BUILD_UNVERIFIED",
            "Newton build identity is missing/conflicting or has not been qualified.",
        )
    if UsdGeom.GetStageMetersPerUnit(stage) != 1.0:
        refuse(
            "BACKEND_UNITS_UNSUPPORTED",
            "Installed Newton requires metersPerUnit=1. Author/convert a separate project in meters; the existing stage has not been rescaled.",
        )
    if identity["release"] != "6.0.0.0":
        import omni.kit.app

        manager_ext = omni.kit.app.get_app().get_extension_manager()
        name = "isaacsim.physics.newton.tensors"
        if not manager_ext.is_extension_enabled(name):
            if not manager_ext.set_extension_enabled_immediate(name, True):
                refuse(
                    "PHYSICS_API_UNAVAILABLE",
                    "The selected Newton engine needs its installed isaacsim.physics.newton.tensors extension before Play.",
                )


def view_for(path, articulation=False):
    sm = manager()
    view = sm.get_physics_simulation_view()
    if view is None:
        refuse(
            "PHYSICS_NOT_READY",
            "Backend tensor view is not initialized. Play, wait for warm-up, then pause.",
        )
    result = (
        view.create_articulation_view(path)
        if articulation
        else view.create_rigid_body_view(path)
    )
    if result.count != 1 or not result.check():
        refuse(
            "PHYSICS_NOT_READY",
            "Expected exactly one valid runtime view; model/stage may have changed. No USD fallback.",
        )
    if articulation and result.max_dofs > 128:
        refuse("LIMIT", "Articulation exceeds 128 DOFs.")
    return result


def body_state(path, time_s, include_dynamics=False, stage=None):
    if type(include_dynamics) is not bool:
        refuse("INVALID_VALUE", "include_dynamics must be boolean.")
    sm = manager()
    engine = sm.get_active_physics_engine()
    if engine == "physx":
        import omni.physx

        pose = omni.physx.get_physx_interface().get_rigidbody_transformation(path)
        if not pose.get("ret_val"):
            refuse(
                "PHYSICS_NOT_READY",
                "PhysX has no initialized body at this path. No USD fallback.",
            )
        q, position = list(pose["rotation"]), list(pose["position"])
        source = "PhysX get_rigidbody_transformation"
    else:
        view = view_for(path)
        transform = array(view.get_transforms()).tolist()[0]
        if len(transform) != 7:
            refuse("PHYSICS_FORMAT", "Newton transform must have seven components.")
        position, q = transform[:3], transform[3:]
        source = "Newton rigid-body tensor view"
    runtime_vector(q, 4, path=path, field="orientation_xyzw", backend=engine)
    runtime_vector(position, 3, path=path, field="world_position", backend=engine)
    result = dict(
        path=path,
        world_position=position,
        orientation_wxyz=[q[3], *q[:3]],
        units="stage units",
        source=source,
        backend=engine,
        time_s=time_s,
        physics_time_s=sm.get_simulation_time(),
    )
    if include_dynamics:
        from pxr import UsdPhysics
        import math

        if stage is None:
            refuse("STALE_TARGET", "Dynamics require the explicitly selected stage.")
        view = view_for(path)
        velocities = array(view.get_velocities()).tolist()[0]
        runtime_vector(velocities, 6, path=path, field="velocities", backend=engine)
        masses = array(view.get_masses()).reshape(-1).tolist()
        if len(masses) != 1 or not math.isfinite(masses[0]) or masses[0] < 0:
            refuse("PHYSICS_FORMAT", "Expected one finite nonnegative runtime mass.")
        result["dynamics"] = dict(
            linear_velocity=velocities[:3],
            angular_velocity=velocities[3:],
            linear_units="stage units/second, world frame at center of mass",
            angular_units="radians/second, world frame",
            mass=masses[0],
            mass_kg=masses[0] * UsdPhysics.GetStageKilogramsPerUnit(stage),
            source=engine + " rigid-body tensor view",
        )
    return result


def newton_stage(stage):
    import omni.usd
    from isaacsim.physics.newton import acquire_stage

    native = acquire_stage()
    if (
        native is None
        or not native.initialized
        or native.stage_id != omni.usd.get_context().get_stage_id()
        or omni.usd.get_context().get_stage() != stage
    ):
        refuse("PHYSICS_NOT_READY", "Newton's initialized stage is stale.")
    return native


def step(stage, steps):
    """Use the unified counter/callback path, with the older paused-step fix.

    Newton <=6.0.1 increments its clock while skipping the solver when its
    internal playing flag is false. Temporarily allow that synchronous native
    operation, without changing the timeline or yielding to the app loop.
    Older capture code also skips the first captured step and bakes in dt.
    Direct solver stepping avoids both issues; invalidate its cached graph
    because direct steps may swap state buffers referenced by that graph.
    """
    sm = manager()
    if sm.get_active_physics_engine() != "newton":
        sm.step(steps=steps)
        return
    from .runtime import runtime_identity

    identity = runtime_identity()
    native = newton_stage(stage)
    playing = native.playing
    capture = native.cfg.use_cuda_graph
    try:
        if identity["release"] in ("6.0.0.0", "6.0.0.1", "6.0.1.0"):
            native.playing = True
        native.cfg.use_cuda_graph = False
        native.graph = None
        sm.step(steps=steps)
    finally:
        native.playing = playing
        native.cfg.use_cuda_graph = capture
        native.graph = None


def sync_fabric(stage):
    """Publish paused Newton steps to rendering, without pumping Kit or editing USD."""
    sm = manager()
    if sm.get_active_physics_engine() != "newton":
        return False
    native = newton_stage(stage)
    if not callable(getattr(native, "update_fabric_attrs", None)):
        refuse(
            "PHYSICS_API_UNAVAILABLE",
            "Newton cannot publish paused runtime poses to Fabric.",
        )
    if not native.cfg.update_fabric:
        refuse(
            "FABRIC_DISABLED",
            "Enable Newton's Fabric updates before step-and-observe rendering.",
        )
    native.update_fabric_attrs()
    return True


def snapshot_poses(stage, time_s):
    """Bounded rigid-body poses for export only. Not a solver checkpoint."""
    sm = manager()
    if sm.get_active_physics_engine() != "newton":
        return []
    from pxr import UsdPhysics

    paths = []
    for index, prim in enumerate(stage.Traverse()):
        if index >= 10000:
            refuse("LIMIT", "Runtime snapshot traversal exceeds 10000 prims.")
        if (
            prim.HasAPI(UsdPhysics.RigidBodyAPI)
            and UsdPhysics.RigidBodyAPI(prim).GetRigidBodyEnabledAttr().Get()
        ):
            if prim.IsInstance() or prim.IsInstanceProxy():
                refuse(
                    "SNAPSHOT_UNSUPPORTED",
                    "Runtime pose export of instanced rigid bodies is unsupported.",
                )
            paths.append(str(prim.GetPath()))
            if len(paths) > 256:
                refuse("LIMIT", "Runtime pose snapshot exceeds 256 rigid bodies.")
    return [body_state(p, time_s) for p in paths]

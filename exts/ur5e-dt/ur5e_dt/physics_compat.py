"""Small USD adapters for the twin's selected physics engine.

These author the scene assembled by the twin, never its referenced asset files.
They do not select or fall back to a different engine or rewrite vendor code.
"""

import re
import weakref
import math


class _ManagedStepSubscription:
    """Own one unified callback without retaining its extension instance."""

    def __init__(self, manager, event, callback, get_stage):
        self._manager = manager
        self._get_stage = get_stage
        self._stage = get_stage()
        if self._stage is None:
            raise RuntimeError("A selected stage is required for the twin's physics callback")
        self._callback = weakref.WeakMethod(callback)
        self._closed = False
        self._uid = manager.register_callback(self._on_step, event=event)

    def _on_step(self, dt, context=None):
        callback = self._callback()
        if not self._closed and callback is not None and self._get_stage() == self._stage:
            callback(dt)

    def close(self):
        self._closed = True
        if self._uid is not None:
            self._manager.deregister_callback(self._uid)
            self._uid = None
        self._stage = None


def subscribe_physics_step(callback):
    """Keep PhysX's existing subscription; use engine-neutral events on Newton."""
    if selected_engine() == "physx":
        import omni.physx

        return omni.physx.get_physx_interface().subscribe_physics_step_events(callback)
    import omni.usd
    import isaacsim.core.simulation_manager as simulation

    events = getattr(simulation, "SimulationEvent", None)
    if events is not None:
        event = events.PHYSICS_POST_STEP
    else:
        # The older packages expose IsaacEvents instead of SimulationEvent.
        event = simulation.IsaacEvents.POST_PHYSICS_STEP
    return _ManagedStepSubscription(
        simulation.SimulationManager, event, callback,
        omni.usd.get_context().get_stage,
    )


def close_physics_step(subscription):
    # Legacy native subscriptions release on dropping the Python reference.
    # SimulationManager owns callbacks by ID, so ours need explicit removal.
    if isinstance(subscription, _ManagedStepSubscription):
        subscription.close()


def selected_engine():
    from isaacsim.core.simulation_manager import SimulationManager

    engine = SimulationManager.get_active_physics_engine()
    if engine not in ("physx", "newton"):
        raise RuntimeError(f"The UR5e twin has no adapter for physics engine {engine!r}")
    return engine


def configure_newton_gripper(prim, engine):
    """Explicit force gains for the twin, preserving the 1 N m torque limit.

    The native importer reads USD angular gains per degree. It does not
    implement the PhysX acceleration-drive normalization used by this asset.
    These are force gains of 30 N m/rad and 8 N m s/rad, not a unit-only
    conversion of the acceleration controller. Mimics remain constraints.
    """
    if engine != "newton":
        return
    values = {
        "drive:angular:physics:type": "force",
        "drive:angular:physics:stiffness": 30.0 * math.pi / 180,
        "drive:angular:physics:damping": 8.0 * math.pi / 180,
    }
    for name, value in values.items():
        attr = prim.GetAttribute(name)
        if not attr or not attr.Set(value):
            raise RuntimeError(f"Could not author Newton gripper {name}; inspect partial scene")


def runtime_link_matrix(articulation, name):
    """Current tensor link pose; never substitute an authored USD transform."""
    import numpy as np
    from pxr import Gf

    view = articulation._physics_view
    if view is None or not view.check() or view.count != 1:
        raise RuntimeError("Gripper runtime articulation is not ready")
    indices = view.shared_metatype.link_indices
    if name not in indices:
        raise RuntimeError(f"Runtime articulation has no link {name!r}")
    values = view.get_link_transforms()
    if hasattr(values, "numpy"):
        values = values.numpy()
    pose = np.asarray(values)[0, indices[name]]
    if pose.shape != (7,) or not np.isfinite(pose).all():
        raise RuntimeError("Invalid runtime link transform")
    if abs(float(np.linalg.norm(pose[3:])) - 1) > 1e-3:
        raise RuntimeError("Runtime link quaternion is not normalized")
    matrix = Gf.Matrix4d().SetRotate(Gf.Quatd(float(pose[6]), Gf.Vec3d(*map(float, pose[3:6]))))
    matrix.SetTranslateOnly(Gf.Vec3d(*map(float, pose[:3])))
    return matrix


def prepare_newton_wrench():
    """Enable supported solver output before creating an owned World.

    Existing initialized simulations are never invalidated or reconfigured.
    Feature absence is reported, not replaced with zero force values.
    """
    if selected_engine() != "newton":
        return
    import copy
    import isaacsim.physics.newton as native

    if not hasattr(native, "NewtonSolverOutputConfig"):
        return
    config = copy.deepcopy(native.get_newton_config()) or native.NewtonConfig()
    if config.solver_outputs.link_incoming_joint_force:
        return
    current = native.acquire_stage()
    if current is not None and current.initialized:
        return
    config.solver_outputs.link_incoming_joint_force = True
    native.configure_newton(config)


def prepare_newton_tensors():
    """Load the bundled native tensor provider before World's first reset.

    6.0.0.0 alone uses the legacy Python provider. Starting from a twin tool
    must not depend on a preceding generic Play having enabled the extension.
    """
    if selected_engine() != "newton":
        return
    import omni.kit.app
    from importlib.metadata import version

    manager = omni.kit.app.get_app().get_extension_manager()
    name = "isaacsim.physics.newton.tensors"
    if manager.is_extension_enabled(name):
        return
    available = any(x.get("id", "").startswith(name + "-") for x in manager.get_extensions())
    if not available:
        if version("isaacsim-core") == "6.0.0.0":
            return
        raise RuntimeError(f"The selected Newton engine needs bundled {name}; it is unavailable")
    if not manager.set_extension_enabled_immediate(name, True):
        raise RuntimeError(f"Could not enable bundled {name}; no physics engine was switched")


def prepare_saved_twin_runtime(stage, timeline):
    """Restore process state for explicit UR Play, without creating a World.

    Device/output configuration is not serialized in USD. World initialization
    would also reauthor physics defaults, so use the selected manager directly.
    Never change an already initialized model's device or solver.
    """
    if selected_engine() != "newton":
        return
    from pxr import UsdGeom
    from isaacsim.core.simulation_manager import SimulationManager
    from isaacsim.core.api.world import World
    import isaacsim.physics.newton as native

    if stage.GetPrimAtPath("/World/UR5e") and stage.GetPrimAtPath("/World/RG2_Gripper"):
        require_newton_mount_support("newton")
    if UsdGeom.GetStageMetersPerUnit(stage) != 1 or UsdGeom.GetStageUpAxis(stage) != "Z":
        raise RuntimeError("The Newton UR5e twin requires a meter/Z-up stage; no units or axes were changed")
    current = native.acquire_stage()
    initialized = current is not None and current.initialized
    world = World.instance()
    if world is not None and str(world.device) != "cpu":
        raise RuntimeError("The existing World is not CPU; use a separate twin stage. It was not reconfigured")
    if initialized and str(current.model.device) != "cpu":
        raise RuntimeError("The active Newton model is not CPU; Stop before restoring twin callbacks. It was not reconfigured")
    if not initialized and not timeline.is_stopped():
        raise RuntimeError("Stop the timeline before restoring the saved Newton twin's runtime")
    prepare_newton_tensors()
    if not initialized:
        prepare_newton_wrench()
        SimulationManager.set_backend("numpy")
        SimulationManager.set_physics_sim_device("cpu")


def wrench_capability():
    engine = selected_engine()
    if engine == "physx":
        return {"available": True, "backend": engine}
    import isaacsim.physics.newton as native

    cfg = native.get_newton_config() if hasattr(native, "get_newton_config") else None
    outputs = getattr(cfg, "solver_outputs", None)
    reason = None
    if outputs is None:
        reason = "Installed Newton tensor backend has no supported incoming-joint-wrench output"
    elif not outputs.link_incoming_joint_force:
        reason = "Newton joint-wrench output was not enabled before initialization; use a fresh twin stage"
    else:
        solver = getattr(cfg, "solver_cfg", None)
        if getattr(solver, "use_mujoco_cpu", False):
            reason = "Newton's native MuJoCo CPU solver does not provide incoming joint wrenches"
        elif getattr(solver, "solver_type", None) not in ("None", "mujoco", "xpbd"):
            reason = "The selected Newton solver has no qualified incoming-joint-wrench adapter"
    return {"available": reason is None, "backend": engine, "reason": reason,
            "quantity": "tree-parent wrench; loop-closing equality reaction is not a separate sensor"}


def newton_tool_wrench(articulation, stage):
    """Map the named wrist child-joint wrench to tool0, including its origin."""
    import numpy as np
    from pxr import Gf, UsdGeom, UsdPhysics

    view = articulation._physics_view
    if view is None or not view.check() or view.count != 1:
        raise RuntimeError("Wrist articulation is not ready")
    names = view.shared_metatype.link_indices
    if "wrist_3_link" not in names:
        raise RuntimeError("Runtime articulation has no wrist_3_link")
    joint = UsdPhysics.Joint(stage.GetPrimAtPath("/World/UR5e/joints/wrist_3_joint"))
    wrist = stage.GetPrimAtPath("/World/UR5e/wrist_3_link")
    tool = stage.GetPrimAtPath("/World/UR5e/wrist_3_link/flange/tool0")
    if not joint or not wrist or not tool or list(map(str, joint.GetBody1Rel().GetTargets())) != [str(wrist.GetPath())]:
        raise RuntimeError("Expected named wrist joint and tool0 frame are unavailable")
    tool_in_wrist, reset = UsdGeom.XformCache().ComputeRelativeTransform(tool, wrist)
    if reset:
        raise RuntimeError("tool0 resets its transform stack")
    joint_in_wrist = Gf.Matrix4d().SetRotate(Gf.Quatd(joint.GetLocalRot1Attr().Get()))
    joint_in_wrist.SetTranslateOnly(Gf.Vec3d(joint.GetLocalPos1Attr().Get()))
    to_tool = joint_in_wrist * tool_in_wrist.GetInverse()
    values = view.get_link_incoming_joint_force()
    if hasattr(values, "numpy"):
        values = values.numpy()
    wrench = np.asarray(values).reshape(1, view.max_links, 6)[0, names["wrist_3_link"]]
    if not np.isfinite(wrench).all():
        raise RuntimeError("Native wrist wrench is not finite")
    force = to_tool.TransformDir(Gf.Vec3d(*map(float, wrench[:3])))
    torque = to_tool.TransformDir(Gf.Vec3d(*map(float, wrench[3:])))
    torque += Gf.Cross(to_tool.ExtractTranslation(), force)
    return [*force, *torque]


def physics_stamp(seconds):
    """Convert observed simulation seconds to ROS's bounded sec/nanosec pair."""
    if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Invalid physics timestamp for wrist wrench")
    nanoseconds = round(seconds * 1_000_000_000)
    sec, nanosec = divmod(nanoseconds, 1_000_000_000)
    if sec > 2**31 - 1:
        raise ValueError("Physics timestamp exceeds the ROS signed-second range")
    return sec, nanosec


def require_newton_mount_support(engine, version=None):
    """Preflight before assembling the twin, including the older importer limit."""
    if engine != "newton":
        return
    if version is None:
        import newton

        version = newton.__version__
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[+.-].*)?", str(version))
    if match is None or tuple(map(int, match.groups())) < (1, 2, 0):
        raise RuntimeError(
            "Newton's installed USD importer cannot represent the twin's separate "
            "UR5e/RG2 attachment reliably (requires MjcEqualityWeldAPI ingestion, "
            f"observed Newton {version!r}). Quick Start has not assembled the scene. "
            "Use a separate PhysX session for the complete twin; generic Newton "
            "tools and standalone robots have separate support. No engine was switched."
        )


def configure_newton_rate(prim, rate, engine):
    """Keep the Newton outer rate and its MuJoCo solver clock consistent."""
    if engine != "newton":
        return
    if type(rate) is not int or not 1 <= rate <= 1000:
        raise ValueError("Physics rate must be an integer in 1..1000 Hz")
    solver_attr = prim.GetAttribute("newton:solver")
    solver = solver_attr.Get() if solver_attr else None
    if solver not in (None, "", "mjcwarp", "mujoco"):
        raise RuntimeError(
            f"The Newton twin adapter is for the MuJoCo solver, observed {solver!r}; "
            "no solver was changed"
        )
    schemas = ("NewtonSceneAPI", "MjcSceneAPI")
    # Check both before authoring: a missing bundled schema must not leave a
    # half-configured clock, nor silently fall back to PhysX attributes.
    for name in schemas:
        if not prim.CanApplyAPI(name):
            raise RuntimeError(f"The selected Newton scene needs bundled {name}")
    for name in schemas:
        if not prim.ApplyAPI(name):
            raise RuntimeError(f"Could not apply {name}; inspect partial scene configuration")
    for name, value in (
        ("newton:timeStepsPerSecond", rate),
        ("mjc:option:timestep", 1 / rate),
    ):
        attr = prim.GetAttribute(name)
        if not attr or not attr.Set(value):
            raise RuntimeError(f"Could not author {name}; inspect partial scene configuration")


def configure_newton_mount(adapter_joint, gripper_joint, engine):
    """Keep QC in the arm tree, RG2 separate, and defer the inter-tree weld.

    In the inspected Newton importers an excluded joint to the otherwise free
    QC can resolve its not-yet-added body to the world. The equality API imports
    the inter-articulation constraint after both body trees have been built.
    Pose offsets, mass, collisions and the gripper's six DOFs are preserved.
    """
    if engine != "newton":
        return
    if not gripper_joint.CanApplyAPI("MjcEqualityWeldAPI"):
        raise RuntimeError("The selected Newton twin needs bundled MjcEqualityWeldAPI")
    if not gripper_joint.ApplyAPI("MjcEqualityWeldAPI"):
        raise RuntimeError("Could not apply Newton attachment schema; inspect partial scene")
    if not adapter_joint.GetAttribute("physics:excludeFromArticulation").Set(False):
        raise RuntimeError("Could not include QuickChanger in Newton arm; inspect partial scene")

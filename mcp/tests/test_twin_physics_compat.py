"""Engine-specific twin authoring must not silently select another backend."""

import importlib.util
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[2] / "exts/ur5e-dt/ur5e_dt/physics_compat.py"
spec = importlib.util.spec_from_file_location("twin_physics_compat_test", path)
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


class Prim:
    def __init__(self, missing=(), failed=None):
        self.missing = missing
        self.failed = failed
        self.events = []
        self.values = {}

    def CanApplyAPI(self, name):
        self.events.append(("check", name))
        return name not in self.missing

    def ApplyAPI(self, name):
        self.events.append(("apply", name))
        return name != self.failed

    def GetAttribute(self, name):
        prim = self

        class Attribute:
            def Get(self):
                return prim.values.get(name)

            def Set(self, value):
                prim.events.append(("set", name, value))
                if name == prim.failed:
                    return False
                prim.values[name] = value
                return True

        return Attribute()


@pytest.mark.parametrize("rate", [60, 120, 240, 1000])
def test_newton_clocks_agree_at_requested_rate(rate):
    prim = Prim()
    compat.configure_newton_rate(prim, rate, "newton")
    assert prim.values == {
        "newton:timeStepsPerSecond": rate,
        "mjc:option:timestep": 1 / rate,
    }
    assert prim.events[:2] == [("check", "NewtonSceneAPI"), ("check", "MjcSceneAPI")]


@pytest.mark.parametrize("rate", [True, 0, 1001, 120.5, "120"])
def test_invalid_rate_has_no_usd_effect(rate):
    prim = Prim()
    with pytest.raises(ValueError, match="integer"):
        compat.configure_newton_rate(prim, rate, "newton")
    assert not prim.events


def test_other_newton_solver_refused_without_reconfiguration():
    prim = Prim()
    prim.values["newton:solver"] = "vbd"
    with pytest.raises(RuntimeError, match="no solver was changed"):
        compat.configure_newton_rate(prim, 120, "newton")
    assert prim.events == []
    assert prim.values == {"newton:solver": "vbd"}


@pytest.mark.parametrize("missing", ["NewtonSceneAPI", "MjcSceneAPI"])
def test_missing_rate_schema_refuses_before_any_authoring(missing):
    prim = Prim(missing={missing})
    with pytest.raises(RuntimeError, match=missing):
        compat.configure_newton_rate(prim, 120, "newton")
    assert all(event[0] == "check" for event in prim.events)


@pytest.mark.parametrize("failed", ["MjcSceneAPI", "mjc:option:timestep"])
def test_native_authoring_failure_reports_partial_effects(failed):
    prim = Prim(failed=failed)
    with pytest.raises(RuntimeError, match="partial"):
        compat.configure_newton_rate(prim, 120, "newton")


def test_physx_keeps_its_existing_scene_and_mount_policy():
    first, second = Prim(), Prim()
    compat.configure_newton_rate(first, 120, "physx")
    compat.configure_newton_mount(first, second, "physx")
    compat.require_newton_mount_support("physx", None)
    assert not first.events and not second.events


@pytest.mark.parametrize("version", [None, "unknown", "1.0.0", "1.1.9", "garbage1.2.0"])
def test_unsupported_importer_is_an_actionable_refusal(version):
    # Pass an explicit string so this CPU test never imports native Newton.
    with pytest.raises(RuntimeError, match="No engine was switched"):
        compat.require_newton_mount_support("newton", str(version))


@pytest.mark.parametrize("version", ["1.2.0", "1.2.1", "1.5.0", "1.6.1"])
def test_installed_importers_with_equality_ingestion_pass_preflight(version):
    compat.require_newton_mount_support("newton", version)


def test_missing_weld_schema_cannot_partially_merge_adapter():
    first, second = Prim(), Prim(missing={"MjcEqualityWeldAPI"})
    with pytest.raises(RuntimeError, match="MjcEqualityWeldAPI"):
        compat.configure_newton_mount(first, second, "newton")
    assert first.events == []
    assert second.events == [("check", "MjcEqualityWeldAPI")]


def test_newton_mount_keeps_offsets_and_gripper_dofs_untouched():
    first, second = Prim(), Prim()
    first.values = {"physics:localPos0": (1, 2, 3), "physics:excludeFromArticulation": True}
    second.values = {"physics:localPos0": (0.01592, 0, 0), "physics:excludeFromArticulation": True}
    compat.configure_newton_mount(first, second, "newton")
    assert first.values == {"physics:localPos0": (1, 2, 3), "physics:excludeFromArticulation": False}
    assert second.values == {"physics:localPos0": (0.01592, 0, 0), "physics:excludeFromArticulation": True}
    assert ("apply", "MjcEqualityWeldAPI") in second.events


class Manager:
    def __init__(self):
        self.callbacks = {}
        self.removed = []
        self.events = []

    def register_callback(self, callback, *, event):
        self.callbacks[0] = callback
        self.events.append(event)
        return 0

    def deregister_callback(self, uid):
        self.removed.append(uid)
        self.callbacks.pop(uid)
        return True


class Receiver:
    def __init__(self):
        self.steps = []

    def step(self, dt):
        self.steps.append(dt)


def test_unified_subscription_bounds_stage_and_has_explicit_idempotent_cleanup():
    manager, receiver = Manager(), Receiver()
    stage = [object()]
    sub = compat._ManagedStepSubscription(manager, "post", receiver.step, lambda: stage[0])
    native = manager.callbacks[0]
    native(1 / 120, object())
    assert receiver.steps == [1 / 120]
    stage[0] = object()
    native(1 / 120, object())
    assert receiver.steps == [1 / 120]
    compat.close_physics_step(sub)
    compat.close_physics_step(sub)
    native(1 / 120, object())  # An already-queued native event after close.
    assert receiver.steps == [1 / 120]
    assert manager.removed == [0] and not manager.callbacks


def test_unified_subscription_does_not_retain_extension():
    import gc
    import weakref
    manager, receiver, stage = Manager(), Receiver(), object()
    sub = compat._ManagedStepSubscription(manager, "post", receiver.step, lambda: stage)
    owner = weakref.ref(receiver)
    del receiver
    gc.collect()
    assert owner() is None
    manager.callbacks[0](0.01, None)
    sub.close()
    assert manager.removed == [0]


def test_absent_stage_refuses_before_registering_callback():
    manager, receiver = Manager(), Receiver()
    with pytest.raises(RuntimeError, match="selected stage"):
        compat._ManagedStepSubscription(manager, "post", receiver.step, lambda: None)
    assert not manager.callbacks


@pytest.mark.parametrize("modern", [False, True])
def test_newton_uses_available_unified_event_enum(monkeypatch, modern):
    import sys
    import types
    manager, receiver, stage = Manager(), Receiver(), object()
    simulation = types.ModuleType("isaacsim.core.simulation_manager")
    simulation.SimulationManager = manager
    simulation.IsaacEvents = types.SimpleNamespace(POST_PHYSICS_STEP="legacy_post")
    if modern:
        simulation.SimulationEvent = types.SimpleNamespace(PHYSICS_POST_STEP="modern_post")
    isaacsim, core = types.ModuleType("isaacsim"), types.ModuleType("isaacsim.core")
    isaacsim.core = core
    core.simulation_manager = simulation
    omni, usd = types.ModuleType("omni"), types.ModuleType("omni.usd")
    omni.usd = usd
    usd.get_context = lambda: types.SimpleNamespace(get_stage=lambda: stage)
    for module in [simulation, isaacsim, core, omni, usd]:
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(compat, "selected_engine", lambda: "newton")
    sub = compat.subscribe_physics_step(receiver.step)
    assert manager.events == ["modern_post" if modern else "legacy_post"]
    manager.callbacks[0](0.02, None)
    assert receiver.steps == [0.02]
    compat.close_physics_step(sub)


def test_physx_keeps_existing_native_subscription(monkeypatch):
    import sys
    import types
    sentinel = object()
    receiver, callbacks = Receiver(), []
    def subscribe(callback):
        callbacks.append(callback)
        return sentinel
    omni, physx = types.ModuleType("omni"), types.ModuleType("omni.physx")
    omni.physx = physx
    physx.get_physx_interface = lambda: types.SimpleNamespace(subscribe_physics_step_events=subscribe)
    monkeypatch.setitem(sys.modules, "omni", omni)
    monkeypatch.setitem(sys.modules, "omni.physx", physx)
    monkeypatch.setattr(compat, "selected_engine", lambda: "physx")
    assert compat.subscribe_physics_step(receiver.step) is sentinel
    assert callbacks == [receiver.step]
    compat.close_physics_step(sentinel)  # Legacy handle releases by reference lifetime.


def test_newton_force_drive_preserves_torque_limit_and_mimic_attributes():
    import math
    prim = Prim()
    prim.values = {"drive:angular:physics:maxForce": 1.0, "physxMimicJoint:rotZ:gearing": -1}
    compat.configure_newton_gripper(prim, "newton")
    assert prim.values["drive:angular:physics:maxForce"] == 1
    assert prim.values["physxMimicJoint:rotZ:gearing"] == -1
    assert prim.values["drive:angular:physics:type"] == "force"
    assert prim.values["drive:angular:physics:stiffness"] * 180 / math.pi == pytest.approx(30)
    assert prim.values["drive:angular:physics:damping"] * 180 / math.pi == pytest.approx(8)
    other = Prim()
    compat.configure_newton_gripper(other, "physx")
    assert not other.events


def install_native(monkeypatch, config, initialized=False, modern=True):
    import sys
    import types
    isaac, physics, native = (types.ModuleType(x) for x in ("isaacsim", "isaacsim.physics", "isaacsim.physics.newton"))
    isaac.physics, physics.newton = physics, native
    native.get_newton_config = lambda: config
    native.acquire_stage = lambda: types.SimpleNamespace(initialized=initialized)
    native.configured = []
    native.configure_newton = native.configured.append
    if modern:
        native.NewtonSolverOutputConfig = object
    for m in (isaac, physics, native):
        monkeypatch.setitem(sys.modules, m.__name__, m)
    monkeypatch.setattr(compat, "selected_engine", lambda: "newton")
    return native


def native_config(enabled=False, solver="mujoco", cpu=False):
    from types import SimpleNamespace as NS
    return NS(solver_outputs=NS(link_incoming_joint_force=enabled),
              solver_cfg=NS(solver_type=solver, use_mujoco_cpu=cpu),
              untouched={"device": "cpu", "substeps": 3})


def test_output_optin_copies_config_without_mutating_original(monkeypatch):
    cfg = native_config()
    native = install_native(monkeypatch, cfg)
    compat.prepare_newton_wrench()
    assert len(native.configured) == 1
    updated = native.configured[0]
    assert updated is not cfg and updated.solver_outputs is not cfg.solver_outputs
    assert updated.solver_outputs.link_incoming_joint_force
    assert not cfg.solver_outputs.link_incoming_joint_force
    assert vars(updated.solver_cfg) == vars(cfg.solver_cfg) and updated.untouched == cfg.untouched


@pytest.mark.parametrize("initialized,enabled,modern", [(True, False, True), (True, True, True), (False, False, False)])
def test_no_late_rebuild_or_old_backend_output_guess(monkeypatch, initialized, enabled, modern):
    native = install_native(monkeypatch, native_config(enabled), initialized, modern)
    compat.prepare_newton_wrench()
    assert not native.configured


@pytest.mark.parametrize("enabled,solver,cpu,available", [
    (False, "mujoco", False, False), (True, "mujoco", False, True),
    (True, "mujoco", True, False), (True, "vbd", False, False),
])
def test_wrench_capability_does_not_replace_unavailable_output(monkeypatch, enabled, solver, cpu, available):
    native = install_native(monkeypatch, native_config(enabled, solver, cpu))
    result = compat.wrench_capability()
    assert result["available"] is available
    assert bool(result["reason"]) is not available
    assert not native.configured


def test_runtime_link_pose_rejects_missing_and_nonfinite_data():
    np = pytest.importorskip("numpy")
    pytest.importorskip("pxr")
    from types import SimpleNamespace as NS
    values = np.array([[[1., 2., 3., 0., 0., 0., 1.]]])
    view = NS(check=lambda: True, count=1, shared_metatype=NS(link_indices={"finger": 0}),
              get_link_transforms=lambda: values)
    art = NS(_physics_view=view)
    assert list(compat.runtime_link_matrix(art, "finger").ExtractTranslation()) == [1., 2., 3.]
    with pytest.raises(RuntimeError, match="no link"):
        compat.runtime_link_matrix(art, "missing")
    values[0, 0, 0] = float("nan")
    with pytest.raises(RuntimeError, match="Invalid"):
        compat.runtime_link_matrix(art, "finger")


@pytest.mark.parametrize("rotation, expected", [
    ((0, 0, 0), [0, 2, 0, 0, 0, -2]),
    ((0, 0, 90), [2, 0, 0, 0, 0, -2]),
    ((0, 90, 0), [0, 2, 0, 2, 0, 0]),
])
def test_wrench_selects_named_wrist_and_transforms_frame_and_origin(rotation, expected):
    np = pytest.importorskip("numpy")
    pxr = pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom, UsdPhysics, Gf
    from types import SimpleNamespace as NS
    s = Usd.Stage.CreateInMemory()
    wrist = UsdGeom.Xform.Define(s, "/World/UR5e/wrist_3_link")
    tool = UsdGeom.Xform.Define(s, "/World/UR5e/wrist_3_link/flange/tool0")
    tool.AddTranslateOp().Set(Gf.Vec3d(1, 0, 0))
    tool.AddRotateXYZOp().Set(Gf.Vec3f(*rotation))
    joint = UsdPhysics.RevoluteJoint.Define(s, "/World/UR5e/joints/wrist_3_joint")
    joint.CreateBody1Rel().SetTargets([wrist.GetPath()])
    # QC is last, deliberately has unrelated force values. Use the named wrist.
    forces = np.array([[[0., 2., 0., 0., 0., 0.], [99.] * 6]])
    view = NS(check=lambda: True, count=1, max_links=2,
              shared_metatype=NS(link_indices={"wrist_3_link": 0, "quick_changer_link": 1}),
              get_link_incoming_joint_force=lambda: forces)
    got = compat.tool_wrench(NS(_physics_view=view), s)
    assert got == pytest.approx(expected)
    forces[0, 0, 0] = float("nan")
    with pytest.raises(RuntimeError, match="not finite"):
        compat.tool_wrench(NS(_physics_view=view), s)


@pytest.mark.parametrize("seconds, expected", [
    (0., (0, 0)), (1 / 120, (0, 8_333_333)),
    (3.9999999996, (4, 0)), (12.25, (12, 250_000_000)),
])
def test_physics_timestamp_uses_simulation_time_with_normalized_nanoseconds(seconds, expected):
    assert compat.physics_stamp(seconds) == expected


@pytest.mark.parametrize("seconds", [True, -0.1, float("nan"), float("inf"), 2**31])
def test_invalid_physics_timestamp_is_not_published(seconds):
    with pytest.raises(ValueError, match="timestamp"):
        compat.physics_stamp(seconds)


@pytest.mark.parametrize("initialized,device,stopped,world_device,error", [
    (False, "cuda:0", True, None, None),
    (True, "cpu", False, "cpu", None),
    (True, "cuda:0", True, None, "active Newton model"),
    (False, "cuda:0", False, None, "Stop the timeline"),
    (False, "cpu", True, "cuda:0", "existing World"),
])
def test_saved_twin_runtime_restores_only_uninitialized_process_configuration(
    monkeypatch, initialized, device, stopped, world_device, error
):
    import sys
    import types
    from types import SimpleNamespace as NS
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    before = stage.GetRootLayer().ExportToString()
    native = install_native(monkeypatch, native_config(), initialized)
    native.acquire_stage = lambda: NS(initialized=initialized, model=NS(device=device))
    events = []
    modules = {name: types.ModuleType(name) for name in (
        "isaacsim.core", "isaacsim.core.api", "isaacsim.core.api.world", "isaacsim.core.simulation_manager")}
    modules["isaacsim.core.api.world"].World = NS(instance=lambda: NS(device=world_device) if world_device else None)
    modules["isaacsim.core.simulation_manager"].SimulationManager = NS(
        set_backend=lambda value: events.append(("backend", value)),
        set_physics_sim_device=lambda value: events.append(("device", value)))
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(compat, "prepare_newton_tensors", lambda: events.append("provider"))
    monkeypatch.setattr(compat, "prepare_newton_wrench", lambda: events.append("output"))
    timeline = NS(is_stopped=lambda: stopped)
    if error:
        with pytest.raises(RuntimeError, match=error):
            compat.prepare_saved_twin_runtime(stage, timeline)
        assert events == []
    else:
        compat.prepare_saved_twin_runtime(stage, timeline)
        assert events == (["provider"] if initialized else
                          ["provider", "output", ("backend", "numpy"), ("device", "cpu")])
    assert stage.GetRootLayer().ExportToString() == before
    assert not native.configured


@pytest.mark.parametrize("engine", ["physx", "newton"])
def test_rebinding_saved_graphs_does_not_rebuild_or_author_usd(monkeypatch, engine):
    import ast
    from types import SimpleNamespace as NS
    source = ast.parse((path.parent / "extension.py").read_text())
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "DigitalTwin")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_resubscribe_physics_callbacks")
    events = []
    stage = NS(GetPrimAtPath=lambda path: True)  # No USD mutation API supplied.
    namespace = {"omni": NS(usd=NS(get_context=lambda: NS(get_stage=lambda: stage))),
                 "og": NS(get_node_by_path=lambda p: p),
                 "selected_engine": lambda: engine,
                 "wrench_capability": lambda: {"available": True},
                 "subscribe_physics_step": lambda callback: events.append(callback) or callback}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "rebind", "exec"), namespace)
    owner = NS(_stop_gripper_physics=lambda: events.append("close gripper"),
               _stop_physics_callback=lambda *a: events.append("close force"),
               _set_force_gate=lambda *a, **kw: events.append("gate"),
               _on_physics_step_gripper="gripper", _on_physics_step_force="force")
    namespace[fn.name](owner)
    assert events == ["close gripper", "gripper", "close force", "gate", "force"]
    assert owner._pad_local is None
    assert owner._gripper_pad_attr.endswith("/pad_pub.inputs:data")
    assert owner._force_publish_active


def test_gate_cleanup_before_graph_creation_never_queries_missing_og_attribute():
    import ast
    from types import SimpleNamespace as NS
    source = ast.parse((path.parent / "extension.py").read_text())
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "DigitalTwin")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_set_force_gate")
    values, queried = [], []
    present = [False]
    stage = NS(GetPrimAtPath=lambda p: present[0])
    namespace = {"omni": NS(usd=NS(get_context=lambda: NS(get_stage=lambda: stage))),
                 "og": NS(Controller=NS(attribute=lambda p: queried.append(p) or NS(set=values.append)))}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "force_gate", "exec"), namespace)
    gate = namespace["_set_force_gate"]
    gate(None, False)
    assert not queried
    with pytest.raises(RuntimeError, match="validity gate"):
        gate(None, True, required=True)
    assert not queried
    present[0] = True
    gate(None, False)
    gate(None, True, required=True)
    assert values == [False, True]


@pytest.mark.parametrize("initial_frame", ["", "stale-frame"])
@pytest.mark.parametrize("header_failure", [False, True])
@pytest.mark.parametrize("engine", ["physx", "newton"])
def test_force_callback_restores_frame_and_time_before_publishing(
        monkeypatch, initial_frame, header_failure, engine):
    import ast
    import sys
    from types import SimpleNamespace as NS

    source = ast.parse((path.parent / "extension.py").read_text())
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "DigitalTwin")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_on_physics_step_force")
    values = {"inputs:header:frame_id": initial_frame}
    events = []

    def attribute(name, node):
        def put(value):
            if name == "inputs:header:frame_id" and header_failure:
                raise RuntimeError("header unavailable")
            values[name] = value
            events.append((name, value))
        return NS(set=put)

    namespace = {
        "selected_engine": lambda: engine,
        "og": NS(Controller=NS(attribute=attribute)),
        "omni": NS(usd=NS(get_context=lambda: NS(get_stage=lambda: object()))),
        "tool_wrench": lambda *args: [1, 2, 3, 4, 5, 6],
        "physics_stamp": compat.physics_stamp,
        "wrench_capability": lambda: {"available": True},
    }
    monkeypatch.setitem(sys.modules, "isaacsim.core.simulation_manager", NS(
        SimulationManager=NS(get_simulation_time=lambda: 1.25)))
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "force_callback", "exec"), namespace)
    owner = NS(_force_pub_node=object(),
               _set_force_gate=lambda value, **kw: events.append(("gate", value)),
               _lazy_init_articulation=lambda *args: NS(_physics_view=object()))
    namespace[fn.name](owner, 1 / 120)
    assert events[0] == ("gate", False)
    if header_failure:
        assert ("gate", True) not in events
        assert owner._force_publish_status["available"] is False
        assert owner._force_publish_status["reason"] == "header unavailable"
    else:
        assert values["inputs:header:frame_id"] == "tool0"
        assert values["inputs:header:stamp:sec"] == 1
        assert values["inputs:header:stamp:nanosec"] == 250_000_000
        assert events[-1] == ("gate", True)
        assert owner._force_publish_status["status"] == "ready"


@pytest.mark.parametrize("enabled,available,release,success,expected", [
    (True, True, "6.2.0.0", True, None),
    (False, True, "6.2.0.0", True, None),
    (False, True, "6.2.0.0", False, "Could not enable"),
    (False, False, "6.0.0.0", True, None),
    (False, False, "6.0.0.1", True, "unavailable"),
])
def test_newton_twin_owns_initial_tensor_provider_setup(monkeypatch, enabled, available, release, success, expected):
    import sys
    import types
    import importlib.metadata
    calls = []
    manager = types.SimpleNamespace(
        is_extension_enabled=lambda name: enabled,
        get_extensions=lambda: [{"id": "isaacsim.physics.newton.tensors-0.8.1"}] if available else [],
        set_extension_enabled_immediate=lambda name, value: calls.append((name, value)) or success)
    omni, kit, app = [types.ModuleType(x) for x in ("omni", "omni.kit", "omni.kit.app")]
    omni.kit, kit.app = kit, app
    app.get_app = lambda: types.SimpleNamespace(get_extension_manager=lambda: manager)
    for m in (omni, kit, app):
        monkeypatch.setitem(sys.modules, m.__name__, m)
    monkeypatch.setattr(compat, "selected_engine", lambda: "newton")
    monkeypatch.setattr(importlib.metadata, "version", lambda name: release)
    if expected:
        with pytest.raises(RuntimeError, match=expected):
            compat.prepare_newton_tensors()
    else:
        compat.prepare_newton_tensors()
    assert calls == ([] if enabled or not available else [("isaacsim.physics.newton.tensors", True)])

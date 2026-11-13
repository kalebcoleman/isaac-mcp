"""Newton parity in the generic scene tools: contact buffers, tower settings, contacts, the push, the timestep
cross-check, the occupancy-map start check and the render report. Everything runs on fakes: no Kit, no GPU, no Newton.

The fakes mirror what the installed Newton exposes (isaacsim.physics.newton NewtonConfig / MuJoCoSolverConfig /
CollisionConfig, NewtonStage.contacts, mujoco_warp Data.nacon / naconmax / nefc / njmax / overflow).
"""

import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

pytest.importorskip("pxr.Usd")
np = pytest.importorskip("numpy")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
import pxr
from pxr import Gf, Usd, UsdGeom, UsdPhysics

try:
    from pxr import PhysxSchema  # noqa: F401
except ImportError:
    pxr.PhysxSchema = mock.MagicMock()
    sys.modules["pxr.PhysxSchema"] = pxr.PhysxSchema
from isaac_mcp.project_assets import AssetError
from sim_tools import advanced as advanced_module, control_run, jenga, newton_support as ns, physics, queries
from sim_tools.advanced import Advanced
from test_scene_usd import scene  # noqa: F401 - the fixture


# --- fakes of the installed Newton configuration ----------------------------------------------------------


@dataclasses.dataclass
class NewtonSolverConfig:
    solver_type: str = "None"


@dataclasses.dataclass
class MuJoCoSolverConfig(NewtonSolverConfig):
    solver_type: str = "mujoco"
    njmax: int = 1200
    nconmax: int | None = 200


@dataclasses.dataclass
class XPBDSolverConfig(NewtonSolverConfig):
    solver_type: str = "xpbd"


@dataclasses.dataclass
class CollisionConfig:
    rigid_contact_max: int | None = None


@dataclasses.dataclass
class SolverOutputs:
    contact_forces: bool = True


@dataclasses.dataclass
class NewtonConfig:
    num_substeps: int = 1
    update_fabric: bool = True
    solver_cfg: NewtonSolverConfig = dataclasses.field(default_factory=NewtonSolverConfig)
    solver_outputs: SolverOutputs = dataclasses.field(default_factory=SolverOutputs)
    collision_cfg: CollisionConfig = dataclasses.field(default_factory=CollisionConfig)


@pytest.fixture
def newton(monkeypatch):
    """A fake isaacsim.physics.newton whose configure_newton records what it was given."""
    state = SimpleNamespace(config=NewtonConfig(), configured=[])
    module = SimpleNamespace(
        NewtonConfig=NewtonConfig, MuJoCoSolverConfig=MuJoCoSolverConfig,
        get_newton_config=lambda: state.config,
        configure_newton=lambda cfg: (state.configured.append(cfg), setattr(state, "config", cfg)),
        acquire_stage=lambda: state.native)
    state.native = None
    for name in ("isaacsim", "isaacsim.physics"):
        monkeypatch.setitem(sys.modules, name, sys.modules.get(name) or SimpleNamespace())
    monkeypatch.setitem(sys.modules, "isaacsim.physics.newton", module)
    monkeypatch.setattr(ns, "engine_name", lambda: "newton")
    return state


def refusal(call, *args, **kwargs):
    with pytest.raises(AssetError) as error:
        call(*args, **kwargs)
    return error.value


class Timeline:
    def __init__(self, stopped=True):
        self.stopped = stopped

    def is_stopped(self):
        return self.stopped

    def is_playing(self):
        return not self.stopped

    def get_current_time(self):
        return 0


def body_stage(count, scenes=1, worlds=0):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World")
    for index in range(scenes):
        UsdPhysics.Scene.Define(stage, "/World/PhysicsScene" + ("" if index == 0 else str(index)))
    for index in range(worlds):
        UsdGeom.Xform.Define(stage, f"/World/envs/env_{index}")
    for index in range(count):
        UsdPhysics.RigidBodyAPI.Apply(UsdGeom.Cube.Define(stage, f"/World/Body{index}").GetPrim())
    return stage, stage.GetPrimAtPath("/World/PhysicsScene")


class FakePrim:
    """A prim that accepts any Newton schema, as Kit's does, and refuses the ones in *missing*."""

    def __init__(self, attributes=None, missing=(), applied=()):
        self.attributes, self.missing, self.applied = dict(attributes or {}), set(missing), set(applied)
        self.values = {}

    def ApplyAPI(self, name):
        if name in self.missing:
            return False
        self.applied.add(name)
        return True

    def HasAPI(self, name):
        return name in self.applied

    def GetAttribute(self, name):
        if name == "newton:solver":
            value = self.attributes.get(name)
            return SimpleNamespace(Get=lambda: value) if value is not None else None
        if name.startswith("mjc:") and "MjcSceneAPI" not in self.applied:
            return None  # an attribute exists only once its schema is applied, as in USD
        if name.startswith("newton:") and not self.applied & {"NewtonSceneAPI", "NewtonCollisionAPI"}:
            return None
        return _Attr(self, name) if name.startswith(("newton:", "mjc:")) else None


class _Attr:
    def __init__(self, owner, name):
        self.owner, self.name = owner, name

    def __bool__(self):
        return True

    def Set(self, value):
        self.owner.values[self.name] = value
        return True


# --- contact buffers ------------------------------------------------------------------------------------------


def test_buffer_sizes_follow_the_body_count_with_headroom():
    assert ns.buffer_sizes(54) == (1728, 13824)  # a 54-block tower: far past Newton's default 200 contacts / 1200 rows
    assert ns.buffer_sizes(3) == (512, 4096)  # a small scene still gets more than the default
    assert ns.buffer_sizes(54, nconmax=900) == (900, 7200)
    assert ns.buffer_sizes(54, nconmax=900, njmax=5000) == (900, 5000)


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, 8_000_001, "100"])
def test_buffer_sizes_refuse_invalid_explicit_values(bad):
    assert refusal(ns.buffer_sizes, 10, nconmax=bad).code == "INVALID_VALUE"
    assert refusal(ns.buffer_sizes, 10, njmax=bad).code == "INVALID_VALUE"


def test_prepare_buffers_sizes_for_a_full_tower_and_copies_the_live_config(newton):
    stage, scene_prim = body_stage(54)
    live = newton.config
    config, report = ns.prepare_buffers(stage, scene_prim, Timeline())
    assert config is not live and live.solver_cfg.solver_type == "None"  # nothing applied yet
    assert isinstance(config.solver_cfg, MuJoCoSolverConfig)
    assert (config.solver_cfg.nconmax, config.solver_cfg.njmax) == (1728, 13824)
    assert config.collision_cfg.rigid_contact_max >= 1728  # Newton's own buffer must hold what MuJoCo-Warp can take
    assert config.solver_outputs.contact_forces is True
    assert (report["rigid_bodies"], report["worlds"], report["nconmax"], report["njmax"]) == (54, 1, 1728, 13824)
    assert report["rigid_contact_max"] == config.collision_cfg.rigid_contact_max and report["warnings"] == []
    json.dumps(report, allow_nan=False)
    ns.apply_buffers(config)
    assert newton.configured == [config]


def test_prepare_buffers_never_shrinks_what_is_configured_but_explicit_values_win(newton):
    newton.config.solver_cfg = MuJoCoSolverConfig(nconmax=5000, njmax=60000)
    stage, scene_prim = body_stage(54)
    config, _ = ns.prepare_buffers(stage, scene_prim, Timeline())
    assert (config.solver_cfg.nconmax, config.solver_cfg.njmax) == (5000, 60000)
    config, report = ns.prepare_buffers(stage, scene_prim, Timeline(), nconmax=100, njmax=900)
    assert (config.solver_cfg.nconmax, config.solver_cfg.njmax) == (100, 900)
    assert {w["code"] for w in report["warnings"]} == {"NEWTON_BUFFER_BELOW_RECOMMENDED"} and len(report["warnings"]) == 2
    assert report["requested"] == dict(nconmax=100, njmax=900)


def test_prepare_buffers_sizes_per_world_and_covers_every_world_in_the_contact_buffer(newton):
    stage, scene_prim = body_stage(64, worlds=4)  # 16 bodies per world
    config, report = ns.prepare_buffers(stage, scene_prim, Timeline())
    assert report["worlds"] == 4 and config.solver_cfg.nconmax == 512  # the 512 floor; 16 * 32 is smaller
    assert config.collision_cfg.rigid_contact_max == 512 * 4  # update_contacts needs nconmax * worlds


def test_prepare_buffers_keeps_a_larger_configured_contact_buffer(newton):
    newton.config.collision_cfg.rigid_contact_max = 100_000
    stage, scene_prim = body_stage(54)
    config, _ = ns.prepare_buffers(stage, scene_prim, Timeline())
    assert config.collision_cfg.rigid_contact_max == 100_000


def test_prepare_buffers_refusals_change_nothing(newton):
    stage, scene_prim = body_stage(5)
    assert refusal(ns.prepare_buffers, stage, scene_prim, Timeline(stopped=False)).code == "TIMELINE_PLAYING"
    assert refusal(ns.prepare_buffers, stage, scene_prim, None).code == "TIMELINE_PLAYING"
    assert refusal(ns.prepare_buffers, stage, None, Timeline()).code == "NOT_FOUND"
    two, first = body_stage(5, scenes=2)
    assert refusal(ns.prepare_buffers, two, first, Timeline()).code == "NEWTON_CONFIG_UNSUPPORTED"
    xpbd = SimpleNamespace(IsValid=lambda: True, GetPath=lambda: scene_prim.GetPath(), GetAttribute=lambda n: None,
                           HasAPI=lambda api: api == "NewtonXpbdSceneAPI")
    assert refusal(ns.prepare_buffers, stage, xpbd, Timeline()).code == "NEWTON_CONFIG_UNSUPPORTED"
    newton.config.solver_cfg = XPBDSolverConfig()
    assert refusal(ns.prepare_buffers, stage, scene_prim, Timeline()).code == "NEWTON_CONFIG_UNSUPPORTED"
    assert newton.configured == [] and isinstance(newton.config.solver_cfg, XPBDSolverConfig)


# --- scene and collision settings -----------------------------------------------------------------------------


def test_scene_settings_write_both_timesteps_from_one_rate():
    scene_prim = FakePrim()
    values = ns.scene_settings(scene_prim, 250)
    assert values == {"newton:timeStepsPerSecond": 250, "mjc:option:timestep": pytest.approx(0.004)}
    assert scene_prim.applied == {"NewtonSceneAPI", "MjcSceneAPI"}


def test_scene_settings_do_not_add_a_second_solver_api_to_another_solver():
    scene_prim = FakePrim(attributes={"newton:solver": "xpbd"})
    assert ns.scene_settings(scene_prim, 120) == {"newton:timeStepsPerSecond": 120}
    assert "MjcSceneAPI" not in scene_prim.applied  # Newton refuses a scene with two solver APIs


def test_scene_settings_for_a_tower_add_the_stack_settings_and_report_missing_schemas():
    values = ns.scene_settings(FakePrim(), 120, tower=True)
    assert values["mjc:option:cone"] == "elliptic" and values["newton:maxSolverIterations"] == 100
    assert refusal(ns.scene_settings, FakePrim(missing=["NewtonSceneAPI"]), 120).code == "PHYSICS_API_UNAVAILABLE"
    assert refusal(ns.scene_settings, FakePrim(missing=["MjcSceneAPI"]), 120).code == "PHYSICS_API_UNAVAILABLE"


def test_collision_settings_are_the_newton_form_of_the_physx_offsets():
    prim = FakePrim()
    assert ns.collision_settings(prim) == {"newton:contactMargin": 0.0, "newton:contactGap": 0.004}
    assert refusal(ns.collision_settings, FakePrim(missing=["NewtonCollisionAPI"])).code == "PHYSICS_API_UNAVAILABLE"


# --- the tower ----------------------------------------------------------------------------------------------------


def _jenga_stage():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World")
    UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    return stage


def test_the_tower_on_physx_authors_no_newton_settings(monkeypatch):
    monkeypatch.setattr(ns, "collision_settings", lambda *a: pytest.fail("PhysX must not author Newton settings"))
    report = jenga.build(_jenga_stage())
    assert not any(key.startswith("newton") for key in report) and "live tower check" not in report["note"] and report["physics_hz"] == 120


def test_the_tower_on_newton_keeps_the_physx_settings_and_adds_buffers_and_collider_settings(newton, monkeypatch):
    colliders, scenes = [], []
    monkeypatch.setattr(ns, "collision_settings", lambda prim: colliders.append(str(prim.GetPath())))
    monkeypatch.setattr(ns, "scene_settings", lambda prim, rate, tower=False: scenes.append((rate, tower)) or {"rate": rate})
    stage = _jenga_stage()
    report = jenga.build(stage, backend="newton", timeline=Timeline())
    assert len(colliders) == 55 and colliders[0].endswith("/Table")  # the table and every block
    assert scenes == [(jenga.NEWTON_HZ, True)] and report["newton_settings"] == {"rate": 500} and report["physics_hz"] == 500
    buffers = report["newton_buffers"]
    assert (buffers["rigid_bodies"], buffers["nconmax"], buffers["njmax"]) == (54, 1728, 13824)
    assert newton.configured and newton.configured[0].solver_cfg.nconmax == 1728
    assert "live tower check" in report["note"] and report["blocks"] == 54
    bodies = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI)]
    assert len(bodies) == 54


def test_the_tower_on_newton_reports_a_setting_it_cannot_author_and_still_builds(newton):
    stage = _jenga_stage()  # no Newton schemas registered in plain USD: ApplyAPI fails
    report = jenga.build(stage, backend="newton", timeline=Timeline())
    assert report["newton_error"]["code"] == "PHYSICS_API_UNAVAILABLE" and "newton_buffers" not in report
    assert len([p for p in stage.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI)]) == 54 and newton.configured == []


def test_the_tower_on_newton_without_a_physics_scene_says_so():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World")
    report = jenga.build(stage, backend="newton", timeline=Timeline())
    assert report["newton_error"]["code"] in ("NOT_FOUND", "PHYSICS_API_UNAVAILABLE")


def test_quick_scene_aligns_the_timesteps_and_sizes_the_buffers(newton, monkeypatch):
    stage, scene_prim = body_stage(12)
    monkeypatch.setattr(ns, "scene_settings", lambda prim, rate, tower=False: {"newton:timeStepsPerSecond": rate})
    report = ns.quick_scene(stage, scene_prim, Timeline())
    assert report["rate_hz"] == 500 and report["settings"] == {"newton:timeStepsPerSecond": 500}
    assert report["buffers"]["nconmax"] == 512 and newton.configured
    monkeypatch.setattr(ns, "scene_settings", lambda *a, **k: pytest.fail("the tower already authored its rate"))
    assert ns.quick_scene(stage, scene_prim, Timeline(), authored_rate=120)["rate_hz"] == 120


def test_quick_scene_authors_into_the_session_layer_only(newton, monkeypatch):
    stage, scene_prim = body_stage(12)
    targets = []
    monkeypatch.setattr(ns, "scene_settings", lambda prim, rate, tower=False: targets.append(stage.GetEditTarget().GetLayer()) or {})
    ns.quick_scene(stage, scene_prim, Timeline())
    assert targets == [stage.GetSessionLayer()] and stage.GetEditTarget().GetLayer() == stage.GetRootLayer()


def test_quick_scene_reports_instead_of_raising(newton):
    stage, scene_prim = body_stage(12)
    report = ns.quick_scene(stage, scene_prim, Timeline(stopped=False), authored_rate=120)
    assert report["error"]["code"] == "TIMELINE_PLAYING" and newton.configured == []


# --- diagnostics -----------------------------------------------------------------------------------------------------


def solver_data(nacon=100, naconmax=1728, nefc=(300,), njmax=13824, overflow=(0,)):
    return SimpleNamespace(nacon=np.array([nacon]), naconmax=naconmax, nefc=np.array(nefc), njmax=njmax,
                           overflow=np.array(overflow))


def fake_native(data=None, **extra):
    native = SimpleNamespace(
        initialized=True, sim_dt=0.002, sim_time=0.0, simulation_step_count=0,
        cfg=SimpleNamespace(num_substeps=1, solver_cfg=SimpleNamespace(solver_type="mujoco"),
                            solver_outputs=SimpleNamespace(contact_forces=True)),
        solver=SimpleNamespace(use_mujoco_cpu=False, mjw_data=data))
    native.__dict__.update(extra)
    return native


def use_native(monkeypatch, native):
    monkeypatch.setattr(physics, "newton_stage", lambda stage: native)


def test_diagnostics_report_configured_buffers_and_healthy_counts(newton, monkeypatch):
    newton.config.solver_cfg = MuJoCoSolverConfig(nconmax=1728, njmax=13824)
    newton.config.collision_cfg.rigid_contact_max = 1728
    use_native(monkeypatch, fake_native(solver_data()))
    result = ns.diagnostics(None)
    assert result["configured"] == dict(nconmax=1728, njmax=13824, rigid_contact_max=1728)
    assert (result["active_contacts"], result["contact_capacity"]) == ([100], 1728)
    assert (result["active_constraints"], result["constraint_capacity"]) == ([300], 13824)
    assert result["warnings"] == [] and result["overflow_history_available"] is True
    assert result["overflow_flags"] == [dict(raw=0, names=[], unknown_bits=0)]
    json.dumps(result, allow_nan=False)


def test_diagnostics_warn_when_the_contact_buffer_is_full(newton, monkeypatch):
    use_native(monkeypatch, fake_native(solver_data(nacon=1728)))
    result = ns.diagnostics(None)
    assert [w["code"] for w in result["warnings"]] == ["CONTACT_BUFFER_FULL"]


def test_diagnostics_warn_when_constraints_fill_in_any_world(newton, monkeypatch):
    use_native(monkeypatch, fake_native(solver_data(nefc=(10, 13824))))
    assert [w["code"] for w in ns.diagnostics(None)["warnings"]] == ["CONSTRAINT_BUFFER_FULL"]


def test_diagnostics_warn_on_a_sticky_overflow_bit_even_when_the_counts_look_fine(newton, monkeypatch):
    use_native(monkeypatch, fake_native(solver_data(overflow=(1 << 3,))))  # NARROWPHASE
    result = ns.diagnostics(None)
    assert [w["code"] for w in result["warnings"]] == ["NEWTON_SOLVER_OVERFLOW"]
    assert result["overflow_flags"][0]["names"] == ["NARROWPHASE"]


def test_diagnostics_separate_convergence_limits_from_buffer_overflow(newton, monkeypatch):
    use_native(monkeypatch, fake_native(solver_data(overflow=((1 << 9) | (1 << 10),))))
    result = ns.diagnostics(None)
    assert [w["code"] for w in result["warnings"]] == ["NEWTON_SOLVER_NOT_CONVERGED"]
    assert result["overflow_flags"][0]["names"] == ["ITERATIONS", "LS_ITERATIONS"]


def test_diagnostics_without_counters_say_so_and_do_not_claim_safety(newton, monkeypatch):
    use_native(monkeypatch, fake_native(None))
    result = ns.diagnostics(None)
    assert result["active_contacts"] is None and result["warnings"] == [] and "unavailable" in result["runtime_status"]


def test_diagnostics_before_physics_is_initialized_still_report_the_configuration(newton, monkeypatch):
    def stale(stage):
        raise AssetError("PHYSICS_NOT_READY", "stale")

    monkeypatch.setattr(physics, "newton_stage", stale)
    result = ns.diagnostics(None)
    assert result["runtime_status"] == "PHYSICS_NOT_READY" and result["configured"]["nconmax"] is None


def test_diagnostics_never_raise(newton, monkeypatch):
    monkeypatch.setattr(physics, "newton_stage", lambda stage: (_ for _ in ()).throw(RuntimeError("boom")))
    result = ns.diagnostics(None)
    assert result["runtime_status"] == "DIAGNOSTICS_FAILED" and "boom" in result["runtime_message"]
    use_native(monkeypatch, fake_native(SimpleNamespace(nacon=np.array([-1]), naconmax=10, nefc=np.array([0]), njmax=10)))
    assert ns.diagnostics(None)["runtime_status"] == "PHYSICS_STATE_INVALID"


# --- contacts -----------------------------------------------------------------------------------------------------------


def contact_world(rows, *, forces=None):
    """A stage with a block on a table, and a native whose snapshot holds *rows* of (shape0, shape1)."""
    stage = Usd.Stage.CreateInMemory()
    for path, body in (("/World/Block", True), ("/World/Table", False), ("/World/Other", True)):
        prim = UsdGeom.Cube.Define(stage, path).GetPrim()
        UsdPhysics.CollisionAPI.Apply(prim)
        if body:
            UsdPhysics.RigidBodyAPI.Apply(prim)
    n = len(rows)
    force = np.zeros((8, 6)) if forces is None else forces
    contacts = SimpleNamespace(
        rigid_contact_count=np.array([n]), rigid_contact_shape0=np.array([r[0] for r in rows] + [0] * (8 - n)),
        rigid_contact_shape1=np.array([r[1] for r in rows] + [0] * (8 - n)), force=force)
    model = SimpleNamespace(shape_body=np.array([0, -1, 1]), shape_label=["/World/Block", "/World/Table", "/World/Other"],
                            body_label=["/World/Block", "/World/Other"])
    return stage, fake_native(solver_data(nacon=n), model=model, contacts=contacts)


def test_contacts_group_points_by_pair_and_sum_forces(newton, monkeypatch):
    forces = np.zeros((8, 6))
    forces[0, :3], forces[1, :3], forces[2, :3] = (0, 0, 1.5), (0, 0, 2.5), (9, 9, 9)
    stage, native = contact_world([(0, 1), (0, 1), (2, 1)], forces=forces)
    use_native(monkeypatch, native)
    result = ns.contacts(["/World/Block"], stage)
    assert len(result["pairs"]) == 1  # the other body's contact is not asked for
    pair = result["pairs"][0]
    assert (pair["body_a"], pair["body_b"]) == ("/World/Block", None)  # the table is static: no body
    assert (pair["collider_a"], pair["collider_b"], pair["contact_points"]) == ("/World/Block", "/World/Table", 2)
    assert pair["force"] == pytest.approx([0, 0, 4.0]) and pair["force_magnitude"] == pytest.approx(4.0)
    assert pair["impulse"] is None and pair["impulse_magnitude"] is None  # a snapshot has no impulse
    assert result["backend"] == "newton" and "Newton" in result["source"] and result["truncated"] is False
    assert result["newton"]["configured"] is not None
    json.dumps(result, allow_nan=False)


def test_contacts_have_the_physx_result_shape(newton, monkeypatch):
    stage, native = contact_world([(0, 1)])
    use_native(monkeypatch, native)
    result = ns.contacts(["/World/Block"], stage)
    physx_keys = {"paths", "pairs", "truncated", "reporting_paths", "paths_without_reporting", "reports_seen", "backend", "units",
                  "source", "note"}
    assert physx_keys <= set(result)
    assert {"body_a", "body_b", "collider_a", "collider_b", "contact_points", "impulse", "impulse_magnitude"} <= set(result["pairs"][0])


def test_contacts_with_no_touching_pair_is_an_empty_report_not_a_refusal(newton, monkeypatch):
    stage, native = contact_world([])
    use_native(monkeypatch, native)
    assert ns.contacts(["/World/Block"], stage)["pairs"] == []


def test_contacts_truncate_at_the_pair_limit(newton, monkeypatch):
    monkeypatch.setattr(queries, "MAX_HITS", 1)
    stage, native = contact_world([(0, 1), (2, 1), (0, 2)])
    use_native(monkeypatch, native)
    result = ns.contacts(["/World/Block", "/World/Other"], stage)
    assert len(result["pairs"]) == 1 and result["truncated"] is True


@pytest.mark.parametrize("change", ["no_forces", "cpu", "other_solver", "no_contacts", "no_shapes"])
def test_contacts_refuse_exactly_when_newton_cannot_provide_them(newton, monkeypatch, change):
    stage, native = contact_world([(0, 1)])
    if change == "no_forces":
        native.cfg.solver_outputs.contact_forces = False
    elif change == "cpu":
        native.solver.use_mujoco_cpu = True
    elif change == "other_solver":
        native.cfg.solver_cfg.solver_type = "xpbd"
    elif change == "no_contacts":
        native.contacts = None
    else:
        native.contacts.rigid_contact_shape1 = None
    use_native(monkeypatch, native)
    error = refusal(ns.contacts, ["/World/Block"], stage)
    assert error.code == "CONTACT_QUERY_UNSUPPORTED"


def test_contacts_refuse_nonfinite_or_overlong_snapshots(newton, monkeypatch):
    bad = np.zeros((8, 6))
    bad[0, 2] = np.nan
    stage, native = contact_world([(0, 1)], forces=bad)
    use_native(monkeypatch, native)
    assert refusal(ns.contacts, ["/World/Block"], stage).code == "PHYSICS_STATE_INVALID"
    stage, native = contact_world([(0, 1)])
    native.contacts.rigid_contact_count = np.array([20])  # more contacts than the arrays hold
    use_native(monkeypatch, native)
    assert refusal(ns.contacts, ["/World/Block"], stage).code == "PHYSICS_STATE_INVALID"


def test_contacts_validate_the_paths_before_reading_anything(newton, monkeypatch):
    stage, native = contact_world([(0, 1)])
    use_native(monkeypatch, native)
    assert refusal(ns.contacts, ["/World/Nope"], stage).code == "NOT_FOUND"
    UsdGeom.Xform.Define(stage, "/World/Plain")
    assert refusal(ns.contacts, ["/World/Plain"], stage).code == "WRONG_TYPE"


def test_the_contacts_query_routes_newton_to_the_snapshot_and_still_needs_a_started_timeline(newton, monkeypatch):
    monkeypatch.setattr(queries, "_engine", lambda: SimpleNamespace(get_active_physics_engine=lambda: "newton"))
    stage, native = contact_world([(0, 1)])
    use_native(monkeypatch, native)
    assert refusal(queries.contacts, ["/World/Block"], stage, Timeline()).code == "PHYSICS_NOT_READY"
    assert queries.contacts(["/World/Block"], stage, Timeline(stopped=False))["backend"] == "newton"


def test_raycast_under_newton_is_refused_with_the_reason_and_the_alternatives(monkeypatch):
    monkeypatch.setattr(queries, "_engine", lambda: SimpleNamespace(get_active_physics_engine=lambda: "newton"))
    error = refusal(queries.raycast, [0, 0, 3], [0, 0, -1])
    assert error.code == "RAYCAST_UNSUPPORTED" and "stub" in str(error) and "contacts" in str(error)
    assert error.details["backend"] == "newton" and "a PhysX session" in error.details["alternatives"]


# --- the timestep cross-check, stepping and the push ----------------------------------------------------------------------


def manager(dt=0.002, engine="newton", clock=None, step=None):
    return SimpleNamespace(get_physics_dt=lambda: dt, get_active_physics_engine=lambda: engine,
                           get_simulation_time=clock or (lambda: 0.0), step=step)


def test_dt_sources_accept_matching_timesteps_and_report_the_substeps():
    native = fake_native(None)
    native.cfg.num_substeps = 4
    assert ns.dt_sources(manager(0.002), native) == dict(scene_dt=0.002, newton_sim_dt=0.002, newton_substeps=4)


def test_dt_sources_refuse_the_mismatch_behind_the_sim_6_2_step_timing_report():
    """mjc:option:timestep defaults to 0.002 s while newton:timeStepsPerSecond defaults to 1000 Hz: 20 steps of 0.002 s
    read as 20 x 0.001 s = 0.020 s on a clock that follows Newton's rate, against 0.040 s expected."""
    native = fake_native(None)
    native.sim_dt = 0.001
    error = refusal(ns.dt_sources, manager(0.002), native)
    assert error.code == "STEP_DT_SOURCES_DISAGREE" and "Nothing was stepped" in str(error)
    assert error.details["dt_sources"] == dict(scene_dt=0.002, newton_sim_dt=0.001, newton_substeps=1)
    assert "sim__physics action=configure" in str(error)


def step_world(scene, monkeypatch, native, dt=0.002, advance=None):
    scene.timeline.is_stopped = lambda: False
    scene.timeline.is_playing = lambda: False
    monkeypatch.setattr(scene, "check_runtime_edit", lambda stage: None)
    state = dict(time=1.0, steps=[])
    sm = manager(dt, clock=lambda: state["time"])
    monkeypatch.setattr(advanced_module, "physics_manager", lambda: sm)

    def step(stage, count):
        state["steps"].append(count)
        state["time"] += (advance if advance is not None else count * dt)
        if native is not None:
            native.sim_time += count * dt
            native.simulation_step_count += count

    monkeypatch.setattr(physics, "step", step)
    monkeypatch.setattr(physics, "sync_fabric", lambda stage: True)
    monkeypatch.setattr(physics, "newton_stage", lambda stage: native)
    return state


def test_a_newton_step_checks_the_timesteps_first_and_reports_both_clocks(scene, monkeypatch):
    native = fake_native(None)
    state = step_world(scene, monkeypatch, native)
    result = Advanced(scene, lambda *a: None).execute("sim.simulation.step", scene.stage(), dict(steps=20))
    assert state["steps"] == [20] and result["measured_delta"] == pytest.approx(0.04)
    assert result["newton"]["timestep_check"] == dict(ran=True, scene_dt=0.002, newton_sim_dt=0.002, newton_substeps=1)
    assert result["newton"]["clocks_before"] == dict(newton_sim_time=0.0, newton_step_count=0)
    assert result["newton"]["clocks_after"] == dict(newton_sim_time=pytest.approx(0.04), newton_step_count=20)
    json.dumps(result, allow_nan=False)


def test_a_newton_step_with_disagreeing_timesteps_refuses_before_stepping(scene, monkeypatch):
    native = fake_native(None)
    native.sim_dt = 0.001
    state = step_world(scene, monkeypatch, native)
    error = refusal(Advanced(scene, lambda *a: None).execute, "sim.simulation.step", scene.stage(), dict(steps=20))
    assert error.code == "STEP_DT_SOURCES_DISAGREE" and state["steps"] == []
    assert "step" not in error.details  # nothing was attempted


def test_a_timing_mismatch_carries_newtons_own_clocks_for_the_live_diagnosis(scene, monkeypatch):
    native = fake_native(None)
    state = step_world(scene, monkeypatch, native, advance=0.02)  # the manager's clock moved by half the expected time
    error = refusal(Advanced(scene, lambda *a: None).execute, "sim.simulation.step", scene.stage(), dict(steps=20))
    assert error.code == "STEP_TIMING_MISMATCH" and state["steps"] == [20]
    clocks = error.details["step"]["newton"]
    assert clocks["clocks_after"]["newton_sim_time"] == pytest.approx(0.04) and clocks["clocks_after"]["newton_step_count"] == 20
    assert error.details["step"]["measured_delta"] == pytest.approx(0.02)


def test_a_newton_step_says_when_the_timestep_check_could_not_run(scene, monkeypatch):
    state = step_world(scene, monkeypatch, None)

    def not_ready(stage):
        raise AssetError("PHYSICS_NOT_READY", "Newton's initialized stage is stale.")

    monkeypatch.setattr(physics, "newton_stage", not_ready)
    result = Advanced(scene, lambda *a: None).execute("sim.simulation.step", scene.stage(), dict(steps=2))
    assert result["newton"]["timestep_check"]["ran"] is False and "stale" in result["newton"]["timestep_check"]["reason"]
    assert state["steps"] == [2]


class View:
    """A rigid-body tensor view that records the wrench it is given."""

    def __init__(self):
        self.calls = []

    def get_transforms(self):
        return np.zeros((1, 7), dtype=np.float32)

    def get_masses(self):
        return np.array([[0.12]], dtype=np.float32)

    def apply_forces_and_torques_at_position(self, force, torque, position, indices, is_global):
        self.calls.append(dict(force=np.asarray(force).tolist(), torque=np.asarray(torque).tolist(),
                               position=None if position is None else np.asarray(position).tolist(),
                               indices=np.asarray(indices).tolist(), is_global=is_global))


def push_world(scene, monkeypatch, *, sim_dt=0.002, substeps=1, scene_dt=0.002, engine="newton"):
    view = View()
    native = fake_native(None)
    native.sim_dt, native.cfg.num_substeps = sim_dt, substeps
    sm = manager(scene_dt, engine=engine)
    scene.timeline.is_stopped = lambda: False
    UsdPhysics.RigidBodyAPI.Apply(UsdGeom.Cube.Define(scene.stage(), "/World/Block").GetPrim())
    monkeypatch.setattr(advanced_module, "physics_manager", lambda: sm)
    monkeypatch.setattr(physics, "manager", lambda: sm)
    monkeypatch.setattr(physics, "view_for", lambda path, articulation=False: view)
    monkeypatch.setattr(physics, "newton_stage", lambda stage: native)
    return view


def test_a_newton_push_delivers_the_impulse_as_a_force_over_one_substep(scene, monkeypatch):
    view = push_world(scene, monkeypatch, substeps=2)
    result = Advanced(scene, lambda *a: None).execute(
        "sim.physics.apply", scene.stage(), dict(path="/World/Block", impulse=[0.0, 0.0, 0.12]))
    assert result["backend"] == "newton" and result["applied"] is True and result["force_duration_s"] == pytest.approx(0.001)
    assert result["applied_force_n"] == pytest.approx([0, 0, 120.0])  # 0.12 N s over 1 ms
    assert len(view.calls) == 1
    call = view.calls[0]
    assert call["force"][0] == pytest.approx([0, 0, 120.0]) and call["position"] is None and call["is_global"] is True
    json.dumps(result, allow_nan=False)


def test_a_newton_push_at_a_world_position_passes_it_to_the_view(scene, monkeypatch):
    view = push_world(scene, monkeypatch)
    Advanced(scene, lambda *a: None).execute(
        "sim.physics.apply", scene.stage(), dict(path="/World/Block", impulse=[1.0, 0.0, 0.0], at=[0.0, 0.0, 0.5]))
    assert view.calls[0]["position"][0] == pytest.approx([0.0, 0.0, 0.5]) and view.calls[0]["is_global"] is True


def test_a_newton_push_with_disagreeing_timesteps_applies_nothing(scene, monkeypatch):
    view = push_world(scene, monkeypatch, sim_dt=0.001, scene_dt=0.002)
    error = refusal(Advanced(scene, lambda *a: None).execute, "sim.physics.apply", scene.stage(),
                    dict(path="/World/Block", impulse=[0.0, 0.0, 1.0]))
    assert error.code == "STEP_DT_SOURCES_DISAGREE" and view.calls == []


def test_a_zero_newton_push_is_reported_as_not_applied_not_as_a_success(scene, monkeypatch):
    view = push_world(scene, monkeypatch)
    result = Advanced(scene, lambda *a: None).execute(
        "sim.physics.apply", scene.stage(), dict(path="/World/Block", impulse=[0.0, 0.0, 0.0]))
    assert result["applied"] is False and view.calls == []


def test_a_newton_push_needs_a_running_timeline_and_a_body(scene, monkeypatch):
    push_world(scene, monkeypatch)
    scene.timeline.is_stopped = lambda: True
    advanced = Advanced(scene, lambda *a: None)
    assert refusal(advanced.execute, "sim.physics.apply", scene.stage(),
                   dict(path="/World/Block", impulse=[0, 0, 1])).code == "PHYSICS_NOT_READY"
    scene.timeline.is_stopped = lambda: False
    UsdGeom.Xform.Define(scene.stage(), "/World/Plain")
    assert refusal(advanced.execute, "sim.physics.apply", scene.stage(),
                   dict(path="/World/Plain", impulse=[0, 0, 1])).code == "INVALID_PHYSICS"


def test_body_io_applies_at_a_world_position_only_in_the_world_frame(monkeypatch):
    view = View()
    monkeypatch.setattr(physics, "manager", lambda: SimpleNamespace(get_active_physics_engine=lambda: "newton"))
    monkeypatch.setattr(physics, "view_for", lambda path, articulation=False: view)
    body = control_run.BodyIO("/World/Block")
    body.apply([1.0, 0, 0], [0, 0, 0], "world", at=[0.1, 0.2, 0.3])
    assert view.calls[0]["position"][0] == pytest.approx([0.1, 0.2, 0.3])
    assert refusal(body.apply, [1.0, 0, 0], [0, 0, 0], "body", at=[0, 0, 0]).code == "INVALID_VALUE"


# --- sensors and pictures, the map ---------------------------------------------------------------------------------------


def test_render_publication_is_nothing_on_physx_and_explicit_on_newton(newton, monkeypatch):
    monkeypatch.setattr(ns, "engine_name", lambda: "physx")
    assert physics.render_publication() is None
    monkeypatch.setattr(ns, "engine_name", lambda: "newton")
    newton.native = SimpleNamespace(cfg=SimpleNamespace(update_fabric=True))
    info = physics.render_publication()
    assert info["fabric_updates"] is True and "warning" not in info and "Fabric" in info["poses_published_to"]
    newton.native = SimpleNamespace(cfg=SimpleNamespace(update_fabric=False))
    assert "authored poses" in physics.render_publication()["warning"]
    newton.native = None
    assert physics.render_publication()["fabric_updates"] is None


def test_a_capture_carries_the_render_report_only_on_newton(newton, monkeypatch):
    from sim_tools import sensor_tools

    monkeypatch.setattr(ns, "engine_name", lambda: "physx")
    assert sensor_tools._render_report() == {}
    monkeypatch.setattr(ns, "engine_name", lambda: "newton")
    newton.native = SimpleNamespace(cfg=SimpleNamespace(update_fabric=False))
    report = sensor_tools._render_report()
    assert report["physics_render"]["fabric_updates"] is False and report["physics_render"]["warning"]


def test_the_occupancy_map_skips_the_physx_overlap_checks_under_newton_and_says_so(monkeypatch, tmp_path):
    from test_scene_lidar_save import _occupancy_world

    graph, scene, stage = _occupancy_world(monkeypatch, tmp_path, ["/World/Wall"])
    monkeypatch.setattr(ns, "engine_name", lambda: "newton")
    monkeypatch.setattr(graph, "_start_in_collider", lambda *a: pytest.fail("a PhysX query must not run under Newton"))
    monkeypatch.setattr(graph, "_colliders_in_bounds", lambda *a, **k: pytest.fail("a PhysX query must not run under Newton"))
    result = graph.occupancy_map(scene, stage, {})
    assert result["backend"] == "newton" and len(result["checks_skipped"]) == 3 and "isaacsim.asset.gen.omap" in result["checks_note"]
    assert (tmp_path / "m.pgm").exists()


def test_the_occupancy_map_on_physx_still_runs_its_checks_and_claims_none_are_skipped(monkeypatch, tmp_path):
    from test_scene_lidar_save import _occupancy_world

    graph, scene, stage = _occupancy_world(monkeypatch, tmp_path, [])
    monkeypatch.setattr(ns, "engine_name", lambda: "physx")
    assert "checks_skipped" not in graph.occupancy_map(scene, stage, {})

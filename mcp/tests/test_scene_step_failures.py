"""Observed invalid solver state is not a bad user pose or a successful step."""

from types import SimpleNamespace
import json

import pytest

from test_scene_usd import scene
from twinsmith.project_assets import AssetError, refuse
from sim_tools import advanced as advanced_module, physics
from sim_tools.advanced import Advanced


@pytest.mark.parametrize("backend", ["physx", "newton"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 1e9])
def test_runtime_vector_identifies_observation_not_input(backend, bad):
    with pytest.raises(AssetError) as error:
        physics.runtime_vector(
            [0, bad, 0], 3, path="/World/Body", field="world_position", backend=backend
        )
    assert error.value.code == "PHYSICS_STATE_INVALID"
    assert error.value.details["path"] == "/World/Body"
    assert error.value.details["field"] == "world_position"
    assert error.value.details["backend"] == backend
    json.dumps(error.value.details, allow_nan=False)


@pytest.mark.parametrize("field", ["joint_positions", "joint_velocities"])
def test_articulation_nonfinite_data_is_runtime_failure(scene, monkeypatch, field):
    import numpy as np
    from pxr import UsdGeom, UsdPhysics

    stage = scene.stage()
    prim = UsdGeom.Xform.Define(stage, "/World/Robot").GetPrim()
    UsdPhysics.ArticulationRootAPI.Apply(prim)
    scene.timeline.is_stopped = lambda: False
    values = {
        "joint_positions": np.array([[0.1]]),
        "joint_velocities": np.array([[0.0]]),
    }
    values[field][0, 0] = np.nan
    view = SimpleNamespace(
        shared_metatype=SimpleNamespace(dof_names=["joint"]),
        get_dof_positions=lambda: values["joint_positions"],
        get_dof_velocities=lambda: values["joint_velocities"],
    )
    monkeypatch.setattr(physics, "view_for", lambda *a, **kw: view)
    monkeypatch.setattr(
        advanced_module,
        "physics_manager",
        lambda: SimpleNamespace(get_active_physics_engine=lambda: "newton"),
    )
    with pytest.raises(AssetError) as error:
        Advanced(scene, lambda *a: None).joints(stage, "/World/Robot")
    assert error.value.code == "PHYSICS_STATE_INVALID"
    assert error.value.details["field"] == field


@pytest.mark.parametrize(
    "failure,code,phase",
    [
        ("body", "PHYSICS_STATE_INVALID", "body_observation"),
        ("joints", "PHYSICS_STATE_INVALID", "joint_observation"),
        ("timing", "STEP_TIMING_MISMATCH", "clock_observation"),
        ("native", "STEP_FAILED", "native_step"),
        ("cuda", "DEVICE_ERROR", "native_step"),
        ("clock", "STEP_FAILED", "clock_observation"),
        ("nonfinite_clock", "STEP_TIMING_MISMATCH", "clock_observation"),
    ],
)
def test_partial_step_reports_observed_clock_without_retry(
    scene, monkeypatch, failure, code, phase
):
    scene.timeline.is_stopped = lambda: False
    monkeypatch.setattr(scene, "check_runtime_edit", lambda stage: None)
    state = {"time": 3.0, "steps": [], "body_reads": 0, "joint_reads": 0}

    def clock():
        if state["steps"] and failure == "clock":
            raise RuntimeError("clock unavailable")
        return state["time"]

    manager = SimpleNamespace(
        get_simulation_time=clock,
        get_physics_dt=lambda: 1 / 120,
        get_active_physics_engine=lambda: "newton",
    )

    def step(stage, count):
        state["steps"].append(count)
        state["time"] += (3 if failure == "timing" else count) / 120
        if failure == "native":
            raise RuntimeError("solver failed after advancing its clock")
        if failure == "cuda":
            raise RuntimeError("CUDA error 700: illegal memory access")
        if failure == "nonfinite_clock":
            state["time"] = float("nan")

    def body(name, args):
        state["body_reads"] += 1
        if state["steps"] and failure == "body":
            refuse(
                "PHYSICS_STATE_INVALID",
                "nonfinite body",
                path=args["path"],
                field="orientation_xyzw",
            )
        return {"path": args["path"], "world_position": [0, 0, 1]}

    def joints(stage, path):
        state["joint_reads"] += 1
        if state["steps"] and failure == "joints":
            refuse(
                "PHYSICS_STATE_INVALID",
                "nonfinite joints",
                path=path,
                field="joint_positions",
            )
        return {"path": path, "positions": [0.1]}

    monkeypatch.setattr(advanced_module, "physics_manager", lambda: manager)
    monkeypatch.setattr(physics, "step", step)
    monkeypatch.setattr(physics, "sync_fabric", lambda stage: True)
    a = Advanced(scene, body)
    monkeypatch.setattr(a, "joints", joints)
    with pytest.raises(AssetError) as error:
        a.execute(
            "sim.simulation.step",
            scene.stage(),
            dict(
                steps=12, observe_prims=["/World/Body"], observe_joints=["/World/Robot"]
            ),
        )
    exc = error.value
    assert exc.code == code
    observation = exc.details["step"]
    assert observation["phase"] == phase
    assert observation["requested_steps"] == 12
    assert observation["physics_time_before"] == 3
    assert observation["expected_delta"] == pytest.approx(0.1)
    assert observation["rendered"] is False
    assert state["steps"] == [12]  # no automatic retry or reset
    if failure in ("clock", "nonfinite_clock"):
        assert observation["physics_time_after"] is None
        assert observation["measured_delta"] is None
    else:
        assert observation["measured_delta"] == pytest.approx(
            0.025 if failure == "timing" else 0.1
        )
    if failure in ("body", "joints"):
        assert exc.details["path"].startswith("/World/")
        assert observation["fabric_updated"] is True
    if failure == "cuda":
        assert "700" in scene.device_error
    json.dumps(exc.details, allow_nan=False)


def test_invalid_state_preflight_does_not_step(scene, monkeypatch):
    scene.timeline.is_stopped = lambda: False
    monkeypatch.setattr(scene, "check_runtime_edit", lambda stage: None)
    monkeypatch.setattr(physics, "step", lambda *a: pytest.fail("must not step"))

    def invalid(*a):
        refuse("PHYSICS_STATE_INVALID", "preexisting invalid state")

    with pytest.raises(AssetError) as error:
        Advanced(scene, invalid).execute(
            "sim.simulation.step",
            scene.stage(),
            dict(steps=1, observe_prims=["/World/Body"]),
        )
    assert error.value.code == "PHYSICS_STATE_INVALID"
    assert "step" not in error.value.details


@pytest.mark.parametrize(
    "before,dt",
    [(float("inf"), 0.01), (1, float("nan")), (None, 0.01), (1, 0), (1, True)],
)
def test_invalid_clock_does_not_request_steps(scene, monkeypatch, before, dt):
    scene.timeline.is_stopped = lambda: False
    monkeypatch.setattr(scene, "check_runtime_edit", lambda stage: None)
    monkeypatch.setattr(physics, "step", lambda *a: pytest.fail("must not step"))
    monkeypatch.setattr(
        advanced_module,
        "physics_manager",
        lambda: SimpleNamespace(
            get_simulation_time=lambda: before, get_physics_dt=lambda: dt
        ),
    )
    with pytest.raises(AssetError) as error:
        Advanced(scene, lambda *a: None).execute(
            "sim.simulation.step", scene.stage(), dict(steps=1)
        )
    assert error.value.code == "PHYSICS_CLOCK_INVALID"
    assert "step" not in error.value.details


def test_successful_step_keeps_measured_result(scene, monkeypatch):
    scene.timeline.is_stopped = lambda: False
    monkeypatch.setattr(scene, "check_runtime_edit", lambda stage: None)
    state = [1.0]
    manager = SimpleNamespace(
        get_simulation_time=lambda: state[0],
        get_physics_dt=lambda: 0.01,
        get_active_physics_engine=lambda: "physx",
    )
    monkeypatch.setattr(advanced_module, "physics_manager", lambda: manager)
    monkeypatch.setattr(
        physics,
        "step",
        lambda stage, steps: state.__setitem__(0, state[0] + steps * 0.01),
    )
    monkeypatch.setattr(physics, "sync_fabric", lambda stage: False)
    out = Advanced(scene, lambda *a: None).execute(
        "sim.simulation.step", scene.stage(), dict(steps=10)
    )
    assert out["measured_delta"] == pytest.approx(0.1)
    assert out["backend"] == "physx" and out["rendered"] is False
    assert out["bodies"] == [] and out["articulations"] == []

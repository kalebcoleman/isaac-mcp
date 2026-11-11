"""sim.simulation.run: the caller's controller in the physics loop, as a job."""

import asyncio
import csv
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr.Usd")
np = pytest.importorskip("numpy")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from isaac_mcp import controller_server
from isaac_mcp.project_assets import AssetError
from isaac_mcp_bridge import jobs
from sim_tools import control_run, physics

DT = 0.01
GRAVITY = 9.81


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Body:
    """A point mass with gravity, behind the methods of a rigid-body tensor view."""

    def __init__(self, mass=2.0):
        self.mass = mass
        self.position = np.zeros(3)
        self.velocity = np.zeros(3)
        self.wrench = (np.zeros(3), np.zeros(3))
        self.applied = []
        self.positions = []
        self.com = [0.0, 0.0, 0.0]
        self.rotation = [0.0, 0.0, 0.0, 1.0]  # x, y, z, w

    def get_transforms(self):
        return np.array([[*self.position, *self.rotation]], dtype=np.float32)

    def get_velocities(self):
        return np.array([[*self.velocity, 0.0, 0.0, 0.0]], dtype=np.float32)

    def get_masses(self):
        return np.array([self.mass], dtype=np.float32)

    def apply_forces_and_torques_at_position(self, force, torque, position, indices, is_global):
        assert indices.tolist() == [0]
        self.wrench = (np.array(force[0], dtype=float), np.array(torque[0], dtype=float))
        self.applied.append(bool(is_global))
        self.positions.append(None if position is None else [round(float(v), 6) for v in position[0]])

    def get_coms(self):
        return np.array([[*self.com, 0.0, 0.0, 0.0, 1.0]], dtype=np.float32)

    def step(self):
        acceleration = self.wrench[0] / self.mass + np.array([0.0, 0.0, -GRAVITY])
        self.velocity = self.velocity + acceleration * DT
        self.position = self.position + self.velocity * DT
        self.wrench = (np.zeros(3), np.zeros(3))  # a force lasts one step


@pytest.fixture
def world(monkeypatch, tmp_path):
    body = Body()
    state = SimpleNamespace(body=body, mode="pause", steps=0, frames=0, fabric=0)

    def step(stage, steps):
        for _ in range(steps):
            body.step()
        state.steps += steps

    manager = SimpleNamespace(get_active_physics_engine=lambda: "physx", get_physics_dt=lambda: DT,
                              get_physics_simulation_view=lambda: object())
    monkeypatch.setattr(physics, "manager", lambda: manager)
    monkeypatch.setattr(physics, "view_for", lambda path, articulation=False: body)
    monkeypatch.setattr(physics, "step", step)
    monkeypatch.setattr(physics, "sync_fabric", lambda stage: setattr(state, "fabric", state.fabric + 1) or True)

    async def next_frame():
        state.frames += 1
        await asyncio.sleep(0)

    state.timeline = SimpleNamespace(is_stopped=lambda: state.mode == "stop", is_playing=lambda: state.mode == "play")
    state.next_frame, state.out = next_frame, tmp_path / "out"
    return state


def start(world, controller, duration=1.0, store=None, **settings):
    cleaned = control_run.validate({"duration_s": duration, **settings})
    log = control_run.CsvLog(world.out, settings.get("filename", "run.csv"), cleaned["fields"])
    run = control_run.Run(control_run.BodyIO("/World/Body"), controller, log, cleaned, object(), lambda: None,
                          world.timeline, world.next_frame, asyncio.Lock())
    store = store or jobs.JobStore(asyncio.ensure_future)
    return store.start("sim.simulation.run", run.work), run


async def finish(job, turns=20000):
    for _ in range(turns):
        if job.state != "running":
            return job.describe()
        await asyncio.sleep(0)
    raise AssertionError("the run did not end")


def hover(state):
    """A PD altitude hold at 1 m."""
    error = state["params"].get("height", 1.0) - state["position"][2]
    lift = state["mass"] * (GRAVITY + 16.0 * error - 8.0 * state["linear_velocity"][2])
    return {"force": [0.0, 0.0, lift], "log": {"error": error}}


@pytest.mark.anyio
async def test_a_controller_flies_the_body_and_the_log_records_every_step(world):
    job, run = start(world, hover, duration=4.0)
    assert run.total == 400 and run.per_frame == 2  # about real time at 60 frames per second
    out = await finish(job)
    assert out["state"] == "succeeded", out
    result = out["result"]
    assert result["steps"] == 400 and result["ended_by"] == "duration" and result["log_rows"] == 400
    assert result["final_state"]["position"][2] == pytest.approx(1.0, abs=0.02), "the PD loop settled at its target"
    assert world.steps == 400 and world.fabric == 200 and world.frames == 200, "stepped in chunks, one app frame each"
    assert world.body.applied and all(world.body.applied), "the default frame is the world frame"
    rows = list(csv.reader(Path(result["log_file"]).read_text().splitlines()))
    assert rows[0] == ["step", "time_s", "px", "py", "pz", "qw", "qx", "qy", "qz", "vx", "vy", "vz", "wx", "wy", "wz",
                       "fx", "fy", "fz", "tx", "ty", "tz", "error"]
    assert len(rows) == 401 and rows[1][:2] == ["0", "0"] and float(rows[1][-1]) == pytest.approx(1.0)
    assert float(rows[1][17]) == pytest.approx(2.0 * (GRAVITY + 16.0)), "the logged force is the one applied at that step"
    assert out["progress"]["percent"] == 100.0


@pytest.mark.anyio
async def test_params_reach_the_controller_and_done_ends_the_run_early(world):
    seen = []

    def controller(state):
        seen.append(state)
        return {"done": True} if state["step"] == 5 else None

    job, _ = start(world, controller, duration=1.0, params={"height": 3}, log=["position"], log_every=2)
    out = await finish(job)
    assert out["result"]["ended_by"] == "controller" and out["result"]["steps"] == 5 and world.steps == 5
    assert seen[0]["params"] == {"height": 3} and seen[0]["dt"] == DT and seen[0]["mass"] == 2.0
    assert seen[3]["time_s"] == pytest.approx(0.03) and seen[3]["position"][2] < 0, "it fell: no force was returned"
    rows = list(csv.reader(Path(out["result"]["log_file"]).read_text().splitlines()))
    assert rows[0] == ["step", "time_s", "px", "py", "pz"] and [r[0] for r in rows[1:]] == ["0", "2", "4"]


@pytest.mark.anyio
@pytest.mark.parametrize("answer,problem", [
    (lambda state: 1 / 0, "ZeroDivisionError"),
    (lambda state: {"force": [1, 2]}, "three finite numbers"),
    (lambda state: {"force": [0, 0, float("nan")]}, "three finite numbers"),
    (lambda state: "up", "returns a dict"),
    (lambda state: {"frame": "sideways"}, "world or body"),
])
async def test_a_bad_controller_fails_the_job_by_name_and_keeps_the_log(world, answer, problem):
    calls = []

    def controller(state):
        calls.append(state["step"])
        return None if state["step"] < 3 else answer(state)

    job, run = start(world, controller)
    out = await finish(job)
    assert out["state"] == "failed" and out["code"] == "CONTROLLER_FAILED" and problem in out["error"]
    assert out["step"] == 3 and out["steps"] == 3 and world.steps == 3, "nothing was stepped after the failure"
    assert Path(out["log_file"]).is_file() and run.log.stream.closed


@pytest.mark.anyio
async def test_cancel_and_a_timeline_change_both_end_the_run_cleanly(world):
    store = jobs.JobStore(asyncio.ensure_future)
    job, run = start(world, hover, duration=100.0, store=store)
    for _ in range(10):
        await asyncio.sleep(0)
    store.cancel(job.id)
    out = await finish(job)
    assert out["state"] == "cancelled" and 0 < world.steps < 10000 and run.log.stream.closed

    job, _ = start(world, hover, duration=100.0, store=store, filename="second.csv")
    for _ in range(10):
        await asyncio.sleep(0)
    world.mode = "stop"
    out = await finish(job)
    assert out["state"] == "failed" and out["code"] == "TIMELINE_INTERRUPTED" and out["steps"] > 0


@pytest.mark.anyio
async def test_a_body_frame_wrench_is_applied_in_the_body_frame(world):
    job, _ = start(world, lambda state: {"torque": [0, 0, 1], "frame": "body"}, duration=0.05)
    await finish(job)
    assert world.body.applied == [False] * 5


@pytest.mark.anyio
async def test_a_tcp_controller_is_a_separate_process_speaking_json_lines(world):
    ready = threading.Event()
    port = []
    server = threading.Thread(target=controller_server.serve, args=(hover, 0),
                              kwargs={"ready": lambda p: (port.append(p), ready.set())}, daemon=True)
    server.start()
    assert ready.wait(5)
    kind, host, number = control_run.parse_controller(f"tcp://127.0.0.1:{port[0]}")
    assert kind == "tcp"
    job, _ = start(world, control_run.TcpController(host, number), duration=2.0)
    out = await finish(job, turns=200000)
    assert out["state"] == "succeeded" and out["result"]["final_state"]["position"][2] == pytest.approx(1.0, abs=0.05)
    server.join(5)
    assert not server.is_alive(), "the run closed its connection, so a one-run server ends"


@pytest.mark.anyio
async def test_on_physx_a_force_is_applied_at_the_centre_of_mass_not_the_body_origin(world):
    world.body.com = [0.5, 0.0, 0.0]
    world.body.position = np.array([1.0, 2.0, 3.0])
    world.body.rotation = [0.0, 0.0, 2 ** -0.5, 2 ** -0.5]  # a quarter turn about Z: body +X points along world +Y
    job, _ = start(world, lambda state: {"force": [0, 0, 2.0 * GRAVITY]}, duration=0.01)
    await finish(job)
    assert world.body.positions == [[1.0, 2.5, 3.0]], "the world position of the centre of mass"
    job, _ = start(world, lambda state: {"force": [0, 0, 1], "frame": "body"}, duration=0.01, filename="b.csv")
    await finish(job)
    assert world.body.positions[-1] == [0.5, 0.0, 0.0], "in the body frame it is the offset itself"
    job, _ = start(world, lambda state: {"torque": [0, 0, 1]}, duration=0.01, filename="c.csv")
    await finish(job)
    assert world.body.positions[-1] is None, "a pure torque needs no point of application"


def test_rotating_a_vector_by_a_quaternion():
    assert control_run.rotate([0, 0, 0, 1], [1, 2, 3]) == pytest.approx([1, 2, 3])
    assert control_run.rotate([0, 0, 2 ** -0.5, 2 ** -0.5], [1, 0, 0]) == pytest.approx([0, 1, 0])
    assert control_run.rotate([1, 0, 0, 0], [0, 1, 0]) == pytest.approx([0, -1, 0])


def test_controller_specs(monkeypatch):
    monkeypatch.delenv(control_run.SCRIPTS_ENV, raising=False)
    assert control_run.parse_controller("tcp://localhost:9000") == ("tcp", "localhost", 9000)
    assert control_run.parse_controller("pkg.mod:fn") == ("python", "pkg.mod", "fn")
    assert control_run.parse_controller("/abs/ctl.py:step") == ("python", "/abs/ctl.py", "step")
    for bad in ("", "tcp://127.0.0.1", "tcp://127.0.0.1:0", "just_a_name", 5, "pkg.mod:not valid"):
        with pytest.raises(AssetError) as error:
            control_run.parse_controller(bad)
        assert error.value.code == "INVALID_VALUE"
    with pytest.raises(AssetError) as error:
        control_run.parse_controller("tcp://192.0.2.10:9000")
    assert error.value.code == "CONTROLLER_NOT_ALLOWED", "another host is opt-in"
    monkeypatch.setenv(control_run.SCRIPTS_ENV, "1")
    assert control_run.parse_controller("tcp://192.0.2.10:9000") == ("tcp", "192.0.2.10", 9000)


def test_a_python_controller_is_code_in_kit_and_needs_the_opt_in(monkeypatch, tmp_path):
    source = tmp_path / "ctl.py"
    source.write_text("def lift(state):\n    return {'force': [0, 0, 1]}\nvalue = 3\n")
    monkeypatch.delenv(control_run.SCRIPTS_ENV, raising=False)
    with pytest.raises(AssetError) as error:
        control_run.load_python(str(source), "lift")
    assert error.value.code == "CONTROLLER_NOT_ALLOWED" and "tcp://127.0.0.1" in str(error.value)
    monkeypatch.setenv(control_run.SCRIPTS_ENV, "1")
    assert control_run.load_python(str(source), "lift")({}) == {"force": [0, 0, 1]}
    assert control_run.load_python("json", "dumps") is json.dumps
    for spec, code in ((str(source), "value"), (str(source), "missing")):
        with pytest.raises(AssetError) as error:
            control_run.load_python(spec, code)
        assert error.value.code == "NOT_FOUND"
    with pytest.raises(AssetError) as error:
        control_run.load_python(str(tmp_path / "none.py"), "lift")
    assert error.value.code == "NOT_FOUND"
    broken = tmp_path / "broken.py"
    broken.write_text("import does_not_exist_anywhere\n")
    with pytest.raises(AssetError) as error:
        control_run.load_python(str(broken), "lift")
    assert error.value.code == "CONTROLLER_FAILED"


def test_no_controller_listening_is_a_refusal_that_says_what_to_start():
    with pytest.raises(AssetError) as error:
        control_run.TcpController("127.0.0.1", 1)
    assert error.value.code == "CONTROLLER_UNREACHABLE" and "controller_server.serve" in str(error.value)


@pytest.mark.parametrize("args", [
    {"duration_s": 0}, {"duration_s": 3601}, {"duration_s": "5"}, {"duration_s": 1, "steps_per_frame": 121},
    {"duration_s": 1, "log_every": 0}, {"duration_s": 1, "frame": "local"}, {"duration_s": 1, "params": [1]},
    {"duration_s": 1, "log": ["position", "altitude"]}, {"duration_s": 1, "log": "position"},
])
def test_bad_run_settings_are_refused_before_anything_starts(args):
    with pytest.raises(AssetError) as error:
        control_run.validate(args)
    assert error.value.code == "INVALID_VALUE"


def test_the_log_never_overwrites_and_names_are_checked(tmp_path):
    control_run.CsvLog(tmp_path, "a.csv", ["position"]).close()
    with pytest.raises(AssetError) as error:
        control_run.CsvLog(tmp_path, "a.csv", ["position"])
    assert error.value.code == "CONFLICT"
    for name in ("../a.csv", "a.txt", "sub/a.csv", ""):
        with pytest.raises(AssetError) as error:
            control_run.CsvLog(tmp_path, name, ["position"])
        assert error.value.code == "INVALID_OUTPUT"


def test_a_view_that_cannot_apply_forces_is_refused_without_a_fallback(world):
    body = control_run.BodyIO("/World/Body")
    body.view = SimpleNamespace()
    body.backend = "newton"
    body.apply((0, 0, 0), (0, 0, 0), "world")  # nothing to apply: no call needed
    with pytest.raises(AssetError) as error:
        body.apply((0, 0, 1), (0, 0, 0), "world")
    assert error.value.code == "WRENCH_UNSUPPORTED"


def test_nonfinite_state_from_the_engine_is_an_observation_failure(world):
    world.body.position = np.array([0.0, float("nan"), 0.0])
    with pytest.raises(AssetError) as error:
        control_run.BodyIO("/World/Body").read()
    assert error.value.code == "PHYSICS_STATE_INVALID"


# ---- the tool itself, through the extension's handler ----

from test_scene_recovery_and_recording import ext, kit  # noqa: E402,F401 - fixtures
from test_scene_usd import scene  # noqa: E402,F401 - fixture


@pytest.fixture
def tool(ext, world, monkeypatch):  # noqa: F811
    stage = ext.scene.stage()
    ext.scene.create(stage, "/World/Drone", "Cube", rigid=True)
    ext.scene.create(stage, "/World/Marker", "Xform")
    ext.scene.output = world.out
    initialized = []

    async def control(args):
        initialized.append(args["action"])
        ext.scene.timeline.mode = "pause"
        return {"action": args["action"]}

    monkeypatch.setattr(ext, "_timeline_control", control)
    monkeypatch.setattr(ext.scene, "check_runtime_edit", lambda stage: None)
    monkeypatch.setenv(control_run.SCRIPTS_ENV, "1")
    ext.initialized = initialized
    return ext.handler("sim.simulation.run")


@pytest.mark.anyio
async def test_the_tool_initializes_a_stopped_timeline_and_returns_a_job_at_once(tool, ext, world, tmp_path):  # noqa: F811
    source = tmp_path / "ctl.py"
    source.write_text("def lift(state):\n    return {'force': [0, 0, state['mass'] * 9.81]}\n")
    started = await tool(**ext.ids, body="/World/Drone", controller=f"{source}:lift", duration_s=0.5, filename="hover.csv", label="hover")
    assert started["status"] == "ok" and started["state"] == "running" and started["controller"] == "python"
    assert started["steps"] == 50 and started["physics_dt"] == DT and started["mass"] == 2.0 and started["backend"] == "physx"
    assert started["log_file"] == str(world.out / "hover.csv") and "initialized" in started and ext.initialized == ["initialize"]
    assert f'job_status(job_id="{started["job_id"]}"' in started["next"]
    job = ext.bridge.jobs.get(started["job_id"])
    out = await finish(job)
    assert out["state"] == "succeeded" and out["label"] == "hover" and out["result"]["steps"] == 50
    assert out["result"]["final_state"]["position"][2] == pytest.approx(0.0, abs=1e-3), "gravity was cancelled exactly"
    assert "paused" in out["result"]["timeline"]


@pytest.mark.anyio
async def test_the_tool_refuses_before_starting_anything(tool, ext, world):  # noqa: F811
    async def refused(**args):
        result = await tool(**{**ext.ids, "body": "/World/Drone", "controller": "json:dumps", "duration_s": 1, **args})
        assert result["status"] == "error", result
        return result["code"]

    assert await refused(body="/World/Marker") == "INVALID_PHYSICS"
    assert await refused(duration_s=0) == "INVALID_VALUE"
    assert await refused(controller="tcp://127.0.0.1:1") == "CONTROLLER_UNREACHABLE"
    assert await refused(filename="bad name.csv") == "INVALID_OUTPUT"
    ext.scene.timeline.mode = "play"
    assert await refused() == "TIMELINE_ACTIVE"
    ext.scene.timeline.mode = "stop"
    assert await refused(stage_id="stale") == "STALE_TARGET"
    assert ext.bridge.jobs.jobs() == [] and world.steps == 0
    ext.bridge.jobs = None
    ext.bridge = SimpleNamespace()
    assert await refused() == "JOBS_UNAVAILABLE"


@pytest.mark.anyio
async def test_too_many_jobs_is_a_refusal_that_leaves_no_log_behind(tool, ext, world):  # noqa: F811
    async def wait(job):
        await asyncio.Event().wait()

    held = [ext.bridge.jobs.start("run_python", wait, cooperative=False) for _ in range(jobs.MAX_RUNNING)]
    result = await tool(**ext.ids, body="/World/Drone", controller="json:dumps", duration_s=1, filename="full.csv")
    assert result["code"] == "JOB_LIMIT" and "job_cancel" in result["message"]
    for job in held:
        ext.bridge.jobs.cancel(job.id)
    await asyncio.sleep(0)

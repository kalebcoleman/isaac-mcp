"""The one check-in call (status), waiting on simulator jobs, and isaac_help admitting what it cannot answer."""

import json
import socket
from pathlib import Path

import pytest
from PIL import Image

import isaac_mcp.server as gw
from isaac_mcp import screen
from isaac_mcp.client import BridgeError

IDENTITY = {
    "pid": 4242, "port": 8766, "app": "Isaac Sim", "app_version": "6.2.0", "headless": True, "app_ready": True,
    "uptime_s": 12.5, "hostname": "lab", "bind_host": "127.0.0.1", "python": "3.12.0", "namespaces": ["sim", "assets"],
    "tools": 80, "toolsets": {"sim": "abc"}, "admission": {"queued": 0}, "hold": {"held": False},
    "jobs": {"running": 0, "jobs": []}, "extension": {"commit": "unknown", "dirty": None},
}
STAGE = {"session_id": "s1", "stage_id": "g1", "source": "anon:0x1:World.usd", "default_prim": "/World", "playing": False,
         "stopped": True, "runtime": {"physics_engine": "physx"}, "prim_count": 9}


class Bridge:
    def __init__(self, answers):
        self.answers, self.calls, self.port = answers, [], 8766

    def call(self, tool, params=None, **kw):
        self.calls.append((tool, params))
        answer = self.answers[tool]
        if isinstance(answer, BaseException):
            raise answer
        return answer(params) if callable(answer) else answer


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "_advertised", lambda: [])
    monkeypatch.setattr(gw, "live_sessions", lambda env=None: [])
    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(gw, "_PRE_DISPATCH_HOOKS", [])
    monkeypatch.setattr(gw, "_POST_DISPATCH_HOOKS", [])
    return gw._BridgeState()


def test_status_is_one_short_answer_with_the_stage_ids(state):
    state.client = Bridge({"status": IDENTITY, "sim.stage.info": STAGE})
    out = json.loads(gw._dispatch("status", {}, state))
    assert out["running"] is True and out["pid"] == 4242 and out["port"] == 8766 and out["app_ready"] is True
    assert out["stage"] == {"session_id": "s1", "stage_id": "g1", "source": "anon:0x1:World.usd", "default_prim": "/World",
                            "timeline": "stopped", "physics_engine": "physx"}
    assert not {"admission", "toolsets", "namespaces", "extension", "hold", "bridges", "jobs_running"} & set(out)
    assert "no need to call this again" in out["note"]
    assert [tool for tool, _ in state.client.calls] == ["status", "sim.stage.info"], "one check-in is two cheap bridge reads"
    assert len(json.dumps(out)) < 900


def test_status_detail_is_the_full_identity_and_never_reaches_an_older_bridge_as_an_argument(state):
    state.client = Bridge({"status": IDENTITY})
    out = json.loads(gw._dispatch("status", {"detail": True}, state))
    host = out.pop("host")
    assert out == IDENTITY and "version" in host
    assert state.client.calls == [("status", None)], "detail is the host's own switch"


def test_status_names_what_else_is_running(state, monkeypatch):
    busy = dict(IDENTITY, namespaces=[], hold={"held": True, "holder": "eval"},
                jobs={"running": 1, "jobs": [{"job_id": "job-1", "kind": "sim.simulation.run", "state": "running"}]})
    state.client = Bridge({"status": busy})
    monkeypatch.setattr(gw, "_advertised", lambda: [{"port": 8766, "pid": 4242}, {"port": 8770, "pid": 5151}])
    monkeypatch.setattr(gw, "live_sessions", lambda env=None: [{"id": "abc", "port": 8770, "pid": 5151, "physics_backend": "newton", "log": "x"}])
    out = json.loads(gw._dispatch("status", {}, state))
    assert out["jobs_running"][0]["job_id"] == "job-1" and out["hold"]["holder"] == "eval"
    assert [b["port"] for b in out["bridges"]] == [8766, 8770]
    assert out["managed_sessions"] == [{"id": "abc", "port": 8770, "pid": 5151, "physics_backend": "newton"}]
    assert "sim-tools" in out["stage"]["note"], "a Kit without the scene pack says so instead of failing"


def test_nothing_running_is_an_answer_that_names_the_next_call(state, monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    monkeypatch.setenv("ISAAC_MCP_PORT", str(free))
    monkeypatch.delenv("UR5E_DT_PORT", raising=False)
    text = gw._dispatch("status", {}, state)
    out = json.loads(text)
    assert out["running"] is False and "sim__launch" in out["message"] and "refresh_tools(wait_s=120)" in out["message"]
    assert out["bridges"] == [] and "version" in out["host"] and not gw._failed_tool_result(text)
    monkeypatch.setattr(gw, "_advertised", lambda: [{"port": 8770, "pid": 5151, "app": "Isaac Sim"}])
    elsewhere = json.loads(gw._dispatch("status", {}, state))
    assert elsewhere["running"] is False and "use_bridge(port=8770)" in elsewhere["message"]
    state.client = Bridge({"status": RuntimeError("multiple bridges running (ports 8766, 8770): set ISAAC_MCP_PORT")})
    several = json.loads(gw._dispatch("status", {}, state))
    assert several["running"] is False and "use_bridge(port)" in several["message"] and "Do not start another" in several["message"]


def test_a_bridge_that_answers_badly_is_still_an_error(state):
    for failure in (TimeoutError("timed out"), BridgeError("DOORWAY_BUSY: run_python holds the loop")):
        state.client = Bridge({"status": failure})
        assert gw._dispatch("status", {}, state).startswith("Error:")
    state.client = Bridge({"status": IDENTITY, "sim.stage.info": BridgeError("APP_STARTING")})
    assert "APP_STARTING" in json.loads(gw._dispatch("status", {}, state))["stage"]["error"]


# ---- job_status through the host ----

def test_job_status_waits_on_the_host_until_the_job_ends(state, monkeypatch):
    monkeypatch.setattr(gw, "JOB_WAIT_POLL_S", 0.0)
    replies = iter([{"job_id": "job-1", "state": "running"}] * 3 + [{"job_id": "job-1", "state": "succeeded", "result": {"steps": 5}}])
    state.client = Bridge({"job_status": lambda params: next(replies)})
    out = json.loads(gw._dispatch("job_status", {"job_id": "job-1", "wait_s": 30}, state))
    assert out["state"] == "succeeded" and "wait_timed_out" not in out
    assert state.client.calls == [("job_status", {"job_id": "job-1"})] * 4, "wait_s never reaches the bridge"


def test_job_status_wait_gives_up_and_says_so_and_bad_waits_are_refused(state, monkeypatch):
    monkeypatch.setattr(gw, "JOB_WAIT_POLL_S", 0.0)
    state.client = Bridge({"job_status": {"job_id": "job-1", "state": "running"}})
    out = json.loads(gw._dispatch("job_status", {"job_id": "job-1", "wait_s": 0.01}, state))
    assert out["state"] == "running" and out["wait_timed_out"] is True
    assert json.loads(gw._dispatch("job_status", {"job_id": "job-1"}, state)) == {"job_id": "job-1", "state": "running"}
    for bad in (61, -1, True):
        refused = gw._dispatch("job_status", {"job_id": "job-1", "wait_s": bad}, state)
        assert gw._failed_tool_result(refused) and "wait_s" in refused
    listing = Bridge({"job_status": {"jobs": [], "running": 0}})
    state.client = listing
    assert json.loads(gw._dispatch("job_status", {"wait_s": 5}, state)) == {"jobs": [], "running": 0}
    assert len(listing.calls) == 1, "a listing has nothing to wait for"


def replay_result(folder, frames=4):
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(1, frames + 1):
        Image.new("RGB", (64, 48), (index * 40, 10, 10)).save(folder / f"flight-{index:05d}.png")
    return {"job_id": "job-7", "state": "succeeded",
            "result": {"frames": frames, "encode": {"folder": str(folder), "pattern": "flight-*.png", "frames": frames,
                                                    "fps": 2.0, "label": "PhysX hover", "output": "flight.mp4"}}}


def test_a_finished_replay_gets_its_labelled_video_once(state, monkeypatch, tmp_path):
    try:
        screen._ffmpeg()
    except RuntimeError:
        pytest.skip("no ffmpeg")
    monkeypatch.setattr(screen, "VIDEO_DIR", tmp_path / "videos")
    state.client = Bridge({"job_status": replay_result(tmp_path / "out")})
    first = json.loads(gw._dispatch("job_status", {"job_id": "job-7"}, state))["result"]
    assert first["video_path"] == str(tmp_path / "videos" / "flight.mp4") and Path(first["video_path"]).stat().st_size > 0
    assert first["video"]["frames"] == 4 and first["video"]["label"] == "PhysX hover" and first["video"]["duration_s"] == 2.0
    written = Path(first["video_path"]).stat().st_mtime_ns
    again = json.loads(gw._dispatch("job_status", {"job_id": "job-7"}, state))["result"]
    assert again["video_path"] == first["video_path"] and Path(first["video_path"]).stat().st_mtime_ns == written
    assert sorted(p.name for p in (tmp_path / "videos").iterdir()) == ["flight.mp4"]


def test_frames_this_server_cannot_read_are_kept_and_the_reply_says_how_to_encode_them(state, monkeypatch, tmp_path):
    monkeypatch.setattr(screen, "VIDEO_DIR", tmp_path / "videos")
    reply = replay_result(tmp_path / "out")
    reply["result"]["encode"]["folder"] = str(tmp_path / "on-another-machine")
    state.client = Bridge({"job_status": reply})
    result = json.loads(gw._dispatch("job_status", {"job_id": "job-7"}, state))["result"]
    assert "video_path" not in result and "0 of 4 frames" in result["video_error"] and 'join_videos(mode="frames"' in result["video_error"]


# ---- isaac_help ----

def gaps(tmp_path):
    file = tmp_path / "state" / "help-gaps.jsonl"
    return [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []


def test_a_drone_controller_question_gets_the_controller_runner_not_the_arm_tools(tmp_path):
    answer = gw._isaac_help("run my own controller on a drone")
    assert "sim__simulation__run" in answer and "tcp://127.0.0.1" in answer and "job_status" in answer
    assert "sim__arm__ik" not in answer and "sim__joints__move" not in answer
    assert "isaac_help(task=..., no_fit=true)" in answer and gaps(tmp_path) == [], "an answered question is not a gap"


def test_replay_and_job_questions_name_their_tools():
    replay = gw._isaac_help("replay saved flights into a labelled video")
    assert "sim__trajectory__replay" in replay and "video_path" in replay
    waiting = gw._isaac_help("is it done yet, how do I wait for a long loop")
    assert "job_status(job_id, wait_s=30)" in waiting and "background=true" in waiting
    assert "run_script" in gw._isaac_help("run a script as a managed job outside isaac")
    assert "action=reset" in gw._isaac_help("the timeline was left playing, reset to a clean state")
    assert "status (one call" in gw._isaac_help("where am i, what is running")


def test_an_unanswered_question_says_so_gives_the_pattern_and_is_recorded_locally(tmp_path):
    answer = gw._isaac_help("simulate cloth tearing")
    assert answer.startswith("No isaac tool is known to fit 'simulate cloth tearing'.")
    assert "background=true" in answer and "next_update_async" in answer and str(tmp_path / "state" / "help-gaps.jsonl") in answer
    (record,) = gaps(tmp_path)
    assert record["task"] == "simulate cloth tearing" and record["reason"] == "no_match" and "time" in record


def test_no_fit_records_a_task_whose_named_tools_did_not_fit(tmp_path):
    answer = gw._isaac_help("move a joint along a spline with jerk limits", no_fit=True)
    assert answer.startswith("No isaac tool is known to fit") and "The closest topics" in answer and "sim__joints__move" in answer
    assert [g["reason"] for g in gaps(tmp_path)] == ["no_fit"]
    assert "full map" not in gw._isaac_help(None, no_fit=True) and len(gaps(tmp_path)) == 1, "no task, nothing to record"


def test_recording_can_be_turned_off_and_never_breaks_the_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_LOG", "0")
    assert "recorded for the maintainer" not in gw._isaac_help("simulate cloth tearing") and gaps(tmp_path) == []
    monkeypatch.setenv("ISAAC_MCP_LOG", "1")
    (tmp_path / "state").write_text("a file where the folder should be")
    assert gw._isaac_help("simulate cloth tearing").startswith("No isaac tool is known to fit")

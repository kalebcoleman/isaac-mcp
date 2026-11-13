"""No RSS, network or GPU required: exercise its executable boundary."""

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest

from isaac_mcp.robosim import PIN, RoboSimBridge, register_robosim_tools


STUB = r'''
import hashlib, json, os, pathlib, re, signal, subprocess, sys, time
args = sys.argv[1:]
run = pathlib.Path(args[args.index("--run-dir") + 1])
if "robosimstudio_real2sim_agent.serve" in args:
    while True:
        time.sleep(0.1)
args = args[2:]
action = args[0]
with (run / "calls.jsonl").open("a") as output:
    output.write(json.dumps({"args": args, "cwd": os.getcwd(), "env": {
        key: os.environ.get(key) for key in ["CUDA_VISIBLE_DEVICES", "ROBOSIMSTUDIO_DEVICE",
        "TMPDIR", "XDG_CACHE_HOME", "WARP_CACHE_PATH", "PYTHONDONTWRITEBYTECODE"]}}) + "\n")
def option(key):
    return args[args.index(key) + 1]
def write(name, text="fake"):
    path = pathlib.Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return str(path)
def state():
    path = run / "state.json"
    return json.loads(path.read_text()) if path.exists() else {"generation": 1, "resets": 0, "last_error": ""}
def save(value):
    (run / "state.json").write_text(json.dumps(value))
reply = {"ok": True}
if action == "up":
    proc = subprocess.Popen([sys.executable, __file__, "-m", "robosimstudio_real2sim_agent.serve",
                             "--run-dir", str(run)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    write(run / "serve.pid", str(proc.pid))
    time.sleep(0.05)
    reply.update(pid=proc.pid, url="http://localhost:" + option("--port"))
    if "BREAK" in (run / "scene.py").read_text():
        save({"generation": 1, "resets": 0, "last_error": "Traceback: scene would not build"})
elif action == "quit":
    if not (run / "no-quit").exists():
        os.kill(int((run / "serve.pid").read_text()), signal.SIGTERM)
elif action == "reload":
    current = state()
    if "BREAK" in (run / "scene.py").read_text():
        current["last_error"] = "Traceback: scene would not build"
        save(current)
        reply.update(ok=False, error=current["last_error"])
    else:
        current.update(generation=current["generation"] + 1, last_error="")
        save(current)
        reply.update(seconds=0.1, names=["block", "pusher", "table"])
elif action == "reset":
    current = state()
    current["resets"] += 1
    save(current)
elif action == "eval":
    code = " ".join(args[1:-1])
    if code.startswith("float("):
        reply.update(value=os.environ.get("FAKE_HZ", "50.0"), stdout="", playing=False)
    else:
        write(run / "rollout.txt", code)
        reply.update(value=None, stdout="", playing=False)
elif action == "ref":
    out = pathlib.Path(option("--out"))
    anchors = []
    for item in option("--at").split(","):
        when, _, label = item.partition(":")
        t = 6.0 if when == "end" else float(when)
        write(out / f"t{t:06.2f}.jpg")
        anchors.append({"t": t, "file": f"t{t:06.2f}.jpg", "label": label})
    write(out / "timeline.json", json.dumps({"clip": "fake", "duration": 12.0, "anchors": anchors}))
    reply.update(frames=[a["file"] for a in anchors], contact=write(out / "contact.jpg"),
                 timeline=str(out / "timeline.json"), duration=12.0, fps=10.0)
elif action == "snapshot":
    reply["image"] = write(option("--out"))
elif action == "compare":
    def reading(centre):
        return {"photo": {"centre": [100.0, 100.0], "pixels": 1000}, "sim": {"centre": centre, "pixels": 1100},
                "iou": 0.5, "offset_px": [2, 3], "width_ratio": 1.1, "height_ratio": 0.9, "size_ratio": 1.049}
    if "--take" in args:
        timeline = json.loads(pathlib.Path(option("--timeline")).read_text())
        rows = []
        last = len(timeline["anchors"]) - 1
        for i, anchor in enumerate(timeline["anchors"]):
            moved = [100.0 - 40 * i / last, 100.0 - 40 * i / last]
            row_photo = [100.0 - 50 * i / last, 100.0 - 60 * i / last]
            entry = reading(moved)
            entry["photo"] = dict(entry["photo"], centre=row_photo)
            rows.append({"t": anchor["t"], "label": anchor["label"], "sim_t": anchor["sim_t"],
                         "sim_step": round(anchor["sim_t"] * 50), "objects": {"block": entry}})
        reply.update(anchors=rows, verdict="1 of 1 did what the clip did", score=0.4, strip=write(run / "strip.png"))
    else:
        reply.update(objects={"block": reading([102.0, 103.0])}, beside=write(option("--out")))
elif action == "play":
    text = (run / "rollout.txt").read_text() if (run / "rollout.txt").exists() else "ROLLOUT = {}"
    rollout = eval(text.split("=", 1)[1], {"None": None})
    write(run / "pending.json", json.dumps({"take": option("--record"), "at": time.time(), "every": int(option("--every")),
                                            "rollout": rollout, "routine": args[1] if not args[1].startswith("--") else None}))
    reply["playing"] = args[1] if not args[1].startswith("--") else "push"
elif action == "status":
    current = state()
    reply.update(playing=0, recording="", last_take="", last_error=current["last_error"], names=["block", "pusher", "table"],
                 cameras=["camera"], generation=current["generation"], resets=current["resets"],
                 routines=["push", "settle"], board=["a proportional verdict"])
    pending = run / "pending.json"
    if pending.exists():
        data = json.loads(pending.read_text())
        take = data["take"]
        if time.time() - data["at"] < 0.2:
            reply.update(playing=1, recording=take)
        elif not (run / (take + ".json")).exists():
            steps = data["rollout"].get("steps", 100)
            every = data["every"]
            marks = list(range(0, steps + 1, every)) + ([steps] if steps % every else [])
            frames = [{"i": i, "step": m, "t": round(m / 50.0, 4)} for i, m in enumerate(marks)]
            write(run / (take + ".mp4"))
            write(run / (take + ".json"), json.dumps({"name": take, "every": every, "fps": round(50 / every), "control_hz": 50.0,
                                                      "frames": frames}))
            if data["rollout"].get("push") and "IGNORE_PUSH" not in (run / "scene.py").read_text():
                write(run / ("push-" + take + ".json"), json.dumps({"applied": True, "pusher_tracked": True}))
            reply["last_take"] = take
        else:
            reply["last_take"] = take
print(json.dumps(reply))
'''


@pytest.fixture
def fake(tmp_path, monkeypatch):
    root = tmp_path / "rss"
    python = root / ".venv/bin/python"
    ctl = root / "examples/live/ctl"
    python.parent.mkdir(parents=True)
    ctl.parent.mkdir(parents=True)
    python.write_text(f"#!{sys.executable}\n" + STUB)
    python.chmod(0o755)
    ctl.write_text(f'#!/bin/sh\nexec {shlex.quote(str(python))} "$@"\n')
    ctl.chmod(0o755)
    original_run = subprocess.run
    gpu_calls = []

    def run(argv, **kwargs):
        if argv[0] == "git":
            return subprocess.CompletedProcess(argv, 0, PIN + "0" * 33 + "\n", "")
        if argv[0] == "nvidia-smi":
            gpu_calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "4242, somebody-elses-sim\n", "")
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    bridge = RoboSimBridge(workspace=tmp_path / "workspace", root=root)
    scene = tmp_path / "scene.py"
    scene.write_text("scene = 'fake'\n")
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    yield bridge, scene, video, gpu_calls
    for future in list(bridge._jobs.values()):
        future.result(timeout=10)
    for path in bridge.runs.glob("*/run.json"):
        bridge.call("stop", run=path.parent.name, wait_s=10)


def start(fake, **kwargs):
    bridge, scene, _, _ = fake
    return bridge.call("start", name="test", scene=str(scene), device="cpu", wait_s=10, **kwargs)


def complete(bridge, action, run, **kwargs):
    result = bridge.call(action, run=run, wait_s=10, **kwargs)
    assert result["ok"], (result.get("error"), result.get("operation"))
    assert result["operation"]["state"] == "completed", result
    return result["operation"]["result"]


@pytest.mark.parametrize("action", ["status", "scene_template", "start", "update_scene", "reference", "snapshot", "compare", "rollout", "compare_take", "stop", "report"])
def test_missing_install(tmp_path, action):
    result = RoboSimBridge(workspace=tmp_path).call(action)
    assert not result["ok"]
    assert PIN in result["error"] and "https://github.com/robosimstudio/robosimstudio" in result["error"]
    assert str(tmp_path / "tools/robosimstudio") in result["error"]
    assert result["rss_commit"] is None


def test_pin_mismatch(fake, monkeypatch):
    bridge = fake[0]
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "abcdef0\n", ""))
    result = bridge.call("scene_template")
    assert result["ok"] and not result["pin_match"]
    assert result["rss_commit"] == "abcdef0"
    assert "differs" in result["warnings"][0]


def test_missing_venv(fake):
    bridge = fake[0]
    (bridge.root / ".venv/bin/python").unlink()
    assert "private environment missing" in bridge.call("status")["error"]


@pytest.mark.parametrize("executable", [".venv/bin/python", "examples/live/ctl"])
def test_unexecutable_install(fake, executable):
    bridge = fake[0]
    (bridge.root / executable).chmod(0o644)
    assert "private environment missing" in bridge.call("status")["error"]


def test_host_python_310_hashing(fake, monkeypatch):
    monkeypatch.delattr(hashlib, "file_digest", raising=False)
    bridge, scene, _, _ = fake
    result = start(fake)
    _, ledger = bridge._load(result["run"])
    assert ledger["inputs"]["scene"]["sha256"] == hashlib.sha256(scene.read_bytes()).hexdigest()


def test_name_length_before_creating_run(fake):
    bridge, scene, _, _ = fake
    result = bridge.call("start", name="a" * 65, scene=str(scene), device="cpu")
    assert "at most 64" in result["error"]
    assert not bridge.runs.exists()


def test_explicit_device_and_gpu_refusal(fake):
    bridge, scene, _, calls = fake
    result = bridge.call("start", name="test", scene=str(scene))
    assert "explicit device" in result["error"]
    result = bridge.call("start", name="test", scene=str(scene), device="cuda:0")
    assert "Other GPU work" in result["error"]
    assert calls
    assert not bridge.runs.exists()
    bridge._gpu_guard(True)
    bridge._gpu_guard(False, owned_pid=4242)


def test_gpu_check_fails_closed(fake, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", "failed"))
    with pytest.raises(ValueError, match="Cannot check GPU"):
        fake[0]._gpu_guard(False)


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("nvidia-smi", 10)])
def test_unavailable_gpu_check(fake, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(ValueError, match="Cannot check GPU"):
        fake[0]._gpu_guard(False)


def test_complete_workflow_and_containment(fake):
    bridge, scene, video, gpu_calls = fake
    result = start(fake)
    assert result["ok"] and result["server_alive"]
    assert result["engine"]["physics"].startswith("plain MuJoCo")
    run = result["run"]
    path = Path(result["run_dir"])
    assert path.parent == bridge.runs and path.name.endswith("-test")
    assert (path / "scene.py").read_bytes() == scene.read_bytes() == (path / "scene-v1.py").read_bytes()
    reference = complete(bridge, "reference", run, video=str(video), at="0:start,6:end", width=100)
    assert reference["window"] is None
    complete(bridge, "snapshot", run, camera="camera", size="80x60")
    compare = complete(bridge, "compare", run, keys=["block=red"], camera="camera", size="80x60")
    assert compare["metrics"]["block"]["iou"] == 0.5
    take = complete(bridge, "rollout", run, routine="push", take="take1", duration_s=6.0, record_fps=10, camera="camera",
                    push={"waypoints": [[0, 0.1, -0.2], [6, 0.0, 0.0]]})
    assert Path(take["mp4"]).is_file() and Path(take["json"]).is_file()
    complete(bridge, "compare_take", run, take="take1", keys=["block=red"])
    report = complete(bridge, "report", run)
    text = Path(report["report"]).read_text()
    assert "0.5" in text and "does not fit mass or friction" in text and "plain MuJoCo" in text
    assert "rollout `take1`: 300 steps = 6.0 s simulated" in text and "scene-v1.py" in text
    assert bridge.call("status")["live_servers"]
    assert complete(bridge, "stop", run)["stopped"]
    assert not bridge.call("status", run=run)["server_alive"]
    ledger = json.loads((path / "run.json").read_text())
    assert ledger["rss_commit"].startswith(PIN)
    assert ledger["inputs"]["scene"]["sha256"]
    assert ledger["device"] == "cpu"
    calls = [json.loads(line) for line in (path / "calls.jsonl").read_text().splitlines()]
    by_action = {call["args"][0]: call["args"] for call in calls}
    port = by_action["up"][by_action["up"].index("--port") + 1]
    assert by_action["up"] == ["up", "--scene", str(path / "scene.py"), "--port", port, "--hold", "--no-watch", "--json"]
    assert by_action["ref"] == ["ref", "frames", str(path / "reference-2.mp4"), "--at", "0:start,6:end", "--width", "100", "--out", str(path / "ref"), "--json"]
    assert by_action["snapshot"] == ["snapshot", "--camera", "camera", "--size", "80x60", "--out", str(path / "snapshot-3.png"), "--json"]
    assert ["compare", "--camera", "camera", "--key", "block=red", "--size", "80x60", "--out", str(path / "comparison-4.png"), "--json"] in [call["args"] for call in calls]
    assert by_action["play"] == ["play", "push", "--record", "take1", "--every", "5", "--camera", "camera", "--json"]
    assert by_action["compare"][:5] == ["compare", "--take", "take1", "--key", "block=red"]
    assert by_action["compare"][5:] == ["--timeline", str(path / "ref/timeline-map-6.json"), "--json"]
    assert by_action["quit"] == ["quit", "--json"]
    for call in calls:
        assert call["cwd"] == str(path)
        assert call["env"]["CUDA_VISIBLE_DEVICES"] == ""
        assert call["env"]["ROBOSIMSTUDIO_DEVICE"] == "cpu"
        assert call["env"]["TMPDIR"] == str(bridge.root / ".tmp")
        assert call["env"]["XDG_CACHE_HOME"] == str(bridge.root / ".cache")
        assert call["env"]["WARP_CACHE_PATH"] == str(bridge.root / ".cache/warp")
    assert not gpu_calls
    assert sorted(str(file.relative_to(bridge.root)) for file in bridge.root.rglob("*") if file.is_file()) == [".venv/bin/python", "examples/live/ctl"]
    assert all("wall_s" in command and "finished_at" in command for command in ledger["commands"])


def test_async_poll_and_busy(fake):
    bridge = fake[0]
    run = start(fake)["run"]
    started = time.monotonic()
    result = bridge.call("rollout", run=run, take="async", wait_s=0)
    assert time.monotonic() - started < 1
    assert result["operation"]["state"] == "running"
    assert "active operation" in bridge.call("snapshot", run=run)["error"]
    result = bridge.call("status", run=run, wait_s=10)
    assert result["operation"]["state"] == "completed"
    assert result["operation"]["wall_s"] >= 0.2


def test_reload_ledger_after_acquiring_lock(fake, monkeypatch):
    bridge = fake[0]
    run = start(fake)["run"]
    original = bridge._load
    injected = False

    def load(name):
        nonlocal injected
        path, ledger = original(name)
        if not injected:
            injected = True
            advanced = dict(ledger, concurrent_marker="preserve")
            bridge._save(path, advanced)
        return path, ledger

    monkeypatch.setattr(bridge, "_load", load)
    complete(bridge, "report", run)
    assert original(run)[1]["concurrent_marker"] == "preserve"


def test_no_stopping_unowned_process(fake):
    bridge = fake[0]
    run = start(fake)["run"]
    path, ledger = bridge._load(run)
    original = ledger["server"]
    ledger["server"] = {"pid": os.getpid(), "start_ticks": "not-ours", "cmdline": []}
    bridge._save(path, ledger)
    assert not complete(bridge, "stop", run)["stopped"]
    assert "quit" not in [json.loads(line)["args"][0] for line in (path / "calls.jsonl").read_text().splitlines()]
    _, ledger = bridge._load(run)
    ledger["server"] = original
    bridge._save(path, ledger)


@pytest.mark.parametrize("kwargs", [{"run": "../escape"}, {"wait_s": 121}, {"wait_s": float("nan")}])
def test_invalid_inputs(fake, kwargs):
    assert not fake[0].call("status", **kwargs)["ok"]


def test_registration_one_group(fake):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("test")
    register_robosim_tools(server, bridge=fake[0])
    tools = asyncio.run(server.list_tools())
    assert [tool.name for tool in tools] == ["robosim"]
    tool = tools[0]
    assert len(tool.description) < 400
    assert all(len(prop.get("description", "")) < 120 for prop in tool.inputSchema["properties"].values())
    assert tool.annotations.destructiveHint and not tool.annotations.readOnlyHint
    assert "update_scene" in tool.inputSchema["properties"]["action"]["enum"]
    for argument in ("push", "duration_s", "steps", "record_fps", "start_s", "end_s", "sim_start_s", "sim_end_s", "shape"):
        assert argument in tool.inputSchema["properties"]
    assert "every" not in tool.inputSchema["properties"]
    flat = " ".join(tool.description.split())
    assert "frozen until update_scene" in flat and "no mass or friction" in flat
    result = asyncio.run(server.call_tool("robosim", {"action": "scene_template"}))
    assert "OBJECT_MASS" in str(result) and "TeeShape" in str(result)


# ---- new behaviour: template, push, timing, mapping, reload, trimming, stop ----

import ast
import socket

from isaac_mcp.robosim import plan, template


def finish(bridge, run, result):
    if (result.get("operation") or {}).get("state") == "running":
        result = bridge.call("status", run=run, wait_s=10)
    return result["operation"]["result"], result


def test_scene_template_shapes_and_contents(fake):
    bridge = fake[0]
    tee = bridge.call("scene_template")
    assert tee["ok"] and tee["shape"] == "tee" and 'SHAPE = "tee"' in tee["scene"]
    box = bridge.call("scene_template", shape="box")
    assert 'SHAPE = "box"' in box["scene"] and 'SHAPE = "tee"' not in box["scene"]
    assert not bridge.call("scene_template", shape="sphere")["ok"]
    ast.parse(tee["scene"])
    for needle in ("class TeeShape", "def push_routine", "def settle_routine", "routines = {", "PUSH = {", "coord_start", "dof_start",
                   "joint_q", "OBJECT_NAME"):
        assert needle in tee["scene"]
    assert "cube" not in tee["scene"]
    assert "PUSH" in tee["parameters"] and "TEE_BAR_HALF" in tee["parameters"]
    assert "MuJoCo-Warp" in tee["note"] and "no mass or friction" in tee["note"]
    with pytest.raises(ValueError):
        template.scene_text("cone")


def test_status_says_no_bridge_and_names_the_engine(fake):
    bridge = fake[0]
    listing = bridge.call("status")
    assert listing["ok"] and listing["bridge_needed"] is False and "needs no Isaac Sim" in listing["note"]
    run = start(fake)["run"]
    status = bridge.call("status", run=run)
    assert status["bridge_needed"] is False and status["engine"]["device"] == "cpu"
    assert "plain MuJoCo" in status["engine"]["physics"] and "does not fit mass or friction" in status["engine"]["scope"]
    assert "MuJoCo-Warp" in plan.engine("cuda:0")["physics"]


def test_start_picks_a_free_port_and_reports_it(fake):
    bridge = fake[0]
    held = socket.socket()
    held.bind(("", 0))
    busy = held.getsockname()[1]
    try:
        assert bridge._free_port() != busy or not 8080 <= busy < 8280
        result = start(fake)
        operation = result["operation"]["result"]
        assert operation["port_requested"] is None and 1024 <= operation["port_chosen"] <= 65535
        assert operation["url"].endswith(str(operation["port_chosen"]))
        assert "all network interfaces" in operation["network"] and "no localhost-only option" in operation["network"]
        assert "frozen" in operation["scene_frozen"] and "update_scene" in operation["scene_frozen"]
    finally:
        held.close()


def test_free_port_skips_a_busy_port(fake, monkeypatch):
    sockets = []
    try:
        first = fake[0]._free_port()
        blocker = socket.socket()
        blocker.bind(("", first))
        sockets.append(blocker)
        assert fake[0]._free_port() != first
    finally:
        for blocker in sockets:
            blocker.close()


def test_explicit_port_is_used(fake):
    bridge, scene, _, _ = fake
    result = bridge.call("start", name="test", scene=str(scene), device="cpu", port=9123, wait_s=10)
    assert result["operation"]["result"]["port_chosen"] == 9123 and result["operation"]["result"]["port_requested"] == 9123
    assert not bridge.call("start", name="x", scene=str(scene), device="cpu", port=80)["ok"]


def test_update_scene_keeps_versions_and_reloads_in_the_same_server(fake, tmp_path):
    bridge, scene, _, _ = fake
    first = start(fake)
    run, pid = first["run"], first["server"]["pid"]
    path = Path(first["run_dir"])
    new = tmp_path / "second.py"
    new.write_text("scene = 'second'\n")
    result, status = finish(bridge, run, bridge.call("update_scene", run=run, scene=str(new), wait_s=10))
    assert result["ok"] and result["version"] == 2 and result["restarted"] is False and result["reloaded"] is True
    assert result["generation"] == {"before": 1, "after": 2} and result["routines"] == ["push", "settle"]
    assert "same process, not restarted" in result["what_happened"] and "older versions are kept" in result["what_happened"]
    assert (path / "scene-v1.py").read_text() == "scene = 'fake'\n"
    assert (path / "scene-v2.py").read_text() == "scene = 'second'\n" == (path / "scene.py").read_text()
    assert status["server"]["pid"] == pid and status["server_alive"]
    ledger = json.loads((path / "run.json").read_text())
    assert [v["version"] for v in ledger["scene_versions"]] == [1, 2] and ledger["scene_versions"][1]["loaded"] is True
    assert ledger["inputs"]["scene"]["sha256"] == hashlib.sha256(new.read_bytes()).hexdigest()
    third = tmp_path / "third.py"
    third.write_text("scene = 'third'\n")
    assert finish(bridge, run, bridge.call("update_scene", run=run, scene=str(third), wait_s=10))[0]["version"] == 3
    assert (path / "scene-v2.py").read_text() == "scene = 'second'\n"
    assert "Scene versions" in Path(complete(bridge, "report", run)["report"]).read_text()


def test_update_scene_failure_keeps_the_old_scene(fake, tmp_path):
    bridge, scene, _, _ = fake
    first = start(fake)
    run, path = first["run"], Path(first["run_dir"])
    bad = tmp_path / "bad.py"
    bad.write_text("scene = 'BREAK'\n")
    result, status = finish(bridge, run, bridge.call("update_scene", run=run, scene=str(bad), wait_s=10))
    assert not result["ok"] and result["loaded"] is False and "build failed" in result["what_happened"]
    assert "Traceback" in result["error"]
    assert (path / "scene.py").read_text() == "scene = 'fake'\n"
    assert (path / "scene-v2.py").read_text() == "scene = 'BREAK'\n"
    ledger = json.loads((path / "run.json").read_text())
    assert ledger["scene_versions"][1]["loaded"] is False and ledger["inputs"]["scene"]["sha256"] == ledger["scene_versions"][0]["sha256"]
    good = tmp_path / "good.py"
    good.write_text("scene = 'good'\n")
    assert finish(bridge, run, bridge.call("update_scene", run=run, scene=str(good), wait_s=10))[0]["ok"]


def test_update_scene_validates_before_touching_the_run(fake, tmp_path):
    bridge = fake[0]
    run = start(fake)["run"]
    assert "absolute .py" in bridge.call("update_scene", run=run)["error"]
    assert "absolute .py" in bridge.call("update_scene", run=run, scene="relative.py")["error"]
    assert "not alive" not in bridge.call("update_scene", run=run, scene=str(tmp_path / "missing.py"))["error"]
    complete(bridge, "stop", run)
    scene = tmp_path / "again.py"
    scene.write_text("scene = 1\n")
    assert "not alive" in bridge.call("update_scene", run=run, scene=str(scene))["error"]


@pytest.mark.parametrize("line", ["sim.state.body_q.assign(q)", "state.body_qd .assign( qd )"])
def test_scene_writing_body_q_is_an_error_naming_the_fix(fake, tmp_path, line):
    bridge, _, _, _ = fake
    bad = tmp_path / "bad_write.py"
    bad.write_text(f"def routine(sim):\n    {line}\n    yield\n")
    result = bridge.call("start", name="test", scene=str(bad), device="cpu")
    assert not result["ok"] and "discarded" in result["error"] and "joint_q" in result["error"] and "push" in result["error"]
    assert not bridge.runs.exists()
    run = start(fake)["run"]
    result = bridge.call("update_scene", run=run, scene=str(bad))
    assert not result["ok"] and "joint_qd" in result["error"]
    ok = tmp_path / "reads_only.py"
    ok.write_text("q = sim.state.body_q.numpy()\n")
    assert finish(bridge, run, bridge.call("update_scene", run=run, scene=str(ok), wait_s=10))[0]["ok"]


def test_rollout_records_what_was_asked_and_returns_the_numbers(fake):
    bridge = fake[0]
    first = start(fake)
    run, path = first["run"], Path(first["run_dir"])
    push = {"waypoints": [[0, 0.1, -0.2], [3, 0.0, -0.05], [6, -0.1, 0.05]], "object_velocity": [0.0, 0.1, 0.0]}
    result = complete(bridge, "rollout", run, take="six", routine="push", push=push, duration_s=6, record_fps=10)
    assert (result["steps_asked"], result["steps"], result["duration_s"]) == (300, 300, 6.0)
    assert (result["control_hz"], result["every"], result["record_fps"], result["video_fps"]) == (50.0, 5, 10.0, 10)
    assert result["frames"] == result["frames_expected"] == 61
    assert result["push"]["waypoints"][1] == [3.0, 0.0, -0.05] and result["push"]["object_velocity"] == [0.0, 0.1, 0.0]
    assert result["push_applied"]["applied"] and result["engine"]["device"] == "cpu" and not result["warnings"]
    assert "board" not in result["status"] and "compare_take" in result["board_note"]
    calls = [json.loads(line)["args"] for line in (path / "calls.jsonl").read_text().splitlines()]
    kinds = [call[0] for call in calls]
    assert kinds.index("eval") < kinds.index("reset") < kinds.index("play")
    rollout_call = [call for call in calls if call[0] == "eval" and call[1].startswith("ROLLOUT")][0]
    literal = ast.literal_eval(rollout_call[1].split("=", 1)[1].strip())
    assert literal == {"steps": 300, "take": "six", "push": result["push"]}
    # steps instead of duration, a different rate, and a duration taken from the push
    by_steps = complete(bridge, "rollout", run, take="steps", steps=120, record_fps=25)
    assert (by_steps["steps"], by_steps["every"], by_steps["record_fps"], by_steps["frames"]) == (120, 2, 25.0, 61)
    by_push = complete(bridge, "rollout", run, take="pushlen", push={"waypoints": [[0, 0, -0.2], [2.5, 0, 0]]})
    assert (by_push["steps"], by_push["duration_s"]) == (125, 2.5)


def test_rollout_warns_when_the_rate_does_not_divide(fake):
    result = complete(fake[0], "rollout", start(fake)["run"], take="odd", duration_s=1, record_fps=12)
    assert result["every"] == 4 and result["record_fps"] == 12.5 and result["record_fps_asked"] == 12.0
    assert "12.5 frames per simulated second" in result["warnings"][0]


def test_rollout_refuses_a_routine_that_ignored_push(fake, tmp_path):
    bridge = fake[0]
    scene = tmp_path / "ignores.py"
    scene.write_text("IGNORE_PUSH = True\n")
    run = bridge.call("start", name="test", scene=str(scene), device="cpu", wait_s=10)["run"]
    result = bridge.call("rollout", run=run, take="ignored", duration_s=1, push={"waypoints": [[0, 0, 0], [1, 0.1, 0]]}, wait_s=10)
    assert not result["ok"]
    failure = result["operation"]["result"]
    assert "did not apply push" in failure["error"] and "ROLLOUT['push']" in failure["error"]
    # without push the same scene is fine
    assert complete(bridge, "rollout", run, take="plain", duration_s=1)["ok"]


@pytest.mark.parametrize("kwargs, message", [
    ({"duration_s": 2, "steps": 100}, "not both"),
    ({"duration_s": -1}, "positive"),
    ({"steps": 0}, "positive integer"),
    ({"duration_s": 1, "record_fps": 500}, "at most the control rate"),
    ({"duration_s": 1000}, "limit of"),
    ({"push": {"waypoints": [[0, 0, 0]]}}, "2 to"),
    ({"push": {"waypoints": [[1, 0, 0], [1, 1, 1]]}}, "strictly increase"),
    ({"push": {"waypoints": [[0, 0, 0], [1, 9, 9]]}}, "within"),
    ({"push": {"speed": 1}}, "unknown keys"),
    ({"push": {}}, "needs waypoints"),
    ({"push": "fast"}, "must be an object"),
    ({"push": {"object_velocity": [1, 2]}}, "[vx, vy, yaw_rate]"),
])
def test_rollout_argument_errors(fake, kwargs, message):
    bridge = fake[0]
    run = start(fake)["run"]
    result = bridge.call("rollout", run=run, take="bad", wait_s=10, **kwargs)
    error = result.get("error") or (result.get("operation") or {}).get("result", {}).get("error") or str(result.get("operation"))
    assert message in error, error


def test_fast_pusher_is_warned(fake):
    result = complete(fake[0], "rollout", start(fake)["run"], take="fast", push={"waypoints": [[0, 0, -0.2], [0.1, 0, 0.2]]})
    assert "passes through" in result["warnings"][0] or "pass through" in result["warnings"][0]


def test_plan_arithmetic():
    six = plan.rollout_plan(50, duration_s=6, record_fps=10)
    assert (six["steps"], six["every"], six["record_fps"], six["frames_expected"], six["duration_s"]) == (300, 5, 10.0, 61, 6.0)
    sixty = plan.rollout_plan(60, duration_s=2, record_fps=10)
    assert (sixty["steps"], sixty["every"], sixty["frames_expected"]) == (120, 6, 21)
    assert plan.rollout_plan(50, steps=7, record_fps=10)["frames_expected"] == 3     # frames at 0 and 5, plus the last state
    assert plan.rollout_plan(50, record_fps=10)["steps"] == 100                      # nothing asked: 2 s
    assert plan.rollout_plan(50, push={"waypoints": [[0, 0, 0], [3, 1, 1]]})["steps"] == 150
    assert plan.rollout_plan(50, duration_s=0.01)["steps"] == 1
    with pytest.raises(ValueError):
        plan.rollout_plan(0, duration_s=1)
    with pytest.raises(ValueError):
        plan.rollout_plan(50, duration_s=1, record_fps=0)


def test_time_mapping_defaults_explicit_and_warnings():
    default = plan.time_mapping([0.0, 3.0, 6.0], 6.0)
    assert default["sim_t"] == {0.0: 0.0, 3.0: 3.0, 6.0: 6.0} and default["sim_seconds_per_clip_second"] == 1.0
    assert default["warnings"] == [] and (default["sim_start_s"], default["sim_end_s"]) == (0.0, 6.0)
    shifted = plan.time_mapping([0.0, 3.0, 6.0], 10.0, sim_start=2.0)
    assert shifted["sim_t"] == {0.0: 2.0, 3.0: 5.0, 6.0: 8.0}
    squeezed = plan.time_mapping([0.0, 3.0, 6.0], 6.0, sim_end=3.0)
    assert squeezed["sim_t"] == {0.0: 0.0, 3.0: 1.5, 6.0: 3.0}
    assert "6 s" in squeezed["warnings"][0] and "3 s" in squeezed["warnings"][0]
    within = plan.time_mapping([0.0, 3.0, 6.0], 6.6, clip_start=0, clip_end=6, sim_start=0, sim_end=6.5)
    assert within["warnings"] == []                              # 8 percent apart: below the 10 percent line
    over = plan.time_mapping([0.0, 3.0, 6.0], 7.0, clip_start=0, clip_end=6, sim_start=0, sim_end=6.7)
    assert len(over["warnings"]) == 1                            # 11.7 percent apart
    windowed = plan.time_mapping([0.0, 3.0, 6.0, 9.0], 6.0, clip_start=3.0, clip_end=9.0)
    assert windowed["sim_t"] == {3.0: 0.0, 6.0: 3.0, 9.0: 6.0} and windowed["anchors_outside_window"] == [0.0]
    for kwargs in ({"sim_end": 7.0}, {"clip_end": 0.0}, {"sim_start": 5.0, "sim_end": 4.0}, {"clip_start": 7.0, "clip_end": 9.0}):
        with pytest.raises(ValueError):
            plan.time_mapping([0.0, 3.0, 6.0], 6.0, **kwargs)
    with pytest.raises(ValueError):
        plan.time_mapping([], 6.0)


def test_compare_take_uses_and_reports_the_explicit_mapping(fake):
    bridge = fake[0]
    first = start(fake)
    run, path = first["run"], Path(first["run_dir"])
    complete(bridge, "reference", run, video=str(fake[2]), at="0:start,3:middle,6:end")
    complete(bridge, "rollout", run, take="t6", duration_s=6, push={"waypoints": [[0, 0, -0.2], [6, 0, 0]]})
    result = complete(bridge, "compare_take", run, take="t6", keys=["block=red"], start_s=0, end_s=6, sim_start_s=1, sim_end_s=4)
    mapping = result["time_mapping"]
    assert (mapping["clip_start_s"], mapping["clip_end_s"], mapping["sim_start_s"], mapping["sim_end_s"]) == (0, 6, 1, 4)
    assert mapping["sim_seconds_per_clip_second"] == 0.5 and mapping["take_duration_s"] == 6.0
    assert [(a["clip_t"], a["sim_t_requested"], a["sim_t_used"]) for a in mapping["anchors"]] == [(0.0, 1.0, 1.0), (3.0, 2.5, 2.5), (6.0, 4.0, 4.0)]
    assert len(result["warnings"]) == 1 and "0.5 simulated s per clip s" in result["warnings"][0]
    mapped = json.loads(Path(mapping["timeline"]).read_text())
    assert [a["sim_t"] for a in mapped["anchors"]] == [1.0, 2.5, 4.0]
    # default mapping: same duration, from zero, no warning
    plain = complete(bridge, "compare_take", run, take="t6", keys=["block=red"])
    assert plain["warnings"] == [] and plain["time_mapping"]["sim_end_s"] == 6.0
    # a window past the take is refused with the take's length
    bad = bridge.call("compare_take", run=run, take="t6", keys=["block=red"], sim_end_s=9, wait_s=10)
    assert "lasts 6 s" in bad["operation"]["result"]["error"]
    missing = bridge.call("compare_take", run=run, take="nope", keys=["block=red"], wait_s=10)
    assert "No readable take" in missing["operation"]["result"]["error"]


def test_compare_take_reports_centroid_displacement_and_area_ratio(fake):
    bridge = fake[0]
    run = start(fake)["run"]
    complete(bridge, "reference", run, video=str(fake[2]), at="0:start,3:middle,6:end")
    complete(bridge, "rollout", run, take="d", duration_s=6)
    result = complete(bridge, "compare_take", run, take="d", keys=["block=red"])
    moved = result["displacement_px"]["block"]
    assert moved["clip_px"] == [-50.0, -60.0] and moved["sim_px"] == [-40.0, -40.0]
    assert moved["difference_px"] == [10.0, 20.0] and moved["same_sign_x"] and moved["same_sign_y"]
    assert moved["magnitude_ratio"] == round(math.hypot(40, 40) / math.hypot(50, 60), 3)
    first = result["anchors"][0]["objects"]["block"]
    assert first["area_ratio"] == 1.1 and first["linear_scale_ratio"] == 1.049 and "size_ratio" not in first
    assert first["width_ratio"] == 1.1 and first["height_ratio"] == 0.9
    assert "cube" not in json.dumps(result) and "does not fit mass or friction" in result["scope"]
    single = complete(bridge, "compare", run, keys=["block=red"])
    assert single["objects"]["block"]["area_ratio"] == 1.1 and "size_ratio" not in single["objects"]["block"]
    assert "square root of the area ratio" in single["ratio_note"]


def test_plan_ratios_displacement_and_validation_helpers():
    reading = {"photo": {"pixels": 1865, "centre": [1, 1]}, "sim": {"pixels": 3624, "centre": [2, 2]}, "size_ratio": 1.394,
               "width_ratio": 0.967, "height_ratio": 0.913, "iou": 0.3}
    out = plan.relabel_ratios(reading)
    assert out["area_ratio"] == round(3624 / 1865, 3) == 1.943 and out["linear_scale_ratio"] == 1.394 and "size_ratio" not in out
    assert plan.relabel_ratios({"photo": None, "sim": None, "size_ratio": None})["area_ratio"] is None
    assert plan.relabel_ratios(None) is None
    first = {"objects": {"b": {"photo": {"centre": [10, 10]}, "sim": {"centre": [5, 5]}}}}
    last = {"objects": {"b": {"photo": {"centre": [0, 20]}, "sim": {"centre": [5, 5]}}}}
    still = plan.centroid_displacement(first, last, "b")
    assert still["clip_px"] == [-10, 10] and still["sim_px"] == [0, 0] and still["same_sign_x"] is False and still["magnitude_ratio"] == 0.0
    assert plan.centroid_displacement(first, last, "missing")["clip_px"] is None
    clean, notes = plan.validate_push({"waypoints": [[0, 0, 0], [10, 0.1, 0]]})
    assert clean == {"waypoints": [[0.0, 0.0, 0.0], [10.0, 0.1, 0.0]], "object_velocity": None} and notes == []
    only_velocity, notes = plan.validate_push({"object_velocity": [0.5, 0, 0]})
    assert only_velocity["object_velocity"] == [0.5, 0.0, 0.0] and "stays off the table" in notes[0]
    for bad in ({"waypoints": [[0, 0, float("nan")], [1, 0, 0]]}, {"waypoints": [[0, True, 0], [1, 0, 0]]}):
        with pytest.raises(ValueError):
            plan.validate_push(bad)
    assert plan.scene_problems("q = sim.state.body_q.numpy()\nq[1][0] = 1\n") == []
    assert len(plan.scene_problems("sim.state.body_qd.assign(x)")) == 1


def test_reference_window(fake):
    bridge = fake[0]
    first = start(fake)
    run, path = first["run"], Path(first["run_dir"])
    result = complete(bridge, "reference", run, video=str(fake[2]), start_s=1, end_s=5)
    assert result["window"] == {"start_s": 1, "end_s": 5} and "contact sheet shows the whole clip" in result["window_note"]
    assert [a["t"] for a in result["anchors"]] == [1.0, 3.0, 5.0] and [a["label"] for a in result["anchors"]] == ["start", "middle", "end"]
    calls = [json.loads(line)["args"] for line in (path / "calls.jsonl").read_text().splitlines()]
    assert [call for call in calls if call[0] == "ref"][0][3:5] == ["--at", "1:start,3:middle,5:end"]
    assert json.loads((path / "run.json").read_text())["reference"]["start_s"] == 1
    explicit = complete(bridge, "reference", run, video=str(fake[2]), start_s=2, at="2:a,4:b")
    assert explicit["window"] == {"start_s": 2, "end_s": 12.0}
    only_start = complete(bridge, "reference", run, video=str(fake[2]), start_s=2)
    assert only_start["anchors"][0]["t"] == 2.0
    beyond = bridge.call("reference", run=run, video=str(fake[2]), start_s=1, end_s=20, wait_s=10)
    assert "beyond the clip's duration" in beyond["operation"]["result"]["error"]
    assert "greater than start_s" in bridge.call("reference", run=run, video=str(fake[2]), start_s=5, end_s=5)["error"]
    assert "zero or more" in bridge.call("reference", run=run, video=str(fake[2]), start_s=-1)["error"]


def test_stop_waits_a_bounded_time_and_reports_the_final_state(fake, monkeypatch):
    from isaac_mcp.robosim import bridge as bridge_module

    bridge = fake[0]
    first = start(fake)
    run, path, pid = first["run"], Path(first["run_dir"]), first["server"]["pid"]
    monkeypatch.setattr(bridge_module, "STOP_WAIT_S", 0.4)
    (path / "no-quit").write_text("")
    result = bridge.call("stop", run=run, wait_s=10)["operation"]["result"]
    assert not result["ok"] and result["stopped"] is False and "still alive after 0.4 s" in result["final_state"]
    assert "force-killed" in result["error"] and result["waited_s"] >= 0.4
    (path / "no-quit").unlink()
    monkeypatch.setattr(bridge_module, "STOP_WAIT_S", 15.0)
    done = complete(bridge, "stop", run)
    assert done["stopped"] and done["final_state"] == f"process {pid} has exited" and done["waited_s"] < 15
    again = complete(bridge, "stop", run)
    assert again["stopped"] is False and again["final_state"] == "not running"


def test_failed_scene_load_points_at_update_scene(fake, tmp_path):
    bridge = fake[0]
    scene = tmp_path / "broken.py"
    scene.write_text("scene = 'BREAK'\n")
    result = bridge.call("start", name="test", scene=str(scene), device="cpu", wait_s=10)
    assert not result["ok"] and "update_scene" in result["operation"]["result"]["error"]
    ledger = json.loads((Path(result["run_dir"]) / "run.json").read_text())
    assert ledger["scene_versions"][0]["loaded"] is False
    good = tmp_path / "fixed.py"
    good.write_text("scene = 'ok'\n")
    assert finish(bridge, result["run"], bridge.call("update_scene", run=result["run"], scene=str(good), wait_s=10))[0]["ok"]


def test_listed_description_and_arguments_stay_short(fake):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("test")
    register_robosim_tools(server, bridge=fake[0])
    tool = asyncio.run(server.list_tools())[0]
    assert len(tool.description) < 400
    longest = max(len(prop.get("description", "")) for prop in tool.inputSchema["properties"].values())
    assert longest < 120, longest

"""No RSS, network or GPU required: exercise its executable boundary."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest

from isaac_mcp.robosim import PIN, RoboSimBridge, register_robosim_tools


STUB = r'''
import json, os, pathlib, signal, subprocess, sys, time
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
reply = {"ok": True}
if action == "up":
    proc = subprocess.Popen([sys.executable, __file__, "-m", "robosimstudio_real2sim_agent.serve",
                             "--run-dir", str(run)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    write(run / "serve.pid", str(proc.pid))
    time.sleep(0.05)
    reply.update(pid=proc.pid, url="http://localhost:8080")
elif action == "quit":
    os.kill(int((run / "serve.pid").read_text()), signal.SIGTERM)
elif action == "ref":
    out = pathlib.Path(option("--out"))
    reply.update(frames=[write(out / "start.png")], contact=write(out / "contact.jpg"),
                 timeline=write(out / "timeline.json", "{}"))
elif action == "snapshot":
    reply["image"] = write(option("--out"))
elif action == "compare":
    metrics = {"iou": 0.5, "offset_px": [2, 3], "width_ratio": 1.1, "height_ratio": 0.9, "size_ratio": 1.05}
    if "--take" in args:
        reply.update(anchors=[metrics], strip=write(run / "strip.png"))
    else:
        reply.update(objects={"cube": metrics}, beside=write(option("--out")))
elif action == "play":
    write(run / "pending.json", json.dumps({"take": option("--record"), "at": time.time()}))
elif action == "status":
    reply.update(playing=0, recording="", last_take="", last_error="", names=["cube", "table"], cameras=["camera"])
    pending = run / "pending.json"
    if pending.exists():
        data = json.loads(pending.read_text())
        take = data["take"]
        if time.time() - data["at"] < 0.2:
            reply.update(playing=1, recording=take)
        else:
            write(run / (take + ".mp4"))
            write(run / (take + ".json"), "{}")
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
    assert result["ok"], result
    assert result["operation"]["state"] == "completed", result
    return result["operation"]["result"]


@pytest.mark.parametrize("action", ["status", "scene_template", "start", "reference", "snapshot", "compare", "rollout", "compare_take", "stop", "report"])
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
    run = result["run"]
    path = Path(result["run_dir"])
    assert path.parent == bridge.runs and path.name.endswith("-test")
    assert (path / "scene.py").read_bytes() == scene.read_bytes()
    complete(bridge, "reference", run, video=str(video), at="0:start,1:end", width=100)
    complete(bridge, "snapshot", run, camera="camera", size="80x60")
    compare = complete(bridge, "compare", run, keys=["cube=red"], camera="camera", size="80x60")
    assert compare["metrics"]["cube"]["iou"] == 0.5
    take = complete(bridge, "rollout", run, routine="routine", take="take1", every=2, camera="camera")
    assert Path(take["mp4"]).is_file() and Path(take["json"]).is_file()
    complete(bridge, "compare_take", run, take="take1", keys=["cube=red"])
    report = complete(bridge, "report", run)
    assert "0.5" in Path(report["report"]).read_text()
    assert "Not inverse-physics" in Path(report["report"]).read_text()
    assert bridge.call("status")["live_servers"]
    assert complete(bridge, "stop", run)["stopped"]
    assert not bridge.call("status", run=run)["server_alive"]
    ledger = json.loads((path / "run.json").read_text())
    assert ledger["rss_commit"].startswith(PIN)
    assert ledger["inputs"]["scene"]["sha256"]
    assert ledger["device"] == "cpu"
    calls = [json.loads(line) for line in (path / "calls.jsonl").read_text().splitlines()]
    by_action = {call["args"][0]: call["args"] for call in calls}
    assert by_action["up"] == ["up", "--scene", str(path / "scene.py"), "--port", "8080", "--hold", "--no-watch", "--json"]
    assert by_action["ref"] == ["ref", "frames", str(path / "reference-2.mp4"), "--at", "0:start,1:end", "--width", "100", "--out", str(path / "ref"), "--json"]
    assert by_action["snapshot"] == ["snapshot", "--camera", "camera", "--size", "80x60", "--out", str(path / "snapshot-3.png"), "--json"]
    assert ["compare", "--camera", "camera", "--key", "cube=red", "--size", "80x60", "--out", str(path / "comparison-4.png"), "--json"] in [call["args"] for call in calls]
    assert by_action["play"] == ["play", "routine", "--record", "take1", "--every", "2", "--camera", "camera", "--json"]
    assert by_action["compare"] == ["compare", "--take", "take1", "--key", "cube=red", "--timeline", str(path / "ref/timeline.json"), "--json"]
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
    result = asyncio.run(server.call_tool("robosim", {"action": "scene_template"}))
    assert "OBJECT_MASS" in str(result)

"""Tests for the Kit bridge guard in Isaac Lab job management.

Refuses to launch an Isaac Lab job (train/eval/play) when a Kit bridge (a running
Isaac Sim app) is reachable, unless allow_concurrent_gpu=True.
"""

import contextlib
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isaac_mcp.isaaclab import jobs

REAL_PROBE = jobs._probe_kit_bridges

LIVE_CMD = [sys.executable, "-c", "import time; time.sleep(60)"]


def make_fake_python(tmp_path: Path) -> Path:
    p = tmp_path / "fake_python.py"
    p.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, time\n"
        "mode = os.environ.get('FAKE_MODE', 'ok')\n"
        "if mode == 'sleep':\n"
        "    time.sleep(3600)\n"
        "elif mode == 'fail':\n"
        "    print('boom', file=sys.stderr)\n"
        "    sys.exit(7)\n"
        "else:\n"
        "    print('line1')\n"
        "    print('line2')\n"
        "    sys.exit(0)\n"
    )
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


@pytest.fixture(autouse=True)
def isolated_jobs(tmp_path, monkeypatch):
    jd = tmp_path / "jobs"
    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(jd))
    jobs._active.clear()
    yield
    jobs._active.clear()


def write_running_record(job_id: str, pid: int, command: list[str]) -> Path:
    d = jobs.jobs_dir() / job_id
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "id": job_id,
        "job_id": job_id,
        "kind": "train",
        "task": "Isaac-Reach-Franka-v0",
        "command": command,
        "pid": pid,
        "started_at": time.time() - 5,
        "finished_at": None,
        "status": "running",
        "exit_code": None,
        "log_dir": str(d),
        "run_dir": str(d),
    }
    jobs._write_job(d, data)
    return d


def cmdline(pid: int) -> bytes:
    return Path(f"/proc/{int(pid)}/cmdline").read_bytes()


@contextlib.contextmanager
def live_job():
    proc = subprocess.Popen(LIVE_CMD)
    try:
        deadline = time.time() + 10
        while time.time() < deadline and b"-c" not in cmdline(proc.pid).split(b"\0"):
            time.sleep(0.02)
        job_id = "20261029-120000-guard"
        d = write_running_record(job_id, proc.pid, LIVE_CMD)
        assert jobs._running_job() is not None
        yield job_id, d, proc.pid
    finally:
        proc.kill()
        proc.wait()


def test_probe_listening_socket(monkeypatch):
    monkeypatch.setattr(jobs, "_probe_kit_bridges", REAL_PROBE)
    from isaac_mcp import client

    monkeypatch.setattr(client, "discover_bridges", lambda: [])

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        monkeypatch.setenv("UR5E_DT_PORT", str(port))
        monkeypatch.delenv("ISAAC_MCP_PORT", raising=False)
        bridges = jobs._probe_kit_bridges()
        assert bridges == [{"host": "127.0.0.1", "port": port}]
    finally:
        sock.close()


def test_probe_env_port_zero(monkeypatch):
    monkeypatch.setattr(jobs, "_probe_kit_bridges", REAL_PROBE)
    from isaac_mcp import client

    monkeypatch.setattr(client, "discover_bridges", lambda: [])
    monkeypatch.setenv("UR5E_DT_PORT", "0")
    monkeypatch.delenv("ISAAC_MCP_PORT", raising=False)
    assert jobs._probe_kit_bridges() == []


def test_probe_closed_port(monkeypatch):
    monkeypatch.setattr(jobs, "_probe_kit_bridges", REAL_PROBE)
    from isaac_mcp import client

    monkeypatch.setattr(client, "discover_bridges", lambda: [])
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    monkeypatch.setenv("UR5E_DT_PORT", str(port))
    monkeypatch.delenv("ISAAC_MCP_PORT", raising=False)
    assert jobs._probe_kit_bridges() == []


def test_probe_discover_bridges(monkeypatch):
    monkeypatch.setattr(jobs, "_probe_kit_bridges", REAL_PROBE)
    from isaac_mcp import client

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        discovered = [{"pid": 1, "port": port, "host": "127.0.0.1"}]
        monkeypatch.setattr(client, "discover_bridges", lambda: discovered)
        monkeypatch.setenv("UR5E_DT_PORT", "0")
        monkeypatch.delenv("ISAAC_MCP_PORT", raising=False)
        bridges = jobs._probe_kit_bridges()
        assert bridges == [{"host": "127.0.0.1", "port": port}]
    finally:
        sock.close()


def test_kit_bridge_guard(monkeypatch):
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: [])
    assert jobs._kit_bridge_guard() is None

    fake_bridges = [{"host": "127.0.0.1", "port": 8766}]
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: fake_bridges)
    res = jobs._kit_bridge_guard()
    assert res is not None
    assert "127.0.0.1:8766" in res["error"]
    assert res["kit_bridges"] == fake_bridges
    assert "allow_concurrent_gpu" in res["hint"]


def test_guard_blocks_train_eval_play_when_kit_bridge_present(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    rl_dir = tmp_path / "scripts" / "reinforcement_learning"
    rl_dir.mkdir(parents=True, exist_ok=True)
    (rl_dir / "train.py").touch()
    (rl_dir / "play.py").touch()
    ckpt = tmp_path / "model.pth"
    ckpt.write_text("x")
    monkeypatch.setenv("FAKE_MODE", "ok")

    fake_bridge = [{"host": "127.0.0.1", "port": 8766}]
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: fake_bridge)

    def no_spawn(*args, **kwargs):
        raise AssertionError("subprocess.Popen called while Kit bridge is running")

    monkeypatch.setattr(jobs.subprocess, "Popen", no_spawn)

    # train
    res_train = jobs.train(python=str(fake_py), root=str(tmp_path), task="Isaac-Reach-Franka-v0")
    assert "error" in res_train
    assert "127.0.0.1:8766" in res_train["error"]
    assert res_train["kit_bridges"] == fake_bridge
    assert "allow_concurrent_gpu" in res_train["hint"]

    # eval_
    res_eval = jobs.eval_(
        python=str(fake_py),
        root=str(tmp_path),
        task="Isaac-Reach-Franka-v0",
        checkpoint=str(ckpt),
    )
    assert "error" in res_eval
    assert "127.0.0.1:8766" in res_eval["error"]
    assert res_eval["kit_bridges"] == fake_bridge
    assert "allow_concurrent_gpu" in res_eval["hint"]

    # play
    res_play = jobs.play(
        python=str(fake_py),
        root=str(tmp_path),
        task="Isaac-Reach-Franka-v0",
        checkpoint=str(ckpt),
    )
    assert "error" in res_play
    assert "127.0.0.1:8766" in res_play["error"]
    assert res_play["kit_bridges"] == fake_bridge
    assert "allow_concurrent_gpu" in res_play["hint"]

    assert list(jobs.jobs_dir().iterdir()) == []
    assert jobs._active == {}


def test_allow_concurrent_gpu_starts_job(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    rl_dir = tmp_path / "scripts" / "reinforcement_learning"
    rl_dir.mkdir(parents=True, exist_ok=True)
    (rl_dir / "train.py").touch()
    monkeypatch.setenv("FAKE_MODE", "sleep")

    fake_bridge = [{"host": "127.0.0.1", "port": 8766}]
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: fake_bridge)

    res = jobs.train(
        python=str(fake_py),
        root=str(tmp_path),
        task="Isaac-Reach-Franka-v0",
        allow_concurrent_gpu=True,
    )
    assert "error" not in res
    assert "job_id" in res
    jid = res["job_id"]
    try:
        out = jobs.runs_cancel(jid, grace_s=1)
        assert out["state"] == "cancelled"
    finally:
        with jobs._jobs_lock:
            proc = jobs._active.pop(jid, None)
        if proc is not None:
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass


def test_allow_concurrent_alone_does_not_bypass_kit_guard(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    rl_dir = tmp_path / "scripts" / "reinforcement_learning"
    rl_dir.mkdir(parents=True, exist_ok=True)
    (rl_dir / "train.py").touch()
    monkeypatch.setenv("FAKE_MODE", "ok")

    fake_bridge = [{"host": "127.0.0.1", "port": 8766}]
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: fake_bridge)

    def no_spawn(*args, **kwargs):
        raise AssertionError("subprocess.Popen called while Kit guard should block")

    monkeypatch.setattr(jobs.subprocess, "Popen", no_spawn)

    res = jobs.train(
        python=str(fake_py),
        root=str(tmp_path),
        task="Isaac-Reach-Franka-v0",
        allow_concurrent=True,
    )
    assert "error" in res
    assert "Kit bridge" in res["error"]
    assert res["kit_bridges"] == fake_bridge
    assert "allow_concurrent_gpu" in res["hint"]
    assert list(jobs.jobs_dir().iterdir()) == []
    assert jobs._active == {}


def test_order_concurrency_guard_runs_before_kit_guard(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    rl_dir = tmp_path / "scripts" / "reinforcement_learning"
    rl_dir.mkdir(parents=True, exist_ok=True)
    (rl_dir / "train.py").touch()
    monkeypatch.setenv("FAKE_MODE", "ok")

    fake_bridge = [{"host": "127.0.0.1", "port": 8766}]
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: fake_bridge)

    with live_job() as (job_id, _job_dir, pid):
        def no_spawn(*args, **kwargs):
            raise AssertionError("subprocess.Popen called while another job is running")

        monkeypatch.setattr(jobs.subprocess, "Popen", no_spawn)

        res = jobs.train(
            python=str(fake_py),
            root=str(tmp_path),
            task="Isaac-Reach-Franka-v0",
        )
        assert "error" in res
        assert res["running_job_id"] == job_id
        assert res["running_pid"] == pid
        assert "another Isaac Lab job is already running" in res["error"]
        assert "kit_bridges" not in res
        assert "allow_concurrent" in res["hint"]
        assert "allow_concurrent_gpu" not in res["hint"]


def test_loopback_aliases_are_listed_once(monkeypatch):
    from isaac_mcp import client

    monkeypatch.setattr(client, "discover_bridges", lambda *a, **k: [{"host": "127.0.0.1", "port": 8766}])
    monkeypatch.setenv(client.TWIN_PORT_ENV, "8766")
    monkeypatch.setenv(client.HOST_ENV, "localhost")
    import socket

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: type("S", (), {"close": lambda self: None})())
    monkeypatch.setattr(jobs, "_probe_kit_bridges", REAL_PROBE)
    found = jobs._probe_kit_bridges()
    assert [(b["port"]) for b in found] == [8766]

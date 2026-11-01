"""Parallel launch policy and process ownership checks without starting Kit."""

import json
import errno
import os
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

from isaac_mcp import sim_sessions as sim


@pytest.fixture
def setup(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    run = root / "run.sh"
    run.write_text("#!/bin/sh\nexit 0\n")
    run.chmod(0o755)
    env = {"ISAAC_MCP_SIM_ROOT": str(root), "ISAAC_MCP_SIM_SESSIONS_DIR": str(tmp_path / "sessions"),
           "UR5E_DT_PYTHON": "/configured/sim-python"}
    monkeypatch.setattr(sim.client, "discover_bridges", lambda: [])
    monkeypatch.setattr(sim, "_boot_id", lambda: "test-boot")
    monkeypatch.setattr(sim, "_lab_pids", lambda env: set())
    monkeypatch.setattr(sim, "_check_port", lambda port: None)
    monkeypatch.setattr(sim, "_resources", lambda: {"gpu_total_mib": 16000, "gpu_free_mib": 12000,
                                                     "ram_available_mib": 24000})
    return env


def test_launch_checks_port_domain_limit_and_resources(setup, monkeypatch):
    monkeypatch.setattr(sim.client, "discover_bridges", lambda: [{"pid": 10, "port": 8766}])
    monkeypatch.setattr(sim, "_domain_of", lambda pid: 7)
    with pytest.raises(ValueError, match="domain 7"):
        sim._launch(setup, 18766, 7, False, False)
    with pytest.raises(ValueError, match="port must"):
        sim._launch(setup, 80, 9, False, False)
    with pytest.raises(ValueError, match="ros_domain"):
        sim._launch(setup, 18766, 233, False, False)
    setup["ISAAC_MCP_SIM_MAX_INSTANCES"] = "1"
    with pytest.raises(RuntimeError, match="already active"):
        sim._launch(setup, 18766, 9, False, False)
    setup["ISAAC_MCP_SIM_MAX_INSTANCES"] = "2"
    monkeypatch.setattr(sim, "_resources", lambda: {"gpu_total_mib": 16000, "gpu_free_mib": 100,
                                                     "ram_available_mib": 24000})
    with pytest.raises(RuntimeError, match="headroom"):
        sim._launch(setup, 18766, 9, False, False)


def test_known_lab_job_counts_toward_limit(setup, monkeypatch):
    monkeypatch.setattr(sim.client, "discover_bridges", lambda: [{"pid": 10, "port": 8766}])
    monkeypatch.setattr(sim, "_lab_pids", lambda env: {20})
    with pytest.raises(RuntimeError, match="2 known simulator/Lab"):
        sim._launch(setup, 18766, 9, False, False)


def test_launch_records_identity_and_isolates_environment(setup, monkeypatch):
    captured = {}

    def popen(args, **kwargs):
        captured.update(args=args, **kwargs)
        return SimpleNamespace(pid=510, wait=lambda: 0)

    monkeypatch.setattr(sim.subprocess, "Popen", popen)
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    monkeypatch.setenv("PYTHONPATH", "/unrelated/project")
    monkeypatch.setenv("ISAAC_MCP_HOLD_TOKEN", "foreign")
    monkeypatch.setenv("ISAAC_MCP_BIND", "0.0.0.0")
    result = sim._launch(setup, 18766, 197, True, False)
    assert result["state"] == "starting" and result["pid"] == 510
    saved = json.loads((Path(result["log"]).parent / "session.json").read_text())
    assert saved["id"] == result["id"] and saved["start_ticks"] == 12345
    assert "boot_id" not in result and "start_ticks" not in result
    child = captured["env"]
    assert child["ROS_DOMAIN_ID"] == "197" and child["ISAAC_MCP_PORT"] == "18766"
    assert child["ISAAC_MCP_BIND"] == "127.0.0.1" and child["MCP_CLIENT_OUTPUT_DIR"] == result["output_dir"]
    assert child["UR5E_DT_PYTHON"] == "/configured/sim-python"
    assert "PYTHONPATH" not in child and "ISAAC_MCP_HOLD_TOKEN" not in child
    assert "--headless" in captured["args"] and captured["start_new_session"] is True
    assert captured["args"][-1] == f"--/app/userConfigPath={result['user_config']}"


def test_explicit_server_settings_do_not_inherit_an_unselected_sim_interpreter(setup, monkeypatch):
    setup.pop("UR5E_DT_PYTHON")
    monkeypatch.setenv("UR5E_DT_PYTHON", "/other/server/python")
    captured = {}

    def popen(args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(pid=510, wait=lambda: 0)

    monkeypatch.setattr(sim.subprocess, "Popen", popen)
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    sim._launch(setup, 18766, 197, False, False)
    assert "UR5E_DT_PYTHON" not in captured["env"]


def test_failed_identity_check_stops_child_instead_of_waiting_indefinitely(setup, monkeypatch):
    calls = []
    proc = SimpleNamespace(pid=510, terminate=lambda: calls.append("terminate"),
                           wait=lambda timeout: calls.append(("wait", timeout)))
    monkeypatch.setattr(sim.subprocess, "Popen", lambda *args, **kwargs: proc)
    monkeypatch.setattr(sim, "_process_identity", lambda pid: None)
    with pytest.raises(RuntimeError, match="could not record simulator process identity"):
        sim._launch(setup, 18766, 197, False, False)
    assert calls == ["terminate", ("wait", 5)]


def _record(env, session_id="a" * 32):
    path = Path(env["ISAAC_MCP_SIM_SESSIONS_DIR"]) / session_id
    path.mkdir(parents=True)
    data = {"id": session_id, "pid": 510, "start_ticks": 12345, "boot_id": "test-boot",
            "port": 18766, "ros_domain": 197}
    (path / "session.json").write_text(json.dumps(data))
    return data


def test_starting_managed_session_reserves_port_and_domain(setup, monkeypatch):
    _record(setup)
    monkeypatch.setattr(sim, "_alive", lambda data: True)
    with pytest.raises(ValueError, match="port 18766"):
        sim._launch(setup, 18766, 198, False, False)
    with pytest.raises(ValueError, match="domain 197"):
        sim._launch(setup, 18767, 197, False, False)


def test_status_verifies_bridge_pid_and_reports_reused_pid_as_exited(setup, monkeypatch):
    record = _record(setup)
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim.client, "call", lambda *a, **kw: {"pid": 510})
    assert sim._status(setup, record["id"])["state"] == "running"
    monkeypatch.setattr(sim.client, "call", lambda *a, **kw: {"pid": 511})
    assert sim._status(setup, record["id"])["state"] == "port_conflict"
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 99999))
    assert sim._status(setup, record["id"])["state"] == "exited"
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim, "_boot_id", lambda: "new-boot")
    assert sim._status(setup, record["id"])["state"] == "exited"


def test_stop_requires_confirmation_matching_pid_and_process_group(setup, monkeypatch):
    record = _record(setup)
    signals = []
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(sim.os, "pidfd_open", lambda pid, flags: os.open("/dev/null", os.O_RDONLY))
    monkeypatch.setattr(sim.signal, "pidfd_send_signal",
                        lambda fd, sig, info=None, flags=0: signals.append((sig, flags)))
    with pytest.raises(ValueError, match="confirm_discard"):
        sim._stop(setup, record["id"], 510, False)
    with pytest.raises(ValueError, match="expected PID"):
        sim._stop(setup, record["id"], 511, True)
    monkeypatch.setattr(sim.os, "getpgid", lambda pid: 500)
    with pytest.raises(RuntimeError, match="process group"):
        sim._stop(setup, record["id"], 510, True)
    assert signals == []
    monkeypatch.setattr(sim.os, "getpgid", lambda pid: pid)
    assert sim._stop(setup, record["id"], 510, True)["state"] == "stop_requested"
    assert signals == [(signal.SIGINT, sim._PIDFD_SIGNAL_PROCESS_GROUP)]
    assert sim._stop(setup, record["id"], 510, True, force=True)["signal"] == "SIGKILL"
    assert signals[-1] == (signal.SIGKILL, sim._PIDFD_SIGNAL_PROCESS_GROUP)


def test_stop_falls_back_to_pidfd_leader_only_on_older_kernels(setup, monkeypatch):
    record = _record(setup)
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(sim.os, "pidfd_open", lambda pid, flags: os.open("/dev/null", os.O_RDONLY))
    calls = []

    def send(fd, sig, info=None, flags=0):
        calls.append(flags)
        if flags:
            raise OSError(errno.EINVAL, "group scope unavailable")

    monkeypatch.setattr(sim.signal, "pidfd_send_signal", send)
    result = sim._stop(setup, record["id"], 510, True)
    assert result["scope"] == "process" and calls == [sim._PIDFD_SIGNAL_PROCESS_GROUP, 0]


def test_stop_does_not_signal_if_identity_changes_after_opening_pidfd(setup, monkeypatch):
    record = _record(setup)
    states = iter([True, False])
    monkeypatch.setattr(sim, "_alive", lambda data: next(states))
    monkeypatch.setattr(sim.os, "pidfd_open", lambda pid, flags: os.open("/dev/null", os.O_RDONLY))
    signals = []
    monkeypatch.setattr(sim.signal, "pidfd_send_signal", lambda *args: signals.append(args))
    assert sim._stop(setup, record["id"], 510, True)["state"] == "exited"
    assert signals == []


@pytest.mark.parametrize("session_id", ["../bad", "abc", "/etc/passwd", "A" * 32])
def test_session_id_cannot_escape_sessions_directory(setup, session_id):
    with pytest.raises(ValueError, match="session ID"):
        sim._status(setup, session_id)


def test_session_lock_reports_non_linux_support(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "fcntl", None)
    with pytest.raises(RuntimeError, match="require Linux"):
        with sim._launch_lock(tmp_path / "sessions"):
            pass

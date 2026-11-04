"""Parallel launch policy and process ownership checks without starting Kit."""

import json
import errno
import os
import signal
import socket
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from isaac_mcp import sim_sessions as sim

_REAL_CHECK_PORT = sim._check_port   # the setup fixture replaces it


@pytest.fixture
def setup(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    run = root / "run.sh"
    run.write_text("#!/bin/sh\nexit 0\n")
    run.chmod(0o755)
    env = {"ISAAC_MCP_SIM_ROOT": str(root), "ISAAC_MCP_SIM_SESSIONS_DIR": str(tmp_path / "sessions"),
           "UR5E_DT_PYTHON": "/configured/sim-python", "ISAAC_MCP_PYTHON": "/configured/new-python",
           "ISAAC_MCP_WORKSPACE": "/configured/workspace"}
    monkeypatch.setattr(sim.client, "discover_bridges", lambda: [])
    monkeypatch.setattr(sim, "_boot_id", lambda: "test-boot")
    monkeypatch.setattr(sim, "_lab_pids", lambda env: set())
    monkeypatch.setattr(sim, "_check_port", lambda port, reserved=(): None)
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
    with pytest.raises(ValueError, match="Cyclone DDS"):
        sim._launch(setup, 18766, 232, False, False)
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
    monkeypatch.setenv("UR5E_DT_SHOW_PANEL", "1")
    monkeypatch.setenv("ISAAC_MCP_SIM_SESSION_ID", "foreign")
    monkeypatch.setenv("ISAAC_MCP_SIM_SESSION_DIR", "/foreign/session")
    result = sim._launch(setup, 18766, 197, True, False)
    assert result["state"] == "starting" and result["pid"] == 510
    saved = json.loads((Path(result["log"]).parent / "session.json").read_text())
    assert saved["id"] == result["id"] and saved["start_ticks"] == 12345
    assert "boot_id" not in result and "start_ticks" not in result
    child = captured["env"]
    assert child["ROS_DOMAIN_ID"] == "197" and child["ISAAC_MCP_PORT"] == "18766"
    assert child["ISAAC_MCP_BIND"] == "127.0.0.1" and child["MCP_CLIENT_OUTPUT_DIR"] == result["output_dir"]
    assert child["ISAAC_MCP_SIM_SESSION_ID"] == result["id"]
    assert child["ISAAC_MCP_SIM_SESSION_DIR"] == str(Path(result["log"]).parent)
    assert child["UR5E_DT_PYTHON"] == "/configured/sim-python"
    assert child["ISAAC_MCP_PYTHON"] == "/configured/new-python"
    assert child["ISAAC_MCP_WORKSPACE"] == "/configured/workspace"
    assert child["ISAAC_MCP_PHYSICS_BACKEND"] == "physx"
    assert result["physics_backend"] == "physx"
    assert "PYTHONPATH" not in child and "ISAAC_MCP_HOLD_TOKEN" not in child
    assert "UR5E_DT_SHOW_PANEL" not in child
    assert "--headless" in captured["args"] and captured["start_new_session"] is True
    assert "--ur5e-panel" not in captured["args"] and result["show_ur5e_panel_on_start"] is False
    assert captured["args"][-1] == f"--/app/userConfigPath={result['user_config']}"


@pytest.mark.parametrize("show_panel", [False, True])
@pytest.mark.parametrize("motion_bvh", [False, True])
@pytest.mark.parametrize("backend", ["physx", "newton"])
def test_launch_selects_twin_panel_and_sensor_renderer(setup, monkeypatch, show_panel, motion_bvh, backend):
    captured = {}

    def popen(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return SimpleNamespace(pid=510, wait=lambda: 0)

    monkeypatch.setattr(sim.subprocess, "Popen", popen)
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    result = sim._launch(setup, 18766, 197, False, False, show_panel, motion_bvh, backend)
    assert ("--ur5e-panel" in captured["args"]) is show_panel
    assert result["show_ur5e_panel_on_start"] is show_panel
    assert ("--motion-bvh" in captured["args"]) is motion_bvh
    assert result["enable_motion_bvh"] is motion_bvh
    assert result["physics_backend"] == backend
    assert captured["env"]["ISAAC_MCP_PHYSICS_BACKEND"] == backend


def test_unknown_backend_refuses_before_process_or_session_creation(setup):
    with pytest.raises(ValueError, match="physics_backend"):
        sim._launch(setup, 18766, 197, False, False, physics_backend="automatic")
    assert not Path(setup["ISAAC_MCP_SIM_SESSIONS_DIR"]).exists()


def test_relative_sessions_directory_is_shared_with_simulator(setup, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    setup["ISAAC_MCP_SIM_SESSIONS_DIR"] = "relative/sessions"
    captured = {}

    def popen(args, **kwargs):
        captured.update(args=args, **kwargs)
        return SimpleNamespace(pid=510, wait=lambda: 0)

    monkeypatch.setattr(sim.subprocess, "Popen", popen)
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    result = sim._launch(setup, 18766, 197, False, False)
    session_dir = tmp_path / "relative" / "sessions" / result["id"]
    assert Path(result["log"]) == session_dir / "sim.log"
    assert Path(result["output_dir"]) == session_dir / "output"
    assert Path(result["user_config"]) == session_dir / "user.config.json"
    assert (session_dir / "session.json").is_file()
    assert captured["env"]["MCP_CLIENT_OUTPUT_DIR"] == str(session_dir / "output")
    assert captured["args"][-1] == f"--/app/userConfigPath={session_dir / 'user.config.json'}"
    assert captured["cwd"] != str(tmp_path)


def test_explicit_server_settings_do_not_inherit_an_unselected_sim_interpreter(setup, monkeypatch):
    setup.pop("UR5E_DT_PYTHON")
    setup.pop("ISAAC_MCP_PYTHON")
    setup.pop("ISAAC_MCP_WORKSPACE")
    monkeypatch.setenv("UR5E_DT_PYTHON", "/other/server/python")
    monkeypatch.setenv("ISAAC_MCP_PYTHON", "/other/server/python2")
    monkeypatch.setenv("ISAAC_MCP_WORKSPACE", "/other/ws")
    captured = {}

    def popen(args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(pid=510, wait=lambda: 0)

    monkeypatch.setattr(sim.subprocess, "Popen", popen)
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    sim._launch(setup, 18766, 197, False, False)
    assert "UR5E_DT_PYTHON" not in captured["env"]
    assert "ISAAC_MCP_PYTHON" not in captured["env"] and "ISAAC_MCP_WORKSPACE" not in captured["env"]


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
    monkeypatch.setattr(sim.client, "call", lambda *a, **kw: {"pid": 510, "app_ready": True})
    assert sim._status(setup, record["id"])["state"] == "running"
    assert f"use_bridge(port={record['port']})" in sim._status(setup, record["id"])["next"]
    monkeypatch.setattr(sim.client, "call", lambda *a, **kw: {"pid": 510, "app_ready": False})
    assert sim._status(setup, record["id"])["state"] == "starting"
    monkeypatch.setattr(sim.client, "call", lambda *a, **kw: {"pid": 510})
    assert sim._status(setup, record["id"])["state"] == "readiness_unknown"
    monkeypatch.setattr(sim.client, "call", lambda *a, **kw: {"pid": 511})
    assert sim._status(setup, record["id"])["state"] == "port_conflict"
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 99999))
    assert sim._status(setup, record["id"])["state"] == "exited"
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim, "_boot_id", lambda: "new-boot")
    assert sim._status(setup, record["id"])["state"] == "exited"


@pytest.mark.parametrize("value", [None, 0, "true"])
def test_status_does_not_treat_unknown_readiness_as_ready(setup, monkeypatch, value):
    record = _record(setup)
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim.client, "call", lambda *a, **kw: {"pid": 510, "app_ready": value})
    assert sim._status(setup, record["id"])["state"] == "readiness_unknown"


def test_stop_requires_confirmation_matching_pid_and_process_group(setup, monkeypatch):
    record = _record(setup)
    signals = []
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(sim.os, "pidfd_open", lambda pid, flags: os.open("/dev/null", os.O_RDONLY))
    monkeypatch.setattr(sim.signal, "pidfd_send_signal",
                        lambda fd, sig, info=None, flags=0: signals.append((sig, flags)))
    with pytest.raises(ValueError, match="confirm_discard=true") as error:
        sim._stop(setup, record["id"], 510, False)
    assert f"session_id={record['id']!r}" in str(error.value) and "expected_pid=510" in str(error.value)
    with pytest.raises(ValueError, match="force=true"):
        sim._stop(setup, record["id"], 510, False, force=True)
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


# ---- Lab 3 run 8: sim__launch refused a free port after sim__stop --------------------------------

TIME_WAIT, FIN_WAIT2, LISTEN = "06", "05", "0A"


def _plain_bind_refuses(port):
    """The check before the fix: a bind with no SO_REUSEADDR."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            assert exc.errno == errno.EADDRINUSE
            return True
    return False


def _tcp_states(port):
    rows = Path("/proc/net/tcp").read_text().splitlines()[1:]
    return {row.split()[3] for row in rows if int(row.split()[1].split(":")[1], 16) == port}


def _leftover_connection(reuse=True):
    """A served connection the server closed first, as when a bridge process exits. The bridge's
    listener sets SO_REUSEADDR (reuse=True), and accepted sockets inherit it. Returns the port and
    the still-open client socket."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if reuse:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        peer = socket.create_connection(("127.0.0.1", port), timeout=2)
        accepted, _ = listener.accept()
        accepted.close()
    assert peer.recv(1) == b""   # the client has seen the server's FIN
    return port, peer


def _wait_for_state(port, state):
    deadline = time.monotonic() + 2
    while state not in _tcp_states(port) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert state in _tcp_states(port), f"the test must really reach TCP state {state} on the server port"


def _time_wait_port(reuse=True):
    port, peer = _leftover_connection(reuse)
    peer.close()
    _wait_for_state(port, TIME_WAIT)
    return port


def test_time_wait_failed_the_old_check_and_passes_the_new_one():
    port = _time_wait_port()
    assert LISTEN not in _tcp_states(port)
    assert _plain_bind_refuses(port), "the reported failure: nothing listens, yet the old check refused"
    sim._check_port(port)


def test_a_connection_the_client_has_not_closed_yet_does_not_block_the_port():
    # The host keeps a persistent socket to its bridge, so after sim__stop the server side can sit
    # in FIN_WAIT2 until the host next uses it.
    port, peer = _leftover_connection()
    with peer:
        _wait_for_state(port, FIN_WAIT2)
        assert _plain_bind_refuses(port)
        sim._check_port(port)




@pytest.mark.parametrize("address", ["127.0.0.1", "0.0.0.0"])
@pytest.mark.parametrize("reuse", [False, True])
def test_a_live_listener_is_still_refused_and_a_free_port_is_suggested(address, reuse):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if reuse:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((address, 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with pytest.raises(ValueError, match=f"bridge port {port} is already in use or cannot be bound") as caught:
            sim._check_port(port)
        message = str(caught.value)
        suggested = int(message.split("retry with port=")[1].rstrip("."))
        assert suggested != port
        assert sim._port_bind_error(suggested) is None


def test_the_real_bridge_can_bind_a_port_in_time_wait():
    # The check must not pass a port the launch then cannot use.
    import asyncio
    import threading

    from isaac_mcp.client import call
    from test_bridge import make_bridge

    port = _time_wait_port()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    bridge = make_bridge(loop)
    try:
        bridge.start(port)
        assert call("status", host="127.0.0.1", port=port, timeout=5)["port"] == port
    finally:
        bridge.stop()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()


def test_time_wait_from_a_listener_without_reuseaddr_stays_refused_and_says_why():
    # Not the bridge's case (it sets SO_REUSEADDR). The bridge could not bind this port either, so
    # refusing is correct, and the message must not claim a process is certainly holding it.
    port = _time_wait_port(reuse=False)
    with pytest.raises(ValueError, match="closed without SO_REUSEADDR"):
        sim._check_port(port)


def test_suggested_port_skips_reserved_ports_and_stays_in_range(monkeypatch):
    monkeypatch.setattr(sim, "_port_bind_error", lambda port: None)
    assert "port=40003" in sim._next_free_port_hint(40000, {40001, 40002})
    assert "port=65535" in sim._next_free_port_hint(65534)
    assert "No free port" in sim._next_free_port_hint(65535)
    monkeypatch.setattr(sim, "_port_bind_error", lambda port: OSError(errno.EADDRINUSE, "in use"))
    assert "No free port found in 40001-40064" in sim._next_free_port_hint(40000)


def test_launch_accepts_a_time_wait_port(setup, monkeypatch):
    monkeypatch.setattr(sim, "_check_port", _REAL_CHECK_PORT)
    monkeypatch.setattr(sim.subprocess, "Popen", lambda args, **kw: SimpleNamespace(pid=510, wait=lambda: 0))
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    port = _time_wait_port()
    assert sim._launch(setup, port, 9, True, False)["port"] == port


def test_a_reserved_port_refusal_suggests_a_port_too(setup, monkeypatch):
    _record(setup)
    monkeypatch.setattr(sim, "_alive", lambda data: True)
    monkeypatch.setattr(sim, "_port_bind_error", lambda port: None)
    with pytest.raises(ValueError, match="port 18766 is reserved.*retry with port=18767"):
        sim._launch(setup, 18766, 198, False, False)


# ---- the ROS domain refusal says how to proceed ---------------------------------------------------

@pytest.mark.parametrize("holder", ["bridge", "session", "lab"])
def test_ros_domain_refusal_says_how_to_proceed(setup, monkeypatch, holder):
    monkeypatch.setattr(sim, "_domain_of", lambda pid: 0)
    if holder == "bridge":
        monkeypatch.setattr(sim.client, "discover_bridges", lambda: [{"pid": 48526, "port": 8766}])
        named = "bridge PID 48526"
    elif holder == "session":
        data = _record(setup)
        (Path(setup["ISAAC_MCP_SIM_SESSIONS_DIR"]) / data["id"] / "session.json").write_text(
            json.dumps({**data, "ros_domain": 0}))
        monkeypatch.setattr(sim, "_alive", lambda data: True)
        named = "a managed session"
    else:
        monkeypatch.setattr(sim, "_lab_pids", lambda env: {77})
        named = "Lab job PID 77"
    with pytest.raises(ValueError) as caught:
        sim._launch(setup, 18770, 0, False, False)
    message = str(caught.value)
    assert f"ROS domain 0 is already used by {named}" in message
    assert "another ros_domain" in message and "ROS_DOMAIN_ID" in message
    assert "allow_shared_domain=true" in message and "assume domain 0" in message


def test_allow_shared_domain_still_passes_the_guard(setup, monkeypatch):
    monkeypatch.setattr(sim.client, "discover_bridges", lambda: [{"pid": 48526, "port": 8766}])
    monkeypatch.setattr(sim, "_domain_of", lambda pid: 0)
    monkeypatch.setattr(sim.subprocess, "Popen", lambda args, **kw: SimpleNamespace(pid=510, wait=lambda: 0))
    monkeypatch.setattr(sim, "_process_identity", lambda pid: ("S", 12345))
    monkeypatch.setattr(sim.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    assert sim._launch(setup, 18770, 0, True, True)["ros_domain"] == 0


def test_live_sessions_reports_the_session_id(setup, monkeypatch):
    data = _record(setup)
    monkeypatch.setattr(sim, "_alive", lambda data: True)
    assert sim.live_sessions(setup) == [
        {"id": data["id"], "port": data["port"], "pid": data["pid"], "started_at": None}]

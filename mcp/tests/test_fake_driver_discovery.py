"""The fake-driver launcher must discover ROS and never silently use real hardware."""

import ast
import os
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest
import twin_source


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/launch_ur_fake.sh"
SOURCE = twin_source.SOURCE


def driver_handler():
    """Load the real bridge handler without importing Kit."""
    tree = ast.parse(SOURCE.read_text())
    namespace = {"__file__": str(SOURCE), "Dict": Dict, "Any": Any}
    for name in ("_cmd_start_ros_driver", "_last_error_line"):
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                      and n.name == name)
        module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
        exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["_cmd_start_ros_driver"]


def test_a_failed_driver_command_names_its_last_output_line(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=4, stdout="", stderr="\x1b[0;31m  ERR\x1b[0m Docker is not installed or not on PATH\n"))
    result = driver_handler()(None, action="status", backend="ursim")
    assert result["status"] == "error"
    assert "Docker is not installed" in result["message"]


def test_managed_bridge_passes_only_its_driver_paths_and_session_marker(tmp_path, monkeypatch):
    session_id = "a" * 32
    monkeypatch.setenv("ISAAC_MCP_SIM_SESSION_ID", session_id)
    monkeypatch.setenv("ISAAC_MCP_SIM_SESSION_DIR", str(tmp_path))
    monkeypatch.setenv("ROS_DOMAIN_ID", "197")
    monkeypatch.setenv("PYTHONPATH", "/foreign/python")
    monkeypatch.setenv("UR_FAKE_PIDFILE", "/foreign/driver.pid")
    captured = {}

    def run(cmd, **kwargs):
        captured.update(cmd=cmd, **kwargs)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert driver_handler()(None, action="status")["status"] == "success"
    env = captured["env"]
    assert env["ROS_DOMAIN_ID"] == "197" and env["ISAAC_MCP_SIM_SESSION_ID"] == session_id
    assert env["UR_FAKE_PIDFILE"] == str(tmp_path / "ur_fake_driver.pid")
    assert env["UR_FAKE_LOG"] == str(tmp_path / "ur_fake_driver.log")
    assert "PYTHONPATH" not in env


@pytest.mark.parametrize("action", ["up", "down", "status"])
def test_managed_bridge_rejects_shared_ursim_and_incomplete_session(monkeypatch, action):
    monkeypatch.setenv("ISAAC_MCP_SIM_SESSION_ID", "b" * 32)
    monkeypatch.setenv("ISAAC_MCP_SIM_SESSION_DIR", "/session")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("must not start a subprocess"))
    handler = driver_handler()
    result = handler(None, action=action, backend="ursim")
    assert result["status"] == "error" and "shared host resources" in result["message"]
    monkeypatch.delenv("ISAAC_MCP_SIM_SESSION_DIR")
    result = handler(None, action="down", backend="fake")
    assert result["status"] == "error" and "incomplete" in result["message"]


def test_unmanaged_bridge_preserves_legacy_fake_driver_defaults(monkeypatch):
    monkeypatch.delenv("ISAAC_MCP_SIM_SESSION_ID", raising=False)
    monkeypatch.delenv("ISAAC_MCP_SIM_SESSION_DIR", raising=False)
    monkeypatch.setenv("UR_FAKE_PIDFILE", "/foreign/driver.pid")
    captured = {}

    def run(cmd, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert driver_handler()(None, action="status")["status"] == "success"
    assert "UR_FAKE_PIDFILE" not in captured["env"]
    assert "ISAAC_MCP_SIM_SESSION_ID" not in captured["env"]


@pytest.fixture
def fake_ros(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ros = bindir / "ros2"
    ros.write_text("""#!/bin/bash
if [ "$1 $2" = "pkg prefix" ]; then
  [ "${MISSING_DRIVER:-0}" != 1 ] || exit 1
  echo /fake/ros; exit 0
fi
if [ "$1" = launch ] && [ "${4:-}" = --show-args ]; then
  printf "  '%s':\\n" "$ADVERTISED_ARG"; exit 0
fi
if [ "$1" = launch ]; then
  printf '%s\\n' "$@" > "$ARG_LOG"
  if [ "${KEEP_LAUNCH_RUNNING:-0}" = 1 ]; then
    trap 'exit 0' INT TERM
    while :; do sleep 1; done
  fi
  exit 0
fi
if [ "$1 $2" = 'control list_controllers' ]; then
  [ "${HANG_CONTROLLERS:-0}" != 1 ] || exec sleep 30
  echo "joint_state_broadcaster ${BROADCASTER_STATE:-active}"
  [ "${NO_TRAJECTORY:-0}" = 1 ] || echo 'scaled_joint_trajectory_controller active'
  exit 0
fi
exit 1
""")
    ros.chmod(0o755)
    pgrep = bindir / "pgrep"
    pgrep.write_text("#!/bin/sh\n[ \"${OTHER_DRIVER:-0}\" = 1 ]\n")
    pgrep.chmod(0o755)
    setup = tmp_path / "setup.bash"
    setup.write_text("# fake ROS setup\n")
    env = dict(os.environ, HOME=str(tmp_path), PATH=f"{bindir}:/usr/bin:/bin",
               ROS_SETUP=str(setup), UR_FAKE_LOG=str(tmp_path / "driver.log"),
               UR_FAKE_PIDFILE=str(tmp_path / "driver.pid"), ARG_LOG=str(tmp_path / "args"),
               ROS_DOMAIN_ID="197")
    env.pop("ISAAC_MCP_SIM_SESSION_ID", None)
    env.pop("ISAAC_MCP_SIM_SESSION_DIR", None)
    return tmp_path, env


@pytest.mark.parametrize("distro,argument", [
    ("humble", "use_fake_hardware"), ("jazzy", "use_mock_hardware"),
])
def test_launch_uses_advertised_mock_argument(fake_ros, distro, argument):
    root, env = fake_ros
    env.update(ROS_DISTRO=distro, ADVERTISED_ARG=argument)
    result = subprocess.run(["bash", str(SCRIPT), "up"], env=env, text=True,
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    args = (root / "args").read_text().splitlines()
    assert argument + ":=true" in args
    assert "robot_ip:=0.0.0.0" in args


def test_refuses_when_driver_does_not_advertise_mock_argument(fake_ros):
    root, env = fake_ros
    env.update(ROS_DISTRO="jazzy", ADVERTISED_ARG="some_other_argument")
    result = subprocess.run(["bash", str(SCRIPT), "up"], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "refusing to start" in result.stderr
    assert not (root / "args").exists()


def test_refuses_unknown_ros_distribution(fake_ros):
    root, env = fake_ros
    env.update(ROS_DISTRO="unknown", ADVERTISED_ARG="use_mock_hardware")
    result = subprocess.run(["bash", str(SCRIPT), "up"], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "does not support ROS_DISTRO" in result.stderr
    assert not (root / "args").exists()


@pytest.mark.parametrize("action", ["up", "status"])
def test_reports_missing_driver_instead_of_silent_status(fake_ros, action):
    root, env = fake_ros
    env.update(ROS_DISTRO="jazzy", ADVERTISED_ARG="use_mock_hardware", MISSING_DRIVER="1")
    result = subprocess.run(["bash", str(SCRIPT), action], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "ur_robot_driver is not installed" in result.stderr
    assert not (root / "args").exists()


@pytest.mark.parametrize("unready", [{"BROADCASTER_STATE": "inactive"}, {"NO_TRAJECTORY": "1"}])
def test_up_requires_both_controllers_active(fake_ros, unready):
    _, env = fake_ros
    env.update(ROS_DISTRO="jazzy", ADVERTISED_ARG="use_mock_hardware", **unready)
    result = subprocess.run(["bash", str(SCRIPT), "up"], env=env, text=True,
                            capture_output=True, timeout=15)
    assert result.returncode != 0
    assert "READY" not in result.stdout


def test_up_explains_conflict_with_unmanaged_driver(fake_ros):
    _, env = fake_ros
    env.update(ROS_DISTRO="jazzy", ADVERTISED_ARG="use_mock_hardware", OTHER_DRIVER="1")
    result = subprocess.run(["bash", str(SCRIPT), "up"], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "another UR driver is already running" in result.stderr


def test_status_returns_when_controller_manager_does_not_respond(fake_ros):
    _, env = fake_ros
    env.update(ROS_DISTRO="jazzy", HANG_CONTROLLERS="1")
    result = subprocess.run(["bash", str(SCRIPT), "status"], env=env, text=True,
                            capture_output=True, timeout=12)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "driver NOT running" in result.stdout
    assert "controller_manager not reachable" in result.stdout
    assert "no /joint_states" in result.stdout


def test_down_ignores_stale_pid_for_an_unrelated_process(fake_ros):
    root, env = fake_ros
    env.update(ROS_DISTRO="jazzy")
    other = subprocess.Popen(["sleep", "30"])
    try:
        (root / "driver.pid").write_text(str(other.pid))
        result = subprocess.run(["bash", str(SCRIPT), "down"], env=env, text=True,
                                capture_output=True, timeout=5)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "no matching managed driver" in result.stdout
        assert other.poll() is None
    finally:
        other.terminate()
        other.wait(timeout=5)


def test_down_stops_only_its_own_mock_driver(fake_ros):
    root, env = fake_ros
    env.update(ROS_DISTRO="jazzy", ADVERTISED_ARG="use_mock_hardware", KEEP_LAUNCH_RUNNING="1")
    other = subprocess.Popen([str(root / "bin" / "ros2"), "launch", "ur_robot_driver",
                              "ur_control.launch.py", "use_mock_hardware:=true"],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    managed_pid = None
    try:
        up = subprocess.run(["bash", str(SCRIPT), "up"], env=env, text=True,
                            capture_output=True, timeout=15)
        assert up.returncode == 0, up.stdout + up.stderr
        managed_pid = int((root / "driver.pid").read_text())
        down = subprocess.run(["bash", str(SCRIPT), "down"], env=env, text=True,
                              capture_output=True, timeout=25)
        assert down.returncode == 0, down.stdout + down.stderr
        assert other.poll() is None
        status = Path(f"/proc/{managed_pid}/stat")
        assert not status.exists() or status.read_text().split()[2] == "Z"
    finally:
        if managed_pid is not None:
            try:
                os.killpg(managed_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            os.killpg(other.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        other.wait(timeout=5)


def test_other_managed_and_unmanaged_sessions_cannot_stop_a_managed_driver(fake_ros):
    root, env = fake_ros
    session_a = dict(env, ROS_DISTRO="jazzy", ADVERTISED_ARG="use_mock_hardware",
                     KEEP_LAUNCH_RUNNING="1", ISAAC_MCP_SIM_SESSION_ID="a" * 32,
                     UR_FAKE_PIDFILE=str(root / "a.pid"), UR_FAKE_LOG=str(root / "a.log"))
    session_b = dict(session_a, ISAAC_MCP_SIM_SESSION_ID="b" * 32, ROS_DOMAIN_ID="198",
                     UR_FAKE_PIDFILE=str(root / "b.pid"), UR_FAKE_LOG=str(root / "b.log"))
    unmanaged = dict(env, ROS_DISTRO="jazzy", UR_FAKE_PIDFILE=str(root / "unmanaged.pid"))
    managed_pid = None
    try:
        up = subprocess.run(["bash", str(SCRIPT), "up"], env=session_a, text=True,
                            capture_output=True, timeout=15)
        assert up.returncode == 0, up.stdout + up.stderr
        managed_pid = int((root / "a.pid").read_text())

        # Session B has no driver: down must not consult A's record.
        down = subprocess.run(["bash", str(SCRIPT), "down"], env=session_b, text=True,
                              capture_output=True, timeout=5)
        assert down.returncode == 0 and Path(f"/proc/{managed_pid}/cmdline").read_bytes()

        # Even an incorrect or stale record naming A cannot pass B's ownership check.
        for foreign_env, path in ((session_b, root / "b.pid"), (unmanaged, root / "unmanaged.pid")):
            path.write_text(str(managed_pid))
            down = subprocess.run(["bash", str(SCRIPT), "down"], env=foreign_env, text=True,
                                  capture_output=True, timeout=5)
            assert down.returncode == 0, down.stdout + down.stderr
            assert "no matching managed driver" in down.stdout
            assert Path(f"/proc/{managed_pid}/cmdline").read_bytes()

        down = subprocess.run(["bash", str(SCRIPT), "down"], env=session_a, text=True,
                              capture_output=True, timeout=25)
        assert down.returncode == 0, down.stdout + down.stderr
        status = Path(f"/proc/{managed_pid}/stat")
        assert not status.exists() or status.read_text().split()[2] == "Z"
    finally:
        if managed_pid is not None:
            try:
                process_env = Path(f"/proc/{managed_pid}/environ").read_bytes()
                if b"ISAAC_MCP_SIM_SESSION_ID=" + b"a" * 32 + b"\0" in process_env:
                    os.killpg(managed_pid, signal.SIGKILL)
            except (FileNotFoundError, ProcessLookupError):
                pass


def test_named_ros_setup_decides_the_distribution(fake_ros, tmp_path):
    """A RoboStack prefix or other install: ROS_SETUP says its distribution, whatever the OS is."""
    root, env = fake_ros
    prefix = tmp_path / "ros2-humble"
    prefix.mkdir()
    (prefix / "setup.bash").write_text(f'. "{env["ROS_SETUP"]}"\nexport ROS_DISTRO=humble\n')
    env.pop("ROS_DISTRO", None)
    env.update(ROS_SETUP=str(prefix / "setup.bash"), ADVERTISED_ARG="use_fake_hardware")
    result = subprocess.run(["bash", str(SCRIPT), "up"], env=env, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "use_fake_hardware:=true" in (root / "args").read_text().splitlines()


def test_named_ros_setup_that_sets_no_distribution_refuses(fake_ros, tmp_path):
    root, env = fake_ros
    setup = tmp_path / "plain.bash"
    setup.write_text(f'. "{env["ROS_SETUP"]}"\nunset ROS_DISTRO\n')
    env.pop("ROS_DISTRO", None)
    env.update(ROS_SETUP=str(setup), ADVERTISED_ARG="use_mock_hardware")
    result = subprocess.run(["bash", str(SCRIPT), "up"], env=env, text=True, capture_output=True)
    assert result.returncode != 0 and "does not support ROS_DISTRO" in result.stderr
    assert not (root / "args").exists()


def test_activated_environment_needs_no_setup_file(fake_ros):
    """RoboStack activated: ROS_DISTRO set, ros2 on PATH, no /opt/ros, no ROS_SETUP."""
    if Path("/opt/ros/humble/setup.bash").exists():
        pytest.skip("this machine has a real /opt/ros/humble")
    root, env = fake_ros
    env.pop("ROS_SETUP")
    env.update(ROS_DISTRO="humble", ADVERTISED_ARG="use_fake_hardware")
    result = subprocess.run(["bash", str(SCRIPT), "up"], env=env, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "use_fake_hardware:=true" in (root / "args").read_text().splitlines()

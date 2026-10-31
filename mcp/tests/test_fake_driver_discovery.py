"""The fake-driver launcher must discover ROS and never silently use real hardware."""

import os
import signal
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/launch_ur_fake.sh"


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

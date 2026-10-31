"""The fake-driver launcher must discover ROS and never silently use real hardware."""

import os
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
if [ "$1" = launch ]; then printf '%s\\n' "$@" > "$ARG_LOG"; exit 0; fi
if [ "$1 $2" = 'control list_controllers' ]; then
  echo 'joint_state_broadcaster active'; exit 0
fi
exit 1
""")
    ros.chmod(0o755)
    pgrep = bindir / "pgrep"
    pgrep.write_text("#!/bin/sh\nexit 1\n")
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

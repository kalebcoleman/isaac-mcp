"""scripts/odom_logger.py: the pure parts, and its refusal when ROS 2 is not loaded."""

import importlib.util
import math
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "odom_logger.py"
spec = importlib.util.spec_from_file_location("odom_logger", SCRIPT)
odom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(odom)


def test_yaw_from_a_quaternion():
    assert odom.yaw_from_quaternion(0, 0, 0, 1) == pytest.approx(0.0)
    half = math.sqrt(0.5)
    assert odom.yaw_from_quaternion(0, 0, half, half) == pytest.approx(math.pi / 2)  # 90 degrees about Z
    assert odom.yaw_from_quaternion(0, 0, 1, 0) == pytest.approx(math.pi)


def row(t, x, y, yaw):
    return (t, t, x, y, yaw, 0.0, 0.0)


def test_summary_of_a_drive_and_turn():
    rows = [row(0, 0, 0, 0), row(1, 0.3, 0, 0), row(2, 0.3, 0.4, 1.0)]
    s = odom.summarize(rows)
    assert s["samples"] == 3 and s["start"] == (0, 0) and s["end"] == (0.3, 0.4)
    assert s["straight_m"] == pytest.approx(0.5) and s["path_m"] == pytest.approx(0.7) and s["yaw_change_rad"] == pytest.approx(1.0)


def test_a_turn_past_pi_is_not_a_jump():
    rows = [row(0, 0, 0, 3.0), row(1, 0, 0, -3.0)]  # 0.283 rad the short way round, not -6
    assert odom.summarize(rows)["yaw_change_rad"] == pytest.approx(2 * math.pi - 6.0)


def test_no_rows_is_zero_samples():
    assert odom.summarize([]) == {"samples": 0}


def test_without_ros_it_says_how_to_load_it(tmp_path, capsys):
    if "rclpy" in sys.modules or importlib.util.find_spec("rclpy"):
        pytest.skip("this environment has ROS 2 loaded")
    assert odom.main(["--seconds", "1", "--out", str(tmp_path / "o.csv")]) == 2
    assert "ros_env.sh" in capsys.readouterr().err and not (tmp_path / "o.csv").exists()


def test_a_nonpositive_duration_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        odom.main(["--seconds", "0", "--out", str(tmp_path / "o.csv")])

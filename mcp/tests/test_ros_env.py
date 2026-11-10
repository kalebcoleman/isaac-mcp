"""The simulator's ROS settings, rendered as a matching shell line."""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("sim_tools_ros_env", REPO / "exts/sim-tools/sim_tools/ros_env.py")
ros_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ros_env)


def test_ros_environment_gives_a_matching_shell_line(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "ros_env.sh").write_text("#")
    info = ros_env.describe({"ROS_DOMAIN_ID": "52", "ROS_DISTRO": "humble", "RMW_IMPLEMENTATION": "rmw_cyclonedds_cpp"},
                            tmp_path)
    assert info["settings"]["ROS_DOMAIN_ID"] == "52" and "export ROS_DOMAIN_ID=52" in info["shell"]
    assert "ros_env.sh" in info["shell"]
    plain = ros_env.describe({}, tmp_path / "nowhere")
    assert "ros_dds.env" in plain["shell"] and "ROS_DOMAIN_ID=0" in plain["shell"]
    assert "DDS settings" in ros_env.shell_hint({}, tmp_path)
    # Every captured setting is re-exported (Kit may not use the repo profile), quoted.
    odd = ros_env.describe({"ROS_DOMAIN_ID": "7", "ROS_DISTRO": "jazzy", "RMW_IMPLEMENTATION": "rmw_fastrtps_cpp",
                            "CYCLONEDDS_URI": "file:///a b/c.xml"}, tmp_path)
    assert odd["shell"].startswith("export ROS_DISTRO=jazzy; export ROS_DOMAIN_ID=7; source ")
    assert "export RMW_IMPLEMENTATION=rmw_fastrtps_cpp" in odd["shell"]
    assert "export CYCLONEDDS_URI='file:///a b/c.xml'" in odd["shell"]
    assert odd["shell"].count("ROS_DOMAIN_ID") == 1
    fallback = ros_env.describe({"ROS_DISTRO": "humble"}, tmp_path / "nowhere")
    assert "/opt/ros/humble/setup.bash" in fallback["shell"]
    # An odd distro is never pasted into a path; profile-forced keys Kit lacks are unset.
    evil = ros_env.describe({"ROS_DISTRO": "x;rm -rf ~"}, tmp_path / "nowhere")
    assert "rm -rf ~/setup" not in evil["shell"] and "/opt/ros/<distro>/setup.bash" in evil["shell"]
    assert "unset ROS_LOCALHOST_ONLY" in evil["shell"] and "unset RMW_IMPLEMENTATION" in evil["shell"]
    assert "unset" not in ros_env.describe({"RMW_IMPLEMENTATION": "a", "ROS_LOCALHOST_ONLY": "1",
                                            "CYCLONEDDS_URI": "b"}, tmp_path)["shell"]

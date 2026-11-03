"""ROS 2 / RViz support: sim.ros2.tf_setup graph layout, the RViz config writer and the TF doctor."""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ros2 = _load("sim_tools_ros2_under_test", "exts/sim-tools/sim_tools/ros2.py")
rviz = _load("ros2_rviz_config_under_test", "scripts/ros2_rviz_config.py")
doctor = _load("ros2_tf_doctor_under_test", "scripts/ros2_tf_doctor.py")

POSE = ((0.1, 0.2, 0.0), (0.0, 0.0, 0.0, 1.0))


def plan(**kw):
    args = dict(robot="/World/ur5e", base_prim="/World/ur5e/base_link", base_frame="base_link", base_pose=POSE)
    args.update(kw)
    return ros2.planned_graph(**args)


def _check_wiring(nodes, values, connects):
    names = {n for n, _ in nodes}
    assert len(names) == len(nodes), "node names must be unique"
    for src, dst in connects:
        assert src.split(".")[0] in names and dst.split(".")[0] in names, (src, dst)
    for attr, _ in values:
        assert attr.split(".")[0] in names, attr
    types = dict(nodes)
    publishers = [n for n, t in nodes if t.startswith("isaacsim.ros2.bridge.ROS2Publish")]
    for name in publishers:
        assert (f"Context.outputs:context", f"{name}.inputs:context") in connects
        assert (f"ReadSimTime.outputs:simulationTime", f"{name}.inputs:timeStamp") in connects
        assert any(d == f"{name}.inputs:execIn" for _, d in connects), f"{name} never ticks"
    return types


def test_fixed_arm_publishes_clock_joint_states_static_base_and_links():
    nodes, values, connects, frames = plan()
    types = _check_wiring(nodes, values, connects)
    v = dict(values)
    assert types["PublishClock"] == "isaacsim.ros2.bridge.ROS2PublishClock"
    assert v["TFWorldToBase.inputs:parentFrameId"] == "world"
    assert v["TFWorldToBase.inputs:childFrameId"] == "base_link"
    assert v["TFWorldToBase.inputs:staticPublisher"] is True
    assert v["TFWorldToBase.inputs:topicName"] == "tf_static"
    assert v["TFWorldToBase.inputs:translation"] == [0.1, 0.2, 0.0]
    assert v["TFWorldToBase.inputs:rotation"] == [0.0, 0.0, 0.0, 1.0]  # IJKR
    assert v["TFRobotLinks.inputs:parentPrim"] == "/World/ur5e/base_link"
    assert v["TFRobotLinks.inputs:targetPrims"] == ["/World/ur5e"]
    assert v["PublishJointState.inputs:topicName"] == "joint_states"
    assert "ComputeOdometry" not in types
    assert frames["root"] == "world"


def test_mobile_base_uses_odometry_and_a_static_world_to_odom():
    nodes, values, connects, frames = plan(mode="mobile", world_frame="map")
    types = _check_wiring(nodes, values, connects)
    v = dict(values)
    assert types["ComputeOdometry"] == "isaacsim.core.nodes.IsaacComputeOdometry"
    assert (v["TFWorldToOdom.inputs:parentFrameId"], v["TFWorldToOdom.inputs:childFrameId"]) == ("map", "odom")
    assert v["TFOdomToBase.inputs:staticPublisher"] is False
    assert ("ComputeOdometry.outputs:position", "TFOdomToBase.inputs:translation") in connects
    # Odometry outputs drive the odom->base TF; the playback tick does not fire it a second time.
    assert ("OnPlaybackTick.outputs:tick", "TFOdomToBase.inputs:execIn") not in connects
    assert v["PublishOdometry.inputs:chassisFrameId"] == "base_link"
    assert frames["root"] == "map"


def test_mobile_without_world_frame_leaves_map_to_odom_to_localization():
    nodes, *_ = plan(mode="mobile", world_frame="")
    assert "TFWorldToOdom" not in dict(nodes)


def test_robot_state_publisher_mode_publishes_no_link_frames():
    nodes, values, connects, frames = plan(link_frames="robot_state_publisher")
    assert "TFRobotLinks" not in dict(nodes)
    assert "PublishJointState" in dict(nodes)
    assert "robot_state_publisher" in frames["links"]


def test_extra_frames_are_published_relative_to_the_base():
    nodes, values, *_ = plan(extra_prims=["/World/cam", "/World/lidar"])
    v = dict(values)
    assert v["TFExtraFrames.inputs:targetPrims"] == ["/World/cam", "/World/lidar"]
    assert v["TFExtraFrames.inputs:parentPrim"] == "/World/ur5e/base_link"


def test_namespace_is_applied_to_every_publisher():
    nodes, values, *_ = plan(namespace="robot1", mode="mobile")
    v = dict(values)
    for name, t in nodes:
        if t.startswith("isaacsim.ros2.bridge.ROS2Publish") and name != "PublishClock":
            assert v[f"{name}.inputs:nodeNamespace"] == "robot1", name


@pytest.mark.parametrize("kw", [
    dict(base_frame="base link"), dict(base_frame="1base"), dict(world_frame="base_link"),
    dict(mode="mobile", world_frame="odom"), dict(mode="wheeled"), dict(link_frames="both"),
    dict(namespace="bad ns"), dict(base_pose=((float("nan"), 0, 0), (0, 0, 0, 1))),
])
def test_invalid_layouts_are_refused(kw):
    with pytest.raises(ValueError):
        plan(**kw)


def test_the_tool_contract_is_registered():
    sys.path.insert(0, str(REPO / "mcp"))
    from isaac_mcp.scene_contracts import REGISTRY

    params = REGISTRY["sim.ros2.tf_setup"]["parameters"]
    assert {"session_id", "stage_id", "robot", "base", "world_frame", "link_frames", "extra_frames"} <= set(params)
    assert params["robot"]["required"] and not params["base"]["required"]


# --------------------------------------------------------------------- RViz config

def test_rviz_config_has_the_fixed_frame_and_one_display_per_topic(tmp_path):
    out = tmp_path / "lab.rviz"
    assert rviz.main(["--fixed-frame", "world", "--out", str(out), "--robot-model",
                      "--image", "/camera/rgb", "--scan", "/scan", "--cloud", "/points", "--odom", "/odom"]) == 0
    text = out.read_text()
    assert 'Fixed Frame: "world"' in text
    for cls in ("TF", "Grid", "RobotModel", "Image", "LaserScan", "PointCloud2", "Odometry"):
        assert f"rviz_default_plugins/{cls}" in text
    for topic in ("/camera/rgb", "/scan", "/points", "/odom", "/robot_description"):
        assert f'Value: "{topic}"' in text
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(text)
    vm = doc["Visualization Manager"]
    assert vm["Global Options"]["Fixed Frame"] == "world"
    assert len(vm["Displays"]) == 2 + 1 + 4


def test_rviz_config_refuses_bad_input(tmp_path, capsys):
    out = tmp_path / "x.rviz"
    for argv in (["--fixed-frame", "my frame", "--out", str(out)],
                 ["--fixed-frame", "world", "--out", str(out), "--scan", "scan"],
                 ["--fixed-frame", "world", "--out", str(tmp_path / "x.yaml")]):
        with pytest.raises(SystemExit):
            rviz.main(argv)
    out.write_text("mine")
    with pytest.raises(SystemExit):
        rviz.main(["--fixed-frame", "world", "--out", str(out)])
    assert out.read_text() == "mine"


# ------------------------------------------------------------------------ TF doctor

def e(parent, child, stamp=5.0, static=False):
    return dict(parent=parent, child=child, static=static, stamp=stamp)


GOOD = [e("world", "base_link", 0, True), e("base_link", "shoulder_link"), e("shoulder_link", "tool0")]


def test_a_clean_tree_has_no_errors():
    errors, warnings, info = doctor.analyze(GOOD, "world", clock_seen=True)
    assert errors == [] and info["roots"] == ["world"] and info["time_source"] == ["sim"]


def test_two_parents_is_the_flicker_error_and_names_the_publishers():
    edges = GOOD + [e("world", "tool0")]
    errors, warnings, _ = doctor.analyze(edges, "world", clock_seen=True,
                                         tf_publishers=["/isaac_sim", "/robot_state_publisher"])
    assert any("'tool0' has 2 parents" in x for x in errors)
    assert any("robot_state_publisher" in w for w in warnings)


def test_static_and_dynamic_for_one_frame_is_an_error():
    errors, *_ = doctor.analyze(GOOD + [e("world", "base_link", 5.0, False)], "world", clock_seen=True)
    assert any("both /tf and /tf_static" in x for x in errors)


def test_frames_that_do_not_reach_the_fixed_frame_are_reported():
    edges = GOOD + [e("odom", "base_footprint")]
    errors, *_ = doctor.analyze(edges, "world", clock_seen=True)
    assert any("do not connect to 'world'" in x and "odom" in x for x in errors)


def test_missing_fixed_frame():
    errors, *_ = doctor.analyze(GOOD, "map", clock_seen=True)
    assert any("'map' is not in the tree" in x for x in errors)


def test_mixed_time_sources_and_missing_clock():
    mixed = GOOD + [e("base_link", "camera_link", stamp=1.79e9)]
    errors, warnings, _ = doctor.analyze(mixed, "world", clock_seen=True)
    assert any("mixed" in x and "camera_link" in x for x in errors)
    assert not any("not updated" in w for w in warnings)  # wall stamps do not make sim frames stale
    errors, *_ = doctor.analyze(GOOD, "world", clock_seen=False)
    assert any("nothing publishes /clock" in x for x in errors)


def test_no_data_says_check_playing_and_domain():
    errors, *_ = doctor.analyze([], "world", clock_seen=False)
    assert any("ROS_DOMAIN_ID" in x for x in errors)


def test_doctor_without_ros_exits_2(monkeypatch, capsys):
    def no_ros(seconds):
        raise ImportError("No module named 'rclpy'")

    monkeypatch.setattr(doctor, "capture", no_ros)
    assert doctor.main(["--fixed-frame", "world"]) == 2
    assert "Source ROS 2" in capsys.readouterr().err


def test_a_frame_that_stopped_updating_is_reported():
    edges = GOOD + [e("base_link", "camera_link", stamp=1.0), e("base_link", "shoulder_link", stamp=9.0)]
    _, warnings, _ = doctor.analyze(edges, "world", clock_seen=True)
    assert any("not updated" in w and "camera_link" in w for w in warnings)

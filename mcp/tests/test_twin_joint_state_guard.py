"""ur5e_dt__quick_start after sim__ros2__tf_setup: the same feedback loop, the other way round."""

import ast
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("joint_state_guard_under_test", REPO / "exts/ur5e-dt/ur5e_dt/joint_state_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
TYPE = "isaacsim.ros2.bridge.ROS2PublishJointState"


@pytest.mark.parametrize("kind,topic,namespace,loops", [
    (TYPE, "joint_states", "", True),
    (TYPE, "/joint_states", "", True),
    (TYPE, "joint_states", "rg2", False),
    (TYPE, "isaac/joint_states", "", False),
    ("omni.isaac.ros2_bridge.ROS2PublishJointState", "joint_states", "", True),
    ("isaacsim.ros2.bridge.ROS2SubscribeJointState", "joint_states", "", False),
])
def test_only_a_publisher_into_the_mirrored_topic_counts(kind, topic, namespace, loops):
    found = guard.loop_publishers([("/Graph/n", kind, topic, namespace)])
    assert found == (["/Graph/n"] if loops else [])


def test_refusal_gives_the_fix():
    text = guard.refusal(["/Graph/isaac_mcp_ros2/PublishJointState"])
    assert "/Graph/isaac_mcp_ros2/PublishJointState" in text and "joint_states_topic=isaac/joint_states" in text


def test_check_stage_raises_only_with_a_publisher(monkeypatch):
    monkeypatch.setattr(guard, "stage_nodes", lambda: [("/Graph/n", TYPE, "joint_states", "")])
    with pytest.raises(RuntimeError, match="oscillate"):
        guard.check_stage()
    monkeypatch.setattr(guard, "stage_nodes", lambda: [])
    guard.check_stage()





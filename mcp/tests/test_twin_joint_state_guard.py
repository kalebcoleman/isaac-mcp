"""ur5e_dt__quick_start after sim__ros2__tf_setup: the same feedback loop, the other way round."""

import ast
import importlib.util
from pathlib import Path

import pytest
import twin_source

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


def test_every_quick_start_checks_before_building_anything():
    tree = ast.parse(twin_source.SOURCE.read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "quick_start")
    source = ast.unparse(method)
    assert source.index("check_joint_state_loop()") < source.index("load_scene")
    for name in ("quick_start_with_workstation", "quick_start_with_workstation_and_simple_room"):
        other = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        assert "self.quick_start()" in ast.unparse(other)


def test_quick_start_descriptions_say_whose_work_this_is():
    from isaac_mcp import host_catalog  # noqa: F401 - the catalog is generated from these

    tree = ast.parse(twin_source.SOURCE.read_text())
    registry = next(n.value for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "MCP_TOOL_REGISTRY")
    tools = ast.literal_eval(registry)
    assert tools["quick_start"]["description"].startswith("RISE lab UR5e digital twin; for a generic start, see sim__quick_start.")
    for name in ("quick_start_with_workstation", "quick_start_with_workstation_and_simple_room"):
        assert tools[name]["description"].startswith("RISE lab UR5e digital twin.")

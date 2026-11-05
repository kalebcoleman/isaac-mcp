"""Public robots by name, mobile TF without loops, the /cmd_vel and /scan graph recipes and the flat floor warning."""

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("pxr.Usd")
from pxr import Usd  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "exts" / "sim-tools"))

from isaac_mcp.project_assets import AssetError  # noqa: E402
from isaac_mcp.scene_contracts import REGISTRY  # noqa: E402
from sim_tools import public_robots as pr, quick_start as qs, ros2  # noqa: E402
from sim_tools.placement import flat_box_warning  # noqa: E402
from sim_tools.stage_open import time_code_report  # noqa: E402

ROOT = "https://example.test/Assets/Isaac/6.1"


def _stat(existing):
    async def stat(url):
        return url in existing
    return stat


def _locate(name, *, root=ROOT, existing=(), **kw):
    return asyncio.run(pr.locate(name, root, _stat(existing), **kw))


def test_a_name_resolves_under_this_builds_root():
    url = f"{ROOT}/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd"
    found = _locate("franka", existing={url})
    assert found["uri"] == url and found["name"] == "Franka" and found["wheels"] is None
    assert found["assets"][0]["source"] == "nvidia_assets_root"


def test_turtlebot_takes_whichever_path_this_release_has():
    older = f"{ROOT}/Isaac/Robots/Turtlebot/Turtlebot3/turtlebot3_burger.usd"
    newer = f"{ROOT}/Isaac/Robots/Turtlebot/Turtlebot3/tb3_burger_processed.usda"
    assert _locate("turtlebot3_burger", existing={older})["uri"] == older
    assert _locate("turtlebot3_burger", existing={older, newer})["uri"] == newer


def test_a_file_this_build_lacks_is_refused_with_the_names():
    with pytest.raises(AssetError) as exc:
        _locate("create3")
    assert exc.value.code == "ROBOT_NOT_ON_THIS_BUILD"
    assert "franka" in str(exc.value) and "ur5e" in str(exc.value) and "create_3.usd" in str(exc.value)


def test_no_root_strict_mode_and_an_unreachable_server_are_each_told_apart():
    with pytest.raises(AssetError) as exc:
        _locate("franka", root=None)
    assert exc.value.code == "ASSETS_UNAVAILABLE"
    with pytest.raises(AssetError) as exc:
        _locate("franka", offline=True, existing={f"{ROOT}/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd"})
    assert exc.value.code == "ASSET_MISSING" and "strict" in str(exc.value)

    async def down(url):
        raise asyncio.TimeoutError()
    with pytest.raises(AssetError) as exc:
        asyncio.run(pr.locate("franka", ROOT, down))
    assert exc.value.code == "ASSETS_UNAVAILABLE" and "reach" in str(exc.value)


def test_only_not_found_means_absent():
    assert pr.stat_found("ok", "ok", "missing") is True
    assert pr.stat_found("missing", "ok", "missing") is False
    with pytest.raises(OSError):
        pr.stat_found("connection lost", "ok", "missing")

    async def offline(url):
        return pr.stat_found("connection lost", "ok", "missing")
    with pytest.raises(AssetError) as exc:
        asyncio.run(pr.locate("franka", ROOT, offline))
    assert exc.value.code == "ASSETS_UNAVAILABLE"


def test_strict_mode_keeps_a_local_assets_root():
    url = "/data/isaac/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd"
    assert _locate("franka", root="/data/isaac", existing={url}, offline=True)["uri"] == url


@pytest.mark.parametrize("robot", ["bogus", "https://example.test/robot.usd", "turtlebot"])
def test_a_robot_that_is_not_a_name_lists_the_names(robot):
    with pytest.raises(AssetError) as exc:
        qs.resolve_robot(robot)
    assert exc.value.code == "INVALID_VALUE"
    for name in ("ur5e", "franka", "turtlebot3_burger", "carter_v1", "create3"):
        assert name in str(exc.value)


def test_the_ur5e_stays_local_first_and_outside_the_nvidia_table():
    assert "ur5e" in qs.PUBLIC_ROBOTS and "ur5e" not in pr.NVIDIA_ROBOTS


def test_every_wheeled_robot_has_a_complete_table_entry():
    wheeled = [entry["wheels"] for entry in pr.NVIDIA_ROBOTS.values() if "wheels" in entry]
    assert len(wheeled) == 5
    for wheels in wheeled:
        assert len(wheels["joints"]) == 2 and wheels["radius_m"] > 0 and wheels["separation_m"] > 0 and wheels["base_link"]


def test_the_contract_names_the_robots_and_stays_within_its_limits():
    tool = REGISTRY["sim.quick_start"]
    text = tool["parameters"]["robot"]["description"]
    assert all(name in text for name in pr.NVIDIA_ROBOTS) and len(text) <= 450
    command = REGISTRY["sim.joints.command"]["description"]
    assert "lost on Stop" in command and "sim__joints__drive" in command and len(command) <= 600


# Links of NVIDIA's older TurtleBot3 (5.1, 6.0): base_footprint is a body fixed to base_link.
LINKS = [(f"/r/{name}", name) for name in ("base_footprint", "base_link", "caster", "scan", "wheel_l", "wheel_r")]
JOINTS = [("/r/base_footprint", "/r/base_link", True), ("/r/base_link", "/r/caster", True), ("/r/base_link", "/r/scan", True),
          ("/r/base_link", "/r/wheel_l", False), ("/r/base_link", "/r/wheel_r", False)]
POSE = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))


@pytest.mark.parametrize("base", ["/r/base_link", "/r/base_footprint"])
def test_a_mobile_base_publishes_each_edge_once_whichever_link_is_the_base(base):
    moving, fixed = ros2.link_publishing(LINKS, base, "mobile", JOINTS)
    assert moving == ["/r/wheel_l", "/r/wheel_r"]
    assert sorted(fixed) == sorted({"/r/base_footprint", "/r/base_link", "/r/caster", "/r/scan"} - {base})


def test_the_live_tf_node_never_sees_the_link_physx_may_root_the_tree_at():
    moving, fixed = ros2.link_publishing(LINKS, "/r/base_footprint", "mobile", JOINTS)
    statics = [(path.rsplit("/", 1)[-1], *POSE) for path in fixed]
    nodes, values, _, frames = ros2.planned_graph(robot="/r", base_prim="/r/base_footprint", base_frame="base_footprint", base_pose=POSE,
                                                  mode="mobile", world_frame="", link_prims=moving, static_links=statics)
    values = dict(values)
    assert values["TFRobotLinks.inputs:targetPrims"] == ["/r/wheel_l", "/r/wheel_r"]
    assert values["TFRobotLinks.inputs:parentPrim"] == "/r/base_footprint"
    assert values["ComputeOdometry.inputs:chassisPrim"] == "/r/base_footprint"
    edges = [(edge["parent"], edge["child"]) for edge in frames["edges"]]
    assert ("base_footprint", "base_link") in edges and ("base_link", "base_footprint") not in edges
    assert sum(1 for _, child in edges if child == "base_footprint") == 1


def test_a_fixed_arm_at_its_root_keeps_the_articulations_tree():
    joints = [("", "/a/base", True), ("/a/base", "/a/link1", False)]
    links = [("/a/base", "base"), ("/a/link1", "link1")]
    assert ros2.link_publishing(links, "/a/base", "fixed", joints) == (None, ())
    nodes, values, _, _ = ros2.planned_graph(robot="/a", base_prim="/a/base", base_frame="base", base_pose=POSE)
    assert dict(values)["TFRobotLinks.inputs:targetPrims"] == ["/a"]


def test_a_fixed_base_with_a_parent_link_does_not_loop():
    moving, fixed = ros2.link_publishing(LINKS, "/r/base_link", "fixed", JOINTS)
    assert "/r/base_link" not in moving and "/r/base_footprint" in fixed


def test_a_base_with_nothing_moving_publishes_only_static_edges():
    links, joints = [("/r/a", "a"), ("/r/b", "b")], [("/r/a", "/r/b", True)]
    moving, fixed = ros2.link_publishing(links, "/r/a", "mobile", joints)
    assert moving == [] and fixed == ["/r/b"]
    nodes, *_ = ros2.planned_graph(robot="/r", base_prim="/r/a", base_frame="a", base_pose=POSE, mode="mobile", world_frame="",
                                   link_prims=moving, static_links=[("b", *POSE)])
    assert "TFRobotLinks" not in dict(nodes) and "TFFixedLink0" in dict(nodes)


def test_cmd_vel_graph_wires_the_table_values():
    wheels = pr.NVIDIA_ROBOTS["create3"]["wheels"]
    args = ros2.cmd_vel_graph_args("/World/Create3", wheels)
    assert args["targets"] == {"Articulation.inputs:targetPrim": ["/World/Create3"]}
    assert args["values"]["Differential.inputs:wheelRadius"] == wheels["radius_m"]
    assert args["values"]["Articulation.inputs:jointNames"] == wheels["joints"]
    names = {name for name, _ in args["nodes"]}
    for source, target in args["connections"]:
        assert source.split(".")[0] in names and target.split(".")[0] in names


def test_scan_graph_targets_the_omnilidar_with_a_product_it_makes_itself():
    args = ros2.scan_graph_args("/r/Lidar2D/RPLidar_S2E", "RPLidar_S2E")
    assert args["targets"] == {"RenderProduct.inputs:cameraPrim": ["/r/Lidar2D/RPLidar_S2E"]}
    assert ["RenderProduct.outputs:renderProductPath", "ScanHelper.inputs:renderProductPath"] in args["connections"]
    assert "ScanHelper.inputs:renderProductPath" not in args["values"]  # never the session-only IsaacMCP_ product
    assert args["values"]["ScanHelper.inputs:resetSimulationTimeOnStop"] is True
    assert args["values"]["ScanHelper.inputs:frameId"] == "RPLidar_S2E"


@pytest.mark.parametrize("kind,scale,collision,warns", [
    ("Cube", (20, 20, 0.1), True, True),   # no wheel contact on 6.2 and 5.1
    ("Cube", (40, 40, 0.3), True, True),
    ("Cube", (5, 5, 0.1), True, True),     # Carter erratic
    ("Cube", (20, 20, 1), True, False),
    ("Cube", (0.2, 6.2, 1), True, False),  # a wall
    ("Cube", (20, 20, 0.1), False, False),  # no collider
    ("Cylinder", (20, 20, 0.1), True, False),
])
def test_flat_box_warning(kind, scale, collision, warns):
    warning = flat_box_warning(kind, scale, collision)
    assert bool(warning) is warns
    if warns:
        assert "sim__quick_start" in warning


def test_the_doctor_reports_a_loop_even_when_it_hides_the_fixed_frame():
    spec = importlib.util.spec_from_file_location("ros2_tf_doctor_loop", REPO / "scripts" / "ros2_tf_doctor.py")
    doctor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doctor)
    edge = lambda parent, child: dict(parent=parent, child=child, static=False, stamp=1.0)  # noqa: E731
    errors, *_ = doctor.analyze([edge("base_footprint", "base_link"), edge("base_link", "base_footprint"),
                                 edge("base_link", "wheel")], "odom", clock_seen=True)
    assert any("TF loop base_footprint -> base_link -> base_footprint" in error for error in errors)
    assert any("every frame has a parent" in error for error in errors)


def test_nav2_params_sets_the_frame_radius_and_start():
    spec = importlib.util.spec_from_file_location("nav2_params_ut", REPO / "scripts" / "nav2_params.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = ('amcl:\n  ros__parameters:\n    base_frame_id: "base_footprint"\n    set_initial_pose: false\n'
              'local_costmap:\n  local_costmap:\n    ros__parameters:\n      robot_base_frame: base_footprint\n      robot_radius: 0.22\n'
              'controller_server:\n  ros__parameters:\n    yaw_goal_tolerance: 0.25\n')
    out = module.rewrite(source, "chassis_link", 0.45, 1.0, 2.0, 0.5, yaw_tolerance=3.2)
    assert out.count("set_initial_pose") == 1 and "set_initial_pose: true" in out
    assert "initial_pose: {x: 1.0, y: 2.0, z: 0.0, yaw: 0.5}" in out
    assert 'base_frame_id: "chassis_link"' in out and "robot_base_frame: chassis_link" in out
    assert "robot_radius: 0.45" in out and "yaw_goal_tolerance: 3.2" in out and "base_footprint" not in out
    assert "yaw_goal_tolerance: 0.25" in module.rewrite(source)


def test_a_file_with_no_time_code_range_is_reported():
    stage = Usd.Stage.CreateInMemory()
    assert "startTimeCode" in time_code_report(stage)["time_code_note"]
    stage.SetEndTimeCode(1000000.0)
    assert time_code_report(stage) == {}


@pytest.mark.parametrize("name, tail", [("unitree_h1", "Unitree/H1/h1.usd"), ("unitree_g1", "Unitree/G1/g1.usd")])
def test_the_humanoids_load_by_name_from_this_builds_root(name, tail):
    url = f"{ROOT}/Isaac/Robots/{tail}"
    found = _locate(name, existing={url})
    assert found["uri"] == url and found["wheels"] is None


def test_a_humanoid_missing_from_a_build_is_refused_with_the_names():
    with pytest.raises(AssetError) as exc:
        _locate("unitree_h1", existing=set())
    assert "unitree_g1" in str(exc.value)


def test_only_the_humanoids_are_lifted_onto_the_ground():
    """A humanoid's origin is its pelvis. Carter's and Nova Carter's files read 50 to 1,500 m below the ground, so they must never be lifted."""
    lifted = sorted(name for name, entry in pr.NVIDIA_ROBOTS.items() if entry.get("lift_to_ground"))
    assert lifted == ["unitree_g1", "unitree_h1"]
    url = f"{ROOT}/Isaac/Robots/Unitree/H1/h1.usd"
    assert _locate("unitree_h1", existing={url})["lift_to_ground"] is True
    franka = f"{ROOT}/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd"
    assert _locate("franka", existing={franka})["lift_to_ground"] is False

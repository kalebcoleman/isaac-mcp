"""Fixes from student run 5 (Nemotron 3.5 Lightning, OpenCode): input shapes weak models send,
unknown arguments, stale-target wording, and the simulator's ROS settings for a shell."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from isaac_mcp import server as gw

REPO = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(f"sim_tools_{name}_run5", REPO / f"exts/sim-tools/sim_tools/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gb = load("graph_build")
ros_env = load("ros_env")
TICK, PUB = "omni.graph.action.OnPlaybackTick", "isaacsim.ros2.bridge.ROS2PublishClock"


def test_graph_spec_accepts_the_shapes_weak_models_send():
    spec = gb.parse_spec(dict(path="/World/ROS_Clock",
                              nodes=[[TICK, "tick"], {"name": "pub", "type": PUB}],   # type first; object
                              connections=[["tick", "tick", "pub", "execIn"],          # 4 parts, bare ports
                                           {"from": "tick.outputs:tick", "to": "pub.inputs:execIn"}]))
    assert spec["nodes"] == [("tick", TICK), ("pub", PUB)]
    assert spec["connections"] == [(("tick", "outputs:tick"), ("pub", "inputs:execIn"))] * 2
    assert any("type first" in n for n in spec["notes"])
    with pytest.raises(gb.SpecError, match="name, node_type"):
        gb.parse_spec(dict(path="/G", nodes=[{"kind": TICK}]))
    with pytest.raises(gb.SpecError, match="srcNode, srcPort"):
        gb.parse_spec(dict(path="/G", nodes=[["tick", TICK]], connections=[["a", "b", "c"]]))


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


def test_scene_tools_refuse_unknown_arguments():
    # Run 5: properties={...} was silently dropped by sim__graph__build and nothing was applied.
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})

    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            await client.initialize()
            bad = await client.call_tool("sim__graph__build", {"session_id": "s", "stage_id": "t", "path": "/G",
                                                               "nodes": [["tick", TICK]], "properties": {"a": 1}})
            assert bad.isError and "properties" in bad.content[0].text and "xtra" in bad.content[0].text
            ok = await client.call_tool("sim__graph__build", {"session_id": "s", "stage_id": "t", "path": "/G",
                                                              "nodes": [{"name": "tick", "type": TICK}]})
            assert "xtra" not in ok.content[0].text   # reaches dispatch (fails offline, not on validation)

    asyncio.run(run())

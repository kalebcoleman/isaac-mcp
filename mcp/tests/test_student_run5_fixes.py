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
    # A bare source port stays unresolved until build() sees the real node (constant nodes have
    # only inputs:value); a bare destination is always an input.
    assert spec["connections"] == [(("tick", "?tick"), ("pub", "inputs:execIn")),
                                   (("tick", "outputs:tick"), ("pub", "inputs:execIn"))]
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
    # Review of #68: every captured setting is re-exported (Kit may not use the repo profile), quoted.
    odd = ros_env.describe({"ROS_DOMAIN_ID": "7", "ROS_DISTRO": "jazzy", "RMW_IMPLEMENTATION": "rmw_fastrtps_cpp",
                            "CYCLONEDDS_URI": "file:///a b/c.xml"}, tmp_path)
    assert odd["shell"].startswith("export ROS_DISTRO=jazzy; export ROS_DOMAIN_ID=7; source ")
    assert "export RMW_IMPLEMENTATION=rmw_fastrtps_cpp" in odd["shell"]
    assert "export CYCLONEDDS_URI='file:///a b/c.xml'" in odd["shell"]
    assert odd["shell"].count("ROS_DOMAIN_ID") == 1
    fallback = ros_env.describe({"ROS_DISTRO": "humble"}, tmp_path / "nowhere")
    assert "/opt/ros/humble/setup.bash" in fallback["shell"]
    # Round 2: an odd distro is never pasted into a path; profile-forced keys Kit lacks are unset.
    evil = ros_env.describe({"ROS_DISTRO": "x;rm -rf ~"}, tmp_path / "nowhere")
    assert "rm -rf ~/setup" not in evil["shell"] and "/opt/ros/<distro>/setup.bash" in evil["shell"]
    assert "unset ROS_LOCALHOST_ONLY" in evil["shell"] and "unset RMW_IMPLEMENTATION" in evil["shell"]
    assert "unset" not in ros_env.describe({"RMW_IMPLEMENTATION": "a", "ROS_LOCALHOST_ONLY": "1",
                                            "CYCLONEDDS_URI": "b"}, tmp_path)["shell"]


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
            listed = {t.name: t for t in (await client.list_tools()).tools}
            assert listed["sim__graph__build"].inputSchema.get("additionalProperties") is False

    asyncio.run(run())


def test_serialized_graph_payload_reaches_parser_through_mcp():
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    seen = []

    class Bridge:
        def call(self, name, args):
            assert name == "sim.graph.build"
            try:
                seen.append(gb.parse_spec(args))
                return {"status": "ok"}
            except gb.SpecError as exc:
                return {"status": "error", "code": exc.code, "message": str(exc)}
        def close(self):
            pass

    gw._state_for(server).client = Bridge()

    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            await client.initialize()
            args = {"session_id": "s", "stage_id": "t", "path": "/G",
                    "nodes": ['["tick", "omni.graph.action.OnPlaybackTick"]',
                              '["clock", "isaacsim.ros2.bridge.ROS2PublishClock"]'],
                    "connections": ['["tick.outputs:tick", "clock.inputs:execIn"]'],
                    "values": '{"clock.inputs:topicName": "/clock", "clock.inputs:queueSize": 10}'}
            result = await client.call_tool("sim__graph__build", args)
            assert not result.isError
            assert seen[-1]["nodes"] == [("tick", TICK), ("clock", PUB)]
            assert ("clock", "inputs:queueSize", 10) in seen[-1]["values"]
            args["nodes"] = '[invalid'
            bad = await client.call_tool("sim__graph__build", args)
            assert bad.isError and "nodes: expected" in bad.content[0].text
            assert "validation errors" not in bad.content[0].text

    asyncio.run(run())


def test_graph_schema_and_serialization_errors_at_mcp_boundary():
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    seen = []

    class Bridge:
        def call(self, name, args):
            assert name == "sim.graph.build"
            seen.append(args)
            try:
                gb.parse_spec(args)
                return {"status": "ok"}
            except gb.SpecError as exc:
                return {"status": "error", "code": exc.code, "message": str(exc)}
        def close(self):
            pass

    gw._state_for(server).client = Bridge()

    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            await client.initialize()
            listed = {t.name: t for t in (await client.list_tools()).tools}
            props = listed["sim__graph__build"].inputSchema["properties"]
            for field, expected in (("nodes", "array"), ("connections", "array"),
                                    ("values", "object"), ("targets", "object")):
                schema = props[field]
                assert schema.get("description")
                assert {branch.get("type") for branch in schema["anyOf"]} >= {"string", expected}
                assert all(branch.get("type") for branch in schema["anyOf"])
            valid = {"session_id": "s", "stage_id": "t", "path": "/G",
                     "nodes": [["clock", PUB]], "connections": [],
                     "values": {"clock.inputs:topicName": "/clock"},
                     "targets": {"clock.inputs:targetPrim": ["/World/Camera"]}}
            result = await client.call_tool("sim__graph__build", valid)
            assert not result.isError and seen[-1]["nodes"] == valid["nodes"]
            deep = "[" * 2000 + "]" * 2000
            cases = [
                ("nodes", deep),
                ("nodes", [deep]),
                ("connections", deep),
                ("values", '{"clock.inputs:value": NaN}'),
                ("targets", {"clock.inputs:targetPrim": deep}),
            ]
            for field, value in cases:
                args = dict(valid, **{field: value})
                before = len(seen)
                result = await client.call_tool("sim__graph__build", args)
                assert len(seen) == before + 1  # bridge parser, not FastMCP pre-parsing
                assert result.isError and field in result.content[0].text
                assert "expected" in result.content[0].text
                assert "recursion" not in result.content[0].text.lower()
                assert len(result.content[0].text) < 500

    asyncio.run(run())


def test_body_path_aliases_reach_the_bridge():
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    seen = []
    class Bridge:
        def call(self, name, args):
            seen.append((name, args))
            return {"status": "ok"}
        def close(self):
            pass
    gw._state_for(server).client = Bridge()

    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            await client.initialize()
            for key in ("path", "prim", "prim_path"):
                result = await client.call_tool("sim__body__state", {"session_id": "s", "stage_id": "t", key: "/Robot"})
                assert not result.isError and seen[-1][1][key] == "/Robot"
    asyncio.run(run())

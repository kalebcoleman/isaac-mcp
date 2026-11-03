"""PR D: the bridge built-ins are listed before any bridge answers, and refresh_tools explains a
Kit that is still starting (optionally waiting for it).

Student run 4 used OpenCode, which ignores tools/list_changed: after refresh_tools registered
run_python and status, the client still said "Unknown tool". Listing them from the packaged
catalog, like the twin and scene tools, makes them visible from the first tools/list.
"""

import asyncio
import json
import socket

import pytest

pytest.importorskip("mcp")

from mcp.shared.memory import create_connected_server_and_client_session

import isaac_mcp.server as gw
from isaac_mcp import host_catalog
from isaac_mcp.client import BridgeClient

BUILTINS = {"run_python", "status", "list_bridges", "reload_tools", "put_file", "get_file", "stat_file"}


class _Offline:
    port = 1

    def call(self, *args, **kwargs):
        raise ConnectionRefusedError("[Errno 111] Connection refused")

    def close(self):
        pass


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_builtins_catalog_matches_the_bridge_registry():
    from isaac_mcp_bridge.builtins import builtin_tools
    from isaac_mcp_bridge.schema import field_specs

    catalog = host_catalog.load_catalog()
    for definition in builtin_tools(None):
        if definition.internal:
            assert definition.name not in catalog          # list_tools stays hidden
            continue
        entry = catalog[definition.name]
        assert entry["namespace"] is None and entry["owner_id"] == "isaac.mcp.bridge"
        assert entry["description"] == definition.description
        assert entry["fields"] == field_specs(definition.parameters)
    assert {name for name in catalog if "." not in name} == BUILTINS


def test_builtins_are_listed_and_callable_through_an_mcp_client_before_discovery(monkeypatch):
    from test_bridge import LiveBridge

    monkeypatch.delenv("ISAAC_MCP_BUILTINS", raising=False)
    monkeypatch.delenv("ISAAC_MCP_NAMESPACE", raising=False)
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    state = gw._state_for(server)
    state.client = _Offline()

    async def offline():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            await client.initialize()
            listed = {t.name: t for t in (await client.list_tools()).tools}
            assert BUILTINS <= set(listed)
            assert "code" in listed["run_python"].inputSchema["properties"]
            assert listed["run_python"].inputSchema["required"] == ["code"]
            answer = await client.call_tool("status", {})
            assert answer.isError
            text = answer.content[0].text
            assert "no Isaac Sim bridge could be reached" in text and "refresh_tools(wait_s=" in text
            note = (await client.call_tool("refresh_tools", {})).content[0].text
            assert "still starting" in note

    asyncio.run(offline())

    with LiveBridge() as bridge:
        state.client = BridgeClient(port=bridge.port)

        async def online():
            async with create_connected_server_and_client_session(server._mcp_server) as client:
                await client.initialize()
                # The offline proxy dials the selected bridge: no refresh needed (OpenCode case).
                answer = await client.call_tool("status", {})
                assert not answer.isError
                assert json.loads(answer.content[0].text)["port"] == bridge.port
                before = {t.name for t in (await client.list_tools()).tools}
                await client.call_tool("refresh_tools", {})
                after = {t.name for t in (await client.list_tools()).tools}
                assert before == after                       # the same names, now served live
                assert BUILTINS <= set(state.name_map)
                assert not BUILTINS & set(gw.offline_tools(server))

        asyncio.run(online())


def test_offline_note_counts_builtins_as_one_group():
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    assert "built-ins: 7" in gw._offline_note(server)


def test_refresh_reports_a_list_tools_timeout_as_kit_still_starting(monkeypatch):
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})

    def booting(_server):
        raise TimeoutError("no response for 'list_tools' within 5.0s (the tool may still be executing)")

    monkeypatch.setattr(gw, "discover_and_register_tools", booting)
    monkeypatch.setattr(gw, "live_sessions", lambda env=None: [])
    message = asyncio.run(gw._refresh_tools(server))
    assert message.startswith("Error:") and "still starting" in message and "list_tools" in message


def test_refresh_wait_polls_until_the_bridge_answers(monkeypatch):
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    attempts = []

    def discover(_server):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionRefusedError("[Errno 111] Connection refused")
        return ["status"]

    monkeypatch.setattr(gw, "discover_and_register_tools", discover)
    monkeypatch.setattr(gw, "_probe_bridge", lambda server: None)
    monkeypatch.setattr(gw, "REFRESH_POLL_S", 0.01)
    message = asyncio.run(gw._refresh_tools(server, None, 5))
    assert len(attempts) == 3 and "after 3 attempt(s)" in message and not message.startswith("Error")


def test_refresh_wait_is_bounded_and_gives_up_with_guidance(monkeypatch):
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    attempts = []

    def offline(_server):
        attempts.append(1)
        raise ConnectionRefusedError("[Errno 111] Connection refused")

    monkeypatch.setattr(gw, "discover_and_register_tools", offline)
    monkeypatch.setattr(gw, "_probe_bridge", lambda server: None)
    monkeypatch.setattr(gw, "REFRESH_POLL_S", 0.01)
    monkeypatch.setattr(gw, "REFRESH_WAIT_MAX_S", 0.05)
    message = asyncio.run(gw._refresh_tools(server, None, 10_000))   # clamped to the max
    assert message.startswith("Error:") and "waited 0 s" in message and len(attempts) >= 2
    assert asyncio.run(gw._refresh_tools(server, None, "soon")).startswith("Error: wait_s")


def test_refresh_wait_does_not_retry_an_ambiguous_bridge(monkeypatch):
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    attempts = []

    def ambiguous(_server):
        attempts.append(1)
        raise RuntimeError("multiple bridges running (a pid=1 port=1, b pid=2 port=2)")

    monkeypatch.setattr(gw, "discover_and_register_tools", ambiguous)
    monkeypatch.setattr(gw, "_probe_bridge", lambda server: None)
    message = asyncio.run(gw._refresh_tools(server, None, 30))
    assert len(attempts) == 1 and "use_bridge(port)" in message


def test_refresh_tools_advertises_wait_s():
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert tools["refresh_tools"].inputSchema["properties"]["wait_s"]["default"] == 0
    assert "ctx" not in tools["refresh_tools"].inputSchema["properties"]


def test_refresh_wait_rejects_nan_and_probes_off_the_event_loop(monkeypatch):
    # Review of #66: wait_s=nan made the deadline nan (poll forever); the boot probe blocked the loop.
    import threading

    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    assert "finite" in asyncio.run(gw._refresh_tools(server, None, float("nan")))
    threads = []
    monkeypatch.setattr(gw, "_probe_bridge", lambda s: threads.append(threading.current_thread().name))
    monkeypatch.setattr(gw, "discover_and_register_tools", lambda s: ["status"])
    asyncio.run(gw._refresh_tools(server, None, 5))
    assert threads and threads[0] != threading.main_thread().name

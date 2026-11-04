"""PR D: the bridge built-ins are listed before any bridge answers, and refresh_tools explains a
Kit that is still starting (optionally waiting for it).

Student run 4 used OpenCode, which ignores tools/list_changed: after refresh_tools registered
run_python and status, the client still said "Unknown tool". Listing them from the packaged
catalog, like the twin and scene tools, makes them visible from the first tools/list.
"""

import asyncio
import json
import socket
from types import SimpleNamespace

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
    monkeypatch.setattr(gw, "live_sessions", lambda env=None: [])   # not this machine's real ones
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


# ---- wait_s and isError ----


def _refused(monkeypatch):
    monkeypatch.setattr(gw, "_probe_bridge", lambda server: (_ for _ in ()).throw(ConnectionRefusedError("refused")))
    monkeypatch.setattr(gw, "discover_and_register_tools", lambda server: (_ for _ in ()).throw(ConnectionRefusedError("refused")))


def test_wait_runs_to_the_deadline_with_a_shorter_last_sleep(monkeypatch):
    import asyncio
    import time

    _refused(monkeypatch)
    monkeypatch.setattr(gw, "REFRESH_POLL_S", 0.2)
    monkeypatch.setattr(gw, "live_sessions", lambda env=None: [])
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    start = time.monotonic()
    message = asyncio.run(gw._refresh_tools(server, None, 0.5))
    elapsed = time.monotonic() - start
    assert message.startswith("Error:") and 0.45 <= elapsed < 0.9


def test_wait_returns_at_once_with_several_sessions_this_server_launched(monkeypatch):
    import asyncio
    import time

    _refused(monkeypatch)
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    state = gw._state_for(server)
    state.launched_sessions = {"a", "b"}
    monkeypatch.setattr(gw, "_screen_port", lambda state: 18000)
    sessions = [{"id": "a", "port": 18001, "pid": 1, "started_at": None}, {"id": "b", "port": 18002, "pid": 2, "started_at": None}]
    monkeypatch.setattr(gw, "live_sessions", lambda env=None: sessions)
    start = time.monotonic()
    message = asyncio.run(gw._refresh_tools(server, None, 60))
    assert time.monotonic() - start < 2
    assert message.startswith("Error:") and "Returned without waiting" in message and "use_bridge" in message


def test_sessions_other_clients_launched_do_not_cancel_the_wait(monkeypatch):
    """Someone may be starting Kit on the configured port beside other agents' simulators."""
    state = SimpleNamespace(launched_sessions={"mine"}, env=None)
    sessions = [{"id": "mine", "port": 1, "pid": 1}, {"id": "theirs", "port": 2, "pid": 2}, {"id": "other", "port": 3, "pid": 3}]
    monkeypatch.setattr(gw, "live_sessions", lambda env=None: sessions)
    monkeypatch.setattr(gw, "_screen_port", lambda state: 18000)
    assert gw._several_launched_sessions(state, ConnectionRefusedError("x")) is False
    state.launched_sessions = {"mine", "theirs"}
    assert gw._several_launched_sessions(state, ConnectionRefusedError("x")) is True
    assert gw._several_launched_sessions(state, TimeoutError("still booting")) is False


def test_several_bridges_error_reads_as_two_sentences(monkeypatch):
    import asyncio

    monkeypatch.setattr(gw, "discover_and_register_tools", lambda server: (_ for _ in ()).throw(
        RuntimeError("multiple bridges running (1, 2) — pass port= or set ISAAC_MCP_PORT")))
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    message = asyncio.run(gw._refresh_tools(server))
    assert "ISAAC_MCP_PORT. Pick one with use_bridge(port)" in message


def test_failed_gateway_tools_set_iserror_with_the_same_words(monkeypatch):
    import asyncio

    from mcp.shared.memory import create_connected_server_and_client_session as connect

    _refused(monkeypatch)
    monkeypatch.setattr(gw, "live_sessions", lambda env=None: [])

    async def scenario():
        server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
        async with connect(server._mcp_server) as client:
            refused = await client.call_tool("refresh_tools", {})
            bad_port = await client.call_tool("use_bridge", {"port": 1})
        return refused, bad_port

    refused, bad_port = asyncio.run(scenario())
    assert refused.isError is True and refused.content[0].text.startswith("Error: no Isaac Sim bridge")
    assert bad_port.isError is True and bad_port.content[0].text.startswith("Error:")


def test_reported_leaves_success_alone():
    assert gw._reported("Registered 3 tool(s)") == "Registered 3 tool(s)"
    assert gw._reported("Connected to port 1 but tool discovery failed: x").isError is True
    assert gw._reported("Error: x").content[0].text == "Error: x"
    assert gw._reported("switched to bridge\nsome text with Error: in it") == "switched to bridge\nsome text with Error: in it"


# ---- a dead configured bridge beside a live one ----

OTHER = {"pid": 4242, "port": 18722, "host": "localhost", "app": "Isaac-Sim Python", "app_version": "6.2.0"}


def _dead_port_server(monkeypatch, bridges):
    _refused(monkeypatch)
    monkeypatch.setattr(gw, "live_sessions", lambda env=None: [])
    monkeypatch.setattr(gw, "_screen_port", lambda state: 8766)
    monkeypatch.setattr("isaac_mcp.client.discover_bridges", lambda: list(bridges))
    return gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})


def test_dead_endpoint_beside_a_live_bridge_names_both_and_does_not_say_wait(monkeypatch):
    server = _dead_port_server(monkeypatch, [OTHER])
    message = asyncio.run(gw._refresh_tools(server))
    assert message.startswith("Error: nothing answers on") and ":8766" in message
    assert "port 18722 (PID 4242, Isaac-Sim Python)" in message and "use_bridge(port=18722)" in message
    assert "probably still starting" not in message and "refresh_tools(wait_s" not in message
    assert "not Kit still starting" in message


def test_several_live_bridges_are_all_listed_lowest_port_first(monkeypatch):
    server = _dead_port_server(monkeypatch, [{**OTHER, "port": 18900, "pid": 2}, OTHER])
    message = asyncio.run(gw._refresh_tools(server))
    assert message.index("port 18722") < message.index("port 18900") and "use_bridge(port=18722)" in message


def test_the_bridge_on_the_configured_port_is_not_offered_as_another(monkeypatch):
    server = _dead_port_server(monkeypatch, [{**OTHER, "port": 8766}])
    message = asyncio.run(gw._refresh_tools(server))
    assert "use_bridge(port=8766)" not in message and "probably not running" in message


def test_with_nothing_advertised_the_starting_advice_stays(monkeypatch):
    server = _dead_port_server(monkeypatch, [])
    message = asyncio.run(gw._refresh_tools(server))
    assert "probably not running" in message and "wait_s=120" in message


def test_a_timeout_is_still_a_slow_start_not_a_wrong_port(monkeypatch):
    def timeout(server):
        raise TimeoutError("list_tools")

    monkeypatch.setattr(gw, "discover_and_register_tools", timeout)
    monkeypatch.setattr(gw, "live_sessions", lambda env=None: [])
    monkeypatch.setattr(gw, "_screen_port", lambda state: 8766)
    monkeypatch.setattr("isaac_mcp.client.discover_bridges", lambda: [OTHER])
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})
    assert "still starting" in asyncio.run(gw._refresh_tools(server))


def test_discovery_failing_cannot_cost_the_error_message(monkeypatch):
    server = _dead_port_server(monkeypatch, [])
    monkeypatch.setattr("isaac_mcp.client.discover_bridges", lambda: (_ for _ in ()).throw(OSError("nope")))
    assert asyncio.run(gw._refresh_tools(server)).startswith("Error:")

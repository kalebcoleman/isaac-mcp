"""Fixes from the Lab 3 student run: run_python await and session resets, clearer gateway
messages, and move_window."""

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

from isaac_mcp import screen
from isaac_mcp import server as gw
from isaac_mcp_bridge import builtins as bi
from isaac_mcp_bridge.protocol import ToolError
from isaac_mcp_bridge.sessions import SessionStore


@pytest.fixture
def kit_stubs(monkeypatch):
    """run_python imports carb, omni and pxr; stub the Kit-only ones."""
    pytest.importorskip("pxr")
    monkeypatch.setitem(sys.modules, "carb", types.ModuleType("carb"))
    monkeypatch.setitem(sys.modules, "omni", types.ModuleType("omni"))
    return types.SimpleNamespace(sessions=SessionStore())


def run(bridge, code, **kw):
    out = bi.cmd_run_python(bridge, code, **kw)
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


def test_plain_code_still_runs_synchronously(kit_stubs):
    out = bi.cmd_run_python(kit_stubs, "print('hi'); result = 2 + 2")
    assert out == {"output": "hi\n", "result": 4}


def test_top_level_await_is_awaited(kit_stubs):
    code = "import asyncio\nprint('a')\nawait asyncio.sleep(0)\nresult = 'done'"
    out = bi.cmd_run_python(kit_stubs, code)
    assert asyncio.iscoroutine(out)  # the bridge awaits it on Kit's loop
    assert asyncio.run(out) == {"output": "a\n", "result": "done"}


def test_errors_in_awaited_code_keep_output_and_traceback(kit_stubs):
    with pytest.raises(ToolError) as err:
        run(kit_stubs, "import asyncio\nprint('before')\nawait asyncio.sleep(0)\nraise ValueError('x')")
    assert err.value.details["output"] == "before\n" and "ValueError" in err.value.details["traceback"]


def test_syntax_errors_are_reported_cleanly(kit_stubs):
    with pytest.raises(ToolError, match="SyntaxError"):
        run(kit_stubs, "def (")


def test_persistent_session_reports_a_stage_open_reset(kit_stubs):
    run(kit_stubs, "import math\nhelper = 3", persistent=True, session_id="lab")
    assert run(kit_stubs, "result = helper", persistent=True, session_id="lab")["result"] == 3
    kit_stubs.sessions.clear()  # what the bridge does on every stage-open event
    with pytest.raises(ToolError) as err:
        run(kit_stubs, "result = helper", persistent=True, session_id="lab")
    assert "was reset because a stage was opened" in str(err.value)
    assert "session_reset" in err.value.details
    # Reported once: the next call is a normal call again.
    out = run(kit_stubs, "helper = 4\nresult = helper", persistent=True, session_id="lab")
    assert out["result"] == 4 and "session_reset" not in out


def test_successful_call_after_a_reset_says_so(kit_stubs):
    run(kit_stubs, "x = 1", persistent=True, session_id="s")
    kit_stubs.sessions.clear()
    out = run(kit_stubs, "result = 'fresh'", persistent=True, session_id="s")
    assert "was reset" in out["session_reset"]


def test_name_error_without_reset_explains_the_persistence_rule(kit_stubs):
    with pytest.raises(ToolError) as err:
        run(kit_stubs, "result = missing", persistent=True, session_id="t")
    assert "only the variables of successful calls" in err.value.details["hint"]


def test_refresh_tools_without_a_simulator_says_what_to_do(monkeypatch):
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})

    def offline(_server):
        raise ConnectionRefusedError("[Errno 111] Connection refused")

    monkeypatch.setattr(gw, "discover_and_register_tools", offline)
    message = asyncio.run(gw._refresh_tools(server))
    assert "no Isaac Sim bridge is reachable" in message and "sim__launch" in message


def test_host_run_asset_tools_are_not_reported_as_unconfirmed(monkeypatch):
    offline = types.SimpleNamespace(names={"assets__roots": "assets.roots", "assets__scan": "assets.scan",
                                           "assets__search": "assets.search"})
    monkeypatch.setattr(gw, "_offline_for", lambda server: offline)
    assert gw._offline_note(object()) == ""
    offline.names["sim__stage__info"] = "sim.stage.info"
    note = gw._offline_note(object())
    assert "sim: 1" in note and "assets" not in note


def test_move_window_unmaximizes_moves_and_reports_geometry(monkeypatch):
    from mcp.server.fastmcp import FastMCP

    disp = MagicMock()
    obj = disp.create_resource_object.return_value
    obj.get_geometry.return_value = types.SimpleNamespace(width=900, height=1000)
    disp.screen.return_value.root.translate_coords.return_value = types.SimpleNamespace(x=960, y=40)
    monkeypatch.setattr(screen, "_xdisplay", lambda: disp)
    monkeypatch.setattr(screen, "_match_window", lambda *_: {"id": 7, "title": "RViz", "pid": 5})
    monkeypatch.setattr(screen.xevent, "ClientMessage", lambda **kwargs: kwargs)
    server = FastMCP("screen-test")
    screen.register_screen_tools(server, groups={"screen_control"})
    tool = server._tool_manager._tools["move_window"].fn
    assert tool(x=960, y=40, width=900, height=1000, window_id=7) == "moved: RViz (id=7) now 900x1000+960+40"
    sent = [c.args[0] for c in disp.screen.return_value.root.send_event.call_args_list]
    assert len(sent) == 2 and sent[1]["data"][1][1:] == [960, 40, 900, 1000]
    with pytest.raises(ValueError, match="PID"):
        tool(x=0, y=0, width=900, height=900, window_id=7, expected_pid=99)
    with pytest.raises(ValueError, match="100..16384"):
        tool(x=0, y=0, width=10, height=900, window_id=7)

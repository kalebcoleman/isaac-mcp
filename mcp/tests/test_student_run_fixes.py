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


@pytest.fixture(autouse=True)
def _no_leftover_stdout_proxy():
    yield
    bi.remove_stdout_proxy()


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
    assert tool(x=960, y=40, width=900, height=1000, window_id=7).startswith("moved: RViz (id=7) now 900x1000+960+40")
    sent = [c.args[0] for c in disp.screen.return_value.root.send_event.call_args_list]
    assert len(sent) == 2 and sent[1]["data"][1][1:] == [960, 40, 900, 1000]
    with pytest.raises(ValueError, match="PID"):
        tool(x=0, y=0, width=900, height=900, window_id=7, expected_pid=99)
    with pytest.raises(ValueError, match="100..16384"):
        tool(x=0, y=0, width=10, height=900, window_id=7)


def test_a_stage_opened_during_an_awaiting_call_is_not_saved(kit_stubs):
    run(kit_stubs, "old_prim = 'stale'", persistent=True, session_id="lab")
    async def scenario():
        async def stage_open_event():  # Kit delivers the stage-open event while the call awaits
            await asyncio.sleep(0)
            kit_stubs.sessions.clear()

        task = asyncio.ensure_future(stage_open_event())
        code = "import asyncio\nfresh = 'new'\nawait asyncio.sleep(0.01)\nresult = old_prim"
        out = await bi.cmd_run_python(kit_stubs, code, persistent=True, session_id="lab")
        await task
        return out

    out = asyncio.run(scenario())
    assert out["session_vars"] == [] and "was not saved" in out["session_reset"]
    with pytest.raises(ToolError) as err:  # nothing stale came back, and the reset was reported once
        run(kit_stubs, "result = old_prim", persistent=True, session_id="lab")
    assert "session_reset" not in err.value.details and "only the variables of successful calls" in err.value.details["hint"]


def test_awaiting_code_captures_only_its_own_prints(kit_stubs, capsys):
    import io

    async def scenario():
        other = []

        async def kit_task():  # another task printing while run_python is suspended
            await asyncio.sleep(0)
            print("kit says hi")
            other.append(True)

        task = asyncio.ensure_future(kit_task())
        out = await bi.cmd_run_python(kit_stubs, "import asyncio\nprint('mine')\nawait asyncio.sleep(0.01)\nresult=1")
        await task
        return out

    out = asyncio.run(scenario())
    assert out["output"] == "mine\n"
    assert "kit says hi" in capsys.readouterr().out
    print("after")  # stdout still works normally afterwards
    assert "after" in capsys.readouterr().out


def test_a_cancelled_call_keeps_the_reset_notice_for_the_next_one(kit_stubs):
    run(kit_stubs, "x = 1", persistent=True, session_id="c")
    kit_stubs.sessions.clear()

    async def cancelled():
        coro = bi.cmd_run_python(kit_stubs, "import asyncio\nawait asyncio.sleep(10)", persistent=True, session_id="c")
        task = asyncio.ensure_future(coro)
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancelled())
    assert "was reset" in run(kit_stubs, "result = 2", persistent=True, session_id="c")["session_reset"]


def test_refresh_tools_with_several_bridges_says_pick_one(monkeypatch):
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})

    def several(_server):
        raise RuntimeError("multiple bridges running: ports 8766, 8781.")

    monkeypatch.setattr(gw, "discover_and_register_tools", several)
    message = asyncio.run(gw._refresh_tools(server))
    assert "use_bridge" in message and "do not start another" in message and "not reachable" not in message


def test_move_window_waits_for_a_position_only_move(monkeypatch):
    from mcp.server.fastmcp import FastMCP

    disp = MagicMock()
    obj = disp.create_resource_object.return_value
    obj.get_geometry.return_value = types.SimpleNamespace(width=900, height=1000)
    positions = iter([types.SimpleNamespace(x=0, y=0)] * 2 + [types.SimpleNamespace(x=960, y=40)] * 50)
    disp.screen.return_value.root.translate_coords.side_effect = lambda *a: next(positions)
    monkeypatch.setattr(screen, "_xdisplay", lambda: disp)
    monkeypatch.setattr(screen, "_match_window", lambda *_: {"id": 7, "title": "RViz", "pid": 5})
    monkeypatch.setattr(screen.xevent, "ClientMessage", lambda **kwargs: kwargs)
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)
    server = FastMCP("screen-test")
    screen.register_screen_tools(server, groups={"screen_control"})
    out = server._tool_manager._tools["move_window"].fn(x=960, y=40, width=900, height=1000, window_id=7)
    assert "now 900x1000+960+40" in out  # not the stale +0+0 read right after sending


def test_tasks_started_by_the_code_print_to_the_console_after_the_call(kit_stubs, capsys):
    async def scenario():
        code = ("import asyncio\nasync def monitor():\n    await asyncio.sleep(0.02)\n    print('later')\n"
                "started = asyncio.ensure_future(monitor())\nprint('now')\nawait asyncio.sleep(0)\nresult = 1")
        out = await bi.cmd_run_python(kit_stubs, code, persistent=True, session_id="m")
        await asyncio.sleep(0.05)  # the monitor prints after the call returned
        return out

    out = asyncio.run(scenario())
    assert out["output"] == "now\n"
    assert "later" in capsys.readouterr().out


def test_proxy_handles_writelines_and_is_removed_on_shutdown(kit_stubs, capsys):
    out = run(kit_stubs, "import sys, asyncio\nsys.stdout.writelines(['a\\n', 'b\\n'])\nawait asyncio.sleep(0)")
    assert out["output"] == "a\nb\n"
    assert getattr(sys.stdout, "_isaac_mcp_proxy", False)
    bi.remove_stdout_proxy()
    assert not getattr(sys.stdout, "_isaac_mcp_proxy", False)

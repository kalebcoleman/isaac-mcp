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


def test_refresh_tools_names_managed_simulators_on_other_ports(monkeypatch):
    # Student run 3 (lab PC): the config pinned port 8766, sim__launch used 8771, and refresh_tools
    # kept saying "no bridge" until the agent found use_bridge on its own.
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "gateway"})

    def offline(_server):
        raise ConnectionRefusedError("[Errno 111] Connection refused")

    monkeypatch.setattr(gw, "discover_and_register_tools", offline)
    monkeypatch.setattr(gw, "_screen_port", lambda state=None: 8766)
    import time
    monkeypatch.setattr(gw, "live_sessions", lambda env=None: [
        {"port": 8766, "pid": 10, "started_at": time.time() - 95},
        {"port": 8771, "pid": 342055, "started_at": None}])
    message = asyncio.run(gw._refresh_tools(server))
    assert "port 8771 (PID 342055; call use_bridge(port) to select it)" in message
    # PR D: the configured port's own session is named too, with how long it has been starting.
    assert "port 8766 (PID 10, started 9" in message and "the configured port" in message
    assert "still starting" in message and "refresh_tools(wait_s=" in message
    monkeypatch.setattr(gw, "live_sessions", lambda env=None: [])
    assert "Managed simulators" not in asyncio.run(gw._refresh_tools(server))


def test_locked_screen_is_detected_from_loginctl_or_the_screensaver(monkeypatch):
    import itertools

    from isaac_mcp import screen

    ticks = itertools.count(100, 10)  # each call is 10 s after the last: no cache hits

    outputs = {}
    monkeypatch.setattr(screen, "_LOCK_CACHE", [-1e9, False])
    monkeypatch.setattr(screen.time, "monotonic", lambda: 0.0 + len(outputs) * 0 + next(ticks))
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1/bus")
    monkeypatch.setattr(screen.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr(screen.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(stdout=outputs.get(cmd[0], "")))
    monkeypatch.setenv("XDG_SESSION_ID", "3")
    outputs.update(loginctl="yes\n")
    assert screen._screen_locked()
    outputs.update(loginctl="no", gdbus="(true,)")
    assert screen._screen_locked()
    outputs.update(gdbus="(false,)")
    assert not screen._screen_locked()
    monkeypatch.setattr(screen.shutil, "which", lambda cmd: None)
    assert not screen._screen_locked()


def test_lock_probe_is_cached_and_skips_gdbus_without_a_session_bus(monkeypatch):
    from isaac_mcp import screen

    calls = []
    monkeypatch.setattr(screen.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr(screen.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd[0]) or types.SimpleNamespace(stdout="(true,)"))
    monkeypatch.delenv("XDG_SESSION_ID", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/nonexistent-runtime-dir")
    monkeypatch.setattr(screen, "_LOCK_CACHE", [-1e9, False])
    assert not screen._screen_locked() and calls == []  # no bus: no gdbus autolaunch
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1/bus")
    assert not screen._screen_locked()  # cached for 5 s
    monkeypatch.setattr(screen, "_LOCK_CACHE", [-1e9, False])
    assert screen._screen_locked() and calls == ["gdbus"]


def test_monitor_captures_warn_when_locked_and_window_captures_do_not(monkeypatch, tmp_path):
    from mcp.server.fastmcp import FastMCP

    from isaac_mcp import screen

    class FakeShot:
        size = (4, 2)
        bgra = bytes(4 * 2 * 4)

    class FakeMSS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def grab(self, bbox):
            return FakeShot()

    monkeypatch.setattr(screen, "_screen_locked", lambda: True)
    monkeypatch.setattr(screen, "_composite", lambda bbox: (screen.PILImage.new("RGB", (4, 2)), 1))
    monkeypatch.setattr(screen, "SHOT_DIR", tmp_path)
    monkeypatch.setattr(screen.mss, "MSS", FakeMSS)
    monkeypatch.setattr(screen, "_bbox", lambda m=0: {"left": 0, "top": 0, "width": 4, "height": 2})
    window = {"id": 7, "title": "Isaac Sim", "x": 0, "y": 0, "width": 4, "height": 2}
    monkeypatch.setattr(screen, "_match_window", lambda *a, **k: window)
    monkeypatch.setattr(screen, "_grab_window_image", lambda win: screen.PILImage.new("RGB", (4, 2)))
    server = FastMCP("t")
    screen.register_screen_tools(server)
    shot = server._tool_manager._tools["screenshot"].fn
    assert "screen is locked" in shot(monitor=1)[1]
    assert "screen is locked" not in shot(window_id=7)[1]


def _ui_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "exts/sim-tools/sim_tools/ui.py"
    spec = importlib.util.spec_from_file_location("sim_tools_ui_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ui_keys_follow_openai_style_names():
    ui = _ui_module()
    assert ui.parse_keys(["ctrl", "s"]) == (["CONTROL"], "S")
    assert ui.parse_keys(["Enter"]) == ([], "ENTER")
    assert ui.parse_keys(["shift", "alt", "tab"]) == (["SHIFT", "ALT"], "TAB")
    assert ui.parse_keys(["7"]) == ([], "KEY_7")
    assert ui.parse_keys(["f5"]) == ([], "F5")
    for bad in ([], ["ctrl"], ["a", "b"], ["ctrl", "ctrl", "a"], ["hyper", "a"], "ctrl+s", [""] ):
        with pytest.raises(ui.UIRefusal):
            ui.parse_keys(bad)


def test_ui_widget_records_are_in_window_pixels_and_match_by_text():
    ui = _ui_module()
    rec = ui.widget_record("ToolButton", "Main ToolBar//Frame/VStack[0]/ToolButton[5]", "Main ToolBar", "play", "",
                           "Play ('Space')", None, (20, 302), (34, 34), 1.5, identifier="play")
    assert (rec["x"], rec["y"], rec["width"]) == (30, 453, 51)
    assert ui.matches(rec, "PLAY") and ui.matches(rec, "space") and not ui.matches(rec, "stop")
    assert ui.matches(rec, "") and "value" not in rec and "enabled" not in rec
    field = ui.widget_record("StringField", "r", "Stage", "", "", "", "Cube", (1, 1), (2, 2), 1.0, enabled=False)
    assert field["value"] == "Cube" and field["enabled"] is False and ui.matches(field, "cube")


def test_locked_screenshot_is_composed_bottom_to_top_without_wm_frames():
    from isaac_mcp import screen

    wins = [{"id": 1, "class": "Isaac"}, {"id": 2, "class": "mutter-x11-frames"}, {"id": 3, "class": "rviz2"},
            {"id": 4, "class": "unmanaged"}]
    order = [w["id"] for w in screen.composite_order(wins, stacking=[3, 1])]
    assert order == [3, 1, 4]  # unmanaged (popups, tooltips) above the managed windows


def test_lock_probe_is_unknown_when_only_loginctl_says_no(monkeypatch):
    # Started over SSH, XDG_SESSION_ID is the SSH session: "no" says nothing about the desktop.
    from isaac_mcp import screen

    outputs = {"loginctl": "no", "gdbus": ""}
    monkeypatch.setattr(screen.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr(screen.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(stdout=outputs[cmd[0]]))
    monkeypatch.setenv("XDG_SESSION_ID", "7")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1/bus")
    assert screen._probe_locked() is None
    outputs["gdbus"] = "(false,)"
    assert screen._probe_locked() is False
    outputs["gdbus"] = "(true,)"
    assert screen._probe_locked() is True


def test_ui_refs_carry_a_fingerprint_of_the_widget():
    ui = _ui_module()
    fp = ui.fingerprint("ToolButton", identifier="play")
    assert fp == "ToolButton:play"
    assert ui.fingerprint("Label", text="Cube") == "Label:Cube"
    assert ui.fingerprint("StringField", text="typed text") == "StringField:"  # values change; not identity
    assert ui.fingerprint("Button", name="a|b") == "Button:a/b"
    assert ui.fingerprint("FloatField", context="Translate") == "FloatField:@Translate"
    assert ui.fingerprint("FloatField", identifier="x", context="Translate") == "FloatField:x"
    ref = "Main ToolBar//Frame/VStack[0]/ToolButton[5]|" + fp
    assert ui.split_ref(ref) == ("Main ToolBar//Frame/VStack[0]/ToolButton[5]", fp)
    assert ui.split_ref("Main ToolBar//Frame/VStack[0]/ToolButton[5]") == (
        "Main ToolBar//Frame/VStack[0]/ToolButton[5]", None)
    assert ui.inside((5, 5), (0, 0, 10, 10)) and not ui.inside((10, 5), (0, 0, 10, 10))


def test_property_fields_are_fingerprinted_by_the_attribute_they_edit():
    # Round 2 of #62: every Property value field is identifier "value"; after selecting another prim the
    # same path is another prim's field. The bound USD attribute and channel tell them apart.
    ui = _ui_module()

    class Model:
        def __init__(self, path, channel):
            self.path, self._channel_index = path, channel

        def get_attribute_paths(self):
            return [self.path]

    class FloatDrag:
        identifier, name, text = "value", "value", ""

        def __init__(self, path, channel):
            self.model = Model(path, channel)

    cube = ui._widget_fingerprint(FloatDrag("/Cube.xformOp:translate", 1), "Property//Frame/x", None)
    sphere = ui._widget_fingerprint(FloatDrag("/Sphere.xformOp:translate", 1), "Property//Frame/x", None)
    assert cube == "FloatDrag:value@/Cube.xformOp:translate#1" and cube != sphere
    assert ui.split_ref("Property//Frame/x|" + cube)[1] == cube

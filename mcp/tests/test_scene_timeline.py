"""Passive native Stop observation and the actual asynchronous tool boundary."""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from isaac_mcp.project_assets import AssetError
from sim_tools import timeline


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def native(monkeypatch):
    state = SimpleNamespace(name="STOPPING")
    orchestrator = SimpleNamespace(get_status=lambda: state)
    monkeypatch.setitem(sys.modules, "omni.replicator.core", SimpleNamespace(orchestrator=orchestrator))
    return state


@pytest.mark.anyio
async def test_waits_for_render_drain_then_reobserves_stopped(native):
    observations = []

    async def update():
        observations.append(native.name)
        if len(observations) == 2:
            native.name = "STOPPED"

    validated = []
    result = await timeline.settle_stop(update, lambda: validated.append(native.name))
    assert observations == ["STOPPING", "STOPPING", "STOPPED"]
    assert result == {"observed": "STOPPED", "updates": 3}
    assert validated[-1] == "STOPPED"


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["STOPPED", "STARTED", "STARTING", "PAUSED", "STEPPED", "STEPPING", "LOADING", "INITIALIZING", None])
async def test_does_not_drive_unrelated_or_absent_orchestrator(native, monkeypatch, status):
    native.name = status
    if status is None:
        monkeypatch.delitem(sys.modules, "omni.replicator.core")

    def update():
        pytest.fail("No pending native Stop; no extra update or orchestrator mutation")

    assert await timeline.settle_stop(update, lambda: None) == {"observed": status, "updates": 0}


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["stage", "timeline", "orchestrator", "unloaded"])
async def test_changed_target_or_native_operation_aborts(native, monkeypatch, change):
    changed = False

    async def update():
        nonlocal changed
        changed = True
        if change == "orchestrator":
            native.name = "STARTED"
        elif change == "unloaded":
            monkeypatch.delitem(sys.modules, "omni.replicator.core")

    def validate():
        if changed and change in {"stage", "timeline"}:
            raise AssetError("STALE_TARGET" if change == "stage" else "TIMELINE_INTERRUPTED", change)

    with pytest.raises(AssetError) as error:
        await timeline.settle_stop(update, validate)
    assert error.value.code == ("STALE_TARGET" if change == "stage" else "TIMELINE_INTERRUPTED")


@pytest.mark.anyio
async def test_unfinished_native_stop_has_bounded_timeout(native):
    never = asyncio.get_running_loop().create_future()
    with pytest.raises(AssetError) as error:
        await timeline.settle_stop(lambda: never, lambda: None, timeout=.001)
    assert error.value.code == "TIMELINE_BUSY"


@pytest.mark.anyio
async def test_fast_unfinished_frames_are_bounded(native):
    frames = []

    async def update():
        frames.append(1)

    with pytest.raises(AssetError) as error:
        await timeline.settle_stop(update, lambda: None, timeout=10)
    assert error.value.code == "TIMELINE_BUSY" and len(frames) == 1200


@pytest.mark.anyio
async def test_cancellation_does_not_issue_any_native_action(native):
    frame = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(timeline.settle_stop(lambda: frame, lambda: None))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert native.name == "STOPPING"


@pytest.mark.parametrize("api", [None, SimpleNamespace(get_status=lambda: object()), SimpleNamespace(get_status=lambda: SimpleNamespace(name="FUTURE"))])
def test_missing_or_unknown_loaded_api_refuses(monkeypatch, api):
    monkeypatch.setitem(sys.modules, "omni.replicator.core", SimpleNamespace(orchestrator=api))
    with pytest.raises(AssetError) as error:
        timeline.native_status()
    assert error.value.code == "TIMELINE_API_UNAVAILABLE"


@pytest.fixture
def handler(monkeypatch, native):
    pytest.importorskip("pxr.Usd")
    modules = {}
    for name in ["omni", "omni.ext", "omni.usd", "omni.timeline", "omni.kit", "omni.kit.app", "isaac_mcp_bridge"]:
        modules[name] = ModuleType(name)
        monkeypatch.setitem(sys.modules, name, modules[name])
        if "." in name:
            parent, child = name.rsplit(".", 1)
            setattr(modules[parent], child, modules[name])
    modules["omni.ext"].IExt = object
    modules["isaac_mcp_bridge"].get_mcp_bridge = lambda: None
    modules["isaac_mcp_bridge"].ToolDefinition = object
    path = Path(__file__).resolve().parents[2] / "exts/sim-tools/sim_tools/extension.py"
    spec = importlib.util.spec_from_file_location("sim_tools._test_timeline_extension", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = SimpleNamespace(mode="stop", frames=0, calls=[], changed=False, interrupt=False, restored=0)

    async def update():
        await asyncio.sleep(0)
        state.frames += 1
        if native.name == "STOPPING" and state.frames >= 2:
            native.name = "STOPPED"
            state.mode = "stop"
        if state.interrupt:
            state.mode = "stop"

    def target(session, stage):
        if (session, stage) != ("session", "stage") or state.changed:
            raise AssetError("STALE_TARGET", "Stage changed")

    def restore(stage):
        state.restored += 1
        return True

    ext = module.Extension()
    ext._operation_lock = asyncio.Lock()
    ext.scene = SimpleNamespace(
        target=target, stage=lambda: "stage", end_runtime_edit=restore,
        timeline=SimpleNamespace(is_stopped=lambda: state.mode == "stop", is_playing=lambda: state.mode == "play", get_current_time=lambda: 0),
    )

    def dispatch(name, args, **kw):
        target(args.pop("session_id"), args.pop("stage_id"))
        state.calls.append((args["action"], state.frames, native.name))
        state.mode = args["action"]
        if state.mode == "stop":
            native.name = "STOPPING"
        return {"action": args["action"]}

    ext.dispatch = dispatch
    modules["omni.kit.app"].get_app = lambda: SimpleNamespace(next_update_async=update)
    return ext.handler("sim.timeline.control"), state


@pytest.mark.anyio
async def test_actual_handler_waits_before_one_play(handler, native):
    call, state = handler
    result = await call(session_id="session", stage_id="stage", action="play")
    assert result["status"] == "ok" and result["playing"]
    assert state.calls == [("play", 3, "STOPPED")]


@pytest.mark.anyio
async def test_actual_stop_returns_after_native_drain(handler, native):
    call, state = handler
    state.mode = "play"
    native.name = "STARTED"
    result = await call(session_id="session", stage_id="stage", action="stop")
    assert result["status"] == "ok" and result["stopped"]
    assert result["native_stop_observation"] == {"observed": "STOPPED", "updates": 2}
    assert state.restored == 1 and len(state.calls) == 1


@pytest.mark.anyio
async def test_actual_handler_returns_observed_failure_without_replay(handler, native):
    call, state = handler
    native.name = "STOPPED"
    state.interrupt = True
    result = await call(session_id="session", stage_id="stage", action="play")
    assert result["status"] == "error" and result["code"] == "TIMELINE_INTERRUPTED"
    assert result["observed_state"]["stopped"] and len(state.calls) == 1


@pytest.mark.anyio
async def test_stale_target_never_issues_play(handler):
    call, state = handler
    result = await call(session_id="session", stage_id="old-stage", action="play")
    assert result["code"] == "STALE_TARGET" and not state.calls

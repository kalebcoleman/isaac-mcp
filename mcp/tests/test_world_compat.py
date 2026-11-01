"""The constructor override is synchronous and restored even on native failure."""

import builtins
import importlib.util
from pathlib import Path
import pytest

path = Path(__file__).resolve().parents[2] / "exts/ur5e-dt/ur5e_dt/world_compat.py"
spec = importlib.util.spec_from_file_location("world_compat_test", path)
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


@pytest.mark.parametrize("original", [True, False, None])
@pytest.mark.parametrize("fail", [False, True])
def test_world_constructor_uses_extension_mode_and_restores_flag(
    monkeypatch, original, fail
):
    flag = "ISAAC_LAUNCHED_FROM_TERMINAL"
    if original is None:
        monkeypatch.delattr(builtins, flag, raising=False)
    else:
        monkeypatch.setattr(builtins, flag, original, raising=False)

    def constructor():
        assert getattr(builtins, flag, None) == (None if original is None else True)
        if fail:
            raise RuntimeError("native constructor failed")
        return "world"

    if fail:
        with pytest.raises(RuntimeError, match="native constructor"):
            compat.extension_world(constructor)
    else:
        assert compat.extension_world(constructor) == "world"
    assert getattr(builtins, flag, None) is original
    if original is None:
        assert not hasattr(builtins, flag)


@pytest.mark.parametrize(
    "behavior", ["none", "not_initialized", "ready", "native_failure"]
)
def test_async_initialization_handles_old_and_new_context_contracts(behavior):
    import asyncio

    calls = []

    class World:
        def get_physics_context(self):
            if behavior == "native_failure":
                raise RuntimeError("unexpected native failure")
            if behavior == "not_initialized":
                raise RuntimeError(
                    "Physics context is not initialized. Call reset_async()"
                )
            return object() if behavior == "ready" else None

        async def initialize_simulation_context_async(self):
            calls.append("initialize")

    if behavior == "native_failure":
        with pytest.raises(RuntimeError, match="unexpected native"):
            asyncio.run(compat.initialized_extension_world(World))
    else:
        assert isinstance(asyncio.run(compat.initialized_extension_world(World)), World)
    assert calls == (["initialize"] if behavior in ("none", "not_initialized") else [])


@pytest.mark.parametrize("initial", ["playing", "paused", "stopped"])
@pytest.mark.parametrize("failure", [None, "native", "stage", "stop"])
def test_scene_edit_preserves_state_and_does_not_resume_replaced_stage(initial, failure):
    import asyncio

    state = {"timeline": initial, "stage": object(), "updates": 0}
    original_stage = state["stage"]
    calls = []

    class Timeline:
        def is_playing(self):
            return state["timeline"] == "playing"

        def is_stopped(self):
            return state["timeline"] == "stopped"

        def pause(self):
            state["timeline"] = "paused"
            calls.append("pause")

        def play(self):
            state["timeline"] = "playing"
            calls.append("play")

    class App:
        async def next_update_async(self):
            state["updates"] += 1
            await asyncio.sleep(0)

    async def exercise():
        async with compat.paused_scene_edit(Timeline(), App(), lambda: state["stage"]):
            assert state["timeline"] != "playing"
            if failure == "native":
                raise RuntimeError("native import failed")
            if failure == "stage":
                state["stage"] = object()
            if failure == "stop":
                state["timeline"] = "stopped"

    if failure in ("native", "stage"):
        with pytest.raises(RuntimeError, match="native import|Stage changed"):
            asyncio.run(exercise())
    else:
        asyncio.run(exercise())
    if failure == "stage":
        assert state["stage"] != original_stage
        assert "play" not in calls
    elif failure == "stop":
        assert state["timeline"] == "stopped"
        assert "play" not in calls
    else:
        assert state["timeline"] == initial
        assert calls == (["pause", "play"] if initial == "playing" else [])
        if not failure:
            assert state["updates"] >= 4

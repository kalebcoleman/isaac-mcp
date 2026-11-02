"""The constructor override is synchronous and restored even on native failure."""

import builtins
import importlib.util
from pathlib import Path
import pytest

path = Path(__file__).resolve().parents[2] / "exts/ur5e-dt/ur5e_dt/world_compat.py"
spec = importlib.util.spec_from_file_location("world_compat_test", path)
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


@pytest.mark.parametrize("failure", [None, "native", "stage", "target", "cancel"])
def test_twin_play_normalization_uses_session_and_respects_external_changes(failure):
    import asyncio
    pytest.importorskip("pxr")
    from pxr import Usd
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim("/Existing", "Xform")
    before = stage.GetRootLayer().ExportToString()
    original = stage.GetEditTarget()
    current = [stage]

    def exercise():
        with compat.twin_play_edit(stage, lambda: current[0]) as validate:
            assert stage.GetEditTarget().GetLayer() == stage.GetSessionLayer()
            stage.DefinePrim("/Runtime", "Xform")
            if failure == "native":
                raise RuntimeError("native failed")
            if failure == "cancel":
                raise asyncio.CancelledError()
            if failure == "stage":
                current[0] = Usd.Stage.CreateInMemory()
            if failure == "target":
                # External targets need to belong to this stage's local stack.
                stage.SetEditTarget(original)
            validate()

    if failure:
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else RuntimeError):
            exercise()
    else:
        exercise()
    assert stage.GetEditTarget() == original
    assert stage.GetRootLayer().ExportToString() == before
    assert stage.GetSessionLayer().GetPrimAtPath("/Runtime")
    if failure == "stage":
        assert current[0].GetEditTarget().GetLayer() == current[0].GetRootLayer()


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


@pytest.mark.parametrize("applied_device", ["cpu", "cuda:0"])
def test_twin_applies_cpu_before_checking_new_world_device(applied_device):
    import asyncio

    events = []

    class World:
        @classmethod
        def instance(cls):
            return None

        def __init__(self, *, device):
            events.append(("construct", device))
            self.requested_device = device
            # Native property reads the global manager until initialization.
            self.device = "cuda:0"
            self.engine = "newton"

        def get_physics_context(self):
            return None

        async def initialize_simulation_context_async(self):
            events.append(("initialize", self.requested_device))
            self.device = applied_device

    if applied_device == "cpu":
        world = asyncio.run(compat.initialized_twin_world(World))
        assert world.device == "cpu"
        assert world.engine == "newton"
    else:
        with pytest.raises(RuntimeError, match="partially initialized"):
            asyncio.run(compat.initialized_twin_world(World))
    assert events == [("construct", "cpu"), ("initialize", "cpu")]


@pytest.mark.parametrize("device", ["cpu", "cuda:0", "cuda:1", None])
@pytest.mark.parametrize("ready", [False, True, "raises"])
def test_existing_world_is_inspected_without_constructor_or_reconfiguration(device, ready):
    import asyncio
    from types import SimpleNamespace

    def context():
        if ready == "raises":
            raise RuntimeError("Physics context is not initialized")
        return object() if ready else None

    async def initialize():
        pytest.fail("An existing context must not be initialized")

    existing = SimpleNamespace(
        device=device,
        get_physics_context=context,
        initialize_simulation_context_async=initialize,
    )

    class World:
        @classmethod
        def instance(cls):
            return existing

        def __init__(self, **kwargs):
            pytest.fail("Do not invoke a singleton constructor for an existing World")

    if device == "cpu" and ready is True:
        assert asyncio.run(compat.initialized_twin_world(World)) is existing
    else:
        with pytest.raises(RuntimeError, match="was not reconfigured"):
            asyncio.run(compat.initialized_twin_world(World))
    assert existing.device == device


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

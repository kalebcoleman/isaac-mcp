"""Real USD lease restoration across external timeline and stage transitions."""

import asyncio
from pathlib import Path
import sys

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import Sdf, Usd
from sim_tools.runtime_edit import RuntimeEditRestorer, stop_edit_target_guard
from test_scene_usd import scene  # noqa: F401 - shared real-USD fixture


@pytest.fixture
def anyio_backend():
    return "asyncio"


def restorer(scene):
    frame = asyncio.get_running_loop().create_future()
    errors = []
    return RuntimeEditRestorer(scene, lambda: frame, errors.append), frame, errors


def test_stop_guard_allows_own_restoration_once_and_preserves_external_target(scene):
    from isaac_mcp.project_assets import AssetError

    stage = scene.stage()
    initial = stage.GetEditTarget()
    scene.begin_runtime_edit(stage)
    guard = stop_edit_target_guard(scene, stage)
    guard()
    scene.end_runtime_edit(stage)
    guard()
    assert stage.GetEditTarget() == initial
    stage.SetEditTarget(stage.GetSessionLayer())
    with pytest.raises(AssetError) as error:
        guard()
    assert error.value.code == "EDIT_TARGET_CHANGED"
    assert stage.GetEditTarget().GetLayer() == stage.GetSessionLayer()


def test_stop_guard_rejects_target_chosen_while_lease_is_being_restored(scene):
    from isaac_mcp.project_assets import AssetError

    stage = scene.stage()
    scene.begin_runtime_edit(stage)
    guard = stop_edit_target_guard(scene, stage)
    external = Sdf.Layer.CreateAnonymous()
    stage.GetRootLayer().subLayerPaths.append(external.identifier)
    stage.SetEditTarget(external)
    scene.end_runtime_edit(stage)
    with pytest.raises(AssetError) as error:
        guard()
    assert error.value.code == "EDIT_TARGET_CHANGED"
    assert stage.GetEditTarget().GetLayer() == external


@pytest.mark.anyio
async def test_external_stop_restores_only_after_native_frame(scene):
    stage = scene.stage()
    root_before = stage.GetRootLayer().ExportToString()
    original = stage.GetEditTarget()
    scene.begin_runtime_edit(stage)
    watcher, frame, errors = restorer(scene)
    watcher.stopped()
    await asyncio.sleep(0)
    assert stage.GetEditTarget().GetLayer() == stage.GetSessionLayer()
    frame.set_result(None)
    await watcher.task
    assert stage.GetEditTarget() == original
    assert scene._runtime_edit_target is None and not errors
    assert stage.GetRootLayer().ExportToString() == root_before


@pytest.mark.anyio
@pytest.mark.parametrize("interruption", ["play", "stage", "lease", "target"])
async def test_delayed_stop_does_not_clobber_new_state(scene, interruption):
    stage = scene.stage()
    scene.begin_runtime_edit(stage)
    watcher, frame, errors = restorer(scene)
    watcher.stopped()
    if interruption == "play":
        scene.timeline.is_stopped = lambda: False
    elif interruption == "stage":
        new = Usd.Stage.CreateInMemory()
        scene.get_stage = lambda: new
    elif interruption == "lease":
        scene.end_runtime_edit(stage)
        scene.begin_runtime_edit(stage)
    else:
        layer = Sdf.Layer.CreateAnonymous()
        stage.GetRootLayer().subLayerPaths.append(layer.identifier)
        stage.SetEditTarget(layer)
    expected = stage.GetEditTarget()
    lease = scene._runtime_edit_target
    frame.set_result(None)
    await watcher.task
    assert stage.GetEditTarget() == expected and not errors
    assert scene._runtime_edit_target is (None if interruption == "target" else lease)


@pytest.mark.anyio
async def test_restart_closing_or_shutdown_cancels_pending_restore(scene):
    stage = scene.stage()
    scene.begin_runtime_edit(stage)
    watcher, frame, errors = restorer(scene)
    watcher.stopped()
    task = watcher.task
    watcher.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert watcher.task is None and not errors
    assert stage.GetEditTarget().GetLayer() == stage.GetSessionLayer()
    # CLOSING/shutdown subsequently release the old stage synchronously.
    scene.close_stage()
    assert stage.GetEditTarget().GetLayer() == stage.GetRootLayer()


@pytest.mark.anyio
async def test_partial_native_failure_is_reported_and_lease_retryable(scene, monkeypatch):
    stage = scene.stage()
    scene.begin_runtime_edit(stage)
    watcher, frame, errors = restorer(scene)
    original = Usd.Stage.SetEditTarget

    def fail(*args):
        raise RuntimeError("native target failure")

    monkeypatch.setattr(Usd.Stage, "SetEditTarget", fail)
    watcher.stopped()
    frame.set_result(None)
    await watcher.task
    assert scene._runtime_edit_target is not None
    assert len(errors) == 1 and "native target failure" in errors[0]
    monkeypatch.setattr(Usd.Stage, "SetEditTarget", original)
    assert scene.end_runtime_edit(stage)
    assert stage.GetEditTarget().GetLayer() == stage.GetRootLayer()


@pytest.mark.anyio
async def test_stop_without_lease_is_noop(scene):
    watcher, frame, errors = restorer(scene)
    watcher.stopped()
    assert watcher.task is None and not errors
    scene.stage()
    watcher.stopped()
    assert watcher.task is None and not errors

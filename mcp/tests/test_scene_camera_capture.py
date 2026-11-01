"""CPU transfer completion, bounds and cancellation; live pixels are a Kit gate."""

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from sim_tools.camera_capture import (
    Readback,
    RenderFrames,
    cached,
    capture,
    invalidate,
    wait_for_frames,
)
from isaac_mcp.project_assets import AssetError


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def native(monkeypatch):
    calls = []
    texture = SimpleNamespace(
        frame=100,
        get_frame_info=lambda handle: {"frame_number": texture.frame},
        get_event_key=lambda: {"texture": 7},
        get_aov_info=lambda *a, **kw: [{"texture": {"rp_resource": "owned-rgb"}}],
    )
    renderer = SimpleNamespace(
        capture_next_frame_rp_resource_callback=lambda callback, resource: calls.append(
            (callback, resource)
        )
    )
    values = [42] * (64 * 64 * 4)
    convert = lambda *args: values.copy()
    state = SimpleNamespace(
        texture=texture, renderer=renderer, calls=calls, values=values, reset=False
    )

    def observe(**kwargs):
        assert kwargs["filter"] == {"texture": 7}
        state.callback = kwargs["on_event"]
        return SimpleNamespace(reset=lambda: setattr(state, "reset", True))

    async def update():
        texture.frame += 1
        state.callback({"result_handle": texture.frame})
        if calls and not getattr(state, "stall", False):
            calls[-1][0](object(), len(values), 64, 64, "TextureFormat.RGBA8_UNORM")
        await asyncio.sleep(0)

    app = SimpleNamespace(get_app=lambda: SimpleNamespace(next_update_async=update))
    ht = SimpleNamespace(GLOBAL_EVENT_DRAWABLE_CHANGED="drawable")
    rc = SimpleNamespace(
        acquire_renderer_capture_interface=lambda: renderer,
        convert_raw_bytes_to_list=convert,
    )
    event = SimpleNamespace(
        get_eventdispatcher=lambda: SimpleNamespace(observe_event=observe)
    )
    for name, module in {
        "omni": SimpleNamespace(
            hydratexture=ht, kit=SimpleNamespace(app=app, renderer_capture=rc)
        ),
        "omni.hydratexture": ht,
        "omni.kit": SimpleNamespace(app=app, renderer_capture=rc),
        "omni.kit.app": app,
        "omni.kit.renderer_capture": rc,
        "carb": SimpleNamespace(eventdispatcher=event),
        "carb.eventdispatcher": event,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    state.sensor = dict(
        product=SimpleNamespace(hydra_texture=texture),
        resolution=(64, 64),
        product_path="/Render/Owned",
    )
    state.convert = convert
    return state


@pytest.mark.anyio
async def test_render_completions_alone_never_publish_cached_pixels(native):
    state = Readback(
        native.texture,
        (64, 64),
        native.renderer,
        native.convert,
        asyncio.get_running_loop(),
    )
    for frame in [100, 101, 101, 102, 103]:
        native.texture.frame = frame
        state.drawable({"result_handle": frame})
    assert not native.calls and not state.future.done()
    native.texture.frame = 104
    state.drawable({"result_handle": 104})
    assert native.calls[0][1] == "owned-rgb" and not state.future.done()
    state.pixels(object(), 64 * 64 * 4, 64, 64, "TextureFormat.RGBA8_UNORM")
    result = await state.future
    native.values[:] = [0] * len(native.values)
    assert result.shape == (64, 64, 4) and np.all(result == 42)
    state.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "width,height,size,fmt",
    [
        (63, 64, 16128, "TextureFormat.RGBA8_UNORM"),
        (64, 64, 1, "TextureFormat.RGBA8_UNORM"),
        (64, 64, 16384, "TextureFormat.RGBA16_SFLOAT"),
        (2048, 64, 524288, "TextureFormat.RGBA8_UNORM"),
    ],
)
async def test_invalid_native_format_is_refused_before_buffer_access(
    native, width, height, size, fmt
):
    state = Readback(
        native.texture,
        (64, 64),
        native.renderer,
        lambda *a: pytest.fail("unsafe conversion"),
        asyncio.get_running_loop(),
    )
    state.pixels(object(), size, width, height, fmt)
    with pytest.raises(AssetError) as error:
        await state.future
    assert error.value.code == "SENSOR_FORMAT"
    state.close()


@pytest.mark.anyio
async def test_async_capture_waits_for_cpu_and_releases_subscription(native):
    validations = []
    data, observation = await capture(native.sensor, lambda: validations.append(True))
    assert (
        observation["cpu_transfer_completed"]
        and observation["completed_frames_observed"] == 4
    )
    assert native.reset and len(validations) >= 5 and data.shape == (64, 64, 4)
    assert len(native.calls) == 1
    assert observation["frame_number_after"] - observation["frame_number_before"] == 4
    assert (
        observation["frame_baseline"]
        == "first completed render observed after this request"
    )


@pytest.mark.anyio
async def test_cold_texture_is_only_read_during_a_valid_drawable_event(native):
    event_frame = None
    real_callback = None
    original_update = sys.modules["omni.kit.app"].get_app().next_update_async

    def frame_info(handle=0):
        # Real Kit 6.2 throws on the former eager, result_handle=0 query.
        if handle == 0:
            raise RuntimeError("IHydraTexture::getFrameData for 0 failed")
        assert handle == event_frame, "result handles must not escape their event"
        return {"frame_number": handle}

    native.texture.get_frame_info = frame_info

    async def update():
        nonlocal real_callback
        if real_callback is None:
            real_callback = native.callback

            def callback(event):
                nonlocal event_frame
                event_frame = event["result_handle"]
                try:
                    real_callback(event)
                finally:
                    event_frame = None

            native.callback = callback
        await original_update()

    sys.modules["omni.kit.app"].get_app = lambda: SimpleNamespace(
        next_update_async=update
    )
    data, observation = await capture(native.sensor, lambda: None)
    assert data.shape == (64, 64, 4)
    assert observation["frame_number_before"] == 101
    assert observation["frame_number_after"] == 105
    assert native.reset and len(native.calls) == 1


@pytest.mark.anyio
async def test_no_completed_render_times_out_without_reading_a_native_result(native):
    native.texture.get_frame_info = lambda *a: pytest.fail("No frame exists")

    async def update():
        await asyncio.sleep(0)

    sys.modules["omni.kit.app"].get_app = lambda: SimpleNamespace(
        next_update_async=update
    )
    with pytest.raises(AssetError) as error:
        await capture(native.sensor, lambda: None, timeout=0.01)
    assert error.value.code == "RENDER_TIMEOUT"
    assert native.reset and not native.calls


@pytest.mark.anyio
async def test_native_event_errors_are_not_retried_or_hidden(native):
    def broken(handle):
        raise RuntimeError("native frame failure")

    native.texture.get_frame_info = broken
    with pytest.raises(RuntimeError, match="native frame failure"):
        await capture(native.sensor, lambda: None)
    assert native.reset and not native.calls


def test_frame_sequence_ignores_empty_handles_and_duplicate_completions(native):
    handles = []

    def read(handle):
        handles.append(handle)
        return {"frame_number": handle}

    native.texture.get_frame_info = read
    state = RenderFrames(native.texture)
    for handle in [0, 8, 8, 0, 9, 9]:
        state.observe({"result_handle": handle})
    assert handles == [8, 8, 9, 9]
    assert state.before == 8 and state.after == 9 and state.frames == 1


@pytest.mark.anyio
async def test_lidar_wait_uses_live_render_events_without_cpu_capture(native):
    validations = []
    observed = await wait_for_frames(native.texture, lambda: validations.append(True))
    assert observed["frame_number_before"] == 101
    assert observed["frame_number_after"] == 102
    assert observed["completed_frames_observed"] == 1
    assert native.reset and not native.calls and len(validations) >= 2


@pytest.mark.anyio
async def test_lidar_render_wait_timeout_releases_subscription(native):
    async def update():
        await asyncio.sleep(0)

    sys.modules["omni.kit.app"].get_app = lambda: SimpleNamespace(
        next_update_async=update
    )
    with pytest.raises(AssetError) as error:
        await wait_for_frames(native.texture, lambda: None, timeout=0.01)
    assert error.value.code == "RENDER_TIMEOUT"
    assert native.reset and not native.calls


@pytest.mark.anyio
async def test_lidar_render_wait_target_change_releases_subscription(native):
    def validate():
        raise AssetError("STALE_TARGET", "Stage changed")

    with pytest.raises(AssetError) as error:
        await wait_for_frames(native.texture, validate)
    assert error.value.code == "STALE_TARGET" and native.reset and not native.calls


@pytest.mark.anyio
async def test_timeout_does_not_queue_more_native_transfers_or_accept_late_pixels(
    native,
):
    native.stall = True
    with pytest.raises(AssetError) as error:
        await capture(native.sensor, lambda: None, timeout=0.01)
    assert error.value.code == "RENDER_TIMEOUT" and native.reset
    with pytest.raises(AssetError) as error:
        await capture(native.sensor, lambda: None)
    assert error.value.code == "RENDER_BUSY" and len(native.calls) == 1
    state = native.sensor["readback_pending"]
    state.convert = lambda *a: pytest.fail("late buffer must not be accessed")
    native.calls[0][0](object(), 16384, 64, 64, "TextureFormat.RGBA8_UNORM")
    assert state.finished and state.future.cancelled()


@pytest.mark.anyio
async def test_target_change_aborts_before_output_and_drops_subscription(native):
    calls = 0

    def validate():
        nonlocal calls
        calls += 1
        if calls > 2:
            raise AssetError("STALE_TARGET", "Stage changed")

    with pytest.raises(AssetError) as error:
        await capture(native.sensor, validate)
    assert error.value.code == "STALE_TARGET" and native.reset and not native.calls


@pytest.mark.anyio
async def test_missing_renderer_api_is_an_actionable_refusal(native):
    native.renderer.capture_next_frame_rp_resource_callback = None
    with pytest.raises(AssetError) as error:
        await capture(native.sensor, lambda: None)
    assert error.value.code == "SENSOR_API_UNAVAILABLE" and not native.calls


def test_empty_cache_requires_tool_acquisition_without_native_work(native):
    with pytest.raises(AssetError) as error:
        cached(native.sensor, lambda: None)
    assert error.value.code == "NOT_READY"
    assert "wait_for_render=true" in str(error.value)
    assert not native.calls and not hasattr(native, "callback")


@pytest.mark.anyio
async def test_cache_is_last_acquisition_with_explicit_historical_metadata(native):
    context = dict(timeline_state="playing", timeline_time_s=12)
    data, fresh = await capture(
        native.sensor, lambda: None, context=lambda: context.copy()
    )
    assert not data.flags.writeable and "readback_pending" not in native.sensor
    context.update(timeline_state="stopped", timeline_time_s=0)
    native.values[:] = [99] * len(native.values)
    for _ in range(2):
        old, observation = cached(native.sensor, lambda: None)
        assert old is data and np.all(old == 42)
        assert not observation["waited"] and not observation["cpu_transfer_completed"]
        assert observation["cache_age_seconds"] >= 0
        assert (
            observation["acquisition"]["acquisition_context_after"]["timeline_time_s"]
            == 12
        )
        assert len(native.calls) == 1
    # Neither callers nor previous response dictionaries may change provenance.
    fresh["cpu_transfer_completed"] = False
    observation["acquisition"]["cpu_transfer_completed"] = False
    assert cached(native.sensor, lambda: None)[1]["acquisition"][
        "cpu_transfer_completed"
    ]
    new, _ = await capture(native.sensor, lambda: None)
    assert new is not data and np.all(new == 99)
    assert cached(native.sensor, lambda: None)[0] is new
    assert "readback_pending" not in native.sensor


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change", ["generation", "product", "texture", "stage", "path", "resolution"]
)
async def test_cache_never_crosses_a_changed_generation_or_binding(native, change):
    await capture(native.sensor, lambda: None)
    if change == "generation":
        invalidate(native.sensor)
    elif change == "texture":
        native.sensor["product"].hydra_texture = object()
    else:
        key, value = {
            "product": ("product", SimpleNamespace(hydra_texture=native.texture)),
            "stage": ("stage", object()),
            "path": ("product_path", "/Render/Replaced"),
            "resolution": ("resolution", (64, 96)),
        }[change]
        native.sensor[key] = value
    with pytest.raises(AssetError) as error:
        cached(native.sensor, lambda: None)
    assert error.value.code in ("NOT_READY", "SENSOR_INVALIDATED")
    assert len(native.calls) == 1


@pytest.mark.anyio
async def test_fresh_failure_never_returns_old_cache_or_accepts_late_pixels(native):
    old, _ = await capture(native.sensor, lambda: None)
    native.stall = True
    with pytest.raises(AssetError) as error:
        await capture(native.sensor, lambda: None, timeout=0.01)
    assert error.value.code == "RENDER_TIMEOUT"
    assert cached(native.sensor, lambda: None)[0] is old
    pending = native.sensor["readback_pending"]
    invalidate(native.sensor)
    assert not pending.active and "camera_cache" not in native.sensor
    native.calls[-1][0](object(), 16384, 64, 64, "TextureFormat.RGBA8_UNORM")
    await asyncio.sleep(0)
    assert (
        "camera_cache" not in native.sensor and "readback_pending" not in native.sensor
    )


@pytest.mark.anyio
async def test_final_validation_failure_cannot_commit_pixels(native):
    def validate():
        pending = native.sensor.get("readback_pending")
        if pending is not None and pending.future.done():
            raise AssetError("STALE_TARGET", "Target changed after CPU transfer")

    with pytest.raises(AssetError) as error:
        await capture(native.sensor, validate)
    assert error.value.code == "STALE_TARGET"
    assert (
        "camera_cache" not in native.sensor and "readback_pending" not in native.sensor
    )
    assert native.reset


@pytest.mark.anyio
async def test_disposal_during_native_completion_cannot_repopulate_cache(native):
    def validate():
        pending = native.sensor.get("readback_pending")
        if pending is not None and pending.future.done():
            invalidate(native.sensor)

    with pytest.raises(AssetError, match="generation"):
        await capture(native.sensor, validate)
    assert "camera_cache" not in native.sensor and native.reset


@pytest.mark.anyio
async def test_failed_subscription_does_not_leave_a_busy_native_request(
    native, monkeypatch
):
    def fail(**kwargs):
        raise RuntimeError("event subscription failed")

    monkeypatch.setattr(
        sys.modules["carb.eventdispatcher"],
        "get_eventdispatcher",
        lambda: SimpleNamespace(observe_event=fail),
    )
    with pytest.raises(RuntimeError, match="subscription failed"):
        await capture(native.sensor, lambda: None)
    assert (
        "readback_pending" not in native.sensor and "camera_cache" not in native.sensor
    )
    assert not native.calls

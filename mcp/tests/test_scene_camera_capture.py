"""CPU transfer completion, bounds and cancellation; live pixels are a Kit gate."""

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from sim_tools.camera_capture import Readback, capture
from isaac_mcp.project_assets import AssetError


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def native(monkeypatch):
    calls = []
    texture = SimpleNamespace(
        frame=100,
        get_frame_info=lambda handle=0: {"frame_number": texture.frame},
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
            calls[0][0](object(), len(values), 64, 64, "TextureFormat.RGBA8_UNORM")
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

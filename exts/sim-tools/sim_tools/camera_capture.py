"""Fresh camera pixels from a completed, owned Hydra render product.

Replicator's paused RGB pointer can lag behind its Hydra frame counter. Request
a renderer-managed CPU transfer during the matching drawable event instead.
Only public Kit APIs are used. No CUDA pointer access or orchestrator stepping.
"""

import asyncio
from contextlib import asynccontextmanager
import copy
from dataclasses import dataclass
import time

from twinsmith.project_assets import refuse

from .compat import has_drawable_events, observe_drawable_changed


@dataclass(frozen=True)
class CameraFrame:
    """Pixel transport and freshness are independent, explicit facts."""

    data: object
    fresh: bool


VIEWPORT_GRID_SETTING = "/app/viewport/grid/enabled"


# A frame drawn before the setting changed can still be the one read back.
GRID_SETTLE_FRAMES = 12


@asynccontextmanager
async def without_viewport_grid(playing, next_update=None):
    """Leave Kit's grid and axis lines out of a stopped-timeline capture, then restore the setting.

    The legacy viewport gizmos draw them into every render product, including ours, unless the timeline is playing.
    The setting is process-wide with no per-product form, so a window's grid is hidden during the capture too. It is
    not persistent. Nothing changes when it is already off, absent (a build without it) or the timeline is playing.
    """
    settings = None
    if not playing:
        try:
            import carb.settings

            settings = carb.settings.get_settings()
        except ImportError:
            pass
    if settings is None or settings.get(VIEWPORT_GRID_SETTING) is not True:
        yield
        return
    settings.set(VIEWPORT_GRID_SETTING, False)
    try:
        for _ in range(GRID_SETTLE_FRAMES):
            await next_update()
        yield
    finally:
        settings.set(VIEWPORT_GRID_SETTING, True)


def invalidate(sensor):
    """Invalidate even partially created/disposed cameras and late callbacks."""
    sensor["capture_generation"] = object()
    sensor.pop("camera_cache", None)
    pending = sensor.pop("readback_pending", None)
    if pending is not None:
        pending.close()


def binding(sensor):
    product = sensor.get("product")
    texture = getattr(product, "hydra_texture", None)
    if product is None or texture is None:
        refuse("SENSOR_INVALIDATED", "Camera render resources were released.")
    return (
        sensor.setdefault("capture_generation", object()),
        product,
        texture,
        sensor.get("stage"),
        sensor["product_path"],
        tuple(sensor["resolution"]),
    )


def validate_binding(sensor, expected, validate):
    validate()
    current = binding(sensor)
    if (
        any(a is not b for a, b in zip(current[:4], expected[:4]))
        or current[4:] != expected[4:]
    ):
        refuse("SENSOR_INVALIDATED", "Camera generation or render resources changed.")


def cached(sensor, validate):
    """No renderer/annotator request: reuse the last validated acquisition."""
    validate()
    record = sensor.get("camera_cache")
    if record is None:
        refuse(
            "NOT_READY",
            "No tool-acquired camera frame is cached. Call capture with wait_for_render=true first; ordinary Play does not seed or refresh this cache.",
        )
    validate_binding(sensor, record["binding"], validate)
    return record["data"], dict(
        waited=False,
        render_product=sensor["product_path"],
        cpu_transfer_completed=False,
        cache_age_seconds=max(0, time.monotonic() - record["acquired_at"]),
        acquisition=copy.deepcopy(record["observation"]),
        meaning="Last validated tool acquisition; may predate Stop/reset, pose edits or physics steps. No new render or CPU transfer requested. PNG encoding and file I/O still occur.",
    )


class RenderFrames:
    """Frame numbers copied only while their owned drawable event is alive."""

    def __init__(self, texture):
        self.texture = texture
        self.before = None
        self.after = None
        self.frames = 0

    def observe(self, event):
        handle = event["result_handle"]
        if not handle:
            return False
        frame = (self.texture.get_frame_info(handle) or {}).get("frame_number")
        if frame is None or frame == self.after:
            return False
        if self.before is None:
            self.before = self.after = frame
            return False
        self.after = frame
        self.frames += 1
        return True

    def observation(self):
        return dict(
            frame_number_before=self.before,
            frame_number_after=self.after,
            frame_baseline="first completed render observed after this request",
            completed_frames_observed=self.frames,
        )


async def wait_for_frames(texture, validate, timeout=5):
    """LiDAR render warm-up; this does not establish a new accumulated scan."""
    try:
        import omni.hydratexture as ht
        import omni.kit.app

        state = RenderFrames(texture)
        error = None
        active = True

        def drawable(event):
            nonlocal error
            if active and error is None:
                try:
                    state.observe(event)
                except Exception as exc:
                    error = exc

        subscription = observe_drawable_changed(ht, texture, drawable, "IsaacMCP.SensorRenderWait")
    except (ImportError, AttributeError) as exc:
        refuse(
            "SENSOR_API_UNAVAILABLE",
            "Render completion events unavailable: " + str(exc),
        )

    async def wait():
        # Yield for post-render annotator dispatch even when the event already
        # arrived. Never drive rendering, Play, or a nested application update.
        while True:
            await omni.kit.app.get_app().next_update_async()
            validate()
            if error is not None:
                raise error
            if state.frames:
                return state.observation()

    try:
        return await asyncio.wait_for(wait(), timeout)
    except asyncio.TimeoutError:
        refuse(
            "RENDER_TIMEOUT",
            "Render warm-up did not complete within five seconds. Check rendering is enabled; no completed scan is implied by this wait.",
        )
    finally:
        active = False
        state.texture = None
        subscription.reset()


class Readback(RenderFrames):
    """One bounded native request; late callbacks cannot publish stale data."""

    def __init__(self, texture, resolution, renderer, convert, loop):
        super().__init__(texture)
        self.resolution = resolution
        self.renderer = renderer
        self.convert = convert
        self.loop = loop
        self.future = loop.create_future()
        # A new texture has no render result yet. get_frame_info(0) throws
        # getFrameData on released headless Kit builds instead of returning
        # an empty dict. Only dereference the result_handle supplied by a
        # drawable event, while that event is alive (the public API contract).
        self.queued = False
        self.finished = False
        self.active = True

    def _complete(self, data=None, error=None):
        if self.active and not self.future.done():
            if error is not None:
                self.future.set_exception(error)
            else:
                self.future.set_result(data)

    def pixels(self, buffer, size, width, height, fmt):
        # Native capture can complete after a timeout/stage change. Its buffer
        # is owned by Kit and valid only in this callback; never retain it.
        try:
            if not self.active:
                return
            if (
                (height, width) != self.resolution
                or not 64 <= height <= 1024
                or not 64 <= width <= 1024
                or size != width * height * 4
                or str(fmt) != "TextureFormat.RGBA8_UNORM"
            ):
                refuse(
                    "SENSOR_FORMAT",
                    "Renderer must deliver bounded RGBA8 CPU pixels at the configured resolution.",
                )
            import numpy as np

            # Kit's public converter copies the CPU capsule while it is valid.
            values = self.convert(buffer, size, width, height, fmt)
            if len(values) != size:
                refuse("SENSOR_FORMAT", "Renderer CPU transfer length is inconsistent.")
            data = np.asarray(values, dtype=np.uint8).reshape(height, width, 4)
            self.loop.call_soon_threadsafe(self._complete, data)
        except Exception as e:
            self.loop.call_soon_threadsafe(self._complete, None, e)
        finally:
            self.finished = True

    def drawable(self, event):
        if not self.active or self.queued:
            return
        try:
            if not self.observe(event):
                return
            # Drain frames already in flight when the request arrived. Unlike
            # the old wait, the fourth completion schedules a NEW CPU transfer.
            if self.frames < 4:
                return
            aovs = self.texture.get_aov_info(
                event["result_handle"], aov_name="LdrColor", include_texture=True
            )
            if not aovs:
                return
            resource = aovs[0].get("texture", {}).get("rp_resource")
            if resource is None:
                return
            self.renderer.capture_next_frame_rp_resource_callback(self.pixels, resource)
            self.queued = True
            self.texture = None
        except Exception as e:
            self.loop.call_soon_threadsafe(self._complete, None, e)

    def close(self):
        self.active = False
        self.texture = None
        if not self.queued:
            self.finished = True
        if not self.future.done():
            self.future.cancel()
        elif not self.future.cancelled():
            # A simultaneous target change may supersede a native error.
            self.future.exception()


async def capture(sensor, validate, timeout=5, context=None):
    """Await ordinary updates and a CPU callback, restoring all subscriptions."""
    validate()
    expected_binding = binding(sensor)
    context_before = context() if context else None
    pending = sensor.get("readback_pending")
    if pending is not None and not pending.finished:
        refuse(
            "RENDER_BUSY",
            "A timed-out CPU transfer is still pending. Wait or recreate this owned camera; another native request was not queued.",
        )
    state = None
    try:
        import omni.hydratexture as ht
        import omni.kit.app
        import omni.kit.renderer_capture as rc

        texture = sensor["product"].hydra_texture
        renderer = rc.acquire_renderer_capture_interface()
        if not has_drawable_events(ht, texture):
            refuse("SENSOR_API_UNAVAILABLE", "This Hydra texture has no drawable-change event API.")
        for obj, methods in (
            (texture, ("get_frame_info", "get_aov_info")),
            (renderer, ("capture_next_frame_rp_resource_callback",)),
            (rc, ("convert_raw_bytes_to_list",)),
        ):
            if any(not callable(getattr(obj, name, None)) for name in methods):
                refuse(
                    "SENSOR_API_UNAVAILABLE",
                    "Enable the installed Hydra texture and renderer capture extensions; fresh bounded CPU readback is unavailable.",
                )
        state = Readback(
            texture,
            sensor["resolution"],
            renderer,
            rc.convert_raw_bytes_to_list,
            asyncio.get_running_loop(),
        )
        sensor["readback_pending"] = state
        subscription = observe_drawable_changed(ht, texture, state.drawable, "IsaacMCP.CameraCapture")
    except Exception as e:
        if state is not None:
            state.close()
            if sensor.get("readback_pending") is state:
                sensor.pop("readback_pending", None)
        if isinstance(e, (ImportError, AttributeError)):
            refuse(
                "SENSOR_API_UNAVAILABLE",
                "Fresh renderer CPU capture unavailable: " + str(e),
            )
        raise
    begin = time.monotonic()

    async def wait():
        while not state.future.done():
            await omni.kit.app.get_app().next_update_async()
            validate_binding(sensor, expected_binding, validate)
        return await state.future

    try:
        data = await asyncio.wait_for(wait(), timeout)
        validate_binding(sensor, expected_binding, validate)
        observation = dict(
            waited=True,
            render_product=sensor["product_path"],
            **state.observation(),
            cpu_transfer_completed=True,
            wait_seconds=time.monotonic() - begin,
            meaning="CPU pixels transferred from this product after the requested render completions; not an atomic physics/sensor snapshot",
        )
        if context:
            observation.update(
                acquisition_context_before=context_before,
                acquisition_context_after=context(),
            )
        # Commit only on the normal Kit/tool loop after final target validation.
        # This is the acquisition boundary, before subsequent PNG publication.
        # The render counter is an observation, not an exact callback image ID.
        data.setflags(write=False)
        sensor["camera_cache"] = dict(
            binding=expected_binding,
            data=data,
            acquired_at=time.monotonic(),
            observation=copy.deepcopy(observation),
        )
        return data, observation
    except asyncio.TimeoutError:
        refuse(
            "RENDER_TIMEOUT",
            "Camera render/CPU transfer did not complete within five seconds. Check rendering is enabled. No cached pixels were returned; wait_for_render=false explicitly permits stale data.",
        )
    finally:
        state.close()
        subscription.reset()
        # Completed futures retain their ndarray. Keep only the cache reference;
        # unfinished native callbacks still guard against another queued request.
        if state.finished and sensor.get("readback_pending") is state:
            sensor.pop("readback_pending", None)

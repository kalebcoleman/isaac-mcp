import os
import sys
from pathlib import Path

# This repository ships the stdlib broker/contracts with the host wheel as well.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "mcp"))
import omni.ext
import omni.usd
import omni.timeline
from isaac_mcp_bridge import get_mcp_bridge, ToolDefinition
from isaac_mcp.scene_contracts import HOST_ONLY, REGISTRY
from isaac_mcp.project_assets import AssetError
from .core import STAGE_TOOLS, TIMELINE_TOOLS, Scene, attribute_change, bounded, facts, stop_call
from .render_export import validate_native_binding
from .reload import draining, register_when_free

# Reads that do not depend on the stage: no session/stage target, and they keep working right when
# a target has just gone stale (the log is how a stale target gets explained).
TARGETLESS = {"sim.graph.node_types", "sim.logs.tail"}


class Extension(omni.ext.IExt):
    def on_startup(self, ext_id):
        self.scene = Scene(
            lambda: omni.usd.get_context().get_stage(),
            omni.timeline.get_timeline_interface(),
            render_binding=validate_native_binding,
        )
        # Exact vendor MDL module, resolved by Kit's MDL subsystem rather than USD.
        # No general missing-dependency bypass or user-configurable string whitelist.
        import isaacsim
        import hashlib

        mdl = Path(isaacsim.__file__).parent / "kit/mdl/core/Base/OmniPBR.mdl"
        if mdl.is_file():
            self.scene.runtime_assets["OmniPBR.mdl"] = {
                "path": str(mdl),
                "sha256": hashlib.sha256(mdl.read_bytes()).hexdigest(),
                "source": "installed trusted Kit MDL library",
            }
        from .advanced import Advanced

        self.advanced = Advanced(self.scene, self.dispatch)
        from .viewport import Follower

        self.follower = Follower()
        import asyncio

        self._operation_lock = asyncio.Lock()
        import omni.kit.app as kit_app
        import carb
        from .runtime_edit import RuntimeEditRestorer

        self._runtime_edit_restorer = RuntimeEditRestorer(
            self.scene, kit_app.get_app().next_update_async, carb.log_error
        )
        self._timeline_events = (
            self.scene.timeline.get_timeline_event_stream().create_subscription_to_pop(
                self._timeline_event
            )
        )
        # Release attachments before Kit invalidates the closing stage. No
        # recursive application update, stage reset or timeline change here.
        self._stage_events = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(self._stage_event)
        )
        if os.getenv("ISAAC_MCP_ENABLE_SCENE_SCRIPTS") == "1":
            import omni.kit.app as kit_app
            import carb.settings

            kit_app.get_app().get_extension_manager().set_extension_enabled_immediate(
                "omni.graph.scriptnode", True
            )
            carb.settings.get_settings().set("/app/omni.graph.scriptnode/opt_in", True)
        self.bridge = get_mcp_bridge()
        self._pending_registration = None
        self._note_reload()
        if draining(self.bridge):
            # A tool call from before this reload is still running. It needs this event loop to
            # finish, so wait for it in the background instead of failing startup.
            self._pending_registration = asyncio.ensure_future(
                register_when_free(self.bridge, self._register_tools, self._report_reload)
            )
        else:
            self._register_tools()

    def _register_tools(self):
        registered = []
        try:
            for namespace in ("sim", "assets"):
                registrar = self.bridge.register_owner(
                    "generic-" + namespace, namespace, display_name="Generic " + namespace
                )
                registered.append("generic-" + namespace)
                for canonical, meta in REGISTRY.items():
                    ns, name = canonical.split(".", 1)
                    if ns == namespace and canonical not in HOST_ONLY:
                        registrar.add(
                            ToolDefinition(
                                name=name,
                                description=meta["description"],
                                parameters=meta["parameters"],
                                handler=self.handler(canonical),
                            )
                        )
        except BaseException:
            # Kit does not call on_shutdown after a failed startup: leave nothing half registered.
            for owner in registered:
                self.bridge.unregister_owner(owner)
            raise

    def _note_reload(self):
        """Say that this extension restarted inside a running Kit. Diagnostics only."""
        try:
            import carb
            from isaac_mcp_bridge.source_state import note_start

            started = note_start("sim-tools")
            self.scene.previous_sessions = list(started.get("session_ids", []))
            if started["reload"]:
                what = (f"sim-tools reloaded in this Kit process (reload {started['reload']}): a file in its folder "
                        "changed, or it was toggled. New session_id; ownership and scene events from before were forgotten")
                self.scene.record_event("extension", what, dict(origin="extension_reload", source="Kit extension manager"))
                carb.log_warn(f"[sim-tools] {what}.")
        except Exception as exc:  # noqa: BLE001
            self._report_reload(f"could not record this start: {exc}", True)

    def _report_reload(self, message, error):
        """For the background registration task: the Kit log and sim.logs.tail."""
        try:
            import carb

            self.scene.record_event("extension", message, dict(origin="extension_reload", source="sim-tools"))
            (carb.log_error if error else carb.log_warn)(f"[sim-tools] {message}")
        except Exception:  # noqa: BLE001 - diagnostics only
            pass

    def on_shutdown(self):
        # Every step runs even if an earlier one fails: an owner left registered would make the next
        # start fail with "already registered".
        import carb

        def attempt(step, *args):
            try:
                step(*args)
            except Exception as exc:  # noqa: BLE001
                carb.log_error(f"[sim-tools] shutdown step failed: {exc}")

        def remember_session():
            from isaac_mcp_bridge.source_state import note_stop

            note_stop("sim-tools", session_ids=(self.scene.previous_sessions + [self.scene.session_id])[-8:])

        attempt(remember_session)
        if getattr(self, "_pending_registration", None) is not None:
            self._pending_registration.cancel()
        attempt(self.follower.stop, "extension shutdown")
        self._timeline_events = None
        attempt(self._runtime_edit_restorer.cancel)
        self._stage_events = None
        attempt(self.advanced.close_sensors)
        attempt(self.scene.close_stage)
        for namespace in ("sim", "assets"):
            attempt(self.bridge.unregister_owner, "generic-" + namespace)

    def _note_change(self, kind, what, certain, stage=None):
        """Report a stage/timeline change to the Kit log and sim.logs.tail. Diagnostics only: this
        must never stop the guards in the event handlers from running."""
        try:
            import carb

            activity = getattr(getattr(self, "bridge", None), "tool_activity", lambda: None)()
            attribution = attribute_change(activity, certain)
            record = self.scene.record_event(kind, what, attribution, stage)
            line = f"[sim-tools] {what}{': ' + stage if stage else ''} (source: {attribution['source']})"
            (carb.log_info if attribution["origin"] == "tool" else carb.log_warn)(line)
            return record
        except Exception as exc:  # noqa: BLE001
            try:
                import carb

                carb.log_error(f"[sim-tools] could not record {what}: {exc}")
            except Exception:  # noqa: BLE001
                pass
            return None

    def _stage_event(self, event):
        types = omni.usd.StageEventType
        if event.type in (int(types.OPENED), int(types.OPEN_FAILED)):
            try:
                ctx = omni.usd.get_context()
                stage = ctx.get_stage()
                url = stage.GetRootLayer().identifier if stage else ctx.get_stage_url()
            except Exception:  # noqa: BLE001 - diagnostics only
                url = None
            what = "stage opened" if event.type == int(types.OPENED) else "stage open failed"
            self._note_change("stage", what, STAGE_TOOLS, url)
        if event.type == int(omni.usd.StageEventType.CLOSING):
            current = self.scene._stage
            self._note_change("stage", "stage closing", STAGE_TOOLS,
                              current.GetRootLayer().identifier if current else None)
            self.follower.stop("stage closing")
            self._runtime_edit_restorer.cancel()
            self.advanced.close_sensors(stage_closing=True)
            self.scene.close_stage()

    def _timeline_event(self, event):
        types = omni.timeline.TimelineEventType
        if event.type == int(types.STOP):
            self._note_timeline("timeline stopped")
            self._runtime_edit_restorer.stopped()
        elif event.type == int(types.PLAY):
            self._note_timeline("timeline playing")
            self._runtime_edit_restorer.cancel()
        elif event.type == int(types.PAUSE):
            self._note_timeline("timeline paused")

    def _note_timeline(self, what):
        # Only state changes: Replicator can issue a second native Stop while draining.
        if getattr(self, "_timeline_state", None) != what:
            self._timeline_state = what
            self._note_change("timeline", what, TIMELINE_TOOLS)

    async def _timeline_control(self, args):
        """sim.timeline.control with its guards: settle a pending Stop before Play, the physics
        backend check and session-layer edit lease (in dispatch), the guarded native Stop, and
        restoring the edit target. Also used by tools that need physics to run briefly."""
        import asyncio
        import omni.kit.app as kit_app
        from isaac_mcp.project_assets import refuse
        from .timeline import complete_requested_stop, settle_stop

        timeline_target = (args["session_id"], args["stage_id"])
        timeline_was_stopped = self.scene.timeline.is_stopped()
        stop_target_guard = None
        if args.get("action") == "stop":
            from .runtime_edit import stop_edit_target_guard

            self.scene.target(*timeline_target)
            stop_target_guard = stop_edit_target_guard(self.scene, self.scene.stage())

        def validate_stopped():
            self.scene.target(*timeline_target)
            if stop_target_guard is not None:
                stop_target_guard()
            if not self.scene.timeline.is_stopped():
                refuse(
                    "TIMELINE_INTERRUPTED",
                    "Timeline changed while awaiting native Stop; no automatic Play retry.",
                )

        if args.get("action") == "play" and self.scene.timeline.is_stopped():
            await settle_stop(kit_app.get_app().next_update_async, validate_stopped)
        result = self.dispatch("sim.timeline.control", args)
        # Await the ordinary frame, never recursively pump the app loop.
        await asyncio.wait_for(kit_app.get_app().next_update_async(), 5)
        self.scene.target(*timeline_target)
        if args["action"] == "stop":
            result["native_stop_observation"] = await complete_requested_stop(
                kit_app.get_app().next_update_async,
                validate_stopped,
            )
        if self.scene.timeline.is_stopped():
            result["edit_target_restored"] = self.scene.end_runtime_edit(self.scene.stage())
        result.update(
            playing=self.scene.timeline.is_playing(),
            paused=not self.scene.timeline.is_playing() and not self.scene.timeline.is_stopped(),
            stopped=self.scene.timeline.is_stopped(),
            time_s=self.scene.timeline.get_current_time(),
        )
        result["effects"] = (
            "Timeline state observed after an application update; physics readiness requires a runtime read."
        )
        expected = {
            "play": result["playing"],
            "stop": result["stopped"],
            "pause": not result["playing"] and (not result["stopped"] or timeline_was_stopped),
        }[args["action"]]
        if not expected:
            refuse(
                "TIMELINE_INTERRUPTED",
                "Requested timeline state was not retained; inspect the returned state before retrying.",
                observed_state=result,
            )
        return result

    async def _occupancy_cycle(self, args):
        """Isaac's occupancy generator only sees colliders while physics runs: from a stopped
        timeline, play (through the guarded timeline path), let a few frames run, generate, then
        stop again so robots return to their authored poses and physics writes stay in the
        session layer."""
        import asyncio
        import omni.kit.app as kit_app
        from isaac_mcp.project_assets import refuse

        from .graph import validate_occupancy
        from isaac_mcp.project_assets import AssetError

        s = self.scene
        ids = dict(session_id=args["session_id"], stage_id=args["stage_id"])
        stage = s.target(**ids)
        if not s.timeline.is_stopped():
            refuse("TIMELINE_ACTIVE", "Stop the timeline first with sim__timeline__control action=stop; the tool "
                   "plays a few frames itself so robots are mapped at their start poses.",
                   before_retry=stop_call(**ids))
        validate_occupancy(s, stage, args)  # refuse bad arguments before anything plays

        async def stop():
            if not s.timeline.is_stopped():
                await self._timeline_control({**ids, "action": "stop"})

        try:
            await self._timeline_control({**ids, "action": "play"})
            for _ in range(8):
                await kit_app.get_app().next_update_async()
            result = self.dispatch("sim.map.occupancy", dict(args))
        except (Exception, asyncio.CancelledError) as exc:
            try:
                await stop()
            except Exception as stop_exc:  # keep the original outcome; report the stop problem with it
                if isinstance(exc, AssetError):
                    exc.details["stop_error"] = str(stop_exc)[:300]
                elif isinstance(exc, Exception):
                    raise RuntimeError(f"{exc}; stopping the timeline also failed ({str(stop_exc)[:200]}), "
                                       "so it may still be playing") from exc
            raise
        stopped = True
        try:
            await stop()
        except Exception as stop_exc:  # the map exists: still return it, with the stop problem
            stopped = False
            result["stop_error"] = (f"{str(stop_exc)[:300]}; the timeline may still be playing. "
                                    "Stop it with sim__timeline__control.")
        result["timeline"] = ("played 8 frames for the physics scene" + (", then stopped" if stopped else "")
                              + ". While playing, OnPlaybackTick graphs ran: /clock restarted and ROS "
                              "publishers/controllers ticked.")
        return result

    async def _wait_sensor_frame(self, name, args):
        """Observe ordinary rendering without changing timeline or re-entering Kit."""
        from isaac_mcp.project_assets import refuse
        import time

        s = self.scene
        s.require_runtime()
        s.target(args["session_id"], args["stage_id"])
        self.advanced.invalidate()
        expected = "camera" if name == "sim.camera.capture" else "lidar"
        adopt = args.pop("adopt", False)
        if type(adopt) is not bool:
            refuse("INVALID_VALUE", "adopt must be boolean.")
        wait = args.pop("wait_for_render", expected == "camera")
        if type(wait) is not bool:
            refuse("INVALID_VALUE", "wait_for_render must be boolean.")
        # Refuse what can be refused before adoption creates anything, or a render is spent on it.
        if expected == "camera" and isinstance(args.get("filename"), str):
            import re

            from .advanced import CAPTURE_NAME, CAPTURE_NAME_RULE

            if not re.fullmatch(CAPTURE_NAME, args["filename"]):
                refuse("INVALID_OUTPUT", CAPTURE_NAME_RULE)
        if wait and expected == "lidar" and not s.timeline.is_playing():
            refuse(
                "SENSOR_PAUSED",
                "LiDAR acquisition requires a playing timeline. Use wait_for_render=false for the latest completed scan, or explicitly play and allow warm-up.",
            )
        entry = self.advanced.sensors.get(args["path"])
        if entry is None or entry[0] != expected:
            from pxr import UsdGeom

            target_prim = s.stage().GetPrimAtPath(args["path"])
            if entry is None and not target_prim.IsValid():
                refuse("NOT_FOUND", f"No prim at {args['path']}. Check sim__stage__tree, or create it with sim__{expected}__create.")
            wrong_kind = target_prim.IsValid() and target_prim.GetTypeName() and target_prim.GetTypeName() != (
                "Camera" if expected == "camera" else "OmniLidar")
            if entry is not None or wrong_kind or target_prim.IsA(UsdGeom.Gprim):  # adopt could never help these
                what = f"a {entry[0]}" if entry is not None else f"a {target_prim.GetTypeName()}"
                refuse("NOT_A_SENSOR", f"{args['path']} is {what}, not a {expected}. Find one with sim__prim__find, or "
                       f"create it with sim__{expected}__create.")
            if entry is not None or not (adopt or args["path"] in s.owned):  # sim__quick_start's camera is ours already
                refuse(
                    "NOT_OWNED",
                    f"{args['path']} has no {expected} render resources in this session: sensors from an opened "
                    "file start unregistered, even ones you made before saving. Retry with adopt=true to create "
                    "them for the sensor at this exact path (session layer only; source files are unchanged), "
                    f"or create a new sensor with sim__{expected}__create.",
                    **({} if entry is not None else {"retry_with": {"adopt": True}}),
                )
            entry = self.advanced.adopt(s.stage(), args["path"], expected)
        if not wait and expected == "lidar":
            await self._wait_for_scan(entry[1], time.monotonic(), lambda: s.target(args["session_id"], args["stage_id"]))
            return {
                "waited": False,
                "meaning": "latest completed LiDAR scan; freshness not established by this call",
            }, None
        import omni.kit.app

        product = entry[1].get("product")
        texture = getattr(product, "hydra_texture", None)
        if texture is None or not callable(getattr(texture, "get_frame_info", None)):
            refuse(
                "SENSOR_API_UNAVAILABLE",
                "Render product does not expose frame completion metadata.",
            )
        begin = time.monotonic()
        timeline_before = s.timeline.get_current_time()
        if expected == "camera":
            from .camera_capture import CameraFrame, cached, capture, without_viewport_grid

            def validate():
                s.target(args["session_id"], args["stage_id"])
                if self.advanced.sensors.get(args["path"]) is not entry:
                    refuse("SENSOR_INVALIDATED", "Sensor changed during CPU capture.")
                prim = s.prim(s.stage(), args["path"])
                if not prim.IsActive() or prim != entry[1]["prim"]:
                    refuse(
                        "SENSOR_INVALIDATED", "Camera prim is inactive or was replaced."
                    )
                if (
                    getattr(entry[1].get("product"), "hydra_texture", None)
                    is not texture
                    or texture.get_usd_context_name()
                    != omni.usd.get_context().get_name()
                    or (texture.get_height(), texture.get_width())
                    != entry[1]["resolution"]
                    or texture.get_camera_path() != args["path"]
                    or texture.get_render_product_path() != entry[1]["product_path"]
                ):
                    refuse(
                        "SENSOR_MODIFIED",
                        "Camera/render product binding changed outside this tool.",
                    )

            def context():
                return dict(
                    timeline_time_s=s.timeline.get_current_time(),
                    timeline_state=(
                        "playing"
                        if s.timeline.is_playing()
                        else "stopped" if s.timeline.is_stopped() else "paused"
                    ),
                )

            if wait:
                async with without_viewport_grid(s.timeline.is_playing(), omni.kit.app.get_app().next_update_async):
                    data, observation = await capture(entry[1], validate, context=context)
            else:
                data, observation = cached(entry[1], validate)
            observation.update(
                timeline_time_before=timeline_before,
                timeline_time_after=s.timeline.get_current_time(),
            )
            return observation, CameraFrame(data, fresh=wait)
        from .camera_capture import wait_for_frames

        def validate_lidar():
            s.target(args["session_id"], args["stage_id"])
            if self.advanced.sensors.get(args["path"]) is not entry:
                refuse("SENSOR_INVALIDATED", "Sensor changed while waiting for render.")

        observation = await wait_for_frames(texture, validate_lidar)
        validate_lidar()
        await self._wait_for_scan(entry[1], begin, validate_lidar)
        return {
            "waited": True,
            "render_product": entry[1]["product_path"],
            **observation,
            "wait_seconds": time.monotonic() - begin,
            "timeline_time_before": timeline_before,
            "timeline_time_after": s.timeline.get_current_time(),
            "meaning": "matching render completed after request; not an atomic physics/sensor snapshot",
        }, None

    async def _wait_for_scan(self, sensor, begin, validate):
        """While playing, wait a moment for points. A finished render is not always a finished scan: on 6.0.0.0 only about
        1 read in 3 found any. The read that follows still reports honestly if there are none."""
        import time

        import omni.kit.app

        from .sensors import LIDAR_SCAN_WAIT_FRAMES, LIDAR_SCAN_WAIT_S, lidar_has_data

        for _ in range(LIDAR_SCAN_WAIT_FRAMES):
            if not self.scene.timeline.is_playing() or time.monotonic() - begin > LIDAR_SCAN_WAIT_S or lidar_has_data(sensor):
                break
            await omni.kit.app.get_app().next_update_async()
            validate()

    async def _finish_graph_show(self, ready, ids, path, layout, size):
        """Wait only as many frames as the editor needs. Each frame can take ~1 s while the
        editor rebuilds on a loaded GPU, so fixed waits made every call slow."""
        import time
        import omni.kit.app as kit_app
        from .graph import editor_ready, show_graph_finish, show_graph_refocus, store_positions
        from .placement import bbox_settled

        app = kit_app.get_app()
        begin, frames = time.monotonic(), 0
        while not ready and frames < 30:  # the editor builds its window on later frames
            await app.next_update_async()
            frames += 1
            ready = editor_ready()
        # Re-check the target: the stage may have changed during those frames.
        stage = self.scene.target(*ids, layout)
        result = show_graph_finish(stage, path, layout, *size)
        # Zoom to fit on every call, once the nodes are drawn at their final size (the view
        # reports a placeholder bbox until then, which left later graphs zoomed out).
        previous, fitted = None, False
        for _ in range(40):
            await app.next_update_async()
            frames += 1
            state = show_graph_refocus()
            if bbox_settled(previous, state):
                fitted = True
                break
            previous = state
        if result.get("laid_out"):
            # The waits above can take tens of seconds; Play or a stage swap may have happened since.
            try:
                self.scene.target(*ids, True)
                safe = self.scene.timeline.is_stopped()
            except Exception:  # noqa: BLE001 - stale target or timeline running
                safe = False
            if safe:
                result["positions_stored"] = store_positions(path)
            else:
                result.update(positions_stored=0, positions_note="not stored: the timeline started or the "
                              "stage changed while the editor was opening")
        result.update(fitted=fitted, frames_waited=frames, seconds=round(time.monotonic() - begin, 2))
        return result

    def handler(self, name):
        async def run(**args):
            try:
                target = REGISTRY[name]["parameters"].get("session_id")
                if target and target["required"] and not (args.get("session_id") and args.get("stage_id")):
                    # Reached by clients that do not enforce the published required list.
                    self.scene.target(args.get("session_id"), args.get("stage_id"))
                async with self._operation_lock:
                    render_observation = None
                    camera_frame = None
                    if name in ("sim.camera.capture", "sim.lidar.read"):
                        (
                            render_observation,
                            camera_frame,
                        ) = await self._wait_sensor_frame(name, args)
                    if name == "sim.stage.open":
                        from .stage_open import open_stage

                        result = await open_stage(self.scene, args)
                    elif name == "sim.quick_start":
                        from .quick_start import run as quick_start

                        result = await quick_start(self, args)
                    elif name in ("sim.ui.find", "sim.ui.act"):
                        # Kit UI, not the stage: no session/stage target (the UI outlives stage changes).
                        from .ui import UIRefusal, act, find

                        try:
                            result = find(args) if name == "sim.ui.find" else await act(args)
                        except UIRefusal as exc:
                            return dict(status="error", code=exc.code, message=str(exc),
                                        effects="No input was sent." if name == "sim.ui.find" or exc.code in (
                                            "INVALID_VALUE", "LIMIT", "STALE_WIDGET", "NOT_VISIBLE",
                                            "OUT_OF_WINDOW", "NO_UI", "DISABLED", "NO_WINDOW") else "Inspect the UI before retrying.")
                    elif name == "sim.timeline.control":
                        result = await self._timeline_control(args)
                    elif name == "sim.map.occupancy":
                        result = await self._occupancy_cycle(args)
                    else:
                        path, layout = args.get("path"), args.get("layout", False)
                        size = (args.get("width", 0), args.get("height", 0))
                        ids = (args.get("session_id"), args.get("stage_id"))
                        result = self.dispatch(name, args, camera_frame=camera_frame)
                        if isinstance(result, dict) and result.get("_finish") == "sim.graph.show":
                            result = await self._finish_graph_show(result["ready"], ids, path, bool(layout), size)
                    if render_observation is not None:
                        result["render_observation"] = render_observation
                import json

                if len(json.dumps(result).encode()) > 65536:
                    return dict(
                        status="error",
                        code="RESPONSE_LIMIT",
                        message="Result exceeds 64 KiB; narrow the query.",
                        effects="Inspect before retrying mutations.",
                    )
                return dict(status="ok", **result)
            except AssetError as e:
                return {
                    "effects": "Operation refused or failed. Inspect state before retrying; step/import/native API failures can have partial effects.",
                    **e.details,
                    "status": "error",
                    "code": e.code,
                    "message": str(e),
                }
            except Exception as e:
                if any(
                    x in str(e).lower()
                    for x in (
                        "cuda error 700",
                        "cudaerrorillegaladdress",
                        "illegal memory access",
                    )
                ):
                    self.scene.device_error = str(e)[:500]
                    return dict(
                        status="error",
                        code="DEVICE_ERROR",
                        message=self.scene.device_error,
                        effects="GPU context may be unusable. Do not retry runtime/sensor operations; inspect/save/Stop, then restart the disposable Kit.",
                    )
                return dict(
                    status="error",
                    # USD's own parse errors are a bad file, not a fault in this tool.
                    code="USD_ERROR" if type(e).__name__ == "ErrorException" else "APPLICATION_ERROR",
                    message=str(e)[:1500],
                    effects="unknown; inspect stage before retrying",
                )

        return run

    def _asset_dependencies(self, paths):
        """Closures for files this Kit may parse: under its approved roots or user stores.

        Not project stores: a project file found from the working directory is not an approval."""
        from isaac_mcp import assets
        from isaac_mcp.project_assets import refuse
        from isaac_mcp.usd_closure import closures, split_runtime

        self.scene.broker.require_profile()
        folders = [root for root, _ in self.scene.broker.roots.values()]
        folders += assets.user_stores()
        folders = [folder.resolve() for folder in folders]
        if not isinstance(paths, list) or not 1 <= len(paths) <= 64:
            refuse("LIMIT", "paths must list 1..64 USD files.")
        for path in paths:
            real = Path(str(path)).resolve()
            if not any(real.is_relative_to(folder) for folder in folders):
                refuse("ROOT_NOT_APPROVED", "This Kit's approved roots and user stores do not include: " + str(real))
        import isaacsim

        return dict(results=split_runtime(closures(paths), Path(isaacsim.__file__).parent.resolve()))

    def dispatch(self, name, a, *, camera_frame=None):
        s = self.scene
        if name == "assets.dependencies":
            return self._asset_dependencies(a["paths"])
        if name in TARGETLESS:
            a.pop("session_id", None)
            a.pop("stage_id", None)
            return self.advanced.targetless(name, a)
        if name in {
            "sim.body.state",
            "sim.joints.state",
            "sim.joints.command",
            "sim.joints.gains",
            "sim.joints.move",
            "sim.joints.teleport",
            "sim.simulation.step",
            "sim.camera.create",
            "sim.camera.capture",
            "sim.lidar.create",
            "sim.lidar.read",
        } or (name == "sim.timeline.control" and a.get("action") != "stop"):
            s.require_runtime()
        if name == "sim.stage.info":
            result = s.stage_info()
            self.advanced.invalidate()
            from .runtime import runtime_identity
            import omni.kit.app as kit_app

            result["runtime"] = runtime_identity()
            result["app_ready"] = kit_app.get_app().is_app_ready()
            result["sensor_resources"] = {
                "count": len(self.advanced.sensors),
                "cleanup_errors": self.advanced.cleanup_errors,
            }
            result["viewport_follow"] = self.follower.status()
            return result
        mutations = {
            "assets.import_urdf",
            "assets.import_usd",
            "assets.import_path",
            "sim.prim.create",
            "sim.prim.set_transform",
        }
        from .advanced import MUTATIONS

        mutations |= MUTATIONS
        if (
            name in mutations
            or name in ("sim.simulation.step", "sim.joints.move")
            or (name == "sim.timeline.control" and a.get("action") != "stop")
        ):
            import omni.kit.app as kit_app
            from isaac_mcp.project_assets import refuse

            if not kit_app.get_app().is_app_ready():
                refuse(
                    "APP_STARTING",
                    "Kit startup is still running. Wait until sim.stage.info reports app_ready=true; no scene action was issued.",
                )
        session_id, stage_id = a.pop("session_id"), a.pop("stage_id")
        stage = s.target(session_id, stage_id, name in mutations)
        if name == "sim.prim.get":
            return s.get(stage, **a)
        if name in ("sim.stage.tree", "sim.physics.inspect"):
            nodes, truncated = bounded(stage, a.get("root", "/"), a.get("limit", 100), a.get("depth"),
                                       a.get("include_runtime", name == "sim.physics.inspect"))
            return dict(
                nodes=[
                    dict(
                        path=str(p.GetPath()),
                        type=str(p.GetTypeName()),
                        active=p.IsActive(),
                        **(facts(p) if name == "sim.physics.inspect" else {}),
                    )
                    for p in nodes
                ],
                truncated=truncated,
            )
        if name == "sim.prim.find":
            contains = a.get("contains", "")
            kind = a.get("type_name", "")
            limit = a.get("limit", 100)
            from isaac_mcp.project_assets import refuse

            if not isinstance(contains, str) or len(contains) > 128 or len(kind) > 128:
                refuse("LIMIT", "Search strings <=128 characters required.")
            if type(limit) is not int or not 1 <= limit <= 200:
                refuse("LIMIT", "limit must be 1..200.")
            found = []
            count = 0
            truncated = False
            for p in stage.TraverseAll():
                count += 1
                if count > 10000:
                    truncated = True
                    break
                if contains in str(p.GetPath()) and (
                    not kind or p.GetTypeName() == kind
                ):
                    if len(found) == limit:
                        truncated = True
                        break
                    found.append(str(p.GetPath()))
            return dict(paths=found, truncated=truncated, visited=min(count, 10000))
        if name == "sim.prim.create":
            from .placement import flat_box_warning

            created = s.create(stage, **a)
            warning = flat_box_warning(a.get("kind"), a.get("scale") or (1, 1, 1), a.get("collision") or a.get("rigid"))
            return {**created, "warnings": [warning]} if warning else created
        if name == "sim.prim.set_transform":
            return s.transform(stage, **a)
        if name == "sim.stage.save":
            runtime_poses = None
            if not s.timeline.is_stopped() and not s.timeline.is_playing():
                from .physics import snapshot_poses

                s.require_runtime()
                runtime_poses = snapshot_poses(stage, s.timeline.get_current_time())
            return s.save(stage, runtime_poses=runtime_poses, **a)
        if name.startswith("assets."):
            a.pop("root_id", None)
            a.pop("relative_path", None)
            if name == "assets.import_urdf":
                return self.advanced.import_urdf(stage, **a)
            if name == "assets.info":
                return s.inspect_asset(**a)[2]
            if name == "assets.prepare_workstation":
                return s.prepare_workstation(**a)
            if name in ("assets.import_path", "assets.import_usd"):
                return s.import_path(stage, **a)
            raise RuntimeError("Unknown asset operation: " + name)
        if name == "sim.body.state":
            from .advanced import physics_manager

            physics_manager()
            from isaac_mcp.project_assets import refuse
            from pxr import UsdPhysics

            from .physics import body_path

            a["path"] = body_path(a)
            prim = s.prim(stage, a["path"])
            if s.timeline.is_stopped():
                refuse("PHYSICS_NOT_READY", "The timeline is stopped, so there is no runtime body state. Play "
                       "(sim__timeline__control action=play) and let a few frames run; on Stop, robots return "
                       "to their authored start pose.")
            if not prim.IsActive() or not prim.HasAPI(UsdPhysics.RigidBodyAPI):
                refuse("PHYSICS_NOT_READY", f"{a['path']} is not an active rigid body (no RigidBodyAPI). Use a "
                       "link with physics, e.g. the robot's base link, or sim__prim__get for any prim's pose.")
            from .physics import body_state

            return body_state(
                a["path"],
                s.timeline.get_current_time(),
                a.get("include_dynamics", False),
                stage,
            )
        if name == "sim.timeline.control":
            from isaac_mcp.project_assets import refuse

            if a["action"] not in ("play", "pause", "stop"):
                refuse(
                    "INVALID_ACTION",
                    "Use play, pause or stop; exact stepping uses sim__simulation__step.",
                )
            if a["action"] == "play":
                from .physics import prepare

                prepare(stage)
                s.begin_runtime_edit(stage)
            getattr(s.timeline, a["action"])()
            return dict(
                action=a["action"],
                playing=s.timeline.is_playing(),
                stopped=s.timeline.is_stopped(),
                time_s=s.timeline.get_current_time(),
                effects="Timeline request issued; physics readiness unknown.",
            )
        if name == "sim.viewport.frame":
            from omni.kit.viewport.utility import (
                get_active_viewport,
                frame_viewport_selection,
            )
            from omni.kit.undo import disabled
            from isaac_mcp.project_assets import refuse
            from .viewport import frame, place

            follow = a.get("follow")
            if follow is not None and type(follow) is not bool:
                refuse("INVALID_VALUE", "follow must be true or false.")
            if follow is False:
                stopped = self.follower.stop("follow=false")
                return dict(stopped=stopped, **self.follower.status())
            plain = not any(a.get(k) is not None for k in ("margin", "eye", "target")) and not follow
            if plain and not a.get("path"):  # refuse before touching a running follow
                refuse("INVALID_VALUE", "Give path to frame, or eye and target.")
            # Any other viewport call takes the camera back from a running follow.
            self.follower.stop("replaced by a new sim.viewport.frame call")
            viewport = get_active_viewport()
            if plain:
                return frame(
                    s, stage, a["path"], omni.usd.get_context(), viewport,
                    frame_viewport_selection, disabled,
                )
            result, spec = place(s, stage, omni.usd.get_context(), viewport, a, disabled)
            if spec is not None:
                self.follower.start(s, stage, viewport, s.timeline, spec, disabled)
                result.update(following=True, note="The camera moves with the prim while the timeline runs; "
                              "call again with follow=false to stop. sim.stage.info reports viewport_follow.")
            return result
        if name == "sim.graph.show":
            a.update(session_id=session_id, stage_id=stage_id)
        return self.advanced.execute(name, stage, a, camera_frame=camera_frame)

import os
import sys
from pathlib import Path

# This repository ships the stdlib broker/contracts with the host wheel as well.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "mcp"))
import omni.ext
import omni.usd
import omni.timeline
from isaac_mcp_bridge import get_mcp_bridge, ToolDefinition
from isaac_mcp.scene_contracts import REGISTRY
from isaac_mcp.project_assets import AssetError
from .core import Scene, bounded, facts
from .render_export import validate_native_binding


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
        for namespace in ("sim", "assets"):
            registrar = self.bridge.register_owner(
                "generic-" + namespace, namespace, display_name="Generic " + namespace
            )
            for canonical, meta in REGISTRY.items():
                ns, name = canonical.split(".", 1)
                if ns == namespace and canonical not in (
                    "assets.roots",
                    "assets.scan",
                    "assets.search",
                ):
                    registrar.add(
                        ToolDefinition(
                            name=name,
                            description=meta["description"],
                            parameters=meta["parameters"],
                            handler=self.handler(canonical),
                        )
                    )

    def on_shutdown(self):
        self._timeline_events = None
        self._runtime_edit_restorer.cancel()
        self._stage_events = None
        self.advanced.close_sensors()
        self.scene.close_stage()
        for namespace in ("sim", "assets"):
            self.bridge.unregister_owner("generic-" + namespace)

    def _stage_event(self, event):
        if event.type == int(omni.usd.StageEventType.CLOSING):
            self._runtime_edit_restorer.cancel()
            self.advanced.close_sensors(stage_closing=True)
            self.scene.close_stage()

    def _timeline_event(self, event):
        if event.type == int(omni.timeline.TimelineEventType.STOP):
            self._runtime_edit_restorer.stopped()
        elif event.type == int(omni.timeline.TimelineEventType.PLAY):
            self._runtime_edit_restorer.cancel()

    async def _wait_sensor_frame(self, name, args):
        """Observe ordinary rendering without changing timeline or re-entering Kit."""
        from isaac_mcp.project_assets import refuse
        import time

        s = self.scene
        s.require_runtime()
        s.target(args["session_id"], args["stage_id"])
        self.advanced.invalidate()
        expected = "camera" if name == "sim.camera.capture" else "lidar"
        entry = self.advanced.sensors.get(args["path"])
        if entry is None or entry[0] != expected:
            refuse("NOT_OWNED", "Sensor is not registered in this session.")
        wait = args.pop("wait_for_render", expected == "camera")
        if type(wait) is not bool:
            refuse("INVALID_VALUE", "wait_for_render must be boolean.")
        if not wait and expected == "lidar":
            return {
                "waited": False,
                "meaning": "latest completed LiDAR scan; freshness not established by this call",
            }, None
        if expected == "lidar" and not s.timeline.is_playing():
            refuse(
                "SENSOR_PAUSED",
                "LiDAR acquisition requires a playing timeline. Use wait_for_render=false for the latest completed scan, or explicitly play and allow warm-up.",
            )
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
            from .camera_capture import CameraFrame, cached, capture

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
        return {
            "waited": True,
            "render_product": entry[1]["product_path"],
            **observation,
            "wait_seconds": time.monotonic() - begin,
            "timeline_time_before": timeline_before,
            "timeline_time_after": s.timeline.get_current_time(),
            "meaning": "matching render completed after request; not an atomic physics/sensor snapshot",
        }, None

    def handler(self, name):
        async def run(**args):
            try:
                async with self._operation_lock:
                    render_observation = None
                    camera_frame = None
                    timeline_target = None
                    timeline_was_stopped = False
                    if name == "sim.timeline.control":
                        import omni.kit.app as kit_app
                        from .timeline import settle_stop

                        timeline_target = (args["session_id"], args["stage_id"])
                        timeline_was_stopped = self.scene.timeline.is_stopped()

                        def validate_stopped():
                            self.scene.target(*timeline_target)
                            if not self.scene.timeline.is_stopped():
                                from isaac_mcp.project_assets import refuse

                                refuse(
                                    "TIMELINE_INTERRUPTED",
                                    "Timeline changed while awaiting native Stop; no automatic Play retry.",
                                )

                        if args.get("action") == "play" and self.scene.timeline.is_stopped():
                            await settle_stop(kit_app.get_app().next_update_async, validate_stopped)
                    if name in ("sim.camera.capture", "sim.lidar.read"):
                        (
                            render_observation,
                            camera_frame,
                        ) = await self._wait_sensor_frame(name, args)
                    result = self.dispatch(name, args, camera_frame=camera_frame)
                    if render_observation is not None:
                        result["render_observation"] = render_observation
                    if name == "sim.timeline.control":
                        import asyncio
                        import omni.kit.app as kit_app

                        # Await the ordinary frame, never recursively pump the app loop.
                        await asyncio.wait_for(kit_app.get_app().next_update_async(), 5)
                        self.scene.target(*timeline_target)
                        if args["action"] == "stop":
                            result["native_stop_observation"] = await settle_stop(
                                kit_app.get_app().next_update_async,
                                validate_stopped,
                            )
                        if self.scene.timeline.is_stopped():
                            result["edit_target_restored"] = (
                                self.scene.end_runtime_edit(self.scene.stage())
                            )
                        result.update(
                            playing=self.scene.timeline.is_playing(),
                            stopped=self.scene.timeline.is_stopped(),
                            time_s=self.scene.timeline.get_current_time(),
                        )
                        result["effects"] = (
                            "Timeline state observed after an application update; physics readiness requires a runtime read."
                        )
                        expected = {
                            "play": result["playing"],
                            "stop": result["stopped"],
                            "pause": not result["playing"] and (
                                not result["stopped"] or timeline_was_stopped
                            ),
                        }[args["action"]]
                        if not expected:
                            from isaac_mcp.project_assets import refuse

                            refuse(
                                "TIMELINE_INTERRUPTED",
                                "Requested timeline state was not retained; inspect the returned state before retrying.",
                                observed_state=result,
                            )
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
                return dict(
                    status="error",
                    code=e.code,
                    message=str(e),
                    effects="Operation refused or failed. Inspect state before retrying; step/import/native API failures can have partial effects.",
                    **e.details,
                )
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
                    code="APPLICATION_ERROR",
                    message=str(e)[:1500],
                    effects="unknown; inspect stage before retrying",
                )

        return run

    def dispatch(self, name, a, *, camera_frame=None):
        s = self.scene
        if name in {
            "sim.body.state",
            "sim.joints.state",
            "sim.joints.command",
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
            return result
        mutations = {
            "assets.import_urdf",
            "assets.import_usd",
            "sim.prim.create",
            "sim.prim.set_transform",
        }
        from .advanced import MUTATIONS

        mutations |= MUTATIONS
        if (
            name in mutations
            or name == "sim.simulation.step"
            or (name == "sim.timeline.control" and a.get("action") != "stop")
        ):
            import omni.kit.app as kit_app
            from isaac_mcp.project_assets import refuse

            if not kit_app.get_app().is_app_ready():
                refuse(
                    "APP_STARTING",
                    "Kit startup is still running. Wait until sim.stage.info reports app_ready=true; no scene action was issued.",
                )
        stage = s.target(a.pop("session_id"), a.pop("stage_id"), name in mutations)
        if name == "sim.prim.get":
            return s.get(stage, **a)
        if name in ("sim.stage.tree", "sim.physics.inspect"):
            nodes, truncated = bounded(stage, a.get("root", "/"), a.get("limit", 100))
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
            return s.create(stage, **a)
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
            return s.import_asset(stage, **a)
        if name == "sim.body.state":
            from .advanced import physics_manager

            physics_manager()
            from isaac_mcp.project_assets import refuse
            from pxr import UsdPhysics

            prim = s.prim(stage, a["path"])
            if (
                s.timeline.is_stopped()
                or not prim.IsActive()
                or not prim.HasAPI(UsdPhysics.RigidBodyAPI)
            ):
                refuse(
                    "PHYSICS_NOT_READY",
                    "Need an active rigid body and initialized playing/paused simulation on the selected backend.",
                )
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
                    "Use play, pause or stop; exact stepping uses sim.simulation.step.",
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
            s.prim(stage, a["path"])
            from omni.kit.viewport.utility import (
                get_active_viewport,
                frame_viewport_selection,
            )
            from isaac_mcp.project_assets import refuse

            viewport = get_active_viewport()
            if viewport is None:
                refuse("NO_VIEWPORT", "No active viewport in this Kit.")
            omni.usd.get_context().get_selection().set_selected_prim_paths(
                [a["path"]], False
            )
            frame_viewport_selection(viewport)
            return dict(
                selected_path=a["path"],
                camera=str(viewport.camera_path),
                capture="Use screenshot with this application PID/window ID.",
            )
        return self.advanced.execute(name, stage, a, camera_frame=camera_frame)

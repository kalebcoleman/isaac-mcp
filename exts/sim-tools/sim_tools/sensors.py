"""Owned render products and LiDAR CPU storage for released RTX APIs.

Only typed, locally authored sensors. No model-selected config file, texture,
URL, annotator, extension or code. Native creation may have partial effects;
cleanup is attempted and reported, never disguised as a successful creation.
"""

from pxr import UsdGeom
from isaac_mcp.project_assets import refuse
from .runtime import require_sensor_api


def dispose(sensor, stage_closing=False):
    from .camera_capture import invalidate

    invalidate(sensor)
    errors = []
    if sensor.get("annotator") is None and sensor.get("product") is None:
        return errors
    stage = sensor.get("stage")
    unsafe = None
    if stage_closing:
        # Replicator/SyntheticData already reset graphs at CLOSING and destroy
        # their textures at CLOSED. Do not mutate that graph/USD teardown from
        # another stage-event callback; only relinquish our owned handles.
        unsafe = "stage teardown owns graph cleanup"
    elif stage is not None:
        import omni.usd

        try:
            if omni.usd.get_context().get_stage() != stage:
                unsafe = (
                    "Stage changed; global Replicator graph cleanup was not attempted"
                )
            elif sensor.get("product_path"):
                prim = stage.GetPrimAtPath(sensor["product_path"])
                owned = sensor.get("product_layers", (stage.GetSessionLayer(),))
                if prim and any(
                    spec.layer not in owned for spec in prim.GetPrimStack()
                ):
                    unsafe = "Render product has external layer opinions; USD preserved"
        except Exception as e:
            unsafe = "Could not verify cleanup stage ownership: " + str(e)[:200]
    if unsafe:
        # BOTH Annotator.detach and HydraTexture.destroy consult the current
        # global USD context. Neither may run against a different/foreign stage.
        product = sensor.get("product")
        if product is not None:
            try:
                texture = getattr(product, "hydra_texture", None)
                if texture is not None:
                    texture.set_updates_enabled(False)
            except Exception as e:
                errors.append("Texture release: " + str(e)[:200])
            finally:
                # HydraTexture.__del__ calls destroy if this handle remains.
                product.hydra_texture = None
        sensor["annotator"] = sensor["product"] = None
        return errors if stage_closing else [unsafe, *errors]
    for key, method in (("annotator", "detach"), ("product", "destroy")):
        obj = sensor.get(key)
        if obj is not None:
            try:
                if key == "product" and sensor.get("stage") is not None:
                    import omni.usd

                    stage = sensor["stage"]
                    current = omni.usd.get_context().get_stage()
                    prim = stage.GetPrimAtPath(sensor["product_path"])
                    # Hydra authors the new product in both the root and the
                    # session layer even inside an EditContext. These are the
                    # exact layers recorded at creation, not all layers of the
                    # currently selected stage. The path is uniquely ours.
                    owned_layers = sensor.get(
                        "product_layers", (stage.GetSessionLayer(),)
                    )
                    foreign = prim and any(
                        spec.layer not in owned_layers for spec in prim.GetPrimStack()
                    )
                    if current != stage or foreign:
                        # Replicator.destroy() removes its path from ALL layers
                        # of the CURRENT stage. Never let that touch another
                        # stage or an externally authored opinion. Release our
                        # texture without calling that cross-layer destructor.
                        texture = getattr(obj, "hydra_texture", None)
                        if texture is not None:
                            texture.set_updates_enabled(False)
                            obj.hydra_texture = None
                        sensor[key] = None
                        if foreign:
                            errors.append(
                                "Render product has external layer opinions; texture released, USD preserved"
                            )
                        continue
                getattr(obj, method)()
                sensor[key] = None
            except Exception as e:
                errors.append(f"{key}.{method}: {str(e)[:300]}")
    return errors


def create(stage, path, kind, width=640, height=480):
    rep, identity = require_sensor_api(lidar=kind == "lidar")
    if kind == "lidar":
        import carb.settings

        if not carb.settings.get_settings().get("/renderer/raytracingMotion/enabled"):
            refuse(
                "SENSOR_SETUP_REQUIRED",
                "RTX LiDAR requires Motion BVH for correct motion effects. Launch a separate managed session with enable_motion_bvh=true, or use run.sh --motion-bvh. Existing renderer settings were not changed.",
            )
    sensor = dict(
        adapter=(
            "hydra-renderer-cpu-v1" if kind == "camera" else "replicator-owned-cpu-v1"
        ),
        release=identity["release"],
        build=identity["build"],
        stage=stage,
    )
    try:
        if kind == "camera":
            camera = UsdGeom.Camera.Define(stage, path)
            camera.CreateVerticalApertureAttr(
                camera.GetHorizontalApertureAttr().Get() * height / width
            )
            sensor["resolution"] = (height, width)
        else:
            # This supported command exists before experimental.rtx was shipped.
            # config=None creates a local default sensor, never fetches an asset.
            try:
                import omni.kit.commands
                import isaacsim.sensors.rtx  # registers the native command/annotator
            except ImportError as e:
                refuse(
                    "SENSOR_API_UNAVAILABLE", "Enable isaacsim.sensors.rtx: " + str(e)
                )
            ok, prim = omni.kit.commands.execute(
                "IsaacSensorCreateRtxLidar", path=str(path), parent=None, config=None
            )
            if not ok or not prim or str(prim.GetPath()) != str(path):
                refuse(
                    "SENSOR_CREATE_FAILED",
                    "Native LiDAR command did not create the exact requested path.",
                )
            attr = prim.GetAttribute("omni:sensor:Core:outputFrameOfReference")
            if not attr:
                refuse(
                    "SENSOR_API_UNAVAILABLE",
                    "LiDAR lacks an explicit output-frame attribute.",
                )
            attr.Set("SENSOR")
            # Full scan scheduling is a USD setting in 6.x. A tick rate which
            # differs from scanRateBaseHz silently produces partial scans.
            rate = prim.GetAttribute("omni:sensor:Core:scanRateBaseHz").Get()
            if not isinstance(rate, (float, int)) or not 0 < rate <= 240:
                refuse(
                    "SENSOR_FORMAT", "Default LiDAR scan rate is outside (0,240] Hz."
                )
            if not prim.GetAttribute("omni:sensor:tickRate").Set(rate):
                refuse("SENSOR_API_UNAVAILABLE", "LiDAR tick-rate schema unavailable.")
            accumulate = prim.GetAttribute("omni:sensor:Core:accumulateOutputs")
            if accumulate:
                accumulate.Set(True)
                sensor["scan_adapter"] = "usd-accumulation"
                annotator_name = "IsaacExtractRTXSensorPointCloudNoAccumulator"
            else:
                # 6.0.0.0 predates this USD schema. Its public full-scan
                # annotator owns accumulation and supplies the same CPU XYZ
                # contract; do not invent an ignored custom USD attribute.
                sensor["scan_adapter"] = "legacy-scan-buffer"
                annotator_name = "IsaacCreateRTXLidarScanBuffer"
            sensor["scan_rate_hz"] = rate
            width = height = 128
        import uuid
        import inspect

        options = {}
        if (
            "write_to_session_layer"
            in inspect.signature(rep.create.render_product).parameters
        ):
            # Later Replicator builds otherwise explicitly author render-vars in
            # the root layer. False respects our surrounding session EditContext.
            # 6.0.0.0 has no such keyword; its native creation is tracked below.
            options["write_to_session_layer"] = False

        product = rep.create.render_product(
            str(path),
            resolution=(width, height),
            force_new=True,
            name="IsaacMCP_" + uuid.uuid4().hex,
            **options,
        )
        sensor["product"] = product
        sensor["product_path"] = product.path
        sensor["product_layers"] = (stage.GetSessionLayer(), stage.GetRootLayer())
        import omni.usd

        if omni.usd.get_context().get_stage() != stage:
            refuse("STALE_TARGET", "Stage changed during native sensor creation.")
        sensor["prim"] = stage.GetPrimAtPath(path)
        if kind == "camera":
            # On-demand renderer readback needs no continuously attached RGB
            # annotator. That redundant SDG branch caused paused ROS RGB writer
            # cycles in the fresh 6.2 headless coexistence control.
            return sensor
        # At ATTACHMENT, not just at retrieval: borrowed GPU storage crashed in
        # real use. There is deliberately no CameraSensor or GPU-copy fallback.
        annotator = rep.AnnotatorRegistry.get_annotator(
            annotator_name, device="cpu", do_array_copy=True
        )
        sensor["annotator"] = annotator
        if sensor.get("scan_adapter") == "legacy-scan-buffer":
            annotator.initialize(enablePerFrameOutput=False)
        annotator.attach([product])
        return sensor
    except Exception as e:
        cleanup_errors = dispose(sensor)
        refuse(
            "SENSOR_CREATE_FAILED",
            f"Sensor creation failed: {e}. Native effects may remain at {path}; cleanup errors={cleanup_errors}",
        )


def read(sensor):
    # Already qualified at attachment; fail if its resources were invalidated.
    annotator = sensor.get("annotator")
    if annotator is None:
        refuse(
            "SENSOR_INVALIDATED",
            "Sensor resources were released; create a new sensor on the current stage.",
        )
    data = annotator.get_data(device="cpu", do_array_copy=True)
    info = data.get("info", {}) if isinstance(data, dict) else {}
    data = data.get("data") if isinstance(data, dict) else data
    if data is None or getattr(data, "size", 0) == 0:
        refuse(
            "NOT_READY",
            "No rendered sensor data yet. Allow ordinary rendered frames while playing; warm-up can take several seconds. No implicit Play or nested update.",
        )
    return data, info


def lidar_points(sensor, limit):
    import numpy as np

    data, info = read(sensor)
    if not isinstance(data, np.ndarray) or data.ndim != 2 or data.shape[1] != 3:
        refuse(
            "SENSOR_FORMAT",
            "Expected CPU Cartesian Nx3 points; no GPU-pointer fallback.",
        )
    if len(data) > 2_000_000:
        refuse("LIMIT", "LiDAR frame exceeds two million points.")
    points = data[np.isfinite(data).all(axis=1) & (np.linalg.norm(data, axis=1) > 0)]
    if not len(points):
        refuse(
            "NOT_READY",
            "LiDAR has no valid returns; allow rendered warm-up and put visible geometry within range.",
        )
    transform = np.asarray(info.get("transform", []))
    if transform.size != 16 or not np.isfinite(transform).all():
        refuse(
            "SENSOR_FORMAT",
            "LiDAR frame is missing its finite sensor-to-world transform.",
        )
    return dict(
        points=points[:limit].tolist(),
        valid_count=len(points),
        truncated=len(points) > limit,
        source_coordinates="CARTESIAN",
        frame="SENSOR",
        sensor_to_world=transform.reshape(4, 4).tolist(),
        matrix_convention="row vectors; translation in last row",
        units="meters (RTX)",
        adapter=sensor["adapter"],
        scan_rate_hz=sensor.get("scan_rate_hz"),
        scan_adapter=sensor.get("scan_adapter"),
        freshness="latest sensor scan; render completion does not imply a new rotating scan",
    )

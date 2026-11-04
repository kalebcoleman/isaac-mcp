"""Owned render products and LiDAR CPU storage for released RTX APIs.

Typed sensors: a locally authored camera/generic LiDAR, or a LiDAR from NVIDIA's fixed
config registry (referenced from the Isaac assets root, as the Create menu does). No
model-selected file, texture, URL, annotator, extension or code. Native creation may have partial effects;
cleanup is attempted and reported, never disguised as a successful creation.
"""

import math
from pathlib import PurePosixPath

from pxr import Gf, Usd, UsdGeom
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


def camera_clipping_range(stage):
    """Use physical distances; USD's fallback near plane is one stage unit."""
    units = UsdGeom.GetStageMetersPerUnit(stage)
    if not math.isfinite(units) or units <= 0:
        refuse("INVALID_STAGE_UNITS", "Camera creation needs finite positive metersPerUnit; stage units were not changed.")
    near, far = 0.01 / units, 1000.0 / units
    if not all(math.isfinite(v) and 0 < v <= 3.4028234663852886e38 for v in (near, far)):
        refuse("INVALID_STAGE_UNITS", "Camera clipping distances cannot be represented in this stage's units.")
    clipping = Gf.Vec2f(near, far)
    if not 0 < clipping[0] < clipping[1]:
        refuse("INVALID_STAGE_UNITS", "Camera clipping distances underflow in this stage's units.")
    return clipping


def create(stage, path, kind, width=640, height=480, *, lidar_asset=None, tick_rate=None, optics=None):
    """Create a camera or LiDAR and its owned render product.

    ``lidar_asset`` is a resolved NVIDIA registry entry (asset path, variant) from
    placement.resolve_lidar_config; None creates the local generic LiDAR. ``optics`` comes from
    placement.camera_optics.
    """
    if kind == "camera":
        clipping = camera_clipping_range(stage)
        if optics is not None:
            clipping = Gf.Vec2f(*optics["clipping_range_stage_units"])
            if not 0 < clipping[0] < clipping[1] or not all(math.isfinite(v) for v in clipping):
                refuse("INVALID_STAGE_UNITS", "Camera clipping distances cannot be represented in this stage's units.")
    rep, identity = require_sensor_api(lidar=kind == "lidar")
    sensor = dict(
        adapter=(
            "hydra-renderer-cpu-v1" if kind == "camera" else "replicator-owned-cpu-v1"
        ),
        release=identity["release"],
        build=identity["build"],
        stage=stage,
    )
    product_path = str(path)
    try:
        if kind == "camera":
            camera = UsdGeom.Camera.Define(stage, path)
            if optics and optics.get("focal_length") is not None:
                camera.CreateFocalLengthAttr(optics["focal_length"])
            if optics and optics.get("horizontal_aperture") is not None:
                camera.CreateHorizontalApertureAttr(optics["horizontal_aperture"])
            camera.CreateVerticalApertureAttr(
                camera.GetHorizontalApertureAttr().Get() * height / width
            )
            if not camera.CreateClippingRangeAttr().Set(clipping):
                refuse("SENSOR_CREATE_FAILED", "Could not author camera clipping range.")
            sensor["clipping_range_stage_units"] = list(clipping)
            sensor["resolution"] = (height, width)
            sensor["focal_length_mm"] = camera.GetFocalLengthAttr().Get()
            sensor["horizontal_aperture_mm"] = camera.GetHorizontalApertureAttr().Get()
            sensor["vertical_aperture_mm"] = camera.GetVerticalApertureAttr().Get()
        else:
            if lidar_asset is not None:
                prim = _create_config_lidar(stage, path, lidar_asset, tick_rate, sensor)
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
                from .compat import generic_lidar_supported

                if not generic_lidar_supported():
                    refuse("CONFIG_REQUIRED", "On this build a LiDAR needs a config: its native command can't place a "
                           "default sensor at an exact path. Pass config, for example config=\"Example_Rotary_2D\". "
                           "Nothing was created.")
                ok, prim = omni.kit.commands.execute(
                    "IsaacSensorCreateRtxLidar", path=str(path), parent=None, config=None
                )
                if not ok or not prim or str(prim.GetPath()) != str(path):
                    refuse(
                        "SENSOR_CREATE_FAILED",
                        f"The native LiDAR command made {prim.GetPath() if prim else 'nothing'} instead of {path}. "
                        "Inspect the stage. Pass config, for example config=\"Example_Rotary_2D\".",
                    )
            sensor["sensor_path"] = product_path = str(prim.GetPath())
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
                    "SENSOR_FORMAT", "LiDAR scan rate is outside (0,240] Hz."
                )
            tick = prim.GetAttribute("omni:sensor:tickRate")
            if not tick or not tick.Set(float(tick_rate if tick_rate is not None else rate)):
                refuse("SENSOR_API_UNAVAILABLE", "LiDAR tick-rate schema unavailable.")
            sensor["tick_rate_hz"] = tick.Get()
            if abs(sensor["tick_rate_hz"] - rate) > 1e-6:
                sensor["warning"] = (
                    f"tick_rate {sensor['tick_rate_hz']:g} Hz differs from the scan rate {rate:g} Hz: "
                    "each output can be a partial scan. Omit tick_rate to use the scan rate."
                )
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
        return _attach(stage, rep, sensor, product_path, kind, width, height,
                       None if kind == "camera" else annotator_name)
    except Exception as e:
        if getattr(e, "code", None) == "CONFIG_REQUIRED":  # refused before anything was made: nothing to clean up
            raise
        cleanup_errors = dispose(sensor)
        refuse(
            "SENSOR_CREATE_FAILED",
            f"Sensor creation failed: {e}. Native effects may remain at {path}; cleanup errors={cleanup_errors}",
        )


def _attach(stage, rep, sensor, product_path, kind, width, height, annotator_name):
    """Create the owned render product (and LiDAR annotator) for an existing sensor prim."""
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
        product_path,
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
    sensor["prim"] = stage.GetPrimAtPath(product_path)
    if kind == "camera":
        # On-demand renderer readback needs no continuously attached RGB
        # annotator. This removes a redundant SDG branch, but native ROS
        # writer cycle warnings also reproduced without it in fresh tests.
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


def find_sensor_prim(stage, path, kind):
    """The sensor prim for an exact path, or None. NVIDIA's config LiDARs can wrap their OmniLidar
    in a referencing prim, so one single OmniLidar below *path* also identifies it."""
    prim = stage.GetPrimAtPath(path)
    if not prim:
        return None
    if kind == "camera":
        return prim if prim.IsA(UsdGeom.Camera) else None
    if prim.GetTypeName() == "OmniLidar":
        return prim
    found = [p for p in Usd.PrimRange(prim) if p.GetTypeName() == "OmniLidar"]
    return found[0] if len(found) == 1 else None


def _set_adopted(prim, name, value, overrides):
    attr = prim.GetAttribute(name)
    if attr and attr.Get() != value:
        previous = attr.Get()
        attr.Set(value)  # the caller's EditContext is the session layer
        overrides.append(dict(property=name, previous=previous, session_value=value, attribute=attr))


def adopt(stage, path, kind):
    """Register owned render resources for a sensor this session did not create (a reopened scene).

    Same adapters and annotators as create. The sensor prim's source layers are not edited. A LiDAR
    needs SENSOR-frame output and accumulated scans for a correct read: differing values are
    overridden in the session layer and reported. Nothing is inferred from names.
    """
    prim = find_sensor_prim(stage, path, kind)
    expected = "Camera" if kind == "camera" else "OmniLidar"
    if prim is None:
        found = stage.GetPrimAtPath(path)
        refuse(
            "NOT_A_SENSOR",
            f"{path} is a {found.GetTypeName() or 'typeless prim'}, not {'a' if expected == 'Camera' else 'an'} {expected}, and adopt only accepts the "
            f"sensor at that exact path. Find one with sim__prim__find type_name={expected}.",
        )
    if not prim.IsActive() or prim.IsInstanceProxy():
        refuse("SENSOR_INVALIDATED", f"{prim.GetPath()} is inactive or an instance proxy; it cannot be adopted.")
    rep, identity = require_sensor_api(lidar=kind == "lidar")
    sensor = dict(
        adapter="hydra-renderer-cpu-v1" if kind == "camera" else "replicator-owned-cpu-v1",
        release=identity["release"],
        build=identity["build"],
        stage=stage,
        adopted=True,
    )
    product_path = str(prim.GetPath())
    overrides = []
    annotator_name = None
    width, height = 128, 128
    try:
        if kind == "camera":
            camera = UsdGeom.Camera(prim)
            horizontal = camera.GetHorizontalApertureAttr().Get()
            vertical = camera.GetVerticalApertureAttr().Get()
            width = 640
            height = 480
            if horizontal and vertical and horizontal > 0 and vertical > 0:
                height = max(64, min(1024, round(width * vertical / horizontal)))
            sensor["resolution"] = (height, width)
            sensor["focal_length_mm"] = camera.GetFocalLengthAttr().Get()
            sensor["horizontal_aperture_mm"] = horizontal
            sensor["vertical_aperture_mm"] = vertical
        else:
            sensor["sensor_path"] = product_path
            if not prim.GetAttribute("omni:sensor:Core:outputFrameOfReference"):
                refuse("SENSOR_API_UNAVAILABLE", "LiDAR lacks an explicit output-frame attribute.")
            _set_adopted(prim, "omni:sensor:Core:outputFrameOfReference", "SENSOR", overrides)
            rate = prim.GetAttribute("omni:sensor:Core:scanRateBaseHz").Get()
            if not isinstance(rate, (float, int)) or not 0 < rate <= 240:
                refuse("SENSOR_FORMAT", "LiDAR scan rate is outside (0,240] Hz.")
            sensor["scan_rate_hz"] = rate
            sensor["tick_rate_hz"] = prim.GetAttribute("omni:sensor:tickRate").Get()
            if prim.GetAttribute("omni:sensor:Core:accumulateOutputs"):
                _set_adopted(prim, "omni:sensor:Core:accumulateOutputs", True, overrides)
                sensor["scan_adapter"] = "usd-accumulation"
                annotator_name = "IsaacExtractRTXSensorPointCloudNoAccumulator"
            else:
                sensor["scan_adapter"] = "legacy-scan-buffer"
                annotator_name = "IsaacCreateRTXLidarScanBuffer"
        _attach(stage, rep, sensor, product_path, kind, width, height, annotator_name)
    except Exception as e:
        cleanup_errors = dispose(sensor)
        for override in overrides:
            override["attribute"].Clear()
        if getattr(e, "code", None) in ("SENSOR_API_UNAVAILABLE", "SENSOR_FORMAT", "STALE_TARGET"):
            raise
        refuse(
            "SENSOR_CREATE_FAILED",
            f"Could not adopt {path}: {e}. Cleanup errors={cleanup_errors}",
        )
    sensor["session_overrides"] = [
        {k: v for k, v in override.items() if k != "attribute"} for override in overrides
    ]
    return sensor


def _create_config_lidar(stage, path, lidar_asset, tick_rate, sensor):
    """NVIDIA's own 6.x creation path (the Create > Sensors menu): reference the vendor asset."""
    asset_path, variant = lidar_asset
    try:
        from isaacsim.sensors.experimental.rtx import Lidar
    except ImportError:
        return _create_config_lidar_by_command(stage, path, asset_path, variant)
    try:
        from isaacsim.storage.native import get_assets_root_path
    except ImportError as e:
        refuse("SENSOR_API_UNAVAILABLE", "Config LiDARs need isaacsim.storage.native: " + str(e))
    try:  # read the configured root without probing it: a remote probe can block Kit for its whole timeout
        try:
            root = get_assets_root_path(skip_check=True)
        except TypeError:  # older API without skip_check
            root = get_assets_root_path()
    except RuntimeError:  # raised (not returned empty) when no assets root is configured
        root = None
    if not root:
        refuse("ASSETS_UNAVAILABLE", "Isaac Sim's assets root is not configured or unreachable, so the NVIDIA config cannot be loaded. Omit config for the local generic LiDAR.")
    sensor["config_asset"] = root + asset_path
    lidar = Lidar.create(str(path), usd_path=root + asset_path, variant=variant, tick_rate=tick_rate)
    prim = None
    for candidate in Usd.PrimRange(stage.GetPrimAtPath(path)):
        if candidate.GetTypeName() == "OmniLidar":
            prim = candidate
            break
    if prim is None or not lidar:
        refuse("SENSOR_CREATE_FAILED", f"The NVIDIA asset {asset_path} has no OmniLidar prim.")
    return prim


def _create_config_lidar_by_command(stage, path, asset_path, variant):
    """Isaac Sim 5.1 has no experimental.rtx; its supported command takes the config name directly."""
    try:
        import omni.kit.commands
        import isaacsim.sensors.rtx  # registers the native command/annotator
    except ImportError as e:
        refuse("SENSOR_API_UNAVAILABLE", "Enable isaacsim.sensors.rtx: " + str(e))
    ok, created = omni.kit.commands.execute(
        "IsaacSensorCreateRtxLidar", path=str(path), parent=None,
        config=PurePosixPath(asset_path).stem, variant=variant if isinstance(variant, str) else None,
    )
    prim = None
    if ok and created and stage.GetPrimAtPath(path):
        for candidate in Usd.PrimRange(stage.GetPrimAtPath(path)):
            if candidate.GetTypeName() == "OmniLidar":
                prim = candidate
                break
    if prim is None:
        refuse("SENSOR_CREATE_FAILED", f"The native command made no OmniLidar at {path} for {asset_path}.")
    return prim


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
            "No rendered sensor data yet, or nothing is in range. Allow ordinary rendered frames while playing; warm-up "
            "can take several seconds. A LiDAR needs visible geometry between its near and far range (1 m to 200 m for "
            "NVIDIA's Example_Rotary configs). On some builds (measured on 6.0.0.0) the buffer also empties on Pause: read "
            "while playing. No implicit Play or nested update.",
        )
    return data, info


LIDAR_SCAN_WAIT_FRAMES, LIDAR_SCAN_WAIT_S = 60, 1.5


def lidar_has_data(sensor):
    """Whether the annotator holds any real points now: a partial first scan can be all zeros. Never refuses: the read
    that follows does that."""
    annotator = sensor.get("annotator")
    if annotator is None:
        return True
    try:
        data = annotator.get_data(device="cpu", do_array_copy=False)
    except Exception:  # noqa: BLE001 - the read that follows reports this properly
        return True
    data = data.get("data") if isinstance(data, dict) else data
    return data is not None and getattr(data, "size", 0) > 0 and bool(data.any())


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

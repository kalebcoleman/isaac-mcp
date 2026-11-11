"""Cameras: the viewport camera, the wrist RealSense, extra cameras and their ROS graphs."""

import omni.kit.commands
import omni.usd
import omni.ui as ui
import numpy as np
import os
import traceback
from isaacsim.core.utils.stage import add_reference_to_stage
import omni.graph.core as og
from pxr import UsdGeom, Gf
from .settings import _local_asset, _use_legacy_tf_publisher


class Cameras:
    """Part of DigitalTwin (extension.py), which mixes it in."""

    def _list_stage_cameras(self):
        """Return all UsdGeom.Camera prim paths on the current stage (deduplicated).
        Always includes /OmniverseKit_Persp as a fallback even if the stage is empty."""
        from pxr import UsdGeom
        stage = omni.usd.get_context().get_stage()
        seen = set()
        cams = []
        if stage:
            for prim in stage.Traverse():
                if prim.IsA(UsdGeom.Camera):
                    p = str(prim.GetPath())
                    if p not in seen:
                        seen.add(p)
                        cams.append(p)
        if "/OmniverseKit_Persp" not in seen:
            cams.insert(0, "/OmniverseKit_Persp")
        return cams

    def _refresh_viewport_camera_list(self):
        """Rebuild the active-viewport ComboBox with all cameras currently on the stage.
        Default selection prefers /World/workspace_camera_sim if present.
        Selecting an item auto-switches the viewport (no Apply needed)."""
        cams = self._list_stage_cameras()
        default_pick = "/World/workspace_camera_sim"
        try:
            default_idx = cams.index(default_pick)
        except ValueError:
            default_idx = 0
        self._viewport_cam_frame.clear()
        with self._viewport_cam_frame:
            self._viewport_cam_combo = ui.ComboBox(default_idx, *cams, width=220)
        # Auto-apply on selection change
        self._viewport_cam_combo.model.add_item_changed_fn(
            lambda model, item: self._apply_active_viewport_camera()
        )
        print(f"Viewport camera list refreshed ({len(cams)} cameras)")

    def _set_active_viewport_camera(self, camera_path: str):
        """Switch the active viewport to render through the given camera prim path.
        Records the previous camera so _undo_viewport_camera can swap back."""
        try:
            import omni.kit.viewport.utility as vp_utils
            from pxr import Sdf
            viewport = vp_utils.get_active_viewport()
            if viewport is None:
                print("No active viewport")
                return False
            prev = str(viewport.camera_path)
            if prev != camera_path:
                self._prev_viewport_cam = prev
            viewport.camera_path = Sdf.Path(camera_path)
            print(f"Active viewport → {camera_path}")
            return True
        except Exception as ex:
            print(f"Error setting active viewport camera: {ex}")
            return False

    def _undo_viewport_camera(self):
        """Swap the active viewport back to the previously selected camera."""
        prev = getattr(self, "_prev_viewport_cam", None)
        if not prev:
            print("No previous viewport camera to undo to")
            return
        # Swapping makes the current camera the new "previous" — handled by _set_active_viewport_camera
        self._set_active_viewport_camera(prev)
        # Also reflect the change in the dropdown selection if the camera is in the list
        try:
            items = self._viewport_cam_combo.model.get_item_children()
            labels = [self._viewport_cam_combo.model.get_item_value_model(i).as_string for i in items]
            if prev in labels:
                self._viewport_cam_combo.model.get_item_value_model().set_value(labels.index(prev))
        except Exception:
            pass

    def _apply_active_viewport_camera(self):
        """Read the current ComboBox selection and apply it to the active viewport."""
        cams = self._list_stage_cameras()  # rebuild in same order as last refresh
        idx = self._viewport_cam_combo.model.get_item_value_model().as_int
        # Use the items from the combo's actual model rather than re-listing, to be safe
        item_models = self._viewport_cam_combo.model.get_item_children()
        if 0 <= idx < len(item_models):
            label = self._viewport_cam_combo.model.get_item_value_model(item_models[idx]).as_string
            self._set_active_viewport_camera(label)
        else:
            print(f"Invalid combo index {idx}")

    def _toggle_viewport_rendering(self):
        """Toggle viewport rendering on/off to free GPU resources."""
        import omni.kit.viewport.utility as vp_utils
        viewport = vp_utils.get_active_viewport()
        if viewport is None:
            print("Warning: No active viewport found")
            return
        self._viewport_rendering_enabled = not self._viewport_rendering_enabled
        viewport.updates_enabled = self._viewport_rendering_enabled
        state = "enabled" if self._viewport_rendering_enabled else "disabled"
        print(f"Viewport rendering {state}")
        if self._viewport_toggle_btn:
            self._viewport_toggle_btn.text = "Disable Viewport" if self._viewport_rendering_enabled else "Enable Viewport"

    # Standing-height view in front of the long side of the bench. Lens matches /World/workspace_camera_sim.
    PERSON_VIEW_PATH = "/World/person_view"

    PERSON_VIEW_EYE = (0.0, -2.2, 1.2)

    PERSON_VIEW_TARGET = (0.0, -0.3, 0.3)

    def _ensure_person_view_camera(self):
        """Create the fixed person-view camera if the stage does not have it, then return its prim path."""
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return None
        if not stage.GetPrimAtPath(self.PERSON_VIEW_PATH).IsValid():
            eye = np.array(self.PERSON_VIEW_EYE)
            forward = (np.array(self.PERSON_VIEW_TARGET) - eye)
            forward /= np.linalg.norm(forward)
            right = np.cross(forward, [0.0, 0.0, 1.0]); right /= np.linalg.norm(right)
            up = np.cross(right, forward)
            rows = [[*right, 0.0], [*up, 0.0], [*(-forward), 0.0], [*eye, 1.0]]
            camera = UsdGeom.Camera.Define(stage, self.PERSON_VIEW_PATH)
            camera.GetProjectionAttr().Set("perspective")
            camera.GetFocalLengthAttr().Set(25.995302200317383)
            camera.GetHorizontalApertureAttr().Set(36.0)
            camera.GetVerticalApertureAttr().Set(20.218040466308594)
            camera.GetClippingRangeAttr().Set(Gf.Vec2f(0.1, 10000.0))
            camera.GetFStopAttr().Set(0.0)
            camera.GetFocusDistanceAttr().Set(0.0)
            UsdGeom.Xformable(camera.GetPrim()).ClearXformOpOrder()
            UsdGeom.Xformable(camera.GetPrim()).AddTransformOp().Set(Gf.Matrix4d(rows))
        return self.PERSON_VIEW_PATH

    async def _select_person_view_when_loaded(self):
        """Make the person view the active viewport camera once the stage has settled after opening."""
        app = omni.kit.app.get_app()
        for _ in range(60):
            await app.next_update_async()
        stage = omni.usd.get_context().get_stage()
        if stage is None or not stage.GetPrimAtPath(self.PERSON_VIEW_PATH).IsValid():
            return
        try:
            from omni.kit.viewport.utility import get_active_viewport
            vp = get_active_viewport()
            if vp is not None:
                vp.camera_path = self.PERSON_VIEW_PATH
        except Exception as e:
            print(f"[ur5e-dt] could not select person view camera: {e}")

    def import_realsense_camera(self):
        """Import Intel RealSense D455 camera"""
        usd_path = _local_asset("sensors/rsd455.usd")
        filename = os.path.splitext(os.path.basename(usd_path))[0]
        prim_path = f"/World/{filename}"
        add_reference_to_stage(usd_path=usd_path, prim_path=prim_path)
        print(f"Prim imported at {prim_path}")

    def attach_camera_to_ur5e(self):
        """Attach RealSense camera to UR5e wrist"""
        import omni.kit.commands
        import omni.usd
        from pxr import Gf, Usd, UsdPhysics

        stage = omni.usd.get_context().get_stage()

        # Check camera prim exists
        if not stage.GetPrimAtPath("/World/rsd455").IsValid():
            print("[ERROR] Camera prim /World/rsd455 not found — import the RealSense camera first")
            return

        # Check UR5e wrist link exists
        if not stage.GetPrimAtPath("/World/UR5e/wrist_3_link").IsValid():
            print("[ERROR] /World/UR5e/wrist_3_link not found — import the UR5e first")
            return

        # Move the prim
        result = omni.kit.commands.execute('MovePrim',
                                 path_from="/World/rsd455",
                                 path_to="/World/UR5e/wrist_3_link/rsd455")
        if not result:
            print("[ERROR] Failed to move camera prim to wrist_3_link")
            return

        # Set transform properties
        omni.kit.commands.execute('ChangeProperty',
                                 prop_path="/World/UR5e/wrist_3_link/rsd455.xformOp:translate",
                                 value=Gf.Vec3d(0.045, -0.05, 0.07),
                                 prev=None)
        omni.kit.commands.execute('ChangeProperty',
                                 prop_path="/World/UR5e/wrist_3_link/rsd455.xformOp:rotateZYX",
                                 value=Gf.Vec3d(-90, -180, 270),
                                 prev=None)

        # Remove RigidBodyAPI only from the RSD455 prim to fix nested rigid body error.
        rsd455_prim = stage.GetPrimAtPath("/World/UR5e/wrist_3_link/rsd455/RSD455")
        if rsd455_prim.IsValid() and rsd455_prim.HasAPI(UsdPhysics.RigidBodyAPI):
            UsdPhysics.RigidBodyAPI(rsd455_prim).GetRigidBodyEnabledAttr().Set(False)
            rsd455_prim.RemoveAPI(UsdPhysics.RigidBodyAPI)
            print("Removed RigidBodyAPI from RSD455 (nested under wrist_3_link rigid body)")

        print("RealSense camera attached to UR5e wrist_3_link")

    def setup_camera_action_graph(self):
        """Create ActionGraph for camera ROS2 publishing"""
        import yaml

        CAMERA_PRIM = "/World/UR5e/wrist_3_link/rsd455/RSD455/Camera_OmniVision_OV9782_Color"

        # Check that camera prim exists
        stage = omni.usd.get_context().get_stage()
        if not stage.GetPrimAtPath(CAMERA_PRIM):
            print(f"Error: Camera prim not found at {CAMERA_PRIM}. Attach camera to UR5e first.")
            return

        # An optional camera config can override the default resolution.
        config_path = os.environ.get("UR5E_DT_CAMERA_CONFIG")
        if config_path and os.path.exists(os.path.expanduser(config_path)):
            config_path = os.path.expanduser(config_path)
            with open(config_path, 'r') as f:
                cam_cfg = yaml.safe_load(f) or {}
            print(f"Loaded camera config from {config_path}")
        else:
            cam_cfg = {}
            if config_path:
                print(f"Warning: {config_path} not found, using defaults")

        # Configuration from yaml (with fallbacks)
        IMAGE_WIDTH = cam_cfg.get("camera_width", 1280)
        IMAGE_HEIGHT = cam_cfg.get("camera_height", 720)
        ROS2_TOPIC = "intel_camera_rgb_sim"

        graph_path = "/Graph/ActionGraph_Camera"

        # Skip if already created
        if stage.GetPrimAtPath(graph_path):
            print(f"ActionGraph already exists at {graph_path}, skipping creation.")
            return

        # Native camera TF publishers already convert USD camera axes to ROS
        # optical axes. Override only the ROS name so it matches the existing
        # image/CameraInfo frame without adding a second rotation. Both native
        # TF adapters honor isaac:nameOverride. Keep the sensor asset untouched.
        from pxr import Sdf
        stage.GetPrimAtPath(CAMERA_PRIM).CreateAttribute(
            "isaac:nameOverride", Sdf.ValueTypeNames.String
        ).Set("camera_link")

        # Camera annotators and TF use the same monotonic simulation clock,
        # including after Stop/Play. Mixing reset-on-stop images with a
        # non-resetting TF clock leaves messages hundreds of seconds apart.
        print(f"Creating ActionGraph: {graph_path}")
        print(f"Camera: {CAMERA_PRIM}")
        print(f"Resolution: {IMAGE_WIDTH}x{IMAGE_HEIGHT}")
        print(f"ROS2 Topic: {ROS2_TOPIC}")

        keys = og.Controller.Keys
        legacy_tf = _use_legacy_tf_publisher(og)
        tf_nodes = [] if legacy_tf else [("TFCompute", "isaacsim.core.nodes.IsaacComputeTransformTree")]
        tf_target = "TFPublish" if legacy_tf else "TFCompute"
        tf_connections = [("OnPlaybackTick.outputs:tick", "TFPublish.inputs:execIn")] if legacy_tf else [
            ("OnPlaybackTick.outputs:tick", "TFCompute.inputs:execIn"),
            ("TFCompute.outputs:execOut", "TFPublish.inputs:execIn"),
            ("TFCompute.outputs:parentFrames", "TFPublish.inputs:parentFrames"),
            ("TFCompute.outputs:childFrames", "TFPublish.inputs:childFrames"),
            ("TFCompute.outputs:translations", "TFPublish.inputs:translations"),
            ("TFCompute.outputs:orientations", "TFPublish.inputs:orientations"),
        ]

        (graph, nodes, _, _) = og.Controller.edit(
            {"graph_path": graph_path, "evaluator_name": "execution"},
            {
                keys.CREATE_NODES: [
                    ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                    ("RunOnce", "isaacsim.core.nodes.OgnIsaacRunOneSimulationFrame"),
                    ("RenderProduct", "isaacsim.core.nodes.IsaacCreateRenderProduct"),
                    ("Context", "isaacsim.ros2.bridge.ROS2Context"),
                    ("ReadSimTime", "isaacsim.core.nodes.IsaacReadSimulationTime"),
                    ("CameraInfoPublish", "isaacsim.ros2.bridge.ROS2CameraInfoHelper"),
                    ("RGBPublish", "isaacsim.ros2.bridge.ROS2CameraHelper"),
                    ("TFPublish", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
                ] + tf_nodes,
                keys.SET_VALUES: [
                    ("RenderProduct.inputs:cameraPrim", CAMERA_PRIM),
                    ("RenderProduct.inputs:width", IMAGE_WIDTH),
                    ("RenderProduct.inputs:height", IMAGE_HEIGHT),
                    ("CameraInfoPublish.inputs:topicName", "camera_info"),
                    ("CameraInfoPublish.inputs:frameId", "camera_link"),
                    ("CameraInfoPublish.inputs:resetSimulationTimeOnStop", False),
                    ("RGBPublish.inputs:topicName", ROS2_TOPIC),
                    ("RGBPublish.inputs:type", "rgb"),
                    ("RGBPublish.inputs:frameId", "camera_link"),
                    ("RGBPublish.inputs:resetSimulationTimeOnStop", False),
                    ("TFPublish.inputs:topicName", "tf_cam"),
                    (f"{tf_target}.inputs:targetPrims", CAMERA_PRIM),
                    (f"{tf_target}.inputs:parentPrim", "/World"),
                    ("ReadSimTime.inputs:resetOnStop", False),
                ],
                keys.CONNECT: [
                    ("OnPlaybackTick.outputs:tick", "RunOnce.inputs:execIn"),
                    ("RunOnce.outputs:step", "RenderProduct.inputs:execIn"),
                    ("RenderProduct.outputs:execOut", "CameraInfoPublish.inputs:execIn"),
                    ("RenderProduct.outputs:execOut", "RGBPublish.inputs:execIn"),
                    ("RenderProduct.outputs:renderProductPath", "CameraInfoPublish.inputs:renderProductPath"),
                    ("RenderProduct.outputs:renderProductPath", "RGBPublish.inputs:renderProductPath"),
                    ("Context.outputs:context", "CameraInfoPublish.inputs:context"),
                    ("Context.outputs:context", "RGBPublish.inputs:context"),
                    ("Context.outputs:context", "TFPublish.inputs:context"),
                    ("ReadSimTime.outputs:simulationTime", "TFPublish.inputs:timeStamp"),
                ] + tf_connections,
            }
        )

        # Override the USD camera's baked-in intrinsics to match the real Intel RealSense
        camera_prim = stage.GetPrimAtPath(CAMERA_PRIM)
        if camera_prim and camera_prim.IsValid():
            from pxr import Sdf
            import numpy as np

            # Use calibrated camera_matrix [fx, fy, cx, cy] from yaml if available,
            # otherwise fall back to HFOV/VFOV computation
            camera_matrix = cam_cfg.get("camera_matrix")
            if camera_matrix and len(camera_matrix) == 4:
                fx, fy, cx, cy = camera_matrix
                print(f"Using calibrated camera_matrix from config: fx={fx}, fy={fy}, cx={cx}, cy={cy}")
            else:
                hfov_deg = cam_cfg.get("camera_hfov", 69.4)
                vfov_deg = cam_cfg.get("camera_vfov", 42.5)
                fx = IMAGE_WIDTH / (2 * np.tan(np.deg2rad(hfov_deg / 2)))
                fy = IMAGE_HEIGHT / (2 * np.tan(np.deg2rad(vfov_deg / 2)))
                cx = IMAGE_WIDTH * 0.5
                cy = IMAGE_HEIGHT * 0.5
                print(f"Using HFOV/VFOV from config: hfov={hfov_deg}, vfov={vfov_deg}")

            # Distortion coefficients from yaml
            dist_coeffs = cam_cfg.get("distortion_coeffs", [0, 0, 0, 0, 0])

            # Set USD aperture/focal-length so the renderer matches the FOV
            horizontal_aperture_mm = 36.0
            focal_length_mm = fx * horizontal_aperture_mm / IMAGE_WIDTH
            vertical_aperture_mm = IMAGE_HEIGHT * focal_length_mm / fy

            camera = UsdGeom.Camera(camera_prim)
            camera.CreateHorizontalApertureAttr().Set(horizontal_aperture_mm)
            camera.CreateVerticalApertureAttr().Set(vertical_aperture_mm)
            camera.CreateFocalLengthAttr().Set(focal_length_mm)
            camera.CreateClippingRangeAttr().Set(Gf.Vec2f(0.1, 10000.0))

            # Set opencvPinhole lens distortion model for correct CameraInfo publishing
            camera_prim.CreateAttribute("omni:lensdistortion:model", Sdf.ValueTypeNames.String).Set("opencvPinhole")
            camera_prim.CreateAttribute("omni:lensdistortion:opencvPinhole:imageSize", Sdf.ValueTypeNames.Int2).Set(Gf.Vec2i(IMAGE_WIDTH, IMAGE_HEIGHT))
            camera_prim.CreateAttribute("omni:lensdistortion:opencvPinhole:fx", Sdf.ValueTypeNames.Float).Set(fx)
            camera_prim.CreateAttribute("omni:lensdistortion:opencvPinhole:fy", Sdf.ValueTypeNames.Float).Set(fy)
            camera_prim.CreateAttribute("omni:lensdistortion:opencvPinhole:cx", Sdf.ValueTypeNames.Float).Set(cx)
            camera_prim.CreateAttribute("omni:lensdistortion:opencvPinhole:cy", Sdf.ValueTypeNames.Float).Set(cy)
            # Set distortion coefficients (k1, k2, p1, p2, k3, ..., s4)
            dist_attr_names = ["k1", "k2", "p1", "p2", "k3", "k4", "k5", "k6", "s1", "s2", "s3", "s4"]
            for i, attr_name in enumerate(dist_attr_names):
                val = dist_coeffs[i] if i < len(dist_coeffs) else 0.0
                camera_prim.CreateAttribute(f"omni:lensdistortion:opencvPinhole:{attr_name}", Sdf.ValueTypeNames.Float).Set(val)

            print(f"Overrode camera intrinsics on {CAMERA_PRIM}: fx={fx:.1f}, fy={fy:.1f}, cx={cx:.1f}, cy={cy:.1f}")
            print(f"Distortion coefficients: {dist_coeffs}")

        print("\nCamera ActionGraph created successfully!")
        print(f"Test with: ros2 topic echo /{ROS2_TOPIC}")

    def create_additional_camera(self):
        """Create additional camera based on selected view type"""
        import omni.usd
        from pxr import Gf, Sdf, UsdGeom
        import numpy as np

        def set_camera_pose(prim_path: str, position_xyz, quat_xyzw):
            """Apply translation and quaternion orientation (x, y, z, w) to a camera prim."""
            stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(prim_path)
            if not prim.IsValid():
                raise RuntimeError(f"Camera prim '{prim_path}' not found.")
            xform = UsdGeom.Xform(prim)
            xform.ClearXformOpOrder()
            xform.AddTranslateOp().Set(Gf.Vec3d(*position_xyz))
            quat = Gf.Quatd(quat_xyzw[3], quat_xyzw[0], quat_xyzw[1], quat_xyzw[2])
            xform.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(quat)

        def configure_camera_properties(camera_prim, width, height):
            """Configure camera properties to match Intel RealSense camera intrinsics."""
            # Intel RealSense specs: HFOV=69.4°, VFOV=42.5° at 1280x720
            hfov_deg = 69.4
            vfov_deg = 42.5

            # Compute intrinsics: fx = width / (2 * tan(hfov/2))
            fx = width / (2 * np.tan(np.deg2rad(hfov_deg / 2)))
            fy = height / (2 * np.tan(np.deg2rad(vfov_deg / 2)))
            cx = width * 0.5
            cy = height * 0.5

            # Derive USD aperture/focal-length so the renderer matches the FOV
            # Keep horizontal_aperture fixed, compute focal_length from fx
            horizontal_aperture_mm = 36.0
            focal_length_mm = fx * horizontal_aperture_mm / width
            vertical_aperture_mm = height * focal_length_mm / fy

            camera = UsdGeom.Camera(camera_prim)
            camera.CreateHorizontalApertureAttr().Set(horizontal_aperture_mm)
            camera.CreateVerticalApertureAttr().Set(vertical_aperture_mm)
            camera.CreateFocalLengthAttr().Set(focal_length_mm)
            camera.CreateProjectionAttr().Set("perspective")
            camera.CreateClippingRangeAttr().Set(Gf.Vec2f(0.1, 10000.0))

            # Set opencvPinhole lens distortion model so ROS2 CameraInfo
            # publishes fx/fy/cx/cy directly
            camera_prim.CreateAttribute("omni:lensdistortion:model", Sdf.ValueTypeNames.String).Set("opencvPinhole")
            camera_prim.CreateAttribute("omni:lensdistortion:opencvPinhole:imageSize", Sdf.ValueTypeNames.Int2).Set(Gf.Vec2i(width, height))
            camera_prim.CreateAttribute("omni:lensdistortion:opencvPinhole:fx", Sdf.ValueTypeNames.Float).Set(fx)
            camera_prim.CreateAttribute("omni:lensdistortion:opencvPinhole:fy", Sdf.ValueTypeNames.Float).Set(fy)
            camera_prim.CreateAttribute("omni:lensdistortion:opencvPinhole:cx", Sdf.ValueTypeNames.Float).Set(cx)
            camera_prim.CreateAttribute("omni:lensdistortion:opencvPinhole:cy", Sdf.ValueTypeNames.Float).Set(cy)
            # Zero distortion coefficients (k1-k6, p1, p2, s1-s4)
            for attr_name in ["k1", "k2", "p1", "p2", "k3", "k4", "k5", "k6", "s1", "s2", "s3", "s4"]:
                camera_prim.CreateAttribute(f"omni:lensdistortion:opencvPinhole:{attr_name}", Sdf.ValueTypeNames.Float).Set(0.0)

        # Check which camera type is selected
        is_workspace = self._workspace_checkbox.model.get_value_as_bool()
        is_custom = self._custom_checkbox.model.get_value_as_bool()

        # Get selected resolution from combo box
        resolution_index = self._resolution_combo.model.get_item_value_model().get_value_as_int()
        resolutions = [(640, 480), (1280, 720), (1920, 1080)]
        resolution = resolutions[resolution_index]

        stage = omni.usd.get_context().get_stage()
        if not stage:
            print("Error: No stage found")
            return

        if is_workspace:
            prim_path = "/World/workspace_camera_sim"
            position = (0.8572405778988392, -1.3321141046870788, 0.9906567613694909)
            # Euler XYZ: (52.144, 39.13, 26.13) degrees
            quat_xyzw = (0.4714, 0.1994, 0.3347, 0.7912)  # x, y, z, w

            # Create camera prim using UsdGeom.Camera
            camera_prim = UsdGeom.Camera.Define(stage, prim_path)
            if not camera_prim:
                print(f"Error: Failed to create camera at {prim_path}")
                return

            # Configure camera properties
            configure_camera_properties(camera_prim.GetPrim(), resolution[0], resolution[1])

            # Set camera pose
            set_camera_pose(prim_path, position, quat_xyzw)
            print(f"Workspace camera created at {prim_path} with resolution {resolution[0]}x{resolution[1]}")

        if is_custom:
            # Get custom prim path from text field
            custom_prim_path = self._custom_camera_prim_field.model.get_value_as_string()
            if not custom_prim_path or custom_prim_path.strip() == "":
                print("Error: Please enter a valid camera prim path")
                return
            
            # Check if the prim already exists
            existing_prim = stage.GetPrimAtPath(custom_prim_path)
            if existing_prim and existing_prim.IsValid():
                print(f"Camera prim already exists at {custom_prim_path}. Using existing prim.")
                camera_prim = UsdGeom.Camera(existing_prim)
                if not camera_prim:
                    # If it exists but is not a camera, convert it
                    print(f"Prim at {custom_prim_path} exists but is not a camera. Creating camera...")
                    camera_prim = UsdGeom.Camera.Define(stage, custom_prim_path)
            else:
                # Create new camera prim at the specified path
                camera_prim = UsdGeom.Camera.Define(stage, custom_prim_path)
                if not camera_prim:
                    print(f"Error: Failed to create camera at {custom_prim_path}")
                    return
            
            # Configure camera properties
            configure_camera_properties(camera_prim.GetPrim(), resolution[0], resolution[1])
            
            # Note: Pose is not set for custom prim - user should position it manually or it uses existing pose
            print(f"Custom camera created/updated at {custom_prim_path} with resolution {resolution[0]}x{resolution[1]}")

    def create_additional_camera_actiongraph(self):
        """Create ActionGraph for additional camera ROS2 publishing"""
        # Check which cameras exist and create action graphs accordingly
        is_workspace = self._workspace_checkbox.model.get_value_as_bool()
        is_custom = self._custom_checkbox.model.get_value_as_bool()

        # Get selected resolution from combo box
        resolution_index = self._resolution_combo.model.get_item_value_model().get_value_as_int()
        resolutions = [(640, 480), (1280, 720), (1920, 1080)]
        width, height = resolutions[resolution_index]

        stage = omni.usd.get_context().get_stage()

        if is_workspace:
            if not stage.GetPrimAtPath("/World/workspace_camera_sim"):
                print("Error: Workspace camera not found at /World/workspace_camera_sim. Create it first.")
            else:
                self._create_camera_actiongraph(
                    "/World/workspace_camera_sim",
                    width, height,
                    "workspace_camera_sim",
                    "WorkspaceCameraSim"
                )

        if is_custom:
            # Get custom prim path from text field
            custom_prim_path = self._custom_camera_prim_field.model.get_value_as_string()
            if not custom_prim_path or custom_prim_path.strip() == "":
                print("Error: Please enter a valid camera prim path")
                return

            # Get ROS2 topic name from text field, or generate from prim path if empty
            topic_name = self._custom_camera_topic_field.model.get_value_as_string()
            if not topic_name or topic_name.strip() == "":
                # Extract a clean name for the topic from the prim path if topic is empty
                prim_name = custom_prim_path.split("/")[-1] if "/" in custom_prim_path else custom_prim_path
                topic_name = prim_name.lower().replace(" ", "_").replace("-", "_")
                print(f"Topic name not specified, using generated name: {topic_name}")

            # Use topic name for graph suffix (sanitize for graph name - remove special chars, capitalize)
            # Convert topic name to a valid graph suffix: remove underscores, capitalize words
            graph_suffix = topic_name.replace("_", " ").replace("-", " ").title().replace(" ", "")

            if not stage.GetPrimAtPath(custom_prim_path):
                print(f"Error: Camera prim not found at {custom_prim_path}. Create it first.")
            else:
                self._create_camera_actiongraph(
                    custom_prim_path,
                    width, height,
                    topic_name,
                    graph_suffix
                )

    def _setup_camera_publisher(self, camera_prim, width, height, topic, graph_suffix):
        """Route camera publishing to viewport publisher or action graph based on toggle."""
        use_viewport = self._viewport_pub_checkbox.model.get_value_as_bool()
        if use_viewport:
            # Viewport publisher starts after play — just skip action graph creation
            print(f"Using viewport publisher for /{topic} (action graph skipped)")
        else:
            self._create_camera_actiongraph(camera_prim, width, height, topic, graph_suffix)

    def _create_camera_actiongraph(self, camera_prim, width, height, topic, graph_suffix):
        """Helper method to create camera ActionGraph using og.Controller.edit().

        Follows the same pattern as isaacsim.ros2.bridge's built-in camera graph
        shortcut (og_rtx_sensors.py). Creates all nodes, sets values, and wires
        connections in a single batch call. If the graph already exists it is
        deleted and recreated with the current settings.
        """
        graph_path = f"/Graph/ActionGraph_{graph_suffix}"

        # Delete existing graph so it gets recreated with current settings
        stage = omni.usd.get_context().get_stage()
        if stage.GetPrimAtPath(graph_path):
            stage.RemovePrim(graph_path)
            print(f"Removed existing ActionGraph at {graph_path}")

        print(f"Creating ActionGraph: {graph_path}")
        print(f"Camera: {camera_prim}, Resolution: {width}x{height}, Topic: /{topic}")

        keys = og.Controller.Keys

        try:
            (graph_handle, nodes, _, _) = og.Controller.edit(
                {"graph_path": graph_path, "evaluator_name": "execution"},
                {
                    keys.CREATE_NODES: [
                        ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                        ("RunOnce", "isaacsim.core.nodes.OgnIsaacRunOneSimulationFrame"),
                        ("RenderProduct", "isaacsim.core.nodes.IsaacCreateRenderProduct"),
                        ("Context", "isaacsim.ros2.bridge.ROS2Context"),
                        ("CameraInfoPublish", "isaacsim.ros2.bridge.ROS2CameraInfoHelper"),
                        ("RGBPublish", "isaacsim.ros2.bridge.ROS2CameraHelper"),
                    ],
                    keys.SET_VALUES: [
                        ("RenderProduct.inputs:cameraPrim", camera_prim),
                        ("RenderProduct.inputs:width", width),
                        ("RenderProduct.inputs:height", height),
                        ("CameraInfoPublish.inputs:topicName", "camera_info"),
                        ("CameraInfoPublish.inputs:frameId", "camera_link"),
                        ("CameraInfoPublish.inputs:resetSimulationTimeOnStop", False),
                        ("RGBPublish.inputs:topicName", topic),
                        ("RGBPublish.inputs:type", "rgb"),
                        ("RGBPublish.inputs:frameId", "camera_link"),
                        ("RGBPublish.inputs:resetSimulationTimeOnStop", False),
                    ],
                    keys.CONNECT: [
                        ("OnPlaybackTick.outputs:tick", "RunOnce.inputs:execIn"),
                        ("RunOnce.outputs:step", "RenderProduct.inputs:execIn"),
                        ("RenderProduct.outputs:execOut", "CameraInfoPublish.inputs:execIn"),
                        ("RenderProduct.outputs:renderProductPath", "CameraInfoPublish.inputs:renderProductPath"),
                        ("Context.outputs:context", "CameraInfoPublish.inputs:context"),
                        ("RenderProduct.outputs:execOut", "RGBPublish.inputs:execIn"),
                        ("RenderProduct.outputs:renderProductPath", "RGBPublish.inputs:renderProductPath"),
                        ("Context.outputs:context", "RGBPublish.inputs:context"),
                    ],
                },
            )
            print(f"{graph_suffix} ActionGraph created successfully!")
            print(f"Test with: ros2 topic echo /{topic}")
        except Exception as e:
            print(f"Error creating ActionGraph: {e}")
            traceback.print_exc()

"""Building the scene: stage, workstation, room and the quick-start sequences."""

import omni.usd
import asyncio
import carb
from typing import Dict, Any
from isaacsim.core.api.world import World
from .asset_cache import AssetMissing, asset_source, cache_dir, missing_assets
from .joint_state_guard import check_stage as check_joint_state_loop
from .world_compat import initialized_twin_world, paused_scene_edit
from .physics_compat import (
    selected_engine, require_newton_mount_support, configure_newton_rate, prepare_newton_tensors,
    prepare_newton_wrench,
)
from isaacsim.core.utils.stage import add_reference_to_stage
from pxr import PhysxSchema
from .settings import (
    GRIPPER_ASSETS, SIMPLE_ROOM_ASSET_REL, SIMPLE_ROOM_PRIM_PATH, SIMPLE_ROOM_Z, WORKSTATION_PRIM_PATH,
    WORKSTATION_SCALE, WORKSTATION_USD, WORKSTATION_XY, _local_asset,
)
from .capture_state import _viewport_pub


class Scene:
    """Part of DigitalTwin (extension.py), which mixes it in."""

    async def _wait_for_stage(self, timeout_s: float = 60.0):
        """The bridge port opens a few seconds before Kit has a USD stage; tools that touch the stage must wait."""
        import time as _time

        app = omni.kit.app.get_app()
        deadline = _time.time() + timeout_s
        while omni.usd.get_context().get_stage() is None and _time.time() < deadline:
            await app.next_update_async()
        if omni.usd.get_context().get_stage() is None:
            raise RuntimeError("no USD stage available after %.0f s; is the Isaac Sim app fully started?" % timeout_s)

    async def load_scene(self):
        await self._wait_for_stage()
        stage = omni.usd.get_context().get_stage()

        # World() otherwise takes the standalone synchronous path under
        # SimulationApp and pumps app.update inside this async MCP task.
        if World.instance() is None:
            prepare_newton_tensors()
            prepare_newton_wrench()
        world = await initialized_twin_world(World)
        print("Simulation context initialized")

        # Check for ground plane on stage and add if missing
        ground_prim = stage.GetPrimAtPath("/World/defaultGroundPlane")
        if ground_prim and ground_prim.IsValid():
            print("Ground plane already exists, skipping")
        else:
            from isaacsim.core.api.objects import GroundPlane
            GroundPlane(prim_path="/World/defaultGroundPlane", z_position=0.0, size=5000)
            print("Added ground plane")

        # Ensure Isaac Sim's standard default lighting exists deterministically.
        # This matches what Isaac Sim creates on a fresh app startup so the scene looks
        # identical regardless of caller (UI button, MCP, or socket after new_stage).
        from pxr import UsdLux, UsdGeom, Gf
        default_light_path = "/Environment/defaultLight"
        existing = stage.GetPrimAtPath(default_light_path)
        if existing and existing.IsValid():
            print("Default distant light already exists, skipping")
        else:
            light = UsdLux.DistantLight.Define(stage, default_light_path)
            light.CreateIntensityAttr().Set(3000.0)
            light.CreateAngleAttr().Set(1.0)
            light.CreateColorAttr().Set(Gf.Vec3f(1.0, 1.0, 1.0))
            xf = UsdGeom.Xformable(light.GetPrim())
            xf.ClearXformOpOrder()
            xf.AddTranslateOp().Set(Gf.Vec3d(0, 0, 0))
            # Isaac Sim's default orientation (sun-angle distant light)
            xf.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(
                Gf.Quatd(0.6532814824381883, 0.2705980500730985, 0.27059805007309845, 0.6532814824381882)
            )
            print("Added default distant light (Isaac Sim canonical default)")

        # Set minimum frame rate to 60
        settings = carb.settings.get_settings()
        mfr = getattr(self, '_min_frame_rate', 60)
        settings.set("/persistent/simulation/minFrameRate", mfr)
        print(f"Set persistent/simulation/minFrameRate to {mfr}")

        # Configure physics scene
        physics_scene_prim = stage.GetPrimAtPath("/physicsScene")
        if physics_scene_prim and physics_scene_prim.IsValid():
            physx_scene_api = PhysxSchema.PhysxSceneAPI.Apply(physics_scene_prim)
            physx_scene_api.CreateEnableGPUDynamicsAttr().Set(False)
            physx_scene_api.CreateTimeStepsPerSecondAttr().Set(getattr(self, '_time_steps_per_second', 120))
            physx_scene_api.CreateEnableCCDAttr().Set(False)
            engine = selected_engine()
            configure_newton_rate(physics_scene_prim, getattr(self, '_time_steps_per_second', 120), engine)
            print(f"Twin scene uses {engine} on CPU at {getattr(self, '_time_steps_per_second', 120)} Hz; PhysX GPU dynamics/CCD flags disabled")
        else:
            print("Warning: /physicsScene not found")

        print("Scene loaded successfully.")

    def _build_placeholder_workstation(self, stage, parent_path):
        """Build a simple bench when the licensed Vention export is not installed.

        The real bench is visual context only. This placeholder keeps the same
        top-at-z=0 contract and ``/World/Vention`` path so the rest of the scene
        and the ROS pose parity are unchanged.
        """
        from pxr import Gf, UsdGeom

        bench_path = parent_path + "/Bench"
        UsdGeom.Xform.Define(stage, bench_path)

        def box(name, size, center):
            cube = UsdGeom.Cube.Define(stage, f"{bench_path}/{name}")
            cube.GetSizeAttr().Set(1.0)
            xform = UsdGeom.Xformable(cube)
            xform.ClearXformOpOrder()
            xform.AddTranslateOp().Set(Gf.Vec3d(*center))
            xform.AddScaleOp().Set(Gf.Vec3f(*size))

        box("Top", (1.4, 0.9, 0.04), (0.0, 0.0, -0.02))
        box("Shelf", (1.3, 0.8, 0.02), (0.0, 0.0, -0.55))
        for index, (x, y) in enumerate(
            ((-0.62, -0.38), (0.62, -0.38), (-0.62, 0.38), (0.62, 0.38))
        ):
            box(f"Leg{index}", (0.06, 0.06, 0.86), (x, y, -0.47))

        parent = stage.GetPrimAtPath(parent_path)
        xform = UsdGeom.Xform(parent)
        xform.ClearXformOpOrder()
        xform.AddTranslateOp().Set(
            Gf.Vec3d(WORKSTATION_XY[0], WORKSTATION_XY[1], 0.0)
        )

        ground = stage.GetPrimAtPath("/World/defaultGroundPlane")
        if ground and ground.IsValid():
            UsdGeom.Imageable(ground).MakeInvisible()
            print("Default ground plane visual hidden (z=0 collision retained).")
        print(f"Placeholder workstation built at {parent_path} (top at z=0).")

    async def load_workstation(self):
        # Large-reference composition must complete outside active physics;
        # otherwise older Motion BVH renderers can fault on the following Stop.
        async with paused_scene_edit(
            self._timeline, omni.kit.app.get_app(), omni.usd.get_context().get_stage
        ):
            await self._load_workstation()

    async def _load_workstation(self):
        """Load the Vention workstation bench under the robot (additive, visual-only).

        References the operator-supplied or cached vention.usd at /World/Vention
        (or builds a placeholder bench) and places it so the
        tabletop TOP surface is exactly at z=0 — coincident with the work plane where
        the robot base (ROBOT_BASE_Z=0.0) and objects already sit. The robot is NOT
        moved and ROBOT_BASE_Z is NOT changed, so ROS pose parity is untouched. The
        bench has no colliders; the default ground plane's z=0 collision is retained
        and only its visual is hidden. The wrapper pauses and restores playback.
        """
        import time
        import omni.kit.app
        from pxr import Usd, UsdGeom, Gf

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            print("No stage; cannot load workstation.")
            return

        parent_path = WORKSTATION_PRIM_PATH          # /World/Vention — a fresh Xform we own
        bench_path = parent_path + "/Bench"          # the referenced asset lives under here
        existing = stage.GetPrimAtPath(parent_path)
        if existing and existing.IsValid():
            print(f"Workstation already present at {parent_path}, skipping.")
            return

        # Own a brand-new parent Xform so there are NO inherited xformOps to collide with.
        # (Referencing the cm-authored asset directly onto the prim imports its quatf
        #  xformOp:orient, which then conflicts with AddOrientOp(PrecisionDouble). The
        #  wrapper sidesteps that gotcha entirely — all our transform lives on the parent.)
        UsdGeom.Xform.Define(stage, parent_path)

        try:
            asset_path = _local_asset(WORKSTATION_USD)
        except AssetMissing:
            self._build_placeholder_workstation(stage, parent_path)
            print(
                "Vention workstation asset not installed; using a placeholder bench. "
                f"Add your licensed copy with `python3 scripts/isaac_assets.py add <file-or-archive>` "
                f"(it lands at {cache_dir() / 'ur5e-dt' / 'workstation'}) to use the real model."
            )
            return

        add_reference_to_stage(usd_path=asset_path, prim_path=bench_path)

        # Wait for the referenced bench prim to compose.
        bench = None
        for _ in range(50):
            bench = stage.GetPrimAtPath(bench_path)
            if bench and bench.IsValid():
                break
            time.sleep(0.1)
        if not (bench and bench.IsValid()):
            raise RuntimeError(f"Failed to load workstation prim at {bench_path}")

        # Transform lives on the fresh parent: XY only (robot at origin in source scene);
        # Z is solved last so the bench top lands exactly at z=0.
        tx, ty = WORKSTATION_XY
        parent = stage.GetPrimAtPath(parent_path)
        xform = UsdGeom.Xform(parent)
        xform.ClearXformOpOrder()
        t_op = xform.AddTranslateOp()                # fresh prim → default (double), no clash
        t_op.Set(Gf.Vec3d(tx, ty, 0.0))

        # Let composition settle. Isaac's MetricAssembler auto-resolves the asset's cm units
        # (metersPerUnit 0.01) into the meters stage on this tick, writing its own
        # xformOp:scale:unitsResolve on the referenced child. We must NOT also scale, or it
        # double-applies (verified: 88m raw, 0.0089m when both fire). So scale only if the
        # measured bench height shows units were NOT resolved (defensive if MetricAssembler off).
        await omni.kit.app.get_app().next_update_async()

        def _world_range():
            c = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                                  [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
            return c.ComputeWorldBound(parent).ComputeAlignedRange()

        rng = _world_range()
        height = float(rng.GetMax()[2] - rng.GetMin()[2])
        applied_scale = 1.0
        if height > 5.0:                             # real bench ~0.885m; >5m ⇒ raw cm, unresolved
            s = WORKSTATION_SCALE
            xform.AddScaleOp().Set(Gf.Vec3f(s, s, s))
            applied_scale = s
            rng = _world_range()
            height = float(rng.GetMax()[2] - rng.GetMin()[2])

        top_z = float(rng.GetMax()[2])
        t_op.Set(Gf.Vec3d(tx, ty, -top_z))
        print(f"Vention placed at {parent_path}: top aligned to z=0 "
              f"(height {height:.4f} m, raw top {top_z:.4f} m, our_scale={applied_scale}), "
              f"xy=({tx}, {ty})")

        # Hide ONLY the visual of the default ground plane; keep its z=0 collider so
        # object/robot physics stay byte-identical to before.
        ground = stage.GetPrimAtPath("/World/defaultGroundPlane")
        if ground and ground.IsValid():
            UsdGeom.Imageable(ground).MakeInvisible()
            print("Default ground plane visual hidden (z=0 collision retained).")

        print("Workstation loaded.")

    async def quick_start_with_workstation(self):
        """quick_start() then load_workstation() — full scene plus the Vention bench."""
        await self.quick_start()
        await self.load_workstation()

    async def load_simple_room(self):
        """Load Isaac's standard Simple Room environment as a backdrop (additive).

        References the standard Isaac Simple_Room asset (via the Isaac assets root, so it
        resolves whether assets are local or cloud) onto a fresh /World/SimpleRoom wrapper
        and places it at z=-0.11 — matching the source scene's room under our
        robot-base-at-0 convention, so the bench feet land on the room floor. Hides the
        default ground plane's visual (the room provides the floor) while keeping its z=0
        collider so object/robot physics are unchanged. Does not move the robot.

        Performance: the Simple_Room asset ships a collider on EVERY mesh (59 of them,
        many full triangle-mesh) — pure backdrop the robot never touches. Left enabled
        they crater the physics step rate (measured: render-off RTF 0.91 -> 0.13, ~7x).
        So we strip all room collision via _make_visual_only(), then stop+play to rebuild
        PhysX without them (render-off RTF back to ~0.95). We keep our own z=0 ground
        collider for objects to rest on.
        """
        import time
        import omni.timeline          # NB: import omni.* at top — a later in-function
        import omni.kit.app           # `import omni.x` makes `omni` local for the WHOLE
        from pxr import UsdGeom, Gf    # function, breaking earlier omni.usd.* (UnboundLocalError)

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            print("No stage; cannot load simple room.")
            return

        parent_path = SIMPLE_ROOM_PRIM_PATH          # fresh Xform we own
        room_path = parent_path + "/Room"            # asset reference lives here
        existing = stage.GetPrimAtPath(parent_path)
        if existing and existing.IsValid():
            print(f"Simple Room already present at {parent_path}, skipping.")
            return

        from isaacsim.storage.native import get_assets_root_path
        root = get_assets_root_path()
        if not root:
            raise RuntimeError("Could not resolve the Isaac assets root for Simple Room.")
        asset_path = root + SIMPLE_ROOM_ASSET_REL

        # Fresh parent Xform → no inherited xformOps to clash with (same gotcha-avoidance
        # as load_workstation). Reference the room asset onto a child.
        UsdGeom.Xform.Define(stage, parent_path)
        add_reference_to_stage(usd_path=asset_path, prim_path=room_path)

        room = None
        for _ in range(100):                         # room streams more sub-assets; allow time
            room = stage.GetPrimAtPath(room_path)
            if room and room.IsValid():
                break
            time.sleep(0.1)
        if not (room and room.IsValid()):
            raise RuntimeError(f"Failed to load simple room prim at {room_path}")

        # Place the room (meters asset → no scale, identity rotation).
        parent = stage.GetPrimAtPath(parent_path)
        xform = UsdGeom.Xform(parent)
        xform.ClearXformOpOrder()
        xform.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, SIMPLE_ROOM_Z))
        print(f"Simple Room placed at {parent_path}: translate z={SIMPLE_ROOM_Z}")

        # Remove the Simple Room's built-in table prop — we use our own Vention bench.
        # Deactivate (not just hide) so its geometry AND any collider leave composition.
        # Match by name prefix so it survives the asset's numeric suffix (e.g. table_low_327).
        for child in room.GetChildren():
            if child.GetName().lower().startswith("table_low"):
                child.SetActive(False)
                print(f"Deactivated Simple Room built-in table: {child.GetPath()}")

        # Strip ALL of the room's colliders — it's pure visual backdrop and its 59
        # (mostly triangle-mesh) colliders otherwise crater the physics step rate.
        stripped = self._make_visual_only(room_path)
        print(f"Room made visual-only: {stripped}")

        # Hide ONLY the default ground plane visual; keep its z=0 collider.
        ground = stage.GetPrimAtPath("/World/defaultGroundPlane")
        if ground and ground.IsValid():
            UsdGeom.Imageable(ground).MakeInvisible()
            print("Default ground plane visual hidden (z=0 collision retained).")

        # The room was added to a live (playing) PhysX scene, so disabling its colliders
        # via USD doesn't drop them until PhysX rebuilds. If playing, stop+play so they
        # actually leave the simulation (measured: render-off RTF 0.38 -> 0.95). Use
        # next_update_async (NOT time.sleep) so the loop runs and stop/play take effect.
        tl = omni.timeline.get_timeline_interface()
        if tl.is_playing():
            tl.stop()
            for _ in range(3):
                await omni.kit.app.get_app().next_update_async()
            tl.play()
            for _ in range(3):
                await omni.kit.app.get_app().next_update_async()
            print("Rebuilt PhysX (stop+play) so disabled room colliders are fully dropped.")

        print("Simple Room loaded.")

    async def quick_start_with_workstation_and_simple_room(self):
        """quick_start() + load_workstation() + load_simple_room() — full scene, bench, room."""
        await self.quick_start()
        await self.load_workstation()
        await self.load_simple_room()

    def _make_visual_only(self, prim_path: str) -> Dict[str, Any]:
        """Strip physics colliders + rigid bodies from a subtree → pure visual backdrop.

        Decorative/environment assets routinely ship a collider on EVERY mesh (Isaac's
        Simple_Room has 59, many full triangle-mesh). For a backdrop the robot never
        touches, those colliders are dead weight that PhysX still processes every step —
        the single biggest scene-FPS/physics-rate killer we've measured (Simple_Room:
        render-off RTF 0.91 -> 0.13, ~7x slowdown; prim count was a red herring, the
        bench's +688 visual-only prims cost ~nothing). Diagnose this class of problem by
        watching RTF with rendering OFF: if it collapses when an asset loads, the cost is
        PhysX (colliders), not the GPU.

        Disables (does not delete) collisionEnabled / rigidBodyEnabled, so it is
        reversible. Call it in the load handler BEFORE returning — the handler blocks
        Kit's main loop, so PhysX never cooks the colliders in the first place (cooking
        an already-built collider then disabling it needs a stop+play to take effect).

        Args:
            prim_path: root of the subtree to neutralize (e.g. "/World/SimpleRoom").
        Returns:
            {"colliders_disabled": int, "rigidbodies_disabled": int}
        """
        from pxr import Usd, UsdPhysics
        stage = omni.usd.get_context().get_stage()
        root = stage.GetPrimAtPath(prim_path) if stage else None
        if not (root and root.IsValid()):
            return {"colliders_disabled": 0, "rigidbodies_disabled": 0}
        nc = nr = 0
        for p in Usd.PrimRange(root):
            if p.HasAPI(UsdPhysics.CollisionAPI):
                UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Set(False)
                nc += 1
            if p.HasAPI(UsdPhysics.RigidBodyAPI):
                UsdPhysics.RigidBodyAPI(p).GetRigidBodyEnabledAttr().Set(False)
                nr += 1
        return {"colliders_disabled": nc, "rigidbodies_disabled": nr}

    async def quick_start(self):
        """Quick start: scene, public UR5e + action graph, workspace camera; the RG2 when its files exist."""
        import numpy as np
        from pxr import Gf, Sdf, UsdGeom

        require_newton_mount_support(selected_engine())
        check_joint_state_loop()

        print("=== Quick Start ===")

        # Decide before building anything. Corrupt gripper files raise here rather than being skipped.
        missing_gripper = missing_assets(GRIPPER_ASSETS)
        # A local copy always wins; say which file each part really comes from.
        self._quick_start_assets = [asset_source(relpath) for relpath in ("robot/ur5e.usd", *GRIPPER_ASSETS)]
        for used in self._quick_start_assets:
            if used["source"] == "remote":
                print(f"Warning: no local copy of {used['asset']}; loading it from {used['location']}")
        self._quick_start_gripper = (
            {"loaded": False, "missing": missing_gripper,
             "note": "The RG2 is optional and has no public download. See docs/ASSETS.md."}
            if missing_gripper else {"loaded": True})

        # 1. Load scene
        print("--- Loading scene ---")
        await self.load_scene()

        # 2. Import UR5e
        print("--- Importing UR5e ---")
        await self.load_ur5e()

        # 3. Setup UR5e action graph
        print("--- Setting up UR5e Action Graph ---")
        await self.setup_action_graph()

        # 4. Setup UR5e force publisher
        print("--- Setting up UR5e Force Publisher ---")
        self.setup_force_publish_action_graph()

        app = omni.kit.app.get_app()
        await app.next_update_async()

        if missing_gripper:
            print(f"--- Skipping the RG2 gripper; not in the asset folder: {missing_gripper} ---")
        else:
            # 5. Import RG2 gripper
            print("--- Importing RG2 Gripper ---")
            self.import_rg2_gripper()
            await app.next_update_async()

            # 6. Attach gripper to UR5e
            print("--- Attaching Gripper to UR5e ---")
            self.attach_rg2_to_ur5e()
            await app.next_update_async()

            # 7. Setup gripper action graph
            print("--- Setting up Gripper Action Graph ---")
            self.setup_gripper_action_graph()
            await app.next_update_async()

        # 8. Workspace camera + viewport switch — ALWAYS run (no headless early-return).
        # Creating the camera and pointing the active viewport at it is what makes the webrtc stream show
        # the workspace view: get_active_viewport() returns a real (windowless under webrtc/--no-window)
        # ViewportAPI and viewport.camera_path is a main-thread USD op, safe with or without an X window.
        # The only off-main-thread op is the viewport-frame ROS *publisher* (capture_viewport_to_buffer),
        # which self-guards on /app/window/enabled inside _ViewportCameraPublisher.start() and no-ops under
        # webrtc/headless — so the publisher is skipped WITHOUT skipping the camera + viewport switch.
        # (The earlier blanket headless skip rested on a false "capture hangs/crashes" premise; the real
        # cause of those wedges was the UJITSO cooking fork-deadlock, fixed by disabling UJITSO in
        # on_startup — see CLAUDE.md 2026-06-21.)

        # 8a. Create workspace camera at 1280x720
        print("--- Creating Workspace Camera (1280x720) ---")
        stage = omni.usd.get_context().get_stage()
        ws_prim_path = "/World/workspace_camera_sim"

        # Skip if workspace camera already exists
        ws_existing = stage.GetPrimAtPath(ws_prim_path)
        if ws_existing and ws_existing.IsValid():
            print(f"Workspace camera already exists at {ws_prim_path}, skipping creation.")
            ws_width, ws_height = 1280, 720
            await app.next_update_async()

            # Ensure publisher is running
            if self._viewport_pub_checkbox.model.get_value_as_bool():
                if not _viewport_pub.is_active("workspace_camera_sim"):
                    try:
                        _viewport_pub.start(ws_prim_path, "workspace_camera_sim", frame_id="workspace_camera_sim")
                    except Exception as e:
                        print(f"[ur5e-dt] workspace camera ROS publisher not started: {e}")

            # Refresh + apply selected viewport camera
            self._refresh_viewport_camera_list()
            self._apply_active_viewport_camera()

            if not self._timeline.is_playing():  # a rebuilt scene plays below; a reused one must too
                self._timeline.play()
                await app.next_update_async()
            print("=== Quick Start Complete ===")
            return

        # Default centered pose
        ws_position = (0.6293466593898318, -1.2106887168521516, 0.8575915712414524)
        ws_quat_xyzw = (0.4842, 0.1458, 0.2488, 0.8261)  # xyzw → Quatd(w, x, y, z)
        # Chat log pose (half-split view — render + chat side-by-side):
        # ws_position = (0.8572405778988392, -1.3321141046870788, 0.9906567613694909)
        # ws_quat_xyzw = (0.4714, 0.1994, 0.3347, 0.7912)
        ws_width, ws_height = 1280, 720

        camera_prim = UsdGeom.Camera.Define(stage, ws_prim_path)
        if not camera_prim:
            print(f"Error: Failed to create workspace camera at {ws_prim_path}")
        else:
            # Configure camera intrinsics (Intel RealSense specs)
            hfov_deg, vfov_deg = 69.4, 42.5
            fx = ws_width / (2 * np.tan(np.deg2rad(hfov_deg / 2)))
            fy = ws_height / (2 * np.tan(np.deg2rad(vfov_deg / 2)))
            cx, cy = ws_width * 0.5, ws_height * 0.5
            horizontal_aperture_mm = 36.0
            focal_length_mm = fx * horizontal_aperture_mm / ws_width
            vertical_aperture_mm = ws_height * focal_length_mm / fy

            cam = UsdGeom.Camera(camera_prim.GetPrim())
            cam.CreateHorizontalApertureAttr().Set(horizontal_aperture_mm)
            cam.CreateVerticalApertureAttr().Set(vertical_aperture_mm)
            cam.CreateFocalLengthAttr().Set(focal_length_mm)
            cam.CreateProjectionAttr().Set("perspective")
            cam.CreateClippingRangeAttr().Set(Gf.Vec2f(0.1, 10000.0))

            cp = camera_prim.GetPrim()
            cp.CreateAttribute("omni:lensdistortion:model", Sdf.ValueTypeNames.String).Set("opencvPinhole")
            cp.CreateAttribute("omni:lensdistortion:opencvPinhole:imageSize", Sdf.ValueTypeNames.Int2).Set(Gf.Vec2i(ws_width, ws_height))
            cp.CreateAttribute("omni:lensdistortion:opencvPinhole:fx", Sdf.ValueTypeNames.Float).Set(fx)
            cp.CreateAttribute("omni:lensdistortion:opencvPinhole:fy", Sdf.ValueTypeNames.Float).Set(fy)
            cp.CreateAttribute("omni:lensdistortion:opencvPinhole:cx", Sdf.ValueTypeNames.Float).Set(cx)
            cp.CreateAttribute("omni:lensdistortion:opencvPinhole:cy", Sdf.ValueTypeNames.Float).Set(cy)
            for attr_name in ["k1", "k2", "p1", "p2", "k3", "k4", "k5", "k6", "s1", "s2", "s3", "s4"]:
                cp.CreateAttribute(f"omni:lensdistortion:opencvPinhole:{attr_name}", Sdf.ValueTypeNames.Float).Set(0.0)

            # Set camera pose
            xform = UsdGeom.Xform(camera_prim.GetPrim())
            xform.ClearXformOpOrder()
            xform.AddTranslateOp().Set(Gf.Vec3d(*ws_position))
            quat = Gf.Quatd(ws_quat_xyzw[3], ws_quat_xyzw[0], ws_quat_xyzw[1], ws_quat_xyzw[2])
            xform.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(quat)
            print(f"Workspace camera created at {ws_prim_path} with resolution {ws_width}x{ws_height}")
        await app.next_update_async()

        # 8.5 Disable gravity on the robot kinematic chain (position-mirrored twin). Authored BEFORE
        #     reset_async so PhysX picks it up when it initializes the articulation views. The twin mirrors
        #     /joint_states (which already reflect the real UR5e's gravity-compensated pose), so simulating
        #     gravity here double-counts it and the PD drives sag (~3 mm tool droop, eats the table-clearance
        #     margin). Objects keep gravity. Full rationale + measurements in _disable_robot_chain_gravity.
        self._disable_robot_chain_gravity()

        # 9. Reset world to initialize all physics views (articulations need this)
        world = World.instance()
        if world:
            await world.reset_async()
        await app.next_update_async()

        # 10. Play the scene, THEN wait out PhysX warmup BEFORE any further stage mutation.
        #     Step 11 below creates the workspace-camera action graph (a stage/OmniGraph mutation) and
        #     switches the viewport camera; if that runs while PhysX is still cooking, the kit main
        #     thread wedges/native-crashes ~1s into play (clean Kit log, NO Python traceback — this was
        #     the "parked quick_start crash"). prismatic drives the SAME isaac.mcp.bridge with no crash
        #     precisely because its play_scene waits here. Same warmup loop as _cmd_play_scene /
        #     _cmd_add_objects. See SKILL.md 5.1 warmup addendum.
        print("--- Playing scene ---")
        self._timeline.play()
        await app.next_update_async()
        try:
            from isaacsim.core.simulation_manager import SimulationManager
            for _ in range(900):  # up to ~15s at 60Hz
                await asyncio.sleep(1.0 / 60.0)
                if not SimulationManager._warmup_needed:
                    break
        except Exception:
            await asyncio.sleep(3.0)

        # 10b. Fixed person-view camera, selected as the active view
        self._ensure_person_view_camera()
        asyncio.ensure_future(self._select_person_view_when_loaded())

        # 11. Start workspace camera publisher (viewport or action graph based on toggle)
        print("--- Starting Workspace Camera Publisher ---")
        if self._viewport_pub_checkbox.model.get_value_as_bool():
            try:
                _viewport_pub.start(ws_prim_path, "workspace_camera_sim", frame_id="workspace_camera_sim")
            except Exception as e:
                # The camera topic is optional for the scene build; keep the scene and report the failure.
                print(f"[ur5e-dt] workspace camera ROS publisher not started: {e}")
        else:
            self._create_camera_actiongraph(
                ws_prim_path, ws_width, ws_height,
                "workspace_camera_sim", "WorkspaceCameraSim"
            )

        # Refresh + apply selected viewport camera (defaults to workspace_camera_sim)
        self._refresh_viewport_camera_list()
        self._apply_active_viewport_camera()

        print("=== Quick Start Complete ===")

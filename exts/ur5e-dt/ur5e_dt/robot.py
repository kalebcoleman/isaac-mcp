"""The UR5e and its RG2 gripper: loading, drives, action graphs and the force publisher."""

import omni.usd
import numpy as np
from isaacsim.core.api.world import World
from .graph_compat import ensure_fixed_graph
from .robot_visuals import prepare_robot_visuals
from .world_compat import initialized_twin_world
from .physics_compat import (
    selected_engine, require_newton_mount_support, configure_newton_mount, subscribe_physics_step,
    close_physics_step, configure_newton_gripper, runtime_link_matrix, prepare_newton_tensors,
    prepare_newton_wrench, wrench_capability, tool_wrench, physics_stamp,
)
from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.prims import Articulation as ArticulationView
from isaacsim.core.utils.numpy.rotations import euler_angles_to_quats
from isaacsim.core.prims import SingleArticulation as Articulation
import omni.graph.core as og
from pxr import UsdGeom, Gf, Sdf, UsdPhysics, UsdShade, PhysxSchema
from .settings import (
    RG2_ACT_DAMPING, RG2_ACT_JOINT, RG2_ACT_MAXFORCE, RG2_ACT_STIFFNESS, RG2_ART_ROOT_REL, RG2_FINGER_LINKS,
    RG2_JOINT_STATE_TOPIC, RG2_JOINT_TARGET_TOPIC, RG2_MAX_JOINT_VEL, RG2_MIMIC_DAMPING, RG2_MIMIC_JOINTS,
    RG2_MIMIC_NAT_FREQ, RG2_PAD_GAP_TOPIC, RG2_THETA_MAX, RG2_THETA_MIN, ROBOT_BASE_Z, _local_asset,
)


class Robot:
    """Part of DigitalTwin (extension.py), which mixes it in."""

    async def load_ur5e(self):
        asset_path = _local_asset("robot/ur5e.usd")
        prim_path = "/World/UR5e"

        # Skip if UR5e already loaded
        stage = omni.usd.get_context().get_stage()
        existing = stage.GetPrimAtPath(prim_path)
        if existing and existing.IsValid():
            print(f"UR5e already exists at {prim_path}, skipping import.")
            return

        # 1. Ensure World exists - create and initialize if needed (won't clear existing stage)
        world = World.instance()
        if world is None:
            print("World not initialized. Creating simulation context for existing stage...")
        if World.instance() is None:
            prepare_newton_tensors()
            prepare_newton_wrench()
        world = await initialized_twin_world(World)
        # No ground plane here: preserve the user's existing stage.
        print("Simulation context initialized.")

        # 2. Add the USD asset
        add_reference_to_stage(usd_path=asset_path, prim_path=prim_path)

        # 3. Wait for prim to exist
        import time
        stage = omni.usd.get_context().get_stage()
        for _ in range(10):
            prim = stage.GetPrimAtPath(prim_path)
            if prim.IsValid():
                break
            time.sleep(0.1)
        else:
            raise RuntimeError(f"Failed to load prim at {prim_path}")

        # Resolve the renderer's instance transform issue before physics starts.
        expanded_visuals = prepare_robot_visuals(stage, prim_path)
        if expanded_visuals:
            print(f"Expanded {len(expanded_visuals)} UR5e visual instances for runtime rendering.")

        # NVIDIA's published UR5e asset has no tool0 frame; the twin's ROS-facing
        # copy did. Recreate it so downstream tools and TF consumers see the same
        # flange contract whether the robot came from the cache or the asset root.
        tool0_path = f"{prim_path}/wrist_3_link/flange/tool0"
        if not stage.GetPrimAtPath(tool0_path).IsValid():
            tool0 = UsdGeom.Xform.Define(stage, tool0_path)
            tool0.ClearXformOpOrder()
            tool0.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.0))
            tool0.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(
                Gf.Quatd(0.5, 0.5, 0.5, 0.5)
            )
            print(f"Added tool0 frame at {tool0_path}")

        # 4. Apply translation and orientation
        xform = UsdGeom.Xform(prim)
        xform.ClearXformOpOrder()

        # Custom position and orientation
        # Convention: ground plane at z=0, robot base lifted by ROBOT_BASE_Z
        position = Gf.Vec3d(0.0, 0.0, ROBOT_BASE_Z)
        rpy_deg = np.array([0.0, 0.0, 180.0])  # Replace with your desired RPY
        rpy_rad = np.deg2rad(rpy_deg)
        quat_xyzw = euler_angles_to_quats(rpy_rad)
        quat = Gf.Quatd(quat_xyzw[0], quat_xyzw[1], quat_xyzw[2], quat_xyzw[3])

        xform.AddTranslateOp().Set(position)
        xform.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(quat)

        print(f"Applied translation and rotation to {prim_path}")

        # 5. Setup Articulation
        self._ur5e_view = ArticulationView(prim_paths_expr=prim_path, name="ur5e_view")
        World.instance().scene.add(self._ur5e_view)
        await World.instance().reset_async()
        self._timeline.stop()

        self._articulation = Articulation(prim_path)

        # Configure joint drives for all arm joints from self._ur5e_max_force / _stiffness / _damping (USD angular drive units: N*m/deg)
        joint_names = [
            "shoulder_pan_joint",
            "shoulder_lift_joint",
            "elbow_joint",
            "wrist_1_joint",
            "wrist_2_joint",
            "wrist_3_joint",
        ]
        for joint_name in joint_names:
            joint_path = f"{prim_path}/joints/{joint_name}"
            joint_prim = stage.GetPrimAtPath(joint_path)
            if not joint_prim.IsValid():
                print(f"Warning: Joint not found at {joint_path}")
                continue
            drive_api = UsdPhysics.DriveAPI.Apply(joint_prim, "angular")
            drive_api.GetMaxForceAttr().Set(self._ur5e_max_force)
            drive_api.GetStiffnessAttr().Set(self._ur5e_stiffness)
            drive_api.GetDampingAttr().Set(self._ur5e_damping)
            # PhysX caches the drive values when the articulation is created and only
            # refreshes on a *change* notice that arrives while it is listening. The
            # asset is authored with ~100x stiffer drives (10230 N*m/deg on the elbow),
            # and a same-value write notifies nobody, so the robot silently ran on the
            # asset gains: measured 586148 N*m/rad instead of the 5729.6 that
            # 100 N*m/deg means. Push the configured values straight into the solver.
            drive_api.GetStiffnessAttr().Set(self._ur5e_stiffness + 0.5)
            drive_api.GetDampingAttr().Set(self._ur5e_damping + 0.5)
            drive_api.GetStiffnessAttr().Set(self._ur5e_stiffness)
            drive_api.GetDampingAttr().Set(self._ur5e_damping)
            print(f"Set {joint_name}: maxForce={self._ur5e_max_force}, stiffness={self._ur5e_stiffness}, damping={self._ur5e_damping}")

        # USD angular drive values are per degree; the PhysX-side API is per radian.
        rad_per_deg = 57.29577951308232
        n = len(joint_names)
        try:
            self._ur5e_view.set_gains(
                kps=np.full((1, n), float(self._ur5e_stiffness) * rad_per_deg, dtype=np.float64),
                kds=np.full((1, n), float(self._ur5e_damping) * rad_per_deg, dtype=np.float64),
                joint_names=list(joint_names),
            )
        except Exception as exc:  # pragma: no cover - older view without set_gains
            print(f"Warning: could not push the drive gains into the selected physics engine ({type(exc).__name__}: {exc})")

        self._verify_drive_gains(prim_path, joint_names)
        print("UR5e robot loaded successfully!")

    def _verify_drive_gains(self, prim_path: str, joint_names: list) -> None:
        """Read the gains the selected engine is using and report them.

        The simulation, not the USD, decides how the joints behave, so a mismatch
        between the configured drive and the solver's gains has to be visible:
        the demo's step-response figures are labelled with these numbers.
        """
        expected_kps = float(self._ur5e_stiffness) * 57.29577951308232
        expected_kds = float(self._ur5e_damping) * 57.29577951308232
        try:
            kps, kds = self._ur5e_view.get_gains()
            import numpy as np
            kps = np.asarray(kps).reshape(-1)
            kds = np.asarray(kds).reshape(-1)
        except Exception as exc:  # pragma: no cover - physics view may be absent
            print(f"Warning: could not read the runtime drive gains ({type(exc).__name__}: {exc})")
            return
        bad = [
            (name, float(kps[i]), float(kds[i]))
            for i, name in enumerate(joint_names)
            if i < kps.size and (abs(float(kps[i]) - expected_kps) > 1.0 or abs(float(kds[i]) - expected_kds) > 0.1)
        ]
        if bad:
            for name, kp, kd in bad:
                print(
                    f"Warning: {name} runtime drive is stiffness={kp:.3f}, damping={kd:.3f} "
                    f"but the USD drive says {self._ur5e_stiffness}/{self._ur5e_damping} "
                    f"(= {expected_kps:.3f}/{expected_kds:.3f} N*m/rad)"
                )
        else:
            print(
                f"Runtime drive gains match the USD drive: {expected_kps:.3f} N*m/rad, "
                f"{expected_kds:.3f} N*m*s/rad on {len(joint_names)} arm joints"
            )

    def _disable_robot_chain_gravity(self):
        """Disable PhysX gravity on every rigid body in the robot chain (arm + Quick-Changer + RG2).

        WHY (the load-bearing reason): this ur5e-dt arm is a POSITION-MIRRORING digital twin. Its joints
        are driven from /joint_states — published by the real (or fake) UR ros2_control driver — via the
        ROS2 OmniGraph, and the gripper from its width command. Those upstream joint states ALREADY encode
        the real UR5e's internally gravity-compensated configuration. Simulating gravity on the twin's own
        links therefore DOUBLE-COUNTS it: the PD joint drives must develop a steady position error (sag) to
        hold against a gravity torque the real robot never actually fights. Measured (dual-a4500,
        2026-06-18): ~4 mrad sag on shoulder_lift/elbow at extended grasp poses → ~3 mm tool droop, which
        ate into the ≥5 mm fingertip table-clearance margin.

        Why NOT feed-forward gravity compensation (the textbook fix when the SIM is the authority — e.g.
        torque control / RL): it does not work on this OmniGraph-driven twin — the ArticulationController
        node rewrites the articulation action every physics step and zeros any joint efforts we apply
        (verified: applied_joint_efforts stays [0,…]). Feed-forward would be right if we drove the joints
        FROM the sim; here the path is the other way around (driver → sim), so the correct model is to stop
        simulating a force the upstream already handled.

        Gravity stays ON the manipulated objects (/World/Objects/*) — the sim IS the authority for how parts
        fall, are held, and settle — so grasping/placing/lifting are unchanged. Verified: sag 4.06 → 0.37
        mrad, tool droop 3.4 → 0.31 mm. Standard treatment for a kinematic/position-driven robot twin; also
        improves sim/real F/T parity (a real wrist F/T sensor is gravity-zeroed too). Authored as a USD
        attribute before reset_async so PhysX reads it when it initializes the articulation views.
        """
        import omni.usd
        from pxr import Usd, UsdPhysics, PhysxSchema
        stage = omni.usd.get_context().get_stage()
        n = 0
        for root in ("/World/UR5e", "/World/QuickChanger", "/World/RG2_Gripper"):
            rp = stage.GetPrimAtPath(root)
            if not rp or not rp.IsValid():
                continue
            for prim in Usd.PrimRange(rp):
                if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                    PhysxSchema.PhysxRigidBodyAPI.Apply(prim).CreateDisableGravityAttr(True)
                    n += 1
        print(f"[gravity] disabled on {n} robot-chain rigid bodies "
              f"(position-mirrored twin; manipulated objects keep gravity)")

    async def setup_action_graph(self):
        import omni.graph.core as og
        import isaacsim.core.utils.stage as stage_utils
        from isaacsim.core.utils.extensions import enable_extension

        print("Setting up ROS 2 Action Graph...")

        # Check that UR5e exists before creating graph
        stage = omni.usd.get_context().get_stage()
        if not stage.GetPrimAtPath("/World/UR5e"):
            print("Error: UR5e not found at /World/UR5e. Load UR5e first.")
            return

        # Ensure extensions are enabled
        enable_extension("isaacsim.ros2.bridge")
        enable_extension("isaacsim.core.nodes")
        enable_extension("omni.graph.action")

        graph_path = "/Graph/ActionGraph_UR5e"

        # Check if graph already exists
        if stage.GetPrimAtPath(graph_path):
            print(f"Action Graph already exists at {graph_path}, skipping creation.")
            return

        (graph, nodes, _, _) = og.Controller.edit(
            {
                "graph_path": graph_path,
                "evaluator_name": "execution"
            },
            {
                og.Controller.Keys.CREATE_NODES: [
                    ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
                    ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
                    ("isaac_read_simulation_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
                    ("ros2_subscribe_joint_state", "isaacsim.ros2.bridge.ROS2SubscribeJointState"),
                    ("ros2_publish_clock", "isaacsim.ros2.bridge.ROS2PublishClock"),
                    ("articulation_controller", "isaacsim.core.nodes.IsaacArticulationController"),
                ],
                og.Controller.Keys.SET_VALUES: [
                    ("ros2_context.inputs:useDomainIDEnvVar", True),
                    ("ros2_context.inputs:domain_id", 0),
                    ("ros2_subscribe_joint_state.inputs:topicName", "/joint_states"),
                    ("ros2_subscribe_joint_state.inputs:nodeNamespace", ""),
                    ("ros2_publish_clock.inputs:topicName", "/clock"),
                    ("ros2_publish_clock.inputs:nodeNamespace", ""),
                    ("isaac_read_simulation_time.inputs:resetOnStop", False),
                    ("articulation_controller.inputs:targetPrim", "/World/UR5e"),
                ],
                og.Controller.Keys.CONNECT: [
                    ("on_playback_tick.outputs:tick", "ros2_subscribe_joint_state.inputs:execIn"),
                    ("on_playback_tick.outputs:tick", "ros2_publish_clock.inputs:execIn"),
                    ("on_playback_tick.outputs:tick", "articulation_controller.inputs:execIn"),
                    ("ros2_context.outputs:context", "ros2_subscribe_joint_state.inputs:context"),
                    ("ros2_context.outputs:context", "ros2_publish_clock.inputs:context"),
                    ("isaac_read_simulation_time.outputs:simulationTime", "ros2_publish_clock.inputs:timeStamp"),
                    ("ros2_subscribe_joint_state.outputs:positionCommand", "articulation_controller.inputs:positionCommand"),
                    ("ros2_subscribe_joint_state.outputs:velocityCommand", "articulation_controller.inputs:velocityCommand"),
                    ("ros2_subscribe_joint_state.outputs:effortCommand", "articulation_controller.inputs:effortCommand"),
                    ("ros2_subscribe_joint_state.outputs:jointNames", "articulation_controller.inputs:jointNames"),
                ],
            }
        )

        print("ROS 2 Action Graph setup complete.")

    def setup_gripper_action_graph(self):
        """Pure JOINT-ACTUATOR seam for the husarion RG2 (migration a).

        The twin no longer owns any width↔joint math: that moved UP into the control-package
        sim backend (ros-mcp-server onrobot_rg2_sim_control). This graph just:
          - SUBSCRIBES std_msgs/Float64 on /rg2_sim/joint_target — the actuated joint angle (rad);
            a physics-rate callback applies it to rg2_gripper_joint (clamped to the URDF stops).
            The 5 mimic joints follow via PhysxMimicJointAPI — nothing to drive.
          - PUBLISHES sensor_msgs/JointState on /rg2_sim/joint_state via a PLAIN ROS2Publisher
            whose name[]/position[] fields the physics callback fills (rg2_gripper_joint, theta)
            so the backend reads the joint BY NAME and derives contact/raw/depth + /gripper_width_sim.
        Removed: the old String /gripper_command parse, the linear width→angle map, the 0..100 cap,
        and the width/effort/asymmetry publishers (the backend owns those semantics now).

        WHY a plain ROS2Publisher + callback (NOT isaacsim.ros2.bridge.ROS2PublishJointState):
        the articulation-introspecting JointState publisher WEDGES the main thread in C++ at
        timeline.play() on this welded gripper articulation (isolated 2026-06-19 by stepping the
        build: every USD step is fine; play wedges iff this graph carries ROS2PublishJointState,
        and a fresh play without it is clean). The force publisher uses this same proven plain-
        publisher pattern (see setup_force_publish_action_graph / _on_physics_step_force).
        """
        import omni.physx

        print("Setting up Gripper Action Graph (joint actuator)...")

        stage_check = omni.usd.get_context().get_stage()
        if not stage_check.GetPrimAtPath("/World/RG2_Gripper"):
            print("Error: RG2 gripper not found at /World/RG2_Gripper. Import and attach gripper first.")
            return


        graph_path = "/Graph/ActionGraph_RG2"
        keys = og.Controller.Keys

        stage = omni.usd.get_context().get_stage()

        (graph, nodes, _, _) = ensure_fixed_graph(
            stage, og, {"graph_path": graph_path, "evaluator_name": "execution"},
            {
                keys.CREATE_NODES: [
                    ("tick", "omni.graph.action.OnPlaybackTick"),
                    ("context", "isaacsim.ros2.bridge.ROS2Context"),
                    ("target_sub", "isaacsim.ros2.bridge.ROS2Subscriber"),
                    ("state_pub", "isaacsim.ros2.bridge.ROS2Publisher"),
                    ("pad_pub", "isaacsim.ros2.bridge.ROS2Publisher"),
                ],
                keys.SET_VALUES: [
                    # actuated-joint target in (rad)
                    ("target_sub.inputs:messageName", "Float64"),
                    ("target_sub.inputs:messagePackage", "std_msgs"),
                    ("target_sub.inputs:topicName", RG2_JOINT_TARGET_TOPIC),
                    # joint state out — plain JointState publisher; callback fills name[]/position[]
                    ("state_pub.inputs:messageName", "JointState"),
                    ("state_pub.inputs:messagePackage", "sensor_msgs"),
                    ("state_pub.inputs:topicName", RG2_JOINT_STATE_TOPIC),
                    # measured pad-to-pad gap (m) — plain Float64 publisher; callback fills data
                    ("pad_pub.inputs:messageName", "Float64"),
                    ("pad_pub.inputs:messagePackage", "std_msgs"),
                    ("pad_pub.inputs:topicName", RG2_PAD_GAP_TOPIC),
                ],
                keys.CONNECT: [
                    ("tick.outputs:tick", "target_sub.inputs:execIn"),
                    ("tick.outputs:tick", "state_pub.inputs:execIn"),
                    ("tick.outputs:tick", "pad_pub.inputs:execIn"),
                    ("context.outputs:context", "target_sub.inputs:context"),
                    ("context.outputs:context", "state_pub.inputs:context"),
                    ("context.outputs:context", "pad_pub.inputs:context"),
                ],
            }
        )

        self._stop_gripper_physics()

        # Store attribute paths for the physics callback (Float64 target → rg2_gripper_joint;
        # callback fills the JointState name[]/position[] on state_pub each step).
        self._gripper_graph_path = graph_path
        self._gripper_sub_attr_path = f"{graph_path}/target_sub.outputs:data"
        self._gripper_state_name_attr = f"{graph_path}/state_pub.inputs:name"
        self._gripper_state_pos_attr = f"{graph_path}/state_pub.inputs:position"
        self._gripper_pad_attr = f"{graph_path}/pad_pub.inputs:data"   # std_msgs/Float64 data (m)
        self._pad_local = None   # cached inner-most pad point per finger (link-local), filled lazily
        self._gripper_articulation = None
        self._gripper_act_dof_idx = None

        # Subscribe to physics step events — joint target applied at physics rate
        self._gripper_physx_sub = subscribe_physics_step(
            self._on_physics_step_gripper
        )
        self._gripper_publish_active = True

        print(f"Gripper Action Graph created at {graph_path}")
        print(f"  sub {RG2_JOINT_TARGET_TOPIC} (Float64 rad) -> {RG2_ACT_JOINT}")
        print(f"  pub {RG2_JOINT_STATE_TOPIC} (JointState; callback fills name/position)")

    def _on_physics_step_gripper(self, dt):
        """Physics step callback — apply the Float64 /rg2_sim/joint_target (rad) to rg2_gripper_joint
        AND publish the actual joint angle as a sensor_msgs/JointState (name[]/position[]).

        Both directions live here (proven plain-publisher pattern; the introspecting
        ROS2PublishJointState node wedges play). The 5 mimic joints follow via PhysxMimicJointAPI.
        """
        import numpy as np
        from isaacsim.core.utils.types import ArticulationActions

        try:
            # Drive via the gripper PRIM-ROOT path (the proven control surface the old RG2 rig
            # used), not the articulation-root child — ArticulationView resolves the husarion
            # articulation under it the same way the old gripper was rigged.
            artic = self._lazy_init_articulation(
                '_gripper_articulation', '/World/RG2_Gripper',
                'gripper_ctrl', '_gripper_warmup')
            if artic is None:
                return
            self._gripper_articulation = artic

            # Cache the actuated-joint DOF index (dof_names is DOF-aligned; joint_names also
            # lists the 3 fixed joints, which are not DOFs).
            if getattr(self, '_gripper_act_dof_idx', None) is None:
                names = list(getattr(artic, 'dof_names', None) or artic.joint_names)
                if RG2_ACT_JOINT not in names:
                    return
                self._gripper_act_dof_idx = names.index(RG2_ACT_JOINT)
            gidx = self._gripper_act_dof_idx

            # Read the Float64 joint target (rad) from the subscriber and apply it
            try:
                raw = og.Controller.get(og.Controller.attribute(self._gripper_sub_attr_path))
            except Exception:
                raw = None
            if raw is not None:
                try:
                    theta = float(np.clip(float(raw), RG2_THETA_MIN, RG2_THETA_MAX))
                    artic.apply_action(ArticulationActions(
                        joint_positions=np.array([[theta]]),
                        joint_indices=np.array([gidx])))
                except (TypeError, ValueError):
                    pass

            # Publish the ACTUAL actuated-joint angle as JointState (name + position) for the
            # control-pkg backend (which selects rg2_gripper_joint BY NAME).
            qpos = artic.get_joint_positions()
            if qpos is not None and qpos.shape[1] > gidx:
                actual = float(qpos[0, gidx])
                try:
                    og.Controller.attribute(self._gripper_state_name_attr).set([RG2_ACT_JOINT])
                    og.Controller.attribute(self._gripper_state_pos_attr).set([actual])
                except Exception:
                    pass

            # Publish the MEASURED pad-to-pad gap (m) — the TRUE contact gap (NOT arc(theta), which
            # under-reports under contact-stall). Nested try so it can NEVER break the joint drive.
            try:
                gap = self._compute_pad_gap_m()
                if gap is not None:
                    og.Controller.attribute(self._gripper_pad_attr).set(float(gap))
            except Exception:
                pass
        except Exception as e:
            print(f"[Gripper callback] {e}")

    def _compute_pad_gap_m(self):
        """Measured pad-to-pad gap (m): inner-most finger-pad collision-mesh point per finger,
        projected on the live closing axis. The inner points are fixed on the rigid pads, so cache
        them in each finger-link's LOCAL frame ONCE (find the extreme-inner collision vertex), then
        per-frame just world-transform the two cached points + project — ~4 transforms, no mesh scan.
        """
        from pxr import Usd, UsdGeom, Gf
        stage = omni.usd.get_context().get_stage()
        base = "/World/RG2_Gripper"
        lL, lR = RG2_FINGER_LINKS
        def link_xform(link):
            p = stage.GetPrimAtPath(f"{base}/{link}")
            if not (p and p.IsValid()):
                return None
            if selected_engine() == "newton":
                return runtime_link_matrix(self._gripper_articulation, link)
            return UsdGeom.Xformable(p).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        xL, xR = link_xform(lL), link_xform(lR)
        if xL is None or xR is None:
            return None
        # live closing axis from the finger markers (37.9, ∓18.98, 0) mm in each link frame
        mL = Gf.Vec3d(xL.Transform(Gf.Vec3d(0.0379, -0.01898, 0.0)))
        mR = Gf.Vec3d(xR.Transform(Gf.Vec3d(0.0379, 0.01898, 0.0)))
        ex = mL - mR
        n = ex.GetLength()
        if n < 1e-9:
            return None
        ex = ex / n
        if self._pad_local is None:
            # find the extreme-inner collision vertex per finger, cache in LINK-LOCAL frame
            def inner_local(link, xf, want_min):
                col = stage.GetPrimAtPath(f"{base}/{link}/collisions")
                mesh = next((UsdGeom.Mesh(pr) for pr in Usd.PrimRange(col)
                             if pr.GetTypeName() == "Mesh"), None)
                if mesh is None:
                    return None
                if selected_engine() == "newton":
                    # Mesh authoring is static relative to its rigid link. USD
                    # world poses do not follow Newton's Fabric runtime poses.
                    relative, reset = UsdGeom.XformCache().ComputeRelativeTransform(
                        mesh.GetPrim(), stage.GetPrimAtPath(f"{base}/{link}"))
                    if reset:
                        raise RuntimeError("Pad mesh resets its transform stack")
                    mw = relative * xf
                else:
                    mw = UsdGeom.Xformable(mesh).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                best_w, best_p = None, None
                for p in mesh.GetPointsAttr().Get():
                    w = Gf.Vec3d(mw.Transform(Gf.Vec3d(p)))
                    d = Gf.Dot(w, ex)
                    if best_p is None or (d < best_p if want_min else d > best_p):
                        best_p, best_w = d, w
                if best_w is None:
                    return None
                return xf.GetInverse().Transform(best_w)   # back to link-local (fixed on the pad)
            pl = inner_local(lL, xL, want_min=True)    # L is +ex side → inner = min projection
            pr = inner_local(lR, xR, want_min=False)   # R is -ex side → inner = max projection
            if pl is None or pr is None:
                return None
            self._pad_local = (pl, pr)
        wL = Gf.Vec3d(xL.Transform(self._pad_local[0]))
        wR = Gf.Vec3d(xR.Transform(self._pad_local[1]))
        return Gf.Dot(wL - wR, ex)

    def _stop_physics_callback(self, sub_attr, artic_attr, active_attr, warmup_attr):
        """Stop a physics callback and clean up its resources."""
        if hasattr(self, sub_attr) and getattr(self, sub_attr) is not None:
            close_physics_step(getattr(self, sub_attr))
            setattr(self, sub_attr, None)
        if hasattr(self, artic_attr):
            setattr(self, artic_attr, None)
        setattr(self, active_attr, False)
        setattr(self, warmup_attr, 0)

    def _lazy_init_articulation(self, artic_attr, prim_path, name_prefix, warmup_attr):
        """Lazy-init an ArticulationView, returning it or None if not ready.

        Also handles warmup countdown and stale handle detection.
        Returns the articulation if ready, None otherwise.
        """
        warmup = getattr(self, warmup_attr)
        if warmup > 0:
            setattr(self, warmup_attr, warmup - 1)
            return None

        artic = getattr(self, artic_attr)
        if artic is None:
            try:
                import time as _t
                from isaacsim.core.prims import Articulation as _AV
                artic = _AV(prim_paths_expr=prim_path, name=f"{name_prefix}_{int(_t.time()*1000)}")
                artic.initialize()
                setattr(self, artic_attr, artic)
            except Exception:
                setattr(self, artic_attr, None)
                return None

        # The physics view is created on a later physics step; asking for handle validity before then raises.
        if getattr(artic, '_physics_view', None) is None:
            return None

        if not artic.is_physics_handle_valid():
            setattr(self, artic_attr, None)
            setattr(self, warmup_attr, 30)
            return None

        return artic

    def _stop_gripper_physics(self):
        """Stop the gripper physics callback and clean up resources."""
        self._stop_physics_callback('_gripper_physx_sub', '_gripper_articulation',
                                    '_gripper_publish_active', '_gripper_warmup')

    def setup_force_publish_action_graph(self):
        """Setup force publishing as geometry_msgs/WrenchStamped on /force_torque_sensor_broadcaster/wrench_sim.

        Uses a physics step callback to read the native incoming-joint force
        [Fx, Fy, Fz, Tx, Ty, Tz] at the named wrist. Publishes the transformed
        tool0 wrench with simulation time, using the real robot's
        force_torque_sensor_broadcaster message format.
        """
        import omni.physx

        print("Setting up UR5e Force Publisher (WrenchStamped)...")

        # Skip if already active (physics callback running)
        if getattr(self, '_force_publish_active', False) and getattr(self, '_force_physx_sub', None) is not None:
            print("Force publisher already active, skipping setup.")
            return

        # Clean up any previous physics callback
        self._stop_force_publish()

        capability = wrench_capability()
        self._force_publish_status = dict(capability, status="warming_up")
        if not capability["available"]:
            self._force_publish_status["status"] = "unavailable"
            print(f"Force publisher unavailable: {capability['reason']}; no zero-wrench publisher created")
            return
        # Check that UR5e prim exists in the scene (may survive hot-reload even if Python state is lost)
        stage_check = omni.usd.get_context().get_stage()
        if not stage_check.GetPrimAtPath("/World/UR5e"):
            print("Error: UR5e prim not found at /World/UR5e. Load UR5e first.")
            return

        # Reuse existing ArticulationView if available; otherwise physics callback will lazy-init
        if self._ur5e_view is not None:
            self._effort_articulation = self._ur5e_view
            print(f"Using existing ArticulationView. Joints: {self._effort_articulation.joint_names}")
        else:
            self._effort_articulation = None
            print("ArticulationView not cached (hot-reload?). Will lazy-init in physics callback.")

        # Delete existing graph if it exists
        graph_path = "/Graph/ActionGraph_UR5e_ForcePublish"
        keys = og.Controller.Keys
        stage = omni.usd.get_context().get_stage()

        # Create OmniGraph with OnPlaybackTick for ROS2 publisher execution
        (graph, nodes, _, _) = ensure_fixed_graph(
            stage, og, {"graph_path": graph_path, "evaluator_name": "execution"},
            {
                keys.CREATE_NODES: [
                    ("tick", "omni.graph.action.OnPlaybackTick"),
                    ("context", "isaacsim.ros2.bridge.ROS2Context"),
                    ("publisher", "isaacsim.ros2.bridge.ROS2Publisher"),
                    ("valid", "omni.graph.action.Branch"),
                ],
                keys.SET_VALUES: [
                    ("publisher.inputs:messageName", "WrenchStamped"),
                    ("publisher.inputs:messagePackage", "geometry_msgs"),
                    ("publisher.inputs:topicName", "force_torque_sensor_broadcaster/wrench_sim"),
                    ("valid.inputs:condition", False),
                ],
                keys.CONNECT: [
                    ("context.outputs:context", "publisher.inputs:context"),
                    ("tick.outputs:tick", "valid.inputs:execIn"),
                    ("valid.outputs:execTrue", "publisher.inputs:execIn"),
                ]
            }
        )

        # Store graph path and publisher node reference for the physics callback.
        # The ROS2Publisher C++ node auto-creates dynamic attributes (wrench:force:x, etc.)
        # based on the WrenchStamped message definition. We access them via og.Controller.attribute()
        # using colon-separated field paths (the convention for nested ROS2 message fields in OG).
        self._force_graph_path = graph_path
        self._force_pub_node = nodes[2]  # publisher node

        # Set header frame_id
        og.Controller.attribute("inputs:header:frame_id", self._force_pub_node).set("tool0")

        # Physics callback reads joint forces at physics rate and updates the attributes
        self._force_physx_sub = subscribe_physics_step(
            self._on_physics_step_force
        )

        self._force_publish_active = True

        print(f"UR5e Force Publisher created at {graph_path}")
        print("Publishing geometry_msgs/WrenchStamped to topic: /force_torque_sensor_broadcaster/wrench_sim")
        print("Full 6-DOF wrench (Fx, Fy, Fz, Tx, Ty, Tz) from UR5e wrist joint")
        print("Joint forces read at the configured physics rate")

    def _on_physics_step_force(self, dt):
        """Physics step callback - read joint forces and update OmniGraph WrenchStamped attributes.

        Reads the named wrist's native incoming-joint wrench and transforms it
        to tool0 before publishing, with the observed simulation timestamp.
        """
        if self._force_pub_node is None:
            # Cleared by a stage OPENED event; the publisher graph is gone until it is rebuilt.
            return
        newton_force = selected_engine() == "newton"
        try:
            # New graphs gate both engines until a complete valid sample. Old
            # PhysX saved graphs without this node remain readable; do not
            # rebuild their authored graph merely to restore Python callbacks.
            self._set_force_gate(False, required=newton_force)
            artic = self._lazy_init_articulation(
                '_effort_articulation', '/World/UR5e',
                'force_pub_ctrl', '_force_warmup')
            if artic is None:
                return
            self._effort_articulation = artic
            # The physics view is created on a later physics step; skip until it exists instead of
            # raising on every step.
            if getattr(artic, '_physics_view', None) is None:
                return

            wrist = tool_wrench(artic, omni.usd.get_context().get_stage())
            from isaacsim.core.simulation_manager import SimulationManager
            sec, nanosec = physics_stamp(float(SimulationManager.get_simulation_time()))
            node = self._force_pub_node
            og.Controller.attribute("inputs:wrench:force:x", node).set(float(wrist[0]))
            og.Controller.attribute("inputs:wrench:force:y", node).set(float(wrist[1]))
            og.Controller.attribute("inputs:wrench:force:z", node).set(float(wrist[2]))
            og.Controller.attribute("inputs:wrench:torque:x", node).set(float(wrist[3]))
            og.Controller.attribute("inputs:wrench:torque:y", node).set(float(wrist[4]))
            og.Controller.attribute("inputs:wrench:torque:z", node).set(float(wrist[5]))
            # Dynamic ROS fields are process state on both engines; saved
            # graphs can reopen with an empty frame and a zero timestamp.
            og.Controller.attribute("inputs:header:frame_id", node).set("tool0")
            og.Controller.attribute("inputs:header:stamp:sec", node).set(sec)
            og.Controller.attribute("inputs:header:stamp:nanosec", node).set(nanosec)
            self._set_force_gate(True, required=newton_force)
            self._force_publish_status = dict(wrench_capability(), status="ready")
            self._force_last_error = None
        except Exception as e:
            self._force_publish_status = dict(available=False, status="unavailable", reason=str(e))
            if str(e) != getattr(self, "_force_last_error", None):
                print(f"[Force callback] {e}; publisher unavailable until a valid runtime observation")
                self._force_last_error = str(e)

    def _set_force_gate(self, value, required=False):
        stage = omni.usd.get_context().get_stage()
        path = "/Graph/ActionGraph_UR5e_ForcePublish/valid"
        if stage is None or not stage.GetPrimAtPath(path):
            if required:
                raise RuntimeError("Force publisher has no validity gate; rebuild its graph")
            return
        og.Controller.attribute(path + ".inputs:condition").set(value)

    def _stop_force_publish(self):
        """Stop the physics-rate force publisher and clean up resources."""
        self._set_force_gate(False)
        self._stop_physics_callback('_force_physx_sub', '_effort_articulation',
                                    '_force_publish_active', '_force_warmup')

    def import_rg2_gripper(self):
        """Reference the CAD-exact husarion-native OnRobot RG2 USD at /World/RG2_Gripper.

        This is the digital twin of the REAL RG2 linkage (husarion description, CAD-verified
        R=55 parallelogram): one actuated revolute `rg2_gripper_joint` plus 5 mimic finger
        joints coupled via PhysxMimicJointAPI. It REPLACES the former Robotiq-2F-relabeled
        RG2.usd (two independent joints, wrong linkage) so that the commanded contact width →
        achieved rubber gap is physically CAD-exact. This is the BARE gripper: the 15mm
        Quick-Changer is NOT baked in — it is a SEPARATE part (QuickChanger.usd) welded as the
        SOLE adapter (see attach_rg2_to_ur5e). The old husarion_rg2_fixed.usd baked a coupler in
        (tool0 → quick_changer → base = +15mm) which double-counted with the external QC (30mm).

        Built by scripts/convert_husarion_rg2_urdf.py + scripts/rewire_husarion_rg2_usd.py from the
        SPLIT onrobot_rg2 xacro (Isaac 5.1 URDF importer, parse_mimic=True). Structure: defaultPrim
        /onrobot_rg2 (→ /World/RG2_Gripper), articulation root rg2_base_link (no tool0, no internal
        quick_changer_link), joints under /World/RG2_Gripper/joints/. metersPerUnit 1.0.
        """
        from isaacsim.core.utils.stage import add_reference_to_stage

        # Skip if RG2 already loaded
        stage = omni.usd.get_context().get_stage()
        existing = stage.GetPrimAtPath("/World/RG2_Gripper")
        if existing and existing.IsValid():
            print("RG2 Gripper already exists at /World/RG2_Gripper, skipping import.")
            return

        # rg2_gripper_fixed.usd: the rewired self-contained BARE gripper (root rg2_base_link, NO
        # tool0/internal coupler; geometry+materials reparented UNDER the links by rewire_husarion_rg2_usd.py
        # since the raw importer orphans them in sibling /visuals,/colliders scopes). Built from the split
        # onrobot_rg2 xacro (no quick_changer). The old husarion_rg2_fixed.usd is kept for rollback.
        rg2_usd_path = _local_asset("gripper/husarion_rg2/rg2_gripper_fixed.usd")
        add_reference_to_stage(rg2_usd_path, "/World/RG2_Gripper")
        print("Husarion RG2 Gripper imported at /World/RG2_Gripper")

        stage = omni.usd.get_context().get_stage()
        joints_scope = "/World/RG2_Gripper/joints"

        # ROOT MASS FIX (2026-07-02): rg2_base_link is the gripper articulation ROOT but is a pure mounting
        # frame with NO geometry -> PhysX gives it mass=0. The Quick-Changer is welded to this zero-mass
        # root via a maximal-coordinate fixed joint (adapter_gripper_joint); a maximal-coord weld to a
        # ZERO-mass articulation root is the infinite-mass-ratio extreme -> compliant -> the whole gripper
        # body drifts ~4mm along the approach under the finger-drive reaction. Per the NVIDIA articulation-
        # stability guide the issue is the mass RATIO, so give the root a real mass + inertia (a massless
        # articulation root is the actual bare-USD authoring defect). This keeps RG2 its OWN articulation
        # (SingleArticulation + apply_action / the /rg2_sim/joint_target action-graph control path stay
        # valid) AND makes the weld rigid — NO articulation merge needed.
        _grip_root = stage.GetPrimAtPath("/World/RG2_Gripper/rg2_base_link")
        if _grip_root and _grip_root.IsValid():
            _m = UsdPhysics.MassAPI.Get(stage, _grip_root.GetPath()) or \
                UsdPhysics.MassAPI.Apply(_grip_root)
            _m.CreateMassAttr().Set(0.5)                                   # kg, ~ the QC (0.41) / hand scale
            _m.CreateDiagonalInertiaAttr().Set(Gf.Vec3f(1e-3, 1e-3, 1e-3))  # isotropic, ~m*(5cm)^2 scale

        # --- Actuated joint: set an explicit strong angular position drive (the imported gains
        # were sized for the weak default mimic and stall mid-range once the mimic is stiffened). ---
        act_path = f"{joints_scope}/{RG2_ACT_JOINT}"
        act_prim = stage.GetPrimAtPath(act_path)
        if act_prim and act_prim.IsValid():
            drive = UsdPhysics.DriveAPI.Get(act_prim, "angular") or \
                UsdPhysics.DriveAPI.Apply(act_prim, "angular")
            drive.CreateStiffnessAttr().Set(RG2_ACT_STIFFNESS)
            drive.CreateDampingAttr().Set(RG2_ACT_DAMPING)
            drive.CreateMaxForceAttr().Set(RG2_ACT_MAXFORCE)
            configure_newton_gripper(act_prim, selected_engine())
            act_pj = PhysxSchema.PhysxJointAPI.Get(stage, act_prim.GetPath()) or \
                PhysxSchema.PhysxJointAPI.Apply(act_prim)
            (act_pj.GetMaxJointVelocityAttr() or act_pj.CreateMaxJointVelocityAttr()).Set(
                float(RG2_MAX_JOINT_VEL))
            print(f"Actuated {RG2_ACT_JOINT}: stiffness={drive.GetStiffnessAttr().Get()} "
                  f"damping={drive.GetDampingAttr().Get()} maxForce={RG2_ACT_MAXFORCE} "
                  f"maxJointVel={RG2_MAX_JOINT_VEL}")
        else:
            print(f"Warning: actuated joint not found at {act_path}")

        # --- Mimic joints: stiffen the PhysxMimicJointAPI spring so the 5 finger joints
        # track the actuated joint RIGIDLY. The importer authors a weakly-damped spring
        # (naturalFrequency=25Hz, dampingRatio=0.005 → ~5.3° lag/ring at open). We do NOT
        # add a fixed-target DriveAPI (it would fight the gearing*ref+offset=0 constraint
        # everywhere except that pose — GPT plan-gate). naturalFrequency=1000/dampingRatio=1.0
        # was measured to give ~0.005° steady lag, 0 ring (scripts/test_husarion_rg2_mimic_rigidity.py).
        for jn in RG2_MIMIC_JOINTS:
            jp = stage.GetPrimAtPath(f"{joints_scope}/{jn}")
            if not (jp and jp.IsValid()):
                print(f"Warning: mimic joint not found: {jn}")
                continue
            nf = jp.GetAttribute("physxMimicJoint:rotZ:naturalFrequency")
            dr = jp.GetAttribute("physxMimicJoint:rotZ:dampingRatio")
            if nf:
                nf.Set(float(RG2_MIMIC_NAT_FREQ))
            if dr:
                dr.Set(float(RG2_MIMIC_DAMPING))
            # Clamp the importer's ~1e6 deg/s maxJointVelocity so the fingers can't slam shut.
            mimic_pj = PhysxSchema.PhysxJointAPI.Get(stage, jp.GetPath()) or \
                PhysxSchema.PhysxJointAPI.Apply(jp)
            (mimic_pj.GetMaxJointVelocityAttr() or mimic_pj.CreateMaxJointVelocityAttr()).Set(
                float(RG2_MAX_JOINT_VEL))
        print(f"Set mimic spring on {len(RG2_MIMIC_JOINTS)} joints: "
              f"naturalFrequency={RG2_MIMIC_NAT_FREQ} dampingRatio={RG2_MIMIC_DAMPING} "
              f"maxJointVel={RG2_MAX_JOINT_VEL}")

        # Configure gripper physics material
        gripper_mat_path = "/World/RG2_Gripper/PhysicsMaterial"
        gripper_material = UsdShade.Material.Define(stage, gripper_mat_path)
        gripper_mat_api = UsdPhysics.MaterialAPI.Apply(gripper_material.GetPrim())
        gripper_mat_api.CreateDynamicFrictionAttr().Set(self._gripper_dynamic_friction)
        gripper_mat_api.CreateRestitutionAttr().Set(self._gripper_restitution)
        gripper_mat_api.CreateStaticFrictionAttr().Set(self._gripper_static_friction)
        gripper_physx_mat_api = PhysxSchema.PhysxMaterialAPI.Apply(gripper_material.GetPrim())
        gripper_physx_mat_api.CreateFrictionCombineModeAttr().Set(self._gripper_friction_combine_mode)
        gripper_physx_mat_api.CreateRestitutionCombineModeAttr().Set(self._gripper_restitution_combine_mode)
        print(f"Created gripper physics material at {gripper_mat_path}")

        # Bind physics material to the fingertip links (and any collider children — material
        # binding inherits down, but bind at the link so finger contact uses these frictions).
        gripper_mat_sdf_path = Sdf.Path(gripper_mat_path)
        finger_prims = [f"/World/RG2_Gripper/{ln}" for ln in RG2_FINGER_LINKS]
        for finger_path in finger_prims:
            finger_prim = stage.GetPrimAtPath(finger_path)
            if finger_prim and finger_prim.IsValid():
                from omni.physx.scripts import physicsUtils
                physicsUtils.add_physics_material_to_prim(stage, finger_prim, gripper_mat_sdf_path)
                print(f"Bound gripper physics material to {finger_path}")
            else:
                print(f"Warning: Finger prim not found at {finger_path}")

    def import_adapter(self):
        """Reference the OnRobot Quick-Changer (15mm) adapter USD at /World/QuickChanger.

        The adapter goes between the UR5e flange (wrist_3/tool0) and the RG2 base.
        quick_changer_link frame == the flange mounting face (weld1 places it there via the
        arm's wrist_3->flange transform F). The coupler's shape orientation lives in the QC
        MESH xform, NOT in a joint: visuals/collisions carry a matrix xformOp = Ry(90) so the
        16mm-thick disc's mount axis runs along the flange approach (+X), landing tool_mount at
        (+15.92, 0, 0). This keeps BOTH coupling joints identity+pure-translation (weld2 is
        literal identity + 15.92mm along +X). Reoriented 2026-07-02 from the +Z-forward original
        (backup QuickChanger.usd.orig-preZtoX). See attach_rg2_to_ur5e for the joint splice.
        """
        from isaacsim.core.utils.stage import add_reference_to_stage
        stage = omni.usd.get_context().get_stage()
        if stage.GetPrimAtPath("/World/QuickChanger").IsValid():
            print("Adapter already at /World/QuickChanger, skipping.")
            return
        add_reference_to_stage(_local_asset("gripper/QuickChanger.usd"), "/World/QuickChanger")
        print("Quick-Changer adapter imported at /World/QuickChanger")

    def attach_rg2_to_ur5e(self):
        """Weld the husarion RG2 to the UR5e through the OnRobot Quick-Changer (TWO fixed joints).

        CANONICAL FLANGE-IDENTITY MOUNT (a4500 cleanroom, operator-confirmed 2026-07-01): the gripper
        is BARE (rg2_gripper_fixed.usd, root rg2_base_link, no baked coupler) and mounts to the UR5e
        FLANGE with ZERO relative rotation — the OnRobot RG2 mesh is X-forward and the flange is
        X-forward, so they align at identity. The external QuickChanger is the SOLE 15.92mm adapter and
        contributes PURE TRANSLATION along the approach (flange +X). Net flange→rg2_base = +15.92mm
        along flange +X, identity rotation — NOT the old 30mm double-count, and NONE of the old chain's
        rotations (quat0's +90 closing-fix, quat1, the internal +90/-90): all were redundant and net to
        flange-identity anyway (verified against the live twin). Two fixed joints:
          robot_gripper_joint :  wrist_3 → quick_changer_link   (placed AT the flange, localRot0/Pos0 = F)
          adapter_gripper_joint: quick_changer_link → rg2_base_link (pure +15.92mm along flange +X, identity)
        where F = the measured wrist_3→flange transform (ur_description: flange = wrist_3·Ry(-90)); it is
        read from the asset, not hand-tuned. We pre-seat both
        /World/QuickChanger and /World/RG2_Gripper so the joints only nudge, then GATE rg2_base vs that
        target pre-play. Both joints are excludeFromArticulation=True (UR5e and gripper stay separate
        articulations, with the free QC rigid body welded between).
        """
        import omni.usd
        from pxr import Usd, Sdf, UsdGeom, Gf
        import math

        engine = selected_engine()
        require_newton_mount_support(engine)

        stage = omni.usd.get_context().get_stage()
        rg2_root = "/World/RG2_Gripper"
        rg2_tool0 = f"{rg2_root}/{RG2_ART_ROOT_REL}"      # husarion articulation root = flange frame
        wrist3_path = "/World/UR5e/wrist_3_link"
        joint_path = "/World/UR5e/joints/robot_gripper_joint"

        rg2_prim = stage.GetPrimAtPath(rg2_root)
        w3_prim = stage.GetPrimAtPath(wrist3_path)
        tool0_prim = stage.GetPrimAtPath(rg2_tool0)
        if not (rg2_prim and rg2_prim.IsValid()):
            print(f"Error: RG2 gripper not found at {rg2_root}. Import gripper first.")
            return
        if not (w3_prim and w3_prim.IsValid()):
            print(f"Error: {wrist3_path} not found. Load UR5e first.")
            return
        if not (tool0_prim and tool0_prim.IsValid()):
            print(f"Error: husarion articulation root not found at {rg2_tool0}.")
            return

        # Skip if already fully connected (both bodies set)
        joint_prim = stage.GetPrimAtPath(joint_path)
        if joint_prim and joint_prim.IsValid():
            _jnt = UsdPhysics.FixedJoint(joint_prim)
            if _jnt.GetBody0Rel().GetTargets() and _jnt.GetBody1Rel().GetTargets():
                print(f"Gripper already attached at {joint_path}, skipping.")
                return

        # === Canonical FLANGE-IDENTITY mount (a4500 cleanroom, operator-confirmed 2026-07-01) ===
        # The gripper mounts to the UR5e FLANGE with ZERO relative rotation: the OnRobot RG2 mesh is
        # X-forward and the flange is X-forward, so they align at identity. The 15.92mm OnRobot
        # Quick-Changer is the SOLE adapter and contributes PURE TRANSLATION along the approach (flange
        # +X). No tool0 on the gripper, no baked coupler, and NO closing-axis / reorient quats — every
        # rotation the old chain carried (quat0's +90, quat1, the internal +90/-90) was redundant and
        # NET to flange-identity anyway (verified against the live twin: flange->rg2_base = identity
        # rotation, [15.92,0,0]mm, approach = flange +X). So we author that net directly.
        def _rot_m(qf):
            m = Gf.Matrix4d(1.0)
            m.SetRotateOnly(Gf.Quatd(float(qf.GetReal()), Gf.Vec3d(qf.GetImaginary())))
            return m

        STANDOFF_M = 0.01592  # OnRobot Quick-Changer standoff (CAD; supersedes the URDF's 15.0 outlier)

        # Flange frame relative to the wrist_3 rigid body. Joint bodies must be rigid bodies, and the
        # gripper aligns to the flange, so the weld carries the MEASURED wrist_3->flange transform F
        # (ur_description: flange = wrist_3 . Ry(-90)) — not a hand-tuned quat.
        flange_prim = stage.GetPrimAtPath(f"{wrist3_path}/flange")
        if not (flange_prim and flange_prim.IsValid()):
            print(f"Error: flange frame not found at {wrist3_path}/flange."); return
        w3_l2w = UsdGeom.Xformable(w3_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        flange_l2w = UsdGeom.Xformable(flange_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        F = flange_l2w * w3_l2w.GetInverse()          # wrist_3 -> flange, in the wrist_3 frame
        _Fq = F.ExtractRotationQuat()
        F_rot = Gf.Quatf(float(_Fq.GetReal()), Gf.Vec3f(_Fq.GetImaginary()))
        F_pos = Gf.Vec3f(F.ExtractTranslation())

        self.import_adapter()
        qc_link_path = "/World/QuickChanger/quick_changer_link"
        adapter_joint_path = "/World/UR5e/joints/adapter_gripper_joint"
        qc_root_prim = stage.GetPrimAtPath("/World/QuickChanger")
        qc_link_prim = stage.GetPrimAtPath(qc_link_path)
        if not (qc_link_prim and qc_link_prim.IsValid()):
            print("Error: QuickChanger quick_changer_link not found — import_adapter failed.")
            return

        # PhysX structural chain (arm | adapter | gripper), matching prismatic pm_scene/
        # load_gripper.py (_attach_adapter_to_ur5e + _attach_rg2_to_adapter). The Quick-Changer is a
        # FREE rigid body welded IN SERIES between the two articulations (both joints
        # excludeFromArticulation=True) and is the STRUCTURAL owner of the 15.92mm standoff — NOT a
        # cosmetic decoration. Removing the QC would drop the gripper by 15.92mm. A properly-stiff
        # maximal weld through the QC is stable under play (the a4500 live oracle holds
        # flange->rg2_base = 15.918mm / 0.059deg for 449s); the ~20-36mm drift seen earlier was a
        # headless-standalone init artifact, not this chain.
        #
        # Joint 1 — robot_gripper_joint: wrist_3 -> quick_changer_link, placing the coupler AT the
        # flange, aligned to it (localRot0/Pos0 = F = measured wrist_3->flange). STRUCTURAL.
        if not (joint_prim and joint_prim.IsValid()):
            joint_prim = stage.DefinePrim(joint_path, "PhysicsFixedJoint")
        joint_prim.CreateRelationship("physics:body0").SetTargets([Sdf.Path(wrist3_path)])
        joint_prim.CreateRelationship("physics:body1").SetTargets([Sdf.Path(qc_link_path)])
        joint_prim.CreateAttribute("physics:jointEnabled", Sdf.ValueTypeNames.Bool).Set(True)
        joint_prim.CreateAttribute("physics:excludeFromArticulation", Sdf.ValueTypeNames.Bool).Set(True)
        joint_prim.CreateAttribute("physics:localPos0", Sdf.ValueTypeNames.Point3f, custom=True).Set(F_pos)
        joint_prim.CreateAttribute("physics:localPos1", Sdf.ValueTypeNames.Point3f, custom=True).Set(Gf.Vec3f(0, 0, 0))
        joint_prim.CreateAttribute("physics:localRot0", Sdf.ValueTypeNames.Quatf, custom=True).Set(F_rot)
        joint_prim.CreateAttribute("physics:localRot1", Sdf.ValueTypeNames.Quatf, custom=True).Set(Gf.Quatf(1, 0, 0, 0))

        # Keep the RG2 articulation separate for its named joint controller.
        # The PhysX chain uses two maximal-coordinate welds. Newton instead
        # includes QC in the arm tree and uses an inter-tree equality weld for
        # RG2; configure_newton_mount applies that adapter below.
        off = Gf.Matrix4d(1.0); off.SetTranslateOnly(Gf.Vec3d(STANDOFF_M, 0, 0))
        rg2_base_target = off * flange_l2w

        def _weld(path, b0, b1, lp0, lr0):
            j = stage.GetPrimAtPath(path)
            if not (j and j.IsValid()): j = stage.DefinePrim(path, "PhysicsFixedJoint")
            j.CreateRelationship("physics:body0").SetTargets([Sdf.Path(b0)])
            j.CreateRelationship("physics:body1").SetTargets([Sdf.Path(b1)])
            j.CreateAttribute("physics:jointEnabled", Sdf.ValueTypeNames.Bool).Set(True)
            # excludeFromArticulation=TRUE: UR5e and RG2 stay SEPARATE articulations (the QC is a free
            # body welded between). This preserves SingleArticulation("/World/RG2_Gripper") + apply_action
            # — the control path the gripper action graph (/rg2_sim/joint_target) and the prismatic
            # pipeline both use. The ~4mm drift is NOT fixed by merging (that breaks gripper control);
            # it's fixed by giving the gripper's articulation-root body real MASS (see import_rg2_gripper)
            # so the maximal-coord weld has a FINITE mass ratio and won't flex.
            j.CreateAttribute("physics:excludeFromArticulation", Sdf.ValueTypeNames.Bool).Set(True)
            j.CreateAttribute("physics:localPos0", Sdf.ValueTypeNames.Point3f, custom=True).Set(lp0)
            j.CreateAttribute("physics:localPos1", Sdf.ValueTypeNames.Point3f, custom=True).Set(Gf.Vec3f(0, 0, 0))
            j.CreateAttribute("physics:localRot0", Sdf.ValueTypeNames.Quatf, custom=True).Set(lr0)
            j.CreateAttribute("physics:localRot1", Sdf.ValueTypeNames.Quatf, custom=True).Set(Gf.Quatf(1, 0, 0, 0))
        # weld1: wrist_3 -> QC (at the flange). weld2: QC -> rg2_base (+15.92 along +X = the adapter face).
        _weld(joint_path, wrist3_path, qc_link_path, F_pos, F_rot)
        _weld(adapter_joint_path, qc_link_path, rg2_tool0, Gf.Vec3f(STANDOFF_M, 0, 0), Gf.Quatf(1, 0, 0, 0))
        configure_newton_mount(
            stage.GetPrimAtPath(joint_path), stage.GetPrimAtPath(adapter_joint_path), engine
        )
        print(f"Mount configured for {engine}: wrist_3 -> QuickChanger -> RG2; arm and gripper remain separate articulations.")

        # Pre-seat QC at the flange and rg2_base at flange+15.92 (joints only hold; no play-time snap).
        qc_link_local = UsdGeom.Xformable(qc_link_prim).GetLocalTransformation()
        qc_seed = qc_link_local.GetInverse() * flange_l2w
        qxf = UsdGeom.Xform(qc_root_prim); qxf.ClearXformOpOrder()
        qxf.AddTranslateOp().Set(qc_seed.ExtractTranslation())
        qxf.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(qc_seed.ExtractRotationQuat())
        tool0_local = UsdGeom.Xformable(tool0_prim).GetLocalTransformation()
        root_seed = tool0_local.GetInverse() * rg2_base_target
        xf = UsdGeom.Xform(rg2_prim); xf.ClearXformOpOrder()
        xf.AddTranslateOp().Set(root_seed.ExtractTranslation())
        xf.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(root_seed.ExtractRotationQuat())

        # GATE: rg2_base_link must resolve to rg2_base_target (the +15mm tool_mount frame) within tol.
        tool0_now = UsdGeom.Xformable(tool0_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        dpos_mm = (tool0_now.ExtractTranslation() - rg2_base_target.ExtractTranslation()).GetLength() * 1000.0
        q_now = tool0_now.ExtractRotationQuat().GetNormalized()
        q_des = rg2_base_target.ExtractRotationQuat().GetNormalized()
        q_rel = q_des.GetInverse() * q_now
        dang_deg = math.degrees(2.0 * math.acos(min(1.0, abs(q_rel.GetReal()))))
        if dpos_mm < 0.1 and dang_deg < 0.1:
            print(f"[attach] QC weld delta OK: dpos={dpos_mm:.4f}mm dang={dang_deg:.4f}deg (gated <0.1/<0.1).")
        else:
            print(f"[attach] !! QC WELD DELTA OUT OF TOL: dpos={dpos_mm:.4f}mm dang={dang_deg:.4f}deg "
                  f"— inspect frames before play; do NOT trust the mount.")

        print("Husarion RG2 attached to UR5e via the 15mm OnRobot Quick-Changer (asset-driven, restored).")

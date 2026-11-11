"""Task objects: adding, placing, randomizing, hiding and publishing their poses."""

import omni.kit.commands
import omni.usd
import asyncio
import numpy as np
import os
import carb
import json
from .asset_cache import AssetMissing, resolve_dir
from .object_pose import set_local_pose
from pxr import UsdGeom, Gf, Sdf, Usd, UsdPhysics, PhysxSchema
from scipy.spatial.transform import Rotation as R
from .settings import ASSEMBLIES, _assembly_layout, _local_asset_dir, _use_legacy_tf_publisher


class Objects:
    """Part of DigitalTwin (extension.py), which mixes it in."""

    def _on_assembly_changed(self, model):
        """Called when the assembly combo box selection changes."""
        idx = model.get_item_value_model().as_int
        self._selected_assembly = list(ASSEMBLIES.keys())[idx]

    @property
    def _objects_folder_path(self):
        return _local_asset_dir(ASSEMBLIES[self._selected_assembly]["folder"])

    @property
    def _assembly_file_path(self):
        return _assembly_layout(ASSEMBLIES[self._selected_assembly]["assembly_file"])

    def _sync_selected_assembly(self, folder_path="/World/Objects"):
        """Auto-detect the active assembly from scene contents and update _selected_assembly."""
        stage = omni.usd.get_context().get_stage()
        objects_root = stage.GetPrimAtPath(folder_path)
        if not objects_root or not objects_root.IsValid():
            return
        scene_names = {child.GetName() for child in objects_root.GetChildren()}
        for key, assembly_def in ASSEMBLIES.items():
            assembly_file = _assembly_layout(assembly_def["assembly_file"])
            if assembly_file and os.path.exists(assembly_file):
                with open(assembly_file, 'r') as f:
                    components = json.load(f).get('components', [])
                board_names = [c['name'] for c in components if c.get('type') == 'board']
                if any(name in scene_names for name in board_names):
                    if self._selected_assembly != key:
                        print(f"Auto-detected active assembly: {key} (was {self._selected_assembly})")
                        self._selected_assembly = key
                    return

    def _load_object_type_map(self):
        """Read the assembly JSON and return a dict mapping object name -> category (board/block/peg/socket).

        Auto-detects the active assembly from scene contents first, so the correct
        file is always used regardless of _selected_assembly state.
        JSON uses type="board" for boards, and type="object" with subtype for the rest.
        Returns the effective category for prim settings lookup.
        """
        self._sync_selected_assembly()
        type_map = {}
        assembly_file = self._assembly_file_path
        if assembly_file and os.path.exists(assembly_file):
            with open(assembly_file, 'r') as f:
                assembly_data = json.load(f)
            for component in assembly_data.get('components', []):
                comp_type = component.get('type', 'object')
                if comp_type == 'board':
                    category = 'board'
                else:
                    category = component.get('subtype', 'block')
                type_map[component['name']] = category
        else:
            print(f"Warning: Assembly file not found: {assembly_file or 'layout is not in the asset folder (run isaac_assets.py doctor)'}, defaulting all objects to 'block'")
        return type_map

    def _get_prim_params(self, category):
        """Return prim settings dict for the given category (board/block/peg/socket)."""
        prefix = f"_{category}_"
        params = {
            "collision_approximation": getattr(self, f"{prefix}collision_approximation"),
            "rest_offset": getattr(self, f"{prefix}rest_offset"),
            "angular_damping": getattr(self, f"{prefix}angular_damping"),
        }
        return params

    def _sample_non_overlapping_objects(
        self,
        num_objects,
        x_range=(-0.2, 0.3),
        y_range=(-0.5, -0.3),
        min_sep=0.18,
        yaw_range=(-180.0, 180.0),
        z_values=None,
        fixed_positions=None,
        max_attempts=10_000,
        max_retries=100,
    ):
        """
        Sample non-overlapping object poses in world frame.

        Args:
            num_objects: Number of objects to place
            x_range: X position range in world frame (default: -0.5 to 0.5)
            y_range: Y position range in world frame (default: -0.5 to 0.0)
            min_sep: Minimum separation between objects
            yaw_range: Yaw rotation range in degrees
            z_values: List of Z values for each object (preserves current Z)
            fixed_positions: List of fixed positions (e.g., base objects) to avoid
            max_attempts: Maximum placement attempts per retry
            max_retries: Maximum number of times to restart the entire placement
        """
        # Convert fixed positions to numpy arrays for distance checking
        fixed_xy = []
        if fixed_positions:
            for pos in fixed_positions:
                if isinstance(pos, (list, tuple, np.ndarray)):
                    fixed_xy.append(np.array(pos[:2]))  # Only X, Y
                elif hasattr(pos, '__getitem__'):
                    fixed_xy.append(np.array([pos[0], pos[1]]))

        for retry in range(max_retries):
            poses, attempts = [], 0

            while len(poses) < num_objects and attempts < max_attempts:
                attempts += 1
                candidate_xy = np.array([
                    np.random.uniform(*x_range),
                    np.random.uniform(*y_range)
                ])
                # Check separation against both existing poses AND fixed positions
                valid = True
                # Check against existing randomized poses
                if any(np.linalg.norm(candidate_xy - p["position"][:2]) < min_sep for p in poses):
                    valid = False
                # Check against fixed positions (base objects)
                if valid and fixed_xy:
                    if any(np.linalg.norm(candidate_xy - fixed) < min_sep for fixed in fixed_xy):
                        valid = False

                if valid:
                    yaw_deg = np.random.uniform(*yaw_range)
                    # Use provided Z value or default
                    z = z_values[len(poses)] if z_values and len(poses) < len(z_values) else 0.0495
                    poses.append({
                        "position": np.array([candidate_xy[0], candidate_xy[1], z]),
                        "yaw_deg": yaw_deg
                    })

            if len(poses) == num_objects:
                if retry > 0:
                    print(f"Placement succeeded after {retry + 1} retries")
                return poses

        raise RuntimeError("Could not place all objects without violating min_sep: the other objects take up the free slots. "
                           "Move them first with randomize_object_poses or set_object_pose.")

    def _set_obj_prim_pose(self, prim_path, position, quat_wxyz):
        stage = omni.usd.get_context().get_stage()
        set_local_pose(stage.GetPrimAtPath(prim_path), position, quat_wxyz)

    def assemble_objects(self, folder_path="/World/Objects"):
        """Assemble objects based on JSON assembly data, positioned relative to the current board pose."""
        assembly_file = self._assembly_file_path
        if not os.path.exists(assembly_file):
            print(f"[assemble] Error: Assembly file not found: {assembly_file or 'layout is not in the asset folder (run isaac_assets.py doctor)'}")
            return 0, 0

        with open(assembly_file, 'r') as f:
            assembly_data = json.load(f)

        stage = omni.usd.get_context().get_stage()

        # Find the board object's current world position to use as assembly origin
        type_map = self._load_object_type_map()
        objects_root = stage.GetPrimAtPath(folder_path)
        if not objects_root.IsValid():
            print(f"[assemble] Error: {folder_path} does not exist")
            return 0, 0

        base_world_pos = Gf.Vec3d(0, 0, 0)
        for child in objects_root.GetChildren():
            obj_name = child.GetName()
            if type_map.get(obj_name, 'block') == 'board':
                child_path = self._get_prim_path(obj_name, folder_path)
                child_prim = stage.GetPrimAtPath(child_path)
                if child_prim and child_prim.IsValid():
                    child_xform = UsdGeom.Xformable(child_prim)
                    world_transform = child_xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                    base_world_pos = world_transform.ExtractTranslation()
                break

        # Position each component from assembly data
        assembled = 0
        skipped = 0
        for component in assembly_data['components']:
            name = component['name']
            position = component['position']
            rotation = component['rotation']

            prim_path = self._get_prim_path(name, folder_path)
            prim = stage.GetPrimAtPath(prim_path)
            if not prim or not prim.IsValid():
                skipped += 1
                continue

            # Assembly position offset relative to base
            assembly_pos = Gf.Vec3d(
                base_world_pos[0] + position['x'],
                base_world_pos[1] + position['y'],
                base_world_pos[2] + position['z']
            )

            q = rotation["quaternion"]
            self._set_obj_prim_pose(prim_path, assembly_pos, [q[k] for k in "wxyz"])

            assembled += 1

        msg = f"[assemble] Assembled {assembled} objects ({self._selected_assembly})"
        if skipped:
            msg += f", {skipped} skipped (prim not found)"
        print(msg)
        return assembled, skipped

    def disassemble_objects(self, folder_path="/World/Objects"):
        """Lay out objects spaced apart along the X-axis (like initial add_objects positioning)."""
        stage = omni.usd.get_context().get_stage()
        objects_root = stage.GetPrimAtPath(folder_path)
        if not objects_root.IsValid():
            print(f"[disassemble] Error: {folder_path} does not exist")
            return 0

        count = 0
        for child in objects_root.GetChildren():
            obj_name = child.GetName()
            if obj_name.endswith("Material"):
                continue

            child_path = self._get_prim_path(obj_name, folder_path)
            child_prim = stage.GetPrimAtPath(child_path)
            if not child_prim or not child_prim.IsValid():
                continue

            # Calculate spaced position: first at center, then alternating +X and -X
            if count == 0:
                x_position = 0.0
            elif count % 2 == 1:
                x_position = self._object_spacing * ((count + 1) // 2)
            else:
                x_position = -self._object_spacing * (count // 2)

            pos = Gf.Vec3d(x_position, self._y_offset, self._z_offset)

            self._set_obj_prim_pose(child_path, pos, [1, 0, 0, 0])

            count += 1

        print(f"[disassemble] Disassembled {count} objects")
        return count

    def randomize_object_poses(self, folder_path="/World/Objects"):
        """
        Randomize object poses in world frame, then transform to local child prim frame.
        Randomizes position of /World/Objects/{object_name} in world coordinates,
        then applies the appropriate local transform to the child prim.
        """
        stage = omni.usd.get_context().get_stage()
        objects_root = stage.GetPrimAtPath(folder_path)
        if not objects_root.IsValid():
            print(f"[randomize] Error: {folder_path} does not exist")
            return 0, 0

        # Collect object info and board positions
        type_map = self._load_object_type_map()
        object_info = []
        board_positions = []

        for child in objects_root.GetChildren():
            obj = child.GetName()
            parent_path = f"{folder_path}/{obj}"
            child_path = self._get_prim_path(obj, folder_path)

            parent_prim = stage.GetPrimAtPath(parent_path)
            child_prim = stage.GetPrimAtPath(child_path)

            if not parent_prim.IsValid() or not child_prim.IsValid():
                continue

            child_xform = UsdGeom.Xformable(child_prim)
            child_world_transform = child_xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            child_world_pos = child_world_transform.ExtractTranslation()

            if type_map.get(obj, 'block') == 'board':
                board_positions.append(child_world_pos)
                continue

            parent_xform = UsdGeom.Xformable(parent_prim)
            parent_world_transform = parent_xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            parent_world_pos = parent_world_transform.ExtractTranslation()

            object_info.append({
                "parent_path": parent_path,
                "child_path": child_path,
                "parent_world_pos": parent_world_pos,
                "parent_world_transform": parent_world_transform,
                "current_z": child_world_pos[2]
            })

        if not object_info:
            print("[randomize] Error: No valid objects found")
            return 0, len(board_positions)

        z_values = [info["current_z"] for info in object_info]

        # Auto-relax min_sep so dense / large-footprint sets still place. The 0.18 default is
        # tuned for the small fmb parts; 5+ jenga blocks (72 mm long) + base4 in the 0.5x0.2 m
        # workspace overflow it and the placer hard-fails. fmb succeeds on the first try (0.18 →
        # identical behavior); jenga steps down until it fits. Floor 0.08 > a jenga block's 76 mm
        # overlap threshold, so non-overlap is still guaranteed. (graceful, not a silent fallback —
        # the used value is logged.)
        poses = None
        min_sep = 0.18
        while min_sep >= 0.08 - 1e-9:
            try:
                poses = self._sample_non_overlapping_objects(
                    num_objects=len(object_info),
                    z_values=z_values,
                    fixed_positions=board_positions,
                    min_sep=min_sep,
                )
                if min_sep < 0.18:
                    print(f"[randomize] placed with relaxed min_sep={min_sep:.3f} m "
                          f"({len(object_info)} objects)")
                break
            except RuntimeError:
                min_sep *= 0.8
        if poses is None:
            raise RuntimeError(
                f"Could not place {len(object_info)} objects even at min_sep=0.08 m — "
                f"workspace too small for this set; reduce count or enlarge the workspace.")

        # Apply randomized poses
        for obj_info, pose in zip(object_info, poses):
            child_path = obj_info["child_path"]
            parent_world_transform = obj_info["parent_world_transform"]

            target_world_pos = Gf.Vec3d(pose["position"][0], pose["position"][1], pose["position"][2])
            parent_inverse = parent_world_transform.GetInverse()
            local_pos = parent_inverse.Transform(target_world_pos)

            quat_xyzw = R.from_euler("xyz", [0.0, 0.0, pose["yaw_deg"]], degrees=True).as_quat()
            quat_wxyz = np.roll(quat_xyzw, 1)

            self._set_obj_prim_pose(child_path, local_pos, quat_wxyz)

        print(f"[randomize] Randomized {len(object_info)} objects, {len(board_positions)} boards skipped")
        return len(object_info), len(board_positions)

    def randomize_single_object(self, object_name, folder_path="/World/Objects",
                                x_range=(-0.35, -0.31), y_range=(-0.5, -0.3),
                                min_sep=0.18, yaw_range=(-180.0, 180.0), max_attempts=1000):
        """Randomize a single object's pose in a clear workspace area, keeping all others fixed.

        Args:
            object_name: Name of the object to randomize.
            folder_path: Parent prim path for objects.
            x_range: X range for randomization (clear workspace area).
            y_range: Y range for randomization (clear workspace area).
            min_sep: Minimum separation from all other objects.
            yaw_range: Yaw rotation range in degrees.
            max_attempts: Maximum placement attempts.
        """
        stage = omni.usd.get_context().get_stage()
        objects_root = stage.GetPrimAtPath(folder_path)
        if not objects_root.IsValid():
            raise ValueError(f"{folder_path} does not exist")

        # Verify the target object exists
        target_parent_path = f"{folder_path}/{object_name}"
        target_child_path = self._get_prim_path(object_name, folder_path)
        target_parent_prim = stage.GetPrimAtPath(target_parent_path)
        target_child_prim = stage.GetPrimAtPath(target_child_path)
        if not target_parent_prim.IsValid() or not target_child_prim.IsValid():
            raise ValueError(f"Object '{object_name}' not found at {target_child_path}")

        # Collect world positions of all OTHER objects as fixed positions to avoid
        fixed_positions = []
        for child in objects_root.GetChildren():
            name = child.GetName()
            if name == object_name:
                continue
            child_path = self._get_prim_path(name, folder_path)
            child_prim = stage.GetPrimAtPath(child_path)
            if child_prim.IsValid():
                xform = UsdGeom.Xformable(child_prim)
                world_pos = xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
                fixed_positions.append(world_pos)

        # Get current Z of target object
        target_xform = UsdGeom.Xformable(target_child_prim)
        target_world_pos = target_xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
        target_z = target_world_pos[2]

        # Sample one non-overlapping position
        poses = self._sample_non_overlapping_objects(
            num_objects=1,
            x_range=x_range,
            y_range=y_range,
            min_sep=min_sep,
            yaw_range=yaw_range,
            z_values=[target_z],
            fixed_positions=fixed_positions,
            max_attempts=max_attempts,
        )

        # Apply the pose
        pose = poses[0]
        parent_xform = UsdGeom.Xformable(target_parent_prim)
        parent_world_transform = parent_xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())

        target_world = Gf.Vec3d(pose["position"][0], pose["position"][1], pose["position"][2])
        local_pos = parent_world_transform.GetInverse().Transform(target_world)

        quat_xyzw = R.from_euler("xyz", [0.0, 0.0, pose["yaw_deg"]], degrees=True).as_quat()
        quat_wxyz = np.roll(quat_xyzw, 1)

        self._set_obj_prim_pose(target_child_path, local_pos, quat_wxyz)

        print(f"[randomize_single] Placed '{object_name}' at world ({pose['position'][0]:.3f}, {pose['position'][1]:.3f}, {pose['position'][2]:.4f}), yaw={pose['yaw_deg']:.1f}°")

    def set_object_pose(self, object_name, position, orientation=None, yaw_deg=None, folder_path="/World/Objects"):
        """Request an EXACT world pose for an object. Mirrors randomize_single_object's apply path
        (resolve parent-local pose, set xformOp:translate/orient via _set_obj_prim_pose) so the pose
        persists under physics. Orientation precedence: orientation=[w,x,y,z] quat > yaw_deg about z >
        identity. position is [x,y,z] in METERS (world). Returns the requested world position; physics
        settles the object from there, so read its pose again to see where it ended up."""
        import numpy as np
        stage = omni.usd.get_context().get_stage()
        target_child_path = self._get_prim_path(object_name, folder_path)
        target_child_prim = stage.GetPrimAtPath(target_child_path)
        if not target_child_prim or not target_child_prim.IsValid():
            raise ValueError(f"Object '{object_name}' not found at {target_child_path}")

        # Resolve the world target into the child's parent-local frame (matches randomize_single_object).
        parent_xform = UsdGeom.Xformable(target_child_prim.GetParent())
        parent_world_transform = parent_xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        target_world = Gf.Vec3d(float(position[0]), float(position[1]), float(position[2]))
        local_pos = parent_world_transform.GetInverse().Transform(target_world)

        if orientation is not None:
            quat_wxyz = np.array([float(q) for q in orientation], dtype=float)  # [w,x,y,z]
        else:
            yaw = float(yaw_deg) if yaw_deg is not None else 0.0
            quat_xyzw = R.from_euler("xyz", [0.0, 0.0, yaw], degrees=True).as_quat()
            quat_wxyz = np.roll(quat_xyzw, 1)

        self._set_obj_prim_pose(target_child_path, local_pos, quat_wxyz)
        print(f"[set_object_pose] requested '{object_name}' at world ({target_world[0]:.3f}, {target_world[1]:.3f}, {target_world[2]:.4f})")
        return target_world

    def sync_real_poses(self):
        """Subscribe to /objects_poses_real and update sim object poses to match."""
        import rclpy
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
        from tf2_msgs.msg import TFMessage

        # Isaac's ROS bridge may already own the default context. Use an independent
        # one so this short-lived subscriber neither initializes nor shuts down it.
        context = Context()
        rclpy.init(args=None, context=context)
        node = None
        executor = None
        try:
            node = rclpy.create_node("_sync_real_poses_tmp", context=context)
            executor = SingleThreadedExecutor(context=context)
            msg_received = [None]

            def _cb(msg):
                msg_received[0] = msg

            sub = node.create_subscription(TFMessage, "/objects_poses_real", _cb, 1)
            # Spin until we get one message (timeout after 3 seconds)
            import time
            t0 = time.time()
            while msg_received[0] is None and (time.time() - t0) < 3.0:
                rclpy.spin_once(node, executor=executor, timeout_sec=0.1)
            node.destroy_subscription(sub)

            if msg_received[0] is None:
                print("[SyncRealPoses] No message received on /objects_poses_real within 3s")
                return {"received": False, "updated": [], "snapped": []}

            # Load assembly data to snap objects near their assembled pose
            import math
            assembly_file = self._assembly_file_path
            assembly_lookup = {}
            if os.path.exists(assembly_file):
                with open(assembly_file, 'r') as f:
                    assembly_data = json.load(f)
                for comp in assembly_data['components']:
                    assembly_lookup[comp['name']] = comp

            # Build dict of real poses by name
            real_poses = {}
            for tf in msg_received[0].transforms:
                t = tf.transform.translation
                r = tf.transform.rotation
                real_poses[tf.child_frame_id] = {
                    'position': (t.x, t.y, t.z),
                    'quat_wxyz': (r.w, r.x, r.y, r.z),
                }

            # Find board's real-world position (used as assembly origin)
            board_real_pos = None
            type_map = self._load_object_type_map()
            for name, pose in real_poses.items():
                if type_map.get(name, 'block') == 'board':
                    board_real_pos = pose['position']
                    break

            # Compute assembled world poses if we have both assembly data and board pose
            assembled_world_poses = {}
            if board_real_pos and assembly_lookup:
                for comp_name, comp in assembly_lookup.items():
                    p = comp['position']
                    q = comp['rotation']['quaternion']
                    assembled_world_poses[comp_name] = {
                        'position': (
                            board_real_pos[0] + p['x'],
                            board_real_pos[1] + p['y'],
                            board_real_pos[2] + p['z'],
                        ),
                        'quat_wxyz': (q['w'], q['x'], q['y'], q['z']),
                    }

            SNAP_THRESHOLD = 0.05  # 5cm — if real pose is within this of assembly pose, snap

            stage = omni.usd.get_context().get_stage()
            updated = []
            snapped = []
            for name, pose in real_poses.items():
                prim_path = self._get_prim_path(name)
                prim = stage.GetPrimAtPath(prim_path)
                if not prim.IsValid():
                    print(f"[SyncRealPoses] Prim not found: {prim_path}")
                    continue

                # Check if close to assembly pose — snap if so
                if name in assembled_world_poses:
                    ap = assembled_world_poses[name]['position']
                    rp = pose['position']
                    dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(ap, rp)))
                    if dist < SNAP_THRESHOLD:
                        self._set_obj_prim_pose(prim_path, ap, assembled_world_poses[name]['quat_wxyz'])
                        snapped.append(name)
                        updated.append(name)
                        continue

                self._set_obj_prim_pose(prim_path, pose['position'], pose['quat_wxyz'])
                updated.append(name)

            print(f"[SyncRealPoses] Updated {len(updated)} objects: {updated}")
            if snapped:
                print(f"[SyncRealPoses] Snapped to assembly pose: {snapped}")
            return {"received": True, "updated": updated, "snapped": snapped}
        finally:
            if executor is not None:
                executor.shutdown()
            if node is not None:
                node.destroy_node()
            rclpy.shutdown(context=context)

    def add_aruco_markers(self, objects_path="/World/Objects"):
        """Add ArUco marker meshes to all objects that have matching aruco JSON files."""
        import omni.kit.commands
        from pxr import Sdf, UsdShade, UsdGeom, Gf

        try:
            ARUCO_DIR = str(resolve_dir("objects/aruco"))
        except AssetMissing as exc:
            print(f"Error: ArUco marker data not available.\n{exc}")
            return
        ARUCO_PNG_DIR = os.path.join(ARUCO_DIR, "pngs")

        stage = omni.usd.get_context().get_stage()
        objects_prim = stage.GetPrimAtPath(objects_path)
        if not objects_prim or not objects_prim.IsValid():
            print(f"Error: {objects_path} not found")
            return

        total_added = 0
        for child in objects_prim.GetChildren():
            obj_name = child.GetName()
            aruco_json = os.path.join(ARUCO_DIR, f"{obj_name}_aruco.json")
            if not os.path.exists(aruco_json):
                continue

            with open(aruco_json, 'r') as f:
                aruco_data = json.load(f)

            # Find the mesh prim (e.g. /World/Objects/base1/base1/base1)
            base_prim_path = self._get_prim_path(obj_name, objects_path)
            base_prim = stage.GetPrimAtPath(base_prim_path)
            if not base_prim or not base_prim.IsValid():
                print(f"Warning: mesh prim not found at {base_prim_path}, skipping")
                continue

            aruco_dict = aruco_data.get('aruco_dictionary', 'DICT_4X4_50')
            dict_name = aruco_dict.replace('DICT_', '').split('_')[0].lower()
            marker_size = aruco_data.get('size', 0.021)

            for marker in aruco_data['markers']:
                aruco_id = marker['aruco_id']
                position = marker['T_object_to_marker']['position']
                rotation = marker['T_object_to_marker']['rotation']

                # Compensate positions for parent prim's scale
                parent_scale_attr = base_prim.GetAttribute("xformOp:scale")
                inv_scale = 1.0 / (parent_scale_attr.Get()[0] if parent_scale_attr and parent_scale_attr.Get() else 1.0)
                scaled_x = position['x'] * inv_scale
                scaled_y = position['y'] * inv_scale
                scaled_z = position['z'] * inv_scale

                cube_prim_path = f"{base_prim_path}/aruco_{aruco_id:03d}"

                # Remove existing marker and recreate
                if stage.GetPrimAtPath(cube_prim_path):
                    stage.RemovePrim(cube_prim_path)

                # Create cube mesh
                omni.kit.commands.execute("CreateMeshPrimCommand",
                                          prim_path=cube_prim_path,
                                          prim_type="Cube")

                cube_prim = stage.GetPrimAtPath(cube_prim_path)
                if not cube_prim:
                    print(f"Error: failed to create cube at {cube_prim_path}")
                    continue

                # Set transforms using existing attributes created by CreateMeshPrimCommand
                cube_prim.GetAttribute("xformOp:translate").Set(Gf.Vec3d(scaled_x, scaled_y, scaled_z))

                rotation_matrix = Gf.Matrix3d(
                    Gf.Rotation(Gf.Vec3d(1, 0, 0), rotation['roll'] * 180.0 / 3.14159) *
                    Gf.Rotation(Gf.Vec3d(0, 1, 0), rotation['pitch'] * 180.0 / 3.14159) *
                    Gf.Rotation(Gf.Vec3d(0, 0, 1), rotation['yaw'] * 180.0 / 3.14159))
                quat = rotation_matrix.ExtractRotation().GetQuat()
                cube_prim.GetAttribute("xformOp:orient").Set(quat)

                # Compensate for parent prim's scale (e.g. 0.01 from Fusion360 export)
                parent_scale_attr = base_prim.GetAttribute("xformOp:scale")
                parent_scale = parent_scale_attr.Get()[0] if parent_scale_attr and parent_scale_attr.Get() else 1.0
                compensated = marker_size / parent_scale
                cube_prim.GetAttribute("xformOp:scale").Set(Gf.Vec3d(compensated, compensated, 0.0001 / parent_scale))

                # Create OmniPBR material, then move it into /World/Objects/Looks
                looks_path = f"{objects_path}/Looks"
                if not stage.GetPrimAtPath(looks_path):
                    UsdGeom.Scope.Define(stage, looks_path)
                out = []
                omni.kit.commands.execute("CreateAndBindMdlMaterialFromLibrary",
                                          mdl_name="OmniPBR.mdl",
                                          mtl_name="OmniPBR",
                                          mtl_created_list=out,
                                          select_new_prim=False)
                if not out:
                    print(f"Error: failed to create material for marker {aruco_id}")
                    continue

                # Move material from /World/Looks/ to /World/Objects/Looks/
                created_path = out[0]
                target_mat_path = f"{looks_path}/aruco_{obj_name}_{aruco_id:03d}"
                omni.kit.commands.execute("MovePrim",
                                          path_from=created_path,
                                          path_to=target_mat_path)

                # Assign aruco texture
                aruco_png = os.path.join(ARUCO_PNG_DIR, f"aruco_marker_{dict_name}_{aruco_id:03d}.png")
                if os.path.exists(aruco_png):
                    shader_prim = stage.GetPrimAtPath(target_mat_path + "/Shader")
                    if shader_prim:
                        texture_attr = shader_prim.CreateAttribute('inputs:diffuse_texture', Sdf.ValueTypeNames.Asset)
                        texture_attr.Set(Sdf.AssetPath(f"file:{aruco_png}"))

                # Bind material to cube
                omni.kit.commands.execute("BindMaterial",
                                          prim_path=cube_prim_path,
                                          material_path=target_mat_path)

                total_added += 1

            print(f"Added {len(aruco_data['markers'])} ArUco markers to {obj_name}")

        print(f"=== ArUco markers complete: {total_added} markers added ===")

    # ── Object Visibility (hide = delete + store, unhide = restore) ────────
    def _get_scene_object_names(self, folder_path="/World/Objects"):
        """Return names of objects currently under /World/Objects (excludes PhysicsMaterial)."""
        stage = omni.usd.get_context().get_stage()
        objects_prim = stage.GetPrimAtPath(folder_path)
        if not objects_prim or not objects_prim.IsValid():
            return []
        return [
            child.GetName() for child in objects_prim.GetChildren()
            if child.IsA(UsdGeom.Xformable) and child.GetName() != "PhysicsMaterial"
        ]

    def _save_all_object_poses(self, folder_path="/World/Objects", exclude=None):
        """Snapshot body poses of all objects under folder_path (excluding *exclude*)."""
        poses = {}
        for name in self._get_scene_object_names(folder_path):
            if name == exclude:
                continue
            body_path = self._get_prim_path(name, folder_path)
            pose = self._read_prim_pose(body_path)
            if pose:
                poses[name] = pose
        return poses

    def _restore_object_poses(self, poses, folder_path="/World/Objects"):
        """Restore active local TRS poses produced by _save_all_object_poses."""
        for name, pose_data in poses.items():
            body_path = self._get_prim_path(name, folder_path)
            if not self._write_prim_pose(body_path, pose_data):
                raise RuntimeError(f"Failed to restore {body_path}; earlier objects may be restored")

    def hide_object(self, object_name, folder_path="/World/Objects"):
        """Hide an object: save its reference path + pose, then delete the prim.

        Stops the simulation, saves poses of all remaining objects, deletes the
        target, restores the remaining poses, and resumes playback.
        """
        stage = omni.usd.get_context().get_stage()

        prim_path = f"{folder_path}/{object_name}"
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            print(f"Object {object_name} not found at {prim_path}")
            return

        # Read the USD reference file path from the root layer spec
        ref_path = None
        prim_spec = stage.GetRootLayer().GetPrimAtPath(prim_path)
        if prim_spec:
            refs = prim_spec.referenceList.prependedItems
            if refs:
                ref_path = refs[0].assetPath
        if not ref_path:
            print(f"Warning: Could not read reference path for {object_name}, unhide will not work")

        # Read the body pose of the object being hidden
        body_prim_path = self._get_prim_path(object_name, folder_path)
        body_pose = self._read_prim_pose(body_prim_path)

        # Determine category for physics re-application on unhide
        type_map = self._load_object_type_map()
        category = type_map.get(object_name, "block")

        # Snapshot poses of all *other* objects before stopping
        other_poses = self._save_all_object_poses(folder_path, exclude=object_name)

        # Stop simulation, delete prim, restore others, resume
        timeline = omni.timeline.get_timeline_interface()
        was_playing = timeline.is_playing()
        if was_playing:
            timeline.stop()

        self._hidden_objects[object_name] = {
            "ref_path": ref_path,
            "body_pose": body_pose,
            "category": category,
        }

        stage.RemovePrim(prim_path)
        print(f"Hidden (deleted) object: {object_name}")

        # Restore other objects to their pre-stop poses and resume
        self._restore_object_poses(other_poses, folder_path)
        if was_playing:
            timeline.play()

    def unhide_object(self, object_name, folder_path="/World/Objects"):
        """Unhide an object: recreate it from saved reference + pose and reapply physics.

        Saves poses of all existing objects, stops simulation, recreates the
        target prim (with physics), restores *all* object poses (including the
        newly-restored one), and resumes playback.
        """
        if object_name not in self._hidden_objects:
            print(f"Object {object_name} is not in the hidden list")
            return

        data = self._hidden_objects[object_name]
        ref_path = data["ref_path"]
        body_pose = data["body_pose"]
        category = data["category"]

        if not ref_path:
            print(f"Error: No reference path saved for {object_name}, cannot restore")
            del self._hidden_objects[object_name]
            return

        stage = omni.usd.get_context().get_stage()

        prim_path = f"{folder_path}/{object_name}"
        if stage.GetPrimAtPath(prim_path).IsValid():
            print(f"Object {object_name} already exists in scene, removing from hidden list")
            del self._hidden_objects[object_name]
            return

        # Snapshot poses of all existing objects before stopping
        other_poses = self._save_all_object_poses(folder_path)

        # Stop simulation
        timeline = omni.timeline.get_timeline_interface()
        was_playing = timeline.is_playing()
        if was_playing:
            timeline.stop()

        # Ensure parent folder exists
        if not stage.GetPrimAtPath(folder_path):
            UsdGeom.Xform.Define(stage, folder_path)

        # Recreate prim with USD reference
        prim = stage.DefinePrim(prim_path)
        prim.GetReferences().AddReference(ref_path)

        # Rename Body1 → object_name (mirrors add_objects logic)
        def _rename_body1(search_prim):
            for child in search_prim.GetAllChildren():
                if child.GetName() == "Body1":
                    new_path = child.GetPath().GetParentPath().AppendChild(object_name)
                    omni.kit.commands.execute("MovePrim", path_from=child.GetPath(), path_to=new_path)
                    return True
                if _rename_body1(child):
                    return True
            return False
        _rename_body1(stage.GetPrimAtPath(prim_path))

        # Rebind physics material if it exists in the scene
        physics_mat_path = f"{folder_path}/PhysicsMaterial"
        if stage.GetPrimAtPath(physics_mat_path).IsValid():
            from omni.physx.scripts import physicsUtils
            physics_mat_sdf_path = Sdf.Path(physics_mat_path)
            obj_prim = stage.GetPrimAtPath(prim_path)
            bound = 0
            for desc in Usd.PrimRange(obj_prim):
                if desc.HasAPI(UsdPhysics.CollisionAPI):
                    physicsUtils.add_physics_material_to_prim(stage, desc, physics_mat_sdf_path)
                    bound += 1
            if bound == 0:
                nested_path = f"{folder_path}/{object_name}/{object_name}"
                nested_prim = stage.GetPrimAtPath(nested_path)
                if nested_prim and nested_prim.IsValid():
                    physicsUtils.add_physics_material_to_prim(stage, nested_prim, physics_mat_sdf_path)

        # Reapply per-category collision / physics settings
        prim_params = self._get_prim_params(category)
        mesh_path = self._get_prim_path(object_name, folder_path)
        mesh_prim = stage.GetPrimAtPath(mesh_path)
        if mesh_prim and mesh_prim.IsValid():
            # Safety net only — the USD is the source of truth. Don't overwrite a USD-baked collider,
            # and don't re-author restOffset/contactOffset/angularDamping (the 2026-06-21 strip moved
            # all object physics to the USD / PhysX defaults). Author a default sdf collider iff the
            # USD lacks one.
            if not mesh_prim.GetAttribute("physics:approximation").HasAuthoredValue():
                approx = prim_params["collision_approximation"]
                UsdPhysics.CollisionAPI.Apply(mesh_prim)
                mesh_collision_api = UsdPhysics.MeshCollisionAPI.Apply(mesh_prim)
                if approx == "sdf":
                    mesh_collision_api.CreateApproximationAttr(PhysxSchema.Tokens.sdf)
                    PhysxSchema.PhysxSDFMeshCollisionAPI.Apply(mesh_prim).CreateSdfResolutionAttr(self._sdf_resolution)
                else:
                    mesh_collision_api.CreateApproximationAttr(approx)

        del self._hidden_objects[object_name]
        print(f"Unhidden (restored) object: {object_name}")

        # Build the full pose set (existing objects + the restored one)
        all_poses = other_poses
        all_poses[object_name] = body_pose

        # Play first, then restore poses while the sim is running — same
        # mechanism as Assemble / Restore State which handles overlapping
        # objects without collision artifacts.
        if was_playing:
            timeline.play()
            async def _restore_after_play():
                app = omni.kit.app.get_app()
                for _ in range(3):
                    await app.next_update_async()
                self._restore_object_poses(all_poses, folder_path)
            asyncio.ensure_future(_restore_after_play())
        else:
            self._restore_object_poses(all_poses, folder_path)

    def delete_objects(self, folder_path="/World/Objects"):
        """Delete the Objects folder from the scene. Stops simulation first to avoid tensor view crash."""
        timeline = omni.timeline.get_timeline_interface()
        was_playing = timeline.is_playing()
        if was_playing:
            timeline.stop()
            print("Stopped simulation before deleting objects")

        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(folder_path)
        if prim and prim.IsValid():
            stage.RemovePrim(folder_path)
            print(f"Deleted {folder_path}")
        else:
            print(f"Warning: {folder_path} does not exist")

        # Also delete the object poses action graph if present
        pose_graph_path = "/Graph/ActionGraph_objects_poses"
        pose_graph_prim = stage.GetPrimAtPath(pose_graph_path)
        if pose_graph_prim and pose_graph_prim.IsValid():
            stage.RemovePrim(pose_graph_path)
            print(f"Deleted {pose_graph_path}")

    def add_objects(self):
        """Import all objects from the selected assembly's local folder into the scene"""
        folder_path = self._objects_folder_path
        if not folder_path:
            print("Error: No folder path for selected assembly")
            return

        print(f"Adding objects from local folder: {folder_path}")

        # DEFEAT THE USD LAYER CACHE before re-referencing the assets. When a referenced
        # <obj>.usd* / <obj>_physics.usd* changes on disk (e.g. a fresh CAD export, or swapping
        # the collider overlay), Sdf keeps the OLD layer in its in-memory registry — SdfLayer.
        # FindOrOpen returns the cached instance, so the AddReference calls below would silently
        # re-compose STALE content (this is the Kit "source changed / pull latest CAD" prompt).
        # delete_objects removes the prim/reference arc but NOT the cached layer, so add_objects
        # alone is not enough. Force-reload every already-cached layer under this assembly folder
        # so the import picks up the current on-disk CAD. This is USD-level only; the stop/play in
        # _cmd_add_objects re-cooks the new collider into PhysX (force-reload ALONE would leave the
        # body with no cooked collider — verified via the omni.physx overlap probe). See CLAUDE.md
        # "USD layer cache" note and .local/scripts/fmb-reload-verify/.
        try:
            from pxr import Sdf
            _folder_rp = os.path.realpath(folder_path)
            for _lyr in Sdf.Layer.GetLoadedLayers():
                if _lyr.realPath and os.path.realpath(_lyr.realPath).startswith(_folder_rp + os.sep):
                    _lyr.Reload(force=True)
        except Exception as _e:
            carb.log_warn(f"add_objects: USD layer force-reload skipped: {_e}")

        # Get the current stage
        stage = omni.usd.get_context().get_stage()

        # Always use /World/Objects as target path
        target_path = "/World/Objects"
        if not stage.GetPrimAtPath(target_path):
            UsdGeom.Xform.Define(stage, target_path)

        # List USD files from local directory. A `<obj>_physics.usd*` sidecar is a PHYSICS OVERLAY
        # (references the geometry asset + authors the collider/rigid-body); it is NOT a separate
        # object. Iterate only the geometry files; the overlay is selected per-object below. This keeps
        # the cad->usd geometry asset collider-free and the collision shape isolated in the overlay.
        all_usd = [f for f in os.listdir(folder_path)
                   if f.endswith(('.usd', '.usda', '.usdc'))]
        overlay_set = {f for f in all_usd if os.path.splitext(f)[0].endswith('_physics')}
        usd_files = sorted([f for f in all_usd if f not in overlay_set])

        if usd_files:
            print(f"Found {len(usd_files)} USD files")

            # Load object type map from assembly file
            type_map = self._load_object_type_map()

            # STRIPPED 2026-06-21: no common physics material. Objects run on PhysX-default friction/
            # restitution — the validated-correct setting (jenga 6/6, fmb2 grasp insertion). The old
            # override (friction 0.1, restitution 0) was unnecessary. Only the sdf collider is authored
            # below; everything else physics falls to PhysX default. Object-specific physics is baked in
            # the USD or in sim when required.

            # Import each USD file with positioning
            for i, usd_file in enumerate(usd_files):
                # Create a child prim under the selected path
                base_name = os.path.splitext(usd_file)[0]
                ext = os.path.splitext(usd_file)[1]

                # Prefer the physics overlay if one exists: it references the geometry asset and
                # carries the collider/rigid-body, so the loaded object = geometry + tuned physics
                # while the cad->usd geometry file stays collision-free. Falls back to the bare
                # geometry file (legacy inline-collider assets keep working unchanged).
                overlay_name = f"{base_name}_physics{ext}"
                ref_file = overlay_name if overlay_name in overlay_set else usd_file
                usd_file_path = "file://" + os.path.join(folder_path, ref_file)

                prim_path = f"{target_path}/{base_name}"

                # Skip if prim already exists
                existing_prim = stage.GetPrimAtPath(prim_path)
                if existing_prim and existing_prim.IsValid():
                    print(f"Skipping {base_name} - already exists at {prim_path}")
                    continue

                # Create a reference to the USD file
                prim = stage.DefinePrim(prim_path)
                references = prim.GetReferences()
                references.AddReference(usd_file_path)
                
                # Calculate position: first at center, then alternating +X and -X
                if i == 0:
                    x_position = 0.0
                else:
                    if i % 2 == 1:  # Odd indices: +X direction
                        x_position = self._object_spacing * ((i + 1) // 2)
                    else:  # Even indices: -X direction
                        x_position = -self._object_spacing * (i // 2)

                # Rename Body1 to match the object name
                def rename_body1_to_object_name(stage, prim_path, object_name):
                    """Recursively search for Body1 and rename it to object_name"""
                    prim = stage.GetPrimAtPath(prim_path)
                    if not prim:
                        return False
                    
                    # Search for Body1 in the hierarchy
                    for child in prim.GetAllChildren():
                        if child.GetName() == "Body1":
                            # Found Body1, rename it
                            new_path = child.GetPath().GetParentPath().AppendChild(object_name)
                            omni.kit.commands.execute('MovePrim',
                                path_from=child.GetPath(),
                                path_to=new_path)
                            print(f"Renamed Body1 to {object_name} at {new_path}")
                            return True
                        # Recursively search in children
                        if rename_body1_to_object_name(stage, child.GetPath(), object_name):
                            return True
                    return False
                
                # Try to rename Body1 to the object name
                rename_body1_to_object_name(stage, prim_path, base_name)

                # Apply position to the final body prim: {name}/{name}/{name}
                # Apply position to the final body prim without clearing inherited xform ops
                body_path = f"{prim_path}/{base_name}/{base_name}"
                body_prim = stage.GetPrimAtPath(body_path)
                if body_prim and body_prim.IsValid():
                    omni.kit.commands.execute('ChangeProperty',
                        prop_path=f"{body_path}.xformOp:translate",
                        value=Gf.Vec3d(x_position, self._y_offset, self._z_offset),
                        prev=None)
                    print(f"Added {usd_file_path} to {body_path} at position ({x_position}, {self._y_offset}, {self._z_offset})")
                else:
                    # Simple (non-nested) USD — e.g. the directly-built meters-native jenga blocks
                    # (Xform -> Mesh, mesh centred at origin, no baked xformOp:translate). The triple
                    # nested {name}/{name}/{name} body prim doesn't exist, so author the translate op
                    # on the top prim directly. (A bare ChangeProperty here sets the attribute value but
                    # never adds it to xformOpOrder, so the transform is ignored and objects pile at the
                    # origin — that was the jenga "all at (0,0,0)" bug.)
                    print(f"Simple-structure USD at {prim_path}; authoring translate op on the top prim")
                    parent_xf = UsdGeom.Xformable(stage.GetPrimAtPath(prim_path))
                    t_ops = [op for op in parent_xf.GetOrderedXformOps()
                             if op.GetOpType() == UsdGeom.XformOp.TypeTranslate]
                    if t_ops:
                        t_ops[0].Set(Gf.Vec3d(x_position, self._y_offset, self._z_offset))
                    else:
                        parent_xf.AddTranslateOp().Set(Gf.Vec3d(x_position, self._y_offset, self._z_offset))
                    print(f"Added {usd_file_path} to {prim_path} at position ({x_position}, {self._y_offset}, {self._z_offset})")

            # STRIPPED 2026-06-21: author ONLY the per-category sdf collider — NO physics material,
            # restOffset/contactOffset, or angularDamping. Objects run on PhysX defaults; that IS the
            # validated-correct setting (jenga 6/6, fmb2 grasp insertion). The old overrides
            # (friction 0.1, restOffset -0.5, angularDamping 30/50) were unnecessary, restOffset -0.5 was
            # harmful (L2-yaw), and the high damping MASKED the physically-correct tip-over of small
            # standing parts (a hex on its tiny footprint SHOULD fail an unsupported place-down -> use a
            # workspace fixture, not damping). Anything object-specific is baked in the USD (aruco's
            # cad->usd stripped export) or in sim when required. NB this collider-apply is a safety net;
            # the stripped USDs already bake the sdf collider + rigid body.
            objects_prim = stage.GetPrimAtPath(target_path)
            if objects_prim.IsValid():
                for child in objects_prim.GetChildren():
                    child_name = child.GetName()
                    if child_name == "PhysicsMaterial":
                        continue
                    category = type_map.get(child_name, 'block')
                    mesh_path = self._get_prim_path(child_name, target_path)
                    mesh_prim = stage.GetPrimAtPath(mesh_path)
                    if mesh_prim and mesh_prim.IsValid():
                        # SAFETY NET ONLY. The stripped cad->usd USDs bake their own sdf collider +
                        # resolution; the USD is the source of truth. Do NOT overwrite a USD-authored
                        # collider (re-authoring sdfResolution here is what silently clobbered base3's
                        # USD-set 512 back to 256). Author a default collider ONLY if the USD lacks one.
                        if mesh_prim.GetAttribute("physics:approximation").HasAuthoredValue():
                            print(f"  collider on {mesh_path} [{category}] = USD-baked, left untouched")
                        else:
                            approx = self._get_prim_params(category)["collision_approximation"]
                            UsdPhysics.CollisionAPI.Apply(mesh_prim)
                            mc = UsdPhysics.MeshCollisionAPI.Apply(mesh_prim)
                            if approx == "sdf":
                                mc.CreateApproximationAttr(PhysxSchema.Tokens.sdf)
                                PhysxSchema.PhysxSDFMeshCollisionAPI.Apply(mesh_prim).CreateSdfResolutionAttr(self._sdf_resolution)
                            else:
                                mc.CreateApproximationAttr(approx)
                            print(f"  {approx} collider AUTHORED on {mesh_path} [{category}] (USD had none)")
                    else:
                        print(f"  Warning: Mesh prim not found at {mesh_path}")
        else:
            print(f"No USD files found in {folder_path}")

    def create_pose_publisher(self):
        """Create action graph for publishing object poses to ROS2"""
        import omni.kit.commands
        from pxr import Sdf, Usd, UsdGeom
        import omni.usd
        import omni.graph.core as og

        def get_objects_in_folder(stage, folder_path="/World/Objects"):
            """
            Scan the Objects folder and find all prims following the pattern:
            /World/Objects/{object_name}/{object_name}/{object_name}
            
            Args:
                stage: USD Stage
                folder_path: Path to the Objects folder
            
            Returns:
                List of paths to object prims
            """
            body_paths = []
            
            # Get the Objects prim
            objects_prim = stage.GetPrimAtPath(folder_path)
            
            if not objects_prim.IsValid():
                print(f"Warning: {folder_path} does not exist")
                return body_paths
            
            # Iterate through children (e.g., fork_orange, base, fork_yellow, line_brown)
            for child in objects_prim.GetChildren():
                object_name = child.GetName()
                
                # Look for the nested structure: {object_name}/{object_name}/object_name{}
                nested_path = f"{folder_path}/{object_name}/{object_name}/{object_name}"
                nested_prim = stage.GetPrimAtPath(nested_path)
                
                if nested_prim.IsValid():
                    body_paths.append(nested_path)
                    print(f"Found: {nested_path}")
            
            return body_paths

        def create_action_graph_with_transforms(target_prims, parent_prim="/World", topic_name="objects_poses_sim"):
            """
            Create an action graph with OnPlaybackTick and ROS2PublishTransformTree nodes
            
            Args:
                target_prims: List of prim paths to publish transforms for
                parent_prim: Parent prim for the transform tree
                topic_name: ROS2 topic name
            """
            
            graph_path = "/Graph/ActionGraph_objects_poses"

            # Check if graph already exists
            stage = omni.usd.get_context().get_stage()
            existing_graph = stage.GetPrimAtPath(graph_path)
            if existing_graph and existing_graph.IsValid():
                print(f"Action graph already exists at {graph_path}, skipping creation")
                return

            # Create the action graph using OmniGraph API
            keys = og.Controller.Keys
            legacy_tf = _use_legacy_tf_publisher(og)
            tf_nodes = [] if legacy_tf else [("compute_transform_tree", "isaacsim.core.nodes.IsaacComputeTransformTree")]
            tf_connections = [("on_playback_tick.outputs:tick", "ros2_publish_transform_tree.inputs:execIn")] if legacy_tf else [
                ("on_playback_tick.outputs:tick", "compute_transform_tree.inputs:execIn"),
                ("compute_transform_tree.outputs:execOut", "ros2_publish_transform_tree.inputs:execIn"),
                ("compute_transform_tree.outputs:parentFrames", "ros2_publish_transform_tree.inputs:parentFrames"),
                ("compute_transform_tree.outputs:childFrames", "ros2_publish_transform_tree.inputs:childFrames"),
                ("compute_transform_tree.outputs:translations", "ros2_publish_transform_tree.inputs:translations"),
                ("compute_transform_tree.outputs:orientations", "ros2_publish_transform_tree.inputs:orientations"),
            ]
            
            (graph, nodes, _, _) = og.Controller.edit(
                {
                    "graph_path": graph_path,
                    "evaluator_name": "execution",
                },
                {
                    keys.CREATE_NODES: [
                        ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
                        ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
                        ("isaac_read_simulation_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
                        ("ros2_publish_transform_tree", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
                    ] + tf_nodes,
                    keys.CONNECT: [
                        ("ros2_context.outputs:context", "ros2_publish_transform_tree.inputs:context"),
                        ("isaac_read_simulation_time.outputs:simulationTime", "ros2_publish_transform_tree.inputs:timeStamp"),
                    ] + tf_connections,
                    keys.SET_VALUES: [
                        ("ros2_publish_transform_tree.inputs:topicName", topic_name),
                        ("isaac_read_simulation_time.inputs:resetOnStop", False),
                    ],
                },
            )
            
            # Get the stage to set relationships
            stage = omni.usd.get_context().get_stage()
            
            # 6.0.0.0 resolves targets in the publisher; newer releases use the compute node.
            node_name = "ros2_publish_transform_tree" if legacy_tf else "compute_transform_tree"
            ros2_node_path = f"{graph_path}/{node_name}"
            ros2_prim = stage.GetPrimAtPath(ros2_node_path)
            
            if ros2_prim.IsValid():
                # Set parent prim as a relationship
                parent_rel = ros2_prim.GetRelationship("inputs:parentPrim")
                if not parent_rel:
                    parent_rel = ros2_prim.CreateRelationship("inputs:parentPrim", custom=True)
                parent_rel.SetTargets([Sdf.Path(parent_prim)])
                
                # Set target prims as a relationship
                target_rel = ros2_prim.GetRelationship("inputs:targetPrims")
                if not target_rel:
                    target_rel = ros2_prim.CreateRelationship("inputs:targetPrims", custom=True)
                target_paths = [Sdf.Path(path) for path in target_prims]
                target_rel.SetTargets(target_paths)
                
                print(f"Action graph created at {graph_path}")
                print(f"Publishing {len(target_prims)} objects to topic: {topic_name}")
            else:
                print(f"Error: Could not find ROS2 node at {ros2_node_path}")

        # Get the current USD stage
        stage = omni.usd.get_context().get_stage()
        
        if not stage:
            print("Error: No stage found")
            return
        
        # Get all Body1 prims from the Objects folder
        object_paths = get_objects_in_folder(stage, "/World/Objects")
        
        if not object_paths:
            print("No objects found in /World/Objects")
            return
        
        print(f"\nFound {len(object_paths)} objects:")
        for path in object_paths:
            print(f"  - {path}")
        
        # Create the action graph with all found objects
        create_action_graph_with_transforms(
            target_prims=object_paths,
            parent_prim="/World",
            topic_name="objects_poses_sim"
        )

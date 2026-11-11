"""The twin's tool handlers (`_cmd_<tool>`), called through the bridge."""

import omni.usd
import asyncio
import os
import carb
import json
import traceback
from typing import Dict, Any
from .asset_cache import asset_source
from .object_pose import read_local_pose, set_local_pose
from .world_compat import twin_play_edit
from .physics_compat import selected_engine, prepare_saved_twin_runtime
from .settings import ASSEMBLIES, RESOURCES_DIR, WORKSTATION_USD, _failure_detail, _last_error_line
from .capture_state import _viewport_pub


class Commands:
    """Part of DigitalTwin (extension.py), which mixes it in."""

    # ==================== Common MCP Handlers ====================
    async def _cmd_play_scene(self) -> Dict[str, Any]:
        """Start/resume the simulation timeline."""
        try:
            import omni.timeline

            timeline = omni.timeline.get_timeline_interface()
            stage = omni.usd.get_context().get_stage()
            twin = stage is not None and (
                stage.GetPrimAtPath("/Graph/ActionGraph_RG2") or
                stage.GetPrimAtPath("/Graph/ActionGraph_UR5e_ForcePublish"))
            from contextlib import nullcontext
            scope = (twin_play_edit(stage, omni.usd.get_context().get_stage)
                     if twin and selected_engine() == "newton" else nullcontext(lambda: None))
            with scope as validate:
                if twin:
                    if (selected_engine() == "newton"
                            and stage.GetPrimAtPath("/Graph/ActionGraph_UR5e_ForcePublish")
                            and not stage.GetPrimAtPath("/Graph/ActionGraph_UR5e_ForcePublish/valid")):
                        raise RuntimeError("Saved Newton force graph has no validity gate. Use a new stage and Quick Start; existing graph was not rebuilt")
                    prepare_saved_twin_runtime(stage, timeline)
                    self._resubscribe_physics_callbacks()
                timeline.play()
                # 5.1: wait for PhysX warmup after play so cooking completes before any
                # subsequent stage mutation (setup_pose_publisher / randomize) runs.
                # Replaying a scene with the heavier fmb2/fmb3 bodies otherwise wedges
                # the kit main thread mid-cook. See SKILL.md 5.1 warmup addendum.
                try:
                    from isaacsim.core.simulation_manager import SimulationManager
                    for _ in range(900):  # up to ~15s at 60Hz
                        await asyncio.sleep(1.0 / 60.0)
                        validate()
                        if omni.usd.get_context().get_stage() != stage:
                            raise RuntimeError("Stage changed during Play; inspect the current stage")
                        if not SimulationManager._warmup_needed:
                            break
                    if SimulationManager._warmup_needed:
                        raise RuntimeError("Physics warm-up did not complete; inspect native errors before retrying")
                    if (twin and selected_engine() == "newton"
                            and SimulationManager.get_physics_simulation_view() is None):
                        raise RuntimeError("Newton tensor view is unavailable after Play; inspect native errors")
                except ImportError:
                    await asyncio.sleep(3.0)
                    validate()
                if omni.usd.get_context().get_stage() != stage or not timeline.is_playing():
                    raise RuntimeError("Requested Play was not retained on the selected stage")
            return {
                "status": "success",
                "message": "Simulation started",
                "force_publisher": getattr(self, "_force_publish_status", None) if twin else None,
            }
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Failed to start simulation: {str(e)}",
                "effects": "Runtime initialization or Play may have partially applied; inspect the selected stage, timeline and native log before retrying"
            }

    def _cmd_stop_scene(self) -> Dict[str, Any]:
        """Stop the simulation timeline."""
        try:
            import omni.timeline
            timeline = omni.timeline.get_timeline_interface()
            timeline.stop()
            return {
                "status": "success",
                "message": "Simulation stopped"
            }
        except Exception as e:
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Failed to stop simulation: {str(e)}"
            }

    # ==================== Scene Management Handlers ====================
    def _get_scene_state_dir(self) -> str:
        """Get the directory for scene state JSON files.

        Uses the output dir pushed by MCP server, or falls back to default resources dir.
        """
        if self._output_dir:
            scene_dir = os.path.join(self._output_dir, "resources", "scene_states")
        else:
            scene_dir = os.path.join(RESOURCES_DIR, "scene_states")
        os.makedirs(scene_dir, exist_ok=True)
        return scene_dir

    def _get_latest_scene_state_path(self) -> str:
        """Find the most recent scene state file by timestamp in the filename."""
        scene_dir = self._get_scene_state_dir()
        import glob as glob_mod
        files = glob_mod.glob(os.path.join(scene_dir, "scene_state_*.json"))
        if not files:
            return None
        files.sort()
        return files[-1]

    def _get_prim_path(self, object_name: str, folder_path: str = "/World/Objects") -> str:
        """Get the full nested prim path for an object name (folder/{name}/{name}/{name})."""
        return f"{folder_path}/{object_name}/{object_name}/{object_name}"

    def _resolve_output_dir(self, output_dir=None):
        """Update internal output_dir if provided by MCP server."""
        if output_dir:
            self._output_dir = os.path.abspath(output_dir)

    def _read_prim_pose(self, prim_path: str) -> Dict[str, Any]:
        """Read the composed local TRS pose, including Euler-rotation assets."""
        try:
            stage = omni.usd.get_context().get_stage()
            return read_local_pose(stage.GetPrimAtPath(prim_path))
        except Exception as e:
            carb.log_error(f"Error reading pose for {prim_path}: {e}")
            return None

    def _write_prim_pose(self, prim_path: str, pose_data: Dict[str, Any]) -> bool:
        """Restore an active local TRS pose without changing referenced source files."""
        try:
            pos = pose_data.get("position", {"x": 0.0, "y": 0.0, "z": 0.0})
            quat = pose_data.get("quaternion", {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0})
            scale = pose_data.get("scale", {"x": 1.0, "y": 1.0, "z": 1.0})
            stage = omni.usd.get_context().get_stage()
            set_local_pose(stage.GetPrimAtPath(prim_path),
                           [pos[k] for k in "xyz"], [quat[k] for k in "wxyz"],
                           [scale[k] for k in "xyz"])
            return True
        except Exception as e:
            carb.log_error(f"Error writing pose for {prim_path}: {e}")
            traceback.print_exc()
            return False

    def _cmd_assemble_objects(self, assembly: str = "fmb1") -> Dict[str, Any]:
        """MCP handler for assembling objects."""
        try:
            if assembly not in ASSEMBLIES:
                return {
                    "status": "error",
                    "message": f"Invalid assembly: {assembly}. Must be one of: {', '.join(ASSEMBLIES.keys())}"
                }

            self._selected_assembly = assembly
            assembly_file = self._assembly_file_path
            if not os.path.exists(assembly_file):
                return {
                    "status": "error",
                    "message": f"Assembly file not found: {assembly_file or 'layout is not in the asset folder (run isaac_assets.py doctor)'}"
                }

            assembled, skipped = self.assemble_objects()
            msg = f"Assembled {assembled} objects ({assembly})"
            if skipped:
                msg += f", {skipped} skipped (prim not found)"
            if not assembled:
                return {"status": "error", "message": msg + ". Call ur5e_dt__add_objects with this assembly first."}
            return {"status": "success", "message": msg}
        except Exception as e:
            traceback.print_exc()
            return {"status": "error", "message": f"Failed to assemble objects: {str(e)}"}

    def _cmd_randomize_object_poses(self) -> Dict[str, Any]:
        """MCP handler for randomizing object poses."""
        try:
            randomized, boards_skipped = self.randomize_object_poses()
            return {
                "status": "success",
                "message": f"Randomized {randomized} objects, {boards_skipped} boards skipped"
            }
        except Exception as e:
            traceback.print_exc()
            return {"status": "error", "message": f"Failed to randomize object poses: {str(e)}"}

    def _cmd_randomize_single_object(self, object_name: str = None) -> Dict[str, Any]:
        """MCP handler for randomizing a single object's pose."""
        if not object_name:
            return {"status": "error", "message": "object_name is required"}
        try:
            self.randomize_single_object(object_name)
            return {
                "status": "success",
                "message": f"Randomized '{object_name}' in clear workspace area"
            }
        except Exception as e:
            traceback.print_exc()
            return {"status": "error", "message": f"Failed to randomize '{object_name}': {str(e)}"}

    def _cmd_set_object_pose(self, object_name: str = None, position=None,
                             orientation=None, yaw_deg=None) -> Dict[str, Any]:
        """MCP handler: teleport an object under /World/Objects to an exact world pose."""
        if not object_name:
            return {"status": "error", "message": "object_name is required"}
        if position is None or len(position) != 3:
            return {"status": "error", "message": "position [x, y, z] (meters, world) is required"}
        try:
            world = self.set_object_pose(object_name, position, orientation=orientation, yaw_deg=yaw_deg)
            return {
                "status": "success",
                "message": f"Requested '{object_name}' at world ({float(world[0]):.3f}, {float(world[1]):.3f}, "
                           f"{float(world[2]):.4f}); physics settles it from there, so read the pose again to see "
                           "where it ended up",
                "requested_world": [float(world[0]), float(world[1]), float(world[2])],
            }
        except Exception as e:
            traceback.print_exc()
            return {"status": "error", "message": f"Failed to set pose for '{object_name}': {str(e)}"}

    def _cmd_save_scene_state(self, json_file_path: str = None, output_dir: str = None) -> Dict[str, Any]:
        """Save scene state (object poses) to a JSON file.

        If json_file_path is provided, saves to that filename inside scene_states dir.
        Otherwise creates a timestamped file.

        Args:
            json_file_path: Optional filename (e.g. 'assembled.json'). If omitted, timestamped.
            output_dir: Optional output directory pushed by MCP server.

        Returns:
            Dictionary with execution result.
        """
        try:
            from pxr import UsdGeom
            from datetime import datetime

            self._resolve_output_dir(output_dir)
            scene_dir = self._get_scene_state_dir()
            if json_file_path:
                if not json_file_path.endswith(".json"):
                    json_file_path += ".json"
                save_path = os.path.join(scene_dir, os.path.basename(json_file_path))
            else:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                save_path = os.path.join(scene_dir, f"scene_state_{timestamp}.json")

            stage = omni.usd.get_context().get_stage()
            if not stage:
                return {
                    "status": "error",
                    "message": "No stage is currently open"
                }

            objects_prim = stage.GetPrimAtPath("/World/Objects")
            if not objects_prim.IsValid():
                return {
                    "status": "error",
                    "message": "/World/Objects path does not exist"
                }

            children = objects_prim.GetChildren()
            object_names = [child.GetName() for child in children if child.IsA(UsdGeom.Xformable)]

            if not object_names:
                return {
                    "status": "error",
                    "message": "No objects found in /World/Objects"
                }

            poses = {}
            saved_count = 0
            failed_names = []

            for object_name in object_names:
                prim_path = self._get_prim_path(object_name)
                pose = self._read_prim_pose(prim_path)
                if pose:
                    poses[object_name] = pose
                    saved_count += 1
                else:
                    failed_names.append(object_name)

            with open(save_path, 'w') as f:
                json.dump(poses, f, indent=4)

            abs_path = os.path.abspath(save_path)
            msg = f"[save_state] Saved {saved_count} objects to {abs_path}"
            if failed_names:
                msg += f", {len(failed_names)} failed: {failed_names}"
            print(msg)

            return {
                "status": "success",
                "message": msg,
                "saved_count": saved_count,
                "failed_names": failed_names,
                "json_file_path": save_path
            }

        except Exception as e:
            print(f"[save_state] Error: {str(e)}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Failed to save scene state: {str(e)}",
                "traceback": traceback.format_exc()
            }

    def _cmd_restore_scene_state(self, json_file_path: str = None, output_dir: str = None) -> Dict[str, Any]:
        """Restore scene state (object poses) from a JSON file.

        If json_file_path is provided, loads that specific file from scene_states dir.
        Otherwise finds the most recent timestamped save.

        Args:
            json_file_path: Optional filename (e.g. 'assembled.json'). If omitted, uses latest.
            output_dir: Optional output directory pushed by MCP server.

        Returns:
            Dictionary with execution result.
        """
        try:
            self._resolve_output_dir(output_dir)

            if json_file_path:
                if not json_file_path.endswith(".json"):
                    json_file_path += ".json"
                restore_path = os.path.join(self._get_scene_state_dir(), os.path.basename(json_file_path))
                if not os.path.exists(restore_path):
                    return {
                        "status": "error",
                        "message": f"Scene state file not found: {restore_path}"
                    }
                latest_path = restore_path
            else:
                latest_path = self._get_latest_scene_state_path()

            if not latest_path:
                return {
                    "status": "error",
                    "message": f"No scene state files found in {self._get_scene_state_dir()}"
                }

            try:
                with open(latest_path, 'r') as f:
                    poses = json.load(f)
            except json.JSONDecodeError as e:
                return {
                    "status": "error",
                    "message": f"Invalid JSON file: {str(e)}"
                }

            if not poses:
                return {
                    "status": "error",
                    "message": "No objects found in scene state file"
                }

            restored_count = 0
            failed_names = []

            for object_name, pose_data in poses.items():
                prim_path = self._get_prim_path(object_name)
                if self._write_prim_pose(prim_path, pose_data):
                    restored_count += 1
                else:
                    failed_names.append(object_name)

            msg = f"[restore_state] Restored {restored_count} objects from {latest_path}"
            if failed_names:
                msg += f", {len(failed_names)} failed: {failed_names}"
            print(msg)

            return {
                "status": "success",
                "message": msg,
                "restored_count": restored_count,
                "failed_names": failed_names,
                "json_file_path": latest_path
            }

        except Exception as e:
            print(f"[restore_state] Error: {str(e)}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Failed to restore scene state: {str(e)}",
                "traceback": traceback.format_exc()
            }

    async def _cmd_add_objects(self, assembly: str = "fmb1") -> Dict[str, Any]:
        """MCP handler for adding objects to the scene.

        Args:
            assembly: Which assembly to load (fmb1 through fmb4; jenga/fmb_jenga alias fmb4)

        Returns:
            Dictionary with execution result.
        """
        try:
            if assembly not in ASSEMBLIES:
                return {
                    "status": "error",
                    "message": f"Invalid assembly: {assembly}. Must be one of: {', '.join(ASSEMBLIES.keys())}"
                }

            self._selected_assembly = assembly

            # Disable UJITSO multiprocess collision cooking for the object cook (root-caused
            # 2026-06-21). UJITSO (on by default) cooks collision meshes in FORKED subprocesses;
            # forking from the live, heavily-multithreaded Kit app (MCP socket-server thread +
            # render + physics) deadlocks in carb.tasking -> omni.physx.cooking — the long-standing
            # "add_objects wedge". The IDENTICAL SDF-256 mesh cooks in ~1.3s in a standalone
            # SimulationApp (clean process, fork OK) and over the socket once UJITSO is off (cook
            # runs in-process). This is robot-INDEPENDENT (a single block wedges with no robot) and
            # keeps the accurate baked SDF collider — no mesh downgrade, no prebake needed. The
            # setting is non-persistent (resets each app reboot) so we set it here per call. We do
            # NOT touch the cached robot load (already cooked into DerivedDataCache by quick_start).
            try:
                import carb.settings
                carb.settings.get_settings().set_bool(
                    "/physics/cooking/ujitsoCollisionCooking", False)
            except Exception as _e:
                carb.log_warn(f"add_objects: could not disable UJITSO cooking: {_e}")

            self.add_objects()

            # Rebuild PhysX tensor views: adding rigid bodies to a PLAYING sim
            # invalidates the simulationView, after which parts slip out of the
            # gripper mid-motion. Stop+play re-registers every body. See the
            # isaac-sim-extension-dev usd-and-physics.md remedy: "Stop and restart
            # the simulation to rebuild physics tensor views". Verified via the
            # ground-truth FMB1 replay (4/4 seated) once this re-init was in place.
            import omni.timeline, omni.kit.app
            _tl = omni.timeline.get_timeline_interface()
            _app = omni.kit.app.get_app()
            _tl.stop()
            await _app.next_update_async()
            _tl.play()
            await _app.next_update_async()

            # Isaac 5.1: collider cooking continues in the BACKGROUND after play(). Returning (or any
            # further stage mutation) while a cook is in flight can wedge the Kit main thread — wait
            # for it to finish. With UJITSO disabled above the cook runs in-process and completes fast
            # even for 6x jenga SDF-256 (verified 2026-06-21: add_objects('fmb4') returns in ~2s with
            # the robot present, blocks settle, socket stays live). The earlier "heavy SDF wedge" was
            # NOT a cook-cost cliff and NOT articulation contention — it was UJITSO forking subprocesses
            # from the live multithreaded Kit app (see the disable above).
            try:
                from isaacsim.core.simulation_manager import SimulationManager
                for _ in range(1800):  # up to ~30s at 60Hz — generous safety margin
                    await _app.next_update_async()
                    if not SimulationManager._warmup_needed:
                        break
            except Exception:
                import asyncio
                await asyncio.sleep(2.0)  # blind fallback if the warmup flag is unavailable

            return {
                "status": "success",
                "message": f"Objects added from {assembly} (physics views rebuilt)",
                "folder_path": self._objects_folder_path,
                "objects": [child.GetName() for child in omni.usd.get_context().get_stage()
                            .GetPrimAtPath("/World/Objects").GetChildren()],
            }
        except Exception as e:
            carb.log_error(f"Error in add_objects: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Failed to add objects: {str(e)}",
                **_failure_detail(e)
            }

    def _cmd_delete_objects(self) -> Dict[str, Any]:
        """MCP handler for deleting objects from the scene.

        Returns:
            Dictionary with execution result.
        """
        try:
            self.delete_objects()
            return {
                "status": "success",
                "message": "Objects deleted from /World/Objects"
            }
        except Exception as e:
            carb.log_error(f"Error in delete_objects: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Failed to delete objects: {str(e)}",
                "traceback": traceback.format_exc()
            }

    async def _cmd_new_stage(self) -> Dict[str, Any]:
        """MCP handler: clear the entire USD stage to free accumulated memory."""
        try:
            import os, gc

            # Measure memory before
            with open(f"/proc/{os.getpid()}/status") as f:
                for line in f:
                    if line.startswith("VmRSS"):
                        rss_before = int(line.split()[1]) // 1024  # MB

            # Stop viewport publishers before stage teardown
            _viewport_pub.stop_all()

            # Stop physics callbacks to avoid crashes during teardown
            self._stop_physics_callback(
                "_gripper_physx_sub", "_gripper_articulation",
                "_gripper_publish_active", "_gripper_warmup_remaining"
            )
            self._stop_physics_callback(
                "_force_physx_sub", "_force_articulation",
                "_force_publish_active", "_force_warmup_remaining"
            )

            # Stop simulation
            timeline = omni.timeline.get_timeline_interface()
            if not timeline.is_stopped():
                timeline.stop()
            app = omni.kit.app.get_app()
            await app.next_update_async()

            # Clear Python-side caches
            if self._mcp is not None:
                self._mcp.clear_sessions()
            self._hidden_objects.clear()

            # Clear extension instance references to old stage objects
            for attr in ['_ur5e_view', '_articulation', '_gripper_articulation',
                         '_force_articulation', '_gripper_pub_node', '_force_pub_node']:
                if hasattr(self, attr):
                    setattr(self, attr, None)

            # Clear the World singleton — releases physics context, articulation views,
            # and render callbacks tied to the old stage. Without this, World.instance()
            # returns a stale World after new_stage, causing FPS degradation.
            from isaacsim.core.api.world import World
            world = World.instance()
            if world is not None:
                try:
                    world.clear_all_callbacks()
                    # The entire stage is about to close. World.clear() calls
                    # synchronous clear_stage()/app.update() and can dispatch a
                    # viewport redraw while its selected camera is being deleted.
                    world.scene.clear(registry_only=True)
                except Exception as e:
                    print(f"[new_stage] World.clear warning: {e}")
                try:
                    World.clear_instance()
                except Exception as e:
                    print(f"[new_stage] clear_instance warning: {e}")

            # Open a fresh stage
            await omni.usd.get_context().new_stage_async()

            # Let Kit process the stage teardown
            app = omni.kit.app.get_app()
            for _ in range(5):
                await app.next_update_async()

            # Force Python GC
            gc.collect()

            # Measure memory after
            with open(f"/proc/{os.getpid()}/status") as f:
                for line in f:
                    if line.startswith("VmRSS"):
                        rss_after = int(line.split()[1]) // 1024  # MB

            freed_mb = max(0, rss_before - rss_after)
            return {
                "status": "success",
                "message": f"Stage cleared. Freed {freed_mb} MB (RSS: {rss_before} MB -> {rss_after} MB).",
                "rss_before_mb": rss_before,
                "rss_after_mb": rss_after,
                "freed_mb": freed_mb,
            }
        except Exception as e:
            carb.log_error(f"Error in new_stage: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Failed to create new stage: {str(e)}",
                "traceback": traceback.format_exc()
            }

    def _quick_start_report(self) -> Dict[str, Any]:
        """What the last Quick Start used: whether the gripper loaded and each asset's source."""
        stage = omni.usd.get_context().get_stage()
        return {"gripper": self._quick_start_gripper, "assets": self._quick_start_assets,
                "robot": {"path": "/World/UR5e", "articulation_root": "/World/UR5e/root_joint"},
                "camera": "/World/workspace_camera_sim",
                "playing": self._timeline.is_playing(),
                **({"workstation": asset_source(WORKSTATION_USD)["source"]}
                   if stage and stage.GetPrimAtPath("/World/Vention").IsValid() else {})}

    def _bench_label(self) -> str:
        if asset_source(WORKSTATION_USD)["source"] == "missing":
            return "a placeholder bench (no Vention workstation asset)"
        return "the Vention workstation"

    async def _cmd_quick_start(self) -> Dict[str, Any]:
        """MCP handler: full scene setup (ground, UR5e, gripper if present, camera, play). No table: see load_workstation."""
        try:
            await self.quick_start()
            return {
                "status": "success",
                "message": "Quick start complete: scene loaded with UR5e"
                           + (", RG2 gripper" if self._quick_start_gripper["loaded"] else " (no gripper)")
                           + ", workspace camera, and simulation running.",
                **self._quick_start_report(),
                "force_publisher": getattr(self, "_force_publish_status", {"status": "not_configured"}),
            }
        except Exception as e:
            carb.log_error(f"Error in quick_start: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Quick start failed: {str(e)}",
                **_failure_detail(e)
            }

    async def _cmd_setup_camera_tf_graph(self) -> Dict[str, Any]:
        """MCP handler: same calls as the panel buttons Import RealSense Camera / Attach Camera / camera action graph."""
        try:
            await self._wait_for_stage()
            stage = omni.usd.get_context().get_stage()
            if not stage.GetPrimAtPath("/World/UR5e/wrist_3_link").IsValid():
                return {"status": "error", "message": "UR5e not loaded: run quick_start_with_workstation first"}
            if not stage.GetPrimAtPath("/World/rsd455").IsValid() and not stage.GetPrimAtPath("/World/UR5e/wrist_3_link/rsd455").IsValid():
                self.import_realsense_camera()
            self.attach_camera_to_ur5e()
            self.setup_camera_action_graph()
            return {
                "status": "success",
                "message": "Camera attached; publishing the camera topics and /tf_cam once the timeline plays.",
                "graph": "/Graph/ActionGraph_Camera",
            }
        except Exception as e:
            carb.log_error(f"Error in setup_camera_tf_graph: {e}")
            return {"status": "error", "message": f"setup_camera_tf_graph failed: {str(e)}", "traceback": traceback.format_exc()}

    async def _cmd_load_workstation(self) -> Dict[str, Any]:
        """MCP handler: add the Vention workstation bench (visual, tabletop top at z=0)."""
        try:
            await self.load_workstation()
            return {
                "status": "success",
                "message": f"Loaded {self._bench_label()} at /World/Vention (tabletop top at z=0; robot and object physics unchanged; ground plane visual hidden)."
            }
        except Exception as e:
            carb.log_error(f"Error in load_workstation: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Load workstation failed: {str(e)}",
                **_failure_detail(e)
            }

    async def _cmd_quick_start_with_workstation(self) -> Dict[str, Any]:
        """MCP handler: quick_start, then load the Vention workstation bench."""
        try:
            await self.quick_start_with_workstation()
            return {
                "status": "success",
                "message": f"Quick start complete with {self._bench_label()} loaded.",
                **self._quick_start_report(),
                "force_publisher": getattr(self, "_force_publish_status", {"status": "not_configured"}),
            }
        except Exception as e:
            carb.log_error(f"Error in quick_start_with_workstation: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Quick start with workstation failed: {str(e)}",
                **_failure_detail(e)
            }

    async def _cmd_load_simple_room(self) -> Dict[str, Any]:
        """MCP handler: add Isaac's Simple Room backdrop at z=-0.11."""
        try:
            await self.load_simple_room()
            return {
                "status": "success",
                "message": "Simple Room loaded at /World/SimpleRoom (z=-0.11; robot/object physics unchanged; ground plane visual hidden)."
            }
        except Exception as e:
            carb.log_error(f"Error in load_simple_room: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Load simple room failed: {str(e)}",
                **_failure_detail(e)
            }

    async def _cmd_quick_start_with_workstation_and_simple_room(self) -> Dict[str, Any]:
        """MCP handler: quick_start, then the Vention bench, then the Simple Room."""
        try:
            await self.quick_start_with_workstation_and_simple_room()
            return {
                "status": "success",
                "message": f"Quick start complete with {self._bench_label()} and Simple Room loaded.",
                **self._quick_start_report(),
                "force_publisher": getattr(self, "_force_publish_status", {"status": "not_configured"}),
            }
        except Exception as e:
            carb.log_error(f"Error in quick_start_with_workstation_and_simple_room: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Quick start with workstation and simple room failed: {str(e)}",
                **_failure_detail(e)
            }

    def _cmd_setup_pose_publisher(self) -> Dict[str, Any]:
        """MCP handler for setting up pose publisher action graph.

        Returns:
            Dictionary with execution result.
        """
        try:
            self.create_pose_publisher()
            return {
                "status": "success",
                "message": "Pose publisher action graph created at /Graph/ActionGraph_objects_poses"
            }
        except Exception as e:
            carb.log_error(f"Error in setup_pose_publisher: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Failed to setup pose publisher: {str(e)}",
                "traceback": traceback.format_exc()
            }

    def _cmd_start_ros_driver(self, mode: str = "sim", action: str = "up",
                              backend: str = "fake") -> Dict[str, Any]:
        """Bring the ROS2 robot driver up/down/status.

        Two backends, both ending at the same controllers/topics (joint_state_broadcaster
        -> /joint_states, scaled_joint_trajectory_controller -> FollowJointTrajectory):
          backend='fake'  (default) -> scripts/launch_ur_fake.sh — apt ur_robot_driver with
                          mock hardware. No Docker or ROS workspace needed. sim only.
          backend='ursim'           -> scripts/sim_bringup.sh — URSim docker + ur_bringup.
                          Needs Docker + a ROS 2 workspace with ur_bringup (WS_SETUP). Supports mode='real'.

        Runs in the MCP client worker thread (not Kit's main loop) and touches no
        USD/OmniGraph state — it's a pure host subprocess (ros2 / docker) — so it
        is safe to block here until the driver is ready. The socket read timeout
        is 300s; bring-up normally takes a few s (fake) / ~110s (ursim), so we cap
        the subprocess at 240s.
        """
        import os
        import subprocess
        from pathlib import Path

        if mode not in ("sim", "real"):
            return {"status": "error", "message": f"invalid mode '{mode}' (expected sim|real)"}
        if action not in ("up", "down", "status"):
            return {"status": "error", "message": f"invalid action '{action}' (expected up|down|status)"}
        if backend not in ("fake", "ursim"):
            return {"status": "error", "message": f"invalid backend '{backend}' (expected fake|ursim)"}

        session_id = os.environ.get("ISAAC_MCP_SIM_SESSION_ID")
        session_dir = os.environ.get("ISAAC_MCP_SIM_SESSION_DIR")
        if bool(session_id) != bool(session_dir):
            return {"status": "error", "message": "managed session driver configuration is incomplete"}
        if session_id and backend == "ursim":
            return {"status": "error", "message": "backend='ursim' uses shared host resources and is not "
                    "available in managed simulator sessions; use the fake backend or an unmanaged session"}

        # extension.py -> ur5e_dt -> ur5e-dt -> exts -> <repo root>
        repo_root = Path(__file__).resolve().parents[3]

        if backend == "fake":
            if mode == "real":
                return {"status": "error",
                        "message": "backend='fake' is sim-only, and real-robot mode is not enabled in this build (docs/TOOLS-UR5E.md)."}
            script = repo_root / "scripts" / "launch_ur_fake.sh"
            cmd = ["bash", str(script), action]          # fake script takes only the action
            label = f"launch_ur_fake.sh {action}"
        else:  # ursim
            script = repo_root / "scripts" / "sim_bringup.sh"
            cmd = ["bash", str(script), action, mode]
            label = f"sim_bringup.sh {action} {mode}"
        if not script.exists():
            return {"status": "error", "message": f"{script.name} not found at {script}"}

        # Run in a sanitized env. Isaac Sim's process carries its own ROS2 /
        # python (env_isaaclab) setup — AMENT_PREFIX_PATH, PYTHONPATH,
        # LD_LIBRARY_PATH, RMW_* — that conflicts with the system ROS2 the script
        # sources. Inheriting it makes `ros2 topic echo`/`ros2 launch` misbehave
        # (false "no /joint_states" → the script reaps a healthy driver). A clean
        # slate lets the script's ROS setup (+ config/ros_dds.env
        # / ros2_ws) be authoritative. ROS_DOMAIN_ID must match every ROS client
        # (see CLAUDE.md); both scripts source config/ros_dds.env for the rest of
        # the DDS profile (Cyclone + localhost-only).
        env = {
            "HOME": os.environ.get("HOME", str(Path.home())),
            "USER": os.environ.get("USER", ""),
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "ROS_DOMAIN_ID": os.environ.get("ROS_DOMAIN_ID", "0"),
        }
        if os.environ.get("ROS_DISTRO"):
            env["ROS_DISTRO"] = os.environ["ROS_DISTRO"]
        if os.environ.get("ROS_SETUP"):
            env["ROS_SETUP"] = os.environ["ROS_SETUP"]
        if session_id:
            # The normal simulator environment is intentionally not inherited, but the fake
            # driver's record, log and ownership marker must follow this managed instance.
            env["ISAAC_MCP_SIM_SESSION_ID"] = session_id
            env["UR_FAKE_PIDFILE"] = str(Path(session_dir) / "ur_fake_driver.pid")
            env["UR_FAKE_LOG"] = str(Path(session_dir) / "ur_fake_driver.log")
        timeout = 240 if action == "up" else 60

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, env=env,
            )
        except subprocess.TimeoutExpired as e:
            return {
                "status": "error",
                "message": f"{label} timed out after {timeout}s",
                "output": (e.output or "")[-2000:],
            }

        output = ((proc.stdout or "") + (proc.stderr or ""))[-2000:]
        if proc.returncode == 0:
            return {
                "status": "success",
                "message": f"ros driver {action} ({backend}/{mode}) completed",
                "ros_domain_id": env["ROS_DOMAIN_ID"],
                "output": output,
            }
        return {
            "status": "error",
            "message": f"{label} exited {proc.returncode}" + (f": {_last_error_line(output)}" if _last_error_line(output) else ""),
            "output": output,
        }

    def _cmd_sync_real_poses(self) -> Dict[str, Any]:
        """MCP handler for syncing real object poses to sim."""
        try:
            result = self.sync_real_poses()
            if not result["received"]:
                return {"status": "error", "message": "No pose message received on /objects_poses_real within 3 seconds.", **result}
            if not result["updated"]:
                return {"status": "error", "message": "Received a pose message, but no matching scene objects were updated.", **result}
            return {
                "status": "success",
                "message": f"Requested {len(result['updated'])} object pose(s) in sim from /objects_poses_real; "
                           "physics settles them from there, so read the poses again to see where they ended up",
                **result,
            }
        except Exception as e:
            carb.log_error(f"Error in sync_real_poses: {e}")
            traceback.print_exc()
            return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}

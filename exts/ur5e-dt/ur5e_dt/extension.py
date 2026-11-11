"""The UR5e digital twin extension: start-up, the bridge registration, stage events and shutdown.

Each area of the twin is a class in its own module, mixed into DigitalTwin below.
"""

import omni.ext
import omni.usd
import omni.ui as ui
import asyncio
import os
import carb
from isaacsim.gui.components.element_wrappers import ScrollingWindow
from isaac_mcp_bridge import ToolDefinition, get_mcp_bridge, knobs as bridge_knobs
from .bridge_binding import OWNER_ID as MCP_OWNER_ID, attach as attach_twin_bridge
from .physics_compat import selected_engine, subscribe_physics_step, wrench_capability
import omni.graph.core as og
from .capture_state import _viewport_pub
from .panel import Panel
from .log_panel import LogPanel
from .environment import Environment
from .video import Video
from .cameras import Cameras
from .scene import Scene
from .robot import Robot
from .objects import Objects
from .commands import Commands


# =============================================================================
# MCP TOOL REGISTRY - Single source of truth for all MCP tools
# =============================================================================
# Each tool definition includes:
#   - description: Tool description shown to MCP clients
#   - parameters: Dict of parameter definitions (name -> {type, description, required, default})
#
# The MCP server queries this registry on startup and dynamically registers tools.
# To add a new tool:
#   1. Add entry here with description and parameters
#   2. Add handler method _cmd_<tool_name>() in this extension
#   3. That's it! The server will automatically discover and expose it.
# =============================================================================
MCP_TOOL_REGISTRY = {
    # NOTE: run_python is provided by the isaac.mcp.bridge bridge as a
    # built-in verb — do not re-declare it here.
    "play_scene": {
        "description": "Start/resume the simulation timeline. Restores the saved twin's Python ROS callbacks; Newton twin playback uses CPU and needs a stopped stage before first initialization. Does not rebuild saved graphs or switch physics engines.",
        "parameters": {}
    },
    "stop_scene": {
        "description": "Stop the UR5e twin's simulation timeline, e.g. before generic sim__* edits. Stop rewinds physics to the authored start pose. For other scenes use sim__timeline__control.",
        "parameters": {}
    },
    "setup_camera_tf_graph": {
        "description": "Import the RealSense camera, attach it to the UR5e wrist and build its ROS 2 action graph (camera image, camera_info and the /tf_cam transform tree). Requires the UR5e to be loaded (e.g. after quick_start_with_workstation).",
        "parameters": {}
    },
    "assemble_objects": {
        "description": "Uses your own lab files from the asset folder, which cannot be downloaded (see docs/ASSETS.md). Assemble objects into a specified configuration.",
        "parameters": {
            "assembly": {
                "type": "string",
                "enum": ["fmb1", "fmb2", "fmb3", "fmb4", "jenga", "fmb_jenga"],
                "description": "Assembly configuration; fmb4 is Jenga, also accepted as jenga or fmb_jenga"
            }
        }
    },
    "randomize_object_poses": {
        "description": "Randomize the positions of non-base objects in /World/Objects of the UR5e twin, after ur5e_dt__add_objects.",
        "parameters": {}
    },
    "randomize_single_object": {
        "description": "Randomize the position of a single named object in a clear workspace area, keeping all other objects fixed.",
        "parameters": {
            "object_name": {
                "type": "string",
                "description": "Name of the object to randomize (e.g. 'inverted_u_yellow')"
            }
        }
    },
    "save_scene_state": {
        "description": "Save the UR5e twin's object poses to a JSON file in its scene_states folder, for ur5e_dt__restore_scene_state. A file of that name is replaced; with no name it gets a timestamp. Returns result, json_file_path and, on failure, error.",
        "parameters": {
            "json_file_path": {
                "type": "string",
                "description": "Path to save file. If omitted, a timestamped filename is used."
            }
        }
    },
    "restore_scene_state": {
        "description": "Restore the UR5e twin's object poses from a JSON file made by ur5e_dt__save_scene_state. Returns result and, on failure, error.",
        "parameters": {
            "json_file_path": {
                "type": "string",
                "description": "Path to restore from. If omitted, the most recent save is used."
            }
        }
    },
    "add_objects": {
        "description": "Uses your own lab files from the asset folder, which cannot be downloaded (see docs/ASSETS.md). Add objects to the scene from a predefined assembly folder.",
        "parameters": {
            "assembly": {
                "type": "string",
                "enum": ["fmb1", "fmb2", "fmb3", "fmb4", "jenga", "fmb_jenga"],
                "description": "Assembly folder; fmb4 is Jenga, also accepted as jenga or fmb_jenga"
            }
        }
    },
    "delete_objects": {
        "description": "Delete all objects from /World/Objects of the UR5e twin and the associated pose publisher graph.",
        "parameters": {}
    },
    "setup_pose_publisher": {
        "description": "Create an action graph publishing the UR5e twin's object poses to ROS 2 topic objects_poses_sim, after ur5e_dt__add_objects.",
        "parameters": {}
    },
    "start_ros_driver": {
        "description": "Bring the ROS 2 driver of the lab's UR5e twin up, down or report status, so robot-motion tools have a driver. backend='fake' (default) is sim only, with mock hardware and no Docker, ready in seconds; 'ursim' needs Docker and is the path to mode='real' (not yet enabled). Refuses if another UR driver is detected. Stop the fake driver before stopping its simulator. Managed sim__launch sessions support only fake. Use once at startup.",
        "parameters": {
            "mode": {
                "type": "string",
                "enum": ["sim", "real"],
                "description": "'sim' (default) or 'real' (lab UR5e — not yet enabled; requires backend='ursim')."
            },
            "action": {
                "type": "string",
                "enum": ["up", "down", "status"],
                "description": "'up' (default) brings the driver up, 'down' tears it down, 'status' reports state."
            },
            "backend": {
                "type": "string",
                "enum": ["fake", "ursim"],
                "description": "'fake' (default) = apt ur_robot_driver fake hardware via launch_ur_fake.sh (lightweight, sim only); 'ursim' = URSim docker via sim_bringup.sh (needs Docker + ~/ros2_ws; required for mode='real'; unavailable in managed sessions)."
            }
        }
    },
    "sync_real_poses": {
        "description": "Wait up to 3 seconds for /objects_poses_real and update matching sim objects. Reports an error if no message or matching objects arrive, and lists updated and snapped objects.",
        "parameters": {}
    },
    "new_stage": {
        "description": "Clear the entire USD stage and free accumulated memory. Use this when Isaac Sim's process memory has grown large after many scene loads/resets. This tears down all USD prims, Hydra render state, and PhysX allocations. The scene will be empty afterwards — use quick_start to rebuild it.",
        "parameters": {}
    },
    "quick_start": {
        "description": "RISE lab UR5e digital twin; for a generic start, see sim__quick_start. One-click scene setup from public assets: loads the scene, imports NVIDIA's UR5e with action graphs, creates a workspace camera and starts simulation. Adds the RG2 gripper only when its files are in the asset folder; the result's gripper field says which happened. Uses CPU physics in the selected engine; an existing non-CPU World is refused. Use after new_stage to rebuild a disposable scene.",
        "parameters": {}
    },
    "start_recording": {
        "description": "Start real-time viewport video recording using ffmpeg. Records frames from the viewport render buffer without blocking physics. Returns immediately — call stop_recording to finalize the video.",
        "parameters": {
            "file_name": {
                "type": "string",
                "description": "Output filename (e.g. 'my_recording.mp4'). Saved to the configured VIDEOS_DIR. Defaults to 'isaacsim_<timestamp>.mp4'."
            },
            "camera": {
                "type": "string",
                "description": "Camera prim path to record from (e.g. '/World/workspace_camera_sim'). If omitted, uses the active viewport camera."
            },
            "fps": {
                "type": "integer",
                "description": "Frames per second for the output video. Defaults to 30."
            }
        }
    },
    "stop_recording": {
        "description": "Stop the current real-time viewport recording and finalize the MP4 video file. Returns the output file path and frame count.",
        "parameters": {}
    },
    "get_recording_status": {
        "description": "Check if viewport video recording is currently active. Returns status, frame count, elapsed time, output path, and FPS.",
        "parameters": {}
    },
    "load_workstation": {
        "description": "Load the Vention bench as visual context under the lab's UR5e twin, at /World/Vention with its top at z=0. Uses your own lab file from the asset folder, which cannot be downloaded (see docs/ASSETS.md); a placeholder bench is used without it. Additive (cleared by new_stage), visual-only and safe while playing. Does not move the robot.",
        "parameters": {}
    },
    "quick_start_with_workstation": {
        "description": "RISE lab UR5e digital twin. Uses your own lab files from the asset folder, which cannot be downloaded (see docs/ASSETS.md). A placeholder bench is used without them. Same as quick_start, then loads the Vention workstation bench (see load_workstation). One call builds the full robot+scene and drops the bench underneath. quick_start itself is unchanged.",
        "parameters": {}
    },
    "load_simple_room": {
        "description": "Load Isaac's standard Simple Room environment (floor, walls, window, lamp) as a backdrop. Additive (cleared by new_stage): references the standard Isaac Simple_Room asset onto /World/SimpleRoom at z=-0.11 so it matches the source scene under the robot-base-at-0 convention (the Vention bench feet land on the room floor). Hides the default ground plane visual while keeping its z=0 collider, so object/robot physics are unchanged. Does not move the robot. Best used together with load_workstation.",
        "parameters": {}
    },
    "quick_start_with_workstation_and_simple_room": {
        "description": "RISE lab UR5e digital twin. Uses your own lab files from the asset folder, which cannot be downloaded (see docs/ASSETS.md). A placeholder bench is used without them. Same as quick_start, then loads the Vention workstation bench AND the Simple Room backdrop. One call builds the full robot+scene, the bench under the robot, and the room around it. quick_start itself is unchanged.",
        "parameters": {}
    },
    "set_object_pose": {
        "description": "Teleport a scene object under /World/Objects to an EXACT world pose. Use to set up controlled scenes — e.g. placing two parts close together to exercise neighbour-aware grasp gating. Applies via the same xformOp path randomize uses, so it persists under physics (object stays put on play). Mirror it with setup_pose_publisher so /objects_poses_sim reflects the new pose.",
        "parameters": {
            "object_name": {
                "type": "string",
                "description": "Object name under /World/Objects (e.g. 'u_brown')."
            },
            "position": {
                "type": "array",
                "description": "[x, y, z] world position in METERS."
            },
            "orientation": {
                "type": "array",
                "description": "Optional [w, x, y, z] quaternion. If given, overrides yaw_deg."
            },
            "yaw_deg": {
                "type": "number",
                "description": "Optional yaw rotation about world z in degrees (used when orientation is omitted). Default 0 (identity)."
            }
        }
    },
}


# Module-level handle to the live extension instance, so socket-driven
# execute_python_code can drive individual build steps (load_ur5e, import_adapter,
# attach_rg2_to_ur5e, ...) one at a time:  from ur5e_dt import extension as E; E._EXT_INSTANCE.load_ur5e()
_EXT_INSTANCE = None


class DigitalTwin(Panel, LogPanel, Environment, Video, Cameras, Scene, Robot, Objects, Commands, omni.ext.IExt):
    def on_startup(self, ext_id):
        print("[DigitalTwin] Digital Twin startup")
        global _EXT_INSTANCE
        self._closing = False

        # Disable UJITSO multiprocess collision cooking for the WHOLE session (root-caused
        # 2026-06-21). UJITSO (on by default) cooks collision meshes in FORKED subprocesses;
        # forking from this live, heavily-multithreaded Kit app deadlocks in carb.tasking ->
        # omni.physx.cooking. That single deadlock is BOTH long-standing wedges:
        #   - `add_objects` SDF object cook (6x jenga SDF-256 wedged; cooks in ~2s with UJITSO off)
        #   - `quick_start`/`load_robot` COLD-CACHE robot cook (documented as "broken on 5.x",
        #     wedged 240s+; cold-cache quick_start completes in ~3s with UJITSO off — verified).
        # In-process cooking is fast enough here (robot ~3s cold, objects ~2s) and removes the
        # whole DerivedDataCache cold-cook fragility (snapshot/restore is then only a speed cache,
        # not a correctness crutch). Set here so it's in force before any cook. The per-call set in
        # _cmd_add_objects stays as a belt-and-suspenders guard. Re-enable only to diagnose.
        try:
            import carb.settings
            carb.settings.get_settings().set_bool(
                "/physics/cooking/ujitsoCollisionCooking", False)
            print("[DigitalTwin] UJITSO collision cooking disabled (in-process cook; no fork deadlock)")
        except Exception as _e:
            carb.log_warn(f"[DigitalTwin] could not disable UJITSO cooking at startup: {_e}")

        self._main_loop = asyncio.get_event_loop()
        self._timeline = omni.timeline.get_timeline_interface()
        self._ur5e_view = None
        self._articulation = None
        self._gripper_view = None
        self._viewport_rendering_enabled = True
        self._viewport_toggle_btn = None

        # Gripper physics callback state
        self._gripper_physx_sub = None
        self._gripper_articulation = None
        self._gripper_graph_path = None
        self._gripper_sub_attr_path = None
        self._gripper_pub_attr_path = None
        self._gripper_asym_pub_attr_path = None
        self._gripper_publish_active = False
        self._gripper_warmup = 0  # skip N physics steps before re-init

        # Force publisher physics callback state
        self._force_physx_sub = None
        self._effort_articulation = None
        self._force_graph_path = None
        self._force_pub_node = None
        self._force_warmup = 0

        # MCP bridge handle (set in on_startup); socket + sessions live on the bridge.
        self._mcp = None
        self._mcp_owner_registered = False

        # Add Objects UI state
        self._selected_assembly = "fmb1"
        self._object_spacing = 0.25  # Spacing between objects along X-axis in meters
        self._y_offset = -0.4  # Y offset for all objects
        self._z_offset = 0.0495  # Z offset for all objects
        self._hidden_objects = {}  # {name: {ref_path, body_pose, category}}

        # Output directory pushed by MCP server on connect (None = use default)
        self._output_dir = None

        # Physics scene settings
        self._min_frame_rate = 60
        self._time_steps_per_second = 120

        # UR5e joint drive parameters (USD angular-drive units: N*m per degree,
        # N*m*s per degree; PhysX works per radian, so these become 57.2958x larger
        # in the solver -- see _verify_drive_gains below).
        #
        # 100/10 per degree was chosen on 2026-10-30 and is the shipped default:
        #   tau = kd/kp = 0.10 s (first-order position loop), measured rise 0.216 s,
        #   settle 0.524 s, no overshoot, zero steady-state error. Overdamped
        #   (zeta ~4 at ~1 kg*m^2), so nothing rings near the 60 Hz sample rate, and
        #   contact with the bench behaves like a 5.7 kN*m/rad spring rather than a
        #   near-resonant one.
        #
        # Fidelity knob, kept documented because mirroring a real robot tightly may
        # matter more than contact softness later: raise stiffness and keep damping
        # so that tau = damping/stiffness lands at 0.02-0.05 s. Measured on the same
        # rig (control experiments, gain sweep, 2026-10-30):
        #   200/10 per deg (tau 50 ms): rise 0.120 s, settle 0.347 s, no overshoot
        #   400/10 per deg (tau 25 ms): rise 0.100 s, settle 0.289 s, no overshoot
        # Do not raise stiffness alone: it lowers zeta (the asset's ~10230/4 ran at
        # zeta ~0.15, a ~120 Hz resonance sampled at 60 Hz), and contact forces grow
        # with it. Re-run the drive-settings and single-joint-motion checks after changes.
        self._ur5e_max_force = 200.0
        self._ur5e_stiffness = 100.0
        self._ur5e_damping = 10.0

        # RG2 gripper joint drive parameters
        # NOTE: the RG2 gripper drive gains are the module constants RG2_ACT_STIFFNESS / RG2_ACT_DAMPING / RG2_ACT_MAXFORCE (applied when the gripper is actuated).

        # Gripper physics material
        self._gripper_dynamic_friction = 0.6
        self._gripper_static_friction = 0.5
        self._gripper_restitution = 0.0
        self._gripper_friction_combine_mode = "max"
        self._gripper_restitution_combine_mode = "min"

        # Common physics material (applied to all objects)
        self._object_dynamic_friction = 0.1
        self._object_static_friction = 0.1
        self._object_restitution = 0.0
        self._object_friction_combine_mode = "max"
        self._object_restitution_combine_mode = "min"

        # Shared collision settings
        # 256 (not 300): object-mesh SDF cooking is NOT cached in DerivedDataCache, so it re-cooks on
        # every add_objects. 300 cooks for many minutes on this host (en4226769-l); 256 is a4500's
        # verified-working value (still seats fmb peg-in-hole assemblies). See SDF resolution cost cliff.
        self._sdf_resolution = 256
        self._contact_offset = 0.003

        # Per-category prim settings (collision approximation, rest offset, angular damping)
        # Board (type: "board", e.g. base1, base2, base3)
        self._board_collision_approximation = "sdf"  # "sdf" or "convexDecomposition"
        self._board_rest_offset = 0.0
        self._board_angular_damping = 30.0

        # Block (subtype: "block", e.g. u_brown, fork_orange, line_green)
        self._block_collision_approximation = "sdf"
        self._block_rest_offset = -0.0005
        self._block_angular_damping = 30.0

        # Socket (subtype: "socket", e.g. inverted_u_brown)
        self._socket_collision_approximation = "sdf"
        self._socket_rest_offset = -0.0005
        self._socket_angular_damping = 30.0

        # Peg (subtype: "peg", e.g. hex_blue, hex_red)
        self._peg_collision_approximation = "sdf"
        self._peg_rest_offset = -0.0005
        self._peg_angular_damping = 50.0

        # Isaac Sim handles ROS2 initialization automatically through its bridge
        print("ROS2 bridge will be initialized by Isaac Sim when needed")

        # The tool handlers also use some widget models, so build the panel but
        # keep it hidden unless explicitly requested for this simulator session.
        show_panel = os.getenv("UR5E_DT_SHOW_PANEL") == "1" and os.getenv("UR5E_DT_HEADLESS") != "1"
        self._window = ScrollingWindow(title="UR5e Digital Twin", width=300, height=800,
                                       visible=show_panel)
        with self._window.frame:
            with ui.VStack(spacing=5):
                self.create_ui()

        # The standard launcher defers opening the shared socket until all twin
        # tools are registered. A direct Kit launch also works: attach joins an
        # existing socket or starts the unbound singleton on the selected port.
        self._attach_bridge()
        _EXT_INSTANCE = self

        # Console can be created after this extension during startup. Deferred
        # docking seats the twin beside it once available instead of floating
        # over the viewport; a missing Console leaves the panel usable.
        if show_panel:
            try:
                self._window.deferred_dock_in("Console", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE)
            except Exception as exc:
                carb.log_warn(f"[DigitalTwin] could not dock the twin panel: {exc}")
            self._focus_panel_soon()

    def _attach_bridge(self):
        try:
            bridge = get_mcp_bridge()
            twin_port = os.getenv("UR5E_DT_PORT") or os.getenv("ISAAC_SIM_PORT")
            configured_port = bridge_knobs.PORT.read()
            attach_twin_bridge(
                bridge, MCP_TOOL_REGISTRY, self, ToolDefinition,
                requested_port=int(twin_port) if twin_port is not None else None,
                configured_port=int(configured_port) if configured_port is not None else None,
                host=bridge_knobs.BIND.read() or "localhost",
            )
        except BaseException:
            # Kit may not call on_shutdown after on_startup raises. Undo the UI,
            # callbacks and deferred logger created before this attachment.
            self._closing = True
            try:
                self._finish_shutdown()
            except Exception as cleanup_error:
                carb.log_error(f"[DigitalTwin] cleanup after bridge startup failure: {cleanup_error}")
            raise
        self._mcp = bridge
        self._mcp_owner_registered = True

    def _subscribe_stage_events(self):
        """Listen for stage OPENED events to clean up stale World singleton.
        Fires whenever the user opens a new stage from the UI or programmatically."""
        import omni.usd
        try:
            stage_events = omni.usd.get_context().get_stage_event_stream()
            self._stage_event_sub = stage_events.create_subscription_to_pop(
                self._on_stage_event, name="ur5e_dt_stage_watcher"
            )
            print("[ur5e-dt] Subscribed to stage lifecycle events")
        except Exception as e:
            print(f"[ur5e-dt] Failed to subscribe to stage events: {e}")

    def _on_stage_event(self, event):
        """Handle stage lifecycle events. On OPENED (new/load), clean up World singleton."""
        import omni.usd
        if event.type == int(omni.usd.StageEventType.OPENED):
            # Callback handles belong to the previous stage. Do not write a
            # force gate here: OPENED already refers to the replacement stage.
            self._stop_gripper_physics()
            self._stop_physics_callback('_force_physx_sub', '_effort_articulation',
                                        '_force_publish_active', '_force_warmup')
            # Clear extension instance references to old stage objects
            for attr in ['_ur5e_view', '_articulation', '_gripper_articulation',
                         '_force_articulation', '_gripper_pub_node', '_force_pub_node']:
                if hasattr(self, attr):
                    setattr(self, attr, None)

            # Stop viewport publishers (they hold references to old viewport/camera)
            try:
                _viewport_pub.stop_all()
            except Exception:
                pass

            # Clear the World singleton
            try:
                from isaacsim.core.api.world import World
                try:
                    world = World.instance()
                    if world is not None:
                        world.clear_all_callbacks()
                        # OPENED already refers to the new stage. Never clear
                        # its USD or synchronously pump Kit from this callback.
                        world.scene.clear(registry_only=True)
                except Exception as e:
                    # World.clear() can fail on a stage that is already gone; the singleton must still be dropped.
                    print(f"[ur5e-dt] World.clear skipped on stage event: {e}")
                World.clear_instance()
                print("[ur5e-dt] Stage OPENED — World singleton cleared")
                asyncio.ensure_future(self._select_person_view_when_loaded())
            except Exception as e:
                print(f"[ur5e-dt] World cleanup on stage event: {e}")

    def _run_on_main_thread(self, fn):
        """Schedule fn to run on the main thread on the NEXT loop tick — NEVER synchronously.

        Running fn() synchronously here re-enters omni.ui from inside whatever is currently on the
        stack. The fatal case: a carb-log callback (_on_carb_log) or the stdout LogRedirector fires
        on the MAIN thread from inside world.reset()/the first physics step — PhysX logs its
        articulation-init warnings synchronously there — so the synchronous branch mutated omni.ui
        mid-articulation-creation and crashed the process (silent C++ death at the first physics
        step). Reproduced + isolated 2026-06-19: disabling the log redirect makes the full live
        ur5e-dt build (UR5e + husarion mimic gripper + graphs + weld) survive reset/play; this was
        misdiagnosed for a long time as an env/PhysX instability. Always deferring via
        call_soon_threadsafe (safe from the main thread AND from PhysX worker threads) moves the UI
        mutation off the physics-step stack to the next tick, killing the re-entrancy in every mode
        while keeping the log panel working."""
        try:
            self._main_loop.call_soon_threadsafe(fn)
        except Exception:
            pass

    def _resubscribe_physics_callbacks(self):
        """Bind runtime callbacks to existing graphs, without rebuilding USD."""

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return

        # Gripper action graph (joint actuator: Float64 target_sub -> rg2_gripper_joint)
        gripper_graph = "/Graph/ActionGraph_RG2"
        if stage.GetPrimAtPath(gripper_graph):
            self._stop_gripper_physics()
            self._gripper_graph_path = gripper_graph
            self._gripper_sub_attr_path = f"{gripper_graph}/target_sub.outputs:data"
            self._gripper_state_name_attr = f"{gripper_graph}/state_pub.inputs:name"
            self._gripper_state_pos_attr = f"{gripper_graph}/state_pub.inputs:position"
            self._gripper_pad_attr = f"{gripper_graph}/pad_pub.inputs:data"
            self._pad_local = None
            self._gripper_articulation = None
            self._gripper_act_dof_idx = None
            self._gripper_physx_sub = subscribe_physics_step(
                self._on_physics_step_gripper
            )
            self._gripper_publish_active = True
            print(f"[HotReload] Re-subscribed gripper physics callback for {gripper_graph}")

        # Force publisher action graph
        force_graph = "/Graph/ActionGraph_UR5e_ForcePublish"
        if stage.GetPrimAtPath(force_graph):
            self._stop_physics_callback('_force_physx_sub', '_effort_articulation',
                                        '_force_publish_active', '_force_warmup')
            self._force_graph_path = force_graph
            self._force_publish_status = dict(wrench_capability(), status="warming_up")
            newton_force = selected_engine() == "newton"
            if newton_force:
                if not stage.GetPrimAtPath(force_graph + "/valid"):
                    self._force_publish_status.update(available=False, status="unavailable",
                        reason="Saved Newton force graph lacks a validity gate; rebuild on a new twin stage")
                    return
            self._set_force_gate(False, required=newton_force)
            if not self._force_publish_status["available"]:
                self._force_publish_status["status"] = "unavailable"
                return
            # Recover the publisher OG node handle from the existing graph
            pub_prim = stage.GetPrimAtPath(f"{force_graph}/publisher")
            if pub_prim:
                self._force_pub_node = og.get_node_by_path(f"{force_graph}/publisher")
            self._force_physx_sub = subscribe_physics_step(
                self._on_physics_step_force
            )
            self._force_publish_active = True
            print(f"[HotReload] Re-subscribed force publisher physics callback for {force_graph}")

    def on_shutdown(self):
        """Clean shutdown"""
        if self._closing:
            return
        self._closing = True
        # Hide this owner's tools immediately, but leave its live state intact
        # until any already-running handler has finished using it.
        global _EXT_INSTANCE
        if _EXT_INSTANCE is self:
            _EXT_INSTANCE = None
        if self._mcp is not None and self._mcp_owner_registered:
            self._mcp_owner_registered = False
            self._mcp.unregister_owner(MCP_OWNER_ID, on_drained=self._finish_shutdown)
        else:
            self._finish_shutdown()

    def _finish_shutdown(self):
        """Release twin-owned state only after its handlers have drained."""
        self._mcp = None
        self._teardown_log_redirect()
        print("[DigitalTwin] Digital Twin shutdown")

        # Stop physics-rate callbacks
        self._stop_force_publish()
        self._stop_gripper_physics()

        # Isaac Sim handles ROS2 shutdown automatically
        print("ROS2 bridge shutdown handled by Isaac Sim")

        # Clear UI widget references to break closure references
        self._workspace_checkbox = None
        self._custom_checkbox = None
        self._custom_camera_prim_field = None
        self._custom_camera_topic_field = None
        self._resolution_combo = None
        self._assembly_combo = None

        # Destroy window - clear frame first to break lambda closure references
        if self._window:
            self._window.frame.clear()
            self._window.destroy()
            self._window = None

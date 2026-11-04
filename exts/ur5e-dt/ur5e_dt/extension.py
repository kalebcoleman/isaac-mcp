import omni.ext
import omni.ui as ui
import asyncio
import numpy as np
import os
import sys
import io
import threading
import glob
import omni.client
import omni.kit.commands
import carb
import math
import socket
import json
import traceback
from typing import Dict, Any

from isaacsim.core.api.world import World
from isaacsim.gui.components.element_wrappers import ScrollingWindow

# The bridge extension owns the process-wide socket; the twin owns only its
# namespace and tool registrations. The dependency is declared in extension.toml.
from isaac_mcp_bridge import ToolDefinition, get_mcp_bridge, knobs as bridge_knobs
from .graph_compat import ensure_fixed_graph
from .asset_cache import AssetCorrupt, AssetMissing, asset_source, cache_dir, missing_assets, resolve_dir, resolve_path, resolve_uri
from .object_pose import read_local_pose, set_local_pose
from .robot_visuals import prepare_robot_visuals
from .bridge_binding import OWNER_ID as MCP_OWNER_ID, attach as attach_twin_bridge
from .recording import FramePacer
from .tf_compat import use_legacy_transform_publisher
from .world_compat import initialized_twin_world, paused_scene_edit, twin_play_edit
from .physics_compat import (
    selected_engine, require_newton_mount_support,
    configure_newton_rate, configure_newton_mount,
    subscribe_physics_step, close_physics_step,
    configure_newton_gripper, runtime_link_matrix,
    prepare_newton_tensors, prepare_newton_wrench, wrench_capability,
    tool_wrench, physics_stamp, prepare_saved_twin_runtime,
)


def _use_legacy_tf_publisher(og):
    from importlib.metadata import PackageNotFoundError, version

    node_version = og.GraphRegistry().get_node_type_version("isaacsim.core.nodes.IsaacComputeTransformTree")
    try:
        sim_version = version("isaacsim")
    except PackageNotFoundError:
        sim_version = None
    return use_legacy_transform_publisher(node_version, sim_version)

# MCP output directory configuration
BASE_OUTPUT_DIR = os.getenv("MCP_CLIENT_OUTPUT_DIR", "").strip()
if BASE_OUTPUT_DIR:
    BASE_OUTPUT_DIR = os.path.abspath(BASE_OUTPUT_DIR)
    RESOURCES_DIR = os.path.join(BASE_OUTPUT_DIR, "resources")
    VIDEOS_DIR = os.path.join(BASE_OUTPUT_DIR, "videos")
else:
    RESOURCES_DIR = "resources"
    VIDEOS_DIR = ""  # resolved below after _ext_dir

# Local asset paths (no Nucleus dependency)
_ext_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not VIDEOS_DIR:
    VIDEOS_DIR = os.path.join(_ext_dir, "videos")

# Environment scene USDs (relative to Isaac assets root). Verified against Isaac 5.0.
ENVIRONMENTS = {
    "None (flat plane only)": None,
    "Gridroom Black": "Isaac/Environments/Grid/gridroom_black.usd",
    "Gridroom Curved": "Isaac/Environments/Grid/gridroom_curved.usd",
    "Simple Room": "Isaac/Environments/Simple_Room/simple_room.usd",
    "Simple Warehouse": "Isaac/Environments/Simple_Warehouse/warehouse.usd",
    "Full Warehouse": "Isaac/Environments/Simple_Warehouse/full_warehouse.usd",
    "Warehouse w/ Forklifts": "Isaac/Environments/Simple_Warehouse/warehouse_with_forklifts.usd",
    "Warehouse w/ Shelves": "Isaac/Environments/Simple_Warehouse/warehouse_multiple_shelves.usd",
    "Hospital": "Isaac/Environments/Hospital/hospital.usd",
    "Office": "Isaac/Environments/Office/office.usd",
}
ENVIRONMENT_PRIM_PATH = "/World/Environment"

# Robot base mount height above the ground plane (meters).
# Convention: world z=0 is the floor surface; robot base is lifted by ROBOT_BASE_Z.
# This matches Isaac Sim's standard env convention so all environments compose cleanly.
ROBOT_BASE_Z = 0.0  # Isaac convention (f19ea90): ground plane + robot base both at z=0. Consumer ros-mcp-server is mode-aware to match this in sim.

# Vention workstation bench — optional, additive (see load_workstation / "Load Workstation" button).
# Bundled, self-contained asset authored in centimeters (metersPerUnit 0.01) → scale 0.01 to meters.
# At load time the bench TOP surface is aligned to z=0 (computed from its world bbox), so it
# coincides with the work plane where the robot base (ROBOT_BASE_Z=0.0) and objects already sit.
# The robot is NOT moved and ROBOT_BASE_Z is NOT changed → ROS pose parity is untouched.
# WORKSTATION_XY is the source scene's vention-relative-to-robot-at-origin offset.
WORKSTATION_USD = "workstation/vention.usd"
WORKSTATION_PRIM_PATH = "/World/Vention"
WORKSTATION_XY = (0.2252, -0.11364)
WORKSTATION_SCALE = 0.01

# Isaac standard "Simple Room" environment — optional backdrop (see load_simple_room).
# Standard Isaac asset (meters, no scale) referenced via the Isaac assets root so it resolves
# whether assets are local Nucleus or cloud. Placed at z = -0.11: the source scene's room is at
# identity with the robot at z=0.11, so under our robot-base-at-0 convention the whole scene
# shifts -0.11 — the bench feet then land on the room floor exactly as authored.
SIMPLE_ROOM_PRIM_PATH = "/World/SimpleRoom"
SIMPLE_ROOM_ASSET_REL = "/Isaac/Environments/Simple_Room/simple_room.usd"
SIMPLE_ROOM_Z = -0.11

# --- Husarion-native OnRobot RG2 gripper (CAD-exact twin; see import_rg2_gripper) ---
# BARE gripper (no baked coupler): rg2_gripper_fixed.usd has defaultPrim /onrobot_rg2 ->
# /World/RG2_Gripper, articulation root rg2_base_link, joints under /World/RG2_Gripper/joints/.
# The 15mm OnRobot Quick-Changer is a SEPARATE part (QuickChanger.usd), the SOLE adapter — see
# attach_rg2_to_ur5e. This replaces husarion_rg2_fixed.usd, which baked in a second coupler
# (tool0 -> quick_changer_link -> rg2_base = +15mm) that DOUBLE-COUNTED with the external
# QuickChanger -> flange->rg2_base = 30mm instead of the real 15mm (two adapters, wrong).
RG2_USD_REL = "gripper/husarion_rg2/rg2_gripper_fixed.usd"
RG2_ART_ROOT_REL = "rg2_base_link"               # articulation root under /World/RG2_Gripper
RG2_ACT_JOINT = "rg2_gripper_joint"              # the single actuated revolute (axis Z)
RG2_MIMIC_JOINTS = (                             # the 5 PhysxMimicJointAPI-coupled finger joints
    "rg2_l_finger_2_joint", "rg2_l_finger_passive_joint",
    "rg2_r_finger_1_joint", "rg2_r_finger_2_joint", "rg2_r_finger_passive_joint",
)
RG2_FINGER_LINKS = ("rg2_l_finger_link", "rg2_r_finger_link")  # fingertip links (pad carriers)
# Mimic-spring rigidity: the importer authors a weak spring (nf=25Hz, damp=0.005 → ~5.3° lag).
# Measured sweep (scripts/test_husarion_rg2_mimic_rigidity.py): nf=1000/damp=1.0 → ~0.005° lag,
# 0 ring. Tunes the constraint's OWN spring (NOT a fighting DriveAPI).
RG2_MIMIC_NAT_FREQ = 1000.0
RG2_MIMIC_DAMPING = 1.0
# Actuated-joint position drive. The importer's gains were sized for the WEAK default mimic
# spring; with the stiffened mimic the actuated joint needs real authority to drive the coupled
# parallelogram across the full range. Validated (scripts/test_husarion_rg2_mount.py): tracks
# theta to ~0.001 rad with mimic residual ≤0.09° across [-0.45, 0.78]. NOTE: this is a
# POSITION-mirroring gripper — grasp firmness comes from holding the commanded width; if the
# grasp loop later needs softer/force-limited contact, lower RG2_ACT_MAXFORCE (trades a little
# position accuracy at the extremes).
RG2_ACT_STIFFNESS = 10000.0
RG2_ACT_DAMPING = 1000.0
# 2026-06-19: maxForce 1000 → 5 → 1.0. The operator saw the gripper "closing too hard".
# This is a FORCE-LIMITED AUTO-STOP gripper, by design and for sim/real PARITY: the command is
# just "close" (target = full-close stop) and maxForce is the grip-force cap, so the drive closes
# until it contacts the part and stalls at maxForce — exactly like the real RG2 firmware and the
# old force-drive RG2.usd (maxForce=1.0). Do NOT command a hard width target (that would break
# parity). The bug was purely the cap value: at 1000 the drive crushed parts THROUGH their
# collision mesh (inverted_u_brown settled at 12.56mm against a true ~24.5mm wall); even at 5 the
# hex settled at 12.4mm (true ~16). Measured the cap DOWN (free-air close still reaches the stop
# even at 0.5, so the stiffened mimic does NOT block low-force closing): at maxForce=1.0 the
# holding effort is gentle (u_green wrap −0.24 N·m; hex friction stalls at the ~1.0 cap, gap
# 14.33mm near the true surface) and BOTH a wrap grasp and the worst-case friction grasp still
# lift clean. 1.0 == the old RG2.usd value. We keep the ACCELERATION drive (not force) because the
# stiffened mimic needs the position-term authority to drive the parallelogram closed reliably;
# the maxForce cap makes the contact gentle regardless of drive type. Raise only if a part slips
# ("increase from there only if required"). hex sits at the cap, so 1.0 is about the floor.
RG2_ACT_MAXFORCE = 1.0
# Cap finger joint velocity. The URDF importer leaves maxJointVelocity at ~1e6 deg/s (effectively
# unlimited) on the mimic finger joints → the jaws SLAM shut (the other half of "closing too hard").
# 114.59 deg/s (= 2 rad/s) is the value the old RG2.usd used and a realistic RG2 closing speed.
RG2_MAX_JOINT_VEL = 114.59
# Actuated-joint hard stops (URDF limit on rg2_gripper_joint). The extension is a PURE joint
# actuator: it clamps the commanded /rg2_sim/joint_target to these physical limits only — the
# contact-width↔theta mapping + CAD operational range live in the control-pkg sim backend.
RG2_THETA_MIN = -0.45
RG2_THETA_MAX = 1.0
# Topics for the migrated gripper seam (control pkg <-> twin); rad, sensor_msgs/JointState.
RG2_JOINT_TARGET_TOPIC = "/rg2_sim/joint_target"   # std_msgs/Float64, actuated joint angle (rad)
RG2_JOINT_STATE_TOPIC = "/rg2_sim/joint_state"     # sensor_msgs/JointState (backend selects by name)
# Measured pad-to-pad gap (the TRUE contact gap, not arc(theta)). The backend republishes this as
# /rg2_sim/contact_width and demotes the arc(theta) value to /rg2_sim/actuated_width. Computed from
# the inner-most finger-pad collision-mesh point per finger, projected on the live closing axis —
# arc(theta) under-reports under contact-stall load (verify §5a / gotchas §8 of isaac-urdf-import).
RG2_PAD_GAP_TOPIC = "/rg2_sim/pad_gap_m"           # std_msgs/Float64, meters (RG2_FINGER_LINKS above)

# Per-environment Z offset to align each env's floor surface to z=0.
# Most envs already author their floor at z=0 — only the legacy gridroom assets need correction.
ENVIRONMENT_Z_OFFSETS = {
    "Gridroom Black":  0.141,   # staticPlaneActor at z=-0.141
    "Gridroom Curved": 0.007,   # staticPlaneActor at z=-0.007
    # All others default to 0.0 (floor already at z=0, or table-workspace scenes like Simple Room)
}

# Ground plane grid tint presets — tints the procedural grid lines of default_environment.usd.
GRID_TINT_PRESETS = {
    "White":      (1.00, 1.00, 1.00),
    "Blue":       (0.20, 0.45, 0.90),
    "Green":      (0.25, 0.75, 0.35),
    "Amber":      (1.00, 0.70, 0.10),
    "Red":        (0.85, 0.20, 0.20),
    "Cyan":       (0.20, 0.80, 0.85),
    "Custom Hex": None,  # special: read from hex field
}

# Module-level recording manager — survives hot-reloads because we guard against re-init.
# Hot-reload re-executes the module, but _recording_mgr persists if already set.
class _RecordingState:
    """Holds ffmpeg process and recording state outside the extension class."""
    def __init__(self):
        self.ffmpeg_proc = None
        self.frame_sub = None
        self.recording = False
        self.output_path = None
        self.frame_count = 0
        self.start_time = 0
        self.fps = 30
        self.last_print = 0
        self.error = None
        self.pacer = None
        self.capture_token = None
        self.recording_lock = threading.RLock()
        self.stopping = False
        # Each Kit process must own its recorder state. A shared state file
        # makes separate simulator instances mistake each other's recording for theirs.
        self.state_dir = (os.path.join(BASE_OUTPUT_DIR, ".recorder-state", str(os.getpid())) if BASE_OUTPUT_DIR
                          else f"/tmp/isaacsim_recording-{os.getpid()}")
        self.state_file = os.path.join(self.state_dir, "recorder_state.json")

    def save_state(self):
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump({"recording": True, "output_path": self.output_path, "fps": self.fps}, f)

    def clear_state(self):
        if os.path.exists(self.state_file):
            os.remove(self.state_file)

    def is_recording(self):
        if getattr(self, "stopping", False):
            return True
        if self.recording and self.ffmpeg_proc is not None:
            return True
        # Check state file as fallback
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    state = json.load(f)
                return state.get("recording", False)
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return False

# Guard: don't re-create if already exists from previous hot-reload
if '_recording_mgr' not in globals():
    _recording_mgr = _RecordingState()


class _ViewportCameraPublisher:
    """Publishes viewport frames to ROS2 topics using capture_viewport_to_buffer.
    Zero extra GPU render cost — reuses the already-rendered viewport frame.
    Survives hot-reloads via module-level singleton.

    Uses a single persistent rclpy node to avoid stale DDS participants
    (destroy_node doesn't immediately release the DDS participant, so
    recreating nodes causes ghost publishers to linger in `ros2 topic info`).
    """

    def __init__(self):
        self._publishers = {}  # {topic_name: {pub, sub, camera_path, active}}
        self._rclpy_initialized = False
        self._node = None  # single persistent node shared across all topics

    def _ensure_rclpy(self):
        if not self._rclpy_initialized:
            import rclpy
            try:
                rclpy.init()
            except RuntimeError as e:
                # Isaac's ROS 2 bridge may already have initialized the process-wide context.
                if 'only be called once' not in str(e):
                    raise
            self._rclpy_initialized = True

    def _ensure_node(self):
        """Create the shared rclpy node on first use."""
        import rclpy
        if self._node is None:
            self._ensure_rclpy()
            self._node = rclpy.create_node("isaacsim_viewport_publisher")
            print(f"[ViewportPub] Created shared node: {self._node.get_name()}")

    def is_rclpy_available(self):
        try:
            import rclpy
            return True
        except ImportError:
            return False

    def start(self, camera_prim_path, topic_name, frame_id="camera_link"):
        """Start publishing viewport frames for a camera to a ROS2 topic."""
        import rclpy
        import omni.kit.viewport.utility as vp_utils
        from sensor_msgs.msg import Image

        # Stop existing publisher for this topic first (prevents stale handles)
        if topic_name in self._publishers:
            if self._publishers[topic_name]["active"]:
                print(f"[ViewportPub] Already publishing on /{topic_name}")
            self.stop(topic_name)

        self._ensure_node()

        # Headless / no-window: capture_viewport_to_buffer + subscribe_to_frame_change HANG the build
        # (they wait on presented frames that never come; under webrtc the off-thread capture during the
        # reset storm crashes — same omni.ui-off-main-thread class as the log-redirect crash). The
        # workspace-camera ROS stream is a visualization nicety the grasp loop does not need. DETECT
        # headless via /app/window/enabled (False under --no-window): get_active_viewport() is NOT
        # reliable — the full experience makes a windowless ViewportAPI even headless (non-None).
        import carb.settings as _carb_settings
        if not bool(_carb_settings.get_settings().get("/app/window/enabled")):
            print("[ViewportPub] Headless (/app/window/enabled=False) — skipping viewport frame publisher.")
            return
        viewport = vp_utils.get_active_viewport()
        pub = self._node.create_publisher(Image, f"/{topic_name}", 10)

        entry = {
            "pub": pub,
            "sub": None,
            "camera_path": camera_prim_path,
            "frame_id": frame_id,
            "active": True,
        }

        def on_capture(buffer, buffer_size, width, height, fmt):
            import ctypes, array
            if not entry["active"]:
                return
            try:
                ctypes.pythonapi.PyCapsule_GetPointer.restype = ctypes.c_void_p
                ctypes.pythonapi.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
                ptr = ctypes.pythonapi.PyCapsule_GetPointer(buffer, None)

                # Single copy from C buffer → array.array (0.3ms for 3.6MB)
                # array.array('B') hits the fast path in Image.data setter (O(1), no element validation)
                c_arr = (ctypes.c_ubyte * buffer_size).from_address(ptr)
                a = array.array('B')
                a.frombytes(c_arr)

                # Publish RGBA directly — no RGB conversion needed (saves ~12ms/frame)
                msg = Image()
                msg.header.frame_id = entry["frame_id"]
                msg.height = height
                msg.width = width
                msg.encoding = "rgba8"
                msg.is_bigendian = False
                msg.step = width * 4
                msg.data = a  # O(1) fast path, no isinstance() per byte
                entry["pub"].publish(msg)
            except Exception:
                pass

        def on_frame(event):
            if not entry["active"]:
                return
            vp_utils.capture_viewport_to_buffer(viewport, on_capture)

        entry["sub"] = viewport.subscribe_to_frame_change(on_frame)
        self._publishers[topic_name] = entry
        print(f"[ViewportPub] Started publishing /{topic_name} from {camera_prim_path}")

    def stop(self, topic_name):
        """Stop publishing a specific topic.
        Destroys only the publisher (not the node), so DDS participants are
        properly released when the publisher goes out of scope."""
        if topic_name not in self._publishers:
            return
        entry = self._publishers[topic_name]
        entry["active"] = False
        entry["sub"] = None
        # Destroy the publisher through the node — this releases its DDS writer
        if self._node is not None:
            try:
                self._node.destroy_publisher(entry["pub"])
            except Exception as e:
                print(f"[ViewportPub] destroy_publisher warning: {e}")
        del self._publishers[topic_name]
        print(f"[ViewportPub] Stopped publishing /{topic_name}")

    def stop_all(self):
        """Stop all viewport publishers."""
        for topic in list(self._publishers.keys()):
            self.stop(topic)

    def is_active(self, topic_name=None):
        if topic_name:
            return topic_name in self._publishers and self._publishers[topic_name]["active"]
        return any(e["active"] for e in self._publishers.values())

    def get_status(self):
        return {t: {"camera": e["camera_path"], "active": e["active"]} for t, e in self._publishers.items()}


if '_viewport_pub' not in globals():
    _viewport_pub = _ViewportCameraPublisher()


def _isaac_sim_data_file(ext_prefix: str, relative: str) -> str:
    """Find a data file shipped inside the running Isaac Sim, or return ``""``.

    Looks for ``<extscache>/<ext_prefix>-<version>/<relative>`` next to the imported
    ``isaacsim`` package (pip installs) and under ``$ISAACSIM_PATH`` / ``$ISAAC_SIM_PATH``
    / ``$ISAAC_PATH`` (standalone installs). The newest matching extension version wins,
    so nothing depends on where the environment lives or which release it is.
    """
    import importlib.util

    roots = []
    try:
        spec = importlib.util.find_spec("isaacsim")
    except (ImportError, ValueError):
        spec = None
    for location in (spec.submodule_search_locations or []) if spec else []:
        roots.append(os.path.join(location, "extscache"))
    for name in ("ISAACSIM_PATH", "ISAAC_SIM_PATH", "ISAAC_PATH"):
        base = os.environ.get(name, "").strip()
        if base:
            roots.extend([os.path.join(base, "extscache"), os.path.join(base, "kit", "extscache")])
    def version_of(match):
        folder = os.path.relpath(match, root).split(os.sep)[0]
        numbers = folder[len(ext_prefix) + 1 :].split("+")[0].split("-")[0].split(".")
        return tuple(int(n) if n.isdigit() else -1 for n in numbers)

    for root in roots:
        matches = glob.glob(os.path.join(root, f"{ext_prefix}-*", relative))
        if matches:
            return max(matches, key=version_of)
    return ""


# Your own files, with no public download. Quick Start adds the gripper only when all of them exist.
GRIPPER_ASSETS = ("gripper/husarion_rg2/rg2_gripper_fixed.usd", "gripper/QuickChanger.usd")


def _local_asset(relpath):
    """Resolve an asset to a file:// or https:// URI.

    Bundled assets win, then the operator cache, then the pinned NVIDIA asset
    root for entries the manifest allows.  See ``asset_cache`` and
    ``docs/provenance/ASSETS.md``.
    """
    return resolve_uri(relpath)


def _assembly_layout(relpath):
    """Return the local path of an assembly layout JSON in the asset folder, or ``""``."""
    try:
        return str(resolve_path(relpath))
    except (AssetMissing, AssetCorrupt) as exc:
        print(f"Warning: assembly layout {relpath} not available.\n{exc}")
        return ""


def _local_asset_dir(relpath):
    """Resolve a local asset directory path (no file:// prefix, for os.listdir)."""
    return str(resolve_dir(relpath))


# Assembly definitions: each entry pairs a local objects folder with its assembly JSON
ASSEMBLIES = {
    "fmb1": {
        "folder": "objects/fmb1",
        "assembly_file": "fmb_assembly1.json",
    },
    "fmb2": {
        "folder": "objects/fmb2",
        "assembly_file": "fmb_assembly2.json",
    },
    "fmb3": {
        "folder": "objects/fmb3",
        "assembly_file": "fmb_assembly3.json",
    },
    # fmb4 = jenga (assembly_id fmb_assembly_4): 3-2-1 pyramid of authoritative 24x72x14 blocks
    # (base4 = a static jenga-block anchor; base* prefix → randomize skips it).
    "fmb4": {
        "folder": "objects/fmb4",
        "assembly_file": "fmb_assembly4.json",
    },
}
# Aliases — the operator refers to jenga as 'fmb_jenga'/'jenga'; map both to fmb4.
ASSEMBLIES["jenga"] = ASSEMBLIES["fmb4"]
ASSEMBLIES["fmb_jenga"] = ASSEMBLIES["fmb4"]

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
        "description": "Stop the simulation timeline in Isaac Sim.",
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
        "description": "Randomize the positions of non-base objects in /World/Objects.",
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
        "description": "Save current scene object poses to a JSON file.\n\nReturns:\n    result: \"success\" or \"failure\"\n    file_path: path to the saved JSON file\n    error: failure reason (only on failure)",
        "parameters": {
            "json_file_path": {
                "type": "string",
                "description": "Path to save file. If omitted, a timestamped filename is used."
            }
        }
    },
    "restore_scene_state": {
        "description": "Restore scene object poses from a previously saved JSON file.\n\nReturns:\n    result: \"success\" or \"failure\"\n    error: failure reason (only on failure)",
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
        "description": "Delete all objects from /World/Objects and the associated pose publisher graph.",
        "parameters": {}
    },
    "setup_pose_publisher": {
        "description": "Create an action graph to publish object poses to ROS2 topic 'objects_poses_sim'.",
        "parameters": {}
    },
    "start_ros_driver": {
        "description": "Bring the ROS2 robot driver up/down/status so robot-motion tools (ros-mcp-server move_home/move_to_grasp/...) have a driver to talk to. Two backends: backend='fake' (default) uses scripts/launch_ur_fake.sh — the apt ur_robot_driver with fake/mock hardware, no Docker and no ~/ros2_ws needed, ready in a few seconds (sim only); backend='ursim' uses scripts/sim_bringup.sh — URSim docker + ur_bringup, hands-free no PolyScope clicks, ~110s, and is the path that supports mode='real' (lab UR5e, not yet enabled). Both end at /joint_states live + scaled_joint_trajectory_controller accepting FollowJointTrajectory, on the same DDS island (the ROS domain from config/ros_dds.env (default 0), Cyclone, localhost-only). Managed sim__launch sessions support only the fake backend; URSim uses shared host resources. The fake launcher refuses startup when another UR driver is detected. Stop the fake driver before stopping its simulator. Use once at startup.",
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
        "description": "One-click scene setup from public assets: loads the scene, imports NVIDIA's UR5e with action graphs, creates a workspace camera and starts simulation. Adds the RG2 gripper only when its files are in the asset folder; the result's gripper field says which happened. Uses CPU physics in the selected engine; an existing non-CPU World is refused. Use after new_stage to rebuild a disposable scene.",
        "parameters": {}
    },
    "start_recording": {
        "description": "Start real-time viewport video recording using ffmpeg. Records frames from the viewport render buffer without blocking physics. Returns immediately — call stop_recording to finalize the video.",
        "parameters": {
            "file_name": {
                "type": "string",
                "description": "Output filename (e.g. 'my_recording.mp4'). Saved to the configured VIDEOS_DIR. Defaults to 'realtime_recording.mp4'."
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
        "description": "Uses your own lab files from the asset folder, which cannot be downloaded (see docs/ASSETS.md). A placeholder bench is used without them. Load the Vention workstation bench as visual context under the robot. Additive (cleared by new_stage): references the operator-supplied or cached vention.usd at /World/Vention, or builds a simple placeholder bench when it is not installed, and places it so the tabletop top is exactly at z=0 — coincident with the work plane where the robot base and objects already sit (ROBOT_BASE_Z stays 0.0, so ROS pose parity is untouched). The default ground plane's z=0 collider is retained and only its visual is hidden. The bench is visual-only (no colliders) so it is safe to add while the sim is playing. Does not move the robot or alter any other tool.",
        "parameters": {}
    },
    "quick_start_with_workstation": {
        "description": "Uses your own lab files from the asset folder, which cannot be downloaded (see docs/ASSETS.md). A placeholder bench is used without them. Same as quick_start, then loads the Vention workstation bench (see load_workstation). One call builds the full robot+scene and drops the bench underneath. quick_start itself is unchanged.",
        "parameters": {}
    },
    "load_simple_room": {
        "description": "Load Isaac's standard Simple Room environment (floor, walls, window, lamp) as a backdrop. Additive (cleared by new_stage): references the standard Isaac Simple_Room asset onto /World/SimpleRoom at z=-0.11 so it matches the source scene under the robot-base-at-0 convention (the Vention bench feet land on the room floor). Hides the default ground plane visual while keeping its z=0 collider, so object/robot physics are unchanged. Does not move the robot. Best used together with load_workstation.",
        "parameters": {}
    },
    "quick_start_with_workstation_and_simple_room": {
        "description": "Uses your own lab files from the asset folder, which cannot be downloaded (see docs/ASSETS.md). A placeholder bench is used without them. Same as quick_start, then loads the Vention workstation bench AND the Simple Room backdrop. One call builds the full robot+scene, the bench under the robot, and the room around it. quick_start itself is unchanged.",
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

# Tool name -> handler method is resolved at registration time from the stable
# `_cmd_<name>` convention (see on_startup), so no separate handler map is needed.

from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.core.prims import Articulation as ArticulationView
from isaacsim.storage.native import get_assets_root_path
from isaacsim.core.utils.numpy.rotations import euler_angles_to_quats
from isaacsim.core.prims import SingleArticulation as Articulation
import omni.graph.core as og
import omni.usd
from pxr import UsdGeom, Gf, Sdf, Usd, UsdPhysics, UsdShade, PhysxSchema
from scipy.spatial.transform import Rotation as R


class LogRedirector:
    """Redirects stdout/stderr to both the original stream and a UI callback."""
    _ur5e_log_redirector = True
    def __init__(self, original, callback, throttle_seconds=0.0):
        self._original = original
        self._callback = callback
        self._throttle = throttle_seconds
        self._last_msgs = {}  # dedup cache: msg -> timestamp

    def write(self, text):
        self._original.write(text)
        stripped = text.strip()
        if not stripped:
            return
        if self._throttle > 0:
            import time as _t
            now = _t.monotonic()
            if stripped in self._last_msgs and now - self._last_msgs[stripped] < self._throttle:
                return
            self._last_msgs[stripped] = now
            if len(self._last_msgs) > 200:
                cutoff = now - self._throttle * 3
                self._last_msgs = {k: v for k, v in self._last_msgs.items() if v > cutoff}
        self._callback(text.rstrip("\n"))

    def flush(self):
        self._original.flush()


# Module-level handle to the live extension instance, so socket-driven
# execute_python_code can drive individual build steps (load_ur5e, import_adapter,
# attach_rg2_to_ur5e, ...) one at a time:  from ur5e_dt import extension as E; E._EXT_INSTANCE.load_ur5e()
_EXT_INSTANCE = None


class DigitalTwin(omni.ext.IExt):
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

    def create_ui(self):
        with ui.VStack(spacing=5):
            self._sim_setup_collapse = ui.CollapsableFrame(title="Simulation Setup", collapsed=False, height=0)
            self._sim_setup_collapse.set_collapsed_changed_fn(
                lambda collapsed: None if collapsed else self._refresh_viewport_camera_list()
            )
            with self._sim_setup_collapse:
                with ui.VStack(spacing=5, height=0):
                    with ui.HStack(spacing=5):
                        ui.Button("Load Scene", width=100, height=35, clicked_fn=lambda: asyncio.ensure_future(self.load_scene()))
                        self._viewport_toggle_btn = ui.Button("Disable Viewport", width=140, height=35, clicked_fn=self._toggle_viewport_rendering)
                        ui.Button("Quick Start", width=120, height=35, clicked_fn=lambda: asyncio.ensure_future(self.quick_start()))
                    with ui.HStack(spacing=5):
                        ui.Button("Load Workstation", width=140, height=35,
                                  clicked_fn=lambda: asyncio.ensure_future(self.load_workstation()),
                                  tooltip="Add the Vention bench under the robot (tabletop top at z=0; robot/objects unchanged). Hides the grid visual.")
                        ui.Button("Import Simple Room", width=160, height=35,
                                  clicked_fn=lambda: asyncio.ensure_future(self.load_simple_room()),
                                  tooltip="Add Isaac's Simple Room backdrop (floor/walls/window) at z=-0.11. Hides the grid visual.")
                    with ui.HStack(spacing=5):
                        ui.Button("Quick Start with Workstation", width=210, height=35,
                                  clicked_fn=lambda: asyncio.ensure_future(self.quick_start_with_workstation()),
                                  tooltip="Run Quick Start, then load the Vention workstation bench (your own lab file; a placeholder is used without it).")
                        ui.Button("Quick Start with Workstation + Simple Room", width=320, height=35,
                                  clicked_fn=lambda: asyncio.ensure_future(self.quick_start_with_workstation_and_simple_room()),
                                  tooltip="Run Quick Start, then load the Vention bench AND the Simple Room backdrop.")
                    with ui.HStack(spacing=5):
                        ui.Button("Dark Studio Ground", width=160, height=30, clicked_fn=self._apply_dark_studio_ground)
                    with ui.HStack(spacing=5):
                        self._viewport_pub_checkbox = ui.CheckBox(width=20)
                        self._viewport_pub_checkbox.model.set_value(True)
                        ui.Label("Viewport Publisher (default, zero GPU cost)", alignment=ui.Alignment.LEFT)
                    with ui.HStack(spacing=5):
                        ui.Label("Active Viewport:", alignment=ui.Alignment.LEFT, width=110)
                        self._viewport_cam_frame = ui.Frame(width=220)
                        with self._viewport_cam_frame:
                            self._viewport_cam_combo = ui.ComboBox(0, "/World/workspace_camera_sim", width=220)
                        ui.Button("Undo", width=55, height=22, clicked_fn=self._undo_viewport_camera, tooltip="Switch back to the previously active viewport camera")
                    # Auto-populate the dropdown with current stage cameras at build time
                    self._refresh_viewport_camera_list()

            with ui.CollapsableFrame(title="UR5e Control", collapsed=False, height=0):
                with ui.VStack(spacing=5, height=0):
                    with ui.HStack(spacing=5):
                        ui.Button("Import UR5e", width=100, height=35, clicked_fn=lambda: asyncio.ensure_future(self.load_ur5e()))
                        ui.Button("Setup UR5e Action Graph", width=200, height=35, clicked_fn=lambda: asyncio.ensure_future(self.setup_action_graph()))
                        ui.Button("Setup UR5e Force Publisher", width=200, height=35, clicked_fn=self.setup_force_publish_action_graph)

            with ui.CollapsableFrame(title="RG2 Gripper", collapsed=False, height=0):
                with ui.VStack(spacing=5, height=0):
                    with ui.HStack(spacing=5):
                        ui.Button("Import RG2 Gripper", width=150, height=35, clicked_fn=self.import_rg2_gripper)
                        ui.Button("Attach Gripper to UR5e", width=180, height=35, clicked_fn=self.attach_rg2_to_ur5e)
                        ui.Button("Setup Gripper Action Graph", width=200, height=35, clicked_fn=self.setup_gripper_action_graph)

            # Intel RealSense Camera
            with ui.CollapsableFrame(title="Intel RealSense Camera", collapsed=False, height=0):
                with ui.VStack(spacing=5, height=0):
                    with ui.HStack(spacing=5):
                        ui.Button("Import RealSense Camera", width=170, height=35, clicked_fn=self.import_realsense_camera)
                        ui.Button("Attach Camera to UR5e", width=160, height=35, clicked_fn=self.attach_camera_to_ur5e)
                        ui.Button("Setup Camera Action Graph", width=200, height=35, clicked_fn=self.setup_camera_action_graph)

            with ui.CollapsableFrame(title="Objects", collapsed=False, height=0):
                with ui.VStack(spacing=5, height=0):
                    with ui.CollapsableFrame(title="Import", name="subFrame", collapsed=False, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Assembly:", alignment=ui.Alignment.LEFT, width=100)
                                assembly_names = list(ASSEMBLIES.keys())
                                self._assembly_combo = ui.ComboBox(0, *assembly_names, width=150)
                                self._assembly_combo.model.add_item_changed_fn(
                                    lambda m, _: self._on_assembly_changed(m)
                                )
                            with ui.HStack(spacing=5):
                                ui.Button("Add Objects", width=150, height=35, clicked_fn=self.add_objects)
                                ui.Button("Add ArUco Markers", width=180, height=35, clicked_fn=self.add_aruco_markers)
                            with ui.HStack(spacing=5):
                                ui.Button("Setup Pose Publisher", width=180, height=35, clicked_fn=self.create_pose_publisher)
                                ui.Button("Delete Objects", width=150, height=35, clicked_fn=self.delete_objects)
                    with ui.CollapsableFrame(title="Scene State", name="subFrame", collapsed=True, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Button("Sync Real Poses", width=180, height=35, clicked_fn=self.sync_real_poses)
                            with ui.HStack(spacing=5):
                                ui.Button("Assemble", width=120, height=35, clicked_fn=self.assemble_objects)
                                ui.Button("Disassemble", width=120, height=35, clicked_fn=self.disassemble_objects)
                                ui.Button("Randomize Poses", width=150, height=35, clicked_fn=self.randomize_object_poses)
                            with ui.HStack(spacing=5):
                                ui.Button("Save State", width=120, height=35,
                                    clicked_fn=lambda: self._cmd_save_scene_state())
                                ui.Button("Restore State", width=120, height=35,
                                    clicked_fn=lambda: self._cmd_restore_scene_state())
                            with ui.CollapsableFrame(title="Exclude / Include Objects", collapsed=True, height=0):
                                self._visibility_frame = ui.Frame(build_fn=self._build_visibility_ui)

            # Additional Camera
            with ui.CollapsableFrame(title="Additional Camera", collapsed=True, height=0):
                with ui.VStack(spacing=5, height=0):
                    ui.Label("Camera Configuration", alignment=ui.Alignment.LEFT)
                    with ui.VStack(spacing=5):
                        with ui.HStack(spacing=5):
                            self._workspace_checkbox = ui.CheckBox(width=20)
                            self._workspace_checkbox.model.set_value(False)
                            ui.Label("Workspace Camera", alignment=ui.Alignment.LEFT, width=120)
                        with ui.HStack(spacing=5):
                            self._custom_checkbox = ui.CheckBox(width=20)
                            ui.Label("Custom Camera", alignment=ui.Alignment.LEFT, width=120)
                    with ui.HStack(spacing=5):
                        ui.Label("Camera Prim Path:", alignment=ui.Alignment.LEFT, width=120)
                        self._custom_camera_prim_field = ui.StringField(width=200)
                        self._custom_camera_prim_field.model.set_value("/World/custom_camera")
                    with ui.HStack(spacing=5):
                        ui.Label("ROS2 Topic Name:", alignment=ui.Alignment.LEFT, width=120)
                        self._custom_camera_topic_field = ui.StringField(width=200)
                        self._custom_camera_topic_field.model.set_value("custom_camera")
                    with ui.HStack(spacing=5):
                        ui.Label("Resolution:", alignment=ui.Alignment.LEFT, width=120)
                        self._resolution_combo = ui.ComboBox(0, "640x480", "1280x720", "1920x1080", width=150)
                    with ui.HStack(spacing=5):
                        ui.Button("Create Camera", width=150, height=35, clicked_fn=self.create_additional_camera)
                        ui.Button("Create Action Graph", width=180, height=35, clicked_fn=self.create_additional_camera_actiongraph)

            # Lighting & Environment
            with ui.CollapsableFrame(title="Lighting & Environment", collapsed=True, height=0):
                with ui.VStack(spacing=5, height=0):
                    with ui.CollapsableFrame(title="Lighting Preset", name="subFrame", collapsed=False, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Preset:", alignment=ui.Alignment.LEFT, width=80)
                                self._lighting_preset_combo = ui.ComboBox(
                                    0, "Default Dome", "Studio (3-Point)", "Dramatic (Sun + Fill)",
                                    "Soft Diffuse", "HDRI: Photo Studio", "HDRI: Stormy Sky",
                                    width=200,
                                )
                            ui.Button("Apply Lighting Preset", height=35, clicked_fn=self._apply_lighting_preset)
                    with ui.CollapsableFrame(title="Manual Light Controls", name="subFrame", collapsed=True, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Dome Intensity:", alignment=ui.Alignment.LEFT, width=120)
                                self._dome_intensity_slider = ui.FloatSlider(min=0, max=5000, step=50, width=200)
                                self._dome_intensity_slider.model.set_value(1000.0)
                            ui.Button("Set Dome Intensity", height=30, clicked_fn=self._set_dome_intensity)
                            with ui.HStack(spacing=5):
                                ui.Label("Color Temp (K):", alignment=ui.Alignment.LEFT, width=120)
                                self._dome_temp_slider = ui.FloatSlider(min=2000, max=10000, step=100, width=200)
                                self._dome_temp_slider.model.set_value(6500.0)
                            with ui.HStack(spacing=5):
                                self._dome_temp_enable = ui.CheckBox(width=20)
                                self._dome_temp_enable.model.set_value(False)
                                ui.Label("Enable Color Temperature", alignment=ui.Alignment.LEFT)
                            ui.Button("Set Color Temperature", height=30, clicked_fn=self._set_dome_color_temp)
                            ui.Separator(height=5)
                            with ui.HStack(spacing=5):
                                ui.Label("Add Light:", alignment=ui.Alignment.LEFT, width=80)
                                self._add_light_combo = ui.ComboBox(0, "Rect Light", "Sphere Light", "Distant Light", width=150)
                            with ui.HStack(spacing=5):
                                ui.Label("Intensity:", alignment=ui.Alignment.LEFT, width=80)
                                self._add_light_intensity = ui.FloatField(width=100)
                                self._add_light_intensity.model.set_value(500.0)
                            ui.Button("Add Light", height=30, clicked_fn=self._add_manual_light)
                            ui.Button("Remove All Extra Lights", height=30, clicked_fn=self._remove_extra_lights)
                    with ui.CollapsableFrame(title="Environment", name="subFrame", collapsed=False, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Preset:", alignment=ui.Alignment.LEFT, width=100)
                                self._env_combo = ui.ComboBox(
                                    0, *list(ENVIRONMENTS.keys()), width=250,
                                )
                            with ui.HStack(spacing=5):
                                ui.Spacer(width=100)
                                self._env_hide_ground = ui.CheckBox(width=20)
                                self._env_hide_ground.model.set_value(True)
                                ui.Label("Hide flat ground plane when loading", alignment=ui.Alignment.LEFT)
                            ui.Button("Load Environment", height=35, clicked_fn=self._load_environment)
                    with ui.CollapsableFrame(title="Ground Plane", name="subFrame", collapsed=False, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Grid Tint:", alignment=ui.Alignment.LEFT, width=100)
                                self._grid_tint_combo = ui.ComboBox(
                                    0, *list(GRID_TINT_PRESETS.keys()), width=150,
                                )
                            with ui.HStack(spacing=5):
                                ui.Label("Custom Hex:", alignment=ui.Alignment.LEFT, width=100)
                                self._grid_tint_hex = ui.StringField(width=150)
                                self._grid_tint_hex.model.set_value("#FFFFFF")
                            ui.Label("Hex used only when 'Custom Hex' is selected", style={"color": 0xFF888888, "font_size": 11}, height=0)
                            ui.Button("Swap Ground Plane", height=35, clicked_fn=self._swap_ground_plane)
                    with ui.CollapsableFrame(title="Ground Appearance", name="subFrame", collapsed=True, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Ground Style:", alignment=ui.Alignment.LEFT, width=100)
                                self._ground_style_combo = ui.ComboBox(
                                    0, "Default Grey", "White Studio", "Dark Studio",
                                    "Concrete", "Invisible (Physics Only)",
                                    width=200,
                                )
                            with ui.HStack(spacing=5):
                                ui.Label("Roughness:", alignment=ui.Alignment.LEFT, width=100)
                                self._ground_roughness_slider = ui.FloatSlider(min=0.0, max=1.0, step=0.05, width=200)
                                self._ground_roughness_slider.model.set_value(0.5)
                            ui.Button("Apply Ground Style", height=35, clicked_fn=self._apply_ground_style)
                    with ui.CollapsableFrame(title="Background", name="subFrame", collapsed=True, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Background:", alignment=ui.Alignment.LEFT, width=100)
                                self._bg_combo = ui.ComboBox(
                                    0, "From Dome Light (HDRI/Default)", "Solid White",
                                    "Solid Black", "Solid Light Grey",
                                    width=250,
                                )
                            ui.Button("Apply Background", height=35, clicked_fn=self._apply_background)
                    ui.Separator(height=5)
                    ui.Button("Reset to Defaults", height=30, clicked_fn=self._reset_lighting_defaults)

            # Video Recording
            self._rec_collapse = ui.CollapsableFrame(title="Video Recording", collapsed=True, height=0)
            self._rec_collapse.set_collapsed_changed_fn(lambda collapsed: None if collapsed else self._refresh_camera_list())
            with self._rec_collapse:
                with ui.VStack(spacing=5, height=0):
                    with ui.HStack(spacing=5):
                        ui.Label("Camera:", alignment=ui.Alignment.LEFT, width=80)
                        self._rec_camera_frame = ui.Frame(width=250)
                        with self._rec_camera_frame:
                            self._rec_camera_combo = ui.ComboBox(0, "Active Viewport", width=250)
                    with ui.HStack(spacing=5):
                        ui.Label("Filename:", alignment=ui.Alignment.LEFT, width=80)
                        self._rec_filename_field = ui.StringField(width=250)
                        self._rec_filename_field.model.set_value("")
                    ui.Label("Leave empty for auto: isaacsim_YYYY-MM-DD_HH-MM-SS.mp4", style={"color": 0xFF888888, "font_size": 11}, height=0)
                    with ui.HStack(spacing=5):
                        ui.Label("Duration (s):", alignment=ui.Alignment.LEFT, width=80)
                        self._rec_duration_field = ui.FloatField(width=80)
                        self._rec_duration_field.model.set_value(0.0)
                    ui.Label("0 = manual stop (ffmpeg only). Per-physics-step always uses this duration.", style={"color": 0xFF888888, "font_size": 11}, height=0)
                    with ui.CollapsableFrame(title="Real-Time Recording (ffmpeg)", name="subFrame", collapsed=False, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                self._rt_record_btn = ui.Button("Start Recording", width=150, height=35, clicked_fn=self._start_rt_from_ui)
                                self._rt_stop_btn = ui.Button("Stop Recording", width=150, height=35, clicked_fn=self._stop_realtime_recording, enabled=False)
                    with ui.CollapsableFrame(title="Per-Physics-Step Recording (no dropped frames)", name="subFrame", collapsed=True, height=0):
                        with ui.VStack(spacing=5, height=0):
                            with ui.HStack(spacing=5):
                                ui.Label("Resolution:", alignment=ui.Alignment.LEFT, width=100)
                                self._offline_res_combo = ui.ComboBox(2, "640x480", "1280x720", "1920x1080", "2560x1440", "3840x2160", width=150)
                            with ui.HStack(spacing=5):
                                ui.Label("FPS:", alignment=ui.Alignment.LEFT, width=100)
                                self._offline_fps_field = ui.IntField(width=80)
                                self._offline_fps_field.model.set_value(30)
                            ui.Button("Record", height=35, clicked_fn=lambda: asyncio.ensure_future(self._record_offline_from_ui()))
                    self._last_video_label = ui.Label("", alignment=ui.Alignment.LEFT, word_wrap=True, height=0)
                    ui.Button("Open Videos Folder", height=25, clicked_fn=self._open_videos_folder)

            with ui.CollapsableFrame(title="Logs", collapsed=True, height=0):
                with ui.VStack(spacing=3, height=0):
                    self._log_tab_style = {
                        "RadioButton": {
                            "margin": 2, "border_radius": 2, "font_size": 16,
                            "background_color": 0xFF212121, "color": 0xFF444444,
                        },
                        "RadioButton.Label": {"font_size": 16, "color": 0xFF777777},
                        "RadioButton:checked": {"background_color": 0xFF3A3A3A, "color": 0xFF222222},
                        "RadioButton.Label:checked": {"color": 0xFFCCCCCC},
                    }
                    self._log_tab_collection = ui.RadioCollection()
                    with ui.HStack(spacing=3, height=30, style=self._log_tab_style):
                        ui.RadioButton(text="Extension", width=80,
                            radio_collection=self._log_tab_collection,
                            clicked_fn=lambda: self._switch_log_tab("extension"))
                        ui.RadioButton(text="System", width=80,
                            radio_collection=self._log_tab_collection,
                            clicked_fn=lambda: self._switch_log_tab("system"))
                    self._log_stack = ui.ZStack(height=100)
                    with self._log_stack:
                        self._ext_log_scroll = ui.ScrollingFrame(
                            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                            vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON,
                            style={"background_color": 0xFF24211F},
                        )
                        with self._ext_log_scroll:
                            self._ext_log_vstack = ui.VStack(spacing=0)
                        self._sys_log_scroll = ui.ScrollingFrame(
                            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                            vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON,
                            visible=False,
                            style={"background_color": 0xFF24211F},
                        )
                        with self._sys_log_scroll:
                            self._sys_log_vstack = ui.VStack(spacing=0)
                    with ui.HStack(spacing=5):
                        ui.Button("Copy Logs", width=120, height=30, clicked_fn=self._copy_logs)
                        ui.Button("Clear Logs", width=120, height=30, clicked_fn=self._clear_logs)

        self._active_log_tab = "extension"
        self._ext_log_lines = []
        self._sys_log_lines = []
        self._log_dirty = False
        # One per-frame, main-thread drain of the log buffers into the UI panels (see
        # _append_*/_drain_log_buffer). Subscribed BEFORE _setup_log_redirect installs the producers
        # so the very first captured line is drained safely on the next frame, never inline.
        try:
            import omni.kit.app
            self._log_drain_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
                self._drain_log_buffer, name="ur5e_dt_log_drain")
        except Exception:
            self._log_drain_sub = None
        # The extension can be enabled from run_python, which temporarily captures
        # sys.stdout. Install the redirector on the next tick, after that capture
        # restores its stream, so a reload cannot retain a retired instance.
        self._run_on_main_thread(self._setup_log_redirect)

        # Re-subscribe physics callbacks if action graphs already exist (e.g. after hot-reload)
        self._resubscribe_physics_callbacks()

        # Subscribe to stage lifecycle events to clean up World singleton on any new stage
        # (Ctrl+N, File > New, or programmatic new_stage calls)
        self._subscribe_stage_events()

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

    def _setup_log_redirect(self):
        if self._closing:
            return
        # A prior extension module may have been reloaded, so class identity is
        # not reliable. The marker also handles an inactive retired redirector.
        while getattr(sys.stdout, "_ur5e_log_redirector", False):
            sys.stdout = sys.stdout._original
        while getattr(sys.stderr, "_ur5e_log_redirector", False):
            sys.stderr = sys.stderr._original
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self._stdout_redirector = LogRedirector(self._orig_stdout, self._append_ext_log)
        self._stderr_redirector = LogRedirector(self._orig_stderr, lambda t: self._append_ext_log(f"[ERROR] {t}"), throttle_seconds=2.0)
        sys.stdout = self._stdout_redirector
        sys.stderr = self._stderr_redirector
        logging = carb.logging.acquire_logging()
        self._carb_log_sub = logging.add_logger(self._on_carb_log)

    def _on_carb_log(self, source, level, filename, linenum, msg):
        # carb levels: VERBOSE=-2, INFO=-1, WARN=0, ERROR=1, FATAL=2
        import time as _t
        now = _t.monotonic()
        # Throttle: skip duplicate messages within 2 seconds
        key = (source, level, msg)
        last = getattr(self, '_carb_log_last', {})
        if key in last and now - last[key] < 2.0:
            return
        last[key] = now
        # Evict stale entries periodically
        if len(last) > 200:
            cutoff = now - 5.0
            last = {k: v for k, v in last.items() if v > cutoff}
        self._carb_log_last = last

        if level >= carb.logging.LEVEL_ERROR:
            self._append_sys_log(f"[ERROR] [{source}] {msg}")
        elif level == carb.logging.LEVEL_WARN:
            self._append_sys_log(f"[WARN] [{source}] {msg}")
        # elif level == carb.logging.LEVEL_INFO:
        #     self._append_sys_log(f"[INFO] [{source}] {msg}")
        # else:
        #     self._append_sys_log(f"[VERBOSE] [{source}] {msg}")

    def _teardown_log_redirect(self):
        stdout = getattr(self, "_stdout_redirector", None)
        stderr = getattr(self, "_stderr_redirector", None)
        # run_python may be temporarily capturing stdout when the extension is
        # disabled. Do not replace that capture; make a subsequently restored
        # old redirector harmless instead.
        if stdout is not None:
            stdout._callback = lambda _text: None
            if sys.stdout is stdout:
                sys.stdout = self._orig_stdout
        if stderr is not None:
            stderr._callback = lambda _text: None
            if sys.stderr is stderr:
                sys.stderr = self._orig_stderr
        if hasattr(self, "_carb_log_sub") and self._carb_log_sub is not None:
            logging = carb.logging.acquire_logging()
            logging.remove_logger(self._carb_log_sub)
            self._carb_log_sub = None

    def _log_color(self, text):
        # Matches Isaac Sim script editor colors (0xAABBGGRR format)
        if "[ERROR]" in text or "[FATAL]" in text:
            return 0xFFAAB1F6  # #F6B1AA - light coral
        elif "[WARN]" in text:
            return 0xFF4ACBDF  # #DFCB4A - yellow/gold
        # elif "[INFO]" in text:
        #     return 0xFFEBBA79  # #79BAEB - light blue
        # elif "[VERBOSE]" in text:
        #     return 0xFFBABABA  # #BABABA - gray
        return 0xFFFFFFFF  # white

    def _add_log_label(self, vstack, scroll, text):
        with vstack:
            ui.Label(text, alignment=ui.Alignment.LEFT_TOP, word_wrap=True,
                     style={"color": self._log_color(text), "font_size": 13, "margin": 0},
                     height=0)
        scroll.scroll_y = scroll.scroll_y_max + 100

    def _rebuild_log_vstack(self, vstack, lines):
        vstack.clear()
        with vstack:
            for line in lines:
                ui.Label(line, alignment=ui.Alignment.LEFT_TOP, word_wrap=True,
                         style={"color": self._log_color(line), "font_size": 13, "margin": 0},
                         height=0)

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

    # Log capture is a producer/consumer queue, NOT per-line UI scheduling. The producers
    # (_on_carb_log + the stdout/stderr LogRedirector) can fire from ANY thread — including PhysX
    # worker threads AND the main thread synchronously inside world.reset()/the first physics step.
    # They must therefore touch ZERO omni.ui (touching omni.ui off-frame/off-main-thread crashed the
    # process — see _run_on_main_thread). So they only append to a list (GIL-atomic) and flip a dirty
    # flag; the single per-frame _drain_log_buffer (subscribed to the app update stream, runs on the
    # main thread between frames) rebuilds the capped UI. This also avoids scheduling thousands of
    # call_soon callbacks during the reset log-storm.
    def _append_ext_log(self, text):
        self._ext_log_lines.append(text)
        if len(self._ext_log_lines) > 500:
            self._ext_log_lines = self._ext_log_lines[-500:]
        self._log_dirty = True

    def _append_sys_log(self, text):
        self._sys_log_lines.append(text)
        if len(self._sys_log_lines) > 500:
            self._sys_log_lines = self._sys_log_lines[-500:]
        self._log_dirty = True

    def _drain_log_buffer(self, _event=None):
        """Main-thread, once-per-frame: rebuild the log panels from the buffers if dirty."""
        if not getattr(self, "_log_dirty", False):
            return
        self._log_dirty = False
        try:
            if getattr(self, "_ext_log_vstack", None):
                self._rebuild_log_vstack(self._ext_log_vstack, self._ext_log_lines)
                if getattr(self, "_ext_log_scroll", None):
                    self._ext_log_scroll.scroll_y = self._ext_log_scroll.scroll_y_max + 100
            if getattr(self, "_sys_log_vstack", None):
                self._rebuild_log_vstack(self._sys_log_vstack, self._sys_log_lines)
                if getattr(self, "_sys_log_scroll", None):
                    self._sys_log_scroll.scroll_y = self._sys_log_scroll.scroll_y_max + 100
        except Exception:
            pass

    def _switch_log_tab(self, tab):
        self._active_log_tab = tab
        is_ext = tab == "extension"
        self._ext_log_scroll.visible = is_ext
        self._sys_log_scroll.visible = not is_ext

    def _copy_logs(self):
        lines = self._ext_log_lines if self._active_log_tab == "extension" else self._sys_log_lines
        try:
            import subprocess
            text = "\n".join(lines)
            proc = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
            proc.communicate(text.encode())
        except Exception:
            import omni.kit.clipboard
            omni.kit.clipboard.copy("\n".join(lines))

    def _clear_logs(self):
        if self._active_log_tab == "extension":
            self._ext_log_lines.clear()
            self._ext_log_vstack.clear()
        else:
            self._sys_log_lines.clear()
            self._sys_log_vstack.clear()

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

    # ==================== Lighting & Environment ====================

    def _clear_all_lights(self):
        """Remove all light prims under /World/Lights/."""
        from pxr import UsdLux
        stage = omni.usd.get_context().get_stage()
        lights_prim = stage.GetPrimAtPath("/World/Lights")
        if lights_prim and lights_prim.IsValid():
            stage.RemovePrim("/World/Lights")
        # Also remove the default dome light if it exists
        default_dome = stage.GetPrimAtPath("/World/defaultDomeLight")
        if default_dome and default_dome.IsValid():
            stage.RemovePrim("/World/defaultDomeLight")

    def _ensure_lights_scope(self):
        """Ensure /World/Lights scope exists."""
        from pxr import UsdGeom
        stage = omni.usd.get_context().get_stage()
        lights_prim = stage.GetPrimAtPath("/World/Lights")
        if not lights_prim or not lights_prim.IsValid():
            UsdGeom.Scope.Define(stage, "/World/Lights")

    def _apply_lighting_preset(self):
        """Apply the selected lighting preset, clearing previous lights first."""
        from pxr import UsdLux, UsdGeom, Gf, Sdf
        stage = omni.usd.get_context().get_stage()
        idx = self._lighting_preset_combo.model.get_item_value_model().as_int
        presets = [
            "Default Dome", "Studio (3-Point)", "Dramatic (Sun + Fill)",
            "Soft Diffuse", "HDRI: Photo Studio", "HDRI: Stormy Sky",
        ]
        preset = presets[idx]

        self._clear_all_lights()
        self._ensure_lights_scope()

        if preset == "Default Dome":
            dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeLight")
            dome.CreateIntensityAttr().Set(1000.0)
            print("Applied preset: Default Dome (intensity=1000)")

        elif preset == "Studio (3-Point)":
            # Key light — strong, from upper-right-front
            key = UsdLux.RectLight.Define(stage, "/World/Lights/KeyLight")
            key.CreateIntensityAttr().Set(800.0)
            key.CreateWidthAttr().Set(1.0)
            key.CreateHeightAttr().Set(1.0)
            xform = UsdGeom.Xform(key.GetPrim())
            xform.ClearXformOpOrder()
            xform.AddTranslateOp().Set(Gf.Vec3d(1.5, -1.0, 2.0))
            xform.AddRotateXYZOp().Set(Gf.Vec3f(-45, 30, 0))

            # Fill light — softer, from upper-left
            fill = UsdLux.RectLight.Define(stage, "/World/Lights/FillLight")
            fill.CreateIntensityAttr().Set(300.0)
            fill.CreateWidthAttr().Set(1.5)
            fill.CreateHeightAttr().Set(1.5)
            xform = UsdGeom.Xform(fill.GetPrim())
            xform.ClearXformOpOrder()
            xform.AddTranslateOp().Set(Gf.Vec3d(-1.5, -0.5, 1.8))
            xform.AddRotateXYZOp().Set(Gf.Vec3f(-40, -30, 0))

            # Rim/back light — highlights edges from behind
            rim = UsdLux.RectLight.Define(stage, "/World/Lights/RimLight")
            rim.CreateIntensityAttr().Set(500.0)
            rim.CreateWidthAttr().Set(0.8)
            rim.CreateHeightAttr().Set(0.8)
            xform = UsdGeom.Xform(rim.GetPrim())
            xform.ClearXformOpOrder()
            xform.AddTranslateOp().Set(Gf.Vec3d(0.0, 1.5, 2.5))
            xform.AddRotateXYZOp().Set(Gf.Vec3f(-60, 180, 0))

            # Dim dome fill for ambient
            dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeFill")
            dome.CreateIntensityAttr().Set(150.0)
            print("Applied preset: Studio (3-Point) — key/fill/rim + dim dome fill")

        elif preset == "Dramatic (Sun + Fill)":
            sun = UsdLux.DistantLight.Define(stage, "/World/Lights/SunLight")
            sun.CreateIntensityAttr().Set(3000.0)
            sun.CreateAngleAttr().Set(0.53)  # sun-sized disc
            xform = UsdGeom.Xform(sun.GetPrim())
            xform.ClearXformOpOrder()
            xform.AddRotateXYZOp().Set(Gf.Vec3f(-55, 35, 0))

            dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeFill")
            dome.CreateIntensityAttr().Set(100.0)
            print("Applied preset: Dramatic (Sun + Fill) — strong directional + dim dome")

        elif preset == "Soft Diffuse":
            dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeLight")
            dome.CreateIntensityAttr().Set(2500.0)
            print("Applied preset: Soft Diffuse — high-intensity dome, no directional")

        elif preset.startswith("HDRI:"):
            hdri_map = {
                "HDRI: Photo Studio": _isaac_sim_data_file(
                    "omni.kit.widget.material_preview", "data/photo_studio_01_4k.hdr"
                ),
                "HDRI: Stormy Sky": _isaac_sim_data_file(
                    "omni.kit.tool.collect",
                    "data/test_stages/OM_55150/2/Assets/Skies/2022_1/Skies/Storm/approaching_storm.hdr",
                ),
            }
            hdr_path = hdri_map.get(preset, "")
            dome = UsdLux.DomeLight.Define(stage, "/World/Lights/DomeLight")
            dome.CreateIntensityAttr().Set(1000.0)
            if os.path.exists(hdr_path):
                dome.CreateTextureFileAttr().Set(hdr_path)
                print(f"Applied preset: {preset} — HDRI dome from {os.path.basename(hdr_path)}")
            else:
                print(
                    f"Warning: HDRI file for {preset!r} not found in this Isaac Sim install"
                    f"{' (' + hdr_path + ')' if hdr_path else ''}, using plain dome instead"
                )

        # Sync slider to match new dome intensity
        self._sync_dome_slider_from_stage()

    def _sync_dome_slider_from_stage(self):
        """Update the dome intensity slider to reflect the current stage value."""
        from pxr import UsdLux
        stage = omni.usd.get_context().get_stage()
        for prim in stage.Traverse():
            if prim.IsA(UsdLux.DomeLight):
                intensity = prim.GetAttribute("inputs:intensity").Get()
                if intensity is not None:
                    self._dome_intensity_slider.model.set_value(float(intensity))
                break

    def _set_dome_intensity(self):
        """Set intensity on all dome lights in the scene."""
        from pxr import UsdLux
        stage = omni.usd.get_context().get_stage()
        val = self._dome_intensity_slider.model.as_float
        found = False
        for prim in stage.Traverse():
            if prim.IsA(UsdLux.DomeLight):
                prim.GetAttribute("inputs:intensity").Set(val)
                found = True
        if found:
            print(f"Dome light intensity set to {val}")
        else:
            print("No dome light found. Apply a lighting preset first.")

    def _set_dome_color_temp(self):
        """Set color temperature on all dome lights."""
        from pxr import UsdLux
        stage = omni.usd.get_context().get_stage()
        temp = self._dome_temp_slider.model.as_float
        enable = self._dome_temp_enable.model.get_value_as_bool()
        found = False
        for prim in stage.Traverse():
            if prim.IsA(UsdLux.DomeLight):
                prim.GetAttribute("inputs:enableColorTemperature").Set(enable)
                if enable:
                    prim.GetAttribute("inputs:colorTemperature").Set(temp)
                found = True
        if found:
            if enable:
                print(f"Dome color temperature set to {temp}K")
            else:
                print("Dome color temperature disabled (using default white)")
        else:
            print("No dome light found. Apply a lighting preset first.")

    def _add_manual_light(self):
        """Add a single light to /World/Lights/ with auto-incrementing name."""
        from pxr import UsdLux, UsdGeom, Gf
        stage = omni.usd.get_context().get_stage()
        self._ensure_lights_scope()

        idx = self._add_light_combo.model.get_item_value_model().as_int
        light_types = ["Rect Light", "Sphere Light", "Distant Light"]
        light_type = light_types[idx]
        intensity = self._add_light_intensity.model.as_float

        # Find next available index
        i = 0
        while stage.GetPrimAtPath(f"/World/Lights/Manual_{light_type.replace(' ', '')}_{i}"):
            i += 1
        path = f"/World/Lights/Manual_{light_type.replace(' ', '')}_{i}"

        if light_type == "Rect Light":
            light = UsdLux.RectLight.Define(stage, path)
            light.CreateWidthAttr().Set(1.0)
            light.CreateHeightAttr().Set(1.0)
        elif light_type == "Sphere Light":
            light = UsdLux.SphereLight.Define(stage, path)
            light.CreateRadiusAttr().Set(0.2)
        elif light_type == "Distant Light":
            light = UsdLux.DistantLight.Define(stage, path)
            light.CreateAngleAttr().Set(0.53)

        light.CreateIntensityAttr().Set(intensity)

        # Place it above the scene so it's immediately useful
        xform = UsdGeom.Xform(light.GetPrim())
        xform.ClearXformOpOrder()
        xform.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 2.5))
        xform.AddRotateXYZOp().Set(Gf.Vec3f(-90, 0, 0))

        print(f"Added {light_type} at {path} (intensity={intensity}). Move it in the viewport to position.")

    def _remove_extra_lights(self):
        """Remove all manually added lights (under /World/Lights/Manual_*)."""
        from pxr import Usd
        stage = omni.usd.get_context().get_stage()
        lights_prim = stage.GetPrimAtPath("/World/Lights")
        if not lights_prim or not lights_prim.IsValid():
            print("No extra lights to remove.")
            return
        removed = 0
        for child in lights_prim.GetChildren():
            if child.GetName().startswith("Manual_"):
                stage.RemovePrim(child.GetPath())
                removed += 1
        print(f"Removed {removed} manual light(s).")

    _ground_mat_path = None  # cached path for the OmniPBR ground material

    def _apply_dark_studio_ground(self):
        """Quick shortcut: apply Dark Studio ground style with default roughness."""
        self._ground_style_combo.model.get_item_value_model().set_value(2)  # "Dark Studio"
        self._ground_roughness_slider.model.set_value(0.5)
        self._apply_ground_style()

    # ==================== Video Recording ====================

    def _open_videos_folder(self):
        """Open the videos output folder in the system file manager."""
        import subprocess
        folder = VIDEOS_DIR
        os.makedirs(folder, exist_ok=True)
        # Use dbus to open folder — avoids Qt plugin conflicts inside Isaac Sim
        env = os.environ.copy()
        env["DISPLAY"] = ":0"
        # Remove Isaac Sim's Qt plugin path that breaks external Qt apps
        for key in list(env.keys()):
            if "QT_PLUGIN" in key or "QT_QPA" in key:
                del env[key]
        subprocess.Popen(["xdg-open", folder], env=env)

    def _get_recording_filename(self):
        """Get filename from UI field, or generate auto name like ros2 recorder."""
        from datetime import datetime
        custom = self._rec_filename_field.model.as_string.strip()
        if custom:
            if not custom.endswith(".mp4"):
                custom += ".mp4"
            return custom
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        return f"isaacsim_{timestamp}.mp4"
        print(f"Opening: {folder}")

    def _refresh_camera_list(self):
        """Scan the stage for all Camera prims and rebuild the camera combo box."""
        from pxr import UsdGeom
        stage = omni.usd.get_context().get_stage()
        cameras = ["Active Viewport"]
        for prim in stage.Traverse():
            if prim.IsA(UsdGeom.Camera):
                cameras.append(str(prim.GetPath()))
        # Rebuild combo box inside its frame container
        self._rec_camera_frame.clear()
        with self._rec_camera_frame:
            self._rec_camera_combo = ui.ComboBox(0, *cameras, width=250)
        print(f"Found {len(cameras) - 1} camera(s): {cameras[1:]}")

    def _get_selected_camera_path(self):
        """Return the prim path of the selected camera, or None for active viewport."""
        model = self._rec_camera_combo.model
        idx = model.get_item_value_model().as_int
        if idx == 0:
            return None  # Active Viewport
        items = model.get_item_children()
        if idx < len(items):
            return model.get_item_value_model(items[idx]).as_string
        return None

    # --- Offline Recording (frame capture, blocks physics) ---

    async def _record_offline_from_ui(self):
        """Record per-physics-step video using settings from the UI panel."""
        duration = self._rec_duration_field.model.as_float
        if duration <= 0:
            print("Error: Set a duration > 0 for per-physics-step recording.")
            return
        fps = self._offline_fps_field.model.as_int
        res_idx = self._offline_res_combo.model.get_item_value_model().as_int
        res_options = ["640x480", "1280x720", "1920x1080", "2560x1440", "3840x2160"]
        w, h = [int(x) for x in res_options[res_idx].split("x")]
        camera_path = self._get_selected_camera_path()
        file_name = self._get_recording_filename().replace(".mp4", "")  # _record_offline appends .mp4
        await self._record_offline(duration_sec=duration, fps=fps, res_width=w, res_height=h,
                                    camera_path=camera_path, file_name=file_name)

    async def _record_offline(self, duration_sec=5, fps=30, res_width=1920, res_height=1080,
                               camera_path=None, output_dir=None, file_name="offline_recording"):
        """Record viewport to MP4 using per-physics-step frame capture."""
        if output_dir is None:
            output_dir = VIDEOS_DIR
        import omni.kit.app
        mgr = omni.kit.app.get_app().get_extension_manager()
        if not mgr.is_extension_enabled("omni.kit.capture.viewport"):
            mgr.set_extension_enabled_immediate("omni.kit.capture.viewport", True)
            await omni.kit.app.get_app().next_update_async()

        from omni.kit.capture.viewport import CaptureExtension, CaptureOptions
        import omni.kit.viewport.utility as vp_utils

        capture = CaptureExtension.get_instance()
        viewport = vp_utils.get_active_viewport()

        os.makedirs(output_dir, exist_ok=True)

        options = CaptureOptions()
        options.camera = camera_path if camera_path else viewport.camera_path.pathString
        options.output_folder = output_dir
        options.file_name = file_name
        options.file_type = ".mp4"
        options.fps = fps
        options.start_frame = 1
        options.end_frame = int(duration_sec * fps)
        options.res_width = res_width
        options.res_height = res_height
        options.overwrite_existing_frames = True
        capture.options = options

        print(f"Per-physics-step recording: {duration_sec}s ({options.end_frame} frames at {fps}fps, {res_width}x{res_height})...")
        if not capture.start():
            print("Error: Failed to start video capture")
            return None

        while not capture.done:
            await omni.kit.app.get_app().next_update_async()

        outputs = capture.get_outputs()
        if outputs:
            self._last_video_label.text = f"Saved: {outputs[0]}"
        print(f"Per-physics-step recording complete: {outputs}")
        return outputs

    async def _record_assemble_disassemble_test(self):
        """Offline test: assemble at 1s, disassemble at 3s, 5s total."""
        import omni.kit.app
        app = omni.kit.app.get_app()

        mgr = app.get_extension_manager()
        if not mgr.is_extension_enabled("omni.kit.capture.viewport"):
            mgr.set_extension_enabled_immediate("omni.kit.capture.viewport", True)
            await app.next_update_async()

        from omni.kit.capture.viewport import CaptureExtension, CaptureOptions
        import omni.kit.viewport.utility as vp_utils

        capture = CaptureExtension.get_instance()
        viewport = vp_utils.get_active_viewport()

        # Use UI settings for resolution
        res_idx = self._offline_res_combo.model.get_item_value_model().as_int
        res_options = ["640x480", "1280x720", "1920x1080", "2560x1440", "3840x2160"]
        w, h = [int(x) for x in res_options[res_idx].split("x")]
        fps = self._offline_fps_field.model.as_int
        camera_path = self._get_selected_camera_path()

        output_dir = VIDEOS_DIR
        os.makedirs(output_dir, exist_ok=True)

        total_frames = 5 * fps

        options = CaptureOptions()
        options.camera = camera_path if camera_path else viewport.camera_path.pathString
        options.output_folder = output_dir
        options.file_name = "assemble_disassemble_test"
        options.file_type = ".mp4"
        options.fps = fps
        options.start_frame = 1
        options.end_frame = total_frames
        options.res_width = w
        options.res_height = h
        options.overwrite_existing_frames = True
        capture.options = options

        print(f"Starting assemble/disassemble test ({w}x{h}, {fps}fps, 5s)...")
        if not capture.start():
            print("Error: Failed to start video capture")
            return

        frame = [0]
        assemble_done = [False]
        disassemble_done = [False]

        while not capture.done:
            await app.next_update_async()
            frame[0] += 1
            if frame[0] == 1 * fps and not assemble_done[0]:
                print("Test: Assembling objects...")
                self.assemble_objects()
                assemble_done[0] = True
            if frame[0] == 3 * fps and not disassemble_done[0]:
                print("Test: Disassembling objects...")
                self.disassemble_objects()
                disassemble_done[0] = True

        outputs = capture.get_outputs()
        if outputs:
            self._last_video_label.text = f"Saved: {outputs[0]}"
        print(f"Test recording complete: {outputs}")

    # --- Real-Time Recording (ffmpeg, non-blocking) ---
    # State lives in module-level _recording_mgr to survive hot-reloads.

    def _start_rt_from_ui(self):
        """Start real-time recording from UI. Auto-stops after duration if set."""
        if _recording_mgr.is_recording():
            return
        file_name = self._get_recording_filename()
        camera_path = self._get_selected_camera_path()
        duration = self._rec_duration_field.model.as_float
        self._start_realtime_recording(file_name=file_name, camera_path=camera_path)
        if duration > 0:
            asyncio.ensure_future(self._rt_auto_stop(duration, _recording_mgr.capture_token))

    async def _rt_auto_stop(self, duration, token):
        """Auto-stop real-time recording after duration seconds."""
        await asyncio.sleep(duration)
        if _recording_mgr.recording:
            self._stop_realtime_recording(token=token)

    def _start_realtime_recording(self, output_dir=None, file_name="realtime_recording.mp4",
                                   fps=30, camera_path=None):
        """Start piping viewport frames to ffmpeg in real-time."""
        if output_dir is None:
            output_dir = VIDEOS_DIR
        import subprocess
        import omni.kit.viewport.utility as vp_utils

        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, file_name)

        viewport = vp_utils.get_active_viewport()

        if camera_path:
            viewport.camera_path = camera_path

        w, h = viewport.get_texture_resolution()

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-nostats",
            "-f", "rawvideo",
            "-pix_fmt", "rgba",
            "-s", f"{w}x{h}",
            "-r", str(fps),
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast",
            "-crf", "18",
            output_path,
        ]
        import time as _time
        mgr = _recording_mgr
        mgr.ffmpeg_proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        mgr.recording = True
        mgr.output_path = output_path
        mgr.frame_count = 0
        mgr.start_time = _time.time()
        mgr.fps = fps
        mgr.last_print = 0
        mgr.error = None
        mgr.pacer = FramePacer(fps, _time.monotonic())
        mgr.recording_lock = threading.RLock()
        mgr.stopping = False
        token = object()
        mgr.capture_token = token

        def on_frame_change(event):
            with mgr.recording_lock:
                if not mgr.recording or mgr.ffmpeg_proc is None or mgr.capture_token is not token:
                    return
                if not mgr.pacer.request_capture(_time.monotonic()):
                    return
            try:
                vp_utils.capture_viewport_to_buffer(
                    viewport,
                    lambda buffer, size, width, height, fmt:
                        self._rt_on_capture(buffer, size, width, height, fmt, token),
                )
            except Exception as e:
                with mgr.recording_lock:
                    if mgr.capture_token is not token:
                        return
                    mgr.pacer.pending = False
                    mgr.error = f"could not capture viewport: {e}"
                self._stop_realtime_recording(pad=False, token=token)

        mgr.frame_sub = viewport.subscribe_to_frame_change(on_frame_change)
        mgr.save_state()

        self._rt_record_btn.enabled = False
        self._rt_stop_btn.enabled = True
        print(f"Real-time recording started: {w}x{h} @ {fps}fps → {output_path}")

    def _rt_on_capture(self, buffer, buffer_size, width, height, fmt, token):
        """Callback for each captured frame — write raw bytes to ffmpeg stdin."""
        import ctypes
        mgr = _recording_mgr
        should_stop = False
        with mgr.recording_lock:
            if not mgr.recording or mgr.ffmpeg_proc is None or mgr.capture_token is not token:
                return
            try:
                ctypes.pythonapi.PyCapsule_GetPointer.restype = ctypes.c_void_p
                ctypes.pythonapi.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
                ptr = ctypes.pythonapi.PyCapsule_GetPointer(buffer, None)
                raw = (ctypes.c_ubyte * buffer_size).from_address(ptr)
                import time as _time
                mgr.pacer.add_frame(bytes(raw), _time.monotonic(), mgr.ffmpeg_proc.stdin.write)
                mgr.frame_count = mgr.pacer.frames
                elapsed = _time.time() - mgr.start_time
                video_dur = mgr.frame_count / mgr.fps
                self._rt_stop_btn.text = f"Stop ({video_dur:.1f}s, {mgr.frame_count}f)"
                sec = int(elapsed)
                if sec > mgr.last_print:
                    mgr.last_print = sec
                    print(f"[RT Record] {elapsed:.1f}s elapsed | {mgr.frame_count} frames | video: {video_dur:.1f}s")
            except (BrokenPipeError, OSError, RuntimeError) as e:
                mgr.error = f"recording failed: {e}"
                should_stop = True
            finally:
                mgr.pacer.pending = False
        if should_stop:
            self._stop_realtime_recording(pad=False, token=token)

    def _stop_realtime_recording(self, pad=True, token=None):
        """Stop real-time recording and finalize the video."""
        mgr = _recording_mgr
        # A recording begun before a hot reload may predate the pacer and lock.
        if not hasattr(mgr, "recording_lock"):
            mgr.recording_lock = threading.RLock()
        with mgr.recording_lock:
            if token is not None and getattr(mgr, "capture_token", None) is not token:
                return
            if getattr(mgr, "stopping", False):
                return
            mgr.stopping = True
            mgr.recording = False
            frame_sub = mgr.frame_sub
            mgr.frame_sub = None
            if pad and mgr.ffmpeg_proc is not None and getattr(mgr, "pacer", None) is not None:
                import time as _time
                try:
                    mgr.pacer.finish(_time.monotonic(), mgr.ffmpeg_proc.stdin.write)
                    mgr.frame_count = mgr.pacer.frames
                except (BrokenPipeError, OSError, RuntimeError) as e:
                    mgr.error = f"recording failed: {e}"
        # Drop the subscription outside the lock: it may wait for an in-flight callback.
        del frame_sub
        if mgr.ffmpeg_proc is not None:
            # Closing stdin gives ffmpeg EOF -> it flushes the encode buffer and writes
            # the moov atom. Large recordings need well over the old 10s to finalize, so a
            # short wait abandoned ffmpeg mid-finalize -> truncated mp4 (no moov atom).
            import signal as _signal
            try:
                mgr.ffmpeg_proc.stdin.close()
            except Exception as e:
                print(f"Warning closing ffmpeg stdin: {e}")
            try:
                exit_code = mgr.ffmpeg_proc.wait(timeout=180)  # generous: let ffmpeg write the moov
                if exit_code and not getattr(mgr, "error", None):
                    detail = mgr.ffmpeg_proc.stderr.read().decode(errors="replace")[-1000:].strip()
                    mgr.error = f"ffmpeg exited with status {exit_code}" + (f": {detail}" if detail else "")
            except Exception as e:
                print(f"ffmpeg finalize exceeded wait ({e}); sending SIGINT to force trailer")
                if not getattr(mgr, "error", None):
                    mgr.error = f"ffmpeg did not finalize normally: {e}"
                try:
                    mgr.ffmpeg_proc.send_signal(_signal.SIGINT)
                    mgr.ffmpeg_proc.wait(timeout=60)
                except Exception as e2:
                    print(f"ffmpeg still not finalized after SIGINT: {e2}")
            mgr.ffmpeg_proc = None
        try:
            self._rt_record_btn.enabled = True
            self._rt_stop_btn.enabled = False
            self._rt_stop_btn.text = "Stop Recording"
            video_dur = round(mgr.frame_count / mgr.fps, 1) if mgr.fps else 0
            self._last_video_label.text = (f"Recording failed: {mgr.error}" if getattr(mgr, "error", None) else
                                           f"Saved: {mgr.output_path} ({video_dur}s, {mgr.frame_count} frames)")
            mgr.clear_state()
        finally:
            mgr.stopping = False
        print(f"Real-time recording stopped: {mgr.frame_count} frames → {mgr.output_path}")

    # --- MCP Recording Handlers ---

    def _cmd_start_recording(self, file_name: str = None, camera: str = None,
                              fps: int = 30, output_dir: str = None) -> Dict[str, Any]:
        """MCP handler: start real-time viewport recording."""
        try:
            print(f"[MCP start_recording] id(mgr)={id(_recording_mgr)} recording={_recording_mgr.recording}")
            if _recording_mgr.is_recording():
                return {"status": "error", "message": "Recording already in progress. Stop it first."}
            if not file_name or file_name == "":
                from datetime import datetime
                timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                file_name = f"isaacsim_{timestamp}.mp4"
            elif not file_name.endswith(".mp4"):
                file_name += ".mp4"
            if not fps or fps <= 0:
                fps = 30
            if camera == "":
                camera = None
            vid_dir = os.path.join(output_dir, "videos") if output_dir else VIDEOS_DIR
            self._start_realtime_recording(output_dir=vid_dir, file_name=file_name, fps=fps, camera_path=camera)
            path = os.path.join(vid_dir, file_name)
            return {
                "status": "success",
                "message": f"Recording started → {path}",
                "file_path": path,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _cmd_stop_recording(self) -> Dict[str, Any]:
        """MCP handler: stop real-time viewport recording."""
        try:
            mgr = _recording_mgr
            print(f"[MCP stop_recording] mgr.recording={mgr.recording} ffmpeg={mgr.ffmpeg_proc is not None} "
                  f"state_file_exists={os.path.exists(mgr.state_file)} id(mgr)={id(mgr)}")
            if not mgr.is_recording():
                return {"status": "error", "message": getattr(mgr, "error", None) or "No recording in progress."}
            if mgr.ffmpeg_proc is None:
                # State file says recording but process lost (e.g. after reload)
                mgr.clear_state()
                return {"status": "error", "message": "Recording process lost (extension reloaded?). State cleared."}
            path = mgr.output_path
            self._stop_realtime_recording()
            if getattr(mgr, "error", None):
                return {"status": "error", "message": mgr.error, "file_path": path,
                        "frame_count": mgr.frame_count}
            return {
                "status": "success",
                "message": f"Recording stopped: {mgr.frame_count} frames",
                "file_path": path,
                "frame_count": mgr.frame_count,
                "video_duration": round(mgr.frame_count / mgr.fps, 2) if mgr.fps else 0,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _cmd_get_recording_status(self) -> Dict[str, Any]:
        """MCP handler: get current recording status."""
        mgr = _recording_mgr
        if not mgr.is_recording():
            if getattr(mgr, "error", None):
                return {"status": "error", "message": mgr.error}
            return {"status": "success", "message": "Status: Not recording"}
        import time as _time
        elapsed = _time.time() - mgr.start_time if mgr.start_time else 0
        video_dur = round(mgr.frame_count / mgr.fps, 2) if mgr.fps else 0
        return {
            "status": "success",
            "message": (
                f"Status: Recording in progress\n"
                f"  Output: {mgr.output_path}\n"
                f"  FPS: {mgr.fps}\n"
                f"  Frames recorded: {mgr.frame_count}\n"
                f"  Video duration: {video_dur}s\n"
                f"  Wall-clock elapsed: {round(elapsed, 1)}s"
            ),
            "recording": True,
            "file_path": mgr.output_path,
            "frame_count": mgr.frame_count,
            "fps": mgr.fps,
            "video_duration": video_dur,
            "elapsed": round(elapsed, 1),
        }

    def _rebuild_ui(self):
        """Tear down and recreate the panel UI from the current (hot-reloaded) code.
        Use after editing extension.py to pick up new widgets without closing the window.
        Loses transient UI state (collapsed/expanded frames, unsaved field text)."""
        try:
            if not getattr(self, "_window", None):
                print("No window to rebuild")
                return
            self._window.frame.clear()
            with self._window.frame:
                with ui.VStack(spacing=5):
                    self.create_ui()
            print("UI rebuilt from current code")
        except Exception as ex:
            import traceback
            print(f"Error rebuilding UI: {ex}\n{traceback.format_exc()}")

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

    def _load_environment(self):
        """Load a full-scene environment USD (walls, floor, ceiling) as a reference at
        /World/Environment. Pauses timeline during swap to avoid PhysX tensor invalidation,
        optionally hides the flat /World/defaultGroundPlane to prevent Z-fighting."""
        from pxr import UsdGeom, Sdf
        import omni.timeline
        from isaacsim.storage.native import get_assets_root_path
        from isaacsim.core.utils.stage import add_reference_to_stage

        stage = omni.usd.get_context().get_stage()
        idx = self._env_combo.model.get_item_value_model().as_int
        names = list(ENVIRONMENTS.keys())
        name = names[idx]
        rel_path = ENVIRONMENTS[name]

        timeline = omni.timeline.get_timeline_interface()
        was_playing = timeline.is_playing()
        if was_playing:
            timeline.pause()

        # Remove existing environment reference if present
        existing = stage.GetPrimAtPath(ENVIRONMENT_PRIM_PATH)
        if existing and existing.IsValid():
            stage.RemovePrim(ENVIRONMENT_PRIM_PATH)
            print(f"Removed existing environment at {ENVIRONMENT_PRIM_PATH}")

        if rel_path is None:
            # "None" option: also un-hide flat ground
            gp = stage.GetPrimAtPath("/World/defaultGroundPlane")
            if gp and gp.IsValid():
                UsdGeom.Imageable(gp).MakeVisible()
            print("Environment cleared (flat ground plane restored)")
            if was_playing:
                timeline.play()
            return

        root = get_assets_root_path()
        if not root:
            print("Error: Isaac assets root not found")
            return
        full_path = root + "/" + rel_path

        try:
            add_reference_to_stage(usd_path=full_path, prim_path=ENVIRONMENT_PRIM_PATH)
            print(f"Loaded environment: {name} ({full_path})")
        except Exception as e:
            print(f"Error loading environment {name}: {e}")
            if was_playing:
                timeline.play()
            return

        # Apply per-env Z offset to align floor surface with z=0
        z_off = ENVIRONMENT_Z_OFFSETS.get(name, 0.0)
        if z_off != 0.0:
            env_prim = stage.GetPrimAtPath(ENVIRONMENT_PRIM_PATH)
            xform = UsdGeom.Xformable(env_prim)
            xform.ClearXformOpOrder()
            xform.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, z_off))
            print(f"Applied env z-offset: {z_off:+.3f} m")

        # Hide flat ground plane if checkbox set
        if self._env_hide_ground.model.get_value_as_bool():
            gp = stage.GetPrimAtPath("/World/defaultGroundPlane")
            if gp and gp.IsValid():
                UsdGeom.Imageable(gp).MakeInvisible()
                print("Flat ground plane hidden")

        if was_playing:
            timeline.play()

    def _swap_ground_plane(self):
        """Delete and recreate /World/defaultGroundPlane with a new grid tint color.
        Pauses timeline during swap so PhysX can release the old rigid body cleanly."""
        from pxr import Gf
        import omni.timeline
        from isaacsim.core.api.objects import GroundPlane

        stage = omni.usd.get_context().get_stage()

        # Resolve tint color
        idx = self._grid_tint_combo.model.get_item_value_model().as_int
        names = list(GRID_TINT_PRESETS.keys())
        name = names[idx]
        if name == "Custom Hex":
            hex_str = self._grid_tint_hex.model.get_value_as_string().strip().lstrip("#")
            try:
                if len(hex_str) != 6:
                    raise ValueError("expected 6 hex digits")
                r = int(hex_str[0:2], 16) / 255.0
                g = int(hex_str[2:4], 16) / 255.0
                b = int(hex_str[4:6], 16) / 255.0
                color = (r, g, b)
            except Exception as e:
                print(f"Invalid hex '{hex_str}': {e}. Falling back to white.")
                color = (1.0, 1.0, 1.0)
        else:
            color = GRID_TINT_PRESETS[name]

        timeline = omni.timeline.get_timeline_interface()
        was_playing = timeline.is_playing()
        if was_playing:
            timeline.pause()

        # Delete existing ground plane
        gp_prim = stage.GetPrimAtPath("/World/defaultGroundPlane")
        if gp_prim and gp_prim.IsValid():
            stage.RemovePrim("/World/defaultGroundPlane")
            print("Removed existing ground plane")

        # Clear cached material path — old material prim is gone with the ground
        DigitalTwin._ground_mat_path = None

        try:
            GroundPlane(
                prim_path="/World/defaultGroundPlane",
                z_position=0.0,
                size=5000,
                color=Gf.Vec3f(*color),
            )
            print(f"Recreated ground plane with tint {name} (rgb={color})")
        except Exception as e:
            print(f"Error recreating ground plane: {e}")

        if was_playing:
            timeline.play()

    def _apply_ground_style(self):
        """Apply a ground plane visual style by creating an OmniPBR material and binding
        it at the Xform root of the ground plane (where the default binding lives)."""
        from pxr import UsdGeom, Gf, UsdShade
        stage = omni.usd.get_context().get_stage()

        idx = self._ground_style_combo.model.get_item_value_model().as_int
        styles = ["Default Grey", "White Studio", "Dark Studio", "Concrete", "Invisible (Physics Only)"]
        style = styles[idx]
        roughness = self._ground_roughness_slider.model.as_float

        ground_prim = stage.GetPrimAtPath("/World/defaultGroundPlane")
        if not ground_prim or not ground_prim.IsValid():
            print("No ground plane found. Load the scene first.")
            return

        if style == "Invisible (Physics Only)":
            UsdGeom.Imageable(ground_prim).MakeInvisible()
            print("Ground plane hidden (physics still active)")
            return
        else:
            UsdGeom.Imageable(ground_prim).MakeVisible()

        # Default Grey: restore the original UsdPreviewSurface material
        if style == "Default Grey":
            orig_mat = stage.GetPrimAtPath("/World/Looks/visual_material")
            if orig_mat and orig_mat.IsValid():
                UsdShade.MaterialBindingAPI.Apply(ground_prim)
                UsdShade.MaterialBindingAPI(ground_prim).Bind(UsdShade.Material(orig_mat))
                print("Applied ground style: Default Grey (restored original material)")
            else:
                print("Warning: Original visual_material not found")
            return

        color_map = {
            "White Studio": (0.95, 0.95, 0.95),
            "Dark Studio": (0.1, 0.1, 0.1),
            "Concrete": (0.6, 0.58, 0.55),
        }
        color = color_map.get(style, (0.5, 0.5, 0.5))

        # Reuse cached material if it still exists on stage
        mat_path = DigitalTwin._ground_mat_path
        mat_prim = stage.GetPrimAtPath(mat_path) if mat_path else None
        if not mat_prim or not mat_prim.IsValid():
            # CreateAndBindMdlMaterialFromLibrary creates at /Looks/ (root scope)
            out = []
            omni.kit.commands.execute(
                "CreateAndBindMdlMaterialFromLibrary",
                mdl_name="OmniPBR.mdl",
                mtl_name="OmniPBR",
                mtl_created_list=out,
                prim_name="GroundMaterial",
                select_new_prim=False,
            )
            if out:
                mat_path = out[0]
                DigitalTwin._ground_mat_path = mat_path
                print(f"Created OmniPBR ground material at {mat_path}")
            mat_prim = stage.GetPrimAtPath(mat_path) if mat_path else None

        if not mat_prim or not mat_prim.IsValid():
            print("Error: Failed to create ground material.")
            return

        # Set shader properties — MDL attributes must be created if they don't exist yet
        from pxr import Sdf
        shader_prim = stage.GetPrimAtPath(f"{mat_path}/Shader")
        if shader_prim and shader_prim.IsValid():
            color_attr = shader_prim.GetAttribute("inputs:diffuse_color_constant")
            if not color_attr or not color_attr.IsValid():
                color_attr = shader_prim.CreateAttribute("inputs:diffuse_color_constant", Sdf.ValueTypeNames.Color3f)
            color_attr.Set(Gf.Vec3f(*color))

            rough_attr = shader_prim.GetAttribute("inputs:reflection_roughness_constant")
            if not rough_attr or not rough_attr.IsValid():
                rough_attr = shader_prim.CreateAttribute("inputs:reflection_roughness_constant", Sdf.ValueTypeNames.Float)
            rough_attr.Set(roughness)

            tex_attr = shader_prim.GetAttribute("inputs:diffuse_texture")
            if tex_attr and tex_attr.IsValid():
                tex_attr.Clear()
        else:
            print(f"Warning: No shader found at {mat_path}/Shader")

        # Bind at the Xform root (overrides the default visual_material binding)
        UsdShade.MaterialBindingAPI.Apply(ground_prim)
        UsdShade.MaterialBindingAPI(ground_prim).Bind(UsdShade.Material(mat_prim))

        print(f"Applied ground style: {style} (color={color}, roughness={roughness:.2f})")

    def _apply_background(self):
        """Set the viewport background. Solid colors use RTX settings; HDRI comes from dome light."""
        idx = self._bg_combo.model.get_item_value_model().as_int
        options = ["From Dome Light (HDRI/Default)", "Solid White", "Solid Black", "Solid Light Grey"]
        choice = options[idx]

        settings = carb.settings.get_settings()

        if choice == "From Dome Light (HDRI/Default)":
            # Restore default — dome light controls the background
            settings.set("/rtx/background/enabled", False)
            print("Background: controlled by dome light / HDRI")
        else:
            color_map = {
                "Solid White": (1.0, 1.0, 1.0),
                "Solid Black": (0.0, 0.0, 0.0),
                "Solid Light Grey": (0.85, 0.85, 0.85),
            }
            r, g, b = color_map[choice]
            # Use the dome light approach: set a flat-color dome
            from pxr import UsdLux
            stage = omni.usd.get_context().get_stage()
            # Find existing dome light
            dome_prim = None
            for prim in stage.Traverse():
                if prim.IsA(UsdLux.DomeLight):
                    dome_prim = prim
                    break
            if dome_prim:
                # Clear HDRI texture so the color attribute controls appearance
                tex_attr = dome_prim.GetAttribute("inputs:texture:file")
                if tex_attr and tex_attr.IsValid():
                    tex_attr.Clear()
                from pxr import Gf
                dome_prim.GetAttribute("inputs:color").Set(Gf.Vec3f(r, g, b))
                print(f"Background: {choice} (dome light color set, HDRI cleared)")
            else:
                print("No dome light found. Apply a lighting preset first, then set background.")

    def _reset_lighting_defaults(self):
        """Reset to default: single dome light at 1000, default ground, default background."""
        self._clear_all_lights()
        from pxr import UsdLux, UsdGeom
        stage = omni.usd.get_context().get_stage()

        # Restore default dome light
        dome = UsdLux.DomeLight.Define(stage, "/World/defaultDomeLight")
        dome.CreateIntensityAttr().Set(1000.0)

        # Make ground visible again
        ground_prim = stage.GetPrimAtPath("/World/defaultGroundPlane")
        if ground_prim and ground_prim.IsValid():
            UsdGeom.Imageable(ground_prim).MakeVisible()

        # Reset background
        settings = carb.settings.get_settings()
        settings.set("/rtx/background/enabled", False)

        # Sync UI
        self._dome_intensity_slider.model.set_value(1000.0)
        self._dome_temp_slider.model.set_value(6500.0)
        self._dome_temp_enable.model.set_value(False)
        self._lighting_preset_combo.model.get_item_value_model().set_value(0)
        self._ground_style_combo.model.get_item_value_model().set_value(0)
        self._ground_roughness_slider.model.set_value(0.5)
        self._bg_combo.model.get_item_value_model().set_value(0)

        print("Reset lighting, ground, and background to defaults.")

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

    async def quick_start(self):
        """Quick start: scene, public UR5e + action graph, workspace camera; the RG2 when its files exist."""
        import numpy as np
        from pxr import Gf, Sdf, UsdGeom

        require_newton_mount_support(selected_engine())

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

        raise RuntimeError("Could not place all objects without violating min_sep; reduce density.")

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
        """Teleport an object to an EXACT world pose. Mirrors randomize_single_object's apply path
        (resolve parent-local pose, set xformOp:translate/orient via _set_obj_prim_pose) so the pose
        persists under physics. Orientation precedence: orientation=[w,x,y,z] quat > yaw_deg about z >
        identity. position is [x,y,z] in METERS (world). Returns the applied world position."""
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
        print(f"[set_object_pose] '{object_name}' -> world ({target_world[0]:.3f}, {target_world[1]:.3f}, {target_world[2]:.4f})")
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

    def _build_visibility_ui(self):
        """Build (or rebuild) the combo boxes for exclude/include.

        Always reads current scene objects and excluded list so the UI
        is up-to-date on first open and after every action.
        """
        scene_objects = self._get_scene_object_names()
        excluded_names = list(self._hidden_objects.keys())
        with ui.VStack(spacing=5, height=0):
            with ui.HStack(spacing=5):
                ui.Label("Object:", alignment=ui.Alignment.LEFT, width=80)
                if scene_objects:
                    self._vis_object_combo = ui.ComboBox(0, *scene_objects, width=150)
                else:
                    self._vis_object_combo = ui.ComboBox(0, "(none)", width=150)
                ui.Button("Exclude", width=80, height=30, clicked_fn=self._on_hide_clicked)
            with ui.HStack(spacing=5):
                ui.Label("Excluded:", alignment=ui.Alignment.LEFT, width=80)
                if excluded_names:
                    self._vis_hidden_combo = ui.ComboBox(0, *excluded_names, width=150)
                else:
                    self._vis_hidden_combo = ui.ComboBox(0, "(none)", width=150)
                ui.Button("Include", width=80, height=30, clicked_fn=self._on_unhide_clicked)

    def _on_hide_clicked(self):
        scene_objects = self._get_scene_object_names()
        if not scene_objects:
            print("No objects to exclude")
            return
        idx = self._vis_object_combo.model.get_item_value_model().as_int
        if idx < len(scene_objects):
            self.hide_object(scene_objects[idx])
        self._visibility_frame.rebuild()

    def _on_unhide_clicked(self):
        hidden_names = list(self._hidden_objects.keys())
        if not hidden_names:
            print("No excluded objects to include")
            return
        idx = self._vis_hidden_combo.model.get_item_value_model().as_int
        if idx < len(hidden_names):
            self.unhide_object(hidden_names[idx])
        self._visibility_frame.rebuild()


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
                "message": f"Set '{object_name}' to world ({float(world[0]):.3f}, {float(world[1]):.3f}, {float(world[2]):.4f})"
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
                "folder_path": self._objects_folder_path
            }
        except Exception as e:
            carb.log_error(f"Error in add_objects: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Failed to add objects: {str(e)}",
                "traceback": traceback.format_exc()
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

            freed_mb = rss_before - rss_after
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
        return {"gripper": self._quick_start_gripper, "assets": self._quick_start_assets}

    def _bench_label(self) -> str:
        if asset_source(WORKSTATION_USD)["source"] == "missing":
            return "a placeholder bench (no Vention workstation asset)"
        return "the Vention workstation"

    async def _cmd_quick_start(self) -> Dict[str, Any]:
        """MCP handler: full scene setup (table, UR5e, gripper, camera, play)."""
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
                "traceback": traceback.format_exc()
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
                "message": "Workstation loaded at /World/Vention (tabletop top at z=0; robot and object physics unchanged; ground plane visual hidden)."
            }
        except Exception as e:
            carb.log_error(f"Error in load_workstation: {e}")
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Load workstation failed: {str(e)}",
                "traceback": traceback.format_exc()
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
                "traceback": traceback.format_exc()
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
                "traceback": traceback.format_exc()
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
                "traceback": traceback.format_exc()
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
                        "message": "backend='fake' is sim-only; use backend='ursim' mode='real' for the lab UR5e"}
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
            "message": f"{label} exited {proc.returncode}",
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
            return {"status": "success", "message": f"Synced {len(result['updated'])} object poses to sim", **result}
        except Exception as e:
            carb.log_error(f"Error in sync_real_poses: {e}")
            traceback.print_exc()
            return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}

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

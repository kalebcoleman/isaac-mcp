"""Paths, asset names and tuning constants of the twin, and where its local assets are."""

import os
import glob
import traceback
from .asset_cache import AssetCorrupt, AssetMissing, resolve_dir, resolve_path, resolve_uri
from .tf_compat import use_legacy_transform_publisher


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
    _scene_output = os.getenv("ISAAC_MCP_SCENE_OUTPUT", "").strip()
    RESOURCES_DIR = os.path.join(os.path.abspath(_scene_output), "resources") if _scene_output else "resources"
    VIDEOS_DIR = ""  # resolved below after _ext_dir


# Local asset paths (no Nucleus dependency)
_ext_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


if not VIDEOS_DIR:
    VIDEOS_DIR = os.path.join(os.path.abspath(_scene_output) if _scene_output else _ext_dir, "videos")


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


def _last_error_line(output: str) -> str:
    """The last non-empty line of a subprocess's output, for a refusal's message. ANSI colors removed."""
    import re

    for line in reversed((output or "").splitlines()):
        cleaned = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        if cleaned:
            return cleaned[:300]
    return ""


def _failure_detail(exc):
    """A missing asset is an expected refusal whose message already names the fix."""
    return {} if isinstance(exc, AssetMissing) else {"traceback": traceback.format_exc()}


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

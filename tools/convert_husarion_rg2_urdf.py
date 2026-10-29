# Convert the CAD-exact husarion-native OnRobot RG2 URDF -> USD (Isaac Sim 5.1).
#
# Reference (authoritative headless invocation, Isaac Sim 5.x standalone example):
#   https://github.com/isaac-sim/IsaacSim  ->
#   source/standalone_examples/api/isaacsim.asset.importer.urdf/urdf_import.py
# Installed importer: isaacsim.asset.importer.urdf 2.4.31 (Isaac Sim 5.1.0).
# 5.1 exposes the COMMAND API (URDFCreateImportConfig / URDFParseAndImportFile +
# _urdf.ImportConfig), NOT the newer impl.URDFImporter class (that is main/6.x).
#
# Why this exists: the ur5e-dt twin shipped a Robotiq-2F-relabeled gripper
# (assets/gripper/RG2.usd). The CAD-exact husarion-native RG2 (1 actuated
# rg2_gripper_joint + 5 <mimic> joints forming the parallelogram) lived only as
# a URDF. This bakes it to USD so the twin's grasp width is physically CAD-exact.
#
# Two load-bearing facts (see the isaac-urdf-import skill / PARITY-LEDGER):
#  - The URDF is a TREE + <mimic> tags (URDF cannot express the closed
#    parallelogram loop). parse_mimic=True makes the importer apply
#    PhysxMimicJointAPI couplings to the 5 finger joints -> no loop-break needed.
#  - PhysX mimic joints lag/oscillate (spring-like) unless their drive
#    stiffness is very high; the ACTUATED joint keeps normal gains. We do NOT
#    set drives here (the extension's import_rg2_gripper owns per-joint drive
#    tuning post-reference); this script only bakes geometry + couplings.
#
# Usage:
#   ~/env_isaaclab/bin/python scripts/convert_husarion_rg2_urdf.py \
#       --urdf ~/Downloads/husarion_native/husarion_native_tool0.urdf \
#       --out  /tmp/husarion_rg2.usd  [--inspect]

import argparse
import os

from isaacsim import SimulationApp

simulation_app = SimulationApp({"headless": True})

import omni.kit.commands  # noqa: E402  (must follow SimulationApp construction)
from isaacsim.asset.importer.urdf import _urdf  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Convert husarion RG2 URDF -> USD")
    ap.add_argument("--urdf", required=True, help="Absolute path to the .urdf")
    ap.add_argument("--out", required=True, help="Destination .usd path")
    ap.add_argument("--inspect", action="store_true",
                    help="Print joint/link tree + bbox of the imported result")
    args = ap.parse_args()

    urdf_path = os.path.abspath(os.path.expanduser(args.urdf))
    out_path = os.path.abspath(os.path.expanduser(args.out))
    if not os.path.exists(urdf_path):
        raise FileNotFoundError(urdf_path)

    # --- Build the import config -------------------------------------------------
    status, cfg = omni.kit.commands.execute("URDFCreateImportConfig")
    cfg.merge_fixed_joints = False        # keep tool0/quick_changer/base frames distinct
    cfg.fix_base = False                  # gripper is welded to the UR5e by attach logic
    cfg.make_default_prim = True
    cfg.parse_mimic = True                # -> PhysxMimicJointAPI on the 5 finger joints
    cfg.self_collision = False
    cfg.distance_scale = 1.0              # URDF is in meters
    cfg.density = 0.0                     # use URDF-declared masses; 0 => auto where missing
    cfg.import_inertia_tensor = True
    cfg.convex_decomp = False
    cfg.collision_from_visuals = False
    cfg.create_physics_scene = False      # twin owns the physics scene

    # --- Parse + import to a USD file -------------------------------------------
    result = omni.kit.commands.execute(
        "URDFParseAndImportFile",
        urdf_path=urdf_path,
        import_config=cfg,
        dest_path=out_path,
    )
    # execute() returns (status, value); value is the robot prim path on the stage
    robot_path = result[1] if isinstance(result, (tuple, list)) else result
    print(f"[convert] imported robot prim: {robot_path}")
    print(f"[convert] wrote USD: {out_path}  exists={os.path.exists(out_path)}")

    if args.inspect:
        _inspect(out_path)

    simulation_app.close()


def _inspect(usd_path):
    # Write to a sidecar file (NOT stdout): simulation_app.close() hard-exits and
    # drops buffered prints, but carb log warnings survive — so stdout inspection
    # vanishes. A flushed file write is reliable.
    from pxr import Usd, UsdGeom, UsdPhysics
    out = os.path.splitext(usd_path)[0] + ".inspect.txt"
    lines = []
    stage = Usd.Stage.Open(usd_path)
    dp = stage.GetDefaultPrim()
    lines.append(f"defaultPrim: {dp.GetPath() if dp else None}")
    lines.append(f"metersPerUnit: {UsdGeom.GetStageMetersPerUnit(stage)}")
    joints, mimics, drives, artroots = [], [], [], []
    for prim in stage.Traverse():
        t = str(prim.GetTypeName())
        schemas = list(prim.GetAppliedSchemas())
        if "Joint" in t:
            joints.append((prim.GetPath().pathString, t))
        if any("Mimic" in s for s in schemas):
            mimics.append((prim.GetName(), [s for s in schemas if "Mimic" in s]))
        if any("DriveAPI" in s for s in schemas):
            drives.append(prim.GetPath().pathString)
        if any("ArticulationRootAPI" in s for s in schemas):
            artroots.append(prim.GetPath().pathString)
    lines.append(f"\nARTICULATION ROOT: {artroots}")
    lines.append(f"\n--- Joints ({len(joints)}) ---")
    for p, t in joints:
        lines.append(f"  {t:24s} {p}")
    lines.append(f"\n--- Mimic-API joints ({len(mimics)}) ---")
    for n, s in mimics:
        lines.append(f"  {n:26s} {s}")
    lines.append(f"\n--- DriveAPI joints ({len(drives)}) ---")
    for p in drives:
        lines.append(f"  {p}")
    # bbox sanity (meters)
    try:
        bb = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        rng = bb.ComputeWorldBound(dp).ComputeAlignedRange()
        lines.append(f"\nBBOX size(m): {rng.GetSize()}  min:{rng.GetMin()} max:{rng.GetMax()}")
    except Exception as e:
        lines.append(f"\nBBOX failed: {e!r}")
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[convert] inspection written: {out}")


if __name__ == "__main__":
    main()

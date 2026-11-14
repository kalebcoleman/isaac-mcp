#!/usr/bin/env python3
"""Build ``ur5e-jenga-workcell/v2`` from ``v1``: a bench collision layer, one articulation with a mimic-driven RG2.

v1 is only read. v2 is written next to it by the pack library's own writer (staged, verified, then moved
into place), so it either exists complete and verified or not at all. Changes from v1:

* ``bench``: split into a render layer and a collision layer of one oriented box per mesh (``assets__mesh
  split``); the asset id now names ``bench/bench.usda``. Newton cannot hold the 1.63 M triangle-mesh
  collider triangles the work cell gave the v1 bench.
* ``rg2``:
  - no articulation root of its own: it joins the arm's articulation through the mount joint;
  - the massless ``gripper_center`` body and its fixed joint become a plain frame;
  - the two loop-closing joints (excluded from the articulation) are removed, and their zero-force passive
    drives with them;
  - each finger's inner joints follow the driven knuckle through ``NewtonMimicAPI``, which both PhysX
    (Isaac Sim 6.2) and Newton 1.6 read. The ratios are solved here from the asset's own joint frames, over
    the joint's limits, and the worst closure gap of the linear fit is reported. PhysX accepts a mimic only on
    revolute joints with finite limits, so each follower gets its leader's range mapped through the ratio,
    plus a margin.
* ``robot``: the disabled, dangling ``robot_gripper_joint`` is removed.

Everything else, the arm's drive gains included, is copied unchanged. Run from ``mcp/`` with the mesh extra:

    uv run --locked --isolated --python 3.12 --extra scene-test python ../scripts/workcell_pack_v2.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "mcp"))

from twinsmith import asset_packs, asset_rules, mesh_usd, pack_manifest  # noqa: E402

PACK, SOURCE, TARGET = "ur5e-jenga-workcell", "v1", "v2"
RG2 = "/khi_rs080n"
# finger -> (driven joint, inner finger joint, finger-to-inner-knuckle joint, loop-closing joint)
FINGERS = {"left": ("finger_joint", "left_inner_finger_joint", "left_finger_knuckle_joint", "left_inner_knuckle_joint"),
           "right": ("right_outer_knuckle_joint", "right_inner_finger_joint", "RevoluteJoint", "right_inner_knuckle_joint")}
MAX_CLOSURE_GAP_MM = 0.1
FOLLOWER_LIMIT_MARGIN_DEG = 5.0   # PhysX needs finite limits on a mimic's revolute joints; the leader's range, mapped


def _joints(stage):
    from pxr import UsdPhysics

    return {prim.GetName(): prim for prim in stage.Traverse() if prim.IsA(UsdPhysics.Joint)}


def _frames(prim):
    from pxr import Gf, UsdPhysics

    joint = UsdPhysics.Joint(prim)
    out = []
    for side in (0, 1):
        m = Gf.Matrix4d().SetRotate(Gf.Quatd(getattr(joint, f"GetLocalRot{side}Attr")().Get()))
        m.SetTranslateOnly(Gf.Vec3d(getattr(joint, f"GetLocalPos{side}Attr")().Get()))
        out.append(np.array(m))
    return out


def _rot_x(q):
    c, s = np.cos(q), np.sin(q)
    m = np.eye(4)
    m[1, 1], m[1, 2], m[2, 1], m[2, 2] = c, s, -s, c        # USD row-vector rotation about the joint's X axis
    return m


def mimic_ratios(stage) -> dict:
    """Solve each finger's four-bar over the driven joint's limits; return the linear follower ratios."""
    from pxr import Usd, UsdGeom
    from scipy.optimize import least_squares

    joints = _joints(stage)
    for name in ("finger_joint", "right_outer_knuckle_joint"):
        axis = joints[name].GetAttribute("physics:axis").Get()
        if axis != "X":
            raise ValueError(f"{name}: expected axis X, found {axis}")
    base = np.array(UsdGeom.Xformable(stage.GetPrimAtPath(f"{RG2}/onrobot_rg2_base_link"))
                    .ComputeLocalToWorldTransform(Usd.TimeCode.Default()))

    def child(name, q, parent):
        j0, j1 = _frames(joints[name])
        return np.linalg.inv(j1) @ _rot_x(q) @ j0 @ parent

    ratios = {}
    for side, (driven, finger, knuckle, loop) in FINGERS.items():
        c0, c1 = _frames(joints[loop])
        lo = np.radians(joints[driven].GetAttribute("physics:lowerLimit").Get())
        hi = np.radians(joints[driven].GetAttribute("physics:upperLimit").Get())

        def gap(x, q):
            k = child(knuckle, x[1], child(finger, x[0], child(driven, q, base)))
            return ((c0 @ base)[3, :3] - (c1 @ k)[3, :3]) * 1000.0      # millimetres

        qs, solved, x = np.linspace(lo, hi, 66), [], np.zeros(2)
        for q in qs:
            x = least_squares(gap, x, args=(q,)).x
            solved.append(x)
        solved = np.array(solved)
        k_finger, k_knuckle = (float(qs @ solved[:, i] / (qs @ qs)) for i in (0, 1))
        worst = max(float(np.abs(gap([k_finger * q, k_knuckle * q], q)).max()) for q in qs)
        if worst > MAX_CLOSURE_GAP_MM:
            raise ValueError(f"{side} finger: a linear mimic leaves a {worst:.3f} mm closure gap; not a parallelogram")
        ratios[side] = dict(driven=driven, finger=finger, knuckle=knuckle, loop=loop, finger_ratio=round(k_finger, 5),
                            knuckle_ratio=round(k_knuckle, 5), worst_closure_gap_mm=round(worst, 4))
    return ratios


def _strip(prim, props=(), schemas=()):
    """Remove applied schemas starting with any of ``schemas`` and authored properties starting with any of ``props``."""
    for name in mesh_usd.applied_schemas(prim):
        if name.startswith(tuple(schemas)):
            prim.RemoveAppliedSchema(name)
    for prop in prim.GetAuthoredPropertyNames():
        if prop.startswith(tuple(props)):
            prim.RemoveProperty(prop)


def build_rg2(src: Path, dest: Path) -> dict:
    from pxr import Sdf

    stage = mesh_usd.copy_root_layer(src, dest)
    ratios = mimic_ratios(stage)
    joints = _joints(stage)
    _strip(stage.GetPrimAtPath(RG2), ("physxArticulation:",), ("PhysicsArticulationRootAPI", "PhysxArticulation"))
    _strip(stage.GetPrimAtPath(f"{RG2}/gripper_center"),
           ("physics:mass", "physics:density", "physics:centerOfMass", "physics:diagonalInertia", "physics:principalAxes",
            "physics:rigidBodyEnabled", "physics:kinematicEnabled", "physics:velocity", "physics:angularVelocity",
            "physics:startsAsleep", "physics:simulationOwner", "physxRigidBody:"),
           ("PhysicsRigidBodyAPI", "PhysicsMassAPI", "PhysxRigidBody"))
    stage.RemovePrim(joints["gripper_center_joint"].GetPath())
    for side in ratios.values():
        stage.RemovePrim(joints[side["loop"]].GetPath())
        _strip(joints[side["finger"]], ("drive:",), ("PhysicsDriveAPI:angular",))
        leader = joints[side["driven"]]
        span = [leader.GetAttribute(f"physics:{end}Limit").Get() for end in ("lower", "upper")]
        for follower, ratio in ((side["finger"], side["finger_ratio"]), (side["knuckle"], side["knuckle_ratio"])):
            prim = joints[follower]
            mapped = sorted(ratio * value for value in span)
            prim.GetAttribute("physics:lowerLimit").Set(round(mapped[0] - FOLLOWER_LIMIT_MARGIN_DEG, 3))
            prim.GetAttribute("physics:upperLimit").Set(round(mapped[1] + FOLLOWER_LIMIT_MARGIN_DEG, 3))
            prim.AddAppliedSchema("NewtonMimicAPI")
            prim.CreateRelationship("newton:mimicJoint").SetTargets([joints[side["driven"]].GetPath()])
            prim.CreateAttribute("newton:mimicCoef0", Sdf.ValueTypeNames.Float).Set(0.0)
            prim.CreateAttribute("newton:mimicCoef1", Sdf.ValueTypeNames.Float).Set(ratio)
    stage.GetRootLayer().Save()
    return ratios


def build_robot(src: Path, dest: Path) -> list[str]:
    stage = mesh_usd.copy_root_layer(src, dest)
    removed = [str(p.GetPath()) for p in stage.Traverse() if p.GetName() == "robot_gripper_joint"]
    for path in removed:
        stage.RemovePrim(path)
    stage.GetRootLayer().Save()
    return removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="build in a temporary folder, validate, write nothing")
    args = parser.parse_args(argv)
    root = pack_manifest.resolve_asset_root().path
    v1 = pack_manifest.AssetPack(root, PACK, SOURCE)          # verifies every hash first
    v1_dir = root / PACK / SOURCE
    records = {r["path"]: r for r in pack_manifest.read_manifest(v1_dir, PACK, SOURCE)["files"]}
    by_id = {r["asset_id"]: r for r in records.values() if "asset_id" in r}
    work = Path(tempfile.mkdtemp(prefix="workcell-v2-"))
    try:
        files, new_records, changes = {}, {}, {}
        for rel, record in records.items():
            if record.get("asset_id") not in ("bench", "rg2", "robot"):
                files[rel], new_records[rel] = v1.asset(record["asset_id"]) if "asset_id" in record else v1_dir / rel, record

        def derived(asset_id, note, keep_id=True):
            """v1's record for the asset (source, licence, fidelity), plus where it came from and what changed."""
            old = by_id[asset_id]
            base = {k: v for k, v in old.items() if k not in ("path", "sha256") and (keep_id or k != "asset_id")}
            return dict(base, derived_from=f"{PACK}/{SOURCE}/{old['path']} sha256 {old['sha256']}", changes=note)

        (work / "bench").mkdir()
        split = mesh_usd.split(v1.asset("bench"), work / "bench", collision=dict(method="box"))
        note = "Split by assets__mesh: render layer plus one oriented box collider per mesh (purpose guide)."
        for name in ("bench.usda", "bench_render.usdc", "bench_collision.usdc"):
            rel = f"bench/{name}"
            files[rel] = work / rel
            new_records[rel] = derived("bench", note, keep_id=name == "bench.usda")
        changes["bench"] = dict(boxes=split["shapes"]["boxes"], meshes=split["shapes"]["meshes"])

        (work / "rg2").mkdir()
        ratios = build_rg2(v1.asset("rg2"), work / "rg2" / "rg2.usdc")
        files["rg2/rg2.usdc"] = work / "rg2" / "rg2.usdc"
        new_records["rg2/rg2.usdc"] = derived("rg2", (
            "One articulation with the arm (no RG2 articulation root); gripper_center is a plain frame; loop-closing "
            "joints and zero-force passive drives removed; inner finger and knuckle joints follow the driven knuckle "
            "through NewtonMimicAPI (ratios solved from the joint frames, see mimic in pack.yaml)."))
        changes["rg2"] = ratios

        (work / "robot").mkdir()
        removed = build_robot(v1.asset("robot"), work / "robot" / "robot.usdc")
        files["robot/robot.usdc"] = work / "robot" / "robot.usdc"
        new_records["robot/robot.usdc"] = derived("robot", "Disabled robot_gripper_joint (no second body) removed; drive gains unchanged.")
        changes["robot"] = dict(removed=removed)

        report = dict(changes=changes, validate={})
        for asset in ("bench/bench.usda", "rg2/rg2.usdc", "robot/robot.usdc"):
            result = asset_rules.validate(files[asset], "newton")
            report["validate"][asset] = dict(ok=result["ok"], findings=[(c["id"], c["level"], c["prim"]) for c in result["checks"] if not c["ok"]])
        if args.dry_run:
            print(json.dumps(dict(dry_run=True, **report), indent=2))
            return 0
        extra = dict(qualification="candidate; built from v1 by scripts/workcell_pack_v2.py; not yet run live on PhysX or Newton",
                     derived_from=f"{PACK}/{SOURCE}", mimic=ratios)
        written = asset_packs.write_version(root, PACK, TARGET, files, new_records, defaults={}, notes=[], extra=extra)
        print(json.dumps(dict(written=written, **report), indent=2))
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

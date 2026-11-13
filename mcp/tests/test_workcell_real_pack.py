"""Geometry checks against the real external pack; skipped when the pack is not synced.

CPU only (no Kit, no GPU): the real robot and camera builders author an in-memory stage
and the checks read the composed USD. They guard the wrist view, the overhead mount
attachment, and the RG2 mass/joint warnings that only the real pack can show.
"""

import importlib.util
import math
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("pxr.Usd")
from pxr import Gf, Usd, UsdGeom, UsdPhysics

PACKAGE = Path(__file__).resolve().parents[2] / "exts/workcell/workcell"
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"realpack_{name}", PACKAGE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hardware_config, asset_pack, robot, cameras = (
    _load(name) for name in ("hardware_config", "asset_pack", "robot", "cameras"))


@pytest.fixture(scope="module")
def cell():
    config, _ = hardware_config.load_config()
    try:
        pack = asset_pack.resolve_pack(config)
    except asset_pack.PackError as error:
        pytest.skip(f"real pack unavailable: {error}")
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    report = robot.build_robot(stage, pack, config)
    assets = {role: pack.asset(config["assets"][role]) for role in config["assets"]}
    camera_report = cameras.build_cameras(stage, assets, config, report)
    return config, stage, report, camera_report, pack


def _vertices_in_frame(stage, frame, roots):
    """Visible mesh vertices of ``roots`` in ``frame`` (USD matrices are row-vector: v * M)."""
    cache = UsdGeom.XformCache()
    inverse = cache.GetLocalToWorldTransform(stage.GetPrimAtPath(frame)).GetInverse()
    chunks = []
    for root in roots:
        for prim in Usd.PrimRange(stage.GetPrimAtPath(root)):
            if not prim.IsA(UsdGeom.Mesh) or UsdGeom.Imageable(prim).ComputePurpose() not in ("default", "render"):
                continue
            points = np.array(UsdGeom.Mesh(prim).GetPointsAttr().Get(), dtype=float)
            matrix = np.array(cache.GetLocalToWorldTransform(prim) * inverse)
            chunks.append((np.c_[points, np.ones(len(points))] @ matrix)[:, :3])
    return np.vstack(chunks)


def test_wrist_view_is_clear_of_the_gripper_stack(cell):
    config, stage, report, *_ = cell
    section = config["wrist_camera"]
    assert section["rotation_in_tool0_deg"][1:] == [0.0, 0.0]
    tilt = math.radians(section["rotation_in_tool0_deg"][0])
    margin = 3.0  # degrees beyond the colour field of view that must also stay clear
    points = _vertices_in_frame(stage, report["tool0_path"], [
        report["gripper_path"], f"{report['assembly_path']}/quick_changer", report["bota_body_path"]])
    assert len(points) > 100_000
    # Rx(tilt) rotates the optical frame; optical coordinates are R^T (p - offset).
    rotation = np.array([[1, 0, 0], [0, math.cos(tilt), -math.sin(tilt)], [0, math.sin(tilt), math.cos(tilt)]])
    optical = (points - np.array(section["offset_in_tool0_m"])) @ rotation
    ahead = optical[:, 2] > cameras.DEFAULT_CLIPPING_RANGE_M[0]  # nearer than the clip plane is not drawn
    angle_x = np.degrees(np.arctan2(np.abs(optical[ahead, 0]), optical[ahead, 2]))
    angle_y = np.degrees(np.arctan2(np.abs(optical[ahead, 1]), optical[ahead, 2]))
    half_x, half_y = section["color_fov_deg"][0] / 2 + margin, section["color_fov_deg"][1] / 2 + margin
    inside = (angle_x < half_x) & (angle_y < half_y)
    assert not inside.any(), f"{int(inside.sum())} RG2/Quick Changer/Bota vertices fall in the wrist frame"


def test_wrist_camera_prim_carries_the_configured_tilt_once(cell):
    config, stage, report, camera_report, _ = cell
    cache = UsdGeom.XformCache()
    camera = cache.GetLocalToWorldTransform(stage.GetPrimAtPath(camera_report["cameras"]["wrist"]["prim_path"]))
    tool0 = cache.GetLocalToWorldTransform(stage.GetPrimAtPath(report["tool0_path"]))
    view = tool0.GetInverse().TransformDir(camera.TransformDir(Gf.Vec3d(0, 0, -1)))
    tilt = math.degrees(math.atan2(-view[1], view[2]))
    assert tilt == pytest.approx(config["wrist_camera"]["rotation_in_tool0_deg"][0], abs=1e-6)


def _bounds(stage, path):
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"]).ComputeWorldBound(
        stage.GetPrimAtPath(path)).ComputeAlignedRange()
    return np.array(box.GetMin()), np.array(box.GetMax())


def test_overhead_camera_is_attached_to_the_gantry_through_its_release_stack(cell):
    config, stage, _, camera_report, _ = cell
    root = cameras.OVERHEAD_ROOT
    body, shelf, release, mount = (_bounds(stage, f"{root}/{name}") for name in ("body", "shelf", "release", "mount"))
    tolerance = 0.002
    assert abs(body[1][2] - shelf[0][2]) <= tolerance      # camera back face meets the shelf
    assert abs(shelf[1][2] - release[0][2]) <= tolerance   # shelf meets the release plate
    beam = [_bounds(stage, f"{root}/mount/TopBeam")]
    assert abs(beam[0][0][2] - release[1][2]) <= tolerance  # release meets the beam underside
    # The stack sits under the beam, within its span, and the camera is laterally centred on it.
    centre = (body[0] + body[1]) / 2
    assert beam[0][0][1] <= centre[1] <= beam[0][1][1]
    assert beam[0][0][0] <= centre[0] <= beam[0][1][0]
    assert release[1][2] <= mount[1][2]


PANEL = "imported_PANEL_B2F52AEB2A0D_804849_271/Mesh_91"


def test_gantry_posts_stand_on_the_bench_top_panel(cell):
    config, stage, _, _, pack = cell
    root = cameras.OVERHEAD_ROOT
    bench = Usd.Stage.Open(str(pack.asset(config["assets"]["bench"])))
    panel = next((prim for prim in bench.Traverse() if str(prim.GetPath()).endswith(PANEL)), None)
    if panel is None:
        pytest.skip("bench top panel prim not found in this pack")
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(panel).ComputeAlignedRange()
    scale = config["world"]["bench_scale"]
    ox, oy = config["world"]["workstation_xy_m"]
    low = (box.GetMin()[0] * scale + ox, box.GetMin()[1] * scale + oy)
    high = (box.GetMax()[0] * scale + ox, box.GetMax()[1] * scale + oy)
    for name in ("LeftPost", "RightPost"):
        post = _bounds(stage, f"{root}/mount/{name}")
        assert post[0][2] == pytest.approx(config["world"]["tabletop_z_m"], abs=1e-6)
        for axis in (0, 1):
            assert low[axis] <= post[0][axis] and post[1][axis] <= high[axis], (name, axis, low, high)


def test_rg2_has_no_geometry_free_body_and_all_joint_frames_agree(cell):
    config, stage, report, *_ = cell
    assert report["frame_bodies_neutralized"] == [f"{report['gripper_path']}/gripper_center"]
    assert report["joints_deactivated"] == [f"{report['robot_path']}/joints/robot_gripper_joint"]
    assert report["velocity_iterations_clamped"] == [report["gripper_path"]]
    for prim in Usd.PrimRange(stage.GetPrimAtPath(report["assembly_path"])):
        count = prim.GetAttribute("physxArticulation:solverVelocityIterationCount")
        assert not count or (count.Get() or 0) <= 4, prim.GetPath()
    cache = UsdGeom.XformCache()
    for prim in Usd.PrimRange(stage.GetPrimAtPath(report["assembly_path"])):
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            assert any(item.IsA(UsdGeom.Gprim) for item in Usd.PrimRange(prim, Usd.TraverseInstanceProxies())), (
                f"{prim.GetPath()} is a rigid body without geometry")
        if not prim.IsA(UsdPhysics.Joint):
            continue
        joint = UsdPhysics.Joint(prim)
        if joint.GetJointEnabledAttr().Get() is False:
            continue
        bodies = [joint.GetBody0Rel().GetTargets(), joint.GetBody1Rel().GetTargets()]
        if not all(bodies):
            continue  # an anchor such as the articulation root_joint
        frames = []
        for side, targets in enumerate(bodies):
            position, rotation = (getattr(joint, f"GetLocalPos{side}Attr")().Get(),
                                  getattr(joint, f"GetLocalRot{side}Attr")().Get())
            local = Gf.Matrix4d().SetRotate(Gf.Quatd(rotation.GetReal(), *rotation.GetImaginary()))
            local.SetTranslateOnly(Gf.Vec3d(*position))
            frames.append(local * cache.GetLocalToWorldTransform(stage.GetPrimAtPath(targets[0])))
        assert (frames[0].ExtractTranslation() - frames[1].ExtractTranslation()).GetLength() < 1e-3, prim.GetPath()
        relative = frames[0].ExtractRotationQuat() * frames[1].ExtractRotationQuat().GetInverse()
        assert 2 * math.degrees(math.acos(min(1.0, abs(relative.GetReal())))) < 0.5, prim.GetPath()

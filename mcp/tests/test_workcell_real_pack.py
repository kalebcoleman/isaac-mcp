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

PACKAGE = Path(__file__).resolve().parents[2] / "exts/ur5e-dt/ur5e_dt"
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


def _in_frame(stage, frame, roots):
    cache = UsdGeom.XformCache()
    inverse = cache.GetLocalToWorldTransform(stage.GetPrimAtPath(frame)).GetInverse()
    triangles = []
    for root in roots:
        for prim in Usd.PrimRange(stage.GetPrimAtPath(root)):
            if not prim.IsA(UsdGeom.Mesh) or UsdGeom.Imageable(prim).ComputePurpose() not in ("default", "render"):
                continue
            mesh = UsdGeom.Mesh(prim)
            points = np.array(mesh.GetPointsAttr().Get(), dtype=float)
            matrix = np.array(cache.GetLocalToWorldTransform(prim) * inverse).T
            points = (np.c_[points, np.ones(len(points))] @ matrix)[:, :3]
            offset, indices = 0, mesh.GetFaceVertexIndicesAttr().Get()
            for count in mesh.GetFaceVertexCountsAttr().Get():
                for k in range(1, count - 1):
                    triangles.append((points[indices[offset]], points[indices[offset + k]],
                                      points[indices[offset + k + 1]]))
                offset += count
    return np.array(triangles)


def _first_hit(origin, direction, triangles):
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    e1, e2 = b - a, c - a
    pv = np.cross(direction, e2)
    det = (e1 * pv).sum(1)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1 / np.where(ok, det, 1), 0)
    tv = origin - a
    u = (tv * pv).sum(1) * inv
    qv = np.cross(tv, e1)
    v = (qv * direction).sum(1) * inv
    t = (e2 * qv).sum(1) * inv
    return bool((ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0) & (t < 2.0)).any())


def test_wrist_view_is_clear_of_the_gripper_stack(cell):
    config, stage, report, *_ = cell
    section = config["wrist_camera"]
    origin = np.array(section["offset_in_tool0_m"])
    rx = math.radians(section["rotation_in_tool0_deg"][0])
    assert section["rotation_in_tool0_deg"][1:] == [0.0, 0.0]
    rotation = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
    margin = 3.0  # degrees of extra field of view on each side
    half_x = math.tan(math.radians(section["color_fov_deg"][0] / 2 + margin))
    half_y = math.tan(math.radians(section["color_fov_deg"][1] / 2 + margin))
    triangles = _in_frame(stage, report["tool0_path"], [report["gripper_path"], f"{report['assembly_path']}/quick_changer",
                                                          report["bota_body_path"]])
    assert len(triangles) > 1000
    blocked = [(x, y) for x in np.linspace(-half_x, half_x, 21) for y in np.linspace(-half_y, half_y, 13)
               if _first_hit(origin, rotation @ np.array([x, y, 1.0]), triangles)]
    assert not blocked, f"{len(blocked)} wrist rays hit the RG2/Quick Changer/Bota"


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


def test_gantry_posts_stand_on_the_bench_footprint(cell):
    config, stage, _, _, pack = cell
    root = cameras.OVERHEAD_ROOT
    bench = Usd.Stage.Open(str(pack.asset(config["assets"]["bench"])))
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(
        bench.GetDefaultPrim()).ComputeAlignedRange()
    scale = config["world"]["bench_scale"]
    ox, oy = config["world"]["workstation_xy_m"]
    low = (box.GetMin()[0] * scale + ox, box.GetMin()[1] * scale + oy)
    high = (box.GetMax()[0] * scale + ox, box.GetMax()[1] * scale + oy)
    for name in ("LeftPost", "RightPost"):
        post = _bounds(stage, f"{root}/mount/{name}")
        assert post[0][2] == pytest.approx(config["world"]["tabletop_z_m"], abs=1e-6)
        for axis in (0, 1):
            assert low[axis] <= post[0][axis] and post[1][axis] <= high[axis], (name, axis)


def test_rg2_has_no_geometry_free_body_and_all_joint_frames_agree(cell):
    config, stage, report, *_ = cell
    assert report["frame_bodies_neutralized"] == [f"{report['gripper_path']}/gripper_center"]
    assert report["joints_deactivated"] == [f"{report['robot_path']}/joints/robot_gripper_joint"]
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

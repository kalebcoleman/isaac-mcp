"""Real OpenUSD regressions for referenced Euler/quaternion object transforms."""

import importlib.util
import math
from pathlib import Path

import pytest

pytest.importorskip('pxr')
from pxr import Gf, Usd, UsdGeom

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('twin_object_pose', ROOT / 'exts/ur5e-dt/ur5e_dt/object_pose.py')
pose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pose)


@pytest.mark.parametrize('rotation', ['euler', 'orient', 'none'])
@pytest.mark.parametrize('precision', [UsdGeom.XformOp.PrecisionFloat, UsdGeom.XformOp.PrecisionDouble])
def test_pose_activates_rotation_preserves_scale_and_roundtrips(rotation, precision):
    stage = Usd.Stage.CreateInMemory()
    body = UsdGeom.Xform.Define(stage, '/Body')
    body.AddTranslateOp().Set(Gf.Vec3d(1, 2, 3))
    if rotation == 'euler':
        body.AddRotateZYXOp().Set(Gf.Vec3f(0, 0, 19))
    elif rotation == 'orient':
        body.AddOrientOp(precision).Set(Gf.Quatf(1) if precision == UsdGeom.XformOp.PrecisionFloat else Gf.Quatd(1))
    body.AddScaleOp().Set(Gf.Vec3f(.01, .02, .03))
    before = stage.GetRootLayer().ExportToString()
    stage.SetEditTarget(stage.GetSessionLayer())
    q = Gf.Rotation(Gf.Vec3d(0, 0, 1), 35).GetQuat()
    pose.set_local_pose(body.GetPrim(), [.22, -.41, .11], [q.GetReal(), *q.GetImaginary()])
    matrix = body.GetLocalTransformation()
    assert list(matrix.ExtractTranslation()) == pytest.approx([.22, -.41, .11])
    assert list(matrix.TransformDir(Gf.Vec3d(1, 0, 0))) == pytest.approx([.01 * math.cos(math.radians(35)), .01 * math.sin(math.radians(35)), 0])
    saved = pose.read_local_pose(body.GetPrim())
    assert list(saved['scale'].values()) == pytest.approx([.01, .02, .03])
    pose.set_local_pose(body.GetPrim(), [0, 0, 0], [1, 0, 0, 0])
    pose.set_local_pose(body.GetPrim(), saved['position'].values(), saved['quaternion'].values(), saved['scale'].values())
    assert Gf.IsClose(body.GetLocalTransformation(), matrix, 1e-6)
    assert stage.GetRootLayer().ExportToString() == before


@pytest.mark.parametrize('bad', ['zero_quat', 'nan', 'animated', 'matrix', 'pivot', 'negative_scale'])
def test_invalid_pose_refuses_without_mutation(bad):
    stage = Usd.Stage.CreateInMemory()
    xf = UsdGeom.Xform.Define(stage, '/Body')
    t = xf.AddTranslateOp(); t.Set(Gf.Vec3d(0))
    if bad == 'animated': t.Set(Gf.Vec3d(1), 1)
    if bad == 'matrix': xf.AddTransformOp().Set(Gf.Matrix4d(1))
    if bad == 'pivot': xf.AddTranslateOp(opSuffix='pivot').Set(Gf.Vec3d(1))
    before = stage.GetRootLayer().ExportToString()
    q = [0, 0, 0, 0] if bad == 'zero_quat' else [1, 0, 0, 0]
    p = [float('nan'), 0, 0] if bad == 'nan' else [1, 2, 3]
    with pytest.raises(ValueError):
        pose.set_local_pose(xf.GetPrim(), p, q, [-1, 1, 1] if bad == 'negative_scale' else None)
    assert stage.GetRootLayer().ExportToString() == before


def test_referenced_fmb_body_rotation_and_source_preservation():
    asset = ROOT / 'exts/ur5e-dt/assets/objects/fmb1/u_brown_physics.usda'
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim('/Object').GetReferences().AddReference(str(asset))
    prim = stage.GetPrimAtPath('/Object/u_brown/u_brown')
    source_before = {layer.identifier: layer.ExportToString() for layer in stage.GetUsedLayers()}
    stage.SetEditTarget(stage.GetSessionLayer())
    pose.set_local_pose(prim, [0, 0, .1], [math.cos(math.pi/4), 0, 0, math.sin(math.pi/4)])
    assert list(UsdGeom.Xformable(prim).GetLocalTransformation().TransformDir(Gf.Vec3d(1, 0, 0))) == pytest.approx([0, .01, 0], abs=1e-7)
    for layer in stage.GetUsedLayers():
        if layer != stage.GetSessionLayer():
            assert layer.ExportToString() == source_before[layer.identifier]

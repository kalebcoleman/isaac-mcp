"""The render workaround changes only referenced robot visual instancing."""
import importlib.util
from pathlib import Path

import pytest
pytest.importorskip('pxr')
from pxr import Usd, UsdGeom, UsdPhysics

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('robot_visuals', ROOT/'exts/ur5e-dt/ur5e_dt/robot_visuals.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_expands_only_owned_robot_visual_instances_without_touching_sources(tmp_path):
    geometry=Usd.Stage.CreateNew(str(tmp_path/'shape.usda'))
    geo=UsdGeom.Xform.Define(geometry,'/Shape').GetPrim()
    geometry.SetDefaultPrim(geo)
    UsdGeom.Cube.Define(geometry,'/Shape/Mesh').GetSizeAttr().Set(.2)
    geometry.GetRootLayer().Save()
    robot=Usd.Stage.CreateNew(str(tmp_path/'robot.usda'))
    root=UsdGeom.Xform.Define(robot,'/Robot').GetPrim();robot.SetDefaultPrim(root)
    body=UsdGeom.Xform.Define(robot,'/Robot/Link').GetPrim();UsdPhysics.RigidBodyAPI.Apply(body)
    for name in ('visuals','collisions'):
        prim=UsdGeom.Xform.Define(robot,'/Robot/Link/'+name).GetPrim()
        prim.GetReferences().AddReference('./shape.usda');prim.SetInstanceable(True)
    robot.GetRootLayer().Save()
    source_bytes={p:p.read_bytes() for p in tmp_path.glob('*.usda')}
    stage=Usd.Stage.CreateInMemory()
    for name in ('Twin','Unrelated'):
        stage.DefinePrim('/'+name).GetReferences().AddReference(str(tmp_path/'robot.usda'))
    before=stage.GetRootLayer().ExportToString()
    stage.SetEditTarget(stage.GetSessionLayer())
    assert stage.GetPrimAtPath('/Twin/Link/visuals').IsInstance()
    assert module.prepare_robot_visuals(stage,'/Twin') == ['/Twin/Link/visuals']
    assert not stage.GetPrimAtPath('/Twin/Link/visuals').IsInstance()
    assert stage.GetPrimAtPath('/Twin/Link/visuals/Mesh').IsA(UsdGeom.Cube)
    assert stage.GetPrimAtPath('/Twin/Link/collisions').IsInstance()
    assert stage.GetPrimAtPath('/Unrelated/Link/visuals').IsInstance()
    assert module.prepare_robot_visuals(stage,'/Twin') == []
    assert stage.GetRootLayer().ExportToString()==before
    assert {p:p.read_bytes() for p in source_bytes}==source_bytes


def test_missing_root_refuses_without_authoring():
    stage=Usd.Stage.CreateInMemory();before=stage.GetRootLayer().ExportToString()
    with pytest.raises(ValueError,match='not editable'):
        module.prepare_robot_visuals(stage,'/Missing')
    assert stage.GetRootLayer().ExportToString()==before

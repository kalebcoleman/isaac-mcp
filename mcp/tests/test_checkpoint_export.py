"""Real OpenUSD regression: root-only export used to lose session scene definitions."""
import datetime
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
from types import SimpleNamespace

import pytest
pytest.importorskip('pxr.Usd')
from pxr import Sdf, Usd, UsdGeom, UsdPhysics

REPO = Path(__file__).resolve().parents[2]
module = runpy.run_path(str(REPO / 'scripts/checkpoint_export.py'))
export = module['export_checkpoint']


def fixture_stage():
    stage = Usd.Stage.CreateInMemory()
    root = stage.GetRootLayer()
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        p = UsdPhysics.Scene.Define(stage, '/physicsScene').GetPrim()
        p.AddAppliedSchema('PhysxSceneAPI')
        p.AddAppliedSchema('NewtonSceneAPI')
        p.CreateAttribute('physxScene:timeStepsPerSecond', Sdf.ValueTypeNames.UInt).Set(120)
        UsdGeom.Xform.Define(stage, '/SessionOnly')
    # A stronger session over must combine with these weaker root settings.
    with Usd.EditContext(stage, root):
        p = stage.OverridePrim('/physicsScene')
        for name, typ, value in [
            ('physxScene:enableGPUDynamics', Sdf.ValueTypeNames.Bool, False),
            ('physxScene:broadphaseType', Sdf.ValueTypeNames.Token, 'MBP'),
            ('physics:gravityMagnitude', Sdf.ValueTypeNames.Float, 9.81),
        ]:
            p.CreateAttribute(name, typ).Set(value)
    return stage


def hashes(stage):
    return {l.identifier: l.ExportToString() for l in stage.GetUsedLayers()}


def test_composed_local_scene_preserved_without_copying_session_or_assets(tmp_path):
    stage = fixture_stage()
    cache = tmp_path / 'operator cache'; cache.mkdir()
    asset = Usd.Stage.CreateNew(str(cache / 'robot.usda'))
    asset.SetDefaultPrim(UsdGeom.Xform.Define(asset, '/Robot').GetPrim())
    UsdGeom.Cube.Define(asset, '/Robot/Link'); asset.GetRootLayer().Save()
    stage.DefinePrim('/Robot').GetReferences().AddReference(str(cache / 'robot.usda'))
    stage.DefinePrim('/Field').CreateAttribute('asset', Sdf.ValueTypeNames.Asset).Set((cache/'robot.usda').as_uri())
    before = hashes(stage); target = tmp_path/'checkpoint.usda'
    result = export(stage, target)
    saved = Usd.Stage.Open(str(target)); prim = saved.GetPrimAtPath('/physicsScene')
    assert prim.IsA(UsdPhysics.Scene)
    assert prim.GetAttribute('physxScene:timeStepsPerSecond').Get() == 120
    assert prim.GetAttribute('physxScene:enableGPUDynamics').Get() is False
    assert prim.GetAttribute('physxScene:broadphaseType').Get() == 'MBP'
    # usd-core has no NVIDIA schema plugin, but must retain its authored API token.
    assert 'PhysxSceneAPI' in prim.GetMetadata('apiSchemas').GetAppliedItems()
    assert 'NewtonSceneAPI' in prim.GetMetadata('apiSchemas').GetAppliedItems()
    assert not saved.GetPrimAtPath('/SessionOnly')
    assert saved.GetPrimAtPath('/Robot/Link')
    assert saved.GetPrimAtPath('/Robot').HasAuthoredReferences()
    assert saved.GetRootLayer().GetPrimAtPath('/Robot/Link') is None
    assert result['relative_asset_refs'] == 2 and result['absolute_refs_left'] == 0
    assert hashes(stage) == before
    assert saved.GetPrimAtPath('/Field').GetAttribute('asset').Get().resolvedPath == str(cache/'robot.usda')


def test_stronger_over_composes_with_weaker_type_without_forcing_rate(tmp_path):
    stage = Usd.Stage.CreateInMemory()
    scene = UsdPhysics.Scene.Define(stage, '/physicsScene')
    scene.CreateGravityMagnitudeAttr(2.5)
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        p = stage.OverridePrim('/physicsScene')
        p.CreateAttribute('physxScene:timeStepsPerSecond', Sdf.ValueTypeNames.UInt).Set(240)
    export(stage, tmp_path/'copy.usda')
    saved = Usd.Stage.Open(str(tmp_path/'copy.usda'))
    assert saved.GetPrimAtPath('/physicsScene').IsA(UsdPhysics.Scene)
    assert saved.GetPrimAtPath('/physicsScene').GetAttribute('physxScene:timeStepsPerSecond').Get() == 240
    assert UsdPhysics.Scene(saved.GetPrimAtPath('/physicsScene')).GetGravityMagnitudeAttr().Get() == 2.5


def test_relative_sublayer_assets_keep_original_anchor(tmp_path):
    folder = tmp_path/'sources'; folder.mkdir(); (folder/'sub').mkdir()
    asset = Usd.Stage.CreateNew(str(folder/'sub/asset.usda'))
    asset.SetDefaultPrim(UsdGeom.Xform.Define(asset,'/Thing').GetPrim()); asset.GetRootLayer().Save()
    sub = Sdf.Layer.CreateNew(str(folder/'sub/local.usda'))
    sub_stage = Usd.Stage.Open(sub); sub_stage.DefinePrim('/Ref').GetReferences().AddReference('./asset.usda'); sub.Save()
    source = Sdf.Layer.CreateNew(str(folder/'root.usda')); source.subLayerPaths = ['sub/local.usda']; source.Save()
    stage = Usd.Stage.Open(source)
    with Usd.EditContext(stage, stage.GetSessionLayer()): UsdPhysics.Scene.Define(stage,'/physicsScene')
    before = hashes(stage); dest = tmp_path/'exports';dest.mkdir()
    export(stage,dest/'copy.usda'); saved = Usd.Stage.Open(str(dest/'copy.usda'))
    assert saved.GetPrimAtPath('/Ref').IsA(UsdGeom.Xform)
    assert len(saved.GetRootLayer().subLayerPaths) == 1
    assert hashes(stage) == before


@pytest.mark.parametrize('case', ['multiple', 'reference', 'child', 'asset', 'connection', 'opaque', 'runtime', 'referenced_runtime'])
def test_unsupported_contributions_refuse_without_output_or_source_edits(tmp_path, case):
    stage = fixture_stage(); p = stage.GetPrimAtPath('/physicsScene')
    if case == 'multiple': UsdPhysics.Scene.Define(stage,'/Other')
    elif case == 'reference':
        ext=Usd.Stage.CreateNew(str(tmp_path/'physics.usda')); ext.SetDefaultPrim(UsdPhysics.Scene.Define(ext,'/Scene').GetPrim());ext.GetRootLayer().Save()
        p.GetReferences().AddReference(str(tmp_path/'physics.usda'))
    elif case == 'child': stage.DefinePrim('/physicsScene/Child')
    elif case == 'asset': p.CreateAttribute('physics:file', Sdf.ValueTypeNames.Asset).Set('anything.usda')
    elif case == 'connection': p.GetAttribute('physics:gravityMagnitude').AddConnection('/World.mass')
    elif case == 'opaque': p.CreateAttribute('physxScene:opaqueHandle', Sdf.ValueTypeNames.UInt64).Set(16)
    elif case == 'runtime': stage.DefinePrim('/Render/P','RenderProduct').CreateAttribute('viewportHandle',Sdf.ValueTypeNames.Int).Set(16)
    elif case == 'referenced_runtime':
        ext=Usd.Stage.CreateNew(str(tmp_path/'render.usda')); ext.SetDefaultPrim(ext.DefinePrim('/Root'))
        ext.DefinePrim('/Root/Hidden/P','RenderProduct').CreateAttribute('viewportHandle',Sdf.ValueTypeNames.Int).Set(16)
        ext.GetPrimAtPath('/Root/Hidden').SetActive(False);ext.GetRootLayer().Save()
        stage.DefinePrim('/Ref').GetReferences().AddReference(str(tmp_path/'render.usda'))
    before=hashes(stage);target=tmp_path/'copy.usda'
    with pytest.raises(ValueError): export(stage,target)
    assert not target.exists() and not list(tmp_path.glob('.checkpoint-*'))
    assert hashes(stage) == before


def test_existing_and_racing_output_never_overwritten(tmp_path, monkeypatch):
    stage=fixture_stage(); target=tmp_path/'copy.usda';target.write_bytes(b'keep')
    with pytest.raises(FileExistsError):export(stage,target)
    assert target.read_bytes()==b'keep';target.unlink()
    real_link=os.link
    def racing_link(source,destination):
        Path(destination).write_bytes(b'new arrival')
        real_link(source,destination)
    monkeypatch.setattr(os,'link',racing_link)
    with pytest.raises(FileExistsError):export(stage,target)
    assert target.read_bytes()==b'new arrival' and not list(tmp_path.glob('.checkpoint-*'))


def test_timestamp_export_collision_does_not_overwrite(tmp_path, monkeypatch):
    root=tmp_path/'repo';(root/'scripts').mkdir(parents=True);(root/'checkpoints').mkdir()
    shutil.copy(REPO/'scripts/checkpoint_export.py',root/'scripts/checkpoint_export.py')
    stage=fixture_stage();usd=SimpleNamespace(get_context=lambda:SimpleNamespace(get_stage=lambda:stage))
    monkeypatch.setitem(sys.modules,'omni',SimpleNamespace(usd=usd));monkeypatch.setitem(sys.modules,'omni.usd',usd)
    instant=datetime.datetime(2026,1,2,tzinfo=datetime.timezone.utc)
    class FixedDateTime(datetime.datetime):
        @classmethod
        def now(cls,tz=None):return instant
    monkeypatch.setattr(datetime,'datetime',FixedDateTime)
    script=REPO/'scripts/checks/export_checkpoint.py';code=compile(script.read_text().replace('__ROOT__',str(root)),str(script),'exec')
    ns={};exec(code,ns);target=Path(ns['result']['file']);before=target.read_bytes()
    with pytest.raises(FileExistsError):exec(code,{})
    assert target.read_bytes()==before

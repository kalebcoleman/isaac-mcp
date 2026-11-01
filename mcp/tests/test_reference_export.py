"""Checkpoint references survive cache relocation without editing source USDs."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr.Usd")
from pxr import Sdf, Usd, UsdGeom


def test_export_rewrites_plain_and_file_uri_assets_in_private_copy(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    checkpoints = root / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / "ur5e-dt-workstation-person-view-portable.json").write_text(
        json.dumps({"checkpoint": "test.usda"})
    )
    cache = tmp_path / "operator cache"
    cache.mkdir()
    asset = Usd.Stage.CreateNew(str(cache / "robot.usda"))
    asset.SetDefaultPrim(UsdGeom.Xform.Define(asset, "/Robot").GetPrim())
    asset.GetRootLayer().Save()
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim("/Plain").GetReferences().AddReference(str(cache / "robot.usda"))
    # The generic asset-valued field tests URI decoding too; no file is copied.
    stage.DefinePrim("/Asset").CreateAttribute("asset", Sdf.ValueTypeNames.Asset).Set(
        (cache / "robot.usda").as_uri()
    )
    source_before = stage.GetRootLayer().ExportToString()
    asset_before = (cache / "robot.usda").read_bytes()
    usd = SimpleNamespace(get_context=lambda: SimpleNamespace(get_stage=lambda: stage))
    monkeypatch.setitem(sys.modules, "omni", SimpleNamespace(usd=usd))
    monkeypatch.setitem(sys.modules, "omni.usd", usd)
    script = Path(__file__).resolve().parents[2] / "scripts/checks/export_reference.py"
    namespace = {}
    exec(compile(script.read_text().replace("__ROOT__", str(root)), str(script), "exec"), namespace)
    assert namespace["result"]["relative_asset_refs"] == 2
    assert namespace["result"]["absolute_refs_left"] == 0
    saved = Usd.Stage.Open(str(checkpoints / "test.usda"))
    assert saved.GetPrimAtPath("/Plain").IsA(UsdGeom.Xform)
    assert saved.GetPrimAtPath("/Plain").HasAuthoredReferences()
    value = saved.GetPrimAtPath("/Asset").GetAttribute("asset").Get()
    assert value.path.startswith("../") and Path(value.resolvedPath) == cache / "robot.usda"
    assert stage.GetRootLayer().ExportToString() == source_before
    assert (cache / "robot.usda").read_bytes() == asset_before

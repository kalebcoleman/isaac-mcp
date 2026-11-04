"""sim.stage.open's unsaved-work check: real edits count, Kit's own bookkeeping does not."""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("pxr")
from pxr import Sdf, Usd, UsdGeom  # noqa: E402

MODULE = Path(__file__).resolve().parents[2] / "exts" / "sim-tools" / "sim_tools" / "stage_open.py"
spec = importlib.util.spec_from_file_location("stage_open_under_test", MODULE)
stage_open = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage_open)


@pytest.fixture
def stage(tmp_path):
    path = tmp_path / "scene.usda"
    made = Usd.Stage.CreateNew(str(path))
    UsdGeom.Xform.Define(made, "/World")
    made.GetRootLayer().Save()
    Sdf.Layer.Find(str(path)).Reload(force=True)
    return Usd.Stage.Open(str(path))


def test_a_freshly_opened_file_has_nothing_to_lose(stage):
    assert not stage_open.file_stage_has_unsaved_work(stage)


def test_kit_rewriting_custom_layer_data_is_not_an_edit(stage):
    root = stage.GetRootLayer()
    root.customLayerData = {"cameraSettings": {"boundCamera": "/OmniverseKit_Persp"}}
    assert root.dirty
    assert not stage_open.file_stage_has_unsaved_work(stage)


def test_kit_session_cameras_are_not_edits(stage):
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdGeom.Camera.Define(stage, "/OmniverseKit_Persp")
        UsdGeom.Xform.Define(stage, "/Render/Vars")
    assert not stage_open.file_stage_has_unsaved_work(stage)


def test_kit_turning_saved_viewport_cameras_into_overs_is_not_an_edit(tmp_path):
    path = tmp_path / "flattened.usda"
    made = Usd.Stage.CreateNew(str(path))
    UsdGeom.Xform.Define(made, "/World")
    UsdGeom.Camera.Define(made, "/OmniverseKit_Persp")
    made.GetRootLayer().Save()
    stage = Usd.Stage.Open(str(path))
    stage.GetRootLayer().GetPrimAtPath("/OmniverseKit_Persp").specifier = Sdf.SpecifierOver
    assert stage.GetRootLayer().dirty
    assert not stage_open.file_stage_has_unsaved_work(stage)


def test_a_real_root_layer_edit_is_unsaved_work(stage):
    UsdGeom.Xform.Define(stage, "/World/Added")
    assert stage_open.file_stage_has_unsaved_work(stage)


def test_a_session_layer_edit_from_our_tools_is_unsaved_work(stage):
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdGeom.Xform.Define(stage, "/World/FromTool")
    assert stage_open.session_edits(stage) == ["World"]
    assert stage_open.file_stage_has_unsaved_work(stage)


def test_an_oversized_file_keeps_the_safe_answer(stage, monkeypatch):
    monkeypatch.setattr(stage_open, "MAX_COMPARE_BYTES", 0)
    stage.GetRootLayer().customLayerData = {"a": 1}
    assert stage_open.file_stage_has_unsaved_work(stage)

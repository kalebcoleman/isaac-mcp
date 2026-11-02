"""Physical camera clipping must not inherit a meter-scale near plane."""

import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr")
from pxr import Usd, UsdGeom
from test_scene_usd import scene
from isaac_mcp.project_assets import AssetError
from sim_tools import sensors


@pytest.mark.parametrize("units", [0.001, 0.01, 1.0, 1000.0])
def test_camera_clipping_uses_physical_distances_and_preserves_root(scene, monkeypatch, units):
    stage = scene.stage()
    UsdGeom.SetStageMetersPerUnit(stage, units)
    before = stage.GetRootLayer().ExportToString()
    usd = SimpleNamespace(get_context=lambda: SimpleNamespace(get_stage=lambda: stage))
    monkeypatch.setitem(sys.modules, "omni", SimpleNamespace(usd=usd))
    monkeypatch.setitem(sys.modules, "omni.usd", usd)
    product = SimpleNamespace(path="/Render/Camera", hydra_texture=object())
    rep = SimpleNamespace(create=SimpleNamespace(render_product=lambda *a, **kw: product))
    monkeypatch.setattr(sensors, "require_sensor_api", lambda **kw: (rep, dict(release="test", build="test")))
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        sensor = sensors.create(stage, "/World/NearCamera", "camera", 96, 64)
    camera = UsdGeom.Camera(stage.GetPrimAtPath("/World/NearCamera"))
    clipping = list(camera.GetClippingRangeAttr().Get())
    assert [v * units for v in clipping] == pytest.approx([0.01, 1000])
    assert sensor["clipping_range_stage_units"] == clipping
    # The formerly clipped 23 cm fixture is inside the camera frustum depth range.
    assert clipping[0] < 0.23 / units < clipping[1]
    assert stage.GetRootLayer().ExportToString() == before
    assert UsdGeom.GetStageMetersPerUnit(stage) == units
    assert stage.GetEditTarget().GetLayer() == stage.GetRootLayer()
    reopened = Usd.Stage.Open(stage.Flatten())
    assert list(UsdGeom.Camera(reopened.GetPrimAtPath("/World/NearCamera")).GetClippingRangeAttr().Get()) == clipping


@pytest.mark.parametrize("units", [0, -1, float("nan"), float("inf"), 1e-300, 1e300])
def test_invalid_camera_units_refuse_before_prim_or_native_resources(scene, monkeypatch, units):
    stage = scene.stage()
    UsdGeom.SetStageMetersPerUnit(stage, units)
    before = stage.GetRootLayer().ExportToString(), stage.GetSessionLayer().ExportToString()
    monkeypatch.setattr(sensors, "require_sensor_api", lambda **kw: pytest.fail("native API reached"))
    with pytest.raises(AssetError) as err:
        sensors.create(stage, "/World/NearCamera", "camera")
    assert err.value.code == "INVALID_STAGE_UNITS"
    assert not stage.GetPrimAtPath("/World/NearCamera")
    assert (stage.GetRootLayer().ExportToString(), stage.GetSessionLayer().ExportToString()) == before

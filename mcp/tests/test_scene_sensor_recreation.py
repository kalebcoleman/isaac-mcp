"""External USD removal must not lose sensor resource accounting."""
from types import SimpleNamespace
import pytest
from test_scene_usd import scene
from pxr import Usd
from isaac_mcp.project_assets import AssetError
from sim_tools.advanced import Advanced


@pytest.mark.parametrize("old_kind", ["camera", "lidar"])
@pytest.mark.parametrize("new_kind", ["camera", "lidar"])
def test_sensor_recreation_refuses_registered_path_after_external_removal(
    scene, monkeypatch, old_kind, new_kind
):
    from sim_tools import sensors
    stage = scene.stage()
    advanced = Advanced(scene, None)
    created, disposed = [], []
    path = "/World/Sensor"

    def create(stage, prim_path, kind, width, height, **_options):
        index = len(created)
        stage.DefinePrim(prim_path, "Camera" if kind == "camera" else "OmniLidar")
        result = dict(
            product=SimpleNamespace(destroy=lambda: disposed.append(index)),
            adapter="test", release="test", build="test",
            clipping_range_stage_units=[0.01, 1000.0],
            focal_length_mm=50.0, horizontal_aperture_mm=20.955, vertical_aperture_mm=15.7,
            sensor_path=str(prim_path), tick_rate_hz=10.0, scan_rate_hz=10.0,
        )
        created.append(result)
        return result

    monkeypatch.setattr(sensors, "create", create)
    advanced.execute(f"sim.{old_kind}.create", stage, dict(path=path))
    old_entry = advanced.sensors[path]
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        stage.RemovePrim(path)
    layers = (stage.GetRootLayer(), stage.GetSessionLayer())
    before = [layer.ExportToString() for layer in layers]
    for _ in range(8):
        with pytest.raises(AssetError) as error:
            advanced.execute(f"sim.{new_kind}.create", stage, dict(path=path))
        assert error.value.code == "CONFLICT"
    assert len(created) == 1 and disposed == []
    assert advanced.sensors == {path: old_entry}
    assert advanced.sensors[path] is old_entry
    assert scene.owned == {path}
    assert [layer.ExportToString() for layer in layers] == before
    advanced.close_sensors()
    assert disposed == [0] and advanced.sensors == {}

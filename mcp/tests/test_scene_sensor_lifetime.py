"""Destruction may never reach another stage or a foreign composition layer."""

from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from test_scene_usd import scene
from pxr import Sdf, Usd
from sim_tools.sensors import dispose


@pytest.mark.parametrize("case", ["owned", "foreign", "stage_changed", "stage_closing"])
def test_product_cleanup_is_bound_to_its_stage_path_and_creation_layers(
    scene, monkeypatch, case
):
    stage = scene.stage()
    path = "/Render/OwnedUUID"
    stage.DefinePrim(path, "RenderProduct")
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        stage.OverridePrim(path)
    if case == "foreign":
        layer = Sdf.Layer.CreateAnonymous("user-layer")
        stage.GetRootLayer().subLayerPaths.append(layer.identifier)
        with Usd.EditContext(stage, layer):
            stage.OverridePrim(path).SetCustomDataByKey("user", "preserve")
    current = Usd.Stage.CreateInMemory() if case == "stage_changed" else stage
    if current != stage:
        current.DefinePrim(path).SetCustomDataByKey("user", "different stage")
    usd = SimpleNamespace(
        get_context=lambda: SimpleNamespace(get_stage=lambda: current)
    )
    monkeypatch.setitem(sys.modules, "omni", SimpleNamespace(usd=usd))
    monkeypatch.setitem(sys.modules, "omni.usd", usd)
    calls = []
    texture = SimpleNamespace(
        set_updates_enabled=lambda v: calls.append(("updates", v))
    )
    product = SimpleNamespace(
        hydra_texture=texture, destroy=lambda: calls.append("destroy")
    )
    sensor = dict(
        stage=stage,
        product_path=path,
        product=product,
        product_layers=(stage.GetRootLayer(), stage.GetSessionLayer()),
        annotator=SimpleNamespace(detach=lambda: calls.append("detach")),
    )
    errors = dispose(sensor, stage_closing=case == "stage_closing")
    if case == "owned":
        assert calls == ["detach", "destroy"] and not errors
    else:
        assert calls == [("updates", False)]
        assert product.hydra_texture is None
        assert stage.GetPrimAtPath(path) and current.GetPrimAtPath(path)
        assert bool(errors) == (case != "stage_closing")
    assert sensor["product"] is None and sensor["annotator"] is None
    assert dispose(sensor) == []


def test_creation_cannot_destroy_on_a_new_stage(scene, monkeypatch):
    from sim_tools import sensors
    from isaac_mcp.project_assets import AssetError

    stage = scene.stage()
    other = Usd.Stage.CreateInMemory()
    render_path = "/Render/PartialProduct"
    other.DefinePrim(render_path).SetCustomDataByKey("user", "preserve")
    current = [stage]
    usd = SimpleNamespace(
        get_context=lambda: SimpleNamespace(get_stage=lambda: current[0])
    )
    monkeypatch.setitem(sys.modules, "omni", SimpleNamespace(usd=usd))
    monkeypatch.setitem(sys.modules, "omni.usd", usd)
    calls = []
    product = SimpleNamespace(
        path=render_path,
        hydra_texture=SimpleNamespace(
            set_updates_enabled=lambda enabled: calls.append(("updates", enabled))
        ),
        destroy=lambda: pytest.fail("destruction touched the newly selected stage"),
    )

    def create_product(*args, **kwargs):
        current[0] = other
        return product

    rep = SimpleNamespace(
        create=SimpleNamespace(render_product=create_product),
    )
    monkeypatch.setattr(
        sensors,
        "require_sensor_api",
        lambda **kw: (rep, dict(release="test", build="test")),
    )
    with pytest.raises(AssetError, match="Stage changed") as error:
        sensors.create(stage, "/World/Camera", "camera")
    assert error.value.code == "SENSOR_CREATE_FAILED"
    assert "Stage changed" in str(error.value)
    assert calls == [("updates", False)]
    assert product.hydra_texture is None
    assert other.GetPrimAtPath(render_path).GetCustomDataByKey("user") == "preserve"


def test_camera_creation_needs_no_annotator_and_disposal_clears_early_return(
    scene, monkeypatch
):
    from sim_tools import sensors

    stage = scene.stage()
    usd = SimpleNamespace(get_context=lambda: SimpleNamespace(get_stage=lambda: stage))
    monkeypatch.setitem(sys.modules, "omni", SimpleNamespace(usd=usd))
    monkeypatch.setitem(sys.modules, "omni.usd", usd)
    product = SimpleNamespace(path="/Render/Camera", hydra_texture=object())
    rep = SimpleNamespace(
        create=SimpleNamespace(render_product=lambda *a, **kw: product)
    )
    monkeypatch.setattr(
        sensors,
        "require_sensor_api",
        lambda **kw: (rep, dict(release="test", build="test")),
    )
    sensor = sensors.create(stage, "/World/Camera", "camera", 96, 64)
    assert "annotator" not in sensor and sensor["adapter"] == "hydra-renderer-cpu-v1"
    calls = []
    sensor.update(
        product=None,
        camera_cache=object(),
        readback_pending=SimpleNamespace(close=lambda: calls.append("close")),
    )
    generation = sensor.get("capture_generation")
    assert dispose(sensor) == []
    assert (
        calls == ["close"]
        and "camera_cache" not in sensor
        and "readback_pending" not in sensor
    )
    assert sensor["capture_generation"] is not generation


def test_camera_api_does_not_require_unused_lidar_annotator_module(monkeypatch):
    from sim_tools import runtime
    from isaac_mcp.project_assets import AssetError

    rep = SimpleNamespace(create=SimpleNamespace(render_product=lambda: None))
    monkeypatch.setitem(
        sys.modules, "omni", SimpleNamespace(replicator=SimpleNamespace(core=rep))
    )
    monkeypatch.setitem(sys.modules, "omni.replicator", SimpleNamespace(core=rep))
    monkeypatch.setitem(sys.modules, "omni.replicator.core", rep)
    monkeypatch.setitem(sys.modules, "omni.replicator.core.annotators", None)
    monkeypatch.setattr(runtime, "runtime_identity", lambda: {"qualified": True})
    monkeypatch.setattr(runtime, "sensor_stack_issues", lambda *a, **kw: [])
    assert runtime.require_sensor_api()[0] is rep
    with pytest.raises(AssetError) as error:
        runtime.require_sensor_api(lidar=True)
    assert error.value.code == "SENSOR_API_UNAVAILABLE"


def test_reading_a_sensor_that_was_never_created_says_not_found():
    # A failed create leaves nothing: suggesting adopt=true would send the caller after a prim that isn't there.
    text = (Path(__file__).resolve().parents[2] / "exts/sim-tools/sim_tools/extension.py").read_text(encoding="utf-8")
    assert text.index('refuse("NOT_FOUND", f"No prim at {args[\'path\']}') < text.index('"NOT_OWNED",\n                    f"{args[\'path\']} has no {expected}')


def test_lidar_scan_wait_does_not_stop_on_an_all_zero_first_scan():
    source = (Path(__file__).resolve().parents[2] / "exts/sim-tools/sim_tools/sensors.py").read_text(encoding="utf-8")
    assert 'getattr(data, "size", 0) > 0 and bool(data.any())' in source

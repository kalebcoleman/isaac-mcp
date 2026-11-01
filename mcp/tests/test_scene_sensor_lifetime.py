"""Destruction may never reach another stage or a foreign composition layer."""

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


def test_failed_attachment_cannot_detach_or_destroy_on_a_new_stage(scene, monkeypatch):
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

    def attach(products):
        current[0] = other
        raise RuntimeError("partial native attachment failure")

    annotator = SimpleNamespace(
        attach=attach,
        detach=lambda: pytest.fail("detach uses the global current stage"),
    )
    rep = SimpleNamespace(
        create=SimpleNamespace(render_product=lambda *a, **kw: product),
        AnnotatorRegistry=SimpleNamespace(get_annotator=lambda *a, **kw: annotator),
    )
    monkeypatch.setattr(
        sensors,
        "require_cpu_annotator",
        lambda **kw: (rep, dict(release="test", build="test")),
    )
    with pytest.raises(AssetError, match="partial native attachment failure") as error:
        sensors.create(stage, "/World/Camera", "camera")
    assert error.value.code == "SENSOR_CREATE_FAILED"
    assert "Stage changed" in str(error.value)
    assert calls == [("updates", False)]
    assert product.hydra_texture is None
    assert other.GetPrimAtPath(render_path).GetCustomDataByKey("user") == "preserve"

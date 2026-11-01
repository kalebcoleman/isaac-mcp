"""Real USD export-policy tests; native texture bindings are separately live-tested."""

from pathlib import Path
import sys
import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import Gf, Sdf, Usd, UsdGeom, UsdRender
from sim_tools.core import Scene
from isaac_mcp.project_assets import AssetError


class Timeline:
    def is_playing(self):
        return False


@pytest.fixture
def configured(tmp_path):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Camera.Define(stage, "/World/Camera")
    producer = stage.DefinePrim("/Graph/Camera/Product", "OmniGraphNode")
    producer.CreateAttribute("node:type", Sdf.ValueTypeNames.Token).Set(
        "isaacsim.core.nodes.IsaacCreateRenderProduct")
    producer.CreateRelationship("inputs:cameraPrim").SetTargets(["/World/Camera"])
    for name, value in [("width", 1280), ("height", 720)]:
        producer.CreateAttribute("inputs:" + name, Sdf.ValueTypeNames.Int).Set(value)
    for kind in ("ROS2CameraHelper", "ROS2CameraInfoHelper"):
        helper = stage.DefinePrim("/Graph/Camera/" + kind, "OmniGraphNode")
        helper.CreateAttribute("node:type", Sdf.ValueTypeNames.Token).Set("isaacsim.ros2.bridge." + kind)
        helper.CreateAttribute("inputs:renderProductPath", Sdf.ValueTypeNames.String).SetConnections(
            [producer.GetPath().AppendProperty("outputs:renderProductPath")])
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        product = UsdRender.Product.Define(stage, "/Render/CameraProduct")
        product.GetResolutionAttr().Set(Gf.Vec2i(1280, 720))
        product.GetCameraRel().SetTargets(["/World/Camera"])
        product.GetOrderedVarsRel().SetTargets(["/Render/Vars/LdrColor"])
        product.GetPrim().CreateAttribute("viewportHandle", Sdf.ValueTypeNames.Int).Set(16)
        product.GetPrim().CreateAttribute("viewPickingId", Sdf.ValueTypeNames.UInt64).Set(1234)
        settings = UsdRender.Settings.Define(stage, "/Render/OmniverseGlobalRenderSettings")
        settings.GetProductsRel().SetTargets([product.GetPath()])
    checked = []
    scene = Scene(lambda: stage, Timeline(), output=tmp_path / "out", render_binding=checked.append)
    return scene, stage, product.GetPrim(), producer, checked


def unchanged(stage):
    return [layer.ExportToString() for layer in stage.GetLayerStack()]


def test_default_refuses_broken_save_and_supplies_explicit_policy(configured):
    scene, stage, product, _, checked = configured
    before = unchanged(stage)
    with pytest.raises(AssetError) as exc:
        scene.save(stage, "default.usda")
    assert exc.value.code == "SAVE_UNSUPPORTED"
    assert exc.value.details["retry_with"] == {"reset_camera_render_handles": True}
    assert exc.value.details["properties"] == [str(product.GetPath()) + ".viewportHandle"]
    assert unchanged(stage) == before and not checked
    assert not (scene.output / "default.usda").exists()


@pytest.mark.parametrize("handle", [0, 16])
@pytest.mark.parametrize("explicit", [False, True])
def test_explicit_policy_preserves_everything_except_exact_handle(configured, handle, explicit):
    scene, stage, product, producer, checked = configured
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        product.GetAttribute("viewportHandle").Set(handle)
    if explicit:
        producer.CreateRelationship("inputs:renderProductPrim").SetTargets([product.GetPath()])
    before = unchanged(stage)
    expected = stage.Flatten()
    spec = expected.GetAttributeAtPath(product.GetPath().AppendProperty("viewportHandle"))
    spec.owner.RemoveProperty(spec)
    result = scene.save(stage, "copy.usda", reset_camera_render_handles=True)
    actual = Sdf.Layer.FindOrOpen(result["file"])
    assert actual.ExportToString() == expected.ExportToString()
    assert unchanged(stage) == before
    assert len(checked) == 1 and checked[0]["handle"] == handle
    assert result["omitted_runtime_render_properties"] == [str(product.GetPath()) + ".viewportHandle"]
    reopened = Usd.Stage.Open(actual)
    assert not reopened.GetPrimAtPath(product.GetPath()).HasAttribute("viewportHandle")


@pytest.mark.parametrize("damage", ["root", "cross_layer", "connection", "animated", "wrong_type", "multiple", "foreign_camera", "missing_binding", "masked", "payload"])
def test_unsupported_handle_never_changes_live_layers_or_publishes(configured, damage):
    scene, stage, product, producer, checked = configured
    attr = product.GetAttribute("viewportHandle")
    if damage in ("root", "cross_layer"):
        attr.Set(16)  # authored in root, including when a stronger session value exists
        if damage == "root":
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                product.RemoveProperty("viewportHandle")
    elif damage == "connection":
        attr.SetConnections(["/World/Value.outputs:value"])
    elif damage == "animated":
        attr.Set(16, Usd.TimeCode(1))
    elif damage == "wrong_type":
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            product.RemoveProperty("viewportHandle")
            product.CreateAttribute("viewportHandle", Sdf.ValueTypeNames.String).Set("16")
    elif damage == "multiple":
        other = UsdRender.Product.Define(stage, "/Render/Other")
        other.GetResolutionAttr().Set(Gf.Vec2i(1280, 720))
        other.GetCameraRel().SetTargets(["/World/Camera"])
    elif damage == "foreign_camera":
        producer.CreateRelationship("inputs:renderProductPrim").SetTargets(["/Render/Unknown"])
    elif damage == "missing_binding":
        scene.render_binding = None
    elif damage == "masked":
        stage.SetPopulationMask(Usd.StagePopulationMask(["/World", "/Graph", "/Render"]))
    else:
        payload = stage.DefinePrim("/World/Unloaded")
        payload.GetPayloads().AddPayload("not-present.usda")
        stage.Unload("/World/Unloaded")
    before = unchanged(stage)
    with pytest.raises(AssetError) as exc:
        scene.save(stage, "refused.usda", reset_camera_render_handles=True)
    assert exc.value.code == "SAVE_UNSUPPORTED"
    assert unchanged(stage) == before
    assert not (scene.output / "refused.usda").exists()


@pytest.mark.parametrize("inactive", [False, True])
@pytest.mark.parametrize("relationship", [False, True])
def test_incoming_dependency_including_inactive_child_is_preserved(configured, inactive, relationship):
    scene, stage, product, _, checked = configured
    parent = stage.DefinePrim("/World/Dependent")
    child = stage.DefinePrim("/World/Dependent/Child")
    target = product.GetPath().AppendProperty("viewportHandle")
    if relationship:
        child.CreateRelationship("handle").SetTargets([target])
    else:
        child.CreateAttribute("inputs:handle", Sdf.ValueTypeNames.Int).SetConnections([target])
    if inactive:
        parent.SetActive(False)
    before = unchanged(stage)
    with pytest.raises(AssetError, match="depends on"):
        scene.save(stage, "dependent.usda", reset_camera_render_handles=True)
    assert unchanged(stage) == before
    assert not (scene.output / "dependent.usda").exists()


def test_native_binding_failure_is_not_an_authorship_guess(configured):
    scene, stage, _, _, _ = configured
    def reject(entry):
        raise AssetError("SAVE_UNSUPPORTED", "native context/binding changed", path=entry["path"])
    scene.render_binding = reject
    with pytest.raises(AssetError, match="binding changed"):
        scene.save(stage, "wrong-context.usda", reset_camera_render_handles=True)
    assert not (scene.output / "wrong-context.usda").exists()


def test_dependency_in_an_instance_prototype_is_not_skipped(configured):
    scene, stage, product, _, _ = configured
    source = stage.DefinePrim("/World/Source")
    node = stage.DefinePrim("/World/Source/Node")
    node.CreateAttribute("inputs:handle", Sdf.ValueTypeNames.Int).SetConnections(
        [product.GetPath().AppendProperty("viewportHandle")])
    instance = stage.DefinePrim("/World/Instance")
    instance.GetReferences().AddInternalReference(source.GetPath())
    instance.SetInstanceable(True)
    assert stage.GetPrimAtPath("/World/Instance/Node").IsInstanceProxy()
    before = unchanged(stage)
    with pytest.raises(AssetError, match="depends on"):
        scene.save(stage, "instance.usda", reset_camera_render_handles=True)
    assert unchanged(stage) == before


def test_inactive_unopened_reference_is_explicitly_incomplete(configured):
    scene, stage, _, _, _ = configured
    parent = stage.DefinePrim("/World/Hidden")
    child = stage.DefinePrim("/World/Hidden/Unseen")
    child.GetReferences().AddReference("unopened-user-content.usda")
    parent.SetActive(False)
    with pytest.raises(AssetError, match="complete"):
        scene.save(stage, "unseen.usda", reset_camera_render_handles=True)


def test_export_dependency_work_is_bounded(configured, monkeypatch):
    from sim_tools import render_export
    scene, stage, _, _, _ = configured
    monkeypatch.setattr(render_export, "MAX_SPECS", 2)
    with pytest.raises(AssetError, match="specs"):
        scene.save(stage, "bounded.usda", reset_camera_render_handles=True)
    assert not (scene.output / "bounded.usda").exists()


def test_binding_api_checks_context_and_supports_zero_handle(monkeypatch):
    import types
    from sim_tools.render_export import validate_native_binding
    data = {"context": "", "product": "/Render/Product", "camera": "/World/Camera"}
    texture = types.SimpleNamespace(
        get_usd_context_name=lambda: data["context"],
        get_render_product_path=lambda: data["product"],
        get_camera_path=lambda: data["camera"], get_width=lambda: 1280, get_height=lambda: 720)
    factory = types.SimpleNamespace(get_hydra_texture_from_handle=lambda handle: texture if handle == 0 else None)
    hydra = types.ModuleType("omni.hydratexture")
    hydra.acquire_hydra_texture_factory_interface = lambda: factory
    usd = types.ModuleType("omni.usd")
    usd.get_context = lambda: types.SimpleNamespace(get_name=lambda: "")
    omni = types.ModuleType("omni")
    omni.hydratexture, omni.usd = hydra, usd
    for name, module in [("omni", omni), ("omni.hydratexture", hydra), ("omni.usd", usd)]:
        monkeypatch.setitem(sys.modules, name, module)
    entry = dict(path="/Render/Product.viewportHandle", product=data["product"], camera=data["camera"],
                 width=1280, height=720, handle=0)
    validate_native_binding(entry)
    data["context"] = "different-stage-context"
    with pytest.raises(AssetError, match="binding differs"):
        validate_native_binding(entry)
    entry["handle"] = 123
    with pytest.raises(AssetError, match="no live texture"):
        validate_native_binding(entry)
    del factory.get_hydra_texture_from_handle
    with pytest.raises(AssetError, match="Cannot verify"):
        validate_native_binding(entry)

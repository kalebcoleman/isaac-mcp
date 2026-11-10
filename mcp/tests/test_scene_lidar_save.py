"""Saving and reopening RTX LiDAR graphs, pack-owned capture products, and save result fields.

A LiDAR's /scan is lost after a reopen when Flatten copied the session-only viewportHandle of the helper's
render product into the file and Replicator's attach_hydra_texture skips any product that has one.
These are real USD policy tests; the reopen itself is verified live.
"""

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdRender
from isaac_mcp.project_assets import AssetError
from sim_tools import render_export
from sim_tools.core import Scene

HANDLE = "viewportHandle"
OWNED = "/Render/OmniverseKit/HydraTextures/IsaacMCP_" + "a" * 32
PRODUCT = "/Render/OmniverseKit/HydraTextures/Replicator"


class Timeline:
    def is_playing(self):
        return False


def add_graph(stage, sensor, helper_type, graph="/Graph", size=(1280, 720)):
    producer = stage.DefinePrim(f"{graph}/rp", "OmniGraphNode")
    producer.CreateAttribute("node:type", Sdf.ValueTypeNames.Token).Set("isaacsim.core.nodes.IsaacCreateRenderProduct")
    producer.CreateRelationship("inputs:cameraPrim").SetTargets([sensor])
    for name, value in zip(("width", "height"), size or ()):
        producer.CreateAttribute("inputs:" + name, Sdf.ValueTypeNames.Int).Set(value)
    if size is None:  # as sim__graph__build leaves defaults: the attributes exist with no value
        producer.CreateAttribute("inputs:width", Sdf.ValueTypeNames.UInt)
        producer.CreateAttribute("inputs:height", Sdf.ValueTypeNames.UInt)
    helper = stage.DefinePrim(f"{graph}/helper", "OmniGraphNode")
    helper.CreateAttribute("node:type", Sdf.ValueTypeNames.Token).Set(helper_type)
    helper.CreateAttribute("inputs:renderProductPath", Sdf.ValueTypeNames.Token).SetConnections(
        [producer.GetPath().AppendProperty("outputs:renderProductPath")])
    stage.DefinePrim(graph, "OmniGraph")
    return producer, helper


def add_product(stage, path, sensor, size=(1280, 720), handle=2, session=True):
    layer = stage.GetSessionLayer() if session else stage.GetRootLayer()
    with Usd.EditContext(stage, layer):
        product = UsdRender.Product.Define(stage, path)
        product.GetResolutionAttr().Set(Gf.Vec2i(*size))
        product.GetCameraRel().SetTargets([sensor])
        if handle is not None:
            product.GetPrim().CreateAttribute(HANDLE, Sdf.ValueTypeNames.Int).Set(handle)
    return product.GetPrim()


@pytest.fixture
def lidar(tmp_path):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World")
    stage.DefinePrim("/World/Lidar2D", "OmniLidar")
    producer, helper = add_graph(stage, "/World/Lidar2D", "isaacsim.ros2.bridge.ROS2RtxLidarHelper")
    checked = []
    scene = Scene(lambda: stage, Timeline(), output=tmp_path / "out", render_binding=checked.append)
    return scene, stage, producer, helper, checked


@pytest.fixture
def camera(tmp_path):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Camera.Define(stage, "/World/Camera")
    add_graph(stage, "/World/Camera", "isaacsim.ros2.bridge.ROS2CameraHelper")
    checked = []
    scene = Scene(lambda: stage, Timeline(), output=tmp_path / "out", render_binding=checked.append)
    return scene, stage, checked


def has_handle(file, path):
    stage = Usd.Stage.Open(str(file))  # keep the stage alive: an expired prim would read as "no handle"
    prim = stage.GetPrimAtPath(path)
    assert prim, f"{path} is not in {file}"
    return prim.HasAttribute(HANDLE)


# ---- LiDAR helper products: the /scan defect


def test_lidar_save_refuses_like_cameras_and_flag_drops_the_handle(lidar):
    scene, stage, _, _, checked = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D")
    before = stage.GetSessionLayer().ExportToString()
    with pytest.raises(AssetError) as error:
        scene.save(stage, "refused.usda")
    assert error.value.code == "SAVE_UNSUPPORTED"
    assert error.value.details["retry_with"] == {"reset_camera_render_handles": True}
    assert error.value.details["properties"] == [PRODUCT + "." + HANDLE]
    assert not (scene.output / "refused.usda").exists()
    result = scene.save(stage, "scan.usda", reset_camera_render_handles=True)
    assert result["omitted_runtime_render_properties"] == [PRODUCT + "." + HANDLE]
    assert not has_handle(result["file"], PRODUCT)
    assert Usd.Stage.Open(result["file"]).GetPrimAtPath(PRODUCT), "only the property goes"
    assert "warnings" not in result
    assert len(checked) == 1 and stage.GetSessionLayer().ExportToString() == before


def test_unauthored_lidar_resolution_uses_the_live_node(lidar, monkeypatch):
    scene, stage, producer, _, _ = lidar
    for name in ("inputs:width", "inputs:height"):
        producer.RemoveProperty(name)
    producer.CreateAttribute("inputs:width", Sdf.ValueTypeNames.UInt)
    producer.CreateAttribute("inputs:height", Sdf.ValueTypeNames.UInt)
    add_product(stage, PRODUCT, "/World/Lidar2D")
    # The sensor pack's own 128x128 product on the same LiDAR must not make this ambiguous.
    add_product(stage, OWNED, "/World/Lidar2D", size=(128, 128), handle=1)
    monkeypatch.setattr(render_export, "runtime_node_resolution", lambda s, p: (1280, 720))
    result = scene.save(stage, "defaults.usda", reset_camera_render_handles=True)
    assert result["omitted_runtime_render_properties"] == [PRODUCT + "." + HANDLE]
    assert not has_handle(result["file"], PRODUCT)
    assert not Usd.Stage.Open(result["file"]).GetPrimAtPath(OWNED)


def test_unresolvable_lidar_resolution_refuses(lidar, monkeypatch):
    scene, stage, producer, _, _ = lidar
    for name in ("inputs:width", "inputs:height"):
        producer.RemoveProperty(name)
    add_product(stage, PRODUCT, "/World/Lidar2D")
    monkeypatch.setattr(render_export, "runtime_node_resolution", lambda s, p: None)
    with pytest.raises(AssetError, match="explicit integer width and height"):
        scene.save(stage, "unknown.usda", reset_camera_render_handles=True)


def test_camera_producer_still_needs_explicit_resolution(camera, monkeypatch):
    scene, stage, _ = camera
    producer = stage.GetPrimAtPath("/Graph/rp")
    for name in ("inputs:width", "inputs:height"):
        producer.RemoveProperty(name)
    add_product(stage, PRODUCT, "/World/Camera")
    monkeypatch.setattr(render_export, "runtime_node_resolution", lambda s, p: None)  # off-stage: nothing proves it
    with pytest.raises(AssetError, match="explicit integer width and height.*inputs:width and inputs:height"):
        scene.save(stage, "camera.usda", reset_camera_render_handles=True)


def test_saved_lidar_handle_saves_with_a_warning_naming_the_fix(lidar):
    """A stale file without the flag: refusing would lose unsaved work, so warn and leave it authored."""
    scene, stage, _, _, checked = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D", session=False)
    result = scene.save(stage, "stale.usda")
    assert has_handle(result["file"], PRODUCT), "an authored handle is not normalized without the flag"
    assert result["omitted_runtime_render_properties"] == [] and not checked
    assert result["stale_render_handles"] == [PRODUCT]
    assert "will not publish" in result["warnings"][0] and "reset_camera_render_handles" in result["warnings"][0]


def test_flag_drops_a_saved_lidar_handle_so_the_copy_is_clean(lidar):
    """The rescue for a stale file. No texture exists for that handle, so there is no binding to validate."""
    scene, stage, _, _, checked = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D", session=False)
    root = stage.GetRootLayer().ExportToString()
    result = scene.save(stage, "rescued.usda", reset_camera_render_handles=True)
    assert not has_handle(result["file"], PRODUCT)
    assert result["omitted_runtime_render_properties"] == [PRODUCT + "." + HANDLE]
    assert "warnings" not in result and not checked
    assert stage.GetRootLayer().ExportToString() == root, "only the copy changes"


def test_a_file_with_a_saved_pipeline_is_reported_with_the_rescue_not_deactivated(lidar):
    _, stage, _, _, _ = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D", session=False)
    stage.DefinePrim(PRODUCT + "/PostRender/SDGPipeline", "OmniGraph")
    repair = render_export.repair_stale_lidar_products(stage, set())
    assert repair["deactivated"] == [] and "saved runtime pipeline" in repair["skipped"][0]["reason"]
    assert "sim__stage__save" in repair["rescue"] and "reset_camera_render_handles=true" in repair["rescue"]
    assert stage.GetPrimAtPath(PRODUCT).IsActive()


def test_a_pipeline_kit_made_this_session_is_not_a_saved_one(lidar):
    _, stage, _, _, _ = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D", session=False)
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        stage.DefinePrim("/Render/PostProcess/SDGPipeline", "OmniGraph")
    assert render_export.saved_runtime_pipeline(stage) == []
    assert render_export.repair_stale_lidar_products(stage, set())["deactivated"] == [PRODUCT]


def test_an_unproven_build_leaves_the_stage_alone_and_gives_the_rescue(lidar):
    _, stage, _, _, _ = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D", session=False)
    repair = render_export.repair_stale_lidar_products(stage, set(), allowed=False)
    assert repair["deactivated"] == [] and "not proven" in repair["skipped"][0]["reason"] and repair["rescue"]
    assert stage.GetPrimAtPath(PRODUCT).IsActive()


@pytest.mark.parametrize("version, proven", [("6.2.0", True), ("6.1.0", False), ("6.0.1", False), ("6.0.0", False), ("5.1.0", True)])
def test_the_repair_is_applied_only_on_measured_builds(monkeypatch, version, proven):
    from sim_tools.compat import stale_lidar_repair_proven

    module = SimpleNamespace(get_version=lambda: (version, "", ""))
    monkeypatch.setitem(sys.modules, "isaacsim", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "isaacsim.core", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "isaacsim.core.version", module)
    assert stale_lidar_repair_proven() is proven


def test_saved_camera_handle_still_refuses(camera):
    scene, stage, _ = camera
    add_product(stage, PRODUCT, "/World/Camera", session=False)
    with pytest.raises(AssetError) as error:
        scene.save(stage, "camera.usda")
    assert error.value.details["retry_with"] == {"reset_camera_render_handles": True}
    with pytest.raises(AssetError, match="authored"):
        scene.save(stage, "camera.usda", reset_camera_render_handles=True)


def test_session_lidar_handle_beside_a_saved_one_still_needs_the_flag(lidar):
    scene, stage, _, _, _ = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D")
    add_product(stage, PRODUCT + "_stale", "/World/Lidar2D", size=(640, 480), session=False)
    with pytest.raises(AssetError) as error:
        scene.save(stage, "mixed.usda")
    assert error.value.details["properties"] == [PRODUCT + "." + HANDLE]


def test_repaired_stage_saves_a_working_copy(lidar):
    """The whole chain for a stale file: repair at open, Play makes a new product, save with the flag."""
    scene, stage, _, _, _ = lidar
    stale = add_product(stage, PRODUCT, "/World/Lidar2D", session=False)
    repair = render_export.repair_stale_lidar_products(stage, set())
    assert repair == dict(deactivated=[PRODUCT], skipped=[])
    fresh = PRODUCT + "_01"
    add_product(stage, fresh, "/World/Lidar2D", handle=1)
    assert not stale.IsActive()
    result = scene.save(stage, "repaired.usda", reset_camera_render_handles=True)
    assert result["omitted_runtime_render_properties"] == [fresh + "." + HANDLE]
    assert not has_handle(result["file"], fresh)
    assert not Usd.Stage.Open(result["file"]).GetPrimAtPath(PRODUCT), "Flatten drops the inactive stale product"
    assert "warnings" not in result


# ---- Repair at open


def test_repair_uses_only_the_session_layer(lidar):
    _, stage, _, _, _ = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D", session=False)
    root = stage.GetRootLayer().ExportToString()
    repair = render_export.repair_stale_lidar_products(stage, set())
    assert repair["deactivated"] == [PRODUCT]
    assert stage.GetRootLayer().ExportToString() == root
    assert not stage.GetPrimAtPath(PRODUCT).IsActive()


def test_repair_ignores_cameras_and_live_handles(lidar):
    _, stage, _, _, _ = lidar
    UsdGeom.Camera.Define(stage, "/World/Camera")
    add_product(stage, "/Render/CameraProduct", "/World/Camera", session=False)
    add_product(stage, "/Render/Live", "/World/Lidar2D", size=(640, 480))
    add_product(stage, "/Render/NoHandle", "/World/Lidar2D", size=(320, 240), handle=None, session=False)
    assert render_export.repair_stale_lidar_products(stage, set()) == dict(deactivated=[], skipped=[])
    assert all(stage.GetPrimAtPath(p).IsActive() for p in ("/Render/CameraProduct", "/Render/Live", "/Render/NoHandle"))


def test_repair_skips_retained_pinned_and_unreadable_state(lidar):
    _, stage, producer, _, _ = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D", session=False)
    assert render_export.repair_stale_lidar_products(stage, {PRODUCT})["skipped"][0]["reason"] == "has a live texture"
    assert render_export.repair_stale_lidar_products(stage, None)["skipped"][0]["reason"] == "texture state unreadable"
    producer.CreateRelationship("inputs:renderProductPrim").SetTargets([PRODUCT])
    assert "pins" in render_export.repair_stale_lidar_products(stage, set())["skipped"][0]["reason"]
    assert stage.GetPrimAtPath(PRODUCT).IsActive()


def test_repair_skips_a_product_whose_handle_is_connected(lidar):
    _, stage, _, _, _ = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D", session=False)
    user = stage.DefinePrim("/World/User")
    user.CreateAttribute("inputs:handle", Sdf.ValueTypeNames.Int).SetConnections([Sdf.Path(PRODUCT + "." + HANDLE)])
    repair = render_export.repair_stale_lidar_products(stage, set())
    assert not repair["deactivated"] and repair["skipped"]
    assert stage.GetPrimAtPath(PRODUCT).IsActive()


def test_open_reports_a_repair_only_when_there_is_one(lidar, monkeypatch):
    from sim_tools import compat
    from sim_tools.stage_open import _repair_saved_lidar_products as fields

    _, stage, _, _, _ = lidar
    monkeypatch.setattr(compat, "stale_lidar_repair_proven", lambda: True)
    monkeypatch.setattr(render_export, "retained_product_paths", lambda: set())
    assert fields(stage) == {}
    add_product(stage, PRODUCT, "/World/Lidar2D", session=False)
    stale = fields(stage)["stale_render_products"]
    assert stale["deactivated"] == [PRODUCT] and "file is unchanged" in stale["note"]


def test_open_never_fails_because_of_the_repair(lidar, monkeypatch):
    from sim_tools.stage_open import _repair_saved_lidar_products as fields

    _, stage, _, _, _ = lidar

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(render_export, "repair_stale_lidar_products", broken)
    assert fields(stage)["stale_render_products"]["skipped"][0]["reason"] == "boom"


# ---- Paused saves: the regenerated LiDAR writer


def writer(stage, topic="/scan", node="isaacsim.ros2.bridge.ROS2PublishLaserScan"):
    stage.DefinePrim("/Render/PostProcess/SDGPipeline", "OmniGraph")
    prim = stage.DefinePrim("/Render/PostProcess/SDGPipeline/Replicator_NodeWriterWriter", "OmniGraphNode")
    prim.CreateAttribute("node:type", Sdf.ValueTypeNames.Token).Set(node)
    prim.CreateAttribute("inputs:topicName", Sdf.ValueTypeNames.String).Set(topic)
    prim.CreateAttribute("inputs:frameId", Sdf.ValueTypeNames.String).Set("base_scan")
    prim.CreateAttribute("inputs:writerId", Sdf.ValueTypeNames.String).Set(
        "NodeWriter_5a8bb773-d7e8-11f1-a94a-9c6b00da09fc")


def helper_inputs(helper):
    helper.CreateAttribute("inputs:topicName", Sdf.ValueTypeNames.String).Set("/scan")
    helper.CreateAttribute("inputs:frameId", Sdf.ValueTypeNames.String).Set("base_scan")


@pytest.mark.parametrize("node", ["isaacsim.ros2.bridge.ROS2PublishLaserScan",
                                  "isaacsim.ros2.bridge.ROS2PublishPointCloud"])
def test_paused_lidar_writer_with_its_helper_is_regenerable(lidar, node):
    scene, stage, _, helper, _ = lidar
    helper_inputs(helper)
    writer(stage, node=node)
    extract = stage.DefinePrim("/Render/PostProcess/SDGPipeline/Replicator_IsaacExtractRTXSensorPointCloud", "OmniGraphNode")
    extract.CreateAttribute("node:type", Sdf.ValueTypeNames.Token).Set(
        "isaacsim.sensors.rtx.nodes.OgnIsaacExtractRTXSensorPointCloud")
    result = scene.save(stage, "paused.usda")
    assert "/Render/PostProcess/SDGPipeline" in result["omitted_runtime_render_paths"]
    assert not Usd.Stage.Open(result["file"]).GetPrimAtPath("/Render/PostProcess/SDGPipeline")


def test_lidar_writer_without_a_matching_helper_still_refuses(lidar):
    scene, stage, _, helper, _ = lidar
    helper_inputs(helper)
    writer(stage, topic="/other")
    with pytest.raises(AssetError) as error:
        scene.save(stage, "paused.usda")
    assert error.value.code == "SAVE_UNSUPPORTED"
    assert error.value.details["node_type"] == "isaacsim.ros2.bridge.ROS2PublishLaserScan"


# ---- Pack-owned capture products


def test_owned_camera_product_needs_no_flag_and_is_reported(camera):
    scene, stage, checked = camera
    add_product(stage, OWNED, "/World/Camera", handle=7)
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        settings = UsdRender.Settings.Define(stage, "/Render/OmniverseGlobalRenderSettings")
        settings.GetProductsRel().SetTargets([OWNED])
    before = stage.GetSessionLayer().ExportToString()
    result = scene.save(stage, "owned.usda")
    assert result["omitted_runtime_render_properties"] == [OWNED + "." + HANDLE]
    assert OWNED in result["omitted_runtime_render_paths"]
    assert not Usd.Stage.Open(result["file"]).GetPrimAtPath(OWNED)
    assert len(checked) == 1, "the native binding is still validated"
    assert stage.GetSessionLayer().ExportToString() == before


def test_owned_product_saved_in_a_file_still_refuses(camera):
    scene, stage, _ = camera
    add_product(stage, OWNED, "/World/Camera", session=False)
    with pytest.raises(AssetError, match="authored"):
        scene.save(stage, "authored.usda")


def test_owned_product_with_a_dependent_still_refuses(camera):
    scene, stage, _ = camera
    add_product(stage, OWNED, "/World/Camera")
    user = stage.DefinePrim("/World/User")
    user.CreateAttribute("inputs:handle", Sdf.ValueTypeNames.Int).SetConnections([Sdf.Path(OWNED + "." + HANDLE)])
    with pytest.raises(AssetError, match="depends on a camera runtime handle"):
        scene.save(stage, "dependent.usda")


def test_owned_product_sharing_a_camera_leaves_the_graph_product_needing_the_flag(camera):
    scene, stage, _ = camera
    add_product(stage, OWNED, "/World/Camera")
    add_product(stage, PRODUCT, "/World/Camera", handle=3)
    with pytest.raises(AssetError, match="cannot survive restart") as err:
        scene.save(stage, "needs-flag.usda")
    assert err.value.details["retry_with"] == {"reset_camera_render_handles": True}


def test_owned_product_failed_binding_still_refuses(tmp_path):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Camera.Define(stage, "/World/Camera")
    add_graph(stage, "/World/Camera", "isaacsim.ros2.bridge.ROS2CameraHelper")
    add_product(stage, OWNED, "/World/Camera")

    def fail(entry):
        render_export.unsupported("Cannot verify current native camera binding: test", entry["path"])

    scene = Scene(lambda: stage, Timeline(), output=tmp_path / "out", render_binding=fail)
    with pytest.raises(AssetError, match="Cannot verify"):
        scene.save(stage, "binding.usda")


def test_native_camera_product_still_needs_the_flag(camera):
    scene, stage, _ = camera
    add_product(stage, PRODUCT, "/World/Camera")
    with pytest.raises(AssetError) as error:
        scene.save(stage, "native.usda")
    assert error.value.details["retry_with"] == {"reset_camera_render_handles": True}


# ---- Filename and graph inventory


def test_bare_basename_gets_the_extension(camera):
    scene, stage, _ = camera
    result = scene.save(stage, "Lab3_01")
    assert Path(result["file"]).name == "Lab3_01.usda"
    assert result["graphs"] == ["/Graph"] and result["graph_count"] == 1 and result["graphs_truncated"] is False


@pytest.mark.parametrize("name", ["a.usd", "a.b.usda", "../a", "a/b", "", ".usda", "x" * 61, 7, None])
def test_other_filenames_still_refuse(camera, name):
    scene, stage, _ = camera
    with pytest.raises(AssetError) as error:
        scene.save(stage, name)
    assert error.value.code == "INVALID_OUTPUT"


def test_sixty_character_basename_is_the_limit(camera):
    scene, stage, _ = camera
    assert Path(scene.save(stage, "x" * 60)["file"]).name == "x" * 60 + ".usda"


def test_graph_list_is_bounded_and_skips_runtime_pipelines(camera):
    scene, stage, _ = camera
    for index in range(render_export.MAX_GRAPHS + 3):
        stage.DefinePrim(f"/G{index:03d}", "OmniGraph")
    stage.DefinePrim("/Render/PostProcess/SDGPipeline", "OmniGraph")
    result = scene.save(stage, "many.usda")
    assert len(result["graphs"]) == render_export.MAX_GRAPHS
    assert result["graph_count"] == render_export.MAX_GRAPHS + 4 and result["graphs_truncated"] is True
    assert not any(path.startswith("/Render") for path in result["graphs"])


# ---- Warnings for handles the export did not select


def test_unselected_handle_is_warned_about(camera):
    scene, stage, _ = camera
    add_product(stage, "/Render/Orphan", "/World/Camera", size=(320, 240))
    add_product(stage, "/Render/OmniverseKit/HydraTextures/omni_kit_widget_viewport_ViewportTexture_0",
                "/World/Camera", size=(800, 600))
    result = scene.save(stage, "warned.usda")
    assert result["stale_render_handles"] == ["/Render/Orphan"]
    assert "will not publish" in result["warnings"][0]


def test_clean_save_has_no_warning(camera):
    scene, stage, _ = camera
    result = scene.save(stage, "clean.usda")
    assert "warnings" not in result and "stale_render_handles" not in result


# ---- Inspect status


@pytest.mark.parametrize("session,handle,retained,playing,status,problem", [
    (False, 2, set(), False, "stale_handle", True),
    (False, 2, set(), True, "stale_handle", True),
    (True, 1, {PRODUCT}, True, "texture_bound", False),
    (True, 1, set(), True, "viewport_bound", False),
    (True, None, set(), False, "texture_on_play", False),
    (True, None, set(), True, "no_texture", True),
    (True, None, None, True, "unknown", False),
])
def test_helper_status_uses_the_fields_replicator_reads(lidar, session, handle, retained, playing, status, problem):
    _, stage, _, helper, _ = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D", handle=handle, session=session)
    entry = render_export.helper_render_status(stage, helper, "", retained, playing)
    assert entry["render_product"] == PRODUCT and entry["status"] == status
    assert ("problem" in entry) is problem
    assert render_export.helper_render_status(stage, helper, PRODUCT, retained, playing)["status"] == status


def test_helper_status_before_any_product_exists(lidar):
    _, stage, _, helper, _ = lidar
    assert render_export.helper_render_status(stage, helper, "", set(), False)["status"] == "no_product_yet"
    assert render_export.helper_render_status(stage, helper, "", set(), True)["status"] == "no_product"


def test_helper_status_ignores_a_deactivated_product(lidar):
    _, stage, _, helper, _ = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D", session=False)
    render_export.repair_stale_lidar_products(stage, set())
    assert render_export.helper_render_status(stage, helper, "", set(), False)["status"] == "no_product_yet"


@pytest.mark.parametrize("entry, playing, flagged", [
    (dict(status="texture_bound", writer_computes=0), True, True),
    (dict(status="texture_bound", writer_computes=0), False, False),  # not expected to run yet
    (dict(status="texture_bound", writer_computes=42), True, False),
    (dict(status="texture_bound", writer_computes=None), True, False),  # no writer node to judge
    (dict(status="no_texture", writer_computes=0), True, False),  # already has its own problem
])
def test_texture_bound_with_an_idle_writer_is_a_problem_only_while_playing(entry, playing, flagged):
    problem = render_export.writer_problem(dict(entry, render_product=PRODUCT), playing)
    assert bool(problem) is flagged
    if flagged:
        assert PRODUCT in problem and "sim__logs__tail" in problem


# ---- Scenes that could not be saved at all


def test_our_capture_product_does_not_make_the_graph_product_ambiguous(camera):
    scene, stage, _ = camera
    add_product(stage, PRODUCT, "/World/Camera")
    add_product(stage, OWNED, "/World/Camera")
    out = scene.save(stage, "captured.usda", reset_camera_render_handles=True)
    assert out["omitted_runtime_render_properties"] == [PRODUCT + ".viewportHandle"]
    assert not has_handle(out["file"], PRODUCT) and not Usd.Stage.Open(out["file"]).GetPrimAtPath(OWNED)


def test_two_non_owned_products_are_still_ambiguous(camera):
    scene, stage, _ = camera
    add_product(stage, PRODUCT, "/World/Camera")
    add_product(stage, PRODUCT + "_other", "/World/Camera")
    with pytest.raises(AssetError, match="Multiple render products"):
        scene.save(stage, "ambiguous.usda", reset_camera_render_handles=True)


def test_two_producers_sharing_one_product_are_not_ambiguous(lidar):
    scene, stage, producer, _, _ = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D")
    add_graph(stage, "/World/Lidar2D", "isaacsim.ros2.bridge.ROS2RtxLidarHelper", graph="/Graph2")
    out = scene.save(stage, "both.usda", reset_camera_render_handles=True)
    assert out["omitted_runtime_render_properties"] == [PRODUCT + ".viewportHandle"]
    assert not has_handle(out["file"], PRODUCT)


def test_producers_that_disagree_about_a_shared_product_still_refuse(lidar):
    # One sees it as a LiDAR product, the other as a camera product: not the same entry.
    scene, stage, *_ = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D")
    add_graph(stage, "/World/Lidar2D", "isaacsim.ros2.bridge.ROS2CameraHelper", graph="/Graph2")
    with pytest.raises(AssetError, match="share the camera product ambiguously"):
        scene.save(stage, "disagree.usda", reset_camera_render_handles=True)


def test_camera_with_unauthored_resolution_uses_the_live_node_when_a_product_proves_it(camera, monkeypatch):
    scene, stage, _ = camera
    producer = stage.GetPrimAtPath("/Graph/rp")
    for name in ("inputs:width", "inputs:height"):
        producer.RemoveProperty(name)
    add_product(stage, PRODUCT, "/World/Camera")
    resolution = tuple(stage.GetPrimAtPath(PRODUCT).GetAttribute("resolution").Get())
    monkeypatch.setattr(render_export, "runtime_node_resolution", lambda s, p: resolution)
    out = scene.save(stage, "camera.usda", reset_camera_render_handles=True)
    assert out["omitted_runtime_render_properties"] == [PRODUCT + ".viewportHandle"]


def test_ros_domain_note_only_for_a_fixed_domain_that_differs():
    from sim_tools.graph import _ros_domain_note

    def node(domain, use_env):
        return dict(type="isaacsim.ros2.bridge.ROS2Context", path="/G/ctx", inputs={
            "inputs:domain_id": dict(value=domain), "inputs:useDomainIDEnvVar": dict(value=use_env)})

    note = _ros_domain_note([node(0, False)], {"ROS_DOMAIN_ID": "81"})["ros_domain_note"]
    assert note["authored_domains"] == {"/G/ctx": 0} and note["ros_domain_id"] == "81"
    assert _ros_domain_note([node(81, False)], {"ROS_DOMAIN_ID": "81"}) == {}
    assert _ros_domain_note([node(0, True)], {"ROS_DOMAIN_ID": "81"}) == {}
    assert _ros_domain_note([dict(type="other", path="/x", inputs={})], {}) == {}


def test_save_explains_a_large_flattened_file(tmp_path, monkeypatch):
    from sim_tools import core

    scene = core.Scene(lambda: stage, SimpleNamespace(is_playing=lambda: False), output=tmp_path)
    stage = Usd.Stage.CreateInMemory()
    monkeypatch.setattr(core, "LARGE_SAVE_BYTES", 10)
    big = scene.save(stage, "big.usda", directory=None)
    assert "flattened" in big["size_note"] and "MB" in big["size_note"]
    monkeypatch.setattr(core, "LARGE_SAVE_BYTES", 10**9)
    assert "size_note" not in scene.save(stage, "small.usda", directory=None)


def test_save_size_note_names_the_size():
    from sim_tools.core import flattened_size_note

    assert "359 MB" in flattened_size_note(359 * 1024 * 1024)


# ---- All-free maps and saved pipelines


def _occupancy_world(monkeypatch, tmp_path, hits):
    """occupancy_map with Kit's generator returning an all-free buffer and PhysX reporting *hits*."""
    from sim_tools import graph

    scene = SimpleNamespace(output=tmp_path)
    plan = dict(lo=(-1.0, -1.0), hi=(1.0, 1.0), z=(0.2, 0.8), cell=0.5, start=(0.0, 0.0), base="m", clear=[],
                pgm_path=tmp_path / "m.pgm", yaml_path=tmp_path / "m.yaml")
    monkeypatch.setattr(graph, "validate_occupancy", lambda *a: plan)
    monkeypatch.setattr(graph, "_enable", lambda name: True)
    monkeypatch.setattr(graph, "_start_in_collider", lambda *a: False)
    interface = SimpleNamespace(
        set_cell_size=lambda *a: None, set_transform=lambda *a: None, update=lambda: None, generate=lambda: None,
        get_occupied_positions=lambda: [], get_max_bound=lambda: (1.0, 1.0), get_min_bound=lambda: (-1.0, -1.0),
        # Calling Kit's get_buffer can overrun the heap and crash the app, on every 6.x build. It must stay unused.
        get_buffer=lambda: pytest.fail("get_buffer must not be called"))
    _omap = SimpleNamespace(acquire_omap_interface=lambda: interface, release_omap_interface=lambda om: None)
    bindings = SimpleNamespace(_omap=_omap)
    for name in ("isaacsim", "isaacsim.asset", "isaacsim.asset.gen", "isaacsim.asset.gen.omap"):
        monkeypatch.setitem(sys.modules, name, SimpleNamespace())
    monkeypatch.setitem(sys.modules, "isaacsim.asset.gen.omap.bindings", bindings)
    query = SimpleNamespace(overlap_box=lambda half, center, rot, report, any_hit: [
        report(SimpleNamespace(collision=hit, rigid_body="")) for hit in hits])
    physx = SimpleNamespace(get_physx_scene_query_interface=lambda: query)
    monkeypatch.setitem(sys.modules, "omni", SimpleNamespace(physx=physx))
    monkeypatch.setitem(sys.modules, "omni.physx", physx)
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    return graph, scene, stage


def test_an_all_free_map_with_colliders_in_the_volume_is_refused_and_writes_nothing(monkeypatch, tmp_path):
    graph, scene, stage = _occupancy_world(monkeypatch, tmp_path, ["/World/Wall"])
    with pytest.raises(AssetError, match="/World/Wall") as err:
        graph.occupancy_map(scene, stage, {})
    assert err.value.code == "MAP_NO_OBSTACLES" and err.value.details["colliders"] == ["/World/Wall"]
    assert not (tmp_path / "m.pgm").exists() and not (tmp_path / "m.yaml").exists()


def test_an_all_free_map_with_nothing_in_the_volume_still_succeeds_with_its_warning(monkeypatch, tmp_path):
    graph, scene, stage = _occupancy_world(monkeypatch, tmp_path, [])
    result = graph.occupancy_map(scene, stage, {})
    assert (tmp_path / "m.pgm").exists() and "No occupied cells" in result["warning"]


def test_a_saved_pipeline_alone_is_reported_with_the_rescue(lidar):
    from sim_tools.stage_open import _repair_saved_lidar_products as fields

    _, stage, _, _, _ = lidar
    stage.DefinePrim("/Render/PostProcess/SDGPipeline", "OmniGraph")
    stale = fields(stage)["stale_render_products"]
    assert stale["saved_runtime_pipeline"] == ["/Render/PostProcess/SDGPipeline"] and stale["rescue"]


def test_the_flag_still_refuses_a_saved_lidar_handle_something_depends_on(lidar):
    scene, stage, _, _, _ = lidar
    add_product(stage, PRODUCT, "/World/Lidar2D", session=False)
    user = stage.DefinePrim("/World/User")
    user.CreateAttribute("inputs:handle", Sdf.ValueTypeNames.Int).SetConnections([Sdf.Path(PRODUCT + "." + HANDLE)])
    with pytest.raises(AssetError, match="depends on a camera runtime handle"):
        scene.save(stage, "dependent.usda", reset_camera_render_handles=True)


def test_the_flag_refuses_an_animated_saved_lidar_handle(lidar):
    scene, stage, _, _, _ = lidar
    product = add_product(stage, PRODUCT, "/World/Lidar2D", session=False)
    product.GetAttribute(HANDLE).Set(3, 1.0)
    with pytest.raises(AssetError, match="connected, animated"):
        scene.save(stage, "animated.usda", reset_camera_render_handles=True)


def test_rescue_text_says_to_save_before_play():
    source = (Path(__file__).resolve().parents[2] / "exts/sim-tools/sim_tools/render_export.py").read_text(encoding="utf-8")
    assert '"Before any Play, with the timeline stopped' in source

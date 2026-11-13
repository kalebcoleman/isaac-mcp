"""Real OpenUSD (no Kit/PhysX) tests. Optional extra scene-test installs usd-core."""

import json
from pathlib import Path
import sys
import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import Usd, UsdGeom, Gf
from sim_tools.core import Scene, bounded
from twinsmith.project_assets import AssetError, Broker


class Timeline:
    def is_stopped(self):
        return True

    def is_playing(self):
        return False

    def get_current_time(self):
        return 0


@pytest.fixture
def scene(tmp_path):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.Xform.Define(stage, "/World")
    return Scene(lambda: stage, Timeline(), output=tmp_path / "out")


def test_stage_tokens_and_source_preservation(scene):
    info = scene.stage_info()
    stage = scene.target(info["session_id"], info["stage_id"])
    before = stage.GetRootLayer().ExportToString()
    scene.create(stage, "/World/Box", "Cube", rigid=True)
    assert stage.GetRootLayer().ExportToString() == before
    assert scene.get(stage, "/World/Box")["physics"]["collision"]
    with pytest.raises(AssetError, match="already exists"):
        scene.create(stage, "/World/Box", "Cube")
    with pytest.raises(AssetError, match="changed"):
        scene.target("other", info["stage_id"])
    scene.get_stage = lambda: Usd.Stage.CreateInMemory()
    with pytest.raises(AssetError, match="changed"):
        scene.target(info["session_id"], info["stage_id"])


def test_stale_target_returns_current_identity_without_retargeting_or_authoring(scene):
    old = scene.stage_info()
    new = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(new, 0.01)
    UsdGeom.SetStageUpAxis(new, "Y")
    scene.get_stage = lambda: new
    before = new.GetRootLayer().ExportToString()
    with pytest.raises(AssetError) as error:
        scene.target(old["session_id"], old["stage_id"], mutation=True)
    assert error.value.code == "STALE_TARGET"
    target = error.value.details["current_target"]
    assert target["session_id"] == old["session_id"]
    assert target["stage_id"] != old["stage_id"]
    assert target["source"] == new.GetRootLayer().identifier
    assert target["meters_per_unit"] == 0.01 and target["up_axis"] == "Y"
    assert new.GetRootLayer().ExportToString() == before
    # Recovery requires a separate call carrying both current IDs.
    assert scene.target(target["session_id"], target["stage_id"]) == new


def test_world_rotated_parent_and_quaternion(scene):
    st = scene.stage()
    q = [2**-0.5, 0, 0, 2**-0.5]
    scene.create(st, "/World/Parent", "Xform", position=[10, 0, 0], orientation=q)
    scene.create(
        st,
        "/World/Parent/Box",
        "Cube",
        position=[2, 3, 4],
        orientation=q,
        frame="world",
    )
    assert scene.get(st, "/World/Parent/Box")["world_position"] == pytest.approx(
        [2, 3, 4]
    )
    scene.transform(st, "/World/Parent/Box", position=[3, 2, 1], frame="world")
    assert scene.get(st, "/World/Parent/Box")["world_position"] == pytest.approx(
        [3, 2, 1]
    )
    with pytest.raises(AssetError):
        scene.transform(st, "/World/Parent/Box", orientation=[0, 0, 0, 0])
    # Any Xformable prim may move now, including a stage root; a prim with no Xformable schema may not.
    moved = scene.transform(st, "/World", position=[1, 2, 3])
    assert moved["world_position"] == pytest.approx([1, 2, 3])
    assert moved["layer"] == "session" and moved["source_files_changed"] is False
    st.DefinePrim("/World/Scope", "Scope")
    with pytest.raises(AssetError) as error:
        scene.transform(st, "/World/Scope", position=[0, 0, 0])
    assert error.value.code == "WRONG_TYPE"


def test_active_physics_refused(scene):
    info = scene.stage_info()
    scene.timeline.is_stopped = lambda: False
    with pytest.raises(AssetError, match="Stop"):
        scene.target(info["session_id"], info["stage_id"], True)


def test_tree_inactive_bounds_and_save_reopen(scene):
    st = scene.stage()
    scene.create(st, "/World/Box", "Cube")
    st.GetPrimAtPath("/World/Box").SetActive(False)
    nodes, cap = bounded(st, "/", 1)
    assert cap
    nodes, cap = bounded(st, "/", 10)
    assert len(nodes) == 2 and not cap
    saved = scene.save(st, "copy.usda")
    reopened = Usd.Stage.Open(saved["file"])
    assert not reopened.GetPrimAtPath(
        "/World/Box"
    )  # Flatten exports composed active state.
    with pytest.raises(AssetError):
        scene.save(st, "copy.usda")
    with pytest.raises(AssetError):
        scene.save(st, "../overwrite.usda")


def test_runtime_snapshot_preserves_live_layers_and_nested_scaled_frames(scene):
    st = scene.stage()
    scene.create(st, "/World/Parent", "Xform", position=[3, 0, 0], scale=[2, 2, 2])
    scene.create(st, "/World/Parent/Body", "Cube", position=[0, 0, 5], rigid=True)
    before = [layer.ExportToString() for layer in st.GetLayerStack()]
    q = [2**-0.5, 0, 0, 2**-0.5]
    saved = scene.save(
        st,
        "runtime.usda",
        runtime_poses=[
            dict(path="/World/Parent", world_position=[2, 3, 0], orientation_wxyz=q),
            dict(
                path="/World/Parent/Body",
                world_position=[4, 3, 1],
                orientation_wxyz=[1, 0, 0, 0],
            ),
        ],
    )
    assert saved["runtime_pose_count"] == 2 and saved["checkpoint"] is False
    assert before == [layer.ExportToString() for layer in st.GetLayerStack()]
    output = Usd.Stage.Open(saved["file"])
    world = UsdGeom.Xformable(
        output.GetPrimAtPath("/World/Parent/Body")
    ).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    assert list(world.ExtractTranslation()) == pytest.approx([4, 3, 1])
    assert list(Gf.Transform(world).GetScale()) == pytest.approx([2, 2, 2])
    assert list(world.ExtractRotationQuat().GetImaginary()) == pytest.approx([0, 0, 0])


def test_runtime_snapshot_invalid_pose_does_not_publish(scene):
    scene.create(scene.stage(), "/World/Box", "Cube")
    with pytest.raises(AssetError, match="unit quaternion"):
        scene.save(
            scene.stage(),
            "invalid.usda",
            runtime_poses=[
                dict(
                    path="/World/Box",
                    world_position=[0, 0, 0],
                    orientation_wxyz=[0, 0, 0, 0],
                ),
            ],
        )
    assert not (scene.output / "invalid.usda").exists()


def test_multifile_dependency_and_units(scene, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    cfg = tmp_path / "cfg"
    cfg.write_text(
        json.dumps(
            dict(
                version=1,
                profile="trusted_project",
                roots={"fixture": str(project)},
                staging=str(staging),
            )
        )
    )
    (project / "part.usda").write_text('#usda 1.0\ndef Cube "Cube" {}\n')
    (project / "robot.usda").write_text(
        '#usda 1.0\n(defaultPrim="Robot"\n metersPerUnit=1\n upAxis="Z")\ndef Xform "Robot" {\n def Xform "Part" (references=@part.usda@</Cube>) {}\n }'
    )
    b = Broker(str(cfg))
    scene.broker = b
    snap = b.snapshot("fixture", "robot.usda")
    snap.pop("bytes")
    result = scene.import_asset(scene.stage(), **snap, prim_path="/World/Robot")
    assert result["dependency_layers"] == 2 and scene.stage().GetPrimAtPath(
        "/World/Robot/Asset/Part"
    )
    UsdGeom.SetStageMetersPerUnit(scene.stage(), 0.01)
    with pytest.raises(AssetError, match="units"):
        scene.import_asset(scene.stage(), **snap, prim_path="/World/Wrong")
    (project / "robot.usda").write_text(
        '#usda 1.0\n(defaultPrim="Robot")\ndef Xform "Robot" (references=@missing.usda@) {}'
    )
    snap = b.snapshot("fixture", "robot.usda")
    snap.pop("bytes")
    with pytest.raises(AssetError, match="Unresolved"):
        scene.inspect_asset(**snap)


def test_real_openusd_package_broker_inspection_import_and_missing_layer(
    scene, tmp_path
):
    from pxr import UsdUtils, Sdf

    project, staging = tmp_path / "project", tmp_path / "stage"
    project.mkdir()
    staging.mkdir(mode=0o700)
    (project / "part.usda").write_text(
        '#usda 1.0\n(defaultPrim="Part")\ndef Cube "Part" {}\n'
    )
    (project / "root.usda").write_text(
        '#usda 1.0\n(defaultPrim="Robot"\n metersPerUnit=1\n upAxis="Z")\ndef Xform "Robot" {\n def Xform "Part" (references=@part.usda@</Part>) {}\n }'
    )
    assert UsdUtils.CreateNewUsdzPackage(
        Sdf.AssetPath(str(project / "root.usda")), str(project / "robot.usdz")
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            dict(
                version=1,
                profile="trusted_project",
                roots={"fixture": str(project)},
                staging=str(staging),
            )
        )
    )
    scene.broker = Broker(str(config))
    snapshot = scene.broker.snapshot("fixture", "robot.usdz")
    snapshot.pop("bytes")
    filename, manifest = scene.broker.verify(
        **dict(token=snapshot["snapshot_id"], digest=snapshot["manifest_sha256"])
    )
    assert len(manifest["packages"]["robot.usdz"]["members"]) == 2
    result = scene.import_asset(
        scene.stage(), **snapshot, prim_path="/World/PackagedRobot"
    )
    assert result["dependency_layers"] >= 2
    assert scene.stage().GetPrimAtPath("/World/PackagedRobot/Asset/Part")
    before = scene.stage().GetRootLayer().ExportToString()
    # A valid container with an unresolved nested composition still must fail.
    from test_usdz import package

    (project / "broken.usdz").write_bytes(
        package(
            [
                (
                    "root.usda",
                    b'#usda 1.0\n(defaultPrim="Robot")\ndef Xform "Robot" (references=@absent.usda@) {}',
                )
            ]
        )
    )
    bad = scene.broker.snapshot("fixture", "broken.usdz")
    bad.pop("bytes")
    with pytest.raises(AssetError, match="Unresolved"):
        scene.import_asset(scene.stage(), **bad, prim_path="/World/Bad")
    assert scene.stage().GetRootLayer().ExportToString() == before
    assert not scene.stage().GetPrimAtPath("/World/Bad")


def test_runtime_edit_target_lease(scene):
    st = scene.stage()
    original = st.GetEditTarget().GetLayer()
    scene.begin_runtime_edit(st)
    assert st.GetEditTarget().GetLayer() == st.GetSessionLayer()
    scene.check_runtime_edit(st)
    assert scene.end_runtime_edit(st)
    assert st.GetEditTarget().GetLayer() == original
    scene.begin_runtime_edit(st)
    st.SetEditTarget(original)
    with pytest.raises(AssetError, match="edit target"):
        scene.check_runtime_edit(st)
    assert not scene.end_runtime_edit(st)
    assert st.GetEditTarget().GetLayer() == original


def test_unavailable_profile_never_reads_stage(scene):
    scene.broker.config_error = "isolated profile unavailable"
    with pytest.raises(AssetError, match="no fallback"):
        scene.stage_info()


def test_native_device_failure_refuses_runtime(scene):
    scene.require_runtime()
    scene.device_error = "CUDA error 700"
    with pytest.raises(AssetError, match="restart"):
        scene.require_runtime()
    assert scene.stage_info()["device_error"] == "CUDA error 700"


def test_stage_closing_releases_lease_and_invalidates_target_before_next_request(scene):
    info = scene.stage_info()
    stage = scene.stage()
    before = stage.GetEditTarget().GetLayer()
    scene.begin_runtime_edit(stage)
    scene.owned.add("/World/OurPrim")
    scene.close_stage()
    assert scene._stage is None and scene.stage_id is None and not scene.owned
    assert stage.GetEditTarget().GetLayer() == before
    with pytest.raises(AssetError, match="Session or stage changed"):
        scene.target(info["session_id"], info["stage_id"])


def test_save_omits_native_render_handles_but_preserves_scene_and_source(scene):
    from pxr import Sdf

    st = scene.stage()
    UsdGeom.Camera.Define(st, "/World/Camera")
    st.DefinePrim("/World/Controller", "OmniGraph")
    product = "/Render/OmniverseKit/HydraTextures/IsaacMCP_" + "a" * 32
    st.DefinePrim(product, "RenderProduct")
    graph = "/Render/PostProcess/SDGPipeline"
    st.DefinePrim(graph, "OmniGraph")
    node = st.DefinePrim(graph + "/Dispatch", "OmniGraphNode")
    node.CreateAttribute("node:type", Sdf.ValueTypeNames.Token).Set(
        "omni.syntheticdata.SdOnNewFrame"
    )
    before = st.GetRootLayer().ExportToString()
    saved = scene.save(st, "render-copy.usda")
    result = Usd.Stage.Open(saved["file"])
    assert result.GetPrimAtPath("/World/Camera") and result.GetPrimAtPath(
        "/World/Controller"
    )
    assert not result.GetPrimAtPath(graph) and not result.GetPrimAtPath(product)
    assert saved["omitted_runtime_render_paths"] == sorted([product, graph])
    assert st.GetRootLayer().ExportToString() == before
    node.GetAttribute("node:type").Set("user.CustomController")
    with pytest.raises(AssetError) as exc:
        scene.save(st, "unknown-graph.usda")
    assert exc.value.code == "SAVE_UNSUPPORTED"
    assert not (scene.output / "unknown-graph.usda").exists()


@pytest.mark.parametrize("async_graph", [False, True])
def test_save_omits_replicator_orchestrator_without_touching_live_stage(
    scene, async_graph
):
    from pxr import Sdf

    st = scene.stage()
    native = Sdf.Layer.CreateAnonymous()
    native.ImportFromString("""#usda 1.0
    def OmniGraph "Orchestrator" {
        token evaluator:type = "push"
        token pipelineStage = "pipelineStageSimulation"
        def OmniGraphNode "OgnReadFabricTime" {
            token node:type = "omni.replicator.core.ReadFabricTime"
        }
    }
    """)
    st.GetRootLayer().subLayerPaths.append(native.identifier)
    if async_graph:
        n = st.DefinePrim("/Orchestrator/OrchestratorUpdate", "OmniGraphNode")
        n.CreateAttribute("node:type", Sdf.ValueTypeNames.Token).Set(
            "omni.replicator.core.OgnUpdateOrchestrator"
        )
        n.CreateAttribute(
            "inputs:rationalTimeOfSimNumerator", Sdf.ValueTypeNames.Int64
        ).SetConnections(
            ["/Orchestrator/OgnReadFabricTime.outputs:fabricFrameTimeNumerator"]
        )
    st.DefinePrim("/World/Controller", "OmniGraph")
    before = st.Flatten().ExportToString()
    saved = scene.save(st, "orchestrator-copy.usda")
    reopened = Usd.Stage.Open(saved["file"])
    assert not reopened.GetPrimAtPath("/Orchestrator")
    assert reopened.GetPrimAtPath("/World/Controller")
    assert "/Orchestrator" in saved["omitted_runtime_render_paths"]
    assert st.Flatten().ExportToString() == before

    # A user graph connected to the native graph must never lose its input.
    user = st.GetPrimAtPath("/World/Controller")
    user.CreateAttribute("inputs:time", Sdf.ValueTypeNames.Int64).SetConnections(
        ["/Orchestrator/OgnReadFabricTime.outputs:fabricFrameTimeNumerator"]
    )
    with pytest.raises(AssetError, match="connects to process-bound"):
        scene.save(st, "dependent-controller.usda")
    assert not (scene.output / "dependent-controller.usda").exists()


@pytest.mark.parametrize("content", ["unknown_node", "unmarked", "empty"])
def test_save_refuses_unrecognized_orchestrator_content(scene, content):
    from pxr import Sdf

    layer = Sdf.Layer.CreateAnonymous()
    attributes = (
        'token evaluator:type = "push"\n token pipelineStage = "pipelineStageSimulation"'
        if content != "unmarked"
        else ""
    )
    node = (
        ""
        if content == "empty"
        else """def OmniGraphNode "OgnReadFabricTime" {
        token node:type = "omni.replicator.core.ReadFabricTime"
    }"""
    )
    layer.ImportFromString(
        f'#usda 1.0\ndef OmniGraph "Orchestrator" {{\n {attributes}\n {node}\n }}'
    )
    st = scene.stage()
    st.GetRootLayer().subLayerPaths.append(layer.identifier)
    if content == "unknown_node":
        st.DefinePrim("/Orchestrator/UserNode", "OmniGraphNode")
    before = st.Flatten().ExportToString()
    with pytest.raises(AssetError, match="Unrecognized /Orchestrator"):
        scene.save(st, "unrecognized.usda")
    assert st.Flatten().ExportToString() == before
    assert not (scene.output / "unrecognized.usda").exists()


def camera_pipeline(stage, kind="Image"):
    from pxr import Sdf

    def node(path, node_type):
        prim = stage.DefinePrim(path, "OmniGraphNode")
        prim.CreateAttribute("node:type", Sdf.ValueTypeNames.Token).Set(node_type)
        return prim

    stage.DefinePrim("/Graph/Camera", "OmniGraph")
    producer = node("/Graph/Camera/RenderProduct", "isaacsim.core.nodes.IsaacCreateRenderProduct")
    UsdGeom.Camera.Define(stage, "/World/Camera")
    producer.CreateRelationship("inputs:cameraPrim").SetTargets(["/World/Camera"])
    producer.CreateAttribute("inputs:width", Sdf.ValueTypeNames.Int).Set(1280)
    producer.CreateAttribute("inputs:height", Sdf.ValueTypeNames.Int).Set(720)
    helper = node("/Graph/Camera/Helper", "isaacsim.ros2.bridge.ROS2Camera" +
                  ("InfoHelper" if kind == "CameraInfo" else "Helper"))
    helper.CreateAttribute("inputs:renderProductPath", Sdf.ValueTypeNames.String).SetConnections(
        [producer.GetPath().AppendProperty("outputs:renderProductPath")]
    )
    stage.DefinePrim("/Render/PostProcess/SDGPipeline", "OmniGraph")
    writer = node("/Render/PostProcess/SDGPipeline/Writer",
                  "isaacsim.ros2.bridge.ROS2Publish" + kind)
    writer.CreateAttribute("inputs:writerId", Sdf.ValueTypeNames.String).Set(
        "NodeWriter_12345678-1234-1234-1234-123456789abc"
    )
    for prim in (helper, writer):
        prim.CreateAttribute("inputs:topicName", Sdf.ValueTypeNames.String).Set("rgb")
        prim.CreateAttribute("inputs:frameId", Sdf.ValueTypeNames.String).Set("camera_link")
    node("/Render/PostProcess/SDGPipeline/Time",
         "isaacsim.core.nodes.IsaacReadSimulationTimeAnnotator")
    node("/Render/PostProcess/SDGPipeline/Sync", "omni.graph.action.RationalTimeSyncGate")
    return helper, writer


@pytest.mark.parametrize("kind", ["Image", "CameraInfo"])
@pytest.mark.parametrize("gate", [
    "omni.graph.action.RationalTimeSyncGate",
    "omni.replicator.core.OgnRationalTimeSyncGateOnce",
])
def test_save_regenerable_ros_camera_pipeline_preserves_authored_helper(scene, kind, gate):
    stage = scene.stage()
    helper, _ = camera_pipeline(stage, kind)
    stage.GetPrimAtPath("/Render/PostProcess/SDGPipeline/Sync").GetAttribute(
        "node:type"
    ).Set(gate)
    before = stage.Flatten().ExportToString()
    result = scene.save(stage, "ros-camera.usda")
    reopened = Usd.Stage.Open(result["file"])
    assert reopened.GetPrimAtPath(helper.GetPath())
    assert reopened.GetPrimAtPath(helper.GetPath()).GetAttribute(
        "inputs:renderProductPath"
    ).GetConnections() == helper.GetAttribute("inputs:renderProductPath").GetConnections()
    assert not reopened.GetPrimAtPath("/Render/PostProcess/SDGPipeline")
    assert stage.Flatten().ExportToString() == before


def test_save_unknown_sync_node_reports_identity_and_preserves_live_stage(scene):
    stage = scene.stage()
    camera_pipeline(stage)
    node_path = "/Render/PostProcess/SDGPipeline/Sync"
    stage.GetPrimAtPath(node_path).GetAttribute("node:type").Set("custom.UnreviewedGate")
    before = stage.Flatten().ExportToString()
    with pytest.raises(AssetError) as error:
        scene.save(stage, "unreviewed-gate.usda")
    assert error.value.code == "SAVE_UNSUPPORTED"
    assert error.value.details == {"path": node_path, "node_type": "custom.UnreviewedGate"}
    assert not (scene.output / "unreviewed-gate.usda").exists()
    assert stage.Flatten().ExportToString() == before


@pytest.mark.parametrize("damage", ["helper", "connection", "frame", "writer_id", "relationship"])
def test_save_ros_pipeline_refuses_nonregenerable_or_user_dependency(scene, damage):
    stage = scene.stage()
    helper, writer = camera_pipeline(stage)
    if damage == "helper":
        stage.RemovePrim(helper.GetPath())
    elif damage == "connection":
        helper.GetAttribute("inputs:renderProductPath").ClearConnections()
    elif damage == "frame":
        writer.GetAttribute("inputs:frameId").Set("a_different_camera")
    elif damage == "writer_id":
        writer.GetAttribute("inputs:writerId").Set("hand-authored")
    else:
        stage.GetPrimAtPath("/World").CreateRelationship("nativeInput").SetTargets(
            [writer.GetPath()]
        )
    before = stage.Flatten().ExportToString()
    with pytest.raises(AssetError) as exc:
        scene.save(stage, "unsafe-ros.usda")
    assert exc.value.code == "SAVE_UNSUPPORTED"
    assert not (scene.output / "unsafe-ros.usda").exists()
    assert stage.Flatten().ExportToString() == before


@pytest.mark.parametrize("settings_path", ["/Render/OmniverseGlobalRenderSettings", "/World/UserSettings"])
def test_save_removes_only_native_settings_links_to_owned_products(scene, settings_path):
    stage = scene.stage()
    owned = "/Render/OmniverseKit/HydraTextures/IsaacMCP_" + "b" * 32
    user = "/Render/UserProduct"
    for path in (owned, user):
        stage.DefinePrim(path, "RenderProduct")
    settings = stage.DefinePrim(settings_path, "RenderSettings")
    settings.CreateRelationship("products").SetTargets([owned, user])
    before = stage.Flatten().ExportToString()
    if settings_path.startswith("/World/"):
        with pytest.raises(AssetError, match="Preserved relationship"):
            scene.save(stage, "user-settings.usda")
    else:
        saved = scene.save(stage, "native-settings.usda")
        reopened = Usd.Stage.Open(saved["file"])
        assert reopened.GetPrimAtPath(settings_path).GetRelationship("products").GetTargets() == [user]
        assert reopened.GetPrimAtPath(user)
        assert not reopened.GetPrimAtPath(owned)
    assert stage.Flatten().ExportToString() == before

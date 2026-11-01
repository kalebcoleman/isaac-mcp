"""Real OpenUSD (no Kit/PhysX) tests. Optional extra scene-test installs usd-core."""

import json
from pathlib import Path
import sys
import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import Usd, UsdGeom, Gf
from sim_tools.core import Scene, bounded
from isaac_mcp.project_assets import AssetError, Broker


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
    with pytest.raises(AssetError):
        scene.transform(st, "/World", position=[1, 2, 3])


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

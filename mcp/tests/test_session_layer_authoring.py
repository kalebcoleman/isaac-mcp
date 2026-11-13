"""Mesh, map and joint-target authoring stays in the session layer and reports what it did (real USD, no Kit)."""

import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr.Usd")
from test_scene_usd import scene  # noqa: F401 - the fixture, and it registers the extension package path
from pxr import Usd, UsdGeom, UsdPhysics
from twinsmith.project_assets import AssetError, Broker
from sim_tools import map_import, mesh_import
from sim_tools.validate import apply_joint_targets


def broker_on(tmp_path, project):
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    cfg = tmp_path / "cfg"
    cfg.write_text(json.dumps(dict(version=1, profile="trusted_project", roots={"fixture": str(project)}, staging=str(staging))))
    return Broker(str(cfg))


def session_only(stage, path):
    """The prim has an opinion in the session layer and none in the root layer."""
    return stage.GetSessionLayer().GetPrimAtPath(path) is not None and stage.GetRootLayer().GetPrimAtPath(path) is None


@pytest.fixture
def prop(scene, tmp_path, monkeypatch):  # noqa: F811
    """Import a 1 m cube as a mesh through mesh_import.run, with the Kit converter replaced by a file it would have written."""

    async def convert(source, output, materials, y_up):
        output.mkdir(parents=True, exist_ok=True)
        (output / "mesh.usd").write_text(
            '#usda 1.0\n(defaultPrim="World"\n metersPerUnit=1\n upAxis="Z")\ndef Xform "World" {\n'
            ' def Mesh "m" {\n point3f[] points = [(-0.5,-0.5,0),(0.5,-0.5,0),(0.5,0.5,0),(-0.5,0.5,1)]\n'
            ' int[] faceVertexCounts = [4]\n int[] faceVertexIndices = [0,1,2,3]\n }\n}\n'
        )
        return output / "mesh.usd"

    monkeypatch.setattr(mesh_import, "convert", convert)
    monkeypatch.setattr(mesh_import, "identity", lambda: "test")
    project = tmp_path / "project"
    project.mkdir()
    (project / "cube.stl").write_bytes(b"solid\nendsolid\n")
    scene.broker = broker_on(tmp_path, project)
    info = scene.stage_info()

    def run(**options):
        snap = scene.broker.snapshot("fixture", "cube.stl", kind="mesh")
        snap.pop("bytes")
        args = dict(**snap, session_id=info["session_id"], stage_id=info["stage_id"], format="mesh", **options)
        return asyncio.run(mesh_import.run(SimpleNamespace(scene=scene), args))

    return scene.stage(), run


def test_a_mesh_scale_really_scales_it_and_the_result_says_the_scaled_size(prop):
    """It was authored in the root layer, under the wrapper's session-layer xformOpOrder, so it was silently ignored."""
    stage, run = prop
    full = run(prim_path="/World/full", position=[0, 0, 1])
    tiny = run(prim_path="/World/tiny", position=[5, 0, 1], scale=0.2)
    assert full["size_m"][0] == pytest.approx(1.0)
    assert tiny["size_m"][0] == pytest.approx(0.2) and tiny["scale"] == 0.2
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]).ComputeWorldBound(stage.GetPrimAtPath("/World/tiny"))
    assert box.ComputeAlignedRange().GetSize()[1] == pytest.approx(0.2)


def test_a_mesh_prop_is_authored_in_the_session_layer_only(prop):
    stage, run = prop
    before = stage.GetRootLayer().ExportToString()
    result = run(prim_path="/World/p", mass=1.5, as_collider="convex_hull", scale=0.5)
    assert stage.GetRootLayer().ExportToString() == before
    wrapper = stage.GetPrimAtPath("/World/p")
    assert wrapper.HasAPI(UsdPhysics.RigidBodyAPI) and UsdPhysics.MassAPI(wrapper).GetMassAttr().Get() == 1.5
    assert result["dynamic"] is True and result["collider"] == "convex_hull"
    assert session_only(stage, "/World/p")


def run_map(scene, project, **options):  # noqa: F811
    (project / "map.pgm").write_bytes(b"P5\n2 1\n255\n" + bytes([0, 255]))
    (project / "map.yaml").write_text("image: map.pgm\nresolution: 0.5\norigin: [0.0, 0.0, 0.0]\n")
    snap = scene.broker.snapshot("fixture", "map.yaml", kind="map")
    snap.pop("bytes")
    return map_import.run(SimpleNamespace(scene=scene), dict(**snap, **options))


def test_map_walls_are_in_the_session_layer_and_can_be_removed_with_the_tools(scene, tmp_path):  # noqa: F811
    """They were written to the root layer and not recorded as the tools' own, so sim__prim__delete refused them."""
    project = tmp_path / "project"
    project.mkdir()
    scene.broker = broker_on(tmp_path, project)
    stage = scene.stage()
    before = stage.GetRootLayer().ExportToString()
    result = run_map(scene, project, prim_path="/World/map", position=[1, 0, 0])
    assert result["walls"] == 1
    assert stage.GetRootLayer().ExportToString() == before
    assert session_only(stage, "/World/map/wall_0")
    assert "/World/map" in scene.owned


def test_joint_targets_are_a_session_edit(scene):  # noqa: F811
    stage = scene.stage()
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdPhysics.RevoluteJoint.Define(stage, "/World/r/elbow")
    before = stage.GetRootLayer().ExportToString()
    report = apply_joint_targets(stage, "/World/r", {"elbow": 1.5})
    assert report["applied"] == {"elbow": 1.5}
    assert stage.GetRootLayer().ExportToString() == before
    drive = UsdPhysics.DriveAPI.Get(stage.GetPrimAtPath("/World/r/elbow"), "angular")
    assert drive.GetTargetPositionAttr().Get() == pytest.approx(85.94, abs=0.01)


















def test_a_map_on_a_stage_that_is_not_z_up_metres_is_refused(scene, tmp_path):  # noqa: F811
    """The walls are built Z up in metres: on a centimetre stage they were 100 times too small, and on a Y-up stage on their side."""
    project = tmp_path / "project"
    project.mkdir()
    scene.broker = broker_on(tmp_path, project)
    UsdGeom.SetStageMetersPerUnit(scene.stage(), 0.01)
    with pytest.raises(AssetError) as error:
        run_map(scene, project, prim_path="/World/map")
    assert error.value.code == "UNITS_MISMATCH" and error.value.details["retry_with"] == dict(up_axis="Z", meters_per_unit=1.0)
    assert not scene.stage().GetPrimAtPath("/World/map")


def test_joint_targets_beyond_a_limit_are_set_to_the_limit_and_reported(scene):  # noqa: F811
    stage = scene.stage()
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        joint = UsdPhysics.RevoluteJoint.Define(stage, "/World/r/elbow")
        joint.CreateLowerLimitAttr(-90.0)
        joint.CreateUpperLimitAttr(90.0)
    report = apply_joint_targets(stage, "/World/r", {"elbow": 99.0})
    assert report["applied"]["elbow"] == pytest.approx(1.5707963)
    assert report["beyond_limits"]["elbow"]["asked"] == 99.0
    drive = UsdPhysics.DriveAPI.Get(stage.GetPrimAtPath("/World/r/elbow"), "angular")
    assert drive.GetTargetPositionAttr().Get() == pytest.approx(90.0)


def test_mass_with_no_collider_says_it_got_one(prop):
    _, run = prop
    result = run(prim_path="/World/p", mass=1.0, as_collider="none")
    assert result["collider"] == "convex_hull" and any("convex hull" in w for w in result["warnings"])




























def test_a_large_map_can_be_deleted_as_a_whole(scene, tmp_path):  # noqa: F811
    from sim_tools.prim_tools import deletable

    project = tmp_path / "project"
    project.mkdir()
    scene.broker = broker_on(tmp_path, project)
    stage = scene.stage()
    (project / "map.pgm").write_bytes(b"P5\n501 1\n255\n" + bytes([0, 255] * 250 + [0]))
    (project / "map.yaml").write_text("image: map.pgm\nresolution: 0.5\norigin: [0.0, 0.0, 0.0]\n")
    snap = scene.broker.snapshot("fixture", "map.yaml", kind="map")
    snap.pop("bytes")
    assert map_import.run(SimpleNamespace(scene=scene), dict(**snap, prim_path="/World/map"))["walls"] == 251
    assert deletable(scene, stage, "/World/map")


def test_bulk_ownership_is_cleared_on_stage_change_and_close(scene):
    scene.stage()
    scene.bulk_owned.add("/World/map")
    scene.get_stage = lambda: Usd.Stage.CreateInMemory()
    scene.stage()
    assert not scene.bulk_owned
    scene.bulk_owned.add("/World/map")
    scene.close_stage()
    assert not scene.bulk_owned

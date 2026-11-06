"""Bugs found by running the product live on Sim 6.2 and 5.1, each pinned on real USD with no Kit."""

import asyncio
import json
from xml.etree import ElementTree
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr.Usd")
from test_scene_usd import scene  # noqa: F401 - the fixture, and it registers the extension package path
from pxr import Usd, UsdGeom, UsdPhysics
from isaac_mcp import urdf_physics
from isaac_mcp.project_assets import AssetError, Broker
from sim_tools import map_import, mesh_import
from sim_tools.urdf import _author_drives
from sim_tools.urdf_export import _lost_its_structure, robot_root
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


def test_a_urdf_continuous_joint_is_free_even_when_the_importer_authors_no_limits():
    """Sim 6.2's URDF importer writes no limit on a continuous joint, so the wheel kept a holding stiffness and stopped at about 0.6 rad."""
    urdf = ('<robot name="r"><link name="a"><inertial><mass value="1"/><inertia ixx="1" iyy="1" izz="1" ixy="0" ixz="0" iyz="0"/></inertial></link>'
            '<link name="b"><inertial><mass value="1"/><inertia ixx="1" iyy="1" izz="1" ixy="0" ixz="0" iyz="0"/></inertial></link>'
            '<joint name="wheel" type="continuous"><parent link="a"/><child link="b"/><axis xyz="0 0 1"/></joint></robot>')
    gains = urdf_physics.default_gains(urdf_physics.read_model(ElementTree.fromstring(urdf)))
    assert gains["wheel"]["continuous"] is True
    stage = Usd.Stage.CreateInMemory()
    UsdPhysics.RevoluteJoint.Define(stage, "/Robot/wheel")  # no limits authored
    authored, _, _ = _author_drives(stage.GetPrimAtPath("/Robot"), gains, False)
    assert authored["wheel"]["stiffness"] == 0.0 and authored["wheel"]["damping"] > 0
    drive = UsdPhysics.DriveAPI.Get(stage.GetPrimAtPath("/Robot/wheel"), "angular")
    assert drive.GetStiffnessAttr().Get() == 0.0


def two_robots():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
    for name in ("a", "b"):
        base = UsdGeom.Xform.Define(stage, f"/World/{name}/Asset/Geometry/base").GetPrim()
        tip = UsdGeom.Xform.Define(stage, f"/World/{name}/Asset/Geometry/base/tip").GetPrim()
        for body in (base, tip):
            UsdPhysics.RigidBodyAPI.Apply(body)
        UsdPhysics.ArticulationRootAPI.Apply(base)
        joint = UsdPhysics.RevoluteJoint.Define(stage, f"/World/{name}/Asset/Physics/j")
        joint.CreateBody0Rel().AddTarget(base.GetPath())
        joint.CreateBody1Rel().AddTarget(tip.GetPath())
    UsdGeom.Plane.Define(stage, "/World/Ground")
    return stage


def test_export_of_an_articulation_root_finds_the_robot_above_it():
    """From the articulation root the exporter wrote one link and a floating joint: the robot was gone and the call said ok."""
    stage = two_robots()
    assert robot_root(stage, "/World/b/Asset/Geometry/base") == "/World/b/Asset"
    assert robot_root(stage, "/World/b/Asset/Geometry/base/tip") == "/World/b/Asset"
    assert robot_root(stage, "/World/b") == "/World/b"


@pytest.mark.parametrize("path", ["/World", None])
def test_export_above_two_robots_refuses_and_lists_them(path):
    """It wrote the first robot it found, named after the parent."""
    with pytest.raises(AssetError) as error:
        robot_root(two_robots(), path)
    assert error.value.code == "AMBIGUOUS_ROBOT" and "/World/a/Asset/Geometry/base" in str(error.value)


def test_export_of_something_with_no_bodies_refuses():
    """A ground plane was exported as another robot's geometry."""
    with pytest.raises(AssetError) as error:
        robot_root(two_robots(), "/World/Ground")
    assert error.value.code == "NOT_A_ROBOT"


def test_export_of_a_prop_with_no_moving_joint_stays_on_the_prop():
    stage = two_robots()
    box = UsdGeom.Cube.Define(stage, "/World/box").GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(box)
    assert robot_root(stage, "/World/box") == "/World/box"


def test_a_floating_base_joint_alone_is_a_lost_robot():
    stage = two_robots()
    urdf = ElementTree.fromstring('<robot><link name="world"/><link name="b"/><joint name="floating_base" type="floating"/></robot>')
    assert _lost_its_structure(stage, "/World/a/Asset", urdf) is True
    good = ElementTree.fromstring('<robot><link name="b"/><link name="t"/><joint name="j" type="revolute"/></robot>')
    assert _lost_its_structure(stage, "/World/a/Asset", good) is False


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


def test_a_folder_that_is_the_approved_root_picks_the_usd_named_after_it():
    """The recommended smallest folder is the root itself, whose relative name was empty, so Teapot/Teapot.usd was not picked."""
    from isaac_mcp import asset_formats

    files = ["Teapot.usd", "DrawModes.usd", "geo/Fancy.usd"]
    found = asset_formats.candidates(files, lambda rel: b"", "", "Teapot")
    assert asset_formats.choose(found)["entry"] == "Teapot.usd"
    with pytest.raises(AssetError):
        asset_formats.choose(asset_formats.candidates(files, lambda rel: b"", "", "Other"))
    inner = [f"sub/{f}" for f in ("sub.usd", "other.usd")]
    assert asset_formats.choose(asset_formats.candidates(inner, lambda rel: b"", "sub", "Root"))["entry"] == "sub/sub.usd"


def test_the_snapshot_quota_refusal_names_the_folder_and_what_to_do(tmp_path, monkeypatch):
    import isaac_mcp.project_assets as pa

    project = tmp_path / "project"
    project.mkdir()
    (project / "a.usda").write_text("#usda 1.0\n")
    broker = broker_on(tmp_path, project)
    monkeypatch.setattr(pa, "MAX_SNAPSHOTS", 1)
    broker.snapshot("fixture", "a.usda")
    (project / "a.usda").write_text("#usda 1.0\n#\n")
    with pytest.raises(AssetError) as error:
        broker.snapshot("fixture", "a.usda")
    assert error.value.code == "QUOTA" and str(broker.staging) in str(error.value) and "delete" in str(error.value)


class Calls:
    """Stands in for server._dispatch: records what quick start asks for and answers as the bridge would."""

    def __init__(self, imported=None, scene=None):
        self.calls = []
        self.imported = imported or dict(status="ok", articulation_root="/World/go2/Asset/base", articulation_roots=["/World/go2/Asset/base"], base="floating",
                                         placement=dict(lifted_by=0.25))
        self.scene = scene or {}

    def __call__(self, name, params, state=None):
        self.calls.append((name, params))
        if name == "sim.quick_start":
            return json.dumps(dict(status="ok", session_id="s", stage_id="t", playing=False, stopped=True, **self.scene))
        if name == "assets.import_path":
            return json.dumps(self.imported)
        return json.dumps(dict(status="ok"))

    def named(self, name):
        return [p for n, p in self.calls if n == name]


def quick_start(monkeypatch, calls, **params):
    from isaac_mcp import server

    monkeypatch.setattr(server, "_dispatch", calls)
    return json.loads(server._quick_start_with_import({"robot": "/data/go2", **params}, None))


def test_quick_start_with_a_file_that_fails_to_import_is_an_error_not_a_success(monkeypatch):
    calls = Calls(imported=dict(status="error", code="UNITS_MISMATCH", message="They differ."))
    result = quick_start(monkeypatch, calls, up_axis="Y")
    assert result["status"] == "error" and result["code"] == "UNITS_MISMATCH" and "not imported" in result["message"] and "They differ" in result["message"]
    assert result["scene_built"] is True and result["session_id"] == "s"


def test_quick_start_with_a_file_plays_after_the_import_not_before(monkeypatch):
    """The scene was playing when the import ran, so the import always refused with TIMELINE_ACTIVE."""
    calls = Calls()
    result = quick_start(monkeypatch, calls, play=True)
    assert "play" not in calls.named("sim.quick_start")[0]
    order = [n for n, _ in calls.calls]
    assert order.index("assets.import_path") < order.index("sim.timeline.control")
    assert result["playing"] is True and result["stopped"] is False


def test_quick_start_with_a_file_leaves_the_base_to_the_file_unless_told(monkeypatch):
    calls = Calls()
    quick_start(monkeypatch, calls)
    assert "fixed_base" not in calls.named("assets.import_path")[0]
    calls = Calls()
    quick_start(monkeypatch, calls, fixed_base=True)
    assert calls.named("assets.import_path")[0]["fixed_base"] is True


def test_the_quick_start_contract_does_not_invent_a_fixed_base():
    """A default of true bolted a MuJoCo Go2 and a Gazebo Pioneer to the world through quick start, and not through the import."""
    from isaac_mcp.scene_contracts import REGISTRY

    fields = REGISTRY["sim.quick_start"]["parameters"]
    assert "default" not in fields["fixed_base"] and "xacro_args" in fields and "sdf_model" in fields


def test_quick_start_with_a_file_in_an_environment_replaces_its_robot_and_goes_to_its_start(monkeypatch):
    """The environment's default robot was added too, and the file stood in the middle of the warehouse."""
    calls = Calls(scene=dict(environment=dict(robot_start=[-10.5, -2.5, 0.0])))
    result = quick_start(monkeypatch, calls, environment="warehouse")
    assert calls.named("sim.quick_start")[0]["no_default_robot"] is True
    moved = calls.named("sim.prim.set_transform")[0]
    assert moved["path"] == "/World/go2" and moved["position"] == [-10.5, -2.5, 0.25]  # keeps the lift out of the ground
    assert result["robot"]["articulation_root"] == "/World/go2/Asset/base" and result["robot"]["base"] == "floating"


def test_quick_start_with_a_file_runs_ros_after_the_import(monkeypatch):
    """ros=true was refused with "needs a robot" though one was given."""
    calls = Calls()
    result = quick_start(monkeypatch, calls, ros=True)
    assert "ros" not in calls.named("sim.quick_start")[0]
    assert calls.named("sim.ros2.tf_setup")[0]["robot"] == "/World/go2" and calls.named("sim.ros2.tf_setup")[0]["base"] == "mobile"
    assert result["ros"]["status"] == "ok"


def test_a_urdf_that_holds_xacro_goes_through_the_import_from_quick_start(tmp_path):
    from isaac_mcp import server

    real = tmp_path / "plain.urdf"
    real.write_text('<robot name="r"/>')
    xacro = tmp_path / "burger.urdf"
    xacro.write_text('<robot name="r" xmlns:xacro="http://ros.org/wiki/xacro"/>')
    project = tmp_path / "project"
    project.mkdir()
    real.rename(project / real.name)
    xacro.rename(project / xacro.name)
    state = SimpleNamespace(asset_broker=broker_on(tmp_path, project))
    assert server._quick_start_wants_import(str(project / real.name), state) is False
    assert server._quick_start_wants_import(str(project / xacro.name), state) is True
    assert server._quick_start_wants_import(str(project / "missing.urdf"), state) is False
    # Outside roots, symlinks and non-regular files are never read by the probe.
    outside = tmp_path / "outside.urdf"
    outside.write_text('<robot xmlns:xacro="http://ros.org/wiki/xacro"/>')
    (project / "link.urdf").symlink_to(outside)
    import os
    os.mkfifo(project / "pipe.urdf")
    assert server._quick_start_wants_import(str(outside), state) is False
    assert server._quick_start_wants_import(str(project / "link.urdf"), state) is False
    assert server._quick_start_wants_import(str(project / "pipe.urdf"), state) is False


def test_a_large_map_can_be_deleted_as_a_whole(scene, tmp_path):  # noqa: F811
    from sim_tools.advanced import deletable

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

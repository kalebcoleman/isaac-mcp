"""sim.quick_start: real USD and the real resolver, with Kit's context and app faked."""

import asyncio
import hashlib
import json
import math
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr.Usd")
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts" / "sim-tools"))

from isaac_mcp.project_assets import AssetError  # noqa: E402
from isaac_mcp.scene_contracts import REGISTRY  # noqa: E402
from sim_tools import quick_start as qs  # noqa: E402
from sim_tools.core import Scene  # noqa: E402

ROBOT_USDA = """#usda 1.0
(
    defaultPrim = "arm"
    metersPerUnit = 1
    upAxis = "Z"
)
def Xform "arm" {
    def Xform "root_joint" (prepend apiSchemas = ["PhysicsArticulationRootAPI"]) {}
    def Xform "base_link" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {}
    def Xform "link1" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {
        def Xform "visuals" (instanceable = true; prepend references = </Mesh>) {}
    }
}
def Xform "Mesh" {
    def Cube "box" {}
}
"""


class Timeline:
    def __init__(self):
        self.playing = False

    def is_stopped(self):
        return not self.playing

    def is_playing(self):
        return self.playing

    def get_current_time(self):
        return 0.0


@pytest.fixture()
def kit(monkeypatch, tmp_path):
    """Fake omni.usd/omni.kit.app around a real stage; asset folder in tmp; no project file."""
    state = SimpleNamespace(stage=Usd.Stage.CreateInMemory(), new_stages=0)

    class Context:
        def get_stage(self):
            return state.stage

        async def new_stage_async(self):
            state.new_stages += 1
            state.stage = Usd.Stage.CreateInMemory()
            return True, ""

    class App:
        def is_app_ready(self):
            return True

        async def next_update_async(self):
            return None

    usd = types.ModuleType("omni.usd")
    usd.get_context = Context
    app_module = types.ModuleType("omni.kit.app")
    app_module.get_app = App
    monkeypatch.setitem(sys.modules, "omni", types.ModuleType("omni"))
    monkeypatch.setitem(sys.modules, "omni.usd", usd)
    monkeypatch.setitem(sys.modules, "omni.kit", types.ModuleType("omni.kit"))
    monkeypatch.setitem(sys.modules, "omni.kit.app", app_module)
    sys.modules["omni"].usd = usd
    sys.modules["omni"].kit = sys.modules["omni.kit"]
    sys.modules["omni.kit"].app = app_module
    store = tmp_path / "store"
    (store / "ur5e-dt" / "robot").mkdir(parents=True)
    (store / "ur5e-dt" / "robot" / "ur5e.usd").write_text(ROBOT_USDA)
    # A lab-modified robot: its own pin decides, not the manifest's NVIDIA hash.
    digest = hashlib.sha256(ROBOT_USDA.encode()).hexdigest()
    (store / "ur5e-dt" / "SHA256SUMS").write_text(f"{digest}  robot/ur5e.usd\n")
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    monkeypatch.delenv("ISAAC_MCP_PROJECT", raising=False)
    monkeypatch.delenv("ISAAC_MCP_ASSET_STRICT", raising=False)
    monkeypatch.chdir(tmp_path)
    state.store = store
    state.timeline = Timeline()
    state.scene = Scene(lambda: state.stage, state.timeline, output=tmp_path / "out")
    state.played = []

    async def control(args):
        state.played.append(args)
        state.timeline.playing = True

    state.ext = SimpleNamespace(scene=state.scene, _timeline_control=control)
    state.run = lambda **args: asyncio.run(qs.run(state.ext, args))
    return state


def refused(kit, **args):
    with pytest.raises(AssetError) as error:
        kit.run(**args)
    return error.value


def test_default_needs_nothing_and_builds_a_working_scene(kit):
    original = kit.stage
    out = kit.run()
    stage = kit.stage
    assert stage is original and out["stage_switched"] is False  # an empty stage is reused
    assert stage.GetPrimAtPath(out["physics_scene"]).IsA(UsdPhysics.Scene)
    assert stage.GetPrimAtPath(out["ground"] + "/Geometry").IsA(UsdGeom.Mesh)
    plane = stage.GetPrimAtPath(out["ground"] + "/CollisionPlane")
    assert plane.IsA(UsdGeom.Plane) and plane.HasAPI(UsdPhysics.CollisionAPI)
    assert stage.GetPrimAtPath(out["camera"]).IsA(UsdGeom.Camera)
    assert stage.GetPrimAtPath(out["lights"][0]).IsA(UsdLux.DomeLight)
    assert out["session_id"] == kit.scene.session_id and out["stage_id"] == kit.scene.stage_id
    assert out["stopped"] is True and "robot" not in out and "ros" not in out and kit.played == []
    assert any("sim__camera__capture" in step for step in out["next_steps"])


def test_no_twin_wiring_is_built(kit):
    kit.run(robot="ur5e")
    assert not kit.stage.GetPrimAtPath("/Graph")
    assert not [p for p in kit.stage.Traverse() if "Subscribe" in p.GetTypeName() or "rg2" in p.GetName().lower()]


def test_camera_looks_at_the_scene(kit):
    kit.run()
    world = UsdGeom.Xformable(kit.stage.GetPrimAtPath(qs.CAMERA)).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    eye = world.ExtractTranslation()
    assert list(eye) == pytest.approx(list(qs.EYE))
    forward = world.TransformDir((0, 0, -1)).GetNormalized()  # USD cameras look down -Z
    to_target = (type(eye)(*qs.EYE_TARGET) - eye).GetNormalized()
    assert forward * to_target == pytest.approx(1.0)


def test_camera_frames_the_robot_at_its_size(kit):
    kit.run(robot="ur5e")
    stage = kit.stage
    world = UsdGeom.Xformable(stage.GetPrimAtPath(qs.CAMERA)).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"]).ComputeWorldBound(
        stage.GetPrimAtPath("/World/UR5e")).ComputeAlignedRange()
    to_center = (box.GetMidpoint() - world.ExtractTranslation())
    half_fov = math.atan(qs.HORIZONTAL_APERTURE / qs.CAPTURE_ASPECT / 2 / qs.FOCAL_LENGTH)
    # Aimed a little above the robot, to leave room for it to rise, with all of it still in view.
    off_axis = math.acos(world.TransformDir((0, 0, -1)).GetNormalized() * to_center.GetNormalized())
    assert 0 < off_axis
    assert off_axis + math.asin(box.GetSize().GetLength() / 2 / to_center.GetLength()) <= half_fov + 1e-6


def test_a_prim_with_no_bounds_keeps_the_default_aim(kit):
    kit.run()
    UsdGeom.Xform.Define(kit.stage, "/World/Nothing")
    assert qs.frame_camera_on(kit.stage, "/World/Nothing") is None


def test_edits_are_in_the_session_layer_only(kit):
    before = kit.stage.GetRootLayer().rootPrims
    kit.run(robot="ur5e")
    assert not list(before) and not list(kit.stage.GetRootLayer().rootPrims)
    assert kit.stage.GetSessionLayer().GetPrimAtPath("/World/UR5e")


def test_public_robot_reports_root_and_source_and_expands_visuals(kit):
    out = kit.run(robot="ur5e")
    assert out["robot"]["prim"] == "/World/UR5e" and out["robot"]["articulation_root"] == "/World/UR5e/root_joint"
    assert out["robot"]["expanded_visuals"] == 1
    assert out["assets"][0]["source"] == "store" and out["assets"][0]["location"].endswith("robot/ur5e.usd")
    assert any('path="/World/UR5e/root_joint"' in step for step in out["next_steps"])
    assert not kit.stage.GetPrimAtPath("/World/UR5e/link1/visuals").IsInstance()


def test_public_robot_without_a_local_copy_uses_nvidia(kit, monkeypatch, tmp_path):
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(tmp_path / "empty"))
    found = qs.resolve_robot("ur5e")
    assert found["assets"][0]["source"] == "remote" and found["uri"].startswith("https://")


def test_strict_mode_refuses_before_changing_anything(kit, monkeypatch, tmp_path):
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("ISAAC_MCP_ASSET_STRICT", "1")
    error = refused(kit, robot="ur5e")
    assert error.code == "ASSET_MISSING" and "strict" in str(error)
    assert not kit.stage.GetPrimAtPath("/World")


def test_a_robot_from_a_project_file_is_not_an_approval(kit, monkeypatch, tmp_path):
    repo = tmp_path / "cloned"
    (repo / "assets" / "ur5e-dt" / "robot").mkdir(parents=True)
    (repo / "assets" / "ur5e-dt" / "robot" / "ur5e.usd").write_text(ROBOT_USDA)
    (repo / "isaac-mcp.json").write_text(json.dumps({"asset_path": ["assets"]}))
    monkeypatch.chdir(repo)
    assert refused(kit, robot="ur5e").code == "ROOT_NOT_APPROVED"
    assert not kit.stage.GetPrimAtPath("/World")


def test_pack_file_is_verified_and_named(kit):
    pack = kit.store / "mine"
    pack.mkdir()
    (pack / "arm.usd").write_text(ROBOT_USDA)
    out = kit.run(robot="mine/arm.usd")
    assert out["robot"]["prim"] == "/World/arm" and out["robot"]["articulation_root"] == "/World/arm/root_joint"
    assert out["assets"][0]["location"].endswith("mine/arm.usd")
    kit2 = pack / "SHA256SUMS"
    kit2.write_text("0" * 64 + "  arm.usd\n")
    assert refused(kit, robot="mine/arm.usd", discard_changes=True).code == "ASSET_INVALID"


@pytest.mark.parametrize("robot,code", [
    ("nobody", "INVALID_VALUE"),
    ("mine/../../etc/passwd.usd", "INVALID_VALUE"),
    ("mine/notes.txt", "INVALID_VALUE"),
    ("../x/arm.usd", "INVALID_VALUE"),
    ("mine/absent.usd", "ASSET_MISSING"),
    ("/etc/hostile.usd", "ROOT_NOT_APPROVED"),  # not through the host: no snapshot, so never read
    ("~/hostile.usd", "ROOT_NOT_APPROVED"),
])
def test_robot_refusals_change_nothing(kit, robot, code):
    assert refused(kit, robot=robot).code == code
    assert not kit.stage.GetPrimAtPath("/World")


def test_a_robot_file_that_composes_to_nothing_is_refused(kit):
    pack = kit.store / "mine"
    pack.mkdir()
    (pack / "empty.usd").write_text('#usda 1.0\n(\n    defaultPrim = "x"\n    metersPerUnit = 1\n    upAxis = "Z"\n)\ndef Xform "x" {}\n')
    error = refused(kit, robot="mine/empty.usd")
    assert error.code == "IMPORT_FAILED" and not kit.stage.GetPrimAtPath("/World/empty")


def test_unsaved_work_is_refused_then_dropped_only_when_asked(kit):
    UsdGeom.Xform.Define(kit.stage, "/Mine")
    old = kit.stage
    error = refused(kit)
    assert error.code == "UNSAVED_CHANGES" and kit.stage is old and kit.new_stages == 0
    out = kit.run(discard_changes=True)
    assert kit.stage is not old and out["stage_switched"] is True and out["discarded_changes"] is True


def test_running_twice_is_refused_because_the_first_scene_is_unsaved_work(kit):
    kit.run()
    assert refused(kit).code == "UNSAVED_CHANGES"


def test_a_running_timeline_is_refused_with_the_stop_call(kit):
    kit.timeline.playing = True
    error = refused(kit)
    assert error.code == "TIMELINE_ACTIVE" and error.details["before_retry"]["arguments"]["action"] == "stop"
    assert not kit.stage.GetPrimAtPath("/World")


def test_play_goes_through_the_guarded_timeline_path(kit):
    out = kit.run(robot="ur5e", play=True)
    assert kit.played == [dict(session_id=out["session_id"], stage_id=out["stage_id"], action="play")]
    assert out["playing"] is True and not any(step.startswith("Play:") for step in out["next_steps"])


def test_ros_without_a_robot_is_refused_first(kit):
    assert refused(kit, ros=True).code == "INVALID_VALUE"
    assert not kit.stage.GetPrimAtPath("/World")


@pytest.mark.parametrize("name", ["ros", "play", "discard_changes"])
def test_flags_must_be_booleans(kit, name):
    assert refused(kit, **{name: "yes"}).code == "INVALID_VALUE"


def test_ros_uses_tf_setup_and_reports_the_shell_line(kit, monkeypatch):
    from sim_tools import ros2

    calls = []

    def setup(scene, stage, args):
        calls.append(args)
        return dict(graph="/Graph/g", topics=["/tf"], rviz_fixed_frame="world", clock=dict(source="c"), warnings=[])

    monkeypatch.setattr(ros2, "setup", setup)
    monkeypatch.setattr(Scene, "stage_info", lambda self, real=Scene.stage_info: {**real(self), "ros": {"shell": "export X"}})
    out = kit.run(robot="ur5e", ros=True)
    assert calls == [dict(robot="/World/UR5e")]
    assert out["ros"]["graph"] == "/Graph/g" and out["ros"]["shell"] == "export X"


def test_ros_failure_keeps_the_scene_and_says_why(kit, monkeypatch):
    from sim_tools import ros2

    def setup(scene, stage, args):
        raise AssetError("ROS2_GRAPH_FAILED", "no bridge extension")

    monkeypatch.setattr(ros2, "setup", setup)
    out = kit.run(robot="ur5e", ros=True)
    assert out["ros"]["error"] == "ROS2_GRAPH_FAILED" and out["robot"]["prim"] == "/World/UR5e"


def test_its_own_ros_path_cannot_trip_the_joint_state_loop(kit):
    """It builds no subscriber, so the default /joint_states topic has nothing to loop with."""
    from sim_tools import ros2

    kit.run(robot="ur5e")
    subscribers = [(str(p.GetPath()), "", "") for p in kit.stage.Traverse() if "SubscribeJointState" in p.GetTypeName()]
    assert subscribers == [] and ros2.joint_state_loop("joint_states", "", subscribers) == []


def test_save_keeps_the_scene_and_it_opens_on_its_own(kit, tmp_path):
    kit.run(robot="ur5e")
    saved = kit.scene.save(kit.stage, "kept")
    reopened = Usd.Stage.Open(saved["file"])
    assert reopened.GetPrimAtPath(qs.PHYSICS_SCENE).IsA(UsdPhysics.Scene)
    assert reopened.GetPrimAtPath(qs.GROUND + "/CollisionPlane").HasAPI(UsdPhysics.CollisionAPI)
    assert reopened.GetPrimAtPath(qs.CAMERA).IsA(UsdGeom.Camera)
    assert reopened.GetPrimAtPath("/World/UR5e/root_joint").HasAPI(UsdPhysics.ArticulationRootAPI)
    assert reopened.GetPrimAtPath("/World/UR5e/link1/visuals/box")
    assert reopened.GetDefaultPrim().GetName() == "World"
    assert not Sdf.Layer.FindOrOpen(saved["file"]).subLayerPaths


# ---- up axis and units ----


def world_of(stage, path):
    return UsdGeom.Xformable(stage.GetPrimAtPath(path)).ComputeLocalToWorldTransform(Usd.TimeCode.Default())


def test_y_up_scene_follows_the_axis(kit):
    out = kit.run(up_axis="Y")
    stage = kit.stage
    assert UsdGeom.GetStageUpAxis(stage) == "Y" and out["up_axis"] == "Y"
    scene = UsdPhysics.Scene(stage.GetPrimAtPath(qs.PHYSICS_SCENE))
    assert list(scene.GetGravityDirectionAttr().Get()) == [0, -1, 0]
    assert UsdGeom.Plane(stage.GetPrimAtPath(qs.GROUND + "/CollisionPlane")).GetAxisAttr().Get() == "Y"
    mesh = UsdGeom.Mesh(stage.GetPrimAtPath(qs.GROUND + "/Geometry"))
    points = mesh.GetPointsAttr().Get()
    assert {p[1] for p in points} == {0} and {abs(p[0]) for p in points} == {25} and {abs(p[2]) for p in points} == {25}
    first = points[1] - points[0]
    second = points[2] - points[1]
    assert list(Gf.Cross(first, second).GetNormalized()) == pytest.approx([0, 1, 0])  # faces up
    assert list(mesh.GetNormalsAttr().Get()[0]) == [0, 1, 0]
    assert list(mesh.GetExtentAttr().Get()[0]) == [-25, 0, -25]


@pytest.mark.parametrize("up,index", [("Z", 2), ("Y", 1)])
def test_camera_is_above_the_ground_and_upright_on_either_axis(kit, up, index):
    kit.run(up_axis=up)
    world = world_of(kit.stage, qs.CAMERA)
    assert world.ExtractTranslation()[index] == pytest.approx(qs.EYE[2])
    assert world.TransformDir((0, 1, 0))[index] > 0  # the camera's own up points up
    assert world.TransformDir((1, 0, 0))[index] == pytest.approx(0, abs=1e-9)  # and the horizon is level
    assert world.TransformDir((0, 0, -1)).GetNormalized()[index] < 0  # it looks down at the ground


@pytest.mark.parametrize("up,index", [("Z", 2), ("Y", 1)])
def test_sun_shines_down_on_either_axis(kit, up, index):
    kit.run(up_axis=up)
    assert world_of(kit.stage, qs.SUN).TransformDir((0, 0, -1))[index] < 0  # distant lights shine along -Z


def test_z_up_metres_stays_the_default_layout(kit):
    out = kit.run()
    scene = UsdPhysics.Scene(kit.stage.GetPrimAtPath(qs.PHYSICS_SCENE))
    assert out["up_axis"] == "Z" and out["meters_per_unit"] == 1.0
    assert scene.GetGravityMagnitudeAttr().Get() == pytest.approx(9.81)
    assert list(world_of(kit.stage, qs.CAMERA).ExtractTranslation()) == pytest.approx(list(qs.EYE))


def test_distances_scale_with_units(kit):
    out = kit.run(meters_per_unit=0.01)
    stage = kit.stage
    assert UsdGeom.GetStageMetersPerUnit(stage) == 0.01 and out["meters_per_unit"] == 0.01
    assert UsdPhysics.Scene(stage.GetPrimAtPath(qs.PHYSICS_SCENE)).GetGravityMagnitudeAttr().Get() == pytest.approx(981.0)
    points = UsdGeom.Mesh(stage.GetPrimAtPath(qs.GROUND + "/Geometry")).GetPointsAttr().Get()
    assert {abs(p[0]) for p in points} == {2500}
    assert world_of(stage, qs.CAMERA).ExtractTranslation()[2] == pytest.approx(qs.EYE[2] * 100)
    clip = UsdGeom.Camera(stage.GetPrimAtPath(qs.CAMERA)).GetClippingRangeAttr().Get()
    assert list(clip) == pytest.approx([1.0, 100000.0])


def y_up_file(kit, name="scene.usd"):
    pack = kit.store / "chess"
    pack.mkdir(exist_ok=True)
    (pack / name).write_text('''#usda 1.0
(
    defaultPrim = "set"
    metersPerUnit = 0.01
    upAxis = "Y"
)
def Xform "set" {
    def Cube "piece" {
        double size = 4
        double3 xformOp:translate = (0, 2, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
}
''')
    return f"chess/{name}"


def test_a_matching_file_works_and_frames_at_its_size(kit):
    out = kit.run(robot=y_up_file(kit), up_axis="Y", meters_per_unit=0.01)
    stage = kit.stage
    assert out["robot"]["prim"] == "/World/scene" and stage.GetPrimAtPath("/World/scene/piece")
    world = world_of(stage, qs.CAMERA)
    assert world.ExtractTranslation()[1] > 0
    minimum = qs.MIN_FRAMED_RADIUS_M * 100  # 50 cm in a centimetre stage, not 0.5 units
    half_fov = math.atan(qs.HORIZONTAL_APERTURE / qs.CAPTURE_ASPECT / 2 / qs.FOCAL_LENGTH)
    assert math.asin(minimum / (world.ExtractTranslation() - Gf.Vec3d(0, 2, 0)).GetLength()) <= half_fov + 1e-6


@pytest.mark.parametrize("args", [{}, {"up_axis": "Y"}, {"meters_per_unit": 0.01}])
def test_a_mismatch_refuses_with_both_sets_and_the_exact_args(kit, args):
    error = refused(kit, robot=y_up_file(kit), **args)
    assert error.code == "UNITS_MISMATCH"
    assert error.details["file_units"] == dict(up_axis="Y", meters_per_unit=0.01)
    assert error.details["scene_units"] == dict(up_axis=args.get("up_axis", "Z"), meters_per_unit=args.get("meters_per_unit", 1.0))
    assert error.details["retry_with"] == dict(up_axis="Y", meters_per_unit=0.01)
    assert 'up_axis="Y" and meters_per_unit=0.01' in str(error)
    assert not kit.stage.GetPrimAtPath("/World")


def test_the_public_robot_on_a_y_up_stage_refuses_rather_than_lying_on_its_side(kit):
    assert refused(kit, robot="ur5e", up_axis="Y").details["retry_with"] == dict(up_axis="Z", meters_per_unit=1.0)


def test_an_unreadable_local_file_refuses_before_changing_anything(kit):
    pack = kit.store / "chess"
    pack.mkdir()
    (pack / "broken.usda").write_text("#usda 1.0\ndef Oops {")
    assert refused(kit, robot="chess/broken.usda").code == "IMPORT_FAILED"
    assert not kit.stage.GetPrimAtPath("/World")


def test_a_remote_file_that_cannot_be_read_first_is_noted_not_refused(kit, monkeypatch):
    monkeypatch.setattr(qs, "file_units", lambda uri: (_ for _ in ()).throw(RuntimeError("no network")))
    wanted = dict(uri="https://x/y.usd", assets=[dict(source="remote")])
    assert qs.check_resolved_units(wanted, "Z", 1.0) is False


@pytest.mark.parametrize("args", [{"up_axis": "X"}, {"up_axis": "y"}, {"up_axis": 1}, {"meters_per_unit": 0},
                                  {"meters_per_unit": -1}, {"meters_per_unit": True}, {"meters_per_unit": "1"},
                                  {"meters_per_unit": float("nan")}, {"meters_per_unit": float("inf")}])
def test_bad_axis_or_units_are_refused_first(kit, args):
    assert refused(kit, **args).code == "INVALID_VALUE"
    assert not kit.stage.GetPrimAtPath("/World")


def test_a_new_stage_gets_the_values_too(kit):
    UsdGeom.Xform.Define(kit.stage, "/Mine")
    kit.run(up_axis="Y", meters_per_unit=0.01, discard_changes=True)
    assert kit.new_stages == 1 and UsdGeom.GetStageUpAxis(kit.stage) == "Y"
    assert UsdGeom.GetStageMetersPerUnit(kit.stage) == 0.01


def test_save_and_reopen_keeps_the_axis_and_units(kit):
    kit.run(robot=y_up_file(kit), up_axis="Y", meters_per_unit=0.01)
    saved = kit.scene.save(kit.stage, "yup")
    reopened = Usd.Stage.Open(saved["file"])
    assert UsdGeom.GetStageUpAxis(reopened) == "Y" and UsdGeom.GetStageMetersPerUnit(reopened) == 0.01
    assert list(UsdPhysics.Scene(reopened.GetPrimAtPath(qs.PHYSICS_SCENE)).GetGravityDirectionAttr().Get()) == [0, -1, 0]
    assert reopened.GetPrimAtPath("/World/scene/piece")


def test_contract_needs_no_ids_and_cannot_name_a_command():
    params = REGISTRY["sim.quick_start"]["parameters"]
    assert set(params) == {"robot", "xacro_args", "sdf_model", "environment", "fixed_base", "ros", "up_axis", "meters_per_unit", "play", "discard_changes"}
    assert not any(field["required"] for field in params.values())


def test_host_turns_an_absolute_path_into_a_snapshot_or_refuses(monkeypatch):
    from isaac_mcp import server as gw

    calls = []

    class Client:
        def call(self, name, params=None):
            calls.append((name, params))
            return dict(staging="/stage") if name == "sim.stage.info" else dict(status="ok")

    class Broker:
        staging = "/stage"

        def locate(self, path, kind="usd"):
            assert path == "/approved/arm.usd"
            return "root", "arm.usd"

        def snapshot(self, root_id, relative_path, kind):
            assert (root_id, relative_path, kind) == ("root", "arm.usd", "usd")
            return dict(snapshot_id="s", manifest_sha256="m")

    state = SimpleNamespace(get_client=lambda: Client(), asset_broker=Broker())
    gw._dispatch("sim.quick_start", {"robot": "/approved/arm.usd"}, state)
    assert calls[-1] == ("sim.quick_start", {"robot": "/approved/arm.usd", "snapshot_id": "s", "manifest_sha256": "m"})
    state.asset_broker.staging = None
    calls.clear()
    answer = json.loads(gw._dispatch("sim.quick_start", {"robot": "/approved/arm.usd"}, state))
    assert answer["code"] == "NO_SHARED_STAGING" and all(name != "sim.quick_start" for name, _ in calls)


def test_host_passes_names_and_packs_straight_through(monkeypatch):
    from isaac_mcp import server as gw

    calls = []
    client = SimpleNamespace(call=lambda name, params=None: calls.append((name, params)) or dict(status="ok"))
    gw._dispatch("sim.quick_start", {"robot": "ur5e"}, SimpleNamespace(get_client=lambda: client))
    assert calls == [("sim.quick_start", {"robot": "ur5e"})]


def test_next_steps_send_inspect_to_the_articulation_root_not_a_link():
    steps = qs.next_steps({"session_id": "s", "stage_id": "g"}, "/World/Robot", "/World/Robot/root_joint", False)
    inspect = [step for step in steps if "sim__joints__inspect" in step]
    assert inspect and 'path="/World/Robot/root_joint"' in inspect[0]
    assert "articulation root" in inspect[0]


def test_public_robot_next_steps_never_name_a_link_for_joint_inspection(kit):
    out = kit.run(robot="ur5e")
    root = out["robot"]["articulation_root"]
    inspect = [step for step in out["next_steps"] if "sim__joints__inspect" in step]
    assert inspect and f'path="{root}"' in inspect[0]

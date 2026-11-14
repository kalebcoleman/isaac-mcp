"""The work cell's quick_start summary, the room around it and the black tabletop (CPU only; pxr for the USD parts)."""

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "exts/workcell/workcell"


def load(name):
    spec = importlib.util.spec_from_file_location(f"workcell_room_test_{name}", PACKAGE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def full_report():
    blocks = [{"prim_path": f"/World/Jenga/Block_{i:02d}", "mass_kg": 0.015, "static_friction": 0.3} for i in range(54)]
    return {
        "state": "ready", "mode": "physics", "backend": "newton", "timeline": "stopped", "gravity_m_s2": -9.81,
        "robot_gravity_disabled": False, "tabletop_z_m": 0.0,
        "pack": {"id": "ur5e-jenga-workcell", "version": "v2", "qualification": "candidate",
                 "files": [{"path": f"f{i}.usda", "sha256": "0" * 64} for i in range(16)]},
        "robot": {"assembly_path": "/World/RobotAssembly", "robot_path": "/World/RobotAssembly/robot",
                  "articulation_root": "/World/RobotAssembly/robot/root_joint", "tool0_path": "/t", "gripper_path": "/g",
                  "max_opening_m": 0.11, "parts": [{"prim_path": "/p"}] * 7, "limitations": ["x"] * 16},
        "cameras": {"wrist": {"status": "built", "prim_path": "/w", "intrinsics": {"fx": 1.0} | {f"k{i}": i for i in range(40)}},
                    "overhead": {"status": "built", "prim_path": "/o"}},
        "jenga": {"block_count": 54, "layers": 18, "variation_profile": "p", "variation_seed": 42, "blocks": blocks,
                  "parts": blocks},
        "parts": [{"prim_path": f"/p{i}"} for i in range(68)],
        "bom_audit": {"ok": True, "errors": [], "bom_ids": {"jenga_block": 54}},
        "newton": {"settings": {"newton:timeStepsPerSecond": 500},
                   "buffers": {"rigid_bodies": 70, "nconmax": 2240, "njmax": 17920, "warnings": [], "sizing": "..."}},
        "environment": {"name": "simple_room", "deactivated_for_overlap": ["table_low_327"]},
        "limitations": ["Pack fidelity is declared."] * 36 + ["Newton: drives differ.", "Newton: cameras not qualified."],
    }


def test_quick_start_returns_a_summary_and_status_the_full_report():
    commands_module = load("commands")
    commands = commands_module.Commands()
    report = full_report()
    calls = []

    async def quick_start(**kwargs):
        calls.append(kwargs)
        return report

    commands.workcell = types.SimpleNamespace(quick_start=quick_start, status=lambda: report)
    summary = asyncio.run(commands._cmd_workcell_quick_start("c.json", "physics", True, "simple_room"))
    assert calls == [{"config_path": "c.json", "mode": "physics", "discard_changes": True, "environment": "simple_room"}]
    assert len(json.dumps(summary)) < len(json.dumps(report)) / 4
    assert summary["state"] == "ready" and summary["backend"] == "newton"
    assert summary["pack"] == {"id": "ur5e-jenga-workcell", "version": "v2", "qualification": "candidate"}
    assert summary["robot"]["articulation_root"] == "/World/RobotAssembly/robot/root_joint"
    assert summary["cameras"] == {"wrist": {"status": "built", "prim_path": "/w"},
                                  "overhead": {"status": "built", "prim_path": "/o"}}
    assert summary["jenga"]["block_count"] == 54 and "blocks" not in summary["jenga"]
    assert summary["parts"] == 68 and summary["bom_audit"] == {"ok": True, "errors": []}
    assert summary["newton"] == {"steps_per_second": 500, "rigid_bodies": 70, "nconmax": 2240, "njmax": 17920, "warnings": []}
    assert summary["environment"]["deactivated_for_overlap"] == ["table_low_327"]
    assert summary["limitations"]["count"] == 38 and len(summary["limitations"]["engine"]) == 2
    assert 'action="status"' in summary["full_report"]
    assert commands._cmd_workcell_status() is report     # status is unchanged: the whole report


def test_a_report_that_is_not_ready_passes_through():
    summarize = load("commands").summarize
    failed = {"state": "failed", "error": "boom", "stage_replaced": True}
    assert summarize(failed) == failed


def test_environment_names_and_refusals():
    rooms = load("environment")
    assert rooms.NAMES == ("none", "simple_room")
    assert rooms.resolve("none") is None and rooms.resolve(None) is None
    with pytest.raises(ValueError, match="environment must be one of"):
        rooms.resolve("warehouse", assets_root="https://x", reachable=lambda url: True)
    with pytest.raises(RuntimeError, match="no NVIDIA assets root"):
        rooms.resolve("simple_room", assets_root="", reachable=lambda url: True)
    with pytest.raises(RuntimeError, match="not reachable.*no stage was replaced"):
        rooms.resolve("simple_room", assets_root="https://assets/Isaac/6.2", reachable=lambda url: False)
    seen = []
    url = rooms.resolve("simple_room", assets_root="https://assets/Isaac/6.2/", reachable=lambda u: seen.append(u) or True)
    assert url == seen[0] == "https://assets/Isaac/6.2/Isaac/Environments/Simple_Room/simple_room.usd"


def _room_file(tmp_path):
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    room = Usd.Stage.CreateNew(str(tmp_path / "room.usda"))
    UsdGeom.SetStageUpAxis(room, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(room, 1.0)
    root = UsdGeom.Xform.Define(room, "/Room")
    room.SetDefaultPrim(root.GetPrim())
    UsdGeom.Xform.Define(room, "/Room/GroundPlane")
    plane = UsdGeom.Plane.Define(room, "/Room/GroundPlane/CollisionPlane")
    plane.AddTranslateOp().Set(Gf.Vec3d(0, 0, 0.2))          # the room's floor is 0.2 m above its origin
    UsdPhysics.CollisionAPI.Apply(plane.GetPrim())
    for name, centre, size in (("Table", (0, 0, 0.6), (1.6, 1.0, 0.8)), ("Wall", (4, 0, 1.5), (0.2, 8, 3)),
                               ("Floor", (0, 0, 0.1), (10, 10, 0.2))):
        UsdGeom.Xform.Define(room, f"/Room/{name}")
        cube = UsdGeom.Cube.Define(room, f"/Room/{name}/box")
        cube.CreateSizeAttr(1.0)
        cube.AddTranslateOp().Set(Gf.Vec3d(*centre))
        cube.AddScaleOp().Set(Gf.Vec3f(*size))
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    room.Save()
    return str(tmp_path / "room.usda")


def _cell(tmp_path):
    from pxr import Gf, Usd, UsdGeom

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Bench")
    leg = UsdGeom.Cube.Define(stage, "/World/Bench/leg")     # the bench: 2 x 1 m, from z=-0.8 (feet) to 0 (top)
    leg.CreateSizeAttr(1.0)
    leg.AddTranslateOp().Set(Gf.Vec3d(0, -0.3, -0.4))
    leg.AddScaleOp().Set(Gf.Vec3f(2.0, 1.0, 0.8))
    return stage


def test_the_room_stands_on_the_bench_floor_and_gives_way_to_it(tmp_path):
    pytest.importorskip("pxr.Usd")
    from pxr import Usd, UsdGeom, UsdPhysics

    rooms = load("environment")
    stage = _cell(tmp_path)
    report = rooms.place(stage, "simple_room", _room_file(tmp_path))
    assert report["path"] == "/World/Environment/simple_room"
    assert report["floor_z_m"] == pytest.approx(-0.8) and report["lifted_m"] == pytest.approx(-1.0)
    plane = stage.GetPrimAtPath("/World/Environment/simple_room/GroundPlane/CollisionPlane")
    z = UsdGeom.Xformable(plane).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()[2]
    assert z == pytest.approx(-0.8)                         # the room's ground is level with the bench's feet
    assert report["deactivated_for_overlap"] == ["Table"]   # the floor slab under it and the far wall stay
    assert not stage.GetPrimAtPath("/World/Environment/simple_room/Table").IsActive()
    assert stage.GetPrimAtPath("/World/Environment/simple_room/Wall").IsActive()
    assert report["colliders"]["ground_plane"].endswith("GroundPlane/CollisionPlane")
    assert UsdPhysics.CollisionAPI(plane).GetCollisionEnabledAttr().Get() in (None, True)
    for name in ("Wall", "Floor"):
        box = stage.GetPrimAtPath(f"/World/Environment/simple_room/{name}/box")
        assert UsdPhysics.CollisionAPI(box).GetCollisionEnabledAttr().Get() is False


def test_the_tabletop_gets_its_colour_on_the_mesh_and_its_friction_on_the_collider():
    pytest.importorskip("pxr.Usd")
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

    scene = load("scene")
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World/Bench/Panel")
    UsdGeom.Mesh.Define(stage, "/World/Bench/Panel/Mesh_91")
    box = UsdGeom.Cube.Define(stage, "/World/Bench/Panel/Mesh_91_collision/box")
    box.CreatePurposeAttr(UsdGeom.Tokens.guide)
    UsdPhysics.CollisionAPI.Apply(box.GetPrim())
    top = {"panel_path": "Panel", "color_rgb": [0.02, 0.02, 0.02], "roughness": 0.6,
           "static_friction": 0.3, "dynamic_friction": 0.25}
    report = scene.bench_top(stage, top)
    assert report["render_meshes"] == ["/World/Bench/Panel/Mesh_91"]
    assert report["colliders"] == ["/World/Bench/Panel/Mesh_91_collision/box"]
    material = UsdShade.Material.Get(stage, report["material"])
    physics = UsdPhysics.MaterialAPI(material.GetPrim())
    assert physics.GetStaticFrictionAttr().Get() == pytest.approx(0.3)
    assert physics.GetDynamicFrictionAttr().Get() == pytest.approx(0.25)
    mesh_binding = UsdShade.MaterialBindingAPI(stage.GetPrimAtPath("/World/Bench/Panel/Mesh_91")).ComputeBoundMaterial()[0]
    collider_binding = UsdShade.MaterialBindingAPI(box.GetPrim()).ComputeBoundMaterial(materialPurpose="physics")[0]
    assert mesh_binding.GetPath() == collider_binding.GetPath() == material.GetPath()
    with pytest.raises(ValueError, match="not a prim under"):
        scene.bench_top(stage, dict(top, panel_path="Missing"))


def test_the_shipped_config_has_a_black_top_and_the_robot_facing_the_table():
    sys.path.insert(0, str(PACKAGE))
    try:
        hardware_config = load("hardware_config")
        values, raw = hardware_config.load_config()
    finally:
        sys.path.remove(str(PACKAGE))
    assert max(values["bench_top"]["color_rgb"]) < 0.1
    assert 0 < values["bench_top"]["dynamic_friction"] <= values["bench_top"]["static_friction"]
    assert values["robot"]["placement"]["orientation_wxyz"] == [0, 0, 0, 1]   # 180 degrees about z


def test_a_room_with_its_own_physics_bodies_instancing_and_lights_is_neutralised(tmp_path):
    """Simple Room 6.2 has none of these, but another room may: a second physics scene makes Newton refuse, a loose
    body falls on Play, an instanced collider cannot be switched off, and room lights change the cell's renders."""
    pytest.importorskip("pxr.Usd")
    from pxr import Gf, Usd, UsdGeom, UsdLux, UsdPhysics

    room = Usd.Stage.Open(_room_file(tmp_path))
    UsdPhysics.Scene.Define(room, "/Room/physicsScene")
    UsdLux.RectLight.Define(room, "/Room/Lamp")
    crate = UsdGeom.Cube.Define(room, "/Room/Crate")
    crate.AddTranslateOp().Set(Gf.Vec3d(3, 3, 0.7))
    UsdPhysics.RigidBodyAPI.Apply(crate.GetPrim())
    UsdPhysics.CollisionAPI.Apply(crate.GetPrim())
    UsdGeom.Xform.Define(room, "/Room/Shelf")
    shelf = UsdGeom.Cube.Define(room, "/Room/Shelf/board")
    shelf.AddTranslateOp().Set(Gf.Vec3d(-3, 3, 0.7))
    UsdPhysics.CollisionAPI.Apply(shelf.GetPrim())
    room.GetPrimAtPath("/Room/Shelf").SetInstanceable(True)
    room.Save()

    rooms = load("environment")
    stage = _cell(tmp_path)
    report = rooms.place(stage, "simple_room", str(tmp_path / "room.usda"))
    base = "/World/Environment/simple_room"
    assert report["physics_scenes_deactivated"] == 1 and not stage.GetPrimAtPath(f"{base}/physicsScene").IsActive()
    assert report["lights_deactivated"] == 1 and not stage.GetPrimAtPath(f"{base}/Lamp").IsActive()
    assert report["rigid_bodies_made_kinematic"] == 1
    assert UsdPhysics.RigidBodyAPI(stage.GetPrimAtPath(f"{base}/Crate")).GetKinematicEnabledAttr().Get() is True
    assert report["deinstanced"] == 1
    board = stage.GetPrimAtPath(f"{base}/Shelf/board")
    assert not board.IsInstanceProxy()
    assert UsdPhysics.CollisionAPI(board).GetCollisionEnabledAttr().Get() is False
    assert [p for p in Usd.PrimRange(stage.GetPseudoRoot()) if p.IsA(UsdPhysics.Scene) and p.IsActive()] == []


@pytest.mark.parametrize("change, message", [
    ({"color_rgb": [0.1, 0.1]}, "three numbers"),
    ({"color_rgb": [0.1, 0.1, 2.0]}, "three numbers"),
    ({"dynamic_friction": 0.5}, "must not exceed"),
    ({"static_friction": float("nan")}, "finite"),
    ({"roughness": 1.5}, "roughness must be from 0 to 1"),
    ({"panel_path": "/World/Bench/Panel"}, "relative"),
    ({"extra": 1}, "needs exactly"),
])
def test_a_malformed_bench_top_is_refused_before_the_stage_is_touched(change, message):
    scene = load("scene")
    top = {"panel_path": "Panel", "color_rgb": [0.02, 0.02, 0.02], "roughness": 0.6,
           "static_friction": 0.3, "dynamic_friction": 0.25}
    scene.check_bench_top(top)
    with pytest.raises(ValueError, match=message):
        scene.check_bench_top(dict(top, **change))
    without = dict(top)
    del without["roughness"]
    with pytest.raises(ValueError, match="needs exactly"):
        scene.check_bench_top(without)

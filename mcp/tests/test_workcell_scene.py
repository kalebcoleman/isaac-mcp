"""CPU-only coverage for workcell preflight and tower layout."""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "exts/workcell/workcell"


@pytest.fixture()
def modules(monkeypatch):
    package = types.ModuleType("workcell_test")
    package.__path__ = [str(PACKAGE)]
    monkeypatch.setitem(sys.modules, "workcell_test", package)
    loaded = []
    for name in ("scene", "jenga"):
        spec = importlib.util.spec_from_file_location(f"workcell_test.{name}", PACKAGE / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        loaded.append(module)
    return loaded


@pytest.mark.parametrize("disabled", [False, True], ids=["physics", "mirror_real"])
@pytest.mark.parametrize("asset_disabled", [False, True])
def test_robot_gravity_overrides_referenced_bodies_only(modules, monkeypatch, disabled, asset_disabled):
    Usd = pytest.importorskip("pxr.Usd")
    from pxr import Sdf, UsdPhysics
    import pxr

    class PhysxRigidBodyAPI:
        @staticmethod
        def Apply(prim):
            return types.SimpleNamespace(CreateDisableGravityAttr=lambda: prim.CreateAttribute(
                "physxRigidBody:disableGravity", Sdf.ValueTypeNames.Bool))

    monkeypatch.setattr(pxr, "PhysxSchema", types.SimpleNamespace(
        PhysxRigidBodyAPI=PhysxRigidBodyAPI), raising=False)
    scene, _ = modules
    asset = Usd.Stage.CreateInMemory()
    asset.SetDefaultPrim(asset.DefinePrim("/RG2", "Xform"))
    for index in range(7):
        body = asset.DefinePrim(f"/RG2/Body{index}", "Xform")
        UsdPhysics.RigidBodyAPI.Apply(body)
        PhysxRigidBodyAPI.Apply(body).CreateDisableGravityAttr().Set(asset_disabled)
    asset_before = asset.GetRootLayer().ExportToString()
    stage = Usd.Stage.CreateInMemory()
    robot_layer = Sdf.Layer.CreateAnonymous()
    stage.GetSessionLayer().subLayerPaths.insert(0, robot_layer.identifier)
    with Usd.EditContext(stage, robot_layer):
        stage.DefinePrim("/World/RobotAssembly/rg2").GetReferences().AddReference(
            asset.GetRootLayer().identifier)
    for path in ("/World/Jenga/Block", "/World/RobotAssemblyOther/Body"):
        body = stage.DefinePrim(path, "Xform")
        UsdPhysics.RigidBodyAPI.Apply(body)
        PhysxRigidBodyAPI.Apply(body).CreateDisableGravityAttr().Set(False)
    physics = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    physics.CreateGravityMagnitudeAttr().Set(9.81)
    root_before = stage.GetRootLayer().ExportToString()
    robot_before = robot_layer.ExportToString()

    scene._configure_robot_gravity(stage, "/World/RobotAssembly", disabled)

    for index in range(7):
        body = stage.GetPrimAtPath(f"/World/RobotAssembly/rg2/Body{index}")
        attribute = body.GetAttribute("physxRigidBody:disableGravity")
        assert attribute.Get() is disabled
        assert attribute.GetPropertyStack()[0].layer == stage.GetSessionLayer()
    assert not stage.GetPrimAtPath("/World/RobotAssembly/rg2").GetAttribute("physxRigidBody:disableGravity")
    assert stage.GetEditTarget().GetLayer() == stage.GetRootLayer()
    assert stage.GetRootLayer().ExportToString() == root_before
    assert robot_layer.ExportToString() == robot_before
    assert asset.GetRootLayer().ExportToString() == asset_before


def config(scene):
    return {"pack": {"id": "ur5e-jenga-workcell", "version": "v1"},
            "assets": {role: role for role in scene.ROLES},
            "world": {"workstation_xy_m": [0.2, -0.1], "bench_scale": 0.01,
                      "bench_asset_top_z_m": 0.8, "tabletop_z_m": 0.0},
            "jenga": {"block_size_m": [0.072, 0.024, 0.014], "block_mass_kg": 0.015,
                      "tower_layers_built": 18, "tower_xy_m": [0.7, -0.13],
                      "static_friction": 0.3, "dynamic_friction": 0.25}}


def install_contracts(monkeypatch, scene, tmp_path, values):
    monkeypatch.setattr(scene, "_bom_rows", lambda *args: [])
    hardware = types.ModuleType("workcell_test.hardware_config")
    hardware.load_config = lambda path: (values, {"provenance": True})
    pack_module = types.ModuleType("workcell_test.asset_pack")
    class Pack:
        manifest = {"id": "ur5e-jenga-workcell"}
        def verify_dependencies(self):
            return True
        def asset(self, logical_id):
            return tmp_path / logical_id
    pack_module.resolve_pack = lambda value: Pack()
    for role in scene.ROLES:
        (tmp_path / role).touch()
    monkeypatch.setitem(sys.modules, hardware.__name__, hardware)
    monkeypatch.setitem(sys.modules, pack_module.__name__, pack_module)


def test_preflight_resolves_all_roles(modules, monkeypatch, tmp_path):
    scene, _ = modules
    values = config(scene)
    install_contracts(monkeypatch, scene, tmp_path, values)
    result, raw, pack, assets = scene._preflight(None, "physics")
    assert result is values and raw["provenance"] and pack.manifest["id"]
    assert set(assets) == set(scene.ROLES)
    (tmp_path / "bench").unlink()
    with pytest.raises(ValueError, match="bench"):
        scene._preflight(None, "physics")


def test_bad_configuration_never_mutates_stage(modules, monkeypatch, tmp_path):
    scene, _ = modules
    values = config(scene)
    values["world"]["tabletop_z_m"] = 1
    install_contracts(monkeypatch, scene, tmp_path, values)
    with pytest.raises(ValueError, match="tabletop"):
        asyncio.run(scene.Workcell().quick_start())


def test_bad_variation_never_reaches_pack_or_stage(modules, monkeypatch, tmp_path):
    scene, _ = modules
    values = config(scene)
    values["jenga"]["variation_profile"] = "missing"
    install_contracts(monkeypatch, scene, tmp_path, values)
    with pytest.raises(ValueError, match="profile"):
        asyncio.run(scene.Workcell().quick_start())


def test_bom_path_is_relative_to_configuration(modules, monkeypatch, tmp_path):
    scene, _ = modules
    values = config(scene)
    values["documentation"] = {"bom_csv": "../parts.csv"}
    bom = types.ModuleType("workcell_test.bom")
    seen = []
    rows = [{"asset_id": "jenga_block", "quantity": 54}]
    bom.load_bom = lambda path: seen.append(path.resolve()) or rows
    bom.verify_config_assets = lambda *args: None
    monkeypatch.setitem(sys.modules, bom.__name__, bom)
    assert scene._bom_rows(values, tmp_path / "config/cell.json") == rows
    assert seen == [tmp_path / "parts.csv"]
    values["jenga"]["tower_layers_built"] = 17
    with pytest.raises(ValueError, match="quantity"):
        scene._bom_rows(values, tmp_path / "config/cell.json")


@pytest.mark.parametrize("condition,message", [
    ("idle", "Build the workcell"), ("stale", "stage changed"),
    ("playing", "Stop the timeline"), ("newton", "PhysX"),
])
def test_rebuild_refuses_unsafe_runtime(modules, monkeypatch, condition, message):
    scene, _ = modules
    stage = object()
    cell = scene.Workcell()
    if condition != "idle":
        cell._runtime = (stage, config(scene), {}, [])
        cell._report = {"state": "ready", "parts": []}
    omni = types.ModuleType("omni")
    omni.__path__ = []
    usd = types.ModuleType("omni.usd")
    usd.get_context = lambda: types.SimpleNamespace(
        get_stage=lambda: object() if condition == "stale" else stage)
    timeline = types.ModuleType("omni.timeline")
    timeline.get_timeline_interface = lambda: types.SimpleNamespace(is_stopped=lambda: condition != "playing")
    omni.usd, omni.timeline = usd, timeline
    physics = types.ModuleType("workcell_test.physics_compat")
    physics.selected_engine = lambda: "newton" if condition == "newton" else "physx"
    for module in (omni, usd, timeline, physics):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(scene, "_replace_tower", lambda *args: pytest.fail("must not replace tower"))
    with pytest.raises(RuntimeError, match=message):
        cell.build_jenga()


@pytest.mark.parametrize("failure", ["build", "audit"])
def test_rebuild_failure_preserves_original_layer(modules, monkeypatch, failure):
    Usd = pytest.importorskip("pxr.Usd")
    scene, jenga = modules
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim("/World/Jenga/Original", "Xform")
    before = stage.GetRootLayer().ExportToString()

    def build(temporary, *args, **kwargs):
        if failure == "build":
            raise ValueError("build failed")
        temporary.DefinePrim("/World/Jenga/Replacement", "Xform")
        return {"parts": []}

    def audit(*args):
        assert stage.GetPrimAtPath("/World/Jenga/Replacement")
        raise ValueError("audit failed")

    monkeypatch.setattr(jenga, "build", build)
    monkeypatch.setattr(scene, "_finish_scene", audit)
    with pytest.raises(ValueError, match=failure):
        scene._replace_tower(stage, config(scene), {"jenga_block": "unused"}, [], [])
    assert stage.GetRootLayer().ExportToString() == before


def test_tower_layout_and_cached_status(modules):
    scene, jenga = modules
    poses = jenga.positions(config(scene))
    assert len(poses) == 54
    assert poses[0][0][2] == pytest.approx(0.007)
    assert poses[3][1] == 90 and poses[3][0][2] == pytest.approx(0.021)
    cell = scene.Workcell()
    report = cell.status()
    report["state"] = "changed"
    assert cell.status()["state"] == "idle"
    assert cell.shutdown()["stage_preserved"]


def test_refuses_non_physx_before_stage_access(modules, monkeypatch, tmp_path):
    scene, _ = modules
    install_contracts(monkeypatch, scene, tmp_path, config(scene))
    physics = types.ModuleType("workcell_test.physics_compat")
    physics.selected_engine = lambda: "newton"
    monkeypatch.setitem(sys.modules, physics.__name__, physics)
    with pytest.raises(RuntimeError, match="not yet qualified on Newton") as refused:
        asyncio.run(scene.Workcell().quick_start())
    for way in ("--physics-backend physx", 'physics_backend = "physx"', "TWINSMITH_PHYSICS_BACKEND=physx", "Work cell on Newton"):
        assert way in str(refused.value)


def test_unsaved_stage_refused_without_replacement(modules, monkeypatch, tmp_path):
    scene, jenga = modules
    install_contracts(monkeypatch, scene, tmp_path, config(scene))
    physics = types.ModuleType("workcell_test.physics_compat")
    physics.selected_engine = lambda: "physx"
    monkeypatch.setitem(sys.modules, physics.__name__, physics)
    for name, function in (("robot", "build_robot"), ("cameras", "build_cameras")):
        module = types.ModuleType(f"workcell_test.{name}")
        setattr(module, function, lambda *args: None)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    class Context:
        def get_stage(self):
            return object()
        async def new_stage_async(self):
            pytest.fail("stage must not be replaced")
    omni = types.ModuleType("omni")
    omni.__path__ = []
    usd = types.ModuleType("omni.usd")
    usd.get_context = Context
    timeline = types.ModuleType("omni.timeline")
    timeline.get_timeline_interface = lambda: types.SimpleNamespace(is_stopped=lambda: True)
    omni.usd, omni.timeline = usd, timeline
    for module in (omni, usd, timeline):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    sim_tools = types.ModuleType("sim_tools")
    sim_tools.__path__ = []
    stage_open = types.ModuleType("sim_tools.stage_open")
    stage_open.stage_has_unsaved_work = lambda stage: True
    monkeypatch.setitem(sys.modules, "sim_tools", sim_tools)
    monkeypatch.setitem(sys.modules, "sim_tools.stage_open", stage_open)
    pxr = types.ModuleType("pxr")
    for name in ("Gf", "UsdGeom", "UsdLux", "UsdPhysics"):
        setattr(pxr, name, types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pxr", pxr)
    with pytest.raises(RuntimeError, match="UNSAVED_CHANGES"):
        asyncio.run(scene.Workcell().quick_start())


def test_bench_keeps_its_own_collision_layer_and_else_gets_triangle_meshes(modules):
    Usd = pytest.importorskip("pxr.Usd")
    from pxr import UsdGeom, UsdPhysics

    scene, _ = modules
    plain = Usd.Stage.CreateInMemory()
    mesh = UsdGeom.Mesh.Define(plain, "/World/Bench/Frame/Mesh_0")
    assert scene.bench_colliders(plain) == "triangle meshes"
    assert mesh.GetPrim().GetAttribute("physics:approximation").Get() == "none"

    split = Usd.Stage.CreateInMemory()
    render = UsdGeom.Mesh.Define(split, "/World/Bench/Frame/Mesh_0")
    box = UsdGeom.Cube.Define(split, "/World/Bench/Frame/Mesh_0_collision/box")
    UsdPhysics.CollisionAPI.Apply(box.GetPrim())
    assert scene.bench_colliders(split) == "own"
    assert not render.GetPrim().HasAPI(UsdPhysics.CollisionAPI)

    with pytest.raises(ValueError):
        scene.bench_colliders(Usd.Stage.CreateInMemory())

"""CPU-only coverage for workcell preflight and tower layout."""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "exts/ur5e-dt/ur5e_dt"


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


def config(scene):
    return {"pack": {"id": "ur5e-jenga-workcell", "version": "v1"},
            "assets": {role: role for role in scene.ROLES},
            "world": {"workstation_xy_m": [0.2, -0.1], "bench_scale": 0.01,
                      "bench_asset_top_z_m": 0.8, "tabletop_z_m": 0.0},
            "jenga": {"block_size_m": [0.072, 0.024, 0.014], "block_mass_kg": 0.015,
                      "tower_layers_built": 18, "tower_xy_m": [0.7, -0.13]}}


def install_contracts(monkeypatch, scene, tmp_path, values):
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
    with pytest.raises(RuntimeError, match="PhysX"):
        asyncio.run(scene.Workcell().quick_start())


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

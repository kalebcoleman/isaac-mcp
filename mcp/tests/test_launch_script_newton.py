"""The Kit launch script: a Newton session starts with Newton's tensor extension on, or not at all."""

import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_ur5e_dt.py"
TENSORS = "isaacsim.physics.newton.tensors"


def launch(monkeypatch, backend, installed=(), refuse=(), engine="newton"):
    """Run the script against stand-ins for Kit; returns the extensions it enabled, in order."""
    enabled = []

    def enable(name, on):
        enabled.append(name)
        return name not in refuse

    manager = SimpleNamespace(set_extension_enabled_immediate=enable,
                              get_extensions=lambda: [{"name": name} for name in installed])
    app = SimpleNamespace(is_running=lambda: False, update=lambda: None, close=lambda: None)
    modules = {name: ModuleType(name) for name in ("isaacsim", "isaacsim.core", "isaacsim.core.simulation_manager",
                                                   "omni", "omni.kit", "omni.kit.app")}
    modules["isaacsim"].SimulationApp = lambda config: app
    modules["isaacsim.core.simulation_manager"].SimulationManager = SimpleNamespace(get_active_physics_engine=lambda: engine)
    modules["omni.kit.app"].get_app = lambda: SimpleNamespace(get_extension_manager=lambda: manager)
    modules["omni"].kit = modules["omni.kit"]
    modules["omni.kit"].app = modules["omni.kit.app"]
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("ISAAC_MCP_PHYSICS_BACKEND", backend)
    with pytest.raises(SystemExit) as done:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert done.value.code == 0
    return enabled


def test_a_newton_launch_enables_the_tensor_extension_where_the_release_ships_one(monkeypatch):
    assert launch(monkeypatch, "newton", installed=["isaacsim.physics.newton", TENSORS]) == ["isaacsim.physics.newton", TENSORS]


def test_a_release_without_a_separate_tensor_extension_starts_as_before(monkeypatch):
    assert launch(monkeypatch, "newton", installed=["isaacsim.physics.newton"]) == ["isaacsim.physics.newton"]


def test_a_physx_launch_touches_no_extension(monkeypatch):
    assert launch(monkeypatch, "physx", installed=[TENSORS]) == []


def test_a_tensor_extension_that_will_not_enable_stops_the_launch_instead_of_starting_half_ready(monkeypatch):
    with pytest.raises(RuntimeError, match="could not be enabled; no PhysX fallback"):
        launch(monkeypatch, "newton", installed=[TENSORS], refuse=[TENSORS])
    with pytest.raises(RuntimeError, match="not active"):
        launch(monkeypatch, "newton", installed=[TENSORS], engine="physx")

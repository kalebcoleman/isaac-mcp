"""The Kit launch script: a Newton session starts with Newton's tensor extension on, or not at all."""

import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_kit.py"
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
    if backend is not None:
        monkeypatch.setenv("TWINSMITH_PHYSICS_BACKEND", backend)
    with pytest.raises(SystemExit) as done:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert done.value.code == 0
    return enabled


def launch_without_env(monkeypatch, installed=(), engine="newton"):
    return launch(monkeypatch, None, installed=installed, engine=engine)


def test_a_newton_launch_enables_the_tensor_extension_where_the_release_ships_one(monkeypatch):
    assert launch(monkeypatch, "newton", installed=["isaacsim.physics.newton", TENSORS]) == ["isaacsim.physics.newton", TENSORS]


def test_a_release_without_a_separate_tensor_extension_starts_as_before(monkeypatch):
    assert launch(monkeypatch, "newton", installed=["isaacsim.physics.newton"]) == ["isaacsim.physics.newton"]


def test_a_physx_launch_touches_no_extension(monkeypatch):
    assert launch(monkeypatch, "physx", installed=[TENSORS], engine="physx") == []


def test_a_physx_request_that_ends_on_another_engine_stops_the_launch(monkeypatch):
    """The mirror of the Newton check: no Newton fallback behind a PhysX request, and nothing enabled."""
    with pytest.raises(RuntimeError, match="Requested physx engine is not active.*no newton fallback"):
        launch(monkeypatch, "physx", installed=[TENSORS], engine="newton")


def test_a_build_with_no_engine_query_is_only_physx_and_launches(monkeypatch):
    """An older build's manager has no get_active_physics_engine: nothing to compare, nothing enabled."""
    original = launch.__globals__["SimpleNamespace"]

    def without_query(**kwargs):
        kwargs.pop("get_active_physics_engine", None)
        return original(**kwargs)

    monkeypatch.setitem(launch.__globals__, "SimpleNamespace", without_query)
    assert launch(monkeypatch, "physx", engine="physx") == []


def test_the_script_reads_the_configured_backend_when_run_directly(monkeypatch, tmp_path):
    """No TWINSMITH_PHYSICS_BACKEND (run.sh always sets it): the one configuration module decides."""
    from twinsmith import config
    monkeypatch.delenv("TWINSMITH_PHYSICS_BACKEND", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "twinsmith.toml").write_text('[sim]\nphysics_backend = "newton"\n')
    assert launch_without_env(monkeypatch, installed=["isaacsim.physics.newton"]) == ["isaacsim.physics.newton"]
    (tmp_path / "twinsmith.toml").unlink()
    assert config.DEFAULT_PHYSICS_BACKEND in config.PHYSICS_BACKENDS


def test_a_tensor_extension_that_will_not_enable_stops_the_launch_instead_of_starting_half_ready(monkeypatch):
    with pytest.raises(RuntimeError, match="could not be enabled; no PhysX fallback"):
        launch(monkeypatch, "newton", installed=[TENSORS], refuse=[TENSORS])
    with pytest.raises(RuntimeError, match="not active"):
        launch(monkeypatch, "newton", installed=[TENSORS], engine="physx")

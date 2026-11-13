"""Engine selection must not silently select another backend."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[2] / "exts/ur5e-dt/ur5e_dt/physics_compat.py"
spec = importlib.util.spec_from_file_location("twin_physics_compat_test", path)
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


@pytest.mark.parametrize("engine", ["physx", "newton"])
def test_selected_engine_returns_active_engine(monkeypatch, engine):
    manager = types.SimpleNamespace(get_active_physics_engine=lambda: engine)
    module = types.ModuleType("isaacsim.core.simulation_manager")
    module.SimulationManager = manager
    monkeypatch.setitem(sys.modules, module.__name__, module)

    assert compat.selected_engine() == engine


@pytest.mark.parametrize("engine", [None, "unknown", "PhysX"])
def test_selected_engine_refuses_unknown_backend(monkeypatch, engine):
    manager = types.SimpleNamespace(get_active_physics_engine=lambda: engine)
    module = types.ModuleType("isaacsim.core.simulation_manager")
    module.SimulationManager = manager
    monkeypatch.setitem(sys.modules, module.__name__, module)

    with pytest.raises(RuntimeError, match="no adapter for physics engine"):
        compat.selected_engine()


@pytest.mark.parametrize("legacy_physx", [True, False])
def test_selected_engine_handles_legacy_manager(monkeypatch, legacy_physx):
    manager = types.SimpleNamespace()
    if legacy_physx:
        manager._physx_sim_interface = object()
    module = types.ModuleType("isaacsim.core.simulation_manager")
    module.SimulationManager = manager
    monkeypatch.setitem(sys.modules, module.__name__, module)

    if legacy_physx:
        assert compat.selected_engine() == "physx"
    else:
        with pytest.raises(RuntimeError, match="no adapter for physics engine"):
            compat.selected_engine()

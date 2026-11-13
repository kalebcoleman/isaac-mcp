"""Identify the work cell's selected physics engine without changing it."""


def selected_engine():
    from isaacsim.core.simulation_manager import SimulationManager

    # Isaac Sim 5.1 has no engine selection: its manager drives PhysX directly.
    getter = getattr(SimulationManager, "get_active_physics_engine", None)
    engine = getter() if getter else ("physx" if hasattr(SimulationManager, "_physx_sim_interface") else None)
    if engine not in ("physx", "newton"):
        raise RuntimeError(f"The UR5e work cell has no adapter for physics engine {engine!r}")
    return engine

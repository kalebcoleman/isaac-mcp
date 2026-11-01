"""Construct World's extension workflow without re-entering the Kit update loop.

All five inspected 6.x SimulationContext constructors use the terminal flag to
choose synchronous initialization. SimulationApp sets it false, but the twin is
an async extension: it explicitly awaits initialize_simulation_context_async.
Scope the constructor choice to this synchronous call; never keep a global flag
changed across an await, and never suppress asyncio's re-entry diagnostics.
"""

import builtins


def extension_world(factory):
    flag = "ISAAC_LAUNCHED_FROM_TERMINAL"
    previous = getattr(builtins, flag, None)
    if previous is False:
        setattr(builtins, flag, True)
    try:
        return factory()
    finally:
        if previous is False:
            setattr(builtins, flag, previous)


async def initialized_extension_world(factory):
    world = extension_world(factory)
    try:
        needs_init = world.get_physics_context() is None
    except RuntimeError as e:
        # Later 6.x raises where 6.0.0.0 returns None before async init.
        if "Physics context is not initialized" not in str(e):
            raise
        needs_init = True
    if needs_init:
        await world.initialize_simulation_context_async()
    return world

"""Construct World's extension workflow without re-entering the Kit update loop.

All five inspected 6.x SimulationContext constructors use the terminal flag to
choose synchronous initialization. SimulationApp sets it false, but the twin is
an async extension: it explicitly awaits initialize_simulation_context_async.
Scope the constructor choice to this synchronous call; never keep a global flag
changed across an await, and never suppress asyncio's re-entry diagnostics.
"""

import builtins
from contextlib import asynccontextmanager


@asynccontextmanager
async def paused_scene_edit(timeline, app, get_stage):
    """Let a large visual reference settle without advancing physics.

    On 6.0.0.1/Kit 110.1.1, importing the workstation during playback and
    immediately stopping reproducibly faults the Motion BVH renderer. Pausing
    around composition and draining four ordinary updates on each side of
    resume fixes that sequence. No nested update or destructive stop/reset.
    Never resume a replaced stage or override a concurrent explicit stop.
    """
    stage = get_stage()
    was_playing = timeline.is_playing()

    async def update():
        await app.next_update_async()
        if get_stage() != stage:
            raise RuntimeError("Stage changed during workstation composition; inspect the new stage before retrying")

    if was_playing:
        timeline.pause()
    try:
        if was_playing:
            await update()
        yield
        for _ in range(4):
            await update()
    finally:
        if was_playing and get_stage() == stage and timeline.is_paused():
            timeline.play()
            for _ in range(4):
                await update()


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

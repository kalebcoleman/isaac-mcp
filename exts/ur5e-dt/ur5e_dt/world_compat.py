"""Construct World's extension workflow without re-entering the Kit update loop.

All five inspected 6.x SimulationContext constructors use the terminal flag to
choose synchronous initialization. SimulationApp sets it false, but the twin is
an async extension: it explicitly awaits initialize_simulation_context_async.
Scope the constructor choice to this synchronous call; never keep a global flag
changed across an await, and never suppress asyncio's re-entry diagnostics.
"""

import builtins
from contextlib import asynccontextmanager, contextmanager


@contextmanager
def twin_play_edit(stage, get_stage):
    """Keep Newton's initial USD transform normalization in the session layer.

    The caller validates after every ordinary update. Restore only our target;
    a concurrent user's target or replacement stage is never overwritten.
    """
    previous = stage.GetEditTarget()
    stage.SetEditTarget(stage.GetSessionLayer())
    owned = stage.GetEditTarget()

    def validate():
        if get_stage() != stage:
            raise RuntimeError("Stage changed during twin Play; inspect the current stage")
        if stage.GetEditTarget() != owned:
            raise RuntimeError("Edit target changed during twin Play; the external target was preserved")

    try:
        validate()
        yield validate
        validate()
    finally:
        if stage.GetEditTarget() == owned:
            stage.SetEditTarget(previous)


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
        # Timeline exposes is_playing/is_stopped in every inspected 6.x build;
        # there is no is_paused() method on the older native interface.
        if (was_playing and get_stage() == stage
                and not timeline.is_playing() and not timeline.is_stopped()):
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
    return await _initialize_world_context(world)


async def _initialize_world_context(world):
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


async def initialized_twin_world(factory):
    """Use the twin's CPU scene policy without changing the selected engine.

    Newton otherwise defaults to CUDA even when the twin authors PhysX's
    enableGPUDynamics=false. Its legacy Articulation gains implementation then
    mixes CPU parameters with CUDA storage during reset. Request the device
    through World's supported constructor, before physics initialization.
    An existing World ignores constructor arguments: refuse that mismatch
    instead of silently reconfiguring an already initialized user's context.
    """
    existing = factory.instance()
    if existing is None:
        world = extension_world(lambda: factory(device="cpu"))
        # device reads SimulationManager's current device, not this new World's
        # requested constructor value. Before async initialization it can still
        # report the previous/default CUDA device. Check the applied device after.
        world = await _initialize_world_context(world)
        if str(world.device) != "cpu":
            raise RuntimeError(
                "The new UR5e World did not apply its requested CPU device; "
                f"observed {world.device!s}. Inspect the partially initialized "
                "scene before retrying. No engine was switched."
            )
        return world
    if str(existing.device) != "cpu":
        raise RuntimeError(
            "The UR5e twin requires a CPU simulation World. The existing World "
            f"uses {existing.device!s} and was not reconfigured. Use a disposable "
            "new_stage before Quick Start, or keep this stage for generic tools."
        )
    try:
        context = existing.get_physics_context()
    except RuntimeError as exc:
        if "Physics context is not initialized" not in str(exc):
            raise
        context = None
    if context is None:
        raise RuntimeError(
            "The existing World is not initialized; its pending device request "
            "cannot be verified and was not reconfigured. Use a disposable "
            "new_stage before Quick Start."
        )
    return existing

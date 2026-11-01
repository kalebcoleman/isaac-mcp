"""Bounded UR-only motion check, started by bridge_async_check.py.

The synchronous bridge exec must return before Kit advances another frame.
Only start_check() schedules work; importing this file is CPU-only.
"""
import asyncio
import math
import sys
import time


async def measure_motion(context, timeline, app, articulation_type, action_type,
                         events, np, state, *, orchestrator=None,
                         clock=time.monotonic, timeout_s=90.0, max_frames=12000):
    stage = context.get_stage()
    prim = stage.GetPrimAtPath('/World/UR5e') if stage else None
    deadline = clock() + timeout_s
    frames = 0
    art = None
    owns_play = False
    control_lost = False
    subscription = None

    def same_target():
        return (stage is not None and context.get_stage() == stage and prim
                and stage.GetPrimAtPath('/World/UR5e') == prim)

    def guard(playing):
        if not same_target():
            raise RuntimeError('stage or UR5e changed during motion verification')
        if state.get('cancel_requested'):
            raise asyncio.CancelledError()
        if clock() >= deadline or frames >= max_frames:
            raise TimeoutError('motion verification exceeded its time/frame bound')
        if control_lost or (playing and not timeline.is_playing()):
            raise RuntimeError('timeline control changed during motion verification')
        if not playing and not timeline.is_stopped():
            raise RuntimeError('motion verification requires a stopped timeline')
        if art is not None and not np.all(np.isfinite(art.get_joint_positions())):
            raise RuntimeError('non-finite runtime joint positions')

    async def tick(playing):
        nonlocal frames
        guard(playing)
        await app.next_update_async()
        frames += 1
        state['frames'] = frames
        guard(playing)

    def on_timeline(event):
        nonlocal control_lost
        if owns_play and event.type in (int(events.STOP), int(events.PAUSE)):
            control_lost = True

    try:
        guard(False)
        # Passive drain: a previous STOPPING -> STOPPED can stop a newer Play
        # on 6.0.0.0. Do not start/stop Replicator or mutate its settings.
        settle_deadline = clock() + 5.0
        stable = 0
        while stable < 2:
            await tick(False)
            if clock() >= settle_deadline:
                raise TimeoutError('previous native Stop has not settled')
            status = orchestrator.get_status().name if orchestrator else 'STOPPED'
            if status not in ('STOPPING', 'STOPPED'):
                raise RuntimeError(f'native orchestrator is still {status}')
            stable = stable + 1 if status == 'STOPPED' else 0

        subscription = timeline.get_timeline_event_stream().create_subscription_to_pop(
            on_timeline, name='UR5e motion verification')
        timeline.play()
        owns_play = True
        # Play is committed by the next Kit update on some builds. Check the
        # resulting state after that update, not the still-stopped request state.
        await app.next_update_async()
        frames += 1
        state['frames'] = frames
        guard(True)
        for _ in range(9):
            await tick(True)
        art = articulation_type('/World/UR5e', name=f'verify_motion_{time.time_ns()}')
        art.initialize()
        guard(True)
        joint = art.dof_names.index('elbow_joint')
        settled = art.get_joint_positions()[0].copy()

        async def run_to(seconds, target):
            start = timeline.get_current_time()
            while True:
                guard(True)
                now = timeline.get_current_time()
                if not math.isfinite(now) or now < start:
                    raise RuntimeError('simulation clock reset or became non-finite')
                if now - start >= seconds:
                    return
                art.apply_action(action_type(joint_positions=np.array([target])))
                await tick(True)

        await run_to(1.0, settled)
        # Negative elbow motion lifts the forearm away from the tabletop.
        moved = settled.copy()
        moved[joint] -= 0.1
        await run_to(3.0, moved)
        achieved = art.get_joint_positions()[0].copy()
        await run_to(3.0, settled)
        returned = art.get_joint_positions()[0].copy()
        state.update(state='completed', result={
            'joint': 'elbow_joint', 'commanded_rad': -0.1,
            'achieved_rad': float(achieved[joint] - settled[joint]),
            'returned_error_rad': float(returned[joint] - settled[joint]),
            'all_joints_finite': bool(np.all(np.isfinite(returned))
                                      and np.all(np.isfinite(achieved))),
        })
    except asyncio.CancelledError:
        state.update(state='cancelled', error='motion verification cancelled')
    except Exception as error:
        state.update(state='failed', error=f'{type(error).__name__}: {error}')
    finally:
        # Never pause a replacement stage or a timeline another actor took over.
        if owns_play and not control_lost and same_target():
            try:
                timeline.pause()
            except Exception as error:
                state.update(state='failed', error=f'pause cleanup failed: {error}')
        subscription = None


def start_check():
    import numpy as np
    import omni.kit.app
    import omni.timeline
    import omni.usd
    from omni.kit.async_engine import run_coroutine
    from isaacsim.core.prims import Articulation
    from isaacsim.core.utils.types import ArticulationActions

    # Non-underscore names intentionally survive run_python's session policy.
    global check_state, check_task
    check_state = {'state': 'running', 'frames': 0}
    check_task = run_coroutine(measure_motion(
        omni.usd.get_context(), omni.timeline.get_timeline_interface(),
        omni.kit.app.get_app(), Articulation, ArticulationActions,
        omni.timeline.TimelineEventType, np, check_state,
        orchestrator=getattr(sys.modules.get('omni.replicator.core'), 'orchestrator', None)))

"""The live verifier must yield to Kit, observe its result, and stop on stale state."""
import asyncio
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest


@pytest.fixture
def anyio_backend():
    return 'asyncio'


np = pytest.importorskip('numpy')
ROOT = Path(__file__).resolve().parents[2]
motion = runpy.run_path(str(ROOT / 'scripts/checks/single_joint_motion.py'))
runner = runpy.run_path(str(ROOT / 'scripts/bridge_async_check.py'))
validator = runpy.run_path(str(ROOT / 'scripts/validate_ur5e_check.py'))['validate']


class World:
    def __init__(self):
        self.prim = object()
        self.stage = SimpleNamespace(GetPrimAtPath=lambda path: self.prim)
        self.frames = 0
        self.sim_time = 0.0
        self.playing = False
        self.stopped = True
        self.positions = np.zeros((1, 6))
        self.callback = lambda event: None
        self.after_frame = lambda: None
        self.freeze_time = False
        self.defer_play = False
        self.pending_play = False
        self.plays = []
        self.pauses = 0
        self.timeline = SimpleNamespace(
            is_playing=lambda: self.playing, is_stopped=lambda: self.stopped,
            play=self.play, pause=self.pause, get_current_time=lambda: self.sim_time,
            get_timeline_event_stream=lambda: SimpleNamespace(
                create_subscription_to_pop=self.subscribe))
        self.context = SimpleNamespace(get_stage=lambda: self.stage)
        self.app = SimpleNamespace(next_update_async=self.update)
        self.art = SimpleNamespace(
            initialize=lambda: None, dof_names=['elbow_joint'],
            get_joint_positions=lambda: self.positions.copy(),
            apply_action=self.apply)

    def subscribe(self, callback, **kwargs):
        self.callback = callback
        return object()

    def play(self):
        if self.defer_play:
            self.pending_play = True
        else:
            self.playing, self.stopped = True, False
        self.plays.append(self.frames)

    def pause(self):
        self.pending_play = False
        self.playing, self.stopped = False, False
        self.pauses += 1
        self.callback(SimpleNamespace(type=2))

    def apply(self, action):
        self.positions = action.joint_positions.copy()

    async def update(self):
        await asyncio.sleep(0)
        self.frames += 1
        if self.pending_play:
            self.playing, self.stopped = True, False
            self.pending_play = False
        if self.playing and not self.freeze_time:
            self.sim_time += 0.1
        self.after_frame()

    async def run(self, **kwargs):
        state = {'state': 'running'}
        await motion['measure_motion'](
            self.context, self.timeline, self.app, lambda *a, **kw: self.art,
            SimpleNamespace, SimpleNamespace(STOP=1, PAUSE=2), np, state,
            clock=lambda: self.frames * 0.01, **kwargs)
        return state


@pytest.mark.anyio
async def test_async_motion_measures_and_returns_through_unchanged_validator():
    world = World()
    state = await world.run()
    assert state['state'] == 'completed'
    assert validator('single_joint_motion', {'result': state['result']}) == []
    assert world.pauses == 1 and not world.playing
    assert world.frames >= 80  # two settled frames, ten warmup, seven seconds


@pytest.mark.anyio
async def test_async_motion_passively_drains_previous_stop_before_single_play():
    world = World()
    orchestrator = SimpleNamespace(get_status=lambda: SimpleNamespace(
        name='STOPPING' if world.frames < 4 else 'STOPPED'))
    state = await world.run(orchestrator=orchestrator)
    assert state['state'] == 'completed'
    assert world.plays == [5]


@pytest.mark.anyio
async def test_async_motion_waits_for_requested_play_to_commit():
    world = World()
    world.defer_play = True
    state = await world.run()
    assert state['state'] == 'completed'
    assert world.plays == [2]
    assert world.pauses == 1 and not world.pending_play


@pytest.mark.anyio
@pytest.mark.parametrize('fault', ['stage', 'prim', 'nonfinite', 'timeline', 'clock', 'frames'])
async def test_async_motion_refuses_changed_or_stalled_state_without_false_success(fault):
    world = World()

    def change():
        if world.frames != 20:
            return
        if fault == 'stage':
            world.stage = SimpleNamespace(GetPrimAtPath=lambda path: object())
        elif fault == 'prim':
            world.prim = object()
        elif fault == 'nonfinite':
            world.positions[0, 0] = np.nan
        elif fault == 'timeline':
            # Stop/Play between frame observations must not restore our ownership.
            world.callback(SimpleNamespace(type=1))
        elif fault == 'clock':
            world.sim_time = -1
        else:
            world.freeze_time = True
    world.after_frame = change
    state = await world.run(max_frames=80)
    assert state['state'] == 'failed', state
    assert 'result' not in state
    if fault in ('stage', 'prim', 'timeline'):
        assert world.pauses == 0
    else:
        assert world.pauses == 1


@pytest.mark.anyio
async def test_async_motion_wall_deadline_and_preexisting_play_are_refused():
    world = World()
    state = await world.run(timeout_s=0.13)
    assert state['state'] == 'failed' and 'time/frame bound' in state['error']
    world = World()
    world.play()
    state = await world.run()
    assert state['state'] == 'failed' and 'stopped timeline' in state['error']
    assert world.pauses == 0


@pytest.mark.anyio
async def test_async_motion_cancel_does_not_leave_owned_play_running():
    world = World()
    state = {'state': 'running'}
    world.after_frame = lambda: state.update(cancel_requested=True) if world.frames == 15 else None
    await motion['measure_motion'](
        world.context, world.timeline, world.app, lambda *a, **kw: world.art,
        SimpleNamespace, SimpleNamespace(STOP=1, PAUSE=2), np, state)
    assert state['state'] == 'cancelled'
    assert world.pauses == 1


class Client:
    def __init__(self, states):
        self.states = iter(states)
        self.calls = []

    def call(self, tool, params, **kwargs):
        self.calls.append(params)
        assert tool == 'run_python' and params['persistent'] is True
        if params['code'] == runner['POLL']:
            state = next(self.states)
            if isinstance(state, Exception):
                raise state
            return {'result': state}
        return {'result': {'state': 'started'}}


def test_host_polling_returns_final_measurements_not_started_string():
    client = Client([{'state': 'running'}, {'state': 'completed', 'result': {'joint': 'elbow_joint'}, 'frames': 90}])
    response = runner['run_check'](client, 'code', sleep=lambda _: None)
    assert response['result'] == {'joint': 'elbow_joint'}
    assert response['async_check']['frames'] == 90
    assert client.calls[-1]['code'] == runner['CLEANUP']
    assert len({c['session_id'] for c in client.calls}) == 1


@pytest.mark.parametrize('state', [
    {'state': 'failed', 'error': 'stage changed'},
    {'state': 'cancelled', 'error': 'cancelled'},
    {'state': 'completed'}, {'state': 'started'},
    RuntimeError('persistent namespace cleared'),
])
def test_host_errors_cancel_instead_of_returning_success(state):
    client = Client([state])
    with pytest.raises(RuntimeError):
        runner['run_check'](client, 'code', sleep=lambda _: None)
    assert client.calls[-1]['code'] == runner['CANCEL']


def test_host_deadline_cancels_task_and_next_run_has_unique_namespace():
    client = Client([])
    for _ in range(2):
        times = iter([0.0, 2.0])
        with pytest.raises(TimeoutError):
            runner['run_check'](client, 'code', clock=lambda: next(times), timeout_s=1.0)
    assert client.calls[0]['session_id'] != client.calls[2]['session_id']
    assert client.calls[-1]['code'] == runner['CANCEL']

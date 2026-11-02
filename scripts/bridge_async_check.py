"""Run an asynchronous verification script through existing run_python.

Requires exclusive ownership of the selected disposable scene. No frame updates
run recursively inside a bridge call. A unique persistent namespace holds the
task/state until completion; stage replacement invalidates that namespace.
"""
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
from isaac_mcp.client import BridgeClient  # noqa: E402

POLL = """
if 'check_state' not in globals() or 'check_task' not in globals():
    raise RuntimeError('verification session disappeared; stage may have changed')
if check_task.done() and check_state['state'] == 'running':
    raise RuntimeError('verification task ended without publishing a result')
result = dict(check_state)
"""
CANCEL = """
if 'check_state' in globals() and isinstance(check_state, dict):
    check_state['cancel_requested'] = True
if 'check_task' in globals() and check_task is not None and not check_task.done():
    check_task.cancel()
result = {'cancel_requested': True}
"""
CLEANUP = """
if 'check_task' in globals() and check_task is not None and not check_task.done():
    raise RuntimeError('cannot clear a running verification task')
check_task = None
check_state = None
result = {'cleared': True}
"""


def run_check(client, code, *, clock=time.monotonic, sleep=time.sleep, timeout_s=95.0):
    session_id = 'ur5e-verify-' + uuid.uuid4().hex

    def execute(source):
        return client.call('run_python', {'code': source, 'persistent': True,
                                         'session_id': session_id}, timeout=5.0)

    complete = False
    try:
        # Scheduling is not completion, and is never returned as a passed check.
        execute(code + "\nstart_check()\nresult = {'state': 'started'}\n")
        deadline = clock() + timeout_s
        while clock() < deadline:
            response = execute(POLL)
            state = response.get('result', {})
            if state.get('state') == 'completed':
                if not isinstance(state.get('result'), dict):
                    raise RuntimeError('motion check completed without measurements')
                execute(CLEANUP)
                complete = True
                return {'result': state['result'], 'async_check': {
                    'session_id': session_id, 'frames': state.get('frames')}}
            if state.get('state') in ('failed', 'cancelled'):
                raise RuntimeError(state.get('error', 'motion verification failed'))
            if state.get('state') != 'running':
                raise RuntimeError(f'invalid verification state: {state!r}')
            sleep(0.1)
        raise TimeoutError('motion verification did not complete within the host deadline')
    finally:
        if not complete:
            # A lost bridge cannot acknowledge cancellation. The coroutine still
            # has its own wall-clock/frame bounds and stage guard in that case.
            try:
                execute(CANCEL)
            except Exception as error:
                print(f'could not acknowledge task cancellation: {error}', file=sys.stderr)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print('usage: bridge_async_check.py CHECK.py', file=sys.stderr)
        return 2
    try:
        code = Path(args[0]).read_text().replace('__ROOT__', str(ROOT))
        with BridgeClient(timeout=5.0) as client:
            result = run_check(client, code)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'error', 'error': f'{type(error).__name__}: {error}'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

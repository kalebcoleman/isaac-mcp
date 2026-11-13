"""Run an Isaac Lab agent script for a fixed number of steps and report what the environment did.

Executed by the Isaac Lab interpreter instead of the stock script, like ``eval_runner.py``:

    python check_runner.py --mode check --steps 60 --output <job>/check_result.json \
        --script <checkout>/scripts/environments/zero_agent.py [script arguments...]

``--mode check`` patches ``gymnasium.make`` so the environment's ``step`` is observed, runs the real
script unchanged, and ends its loop after ``--steps`` steps through the simulation app's
``is_running()``. The result lists what a bad environment gets wrong early: non-finite observations or
rewards, shape and reward statistics, resets in the first steps, step time and GPU memory.
``--mode record`` also wraps the environment in gymnasium's ``RecordVideo`` (``--video-dir``,
``--video-length``) so a headless run leaves an MP4. The statistics are plain Python, so they can be
unit-tested without Isaac Lab.
"""

from __future__ import annotations

import argparse
import math
import os
import runpy
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_runner import _bools, _count, _floats, _prepare, _step_parts, _to_list, _write_atomic  # noqa: E402

EARLY_STEPS = 5
MAX_SAMPLE = 200000


def _flatten(value):
    out = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, (list, tuple)):
            stack.extend(item)
        elif isinstance(item, bool):
            out.append(float(item))
        elif isinstance(item, (int, float)):
            out.append(float(item))
        if len(out) > MAX_SAMPLE:
            break
    return out


def _shape(value):
    shape = getattr(value, "shape", None)
    if shape is not None:
        try:
            return [int(x) for x in shape]
        except (TypeError, ValueError):
            pass
    item = _to_list(value)
    shape = []
    while isinstance(item, (list, tuple)):
        shape.append(len(item))
        if not item:
            break
        item = item[0]
    return shape


def _nonfinite(value) -> int:
    return sum(1 for x in _flatten(_to_list(value)) if math.isnan(x) or math.isinf(x))


class CheckStats:
    """What a short run of an environment looked like. No torch or Isaac Lab needed."""

    def __init__(self, steps: int):
        self.target = max(1, int(steps))
        self.steps = 0
        self.num_envs = 0
        self.obs_shapes: dict = {}
        self.obs_nonfinite = 0
        self.reward_nonfinite = 0
        self.rewards: list[float] = []
        self.first_reward: list[float] = []
        self.last_reward: list[float] = []
        self.terminated = 0
        self.truncated = 0
        self.early_resets = 0
        self.step_s: list[float] = []
        self.action_shape = None

    @property
    def finished(self) -> bool:
        return self.steps >= self.target

    def observe(self, obs, reward, terminated, truncated, step_s: float | None = None) -> None:
        self.steps += 1
        n = _count(reward) if _to_list(reward) is not None else self.num_envs
        rewards = _floats(reward, n)
        self.num_envs = max(self.num_envs, n)
        if isinstance(obs, dict):
            for key, val in obs.items():
                self.obs_shapes.setdefault(str(key), _shape(val))
                self.obs_nonfinite += _nonfinite(val)
        elif obs is not None:
            self.obs_shapes.setdefault("obs", _shape(obs))
            self.obs_nonfinite += _nonfinite(obs)
        for r in rewards:
            if math.isnan(r) or math.isinf(r):
                self.reward_nonfinite += 1
            else:
                self.rewards.append(r)
        if self.steps == 1:
            self.first_reward = rewards
        self.last_reward = rewards
        term = [bool(x) for x in _bools(terminated, n)]
        trunc = [bool(x) for x in _bools(truncated, n)]
        self.terminated += sum(term)
        self.truncated += sum(trunc)
        if self.steps <= EARLY_STEPS:
            self.early_resets += sum(1 for a, b in zip(term, trunc) if a or b)
        if step_s is not None:
            self.step_s.append(step_s)

    def problems(self) -> list[str]:
        out = []
        if self.steps == 0:
            out.append("the environment never stepped")
        if self.obs_nonfinite:
            out.append(f"{self.obs_nonfinite} non-finite observation values (NaN or inf)")
        if self.reward_nonfinite:
            out.append(f"{self.reward_nonfinite} non-finite rewards (NaN or inf)")
        if self.num_envs and self.early_resets >= max(1, self.num_envs * EARLY_STEPS // 2):
            out.append(f"{self.early_resets} episode ends in the first {EARLY_STEPS} steps: the environment resets "
                       "almost at once (bad spawn pose, a termination that fires at reset, or an unstable scene)")
        if self.rewards and all(r == 0.0 for r in self.rewards):
            out.append("every reward was exactly 0 (with the agent's own actions this can be normal, "
                       "but check the reward terms)")
        if self.steps and self.steps < self.target:
            out.append(f"the run ended after {self.steps} of {self.target} steps")
        return out

    def result(self, task: str | None = None, mode: str = "check", extra: dict | None = None,
               error: str | None = None) -> dict:
        r = self.rewards
        problems = self.problems()
        if error:
            problems.append(error)
        data = {
            "kind": "env_check_result",
            "mode": mode,
            "task": task,
            "steps": self.steps,
            "steps_requested": self.target,
            "num_envs": self.num_envs,
            "observation_shapes": self.obs_shapes,
            "action_shape": self.action_shape,
            "reward": {"mean": (sum(r) / len(r)) if r else None, "min": min(r) if r else None,
                       "max": max(r) if r else None,
                       "first_step_mean": (sum(self.first_reward) / len(self.first_reward)) if self.first_reward else None,
                       "last_step_mean": (sum(self.last_reward) / len(self.last_reward)) if self.last_reward else None},
            "non_finite": {"observations": self.obs_nonfinite, "rewards": self.reward_nonfinite},
            "episode_ends": {"terminated": self.terminated, "truncated": self.truncated,
                             "in_first_steps": self.early_resets},
            "step_time_ms": (1000.0 * sum(self.step_s) / len(self.step_s)) if self.step_s else None,
            "problems": problems,
            "ok": not problems,
        }
        if error:
            data["error"] = error
        if extra:
            data.update(extra)
        return data


def gpu_mib_for_pid(pid: int) -> int | None:
    """This process's GPU memory in MiB from nvidia-smi, or None."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[0] == str(pid):
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def build_command(python: str, script: str, script_args: list[str], output: str, mode: str = "check",
                  steps: int = 60, video_dir: str | None = None, video_length: int = 200) -> list[str]:
    """The command ``jobs._start_job`` runs for an env_check or record job."""
    cmd = [str(python), str(Path(__file__).resolve()), "--mode", mode, "--steps", str(int(steps)),
           "--output", str(output)]
    if video_dir:
        cmd += ["--video-dir", str(video_dir), "--video-length", str(int(video_length))]
    return cmd + ["--script", str(script), "--", *[str(a) for a in script_args]]


class CheckStop(BaseException):
    """Raised on a step after the target, if the agent loop ignored every ``is_running()`` check."""


class _Hooks:
    def __init__(self, stats: CheckStats, output: str, mode: str, task: str | None,
                 video_dir: str | None, video_length: int):
        self.stats, self.output, self.mode, self.task = stats, output, mode, task
        self.video_dir, self.video_length = video_dir, video_length
        self.app = None
        self.env = None
        self.t0 = time.time()
        self.failure: str | None = None
        self.last_obs = None

    def publish(self) -> None:
        try:
            extra = {"wall_s": round(time.time() - self.t0, 2)}
            mib = gpu_mib_for_pid(os.getpid())
            if mib is not None:
                extra["gpu_mib"] = mib
                if self.stats.num_envs:
                    extra["gpu_mib_per_env_upper_bound"] = round(mib / self.stats.num_envs, 2)
            if self.video_dir:
                extra["video_dir"] = self.video_dir
            _write_atomic(Path(self.output), self.stats.result(self.task, self.mode, extra, self.failure))
        except Exception as exc:  # noqa: BLE001 - never mask the real run
            print(f"[twinsmith-check] could not write {self.output}: {exc}", file=sys.stderr)

    def wrap_env(self, env) -> None:
        if getattr(env, "_twinsmith_check_wrapped", False):
            return
        self.env = env
        try:
            self.stats.action_shape = _shape(env.action_space.sample()) if hasattr(env, "action_space") else None
        except Exception:  # noqa: BLE001
            pass
        original_step = env.step
        original_close = getattr(env, "close", None)

        sim = getattr(getattr(env, "unwrapped", None), "sim", None)
        sim_running = getattr(sim, "is_running", None)
        if callable(sim_running):  # Lab 3's agents loop on `sim.is_running()`, not the app's
            def running(*args, **kwargs):
                return False if self.stats.finished else bool(sim_running(*args, **kwargs))
            try:
                sim.is_running = running
            except Exception:  # noqa: BLE001
                pass

        def step(action):
            if self.stats.finished:
                raise CheckStop()
            t = time.perf_counter()
            result = original_step(action)
            dt = time.perf_counter() - t
            parts = _step_parts(result)
            if parts is not None:
                reward, terminated, truncated, _info = parts
                obs = result[0] if isinstance(result, (tuple, list)) and result else None
                self.stats.observe(obs, reward, terminated, truncated, dt)
                if self.stats.finished:
                    self.publish()
            return result

        def close(*args, **kwargs):
            self.publish()
            return original_close(*args, **kwargs) if original_close else None

        env.step = step
        if original_close:
            env.close = close
        env._twinsmith_check_wrapped = True

    def adopt_app(self, app) -> None:
        if self.app is not None:
            return
        self.app = app
        original = getattr(app, "is_running", None)
        if callable(original):
            def is_running(*args, **kwargs):
                return False if self.stats.finished else bool(original(*args, **kwargs))
            app.is_running = is_running


def _patch(hooks: _Hooks) -> None:
    import gymnasium

    original = gymnasium.make

    def make(*args, **kwargs):
        if hooks.mode == "record":
            kwargs.setdefault("render_mode", "rgb_array")
        env = original(*args, **kwargs)
        hooks.wrap_env(env)
        if hooks.mode == "record" and hooks.video_dir:
            from gymnasium.wrappers import RecordVideo

            env = RecordVideo(env, video_folder=hooks.video_dir, step_trigger=lambda s: s == 0,
                              video_length=hooks.video_length, disable_logger=True)
        return env

    gymnasium.make = make
    try:
        from isaaclab.app import AppLauncher

        original_init = AppLauncher.__init__

        def __init__(self, *a, **k):
            original_init(self, *a, **k)
            app = getattr(self, "app", None)
            if app is not None:
                hooks.adopt_app(app)

        AppLauncher.__init__ = __init__
    except Exception:  # noqa: BLE001 - the script fails for the same reason if it needs it
        pass


def _parse(argv=None):
    p = argparse.ArgumentParser(description="Run an Isaac Lab script for N steps and report.")
    p.add_argument("--mode", choices=("check", "record"), default="check")
    p.add_argument("--steps", type=int, default=60)
    p.add_argument("--output", required=True)
    p.add_argument("--video-dir")
    p.add_argument("--video-length", type=int, default=200)
    p.add_argument("--script", required=True)
    p.add_argument("script_args", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    if a.steps < 1:
        p.error("--steps must be at least 1")
    return a


def main(argv=None) -> int:
    a = _parse(argv)
    args = list(a.script_args)
    if args and args[0] == "--":
        args = args[1:]
    task = args[args.index("--task") + 1] if "--task" in args and args.index("--task") + 1 < len(args) else None
    steps = a.steps if a.mode == "check" else max(a.steps, a.video_length + 1)
    stats = CheckStats(steps)
    hooks = _Hooks(stats, a.output, a.mode, task, a.video_dir, a.video_length)
    try:
        _prepare()
        _patch(hooks)
        sys.argv = [a.script, *args]
        sys.path.insert(0, os.path.dirname(os.path.abspath(a.script)))
        runpy.run_path(a.script, run_name="__main__")
    except CheckStop:
        pass  # the agent loop stepped past the target; the result is already complete
    except SystemExit as exc:
        if exc.code not in (None, 0):  # Lab's agent scripts end with SystemExit(0), which is a normal exit
            hooks.failure = f"SystemExit: {exc.code}"
            raise
    except BaseException as exc:  # noqa: BLE001 - write what was measured, then exit non-zero
        hooks.failure = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        hooks.publish()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Score an Isaac Lab play.py run without changing Isaac Lab.

Isaac Lab has no eval entry point, and its ``play.py`` plays forever without reporting a return. This
runner is executed by the Isaac Lab interpreter instead of ``play.py``:

    python eval_runner.py --episodes 20 --max-steps 20000 --output <job>/eval_result.json \
        --play <checkout>/scripts/reinforcement_learning/<library>/play.py [play.py arguments...]

It imports the configured task modules, patches ``gymnasium.make`` so the environment it returns has
its ``step`` (and ``reset``/``close``) wrapped, then runs the real ``play.py`` unchanged with
``runpy``. Every environment step sums the reward each parallel environment receives; a terminated or
truncated step closes that environment's episode, recording its undiscounted return and length. Once
the target number of episodes has finished (and, where possible, every environment has finished at
least one), or the step bound is reached, the runner stops the play loop through the simulation
app's ``is_running()``, so ``play.py`` closes the environment and the app itself and exits 0.

The wrapper sits on the object ``gymnasium.make`` returned, which is what the RL libraries'
vector-environment wrappers call in their own ``step``; it therefore counts exactly one step per
simulator step, with the reward and termination values the environment produced. It is stdlib-only
and uses duck typing for torch/numpy/plain values, so it can be unit-tested without Isaac Lab.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import runpy
import sys
import tempfile
from pathlib import Path

_SUCCESS_KEYS = ("success", "is_success", "episode_success")


class EvalStop(BaseException):
    """Raised inside a second post-target step if the play loop ignored ``is_running()``.

    A BaseException so a play script's own ``except Exception`` handler (Lab 3's prints a traceback and exits 0) can't
    swallow it: that made every finished eval read as a crash.
    """


def _to_list(value):
    """A torch tensor on any device, numpy array, sequence or scalar as plain Python."""
    if value is None:
        return None
    for method in ("detach", "cpu"):
        method_fn = getattr(value, method, None)
        if callable(method_fn):
            try:
                value = method_fn()
            except Exception:  # noqa: BLE001 - a duck-typed tensor may refuse one
                pass
    for method in ("tolist", "numpy"):
        method_fn = getattr(value, method, None)
        if callable(method_fn):
            try:
                result = method_fn()
            except Exception:  # noqa: BLE001
                continue
            if method == "numpy":
                inner = getattr(result, "tolist", None)
                if callable(inner):
                    try:
                        return inner()
                    except Exception:  # noqa: BLE001
                        pass
            return result
    return value


def _values(value, n):
    """``value`` as a flat list of n Python items; a scalar is broadcast."""
    item = _to_list(value)
    if item is None:
        return [0] * n
    if isinstance(item, (int, float, bool)):
        return [item] * n
    if isinstance(item, (list, tuple)):
        flat = []
        for entry in item:
            if isinstance(entry, (list, tuple)):
                flat.extend(_to_list(entry))
            else:
                flat.append(_to_list(entry))
        if len(flat) == n:
            return flat
        if len(flat) == 1:
            return flat * n
        return flat[:n] if len(flat) > n else flat + [flat[-1] if flat else 0] * (n - len(flat))
    return [item] * n


def _count(value) -> int:
    """How many per-environment entries a reward/flag value holds (1 for a scalar or a 0-d tensor)."""
    item = _to_list(value)
    if isinstance(item, (list, tuple)):
        return len(item)
    return 1


def _floats(value, n):
    out = []
    for entry in _values(value, n):
        if entry is None:
            out.append(0.0)
            continue
        try:
            out.append(float(entry))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"reward is not a per-environment number ({entry!r}); this environment cannot be scored"
            ) from exc
    return out


def _bools(value, n):
    out = []
    for entry in _values(value, n):
        if entry is None:
            out.append(False)
        elif isinstance(entry, (bool, int, float)):
            out.append(bool(entry))
        else:
            raise RuntimeError(
                f"terminated/truncated is not a per-environment flag ({entry!r}); this environment cannot be scored"
            )
    return out


def _step_parts(result):
    """(reward, terminated, truncated, info) from a gymnasium 5-tuple, else an old 4-tuple."""
    if not isinstance(result, (tuple, list)) or len(result) < 4:
        return None
    if len(result) >= 5:
        return result[1], result[2], result[3], result[4]
    info = result[3]
    if isinstance(info, dict):
        return result[1], result[2], False, info
    return result[1], result[2], result[3], {}


def _success_flags(env, info, n):
    """Per-environment success from the step info or the environment's termination terms, else None."""
    if isinstance(info, dict):
        for key in _SUCCESS_KEYS:
            if key in info:
                flags = _bools(info[key], n)
                if len(flags) == n:
                    return flags
    try:
        unwrapped = getattr(env, "unwrapped", env)
        manager = getattr(unwrapped, "termination_manager", None)
        if manager is not None:
            terms = getattr(manager, "active_terms", None) or []
            for term in terms:
                if "success" in str(term).lower():
                    flags = _bools(manager.get_term(term), n)
                    if len(flags) == n:
                        return flags
    except Exception:  # noqa: BLE001 - success reporting is optional
        return None
    return None


class Scorer:
    """Accumulates per-environment episode returns and decides when enough have finished."""

    def __init__(self, num_episodes: int = 20, max_steps: int | None = 20000):
        self.target = int(num_episodes)
        self.max_steps = int(max_steps) if max_steps else None
        self.steps = 0
        self.num_envs = 0
        self.returns: list[float] = []
        self.lengths: list[int] = []
        self.finished_per_env: list[int] = []
        self.completed: list[tuple[float, int, bool | None]] = []
        self.finished = False
        self.stopped_by: str | None = None
        self.waited_for_all_envs = True
        self.ended_early = 0  # episodes ended by a termination, not by running out of time
        self.term_sums: dict[str, float] = {}
        self.term_counts: dict[str, int] = {}

    def reset_envs(self) -> None:
        """Forget partial episodes: an explicit reset starts new, uncounted ones."""
        self.returns = [0.0] * self.num_envs
        self.lengths = [0] * self.num_envs
        self.waited_for_all_envs = True

    def _ensure(self, n: int) -> None:
        if n > self.num_envs:
            self.returns.extend([0.0] * (n - self.num_envs))
            self.lengths.extend([0] * (n - self.num_envs))
            self.finished_per_env.extend([0] * (n - self.num_envs))
            self.num_envs = n

    def all_envs_finished(self) -> bool:
        return self.num_envs > 0 and all(count > 0 for count in self.finished_per_env[: self.num_envs])

    def observe(self, reward, terminated, truncated, info=None, env=None) -> None:
        if self.finished:
            return
        n = max(self.num_envs, _count(reward), _count(terminated), _count(truncated))
        self._ensure(n)
        rewards = _floats(reward, n)
        terms = _bools(terminated, n)
        truncs = _bools(truncated, n)
        success = _success_flags(env, info, n) if any(terms) or any(truncs) else None
        self.steps += 1
        self._observe_terms(info)
        for index in range(n):
            self.returns[index] += rewards[index]
            self.lengths[index] += 1
            if terms[index] or truncs[index]:
                self.ended_early += 1 if terms[index] and not truncs[index] else 0
                ok = success[index] if success is not None and index < len(success) else None
                self.completed.append((self.returns[index], self.lengths[index], ok))
                self.finished_per_env[index] += 1
                self.returns[index] = 0.0
                self.lengths[index] = 0

    def _observe_terms(self, info) -> None:
        """Isaac Lab reports each reward and termination term, per episode, in info["log"] as environments reset."""
        log = info.get("log") if isinstance(info, dict) else None
        if not isinstance(log, dict):
            return
        for key, value in log.items():
            if not str(key).startswith(("Episode_Reward/", "Episode_Termination/")):
                continue
            numbers = _floats(value, 1)
            if numbers and math.isfinite(numbers[0]):
                self.term_sums[key] = self.term_sums.get(key, 0.0) + numbers[0]
                self.term_counts[key] = self.term_counts.get(key, 0) + 1

    def decide(self) -> str | None:
        """The reason the run should stop, or None."""
        if self.finished:
            return self.stopped_by
        self.waited_for_all_envs = self.all_envs_finished()
        if self.target and len(self.completed) >= self.target and self.waited_for_all_envs:
            return "episodes"
        if self.max_steps and self.steps >= self.max_steps:
            return "max_steps"
        return None

    def stop(self, reason: str) -> None:
        if not self.finished:
            self.stopped_by = reason
            self.finished = True
            if reason == "max_steps":
                self.waited_for_all_envs = self.all_envs_finished()

    def result(self, note: str | None = None, error: str | None = None) -> dict:
        returns = [entry[0] for entry in self.completed]
        lengths = [entry[1] for entry in self.completed]
        successes = [entry[2] for entry in self.completed if entry[2] is not None]
        data: dict = {"episodes": len(returns)}
        if returns:
            mean = sum(returns) / len(returns)
            variance = sum((value - mean) ** 2 for value in returns) / len(returns)
            data.update(
                mean_return=mean,
                std_return=math.sqrt(variance),
                min_return=min(returns),
                max_return=max(returns),
                mean_length=sum(lengths) / len(lengths),
            )
        else:
            data.update(mean_return=None, std_return=None, min_return=None, max_return=None, mean_length=None)
        data["steps"] = self.steps
        if successes:
            data["success_rate"] = sum(1 for value in successes if value) / len(successes)
        if returns:
            data["ended_early"] = self.ended_early
            data["survived_to_time_limit"] = 1.0 - self.ended_early / len(returns)
        terms = {key: self.term_sums[key] / self.term_counts[key] for key in sorted(self.term_sums)}
        rewards = {key.split("/", 1)[1]: value for key, value in terms.items() if key.startswith("Episode_Reward/")}
        endings = {key.split("/", 1)[1]: value for key, value in terms.items() if key.startswith("Episode_Termination/")}
        if rewards:
            data["reward_terms"] = rewards
        if endings:
            data["termination_terms"] = endings
        data["stopped_by"] = self.stopped_by or "play_exited"
        data["num_envs"] = self.num_envs
        data["num_episodes_requested"] = self.target
        data["max_steps"] = self.max_steps
        if error:
            data["error"] = error
        data["note"] = note or self._default_note()
        return data

    def _default_note(self) -> str:
        if not self.completed:
            if self.steps == 0:
                return ("No environment step was observed: this play script may not build its environment with "
                        "gymnasium.make, so there is no score.")
            return (f"No episode finished within {self.steps} environment steps, so there is no score. The episodes "
                    "still running at the end are not estimated or counted.")
        detail = ("Only complete episodes count: an episode's return sums the reward it received from reset to its "
                  "terminated or truncated step, and its length is that many environment steps.")
        if self.target and len(self.completed) < self.target and self.stopped_by == "max_steps":
            detail += (f" The step bound stopped the run after {len(self.completed)} of the requested "
                       f"{self.target} episodes.")
        elif self.waited_for_all_envs and self.num_envs > 1:
            detail += " All parallel environments contributed at least one finished episode."
        return detail


def _write_atomic(path: Path, data: dict) -> None:
    """Publish one whole JSON snapshot; a reader sees the old file or the new one, never a partial one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, sort_keys=False, default=str)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_command(python: str, play_script: str, play_args: list[str], output: str,
                  num_episodes: int = 20, max_steps: int | None = 20000) -> list[str]:
    """The command ``jobs._start_job`` runs for an eval job."""
    return [
        str(python), str(Path(__file__).resolve()),
        "--episodes", str(int(num_episodes)),
        "--max-steps", str(int(max_steps) if max_steps else 0),
        "--output", str(output),
        "--play", str(play_script),
        "--",
        *[str(arg) for arg in play_args],
    ]


class _Hooks:
    """State for the patched environment, launcher and simulation app."""

    def __init__(self, scorer: Scorer, output: str | None = None):
        self.scorer = scorer
        self.output = Path(output) if output else None
        self.env = None
        self.app = None
        self.env_closed = False
        self.app_closed = False
        self.failure: str | None = None

    def publish(self) -> None:
        """Write the result now. Isaac Sim's close can end the process without running our ``finally``."""
        if self.output is None:
            return
        try:
            _write_atomic(self.output, self.scorer.result(note=self._note(), error=self.failure))
        except Exception as exc:  # noqa: BLE001 - never mask the real run
            print(f"[isaac-mcp-eval] could not write {self.output}: {exc}", file=sys.stderr)

    def _note(self) -> str | None:
        if self.failure and self.scorer.steps and self.scorer.completed:
            return ("The run ended with an error after the episodes above; those returns are real, but the run did "
                    "not finish normally.")
        if self.failure:
            return "The run ended with an error before scoring anything, so there is no score."
        return self.scorer._default_note()

    def wrap_env(self, env) -> None:
        if getattr(env, "_isaac_mcp_eval_wrapped", False):
            return
        self.env = env
        original_step = env.step
        original_reset = getattr(env, "reset", None)
        original_close = getattr(env, "close", None)

        def step(action):
            if self.scorer.finished:
                raise EvalStop()
            result = original_step(action)
            parts = _step_parts(result)
            if parts is None:
                raise RuntimeError("the environment step returned no reward; this play script can't be scored")
            reward, terminated, truncated, info = parts
            self.scorer.observe(reward, terminated, truncated, info, env=env)
            reason = self.scorer.decide()
            if reason:
                self.scorer.stop(reason)
                self.publish()
            return result

        def reset(*args, **kwargs):
            self.scorer.reset_envs()
            return original_reset(*args, **kwargs)

        def close(*args, **kwargs):
            self.env_closed = True
            self.publish()
            if original_close is not None:
                return original_close(*args, **kwargs)
            return None

        env.step = step
        if original_reset is not None:
            env.reset = reset
        if original_close is not None:
            env.close = close
        env._isaac_mcp_eval_wrapped = True

    def adopt_app(self, app) -> None:
        """Stop the play loop at the next condition check, and remember the app for a clean close."""
        if self.app is not None:
            return
        self.app = app
        original_running = getattr(app, "is_running", None)
        original_close = getattr(app, "close", None)

        if callable(original_running):
            def is_running(*args, **kwargs):
                if self.scorer.finished:
                    return False
                return bool(original_running(*args, **kwargs))

            app.is_running = is_running
        if callable(original_close):
            def close(*args, **kwargs):
                self.app_closed = True
                self.publish()
                return original_close(*args, **kwargs)

            app.close = close

    def close(self) -> None:
        if self.env is not None and not self.env_closed:
            try:
                self.env.close()
            except Exception:  # noqa: BLE001 - best effort during shutdown
                pass
        if self.app is not None and not self.app_closed:
            try:
                self.app.close()
            except Exception:  # noqa: BLE001
                pass


def _patch_gymnasium(hooks: _Hooks) -> None:
    import gymnasium

    original = gymnasium.make
    if getattr(original, "_isaac_mcp_eval", False):
        return

    def make(*args, **kwargs):
        env = original(*args, **kwargs)
        hooks.wrap_env(env)
        return env

    make._isaac_mcp_eval = True
    gymnasium.make = make


def _patch_app_launcher(hooks: _Hooks) -> None:
    """Capture the SimulationApp that play.py creates, before it imports it."""
    from isaaclab.app import AppLauncher

    original = AppLauncher.__init__
    if getattr(original, "_isaac_mcp_eval", False):
        return

    def __init__(self, *args, **kwargs):
        original(self, *args, **kwargs)
        app = getattr(self, "app", None)
        if app is not None:
            hooks.adopt_app(app)

    __init__._isaac_mcp_eval = True
    AppLauncher.__init__ = __init__


def _prepare() -> None:
    """The same pre-imports launcher.py does, so a project's tasks are registered before lookup."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        early = int(version("isaacsim").split(".")[0]) >= 6
    except (PackageNotFoundError, ValueError):
        early = True
    if early:  # on Sim 5.1 the environment's newer pip Warp, imported first, breaks Kit's extensions
        try:
            import warp as wp

            wp.config.enable_backward = False
        except ImportError:
            pass
    for name in (entry.strip() for entry in os.environ.get("ISAACLAB_TASK_MODULES", "").split(",")):
        if name:
            __import__(name)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score an Isaac Lab play.py run.")
    parser.add_argument("--episodes", type=int, default=20, help="Finished episodes to collect.")
    parser.add_argument("--max-steps", type=int, default=20000, help="Environment-step bound; 0 means none.")
    parser.add_argument("--output", required=True, help="Where to write eval_result.json.")
    parser.add_argument("--play", required=True, help="The real play.py to run.")
    parser.add_argument("play_args", nargs=argparse.REMAINDER, help="Arguments for play.py.")
    args = parser.parse_args(argv)
    if args.episodes < 1:
        parser.error("--episodes must be at least 1")
    if args.max_steps < 0:
        parser.error("--max-steps cannot be negative")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    play_args = list(args.play_args)
    if play_args and play_args[0] == "--":
        play_args = play_args[1:]
    scorer = Scorer(num_episodes=args.episodes, max_steps=args.max_steps or None)
    hooks = _Hooks(scorer, args.output)
    failure = None
    try:
        _prepare()
        try:
            _patch_gymnasium(hooks)
        except Exception as exc:  # noqa: BLE001 - report clearly instead of inventing a score
            raise RuntimeError(f"gymnasium.make could not be patched, so this run cannot be scored: {exc}") from exc
        try:
            _patch_app_launcher(hooks)
        except Exception:  # noqa: BLE001 - play.py will fail for the same reason if it needs it
            pass
        sys.argv = [args.play, *play_args]
        sys.path.insert(0, os.path.dirname(os.path.abspath(args.play)))
        runpy.run_path(args.play, run_name="__main__")
    except EvalStop:
        pass  # the play loop stepped again after the target; the result is already complete
    except BaseException as exc:  # noqa: BLE001 - write what was measured, then exit non-zero
        failure = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        hooks.failure = failure
        if failure and not scorer.finished:
            scorer.stop("error")
        elif not scorer.finished:
            scorer.stop("play_exited")
        hooks.close()
        hooks.publish()
    return 0


if __name__ == "__main__":
    sys.exit(main())

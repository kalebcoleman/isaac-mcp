"""Register the Isaac Lab tool pack on the host FastMCP server.

These are host-side tools (like the screen tools): they never need a Kit process. Each returns a JSON
string; failures come back as ``{"error": ...}`` rather than exceptions so a client can read and retry.
Design: docs/ISAACLAB-TOOLS.md.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Callable, Iterable

from pydantic import BeforeValidator, Field

from ..local_tools import DESTRUCTIVE, IDEMPOTENT_WRITE, PYTHON_EXECUTION, READ_ONLY, ScopedTools
from . import install as _install
from . import jobs as _jobs
from . import metrics as _metrics

_NO_INSTALL = {
    "error": "no Isaac Lab install found",
    "hint": "set ISAACLAB_PYTHON (and optionally ISAACLAB_ROOT) to the interpreter of an Isaac Lab env (3.0, or 2.3.x with Isaac Sim 5.1)",
}


def _not_a_bool(value: Any) -> Any:
    """Pydantic would turn ``true`` into 1 before the check in metrics.tensorboard_tail could see it."""
    if isinstance(value, bool):
        raise ValueError("last_n must be an integer (not a boolean) or null")
    return value


_LastN = Annotated[int | None, BeforeValidator(_not_a_bool), Field(description="How many of the newest points to return.")]
_Task = Annotated[str, Field(description="Task id from isaaclab__envs_list, e.g. Isaac-Cartpole on Lab 3, Isaac-Cartpole-v0 on Lab 2.3.")]
_Checkpoint = Annotated[str, Field(description="Checkpoint file from isaaclab__checkpoints_list.")]
_JobId = Annotated[str, Field(description="job_id from isaaclab__train, eval or play, or isaaclab__runs_list.")]
_RunDir = Annotated[str, Field(description="Run folder, as shown by isaaclab__checkpoints_list or isaaclab__runs_status.")]
_Headless = Annotated[bool, Field(description="false opens a window. true with extra_args=['--video'] keeps a renderer with no window.")]
_RlLibrary = Annotated[str | None, Field(description="RL library, e.g. rsl_rl. Required on Lab 2.x, which has one script folder per library; install_info's rl_libraries lists them. Lab 3 has a default.")]
_ExtraArgs = Annotated[list[str] | None, Field(description="More command-line arguments for the Isaac Lab script, e.g. ['--video'].")]
_Timeout = Annotated[float | None, Field(description="Stop the job after this many seconds. Default: no limit.")]
_AllowConcurrent = Annotated[bool, Field(description="Start even if another job is running; a 16 GB GPU normally fits one.")]
_AllowGpu = Annotated[bool, Field(description="Start even if an Isaac Sim (Kit bridge) app is using the GPU.")]


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


def _guard(fn: Callable[[], Any]) -> str:
    try:
        return _dump(fn())
    except (ValueError, OSError) as exc:
        return _dump({"error": str(exc)})


_WINDOW_HINT = (
    "A window opens within ~30 s but the scene is empty/black until loading finishes. Poll "
    "isaaclab__runs_status(job_id) until it reports `window`, and isaaclab__runs_tail(job_id, lines=200) until it contains "
    "'Completed setting up the environment' (later lines can push it out of last_log_lines), wait ~5 s, then screenshot(window_id=...) or a short "
    "start_screen_recording(window_id=...) to inspect the result, and isaaclab__runs_cancel when done."
)


def _launch(fn: Callable[..., Any], **kwargs: Any) -> str:
    inst = _install.locate_install()
    if not inst:
        return _dump(_NO_INSTALL)
    if not inst.get("isaaclab_root"):
        return _dump({"error": "Isaac Lab checkout not found",
                      "hint": "set ISAACLAB_ROOT to the Isaac Lab checkout containing scripts/reinforcement_learning"})

    def run() -> dict:
        out = fn(python=inst["python"], root=inst["isaaclab_root"], **kwargs)
        if out.get("error") or not out.get("job_id"):
            return out  # a refusal keeps its own hint, e.g. allow_concurrent=true
        overrides = [arg for arg in kwargs.get("extra_args") or []
                     if isinstance(arg, str) and arg.split('=', 1)[0] in ('--viz', '--visualizer')]
        if overrides:
            out["hint"] = (
                "The explicit visualizer override is forwarded once. headless controls Kit's HEADLESS "
                "setting; non-Kit visualizers use their own Lab configuration. Inspect runs_status/window "
                "and runs_tail for the actual display and startup result."
            )
        elif kwargs.get("headless") is False:
            out["hint"] = _WINDOW_HINT
        return out

    return _guard(run)


def register_isaaclab_tools(server: Any, groups: Iterable[str] | None = None) -> None:
    """Register the Isaac Lab tools whose group is in ``groups`` (default: all of them)."""
    mcp = ScopedTools(server, groups)

    @mcp.tool(name="isaaclab__install_info", annotations=PYTHON_EXECUTION)
    def isaaclab_install_info() -> str:
        """Report the Isaac Lab install: interpreter, versions, root, venv.

        Runs the configured Python interpreter, whose startup code may have side effects."""
        return _dump(_install.install_info())

    @mcp.tool(name="isaaclab__envs_list", annotations=PYTHON_EXECUTION)
    def isaaclab_envs_list(filter: Annotated[str | None, Field(description="Keep task ids containing this text.")] = None) -> str:
        """List registered Isaac Lab task ids (optionally filtered by substring).

        Runs Python and imports task modules, which may have side effects."""
        return _dump(_install.envs_list(filter=filter))

    @mcp.tool(name="isaaclab__train", annotations=PYTHON_EXECUTION)
    def isaaclab_train(
        task: _Task,
        num_envs: Annotated[int, Field(description="Parallel environments.")] = 64,
        max_iterations: Annotated[int | None, Field(description="Training iterations. Default: the task's own.")] = None,
        seed: Annotated[int | None, Field(description="Random seed.")] = None,
        device: Annotated[str | None, Field(description="e.g. cuda:0 or cpu. Default: Isaac Lab's choice.")] = None,
        headless: _Headless = True,
        checkpoint: Annotated[str | None, Field(description="Checkpoint file to resume from, from isaaclab__checkpoints_list.")] = None,
        rl_library: _RlLibrary = None,
        extra_args: _ExtraArgs = None,
        timeout_s: _Timeout = None,
        allow_concurrent: _AllowConcurrent = False,
        allow_concurrent_gpu: _AllowGpu = False,
    ) -> str:
        """Start a training job and return its job_id immediately; follow it with isaaclab__runs_status/tail.

        Refuses while a Kit bridge app is running unless allow_concurrent_gpu=true. Video needs optional
        MoviePy/imageio-ffmpeg in the Lab interpreter.
        """
        return _launch(
            _jobs.train,
            task=task, num_envs=num_envs, max_iterations=max_iterations, seed=seed, device=device,
            headless=headless, checkpoint=checkpoint, rl_library=rl_library, extra_args=extra_args,
            timeout_s=timeout_s, allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
        )

    @mcp.tool(name="isaaclab__eval", annotations=PYTHON_EXECUTION)
    def isaaclab_eval(
        task: _Task,
        checkpoint: _Checkpoint,
        num_envs: Annotated[int | None, Field(description="Parallel environments. Default: the task's own.")] = None,
        headless: _Headless = True,
        rl_library: _RlLibrary = None,
        extra_args: _ExtraArgs = None,
        num_episodes: Annotated[int, Field(description="Finished episodes to collect before the run stops itself.")] = 20,
        max_steps: Annotated[int | None, Field(description="Environment-step bound that also ends the run; null removes it.")] = 20000,
        timeout_s: _Timeout = None,
        allow_concurrent: _AllowConcurrent = False,
        allow_concurrent_gpu: _AllowGpu = False,
    ) -> str:
        """Run a checkpoint as a job and score it. Returns its job_id; when the job ends, isaaclab__runs_status
        reports `eval_result`: episodes, mean/std/min/max return, mean length, steps, and success_rate if the
        task reports one. It counts complete episodes, stops after num_episodes (waiting for each environment's
        first finished episode where possible) or max_steps, then exits by itself. Without a finished episode it
        reports no return, never a made-up score.

        Refuses while a Kit bridge app is running unless allow_concurrent_gpu=true. Video needs optional
        MoviePy/imageio-ffmpeg in the Lab interpreter.
        """
        return _launch(
            _jobs.eval_,
            task=task, checkpoint=checkpoint, num_envs=num_envs, headless=headless,
            rl_library=rl_library, extra_args=extra_args, num_episodes=num_episodes, max_steps=max_steps,
            timeout_s=timeout_s, allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
        )

    @mcp.tool(name="isaaclab__play", annotations=PYTHON_EXECUTION)
    def isaaclab_play(
        task: _Task,
        checkpoint: _Checkpoint,
        num_envs: Annotated[int, Field(description="Parallel environments.")] = 1,
        headless: _Headless = False,
        rl_library: _RlLibrary = None,
        extra_args: _ExtraArgs = None,
        timeout_s: _Timeout = None,
        allow_concurrent: _AllowConcurrent = False,
        allow_concurrent_gpu: _AllowGpu = False,
    ) -> str:
        """Play a checkpoint with a window (interactive); stop it with isaaclab__runs_cancel.

        Refuses while a Kit bridge app is running unless allow_concurrent_gpu=true. Video needs optional
        MoviePy/imageio-ffmpeg in the Lab interpreter.
        """
        return _launch(
            _jobs.play,
            task=task, checkpoint=checkpoint, num_envs=num_envs, headless=headless,
            rl_library=rl_library, extra_args=extra_args, timeout_s=timeout_s,
            allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
        )

    @mcp.tool(name="isaaclab__runs_list", annotations=DESTRUCTIVE)
    def isaaclab_runs_list(state: Annotated[str | None, Field(description="running or finished. Default: all.")] = None) -> str:
        """List Isaac Lab jobs, to find a job_id.

        Reconciles orphaned jobs by updating their stored metadata."""
        return _guard(lambda: _jobs.runs_list(state=state))

    @mcp.tool(name="isaaclab__runs_status", annotations=DESTRUCTIVE)
    def isaaclab_runs_status(job_id: _JobId) -> str:
        """State, exit code, elapsed time, last log lines, newest checkpoint and, for an eval job, its
        `eval_result` score once written. For a windowed job also `window` (id, title, size): pass its id to
        screenshot(window_id=...) or start_screen_recording(window_id=...) to see the result. Reconciles
        orphaned jobs by updating their stored metadata."""
        def status() -> dict:
            st = _jobs.runs_status(job_id)
            if st.get("pid_alive"):
                from ..screen import window_for_pid

                w = window_for_pid(st.get("pid"))
                if w:
                    st["window"] = {k: w[k] for k in ("id", "title", "width", "height")}
            return st

        return _guard(status)

    @mcp.tool(name="isaaclab__runs_tail", annotations=IDEMPOTENT_WRITE)
    def isaaclab_runs_tail(job_id: _JobId, stream: Annotated[str, Field(description="stdout or stderr.")] = "stdout",
                           lines: Annotated[int, Field(description="How many of the last lines to return.")] = 100) -> str:
        """Last lines of a job's stdout or stderr log. Creates the jobs directory if missing."""
        return _guard(lambda: _jobs.runs_tail(job_id, stream=stream, lines=lines))

    @mcp.tool(name="isaaclab__runs_cancel", annotations=DESTRUCTIVE)
    def isaaclab_runs_cancel(job_id: _JobId, grace_s: Annotated[int, Field(description="Seconds between SIGTERM and SIGKILL.")] = 10) -> str:
        """Stop a job: SIGTERM to its process group, SIGKILL after grace_s seconds."""
        return _guard(lambda: _jobs.runs_cancel(job_id, grace_s=grace_s))

    @mcp.tool(name="isaaclab__checkpoints_list", annotations=READ_ONLY)
    def isaaclab_checkpoints_list(run_dir: Annotated[str | None, Field(description="Run folder. Default: search the configured logs.")] = None,
                                  task: Annotated[str | None, Field(description="Keep checkpoints whose path contains this text. Paths use the experiment name, such as cartpole, not the task id.")] = None) -> str:
        """List policy checkpoints (*.pt / *.pth, and stable-baselines3's .zip), newest first, under a run dir or the configured logs."""
        return _guard(lambda: _metrics.checkpoints_list(run_dir=run_dir, task=task))

    @mcp.tool(name="isaaclab__metrics_summary", annotations=READ_ONLY)
    def isaaclab_metrics_summary(run_dir: _RunDir, tags: Annotated[list[str] | None, Field(description="Scalar tags to include. Default: all.")] = None) -> str:
        """Summarise TensorBoard scalars of a run (last, min, max, step, count per tag)."""
        return _guard(lambda: _metrics.metrics_summary(run_dir, tags=tags))

    @mcp.tool(name="isaaclab__tensorboard_tail", annotations=READ_ONLY)
    def isaaclab_tensorboard_tail(run_dir: _RunDir, tag: Annotated[str, Field(description="Scalar tag, from isaaclab__metrics_summary.")], last_n: _LastN = 50) -> str:
        """Last points of one TensorBoard scalar (a training curve). Find tags with isaaclab__metrics_summary."""
        return _guard(lambda: _metrics.tensorboard_tail(run_dir, tag, last_n=last_n))

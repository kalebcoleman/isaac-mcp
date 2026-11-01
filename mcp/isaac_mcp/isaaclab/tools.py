"""Register the Isaac Lab tool pack on the host FastMCP server.

These are host-side tools (like the screen tools): they never need a Kit process. Each returns a JSON
string; failures come back as ``{"error": ...}`` rather than exceptions so a client can read and retry.
Design: docs/ISAACLAB-TOOLS.md.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable

from ..local_tools import DESTRUCTIVE, IDEMPOTENT_WRITE, PYTHON_EXECUTION, READ_ONLY, ScopedTools
from . import install as _install
from . import jobs as _jobs
from . import metrics as _metrics

_NO_INSTALL = {
    "error": "no Isaac Lab install found",
    "hint": "set ISAACLAB_PYTHON (and optionally ISAACLAB_ROOT) to the interpreter of an Isaac Lab 3.0 env",
}


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


def _guard(fn: Callable[[], Any]) -> str:
    try:
        return _dump(fn())
    except (ValueError, OSError) as exc:
        return _dump({"error": str(exc)})


_WINDOW_HINT = (
    "A window opens within ~30 s but the scene is empty/black until loading finishes. Poll "
    "isaaclab__runs_status(job_id) until it reports `window` AND last_log_lines contains "
    "'Completed setting up the environment', wait ~5 s, then screenshot(window_id=...) or a short "
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
    def isaaclab_envs_list(filter: str | None = None) -> str:
        """List registered Isaac Lab task ids (optionally filtered by substring).

        Runs Python and imports task modules, which may have side effects."""
        return _dump(_install.envs_list(filter=filter))

    @mcp.tool(name="isaaclab__train", annotations=PYTHON_EXECUTION)
    def isaaclab_train(
        task: str,
        num_envs: int = 64,
        max_iterations: int | None = None,
        seed: int | None = None,
        device: str | None = None,
        headless: bool = True,
        checkpoint: str | None = None,
        rl_library: str | None = None,
        extra_args: list[str] | None = None,
        timeout_s: float | None = None,
        allow_concurrent: bool = False,
        allow_concurrent_gpu: bool = False,
    ) -> str:
        """Start a training job and return its job_id immediately; follow it with isaaclab__runs_status/tail.

        headless=True with extra_args=['--video'] keeps a Kit renderer without a window;
        video needs optional MoviePy/imageio-ffmpeg in the Lab interpreter.
        allow_concurrent: start even if another job is running (a 16 GB GPU normally fits one).
        allow_concurrent_gpu: start even if an Isaac Sim (Kit bridge) app is running on the GPU.
        """
        return _launch(
            _jobs.train,
            task=task, num_envs=num_envs, max_iterations=max_iterations, seed=seed, device=device,
            headless=headless, checkpoint=checkpoint, rl_library=rl_library, extra_args=extra_args,
            timeout_s=timeout_s, allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
        )

    @mcp.tool(name="isaaclab__eval", annotations=PYTHON_EXECUTION)
    def isaaclab_eval(
        task: str,
        checkpoint: str,
        num_envs: int | None = None,
        headless: bool = True,
        rl_library: str | None = None,
        extra_args: list[str] | None = None,
        timeout_s: float | None = None,
        allow_concurrent: bool = False,
        allow_concurrent_gpu: bool = False,
    ) -> str:
        """Run a policy checkpoint (headless by default) as a job and return its job_id.

        headless=True with extra_args=['--video'] keeps a Kit renderer without a window;
        video needs optional MoviePy/imageio-ffmpeg in the Lab interpreter.
        allow_concurrent: start even if another job is running (a 16 GB GPU normally fits one).
        allow_concurrent_gpu: start even if an Isaac Sim (Kit bridge) app is running on the GPU.
        """
        return _launch(
            _jobs.eval_,
            task=task, checkpoint=checkpoint, num_envs=num_envs, headless=headless,
            rl_library=rl_library, extra_args=extra_args, timeout_s=timeout_s,
            allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
        )

    @mcp.tool(name="isaaclab__play", annotations=PYTHON_EXECUTION)
    def isaaclab_play(
        task: str,
        checkpoint: str,
        num_envs: int = 1,
        headless: bool = False,
        rl_library: str | None = None,
        extra_args: list[str] | None = None,
        timeout_s: float | None = None,
        allow_concurrent: bool = False,
        allow_concurrent_gpu: bool = False,
    ) -> str:
        """Play a checkpoint with a window (interactive); stop it with isaaclab__runs_cancel.

        headless=True with extra_args=['--video'] keeps a Kit renderer without a window;
        video needs optional MoviePy/imageio-ffmpeg in the Lab interpreter.
        allow_concurrent: start even if another job is running (a 16 GB GPU normally fits one).
        allow_concurrent_gpu: start even if an Isaac Sim (Kit bridge) app is running on the GPU.
        """
        return _launch(
            _jobs.play,
            task=task, checkpoint=checkpoint, num_envs=num_envs, headless=headless,
            rl_library=rl_library, extra_args=extra_args, timeout_s=timeout_s,
            allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
        )

    @mcp.tool(name="isaaclab__runs_list", annotations=DESTRUCTIVE)
    def isaaclab_runs_list(state: str | None = None) -> str:
        """List jobs; state is 'running', 'finished' or omitted for all.

        Reconciles orphaned jobs by updating their stored metadata."""
        return _guard(lambda: _jobs.runs_list(state=state))

    @mcp.tool(name="isaaclab__runs_status", annotations=DESTRUCTIVE)
    def isaaclab_runs_status(job_id: str) -> str:
        """State, exit code, elapsed time, last log lines and newest checkpoint of a job. For a windowed
        job also `window` (id, title, size): pass its id to screenshot(window_id=...) or
        start_screen_recording(window_id=...) to see the result. Reconciles orphaned jobs by
        updating their stored metadata."""
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
    def isaaclab_runs_tail(job_id: str, stream: str = "stdout", lines: int = 100) -> str:
        """Last lines of a job's stdout or stderr log. Creates the jobs directory if missing."""
        return _guard(lambda: _jobs.runs_tail(job_id, stream=stream, lines=lines))

    @mcp.tool(name="isaaclab__runs_cancel", annotations=DESTRUCTIVE)
    def isaaclab_runs_cancel(job_id: str, grace_s: int = 10) -> str:
        """Stop a job: SIGTERM to its process group, SIGKILL after grace_s seconds."""
        return _guard(lambda: _jobs.runs_cancel(job_id, grace_s=grace_s))

    @mcp.tool(name="isaaclab__checkpoints_list", annotations=READ_ONLY)
    def isaaclab_checkpoints_list(run_dir: str | None = None, task: str | None = None) -> str:
        """List policy checkpoints (*.pt / *.pth), newest first, under a run dir or the configured logs."""
        return _guard(lambda: _metrics.checkpoints_list(run_dir=run_dir, task=task))

    @mcp.tool(name="isaaclab__metrics_summary", annotations=READ_ONLY)
    def isaaclab_metrics_summary(run_dir: str, tags: list[str] | None = None) -> str:
        """Summarise TensorBoard scalars of a run (last, min, max, step, count per tag)."""
        return _guard(lambda: _metrics.metrics_summary(run_dir, tags=tags))

    @mcp.tool(name="isaaclab__tensorboard_tail", annotations=READ_ONLY)
    def isaaclab_tensorboard_tail(run_dir: str, tag: str, last_n: int = 50) -> str:
        """Last points of one TensorBoard scalar (training curve)."""
        return _guard(lambda: _metrics.tensorboard_tail(run_dir, tag, last_n=last_n))

"""Register the Isaac Lab tool pack on the host FastMCP server.

These are host-side tools (like the screen tools): they never need a Kit process. Each returns a JSON
string; failures come back as ``{"error": ...}`` rather than exceptions so a client can read and retry.
Design: docs/ISAACLAB-TOOLS.md.
"""

from __future__ import annotations

import json
from typing import Any, Callable

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


def _launch(fn: Callable[..., Any], **kwargs: Any) -> str:
    inst = _install.locate_install()
    if not inst:
        return _dump(_NO_INSTALL)
    return _guard(lambda: fn(python=inst["python"], root=inst["isaaclab_root"], **kwargs))


def register_isaaclab_tools(mcp: Any) -> None:
    @mcp.tool(name="isaaclab__install_info")
    def isaaclab_install_info() -> str:
        """Report the Isaac Lab install the tools will use: interpreter, versions, root, venv."""
        return _dump(_install.install_info())

    @mcp.tool(name="isaaclab__envs_list")
    def isaaclab_envs_list(filter: str | None = None) -> str:
        """List registered Isaac Lab task ids (optionally filtered by substring)."""
        return _dump(_install.envs_list(filter=filter))

    @mcp.tool(name="isaaclab__train")
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
    ) -> str:
        """Start a training job and return its job_id immediately; follow it with isaaclab__runs_status/tail."""
        return _launch(
            _jobs.train,
            task=task, num_envs=num_envs, max_iterations=max_iterations, seed=seed, device=device,
            headless=headless, checkpoint=checkpoint, rl_library=rl_library, extra_args=extra_args,
            timeout_s=timeout_s,
        )

    @mcp.tool(name="isaaclab__eval")
    def isaaclab_eval(
        task: str,
        checkpoint: str,
        num_envs: int | None = None,
        headless: bool = True,
        rl_library: str | None = None,
        extra_args: list[str] | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """Run a policy checkpoint (headless by default) as a job and return its job_id."""
        return _launch(
            _jobs.eval_,
            task=task, checkpoint=checkpoint, num_envs=num_envs, headless=headless,
            rl_library=rl_library, extra_args=extra_args, timeout_s=timeout_s,
        )

    @mcp.tool(name="isaaclab__play")
    def isaaclab_play(
        task: str,
        checkpoint: str,
        num_envs: int = 1,
        headless: bool = False,
        rl_library: str | None = None,
        extra_args: list[str] | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """Play a checkpoint with a window (interactive); stop it with isaaclab__runs_cancel."""
        return _launch(
            _jobs.play,
            task=task, checkpoint=checkpoint, num_envs=num_envs, headless=headless,
            rl_library=rl_library, extra_args=extra_args, timeout_s=timeout_s,
        )

    @mcp.tool(name="isaaclab__runs_list")
    def isaaclab_runs_list(state: str | None = None) -> str:
        """List jobs; state is 'running', 'finished' or omitted for all."""
        return _guard(lambda: _jobs.runs_list(state=state))

    @mcp.tool(name="isaaclab__runs_status")
    def isaaclab_runs_status(job_id: str) -> str:
        """State, exit code, elapsed time, last log lines and newest checkpoint of a job."""
        return _guard(lambda: _jobs.runs_status(job_id))

    @mcp.tool(name="isaaclab__runs_tail")
    def isaaclab_runs_tail(job_id: str, stream: str = "stdout", lines: int = 100) -> str:
        """Last lines of a job's stdout or stderr log."""
        return _guard(lambda: _jobs.runs_tail(job_id, stream=stream, lines=lines))

    @mcp.tool(name="isaaclab__runs_cancel")
    def isaaclab_runs_cancel(job_id: str, grace_s: int = 10) -> str:
        """Stop a job: SIGTERM to its process group, SIGKILL after grace_s seconds."""
        return _guard(lambda: _jobs.runs_cancel(job_id, grace_s=grace_s))

    @mcp.tool(name="isaaclab__checkpoints_list")
    def isaaclab_checkpoints_list(run_dir: str | None = None, task: str | None = None) -> str:
        """List policy checkpoints (*.pt / *.pth), newest first, under a run dir or the configured logs."""
        return _guard(lambda: _metrics.checkpoints_list(run_dir=run_dir, task=task))

    @mcp.tool(name="isaaclab__metrics_summary")
    def isaaclab_metrics_summary(run_dir: str, tags: list[str] | None = None) -> str:
        """Summarise TensorBoard scalars of a run (last, min, max, step, count per tag)."""
        return _guard(lambda: _metrics.metrics_summary(run_dir, tags=tags))

    @mcp.tool(name="isaaclab__tensorboard_tail")
    def isaaclab_tensorboard_tail(run_dir: str, tag: str, last_n: int = 50) -> str:
        """Last points of one TensorBoard scalar (training curve)."""
        return _guard(lambda: _metrics.tensorboard_tail(run_dir, tag, last_n=last_n))

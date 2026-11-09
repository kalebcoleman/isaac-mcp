"""Register the Isaac Lab tool pack on the host FastMCP server.

These are host-side tools (like the screen tools): they never need a Kit process. Each returns a JSON
string; failures come back as ``{"error": ...}`` rather than exceptions so a client can read and retry.
Reference: docs/TOOLS-ISAACLAB.md.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Annotated, Any, Callable, Iterable

from pydantic import BeforeValidator, Field

from ..local_tools import ADDITIVE, DESTRUCTIVE, IDEMPOTENT_WRITE, PYTHON_EXECUTION, READ_ONLY, ScopedTools
from . import install as _install
from . import jobs as _jobs
from . import metrics as _metrics
from . import preflight as _preflight
from . import registry as _registry
from . import evalsummary as _evalsummary
from . import rundir as _rundir

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
_RlLibrary = Annotated[str | None, Field(description="RL library, e.g. rsl_rl. Required on Lab 2.x, which has one script folder per library; install_info's rl_libraries lists them. Lab 3 uses the task's registered default and needs this only for a task with none.")]
_ExtraArgs = Annotated[list[str] | None, Field(description="More command-line arguments and Hydra overrides for the Isaac Lab script, e.g. ['--video', '--video_length', '400'] or ['env.commands.base_velocity.ranges.lin_vel_x=[0.5,0.5]'].")]
_Physics = Annotated[str | None, Field(description="physx or newton. Default: the task's own (on Lab 3 often Newton). Lab 2.x is PhysX only. A task with no engine presets fails with it: leave it out.")]
_Timeout = Annotated[float | None, Field(description="Stop the job after this many seconds. Default: no limit.")]
_AllowConcurrent = Annotated[bool, Field(description="Start even if another job is running; a 16 GB GPU normally fits one.")]
_PythonPath = Annotated[list[str] | None, Field(description="Absolute directories prepended to PYTHONPATH, for tasks that live in a project package that is not installed. Also ISAACLAB_TASK_PATH.")]
_TaskEnv = Annotated[dict[str, str] | None, Field(description="Extra environment variables for the Isaac Lab process (names are identifiers). PATH, LD_*, PYTHON*, HOME and similar are refused; use python_path for PYTHONPATH.")]
_ExternalCallback = Annotated[str | None, Field(description="Dotted module.function that registers external tasks, passed as --external_callback=module.function, e.g. g1_recovery.register.register. Default: ISAACLAB_EXTERNAL_CALLBACK. Isaac Lab 3.0.0 runs it only in rsl_rl train and play (eval runs as play); other libraries, env_check and record refuse it (an env default is skipped there).")]
_Label = Annotated[str | None, Field(description="Short name for the job, shown in runs_list and runs_status, e.g. 'v3 before 3b'.")]
_AllowGpu = Annotated[bool, Field(description="Start even if an Isaac Sim (Kit bridge) app is using the GPU.")]


_MAX_WAIT_S = 3600.0
_WAIT_POLL_S = 2.0  # patched by tests


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


def _task_kw(python_path: Any, env: Any, external_callback: Any) -> dict[str, Any]:
    return _jobs._launch_kw(python_path, env, external_callback)


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
    def isaaclab_envs_list(filter: Annotated[str | None, Field(description="Keep task ids containing this text.")] = None,
                           python_path: _PythonPath = None, env: _TaskEnv = None,
                           external_callback: _ExternalCallback = None) -> str:
        """List registered Isaac Lab task ids (optionally filtered by substring).

        Tasks of an external project appear only when its package is importable and its registration ran:
        pass python_path and external_callback (called here with no arguments), or set ISAACLAB_TASK_PATH with
        ISAACLAB_TASK_MODULES. An empty result carries a `hint` saying so.

        Runs Python and imports task modules, which may have side effects."""
        return _guard(lambda: _install.envs_list(filter=filter, python_path=python_path, task_env=env,
                                                 external_callback=external_callback))

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
        physics: _Physics = None,
        extra_args: _ExtraArgs = None,
        timeout_s: _Timeout = None,
        allow_concurrent: _AllowConcurrent = False,
        allow_concurrent_gpu: _AllowGpu = False,
        python_path: _PythonPath = None,
        env: _TaskEnv = None,
        external_callback: _ExternalCallback = None,
        label: _Label = None,
    ) -> str:
        """Start a training job and return its job_id immediately; follow it with isaaclab__runs_status/tail.

        Refuses while a Kit bridge app is running unless allow_concurrent_gpu=true. Video needs optional
        MoviePy/imageio-ffmpeg in the Lab interpreter.
        """
        return _launch(
            _jobs.train,
            task=task, num_envs=num_envs, max_iterations=max_iterations, seed=seed, device=device,
            headless=headless, checkpoint=checkpoint, rl_library=rl_library, physics=physics, extra_args=extra_args,
            timeout_s=timeout_s, allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
            label=label, **_task_kw(python_path, env, external_callback),
        )

    @mcp.tool(name="isaaclab__eval", annotations=PYTHON_EXECUTION)
    def isaaclab_eval(
        task: _Task,
        checkpoint: _Checkpoint,
        num_envs: Annotated[int | None, Field(description="Parallel environments. Default: the task's own.")] = None,
        headless: _Headless = True,
        rl_library: _RlLibrary = None,
        physics: _Physics = None,
        extra_args: _ExtraArgs = None,
        num_episodes: Annotated[int, Field(description="Finished episodes to collect before the run stops itself.")] = 20,
        max_steps: Annotated[int | None, Field(description="Environment-step bound that also ends the run; null removes it.")] = 20000,
        timeout_s: _Timeout = None,
        allow_concurrent: _AllowConcurrent = False,
        allow_concurrent_gpu: _AllowGpu = False,
        python_path: _PythonPath = None,
        env: _TaskEnv = None,
        external_callback: _ExternalCallback = None,
        label: _Label = None,
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
            rl_library=rl_library, physics=physics, extra_args=extra_args, num_episodes=num_episodes, max_steps=max_steps,
            timeout_s=timeout_s, allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
            label=label, **_task_kw(python_path, env, external_callback),
        )

    @mcp.tool(name="isaaclab__play", annotations=PYTHON_EXECUTION)
    def isaaclab_play(
        task: _Task,
        checkpoint: _Checkpoint,
        num_envs: Annotated[int, Field(description="Parallel environments.")] = 1,
        headless: _Headless = False,
        rl_library: _RlLibrary = None,
        physics: _Physics = None,
        extra_args: _ExtraArgs = None,
        timeout_s: _Timeout = None,
        allow_concurrent: _AllowConcurrent = False,
        allow_concurrent_gpu: _AllowGpu = False,
        python_path: _PythonPath = None,
        env: _TaskEnv = None,
        external_callback: _ExternalCallback = None,
        label: _Label = None,
    ) -> str:
        """Play a checkpoint with a window (interactive); stop it with isaaclab__runs_cancel. For a video instead, headless=true with extra_args=['--video', '--video_length', '400'], and isaaclab__runs_status lists video_paths.

        Refuses while a Kit bridge app is running unless allow_concurrent_gpu=true. Video needs optional
        MoviePy/imageio-ffmpeg in the Lab interpreter.
        """
        return _launch(
            _jobs.play,
            task=task, checkpoint=checkpoint, num_envs=num_envs, headless=headless,
            rl_library=rl_library, physics=physics, extra_args=extra_args, timeout_s=timeout_s,
            allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
            label=label, **_task_kw(python_path, env, external_callback),
        )

    @mcp.tool(name="isaaclab__env_check", annotations=PYTHON_EXECUTION)
    def isaaclab_env_check(
        task: _Task,
        num_envs: Annotated[int, Field(description="Parallel environments; keep it small (default 4).")] = 4,
        steps: Annotated[int, Field(description="Environment steps to run before stopping.")] = 60,
        agent: Annotated[str, Field(description="zero (all-zero actions) or random.")] = "zero",
        headless: _Headless = True,
        physics: _Physics = None,
        extra_args: _ExtraArgs = None,
        timeout_s: _Timeout = 900,
        allow_concurrent: _AllowConcurrent = False,
        allow_concurrent_gpu: _AllowGpu = False,
        python_path: _PythonPath = None,
        env: _TaskEnv = None,
        external_callback: _ExternalCallback = None,
    ) -> str:
        """Build a task, step it with zero or random actions, and report what is wrong, as a job. Returns job_id;
        isaaclab__runs_status then gives `env_check_result`: observation and action shapes, reward statistics,
        non-finite values, episode ends in the first steps, step time, GPU memory, and a `problems` list. Run it
        before a long train, and after changing a scene or asset. Task-specific checks (a tip height, a tower
        standing) still need the task's own script.

        Refuses while a Kit bridge app is running unless allow_concurrent_gpu=true."""
        return _launch(_jobs.env_check, task=task, num_envs=num_envs, steps=steps, agent=agent, headless=headless,
                       physics=physics, extra_args=extra_args, timeout_s=timeout_s,
                       allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
                       **_task_kw(python_path, env, external_callback))

    @mcp.tool(name="isaaclab__record", annotations=PYTHON_EXECUTION)
    def isaaclab_record(
        task: _Task,
        checkpoint: Annotated[str | None, Field(description="Checkpoint to play. Omit to record zero or random actions.")] = None,
        video_length: Annotated[int, Field(description="Steps to record.")] = 200,
        num_envs: Annotated[int, Field(description="Parallel environments.")] = 1,
        agent: Annotated[str, Field(description="Without a checkpoint: zero or random.")] = "zero",
        headless: _Headless = True,
        rl_library: _RlLibrary = None,
        physics: _Physics = None,
        extra_args: _ExtraArgs = None,
        timeout_s: _Timeout = 1800,
        allow_concurrent: _AllowConcurrent = False,
        allow_concurrent_gpu: _AllowGpu = False,
        python_path: _PythonPath = None,
        env: _TaskEnv = None,
        external_callback: _ExternalCallback = None,
    ) -> str:
        """Record an MP4 of a task with no window, as a job. With a checkpoint it plays the policy; without one it
        runs zero or random actions, which is enough to see a scene. Returns job_id; isaaclab__runs_status lists
        `video_paths` when it ends. Needs MoviePy or imageio-ffmpeg in the Lab interpreter when a checkpoint is given.

        Refuses while a Kit bridge app is running unless allow_concurrent_gpu=true."""
        return _launch(_jobs.record, task=task, checkpoint=checkpoint, video_length=video_length, num_envs=num_envs,
                       agent=agent, headless=headless, rl_library=rl_library, physics=physics, extra_args=extra_args,
                       timeout_s=timeout_s, allow_concurrent=allow_concurrent,
                       allow_concurrent_gpu=allow_concurrent_gpu, **_task_kw(python_path, env, external_callback))

    @mcp.tool(name="isaaclab__run_script", annotations=PYTHON_EXECUTION)
    async def isaaclab_run_script(
        script: Annotated[str, Field(description="Path to an existing .py file. It runs as `<python> <script> <args...>` with no shell.")],
        args: Annotated[list[str] | None, Field(description="Command-line arguments for the script.")] = None,
        env: Annotated[dict[str, str] | None, Field(description="Extra environment variables; keys must be valid identifiers.")] = None,
        python_path: Annotated[list[str] | None, Field(description="Folders put at the front of PYTHONPATH.")] = None,
        python: Annotated[str | None, Field(description="Interpreter. Default: the configured ISAACLAB_PYTHON.")] = None,
        cwd: Annotated[str | None, Field(description="Working directory. Default: the script's folder.")] = None,
        label: Annotated[str | None, Field(description="Short name, used in the job_id and runs_list.")] = None,
        timeout_s: Annotated[float, Field(description="Hard wall limit in seconds; the whole process group is killed at expiry.")] = 600,
        min_free_vram_mb: Annotated[int | None, Field(description="Refuse to start unless this much GPU memory (MiB) is free on GPU 0 (refused together with CUDA_VISIBLE_DEVICES other than 0). Default: no check.")] = None,
        wait_for_vram_s: Annotated[float, Field(description="With min_free_vram_mb: wait up to this many seconds for the memory to free up (at most 3600).")] = 0,
        single_instance_key: Annotated[str | None, Field(description="Refuse to start while a running job has the same key, so a double-queued sweep cannot run twice.")] = None,
        allow_concurrent: _AllowConcurrent = False,
        allow_concurrent_gpu: _AllowGpu = False,
    ) -> str:
        """Run your own bounded Python script as a managed job (kind "script") and return its job_id at once.

        WARNING: executes arbitrary code with your rights; not sandboxed, not idempotent. Own process
        group: timeout or isaaclab__runs_cancel kills all of it. Print `ISAAC_MCP_ARTIFACT: <path>` to list
        outputs; isaaclab__runs_status then gives `script_result` (exit_code, wall_time_s, log_path, artifacts).

        Refuses while a Kit bridge app is running unless allow_concurrent_gpu=true."""
        cancel = threading.Event()

        def start() -> dict:
            py = python
            if not py:
                inst = _install.locate_install()
                if not inst:
                    return dict(_NO_INSTALL)
                py = inst["python"]
            return _jobs.run_script(
                py, script, args=args, env=env, python_path=python_path, cwd=cwd, label=label,
                timeout_s=timeout_s, min_free_vram_mb=min_free_vram_mb, wait_for_vram_s=wait_for_vram_s,
                single_instance_key=single_instance_key, allow_concurrent=allow_concurrent,
                allow_concurrent_gpu=allow_concurrent_gpu, cancel=cancel)

        # wait_for_vram_s can sleep for minutes: keep it off the event loop so the server keeps answering.
        try:
            return await asyncio.to_thread(_guard, start)
        except asyncio.CancelledError:
            cancel.set()  # the client gave up: stop waiting for GPU memory instead of launching later
            raise

    @mcp.tool(name="isaaclab__preflight", annotations=READ_ONLY)
    def isaaclab_preflight(
        task: Annotated[str | None, Field(description="Task id, to suggest a num_envs from its last isaaclab__env_check.")] = None,
        ledger: Annotated[bool, Field(description="Also report GPU-hours used by your jobs (`gpu_hours`).")] = False,
        label: Annotated[str | None, Field(description="With ledger: also list the jobs whose label or id contains this text.")] = None,
        since: Annotated[str | None, Field(description="With ledger: count jobs from this time (ISO, or an age such as 12h, 3d).")] = None,
    ) -> str:
        """Before launching: free GPU memory and its holders, running Lab jobs, any Kit bridge, `can_start` with
        reasons, and, for an env_checked task, a conservative suggested num_envs. `ledger=true` adds an optional
        `gpu_hours` report (jobs sharing a GPU are counted once). Details: docs/TOOLS-ISAACLAB.md."""
        return _guard(lambda: _preflight.preflight(task, ledger=ledger, label=label, since=since))

    @mcp.tool(name="isaaclab__runs_list", annotations=DESTRUCTIVE)
    def isaaclab_runs_list(
        state: Annotated[str | None, Field(description="running or finished. Default: all.")] = None,
        task: Annotated[str | None, Field(description="Keep jobs whose task id contains this text (case-insensitive).")] = None,
        kind: Annotated[str | None, Field(description="Keep one kind of job: train, eval, play, env_check or record.")] = None,
        since: Annotated[str | None, Field(description="Keep jobs started after this: an ISO time (2026-05-01T12:00, local time if no zone) or an age such as 90m, 2h, 1d, 1w.")] = None,
        limit: Annotated[int, Field(description="Most jobs to return, newest first (1 to 500).")] = 25,
    ) -> str:
        """List Isaac Lab jobs, newest first, to find a job_id. Returns at most `limit` (default 25) matches,
        with `total` (all matches) and `truncated`; narrow with task, kind, state or since, or raise limit.
        Older versions returned every job oldest first.

        Reconciles orphaned jobs by updating their stored metadata."""
        return _guard(lambda: _jobs.runs_list(state=state, task=task, kind=kind, since=since, limit=limit))

    @mcp.tool(name="isaaclab__runs_status", annotations=DESTRUCTIVE)
    async def isaaclab_runs_status(
        job_id: Annotated[str | None, Field(description="job_id from isaaclab__train, eval or play, or isaaclab__runs_list. Give this, or log_root with run_name.")] = None,
        require_run_dir: Annotated[bool, Field(description="With job_id: return an `error` instead of a status when no Lab run folder is found for the job (else run_dir may be the job's own folder; see run_dir_source).")] = False,
        log_root: Annotated[str | None, Field(description="Instead of a job: resolve a run folder by name under this logs folder. Needs run_name.")] = None,
        run_name: Annotated[str | None, Field(description="With log_root: the --run_name the run used; matches folders named <timestamp>_<run_name> exactly.")] = None,
        experiment: Annotated[str | None, Field(description="With log_root: experiment folder name, to narrow several matches.")] = None,
        latest: Annotated[bool, Field(description="With log_root: when several folders match run_name, take the newest instead of an error listing them.")] = False,
        wait_s: Annotated[float, Field(description="With job_id: block up to this many seconds (0 to 3600) until the job is no longer running, then return the final status. Many clients cut a tool call off after a minute or so: wait in slices of 30 to 60 s.")] = 0,
    ) -> str:
        """State, exit code, elapsed time, last log lines, newest checkpoint, `run_dir` with `run_dir_source`
        (lab_log, checkpoint, or job_dir plus a `run_dir_warning` when no Lab run folder is known), `physics`,
        `environment_ready`, an eval job's `eval_result`, and a windowed job's `window`.

        wait_s blocks until the job ends (or returns `running` with `wait_timed_out`) and holds no lock.
        log_root + run_name instead of job_id resolves a run folder by name; no match, several (unless
        latest=true) or an empty name is an `error`.

        Reconciles orphaned jobs by updating their stored metadata."""
        def status() -> dict:
            if job_id is None:
                return _rundir.from_log_root(log_root, run_name, experiment, latest)
            if log_root is not None or run_name is not None:
                raise ValueError("Give job_id, or log_root with run_name, not both.")
            st = _jobs.runs_status(job_id)
            if require_run_dir and st.get("run_dir_source") == "job_dir":
                raise ValueError(
                    f"job {job_id} ({st.get('state')}) has not written a Lab run folder: its log names none and no checkpoint exists. "
                    f"Its own job folder is {st.get('log_dir')}; read isaaclab__runs_tail for the reason.")
            if st.get("pid_alive"):
                from ..screen import window_for_pid

                w = window_for_pid(st.get("pid"))
                if w:
                    st["window"] = {k: w[k] for k in ("id", "title", "width", "height")}
            return st

        if not wait_s:
            return _guard(status)
        if job_id is None:
            return _dump({"error": "wait_s needs a job_id"})
        if isinstance(wait_s, bool) or not 0 < wait_s <= _MAX_WAIT_S:
            return _dump({"error": f"wait_s must be between 0 and {int(_MAX_WAIT_S)} seconds"})
        loop = asyncio.get_running_loop()
        started = loop.time()
        while True:
            out = await asyncio.to_thread(_guard, status)
            parsed = json.loads(out)
            waited = loop.time() - started
            if "error" in parsed or parsed.get("state") != "running":
                parsed["waited_s"] = round(waited, 1)
                return _dump(parsed)
            if waited >= wait_s:
                parsed.update(wait_timed_out=True, waited_s=round(waited, 1))
                return _dump(parsed)
            await asyncio.sleep(min(_WAIT_POLL_S, max(0.01, wait_s - waited)))

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
    def isaaclab_metrics_summary(
        run_dir: Annotated[str | None, Field(description="Run folder, as shown by isaaclab__checkpoints_list or isaaclab__runs_status. Or give job_id.")] = None,
        tags: Annotated[list[str] | None, Field(description="Scalar tags to include. Default: all.")] = None,
        job_id: Annotated[str | None, Field(description="A job from isaaclab__train, instead of run_dir.")] = None,
        compare_jobs: Annotated[list[str] | None, Field(description="Several job_ids: returns their final scalars side by side, to compare seeds or settings.")] = None,
        eval_json: Annotated[list[str] | None, Field(description="ABSOLUTE paths of evaluation-result JSON files (repeat runs of one checkpoint on one pose set): returns pass counts instead of TensorBoard scalars. Relative paths resolve from the server's folder, not yours.")] = None,
        criteria: Annotated[str | None, Field(description="With eval_json: v3 (default, = v3_ground_only: v3 before 3b, ground-only flight), v3_latest_substep, v3_a4, v21, v2 or strict.")] = None,
        label: Annotated[str | None, Field(description="With eval_json: your name for this result, e.g. 'v3 before 3b'; echoed back.")] = None,
        baseline_json: Annotated[list[str] | None, Field(description="With eval_json: absolute paths of the baseline's evaluation files on the same pose set, for the comparison.")] = None,
        baseline_label: Annotated[str | None, Field(description="With baseline_json: the baseline's name, e.g. recC.")] = None,
        allow_test: Annotated[bool, Field(description="With eval_json: permit the TEST pose set (refused by default). Set it only when the user asked to score TEST.")] = False,
    ) -> str:
        """Summarise TensorBoard scalars of a run (last, min, max, step, count per tag), or compare jobs' final values.

        With eval_json it summarises evaluation-result files (G1 recovery `final_*.json`): pass count and mean per file
        set, count per fall direction, the pose set (DEV/HELDOUT/TEST), and the change against baseline_json. TEST,
        mixed splits, unknown pose sets and missing keys are refused with an `error`. Details: docs/TOOLS-ISAACLAB.md."""
        def summarise() -> dict:
            if eval_json is not None:
                if run_dir or job_id or compare_jobs or tags:
                    raise ValueError("eval_json summarises evaluation files; do not combine it with run_dir, job_id, compare_jobs or tags.")
                return _evalsummary.summarize(eval_json, criteria, label, baseline_json, baseline_label, allow_test)
            if any(x is not None for x in (criteria, label, baseline_json, baseline_label)) or allow_test:
                raise ValueError("criteria, label, baseline_json, baseline_label and allow_test apply only with eval_json.")
            if compare_jobs:
                return _metrics.compare_runs({job: _jobs.runs_status(job).get("run_dir") for job in compare_jobs}, tags=tags)
            folder = run_dir or (_jobs.runs_status(job_id).get("run_dir") if job_id else None)
            if not folder:
                raise ValueError("Give run_dir, job_id, compare_jobs or eval_json. A job that has not written a run folder yet has no metrics.")
            return _metrics.metrics_summary(folder, tags=tags)

        return _guard(summarise)

    @mcp.tool(name="isaaclab__checkpoint_register", annotations=ADDITIVE)
    def isaaclab_checkpoint_register(
        path: Annotated[str, Field(description="Absolute path of the checkpoint file (.pt, .pth, .zip, .ckpt, .onnx) to register, e.g. from isaaclab__checkpoints_list.")],
        role: Annotated[str, Field(description="What it is for, one short line: baseline, candidate, init, final...")],
        provenance: Annotated[str, Field(description="Where it came from, one line: run name, commit, settings, evaluation result.")],
        registry: Annotated[str | None, Field(description="Markdown file to add the row to. Default: README.md in the checkpoint's folder. It must be in that folder or one above it.")] = None,
        read_only: Annotated[bool, Field(description="Also remove write permission from the checkpoint file, so a later run cannot overwrite it.")] = False,
    ) -> str:
        """Record a checkpoint in a README table (path, role, sha256, size, time, provenance) so a baseline cannot be
        replaced unnoticed. Append-only: one atomic write adds a row; a path that already has a row is refused with the
        existing row and whether the file still matches its hash. Returns the sha256 and the row, `same_content_as`
        if another registered path has identical bytes, and, in a git work tree, a `git` hint: `.pt` files are usually
        ignored, so commit them with `git add -f` (this tool never runs git). Reads the checkpoint, writes only the registry."""
        return _guard(lambda: _registry.register(path, role, provenance, registry, read_only))

    @mcp.tool(name="isaaclab__tensorboard_tail", annotations=READ_ONLY)
    def isaaclab_tensorboard_tail(run_dir: _RunDir, tag: Annotated[str, Field(description="Scalar tag, from isaaclab__metrics_summary.")], last_n: _LastN = 50) -> str:
        """Last points of one TensorBoard scalar (a training curve). Find tags with isaaclab__metrics_summary."""
        return _guard(lambda: _metrics.tensorboard_tail(run_dir, tag, last_n=last_n))

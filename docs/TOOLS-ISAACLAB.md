# Isaac Lab Host Tools (`isaaclab__*`)

This document covers the 12 host-side tools registered by `mcp/isaac_mcp/isaaclab/tools.py`
(`install.py`, `jobs.py`, `metrics.py`). It is the implementation reference; every argument is
listed in [TOOLS-ARGUMENTS.md](TOOLS-ARGUMENTS.md#isaac-lab).
For design history and the original job-guard write-up, see [ISAACLAB-TOOLS.md](ISAACLAB-TOOLS.md).

## Naming: host-side tools

- These are **host-side** MCP tools, not bridge-discovered Kit tools. They are registered
  directly on the FastMCP server in `mcp/isaac_mcp/server.py` and run in the `isaac-mcp`
  process. They spawn and manage Isaac Lab subprocesses; no Kit process is required for
  the host to expose them, and they do not call `run_python`.
- MCP tool names cannot contain dots, so they appear as `isaaclab__<tool>`
  (double underscore). There is no `isaaclab.` bridge namespace behind them.
- Every tool returns a JSON string. Failures come back as `{"error": ...}` (often with
  `hint`) rather than exceptions, so a client can read and retry. An argument of the wrong type or a missing
  one is refused earlier by the MCP layer, as an MCP error.

## Setup and configuration

### Locating an Isaac Lab install

`isaaclab__install_info` and the job launchers resolve an install in this order
(`mcp/isaac_mcp/isaaclab/install.py`):

1. `ISAACLAB_PYTHON` (interpreter path), with `ISAACLAB_ROOT` if provided; otherwise a
   sibling `IsaacLab` directory is tried. If the explicitly configured paths are invalid,
   discovery returns no install (it does not fall back).
2. `ISAACLAB_ROOT/.venv/bin/python`, or the active `VIRTUAL_ENV` when the checkout has no `.venv`.
3. An activated `VIRTUAL_ENV`, if it belongs to an Isaac Lab checkout or has a sibling
   `IsaacLab` checkout with the reinforcement-learning scripts.
4. The current directory's `.venv` when the current directory is an Isaac Lab checkout.

Between steps 2 and 3, a project named by `ISAAC_MCP_PROJECT` in the server's own environment (a directory or its
`isaac-mcp.json`) can set `isaaclab_python` and optionally `isaaclab_root`, for a project with its own Lab. Set that variable
in the `env` of the client's server entry. Relative paths start at that file's folder:

```json
{"isaaclab_python": ".venv/bin/python", "isaaclab_root": "../IsaacLab"}
```

A file found only by searching up from the working directory is ignored for these two keys, and `isaaclab__install_info`
reports `project_setting_ignored` with what to set. Lab tools run this interpreter, and starting a client inside a cloned
repository must not let that repository choose what runs. The same rule applies to a project file's `asset_path`, which
is not an approval.

The interpreter must be a virtual environment with Isaac Lab installed, which is checked from its files before anything
runs it. An invalid setting is an error, not a fallback. That check catches mistakes; it does not stop a deliberately
faked environment, so name only a project you trust. A model with `put_file` can write any file there. Hide `put_file`
(`ISAAC_MCP_BUILTINS=0`) if that matters. Explicit `ISAACLAB_PYTHON` or `ISAACLAB_ROOT` win, and then
`isaaclab__install_info` reports `project_setting_ignored`; remove them from that server's configuration to use the
project's. It reports `source`, and `tested: false` for a Lab version not in the tested list below, such as 2.3.2 on
Sim 5.1: the version read works, but jobs on it are untested. A pip-only Lab has no `scripts/reinforcement_learning`, so
`isaaclab__train`, `eval` and `play` refuse with "Isaac Lab checkout not found" until `isaaclab_root` points at a checkout.

The resolver does not scan the home directory or assume a project-specific path. If no standard
environment or current checkout is available, set `ISAACLAB_PYTHON` and, when needed,
`ISAACLAB_ROOT`, or set `ISAACLAB_ROOT` for a checkout with its own `.venv`.

Automatic environment discovery requires local Isaac Lab package metadata; it does not run an
interpreter to decide whether to select one. `isaaclab__install_info` then probes package metadata
(`isaaclab`, `isaacsim`) in a short-lived subprocess. It does not search `PATH` or
`ISAACLAB_PATH`, and does not validate every supported combination. Set explicit paths for
other layouts. `ISAACLAB_JOBS_DIR` overrides the job state directory (default
`~/.local/share/isaac-mcp/jobs`).

For example, substitute the paths for your own installation before starting the host server:

```bash
export ISAACLAB_PYTHON=/path/to/lab-env/bin/python
export ISAACLAB_ROOT=/path/to/IsaacLab
./install.sh --no-sim --claude --isaaclab-python "$ISAACLAB_PYTHON" --isaaclab-root "$ISAACLAB_ROOT"
```

The installation must already exist; `--no-sim` does not install Isaac Lab. Restart a client whose configuration was
changed, then call `isaaclab__install_info` and `isaaclab__envs_list` to check discovery.

### Task discovery

`isaaclab__envs_list` imports `isaaclab_tasks`, plus the comma-separated modules in
`ISAACLAB_TASK_MODULES` (with `ISAACLAB_TASK_PATH` added to `PYTHONPATH`), then reads
`gymnasium.registry`. Import failures are returned in `import_errors`. Custom task modules must
be explicitly configured; sibling packages are no longer imported automatically. Jobs use the
same resolver (`ISAACLAB_TASK_MODULES` / `ISAACLAB_TASK_PATH` are forwarded to the child).

### TensorBoard readout

`isaaclab__metrics_summary` and `isaaclab__tensorboard_tail` read `events.out.tfevents.*`
files and require the `tensorboard` Python package. Install the extra:
from the repo root use `uv tool install --force --editable './mcp[isaaclab]'`; `install.sh` includes this extra. Without it these tools
return `{"error": "install the 'isaaclab' extra: pip install 'isaac-mcp[isaaclab]'"}`.

### Log and checkpoint roots

`isaaclab__checkpoints_list`, `isaaclab__metrics_summary` and
`isaaclab__tensorboard_tail` only operate under allowed roots:

- `ISAACLAB_LOGS_DIR`, when set;
- `ISAACLAB_JOBS_DIR` (or `~/.local/share/isaac-mcp/jobs`), and the default jobs directory;
- `<isaaclab_root>/logs` and `<parent_of_isaaclab_root>/logs` (from the resolved install);
- the temp directory only when `ISAACLAB_ALLOW_TMP=1` (test fixtures).

Without `run_dir`, checkpoint discovery searches `ISAACLAB_LOGS_DIR` when set; otherwise it
searches `<isaaclab_root>/logs` (if an install root is resolved) and the jobs directory.
The former implicit project-specific log directory is no longer searched or allowed by default;
set `ISAACLAB_LOGS_DIR` to continue using it. Paths outside the allowed roots are rejected with
`{"error": "path outside allowed roots"}`.

### GPU resources and authorization

Training, eval and play jobs launch Isaac Lab's own RL stack. The tested configurations use a GPU; resource needs depend on the task and backend. These can be
long-running processes and must be started only with the user's explicit
authorization and with GPU capacity available. On a single GPU, a running Kit app and a
training job can contend for GPU memory. This pack does not evict or stop a sim; it only
refuses to start (see [Concurrency guards](#concurrency-guards)) unless the caller passes the
override flags. `headless=true` is the default for `train`/`eval` to avoid a second
renderer for ordinary jobs; adding `--video` requires rendering even headlessly.
`play` defaults to `headless=false` and opens a window. No actual training run is
required or performed by this documentation.

### Environment variables (summary)

| Variable | Used by | Effect |
|---|---|---|
| `ISAACLAB_PYTHON` | install, jobs | Isaac Lab interpreter (or use `ISAACLAB_ROOT` with a `.venv`) |
| `ISAACLAB_ROOT` | install, jobs | Isaac Lab root (scripts live under `scripts/reinforcement_learning/`) |
| `ISAACLAB_JOBS_DIR` | jobs | Job state directory (default `~/.local/share/isaac-mcp/jobs`) |
| `ISAACLAB_LOGS_DIR` | metrics | Primary log root for checkpoints/metrics |
| `ISAACLAB_TASK_MODULES` | install, jobs | Comma list of task modules to import before lookup |
| `ISAACLAB_TASK_PATH` | install, jobs | `os.pathsep` list added to `PYTHONPATH` |
| `ISAACLAB_ALLOW_TMP` | metrics | `1` adds the temp dir to allowed roots (tests only) |
| `UR5E_DT_PORT` / `ISAAC_MCP_PORT` | jobs (Kit guard) | Fixed port probed for a running Kit bridge (default 8766) |
| `CUDA_VISIBLE_DEVICES` | jobs (set by `device`) | Set per job from the `device` argument |

## Quick reference

| MCP name | Purpose |
|---|---|
| `isaaclab__install_info` | Report the Isaac Lab install: interpreter, versions, root, venv |
| `isaaclab__envs_list` | List registered Isaac Lab task ids (optional substring filter) |
| `isaaclab__train` | Start a training job (async) and return its `job_id` |
| `isaaclab__eval` | Run and score a policy checkpoint as a job (async) |
| `isaaclab__play` | Play a checkpoint with a window (async, interactive) |
| `isaaclab__runs_list` | List jobs (running/finished/all) |
| `isaaclab__runs_status` | State, pid, elapsed, exit code, log tail, checkpoint, run dir, window |
| `isaaclab__runs_tail` | Last lines of a job's stdout/stderr |
| `isaaclab__runs_cancel` | Stop a job (SIGTERM then SIGKILL process group) |
| `isaaclab__checkpoints_list` | List `*.pt`/`*.pth` checkpoints (newest first) |
| `isaaclab__metrics_summary` | Summarise TensorBoard scalars (last/min/max/step/count) |
| `isaaclab__tensorboard_tail` | Last points of one TensorBoard scalar |

## What a checkout offers: the profile

`isaaclab__install_info` returns `profile`, read from the checkout's script files once per root, without importing Isaac Lab
or starting Kit: `lab_version`, `isaaclab_version`, `isaacsim_version`, `layout`, `libraries` and, per script, its declared
`flags` (including a helper beside it and AppLauncher's when that source is present). `flags: null` means unknown, not none.
Isaac Lab 3's scripts hand over to a CLI elsewhere, so theirs are unknown. `tested` is a separate statement: whether that Lab and Sim pair
was verified with real jobs, which so far is only Isaac Lab 3.0.0 on Isaac Sim 6.2.

| `layout` | Scripts | Library | Display |
|---|---|---|---|
| `unified` | `scripts/reinforcement_learning/{train,play}.py` | `--rl_library` | `--viz none\|kit` |
| `per_library` | `scripts/reinforcement_learning/<library>/{train,play}.py` | the folder; `rl_library` is required, and the refusal lists `libraries` | `--headless` when headless and without `--video` |
| `none` | not found: jobs are refused before starting, naming the expected paths | | |

`train`, `eval` and `play` build their command from this. A flag a script does not declare, generated or in `extra_args`, is refused
before anything starts, naming the flag and the alternative; otherwise a per-library script would pass it to Hydra after Kit started. With unknown flags nothing is refused.
`HEADLESS` is always set in the child.

**Isaac Lab 2.3.2 with Sim 5.1 is experimental, and `tested` stays false.** Tiny Cartpole jobs ran through these tools on one desktop with `rsl_rl`, `skrl`, `sb3` and
`rl_games`, including checkpoints, metrics, eval and play; the profile's flags matched each script's real `--help`. That shows the launcher works, not long training or other tasks.
`rl_games` needs `minibatch_size` to divide `num_envs` times the task's `horizon_length` from its rl_games config, which varies by task (24 for `Isaac-Reach-UR10`; not the log's `seq_length`). A failure is a bare `AssertionError` on `batch_size % minibatch_size`. Measured on `Isaac-Reach-UR10`: `minibatch_size=64` or `192` at 16 environments, and `1536` at 64. `sb3` saves `model.zip`, which counts as a checkpoint only under an `sb3` path. The task's default can be larger than a small run's whole batch (24576 for `Isaac-Reach-Franka-v0`, against 64 environments times 24 steps), so pass the override. `rl_games` also saves a checkpoint only every `save_frequency` epochs (100 by default) and `save_best_after` (200), so a short run leaves nothing to evaluate unless you pass `agent.params.config.save_frequency=20` and `agent.params.config.save_best_after=10`; `rsl_rl` always saves at the end.

`isaaclab__envs_list` can't list tasks on this pair: importing `isaaclab_tasks` needs `pxr`, which exists only once Kit has started. It
reports `isaac_tasks_unavailable` and lists the ids it can read from the checkout's source, marked `source: "scanned"`. `train` and `play` work with those.

A job records its own exit code in its folder, so one that finishes after the host that started it has gone still reports `succeeded` or `failed` correctly. `failed` with `note: "host restarted"` now means it left no code: it was killed, or predates this. `timeout_s` is enforced by the host that started the job and, if that has gone, by the next `runs_status` or `runs_list`; `runs_status` echoes it as `timeout_s` when one was set.

**A crash can exit 0.** Kit's shutdown can hide an uncaught exception: seen on Lab 2.3.2 with an unknown task. A job that exits 0 with a traceback at the end of stderr reports `failed` with a note.
One exception: a `train` job whose run folder already has a checkpoint still reports `succeeded`, so check its log and iteration count if it stopped early.

When `isaaclab_tasks` cannot be imported, `isaaclab__envs_list` adds `isaac_tasks_unavailable` and leaves out gymnasium's own tasks, such as classic-control `CartPole`.

## Parameter conventions

- All tools return JSON strings; the tables below list the parsed object shape.
- Required parameters are marked **required**. Optional parameters with `null` defaults can be omitted.
- `task` must match `^[A-Za-z0-9_.:-]+$`. `rl_library` must match `^[a-z0-9_]+$`.
- `extra_args` accepts syntactically validated `--flag`/`--flag=value` arguments and Hydra overrides
  such as `key.sub=value`, `+key=value`, or `~key`. It is not a semantic allowlist.
- Checkpoint paths are resolved and must exist and lie under the Isaac Lab root or the jobs
  directory. `run_dir` for metrics must lie under the allowed roots above.
- Job ids match `^\d{8}-\d{6}-[A-Za-z0-9_.-]+$` (timestamp + task slug, with `-2`, `-3`
  suffixes if two launches land in the same second).
- Durations are in seconds. `num_envs` defaults to 64 for train, 1 for play.

## Tool details

### `isaaclab__install_info`

- Parameters: none.
- Returns: `{python, isaaclab_version, isaacsim_version, isaaclab_root, venv, source, rl_libraries, tested, profile}` on success, with a `note` if this pair isn't tested;
  `{"error": "Isaac Lab install not found", "hint": "set ISAACLAB_PYTHON or ISAACLAB_ROOT"}` (or a probe error)
  on failure. A successful probe can still return null version fields if distributions are absent;
  it does not establish task import, renderer or training readiness.
- Example call shape: `{"tool": "isaaclab__install_info", "arguments": {}}`

### `isaaclab__envs_list`

- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `filter` | string | no | `null` | Case-insensitive substring matched against the task id. |
- Returns: `{envs: [{id, entry_point}], import_errors?}`. `import_errors` appears only if a
  configured task module failed to import. Short-lived subprocess with a 60 s timeout.
- Example: `{"filter": "reach"}`

### `isaaclab__train`

- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `task` | string | yes | — | Task id (validated). |
  | `num_envs` | integer | no | `64` | |
  | `max_iterations` | integer | no | `null` | |
  | `seed` | integer | no | `null` | |
  | `device` | string | no | `null` | Sets `CUDA_VISIBLE_DEVICES` when not `cpu`. |
  | `headless` | boolean | no | `true` | No window. Normally `--viz none`; `--video` selects Kit with child-only `HEADLESS=1`. `false` selects Kit with `HEADLESS=0`. |
  | `checkpoint` | string | no | `null` | Path to resume from (must be under allowed roots). |
  | `rl_library` | string | no | `null` | RL backend, e.g. `rl_games`, `rsl_rl`, `skrl`. Before launching, the tool runs `isaaclab__install_info`'s `rl_libraries` check in the same interpreter and refuses a library that is not importable there, naming the installed ones. If that check cannot run, the launch is not blocked. |
  | `extra_args` | array[string] | no | `null` | Validated flags/Hydra overrides. |
  | `timeout_s` | number | no | `null` | The host that started the job kills its process group after this many seconds. If that host has gone, the job runs on until the next `runs_status` or `runs_list`, and `runs_status` echoes it. |
  | `allow_concurrent` | boolean | no | `false` | Override the job-vs-job guard. |
  | `allow_concurrent_gpu` | boolean | no | `false` | Override the Kit-bridge (GPU) guard. |
- Returns on success: `{job_id, log_dir, command}`. A guard refusal includes `error` and `hint`, along with `running_job_id`/`running_pid` (job guard) or `kit_bridges` (Kit guard). On no install: `{"error": "no Isaac Lab install found", "hint": ...}`.
- Example: `{"task": "Isaac-Reach-UR10", "num_envs": 64, "max_iterations": 2, "rl_library": "rl_games", "headless": true}`

### `isaaclab__eval`

- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `task` | string | yes | — | |
  | `checkpoint` | string | yes | — | Required; validated path. |
  | `num_envs` | integer | no | `null` | |
  | `headless` | boolean | no | `true` | |
  | `rl_library` | string | no | `null` | |
  | `extra_args` | array[string] | no | `null` | |
  | `num_episodes` | integer | no | `20` | Finished episodes to collect. Must be at least 1. |
  | `max_steps` | integer | no | `20000` | Environment-step bound that also ends the run; `null` removes it. |
  | `timeout_s` | number | no | `null` | Host watchdog, separate from the step bound. |
  | `allow_concurrent` | boolean | no | `false` | |
  | `allow_concurrent_gpu` | boolean | no | `false` | |
- Returns on launch: same shape as `isaaclab__train`. Runs `play.py` with `--checkpoint` (Isaac Lab has no separate
  eval entrypoint) and `--viz none|kit`, through a scoring launcher (see below). Once it finishes,
  `isaaclab__runs_status` also returns `eval_result`.
- Example: `{"task": "Isaac-Reach-UR10", "checkpoint": "/abs/path/to/model.pth", "rl_library": "rl_games", "headless": true, "num_episodes": 20}`

#### How eval produces a score

The job runs `mcp/isaac_mcp/isaaclab/eval_runner.py` with the Isaac Lab interpreter instead of `play.py`
directly. The runner imports the configured task modules, patches `gymnasium.make` so the environment it
returns has its `step` wrapped, and then runs the real `play.py` unchanged through `runpy` with the same
arguments. Isaac Lab itself is not modified, and `isaaclab__play` still runs the plain script.

- The wrapper sits on the object `gymnasium.make` returned, which is what the libraries' vector-environment
  wrappers (`RslRlVecEnvWrapper`, `RlGamesVecEnvWrapper`, `SkrlVecEnvWrapper`, `Sb3VecEnvWrapper`) call in
  their own `step`. It therefore sees exactly one call per simulator step, with the reward and
  terminated/truncated flags the environment produced. Works with torch tensors on any device, numpy or
  plain Python values.
- Each environment's reward is summed until that environment is terminated or truncated; that episode's
  undiscounted return and length (environment steps) are then recorded. Only complete episodes count.
- The run stops when at least `num_episodes` have finished **and** every parallel environment has finished
  at least one episode, or when `max_steps` environment steps have been taken. `max_steps` is the bound
  that makes a run end even when a task never terminates. `stopped_by` says which happened. Waiting for
  every environment's first finished episode avoids scoring only the fastest environments; a run cut short
  by `max_steps` says so in `note`.
- The runner then makes the simulation app's `is_running()` return false, so `play.py` leaves its loop and
  closes the environment and app itself; the job exits 0. If a play loop ignored that, the second step
  after the target raises a private exception instead of adding made-up episodes.
- `eval_result.json` is written atomically in the job folder and is what `runs_status` reports once it is
  there. Its fields are `episodes`, `mean_return`, `std_return`, `min_return`, `max_return`, `mean_length`,
  `steps`, `success_rate` (only when the environment reports success), `stopped_by`, `num_envs`,
  `num_episodes_requested`, `max_steps` and `note`. `steps` counts environment steps (one per simulator
  step across all parallel environments), not summed per-environment steps.
- If no episode finishes, `episodes` is `0` and the return fields are `null`, with a `note`; no score is
  estimated. A play script that never builds its environment through `gymnasium.make`, or never steps it,
  likewise produces no score: the runner reports that rather than inventing one. A multi-agent environment
  whose base `step` returns a per-agent reward dict is also reported as unscoreable. An uncaught error is
  written into the result and still fails the job. A job killed by `timeout_s` or `runs_cancel` may leave
  no result at all.
- A training log's mean reward is a different measurement: it usually covers a recent window of rollouts,
  often with stochastic actions, while eval runs the saved policy deterministically. Expect a gap in either
  direction; use `eval_result` when the question is how good the saved policy is.

Measured on Isaac Lab 2.3.2 with Isaac Sim 5.1 and `Isaac-Cartpole-v0` for `rsl_rl`, `rl_games`, `skrl` and `sb3`:
eval returned scores for early and trained checkpoints, and the trained checkpoint scored clearly higher.
Isaac Lab 3's unified `play.py` was not run here, so whether its libraries are wrapped the same way is not
verified; an unscoreable script shows up as `episodes: 0` / no score, not a number.

### `isaaclab__play`

- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `task` | string | yes | — | |
  | `checkpoint` | string | yes | — | Required; validated path. |
  | `num_envs` | integer | no | `1` | |
  | `headless` | boolean | no | `false` | Defaults to a window (`--viz kit`). |
  | `rl_library` | string | no | `null` | |
  | `extra_args` | array[string] | no | `null` | |
  | `timeout_s` | number | no | `null` | |
  | `allow_concurrent` | boolean | no | `false` | |
  | `allow_concurrent_gpu` | boolean | no | `false` | |
- Returns: same shape as `isaaclab__train`. Interactive; stop it with `isaaclab__runs_cancel`. With `headless=false` a window opens, titled "Isaac Lab 3.0.0" on that version and "Isaac Sim 5.1.0" on Lab 2.3.2; `isaaclab__runs_status` reports its `window` (id, title, size) for screenshot/recording.
- Example: `{"task": "Isaac-Reach-UR10", "checkpoint": "/abs/path/to/model.pth", "rl_library": "rl_games", "num_envs": 4, "headless": false}`

### `isaaclab__runs_list`

- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `state` | string | no | `null` | `"running"`, `"finished"` (any end state, including failed), `"all"`, or omitted for all; anything else returns an error naming these. |
- Returns: `{runs: [{job_id, kind, task, state, started_at, elapsed_s, log_dir}]}`. Dead pids are reconciled to `failed` ("host restarted") on read.
- Example: `{"state": "running"}`

### `isaaclab__runs_status`

- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `job_id` | string | yes | — | Validated id. |
- Returns:
  `{state, pid_alive, elapsed_s, exit_code, last_log_lines, latest_checkpoint, run_dir, log_dir, pid, environment_ready}` and `physics` (the engine Lab's banner names, such as `newton_mjwarp` or `physx`; a task picks its own, and Newton is not PhysX)
  plus `window: {id, title, width, height}` when the job is live and owns a window, and `eval_result`
  (`episodes`, `mean_return`, ...) for an `isaaclab__eval` job whose runner has written it, including after the
  host that started the job has gone (it is read from `eval_result.json` in the job folder). `last_log_lines` is the last 20 useful lines of `stdout.log` (a progress-bar line keeps only its newest text after a carriage return, and a line that is only a bar is dropped, so skrl and sb3 tails show log text rather than bars); a failed job also has `last_stderr_lines`, the last 20 of `stderr.log`, where launch and import failures go. `latest_checkpoint` is the newest `*.pt`/`*.pth` under `run_dir` (or parsed from the log's "saving checkpoint '...'" lines); `run_dir` is the experiment directory reported by status (may be updated from the log). Discovery accepts both a complete experiment path and Isaac Lab 3's separate experiment-root/run-name lines, then locates checkpoints in that directory. `log_dir` remains the MCP job's stdout/stderr directory; it is not necessarily where training artifacts are written.
- Example: `{"job_id": "20261030-120000-Isaac-Reach-UR10"}`

### `isaaclab__runs_tail`

- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `job_id` | string | yes | — | |
  | `stream` | string | no | `"stdout"` | `"stdout"` or `"stderr"`. |
  | `lines` | integer | no | `100` | Clamped to 1..1000. |
- Returns: `{lines: [str]}`.
- Example: `{"job_id": "20261030-120000-Isaac-Reach-UR10", "stream": "stderr", "lines": 50}`

### `isaaclab__runs_cancel`

- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `job_id` | string | yes | — | |
  | `grace_s` | integer | no | `10` | Seconds to wait after SIGTERM before SIGKILL. |
- Returns: `{"cancelled": true, "state": "cancelled"}` when stopped; `{"cancelled": false, "state": <state>, "note": "job already <state>"}` when the job already finished. SIGTERM is sent to the process group, then SIGKILL after `grace_s`.
- Example: `{"job_id": "20261030-120000-Isaac-Reach-UR10", "grace_s": 10}`

### `isaaclab__checkpoints_list`

- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `run_dir` | string | no | `null` | Restrict search to this run dir (must be under allowed roots). |
  | `task` | string | no | `null` | Substring filter against the full checkpoint path. |
- Returns: `{checkpoints: [{path, step, size_bytes, mtime}]}`, newest first, capped at 200. Without `run_dir`, searches the default log roots. `step` is parsed from the filename when possible, else `null`.
- Example: `{"run_dir": "/abs/path/to/run", "task": "model"}`

### `isaaclab__metrics_summary`

- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `run_dir` | string | yes | — | Must be under allowed roots. |
  | `tags` | array[string] | no | `null` | Tag list; `null` summarises every available tag. |
- Returns: `{scalars: {<tag>: {last, min, max, step, count}}}`. Unknown tags return `{"error": "unknown tag", "available": [...]}`. Non-finite values are encoded as the strings `"nan"`, `"inf"`, `"-inf"`. Requires the `tensorboard` extra.
- Example: `{"run_dir": "/abs/path/to/run", "tags": ["rewards/iter", "Episode/dist_m"]}`

### `isaaclab__tensorboard_tail`

- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `run_dir` | string | yes | — | Must be under allowed roots. |
  | `tag` | string | yes | — | Scalar tag to read. |
  | `last_n` | integer | no | `50` | Last N points; must be an integer (not a boolean) or null. |
- Returns: `{points: [{step, value, wall_time}]}`. Unknown tag returns `{"error": "unknown tag", "available": [...]}`. Points are sorted by (step, wall_time) and sliced to the last `last_n` when `last_n > 0`.
- Example: `{"run_dir": "/abs/path/to/run", "tag": "rewards/iter", "last_n": 50}`

## Workflows

These describe the intended call sequence. Examples are illustrative shapes only; do not
start a job without the user's authorization and available GPU capacity.

### 1. Discover the install and tasks

1. `isaaclab__install_info` — confirm interpreter, `isaaclab`/`isaacsim` versions, root, venv.
2. `isaaclab__envs_list` with `{"filter": "reach"}` — confirm the task id exists and check
   `import_errors`.

### 2. Train

1. (Optional) `isaaclab__runs_list` with `{"state": "running"}` — ensure no other job is active.
2. `isaaclab__train` with the chosen task and parameters. It returns immediately with
   `{job_id, log_dir, command}`.
3. Poll with `isaaclab__runs_status` and `isaaclab__runs_tail` (stdout/stderr) until the
   state is no longer `running`. For a windowed job, `runs_status` also reports `window`.
4. `isaaclab__checkpoints_list` / `isaaclab__metrics_summary` against the `run_dir`
   reported by status.

Example train call shape (illustrative):

```json
{
  "tool": "isaaclab__train",
  "arguments": {
    "task": "Isaac-Reach-UR10",
    "num_envs": 64,
    "max_iterations": 2,
    "rl_library": "rl_games",
    "headless": true,
    "extra_args": ["agent.params.config.minibatch_size=1536"]
  }
}
```

### 3. Inspect checkpoints and metrics

1. `isaaclab__runs_status` → read `run_dir` and `latest_checkpoint`.
2. `isaaclab__checkpoints_list` with `{"run_dir": "<run_dir>"}`.
3. `isaaclab__metrics_summary` with `{"run_dir": "<run_dir>"}` (or a `tags` list).
4. `isaaclab__tensorboard_tail` with `{"run_dir": "<run_dir>", "tag": "<tag>", "last_n": 50}`.

### 4. Play / eval a checkpoint

1. `isaaclab__play` (or `isaaclab__eval`) with `task`, `checkpoint`, and options. Returns
   `{job_id, log_dir, command}`. For eval, wait for the job to finish and read `eval_result` from
   `isaaclab__runs_status`: it ends on its own after `num_episodes` or `max_steps`, so no cancel is needed.
2. For a windowed `play`, call `isaaclab__runs_status` to get `window.id`, then use the host
   `screenshot(window_id=...)` / `start_screen_recording(window_id=...)` tools to inspect.
   The viewport is black until the log contains "Completed setting up the environment"; check for it with
   `isaaclab__runs_tail`, since later lines can push it out of `last_log_lines`, then wait ~5 s before capturing.
3. Stop with `isaaclab__runs_cancel` when done.

Example play call shape (illustrative):

```json
{
  "tool": "isaaclab__play",
  "arguments": {
    "task": "Isaac-Reach-UR10",
    "checkpoint": "/abs/path/to/model.pth",
    "num_envs": 4,
    "headless": false
  }
}
```

### 5. Cancel and clean up

- `isaaclab__runs_cancel` with `{"job_id": "...", "grace_s": 10}`.
- Re-check with `isaaclab__runs_list` / `isaaclab__runs_status`. Cancelled jobs release the
  job-vs-job guard.

## Concurrency guards

`isaaclab__train`, `isaaclab__eval` and `isaaclab__play` apply two independent guards
before spawning. The job-vs-job guard runs first.

1. **Job-vs-job guard** (`allow_concurrent`, default `false`): refuses to start if another
   Isaac Lab job started by this tool pack is still running. A job a watcher in this process
   owns counts as running until its process exits; a record from another process counts only
   if its pid is alive. Stale records of dead jobs do not block. The refusal includes `error`, `running_job_id`, `running_pid`, and `hint`.
   Pass `allow_concurrent=true` to start anyway.
2. **Kit-bridge (GPU) guard** (`allow_concurrent_gpu`, default `false`): refuses to start if
   a Kit bridge (a running Isaac Sim app) is reachable — either advertised via runtime
   portfiles (`client.discover_bridges`) or listening on the configured port (`UR5E_DT_PORT`,
   else `ISAAC_MCP_PORT`, else 8766; `0` or a non-integer disables the fixed-port probe).
   The refusal includes `error`, `kit_bridges`, and `hint`.
   Pass `allow_concurrent_gpu=true` to start anyway.

Each refusal's own `hint` is returned unchanged: it names the override (`allow_concurrent=true` or `allow_concurrent_gpu=true`), and the window paragraph is only added to a job that actually started.

The two flags are independent: `allow_concurrent` covers other Lab jobs only,
`allow_concurrent_gpu` covers the Kit app only. The probe is a TCP connect with a 0.3 s
timeout and does not touch the GPU. The guard is atomic only within one host process; two
separate host processes can still race. The Kit guard checks the configured port plus advertised portfiles. It does not independently scan port 8767
unless that port is configured or advertised.

### Checks before anything is launched

Three launch-time mistakes would otherwise surface tens of seconds later, after Kit has loaded,
so the tools check them first and refuse with a clear message. Each uses a short-lived probe of
the same Lab interpreter, cached for five minutes; if a probe cannot run, the check is skipped and
the launch proceeds as before.

- **`rl_library` not installed.** `isaaclab__install_info`'s `rl_libraries` check (importlib finding
  the package behind each library) is applied to the chosen library, and the refusal names the
  installed ones.
- **`--video` without its packages.** MoviePy and imageio-ffmpeg must be importable in the Lab
  interpreter; the refusal says `pip install moviepy imageio-ffmpeg` for that interpreter.
- **A task id this checkout does not register.** When no `ISAACLAB_TASK_MODULES` are configured,
  the ids in the checkout's `source/isaaclab_tasks` text are read (the same source scan
  `isaaclab__envs_list` uses) and an id whose family is there but whose exact name is not is refused
  with close matches. An id from an unscanned family is left to Lab, and configured task modules
  disable the check entirely, because they can register any id.

## Job model and state

- State directory: `~/.local/share/isaac-mcp/jobs` (or `ISAACLAB_JOBS_DIR`).
- Each job writes `job.json`, `stdout.log`, `stderr.log`; an `isaaclab__eval` job also writes
  `eval_result.json` (atomically) when its scoring runner finishes. Final status on exit is one of
  `succeeded`, `failed`, `cancelled`, `timeout`.
- Job ids: `YYYYMMDD-HHMMSS-<slug>` of the task, with `-2`, `-3`, ... if two launches land
  in the same second.
- The host keeps an in-memory map of active `Popen` handles; on server restart, records are
  rehydrated and dead "running" jobs take their recorded exit code, or `failed` with note "host restarted" if they left none.
- `runs_cancel` records `cancelled` before signalling, so the watcher does not overwrite it.
  Cancelling an already-finished job is a no-op.
- Subprocesses are launched with `shell=False`, an explicit argv list, and
  `start_new_session=True`, so the whole process group can be signalled. The child inherits a
  copy of the host environment plus `OMNI_KIT_ACCEPT_EULA=YES`, `PYTHONUNBUFFERED=1`, and
  `CUDA_VISIBLE_DEVICES` when `device` is set.

## Security and trust boundaries

- Jobs invoke `train.py`/`play.py` with `shell=False` and a validated argument list.
  Checkpoint paths are resolved against the Isaac Lab root and the jobs directory; metrics
  paths against the allowed roots.
- `extra_args` is syntactically validated (flags and Hydra overrides) but is not a semantic
  allow-list of safe flags.
- The child process inherits a copy of the host environment; do not assume credentials or
  other environment variables are removed.
- Custom task modules (`ISAACLAB_TASK_MODULES`) execute Python when imported and must be
  trusted. Lab tools provide no `run_python` equivalent.

## Verified vs. version and platform limitations

See the [compatibility matrix](COMPATIBILITY.md) for simulator and Isaac Lab version pairings and the scope of each test.

The later [self-contained Lab reproduction](REPRODUCIBILITY.md#self-contained-lab-3-reproduction)
at exact MCP `c2ceb7f` / official Lab `e3f7d9f` exercised all 12 tools with 114 retained
calls (111 workflow plus three cleanup) and 16 assertions. Separate mode/video and cleanup
records preserve their own counts. It used Lab 3.0 / Sim 6.2 / Newton MJWarp without an
inherited `.pth`, but retains two `fsspec`/`urllib3` metadata conflicts. The guide links exact
profile/lock/script inputs and distinguishes measured sync from an unexecuted locked replay.
It is not generic Sim Newton qualification or a clean-machine/full-installer claim.

Historical verification includes:

- Host suite: 421 tests passed in GitHub Actions for `66e84b4` (2026-10-30). An isolated host wheel install and
  npm launcher fetch also worked in the prior audit.
- Isaac Lab 3.0.0 / Isaac Sim 6.2.0.0 on Ubuntu 24.04 (RTX 5080 16 GB) and Ubuntu 22.04
  (RTX PRO 6000 Blackwell): a real 2-epoch `Isaac-Reach-UR10` rl_games run through
  `isaaclab__train` succeeded; `runs_status`, checkpoint discovery and TensorBoard metrics
  read it back. A custom `UR5e-Cell-Reach-Direct-v0` task was discovered (206 envs listed)
  and trained through the job layer.
- Windowed `isaaclab__play` opened "Isaac Lab 3.0.0"; status reported `window`; a screenshot
  and a 40-frame recording were captured. Viewport is black until "Completed setting up the
  environment".
- Job guards (job-vs-job, Kit-bridge, release after cancel) behaved as documented on the
  RTX 5080 (2026-10-30); four bugs were fixed in that pass.
- Isaac Lab 3 has no `--headless` flag. Ordinary headless jobs pass `--viz none`.
  With `extra_args=["--video", "--video_length=60"]`, headless jobs use `--viz kit`
  and child-only `HEADLESS=1` so the renderer can produce frames without a window.
  Windowed jobs select Kit and `HEADLESS=0`. These settings do not change the host
  environment or another job. One explicit `--viz=VALUE` or `--visualizer=VALUE`
  extra argument overrides the generated selection and is forwarded once, with a
  response hint. Non-Kit visualizers retain their own Lab display configuration.
  Duplicate/missing visualizer values and explicit `--viz=none` with `--video`
  are refused before launch; other invalid backend choices fail in the job log.
- Lab's optional video dependencies belong in the **Lab interpreter**, not just
  the MCP host. The inspected Lab 3 launcher requires MoviePy 1.x
  (`moviepy>=1.0.3,<2.0.0.dev0`) and `imageio-ffmpeg` for `--video`. They are not
  required for plain headless training. Use the Lab checkout's documented `video`
  extra or install these into a separate Lab environment; `install.sh --no-sim`
  does not install or repair that environment. A job can start successfully and
  later fail for a missing dependency; inspect `runs_status` and `runs_tail`.
  The RL backend is chosen with `--rl_library`; when omitted, Isaac Lab 3.0.0 uses the task's registered `default_agent` if it has one; otherwise Lab exits 2 with "the following argument is required: --rl_library", which a misspelled task id also triggers.
  Name the library when you load a checkpoint: a task default of `rsl_rl` applied to an `rl_games`
  checkpoint fails inside the backend with `KeyError: 'actor_state_dict'`. `isaaclab__eval` and `isaaclab__play` therefore
  use the library named by a checkpoint under `logs/<library>/` when you pass none, and report it as
  `rl_library_from_checkpoint_path`. `--num_episodes` was
  dropped because only the rlinf backend has it. `extra_args` accepts flags and Hydra
  overrides; rl_games needs `minibatch_size` to divide `num_envs` times the task's horizon.
- Runs land under `<isaaclab_root>/logs/...`, not the job dir; `runs_status` finds the newest
  checkpoint from the training log and reports the experiment dir as `run_dir`.

Limitations and not verified:

- The historical host/smoke audit did not run training; later native runs above did.
  Long runs above ~15 min or above 1024 envs on the 5080 remain unverified.
- The host server requires Python >=3.10; the verified interpreter is Python 3.12. The Lab
  install is pinned to Isaac Lab 3.0.0 / Isaac Sim 6.2.0.0.
- Other operating systems, GPUs, Isaac Sim versions and installation layouts are not
  supported claims. A prior isolated test of the generic bridge in Isaac Sim 5.1 is not proof
  that the complete twin and Lab stack support 5.1.
- The Kit guard only probes the configured port plus advertised portfiles; two separate host
  processes can still race the guard.
- `metrics_summary` returns no scalars for a run too short to log any. Measured on Lab 2.3.2: `sb3` logs every
  `--log_interval` timesteps (default 100,000), `skrl` writes no event file when `max_iterations` is very small, and
  `rl_games` wrote nothing at 3 epochs. For a short `sb3` run, pass `extra_args=["--log_interval=1000"]`.
- `randomize_single_object` (UR5e twin, not Lab) can fail with its fixed narrow strip; not
  relevant to Lab tools but noted for completeness.
- The npm/PyPI published install paths are not verified.
- The `tensorboard` extra must be installed for metrics readout; otherwise those two tools
  return an install hint.

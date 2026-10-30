# Isaac Lab 3.0 Tool Pack Design

Design for an `isaaclab.*` tool pack for the `isaac-mcp` host server. Grounded in
`mcp/isaac_mcp/server.py`, `exts/isaac.mcp.bridge/isaac_mcp_bridge/bridge.py`,
`mcp/docs/isaac-lab.md`, `mcp/examples/isaaclab_bridge_launch.py`,
`docs/ARCHITECTURE.md`, and the reference install at
`learning6/` (Isaac Lab 3.0.0, Isaac Sim 6.2.0.0, Python 3.12).

## 1. Execution model decision

Isaac Lab standalone scripts own the Kit app via `AppLauncher` and their own main
loop (`learning6/IsaacLab/scripts/reinforcement_learning/train.py` calls
`run_train_cli`). The bridge doc notes they are a separate Kit process that must
be pumped by `env.step()`/`sim.step()` (`mcp/docs/isaac-lab.md`).

Two candidate drivers:

1. **Long-lived worker process** the host spawns and talks to (TCP bridge on
   port 8767 per `isaaclab_bridge_launch.py` convention).
2. **One-shot subprocess jobs** for train/eval/play, with log tailing.

**Decision: (2) by default, with (1) only as an opt-in interactive mode.**

Justification:
- A long-lived worker pins a full GPU context and a Kit process for the whole
  session, even for cheap queries. On a single-GPU box that is the same GPU the
  running Isaac Sim app uses for rendering/physics, so it invites OOM and
  serialization (see section 8).
- Training/eval already run to completion and write artifacts (run dirs under
  `logs/rl_games/.../run.json`, checkpoints `*.pth`, TensorBoard event files).
  Tailing files gives progress without holding a second Kit process.
- Cheap queries (env list, checkpoint list, metric readout, install info) can be
  satisfied from the filesystem plus short-lived subprocesses that exit
  immediately; only `envs.list` needs Isaac Lab's Python.
- The bridge-in-Lab template (`isaaclab_bridge_launch.py`) remains supported
  for interactive use: a user can `use_bridge(8767)` to reach a running Lab app's
  own tools. The `isaaclab.*` pack itself does not depend on that.

## 2. Where the tools live

Unlike `ur5e_dt.*`, which are registered inside Kit via `OwnerRegistrar`
(`bridge.py`), `isaaclab.*` are **host-side** MCP tools registered directly on
the FastMCP server, like the screen tools (`register_screen_tools` in
`server.py`). They spawn and manage subprocesses; no Kit process is required for
the host to expose them. Names use dots on the host (MCP allows... actually MCP
tool names cannot contain dots per `server.py`), so they are exposed as
`isaaclab__<tool>` consistent with the `.` -> `__` rule in
`docs/ARCHITECTURE.md` section 4.

## 3. Job model

State is kept in a jobs directory (default `~/.local/share/isaac-mcp/jobs`,
configurable via `ISAACLAB_JOBS_DIR`). Each job writes:
- `job.json` (id, kind, command, pid, started_at, status, log_path, run_dir)
- `stdout.log`, `stderr.log` (subprocess stdio)
- final status updated on exit (`succeeded`/`failed`/`cancelled`/`timeout`)

Job IDs are `YYYYMMDD-HHMMSS-<slug>`. The host holds an in-memory map of active
`Popen` handles; on server restart, `jobs.list` rehydrates from `job.json`.

## 4. Proposed tools

All inputs/outputs JSON. Durations in seconds.

### `isaaclab.install.info`
- in: none
- out: `{python, isaaclab_version, isaacsim_version, isaaclab_root, venv}`

### `isaaclab.envs.list`
- in: `{filter?: string}`
- out: `{envs: [{id, entry_point}]}` (short-lived subprocess; 60s timeout)

### `isaaclab.train`
- in: `{task: str, num_envs?: int=64, max_iterations?: int, seed?: int,
  device?: str, headless?: bool=true, checkpoint?: path, rl_library?: str, extra_args?: [str], timeout_s?: float}`
- out: `{job_id, log_dir, command}` (returns immediately; job runs async)

### `isaaclab.eval`
- in: `{task, checkpoint: path, num_envs?: int, headless?: bool=true, rl_library?: str, extra_args?: [str]}`
- out: `{job_id, log_dir, command}`

### `isaaclab.play`
- in: `{task, checkpoint: path, num_envs?: int=1, headless?: bool=false, rl_library?: str}`
- out: `{job_id, log_dir, command}` (interactive; usually stopped by user/cancel)

### `isaaclab.runs.list`
- in: `{state?: "running"|"finished"|"all"}` (default all)
- out: `{runs: [{job_id, kind, task, state, started_at, elapsed_s, log_dir}]}`

### `isaaclab.runs.status`
- in: `{job_id}`
- out: `{state, pid_alive, elapsed_s, exit_code?, last_log_lines: [str],
  latest_checkpoint?: path, run_dir?}`

### `isaaclab.runs.tail`
- in: `{job_id, stream?: "stdout"|"stderr" (default stdout), lines?: int=100}`
- out: `{lines: [str]}`

### `isaaclab.runs.cancel`
- in: `{job_id, grace_s?: int=10}`
- out: `{cancelled: bool, state}` (SIGTERM then SIGKILL process group)

### `isaaclab.checkpoints.list`
- in: `{run_dir?: path, task?: str}`
- out: `{checkpoints: [{path, step?, size_bytes, mtime}]}` (filesystem only)

### `isaaclab.metrics.summary`
- in: `{run_dir: path, tags?: [str]}`
- out: `{scalars: {tag: {last, min, max, step, count}}}` (reads tfevents)

### `isaaclab.tensorboard.tail`
- in: `{run_dir, tag, last_n?: int=50}`
- out: `{points: [{step, value, wall_time}]}`

## 5. Cheap queries vs long jobs

- Cheap (no subprocess or short-lived): `install.info` (cached after first probe),
  `runs.*`, `checkpoints.list`, `metrics.summary`, `tensorboard.tail`. These read
  files or job state only.
- Long (subprocess): `train`, `eval`, `play`, `envs.list` (needs Lab Python import).
  All return a `job_id` immediately and never block the MCP call beyond spawning.

`runs.tail` defaults to 100 lines and caps at 1000 to bound output.

## 6. Progress reporting

- Log files: `runs.tail` + `runs.status.last_log_lines`.
- TensorBoard: `metrics.summary` / `tensorboard.tail` read the `events.out.tfevents.*`
  files with the `tensorboard` Python package (available in `learning6/.venv`).
- Checkpoints: `runs.status` globs `*.pth`/`*.pt` in `run_dir/nn` and reports the
  newest; `checkpoints.list` enumerates them. The training library's own
  `run.json` (see `logs/rl_games/.../run.json`) gives task/library metadata.

## 7. Failure and timeout handling

- Subprocess launch failures (missing Python, import error) are captured into
  `stderr.log` and the job is marked `failed` with `exit_code`.
- Cheap queries have a 60s subprocess timeout; on timeout the process is killed
  and an error string returned (matching `BridgeError`-style reporting in
  `server.py`).
- Long jobs have no hard timeout by default; an optional `timeout_s` may be
  passed. A watchdog thread marks `timeout` and kills the process group if the
  deadline passes. Partial logs remain readable via `runs.tail`.
- `runs.cancel` sends SIGTERM to the process group, waits `grace_s`, then
  SIGKILL. Status becomes `cancelled`.
- Nonzero exit: `runs.status` returns `exit_code` and the last 20 log lines.

## 8. GPU contention with a running Kit app

Isaac Sim 6.2 (the `ur5e-dt` bridge) and Isaac Lab training both need CUDA. On
one GPU they will contend and likely OOM. Rules:
- `train`/`eval`/`play` refuse to start if a Kit bridge is reachable on the
  configured port (default 8766) **unless** the caller passes
  `allow_concurrent_gpu=true` (default false). The check uses the same
  `BridgeClient` used by `server.py`.
- When allowed, the job sets `CUDA_VISIBLE_DEVICES` per the `device` argument
  (default: inherit; user can pin e.g. `1`).
- `headless=true` is the default for train/eval to avoid a second RTX
  renderer competing for VRAM; `play` defaults headless=false but warns.
- Documented expectation: stop the Kit app (or use a second GPU) before
  training. No automatic eviction of the sim.

## 9. Locating an Isaac Lab install

Resolution order (first valid wins):
1. `ISAACLAB_PYTHON` (absolute path to python) + `ISAACLAB_ROOT`.
2. `ISAACLAB_ROOT` with `.venv/bin/python` (reference layout
   `learning6/`: python 3.12, `IsaacLab/` at `learning6/IsaacLab`).
3. `which isaaclab.sh` / `ISAACLAB_PATH` env (upstream convention).

4. Probe `python -c "import isaaclab, isaacsim; print(...)"` and read
   `isaaclab.__version__` (expect `3.0.0`) and `isaacsim` metadata
   (expect `6.2.0.0`).

Custom task packages: `isaaclab__envs_list` imports `isaaclab_tasks` plus every module in
`ISAACLAB_TASK_MODULES` (comma list, e.g. `ur5e_lab`), with `ISAACLAB_TASK_PATH` (os.pathsep list) added to
`PYTHONPATH`, then reads `gymnasium.registry`. Failed imports are returned as `import_errors`. If the variable is unset,
a `ur5e_lab` directory beside `ISAACLAB_ROOT` is still picked up.

Validation: require Python 3.12 and `isaaclab` importable; refuse otherwise with
the resolved path in the error. Reference paths are never hard-coded as defaults
in code; they are documentation/examples only.

## 10. Security

- No arbitrary code execution by default: no `run_python`-equivalent into the
  Isaac Lab process. Only allowlisted entrypoints (`train.py`, `play.py`,
  `list_envs.py`, etc.) are invoked, with validated, typed arguments.
- All paths are resolved and checked to lie under configured roots
  (`ISAACLAB_ROOT`, job dir, checkpoint roots); `..` traversal and absolute
  escapes are rejected.
- Subprocesses are launched with `shell=False` and an explicit argv list;
  `extra_args` is schema-validated against an allowlist of known flags.
- Environment variables passed to jobs are an explicit allowlist (PATH,
  CUDA_VISIBLE_DEVICES, PYTHONPATH for the Lab root); no host secrets are
  forwarded.
- The bridge's `run_python` builtin (`bridge.py` builtins) is not exposed
  through this pack; if a Lab bridge is attached via `use_bridge`, that is a
  separate, user-opt-in surface governed by `ISAAC_MCP_BUILTINS`.

## 11. Out of scope (this design)

- No actual implementation, no Isaac Sim/GPU jobs run.
- No multi-node/multi-GPU orchestration beyond `CUDA_VISIBLE_DEVICES`.
- No dataset recording or motion-capture tools.
- No changes to `ur5e-digital-twin`; this doc lives only in `isaac-mcp`.

## 12. Implementation notes (verified 2026-10-29 on Isaac Lab 3.0.0 / Isaac Sim 6.2.0.0)

Implemented in `mcp/isaac_mcp/isaaclab/` (`install.py`, `jobs.py`, `metrics.py`, wired by `tools.py`; tools are
exposed as `isaaclab__<tool>`). A real 2-epoch `Isaac-Reach-UR10` rl_games run through `isaaclab__train` succeeded,
and `runs_status` / `metrics_summary` read its checkpoint and TensorBoard scalars. Things the real run taught us:

- Isaac Lab 3 has **no `--headless` flag**. Headless jobs pass `--viz none`, windowed ones `--viz kit` (matches `learning6/README.md`).
- The RL backend is chosen with `--rl_library` (else the task's registered default); `episodes` was dropped because
  only the rlinf backend has `--num_episodes`.
- `extra_args` accepts `--flags` **and Hydra overrides** (`agent.params.config.minibatch_size=1536`, `+key=v`, `~key`).
  rl_games needs `minibatch_size` to divide `num_envs * 24`, so small `num_envs` runs need that override.
- Runs live under `<isaaclab_root>/logs/...`, not the job dir; `runs_status` finds the newest checkpoint from the
  training log ("saving checkpoint '...'") and reports the experiment dir as `run_dir`.
- Job ids are validated (`YYYYMMDD-HHMMSS-<slug>`), and cancel only signals a pid whose command line still matches the job.
- `envs_list` scans registrations statically; custom envs registered by user code (e.g. `ur5e_lab`) are not listed yet.

### Windowed jobs and visual feedback (verified 2026-10-29)

`isaaclab__play` (or train/eval with `headless=false`) opens an Isaac Lab window (`--viz kit`), titled "Isaac Lab 3.0.0".
The host's screen tools find it (`AUTO_WINDOWS` in `mcp/isaac_mcp/screen.py` matches "Isaac Sim" and "Isaac Lab" titles),
capture it even when other windows cover it, and can record video. `isaaclab__runs_status` adds a `window` entry
(id, title, size) for a live windowed job so an agent can call `screenshot(window_id=...)` /
`start_screen_recording(window_id=...)` on its own. The window shows a black viewport until the log line
"Completed setting up the environment"; the launch response carries a hint saying so. A real run captured a 1440x900
screenshot and a 40-frame video of four UR10 arms.

Note: the stock `Isaac-Reach-UR10` task's window reads "Physics: Newton MJWarp". The twin's own `ur5e_lab` tasks configure
PhysX directly (see `learning6/README.md`), so check the physics backend in the window before trusting results from stock tasks.

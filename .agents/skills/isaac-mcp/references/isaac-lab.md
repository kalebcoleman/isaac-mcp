# Isaac Lab through isaac-mcp

Read this when you are about to start, monitor or judge an Isaac Lab job. The full parameter
reference is `docs/TOOLS-ISAACLAB.md` in the isaac-mcp repository.

## What the tools run

The `isaaclab__*` tools live in the host server, not in Kit. They run the Isaac Lab interpreter
named by `ISAACLAB_PYTHON` against the checkout at `ISAACLAB_ROOT`:

| Tool | Runs | Notes |
|---|---|---|
| `isaaclab__train` | `scripts/reinforcement_learning/train.py` | `headless=true` by default; ordinary jobs use `--viz none`, video jobs retain a windowless Kit renderer |
| `isaaclab__eval` | `scripts/reinforcement_learning/play.py --checkpoint ...` | Isaac Lab has no separate eval script; headless by default |
| `isaaclab__play` | `scripts/reinforcement_learning/play.py --checkpoint ...` | Opens a window by default (`--viz kit`), `num_envs=1` |

Each launch returns `{job_id, log_dir, command}`. Jobs survive the MCP call and are tracked under
`~/.local/share/isaac-mcp/jobs` (or `ISAACLAB_JOBS_DIR`). If the host restarts, a dead job is
reported as `failed` ("host restarted") on the next read.

## Isaac Lab 3 conventions to get right

These were checked against an Isaac Lab 3.0.0 checkout and NVIDIA's own skills:

- Task ids have no version suffix: `Isaac-Reach-Franka`, `Isaac-Cartpole`. Use
  `isaaclab__envs_list` with a `filter` substring, never guess.
- The RL backend is chosen with `rl_library` (Isaac Lab 3.0.0 accepts `rsl_rl`, `rl_games`, `skrl`,
  `sb3`, `rlinf` and `torchrl`). When omitted,
  the task's registered `default_agent` is used; a task without one (or a misspelled task id) exits 2
  asking for `--rl_library`. Name the library when loading a checkpoint: another library's default
  fails inside the backend (`KeyError: 'actor_state_dict'`). Do not mix one library's agent config with another.
- There is no `--headless` flag in Isaac Lab 3. Ordinary headless jobs use `--viz none`;
  `extra_args=["--video", "--video_length=60"]` selects Kit with child-only `HEADLESS=1`.
  Windowed jobs use Kit with `HEADLESS=0`. One explicit `--viz=VALUE` or
  `--visualizer=VALUE` overrides the generated selection; non-Kit display settings belong
  to that visualizer. Explicit `none` plus video is refused. See `docs/TOOLS-ISAACLAB.md`.
- Video needs optional MoviePy 1.x and imageio-ffmpeg in the selected **Lab interpreter**.
  A returned job id is not successful completion: check `runs_status` and `runs_tail`,
  allow renderer startup time, and inspect recorded pixels or the actual window.
- `extra_args` takes `--flag` / `--flag=value` and Hydra overrides such as
  `env.actions.joint_effort.scale=10.0` or `agent.seed=2024` (RL-Games uses `agent.params.seed`),
  plus `+key=value` or `~key`. See `docs/source/features/hydra.rst` in the Lab checkout. The
  syntax is checked, not the meaning: make sure the key exists in that task's config.
- Environments are `@configclass` dataclasses built on `ManagerBasedRLEnvCfg` (manager-based) or
  `DirectRLEnvCfg` (direct), registered with `gymnasium.register(...)` and an
  `env_cfg_entry_point`. Upstream NVIDIA documents the current import paths in
  `skills/README.md` of the IsaacLab repository; check there rather than inventing module paths.
- Upstream Isaac Lab now recommends `uv run isaaclab train` / `uv run isaaclab play` from a checkout.
  The MCP tools still call the `scripts/reinforcement_learning/` entry points, which exist in
  3.0.0. If a future Lab release removes them, `isaaclab__train` will fail with a missing-script
  error: report that instead of hand-running commands.

## Custom tasks

`isaaclab__envs_list` imports `isaaclab_tasks` plus the comma-separated modules in
`ISAACLAB_TASK_MODULES`, with `ISAACLAB_TASK_PATH` added to `PYTHONPATH`. The same variables are
forwarded to jobs. Set them in the MCP server's environment (client config), then restart the
connection. Importing a task module runs its code, so only add modules the user trusts.

Per call instead: `python_path` (directories for `PYTHONPATH`), `env` (extra variables; `PATH`, `LD_*`, `PYTHON*`
are refused) and `external_callback` (`module.function`, passed as `--external_callback=`) on `envs_list`, `train`,
`eval`, `play`, `env_check` and `record`. `ISAACLAB_EXTERNAL_CALLBACK` sets a default. An empty `envs_list` result
carries a `hint`. `runs_list` returns the newest 25 jobs (`total`, `truncated`); filter with `task`, `kind`, `state`, `since`, `limit`.

## Guards

- **Job guard**: one Isaac Lab job at a time. A refusal includes `running_job_id`; wait for it, cancel
  it (only with the user's agreement), or pass `allow_concurrent=true`.
- **Kit-bridge (GPU) guard**: a running Kit bridge blocks train, eval and play because both use the
  GPU. A refusal lists `kit_bridges`. Ask the user before passing `allow_concurrent_gpu=true`.
- `timeout_s` makes a watchdog kill the job's process group; use it for smoke runs.

## A smoke run, step by step

1. `isaaclab__install_info` and confirm `isaaclab_version`.
2. `isaaclab__envs_list` `{"filter": "Reach"}` and choose the id.
3. `isaaclab__train` `{"task": "<id>", "num_envs": 32, "max_iterations": 20, "timeout_s": 900}`.
4. `isaaclab__runs_status` `{"job_id": "<job_id>"}` until `state` is no longer `running`. Report
   progress between checks instead of polling in a tight loop.
5. On failure: `isaaclab__runs_tail` `{"job_id": "<job_id>", "stream": "stderr", "lines": 200}`.
   Classify the failure (install, task import, config, GPU memory, runner) before changing anything.
6. On success: scale `num_envs` / `max_iterations` only after the user agrees.

## Reading results

- `isaaclab__runs_status` gives `run_dir` and `latest_checkpoint`.
- `isaaclab__metrics_summary` `{"run_dir": "<run_dir>"}` lists every scalar with last, min, max,
  step and count. Unknown tags return the `available` list. Then `isaaclab__tensorboard_tail` for
  the curve of one tag.
- `isaaclab__checkpoints_list` `{"run_dir": "<run_dir>"}` lists `*.pt` / `*.pth`, newest first.

Judge a run the way NVIDIA's `isaaclab-debugging-rl-training` skill recommends:

- A rising reward is not proof the task improved. Compare reward terms with the task's own metrics
  (success rate, distance, and so on) where the task logs them.
- Change one variable per experiment and record task, library, seed, backend and overrides.
- Choose checkpoints by task metrics and rollout behaviour, not by reward alone.
- For checkpoint load errors, compare observation and action sizes with the training config
  before editing policy code.

## Watching a policy

`isaaclab__play` opens a window. `isaaclab__runs_status` reports `window: {id, title, width,
height}` once it is up. Take `screenshot` `{"window_id": <id>}`, or record with
`start_screen_recording` `{"window_id": <id>, "max_seconds": 20, "full_screen": false}` followed
by `stop_screen_recording`. Stop play with `isaaclab__runs_cancel` when done; it is interactive and
does not exit on its own.

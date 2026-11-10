# PolicyIK usage gaps (Isaac Lab tools)

Found on 2026-11-10 reproducing PolicyIK (an external `rl_games` project) through the Isaac Lab tools on host
commit `cebd726`, Isaac Sim 6.2.0.0 and Isaac Lab 3.0.0. Each entry says what failed, what was changed and where it is
documented. The arguments are in [TOOLS-ISAACLAB.md](TOOLS-ISAACLAB.md#tasks-from-an-external-project).

| # | Gap | Status | Fix |
|---|---|---|---|
| 1 | `external_callback` only worked for `rsl_rl` train/play; `rl_games` projects needed `env={"ISAACLAB_TASK_MODULES": ...}` | fixed | The server calls the callback itself for every other library and for `env_check`/`record` (`launcher.py`, `eval_runner.py`, `check_runner.py`); `rsl_rl` keeps Lab's own flag. New `task_modules` argument replaces the `env` workaround. |
| 2 | `train`/`eval`/`play`/`env_check`/`record` had no `cwd`, so relative data paths crashed | fixed | New `cwd` argument, validated like `run_script`'s. |
| 3 | `OMNI_KIT_ACCEPT_EULA` refused by `play` but accepted by `run_script` | fixed | One shared `install.validate_env` for every Isaac Lab tool. `OMNI_KIT_ACCEPT_EULA`, `CUDA_VISIBLE_DEVICES` and `HEADLESS` are accepted everywhere; `PATH`, `HOME`, `LD_*`, `PYTHON*`, `ISAAC_MCP_*` and the server's own settings are refused everywhere. |
| 4 | `play` refused a checkpoint outside the Lab and jobs roots | fixed | Also allowed: folders passed as `python_path` or `cwd` in the same call, and `ISAACLAB_CHECKPOINT_ROOTS`. The refusal names every allowed root. Symlinks that escape are still refused. |
| 5 | `eval` cannot run a project's own evaluator | documented, partly deferred | `run_script` is the documented route. A metrics-file convention for `metrics_summary` is not added: it needs a format decision. |
| 6 | No progress signal in `runs_status` | fixed | A line `ISAAC_MCP_PROGRESS: <fraction\|percent%\|done/total> [text]` is reported as `progress`. |
| 7 | `run_script` jobs report the job folder as `run_dir` | documented | Pass the real run folder to `metrics_summary`/`checkpoints_list`. |

## Added in the same round

| # | Gap | Status | Fix |
|---|---|---|---|
| 8 | Hard-bound to the harness's Isaac Lab 3.0.0: `ISAACLAB_PYTHON`/`ISAACLAB_ROOT` are protected and win over project files, so Lab 2.3.2 / Sim 5.1 could not be targeted without restarting the server | fixed | Optional `install` argument on `train`, `eval`, `play`, `env_check`, `record`, `run_script`, `envs_list` and `install_info`: a known name or version, or an absolute venv path. Known installs come from the default's sibling environments, the workspace `envs/`, `ISAACLAB_INSTALLS` and `isaac_env.py`. A project's `isaac-mcp.json` can set `isaaclab_install` (only via `ISAAC_MCP_PROJECT`). 3.0.0 stays the default. |

## Audit of the rest of the tool surface

Fixed here (clear and low risk):

| Finding | Fix |
|---|---|
| `run_script` accepted relative or missing `python_path` entries that the other tools refuse | One shared `install.validate_python_path`. |
| `metrics_summary`, `checkpoints_list`, `tensorboard_tail` could not read a run under a project or scratch folder that `play` accepts checkpoints from | `ISAACLAB_CHECKPOINT_ROOTS` and the logs of every known install are allowed roots there too. |
| Their refusal and the "no install" error did not say how to fix it | They name `ISAACLAB_CHECKPOINT_ROOTS`, `install` and `ISAACLAB_INSTALLS`; a protected-`env` refusal says to set it in the server's environment. |
| Nothing showed which install a launch used | A successful launch returns `install`. |
| Docs said `env_check`/`record`/non-`rsl_rl` libraries refuse `external_callback`, that `CUDA_VISIBLE_DEVICES`/`HEADLESS` are refused, and that checkpoints must sit under Lab or jobs only | Corrected in `TOOLS-ISAACLAB.md`, `ENVIRONMENT.md`, `COMPATIBILITY.md` and the agent skill. |

Left for later, with a suggested fix:

| Finding | Suggested fix |
|---|---|
| `isaaclab__preflight`, `runs_list` and the GPU-hour ledger use the default install only | Accept `install`, or read it from each job record (jobs do not record which install ran them yet). |
| `isaaclab__checkpoint_register` takes any file path under any folder (no root check) while `play` is root-checked | Decide one policy: either register only under the shared roots, or document it as a pure bookkeeping tool. |
| Framework coverage is uneven: only sb3 `.zip` is found by `checkpoints_list` (and only under a path containing `sb3`); `metrics_summary` reads TensorBoard scalars only, so rl_games `summaries/` and skrl `runs/` need checking on a real run | Verify rl_games, skrl and sb3 on Lab 2.3.2 through the tools (the compatibility page says they ran tiny jobs) and widen the file-name rules from what they write. |
| `isaaclab__eval` has no `external_callback`-independent way to name the evaluator's metric file | Pick a `metrics.json` convention beside `ISAAC_MCP_ARTIFACT:` (gap 5). |
| `used_checkpoint` of `eval`/`play` for rl_games on Lab 3 (`--checkpoint` handling) is untested here | Add a fake-Lab test per library once a real run confirms the flags. |

Room is left below for the UR5e Jenga round.

## UR5e Jenga round

_To be added._

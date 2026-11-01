---
name: isaac-mcp
description: Operate NVIDIA Isaac Sim and Isaac Lab through the isaac-mcp server (tools named refresh_tools, use_bridge, run_python, status, screenshot, isaaclab__*, ur5e_dt__*, sim__*, window_*). Use when asked to inspect or change a running Isaac Sim scene, start or check an Isaac Lab training, eval or play job, read TensorBoard metrics or checkpoints, capture the simulator window, or work out why Isaac tools are missing from the client.
---

# Operating Isaac Sim and Isaac Lab with isaac-mcp

`isaac-mcp` is one stdio MCP server, usually registered under the name `isaac`. Some clients add a
prefix to tool names (Claude Code shows `mcp__isaac__refresh_tools`); the names below are the bare
tool names. If none of these tools are listed, the server is not configured in this client: see
`docs/MCP-SERVER.md` in the isaac-mcp repository and stop rather than improvising shell commands.

This skill covers how to drive the tools safely. For Isaac Lab and Isaac Sim domain knowledge
(writing environments, rewards, USD assets, sensors), route to NVIDIA's official skills listed in
[references/official-skills.md](references/official-skills.md).

## Ground rules

1. **Ask before GPU work.** `isaaclab__train`, `isaaclab__eval`, `isaaclab__play` and `sim__launch`
   start long-running GPU processes. Start one only when the user asked for it, and run one GPU job
   at a time on a single card (16 GB fits a small `num_envs`, not a large sweep).
2. **Never start a second Kit when a bridge is reachable.** Discover first (step 2 below).
3. **Verify the target before changing a scene.** Confirm the bridge's PID and port with `status`
   before `run_python`, file writes or any `ur5e_dt__*` call that changes the stage.
4. **Look, don't ask.** When a window is open (play, a GUI Sim), take a `screenshot` or a short
   recording yourself and decide the next step from it.
5. **Report tool results as they are.** Every tool returns JSON; failures come back as
   `{"error": ..., "hint": ...}`. Quote the error and follow the hint before retrying.

## First calls in a session

1. `refresh_tools`, which rediscovers bridge tools. A client that connected before Kit started
   lists only host and Isaac Lab tools until this runs.
2. Find bridges. Use `list_bridges` if it is listed. Otherwise run the CLI in a shell
   (`python -m isaac_mcp.client --list-bridges`), which works even when bridge built-ins are hidden
   by `ISAAC_MCP_BUILTINS=0` or `ISAAC_MCP_NAMESPACE`. It exits 2 with `[]` when none is advertised.
3. With several bridges, call `use_bridge` with the listed port, then `status`, and check that the
   PID and app version are the process you mean.
4. For Isaac Lab work, call `isaaclab__install_info`. An error here means `ISAACLAB_PYTHON` /
   `ISAACLAB_ROOT` are not set in the server's environment; fix the client config, not the code.

If a tool you expect is missing, it is usually filtered rather than broken:
`ISAAC_MCP_LOCAL_TOOLS` selects host groups (`window_*` needs `screen_input`, `sim__*` needs
`sim_launch`), and `ISAAC_MCP_NAMESPACE` / `ISAAC_MCP_BUILTINS` filter bridge tools.

## Isaac Lab: train, watch, evaluate

Details and parameter tables: [references/isaac-lab.md](references/isaac-lab.md).

1. `isaaclab__envs_list` with a `filter` substring to find the task id. Isaac Lab 3 ids have no
   `-v0` suffix (for example `Isaac-Reach-Franka`).
2. Training refuses to start next to a running Kit bridge (the GPU guard). Ask the user whether to
   stop the Sim or to pass `allow_concurrent_gpu=true`; do not set it on your own.
3. Smoke test first: `isaaclab__train` with a small `num_envs` and `max_iterations`, and a
   `timeout_s`. Keep the returned `job_id`.
4. Poll with `isaaclab__runs_status`, and `isaaclab__runs_tail` (`stream="stderr"`) on failure.
   Do not busy-loop: check, report progress, check again later.
5. Read results with `isaaclab__metrics_summary` (`run_dir` from status) and
   `isaaclab__checkpoints_list`. Judge by task metrics, not reward alone.
6. `isaaclab__eval` (headless) or `isaaclab__play` (window) with the chosen checkpoint. For play,
   `isaaclab__runs_status` returns `window.id`; pass it to `screenshot(window_id=...)` or
   `start_screen_recording(window_id=...)`, then stop the job with `isaaclab__runs_cancel`.

## Isaac Sim: inspect and change a live scene

Details: [references/isaac-sim.md](references/isaac-sim.md).

- `run_python` runs on Kit's main thread with `omni`, `carb`, `Usd`, `UsdGeom`, `Sdf` and `Gf`
  preloaded. Assign `result` to return a JSON-serialisable value. Read first, then write.
- Prefer a pack's named tools (for example `ur5e_dt__play_scene`) over hand-written `run_python`
  when one exists; they carry the project's checks.
- `screenshot` with no selector auto-targets the Isaac Sim / Isaac Lab window, even if covered. For
  clean viewport video use the twin's `ur5e_dt__start_recording` instead of a screen recording.
- `put_file`, `get_file` and `stat_file` act on the bridge machine with Kit's permissions.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Only host and `isaaclab__*` tools listed | Connected before Kit, or bridge tools filtered | `refresh_tools`, then the `--list-bridges` CLI |
| `refresh_tools` reports connection refused | No bridge on that port | Check the listing; start Kit with `isaac.mcp.bridge` only if none is advertised and the user agrees |
| New tools not shown after refresh | Client ignores `tools/list_changed` | Reconnect the MCP server, or use `python -m isaac_mcp.client list_tools --port <port>` |
| Train/eval/play refused with `kit_bridges` | GPU guard | Ask: stop the Sim, or pass `allow_concurrent_gpu=true` |
| `path outside allowed roots` | Metrics/checkpoint path not under the Lab or jobs roots | Use `run_dir` from `isaaclab__runs_status`, or set `ISAACLAB_LOGS_DIR` |
| `envs_list` shows `import_errors` | A task module failed to import | Read the error; custom tasks need `ISAACLAB_TASK_MODULES` / `ISAACLAB_TASK_PATH` |
| Metrics tools ask for the `isaaclab` extra | `tensorboard` not installed in the server env | `uv tool install --force --editable './mcp[isaaclab]'` from the repo |

## Reference

Tool references live in the isaac-mcp repository under `docs/`: `TOOLS-HOST.md` (gateway, screen,
input, sim sessions, bridge built-ins), `TOOLS-ISAACLAB.md`, `TOOLS-UR5E.md`, and `MCP-SERVER.md`
for client setup. `AGENTS.md` at the repository root gives the tier map and current tool counts.

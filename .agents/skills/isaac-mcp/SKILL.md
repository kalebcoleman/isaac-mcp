---
name: isaac-mcp
description: Operate NVIDIA Isaac Sim and Isaac Lab through the isaac-mcp server (tools named refresh_tools, use_bridge, run_python, status, screenshot, isaaclab__*, ur5e_dt__*, sim__*, assets__*, window_*). Use when asked to inspect or change a running Isaac Sim scene, import a project USD or URDF, inspect or drive joints and physics, start or check an Isaac Lab training, eval or play job, read TensorBoard metrics or checkpoints, capture the simulator window, or work out why Isaac tools are missing from the client.
---

# Operating Isaac Sim and Isaac Lab with isaac-mcp

`isaac-mcp` is one stdio MCP server, usually registered under the name `isaac`. Some clients add a
prefix to tool names (Claude Code shows `mcp__isaac__refresh_tools`); the names below are the bare
tool names. If none of these tools are listed, the server is not configured in this client: see
`docs/MCP-SERVER.md` in the isaac-mcp repository and stop rather than improvising shell commands.

This skill covers how to drive the tools safely. For Isaac Lab and Isaac Sim domain knowledge
(writing environments, rewards, USD assets, sensors), route to NVIDIA's official skills listed in
[references/official-skills.md](references/official-skills.md).

For setup, use `docs/LINUX-SETUP.md#choose-a-simulator-build` and `docs/COMPATIBILITY.md`: the
full installer selects Sim 6.2.0.0, while 6.0/6.1 tests used separately prepared environments.
Do not claim future releases or pending tests are supported. The Sim runtime supplies its own Kit
extensions, including HydraTexture; its `.pyi` API descriptions are not an extra package to install.
External system ROS and Isaac Lab are separate and only needed for the workflows that use them.
The supplied launcher enables its bundled ROS bridge even without an external ROS installation.

For ROS camera saves, `sim__stage__save` defaults `reset_camera_render_handles` to `false`.
An affected export reports `SAVE_UNSUPPORTED` with `properties` and `retry_with`; inspect these
before explicitly opting in to normalization with `true`. Successful exports report exact
`omitted_runtime_render_properties`. Ambiguous, authored or unavailable bindings still refuse.
This changes the private export only; it does not repair an already-stale scene or prove authorship.
Pause before saving and check the exported scene after reopening in a fresh process.

## Ground rules

0. **Use the Isaac Sim already on the machine; never download one unasked.** `sim__launch` and
   `run.sh` find the installed Isaac Sim 6.x (`python3 scripts/isaac_env.py find` lists them).
   Do not run `./install.sh --download-sim`, `uv sync`, `pip install isaacsim`, or `uv run`/`uv lock`/
   `uv add` in the repository root (they create a ~20 GB `.venv`), and do not
   replace or upgrade an existing Isaac Sim or Isaac Lab environment, unless the user explicitly
   asks. If none is found, ask where it is installed (`ISAACSIM_PATH` or `ISAAC_MCP_PYTHON`).
1. **Ask before GPU work.** `isaaclab__train`, `isaaclab__eval`, `isaaclab__play` and `sim__launch`
   start long-running GPU processes. Start one only when the user asked for it, and run one GPU job
   at a time on a single card (16 GB fits a small `num_envs`, not a large sweep).
2. **Discover before launching Kit.** Reuse the intended reachable bridge. Start an additional
   isolated session only when the user requested one and its resource checks pass.
3. **Verify the target before changing a scene.** Confirm the bridge's PID and port with `status`
   before `run_python`, file writes or any `ur5e_dt__*`, `sim__*` or `assets__*` call that changes
   the stage.
4. **Look, don't ask.** When a window is open (play, a GUI Sim), take a `screenshot` or a short
   recording yourself and decide the next step from it.
5. **Report tool results as they are.** Generic scene tools set MCP `isError` and return a `code`
   such as `STALE_TARGET`; legacy tools may return `{"status": "error", "message": ...}`
   or `{"error": ..., "hint": ...}`. Quote the error and
   follow the hint before retrying.

## First calls in a session

1. `refresh_tools`, which rediscovers bridge tools. Generic scene and UR5e catalog tools are
   listed even with Kit offline; execution still requires the selected bridge and extension.
2. Find bridges. Use `list_bridges` if it is listed. Otherwise run the CLI in a shell
   (`python -m isaac_mcp.client --list-bridges`), which works even when bridge built-ins are hidden
   by `ISAAC_MCP_BUILTINS=0` or `ISAAC_MCP_NAMESPACE`. It exits 2 with `[]` when none is advertised.
3. With several bridges, call `use_bridge` with the listed port, then `status`, and check that the
   PID and app version are the process you mean.
4. For Isaac Lab work, call `isaaclab__install_info`. Inspect the returned error: installation
   discovery, interpreter execution and package metadata are separate checks. Missing configuration
   is only one possible cause; valid automatic discovery needs no explicit variables. Confirm
   non-null Lab/Sim versions and the intended interpreter/root before task discovery. See
   `docs/TOOLS-ISAACLAB.md#locating-an-isaac-lab-install`; do not reinstall or rename an existing
   environment merely because a probe fails.
5. For generic scene or project-asset work, call `sim__stage__info` or `assets__roots`. The
   46-tool pack (38 `sim`, 8 `assets`) is on by default; `ISAAC_MCP_SCENE_TOOLS=0` hides it per server.
   `assets__roots`, `assets__scan` and `assets__search` run on the host; the rest need the
   `sim-tools` extension in the selected Kit. These tools are visibility-filtered, not an access
   boundary.

If a tool you expect is missing, it is usually filtered rather than broken:
`ISAAC_MCP_LOCAL_TOOLS` selects host groups (`window_*` needs `screen_input`; managed
`sim__launch`, `sim__list`, `sim__status` and `sim__stop` need `sim_launch`). The generic
`sim__*` / `assets__*` pack is separate. `ISAAC_MCP_NAMESPACE` / `ISAAC_MCP_BUILTINS` filter
bridge tools.

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
  Top-level `await` works, e.g. play, `for _ in range(60): await omni.kit.app.get_app().next_update_async()`,
  then read the pose in one call. Persistent sessions (`persistent=true`, `session_id`) keep the
  variables of successful calls only, and opening a stage (including `save_as_stage`) resets every
  session: the next call reports `session_reset`, so re-run your setup code.
- **Saving your work:** edits made with `sim__*` tools go to the session layer, which
  `omni.usd` `save_as_stage` does not write; export them with `sim__stage__save`, or make edits you
  want in a checkpoint file with `run_python` on the root layer. Stop the timeline before saving:
  while it plays, physics writes moved link poses back into the USD, so a "start" checkpoint can
  save the robot wherever it drove.
- **Demo videos:** lay windows out with `move_window` (for example Isaac Sim left, RViz 2 right),
  then record a monitor or one window with `start_screen_recording`. A window that closes can hand
  focus to another one, which then covers RViz; raise it again or record RViz's window by id.
- Prefer a pack's named tools (for example `ur5e_dt__play_scene`) over hand-written `run_python`
  when one exists; they carry the project's checks.
- `screenshot` with no selector auto-targets the Isaac Sim / Isaac Lab window, even if covered. For
  clean viewport video use the twin's `ur5e_dt__start_recording` instead of a screen recording.
- `put_file`, `get_file` and `stat_file` act on the bridge machine with Kit's permissions.

### Generic scene and project assets (`sim__*`, `assets__*`)

These are the safe default for non-UR5e work. Prefer them over hand-written `run_python`.

- **Start with `sim__stage__info`.** It returns the process, stage identity, units, up-axis and
  the `session_id` / `stage_id` every other call needs. A `STALE_TARGET` refusal includes
  `current_target`; resend those IDs explicitly. Never assume the target was retargeted.
- **Inspect:** `sim__stage__tree`, `sim__prim__get`, `sim__prim__find` (literal substring, not
  regex), `sim__physics__inspect`, `sim__joints__inspect`.
- **Edit only while stopped:** `sim__prim__create`, `sim__prim__set_transform`,
  `sim__prim__clone`, `sim__prim__delete` (`confirm=true`), `sim__stage__clear_owned`,
  `sim__light__*`, `sim__material__*`, `sim__physics__configure`, `sim__joints__drive`.
  Changes go to the session layer; source USD files are not rewritten. `TIMELINE_ACTIVE` means
  stop the timeline first.
- **Project assets:** `assets__roots` → `assets__scan` / `assets__search` (filename-only, host
  side) → `assets__info` → `assets__import_usd` or `assets__import_urdf`. Only launch-approved
  trusted roots are accepted; there is no URL input and no untrusted/hostile-USD isolation.
  Import requires a stopped timeline, matching units/up-axis and a new wrapper path.
  For a user-supplied absolute USD path under an approved root, use `assets__import_path`
  with the current session/stage IDs. It preserves the source and copies the bounded project;
  inspect the returned prim and rendered result before assuming visual or physics readiness.
  For the UR5e workstation workflow, use `assets__prepare_workstation` with those IDs and
  `source_path`. It prepares `workstation/vention.usd` in the private asset cache and returns
  `loaded:false`; then call `ur5e_dt__load_workstation` and inspect `/World/Vention`.
  The twin and selected Kit must share the cache. Existing files are not overwritten, and a
  project store (`isaac-mcp.json`) takes precedence over the asset folder; verify which file was loaded (`scripts/isaac_assets.py doctor`). The repository ships no assets.
  See `docs/TOOLS-SCENE.md` for approval, staging, dependency limitations and cache placement.
- **Playback and runtime state:** `sim__timeline__control` (play/pause/stop) and
  `sim__simulation__step` (1–120 exact steps while paused). `sim__body__state` and
  `sim__joints__state` require initialized physics; `sim__joints__command` sets targets and
  returns acknowledgment, not the achieved pose.
- **Sensors and capture:** `sim__camera__create` then `sim__camera__capture` (RTX, PNG in the
  configured output root); `sim__lidar__*` needs Kit launched with `--motion-bvh`.
  `sim__viewport__frame` selects visible geometry and edits the active camera in the session
  layer; it is a scene edit, with no Kit undo entry. `render_complete=false` means allow
  ordinary rendering before inspecting the verified application window. A native failure can
  leave selection or session-camera changes; inspect before retrying. Prefer a smaller subtree
  when surrounding geometry occludes the target. See `docs/TOOLS-SCENE.md` for context,
  look-through, session-sublayer and bounds refusals.
  By default `sim__camera__capture` waits for fresh render completions and a new renderer CPU
  transfer (`cpu_transfer_completed=true`). `wait_for_render=false` writes the last validated
  tool-acquired CPU frame, with cache age and acquisition context; it requests no new render
  or transfer. An unseeded cache returns `NOT_READY`: run a default/true capture first.
  Ordinary Play does not seed or refresh the cache. Cached pixels can predate Stop/reset or
  pose edits; sensor/stage teardown clears them. Acquisition commits before PNG publication,
  so a filename conflict can leave a new valid cached frame without saving a new file.
  A fresh capture's `RENDER_TIMEOUT` means render/CPU transfer did not complete within five
  seconds; check rendering and the selected Kit log. It returns no image or stale fallback.
  LiDAR retains its separate latest-completed-scan behavior and needs Play for acquisition.
  A saved scene from before the corrected save tool can retain a serialized
  `/Orchestrator` graph; re-export it with `sim__stage__save` and create fresh sensors after
  reopening.
- **Save and diagnose:** `sim__stage__save` writes a new basename; it never overwrites or switches
  the current stage. It omits recognized process-bound `/Orchestrator` and SyntheticData graphs,
  so re-export older saved scenes before relying on camera readout after reopening.
  `sim__logs__tail` reads the selected Kit log.
- **Code execution is opt-in:** `sim__controller__create/update` run inline Python in a
  ScriptNode. Use them only when the user asked and Kit was launched with
  `ISAAC_MCP_ENABLE_SCENE_SCRIPTS=1`; this is not a sandbox.

### Action Graphs and maps

- Before building an Action Graph, look node types up with `sim__graph__node_types` (by id or UI
  name, e.g. `contains="Make Array"`): it returns the exact type id and port names for this Isaac
  version, which often differ from tutorials (Break 3-Vector takes `inputs:tuple`; Make Array is
  `ConstructArray` and needs `inputs:input1..` added with `og.Controller.create_attribute`).
  Connect with full paths (`<graph>/<node>.outputs:x`). Check the result with `sim__graph__inspect`.
- For a video or screenshot of a graph, `sim__graph__show` (windowed Kit, `layout=true`, a
  `width`/`height` to undock it) opens it readable in the Action Graph editor.
- No map files for Nav2? `sim__map__occupancy` writes a `map_server` PGM + YAML from the scene's
  colliders (centre `z` on the lidar height; put `start` in open floor; `clear` the robot's own
  footprint). It plays 8 frames itself, so `/clock` restarts: generate the map before starting RViz.

### ROS 2 TF and RViz 2

Never hand-build TF/clock graphs with `run_python`; use the tools so the tree is the same every
time (full recipe: `docs/ROS2-RVIZ.md`).

1. Stop the timeline, then `sim__ros2__tf_setup` with the robot prim. It publishes `/clock`,
   `/joint_states`, a static `world -> base_link` (or `world -> odom` + odometry with
   `base=mobile`) and the link frames, finds the articulation root itself, and reports
   `rviz_fixed_frame`, the topics and the ROS domain. Call it again to change options: it
   rebuilds its single graph instead of adding a second publisher.
2. Choose who publishes the links: Isaac (`link_frames=isaac`, default) **or**
   `robot_state_publisher` with the URDF (`link_frames=robot_state_publisher`, needed for
   RViz's RobotModel). Never both: two publishers per frame is what makes RViz jump.
3. If AMCL/SLAM provides `map -> odom`, pass `world_frame=""`. Add cameras/LiDARs/`tool0`
   with `extra_frames`.
4. Play, then `python3 scripts/ros2_rviz_config.py --fixed-frame <rviz_fixed_frame> --out <f>.rviz`
   (add display topics) and `rviz2 -d <f>.rviz --ros-args -p use_sim_time:=true`, with
   `config/ros_dds.env` sourced in that terminal. Every ROS node needs `use_sim_time:=true`.
5. Before debugging RViz, run `python3 scripts/ros2_tf_doctor.py --fixed-frame <frame>`
   (`--isaac-ros` without a system ROS 2) and fix every ERROR it prints. It also checks
   sensor topics' stamps against `/clock`.
6. Hand-built graphs (a lab that names its own graphs): give every Read Simulation Time node
   the same `resetOnStop`. Sensor helpers (lidar, camera) restart at 0 on Stop, so with
   `resetOnStop=False` on the clock graph RViz drops every scan after the first Stop/Play.
   `True` everywhere is the safe choice when sensor helpers are present.

## Using Isaac Sim's UI (works on a locked or remote PC)

Lab PCs are often used remotely with the screen locked. Then monitor screenshots are composed
from window pixels, and desktop input (`window_*`) is refused, because the lock screen holds the
keyboard and would receive the keystrokes. Isaac Sim's own UI still works through Kit's input:

1. `sim__ui__find(query="play")`, or `window="Stage"`: widgets with text, tooltip, value, a `ref`,
   and their centre in Isaac Sim window pixels.
2. `sim__ui__act` with that `ref` (or `x`, `y`): `click`, `double_click`, `right_click`, `move`,
   `drag`, `scroll`, `type` (ref first focuses the field), `keypress` (`["ctrl", "s"]`, `["enter"]`),
   `menu` (`"Create/Mesh/Cube"`; a wrong path lists the real items), `tab` (`window="Console"`:
   bring a docked tab to the front; `sim__ui__find` lists `hidden_tabs`), or `wait`.
3. Check the result: `screenshot(window_id=<Isaac Sim window>)` (its pixels are the same as
   `sim__ui__find`'s; a downscaled image's caption gives the scale factor), or `sim__ui__find`.

Prefer a dedicated tool (timeline, stage, graph) when one exists; use the UI for what has none.
Use `window_*` only for other apps (RViz 2) on an unlocked desktop.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Only host and `isaaclab__*` tools listed | Connected before Kit, or bridge tools filtered | `refresh_tools`, then the `--list-bridges` CLI |
| `refresh_tools` reports connection refused | No bridge on that port | Check the listing; start Kit with `isaac.mcp.bridge` only if none is advertised and the user agrees |
| `refresh_tools` refused right after `sim__launch` | The server dials its configured port, not the new session's | `use_bridge(port=<the launched port>)` (sim__status's `next` says so) |
| Monitor recording is all black; screenshot says "composed from N window(s)" | Screen locked or blanked (PC used remotely) | Record a window (`window=`/`window_id=`); screenshots are composed from window pixels |
| `window_*` refused: "the desktop is locked" | The lock screen would get the input | For Isaac Sim use `sim__ui__find`/`sim__ui__act`; other apps need the desktop unlocked |
| New tools not shown after refresh | Client ignores `tools/list_changed` | Reconnect the MCP server, or use `python -m isaac_mcp.client list_tools --port <port>` |
| Train/eval/play refused with `kit_bridges` | GPU guard | Ask: stop the Sim, or pass `allow_concurrent_gpu=true` |
| `path outside allowed roots` | Metrics/checkpoint path not under the Lab or jobs roots | Use `run_dir` from `isaaclab__runs_status`, or set `ISAACLAB_LOGS_DIR` |
| `STALE_TARGET` from `sim__*` / `assets__*` | Stage or session changed | Read `current_target`; resend its `session_id` and `stage_id` explicitly |
| A cleanup command killed its own shell (exit 144) | `pkill -f`/`pgrep -f` patterns also match the shell running them | Stop processes by the exact PID you recorded when starting them |
| RViz frames jump, "Frame does not exist", extrapolation errors | Two TF publishers, wrong Fixed Frame or wall-clock node | `scripts/ros2_tf_doctor.py`; see the ROS 2 section above |
| RViz drops scans: "timestamp on the message is earlier than all the data in the transform cache" | Sensor stamps and `/clock` on different time bases (`resetOnStop` differs) | The doctor names the topic; use one `resetOnStop` setting everywhere, then Stop and Play |
| `TIMELINE_ACTIVE` on an edit | Timeline is playing or paused | `sim__timeline__control` with `stop`, then retry |
| `NOT_READY` on a nonwaiting camera capture | No validated tool-acquired frame in this sensor's cache | Capture with default/`wait_for_render=true`; ordinary Play or waiting a few frames will not seed it |
| `RENDER_TIMEOUT` on a fresh camera capture | Matching render completions or CPU transfer did not finish within five seconds | Check rendering and `sim__logs__tail`; no image or stale fallback was returned. A pending native transfer can cause `RENDER_BUSY` on retry; allow completion before another fresh request |
| `assets__roots` empty or a path is refused | No launch-approved root, or the file is outside it | Run the `isaac_mcp.project_assets` approval command in `docs/TOOLS-SCENE.md`; no tool can grant roots |
| `envs_list` shows `import_errors` | A task module failed to import | Read the error; custom tasks need `ISAACLAB_TASK_MODULES` / `ISAACLAB_TASK_PATH` |
| Metrics tools ask for the `isaaclab` extra | `tensorboard` not installed in the server env | `uv tool install --force --editable './mcp[isaaclab]'` from the repo |

## Reference

Tool references live in the isaac-mcp repository under `docs/`: `TOOLS-HOST.md` (gateway, screen,
input, sim sessions, bridge built-ins), `TOOLS-ISAACLAB.md`, `TOOLS-UR5E.md`, `TOOLS-SCENE.md`
(the 46 generic tools: 38 `sim` and 8 `assets`, and their limits), and `MCP-SERVER.md` for client setup.
`AGENTS.md` at the repository root gives the tier map and current tool counts.

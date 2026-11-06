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

For ROS camera and LiDAR saves, `sim__stage__save` defaults `reset_camera_render_handles` to `false`.
An affected export reports `SAVE_UNSUPPORTED` with `properties` and `retry_with`; inspect these
before explicitly opting in to normalization with `true`. Successful exports report exact
`omitted_runtime_render_properties`. Ambiguous or unavailable bindings and authored camera handles still refuse.
This changes the private export only and does not prove authorship. Its copy is the rescue for a stale LiDAR file:
if `sim__stage__open` returns `stale_render_products.rescue`, follow it.
Open saved scenes with `sim__stage__open`. It repairs stale LiDAR products on builds where that was measured (6.2.0.0, 5.1.0.0); elsewhere it returns `rescue` steps. Most scene tools need `session_id` and `stage_id`
from `sim__stage__info`; `sim__graph__node_types` and `sim__logs__tail` do not. `sim__logs__tail` also lists recent stage and timeline changes
(`scene_events`), including ones from outside this session. If `/scan` is missing, check `ros_render_status` from `sim__graph__inspect` and confirm with `ros2 topic info`.
For a missing active, concrete camera producer relationship, save restores a runtime target in
the export only when the observed render product confirms the camera and resolution; see
`restored_camera_targets`.
Pause before saving and check the exported scene after reopening in a fresh process.

## Which tool do I start with?

1. **Build a new scene:** `sim__quick_start`. It needs no IDs and works for anyone; add `robot` for one.
2. **An existing scene:** `sim__stage__info` first. Other `sim__*` and `assets__*` tools need its
   `session_id` and `stage_id`.
3. **Import your own robot or asset (USD, URDF, xacro, SDF, MuJoCo XML, a mesh, a ROS map, or a folder that holds one):** `assets__roots`, then `assets__import_path` (format auto). See `docs/ASSET-IMPORT.md`.
4. **Copy a USD or URDF and its files into your asset folder:** `assets__pack_build`, then `sim__quick_start robot="<pack>/<file>"`.
5. **Train, evaluate or play a policy:** `isaaclab__install_info`, then `isaaclab__train`. No bridge is needed.
6. **One lab's UR5e digital twin (RISE lab):** `ur5e_dt__quick_start`, only when that twin is what was asked for.
7. **See the result:** `screenshot`; for video, `start_screen_recording`.

`sim__*` and `assets__*` are universal. `ur5e_dt__*` is specific to that twin. If a tool answers that no
bridge is reachable, go to [First calls in a session](#first-calls-in-a-session).

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
   recording yourself and decide the next step from it. Use `list_windows` to find windows:
   `wmctrl`/`xdotool` from a shell may see nothing on a locked or remote session, which does not
   mean Isaac Sim is headless (`status` says `headless`).
5. **Report tool results as they are.** Generic scene tools set MCP `isError` and return a `code`
   such as `STALE_TARGET`; legacy tools may return `{"status": "error", "message": ...}`
   or `{"error": ..., "hint": ...}`. Quote the error and
   follow the hint before retrying.
   Generic scene tools refuse arguments they do not have ("Extra inputs are not permitted"), so
   a misspelled or invented parameter is never silently ignored: check the tool's schema.

## Calling the tools

Call the isaac tools as MCP tools, not by importing Python packages or running repo scripts.
If your client only gives you a code tool (OpenCode's "Code Mode" `execute`), the tools are
JavaScript functions there, not Python:

```js
;(async () => {
  const info = await tools.isaac.sim__stage__info({});
  const r = info.result ?? info;                       // a JSON string or already an object
  const target = typeof r === "string" ? JSON.parse(r) : r;
  return JSON.stringify({session_id: target.session_id, stage_id: target.stage_id});
})()
```

Pass Python for `run_python` in a template literal so `\n` and quotes survive:
``await tools.isaac.run_python({code: String.raw`import omni.usd
result = omni.usd.get_context().get_stage().GetRootLayer().identifier`})``
(the code must not contain a backtick or `${`).
The bridge CLI is `"$(uv tool dir)/isaac-mcp/bin/python" -m isaac_mcp.client` from any directory, or `python3 mcp/isaac_mcp/client.py` from the isaac-mcp checkout root (a plain `python -m isaac_mcp.client` needs the MCP server's own Python); do not import
`omni`/`pxr` outside Kit (use `run_python`), and never `pip install` into the system Python
(`--break-system-packages`) to make a script run.

## First calls in a session

1. `refresh_tools`, which rediscovers bridge tools. The built-ins (`run_python`, `status`, ...),
   generic scene and UR5e catalog tools are listed even with Kit offline, so a listed tool does not
   mean Kit is up: `status` tells you. Kit can take 2-3 minutes to start; while it boots, call
   `refresh_tools(wait_s=120)` (max 180) instead of retrying by hand.
2. Find bridges. Use `list_bridges` if it is listed. Otherwise run the CLI in a shell
   (`"$(uv tool dir)/isaac-mcp/bin/python" -m isaac_mcp.client --list-bridges`, or the checkout form above), which works even when bridge built-ins are hidden
   by `ISAAC_MCP_BUILTINS=0` or `ISAAC_MCP_NAMESPACE`. It exits 2 with `[]` when none is advertised.
3. With several bridges, call `use_bridge` with the listed port, then `status`, and check that the
   PID and app version are the process you mean.
4. For Isaac Lab work, call `isaaclab__install_info`. Inspect the returned error: installation
   discovery, interpreter execution and package metadata are separate checks. Missing configuration
   is only one possible cause; valid automatic discovery needs no explicit variables. Confirm
   non-null Lab/Sim versions and the intended interpreter/root before task discovery. See
   `docs/TOOLS-ISAACLAB.md#locating-an-isaac-lab-install`; do not reinstall or rename an existing
   environment merely because a probe fails.
5. For generic scene or project-asset work, call `sim__quick_start` for a new scene, or
   `sim__stage__info` on an existing one, and `assets__roots` for your own files. The
   69-tool pack (57 `sim`, 12 `assets`) is on by default; `ISAAC_MCP_SCENE_TOOLS=0` hides it per server.
   `assets__roots`, `assets__scan`, `assets__search` and `assets__packs` / `assets__pack_*` run on the host; the rest need the
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
   `-v0` suffix (for example `Isaac-Reach-Franka`); Lab 2.3 ids keep it (`Isaac-Cartpole-v0`), and Lab 2.x
   needs `rl_library` too (`install_info` lists the installed ones).
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
- **Demo videos:** `tile_windows(window_ids=[sim, rviz])` puts them side by side without frame
  overlap (or `move_window`, whose x/y/size are the client area), then
  `start_screen_recording(window_ids=[sim, rviz])` records both into ONE synced video from each
  window's own pixels, so covering windows, focus changes and a locked screen do not matter. Output
  is native size up to 1920 wide. Join clips with `join_videos` (`concat`, `hstack`, `vstack`)
  instead of hand-written ffmpeg.
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
- **Edit only while stopped:** `sim__prim__create` (also `Plane`, `mass`, `density`),
  `sim__prim__set_transform` (any Xformable prim), `sim__prim__set_attribute`, `sim__physics__apply`
  (collider, rigid body, kinematic, mass, density), `sim__prim__revert`, `sim__prim__clone`,
  `sim__prim__delete` (`confirm=true`), `sim__stage__clear_owned`, `sim__light__*`,
  `sim__material__*`, `sim__physics__configure`, `sim__joints__drive`.
  Changes go to the session layer; source USD files are not rewritten, and `sim__prim__revert`
  removes only what these tools recorded. `TIMELINE_ACTIVE` means stop the timeline first.
- **Asset packs:** each project has a subfolder in the user's `isaac-mcp-assets`. `assets__packs` and
  `assets__pack_doctor` read; `assets__pack_adopt`, `assets__pack_pin` and `assets__pack_build` write
  only to the primary asset folder and need `force=true` to replace anything. Read refusals such as
  `UNRESOLVED_DEPENDENCIES` before passing an override. See `docs/ASSETS.md`.
- **Where assets come from:** use a source the user names (a link, or a public robot name such as
  `unitree_h1` for `sim__quick_start`). If they named none and you need one, propose a short list and wait; do
  not search for random repositories. Download into the project's `downloads/` folder, build a pack with
  `assets__pack_build` and set `origin` to the repository URL and commit, and never run code from a download.
  An agent with a shell can also copy files into the asset folder, which loads with no approval, so recording
  `origin` is what lets the user see what was used. See `docs/ASSETS.md`.
- **Project assets:** `assets__roots` → `assets__scan` / `assets__search` (filename-only, host
  side) → `assets__info` → `assets__import_path`. Only launch-approved
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
  `sim__joints__state` require initialized physics; body state accepts `path`, `prim` or
  `prim_path`. `sim__joints__command` sets position targets by name or DOF order, with optional
  velocity targets and clamp, and returns the applied targets, not the achieved pose;
  `sim__joints__gains` reads/sets runtime gains, `sim__joints__move` settles while paused and reports
  every joint's error (`settled=false` means a contact, a weak drive or a step limit),
  `sim__joints__teleport` is a state reset. All of them are runtime-only and lost on Stop.
- **Sensors and capture:** `sim__camera__create` then `sim__camera__capture` (RTX, PNG in the
  configured output root); the camera takes a parent path, `rotate_xyz` degrees or a quaternion,
  `focal_length` and `clipping_range`. `sim__lidar__create` with `config=Example_Rotary_2D`
  builds NVIDIA's named lidar (an unknown name lists the valid ones); no `--motion-bvh` needed.
  `sim__viewport__frame` selects visible geometry and edits the active camera in the session
  layer; it is a scene edit, with no Kit undo entry. For videos of a moving robot use
  `margin` (metres) or `eye`/`target`, and `follow=true` to keep it in view while playing
  (`follow=false` stops). For a geometry-free `base_footprint`, the tool can select a visible
  `base_link` sibling when either link or their parent has a rigid-body or articulation API.
  Otherwise it returns nearby geometry candidates for an explicit retry. Check the returned
  selected path, which is also used for follow. `render_complete=false` means allow
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

- Open a scene file with `sim__stage__open` (not run_python); it returns the new IDs.
- Build Action Graphs with `sim__graph__build` (nodes, values, connections, prim targets in one
  call; timeline stopped). Look node types up first with `sim__graph__node_types` (by id or UI name,
  e.g. `contains="Make Array"`): exact type ids, ports and allowed token values for this Isaac
  version, which often differ from tutorials. The build checks every port, adds Make Array inputs,
  stores token values, and rolls back on errors. Then play and `sim__graph__inspect`: it lists each
  node's compute errors (a wrong token makes a node silently output zeros).
  Pairs/objects, JSON-encoded entries or whole containers, and flat alternating pairs are
  accepted. For example `nodes=['["tick", "omni.graph.action.OnPlaybackTick"]']` works;
  `values` may be `'{"clock.inputs:topicName": "/clock"}'`. Node types and ports
  are still checked. For manual `run_python` work, use `og.get_registered_nodes()` and reacquire
  `og.Controller.attribute(full_port_path)` before `og.Controller.get`; use
  `og.Controller.edit(graph_path, {og.Controller.Keys.SET_VALUES: [(full_port_path, value)]})`
  for an authored value. Prefer `sim__graph__node_types` / `sim__graph__inspect`.
- Save checkpoints into your own folder: `sim__stage__save` with `directory=<folder under home>`.
- For a video or screenshot of a graph, `sim__graph__show` (windowed Kit, `layout=true`, a
  `width`/`height` to undock it) opens it readable and fitted in the Action Graph editor;
  `close=true` closes it. Stop before `layout=true`; while playing, use `layout=false` or follow
  the `retry_with` in the `TIMELINE_ACTIVE` response. The undocked editor floats inside Isaac Sim.
- No map files for Nav2, or an empty course maps folder? `sim__map__occupancy` writes a `map_server`
  PGM + YAML from scene colliders such as `/SimpleRoom` (centre `z` on the lidar height; put `start` in open floor; `clear` the robot's own
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
6. Hand-built graphs (a lab that names its own graphs): Isaac 6.2 differs from 6.0 tutorials
   (Constant Token, token values, Make Array, 2D RTX lidar, `IsaacComputeTransformTree`); read
   `docs/ISAAC-6.2-NOTES.md` first. Also give every Read Simulation Time node
   the same `resetOnStop`. Sensor helpers (lidar, camera) restart at 0 on Stop, so with
   `resetOnStop=False` on the clock graph RViz drops every scan after the first Stop/Play.
   `True` everywhere is the safe choice when sensor helpers are present.

### ROS 2 from an agent shell

DDS settings, `/scan`, and lessons from the TurtleBot3/Nav2 lab are in
`references/isaac-sim.md#ros-2-from-an-agent-shell`. The short form: `sim__stage__info` returns the
exact shell line under `ros`; use it, or `ros2 topic list` stays empty while Kit publishes.

## Using Isaac Sim's UI (works on a locked or remote PC)

Lab PCs are often used remotely with the screen locked. Then monitor screenshots are composed
from window pixels, and desktop input (`window_*`) is refused, because the lock screen holds the
keyboard and would receive the keystrokes. Isaac Sim's own UI still works through Kit's input:

1. `sim__ui__find(query="play")`, or `window="Stage"`: widgets with text, tooltip, value, a `ref`,
   and their centre in Isaac Sim window pixels.
2. `sim__ui__act` with that `ref` (or `x`, `y`): `click`, `double_click`, `right_click`, `move`,
   `drag`, `scroll`, `type` (ref first focuses the field), `keypress` (`["ctrl", "s"]`, `["enter"]`),
   `menu` (`"Create/Mesh/Cube"`; a wrong path lists the real items; `"Layouts"` alone lists that
   menu's items), `tab` (`window="Console"`: bring a docked tab to the front; `sim__ui__find` lists
   `hidden_tabs`), `close` (`window="Console"`: hide a panel; reopen with `tab`), or `wait`.
3. Check the result: `screenshot(window_id=<Isaac Sim window>)` (its pixels are the same as
   `sim__ui__find`'s; a downscaled image's caption gives the scale factor), or `sim__ui__find`.

Prefer a dedicated tool (timeline, stage, graph) when one exists; use the UI for what has none.
Use `window_*` only for other apps (RViz 2) on an unlocked desktop. `raise_window` cannot work
while locked (it says so); captures and recordings do not need it. `move_window`/`tile_windows`
worked while locked on GNOME.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `run_python`/`status` not listed | `ISAAC_MCP_BUILTINS=0` (they are listed offline otherwise) | Use the `isaac_mcp.client` CLI, or ask the user to enable built-ins |
| `refresh_tools`: no bridge reachable, or no response for `list_tools` | Kit still starting | `refresh_tools(wait_s=120)`; the error names a managed session and its age |
| `refresh_tools` reports connection refused | No bridge on that port | Check the listing; start Kit with `isaac.mcp.bridge` only if none is advertised and the user agrees |
| `refresh_tools` refused right after `sim__launch` | The server dials its configured port, not the new session's | `use_bridge(port=<the launched port>)` (sim__status's `next` says so) |
| Monitor recording looks choppy; result says "composed" | Screen locked or blanked (PC used remotely): frames are composed from window pixels | Record the windows that matter with `window_id=` or `window_ids=[...]` |
| `window_*` refused: "the desktop is locked" | The lock screen would get the input | For Isaac Sim use `sim__ui__find`/`sim__ui__act`; other apps need the desktop unlocked |
| New tools not shown after refresh | Client ignores `tools/list_changed` | Reconnect the MCP server, or use the bridge CLI: `... list_tools --port <port>` |
| Train/eval/play refused with `kit_bridges` | GPU guard | Ask: stop the Sim, or pass `allow_concurrent_gpu=true` |
| `path outside allowed roots` | Metrics/checkpoint path not under the Lab or jobs roots | Use `run_dir` from `isaaclab__runs_status`, or set `ISAACLAB_LOGS_DIR` |
| `STALE_TARGET` from `sim__*` / `assets__*` | Stage or session changed (Kit restarted, stage opened) | The message names the current `session_id` and `stage_id`: resend with those, never the old ones |
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
(the 69 generic tools: 57 `sim` and 12 `assets`, and their limits), and `MCP-SERVER.md` for client setup.
`AGENTS.md` at the repository root gives the tier map and current tool counts.

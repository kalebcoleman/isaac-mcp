# Host and bridge tools

The host `isaac-mcp` process speaks MCP to the client. It provides the gateway and desktop tools below even when
Kit is disconnected. The 23 UR5e tools are also listed from a packaged catalog, even with Kit down; a live twin
bridge is required to execute them. The seven bridge built-ins are listed the same way, from a packaged snapshot of the bridge's own
verbs, and run on the selected bridge when called; they can be hidden from the host's MCP list with
`ISAAC_MCP_BUILTINS=0`. Clients that ignore `tools/list_changed` (OpenCode) therefore see them from startup. Other packs are documented in the [UR5e](TOOLS-UR5E.md) and
[Isaac Lab](TOOLS-ISAACLAB.md) references. All arguments are listed in [Tool arguments](TOOLS-ARGUMENTS.md) and all settings in
[Environment variables](ENVIRONMENT.md).

Examples below show the **tool name** and a JSON object of **arguments**. Use the tool-calling interface of your MCP
client; these objects are not standalone shell commands or complete MCP protocol messages.

## Connect and discover

Start a compatible Kit application with its bridge enabled. For the supplied twin, `./run.sh --headless` starts the
bridge on port 8766 by default. The host chooses an explicit port first, then `UR5E_DT_PORT`, then
`ISAAC_MCP_PORT`; otherwise it discovers an unambiguous advertised bridge. Discovery can fail if there are no live
bridges or several candidates. The host and the Kit app are separate processes: screen tools can work while the
bridge is unavailable.

| MCP tool | Arguments | Use and effects |
|---|---|---|
| `isaac_help` | `task` (optional text), `no_fit=false` | Start here. Names the isaac tools to load and call for what you want to do (record a video, find why a grasp failed, fly a drone with your own controller, train a policy, start a simulator). Topics are ranked by how many of their words the task uses, and a topic that shares only one common word is dropped when another fits clearly better. When nothing matches it says no tool is known to fit, gives the `run_python` pattern (one background call that runs the whole loop), and appends the question to `help-gaps.jsonl` in the server's log folder (`~/.local/state/isaac-mcp`, or `ISAAC_MCP_LOG_DIR`); `no_fit=true` does the same for a task whose named tools did not fit. The file stays on this machine, is capped at 1 MiB, and `ISAAC_MCP_LOG=0` turns it off. Reads nothing from the simulator and works with no bridge. |
| `refresh_tools` | `wait_s=0` (seconds, clamped to 0-180) | Rediscover the selected bridge's registry after a bridge starts or a module changes. If nothing listens on the selected port and exactly one managed session started by this server is live on another loopback port (its PID and readiness checked), it selects that session, says so and why, and the choice lasts until `use_bridge` or a restart; this applies with `wait_s=0` too. It never leaves a port that answers, and with several sessions it names them instead. Adds or updates visible tools and removes tools no longer advertised; catalog-listed tools remain visible as proxies. A failed or malformed discovery, or a collision between bridge tool names or with a host tool, leaves the existing list intact. The host advertises `tools.listChanged` and sends `notifications/tools/list_changed` after a refresh that changed the list; a client that ignores the notification still needs a reconnect (or `python3 mcp/isaac_mcp/client.py list_tools`). When no bridge answers, or one accepts the connection but `list_tools` times out, the error says Kit is probably still starting and names any managed `sim__launch` session with its age; with `wait_s` > 0 it polls every 3 s until the bridge answers or the time runs out (the last sleep is shorter, so it uses the whole wait). When nothing listens on the selected port and another bridge is advertised, the error names the endpoint it tried, lists each advertised bridge's port, PID and app with the `use_bridge` call, and does not suggest waiting; an explicit `wait_s` is still honored, since you may be starting a simulator on that port. It returns at once on an ambiguous "multiple bridges" error, and when nothing listens on the selected port and this server launched several simulators that are still live: it will not choose one, so call `use_bridge`. A failed call sets MCP `isError`, with the same text. A first Kit start took 172 s on a lab PC. |
| `use_bridge` | `port` (integer, required) | Connect to a different bridge on that port, report its identity, and replace the previously discovered bridge tools. The choice lasts until the host server restarts. Sends `notifications/tools/list_changed` after replacing the tools. A failed connection or tool discovery sets MCP `isError`. |

`status` is the one check-in call: the selected bridge's PID, port and app, what else runs on this machine, and the
stage IDs, or a plain "nothing is running" answer (see [built-ins](#kit-bridge-built-ins)). Call it once before the
first simulator action. `list_bridges`
lists advertised bridge instances (they are bridge built-ins: listed unless `ISAAC_MCP_BUILTINS=0`, but they need a reachable bridge to answer;
`python3 mcp/isaac_mcp/client.py --list-bridges` works either way). Example MCP calls: `use_bridge` with `{"port":8766}`, followed by `status` with
`{}` and `refresh_tools` with `{}`. Switching targets changes the selected simulation; verify the result before
issuing scene-changing commands.

Catalog-listed tools, built-ins included, do not prove a live bridge is available: `status` says whether one is, without failing when none is.
`ISAAC_MCP_NAMESPACE` / `ISAAC_MCP_BUILTINS=0` may hide bridge tools (including `status` and
`list_bridges`), and tools of uncatalogued extensions appear only after discovery. Call `refresh_tools` and verify with the CLI below before starting another Kit.

The bridge also has an internal `list_tools` discovery operation. It is intentionally hidden from the bridge's own
advertised tool list, so it is not normally a dynamically registered MCP tool. Ask the MCP client to list tools, or
use the standard-library bridge CLI from the checkout root. Discover the advertised bridges first and use the
host and port from that listing; 8766 below is only the twin's default port:

```bash
python3 mcp/isaac_mcp/client.py --list-bridges
python3 mcp/isaac_mcp/client.py status --port 8766
python3 mcp/isaac_mcp/client.py list_tools --port 8766
python3 mcp/isaac_mcp/client.py ur5e_dt.play_scene '{}' --port 8766
```

The CLI uses canonical bridge names with dots; the MCP server exposes namespaced tools using double underscores,
such as `ur5e_dt__play_scene`.

## Grouped tools

A client is shown one tool per subject, with an `action` argument, instead of one tool per operation:
`sim__prim(action="create", ...)` rather than `sim__prim__create(...)`. A grouped tool takes the arguments of the
action it runs and returns that tool's own answer. An argument that belongs to another action is refused, and nothing
runs. `action="help"` returns the full description and arguments of every action in the group.

The single-purpose names in brackets below still work when called, with the same arguments and answers, so existing
prompts and scripts keep running. They are **deprecated**: they are no longer listed, and new work should use the
grouped form. `ISAAC_MCP_LEGACY_TOOLS=1` lists them as before, with no grouped tools. Tools that belong to no group
(`status`, `run_python`, `sim__quick_start`, `sim__timeline__control`, `assets__import_path`, `screenshot` and others)
are listed under their own names either way. Where a tool has an argument of its own called `action`, the grouped
tool takes it as `sub_action`.

| Grouped tool | What it covers | Actions (single-purpose tool) |
|---|---|---|
| `sim__stage` | The open scene: identify it, list it, open, save, or clear what these tools made. | `info` (`sim__stage__info`), `tree` (`sim__stage__tree`), `open` (`sim__stage__open`), `save` (`sim__stage__save`), `clear_owned` (`sim__stage__clear_owned`) |
| `sim__prim` | Prims: read, find, measure, create, move, edit, copy, delete and undo. | `get` (`sim__prim__get`), `find` (`sim__prim__find`), `bounds` (`sim__prim__bounds`), `measure` (`sim__prim__measure`), `create` (`sim__prim__create`), `create_many` (`sim__prim__create_many`), `set_transform` (`sim__prim__set_transform`), `set_attribute` (`sim__prim__set_attribute`), `clone` (`sim__prim__clone`), `delete` (`sim__prim__delete`), `revert` (`sim__prim__revert`) |
| `sim__physics` | Physics: inspect, body state, contacts, raycasts, scene settings, colliders, mass and pushes. | `inspect` (`sim__physics__inspect`), `body_state` (`sim__body__state`), `contacts` (`sim__physics__contacts`), `raycast` (`sim__physics__raycast`), `configure` (`sim__physics__configure`), `apply` (`sim__physics__apply`) |
| `sim__joints` | A robot's joints: read them, command targets, set drives and gains, move and teleport. | `inspect` (`sim__joints__inspect`), `state` (`sim__joints__state`), `command` (`sim__joints__command`), `move` (`sim__joints__move`), `drive` (`sim__joints__drive`), `gains` (`sim__joints__gains`), `teleport` (`sim__joints__teleport`) |
| `sim__arm` | An arm's end effector: inverse and forward kinematics. | `ik` (`sim__arm__ik`), `fk` (`sim__arm__fk`) |
| `sim__simulation` | Advance physics yourself: exact steps, a run with your own controller in the loop, or a saved trajectory replayed to video. | `step` (`sim__simulation__step`), `run` (`sim__simulation__run`), `replay` (`sim__trajectory__replay`) |
| `sim__camera` | Cameras: list, create, capture a picture. | `list` (`sim__camera__list`), `create` (`sim__camera__create`), `capture` (`sim__camera__capture`) |
| `sim__lidar` | LiDAR: create one, read a scan. | `create` (`sim__lidar__create`), `read` (`sim__lidar__read`) |
| `sim__light` | Lights: create or change one. | `create` (`sim__light__create`), `modify` (`sim__light__modify`) |
| `sim__material` | Materials: create one, bind it to geometry. | `create` (`sim__material__create`), `apply` (`sim__material__apply`) |
| `sim__graph` | OmniGraph action graphs: node types, build, inspect, show in the editor. | `node_types` (`sim__graph__node_types`), `build` (`sim__graph__build`), `inspect` (`sim__graph__inspect`), `show` (`sim__graph__show`) |
| `sim__controller` | OPT-IN CODE EXECUTION: a script-node controller that ticks while the timeline plays. | `create` (`sim__controller__create`), `update` (`sim__controller__update`) |
| `sim__ui` | Isaac Sim's own windows: find a widget, act on it. | `find` (`sim__ui__find`), `act` (`sim__ui__act`) |
| `sim__logs` | The simulator's log: tail it, mark a place, summarise. | `tail` (`sim__logs__tail`), `mark` (`sim__logs__mark`), `summary` (`sim__logs__summary`) |
| `assets__library` | Your asset folders: approved roots, files in one, a search, one file's details. | `roots` (`assets__roots`), `scan` (`assets__scan`), `search` (`assets__search`), `info` (`assets__info`) |
| `assets__pack` | Named asset packs: list, check, dependencies, adopt a folder, pin, build. | `list` (`assets__packs`), `doctor` (`assets__pack_doctor`), `dependencies` (`assets__dependencies`), `adopt` (`assets__pack_adopt`), `pin` (`assets__pack_pin`), `build` (`assets__pack_build`) |
| `ur5e_dt__scene` | The RISE lab's UR5e twin: build its scene, load the bench or room, play, stop, save, restore. | `quick_start` (`ur5e_dt__quick_start`), `quick_start_with_workstation` (`ur5e_dt__quick_start_with_workstation`), `quick_start_with_workstation_and_room` (`ur5e_dt__quick_start_with_workstation_and_simple_room`), `new_stage` (`ur5e_dt__new_stage`), `load_workstation` (`ur5e_dt__load_workstation`), `load_room` (`ur5e_dt__load_simple_room`), `play` (`ur5e_dt__play_scene`), `stop` (`ur5e_dt__stop_scene`), `save_state` (`ur5e_dt__save_scene_state`), `restore_state` (`ur5e_dt__restore_scene_state`) |
| `ur5e_dt__objects` | The twin's task objects: add, delete, assemble, randomize, place, sync from the real cell. | `add` (`ur5e_dt__add_objects`), `delete` (`ur5e_dt__delete_objects`), `assemble` (`ur5e_dt__assemble_objects`), `randomize` (`ur5e_dt__randomize_object_poses`), `randomize_one` (`ur5e_dt__randomize_single_object`), `set_pose` (`ur5e_dt__set_object_pose`), `sync_real_poses` (`ur5e_dt__sync_real_poses`) |
| `ur5e_dt__ros` | The twin's ROS 2 side: camera TF graph, pose publisher, the UR driver. | `camera_tf_graph` (`ur5e_dt__setup_camera_tf_graph`), `pose_publisher` (`ur5e_dt__setup_pose_publisher`), `start_driver` (`ur5e_dt__start_ros_driver`) |
| `ur5e_dt__recording` | The twin's viewport recorder: start, stop, status. | `start` (`ur5e_dt__start_recording`), `stop` (`ur5e_dt__stop_recording`), `status` (`ur5e_dt__get_recording_status`) |
| `files` | Files on the simulator's machine: write, read, check size and hash. | `put` (`put_file`), `get` (`get_file`), `stat` (`stat_file`) |
| `jobs` | Every job, simulator or process: list, status (with wait_s), log tail, cancel. | `status` (`job_status`), `cancel` (`job_cancel`), `list` (`isaaclab__runs_list`), `tail` (`isaaclab__runs_tail`), `lab_status` (`isaaclab__runs_status`) |
| `isaaclab__launch` | Start an Isaac Lab job and get its job_id: train, evaluate, play, record or check a task. | `train` (`isaaclab__train`), `eval` (`isaaclab__eval`), `play` (`isaaclab__play`), `record` (`isaaclab__record`), `env_check` (`isaaclab__env_check`) |
| `isaaclab__inspect` | Before launching: the Isaac Lab install, its tasks, GPU room, and checkpoints. | `install` (`isaaclab__install_info`), `envs` (`isaaclab__envs_list`), `preflight` (`isaaclab__preflight`), `checkpoints` (`isaaclab__checkpoints_list`) |
| `isaaclab__metrics` | Training results: a summary of scalars or evaluation files, or one curve's last points. | `summary` (`isaaclab__metrics_summary`), `curve` (`isaaclab__tensorboard_tail`) |
| `screen_recording` | Record the desktop or a window to MP4: start, stop, status. | `start` (`start_screen_recording`), `stop` (`stop_screen_recording`), `status` (`screen_recording_status`) |
| `windows` | Desktop windows: list, raise, move, tile. | `list` (`list_windows`), `raise` (`raise_window`), `move` (`move_window`), `tile` (`tile_windows`) |
| `window_input` | Mouse and keyboard into the focused window (opt-in group). | `click` (`window_click`), `drag` (`window_drag`), `scroll` (`window_scroll`), `type` (`window_type`), `key` (`window_key`) |
| `sim__session` | Simulators this server manages (opt-in group): launch, list, status, stop. | `launch` (`sim__launch`), `list` (`sim__list`), `status` (`sim__status`), `stop` (`sim__stop`) |

Three more tools are left out of the list because another does the same job, and still work when called:
`list_bridges` (use `status`), `isaaclab__run_script` (use `run_script`), `isaaclab__runs_cancel` (use `jobs(action="cancel")`).

## Choosing which local tools a server exposes

The 30 standard local tools (the thirteen host gateway and desktop tools and the seventeen `isaaclab__*` tools) are registered by
default. Five desktop input tools and four simulator-session tools are opt-in. `ISAAC_MCP_LOCAL_TOOLS` selects groups for one server process, read once at startup. It is a
comma-separated list of group names and aliases; unset or empty means `all`. An unknown name stops the server at
startup with a message listing the valid names; it never registers nothing silently.

The number registered follows this setting: **134 tools** by default, **+5** with `screen_input` and **+4** with
`sim_launch`, **143** with both. A client is shown **45 tools** of them ([grouped tools](#grouped-tools)). The server writes its tool count and filters to its log at startup; see
[diagnosing disconnects](MCP-SERVER.md#diagnosing-disconnects-the-server-log).

| Group | Tools |
|---|---|
| `isaaclab_read` | `isaaclab__checkpoints_list`, `isaaclab__metrics_summary`, `isaaclab__tensorboard_tail`, `isaaclab__preflight` |
| `isaaclab_probe` | `isaaclab__install_info`, `isaaclab__envs_list` |
| `isaaclab_jobs` | `isaaclab__runs_list`, `isaaclab__runs_status`, `isaaclab__runs_tail` |
| `isaaclab_run` | `isaaclab__train`, `isaaclab__eval`, `isaaclab__play`, `isaaclab__runs_cancel`, `isaaclab__env_check`, `isaaclab__record`, `isaaclab__run_script`, `isaaclab__checkpoint_register`, `run_script` |
| `screen_read` | `screen_info`, `list_windows`, `screen_recording_status`, `isaac_help` |
| `screen_capture` | `screenshot`, `start_screen_recording`, `stop_screen_recording`, `join_videos` |
| `screen_control` | `raise_window`, `move_window`, `tile_windows` |
| `screen_input` | `window_click`, `window_drag`, `window_scroll`, `window_type`, `window_key` (explicit opt-in) |
| `sim_launch` | `sim__launch`, `sim__list`, `sim__status`, `sim__stop` (explicit opt-in) |
| `gateway` | `refresh_tools`, `use_bridge` |

Aliases: `all` (standard groups, excluding `screen_input` and `sim_launch`; the default), `readonly` (`isaaclab_read,screen_read`: the local tools annotated as read-only)
and `none` (no local tools; a pure Kit gateway, since bridge tools are still discovered). Groups and aliases can be mixed, for example
`readonly,gateway`.

To enable input as well as the standard tools, set `ISAAC_MCP_LOCAL_TOOLS=all,screen_input` in the MCP client's
server environment and reconnect. Selecting `screen_input` alone exposes only the five input tools; include
`screen_read` and `screen_capture` if the client needs to identify and inspect a window. Input tools have
conservative destructive and open-world annotations. To enable the separate simulator launcher, add `sim_launch`
(for example, `all,screen_input,sim_launch`) and reconnect.

- `isaaclab_probe` runs the configured Python interpreter; its startup code can have side effects. `envs_list` also
  imports configured task modules, which can run arbitrary code. Both tools use conservative non-read-only,
  destructive, non-idempotent and open-world hints.
- `isaaclab__train`, `isaaclab__eval`, and `isaaclab__play` also run configured Python and task modules, so they carry
  the same conservative hints; those processes may write files or contact external systems.
- `isaaclab_jobs` can write: `runs_list` and `runs_status` replace stored metadata when reconciling orphaned jobs,
  and `runs_tail` can create the jobs directory. The first two have destructive hints; `runs_tail` has an additive hint.
- `readonly` and `isaaclab_read` do not include these five tools. To add them, select `isaaclab_probe,isaaclab_jobs`
  as well (for example, `readonly,isaaclab_probe,isaaclab_jobs`). The default `all` selection includes them.
- The capture directories (`~/Pictures/screen-mcp`, `~/Videos/screen-mcp`) are created only when `screen_capture` is
  enabled, so a profile without that group creates neither. Importing the Isaac Lab job module can still reconcile
  orphaned job records in an existing jobs directory, whatever the groups. Thus even
  `readonly` does not make server startup free of filesystem writes.
- `gateway` only controls the two tools. Bridge discovery at startup is part of the server lifespan and still runs when
  `gateway` is excluded; without the tools, a client cannot re-run or redirect it, so restart the MCP connection after
  starting Kit.
- `ISAAC_MCP_NAMESPACE` and `ISAAC_MCP_BUILTINS` keep their meaning and filter only bridge tools (discovered live or listed from the catalog).
  `ISAAC_MCP_LOCAL_TOOLS` filters only the host's local tools. These compose with
  `ISAAC_MCP_SCENE_TOOLS=0`, an optional startup preference hiding all 71 generic `sim`/`assets`
  tools from both the offline catalog and live discovery. The default is `1`; UR5e, Isaac Lab
  and optional managed-session tools are unaffected. With standard local groups, opting out
  keeps the other tiers, leaving 63 tools, offline or with a bridge. These are visibility controls, not isolation.
- `ISAAC_MCP_LOCAL_TOOLS` does **not** restrict bridge tools, so `readonly` is a read-only *local* profile, not a read-only
  server: with a bridge connected, bridge tools (`run_python`, `ur5e_dt__*`) remain and can change the scene. See
  [Limiting what a client can reach](MCP-SERVER.md#limiting-what-a-client-can-reach) for what restricting them takes.
- Every local tool carries MCP annotations. `readOnlyHint` is true for exactly the `isaaclab_read` and `screen_read`
  groups. The capture tools are not read-only because they write files and return desktop pixels, and
  the job reconciliation and Python probe tools are also not read-only, as described above. Annotations are hints,
  not enforcement.
  Bridge tools registered dynamically are not annotated.

## Desktop and window tools

These tools use the host's X11 display (`DISPLAY`, default `:0`), and do not require a Kit bridge. Their behavior
depends on an accessible graphical session. Screenshots are saved under `~/Pictures/screen-mcp/` and recordings
under `~/Videos/screen-mcp/` on the **host server** machine. Mouse and keyboard tools require the separate opt-in below.

| MCP tool | Arguments and defaults | Use and effects |
|---|---|---|
| `screen_info` | none | Report display, monitors, auto-target, capture locations and recording state. |
| `list_windows` | `filter=null` | List visible windows and IDs; the optional string filters title or window class. A `client` marker identifies windows reported by the window manager as application clients. |
| `move_window` | `x`, `y`, `width`, `height`, `window=null`, `window_id=null`, `expected_pid=null`, `frame=false` | Un-maximize, move and resize a window through the window manager (EWMH), for example Isaac Sim on the left and RViz 2 on the right before a screen recording. By default the geometry is the **client area** (the window content that captures record); `frame=true` places the outer frame including the title bar. The frame offset comes from `_NET_FRAME_EXTENTS`, and an unexpected offset is corrected once. Returns the final client and frame geometry and says `clamped` when the window manager or app limited it (for example GNOME keeps title bars below the top panel). Negative coordinates are allowed. |
| `tile_windows` | `window_ids` (2–6 IDs), `layout="left_right"`, `monitor=null`, `gap=0` | Tile windows over a monitor's work area (`_NET_WORKAREA`, the screen minus panels): `left_right` (one row), `top_bottom` (one column) or `grid`. Each outer frame fills its tile, so title bars do not overlap. `monitor` is the 1-based index from `screen_info` (default: the monitor holding the first window). Reports each window's client geometry and any clamp. |
| `raise_window` | `window=null`, `window_id=null`, `expected_pid=null` | Request window-manager activation and verify it became active. Activation gives the window focus. Select the actual client window ID rather than a window-manager frame, and supply the expected PID when targeting a known process. Ambiguous titles owned by different processes require a window ID. On a locked screen activation is impossible, and the error says so; window screenshots and recordings do not need it. |
| `screenshot` | `monitor=null`, `window=null`, `window_id=null`, `max_width=1600` | Save a full-resolution PNG and return a caption plus inline image, scaled down to at most `max_width` (clamped to 320–3840). With no selector, auto-targets Isaac Sim/Lab if found, otherwise monitor 1. `monitor=0` means all monitors. When the session is locked or blanked (a PC used remotely), monitor captures are composed from the visible windows' own pixels in stacking order, and the caption says so. A downscaled inline image's caption gives the factor back to original pixels. |
| `start_screen_recording` | `monitor=null`, `window=null`, `window_id=null`, `fps=15`, `max_width=1920`, `max_seconds=600`, `full_screen=false`, `window_ids=null`, `layout="side_by_side"`, `wait_for_window=0`, `stop_on_log_marker=null`, `stop_log_file=null` | Start one MP4 recording; no audio. With no selector, auto-target a simulator window, otherwise monitor 1. `window_ids` (2–6 IDs) records several windows into **one** video, reading each window's own pixels every frame so they stay in sync; `layout` is `side_by_side`, `top_bottom` or `grid`, aspect ratios are kept and cells padded. Output is the native capture size scaled down (never up) to `max_width`. `full_screen=true` letterboxes into the primary monitor's shape, also capped by `max_width`. A monitor recording on a locked or blanked screen is composed from the windows' own pixels each frame. `fps` is clamped to 1–30 and `max_seconds` to 5–3600; when capture falls behind `fps`, frames are repeated so the video keeps real time, and the stop result reports it. `wait_for_window=N` (at most 600 s, needs `window` as a title substring) returns at once and starts recording when a matching window appears, so you can launch the app next; `screen_recording_status` shows the wait or why it ended, and `stop_screen_recording` cancels it. `stop_on_log_marker=TEXT` (at most 256 characters) ends the recording when TEXT is added to the selected bridge's Kit log after the recording starts, or to `stop_log_file`, which must be a `.log` in Kit's log folder or under `~/.local/share/isaac-mcp`; call `stop_screen_recording` for the file, whose result has `stopped_by`. `max_seconds` still applies. |
| `stop_screen_recording` | none | Stop and finalize the active recording, reporting its path and result. Cancels a `wait_for_window` that is still waiting. |
| `join_videos` | `inputs` (2–8 paths; 1–8 for `sheet`; a folder, a glob pattern or files for `frames`), `mode="concat"`, `output=null`, `max_width=1920`, `fps=null`, `shortest=false`, `times=null`, `job_id=null`, `label=null` | Join clips into one new MP4 with the bundled ffmpeg (no ffprobe needed): `concat` plays them in order, each scaled and padded to the first clip's size; `hstack`/`vstack` place them side by side (scaled to the tallest clip's height) or top to bottom (widest width), with the result capped by `max_width`. A shorter stacked clip freezes on its last frame unless `shortest=true`. Inputs must be video files under the home folder or `~/Videos/screen-mcp` (a bare name means that folder); `output` is a new `.mp4` name in that folder or an absolute path under home and is never overwritten (default `joined-<time>.mp4`).  Audio is dropped; encoding is bounded to 15 minutes and the result to one hour. **`mode=sheet`** instead writes a matched PNG contact sheet: one row per video (named above its row), one column per time in `times` (default 0.5, 1, 2, 4, 6, 9 s, the G1 recovery convention for an uncut 10 s clip; up to 12; at most 6 per row), every cell labelled `t = N s`, from one fast seek per frame with the bundled ffmpeg. A time past a clip's end is an error, not a blank cell. `job_id` takes the videos of an Isaac Lab job (`isaaclab__runs_status` `video_paths`) instead of `inputs`. The PNG goes to a new file in `~/Pictures/screen-mcp` (bare name or default `sheet-<time>.png`) or an absolute `.png` path under home, never overwriting. The result has an `attachment` JSON object: `upload` is exactly the argument for `t3_attachment_prepare_upload` (`type`, `name`, `mimeType`, `sizeBytes`; images up to 10 MiB, else a `warning`), with `file_path`, `sha256` and `next_steps`. The tool does not upload: POST the file's bytes to the returned URL, then `t3_thread_send_attachments`. **`mode=frames`** turns saved image frames into one MP4 with no display or window, which is the video path for a headless simulator or a locked screen that has no window to record: `inputs` is one folder, one glob pattern such as `/out/replay-*.png`, or the `.png`/`.jpg` files in order (sorted naturally, so `-2` comes before `-10`); they must be under the home folder or a simulator's output folder. `fps` defaults to 30, and `label` (one line, up to 120 characters) is drawn on every frame. `sim__trajectory__replay` uses the same encoder. If ffmpeg is missing it fails with that message. |
| `screen_recording_status` | none | Report active recording, elapsed time and frame count, or the most recent recording. |

Examples: `list_windows` with `{"filter":"Isaac"}`; `screenshot` with `{"window_id":12345}`;
X11 reuses window IDs after a window closes. On a shared desktop, list windows again right before `screenshot` or `start_screen_recording` with a `window_id`. A recording lives in the MCP server process. If that client exits first, it leaves a very short MP4, and a later `stop_screen_recording` from another process reports none active. Keep one client connected from start to stop.

`start_screen_recording` with `{"window_id":12345,"fps":15,"max_seconds":20}`; then
`stop_screen_recording` with `{}`. For a demo with Isaac Sim and RViz 2 together: `tile_windows` with
`{"window_ids":[12345,23456]}`, then `start_screen_recording` with `{"window_ids":[12345,23456]}`; join clips
afterwards with `join_videos` and `{"inputs":["intro.mp4","demo.mp4"],"output":"final.mp4"}`. Window capture reads the selected window's pixels, including when another window
covers it. For a clean simulator viewport without desktop chrome, use the twin's `ur5e_dt__start_recording` instead.

Recording defaults: `full_screen` is false and `max_width` is 1920. A window keeps its native size up to 1920 wide. Explicit
`full_screen=true` letterboxes into the monitor's shape at most `max_width` wide; pass `max_width=3840` for
full-resolution full-screen output.

On a locked or remote PC (monitor captures black, X input refused): window screenshots and recordings,
`window_ids` recordings and locked monitor recordings work from window pixels; `move_window` and `tile_windows`
worked on GNOME/mutter (Ubuntu 22.04) while locked; `raise_window` cannot work and says so. A composed locked
monitor recording costs about 150 ms per frame for six windows at 3440×1440 (about six distinct captures per
second, with frames repeated to keep real time), so prefer `window_ids` for the windows that matter.

### Optional desktop input

These tools use the existing `python-xlib` dependency and the X11 XTEST extension. No `xdotool` installation is needed.
They work on the host's shared X11 desktop; native Wayland and headless sessions are not supported. They do not require
a bridge and can operate windows other than Isaac Sim/Lab, so enable them only for a client you trust. They are off by
default even when `ISAAC_MCP_LOCAL_TOOLS=all`. Window activation requires a window manager that implements
`_NET_ACTIVE_WINDOW`.

| MCP tool | Additional arguments | Use |
|---|---|---|
| `window_click` | `x`, `y`, `expected_width`, `expected_height`, `button="left"`, `clicks=1` | Click once or twice; buttons are `left`, `middle`, `right`. |
| `window_drag` | `x`, `y`, `end_x`, `end_y`, `expected_width`, `expected_height` | Left-button drag inside one window. |
| `window_scroll` | `x`, `y`, `expected_width`, `expected_height`, `direction`, `steps=1` | Scroll `up`, `down`, `left` or `right`, 1–10 steps. |
| `window_type` | `text` | Type 1–200 printable ASCII characters without using the clipboard. |
| `window_key` | `key`, `modifiers=null` | One navigation/edit key, F1–F12, or ASCII letter/digit; modifiers may be `ctrl`, `shift`, `alt` and `super`. |

Every input call also requires integer `window_id` and `expected_pid`, taken from a fresh `list_windows` result.
Identify the actual client window, not an unrelated frame or dialog; some desktops list both. Inspect a fresh
`screenshot(window_id=...)`, then use its **saved size**, not its potentially scaled inline image, for coordinate
arguments. Convert inline-image coordinates by multiplying each axis by saved-size / inline-size. Coordinates are
relative to the selected window's top-left corner. For example, for a 1200×800 window with ID 12345 and PID 6789,
`window_click` takes `{"window_id":12345,"expected_pid":6789,"x":200,"y":100,"expected_width":1200,"expected_height":800}`.
First use `raise_window(window_id=12345, expected_pid=6789)` if the window is covered. Take a new screenshot after an action changes the
UI; refresh the size and coordinates after a resize. Input tool success means events were sent, not that the app has
processed them. A busy simulator can take several seconds to show the result; wait and inspect before the next action.

All `window_*` input is refused while the desktop is locked or its screen saver is active: the lock screen holds the X keyboard and pointer grabs, so input (and typed text) would reach it instead of the window. `window_type` is also refused while another client holds the keyboard grab (an open menu). For Isaac Sim's own UI, use `sim__ui__find` and `sim__ui__act`, which drive Kit's input and keep working on a locked or remote desktop.

Pointer actions fail if the ID/PID or size changed, the point is offscreen, or another window covers a checked point.
Keyboard actions require focus inside the selected window; click it first. Caps Lock must be off for `window_type`
and letter keys. The key mapping must produce the requested characters with no modifier or Shift; alternate keyboard
groups or layouts can still affect the result, so verify it in the UI. A held key,
mouse button or modifier causes the action to fail. Popup menus or dialogs outside the chosen window may need a
separately listed target; unlisted or untitled popups cannot be controlled by these tools. There is no clipboard,
arbitrary shell-command, Unicode typing, or general accessibility-tree API in this first slice.

X11 input is global: these checks reduce mistakes but cannot enforce a security boundary against other X11 clients,
input grabs or a person changing focus. Cooperating MCP servers using the same `DISPLAY` spelling and `XDG_RUNTIME_DIR`
share an input lock; other applications, users, or differently configured servers do not take that lock. A mouse move may cause hover effects
even if a later target check rejects a click. Do not share the desktop concurrently while an agent is acting; use a
dedicated session for stronger isolation.
Enabling the tools grants the client the ability to trigger any action available in the targeted app, including file
deletion or network operations. Tool annotations are advisory, and X11 window IDs/PIDs are not authentication.
See the [working checklist](CHECKLIST.md#tool-surface-and-reliability) for remaining work and validation.

### Optional simulator sessions

Select `sim_launch` explicitly to expose these local Linux tools. They launch this repository's `run.sh` and manage only
processes they started; they do not attach to or stop unrelated simulators. This runs the configured simulator Python,
creates files, and can affect the GPU and network. All four tools have conservative annotations.

| MCP tool | Arguments | Use |
|---|---|---|
| `sim__launch` | `port=null`, `ros_domain=null`, `headless=false`, `allow_shared_domain=false`, `show_ur5e_panel=false`, `enable_motion_bvh=false`, `physics_backend="physx"` | Start a new process with a local bridge, UR5e tools, chosen ROS domain and separate output/log and persistent Kit settings. Omit `port` and `ros_domain` to take the first free, unclaimed values inside the configured ranges (or upward from the configured port and domain); the result reports what was chosen. Set `show_ur5e_panel=true` to show the panel in a graphical session. Optional Motion BVH enables RTX LiDAR motion effects; `physics_backend="newton"` selects the new process’s installed Newton engine with no fallback, and enables Newton's tensor extension at launch where the release ships it separately, so body and joint reads work from the first Play whoever starts it. Returns a session ID and PID before the bridge is ready. |
| `sim__list` | `include_exited=false` | List simulators. Managed sessions have `managed: true`, including those started by an earlier server process; only the 3 newest exited ones are listed unless `include_exited=true` (their records and logs stay on disk). Advertised bridges started some other way have `managed: false`, an ID `external-<pid>-<port>`, and their PID, port, app and version from the portfile; the listing does not call them. |
| `sim__status` | `session_id` | Managed session: check the original PID's identity, bridge and Kit readiness, and current memory snapshot. For a session launched with `physics_backend="newton"` that is running, `physics` reports whether Newton is the active engine and its tensor extension is on (`ready`, `engine`, `tensor_extension_enabled`); wait for `ready: true` before starting work on it. External ID: call that bridge's `status`, report its app, version and readiness, and report `port_conflict` if another PID answers. |
| `sim__stop` | `session_id`, `expected_pid`, `confirm_discard=false`, `force=false` | With `confirm_discard=true`, send SIGINT to the matching managed process. Without it, the error includes the exact retry call. Poll status for exit; if normal shutdown fails, `force=true` sends SIGKILL. Unsaved changes may be lost. It always refuses an external bridge, because isaac-mcp did not start it. |

Example calls: `sim__launch` with `{}` (both values chosen), or `{"port":18766,"ros_domain":42}`, then
`sim__status` with `{"session_id":"<id returned by launch>"}`. Wait until `state` is `running`, select that
port with `use_bridge`, and verify its PID before operating the scene. If necessary, identify its
graphical client with `list_windows` by PID.
With no `port` or `ros_domain`, the launch takes the first value that is free, unclaimed by another
launch and not advertised by a live bridge: inside `ISAAC_MCP_PORT_RANGE` and
`ISAAC_MCP_ROS_DOMAIN_RANGE` when they are set, otherwise from the configured port
(`ISAAC_MCP_PORT` or `UR5E_DT_PORT`, else 8766) over the next 64 ports, and from this server's
`ROS_DOMAIN_ID` (else 0) up to 231. The result's `port` and `ros_domain` are the chosen values, and
`auto_selected` names the ones chosen for you. A stated port or domain outside its range is refused as
`PORT_OUT_OF_RANGE` or `ROS_DOMAIN_OUT_OF_RANGE`, naming the range.
A refused port or ROS domain message suggests a port that is free, unclaimed and not advertised, or
says to use another `ros_domain` (exporting the same `ROS_DOMAIN_ID` in ROS shells) or
`allow_shared_domain=true`. The port check uses `SO_REUSEADDR` like the bridge, so a port in TIME_WAIT
after `sim__stop` is accepted.
Managed launches accept ROS domains 0–231. With the bundled Cyclone DDS profile's participant-index range, domain
232 would make discovery probe beyond UDP port 65535.
Each launch claims its port and ROS domain in the bridge runtime folder (`$XDG_RUNTIME_DIR/isaac-mcp`, else
`/tmp/isaac-mcp-<uid>`): one file per value, created exclusively and holding the claiming host's PID, start
time and boot ID. A second launch is refused while that claim is live, including one from another host
process with a different sessions directory. A claim whose owner has died is removed. Claims are released
when the launch returns or fails; from then on the bridge's portfile and its process environment make its
port, PID and ROS domain visible to later launches on this machine. The folder must belong to you and not be
writable by group or others, and a symlink planted at a claim path is refused. A program that does not make
claims can still take a port or domain.
Each session has its own fake-driver PID file and log. Stop that driver before stopping the session; `sim__stop`
does not stop its separate process group. The fake launcher still prevents startup alongside another detected UR
driver. The shared-resource URSim backend is unavailable in managed sessions. These protections apply to newly
launched sessions; restart a session started with an older version so it receives the session identity.
`starting` means Kit reports it is still initializing. `readiness_unknown` means the bridge answered but cannot
report Kit readiness (for example, an older bridge); verify the application before using scene tools. An unavailable
bridge reports `starting_or_unresponsive`.
The UR5e tools are enabled in either mode. To show their tab at launch, add `"show_ur5e_panel":true`.
The `show_ur5e_panel_on_start` field records the launch choice, not whether it remains visible later.
The extension can also be enabled in an already running simulator; see [UR5e tools](TOOLS-UR5E.md#simulator-window).
Use different ROS domains for isolated simulations; `allow_shared_domain=true` is an explicit exception when they
intentionally need to communicate. Other ROS nodes must use the matching domain to communicate with a simulator.

At server startup the operator may set these environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `ISAAC_MCP_SIM_ROOT` | repository root when available | Repository containing the executable `run.sh`. Set it explicitly if the host is installed outside a checkout. |
| `ISAAC_MCP_PYTHON` | `<root>/.venv/bin/python`, else auto-detected | Isaac Sim interpreter used by `run.sh`. `UR5E_DT_PYTHON` is a deprecated alias. Auto-detection only finds 6.x; a 5.1 interpreter works when named here. `physics_backend="newton"` refuses before starting anything when the interpreter's Isaac Sim has no Newton adapter, such as 5.1. |
| `ISAAC_MCP_SIM_LAUNCHER` | unset, so `<root>/run.sh` | [A project's own launcher](#a-projects-own-launcher): an absolute path to an executable. |
| `ISAAC_MCP_SIM_SESSIONS_DIR` | `~/.local/share/isaac-mcp/sim-sessions` | Managed session metadata, logs and separate output directories. A relative path is resolved against the server's working directory. |
| `ISAAC_MCP_PORT_RANGE` | unset | Ports `sim__launch` may use, as `18800-18899`. An omitted port is chosen inside it; a stated port outside it is refused. |
| `ISAAC_MCP_ROS_DOMAIN_RANGE` | unset | `ROS_DOMAIN_ID` values `sim__launch` may use, as `40-49`. An omitted domain is chosen inside it; a stated domain outside it is refused. |
| `ISAAC_MCP_SIM_MAX_INSTANCES` | `2` | Maximum known bridge, managed Sim and configured Lab-job processes. `0` disables this count limit. |
| `ISAAC_MCP_SIM_MIN_FREE_VRAM_MIB` | `8192` | Minimum free GPU memory at launch. |
| `ISAAC_MCP_SIM_MIN_FREE_RAM_MIB` | `4096` | Minimum available system RAM at launch. |

A launched simulator inherits this server's environment, so `ISAAC_MCP_ASSET_PATH` and `DISPLAY` come from the `env` of your MCP
client's server entry, not from a shell you opened later. It removes `PYTHONPATH`, `UR5E_DT_HEADLESS` and `UR5E_DT_SHOW_PANEL`, sets its own port, bind address, ROS domain, backend and output folder, and
passes `ISAAC_MCP_PYTHON`, `UR5E_DT_PYTHON` and `ISAAC_MCP_WORKSPACE` only from the server's own settings.

The count includes advertised local bridges, managed sessions still starting, and running jobs in the configured
`ISAACLAB_JOBS_DIR`; processes outside those sources may not be counted. Port availability and ROS domains of known
processes are checked, but other unadvertised ROS nodes may share a domain. Host servers using the same sessions
directory serialize their launches, and each launch claims its port and ROS domain across host processes; other
programs or differently configured servers that do not claim them can still race the checks. Current resource checks require Linux,
`nvidia-smi`, and exactly one NVIDIA GPU. The memory thresholds and instance limit are operator settings, not guarantees:
they do not reserve memory or predict scene, renderer, sensor or training peaks. There is no automatic termination if
memory later runs low. Monitor workloads and tune the limits only after testing. Isaac Lab jobs have their own
concurrency guards and explicit overrides; see [Lab tools](TOOLS-ISAACLAB.md#concurrency-guards). Managed sessions use
separate Kit user configuration files, but other Kit caches and machine resources can still be shared.
Stopping uses a Linux pidfd and the original boot and process-start identity to avoid signalling a reused PID.
Linux 6.9 and newer receive the signal for the process group; older kernels receive it for the leader only, reported
as `scope: "process"`, and a forced stop there may leave child processes that require separate cleanup.

### A project's own launcher

A project with its own environment can have `sim__launch` start its launcher instead of `run.sh`. Set
`ISAAC_MCP_SIM_LAUNCHER` in the `env` of that project's MCP server entry. It is read when the server starts.

The launcher gets the same arguments as `run.sh` (`--port N`, `--headless`, `--ur5e-panel`, `--motion-bvh`, then
`-- <kit args>`) and the same environment, including `ISAAC_MCP_PHYSICS_BACKEND`, and runs in its own folder. It must
end by `exec`ing Kit, because the PID that `sim__launch` records must be the simulator; otherwise `sim__status` reports
`port_conflict`. The file must be executable, belong to you or root, and not be writable by group or others. For
example, to use 5.1 with this repository's launcher:

```bash
#!/bin/bash
export ISAAC_MCP_PYTHON=<project>/.venv/bin/python
exec <repo>/run.sh "$@"
```

With a launcher set, the Newton check above is the launcher's job (`run.sh` also refuses, with exit status 2).

This setting is deliberately not in `isaac-mcp.json` and not a tool argument. A launcher is an arbitrary command, and a
model can write files on this machine (`put_file` has no path limit), so a project file would let it choose what runs.
The server configuration comes from you, through your MCP client. `isaaclab_python` in `isaac-mcp.json` is a narrower
exception: it is honored only for a project you named with `ISAAC_MCP_PROJECT` in that same configuration, and it must be a virtual environment with Isaac Lab ([details](TOOLS-ISAACLAB.md#setup-and-configuration)).

## Kit bridge built-ins

These tools operate on the **bridge machine**, inside or alongside the Kit process. The host lists them from a packaged
snapshot (`mcp/isaac_mcp/catalogs/builtins.json`, regenerated by `scripts/gen_host_catalog.py --write`) unless
`ISAAC_MCP_BUILTINS=0`; each call is sent to the selected bridge, and without one it returns an error that says what to do.

| MCP tool | Arguments and defaults | Use and effects |
|---|---|---|
| `status` | `detail=false` | **Where am I, in one call.** The reply is short: `running`, the selected bridge's `pid`, `port`, `app`, `app_version`, `headless`, `app_ready` and `uptime_s`; `stage` with the `session_id` and `stage_id` the scene tools need, its `source`, `timeline` (playing, paused or stopped) and `physics_engine`; `jobs_running`; `bridges` when more than one is advertised on this machine; `managed_sessions` and `lab_jobs_running` when there are any; and a short `host`. With no bridge it is still an answer, not an error: `running: false`, a `message` naming the next call (`sim__launch`, or `use_bridge` when other bridges are advertised), and the same surroundings. Call it once before the first simulator action; a bridge that answers but is busy, and a timeout, are still errors. `detail=true` returns the full bridge identity instead: process identity, port, app version, Kit `app_ready` (`true`, `false`, or `null` if unknown), headless state, registry information, `admission`, `hold` and `jobs`. With `detail=true` the host adds `host`: `version`, `pid` and, from a checkout, `commit`, `checkout_commit`, `stale`, and a `warning` if the checkout moved on since this server started. Restart the MCP connection then. It compares commits only, so a docs-only pull warns and uncommitted edits do not. The bridge adds `extension`: the `root` of its tree, the `commit` Kit started from, the `checkout_commit` now, `dirty` (with `dirty_paths` and `dirty_count`), `hot_reload` (is Kit's folder watcher on) and `reloads` (extensions restarted inside this process: count and time). These come from git's files without starting a process; an exported tree with no `.git` reports commit `unknown` and `dirty: null`. `dirty` covers tracked files under `exts/` and `mcp/isaac_mcp/`, not new untracked files. The host then adds `bridge_match` (`true`, `false`, or `null` when either side does not know) and warns if the commits differ or the bridge's tree has uncommitted changes. |
| `list_bridges` | none | List bridge instances advertised on the bridge machine. |
| `run_python` | `code` (string, required); `session_id="default"`, `persistent=false`, `background=false`, `label=null` | Execute Python on Kit's main thread with `omni`, `carb` and common USD symbols preloaded. Assign `result` to return a value. Persistent sessions retain eligible variables for the same ID; a stage-open event clears session state when the subscription is available. The reply's `session_vars` lists only the names this call added or rebound, with `session_var_count` for the total, so a long session does not echo every variable each call. `background=true` runs the code as a [job](#background-jobs) and returns its `job_id` at once: the code must `await` (for example `next_update_async()` in its loop), gets a preloaded `job` (`job.report(fraction, text)`, `job.checkpoint()`), and its `result` and prints come back through `job_status`. Arbitrary code can change the scene or access files. |
| `job_status` | `job_id=null`, `wait_s=0` | State of a [background job](#background-jobs): `running`, `succeeded`, `failed` or `cancelled`, `elapsed_s`, `progress`, the last 2000 characters of output, and the `result` or `error` when it has ended. `wait_s` (0–60) waits for the job to end instead of polling, and sets `wait_timed_out` if it is still running. Without `job_id` it lists the simulator's jobs, newest first. For a finished `sim__trajectory__replay`, or a `sim__simulation__run` recorded with `camera`, the host encodes the frames and adds `video_path`. It also takes the `job_id` of an Isaac Lab or script job (`isaaclab__train`, `run_script`, ...): the host answers that itself with what `isaaclab__runs_status` gives, no bridge needed, so one tool follows every job. |
| `job_cancel` | `job_id` (string, required) | Ask a running job to stop. It ends at its next step and cleans up after itself; confirm with `job_status`. An Isaac Lab or script `job_id` is stopped as by `isaaclab__runs_cancel`. |
| `reload_tools` | `module` (string, required) | Reload an autoloaded tool package by module name, then call host `refresh_tools` to update the client-facing registry. If that pack has a call in flight, reload is refused until it finishes. |
| `put_file` | `path`, `content_b64` (strings, required); `mkdirs=true` | Decode base64 and write or overwrite a file on the bridge machine; creates parents by default. |
| `get_file` | `path` (string, required) | Read a file on the bridge machine and return base64 content. |
| `stat_file` | `path` (string, required) | Report whether a bridge-machine file exists, with its size and SHA-256 when present. |

### Background jobs

Work that takes longer than one call should not be polled with more calls. `run_python` with `background=true`,
`sim__simulation__run` and `sim__trajectory__replay` start a **job** in the Kit process and return a `job_id` at once;
`job_status` reads it (and waits, with `wait_s`), `job_cancel` stops it, and `status` lists the running ones. This is
the simulator-side counterpart of the Isaac Lab job tools, which manage separate processes.

- A job is a coroutine on Kit's main loop. It must only `await`; it must not pump the app loop itself.
  At most 4 run at once, and the last 32 finished ones are remembered until Kit restarts.
- Every job also has a record on disk, `<jobs folder>/<job_id>/job.json`, in the same format as an Isaac Lab job
  (`"runner": "kit"`). So `job_status` still answers after the simulator has exited (a job that was running then reads
  `failed`: the simulator exited before it finished), and `jobs(action="list")` shows simulator and Lab jobs together.
  The jobs folder is `ISAACLAB_JOBS_DIR`, or `~/.local/share/isaac-mcp/jobs`.
- A job is not a queued request. Another caller's `run_python` that pumps the app loop from inside its own
  call can end a job; the job then reads `failed` with the reason, never `running` forever.
- A result larger than 16 KiB is cut and says so: write large data to a file and return its path.
- `wait_s` is served inside Kit, off the main loop: the call returns when the job ends, with no polling and no
  simulator time spent, for the MCP host and a direct socket caller (`isaac_mcp.client`) alike.
- `job_status` and `job_cancel` are bridge built-ins, so `ISAAC_MCP_BUILTINS=0` hides them with `status` and
  `run_python`. A client set up that way can start `sim__simulation__run` and `sim__trajectory__replay` but cannot
  follow them; leave the built-ins listed for an agent that uses those tools.

```python
# run_python, background=true: one call that runs the whole loop
import omni.kit.app
app = omni.kit.app.get_app()
for i in range(600):
    job.checkpoint()                      # stops here after job_cancel
    await app.next_update_async()         # let Kit run a frame
    if i % 60 == 0:
        job.report(i / 600, f"frame {i}")
result = {"frames": 600}
```

Examples: `run_python` with `{"code":"result = 1"}`; `stat_file` with
`{"path":"/tmp/isaac-example.txt"}`; `put_file` with
`{"path":"/tmp/isaac-example.txt","content_b64":"aGVsbG8="}` writes `hello`. File content is transmitted as
a single message, so these tools are for reasonably sized files rather than bulk multi-gigabyte transfers.

## Trust and operational limits

The bridge listens on loopback and requires a token on every connection. The token is in a file only your account
can read, so your own clients connect without configuration and another local account cannot run Python in your
simulator. The token is not encryption: do not expose the bridge to an untrusted network; use an SSH tunnel, or a
private address with `ISAAC_MCP_TOKEN` given to the client. `ISAAC_MCP_READ_ONLY=1` starts a bridge that refuses every
tool that changes something. `ISAAC_MCP_BUILTINS=0` only hides the built-ins from an MCP client's list. See
[limiting what a client can reach](MCP-SERVER.md#limiting-what-a-client-can-reach).

Desktop capture depends on the display session and permissions, and can contain sensitive information. Confirm the
target window before capturing. See [server setup](MCP-SERVER.md), [architecture](ARCHITECTURE.md), and
[tool authoring](../mcp/docs/tool-authoring.md) for more detail.

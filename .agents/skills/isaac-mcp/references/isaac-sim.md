# Isaac Sim through isaac-mcp

Read this before calling bridge tools or editing a live stage. Full references are
`docs/TOOLS-HOST.md`, `docs/TOOLS-UR5E.md` and `docs/TOOLS-SCENE.md` in the isaac-mcp repository.

## How the pieces connect

```
MCP client ──stdio──► isaac-mcp (host process)
                        │  NDJSON over TCP (loopback by default)
                        ▼
                      isaac.mcp.bridge (extension inside Kit: Isaac Sim or Isaac Lab)
                        ├── built-ins: status, list_bridges, run_python, job_status, job_cancel,
                        │              reload_tools, put_file, get_file, stat_file
                        └── tool packs registered under a namespace, e.g. ur5e_dt.*, sim.*, assets.*
```

Bridge tools appear in MCP as `<namespace>__<tool>` (dots are not allowed in MCP names), so
`ur5e_dt.play_scene` is called as `ur5e_dt__play_scene`. The host and the bridge are separate
processes: screen and Isaac Lab tools working says nothing about the bridge.

## Choosing the bridge

- One bridge per Kit process. The supplied twin (`./run.sh`) listens on 8766 unless `UR5E_DT_PORT`
  or `--port` says otherwise; other apps may use other ports.
- Endpoint selection order: explicit port, then `UR5E_DT_PORT`, then `ISAAC_MCP_PORT`, then a lone
  advertised bridge. With several bridges, discovery refuses to guess: call `use_bridge`.
- `use_bridge` lasts until the host restarts. Re-check `status` after switching.
- Reuse the intended reachable bridge. Start an additional isolated simulator only when the
  user requested one and resource checks pass. With the
  optional `sim_launch` group, `sim__launch` starts an isolated session (own port, ROS domain,
  output directory) after resource checks; poll `sim__status` until `state` is `running`, then
  `use_bridge` to its port. `sim__stop` needs `expected_pid` and `confirm_discard=true`.

## run_python

`run_python` executes on Kit's main thread. Preloaded names: `omni`, `carb`, `Usd`, `UsdGeom`,
`Sdf`, `Gf`. Assign `result` to return a value; it must be JSON-serialisable, so convert USD
types (`str(path)`, `list(vec)`).

Read the stage before changing it:

```python
stage = omni.usd.get_context().get_stage()
result = {
    "root_layer": stage.GetRootLayer().identifier,
    "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
    "prims": [str(p.GetPath()) for p in stage.Traverse()][:200],
}
```

Check a prim and its world transform. USD prim paths are case-sensitive: the supplied twin
uses `/World/UR5e`. For imported robots, discover the actual path with `sim__stage__tree` or
`sim__prim__find` using the current stage identity; substitute that path below rather than
assuming the twin's name applies. Prefer `sim__prim__get` when a named read is sufficient:

```python
stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath("/World/UR5e")
if not prim.IsValid():
    result = {"error": "no prim at /World/UR5e"}
else:
    xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    result = {"type": prim.GetTypeName(), "translation": list(xf.ExtractTranslation())}
```

Timeline state:

```python
import omni.timeline
tl = omni.timeline.get_timeline_interface()
result = {"playing": tl.is_playing(), "time": tl.get_current_time()}
```

Rules of thumb:

- Keep each call short. Long loops block Kit's main thread and the UI with it.
- `persistent=true` with a `session_id` keeps variables between calls; opening a new stage clears
  them because their prim handles are dead.
- Prefer a namespaced tool over `run_python` when one exists, and `reload_tools` (then
  `refresh_tools`) after editing a tool pack instead of restarting Kit.
- `run_python` and the file tools run with the Kit process's permissions. Do not run code you have
  not read, and do not expose the bridge beyond loopback.

## Seeing the simulator

- `screenshot` without a selector picks the Isaac Sim / Isaac Lab window and captures it even when
  covered. `list_windows` with `{"filter": "Isaac"}` gives ids for `window_id`.
- `start_screen_recording` / `stop_screen_recording` record a window or monitor to MP4 (no audio).
  For a clean viewport without desktop chrome, use the twin's `ur5e_dt__start_recording`.
- Optional `screen_input` tools (`window_click`, `window_drag`, `window_scroll`, `window_type`,
  `window_key`) act inside one window. They need `expected_width` / `expected_height` from a fresh
  screenshot, so take one first. Prefer `run_python` or a named tool over clicking UI.

## Generic scene and project assets (`sim__*`, `assets__*`)

The `sim`/`assets` pack is on by default (`run.sh` enables `sim-tools`). `ISAAC_MCP_SCENE_TOOLS=0`
on the host hides all of them per server; it is a visibility preference, not access control.
Arguments are in `docs/TOOLS-ARGUMENTS.md`. `ur5e_dt__quick_start` works from public files (check `gripper` and `assets` in its result; local files always win); the workstation and object tools need the user's lab files. `ur5e_dt__quick_start*` leaves the timeline playing, so call
`sim__timeline__control` stop before generic edits. After reopening a scene, use `adopt=true` on `sim__lidar__read` and `sim__camera__capture`, and `allow_foreign=true` on `sim__prim__delete` (it deactivates; the path stays taken). In RAS545 Lab 3, the ground plane sits at z=-0.75, so a robot settling there after Play is not falling through the floor, and the starter reports `composition_errors: 2` on opening. Joint tools take the articulation root
(`/World/UR5e/root_joint` on the twin), not the parent Xform, and a new stage needs `/World` created first.

- Call `sim__stage__info` first. Keep its `session_id` and `stage_id`; every call except discovery
  and stage-info needs them. A `STALE_TARGET` refusal returns `current_target`; resend those IDs
  explicitly, because mutations never retarget silently.
- Read with `sim__stage__tree`, `sim__prim__get`, `sim__prim__find`, `sim__physics__inspect` and
  `sim__joints__inspect`.
- Authored edits require a stopped timeline: `sim__prim__create` / `sim__prim__set_transform` /
  `sim__prim__clone` / `sim__prim__delete` (`confirm=true`), `sim__stage__clear_owned`,
  `sim__light__*`, `sim__material__*`, `sim__physics__configure`, `sim__joints__drive`. They write
  to the session layer only; source USD files are not rewritten.
- `assets__roots` → `assets__scan` / `assets__search` → `assets__info` →
  `assets__import_path`. Only launch-approved roots are accepted; there is
  no URL input and no untrusted/hostile-USD isolation. Import needs matching units/up-axis, a
  stopped timeline and a free wrapper path.
  For an absolute USD path under an approved root, use `assets__import_path` with current
  session/stage IDs. For the twin's workstation cache, `assets__prepare_workstation` prepares
  a private copy but returns `loaded:false`; follow with `ur5e_dt__load_workstation` and inspect
  `/World/Vention`. See [the main skill](../SKILL.md) and `docs/TOOLS-SCENE.md` for cache
  precedence, approved-root setup and dependency limitations.
- Runtime state: `sim__timeline__control`, `sim__simulation__step` while paused,
  `sim__body__state`, `sim__joints__state` and `sim__joints__command` after physics is initialized.
- Capture and sensors: `sim__camera__create` / `sim__camera__capture` (RTX), `sim__lidar__*`
  (`config=` an NVIDIA name such as `Example_Rotary_2D`; Motion BVH optional).
  `sim__viewport__frame` (native framing, `margin`, `eye`/`target`, `follow`) edits the active
  camera in the session layer and, for native framing, changes selection, with no Kit undo entry. Its `render_complete=false` is not
  proof of a rendered view: allow ordinary rendering, then inspect the verified application
  window. Native failures can leave partial selection/session-camera effects; inspect before
  retrying. A smaller geometry subtree may avoid surrounding occluders. See
  `docs/TOOLS-SCENE.md` for unsupported contexts, camera opinions and target bounds.
  The default capture waits for fresh render
  completions and a new renderer CPU transfer (`cpu_transfer_completed=true`);
  `wait_for_render=false` reads only the last validated tool-acquired CPU cache, with age and
  acquisition context. An empty cache returns `NOT_READY`: use a default/true capture to seed
  it. Ordinary Play or waiting more frames does not seed or refresh the cache. Cached pixels
  may predate Stop/reset or pose edits; sensor/stage teardown clears them. Acquisition commits
  before PNG publication, so a filename conflict can leave newly acquired pixels cached while
  the file write fails. Fresh capture's `RENDER_TIMEOUT` instead means render/CPU transfer did
  not finish within five seconds; inspect rendering and the Kit log. No image or stale fallback
  is returned. A pending native transfer can cause `RENDER_BUSY` until it completes.
  LiDAR keeps its separate latest-completed-scan behavior and requires Play for acquisition.
  Saved scenes omit recognized process-bound `/Orchestrator` graphs; re-export older saved
  scenes and create fresh sensors after reopening, following the save policy below.
- `sim__stage__save` writes a new basename; it never overwrites or switches the current stage.
  It omits recognized process-bound `/Orchestrator` and SyntheticData graphs; re-export older
  saved scenes before camera readout after reopening. ROS camera handles may require the
  explicit `reset_camera_render_handles` export policy described in [the main skill](../SKILL.md);
  a successful save still needs fresh-process reopen verification. `sim__logs__tail` reads the Kit log.
- `sim__controller__*` execute inline Python in a ScriptNode. Use them only with
  `ISAAC_MCP_ENABLE_SCENE_SCRIPTS=1` at Kit launch and an explicit user request; this is not a
  sandbox.

## UR5e twin pack (`ur5e_dt__*`)

The twin extension registers scene tools for a UR5e workstation: `ur5e_dt__quick_start*` variants
build a scene, `ur5e_dt__play_scene` / `ur5e_dt__stop_scene` control playback, object tools add,
move, randomise, save and restore objects, and `ur5e_dt__start_recording` captures the viewport.
`ur5e_dt__start_ros_driver` brings a ROS 2 driver up or down; use `backend="fake"` (simulation
only) and never `mode="real"` in a simulation task. Stop the fake driver before stopping its
simulator. The catalog can be listed with Kit down; calls need a selected bridge running
the twin.

## Typical safe sequence

1. `refresh_tools` → bridge listing → `use_bridge` if needed → `status`.
2. For generic work, `sim__stage__info` and keep its `session_id` / `stage_id`. Otherwise read
   with `run_python`.
3. Read: `sim__stage__tree` / `sim__prim__get` or `run_python`, plus `screenshot`.
4. Change: one named tool (`sim__*`, `assets__*`, `ur5e_dt__*`) or one small `run_python` edit.
5. Verify: read the changed prim back, `screenshot` again.
6. Report what changed, with the PID, port and (for generic tools) the session/stage IDs.

## ROS 2 from an agent shell

Lessons from the TurtleBot3/Nav2 lab.

- Your shell must use the simulator's DDS settings or `ros2 topic list` stays empty while Kit
  publishes: `sim__stage__info` returns them under `ros` with the exact shell line to run
  (`sim__graph__build` repeats it for ROS graphs).
- `source scripts/ros_env.sh [install] [--overlay <nav2 env>]` (after `export ROS_DOMAIN_ID=<sim's>`)
  sources ROS, then `config/ros_dds.env`, then stops the `ros2` daemon. A daemon started with other
  DDS settings makes `ros2 topic list` / `node list` empty while data flows; otherwise use
  `--no-daemon` (`ros2 topic hz` has no such flag).
- Kit's bridge nodes do not show in `ros2 node list`; verify with topics and `/tf`.
- Background RViz needs a valid desktop `DISPLAY` exported in the launching shell alongside
  `ros_env.sh` (for example `export DISPLAY=:0` when that is the desktop's display).
- The harness kills a tool call's process group when it ends: start RViz and other long-lived
  processes with `setsid nohup <cmd> > log 2>&1 < /dev/null &`, record `$!`, stop that PID.
- Save your scene before stopping a managed simulator, then call
  `sim__stop(session_id=<managed session id>, expected_pid=<its pid>, confirm_discard=true)`.
- Bound every ROS command: `ros2 topic pub --times N`, `timeout -s KILL <s> ...`. A plain
  `timeout 6 ros2 topic pub ... | tail` hung 120 s while the robot drove into a wall.
- Keyboard teleop needs a TTY. Drive with `python3 scripts/cmd_vel_sequence.py "0.15,0,5;0,0.5,3"`
  (m/s, rad/s, s per step); it always publishes zero at the end, also on Ctrl-C/SIGTERM.
- Nav2's `map` frame is the world frame: the map YAML `origin` is only the image's corner, so
  do not offset goals by it.
- Same distro on both sides: Jazzy on Ubuntu 24.04, Humble on 22.04 (`run.sh` picks by OS unless
  `ROS_DISTRO` is exported). Jazzy and Humble did not exchange messages with these DDS settings.
- RViz layout: `scripts/ros2_rviz_config.py --view topdown --scale 80 --target-frame base_link
  --hide-panels` (top-down, follows the robot, 3D view only). `Hide Left Dock: true` in a
  config does nothing in RViz2.

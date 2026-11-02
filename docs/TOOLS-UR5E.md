# UR5e Digital Twin MCP Tools (`ur5e_dt__*`)

This document covers the 23 tools registered by `exts/ur5e-dt/ur5e_dt/extension.py`
on the shared Kit bridge. It is the implementation reference; it documents the
present implementation, not the proposed generic sim tool pack
([GENERIC-SIM-TOOLS.md](GENERIC-SIM-TOOLS.md)).

## Naming: bridge vs. MCP

- Inside Kit the tools are registered under the canonical namespace with **dots**:
  `ur5e_dt.<tool_name>` (e.g. `ur5e_dt.play_scene`).
- The host server (`mcp/isaac_mcp/server.py`) exposes namespaced tools with **double underscores**: `ur5e_dt__<tool_name>`
  (e.g. `ur5e_dt__play_scene`). Dispatch maps `__` back to `.`.
- Bridge built-ins (`status`, `run_python`, `list_bridges`) and host gateway tools
  (`use_bridge`, `refresh_tools`) and desktop-capture tools
  (`screenshot`, `start_screen_recording`, …) carry **no** `ur5e_dt__` prefix.

The twin bridge listens on port `8766` by default (`UR5E_DT_PORT` /
`ISAAC_SIM_PORT` override). See [ARCHITECTURE.md](ARCHITECTURE.md) §4 and
[MCP-SERVER.md](MCP-SERVER.md).

Through the host MCP server, bridge connection failures, timeouts and explicit
`{"status":"error", ...}` results set MCP's `isError` flag. Legacy diagnostic
text/JSON and the string-valued `structuredContent.result` remain available;
clients must not interpret a returned error message as successful execution.
A lost response or timeout does not establish cancellation or rollback. Inspect
the selected simulator's identity, state and native log before retrying a mutation.

## Simulator window

Plain `./run.sh` starts Isaac Sim, the bridge and the twin extension, but hides the **UR5e Digital Twin**
panel by default. The 23 tools work with the panel hidden. Pass `--ur5e-panel` (or use the dedicated
`scripts/launch_ur5e_dt.sh`) to show the panel; in a graphical session it docks alongside Console when
available and can be moved like other Kit tabs. `./run.sh --headless` has no visible simulator window.
The twin panel is separate from the optional `isaac.mcp.panel` registry inspector.

Managed `sim__launch` sessions also start the twin tools with the panel hidden. Pass
`"show_ur5e_panel":true` to show it in a graphical launch. Neither launching the extension nor showing its
panel loads a robot scene; use Quick Start or a twin tool for that. The host MCP server lists these tools even
when no simulator is running, but executing them requires a selected bridge that serves the extension.

In a simulator started without the extension, use Kit's Extensions window to enable `ur5e-dt`, or, if the
`run_python` bridge built-in is available, call it on the intended bridge with:

```python
manager = omni.kit.app.get_app().get_extension_manager()
manager.set_extension_enabled_immediate("ur5e-dt", True)
result = {"enabled": manager.is_extension_enabled("ur5e-dt")}
```

Call `refresh_tools` afterward to obtain the live bridge's metadata. Disabling the extension unregisters the live
tools, but their catalog entries remain visible and report that the extension is unavailable when called. Stop active
recordings and other twin work before disabling it. If built-ins are filtered, `run_python` is unavailable; use the
Extensions window or start a managed session instead. Namespace filters can also hide the twin tools.

Several panel actions, including Quick Start, loading the workstation or room, playing/stopping the scene and
recording, have MCP tools in the reference below. The buttons and tools are interfaces to extension operations;
they are not a one-to-one list. Host screen tools can inspect and capture simulator windows, but
optional X11 mouse and keyboard tools can be enabled separately as described in
[host tools](TOOLS-HOST.md#optional-desktop-input). Not every panel control has a dedicated MCP equivalent.

Quick Start constructs the twin's simulation World on the **CPU**, retaining the
selected physics engine. This makes the twin's existing CPU scene policy explicit
for Newton too: its default CUDA device is incompatible with the legacy
articulation reset's CPU gain arrays in the inspected 6.2 build. An already
initialized non-CPU World is refused without changing its device; use a separate
disposable stage/session for the twin. Quick Start does not reset a stage on its
own. Generic tools retain their own selected device and backend; this does not
establish that every legacy twin operation works on Newton. See the measured
[compatibility matrix](COMPATIBILITY.md) for qualified workflows.

The Newton twin adapter preserves separate UR5e and RG2 articulations. The
QuickChanger joins the arm's tree; a MuJoCo equality weld attaches the RG2.
Newton 1.0's USD importer cannot reliably resolve this separate-articulation
attachment, so complete Quick Start refuses before assembly on that importer.
Moving the joint to another USD path does not fix its parser behavior. Generic
Newton tools and standalone robots have separate coverage; there is no implicit
fallback to PhysX.

Newton uses force-drive gains of 30 N·m/rad and 8 N·m·s/rad for the actuated RG2
joint, retaining its 1 N·m cap and five mimic constraints. These are explicit
controller gains, not a unit conversion of the PhysX acceleration drive.
Pad-gap observations use runtime link poses, since Newton's Fabric poses need
not appear in authored USD transforms. PhysX retains its original controller.

On a backend providing the opt-in incoming-joint-wrench output, the twin enables
that output before physics initialization and publishes the named wrist's
tree-parent wrench at the `tool0` origin and orientation, with physics-time ROS
timestamps. It does not assume that the last link is the wrist. The output is
not a separate sensor for the RG2 loop-closing equality constraint. The inspected
6.2 backend supports this output for MuJoCo Warp and XPBD; native MuJoCo CPU and
VBD do not. A CPU *device* does not select the native MuJoCo CPU solver. Actual
qualification is narrower than API availability; consult the matrix. Older
backends lacking this output, or an already initialized scene with the output
disabled, report `force_publisher.available: false` and its reason instead of
publishing placeholder zero wrenches. No active solver is rebuilt to enable it.

PhysX and Newton both select the named `wrist_3_link`, transform its incoming
joint force and moment into `tool0`, and restore that frame name and the observed
physics timestamp on every valid sample. This corrects the legacy PhysX publisher's
last-row assumption and zero/empty headers after reopening. Fresh graphs gate
publication until a complete sample is available. Legacy PhysX graphs without a
validity gate remain unchanged when reopened and can publish default or cached
values before a valid callback; use a new disposable twin stage to create the
guarded graph. Read `force_publisher` and verify actual messages after warm-up.

## Parameter conventions

- Parameter shapes come from `MCP_TOOL_REGISTRY` in `extension.py`; required/default
  enforcement comes from the `_cmd_*` handler signatures.
- The MCP schema marks every parameter optional (no `required`/`default` keys in
  the registry). A handler may still reject a missing value (e.g. `object_name`,
  `position`) — those are marked **required (handler)** below.
- Defaults listed are the handler-signature defaults. Handlers also accept an
  internal `output_dir` that is **not** exposed by this MCP schema and is omitted below.
- Handlers return dictionaries, typically with `status` and `message` plus tool-specific fields. The MCP host
  serializes bridge results as JSON text; transport failures may instead be reported as an error string.

## Quick reference

| MCP name | Purpose |
|---|---|
| `ur5e_dt__play_scene` | Start/resume simulation timeline |
| `ur5e_dt__stop_scene` | Stop simulation timeline |
| `ur5e_dt__setup_camera_tf_graph` | Import RealSense, attach to wrist, build camera ROS graph |
| `ur5e_dt__assemble_objects` | Assemble objects from an assembly definition |
| `ur5e_dt__randomize_object_poses` | Randomize all non-board objects |
| `ur5e_dt__randomize_single_object` | Randomize one object in a clear strip |
| `ur5e_dt__save_scene_state` | Save object poses to JSON |
| `ur5e_dt__restore_scene_state` | Restore object poses from JSON |
| `ur5e_dt__add_objects` | Load objects from an assembly folder |
| `ur5e_dt__delete_objects` | Delete `/World/Objects` and pose-publisher graph |
| `ur5e_dt__setup_pose_publisher` | Publish object poses to ROS2 `objects_poses_sim` |
| `ur5e_dt__start_ros_driver` | Bring ROS2 robot driver up/down/status |
| `ur5e_dt__sync_real_poses` | Sync sim poses from real-world detections |
| `ur5e_dt__new_stage` | Clear stage and free memory |
| `ur5e_dt__quick_start` | Full scene setup (table, UR5e, RG2, camera, play) |
| `ur5e_dt__start_recording` | Start viewport MP4 recording |
| `ur5e_dt__stop_recording` | Finalize MP4 recording |
| `ur5e_dt__get_recording_status` | Recording status |
| `ur5e_dt__load_workstation` | Add Vention bench (visual) |
| `ur5e_dt__quick_start_with_workstation` | `quick_start` + Vention bench |
| `ur5e_dt__load_simple_room` | Add Simple Room backdrop |
| `ur5e_dt__quick_start_with_workstation_and_simple_room` | `quick_start` + bench + room |
| `ur5e_dt__set_object_pose` | Teleport an object to an exact world pose |

---

## Tool details

### `ur5e_dt__play_scene`

- Bridge: `ur5e_dt.play_scene`
- Parameters: none.
- Async: yes.
- Purpose: start/resume the simulation timeline.
- Side effects: sets timeline playing and waits for physics warm-up. Failure to
  complete warm-up, a changed stage or a Play request that is not retained returns
  an error; initialization or playback can already have partial effects.
- For saved twin graphs, restores the process-local gripper and force callbacks
  without recreating the USD graphs. Newton's first initialization requires a
  stopped meter/Z-up stage, enables the installed tensor provider and uses the
  twin's CPU policy. An active model or existing World on another device is refused.
  Native initial transform normalization is scoped to the session layer, with the
  caller's edit target restored unless an external editor changed it. This does
  not promise byte-identical in-memory root layers: native viewport/render
  metadata can change during opening or playback. Source files are not saved by
  this operation. PhysX's normal USD writeback can also update in-memory body poses,
  velocities and joint state during playback; the scoped Newton initialization
  policy is not a claim of PhysX root-layer byte preservation. A legacy
  Newton force graph without a validity gate is refused; use a new disposable
  stage and Quick Start to build the current graph.
- `force_publisher` reports the runtime wrench capability/readiness when a twin
  graph is present. Simulation Play alone does not prove wrench delivery; inspect
  the reason and actual ROS output. Generic Play has its own physics/edit-target
  contract and does not restore these twin-specific Python callbacks.
- Example: `{"tool": "ur5e_dt__play_scene", "arguments": {}}`

### `ur5e_dt__stop_scene`

- Bridge: `ur5e_dt.stop_scene`
- Parameters: none.
- Purpose: stop the simulation timeline.
- Side effects: timeline stopped; physics paused.
- Example: `{}`

### `ur5e_dt__setup_camera_tf_graph`

- Bridge: `ur5e_dt.setup_camera_tf_graph`
- Parameters: none.
- Async: yes.
- Purpose: import the Intel RealSense camera, attach it to `UR5e/wrist_3_link`, and build its ROS 2 action graph (camera image, `camera_info`, and the `/tf_cam` transform tree).
- Camera resolution defaults to 1280 × 720. Set `UR5E_DT_CAMERA_CONFIG` to a YAML file containing
  `camera_width` and `camera_height` to override it; the former project-specific location is no longer read implicitly.
- Preconditions: UR5e must be loaded (e.g. after `quick_start_with_workstation`); otherwise returns an error.
- Side effects: creates `/World/rsd455` (if absent) and `/Graph/ActionGraph_Camera`.
- Example: `{}`

### `ur5e_dt__assemble_objects`

- Bridge: `ur5e_dt.assemble_objects`
- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `assembly` | string | no | `"fmb1"` | MCP schema enum: `fmb1`, `fmb2`, `fmb3`. |
- Purpose: assemble objects into a configuration from an assembly JSON, positioning components relative to the current board pose.
- Side effects: mutates object poses in `/World/Objects`; requires objects already added.
- Example: `{"assembly": "fmb1"}`

### `ur5e_dt__randomize_object_poses`

- Bridge: `ur5e_dt.randomize_object_poses`
- Parameters: none.
- Purpose: randomize poses of all non-board objects in `/World/Objects`; boards are skipped.
- Side effects: overwrites object poses; reports randomized and skipped counts in its message.
- Known default randomization limit: sampler uses a default minimum separation of `0.18 m`, reducing the attempted separation while it remains at least `0.08 m` if placement fails; workspace bounds default to x `[-0.2, 0.3]`, y `[-0.5, -0.3]`. These values are fixed (not exposed via MCP).
- Example: `{}`

### `ur5e_dt__randomize_single_object`

- Bridge: `ur5e_dt.randomize_single_object`
- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `object_name` | string | yes (handler) | — | Object name under `/World/Objects` (e.g. `inverted_u_yellow`). |
- Purpose: randomize one named object in a clear workspace strip while keeping all other objects fixed.
- Side effects: overwrites that object's pose.
- Known default randomization limit: fixed strip x `[-0.35, -0.31]` (≈4 cm), y `[-0.5, -0.3]` (≈20 cm), minimum separation `0.18 m`, up to 1000 attempts. Not configurable via MCP.
- Example: `{"object_name": "inverted_u_yellow"}`

### `ur5e_dt__save_scene_state`

- Bridge: `ur5e_dt.save_scene_state`
- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `json_file_path` | string | no | timestamped `scene_state_YYYYMMDD_HHMMSS.json` | Filename only; stored under the scene-states directory. `.json` appended if missing. |
- Purpose: save current object poses under `/World/Objects` to JSON.
- Side effects: writes a file under `<output>/resources/scene_states/` (or `resources/scene_states/`). Returns `saved_count`, `failed_names`, `json_file_path`.
- Example: `{"json_file_path": "assembled.json"}`

### `ur5e_dt__restore_scene_state`

- Bridge: `ur5e_dt.restore_scene_state`
- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `json_file_path` | string | no | most recent saved file | Filename under the scene-states directory. |
- Purpose: restore object poses from a previously saved JSON file.
- Side effects: overwrites object poses; returns `restored_count`, `failed_names`.
- Example: `{"json_file_path": "assembled.json"}`

### `ur5e_dt__add_objects`

- Bridge: `ur5e_dt.add_objects`
- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `assembly` | string | no | `"fmb1"` | MCP schema enum: `fmb1`, `fmb2`, `fmb3`. |
- Purpose: add objects to the scene from a predefined assembly folder.
- Side effects: loads USD references, rebuilds PhysX views (stops then plays the timeline), waits for collider cooking (up to ~30 s). Returns `folder_path`.
- Example: `{"assembly": "fmb1"}`

### `ur5e_dt__delete_objects`

- Bridge: `ur5e_dt.delete_objects`
- Parameters: none.
- Purpose: delete all objects from `/World/Objects` and the associated pose-publisher action graph.
- Side effects: removes prims/graph; objects list becomes empty.
- Example: `{}`

### `ur5e_dt__setup_pose_publisher`

- Bridge: `ur5e_dt.setup_pose_publisher`
- Parameters: none.
- Purpose: create an action graph publishing object poses to ROS 2 topic `objects_poses_sim`.
- Side effects: creates `/Graph/ActionGraph_objects_poses`.
- Example: `{}`

### `ur5e_dt__start_ros_driver`

- Bridge: `ur5e_dt.start_ros_driver`
- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `mode` | string | no | `"sim"` | Enum: `sim`, `real`. `real` requires `backend="ursim"` and lab UR5e (not yet enabled). |
  | `action` | string | no | `"up"` | Enum: `up`, `down`, `status`. |
  | `backend` | string | no | `"fake"` | Enum: `fake`, `ursim`. `fake` is sim-only (apt `ur_robot_driver` with fake hardware, no Docker/`~/ros2_ws`). `ursim` needs Docker + `~/ros2_ws` and supports `real`. |
- Purpose: bring the ROS 2 robot driver up/down/status so motion tools (ros-mcp-server `move_home`, `move_to_grasp`, …) have a driver.
- Side effects: spawns a host subprocess (`scripts/launch_ur_fake.sh` or `scripts/sim_bringup.sh`) with a sanitized environment; no USD changes. Subprocess cap 240 s for `up`, 60 s otherwise. Both backends end at `/joint_states` live and `scaled_joint_trajectory_controller` accepting `FollowJointTrajectory` on the configured ROS domain (default 0, Cyclone, localhost-only).
- Real-hardware/ROS constraints: `backend="fake"` is simulation-only; `mode="real"` is not yet enabled in this codebase. Do not launch a real robot driver for a simulation-only task.
- The fake backend discovers the standard ROS setup for Ubuntu 22.04/Humble or Ubuntu 24.04/Jazzy. On Jazzy, install `ros-jazzy-ur-robot-driver`, `ros-jazzy-ros2controlcli`, and `ros-jazzy-rmw-cyclonedds-cpp` through the system package manager. The launcher checks the driver's advertised mock-hardware argument and refuses to start if it cannot verify it; use `ROS_SETUP` for a nonstandard sourced ROS installation.
- Fake-driver startup waits until both the joint-state broadcaster and scaled trajectory controller are active; controller queries are bounded when the manager is unavailable.
- Fake-driver shutdown targets the verified mock process recorded by this launcher and its dedicated process group; it does not globally stop other UR drivers. If its PID file is missing or stale, inspect the host's processes instead of assuming an unrelated driver was stopped.
- Managed `sim__launch` sessions use separate fake-driver records and logs, with an ownership marker checked before
  shutdown. The launcher still refuses startup if any UR driver is detected on the host, even in another ROS domain.
  Stop the fake driver before stopping its simulator; `sim__stop` does not stop the driver's separate process group.
- `backend="ursim"` is unavailable in managed sessions because its process and container operations are global.
  It remains available to unmanaged sessions with the prerequisites above.
- Example: `{"action": "status"}` / `{"mode": "sim", "action": "up", "backend": "fake"}`

### `ur5e_dt__sync_real_poses`

- Bridge: `ur5e_dt.sync_real_poses`
- Parameters: none.
- Purpose: subscribe to `/objects_poses_real` and update sim object poses to match real-world detected poses.
- Side effects: creates a temporary rclpy node, waits up to 3 s for one `TFMessage`; mutates object poses. Requires an external publisher on `/objects_poses_real` (real detection pipeline).
- Result: `updated` and `snapped` list object names. Returns an error when no message arrives or no scene objects match; it does not report a successful sync in those cases. Its temporary ROS context does not shut down the simulator's existing ROS context.
- Real-hardware/ROS constraints: intended for real-world sync; not useful in a pure sim session without that publisher.
- Example: `{}`

### `ur5e_dt__new_stage`

- Bridge: `ur5e_dt.new_stage`
- Parameters: none.
- Async: yes.
- Purpose: clear the entire USD stage and free accumulated memory (tears down prims, Hydra, PhysX).
- Side effects: stage becomes empty; viewport publishers stopped; physics callbacks stopped; World singleton cleared. Rebuild with `quick_start*`.
- Example: `{}`

### `ur5e_dt__quick_start`

- Bridge: `ur5e_dt.quick_start`
- Parameters: none.
- Async: yes.
- Purpose: one-click setup: load table scene, import UR5e with action graphs, attach RG2 gripper with action graphs, create workspace camera, start simulation.
- Side effects: full scene creation and timeline play.
- Example: `{}`

### `ur5e_dt__start_recording`

- Bridge: `ur5e_dt.start_recording`
- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `file_name` | string | no | `isaacsim_<timestamp>.mp4` | Output filename; `.mp4` appended if missing. Saved to configured videos dir. |
  | `camera` | string | no | active viewport camera | Camera prim path (e.g. `/World/workspace_camera_sim`). |
  | `fps` | integer | no | `30` | Output framerate; values `<= 0` become `30`. |
- Purpose: start real-time viewport video recording via ffmpeg without blocking physics.
- Side effects: spawns ffmpeg; non-blocking. Requires ffmpeg and a usable viewport.
- Frames are paced to the requested output rate against elapsed time; short gaps repeat the latest frame. If capture
  falls more than five seconds behind or no frame arrives, recording reports an error instead of claiming success.
- Complete physics warm-up before recording. A measured cold Newton initialization
  exceeded the five-second capture-lag limit; restarting recording after warm-up
  succeeded. A camera switch can retain an initial frame from the previous view;
  inspect the output before using it as evidence of a particular camera.
- Example: `{"file_name": "run.mp4", "fps": 30}`

### `ur5e_dt__stop_recording`

- Bridge: `ur5e_dt.stop_recording`
- Parameters: none.
- Purpose: stop the current recording and finalize the MP4.
- Side effects: finalizes file. Returns `file_path`, `frame_count`.
- Example: `{}`

### `ur5e_dt__get_recording_status`

- Bridge: `ur5e_dt.get_recording_status`
- Parameters: none.
- Purpose: check whether recording is active; returns status, frame count, elapsed time, output path, FPS.
- Example: `{}`

### `ur5e_dt__load_workstation`

When playing, workstation composition temporarily pauses physics and drains ordinary
render updates before restoring playback. This avoids the observed 6.0.0.1 Motion BVH
crash when an immediate Stop followed a live bench import. A paused/stopped timeline
stays in that state; a replacement stage or concurrent Stop is never resumed.

- Bridge: `ur5e_dt.load_workstation`
- Parameters: none.
- Async: yes.
- Purpose: load the Vention workstation bench as visual context under the robot (tabletop top at z=0). Additive; cleared by `new_stage`.
- Side effects: references `workstation/vention.usd` at `/World/Vention`; hides the default ground-plane visual (collider retained). Visual-only, no colliders; does not move the robot.
- Example: `{}`

### `ur5e_dt__quick_start_with_workstation`

- Bridge: `ur5e_dt.quick_start_with_workstation`
- Parameters: none.
- Async: yes.
- Purpose: `quick_start`, then load the Vention workstation bench.
- Example: `{}`

### `ur5e_dt__load_simple_room`

- Bridge: `ur5e_dt.load_simple_room`
- Parameters: none.
- Async: yes.
- Purpose: load Isaac's standard Simple Room environment (floor, walls, window, lamp) as a backdrop at z=-0.11. Additive; cleared by `new_stage`.
- Side effects: references the Simple Room asset at `/World/SimpleRoom`; hides the default ground-plane visual (collider retained).
- Example: `{}`

### `ur5e_dt__quick_start_with_workstation_and_simple_room`

- Bridge: `ur5e_dt.quick_start_with_workstation_and_simple_room`
- Parameters: none.
- Async: yes.
- Purpose: `quick_start`, then load the Vention bench and the Simple Room backdrop.
- Example: `{}`

### `ur5e_dt__set_object_pose`

- Bridge: `ur5e_dt.set_object_pose`
- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `object_name` | string | yes (handler) | — | Object name under `/World/Objects` (e.g. `u_brown`). |
  | `position` | array[3] | yes (handler) | — | `[x, y, z]` world position in meters. |
  | `orientation` | array[4] | no | — | `[w, x, y, z]` quaternion; overrides `yaw_deg`. |
  | `yaw_deg` | number | no | `0` | Yaw about world Z in degrees (used when `orientation` omitted). |
- Purpose: teleport a scene object to an exact world pose, persisting under physics (same xform path as randomize).
- Side effects: mutates object pose. Pair with `setup_pose_publisher` so `/objects_poses_sim` reflects the new pose.
- Example: `{"object_name": "u_brown", "position": [0.3, 0.2, 0.05], "yaw_deg": 90}`

---

## Typical safe simulation workflow

1. **Connect**: call `refresh_tools`, discover bridges and select the intended endpoint
   with `use_bridge`; verify its PID and port with `status` before changing a scene.
   `8766` is only the default. If discovery tools are filtered, use
   `python -m isaac_mcp.client --list-bridges`, then probe the explicit host and port
   as described in [the agent guide](../AGENTS.md#getting-the-full-surface).
2. **Fresh stage (optional)**: `ur5e_dt__new_stage` to free memory, then rebuild.
3. **Build scene**: `ur5e_dt__quick_start_with_workstation` (or `quick_start` / `quick_start_with_workstation_and_simple_room`).
4. **Camera ROS (optional)**: `ur5e_dt__setup_camera_tf_graph` after UR5e is loaded.
5. **Objects**: `ur5e_dt__add_objects` with `{"assembly": "fmb1"}`; then `ur5e_dt__setup_pose_publisher` if ROS consumers need poses.
6. **Checkpoint**: `ur5e_dt__save_scene_state` before randomization.
7. **Randomize**: `ur5e_dt__randomize_object_poses` or `ur5e_dt__randomize_single_object`.
8. **Controlled edits**: `ur5e_dt__stop_scene`, `ur5e_dt__set_object_pose`, then `ur5e_dt__play_scene`.
9. **Recording**: `ur5e_dt__start_recording` … `ur5e_dt__stop_recording`.
10. **ROS driver (sim only)**: `ur5e_dt__start_ros_driver` with `{"action": "status"}` first, then `{"mode": "sim", "action": "up", "backend": "fake"}`. Do not use `mode="real"` (not enabled).
11. **Teardown**: `ur5e_dt__delete_objects`, then `ur5e_dt__new_stage` if rebuilding.

## Compatibility notes

- Validated by `scripts/smoke_tools.py` against Isaac Sim 6.2.0.0 (it checks `app_version` starts with `6.2` and 23 registered twin tools).
- Real-hardware/real mode is **not** enabled in this codebase; `start_ros_driver` `mode="real"` requires `backend="ursim"` and a lab UR5e that is not yet supported.
- Generic bridge compatibility with Isaac Sim 5.1 was reported (see [MCP-SERVER.md](MCP-SERVER.md)), but that is a bridge-level claim, not a claim about these 23 twin tools.
- Default randomization limits are fixed and not exposed via MCP (see `randomize_object_poses` / `randomize_single_object`).

These tools require a running compatible Kit process and, for the twin, the simulator resources described in the README. The examples do not start training.

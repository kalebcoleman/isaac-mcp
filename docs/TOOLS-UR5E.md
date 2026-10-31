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
- Side effects: sets timeline playing; waits for PhysX warm-up (up to ~15 s at 60 Hz) before returning so cooking completes.
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
- Example: `{"action": "status"}` / `{"mode": "sim", "action": "up", "backend": "fake"}`

### `ur5e_dt__sync_real_poses`

- Bridge: `ur5e_dt.sync_real_poses`
- Parameters: none.
- Purpose: subscribe to `/objects_poses_real` and update sim object poses to match real-world detected poses.
- Side effects: creates a temporary rclpy node, waits up to 3 s for one `TFMessage`; mutates object poses. Requires an external publisher on `/objects_poses_real` (real detection pipeline).
- If no message arrives, the underlying sync logs a timeout and returns; the wrapper can still report success. Verify
  the publisher and the resulting poses rather than treating that success response as proof an update arrived.
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

1. **Connect**: ensure the twin bridge is up on `8766`; call `status` and refresh the MCP client tool list; the bridge CLI also supports `list_tools`.
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

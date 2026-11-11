# UR5e Digital Twin MCP Tools (`ur5e_dt__*`)

**These tools are RISE lab's UR5e digital twin: Kaleb's work with that lab and that robot.** Every other tool in this project is
robot-agnostic. For a generic start with any robot, including a UR5e without the twin, use
[`sim__quick_start`](TOOLS-SCENE.md#start-a-scene).

This document covers the 23 tools registered by the twin extension (`exts/ur5e-dt/ur5e_dt/`: the registry is in `extension.py`, the handlers in `commands.py`)
on the shared Kit bridge. It is the implementation reference. Generic tools are in
[TOOLS-SCENE.md](TOOLS-SCENE.md), and every argument is listed in [TOOLS-ARGUMENTS.md](TOOLS-ARGUMENTS.md#ur5e-twin-ur5e_dt__).

## Naming: bridge vs. MCP

- Inside Kit the tools are registered under the canonical namespace with **dots**:
  `ur5e_dt.<tool_name>` (e.g. `ur5e_dt.play_scene`).
- The host server (`mcp/isaac_mcp/server.py`) exposes namespaced tools with **double underscores**: `ur5e_dt__<tool_name>`
  (e.g. `ur5e_dt__play_scene`). Dispatch maps `__` back to `.`.
- Bridge built-ins (`status`, `run_python`, `list_bridges`) and host gateway tools
  (`use_bridge`, `refresh_tools`) and desktop-capture tools
  (`screenshot`, `start_screen_recording`, …) carry **no** `ur5e_dt__` prefix.

Most of these tools serve one lab's digital twin. `ur5e_dt__quick_start` works for anyone from public files, while the gripper, workstation and
object tools need [your own lab files](ASSETS.md#public-files-and-lab-files), which cannot be hosted. `ur5e_dt__quick_start` builds no table; the bench comes from `ur5e_dt__load_workstation`. At its zero pose the arm reaches out low, with the wrist about 6 cm above the ground, which can look collapsed but is not a fault: check `sim__joints__state` and command a pose. `ur5e_dt__quick_start` refuses if this stage already publishes joint states on `/joint_states` (`sim__ros2__tf_setup`'s default topic), because the twin's graph drives the arm from that topic and the arm would oscillate; the error gives the fix. Quick Start leaves the timeline playing. Call `sim__timeline__control` with `action="stop"` before generic scene edits.

The twin bridge listens on port `8766` by default (`UR5E_DT_PORT` /
`ISAAC_SIM_PORT` override). See [how the server reaches Isaac Sim](ARCHITECTURE.md#how-the-server-reaches-isaac-sim) and
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
available, is focused once docked (so it does not stay behind another tab), and can be moved like other
Kit tabs. If it is hidden behind a tab anyway, the generic `sim__ui__act action=tab window="UR5e Digital Twin"`
shows and focuses it. `./run.sh --headless` has no visible simulator window.
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
| `ur5e_dt__add_objects` | Load objects from an assembly folder; your lab files |
| `ur5e_dt__delete_objects` | Delete `/World/Objects` and pose-publisher graph |
| `ur5e_dt__setup_pose_publisher` | Publish object poses to ROS2 `objects_poses_sim`: one `tf2_msgs/msg/TFMessage` holding them all, parent frame `World`, each child frame the object's name |
| `ur5e_dt__start_ros_driver` | Bring ROS2 robot driver up/down/status |
| `ur5e_dt__sync_real_poses` | Sync sim poses from real-world detections |
| `ur5e_dt__new_stage` | Clear stage and free memory |
| `ur5e_dt__quick_start` | Scene from public files (UR5e, camera, play); adds the RG2 when your files are present |
| `ur5e_dt__start_recording` | Start viewport MP4 recording |
| `ur5e_dt__stop_recording` | Finalize MP4 recording |
| `ur5e_dt__get_recording_status` | Recording status |
| `ur5e_dt__load_workstation` | Add Vention bench (visual); your lab file, else a placeholder |
| `ur5e_dt__quick_start_with_workstation` | `quick_start` + Vention bench |
| `ur5e_dt__load_simple_room` | Add Simple Room backdrop |
| `ur5e_dt__quick_start_with_workstation_and_simple_room` | `quick_start` + bench + room |
| `ur5e_dt__set_object_pose` | Request an exact world pose for an object; physics settles it from there |

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
  `camera_width` and `camera_height` to override it; no other file is read.
- Preconditions: UR5e must be loaded (e.g. after `quick_start_with_workstation`); otherwise returns an error.
- Side effects: creates `/World/rsd455` (if absent) and `/Graph/ActionGraph_Camera`.
- RGB and CameraInfo use the `camera_link` optical frame. The native TF publisher converts
  USD camera axes to ROS optical axes; the camera's `isaac:nameOverride` makes its `/tf_cam`
  child frame match those image headers. Subscribe/remap `/tf_cam` when using TF consumers.
- Image, CameraInfo and TF timestamps use monotonic simulation time across Stop/Play.
  The ROS camera helpers regenerate their native session render pipeline on playback;
  saved output retains the authored Action Graph and camera definition.
- Example: `{}`

### `ur5e_dt__assemble_objects`

- Bridge: `ur5e_dt.assemble_objects`
- Parameters:

  | Name | Type | Required | Default | Notes |
  |---|---|---|---|---|
  | `assembly` | string | no | `"fmb1"` | MCP schema enum: `fmb1`, `fmb2`, `fmb3`, `fmb4`, `jenga`, `fmb_jenga`; the last two alias `fmb4` (six Jenga blocks). |
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
  | `assembly` | string | no | `"fmb1"` | MCP schema enum: `fmb1`, `fmb2`, `fmb3`, `fmb4`, `jenga`, `fmb_jenga`; the last two alias `fmb4` (six Jenga blocks). |
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
- Side effects: spawns a host subprocess (`scripts/launch_ur_fake.sh` or `scripts/sim_bringup.sh`) with a sanitized environment; no USD changes. Subprocess cap 240 s for `up`, 60 s otherwise. A non-zero exit is reported as an error whose message includes the last line of the script's output; for `backend="ursim"` `action="status"` that is why Docker is missing or unreachable, not a success with nothing running. The fake driver's status prints `(1 msg)` only when it actually read `/joint_states`, so it no longer claims one message and none in the same report. Both backends end at `/joint_states` live and `scaled_joint_trajectory_controller` accepting `FollowJointTrajectory` on the configured ROS domain (default 0, Cyclone, localhost-only).
- Real-hardware/ROS constraints: `backend="fake"` is simulation-only; `mode="real"` is not yet enabled in this codebase. Do not launch a real robot driver for a simulation-only task.
- The fake backend discovers the standard ROS setup for Ubuntu 22.04/Humble or Ubuntu 24.04/Jazzy. On Jazzy, install `ros-jazzy-ur-robot-driver`, `ros-jazzy-ros2controlcli`, and `ros-jazzy-rmw-cyclonedds-cpp` through the system package manager. The launcher checks the driver's advertised mock-hardware argument and refuses to start if it cannot verify it; use `ROS_SETUP` for a nonstandard sourced ROS installation or a RoboStack prefix, in which case the distribution comes from what that file sets. An environment that is already activated (`ROS_DISTRO` set, `ros2` on `PATH`, no `/opt/ros/<distro>`) needs no `ROS_SETUP`.
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
- Result: `updated` and `snapped` list object names, and the message says the poses were requested (physics settles them; read them again to see the result). Returns an error when no message arrives or no scene objects match; it does not report a successful sync in those cases. Its temporary ROS context does not shut down the simulator's existing ROS context.
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
- Result: `gripper` says whether the RG2 loaded and what is missing; `assets` lists each asset's `source` (`project`, `store`, `remote`, `missing`) and `location`.
- Purpose: one-click setup: load scene, import UR5e with action graphs, attach RG2 gripper with action graphs when its files exist, create workspace camera, start simulation.
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
- Purpose: load the Vention workstation bench as visual context under the robot (tabletop top at z=0). Additive; cleared by `new_stage`. Uses an operator-supplied or cached `workstation/vention.usd`; builds a simple placeholder bench when it is not installed.
- Side effects: references the bench at `/World/Vention`; hides the default ground-plane visual (collider retained). Visual-only, no colliders; does not move the robot.
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
- Purpose: request an exact world pose for a scene object, persisting under physics (same xform path as randomize). The result says `requested`: physics settles the object from there, so read its pose again to see where it ended up.
- Side effects: mutates object pose. Pair with `setup_pose_publisher` so `/objects_poses_sim` reflects the new pose.
- Example: `{"object_name": "u_brown", "position": [0.3, 0.2, 0.05], "yaw_deg": 90}`

---

## Which colliders the workstation has

Answered from the extension's code (`exts/ur5e-dt/ur5e_dt/`) only; where the code does not say, this page says unknown.

| Part | Colliders | Source |
|---|---|---|
| Default ground plane (`/World/defaultGroundPlane`) | Yes, at z=0. `load_workstation` and `load_simple_room` hide only its visual and keep the collision. | Comments and code in `_load_workstation` and `_load_simple_room` |
| Vention bench (`/World/Vention`, from `workstation/vention.usd`, or the placeholder bench) | Documented as "no colliders" (visual-only). The code does not strip colliders from the bench itself, so this holds only if the operator-supplied USD has none: **unknown** for your file. The placeholder bench's colliders: unknown. | `_load_workstation` docstring; `_make_visual_only` is not called on the bench |
| Simple Room (`/World/SimpleRoom`) | None. The asset ships 59 colliders; `_make_visual_only` disables every `CollisionAPI` collision and rigid body in the subtree, then the timeline is stopped and played to rebuild PhysX. The floor collider for objects is the ground plane at z=0. | `_load_simple_room`, `_make_visual_only` |
| UR5e (`robot/ur5e.usd`) and the RG2 gripper | The code reads finger-pad collision meshes under `<link>/collisions` for the gripper gap measurement, and binds a physics material to the fingertip links and any collider children. Which other links have colliders, and their approximations: **unknown** (they come from the USD files, not from this code). | `robot/ur5e.usd` and `gripper` assets; gap measurement and material binding in `extension.py` |
| Objects from `add_objects` (boards, blocks, sockets, pegs) | The baked USD collider is kept. Only if a prim has no authored `physics:approximation` does the extension author a default: `sdf` for all four categories (`sdfResolution` 256), with `convexDecomposition` as the only alternative the code names. Rest and contact offsets are not re-authored by that fallback (the code says they come from the USD or PhysX defaults), so their effective values are **unknown** here. | `extension.py` collision settings and the object restore path |
| Quick Changer (`/World/QuickChanger`) | **Unknown**: referenced from `gripper/QuickChanger.usd`; the code authors nothing about its colliders. | `robot.py` |

Inspect the live scene instead of trusting this table: `sim__physics__inspect` and `sim__prim__get` report whether a
prim has a collider, and `sim__physics__contacts` reports what touches what once the timeline plays.

## Typical safe simulation workflow

1. **Connect**: call `refresh_tools`, discover bridges and select the intended endpoint
   with `use_bridge`; verify its PID and port with `status` before changing a scene.
   `8766` is only the default. If discovery tools are filtered, use
   `python3 mcp/isaac_mcp/client.py --list-bridges`, then probe the explicit host and port
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

Repeated Quick Start reuses matching fixed gripper/force graphs, including graphs composed
from another USD layer or a reopened stage. Conflicting graph contracts are refused and
preserved rather than removed from source layers.

## Arm drive gains

Every arm joint is a position-driven spring: `J*q'' + kd*q' + kp*q = kp*q_cmd`. The extension writes
`kp = _ur5e_stiffness` and `kd = _ur5e_damping` (`exts/ur5e-dt/ur5e_dt/robot.py`, `load_ur5e()`), in
**USD angular-drive units: N·m per degree and N·m·s per degree**. PhysX and the Python API work per radian,
so the solver sees 57.2958x larger numbers; `_verify_drive_gains()` reads `get_gains()` back and prints a
warning if the two ever disagree.

Two traps, both hit on 2026-10-30:

1. **A same-value write never reaches PhysX.** Writing `100` into an attribute that already holds `100`
   notifies nobody, so the articulation kept the asset's authored ~10230/4 per degree and the arm ran ~100x
   stiffer than configured, silently. The extension now applies the configured values with `set_gains()`
   and verifies them.
2. **Stiffness alone is not the knob.** What governs the response is `tau = kd/kp` (the position loop's time
   constant) and `zeta = kd / (2*sqrt(kp*J))` (whether it rings). Raising stiffness without raising damping
   lowers `zeta`: the asset gains ran at `zeta ~ 0.15`, a ~120 Hz resonance sampled at 60 Hz.

**Shipped default: 100/10 per degree (`tau = 0.10 s`), chosen 2026-10-30.** Measured on the UR5e twin
(elbow_joint, 0.3 rad step, 100 Hz `/joint_states`):

| stiffness/damping (per deg) | tau | rise (10-90 %) | settle (2 %) | overshoot | steady-state error |
|---|---|---|---|---|---|
| 50/5 | 0.10 s | 0.213 s | 0.505 s | 0.0 % | 1.9e-6 rad |
| 100/10 | 0.10 s | 0.216 s | 0.524 s | 0.0 % | 2.1e-6 rad |
| 100/20 | 0.20 s | 0.438 s | 0.918 s | 0.0 % | 5.1e-6 rad |
| 200/10 | 0.05 s | 0.120 s | 0.347 s | 0.0 % | 9.1e-7 rad |
| 400/10 | 0.025 s | 0.100 s | 0.289 s | 0.0 % | 4.2e-7 rad |

`tau` alone predicts the shape: the two pairs with `tau = 0.10 s` (50/5 and 100/10) settle within 4 % of
each other. 100/10 is heavily overdamped, so nothing rings at the 60 Hz sample rate and bench contact behaves
like a 5.7 kN·m/rad spring.

**To trade contact softness for mirroring fidelity**, keep `tau = damping/stiffness` in 0.02-0.05 s:
- 200/10 per degree (`tau` 50 ms) - settle 0.347 s, measured lag at 0.5 Hz ~34 deg (vs 41 deg at 100/10)
- 400/10 per degree (`tau` 25 ms) - settle 0.289 s, measured lag at 0.5 Hz ~30 deg

Both are measured, both stay overdamped. Costs: contact forces scale with stiffness (a bench touch is 2-4x
harder), and the `max_force = 200 N·m` cap starts to matter for fast moves. Raise stiffness *with* the
damping tail, never alone. After changes, re-check the motion and drive baselines in [VERIFICATION.md](VERIFICATION.md).

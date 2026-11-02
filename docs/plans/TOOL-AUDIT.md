# Tool audit: static review and smoke test

Scope: every tool the host registers with `ISAAC_MCP_LOCAL_TOOLS=all,screen_input,sim_launch` (92 tools, plus
the 7 bridge built-ins once a bridge is discovered). Evidence uses `<repo>` and `<home>` placeholders.
Driver: `scripts/smoke_mcp_surface.py` (in-process host server, the same dispatch an MCP client uses).

Environment: Isaac Sim 6.2.0.0 headless, PhysX, port 8780, ROS domain 11, host Ubuntu 22.04, RTX PRO 6000
Blackwell. Not verified: Newton, Isaac Lab jobs (no install on this host), desktop/screen tools (no X display),
`window_*` input, `sim__launch/stop` (the sim was started with `run.sh`), real ROS driver/hardware.

## Static findings

Severity: **bug** = wrong behaviour, **confusing** = misleading to an agent, **nit** = cosmetic.

| # | Sev | Tool(s) | Finding | Proposed fix | Status |
|---|---|---|---|---|---|
| 1 | confusing | `sim__light__modify` | Description says "intensity/color" but both are required; a caller cannot change one alone. | Say both are required (or make them optional: behaviour change). | Description fixed |
| 2 | confusing | `sim__stage__save` | Description says "new basename"; only `.usda` is accepted (`INVALID_OUTPUT`). | Mention `.usda`. | Description fixed |
| 3 | confusing | `sim__camera__create` | `target` needs `frame=world`, but `frame` defaults to `local`; the default call with `target` always fails. | Default to `world` when `target` is given, or document it. | Open (behaviour) |
| 4 | confusing | `sim__prim__create` | `INVALID_PARENT` ("Parent must exist...") does not name the missing parent; an empty stage needs `/World` created first. `CONFLICT` "A PhysicsScene already exists" does not give its path (`/PhysicsScene`), which `sim__physics__configure` then needs. | Include the path in both messages. | Open |
| 5 | confusing | `sim__joints__*` | The articulation root on the UR5e is `<robot>/root_joint`, not the robot prim; `PHYSICS_NOT_READY` ("Select an initialized articulation root") does not say how to find it. `sim__physics__inspect` shows `articulation_root` only on that prim. | Point to `sim__physics__inspect` in the error text. | Open |
| 6 | confusing | `ur5e_dt__play_scene` vs `sim__timeline__control` | After `ur5e_dt__play_scene`, `sim__simulation__step` fails `EDIT_TARGET_CHANGED`; the message is correct but mixing the two timeline controls is a trap. | Cross-reference in both descriptions. | Open |
| 7 | confusing | `ur5e_dt__*` vs `sim__*` | Error shape differs. `sim__*`/`assets__*` raise tool errors (`isError`), `ur5e_dt__*` return `{"status":"error"}` as a normal result. Offline, `ur5e_dt__*` and `refresh_tools` return a text beginning `Error:` as a normal result too. | Set `isError` for catalog failures. | Open (behaviour) |
| 8 | confusing | 23 `ur5e_dt__*` | No MCP annotations at all (defaults mean "destructive, not read-only"). `get_recording_status` is a pure read. | Add annotations to the twin registry (needs a catalog regeneration). | Open |
| 9 | confusing | `sim__list`, `sim__status` | Annotated as `PYTHON_EXECUTION` (destructive, open-world) although `sim__list` only reads session records (`sim__status` also probes the bridge; not verified for side effects). | Use a read-only annotation if `readonly` group semantics allow. | Open |
| 10 | confusing | `isaaclab__runs_list`, `runs_status` | Annotated `DESTRUCTIVE` although they list/read. AGENTS.md says inspection tools can have side effects; not verified why. | Verify; downgrade if no writes. | Unverified |
| 11 | confusing | `assets__roots/scan/search` | `openWorldHint=true` though the result says `"network": false`. Filesystem access, not open world. | Set `openWorldHint=false`. | Open |
| 12 | confusing | `isaaclab__train/eval/play` | Two flags, `allow_concurrent` and `allow_concurrent_gpu`, with overlapping meaning. | Document which wins or merge. | Unverified |
| 13 | confusing | `refresh_tools` | With `assets__*` registered, prints "3 tool(s) are listed from the packaged catalog but have not been confirmed on the selected bridge" even though the live bridge reports the `assets` namespace. | Confirm `assets__*` against the live bridge. | Open |
| 14 | nit | `sim__stage__info`, `sim__logs__tail` | Return absolute `<home>` interpreter and Kit log paths. Expected for a local tool; note it if results are shared. | None. | Won't fix |
| 15 | nit | argument names | Same idea, different names: `path` (sim) vs `prim_path` (assets import); `root_id` (assets) vs `root` (prim subtree); `confirm` vs `confirm_discard`. | Document in TOOLS-SCENE. | Open |
| 16 | nit | `ur5e_dt__new_stage` | Message "Freed -24 MB" when memory grew. | Clamp or reword. | Open |
| 17 | nit | `ur5e_dt__save_scene_state`/`restore_scene_state` | An absolute `json_file_path` (`<tmp>/state.json`) is silently relocated to `<repo>/resources/scene_states/state.json`; only the response shows it. | Say so in the description. | Open |
| 18 | nit | `ur5e_dt__add_objects` | Assembly `jenga` loads folder `fmb4`; parameter has no documented default if omitted. | Document the alias. | Open |
| 19 | nit | `sim__*` generic tier | Vendor-neutral wording holds. Only `sim__launch` and `assets__prepare_workstation` mention the UR5e (both by design, they configure the twin). | None. | OK |
| 20 | info | `ur5e_dt__load_ur5e` asset | `robot/ur5e.usd` resolved to the NVIDIA remote fallback pinned to the **5.1** asset root, although the sim is 6.2 (the Simple Room came from the 6.2 root). See smoke notes. | Coordinator: decide whether the fallback root should follow the running release. | Open (resolver) |

Docs agreement: `python3 scripts/check_docs.py` passes (every registered tool is documented, counts 83 default / 92
with optional groups match). Annotations were compared with `docs/TOOLS-*.md` descriptions by sampling, not line by line.

## Offline smoke (no simulator)

| Tool | Result | Note |
|---|---|---|
| `refresh_tools` | returns text `Error: no bridge found...` | not an error result (#7) |
| `screen_info`, `list_windows` | error | no X display on this host (expected) |
| `screen_recording_status` | ok | "no screen recording is active" |
| `sim__list` | ok | lists a stale session record (process exited) |
| `isaaclab__install_info`, `envs_list` | error `Isaac Lab install not found` + hint | clear |
| `isaaclab__runs_list`, `checkpoints_list` | ok | existing history / empty |
| `assets__roots` | ok | no roots configured |
| `assets__scan` (unknown root) | error `ROOT_NOT_APPROVED` | clear |
| `assets__search` | ok | empty, `network: false` |
| `sim__stage__info`, `ur5e_dt__new_stage`, `ur5e_dt__get_recording_status` | error "not available: no Isaac Sim bridge" | actionable text |

## Live smoke (Sim 6.2.0.0, PhysX, headless, port 8780)

Times are wall-clock per call from the host; "retry" means the first call used wrong arguments (my error or the
findings above) and was repeated correctly.

| Tool | Result | Note |
|---|---|---|
| `status`, `list_bridges` | ok | PID/port/version match; 67 bridge tools |
| `sim__stage__info`, `stage__tree`, `prim__find`, `prim__get` | ok | |
| `sim__logs__tail`, `sim__physics__inspect` | ok | |
| `sim__prim__create` (Xform, Cube) | ok after retry | needs `/World` first (#4); second PhysicsScene refused (#4) |
| `sim__prim__set_transform`, `prim__clone`, `prim__delete` | ok | |
| `sim__physics__configure` | error, then not retried | wrong path: the scene is `/PhysicsScene` (#4) |
| `sim__material__create`/`apply`, `sim__light__create` | ok | |
| `sim__light__modify` | ok after retry | `color` required (#1) |
| `sim__camera__create` | ok after retry | `frame=world` needed with `target` (#3) |
| `sim__camera__capture` | ok, 0.14 s | 320x240 PNG written to the output root |
| `sim__viewport__frame` | ok | |
| `sim__timeline__control` play/pause/stop | ok | play 2.3 s the first time |
| `sim__simulation__step`, `sim__body__state` | ok | 5 steps, delta matched expectation |
| `sim__stage__save` | ok after retry | `.usda` only (#2) |
| `sim__stage__clear_owned` | ok | removed the smoke prims |
| `sim__lidar__create` / `lidar__read` | skipped / refused | needs `--motion-bvh` session (`SENSOR_SETUP_REQUIRED`) |
| `sim__controller__create` | refused `SCRIPT_DISABLED` | opt-in by design |
| `run_python`, `stat_file`, `reload_tools` | ok / ok / needs `module` | |
| `ur5e_dt__new_stage` | ok 0.45 s | |
| `ur5e_dt__load_simple_room` | ok 0.8 s | Simple Room from the NVIDIA 6.2 root |
| `ur5e_dt__quick_start` | ok 15.6 s | robot, gripper, camera, physics playing |
| `sim__joints__inspect`, `joints__drive` | ok | target 0.2 rad authored as 11.46 deg |
| `sim__joints__state`/`command` on robot prim | error `PHYSICS_NOT_READY` | the root is `/World/UR5e/root_joint` (#5) |
| `sim__joints__state`/`command` on root_joint | ok | 6 DOF; 30 steps reach the target |
| `sim__simulation__step` after `ur5e_dt__play_scene` | error `EDIT_TARGET_CHANGED` | use `sim__timeline__control` (#6) |
| `ur5e_dt__load_workstation` | ok 0.4 s | `/World/Vention` |
| `ur5e_dt__add_objects` (jenga) | ok 2.4 s | |
| `ur5e_dt__setup_pose_publisher`, `setup_camera_tf_graph` | ok | no ROS driver needed to build the graph |
| `ur5e_dt__randomize_object_poses` | ok 7.6 s | |
| `ur5e_dt__save_scene_state` / `restore_scene_state` | ok | path relocated (#17) |
| `ur5e_dt__randomize_single_object`, `set_object_pose` | error for a missing name | clear message |
| `ur5e_dt__assemble_objects`, `play_scene`, `stop_scene` | ok | |
| `ur5e_dt__start_recording`/`get_recording_status`/`stop_recording` | ok | headless: 1 frame recorded |
| `ur5e_dt__delete_objects` | ok | |
| `ur5e_dt__start_ros_driver` (status) | error | `/opt/ros/humble/setup.bash` missing; no system ROS. Skipped |
| `ur5e_dt__sync_real_poses` | error after 3.1 s | no real peer. Skipped |
| `assets__info`, `import_usd`, `import_path`, `import_urdf`, `prepare_workstation` | refused `NO_SHARED_STAGING` | no approved root or staging configured; refusal text is clear |

### Which `robot/ur5e.usd` was loaded

`/World/UR5e` references the NVIDIA **remote fallback**:
`https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1/Isaac/Robots/UniversalRobots/ur5e/ur5e.usd`
(plus its `configuration/ur5e_physics.usd` and `ur5e_sensor.usd` siblings). No local `robot/ur5e.usd` store was
configured. Two points for the resolver owner: the fallback root is the 5.1 release while the running sim is 6.2,
and the gripper and changer came from local `<repo>/exts/ur5e-dt/assets`.

### Teardown

The sim was started with `run.sh --headless --port 8780`. SIGTERM did not stop Kit within 40 s; it was killed with
SIGKILL, no Kit process or bridge advertisement remained, and GPU memory returned to the idle baseline.

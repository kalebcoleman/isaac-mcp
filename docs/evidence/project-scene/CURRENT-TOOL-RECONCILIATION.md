# Current tool, source and backend reconciliation

This is the final inventory/evidence map for review, **not completion of all requested runtime gates**. The real MCP inventory was captured at `fdb7a02da1be003e3d338f676ba8105720869ea3` (tree `77407d490684970161f223c28b2548f1d4289db2`). Subsequent `7bafbea` changes documentation only; the tested process is not relabeled.

**Supplement:** the later [exact `0163b3f` PhysX regression](PHYSX-CURRENT-POSITIVE.md)
closes the normal body/joint/step coverage gap in this snapshot, with 445 calls / 230
assertions across all five H/W pairs. Its counts are separate from the census below.
Per the user's 2026-11-02 direction, PhysX is the release target; Newton remains
experimental and needs no additional investigation for this release. Existing Newton
failures and bounded 6.2 successes remain recorded. The original `20f` PhysX stall
still needs a bounded reproduction/recovery disposition.

All **99 names** have retained calls. This census indexes **12,073 call records across 39 source-identified cases**, with **1,583 explicit or recomputed observation links**. These are record counts, including repeated observations, expected refusals and failed workflows; they are not deduplicated dispatches, independent effect tests, or proof that 99 tools work on every backend. Original input hashes, JSON pointers, actual arguments and MCP envelopes are retained. The original [1,944-call index](ALL-TOOLS-INDEX-20261102.json) remains immutable.

The [compact machine-readable ledger](CURRENT-TOOL-RECONCILIATION-20261102.json) has one row per tool and per-case counters/sample call IDs. The [draft-release index and two record parts](RELEASE-ASSETS.md) contain full lookups and actual responses. Each `Rxx` case identifies the exact source, release, mode and backend; a table cell never changes the original SHA. Draft attachments require repository access.

## Actual discovery profiles

These were real MCP client/server captures, online against selected PID 2948441 / port 18985 / ROS domain 231 and separately offline. The fresh headless 6.2 process made no simulation steps and exited normally through managed SIGINT. Its final native log had 0 error lines and 9 warnings. This qualifies discovery/targeting/error envelopes only.

| Profile | Environment overrides | Online | Offline |
|---|---|---:|---:|
| Default | `ISAAC_MCP_LOCAL_TOOLS` unset; scene/builtins/namespace filters unset | 90 | 83 |
| Optional-inclusive | `ISAAC_MCP_LOCAL_TOOLS=all,screen_input,sim_launch` | 99 | 92 |
| Optional-inclusive, scene off | Above plus `ISAAC_MCP_SCENE_TOOLS=0` | 60 | 53 |
| Optional-inclusive, built-ins off | Above plus `ISAAC_MCP_BUILTINS=0` | 92 | 92 |

Default-group scene-off remains **51 online / 44 offline** in the documented catalog; it was not one of the newly measured profiles. Do not substitute the optional-inclusive 60/53 result. The retained capture harness removes inherited namespace/builtins/scene/group filters before applying each profile. Explicit endpoint, project approvals and output roots are separate launch configuration.

The 99 names are 31 generic scene + 8 assets + 23 UR5e + 7 bridge built-ins + 12 Lab + 14 host/display/gateway + 4 managed-session tools. Nine are opt-in (five desktop-input and four session tools). Against the earlier `c2ceb7f` inventory there are no name, parameter-schema, output-schema or annotation changes; six descriptions changed: body state, camera creation, joint state, stepping, UR Play and Quick Start.

## Source relationships and remaining gates

Twenty-one complete module comparisons match the retained references byte for byte: current twin modules match `2c8ce63`; host error-envelope code matches `826b84b`; current camera/advanced modules match `1f21407`; physics observation code matches `ba1d4b2`; selected host files match `884a8fa`; Lab, asset, core/timeline/runtime-edit files match `c2ceb7f`. Matching modules document source relationships, not an execution of a newer complete head.

- Broad PhysX fixtures exist in both modes for all five installed releases. Their source heads differ. Later `2c8ce63` affected runs exercise all 23 UR tools and seven built-ins in all ten cells; later `1f21407` camera fixtures exercise units/clipping/resolution/refusal effects in all ten cells. Earlier generic workflows and independent tasks retain their own source identities.
- The newest body/joint/step invalid-data checks have CPU coverage and a fresh `ba1d4b2` Newton error-reporting fixture (33 calls / 14 checks). The normal PhysX path was unrecorded when this JSON census was made; the later [exact `0163` supplement](PHYSX-CURRENT-POSITIVE.md) now qualifies it on all five H/W pairs. Neither record relabels the older positive observations.
- The `1f` newly saved camera fixtures were reopened with standalone OpenUSD, not fresh Kit. Earlier fresh sensor/agent-save reopens remain separate; fresh-Kit reuse of these new clipping fixtures is an additional narrow check if claimed.
- **The PhysX risk remains:** original `20f` 6.0.1 windowed 108-step stall. **Experimental Newton limitations remain:** 6.0.0.1 crashes/room stall; 6.0.1 and 6.1 nonfinite assembled-scene states, black rendering and retained fatal transitions. Private controls, a typed error, process cleanup and successful later tasks do not resolve those failures. Newton 1.0 RG2 mounting is unsupported; older unavailable wrench APIs are distinct. Candidate9/10 Newton6.2 evidence is bounded historical qualification, not all 6.2 Newton support. No further Newton qualification is a release gate under the revised user scope.
- Prepared older installations, a scoped locked 6.2 venv and the diagnostic crash-upload-disabled launcher are explicit. Full-installer/clean-machine qualification remains open. Lab3/Sim6.2 has two dependency metadata conflicts and an unexecuted locked replay. No physical robot, future release, distinct-host authenticated upload or isolated-untrusted support is claimed.

PR #16 remains draft and unmerged. These findings support review and the next bounded checks; they do not authorize merging around a material failure. Residual warnings retain their scope and uncertain impact, without making warning counts an unconditional blocker.

## Reading the per-tool matrix

Each build cell is **headless/windowed**. `O` links an explicit or recomputed bounded observation; `C` has calls but no direct effect predicate in this particular index; `F` links the later published camera-focused effect fixture; `—` is absent from the selected case. `O` is not every-argument coverage. UR rows select later `2c` cases; camera create/capture select `1f`; other rows use the broad original sources below. Refusals are counted separately in JSON. Additional save/reopen, visual, agent and failure scopes live in the linked compacts.

| Release | Broad H / W source | Later affected twin H/W | Camera-focused H/W |
|---|---|---|---|
| 6.0.0.0 | `c2ceb7f` / `c2ceb7f` | `2c8ce63` | `1f21407` |
| 6.0.0.1 | `c2ceb7f` / `c2ceb7f` | `2c8ce63` | `1f21407` |
| 6.0.1.0 | `eea18c8` / `20f2953` | `2c8ce63` | `1f21407` |
| 6.1.0.0 | `6bc45c2` / `6bc45c2` | `2c8ce63` | `1f21407` |
| 6.2.0.0 | locked `c2ceb7f` / `c2ceb7f` | `2c8ce63` | `1f21407` |

The original `eea` URDF mass/collision check failed; later `230f4b3` independently verifies converted physical schemas and measured articulation motion. It is a separate case, not a repaired historical result. The explicit Newton6001W post-Stop readback failure is also retained. Additional native failures are not reduced to the number of false assertions in this census.

| Tool | 6.0.0.0 | 6.0.0.1 | 6.0.1.0 | 6.1.0.0 | 6.2.0.0 | Observed scope / qualification limit |
|---|---|---|---|---|---|---|
| `assets__import_path` | O/O | O/O | O/O | O/O | O/O | multifile absolute-path robot import composes with placed pose and joints |
| `assets__import_urdf` | O/O | O/O | failed → 230/O | O/O | O/O | URDF separate subtree and physics reports complete |
| `assets__import_usd` | O/O | O/O | O/O | O/O | O/O | Independent imported wrapper readback is world [0.5,0,0] |
| `assets__info` | O/O | O/O | O/O | O/O | O/O | Multifile inspection reports >=2 layers and >=1 asset dependency |
| `assets__prepare_workstation` | O/O | O/O | O/O | O/O | O/O | licensed multifile workstation prepared without replacing existing destination |
| `assets__roots` | O/O | O/O | O/O | O/O | O/O | Approved fixture root is listed and robot.usda appears in its bounded scan |
| `assets__scan` | O/O | O/O | O/O | O/O | O/O | Approved fixture root is listed and robot.usda appears in its bounded scan |
| `assets__search` | O/O | O/O | O/O | O/O | O/O | Search returns the approved fixture robot with explicit no-network response |
| `get_file` | O/O | O/O | O/O | O/O | O/O | builtin byte roundtrip |
| `list_bridges` | O/O | O/O | O/O | O/O | O/O | bridge discovery includes target |
| `put_file` | O/O | O/O | O/O | O/O | O/O | builtin byte roundtrip |
| `reload_tools` | O/O | O/O | O/O | O/O | O/O | reloaded fixture 7 |
| `run_python` | O/O | O/O | O/O | O/O | O/O | builtin arithmetic |
| `sim__body__state` | O/O | O/O | O/O | O/O | O/O | Historical gravity/runtime-mass observation; current path additionally qualified in the separate 0163 H/W supplement. |
| `sim__camera__capture` | F/F | F/F | F/F | F/F | F/F | 1f new-camera clipping: meter/cm units, near-plane pixel change, two resolutions, invalid-unit refusal; no physics steps. |
| `sim__camera__create` | F/F | F/F | F/F | F/F | F/F | 1f new-camera clipping: meter/cm units, near-plane pixel change, two resolutions, invalid-unit refusal; no physics steps. |
| `sim__controller__create` | O/O | O/O | O/O | O/O | O/O | Updated opt-in controller changes independent Visual prim readback to z=2 |
| `sim__controller__update` | O/O | O/O | O/O | O/O | O/O | Updated opt-in controller changes independent Visual prim readback to z=2 |
| `sim__joints__command` | O/O | O/O | O/O | O/O | O/O | 120 steps advance 1 simulated second; commanded elbow reaches -0.2 rad and body rests on floor |
| `sim__joints__drive` | O/O | O/O | O/O | O/O | O/O | Runtime elbow reaches authored +0.2 rad drive |
| `sim__joints__inspect` | O/O | O/O | O/O | O/O | O/O | Loaded fixture exposes authored revolute-joint configuration |
| `sim__joints__state` | O/O | O/O | O/O | O/O | O/O | Historical runtime elbow observation; current path additionally qualified in the separate 0163 H/W supplement. |
| `sim__launch` | O/O | O/O | O/O | O/O | O/O | Managed launch PID matches later independent Kit stage response |
| `sim__lidar__create` | O/O | O/O | O/O | O/O | O/O | LiDAR scan contains the known wall at 2.5m within .03m |
| `sim__lidar__read` | O/O | O/O | O/O | O/O | O/O | LiDAR scan contains the known wall at 2.5m within .03m |
| `sim__light__create` | O/O | O/O | O/O | O/O | O/O | light create independently reopened USD type/intensity/color |
| `sim__light__modify` | O/O | O/O | O/O | O/O | O/O | light modify independently reopened intensity/color |
| `sim__list` | O/O | O/O | O/O | O/O | O/O | managed status matches target |
| `sim__logs__tail` | O/O | O/O | O/O | O/O | O/O | Bounded native log response contains <=10 lines (not a no-error claim) |
| `sim__material__apply` | O/O | O/O | O/O | O/O | O/O | source material collision and dimensions preserved |
| `sim__material__create` | O/O | O/O | O/O | O/O | O/O | source material collision and dimensions preserved |
| `sim__physics__configure` | O/O | O/O | O/O | O/O | O/O | 120 steps advance 1 simulated second; commanded elbow reaches -0.2 rad and body rests on floor |
| `sim__physics__inspect` | O/O | O/O | O/O | O/O | O/O | Imported USD subtree reports articulation and joint schemas |
| `sim__prim__clone` | O/O | O/O | O/O | O/O | O/O | clone pre-delete USD content equals source |
| `sim__prim__create` | O/O | O/O | O/O | O/O | O/O | Observed body falls with positive runtime mass and increased downward velocity |
| `sim__prim__delete` | O/O | O/O | O/O | O/O | O/O | Independent get after clone deletion refuses missing path |
| `sim__prim__find` | O/O | O/O | O/O | O/O | O/O | Bounded Robot search contains the independently loaded wrapper |
| `sim__prim__get` | O/O | O/O | O/O | O/O | O/O | Independent imported wrapper readback is world [0.5,0,0] |
| `sim__prim__set_transform` | O/O | O/O | O/O | O/O | O/O | Independent imported wrapper readback is world [0.5,0,0] |
| `sim__simulation__step` | O/O | O/O | O/O | O/O | O/O | Historical timing/joint/floor observations; current path additionally qualified in the separate 0163 H/W supplement. |
| `sim__stage__clear_owned` | O/O | O/O | O/O | O/O | O/O | Independent post-clear tree omits deleted owned roots while retaining protected robot |
| `sim__stage__info` | O/O | O/O | O/O | O/O | O/O | Stage info matches selected managed PID and has explicit stage/session identities |
| `sim__stage__save` | O/O | O/O | O/C | O/O | O/O | Private saved copy and source/live-layer preservation checks; default runtime-handle refusal and explicit normalization are distinct. W601 extra save/reopen is linked separately. |
| `sim__stage__tree` | O/O | O/O | O/O | O/O | O/O | URDF separate subtree and physics reports complete |
| `sim__status` | O/O | O/O | O/O | O/O | O/O | managed status matches target |
| `sim__stop` | O/O | O/O | C/C | O/O | O/O | Owned managed Stop followed by recorded PID absence |
| `sim__timeline__control` | O/O | O/O | O/O | O/O | O/O | Observed body falls with positive runtime mass and increased downward velocity |
| `sim__viewport__frame` | C/C | C/C | C/C | C/C | C/C | Broad calls; separate eea 46-call/12-check session/source/error fixture. render_complete=false; visibility not guaranteed. |
| `stat_file` | O/O | O/O | O/O | O/O | O/O | builtin byte roundtrip |
| `status` | O/O | O/O | O/O | O/O | O/O | Managed launch PID matches later independent Kit stage response |
| `ur5e_dt__add_objects` | O/O | O/O | O/O | O/O | O/O | five actual objects present; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__assemble_objects` | O/O | O/O | O/O | O/O | O/O | assembly orientation u_brown; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__delete_objects` | O/O | O/O | O/O | O/O | O/O | objects deleted; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__get_recording_status` | O/O | O/O | O/O | O/O | O/O | viewport recorder running; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__load_simple_room` | O/O | O/O | O/O | O/O | O/O | room composed; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__load_workstation` | O/O | O/O | O/O | O/O | O/O | real licensed bench composed; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__new_stage` | O/O | O/O | O/O | O/O | O/O | new stage has no owned scene; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__play_scene` | O/O | O/O | O/O | O/O | O/O | real runtime elbow moved; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__quick_start` | O/O | O/O | O/O | O/O | O/O | quick start creates robot; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__quick_start_with_workstation` | O/O | O/O | O/O | O/O | O/O | bench in quick_start_with_workstation; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__quick_start_with_workstation_and_simple_room` | O/O | O/O | O/O | O/O | O/O | bench in quick_start_with_workstation_and_simple_room; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__randomize_object_poses` | O/O | O/O | O/O | O/O | O/O | randomize preserves board; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__randomize_single_object` | O/O | O/O | O/O | O/O | O/O | single randomizer only changes requested body; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__restore_scene_state` | O/O | O/O | O/O | O/O | O/O | nonidentity pose restored; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__save_scene_state` | O/O | O/O | O/O | O/O | O/O | saved all objects; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__set_object_pose` | O/O | O/O | O/O | O/O | O/O | pose position and yaw applied; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__setup_camera_tf_graph` | O/O | O/O | O/O | O/O | O/O | ROS publishes /intel_camera_rgb_sim; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__setup_pose_publisher` | O/O | O/O | O/O | O/O | O/O | ROS publishes /objects_poses_sim; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__start_recording` | O/O | O/O | O/O | O/O | O/O | viewport recorder running; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__start_ros_driver` | O/O | O/O | O/O | O/O | O/O | fake trajectory completed; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__stop_recording` | O/O | O/O | O/O | O/O | O/O | viewport recorder stopped; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__stop_scene` | O/O | O/O | O/O | O/O | O/O | Immediate independent stage info after named UR Stop is stopped; PhysX scope, no failed-Newton promotion. |
| `ur5e_dt__sync_real_poses` | O/O | O/O | O/O | O/O | O/O | simulated TF pose position and yaw; PhysX scope, no failed-Newton promotion. |

## Host/display and Lab evidence

These tools are not expected to be rerun once per Sim release when their implementation and scope are independent of that release. Selected PID/window/managed-session identity remains part of simulator interaction. The Lab rows refer only to the explicitly recorded pairing. Historical desktop effects coexist with later partial agent interactions; an acknowledgement alone is insufficient.

| Tool | Scope and source | Observation / limitation |
|---|---|---|
| `isaaclab__checkpoints_list` | Lab3.0 / Sim6.2, c2 pairing; H/W jobs | checkpoint produced and located. Two metadata conflicts; no policy-quality claim. |
| `isaaclab__envs_list` | Lab3.0 / Sim6.2, c2 pairing; H/W jobs | actual Cartpole registry discovered. Two metadata conflicts; no policy-quality claim. |
| `isaaclab__eval` | Lab3.0 / Sim6.2, c2 pairing; H/W jobs | checkpoint evaluation completed. Two metadata conflicts; no policy-quality claim. |
| `isaaclab__install_info` | Lab3.0 / Sim6.2, c2 pairing; H/W jobs | installed compatible Lab/Sim pair. Two metadata conflicts; no policy-quality claim. |
| `isaaclab__metrics_summary` | Lab3.0 / Sim6.2, c2 pairing; H/W jobs | actual training metrics exist. Two metadata conflicts; no policy-quality claim. |
| `isaaclab__play` | Lab3.0 / Sim6.2, c2 pairing; H/W jobs | windowed policy playing. Two metadata conflicts; no policy-quality claim. |
| `isaaclab__runs_cancel` | Lab3.0 / Sim6.2, c2 pairing; H/W jobs | Completed-job protection and actual owned-live-job cancellation; PID absence checked. Two metadata conflicts; no policy-quality claim. |
| `isaaclab__runs_list` | Lab3.0 / Sim6.2, c2 pairing; H/W jobs | Runs list includes the completed training job and its succeeded state. Two metadata conflicts; no policy-quality claim. |
| `isaaclab__runs_status` | Lab3.0 / Sim6.2, c2 pairing; H/W jobs | real training completed. Two metadata conflicts; no policy-quality claim. |
| `isaaclab__runs_tail` | Lab3.0 / Sim6.2, c2 pairing; H/W jobs | real training completed. Two metadata conflicts; no policy-quality claim. |
| `isaaclab__tensorboard_tail` | Lab3.0 / Sim6.2, c2 pairing; H/W jobs | two recorded training iterations. Two metadata conflicts; no policy-quality claim. |
| `isaaclab__train` | Lab3.0 / Sim6.2, c2 pairing; H/W jobs | real training completed. Two metadata conflicts; no policy-quality claim. |
| `list_windows` | Real X11 host; original source in JSON, no relabel | Actual bounded discovery/inspection response inspected; no broader parameter claim. |
| `raise_window` | Real X11 host; original source in JSON, no relabel | Actual bounded discovery/inspection response inspected; no broader parameter claim. |
| `refresh_tools` | Real X11 host; original source in JSON, no relabel | 99 shipped tools exposed. |
| `screen_info` | Real X11 host; original source in JSON, no relabel | Actual bounded discovery/inspection response inspected; no broader parameter claim. |
| `screen_recording_status` | Real X11 host; original source in JSON, no relabel | Real selected-window video decoded with motion. |
| `screenshot` | Real X11 host; original source in JSON, no relabel | Console and actual native log inspected. |
| `start_screen_recording` | Real X11 host; original source in JSON, no relabel | Real selected-window video decoded with motion. |
| `stop_screen_recording` | Real X11 host; original source in JSON, no relabel | Real selected-window video decoded with motion. |
| `use_bridge` | Real X11 host; original source in JSON, no relabel | 99 shipped tools exposed. |
| `window_click` | Real X11 host; original source in JSON, no relabel; opt-in | Click, type and key visible effects. Later desktop acknowledgements can be partial. |
| `window_drag` | Real X11 host; original source in JSON, no relabel; opt-in | Real dock divider movement and restoration. Later desktop acknowledgements can be partial. |
| `window_key` | Real X11 host; original source in JSON, no relabel; opt-in | Click, type and key visible effects. Later desktop acknowledgements can be partial. |
| `window_scroll` | Real X11 host; original source in JSON, no relabel; opt-in | Scroll visibly moves Console content. Later desktop acknowledgements can be partial. |
| `window_type` | Real X11 host; original source in JSON, no relabel; opt-in | Click, type and key visible effects. Later desktop acknowledgements can be partial. |

## Audit and reproduction

All 5,058 original input files were rehashed and all 12,073 indexed call pointers/arguments/response hashes checked before curation. The unchanged privacy scanner checked each archive, including nested members; no images, source geometry, checkpoints or vendor files are included. Distribution hashes differ from original-record hashes after path/hostname and opaque-pixel redaction. Three draft archives contain the index and two bounded record parts; [RELEASE-ASSETS](RELEASE-ASSETS.md) lists their sizes and SHA-256.

Use `CALLS.json` to resolve a sample ID, then follow its `record.path` and JSON pointer in `records/.work/...` or `records/docs/...` from the two record archives. `OBSERVATIONS.json` links the exact retained assertion or retrospective numerical comparison. Review the real response alongside the effect predicate, source case and known failure list. The analysis scripts are included; they make no simulator calls. The separate inventory harness uses a real stdio MCP server and an explicitly selected disposable session.

To verify distributed files, inspect archive listings and extract into a new empty private directory, then run `sha256sum -c REDACTED-SHA256SUMS` for each part before combining them. Never substitute redacted hashes for original run hashes. Setup and native workflow commands remain in the repository reproduction guide and the feature-specific bundles. This reconciliation performs no new desktop action, broad simulation, installer execution or physics repair.

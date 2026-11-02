# All exposed tools: final validation ledger

Validation remains in progress. All implementation PRs are unmerged; combined PR #16
is a draft. The candidate at this update is `d725f84cb853bf5ceb2fa3b7d13d0e002d29becf`.
The real MCP inventories contain **90 default tools** and **99** with
`ISAAC_MCP_LOCAL_TOOLS=all,screen_input,sim_launch`. Disabling the generic pack
with those optional groups selected leaves 60 tools. Every name is tracked below.

The earlier 6.2 PhysX runs used `2729b076817e9a0672825ed7e3cc7d160fda96db`
(headless) and `884a8fa6f8ca7ab55e3c68874c97703e44df7b18` (windowed).
Runtime source is unchanged between those heads and `8106771`; the later changes
correct the agent skill and its linked reference. The recorded run SHAs are retained,
not relabeled as runs of the newer documentation commit. Both broad suites completed
all 39 generic tools, all 23 UR5e tools and all seven bridge built-ins. The independent
windowed agent task and its final visual/Console review are complete: 117 requests,
40 tools, 28 passing effect checks and a retained RG2 controller-conflict failure.

Each suite checks outputs or side effects, including measured body/joint motion, ROS
messages, decoded sensor images/video, source preservation and explicit refusal paths.
A tool call count or successful response alone is not an effect assertion. The [99-name evidence projection](ALL-TOOLS-INDEX-20261102.json) records representative
arguments, exact source identities, response hashes/pointers and observation links.
It covers 1,944 calls and 437 assertion/observation links from a defined historical
subset, not the full version matrix. All names have calls; its original four weak
links are preserved. Subsequent light/clone readbacks and the URDF finding/fix are
recorded separately below; the immutable index is not retroactively relabeled.
The corrected 6.0.0.0 runtime passed the broad PhysX suites headless at
`d93226051895b48d8e2be79e1f7e3814011ec5c5` and windowed at
`2fd143861f654799a41c0bcbe5e3ea1691fd7b62`. Simulator runtime was unchanged
from those heads through `2bbeee4`; later Stop-completion, bridge lifecycle and viewport
fixes require affected-path requalification. Its independent windowed task made 71 requests and passed
20 effect checks, retaining one multi-joint target miss before a smaller target reached
all six joints within 0.001 rad. This is a partial target result, not an all-pose claim.
The 6.0.0.1 headless broad suite passed at `4499bf7` after the explicit-Stop completion fix, including 27 fresh-process reopen captures, all 39 generic tools, all 23 UR5e tools and seven built-ins, 137 calls, 55 assertions, 21 save checks and legacy motion. Its windowed counterpart also passed the broad suite (141 calls / 54 assertions); independent use made 93 requests / 35 names with 22 effect checks and one framing limitation; legacy verification also passed. A later anonymous-camera fixture confirmed viewport source-layer mutation. The complete fresh viewport fixture at `eea18c8` passed 46 MCP calls and 12 checks,
including source/reference/edit-target preservation and injected failure responses.
The subsequent `eea18c8` 6.0.1.0 headless broad suite passed its 14 phases, but an extra
URDF readback found missing physical properties behind an invalid native variant
selection. `230f4b3` fixes the private conversion output; fresh 6.0.1.0 headless
qualification independently verified mass/collision/geometry and measured two-link
joint motion. The `20f2953` 6.0.1.0 windowed broad run passed, but its later independent
task stalled in a native 108-step call and required owned-process force termination.
Fresh repeats and a separate successful 89-request task do not resolve that failure.
The [new supplement](RECENT-601W-20261102.json) also records independent light/clone/URDF
effects and fresh `6bc45c2` checkpoint/discovery qualification. Use the agent bundle's
v2 ledger/addendum; copied PID/port and overbroad observations in v1 are corrected
without rewriting original calls. Exact `6bc45c2` 6.1.0.0 headless/windowed broad
suites, legacy verification, independent light/clone readbacks and articulated URDF
checks passed. A named-tool agent task added 90 requests / 40 names / 17 checks;
its 99-name ledger retains 59 uncalled names. Fresh saved-output reopening at `d725f84`
passed 16 calls / 12 checks. [The scoped supplement](RECENT-61-20261102.json) preserves
warnings, sampled visual limits, source hashes and the unresolved original stall.
Other-build URDF fix qualification, affected 6.2/6.0.0.0/6.0.0.1 paths and final Newton
coverage remain pending. All 12 Isaac Lab tools passed a real Lab 3.0 / Sim 6.2 workflow at `411d89f` (41 calls, 16 effect checks). Later mode/video fixes were tested as exact source bytes on a private `33de98d`-based candidate and integrated into `2bbeee4`; these scoped results do not establish other Lab/Sim pairings or a clean installation.
Host/display tools need one actual-display run, plus simulator target verification.

Residual native cycle and simulation-time warnings remain recorded with uncertainty.
They do not by themselves block progress; crashes, incorrect outputs, data loss and
unreliable recovery require fixes or an explicit unresolved limitation.

| Tool | Exposure | Required scenario | Current evidence state |
|---|---|---|---|
| `assets__import_urdf` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | Original index: invocation only. Extra eea 6.0.1H readback found missing body/mass. Fixed230:24-call authored effect suite and25-call two-link runtime suite passed. Exact6bc 6.1H/W also passed both suites; remaining builds pending. |
| `assets__import_path` | default | Absolute approved multifile USD import; authored pose/joints, source hashes, conflict/unapproved-path refusal | 6 recorded calls; all input source hashes unchanged (4499bf7). Scope/arguments/response references: JSON index. |
| `assets__prepare_workstation` | default | Licensed Vention project into fresh cache; repeat refusal; real Quick Start composition and source hashes | 4 recorded calls; all input source hashes unchanged (4499bf7). Scope/arguments/response references: JSON index. |
| `assets__import_usd` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 9 recorded calls; Imported robot world transform reads back placed0.5m (4499bf7). Scope/arguments/response references: JSON index. |
| `assets__info` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 7 recorded calls; Imported fixture has articulation and joint structure (4499bf7). Scope/arguments/response references: JSON index. |
| `assets__roots` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 5 recorded calls; Approved Agilebot multifile project imported (4499bf7). Scope/arguments/response references: JSON index. |
| `assets__scan` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 4 recorded calls; Approved scan returned fixture robot later inspected/imported; unapproved root refused (4499bf7). Scope/arguments/response references: JSON index. |
| `assets__search` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 3 recorded calls; Approved Agilebot multifile project imported (4499bf7). Scope/arguments/response references: JSON index. |
| `get_file` | default | Disposable scene and private temporary files; all five Sim releases | 2 recorded calls; builtin byte roundtrip (4499bf7). Scope/arguments/response references: JSON index. |
| `isaaclab__checkpoints_list` | default | Real Lab 3.0 / Sim 6.2 workflow; jobs, output/checkpoints/metrics or observation | 3 recorded calls; Headless training produced two checkpoints and metrics (411d89f, 33de98d). Scope/arguments/response references: JSON index. |
| `isaaclab__envs_list` | default | Real Lab 3.0 / Sim 6.2 workflow; jobs, output/checkpoints/metrics or observation | 1 recorded calls; actual Cartpole registry discovered (411d89f). Scope/arguments/response references: JSON index. |
| `isaaclab__eval` | default | Real Lab 3.0 / Sim 6.2 workflow; jobs, output/checkpoints/metrics or observation | 2 recorded calls; Recorded video decoded 60 frames (411d89f, 33de98d). Scope/arguments/response references: JSON index. |
| `isaaclab__install_info` | default | Real Lab 3.0 / Sim 6.2 workflow; jobs, output/checkpoints/metrics or observation | 1 recorded calls; installed compatible Lab/Sim pair (411d89f). Scope/arguments/response references: JSON index. |
| `isaaclab__metrics_summary` | default | Real Lab 3.0 / Sim 6.2 workflow; jobs, output/checkpoints/metrics or observation | 2 recorded calls; Headless training produced two checkpoints and metrics (411d89f, 33de98d). Scope/arguments/response references: JSON index. |
| `isaaclab__play` | default | Real Lab 3.0 / Sim 6.2 workflow; jobs, output/checkpoints/metrics or observation | 3 recorded calls; Recorded video decoded 60 frames (411d89f, 33de98d). Scope/arguments/response references: JSON index. |
| `isaaclab__runs_cancel` | default | Real Lab 3.0 / Sim 6.2 workflow; jobs, output/checkpoints/metrics or observation | 2 recorded calls; cancel stopped owned live job (411d89f). Scope/arguments/response references: JSON index. |
| `isaaclab__runs_list` | default | Real Lab 3.0 / Sim 6.2 workflow; jobs, output/checkpoints/metrics or observation | 2 recorded calls; Final job list agrees with separate final status of actual train/eval/play jobs (411d89f). Scope/arguments/response references: JSON index. |
| `isaaclab__runs_status` | default | Real Lab 3.0 / Sim 6.2 workflow; jobs, output/checkpoints/metrics or observation | 84 recorded calls; cancel stopped owned live job (411d89f, 33de98d). Scope/arguments/response references: JSON index. |
| `isaaclab__runs_tail` | default | Real Lab 3.0 / Sim 6.2 workflow; jobs, output/checkpoints/metrics or observation | 17 recorded calls; windowed policy playing (411d89f, 33de98d). Scope/arguments/response references: JSON index. |
| `isaaclab__tensorboard_tail` | default | Real Lab 3.0 / Sim 6.2 workflow; jobs, output/checkpoints/metrics or observation | 2 recorded calls; unknown scalar clearly refused (411d89f). Scope/arguments/response references: JSON index. |
| `isaaclab__train` | default | Real Lab 3.0 / Sim 6.2 workflow; jobs, output/checkpoints/metrics or observation | 3 recorded calls; Headless training produced two checkpoints and metrics (411d89f, 33de98d). Scope/arguments/response references: JSON index. |
| `list_bridges` | default | Disposable scene and private temporary files; all five Sim releases | 5 recorded calls; Exact intended target, unchanged stage identity (4499bf7, 2fd1438, 884a8fa). Scope/arguments/response references: JSON index. |
| `list_windows` | default | Real host/display; verify selected simulator window/PID and observable output | 10 recorded calls; ['Actual bounded discovery/inspection response inspected; no broader parameter claim.'] (4499bf7, 2fd1438, 884a8fa, eea18c8). Scope/arguments/response references: JSON index. |
| `put_file` | default | Disposable scene and private temporary files; all five Sim releases | 8 recorded calls; reloaded fixture 11 (4499bf7). Scope/arguments/response references: JSON index. |
| `raise_window` | default | Real host/display; verify selected simulator window/PID and observable output | 4 recorded calls; ['Actual bounded discovery/inspection response inspected; no broader parameter claim.'] (4499bf7, 2fd1438, 884a8fa). Scope/arguments/response references: JSON index. |
| `refresh_tools` | default | Real host/display; verify selected simulator window/PID and observable output | 11 recorded calls; ['Actual bounded discovery/inspection response inspected; no broader parameter claim.'] (4499bf7, 2fd1438, 884a8fa). Scope/arguments/response references: JSON index. |
| `reload_tools` | default | Disposable scene and private temporary files; all five Sim releases | 6 recorded calls; temporary tool removed (4499bf7). Scope/arguments/response references: JSON index. |
| `run_python` | default | Disposable scene and private temporary files; all five Sim releases | 74 recorded calls; builtin persistence (4499bf7, 37a7358, eea18c8). Scope/arguments/response references: JSON index. |
| `screen_info` | default | Real host/display; verify selected simulator window/PID and observable output | 1 recorded calls; ['Actual bounded discovery/inspection response inspected; no broader parameter claim.'] (884a8fa). Scope/arguments/response references: JSON index. |
| `screen_recording_status` | default | Real host/display; verify selected simulator window/PID and observable output | 8 recorded calls; Real selected-window video decoded with motion (4499bf7, 2fd1438, 884a8fa). Scope/arguments/response references: JSON index. |
| `screenshot` | default | Real host/display; verify selected simulator window/PID and observable output | 40 recorded calls; Console and actual native log inspected (4499bf7, 2fd1438, 884a8fa, 411d89f, 33de98d, eea18c8). Scope/arguments/response references: JSON index. |
| `sim__body__state` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 14 recorded calls; Falling body z decreases and downward velocity increases; positive mass (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__camera__capture` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 544 recorded calls; Fresh images show the colored sample falling (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__camera__create` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 37 recorded calls; 640×480 Hydra CPU camera worked on 6.0.0.1; own deletion/recreation succeeded. (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__controller__create` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 2 recorded calls; Updated named controller moves Visual to z2 on playback (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__controller__update` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 4 recorded calls; Updated named controller moves Visual to z2 on playback (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__joints__command` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 11 recorded calls; 120 steps measure1s, commanded elbow near-.2rad, body settled on floor (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__joints__drive` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 4 recorded calls; Driven elbow readback reaches +0.2 rad within0.03 (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__joints__inspect` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 4 recorded calls; Original joint limits and drives retained (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__joints__state` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 11 recorded calls; Driven elbow readback reaches +0.2 rad within0.03 (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__launch` | opt-in | Owned process, readiness/PID/port and shutdown, each Sim release | 4 recorded calls; Launched PID subsequently answered named state calls (4499bf7, eea18c8, 37a7358). Scope/arguments/response references: JSON index. |
| `sim__lidar__create` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 6 recorded calls; Point cloud includes known near wall at2.5m±.03 (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__lidar__read` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 12 recorded calls; Point cloud includes known near wall at2.5m±.03 (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__light__create` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | Original index: weak independent effect link. Extra eea and230 6.0.1H saved-USD readbacks verify light values and clone type/pose/material/collision; moving clone leaves source unchanged. No isolated lighting-pixel claim. |
| `sim__light__modify` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | Original index: weak independent effect link. Extra eea and230 6.0.1H saved-USD readbacks verify light values and clone type/pose/material/collision; moving clone leaves source unchanged. No isolated lighting-pixel claim. |
| `sim__list` | opt-in | Owned process, readiness/PID/port and shutdown, each Sim release | 2 recorded calls; managed status matches target (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__logs__tail` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 4 recorded calls; Native interval retains warnings without new errors (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__material__apply` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 11 recorded calls; Colored sample visibly orange in fresh output. (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__material__create` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 11 recorded calls; Created orange UsdPreviewSurface; actual camera pixels matched. (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__physics__configure` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 6 recorded calls; 120 steps measure1s, commanded elbow near-.2rad, body settled on floor (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__physics__inspect` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 7 recorded calls; Imported fixture has articulation and joint structure (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__prim__clone` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | Original index: weak independent effect link. Extra eea and230 6.0.1H saved-USD readbacks verify light values and clone type/pose/material/collision; moving clone leaves source unchanged. No isolated lighting-pixel claim. |
| `sim__prim__create` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 57 recorded calls; Falling body z decreases and downward velocity increases; positive mass (4499bf7, 37a7358, eea18c8). Scope/arguments/response references: JSON index. |
| `sim__prim__delete` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 41 recorded calls; Deleted cloned prim absent on subsequent get (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__prim__find` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 5 recorded calls; Located exact wrist link and tool0 paths; returned bounded search with 2214 visited. Unsupported root argument was ignored, so no root-specific search claim. (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__prim__get` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 136 recorded calls; Existing direct roots and graph tree preserved (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__prim__set_transform` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 11 recorded calls; Imported robot world transform reads back placed0.5m (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__simulation__step` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 165 recorded calls; 120 steps measure1s, commanded elbow near-.2rad, body settled on floor (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__stage__clear_owned` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 2 recorded calls; Independent post-clear tree confirms removed objects absent and protected camera/robot retained (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__stage__info` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 131 recorded calls; Returned paused in same selected stage (4499bf7, 37a7358, eea18c8). Scope/arguments/response references: JSON index. |
| `sim__stage__save` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 20 recorded calls; live root and session layer hashes unchanged (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__stage__tree` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 18 recorded calls; Bounded stage traversal marks truncation (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__status` | opt-in | Owned process, readiness/PID/port and shutdown, each Sim release | 2 recorded calls; managed status matches target (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__stop` | opt-in | Owned process, readiness/PID/port and shutdown, each Sim release | 4 recorded calls; Recorded process exit after managed Stop (4499bf7, 37a7358, eea18c8). Scope/arguments/response references: JSON index. |
| `sim__timeline__control` | default | 39-tool behavior/edge suite and recorded actual use, each Sim release | 96 recorded calls; Falling body z decreases and downward velocity increases; positive mass (4499bf7). Scope/arguments/response references: JSON index. |
| `sim__viewport__frame` | default | Named framing, actual rendered view, source/edit-target preservation and failure reporting | 24 recorded calls; console-review.json (4499bf7, 37a7358, eea18c8). Scope/arguments/response references: JSON index. |
| `start_screen_recording` | default | Real host/display; verify selected simulator window/PID and observable output | 5 recorded calls; Real selected-window video decoded with motion (4499bf7, 2fd1438, 884a8fa, 411d89f). Scope/arguments/response references: JSON index. |
| `stat_file` | default | Disposable scene and private temporary files; all five Sim releases | 2 recorded calls; builtin byte roundtrip (4499bf7). Scope/arguments/response references: JSON index. |
| `status` | default | Disposable scene and private temporary files; all five Sim releases | 15 recorded calls; Exact intended target, unchanged stage identity (4499bf7, 2fd1438, 884a8fa, 37a7358, eea18c8). Scope/arguments/response references: JSON index. |
| `stop_screen_recording` | default | Real host/display; verify selected simulator window/PID and observable output | 5 recorded calls; Real selected-window video decoded with motion (4499bf7, 2fd1438, 884a8fa, 411d89f). Scope/arguments/response references: JSON index. |
| `ur5e_dt__add_objects` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 4 recorded calls; all six redistributable FMB4 blocks composed (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__assemble_objects` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 4 recorded calls; assembly orientation inverted_u_yellow (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__delete_objects` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 2 recorded calls; objects deleted (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__get_recording_status` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 4 recorded calls; recorded video decodes actual frames (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__load_simple_room` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 2 recorded calls; room composed (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__load_workstation` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 2 recorded calls; restored real cache recovers bench/robot/tool0 through named tools (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__new_stage` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 33 recorded calls; {'transition': 'generic play -> UR new stage', 'lease_released': True} (4499bf7, 37a7358, eea18c8). Scope/arguments/response references: JSON index. |
| `ur5e_dt__play_scene` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 4 recorded calls; real runtime elbow moved (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__quick_start` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 6 recorded calls; restored real cache recovers bench/robot/tool0 through named tools (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__quick_start_with_workstation` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 11 recorded calls; bench in quick_start_with_workstation (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__quick_start_with_workstation_and_simple_room` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 2 recorded calls; room in composite (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__randomize_object_poses` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 2 recorded calls; randomize changes all four parts (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__randomize_single_object` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 2 recorded calls; single randomizer uses documented strip (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__restore_scene_state` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 2 recorded calls; nonidentity pose restored (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__save_scene_state` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 2 recorded calls; saved all objects (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__set_object_pose` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 18 recorded calls; FMB4 position and active yaw (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__setup_camera_tf_graph` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 3 recorded calls; image clock remains monotonic across Stop/Play (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__setup_pose_publisher` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 2 recorded calls; ROS publishes /objects_poses_sim (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__start_recording` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 2 recorded calls; recorded video decodes actual frames (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__start_ros_driver` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 6 recorded calls; real runtime elbow moved (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__stop_recording` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 2 recorded calls; recorded video decodes actual frames (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__stop_scene` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 21 recorded calls; {'transition': 'generic play -> UR new stage', 'lease_released': True} (4499bf7). Scope/arguments/response references: JSON index. |
| `ur5e_dt__sync_real_poses` | default | Named operation, effect readback; simulated ROS where relevant, all five Sim releases | 4 recorded calls; no TF publisher returns clear error (4499bf7). Scope/arguments/response references: JSON index. |
| `use_bridge` | default | Real host/display; verify selected simulator window/PID and observable output | 5 recorded calls; Exact intended target, unchanged stage identity (4499bf7, 2fd1438, 884a8fa). Scope/arguments/response references: JSON index. |
| `window_click` | opt-in | Real host/display; verify selected simulator window/PID and observable output | 12 recorded calls; Click, type and key visible effects (4499bf7, 2fd1438, 884a8fa). Scope/arguments/response references: JSON index. |
| `window_drag` | opt-in | Real host/display; verify selected simulator window/PID and observable output | 2 recorded calls; Real dock divider movement and restoration (884a8fa). Scope/arguments/response references: JSON index. |
| `window_key` | opt-in | Real host/display; verify selected simulator window/PID and observable output | 4 recorded calls; Click, type and key visible effects (4499bf7, 884a8fa). Scope/arguments/response references: JSON index. |
| `window_scroll` | opt-in | Real host/display; verify selected simulator window/PID and observable output | 3 recorded calls; Scroll visibly moves Console content (4499bf7, 884a8fa). Scope/arguments/response references: JSON index. |
| `window_type` | opt-in | Real host/display; verify selected simulator window/PID and observable output | 2 recorded calls; Click, type and key visible effects (4499bf7, 884a8fa). Scope/arguments/response references: JSON index. |

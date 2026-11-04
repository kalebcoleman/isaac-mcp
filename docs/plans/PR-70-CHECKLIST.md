# PR 70 working checklist

Kaleb's running list for PR 70, so nothing is lost as requests are added. Tick an item only after it is tested.
When PR 70 merges, move what is left to the roadmap and delete this file.

## Fleet and version testing

- [x] Install Isaac Sim 6.0.0.0, 6.0.0.1 and 6.1.0.0 on the ASU PC
- [x] ROS 2 on every machine, matching the OS: Humble system install on the ASU PC, Jazzy on this desktop
Claude codes and reviews. DeepSeek V4.1 Flash on OpenCode Go scouts and tests, as a deliberately weaker user.

- [x] First round on the ASU PC, reporting to `<home>/isaac-mcp-test-logs/pr70-fleet/`: host and docs, generic scene, windowed, UR5e twin, ROS 2, 6.0.1, Isaac Lab, sessions, then 6.0.0.0, 6.0.0.1 and 6.1.0.0
- [ ] Fix what the fleet found (in progress)
- [x] Re-test round on every build at the pushed commit, including goal-only briefs with no hand-holding
- [x] Run `validate_scene_equivalents.py` and `validate_scene_clear.py`, which need the Agilebot pack
- [ ] End-to-end asset pack test with public GitHub assets. First run: the build tool failed for the chess set. A pass means no workaround:
  - [x] `assets__pack_build` alone makes a pack that imports and renders with textures, with the source hidden
  - [ ] The same for a real robot with physics: build, import, Play, and move a joint with universal tools
  - [ ] Anything needing a workaround counts as a fail
- [ ] Pack test findings:
  - [x] Closure follows MaterialX and MDL textures, and `self_contained` is never falsely true
  - [x] Add specific files to a pack without adopting a whole folder
  - [x] `package://` URDF refusal names the path, and the up-axis requirement is documented
  - [x] Approval: one missing root must not disable the rest; the helper can add a root; fewer Kit restarts
  - [x] `assets__pack_adopt` records entry files
  - [x] Axis lines in `sim__camera__capture` images: exclude them, or document why not
- [ ] Stable Isaac Lab pairs only: 5.1 with Lab 2.3.x, and 6.2 with Lab 3.0.0 (done). Tiny real jobs passed through the tools on both pairs, with all four RL libraries on 2.3.2; `tested` stays false for 5.1 + 2.3.2
- [x] Old stale LiDAR files: the repair failed on 6.0.0.0, 6.0.0.1, 6.0.1 and 6.1, and passed on 6.2. Fix or scope it, then re-test per build
- [ ] `sim__map__occupancy` all-free map on 6.0.0.1
- [x] Standalone install launcher crash on 6.0.1

## Isaac Sim 5.1 (on this desktop)

- [x] Version adapters: engine, view, step, composition errors, drawable events, LiDAR config
- [x] `run.sh` finds 5.1's ROS libraries; the 5.1 build is recorded
- [x] Headless walk, `/scan` across a new Kit, `smoke_tools.py` 20/20, `verify.sh`
- [x] Windowed: Quick Start, panel, `sim__graph__show`, recording, UI tools
- [x] `sim__launch` with a 5.1 interpreter; `install.sh` accepting a pinned 5.1 (with fakes)
- [x] Update `docs/COMPATIBILITY.md` and the "6.x" wording in the docs with what was actually tested
- [x] Retest on BrickSim's baseplate scene

## Universal tools and the UR5e twin

Kaleb's direction: a normal quick start for anyone; the `ur5e_dt__*` tools are his RISE lab UR5e work, clearly labeled; and all other tools are universal, so a UR5e owner is almost plug and play.

- [x] `sim__quick_start`: a working scene with nothing to supply, and no twin wiring
- [x] Plug-and-play check: `sim__quick_start` with the UR5e, then generic tools only
- [x] Label `ur5e_dt__*` as RISE lab's UR5e twin in docs and tool descriptions
- [x] README's first scene uses `sim__quick_start`
- [x] `ur5e_dt__quick_start` after `sim__ros2__tf_setup` must not silently create the `/joint_states` loop

## BrickSim requests

Built and reviewed, uncommitted until the gate passes; ticked once pushed.

- [x] **1. Named asset packs**, not hard coded: `<store>/<pack>/` chosen by `isaac-mcp.json` or an env var, defaulting to `ur5e-dt`
  - [x] `assets__*` tools: list packs, doctor a pack, create or adopt a pack, re-pin. No overwrite without force, nothing inside the repo, no tool approving folders
  - [x] Build a pack from a USD's dependency closure
  - [x] Doctor checks dependencies, not a folder listing, including unresolved ones
  - [ ] Update BrickSim's four `run_path` call sites to `from isaac_mcp import assets` (BrickSim's own change)
- [x] **2. Supported resolver import:** a stable stdlib-only module usable without Kit
- [x] **3. External apps in session tools:** registered launcher or attach, so `sim__list` and `sim__status` include external bridges
- [x] **4. Version and reload visibility:**
  - [x] Say clearly when a hot reload happened, and allow launching with reload off
  - [x] `status` reports the extension commit and dirty state, and warns when gateway and bridge differ
  - [x] Fix the `sim-tools` hot reload failure (`owner already registered`)
- [x] **5. ROS:** connect separate articulations with a dynamic parent-link attachment (`wrist_3_link -> rg2_base_link`)
- [ ] **6. Smaller fixes:**
  - [x] `sim__camera__capture` schema has `adopt`
  - [x] `sim__ros2__tf_setup` `extra_frames` accepts prim paths
  - [x] `sim__stage__open` no longer errors after switching on 5.1
  - [ ] `sim__simulation__step`: a way forward from `EDIT_TARGET_CHANGED` when Play came from elsewhere
  - [x] `sim__joints__drive`: `JOINT_LIMIT` at target 1.0 on `rg2_gripper_joint`
  - [x] `isaaclab__install_info`: per-project interpreter
  - [x] `sim__stage__save`: a size note for large flattened scenes
- [ ] **7. Nice to have:**
  - [ ] Documented example of a project shipping its own tools through the bridge
  - [ ] Start a recording before a window exists, or stop on a log marker

Keep as they are: `run_python`, `use_bridge` with `status`, `sim__ros2__tf_setup`, window-only recording, and the specific refusal codes.

## Open from earlier in PR 70

- [ ] Stale camera handles have no repair on open
- [ ] A saved copy can list a removed capture product in `RenderSettings.products`
- [ ] `assets__*` `STALE_TARGET` is checked on the host, so it lacks the outside-change explanation
- [ ] Kaleb moves aside the stale `checkpoints/*.usda` in the main checkout

## Before asking for review

- [ ] Local CI gate passes on the final commit, and GitHub CI is green
- [ ] Docs match behavior: `AGENTS.md`, tool references, generated arguments, skill, compatibility, verification record
- [ ] PR description is current

## Open after the 2026-11-04 push

Found by the fleet at `e658289`; not fixed yet:

- [x] `sim__map__occupancy` crashes: NVIDIA `getBuffer()` heap overrun on all 6.x builds; the tool no longer calls it. Older-build retest of the crash scenes pending
- [x] Non-finite physics after reopening: a collider overlapping the robot, not save or reopen. Documented
- [x] `/scan` on 6.0.0.0, and the config LiDAR on 6.1: both work with a target beyond the 1 m near range
- [ ] `start_ros_driver up` fails on a cold first launch and leaves a healthy driver; `status` always exits 0
- [x] `verify.sh` version gate fails on the standalone layout (needs the ASU retest)
- [ ] Host needs `python-xlib` when run from the Isaac venv
- [ ] Selection outline appears in `sim__camera__capture` after `sim__viewport__frame`, windowed
- [ ] URDF packs: `assets__import_urdf` has no pack route, the `package://` fix text is incomplete, and an unstable robot reads `status: ok`
- [ ] Approval helper silently keeps one of several roots
- [ ] A client exiting mid-recording leaves a tiny MP4
- [ ] Kit uploads crash minidumps to NVIDIA by default: decide whether `run.sh` turns this off, and document it

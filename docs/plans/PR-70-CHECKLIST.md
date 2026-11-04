# PR 70 working checklist

Kaleb's running list for PR 70, so nothing is lost as requests are added. Tick an item only after it is tested.
When PR 70 merges, move what is left to the roadmap and delete this file.

## Fleet and version testing

- [x] Install Isaac Sim 6.0.0.0, 6.0.0.1 and 6.1.0.0 on the ASU PC (started; confirm results)
- [ ] Parallel fleet on the ASU PC, using OpenCode Go DeepSeek V4.1 Flash, reporting to `<home>/isaac-mcp-test-logs/pr70-fleet/`:
  - [ ] 1 host, install and docs commands
  - [ ] 2 all generic scene tools and validators, 6.2 headless
  - [ ] 3 windowed and desktop tools, 6.2
  - [ ] 4 UR5e twin and asset system, 6.2
  - [ ] 5 ROS 2 and `/scan`, 6.2
  - [ ] 6 Isaac Sim 6.0.1
  - [ ] 7 Isaac Lab jobs
  - [ ] 8 managed sessions and multi-bridge
- [ ] Repeat the suite on 6.0.0.0, 6.0.0.1 and 6.1.0.0 once installed
- [ ] Fix what the fleet finds, then re-test those fixes
- [ ] Decide whether a build's 6.x API results differ: 6.0.0.0 lacks `renderProductPrim` and the `viewportHandle` early return in a static check, so test `/scan` there live

## Isaac Sim 5.1 (on this desktop)

- [x] Version adapters: engine, view, step, composition errors, drawable events, LiDAR config
- [x] `run.sh` finds 5.1's ROS libraries; the 5.1 build is recorded
- [x] Headless walk, `/scan` across a new Kit, `smoke_tools.py` 20/20, `verify.sh`
- [x] Windowed: Quick Start, panel, `sim__graph__show`, recording, UI tools
- [ ] `sim__launch` with a 5.1 interpreter; `install.sh` accepting a pinned 5.1
- [ ] Update `docs/COMPATIBILITY.md` and the "6.x" wording in the docs with what was actually tested
- [ ] Retest on BrickSim's baseplate scene

## BrickSim requests

- [ ] **1. Named asset packs**, not hard coded: `<store>/<pack>/` chosen by `isaac-mcp.json` or an env var, defaulting to `ur5e-dt`
  - [ ] `assets__*` tools: list packs, doctor a pack, create or adopt a pack, re-pin. No overwrite without force, nothing inside the repo, no tool approving folders
  - [ ] Build a pack from a USD's dependency closure
  - [ ] Doctor checks dependencies, not a folder listing, including unresolved ones
- [ ] **2. Supported resolver import:** a stable stdlib-only module usable without Kit
- [ ] **3. External apps in session tools:** registered launcher or attach, so `sim__list` and `sim__status` include external bridges
- [ ] **4. Version and reload visibility:**
  - [ ] Say clearly when a hot reload happened, and allow launching with reload off
  - [ ] `status` reports the extension commit and dirty state, and warns when gateway and bridge differ
  - [ ] Fix the `sim-tools` hot reload failure (`owner already registered`)
- [ ] **5. ROS:** connect separate articulations with a dynamic parent-link attachment (`wrist_3_link -> rg2_base_link`)
- [ ] **6. Smaller fixes:**
  - [x] `sim__camera__capture` schema has `adopt`
  - [x] `sim__ros2__tf_setup` `extra_frames` accepts prim paths
  - [x] `sim__stage__open` no longer errors after switching on 5.1
  - [ ] `sim__simulation__step`: a way forward from `EDIT_TARGET_CHANGED` when Play came from elsewhere
  - [ ] `sim__joints__drive`: `JOINT_LIMIT` at target 1.0 on `rg2_gripper_joint`
  - [ ] `isaaclab__install_info`: per-project interpreter
  - [ ] `sim__stage__save`: a size note for large flattened scenes
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

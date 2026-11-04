# PR 70 working checklist

Kaleb's running list for PR 70, so nothing is lost as requests are added. Tick an item only after it is tested.
When PR 70 merges, move what is left to the roadmap and delete this file.

## Fleet and version testing

- [x] Install Isaac Sim 6.0.0.0, 6.0.0.1 and 6.1.0.0 on the lab PC
- [x] ROS 2 on every machine, matching the OS: Humble system install on the lab PC, Jazzy on this desktop
Claude codes and reviews. DeepSeek V4.1 Flash on OpenCode Go scouts and tests, as a deliberately weaker user.

- [x] First round on the lab PC, reporting to `<home>/isaac-mcp-test-logs/pr70-fleet/`: host and docs, generic scene, windowed, UR5e twin, ROS 2, 6.0.1, Isaac Lab, sessions, then 6.0.0.0, 6.0.0.1 and 6.1.0.0
- [ ] Fix what the fleet found (in progress)
- [x] Re-test round on every build at the pushed commit, including goal-only briefs with no hand-holding
- [x] Run `validate_scene_equivalents.py` and `validate_scene_clear.py`, which need the Agilebot pack
- [ ] End-to-end asset pack test with public GitHub assets. First run: the build tool failed for the chess set. A pass means no workaround:
  - [x] `assets__pack_build` alone makes a pack that imports and renders with textures, with the source hidden
  - [x] The same for a real robot with physics: build, import, Play, and move a joint with universal tools
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
- [x] Stale `checkpoints/*.usda` moved aside in the main checkout, to `~/.cache/isaac-mcp-moved-checkpoints`

## Before asking for review

- [ ] Local CI gate passes on the final commit, and GitHub CI is green
- [ ] Docs match behavior: `AGENTS.md`, tool references, generated arguments, skill, compatibility, verification record
- [ ] PR description is current

## Open after the 2026-11-04 push

Found by the fleet at `e658289`; not fixed yet:

- [x] `sim__map__occupancy` crashes: NVIDIA `getBuffer()` heap overrun on all 6.x builds; the tool no longer calls it. 26 clean runs on the older builds
- [x] Non-finite physics after reopening: a collider overlapping the robot, not save or reopen. Documented
- [x] `/scan` on 6.0.0.0, and the config LiDAR on 6.1: both work with a target beyond the 1 m near range
- [x] `start_ros_driver up` on a cold first launch: passes on all five builds. `status` still always exits 0
- [x] `verify.sh` version gate fails on the standalone layout (needs a retest on the lab PC)
- [x] Host needs `python-xlib` when run from the Isaac venv: documented
- [x] Selection outline appears in `sim__camera__capture` after `sim__viewport__frame`, windowed: selection restored after framing
- [x] URDF packs: pack route, `package://` and holding drives, on 6.2 and 5.1 with Panda, UR5, xArm7, TIAGo and Go2. Go2 flips (zero pose outside limits); TIAGo's base does not drive. 6.0 and 6.1 not run
- [x] Approval helper silently keeps one of several roots: now refuses
- [x] A client exiting mid-recording leaves a tiny MP4: documented
- [x] Kit uploads crash minidumps to NVIDIA by default: off for `run.sh` and Lab jobs, `ISAAC_MCP_CRASH_UPLOAD=1` opts in
- [ ] Git history: Kaleb chose variant B. **Run last**, after the tool work, all builds, Lab setup and the merge. Plan: `~/.cache/isaac-mcp-history-scrub/PLAN.md`
- [ ] Windowed `sim__graph__show` on 6.0.0.1 logs a Kit `ZeroDivisionError` in its own zoom-to-fit, with the graph still fitted. Kit's code
- [ ] License: `mcp/` and the bridge derive from `omni-kit-mcp`, whose upstream has no license file. Kaleb is asking its author himself; update VENDORING, README and LICENSE when he reports the answer
- [ ] Public cleanup, **last, with the history rewrite**: Kaleb wants anything that shouldn't be public removed. Take out `docs/plans` (keep a local copy and fix its links), course names and "student run" wording, and the private strings in `scripts/check_private_paths.py`. "Kaleb's work with RISE lab" stays unless he says otherwise. List: memory `history-scrub-pending`
- [ ] Enable private vulnerability reporting on the repository, which SECURITY.md points to

## Tool-use fleet at `9803f4c`, 2026-11-04

14 agents (8 DeepSeek, 6 Gemini) worked from goals on all six builds and both Lab pairs. Every goal was reached, with no crash or hang. `[x]` means fixed in code and tested offline; **live** marks the ones also seen working in a simulator. Each stays open until the final retest.

### Fixed

- [x] `/clock` from the documented graph publishes zero stamps: example fixed, and `sim__graph__build` notes a clock with no time source. **live** (note)
- [x] `sim__lidar__create` claimed a `targetPrim` on 6.2 that no build has; now one route, including the exec wiring
- [x] `sim__joints__command` said `achieved: false` and blamed controllers when the arm was on the ground. **live**
- [x] `sim__quick_start` camera needed `adopt`, and lost a raised arm. **live**
- [x] Timeline results have no `paused`. **live**
- [x] `sim__stage__open` refused its own output folder when group-writable: my regression from the audit fix. **live**
- [x] Wrong-kind sensor told to retry with `adopt`, which could never work: now `NOT_A_SENSOR`. **live**
- [x] `ur5e_dt__quick_start` on a reused scene said "simulation running" while stopped: now plays, and reports `playing`. **live**
- [x] Twin quick starts return robot path, articulation root, camera and bench source; `add_objects` returns object names. **live**
- [x] Lab job that outlives its host reported `failed` even after succeeding: exit code recorded in the job folder. **live** on Lab 2.3.2 and 3.0: succeeded, failed, timeout and cancelled all held, with process trees gone
- [x] Lab `timeout_s` died with its host: the next `runs_status` or `runs_list` enforces it. **live** on Lab 2.3.2
- [x] `latest_checkpoint` picked the unplayable `exported/policy.pt`
- [x] `envs_list` empty on Lab 2.3: ids read from the checkout's source, marked `scanned`. **live**
- [x] `install_info` adds `rl_libraries` (what is really installed); its note names the package version plainly
- [x] rl_games `minibatch_size` docs wrong (`num_envs * 24`): the horizon varies by task
- [x] Refusals with no next step: `ROOT_NOT_APPROVED`, missing checkpoint, bad `extra_args`, bad job id, `randomize_single_object`, `UNKNOWN_NODE_TYPE` (now suggests the nearest), `INVALID_PARENT` (names the parent), `HEADLESS`, real-robot mode pointing at a disabled route
- [x] `NO_SHARED_STAGING` wrongly said a pack needs no staging; a URDF does
- [x] A `.urdf` holding xacro reported `USD_UNREADABLE`: now `XACRO`
- [x] `run.sh` could hang silently on the EULA prompt during its import check
- [x] Wording: `sim__simulation__step` observe arguments, `sim__joints__state` needs playing or paused, `sim__prim__create` ancestors, `sim__joints__drive` joint paths and stiffness, `sim__stage__save` extensions, Play not being real time, `assets__packs` host and Kit stores, `source: snapshot`, `/objects_poses_sim` format, `/scan` and `resetSimulationTimeOnStop`, `eval` gives no score, doctor's `package://` hint, `--field ranges`

### Open: to attempt

- [x] `sim__lidar__read` stuck on `NOT_READY` on 6.0.1: reproduced. A LiDAR left at the default origin sits inside a robot's base there. At 1.5 m on the same stage it reads, and with no robot at the origin it reads. The refusal now says so
- [x] The same on 6.0.0.0, 21 fresh Kits: `NOT_READY` means the scan is empty. The stuck run's 1 m cube, 0.5 m from a LiDAR at the default height, hid its wall inside the near range. `sim__stage__clear_owned` removes the ground, so a rigid wall falls out of the scan. No effect from save and reopen, pause and step, or read timing. The refusal now leads with this
- [x] **Driving a mobile base and Nav2**: merged. NVIDIA robots load by name and drive from `/cmd_vel`, and Nav2 reached a goal with Create 3 and TurtleBot3, on 6.2 and 5.1. Pictures and steps: `docs/evidence/public-robot-nav2-2026-11-04/`
- [x] Worked example on 6.0.0.0: Create 3 and TurtleBot3 (`--yaw-tolerance 3.2`) reached goals, TF clean, thin floor doesn't drive and thick does, reopened file publishes and drives
- [ ] Same on 6.0.0.1, 6.0.1 and 6.1: running. The first TF loop report came from 6.1
- [ ] On 6.0.0.0 a LiDAR read after building a scan graph on a tool-created sensor reported `NOT_OWNED`, then `NOT_READY` while playing. A sensor `sim__lidar__create` made should be registered. Check against the stuck LiDAR read
- [ ] `[spin] error in rcl_take: error not set` lines in the Kit log on 6.0.0.0: appears harmless, not explained
- [ ] Carter: drives, but Nav2 stopped 1.2 m short (`Failed to make progress`); not tuned. Nova Carter and Jetbot: drive, Nav2 not run
- [ ] TurtleBot3 stalls on the final heading without `--yaw-tolerance 3.2`, probably its box caster
- [ ] A very flat floor already saved in a file gets no warning: only `sim__prim__create` warns
- [ ] Why a floor scaled 20x20x0.1 gives wheels no contact is measured, not explained (133:1 fails, 100:1 drives on TurtleBot3; Carter erratic from 50:1). Limitation unless the cause turns up
- [x] 5.1 saves write an empty `/WriterOrchestrator` graph with no nodes. It survives a reopen, `/scan` publishes once, nothing breaks. `graphs` in the save result still lists it, which reads like a saved pipeline
- [ ] A collapsed body after reopening, seen once on 6.1, did not reproduce on 6.2 or 5.1. Cause unknown
- [ ] RViz not used in these runs: all headless
- [ ] **URDF pack needs one-time approval and staging**, unlike a USD pack. Decide: a default staging folder, or keep it and document
- [x] Windowed 5.1: first `sim__quick_start` failed twice earlier with no error. 5 of 5 pass windowed and 5 of 5 headless, so not reproduced. A returned `ok` can precede the first rendered frame: one screenshot 4 s later was black, and 13 s later showed the arm
- [ ] Refusals check state before type: `joints__state` on a Cube says "play first", then the real fault. Same for `steps=0`
- [ ] `NO_SHARED_STAGING` comes before validating a root or path, so a typo reads as an unconfigured install
- [ ] Range refusals show the value but not the argument name
- [ ] A guessed argument gets `Extra inputs are not permitted` with no list of real ones; NaN shows as `None`
- [ ] `sim__light__modify` requires both intensity and color
- [ ] `sim__map__occupancy` requires `min` and `max`; a default from colliders needs care with a ground plane
- [ ] `assets__pack_doctor` doesn't list a pack's files
- [ ] `start_ros_driver backend=ursim status` returns success with Docker missing; its status text can contradict itself
- [ ] `set_object_pose` and `sync_real_poses` report the requested pose, not where physics left it
- [ ] Lab: `rl_library` isn't checked against `rl_libraries` before launch: `skrl` on Lab 3 fails after about 80 s
- [ ] Lab: a task id isn't checked before a roughly 50 s launch; a bad task with no library first blames `--rl_library`
- [ ] Lab: tqdm bars fill `last_log_lines` for skrl and sb3
- [ ] Lab: `runs_status.window` was missing on the lab PC, present on the desktop
- [ ] Lab: `--video` without MoviePy fails only after launch
- [ ] Docs say `Example_Rotary_2D` is 10 Hz; 5.1 reports 30
- [ ] `sim__lidar__read`'s 256 samples are the start of the buffer, not a whole sweep: say so on `limit`
- [ ] For LEGO and Jenga: mass or density authoring, an arm with a gripper in `sim__quick_start`, a route from a saved scene to a Lab task

### Known limitations, to state in the docs

- [ ] `isaaclab__eval` is Lab's `play.py`: it runs until stopped and gives no score
- [ ] `--video` needs MoviePy and imageio-ffmpeg in the Lab environment
- [ ] A screen recording belongs to the server process that started it, so a client that restarts between calls can't stop it
- [ ] `timeout_s` with no host running waits until the next status call
- [ ] Real-robot mode is not enabled; `ursim` needs Docker
- [ ] `randomize_single_object` often can't fit: a 4 cm by 20 cm strip with 0.18 m separation, not configurable
- [ ] `sim__launch` suggests a free port, which may belong to another agent on a shared machine: it can't know

### Windowed coverage

Fleet runs at `9803f4c` were almost all headless, so a pass there doesn't cover a window. Windowed runs started at `8336c4c` on every build.

- [x] Windowed 6.1 (Gemini): UR5e, falling box, viewport, save and reopen in a new windowed Kit, `sim__graph__show`, a 62-frame recording, `create3` and `turtlebot3_burger`. No `run_python`
- [ ] Windowed: the same on 5.1, 6.0.0.0, 6.0.0.1, 6.0.1 and 6.2, plus `sim__ui__*`
- [ ] Windowed Isaac Lab `play` on both Lab pairs
- [ ] RViz against a running scene

### Parallel work

Five builds at once plus one started by hand, on the lab PC, with other agents running. Same scene fired concurrently in all five took 29 s. No crossover: distinct session and stage IDs, each domain saw only its own one `/scan` publisher, each output folder only its own files. Two clients on one bridge queue. A SIGKILL left the others running, and discovery dropped the dead one. About 7 to 7.5 GB of GPU per Kit with a UR5e scene. Start time did not rise with the count.

- [x] Parallel instances: no crossover or crash across 6.0.0.0, 6.0.0.1, 6.0.1, 6.1 and 6.2
- [ ] **`sim__stop` reports success while Kit keeps running on 6.0.1**: its `python.sh` doesn't `exec`, so `sim__launch` records the bash wrapper. `sim__status` then says `port_conflict`, and the simulator is listed twice. Fix after the port work merges, since it's the same file
- [ ] `DOORWAY_BUSY` never reaches an MCP caller: a queued call only sees a client timeout, with nothing about what it's waiting for
- [ ] A SIGKILL mid-call gives `Connection reset by peer`, without naming the tool or saying the simulator is gone
- [ ] `ISAAC_MCP_SIM_MAX_INSTANCES` counts the whole machine, and its refusal doesn't say `0` disables it
- [x] Windowed 5.1 (DeepSeek): capture, screenshot, viewport, `sim__graph__show`, a 141-frame recording, LiDAR and `/scan`, reopen, `create3` drove 1.38 m. No `run_python`
- [x] Regression from the exit-code wrapper: the recorded pid became the shell, so `runs_status` lost `window`, and cancel could report done with Kit still running. Shell now outlasts the signal; windows match the job's process group
- [x] Windowed `play` and cancel on Lab 2.3.2: `window` reported, and after a full load Kit ignored SIGTERM, so cancel escalated at 10 s and left nothing, twice
- [ ] The same on Lab 3.0.0


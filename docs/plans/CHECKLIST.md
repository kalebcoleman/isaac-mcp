# Working checklist

Kaleb's running list. It holds only what is still to do; finished work is in the git history and the merged PRs
(PR 70 merged 2026-11-04, README PRs 71 and 72). Tick an item only after it is tested. Add every new issue here
so nothing is lost.

Claude codes and reviews. Testers are weaker models given short plain requests; what they fumble is the finding.

## 1. Speed: build many things in few calls (priority)

A 36-block tower took **43 creates and 72 material calls**, over 110 in all. Each call is a round trip, so
building anything with many parts is slow and costly. **Goal: that tower in 5 calls or fewer**, with the same result
(mass on every block, stands 10 s, same height).

- [ ] Measure first: calls and wall time per call for the tower, and the other repeated patterns in earlier runs
- [ ] `material` (and `color`) option on `sim__prim__create`, so a shape and its look are one call
- [ ] A batch create: one call takes a list of shapes (kind, pose, size, scale, mass, material) and returns the list
- [ ] `sim__prim__clone` with a count and an offset, a grid or a stack, so repeats cost one call (check what clone does today)
- [ ] Batch material apply (a list of paths) and batch transforms
- [ ] One undo and one render settle per batch, not per item; a failed item names itself and says what was kept
- [ ] Test: the tower in 5 calls or fewer, in a unit test and live on 6.2; record the before and after call counts here
- [ ] Look for other N-call patterns (joints, physics apply, lights) in the earlier test transcripts and fix the worst

## 2. Poke a running scene

Authoring needs a stopped timeline, and Stop rewinds physics, so an agent could not nudge a block while playing.
Jenga, LEGO and manipulation need it.

- [ ] A tool that applies a force, an impulse, or sets a velocity on a body while playing or paused
- [ ] Works on PhysX; says plainly what it does on Newton (experimental) rather than guessing
- [ ] Test: nudge the top block of the tower and watch it move; pull a block out of a layer and see what falls
- [ ] Docs and the example prompts in the README

## 3. Second PR: M6 and M7 (branched from `main`)

- [ ] M6: `assets__robot_library`, `assets__import_mjcf`, `bundled` and `nvidia` sources
- [ ] M7: `sim__viewport__screenshot`, `sim__viewport__record_start`, `sim__viewport__record_stop`, and the final demo
- [ ] For LEGO and Jenga: an arm with a gripper in `sim__quick_start`, and a route from a saved scene to a Lab task
- [ ] Update `docs/TOOLS-*.md`, the counts, and regenerate the catalog and tool reference (`check_docs.py` enforces this)

## 4. Small fixes

Host layer and refusals
- [ ] A queued call that times out should say what the bridge is running (do it in the host layer's error formatting; the client attempt broke 11 tests)
- [ ] A SIGKILL mid-call should name the tool and say the effects are unknown (host layer, same reason)
- [ ] `ISAAC_MCP_SIM_MAX_INSTANCES` counts the whole machine, and its refusal doesn't say `0` disables it
- [ ] Refusals check state before type: `joints__state` on a Cube says "play first", then the real fault; same for `steps=0`
- [ ] `NO_SHARED_STAGING` comes before validating a root or path, so a typo reads as an unconfigured install
- [ ] Range refusals show the value but not the argument name; NaN shows as `None`
- [ ] A guessed argument gets `Extra inputs are not permitted` with no list of the real ones
- [ ] `assets__*` `STALE_TARGET` is checked on the host, so it lacks the outside-change explanation

Tools
- [ ] `sim__light__modify` requires both intensity and color
- [ ] `sim__map__occupancy` requires `min` and `max`; a default from colliders needs care with a ground plane
- [ ] `sim__logs__tail` reads the last 32 KiB (about 2 s of Info lines): add level and text filtering
- [ ] `sim__body__state` refuses while stopped, so "stop then read the pose" fails
- [ ] `assets__pack_doctor` doesn't list a pack's files
- [ ] `sim__lidar__read`: its 256 samples are the start of the buffer, not a whole sweep; say so on `limit`
- [ ] `sim__lidar__read` `NOT_READY` text should mention a sensor inside its own robot's body (Carter's was on a chassis link)
- [ ] `clock=auto` reuses any existing clock, even one with no time source
- [ ] A twin tool (`ur5e_dt__set_object_pose`) tried on a non-twin object fails with a path nobody expects: its description should say it only works on the twin's objects
- [ ] A very flat floor already saved in a file gets no warning; only `sim__prim__create` warns
- [ ] A window appears 8 to 12 s before it renders: consider a `loading` field on `runs_status`
- [ ] On 6.0.0.0, a LiDAR read after building a scan graph on a tool-created sensor reported `NOT_OWNED`, then `NOT_READY` while playing: a sensor `sim__lidar__create` made should be registered
- [ ] Stale camera handles have no repair on open (only `RemoveProperty` from `run_python` worked); both documented repairs fail

Docs and guides
- [ ] Trim the guide's `--yaw-tolerance 3.2` advice to the measured 0.2 to 0.5
- [ ] State that `num_episodes` is a floor: all parallel environments finish once first
- [ ] Say in the guide that `isaac_assets.py adopt` (no approval) is the route for an agent building a pack, if true for a URDF with meshes
- [ ] Docs say `Example_Rotary_2D` is 10 Hz; 5.1 reports 30
- [ ] A documented example of a project shipping its own tools through the bridge
- [ ] Say that `sim__camera__capture` made in a session loses its render handles across save and reopen (`NOT_OWNED`, fixed by `adopt=true`); keep the refusal's `retry_with`

Lab
- [ ] `runs_status.window` was missing on the lab PC, present on the desktop
- [ ] Two windowed jobs have the same title, so only id and pid tell them apart
- [ ] A window still loading is black for up to 4 minutes when two Kits share a GPU, with no signal
- [ ] `timeout_s` with no host running waits until the next status call

Map and Nav2
- [ ] Map rasterising: a collider marks every cell it touches, so a thin wall can show a cell wider
- [ ] With a robot on the origin, pass `start` on open floor; `nav2_params.py` needs ROS sourced

Optional
- [ ] Start a recording before a window exists, or stop on a log marker

## 5. Testing still owed

- [ ] **Lab PC, when Kaleb says it is back** (it was down, shared workstation): 6.0.0.0, 6.0.0.1, 6.0.1 and 6.1 with the smoke test, M1 to M5 and the plain-request tests; Lab 3.0.0 with the eval fix and the refusal hints
- [ ] 6.0.1 `sim__stop` fix, live (stop must leave nothing running)
- [ ] Lab 3.0.0: windowed `play` cancel after a full load, and the `window` field
- [ ] M1 to M5 on 6.0.0.0, 6.0.0.1, 6.0.1, 6.1 and Newton: only the API adapters and unit tests cover them
- [ ] A **windowed** plain-request run on 6.2 with the viewport and capture tools (home runs so far were headless)
- [ ] Have a plain-request tester use the new tools (tower, measure, raycast, poke) and fix what it fumbles
- [ ] Mark Lab 2.3.2 with Sim 5.1 `tested: true` in the compatibility matrix (a small PR)
- [ ] Re-run the README example prompts, including the LiDAR one, and fix any that fail
- [ ] Merged M1 to M5 with the timeline playing: `sim__joints__move` was only checked after Stop

## 6. Known limitations to state in the docs

- [ ] `--video` needs MoviePy and imageio-ffmpeg in the Lab environment
- [ ] A screen recording belongs to the server process that started it, so a client that restarts between calls can't stop it
- [ ] Real-robot mode is not enabled; `ursim` needs Docker
- [ ] `randomize_single_object` often can't fit: a 4 cm by 20 cm strip with 0.18 m separation, not configurable
- [ ] Why a floor scaled 20x20x0.1 gives wheels no contact is measured, not explained (133:1 fails, 100:1 drives on TurtleBot3; Carter erratic from 50:1)
- [ ] `[spin] error in rcl_take: error not set` lines in the Kit log on 6.0.0.0: appears harmless, not explained
- [ ] One `sim__camera__capture` had the selection outline in it after a prim was selected by hand; not reproduced in 4 tries

## 7. For Kaleb to decide

- [ ] **Keep, combine or remove review of the tool surface.** Nothing is removed or combined without Kaleb's review. Bring a list
- [ ] Twin tool proposals from `SCENE-TOOLS-AGENT-UX.md` that are still unbuilt (a scout is to list them)
- [ ] Aldrin licence: Kaleb handles it. Update VENDORING, README and LICENSE only when he reports; do not ask again

## 8. Last of all

Run only after the tool work, all builds, Lab setup and merges, and only with Kaleb's go-ahead.

- [ ] Git history: variant B rewrite (plan `~/.cache/isaac-mcp-history-scrub/PLAN.md`)
- [ ] Public cleanup with it: remove `docs/plans` (keep a local copy and fix its links), course names and "student run" wording, the private strings in `scripts/check_private_paths.py`, and the remote branch `fix/student-run3`. "Kaleb's work with RISE lab" stays
- [ ] Clear PR edit history in the browser (it can't be done from the API), and delete `/tmp/scrub/`

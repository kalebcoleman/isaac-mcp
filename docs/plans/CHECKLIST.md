# Working checklist

Kaleb's running list. It holds only what is still to do; finished work is in the git history and the merged PRs
(PR 70 merged 2026-11-04, README PRs 71 and 72). Tick an item only after it is tested. Add every new issue here
so nothing is lost.

Claude codes and reviews. Testers are weaker models given short plain requests; what they fumble is the finding.

## 1. Speed: build many things in few calls (priority)

A 36-block tower took **43 creates and 72 material calls**, over 110 in all. Each call is a round trip, so
building anything with many parts is slow and costly. **Goal: that tower in 5 calls or fewer**, with the same result
(mass on every block, stands 10 s, same height).

- [x] Measured: the tower was 43 creates, 72 material binds and about 4 setup calls
- [x] `material` option on `sim__prim__create`: a shape and its look in one call; if the material can't be bound nothing is created (a bare `color` option was left out: make a material once with `sim__material__create` and share it)
- [x] `sim__prim__create_many`: up to 200 shapes in one call, all or none, with `defaults` for shared keys (skipped for an Xform or Plane where they don't apply) and the error naming the failed item
- [x] `sim__material__apply` takes `paths` (up to 128), checked before any is bound
- [x] A failed batch removes everything it made and keeps the original error's code and details; tested offline and live
- [x] Test: the tower, including its Xform parent, in one call (5 calls with setup, from over 110); live on 6.2 it stands after 10 s with the top block 1 mm below its authored height
- [ ] `sim__prim__clone` with a count and an offset, a grid or a stack (check what clone does today)
- [ ] Batch transforms (move many prims in one call)
- [ ] Look for other N-call patterns (joints, physics apply, lights) in the earlier test transcripts and fix the worst
- [ ] Have a plain-request tester build something with many parts and confirm it picks `sim__prim__create_many`

## 2. Tool surface review: combine or remove

131 tools is a lot for an agent to choose from, and some do nearly the same job. Review all of them, and merge or
drop the ones that earn no place. **Nothing is removed or combined until Kaleb has approved the list**; Kaleb said the
review itself is fine to do, and to land as its own branch.

- [ ] **Inventory.** One table of every tool: group, what it does, the tools it overlaps, and how often agents used it in the fleet and plain-request runs (count calls from the saved transcripts, not by guess)
- [ ] **Classify** each as keep, combine, rename, or remove, with a one-line reason and a risk: who might call it (README prompts, the skill, the UR5e twin, the docs, other repos)
- [ ] **Bring the list to Kaleb.** He approves, changes or rejects each line before any code moves
- [ ] **Implement the approved list** on its own branch: a merged tool keeps the old name as a thin alias for one release with a note in its description, and a removed one is documented
- [ ] Update everything the counts touch: `AGENTS.md`, the README tier table, the skill, `docs/TOOLS-*.md`, the generated catalog and tool reference (`check_docs.py` and `gen_host_catalog.py --write` enforce this)
- [ ] Re-run a plain-request tester afterwards to check an agent does at least as well with the smaller set (fewer wrong tool picks, same or fewer calls)

Candidates to look at first (not decisions): the three step/play/pause paths (`sim__simulation__step`, `sim__timeline__control`, `sim__joints__move`); the joint family (`state`, `command`, `drive`, `gains`, `move`, `teleport`, `inspect`); the pack tools (`pack_adopt`, `pack_build`, `pack_pin`, `pack_doctor`, `packs`); the camera pair and the many `ur5e_dt__quick_start*` variants; `sim__prim__get`, `bounds` and `measure`; the desktop `window_*` tools.

### Twin tools (`ur5e_dt__*`, 23 of them)

The plan in `SCENE-TOOLS-AGENT-UX.md` is to make the lab twin's tools predictable for an agent, without breaking the lab's
current use. In short:

- Each tool says exactly what it did: what it created, what it skipped, what was left half-done, how long it waited. Today most return only "success" and a message, and a few descriptions promise fields the code doesn't return (`save_scene_state` and `restore_scene_state`)
- Prerequisites and side effects are written down (for example `add_objects` stops and replays the timeline; `new_stage` and `delete_objects` destroy things without a confirmation)
- One `setup_scene(workstation, simple_room, ...)` replaces the pile of `quick_start*` combinations, which stay as wrappers
- Destructive tools ask for `confirm=true`, as the generic `sim__prim__delete` does
- Parameter descriptions and read-only/destructive hints reach the agent
- Long calls report their real waits and, on timeout, say the outcome is unknown rather than failing silently
- Part of the plan is already done by the generic tools (`sim__stage__info`, `sim__stage__tree`, `sim__prim__get` now inspect any scene), so the twin does not need its own inspector

- [ ] Scout re-checks the plan against today's code and lists which proposals are still unbuilt
- [ ] Fold the twin tools into the review above: decide which `quick_start*` variants stay
- [ ] Then build what is left, in the plan's order: descriptions and recipes first, honest outcomes next, setup and confirmations after

## 3. Poke a running scene

Authoring needs a stopped timeline, and Stop rewinds physics, so an agent could not nudge a block while playing.
Jenga, LEGO and manipulation need it.

- [ ] A tool that applies a force, an impulse, or sets a velocity on a body while playing or paused
- [ ] Works on PhysX; says plainly what it does on Newton (experimental) rather than guessing
- [ ] Test: nudge the top block of the tower and watch it move; pull a block out of a layer and see what falls
- [ ] Docs and the example prompts in the README

## 4. Second PR: M6 and M7 (branched from `main`)

- [ ] M6: `assets__robot_library`, `assets__import_mjcf`, `bundled` and `nvidia` sources
- [ ] M7: `sim__viewport__screenshot`, `sim__viewport__record_start`, `sim__viewport__record_stop`, and the final demo
- [ ] For LEGO and Jenga: an arm with a gripper in `sim__quick_start`, and a route from a saved scene to a Lab task
- [ ] Update `docs/TOOLS-*.md`, the counts, and regenerate the catalog and tool reference (`check_docs.py` enforces this)

## 5. Small fixes

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

## 6. Testing still owed

- [ ] **Lab PC, when Kaleb says it is back** (it was down, shared workstation): 6.0.0.0, 6.0.0.1, 6.0.1 and 6.1 with the smoke test, M1 to M5 and the plain-request tests; Lab 3.0.0 with the eval fix and the refusal hints
- [ ] 6.0.1 `sim__stop` fix, live (stop must leave nothing running)
- [ ] Lab 3.0.0: windowed `play` cancel after a full load, and the `window` field
- [ ] M1 to M5 on 6.0.0.0, 6.0.0.1, 6.0.1, 6.1 and Newton: only the API adapters and unit tests cover them
- [ ] A **windowed** plain-request run on 6.2 with the viewport and capture tools (home runs so far were headless)
- [ ] Have a plain-request tester use the new tools (tower, measure, raycast, poke) and fix what it fumbles
- [ ] Mark Lab 2.3.2 with Sim 5.1 `tested: true` in the compatibility matrix (a small PR)
- [ ] Re-run the README example prompts, including the LiDAR one, and fix any that fail
- [ ] Merged M1 to M5 with the timeline playing: `sim__joints__move` was only checked after Stop

## 7. Known limitations to state in the docs

- [ ] `--video` needs MoviePy and imageio-ffmpeg in the Lab environment
- [ ] A screen recording belongs to the server process that started it, so a client that restarts between calls can't stop it
- [ ] Real-robot mode is not enabled; `ursim` needs Docker
- [ ] `randomize_single_object` often can't fit: a 4 cm by 20 cm strip with 0.18 m separation, not configurable
- [ ] Why a floor scaled 20x20x0.1 gives wheels no contact is measured, not explained (133:1 fails, 100:1 drives on TurtleBot3; Carter erratic from 50:1)
- [ ] `[spin] error in rcl_take: error not set` lines in the Kit log on 6.0.0.0: appears harmless, not explained
- [ ] One `sim__camera__capture` had the selection outline in it after a prim was selected by hand; not reproduced in 4 tries

## 8. For Kaleb to decide

- [ ] Approve, change or reject the keep/combine/remove list when it arrives (section 2)
- [ ] Aldrin licence: Kaleb handles it. Update VENDORING, README and LICENSE only when he reports; do not ask again

## 9. Last of all

Run only after the tool work, all builds, Lab setup and merges, and only with Kaleb's go-ahead.

- [ ] Git history: variant B rewrite (plan `~/.cache/isaac-mcp-history-scrub/PLAN.md`)
- [ ] Public cleanup with it: remove `docs/plans` (keep a local copy and fix its links), course names and "student run" wording, the private strings in `scripts/check_private_paths.py`, and the remote branch `fix/student-run3`. "Kaleb's work with RISE lab" stays
- [ ] Clear PR edit history in the browser (it can't be done from the API), and delete `/tmp/scrub/`

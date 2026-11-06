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

## 2. Foundation: assets, projects and the workspace (in progress)

Goal: one clean layout the user controls, with the same tools working for three kinds of people. Acceptance cases to keep green:
a fresh clone (Franka pick-and-place; a public UR5e with a public gripper pick-and-place), a user with their own robots and
assets in a project folder, and the lab user with the full UR5e twin and Vention workcell on both of their PCs.

```text
<workspace>/
  isaac-mcp/          the checkout: code only (this repository)
  isaac-mcp-assets/   local assets: packs, the lab twin files; never pushed
  projects/<name>/    each project, some with their own git remote, each may hold an isaac-mcp.json
  runs/  envs/        outputs, and Isaac Sim / Isaac Lab Python environments
```

Found on 2026-11-05 by scripted scenarios (17 resolver checks in throwaway worlds, all passed) and live runs on Isaac Sim 6.2:
- [x] Resolver basics hold: a fresh clone's default store is next to the checkout; `ISAAC_MCP_ASSET_PATH` replaces the defaults; a project file is found by walking up, relative `asset_path` is relative to the file, `ISAAC_MCP_PROJECT` wins; strict precedence (env 0 beats project true); bad project files are errors that name the file; a changed pinned file fails `doctor`; reading never creates the asset folder
- [x] A pinned pack in the user's asset folder loads with no approval (default staging); a trusted root, once approved, loads by path and adopts into a pack with no restart
- [x] The lab asset folder is complete: `doctor` checked 170 files, the only lab-supplied file the manifest names (`workstation/vention.usd`) is present; `sensors/rsd455.usd` lives under `Isaac/Sensors/Intel/RealSense/` as the manifest says (an earlier report that it was missing was a wrong check)
- [ ] **F1. A robot an agent puts in the project's own `./assets` cannot be loaded**: "A pack named only by a project file's asset_path is not an approval". By design (a cloned repo could name any folder), but it blocks the flow of an agent creating an assets or downloads folder in the project. Fixed by workspace trust below
- [x] **F2. Refusals do not tell the agent the exact fix.** `ROOT_NOT_APPROVED` and the asset-path message should name the folder and give the filled-in command the user runs (`python -m isaac_mcp.project_assets --root-id ... --root <folder>`), and say that no restart is needed if that is true
- [x] **F3. Kit launched the usual way never reads the project's `isaac-mcp.json`; the host does.** The same `sim__quick_start robot="ur5e"` is refused when Kit knows the project (override not approved) and quietly loads NVIDIA's public UR5e when it does not. Fix: the host passes the project to Kit when it launches one; `status` reports the project file Kit sees; `assets__packs` warns when host and Kit disagree
- [ ] **F4. `assets__pack_adopt` and `pack_build` always write to the one primary asset folder**, so an agent in project A can add packs to the shared folder, and a project cannot choose its own write location. Decide: a project-level write folder, or document it
- [ ] **F5. Global strict mode blocks public robots and grippers for every tool.** Scope it to the twin (lab profile PR)
- [ ] **F6. The printed approval command must run as shown.** Found by running it (2026-11-05): the helper exits without `--trust-project` and needs `--staging` while it creates the file, and the first F2 message had neither. Fixed in PR 1 with a test that runs the printed command through the helper. Re-check it whenever the helper's flags change
- [x] **F7. Approving a large parent folder breaks every import.** An import copies the whole approved folder (at most 1000 files or 256 MiB), so with `<workspace>/projects` approved a few-hundred-byte URDF fails with `LIMIT` (live, 2026-11-05). Approving only the robot folder worked end to end. Decision (Kaleb): do not approve the whole projects folder. PR 1 now advises the smallest folder, and PR 3 approves only what each project needs (its assets folder), never `projects/` as a whole
- [ ] **F8. The store list names the same folder twice** when a project's `asset_path` lists the workspace store that the defaults already add (`assets__packs` and `sim__stage__info` `stores`). Dedupe, keeping the first position
- [x] **F9. Approval does not stop an agent that has a shell.** Tester 1 (OpenCode Pulse Alpha Free, 2026-11-05) `cp`'d a cloned repo into the asset folder and built a pack from it with no approval, as designed: the asset folder is trusted. Decision (Kaleb): keep approval for paths outside the folders, and add provenance. Done in PR 1: `assets__pack_build origin` is stored in the pack file and listed by `assets__packs`; docs and the skill say to use sources the user names, keep downloads in `downloads/`, and never run downloaded code. Still to decide: whether `pack_adopt` should record a source as well
- [x] **F10. A named humanoid started half inside the floor.** Found by Kaleb on the first live load of `unitree_h1` (pelvis is the origin). `sim__quick_start` now lifts the named humanoids onto the ground (H1 1.049 m, G1 0.798 m; any first transform op), and only them. Added `unitree_h1` and `unitree_g1` as public names (tester wish W4). The first version lifted every named robot; a live check after review round 1 showed `carter_v1` raised 1,464 m and `nova_carter` 50 m (stray geometry in their USD), so the lift is opt-in per robot (`lift_to_ground`) and capped at 3 m, and every other named robot was re-checked live as unmoved
- [x] **F11. `sim__launch` said `id`, `sim__status` and `sim__stop` want `session_id`** (tester 1 hit the validation error). Results now carry both. `start_screen_recording` now says a recording ends if the MCP server process exits (tester 1 lost one and wrote its own driver)
- [ ] Wait for more evidence: a settle or `after_command` option for `sim__camera__capture` (tester 1 saw one capture look neutral after an arm command; not proven), and a `probe_video` tool (the bundled ffmpeg already works; the tester only lacked it on its PATH)
- [x] Tester 2 (OpenCode Pulse Alpha Free, 2026-11-05, PR 81 head `012efc8`): trained `Isaac-Velocity-Flat-G1` in Isaac Lab 3.0.0 on Sim 6.2.0.0, 1500 iterations in 8.6 minutes; mean episode length 14 to 948 of 1000, eval mean return 27.25 over 16 episodes, about 7% falls, and a 45 s window video of it walking. One run, one seed. Exact prompts, numbers and the verification are in [evidence/pr81-agent-tests-2026-11-05.md](../evidence/pr81-agent-tests-2026-11-05.md)
- [x] **F12. The walking task trained and played on Newton MJWarp, and nothing in the tools said so.** The task config picks its own engine (the log banner says `Physics newton_mjwarp`). `isaaclab__runs_status` now returns `physics` and `environment_ready` (the setup line was in the log but pushed out of `last_log_lines`)
- [x] **F17. The printed approval command broke on a path with a space, and said `--root-id None` with no folder** (found while preparing review round 1). Paths are now printed as one shell word, tested by running the printed command through the helper with spaces in every path
- [x] **F18. Review round 1 (Claude Sonnet, medium) findings, all reproduced and fixed:** a folder named `robots v1.2` or `repo.git` was treated as a file, so the command approved its parent; a root id containing a dot or over 48 characters was rejected by the helper, so the printed command failed; a stray comma after `--trust-project`; `_raise_prim` dropped the reset-stack flag. The command is now on its own line, and tests run each printed command through the helper
- [x] **F19. Review round 2 finding, reproduced and fixed:** the shell-quoting helper left any value starting with `<` unquoted (a shortcut for the two placeholders), so a folder or config named like `<x; touch /tmp/PWNED #` would print a command that runs something else. Only the two exact placeholders are bare now, and a test feeds hostile values (`;`, `$()`, backticks, quotes, newlines) through and checks each stays one shell word. Minor notes taken: more file suffixes (`.glb`, `.gltf`, `.fbx`, `.sdf`, `.ply`, `.hdr`, `.exr`, `.csv` and others) and a unit on the lift warning. Not covered by a unit test: the `quick_start` wiring (it needs Kit), which was checked live on all eight named robots
- [x] **F20. Wishlist-2 and -3 testers (OpenCode Pulse Alpha Free, 2026-11-05, main `640cbe9`): robots from three named GitHub repos, and a robot driven from ROS 2.** Results, prompts and verification are in `~/isaac-workspace/runs/wishlist-2-tests/`: all three robots loaded and moved (Go2, UR5e via a self-written URDF, TurtleBot3 via a self-stripped xacro), and a Create 3 drove from `/cmd_vel` with a Nav2 goal reached (live `/odom` after: (2.290, -0.028) for a goal at (2.2, 0))
- [x] **F21. The approval command offered `<package>/urdf` for a ROS-package URDF, but its meshes live above it.** Found by tester A on the Go2 and TurtleBot3, reproduced. The command now approves the nearest folder with a `package.xml` (never the home folder or above), or a `urdf` folder's parent when it sits beside a meshes folder
- [x] **F22. The approval command was offered for any path, including `/etc`.** Found while testing hostile files. No command is offered now for a system folder, the home folder itself, or `~/.ssh`, `~/.config` and similar private folders (symlinks are judged by where they point)
- [x] **F23. Documentation gaps from the testers, fixed:** running `call.py` or any `uv` program from a shell where `ros_env.sh` was sourced crashes on a RoboStack `libssl` import (reproduced; now in `ROS2-RVIZ.md` and the `ros_env.sh` header); `nav2_params.py`'s "not found" message now names the pipeline and subshell cause (the tester sourced the script inside a pipeline, which the docs already warned about); the URDF limits say how to drive a wheel by speed; `assets__import_urdf` points to `assets__dependencies` as the free pre-check
- [x] **F24. `scripts/odom_logger.py`** (tester B wrote the same 40-line logger twice): logs `/odom` to a CSV and prints start, end, distance, path and yaw change
- [x] **F27. PR 82 review round 1 (Claude Sonnet, medium) found two blockers in the MJCF resolver, both reproduced and fixed, plus two smaller ones.** (1) A `<compiler meshdir>` set in an included file was recorded and never checked (`../../../etc` or `/etc` passed), and the first folder won where MuJoCo's last one does; (2) an include not named `.xml` was never scanned, so `<mesh file="/etc/passwd"/>` in an `.txt` include passed. The resolver now walks the model in document order with includes expanded in place, checks every `<compiler>` folder wherever it appears (even one that is overridden), scans every included file whatever its name, follows `<model>` sub-models against their own folder, and returns the parsed trees to Kit. Also: an included file may have a `<mujocoinclude>` root; `~/.local` and `~/.cache` are ordinary project homes (only `.ssh`, `.config` and similar are refused); the `package.xml` check is exact and case-insensitive. Both attack files are refused on a real Kit, and the five real imports are unchanged
- [ ] **F25. Expert-level asset coverage, with the tools we already have (Kaleb, 2026-11-05).** Plan and research are in [ASSET-FORMATS.md](ASSET-FORMATS.md); the items are F28 to F41 below. Rules for all of it: **no new tools**; every format is an option on a tool that exists, so an agent finds one entry point and switches format at will; every importer keeps the host-side checks (nothing downloaded is ever run, everything stays inside the approved folder, a verified snapshot is imported). PR 82 (MJCF, merged `ab7e233`) is the foundation
- [ ] **F28. One import entry for every format.** `assets__import_path` becomes the universal importer: `format` defaults to `auto` (read from the extension and the first bytes: USD/USDA/USDC/USDZ, URDF, MJCF, xacro, SDF, glTF/GLB/OBJ/STL/FBX/DAE, a model folder) and can be set to override. It returns the same ready summary for every format (articulation root, movable joints with limits, bounds, what the converter dropped). `assets__import_usd`, `assets__import_urdf` and `assets__import_mjcf` keep working as thin aliases; folding them away is part of the section 3 review and needs Kaleb's approval first
- [ ] **F29. Safe xacro expansion** (tester C, wishlist-3: the UR10e is xacro only; six calls, and a near-miss with the system `ur_description`). Option on the same import: `xacro_args` such as `{"ur_type": "ur10e"}`. A restricted expander on the host (properties, args, macros, `$(find pkg)` limited to the approved folder, arithmetic through a whitelist), never the `xacro` program and never `python:` or `eval` of arbitrary code. A refusal names what it could not expand
- [ ] **F30. Mesh and prop import** (tester C: the Khronos Duck had no route at all, because the import is USD only). glTF, GLB, OBJ, STL, FBX, DAE, PLY through `omni.kit.asset_converter` behind the same entry; options `scale`, `up_axis`, `as_collider` (none, convex hull, convex decomposition), `mass`. Units and up axis are stated in the result, never guessed silently
- [ ] **F31. SDF (Gazebo) import** (tester C: the blue vehicle had to be rebuilt by hand from primitives, with no joints). There is no native importer, so write a restricted SDF reader on the host: links, inertials, visuals and collisions (box, sphere, cylinder, capsule, mesh), joints (fixed, revolute, prismatic, continuous, ball), `<include>` and `model://` resolved inside the approved folder, worlds with several models. Output goes through the same checked-USD path as URDF
- [ ] **F32. URDF with `.dae` and other mesh files.** Check what the importer does with Collada meshes and with `package://` meshes in other formats, and convert through F30 when it cannot read them
- [ ] **F33. Export with the tools we have.** `sim__stage__save` gains `format` (usd, usda, usdz, urdf); URDF through `isaacsim.asset.exporter.urdf`, and the result says what could not be exported. MJCF export only if a converter exists (none shipped: say so)
- [ ] **F34. MJCF and every format in packs and `sim__quick_start`.** `assets__pack_build` accepts `.xml` (MJCF) and the other formats and records `origin`; `sim__quick_start` takes `robot_file` (any supported file or pack) beside `robot=`, so a downloaded robot is one call from a scene
- [ ] **F35. `assets__dependencies` reads every format** on the host (xacro, SDF, glTF buffers and images, OBJ `mtllib`, DAE images), the free pre-check for the import
- [ ] **F36. Model folders and bundles.** `auto` recognises a Gazebo model folder (`model.config`), a ROS package (`package.xml`), a MuJoCo Menagerie folder (the `.xml` that has a `<worldbody>`, usually `scene.xml` or the robot name) and a LeRobot or Hugging Face robot repo, and picks the entry file; a folder with several candidates lists them instead of guessing
- [ ] **F37. Maps and terrain as options.** Import a Nav2 map (YAML plus image) as a floor plan, a heightmap image as terrain (`isaacsim.asset.importer.heightmap`), through the same entry
- [ ] **F38. CAD (STEP, IGES, JT)** through `omni.kit.converter.cad`, and Gaussian splats (`.ply`, `.spz`) through `omni.kit.converter.gsplat`, behind the same entry; add when a user asks, but the `format` list names them so an agent is told what is missing
- [ ] **F39. Validate every converted asset** before it is trusted: `omni.asset_validator` or our own checks (a physics variant, rigid bodies, joints, no NaN, sane scale), reported in the import result
- [ ] **F40. Tester evidence per format.** For each of F29 to F38, a plain-request tester (Gemini 3.8 Flash or another named model) downloads one real asset from a named repo and gets it into a scene; the result and its verification go to `~/isaac-workspace/runs/`; a format is ticked only after that
- [ ] **F41. Fewer calls (section 1, in assets).** (a) A bridge choice that survives between calls, or a `port` argument on the sim tools (tester C, W3: `use_bridge` first in every server session, about eight script runs); (b) the import result is enough to act on (joints, limits, bounds, articulation root), so no `sim__joints__inspect` or `sim__prim__get` follow-up is needed; (c) `assets__import_path` options `pose`, `fixed_base`, `joint_targets` and `ground=true` so import, place and first move are one call; (d) `assets__scan` and `assets__search` list files in every supported format so the agent finds the entry itself; (e) measure calls per task before and after with the same plain-request tester prompts; goal: a downloaded robot loaded, posed and photographed in 3 calls or fewer
- [ ] **F26. Wait for more evidence (tester B and A wishes):** a choice of output folder for `sim__camera__capture` and `sim__map__occupancy` (the folder is fixed on purpose); a viewport-only screenshot; a screen recording that survives its server exiting (the description now warns); a brighter overhead capture (not reproduced: the picture was readable)
- [ ] **F13. Aim the camera of a Lab play window** (tester 2 wish): the play window is its own Kit app with no MCP bridge, so `sim__viewport__frame` cannot reach it. Either a camera pose argument on `isaaclab__play`, or run it with the bridge enabled. Needed for a good demo video
- [ ] **F14. A one-call video of a play run** (tester 2 wish). Lab's own `--video` needs `moviepy` (1.x) and `imageio-ffmpeg` in the Lab interpreter, which the workspace env does not have; the tool refuses with that message and points at window recording, which worked. Decide whether to install them into the Lab env, or add a `record` option on `isaaclab__play`
- [ ] **F15. A PhysX run of the same walking task** for comparison with the Newton one (Newton is experimental here). Check which Hydra preset or override selects it, and report it in `physics`
- [ ] **F16. A named-source GitHub test:** the prompt names the repository (for example `unitreerobotics/unitree_ros`), the agent pulls it, the approval refusal goes to the human, and the pack is built with `origin`. Run 1 skipped approval by copying into the trusted folder, so the approval command flow has not been exercised by an agent
- [x] Tester 2 wishes checked and not changed: live training curves (the tester did not try `metrics_summary` during a run; I did, mid-run at step 480, and it worked); per-episode eval is already the open item "Eval beyond mean return"

Plan, one pull request each, in this order:
- [ ] PR 1: approval messages and Kit sees the project (F2, F3)
- [ ] PR 2: lab profile: strict scoped to the twin and the `ur5e-dt` pack, `ISAAC_MCP_PUBLIC_ASSETS` for public robots (default on), a `require` list in a project's `isaac-mcp.json` so the twin refuses to build a lab scene with missing lab files, `lab_fidelity` in twin results, descriptions that steer plain UR5e work to `sim__quick_start`
- [ ] PR 3: workspace mode: with `ISAAC_MCP_WORKSPACE` set, an opt-in step (`isaac_env.py init`) approves the asset folder and each project's own assets folder (the smallest folder that holds its files), never `<workspace>/projects` as a whole, so a project needs no per-folder approval command; per-project `isaac-mcp.json` honoured by host and Kit; one setup command; a safety note (cloned repos inside `projects/` are only trusted for the folders approved; off by default; the fresh-clone path is unchanged)
- [ ] PR 4: public grippers: `sim__quick_start gripper=` (default: a public gripper for any arm that has none, `none` to turn it off), a gripper command tool, and an optional fetch of NVIDIA's public robots and grippers into a pack for offline or strict use. Prototype: NVIDIA's UR5e asset has a `Gripper` variant (`Robotiq_2f_85`) that joins the arm's articulation with a `finger_joint`; a pick with IK and that joint did not yet lift the cube (open, to debug: tool offset, finger command, friction)
- [ ] PR 5: suction: NVIDIA's `isaacsim.robot.surface_gripper` is available on 6.2; wrap it as on/off, plus the attach/detach tool
- [ ] Scenario checks into the repo as tests (the throwaway-world resolver scenarios), and a live scenario write-up
- [ ] Agent tests in new project threads: fresh clone, project with its own assets, lab user with the full folder. Prove the real asset folder is untouched (file list and timestamps before and after; baseline taken 2026-11-05: 171 files)

## 3. Tool surface review: combine or remove

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

## 4. New tools for robotics and RL (from the live tests, 2026-11-04)

Found by two weaker-model testers given plain requests (a robot from GitHub into a scene; an Isaac Lab research
workflow) and by a G1 walking run. Each item is something an agent worked around or could not do. Verify each against
the code before building. Ranked by how much real work it blocked and how general it is.

What the tests showed working: the Unitree G1 and Google Barkour loaded from real repos through `assets__pack_build`
and `sim__quick_start`; joints, camera, LiDAR and a hit by a dropped mass all worked; Lab trained and scored the
built-in Franka reach task with two libraries, resumed one, recorded a video, and ran a custom task package. A built-in
G1 flat-ground task trained in 152 s (1,024 envs, 300 iterations), scored 13.3 over 16 full-length episodes with no falls,
and played in a window.

Bring a robot in
- [x] **MJCF import** (`assets__import_mjcf`): done in PR 82. Tested live on Sim 6.2 with the Menagerie UR5e (fixed base, 6 joints), Franka Panda (fixed, 9 joints) and Go2 (floating, 12 joints), with hostile files refused (absolute path, `..` escape, URL, missing include, DTD). Not yet: `sim__quick_start` for a `.xml`, packs for MJCF, and Isaac Sim 5.1 / 6.0 / 6.1 (the 6.0.x importers were not tried). The converter's documented gaps (no cameras, lights, sensors, flex) are listed in [ASSET-FORMATS.md](ASSET-FORMATS.md)
- [ ] **Safe xacro to URDF**: many ROS repos (UR, xArm) ship only `.xacro`, and we refuse it. Convert on the host without running the downloaded repo's scripts
- [ ] **Import a robot from a big repo without hand-packing**: one robot in a monorepo hit the 1,000-file / 256 MiB limit and needed `assets__dependencies` plus `assets__pack_build` by hand. An `auto_pack` option on `sim__quick_start` and `assets__import_urdf` (copy the closure, then import)
- [ ] **A robot from your own URDF into an Isaac Lab task**: the tester registered a custom task package (works through `ISAACLAB_TASK_MODULES`) but only around the built-in Franka. A tool or recipe to scaffold a Lab task and robot config from a pack URDF is the biggest gap for real RL. Check what Lab needs (articulation config, actuators) before designing it

Act on the robot
- [x] **Inverse and forward kinematics** (`sim__arm__ik`, `sim__arm__fk`): from the robot's own USD joints, no per-robot file; checked live on a Franka, UR5e, Unitree G1 arm, SO-ARM100 and a Barkour leg, and an agent found and used them unprompted. Solve only. Still open below: a one-call move-to-pose (solve then move), a straight-line Cartesian path, and collision awareness
- [ ] **Gripper for any robot**: open, close and grip by width and force, sync the fingers, report contact. The Franka's second finger has no authored drive; now flagged by `sim__joints__inspect` (done). A second agent test (a UR5e, no gripper) stopped at the grasp: needs a public gripper that `sim__quick_start` can attach to any arm (the Robotiq 2F-85 inside the Franka asset is a candidate; check its licence and that it attaches), plus the command above
- [x] `sim__prim__find` takes a `root` to search one subtree; `sim__joints__inspect` flags joints with no drive (`no_drive`, `joints_without_drive`)
- [ ] **Attach / suction constraint** (`sim__physics__attach`, `sim__physics__detach`): a temporary fixed joint between a link and an object, for pick-and-place without finger friction (agent wishlist, UR5e run). Not physically realistic: label it so
- [ ] **Create a joint between two prims** (`sim__joint__create`: fixed, revolute, prismatic) to build fingers or a tool; lower priority, overlaps the attach tool
- [ ] **`screenshot` saves outside a sandbox** (`~/Pictures/screen-mcp`): say so in its description, and optionally respect `MCP_CLIENT_OUTPUT_DIR`. The agent that hit it stopped using it and reported it
- [ ] **Twin quick-start descriptions** should say that for any robot other than the lab twin, or when a gripper is needed, `sim__quick_start` is the tool: an agent wanting a table picked `ur5e_dt__quick_start_with_workstation`, which has no gripper without private lab files (my guess at why; not verified)
- [ ] IK follow-ups: one-call `move_to` (solve then move), a Cartesian straight-line path, a check that the arm will not hit the ground or itself, and an orientation-weighting test on a non-metre stage (a known test gap)
- [ ] A tabletop preset for `sim__quick_start` (a table and a few pickable objects in one call). Low priority
- [ ] Decide: may read-only scene tools omit `session_id` and `stage_id`? (Today a plain read fails until `sim__stage__info` is called; the IDs stop edits landing on the wrong stage.)
- [ ] **Force, impulse and velocity on a running scene** (section 5): the tester dropped a 2 kg ball as a stand-in for a push
- [ ] **Smooth joint trajectories during play**: `sim__joints__move` only works paused and `sim__joints__command` steps to the target. A trajectory tool (a spline or trapezoid over a duration)
- [ ] **Command a trained policy's velocity** in a windowed `isaaclab__play` (the G1 task tracks a velocity command; today it is whatever the task samples)

RL workflow
- [ ] **`isaaclab__runs_compare`**: several `job_id`s in, side-by-side scalars, FPS and final return out. The tester compared seeds, library and env counts by hand
- [ ] **A sweep tool**: seeds and hyperparameters as one queued request, run one at a time on a small GPU, with a summary table
- [ ] **Resume that behaves the same on every library**: `rsl_rl` rejected an absolute checkpoint path (it matches a filename inside the run folder and needs a resume flag) while `rl_games` took it. Normalise `checkpoint` in `isaaclab__train`
- [ ] **`video_paths` in `isaaclab__runs_status`** when a play or eval records video (the tester had to `find` the file); and make `--video_length 100` and `--video_length=100` both work
- [ ] **`rl_games` pre-launch check**: `minibatch_size` must divide `num_envs * horizon`, and short runs save no checkpoint. Compute a valid value or say how before starting Kit
- [ ] **`isaaclab__metrics_summary` takes `job_id`** (it wants `run_dir`, which no other Lab tool uses)
- [ ] **Eval beyond mean return**: survival rate, episode length and per-term reward in `eval_result` (the G1 run needed mean length to show no falls)

Method
- [ ] Add this section's list to the `mcp-wishlist-testing` skill as a worked example, and re-run both tester briefs after each item lands to confirm the friction is gone

## 5. Poke a running scene

Authoring needs a stopped timeline, and Stop rewinds physics, so an agent could not nudge a block while playing.
Jenga, LEGO and manipulation need it.

- [ ] A tool that applies a force, an impulse, or sets a velocity on a body while playing or paused
- [ ] Works on PhysX; says plainly what it does on Newton (experimental) rather than guessing
- [ ] Test: nudge the top block of the tower and watch it move; pull a block out of a layer and see what falls
- [ ] Docs and the example prompts in the README

## 6. Second PR: M6 and M7 (branched from `main`)

- [ ] M6: `assets__robot_library`, `assets__import_mjcf`, `bundled` and `nvidia` sources
- [ ] M7: `sim__viewport__screenshot`, `sim__viewport__record_start`, `sim__viewport__record_stop`, and the final demo
- [ ] For LEGO and Jenga: an arm with a gripper in `sim__quick_start`, and a route from a saved scene to a Lab task
- [ ] Update `docs/TOOLS-*.md`, the counts, and regenerate the catalog and tool reference (`check_docs.py` enforces this)

## 7. Small fixes

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
- [ ] `timeout_s` with no host running waits until the next status call: a 25-minute play window stayed open 30 minutes. Enforce it from the job's own shell, and write an exit-code file when the timeout kills a job (today a script waiting for the file waits forever)

Map and Nav2
- [ ] Map rasterising: a collider marks every cell it touches, so a thin wall can show a cell wider
- [ ] With a robot on the origin, pass `start` on open floor; `nav2_params.py` needs ROS sourced

Optional
- [ ] Start a recording before a window exists, or stop on a log marker

## 8. Testing still owed

- [ ] **Lab PC, when Kaleb says it is back** (it was down, shared workstation): 6.0.0.0, 6.0.0.1, 6.0.1 and 6.1 with the smoke test, M1 to M5 and the plain-request tests; Lab 3.0.0 with the eval fix and the refusal hints
- [ ] 6.0.1 `sim__stop` fix, live (stop must leave nothing running)
- [ ] Lab 3.0.0: windowed `play` cancel after a full load, and the `window` field
- [ ] M1 to M5 on 6.0.0.0, 6.0.0.1, 6.0.1, 6.1 and Newton: only the API adapters and unit tests cover them
- [ ] A **windowed** plain-request run on 6.2 with the viewport and capture tools (home runs so far were headless)
- [ ] Have a plain-request tester use the new tools (tower, measure, raycast, poke) and fix what it fumbles
- [ ] Mark Lab 2.3.2 with Sim 5.1 `tested: true` in the compatibility matrix (a small PR)
- [ ] Re-run the README example prompts, including the LiDAR one, and fix any that fail
- [ ] Merged M1 to M5 with the timeline playing: `sim__joints__move` was only checked after Stop

## 9. Known limitations to state in the docs

- [ ] `--video` needs MoviePy and imageio-ffmpeg in the Lab environment
- [ ] A screen recording belongs to the server process that started it, so a client that restarts between calls can't stop it
- [ ] Real-robot mode is not enabled; `ursim` needs Docker
- [ ] `randomize_single_object` often can't fit: a 4 cm by 20 cm strip with 0.18 m separation, not configurable
- [ ] Why a floor scaled 20x20x0.1 gives wheels no contact is measured, not explained (133:1 fails, 100:1 drives on TurtleBot3; Carter erratic from 50:1)
- [ ] `[spin] error in rcl_take: error not set` lines in the Kit log on 6.0.0.0: appears harmless, not explained
- [ ] One `sim__camera__capture` had the selection outline in it after a prim was selected by hand; not reproduced in 4 tries

## 10. For Kaleb to decide

- [ ] Approve, change or reject the keep/combine/remove list when it arrives (section 3)
- [ ] Aldrin licence: Kaleb handles it. Update VENDORING, README and LICENSE only when he reports; do not ask again

## 11. Last of all

Run only after the tool work, all builds, Lab setup and merges, and only with Kaleb's go-ahead.

- [ ] Git history: variant B rewrite (plan `~/.cache/isaac-mcp-history-scrub/PLAN.md`)
- [ ] Public cleanup with it: remove `docs/plans` (keep a local copy and fix its links), course names and "student run" wording, the private strings in `scripts/check_private_paths.py`, and the remote branch `fix/student-run3`. "Kaleb's work with RISE lab" stays
- [ ] Clear PR edit history in the browser (it can't be done from the API), and delete `/tmp/scrub/`

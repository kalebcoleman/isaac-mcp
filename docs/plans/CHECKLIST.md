# Working checklist

Kaleb's running list. It holds only what is still to do; finished work is in the git history and the merged PRs
(PR 70 merged 2026-11-04, README PRs 71 and 72). Tick an item only after it is tested. Add every new issue here
so nothing is lost.

Claude codes and reviews. Testers are weaker models given short plain requests; what they fumble is the finding.

## Findings from the overnight run (2026-11-05 to 06)

- [x] **F52. `sim__arm__ik move=true` commanded failed solves.** A solve with `reached=false` swung the arm and knocked the tower over. It now commands nothing and says so.
- [x] **F53. Holding a Franka's hand level needs about 60 cm above its base** (joint 6's limit). The Jenga scene stands the arm on the floor beside a low table, and the hand holds a rod, because the 20 cm palm hit the tower on a deep push. In the Lab task a table under the wrist held the hand 3.5 cm high; a scripted push found it where 300 training iterations could not.
- [x] **F54. The URDF importer's fixed base joint breaks in cloned Lab environments.** Observations reach 1e12, then PPO fails with `normal expects all elements of std >= 0.0`. Use `fix_base=False` with `fix_root_link=True`.
- [x] **F56. A Jenga tower with blocks of one height depends on the solver.** Sim 5.1 put the load on the middle block, and pushing it carried the upper tower along until it fell; Sim 6.2 did not. Odd layers' middle blocks are now 0.4 mm thinner, and the same push gives 12 cm out with 0.3 mm at the top on both.
- [x] **F57. `sim__timeline__control` could not continue a timeline a project's own script paused** without the stop that would end the script (BrickSim's demo). `resume=true` does.
- [x] **F58. Tester G (Gemini 3.8 Flash, plain request, 2026-11-06) built the tower and pushed two blocks out with no help:** 42 calls, one error of its own, 11.9 cm each, top block moved 0.29 mm in all; picture and video checked by the operator. Its wishes that fit: `speed` on `sim__arm__ik` for a straight move in one call, and `paths` on `sim__body__state`, both added. Decided not to do: an `id` alias on `sim__status` (the error explains it), and an output path on `screenshot` and `start_screen_recording` (those save only under the user's picture and video folders by design).
- [x] **F59. Tester H (Gemini 3.8 Flash, plain request, 2026-11-06) trained the G1 and the SO-101 with no help:** 49 calls. G1 on PhysX: eval over 50 episodes 98% survived, 0.13 m/s tracking error. Found, and fixed: `physics=physx` picks the optional OvPhysX runtime and fails, so train, eval and play now have a `physics` option and a failed job says so; the play tool now says how to get a video; the reach scaffold's targets were half out of reach (eval 52% within 3 cm), so targets are now points the arm was measured at (eval 100%, 2.6 mm). Not an error of the tools but worth knowing: a task's training `Metrics/` come from whichever environments reset last, so it showed 100% where eval showed 52%; judge by eval. Decided not to do: a bridge inside play (`--video` with a set camera covers the need), a `record_to` option on play (`--video` does it). Its claim that `--video` lacks packages was not tested by it, and those packages are installed.
- [ ] **BrickSim's task is not trained:** 300 iterations reached a mean reward of 35 and some grasping, no success. Needs longer runs or shaping if it matters.
- [x] **F55. Branch review findings, all fixed:** percent-encoded glTF references could pass the folder check; `$(dirname)` in a folder name looped forever; output and parsing were bounded by element count, not bytes; mesh and map imports skipped the stage target and timeline guard; SDF degrees, quaternions (including at 90 degrees of pitch), `expressed_in` and open limits were read wrong; several raw exceptions; map yaw, 16-bit PNG, PNG size check; 1000 files plus a generated one; a timed-out job that ignored TERM reported "host restarted"; video search unbounded; MJCF base on 5.x; a fixed prop refused on export.

## Priorities (Kaleb, 2026-11-05)

Most of what was on this list was triaged away: what is left serves these goals. **BrickSim, new Jenga work, and RL** come first. The claim is **Isaac Sim 6.2 with Lab 3.0.0, and Sim 5.1 with Lab 2.3.2**; other builds are best effort. The UR5e twin tools stay, and are not a focus. An option on an existing tool beats a new tool. Skip what is impossible or nobody uses (the list of what was dropped is at the end).

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
- [x] **F2. Refusals do not tell the agent the exact fix.** `ROOT_NOT_APPROVED` and the asset-path message should name the folder and give the filled-in command the user runs (`python -m isaac_mcp.project_assets --root-id ... --root <folder>`), and say that no restart is needed if that is true
- [x] **F3. Kit launched the usual way never reads the project's `isaac-mcp.json`; the host does.** The same `sim__quick_start robot="ur5e"` is refused when Kit knows the project (override not approved) and quietly loads NVIDIA's public UR5e when it does not. Fix: the host passes the project to Kit when it launches one; `status` reports the project file Kit sees; `assets__packs` warns when host and Kit disagree
- [ ] **F5. Global strict mode blocks public robots and grippers for every tool.** Scope it to the twin (lab profile PR)
- [x] **F7. Approving a large parent folder breaks every import.** An import copies the whole approved folder (at most 1000 files or 256 MiB), so with `<workspace>/projects` approved a few-hundred-byte URDF fails with `LIMIT` (live, 2026-11-05). Approving only the robot folder worked end to end. Decision (Kaleb): do not approve the whole projects folder. PR 1 now advises the smallest folder, and PR 3 approves only what each project needs (its assets folder), never `projects/` as a whole
- [x] **F9. Approval does not stop an agent that has a shell.** Tester 1 (OpenCode Pulse Alpha Free, 2026-11-05) `cp`'d a cloned repo into the asset folder and built a pack from it with no approval, as designed: the asset folder is trusted. Decision (Kaleb): keep approval for paths outside the folders, and add provenance. Done in PR 1: `assets__pack_build origin` is stored in the pack file and listed by `assets__packs`; docs and the skill say to use sources the user names, keep downloads in `downloads/`, and never run downloaded code. Still to decide: whether `pack_adopt` should record a source as well
- [x] **F10. A named humanoid started half inside the floor.** Found by Kaleb on the first live load of `unitree_h1` (pelvis is the origin). `sim__quick_start` now lifts the named humanoids onto the ground (H1 1.049 m, G1 0.798 m; any first transform op), and only them. Added `unitree_h1` and `unitree_g1` as public names (tester wish W4). The first version lifted every named robot; a live check after review round 1 showed `carter_v1` raised 1,464 m and `nova_carter` 50 m (stray geometry in their USD), so the lift is opt-in per robot (`lift_to_ground`) and capped at 3 m, and every other named robot was re-checked live as unmoved
- [x] **F11. `sim__launch` said `id`, `sim__status` and `sim__stop` want `session_id`** (tester 1 hit the validation error). Results now carry both. `start_screen_recording` now says a recording ends if the MCP server process exits (tester 1 lost one and wrote its own driver)
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
- [x] **F28. One import entry for every format.** `assets__import_path` becomes the universal importer: `format` defaults to `auto` (read from the extension and the first bytes: USD/USDA/USDC/USDZ, URDF, MJCF, xacro, SDF, glTF/GLB/OBJ/STL/FBX/DAE, a model folder) and can be set to override. It returns the same ready summary for every format (articulation root, movable joints with limits, bounds, what the converter dropped). `assets__import_usd`, `assets__import_urdf` and `assets__import_mjcf` keep working as thin aliases; folding them away is part of the section 3 review and needs Kaleb's approval first **Done (branch `feat/universal-asset-import`): `assets__import_path` takes `format=auto|usd|urdf|xacro|sdf|mjcf|mesh|map` and a folder; the host picks, converts and snapshots; the result has an `import` block. `assets__import_usd`, `assets__import_urdf` and `assets__import_mjcf` were folded into it with Kaleb's approval (126 tools became 123); `root_id` with `relative_path` still works.**
- [x] **F29. Safe xacro expansion** (tester C, wishlist-3: the UR10e is xacro only; six calls, and a near-miss with the system `ur_description`). Option on the same import: `xacro_args` such as `{"ur_type": "ur10e"}`. A restricted expander on the host (properties, args, macros, `$(find pkg)` limited to the approved folder, arithmetic through a whitelist), never the `xacro` program and never `python:` or `eval` of arbitrary code. A refusal names what it could not expand **Done (branch `feat/universal-asset-import`): `mcp/isaac_mcp/xacro_lite.py`, an expression parser with a whitelist, never `eval` or the `xacro` program; the real UR10e expands to the same links, joints, limits, meshes and inertias as the system xacro; 36 tests including hostile expressions, includes and runaway macros; all missing `$(arg)` are named at once.**
- [x] **F30. Mesh and prop import** (tester C: the Khronos Duck had no route at all, because the import is USD only). glTF, GLB, OBJ, STL, FBX, DAE, PLY through `omni.kit.asset_converter` behind the same entry; options `scale`, `up_axis`, `as_collider` (none, convex hull, convex decomposition), `mass`. Units and up axis are stated in the result, never guessed silently **Done (branch `feat/universal-asset-import`): glTF, GLB, OBJ, STL, FBX, PLY through `omni.kit.asset_converter`, with `scale`, `up_axis`, `as_collider`, `mass`; references checked (hostile glTF refused live); live on Sim 6.2: Duck falls as a dynamic prop, STL Z-up, OBJ with absent .mtl warns. Collada (.dae) cannot be a prop (the converter does not read it): refused with a message; it works inside URDF.**
- [x] **F31. SDF (Gazebo) import** (tester C: the blue vehicle had to be rebuilt by hand from primitives, with no joints). There is no native importer, so write a restricted SDF reader on the host: links, inertials, visuals and collisions (box, sphere, cylinder, capsule, mesh), joints (fixed, revolute, prismatic, continuous, ball), `<include>` and `model://` resolved inside the approved folder, worlds with several models. Output goes through the same checked-USD path as URDF **Done (branch `feat/universal-asset-import`): `mcp/isaac_mcp/sdf_urdf.py`; live: the Gazebo blue vehicle (model chosen from a three-model world) imports with 5 joints and drives; ball joints become three continuous ones (warned), plugins and sensors are dropped and listed.**
- [x] **F43. Gemini 3.8 Flash testers (2026-11-05), windowed, verified by me.** Tester C (wishlist-3, frozen server from before PR 82): the UR10e (xacro expanded with the system `xacro`, 14 `.dae` meshes) imported through `assets__import_urdf` with its meshes visible and two joints moved (picture checked); the Teapot USD loaded; the Duck glTF and the SDF vehicle have no route, as predicted (F30, F31). Tester D (wishlist-4, PR 82 code): all four Menagerie robots (Panda, UR5e, Go2, H1) imported with `assets__import_mjcf`, each moved a joint, and all four stood in one scene (pictures checked); no processes left, the real asset store unchanged (171 files). What was a tester choice and not a bug: `fixed_base=true` holds Go2 and H1 in the air at their XML height (the tester asked for it); the 4 mm "below ground" warning on Go2 is the tool working as designed. Real findings: F44 and F45
- [x] **F44. A MuJoCo robot driven by torque motors comes in with no joint drives and sags** (tester D, Go2 and H1: `stiffness 0`, `damping 0`, limbs collapse until `sim__joints__gains`; the Panda and UR5e use position actuators and import with drives). This is faithful to the model, but the agent finds out only by failing. Make the import result say it: count the movable joints with no drive, and say that the model is torque-actuated and how to hold it (`sim__joints__gains`, or an option on the import such as `drive_gains={"stiffness": ..., "damping": ...}` applied to every movable joint). No new tool **Done (branch `feat/universal-asset-import`): the MJCF result lists `undriven_joints` and warns; `default_drives=true` gives them a generic holding drive; live on Go2.**
- [x] **F45. Tester C's per-call bridge wish (W3) is mostly a documentation gap:** `./call.py --port <port> <tool> '<json>'` already works for single calls. Say so at the top of `call.py --help`, in the skill and in `docs/MCP-SERVER.md`; and see F41 for a bridge choice that survives between calls in the MCP server itself **Done (branch `feat/universal-asset-import`): `call.py --port` and `client.py --port` documented in ASSET-IMPORT.md; a bridge choice that survives in the MCP server itself is not built.**
- [x] **F33. Export with the tools we have.** `sim__stage__save` gains `format` (usd, usda, usdz, urdf); URDF through `isaacsim.asset.exporter.urdf`, and the result says what could not be exported. MJCF export only if a converter exists (none shipped: say so) **Done (branch `feat/universal-asset-import`): `sim__stage__save format=urdf robot_path=...` through `isaacsim.asset.exporter.urdf`; live: the imported UR10e exported (34 meshes) and imported again. The exported OBJ material files are reported absent on re-import (colours lost): a known gap.**
- [x] **F34. MJCF and every format in packs and `sim__quick_start`.** `assets__pack_build` accepts `.xml` (MJCF) and the other formats and records `origin`; `sim__quick_start` takes `robot_file` (any supported file or pack) beside `robot=`, so a downloaded robot is one call from a scene **Done: `assets__pack_build` takes MJCF, SDF, meshes and maps (copies exactly what each names, pinned; a xacro file is refused with a pointer, because it needs its arguments); `sim__quick_start robot=<absolute path to any file or folder>` builds the scene and imports it in one call (live on the Menagerie Go2).**
- [x] **F35. `assets__dependencies` reads every format** on the host (xacro, SDF, glTF buffers and images, OBJ `mtllib`, DAE images), the free pre-check for the import **Done (branch `feat/universal-asset-import`): `assets__dependencies` reads xacro, SDF, meshes and maps on the host (live on the real UR10e, Duck, a Nav2 map and the Gazebo world).**
- [x] **F36. Robot bundles, all of them, through the same entry.** `format=auto` on a folder recognises and picks the entry file of: a Gazebo model folder (`model.config` plus `model.sdf`, as on Gazebo Fuel), a ROS package (`package.xml`, URDF or xacro under `urdf/`), a MuJoCo Menagerie folder (the `.xml` with a `<worldbody>`: `scene.xml` or the robot's own), a LeRobot or Hugging Face robot repo (URDF or MJCF plus meshes, often under `assets/` or `urdf/`), a `robot_descriptions` style package, and a USD folder with its own `payloads`/`textures`. A folder with several candidates lists them (file, kind, why) instead of guessing; one with none says what it looked for. The approval command is for the bundle's own folder, and `assets__scan` shows the kind of each bundle it finds **Done (branch `feat/universal-asset-import`): a folder as `source_path`: Gazebo model, ROS package, Menagerie/MuJoCo folder (robot beats scene, MJX variants last, meshes only if nothing else), URDF under `urdf/`, ROS map folder, USD; ties are listed as `AMBIGUOUS_BUNDLE`.**
- [x] **F42. Test matrix: every format and bundle is tested with one real asset and one plain-request tester.** The list is in [ASSET-FORMATS.md](ASSET-FORMATS.md#test-matrix); a row is ticked only when the tester's run, pictures and my own checks are in `~/isaac-workspace/runs/`, and tester mistakes are written down apart from real bugs **Done, with gaps listed.** Rows 1 to 13 of the matrix have a real asset run live: USD (Teapot), MJCF (Menagerie), URDF with `.dae`, xacro with arguments (UR10e, Franka FR3 without its hand), glTF/GLB (Duck, DamagedHelmet), STL and OBJ, SDF (diff_drive, Pioneer 2DX), Gazebo model folder, ROS package (TurtleBot3, UR description), Menagerie folder, SO-101 arm, Nav2 maps (two), export, hostile files (hostile glTF live; hostile xacro, SDF, YAML, UTF-16 in unit tests). Row 14 (fewer calls): testers E and F used 4 to 9 tool calls per item, setup excluded. **Not covered:** a LeRobot-style repo with its own layout beyond SO-101, a Fuel-format Gazebo model with `model.config` plus several SDF versions, Franka FR3 with the hand (the tester trimmed the `end_effectors/` folder away), and Nav2 goals through the quick-start warehouse.
- [x] **F37. Maps and terrain as options.** Import a Nav2/ROS map (the YAML with `image`, `resolution`, `origin`, `negate`, `occupied_thresh`, and its PGM or PNG) as a floor plan with walls from occupied cells, so a robot can be placed and driven in the map it will navigate; the same entry takes a heightmap image as terrain (`isaacsim.asset.importer.heightmap`). Round trip test: export a scene with `sim__map__occupancy`, import it back, and compare the cells. Today we only export maps **Done (branch `feat/universal-asset-import`): ROS map (.yaml + PGM/PNG) as wall boxes with static colliders; live on Nav2 `tb3_sandbox` (870 cells became 244 boxes). The reverse of `sim__map__occupancy`.**
- [x] **F38. CAD (STEP, IGES, JT)** through `omni.kit.converter.cad`, and Gaussian splats (`.ply`, `.spz`) through `omni.kit.converter.gsplat`, behind the same entry; add when a user asks, but the `format` list names them so an agent is told what is missing **Closed without building (Kaleb, 2026-11-05: skip anything not possible or not used).** CAD (STEP/IGES/JT) and Gaussian splats were not tried: no test file, no user, and the Kit converters are untested here. A CAD or splat file is refused with `UNSUPPORTED_FORMAT`. Reopen when someone needs one.
- [x] **F39. Validate every converted asset** before it is trusted: `omni.asset_validator` or our own checks (a physics variant, rigid bodies, joints, no NaN, sane scale), reported in the import result **Done (branch `feat/universal-asset-import`): every import result has `validation` (geometry, finite bounds, sane size, rigid bodies, articulation root, joints; mesh collision; map walls). It is our own checks, not `omni.asset_validator`.**
- [x] **F40. Tester evidence per format.** For each of F29 to F38, a plain-request tester (Gemini 3.8 Flash or another named model) downloads one real asset from a named repo and gets it into a scene; the result and its verification go to `~/isaac-workspace/runs/`; a format is ticked only after that **Done for the formats built.** Gemini 3.8 Flash testers, windowed, from real GitHub downloads, each verified by me (pictures, no leftover processes, real asset store unchanged at 171 files): C (UR10e xacro, Teapot USD), D (four Menagerie robots), E (Duck glTF, UR10e xacro, Gazebo diff-drive SDF, Nav2 map, Go2 folder, URDF export and re-import: 6 of 6), F (DamagedHelmet glb, Pioneer 2DX SDF with meshes, SO-101 URDF, warehouse map: 4 of 6 on the first run; Franka FR3 xacro and TurtleBot3 failed on real bugs in the xacro expander, fixed and re-checked live). Evidence: `runs/wishlist-3-4-gemini/`, `runs/wishlist-5-6-gemini/`, `runs/universal-import-demo/`.
- [x] **F46. A warehouse with a robot in one call** (Kaleb liked the Nav2 warehouse). `sim__quick_start environment=warehouse` builds a 24 by 16 m room with six racks, 90 pallet loads, two aisles, lane lines, three loading docks with staged pallets, pilasters and a charging station, and puts the robot (default `carter_v1`) at the start of an aisle; plain USD boxes with colliders, no download. Live on Sim 6.2: the Carter drove 10 m down the aisle in six seconds; the editor view frames the whole room. The Nav2-style occupancy map of it was exported with `sim__map__occupancy` (500 by 339 cells; racks, docks and walls occupied). Not done: Nav2 goals in this room (`ros=true` is set up but untested here), and picking the layout (size, number of racks) by arguments
- [x] **F47. Snapshot quota is 16 and every import uses two** (the approved folder, then the converted copy). Found by my own live runs: six robots in one scene used twelve, and a new import then failed with `QUOTA` until the staging folder was cleared by hand. Documented in ASSET-IMPORT.md. **Decision for Kaleb:** raise the limit, or delete the oldest snapshot that no live import uses. Left alone because it is a disk and safety trade-off
- [x] **F48. An agent can ask for a broad approval by pointing at a parent folder.** Tester F passed `downloads/` and the tool, correctly, offered to approve `downloads/` (six repositories, 165 MiB). The operator approved the six smallest folders instead. No code change: the approval message already says to approve the smallest folder, and a human runs it. Worth keeping in mind when writing agent instructions: name the file, not its parent
- [x] **F49. Findings from testers E and F, all fixed or decided:** an unapproved folder now gets the approval command (E, was "unsupported format"); a straight-down viewport view no longer refuses (E); xacro macro defaults with spaces (`xyz:='0 0 0'`), `**` block parameters, dotted access to YAML values, and xacro kept in a file named `.urdf` (F: Franka FR3 and TurtleBot3). Skipped on purpose: `xacro_args` on `sim__quick_start` (two calls are fine) and `path` against `prim_path` naming across `sim__prim__*` (not an asset issue)
- [x] **F50. The three import tools were folded into `assets__import_path`** (Kaleb approved: one tool, formats as options). 126 tools became 123; the counts, docs, skill and scripts were updated; `root_id` with `relative_path` still works
- [x] **F13. Aim the camera of a Lab play window** (tester 2 wish): the play window is its own Kit app with no MCP bridge, so `sim__viewport__frame` cannot reach it. Either a camera pose argument on `isaaclab__play`, or run it with the bridge enabled. Needed for a good demo video **Done (2026-11-06):** `env.sim.default_visualizer_cfg.eye` and `lookat` in `extra_args` aim a `--video` camera, used on the Jenga and SO-101 runs; see TOOLS-ISAACLAB section 8. A live window is still not reachable from MCP.
- [x] **F14. A one-call video of a play run** (tester 2 wish). Lab's own `--video` needs `moviepy` (1.x) and `imageio-ffmpeg` in the Lab interpreter, which the workspace env does not have; the tool refuses with that message and points at window recording, which worked. Decide whether to install them into the Lab env, or add a `record` option on `isaaclab__play` **Done (2026-11-06):** `--video` works headless on both claimed pairs once the packages are in the Lab env, and `video_paths` reads Lab 3's log lines.
- [x] **F15. A PhysX run of the same walking task** for comparison with the Newton one (Newton is experimental here). Check which Hydra preset or override selects it, and report it in `physics` **Done (2026-11-05):** `physics=isaacsim_physx`, 762 s, length 990, eval 33.8 over 64 episodes with 3 falls; table in TOOLS-ISAACLAB section 9.
- [x] Tester 2 wishes checked and not changed: live training curves (the tester did not try `metrics_summary` during a run; I did, mid-run at step 480, and it worked); per-episode eval is already the open item "Eval beyond mean return"

Plan, one pull request each, in this order:
- [ ] PR 2: lab profile: strict scoped to the twin and the `ur5e-dt` pack, `ISAAC_MCP_PUBLIC_ASSETS` for public robots (default on), a `require` list in a project's `isaac-mcp.json` so the twin refuses to build a lab scene with missing lab files, `lab_fidelity` in twin results, descriptions that steer plain UR5e work to `sim__quick_start`
- [ ] PR 4: public grippers: `sim__quick_start gripper=` (default: a public gripper for any arm that has none, `none` to turn it off), a gripper command tool, and an optional fetch of NVIDIA's public robots and grippers into a pack for offline or strict use. Prototype: NVIDIA's UR5e asset has a `Gripper` variant (`Robotiq_2f_85`) that joins the arm's articulation with a `finger_joint`; a pick with IK and that joint did not yet lift the cube (open, to debug: tool offset, finger command, friction)
- [ ] PR 5: suction: NVIDIA's `isaacsim.robot.surface_gripper` is available on 6.2; wrap it as on/off, plus the attach/detach tool

## 3. Tool surface review: combine or remove

131 tools is a lot for an agent to choose from, and some do nearly the same job. Review all of them, and merge or
drop the ones that earn no place. **Nothing is removed or combined until Kaleb has approved the list**; Kaleb said the
review itself is fine to do, and to land as its own branch.


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
- [x] **A robot from your own URDF into an Isaac Lab task**: the tester registered a custom task package (works through `ISAACLAB_TASK_MODULES`) but only around the built-in Franka. A tool or recipe to scaffold a Lab task and robot config from a pack URDF is the biggest gap for real RL. Check what Lab needs (articulation config, actuators) before designing it **Done (2026-11-06):** `examples/urdf_reach` trained the public SO-101 in 40 s, 95.3% of targets reached in eval; TOOLS-ISAACLAB section 6.

Act on the robot
- [x] **Inverse and forward kinematics** (`sim__arm__ik`, `sim__arm__fk`): from the robot's own USD joints, no per-robot file; checked live on a Franka, UR5e, Unitree G1 arm, SO-ARM100 and a Barkour leg, and an agent found and used them unprompted. Solve only. Still open below: a one-call move-to-pose (solve then move), a straight-line Cartesian path, and collision awareness
- [ ] **Gripper for any robot**: open, close and grip by width and force, sync the fingers, report contact. The Franka's second finger has no authored drive; now flagged by `sim__joints__inspect` (done). A second agent test (a UR5e, no gripper) stopped at the grasp: needs a public gripper that `sim__quick_start` can attach to any arm (the Robotiq 2F-85 inside the Franka asset is a candidate; check its licence and that it attaches), plus the command above
- [x] `sim__prim__find` takes a `root` to search one subtree; `sim__joints__inspect` flags joints with no drive (`no_drive`, `joints_without_drive`)
- [ ] **Attach / suction constraint** (`sim__physics__attach`, `sim__physics__detach`): a temporary fixed joint between a link and an object, for pick-and-place without finger friction (agent wishlist, UR5e run). Not physically realistic: label it so
- [ ] **`screenshot` saves outside a sandbox** (`~/Pictures/screen-mcp`): say so in its description, and optionally respect `MCP_CLIENT_OUTPUT_DIR`. The agent that hit it stopped using it and reported it
- [ ] **Twin quick-start descriptions** should say that for any robot other than the lab twin, or when a gripper is needed, `sim__quick_start` is the tool: an agent wanting a table picked `ur5e_dt__quick_start_with_workstation`, which has no gripper without private lab files (my guess at why; not verified)
- [ ] IK follow-ups: one-call `move_to` (solve then move), a Cartesian straight-line path, a check that the arm will not hit the ground or itself, and an orientation-weighting test on a non-metre stage (a known test gap)
- [ ] Decide: may read-only scene tools omit `session_id` and `stage_id`? (Today a plain read fails until `sim__stage__info` is called; the IDs stop edits landing on the wrong stage.)
- [ ] **Force, impulse and velocity on a running scene** (section 5): the tester dropped a 2 kg ball as a stand-in for a push
- [ ] **Smooth joint trajectories during play**: `sim__joints__move` only works paused and `sim__joints__command` steps to the target. A trajectory tool (a spline or trapezoid over a duration)
- [ ] **Command a trained policy's velocity** in a windowed `isaaclab__play` (the G1 task tracks a velocity command; today it is whatever the task samples)

RL workflow
- [x] **`isaaclab__runs_compare`**: several `job_id`s in, side-by-side scalars, FPS and final return out. The tester compared seeds, library and env counts by hand **Done as an option:** `compare_jobs` on `isaaclab__metrics_summary`.
- [ ] **A sweep tool**: seeds and hyperparameters as one queued request, run one at a time on a small GPU, with a summary table
- [x] **Resume that behaves the same on every library**: `rsl_rl` rejected an absolute checkpoint path (it matches a filename inside the run folder and needs a resume flag) while `rl_games` took it. Normalise `checkpoint` in `isaaclab__train` **Done** for `rsl_rl` on 2.3.2 and 3.0.0, measured.
- [x] **`video_paths` in `isaaclab__runs_status`** when a play or eval records video (the tester had to `find` the file); and make `--video_length 100` and `--video_length=100` both work **Done**, both flag forms.
- [ ] **`rl_games` pre-launch check**: `minibatch_size` must divide `num_envs * horizon`, and short runs save no checkpoint. Compute a valid value or say how before starting Kit
- [x] **`isaaclab__metrics_summary` takes `job_id`** (it wants `run_dir`, which no other Lab tool uses) **Done.**
- [x] **Eval beyond mean return**: survival rate, episode length and per-term reward in `eval_result` (the G1 run needed mean length to show no falls) **Done:** `ended_early`, `survived_to_time_limit`, `reward_terms`, `termination_terms` and a task's own `task_metrics`. Terms count only at resets, weighted by how many environments ended; the first version counted stale log values on every step and read far too low.

Method

## 5. Poke a running scene

Authoring needs a stopped timeline, and Stop rewinds physics, so an agent could not nudge a block while playing.
Jenga, LEGO and manipulation need it.

- [ ] A tool that applies a force, an impulse, or sets a velocity on a body while playing or paused
- [ ] Works on PhysX; says plainly what it does on Newton (experimental) rather than guessing
- [ ] Test: nudge the top block of the tower and watch it move; pull a block out of a layer and see what falls
- [ ] Docs and the example prompts in the README

## 6. Second PR: M6 and M7 (branched from `main`)

- [ ] For LEGO and Jenga: an arm with a gripper in `sim__quick_start`, and a route from a saved scene to a Lab task

## 7. Small fixes

Host layer and refusals
- [ ] A queued call that times out should say what the bridge is running (do it in the host layer's error formatting; the client attempt broke 11 tests)
- [ ] A SIGKILL mid-call should name the tool and say the effects are unknown (host layer, same reason)
- [ ] `ISAAC_MCP_SIM_MAX_INSTANCES` counts the whole machine, and its refusal doesn't say `0` disables it
- [ ] Refusals check state before type: `joints__state` on a Cube says "play first", then the real fault; same for `steps=0`
- [ ] `NO_SHARED_STAGING` comes before validating a root or path, so a typo reads as an unconfigured install
- [ ] Range refusals show the value but not the argument name; NaN shows as `None`
- [ ] A guessed argument gets `Extra inputs are not permitted` with no list of the real ones

Tools
- [ ] `sim__light__modify` requires both intensity and color
- [ ] `sim__logs__tail` reads the last 32 KiB (about 2 s of Info lines): add level and text filtering
- [ ] `sim__body__state` refuses while stopped, so "stop then read the pose" fails
- [ ] `sim__lidar__read`: its 256 samples are the start of the buffer, not a whole sweep; say so on `limit`
- [ ] `sim__lidar__read` `NOT_READY` text should mention a sensor inside its own robot's body (Carter's was on a chassis link)
- [ ] `clock=auto` reuses any existing clock, even one with no time source
- [ ] A twin tool (`ur5e_dt__set_object_pose`) tried on a non-twin object fails with a path nobody expects: its description should say it only works on the twin's objects
- [ ] A very flat floor already saved in a file gets no warning; only `sim__prim__create` warns

Docs and guides
- [ ] Trim the guide's `--yaw-tolerance 3.2` advice to the measured 0.2 to 0.5
- [ ] State that `num_episodes` is a floor: all parallel environments finish once first
- [ ] Say in the guide that `isaac_assets.py adopt` (no approval) is the route for an agent building a pack, if true for a URDF with meshes
- [ ] Docs say `Example_Rotary_2D` is 10 Hz; 5.1 reports 30
- [ ] Say that `sim__camera__capture` made in a session loses its render handles across save and reopen (`NOT_OWNED`, fixed by `adopt=true`); keep the refusal's `retry_with`

Lab
- [ ] `timeout_s` with no host running waits until the next status call: a 25-minute play window stayed open 30 minutes. Enforce it from the job's own shell, and write an exit-code file when the timeout kills a job (today a script waiting for the file waits forever)

Map and Nav2
- [ ] Map rasterising: a collider marks every cell it touches, so a thin wall can show a cell wider
- [ ] With a robot on the origin, pass `start` on open floor; `nav2_params.py` needs ROS sourced

Optional

## 8. Testing still owed

- [ ] Mark Lab 2.3.2 with Sim 5.1 `tested: true` in the compatibility matrix (a small PR)
- [ ] Re-run the README example prompts, including the LiDAR one, and fix any that fail

## 9. Known limitations to state in the docs

- [ ] `--video` needs MoviePy and imageio-ffmpeg in the Lab environment
- [ ] A screen recording belongs to the server process that started it, so a client that restarts between calls can't stop it
- [ ] `randomize_single_object` often can't fit: a 4 cm by 20 cm strip with 0.18 m separation, not configurable

## 10. For Kaleb to decide

- [ ] Aldrin licence: Kaleb handles it. Update VENDORING, README and LICENSE only when he reports; do not ask again

## Decided not to do (triage, 2026-11-05)

Closed as done, or dropped as not needed. Reopen one if a real run needs it.

- `sim__prim__clone` with a count and an offset, a grid or a stack (check what clone does today)
- Batch transforms (move many prims in one call)
- Look for other N-call patterns (joints, physics apply, lights) in the earlier test transcripts 
- F1. A robot an agent puts in the project's own `./assets` cannot be loaded: "A pack named only 
- F4. `assets__pack_adopt` and `pack_build` always write to the one primary asset folder, so an a
- F6. The printed approval command must run as shown. Found by running it (2026-11-05): the helpe
- F8. The store list names the same folder twice when a project's `asset_path` lists the workspac
- Wait for more evidence: a settle or `after_command` option for `sim__camera__capture` (tester 1
- F25. Expert-level asset coverage, with the tools we already have (Kaleb, 2026-11-05). Plan and 
- F32. URDF with `.dae` (Collada) and other mesh files. Many ROS robots (the UR descriptions amon
- F41. Fewer calls (section 1, in assets). (a) A bridge choice that survives between calls, or a 
- F26. Wait for more evidence (tester B and A wishes): a choice of output folder for `sim__camera
- F16. A named-source GitHub test: the prompt names the repository (for example `unitreerobotics/
- PR 1: approval messages and Kit sees the project (F2, F3)
- PR 3: workspace mode: with `ISAAC_MCP_WORKSPACE` set, an opt-in step (`isaac_env.py init`) appr
- Scenario checks into the repo as tests (the throwaway-world resolver scenarios), and a live sce
- Agent tests in new project threads: fresh clone, project with its own assets, lab user with the
- Inventory. One table of every tool: group, what it does, the tools it overlaps, and how often a
- Classify each as keep, combine, rename, or remove, with a one-line reason and a risk: who might
- Bring the list to Kaleb. He approves, changes or rejects each line before any code moves
- Implement the approved list on its own branch: a merged tool keeps the old name as a thin alias
- Update everything the counts touch: `AGENTS.md`, the README tier table, the skill, `docs/TOOLS-
- Re-run a plain-request tester afterwards to check an agent does at least as well with the small
- Safe xacro to URDF: many ROS repos (UR, xArm) ship only `.xacro`, and we refuse it. Convert on 
- Import a robot from a big repo without hand-packing: one robot in a monorepo hit the 1,000-file
- Create a joint between two prims (`sim__joint__create`: fixed, revolute, prismatic) to build fi
- A tabletop preset for `sim__quick_start` (a table and a few pickable objects in one call). Low 
- Add this section's list to the `mcp-wishlist-testing` skill as a worked example, and re-run bot
- M6: `assets__robot_library`, `assets__import_mjcf`, `bundled` and `nvidia` sources
- M7: `sim__viewport__screenshot`, `sim__viewport__record_start`, `sim__viewport__record_stop`, a
- Update `docs/TOOLS-*.md`, the counts, and regenerate the catalog and tool reference (`check_doc
- `assets__*` `STALE_TARGET` is checked on the host, so it lacks the outside-change explanation
- `sim__map__occupancy` requires `min` and `max`; a default from colliders needs care with a grou
- `assets__pack_doctor` doesn't list a pack's files
- A window appears 8 to 12 s before it renders: consider a `loading` field on `runs_status`
- On 6.0.0.0, a LiDAR read after building a scan graph on a tool-created sensor reported `NOT_OWN
- Stale camera handles have no repair on open (only `RemoveProperty` from `run_python` worked); b
- A documented example of a project shipping its own tools through the bridge
- `runs_status.window` was missing on the lab PC, present on the desktop
- Two windowed jobs have the same title, so only id and pid tell them apart
- A window still loading is black for up to 4 minutes when two Kits share a GPU, with no signal
- Start a recording before a window exists, or stop on a log marker
- Lab PC, when Kaleb says it is back (it was down, shared workstation): 6.0.0.0, 6.0.0.1, 6.0.1 a
- 6.0.1 `sim__stop` fix, live (stop must leave nothing running)
- Lab 3.0.0: windowed `play` cancel after a full load, and the `window` field
- M1 to M5 on 6.0.0.0, 6.0.0.1, 6.0.1, 6.1 and Newton: only the API adapters and unit tests cover
- A windowed plain-request run on 6.2 with the viewport and capture tools (home runs so far were 
- Have a plain-request tester use the new tools (tower, measure, raycast, poke) and fix what it f
- Merged M1 to M5 with the timeline playing: `sim__joints__move` was only checked after Stop
- Real-robot mode is not enabled; `ursim` needs Docker
- Why a floor scaled 20x20x0.1 gives wheels no contact is measured, not explained (133:1 fails, 1
- `[spin] error in rcl_take: error not set` lines in the Kit log on 6.0.0.0: appears harmless, no
- One `sim__camera__capture` had the selection outline in it after a prim was selected by hand; n
- Approve, change or reject the keep/combine/remove list when it arrives (section 3)

## 11. Last of all

Run only after the tool work, all builds, Lab setup and merges, and only with Kaleb's go-ahead.

- [ ] Git history: variant B rewrite (plan `~/.cache/isaac-mcp-history-scrub/PLAN.md`)
- [ ] Public cleanup with it: remove `docs/plans` (keep a local copy and fix its links), course names and "student run" wording, the private strings in `scripts/check_private_paths.py`, and the remote branch `fix/student-run3`. "Kaleb's work with RISE lab" stays
- [ ] Clear PR edit history in the browser (it can't be done from the API), and delete `/tmp/scrub/`

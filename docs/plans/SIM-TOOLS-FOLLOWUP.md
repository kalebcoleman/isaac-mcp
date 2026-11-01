# Build plan: finishing the generic Isaac Sim tools after PR #11

**Status: plan, not built.** Decisions D1–D3 were made by the owner on 2026-10-31 and adjusted on
2026-11-01 after PR #11 was reviewed (see §2). The rest of this document has not been reviewed by
the owner yet. Written for an implementing agent.

## 1. Where things stand

Two other pull requests already cover much of what this plan first proposed:

- **[PR #10](https://github.com/kalebcoleman/isaac-mcp/pull/10)** (`docs/asset-scene-e2e-plan`,
  open against `main`): the plan `docs/plans/ASSET-SCENE-END-TO-END.md` for project-folder USD
  assets and generic scene tools.
- **[PR #11](https://github.com/kalebcoleman/isaac-mcp/pull/11)** (`feat/project-usd-generic-scene`,
  draft, stacked on #10, **still being worked on** in its own thread as of 2026-11-01): the
  implementation. Its Kit extension `exts/sim-tools` registers two namespaces on the shared
  bridge, `sim` (31 tools) and `assets` (6 tools), with contracts in
  `mcp/isaac_mcp/scene_contracts.py`, offline catalogs `mcp/isaac_mcp/catalogs/{sim,assets}.json`,
  reference `docs/TOOLS-SCENE.md`, a tool-by-tool comparison with the whats2000 fork
  (`docs/FORK-COMPARISON.md`), and evidence under `docs/evidence/project-scene/` (live 6.2 runs of
  all tools, an independent agent's 76-call task, camera and LiDAR stress runs, adapters for
  Isaac Sim 6.0.0.0 through 6.2 and for the Newton backend).

This plan covers only what PR #11 does **not** deliver, measured against the High-priority areas in
[ECOSYSTEM.md](../ECOSYSTEM.md#what-to-build-next) and the owner's asset decision. The remaining
gates PR #10 owns (isolated untrusted loading, authenticated remote upload, presets/manifests,
general Action Graph editing, 3D generation) stay with PR #10's plan and are **out of scope** here.

| ECOSYSTEM area | PR #11 provides (verify against its final head) | Still missing → milestone |
|---|---|---|
| Scene inspection | `sim.stage.info`, `sim.stage.tree`, `sim.prim.get`, `sim.prim.find` (literal substring), `sim.physics.inspect` | world bounds and distances, camera listing → **M1** |
| Logs and diagnostics | `sim.logs.tail` (bounded tail of Kit's log file, warnings and errors) | level/channel/text filters, a cursor, "since now" summaries → **M2** |
| Playback and stepping | `sim.timeline.control` (play/pause/stop), `sim.simulation.step` (1–120 exact paused steps with observed prims and joints), `sim.body.state` | raycast and contact queries → **M3** |
| Robot state and joint control | `sim.joints.inspect`, `sim.joints.state`, `sim.joints.command` (position targets in DOF order), `sim.joints.drive` (authored, stopped timeline) | name-based and velocity targets, limit clamping, runtime gains, move-until-settled with tracking error, teleport → **M4** |
| Prim editing | `sim.prim.create` (Cube, Sphere, Xform, PhysicsScene), `set_transform`, `clone`, `delete`, `sim.stage.clear_owned`, lights, materials, `sim.physics.configure` (all limited to prims the pack created, in the session layer) | more primitive types, typed attributes, physics schemas on imported assets, posing imported wrappers → **M5** (scope needs an owner decision, §2 D4) |
| Assets, robots and environments | `assets.roots`, `assets.scan`, `assets.search` (filenames in launch-approved roots), `assets.info`, `assets.import_usd`, `assets.import_urdf` (trusted projects, snapshots) | NVIDIA cloud library and robot catalogue, bundled samples, MJCF import → **M6** |
| Viewport capture | `sim.camera.create` / `sim.camera.capture` (RTX camera to PNG), `sim.viewport.frame`; desktop `screenshot` on the host | Kit viewport screenshot and MP4 recording from the pack, then the final demo → **M7** |

## 2. Decisions

| # | Decision | Consequence |
|---|---|---|
| D1 | **Namespaces `sim` and `assets`, as shipped by PR #11.** This replaces the `isaacsim` namespace chosen on 2026-10-31, which was picked before PR #11's tools existed. | New tools join `exts/sim-tools` and `scene_contracts.py`; no second pack, no renames. MCP names stay `sim__<group>__<verb>` and `assets__<verb>`; the host's session tools `sim__launch/list/status/stop` remain distinct names. If the owner still wants `isaacsim`, renaming all of PR #11's 37 tools is a separate, breaking change and is not part of this plan. |
| D2 | **Assets in trusted-operator mode, including the NVIDIA cloud library.** | Unchanged from 2026-10-31. M6 adds two sources next to PR #11's launch-approved project roots: Isaac Sim's bundled samples and the NVIDIA asset root (`get_assets_root_path()`, the S3 library, needs outbound HTTPS). Neither is a sandbox. The cloud source is configured like PR #11's other asset settings: a switch in `ISAAC_MCP_ASSET_CONFIG`, or `ISAAC_MCP_CLOUD_ASSETS=0` if the config file is not the right place, turns it off. |
| D3 | **One pull request per milestone.** | Stack them: M0 branches from PR #11's final head **with PR #12 (`docs/agent-skill-ecosystem`) merged in**, unless both are already on `main`; each later milestone branches from the previous one. Rebase and retarget as #10, #11 and #12 merge. Never push to `main` or merge; the owner merges. |
| D4 | **Open: how far may edits reach beyond pack-created prims?** | PR #11 deliberately edits only prims it created, in the session layer. M5's recommendation is to extend editing to wrappers created by `assets.import_*` (pose, physics schemas, typed attributes on the wrapper only) and keep foreign prims read-only. The implementer must ask the owner before starting M5 if this is still open. |

## 3. Preconditions and rules for the implementing agent

**Do not start until PR #11's own work has stopped**: its thread is idle and its PR head is pushed
(it was `bed3082` on GitHub with a later local commit `1135ec4` still unpushed when this was written).
Never edit PR #11's branch or worktree (the worktree its own thread uses).

1. Read [AGENTS.md](../../AGENTS.md), the [agent skill](../../.agents/skills/isaac-mcp/SKILL.md),
   [tool-authoring.md](../../mcp/docs/tool-authoring.md), [protocol.md](../../mcp/docs/protocol.md),
   and from PR #11's final head: `docs/TOOLS-SCENE.md`, `docs/FORK-COMPARISON.md`,
   `docs/evidence/project-scene/README.md` and `TOOL-COVERAGE.md`, and
   `docs/plans/ASSET-SCENE-END-TO-END.md` from PR #10.
2. **Follow PR #11's conventions** rather than inventing new ones:
   - every scene call targets an exact `session_id` and `stage_id` from `sim.stage.info`;
   - edits go into the session layer and preserve source files and the user's stage;
   - refusals use the pack's `refuse(CODE, message)` helper with stable codes;
   - runtime reads go through the backend adapters (PhysX and Newton), selected by build and
     capability detection, never by a fixed version list;
   - output files go under `ISAAC_MCP_SCENE_OUTPUT`; asset roots come from `ISAAC_MCP_ASSET_CONFIG`;
   - code-executing features stay behind `ISAAC_MCP_ENABLE_SCENE_SCRIPTS=1`.
3. **Use disposable simulators only.** Start them with `sim__launch` (opt-in group
   `ISAAC_MCP_LOCAL_TOOLS=all,sim_launch`) on ports from 18766 up, with an unused ROS domain, or
   `./run.sh --port <port>` (add `--motion-bvh` for LiDAR, as PR #11 requires). Check PID and port
   with `status` before every scene change. Never touch the owner's session on 8766 or any bridge
   you did not start, and stop what you started.
4. One GPU job at a time on the 16 GB card. No Isaac Lab jobs during this work.
5. Handlers never pump the app (`app.update()`, `world.step()`, `world.render()`); PR #11's step
   tool shows the paused-stepping pattern to reuse.
6. **Never install a Python log callback** (`omni.log` message consumers, `carb.logging` loggers,
   the log event stream, `error_log` events). Other servers deadlocked Kit that way (S12, S13).
   PR #11's `sim.logs.tail` already reads Kit's log file; M2 extends that.
7. Do not change `exts/ur5e-dt` behaviour or `exts/ur5e-dt/assets/**`.
8. **Every released 6.x build, as the owner requires for PR #11.** Run each milestone's suite on
   every build PR #11's adapters support (6.0.0.0, 6.0.0.1, 6.0.1.0, 6.1.0.0 and 6.2.0.0 when this
   was written), on PhysX and on Newton where PR #11 qualifies it. A tool that cannot work on a
   build or backend must refuse with a stable code before reaching a risky native path, and the
   evidence must record the result per build and backend.
9. If something here turns out wrong against PR #11's final code or a live test, record what you
   observed, adapt, and update this plan in the same PR.
10. Stop and ask the owner only for: D4 if still open, credentials, another machine, deleting
    anything you did not create, or a change to an existing public tool contract.

## 4. Proof: what every milestone must deliver

Reuse PR #11's harness instead of building a new one: `scripts/scene_mcp_client.py` (real stdio MCP
client) and the `scripts/validate_scene_*.py` suites. Add one suite per milestone,
`scripts/validate_scene_m<N>_<slug>.py`, that calls the new tools through MCP with numeric
assertions, and extend the client if it lacks screenshot or recording helpers (use the host
`screenshot(window_id=…)` and `start_screen_recording(window_id=…, full_screen=false)` tools).

**Per milestone, commit** `docs/evidence/scene-followup/M<N>/` in the same layout as
`docs/evidence/project-scene/`: `README.md` (environment block, results table, what was not
verified), JSON run records, and PNG screenshots (each ≤ 400 KB, ≤ 15 per milestone). The environment
block lists OS, display, GPU and driver, every Isaac Sim build tested, backend, Kit PID and port,
isaac-mcp git SHA, enabled tool groups, and the assets used.

**Videos** (MP4, 1280 px, 10–40 s) are not committed: upload them with `gh release upload` to a
**draft** release `scene-followup-evidence` (create once with
`gh release create scene-followup-evidence --draft`), and record each URL, SHA-256, duration and
frame count (`ffprobe`) in the README.

**Definition of done for every milestone:**

1. Tools added to `scene_contracts.py` and `exts/sim-tools`, with bounded responses and stable
   refusal codes; catalogs regenerated (`python3 scripts/gen_host_catalog.py --write`, then `--check`).
2. Unit tests pass: `pytest -q` from `mcp/`, plus the minimum-SDK command in
   `.github/workflows/ci.yml`.
3. `python3 scripts/check_docs.py` passes; `docs/TOOLS-SCENE.md`, AGENTS.md counts, the agent
   skill and `docs/FORK-COMPARISON.md` (where a fork gap closes) are updated.
4. The milestone's live suite passes through MCP on every build and backend in rule 8, with screenshots and
   any required video captured and looked at by the agent.
5. The evidence README states failures and what was not verified.
6. One PR, stacked as in D3, whose body embeds screenshots with absolute links pinned to the
   commit (`https://github.com/kalebcoleman/isaac-mcp/blob/<sha>/docs/evidence/scene-followup/M<N>/<file>.png?raw=true`;
   relative links do not render in a PR body) and links the videos.

## 5. Milestones

Tool names below are proposals in PR #11's naming style; parameters are in addition to the
`session_id` and `stage_id` every scene tool takes.

### M0: Baseline on PR #11's final head

No new tools. Create the branch from PR #11's final head and merge `docs/agent-skill-ecosystem`
(PR #12) into it, unless both are on `main` already. PR #12 brings the agent skill, ECOSYSTEM.md,
`scripts/ecosystem_capabilities.py` and this plan, none of which PR #11 has. A test merge on
2026-11-01 conflicted only in two status lines, `docs/GENERIC-SIM-TOOLS.md` and
`docs/plans/README.md`: keep both sides' content. `AGENTS.md`, `README.md`, `scripts/check_docs.py`
and `mcp/tests/test_check_docs.py` merged automatically, but rerun `check_docs.py` and the tests
because tool counts change. Then run PR #11's full live suites on every supported build with
the current harness, and record the baseline in `docs/evidence/scene-followup/M0/`. Then reconcile
the table in §1 against the final registry and evidence (PR #11 may have closed some gaps after
this plan was written) and update this plan in the M0 PR. Screenshot: the disposable Kit window
with a scene built by PR #11's tools.

### M1: Bounds, distances and cameras

| Tool | Parameters | Returns |
|---|---|---|
| `sim.prim.bounds` | `path`, `purpose="default"` | world-space axis-aligned and oriented bounds, centre, size, stage units (`UsdGeom.BBoxCache.ComputeWorldBound`; design §3.24 shows world, local and untransformed bounds differ) |
| `sim.prim.measure` | `path_a`, `path_b` | centre distance and the gap between world bounds (0 when overlapping) |
| `sim.camera.list` | `root="/"`, `limit=100` | cameras with focal length, aperture, clipping range, world pose, and the active viewport camera |

**Prove:** an imported robot and a pack-created cube; bounds match a `run_python`
`ComputeWorldBound` reading to 1e-6, a measured gap matches a hand calculation, and a camera created
with `sim.camera.create` is listed. Screenshot: the scene with `sim.viewport.frame` on the robot.

### M2: Log filtering, cursors and summaries

Extend `sim.logs.tail` without breaking its callers (new optional parameters, same default output)
and add two tools:

| Tool | Parameters | Returns |
|---|---|---|
| `sim.logs.tail` (extended) | `min_level`, `channel`, `contains`, `cursor` | entries with timestamp, level, channel, message (each capped at 2 KB), `next_cursor`, `truncated` |
| `sim.logs.mark` | — | a cursor at the current end of Kit's log (byte offset plus file identity, so rotation or truncation is detected) |
| `sim.logs.summary` | `cursor` | counts by level and channel since the cursor, first and last error |

Parse the file the same way PR #11 does, or adapt serionist's header regex (S12; MIT, credit it).
Read at most 1 MiB per call from the end. Redact obvious secrets. Add a unit test that fails if the
pack contains any log-hook name from S13's list. This also closes the fork's "since play" gap
(`get_isaac_logs(since_last_play=True)`): `mark` before Play.

**Prove:** mark; emit `carb.log_warn` and `carb.log_error` probes with `run_python`; import a USD
with a missing dependency; `summary` counts them, and the filters return exactly them. Then import
a robot and play for 30 s while calling `tail` every 2 s: no stall (S13's failure). Screenshot: the
Kit Console panel beside the matching JSON.

### M3: Raycasts and contacts

| Tool | Parameters | Returns |
|---|---|---|
| `sim.physics.raycast` | `origin`, `direction`, `max_distance=100` (≤ 100), `all=false` (≤ 32 hits) | hits with position, normal, distance, collider path, rigid body path; `scene_ready=false` before physics initialises |
| `sim.physics.contacts` | `paths` (≤ 32) | current contact pairs and impulses for those bodies, if the backend exposes them; otherwise a clear refusal |

Use the PhysX scene-query interface (C12 in `GENERIC-SIM-TOOLS.md` §3.25). For Newton, refuse with a
stable code unless an equivalent query is verified.

**Prove:** a cube resting on the ground after `sim.simulation.step`; a ray straight down from 2 m
hits the cube top within 1 cm; with `all=true` it also reports the ground; `contacts` on the cube
reports the ground pair. Screenshot of the scene; no video needed.

### M4: Joint control an agent can steer by name

PR #11 commands position targets in DOF order and edits drives only on a stopped timeline. Add, on
the same backend adapters (the experimental `Articulation` API, S6):

| Tool | Parameters | Returns |
|---|---|---|
| `sim.joints.command` (extended) | optional `targets={dof_name: value}` and `velocities={…}` alongside the existing `positions`, `clamp=false` | applied targets by name; out-of-limit values refused unless `clamp=true`, which reports what was clamped |
| `sim.joints.gains` | `path`, `dofs`, `stiffness`, `damping`, `max_effort`, or no values to read | runtime gains read back before and after (`set_dof_gains`/`get_dof_gains`, `set_dof_max_efforts`) while initialised; complements the authored `sim.joints.drive` |
| `sim.joints.move` | `path`, `targets={…}`, `max_steps=600`, `tolerance=0.01` | steps the paused simulation until every joint is within tolerance or the limit; per-joint error, steps used, simulated time, `settled` |
| `sim.joints.teleport` | `path`, `positions={…}` | sets joint positions directly (state reset, not motion), with a warning that physics is bypassed |

Units are explicit in every response (rad and rad/s for revolute joints, stage units for
prismatic). Unknown DOF names return the valid names. The name-based shape follows GenRobo's
`set_joint_targets_by_name` (S10); the settle-and-measure idea follows dex-isaac-mcp's
`sim_get_stats` (S11); Tim0320's 6.x adapter (S9) uses the same experimental `Articulation` API.

**Prove on two robots**: a non-UR5e articulated asset from PR #11's evidence (confirm where it
lives, that your session can reach it through an approved root, and its license; PR #11's thread
questioned one candidate's provenance), and the Cobotta Pro 900 converted from the URDF bundled with Isaac Sim (S16) with `assets.import_urdf`
(add the bundled folder as a trusted root for the test until M6 adds the bundled source). For
each: `move` three joints about 0.5 rad and back (settled, error ≤ 0.01 rad), runtime gains changed
and read back, an out-of-limit target refused and then clamped. Video: both robots moving (about
20 s). Screenshots: start and end poses.

### M5: Editing imported assets (after D4)

With the owner's answer to D4, extend editing to wrappers created by `assets.import_*`:

| Tool | Notes |
|---|---|
| `sim.prim.create` (extended) | add `Cylinder`, `Capsule`, `Cone`, `Plane` to the existing kinds |
| `sim.prim.set_transform` (extended) | also accept import wrappers, still refusing articulation links and animated stacks |
| `sim.prim.set_attribute` | typed, read back old and new; only on pack-owned prims and import wrappers; refuses engine-owned attributes (`points`, `extent`, `xformOpOrder`, `physics:*`, design §4.3) |
| `sim.physics.apply` | collider, rigid body or mass on a pack-owned prim or import wrapper; refuses when an ancestor is already a rigid body |

Keep `sim.stage.clear_owned`'s behaviour; do not add a full-stage reset unless the owner asks.

**Prove:** import a visual-only USD object, give it a collider and rigid body with
`sim.physics.apply`, pose it above a table and step until it rests. Show the refusals (foreign prim,
`xformOpOrder`, nested rigid body). Video: the object falling and resting (about 15 s).

### M6: NVIDIA cloud library, bundled samples and MJCF (D2)

| Tool | Notes |
|---|---|
| `assets.roots` (extended) | also report a `bundled` source (the importers' sample data in the running install) and an `nvidia` source (the resolved asset root and whether it is reachable) |
| `assets.robot_library` | browse `<asset root>/Isaac/Robots/<maker>/<model>/` with `omni.client.list` (bounded, cached per session), as Tim0320 does (S9) |
| `assets.import_usd` (extended) | accept `source="nvidia"` or `"bundled"` with a relative path; the cloud source references the asset directly (no snapshot) and says so |
| `assets.import_mjcf` | `MJCFImporter` (S17) with the same conversion pattern PR #11 uses for URDF |

**First step, before building on it:** in a live session, try `omni.client.list` on
`<asset root>/Isaac/Robots/` and record the result. Listing an HTTPS S3 root may not be supported
(`get_assets_root_path` has an `accept_unsupported` flag for providers without path metadata, S7).
If it fails, `robot_library` uses a curated table of known robot paths, like whats2000's (S14),
checking each entry with `omni.client.stat` and marking unreachable ones; check whether the
`isaac-sim-assets` skill (S24) documents an availability manifest that could replace the table.

Caller-supplied absolute paths and URLs stay refused; sources are named, paths are relative. Report
unit and up-axis mismatches instead of fixing them silently. Add a "Cloud and bundled sources"
section to `docs/TOOLS-SCENE.md` stating that these are trusted sources, not a sandbox, and that the
cloud source needs network access.

**Prove** with four robots from three sources: a trusted project root (PR #11's), the Cobotta URDF
and the `nv_ant` MJCF from `bundled`, and the Franka Panda from `nvidia` through `robot_library`.
List them with `sim.physics.inspect`, move one joint on each with M4. Show refusals: absolute path,
`..`, unknown source, the cloud source switched off (D2). Video: the four robots
loading and moving (about 30 s). Screenshot: all four in one frame.

### M7: Kit viewport capture and recording, then the final demo

| Tool | Notes |
|---|---|
| `sim.viewport.screenshot` | `filename`, `camera=null`, `width`, `height`; viewport capture through `omni.kit.capture.viewport` (C4), restoring the active camera afterwards |
| `sim.viewport.record_start` / `record_stop` | MP4 through the same extension; one capture at a time; frame caps from design §4.4 |

These complement PR #11's RTX `sim.camera.capture` (one sensor image) and the host's desktop
recorder. Test windowed and headless sessions and record what happens without a viewport (design
§3.26, §7 risk 3).

**Final demo**, one MCP scenario from an empty stage: four robots from M6; `sim.logs.mark`;
`sim.stage.tree`; `sim.viewport.record_start`; `sim.joints.move` each robot through two poses;
three seconds of play for the ant to settle; `record_stop`; `sim.joints.state` within tolerance;
`sim.logs.summary`; `sim.stage.save` and a reopen compared with `sim.stage.tree`;
`sim.viewport.screenshot`. Deliver the pack's recording **and** a host screen recording of the
same run. Then update `scripts/ecosystem_capabilities.py` ("isaac-mcp today" column) and regenerate
ECOSYSTEM.md, and update the agent skill, `docs/GENERIC-SIM-TOOLS.md` status and
`docs/VERIFICATION.md`.

## 6. Risks to watch

| Risk | Where | What to do |
|---|---|---|
| PR #11 changes after this plan | all | M0 reconciles the gap table with its final head |
| Tensor views not initialised | M3, M4 | Reuse PR #11's runtime checks; return its "not initialised" refusal |
| Kit deadlock from log callbacks | M2 | File reading only (rule 6) and the guard test |
| Newton lacks an equivalent query | M3, M4 | Refuse with a stable code unless verified live |
| Cloud assets slow, unreachable or not listable | M6 | Bounded `omni.client` calls; probe listing first; curated, `stat`-checked fallback |
| Importer API drift between 6.x builds | M6 | Use PR #11's build detection; cite installed versions (S15, S17) |
| Capture with no viewport | M7 | Test windowed and headless; refuse clearly |
| Out-of-limit or unstable joint targets | M4 | Refuse by default; `move` has a step limit and reports `settled=false` |

## 7. Sources

Source ids are stable labels used in the text above; the numbering has gaps on purpose.

Internal (this repository and its open pull requests):

- I0 [PR #11](https://github.com/kalebcoleman/isaac-mcp/pull/11) on `feat/project-usd-generic-scene`
  (read at local commit `1135ec4`, 2026-11-01): `exts/sim-tools/sim_tools/{core,advanced,extension}.py`,
  `mcp/isaac_mcp/scene_contracts.py`, `mcp/isaac_mcp/project_assets.py`, `docs/TOOLS-SCENE.md`,
  `docs/FORK-COMPARISON.md`, `docs/evidence/project-scene/`, `scripts/scene_mcp_client.py`,
  `scripts/validate_scene_*.py`. [PR #10](https://github.com/kalebcoleman/isaac-mcp/pull/10):
  `docs/plans/ASSET-SCENE-END-TO-END.md`.
- I1 [GENERIC-SIM-TOOLS.md](../GENERIC-SIM-TOOLS.md): tool contracts, safety rules (§4), API
  citations C1–C18 (§3.25) and risks (§7).
- I2 [ASSET-PRESETS.md](ASSET-PRESETS.md): the isolated-loader proposal; D2 is its trusted option.
- I3 [SCENE-TOOLS-AGENT-UX.md](SCENE-TOOLS-AGENT-UX.md): error envelopes, confirmations, long calls.
- I4 [ECOSYSTEM.md](../ECOSYSTEM.md): what other servers offer and why these areas matter.
- I5 [tool-authoring.md](../../mcp/docs/tool-authoring.md), [protocol.md](../../mcp/docs/protocol.md).
- I6 [`scripts/gen_host_catalog.py`](../../scripts/gen_host_catalog.py),
  [`scripts/check_docs.py`](../../scripts/check_docs.py), [`run.sh`](../../run.sh).
- I7 [TOOLS-HOST.md](../TOOLS-HOST.md) (`sim__launch`, screen tools) and
  [evidence/desktop-control-2026-10-31.md](../evidence/desktop-control-2026-10-31.md).

Installed Isaac Sim 6.2 (paths under `.venv/lib/python3.12/site-packages/isaacsim/exts/`, read
2026-10-31):

- S6 `isaacsim.core.experimental.prims` 1.12.1, `impl/articulation.py`: `num_dofs` 202, `dof_names`
  222, `is_physics_tensor_entity_valid` 636, `get_dof_limits` 772, `set_dof_position_targets` 1555,
  `set_dof_velocity_targets` 1685, `set_dof_efforts` 1811, `get_dof_positions` 2010,
  `get_dof_position_targets` 2055, `get_dof_velocities` 2113, `set_dof_max_efforts` 2922,
  `set_dof_gains` 3146, `get_dof_gains` 3242.
- S7 `isaacsim.storage.native` 1.12.2: `get_assets_root_path` in `nucleus.py:630`;
  `config/extension.toml:27` sets the default root to
  `https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.2`;
  `impl/extension.py:24` reads the `ISAACSIM_ASSET_ROOT` override.
- S15 `isaacsim.asset.importer.urdf` 3.11.12: `URDFImporterConfig` (`impl/config.py:24`),
  `URDFImporter.import_urdf` (`impl/converter.py:49, 95`), under `pip_prebundle/isaacsim/asset/importer/urdf/`.
- S16 Bundled sample URDFs: `isaacsim.asset.importer.urdf/data/urdf/robots/cobotta_pro_900/cobotta_pro_900.urdf`,
  `.../robots/cartpole/cartpole.urdf`, `.../objects/*.urdf`.
- S17 `isaacsim.asset.importer.mjcf` 3.13.0: `MJCFImporter.import_mjcf` (`impl/converter.py:34, 78`);
  samples `data/mjcf/nv_ant.xml`, `data/mjcf/nv_humanoid.xml`.

Reference implementations (pinned to the commits read for ECOSYSTEM.md; MIT unless noted; check the
license before reusing code, and credit what you adapt):

- S9 Tim0320/IsaacSim-MCP, 6.x runtime: [`adapters/v6_runtime/robots.py`](https://github.com/Tim0320/IsaacSim-MCP/blob/6f676da52618c652081598b222a0e69ab79b1094/isaac.sim.mcp_extension/isaac_sim_mcp_extension/adapters/v6_runtime/robots.py)
  (experimental `Articulation` at L80, robot library discovery L149, joint info L233, joint state
  L419, joint command L451).
- S10 GenRobo/isaac-sim-mcp: [`set_joint_targets_by_name`](https://github.com/GenRobo/isaac-sim-mcp/blob/0bb9c0712080a0fc02d97338e4568d10d58c7294/isaac_mcp/server.py#L717),
  [`get_articulation_info`](https://github.com/GenRobo/isaac-sim-mcp/blob/0bb9c0712080a0fc02d97338e4568d10d58c7294/isaac_mcp/server.py#L338),
  [`set_joint_drive_params`](https://github.com/GenRobo/isaac-sim-mcp/blob/0bb9c0712080a0fc02d97338e4568d10d58c7294/isaac_mcp/server.py#L770).
  Its Kit side uses the pre-5.0 `omni.isaac.core` API: copy the interface, not the code.
- S11 Milokucia/dex-isaac-mcp: [`mcp_server.py`](https://github.com/Milokucia/dex-isaac-mcp/blob/d04d7fbbb5df0654a1cab1252a5d54ec4f89b3c8/dex_isaac_mcp/mcp_server.py)
  (`sim_get_stats` tracking error, `sim_frame_robot`).
- S12 serionist/omniverse-mcp: [`extension/ovmcp.bridge/ovmcp/core/logs.py`](https://github.com/serionist/omniverse-mcp/blob/677849c2a9055449590f660e55838e553c7544a6/extension/ovmcp.bridge/ovmcp/core/logs.py)
  (file-based log reading, header regex, 1 MiB read cap).
- S13 Deadlock evidence and guard: whats2000 [`handlers/simulation.py` L404](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac.sim.mcp_extension/isaac_sim_mcp_extension/handlers/simulation.py#L404)
  and serionist [`tests/test_logs.py` L55](https://github.com/serionist/omniverse-mcp/blob/677849c2a9055449590f660e55838e553c7544a6/tests/test_logs.py#L55).
- S14 whats2000/isaacsim-mcp-server: [`handlers/robots.py`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac.sim.mcp_extension/isaac_sim_mcp_extension/handlers/robots.py)
  (curated library paths such as `/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd`).

NVIDIA documentation and skills:

- S20 [URDF Importer Extension](https://docs.isaacsim.omniverse.nvidia.com/latest/importer_exporter/ext_isaacsim_asset_importer_urdf.html)
  and [Tutorial: Import URDF](https://docs.isaacsim.omniverse.nvidia.com/latest/importer_exporter/import_urdf.html).
- S21 [URDF, MJCF and robot asset pipeline changes in 6.0](https://docs.isaacsim.omniverse.nvidia.com/latest/migration_guides/isaac_sim_6_0/urdf_mjcf_importer_exporter_pipeline.html).
- S22 [Isaac Sim 6.2 Python API index](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/py/index.html)
  and [`isaacsim.storage.native` (6.0)](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/py/source/extensions/isaacsim.storage.native/docs/index.html).
- S23 [Setup tips (6.0.1)](https://docs.isaacsim.omniverse.nvidia.com/6.0.1/installation/install_faq.html):
  outbound HTTPS to the S3 host is needed for the default cloud asset root.
- S24 NVIDIA Isaac Sim skills ([`skills/`](https://github.com/isaac-sim/IsaacSim/tree/5ef464e8d7853d7b937bc9316e999300276454df/skills)):
  `urdf-mjcf-to-usd-conversion`, `usd-articulation`, `physics-simulation`, `isaac-sim-assets`,
  `isaac-sim-troubleshooting`, `isaac-camera`.

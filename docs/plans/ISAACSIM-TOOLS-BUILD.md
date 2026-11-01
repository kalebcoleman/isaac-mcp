# Build plan: the `isaacsim` tool pack

**Status: plan, not built.** Decisions D1–D3 were made by the owner on 2026-10-31; the rest of this
document has not been reviewed by the owner yet. Written for an implementing agent. It turns the
High-priority areas of [ECOSYSTEM.md](../ECOSYSTEM.md#what-to-build-next) and phases 1–3 of the
[generic Sim design](../GENERIC-SIM-TOOLS.md) into eight milestones, one pull request each, every
one proven on a live Isaac Sim 6.2 session with screenshots and video. Work through the milestones
in order; do not start a milestone until the previous one meets its definition of done.

## 1. What gets built

A robot-agnostic Kit-side tool pack, `exts/isaacsim-tools`, that lets an agent:

| Area (ECOSYSTEM priority) | Milestone | Tools (`isaacsim__…`) |
|---|---|---|
| Pack health | M0 | `info` |
| Scene inspection (High) | M1 | `stage__info`, `stage__tree`, `prim__get`, `prim__find`, `prim__measure`, `physics__scene_info`, `camera__list` |
| Logs and diagnostics (High) | M2 | `logs__tail`, `logs__mark`, `logs__summary` |
| Playback and stepping (High) | M3 | `sim__state`, `sim__play`, `sim__pause`, `sim__stop`, `sim__step`, `body__state`, `raycast` |
| Robot state and joint control (High) | M4 | `articulation__list`, `articulation__info`, `articulation__state`, `articulation__set_targets`, `articulation__set_positions`, `articulation__set_drive`, `articulation__move` |
| Prim editing (High) and stage files | M5 | `prim__create`, `prim__set_transform`, `prim__set_attribute`, `prim__duplicate`, `prim__delete`, `physics__ensure_scene`, `physics__apply`, `stage__new`, `stage__save` |
| Assets, robots and environments (High) | M6 | `assets__sources`, `assets__list`, `assets__robot_library`, `assets__load_usd`, `assets__import_urdf`, `assets__import_mjcf`, `assets__add_robot` |
| Viewport capture | M7 | `capture__screenshot`, `capture__record_start`, `capture__record_stop`, `camera__frame` |

That is 45 tools. M0 builds the scaffolding and the proof harness; M7 ends with an end-to-end demo
on four different robots. Every tool name and parameter below is a starting contract: the
implementer may adjust them, but must update this table, the tool reference and the tests in the
same PR.

## 2. Decisions already made (owner, 2026-10-31)

| # | Decision | Consequence |
|---|---|---|
| D1 | **Namespace `isaacsim`**, not the design's `sim`. | The host already has `sim__launch/list/status/stop` for sessions. Canonical names are `isaacsim.<group>.<verb>` and appear in MCP as `isaacsim__<group>__<verb>` (the host rewrites dots, see `_mcp_name` in `mcp/isaac_mcp/server.py`). Owner id `isaacsim-tools`. Update `GENERIC-SIM-TOOLS.md` to say its `sim.*` names ship as `isaacsim.*`. |
| D2 | **Assets in trusted-operator mode, including the NVIDIA cloud library.** | Loads run in the user's normal Kit session from (a) folders the operator lists in `ISAACSIM_TOOLS_ASSET_DIRS`, default empty, (b) samples bundled with the installed Isaac Sim, and (c) the NVIDIA asset root (`get_assets_root_path()`, which defaults to the S3 library and needs outbound HTTPS). This is the "trusted-session integration" that [ASSET-PRESETS.md](ASSET-PRESETS.md) left to an owner decision. It is **not** a sandbox: a USD or URDF can reference other files and enabled extensions can run code. Document it that way, keep it separate from the isolated loader, and update ASSET-PRESETS.md's status to say so. Setting `ISAACSIM_TOOLS_CLOUD_ASSETS=0` turns the cloud source off. |
| D3 | **One pull request per milestone** (M0–M7). | Each PR has its own tests, docs and evidence. Stack them: branch `feat/isaacsim-tools-m<N>-<slug>` from the previous milestone's branch until that PR merges, then rebase on `main` and retarget. **M0 starts from `docs/agent-skill-ecosystem`** (PR #12, which adds the agent skill, ECOSYSTEM.md and this plan) until #12 merges, then rebases onto `main` the same way. Never push to `main` or merge; the owner merges. |

## 3. Rules for the implementing agent

1. Read [AGENTS.md](../../AGENTS.md), the [agent skill](../../.agents/skills/isaac-mcp/SKILL.md),
   [tool-authoring.md](../../mcp/docs/tool-authoring.md) and [protocol.md](../../mcp/docs/protocol.md)
   before M0.
2. **Use disposable simulators only.** Start them with `sim__launch` (enable the opt-in group with
   `ISAAC_MCP_LOCAL_TOOLS=all,sim_launch`) on ports 18766 and up, with a ROS domain no one else
   uses, or with `UR5E_DT_PORT=<port> ./run.sh --port <port>`. Verify PID and port with
   `status` before every scene change. Never touch the owner's session on 8766 or any bridge
   you did not start. Stop what you started with `sim__stop` at the end.
3. One GPU job at a time on the 16 GB card. Do not run Isaac Lab jobs during this work.
4. Handlers never call `app.update()`, `world.step()` or `world.render()`. Long work awaits
   `omni.kit.app.get_app().next_update_async()`. `isaac_mcp.marks` must report no pump or opaque
   handler for the pack (`GENERIC-SIM-TOOLS.md` §2.4).
5. **Never install a Python log callback** (`omni.log` message consumers, `carb.logging`
   loggers, the log event stream, `error_log` events). Two other servers deadlocked Kit that
   way when PhysX logged from worker threads (sources S12, S13). M2 reads Kit's log file instead.
6. Every response is bounded JSON with `status`, plus `code`, `message` and `next_action` on
   errors. No unbounded tracebacks. Distinguish invalid arguments, missing prerequisites (no
   stage, physics not initialised), missing resources, timeouts and partial effects
   ([SCENE-TOOLS-AGENT-UX.md](SCENE-TOOLS-AGENT-UX.md#cross-cutting-design), point 3).
7. Do not change `exts/ur5e-dt` behaviour. The UR5e is just one robot the new tools must work on.
8. If a planned API does not behave as documented here, record what you observed in the
   milestone's evidence, adapt, and update this plan in the same PR. Do not paper over it.
9. Stop and ask the owner only for things this plan does not settle: credentials, a different
   machine, deleting anything you did not create, or a decision that changes a public contract
   from §1.

## 4. Architecture (built in M0)

```
exts/isaacsim-tools/
  config/extension.toml      # deps: isaac.mcp.bridge, omni.kit.uiapp (same keys as ur5e-dt)
  docs/README.md, docs/CHANGELOG.md
  isaacsim_tools/
    __init__.py              # from .extension import *
    extension.py             # IExt: register owner "isaacsim-tools", namespace "isaacsim"
    registry.py              # MCP_TOOL_REGISTRY: a pure dict literal, readable with ast
    _state.py                # live handles that must survive reload_tools
    policy.py                # stdlib only: paths, caps, refusals, envelope helpers
    stage_tools.py           # M1, M5
    log_tools.py             # M2 (stdlib file reader + Kit setting lookup)
    sim_tools.py             # M3
    articulation_tools.py    # M4
    authoring_tools.py       # M5
    asset_tools.py           # M6
    capture_tools.py         # M7
```

- **Registration** mirrors the twin's [`bridge_binding.py`](../../exts/ur5e-dt/ur5e_dt/bridge_binding.py):
  `get_mcp_bridge().register_owner("isaacsim-tools", "isaacsim", ...)`, then one
  `ToolDefinition` per registry entry with handler `_cmd_<name with dots as _>`. Remove only
  your own registrations on shutdown; never stop the shared socket (`GENERIC-SIM-TOOLS.md` §2.3).
- **Loading:** add `--enable isaacsim-tools` next to `--enable ur5e-dt` in `run.sh` (line ~129),
  which `sim__launch` also uses. Add a `--no-isaacsim-tools` flag only if a test needs it.
- **Offline listing:** extend `scripts/gen_host_catalog.py` to also write
  `mcp/isaac_mcp/catalogs/isaacsim.json` from `registry.py`, so the host lists the pack before
  Kit starts, like the UR5e tools (`mcp/isaac_mcp/host_catalog.py`).
- **Docs check:** extend `scripts/check_docs.py` so `expected_tools()` reads `registry.py` for a
  new `docs/TOOLS-ISAACSIM.md`, the stale-name check covers the `isaacsim__` prefix (also in skill
  files), and `TIER_ROWS` / `tier_counts()` gain an `isaacsim` tier. Add the tier row to
  AGENTS.md and fix every count sentence there; update `PHRASES` in
  `mcp/tests/test_check_docs.py` accordingly.
- **Tests without Kit:** add `exts/isaacsim-tools` to the paths in `mcp/tests/conftest.py`. Unit
  tests cover `policy.py`, `log_tools.py`'s parser, registry shape (description present, known
  parameter types, `isaacsim.` prefix, matching `_cmd_` handler), and the marks roster.
  `Usd.Stage.CreateInMemory()` covers tree and path logic where `pxr` is importable
  (`GENERIC-SIM-TOOLS.md` §5.1).
- **Live checks:** one script per milestone in `scripts/checks/isaacsim_m<N>_*.py` run through
  `scripts/bridge_exec.py`, plus the MCP scenario below.

## 5. Proof: what every milestone must deliver

The owner wants to see each milestone working. Evidence goes through the real MCP server, not the
bridge CLI, so it also proves names, schemas and the gateway.

**Harness (built in M0):** `scripts/evidence/mcp_scenario.py` starts `isaac-mcp` over stdio with
the MCP Python SDK (`mcp.ClientSession` + `stdio_client`; `mcp` is already a dependency), runs a
JSON scenario of steps `{tool, args, expect, save_as}`, and writes:

- `transcript.jsonl`: every call, arguments, result (secrets and home paths redacted), duration;
- `report.md`: a table of steps with PASS/FAIL, assertion text, timings, environment block;
- screenshots taken through the host `screenshot(window_id=…)` tool of the disposable Kit
  window, downscaled to 1280 px wide.

Scenarios live in `scripts/evidence/scenarios/m<N>.json` and are rerunnable. Assertions are
simple JSON-path comparisons with tolerances, written down in the scenario.

**Per milestone, commit** `docs/evidence/isaacsim-tools/M<N>/` with `report.md`, `transcript.jsonl`
and PNG screenshots (each ≤ 400 KB, ≤ 15 per milestone). **Videos** (MP4, 1280 px, 10–40 s) are not
committed: upload them with `gh release upload` to a **draft** release named
`isaacsim-tools-evidence` (create it once with `gh release create isaacsim-tools-evidence --draft`).
Record each video's URL, SHA-256, duration and frame count (`ffprobe`) in the report. Record videos
with `start_screen_recording(window_id=…, full_screen=false)` until M7 exists, then also with the
pack's own `capture__record_start`.

**The environment block** in every report: OS, display, GPU and driver (`nvidia-smi`), Isaac Sim
version, Kit PID and bridge port, isaac-mcp git SHA, enabled tool groups, and which robots and
assets were used.

**Definition of done for every milestone:**

1. Tools implemented with bounded, documented responses and the error codes from rule 6.
2. Unit tests added; `pytest -q` from `mcp/` passes (CI command in `.github/workflows/ci.yml`).
3. `python3 scripts/check_docs.py` and `python3 scripts/gen_host_catalog.py --check` pass.
4. `docs/TOOLS-ISAACSIM.md` documents every new tool (parameters, returns, side effects, an
   example); AGENTS.md counts and the agent skill are updated.
5. The live check script passes on a disposable 6.2 session, and the MCP scenario passes with
   every listed screenshot and video captured and inspected by the agent.
6. `report.md` states what was **not** verified.
7. One PR, linked to the previous one, with a body that embeds the screenshots, links the
   videos, and lists failures honestly. Relative image links do not render in a PR body: embed
   each screenshot with an absolute link pinned to the commit,
   `https://github.com/kalebcoleman/isaac-mcp/blob/<sha>/docs/evidence/isaacsim-tools/M<N>/<file>.png?raw=true`.

## 6. Milestones

### M0: Scaffold, harness and an empty pack

**Build:** the layout in §4; registry with one tool, `isaacsim.info` (pack version, Isaac Sim
and Kit versions, whether a stage is open, the log file path, and which asset sources are
enabled); `run.sh` enable line; catalog, docs-check and conftest changes; `docs/TOOLS-ISAACSIM.md`
skeleton; the evidence harness from §5; `docs/evidence/isaacsim-tools/README.md` explaining the
layout.

**Prove:** launch a disposable session, `refresh_tools`, and show that `isaacsim__info` and all 23
`ur5e_dt__*` tools are listed from the same bridge. `reload_tools isaacsim_tools` keeps both
packs reachable. Screenshot: the Kit window with the session running. No video needed.

### M1: Read-only scene inspection

**Build** (design §3 rows 1–4, 5, 10, plus `prim.measure` from the ecosystem gap):

| Tool | Parameters | Returns |
|---|---|---|
| `isaacsim.stage.info` | — | stage URL and state, default prim, up axis, meters per unit, prim count (capped 100k, `truncated`), playing, time |
| `isaacsim.stage.tree` | `root="/World"`, `depth=3`, `max_nodes=2000`, `types=null`, `include_inactive=false` | nodes `{path,name,type,active,children}`, count, `truncated` |
| `isaacsim.prim.get` | `path`, `attributes=null`, `max_attributes=64`, `include_bounds=true` | type, active, applied schemas, world pose, world bounds, attributes |
| `isaacsim.prim.find` | `regex`, `type=null`, `max=500` | paths, count, `truncated` (Python `re.fullmatch` + `GetTypeName`, not the deprecated helper; design §3.24) |
| `isaacsim.prim.measure` | `path_a`, `path_b` | centre distance, axis-aligned gap between world bounds |
| `isaacsim.physics.scene_info` | — | physics scene path, gravity, steps per second, dt; nulls when no scene |
| `isaacsim.camera.list` | — | cameras with focal length and clipping range, active viewport camera |

Use the APIs cited in design §3.25 (C1, C2, C8, C9, C10, C11, C16, C17). Reject relative and
invalid paths; `Sdf.Path.IsValidPathString` returns a bool-like object, so normalise it (§3.24).

**Prove:** in a disposable session, run `ur5e_dt__quick_start`, then show the tree, `prim__get`
on the robot base and a gripper link with world bounds, `prim__find` for all `Xform`s under
`/World`, and `prim__measure` between the gripper and table. Also run against a plain new stage
with one cube to show no UR5e assumptions. Compare every number with a `run_python` reading of
the same prims (tolerance 1e-6). Screenshots: the scene, with the Stage panel expanded to match
the returned tree. No video needed.

### M2: Logs and diagnostics

**Build:**

| Tool | Parameters | Returns |
|---|---|---|
| `isaacsim.logs.tail` | `count=50` (≤ 500), `min_level="warn"`, `channel=null`, `search=null`, `cursor=null` | entries `{timestamp, level, channel, message}`, `next_cursor`, `log_file`, `truncated` |
| `isaacsim.logs.mark` | — | a cursor at the current end of the log, to ask "what went wrong since now?" |
| `isaacsim.logs.summary` | `cursor` | counts by level and by channel since the cursor, the first and last error |

Read the file named by the Kit setting `/log/file`; a cursor is a byte offset plus the file's
inode and size, so rotation or truncation is detected and reported. Read at most 1 MiB per call
from the end. Parse header lines (`[Warning] [channel] message`), join continuation lines, cap
each message at 2 KB, and redact obvious secrets (tokens, `Authorization:` values). Base the parser
on serionist's `core/logs.py` (S12), written fresh (check its license first and credit it). Add a
unit test that fails if the pack's source contains any log-hook name from S13's guard list.

**Prove:** mark, then cause known messages with `run_python`
(`carb.log_warn("isaacsim-tools M2 probe")`, `carb.log_error(...)`) and a real error (reference a
missing USD file); `logs__summary` must count them and `logs__tail` must return them with the
right levels and channels. Show a filter by channel and by search text. Then load a
robot in a playing scene for 30 s and confirm the calls stay responsive (no deadlock): this is the
failure S13 reports. Screenshot: the Kit Console panel next to the matching JSON. No video needed.

### M3: Playback and stepping

**Build** (design rows 6, 7, 9, 20–23):

| Tool | Parameters | Returns |
|---|---|---|
| `isaacsim.sim.state` | — | playing, stopped, paused, time, physics dt, time codes per second |
| `isaacsim.sim.play` / `pause` / `stop` | — | new state and time; stop reports the rewound time |
| `isaacsim.sim.step` | `frames=1` (≤ 600) | frames advanced and new time; refused while playing |
| `isaacsim.body.state` | `paths` (≤ 64), `velocities=true` | world pose, linear and angular velocity, mass |
| `isaacsim.raycast` | `origin`, `direction`, `max_distance=100`, `all=false` | hits with position, normal, distance, collider, rigid body |

Use `omni.timeline` (C2), `SimulationManager` for paused and stepping (C6), the experimental
`RigidPrim` (C5) and the PhysX scene-query interface (C12). Before the first play, tensor views may
not exist: return `code: "physics_not_initialized"` with `next_action` naming `sim__play` or
`sim__step`, not a stack trace (design §7, risk 2). The time limits are in design §4.4.

**Prove:** a new stage with a ground plane and a cube 1 m up. `sim__step 60` from paused, then
`body__state` shows the cube falling, compared with the free-fall estimate for that time (expect
a small solver difference; record it). Play, pause, step 10, stop, and show the times. Raycast
down from 2 m hits the cube top or the ground within 1 cm. Video: about 10 s of the cube dropping
during play, with the JSON of each call shown in the report.

### M4: Robot state and joint control

**Build** on `isaacsim.core.experimental.prims.Articulation` (S6), the same API Tim0320's 6.x
adapter uses (S9). The UR5e twin still uses the deprecated `isaacsim.core.prims`; do not copy it.

| Tool | Parameters | Returns |
|---|---|---|
| `isaacsim.articulation.list` | `root="/World"` | prims with `ArticulationRootAPI`, DOF count |
| `isaacsim.articulation.info` | `path` | DOF names, types, limits (rad or m), drive type, stiffness, damping, max effort |
| `isaacsim.articulation.state` | `path`, `dofs=null` | positions, velocities, position and velocity targets, efforts, by DOF name |
| `isaacsim.articulation.set_targets` | `path`, `positions={name: value}` or `velocities={…}`, `clamp=false` | applied targets; values outside limits are refused unless `clamp=true`, which reports what was clamped |
| `isaacsim.articulation.set_positions` | `path`, `positions={…}` | teleports joints (state reset, not motion); warns that physics is bypassed |
| `isaacsim.articulation.set_drive` | `path`, `dofs`, `stiffness`, `damping`, `max_effort` | old and new values read back (Tim0320's `set_joint_drive_config` reads back too, S9) |
| `isaacsim.articulation.move` | `path`, `positions={…}`, `max_frames=600`, `tolerance=0.01` | steps while paused until every joint is within tolerance or the limit; returns per-joint error, frames used, `settled` |

Name-based targets follow GenRobo's `set_joint_targets_by_name` (S10); `move`'s tracking error
follows dex-isaac-mcp's `sim_get_stats` (S11). Unknown DOF names return the valid names (capped).
Units are explicit in every response: rad and rad/s for revolute joints, m and m/s for prismatic.

**Prove on two robots,** to show it is robot-agnostic: the UR5e from `ur5e_dt__quick_start`, and
the Cobotta Pro 900 imported with `run_python` from the URDF bundled with Isaac Sim (S16) until
M6 provides the tool. For each: `info`, a `move` to a pose about 0.5 rad from home on three joints
(settled, final error ≤ 0.01 rad), a `state` read matching the targets, `set_drive` changing one
joint's stiffness with read-back, an out-of-limit target refused, then the same target with
`clamp=true`. Video: each robot moving to the pose and back (about 20 s total). Screenshots: start
and end poses.

### M5: Prim editing, physics setup and stage files

**Build** (design rows 14–19, 24, and §4.2–4.3 refusals):

| Tool | Notes |
|---|---|
| `isaacsim.prim.create` | `path`, `type`, `transform`, `attributes`, `on_conflict="error"`; undoable command (C13) |
| `isaacsim.prim.set_transform` | reuses existing xform ops, never stacks new ones (design row 15) |
| `isaacsim.prim.set_attribute` | typed, reads back old and new; refuses engine-owned attributes (§4.3) |
| `isaacsim.prim.duplicate` | refuses articulation and physics roots |
| `isaacsim.prim.delete` | `paths` (≤ 200), `confirm=true` required; full refusal table in design §4.2 |
| `isaacsim.physics.ensure_scene` | creates a `PhysicsScene` and a ground plane only when missing; reports what it created |
| `isaacsim.physics.apply` | collider, rigid body, mass; refuses when an ancestor is already a rigid body |
| `isaacsim.stage.new` | `confirm=true` required; reports what was discarded (unsaved changes) |
| `isaacsim.stage.save` | basename only under the output root, or in place for the open file (design §4.1) |

Output root: `ISAACSIM_TOOLS_OUTPUT_ROOT`, default `~/.local/share/isaac-mcp/isaacsim-tools/`.

**Prove:** from `stage__new`, build a small scene only with these tools (ground, table from a cube,
three coloured primitives with physics, one duplicated), play it with M3 and show the objects
falling and resting. Show refusals: deleting `/World`, deleting without `confirm`, setting
`xformOpOrder`, a `../` filename. Save the stage, open it again with `run_python`, and compare the
tree with M1 tools. Video: the scene being built step by step and then simulated (about 30 s).

### M6: Assets, robots and environments

**Build** (decision D2):

| Tool | Notes |
|---|---|
| `isaacsim.assets.sources` | lists sources: each `ISAACSIM_TOOLS_ASSET_DIRS` root by id (format `id=path` separated by `os.pathsep`), `bundled` (Isaac Sim's importer sample data), and `nvidia` (the resolved asset root, with reachability) |
| `isaacsim.assets.list` | `source`, `relative_dir=""`, `kinds=["usd","urdf","mjcf"]`, `cursor`; bounded, paginated, no symlink escapes, relative paths only |
| `isaacsim.assets.robot_library` | browses `<asset root>/Isaac/Robots/<maker>/<model>/` with `omni.client.list` (bounded, cached for the session), as Tim0320 does (S9); falls back to a curated table (below) |
| `isaacsim.assets.load_usd` | `source`, `path`, `prim_path`, `pose`, `on_conflict="error"`; adds a reference and reports default prim, units, up axis, articulations found |
| `isaacsim.assets.import_urdf` | `URDFImporter(URDFImporterConfig(...)).import_urdf()` (S15), then reference the produced USD; the old `URDFParseAndImportFile` command was removed in 6.0 (S21) |
| `isaacsim.assets.import_mjcf` | `MJCFImporter` (S17), same pattern |
| `isaacsim.assets.add_robot` | a library id from `robot_library` → `load_usd` from the `nvidia` source |

**First step of M6, before building on it:** in a live session, try `omni.client.list` on
`<asset root>/Isaac/Robots/` and record the result in the evidence. Listing an HTTPS S3 root may not
be supported (`get_assets_root_path` has an `accept_unsupported` flag for providers without path
metadata, S7). If it fails, `robot_library` uses a curated table of known robot paths, like
whats2000's hard-coded table (S14), checking each entry with `omni.client.stat` and marking
unreachable ones; check whether the `isaac-sim-assets` skill (S24) documents an availability
manifest that could replace the table.

Paths are always `source` + relative path; absolute paths and URLs from the caller are refused.
Report unit and up-axis mismatches instead of silently fixing them (ASSET-PRESETS.md, "Units,
physics and compatibility"). Cloud loads may take seconds: bound them and say so on timeout. Write
importer outputs under the output root. Add a "Trusted-operator mode" section to
`TOOLS-ISAACSIM.md` saying plainly that this is not a sandbox (D2).

**Prove** with four robots from three sources: the UR5e USD from a trusted folder configured for
the test (`exts/ur5e-dt/assets/robot`), the Cobotta Pro 900 URDF and the `nv_ant` MJCF from
`bundled`, and the Franka Panda from `nvidia` through `robot_library` and `add_robot`. Place them
side by side, list them with `articulation__list`, and move one joint on each with M4. Show
refusals: an absolute path, `..`, an unknown source, the cloud source with
`ISAACSIM_TOOLS_CLOUD_ASSETS=0`. Video: the four robots loading and each moving (about 30 s).
Screenshot: all four in one frame.

### M7: Capture, then the end-to-end demo

**Build** (design rows 11–13 and the auto-framing idea from dex-isaac-mcp, S11):

| Tool | Notes |
|---|---|
| `isaacsim.capture.screenshot` | `filename`, `camera=null`, `width`, `height`; restores the active camera afterwards (design row 11) |
| `isaacsim.capture.record_start` / `record_stop` | MP4 through `omni.kit.capture.viewport` (C4), one capture at a time, frame caps in §4.4 |
| `isaacsim.camera.frame` | points the viewport camera so a prim's world bounds fill the frame |

Test both windowed and headless sessions and record what happens when there is no viewport (design
§3.26 and §7, risk 3).

**Final demo (the proof for the whole plan),** one scenario through MCP from an empty stage:

1. `stage__new` → `physics__ensure_scene`.
2. Load the UR5e (trusted folder), Cobotta (bundled URDF), Franka (cloud) and the ant (bundled
   MJCF) with M6 tools; `camera__frame` on `/World`.
3. `logs__mark`; `stage__tree` and `articulation__list` show all four.
4. `capture__record_start`; `articulation__move` each robot through two poses; `sim__play` for
   3 s so the ant settles; `capture__record_stop`.
5. `articulation__state` on each, within tolerance; `logs__summary` reports any errors since the
   mark (expected: none from the tools; list any from assets).
6. `stage__save`; `stage__new`; reopen the saved file and compare the tree.
7. `capture__screenshot` of the final scene.

Deliver the pack's own recording **and** a host screen recording of the same run, so the video
does not rely only on the tool being tested. Update ECOSYSTEM's capability statuses
(`scripts/ecosystem_capabilities.py`, the "isaac-mcp today" column, then regenerate), the agent
skill, `GENERIC-SIM-TOOLS.md` (status: implemented, with links to the evidence), and
`docs/VERIFICATION.md`.

## 7. Risks to watch

| Risk | Where | What to do |
|---|---|---|
| Tensor views not initialised before the first step | M3, M4 | Detect with `is_physics_tensor_entity_valid()` (S6, line 636) and return `physics_not_initialized` |
| Kit deadlock from log callbacks | M2 | File reading only (rule 5) and the guard test |
| Cloud assets slow or unreachable | M6 | Time-bounded `omni.client` calls; report reachability in `assets.sources` |
| Directory listing unsupported on the S3 asset root | M6 | Probe first; fall back to a curated, `stat`-checked robot table |
| Importer API drift between 6.x releases | M6 | Pin to the installed 6.2 API (S15, S17), cite versions in the docs |
| Capture with no viewport | M7 | Test windowed and headless; refuse clearly |
| Out-of-limit or unstable joint targets | M4 | Refuse by default; `move` has a frame limit and reports `settled=false` |
| Tool count changes break doc checks | all | Update AGENTS.md and `test_check_docs.py` in the same PR (§4) |

## 8. Sources

Source ids are stable labels used in the text above; the numbering has gaps on purpose.

Internal (this repository):

- I1 [GENERIC-SIM-TOOLS.md](../GENERIC-SIM-TOOLS.md): the tool contracts, safety rules (§4), test
  plan (§5), API citations C1–C18 (§3.25) and risks (§7) this plan builds on.
- I2 [ASSET-PRESETS.md](ASSET-PRESETS.md): the isolated-loader proposal; D2 is its trusted-session
  option.
- I3 [SCENE-TOOLS-AGENT-UX.md](SCENE-TOOLS-AGENT-UX.md): error envelopes, confirmations and
  long-call guidance.
- I4 [ECOSYSTEM.md](../ECOSYSTEM.md): what other servers offer and why these areas are High.
- I5 [tool-authoring.md](../../mcp/docs/tool-authoring.md), [protocol.md](../../mcp/docs/protocol.md):
  handler contract and bridge protocol.
- I6 [`exts/ur5e-dt/ur5e_dt/bridge_binding.py`](../../exts/ur5e-dt/ur5e_dt/bridge_binding.py),
  [`scripts/gen_host_catalog.py`](../../scripts/gen_host_catalog.py),
  [`scripts/check_docs.py`](../../scripts/check_docs.py), [`run.sh`](../../run.sh),
  [`scripts/bridge_exec.py`](../../scripts/bridge_exec.py): registration, offline catalog, doc
  checks, launch and live-check patterns.
- I7 [TOOLS-HOST.md](../TOOLS-HOST.md) (`sim__launch`, screen tools) and
  [evidence/desktop-control-2026-10-31.md](../evidence/desktop-control-2026-10-31.md) (the evidence
  style to follow).

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

Reference implementations (pinned to the commits read for ECOSYSTEM.md; check each license before
reusing code, and credit what you adapt):

- S9 Tim0320/IsaacSim-MCP, 6.x runtime: [`adapters/v6_runtime/robots.py`](https://github.com/Tim0320/IsaacSim-MCP/blob/6f676da52618c652081598b222a0e69ab79b1094/isaac.sim.mcp_extension/isaac_sim_mcp_extension/adapters/v6_runtime/robots.py)
  (experimental `Articulation` at L80, robot library discovery L149, joint info L233, joint state
  L419, joint command L451) and [`adapters/v6_runtime/assets.py`](https://github.com/Tim0320/IsaacSim-MCP/blob/6f676da52618c652081598b222a0e69ab79b1094/isaac.sim.mcp_extension/isaac_sim_mcp_extension/adapters/v6_runtime/assets.py#L37)
  (URDF import). MIT.
- S10 GenRobo/isaac-sim-mcp: tool shapes for [`list_robots`](https://github.com/GenRobo/isaac-sim-mcp/blob/0bb9c0712080a0fc02d97338e4568d10d58c7294/isaac_mcp/server.py#L288),
  [`get_articulation_info`](https://github.com/GenRobo/isaac-sim-mcp/blob/0bb9c0712080a0fc02d97338e4568d10d58c7294/isaac_mcp/server.py#L338),
  [`get_joint_states`](https://github.com/GenRobo/isaac-sim-mcp/blob/0bb9c0712080a0fc02d97338e4568d10d58c7294/isaac_mcp/server.py#L455),
  [`set_joint_targets_by_name`](https://github.com/GenRobo/isaac-sim-mcp/blob/0bb9c0712080a0fc02d97338e4568d10d58c7294/isaac_mcp/server.py#L717),
  [`set_joint_drive_params`](https://github.com/GenRobo/isaac-sim-mcp/blob/0bb9c0712080a0fc02d97338e4568d10d58c7294/isaac_mcp/server.py#L770).
  Its Kit side uses the pre-5.0 `omni.isaac.core` API: copy the interface, not the code. MIT.
- S11 Milokucia/dex-isaac-mcp: [`mcp_server.py`](https://github.com/Milokucia/dex-isaac-mcp/blob/d04d7fbbb5df0654a1cab1252a5d54ec4f89b3c8/dex_isaac_mcp/mcp_server.py)
  (`sim_get_stats` tracking error, `sim_frame_robot` auto-framing). MIT.
- S12 serionist/omniverse-mcp: [`extension/ovmcp.bridge/ovmcp/core/logs.py`](https://github.com/serionist/omniverse-mcp/blob/677849c2a9055449590f660e55838e553c7544a6/extension/ovmcp.bridge/ovmcp/core/logs.py)
  (file-based log reading, header regex, 1 MiB read cap, explanation of the deadlock). MIT.
- S13 The deadlock evidence and guard: whats2000 [`handlers/simulation.py` L404](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac.sim.mcp_extension/isaac_sim_mcp_extension/handlers/simulation.py#L404)
  (`_ensure_log_listener`: a Python consumer wedged Kit while spawning a Franka FR3 on 5.1) and
  serionist [`tests/test_logs.py` L55](https://github.com/serionist/omniverse-mcp/blob/677849c2a9055449590f660e55838e553c7544a6/tests/test_logs.py#L55)
  (the hook names to forbid). MIT.
- S14 whats2000/isaacsim-mcp-server: [`handlers/robots.py`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac.sim.mcp_extension/isaac_sim_mcp_extension/handlers/robots.py)
  (library paths such as `/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd`) and
  [`tools/robots.py`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/robots.py#L74)
  (`list_available_robots`, `refresh_robot_library`). MIT.

NVIDIA documentation and skills:

- S20 [URDF Importer Extension](https://docs.isaacsim.omniverse.nvidia.com/latest/importer_exporter/ext_isaacsim_asset_importer_urdf.html)
  and [Tutorial: Import URDF](https://docs.isaacsim.omniverse.nvidia.com/latest/importer_exporter/import_urdf.html)
  ("latest" pages; check parameter names against S15).
- S21 [URDF, MJCF and robot asset pipeline changes in 6.0](https://docs.isaacsim.omniverse.nvidia.com/latest/migration_guides/isaac_sim_6_0/urdf_mjcf_importer_exporter_pipeline.html)
  (`URDFParseAndImportFile` removed; use `URDFImporter(config).import_urdf()`).
- S22 [Isaac Sim 6.2 Python API index](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/py/index.html)
  and [`isaacsim.storage.native` (6.0)](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/py/source/extensions/isaacsim.storage.native/docs/index.html)
  (asset root precedence; S7 confirms the 6.2 default locally).
- S23 [Setup tips (6.0.1)](https://docs.isaacsim.omniverse.nvidia.com/6.0.1/installation/install_faq.html):
  outbound HTTPS to the S3 host is required for the default cloud asset root.
- S24 NVIDIA Isaac Sim skills ([`skills/`](https://github.com/isaac-sim/IsaacSim/tree/5ef464e8d7853d7b937bc9316e999300276454df/skills)):
  `urdf-mjcf-to-usd-conversion`, `usd-articulation`, `physics-simulation`, `isaac-sim-assets`,
  `isaac-sim-troubleshooting`. Read the matching one before each milestone.

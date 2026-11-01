# Project USD and generic scene tools

**Implementation preview; end-to-end acceptance incomplete.** Thirty-seven catalogued tools implement
trusted local/shared-mount projects and explicit scene operations. Untrusted isolation, authenticated
remote upload and named presets are **unavailable**, not silently substituted. Bounded USDZ support is available only in the trusted-project profile.
See [evidence](evidence/project-scene/README.md) for actual tests and missing gates.
The earlier [design](GENERIC-SIM-TOOLS.md) is broader than this implementation.

## One-time approval

The user runs this outside the model-facing tool interface, with the host package installed:

```bash
python -m isaac_mcp.project_assets --config /absolute/private/approval.json \
  --root-id project --root /absolute/project/assets \
  --staging /absolute/private/staging --trust-project
```

Set `ISAAC_MCP_ASSET_CONFIG=/absolute/private/approval.json` in **both** the MCP host and
Kit launch environments, then restart them. `run.sh` enables `sim-tools` and retains all UR5e
behavior. Direct Kit users enable `sim-tools` beside `isaac.mcp.bridge` from this checkout.
No manifest or hand-written Python is needed. Approval is explicit configuration, not a universal
MCP approval dialog. No tool can grant roots. Launching outside the project works because paths
are absolute. Default configuration grants no roots. Existing approval files are never overwritten
by the helper; the user can edit `roots` to add narrowly scoped, nonoverlapping folders.

Only use this for files and dependencies the user trusts with the OS account's permissions.
The raw bridge remains powerful and unauthenticated: keep it on a trusted local interface.
The broker is a same-user stdio service, **not** a remote multi-user service. An agent path
is not evidence of a Kit mount. Host and Kit must share the configured staging location; the
receiver verifies snapshot hashes before parsing. A shared mount must be deliberately configured.
A missing/different mount produces an error, never an implicit transfer via `put_file`.

## Tools

All names below remain discoverable offline by default, subject to existing namespace filters.
Set `ISAAC_MCP_SCENE_TOOLS=0` on the host server and restart that MCP connection to hide
all 37 generic tools, including live bridge discovery. This per-server preference preserves
UR5e, Isaac Lab, desktop and managed-session tools; it is not an enforcement boundary.
`ISAAC_MCP_LOCAL_TOOLS` does not select these catalogued tools. `assets__roots` and
`assets__scan` and `assets__search` execute in the host broker even without Kit. Other tools explain an absent
bridge/extension. Existing `sim__launch/list/status/stop` remain separate optional process tools.

| Tool | Contract |
|---|---|
| `assets__roots` | List launch-approved project roots and capability refusals. Filename access only; trusted projects are NOT sandboxed. |
| `assets__scan` | Bounded filename-only USD discovery on the MCP host. No USD parser. Results are untrusted data. |
| `assets__info` | Snapshot and inspect a USER-TRUSTED project on the selected Kit. Native USD parsing is NOT sandboxed. Refuses untrusted profiles and unresolved/outside snapshot dependencies. |
| `assets__import_usd` | Import a USER-TRUSTED project snapshot below a new wrapper prim, preserving the source and current stage. Requires stopped timeline, matching units/up-axis, explicit target and no conflict. No URLs or arbitrary asset attributes. |
| `sim__stage__info` | Read process incarnation, stage identity, units, timeline and bounded prim count. Start here before scene operations. |
| `sim__stage__tree` | Bounded stage hierarchy including inactive prims. Text is untrusted data. |
| `sim__prim__get` | Read local/world matrices and authored physics at default USD time. Runtime physics readiness remains unknown. |
| `sim__prim__find` | Bounded literal path substring/type search (not regex). Includes inactive prims. |
| `sim__prim__create` | Create a Cube, Sphere, Xform, or PhysicsScene in session layer. No arbitrary references/attributes. Stopped timeline, parent and no-conflict required. Dimensions are stage units. Also supports Cylinder, Capsule and Cone; size is diameter/height before optional scale. |
| `sim__prim__set_transform` | Set a static local/world pose for a prim created by this pack. Refuses playing/paused physics and foreign, animated or complex transform stacks. No claim of runtime teleportation. |
| `sim__physics__inspect` | Bounded authored collision/body/mass/joint/articulation report. Does not assert initialized or stable physics. |
| `sim__body__state` | Read initialized rigid-body world pose on selected PhysX or qualified Newton backend; fails when stopped/uninitialized or not a body. Position in stage units, quaternion [w,x,y,z]. Optional runtime velocities/mass from tensor API; no authored-USD fallback. |
| `sim__timeline__control` | Explicit play, pause or stop. Stop rewinds physics. Leases the session edit target for physics writeback and restores it on Stop. Never pumps the Kit update loop; exact stepping uses sim.simulation.step. |
| `sim__stage__save` | Export a flattened copy, including session edits, to a new basename in configured output root. Never overwrite or switch the current stage. Paused Newton copies runtime rigid-body poses into the export only (<=256 bodies), not a solver checkpoint. External texture paths remain dependencies. |
| `sim__viewport__frame` | Select and frame an existing prim in the active viewport. Does not change the scene; capture the verified app window using screenshot. |
| `assets__search` | Fresh filename search across approved project/library roots. No network discovery, semantic service, manifest or fixed robot count. Refresh by calling again. |
| `sim__prim__delete` | Delete one pack-owned subtree from the session layer only. Requires stopped physics and confirm=true; exact owned sensors release their resources; sensor-containing subtrees and articulation roots are refused. |
| `sim__prim__clone` | Clone a pack-created simple leaf primitive into a new path, preserving session-layer attributes. Refuses references, children, sensors and articulations. |
| `sim__stage__clear_owned` | Remove only pack-created non-sensor, non-articulated subtrees, preserving other scene content. Explicit stopped target and confirm required. Returns removed/refused lists. |
| `sim__light__create` | Create a SphereLight, DistantLight or DomeLight without asset-valued textures. Pose in stage units, orientation wxyz. |
| `sim__light__modify` | Change intensity/color of a pack-owned light on a stopped timeline. |
| `sim__material__create` | Create a UsdPreviewSurface or physics material with finite numeric parameters. No asset-valued attributes or texture URLs. |
| `sim__material__apply` | Bind a pack-created material to an existing geometry prim in the session layer, preserving source files. |
| `sim__physics__configure` | Set explicitly selected PhysicsScene gravity and time step while stopped. No implicit scene creation or GPU/backend switch. |
| `sim__joints__inspect` | Inspect bounded authored joint limits/drives below an articulation, reporting USD degrees versus stage units. No runtime-state claim. |
| `sim__joints__drive` | Set one revolute/prismatic joint drive target and gains in session layer; stopped timeline only. Angular target radians; gains use USD degrees-based convention. |
| `sim__joints__state` | Read initialized articulation DOF positions/velocities on selected PhysX or qualified Newton tensor API. Angular radians, linear stage units; no authored-target fallback. |
| `sim__simulation__step` | Advance 1..120 exact steps on the selected PhysX or qualified Newton backend while PAUSED and initialized. No app-update pumping or engine fallback. Return measured time delta, runtime rigid-body poses and articulation DOFs; Newton publishes poses to Fabric. |
| `sim__camera__create` | Create a persistent RTX camera on a stopped timeline. Build identity and owned-CPU Replicator capabilities are checked; experimental.rtx is not required. USD axes: -Z forward, +Y up. Needs rendered frames; width/height capped at 1024. |
| `sim__camera__capture` | Capture RTX camera RGB into an owned CPU buffer and a new PNG in configured output root. Default waits up to five seconds for the first render completion, four further frames AND a matching renderer CPU transfer; no stale annotator fallback. Frame-number baseline is the first completion observed after the request, so an immediately created camera needs no caller-side warm-up. Returns dimensions/hash. No implicit Play or atomic physics-frame synchronization. |
| `sim__lidar__create` | Create generic RTX LiDAR while stopped using qualified installed 6.x APIs. Requires a session launched with enable_motion_bvh=true (run.sh --motion-bvh). No hardware preset promise. At most eight sensors; delete an exact owned sensor path to release resources. |
| `sim__lidar__read` | Read CPU Cartesian LiDAR return points in sensor coordinates with sensor-to-world transform, filter invalid ranges, cap at 256 samples. Requires initialized sensor and rendered simulation frames. |
| `sim__logs__tail` | Read bounded tail of selected Kit log, not arbitrary files. Returns warnings/errors and source path, without clearing shared logs. |
| `sim__controller__create` | OPT-IN CODE EXECUTION: create OnPlaybackTick -> ScriptNode controller using inline Python. Requires ISAAC_MCP_ENABLE_SCENE_SCRIPTS=1 in Kit launch; NOT a sandbox. |
| `sim__controller__update` | OPT-IN CODE EXECUTION: replace inline code of this pack's controller and request recompilation while stopped. Same explicit script enable required. |
| `sim__joints__command` | Command initialized articulation position targets on selected PhysX or qualified Newton backend in DOF order. Radians for revolute, stage units for prismatic; validates runtime limits. Returns target acknowledgment, not achieved pose. |
| `assets__import_urdf` | Convert a trusted approved relative URDF project using the installed importer, then snapshot and import its USD. No source overwrite. Rejects DTD/entities, package/URL/absolute mesh paths and missing files. Requires 6.x URDFImporter API. |

Other than discovery and stage-info, every tool requires exact `session_id` and `stage_id`
from stage-info. Tokens are stale-target guards, **not authentication**. Check stage-info again
after selecting/restarting Kit. Asset preparation checks the destination before copying files.
The public schemas expose only root IDs/relative paths; broker snapshot parameters are internal.
Errors from these tools set MCP `isError`; legacy tools retain their prior result envelopes.
Connection/timeout failures after dispatch have unknown outcomes: inspect before retrying.

Pose coordinates use **stage units**, `orientation=[w,x,y,z]` must be a finite unit quaternion,
and `frame` is `local` or `world`. World transforms use the parent's inverse matrix; singular
parents are refused. Source and target units/up-axis must match. No automatic normalization.
Authored edits require a **stopped** timeline (paused physics is also refused). Saving a composed copy also permits paused physics; playing saves refuse. Physics state
observations do not establish joint stability, robot fidelity or hardware behavior.

Edits go to the session layer, never source layers or source project files. Imports use a wrapper
so asset-authored transforms survive. Parents must exist. Failure after an application exception
reports unknown effects, and failed creations/imports attempt to remove their new session-layer
prim. Deletion/clear only touch pack-owned session-layer subtrees; sensors/articulations are protected.
No generic attribute setter or arbitrary asset-valued entry point is provided. Reloading the pack loses pack ownership and invalidates its session token.

## Bounds and security limits

Approved-root reads use descriptor-relative no-follow opens, inode/change checks, and reject
symlinks/special files, traversal, URLs, controls and replacement races detected during copying.
Filename scans count all entries, not only USDs. They fail explicitly on budget exhaustion rather
than claiming a complete partial result. Pagination is a fresh scan, not a stable filesystem snapshot.

Snapshot limits: 64 MiB/file, 256 MiB/project, 1,000 files, 10,000 entries, eight path components,
15-second enumeration budget, 16 retained snapshots/staging directory plus at most one
bounded temporary copy under the staging lock. Identical project/entry manifests reuse an
existing snapshot only after all its contents are verified again; changed projects consume
another slot. Existing snapshots are never evicted while a stage or saved output may use them.
The complete bounded
project is copied with relative layout intact. Hashes are checked on the Kit side. Source files
may change later without changing loaded copies. Files are private/read-only but the same OS user
can modify them: hashes and permissions are **not immutable OS isolation**. Do not expose this
configuration to hostile writers. Snapshots are retained because loaded/saved scenes may reference
textures in them; clean staging only after all consumers and exported scenes are no longer needed.
Quota exhaustion is explicit; there is no unsafe automatic deletion of live dependencies.

The trusted native `UsdUtils.ComputeAllDependencies` inspection can itself invoke resolvers/parsers.
It reports missing/outside-snapshot dependencies and rejects those imports. The sole external
exception is the exact installed Kit `OmniPBR.mdl` module; its path/hash/source are returned.
Archive-bearing inputs must pass the bounded trusted USDZ checks below; unsupported ZIP formats are refused before native parsing. This is **not** a
pre-parser sandbox or proof of exhaustive plugin behavior. Known OmniGraph prims are refused,
but the importer makes no claim that all executable asset content is detected or disabled.
No untrusted USD is parsed by this workflow; an isolated profile config is refused entirely, including MCP server startup (no raw tools are exposed by such a failed server). No isolated Kit endpoint is created.
There is no verified whole-lifetime isolated GPU Kit deployment or protected untrusted endpoint.

Scene traversal is capped at 10,000 visits; tree/physics responses at 200 nodes and the generic
bridge response at 64 KiB. `prim.get` omits arbitrary attribute/custom-data contents and expensive
recursive bounds. Native USD composition itself is not resource-isolated in this trusted profile.

Save flattens the **composed active state**: inactive prims and unselected variants are omitted.
It is not a lossless authoring-layer archive. Textures can retain snapshot paths. Output publishing
uses no-replace hard links; existing files and symlinks are not overwritten. The configured output
directory should be private to the OS user. Defaults are process-specific under the temporary directory.

## Sensors, physics and trusted controllers

Runtime APIs use PhysX or the qualified Newton adapter described below. Generic Play temporarily selects the session edit target
so native physics writeback can override session-authored spawn poses. Stop restores the prior
edit target after the normal application frame. External edit-target changes cause exact stepping
to refuse, and Stop does not overwrite a target chosen by another editor. First generic Play on
an externally running timeline requires an explicit Stop. This fixes the independently observed
case where PhysX moved a body but stronger session poses left its image at spawn. `sim__simulation__step` accepts an initialized **paused**
timeline and 1–120 physics steps; it checks measured simulation-time delta and returns selected
body poses/DOF observations. It does not render, pump nested application updates, or claim that
changing an authored USD transform moved an active simulated body. Angular runtime values and
drive targets use radians; USD authored joint diagnostics and angular drive gains use degrees.

RTX cameras use UsdGeom and a Replicator RGB annotator configured for owned CPU
storage **at attachment** (`device=cpu`, `do_array_copy=True`). The earlier experimental
CameraSensor path crashed in native CUDA; asking that path for a CPU result at retrieval
was insufficient. Cameras do not require `isaacsim.sensors.experimental.rtx`.
LiDAR uses the installed `IsaacSensorCreateRtxLidar` command and Cartesian CPU annotator,
including the older 6.0.0.0 API. The full build, running Kit and loaded extensions must match
a qualified stack; missing/conflicting identity and unknown builds fail before native creation.
`sim__stage__info.runtime` reports the actual package, full VERSION, Kit, extension, Warp,
solver and adapter information. Adding a release requires qualification evidence and a data
entry in `sim_versions.py`, not a major-version guess.

Launch a new LiDAR session with `sim__launch(enable_motion_bvh=true)` or `run.sh --motion-bvh`.
This renderer setting is explicit because it consumes resources and affects the whole process.
Create sensors stopped, explicitly play for warm-up, then pause to read completed output.
Camera capture normally waits up to five seconds for four matching Hydra drawable events
and a new renderer-managed CPU pixel transfer (`hydra-renderer-cpu-v1`). Advancing render
counters alone did not prevent a stale Replicator pointer after paused physics steps on
6.0.0.1. The default now bypasses that cached pointer, bounds and copies the callback buffer,
and returns `cpu_transfer_completed=true`. Timeout/stage change returns no image; a pending
native transfer prevents another request until completion. It does not step the Replicator
orchestrator or change timeline state. `wait_for_render=false` explicitly permits the latest
Replicator buffer, which can be stale. LiDAR defaults to the latest
completed scan: its acquisition clock requires Play, and requesting a fresh render while
paused returns `SENSOR_PAUSED`. Scan tick rate matches scan rate; accumulation is enabled.
Render completion is **not** an atomic physics/sensor snapshot or proof of a fresh rotating
scan. Camera sizes are 64–1024 pixels per side, at most eight live sensors; LiDAR returns at
most 256 finite nonzero XYZ points with sensor-to-world matrix, in meters.

`STALE_TARGET` includes `current_target` with the current session/stage IDs, PID, layer
identifier, default prim, units and up-axis. Review that identity and resend the chosen IDs
explicitly. A refused call never silently switches targets or performs the requested edit.

Delete an exact owned sensor path while stopped to detach its annotator and release its
render product. Clear/subtree deletion still protects sensors. Cleanup is bound to the
original stage and creation layers; foreign composition opinions cause a partial-cleanup
error instead of cross-layer deletion. Stage closing and extension shutdown release resources.
Reported CUDA illegal-address errors latch `device_error`: runtime/sensor operations refuse
until process restart. A native crash may bypass that guard. Inspect, save when possible,
Stop, and restart only the owned disposable process. See the compatibility follow-up evidence
for measured outcomes and retained earlier failures; no indefinite lifetime guarantee is made.

Managed launches can select `physics_backend="newton"` (default `"physx"`); an unavailable
requested engine fails instead of falling back. Newton uses its tensor adapter, explicit
paused solver steps and Fabric publication; it does not report authored USD as runtime pose.
The older three releases need a scoped internal playing-flag adapter, restored even on error.
Direct Newton steps invalidate captured CUDA graphs to avoid stale dt/state buffers.
Newton requires meter stages and a qualified extension/library stack. Paused saves copy up to
256 runtime rigid-body poses into a private flattened export; the live/source USD stays unchanged.
This is a pose snapshot, not a solver checkpoint. Runtime mass/COM velocities are optional via
`sim__body__state(include_dynamics=true)`. Angular velocities are radians/s; mass includes kg
conversion. Validation is scoped to the recorded Newton/MuJoCo and PhysX configurations.

Controller tools execute arbitrary Python through ScriptNode and require the user's launch-time
`ISAAC_MCP_ENABLE_SCENE_SCRIPTS=1`. That explicitly enables Kit's ScriptNode consent setting;
it is not OS isolation. By default both tools return `SCRIPT_DISABLED`. The graph topology is
fixed to playback tick → inline ScriptNode; update recompiles owned code while stopped.
Controller USD edits must respect layer strength (for pack-authored geometry, use its session
layer). General graph topology and file/module hot-reload are not implemented. Existing trusted
`run_python` is unchanged, and is never used as a substitute in the capability mapping.

URDF conversion requires the installed 6.x importer API. Relative mesh filenames must be in
the approved snapshot; package URLs, absolute paths, DTDs and entities are refused. Conversion
writes service-owned derived files; the active root/session layers are compared before and after
conversion. The result passes the same USD dependency and units checks. Unsupported importer
APIs fail explicitly. See [fork comparison](FORK-COMPARISON.md) for each narrower capability.

## Reproduce

```bash
python scripts/make_scene_fixture.py /absolute/new-fixture
# Approve new-fixture above, launch a disposable Sim on a free port, then:
python scripts/scene_mcp_client.py --port 18861 assets__roots '{}'
python scripts/scene_mcp_client.py --port 18861 sim__stage__info '{}'
python scripts/validate_scene_tools.py --port 18861 --output /absolute/transcript.json
```

The validator expects the approved root ID `fixture`; it changes the selected disposable stage.
It uses a real stdio MCP client/server and never `run_python`. CPU OpenUSD tests require the
`scene-test` extra; they do not test live PhysX/Kit. See evidence for precise scope.

## USDZ trusted-project subset

The broker validates stored, unencrypted, 64-byte-aligned USDZ containers before native USD
parsing. It checks central/local headers, CRCs, hashes, types, duplicate/case-folded names,
relative member paths, counts and sizes without extracting files. The default member must be
USD. Nested archives, ZIP64, compression, descriptors, links, traversal and unknown member
types refuse. Common multi-file USD plus texture packages retain their package paths;
resolved package members must belong to the verified manifest. This is a bounded supported
subset of USDZ, **not OS isolation**. Native resolver/plugins still run with the trusted
process's permissions. Retain verified staging snapshots while referenced/saved outputs use them.

Saved copies omit recognized process-bound Replicator `/Orchestrator` graphs, SyntheticData
pipelines and pack-owned render products; their
native graph/CPU handles cannot be reopened as USD. Recognized ROS camera writers are omitted
only when a preserved camera helper with the same topic/frame and an authored render-product
source can regenerate them. Camera and LiDAR definitions, materials and user Action Graphs
remain. The result lists `omitted_runtime_render_paths`. Unknown content
inside a native pipeline or `/Orchestrator`, or a preserved controller connected to omitted
runtime state (including relationship targets), returns `SAVE_UNSUPPORTED` before publication;
the live stage is unchanged.
Create fresh sensors after reopening; a saved scene is not a running sensor or physics checkpoint.

The earlier 6.0.0.0 reopened-camera `NOT_READY` failure was traced to a serialized
Replicator orchestration graph suppressing recreation of its Python update subscription.
New exports omit that recognized graph; fresh-process windowed and headless MCP reopen,
body-state/step and camera checks pass. Previously exported files can retain obsolete native
state: re-export a trusted scene through the corrected save tool into a new file, then reopen
that copy. No runtime patch, implicit stage reset or private Replicator lifecycle call is used.
See the compatibility evidence for the retained failure history and tested scope.

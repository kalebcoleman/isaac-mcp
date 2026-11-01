# Generic Isaac Sim Tool Pack (`sim.*`) — Design

> **Status: design draft, generic tools not implemented.** The original draft was written 2026-10-29 from static
> inspection. The bridge attachment obstacle was addressed in the shared-bridge update: a temporary second pack and
> the UR5e pack were exercised on one live 6.2 endpoint. The first stage/prim inspection APIs have now been checked
> in a separate non-robot 6.2 scene; see [verification](VERIFICATION.md#generic-scene-api-validation). The tools and
> the remaining proposed APIs still need implementation and validation.

The [end-to-end asset and scene plan](plans/ASSET-SCENE-END-TO-END.md) combines this draft with project-folder
discovery, current catalog/session behavior and the required live verification. Its proposed shared host/catalog
integration supersedes this draft's original no-host-changes assumption; generic tools remain unimplemented.


Design for a second Kit-side tool pack exposing **robot-agnostic** Isaac Sim
capabilities: stage inspection, prim authoring/deletion, physics control and
state, viewport capture and recording. It is a sibling of the UR5e pack: nothing
in it may reference `/World/UR5e`, RG2 or any twin asset.

Grounded in the installed Isaac Sim 6.2 tree (`$SP`, §3.25), `docs/ARCHITECTURE.md`,
`mcp/docs/tool-authoring.md`, `mcp/docs/protocol.md`, and
the UR5e pack's own registration code.

The gaps that remain after the first implementation of these tools are planned in
[plans/SIM-TOOLS-FOLLOWUP.md](plans/SIM-TOOLS-FOLLOWUP.md).

## 1. Goals and non-goals

### Goals

1. Answer "what is on this stage, and what is it doing?" from typed tools, not `run_python`.
2. Build and destroy a **throwaway** stage: create prims, set transforms and attributes, apply physics schemas, delete what was created.
3. Drive time (play/pause/stop/step) and read physics state for any rigid body or articulation.
4. See the result: viewport stills, camera stills, MP4 recordings.
5. Keep every answer small, bounded and JSON-serializable.

### Non-goals

- No UR5e, RG2, workstation or twin-asset knowledge; twin behaviour stays in
  `ur5e_dt.*`.
- No ROS 2 / OmniGraph authoring — a separate pack. `prim.SetActive(False)` does
  not stop a graph (`mcp/docs/tool-authoring.md`), and getting that wrong
  silently breaks the twin.
- No USD import from the network (Nucleus/HTTP): local files under an
  allow-listed root only.
- One `omni.usd` default context; no multi-stage management.
- No headless offscreen capture before phase 4 (§3.26, §6).
- No changes to `exts/ur5e-dt/`, `run.sh` or `mcp/` from this document.

## 2. Extension layout and registration

### 2.1 Directory layout (mirrors `exts/ur5e-dt/`)

```
exts/sim-tools/
  config/extension.toml   # same keys as exts/ur5e-dt/config/extension.toml
  docs/README.md
  docs/CHANGELOG.md
  LICENSE                 # MIT, upstream copyright kept
  sim_tools/
    __init__.py           # from .extension import *
    extension.py          # omni.ext.IExt: on_startup/on_shutdown
    registry.py           # MCP_TOOL_REGISTRY (same schema dict shape)
    _state.py             # live handles that survive reload_tools
    policy.py             # pure-stdlib validation: paths, caps, guards
    stage_tools.py        # sim.stage.*, sim.prim.get/find
    prim_tools.py         # sim.prim.create/set_*/duplicate/delete
    physics_tools.py      # sim.physics.*, body/articulation state, raycast
    capture_tools.py      # sim.camera.list, sim.capture.*
    tests/
```
`extension.toml` keeps the UR5e pack's `[package]` keys (`version`, `authors`,
`title`, `description`, `readme`, `category`, `keywords`, `changelog`), then
`[dependencies]` with `"omni.kit.uiapp" = {}` and `"isaac.mcp.bridge" = {}` (the
bridge must load first: `get_mcp_bridge()` is used at startup), then
`[[python.module]] name = "sim_tools"`. Phase 1 builds no `omni.ui` window, so
`registrar.window(...)` is not called and the reload contract
(`mcp/docs/tool-authoring.md`) has nothing to destroy.

### 2.2 Registry shape (identical to the twin)

The twin's [`bridge_binding.py`](../exts/ur5e-dt/ur5e_dt/bridge_binding.py) registers an owner and loops over
`MCP_TOOL_REGISTRY`, resolving each handler from a stable `_cmd_<name>` attribute. The sim pack can do the same:

```python
self._mcp = ...                       # see 2.3
registrar = self._mcp.register_owner("sim-tools", "sim", display_name="Isaac Sim (generic)")
for name, schema in MCP_TOOL_REGISTRY.items():
    registrar.add(ToolDefinition(name=name, description=schema["description"],
        parameters=schema.get("parameters", {}),
        handler=getattr(self, "_cmd_" + name.replace(".", "_"))))
```

Canonical names are `sim.stage.info`, `sim.prim.delete`, …;
`mcp/isaac_mcp/server.py` rewrites dots to `__`, so clients see
`sim__stage__info`, `sim__prim__delete`. `sim` does not collide with `ur5e_dt`
(`register_owner` rejects duplicate namespaces and owner ids).

### 2.3 Shared bridge attachment

The UR5e pack now registers on `get_mcp_bridge()`, the singleton provided by the bridge extension. The standard twin
launcher sets the selected port on the bridge and defers opening the socket until the twin has registered. A future
Sim pack can use the same singleton and register a distinct owner and namespace. Its extension must declare the
`isaac.mcp.bridge` dependency and remove only its own registrations on shutdown; it must not stop the shared socket.

A temporary autoloaded test pack was verified alongside all 23 UR5e tools in a headless 6.2 session. The probe pack
reloaded, and the twin could be disabled and re-enabled while the other pack remained reachable. This validates the
connection mechanism only: the `sim.*` tools proposed below do not exist yet. The first stage and prim inspection
APIs have since been checked separately; this does not validate the remaining proposals.
See [architecture](ARCHITECTURE.md), [verification](VERIFICATION.md), and the live-test checklist there.

### 2.4 Admission, exclusivity, reload

- Owners registered through `register_owner` are exempt from `DOORWAY_ADMISSION`
  (`bridge.py:1007-1020`); if the pack is instead loaded as an autoloaded *tool
  module* via `ISAAC_MCP_TOOL_MODULES` (`isaac_mcp_bridge/autoload.py`) it
  **must** set `DOORWAY_ADMISSION = 1` at module level.
- No handler calls `app.update()`, `world.step()` or `world.render()`; long work
  `await omni.kit.app.get_app().next_update_async()`. Hence no
  `__isaac_mcp_bridge_exclusive__` marks, and a test asserts `isaac_mcp.marks`
  reports `PUMPS ∪ OPAQUE == ∅` for the pack (`mcp/isaac_mcp/marks.py`).
- Live objects that must survive `reload_tools` (physics tensor views, the active
  `CaptureExtension` handle, capture bookkeeping) live in `sim_tools/_state.py` —
  that exact module name is preserved across reloads.

## 3. Proposed tools (24)

Citation keys expand to exact installed paths in §3.25. Handlers take kwargs and
return JSON-serializable data or raise (`ToolError` for diagnostics). "sync"
means "no await, still Kit-main-thread-only"; every handler runs on Kit's main
thread because the bridge hops it there (`mcp/docs/protocol.md`).

| # | Tool (`sim.*`) | Parameters | Returns | API (key) | Thread | Main failure modes |
|---|---|---|---|---|---|---|
| 1 | `stage.info` | — | `{stage_url, state, default_prim, up_axis, meters_per_unit, prim_count, playing, time_s}` | C1 `get_stage_url/get_stage_state`, C8 `GetDefaultPrim`, C9 `UsdGeom.GetStageMetersPerUnit/GetStageUpAxis`, C2 `is_playing/get_current_time` | sync | no stage open needs a clear error; `prim_count` capped at 100k and flagged `truncated`; stage-wide counts include other app or extension content; an anonymous `anon:` URL is not a saved file |
| 2 | `stage.tree` | `root="/World"`, `depth=3`, `max_nodes=2000`, `types=null`, `include_inactive=false` | `{nodes:[{path,name,type,active,children}], node_count, truncated}` | C8 `GetPrimAtPath`, `GetChildren` / `GetAllChildren`, `Usd.Prim.IsActive` | sync | root absent → error naming the path; inactive prims need the appropriate traversal; truncation and depth semantics must be explicit |
| 3 | `prim.get` | `path`, `attributes=null\|[names]`, `max_attributes=64`, `include_bounds=true` | `{path,name,type,active,applied_schemas,world_pose{position,orientation},world_bounds{min,max},attributes:{name:{type,value}}}` | C8 `GetPrimAtPath`; C9 `Xformable.ComputeLocalToWorldTransform`, C17 `ExtractTranslation/ExtractRotationQuat`; C9 `BBoxCache.ComputeWorldBound` | sync | reject invalid or relative prim paths (C16); non-Xformable prims need a null pose; world, local and untransformed bounds differ; large attributes and bounds need caps |
| 4 | `prim.find` | `regex`, `type=null`, `max=500` | `{paths, count, truncated}` | C8 `Traverse` plus Python `re.fullmatch` and `GetTypeName` | sync | invalid Python regex → clear error; results must be capped and flagged when truncated; the deprecated C7 helper does not implement the same general regex and USD-type contract |
| 5 | `physics.scene_info` | — | `{scene_path, gravity_magnitude, gravity_direction, time_steps_per_second, physics_dt, playing}` | C10 `UsdPhysics.Scene.GetGravityMagnitudeAttr/GetGravityDirectionAttr`; C11 `PhysxSceneAPI.GetTimeStepsPerSecondAttr`; C6 `SimulationManager.get_physics_dt` | sync | no `UsdPhysics.Scene` prim → nulls, not an error; `PhysxSchema` only importable once the PhysX schema extension is loaded |
| 6 | `sim.state` | — | `{playing, stopped, paused, time_s, physics_dt, time_codes_per_second}` | C2 `is_playing/is_stopped/get_current_time/get_time_codes_per_second`; C6 `SimulationManager.is_paused/get_physics_dt` | sync | non-app Kit without a timeline → error; `paused` is not reachable from `omni.timeline` alone (comes from `SimulationManager`) |
| 7 | `body.state` | `paths=[..]`, `velocities=true` | `{bodies:[{path,position,orientation,linear_velocity,angular_velocity}]}` | C5 `RigidPrim.get_world_poses`, `get_velocities`, `get_masses` | sync read of physics buffers | path is not a rigid body; called before the first step (tensor view not initialized); `wp.array` must be converted with `.numpy()`; one invalid path invalidates the whole view → validate per path first |
| 8 | `articulation.state` | `path`, `dofs=null` (names or indices) | `{path, num_dofs, dof_names, positions, velocities}` | C5 `Articulation.num_dofs/dof_names/get_dof_positions/get_dof_velocities` | sync read of physics buffers | prim is not an articulation root; before first step; unknown dof name → error listing valid names (capped) |
| 9 | `raycast` | `origin[3]`, `direction[3]`, `max_distance=100`, `all=false` | `{hit:bool, hits:[{position,normal,distance,collision,rigid_body,face_index}]}` | C12 `get_physx_scene_query_interface().raycast_closest / raycast_all` (`ifaces.py:44`) | sync; needs an attached PhysX scene | called before the timeline ever played → no scene, empty result (return `hit:false` plus `scene_ready:false`); direction normalized in-handler; distance capped at 100 m; `all=true` capped at 32 hits |
| 10 | `camera.list` | — | `{cameras:[{path,focal_length,clipping_range}], active_viewport_camera}` | C8 `Traverse`; C9 `UsdGeom.Camera` `GetFocalLengthAttr/GetClippingRangeAttr`; C3 `get_active_viewport_camera_string` | sync | no viewport → `active_viewport_camera:null`; no cameras → empty list, not an error |
| 11 | `capture.screenshot` | `filename`, `camera=null`, `width=1280`, `height=720`, `overwrite=false` | `{file, width, height, bytes}` | C3 `capture_viewport_to_file(get_active_viewport(), …)` fired, then await frames (C15); camera selection via C4 `CaptureOptions.camera` + `CaptureExtension` | **async** | if no viewport exists, return a typed error; camera capture can mutate the active camera (C4 `_prepare_viewport`, `extension.py:616-618`) — restore it in `finally`; capture never completes → timeout, partial file deleted; validate actual headless behavior |
| 12 | `capture.record_start` | `filename`, `duration_s=5`, `fps=24`, `camera=null`, `width=1920`, `height=1080`, `overwrite=false` | `{recording:true, expected_frames, output_folder}` | C4 `CaptureOptions(file_type=".mp4")` (`OUTPUT_FILE_TYPES` = `.png/.tga/.exr/.mp4`, `capture_options.py:78`), `CaptureExtension.get_instance().start()` (`extension.py:317`) | **async** (start only) | a capture is already running (`get_instance()` is a singleton); `duration_s*fps` over cap (1800 frames); no active viewport; folder not writable; MP4 encoding is driven by the `mp4_encoding_*` options, so encoder failures surface only at `record_stop` |
| 13 | `capture.record_stop` | `wait=true`, `cancel=false`, `timeout_s=120` | `{files, cancelled, elapsed_s}` | C4 `CaptureExtension.done` (`extension.py:309`), `cancel()` (`:369`), `get_outputs()` (`:1747`) | **async** (awaits frames while `not done`) | nothing running → error; cancel leaves partial frames on disk (report them); `get_outputs()` returns `[]` on encoder failure → report with the last logged `carb` error |
| 14 | `prim.create` | `path`, `type`, `parent=null`, `transform={translate,orient,scale}`, `reference=null`, `attributes={}`, `on_conflict="error"` | `{path, type, created:true}` | C13 `CreatePrimWithDefaultXformCommand` (`usd_commands.py:825`, undoable) or C7 `create_prim`; conflict handling C14 `get_stage_next_free_path` (`utils.py:1259`); path check C16 | sync | invalid path or prim type; parent missing; `reference` outside the allow-list root; path occupied (`on_conflict="next_free"` appends a suffix); creating while the timeline plays is allowed but physics will not see the prim until a pause/play cycle |
| 15 | `prim.set_transform` | `path`, `translate=null`, `orient=null` (wxyz), `scale=null`, `space="local"` | `{path, world_pose{position,orientation}}` | C9 `Xformable.AddTranslateOp/AddOrientOp/AddScaleOp`, `GetOrderedXformOps`, `ComputeLocalToWorldTransform`; world-space via C5 `XformPrim.set_world_poses` | sync | prim not `Xformable`; re-adding an op on every call (must reuse the existing op when present — otherwise ops accumulate and `GetOrderedXformOps` grows unbounded); zero scale; `space="world"` on a child of a parented body fights physics |
| 16 | `prim.set_attribute` | `path`, `name`, `value`, `type_hint=null` | `{path, name, old, new}` | C13 `ChangePropertyCommand` (`usd_commands.py:4340`), or `prim.GetAttribute(name).Set(value)`; type check `attr.GetTypeName()` | sync | attribute missing; type mismatch (rejected with both type names); `points`/`extent`/`xformOpOrder` and other engine-owned attributes are explicitly refused; relationships need `set_targets` and are not exposed |
| 17 | `prim.duplicate` | `path`, `new_path`, `count=1` | `{paths}` | C13 `CopyPrimCommand` (`usd_commands.py:980`) | sync | target exists; copying an articulation root duplicates physics state (refused unless `type` is not an articulation/physics root); large subtree → size cap |
| 18 | `prim.delete` | `paths:[..]`, `confirm=true` | `{deleted:[..], refused:[{path,reason}]}` | C13 `DeletePrimsCommand(paths, destructive=True)` (`usd_commands.py:1365`) which already skips the absolute root and ancestral prims; pre-checks C14 `editor.is_no_delete`, `check_ancestral` | sync | per-path refusals from §4; `confirm` missing → refuse whole call; deleting while playing → refuse; a path that resolves to a prim brought in by composition is skipped with `reason:"ancestral"` |
| 19 | `physics.apply` | `path`, `kind="collider"\|"rigid_body"\|"mass"`, `params={mass,density,kinematic,approximation}` | `{path, applied:[schemas], mass}` | C10 `RigidBodyAPI.Apply`, `MassAPI.Apply/CreateMassAttr/CreateDensityAttr`, `CollisionAPI.Apply`, `MeshCollisionAPI.Apply`; C11 `PhysxRigidBodyAPI` for `kinematic`/damping | sync | schema already applied on the prim (idempotent, reported); an **ancestor** carries `RigidBodyAPI` → refuse (double body); `approximation="sdf"` on a non-mesh prim → error; new colliders are only picked up after a physics reset (deactivate/reactivate or stop/play) — the response says so |
| 20 | `sim.play` | — | `{playing:true, time_s}` | C2 `timeline.play()` | sync (returns immediately) | the known first-play cook wedge — we do **not** reproduce the twin's 900-frame warmup loop; the caller polls `sim.state` |
| 21 | `sim.pause` | — | `{playing:false, time_s}` | C2 `timeline.pause()` | sync | pausing during an active capture freezes frames (allowed; `record_stop` reports the short count) |
| 22 | `sim.stop` | — | `{playing:false, time_s}` | C2 `timeline.stop()` | sync | stop rewinds to the start timecode — report the new `time_s` so callers do not assume a resume |
| 23 | `sim.step` | `frames=1`, `max_frames=600` | `{frames, time_s}` | C2 `get_current_time`, C15 `await next_update_async()` per frame; C6 `SimulationManager.step(steps=n)` only when the timeline is paused | **async** | timeline already playing → refuse (double-stepping); `frames > max_frames` → refuse; frame never arrives (UI thread starved) → timeout after `frames/10 + 10` s; `SimulationManager.step` bypasses rendering, so it is opt-in (`render=false` semantics) |
| 24 | `stage.save` | `filename=null` (save-as), `overwrite=false` | `{file, mode:"save"\|"save_as", bytes}` | C1 `save_stage()` for a saveable named stage, else `save_as_stage(url)` (`:769`, synchronous, returns `bool`) | sync | an anonymous stage can have a nonempty `anon:` URL but still needs save-as; missing filename, path policy, write permissions and `False` results need explicit errors |

### 3.24 First live scene-inspection observations

The non-robot scene check in [VERIFICATION.md](VERIFICATION.md#generic-scene-api-validation) exercised the underlying
stage, tree, prim and search APIs on Isaac Sim 6.2, not the unimplemented `sim.*` tools. In that session:

- `GetChildren` and `Traverse` excluded an inactive prim; `GetAllChildren` and `TraverseAll` included it. The session
  also contained prims outside the fixture's `/World` subtree, so a stage-wide count is not just the authored fixture.
- A translated cube had different world, local and untransformed bounds. `ComputeWorldBound` therefore supports the
  proposed `world_bounds` field, not the former `local_bounds` label. A `Scope` was not Xformable.
- `Sdf.Path.IsValidPathString` returned a bool-like `_IsValidPathStringResult` for invalid syntax; its representation
  included `(False, reason)` and its truth value was false. A relative path had valid syntax. Normalize results across
  USD bindings and check that a requested prim path is absolute as well.
- The deprecated `find_matching_prim_paths` helper matched a simple wildcard and one alternation, but an anchored
  pattern and invalid pattern both returned empty results, and a `Cube` type filter raised `ValueError`. Its documented
  contract is narrower than general regex and USD type names. Python `re.fullmatch` plus `GetTypeName` matched the
  fixture as expected; the eventual tool still needs bounded traversal and its own error and truncation tests.

No-camera/no-stage behavior, deep trees, instance proxies, non-scalar attribute serialization and other Sim versions
remain untested. These checks do not validate physics, capture, authoring or a shipped tool implementation.

### 3.25 Citation expansion (exact installed files)

`$SP = <repo>/.venv/lib/python3.12/site-packages`
`$UD = $SP/isaacsim/extscache/omni.usd-1.19.11+187cf21b.lx64.r.cp312/omni/usd`
`$CP = $SP/isaacsim/extscache/omni.kit.capture.viewport-2.11.1+187cf21b/omni/kit/capture/viewport`
`$EP = $SP/isaacsim/exts/isaacsim.core.experimental.prims/isaacsim/core/experimental/prims/impl`
`$DU = $SP/isaacsim/extsDeprecated/isaacsim.core.utils/isaacsim/core/utils`

- **C1** `$UD/_usd.pyi` — `get_context`:960, `get_stage_state`:658,
  `get_stage_url`:668, `open_stage`:707, `save_as_stage`:769, `save_stage`:813.
  The runtime also has `new_stage()`, despite its absence from this stub (§3.26).
- **C2** `$SP/isaacsim/extscache/omni.timeline-1.1.2+187cf21b.lx64.r.cp312/omni/timeline/_timeline.pyi`
  — `play`:204, `pause`:200, `stop`:297, `is_playing`:179, `is_stopped`:193,
  `get_current_time`:73, `get_time_codes_per_second`:130 (no `is_paused`).
- **C3** `$SP/isaacsim/extscache/omni.kit.viewport.utility-2.1.2+187cf21b/omni/kit/viewport/utility/__init__.py`
  — `get_active_viewport`:171, `get_active_viewport_camera_string`:233,
  `capture_viewport_to_file`:356; `camera_path` get/set + `schedule_capture` in
  `$SP/isaacsim/extscache/omni.kit.widget.viewport-110.0.1+187cf21b/omni/kit/widget/viewport/api.py:270-279,192`.
- **C4** `$CP/` — `capture_options.py:116` (`CaptureOptions`), `:78`
  (`OUTPUT_FILE_TYPES`), `:34` (`CaptureRangeType`); `extension.py:348-349`
  (no active viewport), `:309` `done`, `:317` `start`, `:369` `cancel`,
  `:608` `_prepare_viewport`, `:1747` `get_outputs`, `:1796` `get_instance`.
- **C5** `$EP/` — `rigid_prim.py:296/543/707` (`get_world_poses`/
  `get_velocities`/`get_masses`); `articulation.py:202/222/2010/2113`
  (`num_dofs`/`dof_names`/`get_dof_positions`/`get_dof_velocities`);
  `xform_prim.py:464/555` (get/set `world_poses`). Returns `warp` arrays —
  convert with `.numpy()`. The classic `isaacsim.core.prims.*` the twin uses is
  `extsDeprecated/` in 6.2 and is not used by new code.
- **C6** `$SP/isaacsim/exts/isaacsim.core.simulation_manager/isaacsim/core/simulation_manager/impl/simulation_manager.py`
  — `is_paused`:950, `step`:968, `get_simulation_time`:896, `get_physics_dt`:1794.
- **C7** `$DU/` — `stage.py:85/199/300/720` (`get_current_stage`/
  `get_stage_up_axis`/`add_reference_to_stage`/`get_stage_units`);
  `prims.py:393/555/669/775` (`find_matching_prim_paths`/`is_prim_no_delete`/
  `create_prim`/`delete_prim`); `bounds.py:82/104`. Deprecated: a migration risk.
- **C8** `$SP/pxr/Usd/__init__.pyi` — `Traverse`:1815, `IsActive`:1114,
  `GetPrimAtPath`:1723, `GetDefaultPrim`:1691, `RemovePrim`:1777, `Save`:1781.
- **C9** `$SP/pxr/UsdGeom/__init__.pyi` — `Xformable`:1812 (`AddOrientOp`:1814,
  `AddScaleOp`:1834, `AddTranslateOp`:1844, `GetOrderedXformOps`:1870,
  `ComputeLocalToWorldTransform`:1640); `Capsule`:328, `Cone`:386, `Cube`:434,
  `Cylinder`:505, `Plane`:734, `Sphere`:1283; `Camera`:239
  (`CreateClippingRangeAttr`:245, `CreateFocalLengthAttr`:259); `BBoxCache`:59.
- **C10** `$SP/pxr/UsdPhysics/__init__.pyi` — `CollisionAPI`:206, `MassAPI`:808
  (`CreateDensityAttr`:816, `CreateMassAttr`:820), `MeshCollisionAPI`:875,
  `RigidBodyAPI`:1098, `MaterialAPI`:846, `Scene`:1258
  (`CreateGravityMagnitudeAttr`:1262, `GetGravityMagnitudeAttr`:1268). No
  `plugInfo.json` beside it: schema registration comes from Kit (§5.1).
- **C11** `$SP/isaacsim/extscache/omni.usd.schema.physx-110.4.4+110.4.0.lx64.r.cp312.u7f4/pxr/PhysxSchema/__init__.pyi`
  — `PhysxArticulationAPI`:111, `PhysxCollisionAPI`:434, `PhysxRigidBodyAPI`:1263,
  `PhysxSceneAPI`:1397 (`CreateTimeStepsPerSecondAttr`:1473,
  `GetTimeStepsPerSecondAttr`:1551). Not importable from the bare venv, and
  never to be imported at module import time.
- **C12** `$SP/isaacsim/extscache/omni.physx-110.4.4+110.4.0.lx64.r.cp312.u7f4/omni/physx/bindings/_physx.pyi`
  — `raycast_all`:2152, `raycast_closest`:2179 (documented keys: `position,
  normal, distance, faceIndex, collision, rigidBody, protoIndex, material`);
  getter `omni/physx/scripts/ifaces.py:44`.
- **C13** `$UD/commands/usd_commands.py` — `CreatePrimWithDefaultXformCommand`:825,
  `CreatePrimCommand`:947, `CopyPrimCommand`:980, `DeletePrimsCommand`:1365,
  `MovePrimCommand`:3700, `ChangePropertyCommand`:4340.
- **C14** `$UD/_impl/utils.py:984` (`check_ancestral`), `:1259`
  (`get_stage_next_free_path`); `$UD/editor/editor.py:56` (`is_no_delete`).
- **C15** The original static scan did not find `omni.kit.app` stubs in the searched install paths. The runtime API
  `next_update_async` is used in repository sources including `mcp/docs/tool-authoring.md`; missing stubs do not mean
  the runtime module is absent. Verify its proposed use in each new handler against the target Kit version.
- **C16–C18** `$SP/pxr/Sdf/__init__.pyi:2071` (`Sdf.Path.IsValidPathString`);
  `$SP/pxr/Gf/__init__.pyi:893/899/901` (`Matrix4d`/`ExtractRotationQuat`/
  `ExtractTranslation`); (phase 4) `$SP/isaacsim/extscache/omni.replicator.core-1.13.38+110.4.0.lx64.r.cp312/omni/replicator/core/scripts/create.py:1434`
  (`render_product`).

### 3.26 APIs and behavior still to investigate

`UsdContext.new_stage()` is present at runtime and created the disposable validation stage, even though the original
stub search did not find it. A generic stage-reset tool is not yet proposed: it would need explicit destructive-action
semantics and tests. `CaptureExtension.start()` requires an active viewport, but headless does not necessarily mean
there is none: the twin's separate real-time viewport recording passed in a headless session. The proposed generic
capture path and its no-viewport behavior still need direct checks. No `omni.usd` stage diff/transaction API was found
in the original scan; undo is available through `omni.kit.commands` as used by the twin.

## 4. Safety rules

Enforced in `sim_tools/policy.py` (pure stdlib, no `omni`/`pxr` imports) and
re-checked in the handler, because a URL can change between validation and use.

### 4.1 File outputs (capture, recording, stage save)

- One root: `SIM_TOOLS_OUTPUT_ROOT`, default
  `~/.local/share/isaac-mcp/sim-tools/{images,videos,stages}`; if
  `MCP_CLIENT_OUTPUT_DIR` is set (the twin's convention, `extension.py:34-38`)
  the root moves under it.
- Filename is a **basename only**: `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` — no
  separators, no `..`, no leading dot — and must match the tool
  (`images/*.png`, `videos/*.mp4`, `stages/*.usd`).
- Resolve with `os.path.realpath`, then `commonpath()` against the resolved
  root; a symlink that escapes the root is rejected, not followed.
- Existing file: refused unless `overwrite=true`. A recording always takes a
  fresh name on collision; silently overwriting a video is never worth it.
- Stage save may target a path *outside* the root only when it prefix-matches
  the currently open stage's directory (editing a scene in place) — the single,
  explicit exception.

### 4.2 Destructive prim deletion

`prim.delete` refuses, per path, with a `reason` string in `refused[]`:

| Refusal | Check |
|---|---|
| `/` | absolute root (`DeletePrimsCommand` also skips it, `usd_commands.py:1404`) |
| `/World` and every ancestor of the stage's default prim | explicit list plus `stage.GetDefaultPrim()` (C8) |
| prim with `no_delete` metadata | `omni.usd.editor.is_no_delete` (C14) |
| prim brought in by composition only | `omni.usd.check_ancestral` (C14); `DeletePrimsCommand` warns but may still drop it |
| prim carrying `UsdPhysics.ArticulationRootAPI` | mapped-schema check (C10); always refused — `sim.stop` and re-open the stage instead |
| more than 200 paths in one call | hard cap; callers enumerate with `prim.find` first |
| wildcard or relative path | only literal absolute `Sdf.Path`s are accepted (C16) |
| timeline playing | refused; stop or pause first so physics is not torn down mid-step |

`confirm=true` is mandatory for the whole call; there is no `force`. Deletion
never cascades implicitly — a parent deletes its subtree because USD does, so a
parent is refused if any descendant triggers a refusal above.

### 4.3 Creation and mutation

- Paths must be absolute, valid (`Sdf.Path.IsValidPathString`, C16), under
  `/World` or the stage's default prim. Depth ≤ 16, name length ≤ 64.
- `reference` and asset-valued attributes must resolve under
  `SIM_TOOLS_ASSET_ROOTS` (comma list, realpath-contained). No `http://`, no
  `omniverse://`.
- `prim.set_attribute` refuses engine-owned attributes: `points`, `normals`,
  `faceVertexIndices`, `faceVertexCounts`, `extent`, `xformOpOrder`,
  `physics:*`, `physx*:*` — derived or schema-managed, and writing them
  corrupts a stage in ways the tool cannot verify.
- `physics.apply` refuses a prim whose ancestor already has
  `UsdPhysics.RigidBodyAPI`.

### 4.4 Size, time and output caps

| Limit | Value | Where |
|---|---|---|
| prims visited by `stage.info` | 100k (then `truncated:true`) | policy |
| nodes from `stage.tree` | 2000 (`max_nodes` ≤ 10000) | policy |
| paths from `prim.find` | 500 (`max` ≤ 5000) | policy |
| attributes from `prim.get` | 64 (`max_attributes` ≤ 200) | policy |
| raycast | 100 m, 32 hits when `all=true` | policy |
| recording | 1800 frames (`duration_s * fps`), 4096×2160 capture size | policy |
| `sim.step` | 600 frames, wall clock `frames/10 + 10` s | policy + handler |
| screenshot wall clock | 30 s | handler |
| recording wall clock | `duration_s*1.5 + 30` s; `record_stop` wait ≤ 600 s | handler + policy |

On any timeout: `CaptureExtension.cancel()` (C4), delete the partial output,
and raise `ToolError` with what was written. Never leave a capture running —
the singleton would refuse every later capture with no explanation.

### 4.5 Concurrency

- The bridge serializes calls, so two mutations cannot interleave. The hazard is
  *time*: a recording or `sim.step` holds the request slot while frames advance;
  both are bounded by §4.4.
- One capture at a time, enforced by `sim_tools/_state.py` — the singleton would
  otherwise be reconfigured under a running capture.
- `sim.step` refuses while the timeline plays; `sim.play` refuses mid-sequence.

## 5. Test plan

### 5.1 Unit-testable without Kit (`mcp/tests/`)

Requires adding `exts/sim-tools` to the sys.path entries in
`mcp/tests/conftest.py` (today it adds `mcp/` and `exts/isaac.mcp.bridge/`).

- `policy.py` is pure Python: filename regex, realpath containment (symlink
  escape, `..`), extension matching, every cap, and the §4.2 refusal table as
  `(path, prim_facts) → reason` decisions fed by fakes.
- Registry shape: each `MCP_TOOL_REGISTRY` entry has a `description`, only
  JSON-schema-known parameter types, a `sim.` name, and a matching `_cmd_<name>`
  attribute — the twin's convention (`extension.py:768-775`) has no compile-time
  check, so this test is that check.
- Marks roster: `isaac_mcp.marks` over the pack reports no `PUMPS` and no
  `OPAQUE` handler.
- USD-level logic with no physics: `Sdf`/`Usd`/`UsdGeom` are plain `pxr`
  bindings in the wheel, so `Usd.Stage.CreateInMemory()` covers tree building,
  path validation, attribute rendering and cap/truncation directly.
  **Verified caveat:** there is no `plugInfo.json` anywhere under `$SP/pxr`
  (zero hits in the whole tree, `UsdPhysics` and `UsdGeom` included) or under
  `$SP/isaacsim/kit/extsPhysics` (empty), so schema plugins come from the Kit
  app: anything that `Apply()`s `UsdPhysics`/`PhysxSchema` belongs to the Kit
  tier below.

### 5.2 Needs a running (headless is fine) Kit — `scripts/checks/*.py`

Same pattern as `scripts/checks/scene_prims.py` through `scripts/bridge_exec.py`
(assign `result`; the script prints it).

| Check | Asserts |
|---|---|
| `sim_stage_read.py` | `sim__stage__info`, `sim__stage__tree` (depth 2, `truncated`), `sim__prim__find` agree with in-process `stage.Traverse()` |
| `sim_prim_create_delete.py` | create `Xform`/`Cube`/`Sphere`/`Camera`, transform, duplicate, delete; `GetPrimAtPath` is `None`; `/World` and default prim refused |
| `sim_physics_state.py` | after `play`, `sim__body_state` matches `get_world_poses()`; `sim__raycast` down from 1 m hits the ground within ±1 cm |
| `sim_sim_control.py` | `play` → `time_s` rises → `pause` freezes → `step 10` advances ≈`10*dt` → `stop` rewinds |
| `sim_capture.py` | screenshot writes a non-empty PNG at the requested size; start/stop writes an `.mp4` of the expected frame count (`ffprobe` if present) |
| `sim_negative.py` | when there is no active viewport, capture returns a clear error and leaves no file; deleting `/World` and a `../` filename are refused |

`verify.sh` gains a read-only section (phase-1 tools only) so a fresh clone can
assert the pack loaded without touching the twin scene.

### 5.3 Out of reach here

Windowed-only paths on a headless box; `isaaclab__*`-style process isolation (out
of scope); `omni.kit.app` behaviour under load (no source or stub, C15).

## 6. Phased rollout

**Phase 1 — read-only (11 tools, no stage mutation, no files except capture
outputs).** `stage.info`, `stage.tree`, `prim.get`, `prim.find`,
`physics.scene_info`, `sim.state`, `body.state`, `articulation.state`,
`raycast`, `camera.list`, `capture.screenshot`, plus `policy.py` and all §5.1
tests. Exit criteria: `verify.sh` read section green headless and windowed,
`sim__stage__info` agrees with `run_python` on the twin scene, no handler reported
as a pump.

**Phase 2 — authoring.** `prim.create`, `prim.set_transform`,
`prim.set_attribute`, `prim.duplicate`, `prim.delete`, `physics.apply`. The §4.2
refusals land **with** the delete tool. Exit criteria:
`sim_prim_create_delete.py` and `sim_negative.py` green, including an
`ancestral` refusal on a composition-referenced prim.

**Phase 3 — time, files, recording.** `sim.play/pause/stop/step`, `stage.save`,
`capture.record_start/stop`. Exit criteria: `sim_sim_control.py` and
`sim_capture.py` green windowed; a cancelled recording leaves no running capture
and reports partial frames.

**Phase 4 (if needed) — headless capture.** A `capture.offscreen` tool on
Replicator render products (C18), not the viewport. Not before 1–3 are green:
different render path, different failure modes.

## 7. Riskiest assumptions

1. **The generic pack can register safely on the shared bridge.** A temporary test pack has shared the live socket
   with the UR5e twin, including reload and twin disable/enable. The actual generic pack, its handlers, and their
   lifecycle behavior have not yet been built or verified.
2. **`isaacsim.core.experimental.prims` is the right state API for 6.2.** The
   twin uses the deprecation-tracked `isaacsim.core.prims`, and the experimental
   API returns `warp` arrays with backend-dependent initialization
   ("Backends: tensor" for dof reads). If the tensor backend is not initialized
   before the first step, `body.state`/`articulation.state` fail on a fresh
   stage — documented here, not yet observed.
3. **Does the proposed capture path work in the selected app configuration?** The twin's separate viewport-recording path
   succeeded headlessly, so a blanket windowed-only assumption is false. The proposed `CaptureExtension` screenshot
   and recording APIs, camera restoration, and genuinely absent-viewport cases still need their own live tests.

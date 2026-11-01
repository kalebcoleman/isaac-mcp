# Isaac Sim through isaac-mcp

Read this before calling bridge tools or editing a live stage. Full references are
`docs/TOOLS-HOST.md` and `docs/TOOLS-UR5E.md` in the isaac-mcp repository.

## How the pieces connect

```
MCP client ──stdio──► isaac-mcp (host process)
                        │  NDJSON over TCP (loopback by default)
                        ▼
                      isaac.mcp.bridge (extension inside Kit: Isaac Sim or Isaac Lab)
                        ├── built-ins: status, list_bridges, run_python, reload_tools,
                        │              put_file, get_file, stat_file
                        └── tool packs registered under a namespace, e.g. ur5e_dt.*, sim.*, assets.*
```

Bridge tools appear in MCP as `<namespace>__<tool>` (dots are not allowed in MCP names), so
`ur5e_dt.play_scene` is called as `ur5e_dt__play_scene`. The host and the bridge are separate
processes: screen and Isaac Lab tools working says nothing about the bridge.

## Choosing the bridge

- One bridge per Kit process. The supplied twin (`./run.sh`) listens on 8766 unless `UR5E_DT_PORT`
  or `--port` says otherwise; other apps may use other ports.
- Endpoint selection order: explicit port, then `UR5E_DT_PORT`, then `ISAAC_MCP_PORT`, then a lone
  advertised bridge. With several bridges, discovery refuses to guess: call `use_bridge`.
- `use_bridge` lasts until the host restarts. Re-check `status` after switching.
- Starting another simulator: only when none is advertised and the user wants one. With the
  optional `sim_launch` group, `sim__launch` starts an isolated session (own port, ROS domain,
  output directory) after resource checks; poll `sim__status` until `state` is `running`, then
  `use_bridge` to its port. `sim__stop` needs `expected_pid` and `confirm_discard=true`.

## run_python

`run_python` executes on Kit's main thread. Preloaded names: `omni`, `carb`, `Usd`, `UsdGeom`,
`Sdf`, `Gf`. Assign `result` to return a value; it must be JSON-serialisable, so convert USD
types (`str(path)`, `list(vec)`).

Read the stage before changing it:

```python
stage = omni.usd.get_context().get_stage()
result = {
    "root_layer": stage.GetRootLayer().identifier,
    "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
    "prims": [str(p.GetPath()) for p in stage.Traverse()][:200],
}
```

Check a prim and its world transform:

```python
stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath("/World/ur5e")
if not prim.IsValid():
    result = {"error": "no prim at /World/ur5e"}
else:
    xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    result = {"type": prim.GetTypeName(), "translation": list(xf.ExtractTranslation())}
```

Timeline state:

```python
import omni.timeline
tl = omni.timeline.get_timeline_interface()
result = {"playing": tl.is_playing(), "time": tl.get_current_time()}
```

Rules of thumb:

- Keep each call short. Long loops block Kit's main thread and the UI with it.
- `persistent=true` with a `session_id` keeps variables between calls; opening a new stage clears
  them because their prim handles are dead.
- Prefer a namespaced tool over `run_python` when one exists, and `reload_tools` (then
  `refresh_tools`) after editing a tool pack instead of restarting Kit.
- `run_python` and the file tools run with the Kit process's permissions. Do not run code you have
  not read, and do not expose the bridge beyond loopback.

## Seeing the simulator

- `screenshot` without a selector picks the Isaac Sim / Isaac Lab window and captures it even when
  covered. `list_windows` with `{"filter": "Isaac"}` gives ids for `window_id`.
- `start_screen_recording` / `stop_screen_recording` record a window or monitor to MP4 (no audio).
  For a clean viewport without desktop chrome, use the twin's `ur5e_dt__start_recording`.
- Optional `screen_input` tools (`window_click`, `window_drag`, `window_scroll`, `window_type`,
  `window_key`) act inside one window. They need `expected_width` / `expected_height` from a fresh
  screenshot, so take one first. Prefer `run_python` or a named tool over clicking UI.

## Generic scene and project assets (`sim__*`, `assets__*`)

The `sim`/`assets` pack is on by default (`run.sh` enables `sim-tools`). `ISAAC_MCP_SCENE_TOOLS=0`
on the host hides all 37 tools per server; it is a visibility preference, not access control.

- Call `sim__stage__info` first. Keep its `session_id` and `stage_id`; every call except discovery
  and stage-info needs them. A `STALE_TARGET` refusal returns `current_target`; resend those IDs
  explicitly, because mutations never retarget silently.
- Read with `sim__stage__tree`, `sim__prim__get`, `sim__prim__find`, `sim__physics__inspect` and
  `sim__joints__inspect`.
- Authored edits require a stopped timeline: `sim__prim__create` / `sim__prim__set_transform` /
  `sim__prim__clone` / `sim__prim__delete` (`confirm=true`), `sim__stage__clear_owned`,
  `sim__light__*`, `sim__material__*`, `sim__physics__configure`, `sim__joints__drive`. They write
  to the session layer only; source USD files are not rewritten.
- `assets__roots` → `assets__scan` / `assets__search` → `assets__info` →
  `assets__import_usd` / `assets__import_urdf`. Only launch-approved roots are accepted; there is
  no URL input and no untrusted/hostile-USD isolation. Import needs matching units/up-axis, a
  stopped timeline and a free wrapper path.
- Runtime state: `sim__timeline__control`, `sim__simulation__step` while paused,
  `sim__body__state`, `sim__joints__state` and `sim__joints__command` after physics is initialized.
- Capture and sensors: `sim__camera__create` / `sim__camera__capture` (RTX), `sim__lidar__*`
  (requires `--motion-bvh`), `sim__viewport__frame`. `NOT_READY` means render warm-up; on Isaac
  Sim 6.0.0.0 a camera created after reopening a saved stage can stay `NOT_READY`.
- `sim__stage__save` writes a new basename; it never overwrites or switches the current stage.
  `sim__logs__tail` reads the selected Kit log.
- `sim__controller__*` execute inline Python in a ScriptNode. Use them only with
  `ISAAC_MCP_ENABLE_SCENE_SCRIPTS=1` at Kit launch and an explicit user request; this is not a
  sandbox.

## UR5e twin pack (`ur5e_dt__*`)

The twin extension registers scene tools for a UR5e workstation: `ur5e_dt__quick_start*` variants
build a scene, `ur5e_dt__play_scene` / `ur5e_dt__stop_scene` control playback, object tools add,
move, randomise, save and restore objects, and `ur5e_dt__start_recording` captures the viewport.
`ur5e_dt__start_ros_driver` brings a ROS 2 driver up or down; use `backend="fake"` (simulation
only) and never `mode="real"` in a simulation task. Stop the fake driver before stopping its
simulator. The catalog can be listed with Kit down; calls need a selected bridge running
the twin.

## Typical safe sequence

1. `refresh_tools` → bridge listing → `use_bridge` if needed → `status`.
2. For generic work, `sim__stage__info` and keep its `session_id` / `stage_id`. Otherwise read
   with `run_python`.
3. Read: `sim__stage__tree` / `sim__prim__get` or `run_python`, plus `screenshot`.
4. Change: one named tool (`sim__*`, `assets__*`, `ur5e_dt__*`) or one small `run_python` edit.
5. Verify: read the changed prim back, `screenshot` again.
6. Report what changed, with the PID, port and (for generic tools) the session/stage IDs.

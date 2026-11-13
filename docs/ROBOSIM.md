# RoboSimStudio host bridge

`isaac_mcp.robosim.register_robosim_tools(server)` registers one grouped `robosim`
tool independently of Kit. **It needs no Isaac Sim or Kit bridge.** The server calls it only when the `robosim` toolset is
selected (off by default, for example `ISAAC_MCP_TOOLSETS=default,+robosim`, or
`toolsets(action="enable", names=["robosim"])`). RSS is never imported
into the MCP host: `examples/live/ctl` uses RSS's own `.venv/bin/python`.
Tested pin: **87b5e32** of <https://github.com/robosimstudio/robosimstudio>.
Every response identifies the checkout commit and warns about a different pin.
Missing installations give setup guidance; the bridge never installs anything.

Set `ISAAC_MCP_ROBOSIM_ROOT` to the RSS checkout. Default:
`<workspace>/tools/robosimstudio`, with `ISAAC_MCP_WORKSPACE` selecting the workspace
(otherwise `<home>/isaac-workspace`). No changes to RSS tracked source are needed.

**Which physics ran.** `device="cpu"` runs plain MuJoCo (MuJoCo's C library); `device="cuda:0"` runs
Newton's MuJoCo-Warp solver (RSS `sim/scene.py`, `build` docstring; `specs/arrangement.py`, `CPU`).
Every run's status, every result and `REPORT.md` say which. This tool **compares images; it does
not fit mass or friction**, and the report says so.

## A worked sequence

The calls below are the real argument names. Times are seconds, lengths are metres.

1. `status()` — lists runs; says `bridge_needed: false`. If your client loads tools lazily,
   search for the tool by name (for example `robosim`) before calling it.
2. `toolsets(action="enable", names=["robosim"])` — only if the toolset is off.
3. `scene_template(shape="tee")` (or `"box"`) — returns trusted Python (`scene`) with a static table,
   one red object named `block` (a T of two boxes in one rigid body, or a box), a small cylinder
   `pusher`, one camera, and a working push. Save it to a file; edit the plain-data constants at the
   top (`OBJECT_XY_YAW`, `TEE_BAR_HALF`, `CAMERA_EYE`, `PUSH`, ...).
4. `start(name="trial", scene="<abs>/scene.py", device="cpu")` — copies the scene as `scene-v1.py` (and
   `scene.py`, the file RSS loads) and starts RSS held, **without file watching: the scene is frozen
   until `update_scene`**. With no `port` it picks a free one and reports it (`port_chosen`, `url`).
   RSS's viewer listens on all interfaces; RSS offers no localhost-only option.
5. `reference(run=..., video="<abs>/clip.mp4", start_s=0, end_s=6)` — extracts anchors at 0, 3 and 6 s
   (override with `at="0:start,2:a,6:end"`) and a contact sheet of the whole clip. Anchor times stay in
   original clip seconds. Without `start_s`/`end_s`, `at` defaults to `0:start,end`.
6. `compare(run=..., keys=["block=red"])` — the live render against the first anchor: IoU, centre
   offset, `width_ratio`, `height_ratio`, `area_ratio` (sim pixels / photo pixels) and
   `linear_scale_ratio` (RSS's own `size_ratio`, the square root of the area ratio; RSS's report text
   calls it "size"). The mask for `block` covers every shape of the compound body.
7. `update_scene(run=..., scene="<abs>/scene2.py")` — replaces the scene **in the same run**: saves
   `scene-v2.py` (older versions stay), copies it to `scene.py`, and asks the running server (same
   process, not restarted) to reload. The result says what happened, the RSS generation before and
   after, the names, routines and cameras. If the build fails RSS keeps the old scene, `scene.py` is put
   back, and the version is recorded as not loaded. Also fixes a `start` whose scene did not build.
8. `rollout(run=..., take="t1", push={"waypoints": [[0, 0.10, -0.20], [1, 0.04, -0.14], [6, -0.12, 0.05]]},
   duration_s=6, record_fps=10)` — resets the scene to its start state, then plays the template's
   `push` routine and records it. The result gives `steps`, `duration_s`, `control_hz`, `every`,
   `record_fps` (what was really recorded), `video_fps`, `frames`, `frames_expected`, the push as applied
   (`push_applied`, including the pusher's tracking error) and `warnings`.
9. `compare_take(run=..., take="t1", keys=["block=red"], start_s=0, end_s=6, sim_start_s=0, sim_end_s=6)` —
   see below. Reports `displacement_px`: the object's image-centroid displacement from the first to the
   last anchor for the clip and the sim, their difference, whether x and y have the same sign, and
   `magnitude_ratio`.
10. `report(run=...)` — writes `REPORT.md` (engine, scene versions, a summary, every operation's JSON).
11. `stop(run=...)` — quits only this run's server and waits up to 15 s for the process to exit; the result
    has `stopped`, `waited_s` and `final_state`. It never force-kills. Stop every run you start.

All long actions return quickly by default. Supply `wait_s` (0–120), or poll
`status(run=..., wait_s=120)` until `operation.state` is `completed` or `failed`.
Only one action operates a run at a time. `status` without a run lists persisted
runs and identity-checked live servers. Runs are referenced by the exact name
returned by start. A different host can inspect completed runs and stop their
owned servers; an in-flight worker cannot resume after its host exits.

## How things move in RSS, and the push

What RSS supports (pin 87b5e32; paths are in the checkout):

- **Writes to `sim.state.body_q` / `body_qd` do nothing.** They are outputs: the solver rebuilds them
  from `joint_q` and `joint_qd` each step (checked on CPU: a `body_q` write followed by `sim.run(1)` left
  the block where it was). `start` and `update_scene` refuse a scene that calls `body_q.assign(...)` or
  `body_qd.assign(...)` and say what to use instead.
- **A free body is moved through its own coordinates.** `domains/rigid/entity.py` `_add_primitive`
  records `coord_start` and `dof_start`; its `reset` writes `state.joint_q[coord_start:coord_start+7]`
  as `(x, y, z, qx, qy, qz, qw)`. The template's pusher is a free cylinder placed that way every control
  step, with its velocity written to `joint_qd[dof_start:dof_start+6]` as `(vx, vy, vz, wx, wy, wz)`,
  so it pushes through real contacts. The object's optional initial velocity is the same write.
- Other mechanisms exist and were not used or run here: a kinematic/mocap channel that places welded
  fixtures once per episode at reset (`domains/static/welded.py`, not a per-step path), and a PD wrench on
  a dynamic body, `ObjectDragger` in `core/draggers.py`, a `rigid_force` substep hook that adds to
  `state.body_f`. The template writes the solver's coordinates instead because the pusher then follows the
  waypoints exactly and needs no hook (a hook on CUDA forces a graph re-capture).

The push is plain data:

```json
{"waypoints": [[0.0, 0.10, -0.20], [1.0, 0.04, -0.14], [6.0, -0.12, 0.05]],
 "object_velocity": [0.0, 0.0, 0.0]}
```

`waypoints` are `[seconds, x, y]` pusher positions on the table (the pusher moves in straight lines
between them; times strictly increase; x and y within 2 m; speeds above 1 m/s give a warning).
`object_velocity` is an optional initial `[vx, vy, yaw_rate]` in m/s and rad/s; friction stops the object
again quickly (a box given 0.9 m/s travelled about 4 cm in the CPU test). Give one or both. Without `push`, `rollout` uses the scene's own
`PUSH` constant. `routine="settle"` steps with no push and is the control run: the block should stay.
Table frame: `+x` right, `+y` away from the camera, yaw `0` has the T's bar far and its stem toward the
camera.

If a routine does not apply `push` (no `push-<take>.json` is written), `rollout` fails and says to use
the template's `push` routine or to read `ROLLOUT['push']` in your own routine. Your own routine reads
`ROLLOUT` (a dict with `steps`, `take`, `push`) from its globals.

## Shapes

`shape="tee"` draws two boxes (bar and stem) on **one** rigid body. RSS draws an object's shape through
`ShapeSpec.add_to(...)` (`specs/asset.py`); the template's `TeeShape` overrides it. Mass is rescaled
for the whole compound (`scene/build/bodies.py` `set_body_mass`). `shape="box"` is one box. Dimensions
are the `TEE_*` and `BOX_HALF` constants.

## Timing

`control_hz` comes from the scene (50 Hz for the template under the pinned RSS, read at rollout time).

- `rollout`: give `duration_s` or `steps` (not both); with neither, the push's last waypoint time, else 2 s.
  `steps = round(duration_s · control_hz)`. `record_fps` (default 10) becomes a stride of
  `round(control_hz / record_fps)` steps; the real rate is `control_hz / every` and a warning appears if
  it differs from the request. Frames recorded: step 0, every stride, and the last state. Limit 20000 steps.
  The template's `push` and `settle` routines run exactly the asked steps; a routine of your own that
  ignores `ROLLOUT['steps']` runs its own length and the result warns when it differs.
- `compare_take`: clip window `start_s`–`end_s` (default: the reference's window, else the span of the
  anchors); simulated window `sim_start_s`–`sim_end_s` (default: starts at 0 and has the same length as
  the clip window). Each anchor at clip time `t` is held against sim time
  `sim_start + (t - start_s)·(sim_end - sim_start)/(end_s - start_s)`, written as `sim_t` into
  `ref/timeline-map-N.json` for RSS. The result's `time_mapping` lists the windows, the scale and, per
  anchor, the sim time requested and the one RSS used. It **warns when the two durations differ by more than
  10 percent**, and refuses a simulated window longer than the take. (RSS itself maps by proportion of
  the whole clip when no `sim_t` is given; against a 12 s clip, a 6 s anchor lands halfway through the
  take. That is the mapping this action replaces.)
- RSS posts its own verdict to its board when a take ends, using that proportional mapping. The
  `rollout` result leaves it out; use `compare_take`.
- Each `rollout` first resets the scene, so every take starts from the scene's start state. After a
  rollout the object stays where it ended until the next rollout or `update_scene`; `compare` and
  `snapshot` show that live state.

## Outputs and device isolation

Each run is `<workspace>/runs/robosim/<YYYYMMDD-HHMMSS>-<name>/`:

- `scene-vN.py` (every version), `scene.py` (the loaded one), `reference-*.mp4` (or original extension):
  copied inputs and hashes.
- `run.json`: device, checkout commit, inputs, scene versions, command argv/logs/results and timings.
- `serve.log`, `serve.pid`, `command-*.stdout/.stderr`: process evidence.
- `ref/`: extracted frames, contact sheet, `timeline.json`, and `timeline-map-N.json` per `compare_take`.
- `snapshot-*.png`, comparison pictures/JSON, `<take>.mp4/.json/_seg.npz`, `push-<take>.json`, `REPORT.md`.

RSS caches are the only checkout-local writes: `TMPDIR=<rss>/.tmp`,
`XDG_CACHE_HOME=<rss>/.cache`, `WARP_CACHE_PATH=<rss>/.cache/warp`.
Bytecode writes are disabled. RSS can place a hashed control socket in TMPDIR
when the run path exceeds the Unix socket path limit. The bridge controls its own
output paths; trusted scene Python can still write arbitrary paths.

`start` **requires** `device="cpu"` or `device="cuda:0"`; subsequent calls inherit it.
CPU hides CUDA. The bridge checks `nvidia-smi` compute workloads before a GPU start and refuses unrelated
work unless `allow_concurrent_gpu=true` explicitly overrides that snapshot check. This is not
a GPU reservation. No existing processes are killed to free resources. The GPU path is guarded in fake
tests only; the pusher and tee were run on CPU.

This is **image-space comparison, not inverse-physics fitting**. There is no
automatic mass/friction estimation. Agents must supply a suitable scene and push and interpret imperfect
mask matches. RSS supplies no real reference video; provenance and licensing remain the caller's
responsibility. Missing masks yield null metrics rather than invented values. Scene Python is arbitrary
code, not sandboxed: do not use untrusted downloaded scenes.

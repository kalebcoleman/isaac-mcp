# RoboSimStudio host bridge

`isaac_mcp.robosim.register_robosim_tools(server)` registers one grouped `robosim`
tool independently of Kit. Host/server wiring is separate. RSS is never imported
into the MCP host: `examples/live/ctl` uses RSS's own `.venv/bin/python`.
Tested pin: **87b5e32** of <https://github.com/robosimstudio/robosimstudio>.
Every response identifies the checkout commit and warns about a different pin.
Missing installations give setup guidance; the bridge never installs anything.

Set `ISAAC_MCP_ROBOSIM_ROOT` to the RSS checkout. Default:
`<workspace>/tools/robosimstudio`, with `ISAAC_MCP_WORKSPACE` selecting the workspace
(otherwise `<home>/isaac-workspace`). No changes to RSS tracked source are needed.

## Workflow

1. `scene_template` returns trusted Python for one red rigid box on a static table,
   with named size, pose, mass, friction and camera parameters. Save/edit it locally.
2. `start(name="trial", scene="<absolute-scene.py>", device="cpu")` copies the scene
   and starts the live server held, without file watching. `port` defaults to 8080;
   **RSS's viewer binds all network interfaces**. Scene Python is arbitrary code,
   not sandboxed. Do not use untrusted downloaded scenes.
3. `reference(run=..., video="<absolute-video>", at="0:start,end", width=320)` copies
   the video and extracts reference anchors and a contact sheet.
4. `snapshot(run=..., camera="camera", size="320x240")`, then
   `compare(run=..., keys=["cube=red"])` compare a render to the first anchor.
   RSS reports IoU, pixel offset, width/height/size ratios and comparison images.
5. `rollout(run=..., routine="routine", take="trial", every=4)` waits for the
   actual recording to finish, returning its MP4 and JSON, not just a queued play.
6. `compare_take(run=..., take="trial", keys=["cube=red"])` uses the extracted
   timeline. RSS uses proportional matching unless the timeline specifies sim times.
7. `report(run=...)` writes `REPORT.md`; `stop(run=...)` quits only that run's
   bridge-owned server. Always stop runs, including after failed scene loads.

All long actions return quickly by default. Supply `wait_s` (0–120), or poll
`status(run=..., wait_s=120)` until `operation.state` is `completed` or `failed`.
Only one action operates a run at a time. `status` without a run lists persisted
runs and identity-checked live servers. Runs are referenced by the exact name
returned by start. A different host can inspect completed runs and stop their
owned servers; an in-flight worker cannot resume after its host exits.

## Outputs and device isolation

Each run is `<workspace>/runs/robosim/<YYYYMMDD-HHMMSS>-<name>/`:

- `scene.py`, `reference-*.mp4` (or original extension): copied inputs and hashes.
- `run.json`: device, checkout commit, inputs, command argv/logs/results and timings.
- `serve.log`, `serve.pid`, `command-*.stdout/.stderr`: process evidence.
- `ref/`: extracted frames, contact sheet, timeline.
- `snapshot-*.png`, comparison pictures/JSON, `<take>.mp4/.json/_seg.npz`, `REPORT.md`.

RSS caches are the only checkout-local writes: `TMPDIR=<rss>/.tmp`,
`XDG_CACHE_HOME=<rss>/.cache`, `WARP_CACHE_PATH=<rss>/.cache/warp`.
Bytecode writes are disabled. RSS can place a hashed control socket in TMPDIR
when the run path exceeds the Unix socket path limit. The bridge controls its own
output paths; trusted scene Python can still write arbitrary paths.

`start` **requires** `device="cpu"` or `device="cuda:0"`; subsequent calls inherit it.
CPU hides CUDA and uses plain MuJoCo. GPU uses Newton's MuJoCo-Warp solver; the
bridge checks `nvidia-smi` compute workloads and refuses unrelated work unless
`allow_concurrent_gpu=true` explicitly overrides that snapshot check. This is not
a GPU reservation. No existing processes are killed to free resources.

This is **image-space comparison, not inverse-physics fitting**. There is no
automatic mass/friction estimation. The template routine only settles the object;
agents must supply a suitable scene/routine and interpret imperfect mask matches.
RSS supplies no real reference video; provenance and licensing remain the caller's
responsibility. Missing masks yield null metrics rather than invented values.

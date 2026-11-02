# Camera clipping: five installed builds, both modes

These records qualify newly created generic cameras on exact combined source
`1f21407eacd02aa1387aa4348611aa60b7848a06`. They are a focused rendering and units
check, not a new all-tool, ROS, Newton, physics-motion, or clean-install qualification.
Use the committed repository setup instructions for the host MCP server and select an
existing compatible Sim interpreter. The compact records each package, Kit/build,
extension version, Python, selected backend, port/domain, PID, mode and source hashes.
The four older environments were prepared installs; 6.2 used the previously documented
locked local environment. No NVIDIA package source was modified.

## Inputs and deployment

The `reproduction/` folder contains the actual harness and JSON tool specifications.
Deploy them into `<worktree>/.work/compat-followup/`; export the exact Git source into
`<worktree>/.work/integration/1f21407eacd02aa1387aa4348611aa60b7848a06/`.
The historical wrapper explicitly selects `<worktree>/.work/host/bin/python` and a
per-version interpreter map in `launch_twin_no_upload.py`. Replace those *deployment
paths in a separate harness copy* with your own host and Sim interpreters. Do not install
stub packages: runtime APIs come from the bundled/enabled NVIDIA extensions.
The validator additionally uses Pillow, NumPy and standalone OpenUSD; the host package
versions are recorded in `analysis/camera-clipping-host-packages.json`. Do not substitute
standalone OpenUSD checks for the actual Kit rendering calls.

`manage.py` also writes the test asset approval config. Its historical approved roots and
cache refer to the larger test suite. For a new deployment, use your own approved empty
fixture/staging directories and remove unused historical roots from the local harness
copy. This camera fixture imports no external asset: the named tools create the cubes,
preview materials, dome light and cameras. No robot, Vention cache, network asset service,
ROS subscriber, Isaac Lab or additional USD source is needed for this focused workflow.
Preserve source identity and the managed launch/resource checks when adapting deployment.
The diagnostic bootstrap adds a stack-dump signal handler and disables crash uploads;
its exact text and flags are recorded separately from byte-identical product modules.

Use an X11 desktop for windowed tests. Discover and verify the selected application
window PID and dimensions before input. Do not click another session's Console. The
actual runs were serial; each owned process exited before the next launched. Use distinct
ports, ROS domains, output/staging paths and a new non-overwriting tag. The ten actual
ports are 18975 through 18984; domains 221 through 230. Headless order was 62,61,601,6001,6000;
windowed order was the same. Version keys map 6000=6.0.0.0,6001=6.0.0.1,601=6.0.1.0,
61=6.1.0.0,62=6.2.0.0, not just the three-part window title.

## Actual sequence

From the worktree root, after adapting only deployment paths and choosing an unused tag:

```bash
export TEST_FINAL_SHA=1f21407eacd02aa1387aa4348611aa60b7848a06
export TEST_NEWTON=0
export DIAGNOSTIC_RUN='<new-owned-tag>'
<host-python> .work/compat-followup/launch_twin_no_upload.py \
  '<new-owned-tag>' '<version-key>' '<headless-or-windowed>' '<port>' '<ros-domain>'
```

Read the returned session record, confirm its PID and readiness. The client checks that
PID again through MCP `status` and `sim__stage__info`, refreshes session/stage targeting
following each new stage, and records every actual argument and response:

```bash
<host-python> .work/compat-followup/diagnostic_mcp_strict.py \
  '<new-owned-tag>' '<port>' '<owned-pid>' .work/compat-followup/camera-units-spec.json
<host-python> .work/compat-followup/diagnostic_mcp.py \
  '<new-owned-tag>-units-error' '<port>' '<owned-pid>' .work/compat-followup/camera-units-error-spec.json
<host-python> .work/compat-followup/validate_camera_units.py '<new-owned-tag>'
```

The 42 positive calls create two anonymous stages (meters and centimeters), two cameras
per stage at 128x128/384x384, and make eight fresh image captures. The red/green targets
start 23 cm from the camera. Moving red inside the 1 cm near plane removes red pixels while
green remains; returning it restores red. The eight-call error control sets an explicitly
invalid zero stage-unit value in a disposable new stage and verifies typed refusal,
no camera prim, unchanged root and restored edit target. Raw Python is confined to this
bounded fixture metadata setup and independent read-only inspection; it does not replace
the named creation, material, transform, camera, capture or save tools.

The validator's 91 predicates include 42 result success flags and 49 count/metadata/pixel/
hash/saved-USD/error predicates. They are not 91 independent physical effects. Two saved
outputs per run are opened with standalone OpenUSD for clipping attributes. That is **not**
a fresh Kit reopen test or a promise to repair existing cameras. Geometry and clipping
opinions authored by the pack use the session layer, but native RenderProduct/settings
allocation also changes the anonymous root. Successful captures do not prove whole-root
preservation. Color targets are horizontally cropped in diagnostic camera images.

For windowed runs, `camera_windowed_phase.py` records the actual start/negative/stop
commands and `desktop_console.py` uses real host MCP screen tools. Inspect the before/
after window captures, then click the visible Console tab using its verified dimensions
and PID. Capture and inspect the Console again after negative controls. Do not blindly
reuse historical coordinates or auto-fill `record_camera_visual.py` without actual pixel
review. The final UI counters and full native-log counts have different scopes.

```bash
PYTHONPATH='<exact-source>/mcp' <host-python> .work/compat-followup/manage.py stop '<new-owned-tag>'
<host-python> .work/compat-followup/final_twin_log_review.py '<new-owned-tag>'
```

Only stop the managed PID you started. The stop helper waits for exit; do not launch the
next process while the prior PID remains. Retain failures and original partial records.
The first headless batch had a harness stop/PID timing error after successful 6.1 effects;
the later bounded-wait v2 resumed the remaining builds. The first 6.2 desktop click was
rejected by schema for missing dimensions before input, then corrected. Neither is
silently rewritten as a product failure or omitted.

## Evidence and privacy

`COMPACT.json` indexes the ten cases. `PER-TOOL-CALL-REFERENCES.json` includes auxiliary
viewport/desktop calls separately from the 500 core calls. `ORIGINAL-RECORD-HASHES.json`
identifies private original bytes; `REDACTED-SHA256SUMS` verifies the distributed projections.
Full native logs are redacted, with their original final hashes retained. Saved USDs,
most PNGs and all user/vendor assets are omitted. Four explicitly pixel-reviewed original
samples are included under `reviewed-images/`: 6.2 window before/after and headless red
visible/near-clipped camera images. No robot asset license or unrelated desktop content is
introduced by these synthetic fixture images. Other images have hashes and scoped review
records, not a claim that every frame was inspected. The release remains draft; authenticated
repository access is required. Neither warnings nor clean fixture images settle the separate
Newton failures, ROS/Fabric black outputs or original 20f stalled task.

# Validation after merging the planning PR

The exact `2bbeee4a1eb8ab806be44ca7120cdf599dd923e8` 6.0.0.1 headless continuation
passed 21 save/preservation assertions and fresh ROS-first reopening. Its next sensor
sequence produced 18 valid captures at three resolutions and measured the LiDAR wall,
then failed Stop with `TIMELINE_BUSY`: native Replicator remained STOPPING for minutes.
The dispatcher had reset to zero while the last scheduled capture time was 127/30;
the native backend reported done and no writer schedules remained pending. An explicit
private diagnostic completion call recovered and left both recorded live layer hashes
unchanged. Named Play/Pause worked afterward. This is diagnostic recovery, not a product
pass. Source fixes `a9d8e0f` and `3ca5fe6` add guarded, awaited completion to requested
Stop only, including cancellation and external edit-target preservation tests. Fresh product qualification at `4499bf7` passed the same reopen sequence in both modes.
The failed process was stopped through the managed-session tool; the original failure
and diagnostic intervention remain separate from the fresh product results.


This is a chronological record: the earlier commit-specific results and failure history below
remain historical. The combined candidate at this update is PR #16 at
`c2ceb7f1b8130f414872cb4475ffdd1c2d38b96a`, still draft and unmerged. No original
implementation PR is being merged separately. The [all-tool ledger](ALL-TOOLS-MATRIX.md)
tracks the current 90 default / 99 optional-inclusive tool surface.

Fresh 6.0.0.1 PhysX qualification at `4499bf70c5f06c37cdf928dd1f3d2c29e5abf621`
completed the broad suite in both headless and windowed modes. Each passed all 14 phases,
including 27 fresh-process reopen captures, 12 immediate camera attempts, 23 cache checks,
project preparation/import, asset refusal/recovery, all 39 generic tools, all 23 UR5e tools,
and seven bridge built-ins. Headless full-surface results were 137 calls / 55 effect
assertions; windowed results were 141 / 54. Simulated Jazzy publishers, TF, fake-driver
trajectory action and gripper command produced observed joint/pose and sensor effects.
Headless legacy/rebuild and existing-physics-scene regressions had no new native errors
in their measured intervals; a later save passed 21 preservation checks.

The headless video decoded 172 frames; two were visually sampled. Its diagnostic zero-pose
view crops the upper arm/wrist. The windowed application showed a normal bent UR and
attached RG2. Its actual Console showed 359 warnings / 63 errors; the search displayed
intentional missing/corrupt asset and invalid-quaternion tests. Native-file counts are
separate: 24 error lines, 22 cycle warnings and 137 simulation-time warnings in the
pre-independent-use interval. No nested-task, GetCamera or Force-callback messages occurred.
Eight temporary Console captures/copies were hashed and deleted. A later independent
windowed task made 93 requests across 35 names, with 22 passing effect checks and one
framing limitation. Raised/rotated Agilebot targets reached 0.000125 rad maximum error
with original drives; the sample settled on the known support, and 720 exact steps
measured six seconds. UR returned within 2.2e-5 rad with attached RG2. The final window
video decoded 98 frames; the original occluded framing/video is retained. Actual Console
counts grew to 374/63; the task interval added 15 warnings and no errors. Later windowed
legacy and existing-physics regressions passed with zero native errors, reentry, cycle or
time warnings in their separate intervals. The owned process exited.

The combined `4499bf7` CPU suite passed 1,182 tests and the minimum SDK suite passed 216.
It retained a bridge accept-thread EBADF shutdown warning alongside the intentional
ZIP warning; `d20e262` moves listener setup before worker startup and binds each worker
to its listener generation. Deterministic lifecycle and related gateway tests passed;
its fresh native startup/teardown qualification is pending. The current combined source
includes this fix and documentation updates without relabeling earlier native runs.

A separate post-regression anonymous-camera fixture demonstrated that the original
`sim__viewport__frame` moved a camera authored in the root layer and changed that layer
in memory. No disk overwrite was observed. This does not explain the agent's earlier
occluded view. Source97325af confines supported native framing to session edits, restores
the prior target, guards context/camera/bounds and reports rendering as unverified.
Fresh combined37a on 6.0.0.1 windowed passed six source/reference/edit-target checks.
A deliberately injected native false return then exposed an error-wrapper `TypeError`
from duplicate `effects` fields. Source5717217 fixes that response path with an actual
handler regression. Complete fresh live qualification remains pending; neither the
prior broad suite nor the six preservation checks supersede this failure.

Additional validation on 2026-11-01 found and fixed two independent classes of failure.
A 6.0.0.0 native Replicator Stop drain could stop a newly requested Play; the original
`8106771` failure is retained. `6998827` passively waits for the previous Stop and
reports an interrupted requested state instead of success. At combined `d9322605`
(headless) and `2fd14386` (windowed), fresh save/reopen and the full 39 generic / 23 UR5e /
seven bridge-built-in suites passed. Their native runtime was unchanged through
`2bbeee4a1eb8ab806be44ca7120cdf599dd923e8`; later Stop-completion changes require
affected-path requalification. Windowed independent use made 71 requests,
with 20 passing effect checks and one retained multi-joint target miss followed by a
successful smaller target. It did not establish that every reachable pose is achievable.

The legacy verifier separately produced seven nested-asyncio errors in its recursive
`app.update()` loop. The asynchronous harness in `7829834` passed the unchanged numerical
validator on the owned 6.0.0.0 windowed session: −0.099999219 rad achieved, return error
−2.55e−9 rad, 434 ordinary frames, and zero new native error/nested-task lines. An initial
harness check ran before deferred Play committed; that failed attempt and its correction
are retained. A separate property-window `save_scroll_pos` callback error during a
headless stage reset is also retained, without a harmlessness claim.

The Lab 3 / Sim 6.2 workflow exposed run-directory parsing and concurrent metadata-read
bugs. Both were fixed, with deterministic CPU regression tests. Exact combined `411d89f`
passed all 12 Lab tools in 41 real MCP calls and 16 behavior checks: actual two-iteration
training, checkpoints, TensorBoard metrics, a decoded evaluation video, visible windowed
policy playback and cancellation. A further headless-video test found the host incorrectly
selected `--viz none`; `d7430ae` keeps a Kit renderer with child-only `HEADLESS=1` for video.
The exact changed bytes on a private `33de98d` base passed headless eval and play with
60 distinct decoded frames each and no observed windows, plus windowed training/play and
plain headless training. Console review showed 22 warnings and zero errors. All owned Lab
processes exited. This is the installed Lab 3 / Sim 6.2 / Newton MJWarp pairing, not proof
of Lab support on every Sim release or the generic Newton adapter.

Video required optional MoviePy and imageio-ffmpeg in a private environment referencing
the existing Lab packages read-only. No user environment was changed. The overlay's
`uv pip check` could not see inherited packages and reported missing dependencies; runtime
imports and actual video worked, but this overlay is not a validated clean-install recipe.

The earlier bounded, tool-acquired camera cache change was `6f6536e`.
It is present in combined `a6308fa` and subsequent documentation-only heads. A fresh
`wait_for_render=false` capture returns `NOT_READY`; ordinary Play does not seed it.
A validated acquisition commits the cache before PNG publication, so a filename conflict
can preserve the old output file while a subsequent cached capture exposes the newly
acquired pixels. Stop retains explicitly stale acquisition metadata; delete/recreate
invalidates it. Real MCP tests passed 23 checks in 27 calls, including changed red-pixel
position, old-file preservation and same-path recreation. The CPU result on `a6308fa`
was 1,092 host tests and 216 minimum-SDK tests, plus focused/package checks. Changes through `8106771` only corrected documentation and a source comment;
subsequent runtime fixes are recorded separately. Those runs keep their original SHAs.

Earlier 6.2 PhysX qualification passed both headless `final62h_2729` at `2729b076`
and windowed `final62w_884` at `884a8fa6`: 39 generic tools, all 23 UR5e tools,
seven bridge built-ins, source-preserving project preparation/import, cache failure and
repair, fresh ROS-first saved-scene reopening, 27 reopened sensor captures, 217 stress
captures, runtime edit-target restoration, measured simulated ROS arm/gripper motion,
TF, RGB/CameraInfo and decoded viewport video. Headless `verify.sh` and the existing
`/physicsScene` regression also passed. The real windowed Console was inspected; its
intentional asset-missing/corruption/invalid-quaternion errors are distinguished from
native warning observations. The independent agent workflow is complete with a retained RG2 controller-conflict
limitation. The remaining release/mode/backend matrix is pending; Lab results above
apply only to the installed pairing and recorded code.

Native cycle warnings are **not resolved**. Fresh camera-only and LiDAR-only controls
reproduced them while RGB/CameraInfo continued publishing; suppressing updates on the
owned LiDAR texture did not eliminate them and was not adopted. Whole-run and settled
phase counts differ and are retained separately. Removing the generic RGB annotator is
not established as a cycle-warning fix. Warning observations alone do not block further
qualification; demonstrated crashes, wrong results, data loss and unreliable recovery
still require action. No general harmlessness or upstream-cause claim is made.

Validation is in progress; this file is not a merge-readiness claim. PR #10 merged at
`7429dbdba6f020ead193ae26175535d4d9f9ae28`; PR #11 was rebased without changing its existing
file content, then fixed the reopened-camera defect at
`d3296b023c219e1f2d389c7755f36b3c9c2fc760`.

CPU:908 passed (one intentional duplicate-ZIP-member warning). Minimum MCP1.22:203 passed
(one retained upstream Pydantic warning). Wheel/sdist, npm dry-run, docs/catalog and privacy
checks pass. The new save tests cover synchronous/asynchronous native orchestration shapes,
source preservation, user-connected graphs and unknown graph refusal.

The installed Replicator1.13.4 source checks for `/Orchestrator` before creating its update
subscription. A saved native graph therefore survived reopening while its Python subscription
did not. The disposable diagnostic reproduced NOT_READY and found no subscription; restoring
it diagnostically then playing made both playing and paused captures work. Product code uses
no private subscription patch: it omits recognized native orchestration from a private export.
A fresh process reopened the corrected export and passed16 real MCP checks. A separate headless
process reopened the independent agent's newly saved bench and passed the same16 checks.

6.0.0.0 at that exact product head: all37 generic tools,217 camera captures across three
resolutions, measured LiDAR planes, timing/body/joint observations, refusal cases and20/20
UR5e smoke after sensor-stage teardown pass. Independent actual use made85 MCP requests with
31 passing behavior/preservation checks; six exploratory refusals are retained. The sample
rested at1.125m with zero velocity;480 steps measured4s; fixture±0.2rad and real Agilebot
joint1+0.3rad reached the stated0.01rad tolerance. All17 source files and94 original scene-node
tuples were preserved. Window46137355/PID2023586 was inspected. Those processes were stopped
through the managed-session tools. Other release/backend and combined integration runs remain
pending until their records are added here.

Independent actual use at `d3296b0` also found a distinct6.0.0.1 stale RGB result: the
first camera frame after240 measured physics steps still showed the airborne sample.
The agent's failed visual assertion is retained. Four Hydra frame-counter changes were
insufficient because the paused Replicator CPU pointer did not necessarily advance.
A diagnostic zero-time Replicator orchestrator step stalled; it was not adopted.

Product fix `1b44d80` uses public Hydra drawable events for the owned render product and
`capture_next_frame_rp_resource_callback`, copying a bounded RGBA8 CPU capsule with Kit's
public converter before returning. No CUDA pointer, global orchestrator advance or cached
fallback is used for default capture. Callback completion, stage changes, cancellation,
late callbacks, missing APIs and invalid buffer formats have9 CPU regression cases.
The full current CPU suite passed917 tests; minimum MCP1.22 passed203. Build/package,
docs/catalog and privacy/history checks passed. Existing warnings remain recorded.

The6.0.0.1 `release6001` process frozen at `245befe` passed all37 tools and217 stress captures;
its product files are identical to `31b6a62` (the later commit changes only test framing).
The new immediate Play/Pause/step/capture test passed18 captures across64²,320×240 and1024×768,
with no multisecond warm-up between initial capture and physics steps. Initial harness errors
(missing PhysicsScene/light, wrong result-field name and clipping the airborne body outside
the test camera's field of view) are retained separately from product results. Runtime rest
and red-pixel motion were both asserted. Further releases and independent retests are pending.

The machine remains Ubuntu24.04.5, kernel7.0.0-38, RTX5080(16303MiB), driver580.178.04,
Python3.12.3, host MCP1.30.0. The release list was rechecked on2026-11-01: installed published
6.x packages are6.0.0.0,6.0.0.1,6.0.1.0,6.1.0.0 and6.2.0.0. Package/Kit/extension identities,
launch flags, ports, PIDs, adapter/backend, commands, transcripts and cleanup are retained in
the pending validation release supplement. No inference to missing/future releases is made.

Isolation, authenticated remote upload, presets and remaining parity remain explicitly deferred
first-slice work. Independent remote Claude execution previously failed before test commands;
no remote success is claimed. No PR merge is authorized by this evidence alone: the owner
requires applicable exact-head and combined tests before the requested merge sequence.


The updated6.0.0.1 independent workflow at `610cd76` passed41 checks in72 MCP requests,
with no MCP errors. Both immediate post-step images changed from airborne to resting, and
720 measured steps advanced6 seconds. The17 source files and93 existing scene-node tuples
were unchanged. A fresh headless process at `2be5035` reopened that paused export, verified
27 captures at three resolutions in playing/paused/stopped states, measurable LiDAR geometry,
resource cleanup and16 additional saved-stage/runtime checks.

A separate UR5e regression appeared with Motion BVH: repeated stop/add-object sequences
crashed6.0.0.1 natively, including a fresh process with no generic camera calls. Motion BVH off
passed20/20. The documented async-throttling workaround did not prevent the crash. Loading the
bench while stopped passed, narrowing the cause to reference composition during playback.
A diagnostic pause/settle/resume wrapper passed20/20 plus the preexisting `/physicsScene`
case. The production wrapper initially used an unavailable `Timeline.is_paused` method; that
failure is retained. `2be5035` uses the actual `is_playing`/`is_stopped` interface and passed
20/20 headless smoke plus9 existing-physics-scene calls after sensor-stage teardown. It leaves
stopped/paused timelines unchanged and never resumes a replacement stage or a concurrent Stop.
Twelve CPU cases cover native failure and timeline/stage restoration. No nested-update or
viewport reset error appeared in that run; the randomizer's preexisting min-separation refusal,
ROS warnings and bridge disconnect warnings remain in the native logs. Windowed final-wrapper
qualification and the other releases remain in progress.

The full CPU suite at `610cd76` passed929 tests. Discovery timeout and failed-refresh tests
confirm that a stalled/failed discovery keeps the prior registry and host recovery tools.
The combined candidate is being rebuilt from the new implementation and skill heads; the old
939-test candidate is not counted as validation of subsequent changes.


The owner expanded the pre-merge gate to every currently registered tool: the real integrated
MCP inventory lists88 defaults and97 with desktop input and managed sessions. The
[all-tool ledger](ALL-TOOLS-MATRIX.md) tracks all names, including complete UR5e/bridge/ROS,
actual desktop and compatible installed Lab workflows. These are validation of existing
functionality, not new feature batches. No merge will be based on the37-tool suite alone.
Combined candidate `8cf750ad579e0a8c10b6d19a372afb70bd5c68b5` passes951 CPU tests,
208 minimum-SDK tests, wheel/sdist/npm and docs/catalog/assets/privacy checks with no asset
privacy exemptions. The initial combined CPU command accidentally resolved the virtualenv's
Python symlink to the system interpreter and failed collection for missing dependencies;
the corrected command used the virtualenv interpreter unchanged. Both logs are retained.


The broader independent run on combined candidate `8cf750ad` found real object-rotation and
UR5e rendering defects; its failed assertions and actual-window captures are retained. PR #14
now authors an active ordered transform stack and expands only the loaded UR5e visual
instances, leaving source USDs, collision instances and other robots unchanged. Fresh
6.0.0.0 retesting exercised all23 UR5e and all7 bridge builtins, with53 behavior assertions.
An independent c8ce207 workflow then verified yaw35°, simulated TF yaw20°, assembly rotations,
nonzero-orientation save/restore, measured robot movement and connected arm/gripper rendering.
An external Stop during its final observation returned the robot to its authored starting pose;
that event is recorded separately from the earlier rendering defect. Desktop-input success
paths remain pending because external desktop activity was observed.

The same independent run found that host recording metadata described input dimensions as
output dimensions. `4bc9017` reports the actual even-sized encoded dimensions; an actual-window
recording with max_width961 reported and decoded960×600 with23 frames. The independent retest
also decoded960×600 output from an explicit window-only recording. Full-screen recordings keep
their distinct source/output dimensions.

Native-log review found that the fetched RealSense USD had relative MDL references, contrary
to the original manifest note. PR #14 `1a9009a` preserves its upstream cache hierarchy and
pins three MDLs plus six textures. No NVIDIA file is rewritten or redistributed. A fresh
6.0.0.0 process at combined candidate `abe1db5` passed27 MCP calls and10 effect/refusal checks:
missing robot sublayer, corrupt root, missing offline root, missing sensor material, repaired
robot/camera, placeholder bench, restored real bench, and identical restored cache bytes.
The corrected camera load emitted none of the previously observed MDL resolution errors.
A harness-local target-binding error on the first attempt is retained and was corrected before
this passing run; it was not a product error.

Combined `abe1db544a893aa7f41ac8aff1073eebc87c9d5c` passes977 CPU tests,208 minimum-SDK tests,
docs/catalog/assets/privacy checks with no legacy asset exemptions. Live qualification across
all five releases, applicable backends and compatible Isaac Lab is still in progress. These
intermediate results do not authorize merging around the remaining gates.


## Current combined candidate and Console review

Candidate `c75455d827bcdf9defead534e6be59713a01f421` combines PR #11 `6891967`, PR #14
`3a2302f`, PR #13 `46e5a09` and PR #12 `0ab0d6b`, plus removal of the six obsolete privacy
exemptions. The real MCP catalog still contains 88 default / 97 optional-inclusive tools.
No new project-preparation PR is included or authorized for this merge sequence.

The final 6.0.0.0 PhysX run used windowed Kit, Motion BVH, port 18869, ROS domain 178,
PID 2173747. It exercised every UR5e and bridge built-in tool: 141 recorded calls and
54 behavior assertions, including FMB4 schema/placement, real licensed bench, source-preserving
poses, real simulated ROS trajectory/gripper movement and decoded viewport recording. A
startup guard refusal on the initial attempt is retained; a retry after checking that no
driver remained completed. No unrelated process was stopped. The final screenshot shows the
connected bent robot, gripper and bench. These results do not substitute for the other versions.

Camera/TF fixes set the camera's ROS name to `camera_link` and use one monotonic simulation
clock. Native TF already performs the optical-axis conversion; no extra rotation was added.
The first ROS-camera save refused unknown native writer content; its fix requires a preserved
matching camera helper and render-product source. A second refusal exposed Kit's global
render-settings relationship to the pack's temporary capture product. The saved copy now removes
only those links, preserving user products and refusing other native-state dependencies.

A fresh process (PID 2179423, port 18870, ROS 179) reopened the saved workcell and passed
16 ROS/camera/save assertions, including actual image buffers, optical-frame transforms,
Stop/Play clock continuity, fresh generic capture and another saved copy. An initial test
threshold incorrectly required every image timestamp to have a received TF sample within
25 ms. The retained data shows dropped/asynchronous TF delivery (maximum observed gap
133 ms), with image times on the same simulation clock. The corrected check uses the measured
TF intervals and overlapping timestamp range; it does not claim atomic sensor/TF capture.

At the owner's request, the actual Console tab was selected, expanded, filtered and scrolled
using PID-checked MCP desktop tools. It showed **36 warnings / 0 errors** before another
Stop/Play cycle, then **53 warnings / 0 errors**. Fourteen warnings per initialization concern
large collision triangles. The bench loader is explicitly visual-only and retains the ground
plane collider; the source of triangle warnings is checked separately, not inferred from the image. Other warnings include startup deprecations,
missing material-binding metadata, small render resolution, native Hydra interface release,
one missing render-variable warm-up, and client disconnects after a failed test assertion.
The installed NVIDIA CameraInfo helper also forces `fy` to `fx` for square pixels; the two
approximate default FOV values produce a small difference. These are retained limitations,
not a blanket claim that warnings are harmless. Camera pixels and TF were checked after warm-up.

Earlier logs distinguish deliberately missing/corrupt assets and invalid-quaternion tests from
actual defects. The RealSense MDL failures were real and were fixed by preserving its source-relative
cache hierarchy and fetching pinned material/texture dependencies. Temporary Console images
with workstation paths were inspected and deleted; only hashes and redacted notes are retained.
All five optional desktop input tools were exercised on the real simulator window: click selected
the Console/search field, drag expanded it, typing filtered to the Hydra warning, key input cleared
the filter, and scrolling exposed startup messages. The viewport area was restored afterward.


The independent `c75455d` workflow made108 real MCP requests and preserved185 project/cache
files plus101 frozen source files. It measured all three articulation targets within0.001rad,
observed gravity, decoded100 desktop-video frames and saved the paused workcell. The Console
ended at115 warnings /0 errors. Sustained native ROS RGB delivery remains under investigation:
its post-save sample received5 images in4seconds, while TF and CameraInfo continued. A further
three6-second observations without another save received61,37 and69 images; the middle
interval stopped receiving RGB early. This does not establish an export-specific cause.
The host has recorded UDP receive-buffer drops, but their causal connection has not yet been
measured. No ROS reliability or warning-cleanliness claim is made from these observations.

A separate user's repeated6.2 headless report led to a new cold-sensor reproduction on
`c75455d`. Six newly created cameras, with immediate initial and retry captures at64x64,
320x240 and1024x768, all returned `IHydraTexture::getFrameData for 0 failed` (12 failed calls).
An immediate LiDAR read with `wait_for_render=true` reproduced the same error. The failure
was an eager frame-metadata query before a first drawable existed, before the requested wait.

PR #11 fix `c8556c9` uses only the render-result handle supplied by the matching drawable
callback. The first completed frame establishes a baseline; camera capture waits for four
further distinct completions and the owned CPU transfer. LiDAR's render wait uses the same
bounded event-based observation; it still does not promise a new accumulated scan. Native
callback errors, target changes, timeouts and late-transfer safeguards remain explicit.
The [installed/public Hydra contract](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.hydra_texture/1.6.0/Events.html)
requires consuming render-result handles inside their callback, without caching them.

On combined candidate `39e1001`, fresh managed6.2 headless PID2211104 /port18872 /ROS181
passed all12 cold captures with actual red-object pixels, in0.08–0.29seconds each, without
playing or caller-side warm-up. Both immediate LiDAR reads returned measurable wall geometry
at2.5metres. The complete37-tool suite then passed: core32 calls, equivalents48, edges23,
sensor stress327 (217 captures), and clear4. Camera, LiDAR, joint/physics timing and pixel
movement assertions passed. Sixteen CPU readback/event tests pass, including cold handles,
duplicates, timeout, target change and native errors. These results do not qualify other
releases or replace the remaining all-tool, windowed, backend and independent-use gates.


The owner subsequently required headless and windowed parity separately for every applicable
exposed tool. A reported independent PR #15 run reproduced the old cold-capture failure on
all five installed releases in both modes; delayed retries passed. Those observations are
reported separately from this coordinator's directly retained captures. Those earlier candidates excluded PR #15. The owner subsequently chose combined draft
PR #16 containing #11–#15, with all originals kept unmerged and final code review still required.

The `39e1001` integrated CPU suite passed1001 tests (one intentional archive warning), and
minimum MCP1.22 passed214 tests (upstream Pydantic warning). Wheel/sdist, npm dry-run,
docs/catalog/assets/privacy passed. Its fresh6.2 windowed PID2218151 /port18873 /ROS182
also passed12 cold captures and all23 UR5e/seven bridge built-ins:141 calls,54 assertions,
simulated ROS elbow/gripper motion, FMB poses, recording and actual-window inspection.
The inspected final frame shows a connected bent arm. Temporary Console captures were
removed after inspection:14 owned files,6,400,222bytes; hashes remain. Console124warnings /
11error entries corresponded to6Error-tagged native-log lines plus displayed traceback text
for the deliberately invalid quaternion. Subsequent verifier errors are separate below.

The unmodified legacy `verify.sh` then found two failures on that owned scene. Repeated
Quick Start under the generic runtime session edit target removed only a local graph spec,
leaving the composed gripper graph; native recreation refused its existing path. The old
checkpoint exporter counted zero relative assets because it replaced only `file://<repo>/`
strings, while the current cache uses ordinary absolute paths. The single-joint motion itself
passed (-0.09999917rad, returned error3.12e-9rad); no nested-update warning appeared in that
6.2 run. These are retained failures, not a passing legacy-verifier claim.

Local PR #14 candidate `7fd930c` validates and reuses the two fixed gripper/force graph
contracts without deleting another layer's graph, and exports a private Sdf copy with local
asset paths made relative to the output. Conflicting graph contracts are refused and preserved.
Seven real-OpenUSD CPU tests cover composition, mismatched graph inputs/types/connections,
source preservation and cache/file-URI checkpoint reopening. This fix is undergoing live
verification on integrated `4467336`; it is not yet declared successful across versions/modes.

The owner selected one combined review PR, #16, containing #11–#15; all originals remain
unmerged. Initial combined head `8a257b87fc270dfb39b6dff5a34319921a70d9b4` exposed
90 default tools and99 with desktop input and managed sessions enabled; scene opt-out left60.
Fresh6.2 headless PID2246692 /port18875 /ROS184 passed12 immediate captures at three
resolutions and the full generic core/equivalence/edge/sensor/clear suite (217 stress captures).
These records are historical evidence for8a, not qualification of a later replacement.

The same exact head reproduced an edit-layer lifecycle defect: generic Play/Pause followed
by `ur5e_dt__stop_scene` left the managed session target selected after Stop. Fix
`e05cffeba50e68c9ff7a90780b18738ab2043433` observes native STOP and releases the lease after
an ordinary frame, with cancellation on Play/closing/shutdown and guards against a changed
stage, lease or user-selected layer. Native restoration failure retains a retryable lease.
Eight new real-USD asynchronous tests and64 focused regressions passed. The combined owner
incorporated it as `642d579eda69d5dff76a74b029156aab092fbe10` (tree
`5a58eab4ded8bcba32395efd38dc4a8783e02140`). Fresh6.2 headless PID2249514 /port18876 /
ROS185 passed four generic Play[/Pause]→UR Stop transitions and one Play→new-stage transition,
with native edit-layer readback. Legacy UR Stop acknowledges before the ordinary frame ends:
an initial immediate-state assertion sampled the pending lease, although the following native
read already showed the restored root. The corrected bounded-completion test retains every
intermediate response; it does not require synchronous completion from that legacy tool.

The earlier ROS delivery observation was investigated with a subscriber-local buffer A/B/A
experiment on6.2 headless `4467336`: the repository DDS profile received229 RGB messages
and recorded18199 UDP drops in10seconds; a4MiB receive buffer received720 RGB/720 CameraInfo
messages with zero drops; reverting received244 RGB and18100 drops. Counters came from the
subscriber's own socket inodes, not cumulative machine counters. No global kernel setting or
user configuration was changed. This establishes a local transport bottleneck for those runs,
not an export-specific renderer failure or a guarantee for other networks/versions. Exact
combined-head and cross-version repetition remains pending.

On642d579, the complete6.2 headless generic suite passed again (217 stress captures), as
did all23 UR5e/seven built-ins:137 MCP calls,55 behavior checks and171 decoded video frames.
The prepared #15 Vention wrapper was used. Four external-Stop transitions and a stage change
restored actual USD edit targets. The ROS camera workflow with a subscriber-local4MiB receive
buffer passed live RGB/TF/CameraInfo, optical axes/center and monotonic Stop/Play checks, then
refused saving: native Replicator1.13.38 generated `OgnRationalTimeSyncGateOnce`, absent from
the export adapter's recognized native graph types. No output was published or live content
removed. Installed writer/annotator source and the native node test were inspected and hashed.
Fix `f8c8c0f012c676d31d6b73bdf9d9577afb4f0d59` recognizes that exact native type; unknown
types still refuse and now report their prim path/type.55 focused tests passed, including three
new cases. Replacement combined head34b24a2 is undergoing fresh live save/reopen and matrix
validation. The6.2 weld diagnostic on642d observed both mount frames aligned before/after
Stop and after360 actual physics steps (maximum translation residual6.4e-8metres and angular
residual5.8e-5degrees). This rules out persistent mount separation in that measured run; it
does not erase the initial native disjoint-joint warning or qualify other releases.


## Later viewport and URDF qualification

Combined `eea18c823f5e462763297951039771f80467e450` passed the fresh 6.0.0.1
windowed viewport fixture: 46 MCP calls, 12 checks, root/reference layers and prior
edit targets preserved, typed partial errors on injected native false/exception,
and conservative refusal of unsafe camera targets. Actual application pixels show
the selected cube framed; Console showed 16 warnings and zero errors. Native error,
cycle, time, reentry, GetCamera and Force-callback counts were zero in that fixture.
The earlier `4499` mutation and `37a` error-serialization failure remain failures.
The [draft viewport supplement](RELEASE-ASSETS.md) includes all three identities.

The next 6.0.1.0 headless broad run at `eea18c8` passed 14 phases, including 137
full-surface calls / 55 assertions, 27 reopened sensor captures, cold/cache sequences,
legacy motion and existing-physics-stage checks. Its saved workcell passed all 21
assertions and matching source/live-layer hashes. The enclosing harness unexpectedly
exited 143 after writing these records; a fresh MCP client observed a ready process
and successfully stopped the timeline. This unexplained harness exit is retained,
without a native-crash inference. The final native-file sample contained 24 expected
negative-test error lines and 18,020 simulation-time warnings, with zero cycle,
reentry, GetCamera or Force-callback matches. All 173 video frames decoded; two were
visually sampled, with upper arm/wrist cropped at the diagnostic zero pose.

Stronger all-tool observation checks then exposed a real URDF import defect. The
single-link conversion selected `Physics=physx` although only `none` and `physics`
existed, composing geometry/collision but no body or mass. Retained older 6.0.0.1 and
6.2 output shows the same missing-physics result under invalid/empty selections;
older 6.0.0.0 output offers `physx` and composes a body. These offline artifact reads
are not new native runs. Source `beb4cfa` validates the shared `physics` variant on
private generated output before snapshot/import and refuses missing expected bodies
or movable joints. It changes neither the input project nor the native physics layers.

Fresh combined `230f4b3f58e294ee3f0474b6d61a857edd965022`, 6.0.1.0 headless PhysX,
passed 24 MCP calls / 12 checks: independently reopened USD confirms single-link
mass 1 kg, collision, dimensions 0.2×0.3×0.4 and requested placement, plus light
creation/modification and independent clone edits with source preservation. A separate
two-link original URDF passed 25 calls / 7 checks: measured joint position
0.199782684 rad for a 0.2 target, 0.5 kg arm mass, changed body orientation, two
120-step intervals of exactly one second, and authored/runtime joint-limit refusals.
The first harness attempt failed parsing an expected error prefix; that record is
retained separately from the corrected run. Native error/cycle/time/reentry/GetCamera/
Force-callback counts were zero in this fresh headless process, which exited through
the managed stop tool. This does not establish other-version or Newton URDF behavior.

`adb3f66`, integrated in `20f2953`, adds the missing independent imported-physics
assertion to the standard live suite and clarifies successful import versus conversion
preservation. Product runtime is unchanged from `230f4b3`; the 6.0.1.0 windowed suite
on this exact candidate is underway. All 99 registered names now have historical
invocation records; this is not 99-tool all-version verification or merge readiness.

## 6.0.1 windowed: broad passes, native stall, and separate checkpoint correction

The exact `20f2953` windowed run completed all 14 broad phases: 141 full-surface
calls / 54 assertions, 24 calls / 12 independent USD effect checks, and 25 calls /
7 articulated URDF checks. Legacy motion and the existing-physics-stage regression
also passed. The actual Console showed 258 warnings / 63 errors; its reviewed
pre-agent native interval contained 24 expected negative-test error lines and 61
simulation-time warnings, with zero cycle/reentry/GetCamera/Force-callback matches.
All 186 video frames decoded; two were viewed, with the upper arm cropped at the
diagnostic zero pose. These scoped passes precede the following material failure.

An independent agent completed 52 requests across 30 names, then its 108-step request
did not return. The owned PID remained alive; a preinstalled faulthandler localized
the main thread to native synchronous `SimulationManager.step` / `simulate`.
Native debugger attachment was unavailable. The actual window retained the pre-step
pose; no post-stall Console repaint or native cancellation was established. Managed
graceful stop did not exit within 25 seconds; force stop terminated only that owned
process. **The native stall remains unresolved.** Stack localization and cleanup do
not establish a cause, recovery, or a crash.

A different process using the same `20f2953` runtime reopened the same checkpoint
and completed the equivalent step. Eight additional Stop/Play/joint/capture cycles,
76 MCP calls, each advanced simulation time by 1.8 seconds over 108 steps. This is negative reproduction
evidence, not resolution. A later independent task in that process explicitly used
named New Stage and Quick Start, then completed 89 requests across 39 names:
38 scoped passes, one partial and 60 uncalled tools. It advanced 480 steps in four
seconds, observed a sample resting at 1.04 m with zero velocity, moved/returned
Agilebot and UR joints within 0.001 rad, captured fresh sensor pixels and saved a
new composed output. All 44 source/cache files and 261 frozen files remained
unchanged. The process had already hosted the root's replay controls; it was not a
pristine process at agent-task start. The new saved output's fresh-process reopening
is pending. The video decoded 232 frames; four were reviewed. Task-native counts
were eight warnings / zero errors, with a USD diagnostic-cap warning retained.

Use the successful task's `correction-v2` ledger and addendum in the
[agent supplement](RELEASE-ASSETS.md): the original derived ledger copied the old
PID/port and overstated some observation scopes. Original calls and manifests remain
unchanged. At-target DOFs came from step call 50; the agent did not read the original
fixture's runtime joints. These corrections do not change measured task outcomes.

The replay investigation independently exposed a legacy checkpoint export defect.
Root-only export omitted a session-defined PhysicsScene type, leaving a typeless
`/physicsScene` over with CPU/120-Hz opinions. Reopening produced an additional native
GPU/60-Hz `/PhysicsScene`. No causal connection to the stall is established.
Source `33982a7` preserves the root/reference checkpoint plus the composed, validated
local PhysicsScene, retaining cache-aware asset paths and refusing unsupported
contributions/runtime fields. It excludes other session edits, uses atomic no-clobber
publication, and adds pre/post-reopen physics checks to the legacy verifier. It is
not a full session or self-contained asset snapshot.

A second source fix, `9c3ec98`, prevents discovery from mistaking a one-line
`tool-failures-<pid>.log` for a bridge advertisement. Diagnostic logs remain intact.
Both fixes were integrated as `6bc45c2`. Fresh 6.0.1 windowed qualification verified
native and host discovery after an intentional tool failure, full legacy motion,
and a ten-call reopen check: exactly one CPU/MBP PhysicsScene at 120 Hz before and
after Play; 12 steps measured 0.1 seconds. Export preserved all 15 live-layer and
14 recorded disk-file hashes and the edit target. An existing destination refused
unchanged. An earlier candidate run used `20f2953` plus exact helper bytes and is
identified separately, not relabeled as this combined run.

Final narrow-run Console counts were 23 warnings / 23 errors; native-file counts
were 20 warning / four error markers. Errors came from the deliberate discovery
failure and an initial harness attempt missing its temporary destination directory.
The corrected setup passed. Native legacy intervals added zero errors; warnings
include schema-path, triangle-size, initialization and closed-client diagnostics.
No general harmlessness claim is made. Temporary Console captures were hashed and
deleted; all three owned 6.0.1 processes exited. The original stall and remaining
release/mode/backend gates stay open.

[Compact facts and 26 original-record hashes](RECENT-601W-20261102.json) distinguish
these sources. Full sanitized records, reproduction scripts, corrected ledgers and
reviewed agent media are in the [draft release supplements](RELEASE-ASSETS.md).

Fresh Sim 6.1.0.0 PhysX qualification at exact `6bc45c2` completed all 14 broad
phases in headless and windowed modes. The full-surface phases made 137 calls / 55
assertions and 141 / 54 respectively, including all 23 UR5e tools and seven bridge
built-ins; the complete phases also exercised all 39 generic tools, 27 reopened
camera captures, 217 stress captures, simulated ROS motion and asset safeguards.
Each mode separately passed the existing-PhysicsScene/legacy verifier, 24 calls /
12 light/clone/URDF readback checks and 25 calls / seven articulated URDF checks.
Each ROS save passed 21 assertions, preserving two live layers and 18 headless or
21 windowed source-file hashes. All owned processes exited normally.

The headless native review retained 6,060 simulation-time warnings; no application
window or Console claim applies. The windowed pre-agent Console showed 270 warnings /
63 errors, while its native prefix contained 24 error markers from negative tests,
267 warnings and 62 simulation-time warnings. No cycle, nested-task, GetCamera,
Force-callback or edit-target-change matches occurred in those reviewed prefixes.
The headless/windowed videos decoded 170/191 frames; two frames from each were viewed.
Their diagnostic zero-pose view crops the upper arm. A separate window capture showed
the bent robot and attached RG2. These visual limits and warning counts remain distinct
from the independent task and final shutdown log.

An independent named-tool task in the 6.1 windowed process made 90 requests across
40 names: 38 scoped passes, two partial observations and 59 uncalled tools. All 17
measured assertions passed. Its 480 steps advanced simulation time by four seconds;
the sample settled at 1.05 m with zero velocity, and Agilebot/UR joints reached and
returned within 0.001 rad. Wrist/gripper relative translation drift was 5.72e-8 m.
Three fresh images showed 144.94 pixels of sample descent. Its actual-window video
decoded 210 frames; the UR wrist was cropped, so that video is not UR-motion proof.
Four selected PNGs were independently viewed by the root reviewer. All 44 audited
source/cache files and 262 frozen files remained unchanged; the recorded launcher
instrumentation is the sole file differing from the commit. The task interval added
six native warnings and zero errors. No blank-stage check between New Stage and
Quick Start or isolated lighting-contribution test was claimed.

A fresh headless 6.1 process using exact `d725f84` reopened that agent's composed save.
Sixteen named MCP calls passed 12 checks: saved pose/camera composition, two runtime
articulations, supported sample with mass, 12 steps advancing 0.1 simulation seconds,
two fresh images, resource cleanup and unchanged saved-source bytes. The old camera
prim correctly returned `NOT_OWNED`; a newly created live camera supplied the images.
This is composition and runtime initialization, not solver/controller checkpoint
restoration. Both images were viewed; the final native log had 18 warnings and zero
errors, and the owned process exited normally.

[The 6.1 compact supplement](RECENT-61-20261102.json) identifies 26 original records
and distinguishes these code/process scopes. Its three redacted bundles and sampled
application-window video are in the draft release. The original `20f2953` 6.0.1
native stall remains unresolved; these passes do not establish recovery or complete
the remaining release/mode/backend matrix.

## 6.2: existing stack, isolated locked stack, and actual use

The next PhysX broad runs used exact `d725f84` headless and `2fdf404` windowed.
The initial headless fullsurface attempt refused to start the simulated ROS driver
because its guard detected another driver. A possible concurrent CPU-test mock is
unproven: no matching PID was retained. A fresh evidence tag on the same owned Kit
PID passed 137 calls / 55 assertions. The windowed run passed 141 / 54. Both also
passed the generic/sensor phases, legacy verifier, independent light/clone readbacks,
24-call authored-physics and 25-call articulated URDF checks, and 21 save checks.
The existing 6.2 environment had NumPy 2.5.4 despite its package requirement for
2.3.1; these results retain that actual stack and are not dependency-clean claims.

An independent `2fdf404` task was initially held after 28 requests when lights and
framing changed outside its calls. The user confirmed those were their own edits
and authorized continuation. The resumed task made 91 requests and passed 15
measured checks: 720 steps advanced six simulation seconds, the sample settled at
1.03999996 m with zero velocity, both robots reached and returned within 0.001 rad,
and the UR gripper followed its wrist with 1.11e-7 m relative drift. Its 99-name
ledger records 38 scoped passes, two partial results and 59 uncalled names. The
full-robot recording decoded 429 frames / 42.9 seconds; three frames were visually
sampled. All 44 recorded source/cache files and 262 frozen files remained unchanged.
The launcher contains the separately recorded diagnostic/startup instrumentation.
The task interval added two warnings and no errors; the actual Console's cumulative
804 warnings / 63 errors includes prior deliberate negative tests. This is actual
use, not all-tool or all-version certification.

A separate environment was then created from the exact project lock with
`uv sync --locked --python /usr/bin/python3.12 --link-mode copy`. The lock inputs
were unchanged between `2fdf404` and `c2ceb7f`; 186 packages resolved, NumPy was
2.3.1, and `uv pip check` passed. Existing user environments were not changed.
This used existing package caches and did not run the full installer, change
client configuration, or establish a clean-machine or clean Lab-video recipe.

Exact `c2ceb7f1b8130f414872cb4475ffdd1c2d38b96a` passed the broad 14-phase suite
in both modes on this isolated environment: 137 calls / 55 fullsurface assertions
headless, 141 / 54 windowed, plus the legacy/effect/URDF/save checks above. Each
mode exercised all 39 generic tools, 23 UR5e tools and seven bridge built-ins.
The windowed isolated-light fixture also passed 16 calls / six checks: changing
a DomeLight from red to blue reversed the neutral cube's measured RGB channels;
zero intensity produced a black measured region. This is a rendered effect,
not photometric calibration or qualification of every light type.

Fresh headless `c2ceb7f` reopening of the earlier agent's composed output passed
16 calls / 12 checks, including supported body state, two articulations, fresh
images and 12 steps advancing 0.1 simulation seconds. Saved source bytes were
unchanged. The old camera was not a live owned sensor; a new camera supplied
the images. First startup took about 222 seconds while native shaders compiled;
the later warm launch took about 15 seconds. A separate launch with invalid ROS
domain 232 was refused before Kit started; its retry used valid domain 171.

Warnings remain. Final headless native counts were 24 deliberate-test error
markers, 14,330 warning markers and 13,989 simulation-time messages. Final windowed
counts, including the later independent task, were 24 / 83,214 / 82,297. Neither
clean run logged an illegal-cycle, nested-task, GetCamera, Force-callback or
edit-target-change marker in the recorded file. These are observed counts, not
a causal NumPy comparison or a claim that warnings are harmless. Windowed Console
samples and native-file counts differ. The broad motion videos decoded 170 and
193 frames respectively; two frames of each were reviewed and their diagnostic
upright view crops the upper arm/wrist. A large lighting-test cube remained in
the later save fixture and obscured its view; the independent task explicitly
reset that disposable scene. No robot geometry failure is inferred from that view.

[The compact 6.2 supplement](RECENT-62-20261102.json) identifies 51 original
records, with separate process identities, retries, image hashes and shutdown
records. Full transcripts and reproduction scripts are attached to the draft
release. The original 6.0.1 native-step stall, remaining older-build checks and
broader Newton matrix are still open.

The subsequent independent task used the same frozen `c2ceb7f` windowed process
on the isolated locked environment. It made 92 requests (91 named calls and one
catalog listing), using 40 names. All 17 measured checks passed; its 99-name ledger
records 38 scoped passes, two partial observations and 59 uncalled names. Over
720 steps, simulation time advanced six seconds. The sample settled at
0.829999983 m with zero velocity; Agilebot and UR5e reached and returned within
0.001 rad. The RG2 followed the wrist by about 0.116 m, with measured relative
translation drift of 1.005e-7 m. Three fresh camera images showed sample descent.

All 40 audited source/cache files and 262 frozen files stayed unchanged; 261 of
those frozen files match the commit, with the documented startup/log/faulthandler
instrumentation in the launcher. The composed save succeeded without switching
the stage. Fresh reopening of this newer agent output is still pending; the
earlier 2fdf output's successful reopening is a separate result.

The actual-window video decoded all 438 frames / 43.8 seconds. Three frames were
visually sampled, showing the complete UR arm and RG2 at baseline, target and
return. Root review separately inspected the recorded target/return/Content
screenshots and one video sample. The task's bounded native-log interval contains
eight warnings and zero errors or illegal-cycle markers. Its Console displayed
cumulative 83,217 warnings / 63 errors from the whole process, including earlier
tests. These scopes are not interchangeable. Light contribution and independent
focus readback remain partial in this agent's ledger; the parent's isolated light
test is separate. The client and recording closed, and the owned Kit process
exited normally afterward.

[The independent-task addendum](RECENT-62-AGENT-20261102.json) identifies the source
records, measured tolerances, media/privacy review and draft-release artifacts.
This closes that actual-use task's stated checks, not the remaining all-build,
all-backend or clean-installer gates.

## Current-runtime 6.0.0.0 qualification

Exact `c2ceb7f` passed the 14-phase PhysX suites in both modes on the prepared
6.0.0.0 interpreter: 137 calls / 55 fullsurface assertions headless, 141 / 54
windowed. Each also passed legacy motion, 24-call / 12-check authored effect
readbacks, 25-call / seven-check articulated URDF behavior, and 21 save checks.
Two live layers and 18 recorded source files remained unchanged in each save.
Both owned processes exited normally after their respective work.

The headless native log contains 38 error markers: 24 deliberate-test markers
plus 14 traceback lines from the native property-window selection callback
`save_scroll_pos` accessing `None.scroll_y_max`. Later measured effects passed;
this does not establish that callback as harmless or fixed. There were 243 warning
markers. The final windowed file, including independent use, contains 24 error
markers and 250 warnings. Neither file contains the selected illegal-cycle,
simulation-time, nested-task, GetCamera, Force-callback or edit-target-change
signatures. Actual windowed Console samples differ from these file counts and
show the deliberate missing/corrupt-asset and invalid-quaternion cases.

The independent windowed task made 87 MCP requests (86 tool calls plus one
catalog listing). All 19 predeclared effect checks passed. Two 90-step advances
each measured 1.500000078 simulation seconds at the scene's 60 Hz rate. Both
robots reached modest targets and returned; the sample rested at 0.139999956 m
with zero velocity on its known support. Wrist/gripper displacement and return
were observed separately. All 230 audited preexisting files stayed unchanged;
the recorded launcher instrumentation remains the exception to commit equality.
Its ledger retains 41 scoped-pass tools, one save partial and 57 uncalled names.
The task's native-log interval added nine warnings and no errors. A lowercase
robot-path lookup returned `NOT_FOUND`; discovery supplied `/World/UR5e`.

Both raw actual-window recordings decoded fully (438 and 308 frames); seven
frames were visually sampled. The raw clips contain private Console paths and
are excluded from distribution. The bundle includes three reviewed original
PNGs and an explicitly labeled 1440-by-610 crop of the 308-frame motion clip.
This shows before/after step results, not a rendering of every physics step.
The broad-suite videos have separate upright diagnostic framing that crops the
upper arm. Appearance is not substituted for the numerical motion checks.

A separate fresh headless process reopened the agent's exact composed save.
Sixteen named MCP calls passed 12 checks: authored pose/camera composition,
supported body with mass, both six-joint runtime articulations, new camera
images, cleanup and unchanged saved-source bytes. Twelve steps advanced
0.2 simulation seconds, preserving 60 Hz instead of assuming 120 Hz. The prior
camera definition correctly lacked live ownership; a new camera acquired pixels.
The new process exited normally with 20 warnings and no errors. This is scene
composition and runtime initialization, not a solver/controller checkpoint.

[The 29-record supplement](RECENT-6000-20261102.json) and its three draft-release
bundles preserve all source identities, failures, measurements and review limits.
The original 6.0.1 native stall, remaining current-build checks and broader Newton
qualification remain open.

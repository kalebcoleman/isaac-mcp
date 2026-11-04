# Compatibility

The full installer is pinned to **Isaac Sim 6.2.0.0 and Python 3.12**. It does not automatically select a
compatible environment for other 6.x releases. The separate MCP host package requires Python 3.10 or newer;
its interpreter can be different from the simulator's.

## Release backend scope

**PhysX is the release target. Newton remains experimental.** Completing Newton support
across builds, or repairing its measured failures, is outside this release scope. The
historical results below remain available with their original source, build, mode and
solver identities; this scope decision does not turn failures into passes.

- **Sim 6.2.0.0 Newton:** the [candidate9/10 records](#6200-newton-twin-candidates-2026-11-02)
  demonstrate specific twin motion, gripper/output, saved-twin reopen and known-load wrench
  workflows with **MuJoCo Warp on CPU**. Broad candidate9 runs and later narrow candidate10
  checks have different source identities. They do not establish all 6.2 Newton workflows,
  current-head qualification, native MuJoCo CPU support or generic/Lab equivalence.
- **Earlier Newton builds:** full-twin mounting with Newton 1.0 is unsupported.
  [6.0.0.1](#6001-newton-failures-and-separate-host-envelope-checks-2026-11-02)
  has measured native crashes and a separate room-load stall;
  [6.0.1.0](#6010-newton-state-and-rendering-failures-2026-11-02)
  has nonfinite physics, black output, scale loss and a later native failure;
  [6.1.0.0](#6100-newton-contact-failures-and-unshipped-controls-2026-11-02)
  has nonfinite assembled physics and failed repeated-cycle contact controls. Narrow generic
  passes do not qualify those twin workflows. No reliable collider workaround was shipped.

The original **`20f2953` / 6.0.1.0 windowed PhysX stepping stall remains unexplained**.
Its [bounded assessment and recovery guidance](evidence/project-scene/PHYSX-STALL-DISPOSITION.md)
are complete: two current-runtime replays did not reproduce it, with no product fix or
cause established. PhysX evidence review, fresh-Kit reuse of the new camera
fixtures, clean-machine/full-installer coverage and Lab setup limitations remain separate.
This is not a readiness or merge declaration. In the dated sections below, earlier references
to remaining Newton qualification describe the investigation at that time, not a current
release requirement. Optional Lab's Newton/MJWarp evidence also remains a separate scope.

For coverage by tool, start with the [current tool/source/backend reconciliation](evidence/project-scene/CURRENT-TOOL-RECONCILIATION.md).
It links all 99 names to their historical calls, bounded observations and remaining gaps;
it does not certify all tools on the current head or every backend. The later
[current PhysX supplement](evidence/project-scene/PHYSX-CURRENT-POSITIVE.md) closes its
then-open positive-path gap for `sim__body__state`, `sim__joints__state` and
`sim__simulation__step` on **all five builds in headless and windowed modes**, at exact
`0163b3f07e6b9d3f51006f8845156f664429e580`: **445 core calls, including 100 expected errors,
and 230 scoped assertions**. This is a targeted regression, not a new full-head 99-tool pass.
Earlier PhysX runs and `ba1d4b2` Newton error-reporting checks keep their original identities.

The supplement records finite falling-body/joint effects, measured step clocks, refusal and
Stop/Play checks; assertions also include identity and preservation, not 230 independent
motion experiments. Prepared environments, diagnostic launchers, retained harness failures,
source-file versus whole-root preservation and separate Console/native warning counts remain
explicit in that record. The original `20f2953` PhysX stall is **not reproduced or resolved**
by this smaller fixture. Fresh-Kit reuse of the new camera fixtures and setup/Lab limits
remain separate; Newton failures remain experimental limitations outside release completion.


The separate bounded assessment used exact `0163b3f` in two **6.0.1.0 windowed PhysX**
processes. Successful continuations had 48 and 54 calls; retained five-call `NO_STAGE` setup
and 25-call history/`INVALID_FRAME` records bring the total to **132 calls / 30 scoped
assertions**. Both measured 12 steps advancing 0.2 simulation seconds, then 108 advancing
1.8 seconds at actual **60 Hz**. The unchanged old checkpoint's authored 120 Hz opinion was
an untyped PhysicsScene over; no rate override was applied. All 44 original asset/cache
hashes matched, but 13 host/runtime/catalog files differ from `20f`, and the second attempt's
prior-history sequence only approximates the original. Neither result explains the stall.

Both processes exited normally within 19.1 minutes of the 35-minute budget. Console counts
were 18/0 and 28/0 warnings/errors, separate from 15/25 final native warning lines; tracked
major native error categories were zero. The [record](evidence/project-scene/PHYSX-STALL-DISPOSITION.md)
retains exact ages, wall versus simulation times, cropped/sample-only visuals and preparation
limits. Its recovery procedure discards an owned unresponsive session and verifies a fresh
one; it does not recover unknown in-flight state. No failure was injected to validate recovery.

The table records what this repository has actually exercised, not a guarantee for every machine or feature.
The 6.0 and 6.1 probes used separate, locally resolved environments rather than this project's locked 6.2
environment; a clean-install recipe for those builds has not been qualified by this repository. For selection,
prerequisites, and what is optional, see [Linux setup by build](LINUX-SETUP.md#choose-a-simulator-build).
See the [test details](evidence/compatibility-2026-10-31.md) and [verification guide](VERIFICATION.md) for the
environment, results and omissions. The table preserves the historical smoke scope; newer
[headless](#6001-headless-follow-up-2026-11-01) and
[windowed](#6001-windowed-follow-up-2026-11-02) 6.0.0.1 observations and the
[6.0.1.0 headless](#6010-headless-follow-up-2026-11-02) and
[windowed follow-ups](#6010-windowed-follow-up-2026-11-02) are recorded separately below.
The [6.1.0.0 PhysX follow-up](#6100-physx-follow-up-2026-11-02) records later mode-specific checks.
The [6.2.0.0 follow-up](#6200-physx-follow-up-2026-11-02) separates existing and isolated locked environments.
The [6.0.0.0 current-runtime follow-up](#6000-physx-follow-up-2026-11-02) records broad checks, an independent task and a separate 60 Hz reopen.
The [current 6.0.0.1 PhysX and later save-reopen results](#6001-current-runtime-physx-follow-up-2026-11-02) retain the earlier runs above as historical evidence.
Final PhysX release review across the requested builds and modes remains pending.
The [6.2 Newton twin candidate follow-up](#6200-newton-twin-candidates-2026-11-02) separates
broad candidate runs from later narrow reopen/force checks and the historical integrated PhysX header defect.
The [PhysX wrench follow-up](#6200-physx-wrench-correction-candidate-2026-11-02) records
that defect and candidate12's scoped correction. The later [integrated affected-path checks](#integrated-affected-physx-paths-across-five-builds-2026-11-02)
record five-build PhysX results without relabelling the earlier full-tool evidence.
The [6.0.0.1 Newton failure follow-up](#6001-newton-failures-and-separate-host-envelope-checks-2026-11-02)
records native failures in both modes, unqualified private controls and separately tested host error reporting.
The [6.0.1.0 Newton state/rendering follow-up](#6010-newton-state-and-rendering-failures-2026-11-02)
separates later solver/rendering failures from fresh-process partial-step error reporting.
The [6.1.0.0 Newton contact follow-up](#6100-newton-contact-failures-and-unshipped-controls-2026-11-02)
records a failed assembled workflow and unsuccessful private collider controls.
The [focused camera clipping checks](#new-camera-clipping-across-five-builds-2026-11-02)
qualify rendering and units on a later source, without advancing physics or backend qualification.

The newest short universal checks on each build are in the [PR 70 retest](evidence/pr70-build-retest-2026-11-04.md), including what remains unexplained on 6.0.0.0 and 6.0.0.1.

| Isaac Sim Python package | Project status | Evidence and limits |
|---|---|---|
| `6.2.0.0` | Pinned baseline; selected workflows tested | The 6.2 twin, bridge, desktop controls and managed sessions were exercised on Ubuntu 24.04.5 / RTX 5080. Historical Isaac Lab tests used Lab 3.0.0 with this Sim version. See the [desktop evidence](evidence/desktop-control-2026-10-31.md) and [Lab guide](TOOLS-ISAACLAB.md#verified-vs-version-and-platform-limitations). |
| `6.1.0.0` | Partial test; further validation needed | Headless and windowed 20-check smoke runs completed, including a decodable recording. The panel was docked and simulated object and camera transforms were received. Kit logged asynchronous task errors and a viewport exception on reset. An earlier Quick Start during startup did not complete. Lab and physical hardware were not tested on 6.1. |
| `6.0.1.0` | Partial test; further validation needed | The 20-check smoke completed in headless and windowed sessions, including decodable recordings. The optional panel was docked, and simulated object and camera transform messages were received. Kit logged asynchronous task errors; Lab and physical hardware were not tested. |
| `6.0.0.1` | Partial test; further validation needed | Headless and windowed 20-check smoke runs completed, including a decodable recording. A fresh window had the panel docked, and simulated object and camera transforms were received. Kit logged asynchronous task errors; a panel was undocked after one extension reload. Lab and physical hardware were not tested. |
| `6.0.0.0` | Partial test; further validation needed | The initial object-pose graph failed because this release uses an older ROS publisher. With the compatibility fix, headless and windowed 20-check smoke runs passed, the optional panel was docked, and simulated object and camera transforms were received. Kit logged asynchronous task errors; Lab and physical hardware were not tested. |
| Other 6.x releases | Not tested | A shared major version does not establish that this bridge, the twin or Lab jobs work on a new release. |
| `5.1.0.0` | Experimental; not auto-detected, so name its interpreter with `ISAAC_MCP_PYTHON`. `run.sh` finds its bundled ROS libraries and `sim-tools` has adapters for its older APIs | Tested through the [PR 70 retest](evidence/pr70-build-retest-2026-11-04.md) and on one project scene: most generic tools work, and `verify.sh` passed. Earlier result, before those adapters: The bridge and twin registered, but Quick Start failed with the default 6.2 ROS library path. With a temporary 5.1 ROS library path, Quick Start and stop returned success and the panel was checked, but Kit logged asynchronous task errors. The complete workflow was not verified. |
| Earlier releases | Not tested | No compatibility claim. |

`scripts/smoke_tools.py` is a scoped check: it does not exercise a physical robot, a real pose publisher or every
tool configuration. A passing count does not establish that the viewport, ROS, desktop controls and Lab work in
every configuration. The existing 6.2 reset comparison documents a `NoneType.GetCamera` exception and recovery for
its tested sequence; the cause remains open. The 6.0 and 6.1 smoke runs rebuilt the scene after reset but did not
verify rendered frames afterward.

The host lists the 23 catalogued UR5e tools even when Kit is offline, unless a namespace filter hides them. Listing
the tools does not establish simulator compatibility: their execution needs a selected bridge with the twin
extension loaded.

The 6.2 lock now selects NumPy 2.3.1, matching the installed `isaacsim-kernel` 6.2.0.0 wheel. A separate copy of
the simulator environment passed `uv sync --locked`, `uv pip check`, the headless smoke and the two simulated ROS
transform checks with that version. Existing environments created from the earlier lock can still contain NumPy
2.5.4 until they are synced with the updated lock; this test did not change the existing installation.

## Isaac Lab, ROS and platforms

- Historical short training and windowed play checks used Isaac Lab **3.0.0 / Isaac Sim 6.2.0.0** on the machines
  described in the [Lab guide](TOOLS-ISAACLAB.md#verified-vs-version-and-platform-limitations). Those checks do not
  establish long-running training, every task, or Lab behavior on the older Sim probes above.
- Isaac Lab 2.3.2 with Sim 5.1 is **experimental**: job launching follows its per-library script layout. Tiny real Cartpole jobs passed through the tools with all four RL
  libraries on one desktop, but no long training or other tasks ran, and `isaaclab__install_info` reports `tested: false`.
- NVIDIA's published Isaac Lab and Sim pairings are shown below. The release dates are UTC. They describe NVIDIA's
  versions, not additional MCP tests: the 6.0 and 6.1 Sim smoke checks above did **not** test Isaac Lab.

  | Isaac Sim | Isaac Lab release | Published |
  |---|---|---|
  | 6.0.0 | [`v3.0.0-beta2`](https://github.com/isaac-sim/IsaacLab/releases/tag/v3.0.0-beta2) | 2026-06-17 |
  | 6.0.1 | [`v3.0.0-beta2.patch1`](https://github.com/isaac-sim/IsaacLab/releases/tag/v3.0.0-beta2.patch1) | 2026-07-02 |
  | 6.1 | [`v3.0.0-EA`](https://github.com/isaac-sim/IsaacLab/releases/tag/v3.0.0-EA) (early access) | 2026-09-16 |
  | 6.2 | [`v3.0.0`](https://github.com/isaac-sim/IsaacLab/releases/tag/v3.0.0) (general availability) | 2026-10-20 |

  The Lab 3.0 general-availability tag is `v3.0.0`; NVIDIA's
  [cross-product matrix](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/compatibility.html) calls it
  `v3.0.0-GA`. The first 3.0 beta, also for Sim 6.0, was published on
  [2026-03-17](https://github.com/isaac-sim/IsaacLab/releases/tag/v3.0.0-beta). Select matching ROS workspaces and
  custom interfaces as described in the matrix.
- ROS 2 Jazzy mock-driver and simulated sensor checks have been exercised on the 6.2 Ubuntu 24.04 setup. Humble
  has launcher tests but has not been live-tested in that check; a physical UR5e was not used. See
  [Linux setup](LINUX-SETUP.md#optional-ur5e-mock-ros-driver) and [verification](VERIFICATION.md#last-live-check).
- The simulator installer targets Ubuntu 22.04 or 24.04 on x86_64; the primary test machine was Ubuntu 24.04.5,
  RTX 5080, NVIDIA driver 580.178.04. Desktop input was tested on X11; native Wayland and other operating systems
  have not been established as supported for these tools. See [Linux setup](LINUX-SETUP.md) and
  [NVIDIA's 6.2 system requirements](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/requirements.html).

## Checking an installation

For a pip-installed Sim environment, use its own interpreter:

```bash
/absolute/path/to/sim-env/bin/python -c 'import importlib.metadata as m, sys; print(sys.version); print(m.version("isaacsim"))'
```

The bridge's `status` also reports the running app's version and PID. Its app version may omit the fourth Python
package-version segment, so use the package metadata to distinguish exact builds. For a workstation installation,
NVIDIA also documents Help > About and the installation's `VERSION` file.

An existing separate environment can be selected with `UR5E_DT_PYTHON=/absolute/path/to/sim-env/bin/python` for
`./run.sh`, or in the MCP server's environment before using `sim__launch`. Selecting an interpreter is not a version
or dependency check. Keep the locked 6.2 environment intact when experimenting, use a disposable scene and an
unused bridge port and ROS domain, and verify the selected PID before scene-changing calls. See
[host tools](TOOLS-HOST.md#optional-simulator-sessions) for managed-session safeguards and
[verification](VERIFICATION.md#live-ur5e-baseline) for the test's effects and `--sim-version` option.

For a simulated ROS check on the tested Ubuntu/Jazzy setup, select the disposable bridge, run Quick Start, add
objects, call `ur5e_dt__setup_pose_publisher` and `ur5e_dt__setup_camera_tf_graph`, then play the scene. In a separate terminal, from
the repository root, use the same ROS domain as the simulator and source the shared DDS configuration:

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42  # replace with the disposable simulator's domain
source config/ros_dds.env
timeout 30 ros2 topic echo --once /objects_poses_sim tf2_msgs/msg/TFMessage
timeout 30 ros2 topic echo --once /tf_cam tf2_msgs/msg/TFMessage
```

These calls change the scene and exercise simulated publishers only. Use the matching ROS distribution and
workspace for other environments; a delivered transform is not a physical-robot or camera-image test.

## Project asset and generic scene preview (separate feature gates)

The current 55-tool pack has its own [evidence and version matrix](evidence/project-scene/README.md),
separate from the historical UR5e/PR #9 runs above. The same-host trusted-project workflow,
explicit stage targeting, units, authored/runtime distinction, and PhysX step/joint paths were
exercised on 6.2.0.0, then representative paths on 6.0.0.0, 6.0.0.1, 6.0.1.0 and 6.1.0.0.
These are scoped tests, not blanket support for every asset, parameter, sensor or backend.

The newer path-based project USD import and workstation preparation were also tested on
all five packages above, in both windowed and headless sessions, using a layered textured
fixture and an operator-supplied workstation. [Test evidence](evidence/project-usd-workstation/README.md)
records the exact builds, sample coverage and limitations. A separate camera capture issue
required a retry in those tests; the asset results do not establish that all other tools or
future 6.x releases work.

The compatibility follow-up replaces that experimental dependency with a UsdGeom/Replicator
owned-CPU camera adapter and a feature-detected legacy LiDAR scan-buffer path. The two 6.0.0
package builds are distinguished by four-component package metadata and full VERSION/build,
not `get_version()[0]`. Running Kit and loaded extension/library identities are recorded and
checked; a new or conflicting stack does not inherit support from its major version.

Disposable Newton trials on all five installed releases measured gravity, collision rest,
positive/negative joint targets, exact paused step timing, repeated Play/Pause/Stop and pose
exports. Newton <=6.0.1 requires a scoped native playing-flag correction for paused steps;
all tested Newton paths avoid stale captured CUDA graphs and publish poses through Fabric.
Runtime observations use the selected engine, with no PhysX/USD substitution. Newton is
qualified only for the recorded stacks and meter stages, with scoped MuJoCo evidence.
The [per-release matrix](evidence/project-scene/RUNS.md) records all 37 tools on each installed
PhysX release, independent agents, repeated captures, known failures and later retests. Newton trials
are not a claim that every backend/asset/edge case passed. Later
[6.0.0.1 twin failures](#6001-newton-failures-and-separate-host-envelope-checks-2026-11-02)
remain open despite those earlier generic checks.

The follow-up also removes synchronous World initialization/clear calls from the twin's async
workflow. A visible 6.1 reset/rebuild smoke passed 20/20 without the previously reproduced
nested-task or GetCamera errors; the actual application window rendered after rebuilding.
Historical failures above remain part of the record. Further release-specific regressions,
sensor stress and independent client use are required before broader claims.

Bounded stored USDZ packages are implemented for trusted projects, including package-member
dependency checks. A live 6.0.0.0 package import/save test and headless export reopening passed.
This is not an archive sandbox. Isolated untrusted import, authenticated remote upload and
named manifests remain unfinished. No version match or passed asset validator supplies those
missing enforcement/transport guarantees. See [tool setup](TOOLS-SCENE.md).

Owner decision 2026-11-01: PR11 is a first slice. Its measured support does not include isolated
untrusted loading, authenticated remote upload, named presets or full fork parity. Those gates
remain open. New native failures and harness failures are retained in the draft evidence release.
The latest UR5e World adapter initializes asynchronously even when `/physicsScene` already exists;
it restores the temporary terminal-launch flag before awaiting and avoids nested Kit updates.
See the final validation attachment for the 6.2 verify/smoke/motion and existing-scene checks.

The earlier 6.0.0.0 fresh-process saved-scene evidence remains partial: geometry/resting body
state and new sensor creation worked, but camera readout stayed `NOT_READY` after extended
warm-up. Later orchestration-graph export fixes are described in [scene tools](TOOLS-SCENE.md);
the final combined-candidate version/mode matrix must requalify that workflow.

On combined candidate `34b24a262ae32fb6ae9f4f3c47f670d108784d07`, a saved ROS camera scene
reopened without RGB or CameraInfo although TF was received. A private copy with one stale
`viewportHandle` property removed restored those streams in fresh 6.2 headless and windowed tests.
That experiment does not qualify the integrated product fix. The combined branch now includes
PR #11 fix `919683337785f3e95972e3ada057b95c57aca7df`: `sim__stage__save` defaults
`reset_camera_render_handles=false`, refuses affected saves with exact `properties` and
`retry_with`, and permits explicit normalization only after the documented association,
composition, dependency and live-binding checks. Successful exports list
`omitted_runtime_render_properties`; the original and live stage remain untouched. See
[save policy and limitations](TOOLS-SCENE.md). The scoped 6.0.0.1 headless follow-up below
includes save/reopen checks; qualification of the remaining release/mode matrix is **pending**. Preserve the original scene; do not manually
edit it as an installation step. An already-stale reopened handle can lack a valid native binding
and must refuse normalization rather than guess.

## 6.0.0.1 headless follow-up (2026-11-01)

The measured source is **`4499bf70c5f06c37cdf928dd1f3d2c29e5abf621`**, tree
`072f8e689d3b5275c1be134488fa46b8a1c474d1`. This run used package **6.0.0.1**, build
`6.0.0-rc.59+release.41464.5f2772bc.gl`, Kit `110.1.1+production.305458.6312fa25.gl`,
**headless PhysX**, on Ubuntu 24.04.5 / RTX 5080 16 GiB / driver 580.178.04.
It used an existing installation, not a clean-machine setup.

All 14 broad phases returned zero, covering all 39 generic tools, 23 UR5e tools and
seven bridge built-ins. The full-surface portion recorded 137 calls and 55 effect
assertions. Separate checks covered 27 reopened sensor captures, 12 cold-camera
attempts, 23 cache assertions, and 21 save/source-preservation assertions. The
existing-physics-scene and legacy async-verifier regressions both passed with no new
error lines in their respective intervals. These counts describe different scopes;
they must not be added into a single all-feature guarantee.

The earlier `2bbeee4a1eb8ab806be44ca7120cdf599dd923e8` run remains a recorded failure:
18 camera captures and a LiDAR wall measurement succeeded before explicit Stop stayed
in native `STOPPING` for minutes and named Play refused with `TIMELINE_BUSY`.
Private diagnostic native completion restored Play/Pause with unchanged live layer
hashes; that intervention was not a product pass. The fresh `4499bf7` run exercised
the integrated explicit-Stop fix and passed the formerly failing sequence. Play was
not automatically retried.

The native review retained 24 error lines attributed to intentional missing/corrupt
asset and zero-quaternion refusal tests, 17 illegal-cycle occurrences and 7,748
simulation-time warning occurrences. No nested-task reentry, `GetCamera` or Force
callback occurrences were recorded in that review. These observations do not establish
that all native warnings are harmless or that their upstream causes are fixed.

The video probe decoded 172 frames; visual review inspected only frames 10 and 160.
The bench, room and arm were visible, with upper-arm/wrist cropping at the diagnostic
zero pose. This was simulated ROS with Jazzy mock hardware, not a physical robot test.

Provenance: the inspected compact handoff `public-facts-final6001h-4499.json`, recorded
at `2026-11-01T23:45:17.301641+00:00`, has SHA-256
`b061066f47fb3122c681fb31dfa527930f7dc877887147f0e5a1e10bcc7698da`.
Its source records include the run-progress, regression, native-review, full-surface
and camera/ROS-save summaries. Redacted records and configurable harnesses are now
listed in the committed [draft release index](evidence/project-scene/RELEASE-ASSETS.md).
The release remains unpublished and source assets are excluded; these attachments
are not a clean-install reproduction bundle. Windowed observations are recorded below;
the complete five-build/mode matrix remains pending. Later documentation or bridge-lifecycle commits do not change this
run's source identity or qualify their new runtime behavior.

## 6.0.0.1 windowed follow-up (2026-11-02)

This separate **windowed PhysX** run used exact source
`4499bf70c5f06c37cdf928dd1f3d2c29e5abf621`, the same package/build/Kit and host stack
as the headless record above. All 14 broad phases returned zero, covering 39 generic
tools, 23 UR5e tools and seven bridge built-ins. The full-surface portion recorded
141 calls and 54 effect assertions; the reopen, cold-camera and cache scopes recorded
27 captures, 12 attempts and 23 assertions respectively. The existing-physics and
legacy async-verifier intervals separately returned zero with no recorded error,
reentry, cycle or simulation-time occurrences. These passes do not supersede the
viewport source-preservation defect found afterward.

Independent actual use made 93 MCP requests across 35 registered names: 34 tool-ledger
passes, one partial result and 64 catalog names uncalled. It recorded 22 passing effect
checks and one framing limitation. Measured robot motion, sample fall/rest and gripper
alignment passed. The first recording decoded 70 frames but retained an occluded view;
a later narrower framing produced a useful UR/gripper recording with 98 decoded frames.
Only its first, middle and last frames were visually inspected. The raised/rotated
Agilebot success does not explain an earlier 6.0.0.0 target miss.

The pre-agent native review retained 24 intentional-negative error lines, 22 cycle
occurrences and 137 simulation-time warnings; no reentry, `GetCamera` or Force callback
occurrences were recorded. The real-window Console showed 359 warnings and 63 errors
before independent use, and 374 warnings and 63 errors afterward. UI event totals are
not native-log line counts. The separate agent interval had zero errors and 15 warnings;
these scopes must not be combined into a claim of a warning-free full run.

A later anonymous-camera fixture confirmed that `sim__viewport__frame` returned success
while changing the in-memory root camera translation from `(6, 6, 6)` to approximately
`(2, 0, 6.677923)`; the session layer was unchanged. No disk overwrite was demonstrated,
and this defect is not an established cause of the earlier occlusion/timing limitation.
The independent audit found 43 on-disk assets and 259 frozen source files unchanged;
that does not establish preservation of the in-memory root layer. Its separate parent
review qualifies the original geometry-under-station wording accordingly.

Source fix `97325af8d6eaf85e5ef3c5088ff0805dd19ae65f`, integrated in `37a73583`, changes
framing to supported session-layer camera edits, reports native failures and
`render_complete=false`, and documents partial effects and refusals in
[scene tools](TOOLS-SCENE.md). The subsequent partial attempt and corrected live retest
are recorded below; they do not retroactively change this `4499bf7` run or establish
all-version readiness.

Provenance: inspected `public-facts-final6001w-4499.json`, timestamp
`2026-11-02T00:04:54.401402+00:00`, SHA-256
`34e507ee522bee118e568c612483657ac2f3e588323a150e3db65f61cafc834b`;
all nine referenced source-record hashes were verified. The compact handoff and parent
review preserve the original observations. The committed
[draft release index](evidence/project-scene/RELEASE-ASSETS.md) lists the redacted records
and configurable harnesses; the release remains unpublished and source assets are excluded.
This is an existing-installation, simulated ROS check, not physical hardware or
clean-install validation; remaining builds/modes still need their own qualification. The viewport follow-up below is limited to 6.0.0.1 windowed.

### Viewport preservation and typed-error retest

The initial fresh `37a73583` attempt passed six preservation/transform checks, then a
deliberately injected native false return exposed a duplicate-`effects` `TypeError` in
the error serializer. That partial attempt remains a failure record. Source fix
`5717217`, integrated into **`eea18c823f5e462763297951039771f80467e450`**, corrected the
wrapper; it was tested in a new **6.0.0.1 windowed PhysX** process.

The fresh retest recorded **46 real MCP calls and 12 passing checks**. Root and referenced
camera layers were preserved, the intended camera effect was authored in the session
layer, and the prior nondefault edit target was restored. Deliberately injected native
false returns and exceptions produced typed `VIEWPORT_FRAME_FAILED` responses with
partial-effect descriptions. Empty geometry, unsafe session-sublayer camera opinions,
look-through targets and read-only sessions were refused in their tested fixtures.
These injected failures test error handling; they were not spontaneous native failures.

The runtime owner's separate visual review at `2026-11-02T00:11:02.274294+00:00`
reported a centered selected cube in the actual application-window capture, SHA-256
`bef55a3134b03c7879e32b299beedf048745a5296c153541ba22864c73d6113d`.
This is visual framing evidence, not a physics check or an assertion that
`render_complete=false` proves a rendered view. The separate Console review recorded
16 warnings and zero errors; warnings included native USD/layer, look-through fixture
and closed-client socket messages. The scoped native review recorded zero error,
cycle, simulation-time, reentry, `GetCamera` or Force callback occurrences. No blanket
harmlessness or all-socket-races claim follows from those observations.

The inspected records are `summary.json`, `visual-review.json` and `console-review.json`
from the private `frame6001w_eea-viewport` run. The original summary's pending visual
status remains unchanged; the later timestamped review supplies the visual observation.
The redacted viewport supplement is indexed in the
[draft release assets](evidence/project-scene/RELEASE-ASSETS.md); public release and
clean-install reproduction remain pending. This qualifies the tested viewport cases on
this exact build/mode/source only, not other releases, a new full-surface run, clean
installation or completion of the remaining matrix.

## 6.0.1.0 headless follow-up (2026-11-02)

At exact **`eea18c823f5e462763297951039771f80467e450`**, the 6.0.1.0 headless
PhysX run completed all 14 broad phases, including 137 full-surface calls / 55
assertions, 27 reopened sensor captures, cold/cache checks and separate legacy-motion
and existing-physics-scene regressions. The initial reopen used a scene saved on
6.0.0.1; it was a cross-build check. A later save passed 21 assertions with matching
source/live-layer hashes, but its enclosing harness unexpectedly exited 143 afterward.
A fresh client observed a ready process and successfully stopped the timeline. The
harness anomaly remains unresolved; it does not establish a native crash.

The runtime owner's native review retained 24 expected negative-test error lines and
18,020 simulation-time warnings, with zero cycle, reentry, `GetCamera` or Force-callback
matches. All 173 video frames decoded; only frames 10 and 160 were visually sampled,
with upper-arm/wrist cropping at the diagnostic zero pose. There was no desktop Console
review in this headless run. These observations do not establish warning harmlessness.

Subsequent independent USD/physics readback exposed a real URDF defect despite a
successful import response: the generated interface selected nonexistent `Physics=physx`
when its defined variants were `none` and `physics`, leaving no rigid body or mass.
Source fix `beb4cfa` selects and validates shared physics on a private generated derivative;
conversion preserves stage identity and source layers, while successful import adds the
requested session subtree. The [scene-tool contract](TOOLS-SCENE.md) distinguishes
authored physics from runtime readiness.

The fresh fix qualification used exact **`230f4b3f58e294ee3f0474b6d61a857edd965022`**,
package **6.0.1.0**, build `6.0.1-rc.7+release.42383.32955d8d.gl`, Kit
`110.1.2+production.326809.f9bf0dda.gl`, **headless PhysX** in an existing installation.
It passed two separate scopes:

- **24 MCP calls / 12 checks:** independent saved-USD reads verified the single-link
  body's 1 kg mass, collision, 0.2 × 0.3 × 0.4 geometry and requested placement.
  Light creation/modification and simple-leaf clone edits preserved their sources.
  These light checks measured authored values, not rendered illumination.
- **25 MCP calls / 7 checks:** a two-link articulated fixture measured 0.199782684 rad
  for a 0.2 rad target, 0.5 kg runtime arm mass, changed body orientation and two
  120-step intervals of one second each, with joint-limit refusals. The initial harness
  failed parsing an expected error prefix; that failed attempt remains separate from
  the corrected retry. Scoped native error/cycle/time/reentry/`GetCamera`/Force-callback
  counts were zero; this is not a headless visual qualification.

[Compact source records and hashes](evidence/project-scene/RECENT-CHECKS-20261102.json)
and the [validation narrative](evidence/project-scene/FINAL-VALIDATION.md) preserve
these separate observations. The redacted supplement is listed in the
[draft release index](evidence/project-scene/RELEASE-ASSETS.md), with source assets excluded.
The fix is qualified only for these 6.0.1.0 headless PhysX fixtures; windowed, other-build,
Newton and clean-install claims require their own evidence. Historical invocation records
for all 99 names do not establish 99-tool all-version verification or final readiness.


## 6.0.1.0 windowed follow-up (2026-11-02)

**Actual-use qualification remains incomplete because the original stepping stall is
unresolved.** The following separate runs used package **6.0.1.0**, build
`6.0.1-rc.7+release.42383.32955d8d.gl`, Kit `110.1.2+production.326809.f9bf0dda.gl`,
windowed PhysX on Ubuntu 24.04.5 / RTX 5080 / driver 580.178.04, with existing installations.

At exact **`20f2953bb28361f29b0aa720513b5d8bc2d415ed`**, all 14 broad phases passed,
including 141 full-surface calls / 54 assertions across the generic/UR5e/built-in scopes.
Separate authored-effect checks passed 24 calls / 12 checks, and articulated URDF checks
passed 25 calls / 7 checks. Legacy and existing-physics regressions returned zero with
zero recorded error/reentry/cycle/time occurrences in their respective intervals.
The earlier broad native review retained 24 intentional-negative error lines and 61
simulation-time warnings; the Console showed 258 warnings and 63 errors. These are
different counting scopes. All 186 video frames decoded; the runtime owner viewed only
frames 10 and 170, with upper-arm/wrist cropping at the diagnostic zero pose.

The subsequent independent task completed 52 requests, then request 53 for 108 simulation
steps produced no observed success or timeout response. Its ledger records 27 scoped
passes, three partial results and 69 uncalled names. The 87 decoded recording frames
precede the stall; recording termination, final save and final Console review were not
established. A captured stack placed execution inside native `simulate()` through
`SimulationManager.step`; debugger attachment was denied, so there is no native backtrace
or established cause. Client closure did not establish cancellation. Normal stop failed
to exit within 25 seconds; identity-checked forced termination removed the process.
That was cleanup, not recovery, and the stall is not a confirmed crash.

A different process at the same `20f2953` source completed eight repeat cycles / 76 calls
with recording, capture and 12+108-step sequences. It then hosted a new independent task
after explicit `new_stage` and named Quick Start; it was not pristine at task start.
The corrected v2 ledger records **89 requests, 39 names, 38 scoped passes, one partial
result and 60 uncalled names**. Fourteen checks covered sample fall/rest, Agilebot and
UR5e target/return measurements, capture and save; 480 measured steps advanced four
seconds at 120 Hz. The saved scene was not reopened in a fresh process. All 232 window-video
frames decoded, but only four were visually sampled; UR motion was measured and separately
photographed, not shown in that recording. The partial discovery result admitted a diagnostic
log as a bridge. The original ledger's copied process/port and overbroad observation wording
are corrected by a separate v2/addendum; raw records remain unchanged. Neither this task
nor the successful repeats explains or resolves the earlier stall.

A separate legacy-checkpoint defect lost a session-authored PhysicsScene type in the
root-only export. Reopening created a default GPU/60 Hz scene instead of preserving
CPU/MBP/120 Hz. This explains the configuration/time-step loss, **not the stall**. The
[legacy export contract](TOOLS-SCENE.md) now preserves a root reference copy plus an
eligible composed local PhysicsScene, excludes other session content, refuses unsafe
runtime state and publishes without overwriting. It is not full-session preservation or
native solver-state serialization; generic stage save retains its own contract.

An initial candidate check used `20f2953` plus helper SHA-256
`2f55200a6418434144991c1d6fc9254981b31d59c24538c44534d8e66021f0a7`.
A separate fresh process at exact **`6bc45c2977270de748b58c630c21cd03f0d74fb6`** then
qualified the integrated checkpoint and discovery fixes:

- Four discovery calls/checks found only the real endpoint through the native built-in
  and host CLI; the intentional failure log remained byte-identical.
- Ten checkpoint-reopen calls found one typed CPU/MBP/120 Hz scene before and after Play;
  12 named simulation steps advanced 0.1 seconds. Separate raw helper export preserved
  15 live-layer and 14 disk-file hashes plus the edit target, and refused an existing
  destination unchanged. Raw helper export is distinct from named-tool coverage.
- Legacy and existing-physics regressions returned zero with zero scoped error/reentry/
  cycle/time counts. The runtime owner's final Console review recorded 23 warnings and
  23 errors; the native sample had four error and 20 warning lines. Intentional discovery
  failure and a missing-parent-directory harness failure were retained. Transient
  `NO_STAGE` polling and a Console-input schema refusal also remain recorded. Warnings
  include closed-client responses and schema/setup messages; no blanket harmlessness
  claim follows. This process exited normally; temporary Console captures were deleted.

Provenance: the [committed compact record](evidence/project-scene/RECENT-601W-20261102.json)
is byte-identical to inspected `601w-handoff-6bc.json`, timestamp
`2026-11-02T01:35:53.527027+00:00`,
SHA-256 `3adcec65eefc272f3acf087fb3bcfa0daf411625e83a785a1bead7da0a28d4b2`;
all 26 original source-record hashes matched. The separately inspected v2 ledger SHA-256 is
`23fc86b4a366a01565361e138edb32b7e08ac34b0237903c5ee1ffc07dc4a710`, with addendum
`7c906199091b9381aff476fcbaadd80c723a5a47884b219e88d0b80838f52f60`.
Redacted reproduction supplements remain in the unpublished draft evidence release;
publication curation is ongoing. These narrow integrated checks do not constitute a
new broad all-tool run, clean-install qualification, other-build support or closure of
the unresolved `20f2953` actual-use failure.

## 6.1.0.0 PhysX follow-up (2026-11-02)

These checks used package **6.1.0.0**, build `6.1.0-rc.26+release.49347.2d230af4.gl`,
Kit `110.3.0+feature.371399.00c488ae.gl`, Ubuntu 24.04.5 / RTX 5080 / driver 580.178.04
and existing installations. Broad headless and windowed runs used exact
**`6bc45c2977270de748b58c630c21cd03f0d74fb6`**. Each completed 14 phases covering the
39 generic tools, 23 UR5e tools and seven bridge built-ins; this is not all 99 names
in each simulator mode.

| Scope | Headless | Windowed |
|---|---|---|
| Full-surface portion | 137 calls / 55 assertions | 141 calls / 54 assertions |
| Independent authored USD effects | 24 calls / 12 checks | 24 calls / 12 checks |
| Articulated URDF runtime checks | 25 calls / 7 checks | 25 calls / 7 checks |
| Save/source preservation | 21 checks; two layers / 18 source files unchanged | 21 checks; two layers / 21 source files unchanged |
| Final native log, including shutdown | 24 error / 6,353 warning lines, including 6,087 simulation-time occurrences | 24 error / 383 warning lines, including 62 simulation-time occurrences |

The native errors were attributed to deliberate missing/corrupt-asset and invalid-quaternion
tests. Legacy and existing-physics regressions separately returned zero with no recorded
error/reentry/cycle/time occurrences in their intervals. Final logs had zero cycle, reentry,
`GetCamera`, Force-callback and edit-target-change matches. Both processes exited; these
observations do not establish that all warnings are harmless. The earlier headless visual
review sampled a shorter log with 6,060 simulation-time occurrences; it is not the final
shutdown count. The windowed Console review showed 270 warnings and 63 errors before
independent use/save continuation. UI totals are not native-line counts, and no final
Console review after that continuation is claimed.

The runtime owner viewed two frames from each fully decoded broad recording (170 headless,
191 windowed frames), with diagnostic zero-pose upper-arm/wrist cropping retained. Headless
sensor/video review is not an application-window or Console check.

A separate windowed actual-use task at the same `6bc45c2` product source recorded **90
requests across 40 names: 38 scoped passes, two partial results and 59 uncalled names**.
Seventeen checks included sample fall/rest, Agilebot and UR5e target/return measurements,
gripper following, fresh camera capture, source preservation and a composed USD save.
The partial observations are the light's isolated contribution and standalone stage clearing
before Quick Start. Fixture runtime joints were not measured; robot results belong to
Agilebot and UR5e. The retained source audit found 261 commit-matching files and one launcher
with diagnostic instrumentation and launch-time saved-scene loading; host/extension files
matched the commit. The task explicitly created a new stage and ran Quick Start.

Its 480 measured steps advanced **four seconds of simulation time**. The native task interval
had zero errors and six warnings. All 210 video frames decoded, with three frames sampled;
UR motion lay outside that recording's view. The runtime owner separately reviewed four
selected sensor/window PNGs. An immediate screenshot after framing still showed the prior
view; a later ordinary-update screenshot showed the requested view. No atomic render/physics
frame or framing-latency guarantee follows.

The resulting agent save was reopened in a **fresh 6.1.0.0 headless PhysX process** at exact
**`d725f84cb853bf5ceb2fa3b7d13d0e002d29becf`**. Sixteen named calls / 12 checks verified
source-byte preservation, sample support/mass, both robot articulations and a new camera's
pixels before/after 12 steps advancing 0.1 simulation seconds. The old saved camera remained
authored, but its previous live sensor ownership correctly refused reuse. The native review
recorded zero errors and 18 warnings; the process exited. Two sensor PNGs were visually
reviewed by the runtime owner, not application-window captures. This establishes composed
USD reopening and new runtime initialization, not solver/controller-state restoration.

[Compact records and source hashes](evidence/project-scene/RECENT-61-20261102.json)
(SHA-256 `222e15ccaace02b89e91095c0640e0ee953ebee74cbfaf5bd669ff08080334e5`)
identify all 26 inspected source records. Redacted bundles and sampled video are listed in
the [unpublished draft release index](evidence/project-scene/RELEASE-ASSETS.md).
These PhysX results do not qualify generic Newton tools or an installed Isaac Lab
Newton/MJWarp pairing; those are separate scopes. They do not resolve the original `20f2953`
6.0.1 stall. That build's newer independent task save has a [separate later reopen](#three-later-agent-save-reopens),
which does not change the original task ledger. Remaining build/mode/backend, clean-install
and final-review gates remain open.

## 6.2.0.0 PhysX follow-up (2026-11-02)

The [parent/setup index](evidence/project-scene/RECENT-62-20261102.json) and separate
[independent clean-stack addendum](evidence/project-scene/RECENT-62-AGENT-20261102.json)
record these scopes. Their SHA-256 values are respectively
`2034b8869d5c60d858ac49ef930724bd12b14318dbb9dc9d70b00c56a95b28f0` and
`0d236c5dd1cfe396deab2c5a0dbee8aa61105224c13b9e7c54faa3f3a6275d2d`;
all 51 + 15 original source-record sizes and hashes were rechecked for this summary.
The evidence files preserve earlier observations; later documentation commits are not new runs.

These runs used package **6.2.0.0**, build `6.2.0-rc.14+release.52805.02189a84.gl`,
Kit `110.4.0+feature.386251.187cf21b.gl`, Python 3.12.3, Ubuntu 24.04.5,
RTX 5080 and driver 580.178.04. Windowed runs used X11. All results below are **PhysX**.

| Environment and mode | Exact source | Full-surface calls / assertions | Save checks; preserved layers / source files |
|---|---|---|---|
| Existing, headless | `d725f84cb853bf5ceb2fa3b7d13d0e002d29becf` | 137 / 55, same-process retry | 21; 2 / 15 |
| Existing, windowed | `2fdf4044ffdaa081431c14485a88d00abe185c07` | 141 / 54 | 21; 2 / 18 |
| Isolated locked, headless | `c2ceb7f1b8130f414872cb4475ffdd1c2d38b96a` | 137 / 55 | 21; 2 / 18 |
| Isolated locked, windowed | `c2ceb7f1b8130f414872cb4475ffdd1c2d38b96a` | 141 / 54 | 21; 2 / 14 |

Across the broad phases, each mode exercised 39 generic tools, 23 UR5e tools and seven
bridge built-ins. Host/desktop and Lab evidence is separate, not all 99 tools per mode.
The runs also passed legacy/existing-physics regressions, 24-call / 12-check authored
USD effect checks and 25-call / seven-check articulated URDF runtime checks. Saving
preserved the recorded source hashes; composed USD export is not a solver/controller checkpoint.

The existing headless run's initial full-surface attempt refused the simulated ROS driver
at call 84 because another driver was detected. A retry under a new evidence tag in the
**same Kit process**, without code changes or guard weakening, passed. A concurrent CPU-test
mock was considered, but no matching PID was captured: the cause remains unproven.
An invalid ROS domain 232 separately caused a locked-environment launch request to refuse
before Kit started; the corrected launch used domain 171. That refusal is a harness input
failure, not a simulator-functionality pass.

The existing stack contained **NumPy 2.5.4** despite the kernel's **2.3.1** requirement;
its dependency check failed. The separate new locked venv used identical `2fdf404`/`c2ceb7f`
project inputs, NumPy **2.3.1**, and passed the 186-package check. Cached locked sync took
18.13 seconds on the existing machine. No existing environment was repaired, and neither
full `install.sh` execution nor clean-machine setup was established. See [reproducibility](REPRODUCIBILITY.md).
Differences between these runs are not evidence that NumPy caused or fixed native warnings.

### Native logs, Console and visual scope

Counts below are **error / warning / illegal-cycle / simulation-time occurrences** in the
specified native file scope. They are not counts of failed functional checks or Console totals.

| Run | Reviewed prefix | Final log including shutdown |
|---|---|---|
| Existing headless `d725f84` | 30 / 9,053 / 2,897 / 5,855 | 30 / 9,142 / 2,926 / 5,914 |
| Existing windowed `2fdf404` | 24 / 582 / 0 / 125 | 24 / 801 / 0 / 125 |
| Locked headless `c2ceb7f` | 24 / 14,299 / 0 / 13,958 | 24 / 14,330 / 0 / 13,989 |
| Locked windowed `c2ceb7f` | 24 / 83,206 / 0 / 82,297 | 24 / 83,214 / 0 / 82,297 |

Final logs recorded zero reentry, `GetCamera`, Force-callback and edit-target-change markers.
The 24-error cases include deliberate missing/corrupt-asset and invalid-quaternion tests;
original responses and other diagnostics remain retained. Neither passing checks nor those
negative tests justify treating unexplained errors or warnings as harmless. All owned
processes exited normally; the locked windowed final log includes subsequent independent use.

The existing windowed Console showed 412–441 warnings / 63 errors during the parent review,
and 804 / 63 at the later review. The locked parent sampled 60,703 / 63, while its later
independent task saw 83,217 / 63. These UI observations have different timing and aggregation
from the native prefixes, final files and bounded task intervals; do not subtract them to
infer a task's native error count.

Runtime-owner visual review sampled **2/172**, **3/193**, **2/170** and **2/193** decoded
broad-video frames respectively for the table's four runs. Diagnostic upright poses crop
the upper arm/wrist; these recordings are not full-robot visual certification. The locked
headless save camera is mostly black with only a partial scene. The later locked windowed
save view is obscured by a retained lighting-test cube, not evidence of a robot geometry
failure. The broad window capture and separate independent task show the complete robot.
Headless sensor/video observations are not window or Console inspections.

The locked windowed parent's separate 16-call / six-check lighting experiment measured
red, blue and zero-intensity effects on a neutral cube. This is scoped rendered-effect
proof, not photometric calibration or qualification of every light type.

### Independent tasks and saved-output reopening

The **existing `2fdf404`** task made 119 requests: 28 before a hold and 91 after the user
confirmed their manual lighting/framing edits and authorized continuation in the same process.
The earlier report that Kit had exited during that hold was corrected; only the later normal
shutdown is exit evidence. Fifteen measured checks passed, including 720 steps advancing
six simulation seconds, sample rest and both robots' target/return. Its ledger records
38 scoped passes, two partials (isolated light contribution and visual confirmation of a
window click), and 59 uncalled names. The task's native interval had two warnings and no
errors. Its full-robot video decoded 429 frames, with three visually sampled. The README
media retains this existing-stack identity and is not relabeled as locked-stack evidence.

That task's **older save** was reopened in a fresh locked **`c2ceb7f` headless** process:
16 named calls / 12 checks passed, including two initialized articulations, supported body
state, new camera images and 12 steps advancing 0.1 simulation seconds. Source bytes stayed
unchanged. The saved camera was not a currently owned live sensor; a new camera supplied
the captures. Native review recorded 18 warnings and zero errors/cycles/time warnings;
the process exited normally. First shader/startup preparation took about 222 seconds,
not a clean-machine installation timing.

The separate **locked `c2ceb7f` windowed** actual-use task made **92 requests** (91 named
calls plus one catalog listing), across 40 names. All **17 measured checks** passed;
its ledger retains **38 scoped passes, two partials and 59 uncalled names**. The partials
are independent focus-state readback for `raise_window` and the light's isolated contribution;
the parent's lighting experiment does not upgrade that agent's observation.

Over 720 steps, physics advanced **six simulation seconds**. The sample settled at
0.829999983 m with zero velocity; Agilebot and UR5e reached and returned within 0.001 rad.
RG2 followed the wrist with approximately 1.005e-7 m relative translation drift; gripper
opening/controller competition and the imported test fixture's joint were not independently
exercised. Forty source/cache files and 262 frozen files stayed unchanged. Of those frozen
files, 261 match the commit; the launcher contains documented diagnostic logging/faulthandler
and initial saved-stage loading. The task then explicitly reset its disposable stage through
a named tool. This deviation is preserved, not described as byte-identical launch code.

All 438 frames of its 43.8-second actual-window video decoded; the child visually sampled
frames 0/150/437, showing the full UR/RG2. The runtime owner separately reviewed target,
return and final Content screenshots plus one video sample. The bounded task interval had
eight warnings, zero errors and zero cycles; it is separate from the final whole-session log.
**This newer clean-agent composed save subsequently passed its own fresh-process reopen**
at exact `c2ceb7f`; see the [later three-save follow-up](#three-later-agent-save-reopens).
That result is separate from the older `2fdf404` save's reopen and the original task ledger.

Redacted records and videos are listed in the [draft release index](evidence/project-scene/RELEASE-ASSETS.md);
they remain unpublished and require access. The original `20f2953` / 6.0.1 native-step
stall is unresolved. PhysX evidence review, clean-machine/full-installer coverage,
dependency-clean Lab setup and final review remain separate; Newton limitations follow the
[current experimental scope](#release-backend-scope). Installed backend packages and historical
99-name invocation coverage do not establish universal tool or version support.


## 6.0.0.0 PhysX follow-up (2026-11-02)

The [committed 6.0.0.0 supplement](evidence/project-scene/RECENT-6000-20261102.json)
records fresh headless and windowed runs on exact source
`c2ceb7f1b8130f414872cb4475ffdd1c2d38b96a`, package **6.0.0.0**, build
`6.0.0-rc.22+release.33481.407f3ea1.gl`, Kit `110.0.0+feature.276876.4a5123f4.gl`.
Its SHA-256 is `7ec9fcd4577cee9957602c26909467210d637c2ea357b6c505350eba19f37b07`;
all 29 original record hashes and sizes were checked for this documentation update.
These runs used the prepared 6.0.0.0 interpreter and PhysX, not the repository's locked
6.2 environment or a newly qualified older-build installation recipe.

| Recorded scope | Headless | Windowed |
|---|---|---|
| Broad phases | 14 passed | 14 passed |
| Full-surface calls / assertions | 137 / 55 | 141 / 54 |
| Additional authored-effect calls / checks | 24 / 12 passed | 24 / 12 passed |
| Articulated URDF calls / checks | 25 / 7 passed | 25 / 7 passed |
| Existing-physics and legacy verifier | Passed | Passed |
| Save assertions / source preservation | 21 passed; two live layers and 18 source files unchanged | 21 passed; two live layers and 18 source files unchanged |
| Reviewed native prefix errors / warnings | 38 / 243 | 24 / 241 |
| Final native errors / warnings | 38 / 243 | 24 / 250, including the independent task |
| Owned process exit | Normal | Normal, after the independent task |

The broad phases exercised all **39 generic tools, 23 UR5e tools and seven bridge
built-ins**. Host/desktop and Lab coverage are separate; this is not all 99 tools in
each mode. Both final native logs contain zero recorded cycle, simulation-time,
reentry, `GetCamera`, Force-callback or edit-target signatures. The headless error
count includes **24 deliberate negative-test markers plus 14 native property-window
callback traceback lines** (`save_scroll_pos` / `NoneType.scroll_y_max`). The subsequent
measured effects passed; that does not establish harmlessness or a fix for the callback.

Windowed Console observations were **246 warnings / 63 errors** after the parent suite
and **255 / 63** at the independent task's end. These UI counters are distinct from
native-log counts. Missing/corrupt offline robot assets and invalid quaternion inputs
were deliberate refusals; restored-cache recovery was checked separately. They do not
establish transactional preservation of an arbitrary existing scene on Quick Start failure.
The independent native interval contained **nine warnings and zero errors**; all warnings
remain retained, including interface-release and robot/gripper joint-transform warnings.

The runtime owner reviewed two of **173 decoded headless** video frames and two of
**189 decoded windowed** frames. Diagnostic arm/wrist cropping and the mostly black
headless save-camera view limit those observations; they do not establish a complete
workcell view. Separate parent window captures show the complete bent UR/RG2 and bench.
The independent task's actual-window evidence has a separate review scope.

### Independent task and subsequent saved-output reopen

The windowed task made **87 requests** (86 named calls and one catalog listing) across
42 names. All **19 predeclared effect checks** passed. Its original ledger remains
**41 scoped passes, one save partial and 57 uncalled names**: a composed save was produced,
but that independent run did not reopen it. The diagnostic launcher deviation remains
recorded; the frozen product source identity does not mean every launch byte matches Git.

Two 90-step groups advanced physics by approximately **three simulation seconds total
at 60 Hz**, independently of the paused timeline clock. The sample settled at about
0.14 m with zero velocity; measured joint states established UR5e and Agilebot target
and return effects. Wrist and gripper displacement were observed; gripper fingers were
not commanded. Fresh camera pixels changed, cached pixels matched their acquired target,
and 230 preexisting source/cache files remained unchanged. A lowercase `/World/ur5e`
lookup refused with `NOT_FOUND`; discovery of `/World/UR5e` recovered the task. The agent
reference example was subsequently corrected, without a runtime change.

Both raw task videos decoded completely (438 and 308 frames); seven frames were visually
sampled. Raw recordings exposed private Console paths and are excluded from distribution.
The evidence bundle contains three reviewed original PNGs and an explicitly cropped
**1440×610 derivative of the 308-frame motion recording**. Before/after step observations
are not a rendering of every physics step. These media reviews are attributed to the
runtime owner and independent agent; this documentation update performed no new pixel review.

A **separate fresh headless process**, still exact `c2ceb7f` / 6.0.0.0, reopened the task's
saved output with **16 named calls and 12 passed checks**. The file hash stayed unchanged;
both six-joint articulations initialized, saved transforms composed, and a new camera
acquired images. The old camera's live-ownership request correctly refused with `NOT_OWNED`.
Twelve steps advanced **0.2000000104 simulation seconds**, preserving the saved **60 Hz**
rate. The final native log recorded 20 warnings, zero errors and none of the selected
signatures above; the process exited normally. Two reviewed 320×240 images show the bench,
UR arm and samples, with the right-side Agilebot partially cropped. This validates the
recorded composed-scene initialization, not restoration of solver/controller checkpoint
state, and does not rewrite the independent ledger's historical save partial.

The three redacted bundles are indexed in the [draft release](evidence/project-scene/RELEASE-ASSETS.md)
and remain unpublished. The original `20f2953` / 6.0.1 native-step stall is unresolved.
These 6.0.0.0 results do not qualify current 6.0.0.1 work, generic Newton, Lab, physical
hardware, a clean-machine/full-installer path, or final all-version readiness.


## 6.0.0.1 current-runtime PhysX follow-up (2026-11-02)

The [committed current-run and reopen supplement](evidence/project-scene/RECENT-6001-20261102.json)
records exact source `c2ceb7f1b8130f414872cb4475ffdd1c2d38b96a`. Its SHA-256 is
`c448a85b0459706487ae0e600e25ea5011bd1abd2bbe87c5566e32376b180505`; all 36 original
source hashes and sizes were verified for this update. The broad runs used prepared
**6.0.0.1** environments, build `6.0.0-rc.59+release.41464.5f2772bc.gl`, Kit
`110.1.1+production.305458.6312fa25.gl`, with **PhysX**. These are new runs at `c2ceb7f`,
not relabelled `4499bf7` evidence or a clean-install qualification.

| Recorded scope | Headless | Windowed |
|---|---|---|
| Broad phases | 14 passed | 14 passed |
| Full-surface calls / assertions | 137 / 55 | 141 / 54 |
| Additional authored-effect calls / checks | 24 / 12 passed | 24 / 12 passed |
| Articulated URDF calls / checks | 25 / 7 passed | 25 / 7 passed |
| Existing-physics and legacy verifier | Passed | Passed |
| Save assertions / preservation | 21 passed; two live layers and 18 source files unchanged | 21 passed; two live layers and 18 source files unchanged |
| Native prefix errors / warnings | 24 / 8,586 | 24 / 43,884 |
| Final native errors / warnings | 24 / 8,679 | 24 / 43,891, including the independent task |
| Final cycle / simulation-time occurrences | 0 / 8,464 | 20 / 43,648 |

The broad phases exercised all 39 generic tools, 23 UR5e tools and seven bridge built-ins;
host/desktop and Lab coverage remain separate. Both processes exited normally. Final logs
contain zero recorded reentry, `GetCamera`, Force-callback or edit-target signatures.
The 24 error markers are retained negative tests. The cycle and simulation-time warnings
remain recorded without a harmlessness or cause claim. Parent windowed Console samples
showed 31,808 then 35,606 warnings and 63 errors; the independent task ended at 43,896 / 63.
These cumulative UI counters are distinct from native counts and bounded task intervals.

Runtime-owner review sampled two of 173 decoded headless motion frames and two of 186
windowed frames. Diagnostic arm/wrist cropping limits those videos; a separate actual-window
capture showed the full bent UR/RG2. The small save-camera views were partially framed.
This documentation update did not repeat pixel review.

The independent windowed task made **88 requests** (87 named calls and one catalog listing)
across 40 names. All **27 predeclared checks** passed. Its ledger remains **37 scoped passes,
three partials and 59 uncalled names**. The save partial describes the original task's lack
of a fresh reopen. `window_type` and `window_click` remain partial: acknowledgements did not
always produce the intended visible text or tab/filter state. An Error-only Console filter
was not achieved; the search was cleared and Content restored, without clearing history.

The 180 explicit physics steps advanced **three simulation seconds at 60 Hz**; the paused
timeline clock was not used as step proof. The sample fell and settled on its support at
about 0.14 m with zero velocity. Joint readbacks measured both robots reaching and returning;
RG2 attachment motion was observed, but finger actuation was not commanded. All 237
preexisting source/cache/product files stayed unchanged. Of 112 product files compared with
the commit, 111 matched; the preexisting diagnostic launcher deviation remains recorded.

The bounded task interval had **seven warnings, zero errors and zero cycle/time signatures**.
A shorter native-tail tool response missed those warnings; the complete recorded interval
controls the report. All 296 original window-video frames decoded, with four visually
sampled. Exact-step calls used `rendered=false`: the video records screen-state transitions,
not every physics step. These scoped passes do not erase earlier whole-session warnings.

### Three later agent-save reopens

Each row is a **separate fresh headless PhysX process at exact `c2ceb7f`**, with **16 named
calls and 12 passed checks**, followed by normal managed shutdown. The saved file's hash
remained unchanged in each run. Original task ledgers and their save partials are retained.

| Saved task output | Reopen package / build / Kit | Preserved rate / 12-step simulation advance | Final native errors / warnings |
|---|---|---|---|
| Current `c2ceb7f` 6.0.0.1 task | 6.0.0.1 / `6.0.0-rc.59+release.41464.5f2772bc.gl` / `110.1.1+production.305458.6312fa25.gl` | 60 Hz / 0.2 s | 0 / 21 |
| Newer `20f2953` 6.0.1 task | 6.0.1.0 / `6.0.1-rc.7+release.42383.32955d8d.gl` / `110.1.2+production.326809.f9bf0dda.gl` | 120 Hz / approximately 0.1 s | 0 / 18 |
| Newer locked `c2ceb7f` 6.2 task | 6.2.0.0 / `6.2.0-rc.14+release.52805.02189a84.gl` / `110.4.0+feature.386251.187cf21b.gl` | 120 Hz / approximately 0.1 s | 0 / 17 |

All three runs observed both articulations, the supported sample with runtime mass, composed
camera definitions and fresh pixels from a newly registered camera. Requests for the saved
camera's prior live ownership correctly refused. None recorded cycle/time, reentry,
`GetCamera`, Force-callback or edit-target signatures. The runtime owner reviewed two
320×240 images per run: the 6.0.0.1 view mostly crops Agilebot, while the newer 6.0.1 and
6.2 views mostly crop UR5e. Joint/body/step responses establish the runtime checks;
these images do not prove a complete workcell view.

The locked 6.2 reopen uses the isolated NumPy 2.3.1 environment; it is separate from the
older `2fdf404` save's previously recorded reopen. These tests validate composed USD and
new runtime initialization, not restoration of solver/controller checkpoint state.
**The original `20f2953` / 6.0.1 native-step stall remains unresolved.** A later different
task's successful save/reopen does not resolve that failed run.

Five artifacts are listed in the [unpublished draft release index](evidence/project-scene/RELEASE-ASSETS.md).
The large windowed native log is separately compressed to respect the unchanged privacy-scan
budget. Later [Lab native/setup evidence](REPRODUCIBILITY.md#self-contained-lab-3-reproduction)
records scoped train/eval/play results with two retained dependency conflicts. Dependency-clean
Lab setup, clean-machine/full-installer testing and final review remain separate open gates;
Newton completion is outside the [PhysX release scope](#release-backend-scope). No all-tool/
all-version readiness claim follows.

## 6.2.0.0 Newton twin candidates (2026-11-02)

The [committed Newton twin supplement](evidence/project-scene/RECENT-NEWTON-TWIN-62-20261102.json)
has SHA-256 `7340be6cac7421b000579de54cf818301dd0967d89e5b247cc1ac6d05e6c3d8b`;
all 23 original source hashes and sizes were verified for this update. These runs used
private candidates on combined base `c2ceb7f1b8130f414872cb4475ffdd1c2d38b96a`.
Candidate10's complete runtime modules match source
`1e9f194979b2f3ecf4654d88c8439a14a9a55062` applied to that base. Candidate9 lacks the
later dynamic wrench `frame_id` assignment. Neither set is relabelled as an execution
of integrated `166e10d` or a later documentation head.

The environment was **Sim 6.2.0.0**, build `6.2.0-rc.14+release.52805.02189a84.gl`,
Kit `110.4.0+feature.386251.187cf21b.gl`, Python 3.12.3, Newton 1.6.1,
MuJoCo Warp 3.12.0 and Warp 1.17.0, on Ubuntu 24.04.5 / RTX 5080 / driver 580.178.04.
The selected engine was Newton, solver **MuJoCo Warp**, device **CPU**, at **120 Hz**.
CPU device does not establish use of the native MuJoCo CPU solver, and this twin
workflow is separate from generic Newton tools and Isaac Lab's Newton/MJWarp pairing.

| Candidate and scope | Headless | Windowed |
|---|---|---|
| Candidate9 broad requests / assertions | 138 / 56 passed | 142 / 55 passed |
| Candidate9 additional output calls / checks | 31 / 23 passed | 32 / 23 passed |
| Candidate10 fresh saved-twin reopen calls / checks | 10 / 16 passed | 10 / 16 passed |
| Candidate9 final native errors / warnings | 6 / 14,264 | 8 / 47 |
| Candidate9 simulation-time / cycle occurrences | 14,092 / 0 | 5 / 0 |
| Candidate10 narrow native errors / warnings | 0 / 17 | 0 / 17 |

The broad requests include the catalog listing and exercise all **23 UR5e tools and
seven bridge built-ins**. Output checks measured actual ROS RG2 commands, three joint
targets, mimic relationships, runtime pad gaps, forces, recording and saved output.
They do not prove every tool parameter or backend. Candidate9's reopened wrench stream
still had an empty frame name. Candidate10's separate fresh processes checked two actual
ROS gripper commands across Stop/Play and nonempty `tool0` headers with advancing physics
timestamps. Source/root/robot-subtree/graph/physics/edit-target preservation passed in
those narrow Newton intervals; this is not universal whole-root preservation.

A separate candidate10 known-load control passed **six calls / eight checks** with a
stationary 2 kg wrist, `tool0` rotated 90 degrees about Y and offset by 0.1 m. The actual
product ROS wrench measured approximately `[-19.62000084, 0, -0.00000067, 0, 1.96200008, 0]`
N/N·m against expected `[-19.62, 0, 0, 0, 1.962, 0]`. Runtime mass, stationarity, advancing
headers and unchanged source hashes were checked. This qualifies that gravity/frame/offset
control, not physical sensor calibration, contact forces or an independent RG2 equality-weld
reaction measurement. Its final native log had zero errors and 12 warnings.

Candidate9 windowed Console counts were **50 warnings / 18 errors**; candidate10's were
**19 / 0**. These are separate from native-log counts. Retained errors include negative
tests and an inspection-harness exception; the counts do not establish harmlessness.
All owned candidate processes exited. Runtime-owner visual review sampled three frames
from each 264-frame headless and 265-frame windowed output video. Bases were cropped;
windowed frame zero retained the previous camera view. The separate broad windowed
video had 193 decoded frames, two sampled, with wrist cropping. This update did not repeat
pixel review or infer physics from video.

Earlier failures remain in the [detailed evidence](evidence/project-scene/FINAL-VALIDATION.md):
saved-twin Play succeeding without tensors/callbacks, root transform normalization,
whole-root changes from viewport metadata, and cold recording refusing after more than
five seconds of lag with one frame. The later warmed recording does not erase that failure.
See the [twin contract](TOOLS-UR5E.md) for explicit UR Play restoration, force capability
checks and recording limits; ordinary generic Play is not proof of restored twin callbacks.

**The original integrated PhysX failure remains recorded.** The first fresh windowed reopen
at `166e10db31cc2e24fad66b204c00301c1281bfae` produced actual wrench messages with zero
timestamps and empty frames despite working motion. The [later correction candidate](#6200-physx-wrench-correction-candidate-2026-11-02)
has separate evidence; the later [integrated affected-path checks](#integrated-affected-physx-paths-across-five-builds-2026-11-02)
record fresh native revalidation within their stated scope.
PhysX's native in-memory robot pose writes during Play must not be described as preserved
by the Newton checks above: saved-file, graph, physics-setting and edit-target preservation
are distinct observations, not proof of unchanged live robot poses or disk overwrite.

Newton **1.0 full-twin mounting is unsupported**; a bundled-USD parser probe disproved
path-only weld relocation. Older backends without incoming-joint wrench output report
unavailable, not substitute zeros; native MuJoCo CPU/VBD do not qualify from these results.
These experimental Newton observations do not establish PhysX qualification or
clean-machine/full-installer coverage; further Newton completion is outside the release scope.
The original `20f2953` 6.0.1 stepping stall remains unresolved. The larger bundle is indexed in the [unpublished draft release](evidence/project-scene/RELEASE-ASSETS.md);
these scoped results do not establish all-version readiness or public artifact availability.

## 6.2.0.0 PhysX wrench correction candidate (2026-11-02)

The [committed PhysX wrench supplement](evidence/project-scene/RECENT-PHYSX-WRENCH-62-20261102.json)
has SHA-256 `1db480c45beda2217d39f8359279a08eb76ffdb290503caf9c24a56bb56343c1`;
all 21 original source hashes and sizes were verified for this update. It preserves
the original `166e10db31cc2e24fad66b204c00301c1281bfae` failure and private candidates
on that base. Source `7454f4466788d8acef7e3d8e900dd021c7d7c167` matches candidate12's
physics helper; its extension differs in a setup docstring and diagnostic rate text.
These observations remain candidate12 evidence, not fresh execution of integrated `2c8ce63`.

Runs used **Sim 6.2.0.0**, build `6.2.0-rc.14+release.52805.02189a84.gl`, Kit
`110.4.0+feature.386251.187cf21b.gl`, **PhysX / CPU / 120 Hz**, Python 3.12.3,
Ubuntu 24.04.5, RTX 5080 and NVIDIA driver 580.178.04.

| Source and measured scope | Result |
|---|---|
| Original `166e10d`, headless existing PhysicsScene | Nine calls; existing scene preserved |
| Original `166e10d`, headless full surface | 137 requests / 55 assertions passed; all 23 UR tools and seven bridge built-ins |
| Original `166e10d`, fresh windowed legacy reopen | Failed: 686 actual wrench messages had zero timestamps and empty frames despite gripper motion/callbacks; harness stopped at its first header assertion |
| Candidate12, headless known-load control | Six calls / eight checks passed |
| Candidate12, fresh windowed legacy-twin reopen | Ten calls / 16 checks passed |
| Candidate12, windowed full surface | 141 requests / 54 assertions passed; all 23 UR tools and seven bridge built-ins |

The legacy reopen checked two actual ROS gripper commands across Stop/Play, advancing
physics timestamps and the `tool0` frame. The stationary 2 kg control used a 0.1 m offset
and 90-degree Y rotation. Actual product ROS output was approximately
`[-19.62000465, 0, -0.00000067, 0, 1.96200047, 0]` N/N·m against expected
`[-19.62, 0, 0, 0, 1.962, 0]`, with fixture files unchanged. This is a simulated
gravity/frame/offset check, not physical sensor calibration or general contact-force validation.

**Preservation is scoped.** Source files, graph, physics settings and edit target remained
unchanged, but whole in-memory root and robot-subtree bytes differed during PhysX playback.
Independent diagnostic copies excluded only native rigid-body transform/velocity and joint-state
fields; remaining authored robot/object opinions compared equal. The inspection did not edit
the originals. This predicate is not whole-root preservation, and native in-memory writeback
is not evidence of a disk overwrite. Candidate11's ineffective experiment that scoped initial
PhysX Play to the session layer remains retained and was not shipped.

| Native-log scope | Errors / warnings | Simulation-time / cycle occurrences | Separate Console sample: warnings / errors |
|---|---|---|---|
| Original `166e10d` headless | 6 / 124 | 1 / 0 | Not recorded here |
| Original `166e10d` failed windowed reopen | 0 / 29 | 0 / 0 | 31 / 0 |
| Candidate11 diagnostic | 0 / 70 | 0 / 0 | 96 / 0 |
| Candidate12 headless known load | 0 / 11 | 0 / 0 | Not recorded here |
| Candidate12 windowed | 6 / 151 | 1 / 0 | 154 / 11 |

The candidate12 six native Error lines belong to the intentional zero-quaternion traceback;
the runtime owner reviewed that filtered Console error, not every one of its 11 entries.
Zero Console errors in the original reopen did not establish valid ROS headers. No general
harmless-warning claim follows. All owned processes exited. Runtime-owner visual review
sampled frames 10 and 100 from the original 169-frame headless and candidate12 191-frame
windowed recordings; both crop the wrist/gripper. A separate final window showed the complete
bent arm/gripper. This update did not repeat pixel review or infer physics from the recordings.

Candidate12 observed a new validity gate and saved the current graph. Fresh reopening was
pending at that handoff; the later [exact integrated checks](#integrated-affected-physx-paths-across-five-builds-2026-11-02)
now record it separately. As the [twin contract](TOOLS-UR5E.md)
explains, fresh PhysX graphs gate publication until a complete sample exists. Legacy ungated
PhysX graphs remain unchanged on reopen and may emit default/cached values before a valid
callback; a successful warmed legacy test is not proof of a gate. Check capability and actual
messages. These candidate passes do not erase the original `166e10d` failure, complete the
affected five-build/mode checks, or resolve the original `20f2953` stepping stall. Newton,
clean-machine/full-installer and broader backend claims remain separate. The larger transcript
bundle is in the [unpublished draft release index](evidence/project-scene/RELEASE-ASSETS.md).

## Integrated affected PhysX paths across five builds (2026-11-02)

The [committed affected-path supplement](evidence/project-scene/RECENT-AFFECTED-PHYSX-2C8-20261102.json)
records exact source `2c8ce63b33dbabf02b2216f9d512ff9a23724d9c`, tree
`77fc20b42b97eebdb0aa04273bb7ef5281bd3001`. Its SHA-256 is
`28597c734ac91958238f24e35bcc9a2620c698804474a9cdb6e1053ccbf3673d`;
all 88 key original hashes and sizes were verified for this update. All ten runs used
PhysX and Python 3.12.3 with motion BVH enabled. Older prepared environments remain
separate from the isolated locked 6.2 environment; this is not a new installation test.

**The affected UR paths passed in both modes on each of the five builds below.** Each
headless full-surface run completed **137 calls / 55 assertions**; each windowed run
completed **141 / 54**. Each exercised all **23 UR tools and seven bridge built-ins**,
with nine additional calls checking an existing PhysicsScene. Named generic support and
managed targeting were exercised, but this is not a new complete 99-tool matrix or an
independent-agent task.

| Sim package | Exact application build | Kit | Headless callback scope | Windowed callback scope |
|---|---|---|---|---|
| 6.0.0.0 | `6.0.0-rc.22+release.33481.407f3ea1.gl` | `110.0.0+feature.276876.4a5123f4.gl` | Same process after settled Stop, 11 calls / 16 checks | Fresh reopen, 10 calls / 16 checks |
| 6.0.0.1 | `6.0.0-rc.59+release.41464.5f2772bc.gl` | `110.1.1+production.305458.6312fa25.gl` | Same process after settled Stop, 11 calls / 16 checks | Fresh reopen, 10 calls / 16 checks |
| 6.0.1.0 | `6.0.1-rc.7+release.42383.32955d8d.gl` | `110.1.2+production.326809.f9bf0dda.gl` | Same process after settled Stop, 11 calls / 16 checks | Fresh reopen, 10 calls / 16 checks |
| 6.1.0.0 | `6.1.0-rc.26+release.49347.2d230af4.gl` | `110.3.0+feature.371399.00c488ae.gl` | Same process after settled Stop, 11 calls / 16 checks | Fresh reopen, 10 calls / 16 checks |
| 6.2.0.0 | `6.2.0-rc.14+release.52805.02189a84.gl` | `110.4.0+feature.386251.187cf21b.gl` | Fresh reopen, 10 calls / 16 checks | Fresh reopen, 10 calls / 16 checks |

The six fresh-process checks comprise 6.2 headless plus all five windowed runs. The
6.2 headless process opened candidate12's newly gated save; its windowed process opened
the exact `2c8ce63` headless output. The other four headless callback tests stayed in
their existing process and explicitly stopped before taking preservation baselines.
They must not be described as four additional fresh reopens. All ten checks observed
two actual ROS gripper commands across Stop/Play, joint motion and nonzero advancing
`tool0` wrench headers. These integrated observations address the measured callback/header
failure on these tested paths; the original `166e10d` failure remains in the history.
Candidate12's known-load result retains its candidate identity and was not rerun by this batch.

Source files, graph, PhysicsScene opinions and edit target compared equal. Diagnostic
copies excluding enumerated native body pose/velocity and joint-state fields preserved
remaining static robot/object opinions. **Whole-root and robot-subtree byte equality were
false in every run** because native PhysX wrote pose/state into memory. These passes do
not establish byte-preserved live layers or a disk overwrite. Legacy ungated graphs still
have the documented default/cached-value warm-up caveat; a later valid sample does not
retrofit a gate into an old graph.

The initial 6.0.0.0 headless supplemental predicate compared a generic leased session
edit target with the original target restored by Stop and failed equality. ROS effects
and file/graph/physics/static comparisons passed. Its client also overlapped parent-harness
completion. That attempt remains separate from the successful sequential retry with an
explicit settled Stop before the baseline; no failed record was replaced.

| Build / mode | Final native errors / warnings | Simulation-time / cycle occurrences | Windowed Console warnings / errors | Decoded video frames |
|---|---|---|---|---|
| 6.0.0.0 / headless | 6 / 166 | 0 / 0 | — | 174 |
| 6.0.0.0 / windowed | 6 / 157 | 0 / 0 | 162 / 11 | 189 |
| 6.0.0.1 / headless | 6 / 151 | 1 / 0 | — | 172 |
| 6.0.0.1 / windowed | 6 / 157 | 2 / 0 | 162 / 11 | 188 |
| 6.0.1.0 / headless | 6 / 147 | 1 / 0 | — | 172 |
| 6.0.1.0 / windowed | 6 / 151 | 2 / 0 | 155 / 11 | 186 |
| 6.1.0.0 / headless | 6 / 147 | 0 / 0 | — | 171 |
| 6.1.0.0 / windowed | 6 / 153 | 2 / 0 | 156 / 11 | 191 |
| 6.2.0.0 / headless | 6 / 157 | 2 / 0 | — | 171 |
| 6.2.0.0 / windowed | 6 / 156 | 1 / 0 | 159 / 11 | 191 |

Each native log's six Error lines belong to the deliberate invalid-quaternion traceback;
no force-callback error was recorded. The filtered windowed Console views confirmed that
negative test, not each of the 11 UI error entries individually. Cooking warnings and
simulation-time occurrences remain recorded without a harmlessness claim. In 6.0.0.1
and 6.1, acknowledged clicks initially failed to select Console; verified screenshots
and a later retry established the inspected tab. No cause is assigned to that desktop behavior.

The runtime owner decoded each full recording but visually sampled only frames **10 and
100**. Wrist/gripper cropping affects all clips. Motion was not clearly distinguishable
in the sampled 6.0.0.1 headless and 6.0.1 headless/windowed views; runtime joint/ROS checks
supply the motion evidence. Separate final window reviews showed complete bent arms and
attached grippers. This documentation update did not repeat pixel review. All ten owned
Kit processes exited; no unrelated process was stopped.

Earlier generic, host, Lab and independent-task evidence keeps its original source identity.
The component-equivalence manifest compares implementation bytes; it is not a rerun or
proof of every edge case. **The original `20f2953` / 6.0.1 native-step stall remains
unresolved.** Final PhysX per-tool/version review and clean-machine/full-installer qualification
remain separate from the [experimental Newton limitations](#release-backend-scope).
The [draft release index](evidence/project-scene/RELEASE-ASSETS.md) links the larger redacted transcript bundle; the release remains unpublished and PR16 unmerged.

## 6.0.0.1 Newton failures and separate host-envelope checks (2026-11-02)

**The Newton twin workflow failed in both headless and windowed qualification on exact
`2c8ce63b33dbabf02b2216f9d512ff9a23724d9c`.** The
[committed failure supplement](evidence/project-scene/RECENT-NEWTON-6001-FAILURES-20261102.json)
(SHA-256 `d9863bdd51b12fbf0b53fcb08a132712ca6c0b036dbbe5374858a3ed300dc68a`)
separates the unchanged runtime from private room-pause and CPU-thread controls. Its 44 key
source records identify the retained runs; the curated evidence reports 728 original hashes.
The larger redacted bundle is indexed in [release assets](evidence/project-scene/RELEASE-ASSETS.md)
and remains attached to an **unpublished draft release**.

The tested 6.0.0.1 stack was Ubuntu 24.04.5, RTX 5080 (16,303 MiB), driver 580.178.04,
Newton 1.2.0, MuJoCo 3.8.0, MuJoCo Warp 3.8.0.3 and loaded Warp 1.13.0. These failures
are separate from the completed PhysX checks on that build and the original `20f2953`
6.0.1.0 windowed stepping stall; a common cause has not been established.

- **Headless, unchanged runtime:** the five generic suites and existing-PhysicsScene
  checks completed first. Generic tools/equivalents/edges/sensors/clear retained
  32/49/23/327/4 records respectively; passing scripted predicates include expected
  refusals and do not establish every tool/parameter combination. Full-surface call 88
  (UR Play) returned success, then Kit disappeared before call 89's joint observation.
  The process log ended with `malloc(): unaligned tcache chunk detected`; native cause
  and exit signal were not established. Separate identity-checked simulated-driver
  cleanup succeeded after the bridge was no longer reachable.
- **Fresh headless full-surface-only control, unchanged runtime:** `load_simple_room`
  stalled. The captured Python stack localized execution to Replicator trigger discovery
  and a USD Runtime graph query; it does not identify the cause. SIGINT did not exit the
  process; the owned force-stop was cleanup, not recovery.
- **Windowed, unchanged runtime and normal thread budget:** the same five generic suites
  and nine existing-PhysicsScene calls completed. Full-surface call 39 was the last
  success; `add_objects` at call 40 returned a connection reset, followed by unreachable
  Stop and unavailable stage-info calls. Those failures do not prove a Stop reached Kit.
  Fatal stderr recorded a segmentation fault / invalid-pointer free, with the main stack
  in Newton's `LoadUsdPhysicsFromRange` / initialization / `on_play` path. This is execution
  localization, not root cause. The PID disappeared before managed cleanup; only an
  initial blank window was reviewed, with no final window or Console available.

The **private controls are not shipped fixes or qualified workarounds**. A room-pause
candidate passed earlier failure locations once, but its wrist-displacement comparison
used a moving baseline and its sampled first/last RGB buffers were zero. A second run
with the same candidate bytes and a settled-baseline harness failed during `add_objects`,
with a malloc diagnostic and Newton USD-parser/initialization stack. An environment-only
worker-limit attempt was ineffective (observed concurrency remained 16); the later
one-thread bootstrap control also changed Carb/TBB/OpenBLAS, not USD alone. It passed
previous failure locations and measured arm motion, but missed the fixed five-wall-second
RG2 predicate, returned zero RGB samples and failed recording with more than five seconds
of backlog. A later settled gripper read does not erase those failures. Message arrival,
dimensions and timestamps alone do not establish useful images; the post-reset lighting
control also changed arm pose and did not isolate lighting as a cause.

The supplement's correction takes precedence over generic shutdown-review wording:
the windowed process exited natively before a managed-stop request. Native-file counts
exclude fatal stderr in the separate process log. All seven owned Kit PIDs were later
absent and owned-driver cleanup was recorded. The curated bundle omits raw dumps,
environment metadata, full crash logs, geometry and media. A private control's crash
reporter attempted an automatic upload, with delivery unconfirmed; later disposable
processes disabled current and old dump uploads locally. No raw dump is published here.

A **separate host reporting fix**, source `7cdd54f`, integrated as
`826b84b69f04a080882c3ef5894877f389af2938`, corrects the earlier legacy
`isError=false` on actual errors. On **6.0.1.0 headless / Newton**, six real MCP calls
passed six checks: selected Kit identity, a successful query without the error bit,
application-error JSON and structured-string preservation with `isError=true`, a native
builtin error, and no file created by the failed upload. This qualifies those envelope
cases only. It neither fixes the 6.0.0.1 native faults nor establishes completion of the
later 6.0.1.0 twin workflow. Broader Newton qualification and the original stepping-stall
disposition remain open; historical CPU, PhysX, Lab and candidate results retain their
own source/build/mode identities.

## 6.0.1.0 Newton state and rendering failures (2026-11-02)

**Default Newton twin instability and rendering failures remain unresolved.** The
[committed state/rendering supplement](evidence/project-scene/RECENT-NEWTON-601-STATE-20261102.json)
(SHA-256 `082fe575acf6be58f95f72bce7d05dce1fdaa526aab90f4ecf94aa6bc37bb928`)
separates exact `826b84b69f04a080882c3ef5894877f389af2938` failures and private controls
from fresh `ba1d4b256a011d00787fe32b2eb85cecec8f8812` reporting checks and later failures.
Its 47 key records identify two serial **headless** processes on prepared Sim **6.0.1.0**,
build `6.0.1-rc.7+release.42383.32955d8d.gl`, Kit
`110.1.2+production.326809.f9bf0dda.gl`, Newton 1.2.1, installed MuJoCo 3.8.0 /
MuJoCo Warp 3.8.0.3 and bundled Warp 1.13.0. The host was Ubuntu 24.04.5,
RTX 5080 (16,303 MiB), driver 580.178.04. Default Newton here used **MuJoCo Warp**;
a CPU World does not establish use of the native MuJoCo CPU solver.

On `826b84b`, the five generic scripts retained 32/49/23/327/4 records, including
expected refusals, and nine existing-PhysicsScene checks completed. The twin workflow
invoked all 23 UR names and seven built-ins, but **failed at call 144**: a 120-step
request returned `INVALID_POSE` after 55 earlier passing predicates. The runtime wrist
pose and all six arm joint positions/velocities were NaN; Kit remained responsive and
paused. All 175 decoded video frames (17.5 seconds) were numerically black, with frames
10 and 100 visually reviewed. Sampled first/last ROS RGB buffers were also zero.
Earlier assertions, message receipt and decodable video are not a full-workflow pass.

Same-process controls did not qualify recovery or a solver workaround. After a retained
lease refusal, an explicit Stop/Play and settled UR-only baseline passed 120 single steps
and two 120-step batches with finite target motion. A later 91-frame UR-only video had
nonzero pixels; one frame was visually reviewed. Unassembled FMB4 stayed finite, while
assembled FMB4 failed. The first capacity setup failed at import without changing the
configuration; its subsequent trial was still the default. The corrected 1024-contact /
6144-constraint control removed overflow messages in the measured interval but still
failed after 185.84 **wall-clock seconds**. Native MuJoCo CPU stayed finite but missed
commanded joints by over 3.2 radians after three **simulation seconds**. A shorter Warp
step control and four substeps also became invalid; native contact generation refused
the planar ground mesh. These are failed private experiments, not recommended settings.
The `826b84b` process later exited through managed SIGINT without force.

Source `f55c64f`, integrated as `ba1d4b2`, improved error reporting. A fresh process
passed **33 real MCP calls / 14 checks**: successful UR-only movement followed by an
actual assembled-FMB4 failure after ten steps. `PHYSICS_STATE_INVALID` identified
`/World/UR5e/wrist_3_link`, field `orientation_xyzw`, backend `newton`, phase
`body_observation`, and a measured physics-time advance from 0.05 to 0.133333 seconds.
The response recorded `fabric_updated=true`, `rendered=false` and no rollback/retry.
Subsequent body/joint reads returned typed errors and another step was refused at
preflight. This qualifies reporting of partial effects, **not valid physics or recovery**;
see the [step error contract](TOOLS-SCENE.md).

Later controls in that same `ba1d4b2` process produced nonblack robot/bench/room images
and six 25-frame recordings. ROS-only received 131 images with a nonzero first sample.
After adding FMB1, 90 ROS messages had zero sampled first/last buffers, the generic PNG
was black and all frames of a 25-frame recording were black, despite finite poses for
five bodies and finite arm joints. Read-only inspection found authored USD world scale
`0.01` represented as unit scale in Fabric; Stop restored a visible, cropped image.
The retained installed-source comparison documents missing scale preservation in older
builds and its presence in 6.1/6.2. This supports the observed scale-loss defect; it does
not establish a cause for solver NaNs, earlier heap faults or the original stepping stall.

The proposed paused Fabric correction **never executed**. A later Play returned, then
Pause lost the connection and the PID vanished; stderr reported a segmentation fault
with the main Python frame in `SimulationApp.update`. No successful A/B, native cause,
exit-code inference or recovery is claimed. Three later stills were visually reviewed;
other video frames were numerically decoded. There was no headless application-window
or Console review. Both owned PIDs were absent at handoff.

The [draft evidence supplement](evidence/project-scene/RELEASE-ASSETS.md) records 768
original hashes and 758 checksums for 759 redacted archive members; geometry, media and
raw crash metadata are excluded. Reproduction retains the historical harness path
substitution, prepared interpreter and private diagnostic launcher (faulthandler and
upload controls). No vendor or installed-environment source was modified; this is not a
clean-machine/full-installer qualification. The release remains unpublished. Earlier
PhysX, Lab and Newton results keep their own identities; the original `20f2953` windowed
108-step stall and remaining backend/version qualification are still open.


## 6.1.0.0 Newton contact failures and unshipped controls (2026-11-02)

**The assembled Newton twin workflow failed; no reliable collider workaround was shipped.**
The corrected [v2 contact supplement](evidence/project-scene/RECENT-NEWTON-61-CONTACTS-20261102.json)
(SHA-256 `51859d0f5de59c1c4d61b56dec2a611911c94772352dd451f166034ecaacb2ba`)
separates exact `ba1d4b256a011d00787fe32b2eb85cecec8f8812` from three private
candidate processes over that base, identified by complete module hashes. Its 39 key
records cover prepared **6.1.0.0 / headless / Newton MuJoCo Warp**, build
`6.1.0-rc.26+release.49347.2d230af4.gl`, Kit
`110.3.0+feature.371399.00c488ae.gl`, Newton 1.5.0, installed MuJoCo and MuJoCo Warp
3.11.0, bundled Warp 1.16.0 and Python 3.12.3. The machine was Ubuntu 24.04.5,
RTX 5080 (16,303 MiB), driver 580.178.04. These are not new windowed or clean-install results.

On exact `ba1d4b2`, five generic suites retained 32/49/23/327/4 records and nine
existing-PhysicsScene checks completed. The twin workflow **failed at call 144 after
55 earlier passing predicates**: the first assembled-FMB4 120-step batch was finite,
but the second returned `PHYSICS_STATE_INVALID` for wrist orientation; native body
and joint arrays contained NaNs. Earlier predicates do not establish a completed workflow.
All 176 decoded video frames were nonzero and unique (17.6 seconds); the runtime owner
visually reviewed only frames 10 and 100, showing a lit room/bench and cropped upright
arm. This is not a full-robot view, valid-physics proof or application-window/Console review.

Private same-process controls retained a wrong-name assertion that partially edited one
disposable collider before stopping without Play. Corrected convex-hull and motion trials
stayed finite; a matched SDF trial failed its third 120-step batch while the hull trial
remained finite. Blocks falling or spreading without the bench did not demonstrate a
stable pyramid. These controls motivated a geometry-validated box adapter, but subsequent
fresh-process tests did not qualify it:

| Private process/control | Observation and limit |
|---|---|
| Candidate full workflow | 35 calls completed, then `ur5e_dt__load_simple_room` timed out at 300 seconds **before** object loading or the adapter ran. The SIGUSR2 stack located a Replicator timeline-trigger USD Runtime query; it did not establish cause. |
| First floor-contact process | Initially 33 calls / 720 steps advanced six simulation seconds, with final maximum block displacement about 0.107 mm and target following. Later Stop/Play cycles scattered the pyramid despite finite poses; native tensors and Fabric agreed. |
| Fresh repeated-cycle process | 62 calls / 2,160 steps across three Stop/Play cycles. The first two roughly retained the pyramid; the third reached about **216 mm maximum block displacement**, despite finite body/joint values. The earlier approximately 28 mm figure measured top-block height loss, not maximum displacement. |
| Box-primitive alternative | 63 calls / 2,160 steps remained finite but scattered the stack in the first and later cycles. This was not adopted. |
| Separated-hull alternative | Moving the assembly away from the robot still produced `PHYSICS_STATE_INVALID` on the first requested 120-step batch. This does not establish robot contact as the cause. |
| Twin-specific Play follow-up | Eight calls ended with `EDIT_TARGET_CHANGED` in approximately 0.063 seconds on the requested 120-step call. The managed session edit-target precondition was unmet; **attempted steps or a new NaN are not established**. |

The v1 derived note incorrectly classified that final refusal as `PHYSICS_STATE_INVALID`.
The v2 correction preserves unchanged call 008 (SHA-256
`3308de01d27518c3ebfcb4976c3f0ae32d13c4bd4f4254c4f07db86c7505a6c9`) and the
superseded compact/archive hashes. The separated-hull failure is the distinct NaN record.
Use v2 for interpretation; the original archive remains historical evidence.

The adapter, its tests and proposed contract were **parked privately, not included in
PR16**. Passing 106 focused CPU tests and initial finite intervals did not establish
stable contacts. No installed/vendor code or source asset changes are claimed. Candidate
saves were not freshly reopened in Kit; later collapsed saves are not stable-stack checkpoints.

A close camera inherited a one-stage-unit near plane (one meter here), hiding the intended
23 cm fixture. Moving the camera farther away showed scattered blocks; an environment light
was already present, so a missing-light cause was not established. The runtime owner reviewed
selected generic PNGs from first/third cycles and the primitive trial, not a real application
window or Console. This observation and the separate new-camera clipping change do not
resolve earlier ROS/Fabric black output or qualify the camera change's later native runs.

All four owned PIDs were absent at handoff: three exited through guarded SIGINT; the room
stall required guarded SIGKILL after SIGINT failed. Cleanup is not recovery. The
[unpublished draft supplement](evidence/project-scene/RELEASE-ASSETS.md) retains 483
individual call records, 695 original-input hashes and 692 checksums for 693 archive members,
without geometry, media, vendor modules or native dumps. No whole-workflow, all-version,
backend-wide or clean-machine claim follows. Earlier PhysX/Lab evidence and the original
`20f2953` windowed stepping stall retain their separate identities and unresolved limits.


## New-camera clipping across five builds (2026-11-02)

**Focused camera clipping and unit checks passed in ten fresh serial processes** on exact
`1f21407eacd02aa1387aa4348611aa60b7848a06` (tree
`c8b73b8a4a441978dee7a94d9d9bfb3472d7c717`). The
[committed camera supplement](evidence/project-scene/RECENT-CAMERA-CLIPPING-1F21407-20261102.json)
(SHA-256 `456f8d9d94f69de675ae05908ee32ab515176d57d10571ddc70eadf83ea3a09a`)
contains 39 key source records. These anonymous fixtures selected PhysX but performed
**no simulation steps**; this is not a physics, UR/ROS, Newton or full-tool matrix pass.

Each build/mode completed 50 core MCP calls: 42 positive calls and eight deliberate
invalid-unit control calls. Across ten runs, **500 core calls / 910 passing predicates**
cover 40 new cameras and 80 actual RGB captures. The predicate total comprises 420
successful-result flags plus 490 count, metadata, pixel, hash, saved-USD and error checks;
it is not 910 independent physical effects. Auxiliary viewport/desktop calls are separate.

| Package | Exact Sim build | Kit build | Headless / windowed focused outcome |
|---|---|---|---|
| 6.0.0.0 | `6.0.0-rc.22+release.33481.407f3ea1.gl` | `110.0.0+feature.276876.4a5123f4.gl` | Each: 50 calls / 91 predicates passed |
| 6.0.0.1 | `6.0.0-rc.59+release.41464.5f2772bc.gl` | `110.1.1+production.305458.6312fa25.gl` | Each: 50 calls / 91 predicates passed |
| 6.0.1.0 | `6.0.1-rc.7+release.42383.32955d8d.gl` | `110.1.2+production.326809.f9bf0dda.gl` | Each: 50 calls / 91 predicates passed |
| 6.1.0.0 | `6.1.0-rc.26+release.49347.2d230af4.gl` | `110.3.0+feature.371399.00c488ae.gl` | Each: 50 calls / 91 predicates passed |
| 6.2.0.0 | `6.2.0-rc.14+release.52805.02189a84.gl` | `110.4.0+feature.386251.187cf21b.gl` | Each: 50 calls / 91 predicates passed |

Meter and centimeter stages were tested at 128×128 and 384×384 resolution. New cameras
reported the physical 0.01 m / 1000 m clipping defaults converted into stage units.
Actual RTX images showed red and green targets at 23 cm; moving red inside the 1 cm
near plane removed red pixels while green remained, and returning it restored red.
PNG dimensions and hashes matched the returned metadata. Deliberate `metersPerUnit=0`
returned `INVALID_STAGE_UNITS` without creating a camera or changing the root layer,
and restored the prior edit target. See the creation contract in [TOOLS-SCENE.md](TOOLS-SCENE.md).

Two saved outputs per run were independently opened with **standalone OpenUSD** and
retained both cameras' clipping attributes. This does not qualify camera reuse after
fresh Kit reopening. Existing/reopened cameras keep their authored settings. Pack-authored
camera and geometry opinions use the session layer, but successful native RenderProduct
and settings allocation changed anonymous root-layer text: **whole-root preservation was
not demonstrated**. The invalid-unit refusal's unchanged-root result is a separate scope.

Camera-file color targets were horizontally cropped; their pixel counts test depth and
color effects, not complete-object framing. The runtime owner separately reviewed sampled
actual application windows, including an entire red cube after viewport framing, and the
Console before and after the negative control. Headless runs have no application-window
or Console claim. Final whole-native logs and final windowed Console counters differ:

| Package | Whole native warnings: headless / windowed | Final windowed Console warnings / errors |
|---|---|---|
| 6.0.0.0 | 13 / 15 | 17 / 0 |
| 6.0.0.1 | 15 / 18 | 21 / 0 |
| 6.0.1.0 | 12 / 15 | 17 / 0 |
| 6.1.0.0 | 12 / 14 | 15 / 0 |
| 6.2.0.0 | 13 / 15 | 19 / 0 |

All ten final native logs recorded zero error, traceback, cycle, simulation-time-warning,
nested-task and force-callback markers. Other warnings remain retained; these counts do
not establish universal harmlessness or supersede warnings from earlier workflows.
All ten owned PIDs were absent after normal managed SIGINT. The first headless batch's
stop/PID timing failure occurred after the 6.1 effects passed; later absence was confirmed
and the wrapper gained a bounded wait. The first 6.2 desktop click was refused by schema
for missing dimensions before input; its corrected guarded call succeeded. Both original
harness failures remain in the evidence.

The [reproduction guide](evidence/project-scene/CAMERA-CLIPPING-REPRODUCTION.md) records
actual harness commands, deployment-path adaptations, diagnostic bootstrap and separate
host validation dependencies. Runs used Ubuntu 24.04.5 / RTX 5080 / driver 580.178.04,
four prepared older installations and the previously documented locked local 6.2 environment.
No vendor source was modified; this is not full-installer or clean-machine qualification.
The [unpublished draft bundle](evidence/project-scene/RELEASE-ASSETS.md) retains 1,028
original hashes and 906 checksums for 907 archive members, including four explicitly
reviewed fixture PNGs and the harness. Other images retain scoped review records/hashes.

No new independent-agent task was performed. This result does not resolve Newton crashes,
NaNs or contact instability, ROS/Fabric black images, or the original `20f2953` 108-step
stall. Earlier runtime identities, final per-tool/source/backend reconciliation, and
setup/Lab dependency and locked-replay limitations remain separate and open.

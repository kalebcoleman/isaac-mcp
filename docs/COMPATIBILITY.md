# Compatibility

The full installer is pinned to **Isaac Sim 6.2.0.0 and Python 3.12**. It does not automatically select a
compatible environment for other 6.x releases. The separate MCP host package requires Python 3.10 or newer;
its interpreter can be different from the simulator's.

The table records what this repository has actually exercised, not a guarantee for every machine or feature.
The 6.0 and 6.1 probes used separate, locally resolved environments rather than this project's locked 6.2
environment; a clean-install recipe for those builds has not been qualified by this repository. For selection,
prerequisites, and what is optional, see [Linux setup by build](LINUX-SETUP.md#choose-a-simulator-build).
See the [test details](evidence/compatibility-2026-10-31.md) and [verification guide](VERIFICATION.md) for the
environment, results and omissions. The table preserves the historical smoke scope; newer
[headless](#6001-headless-follow-up-2026-11-01) and
[windowed](#6001-windowed-follow-up-2026-11-02) 6.0.0.1 observations are recorded separately below.
Final qualification of the combined candidate across the requested builds and modes remains pending.

| Isaac Sim Python package | Project status | Evidence and limits |
|---|---|---|
| `6.2.0.0` | Pinned baseline; selected workflows tested | The 6.2 twin, bridge, desktop controls and managed sessions were exercised on Ubuntu 24.04.5 / RTX 5080. Historical Isaac Lab tests used Lab 3.0.0 with this Sim version. See the [desktop evidence](evidence/desktop-control-2026-10-31.md) and [Lab guide](TOOLS-ISAACLAB.md#verified-vs-version-and-platform-limitations). |
| `6.1.0.0` | Partial test; further validation needed | Headless and windowed 20-check smoke runs completed, including a decodable recording. The panel was docked and simulated object and camera transforms were received. Kit logged asynchronous task errors and a viewport exception on reset. An earlier Quick Start during startup did not complete. Lab and physical hardware were not tested on 6.1. |
| `6.0.1.0` | Partial test; further validation needed | The 20-check smoke completed in headless and windowed sessions, including decodable recordings. The optional panel was docked, and simulated object and camera transform messages were received. Kit logged asynchronous task errors; Lab and physical hardware were not tested. |
| `6.0.0.1` | Partial test; further validation needed | Headless and windowed 20-check smoke runs completed, including a decodable recording. A fresh window had the panel docked, and simulated object and camera transforms were received. Kit logged asynchronous task errors; a panel was undocked after one extension reload. Lab and physical hardware were not tested. |
| `6.0.0.0` | Partial test; further validation needed | The initial object-pose graph failed because this release uses an older ROS publisher. With the compatibility fix, headless and windowed 20-check smoke runs passed, the optional panel was docked, and simulated object and camera transforms were received. Kit logged asynchronous task errors; Lab and physical hardware were not tested. |
| Other 6.x releases | Not tested | A shared major version does not establish that this bridge, the twin or Lab jobs work on a new release. |
| `5.1.0.0` | Experimental; not supported by the default launcher | The bridge and twin registered, but Quick Start failed with the default 6.2 ROS library path. With a temporary 5.1 ROS library path, Quick Start and stop returned success and the panel was checked, but Kit logged asynchronous task errors. The complete workflow was not verified. |
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

The current 39-tool pack has its own [evidence and version matrix](evidence/project-scene/README.md),
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
The [per-release matrix](evidence/project-scene/RUNS.md) records all37 tools on each installed
PhysX release, independent agents, repeated captures, known failures and later retests. Newton trials
are not a claim that every backend/asset/edge case passed.

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

Owner decision2026-11-01: PR11 is a first slice. Its measured support does not include isolated
untrusted loading, authenticated remote upload, named presets or full fork parity. Those gates
remain open. New native failures and harness failures are retained in the draft evidence release.
The latest UR5e World adapter initializes asynchronously even when `/physicsScene` already exists;
it restores the temporary terminal-launch flag before awaiting and avoids nested Kit updates.
See the final validation attachment for the6.2 verify/smoke/motion and existing-scene checks.

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
and camera/ROS-save summaries. The private reproduction harness, asset outputs and
curated release index are **not yet published**; this summary is not a publicly
reproducible bundle. Windowed observations are recorded below; the complete five-build/mode
matrix remains pending. Later documentation or bridge-lifecycle commits do not change this
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
[scene tools](TOOLS-SCENE.md). Fresh live qualification of that fix is pending; it does
not retroactively change this `4499bf7` run or establish all-version readiness.

Provenance: inspected `public-facts-final6001w-4499.json`, timestamp
`2026-11-02T00:04:54.401402+00:00`, SHA-256
`34e507ee522bee118e568c612483657ac2f3e588323a150e3db65f61cafc834b`;
all nine referenced source-record hashes were verified. The compact handoff and parent
review preserve the original observations. The reproduction harness, asset outputs and
curated release index remain unpublished. This is an existing-installation, simulated
ROS check, not physical hardware or clean-install validation; remaining builds/modes
and the new viewport fix still need their own qualification.

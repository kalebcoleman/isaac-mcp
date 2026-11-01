# Compatibility

The full installer is pinned to **Isaac Sim 6.2.0.0 and Python 3.12**. It does not automatically select a
compatible environment for other 6.x releases. The separate MCP host package requires Python 3.10 or newer;
its interpreter can be different from the simulator's.

The table records what this repository has actually exercised, not a guarantee for every machine or feature.
The 6.0 and 6.1 probes used separate, locally resolved environments rather than this project's locked 6.2
environment. See the [test details](evidence/compatibility-2026-10-31.md) and
[verification guide](VERIFICATION.md) for the environment, results and omissions.

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
objects, call `setup_pose_publisher` and `setup_camera_tf_graph`, then play the scene. In a separate terminal, from
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

The new 37-tool pack has its own [evidence and version matrix](evidence/project-scene/README.md),
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

The6.0.0.0 fresh-process saved-scene test remains partial: geometry/resting body state and new
sensor creation work, but camera readout stays `NOT_READY` after extended warm-up. Removing
serialized native render graphs fixed a creation error, not this remaining readout edge.

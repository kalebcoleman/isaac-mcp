# Compatibility

The full installer is pinned to **Isaac Sim 6.2.0.0 and Python 3.12**. The MCP host
requires Python 3.10 or newer and can use a different interpreter. The results below
are scoped observations, not guarantees for every machine, asset, tool or configuration.
For installation choices see [Linux setup](LINUX-SETUP.md#choose-a-simulator-build).

## Release backend scope

**PhysX is the release target; Newton is experimental.** Verify the selected engine and do
not silently change the backend of an existing scene. Narrow 6.2 Newton twin experiments used
MuJoCo Warp on CPU; they do not qualify all 6.2, generic-scene or Lab workflows. Earlier
Newton twin tests produced native crashes, invalid state, rendering and contact failures. No
reliable collider workaround was shipped.

A 6.0.1.0 windowed PhysX step stalled at `20f2953`. Two later scoped replays did not
reproduce it, but its cause remains unknown. If a call stops returning, follow the
[unknown-outcome and recovery guidance](TOOLS-SCENE.md#bounds-and-security-limits); a client
timeout does not cancel native execution. Clean-machine/full-installer coverage and fresh-Kit
camera reuse remain separate limits.

## Simulator builds

The 6.0 and 6.1 probes used separately resolved environments, not this project's 6.2 lock.
The [PR 70 retest](evidence/pr70-build-retest-2026-11-04.md) records additional short checks. The 20-check smoke is scoped: it does not exercise real
hardware or establish that every viewport, ROS, desktop or Lab configuration works.
A listed tool does not establish that a compatible bridge is running.

| Isaac Sim Python package | Project status | Evidence and limits |
|---|---|---|
| `6.2.0.0` | **Claimed, with Isaac Lab 3.0.0.** Pinned baseline; selected workflows tested | The 6.2 twin, bridge, desktop controls and managed sessions were exercised on Ubuntu 24.04.5 / RTX 5080. The supported import families, the warehouse and Jenga scenes, and Lab train, resume, compare, eval and play were run on it (2026-11-05). See the [desktop evidence](evidence/desktop-control-2026-10-31.md) and [Lab guide](TOOLS-ISAACLAB.md#verified-vs-version-and-platform-limitations). |
| `6.1.0.0` | Best effort; partial test | Headless and windowed 20-check smoke runs completed, including a decodable recording. The panel was docked and simulated object and camera transforms were received. Kit logged asynchronous task errors and a viewport exception on reset. An earlier Quick Start during startup did not complete. Lab and physical hardware were not tested on 6.1. |
| `6.0.1.0` | Best effort; partial test | The 20-check smoke completed in headless and windowed sessions, including decodable recordings. The optional panel was docked, and simulated object and camera transform messages were received. Kit logged asynchronous task errors; Lab and physical hardware were not tested. |
| `6.0.0.1` | Best effort; partial test | Headless and windowed 20-check smoke runs completed, including a decodable recording. A fresh window had the panel docked, and simulated object and camera transforms were received. Kit logged asynchronous task errors; a panel was undocked after one extension reload. Lab and physical hardware were not tested. |
| `6.0.0.0` | Best effort; partial test | The initial object-pose graph failed because this release uses an older ROS publisher. With the compatibility fix, headless and windowed 20-check smoke runs passed, the optional panel was docked, and simulated object and camera transforms were received. Kit logged asynchronous task errors; Lab and physical hardware were not tested. |
| Other 6.x releases | Not tested | A shared major version does not establish that this bridge, the twin or Lab jobs work on a new release. |
| `5.1.0.0` | **Claimed, with Isaac Lab 2.3.2.** Not auto-detected, so name its interpreter with `ISAAC_MCP_PYTHON`. `run.sh` finds its bundled ROS libraries and `sim-tools` has adapters for its older APIs | Measured on 2026-11-05: `assets__import_path` for USD, URDF, xacro, SDF, MuJoCo XML, glTF and ROS maps through Isaac Sim's older importers; the warehouse quick start, with a Carter driving 15.7 m; Lab train, resume, compare, eval and play. Limits: a later MJCF sweep returned `MJCF_PHYSICS` for an SO-101 model and lost the bridge connection importing a Panda; those failures remain unresolved. URDF export writes an empty robot on this build, so it is refused with a reason; `sim__lidar__create` needs `config`; USD scenes keep their own units, so check them. Earlier: the [PR 70 retest](evidence/pr70-build-retest-2026-11-04.md) and one project scene, where most generic tools worked and `verify.sh` passed. The twin's full workflow was not verified here. |
| Earlier releases | Not tested | No compatibility claim. |

The 6.2 lock selects NumPy 2.3.1, matching the inspected `isaacsim-kernel` 6.2.0.0 wheel.
Existing environments made from an older lock may differ; inspect them before changing anything.

## Isaac Lab, ROS and platforms

- Historical short training and windowed play checks used Isaac Lab **3.0.0 / Isaac Sim 6.2.0.0** on the machines
  described in the [Lab guide](TOOLS-ISAACLAB.md#verified-vs-version-and-platform-limitations). Those checks do not
  establish long-running training, every task, or Lab behavior on the older Sim probes above.
- The two claimed Lab pairs are **3.0.0 with Sim 6.2** and **2.3.2 with Sim 5.1**; `isaaclab__install_info` reports `tested: true` for them and false for any other. On both, a real Cartpole job went through train, resume, compare, eval and play with `rsl_rl` through the tools; 2.3.2 also ran tiny jobs with `rl_games`, `skrl` and `sb3`. That is not proof of every task or of long training. Task ids differ between them (`Isaac-Cartpole-v0` on 2.3.2, `Isaac-Cartpole` on 3.0.0), and 2.3.2 needs `rl_library` because its scripts are per library. Lab 3 tasks can default to Newton; pass `physics="physx"` to the MCP job tool to select PhysX where the task has that preset. See the [Lab guide](TOOLS-ISAACLAB.md#isaaclab__train) for task-specific limits.
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

An existing separate environment can be selected with `ISAAC_MCP_PYTHON=/absolute/path/to/sim-env/bin/python` for
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

## Project assets and generic scenes

Trusted project USD import and workstation preparation were exercised on the five 6.x
builds above in headless and windowed sessions, using a limited set of projects on one Linux
machine. Initial captures from newly created cameras required retries in those tests. These
results do not establish support for every asset, sensor or backend. Trusted USDZ support is
bounded, but native parsers are not an archive sandbox. Isolated untrusted import and
authenticated remote upload remain unfinished. See [scene tools](TOOLS-SCENE.md) and
[asset import](ASSET-IMPORT.md) for current contracts.

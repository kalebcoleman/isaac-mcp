# NVIDIA's official Isaac Lab and Isaac Sim skills

NVIDIA ships Agent Skills inside its own repositories. They carry the domain knowledge this skill
leaves out (environment design, rewards, assets, sensors, migration). Use them for *what to build*
and this skill for *how to run it through twinsmith*.

- Isaac Lab: [`skills/`](https://github.com/isaac-sim/IsaacLab/tree/main/skills) in
  `isaac-sim/IsaacLab` (BSD-3-Clause). Catalog: `skills/README.md`.
- Isaac Sim: [`skills/`](https://github.com/isaac-sim/IsaacSim/tree/release/6.3/skills) in
  `isaac-sim/IsaacSim` (Apache-2.0). Catalog: `skills/SKILLS.md`.

Names below are each skill's frontmatter `name`, as read on 2026-10-31 (IsaacLab `7529e35`,
IsaacSim `5ef464e`). The skills are marked experimental upstream and change often; trust the
upstream frontmatter over this table.

## Getting them

Both repositories expose their skills through `.agents/skills/` (Codex, OpenCode, Antigravity) and
`.claude/skills/` (Claude Code), so an agent started inside an Isaac Lab or Isaac Sim checkout
finds them automatically. To use them from another project, symlink the skill directories you
want into your user skills directory. Do not copy them flat: they link to files elsewhere in their
repository. If they are not installed, read the `SKILL.md` on GitHub instead.

## Isaac Lab skills and the matching twinsmith tools

| Need | Official skill | twinsmith side |
|---|---|---|
| Train, resume or configure RL | `isaaclab-training-rl-agents` (`skills/user/train-rl-agents`) | `isaaclab__train`, `isaaclab__runs_status`, `isaaclab__runs_tail`, `isaaclab__checkpoints_list` |
| Reward curves look wrong, checkpoints mismatch | `isaaclab-debugging-rl-training` (`skills/user/debug-rl-training`) | `isaaclab__metrics_summary`, `isaaclab__tensorboard_tail`, `isaaclab__eval` |
| Build or register a new task | `isaaclab-building-environments` (`skills/user/create-environments`) | `isaaclab__envs_list` with `ISAACLAB_TASK_MODULES`, then a smoke `isaaclab__train` |
| Manipulation task phases (reach, grasp, lift, place) | `isaaclab-planning-manipulation-tasks` (`skills/user/plan-manipulation-tasks`) | `isaaclab__play` + `screenshot(window_id=...)` per phase |
| Wrong initial wrist/gripper pose | `isaaclab-diagnosing-joint-poses` (`skills/user/diagnose-joint-poses`) | `isaaclab__play` window capture; `run_python` for articulation state in a live Sim |
| Sensors and actuators | `isaaclab-using-sensors-actuators` (`skills/user/use-sensors-actuators`) | smoke `isaaclab__train` with small `num_envs` (cameras cost GPU memory) |
| Domain randomization | `isaaclab-randomizing-with-events` (`skills/user/domain-randomization-events`) | `extra_args` Hydra overrides for quick variants |
| PhysX vs Newton | `isaaclab-selecting-backends`, `isaaclab-using-presets`, `isaaclab-preparing-assets-for-newton`, `isaaclab-transferring-policies-sim-to-sim` | `isaaclab__eval` of one checkpoint per backend |
| Multi-GPU | `isaaclab-training-multi-gpu` (`skills/user/train-multi-gpu`) | not covered: the job tools launch single-GPU `train.py` |
| Install or broken setup | `isaaclab-installing-isaac-lab`, `isaaclab-setup-troubleshooting` | `isaaclab__install_info` to confirm the result |
| Porting old code | `isaaclab-migrating-2x-to-3x`, `isaaclab-migrating-from-isaac-gym`, `isaaclab-converting-direct-to-manager` | `isaaclab__envs_list` + smoke run to validate |

## Isaac Sim skills and the matching twinsmith tools

| Need | Official skill | twinsmith side |
|---|---|---|
| Plan a demo or deliverable | `isaac-sim-workflow`, then `isaac-sim-orchestrator` | `status`, `screenshot` to report progress |
| Drive a running Sim live | `isaac-sim-remote` (uses Kit's code-editor server on 8226) | Same job through `sim__*` scene tools or `run_python` on the twinsmith bridge; use one, not both |
| Physics bodies, joints, materials | `physics-simulation` | `sim__physics__inspect`, `sim__body__state`, `sim__joints__inspect/state`, `sim__material__*`; `run_python` only for gaps |
| Robot import and articulations | `urdf-mjcf-to-usd-conversion`, `usd-articulation`, `physx-to-newton-conversion` | `assets__import_path` (URDF, xacro, SDF, MJCF, USD, meshes), then `sim__joints__*` and `sim__body__state` |
| Scene assembly and transforms | `usd-pipeline`, `usd-composition-architecture`, `spatial-reasoning` | `sim__stage__tree`, `sim__prim__get/find/create/set_transform`; `run_python` only for unsupported edits |
| Assets will not load | `isaac-sim-assets` | `assets__roots`/`assets__info` for approved projects, `sim__logs__tail`, `run_python` for the asset root setting |
| Cameras and sensors | `isaac-camera`, `isaac-sim-sensor`, `place-camera-aim-at`, `place-camera-max-coverage` | `sim__camera__create/capture`, `sim__lidar__*`, `screenshot` |
| Arm control | `manipulation-ik`, `motion-generation` | `sim__joints__command/state/drive`; `workcell__*` for the work cell |
| ROS 2 | `isaac-sim-ros2-bridge`, `isaac-sim-ros-workspaces` | `sim__ros2__tf_setup` |
| Navigation | `navigation-primitives`, `occupancy-map`, `isaac-sim-robot-navigation`, `mobility-gen` | `run_python` |
| Synthetic data | `data-collection-sim`, `action-and-event-data-generation` and the `actor-sdg-*` skills | `sim__camera__capture`, `sim__stage__save`, `get_file` for outputs |
| Headless or remote runs | `isaac-sim-headless-deployment`, `isaac-sim-web-viewer` | `sim__session(action="launch", headless=true)` |
| Rendering quality | `isaac-sim-rendering`, `validation-diff-gifs` | `sim__camera__capture`, `screenshot`, `get_file` |
| Hangs, crashes, slowness | `isaac-sim-troubleshooting`, `profile-isaac-sim` | `status`, `sim__logs__tail`, `screenshot`, `run_python` for `carb` settings |
| Install or upgrade | `isaac-sim-installation`, `isaac-sim-migration` | `status` reports the running app version |

## Precedence

When an official skill and this skill disagree about Isaac Lab or Isaac Sim APIs, the official
skill and the installed source win. When they disagree about how to call twinsmith tools, safety
gates or GPU guards, this skill wins. Official skills often give shell commands (`uv run isaaclab
train ...`, `./isaac-sim.sh ...`); when the matching twinsmith tool exists, use the tool so the job
is tracked and guarded, and only fall back to the shell when the user asks.

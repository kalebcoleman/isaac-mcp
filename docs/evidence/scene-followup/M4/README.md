# M4: joint control an agent can steer by name

Tools: extended `sim__joints__command` (`targets`, `velocities`, `clamp`), new `sim__joints__gains`,
`sim__joints__move` and `sim__joints__teleport`, all on the backend tensor views already used by
`sim__joints__state`. Runtime values only; nothing is saved.

## Environment

| | 6.2 | 5.1 |
|---|---|---|
| Isaac Sim | 6.2.0.0 | 5.1.0 |
| Backend / device | PhysX / CUDA | PhysX / CUDA |
| Kit | headless, port 18901, stopped by PID after the run | same |
| Robot | `sim__quick_start` public `ur5e` and `franka` | same |
| ROS | domain 111 | same |

Command: `ISAAC_MCP_BUILTINS=1 ISAAC_MCP_LOCAL_TOOLS=all uv run --isolated --python 3.12
--with-editable . --extra scene-test python ../scripts/validate_scene_m4_joints.py --port 18901
--output <tmp>/m4-<robot>-<build>.json --robot <robot>`.

## Results

| Check | 6.2 ur5e | 5.1 ur5e | 6.2 franka | 5.1 franka |
|---|---|---|---|---|
| `sim__joints__command` by name returns applied targets and previous values | pass | pass | pass | pass |
| Unknown name refused with `UNKNOWN_DOF` and the valid names | pass | pass | pass | pass |
| Out-of-limit target refused (`JOINT_LIMIT`), then clamped and reported with `clamp=true` | pass | pass | pass | pass |
| Velocity targets acknowledged with per-name previous values | pass | pass | pass | pass |
| `sim__joints__gains` reads, changes stiffness/damping/max_effort and reads them back | pass | pass | pass | pass |
| `sim__joints__move` reaches 0.5 rad and returns to the start pose, `settled=true`, every error ≤ 0.01 rad | pass | pass | pass | pass |
| A joint pressed into the floor reports `settled=false` (real contact, `shoulder_lift_joint` / `panda_joint1`) instead of success | pass (error −0.494 rad after 400 steps) | pass (same) | pass (error 5.02 rad; the joint was held far short) | pass (error 0.013 rad) |
| A zero-stiffness, 1e-6 max-effort drive reports `settled=false` with `blocked` and per-joint errors | pass (60 steps) | pass | pass | pass |
| `sim__joints__teleport` sets a state directly and reports before/after with velocities zeroed | pass | pass | pass | pass |
| Timeline stopped and `sim__stage__info.stopped=true` after the run | pass | pass | pass | pass |

Both robots began in their all-zero pose lying on the floor, so the first direction of travel
(`+0.5 rad` on the first three joints) is genuinely blocked for the arm; the run records that refusal
and then moves `-0.5 rad` and back, where every joint settles. The documentation already warned that
a low arm can be held by the ground; this run confirms the tool says so rather than claiming success.

## Refusal codes exercised

`UNKNOWN_DOF` (valid names in the envelope), `JOINT_LIMIT` (limits and units in the envelope, with the
`clamp=true` way forward), `PHYSICS_NOT_READY` (move while stopped or playing), `PHYSICS_FORMAT`
(unexpected limit shape; unit-tested), `PHYSICS_API_UNAVAILABLE` (a build with neither gain API;
unit-tested).

## Not verified

6.0.0.0, 6.0.0.1, 6.0.1.0 and 6.1.0.0 (not installed here) and Newton (not run for this milestone).
No video was captured: the proof is the numeric settle result and per-joint error.

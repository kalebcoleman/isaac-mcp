# Control, explained end to end

Audience: a robotics/AI engineering student who will present this work. You know what a
joint, a PID loop and a neural network are. You have not necessarily seen how *this* repo
drives a robot, and the two control paths in this project are easy to mix up.

Everything below is read from the code, not from memory. Every claim about this repo ends
with `file:line`. Where I could not verify something from the files, the text says
**unverified** and section 8 collects those items in one list.

## How to read the citations

| Prefix | What it is |
|---|---|
| `exts/ur5e-dt/...` | the digital-twin Kit extension in **this** repo (`isaac-mcp`) |
| `scripts/`, `config/`, `docs/` | this repo (`isaac-mcp`) |
| `.venv/lib/python3.12/site-packages/...` | the installed Isaac Sim 6.2 / USD runtime, i.e. NVIDIA's code and docs |
| `learning6/...` | the **other** repo, `~/Desktop/ur5e-digital-twin` (Isaac Lab 3 project) |
| `checkpoints/...` | the exported twin scene in the **other** repo (`ur5e-digital-twin/checkpoints`) |

Two unit traps to keep in mind the whole way through, because they are the source of most
confusion in this stack:

1. **ROS is radians, USD is degrees.** Every joint angle that crosses between them is
   converted (section 2.4).
2. **"Stiffness = 100" means two different things** depending on whether the number is
   handed to Isaac Sim's Python API (per radian) or written into a USD drive attribute
   (per degree). Off by 57.3x if you mix them up (section 2.4).

---

# 1. The big picture: two separate control worlds

This project contains two complete, independent ways to make a UR5e arm move. They share
CAD, joint names, and even the same exported USD file, but they answer different questions
and are wired in opposite directions.

## 1.1 World A — the digital twin mirrors a robot

The extension `exts/ur5e-dt/` builds a UR5e + OnRobot RG2 cell in Isaac Sim. It does **not**
decide where the robot should go. It subscribes to `/joint_states` and copies the joint
angles it hears into the simulated arm.

```
   WHO PRODUCES THE SETPOINT                      WHAT MOVES THE JOINTS
   ---------------------------                    ---------------------

   operator / AI client / test script
            |
            |  (goal in joint space; the consumer computes it, not this repo - see 8.10)
            v
   +--------------------------+
   | ros-mcp-server or CLI    |     FollowJointTrajectory (action, joint positions in rad)
   |  (out of this repo)      |
   +------------+-------------+
                |
                v
   +--------------------------+      +-------------------------------+
   | scaled_joint_trajectory_ | ---> | ur_robot_driver               |
   | controller               |      |  backend = FAKE hardware      |
   | (inside the UR driver)   |      |      (launch_ur_fake.sh)  OR  |
   +--------------------------+      |  backend = URSim container    |
                                     |      (sim_bringup.sh)     OR  |
                                     |  backend = real UR5e          |
                                     |      (sim_bringup.sh real)    |
                                     +---------------+---------------+
                                                     |
                                   /joint_states (sensor_msgs/JointState, rad)
                                   publisher = joint_state_broadcaster
                                                     |
                    +================================v==========================+
                    |  ISAAC SIM (the twin)                                     |
                    |                                                           |
                    |  /Graph/ActionGraph_UR5e                                  |
                    |    OnPlaybackTick --> ROS2SubscribeJointState             |
                    |       |                    |                              |
                    |       |        positionCommand (rad, double[6])           |
                    |       |        velocityCommand (rad/s)                   |
                    |       |        effortCommand   (N.m)                     |
                    |       v                    v                              |
                    |  IsaacArticulationController  (targetPrim /World/UR5e)    |
                    |       |  set_dof_position_targets(...)                   |
                    |       v                                                   |
                    |  PhysX articulation: revolute joint with an angular       |
                    |  DriveAPI spring: tau = k(q* - q) + c(qd* - qd),          |
                    |  clipped to |tau| <= 200 N.m                              |
                    |       |                                                   |
                    |       v                                                   |
                    |  rigid-body integration at 120 Hz  ==>  the arm visibly   |
                    |  follows the real robot                                   |
                    +===========================================================+
```

The graph is built in `exts/ur5e-dt/ur5e_dt/extension.py:3011-3074`:

- nodes: `OnPlaybackTick`, `ROS2Context`, `IsaacReadSimulationTime`,
  `ROS2SubscribeJointState`, `ROS2PublishClock`, `IsaacArticulationController`
  (`extension.py:3042-3048`);
- topic is literally `/joint_states`, namespace empty (`extension.py:3053-3054`);
- target is the UR5e prim (`extension.py:3058`);
- the three command outputs of the subscriber are wired straight into the controller
  (`extension.py:3067-3070`).

| Question | Answer for World A |
|---|---|
| Who produces the setpoint? | A ROS 2 joint-trajectory controller inside `ur_robot_driver`, i.e. a real or faked UR control stack. The twin produces nothing. |
| Message / format | `sensor_msgs/JointState` on `/joint_states`: `name[]`, `position[]` (rad), optionally `velocity[]` (rad/s), `effort[]` (N·m) (`extension.py:3067-3070`) |
| Rate | every graph tick for the controller; the topic rate is whatever the driver publishes (**unverified**, section 8.2). The graph ticks once per Kit app update, and the app's minimum frame rate is set to 60 (`extension.py:2395-2398`), so expect ticks near or above 60 Hz, not exactly 60 (**unverified** in practice) |
| Where is it consumed | `IsaacArticulationController` → the UR5e articulation (`extension.py:3058`) |
| What actually moves the joints | the PhysX joint drive spring plus the 200 N·m cap (`extension.py:701-703`, written at `extension.py:2962-2965`) |
| Physics rate | 120 steps/s, GPU dynamics off, CCD off (`extension.py:2403-2405`) |

## 1.2 World B — Isaac Lab learns a policy

`learning6/ur5e_lab/` (in `ur5e-digital-twin`) is a completely separate program. It starts
its own Isaac Lab app, clones the same cell USD 128 times (`reach_env.py:92`), and lets a
PPO network move the arm. Here the simulation *is* the authority: nothing outside the sim
knows where the arm is.

```
   WHO PRODUCES THE SETPOINT                      WHAT MOVES THE JOINTS
   ---------------------------                    ---------------------

   PPO actor network  (rl_games, MLP 256-128-64, tanh-free Gaussian)
            |
            |  action a in R^6, each component clipped to [-1, 1]
            v
   +--------------------------------------------------------------+
   | UR5eReachEnv._pre_physics_step                               |
   |   q_target <- clamp(q_target + 0.05 * a, soft_lo, soft_hi)   |
   |   (absolute joint targets, integrated from the previous one) |
   +----------------------------+---------------------------------+
                                |
                                |  once per control step = every 4 physics steps
                                v
   +--------------------------------------------------------------+
   | robot.actuators.target_command.set_position_index(q_target)  |
   |   (ImplicitActuator -> PhysX DOF position target, rad)       |
   +----------------------------+---------------------------------+
                                |
                                v
   +--------------------------------------------------------------+
   | PhysX: SAME kind of angular drive spring as World A          |
   |   tau = k(q* - q) + c(qd* - qd), |tau| <= 200 N.m            |
   |   120 Hz physics, TGS solver, 128-1024 parallel copies       |
   +----------------------------+---------------------------------+
                                |
                                v
   observation (21 numbers) --> PPO --> next action
```

Sources: action path `learning6/ur5e_lab/reach_env.py:165-170`; gains left at the USD
values `reach_env.py:29`; physics settings `reach_env.py:71-91`; parallel envs
`reach_env.py:92`.

| Question | Answer for World B |
|---|---|
| Who produces the setpoint? | the PPO policy, 30 times per simulated second |
| Message / format | a `torch.Tensor` of 6 floats in [-1, 1]; no message bus at all |
| Rate | control step 30 Hz (dt 1/120 × decimation 4, `reach_env.py:66,72`); physics 120 Hz |
| Where is it consumed | `UR5eReachEnv._apply_action` → the articulation's position target (`reach_env.py:169-170`) |
| What actually moves the joints | the same PhysX drive spring, same 100/10/200 gains, because the env references the twin's exported USD |
| Number of robots | 128 by default (`reach_env.py:92`), 1024 in the reported long run (`docs/HANDOFF.md:79,84`) |

## 1.3 Why two worlds instead of one

Because the two worlds disagree about who is in charge.

In World A the real robot is the authority and the sim is a *renderer of truth*: the twin
exists so that perception code, grasp planners and MCP tools can be developed against
something that looks and measures like the lab cell, without touching the lab robot. The
extension says this in its own words: it is a "POSITION-MIRRORING digital twin" whose joints
"are driven from `/joint_states` … published by the real (or fake) UR ros2_control driver"
(`extension.py:2974-2977`).

In World B the sim is the authority: there are up to 1024 copies of the robot, no real hardware,
and the only ground truth is PhysX.

Trying to merge them breaks on one specific mechanism. In World A an OmniGraph node holds
the articulation every tick, so anything else that writes joint data is overwritten:

> "it does not work on this OmniGraph-driven twin — the ArticulationController node rewrites
> the articulation action every physics step and zeros any joint efforts we apply (verified:
> applied_joint_efforts stays [0,…])" — `extension.py:2983-2987`

That is why the twin cannot host a torque controller or an RL policy in place: a policy that
writes torques (or even position targets through the Python API) is fighting a node that
re-issues the ROS command on every tick. The twin's workaround for *its* problem is to
switch gravity off on the robot chain rather than to add gravity feed-forward
(`extension.py:2971-2999`); World B never has that problem because nothing else writes to
the articulation.

## 1.4 So what is a "digital twin" here?

Not a simulator of the robot's dynamics. A **pose mirror plus a physics playground around
it**, in three layers:

1. **The robot is kinematic-ish.** Gravity is disabled on all 16 rigid bodies of the arm,
   Quick-Changer and gripper (`extension.py:2971-3010`; the flag lands in the USD as
   `bool physxRigidBody:disableGravity = 1`, e.g.
   `checkpoints/ur5e-dt-workstation-person-view-portable-20261030T015724Z.usda:277`). The
   measured effect of that change: joint sag 4.06 → 0.37 mrad, tool droop 3.4 → 0.31 mm
   (`extension.py:2990-2992`).
2. **Manipulated objects are real.** Gravity stays on for `/World/Objects/*`
   (`extension.py:2990-2991`), so parts fall, get held and settle with real contact physics.
3. **The scene is mirrored both ways.** Objects can be pushed from sim to a ROS topic
   (`objects_poses_sim`, `extension.py:5429-5530`) and pulled back from a real perception
   topic (`sync_real_poses` / `objects_poses_real`, `extension.py:508-511`).

That split is the honest summary: **the arm is mirrored, the world is simulated.**

---

# 2. Path A in depth

## 2.1 What is actually in the graph

`setup_action_graph` (`extension.py:3011`) refuses to run without `/World/UR5e`
(`extension.py:3018-3022`), enables the ROS 2 bridge extensions
(`extension.py:3024-3027`), and creates one graph at `/Graph/ActionGraph_UR5e`
(`extension.py:3029`). The ROS context takes its domain id from the environment
(`extension.py:3051-3052`), which is why `config/ros_dds.env` must be sourced by every
process (that file explains the failure mode: a DDS mismatch makes messages "publish into
the void", `config/ros_dds.env:6-30`).

Nothing in the graph publishes the robot's joint state back out. The twin is a **sink** for
`/joint_states`. (It publishes `/clock`, camera images, `/tf`, object poses, a force/torque
wrench, and the gripper's own joint state — but not the arm's.)

## 2.2 The PD drive, exactly

A USD joint drive is a spring-damper with a force clamp. The schema says it in one line:

> "Each drive is an implicit force-limited damped spring:
> Force or acceleration = stiffness * (targetPosition - position) + damping *
> (targetVelocity - velocity)" — `usdPhysics/schema.usda:800-803`, in
> `.venv/lib/python3.12/site-packages/usd_exchange.libs/usd/usdPhysics/resources/usdPhysics/`

For a revolute joint that becomes, in SI units:

```
tau  =  k * (q_target - q)  +  c * (qd_target - qd)          [N.m]
|tau| <=  maxForce                                            (saturation)
```

with `q`, `q_target` in rad, `qd` in rad/s, `k` in N·m/rad, `c` in N·m·s/rad. Then the
physics step integrates `tau` into the link accelerations. There is no integral term: this
is a **PD**, so any steady torque (gravity, contact force) shows up as a steady position
error `e = tau_steady / k`. That is exactly the "sag" the twin removed by disabling gravity
instead of adding the integral or a feed-forward term (`extension.py:2980-2990`).

Two consequences worth saying on a slide:

- **The drive is a filter on the command, not a copy of it.** The arm reaches `q_target`
  only asymptotically; during motion there is always a lag proportional to velocity.
- **`maxForce` is a torque limit, and it is the only torque limit in the system.** The
  policy in World B is also capped by it (section 5.3).

## 2.3 This repo's numbers

Arm (UR5e, all six joints), set once in `on_startup` and written into USD on load:

| Quantity | Value | Where |
|---|---|---|
| `maxForce` | 200 N·m | `extension.py:701`, written `extension.py:2963` |
| `stiffness` | 100 | `extension.py:702`, written `extension.py:2964` |
| `damping` | 10 | `extension.py:703`, written `extension.py:2965` |
| joints touched | `shoulder_pan_joint`, `shoulder_lift_joint`, `elbow_joint`, `wrist_1_joint`, `wrist_2_joint`, `wrist_3_joint` | `extension.py:2948-2955` |

Gripper (RG2):

| Quantity | Value | Where |
|---|---|---|
| actuated joint | `rg2_gripper_joint` | `extension.py:98` |
| `stiffness` | 10000 | `extension.py:116`, written `extension.py:3527` |
| `damping` | 1000 | `extension.py:117`, written `extension.py:3528` |
| `maxForce` | 1.0 N·m | `extension.py:133`, written `extension.py:3529` |
| joint velocity cap | 114.59 deg/s (= 2 rad/s) | `extension.py:137`, written `extension.py:3533` |
| mimic joints | 5 joints coupled by `PhysxMimicJointAPI` | `extension.py:99-102` |
| mimic spring | natural frequency 1000 Hz, damping ratio 1.0 | `extension.py:107-108`, written `extension.py:3546-3564` |
| stops | θ ∈ [-0.45, 1.0] rad | `extension.py:141-142` |

> Warning: `extension.py:706-708` also defines `_gripper_max_force = 1.2`,
> `_gripper_stiffness = 0.1`, `_gripper_damping = 0.05`. Those three attributes are
> **never read anywhere in the file** (grep: they appear only on the lines that define
> them). The gripper's real gains are the `RG2_ACT_*` constants above. Do not put 1.2/0.1/0.05
> on a slide. See section 9, item B.

**What the numbers mean physically.** Convert the arm drive to SI per radian (the
conversion factor is derived in 2.4):

```
k = 100 N.m/deg * (180/pi) = 5729.6 N.m/rad
c =  10 N.m.s/deg * (180/pi) =  573.0 N.m.s/rad
```

- **Stiffness: 5.7 N·m per milliradian.** A 0.01 rad (0.57°) tracking error already
  commands 57 N·m. The 200 N·m cap is only reached beyond ~35 mrad (2°) of error, so the
  drive is stiff but not saturated in normal tracking.
- **The stiffness is "infinite" in practice for a mirror.** Holding a static pose with no
  gravity needs zero torque, so the steady-state error is zero.
- **Damping/stiffness is a time constant.** `c/k = 10/100 = 0.1 s` — and that ratio is
  unit-free, so it survives the deg/rad confusion. It is the closed-loop time constant of
  an overdamped drive: the arm chases a step in ~0.1 s and then the residual decays.
- **Damping ratio.** `zeta = c / (2*sqrt(k*J))` for an effective inertia `J` about that
  joint axis. For a few kg·m² (a rough estimate for `shoulder_lift` with the arm extended —
  link masses 4.0/3.7/8.39/2.28/1.22/1.22/0.19 kg read from
  `exts/ur5e-dt/assets/robot/ur5e.usd`) `zeta ≈ 2-3`: overdamped, no overshoot. For a light
  wrist joint `zeta` is in the hundreds: *massively* overdamped, i.e. the response is
  first-order and the inertia barely matters. Either way: **no ringing**, which is what you
  want in a mirror. (The effective inertias themselves are **unverified**, section 8.5.)
- **maxForce 200 N·m is generous.** UR5e datasheet peak torques are 150 N·m (base,
  shoulder, elbow) and 28 N·m (wrists) — external numbers, **unverified here**; 200 N·m
  therefore never binds on the arm and the cap exists to keep the solver sane, not to model
  the motor.
- **Gripper: 10 000 N·m/deg is absurd as a "gain" and correct as a design.** It is
  573 000 N·m/rad, i.e. a nearly rigid position servo, deliberately paired with a
  **1.0 N·m** force cap so the jaws stall at a gentle grip force instead of crushing a part
  (`extension.py:118-133` documents that the cap was walked down from 1000 → 5 → 1.0 after
  a part was crushed through its own collision mesh). This is a force-limited position
  drive, not a stiff one: `stiffness` sets the *shape* of the approach, `maxForce` sets the
  *grip*. That distinction is a good slide.

## 2.4 Degrees or radians? (the 57.3x trap)

**Answer: USD angular drive stiffness and damping are per degree. Angular drive targets are
also in degrees. The runtime and the ROS bridge are radians.**

Three independent confirmations from the installed 6.2 tree:

1. **The USD schema itself.** In
   `.venv/.../usd_exchange.libs/usd/usdPhysics/resources/usdPhysics/schema.usda`:
   - `physics:targetPosition`: "Target value for position. Units: … if angular drive:
     degrees." (:837)
   - `physics:targetVelocity`: "… if angular drive: degrees/second." (:847)
   - `physics:damping`: "… If angular drive: mass*DIST_UNITS*DIST_UNITS/second/degrees."
     (:856)
   - `physics:stiffness`: "… if angular drive:
     mass*DIST_UNITS*DIST_UNITS/degrees/second/second." (:865)
   (`mass*DIST^2/second^2` = kg·m²/s² = N·m, so stiffness is N·m/deg and damping is
   N·m·s/deg.)
2. **Isaac Sim's own importer says it converts.** `_set_stiffness_on_joints` documents
   "For revolute joints values (Nm/rad) are converted to USD (Nm/deg)" and applies
   `value * math.pi / 180.0`
   (`.venv/.../isaacsim/exts/isaacsim.asset.importer.utils/pip_prebundle/isaacsim/asset/importer/utils/impl/asset_utils.py:583-593`,
   same statement in the `set_drive_gains` docstring at :503-504).
3. **Isaac Sim's system-identification docs warn about exactly this bug.** "USD angular
   drive stiffness is per degree, so a gain meant per radian is 57.3x too stiff"
   (`.venv/.../isaacsim/exts/isaacsim.robot_setup.sysid/docs/Overview.md:292-293`), and the
   Newton bridge converts with `stiffness *= 180/pi` because "USD angular drives are
   authored per degree" while "explicit actuators consume effective gains per radian"
   (`.../isaacsim.robot_setup.sysid/isaacsim/robot_setup/sysid/explicit_pd_actuator_compat.py:94-99`).

The ROS side is the opposite convention, and Isaac's ROS controller node says so out loud:

> "Note angular units are expressed in radians while angles in USD are expressed in degrees
> and will be adjusted accordingly by the articulation controller."
> — `.venv/.../isaacsim/exts/isaacsim.core.nodes/isaacsim/core/nodes/ogn/python/nodes/OgnIsaacArticulationController.ogn:4`

So the two numbers that look the same are not:

```
100 written into USD drive:stiffness      -> 100 N.m/deg   = 5729.6 N.m/rad
100 handed to Isaac Lab / PhysX tensors   -> 100 N.m/rad   (57.3x softer)
```

Each side is self-consistent as long as you never hand a number across the boundary without
converting. Section 9 shows one place in this repo that does cross it.

**A second way to get the same 100x surprise, found on 2026-10-30.** Writing a drive value
into USD only reaches PhysX if the attribute actually *changes*; a write of the value it
already holds notifies nobody. `load_ur5e()` wrote `100` into `physics:stiffness`, but the
asset had been authored with ~10230 N·m/deg on the elbow and PhysX had already cached that
at articulation creation, so the robot ran 100x stiffer than its own configuration while the
USD attribute read 100. Measured with `ArticulationView.get_gains()`: 586148 N·m/rad, not
5729.6. The extension now pushes the configured gains with
`set_gains(kps=..., kds=...)` and verifies them (`exts/ur5e-dt/ur5e_dt/extension.py`,
`_verify_drive_gains`). **Read back the solver's gains before you trust any step response.**

## 2.5 What the three command ports do

`IsaacArticulationController` exposes three parallel arrays and one name list
(`extension.py:3059-3070` wires all four). Implementation:
`.venv/.../isaacsim.core.nodes/isaacsim/core/nodes/ogn/python/nodes/OgnIsaacArticulationController.py:89-94`:

| Port | Meaning | What the node calls | Effect on the joint |
|---|---|---|---|
| `positionCommand` (double[], rad) | where each joint should be | `set_dof_position_targets(...)` | sets the drive's `q_target`; the PD spring does the rest |
| `velocityCommand` (double[], rad/s) | how fast each joint should move | `set_dof_velocity_targets(...)` | sets `qd_target`, i.e. the second term of the same spring |
| `effortCommand` (double[], N·m) | feed-forward torque | `set_dof_efforts(...)` | adds torque directly, *on top of* the spring |
| `jointNames` (token[]) | which joints the arrays refer to | `get_dof_indices(names)` | name → DOF index mapping; a width mismatch aborts the whole command (`...Controller.py:104-130`) |

Notes that matter in practice:

- Only `positionCommand` is load-bearing for the twin. `JointState` from `ur_robot_driver`
  carries `position[]` for sure; whether it carries `velocity[]` and `effort[]` decides
  whether the other two ports do anything (**unverified**, section 8.1). If `velocity[]` is
  filled with the measured velocity, the damping term `c(qd_target - qd)` nearly cancels
  while the robot moves, which makes the twin behave like a pure position spring during
  motion — a subtle reason tracking looks better at speed than a naive PD model predicts.
- `jointNames` comes from the *message*, and the UR joint names
  (`shoulder_pan_joint`, …) match the USD prim names (`extension.py:2948-2955`). If a
  driver ever renames joints, the controller rejects the command with an explicit
  "command mismatch" error rather than silently driving the wrong joint.
- `effortCommand` is wired, but the twin's own Python attempts to write efforts are zeroed
  by this same node every tick (`extension.py:2983-2987`). Do not read the wiring as "the
  twin supports torque control".
- The node also caches its articulation handle and tracks a `prim_path`
  (`...Controller.py:30-47`), which is why the twin has to set `targetPrim` at graph build
  time rather than per tick (`extension.py:3058`).

## 2.6 The RG2 gripper path

The gripper is a second, deliberately different control seam
(`extension.py:3077-3171`). It is **not** driven from `/joint_states`.

```
   /rg2_sim/joint_target  (std_msgs/Float64, rad, one number)
            |
            v
   +-------------------------------------------------------------+
   | /Graph/ActionGraph_RG2                                       |
   |   OnPlaybackTick -> ROS2Subscriber(Float64)                  |
   |   OnPlaybackTick -> ROS2Publisher(JointState)  /rg2_sim/joint_state
   |   OnPlaybackTick -> ROS2Publisher(Float64)     /rg2_sim/pad_gap_m
   +------------------------------+------------------------------+
                                  | the callback reads the subscriber's
                                  | output attribute at PHYSICS rate
                                  v
   omni.physx.subscribe_physics_step_events(_on_physics_step_gripper)
      theta <- clip(msg, -0.45, 1.0)
      artic.apply_action(joint_positions=[theta], joint_indices=[dof])
                                  |
                                  v
   rg2_gripper_joint drive: k=10000, c=1000, maxForce=1.0 N.m, qd <= 2 rad/s
                                  |
                                  +--> 5 mimic joints follow through
                                       PhysxMimicJointAPI (nf=1000 Hz, zeta=1)
```

- **One actuated joint, five mimics.** `rg2_gripper_joint` is the only powered revolute;
  the parallelogram linkage is completed by five joints coupled with
  `PhysxMimicJointAPI` (`extension.py:98-102`). Nothing drives the mimics: the constraint
  does (`extension.py:3083-3084`). The mimic spring was stiffened from the importer's
  25 Hz / 0.005 (≈5.3° lag, visible ring) to 1000 Hz / 1.0 (≈0.005° lag, no ring)
  (`extension.py:3540-3545`).
- **The graph has no controller node.** It only moves *data*. The arithmetic happens in a
  Python physics-step callback, 120 times per second
  (`extension.py:3163-3166`, body `extension.py:3173-3233`). That is why the docstring calls
  it a "pure JOINT-ACTUATOR seam" (`extension.py:3078-3081`): all width↔angle semantics live
  in the control package outside this repo.
- **Mimic + stiff drive = a force-limited gripper.** See 2.3: the position gain is huge and
  the torque cap is 1 N·m.
- **The published gap is measured, not computed.** `_compute_pad_gap_m`
  (`extension.py:3236-3290`) projects the innermost collision vertex of each finger pad onto
  the live closing axis. It exists because `arc(theta)` under-reports the gap when the jaws
  stall on a part (`extension.py:3228-3229`). Nice example of "measure the thing you
  actually care about".

## 2.7 Why a plain `ROS2Publisher` and not `ROS2PublishJointState`

This is a bug workaround, and the comment is unusually explicit
(`extension.py:3091-3096`):

> "the articulation-introspecting JointState publisher WEDGES the main thread in C++ at
> `timeline.play()` on this welded gripper articulation (isolated 2026-06-19 … play wedges
> iff this graph carries `ROS2PublishJointState`)".

So the gripper's `JointState` is assembled by hand: a generic `ROS2Publisher` node whose
`inputs:name` / `inputs:position` attributes the Python callback overwrites each step
(`extension.py:3154-3158`, `extension.py:3222-3226`). The `pad_gap` publisher uses the same
pattern, and so does the force/torque publisher (`extension.py:3340-3430`).

Presentation-worthy generalisation: **an introspection node that walks the articulation at
play time is a liability on a hand-assembled articulation; publish the primitives you own.**

## 2.8 The sim/real duality: what each script is for

All three backends end at the same two topics, which is what makes them interchangeable:

```
/joint_states                        (joint_state_broadcaster, sensor_msgs/JointState)
/scaled_joint_trajectory_controller  (FollowJointTrajectory action server)
```

| Path | Script | What it is | Verified where |
|---|---|---|---|
| **fake hardware** | `scripts/launch_ur_fake.sh` | `ur_robot_driver` with `use_fake_hardware:=true`: a drivetrain-free mock that publishes `/joint_states` and runs the controllers. No Docker, no URSim, no `~/ros2_ws`. Ready in seconds. | header `scripts/launch_ur_fake.sh:1-20`; launch line `:49-51`; readiness gate = `joint_state_broadcaster` active, i.e. the node that publishes `/joint_states` (`:55-62`) |
| **URSim** | `scripts/sim_bringup.sh` | Docker URSim (a virtual teach pendant) + `ur_bringup`. Five ordered steps: start URSim → stage `external_control.urp` → dashboard power-on + brake release → launch driver → load + play the program. Order matters: playing the program before the driver listens makes the URCap fail (`scripts/sim_bringup.sh:12-18`). | documented "Verified working 2026-05-27" `scripts/sim_bringup.sh:8` |
| **real UR5e** | `scripts/sim_bringup.sh` with `MODE=real` | same driver against `ROBOT_IP=192.168.1.111`; refuses to run unless `FORCE_REAL=1` because it has not been verified in the lab (`scripts/sim_bringup.sh:29-31`, `:188-201`) | |

`scripts/ur_dashboard.py` is the piece that makes URSim hands-free: it speaks the UR
dashboard wire protocol (raw TCP :29999) instead of going through ROS services, so it works
"the moment the container boots — BEFORE any ROS driver exists"
(`scripts/ur_dashboard.py:1-13`). Once the driver is up, `/joint_states` starts flowing and
the twin is live — the twin never talks to URSim or the dashboard itself.

`config/ros_dds.env` is the other half of "they are interchangeable": every process must
agree on DDS vendor *and* `ROS_LOCALHOST_ONLY`, or discovery half-works (a publisher can be
seen while an action server is not) — the file documents a reproduced failure where
`move_home` timed out while the gripper worked (`config/ros_dds.env:6-28`).

> Inconsistency to know about before you present: two places claim the domain is 7
> (`extension.py:489`, `scripts/launch_ur_fake.sh:14,26`) while `config/ros_dds.env:33`
> defaults `ROS_DOMAIN_ID=0`, and the graph honours the environment variable
> (`extension.py:3051-3052`). The env file wins. See section 9, item D.

## 2.9 The ROS 2 transform tree of the scene

Two graphs, two topics, one 180° rotation that you must not "fix".

```
 /Graph/ActionGraph_Camera                     (camera TF, extension.py:3879-3930)
   OnPlaybackTick -> IsaacComputeTransformTree(targetPrims=the D455 camera,
                                               parentPrim=/World)
                        | parentFrames, childFrames, translations, orientations
                        v
                  ROS2PublishTransformTree(topicName = tf_cam, frameId camera_link)
                        |
                        v
                    /tf_cam

 /Graph/ActionGraph_objects_poses              (object TF, extension.py:5429-5530)
   OnPlaybackTick -> IsaacComputeTransformTree(parentPrim=/World,
                                               targetPrims=every /World/Objects/...)
                        v
                  ROS2PublishTransformTree(topicName = objects_poses_sim)
                        |
                        v
                    /objects_poses_sim     (frame names: bare prim name, or parent_child on
                                            collision, or isaac:nameOverride — docs/ARCHITECTURE.md:150-160)
```

Why `IsaacComputeTransformTree` appears at all: in Isaac Sim 6.x the publisher node "no
longer resolves targetPrims itself (its legacy pose-tree path publishes nothing)"
(`extension.py:3911-3915`); the compute node produces the tree and feeds the publisher's
four data ports. Both graphs do it the same way (`extension.py:5461-5478`,
`extension.py:5492-5520`), and `docs/ARCHITECTURE.md:145-151` states the same rule.

**The 180° camera rotation.** For camera prims the compute node rotates the emitted
transform 180° about X, because USD cameras look down −Z with +Y up while ROS optical
frames expect +X forward/+Z down. It is deliberate and documented:

- behaviour and rationale: `docs/ARCHITECTURE.md:162-167`
- the instruction: "**Do not attempt to 'fix' or revert this rotation.**"
  (`docs/ARCHITECTURE.md:167`)
- reference numbers to diff against: `docs/provenance/tf-baseline-6.2.json`
- it applies only to `UsdGeomCamera` prims; `/objects_poses_sim` is unaffected
  (`docs/ARCHITECTURE.md:166`)

Practical consequence: a hand-eye calibration done on Isaac Sim 5.1 will be 180° wrong on
6.2 for the camera link, and correct for the objects. That is a one-slide gotcha.

Also relevant: the camera is attached to `wrist_3_link` with an explicit
`rotateZYX(-90, -180, 270)` (`extension.py:3822-3825`), i.e. the mount rotation is authored,
not derived.

---

# 3. Kinematics: what exists in the code, and what does not

## 3.1 The import is there and it is unused

```
exts/ur5e-dt/ur5e_dt/extension.py:590
from isaacsim.robot_motion.motion_generation import LulaKinematicsSolver, ArticulationKinematicsSolver
```

Those two names appear on that line and **nowhere else in the 6370-line file** (grep for
`LulaKinematicsSolver|ArticulationKinematicsSolver` returns line 590 only). So: **imported,
never used.** The twin does no forward kinematics, no Jacobian, no inverse kinematics. It
mirrors joint angles and reads body poses; that is all it needs.

Additional context you may want on a slide: in the installed Isaac Sim 6.2 tree the module
only exists under `extsDeprecated/`
(`.venv/.../isaacsim/extsDeprecated/isaacsim.robot_motion.motion_generation/`), i.e. the
import refers to a deprecated package. It is dead weight either way.

## 3.2 Forward kinematics (FK)

Given the six joint angles `q`, FK evaluates the chain of fixed transforms `T_i(q_i)` and
returns the tool pose:

```
T_tool(q) = T_base * T_1(q_1) * T_2(q_2) * ... * T_6(q_6) * T_tool_offset
```

6 numbers in, one pose (position + orientation) out. Unique: one `q` gives exactly one
pose. In this stack, "the tool pose" is whatever frame you name — the
reach environment instead uses the **midpoint of the two finger links**
(`reach_env.py:162-163`), which is a deliberate, slightly non-standard end effector.

## 3.3 The Jacobian

Differentiate FK with respect to `q`:

```
v = J(q) * qd          (v = 6-vector: 3 linear + 3 angular tool velocity)
```

`J` is 6×6 for a 6-DoF arm. It is the local, linear map from joint speed to tool speed, and
it changes with pose. Two things follow, and both matter for a project like this:

- `det(J) = 0` at a **singularity** (wrist aligned, arm stretched). Near it, small tool
  motions demand huge joint speeds; that is why real UR programs slow down at the edge of
  the workspace.
- The exact inverse `qd = J^-1 v` is fine away from singularities; everywhere else you use
  the damped pseudo-inverse `qd = J^T (J J^T + lambda^2 I)^-1 v`. That is the textbook
  "resolved-rate" controller.

## 3.4 Inverse kinematics (IK)

Pose in → joint angles out. Not unique: a 6-DoF arm has up to 8 solutions per reachable
pose (elbow up/down × shoulder front/back × wrist flip), plus continuum solutions at
singularities. Every real solver therefore needs a seed and tolerances, which is exactly
what the (unused) Isaac API asks for:

```
compute_inverse_kinematics(target_position, target_orientation,
                           position_tolerance, orientation_tolerance)
    -> (ArticulationAction, success)
    "using the current robot position as a warm start"
```
`.venv/.../isaacsim/extsDeprecated/isaacsim.robot_motion.motion_generation/isaacsim/robot_motion/motion_generation/articulation_kinematics_solver.py:76-111`

and FK for the same wrapper, from live joint positions, at `:53-73`. Note the wrapper
exposes FK, IK, end-effector frame selection and a joint subset — but **no Jacobian
accessor** (grep for `jacobian` in that package: no hits). If you need `J` you call Lula
directly. Also note the `LulaKinematicsSolver` behind it needs two files that this repo does
not ship: a robot description (`.json`) and a URDF (`.../kinematics.py:49-75`).

## 3.5 How an end-effector goal *would* become joint targets here

The honest answer for the current code: **it already does, outside this repo, and the twin
never sees the Cartesian goal.**

Because the only thing the twin accepts is `/joint_states` (radians, one per joint), and the
only thing the driver accepts is `FollowJointTrajectory` (also joint space), someone
upstream must convert "move the tool to (x, y, z, orientation)" into six joint angles. In
the documented flow that someone is the control consumer (`ros-mcp-server`'s `move_home` /
`move_to_grasp`), not the twin
(`extension.py:488-495`; `scripts/launch_ur_fake.sh:5-8`). Which solver it uses is
**unverified** — it lives outside this repo (section 8.10).

So the chain for a Cartesian goal is:

```
goal pose (x,y,z,R)
   -> [IK somewhere upstream: seed = current q, tolerances, joint limits]
   -> 6 joint angles
   -> FollowJointTrajectory  (or a raw /joint_states publish)
   -> scaled_joint_trajectory_controller -> driver -> /joint_states
   -> ROS2SubscribeJointState -> IsaacArticulationController -> PD drive -> the arm
```

If you *did* want IK inside the twin, the shape of the code is already there: imagine wiring
the `compute_inverse_kinematics` call above, feed its `ArticulationAction` into the same
`set_dof_position_targets` path, and skip ROS entirely — that is exactly World B's wiring
(section 4). The reason it is not done is architectural, not technical: **the twin must not
be a second authority over the arm.**

---

# 4. World B in depth: the Isaac Lab reach task

Files: `learning6/ur5e_lab/reach_env.py` (environment), `learning6/ur5e_lab/agents/rl_games_reach.yaml`
(algorithm), `learning6/ur5e_lab/train.py` (launcher), `learning6/ur5e_lab/make_cell_usd.py` (scene).

Registered as `UR5e-Cell-Reach-Direct-v0` in `learning6/ur5e_lab/__init__.py:7-15`, with the
YAML wired in as the `rl_games` config (`__init__.py:13`).

## 4.1 Where the robot comes from

`make_cell_usd()` writes a 40-line USDA that **references the twin's own exported checkpoint**
under a `/World` prim (`make_cell_usd.py:12-44`, path `:8`), disabling only the ground plane
and the workspace camera (`:33-42`). Consequences you should state out loud:

1. **The gains are the twin's gains.** The env's actuators are
   `ImplicitActuatorCfg(..., stiffness=None, damping=None)` (`reach_env.py:29`), and in Isaac
   Lab "If None, the stiffness is set to the value from the USD joint prim"
   (`learning6/IsaacLab/source/isaaclab/isaaclab/actuators/actuator_base_cfg.py:100-118`).
   Those values are 100/10/200 for the arm and 10000/1000/1.0 for the gripper
   (`checkpoints/ur5e-dt-workstation-person-view-portable-20261030T015724Z.usda:288-290`,
   `:402-404`). Same drive, same torque cap as World A.
2. **Gravity is off on the robot chain here too**, because the flag is authored in the USD
   the env references (`physxRigidBody:disableGravity = 1`, 16 occurrences in that
   checkpoint). The reach task therefore trains a policy that never has to hold the arm up.
   That is a real sim-to-real gap and worth a slide (section 5.4).
3. **The twin's collision-cooking workaround is applied** before the scene spawns
   (`reach_env.py:127-130`), because the twin's known UJITSO wedge would otherwise hang the
   run.

## 4.2 The MDP, term by term

### Observation — 21 numbers, no noise, no history

`_get_observations` (`reach_env.py:172-175`) concatenates:

| # | Term | Dim | Scaling | Why it is there |
|---|---|---|---|---|
| 1 | arm joint positions `q` | 6 | none (rad) | what the arm is doing |
| 2 | arm joint velocities `qd` | 6 | ×0.1 | keeps the number in the same range as `q` (rad/s can be 10x rad) |
| 3 | end-effector position | 3 | metres, relative to the env origin | where the hand is |
| 4 | goal position | 3 | metres, relative to the env origin | where it should be |
| 5 | goal − ee | 3 | metres | the error vector — the single most useful feature |
| | **total** | **21** | | matches `observation_space = 21` (`reach_env.py:69`) |

The whole vector is `nan_to_num`'d and clamped to ±100 before it leaves the env
(`reach_env.py:175`); observations are additionally clipped at 100 by the algorithm config
(`rl_games_reach.yaml:4`). There is **no proprioceptive delay, no sensor noise, no
observation history stack** — the policy sees a clean, current, fully observed state. That
makes it an MDP, not a POMDP, which is why a small MLP is enough.

Note what is missing: no gripper state, no object pose, no camera. This task is pure
free-space reaching, not grasping.

### Action — 6 numbers, absolute joint targets via scaled deltas

`_pre_physics_step` (`reach_env.py:165-167`) and `_apply_action` (`:169-170`):

```python
a           = clamp(nan_to_num(raw_action), -1, 1)       # 6 numbers
q_target    = clamp(q_target_prev + 0.05 * a, lo, hi)    # action_scale = 0.05 rad/unit
robot.actuators.target_command.set_position_index(q_target)
```

So the network does **not** output joint angles directly. It outputs a *velocity-like*
increment, and the environment integrates it (`0.05 rad` ≈ 2.9° per unit action per control
step, so the maximum joint speed the policy can command is `0.05 / (1/30 s) = 1.5 rad/s`).
This is the standard trick to keep actions bounded and smooth. Integration is why the
target is a state variable (`self._targets`, `reach_env.py:144`), reset on teleport
(`:222`, `:235`).

The bounds `lo/hi` are the **intersection of the USD soft joint limits and a ±(1.6, 0.9,
1.0, 1.2, 0.8, 1.5) rad box around the ready pose** (`reach_env.py:96-97`, `:135-139`). The
task is deliberately kept to a small, safe slice of the workspace.

Which controller consumes it? An `ImplicitActuator` writing the PhysX DOF position target —
"the discrete solver applies the joint drive" (`learning6/IsaacLab/source/isaaclab/isaaclab/actuators/actuator_pd.py:37-44`),
i.e. **the same PD spring as section 2.2, with the same gains and the same 200 N·m cap.**

### Timing — two rates, and the numbers that follow

| Quantity | Value | Derivation / source |
|---|---|---|
| physics `dt` | 1/120 s | `reach_env.py:72` |
| `decimation` | 4 | `reach_env.py:66` |
| control step | 1/30 s (30 Hz) | `dt × decimation` = `step_dt` (`learning6/IsaacLab/source/isaaclab/isaaclab/envs/direct_rl_env.py:291`) |
| episode length | 5.0 s | `reach_env.py:67` |
| max episode steps | 150 | `ceil(5.0 / (1/120 × 4))`, `direct_rl_env.py:306` |
| reward evaluated | once per control step | `_get_rewards` called from the env step |
| render interval | 8 physics steps | `reach_env.py:73` (`2 × decimation`) → 15 Hz rendering |
| rendered envs | 1 of N | `visible_env_indices=[0]`, `reach_env.py:88` |

So: **physics runs 4x faster than the policy.** Each control step is 4 physics steps of
30 ms total; the policy decides, then the arm is integrated 4 times with that new target
before the policy sees the result. This decimation is what makes GPU-parallel training fast:
the expensive neural network runs at 1/4 the physics rate.

### Reward — five terms and a terminal penalty

`_get_rewards` (`reach_env.py:177-185`), with `d = ||goal − ee||`:

| Term | Weight | Expression | What it does, and why the weight |
|---|---|---|---|
| `rew_lin` | −2.0 | `−2 · min(d, 2)` | the baseline "get closer" signal. Linear (not quadratic) so the gradient does not vanish far away; clamped at 2 m so a crazy reset cannot dominate |
| `rew_dist` | +2.0 | `+2 · (1 − tanh(d/0.15))` | soft "approach" shaping at the 15 cm scale: reward saturates near zero once you are inside ~15 cm, which keeps a gradient alive in the last stretch where the linear term is flat |
| `rew_fine` | +3.0 | `+3 · (1 − tanh(d/0.03))` | the precision term, scale 3 cm; this is what actually pays for the last centimetre. Largest positive weight because the task's success threshold is 2 cm (`reach_env.py:104`) |
| `rew_action` | −0.005 | `−0.005 · Σa²` | effort/jitter regulariser: penalises big commands, discourages bang-bang |
| `rew_vel` | −0.0005 | `−0.0005 · Σqd²` (clamped ±50 rad/s) | smoothness: penalises fast joints, which is also what a real controller and a real gearbox prefer |
| `rew_terminate` | −5.0 | `−5 · terminated` | one-off penalty for a *bad* termination (not for a timeout) |

With the goal reached (`d = 0`) a step pays `0 + 2 + 3 = +5`, i.e. a perfect 150-step
episode is worth ~750 before penalties. The reported `rewards/iter` of +358.6 (section 4.4)
is therefore roughly half of that shaping ceiling — not a "score out of 750", since
`rewards/iter` is rl_games' iteration-level aggregate, but a useful sanity anchor.

Design reading for the slides: this is **dense potential-based-style shaping** (two tanh
funnels at different scales plus a linear term), not a sparse success bonus. The two scales
(15 cm, 3 cm) exist because one shaping scale cannot serve both "find the goal" and "land on
it".

### Termination, timeout, and the physics sanity net

`_get_dones` (`reach_env.py:187-214`) returns `(terminated, time_out)`:

| Condition | Consequence | Source |
|---|---|---|
| `episode_length_buf >= max_episode_length − 1` | **timeout** (not a failure): episode ends at 150 steps, no `rew_terminate` | `:188` |
| non-finite `q` or `qd` | terminated, −5 | `:190` |
| `max|qd| > 30 rad/s` | terminated, −5 (`vel_limit = 30.0`) | `:99`, `:191` |
| `ee_z < TABLE_TOP_Z + 0.01` | terminated, −5: the hand must not go through the table | `:100`, `:192` |
| wrist→gripper root gap > 0.3 m | terminated, −5: the welded gripper "detached", i.e. the articulation exploded | `:193-194` |

The `detached` check is a lovely example of defensive engineering: a numerically unstable
welded child articulation does not always produce NaNs, it drifts. The env measures the
weld and calls it a failure. There is also an optional diagnostic path
(`REACH_DEBUG`, `:207-211`) and counters used by `physics_check.py` that count these events
*before* auto-reset can hide them (`:196-206`).

### Randomisation — there is almost none

| What is randomised | Range | Where |
|---|---|---|
| goal, uniformly per reset | box x ∈ [−0.45, 0.45], y ∈ [−0.60, −0.20], z ∈ [0.00, 0.30] m | `reach_env.py:102-103`, `:240` |
| initial joint pose | ready pose ± 0.15 rad on each joint | `reach_env.py:98`, `:218` |
| seed | 42 | `rl_games_reach.yaml:2` |

That is it: no mass/friction/inertia randomisation, no actuator-gain randomisation, no
latency randomisation, no observation noise. So the policy is trained for exactly one
plant. If you present sim-to-real, say this plainly — it is the biggest "would have to
change" item in section 5.

### PhysX settings, and why they are frozen

`reach_env.py:71-91`:

| Setting | Value | Why |
|---|---|---|
| `dt` | 1/120 | matches the twin's `timeStepsPerSecond=120` (`extension.py:2404`) |
| `solver_type` | 1 = **TGS** | `1 → "TGS"` mapping at `learning6/IsaacLab/source/isaaclab_physx/isaaclab_physx/physics/physx_manager.py:846`; TGS is the modern solver, better for stacked/contact-rich scenes than PGS |
| `enable_external_forces_every_iteration` | False | matches the archived 5.1 run (`reach_env.py:74-75`) |
| friction | static 0.5, dynamic 0.5, combine `average` | `:76-84` |
| restitution | 0.0 | no bounce |
| compliant contacts | 0 | rigid contacts (no soft contact model) |
| `replicate_physics` | True | one physics scene cloned per env — the reason 1024 envs are cheap |
| `num_envs` | 128 | `:92`; note the env-spacing is 3.5 m, so envs do not interact |

The constructor **hard-fails** if these are changed (`reach_env.py:122-126`), which is a
deliberate "do not silently re-tune the physics" guard — the same idea as freezing a
test's fixture.

## 4.3 The learner: PPO as configured

`learning6/ur5e_lab/agents/rl_games_reach.yaml`, interpreted line by line:

| Key | Value | Plain meaning |
|---|---|---|
| `network.mlp.units` | `[256, 128, 64]` | actor and critic share one MLP (below), tapering 21 → 256 → 128 → 64 → 6 (actor) / 1 (critic) |
| `network.separate` | `False` | one trunk for both heads ("shared" architecture) — cheaper, fine for a small MDP |
| `activation` | `elu` | smooth ReLU variant; smooth matters because PPO differentiates through it |
| `fixed_sigma: True`, `sigma_init: -1.5` | | the policy's exploration std is a **learned state-independent parameter**, initialised at `exp(-1.5) ≈ 0.22` — the model is `continuous_a2c_logstd`, and it exponentiates the stored log-std (`rl_games/algos_torch/models.py:284` in `learning6/.venv`). So exploration is a fixed-width Gaussian around the mean action; it shrinks only through the loss, not through the network |
| `gamma` | 0.99 | discount: a reward 1 s away (30 steps) is worth `0.99^30 ≈ 0.74` of its face value. Horizon ~100 steps |
| `tau` | 0.95 | **GAE λ**, the bias/variance knob for advantage estimation (0 = pure 1-step TD, 1 = pure Monte-Carlo) |
| `learning_rate` | 3e-4, adaptive | `lr_schedule: adaptive` builds `AdaptiveScheduler(kl_threshold)` (`rl_games/common/a2c_common.py:176-177`): if the measured KL between the old and new policy exceeds 2×threshold (0.02) it divides the LR by 1.5; below 0.5×threshold (0.005) it multiplies by 1.5. This is the single most useful rl_games feature — it keeps PPO from blowing up. Note the scheduler's own floor/ceiling are hardcoded at 1e-6 / 1e-2 (`rl_games/common/schedulers.py:22-31`), so this YAML's `min_lr`/`max_lr` are not used (section 9, item I) |
| `horizon_length` | 24 | steps collected per env per iteration → **batch = num_envs × 24** |
| `minibatch_size` | 3072 | the YAML value is exactly `128 × 24`, i.e. one minibatch at the default env count (`batch_size = horizon_length × num_actors`, `num_minibatches = batch_size // minibatch_size` with a divisibility assert — `rl_games/common/a2c_common.py:241-256`). The reported long run overrode it to 12288 (`docs/HANDOFF.md:79`), which for 1024 envs (`batch = 24576`) is 2 minibatches |
| `mini_epochs` | 5 | how many passes over the collected batch per iteration → 5 (or 10) gradient updates per iteration |
| `e_clip` | 0.2 | PPO's clip range |
| `entropy_coef` | 0.0 | **no entropy bonus** — exploration comes only from the fixed `sigma`. Consistent with a dense shaped reward: no need to keep exploring |
| `critic_coef` | 2.0 | value loss weighted 2x the policy loss |
| `clip_value` | True | clip the value-function target update too |
| `grad_norm` | 1.0 | global gradient-norm clip |
| `normalize_input`, `normalize_value`, `normalize_advantage` | True | running mean/std normalisation of observations, returns, advantages. Essential with mixed unit scales (rad vs m) |
| `value_bootstrap` | True | bootstrap the value at a timeout instead of treating it as terminal — correct here, because a timeout is not a failure |
| `bounds_loss_coef` | 1e-4 | small penalty keeping the Gaussian mean inside the action bounds |
| `reward_shaper.scale_value` | 1.0 | no reward rescaling |
| `max_epochs` | 300 | rl_games' epoch = one iteration of `horizon_length` steps per env |
| `save_best_after` / `save_frequency` | 50 / 50 | checkpoint cadence (iterations) |
| `score_to_win` | 100000 | effectively "never early-stop" |
| `clip_actions` | False | the **environment** clips actions (`reach_env.py:166`), so the algorithm does not need to |

### PPO in one paragraph, plus what each knob does

PPO is policy-gradient with a trust region enforced by clipping. You collect a batch of
transitions with the current policy, compute the advantage `A` of each action (how much
better than the critic's expectation it was, here via GAE with λ = 0.95), then do several
gradient passes on the surrogate objective

```
L(theta) = E[ min( r(theta) * A,  clip(r(theta), 1-eps, 1+eps) * A ) ]
      r(theta) = pi_theta(a|s) / pi_old(a|s)
```

`r` is how much more likely the new policy is to repeat that action. If an action looked
good (`A > 0`) the objective rises as `r` grows — but only until `r = 1 + eps = 1.2`; past
that, `min` picks the clipped branch and the gradient switches off. Symmetrically for bad
actions with `1 - eps = 0.8`. That is the whole trick: **one update can never move the
policy more than ~20% in probability ratio per sample**, so a lucky batch cannot destroy the
policy, and you can safely reuse the same batch for 5 epochs instead of 1.

Mapping the knobs onto that sentence:

- `e_clip = 0.2` — how far one update may move the policy. Bigger = faster, riskier.
- `mini_epochs = 5` — how many times you reuse the batch. Bigger = more sample-efficient,
  more risk of over-fitting this batch (which is what the clip and the adaptive LR exist to
  bound).
- `horizon_length = 24` — how much data one iteration considers. Short horizons make
  advantages noisier; long horizons cost memory.
- `gamma`, `tau` — what "good" means (how far ahead you look) and how you trade bias for
  variance when estimating it.
- `kl_threshold = 0.01` — the real safety belt: the LR adapts to keep the *measured* policy
  change near this value, which is why `e_clip` rarely binds in practice.
- `critic_coef = 2` — the critic must be accurate for advantages to mean anything, so it
  gets more weight.
- `normalize_*` — the observation contains radians, metres and rad/s; without normalisation
  the first layer sees wildly different scales.
- `fixed_sigma` + `entropy_coef = 0` — this run explores a fixed 0.22-wide Gaussian and
  never anneals; with the 0.05 rad action scale that is ≈0.011 rad (0.6°) of jitter per
  control step. Gentle, deliberate.

### The launcher

`learning6/ur5e_lab/train.py` does almost nothing: it imports `ur5e_lab` to register the
task (`:11-12`), then calls Isaac Lab's `train_rl_games` (`:13`) with two small subclasses
that flush and close the TensorBoard writer before Kit's fast shutdown (`:16-41`). Explicitly:
"this changes logging only, not PPO or the environment" (`train.py:5`).

## 4.4 The observed result

From `docs/HANDOFF.md:77-89` (section "Long runs and job guards on the 5080", dated
2026-10-30):

| Quantity | Value |
|---|---|
| task | `UR5e-Cell-Reach-Direct-v0` (own PhysX task) |
| `num_envs` | 1024, headless |
| `minibatch_size` | 12288 |
| iterations | 945 |
| wall clock | 970 s (≈16 min; relaunched once after a 90 s calibration to hit ~15 min) |
| throughput | ≈24.8k fps |
| peak VRAM | 3.7 GB (of 16 GB) |
| reward (`rewards/iter`) | −43.5 → **+358.6** |
| mean end-effector distance (`Episode/dist_m`) | 0.39 m → **0.15 m** |
| success rate (within 2 cm) | 0 → **0.39 final, 0.77 peak, noisy** |

Three honest readings for the presenter:

1. **It learns.** Reward climbs monotonically in trend and the distance drops by 24 cm.
2. **It is not solved.** Success is 39% at the end of a 16-minute run, and the success curve
   is noisy — it hit 77% earlier and came back down. The policy is still improving
   unstably, or the shaping reward and the 2 cm success criterion disagree about what
   "good" is (the reward is happy at 15 cm; success needs 2 cm).
3. **The reward numbers are not comparable across tasks** (`docs/HANDOFF.md:80`) — different
   shaping, different scale. Never put +358 next to +0.24 from the UR10 task on one axis.

Also note: `learning6/README.md:36-37` still says "the reach training result is not yet
verified", which is now stale (section 9, item F).

---

# 5. How the two worlds could connect (sim-to-real)

## 5.1 The goal

Run the *trained* policy from World B as the *setpoint source* for World A:

```
 policy (30 Hz)  --a_t-->  q_target = clamp(q_target + 0.05 a_t)   [rad, 6]
      |
      |  publish sensor_msgs/JointState  (name[], position[] = q_target)
      |  on /joint_states        ... or publish a FollowJointTrajectory goal
      v
 fake / URSim / real UR driver  (joint_state_broadcaster republishes as /joint_states)
      |
      v
 ROS2SubscribeJointState -> IsaacArticulationController -> same PD drive -> arm
```

The pleasant surprise: **this is nearly free, because the low-level controller is already
identical.** Both worlds hand a position target (radians) to the same USD angular drive with
the same 100/10/200 gains, in the same joint order, capped by the same 200 N·m
(section 5.3). The policy is, in control terms, a *reference generator* wrapped around a
position-controlled joint drive. Swapping "Isaac Lab's ImplicitActuator" for "ROS →
ArticulationController" changes who writes the target, not what the joint does.

## 5.2 What would have to be true

1. **The policy must run at the rate it was trained at**, or its action scaling changes
   meaning. It was trained at 30 Hz control steps with `a` interpreted as `0.05 rad` per
   step (`reach_env.py:95,167`). Run it at 60 Hz and every action is half as big in time;
   the arm moves at half speed per second. (This is the single most common sim-to-real bug
   for delta-action policies.)
2. **The observation must be reconstructible outside Isaac Lab.** `q` and `qd` are on
   `/joint_states` if the driver fills `velocity[]`; the end-effector position is *not* a
   topic — the env uses the midpoint of the two finger link origins (`reach_env.py:162-163`),
   so you would need TF for `rg2_l_finger_link` / `rg2_r_finger_link` (available: the camera
   graph publishes `/tf_cam`, and object poses come from `objects_poses_sim`; arm-links TF is
   **unverified** — no graph in the extension publishes the arm chain) or forward kinematics
   of the six angles. The goal comes from wherever the task definition lives; here it is
   sampled inside the env (`reach_env.py:240`) and drawn as a marker
   (`reach_env.py:110-113`, `:242`).
3. **The coordinate frame must match.** The env's observations are **cell-frame**
   (minus `scene.env_origins`, `reach_env.py:163`), and the goal box is expressed in that
   frame (`reach_env.py:102-103`). The twin's robot base is at the world origin with a 180°
   yaw (`extension.py:2929-2935`), so "cell frame" maps to the twin's world frame — but you
   must confirm the sign convention, not assume it.
4. **The joint order must match.** Both use the USD DOF order of the same checkpoint, so it
   should; verify with `robot.joint_names` from `physics_check.py`
   (`learning6/ur5e_lab/physics_check.py:57`) before trusting a live rollout.
5. **The action bounds must be re-applied.** The env clipped targets to the ready-pose box
   (`reach_env.py:97,135-139`); a deployed policy must be clipped the same way or it will
   command poses the task never visited.
6. **Someone must own the loop.** A 30 Hz Python node that subscribes `/joint_states`,
   builds the 21-vector, runs the MLP (a 256-128-64 MLP on 21 inputs is microseconds), and
   publishes the target. That node does not exist in either repo yet.

## 5.3 Gains: is there a mismatch? (checked, and the answer is interesting)

The brief asked me to compare the reach env's gains with the twin's. **There is no numeric
mismatch, and that near-miss is worth a slide.**

| | Twin (extension.py) | Reach env (reach_env.py) |
|---|---|---|
| arm stiffness | 100 written into USD by `extension.py:2964` | `None` → keeps USD value (`reach_env.py:29`, `actuator_base_cfg.py:107`) |
| arm damping | 10 written into USD by `extension.py:2965` | `None` → keeps USD value |
| arm max force | 200 written into USD by `extension.py:2963` | `None` → keeps USD value |
| gripper | 10000 / 1000 / 1.0 (`extension.py:116-133`) | same, via the USD (`checkpoints/...20261030T015724Z.usda:402-404`) |
| effective value in the sim | 100 N·m/deg = 5729.6 N·m/rad | **identical** — the exact same USD attributes |

So the two worlds share one plant model at the actuator level. What *differs* is everything
around it:

| Aspect | World A (twin) | World B (reach env) |
|---|---|---|
| who writes the target | OmniGraph node from ROS | PPO, 30 Hz |
| target source rate | ROS driver rate (**unverified**) | 30 Hz exactly |
| gravity on the arm | **off** (`extension.py:2971-3010`) | **off** (inherited from the same USD) |
| physics rate | 120 Hz, GPU off, CCD off (`extension.py:2403-2405`) | 120 Hz, TGS, GPU on, `replicate_physics` |
| contact material | per-prim, `max`/`min` combine (`extension.py:712-722`) | scene-wide 0.5/0.5, `average` (`reach_env.py:76-84`) |
| render | one viewport | one env of N (`reach_env.py:88`) |
| torque cap | 200 N·m | 200 N·m |

The remaining gap is not in the gains but in **who closes the loop and how fast**, plus the
plant differences in section 5.4.

## 5.4 The real gaps (the honest slide)

1. **Latency.** World B's loop is: action → 4 physics steps → observation, i.e. **33 ms,
   deterministic, zero transport**. World A's loop adds: policy compute + DDS publish +
   driver + `/joint_states` + graph tick. The twin's tick is a playback tick, with the app's
   minimum frame rate set to 60 (`extension.py:2395-2398`), and the driver adds its own
   latency. Total loop is plausibly 50-100 ms with several sources of jitter (**unverified** —
   nobody has measured it in this repo). A delta-action policy trained at a fixed 33 ms will
   oscillate at 100 ms.
2. **Unmodelled friction.** PhysX joint friction in this asset is whatever the URDF importer
   left in the joint prims; the reach env adds nothing (`reach_env.py:29`). A real UR5e has
   harmonic-drive friction, backlash and compliance. Nothing in either world models that.
3. **Gravity.** Both worlds run with gravity disabled on the arm (section 5.3). A policy that
   learned to move a weightless arm has never seen the roughly 30-50 N·m that `shoulder_lift`
   must produce to hold the arm out (a moment-arm estimate from the link masses, not a
   measurement). This is the largest modelling gap, and it is *inherited*,
   not chosen: it comes from reusing the twin's "position-mirror" USD as a training asset.
   The neutral fix is a separate export with gravity on for training.
4. **Nothing randomises the plant.** No mass, friction, gain, delay or noise randomisation
   (section 4.2). Policies trained without it are famously brittle on hardware.
5. **The observation set assumes perfect state.** The policy reads exact `q`, `qd` and an
   exact end-effector position. On hardware those are encoder reads (good) and FK of those
   reads (also good) — this one is actually fine, provided the frames line up.
6. **Success is defined in a frame.** "Within 2 cm" (`reach_env.py:104`) of a goal that the
   env also *renders* as a 2.5 cm sphere (`reach_env.py:112`). On hardware you need a
   perception estimate of that goal, and its error adds directly to your success rate.
7. **Rate mismatch of the reward.** Not a deployment issue but a diagnostics trap: the reward
   saturates at 15 cm while success needs 2 cm, so a run can look "converged" at reward +358
   with 39% success. If you deploy that checkpoint, expect ~39%, not "trained".

---

# 6. What to say on the slides

Eleven blocks. Each block is a title, three bullets you can read out loud, and the figure
that belongs with it. `docs/demo/` currently contains only `TEST-PLAN.md` — there are **no
media yet**, so each figure names either the scenario in `scripts/demo/run_demo.py` that
would produce it, or the experiment that does not exist yet.

**S1 — Two control worlds, on purpose**
- World A (the twin) is a *mirror*: the arm copies `/joint_states`; the sim never decides.
- World B (Isaac Lab) is a *learner*: 1024 parallel arms, the sim is the only truth.
- They cannot share one process, because an OmniGraph node rewrites the articulation every tick (`extension.py:2983-2987`).
- Figure: the two block diagrams from sections 1.1/1.2, side by side. (Draw them; no file exists.)

**S2 — World A is one graph and one spring**
- `OnPlaybackTick → ROS2SubscribeJointState → IsaacArticulationController → PhysX drive` (`extension.py:3042-3070`).
- The drive is `tau = k(q*−q) + c(qd*−qd)`, clipped at 200 N·m (`extension.py:701-703`).
- 120 Hz physics, 60 fps minimum frame rate, GPU dynamics off (`extension.py:2395-2405`).
- Figure: `docs/demo/media/<machine>/S2-01-workstation-loaded.png` + `S2-02-objects-playing.png` (scenario S2).

**S3 — A mirror that does not sag**
- Gravity is off on all 16 robot-chain rigid bodies, on for the objects (`extension.py:2971-3010`).
- Why: `/joint_states` already reflects the real robot's gravity compensation; simulating it again double-counts it.
- Measured: sag 4.06 → 0.37 mrad, tool droop 3.4 → 0.31 mm (`extension.py:2990-2992`).
- Figure: a before/after tool-height trace at a fixed pose. **Does not exist**; nearest probe is `scripts/checks/single_joint_motion.py`, which returns the numbers but plots nothing.

**S4 — Degrees vs radians: the 57.3x trap**
- USD angular drive gains are per **degree**; ROS and PhysX targets are per **radian** (`usdPhysics/schema.usda:856-865`, `...ArticulationController.ogn:4`).
- 100 N·m/deg = 5729.6 N·m/rad; the same number in Python means 57x softer.
- Isaac Sim's own docs warn about exactly this failure (`...sysid/docs/Overview.md:292`).
- Figure: a two-column table of the same constants in both unit systems. (Table, no file.)

**S5 — The gripper is force-limited, not stiff**
- One actuated joint + 5 mimic joints through `PhysxMimicJointAPI` (`extension.py:98-102`).
- k = 10000, c = 1000, but maxForce = **1.0 N·m** – stiffness shapes the approach, the cap sets the grip (`extension.py:116-133`).
- `maxForce` was walked down 1000 → 5 → 1.0 after a part was crushed through its own collision mesh (`extension.py:118-133`).
- Figure: an RG2 close-on-part capture showing the stall, plus the pad-gap trace from `/rg2_sim/pad_gap_m`. **Does not exist**; the topic does (`extension.py:144-150`).

**S6 — The plant is the same in both worlds**
- The reach env references the twin's own exported USD (`make_cell_usd.py:8-44`), so it inherits 100/10/200 exactly (`reach_env.py:29`, `actuator_base_cfg.py:100-118`).
- One number that crosses the boundary unchanged: `maxForce = 200 N·m`.
- Which means the sim-to-real question is *not* "are the gains the same" — it is "who closes the loop, and how fast".
- Figure: the gain comparison table from section 5.3.

**S7 — The reach MDP in one slide**
- Observation 21 = q(6) + 0.1·qd(6) + ee(3) + goal(3) + (goal−ee)(3) (`reach_env.py:172-175`).
- Action 6 = clipped deltas × 0.05 rad, integrated into absolute joint targets (`reach_env.py:165-167`).
- Physics 120 Hz, policy 30 Hz (decimation 4), episode 150 steps = 5 s (`reach_env.py:66-67,72`).
- Figure: the observation/action diagram from section 4.2, or the goal marker in the rendered env — `S8-01-play-window.png` (scenario S8).

**S8 — Reward shaping: two funnels and a brake**
- −2·d linear (find it) + 2·(1−tanh(d/0.15)) (approach it) + 3·(1−tanh(d/0.03)) (land on it) (`reach_env.py:105-108,177-185`).
- Penalties on action magnitude and joint speed; one-off −5 for a *bad* termination, not for a timeout.
- Success threshold is 2 cm (`reach_env.py:104`) — stricter than where the reward saturates.
- Figure: the reward as a function of distance — plot `-2d + 2(1-tanh(d/0.15)) + 3(1-tanh(d/0.03))` for d ∈ [0, 1]. **Does not exist**; 10 lines of matplotlib.

**S9 — PPO, configured conservatively**
- MLP 256-128-64 ELU, shared trunk, fixed σ = e^−1.5 ≈ 0.22 (`rl_games_reach.yaml:23-25`).
- Horizon 24, minibatch = envs×24, 5 epochs, clip 0.2, no entropy bonus.
- The real safety belt is `kl_threshold = 0.01` with an adaptive LR that moves in 1.5x steps (`rl_games_reach.yaml:50-55`, `rl_games/common/schedulers.py:26-32`).
- Figure: the clipped-objective sketch (r vs L for A>0 and A<0). (Draw it; it is three lines.)

**S10 — It learns, and it is not solved**
- 16 minutes, 1024 envs, 945 iterations, 3.7 GB VRAM: reward −43.5 → **+358.6** (`docs/HANDOFF.md:84`).
- Mean distance 0.39 m → **0.15 m**; success 0 → **0.39**, peak 0.77, noisy.
- Reward and success disagree at the margin because the reward saturates at 15 cm but success needs 2 cm.
- Figure: `docs/demo/media/<machine>/S7-01-reward-curve.png` + `S7-02-success-curve.png` (scenario S7, produced by `scripts/demo/plot_curves.py:193-196`).

**S11 — What deploying this policy would take**
- Policy at exactly 30 Hz, publishing joint targets, through the *same* PD drive; gains already match, so the risk is timing.
- Gaps, in order of size: gravity is off in training, no plant randomisation, unmeasured loop latency, friction unmodelled.
- Nothing in either repo computes IK for this: the twin never needs it and the policy outputs joint space.
- Figure: the deployment block diagram from section 5.1, with the 33 ms budget annotated.

If you need a twelfth: **S12 — The transform tree you must not "fix"** (the intentional 180°
camera rotation, `docs/ARCHITECTURE.md:162-167`; figure = `data/tf.json` from scenario S3).

## Self-test (12 questions, short answers)

1. **What makes the twin's arm move — a controller in the extension, or something outside?**
   Something outside: it copies `/joint_states` into the joints
   (`extension.py:3011-3074`). The extension computes no motion.

2. **Why can't you just add a torque controller to the twin?**
   The OmniGraph `IsaacArticulationController` re-writes the articulation action every tick
   and zeroes joint efforts (`extension.py:2983-2987`).

3. **Write the drive equation and the units of each symbol.**
   `tau = k(q*−q) + c(qd*−qd)`, `|tau| ≤ maxForce`; `q` rad, `qd` rad/s, `k`, `c` in N·m/rad and
   N·m·s/rad at runtime but authored in USD as the per-degree equivalents (`schema.usda:800-865`).

4. **A gain of 100 is written into a USD angular drive. What is the per-radian value?**
   5 729.6 N·m/rad (= 100 × 180/π).

5. **What is the twin's damping/stiffness ratio and what does it mean physically?**
   10/100 = 0.1 s; an overdamped drive with a ~0.1 s time constant, i.e. no overshoot and no
   oscillation for realistic inertias.

6. **Why is the gripper's stiffness 10 000 while its max force is 1 N·m?**
   Stiffness sets how firmly the jaws follow the commanded angle; `maxForce` caps grip
   torque, so the drive stalls gently on a part — force-limited position control, matching
   real RG2 firmware (`extension.py:118-133`).

7. **Why does the gripper publish its joint state with a plain publisher?**
   `ROS2PublishJointState` wedges the main thread at `timeline.play()` on that welded
   articulation (`extension.py:3091-3096`).

8. **What does "digital twin" mean in this project, precisely?**
   Joint-angle mirroring of the real robot plus a physically simulated *world* around it:
   gravity off on the robot chain, on for objects (`extension.py:2971-3010`).

9. **Is `LulaKinematicsSolver` used?**
   No. Imported at `extension.py:590`, referenced nowhere else.

10. **Give the reach env's observation layout and its dimension.**
    21 = 6 q + 6 (0.1·qd) + 3 ee + 3 goal + 3 (goal−ee) (`reach_env.py:172-175`).

11. **At what rate does the policy act, and what is `a · 0.05`?**
    30 Hz (dt 1/120 × decimation 4); 0.05 rad of joint-target increment per unit action
    (`reach_env.py:72`, `:167`).

12. **Do the two worlds use different gains?**
    No. The reach env inherits the same USD drives (`reach_env.py:29`,
    `actuator_base_cfg.py:107`), so both use 100/10/200. The real gaps are gravity, plant
    randomisation, and loop latency.

---

# 7. Unverified items (mirrored in section 8)

- whether `/joint_states` from `ur_robot_driver` includes `velocity[]` / `effort[]` in each
  mode (fake hardware / URSim / real);
- the actual `/joint_states` publish rate;
- effective joint inertias (only link masses and diagonal inertias were read);
- the real UR5e torque limits (external datasheet);
- the exact `OnPlaybackTick` rate on a given machine;
- the DOF ordering used by the policy (expected `shoulder_pan … wrist_3`, not printed);
- where a Cartesian grasp goal is turned into joint angles (outside both repos);
- whether TF for the arm links is published anywhere (no graph in the extension does it);
- the exact arithmetic inside the PhysX USD parser's deg→rad conversion (three secondary
  sources agree, the C++ was not read);
- whether the reported run's checkpoint still exists.

---

# 8. Open questions and things I could not verify

1. **`/joint_states` contents per backend.** The graph wires `velocityCommand` and
   `effortCommand` (`extension.py:3068-3069`), so the *behaviour* depends on what the
   publisher fills in. `joint_state_broadcaster` normally publishes position and velocity
   for UR robots; whether this driver build fills `effort[]` is **unverified**. If it does,
   a feed-forward torque is silently added on top of the PD spring — which is not part of
   the twin's documented model.
2. **Publish rate.** No config in this repo pins it; the graph consumes whatever arrives
   (`extension.py:3053`). If you present a "twin lag" number, measure it first.
3. **Does the twin lag the real robot, and by how much?** No measurement exists in the repo.
   This would be the single most valuable experiment for the presentation (drive a joint
   ramp, log both `/joint_states` and the simulated joint angles — the pattern is
   `artic.get_joint_positions()` at `extension.py:3219`; the extension registers no
   `get_joint_positions` *tool* despite `docs/ARCHITECTURE.md:116` using that name as an
   example — cross-correlate the two).
4. **`object`-side parity.** `objects_poses_sim` publishes prim transforms; whether the
   frame names and units match the real perception stack's convention was not checked here
   (see `docs/provenance/tf-baseline-6.2.json` for the recorded baseline).
5. **Effective inertias.** I read link masses and diagonal inertias out of
   `exts/ur5e-dt/assets/robot/ur5e.usd` (base 4.0, shoulder 3.7, upper arm 8.393, forearm
   2.275, wrists 1.219/1.219/0.1879 kg), but the effective inertia about each joint axis is
   what enters `zeta = c/(2√(kJ))`. My "zeta ≈ 2-3 for the shoulder, hundreds for the wrist"
   is an estimate, not a measurement.
6. **UR5e torque ratings.** 150 N·m / 28 N·m are datasheet values, not read from this repo.
7. **`OnPlaybackTick` rate.** Whether it is exactly the render rate on both machines was not
   measured; the twin only sets a *minimum* frame rate of 60 (`extension.py:2395-2398`).
8. **Policy DOF order.** Expected to be the USD articulation order, and
   `physics_check.py` prints `joint_names` (`physics_check.py:57`) — but I did not find a
   saved copy of that report, so the mapping is inferred.
9. **Training with gravity off.** I verified the flag is in the referenced USD, and that
   nothing in `reach_env.py` re-enables it. I did **not** find a note anywhere saying this
   was intended for the RL task; it looks inherited from the twin's mirror design. Flag it
   rather than assert intent.
10. **Who does IK for real grasps.** `move_home` / `move_to_grasp` live in `ros-mcp-server`
    (`extension.py:489-495`), outside both repos. Whether they use MoveIt, a Lula solver, or
    pre-recorded joint poses is **unverified**.
11. **Arm-link TF.** The extension publishes `/tf_cam` and `/objects_poses_sim`; I found no
    graph publishing the UR5e link chain, so a deployed policy cannot get the finger
    positions from TF today (it would have to do FK itself).
12. **PhysX's internal deg→rad conversion.** Three independent secondary sources
    (schema doc, importer, sysid docs) agree that USD is per degree and runtime is per
    radian; I did not read the C++ USD parser to see the multiplication.
13. **The 15-minute run's checkpoint.** `docs/HANDOFF.md:88-89` says a checkpoint was played in
    a window, but I did not confirm which file, so a "play the trained policy" demo may need
    to re-train.
14. **`learning6/README.md:36-37` is stale** ("the reach training result is not yet verified")
    relative to `docs/HANDOFF.md:84`. Harmless, but do not quote both on the same slide.

---

# 9. Code inconsistencies noticed (for the owner to decide)

Not fixed here — this document is the only file I wrote. Each item is a small, independent
decision.

**A. Stale comment about the arm gains.** `extension.py:2947` says
`# Configure joint drives: set maxForce=150 and stiffness=300 for all joints`, but the code
immediately below writes `self._ur5e_max_force` / `_ur5e_stiffness` / `_ur5e_damping`, which
are 200 / 100 / 10 (`extension.py:701-703`, used at `:2963-2965`). The comment is from an
older experiment; anyone reading it will believe the arm is 3x stiffer and 25% weaker than
it is.

**B. Dead, misleading gripper constants.** `extension.py:706-708` defines
`_gripper_max_force = 1.2`, `_gripper_stiffness = 0.1`, `_gripper_damping = 0.05`. None of
the three is read anywhere in the file. The gripper actually runs 10000 / 1000 / 1.0 from
`RG2_ACT_*` (`extension.py:116-133`, applied at `:3527-3529`). Anyone who trusts lines
706-708 gets the gripper story exactly backwards (0.1 stiffness is soft; the real drive is
near-rigid). Deleting the three lines, or renaming them to `_unused_*`, would prevent a
wrong slide.

**C. A unit conversion in a check script that contradicts the code it checks.**
`scripts/checks/drive_settings.py:14` computes
`nominal = {'stiffness_usd': 100 * math.pi / 180, 'damping_usd': 10 * math.pi / 180, ...}`,
i.e. it treats the twin's Python constants as N·m/rad and converts them to USD units. But
`extension.py:2963-2965` writes them **verbatim** into USD attributes that are N·m/deg. So
the script's idea of "nominal" is 57.3x below what is actually in the stage, and a future
reader comparing the two will conclude the drive is wrong when it is the script's convention
that is. Two possible intents, and they need a decision:
 - if 100/10 were meant as N·m/rad, the twin should author 1.745/0.175 and the arm is
   currently 57x stiffer than intended;
 - if 100/10 were meant as N·m/deg (which the matched damping/stiffness ratio of 0.1 s
   suggests was at least acceptable), the script should compare against 100/10.
 Either way the script and the code currently disagree.

**D. "Domain 7" vs `ROS_DOMAIN_ID=0`.** `extension.py:489` and
`scripts/launch_ur_fake.sh:14,26` say the stack runs on domain 7; `config/ros_dds.env:33`
defaults `ROS_DOMAIN_ID` to 0, and the graph takes the domain from the environment
(`extension.py:3051-3052`). Since the env file is the source of truth and it wins, the two
prose statements are wrong (or the env default is). This one *will* bite someone debugging
DDS discovery.

**E. Unused import of a deprecated package.** `extension.py:590` imports
`LulaKinematicsSolver` and `ArticulationKinematicsSolver`, used nowhere. In the installed
6.2 tree that module lives under `extsDeprecated/`. Import at module scope means the
extension depends on a deprecated extension being present — a fragile dependency for zero
benefit.

**F. Stale result statement in the sibling repo.** `learning6/README.md:36-37` says "the
reach training result is not yet verified" while `docs/HANDOFF.md:84` reports a full 945-
iteration run. Probably written before the run; worth a one-line update so the two repos do
not contradict each other in a presentation.

**G. Possible design gap, not a bug: gravity off in the RL task.** The reach env inherits
`physxRigidBody:disableGravity = 1` from the twin checkpoint
(`learning6/ur5e_lab/reach_env.py:36-38` referencing `make_cell_usd.py:8-44`). Correct for a
position mirror; questionable for a policy you might deploy. Worth an explicit decision, and
worth one sentence on the slide either way.

**H. Minor: `state_space = 0`** (`reach_env.py:70`) with a comment-free value — correct for
`DirectRLEnv` (no privileged critic state), but it reads like an oversight next to
`observation_space = 21`.

**I. Two PPO knobs in the YAML are inert.** `lr_schedule: adaptive` constructs
`AdaptiveScheduler(kl_threshold)` with only the threshold
(`learning6/.venv/lib/python3.12/site-packages/rl_games/common/a2c_common.py:176-177`), and
that scheduler hardcodes its own bounds at `min_lr = 1e-6`, `max_lr = 1e-2`
(`.../rl_games/common/schedulers.py:22-23`). The YAML's `min_lr: 1e-5` and `max_lr: 1e-3`
(`rl_games_reach.yaml:51-52`) are therefore never read. Not harmful — the run behaves fine —
but anyone tuning the LR floor will edit a number that does nothing. Also note the adaptive
step is ×/÷1.5, not ×/÷2, so "halve the learning rate" is the wrong description.

**J. `num_actors: -1`** (`rl_games_reach.yaml:44`) is rl_games shorthand for "use the actual
env count"; combined with a fixed `minibatch_size` (3072) it means the config is *only* valid
at 128 envs without an override — 1024 envs with 3072 would give 8 minibatches, which is why
the reported run had to pass 12288 (`docs/HANDOFF.md:79`). Fine, but fragile: the YAML reads
as if it scales.

**K. A doc example names a tool that does not exist.** `docs/ARCHITECTURE.md:116` lists
`ur5e_dt.get_joint_positions` as an example tool name, but the tool table
(`extension.py:418-582`) registers no such tool (grep for `get_joint` in the extension returns
only the internal `artic.get_joint_positions()` call at `extension.py:3219`). Anyone trying to
verify the twin by reading joint angles through MCP will fail; a one-line fix to the doc, or
adding the tool, resolves it.

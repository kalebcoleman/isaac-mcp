# URDF robots from a public file to a moving joint: record, 2026-11-04

Each robot went through the real MCP stdio server with no `run_python` in the goal path: `assets__pack_build`, `assets__pack_doctor`, then
`sim__quick_start robot=<pack>/<file>` (and separately `assets__import_urdf source_path=...`), Play and pause, `sim__simulation__step`,
`sim__joints__command`, more steps, `sim__joints__state` and `sim__camera__capture`. `run_python` was used only to diagnose. Sources: `Gepetto/example-robot-data` at `c8238fc`
(BSD-2-Clause) and `ros/urdf_tutorial` at `050f1e4` (BSD), cloned to `<home>/.cache/urdf-goal/src`. No robot file is in this repository.
`frankaemika/franka_description` and `ros-industrial/universal_robot` commit no URDF, only xacro, so the Panda and UR5 URDFs are the committed ones in example-robot-data.

Builds: Isaac Sim 6.2.0.0 (importer 3.11.12) and 5.1.0 (importer 2.4.30), PhysX, headless. No other build is on this machine.

## Commanded joints, after 6 s, then 2 s more

Radians, or metres for `torso_lift_joint` and `gripper_extension`. Drift is the largest change in any joint over the last 2 s.

| Robot, base | Joint | Target | 6.2 reached | 5.1 reached | Drift 6.2 / 5.1 |
|---|---|---|---|---|---|
| Panda, fixed | `panda_joint2` | -0.6 | -0.5971 | -0.5965 | 0 / 0 |
| | `panda_joint4` | -2.0 | -2.0095 | -2.0137 | |
| | `panda_joint6` | 1.5 | 1.4930 | 1.4874 | |
| UR5, fixed | `shoulder_lift_joint` | -1.2 | -1.1934 | -1.1943 | 0 / 0 |
| | `elbow_joint` | 1.2 | 1.2083 | 1.2104 | |
| xArm7, fixed | `joint2` | 0.5 | 0.5051 | 0.4951 | 0 / 0 |
| urdf_tutorial 07, fixed | `head_swivel` | 0.8 | 0.7991 | 0.7991 | 0 / 0 |
| | `gripper_extension` | -0.2 | -0.2000 | -0.2000 | |
| urdf_tutorial 07, floating | `head_swivel` | 0.8 | 0.7991 | 0.7991 | 0 / 0 |
| TIAGo, floating | `torso_lift_joint` | 0.2 | 0.1778 at 6 s, about 0.199 at 8 s | 0.1991 | 0.021 / 0 |
| | `arm_1_joint` | 1.0 | 1.0000 | 1.0000 | |
| Go2, floating | `FR_calf_joint` | -1.5 | -1.5079 | -1.5079 | 0 / 0 |

The errors match the design: the gains allow about 0.5 degrees (0.009 rad) of sag at the worst-case load. Commanded joints stopped; final speed 0 except TIAGo's torso on 6.2 at 6 s.
The `assets__import_urdf source_path` route gave the same 6.2 values for Panda and UR5.

Final captures viewed: Panda (6.2, 5.1) bent in the commanded pose with the gripper down; xArm7 (6.2, 5.1) bent and standing; the tutorial robot (5.1, floating; 6.2 viewed in an earlier run of the same path) upright on its wheels with the gripper out and head turned; TIAGo (6.2, 5.1) standing with its arm out, torso raised. UR5 (6.2): the arm is posed, but its top leaves the frame, because the camera is framed at the zero pose.
**Go2 (6.2, 5.1) lay on its back with its legs up** (see below). Appearance differs by build, and this was not investigated: on 5.1 Go2 is all white, Panda's dark parts are grey, and TIAGo's base shows a yellow band that is absent on 6.2.

A pack built once served both builds. In one shared staging folder, 6.2 and then 5.1 imported the same Panda snapshot, and 5.1 converted it afresh (`PhysX`, `root_joint`).

## What caused the instability

`urdf_tutorial` `07-physics.urdf`, 6.2, as the importer leaves it, 300 steps at 60 Hz. Largest joint speed in each 60-step window, rad/s:

| Case | Speed |
|---|---|
| Base at the origin, on the ground plane | 3832, 1080, 599, 1176, 634 |
| Same, `physx` variant | 27, 108, 706, 47, 736 |
| Same, ground plane removed | 0, 0, 0, 0, 0 |
| Raised 0.56 m, or 1 m, or 2 m | 0, 0, 0, 0, 0 |
| At the origin, inertia corrected to 0.1 x mass | 28, 218, 12, 367, 330 |
| At the origin, with gains 10 and 1 | 55, 18, 13, 13, 15 (gripper still 44 rad past its limit) |
| Raised 2 m, with gains | 0, 0, 0, 0, 0 |

So the runaway needs **a base fixed to the world and a start inside the ground plane**. The robot's origin is at its body, and its lowest point is 0.47 m below that, so at the origin its legs are under the floor,
and the fixed base stops the contact solver pushing them out. Run through the final tools with `fixed_base=false` at the origin, no default drives: on 6.2 the base popped up to z=0.47 at once, and speeds stayed near 6.5 rad/s with the wheels rolling it 1.2 m away; on 5.1, speed 0.
The importers fix the base by default. The variant does not matter. Correct inertia reduces the speeds and does not stop them. A separate cause explains the ignored targets: **a URDF has no position gains, so a 6.x conversion authors stiffness 0.**
Raised and stable, with `default_drives=false`, `head_swivel` commanded to 0.8 stayed at -0.0 on 6.2. On 5.1 that case reached 0.8, since its importer authors its own gains.

The 6.x conversion always writes a world joint (`root_joint`), so its `fix_base=true` is a no-op and the "free" and "fixed" rows above were both fixed. Only `fix_base=false` frees the base. With no ground contact a fixed base is stable: speed 0.

Through the final tools, `position=[0,0,0]` keeps the bad start and warns: 6.2 reached 3752 rad/s and `sim__joints__state` reported joints past their limits; 5.1 reached 25 million rad/s, and now reports them as far too fast.
With no position given, the robot is raised 0.475 m and all speeds are 0.

**The tutorial asset itself is also at fault:** inertia 1e-3 for 10 kg links is up to 400 times too small for their shapes. The import warns, names each link and does not change the file.

## Other asset findings

- **Go2:** the zero pose is outside `*_calf_joint` limits (-2.72 to -0.84). The import warns; on Play the legs jump to the limit and the robot flips over. Joints still follow commands, but it does not stand: no tool sets a start pose.
- **TIAGo:** warned for impossible inertia on three links and effort limits below the worst-case load on three arm joints. Its wheels reached 2.92 of 3.0 rad, and **the base did not move**. The URDF fixes its casters; the cause was not investigated further.
- **urdf_tutorial wheels:** a 6 rad wheel command moved the floating base 0.156 m, stopping with speed 0. An earlier gain sized for the wheel alone ran away (35 rad/s, base 6.4 m away), so continuous joints are now sized for the whole robot.
- **urdf_tutorial meshes** name two absent normal maps: the pack reports `self_contained: false` and lists them.

## 5.1 importer problems found and handled

- **Kit hung forever on any URDF with a mesh, whenever `ur5e-dt` was enabled.** The importer holds the GIL while waiting for workers, and the twin's Python carb log listener makes a logging worker wait for the GIL. Stacks from `faulthandler` showed the main thread inside `import_robot`. Standalone it took 0.1 s; removing the listener fixed it in the same Kit. The listener is now paused around the call. Other Python log listeners would do the same.
- **Kit crashed (SIGSEGV in `libomniverse_asset_converter`) on a COLLADA file with no `<unit>`:** all 11 TIAGo DAEs, each alone. Adding the element fixed it. A private copy now states the format's default.
- It authors acceleration drives, so force-sized gains came out too weak (`panda_joint6` 0.134 rad short). The drives are now force type. Its own default gains were also too weak for TIAGo's torso (0.054 m short).
- Links with no visuals get references to prims it never wrote, a composition error. Those references are removed from the generated files only.

## Not checked

Isaac Sim 6.0 and 6.1, and 5.0: the code detects the API, but nothing ran there. Newton. A windowed Kit. A real robot. Driving a mobile robot along a path or at a speed. Contact and grasping. A saved scene reopened. Xacro refusal on a real repository's file (covered by tests with small fixtures). `sim__ros2__tf_setup` on an imported URDF.

# ROS 2 TF and RViz 2 with Isaac Sim

Use this guide to get a correct TF tree and a working RViz 2 view of an Isaac Sim robot,
the same way on any machine. It covers three tools:

| Step | Tool | Runs where |
|---|---|---|
| 1. Publish `/clock`, `/joint_states` and the robot's TF | `sim__ros2__tf_setup` (MCP) | inside Isaac Sim |
| 2. Write an RViz config with the right Fixed Frame | `python3 scripts/ros2_rviz_config.py` | anywhere (stdlib only) |
| 3. Check the live TF tree before trusting RViz | `python3 scripts/ros2_tf_doctor.py` | with ROS 2, or `--isaac-ros` |

The graph layout follows NVIDIA's own helpers that ship with Isaac Sim 6.2
(`isaacsim.ros2.nodes`, `impl/graph_api.py`: clock, TF and odometry graphs) and the
[Transform Trees and Odometry tutorial](https://docs.isaacsim.omniverse.nvidia.com/6.0.1/ros2_tutorials/tutorial_ros2_tf.html),
and the RViz layout follows NVIDIA's `isaac_tutorials` configs in
[IsaacSim-ros_workspaces](https://github.com/isaac-sim/IsaacSim-ros_workspaces). Frame names
follow [REP 105](https://www.ros.org/reps/rep-0105.html) (`map`, `odom`, `base_link`).

## The rules that keep RViz stable

Every "the map is wrong", "frames jump" or "RobotModel is white" problem we have seen comes from one of these:

1. **One publisher per frame.** Each frame has exactly one parent, from exactly one node.
   Isaac publishing the robot's links *and* `robot_state_publisher` publishing them from the
   URDF gives every link two sources, so RViz jumps between them. Pick one
   (`link_frames=isaac` or `link_frames=robot_state_publisher`).
2. **One clock.** Isaac stamps everything with simulation time and publishes `/clock`. Every
   other node, RViz included, must run with `use_sim_time:=true`; mixing wall-clock and sim
   stamps makes TF lookups fail or extrapolate.
3. **One root.** RViz's *Fixed Frame* must be the root of a single connected tree
   (`world` for an arm on a table, `map` or `odom` for a mobile base). A frame that does not
   connect to it is drawn nowhere.
4. **`map -> odom` belongs to localization.** If AMCL or SLAM runs, it publishes `map -> odom`;
   then Isaac must publish only `odom -> base_link` (`world_frame=""`). A namespaced mobile base
   uses `<ns>_odom` (`a/b` gives `a__b_odom`); set the localization node's `odom_frame_id` to it.
5. **Same DDS settings on both sides.** `run.sh` uses `config/ros_dds.env` (Cyclone DDS,
   localhost only, `ROS_DOMAIN_ID`). Source the same file in the RViz/ROS terminal, or nothing
   is discovered.

## 1. Publish the robot from Isaac Sim

Stop the timeline, then call the tool once per robot. Calling it again rebuilds that robot's
graph (`/Graph/isaac_mcp_ros2`, or `/Graph/isaac_mcp_ros2_<namespace>_<hash>`), so nothing is ever
published twice by it. The static `world -> base_link` (or `world -> odom`) is the pose at setup
time: call it again after moving the robot.

Tool `sim__ros2__tf_setup`, with the IDs from `sim__stage__info`:

```json
{"session_id": "<from sim__stage__info>", "stage_id": "<from sim__stage__info>", "robot": "/World/UR5e"}
```

| Situation | Arguments |
|---|---|
| Arm on a table, RViz shows TF frames | defaults: `base=fixed`, `world_frame=world`, `link_frames=isaac` |
| You want the URDF RobotModel in RViz | `link_frames=robot_state_publisher`, then run `robot_state_publisher` with the URDF and `use_sim_time:=true` |
| Mobile base, no localization | `base=mobile`, `base_link=<chassis prim>`, `world_frame=map` |
| Mobile base with AMCL/SLAM | `base=mobile`, `base_link=<chassis prim>`, `world_frame=""` |
| Cameras, LiDARs, `tool0` | `extra_frames=["/World/.../tool0", "/World/camera"]` (published relative to the base link) |
| Several robots | one call per robot, each with its own `namespace` (its own graph; `/joint_states` and `/odom` are namespaced, TF stays on `/tf`). Their link names must differ: set `isaac:nameOverride`. A namespaced mobile base gets its own odometry frame `<ns>_odom`. Each graph also publishes `/clock` (same sim time) |

The tool finds the articulation root itself (often a fixed `root_joint` below the robot prim).
It refuses a `base_link` that is not one of the robot's links, a `base_frame` that differs from
the name Isaac publishes for it when Isaac publishes the links or extra frames (that would split
the tree; with `robot_state_publisher` and no extra frames it must be the URDF root), and links or extra frames whose TF
names collide (TF name = `isaac:nameOverride`, else the prim name). It returns the frame tree, the
topics, the ROS domain and the Fixed Frame to use (`rviz_fixed_frame`). Then play the timeline:
it publishes on every playback tick.

## 2. Write the RViz config

```bash
python3 scripts/ros2_rviz_config.py --fixed-frame world --out ~/lab.rviz \
    --image /camera/rgb --scan /scan --cloud /point_cloud --odom /odom
#   add --robot-model when robot_state_publisher provides /robot_description
set -a; . config/ros_dds.env; set +a          # same DDS settings as the simulator
rviz2 -d ~/lab.rviz --ros-args -p use_sim_time:=true
```

## 3. Check the tree

```bash
set -a; . config/ros_dds.env; set +a
source /opt/ros/<distro>/setup.bash
python3 scripts/ros2_tf_doctor.py --fixed-frame world
# no system ROS 2 (only Isaac Sim installed): use its bundled ROS 2
python3 scripts/ros2_tf_doctor.py --fixed-frame world --isaac-ros
```

It listens for a few seconds and reports frames with two parents, frames on both `/tf` and
`/tf_static`, frames that do not reach the Fixed Frame, mixed sim/wall time, a missing
`/clock`, frames that stopped updating, and which nodes publish TF. Exit 0 means the tree is
consistent. Fix every `ERROR` before debugging RViz itself.

Example: an extra `robot_state_publisher` running next to Isaac's link TF:

```text
ERROR: frame 'wrist_3_link' has 2 parents (base_link, wrist_2_link): two publishers disagree about the tree, so RViz jumps between them. Keep exactly one.
ERROR: simulation-time and wall-clock stamps are mixed: ... Start every node with use_sim_time:=true.
WARNING: several nodes publish TF: /_Graph_isaac_mcp_ros2_TFRobotLinks, ..., /robot_state_publisher. If Isaac publishes the robot's links, do not also run robot_state_publisher for it.
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| RViz: "Frame [map] does not exist" | Fixed Frame is not the tree's root | Use `rviz_fixed_frame` from the tool, or the doctor's `roots` |
| Robot jumps or flickers | two publishers for the same frames | doctor shows "has 2 parents"; keep one (`link_frames`) |
| "Lookup would require extrapolation", nothing moves | wall-clock node in a sim-time tree | `use_sim_time:=true` everywhere; Isaac must be playing (`/clock`) |
| No topics at all | DDS or domain mismatch | source `config/ros_dds.env` and use the simulator's `ROS_DOMAIN_ID` (the tool reports it) |
| TF shows only the robot's top prim | targeted the robot Xform, not its articulation | the tool resolves the articulation root; for hand-built graphs target that prim |
| RobotModel white/missing | no `/robot_description` | `link_frames=robot_state_publisher` + `robot_state_publisher` with the URDF |
| Frames stop after reset | timeline stopped | play again; `/clock` restarts from 0, so restart RViz or press Reset |

Validated on Isaac Sim 6.2 (UR5e + RG2 + camera: 17 frames, one root; NVIDIA Jetbot mobile
base: `map -> odom -> chassis -> wheels` with `/odom`), with the doctor using the bundled ROS 2.

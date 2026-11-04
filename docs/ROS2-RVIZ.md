# ROS 2 TF and RViz 2 with Isaac Sim

Use this guide to get a correct TF tree and a working RViz 2 view of an Isaac Sim robot,
the same way on any machine. Labs written for Isaac Sim 6.0 should also read the
[Isaac Sim 6.2 notes](ISAAC-6.2-NOTES.md) (node and API differences, verified live). It covers three tools:

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

## Installing ROS 2 (for RViz 2 and the doctor)

Isaac Sim ships its own ROS 2 libraries for the bridge, but RViz 2 needs a ROS 2 installation.

- **With sudo:** the official packages for your Ubuntu release (Humble on 22.04, Jazzy on 24.04):
  `sudo apt install ros-<distro>-ros-base ros-<distro>-rviz2 ros-<distro>-robot-state-publisher
  ros-<distro>-tf2-tools ros-<distro>-rmw-cyclonedds-cpp ros-<distro>-xacro`, then
  `source /opt/ros/<distro>/setup.bash`.
- **Without sudo** (lab and university accounts): [RoboStack](https://robostack.github.io/)
  installs ROS 2 into your home folder with micromamba:

  Pick the distro from the OS first, because it must match the simulator's bundled bridge (see the
  last item): **Jazzy on Ubuntu 24.04, Humble on Ubuntu 22.04.**

  ```bash
  . /etc/os-release
  case "$VERSION_ID" in 22.04) DISTRO=humble ;; *) DISTRO=jazzy ;; esac   # what run.sh picks
  mkdir -p ~/.local && curl -fsSL https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj -C ~/.local bin/micromamba
  ~/.local/bin/micromamba create -y -p ~/ros2/$DISTRO -c conda-forge -c robostack-$DISTRO \
      ros-$DISTRO-desktop ros-$DISTRO-rmw-cyclonedds-cpp ros-$DISTRO-tf2-tools ros-$DISTRO-xacro
  ~/.local/bin/micromamba run -p ~/ros2/$DISTRO rviz2
  # or, per shell: source scripts/ros_env.sh ~/ros2/$DISTRO   (activates it, then config/ros_dds.env)
  ```

  About 5.5 GB. Validated with Humble on Ubuntu 22.04 with Isaac Sim 6.2: the doctor and RViz 2 see
  the simulator's TF with `config/ros_dds.env` sourced. (Older Humble environments were created from
  the `robostack-staging` channel; they keep working.) On 24.04 with sudo, apt's
  `/opt/ros/jazzy` is the simpler choice.
- **Only the doctor:** `--isaac-ros` uses Isaac Sim's bundled ROS 2; no install needed.
- **Use the same distro as the simulator's bridge.** `run.sh` loads Isaac's bundled Humble on
  Ubuntu 22.04 and Jazzy on 24.04 (`ROS_DISTRO` overrides it). Humble and Jazzy nodes did not
  discover each other with `ROS_LOCALHOST_ONLY=1` in local tests, so install the matching distro
  (or point `ROS_DISTRO` at the one you have) rather than mixing them.

## The rules that keep RViz stable

Every "the map is wrong", "frames jump" or "RobotModel is white" problem we have seen comes from one of these:

1. **One publisher per frame.** Each frame has exactly one parent, from exactly one node.
   Isaac publishing the robot's links *and* `robot_state_publisher` publishing them from the
   URDF gives every link two sources, so RViz jumps between them. Pick one
   (`link_frames=isaac` or `link_frames=robot_state_publisher`).
2. **One clock.** Isaac stamps everything with simulation time and publishes `/clock`, from
   exactly one node. Two `/clock` publishers (for example the UR5e twin's graph plus a
   hand-built one) disagree by a few frames, so time keeps jumping back: RViz logs "Detected
   jump back in time", resets and clears TF many times a second. `sim__ros2__tf_setup` reuses
   an existing clock (`clock=auto`), or else publishes one shared clock
   (`/Graph/isaac_mcp_ros2_clock`) for every robot it sets up, and matches its TF stamps to that
   clock's stop/play behaviour. Running the twin's own setup (`quick_start`) afterwards adds a second
   clock: call the tool again with `clock=auto`; it drops its clock and updates every robot it
   set up. The shared clock stays after the last robot graph is deleted (`sim__stage__clear_owned`
   removes it). Every other node, RViz included, must run with
   `use_sim_time:=true`; mixing wall-clock and sim stamps makes TF lookups fail or extrapolate.
3. **One root.** RViz's *Fixed Frame* must be the root of a single connected tree
   (`world` for an arm on a table, `map` or `odom` for a mobile base). A frame that does not
   connect to it is drawn nowhere.
4. **`map -> odom` belongs to localization.** If AMCL or SLAM runs, it publishes `map -> odom`;
   then Isaac must publish only `odom -> base_link` (`world_frame=""`). A namespaced mobile base
   uses `<ns>_odom` (`a/b` gives `a__b_odom`; namespace segments may not start or end with `_`);
   set the localization node's `odom_frame_id` to it.
5. **Same DDS settings on both sides.** `run.sh` uses `config/ros_dds.env` (Cyclone DDS,
   localhost only, `ROS_DOMAIN_ID`). Source the same file in the RViz/ROS terminal, or nothing
   is discovered.

## 1. Publish the robot from Isaac Sim

Stop the timeline, then call the tool once per robot. Calling it again rebuilds that robot's
graph (`/Graph/isaac_mcp_ros2`, or `/Graph/isaac_mcp_ros2_<namespace>_<hash>`), so nothing is ever
published twice by it. The static `world -> base_link` (or `world -> <odom>`) is the pose at setup
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
| A gripper that is its own articulation on an arm | one call for the arm, then one for the gripper: `robot=/World/RG2_Gripper`, `base_link=/World/RG2_Gripper/rg2_base_link`, `namespace=rg2`, `attach_to=/World/UR5e/wrist_3_link` |

A gripper joined to an arm by a fixed joint with `excludeFromArticulation` is a separate articulation, so neither one's
link TF covers the joint, and the doctor reports its frames as not connected to `world`. A static transform would be wrong
once the arm moves. `attach_to` publishes `wrist_3_link -> rg2_base_link` on every playback tick from the two live poses,
in place of the world link. The parent must be a rigid body outside this robot, and the arm needs its own call so its
link connects to `world`. Do not also give the gripper a world link. Checked on 6.2 with Quick Start's UR5e and RG2: the
doctor found 17 frames all connected to `world`, and with the arm moving the gripper followed it about 1 m while the edge
stayed about constant.

One namespace holds one robot, because each namespace has one graph. Setting up a second robot in a namespace already used
refuses with `ROBOT_GRAPH_CONFLICT`, names the robot there, and gives a free namespace in `retry_with`, so the first robot's
joint states and frames stay. Repeating setup on the same robot rebuilds its graph as before. This also holds for a graph saved
in a file and reopened.

The tool refuses with `JOINT_STATE_LOOP` when another graph subscribes to the joint state topic it would publish. On a
Quick Start scene, `ur5e_dt__*`'s graph mirrors a real robot from `/joint_states`, so publish the simulated state elsewhere:
`joint_states_topic=isaac/joint_states`. Without this, the arm chased its own state and oscillated at about 3.14 rad/s once
playing (checked on 6.2). The check sees graphs that exist at setup time: running `ur5e_dt__quick_start` after this tool
creates the same loop, so call this tool again afterwards, which refuses until you pick another topic.

The tool finds the articulation root itself (often a fixed `root_joint` below the robot prim).
It refuses a `base_link` that is not one of the robot's links, a `base_frame` that differs from
the name Isaac publishes for it when Isaac publishes the links or extra frames (that would split
the tree; with `robot_state_publisher` and no extra frames it should be the URDF root), and links or extra frames whose TF
names collide (TF name = `isaac:nameOverride`, else the prim name). It returns the frame tree, the
topics, the ROS domain and the Fixed Frame to use (`rviz_fixed_frame`). Then play the timeline:
it publishes on every playback tick.

## 2. Write the RViz config

```bash
python3 scripts/ros2_rviz_config.py --fixed-frame world --out ~/lab.rviz \
    --image /camera/rgb --scan /scan --cloud /point_cloud --odom /odom
#   add --robot-model when robot_state_publisher provides /robot_description
export ROS_DOMAIN_ID=<the simulator's domain>
source scripts/ros_env.sh                      # ROS 2, then config/ros_dds.env, then daemon stop
setsid nohup rviz2 -d ~/lab.rviz --ros-args -p use_sim_time:=true > /tmp/rviz.log 2>&1 < /dev/null &
echo $! > /tmp/rviz.pid                         # stop it later with: kill "$(cat /tmp/rviz.pid)"
```

`setsid nohup` keeps RViz running after the command that started it ends (agent harnesses kill
the process group of each tool call). View and window options:

| Option | Effect |
|---|---|
| `--view orbit` (default) / `--view topdown` | 3D orbit camera, or a 2D `TopDownOrtho` view like a map |
| `--distance M` | orbit camera distance (default 3 m) |
| `--scale PX` | top-down zoom in pixels per metre (default 100) |
| `--target-frame base_link` | the view follows that frame (default: the Fixed Frame, a still view) |
| `--hide-panels` | leaves the Displays and Views panels out: the 3D view (and any image docks) only. Add panels back from RViz's Panels menu |
| `--width` / `--height` | window size in pixels (default 1400 x 900) |

Checked in rviz2 (Humble, RoboStack) with window captures: `Hide Left Dock: true` and per-panel
`collapsed: true` in a config file have no effect when RViz2 loads it, so `--hide-panels` omits the
panels instead. An Image display opens its own dock about 320 px wide; RViz2 has no per-display
size setting (the dock layout is a binary `QMainWindow State` that RViz writes itself), so drag
the dock edge and use File > Save Config to keep a larger image.

## Agent shells: environment and driving

`scripts/ros_env.sh` must be **sourced**, and not in a pipeline: `source scripts/ros_env.sh ... | tail`
runs it in a subshell, so the environment is silently lost (redirect to a file instead). It sources a ROS 2 install (a path, or auto-detected:
`ROS_DISTRO` if exported, else Humble on 22.04 and Jazzy on 24.04, in `/opt/ros/<distro>` and
`~/ros2/<distro>`), any `--overlay` on top of it, then `config/ros_dds.env`, then runs
`ros2 daemon stop`, and prints what it chose:

```bash
export ROS_DOMAIN_ID=63                      # a prefix assignment (ROS_DOMAIN_ID=63 source ...) does not last
source scripts/ros_env.sh ~/ros2/humble --overlay ~/ros2/humble_nav2   # RoboStack + a Nav2 env
source scripts/ros_env.sh /opt/ros/jazzy --overlay ~/ros2/jazzy_nav2/install
source scripts/ros_env.sh --dry-run          # print the choice only
```

RoboStack prefixes are activated with micromamba (stacked for an overlay, with the overlay first on
`AMENT_PREFIX_PATH`). Prefer this to `micromamba run` for `ros2` commands: `micromamba run` takes a lock
on the prefix, which breaks concurrent `ros2` calls. The daemon matters: one started earlier with other DDS settings answers
`ros2 topic list` and `ros2 node list` with nothing while data flows. `--keep-daemon` skips the
stop; `ros2 topic list --no-daemon` avoids the daemon (`ros2 topic hz` has no such flag). Kit's
bridge nodes do not appear in `ros2 node list` at all; check topics and `/tf` instead.

`ros2 topic echo` truncates long arrays, so a LaserScan can look all invalid: judge `/scan` with a small
`rclpy` subscriber that prints the count and the range. `--field ranges` works too, but prints every value on one line.

Without a TTY there is no keyboard teleop. Drive with timed steps (linear m/s, angular rad/s,
seconds):

```bash
python3 scripts/cmd_vel_sequence.py "0.15,0,5;0,0.5,3;0,0,1"         # --topic, --rate, --stamped
python3 scripts/cmd_vel_sequence.py "0.15,0,5" --dry-run              # print the plan only
```

It always publishes a zero Twist at the end, on errors, and on Ctrl-C, SIGTERM or SIGHUP; only
SIGKILL skips that. For one-off messages use `ros2 topic pub --times N`, and wrap any command
that could block in `timeout -s KILL <s>`: a `timeout 6 ros2 topic pub ... | tail` hung for 120 s
in a lab run while the robot drove into a wall.

For Nav2, the `map` frame is the world frame. The `origin` in the map YAML is the position of the
image's lower-left corner, not an offset to add to goals.

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
`/clock`, frames that stopped updating, and which nodes publish TF. It also compares the
header stamps of sensor topics (LaserScan, Image, CameraInfo, PointCloud2, Imu, Odometry, and
any `--topic`) with `/clock`. Exit 0 means the tree is consistent. Fix every `ERROR` before debugging RViz itself.

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
| RViz log floods "Detected jump back in time. Resetting RViz" | two `/clock` publishers | doctor lists them; keep one (`clock=auto` reuses the existing one) |
| RViz drops every scan: "timestamp on the message is earlier than all the data in the transform cache" | the lidar helper restarts at 0 on Stop but the clock graph's Read Simulation Time has `resetOnStop=False` | doctor reports the topic's offset; give every Read Simulation Time node the same `resetOnStop` (`True` with sensor helpers), then Stop and Play |
| LaserScan shown in RViz but no points visible | Isaac's 2D lidar publishes zero intensities and the display colours by intensity | use `Color Transformer: FlatColor` (`ros2_rviz_config.py` writes that) |
| ROS 2 node fails: "SharedMemory: unknown element" / "rmw handle is invalid" | an old `cyclonedds.xml` with `<SharedMemory>` and a Cyclone build without it | use this repository's current `config/cyclonedds.xml` |
| Frames stop after reset | timeline stopped | play again; RViz resets once if `/clock` restarts from 0 (the twin's clock keeps counting) |
| Robot vanishes after stop/play, "extrapolation into the past" | TF and `/clock` from different time sources | doctor reports the skew; re-run `sim__ros2__tf_setup` (`clock=auto`) |

Validated on Isaac Sim 6.2 (UR5e + RG2 + camera: 17 frames, one root; NVIDIA Jetbot mobile
base: `map -> odom -> chassis -> wheels` with `/odom`), with the doctor using the bundled ROS 2.

# rclpy Setup for Isaac Sim 5.0

Isaac Sim 5.0 uses Python 3.11, but ROS2 Humble ships rclpy compiled for Python 3.10. This guide builds a Python 3.11-compatible ROS2 Humble workspace so that `import rclpy` works inside Isaac Sim extensions.

## Why

The Isaac Sim ROS2 bridge (`isaacsim.ros2.bridge`) uses C++ internally and works without rclpy. However, rclpy enables:

- **Subscriber-aware cameras** — detect when a ROS2 node subscribes to a camera topic and only render when needed
- **ROS2 services** — request/response pattern from within the extension
- **Direct Python callbacks** — handle incoming ROS2 messages without OmniGraph polling
- **Custom message types** — use any ROS2 message type directly

## Prerequisites

- Ubuntu 22.04
- ROS2 Humble (`/opt/ros/humble/`)
- Docker
- Isaac Sim 5.0 installed via pip in a virtualenv (e.g. `env_isaaclab`)

## Build

The build uses NVIDIA's [IsaacSim-ros_workspaces](https://github.com/isaac-sim/IsaacSim-ros_workspaces) repo. It runs entirely inside Docker and does not modify your system ROS2 installation.

```bash
# Clone the repo
git clone https://github.com/isaac-sim/IsaacSim-ros_workspaces.git ~/IsaacSim-ros_workspaces
cd ~/IsaacSim-ros_workspaces

# Checkout the tag matching Isaac Sim 5.0 (Python 3.11)
# The main branch targets Python 3.12 (Isaac Sim 5.1+) which won't work.
git checkout IsaacSim-5.0.0-full

# Build ROS2 Humble with Python 3.11 (takes ~10-20 min)
bash build_ros.sh -d humble -v 22.04
```

Output goes to `~/IsaacSim-ros_workspaces/build_ws/humble/`.

## Configuration

Add the following to your virtualenv's `bin/activate` script (at the end):

```bash
# Source ROS2 Humble Python 3.11 workspace for rclpy compatibility with Isaac Sim
source "$HOME/IsaacSim-ros_workspaces/build_ws/humble/humble_ws/install/local_setup.bash"
source "$HOME/IsaacSim-ros_workspaces/build_ws/humble/isaac_sim_ros_ws/install/local_setup.bash"
export ROS_DISTRO=humble
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export LD_LIBRARY_PATH="$HOME/IsaacSim-ros_workspaces/build_ws/humble/humble_ws/install/lib:$LD_LIBRARY_PATH"
```

If you use FastDDS instead of CycloneDDS, change the `RMW_IMPLEMENTATION` line to:

```bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
```

## Usage

```bash
# Deactivate and reactivate to pick up the new paths
deactivate
source ~/env_isaaclab/bin/activate

# Launch Isaac Sim
isaacsim
```

## Verification

From the Isaac Sim Script Editor (Window > Script Editor), run:

```python
import rclpy
from sensor_msgs.msg import Image
print("rclpy OK")
```

If it prints `rclpy OK` without errors, the setup is working. If you see `No module named 'rclpy._rclpy_pybind11'`, the workspace was not sourced before launching Isaac Sim — deactivate, reactivate, and relaunch.

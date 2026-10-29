#!/usr/bin/env bash
# launch_ur_fake.sh — bring up the UR5e ros2_control driver with FAKE (mock) hardware.
#
# Why this exists: the URSim-docker path (sim_bringup.sh) needs Docker + a ~/ros2_ws
# install of ur_bringup and clicks through PolyScope; on a fresh box that workspace
# may be absent. ur_robot_driver's mock_components hardware publishes /joint_states and
# runs the controllers WITHOUT URSim or a real robot, which is all the Isaac twin needs:
#   ros-mcp-server --FollowJointTrajectory--> scaled_joint_trajectory_controller (fake hw)
#                  --/joint_states--> Isaac Sim ur5e-dt joint-state subscriber (robot mirrors).
#
# This is the LIGHTWEIGHT alternative to sim_bringup.sh, kept ALONGSIDE it (not a
# replacement). Modeled on prismatic-manipulation/scripts/launch_ur.sh (a4500) but sources
# THIS repo's config/ros_dds.env so the driver joins the same DDS island (Cyclone +
# ROS_LOCALHOST_ONLY=1 + domain 7) as Isaac (launched via scripts/launch_sim.sh) and the
# ros-mcp-server consumer — see config/ros_dds.env for why that homogeneity is mandatory.
#
# Usage:
#   scripts/launch_ur_fake.sh up      # launch fake-hw driver, block until /joint_states live
#   scripts/launch_ur_fake.sh status  # controllers + /joint_states state
#   scripts/launch_ur_fake.sh down    # tear down the driver
#
# Reference: https://github.com/UniversalRobots/Universal_Robots_ROS2_Driver
set -eo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ROS distro + the homogeneous sim DDS env (domain 7 / Cyclone / localhost-only).
[ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash
source "$REPO/config/ros_dds.env"
# Optional overlay workspace (apt ur_robot_driver works without it; source if present).
[ -f "$HOME/ros2_ws/install/setup.bash" ] && source "$HOME/ros2_ws/install/setup.bash"

UR_TYPE="${UR_TYPE:-ur5e}"
LOG="/tmp/ur_fake_driver.log"
PIDFILE="/tmp/ur_fake_driver.pid"
ACTION="${1:-up}"

# Bracket-trick ([r]os2) so the match never hits the calling shell.
_driver_running() { pgrep -f "[r]os2 launch ur_robot_driver" >/dev/null 2>&1; }

_controllers() { ros2 control list_controllers 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g'; }

case "$ACTION" in
  up)
    if _driver_running; then
      echo "[ur-fake] already running (pid $(cat "$PIDFILE" 2>/dev/null)); run '$0 down' first" >&2
      exit 2
    fi
    echo "[ur-fake] launching $UR_TYPE fake hardware -> domain $ROS_DOMAIN_ID (Cyclone, localhost_only=$ROS_LOCALHOST_ONLY)"
    nohup ros2 launch ur_robot_driver ur_control.launch.py \
        ur_type:="$UR_TYPE" use_fake_hardware:=true robot_ip:=0.0.0.0 launch_rviz:=false \
        > "$LOG" 2>&1 &
    echo "$!" > "$PIDFILE"
    echo "[ur-fake] pid $(cat "$PIDFILE")  log $LOG"

    # Ready = joint_state_broadcaster active (that's the node that publishes /joint_states).
    # Event-based: poll the controller list (a real state signal), not a blind sleep.
    echo "[ur-fake] waiting for joint_state_broadcaster ..."
    for i in $(seq 1 30); do
      sleep 2
      if _controllers | grep -q "joint_state_broadcaster.*active"; then
        echo "[ur-fake] READY: joint_state_broadcaster active (after ${i}x2s)"
        # Ensure the trajectory controller is active so FollowJointTrajectory works.
        if _controllers | grep -q "scaled_joint_trajectory_controller.*inactive"; then
          echo "[ur-fake] activating scaled_joint_trajectory_controller ..."
          ros2 control switch_controllers --activate scaled_joint_trajectory_controller 2>/dev/null | tail -1 || true
        fi
        _controllers | sed 's/^/  ctrl: /'
        exit 0
      fi
      if [ -f "$PIDFILE" ] && ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "ERROR: ur_robot_driver exited; see $LOG" >&2; tail -20 "$LOG" | sed 's/^/  log: /'; exit 4
      fi
    done
    echo "ERROR: joint_state_broadcaster not active within timeout; see $LOG" >&2
    tail -20 "$LOG" 2>/dev/null | sed 's/^/  log: /'
    exit 4
    ;;

  status)
    if _driver_running; then
      echo "[ur-fake] driver RUNNING (pid $(cat "$PIDFILE" 2>/dev/null))"
    else
      echo "[ur-fake] driver NOT running"
    fi
    echo "--- controllers ---"; _controllers || echo "  (controller_manager not reachable)"
    echo "--- /joint_states (1 msg) ---"
    timeout 5 ros2 topic echo --once /joint_states 2>/dev/null | grep -E "name:|position:" | head -3 \
      || echo "  (no /joint_states)"
    ;;

  down)
    echo "[ur-fake] tearing down ..."
    # SIGINT the launch process group first (ScaledJointTrajectoryController can hang in
    # its destructor on a bare TERM), then escalate. Headless node, no X11 -> KILL is safe.
    if [ -f "$PIDFILE" ]; then
      PID="$(cat "$PIDFILE")"
      kill -INT "$PID" 2>/dev/null || true
      for _ in $(seq 1 10); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
      kill -0 "$PID" 2>/dev/null && kill -TERM "$PID" 2>/dev/null || true
      for _ in $(seq 1 5); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
      kill -0 "$PID" 2>/dev/null && kill -KILL "$PID" 2>/dev/null || true
      rm -f "$PIDFILE"
    fi
    # Reap any stragglers (ur_ros2_control_node, the launch, spawners).
    pkill -INT -f "[r]os2 launch ur_robot_driver" 2>/dev/null || true
    pkill -f "[u]r_ros2_control_node" 2>/dev/null || true
    echo "[ur-fake] down."
    ;;

  *)
    echo "usage: $0 {up|status|down}" >&2; exit 1 ;;
esac

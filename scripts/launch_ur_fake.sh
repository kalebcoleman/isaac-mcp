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
# ROS_LOCALHOST_ONLY=1 + the ROS domain from config/ros_dds.env (default 0)) as Isaac (launched via scripts/launch_sim.sh) and the
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

# Match the system ROS distribution to the simulator. Never guess the mock-hardware
# argument for an unsupported distro: an unrecognized launch argument can otherwise
# leave the driver in real-hardware mode.
if [ -z "${ROS_DISTRO:-}" ]; then
  case "$(. /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-}")" in
    jammy) ROS_DISTRO=humble ;;
    noble) ROS_DISTRO=jazzy ;;
    *) echo "ERROR: set ROS_DISTRO to humble or jazzy for the fake driver" >&2; exit 1 ;;
  esac
fi
case "$ROS_DISTRO" in
  humble) MOCK_ARG=use_fake_hardware ;;
  jazzy) MOCK_ARG=use_mock_hardware ;;
  *) echo "ERROR: fake driver does not support ROS_DISTRO=$ROS_DISTRO" >&2; exit 1 ;;
esac
ROS_SETUP="${ROS_SETUP:-/opt/ros/$ROS_DISTRO/setup.bash}"
if [ ! -f "$ROS_SETUP" ]; then
  echo "ERROR: ROS setup not found: $ROS_SETUP" >&2
  exit 1
fi
source "$ROS_SETUP"
source "$REPO/config/ros_dds.env"
# Optional overlay workspace (apt ur_robot_driver works without it; source if present).
[ -f "$HOME/ros2_ws/install/setup.bash" ] && source "$HOME/ros2_ws/install/setup.bash"
if ! command -v ros2 >/dev/null 2>&1; then
  echo "ERROR: ros2 is unavailable after sourcing $ROS_SETUP" >&2
  exit 1
fi

UR_TYPE="${UR_TYPE:-ur5e}"
LOG="${UR_FAKE_LOG:-/tmp/ur_fake_driver.log}"
PIDFILE="${UR_FAKE_PIDFILE:-/tmp/ur_fake_driver.pid}"
ACTION="${1:-up}"

if [ "$ACTION" != "down" ] && ! ros2 pkg prefix ur_robot_driver >/dev/null 2>&1; then
  echo "ERROR: ur_robot_driver is not installed in ROS $ROS_DISTRO. Install the matching ROS driver package and ros2controlcli." >&2
  exit 1
fi

# Bracket-trick ([r]os2) so the match never hits the calling shell.
_driver_running() { pgrep -f "[r]os2 launch ur_robot_driver" >/dev/null 2>&1; }

# A stale or replaced PID file must never be used to signal an unrelated process.
_managed_driver_running() {
  local pid="$1" cmdline process_env
  [[ "$pid" =~ ^[0-9]+$ ]] && [ -r "/proc/$pid/cmdline" ] || return 1
  cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline")" || return 1
  [[ "$cmdline" == *"/ros2 launch ur_robot_driver "* ]] || return 1
  [[ "$cmdline" == *" use_mock_hardware:=true "* || "$cmdline" == *" use_fake_hardware:=true "* ]] || return 1
  # A copied or stale PID file must not let one managed session stop another.
  # The launcher passes its unique session ID to the actual ros2 process.
  process_env="$(tr '\0' '\n' < "/proc/$pid/environ")" || return 1
  if [ -n "${ISAAC_MCP_SIM_SESSION_ID:-}" ]; then
    grep -Fxq -- "ISAAC_MCP_SIM_SESSION_ID=$ISAAC_MCP_SIM_SESSION_ID" <<<"$process_env"
  else
    # The legacy, unmanaged PID file must not claim a managed session's driver.
    ! grep -q '^ISAAC_MCP_SIM_SESSION_ID=' <<<"$process_env"
  fi
}

# ros2 control waits indefinitely for a missing controller manager. Bound every
# query so status works after shutdown and a failed startup can reach its timeout.
_controllers() { timeout -k 2s 5s ros2 control list_controllers 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g'; }
_controller_in_state() {
  local name="$1" state="$2" controllers="$3"
  grep -Eq "^${name}([[:space:]].*)?[[:space:]]${state}[[:space:]]*$" <<<"$controllers"
}

case "$ACTION" in
  up)
    if ! launch_args="$(ros2 launch ur_robot_driver ur_control.launch.py --show-args 2>&1)"; then
      echo "ERROR: could not inspect ur_robot_driver launch arguments: $launch_args" >&2
      exit 1
    fi
    if ! grep -Eq "^[[:space:]]*'$MOCK_ARG'[[:space:]]*:" <<<"$launch_args"; then
      echo "ERROR: driver does not advertise $MOCK_ARG; refusing to start without verified mock hardware" >&2
      exit 1
    fi
    if _driver_running; then
      PID="$(cat "$PIDFILE" 2>/dev/null || true)"
      if _managed_driver_running "$PID"; then
        echo "[ur-fake] already running (pid $PID); run '$0 down' first" >&2
      else
        echo "[ur-fake] another UR driver is already running; stop it before starting fake hardware" >&2
      fi
      exit 2
    fi
    echo "[ur-fake] launching $UR_TYPE fake hardware -> domain $ROS_DOMAIN_ID (Cyclone, localhost_only=$ROS_LOCALHOST_ONLY)"
    # A dedicated process group lets down clean up this driver's children without
    # touching another UR driver that happens to be running on the machine.
    nohup setsid ros2 launch ur_robot_driver ur_control.launch.py \
        ur_type:="$UR_TYPE" "$MOCK_ARG:=true" robot_ip:=0.0.0.0 launch_rviz:=false \
        > "$LOG" 2>&1 &
    echo "$!" > "$PIDFILE"
    echo "[ur-fake] pid $(cat "$PIDFILE")  log $LOG"

    # Both controllers must be active before claiming that motion is ready.
    echo "[ur-fake] waiting for joint states and the trajectory controller ..."
    switch_attempted=0
    for i in $(seq 1 30); do
      sleep 2
      if controllers="$(_controllers)"; then
        if _controller_in_state joint_state_broadcaster active "$controllers" && \
           _controller_in_state scaled_joint_trajectory_controller active "$controllers"; then
          echo "[ur-fake] READY: joint state broadcaster and trajectory controller active (after $i checks)"
          printf '%s\n' "$controllers" | sed 's/^/  ctrl: /'
          exit 0
        fi
        if [ "$switch_attempted" = 0 ] && \
           _controller_in_state joint_state_broadcaster active "$controllers" && \
           _controller_in_state scaled_joint_trajectory_controller inactive "$controllers"; then
          switch_attempted=1
          echo "[ur-fake] activating scaled_joint_trajectory_controller ..."
          timeout -k 2s 10s ros2 control switch_controllers --activate scaled_joint_trajectory_controller 2>/dev/null | tail -1 || true
        fi
      fi
      if [ -f "$PIDFILE" ] && ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "ERROR: ur_robot_driver exited; see $LOG" >&2; tail -20 "$LOG" | sed 's/^/  log: /'; exit 4
      fi
    done
    echo "ERROR: joint state broadcaster and trajectory controller not both active within timeout; see $LOG" >&2
    tail -20 "$LOG" 2>/dev/null | sed 's/^/  log: /'
    exit 4
    ;;

  status)
    PID="$(cat "$PIDFILE" 2>/dev/null || true)"
    if _managed_driver_running "$PID"; then
      echo "[ur-fake] driver RUNNING (pid $PID)"
    elif _driver_running; then
      echo "[ur-fake] another UR driver is running; it is not managed by this launcher"
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
    if [ -f "$PIDFILE" ]; then
      PID="$(cat "$PIDFILE" 2>/dev/null || true)"
      if _managed_driver_running "$PID"; then
        # New launches have their own group. Older launches may share a group
        # with the simulator, so signal only their verified launch PID.
        PGID="$(ps -o pgid= -p "$PID" | tr -d '[:space:]')"
        TARGET="$PID"
        [ "$PGID" = "$PID" ] && TARGET="-$PID"
        kill -INT -- "$TARGET" 2>/dev/null || true
        for _ in $(seq 1 10); do kill -0 -- "$TARGET" 2>/dev/null || break; sleep 1; done
        kill -0 -- "$TARGET" 2>/dev/null && kill -TERM -- "$TARGET" 2>/dev/null || true
        for _ in $(seq 1 5); do kill -0 -- "$TARGET" 2>/dev/null || break; sleep 1; done
        kill -0 -- "$TARGET" 2>/dev/null && kill -KILL -- "$TARGET" 2>/dev/null || true
      else
        echo "[ur-fake] no matching managed driver for pid '$PID'; leaving other processes alone"
      fi
      rm -f "$PIDFILE"
    fi
    echo "[ur-fake] down."
    ;;

  *)
    echo "usage: $0 {up|status|down}" >&2; exit 1 ;;
esac

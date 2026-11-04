#!/bin/bash
# Reference: Universal_Robots_ROS2_Driver + ur_client_library start_ursim.sh
#   https://github.com/UniversalRobots/Universal_Robots_ROS2_Driver
#
# Hands-free bring-up of the UR sim driver stack (URSim + ur_robot_driver),
# to the point where `/joint_states` flows and the driver is "Ready to receive
# control commands" — no PolyScope/browser clicks required.
#
# Verified working 2026-05-27 on host EN4226768 (ur5e, URSim 5.25.1, ROS Humble).
#
# The ordering matters and is the whole trick:
#   1. start URSim container          (dashboard server on :29999 comes up)
#   2. stage external_control.urp     (into the mounted programs dir)
#   3. dashboard: power on + brakes   (robot -> RUNNING)
#   4. launch ur_bringup driver       (its script-sender starts listening)
#   5. dashboard: load + play urp     (External Control connects back -> ready)
# Playing the program BEFORE the driver listens makes the URCap fail and stop.

set -uo pipefail

# ---- config (override via env / args) -----------------------------------
ACTION="${1:-up}"
MODE="${2:-${MODE:-sim}}"                   # sim (URSim docker) | real (lab robot)
MODEL="${MODEL:-ur5e}"
if [ "$MODE" = "real" ]; then
  ROBOT_IP="${ROBOT_IP:-192.168.1.111}"    # real UR5e on the lab network
else
  ROBOT_IP="${ROBOT_IP:-192.168.56.101}"   # URSim container IP on ursim_net
fi
DASH_HOST="${DASH_HOST:-127.0.0.1}"        # dashboard via forwarded :29999
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}" # MUST match every ROS client (see CLAUDE.md)
CONTAINER="${CONTAINER:-ursim}"
DRIVER_LOG="${DRIVER_LOG:-/tmp/ur_driver.log}"
DRIVER_PIDFILE="${DRIVER_PIDFILE:-/tmp/ur_driver.pgid}"
ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
WS_SETUP="${WS_SETUP:-$HOME/ros2_ws/install/setup.bash}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
# Canonical ROS2/DDS env (RMW + ROS_LOCALHOST_ONLY + Cyclone profile) so the
# driver — and this script's own ros2 calls — match the MCP consumer. Without
# it the driver discovery handshake never matches the consumer. See config file.
DDS_CONFIG="${DDS_CONFIG:-$REPO_DIR/config/ros_dds.env}"
DASH="python3 $SCRIPT_DIR/ur_dashboard.py --host $DASH_HOST"
BUNDLED_URP="$REPO_DIR/config/ursim/external_control.urp"
PROGRAM_DIR="${PROGRAM_DIR:-$HOME/.ursim/e-series/$MODEL/programs}"
CONTAINER_URP="/ursim/programs/external_control.urp"

log()  { printf '\033[0;36m[sim-bringup]\033[0m %s\n' "$*"; }
ok()   { printf '\033[0;32m  ok\033[0m %s\n' "$*"; }
err()  { printf '\033[0;31m  ERR\033[0m %s\n' "$*" >&2; }

source_ros() {
  # ROS setup scripts reference unbound vars; nounset would abort us silently.
  set +u
  source "$ROS_SETUP" 2>/dev/null
  source "$WS_SETUP" 2>/dev/null
  # DDS env AFTER the ROS setups (which leave RMW unset -> FastDDS default) so
  # Cyclone + ROS_LOCALHOST_ONLY=1 win. Matches the consumer; without it this
  # script's own ros2 topic/echo calls run on a different DDS island than the
  # driver and falsely report "/joint_states never appeared".
  [ -f "$DDS_CONFIG" ] && source "$DDS_CONFIG"
  set -u
}

# ---- steps ---------------------------------------------------------------
start_ursim() {
  if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    ok "URSim container '$CONTAINER' already running"
    return 0
  fi
  log "starting URSim ($MODEL) ..."
  ros2 run ur_client_library start_ursim.sh -m "$MODEL" -d >/tmp/start_ursim.log 2>&1
  if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    err "URSim container did not start (see /tmp/start_ursim.log)"; return 1
  fi
  ok "URSim container up"
}

stage_program() {
  mkdir -p "$PROGRAM_DIR"
  if [ ! -f "$BUNDLED_URP" ]; then
    err "bundled external_control.urp missing at $BUNDLED_URP"; return 1
  fi
  cp -f "$BUNDLED_URP" "$PROGRAM_DIR/external_control.urp"
  ok "staged external_control.urp -> $PROGRAM_DIR"
}

wait_dashboard() { $DASH --wait 120 wait_dashboard; }

power_up() { log "powering on + releasing brakes ..."; $DASH power_up; }

driver_running() { pgrep -f '/ur_ros2_control_node' >/dev/null 2>&1; }
joint_states_live() { timeout 4 ros2 topic echo --once /joint_states >/dev/null 2>&1; }

# Reap the driver reliably. The UR control node can hang/crash in its
# ScaledJointTrajectoryController destructor on SIGINT, so we poll and escalate.
# The node is headless (launch_rviz:=false) so SIGKILL is safe — no X11 window.
kill_driver() {
  local pgid=""
  [ -f "$DRIVER_PIDFILE" ] && pgid="$(cat "$DRIVER_PIDFILE" 2>/dev/null)"
  [ -n "$pgid" ] && kill -INT -- "-$pgid" 2>/dev/null
  pkill -INT -f 'ur5e[.]launch[.]py'   2>/dev/null
  pkill -INT -f '/ur_ros2_control_node' 2>/dev/null
  for _ in $(seq 1 15); do driver_running || { rm -f "$DRIVER_PIDFILE"; return 0; }; sleep 1; done
  pkill -TERM -f '/ur_ros2_control_node' 2>/dev/null
  for _ in $(seq 1 5);  do driver_running || { rm -f "$DRIVER_PIDFILE"; return 0; }; sleep 1; done
  pkill -KILL -f '/ur_ros2_control_node' 2>/dev/null
  pkill -KILL -f 'ur5e[.]launch[.]py'   2>/dev/null
  sleep 1; rm -f "$DRIVER_PIDFILE"
  driver_running && return 1 || return 0
}

launch_driver() {
  if driver_running; then
    if joint_states_live; then ok "ur_robot_driver already running (/joint_states live)"; return 0; fi
    # Fast-fail rather than silently reaping: a false negative here would destroy
    # a healthy driver. `down` reaps reliably, so make the operator decide.
    err "a ur_robot_driver is running but /joint_states is not live."
    err "refusing to silently reap it — run '$0 down $MODE' first, then '$0 up $MODE'."
    return 1
  fi
  log "launching ur_bringup (robot_ip=$ROBOT_IP, domain=$ROS_DOMAIN_ID, dds=$DDS_CONFIG) ..."
  setsid bash -c "source '$ROS_SETUP'; source '$WS_SETUP'; export ROS_DOMAIN_ID=$ROS_DOMAIN_ID; \
    [ -f '$DDS_CONFIG' ] && source '$DDS_CONFIG'; \
    exec ros2 launch ur_bringup ur5e.launch.py ur_type:=$MODEL robot_ip:=$ROBOT_IP \
    launch_rviz:=false headless_mode:=false" >"$DRIVER_LOG" 2>&1 &
  local pid=$!
  ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' >"$DRIVER_PIDFILE"
  ok "driver launching (pgid $(cat "$DRIVER_PIDFILE" 2>/dev/null), log: $DRIVER_LOG)"
}

wait_joint_states() {
  log "waiting for /joint_states ..."
  for i in $(seq 1 40); do
    if timeout 4 ros2 topic echo --once /joint_states >/dev/null 2>&1; then
      ok "/joint_states publishing"; return 0
    fi
    sleep 2
  done
  err "/joint_states never appeared (see $DRIVER_LOG)"; return 1
}

start_external() { log "loading + playing external_control.urp ..."; $DASH start_external "$CONTAINER_URP"; }

wait_reverse_iface() {
  log "waiting for reverse interface (control commands ready) ..."
  for i in $(seq 1 20); do
    if grep -q 'Ready to receive control commands' "$DRIVER_LOG" 2>/dev/null; then
      ok "driver ready to receive control commands"; return 0
    fi
    sleep 1
  done
  err "reverse interface not confirmed (External Control may not have connected)"; return 1
}

cmd_up() {
  case "$MODE" in
    sim)  cmd_up_sim ;;
    real) cmd_up_real ;;
    *) err "unknown mode '$MODE' (expected sim|real)"; exit 2 ;;
  esac
}

cmd_up_sim() {
  source_ros
  # Clear any stale ros2 CLI daemon: a daemon left over from an earlier ros2
  # call under a different RMW/domain caches a corrupt node graph and makes the
  # `ros2 topic echo /joint_states` readiness gates below see nothing (the topic
  # is actually live — rclpy consumers find it fine). Daemon auto-restarts clean
  # under the env we just sourced.
  ros2 daemon stop >/dev/null 2>&1 || true
  start_ursim       || exit 1
  stage_program     || exit 1
  wait_dashboard    || exit 1
  power_up          || exit 1
  launch_driver     || exit 1
  wait_joint_states || exit 1
  start_external    || exit 1
  wait_reverse_iface|| exit 1
  echo
  log "SIM DRIVER STACK READY"
  echo "  robot_ip=$ROBOT_IP  ROS_DOMAIN_ID=$ROS_DOMAIN_ID"
  echo "  /joint_states live; scaled_joint_trajectory_controller ready."
  echo "  tear down with: $0 down"
}

# real mode is intentionally NOT wired yet — it must be verified in the lab
# (real UR5e + OnRobot gripper + ArUco camera). The exact sequence is recorded
# here so enabling it later is a one-line flip (set FORCE_REAL=1), but by default
# it refuses to run so we never ship an unverified robot-motion path.
cmd_up_real() {
  source_ros
  log "REAL mode bring-up sequence (robot_ip=$ROBOT_IP, domain=$ROS_DOMAIN_ID):"
  echo "    1) ros2 launch ur_bringup ur5e.launch.py ur_type:=$MODEL robot_ip:=$ROBOT_IP"
  echo "       (power on / brake release / load+play External Control done on the REAL pendant)"
  echo "    2) ros2 run onrobot_ros gripper_control"
  echo "    3) ros2 run aruco_camera_localizer localize"
  if [ "${FORCE_REAL:-0}" != "1" ]; then
    err "real mode not yet verified in-lab — refusing to run. Re-run with FORCE_REAL=1 when at the robot."
    exit 3
  fi
  log "FORCE_REAL=1 set — launching real robot stack ..."
  ros2 daemon stop >/dev/null 2>&1 || true   # clear stale CLI daemon (see cmd_up_sim)
  launch_driver        || exit 1   # against the real ROBOT_IP, no URSim/dashboard
  wait_joint_states    || exit 1
  setsid bash -c "source '$ROS_SETUP'; source '$WS_SETUP'; export ROS_DOMAIN_ID=$ROS_DOMAIN_ID; [ -f '$DDS_CONFIG' ] && source '$DDS_CONFIG'; exec ros2 run onrobot_ros gripper_control" >/tmp/onrobot_gripper.log 2>&1 &
  ok "gripper_control launched (log: /tmp/onrobot_gripper.log)"
  setsid bash -c "source '$ROS_SETUP'; source '$WS_SETUP'; export ROS_DOMAIN_ID=$ROS_DOMAIN_ID; [ -f '$DDS_CONFIG' ] && source '$DDS_CONFIG'; exec ros2 run aruco_camera_localizer localize" >/tmp/aruco_localizer.log 2>&1 &
  ok "aruco localizer launched (log: /tmp/aruco_localizer.log)"
  echo; log "REAL DRIVER STACK LAUNCHED (verify topics manually)"
}

cmd_down() {
  source_ros
  log "stopping driver ..."
  if kill_driver; then ok "driver stopped"; else err "driver may still be running"; fi
  if [ "$MODE" = "real" ]; then
    pkill -INT -f 'onrobot_ros gripper_control'        2>/dev/null && ok "gripper_control stopped"
    pkill -INT -f 'aruco_camera_localizer localize'    2>/dev/null && ok "aruco localizer stopped"
  fi
  if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    log "stopping URSim container ..."; docker stop "$CONTAINER" >/dev/null 2>&1 && ok "URSim stopped"
  fi
}

cmd_status() {
  if ! command -v docker >/dev/null 2>&1; then
    err "Docker is not installed or not on PATH, so URSim (backend='ursim') cannot run."
    err "Use backend='fake' for a sim-only driver, or install Docker to use URSim."
    exit 4
  fi
  if ! docker info >/dev/null 2>&1; then
    err "Docker is installed but not reachable (is dockerd running, and is this user allowed to use it?), so URSim (backend='ursim') cannot run."
    err "Use backend='fake' for a sim-only driver, or fix Docker access to use URSim."
    exit 4
  fi
  source_ros
  echo "container: $(docker ps --format '{{.Names}} {{.Status}}' | grep "$CONTAINER" || echo 'not running')"
  echo "driver:    $(driver_running && echo running || echo 'not running')"
  # 'status' shows the LOADED program only; print run state too — a stopped
  # external_control means the driver accepts goals that never execute
  # (silent motion-dead sim, cost a run on 2026-06-11).
  if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    $DASH status || true
    $DASH running state || true
  fi
}

case "$ACTION" in
  up)     cmd_up ;;
  down)   cmd_down ;;
  status) cmd_status ;;
  *) echo "usage: $0 [up|down|status] [sim|real]   (env: MODEL ROBOT_IP ROS_DOMAIN_ID DASH_HOST CONTAINER FORCE_REAL)"; exit 2 ;;
esac

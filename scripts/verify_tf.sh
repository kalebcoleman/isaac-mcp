#!/bin/bash
# Phase 3e: boot the twin headless, build the objects + camera TF publishers, play, and record
# /objects_poses_sim and /tf_cam with scripts/tf_capture.py. Output: $OUT/tf_after.json
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${UR5E_DT_PORT:-8799}"; OUT="${PROBE_OUT:-${TMPDIR:-/tmp}/isaac-mcp-probe}"; PY="${ISAAC_MCP_PYTHON:-${UR5E_DT_PYTHON:-$ROOT/.venv/bin/python}}"
export OMNI_KIT_ACCEPT_EULA=YES ISAAC_MCP_PORT="$PORT" ISAAC_MCP_DEFER_START=1 UR5E_DT_PORT="$PORT" UR5E_DT_HEADLESS=1 PYTHONDONTWRITEBYTECODE=1
export ROS_DISTRO="${ROS_DISTRO:-humble}" RMW_IMPLEMENTATION=rmw_cyclonedds_cpp PATH="$HOME/.local/bin:$PATH"
# Canonical DDS settings (localhost-only + Cyclone profile) so the sim and tf_capture.py discover each other.
. "$ROOT/config/ros_dds.env"
SITE="$("$PY" -c 'import isaacsim,os;print(os.path.dirname(isaacsim.__file__))')"
. "$ROOT/scripts/isaac_ros_libs.sh"
ROS_LIB="$(isaac_ros_lib_dir "$SITE" "$ROS_DISTRO")" && export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}$ROS_LIB"
mkdir -p "$OUT"; LOG="$OUT/verify_tf_app.log"
setsid "$PY" "$ROOT/scripts/run_ur5e_dt.py" --ext-folder "$ROOT/exts" --enable isaac.mcp.bridge --enable ur5e-dt --enable isaacsim.ros2.bridge > "$LOG" 2>&1 &
APP=$!
# The whole group: a standalone app's python.sh does not exec, so its Kit outlives the wrapper, and can ignore SIGTERM.
trap 'kill -- -$APP 2>/dev/null; sleep 3; kill -9 -- -$APP 2>/dev/null; wait $APP 2>/dev/null' EXIT
for _ in $(seq 1 45); do (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null && break; sleep 2; done
c() { ( cd "$ROOT/mcp" && PYTHONPATH="$PWD" timeout 200 "$PY" -m isaac_mcp.client "$@" 2>&1 | tail -4 ); }
c ur5e_dt.quick_start_with_workstation '{}' --timeout 150
c ur5e_dt.add_objects '{}' --timeout 100
c ur5e_dt.setup_pose_publisher '{}' --timeout 60
CAM='import ur5e_dt.extension as x; e=x._EXT_INSTANCE; e.import_realsense_camera(); e.attach_camera_to_ur5e(); e.setup_camera_action_graph(); print("camera graph requested")'
c run_python "{\"code\": $(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$CAM")}"
c ur5e_dt.play_scene '{}' --timeout 60
sleep 4
capture_rc=0; "$PY" "$ROOT/scripts/tf_capture.py" --out "$OUT/tf_after.json" --duration 5 || capture_rc=$?; echo "capture rc=$capture_rc"
echo "PoseTree warnings: $(grep -c 'PoseTree' "$LOG")"; grep -iE '\[Error\]' "$LOG" | cut -c1-200 | head -8
exit "$capture_rc"

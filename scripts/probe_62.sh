#!/bin/bash
# Boot Isaac Sim headless, list the twin's MCP tools, build the workstation scene,
# and run the check suite.
#
#   scripts/probe_62.sh                 # use the repository extensions
#   scripts/probe_62.sh --no-checks     # boot + tool list + quick start only
#   ISAAC_MCP_PYTHON=... scripts/probe_62.sh
#
# The probe never modifies the scene on disk. It kills the app on exit.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${UR5E_DT_PORT:-8799}"
OUT="${PROBE_OUT:-${TMPDIR:-/tmp}/isaac-mcp-probe}"
LOG="$OUT/probe_62_app.log"
RUN_CHECKS=1
[ "${1:-}" = "--no-checks" ] && RUN_CHECKS=0

mkdir -p "$OUT"
# Must precede the first `import isaacsim`, which otherwise blocks on the EULA prompt.
export OMNI_KIT_ACCEPT_EULA=YES

# ---- interpreter -------------------------------------------------------------------
# This probe requires Isaac Sim 6.x.
pick_py() {
  cand="$ROOT/.venv/bin/python"
  [ -x "$cand" ] || return 1
  v="$("$cand" -c 'import importlib.metadata as m;print(m.version("isaacsim"))' 2>/dev/null)" || return 1
  case "$v" in 6.*) printf '%s\n' "$cand"; return 0 ;; *) return 1 ;; esac
}

_PY_ENV="${ISAAC_MCP_PYTHON:-${UR5E_DT_PYTHON:-}}"
if [ -n "$_PY_ENV" ]; then
  PY="$_PY_ENV"
else
  PY="$(pick_py)" || {
    echo "FAIL: no interpreter with Isaac Sim 6.x found." >&2
    echo "      Tried $ROOT/.venv." >&2
    echo "      Run: uv sync --python 3.12   (or set ISAAC_MCP_PYTHON)" >&2
    exit 1
  }
fi

if ! "$PY" -c 'import isaacsim' 2>/dev/null; then
  echo "FAIL: $PY cannot import isaacsim." >&2
  exit 1
fi

. "$ROOT/scripts/isaac_ros_libs.sh"
ISAAC_VER="$(isaac_sim_version "$PY")"
case "$ISAAC_VER" in
  6.*) ;;
  *) echo "FAIL: $PY has isaacsim $ISAAC_VER; this probe needs 6.x." >&2; exit 1 ;;
esac

# ---- ext folder layout -------------------------------------------------------------
if [ ! -d "$ROOT/exts/ur5e-dt" ]; then
  echo "FAIL: cannot find ur5e-dt under exts/." >&2
  exit 1
fi
EXT_ARGS=(--ext-folder "$ROOT/exts")

# ---- ROS / Kit env -----------------------------------------------------------------
SITE="$("$PY" -c 'import isaacsim,os;print(os.path.dirname(isaacsim.__file__))')"
if [ -z "${ROS_DISTRO:-}" ]; then
  case "$(. /etc/os-release; echo "${VERSION_CODENAME:-}")" in jammy) ROS_DISTRO=humble ;; *) ROS_DISTRO=jazzy ;; esac
fi
export ROS_DISTRO
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
. "$ROOT/scripts/isaac_ros_libs.sh"
ROS_LIB="$(isaac_ros_lib_dir "$SITE" "$ROS_DISTRO")" && export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}$ROS_LIB"
export ISAAC_MCP_PORT="$PORT"  # shared endpoint, opened after the twin registers
export ISAAC_MCP_DEFER_START=1
export UR5E_DT_PORT="$PORT"
export UR5E_DT_HEADLESS=1
export PYTHONDONTWRITEBYTECODE=1
export PATH="$HOME/.local/bin:$PATH"

echo "py        : $PY"
echo "isaacsim  : $ISAAC_VER"
echo "ros       : $ROS_DISTRO"
echo "port      : $PORT"

if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then
  echo "FAIL: port $PORT already in use; stop the running app first." >&2
  exit 1
fi

# ---- boot --------------------------------------------------------------------------
echo "== boot"
"$PY" "$ROOT/scripts/run_ur5e_dt.py" \
  "${EXT_ARGS[@]}" --enable isaac.mcp.bridge --enable ur5e-dt \
  --enable isaacsim.ros2.bridge > "$LOG" 2>&1 &
APP_PID=$!
cleanup() {
  kill "$APP_PID" 2>/dev/null; pkill -P "$APP_PID" 2>/dev/null
  # Kit ignores SIGTERM while it is busy shutting down; escalate so the probe cannot hang on exit.
  for _ in 1 2 3 4 5; do kill -0 "$APP_PID" 2>/dev/null || break; sleep 1; done
  kill -9 "$APP_PID" 2>/dev/null; pkill -9 -P "$APP_PID" 2>/dev/null
  wait "$APP_PID" 2>/dev/null
}
trap cleanup EXIT

for _ in $(seq 1 45); do
  (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null && break
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "FAIL: app exited during boot. Last 30 log lines:" >&2
    tail -30 "$LOG" >&2
    exit 1
  fi
  sleep 2
done
if ! (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then
  echo "FAIL: bridge port $PORT never opened (90s). Last 30 log lines:" >&2
  tail -30 "$LOG" >&2
  exit 1
fi
echo "   bridge up"

# ---- MCP plumbing ------------------------------------------------------------------
MCP_DIR="$ROOT/mcp"
client() { ( cd "$MCP_DIR" && PYTHONPATH="$PWD" timeout 120 "$PY" -m isaac_mcp.client "$@" ); }

echo "== tools"
client list_tools 2>/dev/null > "$OUT/probe_62_tools.json"
"$PY" - "$OUT/probe_62_tools.json" "$OUT/probe_62_toolnames.txt" <<'PY'
import json, sys
raw = open(sys.argv[1]).read()
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    print("   FAIL: list_tools did not return JSON"); print(raw[:400]); sys.exit(1)
tools = data.get("tools", data)
names = sorted(tools)
twin = [n for n in names if n.startswith("ur5e_dt.")]
print(f"   total={len(names)}  ur5e_dt.*={len(twin)}  generic={len(names)-len(twin)}")
print("   (the host server exposes these to MCP clients as ur5e_dt__<name>)")
for n in names:
    print(f"     {n}")
open(sys.argv[2], "w").write("\n".join(names) + "\n")
PY

# ---- scene -------------------------------------------------------------------------
echo "== quick_start_with_workstation"
client ur5e_dt.quick_start_with_workstation '{}' --timeout 180 2>&1 | tail -15

if [ "$RUN_CHECKS" = 1 ]; then
  for check in scene_prims gravity_robot_chain joint_limits drive_settings single_joint_motion; do
    echo "== check: $check"
    "$PY" "$ROOT/scripts/bridge_exec.py" "$ROOT/scripts/checks/$check.py" 2>&1 | tail -8
  done
fi

echo "== app errors"
grep -cE '\[Error\]' "$LOG" || true
echo "   app log: $LOG"
echo "== done"

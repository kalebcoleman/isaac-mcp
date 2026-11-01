#!/bin/bash
# run.sh — primary entry point to launch Isaac Sim 6.2 with the MCP bridge.
#
# Replaces the old 5.1 launcher (scripts/launch_ur5e_dt.sh is now a shim to this file).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${UR5E_DT_PORT:-${ISAAC_SIM_PORT:-8766}}"
HEADLESS=0
UR5E_DT=0
SHOW_HELP=0
EXTRA_ARGS=()

# Resolve interpreter (defaults to .venv/bin/python, can be overridden by UR5E_DT_PYTHON)
if [ -n "${UR5E_DT_PYTHON:-}" ]; then
  PY="$UR5E_DT_PYTHON"
else
  PY="$ROOT/.venv/bin/python"
fi

# Parse arguments
while [ "$#" -gt 0 ]; do
  case "$1" in
    --headless)
      HEADLESS=1
      shift
      ;;
    --ur5e-dt)
      UR5E_DT=1
      shift
      ;;
    --port)
      [ "$#" -ge 2 ] || { echo "--port needs a value" >&2; exit 2; }
      PORT="$2"
      shift 2
      ;;
    -h|--help)
      SHOW_HELP=1
      shift
      ;;
    --)
      shift
      EXTRA_ARGS+=("$@")
      break
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

if [ "$SHOW_HELP" = 1 ]; then
  cat <<EOF
Usage: ./run.sh [options] [-- <extra kit args>]

Launch Isaac Sim 6.2 with the Kit MCP bridge; optionally enable the UR5e extension.

Configuration:
  Port:        $PORT (override with UR5E_DT_PORT or --port)
  Interpreter: $PY (override with UR5E_DT_PYTHON)

Options:
  --headless     Run without visible window (sets UR5E_DT_HEADLESS=1)
  --ur5e-dt      Enable the UR5e panel and its tools
  --port N       Bridge port (default: 8766)
  -h, --help     Show this help message and exit
EOF
  exit 0
fi

# Refuse to start on a busy bridge port
if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then
  echo "bridge port $PORT is already in use; choose a free port with --port or UR5E_DT_PORT" >&2
  exit 1
fi

export UR5E_DT_PORT="$PORT"

# One shared bridge serves all packs. When the twin is requested, defer binding
# until it has registered, preserving the launcher's socket-readiness contract.
export ISAAC_MCP_PORT="$PORT"
if [ "$UR5E_DT" = 1 ]; then
  export ISAAC_MCP_DEFER_START=1
else
  export ISAAC_MCP_DEFER_START=0
fi
export OMNI_KIT_ACCEPT_EULA=YES
export PYTHONDONTWRITEBYTECODE=1
export PATH="$HOME/.local/bin:$PATH"

if [ "$HEADLESS" = 1 ]; then
  export UR5E_DT_HEADLESS=1
fi

# Isaac Sim bundled ROS 2 bridge: humble on Ubuntu 22.04 (jammy), jazzy on 24.04 (noble).
if [ -z "${ROS_DISTRO:-}" ]; then
  case "$(. /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-}")" in
    jammy) ROS_DISTRO=humble ;;
    *)     ROS_DISTRO=jazzy ;;
  esac
fi
export ROS_DISTRO
# Canonical DDS env (Cyclone, localhost-only, no multicast). Every ROS client of the sim must use the
# same file or discovery silently fails (a mixed ROS_LOCALHOST_ONLY setup publishes into the void).
if [ -f "$ROOT/config/ros_dds.env" ]; then
  # shellcheck disable=SC1091
  . "$ROOT/config/ros_dds.env"
else
  export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
fi

# Verify interpreter
if [ ! -x "$PY" ]; then
  echo "FAIL: interpreter $PY not found or not executable." >&2
  echo "      Run ./install.sh first or set UR5E_DT_PYTHON." >&2
  exit 1
fi

if ! "$PY" -c 'import isaacsim' 2>/dev/null; then
  echo "FAIL: $PY cannot import isaacsim." >&2
  exit 1
fi

SITE="$("$PY" -c 'import isaacsim,os;print(os.path.dirname(isaacsim.__file__))')"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}$SITE/exts/isaacsim.ros2.core/$ROS_DISTRO/lib"

EXT_ARGS=(--ext-folder "$ROOT/exts")
TWIN_ARGS=()
if [ "$UR5E_DT" = 1 ]; then
  TWIN_ARGS=(--enable ur5e-dt)
fi

exec "$PY" "$ROOT/scripts/run_ur5e_dt.py" \
  "${EXT_ARGS[@]}" --enable isaac.mcp.bridge "${TWIN_ARGS[@]}" \
  --enable isaacsim.ros2.bridge "${EXTRA_ARGS[@]}"

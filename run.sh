#!/bin/bash
# run.sh — primary entry point to launch Isaac Sim 6.2 with the MCP bridge.
#
# Replaces the old 5.1 launcher (scripts/launch_ur5e_dt.sh is now a shim to this file).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${UR5E_DT_PORT:-${ISAAC_SIM_PORT:-8766}}"
HEADLESS=0
SHOW_UR5E_PANEL="${UR5E_DT_SHOW_PANEL:-0}"
PHYSICS_BACKEND="${ISAAC_MCP_PHYSICS_BACKEND:-physx}"
SHOW_HELP=0
EXTRA_ARGS=()

# Interpreter: ISAAC_MCP_PYTHON (preferred) or UR5E_DT_PYTHON (alias), else the checkout's
# .venv, else auto-detection by scripts/isaac_env.py (resolved after argument parsing).
PY=""
PY_AUTODETECT=0
if [ -n "${ISAAC_MCP_PYTHON:-}" ]; then
  PY="$ISAAC_MCP_PYTHON"
elif [ -n "${UR5E_DT_PYTHON:-}" ]; then
  PY="$UR5E_DT_PYTHON"
elif [ -e "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
else
  PY_AUTODETECT=1
fi

# Parse arguments
while [ "$#" -gt 0 ]; do
  case "$1" in
    --headless)
      HEADLESS=1
      shift
      ;;
    --motion-bvh)
      EXTRA_ARGS+=("--/renderer/raytracingMotion/enabled=true"
                   "--/renderer/raytracingMotion/enableHydraEngineMasking=true"
                   "--/renderer/raytracingMotion/enabledForHydraEngines=0,1,2,3,4")
      shift
      ;;
    --physics-backend)
      [ "$#" -ge 2 ] || { echo "--physics-backend needs physx or newton" >&2; exit 2; }
      PHYSICS_BACKEND="$2"
      shift 2
      ;;
    --no-hot-reload)
      ISAAC_MCP_NO_HOT_RELOAD=1
      shift
      ;;
    --ur5e-panel|--ur5e-dt)
      SHOW_UR5E_PANEL=1
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

if [ "$PY_AUTODETECT" = 1 ]; then
  if command -v python3 >/dev/null 2>&1 && [ -f "$ROOT/scripts/isaac_env.py" ]; then
    # Any Isaac Sim 6.x; the first match in the documented search order wins.
    # Inherit stderr rather than opening /dev/stderr, which fails when fd 2 is a socket (journald, some CI).
    if [ "$SHOW_HELP" = 1 ]; then
      DETECTED="$(python3 "$ROOT/scripts/isaac_env.py" resolve-python --require-version 6 2>/dev/null)" || DETECTED=""
    else
      DETECTED="$(python3 "$ROOT/scripts/isaac_env.py" resolve-python --require-version 6)" || DETECTED=""
    fi
    if [ -n "$DETECTED" ]; then
      PY="$DETECTED"
      if [ "$SHOW_HELP" != 1 ]; then
        echo "run.sh: auto-detected interpreter $PY (pin one with ISAAC_MCP_PYTHON=/path/to/python; list all with scripts/isaac_env.py find)" >&2
      fi
    fi
  fi
  [ -n "$PY" ] || PY="$ROOT/.venv/bin/python"
fi

if [ "${ISAAC_MCP_PRINT_PYTHON:-0}" = 1 ]; then
  echo "$PY"
  exit 0
fi

if [ "$SHOW_HELP" = 1 ]; then
  if [ -x "$PY" ]; then HELP_PY="$PY"; else HELP_PY="$PY (not found; list candidates with: python3 scripts/isaac_env.py find)"; fi
  cat <<EOF
Usage: ./run.sh [options] [-- <extra kit args>]

Launch Isaac Sim with the Kit MCP bridge and UR5e tools (6.2 by default; other builds by ISAAC_MCP_PYTHON).

Configuration:
  Port:        $PORT (override with UR5E_DT_PORT or --port)
  Interpreter: $HELP_PY
               (override with ISAAC_MCP_PYTHON; UR5E_DT_PYTHON is an alias; without either, the
               checkout .venv is used, else scripts/isaac_env.py auto-detects an existing install)

Options:
  --headless     Run without visible window (sets UR5E_DT_HEADLESS=1)
  --motion-bvh   Enable RTX sensor motion compensation (increases GPU memory use)
  --physics-backend physx|newton  Select this new session's engine (default: physx)
  --no-hot-reload  Do not reload extensions when their files change (or set ISAAC_MCP_NO_HOT_RELOAD=1)
  --ur5e-panel   Show the UR5e panel (the tools are always enabled)
  --ur5e-dt      Compatibility alias for --ur5e-panel
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
case "$PHYSICS_BACKEND" in
  physx|newton) export ISAAC_MCP_PHYSICS_BACKEND="$PHYSICS_BACKEND" ;;
  *) echo "--physics-backend must be physx or newton" >&2; exit 2 ;;
esac

# Defer binding until the twin tools register, so bridge readiness means the
# default pack is ready whether or not its panel is visible.
export ISAAC_MCP_PORT="$PORT"
export ISAAC_MCP_DEFER_START=1
export UR5E_DT_SHOW_PANEL="$SHOW_UR5E_PANEL"
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
  echo "      Run ./install.sh first or set ISAAC_MCP_PYTHON (UR5E_DT_PYTHON also works)." >&2
  echo "      To see what was searched: python3 scripts/isaac_env.py find (see docs/LINUX-SETUP.md)." >&2
  exit 1
fi

if ! "$PY" -c 'import isaacsim' 2>/dev/null; then
  echo "FAIL: $PY cannot import isaacsim." >&2
  exit 1
fi

SITE="$("$PY" -c 'import isaacsim,os;print(os.path.dirname(isaacsim.__file__))')"
# Refuse here rather than after Kit has started: 5.1 has no Newton extension, and never fall back to PhysX.
# Extensions sit in <site>/exts in a pip install and two levels above the package in a standalone app.
if [ "$PHYSICS_BACKEND" = newton ] \
    && [ ! -d "$SITE/exts/isaacsim.physics.newton" ] && [ ! -d "$(dirname "$(dirname "$SITE")")/exts/isaacsim.physics.newton" ]; then
  echo "FAIL: this Isaac Sim ($SITE) has no isaacsim.physics.newton extension. Nothing was started and there is no PhysX fallback." >&2
  echo "      Use --physics-backend physx, or an interpreter with Newton (see docs/COMPATIBILITY.md)." >&2
  exit 2
fi
. "$ROOT/scripts/isaac_ros_libs.sh"
if ROS_LIB="$(isaac_ros_lib_dir "$SITE" "$ROS_DISTRO")"; then
  export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}$ROS_LIB"
else
  echo "run.sh: WARNING: no ROS 2 $ROS_DISTRO libraries bundled under $SITE or its install root;" >&2
  echo "        the ROS 2 bridge will not start. Set ROS_DISTRO or LD_LIBRARY_PATH if yours are elsewhere." >&2
fi

EXT_ARGS=(--ext-folder "$ROOT/exts")

# Kit watches extension folders and reloads an extension when a file in it changes, inside the running
# process. Turn that off for a recorded run, or when another process edits this checkout.
case "${ISAAC_MCP_NO_HOT_RELOAD:-0}" in
  1|true|yes) EXT_ARGS+=("--/app/extensions/fsWatcherEnabled=0") ;;
esac

exec "$PY" "$ROOT/scripts/run_ur5e_dt.py" \
  "${EXT_ARGS[@]}" --enable isaac.mcp.bridge --enable ur5e-dt --enable sim-tools \
  --enable isaacsim.ros2.bridge "${EXTRA_ARGS[@]}"

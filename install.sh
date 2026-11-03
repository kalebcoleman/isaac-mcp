#!/bin/bash
# Layout: mcp/ is the host MCP package, exts/ holds the Kit extensions (isaac.mcp.bridge, ur5e-dt),
# config/ and scripts/ hold the launch/check helpers (see docs/ARCHITECTURE.md).
#
# install.sh — idempotent one-time setup for isaac-mcp.
#
# Usage:
#   ./install.sh [options]
#
# Options:
#   --no-sim          Skip the 20 GB Isaac Sim environment sync (installs host MCP server only)
#   --download-sim    Create the locked 6.2 environment even when an Isaac Sim is already installed
#   --claude          Configure both Claude Code and Claude Desktop
#   --claude-code     Configure Claude Code only
#   --claude-desktop  Configure Claude Desktop only
#   --print-opencode  Print configuration snippet for OpenCode
#   --port N          Twin bridge port for MCP client configuration (default: 8766)
#   --isaaclab-python PATH  Isaac Lab venv python -> ISAACLAB_PYTHON in client configs ($ISAACLAB_PYTHON by default)
#   --isaaclab-root PATH    Isaac Lab checkout -> ISAACLAB_ROOT in client configs ($ISAACLAB_ROOT by default)
#   --accept-eula     Acknowledge the NVIDIA Omniverse EULA (sets OMNI_KIT_ACCEPT_EULA=YES)
#   -h, --help       Show this help message and exit
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8766
DISPLAY_NAME="${DISPLAY:-:0}"
NO_SIM=0
DOWNLOAD_SIM=0
DO_CLAUDE_CODE=0
DO_CLAUDE_DESKTOP=0
DO_PRINT_OPENCODE=0
# Optional Isaac Lab install for the isaaclab__* tools (defaults to the same env vars the tools read).
LAB_PYTHON="${ISAACLAB_PYTHON:-}"
LAB_ROOT="${ISAACLAB_ROOT:-}"

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Idempotent setup for the UR5e digital twin repository.

Options:
  --no-sim          Skip Isaac Sim environment sync (host MCP server only)
  --download-sim    Download the locked Isaac Sim 6.2 environment into .venv even when an
                    Isaac Sim 6.x is already installed (by default an existing install is used)
  --claude          Configure both Claude Code and Claude Desktop
  --claude-code     Configure Claude Code only
  --claude-desktop  Configure Claude Desktop only
  --print-opencode  Print configuration snippet for OpenCode
  --port N          Twin bridge port for MCP client configuration (default: 8766)
  --isaaclab-python PATH  Isaac Lab venv python, written to client configs as ISAACLAB_PYTHON
                          (default: $ISAACLAB_PYTHON if set)
  --isaaclab-root PATH    Isaac Lab checkout, written as ISAACLAB_ROOT (default: $ISAACLAB_ROOT if set)
  --accept-eula    Acknowledge NVIDIA Omniverse EULA (sets OMNI_KIT_ACCEPT_EULA=YES)
  -h, --help        Show this help message and exit
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-sim)
      NO_SIM=1
      shift
      ;;
    --download-sim)
      DOWNLOAD_SIM=1
      shift
      ;;
    --claude)
      DO_CLAUDE_CODE=1
      DO_CLAUDE_DESKTOP=1
      shift
      ;;
    --claude-code)
      DO_CLAUDE_CODE=1
      shift
      ;;
    --claude-desktop)
      DO_CLAUDE_DESKTOP=1
      shift
      ;;
    --print-opencode)
      DO_PRINT_OPENCODE=1
      shift
      ;;
    --port)
      [ "$#" -ge 2 ] || { echo "--port needs a value" >&2; exit 2; }
      PORT="$2"
      shift 2
      ;;
    --isaaclab-python)
      [ "$#" -ge 2 ] || { echo "--isaaclab-python needs a value" >&2; exit 2; }
      LAB_PYTHON="$2"
      shift 2
      ;;
    --isaaclab-root)
      [ "$#" -ge 2 ] || { echo "--isaaclab-root needs a value" >&2; exit 2; }
      LAB_ROOT="$2"
      shift 2
      ;;
    --accept-eula)
      export OMNI_KIT_ACCEPT_EULA=YES
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [ "$NO_SIM" = 1 ] && [ "$DOWNLOAD_SIM" = 1 ]; then
  echo "--no-sim and --download-sim cannot be combined" >&2
  exit 2
fi

# Use an Isaac Sim that is already installed (a lab PC's, another environment's) instead of
# downloading ~20 GB. Only the checkout's own .venv is re-synced without asking, because that is
# this installer's environment. run.sh finds the same install at launch.
if [ "$NO_SIM" = 0 ] && [ "$DOWNLOAD_SIM" = 0 ] && [ ! -x "$ROOT/.venv/bin/python" ]; then
  if command -v python3 >/dev/null 2>&1 \
     && EXISTING="$(python3 "$ROOT/scripts/isaac_env.py" resolve-python --require-version 6 2>/dev/null)" \
     && [ -n "$EXISTING" ]; then
    echo "== using the Isaac Sim already installed: $EXISTING"
    echo "   (no download; run.sh finds it at launch. Pass --download-sim to install the locked 6.2 environment instead.)"
    NO_SIM=1
  fi
fi

echo "== checking preconditions"

# Check uv
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

if [ "$NO_SIM" = 0 ]; then
  # 1. OS check: Ubuntu 22.04 / 24.04, x86_64
  codename="$(. /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-unknown}")"
  arch="$(uname -m)"
  if [ "$arch" != "x86_64" ] || { [ "$codename" != "jammy" ] && [ "$codename" != "noble" ]; }; then
    echo "Isaac Sim 6.2 supports Ubuntu 22.04/24.04 x86_64" >&2
    exit 1
  fi

  # 2. NVIDIA GPU + driver
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "nvidia-smi not found: install the NVIDIA driver" >&2
    exit 1
  fi
  gpu_info="$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | head -1 || true)"
  if [ -z "$gpu_info" ]; then
    echo "nvidia-smi found but reports no GPU" >&2
    exit 1
  fi
  echo "NVIDIA GPU and driver: $gpu_info"
  drv="$(echo "$gpu_info" | awk -F', ' '{print $NF}' | cut -d. -f1)"
  case "$gpu_info" in
    *Blackwell*|*"RTX 50"*) min_drv=580 ;;
    *)                      min_drv=570 ;;
  esac
  if [ "${drv:-0}" -lt "$min_drv" ] 2>/dev/null; then
    echo "WARNING: driver ${drv:-unknown} is below the tested floor (>= $min_drv for $gpu_info)" >&2
  fi

  # 3. Free disk >= 40 GB
  free_gb="$(df -BG --output=avail "$ROOT" | tail -1 | tr -dc '0-9')"
  echo "free disk: ${free_gb} GB"
  if [ "${free_gb:-0}" -lt 40 ]; then
    echo "free disk: ${free_gb:-0} GB (need at least 40 GB for Isaac Sim)" >&2
    exit 1
  fi

  # 4. EULA acknowledged
  if [ "${OMNI_KIT_ACCEPT_EULA:-}" != "YES" ]; then
    echo "NVIDIA Omniverse EULA must be acknowledged: set OMNI_KIT_ACCEPT_EULA=YES" >&2
    exit 1
  fi
fi

# Step 1: Sync Isaac Sim environment (if not --no-sim)
if [ "$NO_SIM" = 0 ]; then
  echo "== syncing Isaac Sim 6.2 environment (Python 3.12)"
  uv sync --python 3.12
fi

# Step 2: Install host MCP package (editable)
MCP_DIR="$ROOT/mcp"
if [ ! -d "$MCP_DIR" ]; then
  echo "FAIL: MCP host package directory not found at $MCP_DIR" >&2
  exit 1
fi

echo "== installing isaac-mcp (editable) from $MCP_DIR"
uv tool install --force --editable "${MCP_DIR}[isaaclab]"   # [isaaclab] adds tensorboard for the metrics tools

# Step 3: Install ffmpeg shim at ~/.local/bin/ffmpeg
BIN_DIR="${HOME}/.local/bin"
mkdir -p "$BIN_DIR"
FFMPEG_SHIM="$BIN_DIR/ffmpeg"
if [ -L "$FFMPEG_SHIM" ] || { [ -e "$FFMPEG_SHIM" ] && ! grep -Fq '# ffmpeg shim: prefer the static build shipped with imageio-ffmpeg.' "$FFMPEG_SHIM" 2>/dev/null; }; then
  echo "== preserving existing $FFMPEG_SHIM (not an isaac-mcp shim)" >&2
else
  echo "== installing ffmpeg shim at $FFMPEG_SHIM"
  cat > "$FFMPEG_SHIM" <<'SH'
#!/bin/bash
# ffmpeg shim: prefer the static build shipped with imageio-ffmpeg.
# The Isaac Sim ur5e-dt extension shells out to a bare `ffmpeg`; this keeps it
# working on machines with no system package. Re-resolved on every call so a
# version bump in any uv tool env cannot leave a dangling path.
set -euo pipefail
BIN="$(ls -1 "$HOME"/.local/share/uv/tools/*/lib/python*/site-packages/imageio_ffmpeg/binaries/ffmpeg-* 2>/dev/null | sort -V | tail -1 || true)"
if [ -z "$BIN" ]; then
  for candidate in /usr/bin/ffmpeg /usr/local/bin/ffmpeg /snap/bin/ffmpeg; do
    if [ -x "$candidate" ]; then
      exec "$candidate" "$@"
    fi
  done
  echo "ffmpeg shim: no ffmpeg found (install imageio-ffmpeg or system ffmpeg)" >&2
  exit 127
fi
exec "$BIN" "$@"
SH
  chmod +x "$FFMPEG_SHIM"
fi

# Step 4: Verify CLI help runs
echo "== verifying CLI"
if [ -x "$BIN_DIR/isaac-mcp" ]; then
  CLI_BIN="$BIN_DIR/isaac-mcp"
elif command -v isaac-mcp >/dev/null 2>&1; then
  CLI_BIN="$(command -v isaac-mcp)"
else
  echo "FAIL: isaac-mcp not found in $BIN_DIR or PATH" >&2
  exit 1
fi

"$CLI_BIN" --help >/dev/null
echo "  verified $CLI_BIN --help runs"

# Optional client configurations
if [ "$DO_CLAUDE_CODE" = 1 ]; then
  if command -v claude >/dev/null 2>&1; then
    echo "== configuring Claude Code (isaac on port $PORT)"
    claude mcp remove --scope user isaac >/dev/null 2>&1 || true
    claude mcp remove --scope user twin >/dev/null 2>&1 || true
    lab_env=()
    [ -n "$LAB_PYTHON" ] && lab_env+=(-e "ISAACLAB_PYTHON=$LAB_PYTHON")
    [ -n "$LAB_ROOT" ] && lab_env+=(-e "ISAACLAB_ROOT=$LAB_ROOT")
    claude mcp add --scope user isaac \
      -e "DISPLAY=$DISPLAY_NAME" -e "UR5E_DT_PORT=$PORT" "${lab_env[@]}" \
      -- "$CLI_BIN"
  else
    echo "== claude CLI not found; skipped Claude Code configuration" >&2
  fi
fi

if [ "$DO_CLAUDE_DESKTOP" = 1 ]; then
  echo "== configuring Claude Desktop (isaac on port $PORT)"
  PORT="$PORT" DISPLAY_NAME="$DISPLAY_NAME" CLI_PATH="$CLI_BIN" LAB_PYTHON="$LAB_PYTHON" LAB_ROOT="$LAB_ROOT" python3 - <<'PY'
import json
import os
import shutil
import sys
import time

port = os.environ["PORT"]
display = os.environ["DISPLAY_NAME"]
cli_path = os.environ["CLI_PATH"]
path = os.path.expanduser("~/.config/Claude/claude_desktop_config.json")
if not os.path.exists(path):
    print(f"Claude Desktop config not found at {path}; skipped")
    sys.exit(0)

with open(path, encoding="utf-8") as f:
    config = json.load(f)
env = {"UR5E_DT_PORT": port, "DISPLAY": display}
if os.environ.get("LAB_PYTHON"):
    env["ISAACLAB_PYTHON"] = os.environ["LAB_PYTHON"]
if os.environ.get("LAB_ROOT"):
    env["ISAACLAB_ROOT"] = os.environ["LAB_ROOT"]
config.setdefault("mcpServers", {})["isaac"] = {
    "command": cli_path,
    "args": [],
    "env": env,
}
backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(path, backup)
tmp = f"{path}.tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
print(f"updated {path}")
print(f"backup  {backup}")
print("restart Claude Desktop to load the new server")
PY
fi

if [ "$DO_PRINT_OPENCODE" = 1 ]; then
  lab_json=""
  [ -n "$LAB_PYTHON" ] && lab_json+=", \"ISAACLAB_PYTHON\": \"$LAB_PYTHON\""
  [ -n "$LAB_ROOT" ] && lab_json+=", \"ISAACLAB_ROOT\": \"$LAB_ROOT\""
  cat <<EOF

OpenCode entry (~/.config/opencode/opencode.jsonc):

  "isaac": {
    "type": "local",
    "command": ["isaac-mcp"],
    "environment": { "UR5E_DT_PORT": "$PORT", "DISPLAY": "$DISPLAY_NAME"$lab_json }
  }
EOF
fi

echo "== install complete"

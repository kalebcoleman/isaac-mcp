#!/bin/bash
# verify.sh — root entry point to run the UR5e digital twin check suite.
#
# Thin wrapper over scripts/verify_ur5e_dt.sh with version-agreement assertion
# enforcing Isaac Sim 6.2.0.0 across environments (§3).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${UR5E_DT_PORT:-${ISAAC_MCP_PORT:-8766}}"

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  cat <<'EOF'
Usage: ./verify.sh [options]

Verify the single-arm workstation scene through the live Kit MCP bridge.
Asserts Isaac Sim version agreement (6.2.0.0) before running checks.

Environment variables:
  UR5E_DT_PORT      Twin bridge port (default: 8766)
  ISAAC_MCP_PYTHON  Python interpreter to use (default: .venv/bin/python; UR5E_DT_PYTHON is a deprecated alias)
  LOG               Path to Isaac Sim app log (for [Error] line counting)
  REBUILD           Set to 1 to run rebuild stress test
EOF
  exit 0
fi

# Pick interpreter: ISAAC_MCP_PYTHON (or its alias UR5E_DT_PYTHON) or repo .venv
_PY_ENV="${ISAAC_MCP_PYTHON:-${UR5E_DT_PYTHON:-}}"
if [ -n "$_PY_ENV" ]; then
  PY="$_PY_ENV"
else
  PY="$ROOT/.venv/bin/python"
fi

if [ ! -x "$PY" ]; then
  echo "FAIL: interpreter $PY not found or not executable." >&2
  echo "      Run ./install.sh first or set ISAAC_MCP_PYTHON." >&2
  exit 1
fi

# Version agreement rule (§3):
# Asserts importlib.metadata.version("isaacsim") == "6.2.0.0" in whichever
# interpreter the twin is launched with.
ISAAC_VER="$("$PY" -c 'import importlib.metadata as m; print(m.version("isaacsim"))' 2>/dev/null || true)"
if [ "$ISAAC_VER" != "6.2.0.0" ]; then
  echo "FAIL: $PY has isaacsim version '${ISAAC_VER:-unknown}', expected '6.2.0.0'" >&2
  exit 1
fi
echo "verified isaacsim version: $ISAAC_VER in $PY"

export UR5E_DT_PORT="$PORT"
export UR5E_DT_PYTHON="$PY"

VERIFY_SCRIPT="$ROOT/scripts/verify_ur5e_dt.sh"
if [ ! -f "$VERIFY_SCRIPT" ]; then
  echo "FAIL: verification script not found at $VERIFY_SCRIPT" >&2
  exit 1
fi

exec "$VERIFY_SCRIPT" "$@"

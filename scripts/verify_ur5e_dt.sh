#!/bin/bash
# Verify the single-arm workstation scene through the live Kit MCP bridge.
# Start the app first (scripts/launch_ur5e_dt.sh), then run this from any directory.
# Prints each check's result and the number of [Error] lines in the app log, if LOG is set.
set -u
# Discovery is unreliable across Isaac versions; the twin bridge port is fixed.
export UR5E_DT_PORT="${UR5E_DT_PORT:-${ISAAC_MCP_PORT:-8766}}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${UR5E_DT_PYTHON:-$ROOT/.venv/bin/python}"
BRIDGE="$ROOT/mcp"
export_reference_if_missing() {
  [ -f "$ROOT/checkpoints/$(python3 -c "import json;print(json.load(open('$ROOT/checkpoints/ur5e-dt-workstation-person-view-portable.json'))['checkpoint'])")" ] || run_check export_reference
}
FAILED=()
run_check() {
  echo "== $1"
  local out status=0 executor=bridge_exec.py
  if [ "$1" = single_joint_motion ]; then executor=bridge_async_check.py; fi
  out="$("$PY" "$ROOT/scripts/$executor" "$ROOT/scripts/checks/$1.py" 2>&1)" || status=$?
  echo "$out"
  # The client can also exit 0 when the bridge answers with an error.
  if [ "$status" -ne 0 ] || grep -qE '"error_type"|^Traceback|^\[Errno|"status": "error"' <<<"$out"; then
    FAILED+=("$1")
  elif ! printf '%s\n' "$out" | "$PY" "$ROOT/scripts/validate_ur5e_check.py" "$1"; then
    FAILED+=("$1")
  fi
}
# First call after a cold boot compiles shaders and caches assets: 2+ minutes on a fresh machine, ~3 s afterwards.
run_quick_start() {
  local out status=0
  out="$(cd "$BRIDGE" && PYTHONPATH=$PWD "$PY" -m isaac_mcp.client ur5e_dt.quick_start_with_workstation '{}' --timeout 300 2>&1)" || status=$?
  echo "$out"
  if [ "$status" -ne 0 ] || ! printf '%s\n' "$out" | "$PY" "$ROOT/scripts/validate_ur5e_check.py" quick_start; then
    FAILED+=("quick_start")
  fi
}
echo "== quick_start (1st)"; run_quick_start
if [ "${REBUILD:-0}" = "1" ]; then  # opt-in stress test: rebuilding a loaded scene logs Kit viewport UI errors
  sleep 10
  echo "== quick_start (2nd, rebuild)"; run_quick_start
fi
run_check scene_prims
run_check gravity_robot_chain
run_check joint_limits
run_check drive_settings
export_reference_if_missing
run_check reopen_checkpoint
sleep 10
run_check scene_prims
run_check single_joint_motion
if [ -n "${LOG:-}" ]; then
  echo "== [Error] lines in app log: $(grep -c '\[Error\]' "$LOG")"
  echo "== [Force callback] lines in app log: $(grep -c 'Force callback' "$LOG")"
fi
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "== FAILED: ${FAILED[*]}"
  exit 1
fi
echo "== all checks passed"

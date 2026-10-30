#!/bin/bash
# Verify the single-arm workstation scene through the live Kit MCP bridge.
# Start the app first (scripts/launch_ur5e_dt.sh), then run this from any directory.
# Prints each check's result and the number of [Error] lines in the app log, if LOG is set.
set -u
# Discovery is unreliable across Isaac versions; the twin bridge port is fixed.
export UR5E_DT_PORT="${UR5E_DT_PORT:-${ISAAC_MCP_PORT:-8766}}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
BRIDGE="$ROOT/mcp"
export_reference_if_missing() {
  [ -f "$ROOT/checkpoints/$(python3 -c "import json;print(json.load(open('$ROOT/checkpoints/ur5e-dt-workstation-person-view-portable.json'))['checkpoint'])")" ] || run_check export_reference
}
FAILED=()
run_check() {
  echo "== $1"
  local out; out="$("$PY" "$ROOT/scripts/bridge_exec.py" "$ROOT/scripts/checks/$1.py" 2>&1)"
  echo "$out"
  # bridge_exec.py exits 0 even when the bridge answers with an error, so read the answer.
  if grep -qE '"error_type"|^Traceback|^\[Errno|"status": "error"' <<<"$out"; then FAILED+=("$1"); fi
}
run_quick_start() {
  (cd "$BRIDGE" && PYTHONPATH=$PWD "$PY" -m isaac_mcp.client ur5e_dt.quick_start_with_workstation '{}' --timeout 90) || FAILED+=("quick_start")
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

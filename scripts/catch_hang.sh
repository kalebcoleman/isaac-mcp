#!/bin/bash
# Loop "boot headless -> quick_start_with_workstation" until it hangs, then keep the evidence.
# Known flake: quick_start occasionally never returns on the first call after boot (seen twice on
# 2026-10-29; usually takes ~5 s). Run on a machine with an NVIDIA GPU and the 6.2 env (./install.sh).
#
#   scripts/catch_hang.sh [iterations=20] [per-call timeout seconds=120]
#
# On a hang it writes $OUT/hang-<n>/ with the app log, a process snapshot, nvidia-smi and (if py-spy is
# installed) a Python stack dump of the Kit process, then stops. Exit 0 = no hang in N runs, 1 = caught one.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
N="${1:-20}"; TMO="${2:-120}"; PORT="${UR5E_DT_PORT:-8793}"; OUT="${OUT:-/tmp/isaac-mcp-hang}"
PY="${ISAAC_MCP_PYTHON:-$ROOT/.venv/bin/python}"
export OMNI_KIT_ACCEPT_EULA=YES UR5E_DT_PORT="$PORT"
mkdir -p "$OUT"
for i in $(seq 1 "$N"); do
  log="$OUT/run-$i.log"
  "$ROOT/run.sh" --headless > "$log" 2>&1 &
  app=$!
  for _ in $(seq 1 60); do (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null && break; sleep 2; done
  start=$(date +%s)
  if ( cd "$ROOT/mcp" && PYTHONPATH="$PWD" timeout "$TMO" "$PY" -m isaac_mcp.client \
        ur5e_dt.quick_start_with_workstation '{}' --timeout "$TMO" >/dev/null 2>&1 ); then
    echo "run $i: ok in $(( $(date +%s) - start )) s"
    kill -9 "$app" $(pgrep -P "$app") 2>/dev/null; wait "$app" 2>/dev/null; sleep 3
  else
    d="$OUT/hang-$i"; mkdir -p "$d"; cp "$log" "$d/app.log"
    ps -eo pid,ppid,pcpu,etime,args --sort=-pcpu | head -15 > "$d/ps.txt"
    nvidia-smi > "$d/nvidia-smi.txt" 2>&1
    kit_pid=$(pgrep -f "run_ur5e_dt.py" | head -1)
    command -v py-spy >/dev/null && [ -n "$kit_pid" ] && py-spy dump --pid "$kit_pid" > "$d/pyspy.txt" 2>&1
    echo "run $i: HANG (no answer in ${TMO}s). Evidence in $d"
    kill -9 "$app" $(pgrep -P "$app") 2>/dev/null
    exit 1
  fi
done
echo "no hang in $N runs"

#!/bin/bash
# Compatibility shim: the launcher now lives in ./run.sh (Isaac Sim 6.2, Python 3.12).
# Kept so existing docs and muscle memory (UR5E_DT_PORT=8766 scripts/launch_ur5e_dt.sh) keep working.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/run.sh" --ur5e-dt "$@"

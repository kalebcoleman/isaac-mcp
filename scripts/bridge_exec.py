"""Run a Python file inside the live Isaac Sim through the Kit MCP bridge and print the result.

The file runs on Kit's main thread with omni, carb and pxr preloaded. Assign to `result` to return a value.

    .venv/bin/python scripts/bridge_exec.py scripts/checks/scene_prims.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'mcp'
code = Path(sys.argv[1]).read_text().replace('__ROOT__', str(ROOT))
command = [sys.executable, '-m', 'isaac_mcp.client', 'run_python', json.dumps({'code': code}), '--timeout', '90']
env = {**os.environ, 'PYTHONPATH': str(BRIDGE)}
if 'UR5E_DT_PORT' not in env and 'ISAAC_MCP_PORT' not in env:
    env['UR5E_DT_PORT'] = '8766'
raise SystemExit(subprocess.run(command, cwd=BRIDGE, env=env, check=False).returncode)

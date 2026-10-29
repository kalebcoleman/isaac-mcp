"""Start Isaac Sim with a visible window and keep it running until closed.

Kit extension arguments are passed through by run.sh. The app stays up so
the Kit MCP bridge can be driven from another terminal (kit_mcp.client).
"""
import sys

from isaacsim import SimulationApp

import os

app = SimulationApp({'headless': os.getenv('UR5E_DT_HEADLESS') == '1', 'width': 1280, 'height': 720})
while app.is_running():
    app.update()
app.close()
sys.exit(0)

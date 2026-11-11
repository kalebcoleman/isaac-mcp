"""An Isaac Lab standalone script with the bridge in it. Copy it and put your own loop at the end.

A Lab script starts its own Kit app, so three things differ from the Isaac Sim app:

* The bridge is enabled by this script through ``--kit_args``; what ``scripts/install.py`` stores
  for the Isaac Sim app does not apply here.
* It is configured by environment variables only, set before the app starts: ``ISAAC_MCP_PORT``,
  ``ISAAC_MCP_TOOL_PATHS``, ``ISAAC_MCP_TOOL_MODULES``.
* The script owns the main loop, and the bridge answers only while Kit updates. On Lab 3.0.0
  ``sim.step()`` updates Kit only with a windowed Kit visualizer, so the loop below calls
  ``simulation_app.update()`` itself, with physics playback switched off around it so Kit does not
  advance physics a second time.

Every bridge needs its own port. The Isaac Sim app usually has 8766, so this defaults to 8767.
"""

import argparse
import os

os.environ.setdefault("ISAAC_MCP_PORT", "8767")
# Your own tool package, as for Isaac Sim:
# os.environ.setdefault("ISAAC_MCP_TOOL_PATHS", "<project>/isaac")
# os.environ.setdefault("ISAAC_MCP_TOOL_MODULES", "myproj_tools")

# This file is in <repo>/mcp/templates; ISAAC_MCP_EXTS names the extensions folder if yours is elsewhere.
EXTENSIONS = os.path.normpath(os.environ.get(
    "ISAAC_MCP_EXTS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "exts")))

from isaaclab.app import AppLauncher  # noqa: E402

parser = argparse.ArgumentParser(description="An Isaac Lab script with the MCP bridge.")
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()
# Kit takes "--enable <name>" with a space; "--enable=<name>" is ignored without an error.
args.kit_args = f"{args.kit_args or ''} --ext-folder {EXTENSIONS} --enable isaac.mcp.bridge".strip()
simulation_app = AppLauncher(args).app

# Isaac Lab imports come after the app has started.
import carb.settings  # noqa: E402
import isaaclab.sim as sim_utils  # noqa: E402

sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=1 / 120))
sim.reset()
_settings = carb.settings.get_settings()

while simulation_app.is_running():
    sim.step()
    _settings.set("/app/player/playSimulations", False)
    simulation_app.update()      # bridge requests are served here
    _settings.set("/app/player/playSimulations", True)

simulation_app.close()

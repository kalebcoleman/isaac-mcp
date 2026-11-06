# isaaclab_bridge_launch.py — template: wire the isaac.mcp.bridge bridge into an
# Isaac Lab STANDALONE script (train/play/custom env loop).
#
# Isaac Lab differs from the Isaac Sim app in three ways that change the setup:
#   1. The script owns the Kit app (AppLauncher + Lab's isaaclab.python.*.kit
#      experience), so scripts/install.py's persistent registration does NOT
#      apply — enable the bridge per-script via --kit_args, as below.
#   2. The script owns the main loop: bridge commands execute only while Kit
#      updates. On Lab 3.0.0 sim.step() and env.step() update Kit only through a
#      windowed Kit visualizer, so call simulation_app.update() in your loop. A
#      request sent while the script blocks stalls until the next update.
#   3. Configuration is env-var only (no carb persistence): ISAAC_MCP_PORT,
#      ISAAC_MCP_TOOL_PATHS, ISAAC_MCP_TOOL_MODULES.
#
# Everything else — protocol, tool packages, canonical names, the isaac-mcp
# front-end — is identical to the Isaac Sim setup (see repo README).

import argparse
import os

# Bridge config must be in the environment BEFORE the app launches.
# A Lab standalone run is its OWN Kit process — "one bridge, one port, per Kit
# process" means each concurrently-running process needs its own port. The
# installed Isaac Sim app typically holds 8766; Lab runs take 8767 by convention.
os.environ.setdefault("ISAAC_MCP_PORT", "8767")
# Optional: autoload your project's tool package(s), same contract as Isaac Sim:
# os.environ.setdefault("ISAAC_MCP_TOOL_PATHS", "<project>/isaac")
# os.environ.setdefault("ISAAC_MCP_TOOL_MODULES", "myproj_tools")

# The repository's exts folder: this file lives in <repo>/mcp/templates/. Override with
# ISAAC_MCP_EXTS if you keep the extensions elsewhere.
_ISAAC_MCP_EXTS = os.environ.get(
    "ISAAC_MCP_EXTS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "exts")
)
_ISAAC_MCP_EXTS = os.path.normpath(_ISAAC_MCP_EXTS)

from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description="My Lab script, bridge-enabled.")
# ... your own arguments here ...
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()

# Enable the bridge at boot. If the user passed their own --kit_args, append.
# Kit args use the SPACE form ("--enable isaac.mcp.bridge"); the "=" form is
# silently ignored for --enable. To also livestream to a remote viewer, add:
#   LIVESTREAM=2 in env, and "--/app/livestream/publicEndpointAddress=<box-ip>"
#   to kit_args — the PUBLIC_IP env var alone does NOT set the endpoint, and an
#   empty endpoint yields an SDP with no ICE candidates (track connects, no
#   frames ever arrive).
_bridge_args = f"--ext-folder {_ISAAC_MCP_EXTS} --enable isaac.mcp.bridge"
args.kit_args = f"{args.kit_args} {_bridge_args}".strip() if args.kit_args else _bridge_args

app_launcher = AppLauncher(args)
simulation_app = app_launcher.app
# Bridge is now listening on ISAAC_MCP_PORT (run_python + your tools).

# ---- your normal Isaac Lab code below; imports must come after app launch ----
import carb.settings
import isaaclab.sim as sim_utils

sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=1 / 120))
sim.reset()
_settings = carb.settings.get_settings()

while simulation_app.is_running():
    sim.step()
    # Bridge requests dispatch here. Lab 3.0.0's sim.step() runs Kit's update only through a windowed Kit
    # visualizer; headless (--viz kit with HEADLESS=1, or --viz none) it does not, and the bridge hangs.
    # Lab guards its own updates this way so that Kit does not also advance physics.
    _settings.set("/app/player/playSimulations", False)
    simulation_app.update()
    _settings.set("/app/player/playSimulations", True)

simulation_app.close()

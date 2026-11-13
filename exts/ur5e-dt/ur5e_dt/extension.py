"""Pack-backed UR5e workcell bridge extension."""

import os

import omni.ext
from isaac_mcp_bridge import ToolDefinition, get_mcp_bridge, knobs

from .bridge_binding import OWNER_ID, attach
from .commands import Commands

MCP_TOOL_REGISTRY = {
    "workcell_quick_start": {
        "description": "Build a stopped PhysX workcell from an external asset pack. Refuses unsaved stage changes unless explicitly allowed.",
        "parameters": {
            "config_path": {"type": "string", "description": "Optional hardware configuration path."},
            "mode": {"type": "string", "enum": ["physics", "mirror_real"], "default": "physics", "description": "Physics mode, or zero-gravity mirror mode."},
            "discard_changes": {"type": "boolean", "default": False, "description": "Allow replacement of an unsaved stage."},
        },
    },
    "workcell_status": {"description": "Read the current workcell build report.", "parameters": {}},
    "workcell_shutdown": {"description": "Release workcell state without clearing the stage.", "parameters": {}},
    "robot_status": {"description": "Read robot build details from the workcell report.", "parameters": {}},
    "perception_status": {"description": "Read camera build details; this does not capture sensor data.", "parameters": {}},
    "jenga_status": {"description": "Read Jenga build details from the workcell report.", "parameters": {}},
    "real2sim_status": {"description": "Report the real-to-simulation interface availability; no synchronization is implemented.", "parameters": {}},
    "planning_status": {"description": "Report the planning interface availability; no planner is implemented.", "parameters": {}},
    "eval_status": {"description": "Report the evaluation interface availability; no evaluation is implemented.", "parameters": {}},
}


class DigitalTwin(Commands, omni.ext.IExt):
    def on_startup(self, ext_id):
        from .scene import Workcell

        self.workcell = Workcell()
        self._mcp = None
        try:
            bridge = get_mcp_bridge()
            requested_port = os.getenv("UR5E_DT_PORT") or os.getenv("ISAAC_SIM_PORT")
            configured_port = knobs.PORT.read()
            attach(bridge, MCP_TOOL_REGISTRY, self, ToolDefinition,
                   requested_port=int(requested_port) if requested_port is not None else None,
                   configured_port=int(configured_port) if configured_port is not None else None,
                   host=knobs.BIND.read() or "localhost")
            self._mcp = bridge
        except Exception:
            self.workcell.shutdown()
            raise

    def on_shutdown(self):
        bridge = getattr(self, "_mcp", None)
        self._mcp = None
        if bridge is not None:
            bridge.unregister_owner(OWNER_ID, on_drained=self.workcell.shutdown)

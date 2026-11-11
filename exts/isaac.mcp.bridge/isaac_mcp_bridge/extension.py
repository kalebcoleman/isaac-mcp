"""Kit lifecycle for the bridge: create it at startup, load any configured tool packages, and listen.

The port comes from ``ISAAC_MCP_PORT`` or the stored ``autostartPort`` setting. With
``ISAAC_MCP_DEFER_START=1`` the socket is left closed for a tool pack to open once its tools are
registered (the twin's launcher does this), so a client never reaches a half-registered simulator.
"""

import os

import carb
import omni.ext

from . import knobs
from .bridge import destroy_bridge, get_mcp_bridge
from .packages import load_tool_modules
from .source_state import note_start


class McpBridgeExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        started = note_start("isaac.mcp.bridge")
        if started["reload"]:
            carb.log_warn(f"[isaac.mcp.bridge] extension reloaded in place (reload {started['reload']} in this "
                          "process): a file in its folder changed, or it was toggled. Clients must reconnect.")
        bridge = get_mcp_bridge()
        self._stage_events = self._clear_sessions_on_stage_open(bridge)
        try:
            loaded = load_tool_modules(bridge, knobs.listed(knobs.TOOL_MODULES.read()), knobs.listed(knobs.TOOL_PATHS.read()))
            port = self._port()
            host = knobs.BIND.read() or "localhost"
            if os.getenv("ISAAC_MCP_DEFER_START") == "1":
                if port is None:
                    raise RuntimeError("ISAAC_MCP_DEFER_START=1 needs ISAAC_MCP_PORT (0 for any free port)")
                carb.log_info(f"[isaac.mcp.bridge] waiting for a tool pack to open {host}:{port}")
            elif port is not None:
                bridge.start(port, host=host)
            elif loaded:
                raise RuntimeError("tool packages are configured but no port is: set ISAAC_MCP_PORT")
            else:
                carb.log_warn("[isaac.mcp.bridge] no port configured, so nothing is listening. Set ISAAC_MCP_PORT.")
        except BaseException as exc:
            # Kit does not call on_shutdown after a failed startup.
            carb.log_error(f"[isaac.mcp.bridge] startup failed: {exc}")
            self._stage_events = None
            destroy_bridge()
            raise

    @staticmethod
    def _port():
        value = knobs.PORT.read()
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            raise RuntimeError(f"ISAAC_MCP_PORT is not a port number: {value!r}")

    @staticmethod
    def _clear_sessions_on_stage_open(bridge):
        """Saved run_python variables point into the stage that was open; drop them when it changes."""
        try:
            import omni.usd

            return omni.usd.get_context().get_stage_event_stream().create_subscription_to_pop_by_type(
                int(omni.usd.StageEventType.OPENED), lambda _event: bridge.clear_sessions(),
                name="isaac.mcp.bridge: clear run_python sessions on stage open")
        except ImportError:
            return None

    def on_shutdown(self):
        from .builtins import remove_stdout_proxy

        self._stage_events = None
        remove_stdout_proxy()
        destroy_bridge()

"""twinsmith.bridge: tools registered by Kit extensions, served to MCP clients over a local socket.

    from twinsmith_bridge import get_mcp_bridge, ToolDefinition

    registrar = get_mcp_bridge().register_owner("my-ext", "my_ns")
    registrar.add(ToolDefinition(name="hello", description="Say hello", handler=lambda: {"hi": True}))
"""

from . import knobs

knobs.apply_legacy_aliases()      # old variable names keep working for one release (see mcp/twinsmith/legacy.py)
from .bridge import McpBridge, OwnerRegistrar, ToolDefinition, destroy_bridge, get_mcp_bridge
from .packages import load_tool_modules, reload_tool_module
from .protocol import ToolError

__all__ = ["McpBridge", "OwnerRegistrar", "ToolDefinition", "ToolError", "destroy_bridge", "get_mcp_bridge", "knobs",
           "load_tool_modules", "reload_tool_module"]

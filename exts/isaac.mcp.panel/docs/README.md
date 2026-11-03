# isaac.mcp.panel

Registry-driven control panel for the `isaac.mcp.bridge` bridge. One collapsible
section per namespace, one row per registered tool, parameter widgets generated
from each tool's bridge-served field specs (`isaac_mcp_bridge.schema`). Install once; every project's tools appear
automatically when their packages register — nothing per-project, nothing
generated on disk.

Every button dispatches through `bridge.dispatch(...)` — the same entrance a
socket request takes — so a human click and an agent call are the same code
path by construction.

See `mcp/README.md` at the repository root for the full setup.

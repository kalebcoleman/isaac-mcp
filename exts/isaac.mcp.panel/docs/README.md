# isaac.mcp.panel

The "MCP Tools" window in Kit: one section per tool namespace and a Run button per tool, drawn
from the bridge's tool listing. A click is submitted to the bridge like a request from the
socket, so it queues behind running calls and obeys read-only mode.

Setup is in `mcp/README.md` at the repository root.

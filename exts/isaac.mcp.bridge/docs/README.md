# isaac.mcp.bridge

The part of isaac-mcp that runs inside Kit. It keeps a registry of tools, listens on a local TCP
port, and runs each requested tool on Kit's main loop. Tool packs (the scene tools, the UR5e twin,
your own package) register under a namespace and are served as `<namespace>.<tool>`.

Its own tools: `status`, `run_python`, `job_status`, `job_cancel`, `list_tools`,
`reload_tools`, `put_file`, `get_file` and `stat_file`.

Connections must present a token (clients on the same account find it automatically), and
`ISAAC_MCP_READ_ONLY=1` starts a bridge that runs only tools which change nothing.

- Protocol, scheduling, token and read-only mode: `mcp/docs/protocol.md`
- Settings: `docs/ENVIRONMENT.md`
- Writing tools: `mcp/docs/tool-authoring.md`

# isaac.mcp.bridge

Generic MCP socket bridge for Omniverse Kit. One bridge, one port, per Kit
process: projects are plain tool packages (`MCP_NAMESPACE` + an optional
`register(registrar)` — without one, the package's public functions register
directly as a declared toolset) autoloaded by the bridge and advertised canonically as
`<namespace>.<tool>` over an NDJSON-over-TCP protocol, with every handler
dispatched on Kit's main thread. Builtins: `run_python` (the escape hatch),
`list_tools` (discovery), `reload_tools` (hot reload), `list_bridges`
(this box's advertised bridges), `put_file` / `get_file` / `stat_file` (remote file push/pull/verify),
`status` (instance identity: pid, port, headless-vs-headful, app version — multi-instance verification),
`job_status` / `job_cancel` (background jobs started by `run_python` with `background=true` or by a tool pack).

See `mcp/README.md` at the repository root for setup and the project contract, and
`mcp/docs/protocol.md` for the protocol.

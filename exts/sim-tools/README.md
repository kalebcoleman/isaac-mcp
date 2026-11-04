# sim-tools extension

Generic, robot-agnostic scene tools for Isaac Sim. It registers the `sim.*` and `assets.*` namespaces on the shared
`isaac.mcp.bridge`, and MCP clients see them as `sim__*` and `assets__*` through the `isaac-mcp` host. `./run.sh`
enables it. To use it elsewhere, enable `sim-tools` next to `isaac.mcp.bridge` from this checkout's `exts/` folder.

The tool contracts live in `mcp/isaac_mcp/scene_contracts.py`, and the host lists them from a packaged catalog.
After changing a contract, run `python3 scripts/gen_host_catalog.py --write` and
`python3 scripts/gen_tool_reference.py --write`.

- Behavior, refusals and limits: [docs/TOOLS-SCENE.md](../../docs/TOOLS-SCENE.md)
- Arguments: [docs/TOOLS-ARGUMENTS.md](../../docs/TOOLS-ARGUMENTS.md)

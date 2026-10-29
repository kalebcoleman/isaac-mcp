# isaac-mcp (npm launcher)

```bash
npx -y isaac-mcp --version
```

MCP client config:

```json
{ "mcpServers": { "ur5e": { "command": "npx", "args": ["-y", "isaac-mcp"],
  "env": { "DISPLAY": ":1", "UR5E_DT_PORT": "8766" } } } }
```

This package only launches the Python server through [uv](https://docs.astral.sh/uv/) (`uvx`). The server
source is `mcp/` in <https://github.com/kalebcoleman/ur5e-digital-twin>. Set `TWIN_MCP_FROM=isaac-mcp` to use
the PyPI release instead of the GitHub source.

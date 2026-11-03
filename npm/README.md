# isaac-mcp (npm launcher)

This launcher is not published yet. After publication:

```bash
npx -y isaac-mcp --version
```

MCP client config:

```json
{ "mcpServers": { "isaac": { "command": "npx", "args": ["-y", "isaac-mcp"],
  "env": { "DISPLAY": ":0", "UR5E_DT_PORT": "8766" } } } }
```

This package only launches the Python server through [uv](https://docs.astral.sh/uv/) (`uvx`). The server
source is `mcp/` in the public repository <https://github.com/kalebcoleman/isaac-mcp>. Set `TWIN_MCP_FROM=isaac-mcp` to use the PyPI release once it is published.

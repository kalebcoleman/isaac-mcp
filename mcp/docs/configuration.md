# Configuring the bridge

Each setting is an environment variable for one launch, and a stored Kit setting under
`/persistent/exts/isaac.mcp.bridge/` for every launch. The environment wins.

| Setting | Environment | Stored as | Default |
|---|---|---|---|
| Port | `ISAAC_MCP_PORT` | `autostartPort` | none: the bridge does not start without one |
| Address to listen on | `ISAAC_MCP_BIND` | `autostartHost` | `localhost` |
| Tool packages to load | `ISAAC_MCP_TOOL_MODULES` | `toolModules` | none |
| Folders they are in | `ISAAC_MCP_TOOL_PATHS` | `toolPaths` | none |
| Require the token | `ISAAC_MCP_AUTH` | `auth` | `1` |
| The token | `ISAAC_MCP_TOKEN` | `token` | a random one per launch |
| Read-only | `ISAAC_MCP_READ_ONLY` | `readOnly` | `0` |

`./run.sh` sets the port for you. `scripts/install.py` writes the stored form into a Kit app's
`user.config.json`, so that app loads the bridge on every launch with no flags; run it with the
app closed, because a running app writes its own settings back over the file when it exits.

## Several simulators on one machine

Give each Kit process its own port. A port that is taken makes the bridge refuse to start and say
so, rather than move to a port no client expects. Every bridge advertises itself, so
`--list-bridges` shows them all, and a client that was given no port refuses to pick between them.
`status` answers with the PID, port, app and version, so you can confirm you reached the one you
meant.

## From another machine

The bridge listens on `localhost` only, because it runs code. The simplest way in is an SSH
tunnel to the port. To listen on a private address instead, set `ISAAC_MCP_BIND` to that address
(never `0.0.0.0` on a machine the internet can reach) and give the client the bridge's token as
`ISAAC_MCP_TOKEN`, plus `ISAAC_MCP_HOST` and `ISAAC_MCP_PORT`. A remote client cannot read the
portfiles; the `list_bridges` tool, asked of any bridge it can reach, lists the others on that
machine.

## The server's own settings

The `isaac-mcp` server reads `ISAAC_MCP_PORT` and `ISAAC_MCP_HOST` (which bridge),
`ISAAC_MCP_NAMESPACE`, `ISAAC_MCP_BUILTINS`, `ISAAC_MCP_LOCAL_TOOLS` and `ISAAC_MCP_LEGACY_TOOLS`
(what a client is shown). They are described in [ENVIRONMENT.md](../../docs/ENVIRONMENT.md).

# isaac-mcp (host package)

The host side of isaac-mcp: a Model Context Protocol (MCP) server for Isaac Sim, Isaac Lab and Omniverse Kit, with one command,
`isaac-mcp`. It connects MCP clients to the tools inside a running Kit process and adds desktop capture, Isaac Lab job control and
a bridge to RoboSimStudio. This is the package readme; the project overview is the [root README](../README.md).

## Install

Install it as an editable tool shared by every MCP client (Linux x86_64):

```bash
./install.sh --no-sim        # from the repository root; links ~/.local/bin/isaac-mcp
```

The installer uses `mcp/uv.lock` (`uv tool install` does not read the lock, so it is not a reproducible checkout install). To
register a client, see [client configuration](../docs/MCP-SERVER.md#4-client-configuration). Setup details are in
[Linux setup](../docs/LINUX-SETUP.md).

## Where things are documented

| Topic | Page |
|---|---|
| What a client is shown, and how to change it | [Toolsets](../docs/TOOLSETS.md) |
| How the host, the bridge and the Kit extensions fit together; loading the bridge in another Kit app; scripts without MCP | [How it works](../docs/ARCHITECTURE.md) |
| Every tool and argument | [Tool arguments](../docs/TOOLS-ARGUMENTS.md) |
| Every setting | [Environment variables](../docs/ENVIRONMENT.md) |
| Your own tool package | [Tool authoring](docs/tool-authoring.md) |
| The bridge protocol, the token, read-only mode, jobs | [Protocol](docs/protocol.md) |

## Layout

```
mcp/isaac_mcp/            the MCP server, the bridge client, Isaac Lab jobs, screen tools, RoboSimStudio bridge
mcp/templates/            a tool package to copy, a remote driver, an Isaac Lab launch script
mcp/tests/                host tests; the bridge is tested here too, outside Kit
mcp/docs/                 the bridge: protocol, writing tools
exts/isaac.mcp.bridge/    the bridge extension
exts/sim-tools/           generic scene and asset tools
exts/ur5e-dt/             the lab's UR5e work cell
scripts/install.py        load the bridge in a Kit app on every launch
```

## Development

```bash
cd mcp
uv run --locked --isolated --python 3.12 --extra test --extra isaaclab --extra scene-test pytest -q
```

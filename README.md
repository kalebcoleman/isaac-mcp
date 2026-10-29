# isaac-mcp

An MCP server that lets an LLM client drive **Isaac Sim 6.2** (and, as tools are added, **Isaac Lab 3.0**):
it talks to a running Kit app over a local bridge, and it can also see the desktop (screenshots and screen
recordings). Not tied to any one project.

Built on Aldrin Inbaraj's [`omni-isaac-mcp`](https://github.com/inbarajaldrin/omni-isaac-mcp) and
[`isaac-sim-mcp`](https://github.com/inbarajaldrin/isaac-sim-mcp) (MIT), with a screen-capture tool pack
added. See [`docs/VENDORING.md`](docs/VENDORING.md).

## Layout

| Path | What it is |
|---|---|
| `mcp/` | Host MCP server (`isaac-mcp`): stdio server, Kit bridge client, screen tools. Pure Python, no Isaac dependency. |
| `exts/isaac.mcp.bridge/` | Kit extension: the socket bridge and tool registry inside Kit. |
| `exts/isaac.mcp.panel/` | Kit extension: registry inspector UI. |
| `exts/ur5e-dt/` | Tool pack: the UR5e digital-twin tools (22 `ur5e_dt.*`) and their assets. One pack among others to come. |
| `npm/` | Optional `npx` launcher (runs the Python server through `uvx`). |
| `scripts/`, `run.sh`, `verify.sh`, `install.sh` | Launch, check and install helpers (Ubuntu 22.04/24.04, Python 3.12). |

## Quick start

```bash
./install.sh --accept-eula        # uv env with Isaac Sim 6.2.0.0 + the isaac-mcp CLI
./run.sh                          # Kit with the bridge on port 8766 (--headless for no window)
./verify.sh                       # tool list, scene build, physics checks
```

Host server only (no Isaac Sim): `uv tool install "./mcp"`.

## Status

Work in progress; goals in [`docs/ROADMAP.md`](docs/ROADMAP.md).

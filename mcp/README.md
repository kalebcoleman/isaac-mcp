# isaac-mcp

Host-side Model Context Protocol (MCP) server for Isaac Sim, Isaac Lab and Omniverse Kit.
It provides the Kit bridge gateway plus host-side desktop capture.
The package provides one CLI, `isaac-mcp`.
Opt-in groups also provide X11 desktop input and managed simulator sessions; see
[host tools](../docs/TOOLS-HOST.md#optional-desktop-input) for configuration and limitations.

Install it as an editable tool (shared by every MCP client):

```bash
# From repository root:
./install.sh --no-sim
# Or install host package directly:
uv tool install --force --editable ./mcp
```

Both commands install `isaac-mcp`. The installer also creates an `~/.local/bin/ffmpeg` shim for the Kit
viewport recorder when that path does not already contain an unrelated file; a direct `uv tool install` does
not create that shim. For the pinned Sim installation and EULA requirements, follow [Linux setup](../docs/LINUX-SETUP.md).

Client configs: from the `isaac-mcp` repository root run
`./install.sh --no-sim --claude` for Claude Code and Claude Desktop. Details:
[docs/MCP-SERVER.md](../docs/MCP-SERVER.md).

The 23 `ur5e_dt__*` tools are listed from a packaged catalog even before a simulator is running.
They execute through a bridge that has the twin extension loaded; when no suitable bridge is available,
they return an error with connection guidance. The supplied `run.sh` enables the tools while hiding their
panel by default; use `--ur5e-panel` to show it. The generic scene/asset pack is also
catalog-listed; see [scene tools](../docs/TOOLS-SCENE.md) for its runtime requirements
and trusted-root setup. Other available tools include:

| Tool | Purpose |
|---|---|
| `screenshot` | Screen, monitor, or window -> inline image + full-res PNG |
| `list_windows` | Visible windows with ids, geometry, class, title |
| `raise_window` | Bring a window to the front before capturing it |
| `start_screen_recording` | Record the desktop or a window to MP4; full-screen output by default |
| `stop_screen_recording` | Finalize and report the MP4 |
| `screen_recording_status` | Elapsed time and frames |
| `screen_info` | Display, monitors, capture dirs, recording state |
| `use_bridge` | Switch to another running Kit process by port |

`list_bridges` (a bridge builtin) shows every running bridge on this box.
Set `UR5E_DT_PORT` to pin the ur5e-dt twin bridge (`ISAAC_MCP_PORT` is the
generic fallback); with several running and no pin, discovery refuses rather
than guessing. The ur5e-dt extension registers with the same bridge as other
tool packs; the launch script sets the shared port to the selected twin port.

For a clean Isaac Sim viewport render without desktop chrome, use the Kit
extension's `ur5e_dt__start_recording` / `ur5e_dt__stop_recording`. A screen
tool call with `window` (or `window_id`) reads that window's own pixels, so
overlapping windows do not appear in the still or the video.
`screenshot()` and `start_screen_recording()` with no target pick the Isaac Sim
window when it is open (the one owned by the Kit process on the configured
port), so the default is the sim, not the desktop. Recorded video is scaled and
padded to the primary monitor (1920x1080 here) by default, so it is full-screen
even when the window is covered; pass `full_screen=False` for native size.
Monitor captures show the desktop as displayed, including whatever is on top.

**One bridge, one port, per Kit process.** Projects register tools under a
namespace; name collisions between projects are impossible by construction
(two projects can each ship a `play_scene` — they land as `arm.play_scene`
and `rover.play_scene`), so co-loaded projects share the single socket.

```mermaid
flowchart TB
    subgraph kit["Kit process (Isaac Sim · Isaac Lab · any Kit app)"]
        direction BT
        BRIDGE["<b>isaac.mcp.bridge</b><br/>NDJSON TCP :8766 · owner/namespace registry<br/>main-thread dispatch<br/>builtins: run_python · list_tools · reload_tools · list_bridges · put_file · get_file · stat_file · status"]
        ARM["arm_tools/<br/>MCP_NAMESPACE = 'arm'"] -->|register| BRIDGE
        ROVER["rover_tools/<br/>MCP_NAMESPACE = 'rover'"] -->|register| BRIDGE
        PANEL["isaac.mcp.panel<br/>registry-driven UI"] -->|"dispatch (click == agent call)"| BRIDGE
    end
    GW["isaac-mcp gateway<br/>operator view: all tools + run_python"] -->|MCP ⇄ NDJSON| BRIDGE
    GWF["isaac-mcp gateway (filtered)<br/>ISAAC_MCP_NAMESPACE=arm · ISAAC_MCP_BUILTINS=0"] -->|MCP ⇄ NDJSON| BRIDGE
    CLI["isaac_mcp.client<br/>scripts · tests · doctors · CLI"] -->|NDJSON| BRIDGE
```

## Setup (once per machine)

Register the bridge permanently (afterwards, launches need no flags or env
vars):

```bash
python3 scripts/install.py            # add --dry-run to preview
```

This edits the Kit app's persistent `user.config.json` (the scriptable
equivalent of the Extensions window's AUTOLOAD checkbox): adds `exts/` as a
search path, auto-enables `isaac.mcp.bridge` + `isaac.mcp.panel`, and sets the
bridge port (default **8766**). Restart the app; the bridge binds with the
built-in tools. `--remove` undoes it.

Run it while the app is **down**; a running app overwrites the file on exit
([why](docs/configuration.md#register-while-the-app-is-down)).

Connect an agent/IDE — install the front-end (`pip install -e .` or `uv sync`)
and add to `.mcp.json` (Claude Code, Cursor, etc.):

```json
{
  "mcpServers": {
    "kit": { "command": "isaac-mcp" }
  }
}
```

No port needed — the gateway discovers a lone running bridge. Pin one with
`"env": { "ISAAC_MCP_PORT": "8766" }` when several Kit processes run.

That alone is fully usable: `run_python` executes arbitrary Python on Kit's
main thread (persistent sessions supported; saved session vars auto-clear when
a new stage opens — they hold prim/handle references the new stage kills),
which reaches everything in the sim. Tools discovered from the bridge appear as native MCP tools
(`robot_demo.spawn_robot` → `robot_demo__spawn_robot`; dots are illegal in MCP names).

## Adding a project (the contract)

A project is a **plain Python package** — not a Kit extension. It declares a
namespace and either exposes one entrypoint (copy `examples/demo_tools/`) or
none at all ([declared toolsets](docs/tool-authoring.md#declared-toolsets)):

```python
# ~/myproj/isaac/myproj_tools/__init__.py
MCP_NAMESPACE = "robot_demo"                       # defaults to the module name
DOORWAY_ADMISSION = 1                              # the admission contract, docs/protocol.md

def register(registrar):                   # receives an owner-bound registrar
    @registrar.tool("Spawn a robot into the scene.", {"name": {"type": "string"}})
    def spawn_robot(name: str = "robot_1"):
        import omni.usd                    # Kit imports live inside handlers
        ...
        return {"path": f"/World/{name}"}  # payload only — no envelope
```

Register it persistently (once per project):

```bash
python3 scripts/install.py add-project --path ~/myproj/isaac --module myproj_tools
```

Next launch, the bridge advertises `robot_demo.spawn_robot`; the panel grows a `robot_demo`
section; agents see it after `refresh_tools`. Per-launch override instead of
persistence: `ISAAC_MCP_TOOL_PATHS=~/myproj/isaac ISAAC_MCP_TOOL_MODULES=myproj_tools`.

> **One registration, three surfaces.** A registered tool isn't just an agent
> verb: the control panel renders it as a clickable widget automatically, and
> the reference client/CLI can call it from scripts — all three go through the
> same dispatch entrance. Write the tool once; button, agent call, and script
> call come with it.

A package with no `register()` is introspected instead: every public function
becomes a tool, its schema derived from the signature. See
[docs/tool-authoring.md](docs/tool-authoring.md) for declared toolsets, the handler
contract and curated per-project agent views.

## Scripting the bridge (no MCP needed)

The bridge's wire protocol is not MCP — the gateway above is the only MCP
speaker. For scripts, tests, and health checks, use the **reference client**
(stdlib-only, single-file, also copyable as-is):

```python
from isaac_mcp.client import call, BridgeClient, BridgeError
call("demo.ping", {"message": "hi"})                 # one-shot; endpoint auto-discovered
with BridgeClient() as c:                            # persistent + reconnect
    c.call("run_python", {"code": "result = 1"})     # (pass host=/port= to pin)
```

```bash
python3 -m isaac_mcp.client <tool> ['{"json":"params"}'] [--port N]
python3 -m isaac_mcp.client --list-bridges          # what's running
# exit codes: 0 success · 1 bridge answered with an error · 2 unreachable
```

With exactly one bridge running you need no port: the client discovers it
([endpoint resolution](docs/protocol.md#endpoint-resolution)).
`import isaac_mcp.client` works without the `mcp` SDK installed — only the
gateway needs it.

## What else it does

- **Hot reload** — edit a tool package and reload it without restarting Kit. [docs/tool-authoring.md](docs/tool-authoring.md#hot-reload)
- **Remote files** — ship code and assets to a remote box through the bridge. [docs/tool-authoring.md](docs/tool-authoring.md#remote-files-put_file--get_file--stat_file)
- **Control panel** — every registered tool becomes a clickable widget. [docs/tool-authoring.md](docs/tool-authoring.md#control-panel)
- **Request admission and doorway hold** — one queue with typed refusals, and a way to reserve the bridge for one caller. [docs/protocol.md](docs/protocol.md)
- **Multiple instances and remote access** — one port per Kit process, discovery, binding beyond localhost. [docs/configuration.md](docs/configuration.md)
- **Isaac Lab** — the bridge inside standalone train/play scripts. [docs/isaac-lab.md](docs/isaac-lab.md)
- **Tool-author gotchas** — Kit facts that cost real debugging time. [docs/tool-authoring.md](docs/tool-authoring.md#tool-author-gotchas-kit-facts-that-cost-real-debugging-time)

## Layout

```
exts/isaac.mcp.bridge/          the bridge extension (protocol, bridge, builtins, autoload)
exts/isaac.mcp.panel/    registry-driven control panel (optional)
isaac_mcp/                    the MCP front-end (FastMCP server, TCP client to the bridge)
scripts/install.py          permanent registration: base install + add-project
examples/demo_tools/        the template tool package — copy to onboard a project
tests/                      protocol / registry / autoload / live-socket tests (no Kit needed)
docs/                       protocol, configuration, tool authoring, Isaac Lab
```

## Development

```bash
python3 -m pytest tests/    # the socket server runs under plain asyncio in tests
```

# Host MCP Server: `isaac-mcp`

The project provides a single host-side Model Context Protocol (MCP) server that exposes both the live Omniverse Kit digital twin tools and desktop screen capture capabilities.

The server package is located at `mcp/` (Python package `isaac_mcp`). The primary CLI executable is `isaac-mcp`. There is one command name and no aliases.

For the system layer breakdown, port mapping, and routing rules, see [docs/ARCHITECTURE.md](ARCHITECTURE.md).

```
Kit Process (Isaac Sim 6.2.0.0)
  ur5e-dt extension ──registers──► shared bridge :8766 ◄── other tool packs
                                    │ NDJSON over TCP
                                    ▼
                        isaac-mcp (stdio MCP server)
                          ├── ur5e_dt__*        dynamic, from the twin bridge
                          ├── list_bridges      running Kit bridge instances
                          ├── use_bridge        switch target port at runtime
                          └── screenshot / screen_* / list_windows (host desktop capture)
```

Inside Kit, `exts/ur5e-dt` registers on the `isaac.mcp.bridge` singleton alongside other packs. The launcher sets
the shared port to 8766 by default and delays binding until the twin has registered its tools. Disabling the twin
removes its tools without stopping the shared bridge; an in-flight call must drain before the twin can re-register.

---

## 1. Installation

The host MCP server is installed automatically when running `./install.sh` from the repository root:

```bash
./install.sh                    # Installs isaac-mcp editable via uv tool
./install.sh --claude           # Also writes Claude Code and Claude Desktop configs
```

To install or reinstall only the host MCP tool manually:

```bash
uv tool install --force --editable ./mcp
```

Because it is installed in editable mode from `./mcp`, modifications to `mcp/isaac_mcp/` take effect whenever the MCP server process restarts.

`install.sh` also creates an ffmpeg shim at `~/.local/bin/ffmpeg` for the twin's viewport recorder, unless an
unrelated file or symlink already occupies that path. Direct `uv tool install` does not create the shim.

---

## 2. Available Tools

### Simulator Tools (`ur5e_dt__*`)
When connected to an Isaac Sim session running `exts/ur5e-dt`, the host dynamically registers its tools. Kit's
canonical `ur5e_dt.<tool>` names appear to MCP clients as `ur5e_dt__<tool>`. The pack includes scene setup,
`play_scene`/`stop_scene`, object management, ROS graphs and viewport recording. See the complete
[UR5e tool reference](TOOLS-UR5E.md) for actual names, parameters and side effects. Joint-position and pause tools
are not registered by this pack.

### Bridge Management Built-ins
- `list_bridges`: Lists all running Kit bridge instances (PID, port, and app name). *Note: This is a Kit bridge built-in that appears when connected to a bridge.*
- `use_bridge`: Points the host MCP server at a different bridge port at runtime without restarting the client.
- `refresh_tools`: Forces rediscovery of tools from the currently connected bridge.

### Desktop & Window Capture Tools
- `screenshot`: Captures an image of the Isaac Sim window (or desktop/monitor) and returns an inline thumbnail plus saved PNG path.
- `list_windows`: Lists visible X11 windows with their window IDs, geometry, class, and titles.
- `raise_window`: Brings a specified window to the front.
- `start_screen_recording`: Records window or desktop activity to MP4 at a specified framerate (default 15 fps). Window capture reads that window's buffer directly, so overlapping windows do not obscure it.
- `stop_screen_recording`: Finalizes recording and returns the path to the MP4.
- `screen_recording_status`: Reports elapsed recording time and captured frames.
- `screen_info`: Reports active display, attached monitors, capture directories, and recording state.

Captures are written to `~/Pictures/screen-mcp/` and `~/Videos/screen-mcp/`. Screen recordings do not contain audio.
See the [host and bridge reference](TOOLS-HOST.md) for the complete list and arguments. The separate
[Isaac Lab reference](TOOLS-ISAACLAB.md) covers the 12 host-side Lab tools.

### Agent workflow

Use the available tools to inspect and operate the user's authorized simulator
before asking the user to repeat terminal or UI steps. `list_windows` identifies
the window and PID; `screenshot(window_id=...)` captures that window even when
covered. Check the capture directory before writing if workspace rules restrict
where files may be created. Screen tools are host-side and can work even when
the simulator bridge is disconnected. They provide capture and window raising,
not a general click or keyboard API.

The host MCP server and the bridge inside Kit are separate processes. A working
`list_windows` or Isaac Lab tool does not mean that the bridge is connected, and a
client that lists only the host and Isaac Lab tools has not proved that no bridge
exists: the client may have connected before Kit started (call `refresh_tools`),
or `ISAAC_MCP_NAMESPACE` / `ISAAC_MCP_BUILTINS=0` may be hiding bridge tools
(verify with the CLI below). Do not start another Kit instance while a reachable
bridge exists. If `refresh_tools` returns `Connection refused`, check for the intended Kit
process, its enabled bridge extension and listening port. The server cannot
control a simulator that did not start a bridge. Once a bridge is available,
call `refresh_tools`, then use `status` to verify its PID and port before
calling `run_python` or any scene-changing tool. When several instances run,
select the intended port with `use_bridge`; never assume the visible window is
the bridge target. A 6.2 host environment does not prove that a 5.1 process has
loaded a compatible bridge. The generic `isaac.mcp.bridge` was also tested in
an Isaac Sim 5.1 process on 2026-10-30: after enabling it and setting
`ISAAC_MCP_PORT`, `status` reported the expected 5.1 process and PID.

If a client does not expose newly discovered tools after `refresh_tools`, use
the installed host client's standard-library CLI against the verified bridge. List the
advertised bridges first and use the host and port it reports; 8766 below is only the twin's
default port:

```bash
python -m isaac_mcp.client --list-bridges
python -m isaac_mcp.client status --port 8766
python -m isaac_mcp.client run_python '{"code":"result = 1"}' --port 8766
```

Use the interpreter that has `isaac_mcp` installed. For a user-started Kit
process without the bridge, do not assume the host server can attach it; load
the extension in an authorized session or start a separate process when the
user and project rules permit it.

Prefer the connected simulator's own tools for playback, inspection and logs.
Use a terminal or other explicitly available local controls when appropriate,
and report actual tool limitations instead of repeatedly asking the user to
perform actions the agent can perform. Respect project-specific rules about
launching or restarting processes, output locations and preserving live scenes.
Do not launch a real robot driver for a simulation-only task.

---

## 3. Multi-Session Configuration (Ports)

By default, the twin bridge binds to port **8766**. When running multiple concurrent simulation sessions, assign distinct ports using `UR5E_DT_PORT`:

```bash
# Terminal 1: Primary session
UR5E_DT_PORT=8766 ./run.sh

# Terminal 2: Secondary session
UR5E_DT_PORT=8767 ./run.sh --port 8767
```

An MCP client connected to port 8766 can switch to the second session at runtime:

```python
use_bridge(8767)
```

The switch persists until the MCP server process restarts.

---

## 4. Client Configuration

### Claude Code

Configure Claude Code using the `claude mcp` CLI:

```bash
claude mcp add --scope user isaac \
  -e DISPLAY="${DISPLAY:-:0}" \
  -e UR5E_DT_PORT=8766 \
  -- "$(command -v isaac-mcp || echo "$HOME/.local/bin/isaac-mcp")"
```

### Claude Desktop

Add the server to `~/.config/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "isaac": {
      "command": "isaac-mcp",
      "args": [],
      "env": {
        "UR5E_DT_PORT": "8766",
        "DISPLAY": ":1"
      }
    }
  }
}
```
*(Ensure `DISPLAY` matches your active session, e.g., `:1` on the lab workstation or `:0` on standard desktops).*

### OpenCode

Add the server to `~/.config/opencode/opencode.jsonc`:

```jsonc
"isaac": {
  "type": "local",
  "command": ["isaac-mcp"],
  "environment": {
    "UR5E_DT_PORT": "8766",
    "DISPLAY": ":1"
  }
}
```

---

## 5. Verification

Verify the MCP server standalone without launching Isaac Sim:

```bash
# Starts the server and immediately exits upon EOF; verifies binary and dependencies
printf '' | isaac-mcp
```

Verify end-to-end communication with Isaac Sim running:

```bash
# Launch simulation
./run.sh --headless &

# Wait for the app and bridge to finish starting, then run the check suite.
# This builds/reopens a scene and moves a joint; use a disposable session.
./verify.sh
```

The verifier checks the bundled workstation's scene, gravity, joint limits, drive settings and an elbow motion. Review
its output and exit status; [verification](VERIFICATION.md) describes the thresholds and remaining limits.

# Host MCP Server: `isaac-mcp`

The project provides a single host-side Model Context Protocol (MCP) server that exposes both the live Omniverse Kit digital twin tools and desktop screen capture capabilities.

The server package is located at `mcp/` (Python package `kit_mcp`). The primary CLI executable is `isaac-mcp`. There is one command name and no aliases.

For the system layer breakdown, port mapping, and routing rules, see [docs/ARCHITECTURE.md](ARCHITECTURE.md).

```
Kit Process (Isaac Sim 6.2.0.0)
  ur5e-dt extension ──owns──► twin bridge :8766
                                    │ NDJSON over TCP
                                    ▼
                        isaac-mcp (stdio MCP server)
                          ├── ur5e_dt__*        dynamic, from the twin bridge
                          ├── list_bridges      running Kit bridge instances
                          ├── use_bridge        switch target port at runtime
                          └── screenshot / screen_* / list_windows (host desktop capture)
```

Inside Kit, `exts/ur5e-dt` runs its own bridge endpoint on port 8766. The launcher sets `OMNI_KIT_MCP_PORT=0` to disable the generic base bridge so that each Kit process exposes exactly one endpoint.

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

Because it is installed in editable mode from `./mcp`, modifications to `mcp/kit_mcp/` take effect whenever the MCP server process restarts.

The installation also installs a static ffmpeg wrapper at `~/.local/bin/ffmpeg` pointing to the binary bundled with `imageio-ffmpeg`. This satisfies Isaac Sim's viewport video recorder without requiring a system package.

---

## 2. Available Tools

### Simulator Tools (`ur5e_dt__*`)
When connected to an active Isaac Sim session running `exts/ur5e-dt`, the server dynamically registers the twin's tools. Note that Kit's canonical `ur5e_dt.<tool>` names are exposed to MCP clients as `ur5e_dt__<tool>`:
- `ur5e_dt__quick_start_with_workstation`: Loads the UR5e robot, RG2 gripper, QuickChanger, and Vention table into the scene.
- `ur5e_dt__play_scene` / `ur5e_dt__pause_scene` / `ur5e_dt__stop_scene`: Controls simulation timeline playback.
- `ur5e_dt__get_joint_positions`: Reads current articulation joint angles.
- `ur5e_dt__set_joint_positions`: Commands joint position targets.
- `ur5e_dt__start_recording` / `ur5e_dt__stop_recording`: Captures a clean viewport render buffer directly to an MP4 file (bypasses window UI chrome).
- `ur5e_dt__save_scene_state` / `ur5e_dt__restore_scene_state`: Checkpoint save and restore.

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
claude mcp add --scope user ur5e \
  -e DISPLAY="${DISPLAY:-:0}" \
  -e UR5E_DT_PORT=8766 \
  -- "$(command -v isaac-mcp || echo "$HOME/.local/bin/isaac-mcp")"
```

### Claude Desktop

Add the server to `~/.config/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ur5e": {
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
"ur5e": {
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

# Run check suite over bridge
./verify.sh
```

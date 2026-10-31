# Host and bridge tools

The host `isaac-mcp` process speaks MCP to the client. It provides the gateway and desktop tools below even when
Kit is disconnected. The seven bridge built-ins are discovered from a running Kit bridge; they can be hidden from
the host's MCP list with `ISAAC_MCP_BUILTINS=0`. Other packs are documented in the [UR5e](TOOLS-UR5E.md) and
[Isaac Lab](TOOLS-ISAACLAB.md) references.

Examples below show the **tool name** and a JSON object of **arguments**. Use the tool-calling interface of your MCP
client; these objects are not standalone shell commands or complete MCP protocol messages.

## Connect and discover

Start a compatible Kit application with its bridge enabled. For the supplied twin, `./run.sh --headless` starts the
bridge on port 8766 by default. The host chooses an explicit port first, then `UR5E_DT_PORT`, then
`ISAAC_MCP_PORT`; otherwise it discovers an unambiguous advertised bridge. Discovery can fail if there are no live
bridges or several candidates. The host and the Kit app are separate processes: screen tools can work while the
bridge is unavailable.

| MCP tool | Arguments | Use and effects |
|---|---|---|
| `refresh_tools` | none | Rediscover the selected bridge's registry after a bridge starts or a module changes. Adds or updates visible tools and removes tools no longer advertised. A failed or malformed discovery, or a collision between bridge tool names or with a host tool, leaves the existing list intact. The host advertises `tools.listChanged` and sends `notifications/tools/list_changed` after a refresh that changed the list; a client that ignores the notification still needs a reconnect (or `python -m isaac_mcp.client list_tools`). |
| `use_bridge` | `port` (integer, required) | Connect to a different bridge on that port, report its identity, and replace the previously discovered bridge tools. The choice lasts until the host server restarts. |

For a bridge already connected, `status` verifies its PID, port, app version and other identity data; `list_bridges`
lists advertised bridge instances. Example MCP calls: `use_bridge` with `{"port":8766}`, followed by `status` with
`{}` and `refresh_tools` with `{}`. Switching targets changes the selected simulation; verify the result before
issuing scene-changing commands.

A client that lists only the host and Isaac Lab tools may simply not have run `refresh_tools` since Kit started, or
`ISAAC_MCP_NAMESPACE` / `ISAAC_MCP_BUILTINS=0` may be hiding bridge tools (including `status` and `list_bridges`), so
call `refresh_tools` and verify with the CLI below before concluding that no bridge exists or starting another Kit.

The bridge also has an internal `list_tools` discovery operation. It is intentionally hidden from the bridge's own
advertised tool list, so it is not normally a dynamically registered MCP tool. Ask the MCP client to list tools, or
use the bridge CLI from an environment with `isaac_mcp` installed:

```bash
python -m isaac_mcp.client status --port 8766
python -m isaac_mcp.client list_tools --port 8766
python -m isaac_mcp.client ur5e_dt.play_scene '{}' --port 8766
```

The CLI uses canonical bridge names with dots; the MCP server exposes namespaced tools using double underscores,
such as `ur5e_dt__play_scene`.

## Desktop and window tools

These tools use the host's X11 display (`DISPLAY`, default `:0`), and do not require a Kit bridge. Their behavior
depends on an accessible graphical session. Screenshots are saved under `~/Pictures/screen-mcp/` and recordings
under `~/Videos/screen-mcp/` on the **host server** machine. They do not provide mouse or keyboard control.

| MCP tool | Arguments and defaults | Use and effects |
|---|---|---|
| `screen_info` | none | Report display, monitors, auto-target, capture locations and recording state. |
| `list_windows` | `filter=null` | List visible windows and IDs; the optional string filters title or window class. |
| `raise_window` | `window=null`, `window_id=null` | Raise the matching window. A window selector is needed to select the intended window. |
| `screenshot` | `monitor=null`, `window=null`, `window_id=null`, `max_width=1600` | Save a full-resolution PNG and return a caption plus inline image, scaled down to at most `max_width` (clamped to 320–3840). With no selector, auto-targets Isaac Sim/Lab if found, otherwise monitor 1. `monitor=0` means all monitors. |
| `start_screen_recording` | `monitor=null`, `window=null`, `window_id=null`, `fps=15`, `max_width=1600`, `max_seconds=600`, `full_screen=true` | Start one MP4 recording; no audio. With no selector, auto-target a simulator window, otherwise monitor 1. `fps` is clamped to 1–30 and `max_seconds` to 5–3600. `full_screen=true` scales/pads to the primary monitor; false uses the window or monitor size capped by `max_width`. |
| `stop_screen_recording` | none | Stop and finalize the active recording, reporting its path and result. |
| `screen_recording_status` | none | Report active recording, elapsed time and frame count, or the most recent recording. |

Examples: `list_windows` with `{"filter":"Isaac"}`; `screenshot` with `{"window_id":12345}`;
`start_screen_recording` with `{"window_id":12345,"fps":15,"max_seconds":20,"full_screen":false}`; then
`stop_screen_recording` with `{}`. Window capture reads the selected window's pixels, including when another window
covers it. For a clean simulator viewport without desktop chrome, use the twin's `ur5e_dt__start_recording` instead.

## Kit bridge built-ins

These tools operate on the **bridge machine**, inside or alongside the Kit process. Their availability depends on
the selected bridge and the host's built-in filter.

| MCP tool | Arguments and defaults | Use and effects |
|---|---|---|
| `status` | none | Return bridge process identity, port, app version, headless state and registry information. |
| `list_bridges` | none | List bridge instances advertised on the bridge machine. |
| `run_python` | `code` (string, required); `session_id="default"`, `persistent=false` | Execute Python on Kit's main thread with `omni`, `carb` and common USD symbols preloaded. Assign `result` to return a value. Persistent sessions retain eligible variables for the same ID; a stage-open event clears session state when the subscription is available. Arbitrary code can change the scene or access files. |
| `reload_tools` | `module` (string, required) | Reload an autoloaded tool package by module name, then call host `refresh_tools` to update the client-facing registry. If that pack has a call in flight, reload is refused until it finishes. |
| `put_file` | `path`, `content_b64` (strings, required); `mkdirs=true` | Decode base64 and write or overwrite a file on the bridge machine; creates parents by default. |
| `get_file` | `path` (string, required) | Read a file on the bridge machine and return base64 content. |
| `stat_file` | `path` (string, required) | Report whether a bridge-machine file exists, with its size and SHA-256 when present. |

Examples: `run_python` with `{"code":"result = 1"}`; `stat_file` with
`{"path":"/tmp/isaac-example.txt"}`; `put_file` with
`{"path":"/tmp/isaac-example.txt","content_b64":"aGVsbG8="}` writes `hello`. File content is transmitted as
a single message, so these tools are for reasonably sized files rather than bulk multi-gigabyte transfers.

## Trust and operational limits

The default bridge binds to loopback. Loopback confines network reachability but is not a substitute for a trust
boundary between local users: bridge built-ins can run Python and read or overwrite files with the Kit process's
permissions. Do not expose the bridge to an untrusted network; evaluate authentication and access controls before
using a non-loopback bind. `ISAAC_MCP_BUILTINS=0` hides built-ins from the host MCP surface; it is not a substitute for
securing the underlying bridge socket.

Desktop capture depends on the display session and permissions, and can contain sensitive information. Confirm the
target window before capturing. See [server setup](MCP-SERVER.md), [architecture](ARCHITECTURE.md), and
[tool authoring](../mcp/docs/tool-authoring.md) for more detail.

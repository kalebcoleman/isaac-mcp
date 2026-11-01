# Host and bridge tools

The host `isaac-mcp` process speaks MCP to the client. It provides the gateway and desktop tools below even when
Kit is disconnected. The 23 UR5e tools are also listed from a packaged catalog, even with Kit down; a live twin
bridge is required to execute them. The seven bridge built-ins are discovered from a running Kit bridge; they can be hidden from
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
| `refresh_tools` | none | Rediscover the selected bridge's registry after a bridge starts or a module changes. Adds or updates visible tools and removes tools no longer advertised; catalog-listed tools remain visible as proxies. A failed or malformed discovery, or a collision between bridge tool names or with a host tool, leaves the existing list intact. The host advertises `tools.listChanged` and sends `notifications/tools/list_changed` after a refresh that changed the list; a client that ignores the notification still needs a reconnect (or `python -m isaac_mcp.client list_tools`). |
| `use_bridge` | `port` (integer, required) | Connect to a different bridge on that port, report its identity, and replace the previously discovered bridge tools. The choice lasts until the host server restarts. Sends `notifications/tools/list_changed` after replacing the tools. |

For a bridge already connected, `status` verifies its PID, port, app version and other identity data; `list_bridges`
lists advertised bridge instances (they are bridge built-ins, so they appear as MCP tools only when built-ins are
enabled and a bridge is reachable; `python -m isaac_mcp.client --list-bridges` works either way). Example MCP calls: `use_bridge` with `{"port":8766}`, followed by `status` with
`{}` and `refresh_tools` with `{}`. Switching targets changes the selected simulation; verify the result before
issuing scene-changing commands.

Catalog-listed twin tools alone do not prove a live bridge is available. The client may have connected before Kit
started, or `ISAAC_MCP_NAMESPACE` / `ISAAC_MCP_BUILTINS=0` may hide bridge tools (including `status` and
`list_bridges`). Call `refresh_tools` and verify with the CLI below before starting another Kit.

The bridge also has an internal `list_tools` discovery operation. It is intentionally hidden from the bridge's own
advertised tool list, so it is not normally a dynamically registered MCP tool. Ask the MCP client to list tools, or
use the bridge CLI from an environment with `isaac_mcp` installed. Discover the advertised bridges first and use the
host and port from that listing; 8766 below is only the twin's default port:

```bash
python -m isaac_mcp.client --list-bridges
python -m isaac_mcp.client status --port 8766
python -m isaac_mcp.client list_tools --port 8766
python -m isaac_mcp.client ur5e_dt.play_scene '{}' --port 8766
```

The CLI uses canonical bridge names with dots; the MCP server exposes namespaced tools using double underscores,
such as `ur5e_dt__play_scene`.

## Choosing which local tools a server exposes

The 21 standard local tools (the nine host gateway and desktop tools and the twelve `isaaclab__*` tools) are registered by
default. Five desktop input tools and four simulator-session tools are opt-in. `ISAAC_MCP_LOCAL_TOOLS` selects groups for one server process, read once at startup. It is a
comma-separated list of group names and aliases; unset or empty means `all`. An unknown name stops the server at
startup with a message listing the valid names; it never registers nothing silently.

| Group | Tools |
|---|---|
| `isaaclab_read` | `isaaclab__checkpoints_list`, `isaaclab__metrics_summary`, `isaaclab__tensorboard_tail` |
| `isaaclab_probe` | `isaaclab__install_info`, `isaaclab__envs_list` |
| `isaaclab_jobs` | `isaaclab__runs_list`, `isaaclab__runs_status`, `isaaclab__runs_tail` |
| `isaaclab_run` | `isaaclab__train`, `isaaclab__eval`, `isaaclab__play`, `isaaclab__runs_cancel` |
| `screen_read` | `screen_info`, `list_windows`, `screen_recording_status` |
| `screen_capture` | `screenshot`, `start_screen_recording`, `stop_screen_recording` |
| `screen_control` | `raise_window` |
| `screen_input` | `window_click`, `window_drag`, `window_scroll`, `window_type`, `window_key` (explicit opt-in) |
| `sim_launch` | `sim__launch`, `sim__list`, `sim__status`, `sim__stop` (explicit opt-in) |
| `gateway` | `refresh_tools`, `use_bridge` |

Aliases: `all` (standard groups, excluding `screen_input` and `sim_launch`; the default), `readonly` (`isaaclab_read,screen_read`: the local tools annotated as read-only)
and `none` (no local tools; a pure Kit gateway, since bridge tools are still discovered). Groups and aliases can be mixed, for example
`readonly,gateway`.

To enable input as well as the standard tools, set `ISAAC_MCP_LOCAL_TOOLS=all,screen_input` in the MCP client's
server environment and reconnect. Selecting `screen_input` alone exposes only the five input tools; include
`screen_read` and `screen_capture` if the client needs to identify and inspect a window. Input tools have
conservative destructive and open-world annotations. To enable the separate simulator launcher, add `sim_launch`
(for example, `all,screen_input,sim_launch`) and reconnect.

- `isaaclab_probe` runs the configured Python interpreter; its startup code can have side effects. `envs_list` also
  imports configured task modules, which can run arbitrary code. Both tools use conservative non-read-only,
  destructive, non-idempotent and open-world hints.
- `isaaclab__train`, `isaaclab__eval`, and `isaaclab__play` also run configured Python and task modules, so they carry
  the same conservative hints; those processes may write files or contact external systems.
- `isaaclab_jobs` can write: `runs_list` and `runs_status` replace stored metadata when reconciling orphaned jobs,
  and `runs_tail` can create the jobs directory. The first two have destructive hints; `runs_tail` has an additive hint.
- Compatibility: `readonly` and `isaaclab_read` no longer include these five tools. To restore the previous local
  selection, add `isaaclab_probe,isaaclab_jobs` (for example, `readonly,isaaclab_probe,isaaclab_jobs`). The default
  `all` selection and the tools' behavior are unchanged.
- The capture directories (`~/Pictures/screen-mcp`, `~/Videos/screen-mcp`) are created only when `screen_capture` is
  enabled, so a profile without that group creates neither. Importing the Isaac Lab job module can still reconcile
  orphaned job records in an existing jobs directory, whatever the groups. Thus even
  `readonly` does not make server startup free of filesystem writes.
- `gateway` only controls the two tools. Bridge discovery at startup is part of the server lifespan and still runs when
  `gateway` is excluded; without the tools, a client cannot re-run or redirect it, so restart the MCP connection after
  starting Kit.
- `ISAAC_MCP_NAMESPACE` and `ISAAC_MCP_BUILTINS` keep their meaning and filter only the tools discovered from the bridge.
  `ISAAC_MCP_LOCAL_TOOLS` filters only the host's local tools. All three apply together.
- `ISAAC_MCP_LOCAL_TOOLS` does **not** restrict bridge tools, so `readonly` is a read-only *local* profile, not a read-only
  server: with a bridge connected, bridge tools (`run_python`, `ur5e_dt__*`) remain and can change the scene. See
  [Limiting what a client can reach](MCP-SERVER.md#limiting-what-a-client-can-reach) for what restricting them takes.
- Every local tool carries MCP annotations. `readOnlyHint` is true for exactly the `isaaclab_read` and `screen_read`
  groups. The capture tools are not read-only because they write files and return desktop pixels, and
  the job reconciliation and Python probe tools are also not read-only, as described above. Annotations are hints,
  not enforcement.
  Bridge tools registered dynamically are not annotated.

## Desktop and window tools

These tools use the host's X11 display (`DISPLAY`, default `:0`), and do not require a Kit bridge. Their behavior
depends on an accessible graphical session. Screenshots are saved under `~/Pictures/screen-mcp/` and recordings
under `~/Videos/screen-mcp/` on the **host server** machine. Mouse and keyboard tools require the separate opt-in below.

| MCP tool | Arguments and defaults | Use and effects |
|---|---|---|
| `screen_info` | none | Report display, monitors, auto-target, capture locations and recording state. |
| `list_windows` | `filter=null` | List visible windows and IDs; the optional string filters title or window class. A `client` marker identifies windows reported by the window manager as application clients. |
| `raise_window` | `window=null`, `window_id=null`, `expected_pid=null` | Request window-manager activation and verify it became active. Activation gives the window focus. Select the actual client window ID rather than a window-manager frame, and supply the expected PID when targeting a known process. Ambiguous titles owned by different processes require a window ID. |
| `screenshot` | `monitor=null`, `window=null`, `window_id=null`, `max_width=1600` | Save a full-resolution PNG and return a caption plus inline image, scaled down to at most `max_width` (clamped to 320–3840). With no selector, auto-targets Isaac Sim/Lab if found, otherwise monitor 1. `monitor=0` means all monitors. |
| `start_screen_recording` | `monitor=null`, `window=null`, `window_id=null`, `fps=15`, `max_width=1600`, `max_seconds=600`, `full_screen=true` | Start one MP4 recording; no audio. With no selector, auto-target a simulator window, otherwise monitor 1. `fps` is clamped to 1–30 and `max_seconds` to 5–3600. `full_screen=true` scales/pads to the primary monitor; false uses the window or monitor size capped by `max_width`. |
| `stop_screen_recording` | none | Stop and finalize the active recording, reporting its path and result. |
| `screen_recording_status` | none | Report active recording, elapsed time and frame count, or the most recent recording. |

Examples: `list_windows` with `{"filter":"Isaac"}`; `screenshot` with `{"window_id":12345}`;
`start_screen_recording` with `{"window_id":12345,"fps":15,"max_seconds":20,"full_screen":false}`; then
`stop_screen_recording` with `{}`. Window capture reads the selected window's pixels, including when another window
covers it. For a clean simulator viewport without desktop chrome, use the twin's `ur5e_dt__start_recording` instead.

### Optional desktop input

These tools use the existing `python-xlib` dependency and the X11 XTEST extension. No `xdotool` installation is needed.
They work on the host's shared X11 desktop; native Wayland and headless sessions are not supported. They do not require
a bridge and can operate windows other than Isaac Sim/Lab, so enable them only for a client you trust. They are off by
default even when `ISAAC_MCP_LOCAL_TOOLS=all`. Window activation requires a window manager that implements
`_NET_ACTIVE_WINDOW`.

| MCP tool | Additional arguments | Use |
|---|---|---|
| `window_click` | `x`, `y`, `expected_width`, `expected_height`, `button="left"`, `clicks=1` | Click once or twice; buttons are `left`, `middle`, `right`. |
| `window_drag` | `x`, `y`, `end_x`, `end_y`, `expected_width`, `expected_height` | Left-button drag inside one window. |
| `window_scroll` | `x`, `y`, `expected_width`, `expected_height`, `direction`, `steps=1` | Scroll `up` or `down`, 1–10 steps. |
| `window_type` | `text` | Type 1–200 printable ASCII characters without using the clipboard. |
| `window_key` | `key`, `modifiers=null` | One navigation/edit key, F1–F12, or ASCII letter/digit; modifiers may be `ctrl` and `shift`. |

Every input call also requires integer `window_id` and `expected_pid`, taken from a fresh `list_windows` result.
Identify the actual client window, not an unrelated frame or dialog; some desktops list both. Inspect a fresh
`screenshot(window_id=...)`, then use its **saved size**, not its potentially scaled inline image, for coordinate
arguments. Convert inline-image coordinates by multiplying each axis by saved-size / inline-size. Coordinates are
relative to the selected window's top-left corner. For example, for a 1200×800 window with ID 12345 and PID 6789,
`window_click` takes `{"window_id":12345,"expected_pid":6789,"x":200,"y":100,"expected_width":1200,"expected_height":800}`.
First use `raise_window(window_id=12345, expected_pid=6789)` if the window is covered. Take a new screenshot after an action changes the
UI; refresh the size and coordinates after a resize. Input tool success means events were sent, not that the app has
processed them. A busy simulator can take several seconds to show the result; wait and inspect before the next action.

Pointer actions fail if the ID/PID or size changed, the point is offscreen, or another window covers a checked point.
Keyboard actions require focus inside the selected window; click it first. Caps Lock must be off for `window_type`
and letter keys. The key mapping must produce the requested characters with no modifier or Shift; alternate keyboard
groups or layouts can still affect the result, so verify it in the UI. A held key,
mouse button or modifier causes the action to fail. Popup menus or dialogs outside the chosen window may need a
separately listed target; unlisted or untitled popups cannot be controlled by these tools. There is no clipboard,
arbitrary shell-command, Unicode typing, or general accessibility-tree API in this first slice.

X11 input is global: these checks reduce mistakes but cannot enforce a security boundary against other X11 clients,
input grabs or a person changing focus. Cooperating MCP servers using the same `DISPLAY` spelling and `XDG_RUNTIME_DIR`
share an input lock; other applications, users, or differently configured servers do not take that lock. A mouse move may cause hover effects
even if a later target check rejects a click. Do not share the desktop concurrently while an agent is acting; use a
dedicated session for stronger isolation.
Enabling the tools grants the client the ability to trigger any action available in the targeted app, including file
deletion or network operations. Tool annotations are advisory, and X11 window IDs/PIDs are not authentication.
See the [desktop control plan](plans/DESKTOP-CONTROL.md) for remaining work and validation.

### Optional simulator sessions

Select `sim_launch` explicitly to expose these local Linux tools. They launch this repository's `run.sh` and manage only
processes they started; they do not attach to or stop unrelated simulators. This runs the configured simulator Python,
creates files, and can affect the GPU and network. All four tools have conservative annotations.

| MCP tool | Arguments | Use |
|---|---|---|
| `sim__launch` | `port`, `ros_domain`, `headless=false`, `allow_shared_domain=false`, `show_ur5e_panel=false` | Start a new process with a local bridge, UR5e tools, chosen ROS domain and separate output/log and persistent Kit settings. Set `show_ur5e_panel=true` to show the panel in a graphical session. Returns a session ID and PID before the bridge is ready. |
| `sim__list` | none | List managed sessions, including those started by an earlier server process. |
| `sim__status` | `session_id` | Check the original PID's identity, bridge and Kit readiness, and current memory snapshot. |
| `sim__stop` | `session_id`, `expected_pid`, `confirm_discard=false`, `force=false` | With `confirm_discard=true`, send SIGINT to the matching managed process. Poll status for exit; if normal shutdown fails, `force=true` sends SIGKILL. Unsaved changes may be lost. |

Example calls: `sim__launch` with `{"port":18766,"ros_domain":42}`, then `sim__status` with
`{"session_id":"<id returned by launch>"}`. Wait until `state` is `running`, select that port with `use_bridge`, and
verify its PID before operating the scene. If necessary, identify its graphical client with `list_windows` by PID.
Managed launches accept ROS domains 0–231. With the bundled Cyclone DDS profile's participant-index range, domain
232 would make discovery probe beyond UDP port 65535.
Each session has its own fake-driver PID file and log. Stop that driver before stopping the session; `sim__stop`
does not stop its separate process group. The fake launcher still prevents startup alongside another detected UR
driver. The shared-resource URSim backend is unavailable in managed sessions. These protections apply to newly
launched sessions; restart a session started with an older version so it receives the session identity.
`starting` means Kit reports it is still initializing. `readiness_unknown` means the bridge answered but cannot
report Kit readiness (for example, an older bridge); verify the application before using scene tools. An unavailable
bridge reports `starting_or_unresponsive`.
The UR5e tools are enabled in either mode. To show their tab at launch, add `"show_ur5e_panel":true`.
The `show_ur5e_panel_on_start` field records the launch choice, not whether it remains visible later.
The extension can also be enabled in an already running simulator; see [UR5e tools](TOOLS-UR5E.md#simulator-window).
Use different ROS domains for isolated simulations; `allow_shared_domain=true` is an explicit exception when they
intentionally need to communicate. Other ROS nodes must use the matching domain to communicate with a simulator.

At server startup the operator may set these environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `ISAAC_MCP_SIM_ROOT` | repository root when available | Repository containing the executable `run.sh`. Set it explicitly if the host is installed outside a checkout. |
| `UR5E_DT_PYTHON` | `<root>/.venv/bin/python` | Optional configured Isaac Sim interpreter used by `run.sh`. |
| `ISAAC_MCP_SIM_SESSIONS_DIR` | `~/.local/share/isaac-mcp/sim-sessions` | Managed session metadata, logs and separate output directories. A relative path is resolved against the server's working directory. |
| `ISAAC_MCP_SIM_MAX_INSTANCES` | `2` | Maximum known bridge, managed Sim and configured Lab-job processes. `0` disables this count limit. |
| `ISAAC_MCP_SIM_MIN_FREE_VRAM_MIB` | `8192` | Minimum free GPU memory at launch. |
| `ISAAC_MCP_SIM_MIN_FREE_RAM_MIB` | `4096` | Minimum available system RAM at launch. |

The count includes advertised local bridges, managed sessions still starting, and running jobs in the configured
`ISAACLAB_JOBS_DIR`; processes outside those sources may not be counted. Port availability and ROS domains of known
processes are checked, but other unadvertised ROS nodes may share a domain. Host servers using the same sessions
directory serialize their launches; other programs or differently configured servers can race the checks. Current resource checks require Linux,
`nvidia-smi`, and exactly one NVIDIA GPU. The memory thresholds and instance limit are operator settings, not guarantees:
they do not reserve memory or predict scene, renderer, sensor or training peaks. There is no automatic termination if
memory later runs low. Monitor workloads and tune the limits only after testing. Isaac Lab jobs have their own
concurrency guards and explicit overrides; see [Lab tools](TOOLS-ISAACLAB.md#concurrency-guards). Managed sessions use
separate Kit user configuration files, but other Kit caches and machine resources can still be shared.
Stopping uses a Linux pidfd and the original boot and process-start identity to avoid signalling a reused PID.
Linux 6.9 and newer receive the signal for the process group; older kernels receive it for the leader only, reported
as `scope: "process"`, and a forced stop there may leave child processes that require separate cleanup.

## Kit bridge built-ins

These tools operate on the **bridge machine**, inside or alongside the Kit process. Their availability depends on
the selected bridge and the host's built-in filter.

| MCP tool | Arguments and defaults | Use and effects |
|---|---|---|
| `status` | none | Return bridge process identity, port, app version, Kit `app_ready` (`true`, `false`, or `null` if unknown), headless state and registry information. |
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

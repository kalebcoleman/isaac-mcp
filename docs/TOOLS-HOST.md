# Host and bridge tools

The host `isaac-mcp` process speaks MCP to the client. It provides the gateway and desktop tools below even when Kit is
disconnected. The bridge built-ins (`status`, `run_python`, the file tools, job control) are listed from packaged schemas.
This page holds what the generated [tool arguments](TOOLS-ARGUMENTS.md) cannot: behaviour, contracts, limits and worked
sequences. Which tools exist for a toolset is in [toolsets](TOOLSETS.md); settings are in
[environment variables](ENVIRONMENT.md).

Examples below show a **tool name** and a JSON object of **arguments**. Use your MCP client's tool-calling interface; these
objects are not shell commands or complete MCP protocol messages.

## Connect and discover

Start a compatible Kit application with its bridge enabled; `./run.sh --headless` starts the bridge on port 8766 by default.
The host chooses an explicit port first, then `UR5E_DT_PORT`, then `ISAAC_MCP_PORT`; otherwise it discovers an unambiguous
advertised bridge. Discovery can fail if there are no live bridges or several candidates. The host and the Kit app are separate
processes: the screen tools work while the bridge is unavailable.

**`isaac_help`** (`core`) names the tools to load and call for what you want to do, ranked by how many of the task's words a
topic uses. When nothing matches it says no tool is known to fit, gives the `run_python` pattern (one background call that runs
the whole loop), and appends the question to `help-gaps.jsonl` in the server's log folder (`ISAAC_MCP_LOG_DIR`); `no_fit=true`
does the same for a task whose named tools did not fit. The file stays on this machine, is capped at 1 MiB, and
`ISAAC_MCP_LOG=0` turns it off. It reads nothing from the simulator and works with no bridge. It names the toolset when the tool
for a task is off.

**`refresh_tools`** rediscovers the selected bridge's registry after a bridge starts or a module changes.

- If nothing listens on the selected port and exactly one managed session started by this server is live on another loopback
  port (its PID and readiness checked), it selects that session, says so and why, and the choice lasts until `use_bridge` or a
  restart. It never leaves a port that answers; with several sessions it names them instead.
- It adds or updates visible tools and removes tools no longer advertised; catalogue-listed tools stay visible as proxies. A
  failed or malformed discovery, or a name collision, leaves the existing list intact.
- The host advertises `tools.listChanged` and sends `notifications/tools/list_changed` after a refresh that changed the list. A
  client that ignores the notification still needs a reconnect (or `python3 mcp/isaac_mcp/client.py list_tools`).
- With no bridge, or a bridge whose `list_tools` times out, the error says Kit is probably still starting and names any managed
  session with its age. With `wait_s` above 0 (clamped to 180) it polls every 3 s until the bridge answers. When another bridge
  is advertised, the error lists each one's port, PID and app with the `use_bridge` call and does not suggest waiting; an explicit
  `wait_s` is still honoured. It returns at once on an ambiguous "multiple bridges" error. A first Kit start took 172 s on a lab
  PC.

**`use_bridge(port)`** connects to a different bridge, reports its identity and replaces the previously discovered bridge tools.
The choice lasts until the host restarts.

**`status`** is the one check-in call; see [built-ins](#kit-bridge-built-ins). It also lists enabled, disabled and planned
toolsets, even with no bridge. Uncatalogued extension tools appear after discovery when `extensions` is enabled. To list
advertised bridges without an MCP connection:

```bash
python3 mcp/isaac_mcp/client.py --list-bridges
python3 mcp/isaac_mcp/client.py status --port 8766
python3 mcp/isaac_mcp/client.py list_tools --port 8766
```

The command-line client uses canonical bridge names with dots (`sim.stage.info`); the MCP server exposes them with double
underscores (`sim__stage__info`).

## Grouped tools

A client is shown one tool per subject, with an `action` argument, instead of one tool per operation:
`sim__prim(action="create", ...)` rather than `sim__prim__create(...)`. [TOOLS-ARGUMENTS.md](TOOLS-ARGUMENTS.md) lists every
grouped tool with its actions, by toolset.

- A grouped tool takes the arguments of the action it runs and returns that tool's own answer. An argument that belongs to
  another action is refused, and nothing runs. `action="help"` returns the full description and arguments of every action.
- The single-purpose names still work when called, with the same arguments and answers, so existing prompts and scripts keep
  running. They are deprecated: no longer listed, and new work should use the grouped form. Only members of enabled toolsets are
  callable. Tools that belong to no group (`status`, `run_python`, `sim__quick_start`, `sim__timeline__control`,
  `assets__import_path`, `screenshot`) are listed under their own names.
- Where a tool has an argument of its own called `action`, the grouped tool takes it as `sub_action`.

## Desktop and window tools

These tools use the host's X11 display (`DISPLAY`, default `:0`) and do not require a Kit bridge. Their behaviour depends on an
accessible graphical session. Screenshots are saved under `~/Pictures/screen-mcp/` and recordings under `~/Videos/screen-mcp/` on
the **host server** machine. Mouse and keyboard tools need the separate opt-in below.

- X11 reuses window IDs after a window closes. On a shared desktop, call `windows(action="list")` again right before a
  `screenshot` or `screen_recording(action="start")` with a `window_id`. Window capture reads the selected window's pixels, so it
  works when another window covers it. `windows(action="info")` reports the display, monitors, capture locations and recording state.
- A recording lives in the MCP server process. If that client exits first it leaves a very short MP4, and a later
  `screen_recording(action="stop")` from another process reports none active. Keep one client connected from start to stop.
- For a demo with Isaac Sim and RViz 2 together: `windows(action="tile", window_ids=[...])`, then
  `screen_recording(action="start", window_ids=[...])`; join clips afterwards with `join_videos`
  (`{"inputs":["intro.mp4","demo.mp4"],"output":"final.mp4"}`). For a clean simulator viewport without desktop chrome, record
  from a camera in the scene instead (`sim__camera`, or `camera=` on a controller run).
- Recording defaults: `full_screen` is false and `max_width` is 1920. A window keeps its native size up to 1920 wide. Explicit
  `full_screen=true` letterboxes into the monitor's shape at most `max_width` wide; pass `max_width=3840` for full-resolution
  full-screen output.
- On a locked or remote PC (monitor captures black, X input refused): window screenshots and recordings, `window_ids` recordings
  and locked monitor recordings work from window pixels; moving and tiling windows worked on GNOME/mutter (Ubuntu 22.04) while
  locked; raising a window cannot work and says so. A composed locked monitor recording costs about 150 ms per frame for six
  windows at 3440×1440 (about six distinct captures per second, with frames repeated to keep real time), so prefer `window_ids`
  for the windows that matter.

### Optional desktop input

These tools (`window_input`, toolset `input`) use the existing `python-xlib` dependency and the X11 XTEST extension; no `xdotool`
is needed. They work on the host's shared X11 desktop; native Wayland and headless sessions are not supported. They do not
require a bridge and can operate windows other than Isaac Sim and Isaac Lab, so enable them only for a client you trust. They are
off even when `ISAAC_MCP_TOOLSETS=all`. Window activation requires a window manager that implements `_NET_ACTIVE_WINDOW`.

Every input call also requires integer `window_id` and `expected_pid`, taken from a fresh `windows(action="list")` result.
Identify the actual client window, not an unrelated frame or dialog; some desktops list both.

1. Inspect a fresh `screenshot(window_id=...)`.
2. Use its **saved size**, not its potentially scaled inline image, for coordinates: convert inline-image coordinates by
   multiplying each axis by saved-size / inline-size. Coordinates are relative to the selected window's top-left corner.
3. Raise the window first if it is covered: `windows(action="raise", window_id=12345, expected_pid=6789)`.
4. Act. For a 1200×800 window with ID 12345 and PID 6789, a click is
   `{"action":"click","window_id":12345,"expected_pid":6789,"x":200,"y":100,"expected_width":1200,"expected_height":800}`.
5. Take a new screenshot after an action changes the UI, and refresh the size and coordinates after a resize.

Success means events were sent, not that the app processed them; a busy simulator can take several seconds to show the result.

- All input is refused while the desktop is locked or its screen saver is active: the lock screen holds the X keyboard and
  pointer grabs, so input and typed text would reach it. Typing is also refused while another client holds the keyboard grab
  (an open menu). For Isaac Sim's own UI use `sim__ui`, which drives Kit's input and keeps working on a locked or remote desktop.
- Pointer actions fail if the ID, PID or size changed, the point is offscreen, or another window covers a checked point.
  Keyboard actions need focus inside the selected window: click it first. Caps Lock must be off for typing and letter keys. A
  held key, mouse button or modifier causes the action to fail. Popup menus or dialogs outside the chosen window may need a
  separately listed target; unlisted or untitled popups cannot be controlled.
- There is no clipboard, shell command, Unicode typing or accessibility-tree API. Typing is 1–200 printable ASCII characters.
- X11 input is global: these checks reduce mistakes but are not a security boundary against other X11 clients, input grabs or a
  person changing focus. Cooperating MCP servers with the same `DISPLAY` spelling and `XDG_RUNTIME_DIR` share an input lock;
  other applications, users or differently configured servers do not take it. Do not share the desktop while an agent is
  acting; use a dedicated session for stronger isolation. Enabling the tools lets the client trigger any action in the targeted
  app, including file deletion or network operations. Tool annotations are advisory, and window IDs and PIDs are not authentication.

See the [open items](OPEN-ITEMS.md#tool-surface-and-reliability) for remaining work.

### Optional simulator sessions

Select the `launch` toolset explicitly to expose `sim__session` (actions `launch`, `list`, `status`, `stop`). It launches this
repository's `run.sh` and manages only processes it started; it does not attach to or stop unrelated simulators. It runs the
configured simulator Python, creates files, and can affect the GPU and network. Its annotations are conservative.

Example sequence: `sim__session` with `{"action":"launch"}` (port and ROS domain both chosen), or
`{"action":"launch","port":18766,"ros_domain":42}`; then `{"action":"status","session_id":"<id returned by launch>"}` until
`state` is `running`; select that port with `use_bridge` and verify its PID before operating the scene. If necessary, identify
its window with `windows(action="list")` by PID. Stopping needs `confirm_discard=true`; without it the error includes the exact
retry call, and `force=true` sends SIGKILL if a normal shutdown fails. It always refuses an external bridge, because this server
did not start it. `physics_backend="newton"` refuses before starting anything when the interpreter's Isaac Sim has no Newton
adapter, such as 5.1; a launch never falls back to another engine.

**Choosing the port and ROS domain.** With no `port` or `ros_domain`, the launch takes the first value that is free, unclaimed by
another launch and not advertised by a live bridge: inside `ISAAC_MCP_PORT_RANGE` and `ISAAC_MCP_ROS_DOMAIN_RANGE` when they are
set; otherwise from the configured port (else 8766) over the next 64 ports, and from this server's `ROS_DOMAIN_ID` (else 0) up to
231. The result's `port` and `ros_domain` are the chosen values and `auto_selected` names the ones chosen for you. A stated value
outside its range is refused as `PORT_OUT_OF_RANGE` or `ROS_DOMAIN_OUT_OF_RANGE`, naming the range. A refused port or domain
message suggests a free one, or `allow_shared_domain=true`. Managed launches accept ROS domains 0–231: with the bundled Cyclone
DDS profile's participant-index range, domain 232 would make discovery probe beyond UDP port 65535. Use different ROS domains for
isolated simulations; other ROS nodes must use the matching domain to communicate with a simulator.

**Claims.** Each launch claims its port and ROS domain in the bridge runtime folder (`$XDG_RUNTIME_DIR/isaac-mcp`, else
`/tmp/isaac-mcp-<uid>`): one file per value, created exclusively and holding the claiming host's PID, start time and boot ID. A
second launch is refused while that claim is live, including one from another host process with a different sessions directory. A
claim whose owner has died is removed. Claims are released when the launch returns or fails; from then on the bridge's portfile and
process environment make its port, PID and ROS domain visible to later launches. The folder must belong to you and not be writable
by group or others, and a symlink planted at a claim path is refused. A program that does not make claims can still take a port.

**States.** `starting` means Kit reports it is still initializing. `readiness_unknown` means the bridge answered but cannot report
Kit readiness (for example an older bridge); verify the application before using scene tools. An unavailable bridge reports
`starting_or_unresponsive`. Restart a session started with an older version so it receives the session identity.

**Environment.** A launched simulator inherits this server's environment, so `ISAAC_MCP_ASSET_ROOT` and `DISPLAY` come from the
`env` of your MCP client's server entry, not from a shell you opened later. The launch removes `PYTHONPATH` and `UR5E_DT_HEADLESS`,
sets its own port, bind address, ROS domain, backend and output folder, and passes `ISAAC_MCP_PYTHON`, `UR5E_DT_PYTHON` and
`ISAAC_MCP_WORKSPACE` only from the server's own settings. The operator's settings for ranges, instance count and free-memory
thresholds are in [ENVIRONMENT.md](ENVIRONMENT.md#managed-simulator-sessions-launch-toolset).

**Limits of the resource checks.** The instance count includes advertised local bridges, managed sessions still starting, and
running jobs in `ISAACLAB_JOBS_DIR`; processes outside those sources may not be counted. Port availability and ROS domains of known
processes are checked, but other unadvertised ROS nodes may share a domain. Host servers using the same sessions directory
serialise their launches, and each launch claims its port and ROS domain across host processes; other programs or differently
configured servers that do not claim them can still race the checks. The checks require Linux, `nvidia-smi` and exactly one NVIDIA
GPU. The thresholds and instance limit are operator settings, not guarantees: they do not reserve memory or predict scene,
renderer, sensor or training peaks, and nothing terminates a process if memory later runs low. Isaac Lab jobs have their own
[concurrency guards](TOOLS-ISAACLAB.md#concurrency-guards). Managed sessions use separate Kit user configuration files, but other
Kit caches and machine resources can still be shared. Stopping uses a Linux pidfd and the original boot and process-start identity
to avoid signalling a reused PID. Linux 6.9 and newer signal the process group; older kernels signal the leader only, reported as
`scope: "process"`, and a forced stop there may leave child processes that need separate cleanup.

### A project's own launcher

A project with its own environment can have `sim__session(action="launch")` start its launcher instead of `run.sh`. Set
`ISAAC_MCP_SIM_LAUNCHER` in the `env` of that project's MCP server entry; it is read when the server starts.

The launcher gets the same arguments as `run.sh` (`--port N`, `--headless`, `--motion-bvh`, then `-- <kit args>`) and the same
environment, including `ISAAC_MCP_PHYSICS_BACKEND`, and runs in its own folder. It must end by `exec`ing Kit, because the PID that
the launch records must be the simulator; otherwise `sim__session(action="status")` reports `port_conflict`. The file must be executable,
belong to you or root, and not be writable by group or others. For example, to use 5.1 with this repository's launcher:

```bash
#!/bin/bash
export ISAAC_MCP_PYTHON=<project>/.venv/bin/python
exec <repo>/run.sh "$@"
```

With a launcher set, refusing an engine the build lacks is the launcher's job (`run.sh` also refuses, with exit status 2).

This setting is deliberately not in `isaac-mcp.json` and not a tool argument. A launcher is an arbitrary command, and a model can
write files on this machine (`put_file` has no path limit), so a project file would let it choose what runs. The server
configuration comes from you, through your MCP client. `isaaclab_python` in `isaac-mcp.json` is a narrower exception: it is
honoured only for a project you named with `ISAAC_MCP_PROJECT` in that same configuration, and it must be a virtual environment
with Isaac Lab ([details](TOOLS-ISAACLAB.md#setup-and-configuration)).

## Kit bridge built-ins

These tools operate on the **bridge machine**, inside or alongside the Kit process. The host lists them from a packaged snapshot
(`mcp/isaac_mcp/catalogs/builtins.json`, regenerated by `scripts/gen_host_catalog.py --write`); each call is sent to the selected
bridge, and without one it returns an error that says what to do. `status` and `job_status` belong to `core`; `run_python`, the
file tools and `reload_tools` belong to `code`.

**`status`** answers "where am I" in one call. The reply is short: `running`, the selected bridge's `pid`, `port`, `app`,
`app_version`, `headless`, `app_ready` and `uptime_s`; `stage` with the `session_id` and `stage_id` the scene tools need, its
`source`, `timeline` (playing, paused or stopped) and `physics_engine`; `jobs_running`; `bridges` when more than one is advertised on
this machine; `managed_sessions` and `lab_jobs_running` when there are any; `toolsets`; and a short `host`. With no bridge it is
still an answer, not an error: `running: false`, a `message` naming the next call, and the same surroundings. A bridge that
answers but is busy, and a timeout, are still errors. Call it once before the first simulator action. `detail=true` returns the
full bridge identity instead: process identity, app version, Kit `app_ready` (`true`, `false`, or `null` if unknown), `busy` (what
is running and waiting), `read_only`, `auth` and `jobs`, plus the host's `version`, `pid` and, from a checkout, `commit`, `stale`
and a `warning` if the running host predates the checkout.

**`run_python`** executes Python on Kit's main thread with `omni`, `carb` and common USD symbols preloaded. Assign `result` to
return a value. Persistent sessions retain eligible variables for the same `session_id`; a stage-open event clears session state
when the subscription is available. The reply's `session_vars` lists only the names this call added or rebound, with
`session_var_count` for the total. `background=true` runs the code as a [job](#background-jobs) and returns its `job_id` at once.
Arbitrary code can change the scene or access files.

**`job_status`** and **`job_cancel`** are described under [background jobs](#background-jobs). **`reload_tools`** reloads an
autoloaded tool package by module name, then calls the host's `refresh_tools`; if that package has a call in flight, the reload is
refused until it finishes. **`put_file`**, **`get_file`** and **`stat_file`** (the `files` group) write, read and check a file on
the bridge machine: reads are for regular files up to 45 MiB, and a file is transmitted as one message of at most 64 MiB, so these
are for reasonably sized files, not bulk transfers. They run off Kit's main loop, so a large file does not hold up a frame.
Examples: `run_python` with `{"code":"result = 1"}`; `files` with `{"action":"stat","path":"/tmp/isaac-example.txt"}`; `files`
with `{"action":"put","path":"/tmp/isaac-example.txt","content_b64":"aGVsbG8="}` writes `hello`.

### Background jobs

Work that takes longer than one call should not be polled with more calls. `run_python` with `background=true`,
`sim__simulation(action="run")` and `sim__simulation(action="replay")` start a **job** in the Kit process and return a `job_id` at
once; `jobs(action="status", job_id=..., wait_s=...)` reads it and waits, `jobs(action="cancel")` stops it, and `status` lists the
running ones. This is the simulator-side counterpart of the Isaac Lab job tools, which manage separate processes.

- A job is a coroutine on Kit's main loop. It must only `await`; it must not pump the app loop itself. At most 4 run at once, and
  the last 32 finished ones are remembered until Kit restarts.
- Every job also has a record on disk, `<jobs folder>/<job_id>/job.json`, in the same format as an Isaac Lab job
  (`"runner": "kit"`). So the status call still answers after the simulator has exited (a job that was running then reads `failed`:
  the simulator exited before it finished), and `jobs(action="list")` shows simulator and Lab jobs together. The jobs folder is
  `ISAACLAB_JOBS_DIR`, or `~/.local/share/isaac-mcp/jobs`.
- A job is not a queued request. Another caller's `run_python` that pumps the app loop from inside its own call can end a job; the
  job then reads `failed` with the reason, never `running` forever.
- A job's state is `running`, `succeeded`, `failed` or `cancelled`, with `elapsed_s`, `progress`, the last 2000 characters of output,
  and the `result` or `error` once it has ended. A result larger than 16 KiB is cut and says so: write large data to a file and
  return its path. For a finished replay, or a controller run recorded with `camera`, the host encodes the frames and adds
  `video_path`.
- `wait_s` (0–60) is served inside Kit, off the main loop: the call returns when the job ends, with no polling and no simulator
  time spent, and sets `wait_timed_out` if the job is still running. Without a `job_id` the status call lists the simulator's jobs,
  newest first. A cancelled job ends at its next step and cleans up after itself; confirm with the status call. A Lab or script
  `job_id` given to the same calls is answered by the host from its own record, with no bridge needed, so one tool follows every
  job.
- Select `code` for Python execution and file transfer; toolsets are not access controls.

```python
# run_python, background=true: one call that runs the whole loop
import omni.kit.app
app = omni.kit.app.get_app()
for i in range(600):
    job.checkpoint()                      # stops here after a cancel
    await app.next_update_async()         # let Kit run a frame
    if i % 60 == 0:
        job.report(i / 600, f"frame {i}")
result = {"frames": 600}
```

## Trust and operational limits

The bridge listens on loopback and requires a token on every connection. The token is in a file only your account can read, so
your own clients connect without configuration and another local account cannot run Python in your simulator. The token is not
encryption: do not expose the bridge to an untrusted network; use an SSH tunnel, or a private address with `ISAAC_MCP_TOKEN` given
to the client. `ISAAC_MCP_READ_ONLY=1` starts a bridge that refuses every tool that changes something. Toolsets control host
registration, not direct bridge access. See [limiting what a client can reach](MCP-SERVER.md#limiting-what-a-client-can-reach).

Desktop capture depends on the display session and permissions, and can contain sensitive information. Confirm the target window
before capturing. See [server setup](MCP-SERVER.md), [architecture](ARCHITECTURE.md) and
[tool authoring](../mcp/docs/tool-authoring.md) for more detail.

# Host MCP Server: `isaac-mcp`

The project provides a single host-side Model Context Protocol (MCP) server that exposes Isaac Sim's scene and asset tools, Isaac Lab jobs and desktop capture to any MCP client. One lab's UR5e digital twin is included as a separate tool group.

The server package is located at `mcp/` (Python package `isaac_mcp`). The primary CLI executable is `isaac-mcp`. There is one command name and no aliases.

For the system layer breakdown, port mapping, and routing rules, see [docs/ARCHITECTURE.md](ARCHITECTURE.md).

```
Kit Process (Isaac Sim 6.2.0.0)
  ur5e-dt extension ──registers──► shared bridge :8766 ◄── other tool packs
                                    │ NDJSON over TCP
                                    ▼
                        isaac-mcp (stdio MCP server)
                          ├── ur5e_dt__*        catalog-listed; execute through the twin bridge
                          ├── list_bridges      running Kit bridge instances
                          ├── use_bridge        switch target port at runtime
                          ├── sim__* / assets__*  generic scene tools, from the sim-tools extension
                          ├── refresh_tools     rediscover tools; can wait for a starting Kit
                          ├── isaaclab__*       Isaac Lab jobs, run on the host
                          └── screenshot / screen_* / list_windows / move_window / tile_windows / join_videos
```

Inside Kit, `exts/ur5e-dt` registers on the `isaac.mcp.bridge` singleton alongside other packs. The launcher sets
the shared port to 8766 by default and delays binding until the twin has registered its tools. Disabling the twin
removes its live handlers without stopping the shared bridge; the host keeps their catalog entries visible, and an
in-flight call must drain before the twin can re-register.

---

## 1. Installation

The host MCP server is installed automatically when running `./install.sh` from the repository root:

```bash
./install.sh --no-sim           # Host only, editable via uv tool; installs no Sim or Lab, records a Sim it finds
# Optional, only to register these clients:
./install.sh --no-sim --claude  # Also writes Claude Code and Claude Desktop configs
```

For the pinned simulator installation, follow [Linux setup](LINUX-SETUP.md); the full installer
requires `--accept-eula` (or an existing `OMNI_KIT_ACCEPT_EULA=YES`).

To install or reinstall only the host MCP tool manually:

```bash
uv tool install --force --editable ./mcp
```

Because it is installed in editable mode from `./mcp`, modifications to `mcp/isaac_mcp/` take effect whenever the MCP server process restarts.

`install.sh` also creates an ffmpeg shim at `~/.local/bin/ffmpeg` (a fixed default) for the twin's viewport recorder, unless an
unrelated file or symlink already occupies that path. Direct `uv tool install` does not create the shim.

---

## 2. Available Tools

### Generic scene and assets (`sim__*`, `assets__*`)

The generic tools are catalog-listed by default. `assets__roots`, `assets__scan` and
`assets__search` execute on the host; the others require `sim-tools` in the selected Kit.
See [scene tools](TOOLS-SCENE.md) for trusted-root configuration and runtime prerequisites,
and [the agent tier map](../AGENTS.md#what-you-can-reach-by-tier) for counts and filters.

The twin's own robot, sensor and bench files are resolved separately from these tools.
Set `ISAAC_MCP_ASSET_PATH` (deprecated alias `UR5E_DT_ASSET_CACHE`) and optionally
`ISAAC_MCP_ASSET_STRICT=1` (alias `UR5E_DT_OFFLINE`) in the **Kit** environment. See
[Assets: stores, project file and doctor](ASSETS.md).

### RISE lab's UR5e twin (`ur5e_dt__*`)
The host lists a packaged catalog of UR5e tools even before a simulator is running. Calls require an Isaac Sim
session running `exts/ur5e-dt`; when connected, the bridge's live metadata takes precedence. Kit's
canonical `ur5e_dt.<tool>` names appear to MCP clients as `ur5e_dt__<tool>`. The pack includes scene setup,
`play_scene`/`stop_scene`, object management, ROS graphs and viewport recording. See the complete
[UR5e tool reference](TOOLS-UR5E.md) for actual names, parameters and side effects. Joint-position and pause tools
are not registered by this pack.

### Bridge discovery and selection

- `list_bridges`: Lists all running Kit bridge instances (PID, port, and app name). *Note: This is a Kit bridge built-in. It is listed from startup but answers only when a bridge is reachable.*
- `use_bridge`: Points the host MCP server at a different bridge port at runtime without restarting the client.
- `refresh_tools`: Forces rediscovery of tools from the currently connected bridge. `refresh_tools(wait_s=120)` waits (up to 180 s) for a Kit that is still starting.

### Desktop & Window Capture Tools
- `screenshot`: Captures an image of the Isaac Sim window (or desktop/monitor) and returns an inline thumbnail plus saved PNG path.
- `list_windows`: Lists visible X11 windows with their window IDs, geometry, class, and titles.
- `raise_window`: Brings a specified window to the front.
- `start_screen_recording`: Records window or desktop activity to MP4 at a specified framerate (default 15 fps). Window capture reads that window's buffer directly, so overlapping windows do not obscure it.
- `stop_screen_recording`: Finalizes recording and returns the path to the MP4.
- `screen_recording_status`: Reports elapsed recording time and captured frames.
- `screen_info`: Reports active display, attached monitors, capture directories, and recording state.
- `move_window` / `tile_windows`: Place one window, or lay out two to six, for a recording.
- `join_videos`: Join clips into one new MP4.

Captures are written to `~/Pictures/screen-mcp/` and `~/Videos/screen-mcp/`. Screen recordings do not contain audio.
See the [host and bridge reference](TOOLS-HOST.md) for the complete list and arguments. The separate
[Isaac Lab reference](TOOLS-ISAACLAB.md) covers the 12 host-side Lab tools.

### Agent workflow

Use the available tools to inspect and operate the user's authorized simulator
before asking the user to repeat terminal or UI steps. `list_windows` identifies
the window and PID; `screenshot(window_id=...)` captures that window even when
covered. Check the capture directory before writing if workspace rules restrict
where files may be created. Screen tools are host-side and can work even when
the simulator bridge is disconnected. Optional X11 mouse and keyboard controls can be enabled
explicitly with `ISAAC_MCP_LOCAL_TOOLS=all,screen_input`; see the targeting rules and shared-desktop
limitations in [host tools](TOOLS-HOST.md#optional-desktop-input).

The host MCP server and the bridge inside Kit are separate processes. A working
`list_windows` or Isaac Lab tool does not mean that the bridge is connected, and a
client that lists the UR5e tools or `run_python` has not proved that a bridge exists: these are catalog-listed
(call `status`). The client may have connected before Kit started (call `refresh_tools`, or
`refresh_tools(wait_s=120)` while Kit boots),
or `ISAAC_MCP_NAMESPACE` / `ISAAC_MCP_BUILTINS=0` may be hiding bridge tools
(verify with the CLI below). Reuse the intended bridge when it meets the task; start another Kit
only when explicitly needed and after the [parallel-launch checks](TOOLS-HOST.md#optional-simulator-sessions).
If `refresh_tools` returns `Connection refused`, check for the intended Kit
process, its enabled bridge extension and listening port. The server cannot
control a simulator that did not start a bridge. Once a bridge is available,
call `refresh_tools`, then use `status` to verify its PID and port before
calling `run_python` or any scene-changing tool. When several instances run,
select the intended port with `use_bridge`; never assume the visible window is
the bridge target. A 6.2 host environment does not prove that a 5.1 process has
loaded a compatible bridge. The generic `isaac.mcp.bridge` was also tested in
an Isaac Sim 5.1 process on 2026-10-30: after enabling it and setting
`ISAAC_MCP_PORT`, `status` reported the expected 5.1 process and PID.

The host advertises `tools.listChanged` and sends `notifications/tools/list_changed` after
`refresh_tools` or `use_bridge` adds, updates or removes tools, so clients that honour it (Claude
Code 2.1.314 does) show the change mid-session. If a client does not expose newly discovered tools
after `refresh_tools`, reconnect it or use the standard-library CLI against
the verified bridge. List the advertised bridges first and use the host and port it reports; 8766
below is only the twin's default port:

```bash
python3 mcp/isaac_mcp/client.py --list-bridges
python3 mcp/isaac_mcp/client.py status --port 8766
python3 mcp/isaac_mcp/client.py run_python '{"code":"result = 1"}' --port 8766
```

Run this from the checkout root; it needs no packages. `python -m isaac_mcp.client` is the same CLI, for an interpreter
that has the host installed. For a user-started Kit
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

By default, the bridge binds to port **8766**. Parallel sessions need their own bridge ports, ROS domains, scene outputs and asset staging directories. For example, start these in separate terminals after replacing the placeholders with existing paths and creating the output directories:

```bash
# Terminal 1: Sim 5.1
ISAAC_MCP_PYTHON=<sim51-env>/bin/python \
ISAAC_MCP_ASSET_CONFIG=<session51>/asset-config.json \
ISAAC_MCP_SCENE_OUTPUT=<session51>/output ROS_DOMAIN_ID=41 \
  ./run.sh --headless --port 18751

# Terminal 2: Sim 6.2
ISAAC_MCP_PYTHON=<sim62-env>/bin/python \
ISAAC_MCP_ASSET_CONFIG=<session62>/asset-config.json \
ISAAC_MCP_SCENE_OUTPUT=<session62>/output ROS_DOMAIN_ID=42 \
  ./run.sh --headless --port 18762
```

Each asset config must exist before that process starts; configure its own staging directory and approved roots as described in [trusted projects](TOOLS-SCENE.md#one-time-approval). Approved source roots can be shared when both sessions need the same read-only inputs; never share staging or output directories between concurrent jobs. ROS nodes that should talk to a session must use that session's domain. To discover the actual running processes and check the target before editing a scene:

```bash
python3 mcp/isaac_mcp/client.py --list-bridges
python3 mcp/isaac_mcp/client.py status --port 18751
python3 mcp/isaac_mcp/client.py status --port 18762
```

In an MCP client, call `list_bridges`, then select and verify the intended process with `use_bridge(port=18762)` and `status`. The selection belongs to the host MCP server process and persists until its restart. For agents working simultaneously, give each its **own MCP server process** and select its port there; otherwise one agent's `use_bridge` can redirect the other's next call. Keep each scene's returned `session_id` and `stage_id` with its own agent and recheck them after a restart or stage change.

Two headless processes, Sim 5.1 and 6.2, were exercised together on a 16 GB RTX 5080 with small robot-import and physics workloads. This is not a capacity guarantee: scene complexity, sensors, renderers and training can require much more memory. Check available GPU and system memory before starting, monitor both workloads, and stop only sessions you own. Headless still uses GPU resources.

For managed launches, `sim__launch` chooses distinct free ports and ROS domains, applies resource checks and gives each session its own output folder; its [configuration and limits](TOOLS-HOST.md#optional-simulator-sessions) describe how to select interpreters and inspect/stop sessions. Isaac Lab has separate job and GPU guards; its [concurrency rules](TOOLS-ISAACLAB.md#concurrency-guards) explain explicit overrides. Do not treat a guard override as extra GPU capacity.


---

## 4. Client Configuration

Replace `<repo>` with your checkout (suggested: `<workspace>/isaac-mcp`; see
[WORKSPACE.md](WORKSPACE.md)) and `<home>` with your home directory. Optional environment
variables for any client below:

- `ISAAC_MCP_WORKSPACE=<workspace>` makes `<workspace>/isaac-mcp-assets` the asset folder (otherwise it is `isaac-mcp-assets` next to the checkout).
- `ISAAC_MCP_SIM_ROOT=<repo>` tells managed `sim__launch` sessions which checkout holds `run.sh`;
  set it when the host is installed outside the checkout.
- `ISAAC_MCP_PYTHON=<env>/bin/python` pins the Sim interpreter that `run.sh` uses.

All settings are listed in [Environment variables](ENVIRONMENT.md).

### Claude Code

Run `./install.sh --no-sim --claude-code`, or configure it with the `claude mcp` CLI:

```bash
claude mcp add --scope user isaac \
  -e DISPLAY="${DISPLAY:-:0}" \
  -e ISAAC_MCP_PORT=8766 \
  -- "$(command -v isaac-mcp || echo "$HOME/.local/bin/isaac-mcp")"
```

### Claude Desktop

Add the server to `~/.config/Claude/claude_desktop_config.json`, or run `./install.sh --no-sim --claude-desktop`:

```json
{
  "mcpServers": {
    "isaac": {
      "command": "isaac-mcp",
      "args": [],
      "env": {
        "ISAAC_MCP_PORT": "8766",
        "DISPLAY": ":0"
      }
    }
  }
}
```
*(Ensure `DISPLAY` matches the active session, for example `:0` or `:1`.)*

### OpenCode

Add the server to `~/.config/opencode/opencode.jsonc` (`./install.sh --no-sim --print-opencode` prints an entry with your values):

```jsonc
"isaac": {
  "type": "local",
  "command": ["isaac-mcp"],
  "environment": {
    "ISAAC_MCP_PORT": "8766",
    "DISPLAY": ":0"
  }
}
```

### Codex

Add the server with the Codex CLI, or as a `[mcp_servers.isaac]` table in `~/.codex/config.toml`
(the key is `mcp_servers`, not `mcpServers`):

```bash
codex mcp add isaac --env DISPLAY="${DISPLAY:-:0}" --env ISAAC_MCP_PORT=8766 \
  -- "$(command -v isaac-mcp || echo "$HOME/.local/bin/isaac-mcp")"
```

```toml
[mcp_servers.isaac]
command = "<home>/.local/bin/isaac-mcp"

[mcp_servers.isaac.env]
DISPLAY = ":0"
ISAAC_MCP_PORT = "8766"
# For the isaaclab__* job tools:
# ISAACLAB_PYTHON = "<workspace>/envs/lab-3.0/bin/python"
# ISAACLAB_ROOT = "<workspace>/IsaacLab"
# ISAAC_MCP_WORKSPACE = "<workspace>"
```

### Antigravity

Add the server to `~/.gemini/config/mcp_config.json` (a workspace's `.agents/mcp_config.json` is not read; inside a workspace, only a plugin's `plugins/<name>/mcp_config.json` is),
or open the agent panel's **…** menu → **MCP Servers** → **Manage MCP Servers** → **View raw
config**. The file does not allow comments.

```json
{
  "mcpServers": {
    "isaac": {
      "command": "<home>/.local/bin/isaac-mcp",
      "args": [],
      "env": {
        "DISPLAY": ":0",
        "ISAAC_MCP_PORT": "8766"
      }
    }
  }
}
```

Format from [Antigravity's MCP docs](https://antigravity.google/docs/mcp) and
[Codex's MCP docs](https://developers.openai.com/codex/mcp). On the test machine, `codex exec`
called `screen_info` through a Codex entry of this form (2026-10-31); the Antigravity entry has not
been tested here.

### Agent skill

The repository's `isaac-mcp` skill tells an agent how to use these tools safely in Claude Code,
Codex, OpenCode and Antigravity. See [AGENT-SKILLS.md](AGENT-SKILLS.md).

### Limiting what a client can reach

Bridge tools such as `run_python` and the `ur5e_dt__*` scene operations reach the client as soon as a Kit bridge
exists, whatever the host's local tools are set to. Decide per client how much of the server it should see, and be
clear about which of the settings below limit what.

Server side (set in the client's `env` for this server; see [TOOLS-HOST.md](TOOLS-HOST.md)):

- `ISAAC_MCP_LOCAL_TOOLS` selects groups of the host's own tools (24 standard screen, Isaac Lab, gateway tools, plus
  five opt-in desktop input and four opt-in simulator-session tools). `readonly`
  keeps only local tools annotated as read-only; `none` keeps none. The default is `all`. The Python probes and job
  inspection tools are excluded because they can have side effects; see [TOOLS-HOST.md](TOOLS-HOST.md) for groups and
  migration. Importing the jobs module can still reconcile orphaned jobs at server startup,
  regardless of the groups. The setting does **not** affect tools discovered from a Kit bridge, which are not
  annotated, so `readonly` is a read-only *local tool selection*, not a read-only server: a client that can still see
  bridge tools can change the scene.
- `ISAAC_MCP_NAMESPACE` limits the bridge tools to the named namespaces, and `ISAAC_MCP_BUILTINS=0` hides the bare
  bridge built-ins such as `run_python`. An empty `ISAAC_MCP_NAMESPACE` means no filter, not "no namespaces".
- The three variables are independent and compose. With `ISAAC_MCP_LOCAL_TOOLS=readonly` alone, every bridge tool is
  still registered; with `ISAAC_MCP_BUILTINS=0` added, the namespaced ones (for example `ur5e_dt__*`) still are.

**Hiding bridge tools.** There is no dedicated switch. To hide both built-ins and namespaced tools, use
`ISAAC_MCP_BUILTINS=0` together with an `ISAAC_MCP_NAMESPACE` naming a namespace that neither the bridge nor the
packaged catalog advertises (the test suite covers this). Disabling built-ins alone does not hide the catalog-listed
UR5e tools. This hides tools from the client's list; it is not a safety boundary. It relies on the chosen name staying
unused, `use_bridge` and `refresh_tools` (if the `gateway` group is on) re-run discovery under the same filter, and
anything that reaches the bridge socket directly (the `isaac_mcp.client` CLI, another process) is not affected by a
host's filters at all.

**A profile that cannot change the scene** needs local tools that could change the scene restricted, and bridge calls
independently restricted by enforcement that does not depend on the client or on which tools the host lists. The bridge
policy mechanisms below require code, not just configuration:

- `isaac_mcp/gate.py` is a deny-gate *mechanism*: it can read and validate a policy file (`load_hooks`) and decide a
  call (`evaluate`). Nothing in the host or the bridge loads a policy file or calls it; there is no environment
  variable or setting that turns it on, and this repository ships no policy.
- On the host, `isaac_mcp.server.register_pre_dispatch_hook(fn)` runs `fn(name, params)` before each dynamically
  registered bridge tool call through that host and can refuse it. It must be registered by Python in the host process;
  the stock `isaac-mcp` command has no plugin loader, so that means your own launcher that imports
  `isaac_mcp.server`, registers the hook (it may call `gate.evaluate`) and then calls `main()`. It covers only the
  bridge tools called through that process: not the local tools, discovery and status calls made by the host itself,
  or callers that dial the bridge directly.
- On the bridge, a tool pack running in Kit can install policy with `registrar.refuse_calls_with(fn)` (see
  [tool authoring](../mcp/docs/tool-authoring.md)). While installed, it applies at bridge dispatch across namespaces
  and callers; it is removed when its owner drains. A rule that must hold regardless of the client needs a policy that
  remains installed and covers every relevant call, including calls that could remove or bypass it. It, too, is code
  in a Kit extension.

Client side (these are client settings, not part of this server; check your client's documentation):

- Register the server per project rather than globally if only some projects need it. In Claude Code that is the
  project or local scope instead of `--scope user`.
- In Claude Code, `--tools` restricts the built-in tools only. To start a process that must not see MCP tools at all,
  pair it with `--strict-mcp-config --mcp-config <file>`, where the file lists only the servers that process may use
  (an empty `{"mcpServers":{}}` for none).
- Give subagents an explicit tool allowlist. A subagent without one can inherit the parent's MCP tools.
- Every local tool carries MCP annotations (`readOnlyHint`, `destructiveHint`), which clients can use for approval
  prompts. Bridge tools do not. Annotations are hints, not an access control; the filters above decide what is
  registered at all, and none of them replaces a policy enforced at the bridge.

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

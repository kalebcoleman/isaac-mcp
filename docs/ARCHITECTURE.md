# How isaac-mcp works

This page explains the whole system: what an MCP server is, how a client connects to this one, how the server reaches
Isaac Sim and Isaac Lab, where assets, recordings and settings live, and how the names and ports fit together.
For setup steps see [Linux setup](LINUX-SETUP.md) and [MCP server](MCP-SERVER.md); for every tool see the
[tool arguments](TOOLS-ARGUMENTS.md).

![The MCP client talks to the isaac-mcp host server over stdio. The server answers some tools itself, forwards simulator tools over TCP to the bridge extension inside Isaac Sim, and runs Isaac Lab and script jobs as separate processes. Assets, staging, outputs and job logs are files on the same machine.](media/architecture.svg)

The diagram source is [`architecture.drawio`](architecture.drawio) (open it at [diagrams.net](https://app.diagrams.net)).

## In one minute

The [Model Context Protocol](https://modelcontextprotocol.io) (MCP) is how an AI agent's client (Claude Code, Codex,
OpenCode and others) uses outside tools. The client starts an **MCP server** as a child process, asks it which tools it
has, and calls them by name with JSON arguments. The server does the work and returns text or images.

`isaac-mcp` is such a server. It is a small Python program that starts in about a second and needs no simulator to
start. It offers three kinds of tool:

| Kind | Examples | Where the work happens |
|---|---|---|
| Host tools | `isaac_help`, `screenshot`, `join_videos`, `assets__search`, `sim__launch` | In the server process itself |
| Simulator tools | `sim__*`, `assets__import_path`, `ur5e_dt__*`, `status`, `run_python`, `job_status` | Inside a running Isaac Sim, reached over a local TCP connection |
| Job tools | `isaaclab__train`, `isaaclab__eval`, `run_script` | In a separate process the server starts and watches |

## The pieces

| Piece | Where | What it is |
|---|---|---|
| Host server | `mcp/isaac_mcp/` (command `isaac-mcp`) | The MCP server. Speaks MCP to the client on stdio; is a TCP client of the bridge. |
| Bridge | `exts/isaac.mcp.bridge/` | A Kit extension inside Isaac Sim: a TCP server that runs tool calls on Kit's main thread. Robot-agnostic. |
| Scene tools | `exts/sim-tools/` | A Kit extension that registers the 70 generic sim and assets tools on the bridge. |
| UR5e twin | `exts/ur5e-dt/` | One lab's digital twin: 23 `ur5e_dt.*` tools on the same bridge. Optional. |
| Launch and checks | `run.sh`, `install.sh`, `scripts/` | Start Isaac Sim with the extensions, install the host, run checks. |

They are separate on purpose. The host must start instantly and must never need the 20 GB simulator environment just
to take a screenshot or manage a job. The bridge knows nothing about robots, so any Kit app and any tool pack can use
it. The scene tools know nothing about the UR5e. The twin keeps its robot-specific logic to itself, and its USD assets
are not in the repository; they live in packs in the local [asset root](ASSET-PACKS.md).

## How a client connects

The client's configuration names a command to run (see [client configuration](MCP-SERVER.md#4-client-configuration)):

```json
{"mcpServers": {"isaac": {"command": "isaac-mcp", "env": {"ISAAC_MCP_PYTHON": "/path/to/isaac-sim/python"}}}}
```

The client starts `isaac-mcp`, and the two exchange JSON-RPC messages on the process's stdin and stdout: `initialize`,
`tools/list`, then `tools/call` for each tool the agent uses. Each client gets its own server process, so two agents do
not share a server, though they can talk to the same simulator.

The server lists its full tool set from the first `tools/list`, whether or not Isaac Sim is running. The simulator
tools are listed from a snapshot packaged with the server (`mcp/isaac_mcp/catalogs/`); each one dials the selected
bridge only when it is called, and answers with what to do if none is there. `status` is the one check-in call: it says
which simulator answered, what else is running, and the IDs the scene tools need, or that nothing is running.

## How a tool call travels

1. The client sends `tools/call` with a name such as `sim__prim` and `action="create"`. A grouped tool checks that
   the arguments belong to that action and passes the call to the single-purpose tool behind it (`sim__prim__create`,
   which can also be called by that name).
2. The server checks the arguments against the tool's schema.
3. It takes one of three routes:
   - **Host tool:** the server runs it and answers.
   - **Simulator tool:** the server sends one JSON line to the bridge
     (`{"id": 7, "type": "sim.prim.create", "params": {...}}`) and waits for the line that echoes its `id`. The bridge
     runs the handler on Kit's main thread. For an asset import the
     server first checks the file is in an approved folder, converts it if needed, and copies a snapshot into a staging
     folder that Kit reads.
   - **Job tool:** the server starts a process in its own process group, records it in the jobs folder, and returns a
     `job_id` at once. A job inside the simulator writes the same kind of record to the same folder.
4. The answer goes back to the client as text (usually JSON), with MCP's error flag set when the tool failed.

**Names.** Inside Kit a tool is `<namespace>.<tool>`: `sim.stage.info`, `ur5e_dt.play_scene`. MCP tool names cannot
contain dots, so the server registers them as `sim__stage__info`, `ur5e_dt__play_scene`. A client is shown related
tools as one grouped tool with an `action` (`sim__stage`, `ur5e_dt__scene`): about 45 names to read instead of 134.
The [table of groups](TOOLS-HOST.md#grouped-tools) maps each action to its single-purpose tool. Tools built into the bridge
(`status`, `run_python`, `job_status`, the file tools), the gateway tools (`use_bridge`, `refresh_tools`) and the desktop
tools (`screenshot`, `list_windows`) have no prefix.

**Long work.** A call that would take longer than a moment returns a `job_id` instead of blocking. Inside the
simulator that is `run_python` with `background=true`, `sim__simulation__run` and `sim__trajectory__replay`; on the
host it is every `isaaclab__*` launch and `run_script`. `job_status` (with `wait_s`) and `job_cancel` follow and stop
either kind, and both kinds keep their record in one folder in one format, so a job can still be read after the
simulator has exited.

## How the server reaches Isaac Sim

**The bridge.** `isaac.mcp.bridge` listens on one TCP port per Kit process, on loopback by default. The protocol is
one JSON object per line each way ([protocol](../mcp/docs/protocol.md)). Tool packs register their tools on it under a
namespace. Three rules decide when a call runs: tools that only read the bridge's own state (`status`, `job_status`)
are answered at once, off the main thread; a tool that advances the app itself (`run_python`) runs alone; every other
tool runs in arrival order. The bridge also keeps background jobs.

**Who may connect, and what they may do.** Every connection presents a token. The bridge writes it to a file only
your account can read, and clients on that account read it from there. Started with `ISAAC_MCP_READ_ONLY=1`, the
bridge runs only tools that change nothing and refuses the rest, for every client.

**Starting a simulator.** `./run.sh` (add `--headless` for no window) starts Isaac Sim with the bridge, the scene
tools and the twin enabled, on port 8766 unless told otherwise. `sim__launch`, when that tool group is enabled, does the
same for the agent, on a free port with its own ROS domain and output folder, after checking GPU and memory headroom.
The server never starts a simulator on its own.

**Finding it.** A running bridge writes a small portfile (`$XDG_RUNTIME_DIR/isaac-mcp/`, or `/tmp/isaac-mcp-<uid>/`)
with its PID and port. The server picks the bridge to dial in this order:

1. An explicit choice: `use_bridge(port)` in the session, or `--port` on the command-line client.
2. `UR5E_DT_PORT`, the older name for the twin's port.
3. `ISAAC_MCP_PORT`.
4. Portfile discovery, when exactly one bridge is advertised. With several and no explicit choice it refuses to guess
   and lists them.

| Port | Used by |
|---|---|
| 8766 | The default bridge port of `run.sh` and client configurations. |
| 8767 | Convention for a second bridge, such as the Isaac Lab bridge example. |
| any | `./run.sh --port 0` asks the OS for a free port; `sim__launch` picks one in `ISAAC_MCP_PORT_RANGE`. |

A configured port that is already taken refuses at startup rather than moving somewhere no client could predict.
`ISAAC_MCP_BIND` can change the bind address; the bridge runs arbitrary Python by design, so beyond loopback use an
SSH tunnel or a private network, and give the client the token ([protocol](../mcp/docs/protocol.md#several-simulators-and-other-machines)). The twin shares the bridge: it
unregisters its own namespace on shutdown and leaves the bridge and the other packs running.

## Isaac Lab jobs

The `isaaclab__*` tools never go through the bridge. They run Isaac Lab's own scripts (train, play, and this
project's evaluation, check and record runners) with the interpreter of an Isaac Lab install, as separate processes.
Each job gets a folder with its command, log, exit code and results; the server's job tools read that folder, so a job
outlives the client and the server that started it. An install is chosen once by `ISAACLAB_PYTHON` and
`ISAACLAB_ROOT`, or per call with `install=`. A job starts its own Kit and uses the GPU, so by default one runs at a
time and none starts beside a running simulator. `run_script` uses the same job system for any Python script, with no
GPU guards. See [Isaac Lab tools](TOOLS-ISAACLAB.md).

## Assets

Assets are not in the repository. They live in an **asset root** of versioned packs (`ISAAC_MCP_ASSET_ROOT`), and in
project folders the user has approved. The server will only read a file inside one of those; only the user can approve
a folder. `assets__import_path` picks the format, converts URDF, xacro, SDF and MuJoCo files on the host without running
any code from them, and hands Kit a verified snapshot in a staging folder both processes can read. Kit then references
the snapshot into the scene's session layer, so source files never change. See [Asset packs](ASSET-PACKS.md) and
[importing](ASSET-IMPORT.md).

## Seeing and recording

| Source | Tools | Needs |
|---|---|---|
| A window on the desktop | `screenshot`, `start_screen_recording`, `stop_screen_recording` | An X11 display. A window is read by its id, so it works when covered or when the screen is locked. |
| A camera in the scene | `sim__camera__create`, `sim__camera__capture`; `camera=` on `sim__simulation__run` and `sim__trajectory__replay` | A running simulator. Works headless. |
| An Isaac Lab task | `isaaclab__record`, or `isaaclab__play` with `--video` | An Isaac Lab install. Works headless. |
| Saved clips and frames | `join_videos` (join, contact sheet, frames to MP4) | Nothing but the files. |

Screenshots and recordings go to `~/Pictures/screen-mcp` and `~/Videos/screen-mcp`. Camera captures, frames and
controller logs go to the simulator's output folder (`ISAAC_MCP_SCENE_OUTPUT`).

## Configuration

Settings are environment variables, read by the process they concern: the server (set them in the client's entry),
Kit (set them where `run.sh` runs; `sim__launch` passes the server's on), or an Isaac Lab job. The full list is in
[Environment variables](ENVIRONMENT.md). The ones that shape the system:

| Variable | Read by | Purpose |
|---|---|---|
| `ISAAC_MCP_PORT` | server, Kit | The bridge port to dial, and to listen on. |
| `ISAAC_MCP_PYTHON` | `run.sh`, `sim__launch` | The Isaac Sim interpreter to start. |
| `ISAAC_MCP_ASSET_ROOT` | server, Kit | The asset root. |
| `ISAAC_MCP_SCENE_OUTPUT` | Kit | Where scene tools write captures, saves and logs. |
| `ISAACLAB_PYTHON`, `ISAACLAB_ROOT` | server | The default Isaac Lab install. |
| `ISAAC_MCP_TOOLSETS` | server | Which tools a client is shown. These control visibility, not security. |
| `ISAAC_MCP_READ_ONLY`, `ISAAC_MCP_AUTH`, `ISAAC_MCP_TOKEN` | Kit (the token also by clients) | Enforced by the bridge: read-only mode, and the token connections must present. |
| `ISAAC_MCP_ENABLE_SCENE_SCRIPTS` | Kit | Opt in to tools that run the caller's Python inside Kit. |

## Naming rules

| Entity | Rule | Examples |
|---|---|---|
| Kit extension ID and folder | The folder under `exts/` is the extension ID | `ur5e-dt`, `sim-tools`, `isaac.mcp.bridge` |
| Python package | The ID with underscores | `ur5e_dt`, `sim_tools`, `isaac_mcp_bridge`, `isaac_mcp` |
| Command and server name | One name, no aliases | `isaac-mcp` |
| Tool | `<namespace>.<tool>` in Kit, `<namespace>__<tool>` in MCP | `sim.stage.info`, `sim__stage__info` |

## ROS 2 and DDS

The digital twin includes OmniGraph nodes for ROS 2 transform publishing and joint state mirroring.

### Mandatory DDS Sourcing Rule

**The simulation and every ROS 2 client or inspection script communicating with it MUST source `config/ros_dds.env` before execution:**

```bash
source config/ros_dds.env
```

`config/ros_dds.env` enforces:
1. `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`: Standardizes on Eclipse Cyclone DDS across all nodes.
2. `ROS_LOCALHOST_ONLY=1`: Confines communication strictly to the loopback interface (`127.0.0.1`).
3. `CYCLONEDDS_URI=file://.../config/cyclonedds.xml`: Explicitly disables multicast in the Cyclone DDS XML configuration (`<Multicast>disabled</Multicast>`).

> [!IMPORTANT]
> If a ROS client does not source `config/ros_dds.env`, Cyclone DDS default multicast settings will fail to discover the localhost-confined simulation publisher. The nodes will report no errors, but messages publish into the void and subscribers receive 0 messages. `./run.sh` automatically sources `config/ros_dds.env`.

---

## Transform tree

In Isaac Sim 6.2, transform publication was modernized to use the standard OmniGraph nodes:

```
USD Stage Prims ──► IsaacComputeTransformTree ──► ROS2PublishTransformTree ──► /tf, /tf_cam, /objects_poses_sim
```

On 6.x builds that have the compute node, this replaces the deprecated `targetPrims` pose-tree interface. 5.1.0.0 and 6.0.0.0 don't have that node and use the older publisher. On 6.2 the older interface still published the whole tree in testing, but logs a deprecation warning.

### Frame Naming Preservation

Frame naming rules in `IsaacComputeTransformTree` preserve existing ROS consumer expectations:
- Unique prims retain their bare name (e.g., `base1`, `inverted_u_yellow`, `line_green`).
- Duplicate prim names under different parents resolve to `<parent>_<child>`.
- Any prim with the string attribute `isaac:nameOverride` uses that override unconditionally.

### Intentional 180° Camera Rotation

`IsaacComputeTransformTree` deliberately applies a **180° rotation around the X-axis** to all `UsdGeomCamera` prims (`ros2.nodes/docs/CHANGELOG.md:310`, version 1.14.0):
- **Rationale:** USD native cameras look down the $-Z$ axis with $+Y$ up. ROS optical frames convention expects $+X$ forward, $+Y$ right, $+Z$ down (or $+Z$ forward for optical axes). NVIDIA corrected the camera transform evaluation in 6.2 to align with ROS standard conventions.
- **Impact:** Transforms published on `/tf_cam` reflect this 180° X rotation compared to the old 5.1 output. Plain `UsdGeomXform` objects (such as those published on `/objects_poses_sim`) are unaffected.
- **Instruction:** **Do not attempt to "fix" or revert this rotation.** The change reflects the official Isaac Sim ROS 2 node standard and represents the pipeline functioning correctly. Reference values are recorded in `docs/provenance/tf-baseline-6.2.json`.

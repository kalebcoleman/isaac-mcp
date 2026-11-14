# Toolsets

A **toolset** is a set of tools that are switched on or off together. The server registers only the tools of the
enabled toolsets, so a client is shown a short tool list. A toolset that is off registers nothing: no listed tool, no
hidden single-purpose tool. Every registered tool belongs to exactly one toolset.

Some listed tools are **grouped tools**: one tool with an `action` argument that runs several single-purpose tools, for
example `sim__prim(action="create")`. The counts below are listed tools. Every tool and its arguments are in
[TOOLS-ARGUMENTS.md](TOOLS-ARGUMENTS.md), which has one section per toolset.

## The toolsets

The table is generated from the code (`scripts/gen_tool_reference.py --write`); `twinsmith toolsets` prints the same
rows for the server you have installed, with the current state of each.

<!-- generated:toolsets-full -->
| Toolset | Default | Listed tools |
|---|---|---|
| `core`: Connection, discovery, help and job tracking | on | `help`, `jobs`, `refresh_tools`, `status`, `toolsets`, `use_bridge` |
| `scene`: Create and edit scenes and primitives | on | `sim__light`, `sim__material`, `sim__prim`, `sim__quick_start`, `sim__stage`, `sim__timeline__control` |
| `physics`: Physics, joints, arms and simulation | on | `sim__arm`, `sim__joints`, `sim__physics`, `sim__simulation` |
| `sensors`: Cameras, LiDAR and viewport framing | on | `sim__camera`, `sim__lidar`, `sim__viewport__frame` |
| `assets`: Import and manage robot and scene assets | on | `assets__import_path`, `assets__library`, `assets__pack` |
| `code`: Execute code and scripts, transfer files | on | `files`, `reload_tools`, `run_python`, `run_script`, `sim__controller` |
| `screen`: Capture, record and arrange desktop windows | on | `doctor`, `join_videos`, `screen_recording`, `screenshot`, `windows` |
| `lab`: Isaac Lab training and evaluation | off | `isaaclab__checkpoint_register`, `isaaclab__inspect`, `isaaclab__launch`, `isaaclab__metrics` |
| `ros2`: ROS 2 transforms, graphs and occupancy maps | off | `sim__graph`, `sim__map__occupancy`, `sim__ros2__tf_setup` |
| `kit_debug`: Inspect Kit logs and operate its UI | off | `sim__logs`, `sim__ui` |
| `workcell`: UR5e workcell tools | off | `workcell__cell`, `workcell__eval`, `workcell__jenga`, `workcell__perception`, `workcell__planning`, `workcell__real2sim`, `workcell__robot` |
| `robosim`: RoboSimStudio real-to-sim and MuJoCo system identification | off | `robosim`, `sysid` |
| `input`: Desktop mouse and keyboard input | explicit only | `computer` |
| `launch`: Launch and stop simulator processes | explicit only | `sim__session` |
| `extensions`: Tools from uncatalogued extension namespaces | on | none |
<!-- /generated -->

- `core` is always on and cannot be turned off.
- `input` and `launch` are never part of a profile, not even `all`. Name them to get them (`+input`, `+launch`).
- `screen` and `input` are not specific to Isaac Sim: `screenshot`, `windows`, `doctor` and `computer` work on any X11 window
  (FreeCAD, RViz 2, a browser), so they carry no `sim__` prefix. `sim__ui` and `sim__logs` (`kit_debug`) need Kit. The
  [computer use workflow](TOOLS-HOST.md#computer-use-workflow) covers both.
- `extensions` carries the tools that a project's own Kit extension registers under a namespace this repository does not
  catalogue. It lists nothing until such an extension connects.
- `workcell` is the lab's UR5e work cell; see its [README](../exts/workcell/workcell/README.md). Its tools for real-to-sim,
  planning and evaluation answer that they are not implemented.
- `robosim` holds two grouped tools, each running in its own Python environment and needing no Kit bridge: `robosim`
  runs RoboSimStudio ([ROBOSIM.md](ROBOSIM.md)) and `sysid` fits model parameters with MuJoCo ([SYSID.md](SYSID.md)).
- The status also names toolsets that are **planned** and not built yet; they list no tools.

## Choosing toolsets

Five sources set the toolsets. Each later source overrides the earlier ones:

| Order | Source | Where |
|---|---|---|
| 1 | Built-in default | The `default` profile |
| 2 | User file | `~/.config/twinsmith/config.toml` |
| 3 | Project file | `twinsmith.toml`, found by walking up from the server's working directory |
| 4 | Environment | `TWINSMITH_TOOLSETS` |
| 5 | Command line | `twinsmith --toolsets ...` |

A source that does not mention toolsets leaves the choice from the sources before it. In the files the list is
`toolsets` under `[tools]`:

```toml
[tools]
toolsets = ["default", "+lab"]
```

In the environment and on the command line it is one comma-separated string, `default,+lab`.

### Syntax

The list starts from a profile and adjusts it, left to right:

- `default`, `all` or `minimal` replaces everything chosen so far with that profile.
- `+name`, or a bare `name`, adds a toolset.
- `-name` removes one.
- `core` is always on, so a list that names no profile starts from `core` alone.

An unknown name, or `-core`, stops the server at startup and lists the valid names. The choice is read once, when the
server starts: after changing a file or variable, restart the MCP server (your client's reconnect or restart action).

Three worked examples:

1. **Everyday work plus Isaac Lab training.** The default tools, and the Lab tools.

   ```toml
   [tools]
   toolsets = ["default", "+lab"]
   ```

2. **A work-cell PC that does not need desktop capture.** The default minus `screen`, plus the work cell.

   ```bash
   TWINSMITH_TOOLSETS=default,-screen,+workcell twinsmith toolsets
   ```

   `twinsmith toolsets` prints the result. In an MCP client's configuration the same value goes in the server's `env`.

3. **Lab training only, with the smallest tool list.** Connection tools, Lab, and `run_python` with the file tools.

   ```bash
   twinsmith --toolsets core,lab,code
   ```

`twinsmith --toolsets core,lab,code toolsets` prints the table for that choice without starting the server.

### Profiles

| Profile | Contains |
|---|---|
| `minimal` | `core` only |
| `default` | `core`, `scene`, `physics`, `sensors`, `assets`, `code`, `screen`, `extensions` |
| `all` | every toolset except `input` and `launch` |

### Size of the tool list

A client reads every listed tool's description and argument schema, so a smaller list leaves more room for the task.
The counts below are generated from the code; the sizes were measured with
[`scripts/measure_tool_list.py`](../scripts/measure_tool_list.py), which builds the server offline and counts the
characters of what `tools/list` returns. Tokens are estimated as characters divided by four.

<!-- generated:profiles -->
| Profile | Toolsets | Listed tools |
|---|---|--:|
| `minimal` | `core` | 6 |
| `default` | `core`, `scene`, `physics`, `sensors`, `assets`, `code`, `screen`, `extensions` | 32 |
| `all` | `core`, `scene`, `physics`, `sensors`, `assets`, `code`, `screen`, `lab`, `ros2`, `kit_debug`, `workcell`, `robosim`, `extensions` | 50 |
| `all,+input,+launch` | `core`, `scene`, `physics`, `sensors`, `assets`, `code`, `screen`, `lab`, `ros2`, `kit_debug`, `workcell`, `robosim`, `input`, `launch`, `extensions` | 52 |
<!-- /generated -->

| Selection | Listed tools | Characters | Estimated tokens |
|---|--:|--:|--:|
| `minimal` | 6 | 6,346 | 1,586 |
| `default` | 32 | 61,863 | 15,465 |
| `all` | 49 | 92,518 | 23,129 |
| `all,+input,+launch` | 51 | 97,491 | 24,372 |
| `core,lab,code` | 15 | 22,027 | 5,506 |
| `core,workcell` | 13 | 10,470 | 2,617 |

Before toolsets existed the server always listed 48 tools, about 28,450 estimated tokens. The listed text is a summary:
`action="help"` on a grouped tool and `help(tool="<name>")` return the full description. A test
(`mcp/tests/test_toolsets.py`) fails when a profile grows past its budget. Measure your own selection; the argument is the
same text the server reads:

```bash
cd mcp
uv run --locked --isolated --python 3.12 --extra test python ../scripts/measure_tool_list.py TWINSMITH_TOOLSETS=default
```

## What is on, and what is off

- **`status`**, the tool an agent calls first, answers with or without a simulator, and carries a `toolsets` field:
  `on` (the enabled toolsets), `off` (each disabled toolset with a one-line summary), `planned` and `enable` (how to turn
  one on). It also reports the stage's physics engine once a simulator answers.
- **`twinsmith://toolsets`** is an MCP resource with the same JSON, for clients and people that read resources.
- **`help`** names the toolset when the tool for a task is off.
- **`twinsmith toolsets`** prints the table from a terminal.

## Enabling a toolset while the server runs

The `toolsets` tool is in `core`, so it is always listed. Its actions:

- `toolsets(action="list")` returns what `status` reports: the toolsets that are on, the ones that are off with a one-line
  summary and tool count, the planned ones, and which can be enabled now.
- `toolsets(action="enable", names=["robosim"])` registers those toolsets' tools for the rest of this server process and
  tells the client the tool list changed. The result names the tools that became available. A client that loads tools
  lazily may need to look a new tool up by name before calling it.
- `toolsets(action="disable", names=["screen"])` removes them again. `core` cannot be disabled.

Rules the server enforces:

- `input` and `launch` are never enabled this way. They let a client act on the desktop or start simulator processes, so
  only a person can turn them on, in the configuration, before the server starts. A call that names one of them is refused
  and changes nothing.
- A change made with this tool lasts until the server stops. It is not written to any file.
- `[tools] runtime_enable = false` in a configuration file, or `TWINSMITH_RUNTIME_ENABLE=0`, turns `enable` and `disable`
  off; `list` still answers.

What the tool changes is the listed tools; it does not change any permission.

## Toolsets control what is listed, not what is allowed

Toolsets are a visibility preference. They are **not a security boundary**. Two things are enforced inside the simulator,
whatever a client is shown:

- the bridge **token**, which every connection must present;
- **read-only mode** (`TWINSMITH_READ_ONLY=1` where Kit starts), which refuses every tool that changes state.

A client that can reach the bridge with the token can still send any registered call to it. To limit what an agent can
change, start the simulator read-only; see [limiting what a client can reach](MCP-SERVER.md#limiting-what-a-client-can-reach).

## Old settings

The selection variables of 0.2 (named in [MIGRATING.md](MIGRATING.md#environment-variables)) are gone. A server that finds one set stops at startup and names it. Remove the old variable and set `TWINSMITH_TOOLSETS` instead.

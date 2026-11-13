# Toolsets

A **toolset** is a set of tools that are switched on or off together. The server registers only the tools of the
enabled toolsets, so a client is shown a short tool list. A toolset that is off registers nothing: no listed tool, no
hidden single-purpose tool. Every registered tool belongs to exactly one toolset.

Some listed tools are **grouped tools**: one tool with an `action` argument that runs several single-purpose tools, for
example `sim__prim(action="create")`. The counts below are listed tools. Every tool and its arguments are in
[TOOLS-ARGUMENTS.md](TOOLS-ARGUMENTS.md), which has one section per toolset.

## The toolsets

The table is generated from the code (`scripts/gen_tool_reference.py --write`); `isaac-mcp toolsets` prints the same
rows for the server you have installed, with the current state of each.

<!-- generated:toolsets-full -->
<!-- /generated -->

- `core` is always on and cannot be turned off.
- `input` and `launch` are never part of a profile, not even `all`. Name them to get them (`+input`, `+launch`).
- `extensions` carries the tools that a project's own Kit extension registers under a namespace this repository does not
  catalogue. It lists nothing until such an extension connects.
- `workcell` is the lab's UR5e work cell; see its [README](../exts/ur5e-dt/workcell/README.md). Its tools for real-to-sim,
  planning and evaluation answer that they are not implemented.
- `robosim` is one grouped tool that runs RoboSimStudio in its own Python environment; it needs no Kit bridge. See
  [ROBOSIM.md](ROBOSIM.md).
- The status also names toolsets that are **planned** and not built yet; they list no tools.

## Choosing toolsets

Five sources set the toolsets. Each later source overrides the earlier ones:

| Order | Source | Where |
|---|---|---|
| 1 | Built-in default | The `default` profile |
| 2 | User file | `~/.config/isaac-mcp/config.toml` |
| 3 | Project file | `isaac-mcp.toml`, found by walking up from the server's working directory |
| 4 | Environment | `ISAAC_MCP_TOOLSETS` |
| 5 | Command line | `isaac-mcp --toolsets ...` |

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
   ISAAC_MCP_TOOLSETS=default,-screen,+workcell isaac-mcp toolsets
   ```

   `isaac-mcp toolsets` prints the result. In an MCP client's configuration the same value goes in the server's `env`.

3. **Lab training only, with the smallest tool list.** Connection tools, Lab, and `run_python` with the file tools.

   ```bash
   isaac-mcp --toolsets core,lab,code
   ```

`isaac-mcp --toolsets core,lab,code toolsets` prints the table for that choice without starting the server.

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
<!-- /generated -->

| Selection | Listed tools | Characters | Estimated tokens |
|---|--:|--:|--:|
| `minimal` | 5 | 6,506 | 1,626 |
| `default` | 30 | 79,269 | 19,817 |
| `all` | 47 | 115,982 | 28,995 |
| `all,+input,+launch` | 49 | 121,057 | 30,264 |
| `core,lab,code` | 14 | 29,624 | 7,406 |
| `core,workcell` | 12 | 11,033 | 2,758 |

These sizes were measured on the commit that added this page, before the work that shortens tool descriptions. They will
fall when that work lands. Measure your own selection; the argument is the same text the server reads:

```bash
cd mcp
uv run --locked --isolated --python 3.12 --extra test python ../scripts/measure_tool_list.py ISAAC_MCP_TOOLSETS=default
```

## What is on, and what is off

- **`status`**, the tool an agent calls first, answers with or without a simulator, and carries a `toolsets` field:
  `on` (the enabled toolsets), `off` (each disabled toolset with a one-line summary), `planned` and `enable` (how to turn
  one on). It also reports the stage's physics engine once a simulator answers.
- **`isaac-mcp://toolsets`** is an MCP resource with the same JSON, for clients and people that read resources.
- **`isaac_help`** names the toolset when the tool for a task is off.
- **`isaac-mcp toolsets`** prints the table from a terminal.

## Enabling a toolset while the server runs

> **Status: described from the design, to be checked against the final code.** Another change adds a `toolsets` tool to the
> `core` toolset; its arguments are not described here until that code is merged.

The `toolsets` tool has the actions `list`, `enable` and `disable`, so an agent can turn a toolset on for the rest of a
session instead of restarting the server. `input` and `launch` are never enabled this way: those two let a client act on
the desktop or start simulator processes, so only a person can turn them on, by editing the configuration before the server
starts. What the tool changes is the listed tools; it does not change any permission.

## Toolsets control what is listed, not what is allowed

Toolsets are a visibility preference. They are **not a security boundary**. Two things are enforced inside the simulator,
whatever a client is shown:

- the bridge **token**, which every connection must present;
- **read-only mode** (`ISAAC_MCP_READ_ONLY=1` where Kit starts), which refuses every tool that changes state.

A client that can reach the bridge with the token can still send any registered call to it. To limit what an agent can
change, start the simulator read-only; see [limiting what a client can reach](MCP-SERVER.md#limiting-what-a-client-can-reach).

## Old settings

`ISAAC_MCP_LOCAL_TOOLS`, `ISAAC_MCP_NAMESPACE`, `ISAAC_MCP_BUILTINS`, `ISAAC_MCP_SCENE_TOOLS` and
`ISAAC_MCP_GROUP_TOOLS` are gone. A server that finds one set stops at startup and prints the `ISAAC_MCP_TOOLSETS` line
that replaces it. Remove the old variable and use that line.

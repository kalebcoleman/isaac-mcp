# twinsmith

A tool for building and driving robot simulations and digital twins from an AI agent: you describe what you want, and
the agent builds the scene, moves the robot, looks through its cameras and reads its sensors. It works with
NVIDIA Isaac Sim and Isaac Lab today, and adds ROS 2, folders of verified assets called asset packs, a lab work cell,
and a bridge to RoboSimStudio. It speaks [MCP](https://modelcontextprotocol.io), so Claude Code, Codex, OpenCode
and other MCP clients can use it.

[![CI](https://github.com/kalebcoleman/twinsmith/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kalebcoleman/twinsmith/actions/workflows/ci.yml)
[![Source version 0.3.0](docs/media/badges/version.svg)](mcp/pyproject.toml)
[![Isaac Sim scoped tests](docs/media/badges/sim.svg)](docs/COMPATIBILITY.md)
[![Host Python 3.10+, Sim Python 3.12](docs/media/badges/python.svg)](docs/LINUX-SETUP.md#which-environment-does-what)
[![License and asset notices](docs/media/badges/license.svg)](docs/VENDORING.md)

[Quick start](#quick-start) · [Toolsets](#toolsets) · [Examples](docs/EXAMPLES.md) · [How it works](docs/ARCHITECTURE.md) ·
[Documentation index](docs/README.md) · [For agents](AGENTS.md)

## Quick start

You need Linux x86_64 (Ubuntu 24.04 or 22.04), [uv](https://docs.astral.sh/uv/getting-started/installation/), an NVIDIA GPU,
and an Isaac Sim 6.x that is already installed. This never downloads a simulator. Run every command from the checkout
root. If you have no Isaac Sim yet, read the [Linux setup](docs/LINUX-SETUP.md#new-pinned-62-installation) first.

**1. Install the server.**

```bash
git clone https://github.com/kalebcoleman/twinsmith.git
cd twinsmith
python3 scripts/env.py find     # lists the Isaac Sim installs it can see
./install.sh --no-sim                 # installs only the MCP server
export PATH="$HOME/.local/bin:$PATH"
twinsmith toolsets                    # prints the toolset table
```

You should see `find` list at least one Isaac Sim; with none it prints `no Isaac Sim / Isaac Lab environment found`, and you
set `ISAACSIM_PATH` or `TWINSMITH_SIM_PYTHON` (see [Linux setup](docs/LINUX-SETUP.md#what-is-auto-detected-and-where-it-looks)).
The installer prints `== using the Isaac Sim already installed: ...` and ends with `== install complete`.
`twinsmith toolsets` prints one row per toolset with `on` or `off` and a tool count.

**2. Start a simulator** and leave that terminal open. The first start can take minutes while shaders compile.

```bash
./run.sh --headless      # no window; leave --headless out for a window. A GPU is needed either way.
```

You should see Kit's startup log and no `FAIL:` line. When it is ready, this prints JSON that contains `"app_ready": true`:

```bash
python3 mcp/twinsmith/client.py status --port 8766
```

**3. Connect a client.** For Claude Code:

```bash
./install.sh --no-sim --claude-code
```

For another client, add this to its MCP configuration (see [client configuration](docs/MCP-SERVER.md#4-client-configuration)):

```json
{"mcpServers": {"twinsmith": {"command": "twinsmith", "env": {"TWINSMITH_PORT": "8766"}}}}
```

Start a new client session. The client should list the tools of the default toolsets (see the table below).

**4. Call `status`.** Ask the agent: *"Call status and tell me what it reports."* Or call the tool yourself. It answers
with `running: true`, the simulator's `pid`, `port` and `app_version`, the stage's `session_id` and `stage_id`, the
timeline state, and a `toolsets` field naming what is on and what is off. With no simulator it says `Nothing is running`
and names the next call. Call it once; the answer does not need repeating before each tool.

**5. Build a scene.** Ask: *"Call sim__quick_start, then take a picture with sim__camera and tell me what you see."*
`sim__quick_start` needs no arguments. It builds a ground plane, a physics scene, lights and a camera in a new stage, leaves
the timeline stopped, and returns the new `session_id`, `stage_id`, the paths it made and the next steps. Add a robot by
name (`robot="franka"`) or a whole scene (`environment="warehouse"`). More prompts are in [Examples](docs/EXAMPLES.md).

If a step fails, see [troubleshooting the first connection](docs/LINUX-SETUP.md#troubleshooting-the-first-connection).

## Toolsets

Tools are grouped into toolsets that you switch on or off. A client is shown only the tools of the enabled toolsets,
which keeps the tool list small. The counts below are listed tools; related tools share one grouped tool with an
`action` argument.

<!-- generated:toolsets-compact -->
| Toolset | Default | Tools | What it holds |
|---|---|--:|---|
| `core` | on | 6 | Connection, discovery, help and job tracking |
| `scene` | on | 6 | Create and edit scenes and primitives |
| `physics` | on | 4 | Physics, joints, arms and simulation |
| `sensors` | on | 3 | Cameras, LiDAR and viewport framing |
| `assets` | on | 3 | Import and manage robot and scene assets |
| `code` | on | 5 | Execute code and scripts, transfer files |
| `screen` | on | 5 | Capture, record and arrange desktop windows |
| `lab` | off | 4 | Isaac Lab training and evaluation |
| `ros2` | off | 3 | ROS 2 transforms, graphs and occupancy maps |
| `kit_debug` | off | 2 | Inspect Kit logs and operate its UI |
| `workcell` | off | 7 | UR5e workcell tools |
| `robosim` | off | 1 | RoboSimStudio real-to-sim runs in its own private environment |
| `input` | explicit only | 1 | Desktop mouse and keyboard input |
| `launch` | explicit only | 1 | Launch and stop simulator processes |
| `extensions` | on | 0 | Tools from uncatalogued extension namespaces |
<!-- /generated -->

Turn one on in `~/.config/twinsmith/config.toml`, with `TWINSMITH_TOOLSETS` or with `--toolsets`:

```toml
[tools]
toolsets = ["default", "+lab", "+ros2"]
```

[TOOLSETS.md](docs/TOOLSETS.md) has the precedence, the syntax and the measured size of each profile. Every tool and
argument is in [TOOLS-ARGUMENTS.md](docs/TOOLS-ARGUMENTS.md), generated from the code.

**Physics engine.** The target engine and the current default are stated in
[COMPATIBILITY.md](docs/COMPATIBILITY.md#target-engine). The Isaac Sim builds tested so far, and their limits, are in the same page.

> [!WARNING]
> `run_python`, the file tools and trusted asset loading run with your account's permissions and are not sandboxed.
> Keep the bridge on loopback and approve only project folders you trust. The bridge accepts only connections that
> present its token, which your own account's clients find automatically, and `TWINSMITH_READ_ONLY=1` starts a simulator
> that agents can inspect but not change. Toolsets control what is listed, not what is allowed.
> See the [trust limits](docs/TOOLS-HOST.md#trust-and-operational-limits).

## Where to go next

| I want to... | Read |
|---|---|
| See what an agent can do, with pictures | [Examples](docs/EXAMPLES.md) |
| Understand the parts and where each setting is read | [How it works](docs/ARCHITECTURE.md) · [Environment variables](docs/ENVIRONMENT.md) |
| Choose which tools a client is shown | [Toolsets](docs/TOOLSETS.md) |
| Find an argument or default | [Tool arguments](docs/TOOLS-ARGUMENTS.md) |
| Set up a machine or configure a client | [Linux setup](docs/LINUX-SETUP.md) · [Client configuration](docs/MCP-SERVER.md#4-client-configuration) |
| Import my own robot, or use asset packs | [Importing assets](docs/ASSET-IMPORT.md) · [Asset packs](docs/ASSET-PACKS.md) |
| Use scene, physics and sensor tools | [Scene tools](docs/TOOLS-SCENE.md) |
| Train a policy with Isaac Lab | [Isaac Lab tools](docs/TOOLS-ISAACLAB.md) |
| Use ROS 2, RViz 2 or Nav2 | [ROS 2 and RViz 2](docs/ROS2-RVIZ.md) |
| Run the lab's UR5e work cell | [Work cell](exts/workcell/workcell/README.md) · [work-cell tools](docs/TOOLS-WORKCELL.md) |
| Use RoboSimStudio | [RoboSimStudio](docs/ROBOSIM.md) |
| Run LEGO bricks (BrickSim) | [BrickSim](docs/BRICKSIM.md) |
| Check a machine or read the test matrix | [Verification](docs/VERIFICATION.md) · [Compatibility](docs/COMPATIBILITY.md) |
| Write my own tools | [Tool authoring](mcp/docs/tool-authoring.md) |
| Work as an agent in this repository | [AGENTS.md](AGENTS.md) · [Agent skill](docs/AGENT-SKILLS.md) |

## Development

The host server is in `mcp/`, the Kit extensions in `exts/`, and the helpers in `scripts/`. Before pushing, run the CPU-only
checks CI runs:

```bash
python3 scripts/check_docs.py
python3 scripts/check_assets.py
python3 scripts/check_portable_paths.py
python3 scripts/gen_host_catalog.py --check
python3 scripts/gen_tool_reference.py --check
cd mcp && uv run --locked --isolated --python 3.12 --extra test --extra isaaclab --extra scene-test pytest -q
```

CI covers the host only; it does not establish GPU or simulator behaviour. The live checks are in
[Verification](docs/VERIFICATION.md). Do not run `uv sync` or `uv run` in the repository root unless you want the 20 GB
simulator environment.

## Credits and license

MIT licensed. This project grew out of work by Aldrin Inbaraj, a lab mate: the UR5e work cell began as a fork of
his MIT-licensed [isaac-sim-mcp](https://github.com/inbarajaldrin/isaac-sim-mcp), and the bridge and client were first
built on his [omni-kit-mcp](https://github.com/inbarajaldrin/omni-kit-mcp) before being rewritten here. Thank you, Aldrin.
Component and asset terms differ, so read [VENDORING](docs/VENDORING.md) and the
[third-party notices](THIRD-PARTY-NOTICES.md) before redistributing. NVIDIA's installed runtime packages remain subject to
their own terms.

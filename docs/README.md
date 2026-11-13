# Documentation index

Start with the [project README](../README.md). Agents should start with [AGENTS.md](../AGENTS.md).
When two pages disagree, trust the **current reference** over a design or history page, and the code
over both.

## Current reference

**Set up and connect**

| Page | Use it for |
|---|---|
| [Linux setup](LINUX-SETUP.md) | Prerequisites, install paths, build selection, assets, first-connection troubleshooting |
| [MCP server](MCP-SERVER.md) | Client configuration (Claude Code and Desktop, Codex, OpenCode, Antigravity), limiting what a client can reach |
| [Workspace](WORKSPACE.md) | One folder for the checkout, assets and environments; what is auto-detected |
| [Assets](ASSETS.md) | Asset stores, project file, doctor, moving assets between machines |
| [Importing any asset](ASSET-IMPORT.md) | One import for USD, URDF, xacro, SDF, MJCF and robot folders: formats, safety, physics after import |
| [Environment variables](ENVIRONMENT.md) | Every setting, and which process reads it |
| [Compatibility](COMPATIBILITY.md) | Tested simulator builds, modes and open issues |

**Tools**

| Page | Covers |
|---|---|
| [Tool arguments](TOOLS-ARGUMENTS.md) | Every tool's arguments and defaults, generated from the code |
| [Scene and assets](TOOLS-SCENE.md) | `sim__*`, `assets__*`: trusted projects, contracts, sensors, physics, limits |
| [Host and bridge](TOOLS-HOST.md) | Gateway, desktop, opt-in input and sessions, bridge built-ins |
| [UR5e twin](TOOLS-UR5E.md) | `ur5e_dt__*`: the digital twin pack |
| [Isaac Lab](TOOLS-ISAACLAB.md) | `isaaclab__*`: discovery, jobs, guards, metrics |
| [BrickSim](BRICKSIM.md) | LEGO bricks with these tools: the bridge next to BrickSim, which tools work on bricks, its RL task |
| [Agent skill](AGENT-SKILLS.md) | The `isaac-mcp` skill and where each client finds it |

**Workflows**

| Page | Use it for |
|---|---|
| [ROS 2 and RViz 2](ROS2-RVIZ.md) | TF setup, RViz config, the TF doctor, Nav2 maps |
| [Isaac Sim 6.2 notes](ISAAC-6.2-NOTES.md) | Adapting labs written for Isaac Sim 6.0 |
| [Verification](VERIFICATION.md) | Host and live checks, expected values and limits; what to record so a result can be reproduced |

**Understand and extend**

| Page | Use it for |
|---|---|
| [How it works](ARCHITECTURE.md) | The whole system with a diagram: what MCP is, how a client connects, how the server reaches Isaac Sim (bridge, ports, launch), Isaac Lab jobs, assets, recording, configuration, naming, ROS 2 rules |
| [Host package guide](../mcp/README.md) | The server package: what a client is shown, loading the bridge in another Kit app, scripts without MCP |
| [Tool authoring](../mcp/docs/tool-authoring.md) | Your own tool package: arguments, errors, flags, reloading, Kit surprises |
| [Bridge protocol](../mcp/docs/protocol.md) | Messages, finding a bridge, the token, scheduling, read-only mode, jobs, access from another machine |
| Extension notes: [bridge](../exts/isaac.mcp.bridge/docs/README.md) · [sim-tools](../exts/sim-tools/README.md) · [ur5e-dt](../exts/ur5e-dt/docs/README.md) | What each Kit extension is |

**Project**

| Page | Use it for |
|---|---|
| [Working checklist](CHECKLIST.md) | Current priorities and open work |
| [Publishing](PUBLISHING.md) | PyPI release steps |
| [Vendoring](VENDORING.md) · [Asset provenance](provenance/ASSETS.md) · [Third-party notices](../THIRD-PARTY-NOTICES.md) | Licenses and origins |
| [Media](media/README.md) | Provenance of the images in the README |

## NVIDIA resources

The [agent skill guide](AGENT-SKILLS.md#nvidias-skills) links NVIDIA's documentation MCP
and official Isaac Sim and Isaac Lab skills. Use the [working checklist](CHECKLIST.md) for future work.

## Evidence

Use [compatibility](COMPATIBILITY.md) for tested builds and known limits, and
[verification](VERIFICATION.md) for checks. The examples and navigation walkthrough keep their supporting material under `docs/evidence/`.

## Scripts

Run these from the checkout root. **Disposable** means the script changes the stage, so point it at a session you can throw
away. Scripts that import `mcp` need `uv run --locked --isolated --project mcp --extra scene-test --python 3.12 python scripts/<name>`
(`scene-test` adds the `pxr` and `numpy` the scene validators import); a plain `python3` will not find them.

| Purpose | Scripts |
|---|---|
| Find and prepare | `isaac_env.py` (find an install, create a workspace), `isaac_assets.py` (asset stores), `fetch_ur5e_assets.py` (NVIDIA assets), `install.py` (register the bridge in a Kit app), `check_machine.sh` (6.2 preflight; can use the GPU) |
| Start | `run_ur5e_dt.py` (what `run.sh` runs) |
| Project checks (CPU, also in CI) | `check_docs.py`, `check_assets.py`, `check_portable_paths.py`, `check_private_paths.py`, `gen_host_catalog.py`, `gen_tool_reference.py` |
| UR5e checks (live, disposable) | `workcell_smoke.py` (requires an explicitly selected disposable bridge and lab config) |
| Smoke checks (live, disposable) | `smoke.py <toolset>`; `smoke.py all --list` lists GPU/simulator requirements offline. Validators live in `scripts/smoke/`; helpers: `scene_mcp_client.py`, `smoke_mcp_surface.py`, `make_scene_fixture.py`, `make_scene_package_fixture.py` |
| Offline surface audits | `measure_tool_list.py [KEY=VALUE ...] [--json]`, `check_names.py --audit [--json]` (name enforcement is opt-in, not yet CI) |
| ROS 2 | `ros_env.sh` (source it), `ros2_rviz_config.py`, `ros2_tf_doctor.py`, `cmd_vel_sequence.py` |

`mcp/templates/` holds bridge integration templates.

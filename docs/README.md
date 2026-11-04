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
| [Agent skill](AGENT-SKILLS.md) | The `isaac-mcp` skill and where each client finds it |

**Workflows**

| Page | Use it for |
|---|---|
| [ROS 2 and RViz 2](ROS2-RVIZ.md) | TF setup, RViz config, the TF doctor, Nav2 maps |
| [Isaac Sim 6.2 notes](ISAAC-6.2-NOTES.md) | Adapting labs written for Isaac Sim 6.0 |
| [Verification](VERIFICATION.md) | Host and live checks, expected values, latest results |
| [Reproducibility](REPRODUCIBILITY.md) | Locked setup, what has been verified, recording your own run |

**Understand and extend**

| Page | Use it for |
|---|---|
| [Architecture](ARCHITECTURE.md) | Layers, ports, tool routing, DDS rules, TF, drive gains |
| [Host package guide](../mcp/README.md) | The gateway, permanent bridge registration, adding a tool project |
| [Tool authoring](../mcp/docs/tool-authoring.md) | Handler contract, hot reload, control panel, Kit gotchas |
| [Bridge protocol](../mcp/docs/protocol.md) · [Bridge configuration](../mcp/docs/configuration.md) · [Bridge in Isaac Lab](../mcp/docs/isaac-lab.md) | Wire protocol, knobs, standalone Lab scripts |
| Extension notes: [bridge](../exts/isaac.mcp.bridge/docs/README.md) · [panel](../exts/isaac.mcp.panel/docs/README.md) · [sim-tools](../exts/sim-tools/README.md) · [ur5e-dt](../exts/ur5e-dt/docs/README.md) | What each Kit extension is |

**Project**

| Page | Use it for |
|---|---|
| [Roadmap](ROADMAP.md) | Status and next work |
| [Publishing](PUBLISHING.md) | PyPI and npm release steps |
| [Vendoring](VENDORING.md) · [Asset provenance](provenance/ASSETS.md) · [Third-party notices](../THIRD-PARTY-NOTICES.md) | Licenses and origins |
| [Media](media/README.md) | Provenance of the images in the README |

## Design and history

These explain why things are the way they are. They can describe proposals or behavior that later changed,
so do not use them as an API reference.

| Page | What it is |
|---|---|
| [Generic Sim tools design](GENERIC-SIM-TOOLS.md) | The original `sim.*` proposal; broader than what shipped |
| [Isaac Lab design notes](ISAACLAB-TOOLS.md) | Design and job-guard history |
| [Ecosystem by capability](ECOSYSTEM.md) · [by repository](ECOSYSTEM-BY-REPO.md) | Generated survey of other Isaac MCP servers |
| [Fork comparison](FORK-COMPARISON.md) | Comparison with one pinned fork |
| [Plans](plans/README.md) | Implementation plans and follow-ups |

## Evidence

[`docs/evidence/`](evidence/project-scene/README.md) holds dated test records, including the [Lab 3 run 8 record](evidence/lab3-run8-2026-11-03.md). They describe what was observed at that
time and are not rewritten later, so paths, counts and versions in them can be out of date on purpose.

## Scripts

Run these from the checkout root. **Disposable** means the script changes the stage, so point it at a session you can throw
away. Scripts that import `mcp` need `uv run --isolated --project mcp --python 3.12 python scripts/<name>`;
a plain `python3` will not find it.

| Purpose | Scripts |
|---|---|
| Find and prepare | `isaac_env.py` (find an install, create a workspace), `isaac_assets.py` (asset stores), `fetch_ur5e_assets.py` (NVIDIA assets), `install.py` (register the bridge in a Kit app), `check_machine.sh` (6.2 preflight; can use the GPU) |
| Start | `launch_ur5e_dt.sh` (`run.sh --ur5e-panel`), `run_ur5e_dt.py` (what `run.sh` runs) |
| Project checks (CPU, also in CI) | `check_checkpoint_refs.py` (used by the verifier), `check_docs.py`, `check_assets.py`, `check_portable_paths.py`, `check_private_paths.py`, `gen_host_catalog.py`, `gen_tool_reference.py` |
| UR5e checks (live, disposable) | `verify_ur5e_dt.sh` (behind `./verify.sh`), `smoke_tools.py`, `validate_ur5e_check.py`, `bridge_exec.py`, `bridge_async_check.py`, `checks/` |
| Scene checks (live, disposable) | `validate_scene_tools.py` first, then `validate_scene_equivalents.py`, `_edges`, `_sensors`, `_camera_freshness`, `_clear`, `_sessions`, `_reopen`; `validate_generic_scene.py`; `scene_mcp_client.py`, `smoke_mcp_surface.py`; fixtures from `make_scene_fixture.py`, `make_scene_package_fixture.py` |
| ROS 2 | `ros_env.sh` (source it), `ros2_rviz_config.py`, `ros2_tf_doctor.py`, `tf_capture.py`, `verify_tf.sh`, `cmd_vel_sequence.py` |
| UR driver | `launch_ur_fake.sh`, `sim_bringup.sh`, `ursim_watchdog.sh`, `ur_dashboard.py`. These can affect the surrounding robot environment, so use them only for the intended setup |
| Diagnostics | `probe_62.sh`, `catch_hang.sh` (both boot Kit on the GPU), `checkpoint_export.py` |
| Docs generators | `ecosystem_catalog.py`, `ecosystem_capabilities.py` |

`tools/` holds one-off asset conversion utilities for the RG2 gripper. `mcp/examples/` holds bridge integration templates.

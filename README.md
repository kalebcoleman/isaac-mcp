# isaac-mcp

An MCP server that lets an agent operate Isaac Sim through a local Kit bridge. It can also use an
optional Isaac Lab installation and capture an available desktop. The bundled installer pins
**Isaac Sim 6.2.0.0**; selected workflows on earlier 6.x builds have separate, limited test evidence.
See the [version and prerequisite matrix](docs/LINUX-SETUP.md#choose-a-simulator-build).

Built on Aldrin Inbaraj's [`omni-kit-mcp`](https://github.com/inbarajaldrin/omni-kit-mcp) and
[`isaac-sim-mcp`](https://github.com/inbarajaldrin/isaac-sim-mcp), with a screen-capture tool pack added.
The latter is MIT-licensed; upstream `omni-kit-mcp` publishes no license file. See
[`docs/VENDORING.md`](docs/VENDORING.md).

## Layout

| Path | What it is |
|---|---|
| `mcp/` | Host MCP server (`isaac-mcp`): stdio server, Kit bridge client, screen tools. Pure Python, no Isaac dependency. |
| `exts/isaac.mcp.bridge/` | Kit extension: the socket bridge and tool registry inside Kit. |
| `exts/isaac.mcp.panel/` | Kit extension: registry inspector UI. |
| `exts/ur5e-dt/` | Tool pack: the UR5e digital-twin tools (23 `ur5e_dt.*`) and their assets. One pack among others to come. |
| `mcp/isaac_mcp/isaaclab/` | Isaac Lab 3.0 tool pack: 12 host-side `isaaclab__*` tools (install info, task list, train/eval/play jobs, run status/tail/cancel, checkpoints, TensorBoard metrics). See [`docs/ISAACLAB-TOOLS.md`](docs/ISAACLAB-TOOLS.md). |
| `npm/` | Optional `npx` launcher (runs the Python server through `uvx`). |
| `scripts/`, `run.sh`, `verify.sh`, `install.sh` | Launch, check and install helpers (Ubuntu 22.04/24.04, Python 3.12). |

This standalone repository contains the **MCP server, bridge, tools and the `ur5e-dt` extension**.
It can be used independently by projects that need Isaac Sim or Isaac Lab tools.

## Quick start: bundled Isaac Sim 6.2

From this checkout on Ubuntu 22.04 or 24.04 x86_64, first install [uv](https://docs.astral.sh/uv/getting-started/installation/)
and check NVIDIA's [6.2 hardware and driver requirements](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/requirements.html).
The full installer needs a working NVIDIA GPU/driver, 40 GB free disk space, network access, and
acceptance of the NVIDIA Omniverse EULA.

```bash
scripts/check_machine.sh          # 6.2-specific preflight; see the note below
./install.sh --accept-eula        # install the pinned 6.2 Sim environment and host server
./run.sh                          # windowed Sim, bridge and tools; UR5e panel hidden
# Alternatively: ./run.sh --headless
# Optional:      ./run.sh --ur5e-panel
```

If its Sim interpreter already exists, `check_machine.sh` checks for **exactly 6.2.0.0** and runs a
small Torch CUDA kernel. It is not an independent version check for 6.0 or 6.1. It warns about a
missing display; headless Sim still requires a compatible GPU and driver.

Connect your agent's MCP client using the [client setup examples](docs/MCP-SERVER.md#4-client-configuration),
then call `refresh_tools`; if needed use `use_bridge` to select the intended port. Confirm the PID
with `status` when available and call `sim__stage__info` before changing the stage. Catalog-listed
Sim/UR5e tools can appear before any simulator is connected. For an optional **scene-changing**
6.2 UR5e workstation check, run `./verify.sh` only in an owned disposable session with its required
assets prepared; it rebuilds/reopens the scene and may write files.

For a host-only setup, use `./install.sh --no-sim`; this does not install Isaac Sim, Isaac Lab, or
system ROS. To use an existing 6.0 or 6.1 Sim environment, follow the separate
[version selection instructions](docs/LINUX-SETUP.md#choose-a-simulator-build); the full installer
always targets 6.2. The bundled NVIDIA Kit extensions, including HydraTexture, are not separate pip
installs. Isaac Lab and external ROS tools/drivers are optional for their respective workflows.

Saving a ROS camera scene can require an explicit export choice: `sim__stage__save` defaults
`reset_camera_render_handles=false` and refuses affected saves with the exact runtime properties
and retry option. Opting in removes only validated handles from the new copy and reports
`omitted_runtime_render_properties`; original and live files remain unchanged. See
[scene save limitations](docs/TOOLS-SCENE.md). Qualification of the integrated fix is still pending.

The launcher, verifier and 6.2 probe use the repository's `.venv` or the interpreter set by
`UR5E_DT_PYTHON`. They no longer search an implicit project-specific environment.

### install.sh options

`./install.sh [options]` is idempotent. Besides `--accept-eula` (acknowledge the NVIDIA Omniverse EULA, sets
`OMNI_KIT_ACCEPT_EULA=YES`):

- `--no-sim` — skip the ~20 GB Isaac Sim environment sync (host MCP server only)
- `--claude`, `--claude-code`, `--claude-desktop` — register the server (name `isaac`) in the Claude Code / Desktop configs
- `--print-opencode` — print an OpenCode config snippet
- `--port N` — bridge port written into the client configs (default 8766)
- `--isaaclab-python PATH`, `--isaaclab-root PATH` — Isaac Lab venv python and checkout; written into the client
  configs as `ISAACLAB_PYTHON` / `ISAACLAB_ROOT` so the `isaaclab__*` tools find the install (default: the same env
  vars, if already set)

The `isaaclab__*` tools discover an activated Isaac Lab environment or a checkout-local `.venv`;
`ISAACLAB_PYTHON` / `ISAACLAB_ROOT` select an explicit installation when needed
(see [`docs/ISAACLAB-TOOLS.md`](docs/ISAACLAB-TOOLS.md)).
For simulator connection, window inspection, and agent workflow, see
[`docs/MCP-SERVER.md`](docs/MCP-SERVER.md#agent-workflow).
Optional X11 mouse/keyboard input and managed parallel simulator launches are explicitly enabled
per MCP server; see [host tools](docs/TOOLS-HOST.md#optional-desktop-input) for setup and safeguards.

### Notes

- Ports: 8766 = shared bridge (`run.sh` default; `--port N` or `UR5E_DT_PORT` to change); 8767 = Isaac Lab bridge by
  convention (`mcp/examples/isaaclab_bridge_launch.py`).
- GPU: for training on a 16 GB card, begin with one GPU job and keep `num_envs` small; a running Kit app plus a training job
  contend for VRAM.
- `scripts/catch_hang.sh` loops boot + `quick_start_with_workstation` to hunt a rare first-call hang flake; on a
  hang it saves evidence under `/tmp/isaac-mcp-hang/`.

## Status

Work in progress. The [project status and roadmap](docs/ROADMAP.md) records completed work, tested configurations,
limitations and next steps. The current twin workflow is verified on Isaac Sim 6.2; broader version and platform
support is a goal, not yet a blanket compatibility claim. See the [compatibility matrix](docs/COMPATIBILITY.md)
for tested builds, installation pins, pending final qualification, and known limitations. Do not infer
support for a future 6.x release from its version number.

## Documentation

- [Agent guide](AGENTS.md) — which tools exist in which tier, and how to reach all of them.
- [Linux setup](docs/LINUX-SETUP.md) — OS, driver, Python, Isaac Lab and optional ROS requirements.
- [Compatibility](docs/COMPATIBILITY.md) — simulator and Lab versions, tested workflows, and limitations.
- [UR5e tools](docs/TOOLS-UR5E.md) — all twin tools, arguments and example calls.
- [Host and bridge tools](docs/TOOLS-HOST.md) — connection management, desktop capture and bridge built-ins.
- [Isaac Lab tools](docs/TOOLS-ISAACLAB.md) — task discovery, job management, checkpoints and metrics.
- [Server setup and agent workflow](docs/MCP-SERVER.md) and [architecture](docs/ARCHITECTURE.md).
- [Agent skill](docs/AGENT-SKILLS.md) — the `isaac-mcp` skill for Claude Code, Codex, OpenCode and Antigravity, and
  how it routes to NVIDIA's Isaac Lab and Isaac Sim skills.
- [Ecosystem](docs/ECOSYSTEM.md) — every tool other Isaac Sim / Isaac Lab MCP servers define, grouped by capability
  with isaac-mcp's coverage and what to build next ([by repository](docs/ECOSYSTEM-BY-REPO.md)).
- [Isaac Lab implementation notes](docs/ISAACLAB-TOOLS.md) and [generic Sim tools design](docs/GENERIC-SIM-TOOLS.md)
  (the latter is a proposal, not an implemented pack).
- [Vendoring](docs/VENDORING.md) and [publishing checklist](docs/PUBLISHING.md).
- [Verification guide](docs/VERIFICATION.md) — host checks, live UR5e baseline and their limits.

## Project USD and generic scene preview

The optional trusted project configuration and 39 offline-discoverable scene/asset tools are
documented in [TOOLS-SCENE.md](docs/TOOLS-SCENE.md). They add local USD/URDF discovery/import,
scene authoring, PhysX observations/stepping, RTX sensors and opt-in controllers.
Camera captures acquire fresh pixels by default. `wait_for_render=false` explicitly reuses
the last tool-acquired image (or returns `NOT_READY`); ordinary Play does not refresh it.
See the [43-tool fork comparison](docs/FORK-COMPARISON.md) and
[validation limits](docs/evidence/project-scene/README.md). This preview does not implement
untrusted OS isolation, authenticated remote upload or full fork parity. Bounded USDZ and released-6.x adapters are undergoing the separate compatibility gates.

Project USD imports require a user-approved local folder and a shared host/Kit staging location.
The tools copy the bounded project and check common USD, MDL and MaterialX dependencies, but
cannot guarantee that every custom resolver, plugin, procedural material or external resource is
detected or rendered correctly. Unsupported or missing dependencies are reported when detected;
network and absolute authored references, tiled/dynamic resources and some material packages are
not automatically collected. Generic imports require matching scene units and up-axis. Verify the
returned prim and rendered result. This trusted-asset workflow is not a sandbox; see
[project USD limitations and setup](docs/TOOLS-SCENE.md#project-usd-and-the-ur5e-workstation).
See the [Isaac Sim 6.x live import evidence](docs/evidence/project-usd-workstation/README.md).

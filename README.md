# isaac-mcp

**Give your AI agent eyes and tools inside NVIDIA Isaac Sim.** Inspect and build USD scenes,
work with robot joints and physics, capture sensors, and run repeatable simulation workflows
through the Model Context Protocol.

[![CI](docs/media/badges/ci.svg)](https://github.com/kalebcoleman/isaac-mcp/actions/workflows/ci.yml)
[![Source version 0.2.0](docs/media/badges/version.svg)](mcp/pyproject.toml)
[![Isaac Sim scoped tests](docs/media/badges/sim.svg)](docs/COMPATIBILITY.md)
[![Host Python 3.10+, Sim Python 3.12](docs/media/badges/python.svg)](docs/LINUX-SETUP.md#which-environment-does-what)
[![Setup guide](docs/media/badges/docs.svg)](docs/LINUX-SETUP.md)
[![License and asset notices](docs/media/badges/license.svg)](docs/VENDORING.md)

[Quick start](#quick-start) · [Setup guide](docs/LINUX-SETUP.md) ·
[Reproduce a run](docs/REPRODUCIBILITY.md) · [Compatibility](docs/COMPATIBILITY.md) · [Agent guide](AGENTS.md)

[![Actual Isaac Sim window showing the complete UR5e arm and attached RG2 gripper at a commanded target, with the stage hierarchy visible](docs/media/ur5e-target.png)](docs/media/README.md)

*Real MCP-driven target-and-return workflow: Isaac Sim **6.2.0.0**, windowed PhysX,
source **2fdf404**. Original application capture; [return view and provenance](docs/media/README.md), or
[42.9-second video — draft access only](https://github.com/kalebcoleman/isaac-mcp/releases/download/untagged-8a38d708b61458020a38/agent-62w-2fdf-full-ur-video.mp4).
This run used an existing installation with a documented NumPy dependency mismatch; it is not the clean locked setup.*

## What you can do

- **Work with your own scenes.** Inspect USD hierarchies, author primitives and materials,
  import trusted USD/URDF projects, inspect joints, step physics, and save composed scenes.
- **See the result.** Acquire fresh camera frames, inspect the actual application window,
  and record simulation workflows. Headless rendering and desktop capture are separate paths.
- **Operate the UR5e digital twin.** Build the workstation, use simulated ROS streams,
  inspect the arm and attached gripper, and exercise motion with measured feedback.
- **Run optional Isaac Lab jobs.** Discover an existing Lab installation, launch training,
  evaluation or play, and inspect metrics and checkpoints in its own environment.

There are **39 generic scene/asset tools, 23 UR5e tools and 12 Lab tools**. Default discovery
lists **83 tools offline**, **90 with bridge built-ins**, or **99 with both optional groups**.
Catalog entries remain visible offline; simulator operations need a live selected bridge.
See the [tool tiers and filters](AGENTS.md#what-you-can-reach-by-tier).

> **Validation status:** the installer pins **6.2.0.0**. Scoped results exist for **6.0.0.0,
> 6.0.0.1, 6.0.1.0, 6.1.0.0 and 6.2.0.0**, with source, mode and limitations recorded in the
> [matrix](docs/COMPATIBILITY.md). The original 6.0.1 windowed native-step stall remains unresolved.
> Final matrix review, affected older-build retests and generic Newton qualification remain open.
> This is a draft integration, not an all-version or clean-machine certification.

## Quick start

**New Linux installation:** Ubuntu 24.04 or 22.04 x86_64, a compatible NVIDIA GPU/driver,
[uv](https://docs.astral.sh/uv/getting-started/installation/), Git, internet access and at least
40 GB free. Read the [prerequisites and installation effects](docs/LINUX-SETUP.md#new-pinned-62-installation)
before running. Windowed use also needs a working display.

```bash
git clone --branch integration/final-review-11-15 https://github.com/kalebcoleman/isaac-mcp.git
cd isaac-mcp
# All commands below run from this checkout root.
./install.sh --accept-eula
export PATH="$HOME/.local/bin:$PATH"
uv pip check --python .venv/bin/python
./run.sh --port 8766
```

`--accept-eula` accepts NVIDIA's EULA. The installer creates/updates `.venv`, replaces the
user-level editable host tool, and may install an ffmpeg shim. It uses `uv sync`, **not
`uv sync --locked`**. For an exact-source locked installation, use the separate
[reproducible setup](docs/REPRODUCIBILITY.md#locked-simulator-setup). Neither path installs Isaac Lab or system ROS.

**Already have Sim?** Keep your environment and select its interpreter with `UR5E_DT_PYTHON`;
follow [existing installations and older builds](docs/LINUX-SETUP.md#choose-a-simulator-build).
Do not sync the 6.2 lock into an older Sim or Lab environment. `--headless` removes the window,
not the GPU requirement. First startup may spend minutes preparing shaders and extensions.

### Connect your agent

Register `isaac-mcp` as a **stdio** server using your client's
[configuration instructions](docs/MCP-SERVER.md#4-client-configuration).
For Claude Code, the installer can register it explicitly:

```bash
# Host-only operation; also replaces the user-level host tool.
./install.sh --no-sim --claude-code --port 8766
```

Other clients, including Codex and OpenCode, use the manual configurations in that guide.
Installation alone does not register a client. Reconnect the client after changing its configuration.

Start with this read-only request:

> Refresh the Isaac tools, select my intended bridge if more than one is available, and
> verify its PID, port and build with status. Call sim__stage__info and report the stage
> identity, units and readiness. Do not create, reset or play a scene yet.

The [terminal connection check](docs/LINUX-SETUP.md#connect-and-check-without-changing-the-scene)
provides the same first inspection without an agent. A successful tool listing alone does not prove Sim is running.

## Add only the workflows you need

| Workflow | Start here |
|---|---|
| External ROS mock driver or ROS clients | [System ROS versus the bundled Sim bridge](docs/LINUX-SETUP.md#optional-ur5e-mock-ros-driver) |
| Isaac Lab training, evaluation and video | [Lab discovery, versions and video dependencies](docs/TOOLS-ISAACLAB.md) |
| Mouse/keyboard input or managed Sim sessions | [Opt-in host tools](docs/TOOLS-HOST.md) |
| Your USD project or private workstation assets | [Trusted project roots and asset preparation](docs/TOOLS-SCENE.md#project-usd-and-the-ur5e-workstation) |
| Repeatable checks and issue reports | [Reproducibility](docs/REPRODUCIBILITY.md) · [Verification](docs/VERIFICATION.md) |

Trusted asset loading is not OS-sandboxed. Approve the project folder and cache scope first;
private Vention/NVIDIA assets are not supplied by cloning this repository. A generic scene
inspection does not require that workstation. Save restrictions, render-handle handling and
legacy checkpoint scope are documented in the [scene reference](docs/TOOLS-SCENE.md).

## Reference and development

[Host tools](docs/TOOLS-HOST.md) · [Scene and assets](docs/TOOLS-SCENE.md) ·
[UR5e tools](docs/TOOLS-UR5E.md) · [Lab tools](docs/TOOLS-ISAACLAB.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Agent skill](docs/AGENT-SKILLS.md) · [Roadmap](docs/ROADMAP.md)

The host lives in `mcp/`; Kit extensions live in `exts/`; setup and validation helpers live
in `scripts/`. See [CPU checks and contributing validation](docs/VERIFICATION.md#host-checks).
The badges link to source, setup and validation scope; they are not live pass indicators.
The CI link opens the CPU workflow, which does not establish GPU or native compatibility.

Built on Aldrin Inbaraj's [omni-kit-mcp](https://github.com/inbarajaldrin/omni-kit-mcp) and
[isaac-sim-mcp](https://github.com/inbarajaldrin/isaac-sim-mcp). The latter carries an MIT license;
the former has no upstream license file. Component and asset terms differ: read
[VENDORING](docs/VENDORING.md) and [asset notices](docs/provenance/ASSETS.md) before redistribution.
NVIDIA's installed runtime packages remain subject to their own terms.

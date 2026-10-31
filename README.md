# isaac-mcp

An MCP server that lets an LLM client drive **Isaac Sim 6.2** and **Isaac Lab 3.0**:
it talks to a running Kit app over a local bridge, and it can also see the desktop (screenshots and screen
recordings). Not tied to any one project.

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

This repo is the source of truth for the **MCP server, the bridge, the tools and the `ur5e-dt`
extension** — nothing else. The experiments that drive the twin (full-system demo, control
experiments, MPC, resolved-rate, test plan, evidence log and published media) live in the workspace
repo, `ur5e-digital-twin`, under `demo/`, and pin this repo as `external/isaac-mcp`. Change this repo
when you add or change a tool (or its docs); change that one when you run or analyse an experiment.

## Quick start

```bash
./install.sh --accept-eula        # uv env with Isaac Sim 6.2.0.0 + the isaac-mcp CLI
scripts/check_machine.sh          # OS, GPU/driver, disk, network, display, port checks (must pass)
./run.sh                          # Kit with the twin bridge on port 8766 (--headless for no window)
./verify.sh                       # tool list, scene build, physics checks
```

Host server only (no Isaac Sim): `./install.sh --no-sim`, or `uv tool install "./mcp"`.

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

The `isaaclab__*` tools locate their Isaac Lab install via `ISAACLAB_PYTHON` / `ISAACLAB_ROOT`; without them they
only search the default paths (see [`docs/ISAACLAB-TOOLS.md`](docs/ISAACLAB-TOOLS.md)).
For simulator connection, window inspection, and agent workflow, see
[`docs/MCP-SERVER.md`](docs/MCP-SERVER.md#agent-workflow).

### Notes

- Ports: 8766 = twin bridge (`run.sh` default; `--port N` or `UR5E_DT_PORT` to change); 8767 = Isaac Lab bridge by
  convention (`mcp/examples/isaaclab_bridge_launch.py`).
- GPU: on a 16 GB card run one GPU job at a time and keep `num_envs` small; a running Kit app plus a training job
  contend for VRAM.
- `scripts/catch_hang.sh` loops boot + `quick_start_with_workstation` to hunt a rare first-call hang flake; on a
  hang it saves evidence under `/tmp/isaac-mcp-hang/`.

## Status

Work in progress. The [project status and roadmap](docs/ROADMAP.md) records completed work, tested configurations,
limitations and next steps. The current twin workflow is verified on Isaac Sim 6.2; broader version and platform
support is a goal, not yet a blanket compatibility claim.

## Documentation

- [UR5e tools](docs/TOOLS-UR5E.md) — all twin tools, arguments and example calls.
- [Host and bridge tools](docs/TOOLS-HOST.md) — connection management, desktop capture and bridge built-ins.
- [Isaac Lab tools](docs/TOOLS-ISAACLAB.md) — task discovery, job management, checkpoints and metrics.
- [Server setup and agent workflow](docs/MCP-SERVER.md) and [architecture](docs/ARCHITECTURE.md).
- [Isaac Lab implementation notes](docs/ISAACLAB-TOOLS.md) and [generic Sim tools design](docs/GENERIC-SIM-TOOLS.md)
  (the latter is a proposal, not an implemented pack).
- [Handoff and dated verification](docs/HANDOFF.md), [vendoring](docs/VENDORING.md), and
  [publishing checklist](docs/PUBLISHING.md).
- [Verification guide](docs/VERIFICATION.md) — host checks, live UR5e baseline and their limits.

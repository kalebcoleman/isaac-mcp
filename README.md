# isaac-mcp

An MCP server that lets an LLM client drive **Isaac Sim 6.2** and **Isaac Lab 3.0**:
it talks to a running Kit app over a local bridge, and it can also see the desktop (screenshots and screen
recordings). Not tied to any one project.

Built on Aldrin Inbaraj's [`omni-kit-mcp`](https://github.com/inbarajaldrin/omni-kit-mcp) and
[`isaac-sim-mcp`](https://github.com/inbarajaldrin/isaac-sim-mcp) (MIT), with a screen-capture tool pack
added. Upstream `omni-kit-mcp` publishes no license file; see [`docs/VENDORING.md`](docs/VENDORING.md).

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

### Notes

- Ports: 8766 = twin bridge (`run.sh` default; `--port N` or `UR5E_DT_PORT` to change); 8767 = Isaac Lab bridge by
  convention (`mcp/examples/isaaclab_bridge_launch.py`).
- GPU: on a 16 GB card run one GPU job at a time and keep `num_envs` small; a running Kit app plus a training job
  contend for VRAM.
- `scripts/catch_hang.sh` loops boot + `quick_start_with_workstation` to hunt a rare first-call hang flake; on a
  hang it saves evidence under `/tmp/isaac-mcp-hang/`.

## Status

Work in progress; goals in [`docs/ROADMAP.md`](docs/ROADMAP.md).

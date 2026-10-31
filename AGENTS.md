# Agent guide

Start here if you are an AI agent working in or with this repository. It is a map; the
per-tool reference lives in `docs/`.

## What you can reach, by tier

An MCP client only shows tools that exist right now. Most of this project's surface lives in
a running Kit process, so what you see depends on whether a bridge is connected.

<!-- tool-counts: host=9 isaaclab=12 builtins=7 ur5e=23 -->

| Tier | Tools | Present when | Reference |
|---|---|---|---|
| Host gateway and desktop | 9 (`refresh_tools`, `use_bridge`, and 7 screen tools) | always, even with Kit down | [TOOLS-HOST.md](docs/TOOLS-HOST.md) |
| Isaac Lab (`isaaclab__*`) | 12 | always; job tools also need `ISAACLAB_PYTHON` / `ISAACLAB_ROOT` | [TOOLS-ISAACLAB.md](docs/TOOLS-ISAACLAB.md) |
| Bridge built-ins (`run_python`, `status`, `list_bridges`, `reload_tools`, file tools) | 7 | a Kit bridge is reachable; hidden by `ISAAC_MCP_BUILTINS=0` | [TOOLS-HOST.md](docs/TOOLS-HOST.md) |
| UR5e twin (`ur5e_dt__*`) | 23 | the twin extension is loaded in that Kit and you have called `refresh_tools` | [TOOLS-UR5E.md](docs/TOOLS-UR5E.md) |

So a client that lists 21 tools has no live bridge: those are the host and Isaac Lab tiers.
With everything up there are 51. Do not conclude that the other 30 do not exist; connect to
them (below). `docs/GENERIC-SIM-TOOLS.md` describes a `sim.*` pack that is a design only and
is not built, so do not look for those tools.

## Getting the full surface

1. Confirm a Kit process with the `isaac.mcp.bridge` extension is running (for the twin,
   `./run.sh --headless` starts it on port 8766).
2. Call `refresh_tools`, then `status` to confirm the PID and port are the instance you mean.
   With several bridges, choose one with `use_bridge`.
3. If the client does not show newly discovered tools, use the CLI against the verified port:
   `python -m isaac_mcp.client list_tools --port 8766`.

Setup details and the agent workflow are in [docs/MCP-SERVER.md](docs/MCP-SERVER.md).

## Handle with care

- `run_python` executes Python inside Kit, and `put_file` / `get_file` / `stat_file` touch the
  filesystem. Verify the target with `status` before scene-changing calls.
- `isaaclab__train`, `eval` and `play` start GPU jobs; `isaaclab__runs_cancel` kills one.
- Screen tools return window titles and pixels to the model, and save captures under
  `~/Pictures/screen-mcp` and `~/Videos/screen-mcp`.
- To expose less to a client, use `ISAAC_MCP_NAMESPACE` and `ISAAC_MCP_BUILTINS=0`. They filter
  bridge tools only; the host and Isaac Lab tiers are always registered.

## Changing tools

Every registered tool must be documented in its `docs/TOOLS-*.md` page, and the counts above
must match the code. Both are enforced by `python3 scripts/check_docs.py`, which also runs in
CI along with the host test suite (`pytest -q` from `mcp/`, see `.github/workflows/ci.yml`).

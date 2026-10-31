# Agent guide

Start here if you are an AI agent working in or with this repository. It is a map; the
per-tool reference lives in `docs/`.

## What you can reach, by tier

An MCP client only shows the tools it has been told about. Most of this project's surface
lives in a running Kit process and reaches the client through the host server's bridge
discovery, so what you see depends on whether that discovery has happened and on the
configured filters.

| Tier | Tools | Present when | Reference |
|---|---|---|---|
| Host gateway and desktop | 9 (`refresh_tools`, `use_bridge`, and 7 screen tools) | always, even with Kit down | [TOOLS-HOST.md](docs/TOOLS-HOST.md) |
| Isaac Lab (`isaaclab__*`) | 12 | always, no bridge needed; job tools also need `ISAACLAB_PYTHON` / `ISAACLAB_ROOT`, and refuse to start next to a running Kit bridge | [TOOLS-ISAACLAB.md](docs/TOOLS-ISAACLAB.md) |
| Bridge built-ins (`run_python`, `status`, `list_bridges`, `reload_tools`, file tools) | 7 | a Kit bridge is reachable; hidden by `ISAAC_MCP_BUILTINS=0` | [TOOLS-HOST.md](docs/TOOLS-HOST.md) |
| UR5e twin (`ur5e_dt__*`) | 23 | the twin extension is loaded in that Kit and you have called `refresh_tools` | [TOOLS-UR5E.md](docs/TOOLS-UR5E.md) |

If your client lists exactly 21 tools, only the host and Isaac Lab tiers are visible. That does
not prove that no bridge exists. The possible causes are:

- no Kit bridge is running;
- the client connected before Kit started, so the bridge tools have not been discovered yet
  (call `refresh_tools`);
- a filter is hiding bridge tools from a reachable bridge (`ISAAC_MCP_NAMESPACE` or
  `ISAAC_MCP_BUILTINS=0`).

The other 30 tools (bridge built-ins and UR5e twin) exist only behind a bridge. With everything
visible there are 51 tools in total. `docs/GENERIC-SIM-TOOLS.md` describes a `sim.*` pack that is
a design only and is not built, so do not look for those tools.

## Getting the full surface

Simulator and twin work (the bridge built-ins and `ur5e_dt__*`) needs a running Kit bridge, so
make sure one is up before relying on those tools. Host and Isaac Lab tools need no bridge.

1. Call `refresh_tools` first. Do not start Kit yet: a reachable bridge may already exist.
2. Verify with `status` (PID and port). `status` and `list_bridges` are themselves bridge
   built-ins, so they are missing if `ISAAC_MCP_BUILTINS=0` or `ISAAC_MCP_NAMESPACE` hides them.
   The CLI check works regardless: `python -m isaac_mcp.client status --port 8766`. With several
   bridges, choose one with `use_bridge`.
3. Only if no bridge is reachable, and project rules permit it, start one with the
   `isaac.mcp.bridge` extension (for the twin, `./run.sh --headless` starts it on port 8766),
   then repeat steps 1 and 2.
4. If the client does not show the newly registered tools, see the client note below.

Do not start another Kit instance while a reachable bridge exists, and confirm the PID and port
before any scene-changing call.

**Isaac Lab training is the exception to "keep a bridge up".** `isaaclab__train`, `eval` and
`play` refuse to start while a Kit bridge app is running, because it shares the GPU with the job
(the Kit-bridge guard, see [TOOLS-ISAACLAB.md](docs/TOOLS-ISAACLAB.md)). Stop the bridge first,
or knowingly pass `allow_concurrent_gpu=true`; the docs advise one GPU job at a time on a 16 GB
card.

<!-- client-note: delete this block if the host server starts advertising tools.listChanged -->
**Client note (current behaviour).** The host server advertises `tools.listChanged=false` and
never sends a list-changed notification, so after `refresh_tools` or `use_bridge` registers new
tools, a client may not show them until its MCP connection restarts (seen with Claude Code
2.1.314 against a test server). Restart the MCP connection, or list the tools directly:
`python -m isaac_mcp.client list_tools --port 8766` (use the port you verified in step 2).
<!-- /client-note -->

Setup details and the agent workflow are in [docs/MCP-SERVER.md](docs/MCP-SERVER.md).

## Handle with care

- `run_python` executes Python inside Kit, and `put_file` / `get_file` / `stat_file` touch the
  filesystem. Verify the target PID and port (`status`, or the CLI check above) before
  scene-changing calls.
- `isaaclab__train`, `eval` and `play` start GPU jobs and are blocked by a running Kit bridge
  unless `allow_concurrent_gpu=true`; `isaaclab__runs_cancel` kills one.
- Screen tools return window titles and pixels to the model, and save captures under
  `~/Pictures/screen-mcp` and `~/Videos/screen-mcp`.
- To expose less to a client, use `ISAAC_MCP_NAMESPACE` and `ISAAC_MCP_BUILTINS=0`. They filter
  bridge tools only; the host and Isaac Lab tiers are always registered.

## Changing tools

Every registered tool must be documented in its `docs/TOOLS-*.md` page, and the counts above
(each tier's Tools cell, the 21 and 30 subtotals and the 51 total) must match the code. Both are enforced by `python3 scripts/check_docs.py`, which also runs in
CI along with the host test suite (`pytest -q` from `mcp/`, see `.github/workflows/ci.yml`).

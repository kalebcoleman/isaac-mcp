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
2. Discover bridges, then verify the one you mean. Do not assume a port: advertised bridges can
   use other ports or hosts. `python -m isaac_mcp.client --list-bridges` prints every live
   advertised bridge as JSON (`pid`, `host`, `port`, `app`). It works even when `status` and
   `list_bridges` are hidden (they are bridge built-ins, so `ISAAC_MCP_BUILTINS=0` or
   `ISAAC_MCP_NAMESPACE` can remove them), and it exits 2 with `[]` when none is advertised.
   Probe the bridge you chose with the values from that listing:
   `python -m isaac_mcp.client status --host <host> --port <port>` (PID, port, app version).
   Without `--port` the CLI discovers a lone bridge but refuses when several are running, and an
   exported `UR5E_DT_PORT` or `ISAAC_MCP_PORT` overrides discovery. The host's `refresh_tools`
   resolves the endpoint the same way, so with several bridges or a pinned port, select the
   right one with `use_bridge` and the listed port.
3. Only if `--list-bridges` shows none, and project rules permit it, start one with the
   `isaac.mcp.bridge` extension (for the twin, `./run.sh --headless` starts it on port 8766 by
   default; `UR5E_DT_PORT` or `--port` changes that), then repeat steps 1 and 2. A running Kit
   process that is not in the listing has no bridge.
4. `refresh_tools` and `use_bridge` send `notifications/tools/list_changed` when they change the
   tool list (the host advertises `tools.listChanged`), so a client that honours it shows the new
   tools without a restart. If yours does not, restart the MCP connection or list the tools
   directly: `python -m isaac_mcp.client list_tools --host <host> --port <port>` (the bridge you
   verified in step 2).

Do not start another Kit instance while a reachable bridge exists, and confirm the PID and port
before any scene-changing call.

**Isaac Lab training is the exception to "keep a bridge up".** `isaaclab__train`, `eval` and
`play` refuse to start while a Kit bridge app is running, because it shares the GPU with the job
(the Kit-bridge guard, see [TOOLS-ISAACLAB.md](docs/TOOLS-ISAACLAB.md)). Stop the bridge first,
or knowingly pass `allow_concurrent_gpu=true`; the docs advise one GPU job at a time on a 16 GB
card.

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

# Agent guide

Start here if you are an AI agent working in or with this repository. It is a map; the
per-tool reference lives in `docs/` ([index](docs/README.md), [every tool's arguments](docs/TOOLS-ARGUMENTS.md)).

## Setup guide for agents

- **Use the Isaac Sim that is already installed. Never download or install Isaac Sim unless the
  user explicitly asks for it.** Lab and shared PCs have their own install: find it with
  `python3 scripts/isaac_env.py find` and install only the MCP server with `./install.sh`
  (it uses an installed Isaac Sim 6.x and skips the download; `--no-sim` guarantees that, and still records the install it finds). Do not run
  `./install.sh --download-sim`, `uv sync`, `pip install isaacsim`, or `uv run`/`uv lock`/`uv add` in
  the repository root (the root project depends on Isaac Sim, so they create a ~20 GB `.venv`) without
  the user asking; run tools with `python3 scripts/...` or `cd mcp && uv run --isolated ...`, and
  do not replace or upgrade their Isaac Sim or Isaac Lab environment. If `find` shows nothing,
  stop and ask where Isaac Sim is installed (`ISAACSIM_PATH` or `ISAAC_MCP_PYTHON`) instead of
  installing one.
- **PhysX is the release target. Newton is experimental.** Use PhysX for release validation;
  do not continue Newton qualification or workaround development unless explicitly requested.
  For a requested Newton workflow, inspect the [backend scope](docs/COMPATIBILITY.md#release-backend-scope)
  and exact source/build/mode evidence first. Specific 6.2 successes are not general support;
  earlier native crashes, invalid state and contact/render failures remain limitations.
  Verify the selected engine; do not silently switch an existing scene or fall back to another
  backend. A CPU device does not identify the solver, and Lab results do not qualify the twin.
- Identify the requested Sim build and workflow from the context or installation; reuse their intended
  running bridge when available.
  The repository's full `./install.sh --accept-eula` creates the locked **6.2.0.0** environment;
  it is not a selector for older builds. For an existing compatible 6.0/6.1 interpreter, use
  [build selection and checks](docs/LINUX-SETUP.md#choose-a-simulator-build). Follow the user's
  authorization for starting GPU work and avoid replacing an existing environment.
- Read [Linux prerequisites](docs/LINUX-SETUP.md) and the [compatibility matrix](docs/COMPATIBILITY.md)
  before recommending packages. The host, Sim, external system ROS, and Isaac Lab can use different
  environments. Install external ROS or Isaac Lab only when the requested workflow needs them.
  The supplied launcher enables NVIDIA's bundled ROS bridge; that does not install system ROS.
- NVIDIA supplies the `omni.kit.hydra_texture` extension with the inspected Sim installations.
  `omni.hydratexture` `.pyi` files describe its API; do not tell users to pip-install stubs or `omni`
  as a separate fix. Kit modules normally become available inside the running app.
- Client setup is in [MCP-SERVER.md](docs/MCP-SERVER.md#4-client-configuration). After connection,
  use `refresh_tools`, select the intended bridge if needed, verify PID/port, and start scene work
  with `sim__stage__info`. Offline tool listings do not prove a running or compatible simulator.
  `scripts/check_machine.sh` is a **6.2-specific** preflight and can run a real CUDA kernel; use the
  version-specific checks for earlier builds. `./verify.sh` changes the UR5e scene and is 6.2-specific.

When saving a scene with native ROS camera or LiDAR render handles, `sim__stage__save` defaults to
`reset_camera_render_handles=false`. An affected save refuses with `SAVE_UNSUPPORTED`, exact
`properties` and `retry_with`. After inspecting that response, explicitly retry with the flag set
to `true` only if normalization of those runtime fields in the exported copy is intended. The
result reports `omitted_runtime_render_properties`; cameras and source/live layers remain intact.
The flag does not bypass ambiguous, dependent or unavailable-binding refusals, or an authored camera handle,
and does not prove property authorship. A LiDAR handle saved in the file is dropped from the copy, and saves omit
Replicator's runtime pipeline, so saving with the flag and opening the copy is the rescue for a stale LiDAR file
([saved LiDAR scenes](docs/TOOLS-SCENE.md#saved-lidar-scenes-and-scan)). This tool's own `IsaacMCP_*` capture products need no flag.
On builds where it was measured (6.2.0.0 and 5.1.0.0), `sim__stage__open` deactivates stale saved LiDAR products in the session layer, so `/scan` returns. On other builds it reports them and its `rescue` field gives the fix; see
[why a saved LiDAR lost its topic](docs/TOOLS-SCENE.md#saved-lidar-scenes-and-scan). Pause before saving and verify the
new file in a fresh process. See [save limitations](docs/TOOLS-SCENE.md).

When a physics read or step returns `PHYSICS_STATE_INVALID`, inspect its backend, path and
field: this is invalid observed runtime data, not a request to rewrite the caller's pose.
A step error can include partial execution and before/after clock diagnostics; the after
clock may be unavailable. Do not count elapsed simulation time as valid physics or blindly
retry the step. No rollback or automatic retry is provided. Inspect solver diagnostics and
the [step error contract](docs/TOOLS-SCENE.md) before continuing; `DEVICE_ERROR` quarantine
still applies to GPU faults. Successful error reporting does not establish solver recovery.

## What you can reach, by tier

An MCP client only shows the tools it has been told about. Most of this project's surface
lives in a running Kit process and reaches the client through the host server's bridge
discovery, so what you see depends on whether that discovery has happened and on the
configured filters.

| Tier | Tools | Present when | Reference |
|---|---|---|---|
| Host gateway and desktop | 12 (`refresh_tools`, `use_bridge`, and 10 screen tools) | by default (`ISAAC_MCP_LOCAL_TOOLS` unset or `all`), even with Kit down | [TOOLS-HOST.md](docs/TOOLS-HOST.md) |
| Desktop input (optional) | 5 (`window_*`) | only when `screen_input` is explicitly selected | [TOOLS-HOST.md](docs/TOOLS-HOST.md#optional-desktop-input) |
| Simulator sessions (optional) | 4 (`sim__*`) | only when `sim_launch` is explicitly selected | [TOOLS-HOST.md](docs/TOOLS-HOST.md#optional-simulator-sessions) |
| Isaac Lab (`isaaclab__*`) | 12 | by default (`ISAAC_MCP_LOCAL_TOOLS` unset or `all`), no bridge needed; job tools also need `ISAACLAB_PYTHON` / `ISAACLAB_ROOT`, and refuse to start next to a running Kit bridge | [TOOLS-ISAACLAB.md](docs/TOOLS-ISAACLAB.md) |
| Bridge built-ins (`run_python`, `status`, `list_bridges`, `reload_tools`, file tools) | 7 | listed from a packaged catalog even with Kit down; execution needs a reachable bridge; hidden by `ISAAC_MCP_BUILTINS=0` | [TOOLS-HOST.md](docs/TOOLS-HOST.md) |
| Generic scene and assets | 55 (`sim__quick_start`, `assets__*`, `sim__stage__*`, `sim__prim__*`, physics, timeline, body, viewport, ROS 2 TF, OmniGraph, maps and Kit UI) | on by default, catalogued offline; hide with `ISAAC_MCP_SCENE_TOOLS=0`; roots, scan, search and the pack tools run on host, others need sim-tools in selected Kit | [TOOLS-SCENE.md](docs/TOOLS-SCENE.md) |
| UR5e twin (`ur5e_dt__*`) | 23 | RISE lab's UR5e digital twin, Kaleb's work with that robot; every other tier is robot-agnostic. Listed from a packaged catalog even with Kit down; execution needs a selected Kit bridge with the twin extension | [TOOLS-UR5E.md](docs/TOOLS-UR5E.md) |

With default groups and no namespace filter, a client lists exactly 109 tools from startup:
the host, Isaac Lab, bridge built-in, generic and UR5e catalog tiers, whether or not Kit is running.
A listed tool does not prove that a bridge exists: catalogued tools dial the selected bridge when
called and return an actionable error if none answers. Use `status` (or the CLI check below) to
find out. Fewer tools mean a filter:

- `ISAAC_MCP_BUILTINS=0` hides the bridge built-ins, `ISAAC_MCP_NAMESPACE` hides namespaced
  bridge and catalog tools, and `ISAAC_MCP_SCENE_TOOLS=0` hides the generic pack;
- `ISAAC_MCP_LOCAL_TOOLS` is restricting the local tools (the default, `all`, registers 24 local tools),
  so the listed host and Isaac Lab tools may be a subset. If its `gateway` group is excluded,
  `refresh_tools` and `use_bridge` are not listed: bridge tools are then discovered only when
  the server starts, so restart the MCP connection after starting Kit.

The other 7 tools (bridge built-ins) are listed from the same kind of snapshot as the twin and
scene tools, so clients that ignore `tools/list_changed` (OpenCode) can call them at once. With
the standard groups and bridge built-ins visible there are 109 tools in total. The optional desktop input and
simulator session groups add 9 more. The initial generic tools are documented in `docs/TOOLS-SCENE.md`; the broader
`docs/GENERIC-SIM-TOOLS.md` design includes capabilities not yet implemented. Untrusted
loading and remote upload remain disabled; bounded trusted USDZ packages are supported.

Set `ISAAC_MCP_SCENE_TOOLS=0` in the MCP server environment to hide the entire generic
scene/asset pack, both offline and after bridge refresh. It is read once per server startup.
With otherwise standard settings this leaves 54 tools, offline or with a bridge.
The 23 UR5e tools, Isaac Lab, desktop tools and optional managed `sim__launch/list/status/stop`
tools retain their existing rules. This discovery preference is not a security boundary.

`ur5e_dt__quick_start*` leaves the timeline playing, and generic edits refuse with `TIMELINE_ACTIVE` until an
explicit `sim__timeline__control` stop. Joint tools take the articulation root (for the twin, `/World/UR5e/root_joint`,
reported by `sim__ros2__tf_setup`), not the robot's parent Xform. See the [scene workflow](docs/TOOLS-SCENE.md#a-typical-session).

After reopening a scene, its sensors and prims are not pack-owned: pass `adopt=true` to `sim__lidar__read` or `sim__camera__capture`, and
`allow_foreign=true` to `sim__prim__delete`, which deactivates rather than removes. See the [scene reference](docs/TOOLS-SCENE.md#reopened-scenes).

Camera capture defaults to a fresh bounded renderer CPU readback. With
`wait_for_render=false`, it writes the last validated tool-acquired image and reports its
age; ordinary Play does not populate this cache. An unseeded camera returns `NOT_READY`.
Cached pixels can predate Stop/reset or scene edits. See [TOOLS-SCENE.md](docs/TOOLS-SCENE.md).

## Getting the full surface

Simulator and twin work (the bridge built-ins and `ur5e_dt__*`) needs a running Kit bridge, so
make sure one is up before relying on those tools. Host and Isaac Lab tools need no bridge.

1. Call `refresh_tools` first. Do not start Kit yet: a reachable bridge may already exist.
2. Discover bridges, then verify the one you mean. The CLI below is standard-library only and runs from
   the checkout root with any Python. From another directory, use `"$(uv tool dir)/isaac-mcp/bin/python" -m isaac_mcp.client`. Do not assume a port: advertised bridges can
   use other ports or hosts. `python3 mcp/isaac_mcp/client.py --list-bridges` prints every live
   advertised bridge as JSON (`pid`, `host`, `port`, `app`). It works even when `status` and
   `list_bridges` are hidden (they are bridge built-ins, so `ISAAC_MCP_BUILTINS=0` or
   `ISAAC_MCP_NAMESPACE` can remove them), and it exits 2 with `[]` when none is advertised.
   Probe the bridge you chose with the values from that listing:
   `python3 mcp/isaac_mcp/client.py status --host <host> --port <port>` (PID, port, app version).
   Without `--port` the CLI discovers a lone bridge but refuses when several are running, and an
   exported `UR5E_DT_PORT` or `ISAAC_MCP_PORT` overrides discovery. The host's `refresh_tools`
   resolves the endpoint the same way, so with several bridges or a pinned port, select the
   right one with `use_bridge` and the listed port.
3. If none is advertised, start one with the `isaac.mcp.bridge` extension (the supplied
   `./run.sh --headless` starts the bridge and twin tools on port 8766 by default), then repeat steps 1 and 2. A running Kit
   process that is not in the listing has no bridge. To start an additional isolated instance,
   use the opt-in `sim__launch` workflow and its resource/identity checks; don't assume that an
   advertised bridge belongs to the new process. Managed and plain launches enable the twin tools
   while hiding its panel by default. Pass `show_ur5e_panel=true` or `--ur5e-panel` to show it.
4. `refresh_tools` and `use_bridge` send `notifications/tools/list_changed` when they change the
   tool list (the host advertises `tools.listChanged`), so a client that honours it shows the new
   tools without a restart. If yours does not, restart the MCP connection or list the tools
   directly: `python3 mcp/isaac_mcp/client.py list_tools --host <host> --port <port>` (the bridge you
   verified in step 2).

Parallel instances are supported when explicitly requested and resource checks pass. Use distinct
ports, ROS domains and output directories, and confirm the selected PID and port before every
scene-changing workflow. Resource checks are snapshots; they cannot guarantee an instance will fit.

**Isaac Lab training is the exception to "keep a bridge up".** `isaaclab__train`, `eval` and
`play` refuse to start while a Kit bridge app is running, because it shares the GPU with the job
(the Kit-bridge guard, see [TOOLS-ISAACLAB.md](docs/TOOLS-ISAACLAB.md)). Stop the bridge first,
or knowingly pass `allow_concurrent_gpu=true`; the docs advise one GPU job at a time on a 16 GB
card.

Setup details and the agent workflow are in [docs/MCP-SERVER.md](docs/MCP-SERVER.md).

The `isaac-mcp` Agent Skill (`.agents/skills/isaac-mcp/`, linked from `.claude/skills/`) turns
these rules into a step-by-step workflow and routes Isaac Lab and Isaac Sim domain questions to
NVIDIA's official skills; Claude Code, Codex and OpenCode load it automatically in this checkout.
See [docs/AGENT-SKILLS.md](docs/AGENT-SKILLS.md).

## Handle with care

- Project USD tools require an explicitly user-approved trusted root and shared host/Kit staging;
  copying includes the entire bounded project. Native USD parsers and resolvers are not sandboxed
  or network-isolated. Common layers, MDL resources and MaterialX references are checked, but
  arbitrary plugins, custom resolvers and procedural materials cannot be verified exhaustively.
  Known missing or unsupported resources are errors; a successful import is not proof of visual
  fidelity or physics readiness. Check units/up-axis and inspect the resulting prim and viewport.
  Workstation preparation does not load the scene or replace an existing cached asset; see
  [project USD setup and limitations](docs/TOOLS-SCENE.md#project-usd-and-the-ur5e-workstation).
- `run_python` executes Python inside Kit, and `put_file` / `get_file` / `stat_file` touch the
  filesystem. Verify the target PID and port (`status`, or the CLI check above) before
  scene-changing calls.
- `isaaclab__train`, `eval` and `play` start GPU jobs and are blocked by a running Kit bridge
  unless `allow_concurrent_gpu=true`; `isaaclab__runs_cancel` kills one.
- Screen tools return window titles and pixels to the model, and save captures under
  `~/Pictures/screen-mcp` and `~/Videos/screen-mcp`.
- Desktop input is off unless `screen_input` is explicitly selected. It operates a shared X11
  desktop and can trigger arbitrary application actions. Inspect the actual window, its PID and
  screenshot first; its target checks are not an operating-system security boundary.
- `sim_launch` is off by default. Its launch guard counts known bridge, managed Sim and configured
  Lab job processes and checks headroom, but cannot detect every untracked workload. Never stop an
  unrelated process to make room, and use separate desktops or coordinate input between agents.
- To expose less to a client, use `ISAAC_MCP_NAMESPACE` and `ISAAC_MCP_BUILTINS=0` (they filter
  bridge tools only) and `ISAAC_MCP_LOCAL_TOOLS` (it selects groups of the host and Isaac Lab
  tools only; the standard groups are registered by default). `ISAAC_MCP_LOCAL_TOOLS=readonly` leaves only the
  local tools that read state, but it does not touch bridge tools: with a bridge connected,
  `run_python` and `ur5e_dt__*` can still change the scene, so it is not a read-only server.
  The three variables compose; see [TOOLS-HOST.md](docs/TOOLS-HOST.md) and, for what a
  scene-safe profile needs, [MCP-SERVER.md](docs/MCP-SERVER.md#limiting-what-a-client-can-reach).
- Every local tool carries MCP annotations (`readOnlyHint`, `destructiveHint`, ...). The job
  inspection and Python probe tools can have side effects and are not in `readonly`;
  `screenshot` writes files and returns desktop pixels. Annotations are hints, not enforcement.
  Generic scene/asset tools, bridge built-ins and the UR5e twin carry host annotations. Tools a project's own extension registers carry none.

## Changing tools

Every registered tool must be documented in its `docs/TOOLS-*.md` page, and the counts above
(each tier's Tools cell, the totals and the built-ins count) must match the code. Both are enforced by `python3 scripts/check_docs.py`, which also runs in
CI along with the host test suite (`pytest -q` from `mcp/`, see `.github/workflows/ci.yml`).
After changing a tool registry or contract, run `python3 scripts/gen_host_catalog.py --write`, then
`python3 scripts/gen_tool_reference.py --write`, and commit both results; `check_docs.py` and the host tests detect drift.

Evidence must use neutral `<repo>`, `<worktree>`, `<home>` and `<sim-install>` placeholders.
`check_docs.py` also scans tracked bytes and nested gzip/tar/zip members for private home and
worktree paths and a former workstation's login, hostname and domain; it fails closed on unreadable/oversized archives. All tracked files, including
assets and evidence, are scanned without legacy hash exemptions.
`python3 scripts/check_private_paths.py --commits <range>` also rejects commits whose author or committer email is not a real address.
Review screenshot pixels separately; the byte scanner cannot recognize text inside images.
Keep large redacted transcripts and reproduction bundles in the draft evidence release, with
URL and SHA-256 in the evidence index. Keep only compact summaries and a few images <=400 KB in Git.

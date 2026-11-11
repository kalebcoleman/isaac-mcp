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
  the user asking; run tools with `python3 scripts/...` or `cd mcp && uv run --locked --isolated ...`, and
  refresh the host lock only with `uv lock --directory mcp`. Do not replace or upgrade their Isaac Sim
  or Isaac Lab environment. If `find` shows nothing,
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
  call `status` once, before the first simulator action: one reply names the selected bridge (PID,
  port), every advertised bridge, what is running, and the stage's `session_id` and `stage_id`; with
  nothing running it says so and names the next call. Do not repeat it before each call or after a
  context reset: a scene tool that meets a changed stage answers `STALE_TARGET` with the new IDs. Offline tool listings do not prove a running or compatible simulator.
  `scripts/check_machine.sh` is a **6.2-specific** preflight and can run a real CUDA kernel; use the
  version-specific checks for earlier builds. `./verify.sh` changes the UR5e scene and is 6.2-specific.

Native ROS camera/LiDAR render handles can make `sim__stage__save` refuse with
`SAVE_UNSUPPORTED`. Inspect its `properties` and `retry_with`; retry with
`reset_camera_render_handles=true` only when normalizing the exported copy is intended.
Other refusal checks still apply. Pause before saving and verify the copy in a fresh process.
Stale LiDAR products and their build-specific reopen behavior are in the
[scene reference](docs/TOOLS-SCENE.md#saved-lidar-scenes-and-scan).

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
| Host gateway and desktop | 13 (`isaac_help`, `refresh_tools`, `use_bridge`, and 10 screen tools) | by default (`ISAAC_MCP_LOCAL_TOOLS` unset or `all`), even with Kit down | [TOOLS-HOST.md](docs/TOOLS-HOST.md) |
| Desktop input (optional) | 5 (`window_*`) | only when `screen_input` is explicitly selected | [TOOLS-HOST.md](docs/TOOLS-HOST.md#optional-desktop-input) |
| Simulator sessions (optional) | 4 (`sim__*`) | only when `sim_launch` is explicitly selected | [TOOLS-HOST.md](docs/TOOLS-HOST.md#optional-simulator-sessions) |
| Isaac Lab (`isaaclab__*`, `run_script`) | 18 | by default (`ISAAC_MCP_LOCAL_TOOLS` unset or `all`), no bridge needed; job tools also need `ISAACLAB_PYTHON` / `ISAACLAB_ROOT`, and refuse to start next to a running Kit bridge | [TOOLS-ISAACLAB.md](docs/TOOLS-ISAACLAB.md) |
| Bridge built-ins (`run_python`, `status`, `job_status`, `job_cancel`, `list_bridges`, `reload_tools`, file tools) | 9 | listed from a packaged catalog even with Kit down; execution needs a reachable bridge; hidden by `ISAAC_MCP_BUILTINS=0` | [TOOLS-HOST.md](docs/TOOLS-HOST.md) |
| Generic scene and assets | 71 (`sim__quick_start`, `assets__*`, `sim__stage__*`, `sim__prim__*`, physics, timeline, controller runs, trajectory replay, body, viewport, ROS 2 TF, OmniGraph, maps and Kit UI) | on by default, catalogued offline; hide with `ISAAC_MCP_SCENE_TOOLS=0`; roots, scan, search and the pack tools run on host, others need sim-tools in selected Kit | [TOOLS-SCENE.md](docs/TOOLS-SCENE.md) |
| UR5e twin (`ur5e_dt__*`) | 23 | RISE lab's UR5e digital twin, Kaleb's work with that robot; every other tier is robot-agnostic. Listed from a packaged catalog even with Kit down; execution needs a selected Kit bridge with the twin extension | [TOOLS-UR5E.md](docs/TOOLS-UR5E.md) |

With default groups and no namespace filter, the server registers exactly 134 tools from startup:
the host, Isaac Lab, bridge built-in, generic and UR5e catalog tiers, whether or not Kit is running.
A client is shown **45 tools**: related tools are listed as one grouped tool with an `action`
argument (`sim__prim(action="create")`, `ur5e_dt__scene(action="quick_start")`, `jobs(action="status")`),
and `action="help"` describes each action. The single-purpose names used in this guide and the
docs (`sim__prim__create`, `sim__stage__save`) are the tools behind those actions: each is still
callable by that name with the same arguments, deprecated, and no longer listed.
`ISAAC_MCP_GROUP_TOOLS=0` lists them singly. The [table of groups](docs/TOOLS-HOST.md#grouped-tools)
maps every action to its tool.
A listed tool does not prove that a bridge exists: catalogued tools dial the selected bridge when
called and return an actionable error if none answers. Use `status` (or the CLI check below) to
find out. Fewer tools mean a filter:

- `ISAAC_MCP_BUILTINS=0` hides the bridge built-ins, `ISAAC_MCP_NAMESPACE` hides namespaced
  bridge and catalog tools, and `ISAAC_MCP_SCENE_TOOLS=0` hides the generic pack;
- `ISAAC_MCP_LOCAL_TOOLS` is restricting the local tools (the default, `all`, registers 31 local tools),
  so the listed host and Isaac Lab tools may be a subset. If its `gateway` group is excluded,
  `refresh_tools` and `use_bridge` are not listed: bridge tools are then discovered only when
  the server starts, so restart the MCP connection after starting Kit.

The other 9 tools are the bridge built-ins, also listed from a snapshot. With the standard
groups there are 134 tools in total; the optional desktop input and simulator session groups add 9 more.
`ISAAC_MCP_SCENE_TOOLS=0` hides the generic pack, leaving 63 tools with otherwise standard settings.
These filters control discovery, not security. Two things are enforced by the bridge itself: every
connection must present the bridge's token (clients on the same account read it from a private
file; `python3 mcp/isaac_mcp/client.py` does too), and a Kit started with `ISAAC_MCP_READ_ONLY=1`
refuses every tool that changes something with the code `READ_ONLY`. Do not work around either.

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

1. Call `status` first, once; it answers with or without a bridge. `refresh_tools` re-reads a
   bridge's tools after Kit starts or changes. From the checkout,
   `python3 mcp/isaac_mcp/client.py --list-bridges` lists advertised PID, host and port even
   when built-ins are hidden. It exits 2 with `[]` if none is advertised. From elsewhere use
   `"$(uv tool dir)/isaac-mcp/bin/python" -m isaac_mcp.client --list-bridges`.
2. Without the MCP tools, confirm the intended bridge with
   `python3 mcp/isaac_mcp/client.py status --host <host> --port <port>`; use `use_bridge` when needed. Pinned port variables override discovery, and an ambiguous
   unpinned selection is refused. A listed tool alone does not prove a bridge is running.
3. If none exists and the requested work needs one, `./run.sh --headless` starts a bridge and
   twin on port 8766 by default. Start additional instances only when explicitly requested and
   resource checks pass; use distinct ports, ROS domains and output directories. Headroom checks
   are snapshots, not reservations. Do not stop unrelated processes.
4. Refresh again. Clients that ignore `tools/list_changed` may need a connection restart; the CLI
   `list_tools --host <host> --port <port>` lists the verified bridge directly. Managed and plain
   launches hide the twin panel by default; use `show_ur5e_panel=true` or `--ur5e-panel` to show it.

`isaaclab__train`, `eval` and `play` refuse to start while a Kit bridge runs unless the user
knowingly permits `allow_concurrent_gpu=true`. See [Lab tools](docs/TOOLS-ISAACLAB.md) for guards.
The [agent skill](docs/AGENT-SKILLS.md) adds workflows and links to NVIDIA's official skills;
client setup is in [MCP server](docs/MCP-SERVER.md).

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
  filesystem. Verify the target PID and port (`status`, or the CLI check above) once, before the
  first scene-changing call, and again only after `use_bridge` or a simulator restart.
- Long simulator work runs as a job: `run_python` with `background=true`, `sim__simulation__run`
  (your controller's force and torque on a body every physics step; `mode="play"` runs it on a
  playing timeline beside ROS and action graphs) and `sim__trajectory__replay`
  (a saved trajectory into a labelled video) return a `job_id`. Wait with `job_status` and `wait_s`
  rather than polling, stop with `job_cancel`, and after a failed run call `sim__timeline__control`
  with `action=reset`. A Python controller is code in Kit and needs `ISAAC_MCP_ENABLE_SCENE_SCRIPTS=1`
  at launch; a `tcp://127.0.0.1:<port>` controller is your own process and needs nothing. See the
  [scene reference](docs/TOOLS-SCENE.md#controller-runs-jobs-and-replay).
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
  For a simulator that cannot be changed, start Kit with `ISAAC_MCP_READ_ONLY=1`.
  The three variables compose; see [TOOLS-HOST.md](docs/TOOLS-HOST.md) and, for what a
  scene-safe profile needs, [MCP-SERVER.md](docs/MCP-SERVER.md#limiting-what-a-client-can-reach).
- Every local tool carries MCP annotations (`readOnlyHint`, `destructiveHint`, ...). The job
  inspection and Python probe tools can have side effects and are not in `readonly`;
  `screenshot` writes files and returns desktop pixels. Annotations are hints, not enforcement.
  Generic scene/asset tools, bridge built-ins and the UR5e twin carry host annotations. Tools a project's own extension registers carry none.
  A grouped tool carries the most cautious hints of its actions.

## Importing robots and assets

One tool, `assets__import_path`, imports USD, URDF, xacro, SDF, MuJoCo XML, meshes, ROS maps and folders that hold them
(`format=auto`); the three older import tools were folded into it. Read [docs/ASSET-IMPORT.md](docs/ASSET-IMPORT.md) for formats,
options, error codes and limits. A file is converted on the host and no code from it is ever run; every file it names must be inside an
approved folder, and only the user can approve one (ask for the smallest folder). `sim__quick_start environment=warehouse` builds a
warehouse with a robot in it. A new format is an option on that tool, not a new tool.

## Changing tools

Every registered tool must be documented in its `docs/TOOLS-*.md` page, and the counts above
(each tier's Tools cell, the totals and the built-ins count) must match the code. Both are enforced by `python3 scripts/check_docs.py`, which also runs in
CI along with the host test suite (`pytest -q` from `mcp/`, see `.github/workflows/ci.yml`).
After changing a tool registry or contract, run `python3 scripts/gen_host_catalog.py --write`, then
`python3 scripts/gen_tool_reference.py --write`, and commit both results; `check_docs.py` and the host tests detect drift.

Use neutral `<repo>`, `<worktree>`, `<home>` and `<sim-install>` placeholders in public
records. `check_docs.py` scans tracked files and nested archives for private paths; it fails closed
on unreadable or oversized archives. `python3 scripts/check_private_paths.py --commits <range>`
also checks commit identities. Inspect screenshot pixels separately. Keep generated logs,
checkpoints and large recordings outside Git; document only the results and media needed to
understand or reproduce the work.

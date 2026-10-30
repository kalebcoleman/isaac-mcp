# System Architecture

This document describes the runtime architecture, naming conventions, port mapping, tool routing, ROS 2 / DDS transport rules, and transform tree interfaces for the UR5e digital twin repository on Isaac Sim 6.2.

---

## 1. Four-Layer Map

The project separates the host-side MCP interface, the generic Omniverse Kit bridge, the UR5e digital twin extension, and the underlying Isaac Sim runtime into four distinct layers:

```
+-------------------------------------------------------------------------------+
| Layer 1: Host MCP Server (mcp/)                                               |
| Package: isaac_mcp  |  CLI: isaac-mcp               |
| - Runs in host Python environment (uv tool / isolated venv)                  |
| - Speaks stdio MCP to AI agents (Claude Code, Claude Desktop, OpenCode)       |
| - Provides desktop capture: screenshot, screen recording, window management  |
| - Acts as TCP client to Omniverse Kit bridge                                  |
+---------------------------------------+---------------------------------------+
                                        | NDJSON over TCP (:8766)
+---------------------------------------v---------------------------------------+
| Layer 2: Generic Kit MCP Bridge (exts/isaac.mcp.bridge/)                          |
| Package: isaac_mcp_bridge  |  Extension ID: isaac.mcp.bridge                          |
| - Runs inside Omniverse Kit main loop                                         |
| - Provides TCP NDJSON server, request queuing, admission control              |
| - Dispatches tool calls onto Kit's main thread                                |
| - Built-ins: run_python, list_tools, reload_tools, list_bridges, file access  |
| - Optional UI inspector: isaac.mcp.panel                                   |
+---------------------------------------+---------------------------------------+
                                        | Python registrar / event hook
+---------------------------------------v---------------------------------------+
| Layer 3: UR5e Digital Twin Extension (exts/ur5e-dt/)                          |
| Package: ur5e_dt  |  Extension ID: ur5e-dt                                    |
| - Runs inside Omniverse Kit                                                   |
| - Owns the twin bridge endpoint and registers ur5e_dt.* tools (23 tools)      |
| - Builds and manages the workstation scene (UR5e, RG2 gripper, QuickChanger)  |
| - Provides motion control, joint drives, scene save/restore, camera setup     |
| - Owns the OmniGraph pipelines for ROS 2 transforms and joint mirroring       |
+---------------------------------------+---------------------------------------+
                                        | Native Kit / USD APIs
+---------------------------------------v---------------------------------------+
| Layer 4: Simulation Runtime (Isaac Sim 6.2.0.0 / Python 3.12)                 |
| - NVIDIA Omniverse Kit core, PhysX 5 (TGS solver), RTX real-time renderer     |
| - USD stage and articulation physics                                          |
| - Bundled ROS 2 packages (isaacsim.ros2.bridge, isaacsim.ros2.nodes, core)    |
+-------------------------------------------------------------------------------+
```

### Why Layers Are Kept Separate

- **`mcp/`** must start instantly on the host and must never require loading or syncing a 20+ GB Isaac Sim simulation environment just to run desktop screen capture or manage processes.
- **`exts/isaac.mcp.bridge`** is a generic, robot-agnostic transport layer. It can connect to any Kit application and exposes a uniform registration API for any tool package.
- **`exts/ur5e-dt`** encapsulates the robot model, physics parameters, workstation USD assets (111 MB), and manipulation logic.
- **`exts/isaac.mcp.panel`** is vendored alongside the bridge to provide an in-editor GUI inspector for registered tools.

---

## 2. Naming Rules

Consistency across extensions, packages, and CLI commands is strictly maintained according to these rules:

| Entity | Format | Rule | Examples |
|---|---|---|---|
| Kit Extension ID | Dotted / Hyphenated | Matches Omniverse extension identifier | `ur5e-dt`, `isaac.mcp.bridge`, `isaac.mcp.panel` |
| Extension Folder | Exact Match | Directory under `exts/` matches Extension ID | `exts/ur5e-dt/`, `exts/isaac.mcp.bridge/` |
| Python Package | Underscored | Standard valid Python import identifier | `ur5e_dt`, `isaac_mcp_bridge`, `isaac_mcp` |
| CLI / Server Name | Hyphenated | User-facing executable installed by `uv tool` | `isaac-mcp` |

---

## 3. Port Map and Endpoint Resolution

### Port Allocations

| Port | Owner | Purpose |
|---|---|---|
| **`8766`** | `ur5e-dt` extension | Default primary twin bridge port. Used by `run.sh`, `verify.sh`, and MCP clients. |
| **`8767`** | `ur5e-dt` secondary | Optional secondary twin instance when running dual sessions. |
| **`8799`** | Probe script | Dedicated port for `scripts/probe_62.sh` to run non-interfering diagnostic probes. |
| *(none)* | `isaac.mcp.bridge` base bridge | Normally autostarts on a port advertised through a portfile, which clients discover. Twin launches set `ISAAC_MCP_PORT=0` to disable it so each Kit process exposes exactly one endpoint (the twin bridge). |

### Endpoint Precedence Chain

When an MCP client or host utility connects to a running bridge, the target port resolves using the following priority order:

1. **Explicit argument:** Parameter passed directly to a tool call (e.g., `use_bridge(8766)`) or CLI flag (`--port 8766`).
2. **`UR5E_DT_PORT`:** Specific environment variable targeting the twin bridge.
3. **`ISAAC_MCP_PORT`:** Generic environment variable fallback.
4. **Portfile discovery:** Active portfiles published by running Kit instances in `~/.local/share/isaac.mcp.bridge/bridges/`. If multiple instances are running without an explicit pin, discovery refuses to guess and lists available ports.

---

## 4. Tool Naming and Routing

Communication between the host MCP server and the Kit bridge uses JSON lines over a raw TCP socket:

```
[MCP Client: Claude / OpenCode]
       │
       │ stdio JSON-RPC
       ▼
[Host MCP Server: isaac-mcp]
       │
       │ TCP NDJSON (:8766)
       ▼
[Kit Bridge: isaac.mcp.bridge (:8766)]
       │
       │ Main-thread dispatch
       ▼
[Twin Handler: ur5e_dt]
```

### Namespace Mapping (`.` vs `__`)

- Inside Kit, the twin registers tools under its canonical namespace with dots: `ur5e_dt.<tool_name>`.
  Examples: `ur5e_dt.play_scene`, `ur5e_dt.quick_start_with_workstation`, `ur5e_dt.setup_camera_tf_graph`.
- Because dots (`.`) are not valid characters in MCP tool names per the Model Context Protocol specification, the host server (`isaac_mcp/server.py`) translates dots to double underscores (`__`).
- Consequently, MCP clients see and invoke tools as: `ur5e_dt__<tool_name>` (e.g., `ur5e_dt__play_scene`).
- Tools built into the bridge itself (such as `list_bridges`, `use_bridge`, `run_python`) and host desktop capture tools (`screenshot`, `start_screen_recording`, `stop_screen_recording`, `list_windows`) do not carry the `ur5e_dt__` prefix.

---

## 5. ROS 2 and DDS Communication Rule

The digital twin includes OmniGraph nodes for ROS 2 transform publishing and joint state mirroring.

### Mandatory DDS Sourcing Rule

**The simulation and every ROS 2 client or inspection script communicating with it MUST source `config/ros_dds.env` before execution:**

```bash
source config/ros_dds.env
```

`config/ros_dds.env` enforces:
1. `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`: Standardizes on Eclipse Cyclone DDS across all nodes.
2. `ROS_LOCALHOST_ONLY=1`: Confines communication strictly to the loopback interface (`127.0.0.1`).
3. `CYCLONEDDS_URI=file://.../config/cyclonedds.xml`: Explicitly disables multicast in the Cyclone DDS XML configuration (`<Multicast>disabled</Multicast>`).

> [!IMPORTANT]
> If a ROS client does not source `config/ros_dds.env`, Cyclone DDS default multicast settings will fail to discover the localhost-confined simulation publisher. The nodes will report no errors, but messages publish into the void and subscribers receive 0 messages. Both `./run.sh` and `./scripts/verify_tf.sh` automatically source `config/ros_dds.env`.

---

## 6. Transform Tree Architecture and Intentional TF Changes

In Isaac Sim 6.2, transform publication was modernized to use the standard OmniGraph nodes:

```
USD Stage Prims ──► IsaacComputeTransformTree ──► ROS2PublishTransformTree ──► /tf, /tf_cam, /objects_poses_sim
```

This supersedes the deprecated 5.1 `targetPrims` pose-tree interface, which generates `[PoseTree] parent getObjectType eInvalid` warnings and fails to publish transforms reliably in 6.2.

### Frame Naming Preservation

Frame naming rules in `IsaacComputeTransformTree` preserve existing ROS consumer expectations:
- Unique prims retain their bare name (e.g., `base1`, `inverted_u_yellow`, `line_green`).
- Duplicate prim names under different parents resolve to `<parent>_<child>`.
- Any prim with the string attribute `isaac:nameOverride` uses that override unconditionally.

### Intentional 180° Camera Rotation

`IsaacComputeTransformTree` deliberately applies a **180° rotation around the X-axis** to all `UsdGeomCamera` prims (`ros2.nodes/docs/CHANGELOG.md:310`, version 1.14.0):
- **Rationale:** USD native cameras look down the $-Z$ axis with $+Y$ up. ROS optical frames convention expects $+X$ forward, $+Y$ right, $+Z$ down (or $+Z$ forward for optical axes). NVIDIA corrected the camera transform evaluation in 6.2 to align with ROS standard conventions.
- **Impact:** Transforms published on `/tf_cam` reflect this 180° X rotation compared to the old 5.1 output. Plain `UsdGeomXform` objects (such as those published on `/objects_poses_sim`) are unaffected.
- **Instruction:** **Do not attempt to "fix" or revert this rotation.** The change reflects the official Isaac Sim ROS 2 node standard and represents the pipeline functioning correctly. Reference values are recorded in `docs/provenance/tf-baseline-6.2.json`.

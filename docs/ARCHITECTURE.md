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
| - Registers ur5e_dt.* tools on the shared bridge (23 tools)                 |
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
| **`8767`** | Optional secondary bridge | Convention for the Isaac Lab bridge example; can also be used for a second twin instance when explicitly configured. |
| **`8799`** | Probe script | Dedicated port for `scripts/probe_62.sh` to run non-interfering diagnostic probes. |
| Selected port | `isaac.mcp.bridge` | One process-wide bridge serves the twin, built-ins and any other registered packs. The twin launcher sets `ISAAC_MCP_PORT` to its selected port and defers binding until the twin's tools have registered. |

### Endpoint Precedence Chain

When an MCP client or host utility connects to a running bridge, the target port resolves using the following priority order:

1. **Explicit argument:** Parameter passed directly to a tool call (e.g., `use_bridge(8766)`) or CLI flag (`--port 8766`).
2. **`UR5E_DT_PORT`:** Specific environment variable targeting the twin bridge.
3. **`ISAAC_MCP_PORT`:** Generic environment variable fallback.
4. **Portfile discovery:** Active portfiles under `$XDG_RUNTIME_DIR/isaac-mcp/`, or `/tmp/isaac-mcp-<uid>/` when
   `XDG_RUNTIME_DIR` is unset. If multiple instances are running without an explicit pin, discovery refuses to guess
   and lists available ports.

`run.sh` sets the shared bridge port to the resolved twin port; it defaults to loopback. `ISAAC_MCP_BIND` can change
the bridge bind address and requires an appropriate trust boundary. A direct Kit launch can join an already-running
shared bridge, or start the unbound bridge on the configured twin/shared port (8766 by default). If an explicitly
requested twin port conflicts with the shared bridge, registration fails rather than silently choosing another port.
The twin unregisters its own namespace on shutdown; the bridge and other owners remain available. Existing in-flight
twin calls drain before its state is cleaned up, so an immediate re-enable can be refused until they finish.
`./run.sh --port 0` asks the OS for a free port, advertised in the bridge's portfile for client discovery. When a twin
requesting port 0 rejoins an already-listening shared bridge, it adopts that bridge's port.

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

---

## 7. Arm Drive Gains (the ur5e-dt fidelity knob)

Every arm joint is a position-driven spring: `J*q'' + kd*q' + kp*q = kp*q_cmd`. The extension writes
`kp = _ur5e_stiffness` and `kd = _ur5e_damping` (`exts/ur5e-dt/ur5e_dt/extension.py`, `load_ur5e()`), in
**USD angular-drive units: N·m per degree and N·m·s per degree**. PhysX and the Python API work per radian,
so the solver sees 57.2958x larger numbers; `_verify_drive_gains()` reads `get_gains()` back and prints a
warning if the two ever disagree.

Two traps, both hit on 2026-10-30:

1. **A same-value write never reaches PhysX.** Writing `100` into an attribute that already holds `100`
   notifies nobody, so the articulation kept the asset's authored ~10230/4 per degree and the arm ran ~100x
   stiffer than configured, silently. The extension now applies the configured values with `set_gains()`
   and verifies them.
2. **Stiffness alone is not the knob.** What governs the response is `tau = kd/kp` (the position loop's time
   constant) and `zeta = kd / (2*sqrt(kp*J))` (whether it rings). Raising stiffness without raising damping
   lowers `zeta`: the asset gains ran at `zeta ~ 0.15`, a ~120 Hz resonance sampled at 60 Hz.

**Shipped default: 100/10 per degree (`tau = 0.10 s`), chosen 2026-10-30.** Measured on the UR5e twin
(elbow_joint, 0.3 rad step, 100 Hz `/joint_states`):

| stiffness/damping (per deg) | tau | rise (10-90 %) | settle (2 %) | overshoot | steady-state error |
|---|---|---|---|---|---|
| 50/5 | 0.10 s | 0.213 s | 0.505 s | 0.0 % | 1.9e-6 rad |
| 100/10 | 0.10 s | 0.216 s | 0.524 s | 0.0 % | 2.1e-6 rad |
| 100/20 | 0.20 s | 0.438 s | 0.918 s | 0.0 % | 5.1e-6 rad |
| 200/10 | 0.05 s | 0.120 s | 0.347 s | 0.0 % | 9.1e-7 rad |
| 400/10 | 0.025 s | 0.100 s | 0.289 s | 0.0 % | 4.2e-7 rad |

`tau` alone predicts the shape: the two pairs with `tau = 0.10 s` (50/5 and 100/10) settle within 4 % of
each other. 100/10 is heavily overdamped, so nothing rings at the 60 Hz sample rate and bench contact behaves
like a 5.7 kN·m/rad spring.

**To trade contact softness for mirroring fidelity**, keep `tau = damping/stiffness` in 0.02-0.05 s:
- 200/10 per degree (`tau` 50 ms) - settle 0.347 s, measured lag at 0.5 Hz ~34 deg (vs 41 deg at 100/10)
- 400/10 per degree (`tau` 25 ms) - settle 0.289 s, measured lag at 0.5 Hz ~30 deg

Both are measured, both stay overdamped. Costs: contact forces scale with stiffness (a bench touch is 2-4x
harder), and the `max_force = 200 N·m` cap starts to matter for fast moves. Raise stiffness *with* the
damping tail, never alone. After any change, re-run `demo/scripts/control_experiments.py --experiments
step,track,gains` (in the `ur5e-digital-twin` workspace) and re-check the S0-S9 demo.

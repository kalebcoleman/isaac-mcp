# Fork capability comparison

Source pin: `bfcd2111a9a98657ea87ba3be8564ea19c1a89e6`. This is an implementation comparison,
not a claim of complete parity or a security audit. The actual `@mcp.tool` registrations total
**43**: scene 7, objects 4, lighting 2, robots 6, sensors 4, materials 2, assets 4, graphs 2,
simulation **12**. `register_all_tools` includes all nine modules. The README table incorrectly
labels simulation as 11; its named list and total 43 agree with source.

Inspection included README examples/known limitations; every tool module's definitions/signatures;
handler registries and relevant scene/object/light/material/robot/sensor/asset/graph/simulation
implementations; version selection, v5/v6 adapters (particularly engine selection, frame handling,
step/reset, sensor caches and GMO decoding), and associated tests. No upstream implementation code/assets were
vendored. Source-derived signatures and short tool descriptions retain the
[MIT notice](provenance/whats2000-LICENSE). API approaches were cross-checked with installed 6.2 sources.
All four user-pinned repositories' remote default heads matched their pins at inspection time.

The fork's CPU test suite, excluding live connection/integration tests, passed **349 tests** with
its declared `mcp<2` constraint. An initial experiment with unconstrained MCP 2 failed three
FastMCP tests; that was a test-environment mismatch, not a supported dependency regression.
Those tests inject Kit/USD stubs and do not prove live simulator compatibility.

The [current release/backend results](evidence/project-scene/RUNS.md) supersede the original sensor guards cited by historical call indexes below.

The fork itself was also run through its real MCP interface on this same 6.2/PhysX machine: **24 calls across 18 tools**, including a falling cube, both approved USD projects, six robot DOFs, camera PNG and LiDAR wall returns. [Raw results and limits](evidence/project-scene/TOOL-COVERAGE.md). This is scoped live evidence, not validation of all 43 tools or all 6.2/Newton configurations.



## Mapping and gaps

Evidence labels are defined in the [run index](evidence/project-scene/README.md). Each implemented
mapping must be checked against that index/transcripts; a source implementation is not itself
live verification. `Partial` identifies a real parameter/behavior gap, not an alternate spelling.
No raw Python/manual UI action is counted as a substitute for any dedicated tool below (except
`execute_script`, whose purpose is explicitly arbitrary Python execution).

| Fork tool / task | Our registered tool(s) | Parameter, behavior and usability difference | Implementation status | Live exercise and evidence |
|---|---|---|---|---|
| [`import_urdf`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/assets.py) — Import a robot from a URDF file into the scene. | assets__import_urdf | Approved snapshot; relative file dependencies; DTD/entities/package:// refused; private generated output, then normal USD pipeline. | Implemented subset | [Expanded 23–25: actual converted prim, same stage](evidence/project-scene/TOOL-COVERAGE.md) |
| [`load_usd`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/assets.py) — Load a USD asset from a URL or file path into the scene. | assets__import_usd | No arbitrary paths/URLs; complete bounded project snapshot, missing/outside dependencies refused except explicitly identified installed OmniPBR module. | Implemented, stronger targeting/policy | [Core 9–14, 11 conflict refusal](evidence/project-scene/TOOL-COVERAGE.md); [agent source hashes + visible robot](evidence/project-scene/TOOL-COVERAGE.md) |
| [`search_usd`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/assets.py) — Search the NVIDIA USD asset library by text description, then load the best match. | assets__search | Local approved filename search only. Fork uses external USDSearch3d semantic service and auto-load; that service is not configured here. | Partial; remote semantic search missing | [Expanded 2: local filename result only](evidence/project-scene/TOOL-COVERAGE.md); semantic service not exercised. |
| [`generate_3d`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/assets.py) — Generate a 3D model from text or image using Beaver3D, then load it into the scene. | None | No generation integration; no approved provider credentials/endpoint or paid usage. No user file sent anywhere or placeholder claimed as implemented. | Missing integration; live provider use also blocked | Missing integration; no provider/service request or paid usage. |
| [`create_action_graph`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/graphs.py) — Create and wire an OmniGraph Action Graph. | sim__controller__create | Opt-in OnPlaybackTick→ScriptNode controller with inline code, fixed execution evaluator. Arbitrary graph topology/file-backed scripts not exposed. | Partial | [Expanded 22, 26, 28, 42: created controller executes](evidence/project-scene/TOOL-COVERAGE.md); arbitrary topology unimplemented. |
| [`edit_action_graph`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/graphs.py) — Edit an existing Action Graph: set attribute values or add connections. | sim__controller__update | Opt-in owned controller inline code replacement/recompile while stopped. General graph values/connections unsupported. | Partial | [Expanded 39–42: new inline code moves marker to Z=2](evidence/project-scene/TOOL-COVERAGE.md); general edits missing. |
| [`create_light`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/lighting.py) — Create a light in the scene. | sim__light__create | Sphere/Distant/Dome; explicit path, linear color/intensity, quaternion frame; no asset textures. | Implemented subset | [Expanded 8: intensity/color readback](evidence/project-scene/TOOL-COVERAGE.md); [agent image](evidence/project-scene/TOOL-COVERAGE.md) |
| [`modify_light`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/lighting.py) — Modify properties of an existing light. | sim__light__modify | Owned light only, explicit color and intensity, stopped target. | Implemented subset | [Expanded 9: intensity becomes 1000, color readback](evidence/project-scene/TOOL-COVERAGE.md); no radiometric test. |
| [`create_material`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/materials.py) — Create a PBR or physics material. | sim__material__create | Numeric UsdPreviewSurface and physics material; no network/asset-valued texture setter. | Implemented | [Expanded 10/12: PBR/physics values](evidence/project-scene/TOOL-COVERAGE.md); dynamic friction/restitution not measured. |
| [`apply_material`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/materials.py) — Bind a material to an object. | sim__material__apply | Session-layer binding to geometry, owned material; physics purpose selected from schema. | Implemented | [Expanded 11/13: binding and purpose](evidence/project-scene/TOOL-COVERAGE.md); [visible colored cube](evidence/project-scene/TOOL-COVERAGE.md) |
| [`create_object`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/objects.py) — Create a primitive object (Cube, Sphere, Cylinder, Cone, Capsule, Plane). | sim__prim__create | Explicit unique path; Cube/Sphere/Cylinder/Capsule/Cone/Xform/PhysicsScene; scale, optional static collision or rigid+collision. Color via material tools. | Implemented subset (no Plane type) | [Core 6/8/18/19 and expanded 3/4](evidence/project-scene/TOOL-COVERAGE.md); [actual fall/rest](evidence/project-scene/TOOL-COVERAGE.md) |
| [`delete_object`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/objects.py) — Delete an object from the scene. | sim__prim__delete | Confirmation and stopped target; pack-owned only, sensor/articulation protection, reports remaining composition. | Partial, intentionally narrower | [Expanded 5–7: clone absent after delete; 45: sensor refusal](evidence/project-scene/TOOL-COVERAGE.md) |
| [`transform_object`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/objects.py) — Set the transform (position, rotation, scale) of an existing object. | sim__prim__set_transform | wxyz instead of Euler degrees; explicit frame/scale; foreign/complex/animated stacks refused; runtime bodies require stop. | Partial | [Core 12/13: world pose under rotated parent; 22 refuses active body](evidence/project-scene/TOOL-COVERAGE.md) |
| [`clone_object`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/objects.py) — Duplicate an existing object to a new prim path. | sim__prim__clone | Simple owned leaves only, no arbitrary composed subtree cloning. | Partial | [Expanded 5: new Cube/pose readback](evidence/project-scene/TOOL-COVERAGE.md); arbitrary subtree clone missing. |
| [`create_robot`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/robots.py) — Create a robot in the scene from the Isaac Sim asset library. | assets__search + assets__import_usd | File-based discovery/import rather than hard-coded robot type aliases. Pinned non-UR5e robot exercised. | Implemented, different workflow | [Expanded 20/21: real Agilebot import](evidence/project-scene/TOOL-COVERAGE.md); [six runtime DOFs and visible robot](evidence/project-scene/TOOL-COVERAGE.md) |
| [`list_available_robots`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/robots.py) — List all available robots discovered from the Isaac Sim asset server. | assets__scan/search | Filename discovery of approved installed/project folders, not an inferred fixed 107-robot library. Metadata inspected explicitly. | Partial | [Core 2/3, expanded 2: approved file discovery](evidence/project-scene/TOOL-COVERAGE.md); automatic robot classification/library server absent. |
| [`refresh_robot_library`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/robots.py) — Force re-scan the asset server for available robots. Use this if new robot assets were added. | assets__scan/search | Every scan is fresh; no cache invalidation tool needed. | Implemented for approved local folders | [Live local scan/search](evidence/project-scene/TOOL-COVERAGE.md); CPU tests add a file and verify a fresh scan; no Nucleus refresh. |
| [`get_robot_info`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/robots.py) — Get robot joint information including names, DOF count, joint types, and limits. | sim__physics__inspect + sim__joints__inspect/state | Authored articulation/joints versus initialized runtime DOFs separated. Cap/truncation reported. | Implemented | [Expanded 15/30: authored/runtime fixture joint](evidence/project-scene/TOOL-COVERAGE.md); [real robot six DOFs](evidence/project-scene/TOOL-COVERAGE.md) |
| [`set_joint_positions`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/robots.py) — Set target joint positions on a robot via ArticulationAction. | sim__joints__command | Full DOF-ordered runtime target array; radians/stage units, runtime limits checked; no direct teleport or subset-index support. | Implemented subset | [Expanded 31–33: -0.2 target then measured joint movement; bad limit refusal](evidence/project-scene/TOOL-COVERAGE.md) |
| [`get_joint_positions`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/robots.py) — Read current joint positions from a robot. | sim__joints__state | PhysX tensor positions, velocities and names; requires initialized playing/paused articulation; no drive-target fallback. | Implemented | [Expanded 30/33: PhysX DOF readback](evidence/project-scene/TOOL-COVERAGE.md); [real robot](evidence/project-scene/TOOL-COVERAGE.md) |
| [`get_scene_info`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/scene.py) — Ping the Isaac Sim extension server and return scene information including stage path, assets root, and prim count. | sim__stage__info | Explicit incarnation/stage IDs, capped count, units and timeline. No unbounded arbitrary attributes. | Implemented | [Core 1: PID/session/stage/units](evidence/project-scene/TOOL-COVERAGE.md); stale-target refusal 32. |
| [`create_physics_scene`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/scene.py) — Create a physics scene, adding a ground plane only if the stage lacks one. | sim__prim__create + sim__physics__configure | Explicit path and stopped timeline; duplicate scene refused; optional static cube floor is a separate create with collision=true. | Implemented, different workflow | [Core 18; expanded 4/14/33: collider/gravity/dt and measured motion](evidence/project-scene/TOOL-COVERAGE.md) |
| [`clear_scene`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/scene.py) — Remove all prims from the scene. | sim__stage__clear_owned | Only pack-owned subtrees; returns protected sensors/articulations as refused. Does not destroy an existing user stage. | Partial, intentionally narrower | [Clear 3/4: removed/refused list versus surviving tree](evidence/project-scene/TOOL-COVERAGE.md); destructive full-stage reset deliberately absent. |
| [`list_prims`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/scene.py) — List the prims directly under root_path, optionally filtered by type. | sim__stage__tree + sim__prim__find | Bounded traversal including inactive prims; literal substring/type search, not regex. | Implemented | [Core 5/15/16: tree/find and truncation](evidence/project-scene/TOOL-COVERAGE.md) |
| [`get_prim_info`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/scene.py) — Get detailed information about a specific prim. | sim__prim__get + sim__physics__inspect | Explicit local/world matrices, default-time USD versus runtime labels; no arbitrary attribute dump or recursive geometry bounds. | Partial | [Core 13/26: transform/schema reads match runtime](evidence/project-scene/TOOL-COVERAGE.md); complete geometry bounds absent. |
| [`list_environments`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/scene.py) — List all available environments discovered from the Isaac Sim asset server. | assets__roots + assets__scan/search | Actual approved folders, fresh scans, no fixed installed/library count. No implicit Nucleus/network scan or environment classification. | Partial | [Core 2/3: actual approved folder/files](evidence/project-scene/TOOL-COVERAGE.md); environment classification/network inventory unimplemented. |
| [`load_environment`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/scene.py) — Load a pre-built environment into the scene. Supports fuzzy matching. | assets__import_usd | Approved relative snapshot, explicit wrapper and pose; units/up-axis mismatch refused, no automatic floor removal/normalization. | Implemented, narrower policy | [Generic multifile USD loader exercised (core 10)](evidence/project-scene/TOOL-COVERAGE.md); no separate full environment-project acceptance test. |
| [`create_camera`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/sensors.py) — Add a camera sensor to the scene. | sim__camera__create | Persistent CPU-backed Replicator camera, bounded image size; wxyz or explicit world look-at target, stopped creation; owned sensor deletion/recreation with bounded resource cleanup. | Implemented subset | [Expanded 18/35: camera produces PNG](evidence/project-scene/TOOL-COVERAGE.md); [6.0.0.1 owned-CPU retest](evidence/project-scene/TOOL-COVERAGE.md) |
| [`capture_image`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/sensors.py) — Capture an RGB image from a camera sensor. | sim__camera__capture | Exclusive PNG output, hash/dimensions, warm-up refusal; latest rendered frame, not step-synchronized. | Implemented | [Agent: six 1024×768 images and hashes](evidence/project-scene/TOOL-COVERAGE.md); earlier CUDA failure retained; timing not synchronized. |
| [`create_lidar`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/sensors.py) — Add a lidar sensor to the scene. | sim__lidar__create | Generic installed 6.x RTX sensor; no unverified hardware preset. Create while stopped; deletion refused. | Implemented subset | [Expanded 19/36: initialized LiDAR returns points](evidence/project-scene/TOOL-COVERAGE.md) |
| [`get_lidar_point_cloud`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/sensors.py) — Get point cloud data from a lidar sensor. | sim__lidar__read | Decode spherical/Cartesian convention, frame label, invalid filtering, <=256 returned points. No empty success. | Implemented subset | [Expanded 36: finite bounded points at ~2.5 m wall](evidence/project-scene/TOOL-COVERAGE.md); no hardware calibration. |
| [`play_simulation`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/simulation.py) — Start the physics simulation. | sim__timeline__control(action=play) | Explicit target; waits for normal Kit update and returns observed timeline state. No nested pumping. | Implemented | [Core 21/23/25: playing plus actual body motion](evidence/project-scene/TOOL-COVERAGE.md) |
| [`pause_simulation`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/simulation.py) — Pause the physics simulation. | sim__timeline__control(action=pause) | Explicit target and observed paused state after ordinary update. | Implemented | [Core 24–27: paused runtime/USD observations and save](evidence/project-scene/TOOL-COVERAGE.md) |
| [`stop_simulation`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/simulation.py) — Stop the physics simulation and reset to spawn state. | sim__timeline__control(action=stop) | Explicit target; rewinds simulation, confirms observed state. | Implemented | [Agent: spawn pose and edit target restored after Stop](evidence/project-scene/TOOL-COVERAGE.md) |
| [`step_simulation`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/simulation.py) — Advance the simulation by exactly N physics frames on a FROZEN timeline. | sim__simulation__step | 1..120 exact selected-engine steps on an initialized PAUSED timeline, time-delta check, <=16 body and <=8 articulation observations. Stopped/uninitialized state refused; qualified Newton uses backend-specific timing/tensors. | Implemented subset | [Expanded 33: 120 steps, measured 1 s, body + joint movement](evidence/project-scene/TOOL-COVERAGE.md); [independent fall/rest](evidence/project-scene/TOOL-COVERAGE.md) |
| [`set_physics_params`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/simulation.py) — Configure physics engine parameters. | sim__physics__configure | Explicit existing PhysicsScene, gravity and timestep with actual integer rate returned. No GPU/backend toggle. | Implemented subset | [Expanded 14/33: 1/120 dt measured over 120 steps](evidence/project-scene/TOOL-COVERAGE.md); GPU/backend toggle missing. |
| [`get_isaac_logs`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/simulation.py) — Diagnostic tool: recent WARN/ERROR logs plus captured print() output. | sim__logs__tail | Selected Kit log only; last 32KiB, <=100 WARN/ERROR lines; no stdout capture, clear, or since-play filter. | Implemented subset | [Expanded 37: selected bounded warning tail](evidence/project-scene/TOOL-COVERAGE.md); stdout/since-play behavior absent. |
| [`get_simulation_state`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/simulation.py) — Get the current simulation state: timeline status (playing/stopped/paused), | sim__stage__info | Target identity, playing/stopped/time and authored units; qualified runtime/backend identity and explicit unsupported-stack errors. | Implemented | [Core 1 and agent state checks](evidence/project-scene/TOOL-COVERAGE.md); [time/lease/Stop behavior](evidence/project-scene/TOOL-COVERAGE.md) |
| [`get_physics_state`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/simulation.py) — Diagnostic tool: get physics state for a prim. | sim__body__state + sim__physics__inspect | Selected PhysX/Newton runtime pose separated from authored schemas; optional runtime velocities and mass from tensors. Contact diagnostics remain narrower. | Partial | [Core 23/25/26: measured PhysX pose versus USD](evidence/project-scene/TOOL-COVERAGE.md); runtime velocity/mass added and exercised; contact diagnostics remain incomplete. |
| [`get_joint_config`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/simulation.py) — Diagnostic tool: get joint drive configuration for a robot articulation. | sim__joints__inspect | Bounded authored limits/drives, units explicit; not all fork joint diagnostics. | Implemented subset | [Expanded 15/16: actual authored limits/drive units](evidence/project-scene/TOOL-COVERAGE.md) |
| [`set_drive_params`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/simulation.py) — Write a joint drive's stiffness, damping and/or max_force. | sim__joints__drive | Single revolute/prismatic joint, explicit target/gains/maxForce; stopped timeline and limits checked. USD gains remain degrees-based. | Implemented subset | [Expanded 16/17/30: authored gains/target, achieved +0.2 rad, limit refusal](evidence/project-scene/TOOL-COVERAGE.md) |
| [`execute_script`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/simulation.py) — Escape hatch: execute arbitrary Python code in Isaac Sim. | Existing run_python | Equivalent only to this explicitly arbitrary-code capability; never counted for another tool. Existing trusted raw bridge behavior unchanged; not isolated or authenticated. | Existing | [Real MCP run_python returns 4](evidence/project-scene/TOOL-COVERAGE.md); only this arbitrary-code capability counts. |
| [`reload_script`](https://github.com/whats2000/isaacsim-mcp-server/blob/bfcd2111a9a98657ea87ba3be8564ea19c1a89e6/isaac_mcp/tools/simulation.py) — Reload a Python controller from a file on disk. | sim__controller__update; existing reload_tools for registered tool modules | Controller code can be replaced; arbitrary Python file/module hot reload is not a dedicated product operation. | Partial; general script reload missing | [Expanded 39–42: owned inline controller recompiles and moves marker](evidence/project-scene/TOOL-COVERAGE.md); general file/module reload absent. |

## Version and trust distinctions

The fork selects v6 for detected major version >=6 and falls back to v5 when detection fails.
That dispatch is not proof of 6.2 support. Its README targets 5.1–6.0.1. The fork documents
Newton drive divergence/unenforced limits, a non-removable first 6.0 RTX camera, intermittent
5.1 LiDAR reads and non-removable 5.1 RTX LiDAR prims. Our runtime paths require the actual
installed APIs and refuse unverified backends rather than silently using USD targets as state.
The former borrowed-GPU camera path crashed on6.0.0.1 and in a later6.2 replay. Those
failures are retained. The owned-CPU attachment adapter subsequently passed217-capture stress
runs on each installed release, including6.0.0.0 without experimental.rtx and the distinct
6.0.0.1 build. A stale first read on6.0.0.0 was reproduced and addressed with CPU priming and
four ordinary render completions. Sensor creation/deletion/recreation and stage teardown now
have ownership checks; the6.0.1 native teardown failure and scoped retest are documented.
A save/reopen test also found invalid serialized native graph handles; saved copies omit those
runtime records while retaining scene sensor definitions. Fresh-process6.0.0 camera readout
after reopening still returns NOT_READY despite extended warm-up; it remains a documented
follow-up, not a working edge case. These are measured adapter paths,
not an all-versions or all-edge-cases guarantee.
See the per-version evidence, not PR #9's different workflows, for tested combinations.

The fork's path/URL loaders, graph values and scripts are not a sandbox. Our original trusted-project
snapshot policy remains in force for generic USD/URDF inputs; arbitrary asset-valued setters are
not exposed. Controller code has an additional launch-time opt-in. ScriptNode's Kit opt-in is a
consent mechanism, not isolation. Neither project demonstrates a hostile USD sandbox by these APIs.
Untrusted import and authenticated remote upload remain disabled. Trusted USDZ now has bounded stored/aligned archive validation, member/dependency verification and live import tests; this is not hostile-archive OS isolation.

## Setup and realistic workflows

Approve narrow local installed/project/library folders using [project setup](TOOLS-SCENE.md).
Search/scan returns real paths and does not promise a robot count. Network asset services are not
queried implicitly. Select session/stage before import; inspect authored physics; play/pause to
initialize PhysX; use exact stepping and measured state for debugging. Create sensors stopped,
allow render warm-up, and inspect the resulting PNG/point coordinates. Configure optional controller
code explicitly at launch. No user files or paid API requests were sent to a generation service.

## Pinned source signatures

These are extracted from the actual 43 registered functions, not the README count. Our schemas
are in the packaged catalogs and were listed by the real MCP client in the independent-use evidence.

| Fork tool | Declared parameters (excluding injected context) |
|---|---|
| `import_urdf` | `urdf_path: str, prim_path: str='/World/robot', position: Optional[List[float]]=None` |
| `load_usd` | `usd_url: str, prim_path: str='/World/my_usd', position: Optional[List[float]]=None, scale: Optional[List[float]]=None` |
| `search_usd` | `text_prompt: str, target_path: str='/World/my_usd', position: Optional[List[float]]=None, scale: Optional[List[float]]=None` |
| `generate_3d` | `text_prompt: Optional[str]=None, image_url: Optional[str]=None, position: Optional[List[float]]=None, scale: Optional[List[float]]=None` |
| `create_action_graph` | `graph_path: str='/World/ActionGraph', nodes: Optional[List[Dict[str, str]]]=None, connections: Optional[List[List[str]]]=None, values: Optional[List[Dict[str, object]]]=None, evaluator: str='execution', script_file: Optional[str]=None, inline_script: Optional[str]=None` |
| `edit_action_graph` | `graph_path: str='/World/ActionGraph', values: Optional[List[Dict[str, object]]]=None, connections: Optional[List[List[str]]]=None` |
| `create_light` | `light_type: str='DistantLight', position: Optional[List[float]]=None, intensity: float=1000.0, color: Optional[List[float]]=None, rotation: Optional[List[float]]=None, prim_path: Optional[str]=None` |
| `modify_light` | `prim_path: str, intensity: Optional[float]=None, color: Optional[List[float]]=None` |
| `create_material` | `material_type: str='pbr', prim_path: Optional[str]=None, material_path: Optional[str]=None, color: Optional[List[float]]=None, roughness: float=0.5, metallic: float=0.0` |
| `apply_material` | `material_path: str, target_prim_path: str` |
| `create_object` | `object_type: str='Cube', position: Optional[List[float]]=None, rotation: Optional[List[float]]=None, scale: Optional[List[float]]=None, size: Optional[float]=None, color: Optional[List[float]]=None, physics_enabled: bool=False, prim_path: Optional[str]=None` |
| `delete_object` | `prim_path: str` |
| `transform_object` | `prim_path: str, position: Optional[List[float]]=None, rotation: Optional[List[float]]=None, scale: Optional[List[float]]=None` |
| `clone_object` | `source_path: str, target_path: str, position: Optional[List[float]]=None` |
| `create_robot` | `robot_type: str='franka', position: Optional[List[float]]=None, name: Optional[str]=None, prim_path: Optional[str]=None` |
| `list_available_robots` | `` |
| `refresh_robot_library` | `` |
| `get_robot_info` | `prim_path: str` |
| `set_joint_positions` | `prim_path: str, joint_positions: List[float], joint_indices: Optional[List[int]]=None` |
| `get_joint_positions` | `prim_path: str` |
| `get_scene_info` | `` |
| `create_physics_scene` | `gravity: Optional[List[float]]=None, scene_name: str='PhysicsScene'` |
| `clear_scene` | `keep_physics: bool=False, keep_environment: bool=False` |
| `list_prims` | `root_path: str='/', prim_type: Optional[str]=None, recursive: bool=False` |
| `get_prim_info` | `prim_path: str` |
| `list_environments` | `` |
| `load_environment` | `environment: str, prim_path: Optional[str]=None` |
| `create_camera` | `prim_path: str='/World/Camera', position: Optional[List[float]]=None, rotation: Optional[List[float]]=None, resolution: Optional[List[int]]=None, target: Optional[List[float]]=None` |
| `capture_image` | `prim_path: str='/World/Camera', output_path: Optional[str]=None` |
| `create_lidar` | `prim_path: str='/World/Lidar', position: Optional[List[float]]=None, rotation: Optional[List[float]]=None, config: Optional[str]=None` |
| `get_lidar_point_cloud` | `prim_path: str='/World/Lidar', max_points: Optional[int]=None, output_path: Optional[str]=None` |
| `play_simulation` | `` |
| `pause_simulation` | `` |
| `stop_simulation` | `` |
| `step_simulation` | `num_steps: int=1, observe_prims: Optional[List[str]]=None, observe_joints: Optional[List[str]]=None` |
| `set_physics_params` | `gravity: Optional[List[float]]=None, time_step: Optional[float]=None, gpu_enabled: Optional[bool]=None` |
| `get_isaac_logs` | `clear: bool=False, count: int=100, since_last_play: bool=True` |
| `get_simulation_state` | `` |
| `get_physics_state` | `prim_path: str` |
| `get_joint_config` | `prim_path: str` |
| `set_drive_params` | `prim_path: str, joint_name: str, stiffness: Optional[float]=None, damping: Optional[float]=None, max_force: Optional[float]=None` |
| `execute_script` | `code: str, cwd: Optional[str]=None` |
| `reload_script` | `file_path: Optional[str]=None, module_name: Optional[str]=None, script_file: Optional[str]=None` |

"""Versioned public contracts; stdlib only, shared by host catalog and Kit pack."""


def field(kind, description, required=True, **kw):
    return dict(type=kind, description=description, required=required, **kw)


TARGET = dict(
    session_id=field(
        "string", "Exact session_id from sim.stage.info (process incarnation)."
    ),
    stage_id=field(
        "string", "Exact stage_id from sim.stage.info; stale stages are refused."
    ),
)
POSE = dict(
    position=field(
        "array", "Three finite coordinates in stage units.", False, default=[0, 0, 0]
    ),
    orientation=field(
        "array", "Unit quaternion [w,x,y,z].", False, default=[1, 0, 0, 0]
    ),
    frame=field("string", "Pose frame: local or world.", False, default="local"),
)
REGISTRY = {}


def add(name, desc, params=None):
    REGISTRY[name] = dict(description=desc, parameters=params or {})


add(
    "assets.roots",
    "List launch-approved project roots and capability refusals. Filename access only; trusted projects are NOT sandboxed.",
)
add(
    "assets.scan",
    "Bounded filename-only USD discovery on the MCP host. No USD parser. Results are untrusted data.",
    dict(
        root_id=field("string", "Approved root ID, never an absolute path."),
        offset=field(
            "integer", "Page offset into the sorted bounded scan.", False, default=0
        ),
        limit=field("integer", "Page size 1..100.", False, default=50),
    ),
)
ASSET = dict(
    root_id=field("string", "Approved trusted project root ID."),
    relative_path=field("string", "Relative USD file path within that root."),
)
add(
    "assets.info",
    "Snapshot and inspect a USER-TRUSTED project on the selected Kit. Native USD parsing is NOT sandboxed. Refuses untrusted profiles and unresolved/outside snapshot dependencies.",
    dict(**TARGET, **ASSET),
)
add(
    "assets.import_usd",
    "Import a USER-TRUSTED project snapshot below a new wrapper prim, preserving the source and current stage. Requires stopped timeline, matching units/up-axis, explicit target and no conflict. No URLs or arbitrary asset attributes.",
    dict(
        **TARGET,
        **ASSET,
        prim_path=field("string", "New absolute wrapper path; parent must exist."),
        **POSE,
    ),
)
add(
    "assets.prepare_workstation",
    "Prepare a trusted project USD and its project files in the UR5e workstation cache without overwriting. Requires an approved root and live Kit dependency validation; call ur5e_dt.load_workstation afterward. Not a sandbox.",
    dict(
        **TARGET,
        source_path=field(
            "string", "Absolute local USD path inside an already approved trusted root."
        ),
    ),
)
add(
    "assets.import_path",
    "Import a trusted local USD from an absolute path under an approved project root. Copies and validates its project dependencies before authoring a reference; source files remain unchanged.",
    dict(
        **TARGET,
        source_path=field(
            "string", "Absolute local USD path inside an already approved trusted root."
        ),
        prim_path=field("string", "New absolute wrapper prim path; parent must exist."),
        **POSE,
    ),
)
add(
    "sim.stage.info",
    "Read process incarnation, stage identity, units, timeline and bounded prim count. Start here before scene operations.",
)
add(
    "sim.stage.open",
    "Open a USD file (.usd/.usda/.usdc/.usdz under your home) as the current stage, like File > Open, with the timeline stopped. Refuses when the current stage has unsaved edits unless discard_changes=true. Returns the new session_id/stage_id for later sim__* calls; run_python sessions reset.",
    dict(
        **TARGET,
        path=field("string", "Absolute path of the USD file to open (a leading tilde for your home is expanded)."),
        discard_changes=field("boolean", "Open even if the current stage has unsaved edits (they are lost).", False, default=False),
    ),
)
add(
    "sim.stage.tree",
    "Bounded stage hierarchy including inactive prims. Text is untrusted data.",
    dict(
        **TARGET,
        root=field("string", "Absolute subtree root.", False, default="/"),
        limit=field("integer", "Maximum nodes 1..200.", False, default=100),
        depth=field("integer", "Levels below root to list (1..64); default all.", False),
        include_runtime=field("boolean", "Also list Kit's runtime /Render (Replicator) prims, hidden by default.", False, default=False),
    ),
)
add(
    "sim.prim.get",
    "Read local/world matrices and authored physics at default USD time. Runtime physics readiness remains unknown.",
    dict(**TARGET, path=field("string", "Absolute prim path.")),
)
add(
    "sim.prim.find",
    "Bounded literal path substring/type search (not regex). Includes inactive prims.",
    dict(
        **TARGET,
        contains=field(
            "string", "Literal path substring <=128 characters.", False, default=""
        ),
        type_name=field("string", "Exact USD type or empty.", False, default=""),
        limit=field("integer", "Maximum matches 1..200.", False, default=100),
    ),
)
add(
    "sim.prim.create",
    "Create a Cube, Sphere, Xform, or PhysicsScene in session layer. No arbitrary references/attributes. Stopped timeline, parent and no-conflict required. Dimensions are stage units.",
    dict(
        **TARGET,
        path=field("string", "New absolute prim path."),
        kind=field(
            "string", "Cube, Sphere, Cylinder, Capsule, Cone, Xform or PhysicsScene."
        ),
        size=field(
            "number", "Positive cube edge or sphere diameter.", False, default=1
        ),
        rigid=field(
            "boolean",
            "Author rigid body AND collider on geometry.",
            False,
            default=False,
        ),
        **POSE,
    ),
)
add(
    "sim.prim.set_transform",
    "Set a static local/world pose for a prim created by this pack. Refuses playing/paused physics and foreign, animated or complex transform stacks. No claim of runtime teleportation.",
    dict(**TARGET, path=field("string", "Pack-owned prim path."), **POSE),
)
add(
    "sim.physics.inspect",
    "Bounded authored collision/body/mass/joint/articulation report. Does not assert initialized or stable physics.",
    dict(
        **TARGET,
        root=field("string", "Absolute subtree path.", False, default="/"),
        limit=field("integer", "Maximum visited nodes 1..200.", False, default=100),
    ),
)
add(
    "sim.body.state",
    "Read initialized rigid-body world pose on selected PhysX or qualified Newton backend; fails when stopped/uninitialized or not a body. Position in stage units, quaternion [w,x,y,z]. Optional runtime velocities/mass from tensor API; no authored-USD fallback. PHYSICS_STATE_INVALID identifies invalid observed data, not bad caller input.",
    dict(
        **TARGET,
        path=field("string", "Absolute rigid body path."),
        include_dynamics=field(
            "boolean",
            "Include measured world COM linear/angular velocity and runtime mass, including kg conversion.",
            False,
            default=False,
        ),
    ),
)
add(
    "sim.timeline.control",
    "Explicit play, pause or stop. Stop rewinds physics and awaits pending native render/writer completion, including writer finalization and capture-settings restoration. The five-second timeout is cooperative, not a hard deadline for native calls. Play only observes a pending Stop; TIMELINE_BUSY or TIMELINE_INTERRUPTED requires inspection and explicit retry, never automatic replay. Leases the session edit target for physics writeback and restores it on Stop. Never pumps the Kit update loop; exact stepping uses sim.simulation.step.",
    dict(**TARGET, action=field("string", "play, pause, or stop.")),
)
add(
    "sim.stage.save",
    "Export a flattened copy, including session edits, to a new basename ending .usda in the configured output root, or in `directory` (an existing folder under your home, e.g. a lab work folder). Never overwrite or switch the current stage. Paused Newton copies runtime rigid-body poses into the export only (<=256 bodies), not a solver checkpoint. External texture paths remain dependencies. Process-bound SyntheticData pipelines and pack render products are omitted from the copy; cameras, LiDAR definitions and user graphs remain. Unknown native pipeline content refuses save.",
    dict(
        **TARGET,
        filename=field("string", "New basename ending .usda; no directories."),
        directory=field("string", "Existing folder under your home to write into instead of the output root (absolute path). Never overwrites.", False),
        reset_camera_render_handles=field(
            "boolean",
            "Explicitly remove validated session-only ROS camera viewportHandle fields from the copy so native textures can rebuild after restart. Default false refuses affected saves; true still refuses authored, ambiguous, connected or unavailable bindings. Originals and live stage are unchanged; omitted property paths are returned. This is export policy, not proof of authorship.",
            False,
            default=False,
        ),
    ),
)
add(
    "sim.viewport.frame",
    "Select and frame visible geometry in the selected stage's active viewport. Edits the active camera in the session layer, preserving source layers and the prior edit target; no Kit undo entry. Refuses mismatched contexts, look-through targets, unsafe session-sublayer camera opinions and empty bounds. Does not wait for rendered pixels or prove visibility; allow ordinary render updates, then capture the verified app window using screenshot. Select a smaller subtree if surrounding geometry occludes it.",
    dict(**TARGET, path=field("string", "Absolute prim path to frame.")),
)

DISABLED = {
    "isolated_untrusted": "Disabled: no verified whole-lifetime GPU Kit filesystem/network/resolver/plugin/script enforcement. No fallback to trusted.",
    "remote_upload": "Disabled: authenticated distinct-host transport and authorization have not been validated. Use an explicitly configured shared mount.",
    "presets": "Named manifests are not implemented; select discovered files directly.",
}

add(
    "assets.search",
    "Fresh filename search across approved project/library roots. No network discovery, semantic service, manifest or fixed robot count. Refresh by calling again.",
    dict(
        query=field(
            "string",
            "Case-insensitive filename substring <=128 characters.",
            False,
            default="",
        ),
        limit=field("integer", "Maximum results 1..100.", False, default=50),
    ),
)
add(
    "sim.prim.delete",
    "Delete one pack-owned subtree from the session layer only. Requires stopped physics and confirm=true; exact owned sensors release their resources; sensor-containing subtrees and articulation roots are refused.",
    dict(
        **TARGET,
        path=field("string", "Pack-owned root path."),
        confirm=field("boolean", "Explicit confirmation; false refuses."),
    ),
)
add(
    "sim.prim.clone",
    "Clone a pack-created simple leaf primitive into a new path, preserving session-layer attributes. Refuses references, children, sensors and articulations.",
    dict(
        **TARGET,
        path=field("string", "Pack-created source leaf."),
        new_path=field("string", "Unused target path."),
    ),
)
add(
    "sim.stage.clear_owned",
    "Remove only pack-created non-sensor, non-articulated subtrees, preserving other scene content. Explicit stopped target and confirm required. Returns removed/refused lists.",
    dict(**TARGET, confirm=field("boolean", "Explicit confirmation.")),
)
add(
    "sim.light.create",
    "Create a SphereLight, DistantLight or DomeLight without asset-valued textures. Pose in stage units, orientation wxyz.",
    dict(
        **TARGET,
        path=field("string", "New prim path."),
        kind=field(
            "string",
            "SphereLight, DistantLight or DomeLight.",
            False,
            default="DistantLight",
        ),
        intensity=field(
            "number", "Nonnegative USD light intensity.", False, default=1000
        ),
        color=field("array", "RGB linear color 0..1.", False, default=[1, 1, 1]),
        **POSE,
    ),
)
add(
    "sim.light.modify",
    "Set intensity and color (both required) of a pack-owned light on a stopped timeline.",
    dict(
        **TARGET,
        path=field("string", "Pack-owned light path."),
        intensity=field("number", "USD light intensity."),
        color=field("array", "RGB linear color 0..1."),
    ),
)
add(
    "sim.material.create",
    "Create a UsdPreviewSurface or physics material with finite numeric parameters. No asset-valued attributes or texture URLs.",
    dict(
        **TARGET,
        path=field("string", "New Material path; parent must exist."),
        color=field("array", "RGB 0..1.", False, default=[0.5, 0.5, 0.5]),
        roughness=field("number", "0..1.", False, default=0.5),
        metallic=field("number", "0..1.", False, default=0),
    ),
)
add(
    "sim.material.apply",
    "Bind a pack-created material to an existing geometry prim in the session layer, preserving source files.",
    dict(
        **TARGET,
        path=field("string", "Geometry prim."),
        material_path=field("string", "Pack-created Material path."),
    ),
)
add(
    "sim.physics.configure",
    "Set explicitly selected PhysicsScene gravity and time step while stopped. No implicit scene creation or GPU/backend switch.",
    dict(
        **TARGET,
        path=field("string", "PhysicsScene prim path."),
        gravity=field(
            "array",
            "Acceleration vector in stage units/s^2.",
            False,
            default=[0, 0, -9.81],
        ),
        dt=field(
            "number", "Physics timestep seconds, 1/1000..1/10.", False, default=1 / 60
        ),
    ),
)
add(
    "sim.joints.inspect",
    "Inspect bounded authored joint limits/drives below an articulation, reporting USD degrees versus stage units. No runtime-state claim.",
    dict(
        **TARGET,
        path=field("string", "Subtree or articulation root."),
        limit=field("integer", "Visited prim cap 1..200.", False, default=200),
    ),
)
add(
    "sim.joints.drive",
    "Set one revolute/prismatic joint drive target and gains in session layer; stopped timeline only. Angular target radians; gains use USD degrees-based convention.",
    dict(
        **TARGET,
        path=field("string", "Revolute or prismatic joint path."),
        target=field("number", "Radians for angular joint, stage units for prismatic."),
        stiffness=field("number", "Nonnegative USD drive stiffness."),
        damping=field("number", "Nonnegative USD drive damping."),
        max_force=field("number", "Positive force/torque cap.", False, default=1000),
    ),
)
add(
    "sim.joints.state",
    "Read initialized articulation DOF positions/velocities on selected PhysX or qualified Newton tensor API. Angular radians, linear stage units; no authored-target fallback. PHYSICS_STATE_INVALID identifies invalid runtime joint data.",
    dict(**TARGET, path=field("string", "Exact articulation root path.")),
)
add(
    "sim.simulation.step",
    "Advance 1..120 exact steps on the selected PhysX or qualified Newton backend while PAUSED and initialized. No app-update pumping or engine fallback. Return measured time delta, runtime rigid-body poses and articulation DOFs; Newton publishes poses to Fabric. Failure after stepping starts reports observed clock/phase in step details; elapsed time does not prove valid physics. No automatic retry or rollback.",
    dict(
        **TARGET,
        steps=field("integer", "Number of physics steps 1..120."),
        observe_prims=field("array", "Up to 16 rigid body paths.", False, default=[]),
        observe_joints=field("array", "Up to 8 articulation paths.", False, default=[]),
    ),
)
add(
    "sim.camera.create",
    "Create a persistent RTX camera on a stopped timeline. Build identity and owned-CPU Replicator capabilities are checked; experimental.rtx is not required. USD axes: -Z forward, +Y up. Near/far clipping is 0.01/1000 meters, converted to stage units and returned. Needs rendered frames; width/height capped at 1024.",
    dict(
        **TARGET,
        path=field("string", "New camera path."),
        width=field("integer", "Image width 64..1024.", False, default=640),
        height=field("integer", "Image height 64..1024.", False, default=480),
        **POSE,
    ),
)
add(
    "sim.camera.capture",
    "Capture RTX camera RGB into an owned CPU buffer and a new PNG in configured output root. Default waits up to five seconds for new render completions AND a matching renderer CPU transfer; no stale annotator fallback. Returns dimensions/hash. No implicit Play or atomic physics-frame synchronization.",
    dict(
        **TARGET,
        path=field("string", "Camera created by this pack."),
        filename=field("string", "New simple basename ending .png."),
    ),
)
add(
    "sim.lidar.create",
    "Create generic RTX LiDAR while stopped using qualified installed 6.x APIs. Requires a session launched with enable_motion_bvh=true (run.sh --motion-bvh). No hardware preset promise. At most eight sensors; delete an exact owned sensor path to release resources.",
    dict(**TARGET, path=field("string", "Unused LiDAR prim path."), **POSE),
)
add(
    "sim.lidar.read",
    "Read CPU Cartesian LiDAR return points in sensor coordinates with sensor-to-world transform, filter invalid ranges, cap at 256 samples. Requires initialized sensor and rendered simulation frames.",
    dict(
        **TARGET,
        path=field("string", "Pack-created LiDAR path."),
        limit=field("integer", "Maximum returned points 1..256.", False, default=64),
    ),
)
add(
    "sim.ros2.tf_setup",
    "Build one ROS 2 graph for a robot, the same way every time: /clock (sim time), /joint_states, the static world->base_link (or world->odom + odometry for a mobile base) and the robot's link TF from Isaac. Rebuilds its own graph on each call, so no frame is ever published twice by this tool; reuses an existing /clock and reports other ROS 2 publishers in the scene. Timeline must be stopped; publishes while playing. Then use scripts/ros2_rviz_config.py and scripts/ros2_tf_doctor.py (docs/ROS2-RVIZ.md).",
    dict(
        **TARGET,
        robot=field("string", "Robot prim at or above its articulation root."),
        base_link=field("string", "Robot root link prim; default: a link named base_link, base_footprint or base.", False, default=""),
        base_frame=field("string", "TF name for the base link; default: the base_link prim name.", False, default=""),
        base=field("string", "fixed (arm on a table: static world->base) or mobile (odometry: world->odom->base).", False, default="fixed"),
        world_frame=field("string", "Root frame for RViz's Fixed Frame, e.g. world or map. Empty: publish no world link (a localization node provides map->odom).", False, default="world"),
        link_frames=field("string", "isaac: Isaac publishes every link frame. robot_state_publisher: Isaac publishes only /joint_states and the base link; run robot_state_publisher with the URDF. Never both.", False, default="isaac"),
        extra_frames=field("array", "Up to 16 more prims (cameras, LiDARs) to publish relative to the base link; frame name = prim name.", False, default=[]),
        namespace=field("string", "ROS node namespace prepended to the topics.", False, default=""),
        joint_states_topic=field("string", "Joint state topic.", False, default="joint_states"),
        clock=field("string", "auto: reuse an existing /clock, else publish one shared clock for all robots (two clocks make RViz reset constantly); publish: always keep the tool's clock; skip: never create one.", False, default="auto", enum=["auto", "publish", "skip"]),
    ),
)
add(
    "sim.graph.node_types",
    "Find OmniGraph node types in the running Isaac Sim by id or UI-name substring (e.g. 'Make Array' -> omni.graph.nodes.ConstructArray, 'PublishClock'), with exact input/output port names, types, defaults and descriptions from the installed .ogn files. Use before building Action Graphs; port names differ between Isaac versions.",
    dict(
        **TARGET,
        contains=field("string", "Substring of the node type id or its UI name, case-insensitive (<=64 chars)."),
        limit=field("integer", "Maximum node types returned, 1..40.", False, default=20),
        ports=field("boolean", "Include ports (default: only when 5 or fewer types match; at most 8 detailed).", False),
    ),
)
add(
    "sim.graph.build",
    "Create a whole OmniGraph (e.g. an Action Graph for ROS 2) in one call: nodes, input values, connections and prim targets. Checks every port against the created nodes and lists the real ports on a typo; creates Make Array-style inputs (inputs:input1, ...) itself; stores a token's value when given its key (Scale To/From Stage Units: 'toStage' -> 'Convert to stage units'); sets prim targets (e.g. an Articulation Controller's inputs:targetPrim) as USD relationships. Timeline stopped. Rolls the graph back on any error; returns it as sim__graph__inspect does. Find node types and ports first with sim__graph__node_types.",
    dict(
        **TARGET,
        path=field("string", "Graph prim path to create, e.g. /World/ROS_Clock or /turtlebot3_burger/ROS_Drive."),
        nodes=field("array", "[name, node_type] pairs, e.g. [[\"tick\", \"omni.graph.action.OnPlaybackTick\"], [\"clock\", \"isaacsim.ros2.bridge.ROS2PublishClock\"]]."),
        values=field("object", "Input values: {\"node.inputs:port\": value}, e.g. {\"clock.inputs:topicName\": \"/clock\"}.", False),
        connections=field("array", "[from, to] pairs: [[\"tick.outputs:tick\", \"clock.inputs:execIn\"]]. A constant node's inputs:value can be a source.", False),
        targets=field("object", "Prim targets: {\"node.inputs:targetPrim\": [\"/World/robot\"]}.", False),
        evaluator=field("string", "Graph evaluator: execution (Action Graph, default), push or dirty_push.", False, default="execution", enum=["execution", "push", "dirty_push"]),
        replace=field("boolean", "Delete an existing graph at path first (it is not restored if the new build fails).", False, default=False),
    ),
)
add(
    "sim.graph.inspect",
    "Read an OmniGraph: every node's type, input values or upstream connections, and output connections. Use to verify a hand-built Action Graph (missing wires, unset targets, ports created at runtime). Read-only.",
    dict(
        **TARGET,
        path=field("string", "Graph prim path, e.g. /World/ROS_Clock."),
        limit=field("integer", "Maximum nodes returned, 1..128.", False, default=64),
    ),
)
add(
    "sim.graph.show",
    "Open a graph in the Action Graph editor of a windowed Isaac Sim and frame its nodes, e.g. to show graphs in a demo video (then screenshot or start_screen_recording). layout=true expands the nodes (port names readable) and auto-arranges them, writing both into the stage. Uses the editor's internal API; refuses cleanly if it changed.",
    dict(
        **TARGET,
        path=field("string", "Graph prim path."),
        layout=field("boolean", "Auto-arrange the nodes first (writes node positions).", False, default=False),
        width=field("integer", "Undock the editor window and size it (pixels); 0 keeps the docked layout.", False, default=0),
        height=field("integer", "Window height in pixels when width is set.", False, default=0),
    ),
)
add(
    "sim.ui.find",
    "List Isaac Sim's own UI widgets (buttons, fields, checkboxes, combo boxes, tree views, and labels when a query is given) with their text, tooltip, current value, a ref, and their centre in pixels of the Isaac Sim window (the same pixels as screenshot(window_id=<Isaac Sim window>)), plus the visible Kit windows. Works while the desktop is locked or used remotely. Use the ref with sim__ui__act instead of guessing coordinates.",
    dict(
        query=field("string", "Case-insensitive text to match in a widget's name, text, tooltip, identifier or type, e.g. 'play' or 'Add'. Empty lists interactive widgets only.", False, default=""),
        window=field("string", "Only Kit windows whose title contains this text, e.g. 'Stage' or 'Property'.", False, default=""),
        limit=field("integer", "Maximum widgets returned, 1..100 (the reply is also capped near 48 KB).", False, default=40),
    ),
)
add(
    "sim.ui.act",
    "Drive Isaac Sim's own UI through Kit's input system, not the desktop: click, double_click, right_click, move, drag, scroll, type, keypress, menu, tab, or wait. Target a widget by ref from sim__ui__find or by x, y window pixels. Works while the desktop is locked or used remotely (desktop window_* input does not). Then check the result with screenshot(window_id=<Isaac Sim window>) or sim__ui__find. UI actions can change or delete scene data and files like a person clicking would.",
    dict(
        action=field("string", "What to do. tab brings a docked window to the front, like clicking its tab (sim__ui__find lists hidden_tabs).", enum=["click", "double_click", "right_click", "move", "drag", "scroll", "type", "keypress", "menu", "tab", "wait"]),
        ref=field("string", "Widget ref from sim__ui__find (its centre is used). For type/keypress it is clicked first to focus it.", False),
        x=field("integer", "X in Isaac Sim window pixels (when no ref).", False),
        y=field("integer", "Y in Isaac Sim window pixels (when no ref).", False),
        end_x=field("integer", "Drag end X in window pixels.", False),
        end_y=field("integer", "Drag end Y in window pixels.", False),
        text=field("string", "Text for action=type (1..500 characters, no newlines; send enter with keypress).", False),
        keys=field("array", "For action=keypress: one key plus optional modifiers, e.g. [\"ctrl\", \"s\"], [\"enter\"], [\"shift\", \"tab\"].", False),
        window=field("string", "For action=tab: the window or tab title, e.g. 'Console' or 'Content'.", False),
        menu=field("string", "For action=menu: a menu path, e.g. 'Window/Graph Editors/Action Graph' or 'Create/Mesh/Cube'.", False),
        scroll_x=field("integer", "Horizontal wheel steps for action=scroll (-20..20).", False, default=0),
        scroll_y=field("integer", "Vertical wheel steps for action=scroll (-20..20; negative scrolls down).", False, default=0),
        frames=field("integer", "Frames to wait after the action so the UI updates (0..600).", False, default=3),
    ),
)
add(
    "sim.map.occupancy",
    "Generate a 2D occupancy map from the stage's colliders (Isaac's occupancy-map generator) and write a Nav2 map_server PGM + YAML (trinary, origin = lower-left corner, +x right, +y up) to the configured output root. Start with the timeline stopped: the tool plays 8 frames through the guarded timeline path (the generator needs running physics; OnPlaybackTick graphs such as /clock run meanwhile), then stops, so robots return to their start poses. Use when a lab or project does not provide map files.",
    dict(
        **TARGET,
        min=field("array", "World [x, y] lower corner of the map in metres."),
        max=field("array", "World [x, y] upper corner of the map in metres."),
        z=field("array", "World [z_min, z_max] in metres; Isaac's generator samples one cell-thick layer at the middle of this range, so centre it on the lidar height.", False, default=[0.05, 0.3]),
        clear=field("array", "Up to 16 [x, y, radius] circles (metres) to mark free, e.g. the robot's own footprint at its start pose.", False, default=[]),
        start=field("array", "World [x, y] in free space the generator flood-fills from; cells it cannot reach are unknown. Default: the centre of the bounds (move it if a table or pillar is there).", False),
        cell_size=field("number", "Cell size in metres (map resolution).", False, default=0.05),
        basename=field("string", "Output file stem (letters, digits, _ -); writes <basename>.pgm and .yaml.", False, default="map"),
    ),
)
add(
    "sim.logs.tail",
    "Read bounded tail of selected Kit log, not arbitrary files. Returns warnings/errors and source path, without clearing shared logs.",
    dict(**TARGET, limit=field("integer", "Maximum lines 1..100.", False, default=50)),
)
add(
    "sim.controller.create",
    "OPT-IN CODE EXECUTION: create OnPlaybackTick -> ScriptNode controller using inline Python. Requires ISAAC_MCP_ENABLE_SCENE_SCRIPTS=1 in Kit launch; NOT a sandbox.",
    dict(
        **TARGET,
        path=field("string", "Unused graph path."),
        code=field(
            "string", "Trusted setup(db)/compute(db) script, <=16384 characters."
        ),
    ),
)
add(
    "sim.controller.update",
    "OPT-IN CODE EXECUTION: replace inline code of this pack's controller and request recompilation while stopped. Same explicit script enable required.",
    dict(
        **TARGET,
        path=field("string", "Pack controller path."),
        code=field("string", "Trusted replacement script <=16384 characters."),
    ),
)

add(
    "sim.joints.command",
    "Command initialized articulation position targets on selected PhysX or qualified Newton backend in DOF order. Radians for revolute, stage units for prismatic; validates runtime limits. Returns target acknowledgment, not achieved pose.",
    dict(
        **TARGET,
        path=field("string", "Exact initialized articulation root path."),
        positions=field(
            "array", "One finite position per DOF, in sim.joints.state order."
        ),
    ),
)
add(
    "assets.import_urdf",
    "Convert a trusted approved relative URDF project using the installed importer. Select the shared Physics=physics variant on private generated output when present; refuse missing bodies or expected revolute/prismatic joints before snapshot/import. Returns authored counts, not runtime readiness. No source overwrite. Rejects DTD/entities, package/URL/absolute mesh paths and missing files. Requires 6.x URDFImporter API.",
    dict(
        **TARGET, **ASSET, prim_path=field("string", "New wrapper prim path."), **POSE
    ),
)

# Extend existing operations rather than proliferating overlapping names.
REGISTRY["sim.prim.create"]["parameters"]["collision"] = field(
    "boolean",
    "Author static collision without rigid-body dynamics.",
    False,
    default=False,
)
REGISTRY["sim.prim.create"]["parameters"]["scale"] = field(
    "array", "Positive local XYZ scale, 1e-6..1e4.", False, default=[1, 1, 1]
)
REGISTRY["sim.prim.set_transform"]["parameters"]["scale"] = field(
    "array", "Positive XYZ scale in the requested pose frame.", False, default=[1, 1, 1]
)
REGISTRY["sim.camera.create"]["parameters"]["target"] = field(
    "array",
    "Optional world look-at point; requires frame=world and explicit position. Overrides orientation.",
    False,
)
REGISTRY["sim.material.create"]["parameters"].update(
    kind=field("string", "pbr or physics.", False, default="pbr"),
    static_friction=field(
        "number", "Physics material static friction 0..10.", False, default=0.5
    ),
    dynamic_friction=field(
        "number", "Physics material dynamic friction 0..10.", False, default=0.5
    ),
    restitution=field("number", "Physics material restitution 0..1.", False, default=0),
)
REGISTRY["sim.prim.create"][
    "description"
] += " Also supports Cylinder, Capsule and Cone; size is diameter/height before optional scale."

REGISTRY["sim.camera.capture"]["parameters"]["wait_for_render"] = field(
    "boolean",
    "True: wait up to five seconds for render completions and a new owned CPU transfer. False: reuse the last validated tool acquisition, possibly from before Stop/reset or scene edits; NOT_READY until a true capture seeds it. Ordinary Play never refreshes this cache. PNG I/O still occurs. No implicit physics advance or atomic sensor/physics guarantee.",
    False,
    default=True,
)
REGISTRY["sim.lidar.read"]["parameters"]["wait_for_render"] = field(
    "boolean",
    "Wait up to five seconds for one render; acquisition requires Play. False reads the latest completed scan, possibly stale. No implicit physics advance or atomic sensor/physics guarantee.",
    False,
    default=False,
)

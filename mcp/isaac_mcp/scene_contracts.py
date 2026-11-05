"""Versioned public contracts; stdlib only, shared by host catalog and Kit pack."""


def field(kind, description, required=True, **kw):
    return dict(type=kind, description=description, required=required, **kw)


TARGET = dict(
    session_id=field(
        "string", "Exact session_id from sim__stage__info (call it first; process incarnation)."
    ),
    stage_id=field(
        "string", "Exact stage_id from sim__stage__info (call it first); stale stages are refused."
    ),
)
# For reads that do not depend on the stage. Older callers still send IDs; refusing them as
# unknown arguments would break those calls for no benefit.
UNUSED_TARGET = dict(
    session_id=field("string", "Not needed; accepted and ignored.", False),
    stage_id=field("string", "Not needed; accepted and ignored.", False),
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
    "Bounded filename-only USD and URDF discovery on the MCP host. No USD parser. Results are untrusted data. Folders deeper than eight levels are skipped and named in skipped_deep; the rest of the root is still scanned.",
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
    relative_path=field("string", "Relative path of the USD file within that root."),
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
    "Import a trusted local USD by absolute path, under an approved project root or in a pack of the user's asset store. Copies and validates its project dependencies before authoring a reference; source files remain unchanged.",
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
    "Open a USD file (.usd/.usda/.usdc/.usdz, under your home or this simulator's output folder) as the current stage, like File > Open, with the timeline stopped. Refuses to drop unsaved edits unless discard_changes=true. Reports stale saved LiDAR render products and never changes the file. Returns the new session_id and stage_id for later sim__* calls; run_python sessions reset.",
    dict(
        **TARGET,
        path=field("string", "Absolute path of the USD file to open, under your home or this simulator's output folder, where sim__stage__save writes by default (a leading tilde for your home is expanded)."),
        discard_changes=field("boolean", "Open even if the current stage has unsaved edits (they are lost).", False, default=False),
    ),
)
add(
    "sim.quick_start",
    "One call that builds a working scene for anyone, with nothing to supply and no IDs: a ground plane, physics scene, lights and a camera aimed at the scene, in the current stage if it is new and empty, otherwise a new one. No real-robot wiring. Edits go to the session layer, and sim__stage__save keeps them. The timeline stays stopped unless play=true. Refuses to drop unsaved edits unless discard_changes=true. Returns new session_id and stage_id, paths, asset sources and next steps; a wheeled robot adds robot.cmd_vel_graph. For the RISE lab UR5e digital twin, use ur5e_dt__quick_start.",
    dict(
        robot=field("string", "Optional robot: a public name, <pack>/<file.usd> or <pack>/<file.urdf> from a pack in your asset folder, or an absolute path under an approved root (as for assets__import_path and assets__import_urdf). Names: ur5e (your local copy first, then NVIDIA, honoring strict mode); franka, turtlebot3_burger, carter_v1, nova_carter, jetbot, create3 (from this simulator's own assets root). A file found only through a project file's asset_path is refused.", False),
        fixed_base=field("boolean", "With a URDF robot: fix its base to the world. Pass false for a robot that drives or walks. A USD robot keeps what its file says.", False, default=True),
        ros=field("boolean", "With a robot: also run what sim__ros2__tf_setup runs, and report the shell line that matches this simulator.", False, default=False),
        up_axis=field("string", "The stage's up axis. The ground, gravity and camera follow it. A robot file must match, or the call refuses with the values to pass. A URDF is always Z up in metres.", False, default="Z", enum=["Z", "Y"]),
        meters_per_unit=field("number", "The stage's units, as a positive number (0.01 for centimetres). Ground size, gravity, camera distance and framing scale with it. A robot file must match.", False, default=1.0),
        play=field("boolean", "Start the timeline after building.", False, default=False),
        discard_changes=field("boolean", "Replace a stage with unsaved edits (they are lost).", False, default=False),
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
    "Bounded search of prim paths: `contains` is a literal substring (not a regex), `type_name` an exact type, `root` limits it to one subtree. Includes inactive prims.",
    dict(
        **TARGET,
        contains=field(
            "string", "Literal path substring <=128 characters.", False, default=""
        ),
        type_name=field("string", "Exact USD type or empty.", False, default=""),
        limit=field("integer", "Maximum matches 1..200.", False, default=100),
        root=field("string", "Only search under this prim, such as a robot's path. Default: the whole stage.", False, default="/"),
    ),
)
add(
    "sim.prim.bounds",
    "World-space bounds of one prim at default USD time from UsdGeom.BBoxCache.ComputeWorldBound: axis-aligned min/max, centre and size, plus the oriented box's world corners. purpose selects default, render, proxy or guide geometry. Stage units; empty says the prim has no boundable content. Authored USD, not a runtime physics observation.",
    dict(
        **TARGET,
        path=field("string", "Absolute prim path."),
        purpose=field(
            "string",
            "Geometry purpose whose bounds to measure.",
            False,
            default="default",
            enum=["default", "render", "proxy", "guide"],
        ),
    ),
)
add(
    "sim.prim.measure",
    "Distance between two prims' world bounds at default USD time: centre-to-centre and the shortest gap (0 when the axis-aligned bounds overlap), in stage units, with each prim's axis-aligned bound. Refuses when either prim has no bounds, naming it and pointing to sim__prim__bounds. Authored USD, not runtime physics.",
    dict(
        **TARGET,
        path_a=field("string", "First absolute prim path."),
        path_b=field("string", "Second absolute prim path."),
    ),
)
add(
    "sim.camera.list",
    "List Camera prims under root with focal length and aperture (mm), clipping range (stage units and metres), projection, world pose and whether each is the active viewport camera. Bounded by limit and a 10,000-prim visit cap; truncated says which. No viewport (headless) reports viewport_available=false instead of guessing. Authored USD, not a rendered image.",
    dict(
        **TARGET,
        root=field("string", "Absolute subtree root.", False, default="/"),
        limit=field("integer", "Maximum cameras 1..100.", False, default=100),
    ),
)
add(
    "sim.prim.create",
    "Create a Cube, Sphere, Cylinder, Capsule, Cone, Plane, Xform or PhysicsScene in the session layer. No arbitrary references/attributes. Stopped timeline, existing parent and no conflict required. Dimensions are stage units; optional mass or density authors PhysicsMassAPI on geometry. Undo a created prim with sim__prim__delete; sim__prim__revert undoes the attributes.",
    dict(
        **TARGET,
        path=field("string", "New absolute prim path; every ancestor must already exist (create them with kind=Xform)."),
        kind=field(
            "string", "Cube, Sphere, Cylinder, Capsule, Cone, Plane, Xform or PhysicsScene."
        ),
        size=field(
            "number",
            "Positive cube edge, sphere/cylinder/capsule/cone diameter or height, or Plane width and length.",
            False,
            default=1,
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
    "Set a static local or world pose on any Xformable prim in the session layer, and report the pose before and after; undo with sim__prim__revert. Refuses instance proxies (name the instance root), articulation links other than the root (moving a link breaks its joints), animated transforms, Kit runtime prims and a running or paused timeline. Authors one transform override, so source files never change. No claim of runtime teleportation.",
    dict(**TARGET, path=field("string", "Absolute Xformable prim path."), **POSE),
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
    "sim.physics.raycast",
    "Cast one ray into the PhysX scene: hits with position, normal, distance, collider and rigid-body paths. all=false returns the closest; all=true returns up to 32 sorted by distance. max_distance 0..100 stage units; direction is normalised and the result says so. scene_ready=false before physics initialises. Newton refuses RAYCAST_UNSUPPORTED. No app update is pumped.",
    dict(
        **TARGET,
        origin=field("array", "Ray origin [x, y, z] in world stage units."),
        direction=field("array", "Ray direction [x, y, z]; normalised before use."),
        max_distance=field("number", "Maximum distance in stage units, >0 and <=100.", False, default=100),
        all=field("boolean", "Return every hit up to 32 instead of only the closest.", False, default=False),
    ),
)
add(
    "sim.physics.contacts",
    "Read current PhysX contact pairs and impulses for up to 32 bodies from the last delivered contact report. Subscribes once on first use or Play; a call before any report says so. Only colliders with PhysxContactReportAPI report; paths without it are listed with the fix, and a call where none report refuses CONTACT_REPORT_DISABLED. Needs a playing or paused initialized timeline. Newton refuses CONTACT_QUERY_UNSUPPORTED. No app update is pumped.",
    dict(
        **TARGET,
        paths=field("array", "1..32 absolute rigid-body or collider paths."),
    ),
)
add(
    "sim.body.state",
    "Read initialized rigid-body world pose on selected PhysX or qualified Newton backend; fails when stopped/uninitialized or not a body. Position in stage units, quaternion [w,x,y,z]. Optional runtime velocities/mass from tensor API; no authored-USD fallback. PHYSICS_STATE_INVALID identifies invalid observed data, not bad caller input.",
    dict(
        **TARGET,
        path=field("string", "Absolute rigid body path; prim and prim_path are aliases. Supply one.", False),
        prim=field("string", "Alias for path.", False),
        prim_path=field("string", "Alias for path.", False),
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
    "Explicit play, pause or stop. Play keeps running between your calls, not always at real time; use sim__simulation__step for an exact duration. Stop rewinds physics and awaits pending render/writer and capture-settings completion; its five-second timeout is cooperative. Play only observes a pending Stop; TIMELINE_BUSY or TIMELINE_INTERRUPTED requires inspection and explicit retry, never automatic replay. Leases the session edit target for physics writeback until Stop. Never pumps the Kit update loop.",
    dict(**TARGET, action=field("string", "play, pause, or stop.")),
)
add(
    "sim.stage.save",
    "Export a flattened copy, including session edits, to a new basename (.usda added if absent) in the output root or in `directory` under your home. Never overwrites or switches the current stage. External texture paths remain dependencies; process-bound render pipelines are omitted, and unknown native pipeline content refuses.",
    dict(
        **TARGET,
        filename=field("string", "New basename, e.g. scene or scene.usda; no other extension, no folders."),
        directory=field("string", "Existing folder under your home to write into instead of the output root (absolute path). Never overwrites.", False),
        reset_camera_render_handles=field(
            "boolean",
            "Explicitly remove validated session-only ROS camera and LiDAR viewportHandle fields from the copy so native textures can rebuild after restart. This tool's own capture products need no flag. Default false refuses affected saves; true still refuses authored, ambiguous, connected or unavailable bindings. Originals and live stage are unchanged; omitted property paths are returned. This is export policy, not proof of authorship.",
            False,
            default=False,
        ),
    ),
)
add(
    "sim.viewport.frame",
    "Point the active viewport camera, e.g. before a screenshot. path alone frames a prim; path+margin frames its bounds plus margin metres; eye+target places the camera exactly; follow tracks the prim. Edits only the active camera in the session layer, with no undo entry. framed_path is the prim this call framed; the Stage selection is put back afterwards, so the Stage and Property panels keep their own selection. Refuses look-through targets and empty bounds, listing candidates. Does not wait for rendered pixels.",
    dict(
        **TARGET,
        path=field("string", "Absolute prim path to frame or follow.", False, default=""),
        margin=field("number", "Extra radius in metres around the prim's bounding sphere (0..1000), e.g. 1.0 for a small robot that will drive around.", False),
        eye=field("array", "Camera position [x, y, z] in world coordinates (stage units).", False),
        target=field("array", "Point to look at [x, y, z] in world coordinates (stage units); default: the prim's centre.", False),
        follow=field("boolean", "true: follow path while playing (until stopped); false: stop following.", False),
    ),
)

DISABLED = {
    "isolated_untrusted": "Disabled: no verified whole-lifetime GPU Kit filesystem/network/resolver/plugin/script enforcement. No fallback to trusted.",
    "remote_upload": "Disabled: authenticated distinct-host transport and authorization have not been validated. Use an explicitly configured shared mount.",
    "presets": "Named manifests are not implemented; select discovered files directly.",
}

add(
    "assets.search",
    "Fresh filename search across approved project/library roots. No network discovery, semantic service, manifest or fixed robot count. Refresh by calling again. Folders deeper than eight levels are skipped and named in skipped_deep.",
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
# These run in the MCP host and work without Kit. Kit must not serve them.
HOST_ONLY = {
    "assets.roots",
    "assets.scan",
    "assets.search",
    "assets.packs",
    "assets.pack_doctor",
    "assets.pack_adopt",
    "assets.pack_pin",
    "assets.pack_build",
}
add(
    "assets.packs",
    "List the asset packs in every store, the selected pack and where that choice came from. Host side, no Kit, no USD parser. Writes nothing. Kit reads its own settings: give host and Kit the same ISAAC_MCP_ASSET_PATH, or Kit will not see these packs. With a Kit connected it also lists kit_environment and warns when Kit and this host use different project files, stores or strict mode.",
)
add(
    "assets.pack_doctor",
    "Check a pack without changing it: corrupt, missing and unpinned files, then the dependencies of its entry files (unresolved, unreadable, remote, outside the pack). A file accepted at build time with allow_unresolved is still missing, so it is reported and ok is false until the file is added: assets__import_path refuses it for the same reason. Unused files are information. Parses USD in a host helper (ISAAC_MCP_USD_PYTHON) or else the selected Kit; says so if neither ran, and ok is then false. A URDF's own references are read on the host.",
    dict(
        pack=field("string", "Pack name. Default: the selected pack.", False),
        entries=field(
            "array",
            "Files in the pack that are its roots. Default: recorded in the pack, or the twin's known names for ur5e-dt.",
            False,
        ),
    ),
)
add(
    "assets.dependencies",
    "Compute dependency closures for files under an approved root or a user asset store. USD uses UsdUtils.ComputeAllDependencies through a host helper (ISAAC_MCP_USD_PYTHON) or else the selected Kit; native USD parsing is NOT sandboxed. A .urdf is read on the host with the build's package rules and returns its meshes, textures and package.xml, so a missing package is reported instead of a parser error. Copies nothing.",
    dict(paths=field("array", "Absolute USD or URDF file paths, 1 to 64.")),
)
add(
    "assets.pack_adopt",
    "Create a pack in the primary asset folder, or copy a folder's asset files into it and pin them. The source must be under a user-approved root; no tool can approve folders. A symlink is never followed: a named one is refused, and one met while copying a folder is listed in skipped_symlinks with a note. A different file already there is refused unless force=true; nothing is written inside the repository.",
    dict(
        pack=field("string", "Pack name: one folder name, no path separators."),
        source_path=field(
            "string", "Absolute folder under an approved root. Omit to create an empty pack.", False
        ),
        include=field("array", "Extra file extensions to copy.", False),
        files=field("array", "Copy only these files or folders, relative to source_path. A named file is copied whatever its type.", False),
        entries=field("array", "Root USD files, relative to the pack, for assets__pack_doctor. Default: a sole top-level USD.", False),
        dry_run=field("boolean", "Report the plan and write nothing.", False, default=False),
        force=field("boolean", "Replace files that differ from the source.", False, default=False),
    ),
)
add(
    "assets.pack_pin",
    "Record the current SHA-256 of a pack's files in its SHA256SUMS, in the primary asset folder. New files are pinned; a pin that would change accepts whatever bytes are there, so it is refused with before and after hashes unless the file is named in files or force=true.",
    dict(
        pack=field("string", "Pack name. Default: the selected pack.", False),
        files=field("array", "Pin only these relative paths, accepting their changes.", False),
        force=field("boolean", "Accept every changed and removed file.", False, default=False),
    ),
)
add(
    "assets.pack_build",
    "Copy USD or URDF files and only what they depend on into a pack in the primary asset folder, keeping relative layout, and pin them. A URDF brings its meshes, their textures and any package.xml it needs. Sources must be under an approved root or a user asset store; nothing outside is copied. Unresolved or remote references refuse the build unless allow_unresolved=true, which records them; such a pack still will not import until the files are added, so the result says importable=false with import_blockers. Files that differ need force=true. Checks the new pack afterwards.",
    dict(
        pack=field("string", "Destination pack name: one folder name."),
        sources=field(
            "array",
            "USD or URDF files, 1 to 64: absolute paths, or paths inside from_pack. A USD scene outside from_pack and layout_root is read for its references and not copied.",
        ),
        from_pack=field(
            "string",
            "Pack that source names and logical references such as robot/ur5e.usd come from.",
            False,
        ),
        layout_root=field(
            "string",
            "Absolute folder the pack layout is relative to. Default: from_pack; for a USD the source's folder; for a URDF the approved root or pack holding it, which is also where its ROS packages are looked for.",
            False,
        ),
        allow_unresolved=field(
            "boolean", "Build despite missing or remote references, and record them.", False, default=False
        ),
        dry_run=field("boolean", "Report the closure and write nothing.", False, default=False),
        force=field("boolean", "Replace files that differ from the source.", False, default=False),
    ),
)
add(
    "sim.prim.delete",
    "Delete one pack-owned subtree from the session layer only. Requires stopped physics and confirm=true; exact owned sensors release their resources; sensor-containing subtrees and articulation roots are refused. Prims from an opened file are not pack-owned, even ones you made earlier: allow_foreign=true deactivates such a prim in the session layer instead (USD cannot remove another layer's opinions from there). It stays composed as inactive and keeps its path; a sim__stage__save copy omits it; source files never change.",
    dict(
        **TARGET,
        path=field("string", "Pack-owned root path, or any prim with allow_foreign=true."),
        confirm=field("boolean", "Explicit confirmation; false refuses."),
        allow_foreign=field("boolean", "Also act on a prim these tools did not create in this session: deactivate it in the session layer (or remove it if all its opinions are session-layer ones). World/default prim, articulation and other protections still apply. Source files are unchanged.", False, default=False),
    ),
)
add(
    "sim.prim.clone",
    "Clone a pack-created simple leaf primitive into a new path, preserving session-layer attributes. Refuses references, children, sensors and articulations.",
    dict(
        **TARGET,
        path=field("string", "Pack-created source leaf."),
        new_path=field("string", "New path, not in use yet."),
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
    "Bind a pack-created material to an existing geometry prim in the session layer, preserving source files. With paths (up to 128 prims) every prim is checked first, so a bad one binds nothing.",
    dict(
        **TARGET,
        path=field("string", "Geometry prim. Give this or paths.", False),
        material_path=field("string", "Pack-created Material path."),
        paths=field("array", "Up to 128 geometry prims to bind the same material to in one call, instead of path.", False),
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
    "Inspect bounded authored joint limits/drives of an articulation, reporting USD degrees versus stage units. No runtime-state claim. The path may be the articulation root (a joint such as <robot>/root_joint, or the link carrying the articulation API): the joints are then the ones reached through its rigid bodies.",
    dict(
        **TARGET,
        path=field("string", "Subtree, articulation root (joint or link), or the robot prim."),
        limit=field("integer", "Visited prim cap 1..200.", False, default=200),
    ),
)
add(
    "sim.joints.drive",
    "Set one revolute/prismatic joint drive target and gains in session layer; stopped timeline only. Angular target radians; gains use USD degrees-based convention.",
    dict(
        **TARGET,
        path=field("string", "Revolute or prismatic joint prim, e.g. /World/UR5e/joints/elbow_joint; sim__joints__inspect lists them."),
        target=field("number", "Radians for angular joint, stage units for prismatic."),
        stiffness=field("number", "Nonnegative USD drive stiffness. Too low and a gravity-loaded joint settles short of its target."),
        damping=field("number", "Nonnegative USD drive damping."),
        max_force=field("number", "Positive force/torque cap.", False, default=1000),
    ),
)
add(
    "sim.joints.state",
    "While playing or paused (stopped gives PHYSICS_NOT_READY), read initialized articulation DOF positions/velocities on selected PhysX or qualified Newton tensor API. Angular radians, linear stage units; no authored-target fallback. PHYSICS_STATE_INVALID identifies invalid runtime joint data.",
    dict(**TARGET, path=field("string", "Exact articulation root path, e.g. /World/UR5e/root_joint: robot.articulation_root from sim__quick_start, or sim__physics__inspect.")),
)
add(
    "sim.simulation.step",
    "Advance 1..120 exact steps on the selected PhysX or qualified Newton backend while PAUSED and initialized: from a stopped timeline, sim__timeline__control action=play and then action=pause. No app-update pumping or engine fallback. Return measured time delta, plus runtime poses for observe_prims and DOFs for observe_joints; Newton publishes poses to Fabric. Failure after stepping starts reports observed clock/phase in step details; elapsed time does not prove valid physics. No automatic retry or rollback.",
    dict(
        **TARGET,
        steps=field("integer", "Number of physics steps 1..120."),
        observe_prims=field("array", "Up to 16 rigid body paths.", False, default=[]),
        observe_joints=field("array", "Up to 8 articulation paths.", False, default=[]),
    ),
)
add(
    "sim.camera.create",
    "Create a persistent RTX camera on a stopped timeline, e.g. on a robot link: path=/robot/base_footprint/Camera_1, position=[0.1,0,0.2] (local), rotate_xyz=[90,0,-90] (looks along the parent's +X), focal_length=18, clipping_range=[0.01,100]. USD axes: -Z forward, +Y up. Clipping is in metres (default 0.01..1000), converted to stage units. Returns optics, FOV and the local/world pose. Needs rendered frames; width/height capped at 1024. A USD camera has no tick rate; set ROS rates on the camera helper.",
    dict(
        **TARGET,
        path=field("string", "New camera path; its parent prim (e.g. a robot link) must exist."),
        width=field("integer", "Image width 64..1024.", False, default=640),
        height=field("integer", "Image height 64..1024.", False, default=480),
        **POSE,
        rotate_xyz=field("array", "Orientation as USD rotateXYZ Euler angles in degrees (X, then Y, then Z), instead of the orientation quaternion. With frame=local it is authored as translate + rotateXYZ ops.", False),
        focal_length=field("number", "Focal length in mm (0.1..1000); default USD 50.", False),
        horizontal_aperture=field("number", "Horizontal aperture in mm (0.1..1000); default USD 20.955. The vertical aperture follows width/height.", False),
        clipping_range=field("array", "[near, far] clipping distances in metres.", False, default=[0.01, 1000.0]),
    ),
)
add(
    "sim.camera.capture",
    "Capture RTX camera RGB into an owned CPU buffer and a new PNG in configured output root. Default waits up to five seconds for new render completions AND a matching renderer CPU transfer; no stale annotator fallback. Returns dimensions/hash. No implicit Play or atomic physics-frame synchronization. For a camera from an opened file, pass adopt=true.",
    dict(
        **TARGET,
        path=field("string", "Camera from sim__camera__create or sim__quick_start, or any other Camera with adopt=true."),
        filename=field("string", "New simple basename ending .png."),
        adopt=field("boolean", "If this exact prim is a Camera with no render resources in this session (e.g. from an opened file), create them here. They count toward the eight sensors and are released on stage change; the prim itself is not edited and stays non-owned.", False, default=False),
    ),
)
add(
    "sim.lidar.create",
    "Create an RTX LiDAR (OmniLidar) while stopped: NVIDIA's named config (e.g. config=Example_Rotary_2D at /robot/base_scan/Lidar2D) or, without config, a local generic LiDAR. An unknown config lists the valid ones. Returns prim type, sensor path, rates and ros2_scan_graph: pass that to sim__graph__build to publish /scan. At most eight sensors; delete an exact owned sensor path to release resources.",
    dict(
        **TARGET,
        path=field("string", "Path for the new LiDAR, not in use yet; its parent prim (e.g. a robot link) must exist."),
        **POSE,
        rotate_xyz=field("array", "Orientation as USD rotateXYZ Euler angles in degrees, instead of the orientation quaternion.", False),
        config=field("string", "NVIDIA lidar config name, e.g. Example_Rotary_2D, Example_Rotary, OS1, RPLIDAR_S2E. Empty: local generic LiDAR.", False, default=""),
        variant=field("string", "Config variant, e.g. OS1_REV6_32ch10hz1024res (an invalid one lists the valid ones).", False, default=""),
        tick_rate=field("number", "Sensor tick rate in Hz (0..240]; default: the config's scan rate (10 Hz for Example_Rotary_2D). A rate other than the scan rate yields partial scans.", False),
    ),
)
add(
    "sim.lidar.read",
    "Read CPU Cartesian LiDAR return points in sensor coordinates with sensor-to-world transform, filter invalid ranges, cap at 256 samples. Requires initialized sensor and rendered simulation frames. For a LiDAR from an opened file, pass adopt=true.",
    dict(
        **TARGET,
        path=field("string", "Pack-created LiDAR path, or an existing OmniLidar with adopt=true."),
        limit=field("integer", "Maximum returned points 1..256.", False, default=64),
        adopt=field("boolean", "If this exact prim is an OmniLidar with no render resources in this session (e.g. from an opened file), create them here. They count toward the eight sensors and are released on stage change; settings a read needs are overridden in the session layer only and reported as session_overrides. The prim stays non-owned.", False, default=False),
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
        extra_frames=field("array", "Up to 16 more prims (cameras, LiDARs, tool0) to publish; frame name = prim name. One mounted on a robot link, or any with a fixed base, is published once as a static transform from its pose now.", False, default=[]),
        attach_to=field("string", "For a robot that is its own articulation mounted on another (RG2 on a UR5e wrist): the parent link's prim. Publishes parent->base_link dynamically on every tick instead of a static world link, so it follows the arm. Call this tool once per articulation, with a namespace for this one.", False, default=""),
        namespace=field("string", "ROS node namespace prepended to the topics.", False, default=""),
        joint_states_topic=field("string", "Joint state topic.", False, default="joint_states"),
        clock=field("string", "auto: reuse an existing /clock, else publish one shared clock for all robots (two clocks make RViz reset constantly); publish: always keep the tool's clock; skip: never create one.", False, default="auto", enum=["auto", "publish", "skip"]),
    ),
)
add(
    "sim.graph.node_types",
    "Find OmniGraph node types in the running Isaac Sim by id or UI-name substring (e.g. 'Make Array' -> omni.graph.nodes.ConstructArray, 'PublishClock'), with exact input/output port names, types, defaults and descriptions from the installed .ogn files. Use before building Action Graphs; port names differ between Isaac versions. Only contains is required; a catalog read needs no session_id or stage_id.",
    dict(
        **UNUSED_TARGET,
        contains=field("string", "Substring of the node type id or its UI name, case-insensitive (<=64 chars)."),
        limit=field("integer", "Maximum node types returned, 1..40.", False, default=20),
        ports=field("boolean", "Include ports (default: only when 5 or fewer types match; at most 8 detailed).", False),
    ),
)
add(
    "sim.graph.build",
    "Create a whole OmniGraph (e.g. an Action Graph for ROS 2) in one call: nodes, input values, connections and prim targets. Find node types and ports first with sim__graph__node_types. Checks every port, lists the real ones on a typo, and rolls the graph back on any error. Timeline stopped. Returns it as sim__graph__inspect does.",
    dict(
        **TARGET,
        path=field("string", "Graph prim path to create, e.g. /World/ROS_Clock or /turtlebot3_burger/ROS_Drive."),
        nodes=field("array", "[name, node_type] pairs or {name, type} objects; entries or whole list may be JSON strings, e.g. ['[\"tick\", \"omni.graph.action.OnPlaybackTick\"]']. A flat alternating list is also accepted."),
        values=field("object", "Input values: {\"node.inputs:port\": value}, or a JSON string containing that object.", False),
        connections=field("array", "[from, to] pairs or {from, to} objects; entries or whole list may be JSON strings. A flat alternating list is also accepted.", False),
        targets=field("object", "Prim targets: {\"node.inputs:targetPrim\": [\"/World/robot\"]}, or a JSON string containing that object or a target path list.", False),
        evaluator=field("string", "Graph evaluator: execution (Action Graph, default), push or dirty_push.", False, default="execution", enum=["execution", "push", "dirty_push"]),
        replace=field("boolean", "Delete an existing graph at path first (it is not restored if the new build fails).", False, default=False),
    ),
)
add(
    "sim.graph.inspect",
    "Read an OmniGraph: every node's type, input values or upstream connections, and output connections. Use to verify a hand-built Action Graph (missing wires, unset targets, ports created at runtime). ROS helpers report ros_render_status and writer_computes, which show a sensor that is not rendering; publisher count is not read here, so check with ros2 topic info. Read-only.",
    dict(
        **TARGET,
        path=field("string", "Graph prim path, e.g. /World/ROS_Clock."),
        limit=field("integer", "Maximum nodes returned, 1..128.", False, default=64),
    ),
)
add(
    "sim.graph.show",
    "Open a graph in the Action Graph editor of a windowed Isaac Sim and zoom to fit, e.g. for a demo video. layout=true arranges nodes and writes positions into the stage, so stop first or use layout=false while playing; TIMELINE_ACTIVE includes a retry_with call. close=true closes the editor. Uses the editor's internal API and refuses cleanly if it changed.",
    dict(
        **TARGET,
        path=field("string", "Graph prim path (not needed with close=true).", False, default=""),
        layout=field("boolean", "Expand and auto-arrange nodes that have no stored position yet (writes node positions).", False, default=False),
        width=field("integer", "Undock the editor window and size it (pixels, clamped to the Isaac Sim window); 0 keeps the docked layout.", False, default=0),
        height=field("integer", "Window height in pixels when width is set.", False, default=0),
        close=field("boolean", "Close the Action Graph editor window instead of showing a graph.", False, default=False),
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
    "Drive Isaac Sim's own UI through Kit's input system, not the desktop: click, double_click, right_click, move, drag, scroll, type, keypress, menu, tab, close, or wait. Target a widget by ref from sim__ui__find or by x, y window pixels. Works while the desktop is locked or used remotely (desktop window_* input does not). Then check the result with screenshot(window_id=<Isaac Sim window>) or sim__ui__find. UI actions can change or delete scene data and files like a person clicking would.",
    dict(
        action=field("string", "What to do. tab brings a docked window to the front, like clicking its tab (sim__ui__find lists hidden_tabs); close hides a Kit window or docked panel such as 'Console' and reports whether it is now hidden (reopen with tab).", enum=["click", "double_click", "right_click", "move", "drag", "scroll", "type", "keypress", "menu", "tab", "close", "wait"]),
        ref=field("string", "Widget ref from sim__ui__find (its centre is used). For type/keypress it is clicked first to focus it.", False),
        x=field("integer", "X in Isaac Sim window pixels (when no ref).", False),
        y=field("integer", "Y in Isaac Sim window pixels (when no ref).", False),
        end_x=field("integer", "Drag end X in window pixels.", False),
        end_y=field("integer", "Drag end Y in window pixels.", False),
        text=field("string", "Text for action=type (1..500 characters, no newlines; send enter with keypress).", False),
        keys=field("array", "For action=keypress: one key plus optional modifiers, e.g. [\"ctrl\", \"s\"], [\"enter\"], [\"shift\", \"tab\"].", False),
        window=field("string", "For action=tab or close: the window or tab title, e.g. 'Console' or 'Content'.", False),
        menu=field("string", "For action=menu: a menu path, e.g. 'Create/Mesh/Cube' (a menu an extension adds exists only once it has loaded), or a top-level menu such as 'Layouts' (opened to list its items, then closed again).", False),
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
        z=field("array", "World [z_min, z_max] in metres. A collider anywhere in this range marks its cells occupied, so keep z_min above the floor and z_max below anything the robot passes under", False, default=[0.05, 0.3]),
        clear=field("array", "Up to 16 [x, y, radius] circles (metres) to mark free, e.g. the robot's own footprint at its start pose.", False, default=[]),
        start=field("array", "World [x, y] in free space the generator flood-fills from; cells it cannot reach are unknown. Default: the centre of the bounds (move it if a table or pillar is there).", False),
        cell_size=field("number", "Cell size in metres (map resolution).", False, default=0.05),
        basename=field("string", "Output file stem (letters, digits, _ -); writes <basename>.pgm and .yaml.", False, default="map"),
    ),
)
add(
    "sim.logs.tail",
    "Read a bounded tail of the selected Kit log file, never arbitrary files. Default: last 1 MiB, warnings/errors and [sim-tools] lines in `lines`. With min_level, channel, contains or cursor: parsed `entries` (timestamp, level, channel, message capped at 2 KB), `next_cursor` and `truncated`; a cursor comes from sim__logs__mark or a previous next_cursor. Reads at most 1 MiB per call and never installs a log callback. Redacts obvious secrets. Also returns recent scene_events. Needs no session_id or stage_id.",
    dict(
        **UNUSED_TARGET,
        limit=field("integer", "Maximum entries or lines 1..100.", False, default=50),
        min_level=field(
            "string",
            "Lowest level to return, e.g. warning.",
            False,
            default="warning",
            enum=["debug", "info", "warning", "error", "fatal"],
        ),
        channel=field("string", "Case-insensitive substring of the log channel, e.g. omni.physx (<=64 characters).", False, default=""),
        contains=field("string", "Case-insensitive substring of the message (<=128 characters).", False, default=""),
        cursor=field(
            "object",
            "Cursor from sim__logs__mark or a previous next_cursor; reads only lines after it and detects rotation/truncation.",
            False,
        ),
    ),
)
add(
    "sim.logs.mark",
    "Cursor at the current end of Kit's log file: byte offset plus file identity (device, inode, size, mtime). Pass it to sim__logs__tail or sim__logs__summary to read only what happens next; rotation or truncation is reported instead of unrelated lines. Reads the log path only; installs no log callback. Needs no session_id or stage_id.",
    dict(**UNUSED_TARGET),
)
add(
    "sim.logs.summary",
    "Counts by level and channel since a sim__logs__mark cursor (or the retained 1 MiB window when omitted), with the first and last error. Reads at most 1 MiB per call; `truncated` and `next_cursor` say whether more remains. Same file parser and secret redaction as sim__logs__tail; installs no log callback. Needs no session_id or stage_id.",
    dict(
        **UNUSED_TARGET,
        cursor=field(
            "object",
            "Cursor from sim__logs__mark or a previous next_cursor; omit for the retained 1 MiB window.",
            False,
        ),
    ),
)
add(
    "sim.controller.create",
    "OPT-IN CODE EXECUTION: create OnPlaybackTick -> ScriptNode controller using inline Python. Requires ISAAC_MCP_ENABLE_SCENE_SCRIPTS=1 in Kit launch; NOT a sandbox.",
    dict(
        **TARGET,
        path=field("string", "Path for the new graph, not in use yet."),
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
    "sim.arm.ik",
    "Inverse kinematics for any robot with revolute or prismatic joints, read from its own USD joints (no per-robot file): the joint values that put a link at a target position and optional orientation, nearest the current pose. Solve only: nothing is commanded; no collision check or path. Needs a playing or paused timeline. Refuses if its model disagrees with physics. Mimic joints are treated as independent. Returns joint_targets for sim__joints__move and the errors; reached=false means out of reach or limits.",
    dict(
        **TARGET,
        path=field("string", "Articulation root, as for sim__joints__state."),
        link=field("string", "The link to place: its name (such as panda_hand) or absolute path."),
        position=field("array", "Target [x, y, z] in stage units, in the world frame unless frame=base."),
        orientation=field("array", "Optional target orientation [w, x, y, z]. Omit to place the link by position only.", False),
        base_link=field("string", "Chain start: a link name or path. Default: the body the articulation hangs from (the root body, or the one fixed to the world).", False),
        offset=field("array", "Optional [x, y, z] in the link's own frame, in stage units: place this point (a fingertip, a tool tip) instead of the link's origin.", False),
        frame=field("string", "world (default) or base: the frame position and orientation are given in.", False, default="world"),
        position_tolerance=field("number", "Metres of position error that counts as reached.", False, default=0.001),
        orientation_tolerance=field("number", "Radians of orientation error that counts as reached.", False, default=0.01),
    ),
)
add(
    "sim.arm.fk",
    "Forward kinematics: where a link (the hand) would be for given joint values, from the robot's own USD joints, in the world and base frames. Joints you leave out use their current values when the simulation runs, else zero. Commands nothing; pair it with sim__arm__ik. Works stopped or running; when running it first checks its model against physics.",
    dict(
        **TARGET,
        path=field("string", "Articulation root, as for sim__joints__state."),
        link=field("string", "The link: its name (such as panda_hand) or absolute path."),
        joints=field("object", "Optional {joint name: value}: radians for revolute, stage units for prismatic, e.g. sim__arm__ik's joint_targets.", False),
        offset=field("array", "Optional [x, y, z] in the link's own frame, in stage units: place this point (a fingertip, a tool tip) instead of the link's origin.", False),
        base_link=field("string", "Chain start: a link name or path. Default: the articulation root's body.", False),
    ),
)
add(
    "sim.joints.command",
    "Command initialized articulation position targets by name (targets) or in DOF order (positions), and/or velocity targets, on the selected PhysX or qualified Newton backend. Radians for revolute, stage units for prismatic. Out-of-limit values are refused unless clamp=true, which reports what it clamped; unknown names list the valid ones. Returns applied targets and previous values, not achieved poses: compare sim__joints__state. Runtime only and lost on Stop; sim__joints__drive authors USD targets.",
    dict(
        **TARGET,
        path=field("string", "Exact initialized articulation root path."),
        positions=field(
            "array",
            "One finite position per DOF, in sim.joints.state order. Give this or targets, not both.",
            False,
        ),
        targets=field(
            "object",
            "{dof name: position}; names come from sim__joints__state. An unknown name is refused with the valid ones.",
            False,
        ),
        velocities=field(
            "object",
            "{dof name: velocity target}, rad/s for revolute and stage units/s for prismatic. A velocity drive needs damping (a wheel usually stiffness 0), so check the response.",
            False,
        ),
        clamp=field(
            "boolean",
            "Use the runtime limit for an out-of-limit value and report it, instead of refusing.",
            False,
            default=False,
        ),
    ),
)
add(
    "sim.joints.gains",
    "Read or set an initialized articulation's runtime drive gains: stiffness, damping and max_effort (the drive's torque/force cap) for named dofs or every DOF. These are PhysX/Newton runtime values, not USD: they are lost on Stop and never saved; sim__joints__drive authors USD gains. Omit every value to only read. Returns each DOF's values before and after. Needs a playing or paused timeline and a valid tensor view.",
    dict(
        **TARGET,
        path=field("string", "Exact initialized articulation root path."),
        dofs=field(
            "array", "DOF names; empty applies to every DOF. An unknown name is refused with the valid ones.", False, default=[]
        ),
        stiffness=field(
            "number", "Nonnegative position-drive stiffness (torque per radian, or force per stage unit).", False
        ),
        damping=field(
            "number", "Nonnegative position-drive damping (torque per rad/s, or force per stage unit/s).", False
        ),
        max_effort=field(
            "number", "Positive drive cap in torque (revolute) or force (prismatic).", False
        ),
    ),
)
add(
    "sim.joints.move",
    "Step a PAUSED, initialized simulation until named joint targets are within tolerance or max_steps is used, then report settled and every joint's error. A joint held by a contact or a weak drive reports settled=false with its remaining error: clear the path, raise gains with sim__joints__gains, or retry with more steps. Runtime only, like sim__joints__command; never pumps the app loop.",
    dict(
        **TARGET,
        path=field("string", "Exact initialized articulation root path."),
        targets=field(
            "object", "{dof name: position}, radians or stage units. An unknown name is refused with the valid ones."
        ),
        max_steps=field("integer", "Physics steps to try, 1..600.", False, default=600),
        tolerance=field(
            "number", "Per-joint position error that counts as settled, >0 (radians or stage units).", False, default=0.01
        ),
        clamp=field(
            "boolean", "Use the runtime limit for an out-of-limit target and report it, instead of refusing.",
            False, default=False,
        ),
    ),
)
add(
    "sim.joints.teleport",
    "Set joint positions directly as a state reset, not motion: physics response, contacts and drive limits are bypassed, and drive targets are left unchanged so the next steps can pull the joints back. Positions are {dof name: value}, radians for revolute and stage units for prismatic. Needs an initialized (playing or paused) articulation and returns each joint's value before and after. Out-of-limit values are refused.",
    dict(
        **TARGET,
        path=field("string", "Exact initialized articulation root path."),
        positions=field(
            "object", "{dof name: position}. Unknown names are refused with the valid ones."
        ),
    ),
)
add(
    "sim.prim.set_attribute",
    "Set one typed attribute on any prim in the session layer, reporting the composed value before and after, and undo it with sim__prim__revert. Refuses engine- and schema-owned attributes (points, normals, faceVertexIndices, faceVertexCounts, extent, xformOpOrder, xformOp:*, physics:*, physx*:*), instance proxies and Kit runtime prims. type creates a missing attribute; without it the attribute must exist. Values are JSON; quaternions are [w,x,y,z].",
    dict(
        **TARGET,
        path=field("string", "Absolute prim path."),
        attribute=field("string", "Attribute name, e.g. visibility, purposes, user:owner or a namespaced name."),
        value=field(
            "any",
            "New value as JSON: true/false, a number, a string, or a flat list for a vector/quaternion. Converted to the attribute's USD type.",
        ),
        type=field(
            "string",
            "USD type for a new attribute: bool, int, uint, int64, uint64, float, double, string, token, float2/3/4, double2/3/4, int2/3, color3f/4f, normal3f, point3f, vector3f, quatf, quatd.",
            False,
        ),
    ),
)
add(
    "sim.physics.apply",
    "Add or remove a collider, rigid body and kinematic state, or set mass / density, on any prim; session layer only, and undo with sim__prim__revert. collider can set an approximation (convexHull, convexDecomposition, boundingSphere, boundingCube, meshSimplification, none). Refuses a rigid body under an existing rigid-body ancestor, a joint or articulation root as a body, both mass and density, and any change while the timeline is active.",
    dict(
        **TARGET,
        path=field("string", "Absolute prim path."),
        collider=field("boolean", "Add (true) or remove (false) UsdPhysics CollisionAPI.", False),
        approximation=field(
            "string",
            "Collider approximation, with collider=true; empty keeps the current one.",
            False,
            default="",
            enum=[
                "convexHull",
                "convexDecomposition",
                "boundingSphere",
                "boundingCube",
                "meshSimplification",
                "none",
            ],
        ),
        rigid_body=field("boolean", "Add (true) or remove (false) UsdPhysics RigidBodyAPI.", False),
        kinematic=field(
            "boolean", "Kinematic flag (physics:kinematicEnabled); needs a rigid body on this prim.", False
        ),
        mass=field(
            "number", "Rigid-body mass in kilograms (stage mass units), through PhysicsMassAPI. Refused together with density.", False
        ),
        density=field(
            "number", "Rigid-body density in kg/m^3; the collider's volume sets the mass. Refused together with mass.", False
        ),
    ),
)
add(
    "sim.prim.revert",
    "Remove the session-layer opinions these tools authored on one prim or its subtree, restoring composed values from the source files, and report what was removed and the values before and after. Source files never change. Prims the pack created stay: delete them with sim__prim__delete. Nothing recorded is a no-op, not an error. Only edits recorded in this session and stage can be undone.",
    dict(
        **TARGET,
        path=field("string", "Absolute prim path whose recorded edits are undone."),
        subtree=field(
            "boolean", "Also revert recorded edits on descendants.", False, default=True
        ),
    ),
)
add(
    "assets.import_urdf",
    "Convert a URDF with the installed importer and import it, from an approved root or a pack in your asset folder. Resolves package:// meshes inside that folder only and reports what it mapped. Gives joints with no gains drives that hold a pose, lifts a robot out of a ground plane, and warns about unusable mass or inertia. Source files never change. Z up, metres. Refuses xacro, DTDs, URLs, paths leaving the folder and missing meshes. Returns authored values, not runtime readiness.",
    dict(
        **TARGET,
        root_id=field("string", "Approved root ID, with relative_path. Or use source_path.", False),
        relative_path=field("string", "Path of the .urdf file within that root.", False),
        source_path=field("string", "Absolute path of the .urdf file, under an approved root or in a pack of your asset folder (pins are checked). Replaces root_id and relative_path.", False),
        prim_path=field("string", "New wrapper prim path."),
        fixed_base=field("boolean", "Fix the robot's base to the world. Pass false for a robot that drives or walks; it then falls under gravity.", False, default=True),
        default_drives=field("boolean", "Author position drives that hold a pose; the URDF format has none. A stiffness in a 6.x conversion is kept, and the 5.x importer's own is replaced. With false you get the importer's own: on 6.x stiffness 0, so targets do nothing until sim__joints__drive sets gains.", False, default=True),
        position=field("array", "Three finite coordinates in stage units. Omit to place the robot at the origin, raised out of any collision ground plane. A given position is used as is, with a warning if it starts under the ground.", False),
        orientation=POSE["orientation"],
        frame=POSE["frame"],
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
REGISTRY["sim.prim.create"]["parameters"]["mass"] = field(
    "number",
    "Rigid-body mass in kilograms (stage mass units), through PhysicsMassAPI. Refused together with density. Give rigid=true for a runtime effect.",
    False,
)
REGISTRY["sim.prim.create"]["parameters"]["density"] = field(
    "number",
    "Rigid-body density in kg/m^3; the geometry's volume sets the mass. Refused together with mass. Give rigid=true for a runtime effect.",
    False,
)
REGISTRY["sim.prim.set_transform"]["parameters"]["scale"] = field(
    "array",
    "Positive XYZ scale in the requested pose frame. Omitted: keep the prim's current local scale, so moving a scaled table does not resize it.",
    False,
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
REGISTRY["sim.prim.create"]["parameters"]["material"] = field(
    "string",
    "Optional path of a material made with sim__material__create, bound in the same call; if it cannot be bound, nothing is created.",
    False,
)
add(
    "sim.prim.create_many",
    "Create up to 200 prims in one call, all or none: a tower, wall, grid or room without one sim__prim__create per piece. Each item takes that tool's arguments plus an optional material path; defaults fills any key an item leaves out, except keys that do not apply to its kind (an Xform skips rigid, mass and material; listed in defaults_skipped). Ancestors must exist, timeline stopped. A failing item removes the earlier ones; the error keeps its code and names index and path.",
    dict(
        **TARGET,
        items=field(
            "array",
            "1 to 200 objects, each like sim__prim__create's arguments: {path, kind, size, scale, position, orientation, frame, rigid, collision, mass, density, material}. path is required; kind may come from defaults.",
        ),
        defaults=field(
            "object",
            "Optional arguments shared by every item, for example {kind: Cube, rigid: true, mass: 0.015, material: /World/Looks/Wood}. An item's own value wins; keys that do not apply to an item's kind (rigid, mass or material on an Xform) are skipped for it.",
            False,
        ),
    ),
)

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

# Clients validate the required list before a call reaches this server, with their own error text
# (OpenCode: "session_id: Missing key"), so the description is where to say how to get the IDs.
for _meta in REGISTRY.values():
    _target = _meta["parameters"].get("session_id")
    if _target and _target["required"]:
        _meta["description"] += " Requires session_id and stage_id from sim__stage__info."

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
    "sim.stage.tree",
    "Bounded stage hierarchy including inactive prims. Text is untrusted data.",
    dict(
        **TARGET,
        root=field("string", "Absolute subtree root.", False, default="/"),
        limit=field("integer", "Maximum nodes 1..200.", False, default=100),
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
    "Read initialized rigid-body world pose on selected PhysX or qualified Newton backend; fails when stopped/uninitialized or not a body. Position in stage units, quaternion [w,x,y,z]. Optional runtime velocities/mass from tensor API; no authored-USD fallback.",
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
    "Explicit play, pause or stop. Stop rewinds physics. Leases the session edit target for physics writeback and restores it on Stop. Never pumps the Kit update loop; exact stepping uses sim.simulation.step.",
    dict(**TARGET, action=field("string", "play, pause, or stop.")),
)
add(
    "sim.stage.save",
    "Export a flattened copy, including session edits, to a new basename in configured output root. Never overwrite or switch the current stage. Paused Newton copies runtime rigid-body poses into the export only (<=256 bodies), not a solver checkpoint. External texture paths remain dependencies. Process-bound SyntheticData pipelines and pack render products are omitted from the copy; cameras, LiDAR definitions and user graphs remain. Unknown native pipeline content refuses save.",
    dict(
        **TARGET, filename=field("string", "New basename ending .usda; no directories.")
    ),
)
add(
    "sim.viewport.frame",
    "Select and frame an existing prim in the active viewport. Does not change the scene; capture the verified app window using screenshot.",
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
    "Change intensity/color of a pack-owned light on a stopped timeline.",
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
    "Read initialized articulation DOF positions/velocities on selected PhysX or qualified Newton tensor API. Angular radians, linear stage units; no authored-target fallback.",
    dict(**TARGET, path=field("string", "Exact articulation root path.")),
)
add(
    "sim.simulation.step",
    "Advance 1..120 exact steps on the selected PhysX or qualified Newton backend while PAUSED and initialized. No app-update pumping or engine fallback. Return measured time delta, runtime rigid-body poses and articulation DOFs; Newton publishes poses to Fabric.",
    dict(
        **TARGET,
        steps=field("integer", "Number of physics steps 1..120."),
        observe_prims=field("array", "Up to 16 rigid body paths.", False, default=[]),
        observe_joints=field("array", "Up to 8 articulation paths.", False, default=[]),
    ),
)
add(
    "sim.camera.create",
    "Create a persistent RTX camera on a stopped timeline. Build identity and owned-CPU Replicator capabilities are checked; experimental.rtx is not required. USD axes: -Z forward, +Y up. Needs rendered frames; width/height capped at 1024.",
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
    "Convert a trusted approved relative URDF project using the installed importer, then snapshot and import its USD. No source overwrite. Rejects DTD/entities, package/URL/absolute mesh paths and missing files. Requires 6.x URDFImporter API.",
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
REGISTRY["sim.prim.create"]["description"] += (
    " Also supports Cylinder, Capsule and Cone; size is diameter/height before optional scale."
)

for _name in ("sim.camera.capture", "sim.lidar.read"):
    REGISTRY[_name]["parameters"]["wait_for_render"] = field(
        "boolean",
        "Camera: wait up to five seconds for four ordinary render completions and a new CPU transfer from this product. LiDAR: wait for one render; acquisition requires Play, so default false. False reads the latest buffer, possibly stale. No implicit physics advance or atomic sensor/physics guarantee.",
        False,
        default=_name == "sim.camera.capture",
    )

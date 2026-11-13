"""Newton configuration and diagnostics, without starting or advancing physics."""

from copy import deepcopy

from pxr import UsdPhysics

from isaac_mcp.project_assets import refuse


def buffer_sizes(body_count, nconmax=None, njmax=None):
    for name, value in (("nconmax", nconmax), ("njmax", njmax)):
        if value is not None and (type(value) is not int or not 1 <= value <= 8_000_000):
            refuse("INVALID_VALUE", f"{name} must be an integer in 1..8000000.")
    contacts = nconmax if nconmax is not None else max(512, body_count * 32)
    return contacts, njmax if njmax is not None else max(2048, contacts * 8)


def prepare_buffers(stage, scene, timeline, nconmax=None, njmax=None):
    """Prepare a copy first; configure_newton invalidates the initialized model."""
    if not timeline.is_stopped():
        refuse("TIMELINE_PLAYING", "Stop Newton before changing solver buffers; paused is not stopped.")
    scenes = []
    bodies = 0
    for index, prim in enumerate(stage.Traverse()):
        if index >= 10000:
            refuse("LIMIT", "Newton buffer sizing requires a scene with at most 10000 prims.")
        if prim.IsA(UsdPhysics.Scene):
            scenes.append(str(prim.GetPath()))
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            bodies += 1
    if scenes != [str(scene.GetPath())]:
        refuse("NEWTON_CONFIG_UNSUPPORTED", "Newton configuration is process-local; select a stage with one PhysicsScene.")
    if any(scene.HasAPI(api) for api in ("NewtonXpbdSceneAPI", "NewtonVbdSceneAPI")):
        refuse("NEWTON_CONFIG_UNSUPPORTED", "nconmax/njmax require MuJoCo; the selected scene uses another solver.")
    try:
        from isaacsim.physics.newton import NewtonConfig, MuJoCoSolverConfig, get_newton_config
    except ImportError:
        refuse("PHYSICS_API_UNAVAILABLE", "This build does not export NewtonConfig/MuJoCoSolverConfig/get_newton_config.")
    config = deepcopy(get_newton_config() or NewtonConfig())
    if not isinstance(config.solver_cfg, MuJoCoSolverConfig):
        if config.solver_cfg.solver_type not in (None, "None", "", "mujoco"):
            refuse("NEWTON_CONFIG_UNSUPPORTED", "Pending Newton configuration selects another solver; not replacing it.")
        config.solver_cfg = MuJoCoSolverConfig()
    contacts, constraints = buffer_sizes(bodies, nconmax, njmax)
    if nconmax is None:
        contacts = max(contacts, config.solver_cfg.nconmax or 0)
    if njmax is None:
        constraints = max(constraints, contacts * 8, config.solver_cfg.njmax or 0)
    config.solver_cfg.nconmax = contacts
    config.solver_cfg.njmax = constraints
    config.collision_cfg.rigid_contact_max = max(config.collision_cfg.rigid_contact_max or 0, contacts)
    config.solver_outputs.contact_forces = True
    report = dict(
        rigid_bodies=bodies, nconmax=contacts, njmax=constraints,
        rigid_contact_max=config.collision_cfg.rigid_contact_max,
        contact_forces=True, persistence="process-local; reconfigure after reopening or restarting",
        sizing="32 contacts/body, 8 constraints/contact; minimum 512/2048; explicit values override sizing",
        scope="nconmax/njmax per world; collision capacity across worlds; inspect actual capacity after initialization",
    )
    return config, report


def apply_buffers(config):
    from isaacsim.physics.newton import configure_newton

    configure_newton(config)


def scene_settings(scene, rate, tower=False):
    for api in ("NewtonSceneAPI", "MjcSceneAPI"):
        if not scene.ApplyAPI(api):
            refuse("PHYSICS_API_UNAVAILABLE", f"{api} unavailable; inspect partial scene configuration.")
    values = {"newton:timeStepsPerSecond": rate, "mjc:option:timestep": 1 / rate}
    if tower:
        values.update({"newton:maxSolverIterations": 100, "mjc:option:cone": "elliptic", "mjc:option:impratio": 1.0})
    for name, value in values.items():
        attr = scene.GetAttribute(name)
        if not attr or not attr.Set(value):
            refuse("PHYSICS_API_UNAVAILABLE", f"{name} unavailable; inspect partial scene configuration.")
    return values


def collision_settings(prim):
    if not prim.ApplyAPI("NewtonCollisionAPI"):
        refuse("PHYSICS_API_UNAVAILABLE", "NewtonCollisionAPI unavailable; inspect partial tower construction.")
    for name, value in (("newton:contactMargin", 0.0), ("newton:contactGap", 0.004)):
        attr = prim.GetAttribute(name)
        if not attr or not attr.Set(value):
            refuse("PHYSICS_API_UNAVAILABLE", f"{name} unavailable; inspect partial tower construction.")


def _counts(value):
    from .physics import array

    if value is None:
        return None
    values = array(value).reshape(-1)
    if any(not float(number).is_integer() or number < 0 for number in values):
        refuse("PHYSICS_STATE_INVALID", "Newton contact/constraint counts must be nonnegative integers.")
    return [int(number) for number in values]


def diagnostics(stage):
    from .physics import newton_stage
    from isaacsim.physics.newton import get_newton_config
    from isaac_mcp.project_assets import AssetError

    config = get_newton_config()
    solver_config = getattr(config, "solver_cfg", None)
    result = dict(
        backend="newton", configured={name: getattr(solver_config, name, None) for name in ("nconmax", "njmax")},
        active_contacts=None, contact_capacity=None, active_constraints=None, constraint_capacity=None,
        warnings=[], overflow_flags=None, overflow_history_available=False,
        note="Overflow flags persist until reset/wake/restore, not for the process lifetime. Missing counters do not prove safety. Configuration is not saved in USD.",
    )
    try:
        native = newton_stage(stage)
    except AssetError as exc:
        if exc.code != "PHYSICS_NOT_READY":
            raise
        result["runtime_status"] = exc.code
        return result
    data = getattr(getattr(native, "solver", None), "mjw_data", None)
    if data is None:
        result["runtime_status"] = "MuJoCo-Warp capacity counters unavailable"
        return result
    result.update(active_contacts=_counts(getattr(data, "nacon", None)),
                  active_constraints=_counts(getattr(data, "nefc", None)),
                  contact_capacity=getattr(data, "naconmax", None),
                  constraint_capacity=getattr(data, "njmax", None), runtime_status="initialized")
    flags = _counts(getattr(data, "overflow", None))
    if flags is not None:
        names = ("NEFC", "NJMAX_NNZ", "BROADPHASE", "NARROWPHASE", "CCD", "HFIELD",
                 "CONTACT_MATCH", "NVMAX", "EPA_HORIZON", "ITERATIONS", "LS_ITERATIONS")
        result["overflow_history_available"] = True
        result["overflow_flags"] = [dict(raw=value, names=[name for bit, name in enumerate(names) if value & (1 << bit)],
                                         unknown_bits=value & ~((1 << len(names)) - 1)) for value in flags]
        if any(flags):
            result["warnings"].append(dict(code="NEWTON_SOLVER_OVERFLOW", message="MuJoCo-Warp overflow/convergence flags are set; inspect overflow_flags before trusting contacts or motion."))
    for field, capacity, code in (("active_contacts", "contact_capacity", "CONTACT_BUFFER_FULL"),
                                  ("active_constraints", "constraint_capacity", "CONSTRAINT_BUFFER_FULL")):
        if result[field] is not None and result[capacity] is not None:
            result[capacity] = int(result[capacity])
            if any(count >= result[capacity] for count in result[field]):
                result["warnings"].append(dict(code=code, message="Capacity reached; contacts or constraints may have been dropped. Stop and increase buffers."))
    return result


def contacts(paths, stage):
    import numpy as np
    from .physics import array, newton_stage
    from .queries import MAX_HITS, _matches

    for path in paths:
        prim = stage.GetPrimAtPath(path)
        if not prim:
            refuse("NOT_FOUND", f"No prim at {path}.")
        if not prim.IsActive() or not (prim.HasAPI(UsdPhysics.RigidBodyAPI) or prim.HasAPI(UsdPhysics.CollisionAPI)):
            refuse("WRONG_TYPE", f"{path} must be an active body or collider.")
    native = newton_stage(stage)
    config = getattr(native, "cfg", None)
    if (getattr(getattr(config, "solver_cfg", None), "solver_type", None) != "mujoco"
            or getattr(getattr(native, "solver", None), "use_mujoco_cpu", False)
            or not getattr(getattr(config, "solver_outputs", None), "contact_forces", False)):
        refuse("CONTACT_QUERY_UNSUPPORTED", "Contact forces require Newton's MuJoCo-Warp solver with solver_outputs.contact_forces enabled before initialization. CPU MuJoCo and other solvers do not populate this contact-force snapshot; use MuJoCo-Warp or PhysX contact reports.")
    model, data = getattr(native, "model", None), getattr(native, "contacts", None)
    if model is None or data is None:
        refuse("CONTACT_QUERY_UNSUPPORTED", "Newton has no initialized contact arrays. Enable MuJoCo contact outputs, then stop and initialize.")
    count_ref = getattr(data, "rigid_contact_count", None)
    if count_ref is None:
        count_ref = getattr(data, "n_contacts", None)
    counts = _counts(count_ref)
    if counts is None or len(counts) != 1:
        refuse("CONTACT_QUERY_UNSUPPORTED", "Newton contact count is unavailable; no empty report was invented.")
    count = counts[0]
    if count > 262144:
        refuse("LIMIT", "Newton contact snapshot exceeds 262144 contacts; reduce scene size.")
    force = getattr(data, "force", None)
    if force is None:
        force = getattr(data, "rigid_contact_force", None)
    if force is None:
        refuse("CONTACT_QUERY_UNSUPPORTED", "Newton contact forces are unavailable. Enable solver_outputs.contact_forces before initialization.")
    shape0, shape1 = (getattr(data, name, None) for name in ("rigid_contact_shape0", "rigid_contact_shape1"))
    if shape0 is None or shape1 is None:
        refuse("CONTACT_QUERY_UNSUPPORTED", "Newton contact shape indices are unavailable; no pairs were guessed.")
    shape_body_ref = getattr(model, "shape_body", None)
    if shape_body_ref is None or not hasattr(model, "shape_label") or not hasattr(model, "body_label"):
        refuse("CONTACT_QUERY_UNSUPPORTED", "Newton model lacks shape/body mappings; no pairs were guessed.")
    for source in (shape0, shape1, force, shape_body_ref):
        shape = getattr(source, "shape", ())
        if not shape or shape[0] > 262144:
            refuse("LIMIT", "Newton contact arrays must have bounded, known capacity (at most 262144 entries).")
    first, second = np.asarray(array(shape0)).reshape(-1), np.asarray(array(shape1)).reshape(-1)
    forces = np.asarray(array(force))
    shape_body = np.asarray(array(shape_body_ref)).reshape(-1)
    shape_labels, body_labels = list(model.shape_label), list(model.body_label)
    if (forces.ndim != 2 or forces.shape[1] not in (3, 6) or count > min(len(first), len(second), len(forces))
            or not np.isfinite(forces[:count, :3]).all()):
        refuse("PHYSICS_STATE_INVALID", "Newton contact count/force arrays are invalid; no truncated or nonfinite forces returned.")
    grouped = {}
    truncated = False
    for index in range(count):
        indices = (first[index], second[index])
        if any(not float(value).is_integer() or value < 0 or value >= min(len(shape_labels), len(shape_body)) for value in indices):
            refuse("PHYSICS_STATE_INVALID", "Newton contact shape index is invalid.")
        colliders, bodies = [], []
        for value in indices:
            shape_index = int(value)
            body_index = shape_body[shape_index]
            if not float(body_index).is_integer() or not -1 <= body_index < len(body_labels):
                refuse("PHYSICS_STATE_INVALID", "Newton shape-to-body index is invalid.")
            colliders.append(str(shape_labels[shape_index]))
            bodies.append(str(body_labels[int(body_index)]) if body_index >= 0 else None)
        if not any(_matches(wanted, candidate) for wanted in paths for candidate in (*bodies, *colliders) if candidate):
            continue
        key = (*bodies, *colliders)
        if key not in grouped:
            if len(grouped) == MAX_HITS:
                truncated = True
                continue
            grouped[key] = dict(body_a=bodies[0], body_b=bodies[1], collider_a=colliders[0], collider_b=colliders[1],
                                contact_points=0, impulse=None, impulse_magnitude=None, force=np.zeros(3))
        pair = grouped[key]
        pair["contact_points"] += 1
        pair["force"] += forces[index, :3]
    pairs = list(grouped.values())
    for pair in pairs:
        pair["force_magnitude"] = float(np.linalg.norm(pair["force"]))
        pair["force"] = pair["force"].tolist()
    return dict(paths=paths, pairs=pairs, truncated=truncated, reporting_paths=paths, paths_without_reporting=[],
                reports_seen=None, backend="newton", units="force in simulation mass*length/time^2; impulse unavailable",
                source="Newton Contacts snapshot; world-frame linear force on collider_a (shape0)",
                note="Instantaneous forces, not integrated impulses; no physics advance. Contact outputs must be enabled.",
                newton=diagnostics(stage))

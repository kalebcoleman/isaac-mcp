"""Newton (MuJoCo-Warp) support for the generic scene tools.

Solver buffers, solver and collision settings, buffer and overflow diagnostics, contact reads, the
one-off push and the timestep cross-check. Nothing here starts, steps or resets physics.

What the installed Newton (Isaac Sim 6.2: isaacsim.physics.newton 1.1.1 with Newton 1.6.1 and
MuJoCo-Warp) really exposes, and where this module relies on it:

* ``isaacsim.physics.newton.configure_newton(NewtonConfig)`` replaces the live configuration and marks
  the stage uninitialized, so the next Play rebuilds the model with it. ``MuJoCoSolverConfig.nconmax``
  (default 200) is the contact capacity per world and ``njmax`` (default 1200) the constraint rows per
  world; MuJoCo-Warp drops contacts past ``naconmax = nconmax * worlds`` and sets bits in ``Data.overflow``.
* ``CollisionConfig.rigid_contact_max`` caps Newton's own contact buffer and must be at least
  ``naconmax``, or ``SolverMuJoCo.update_contacts`` raises on every step.
* After each step ``NewtonStage.contacts`` holds the converted MuJoCo contacts: ``rigid_contact_count``,
  ``rigid_contact_shape0/1`` and ``force`` (world-frame spatial vector, on shape0). This is the same data
  Isaac's own contact tensor view reads. It exists only when ``solver_outputs.contact_forces`` is on
  before initialization (the default).
* ``handle_raycast`` is an empty stub and there is no public Newton ray query, so raycast stays refused.
"""

from copy import deepcopy

from pxr import UsdPhysics

from isaac_mcp.project_assets import AssetError, refuse

MAX_BUFFER = 8_000_000
MIN_CONTACTS, MIN_CONSTRAINTS = 512, 2048
CONTACTS_PER_BODY, ROWS_PER_CONTACT = 32, 8
MAX_PRIMS = 10000
MAX_SNAPSHOT = 262144
MUJOCO_SOLVERS = (None, "", "mjcwarp", "mujoco")
OVERFLOW_BITS = ("NEFC", "NJMAX_NNZ", "BROADPHASE", "NARROWPHASE", "CCD", "HFIELD",
                 "CONTACT_MATCH", "NVMAX", "EPA_HORIZON", "ITERATIONS", "LS_ITERATIONS")
CAPACITY_BITS = 9  # the first nine bits are buffer overflows; the last two are convergence limits
DT_TOLERANCE = 1e-6


def engine_name():
    """The active physics engine, or None when this build cannot say. Never refuses."""
    try:
        from isaacsim.core.simulation_manager import SimulationManager

        return SimulationManager.get_active_physics_engine()
    except Exception:  # noqa: BLE001 - a probe: Isaac Sim 5.1 has no engine selection
        return None


# --- solver buffers ---------------------------------------------------------------------------------


def buffer_sizes(body_count, nconmax=None, njmax=None):
    """``(nconmax, njmax)``: the explicit values, else 32 contacts per body (at least 512) and 8 constraint
    rows per contact (at least 2048). The rule is generous: a box stack makes about 12 to 24 contacts per body."""
    for name, value in (("nconmax", nconmax), ("njmax", njmax)):
        if value is not None and (type(value) is not int or not 1 <= value <= MAX_BUFFER):
            refuse("INVALID_VALUE", f"{name} must be an integer in 1..{MAX_BUFFER}.")
    contacts = nconmax if nconmax is not None else max(MIN_CONTACTS, body_count * CONTACTS_PER_BODY)
    return contacts, njmax if njmax is not None else max(MIN_CONSTRAINTS, contacts * ROWS_PER_CONTACT)


def _scan(stage):
    """``(PhysicsScene paths, rigid body count, world count)``, bounded. Cloned environments under
    /World/envs are separate Newton worlds, and the buffers are per world."""
    scenes, bodies = [], 0
    for index, prim in enumerate(stage.Traverse()):
        if index >= MAX_PRIMS:
            refuse("LIMIT", f"Newton buffer sizing requires a scene with at most {MAX_PRIMS} prims.")
        if prim.IsA(UsdPhysics.Scene):
            scenes.append(str(prim.GetPath()))
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            bodies += 1
    envs = stage.GetPrimAtPath("/World/envs")
    worlds = sum(1 for child in envs.GetChildren() if child.GetName().startswith("env_")) if envs else 0
    return scenes, bodies, max(1, worlds)


def prepare_buffers(stage, scene, timeline, nconmax=None, njmax=None):
    """``(config, report)``: a copy of Newton's configuration with the buffers sized for this scene.

    Nothing is applied yet (``apply_buffers`` does that), so a refusal leaves the live configuration alone.
    ``configure_newton`` marks the initialized model stale, so only a Stopped timeline may do it; paused is not stopped."""
    if scene is None or not scene.IsValid():
        refuse("NOT_FOUND", "No PhysicsScene to size the Newton buffers for.")
    if timeline is None or not timeline.is_stopped():
        refuse("TIMELINE_PLAYING", "Stop Newton before changing solver buffers; paused is not stopped.")
    scenes, bodies, worlds = _scan(stage)
    if scenes != [str(scene.GetPath())]:
        refuse("NEWTON_CONFIG_UNSUPPORTED", "Newton configuration is process-local; select a stage with one PhysicsScene.")
    solver_attr = scene.GetAttribute("newton:solver")
    solver = solver_attr.Get() if solver_attr else None
    if solver not in MUJOCO_SOLVERS or any(scene.HasAPI(api) for api in ("NewtonXpbdSceneAPI", "NewtonVbdSceneAPI")):
        refuse("NEWTON_CONFIG_UNSUPPORTED", "nconmax/njmax are MuJoCo-Warp settings; the selected scene uses another solver.")
    try:
        from isaacsim.physics.newton import MuJoCoSolverConfig, NewtonConfig, get_newton_config
    except ImportError:
        refuse("PHYSICS_API_UNAVAILABLE", "This build does not export NewtonConfig, MuJoCoSolverConfig and get_newton_config.")
    config = deepcopy(get_newton_config() or NewtonConfig())
    if not isinstance(config.solver_cfg, MuJoCoSolverConfig):
        if config.solver_cfg.solver_type not in (None, "None", "", "mujoco"):
            refuse("NEWTON_CONFIG_UNSUPPORTED", "The pending Newton configuration selects another solver; not replacing it.")
        config.solver_cfg = MuJoCoSolverConfig()
    per_world = -(-bodies // worlds)
    recommended, recommended_rows = buffer_sizes(per_world)
    contacts, rows = buffer_sizes(per_world, nconmax, njmax)
    if nconmax is None:  # never shrink what is already configured: a larger earlier value is harmless
        contacts = max(contacts, config.solver_cfg.nconmax or 0)
    if njmax is None:
        rows = max(rows, contacts * ROWS_PER_CONTACT, config.solver_cfg.njmax or 0)
    config.solver_cfg.nconmax, config.solver_cfg.njmax = contacts, rows
    config.collision_cfg.rigid_contact_max = max(config.collision_cfg.rigid_contact_max or 0, contacts * worlds)
    config.solver_outputs.contact_forces = True
    warnings = []
    for name, used, wanted in (("nconmax", contacts, recommended), ("njmax", rows, recommended_rows)):
        if used < wanted:
            warnings.append(dict(code="NEWTON_BUFFER_BELOW_RECOMMENDED",
                                 message=f"{name}={used} is below the {wanted} this scene's {bodies} bodies suggest; "
                                         "contacts may be dropped once the buffer fills. Check physics inspect."))
    report = dict(
        rigid_bodies=bodies, worlds=worlds, nconmax=contacts, njmax=rows,
        rigid_contact_max=config.collision_cfg.rigid_contact_max, contact_forces=True,
        recommended=dict(nconmax=recommended, njmax=recommended_rows),
        requested=dict(nconmax=nconmax, njmax=njmax), warnings=warnings,
        persistence="process-local; applied on the next Play; configure again after restarting Kit",
        sizing=f"{CONTACTS_PER_BODY} contacts per body (at least {MIN_CONTACTS}), {ROWS_PER_CONTACT} constraint rows per contact "
               f"(at least {MIN_CONSTRAINTS}); explicit values override. Both are per world.",
        scope="nconmax and njmax are per world; rigid_contact_max covers all worlds and is kept at or above nconmax*worlds.",
    )
    return config, report


def apply_buffers(config):
    from isaacsim.physics.newton import configure_newton

    configure_newton(config)


# --- scene and collision settings ---------------------------------------------------------------------


def _mujoco_scene(scene):
    attr = scene.GetAttribute("newton:solver")
    return (attr.Get() if attr else None) in MUJOCO_SOLVERS


def _apply(prim, api):
    """Apply a schema; False, not an exception, when this USD has no such schema (Tf raises for an unregistered one)."""
    try:
        return bool(prim.ApplyAPI(api))
    except Exception:  # noqa: BLE001 - pxr.Tf.ErrorException
        return False


def _author(prim, values, what):
    for name, value in values.items():
        attr = prim.GetAttribute(name)
        if not attr or not attr.Set(value):
            refuse("PHYSICS_API_UNAVAILABLE", f"{name} unavailable; inspect partial {what}.")
    return values


def scene_settings(scene, rate, tower=False):
    """Author the outer rate on a Newton scene, plus the MuJoCo timestep when MuJoCo is the solver.

    Newton parses its rate from NewtonSceneAPI, while SimulationManager's MuJoCo wrapper (``get_physics_dt``)
    reads ``mjc:option:timestep``. With only one of them authored the two disagree (the Newton default is 1000 Hz,
    the MuJoCo default 0.002 s), so both are always written from one rate. A scene on another solver is not given MjcSceneAPI.
    ``tower=True`` adds the stack settings: an elliptic friction cone and an explicit solver-iteration limit."""
    if not _apply(scene, "NewtonSceneAPI"):
        refuse("PHYSICS_API_UNAVAILABLE", "NewtonSceneAPI is unavailable; inspect partial scene configuration.")
    values = {"newton:timeStepsPerSecond": rate}
    if tower:
        values["newton:maxSolverIterations"] = 100
    if _mujoco_scene(scene):
        if not _apply(scene, "MjcSceneAPI"):
            refuse("PHYSICS_API_UNAVAILABLE", "MjcSceneAPI unavailable; inspect partial scene configuration.")
        values["mjc:option:timestep"] = 1 / rate
        if tower:
            values.update({"mjc:option:cone": "elliptic", "mjc:option:impratio": 1.0, "mjc:option:iterations": 100})
    return _author(scene, values, "scene configuration")


def collision_settings(prim):
    """The Newton equivalent of PhysX's contact and rest offsets: a contact gap of 4 mm and no margin."""
    if not _apply(prim, "NewtonCollisionAPI"):
        refuse("PHYSICS_API_UNAVAILABLE", "NewtonCollisionAPI unavailable; inspect partial tower construction.")
    return _author(prim, {"newton:contactMargin": 0.0, "newton:contactGap": 0.004}, "tower construction")


QUICK_SCENE_HZ = 500  # 1 / 0.002 s, the MuJoCo timestep that exact stepping and controllers already report


def quick_scene(stage, scene, timeline, authored_rate=None):
    """Quick-scene setup under Newton: the scene's one timestep, and contact buffers sized for every body now in it.

    ``authored_rate`` is the rate something else already authored (the Jenga tower); None means author ``QUICK_SCENE_HZ``.
    Never raises: a failure is reported, and the scene it leaves is the plain one Newton would have started with."""
    report = {}
    try:
        report["rate_hz"] = authored_rate or QUICK_SCENE_HZ
        if authored_rate is None:
            report["settings"] = scene_settings(scene, QUICK_SCENE_HZ)
        config, report["buffers"] = prepare_buffers(stage, scene, timeline)
        apply_buffers(config)
    except AssetError as exc:
        report["error"] = dict(code=exc.code, message=str(exc)[:300])
    return report


# --- diagnostics --------------------------------------------------------------------------------------


def _counts(value):
    from .physics import array

    if value is None:
        return None
    values = array(value).reshape(-1)
    if any(not float(number).is_integer() or number < 0 for number in values):
        refuse("PHYSICS_STATE_INVALID", "Newton contact/constraint counts must be nonnegative integers.")
    return [int(number) for number in values]


def _warn(result, code, message):
    result["warnings"].append(dict(code=code, message=message))


def diagnostics(stage):
    """Configured buffers, and what the running solver says about them. Never raises: a failure is reported in the result.

    Active counts are the last step's. Overflow bits persist until the solver is reset, so a set bit means the buffer was
    exceeded at some earlier step this run, not necessarily now. A missing counter does not prove the buffers are big enough."""
    result = dict(
        backend="newton", configured=None, active_contacts=None, contact_capacity=None, active_constraints=None,
        constraint_capacity=None, warnings=[], overflow_flags=None, overflow_history_available=False,
        note="Configuration is process-local, not saved in USD. Counters are read from the last completed step.",
    )
    try:
        from isaacsim.physics.newton import get_newton_config

        config = get_newton_config()
        solver_config = getattr(config, "solver_cfg", None)
        result["configured"] = dict(
            nconmax=getattr(solver_config, "nconmax", None), njmax=getattr(solver_config, "njmax", None),
            rigid_contact_max=getattr(getattr(config, "collision_cfg", None), "rigid_contact_max", None))
        _runtime(stage, result)
    except AssetError as exc:
        result["runtime_status"], result["runtime_message"] = exc.code, str(exc)[:300]
    except Exception as exc:  # noqa: BLE001 - diagnostics must never break the call that carries them
        result["runtime_status"], result["runtime_message"] = "DIAGNOSTICS_FAILED", f"{type(exc).__name__}: {str(exc)[:300]}"
    return result


def _runtime(stage, result):
    from .physics import newton_stage

    try:
        native = newton_stage(stage)
    except AssetError as exc:
        if exc.code != "PHYSICS_NOT_READY":
            raise
        result["runtime_status"] = exc.code
        return
    data = getattr(getattr(native, "solver", None), "mjw_data", None)
    if data is None:
        result["runtime_status"] = "MuJoCo-Warp capacity counters unavailable (another solver, or CPU MuJoCo)"
        return
    result.update(active_contacts=_counts(getattr(data, "nacon", None)),
                  active_constraints=_counts(getattr(data, "nefc", None)),
                  contact_capacity=getattr(data, "naconmax", None),
                  constraint_capacity=getattr(data, "njmax", None), runtime_status="initialized")
    flags = _counts(getattr(data, "overflow", None))
    if flags is not None:
        result["overflow_history_available"] = True
        result["overflow_flags"] = [dict(raw=value, names=[name for bit, name in enumerate(OVERFLOW_BITS) if value & (1 << bit)],
                                         unknown_bits=value & ~((1 << len(OVERFLOW_BITS)) - 1)) for value in flags]
        if any(value & ((1 << CAPACITY_BITS) - 1) for value in flags):
            _warn(result, "NEWTON_SOLVER_OVERFLOW", "MuJoCo-Warp set a buffer-overflow bit (see overflow_flags): contacts or "
                  "constraints were dropped at some step since the last reset. Stop, raise nconmax/njmax with "
                  "sim__physics action=configure, and run again.")
        if any(value >> CAPACITY_BITS for value in flags):
            _warn(result, "NEWTON_SOLVER_NOT_CONVERGED", "The solver hit an iteration limit (ITERATIONS or LS_ITERATIONS) at some "
                  "step: motion may be inaccurate, but no buffer overflowed.")
    for field, capacity, code in (("active_contacts", "contact_capacity", "CONTACT_BUFFER_FULL"),
                                  ("active_constraints", "constraint_capacity", "CONSTRAINT_BUFFER_FULL")):
        if result[field] is not None and result[capacity] is not None:
            result[capacity] = int(result[capacity])
            if any(count >= result[capacity] for count in result[field]):
                _warn(result, code, "Capacity reached on the last step; contacts or constraints may have been dropped. "
                      "Stop and raise the buffers with sim__physics action=configure.")


# --- contacts -----------------------------------------------------------------------------------------


def contacts(paths, stage):
    """Current contact pairs touching *paths*, from Newton's contact snapshot, in the shape of the PhysX report.

    Forces are instantaneous (N), summed per body-and-collider pair, in the world frame on collider_a; there are no
    impulses. This reads the last completed step and advances nothing."""
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
        refuse("CONTACT_QUERY_UNSUPPORTED", "Contact forces need Newton's MuJoCo-Warp solver with solver_outputs.contact_forces "
               "on before initialization. CPU MuJoCo and the other solvers do not fill this snapshot; use MuJoCo-Warp, or a PhysX session.")
    model, data = getattr(native, "model", None), getattr(native, "contacts", None)
    if model is None or data is None:
        refuse("CONTACT_QUERY_UNSUPPORTED", "Newton has no initialized contact arrays. Play, step once, then pause.")
    count_ref = getattr(data, "rigid_contact_count", None)
    if count_ref is None:
        count_ref = getattr(data, "n_contacts", None)
    counts = _counts(count_ref)
    if counts is None or len(counts) != 1:
        refuse("CONTACT_QUERY_UNSUPPORTED", "Newton's contact count is unavailable; no empty report was invented.")
    count = counts[0]
    if count > MAX_SNAPSHOT:
        refuse("LIMIT", f"Newton contact snapshot exceeds {MAX_SNAPSHOT} contacts; reduce the scene.")
    force = getattr(data, "force", None)
    if force is None:
        force = getattr(data, "rigid_contact_force", None)
    if force is None:
        refuse("CONTACT_QUERY_UNSUPPORTED", "Newton contact forces are unavailable. Enable solver_outputs.contact_forces "
               "before initialization, then Stop and Play.")
    shape0, shape1 = (getattr(data, name, None) for name in ("rigid_contact_shape0", "rigid_contact_shape1"))
    if shape0 is None or shape1 is None:
        refuse("CONTACT_QUERY_UNSUPPORTED", "Newton's contact shape indices are unavailable; no pairs were guessed.")
    shape_body_ref = getattr(model, "shape_body", None)
    if shape_body_ref is None or not hasattr(model, "shape_label") or not hasattr(model, "body_label"):
        refuse("CONTACT_QUERY_UNSUPPORTED", "The Newton model lacks shape and body mappings; no pairs were guessed.")
    for source in (shape0, shape1, force, shape_body_ref):
        shape = getattr(source, "shape", ())
        if not shape or shape[0] > MAX_SNAPSHOT:
            refuse("LIMIT", f"Newton contact arrays must have a known capacity of at most {MAX_SNAPSHOT} entries.")
    first, second = np.asarray(array(shape0)).reshape(-1), np.asarray(array(shape1)).reshape(-1)
    forces = np.asarray(array(force))
    shape_body = np.asarray(array(shape_body_ref)).reshape(-1)
    shape_labels, body_labels = list(model.shape_label), list(model.body_label)
    if (forces.ndim != 2 or forces.shape[1] not in (3, 6) or count > min(len(first), len(second), len(forces))
            or not np.isfinite(forces[:count, :3]).all()):
        refuse("PHYSICS_STATE_INVALID", "Newton's contact count or force arrays are invalid; nothing truncated or nonfinite is returned.")
    grouped, truncated = {}, False
    for index in range(count):
        indices = (first[index], second[index])
        if any(not float(value).is_integer() or value < 0 or value >= min(len(shape_labels), len(shape_body)) for value in indices):
            refuse("PHYSICS_STATE_INVALID", "A Newton contact shape index is invalid.")
        colliders, bodies = [], []
        for value in indices:
            shape_index = int(value)
            body_index = shape_body[shape_index]
            if not float(body_index).is_integer() or not -1 <= body_index < len(body_labels):
                refuse("PHYSICS_STATE_INVALID", "A Newton shape-to-body index is invalid.")
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
                reports_seen=None, backend="newton", units="force in N (simulation mass*length/time^2); impulse unavailable",
                source="Newton Contacts snapshot (MuJoCo-Warp); world-frame linear force on collider_a, summed per pair",
                note="Instantaneous forces of the last completed step, not integrated impulses. No physics step was run.",
                newton=diagnostics(stage))


# --- the timestep, and the one-off push ---------------------------------------------------------------


def dt_sources(sm, native):
    """The step timestep as the two sources that must agree report it, or a refusal when they do not.

    ``sm.get_physics_dt()`` is what exact stepping and controllers use; it reads ``mjc:option:timestep`` on a MuJoCo scene.
    ``native.sim_dt`` is what Kit's own play loop steps with and what the stage clock follows; Newton parses it from the
    first authored of newton:timeStepsPerSecond, mjc:option:timestep and physxScene:timeStepsPerSecond, else 1000 Hz.
    A scene authored with only one of them has two timesteps."""
    scene_dt, newton_dt = float(sm.get_physics_dt()), getattr(native, "sim_dt", None)
    sources = dict(scene_dt=scene_dt, newton_sim_dt=None if newton_dt is None else float(newton_dt),
                   newton_substeps=int(getattr(native.cfg, "num_substeps", 1) or 1))
    if newton_dt is not None and abs(scene_dt - float(newton_dt)) > DT_TOLERANCE * max(scene_dt, float(newton_dt)):
        refuse("STEP_DT_SOURCES_DISAGREE",
               f"The scene reports a timestep of {scene_dt:.6g} s (SimulationManager, mjc:option:timestep) but Newton steps with "
               f"{float(newton_dt):.6g} s (newton:timeStepsPerSecond, else its 1000 Hz default). The clock and the dynamics would "
               "not agree. Stop, run sim__physics action=configure on the PhysicsScene with the dt you want (it authors both from "
               "one value), then Play and pause again. Nothing was stepped.", dt_sources=sources)
    return sources


def clocks(native):
    """Newton's own clock and step counter, for comparing with SimulationManager's clock when a step is checked."""
    return dict(newton_sim_time=getattr(native, "sim_time", None), newton_step_count=getattr(native, "simulation_step_count", None))


def push(stage, path, impulse, at, sm):
    """A one-off impulse on a body, as a force over the first substep of the next physics step.

    Newton has no atomic impulse call. ``apply_forces_and_torques_at_position`` writes the force into ``state_0.body_f``, which
    the next step consumes in its first substep and then clears, so force = impulse / (dt / substeps) delivers *impulse* in total."""
    from .control_run import BodyIO

    native = _newton_stage_for(stage)
    sources = dt_sources(sm, native)
    duration = sources["scene_dt"] / sources["newton_substeps"]
    force = [value / duration for value in impulse]
    body = BodyIO(path)
    body.apply(force, [0.0, 0.0, 0.0], "world", at=at)
    return dict(duration_s=duration, force=force, timestep=sources)


def _newton_stage_for(stage):
    from .physics import newton_stage

    return newton_stage(stage)

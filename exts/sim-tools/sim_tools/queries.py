"""Scene queries: PhysX raycasts and contact reports, and Newton contact snapshots (newton_support).

No app update, physics step or engine fallback. A backend without a verified equivalent refuses
with a stable code (Newton has no raycast). Nothing here invents a hit, an impulse or a force.
"""

import math

from twinsmith.project_assets import refuse

MAX_HITS = 32
MAX_CAPTURE = 1024  # contact pairs kept per report before filtering by the caller's paths
MAX_PATHS = 32


def _vector3(value, field, *, nonzero=False):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1e8 for x in value)
    ):
        refuse("INVALID_VALUE", f"{field} must be three finite numbers with magnitude <=1e8.")
    result = [float(x) for x in value]
    if nonzero and math.sqrt(sum(x * x for x in result)) < 1e-9:
        refuse("INVALID_VALUE", f"{field} must not be the zero vector.")
    return result


def _engine():
    from .physics import manager

    return manager()


def _physx_only(engine, code, what):
    if engine != "physx":
        refuse(
            code,
            f"{what} is not implemented or verified for the {engine} backend, and this tool will not "
            "fall back to PhysX or to authored USD. Use a PhysX session for this query.",
            backend=engine,
        )


def raycast(origin, direction, max_distance=100, all_hits=False, timeline=None):
    origin = _vector3(origin, "origin")
    direction = _vector3(direction, "direction", nonzero=True)
    if type(max_distance) not in (int, float) or not math.isfinite(max_distance) or not 0 < max_distance <= 100:
        refuse("LIMIT", "max_distance must be a finite number >0 and <=100 stage units.")
    if type(all_hits) is not bool:
        refuse("INVALID_VALUE", "all must be true or false.")
    length = math.sqrt(sum(x * x for x in direction))
    used_direction = [x / length for x in direction]
    engine = _engine().get_active_physics_engine()
    if engine == "newton":
        refuse("RAYCAST_UNSUPPORTED",
               "Raycasting is not available under Newton. Isaac Sim 6.2's Newton extension registers its raycast handler as an "
               "empty stub (it returns without any hit data), and Newton 1.6.1 offers no public ray query to call instead. "
               "To see what a body touches use sim__physics action=contacts; for geometry use sim__prim__bounds or sim__prim__measure; "
               "for a collision raycast use a session started on PhysX. No fallback was attempted.",
               backend=engine, alternatives=["sim__physics action=contacts", "sim__prim__bounds / sim__prim__measure", "a PhysX session"])
    _physx_only(engine, "RAYCAST_UNSUPPORTED", "Raycasting")
    base = dict(
        origin=origin,
        direction=used_direction,
        direction_input=direction,
        normalized=True,
        max_distance=float(max_distance),
        all=all_hits,
        backend=engine,
        units="stage units",
        source="PhysX scene query raycast",
        note="Direction was normalised before the query; distances are measured along it.",
    )
    if timeline is not None and timeline.is_stopped():
        result = dict(**base, scene_ready=False, hits=[])
        result["note"] = (
            "The timeline is stopped, so PhysX has no initialized query scene. Play or step, then "
            "call again. No authored-USD substitute was used."
        )
        return result
    import omni.physx

    interface = omni.physx.get_physx_scene_query_interface()
    if not all_hits:
        hit = interface.raycast_closest(origin, used_direction, float(max_distance))
        if not hit.get("hit"):
            return dict(**base, scene_ready=True, hits=[])
        return dict(**base, scene_ready=True, hits=[_closest(hit)])
    found = []

    def report(hit):
        found.append(_hit(hit))
        return len(found) < MAX_HITS

    interface.raycast_all(origin, used_direction, float(max_distance), report)
    found.sort(key=lambda item: item["distance"])
    return dict(
        **base,
        scene_ready=True,
        hits=found,
        truncated=len(found) == MAX_HITS,
    )


def _hit(hit):
    """A callback RaycastHit (snake_case) as a bounded result."""
    return dict(
        position=[float(x) for x in hit.position],
        normal=[float(x) for x in hit.normal],
        distance=float(hit.distance),
        collider=str(hit.collision) or None,
        rigid_body=str(hit.rigid_body) or None,
        face_index=int(hit.face_index),
        material=str(hit.material) or None,
    )


def _closest(hit):
    """raycast_closest's dictionary (camelCase) as the same result shape."""
    return dict(
        position=[float(x) for x in hit["position"]],
        normal=[float(x) for x in hit["normal"]],
        distance=float(hit["distance"]),
        collider=str(hit["collision"]) or None,
        rigid_body=str(hit["rigidBody"]) or None,
        face_index=int(hit["faceIndex"]),
        material=str(hit["material"]) or None,
    )


def _matches(requested, candidate):
    return candidate == requested or candidate.startswith(requested + "/") or requested.startswith(candidate + "/")


def _pairs(headers, data, paths, to_path, limit=MAX_HITS):
    """Current contact pairs (optionally touching *paths*), with their summed impulse."""
    pairs = []
    for header in headers:
        if int(header.type) == 1:  # CONTACT_LOST: no longer a current pair
            continue
        actor_a = str(to_path(header.actor0))
        actor_b = str(to_path(header.actor1))
        collider_a = str(to_path(header.collider0))
        collider_b = str(to_path(header.collider1))
        if paths is not None and not any(
            _matches(wanted, actor) for wanted in paths for actor in (actor_a, actor_b)
        ):
            continue
        impulse = [0.0, 0.0, 0.0]
        points = int(header.num_contact_data)
        for index in range(header.contact_data_offset, header.contact_data_offset + min(points, 256)):
            point = data[index].impulse
            impulse = [impulse[i] + float(point[i]) for i in range(3)]
        pairs.append(dict(
            body_a=actor_a or None,
            body_b=actor_b or None,
            collider_a=collider_a or None,
            collider_b=collider_b or None,
            contact_points=points,
            impulse=impulse,
            impulse_magnitude=math.sqrt(sum(x * x for x in impulse)),
            **({"impulse_partial": True} if points > 256 else {}),
        ))
        if len(pairs) == limit:
            break
    return pairs


# The last report PhysX delivered. A subscription is the documented way to receive contact
# reports; it is passive (no app update, no step) and stores only bounded plain data.
_LAST_REPORT = {"pairs": [], "reports": 0, "error": None, "capped": False}
_SUBSCRIPTION = None
_SUBSCRIPTION_ERROR = None


def _receive_contact_report(headers, data):
    try:
        from pxr import PhysicsSchemaTools

        pairs = _pairs(headers, data, None, PhysicsSchemaTools.intToSdfPath, limit=MAX_CAPTURE + 1)
        _LAST_REPORT["capped"] = len(pairs) > MAX_CAPTURE  # more pairs than kept: a requested body may be missing
        _LAST_REPORT["pairs"] = pairs[:MAX_CAPTURE]
        _LAST_REPORT["reports"] += 1
        _LAST_REPORT["error"] = None
    except Exception as exc:  # noqa: BLE001 - a physics callback must never raise into Kit
        _LAST_REPORT["error"] = str(exc)[:300]
        _LAST_REPORT["capped"] = False


def stop_contact_reports():
    """Release the contact-report subscription at extension shutdown or reload."""
    global _SUBSCRIPTION
    subscription, _SUBSCRIPTION = _SUBSCRIPTION, None
    if subscription is not None and callable(getattr(subscription, "unsubscribe", None)):
        subscription.unsubscribe()


def start_contact_reports():
    """Subscribe to PhysX contact reports once. Returns True when subscribed."""
    global _SUBSCRIPTION, _SUBSCRIPTION_ERROR
    if _SUBSCRIPTION is not None:
        return True
    try:
        import omni.physx

        _SUBSCRIPTION = omni.physx.get_physx_simulation_interface().subscribe_contact_report_events(
            _receive_contact_report
        )
    except Exception as exc:  # noqa: BLE001 - report why, never fall back to a guess
        _SUBSCRIPTION_ERROR = str(exc)[:300]
        return False
    _SUBSCRIPTION_ERROR = None
    return True


def contacts(paths, stage, timeline=None):
    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_PATHS:
        refuse("LIMIT", f"paths must list 1..{MAX_PATHS} absolute body or collider paths.")
    if any(not isinstance(p, str) or not p.startswith("/") for p in paths):
        refuse("INVALID_VALUE", "Every path must be an absolute prim path.")
    engine = _engine().get_active_physics_engine()
    if timeline is not None and timeline.is_stopped():
        refuse(
            "PHYSICS_NOT_READY",
            "The timeline is stopped, so there is no current contact report. Play and step, then call "
            "again while paused or playing.",
        )
    if engine == "newton":
        from .newton_support import contacts as newton_contacts

        return newton_contacts(paths, stage)
    _physx_only(engine, "CONTACT_QUERY_UNSUPPORTED", "Contact-report reading")
    try:
        from pxr import PhysxSchema
    except ImportError:
        refuse("PHYSICS_API_UNAVAILABLE", "PhysxSchema is unavailable; contact reports cannot be read.")
    reporting, not_reporting = [], []
    for value in paths:
        prim = stage.GetPrimAtPath(value)
        if not prim:
            refuse("NOT_FOUND", f"No prim at {value}. Find it with sim__prim__find or sim__physics__inspect.")
        if not prim.IsActive():
            refuse("INVALID_VALUE", f"{value} is inactive, so it has no current contact report.")
        if not (
            prim.HasAPI(PhysxSchema.PhysxContactReportAPI)
            or prim.HasAPI("PhysicsCollisionAPI")
            or prim.HasAPI("PhysicsRigidBodyAPI")
        ):
            refuse(
                "WRONG_TYPE",
                f"{value} is neither a collider nor a body with a contact report. Choose a collider path.",
            )
        if prim.HasAPI(PhysxSchema.PhysxContactReportAPI):
            reporting.append(value)
        else:
            not_reporting.append(value)
    if not start_contact_reports():
        refuse(
            "CONTACT_QUERY_UNSUPPORTED",
            "PhysX contact-report subscription is unavailable in this build: "
            + str(_SUBSCRIPTION_ERROR or "unknown reason")
            + ". No contact data was guessed.",
        )
    if _LAST_REPORT["error"]:
        refuse(
            "CONTACT_REPORT_FAILED",
            "PhysX delivered a contact report this tool could not read: " + _LAST_REPORT["error"]
            + ". Inspect the paths and retry; no impulse was guessed.",
        )
    pairs = [
        pair for pair in _LAST_REPORT["pairs"]
        if any(
            _matches(wanted, candidate)
            for wanted in paths
            for candidate in (pair["body_a"], pair["body_b"], pair["collider_a"], pair["collider_b"])
            if candidate
        )
    ]
    if not pairs and not reporting and _LAST_REPORT.get("capped"):
        refuse(
            "CONTACT_REPORT_TRUNCATED",
            f"The last report held more than {MAX_CAPTURE} contact pairs and none kept touches the requested "
            "paths, so their contacts may have been dropped. Step again or reduce the number of touching bodies.",
        )
    if not pairs and not reporting:
        refuse(
            "CONTACT_REPORT_DISABLED",
            "None of the requested paths has PhysxContactReportAPI, so PhysX produced no report for them. "
            "Apply PhysxSchema.PhysxContactReportAPI to the body (threshold 0), then Stop and Play so the "
            "actor is recreated with reporting, and step again.",
            paths_without_reporting=not_reporting[:MAX_PATHS],
        )
    result = dict(
        paths=paths,
        pairs=pairs[:MAX_HITS],
        truncated=len(pairs) > MAX_HITS or bool(_LAST_REPORT.get("capped")),
        reporting_paths=reporting,
        paths_without_reporting=not_reporting,
        reports_seen=_LAST_REPORT["reports"],
        backend=engine,
        units="impulse in kg*stage units/s (PhysX); stage units for positions",
        source="PhysX contact-report subscription; the last report delivered by a simulation step",
        note="Only bodies with PhysxContactReportAPI report contacts; a path without it is listed rather "
             "than guessed. This is the last delivered report, not a fresh query or a solver checkpoint.",
    )
    if _LAST_REPORT["reports"] == 0:
        result["next"] = (
            "Subscribed to PhysX contact reports. Step or play once, then call this tool again: no "
            "report has been delivered yet."
        )
    return result

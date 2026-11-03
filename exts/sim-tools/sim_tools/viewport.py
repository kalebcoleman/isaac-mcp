"""Point the active viewport camera without authoring into the source USD layers.

Native select-and-frame, exact eye/target placement, framing with a margin, and an opt-in
follow mode (a Kit update subscription, released on stop, stage close, camera change or error).
"""

import math

from pxr import Gf, Usd, UsdGeom, UsdPhysics

from isaac_mcp.project_assets import refuse
from .core import CAP, matrix


def checked_camera(scene, stage, context, viewport):
    """The active viewport camera if it is safe to edit in the session layer, and a revalidator."""
    if viewport is None:
        refuse("NO_VIEWPORT", "No active viewport in this Kit.")

    camera_path = str(viewport.camera_path)

    def validate():
        if (
            scene.get_stage() != stage
            or context.get_stage() != stage
            or viewport.stage != stage
            or viewport.usd_context_name != context.get_name()
            or str(viewport.camera_path) != camera_path
        ):
            refuse(
                "VIEWPORT_CHANGED",
                "The viewport camera, context or stage changed. Inspect before explicitly retrying.",
                camera=camera_path,
            )

    validate()
    camera = stage.GetPrimAtPath(camera_path)
    if (
        not camera or not camera.IsActive() or not camera.IsA(UsdGeom.Camera)
        or camera.IsInstanceProxy() or camera.IsInPrototype()
    ):
        refuse("VIEWPORT_CAMERA_UNSUPPORTED", "Choose an editable USD camera in the selected stage.")
    if not stage.GetSessionLayer().permissionToEdit:
        refuse("READ_ONLY", "The session layer is not editable; no framing was requested.")
    look_through = camera.GetRelationship("omni:kit:viewport:lookThrough:target")
    if look_through and look_through.GetForwardedTargets():
        refuse(
            "VIEWPORT_CAMERA_UNSUPPORTED",
            "This camera redirects framing to another prim. Choose a viewport camera without a look-through target.",
        )

    # Kit's transform/property commands can explicitly retarget to camera specs
    # in session sublayers, ignoring the current edit target. Do not mutate those
    # layers. Root/reference opinions are safely overridden in the session root.
    layers = stage.GetLayerStack(True)
    if len(layers) > 128 or stage.GetRootLayer() not in layers:
        refuse("VIEWPORT_CAMERA_UNSUPPORTED", "Cannot bound the viewport camera's local layer stack.")
    for layer in layers:
        if layer == stage.GetRootLayer():
            break
        if layer != stage.GetSessionLayer() and layer.GetPrimAtPath(camera_path):
            refuse(
                "VIEWPORT_CAMERA_UNSUPPORTED",
                "Camera opinions in a session sublayer can redirect native edits. Choose a camera without those opinions.",
                camera=camera_path,
            )
    return camera_path, camera, validate


def check_subtree(prim):
    traversal = Usd.PrimRange(prim, Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate))
    for index, _ in enumerate(traversal):
        if index >= CAP:
            refuse("LIMIT", f"Viewport framing is limited to {CAP} composed prims; select a smaller subtree.")


def _is_visible(prim, time):
    # A query root may be a non-imageable Scope; its nearest imageable parent
    # still supplies visibility inherited from the rest of the namespace.
    while prim and not prim.IsPseudoRoot():
        imageable = UsdGeom.Imageable(prim)
        if imageable:
            return imageable.ComputeVisibility(time) != UsdGeom.Tokens.invisible
        prim = prim.GetParent()
    return True


def _finite_bounds(prim, time):
    """Current world-aligned bounds of visible geometry. PhysX writes simulated link poses to
    USD while playing, so this follows a robot whose links move under a fixed root prim."""
    if not _is_visible(prim, time):
        return None
    bounds = UsdGeom.BBoxCache(
        time, [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy]
    ).ComputeWorldBound(prim).ComputeAlignedRange()
    if bounds.IsEmpty() or not all(
        math.isfinite(v) for p in (bounds.GetMin(), bounds.GetMax()) for v in p
    ):
        return None
    return bounds


def world_bounds(prim, time):
    bounds = _finite_bounds(prim, time)
    if bounds is None:
        refuse("NO_BOUNDS", "Selected prim has no finite visible geometry to frame.", candidates=[])
    return bounds


def frame_prim(stage, prim, time):
    """Use the requested subtree, or a nearby robot subtree when it has no bounds."""
    check_subtree(prim)
    if _finite_bounds(prim, time) is not None:
        return prim
    parent = prim.GetParent()
    if not parent or parent.IsPseudoRoot():
        refuse("NO_BOUNDS", "No finite visible geometry found near the requested prim.", candidates=[])
    # A geometry-free robot footprint often has a base_link sibling. Require
    # physics evidence on the robot or either link so ordinary scene siblings
    # are never selected silently. Check this small subtree before scanning a
    # potentially large parent.
    if prim.GetName() == "base_footprint":
        base = parent.GetChild("base_link")
        robot = base and any(
            p.HasAPI(UsdPhysics.RigidBodyAPI) or p.HasAPI(UsdPhysics.ArticulationRootAPI)
            for p in (prim, parent, base)
        )
        if robot:
            check_subtree(base)
            if _finite_bounds(base, time) is not None:
                return base
    # Bound the search even if the surrounding environment is very large.
    # Offer visible geometry paths for an explicit retry, without guessing that
    # any sibling (or the entire parent) is the requested object.
    candidates = []
    truncated = False
    traversal = iter(Usd.PrimRange(parent, Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)))
    for index, candidate in enumerate(traversal):
        if index >= CAP:
            truncated = True
            break
        # Prune invisible branches so they cannot exhaust the bounded search
        # before visible siblings are reached. Compute inherited visibility at
        # the search root; below it a local invisible opinion hides the subtree.
        imageable = UsdGeom.Imageable(candidate)
        if ((index == 0 and not _is_visible(candidate, time))
                or (imageable and imageable.GetVisibilityAttr().Get(time) == UsdGeom.Tokens.invisible)):
            traversal.PruneChildren()
            continue
        if candidate.IsA(UsdGeom.Gprim) and _finite_bounds(candidate, time) is not None:
            if len(candidates) == 20:
                truncated = True
                break
            candidates.append(str(candidate.GetPath()))
    refuse("NO_BOUNDS", "No unambiguous visible geometry subtree near the requested prim; select a candidate.",
           candidates=candidates, candidates_truncated=truncated)


def frame(scene, stage, path, context, viewport, frame_selection, undo_disabled):
    prim = scene.prim(stage, path)
    camera_path, camera, validate = checked_camera(scene, stage, context, viewport)
    # The native utility otherwise returns True even for an empty bounding box.
    prim = frame_prim(stage, prim, viewport.time)
    selected_path = str(prim.GetPath())

    context.get_selection().set_selected_prim_paths([selected_path], False)
    try:
        # These Kit commands otherwise store undo callbacks which can re-author
        # using a later edit target. Do not enqueue such callbacks for this tool.
        with undo_disabled(), Usd.EditContext(stage, stage.GetSessionLayer()):
            accepted = frame_selection(viewport)
            validate()
    except Exception as exc:
        refuse(
            "VIEWPORT_FRAME_FAILED",
            f"Native framing failed: {str(exc)[:500]}",
            camera=camera_path,
            selected_path=selected_path,
            effects="Selection and session camera edits may have occurred. Inspect before retrying; the prior edit target was restored.",
        )
    if accepted is not True:
        refuse(
            "VIEWPORT_FRAME_FAILED",
            "The native viewport utility did not confirm framing.",
            camera=camera_path,
            selected_path=selected_path,
            effects="Selection and session camera edits may have occurred; inspect before retrying.",
        )
    validate()
    camera_matrix = matrix(
        UsdGeom.Xformable(camera).ComputeLocalToWorldTransform(viewport.time)
    )
    if not all(math.isfinite(v) for row in camera_matrix for v in row):
        refuse(
            "VIEWPORT_FRAME_FAILED",
            "Native framing left a non-finite camera transform; inspect the session camera before retrying.",
            camera=camera_path,
            effects="Selection and session camera edits occurred; source layers and the prior edit target were preserved.",
        )
    return dict(
        requested_path=path,
        selected_path=selected_path,
        camera=camera_path,
        camera_world_matrix=camera_matrix,
        authored_layer="session",
        render_complete=False,
        effects="Selected prim and framed the active camera in the session layer. Source layers are unchanged; rendering and visibility are not verified.",
        capture="Allow ordinary render updates, then use screenshot with this application PID/window ID. A large subtree can frame occluding geometry; select a smaller subtree if needed.",
    )


def _up(stage):
    return Gf.Vec3d(0, 0, 1) if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z else Gf.Vec3d(0, 1, 0)


def set_camera(stage, viewport, camera_path, eye, target, undo_disabled):
    """Author a look-at pose on the viewport camera in the session layer (no undo entry)."""
    import omni.kit.commands

    from .placement import look_at_valid

    up = _up(stage)
    distance = look_at_valid(list(eye), list(target), list(up))
    eye, target = Gf.Vec3d(*eye), Gf.Vec3d(*target)
    camera = UsdGeom.Xformable(stage.GetPrimAtPath(camera_path))
    time = viewport.time
    world = Gf.Matrix4d().SetLookAt(eye, target, up).GetInverse()
    local = world * camera.ComputeParentToWorldTransform(time).GetInverse()
    with undo_disabled(), Usd.EditContext(stage, stage.GetSessionLayer()):
        omni.kit.commands.create(
            "TransformPrimCommand",
            path=camera_path,
            new_transform_matrix=local,
            old_transform_matrix=camera.GetLocalTransformation(time),
            time_code=time,
            usd_context_name=viewport.usd_context_name,
        ).do()
        # Kit's orbit/zoom tools pivot on this point: keep it on the target.
        from pxr import Sdf

        coi = camera.GetPrim().GetAttribute("omni:kit:centerOfInterest")
        if not coi:
            coi = camera.GetPrim().CreateAttribute("omni:kit:centerOfInterest", Sdf.ValueTypeNames.Vector3d, True)
        coi.Set(Gf.Vec3d(0, 0, -distance))
    return distance


def _sphere(prim, time, margin_su=0.0):
    bounds = world_bounds(prim, time)
    center = bounds.GetMidpoint()
    radius = max((bounds.GetMax() - bounds.GetMin()).GetLength() / 2, 1e-6) + margin_su
    return center, radius


def place(scene, stage, context, viewport, a, undo_disabled):
    """eye/target placement and framing with a margin. Returns (result, follow_spec or None)."""
    from .placement import PlacementError, frame_eye

    camera_path, camera, validate = checked_camera(scene, stage, context, viewport)
    path = a.get("path") or ""
    requested_path = path
    margin = a.get("margin")
    eye, target = a.get("eye"), a.get("target")
    mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
    if margin is not None and (type(margin) not in (int, float) or not math.isfinite(margin) or not 0 <= margin <= 1000):
        refuse("INVALID_VALUE", "margin must be 0..1000 metres.")
    prim = center = radius = None
    if path:
        prim = scene.prim(stage, path)
        prim = frame_prim(stage, prim, viewport.time)
        path = str(prim.GetPath())
        center, radius = _sphere(prim, viewport.time, (margin or 0.0) / mpu)
    elif target is None or eye is None:
        refuse("INVALID_VALUE", "Give path, or both eye and target.")
    elif margin is not None or a.get("follow"):
        refuse("INVALID_VALUE", "margin and follow need path.")
    cam_world = UsdGeom.Xformable(camera).ComputeLocalToWorldTransform(viewport.time)
    try:
        target = Gf.Vec3d(*target) if target is not None else center
        if eye is not None:
            eye = Gf.Vec3d(*eye)
        elif margin is not None:
            geom = UsdGeom.Camera(camera)
            res = viewport.resolution
            forward = cam_world.TransformDir(Gf.Vec3d(0, 0, -1))
            eye_list, _ = frame_eye(
                list(target), radius, list(forward), geom.GetFocalLengthAttr().Get(),
                geom.GetHorizontalApertureAttr().Get(), res[0] / max(res[1], 1),
            )
            eye = Gf.Vec3d(*eye_list)
        else:
            eye = cam_world.ExtractTranslation()  # follow alone: keep the current camera position
        for v in (*eye, *target):
            if not math.isfinite(v) or abs(v) > 1e8:
                refuse("INVALID_POSE", "eye and target must be finite coordinates with magnitude <= 1e8.")
        distance = set_camera(stage, viewport, camera_path, eye, target, undo_disabled)
    except PlacementError as e:
        refuse(e.code, str(e), camera=camera_path)
    validate()
    result = dict(
        camera=camera_path,
        eye=list(eye),
        target=list(target),
        distance=distance,
        units="stage units",
        authored_layer="session",
        render_complete=False,
        capture="Allow ordinary render updates, then use screenshot with this application PID/window ID.",
    )
    if prim is not None:
        result.update(path=path, requested_path=requested_path, selected_path=path,
                      prim_center=list(center), bounding_radius=radius,
                      margin_m=margin or 0.0)
    follow = None
    if a.get("follow"):
        follow = dict(camera_path=camera_path, path=path, validate=validate,
                      offset=eye - center, target_offset=target - center)
    return result, follow


def _physics_time():
    """The physics simulation time (advances on every step, paused or playing); None if unavailable."""
    try:
        from .physics import manager

        return manager().get_simulation_time()
    except Exception:  # noqa: BLE001 - no physics: the timeline time alone decides
        return None


class Follower:
    """Keeps one prim in view while the timeline runs. One Kit update subscription at most."""

    def __init__(self):
        self._sub = None
        self._spec = None
        self.last = None  # why the last follow ended

    @property
    def active(self):
        return self._sub is not None

    def status(self):
        if self._spec is None:
            return dict(following=False, last_stop=self.last)
        return dict(following=True, path=self._spec["path"], camera=self._spec["camera_path"],
                    updates=self._spec["updates"])

    def stop(self, reason):
        if self._sub is None:
            return False
        sub, self._sub = self._sub, None
        try:
            sub.unsubscribe()
        except Exception:
            pass
        spec, self._spec = self._spec, None
        self.last = dict(path=spec["path"], reason=reason, updates=spec["updates"])
        return True

    def start(self, scene, stage, viewport, timeline, spec, undo_disabled):
        import omni.kit.app

        self.stop("replaced by a new follow")
        spec = dict(spec, stage=stage, viewport=viewport, timeline=timeline, undo=undo_disabled,
                    updates=0, prim=stage.GetPrimAtPath(spec["path"]))
        self._spec = spec
        self._sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
            self._tick, name="isaac-mcp viewport follow"
        )

    def _tick(self, _event):
        spec = self._spec
        if spec is None or spec["timeline"].is_stopped():
            return  # paused stepping still moves bodies; Stop resets them, so keep the last view
        try:
            spec["validate"]()  # cheap; a camera switch must stop the follow even while paused
            prim = spec["prim"]
            if not prim or not prim.IsValid() or not prim.IsActive():
                self.stop("followed prim was removed or deactivated")
                return
            # Skip the subtree bounds pass when nothing can have moved. Keyed on the physics clock as
            # well as the timeline: paused sim.simulation.step moves bodies without advancing the
            # timeline, and articulation links move while the followed root prim's own pose does not.
            key = (spec["viewport"].time, _physics_time())
            if spec.get("last_key") == key:
                return
            spec["last_key"] = key
            center, _ = _sphere(prim, spec["viewport"].time)
            set_camera(spec["stage"], spec["viewport"], spec["camera_path"],
                       center + spec["offset"], center + spec["target_offset"], spec["undo"])
            spec["updates"] += 1
        except Exception as exc:  # never raise inside Kit's update loop
            self.stop(f"stopped: {str(exc)[:300]}")

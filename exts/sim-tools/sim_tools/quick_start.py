"""sim.quick_start: one call that builds a working scene for anyone, from public or approved files.

A ground plane, physics scene, lights and an aimed camera, optionally with a robot. It wires nothing
to a real robot: no mirror graph, no /joint_states subscriber, no gripper, force publisher or panel.
That is the UR5e twin's job (ur5e_dt__quick_start). Like every generic tool it authors in the session
layer; sim.stage.save flattens that layer into the saved file, so a save keeps the result.

``resolve_robot`` and ``author_scene`` are pure USD and resolver code, tested without Kit.
"""

from __future__ import annotations

import math
import os
import re

from isaac_mcp.project_assets import AssetError, refuse
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics

from .public_robots import NVIDIA_ROBOTS
from .stage_open import KIT_DEFAULT_ROOTS, SUFFIXES, stage_has_unsaved_work

# Name -> (file in the twin's pack, prim name): the local copy wins, then NVIDIA. NVIDIA_ROBOTS come from the
# running build's own assets root instead.
PUBLIC_ROBOTS = {"ur5e": ("robot/ur5e.usd", "UR5e")}
PUBLIC_PACK = "ur5e-dt"
WORLD, GROUND, PHYSICS_SCENE = "/World", "/World/GroundPlane", "/World/PhysicsScene"
DOME, SUN, CAMERA = "/World/DomeLight", "/World/SunLight", "/World/Camera"
GROUND_HALF_SIZE_M = 25.0
# Lengths below are metres in a Z-up layout. _stage_point turns them into the stage's axis and units.
# Framing an empty scene: above and to the side of the origin. A robot's own bounds replace this.
EYE, EYE_TARGET = (2.2, 2.2, 1.5), (0.0, 0.0, 0.35)
VIEW_DIRECTION = Gf.Vec3d(1.0, 1.0, 0.7).GetNormalized()
FOCAL_LENGTH, HORIZONTAL_APERTURE, CAPTURE_ASPECT = 24.0, 20.955, 4.0 / 3.0
MIN_FRAMED_RADIUS_M = 0.3
ROOM_TO_MOVE = 1.5  # an arm's first pose can be its flattest: leave room for it to rise


def _resolved(relpath, pack, label, name):
    """A resolver answer under the assets__* rules: the first local copy wins, then NVIDIA's URL if this
    file has one and strict mode is off. A project store is searched but is not an approval."""
    from isaac_mcp import assets

    resolver = assets.resolver(pack)
    report = resolver.asset_source(relpath)
    if report["source"] == "missing":
        refuse("ASSET_MISSING", f"{label} has no local copy" + (" and strict mode forbids the download."
               if resolver.remote_uri(relpath) else ", and it has no public source. See docs/ASSETS.md."), asset=relpath)
    if report["source"] == "project":
        refuse("ROOT_NOT_APPROVED", f"{label} would come from a project file's asset_path ({report['location']}), which "
               "is not an approval, because a cloned repository could name any folder. Approve that folder as a "
               "trusted root, or put the file in your asset folder. See docs/ASSETS.md.", asset=relpath)
    try:
        # A plain path for a local file: USD resolves it the same with or without Kit's URI resolver.
        uri = resolver.resolve_uri(relpath) if report["source"] == "remote" else str(resolver.resolve_path(relpath))
    except assets.AssetCorrupt as exc:
        refuse("ASSET_INVALID", str(exc)[:500], asset=relpath)
    except assets.AssetMissing as exc:
        refuse("ASSET_MISSING", str(exc)[:500], asset=relpath)
    return dict(name=name, uri=uri, assets=[report])


def resolve_robot(robot, snapshot_id=None):
    """What ``robot`` names, before anything changes. A public name, ``<pack>/<file>``, or a path the host
    turned into a verified snapshot under an approved root or in a pack. A URDF always comes as a snapshot."""
    if robot in PUBLIC_ROBOTS:
        relpath, name = PUBLIC_ROBOTS[robot]
        return _resolved(relpath, PUBLIC_PACK, f"The {robot} robot ({relpath})", name)
    known = ", ".join(sorted({*PUBLIC_ROBOTS, *NVIDIA_ROBOTS}))
    if isinstance(robot, str) and "://" in robot:
        refuse("INVALID_VALUE", f"robot {robot!r} is a URL, and this tool does not load URLs. Public names: {known}. For your own "
               "file: <pack>/<file.usd>, or an absolute path under an approved root. Nothing was changed.")
    if not isinstance(robot, str) or not robot.strip():
        refuse("INVALID_VALUE", f"robot: a public name ({known}), <pack>/<file.usd> or an absolute path under an approved root.")
    is_urdf = robot.lower().endswith(".urdf")
    if snapshot_id and is_urdf:
        return dict(name=_prim_name(robot), snapshot=True, urdf=True, assets=[dict(asset=robot, source="snapshot", location=robot)])
    if is_urdf:
        refuse("ROOT_NOT_APPROVED", "A URDF goes through the MCP host, which checks it against your approved roots and "
               "asset folder. Nothing was changed.")
    if os.path.isabs(os.path.expanduser(robot)):
        if not snapshot_id:
            refuse("ROOT_NOT_APPROVED", "An absolute robot path goes through the MCP host, which checks it against your "
                   "approved roots (the same rule as assets__import_path). Nothing was changed.")
        return dict(name=_prim_name(robot), snapshot=True, assets=[dict(asset=robot, source="approved_root", location=robot)])
    pack, _, relpath = robot.partition("/")
    if not relpath:
        refuse("INVALID_VALUE", f"Unknown robot {robot!r}. Public names: {known}. For your own file: <pack>/<file.usd>, "
               "or an absolute path under an approved root.")
    from isaac_mcp import assets

    try:
        pack = assets.validate_pack_name(pack)
    except ValueError as exc:
        refuse("INVALID_VALUE", str(exc)[:300])
    if ".." in relpath.split("/") or not relpath.lower().endswith(SUFFIXES):
        refuse("INVALID_VALUE", f"{relpath} must be a USD file ({', '.join(SUFFIXES)}) inside the pack.")
    return _resolved(relpath, pack, f"{relpath} in pack {pack}", _prim_name(relpath))


def _prim_name(path):
    name = re.sub(r"[^A-Za-z0-9_]", "_", os.path.splitext(os.path.basename(path))[0]) or "Robot"
    return f"Robot_{name}" if name[0].isdigit() else name


def check_units(up_axis, meters_per_unit):
    """The requested stage axis and units, or a refusal before anything changes."""
    if up_axis not in ("Z", "Y"):
        refuse("INVALID_VALUE", f"up_axis must be Z or Y, not {up_axis!r}. Nothing was changed.")
    if type(meters_per_unit) not in (int, float) or not math.isfinite(meters_per_unit) or meters_per_unit <= 0:
        refuse("INVALID_VALUE", f"meters_per_unit must be a positive number, not {meters_per_unit!r}. Nothing was changed.")
    return up_axis, float(meters_per_unit)


def refuse_units(what, file_up, file_units, up_axis, meters_per_unit):
    refuse("UNITS_MISMATCH", f"This scene would be {up_axis} up with {meters_per_unit:g} m per unit, but {what} is {file_up} "
           f"up with {file_units:g} m per unit; there is no automatic conversion. Nothing was changed. Resend with "
           f"up_axis=\"{file_up}\" and meters_per_unit={file_units:g} to match it.",
           scene_units=dict(up_axis=up_axis, meters_per_unit=meters_per_unit),
           file_units=dict(up_axis=file_up, meters_per_unit=file_units),
           retry_with=dict(up_axis=file_up, meters_per_unit=file_units))


def file_units(uri):
    """(up axis, metres per unit) a USD file declares, with USD's fallbacks when it declares none."""
    stage = Usd.Stage.Open(uri, Usd.Stage.LoadNone)
    return str(UsdGeom.GetStageUpAxis(stage)), float(UsdGeom.GetStageMetersPerUnit(stage))


def check_resolved_units(wanted, up_axis, meters_per_unit):
    """Refuse a local file that does not match. A remote one may be unreachable from here, so a failed read
    of it only means unknown; the reference then reports its own errors."""
    remote = wanted["assets"][0]["source"] == "remote"
    try:
        up, units = file_units(wanted["uri"])
    except Exception as exc:  # noqa: BLE001 - USD raises Tf.ErrorException
        if remote:
            return False
        refuse("IMPORT_FAILED", f"Could not read {wanted['uri']}: {str(exc)[:400]}. Nothing was changed.")
    if up != up_axis or not math.isclose(units, meters_per_unit, rel_tol=1e-9):
        refuse_units(wanted["uri"], up, units, up_axis, meters_per_unit)
    return True


def _stage_layout(stage):
    """(up axis, stage units per metre), from the root layer's metadata."""
    return str(UsdGeom.GetStageUpAxis(stage)), 1.0 / UsdGeom.GetStageMetersPerUnit(stage)


def _stage_point(point, up_axis, scale=1.0):
    """A Z-up point in the stage's axis, scaled. Y up maps (a, b, height) to (a, height, -b): a rotation, so
    handedness and face winding hold."""
    a, b, height = (value * scale for value in point)
    return Gf.Vec3d(a, b, height) if up_axis == "Z" else Gf.Vec3d(a, height, -b)


def author_scene(stage):
    """Author the base scene in the session layer, following the stage's up axis and units; returns its paths."""
    up, units = _stage_layout(stage)
    point = lambda *xyz: Gf.Vec3f(_stage_point(xyz, up, units))  # noqa: E731
    up_vector = Gf.Vec3f(_stage_point((0, 0, 1), up))
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdGeom.Xform.Define(stage, WORLD)
        scene = UsdPhysics.Scene.Define(stage, PHYSICS_SCENE)
        scene.CreateGravityDirectionAttr(-up_vector)
        scene.CreateGravityMagnitudeAttr(9.81 * units)  # stage units per second squared
        # What Isaac's own ground plane uses: a mesh to see and an infinite plane to collide with.
        UsdGeom.Xform.Define(stage, GROUND)
        half = GROUND_HALF_SIZE_M
        mesh = UsdGeom.Mesh.Define(stage, GROUND + "/Geometry")
        corners = [point(-half, -half, 0), point(half, -half, 0), point(half, half, 0), point(-half, half, 0)]
        mesh.CreatePointsAttr(corners)
        mesh.CreateFaceVertexCountsAttr([4])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        mesh.CreateNormalsAttr([up_vector] * 4)
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        mesh.CreateExtentAttr(UsdGeom.PointBased.ComputeExtent(corners))
        mesh.CreateDisplayColorAttr([(0.42, 0.44, 0.47)])
        plane = UsdGeom.Plane.Define(stage, GROUND + "/CollisionPlane")
        plane.CreateAxisAttr(UsdGeom.Tokens.z if up == "Z" else UsdGeom.Tokens.y)
        plane.CreatePurposeAttr(UsdGeom.Tokens.guide)
        UsdPhysics.CollisionAPI.Apply(plane.GetPrim())
        dome = UsdLux.DomeLight.Define(stage, DOME)
        dome.CreateIntensityAttr(600.0)
        sun = UsdLux.DistantLight.Define(stage, SUN)
        sun.CreateIntensityAttr(1500.0)
        sun.CreateAngleAttr(1.0)
        tilt = (Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), 45.0))
                * Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), 30.0)))
        if up == "Y":
            tilt = tilt * Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), -90.0))
        sun.AddTransformOp().Set(tilt)
        camera = UsdGeom.Camera.Define(stage, CAMERA)
        camera.CreateClippingRangeAttr(Gf.Vec2f(0.01 * units, 1000.0 * units))
        camera.CreateFocalLengthAttr(FOCAL_LENGTH)
        camera.CreateHorizontalApertureAttr(HORIZONTAL_APERTURE)
        camera.AddTransformOp().Set(_aim(_stage_point(EYE, up, units), _stage_point(EYE_TARGET, up, units), up))
    return dict(world=WORLD, ground=GROUND, physics_scene=PHYSICS_SCENE, lights=[DOME, SUN], camera=CAMERA)


def _aim(eye, target, up_axis="Z"):
    return Gf.Matrix4d().SetLookAt(eye, target, _stage_point((0, 0, 1), up_axis)).GetInverse()


def frame_camera_on(stage, path):
    """Aim the camera so the whole prim fits, at any robot's size. Returns the aimed-at centre, or None
    when the prim has no bounds yet (a remote file still loading), which keeps the default aim."""
    from .placement import fit_distance

    box = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]).ComputeWorldBound(
        stage.GetPrimAtPath(path)).ComputeAlignedRange()
    if box.IsEmpty():
        return None
    center = box.GetMidpoint()
    up, units = _stage_layout(stage)
    radius = max(box.GetSize().GetLength() / 2.0 * ROOM_TO_MOVE, MIN_FRAMED_RADIUS_M * units)
    distance = fit_distance(radius, FOCAL_LENGTH, HORIZONTAL_APERTURE, CAPTURE_ASPECT)
    center = center + _stage_point((0, 0, 1), up) * radius / 3  # robots rise from a first pose; the first pose still fits
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        eye = center + _stage_point(VIEW_DIRECTION, up) * distance
        UsdGeom.Xformable(stage.GetPrimAtPath(CAMERA)).GetOrderedXformOps()[0].Set(_aim(eye, center, up))
    return center


def reference_robot(stage, name, uri):
    """Reference the robot under /World in the session layer; returns its prim path."""
    path = f"{WORLD}/{name}"
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        prim = stage.DefinePrim(path, "Xform")
        if not prim.GetReferences().AddReference(uri):
            refuse("IMPORT_FAILED", f"Could not reference {uri}.")
    return path


def expand_visuals(stage, root_path):
    """Instanced robot visuals stayed at their first pose in the renderer on some Kit builds after physics
    moved the links. Expanding them is a session-layer override, never an edit to the asset. Returns the count."""
    changed = 0
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for prim in Usd.PrimRange(stage.GetPrimAtPath(root_path)):
            if prim.GetName() == "visuals" and prim.IsInstance() and prim.SetInstanceable(False):
                changed += 1
    return changed


def next_steps(ids, robot_path, root, play, wheels=None):
    ids_text = f'session_id="{ids["session_id"]}", stage_id="{ids["stage_id"]}"'
    steps = []
    if not play:
        steps.append(f"Play: sim__timeline__control with {ids_text}, action=\"play\".")
    if root:
        steps += [
            "A robot starts at its all-zero joint pose. For an arm that can be low and stretched out, which can look collapsed; check the joint state before assuming a fault. A joint driven into the ground just stops, with no error, so raise a low arm first (on a UR5e, shoulder_lift_joint to about -1.57).",
            f"Read joints while playing or paused: sim__joints__state with path=\"{root}\".",
            f"Move: sim__joints__command with path=\"{root}\" and one position per joint, in radians.",
            "Exact steps: pause with sim__timeline__control, then sim__simulation__step.",
            f"ROS 2 TF and joint states: sim__ros2__tf_setup with robot=\"{robot_path}\" on a stopped timeline.",
        ]
    if wheels:
        steps = [step for step in steps if not step.startswith(("A robot starts", "Move:"))]
        steps += [
            "Drive from ROS 2: on a stopped timeline, pass robot.cmd_vel_graph to sim__graph__build, then Play and publish "
            "geometry_msgs/Twist on /cmd_vel.",
            f"For a wheeled base, sim__ros2__tf_setup takes base=\"mobile\", world_frame=\"\" and base_link=\"{wheels['base_link']}\" "
            "with localization, which publishes map->odom. Use Quick Start's ground as the floor: see docs/ROS2-RVIZ.md.",
        ]
    steps += [
        f"Look: sim__camera__capture with path=\"{CAMERA}\", filename=\"scene.png\", then read the file.",
        "Keep it: sim__stage__save. These edits are in the session layer, and the saved file contains them.",
    ]
    return steps


async def run(ext, args):
    """The handler. Everything that can be refused is refused before the stage changes."""
    import omni.kit.app
    import omni.usd

    from .compat import composition_errors
    from .core import stop_call
    from .public_robots import assets_root, client_stat, locate
    from .ros2 import cmd_vel_graph_args
    from .ros2 import _articulation_roots
    from .ros2 import setup as ros_setup

    scene = ext.scene
    app = omni.kit.app.get_app()
    if not app.is_app_ready():
        refuse("APP_STARTING", "Kit startup is still running. Wait until sim__stage__info reports app_ready=true; "
               "nothing was changed.")
    robot, ros, play = args.get("robot"), args.get("ros", False), args.get("play", False)
    fixed_base = args.get("fixed_base", True)
    if type(fixed_base) is not bool:
        refuse("INVALID_VALUE", "fixed_base must be a boolean.")
    discard = args.get("discard_changes", False)
    if any(type(value) is not bool for value in (ros, play, discard)):
        refuse("INVALID_VALUE", "ros, play and discard_changes must be booleans.")
    if ros and not robot:
        refuse("INVALID_VALUE", "ros=true needs a robot to publish. Nothing was changed.")
    up_axis, meters_per_unit = check_units(args.get("up_axis", "Z"), args.get("meters_per_unit", 1.0))
    if not scene.timeline.is_stopped():
        info = scene.stage_info()
        refuse("TIMELINE_ACTIVE", "Stop the timeline first: call sim__timeline__control action=stop, then resend this call.",
               before_retry=stop_call(info["session_id"], info["stage_id"]))
    snapshot = args.get("snapshot_id"), args.get("manifest_sha256")
    if robot in NVIDIA_ROBOTS:
        from isaac_mcp import assets

        wanted = await locate(robot, assets_root(), client_stat, offline=assets.resolver(PUBLIC_PACK).offline())
    else:
        wanted = resolve_robot(robot, snapshot[0]) if robot else None
    is_urdf = bool(wanted and wanted.get("urdf"))
    if is_urdf and (up_axis, meters_per_unit) != ("Z", 1.0):
        # Both importers write Z up in metres, whatever the stage is.
        refuse_units("a URDF", "Z", 1.0, up_axis, meters_per_unit)
    if wanted and wanted.get("snapshot") and not is_urdf:
        info = scene.inspect_asset(*snapshot)[2]
        if info["up_axis"] != up_axis or not math.isclose(info["meters_per_unit"], meters_per_unit, rel_tol=1e-9):
            refuse_units("the robot file", info["up_axis"], info["meters_per_unit"], up_axis, meters_per_unit)
        scene.validate_project_materials(*snapshot)
    units_checked = check_resolved_units(wanted, up_axis, meters_per_unit) if wanted and not wanted.get("snapshot") else True

    ctx = omni.usd.get_context()
    stage = ctx.get_stage()
    reuse = bool(stage) and stage.GetRootLayer().anonymous and all(
        p.GetName() in KIT_DEFAULT_ROOTS for p in stage.GetPseudoRoot().GetChildren())
    discarded = False
    if not reuse:
        discarded = bool(stage) and stage_has_unsaved_work(stage)
        if discarded and not discard:
            refuse("UNSAVED_CHANGES", "The current stage has edits this file does not hold. sim__stage__save writes them to a new "
                   "file but does not clear this, so after saving, or to drop them, pass discard_changes=true.",
                   source=stage.GetRootLayer().identifier)
        ok, error = await ctx.new_stage_async()
        if not ok:
            refuse("OPEN_FAILED", f"Kit could not make a new stage: {str(error)[:300]}")
        for _ in range(10):  # let stage events reach the scene tools and the bridge sessions
            await app.next_update_async()
        stage = ctx.get_stage()
    # Root-layer metadata, before anything is authored: the scene below reads it back.
    if UsdGeom.GetStageUpAxis(stage) != up_axis:
        UsdGeom.SetStageUpAxis(stage, up_axis)
    if UsdGeom.GetStageMetersPerUnit(stage) != meters_per_unit:
        UsdGeom.SetStageMetersPerUnit(stage, meters_per_unit)

    paths = author_scene(stage)
    stage.GetRootLayer().defaultPrim = WORLD.lstrip("/")
    result = dict(
        **paths, up_axis=up_axis, meters_per_unit=meters_per_unit, stage_switched=not reuse, discarded_changes=discarded,
        layer="session; sim__stage__save flattens it into the saved file",
        wiring="none: no mirror graph, gripper or /joint_states subscriber. For the RISE lab UR5e twin see ur5e_dt__quick_start.",
    )
    owned = [WORLD]
    root = robot_path = wheels = None
    if wanted:
        if is_urdf:
            robot_path = f"{WORLD}/{wanted['name']}"
            converted = ext.advanced.import_urdf(stage, *snapshot, robot_path, fixed_base=fixed_base)
            urdf_report = {k: converted[k] for k in ("base", "packages", "mapped_files", "placement", "warnings")}
            urdf_report["drives"] = converted["converted_physics"].get("drives")
        elif wanted.get("snapshot"):
            robot_path = f"{WORLD}/{wanted['name']}"
            scene.import_path(stage, *snapshot, robot_path)
        else:
            robot_path = reference_robot(stage, wanted["name"], wanted["uri"])
        await app.next_update_async()
        roots = _articulation_roots(stage.GetPrimAtPath(robot_path))
        errors = composition_errors(stage)
        if not stage.GetPrimAtPath(robot_path).GetChildren():
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                stage.RemovePrim(robot_path)
            refuse("IMPORT_FAILED", f"The robot file composed to nothing at {robot_path}: {str(errors)[:600]}. "
                   "The rest of the scene was built.", asset=wanted["assets"], **paths)
        expanded = expand_visuals(stage, robot_path)
        frame_camera_on(stage, robot_path)
        root = roots[0] if roots else None
        wheels = wanted.get("wheels")
        result["robot"] = dict(prim=robot_path, articulation_root=root, articulation_roots=roots,
                               expanded_visuals=expanded, composition_errors=len(errors),
                               **({"composition_error_samples": [str(e)[:200] for e in errors[:3]]} if errors else {}),
                               **({} if root else {"note": "No articulation root found, so the joint tools will not work on it."}))
        if is_urdf:
            result["robot"]["urdf"] = urdf_report
        result["assets"] = wanted["assets"]
        if wheels and root:
            result["robot"].update(wheels=wheels, cmd_vel_graph=cmd_vel_graph_args(root, wheels))
        if not units_checked:
            result["robot"]["units_note"] = "The remote file's up axis and units could not be read beforehand; check the result."
    # stage_info first: on a new stage it resets the owned set, and this registration must come after that.
    # Each prim by name, because tools that change a prim check its exact path.
    info = scene.stage_info()
    scene.owned.update(owned + [GROUND, PHYSICS_SCENE, DOME, SUN, CAMERA] + ([robot_path] if robot_path else []))
    if ros:
        shell = (info.get("ros") or {}).get("shell")
        try:
            mobile = dict(base="mobile", world_frame="", base_link=wheels["base_link"]) if wanted.get("wheels") else {}
            done = ros_setup(scene, stage, dict(robot=robot_path, **mobile))
            result["ros"] = dict(graph=done["graph"], topics=done["topics"], rviz_fixed_frame=done["rviz_fixed_frame"],
                                 clock=done["clock"]["source"], warnings=done["warnings"], shell=shell)
        except AssetError as exc:  # the scene exists: say why ROS is not up, and keep it
            result["ros"] = dict(error=exc.code, message=str(exc)[:600], shell=shell)
    ids = dict(session_id=info["session_id"], stage_id=info["stage_id"])
    if play:
        await ext._timeline_control(dict(ids, action="play"))
        for _ in range(4):  # articulations initialize on the frames after Play
            await app.next_update_async()
    result.update(ids, playing=scene.timeline.is_playing(), stopped=scene.timeline.is_stopped(),
                  next_steps=next_steps(ids, robot_path, root, play, wanted.get("wheels") if wanted else None))
    return result

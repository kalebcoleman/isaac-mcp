"""sim.ros2.tf_setup: one consistent ROS 2 clock/TF/joint-state graph for a robot.

The layout follows NVIDIA's own graph helpers shipped with Isaac Sim 6.2
(``isaacsim.ros2.nodes`` ``impl/graph_api.py``: ``create_ros2_clock_graph``,
``create_ros2_tf_graph``, ``create_ros2_odometry_graph``) but only uses node
types and inputs that exist in every Isaac Sim 6.x release, so it also works on
6.0/6.1. One graph, rebuilt from scratch on every call, so repeated setup never
leaves a second publisher for the same frame behind.

Frame tree (REP 105 names):

    <world_frame> --static--> <base_frame> --(Isaac link TF)--> every robot link
    <world_frame> --static--> odom --(odometry)--> <base_frame>      (base="mobile")

``planned_graph`` is pure (no Kit imports) so the layout can be unit-tested.
"""

from __future__ import annotations

import math
import os
import re

GRAPH_PATH = "/Graph/isaac_mcp_ros2"
FRAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TOPIC_RE = re.compile(r"^/?[A-Za-z_][A-Za-z0-9_/]*$")
CLOCK, CONTEXT, TIME, TICK = "PublishClock", "Context", "ReadSimTime", "OnPlaybackTick"


def _frame(name, what):
    if not isinstance(name, str) or not FRAME_RE.match(name):
        raise ValueError(f"{what} {name!r} is not a valid ROS frame name (letters, digits, _; not starting with a digit)")
    return name


def planned_graph(*, robot, base_prim, base_frame, base_pose, mode="fixed", world_frame="world",
                  link_frames="isaac", extra_prims=(), namespace="", joint_states_topic="joint_states"):
    """Return ``(nodes, values, connects, frames)`` for the graph.

    ``base_pose`` is ``((x, y, z) metres, (qx, qy, qz, qw))`` of the base link in the world at
    setup time; it becomes the static ``world -> base`` (fixed) or ``world -> odom`` (mobile)
    transform. ``frames`` describes the resulting tree for the caller.
    """
    if mode not in ("fixed", "mobile"):
        raise ValueError("base must be 'fixed' or 'mobile'")
    if link_frames not in ("isaac", "robot_state_publisher"):
        raise ValueError("link_frames must be 'isaac' or 'robot_state_publisher'")
    _frame(base_frame, "base frame")
    if world_frame:
        _frame(world_frame, "world_frame")
        if world_frame in (base_frame, "odom" if mode == "mobile" else None):
            raise ValueError("world_frame must differ from the base frame (and from 'odom' for a mobile base)")
    if namespace and not re.match(r"^[A-Za-z_][A-Za-z0-9_/]*$", namespace):
        raise ValueError(f"namespace {namespace!r} is not a valid ROS namespace")
    if not TOPIC_RE.match(joint_states_topic or ""):
        raise ValueError(f"joint_states_topic {joint_states_topic!r} is not a valid topic")
    (tx, ty, tz), (qx, qy, qz, qw) = base_pose
    if not all(math.isfinite(v) for v in (tx, ty, tz, qx, qy, qz, qw)):
        raise ValueError("base pose is not finite")

    nodes = [
        (TICK, "omni.graph.action.OnPlaybackTick"),
        (CONTEXT, "isaacsim.ros2.bridge.ROS2Context"),
        (TIME, "isaacsim.core.nodes.IsaacReadSimulationTime"),
        (CLOCK, "isaacsim.ros2.bridge.ROS2PublishClock"),
        ("PublishJointState", "isaacsim.ros2.bridge.ROS2PublishJointState"),
    ]
    values = [
        (f"{TIME}.inputs:resetOnStop", True),
        (f"{CLOCK}.inputs:topicName", "clock"),
        ("PublishJointState.inputs:targetPrim", robot),
        ("PublishJointState.inputs:topicName", joint_states_topic.lstrip("/")),
        ("PublishJointState.inputs:nodeNamespace", namespace),
    ]
    publishers = [CLOCK, "PublishJointState"]
    connects = []
    frames = {"links": "isaac" if link_frames == "isaac" else "robot_state_publisher (from /joint_states)"}
    edges = []

    def raw_tf(name, parent, child, static, translation=None, rotation=None):
        nodes.append((name, "isaacsim.ros2.bridge.ROS2PublishRawTransformTree"))
        values.extend([
            (f"{name}.inputs:parentFrameId", parent),
            (f"{name}.inputs:childFrameId", child),
            (f"{name}.inputs:staticPublisher", static),
            (f"{name}.inputs:nodeNamespace", namespace),
            (f"{name}.inputs:topicName", "tf_static" if static else "tf"),
        ])
        if translation is not None:
            values.append((f"{name}.inputs:translation", list(translation)))
            values.append((f"{name}.inputs:rotation", list(rotation)))
        publishers.append(name)
        edges.append(dict(parent=parent, child=child, static=static, source=name))

    if mode == "fixed":
        if world_frame:
            raw_tf("TFWorldToBase", world_frame, base_frame, True, (tx, ty, tz), (qx, qy, qz, qw))
    else:
        if world_frame:
            raw_tf("TFWorldToOdom", world_frame, "odom", True, (tx, ty, tz), (qx, qy, qz, qw))
        nodes.extend([
            ("ComputeOdometry", "isaacsim.core.nodes.IsaacComputeOdometry"),
            ("PublishOdometry", "isaacsim.ros2.bridge.ROS2PublishOdometry"),
        ])
        values.extend([
            ("ComputeOdometry.inputs:chassisPrim", robot),
            ("PublishOdometry.inputs:topicName", "odom"),
            ("PublishOdometry.inputs:odomFrameId", "odom"),
            ("PublishOdometry.inputs:chassisFrameId", base_frame),
            ("PublishOdometry.inputs:nodeNamespace", namespace),
        ])
        raw_tf("TFOdomToBase", "odom", base_frame, False)
        connects.extend([
            (f"{TICK}.outputs:tick", "ComputeOdometry.inputs:execIn"),
            ("ComputeOdometry.outputs:execOut", "PublishOdometry.inputs:execIn"),
            ("ComputeOdometry.outputs:execOut", "TFOdomToBase.inputs:execIn"),
            ("ComputeOdometry.outputs:position", "PublishOdometry.inputs:position"),
            ("ComputeOdometry.outputs:orientation", "PublishOdometry.inputs:orientation"),
            ("ComputeOdometry.outputs:linearVelocity", "PublishOdometry.inputs:linearVelocity"),
            ("ComputeOdometry.outputs:angularVelocity", "PublishOdometry.inputs:angularVelocity"),
            ("ComputeOdometry.outputs:position", "TFOdomToBase.inputs:translation"),
            ("ComputeOdometry.outputs:orientation", "TFOdomToBase.inputs:rotation"),
        ])
        publishers.append("PublishOdometry")

    if link_frames == "isaac":
        # Whole articulation, relative to the base link: base_frame -> every link (prim names).
        nodes.append(("TFRobotLinks", "isaacsim.ros2.bridge.ROS2PublishTransformTree"))
        values.extend([
            ("TFRobotLinks.inputs:parentPrim", base_prim),
            ("TFRobotLinks.inputs:targetPrims", [robot]),
            ("TFRobotLinks.inputs:topicName", "tf"),
            ("TFRobotLinks.inputs:nodeNamespace", namespace),
        ])
        publishers.append("TFRobotLinks")
        edges.append(dict(parent=base_frame, child="<robot links>", static=False, source="TFRobotLinks"))
    if extra_prims:
        nodes.append(("TFExtraFrames", "isaacsim.ros2.bridge.ROS2PublishTransformTree"))
        values.extend([
            ("TFExtraFrames.inputs:parentPrim", base_prim),
            ("TFExtraFrames.inputs:targetPrims", list(extra_prims)),
            ("TFExtraFrames.inputs:topicName", "tf"),
            ("TFExtraFrames.inputs:nodeNamespace", namespace),
        ])
        publishers.append("TFExtraFrames")
        edges.append(dict(parent=base_frame, child="<extra prims>", static=False, source="TFExtraFrames"))

    for name in publishers:
        connects.append((f"{CONTEXT}.outputs:context", f"{name}.inputs:context"))
        connects.append((f"{TIME}.outputs:simulationTime", f"{name}.inputs:timeStamp"))
        if name not in ("PublishOdometry", "TFOdomToBase"):  # driven by ComputeOdometry
            connects.append((f"{TICK}.outputs:tick", f"{name}.inputs:execIn"))
    frames["edges"] = edges
    frames["root"] = world_frame or ("odom" if mode == "mobile" else base_frame)
    return nodes, values, connects, frames


# ----------------------------------------------------------------- Kit side

def _links(stage, robot_prim, limit=512):
    """Rigid-body link prims below *robot_prim* (bounded), as (path, name)."""
    from pxr import Usd, UsdPhysics

    found = []
    for prim in Usd.PrimRange(robot_prim):
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            found.append((str(prim.GetPath()), prim.GetName()))
            if len(found) >= limit:
                break
    return found


def _articulation_roots(robot_prim, limit=2000):
    """Prims at or below *robot_prim* that carry the ArticulationRootAPI (often a fixed root
    joint such as ``<robot>/root_joint``, not the robot's top prim). The TF, joint-state and
    odometry nodes need this exact prim: given only the top prim, the TF node publishes that one
    prim instead of the whole link tree."""
    from pxr import Usd, UsdPhysics

    found = []
    for i, prim in enumerate(Usd.PrimRange(robot_prim)):
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            found.append(str(prim.GetPath()))
        if i >= limit:
            break
    return found


def setup(scene, stage, a):
    """Build (or rebuild) the graph. The timeline is stopped (checked by Scene.target)."""
    from isaac_mcp.project_assets import refuse
    from pxr import Usd, UsdGeom

    robot_path = a["robot"]
    robot = scene.prim(stage, robot_path)
    roots = _articulation_roots(robot)
    if not roots:
        refuse("NOT_ARTICULATION", f"{robot_path} has no articulation root at or below it; pass the robot's root prim.")
    if len(roots) > 1:
        refuse("SEVERAL_ARTICULATIONS", f"{robot_path} contains {len(roots)} articulations ({', '.join(roots[:4])}); "
               "pass a prim that contains one of them; publish frames of the others with extra_frames.")
    articulation = roots[0]
    links = _links(stage, robot)
    if not links:
        refuse("NO_LINKS", f"No rigid-body links found below {robot_path}.")
    base_path = a.get("base_link") or ""
    if not base_path:
        named = [p for p, n in links if n in ("base_link", "base_footprint", "base")]
        if not named:
            refuse("BASE_LINK_REQUIRED", "Pass base_link: the robot's root link prim. Links found: "
                   + ", ".join(n for _, n in links[:12]))
        base_path = named[0]
    base = scene.prim(stage, base_path)
    base_frame = a.get("base_frame") or base.GetName()

    names = {}
    for p, n in links:
        names.setdefault(n, []).append(p)
    duplicates = {n: ps for n, ps in names.items() if len(ps) > 1}
    if duplicates and a.get("link_frames", "isaac") == "isaac":
        refuse("DUPLICATE_FRAME_NAMES", "Several links share a name, so their TF frames would collide: "
               + "; ".join(f"{n}: {', '.join(ps[:3])}" for n, ps in list(duplicates.items())[:5])
               + ". Rename them (or set isaac:nameOverride) first.")

    extra = list(a.get("extra_frames") or [])
    if len(extra) > 16:
        refuse("LIMIT", "At most 16 extra_frames.")
    for p in extra:
        prim = scene.prim(stage, p)
        if prim.GetName() in names:
            refuse("DUPLICATE_FRAME_NAMES", f"extra frame {p} has the same name as a robot link.")

    world = UsdGeom.XformCache().GetLocalToWorldTransform(base)
    mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
    t = world.ExtractTranslation() * mpu
    q = world.RemoveScaleShear().ExtractRotationQuat().GetNormalized()
    pose = ((t[0], t[1], t[2]), (q.GetImaginary()[0], q.GetImaginary()[1], q.GetImaginary()[2], q.GetReal()))
    try:
        nodes, values, connects, frames = planned_graph(
            robot=articulation, base_prim=base_path, base_frame=base_frame, base_pose=pose,
            mode=a.get("base", "fixed"), world_frame=a.get("world_frame", "world"),
            link_frames=a.get("link_frames", "isaac"), extra_prims=extra,
            namespace=a.get("namespace", ""), joint_states_topic=a.get("joint_states_topic", "joint_states"),
        )
    except ValueError as exc:
        refuse("INVALID_VALUE", str(exc))

    import omni.graph.core as og

    existing = bool(stage.GetPrimAtPath(GRAPH_PATH))  # read before removal: a removed prim is falsy
    if existing:
        if GRAPH_PATH not in scene.owned:
            refuse("CONFLICT", f"{GRAPH_PATH} exists and was not created by this tool in this session; "
                   "remove it or start a new stage.")
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            stage.RemovePrim(GRAPH_PATH)
    from pxr import Sdf

    keys = og.Controller.Keys
    # Relationship ("target") inputs take a path string, or a list of Sdf.Path for several targets,
    # exactly as NVIDIA's graph_api sets them.
    set_values = [
        (attr, [Sdf.Path(v) for v in value] if attr.endswith("targetPrims") else value)
        for attr, value in values
    ]
    try:
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            og.Controller.edit(
                {"graph_path": GRAPH_PATH, "evaluator_name": "execution"},
                {keys.CREATE_NODES: nodes, keys.CONNECT: connects, keys.SET_VALUES: set_values},
            )
    except Exception as exc:  # node types missing: the ROS 2 bridge extension is not enabled
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if stage.GetPrimAtPath(GRAPH_PATH):
                stage.RemovePrim(GRAPH_PATH)
        scene.owned.discard(GRAPH_PATH)
        refuse("ROS2_UNAVAILABLE", f"Could not build the ROS 2 graph ({str(exc)[:300]}). Launch Kit with "
               "isaacsim.ros2.bridge enabled (run.sh does) and a ROS 2 environment it can load.")
    scene.owned.add(GRAPH_PATH)

    ns = ("/" + a.get("namespace").strip("/")) if a.get("namespace") else ""
    topics = ["/clock", f"{ns}/tf", f"{ns}/tf_static", ns + "/" + a.get("joint_states_topic", "joint_states").lstrip("/")]
    if a.get("base", "fixed") == "mobile":
        topics.append(f"{ns}/odom")
    fixed = frames["root"]
    rsp = a.get("link_frames", "isaac") == "robot_state_publisher"
    return dict(
        graph=GRAPH_PATH,
        robot=robot_path,
        articulation_root=articulation,
        base_link=base_path,
        base_frame=base_frame,
        frames=frames,
        link_count=len(links),
        topics=topics,
        ros_domain_id=os.environ.get("ROS_DOMAIN_ID", "0 (unset)"),
        rviz_fixed_frame=fixed,
        publishes_when="timeline is playing (OnPlaybackTick); stamps use simulation time",
        next_steps=[
            "Play the timeline (sim__timeline__control action=play).",
            "Every ROS 2 node, RViz included, must use use_sim_time:=true and the same ROS_DOMAIN_ID.",
            f"python3 scripts/ros2_rviz_config.py --fixed-frame {fixed} --out <file>.rviz "
            + ("--robot-model " if rsp else "") + "then: rviz2 -d <file>.rviz --ros-args -p use_sim_time:=true",
            f"python3 scripts/ros2_tf_doctor.py --fixed-frame {fixed}  (with ROS 2 sourced) before trusting RViz",
        ] + ([
            "Run robot_state_publisher with the robot's URDF and use_sim_time:=true; it turns /joint_states "
            "into the link frames. Isaac does not publish them in this mode, so nothing is published twice.",
        ] if rsp else [
            "Do NOT also run robot_state_publisher for this robot: Isaac already publishes its link frames, "
            "and two publishers for the same frames make RViz jump. Use link_frames=robot_state_publisher instead "
            "if you need a URDF RobotModel in RViz.",
        ]) + ([
            "Do not run AMCL/SLAM with world_frame='map': they publish map->odom themselves. Use world_frame='' "
            "(Isaac publishes only odom->base) when a localization node provides map->odom.",
        ] if a.get("base", "fixed") == "mobile" else []),
        rebuilt=existing,
    )

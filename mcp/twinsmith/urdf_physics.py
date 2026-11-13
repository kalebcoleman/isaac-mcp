"""Read a robot's physics from its URDF: default drive gains and warnings. Standard library only.

A URDF has no position gains, so the importer authors drives with stiffness 0 and targets do
nothing. The gains here hold a pose: stiff enough that gravity moves a joint by about
``DROOP_RAD``, and critically damped for the mass the joint carries. All of it is computed at
the zero pose, with moment arms taken at their largest, so they are upper bounds, not a tuned controller.
"""

from __future__ import annotations

import math
import re

GRAVITY = 9.81
DROOP_RAD = math.radians(0.5)
DROOP_M = 0.001
BANDWIDTH_RAD_S = 2 * math.pi * 2.0
MIN_INERTIA = 1e-4  # kg m^2 or kg: a floor so a massless subtree still gets a usable drive
TOO_SMALL_FACTOR = 25.0
TINY_RADIUS_M = 0.01
MOVABLE = ("revolute", "continuous", "prismatic")
WHEEL_RADIUS_M = 0.1  # a typical size: a wheel's drive also moves the robot it carries


def _floats(text, count, default):
    try:
        values = [float(x) for x in (text or "").split()]
    except ValueError:
        return list(default)
    return values if len(values) == count and all(math.isfinite(v) for v in values) else list(default)


def _number(element, name, default=None):
    if element is None:
        return default
    try:
        value = float(element.attrib[name])
    except (KeyError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _rotation(rpy):
    roll, pitch, yaw = rpy
    cr, sr, cp, sp, cy, sy = (math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch),
                              math.cos(yaw), math.sin(yaw))
    return [
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _apply(m, v):
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def _transpose(m):
    return [[m[j][i] for j in range(3)] for i in range(3)]


def _origin(element):
    origin = element.find("origin") if element is not None else None
    if origin is None:
        return [0.0, 0.0, 0.0], _rotation((0.0, 0.0, 0.0))
    return (_floats(origin.attrib.get("xyz"), 3, (0, 0, 0)),
            _rotation(_floats(origin.attrib.get("rpy"), 3, (0, 0, 0))))


def _primitive_moment(link):
    """The largest principal moment per kg of the link's box, cylinder or sphere, or None for a mesh."""
    largest = None
    for section in link.findall("collision") or link.findall("visual"):
        shape = section.find("geometry")
        if shape is None:
            continue
        box, cylinder, sphere = shape.find("box"), shape.find("cylinder"), shape.find("sphere")
        moment = None
        if box is not None:
            x, y, z = sorted(_floats(box.attrib.get("size"), 3, (0, 0, 0)))
            moment = (y * y + z * z) / 12
        elif cylinder is not None:
            r, h = _number(cylinder, "radius", 0.0), _number(cylinder, "length", 0.0)
            moment = max((3 * r * r + h * h) / 12, r * r / 2)
        elif sphere is not None:
            moment = 0.4 * _number(sphere, "radius", 0.0) ** 2
        if moment is None:
            return None
        largest = max(largest or 0.0, moment)
    return largest


def read_model(tree):
    """Links and joints, with each link's mass, centre and inertia in the world at the zero pose."""
    links, joints = {}, []
    for link in tree.findall("link"):
        inertial = link.find("inertial")
        origin, rotation = _origin(inertial)
        mass = _number(inertial.find("mass"), "value", 0.0) if inertial is not None else 0.0
        element = inertial.find("inertia") if inertial is not None else None
        values = {k: _number(element, k, 0.0) for k in ("ixx", "ixy", "ixz", "iyy", "iyz", "izz")}
        inertia = [[values["ixx"], values["ixy"], values["ixz"]],
                   [values["ixy"], values["iyy"], values["iyz"]],
                   [values["ixz"], values["iyz"], values["izz"]]]
        links[link.attrib.get("name", "")] = dict(
            mass=mass, com=origin, inertia=_mul(_mul(rotation, inertia), _transpose(rotation)),
            has_inertial=inertial is not None, primitive=_primitive_moment(link), element=link,
        )
    for joint in tree.findall("joint"):
        parent, child = joint.find("parent"), joint.find("child")
        if parent is None or child is None:
            continue
        xyz, rotation = _origin(joint)
        axis = _floats(joint.find("axis").attrib.get("xyz") if joint.find("axis") is not None else None, 3, (1, 0, 0))
        limit = joint.find("limit")
        joints.append(dict(
            name=joint.attrib.get("name", ""), type=joint.attrib.get("type", ""), parent=parent.attrib.get("link"),
            child=child.attrib.get("link"), xyz=xyz, rotation=rotation, axis=axis,
            effort=_number(limit, "effort"), mimic=joint.find("mimic") is not None,
            lower=_number(limit, "lower"), upper=_number(limit, "upper"),
        ))
    children, is_child = {}, set()
    for joint in joints:
        children.setdefault(joint["parent"], []).append(joint)
        is_child.add(joint["child"])
    roots = [name for name in links if name not in is_child]
    pose = {name: ([0.0, 0.0, 0.0], _rotation((0.0, 0.0, 0.0))) for name in roots}
    stack, seen = list(roots), set(roots)
    while stack:
        name = stack.pop()
        position, rotation = pose[name]
        for joint in children.get(name, []):
            child = joint["child"]
            if child in seen or child not in links:
                continue
            seen.add(child)
            placed = [p + q for p, q in zip(position, _apply(rotation, joint["xyz"]))]
            turned = _mul(rotation, joint["rotation"])
            pose[child] = (placed, turned)
            joint["world_point"], joint["world_axis"] = placed, _apply(turned, joint["axis"])
            stack.append(child)
    for name, link in links.items():
        if name not in pose:
            continue
        position, rotation = pose[name]
        link["world_com"] = [p + q for p, q in zip(position, _apply(rotation, link["com"]))]
        link["world_inertia"] = _mul(_mul(rotation, link["inertia"]), _transpose(rotation))
    return dict(links=links, joints=joints, children=children, roots=roots)


def _subtree(model, start):
    names, stack = [], [start]
    while stack:
        name = stack.pop()
        if name in names:
            continue
        names.append(name)
        stack += [joint["child"] for joint in model["children"].get(name, [])]
    return [n for n in names if "world_com" in model["links"].get(n, {})]


def joint_load(model, joint):
    """(inertia about the axis, largest gravity load) carried by a joint, at the zero pose."""
    point, axis = joint.get("world_point"), joint.get("world_axis")
    if point is None:
        return 0.0, 0.0
    size = math.sqrt(sum(a * a for a in axis)) or 1.0
    axis = [a / size for a in axis]
    inertia = load = 0.0
    for name in _subtree(model, joint["child"]):
        link = model["links"][name]
        mass = max(link["mass"], 0.0)
        if joint["type"] == "prismatic":
            inertia += mass
            load += GRAVITY * mass
            continue
        arm = [c - p for c, p in zip(link["world_com"], point)]
        along = sum(a * b for a, b in zip(arm, axis))
        off_axis = [a - along * b for a, b in zip(arm, axis)]
        inertia += mass * sum(a * a for a in off_axis)
        inertia += max(sum(a * b for a, b in zip(axis, _apply(link["world_inertia"], axis))), 0.0)
        load += GRAVITY * mass * math.sqrt(sum(a * a for a in arm))
    return inertia, load


def default_gains(model):
    """Joint name -> stiffness, damping and load, in SI units per radian or metre."""
    gains = {}
    total_mass = sum(max(link["mass"], 0.0) for link in model["links"].values())
    for joint in model["joints"]:
        if joint["type"] not in MOVABLE:
            continue
        inertia, load = joint_load(model, joint)
        inertia = max(inertia, MIN_INERTIA)
        if joint["type"] == "continuous":
            # It may be a wheel, which carries the whole robot through the ground, not just itself. Sized
            # for the wheel alone, the robot's momentum overruns the drive and the wheel keeps spinning.
            inertia = max(inertia, total_mass * WHEEL_RADIUS_M**2)
        linear = joint["type"] == "prismatic"
        droop = DROOP_M if linear else DROOP_RAD
        stiffness = max(inertia * BANDWIDTH_RAD_S**2, load / droop)
        damping = 2.0 * math.sqrt(stiffness * inertia)
        gains[joint["name"]] = dict(stiffness=stiffness, damping=damping, load=load, inertia=inertia, linear=linear,
                                    continuous=joint["type"] == "continuous")
    return gains


def usd_gains(gain):
    """USD drive units: an angular drive's gains are per degree, a prismatic one's per stage unit."""
    scale = 1.0 if gain["linear"] else math.pi / 180.0
    return gain["stiffness"] * scale, gain["damping"] * scale


def warnings(model):
    """Say which links and joints look unusable. Nothing is changed: the source file stays as it is."""
    found = []
    movable = {joint["child"] for joint in model["joints"] if joint["type"] in MOVABLE}
    massless, flat = [], []
    for name, link in model["links"].items():
        mass = link["mass"]
        diagonal = [link["inertia"][i][i] for i in range(3)]
        if name in movable and (not link["has_inertial"] or mass <= 0):
            massless.append(name)
            continue
        if not link["has_inertial"] or mass <= 0:
            continue
        if min(diagonal) <= 0 or any(diagonal[i] > diagonal[(i + 1) % 3] + diagonal[(i + 2) % 3] + 1e-9 for i in range(3)):
            flat.append(name)
            continue
        largest = max(diagonal)
        expected = link["primitive"]
        if expected and largest < mass * expected / TOO_SMALL_FACTOR:
            found.append(f"Link {name}: inertia {largest:g} is about {mass * expected / largest:.0f} times too small for "
                         f"its {mass:g} kg and its shape. Light inertia makes contact unstable: fix the URDF.")
        elif mass >= 1.0 and math.sqrt(largest / mass) < TINY_RADIUS_M:
            found.append(f"Link {name}: inertia {largest:g} is very small for {mass:g} kg (as if it were under "
                         f"{TINY_RADIUS_M * 100:.0f} cm in size). Light inertia makes contact unstable: fix the URDF.")
    if massless:
        found.append(f"Moving links with no mass in the URDF: {massless[:10]}. The simulator supplies a value that may be "
                     "far off, so check the result.")
    if flat:
        found.append(f"Links with an inertia that is not physically possible: {flat[:10]}. Fix the URDF, or the "
                     "simulator may replace or reject it.")
    weak = []
    for joint in model["joints"]:
        if joint["type"] not in MOVABLE or joint["mimic"]:
            continue
        _, load = joint_load(model, joint)
        if joint["effort"] is not None and 0 < joint["effort"] < load:
            weak.append(f"{joint['name']} ({joint['effort']:g} vs {load:.3g})")
    outside = [joint["name"] for joint in model["joints"] if joint["type"] in ("revolute", "prismatic")
               and joint["lower"] is not None and joint["upper"] is not None and joint["lower"] < joint["upper"]
               and not joint["lower"] <= 0.0 <= joint["upper"]]
    if outside:
        found.append(f"The all-zero pose is outside these joints' limits, so they jump to a limit on Play: {outside[:12]}. "
                     "A free-standing robot can fall over when that happens.")
    if weak:
        found.append("Effort limits below the largest gravity load, so these can sag at a stretched pose: " + ", ".join(weak[:10]) + ".")
    return found


def usd_name(name):
    """A URDF joint name as a USD prim name."""
    return re.sub(r"\W", "_", name)

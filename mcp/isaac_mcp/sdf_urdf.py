"""A restricted SDF (Gazebo / SDFormat) reader that writes a URDF. Standard library only; nothing in the file is run.

Isaac Sim ships no SDF importer, so a Gazebo model is converted on the host to the URDF that the checked URDF import already
reads. What is converted: one model's links (pose, mass and inertia, visual and collision geometry: box, sphere, cylinder,
mesh, a plane as a thin box, a capsule as a cylinder), its joints (fixed, revolute, continuous, prismatic, a ball as three
continuous joints, universal as two) with axes, limits, damping and friction, and material colours. What is dropped is listed in
the result: plugins (never run), sensors, lights, physics settings, other models, frames, and geometry kinds a URDF cannot hold.
Mesh files stay in the approved folder: ``model://name/path`` and relative names are resolved there and nowhere else.

``convert(entry, project, model=None)`` returns the URDF text, the model it chose, the other model names, and warnings.
"""

from __future__ import annotations

import math
import posixpath
import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

from .project_assets import AssetError, refuse
from .urdf_refs import MAX_XML, SCHEME, Project, _parent, normal_path

MAX_LINKS = 2000
MAX_INCLUDES = 64
HUGE = 1e9  # a revolute joint with limits this wide is a continuous one
OPEN_SLIDE = 1000.0  # metres each way, for a prismatic joint with no limit
DUMMY_MASS = 1e-3
DUMMY_INERTIA = 1e-6
_JOINTS = {"revolute", "continuous", "prismatic", "fixed", "ball", "universal", "revolute2"}


def _fail(message, code="SDF_XML"):
    raise AssetError(code, message)


def is_sdf(path) -> bool:
    return PurePosixPath(str(path)).suffix.lower() in (".sdf", ".world")


# ------------------------------------------------------------ small pose algebra (row-major 4x4 as nested lists)

def _identity():
    return [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _inverse(m):
    r = [[m[j][i] for j in range(3)] for i in range(3)]
    t = [m[i][3] for i in range(3)]
    inv = [r[i] + [-sum(r[i][k] * t[k] for k in range(3))] for i in range(3)]
    return inv + [[0, 0, 0, 1.0]]


def _pose_of(element) -> list:
    """A ``<pose>`` element as a matrix, reading its ``degrees`` and ``rotation_format`` attributes; identity when there is none."""
    if element is None:
        return _identity()
    return _pose(element.text, (element.get("degrees") or "").strip().lower() in ("true", "1"), (element.get("rotation_format") or "").strip())


def _pose(text, degrees=False, rotation_format=None) -> list:
    """An SDF ``x y z roll pitch yaw`` (radians unless *degrees*, fixed axes), or ``x y z qx qy qz qw``, as a matrix."""
    values = [0.0] * 6
    if text and text.strip():
        parts = text.split()
        if rotation_format not in (None, "", "euler_rpy", "quat_xyzw"):
            _fail(f"A pose's rotation_format {rotation_format[:40]!r} is not one SDF defines.")
        wanted = 7 if rotation_format == "quat_xyzw" else 6
        if len(parts) != wanted:
            _fail(f"A pose needs {wanted} numbers, not {text[:60]!r}.")
        try:
            values = [float(v) for v in parts]
        except ValueError:
            _fail(f"A pose has a value that is not a number: {text[:60]!r}.")
        if not all(math.isfinite(v) for v in values):
            _fail("A pose has a value that is not finite.")
        if wanted == 7:
            qx, qy, qz, qw = values[3:]
            norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
            if norm < 1e-9:
                _fail("A pose's quaternion is all zeros.")
            qx, qy, qz, qw = qx / norm, qy / norm, qz / norm, qw / norm  # directly to a matrix: going through angles loses a 90-degree pitch
            return [[1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw), values[0]],
                    [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw), values[1]],
                    [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy), values[2]],
                    [0, 0, 0, 1.0]]
        if degrees:
            values[3:] = [math.radians(v) for v in values[3:]]
    x, y, z, roll, pitch, yaw = values
    cr, sr, cp, sp, cy, sy = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
    return [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr, x],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr, y],
            [-sp, cp * sr, cp * cr, z],
            [0, 0, 0, 1.0]]


def _xyz_rpy(m):
    sp = -m[2][0]
    pitch = math.asin(max(-1.0, min(1.0, sp)))
    if abs(sp) < 0.999999:
        roll = math.atan2(m[2][1], m[2][2])
        yaw = math.atan2(m[1][0], m[0][0])
    else:  # gimbal lock: put it all in yaw
        roll = 0.0
        yaw = math.atan2(-m[0][1], m[1][1])
    return (m[0][3], m[1][3], m[2][3]), (roll, pitch, yaw)


def _num(value) -> str:
    text = f"{value:.9g}"
    return "0" if text in ("-0", "") else text


def _vec(text, count, what):
    try:
        values = [float(v) for v in (text or "").split()]
    except ValueError:
        _fail(f"{what}: {str(text)[:60]!r} is not numbers.")
    if len(values) != count or not all(math.isfinite(v) for v in values):
        _fail(f"{what}: expected {count} finite numbers, got {str(text)[:60]!r}.")
    return values


def _float(element, default=0.0):
    if element is None or element.text is None or not element.text.strip():
        return default
    try:
        value = float(element.text)
    except ValueError:
        _fail(f"<{element.tag}> is not a number: {element.text[:40]!r}.")
    if not math.isfinite(value):
        return default
    return value


# ------------------------------------------------------------ reading files

def _parse(project: Project, rel: str) -> ET.Element:
    if rel not in project.files:
        _fail(f"{rel} is not in the approved folder.", "NOT_FOUND")
    if project._unsafe(rel):
        _fail(f"{rel} is behind a symlink, which is refused.", "UNSAFE_ENTRY")
    data = project.read(rel)
    if len(data) > MAX_XML:
        refuse("LIMIT", f"{rel} exceeds 64 MiB.")
    if b"\x00" in data:
        _fail(f"{rel}: only UTF-8 text is supported.")
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        _fail(f"{rel}: DTDs and entities are not supported.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        _fail(f"{rel} is not valid XML: {exc}")
    if root.tag != "sdf":
        _fail(f"{rel}: the root element must be sdf, not {str(root.tag)[:40]}.")
    return root


class _Converter:
    def __init__(self, project: Project, entry: str):
        self.project = project
        self.entry = entry
        self.warnings = []
        self.dropped = {}
        self.includes = 0
        self.ids = 0

    def warn(self, text):
        if text not in self.warnings and len(self.warnings) < 50:
            self.warnings.append(text)

    def drop(self, kind):
        self.dropped[kind] = self.dropped.get(kind, 0) + 1

    # -- finding the model
    def models(self, root, current):
        """``[(name, element, file)]`` for the models of an sdf: its own, its world's, and models it includes."""
        found = []
        holders = [root] + root.findall("world")
        for holder in holders:
            for model in holder.findall("model"):
                found.append((model.get("name", "model"), model, current))
            for include in holder.findall("include"):
                self.includes += 1
                if self.includes > MAX_INCLUDES:
                    _fail("Too many <include> elements.", "LIMIT")
                located = self._include(include, current)
                if located is not None:
                    found.append(located)
        return found

    def _include(self, include, current):
        uri = (include.findtext("uri") or "").strip()
        folder, file = self._model_folder(uri, current)
        if file is None:
            self.warn(f"<include> {uri[:60]!r} was not found in the approved folder and was left out.")
            return None
        sub = _parse(self.project, file)
        inner = sub.findall("model")
        if not inner:
            self.warn(f"{file} has no model.")
            return None
        name = (include.findtext("name") or inner[0].get("name") or "model").strip()
        model = inner[0]
        override = include.find("pose")
        return name, model, file if override is None else file  # a pose on an include is the model's placement: not needed here

    def _model_folder(self, uri, current):
        if SCHEME.match(uri) and not uri.startswith("model://"):
            _fail(f"{uri[:80]!r}: only model:// names and paths inside the approved folder are supported.", "UNSUPPORTED_URI")
        if uri.startswith("model://"):
            name = uri[len("model://"):].strip("/").split("/")[0]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}", name):
                _fail(f"{uri[:80]!r} is not a model name.", "INVALID_PATH")
            folders = [f for f in self.project.folders_named(name)]
            for folder in folders:
                for candidate in ("model.sdf", "model.sdf.xml"):
                    path = f"{folder}/{candidate}"
                    if path in self.project.files:
                        return folder, path
                config = f"{folder}/model.config"
                if config in self.project.files:
                    try:
                        for sdf in _parse(self.project, config).iter("sdf"):
                            if sdf.text and sdf.text.strip():
                                path = normal_path(folder, sdf.text.strip())
                                if path and path in self.project.files:
                                    return folder, path
                    except (AssetError, OSError):
                        pass  # no usable config: the include is reported as not found
            return None, None
        path = normal_path(_parent(current), uri)
        return (_parent(path), path) if path and path in self.project.files else (None, None)

    # -- one model
    def convert_model(self, name, model, model_file):
        self.model_file = model_file or self.entry  # a relative mesh uri is relative to the file that names it
        links = {}
        order = []
        for link in model.findall("link"):
            lname = link.get("name")
            if not lname:
                _fail("A link has no name.")
            if lname in links:
                _fail(f"Two links are named {lname!r}.")
            links[lname] = link
            order.append(lname)
            if len(order) > MAX_LINKS:
                _fail("Too many links.", "LIMIT")
        if not links:
            _fail(f"Model {name!r} has no links.")
        for kind in ("model", "frame", "plugin", "include"):
            for _ in model.findall(kind):
                self.drop(kind)
        if model.findall("model"):
            self.warn("Nested models were left out: only this model's own links are converted.")
        pose_of = {lname: _pose_of(links[lname].find("pose")) for lname in order}
        for lname in order:
            if links[lname].find("pose") is not None and links[lname].find("pose").get("relative_to"):
                self.warn(f"Link {lname!r} has a pose relative_to another frame; it is read relative to the model.")
        joints = []
        for joint in model.findall("joint"):
            jname, jtype = joint.get("name"), (joint.get("type") or "").strip()
            parent, child = (joint.findtext("parent") or "").strip(), (joint.findtext("child") or "").strip()
            if jtype not in _JOINTS:
                self.warn(f"Joint {jname!r} has type {jtype!r}, which a URDF cannot hold; it is fixed.")
                jtype = "fixed"
            if child not in links:
                self.warn(f"Joint {jname!r} names a child link {child!r} that is not in the model; it was left out.")
                continue
            if parent != "world" and parent not in links:
                self.warn(f"Joint {jname!r} names a parent link {parent!r} that is not in the model; it was left out.")
                continue
            joints.append(dict(name=jname or f"joint_{len(joints)}", type=jtype, parent=parent, child=child, element=joint))
        children = {j["child"] for j in joints if j["parent"] != "world"}
        world_joint = {j["child"] for j in joints if j["parent"] == "world"}
        roots = [lname for lname in order if lname not in children]
        if not roots:
            _fail("The links form a loop with no root link.")
        fixed_to_world = bool(world_joint) or (model.findtext("static") or "").strip().lower() in ("true", "1")
        # Several roots (for example a static prop beside the body): hang the others on the first with fixed joints.
        base = roots[0]
        for extra in roots[1:]:
            joints.append(dict(name=f"fixed_{extra}", type="fixed", parent=base, child=extra, element=None, synthetic=True))
            children.add(extra)
            self.warn(f"Link {extra!r} has no joint; it is fixed to {base!r}.")
        # Frames: a link's URDF frame is its joint frame; the root's is its own pose.
        frame = {base: pose_of[base]}
        by_parent = {}
        for j in joints:
            if j["parent"] != "world":
                by_parent.setdefault(j["parent"], []).append(j)
        extra_links, urdf_joints, seen = [], [], {base}
        queue = [base]
        while queue:
            current = queue.pop(0)
            for j in by_parent.get(current, []):
                child = j["child"]
                if child in seen:
                    _fail(f"Link {child!r} has two parents (a closed loop): a URDF is a tree.", "SDF_LOOP")
                seen.add(child)
                element = j["element"]
                jpose = _pose_of(element.find("pose")) if element is not None else _identity()
                if element is not None and element.find("pose") is not None and element.find("pose").get("relative_to"):
                    self.warn(f"Joint {j['name']!r} has a pose relative_to another frame; it is read in the child link's frame.")
                joint_frame = _mul(pose_of[child], jpose)
                frame[child] = joint_frame
                origin = _mul(_inverse(frame[current]), joint_frame)
                urdf_joints.append((j, current, child, origin, joint_frame))
                queue.append(child)
        missing = [lname for lname in order if lname not in seen]
        if missing:
            _fail(f"Links {', '.join(missing[:4])} are not connected to the root.", "SDF_LOOP")
        # Write the URDF.
        out = ['<?xml version="1.0"?>', f'<robot name="{self._attr(name)}">']
        for lname in order:
            out.extend(self._link(lname, links[lname], pose_of[lname], frame[lname]))
        for j, parent, child, origin, joint_frame in urdf_joints:
            out.extend(self._joint(j, parent, child, origin, joint_frame, extra_links))
        for text in extra_links:
            out.append(text)
        out.append("</robot>")
        return "\n".join(out) + "\n", fixed_to_world, [j["name"] for j, *_ in urdf_joints]

    @staticmethod
    def _attr(value) -> str:
        return (str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;"))

    def _origin(self, matrix, indent="    "):
        xyz, rpy = _xyz_rpy(matrix)
        return f'{indent}<origin xyz="{" ".join(_num(v) for v in xyz)}" rpy="{" ".join(_num(v) for v in rpy)}"/>'

    def _link(self, lname, link, link_pose, urdf_frame):
        to_frame = _mul(_inverse(urdf_frame), link_pose)  # an element's pose in this link, as the URDF frame sees it
        lines = [f'  <link name="{self._attr(lname)}">']
        inertial = link.find("inertial")
        if inertial is not None:
            mass = _float(inertial.find("mass"), 0.0)
            inertia = inertial.find("inertia")
            if mass > 0:
                ip = _mul(to_frame, _pose_of(inertial.find("pose")))
                parts = {k: _float(inertia.find(k) if inertia is not None else None, 0.0) for k in ("ixx", "ixy", "ixz", "iyy", "iyz", "izz")}
                lines += ["    <inertial>", self._origin(ip, "      "), f'      <mass value="{_num(mass)}"/>',
                          '      <inertia ' + " ".join(f'{k}="{_num(v)}"' for k, v in parts.items()) + "/>", "    </inertial>"]
        for tag in ("visual", "collision"):
            for index, element in enumerate(link.findall(tag)):
                geometry = element.find("geometry")
                shape = self._geometry(geometry, f"{lname}/{tag}")
                if shape is None:
                    continue
                pose = _mul(to_frame, _pose_of(element.find("pose")))
                name = element.get("name") or f"{tag}_{index}"
                lines.append(f'    <{tag} name="{self._attr(name)}">')
                lines.append(self._origin(pose, "      "))
                lines.append("      <geometry>")
                lines.append("        " + shape[0])
                lines.append("      </geometry>")
                if tag == "visual":
                    color = self._color(element.find("material"))
                    if color:
                        self.ids += 1
                        lines.append(f'      <material name="material_{self.ids}"><color rgba="{color}"/></material>')
                    shape[1]  # noqa: B018 (the shape warning was recorded when it was built)
                lines.append(f"    </{tag}>")
        for kind in ("sensor", "light", "audio_sink", "projector"):
            for _ in link.findall(kind):
                self.drop(kind)
        lines.append("  </link>")
        return lines

    def _color(self, material):
        if material is None:
            return None
        for key in ("diffuse", "ambient"):
            text = material.findtext(key)
            if text:
                try:
                    values = [float(v) for v in text.split()]
                except ValueError:
                    return None
                if len(values) in (3, 4) and all(math.isfinite(v) for v in values):
                    if len(values) == 3:
                        values.append(1.0)
                    return " ".join(_num(max(0.0, min(1.0, v))) for v in values)
        return None

    def _geometry(self, geometry, where):
        if geometry is None:
            return None
        for child in geometry:
            kind = child.tag
            if kind == "box":
                x, y, z = _vec(child.findtext("size"), 3, f"{where} box size")
                return f'<box size="{_num(x)} {_num(y)} {_num(z)}"/>', None
            if kind == "sphere":
                return f'<sphere radius="{_num(_float(child.find("radius"), 0.5))}"/>', None
            if kind in ("cylinder", "capsule"):
                if kind == "capsule":
                    self.warn(f"{where}: a capsule is written as a cylinder (a URDF has no capsule).")
                radius, length = _float(child.find("radius"), 0.5), _float(child.find("length"), 1.0)
                if kind == "capsule":
                    length += 2 * radius
                return f'<cylinder radius="{_num(radius)}" length="{_num(length)}"/>', None
            if kind == "plane":
                normal = _vec(child.findtext("normal") or "0 0 1", 3, f"{where} plane normal")
                size = _vec(child.findtext("size") or "1 1", 2, f"{where} plane size")
                if abs(normal[2]) < 0.999:
                    self.warn(f"{where}: a plane that is not horizontal was left out.")
                    return None
                return f'<box size="{_num(size[0])} {_num(size[1])} 0.002"/>', None
            if kind == "mesh":
                uri = (child.findtext("uri") or "").strip()
                path = self._mesh_path(uri)
                if path is None:
                    self.warn(f"{where}: the mesh {uri[:60]!r} is not in the approved folder and was left out.")
                    return None
                scale = child.findtext("scale")
                scale_text = ""
                if scale:
                    scale_text = ' scale="' + " ".join(_num(v) for v in _vec(scale, 3, f"{where} mesh scale")) + '"'
                return f'<mesh filename="{self._attr(path)}"{scale_text}/>', None
            self.drop(f"geometry:{kind}")
            self.warn(f"{where}: a {kind} geometry cannot be written as a URDF and was left out.")
            return None
        return None

    def _mesh_path(self, uri):
        """A mesh uri as a path relative to the generated URDF (which sits beside the SDF file), or None if it is not in the folder."""
        if SCHEME.match(uri) and not uri.startswith("model://"):
            _fail(f"The mesh uri {uri[:80]!r} must be a model:// name or a path inside the approved folder.", "UNSUPPORTED_URI")
        if "\\" in uri or any(ord(c) < 32 for c in uri) or uri.startswith("/"):
            _fail(f"The mesh uri {uri[:80]!r} must be a relative path or model://name/path.", "INVALID_PATH")
        base = _parent(self.entry)
        if uri.startswith("model://"):
            name, _, rest = uri[len("model://"):].partition("/")
            if not rest or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}", name):
                _fail(f"The mesh uri {uri[:80]!r} is not model://name/path.", "INVALID_PATH")
            candidates = []
            for folder in self.project.folders_named(name):
                target = normal_path(folder, rest)
                if target and target in self.project.files:
                    candidates.append(target)
            if not candidates:
                return None
            target = candidates[0]
        else:
            target = normal_path(_parent(getattr(self, "model_file", self.entry)), uri)
            if target is None:
                _fail(f"The mesh uri {uri[:80]!r} climbs out of the approved folder.", "OUTSIDE_ROOT")
            if target not in self.project.files:
                return None
        return posixpath.relpath(target, base or ".")

    def _joint(self, j, parent, child, origin, joint_frame, extra_links):
        element, jtype = j["element"], j["type"]
        name = self._attr(j["name"])
        axis_el = element.find("axis") if element is not None else None
        axis = [0.0, 0.0, 1.0]
        limit = lower = upper = None
        effort = velocity = None
        damping = friction = None
        if axis_el is not None:
            axis = _vec(axis_el.findtext("xyz") or "0 0 1", 3, f"joint {j['name']} axis")
            xyz_el = axis_el.find("xyz")
            expressed_in = (xyz_el.get("expressed_in") or "").strip() if xyz_el is not None else ""
            if expressed_in not in ("", "__model__"):
                self.warn(f"Joint {j['name']!r} gives its axis in the frame {expressed_in[:40]!r}; it is read in the joint's own frame.")
            if expressed_in == "__model__" or (axis_el.findtext("use_parent_model_frame") or "").strip().lower() in ("true", "1"):
                rot = [[joint_frame[r][c] for c in range(3)] for r in range(3)]
                axis = [sum(rot[k][i] * axis[k] for k in range(3)) for i in range(3)]  # model frame -> joint frame
            limits = axis_el.find("limit")
            if limits is not None:
                lower, upper = (_float(limits.find("lower"), -math.inf), _float(limits.find("upper"), math.inf))
                effort, velocity = _float(limits.find("effort"), None), _float(limits.find("velocity"), None)
            dynamics = axis_el.find("dynamics")
            if dynamics is not None:
                damping, friction = _float(dynamics.find("damping"), None), _float(dynamics.find("friction"), None)
        norm = math.sqrt(sum(v * v for v in axis))
        axis = [v / norm for v in axis] if norm > 1e-12 else [0.0, 0.0, 1.0]

        def joint_text(jname, jtype_, parent_, child_, origin_, axis_):
            lines = [f'  <joint name="{jname}" type="{jtype_}">', f'    <parent link="{self._attr(parent_)}"/>', f'    <child link="{self._attr(child_)}"/>',
                     self._origin(origin_)]
            if jtype_ != "fixed":
                lines.append(f'    <axis xyz="{" ".join(_num(v) for v in axis_)}"/>')
                if jtype_ in ("revolute", "prismatic"):
                    lo, hi = lower, upper
                    lines.append(f'    <limit lower="{_num(lo)}" upper="{_num(hi)}" effort="{_num(effort if effort and math.isfinite(effort) and effort > 0 else 1e6)}" '
                                 f'velocity="{_num(velocity if velocity and math.isfinite(velocity) and velocity > 0 else 1e3)}"/>')
                else:
                    lines.append(f'    <limit effort="{_num(effort if effort and math.isfinite(effort) and effort > 0 else 1e6)}" '
                                 f'velocity="{_num(velocity if velocity and math.isfinite(velocity) and velocity > 0 else 1e3)}"/>')
                if damping is not None or friction is not None:
                    lines.append(f'    <dynamics damping="{_num(damping or 0)}" friction="{_num(friction or 0)}"/>')
            lines.append("  </joint>")
            return lines

        low_open = lower is None or abs(lower) > HUGE
        high_open = upper is None or abs(upper) > HUGE
        if jtype == "revolute" and low_open and high_open:
            jtype = "continuous"
        elif jtype == "revolute" and (low_open or high_open):  # a URDF limit needs both ends
            lower, upper = (upper - 2 * math.pi, upper) if low_open else (lower, lower + 2 * math.pi)
            self.warn(f"Joint {j['name']!r} has a limit on one side only; the other side is written one full turn away.")
        elif jtype == "prismatic" and (low_open or high_open):  # SDF's default is unlimited, a URDF prismatic joint needs numbers
            if low_open and high_open:
                lower, upper = -OPEN_SLIDE, OPEN_SLIDE
            elif low_open:
                lower = upper - 2 * OPEN_SLIDE
            else:
                upper = lower + 2 * OPEN_SLIDE
            self.warn(f"Joint {j['name']!r} slides without a limit in the SDF; it is written with a range of {_num(upper - lower)} m.")
        if jtype in ("revolute2", "universal"):
            self.warn(f"Joint {j['name']!r} is a two-axis joint; it is written as two continuous joints (the second axis is not read).")
            jtype = "continuous"
        if jtype == "ball":
            self.warn(f"Joint {j['name']!r} is a ball joint; it is written as three continuous joints with two tiny links between them.")
            a, b = f"{j['name']}__x_link", f"{j['name']}__y_link"
            for dummy in (a, b):
                extra_links.append(
                    f'  <link name="{self._attr(dummy)}"><inertial><origin xyz="0 0 0" rpy="0 0 0"/><mass value="{_num(DUMMY_MASS)}"/>'
                    f'<inertia ixx="{_num(DUMMY_INERTIA)}" ixy="0" ixz="0" iyy="{_num(DUMMY_INERTIA)}" iyz="0" izz="{_num(DUMMY_INERTIA)}"/></inertial></link>')
            ident = _identity()
            return (joint_text(f"{name}__x", "continuous", parent, a, origin, [1.0, 0, 0])
                    + joint_text(f"{name}__y", "continuous", a, b, ident, [0, 1.0, 0])
                    + joint_text(f"{name}__z", "continuous", b, child, ident, [0, 0, 1.0]))
        if jtype == "revolute" and (lower is None or upper is None):
            jtype = "continuous"
        return joint_text(name, jtype, parent, child, origin, axis)


def convert(entry: str, project: Project, model: str | None = None) -> dict:
    """One SDF model as a URDF. Any failure is an AssetError."""
    try:
        return _convert(entry, project, model)
    except AssetError:
        raise
    except RecursionError:
        _fail(f"{entry} nests too deeply.", "LIMIT")
    except (TypeError, ValueError, OverflowError, AttributeError, KeyError, IndexError) as exc:
        _fail(f"{entry} cannot be converted ({type(exc).__name__}: {str(exc)[:200]}).")


def _convert(entry: str, project: Project, model: str | None = None) -> dict:
    """Read the SDF file *entry*, pick one model, and write it as a URDF: ``{"urdf", "model", "models", "static", "warnings", "dropped", "joints"}``."""
    if not is_sdf(entry):
        refuse("UNSUPPORTED_FORMAT", f"Expected a .sdf or .world file, not {entry}.")
    converter = _Converter(project, entry)
    root = _parse(project, entry)
    found = converter.models(root, entry)
    names = [name for name, _, _ in found]
    if not found:
        _fail(f"{entry} has no model.", "SDF_NO_MODEL")
    if model is not None:
        choices = [item for item in found if item[0] == model]
        if not choices:
            _fail(f"{entry} has no model named {model!r}. Its models are: {', '.join(names)}.", "SDF_MODEL")
        chosen = choices[0]
    else:
        def moves(item):
            element = item[1]
            return (element.findtext("static") or "").strip().lower() not in ("true", "1") and bool(element.findall("link"))

        movable = [item for item in found if moves(item)]
        if len(found) == 1:
            chosen = found[0]
        elif len(movable) == 1:
            chosen = movable[0]
        else:
            _fail(f"{entry} has several models: {', '.join(names)}. Pass sdf_model with the one to import.", "SDF_MODEL")
    name, element, file = chosen
    if file != entry:
        converter.entry = entry  # the generated URDF sits beside the entry; meshes are made relative to it
    text, static, joints = converter.convert_model(name, element, file)
    for tag in ("plugin", "light", "physics", "gravity", "scene", "atmosphere", "spherical_coordinates", "gui"):
        for holder in [root] + root.findall("world"):
            for _ in holder.findall(tag):
                converter.drop(tag)
    return dict(urdf=text, model=name, models=names, static=static, warnings=converter.warnings, dropped=converter.dropped, joints=joints)

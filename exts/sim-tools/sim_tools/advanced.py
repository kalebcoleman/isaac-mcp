"""Dedicated typed scene operations. Native API adapters are version checked at use.

No app.update(), render(), World.step(), network fetch or arbitrary attribute setter.
"""

import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import shutil
import uuid
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade
from isaac_mcp.project_assets import AssetError, refuse
from .core import KIT_ROOTS, bounded, vector

MUTATIONS = {
    "sim.graph.build",
    "sim.prim.delete",
    "sim.prim.clone",
    "sim.prim.create_many",
    "sim.stage.clear_owned",
    "sim.light.create",
    "sim.light.modify",
    "sim.material.create",
    "sim.material.apply",
    "sim.physics.configure",
    "sim.physics.apply",
    "sim.prim.set_attribute",
    "sim.prim.revert",
    "sim.joints.drive",
    "sim.camera.create",
    "sim.lidar.create",
    "sim.controller.create",
    "sim.controller.update",
    "sim.ros2.tf_setup",
}


def scalar(x, lo=0, hi=1e8):
    if type(x) not in (float, int) or not math.isfinite(x) or not lo <= x <= hi:
        refuse("INVALID_VALUE", f"{x!r} is not valid here: a finite number in [{lo},{hi}] is required.")
    return x


CAPTURE_NAME = r"[A-Za-z0-9][A-Za-z0-9_-]{0,59}\.png"
CAPTURE_NAME_RULE = "Use a new name of letters, digits, _ and -, ending .png (<=64 characters). No spaces or folders."


def runtime_dof_limits(path):
    """This articulation's [[low, high], ...], or None if this build or backend can't say."""
    from .physics import dof_limits, view_for

    try:
        return dof_limits(view_for(path, articulation=True))
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def joints_past_limits(names, positions, limits, slack=0.02):
    """The DOFs clearly outside their limits. A drive can press slightly past one, so a small slack is allowed."""
    if not limits or len(limits) != len(positions):
        return []
    return [name for name, value, (low, high) in zip(names, positions, limits)
            if value < low - slack or value > high + slack]


# Joint limits are stored as float32, and a radian target is converted to degrees as float64, so a
# target exactly at a limit can land a few float32 steps past it (RG2: 1.0 rad is 57.29577951 degrees,
# authored as 57.29577637). About 8 float32 steps, relative to the limit's size.
LIMIT_ROUNDING = 1e-6


STATE_ECHO_TOLERANCE = 1e-4
UNSTABLE_SPEED = 500.0  # rad/s or stage units/s: no real joint turns this fast


def joints_too_fast(names, velocities):
    return [name for name, speed in zip(names, velocities) if abs(speed) > UNSTABLE_SPEED][:10]


def to_limit(value, lo, hi):
    """``value``, or the limit it only rounds past; None when it is really outside [lo, hi]."""
    for limit, past in ((lo, lo is not None and value < lo), (hi, hi is not None and value > hi)):
        if past:
            return limit if abs(value - limit) <= LIMIT_ROUNDING * max(1.0, abs(limit)) else None
    return value


def command_target(value, lo, hi, current):
    """A commanded position, or None if it is outside the limits. A joint resting on a limit reads a
    little past it, so sending back the pose from sim__joints__state means that limit, not an error."""
    limited = to_limit(value, lo, hi)
    if limited is None and abs(value - current) <= STATE_ECHO_TOLERANCE:
        return lo if value < lo else hi
    return limited


def bounded_names(names, limit=32):
    shown = ", ".join(names[:limit])
    return shown + (f", ... ({len(names)} in all)" if len(names) > limit else "")


def resolve_named(values, names, label):
    """[(index, name, requested)] from {dof name: number}; an unknown name lists the valid ones."""
    if not isinstance(values, dict) or not values:
        refuse(
            "INVALID_VALUE",
            f"{label} must be a non-empty object of {{dof name: number}}. Valid DOF names: "
            f"{bounded_names(names)}.",
        )
    unknown = [key for key in values if not isinstance(key, str) or key not in names]
    if unknown:
        refuse(
            "UNKNOWN_DOF",
            f"{label} names {unknown[:8]!r}, which this articulation does not have. Valid DOF names: "
            f"{bounded_names(names)}.",
            valid_names=names,
        )
    rows = []
    for name, value in values.items():
        if type(value) not in (int, float) or not math.isfinite(value):
            refuse("INVALID_VALUE", f"{label}[{name!r}] must be a finite number, not {value!r}.")
        rows.append((names.index(name), name, float(value)))
    return rows


def allowed_targets(rows, limits, current, units, clamp, label):
    """Applied per-DOF targets, plus what was clamped; refuses a real excess unless clamp=true."""
    applied, clamped, rounded = {}, [], []
    for index, name, requested in rows:
        low, high = limits[index]
        if math.isnan(low) or math.isnan(high) or low > high:
            refuse("PHYSICS_FORMAT", f"Runtime limits for {name} are invalid; no targets were changed.")
        limited = command_target(requested, low, high, current[index])
        if limited is None:
            if not clamp:
                refuse(
                    "JOINT_LIMIT",
                    f"{label} {requested:g} for {name} ({units[index]}) is outside its runtime limits "
                    f"[{low:g}, {high:g}]. Resend with clamp=true to use the limit instead, or pick a value "
                    "inside the limits.",
                    name=name,
                    requested=requested,
                    limits=[low, high],
                    units=units[index],
                )
            limited = low if requested < low else high
            clamped.append(
                dict(name=name, requested=requested, applied=limited, limits=[low, high], units=units[index])
            )
        elif limited != requested:
            rounded.append(dict(name=name, requested=requested, applied=limited, units=units[index]))
        applied[index] = (name, requested, float(limited), low, high, units[index])
    return applied, clamped, rounded


# Visited while looking for an articulation's joints; the caller's limit bounds what is returned.
MAX_ARTICULATION_SCAN_PRIMS = 20000


def articulation_joints(stage, root, limit):
    """(joints, truncated) of the articulation under ``root``: the joints reached through its rigid
    bodies, in stage order. ``root`` may be the articulation's root joint, a link that carries the
    articulation API, or the prim holding all of them. A joint excluded from the articulation is left
    out, and so is another articulation under the same parent."""
    if root.IsA(UsdPhysics.Joint):
        # A root joint is a leaf under the joints' own folder: its siblings are the articulation's.
        parent = root.GetParent()
        scan = stage.GetPseudoRoot() if parent.IsPseudoRoot() else parent
    else:
        # A link's joints can sit in a sibling folder (a URDF import puts them under
        # <robot>/Asset/Physics while the bodies are under <robot>/Asset/Geometry), so look at the
        # whole stage. The body walk below still keeps other articulations out.
        scan = stage.GetPseudoRoot()
    found, truncated = [], False
    for count, prim in enumerate(Usd.PrimRange(scan)):
        if count >= MAX_ARTICULATION_SCAN_PRIMS:
            truncated = True
            break
        if prim.IsA(UsdPhysics.Joint):
            found.append(prim)

    def bodies(prim):
        joint = UsdPhysics.Joint(prim)
        return {str(t) for rel in (joint.GetBody0Rel(), joint.GetBody1Rel()) for t in rel.GetTargets()}

    if root.IsA(UsdPhysics.Joint):
        reached = bodies(root)
        joints = [root]
    else:
        # A link or the robot's own prim: start from the rigid bodies at or below it. Its joints are
        # siblings, so a scan below the link alone would find none.
        reached = {str(prim.GetPath()) for prim in Usd.PrimRange(root)
                   if prim.HasAPI(UsdPhysics.RigidBodyAPI)}
        joints = []
    pending = [p for p in found if p != root and not UsdPhysics.Joint(p).GetExcludeFromArticulationAttr().Get()]
    grew = True
    while grew:
        grew = False
        for prim in list(pending):
            if bodies(prim) & reached:
                reached |= bodies(prim)
                joints.append(prim)
                pending.remove(prim)
                grew = True
    order = {prim: index for index, prim in enumerate(found)}
    joints.sort(key=lambda prim: order.get(prim, -1))
    if len(joints) > limit:
        return joints[:limit], True
    return joints, truncated


def finite_clock(value):
    try:
        return not isinstance(value, bool) and math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def color(value):
    vector(value, 3)
    return Gf.Vec3f(*[scalar(x, 0, 1) for x in value])


def owned(s, stage, p):
    prim = s.prim(stage, p)
    if p not in s.owned:
        refuse(
            "NOT_OWNED",
            "Operation is limited to pack-created prims: ones these tools made on this stage since it opened. "
            "Prims from an opened file do not count, even ones you made before saving.",
        )
    return prim


def bind_material(s, stage, prim_path, material_path):
    """Bind a pack-made material to a geometry prim in the session layer. Shared by sim.material.apply,
    sim.prim.create's material option and sim.prim.create_many."""
    prim = s.prim(stage, prim_path)
    material = UsdShade.Material(owned(s, stage, material_path))
    if not material or not UsdGeom.Gprim(prim):
        refuse("WRONG_TYPE", "Need geometry and a pack-owned material.")
    purpose = "physics" if material.GetPrim().HasAPI(UsdPhysics.MaterialAPI) else ""
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(material, materialPurpose=purpose)
    return dict(
        path=prim_path,
        material=str(UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial(materialPurpose=purpose)[0].GetPath()),
        purpose=purpose,
    )


CREATE_KEYS = ("path", "kind", "size", "rigid", "position", "orientation", "frame", "collision", "scale", "mass",
               "density", "material")
MAX_CREATE_MANY = 200
# Shared `defaults` that do not apply to a kind are left off it, so one list can hold a group Xform and its blocks.
SHARED_ONLY_FOR = {
    "Xform": ("rigid", "mass", "density", "collision", "material"),
    "PhysicsScene": ("rigid", "mass", "density", "collision", "material"),
    "Plane": ("rigid", "mass", "density"),
}


def create_one(s, stage, spec):
    """One prim from sim.prim.create's arguments plus an optional pack-made material. A material that cannot
    be bound removes the prim again, so a call either makes the whole prim or nothing."""
    from .placement import flat_box_warning

    spec = dict(spec)
    material = spec.pop("material", None)
    if material is not None and (not isinstance(material, str) or not material.startswith("/")):
        refuse("INVALID_VALUE", "material must be the absolute path of a material made with sim__material__create.")
    created = s.create(stage, **spec)
    warning = flat_box_warning(spec.get("kind"), spec.get("scale") or (1, 1, 1), spec.get("collision") or spec.get("rigid"))
    if material is not None:
        try:
            bind_material(s, stage, spec["path"], material)
        except Exception:
            remove_created(s, stage, [spec["path"]])
            raise
        created = {**created, "material": material}
    return created, warning


def remove_created(s, stage, paths):
    """Take back prims this call made, newest first."""
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for p in reversed(paths):
            stage.RemovePrim(p)
            s.owned.discard(p)
            getattr(s, "edits", {}).pop(p, None)


def create_many(s, stage, a):
    """Make up to MAX_CREATE_MANY prims in one call, all or none. `defaults` fills any key an item leaves out."""
    items, defaults = a.get("items"), a.get("defaults") or {}
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_CREATE_MANY:
        refuse("INVALID_VALUE", f"items must be a list of 1 to {MAX_CREATE_MANY} prim specs.")
    if not isinstance(defaults, dict):
        refuse("INVALID_VALUE", "defaults must be an object of prim arguments shared by every item.")
    for label, spec in [("defaults", defaults), *[(f"items[{i}]", item) for i, item in enumerate(items)]]:
        if not isinstance(spec, dict):
            refuse("INVALID_VALUE", f"{label} must be an object.")
        unknown = sorted(set(spec) - set(CREATE_KEYS))
        if unknown:
            refuse("INVALID_VALUE", f"{label} has unknown keys {unknown}; allowed: {list(CREATE_KEYS)}. Nothing was created.")
    made, warnings = [], []
    for index, item in enumerate(items):
        spec = {**defaults, **item}
        kind = spec.get("kind")
        for key in SHARED_ONLY_FOR.get(kind, ()):
            if key in defaults and key not in item:
                spec.pop(key, None)  # a shared rigid, mass or material is for shapes, so an Xform in the list skips it
        try:
            if "path" not in spec or "kind" not in spec:
                refuse("INVALID_VALUE", "each item needs path and kind (defaults may supply kind).")
            created, warning = create_one(s, stage, spec)
        except Exception as exc:
            remove_created(s, stage, made)
            detail = str(exc)
            refuse(
                getattr(exc, "code", None) or "BATCH_FAILED",
                f"items[{index}] ({spec.get('path')!r}) failed, so all {len(made)} earlier prims were removed and "
                f"nothing was created: {detail}",
                **{**getattr(exc, "details", {}), "failed_index": index, "failed_path": spec.get("path")},
            )
        made.append(spec["path"])
        if warning:
            warnings.append(dict(index=index, path=spec["path"], warning=warning))
    return dict(
        count=len(made),
        paths=made,
        **({"warnings": warnings} if warnings else {}),
        note="All items were created in one call; undo them with sim__prim__delete on each path.",
    )


# Attributes the engine or a schema derives from other data (design 4.3). Writing them corrupts a
# stage in ways this tool could not verify afterwards, so they are refused with the tool that owns them.
ENGINE_OWNED = ("points", "normals", "faceVertexIndices", "faceVertexCounts", "extent", "xformOpOrder")
ATTRIBUTE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z_][A-Za-z0-9_]*)*")
VECTOR_TYPES = {
    "float2": (2, Gf.Vec2f), "double2": (2, Gf.Vec2d), "int2": (2, Gf.Vec2i),
    "float3": (3, Gf.Vec3f), "double3": (3, Gf.Vec3d), "int3": (3, Gf.Vec3i),
    "color3f": (3, Gf.Vec3f), "normal3f": (3, Gf.Vec3f), "point3f": (3, Gf.Vec3f),
    "vector3f": (3, Gf.Vec3f), "float4": (4, Gf.Vec4f), "double4": (4, Gf.Vec4d),
    "color4f": (4, Gf.Vec4f),
}
SCALAR_TYPES = ("bool", "uchar", "int", "uint", "int64", "uint64", "half", "float", "double",
                "string", "token", "asset")
SUPPORTED_TYPES = (*SCALAR_TYPES, *VECTOR_TYPES, "quatf", "quatd")


def jsonable(value):
    """A USD value as JSON: vectors, quaternions and paths become plain lists/strings."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Gf.Quatf):
        return [value.GetReal(), *value.GetImaginary()]
    if isinstance(value, Gf.Quatd):
        return [value.GetReal(), *value.GetImaginary()]
    if isinstance(value, (Sdf.Path, Sdf.AssetPath)):
        return str(value)
    try:
        return list(value)
    except TypeError:
        return str(value)


def typed_value(value, type_name):
    """Convert a JSON value to USD's type, refusing what does not fit instead of coercing silently."""
    if type_name in ("string", "token", "asset"):
        if not isinstance(value, str):
            refuse("INVALID_VALUE", f"A {type_name} attribute takes a string, not {value!r}.")
        return value
    if type_name == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        refuse("INVALID_VALUE", "A bool attribute takes true or false.")
    if type_name in ("uchar", "int", "uint", "int64", "uint64"):
        if type(value) is not int:
            refuse("INVALID_VALUE", f"A {type_name} attribute takes a whole number, not {value!r}.")
        if type_name in ("uchar", "uint", "uint64") and value < 0:
            refuse("INVALID_VALUE", f"A {type_name} attribute cannot be negative.")
        return value
    if type_name in ("half", "float", "double"):
        if type(value) not in (int, float) or not math.isfinite(value):
            refuse("INVALID_VALUE", f"A {type_name} attribute takes a finite number, not {value!r}.")
        return float(value)
    if type_name in VECTOR_TYPES:
        size, factory = VECTOR_TYPES[type_name]
        if (
            not isinstance(value, (list, tuple))
            or len(value) != size
            or any(type(item) not in (int, float) or not math.isfinite(item) for item in value)
        ):
            refuse("INVALID_VALUE", f"A {type_name} attribute takes {size} finite numbers, e.g. [1, 2, 3].")
        return factory(*value)
    if type_name in ("quatf", "quatd"):
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 4
            or any(type(item) not in (int, float) or not math.isfinite(item) for item in value)
        ):
            refuse("INVALID_VALUE", "A quaternion attribute takes four finite numbers [w, x, y, z].")
        if type_name == "quatf":
            return Gf.Quatf(value[0], Gf.Vec3f(*value[1:]))
        return Gf.Quatd(value[0], Gf.Vec3d(*value[1:]))
    refuse(
        "UNSUPPORTED_TYPE",
        f"{type_name!r} is not a supported attribute type here. Use one of: {', '.join(SUPPORTED_TYPES)}.",
    )


def attribute_kind(name):
    """Why this attribute is engine- or schema-owned, or None when the tool may write it."""
    if name in ENGINE_OWNED:
        return ("It is derived or schema-managed; writing it corrupts the stage in ways this tool cannot "
                "verify. Use sim__prim__create or the tool that owns it.")
    if name.startswith("physics:") or (name.startswith("physx") and ":" in name):
        return "Physics schema attributes are authored by sim__physics__apply, sim__joints__drive or sim__physics__configure."
    if name.startswith("xformOp:"):
        return "Transforms are authored by sim__prim__set_transform, which keeps the op order consistent."
    return None


def source_api_layers(stage, prim, token):
    """Layers other than this session that apply an API schema to *prim*."""
    session = stage.GetSessionLayer()
    found = []
    for spec in prim.GetPrimStack():
        if spec.layer == session:
            continue
        value = spec.GetInfo("apiSchemas")
        if value is not None and token in (list(value.prependedItems) + list(value.explicitItems)):
            found.append(spec.layer.identifier)
    return found


def remove_api_schema(stage, prim, token, alternative):
    """Remove this session's API application; refuse when another layer applies it."""
    layers = source_api_layers(stage, prim, token)
    if layers:
        refuse(
            "READ_ONLY_SOURCE",
            f"{prim.GetPath()} has {token} applied in {layers[0]}, and this tool edits only the session layer. "
            f"Edit the source file, or use {alternative} on a prim the pack created or imported.",
            source_layers=layers,
        )
    session = stage.GetSessionLayer()
    spec = session.GetPrimAtPath(prim.GetPath())
    value = spec.GetInfo("apiSchemas") if spec is not None else None
    if value is None:
        return False
    prepended = [item for item in value.prependedItems if item != token]
    explicit = [item for item in value.explicitItems if item != token]
    if (len(prepended), len(explicit)) == (len(list(value.prependedItems)), len(list(value.explicitItems))):
        return False
    if prepended or explicit:
        created = dict(prependedItems=tuple(str(item) for item in prepended))
        if explicit:
            created["explicitItems"] = tuple(str(item) for item in explicit)
        spec.SetInfo("apiSchemas", Sdf.TokenListOp.Create(**created))
    else:
        spec.ClearInfo("apiSchemas")
    return True



def foreign_prim(s, stage, p, allow_foreign):
    """The explicit route for a prim that is not pack-owned; refuses unless it was asked for."""
    prim = s.prim(stage, p)
    if not allow_foreign and p == "/World":  # allow_foreign wouldn't help, so don't send the caller there
        refuse("PROTECTED_PRIM", "World/default prim or its ancestor is protected.")
    if not allow_foreign:
        refuse(
            "NOT_OWNED",
            f"{p} was not created by these tools on this stage: prims from an opened file do not count, even "
            "ones you made before saving. Retry with allow_foreign=true to deactivate it in the session layer "
            "(it stays composed as inactive and keeps its path; source files are unchanged), or reach it through "
            "the file that defines it.",
            retry_with={"allow_foreign": True},
        )
    if prim.IsInstanceProxy() or prim.GetTypeName() == "OmniGraphNode":
        refuse(
            "PROTECTED_PRIM",
            "Cannot deactivate an instance proxy or a single graph node; "
            "act on the instance or graph, or rebuild a graph with sim__graph__build replace=true.",
        )
    if prim.GetPath().GetPrefixes()[0].name in KIT_ROOTS:
        refuse("PROTECTED_PRIM", "Kit's runtime render and viewport prims are protected.")
    return prim


MAX_SENSOR_SUBTREE = 10000


def deletable(s, stage, p, direct_sensor=False, sensor_paths=(), foreign=False):
    prim = s.prim(stage, p) if foreign else owned(s, stage, p)
    # USD traversal cannot see children below inactive prims, or registered
    # sensors removed/retyped externally. Their native resources still exist.
    if any(
        Sdf.Path(q) != prim.GetPath() and Sdf.Path(q).HasPrefix(prim.GetPath())
        for q in sensor_paths
    ):
        refuse("PROTECTED_PRIM", "Subtree contains registered sensor resources.")
    if (
        p == "/World"
        or stage.GetDefaultPrim()
        and stage.GetDefaultPrim().GetPath().HasPrefix(prim.GetPath())
    ):
        refuse("PROTECTED_PRIM", "World/default prim or its ancestor is protected.")
    # A sensor the pack registered can bring a large config subtree; its slot must stay releasable.
    if direct_sensor:
        nodes = list(itertools.islice(Usd.PrimRange(prim, Usd.PrimAllPrimsPredicate), MAX_SENSOR_SUBTREE + 1))
        truncated = len(nodes) > MAX_SENSOR_SUBTREE
    else:
        nodes, truncated = bounded(stage, p, 200)
    if truncated:
        refuse("LIMIT", f"Subtree deletion exceeds {MAX_SENSOR_SUBTREE if direct_sensor else 200} prims.")
    # Direct deletion releases only this sensor, never sensors below it.
    if any(
        n.HasAPI(UsdPhysics.ArticulationRootAPI)
        or (
            (not direct_sensor or str(n.GetPath()) not in (str(prim.GetPath()), direct_sensor))
            and (n.IsA(UsdGeom.Camera) or n.GetTypeName() == "OmniLidar")
        )
        or n.GetMetadata("no_delete")
        for n in nodes
    ):
        refuse(
            "PROTECTED_PRIM",
            "Subtree contains articulation/sensor/protected prim; use a new disposable session.",
        )
    return prim


def physics_manager():
    from .physics import manager

    return manager()


class Advanced:
    def __init__(self, scene, dispatch):
        self.s = scene
        self.dispatch = dispatch
        self.sensors = {}
        self.controllers = set()
        self.stage_id = None
        self.cleanup_errors = []

    def close_sensors(self, stage_closing=False):
        from .sensors import dispose

        for p, (_, sensor) in list(self.sensors.items()):
            self.cleanup_errors.extend(
                {"path": p, "error": e}
                for e in dispose(sensor, stage_closing=stage_closing)
            )
        self.cleanup_errors = self.cleanup_errors[-16:]
        self.sensors.clear()

    def adopt(self, stage, path, kind):
        """Register render resources for an existing sensor, on an explicit adopt=true. The prim is
        not added to the pack-owned set: deleting it still needs allow_foreign."""
        from .sensors import adopt

        self.s.prim(stage, path)
        if len(self.sensors) >= 8:
            refuse("LIMIT", "At most eight live sensors per session; delete an owned sensor to release its resources.")
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            sensor = adopt(stage, path, kind)
        if stage.GetRootLayer().GetPrimAtPath(sensor["product_path"]):
            # Hydra authors a new render product in the root layer too, whatever the edit target.
            sensor["root_layer_note"] = (
                "Kit's render product is also in the in-memory root layer; nothing is on disk. Do not File > Save "
                "over the source. sim__stage__save copies omit it."
            )
        self.sensors[path] = (kind, sensor)
        return self.sensors[path]

    def delete_foreign(self, stage, prim):
        """What the session layer can honestly do to a prim defined elsewhere. USD has no delete
        opinion and RemovePrim only edits the edit target's specs, so a prim with opinions in the
        root layer stays composed. Never edit that layer: a File > Save would write it to the source."""
        p = str(prim.GetPath())
        session = stage.GetSessionLayer()
        if not session.permissionToEdit:
            refuse("READ_ONLY", "Session layer is not editable.")
        if all(spec.layer == session for spec in prim.GetPrimStack()):
            with Usd.EditContext(stage, session):
                if not stage.RemovePrim(p):
                    refuse("DELETE_FAILED", "Session-layer removal failed.")
            return dict(
                deleted=p, method="removed_from_session_layer", still_composed=bool(stage.GetPrimAtPath(p)),
                foreign=True, source_files_changed=False,
            )
        was_active = prim.IsActive()
        with Usd.EditContext(stage, session):
            if not prim.SetActive(False):
                refuse("DELETE_FAILED", "Session-layer deactivation failed.")
        return dict(
            deleted=p, method="deactivated_in_session_layer", already_inactive=not was_active,
            still_composed=True, active=False, foreign=True, source_files_changed=False,
            note="The prim is inactive, so rendering, physics and traversal skip it, but it still holds this "
                 "path: create a replacement at a new path. Reopening the file restores it; a sim__stage__save copy "
                 "omits it, so opening that copy frees the path.",
        )

    def targetless(self, name, a):
        """Reads with no stage dependency. Deliberately no invalidate(): its sensor cleanup must
        follow a real stage check, and these run without one."""
        if name == "sim.logs.tail":
            from . import logs

            return logs.tail({**a, "scene_events": self.s.public_events()})
        if name == "sim.logs.mark":
            from . import logs

            return logs.mark()
        if name == "sim.logs.summary":
            from . import logs

            return logs.summary(a)
        from . import graph

        contains = a.get("contains")
        limit = a.get("limit", 20)
        if not isinstance(contains, str) or not 1 <= len(contains.strip()) <= 64:
            refuse("INVALID_VALUE", "contains: 1..64 characters.")
        if type(limit) is not int or not 1 <= limit <= 40:
            refuse("LIMIT", "limit must be 1..40.")
        return graph.node_types(contains, limit, a.get("ports"))

    def invalidate(self):
        if self.stage_id != self.s.stage_id:
            self.close_sensors()
            self.controllers.clear()
            self.stage_id = self.s.stage_id

    def joints(self, stage, p):
        s = self.s
        prim = s.prim(stage, p)
        if s.timeline.is_stopped() or not prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            refuse(
                "PHYSICS_NOT_READY",
                "The timeline is stopped: play first." if s.timeline.is_stopped() else
                f"{p} is not an articulation root. sim__physics__inspect lists them, or sim__ros2__tf_setup reports it.",
            )
        sm = physics_manager()
        from .physics import array as observed, runtime_vector, view_for

        art = view_for(p, articulation=True)

        def values(data):
            return observed(data).tolist()[0]

        names = list(art.shared_metatype.dof_names)
        positions = values(art.get_dof_positions())
        velocities = values(art.get_dof_velocities())
        backend = sm.get_active_physics_engine()
        runtime_vector(
            positions, len(names), path=p, field="joint_positions", backend=backend
        )
        runtime_vector(
            velocities, len(names), path=p, field="joint_velocities", backend=backend
        )
        return dict(
            path=p,
            positions=positions,
            velocities=velocities,
            names=names,
            source=sm.get_active_physics_engine() + " tensor view",
            backend=sm.get_active_physics_engine(),
            units="angular radians, prismatic stage units",
        )

    def import_urdf(self, stage, snapshot_id, manifest_sha256, prim_path, fixed_base=True, default_drives=True, **pose):
        from isaac_mcp import urdf_physics, urdf_refs
        from isaac_mcp.project_assets import Broker, open_dir, signature

        from .urdf import (
            convert, importer_identity, place_above_ground, prepare_generated_physics,
            repair_meshes_for_legacy_importer, uses_legacy_importer,
        )

        s = self.s
        if type(fixed_base) is not bool or type(default_drives) is not bool:
            refuse("INVALID_VALUE", "fixed_base and default_drives must be booleans.")
        s.new_path(stage, prim_path)
        filename, manifest = s.broker.verify(snapshot_id, manifest_sha256, kind="urdf")
        urdf_refs.check_urdf_path(filename.name)
        entry = manifest["entry"]
        tree = urdf_refs.parse(filename.read_bytes(), entry)
        project_dir = filename.parents[len(Path(entry).parts) - 1]
        project = urdf_refs.project_from_manifest(project_dir, manifest["files"], manifest.get("root_name", ""))
        found = urdf_refs.resolve(entry, tree, project)
        if found["problems"]:
            urdf_refs.refusal_for(found["problems"], entry)
        followed = urdf_refs.follow_mesh_files(project, sorted(set(found["mapped"].values())))
        model = urdf_physics.read_model(tree)
        workspace = s.broker.staging / snapshot_id
        # The rewritten URDF stays in private staging, and only there: not the source and not the
        # pack, and outside the verified project folder. Each file it names is in that folder.
        rewritten = workspace / "urdf-input" / filename.name
        rewritten.parent.mkdir(exist_ok=True)
        mapped, repaired = found["mapped"], []
        if uses_legacy_importer():
            mapped, repaired = repair_meshes_for_legacy_importer(mapped, project, rewritten.parent / "repaired")
        data = urdf_refs.rewritten(tree, mapped, project_dir)
        if not rewritten.is_file() or rewritten.read_bytes() != data:
            rewritten.write_bytes(data)
        key = hashlib.sha256(json.dumps([importer_identity(), fixed_base, default_drives]).encode() + data).hexdigest()[:24]
        output = workspace / f"converted-{key}"
        root_before = stage.GetRootLayer().ExportToString()
        session_before = stage.GetSessionLayer().ExportToString()
        done = output / "generated.txt"
        if done.is_file():
            generated = output / done.read_text()
        else:
            pending = workspace / f".pending-{uuid.uuid4().hex}"
            try:
                try:
                    generated = convert(rewritten, pending, fixed_base)
                except AssetError:
                    raise
                except Exception as exc:  # noqa: BLE001 - the native importer raises anything
                    refuse("IMPORT_FAILED", f"The URDF importer failed on {entry}: {str(exc)[:800]}")
                if (
                    s.get_stage() != stage
                    or stage.GetRootLayer().ExportToString() != root_before
                    or stage.GetSessionLayer().ExportToString() != session_before
                ):
                    refuse(
                        "IMPORTER_STAGE_CHANGED",
                        "Native importer changed the active stage. Partial effects possible; inspect before retrying.",
                    )
                converted_physics = prepare_generated_physics(
                    generated,
                    pending,
                    movable_joints=sum(
                        joint.get("type") in ("revolute", "continuous", "prismatic")
                        for joint in tree.findall("joint")
                    ),
                    gains=urdf_physics.default_gains(model) if default_drives else None,
                    fixed_base=fixed_base,
                )
                relative_generated = str(generated.relative_to(pending))
                (pending / "generated.txt").write_text(relative_generated)
                (pending / "report.json").write_text(json.dumps(converted_physics))
                pending.rename(output)
                generated = output / relative_generated
            except BaseException:
                shutil.rmtree(pending, ignore_errors=True)
                raise
        converted_physics = json.loads((output / "report.json").read_text())
        # Generated artifacts are service-owned derivatives, not an approval of a
        # new model-supplied folder. Reuse the normal bounded snapshot pipeline.
        derived = Broker("")
        derived.staging = s.broker.staging
        fd = open_dir(output)
        try:
            ident = signature(os.fstat(fd))[:2]
        finally:
            os.close(fd)
        derived.roots = {"converted": (output, ident)}
        snap = derived.snapshot("converted", str(generated.relative_to(output)))
        position_given = "position" in pose
        result = s.import_asset(
            stage, snap["snapshot_id"], snap["manifest_sha256"], prim_path, **pose
        )
        from .ros2 import _articulation_roots

        roots = _articulation_roots(stage.GetPrimAtPath(prim_path))
        warnings = urdf_physics.warnings(model)
        placement = place_above_ground(stage, prim_path, position_given)
        if "warning" in placement:
            warnings.append(placement.pop("warning"))
        if followed["missing"]:
            warnings.append(f"Meshes name files that are absent, so those textures will be missing: "
                            f"{[item['reference'] for item in followed['missing'][:5]]}.")
        if converted_physics["base"] != ("fixed" if fixed_base else "floating"):
            warnings.append(f"The converted robot's base is {converted_physics['base']}, not what fixed_base asked for.")
        if fixed_base and any(joint["type"] == "continuous" for joint in model["joints"]):
            warnings.append("This robot has continuous joints, which may be wheels, and its base is fixed to the world. "
                            "For a robot that drives, pass fixed_base=false.")
        result.update(
            urdf_snapshot_id=snapshot_id,
            converted_physics=converted_physics,
            articulation_root=roots[0] if roots else None,
            articulation_roots=roots,
            base=converted_physics["base"],
            packages=found["packages"],
            mapped_files=len(found["mapped"]),
            **({"repaired_meshes": repaired} if repaired else {}),
            placement=placement,
            warnings=warnings,
        )
        return result

    def set_attribute(self, stage, a):
        s = self.s
        prim = s.prim(stage, a["path"])
        if prim.IsInstanceProxy():
            refuse(
                "INSTANCE_PROXY",
                f"{a['path']} is an instance proxy inside an instance and cannot be edited directly. Edit the "
                "instance root instead.",
            )
        if prim.GetPath().GetPrefixes()[0].name in KIT_ROOTS:
            refuse("PROTECTED_PRIM", "Kit's runtime render and viewport prims are protected.")
        name = a["attribute"]
        if not isinstance(name, str) or len(name) > 128 or not ATTRIBUTE_NAME.fullmatch(name):
            refuse("INVALID_VALUE", "attribute must be a USD property name such as visibility, purposes or "
                   "user:owner (letters, digits, _ and :, at most 128 characters).")
        owned = attribute_kind(name)
        if owned:
            refuse("ENGINE_OWNED_ATTRIBUTE", f"{name} cannot be set here. {owned}")
        type_name = a.get("type")
        if type_name is not None and type_name not in SUPPORTED_TYPES:
            refuse(
                "UNSUPPORTED_TYPE",
                f"type {type_name!r} is not supported. Use one of: {', '.join(SUPPORTED_TYPES)}.",
            )
        attr = prim.GetAttribute(name) if prim.HasAttribute(name) else None
        created = attr is None
        if attr:
            type_name = str(attr.GetTypeName())
            if type_name not in SUPPORTED_TYPES:
                refuse(
                    "UNSUPPORTED_TYPE",
                    f"{name} on {a['path']} has USD type {type_name}, which this tool does not convert. "
                    f"Supported types: {', '.join(SUPPORTED_TYPES)}.",
                )
        elif not type_name:
            refuse(
                "INVALID_VALUE",
                f"{a['path']} has no attribute {name!r}. Pass type to create it, e.g. type=\"float\" or "
                "type=\"float3\"; supported types: " + ", ".join(SUPPORTED_TYPES) + ".",
            )
        value = typed_value(a["value"], type_name)
        before = attr.Get() if attr else None
        authored_before = bool(attr and attr.HasAuthoredValueOpinion())
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            s.remember_properties(stage, a["path"], [name])
            if not attr:
                attr = prim.CreateAttribute(name, Sdf.ValueTypeNames.Find(type_name), custom=True)
            if not attr or not attr.Set(value):
                refuse("SET_FAILED", f"Could not author {name} on {a['path']}; inspect the prim before retrying.")
        after = prim.GetAttribute(name).Get()
        return dict(
            path=a["path"],
            attribute=name,
            type=type_name,
            before=jsonable(before),
            after=jsonable(after),
            authored_before=authored_before,
            created_attribute=created,
            layer="session",
            source_files_changed=False,
            revert="sim__prim__revert",
        )

    def physics_apply(self, stage, a):
        from pxr import UsdPhysics

        s = self.s
        path = a["path"]
        prim = s.prim(stage, path)
        if prim.IsInstanceProxy():
            refuse(
                "INSTANCE_PROXY",
                f"{path} is an instance proxy inside an instance. Edit the instance root instead.",
            )
        if prim.GetPath().GetPrefixes()[0].name in KIT_ROOTS:
            refuse("PROTECTED_PRIM", "Kit's runtime render and viewport prims are protected.")
        collider, rigid = a.get("collider"), a.get("rigid_body")
        kinematic, approximation = a.get("kinematic"), a.get("approximation") or ""
        mass, density = a.get("mass"), a.get("density")
        for label, flag in (("collider", collider), ("rigid_body", rigid), ("kinematic", kinematic)):
            if flag is not None and type(flag) is not bool:
                refuse("INVALID_VALUE", f"{label} must be true or false.")
        if collider is None and rigid is None and kinematic is None and mass is None and density is None:
            refuse("INVALID_VALUE",
                   "Give at least one of collider, rigid_body, kinematic, mass or density. Nothing was changed.")
        if approximation and collider is not True:
            refuse("INVALID_VALUE", "approximation needs collider=true. Nothing was changed.")
        if approximation and approximation not in (
                "convexHull", "convexDecomposition", "boundingSphere", "boundingCube",
                "meshSimplification", "none"):
            refuse("INVALID_VALUE", "approximation must be convexHull, convexDecomposition, boundingSphere, "
                   "boundingCube, meshSimplification or none.")
        if mass is not None and density is not None:
            refuse("CONFLICT", "Give mass or density, not both: a mass overrides the density-derived one.")
        for label, value in (("mass", mass), ("density", density)):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
                refuse("INVALID_VALUE", f"{label} must be a finite number greater than 0.")
        if rigid is True:
            if prim.IsA(UsdPhysics.Joint) or prim.HasAPI(UsdPhysics.ArticulationRootAPI):
                refuse(
                    "INVALID_PHYSICS",
                    f"{path} is a joint or articulation root, not a link; a rigid body belongs on the link "
                    "prims under it. sim__physics__inspect lists them.",
                )
            ancestor = prim.GetParent()
            while ancestor and not ancestor.IsPseudoRoot():
                if ancestor.HasAPI(UsdPhysics.RigidBodyAPI):
                    refuse(
                        "NESTED_BODY",
                        f"{ancestor.GetPath()} already carries a rigid body, and a rigid body under another one "
                        "is not a separate simulation body. Apply it to a link that has no body ancestor.",
                        body_ancestor=str(ancestor.GetPath()),
                    )
                ancestor = ancestor.GetParent()
        has_body = prim.HasAPI(UsdPhysics.RigidBodyAPI)
        if kinematic is not None and not (rigid is True or (rigid is None and has_body)):
            refuse("INVALID_PHYSICS",
                   "kinematic needs a rigid body on this prim: pass rigid_body=true with it, or apply the body first.")
        if rigid is False and kinematic is True:
            refuse("CONFLICT", "A removed rigid body cannot be kinematic. Nothing was changed.")
        before = s.get(stage, path)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            if collider is True:
                s.remember_api(stage, path, ["PhysicsCollisionAPI"])
                UsdPhysics.CollisionAPI.Apply(prim)
                if approximation:
                    s.remember_api(stage, path, ["PhysicsMeshCollisionAPI"])
                    s.remember_properties(stage, path, ["physics:approximation"])
                    UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr(approximation)
            elif collider is False:
                remove_api_schema(stage, prim, "PhysicsCollisionAPI", "collider=true")
            if rigid is True:
                s.remember_api(stage, path, ["PhysicsRigidBodyAPI"])
                UsdPhysics.RigidBodyAPI.Apply(prim)
            elif rigid is False:
                remove_api_schema(stage, prim, "PhysicsRigidBodyAPI", "rigid_body=true")
            if kinematic is not None:
                s.remember_properties(stage, path, ["physics:kinematicEnabled"])
                UsdPhysics.RigidBodyAPI(prim).CreateKinematicEnabledAttr(kinematic)
            if mass is not None or density is not None:
                s.remember_api(stage, path, ["PhysicsMassAPI"])
                mass_api = UsdPhysics.MassAPI.Apply(prim)
                if mass is not None:
                    s.remember_properties(stage, path, ["physics:mass"])
                    mass_api.CreateMassAttr(float(mass))
                else:
                    s.remember_properties(stage, path, ["physics:density"])
                    mass_api.CreateDensityAttr(float(density))
        after = s.get(stage, path)
        scale = UsdPhysics.GetStageKilogramsPerUnit(stage)
        result = dict(
            path=path,
            before=before,
            after=after,
            applied=dict(
                collider=collider, approximation=approximation or None, rigid_body=rigid,
                kinematic=kinematic, mass=mass, density=density,
            ),
            units=dict(mass="kilograms (stage mass units)", density="kg/m^3, scaled by the stage units"),
            stage_kilograms_per_unit=scale,
            layer="session",
            source_files_changed=False,
            revert="sim__prim__revert",
        )
        mass_value = after["physics"]["mass"]
        if mass_value is not None:
            result["authored_mass_kg"] = mass_value * scale
        if (mass is not None or density is not None) and not after["physics"]["rigid_body"]:
            result["mass_note"] = ("Authored, but this prim is not a rigid body (and has no body ancestor), so the "
                                   "value has no runtime effect yet. Apply rigid_body=true for simulation.")
        if collider is True and not prim.IsA(UsdGeom.Gprim):
            result["collision_note"] = ("CollisionAPI is authored, but this prim is not geometry; the physics "
                                        "backend simulates a collider only on geometry such as a mesh or shape.")
        return result

    def revert(self, stage, a):
        s = self.s
        subtree = a.get("subtree", True)
        if type(subtree) is not bool:
            refuse("INVALID_VALUE", "subtree must be true or false.")
        s.prim(stage, a["path"])
        result = s.revert(stage, a["path"], subtree)
        if not result["reverted_paths"]:
            result["note"] = ("No session-layer opinion from these tools is recorded on this prim or its subtree, so "
                              "nothing was removed. Edits recorded before a stage or session change are not "
                              "recoverable; the source files were never changed.")
        return result

    def execute(self, name, stage, a, *, camera_frame=None):
        s = self.s
        self.invalidate()
        if name in ("sim.prim.bounds", "sim.prim.measure"):
            from . import geometry

            if name == "sim.prim.bounds":
                return geometry.world_bound(stage, a["path"], a.get("purpose", "default"))
            return geometry.measure(stage, a["path_a"], a["path_b"])
        if name == "sim.camera.list":
            from . import geometry

            return geometry.cameras(stage, a.get("root", "/"), a.get("limit", 100))
        if name in ("sim.physics.raycast", "sim.physics.contacts"):
            from . import queries

            if name == "sim.physics.raycast":
                return queries.raycast(
                    a["origin"], a["direction"], a.get("max_distance", 100),
                    a.get("all", False), timeline=s.timeline,
                )
            return queries.contacts(a["paths"], stage, timeline=s.timeline)
        if name == "sim.prim.delete":
            if a["confirm"] is not True:
                refuse("CONFIRM_REQUIRED", "confirm=true required.")
            p = a["path"]
            allow_foreign = a.get("allow_foreign", False)
            if type(allow_foreign) is not bool:
                refuse("INVALID_VALUE", "allow_foreign must be boolean.")
            foreign = p not in s.owned
            direct_sensor = (self.sensors[p][1].get("sensor_path") or p) if p in self.sensors else False
            if foreign:
                from .sensors import find_sensor_prim

                prim = foreign_prim(s, stage, p, allow_foreign)
                if not direct_sensor:
                    # An unregistered sensor holds no resources, so this exact prim may go.
                    found = find_sensor_prim(stage, p, "lidar") or find_sensor_prim(stage, p, "camera")
                    direct_sensor = str(found.GetPath()) if found else False
            deletable(
                s, stage, p, direct_sensor=direct_sensor, sensor_paths=self.sensors, foreign=foreign
            )
            if p in self.sensors:
                from .sensors import dispose

                errors = dispose(self.sensors[p][1])
                if errors:
                    refuse(
                        "SENSOR_CLEANUP_FAILED",
                        "Some resources may have been released; inspect before retrying: "
                        + str(errors),
                    )
                del self.sensors[p]
            if foreign:
                return self.delete_foreign(stage, prim)
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                if not stage.RemovePrim(p):
                    refuse("DELETE_FAILED", "Session-layer removal failed.")
            s.owned = {q for q in s.owned if q != p and not q.startswith(p + "/")}
            return dict(deleted=p, still_composed=bool(stage.GetPrimAtPath(p)))
        if name == "sim.stage.clear_owned":
            if a["confirm"] is not True:
                refuse("CONFIRM_REQUIRED", "confirm=true required.")
            removed = []
            refused = []
            for p in sorted(s.owned, key=len):
                if p not in s.owned:
                    continue
                try:
                    if p in self.sensors:
                        refuse(
                            "PROTECTED_PRIM",
                            "Clear preserves sensors; delete the exact owned sensor path explicitly.",
                        )
                    removed.append(
                        self.execute(
                            "sim.prim.delete", stage, dict(path=p, confirm=True)
                        )
                    )
                except ValueError as e:
                    refused.append(dict(path=p, reason=str(e)))
            return dict(
                removed=removed,
                refused=refused,
                scope="pack-owned only; current stage preserved",
            )
        if name == "sim.prim.clone":
            prim = owned(s, stage, a["path"])
            p, _ = s.new_path(stage, a["new_path"])
            if (
                prim.GetChildren()
                or prim.GetTypeName()
                not in ("Cube", "Sphere", "Cylinder", "Capsule", "Cone", "Xform")
                or prim.HasAuthoredReferences()
                or prim.HasAuthoredPayloads()
            ):
                refuse(
                    "UNSUPPORTED_CLONE",
                    "Only pack-created simple leaf geometry/Xform can be cloned.",
                )
            if not Sdf.CopySpec(
                stage.GetSessionLayer(), prim.GetPath(), stage.GetSessionLayer(), p
            ):
                refuse("CLONE_FAILED", "CopySpec returned false.")
            s.owned.add(str(p))
            return s.get(stage, str(p))
        if name.startswith("sim.light."):
            rgb = color(a.get("color", [1, 1, 1]))
            intensity = scalar(a.get("intensity", 1000))
            if name.endswith("create"):
                p, parent = s.new_path(stage, a["path"])
                kind = a.get("kind", "DistantLight")
                cls = {
                    "SphereLight": UsdLux.SphereLight,
                    "DistantLight": UsdLux.DistantLight,
                    "DomeLight": UsdLux.DomeLight,
                }.get(kind)
                if cls is None:
                    refuse(
                        "UNSUPPORTED_TYPE",
                        "Use SphereLight, DistantLight or DomeLight.",
                    )
                pose = s.pose(
                    stage,
                    parent,
                    a.get("position", [0, 0, 0]),
                    a.get("orientation", [1, 0, 0, 0]),
                    a.get("frame", "local"),
                )
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    prim = cls.Define(stage, p).GetPrim()
                    UsdGeom.Xformable(prim).AddTransformOp().Set(pose)
                s.owned.add(str(p))
            else:
                prim = owned(s, stage, a["path"])
            light = UsdLux.LightAPI(prim)
            if not light:
                refuse("WRONG_TYPE", "Not a USD light.")
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                light.CreateIntensityAttr(intensity)
                light.CreateColorAttr(rgb)
            return dict(
                path=str(prim.GetPath()),
                intensity=light.GetIntensityAttr().Get(),
                color=list(light.GetColorAttr().Get()),
            )
        if name == "sim.material.create":
            p, _ = s.new_path(stage, a["path"])
            rgb = color(a.get("color", [0.5, 0.5, 0.5]))
            rough = scalar(a.get("roughness", 0.5), 0, 1)
            metal = scalar(a.get("metallic", 0), 0, 1)
            kind = a.get("kind", "pbr")
            if kind not in ("pbr", "physics"):
                refuse("WRONG_TYPE", "Material kind must be pbr or physics.")
            friction = scalar(a.get("static_friction", 0.5), 0, 10)
            dynamic = scalar(a.get("dynamic_friction", 0.5), 0, 10)
            bounce = scalar(a.get("restitution", 0), 0, 1)
            if kind == "physics":
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    material = UsdShade.Material.Define(stage, p)
                    api = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
                    api.CreateStaticFrictionAttr(friction)
                    api.CreateDynamicFrictionAttr(dynamic)
                    api.CreateRestitutionAttr(bounce)
                s.owned.add(str(p))
                return dict(
                    path=str(p),
                    kind=kind,
                    static_friction=friction,
                    dynamic_friction=dynamic,
                    restitution=bounce,
                )
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                material = UsdShade.Material.Define(stage, p)
                shader = UsdShade.Shader.Define(stage, p.AppendChild("Surface"))
                shader.CreateIdAttr("UsdPreviewSurface")
                shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(rgb)
                shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(rough)
                shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metal)
                material.CreateSurfaceOutput().ConnectToSource(
                    shader.ConnectableAPI(), "surface"
                )
            s.owned.add(str(p))
            return dict(
                path=str(p),
                shader="UsdPreviewSurface",
                color=list(rgb),
                roughness=rough,
                metallic=metal,
            )
        if name == "sim.material.apply":
            paths, single = a.get("paths"), a.get("path")
            if (paths is None) == (single in (None, "")):
                refuse("INVALID_VALUE", "Give path (one prim) or paths (a list of 1 to 128 prims), one of the two.")
            if paths is None:
                return bind_material(s, stage, single, a["material_path"])
            if not isinstance(paths, list) or not 1 <= len(paths) <= 128:
                refuse("INVALID_VALUE", "paths must be a list of 1 to 128 prim paths.")
            owned(s, stage, a["material_path"])
            for p in paths:  # check every prim before binding any, so a bad one leaves nothing half done
                if not isinstance(p, str) or not UsdGeom.Gprim(s.prim(stage, p)):
                    refuse("WRONG_TYPE", f"{p!r} is not geometry, so nothing was bound.", failed_path=p)
            return dict(applied=[bind_material(s, stage, p, a["material_path"]) for p in paths])
        if name == "sim.prim.create_many":
            return create_many(s, stage, a)
        if name == "sim.physics.configure":
            sm = physics_manager()
            prim = s.prim(stage, a["path"])
            physics = UsdPhysics.Scene(prim)
            if not physics:
                refuse("WRONG_TYPE", "Not a PhysicsScene.")
            gravity = vector(a.get("gravity", [0, 0, -9.81]), 3)
            dt = scalar(a.get("dt", 1 / 60), 0.001, 0.1)
            length = math.sqrt(sum(x * x for x in gravity))
            direction = (
                Gf.Vec3f(*[x / length for x in gravity])
                if length
                else Gf.Vec3f(0, 0, -1)
            )
            from pxr import PhysxSchema

            with Usd.EditContext(stage, stage.GetSessionLayer()):
                physics.CreateGravityDirectionAttr(direction)
                physics.CreateGravityMagnitudeAttr(length)
                rate = round(1 / dt)
                if sm.get_active_physics_engine() == "physx":
                    PhysxSchema.PhysxSceneAPI.Apply(prim).CreateTimeStepsPerSecondAttr(
                        rate
                    )
                else:
                    if not prim.ApplyAPI("NewtonSceneAPI"):
                        refuse(
                            "PHYSICS_API_UNAVAILABLE",
                            "NewtonSceneAPI is unavailable; gravity may already have been authored.",
                        )
                    prim.GetAttribute("newton:timeStepsPerSecond").Set(rate)
                    solver_attr = prim.GetAttribute("newton:solver")
                    solver = solver_attr.Get() if solver_attr else None
                    if solver in (None, "", "mjcwarp", "mujoco"):
                        # Newton parses its outer rate from NewtonSceneAPI,
                        # while SimulationManager's MuJoCo wrapper reads this
                        # solver-specific timestep. Author the public schema:
                        # 6.0.0.0 ships the wrapper but does not export its class.
                        if not prim.ApplyAPI("MjcSceneAPI"):
                            refuse(
                                "PHYSICS_API_UNAVAILABLE",
                                "MjcSceneAPI unavailable; inspect partial scene configuration.",
                            )
                        attr = prim.GetAttribute("mjc:option:timestep")
                        if not attr or not attr.Set(1 / rate):
                            refuse(
                                "PHYSICS_API_UNAVAILABLE",
                                "MuJoCo timestep unavailable; inspect partial scene configuration.",
                            )
            return dict(path=a["path"], gravity=gravity, dt=1 / rate, requested_dt=dt)
        if name == "sim.prim.set_attribute":
            return self.set_attribute(stage, a)
        if name == "sim.physics.apply":
            return self.physics_apply(stage, a)
        if name == "sim.prim.revert":
            return self.revert(stage, a)
        if name == "sim.joints.inspect":
            nodes, truncated = bounded(stage, a["path"], a.get("limit", 200))
            # An articulation root is often a leaf joint (<robot>/root_joint): the joints that
            # matter are its siblings, connected through the links, not below it.
            root_prim = s.prim(stage, a["path"])
            scope = {}
            if root_prim.HasAPI(UsdPhysics.ArticulationRootAPI):
                nodes, truncated = articulation_joints(stage, root_prim, a.get("limit", 200))
                if nodes:
                    scope = dict(scope="articulation", articulation_root=a["path"])
                else:  # nothing linked through bodies: keep the subtree view rather than an empty list
                    nodes, truncated = bounded(stage, a["path"], a.get("limit", 200))
            joints = []
            for prim in nodes:
                if prim.IsA(UsdPhysics.Joint):
                    attrs = {
                        key: prim.GetAttribute(key).Get()
                        for key in (
                            "physics:lowerLimit",
                            "physics:upperLimit",
                            "drive:angular:physics:targetPosition",
                            "drive:angular:physics:stiffness",
                            "drive:angular:physics:damping",
                            "drive:angular:physics:maxForce",
                            "drive:linear:physics:targetPosition",
                            "drive:linear:physics:stiffness",
                            "drive:linear:physics:damping",
                            "drive:linear:physics:maxForce",
                        )
                        if prim.HasAttribute(key)
                    }
                    joints.append(
                        dict(
                            path=str(prim.GetPath()),
                            type=prim.GetTypeName(),
                            authored=attrs,
                        )
                    )
            return dict(
                joints=joints,
                truncated=truncated,
                **scope,
                units="USD angular degrees; prismatic stage units",
                source="authored USD, not DOF positions",
            )
        if name == "sim.joints.drive":
            prim = s.prim(stage, a["path"])
            angular = prim.IsA(UsdPhysics.RevoluteJoint)
            if not angular and not prim.IsA(UsdPhysics.PrismaticJoint):
                refuse("WRONG_TYPE", "Expected revolute/prismatic joint.")
            target = scalar(a["target"], -1e6, 1e6)
            stiff = scalar(a["stiffness"])
            damp = scalar(a["damping"])
            force = scalar(a.get("max_force", 1000), 1e-9)
            usd_target = math.degrees(target) if angular else target
            lo = prim.GetAttribute("physics:lowerLimit").Get()
            hi = prim.GetAttribute("physics:upperLimit").Get()
            limited = to_limit(usd_target, lo, hi)
            if limited is None:
                unit = "degrees" if angular else "stage units"
                refuse("JOINT_LIMIT", f"Requested target ({usd_target:g} {unit}) is outside the authored joint "
                       f"limits [{lo}, {hi}] {unit}.", target=usd_target, lower=lo, upper=hi, unit=unit)
            at_limit = limited != usd_target
            usd_target = limited
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                drive = UsdPhysics.DriveAPI.Apply(
                    prim, "angular" if angular else "linear"
                )
                drive.CreateTargetPositionAttr(usd_target)
                drive.CreateStiffnessAttr(stiff)
                drive.CreateDampingAttr(damp)
                drive.CreateMaxForceAttr(force)
            return dict(
                path=a["path"],
                requested_target=target,
                authored_target=usd_target,
                unit="degrees" if angular else "stage units",
                effect="drive target authored; motion unverified until runtime observation",
                **({"note": "Target rounded onto the authored limit, which is stored as float32."} if at_limit else {}),
            )
        if name == "sim.joints.command":
            state = self.joints(stage, a["path"])
            names = state["names"]
            if not names:
                refuse("JOINT_COUNT", "Selected articulation has no commandable DOFs.")
            positions_arg, targets_arg = a.get("positions"), a.get("targets")
            if positions_arg is None and targets_arg is None and a.get("velocities") is None:
                refuse(
                    "INVALID_VALUE",
                    "Give positions (one per DOF, in sim__joints__state order), targets ({dof name: value}) "
                    f"or velocities ({{dof name: value}}). Valid DOF names: {bounded_names(names)}.",
                )
            if positions_arg is not None and targets_arg is not None:
                refuse("CONFLICT", "Give positions or targets, not both. Nothing was changed.")
            clamp = a.get("clamp", False)
            if type(clamp) is not bool:
                refuse("INVALID_VALUE", "clamp must be true or false.")
            from .physics import array as observed, dof_limits, dof_set, dof_units, view_for

            view = view_for(a["path"], articulation=True)
            if positions_arg is not None:
                positions = vector(positions_arg, len(names))
                rows = [(index, name, float(positions[index])) for index, name in enumerate(names)]
            elif targets_arg is not None:
                rows = resolve_named(targets_arg, names, "targets")
            else:
                rows = []
            limits, units = dof_limits(view), dof_units(view)
            applied, clamped, rounded = allowed_targets(
                rows, limits, state["positions"], units, clamp, "Target"
            )
            previous = dict(enumerate(observed(view.get_dof_position_targets()).reshape(-1).tolist()))
            if applied:
                dof_set(view, "get_dof_position_targets", "set_dof_position_targets",
                        {index: row[2] for index, row in applied.items()})
            velocities = []
            if a.get("velocities") is not None:
                velocity_rows = resolve_named(a["velocities"], names, "velocities")
                before_velocity = observed(view.get_dof_velocity_targets()).reshape(-1).tolist()
                dof_set(view, "get_dof_velocity_targets", "set_dof_velocity_targets",
                        {index: value for index, _, value in velocity_rows})
                for index, name, value in velocity_rows:
                    velocities.append(dict(name=name, index=index, requested=value, applied=value,
                                           previous=before_velocity[index],
                                           units=units[index] + "/s"))
            return dict(
                path=a["path"],
                backend=state["backend"],
                names=names,
                units="rad for revolute DOFs, stage units for prismatic ones; velocities per second",
                targets=[
                    dict(name=row[0], index=index, requested=row[1], applied=row[2], previous=previous.get(index),
                         limits=[row[3], row[4]], units=row[5], clamped=row[1] != row[2])
                    for index, row in applied.items()
                ],
                **({"velocities": velocities} if velocities else {}),
                clamped=clamped,
                rounded=rounded,
                effect="Targets set; the joints have not moved yet. Compare sim__joints__state while the "
                       "simulation runs, or settle them with sim__joints__move. A joint that stops short is "
                       "usually in contact (a low arm against the ground) or has a controller replacing its target.",
                runtime_only="Lost on Stop and never saved; sim__joints__drive authors USD targets instead.",
            )
        if name == "sim.joints.gains":
            state = self.joints(stage, a["path"])
            names = state["names"]
            if not names:
                refuse("JOINT_COUNT", "Selected articulation has no commandable DOFs.")
            dofs = a.get("dofs") or []
            if not isinstance(dofs, list) or any(not isinstance(name, str) for name in dofs):
                refuse("INVALID_VALUE", f"dofs must be a list of DOF names; valid names: {bounded_names(names)}.")
            unknown = [name for name in dofs if name not in names]
            if unknown:
                refuse(
                    "UNKNOWN_DOF",
                    f"dofs names {unknown[:8]!r}, which this articulation does not have. Valid DOF names: "
                    f"{bounded_names(names)}.",
                    valid_names=names,
                )
            selected = [names.index(name) for name in dofs] if dofs else list(range(len(names)))
            values = {}
            for label in ("stiffness", "damping"):
                if a.get(label) is not None:
                    values[label] = scalar(a[label])
            if a.get("max_effort") is not None:
                values["max_effort"] = scalar(a["max_effort"], 1e-9)
            from .physics import dof_gains, dof_max_forces, dof_row, dof_write, view_for

            view = view_for(a["path"], articulation=True)
            before_stiffness, before_damping = dof_gains(view)
            before_effort = dof_max_forces(view)
            if "stiffness" in values or "damping" in values:
                row = dof_row(view, before_stiffness)
                if "stiffness" in values:
                    row[0, selected] = values["stiffness"]
                if "damping" in values:
                    damping_row = dof_row(view, before_damping)
                    damping_row[0, selected] = values["damping"]
                    dof_write(view, "set_dof_dampings", damping_row)
                dof_write(view, "set_dof_stiffnesses", row)
            if "max_effort" in values:
                effort_row = dof_row(view, before_effort)
                effort_row[0, selected] = values["max_effort"]
                dof_write(view, "set_dof_max_forces", effort_row)
            after_stiffness, after_damping = dof_gains(view)
            after_effort = dof_max_forces(view)
            report = [
                dict(name=names[index], index=index,
                     before=dict(stiffness=before_stiffness[index], damping=before_damping[index],
                                 max_effort=before_effort[index]),
                     after=dict(stiffness=after_stiffness[index], damping=after_damping[index],
                                max_effort=after_effort[index]))
                for index in selected
            ]
            changed = any(
                item["after"]["stiffness"] != item["before"]["stiffness"]
                or item["after"]["damping"] != item["before"]["damping"]
                or item["after"]["max_effort"] != item["before"]["max_effort"]
                for item in report
            )
            return dict(
                path=a["path"],
                backend=state["backend"],
                dofs=report,
                read_only=not values,
                applied=changed,
                units="stiffness: torque per radian (force per stage unit); damping: torque per rad/s; "
                      "max_effort: torque (force for prismatic)",
                source=state["backend"] + " tensor view",
                effect="Runtime gains changed for the selected DOFs; they are not USD, are lost on Stop and "
                       "are never saved. sim__joints__drive authors USD gains instead.",
            )
        if name == "sim.joints.move":
            if s.timeline.is_stopped() or s.timeline.is_playing():
                refuse(
                    "PHYSICS_NOT_READY",
                    "Pause an initialized simulation first: from a stopped timeline, sim__timeline__control "
                    "action=play and then action=pause. Move does not initialize by playing or pumping frames.",
                )
            s.check_runtime_edit(stage)
            max_steps, tolerance = a.get("max_steps", 600), a.get("tolerance", 0.01)
            if type(max_steps) is not int or not 1 <= max_steps <= 600:
                refuse("LIMIT", "max_steps must be an integer 1..600.")
            if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or not 0 < tolerance <= 100:
                refuse("INVALID_VALUE", "tolerance must be a finite number greater than 0.")
            clamp = a.get("clamp", False)
            if type(clamp) is not bool:
                refuse("INVALID_VALUE", "clamp must be true or false.")
            state = self.joints(stage, a["path"])
            names = state["names"]
            if not names:
                refuse("JOINT_COUNT", "Selected articulation has no commandable DOFs.")
            from .physics import array as tensor_array, dof_limits, dof_set, dof_units, step, view_for

            view = view_for(a["path"], articulation=True)
            rows = resolve_named(a["targets"], names, "targets")
            limits, units = dof_limits(view), dof_units(view)
            applied, clamped, rounded = allowed_targets(
                rows, limits, state["positions"], units, clamp, "Target"
            )
            dof_set(view, "get_dof_position_targets", "set_dof_position_targets",
                    {index: row[2] for index, row in applied.items()})
            sm = physics_manager()
            before_time, dt = sm.get_simulation_time(), sm.get_physics_dt()
            if not finite_clock(before_time) or not finite_clock(dt) or dt <= 0:
                refuse("PHYSICS_CLOCK_INVALID",
                       "Runtime physics time must be finite and the timestep positive; no steps were taken.")
            tracked = {index: (row[0], row[2]) for index, row in applied.items()}
            positions, errors, steps_used, settled = state["positions"], [], 0, False
            phase = "stepping"
            try:
                while steps_used < max_steps and not settled:
                    chunk = min(10, max_steps - steps_used)
                    step(stage, chunk)
                    steps_used += chunk
                    phase = "observation"
                    positions = tensor_array(view.get_dof_positions()).reshape(-1).tolist()
                    errors = [
                        dict(name=name, index=index, target=target, position=positions[index],
                             error=positions[index] - target, units=units[index])
                        for index, (name, target) in tracked.items()
                    ]
                    settled = all(abs(item["error"]) <= tolerance for item in errors)
                phase = "clock_observation"
                after_time = sm.get_simulation_time()
            except Exception as exc:  # noqa: BLE001 - report partial execution, never retry
                if isinstance(exc, AssetError):
                    raise
                if any(token in str(exc).lower() for token in
                       ("cuda error 700", "cudaerrorillegaladdress", "illegal memory access")):
                    s.device_error = str(exc)[:500]
                    refuse("DEVICE_ERROR", str(exc)[:1500],
                           step=dict(steps_used=steps_used, phase=phase, backend=state["backend"]),
                           effects="GPU context may be unusable. Do not retry runtime operations; inspect, save, "
                                   "Stop, then restart the disposable Kit.")
                refuse("STEP_FAILED", str(exc)[:1500],
                       step=dict(steps_used=steps_used, phase=phase, backend=state["backend"]),
                       effects="Stepping was attempted and may have changed runtime state; no rollback or retry.")
            if not finite_clock(after_time):
                refuse("PHYSICS_CLOCK_INVALID",
                       "The physics clock is not finite after stepping; inspect the state before continuing.",
                       steps_used=steps_used, joints=errors)
            if not math.isclose(after_time - before_time, steps_used * dt, rel_tol=1e-5, abs_tol=1e-7):
                refuse("STEP_TIMING_MISMATCH",
                       "Observed physics time does not match the steps taken; inspect state before retrying.",
                       steps_used=steps_used, physics_time_before=before_time, physics_time_after=after_time,
                       expected_delta=steps_used * dt, joints=errors)
            largest = max(errors, key=lambda item: abs(item["error"])) if errors else None
            return dict(
                path=a["path"],
                backend=state["backend"],
                settled=settled,
                steps_used=steps_used,
                max_steps=max_steps,
                tolerance=tolerance,
                units="rad / stage units",
                physics_time_before=before_time,
                physics_time_after=after_time,
                simulated_seconds=after_time - before_time,
                joints=errors,
                clamped=clamped,
                rounded=rounded,
                **({"blocked": dict(
                    name=largest["name"], error=largest["error"], units=largest["units"],
                    note="The joint did not reach its target within max_steps. A contact, a too-weak drive or "
                        "a blocked path can hold it: clear the path, raise stiffness or max_effort with "
                        "sim__joints__gains, or retry with more steps.",
                )} if largest and not settled else {}),
            )
        if name == "sim.joints.teleport":
            state = self.joints(stage, a["path"])
            names = state["names"]
            if not names:
                refuse("JOINT_COUNT", "Selected articulation has no commandable DOFs.")
            from .physics import array as tensor_array, dof_limits, dof_set, dof_units, view_for

            view = view_for(a["path"], articulation=True)
            rows = resolve_named(a["positions"], names, "positions")
            limits, units = dof_limits(view), dof_units(view)
            for index, name, value in rows:
                low, high = limits[index]
                if value < low - LIMIT_ROUNDING * max(1.0, abs(low)) or value > high + LIMIT_ROUNDING * max(1.0, abs(high)):
                    refuse(
                        "JOINT_LIMIT",
                        f"Teleport {value:g} for {name} ({units[index]}) is outside its runtime limits "
                        f"[{low:g}, {high:g}]. Choose a value inside the limits: a state reset outside them is "
                        "unphysical.",
                        name=name, limits=[low, high], units=units[index],
                    )
            before = {index: state["positions"][index] for index, _, _ in rows}
            dof_set(view, "get_dof_positions", "set_dof_positions", {index: value for index, _, value in rows})
            zeroed = True
            try:
                dof_set(view, "get_dof_velocities", "set_dof_velocities", {index: 0.0 for index, _, _ in rows})
            except Exception:  # noqa: BLE001 - state reset still succeeded
                zeroed = False
            after = tensor_array(view.get_dof_positions()).reshape(-1).tolist()
            return dict(
                path=a["path"],
                backend=state["backend"],
                joints=[
                    dict(name=name, index=index, before=before[index], after=after[index], units=units[index])
                    for index, name, value in rows
                ],
                velocities_zeroed=zeroed,
                physics_bypassed=True,
                warning="Joint positions were set directly: no motion, contact response or damping was simulated. "
                        "Drive targets were left unchanged, so the next steps can pull the joints back; command "
                        "or move them if the new pose must hold.",
            )
        if name == "sim.joints.state":
            state = self.joints(stage, a["path"])
            past = joints_past_limits(state["names"], state["positions"], runtime_dof_limits(a["path"]))
            if past:
                state["warning"] = (f"Past their limits: {', '.join(past)}. Physics is probably unstable, and drive targets "
                                    "may not be reached. Check overlapping colliders, very small inertias and the timestep.")
            too_fast = joints_too_fast(state["names"], state["velocities"])
            if too_fast:
                state["warning"] = ((state.get("warning", "") + " ").lstrip() + f"Far too fast: {', '.join(too_fast)}. Physics is "
                                    "unstable, often because the robot started inside a collider such as the ground.")
            return state
        if name == "sim.simulation.step":
            if s.timeline.is_stopped() or s.timeline.is_playing():
                refuse(
                    "PHYSICS_NOT_READY",
                    "Pause an initialized simulation first: from a stopped timeline, sim__timeline__control action=play and then action=pause. Step does not initialize by playing or pumping frames.",
                )
            s.check_runtime_edit(stage)
            steps = a["steps"]
            bodies = a.get("observe_prims", [])
            arts = a.get("observe_joints", [])
            if (
                type(steps) is not int
                or not 1 <= steps <= 120
                or len(bodies) > 16
                or len(arts) > 8
            ):
                refuse("LIMIT", "steps 1..120, at most 16 bodies and 8 articulations.")
            target = dict(session_id=s.session_id, stage_id=s.stage_id)
            for p in bodies:
                self.dispatch("sim.body.state", dict(**target, path=p))
            for p in arts:
                self.joints(stage, p)
            sm = physics_manager()
            before = sm.get_simulation_time()
            dt = sm.get_physics_dt()
            if not finite_clock(before) or not finite_clock(dt) or dt <= 0:
                refuse(
                    "PHYSICS_CLOCK_INVALID",
                    "Runtime physics time and positive timestep must be finite; no steps requested.",
                )
            from .physics import step as backend_step, sync_fabric

            backend = sm.get_active_physics_engine()
            after = None
            fabric_updated = False
            phase = "native_step"
            try:
                backend_step(stage, steps)
                phase = "clock_observation"
                after = sm.get_simulation_time()
                if not finite_clock(after) or not math.isclose(
                    after - before, steps * dt, rel_tol=1e-5, abs_tol=1e-7
                ):
                    refuse(
                        "STEP_TIMING_MISMATCH",
                        "Observed physics time does not match the requested step interval; inspect state before retrying.",
                    )
                phase = "fabric_update"
                fabric_updated = sync_fabric(stage)
                phase = "body_observation"
                body_states = [
                    self.dispatch("sim.body.state", dict(**target, path=p))
                    for p in bodies
                ]
                phase = "joint_observation"
                joint_states = [self.joints(stage, p) for p in arts]
            except Exception as exc:
                # A clock is evidence of elapsed simulation time, not proof
                # that the solver produced a valid pose or completed each step.
                if after is None:
                    try:
                        after = sm.get_simulation_time()
                    except Exception:
                        pass
                if not finite_clock(after):
                    after = None
                details = dict(
                    requested_steps=steps,
                    physics_dt=dt,
                    physics_time_before=before,
                    physics_time_after=after,
                    measured_delta=None if after is None else after - before,
                    expected_delta=steps * dt,
                    phase=phase,
                    backend=backend,
                    fabric_updated=fabric_updated,
                    rendered=False,
                )
                effects = "Stepping was attempted and may have changed runtime state. No rollback or automatic retry was performed; inspect solver diagnostics before continuing."
                if isinstance(exc, AssetError):
                    exc.details.update(step=details, effects=effects)
                    raise
                message = str(exc)[:1500]
                if any(
                    token in message.lower()
                    for token in (
                        "cuda error 700",
                        "cudaerrorillegaladdress",
                        "illegal memory access",
                    )
                ):
                    s.device_error = message[:500]
                    refuse(
                        "DEVICE_ERROR",
                        message,
                        step=details,
                        effects="GPU context may be unusable. Do not retry runtime/sensor operations; inspect/save/Stop, then restart the disposable Kit.",
                    )
                refuse("STEP_FAILED", message, step=details, effects=effects)
            return dict(
                steps=steps,
                physics_dt=dt,
                physics_time_before=before,
                physics_time_after=after,
                measured_delta=after - before,
                expected_delta=steps * dt,
                timeline_time=s.timeline.get_current_time(),
                bodies=body_states,
                articulations=joint_states,
                backend=backend,
                fabric_updated=fabric_updated,
                rendered=False,
            )
        if name in ("sim.camera.create", "sim.lidar.create"):
            from . import placement

            p, parent = s.new_path(stage, a["path"])
            if str(p) in self.sensors:
                refuse(
                    "CONFLICT",
                    "Path still has registered sensor resources after external USD changes. "
                    "Use a new stage to release them before reusing this path.",
                )
            kind = "camera" if name == "sim.camera.create" else "lidar"
            parent_prim = stage.GetPrimAtPath(str(p).rsplit("/", 1)[0] or "/")
            if kind == "lidar" and parent_prim.IsA(UsdGeom.Gprim):
                refuse("INVALID_PARENT", f"{parent_prim.GetPath()} is a {parent_prim.GetTypeName()}, and USD can't nest a sensor "
                       "under geometry. Use an Xform parent, such as the link above it.")
            frame = a.get("frame", "local")
            lidar_asset = optics = None
            try:
                orientation, rotate_xyz = placement.orientation_args(a.get("orientation"), a.get("rotate_xyz"))
                tick = placement.tick_rate(a.get("tick_rate")) if kind == "lidar" else None
                if kind == "lidar" and a.get("config"):
                    from .compat import lidar_config_registry

                    lidar_asset = placement.resolve_lidar_config(
                        a["config"], a.get("variant") or None, lidar_config_registry()
                    )
                elif kind == "lidar" and a.get("variant"):
                    refuse("INVALID_VALUE", "variant needs config.")
                if kind == "camera":
                    optics = placement.camera_optics(
                        a.get("focal_length"), a.get("horizontal_aperture"),
                        a.get("clipping_range"), UsdGeom.GetStageMetersPerUnit(stage),
                    )
            except placement.PlacementError as e:
                refuse(e.code, str(e), **e.details)
            except ImportError as e:
                refuse("SENSOR_API_UNAVAILABLE", "Config LiDARs need isaacsim.sensors.rtx: " + str(e))
            pose = s.pose(stage, parent, a.get("position", [0, 0, 0]), orientation, frame)
            if name == "sim.camera.create" and a.get("target") is not None:
                if frame != "world":
                    refuse("INVALID_FRAME", "Camera target requires frame=world.")
                if rotate_xyz is not None:
                    refuse("INVALID_POSE", "target overrides the orientation; omit rotate_xyz.")
                eye = Gf.Vec3d(*vector(a.get("position", [0, 0, 0]), 3))
                target = Gf.Vec3d(*vector(a["target"], 3))
                up = (
                    Gf.Vec3d(0, 0, 1)
                    if UsdGeom.GetStageUpAxis(stage) == "Z"
                    else Gf.Vec3d(0, 1, 0)
                )
                direction = target - eye
                if (
                    direction.GetLength() < 1e-8
                    or Gf.Cross(direction, up).GetLength() < 1e-8
                ):
                    refuse(
                        "INVALID_POSE",
                        "Look-at needs distinct eye/target not parallel to stage up-axis.",
                    )
                pose = (
                    Gf.Matrix4d().SetLookAt(eye, target, up).GetInverse()
                    * UsdGeom.XformCache().GetLocalToWorldTransform(parent).GetInverse()
                )
                rotate_xyz = None
            if len(self.sensors) >= 8:
                refuse(
                    "LIMIT",
                    "At most eight live sensors per session; delete an owned sensor to release its resources.",
                )
            if name == "sim.camera.create":
                w = a.get("width", 640)
                h = a.get("height", 480)
                if (
                    type(w) is not int
                    or type(h) is not int
                    or not 64 <= w <= 1024
                    or not 64 <= h <= 1024
                ):
                    refuse("LIMIT", "width/height must be integers 64..1024.")
            from .sensors import create as create_sensor

            with Usd.EditContext(stage, stage.GetSessionLayer()):
                sensor = create_sensor(
                    stage,
                    p,
                    kind,
                    w if kind == "camera" else 128,
                    h if kind == "camera" else 128,
                    lidar_asset=lidar_asset,
                    tick_rate=tick,
                    optics=optics,
                )
                xf = UsdGeom.Xformable(stage.GetPrimAtPath(p))
                xf.ClearXformOpOrder()
                if rotate_xyz is not None and frame == "local":
                    # The ops a person authors in the Property panel: readable and editable there.
                    xf.AddTranslateOp().Set(Gf.Vec3d(*a.get("position", [0, 0, 0])))
                    xf.AddRotateXYZOp().Set(Gf.Vec3f(*rotate_xyz))
                else:
                    xf.AddTransformOp().Set(pose)
            self.sensors[str(p)] = (kind, sensor)
            s.owned.add(str(p))
            world = UsdGeom.Xformable(stage.GetPrimAtPath(p)).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            result = dict(
                path=str(p),
                kind=kind,
                prim_type=str(stage.GetPrimAtPath(sensor.get("sensor_path", str(p))).GetTypeName()),
                parent=str(parent.GetPath()),
                local_translate=list(pose.ExtractTranslation()),
                local_orientation_wxyz=[pose.ExtractRotationQuat().GetReal(), *pose.ExtractRotationQuat().GetImaginary()],
                **({"rotate_xyz_deg": rotate_xyz} if rotate_xyz is not None and frame == "local" else {}),
                world_position=list(world.ExtractTranslation()),
                ready=False,
                adapter=sensor["adapter"],
                release=sensor["release"],
                build=sensor["build"],
                note="Needs ordinary rendered warm-up; no implicit Play. Direct owned-sensor deletion releases render resources.",
            )
            if kind == "camera":
                hfov = placement.fov_degrees(sensor["focal_length_mm"], sensor["horizontal_aperture_mm"])
                result.update(
                    clipping_range_m=optics["clipping_range_m"],
                    clipping_range_stage_units=sensor["clipping_range_stage_units"],
                    resolution=[w, h],
                    focal_length_mm=sensor["focal_length_mm"],
                    horizontal_aperture_mm=sensor["horizontal_aperture_mm"],
                    vertical_aperture_mm=sensor["vertical_aperture_mm"],
                    horizontal_fov_deg=round(hfov, 3),
                    rate="A USD camera has no tick rate: it renders every frame. Set a ROS publishing rate on the ROS2 Camera Helper (frameSkipCount).",
                )
            else:
                from .ros2 import scan_graph_args, tf_name

                try:
                    import carb.settings

                    motion = bool(carb.settings.get_settings().get("/renderer/raytracingMotion/enabled"))
                except ImportError:  # host unit tests without Kit
                    motion = None
                result.update(
                    sensor_path=sensor["sensor_path"],
                    render_product=sensor.get("product_path"),
                    ros2_scan_graph=scan_graph_args(sensor["sensor_path"], tf_name(stage.GetPrimAtPath(sensor["sensor_path"]))),
                    ros2_note=(
                        "To publish /scan, pass ros2_scan_graph to sim__graph__build: it targets sensor_path, the OmniLidar, "
                        "and makes its own render product at Play, which survives a save. render_product is this "
                        "session's own, for sim__lidar__read; saves drop it. Scans are stamped with the sensor's own frame: pass "
                        "sensor_path in sim__ros2__tf_setup extra_frames so that frame is in the tree."
                    ),
                    tick_rate_hz=sensor["tick_rate_hz"],
                    scan_rate_hz=sensor["scan_rate_hz"],
                    config=a.get("config") or None,
                    **({"config_asset": sensor["config_asset"]} if "config_asset" in sensor else {}),
                    **({"variant": a.get("variant")} if a.get("variant") else {}),
                    motion_bvh=motion,
                    **({"warning": sensor["warning"]} if "warning" in sensor else {}),
                )
                if motion is False:
                    result["motion_note"] = (
                        "Motion BVH is off: scans are correct, but motion distortion within one scan "
                        "(sensor or objects moving during the sweep) is not modelled. Launch with "
                        "run.sh --motion-bvh for that."
                    )
            return result
        if name in ("sim.camera.capture", "sim.lidar.read"):
            entry = self.sensors.get(a["path"])
            expected = "camera" if name == "sim.camera.capture" else "lidar"
            if entry is None or entry[0] != expected:
                refuse("NOT_OWNED", "Sensor is not registered in this session.")
            sensor = entry[1]
            adoption = (
                dict(adopted=True, session_overrides=sensor.get("session_overrides", []),
                     **({"root_layer_note": sensor["root_layer_note"]} if "root_layer_note" in sensor else {}))
                if sensor.get("adopted") else {}
            )
            prim = s.prim(stage, a["path"])
            if not prim.IsActive():
                refuse("SENSOR_INVALIDATED", "Sensor prim is inactive.")
            if expected == "lidar":
                limit = a.get("limit", 64)
                if type(limit) is not int or not 1 <= limit <= 256:
                    refuse("LIMIT", "limit must be 1..256.")
                sensor_prim = stage.GetPrimAtPath(sensor.get("sensor_path") or a["path"])
                attr = sensor_prim.GetAttribute("omni:sensor:Core:outputFrameOfReference")
                if not attr or attr.Get() != "SENSOR":
                    refuse(
                        "SENSOR_MODIFIED",
                        "LiDAR output frame changed outside this tool; restore SENSOR before reading.",
                    )
                from .sensors import lidar_points

                return dict(path=a["path"], **adoption, **lidar_points(sensor, limit))
            h, w = sensor["resolution"]
            if not 64 <= h <= 1024 or not 64 <= w <= 1024:
                refuse(
                    "SENSOR_FORMAT",
                    "Camera resolution changed outside configured bounds.",
                )
            from .camera_capture import CameraFrame

            if not isinstance(camera_frame, CameraFrame):
                refuse(
                    "NOT_READY",
                    "Capture requires validated renderer acquisition or an explicit tool-acquired cache read.",
                )
            data = camera_frame.data
            if expected == "camera":
                import numpy as np
                from PIL import Image

                if (
                    not isinstance(data, np.ndarray)
                    or data.dtype != np.uint8
                    or data.shape not in ((h, w, 3), (h, w, 4))
                ):
                    refuse(
                        "SENSOR_FORMAT",
                        "Expected bounded CPU uint8 RGB/RGBA data; no GPU-pointer fallback.",
                    )
                array = np.array(data[:, :, :3], copy=True, order="C")
                filename = a["filename"]
                if not re.fullmatch(CAPTURE_NAME, filename):
                    refuse("INVALID_OUTPUT", CAPTURE_NAME_RULE)
                s.output.mkdir(mode=0o700, parents=True, exist_ok=True)
                from isaac_mcp.project_assets import open_dir

                fd = open_dir(s.output)
                try:
                    # Write beside it, then link: a no-replace publish, so a crash or a failed encode can't leave a
                    # partial image at the name a retry would then be refused for.
                    temp = f".{filename}.{os.getpid()}.tmp"
                    try:
                        os.unlink(temp, dir_fd=fd)  # left by a crash under a reused PID
                    except FileNotFoundError:
                        pass
                    out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                    try:
                        with os.fdopen(out, "wb") as stream:
                            Image.fromarray(array).save(stream, format="PNG")
                        os.link(temp, filename, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                    except FileExistsError:
                        refuse(
                            "CONFLICT",
                            f"{filename} already exists; use another filename. No overwrite. "
                            "The acquisition stays cached, so wait_for_render=false can save it.",
                        )
                    finally:
                        os.unlink(temp, dir_fd=fd)
                finally:
                    os.close(fd)
                file = s.output / filename
                return dict(
                    **adoption,
                    file=str(file),
                    width=array.shape[1],
                    height=array.shape[0],
                    sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                    source=(
                        "Hydra renderer CPU callback (owned copy)"
                        if camera_frame.fresh
                        else "Cached owned CPU pixels from the last validated tool acquisition"
                    ),
                    adapter="hydra-renderer-cpu-v1",
                    freshness=(
                        "new render and CPU transfer completed"
                        if camera_frame.fresh
                        else "cached acquisition; may predate Stop/reset, pose edits or physics steps"
                    ),
                )
        if name == "sim.graph.build":
            from .graph_build import build

            return build(stage, a, s.timeline.is_stopped())
        if name in ("sim.graph.inspect", "sim.graph.show", "sim.map.occupancy"):
            from . import graph

            if name == "sim.graph.inspect":
                limit = a.get("limit", 64)
                if type(limit) is not int or not 1 <= limit <= 128:
                    refuse("LIMIT", "limit must be 1..128.")
                return graph.inspect_graph(stage, a["path"], limit)
            if name == "sim.graph.show":
                if a.get("close"):
                    if graph._headless():
                        refuse("HEADLESS", "No Action Graph editor in a headless Isaac Sim.")
                    return graph.close_editor()
                if not a.get("path"):
                    refuse("INVALID_VALUE", "path: the graph prim to show (or close=true).")
                w, h = a.get("width", 0), a.get("height", 0)
                if type(w) is not int or type(h) is not int or (w or h) and not (300 <= w <= 8000 and 200 <= h <= 8000):
                    refuse("INVALID_VALUE", "width 300..8000 and height 200..8000, or both 0.")
                if a.get("layout") and not s.timeline.is_stopped():
                    refuse("TIMELINE_ACTIVE", "layout=true writes node positions into the stage: stop the timeline "
                           "first, or retry sim__graph__show with the same arguments and layout=false.",
                           retry_with={"session_id": a.get("session_id"), "stage_id": a.get("stage_id"),
                                       "path": a["path"], "width": w, "height": h, "layout": False})
                ready = graph.show_graph_open(stage, a["path"], w, h)
                return {"_finish": "sim.graph.show", "ready": ready}  # extension.py completes it
            return graph.occupancy_map(s, stage, a)
        if name == "sim.ros2.tf_setup":
            from .ros2 import setup

            return setup(s, stage, a)
        if name.startswith("sim.controller."):
            if os.getenv("ISAAC_MCP_ENABLE_SCENE_SCRIPTS") != "1":
                refuse(
                    "SCRIPT_DISABLED",
                    "User must opt in at Kit launch with ISAAC_MCP_ENABLE_SCENE_SCRIPTS=1. This executes arbitrary code.",
                )
            code = a["code"]
            p = a["path"]
            if not isinstance(code, str) or len(code) > 16384:
                refuse("LIMIT", "Script is limited to 16384 characters.")
            compile(code, "<trusted MCP controller>", "exec")
            import omni.graph.core as og

            if name.endswith("create"):
                s.new_path(stage, p)
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    og.Controller.edit(
                        dict(graph_path=p, evaluator_name="execution"),
                        {
                            og.Controller.Keys.CREATE_NODES: [
                                ("Tick", "omni.graph.action.OnPlaybackTick"),
                                ("Script", "omni.graph.scriptnode.ScriptNode"),
                            ],
                            og.Controller.Keys.CONNECT: [
                                ("Tick.outputs:tick", "Script.inputs:execIn")
                            ],
                            og.Controller.Keys.SET_VALUES: [
                                ("Script.inputs:usePath", False),
                                ("Script.inputs:script", code),
                            ],
                        },
                    )
                self.controllers.add(p)
                s.owned.add(p)
            else:
                if p not in self.controllers:
                    refuse("NOT_OWNED", "Not a pack-created controller.")
                with Usd.EditContext(stage, stage.GetSessionLayer()):
                    og.Controller.set(
                        og.Controller.attribute(p + "/Script.inputs:script"), code
                    )
                node = og.Controller.node(p + "/Script")
                og.Controller.set(node.get_attribute("state:omni_initialized"), False)
            return dict(
                path=p,
                code_sha256=hashlib.sha256(code.encode()).hexdigest(),
                executes="on playback tick",
                sandboxed=False,
            )
        refuse("UNKNOWN_TOOL", "No handler for " + name)

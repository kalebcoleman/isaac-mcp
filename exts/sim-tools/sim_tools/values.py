"""Checked numbers, colours and typed USD attribute values shared by the scene tools."""

import math
import re
from pxr import Gf, Sdf
from isaac_mcp.project_assets import refuse
from .core import vector


def scalar(x, lo=0, hi=1e8):
    if type(x) not in (float, int) or not math.isfinite(x) or not lo <= x <= hi:
        refuse("INVALID_VALUE", f"{x!r} is not valid here: a finite number in [{lo},{hi}] is required.")
    return x


def finite_clock(value):
    try:
        return not isinstance(value, bool) and math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def color(value):
    vector(value, 3)
    return Gf.Vec3f(*[scalar(x, 0, 1) for x in value])


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

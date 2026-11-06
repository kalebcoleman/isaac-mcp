"""Pure helpers for sensor placement, viewport framing and editor windows.

Stdlib only (no Kit, no pxr), so the host test suite can import and check them.
"""

import difflib
import math
from pathlib import PurePosixPath


class PlacementError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def _finite(values, n):
    return (
        isinstance(values, (list, tuple))
        and len(values) == n
        and all(type(v) in (int, float) and math.isfinite(v) for v in values)
    )


# ---------------------------------------------------------------- LiDAR configs


def config_aliases(asset_path):
    """Names accepted for one NVIDIA registry entry, e.g. '/Isaac/Sensors/NVIDIA/Example_Rotary_2D.usda'
    -> ('/Isaac/...usda', 'Example_Rotary_2D', 'Example Rotary 2D', 'Example_Rotary_2D', 'Example Rotary 2D').
    Same aliases as isaacsim.sensors.experimental.rtx (vendor-stripped stem too)."""
    p = PurePosixPath(asset_path)
    vendor = p.parts[3] if len(p.parts) > 3 else ""
    stem = p.stem
    short = stem[len(vendor) + 1:] if vendor and stem.startswith(vendor + "_") else stem
    return (asset_path, stem, stem.replace("_", " "), short, short.replace("_", " "))


def variant_names(variants):
    """Flat names a caller can pass as `variant` for one registry entry."""
    if isinstance(variants, (set, frozenset, list, tuple)) and all(isinstance(v, str) for v in variants):
        return sorted(variants)
    names = []
    for v in variants or ():
        if isinstance(v, dict):
            names.append("/".join(str(x) for x in v.values()))
    return sorted(names)


def resolve_lidar_config(config, variant, registry):
    """Map a config name (and optional variant) onto NVIDIA's lidar registry.

    ``registry`` is ``SUPPORTED_LIDAR_CONFIGS``: asset path -> set of flat variant names, or a list
    of {variant_set: value} dicts. Returns (asset_path, variant_for_native_api) where the variant
    is None, a str (variant set "sensor") or a dict. Raises PlacementError listing valid names.
    """
    if not isinstance(config, str) or not 1 <= len(config) <= 128:
        raise PlacementError("INVALID_VALUE", "config: an NVIDIA lidar config name, 1..128 characters.")
    match = None
    shorts, aliases = [], []
    for asset_path in registry:
        names = config_aliases(asset_path)
        if config in names:
            match = asset_path
            break
        shorts.append(names[3])
        aliases.extend(names)
    if match is None:
        close = difflib.get_close_matches(config, sorted(set(aliases)), n=3, cutoff=0.6)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise PlacementError(
            "UNKNOWN_CONFIG",
            f"Unknown lidar config {config!r}.{hint} Valid configs: {', '.join(sorted(set(shorts)))}.",
            valid_configs=sorted(set(shorts)),
        )
    variants = registry[match]
    valid = variant_names(variants)
    if variant in (None, ""):
        return match, None
    if not isinstance(variant, str) or variant not in valid:
        raise PlacementError(
            "UNKNOWN_VARIANT",
            f"Config {config!r} has no variant {variant!r}. "
            + (f"Valid variants: {', '.join(valid)}." if valid else "It has no variants; omit variant."),
            valid_variants=valid,
        )
    if isinstance(variants, (set, frozenset)) or all(isinstance(v, str) for v in variants):
        return match, variant
    for v in variants:
        if isinstance(v, dict) and "/".join(str(x) for x in v.values()) == variant:
            return match, dict(v)
    raise PlacementError("UNKNOWN_VARIANT", f"Variant {variant!r} could not be resolved.")


def tick_rate(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 240:
        raise PlacementError("INVALID_VALUE", "tick_rate must be a number in (0, 240] Hz.")
    return float(value)


# ---------------------------------------------------------------- orientation


def quat_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def euler_xyz_to_quat(degrees):
    """USD xformOp:rotateXYZ angles (degrees; X applied first, then Y, then Z) -> unit [w,x,y,z]."""
    if not _finite(degrees, 3) or any(abs(d) > 1e5 for d in degrees):
        raise PlacementError("INVALID_POSE", "rotate_xyz must be three finite angles in degrees.")
    qs = []
    for axis, deg in enumerate(degrees):
        half = math.radians(deg) / 2
        q = [math.cos(half), 0.0, 0.0, 0.0]
        q[1 + axis] = math.sin(half)
        qs.append(q)
    qx, qy, qz = qs
    q = quat_mul(qz, quat_mul(qy, qx))
    n = math.sqrt(sum(c * c for c in q))
    return [c / n for c in q]


def rotate(q, v):
    """Rotate vector v by unit quaternion q=[w,x,y,z]."""
    w, x, y, z = q
    p = quat_mul(quat_mul(q, (0.0, *v)), (w, -x, -y, -z))
    return list(p[1:])


def orientation_args(orientation, rotate_xyz):
    """Pick one orientation input. Returns (quaternion_wxyz, rotate_xyz_or_None)."""
    if rotate_xyz is not None:
        if orientation is not None and list(orientation) != [1, 0, 0, 0]:
            raise PlacementError("INVALID_POSE", "Give orientation (quaternion) or rotate_xyz (degrees), not both.")
        return euler_xyz_to_quat(rotate_xyz), list(rotate_xyz)
    return list(orientation if orientation is not None else [1, 0, 0, 0]), None


# ---------------------------------------------------------------- camera optics


def camera_optics(focal_length=None, horizontal_aperture=None, clipping_range=None, meters_per_unit=1.0):
    """Validate optics. Lengths in mm (USD camera convention, independent of stage units);
    clipping in metres, returned in stage units too."""
    out = {}
    for name, value in (("focal_length", focal_length), ("horizontal_aperture", horizontal_aperture)):
        if value is not None:
            if type(value) not in (int, float) or not math.isfinite(value) or not 0.1 <= value <= 1000:
                raise PlacementError("INVALID_VALUE", f"{name} must be 0.1..1000 mm.")
            out[name] = float(value)
    near, far = (0.01, 1000.0) if clipping_range is None else (None, None)
    if clipping_range is not None:
        if not _finite(clipping_range, 2):
            raise PlacementError("INVALID_VALUE", "clipping_range must be [near, far] in metres.")
        near, far = float(clipping_range[0]), float(clipping_range[1])
        if not (1e-5 <= near < far <= 1e7) or far / near > 1e9:
            raise PlacementError("INVALID_VALUE", "clipping_range needs 1e-5 <= near < far <= 1e7 m (far/near <= 1e9).")
    if not math.isfinite(meters_per_unit) or meters_per_unit <= 0:
        raise PlacementError("INVALID_STAGE_UNITS", "Stage metersPerUnit must be finite and positive.")
    out["clipping_range_m"] = [near, far]
    out["clipping_range_stage_units"] = [near / meters_per_unit, far / meters_per_unit]
    return out


def fov_degrees(focal_length, aperture):
    return math.degrees(2 * math.atan(aperture / (2 * focal_length)))


# ---------------------------------------------------------------- viewport framing


def fit_distance(radius, focal_length, horizontal_aperture, aspect):
    """Camera distance at which a sphere of ``radius`` fits both image axes.

    The viewport keeps the horizontal aperture and derives the rendered vertical aperture from
    its own aspect ratio (width/height), so the authored vertical aperture is not used.
    """
    if not (radius > 0 and focal_length > 0 and horizontal_aperture > 0 and aspect > 0):
        raise PlacementError("NO_BOUNDS", "Cannot frame: radius, focal length, aperture and aspect must be positive.")
    half = min(math.atan(horizontal_aperture / (2 * focal_length)),
               math.atan(horizontal_aperture / aspect / (2 * focal_length)))
    return radius / math.sin(half)


def frame_eye(center, radius, forward, focal_length, horizontal_aperture, aspect):
    """Eye position that keeps the current view direction ``forward`` and fits the sphere."""
    n = math.sqrt(sum(c * c for c in forward))
    if not n > 1e-9:
        raise PlacementError("VIEWPORT_CAMERA_UNSUPPORTED", "The viewport camera has no usable view direction.")
    d = fit_distance(radius, focal_length, horizontal_aperture, aspect)
    return [c - f / n * d for c, f in zip(center, forward)], d


def view_up(eye, target, up):
    """The up vector to look with: the stage's, or a sideways one when the view runs along it (a straight-down map view)."""
    d = [t - e for e, t in zip(eye, target)]
    length = math.sqrt(sum(c * c for c in d))
    if length < 1e-6:
        return list(up)
    cross = (d[1] * up[2] - d[2] * up[1], d[2] * up[0] - d[0] * up[2], d[0] * up[1] - d[1] * up[0])
    if math.sqrt(sum(c * c for c in cross)) >= 1e-6 * length:
        return list(up)
    return [0.0, 1.0, 0.0] if abs(up[2]) > 0.5 else [0.0, 0.0, -1.0]  # Z-up: image up is +Y; Y-up: image up is -Z


def look_at_valid(eye, target, up):
    if not (_finite(eye, 3) and _finite(target, 3)):
        raise PlacementError("INVALID_POSE", "eye and target must be three finite world coordinates.")
    d = [t - e for e, t in zip(eye, target)]
    length = math.sqrt(sum(c * c for c in d))
    if length < 1e-6:
        raise PlacementError("INVALID_POSE", "eye and target must differ.")
    cross = (d[1] * up[2] - d[2] * up[1], d[2] * up[0] - d[0] * up[2], d[0] * up[1] - d[1] * up[0])
    if math.sqrt(sum(c * c for c in cross)) < 1e-6 * length:
        raise PlacementError("INVALID_POSE", "The view direction is parallel to the stage up axis; move eye sideways a little.")
    return length


# ---------------------------------------------------------------- editor window


def clamp_window(width, height, x, y, main_width, main_height, margin=8, top=30):
    """Keep a floating Kit window (and its title-bar close button) inside the main window.

    Kit's undocked windows float *inside* the application's main window, so they must fit
    within its current size. Returns (width, height, x, y) in pixels.
    """
    max_w = max(200.0, float(main_width) - 2 * margin)
    max_h = max(150.0, float(main_height) - top - margin)
    w = min(float(width), max_w)
    h = min(float(height), max_h)
    x = min(max(float(x), margin), max(margin, float(main_width) - margin - w))
    y = min(max(float(y), top), max(top, float(main_height) - margin - h))
    return w, h, x, y


PLACEHOLDER_BBOX = (500, 500, 0, 0)


def bbox_settled(previous, current):
    """The graph view reports a 500x500 placeholder bbox until its nodes are built; the view is
    settled once a real (bbox, zoom, pan) state repeats on two consecutive frames."""
    if current is None or tuple(current[:4]) == PLACEHOLDER_BBOX:
        return False
    return previous is not None and len(previous) == len(current) and all(
        abs(a - b) <= 1e-3 * max(1.0, abs(a)) for a, b in zip(previous, current))


FLAT_BOX_RATIO = 50.0


def flat_box_warning(kind, scale, collision):
    """A warning for a very flat box collider, else None.

    Measured with TurtleBot3 and Carter on 6.2: UsdGeom.Cylinder wheels got no contact from a Cube floor scaled 20x20x0.1
    (nor at 133:1, 200:1), so the wheels spun at exactly v/r while the robot rested on its box colliders or fell through.
    Kit's convex cylinders, a plane and a mesh floor did not. Carter was also erratic on floors thinner than its wheels at
    50:1 and 100:1, which is why this warns from 50:1.
    """
    if kind != "Cube" or not collision:
        return None
    sides = [abs(float(value)) for value in scale]
    if not sides or min(sides) <= 0 or max(sides) / min(sides) < FLAT_BOX_RATIO:
        return None
    return (f"This box collider is {max(sides) / min(sides):.0f}:1. Cylinder colliders, such as most robots' wheels, can get no "
            "contact from a box this flat: the wheels spin but the robot stays put or falls through. For a floor, use "
            "sim__quick_start's ground. A thicker box, thicker than the wheels, also works. See docs/ROS2-RVIZ.md.")

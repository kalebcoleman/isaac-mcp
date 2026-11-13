"""World-space bounds, distances and camera listing. Authored USD only.

No app update, render, physics step or arbitrary attribute access. Every number comes from
UsdGeom.BBoxCache.ComputeWorldBound or the camera's own authored attributes.
"""

import math

from pxr import Gf, Usd, UsdGeom
from twinsmith.project_assets import refuse

PURPOSES = {
    "default": UsdGeom.Tokens.default_,
    "render": UsdGeom.Tokens.render,
    "proxy": UsdGeom.Tokens.proxy,
    "guide": UsdGeom.Tokens.guide,
}
CAMERA_VISIT_CAP = 10000


def _purpose(value):
    if value not in PURPOSES:
        refuse("INVALID_VALUE", "purpose must be default, render, proxy or guide.")
    return PURPOSES[value]


def _point(value):
    return [float(value[0]), float(value[1]), float(value[2])]


def _finite_point(value):
    return all(math.isfinite(float(x)) for x in value)


def world_bound(stage, value, purpose="default"):
    """The prim's world bound as axis-aligned and oriented data, or empty."""
    from .core import path as prim_path

    p = prim_path(value)
    prim = stage.GetPrimAtPath(p)
    if not prim:
        refuse("NOT_FOUND", "No prim at " + value + ". Find it with sim__prim__find or sim__stage__tree.")
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [_purpose(purpose)])
    box = cache.ComputeWorldBound(prim)
    aligned = box.ComputeAlignedRange()
    oriented = box.GetRange()
    if aligned.IsEmpty() and oriented.IsEmpty():
        return dict(
            path=str(prim.GetPath()),
            purpose=purpose,
            empty=True,
            axis_aligned=None,
            oriented=None,
            units="stage units",
            time_sample="default",
            source="UsdGeom.BBoxCache.ComputeWorldBound",
            note="This prim has no boundable content for the requested purpose.",
        )
    low, high = aligned.GetMin(), aligned.GetMax()
    if not (_finite_point(low) and _finite_point(high)):
        refuse(
            "INVALID_BOUNDS",
            f"USD returned non-finite world bounds for {value}; no measurement was reported.",
        )
    centre = [(float(low[i]) + float(high[i])) / 2 for i in range(3)]
    size = [float(high[i]) - float(low[i]) for i in range(3)]
    matrix = box.GetMatrix()
    corners = []
    for x in (oriented.GetMin()[0], oriented.GetMax()[0]):
        for y in (oriented.GetMin()[1], oriented.GetMax()[1]):
            for z in (oriented.GetMin()[2], oriented.GetMax()[2]):
                corners.append(_point(matrix.Transform(Gf.Vec3d(x, y, z))))
    return dict(
        path=str(prim.GetPath()),
        purpose=purpose,
        empty=False,
        axis_aligned=dict(
            min=_point(low),
            max=_point(high),
            centre=centre,
            size=size,
        ),
        oriented=dict(
            centre=_point(matrix.Transform(oriented.GetMidpoint())),
            corners=corners,
        ),
        units="stage units",
        time_sample="default",
        source="UsdGeom.BBoxCache.ComputeWorldBound",
        note="Axis-aligned range in world; oriented corners are the unaligned box mapped to world.",
    )


def measure(stage, path_a, path_b):
    """Centre distance and shortest gap between two world axis-aligned bounds."""
    a = world_bound(stage, path_a)
    b = world_bound(stage, path_b)
    for name, value in (("path_a", a), ("path_b", b)):
        if value["empty"]:
            refuse(
                "NO_BOUNDS",
                f"{name} {value['path']} has no world bounds for purpose=default, so no distance can be "
                "measured. Check it with sim__prim__bounds, or choose a prim with visible geometry.",
                path=value["path"],
                argument=name,
            )
    amin, amax = a["axis_aligned"]["min"], a["axis_aligned"]["max"]
    bmin, bmax = b["axis_aligned"]["min"], b["axis_aligned"]["max"]
    axis_gap = [max(0.0, amin[i] - bmax[i], bmin[i] - amax[i]) for i in range(3)]
    gap = math.sqrt(sum(x * x for x in axis_gap))
    centre_distance = math.sqrt(
        sum((a["axis_aligned"]["centre"][i] - b["axis_aligned"]["centre"][i]) ** 2 for i in range(3))
    )
    return dict(
        path_a=a["path"],
        path_b=b["path"],
        centre_distance=centre_distance,
        gap=gap,
        overlapping=gap == 0.0,
        axis_gap=axis_gap,
        units="stage units",
        time_sample="default",
        bounds=dict(a=a["axis_aligned"], b=b["axis_aligned"]),
        method="world axis-aligned bounds from UsdGeom.BBoxCache.ComputeWorldBound; gap is the shortest "
               "distance between the boxes (0 when they overlap)",
        note="Authored USD geometry, not runtime physics collision state.",
    )


def _value(attr):
    value = attr.Get()
    return value


def _camera(prim, active_camera):
    camera = UsdGeom.Camera(prim)
    xform = UsdGeom.Xformable(prim)
    world = xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default()) if xform else None
    quat = world.ExtractRotationQuat() if world is not None else None
    clipping = _value(camera.GetClippingRangeAttr())
    meters = UsdGeom.GetStageMetersPerUnit(prim.GetStage())
    return dict(
        path=str(prim.GetPath()),
        active=prim.IsActive(),
        active_viewport_camera=active_camera is not None and str(prim.GetPath()) == active_camera,
        projection=str(_value(camera.GetProjectionAttr())),
        focal_length_mm=float(_value(camera.GetFocalLengthAttr())),
        horizontal_aperture_mm=float(_value(camera.GetHorizontalApertureAttr())),
        vertical_aperture_mm=float(_value(camera.GetVerticalApertureAttr())),
        horizontal_aperture_offset_mm=float(_value(camera.GetHorizontalApertureOffsetAttr())),
        vertical_aperture_offset_mm=float(_value(camera.GetVerticalApertureOffsetAttr())),
        clipping_range_stage_units=[float(clipping[0]), float(clipping[1])],
        clipping_range_m=[float(clipping[0]) * meters, float(clipping[1]) * meters],
        f_stop=float(_value(camera.GetFStopAttr())),
        focus_distance_stage_units=float(_value(camera.GetFocusDistanceAttr())),
        world_position=_point(world.ExtractTranslation()) if world is not None else None,
        world_orientation_wxyz=(
            [float(quat.GetReal()), *[float(x) for x in quat.GetImaginary()]]
            if quat is not None
            else None
        ),
        units="apertures and focal length in mm; clipping range in stage units and metres",
    )


def active_viewport_camera():
    """(camera_path, viewport_available) from Kit, or (None, False) with no usable viewport."""
    try:
        from omni.kit.viewport.utility import get_active_viewport

        viewport = get_active_viewport()
        if viewport is None:
            return None, False
        path = getattr(viewport, "camera_path", None)
        return (str(path) if path else None), True
    except Exception:  # noqa: BLE001 - headless or a build without the viewport utility
        return None, False


def cameras(stage, root="/", limit=100, active_camera=None, viewport_available=None):
    from .core import path as prim_path

    if type(limit) is not int or not 1 <= limit <= 100:
        refuse("LIMIT", "limit must be 1..100.")
    root_path = prim_path(root, True)
    if not stage.GetPrimAtPath(root_path):
        refuse("NOT_FOUND", "No prim at requested root. List paths with sim__stage__tree root=\"/\".")
    if viewport_available is None:
        active_camera, viewport_available = active_viewport_camera()
    found, visited, truncated = [], 0, False
    for prim in stage.TraverseAll():
        visited += 1
        if visited > CAMERA_VISIT_CAP:
            truncated = True
            break
        if not prim.GetPath().HasPrefix(root_path):
            continue
        if prim.GetTypeName() != "Camera":
            continue
        if len(found) == limit:
            truncated = True
            break
        found.append(_camera(prim, active_camera))
    return dict(
        cameras=found,
        truncated=truncated,
        visited=min(visited, CAMERA_VISIT_CAP),
        root=str(root_path),
        viewport_available=bool(viewport_available),
        active_camera=active_camera if viewport_available else None,
        units="stage units for poses and clipping; mm for focal length and apertures",
        source="authored USD Camera attributes; active camera from the active viewport",
        note="A listed camera is not proof that it has render resources or that a frame was rendered.",
    )

"""Validate proposed read-only scene APIs in a disposable headless Kit process.

Run with an Isaac Sim Python environment; see docs/VERIFICATION.md. This creates
a synthetic stage in the new process and does not explicitly enable the UR5e extension.
The final GENERIC_SCENE_VALIDATION line is JSON; assertion failures exit nonzero.
"""

import importlib.metadata
import inspect
import json
import os
import platform
import re
import sys
import traceback



def _distribution_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _path_is_valid(sdf, path):
    # Normalize bool-like results and tuple-shaped results from USD bindings.
    result = sdf.Path.IsValidPathString(path)
    return bool(result[0] if isinstance(result, tuple) else result)


def _range(bound):
    aligned = bound.ComputeAlignedRange()
    return {"min": list(aligned.GetMin()), "max": list(aligned.GetMax())}


def _helper_result(fn, *args):
    """Record version-specific helper behavior without assuming it is portable."""
    try:
        return {"paths": sorted(fn(*args))}
    except Exception as error:
        return {"error_type": type(error).__name__, "message": str(error)}


def run_checks():
    # Kit APIs are available only after SimulationApp has started.
    import omni.timeline
    import omni.usd
    from pxr import Gf, Sdf, Usd, UsdGeom

    context = omni.usd.get_context()
    if not context.new_stage():
        raise RuntimeError("could not create a disposable stage")
    stage = context.get_stage()
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)

    group = UsdGeom.Xform.Define(stage, "/World/GroupA")
    group.AddTranslateOp().Set(Gf.Vec3d(10, 20, 30))
    cube = UsdGeom.Cube.Define(stage, "/World/GroupA/Cube")
    cube.CreateSizeAttr(2.0)
    cube.AddTranslateOp().Set(Gf.Vec3d(1, 2, 3))
    cube.GetPrim().CreateAttribute("user:sample", Sdf.ValueTypeNames.String).Set("hello")
    UsdGeom.Xform.Define(stage, "/World/GroupB")
    UsdGeom.Cube.Define(stage, "/World/GroupB/Cube")
    UsdGeom.Sphere.Define(stage, "/World/GroupB/Sphere")
    UsdGeom.Scope.Define(stage, "/World/Scope")
    UsdGeom.Xform.Define(stage, "/World/Inactive").GetPrim().SetActive(False)

    checks = []

    def check(name, actual, expected):
        if actual != expected:
            raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
        checks.append(name)

    # Proposed stage.info and stage.tree: use the current Kit USD context.
    check("default_prim", str(stage.GetDefaultPrim().GetPath()), "/World")
    check("up_axis", str(UsdGeom.GetStageUpAxis(stage)), "Z")
    check("meters_per_unit", UsdGeom.GetStageMetersPerUnit(stage), 0.01)
    timeline = omni.timeline.get_timeline_interface()
    check("timeline_playing", timeline.is_playing(), False)
    check("timeline_time", timeline.get_current_time(), 0.0)
    check("active_children", sorted(p.GetName() for p in world.GetPrim().GetChildren()),
          ["GroupA", "GroupB", "Scope"])
    check("all_children", sorted(p.GetName() for p in world.GetPrim().GetAllChildren()),
          ["GroupA", "GroupB", "Inactive", "Scope"])
    active = {str(p.GetPath()) for p in stage.Traverse()}
    all_prims = {str(p.GetPath()) for p in stage.TraverseAll()}
    check("inactive_excluded_from_traverse", "/World/Inactive" in active, False)
    check("inactive_in_traverse_all", "/World/Inactive" in all_prims, True)

    # Proposed prim.get: check path validity, type, attribute, transform and
    # all three different USD bounds spaces on a translated cube.
    check("valid_prim_path", _path_is_valid(Sdf, "/World/GroupA/Cube"), True)
    check("invalid_prim_path", _path_is_valid(Sdf, "not valid"), False)
    check("relative_path_is_valid_syntax", _path_is_valid(Sdf, "GroupA"), True)
    check("relative_path_is_not_absolute", Sdf.Path("GroupA").IsAbsolutePath(), False)
    check("missing_prim", bool(stage.GetPrimAtPath("/World/Missing")), False)
    check("scope_is_not_xformable", bool(UsdGeom.Xformable(stage.GetPrimAtPath("/World/Scope"))), False)
    prim = stage.GetPrimAtPath("/World/GroupA/Cube")
    check("prim_type", prim.GetTypeName(), "Cube")
    attribute = prim.GetAttribute("user:sample")
    check("attribute_value", attribute.Get(), "hello")
    check("attribute_type", str(attribute.GetTypeName()), "string")
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    check("world_translation", list(matrix.ExtractTranslation()), [11.0, 22.0, 33.0])
    rotation = matrix.ExtractRotationQuat()
    check("world_orientation_wxyz", [rotation.GetReal(), *rotation.GetImaginary()], [1.0, 0.0, 0.0, 0.0])
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy])
    bounds = {
        "world": _range(cache.ComputeWorldBound(prim)),
        "local": _range(cache.ComputeLocalBound(prim)),
        "untransformed": _range(cache.ComputeUntransformedBound(prim)),
    }
    check("world_bounds", bounds["world"], {"min": [10.0, 21.0, 32.0], "max": [12.0, 23.0, 34.0]})
    check("local_bounds", bounds["local"], {"min": [0.0, 1.0, 2.0], "max": [2.0, 3.0, 4.0]})
    check("untransformed_bounds", bounds["untransformed"],
          {"min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0]})

    # Proposed prim.find: Python regex plus USD type names can express the
    # proposed general search even where Isaac's helper is more restricted.
    expression = re.compile(r"/World/Group(A|B)/Cube")
    matches = sorted(str(p.GetPath()) for p in stage.Traverse()
                     if expression.fullmatch(str(p.GetPath())) and p.GetTypeName() == "Cube")
    check("regex_and_usd_type", matches, ["/World/GroupA/Cube", "/World/GroupB/Cube"])
    anchored = re.compile(r"^/World/Group[AB]/Cube$")
    check("anchored_regex", sorted(path for path in active if anchored.fullmatch(path)), matches)
    try:
        re.compile("[")
    except re.error:
        checks.append("invalid_regex_rejected")
    else:
        raise AssertionError("invalid regex was accepted")

    stage_url = context.get_stage_url()
    invalid_path_result = Sdf.Path.IsValidPathString("not valid")
    outside_world = sorted(path for path in active if path != "/World" and not path.startswith("/World/"))
    observations = {
        "stage_url_is_anonymous": isinstance(stage_url, str) and stage_url.startswith("anon:"),
        "stage_state": str(context.get_stage_state()),
        "invalid_path_result_type": type(invalid_path_result).__name__,
        "outside_world_active_prim_count": len(outside_world),
        "outside_world_active_prim_sample": outside_world[:5],
        "bounds": bounds,
        "regex_and_usd_type": matches,
    }
    try:
        from isaacsim.core.utils import prims as prim_utils, stage as stage_utils
    except ImportError as error:
        observations["isaacsim_core_utils"] = {"available": False, "message": str(error)}
    else:
        check("helper_up_axis", str(stage_utils.get_stage_up_axis()), "Z")
        check("helper_meters_per_unit", stage_utils.get_stage_units(), 0.01)
        pattern = "/World/Group.*/Cube"
        observations["isaacsim_core_utils"] = {
            "available": True,
            "wildcard": _helper_result(prim_utils.find_matching_prim_paths, pattern),
            "regex_alternation": _helper_result(prim_utils.find_matching_prim_paths,
                                                "/World/Group(A|B)/Cube"),
            "regex_anchors_and_class": _helper_result(prim_utils.find_matching_prim_paths,
                                                       "^/World/Group[AB]/Cube$"),
            "invalid_regex": _helper_result(prim_utils.find_matching_prim_paths, "["),
            "usd_type_filter": _helper_result(prim_utils.find_matching_prim_paths, pattern, "Cube"),
        }
    report = {"status": "passed", "checks": checks, "observations": observations,
              "isaacsim_distribution": _distribution_version("isaacsim"),
              "python": platform.python_version(), "platform": platform.platform()}
    return report


def main():
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True})
    try:
        report = run_checks()
        print("GENERIC_SCENE_VALIDATION=" + json.dumps(report, sort_keys=True), flush=True)
    except BaseException:
        # Some Kit versions terminate with status 0 during fast shutdown even
        # while an exception is propagating. Print it first and preserve failure.
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            supports_exit_code = "exit_code" in inspect.signature(app.close).parameters
        except (TypeError, ValueError):
            supports_exit_code = False
        if not supports_exit_code:
            # Older SimulationApp.close has no way to preserve a failing code
            # during fast shutdown. This is a disposable process and stage.
            os._exit(1)
        app.close(exit_code=1)
        raise
    else:
        app.close()


if __name__ == "__main__":
    main()

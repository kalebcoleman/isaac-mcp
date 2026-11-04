"""Explicit export normalization of camera render handles, never the live stage.

An active native binding is not proof of USD authorship. The caller's explicit
export policy authorizes the omission; the checks only constrain its scope.
"""

import re

from pxr import Sdf, Usd, UsdGeom
from isaac_mcp.project_assets import refuse

PRODUCER = "isaacsim.core.nodes.IsaacCreateRenderProduct"
_ROS_NAMESPACES = ("isaacsim.ros2.bridge.", "isaacsim.ros2.nodes.")
CAMERA_HELPERS = {ns + name for ns in _ROS_NAMESPACES for name in ("ROS2CameraHelper", "ROS2CameraInfoHelper")}
LIDAR_HELPERS = {ns + "ROS2RtxLidarHelper" for ns in _ROS_NAMESPACES}
HELPERS = CAMERA_HELPERS | LIDAR_HELPERS
# sim_tools.sensors names its private capture products this way; save removes the whole prim.
OWNED_PRODUCT = re.compile(r"IsaacMCP_[0-9a-f]{32}")
VIEWPORT_PRODUCT_PREFIX = "omni_kit_widget_viewport_"
MAX_PRIMS = 10000
MAX_SPECS = 200000
MAX_GRAPHS = 32
MAX_REPORTED_HANDLES = 16
PUBLISHER_NOTE = ("Publisher count is not read from inside Kit: run `ros2 topic info <topic>` in the shell line from "
                  "sim__stage__info. writer_computes 0 while playing means the writer never ran and the topic is not advertised.")
STALE_HANDLE_FIX = (
    "On 6.2, Replicator's attach_hydra_texture skips a product that already has viewportHandle, so a saved handle leaves it "
    "with no texture: the sensor renders nothing, its ROS writer never runs and the topic is not advertised. "
    "Rebuilding the graph reuses the same product. Save a clean copy with reset_camera_render_handles=true and open it "
    "with sim__stage__open, or rebuild the graph with another width/height.")
STALE_RESCUE = (
    "Before any Play, with the timeline stopped, call sim__stage__save with a new filename and "
    "reset_camera_render_handles=true, then sim__stage__open the new file. It has no saved LiDAR handle or runtime "
    "pipeline, and Play builds both again. If you already played, open the original again first: on some builds Play adds "
    "a second product, and the save then refuses.")


# A reference or payload to a prim path that does not exist contributes no specs, so it cannot hide a
# render-handle dependency. Lab handouts ship with such dangling internal references (the TurtleBot3
# scene has two), and refusing on them left students with no way to save at all.
HARMLESS_COMPOSITION_ERRORS = ("ErrorUnresolvedPrimPath",)


def blocking_composition_errors(errors):
    """Composition errors that could hide content from the dependency scan (all but harmless kinds)."""
    return [e for e in errors if type(e).__name__ not in HARMLESS_COMPOSITION_ERRORS]


def unsupported(message, path, **details):
    refuse("SAVE_UNSUPPORTED", message + " Live stage and source files preserved.",
           path=str(path), **details)


def runtime_camera_target(stage, producer):
    """Read the existing OmniGraph target and output; never create a product or guess a camera."""
    try:
        import omni.graph.core as og
        import omni.usd

        if omni.usd.get_context().get_stage() != stage:
            raise ValueError("the graph is not on the current stage")
        node = og.Controller.node(str(producer.GetPath()))
        cameras = og.Controller.get(node.get_attribute("inputs:cameraPrim"))
        product = og.Controller.get(node.get_attribute("outputs:renderProductPath"))
        if not isinstance(cameras, (list, tuple)) or len(cameras) != 1:
            raise ValueError("runtime camera target is absent or ambiguous")
        return Sdf.Path(str(cameras[0])), Sdf.Path(str(product))
    except Exception as exc:
        unsupported(f"Cannot resolve camera for producer {producer.GetPath()}: {str(exc)[:250]}. "
                    "Inspect its cameraPrim target and rebuild or set the camera target before saving.",
                    producer.GetPath(), camera=None)


def runtime_node_resolution(stage, producer):
    """(width, height) of a live producer with unauthored inputs (its OGN default), or None off-stage."""
    try:
        import omni.graph.core as og
        import omni.usd

        if omni.usd.get_context().get_stage() != stage:
            return None
        node = og.Controller.node(str(producer.GetPath()))
        return (int(og.Controller.get(node.get_attribute("inputs:width"))),
                int(og.Controller.get(node.get_attribute("inputs:height"))))
    except Exception:  # noqa: BLE001 - no Kit, or a node that is not on this graph: not resolvable
        return None


def session_only(attr, stage):
    """True when every spec of *attr* is in the session layer: a runtime value, not a saved one."""
    stack = attr.GetPropertyStack()
    return bool(stack) and all(spec.layer == stage.GetSessionLayer() for spec in stack)


def camera_handles(stage, reset, binding_check, target_resolver=None):
    """Select bounded, unambiguous helper products before provenance is flattened."""
    if type(reset) is not bool:
        refuse("INVALID_ARGUMENT", "reset_camera_render_handles must be boolean.")
    prims = []
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)):
        if len(prims) == MAX_PRIMS:
            unsupported("Render-handle inspection exceeds 10000 prims.", "/")
        prims.append(prim)
    products = [p for p in prims if p.GetTypeName() == "RenderProduct"]
    selected = {}
    repairs = {}
    # An empty target must be resolved even before the first Play, when no
    # render product or viewport handle exists yet. Inspect all active,
    # concrete producers, including ones used by non-ROS camera helpers.
    # Inactive and abstract producers do not execute in the exported stage.
    for producer in prims:
        if (not producer.IsActive() or producer.IsAbstract()
                or producer.GetAttribute("node:type").Get() != PRODUCER):
            continue
        rel = producer.GetRelationship("inputs:cameraPrim")
        if not rel and producer.GetAttribute("inputs:cameraPrim"):
            unsupported(f"Camera producer {producer.GetPath()} has an attribute instead of an "
                        "inputs:cameraPrim relationship; rebuild or set the camera target before saving.",
                        producer.GetPath(), camera=None)
        cameras = rel.GetTargets() if rel else []
        if cameras:
            if len(cameras) != 1:
                unsupported(f"Camera producer {producer.GetPath()} needs exactly one camera target.",
                            producer.GetPath(), camera=[str(c) for c in cameras])
            continue
        width_attr = producer.GetAttribute("inputs:width")
        height_attr = producer.GetAttribute("inputs:height")
        width = width_attr.Get() if width_attr else None
        height = height_attr.Get() if height_attr else None
        if type(width) is not int or type(height) is not int:
            unsupported(f"Camera producer {producer.GetPath()} needs explicit integer width and height.",
                        producer.GetPath(), camera=[])
        camera, runtime_product = (target_resolver or runtime_camera_target)(stage, producer)
        if (not camera.IsAbsolutePath() or not camera.IsPrimPath()
                or not runtime_product.IsAbsolutePath() or not runtime_product.IsPrimPath()):
            unsupported(f"Cannot verify runtime camera {camera} and render product {runtime_product} "
                        f"for producer {producer.GetPath()}; inspect cameraPrim and resolution.",
                        producer.GetPath(), camera=str(camera), product=str(runtime_product))
        camera_prim, product_prim = stage.GetPrimAtPath(camera), stage.GetPrimAtPath(runtime_product)
        if (not camera_prim or not camera_prim.IsA(UsdGeom.Camera) or not product_prim
                or product_prim.GetTypeName() != "RenderProduct"
                or product_prim.GetRelationship("camera").GetTargets() != [camera]
                or tuple(product_prim.GetAttribute("resolution").Get() or ()) != (width, height)):
            unsupported(f"Cannot verify runtime camera {camera} and render product {runtime_product} "
                        f"for producer {producer.GetPath()}; inspect cameraPrim and resolution.",
                        producer.GetPath(), camera=str(camera), product=str(runtime_product))
        explicit_rel = producer.GetRelationship("inputs:renderProductPrim")
        explicit = explicit_rel.GetTargets() if explicit_rel else []
        if explicit and explicit != [runtime_product]:
            unsupported("Explicit camera product does not match the observed runtime product.",
                        producer.GetPath(), camera=str(camera), product=str(runtime_product))
        if producer.IsInstance() or producer.IsInstanceProxy() or producer.IsInPrototype():
            unsupported("Cannot restore a camera target on an instanced producer.", producer.GetPath(),
                        camera=str(camera))
        repairs[str(producer.GetPath())] = dict(producer=str(producer.GetPath()), camera=str(camera),
                                                product=str(runtime_product))
    if not any(p.HasAttribute("viewportHandle") for p in products):
        return [], list(repairs.values())
    for helper in prims:
        helper_type = helper.GetAttribute("node:type").Get()
        if helper_type not in HELPERS:
            continue
        lidar_helper = helper_type in LIDAR_HELPERS
        for source in helper.GetAttribute("inputs:renderProductPath").GetConnections():
            producer = stage.GetPrimAtPath(source.GetPrimPath())
            if (not producer or not producer.IsActive() or producer.IsAbstract()
                    or producer.GetAttribute("node:type").Get() != PRODUCER
                    or source.name != "outputs:renderProductPath"):
                continue
            rel = producer.GetRelationship("inputs:cameraPrim")
            cameras = rel.GetTargets() if rel else []
            if not cameras:
                cameras = [Sdf.Path(repairs[str(producer.GetPath())]["camera"])]
            width = producer.GetAttribute("inputs:width").Get()
            height = producer.GetAttribute("inputs:height").Get()
            if type(width) is not int or type(height) is not int:
                # A default graph leaves them unauthored (OGN default 1280x720). The node's real values are only a
                # candidate: the matches below still need a product with this camera and that authored resolution.
                width, height = runtime_node_resolution(stage, producer) or (None, None)
            if type(width) is not int or type(height) is not int:
                unsupported(f"Camera producer {producer.GetPath()} needs explicit integer width and height: "
                            "set its inputs:width and inputs:height.", producer.GetPath(),
                            camera=[str(c) for c in cameras])
            if len(cameras) != 1:
                unsupported(f"Camera producer {producer.GetPath()} needs exactly one camera target.",
                            producer.GetPath(), camera=[str(c) for c in cameras])
            # Version 2 (6.0.0.0) has no renderProductPrim input. Never require it.
            explicit = producer.GetRelationship("inputs:renderProductPrim").GetTargets()
            # An inactive product is not reusable: NVIDIA's lookup only walks active prims.
            matches = [p for p in products
                       if p.IsActive() and p.GetRelationship("camera").GetTargets() == cameras
                       and tuple(p.GetAttribute("resolution").Get() or ()) == (width, height)]
            if explicit:
                if len(explicit) != 1:
                    unsupported("Camera producer targets multiple render products.", producer.GetPath())
                matches = [p for p in matches if p.GetPath() == explicit[0]]
                if not matches:
                    unsupported("Explicit camera product does not match camera/resolution.", producer.GetPath())
            # Our capture product is removed whole from the copy, so it cannot compete with the graph's own.
            matches = [p for p in matches if not OWNED_PRODUCT.fullmatch(p.GetName())] or matches
            if not any(p.HasAttribute("viewportHandle") for p in matches):
                continue
            if len(matches) != 1:
                unsupported("Multiple render products match this camera; export cannot guess which one to reset. If Play added one "
                            "to a stale file, open that file again and save before playing.",
                            producer.GetPath(), products=[str(p.GetPath()) for p in matches])
            product = matches[0]
            if any(p.IsInstance() or p.IsInstanceProxy() or p.IsInPrototype()
                   for p in (helper, producer, product)):
                unsupported("Instanced camera render-handle normalization is unsupported.", product.GetPath())
            attr = product.GetAttribute("viewportHandle")
            entry = dict(path=str(attr.GetPath()), product=str(product.GetPath()),
                         camera=str(cameras[0]), width=width, height=height,
                         handle=attr.Get(), producer=str(producer.GetPath()), lidar=lidar_helper,
                         owned=bool(OWNED_PRODUCT.fullmatch(product.GetName())))
            previous = selected.get(entry["path"])
            # Several producers can reuse one product (a LaserScan and a PointCloud on one LiDAR). Dropping that one
            # handle is safe unless they disagree about what it is.
            if previous and dict(previous, producer=None) != dict(entry, producer=None):
                unsupported("Multiple producers share the camera product ambiguously.", attr.GetPath())
            selected[entry["path"]] = entry
    if not selected:
        return [], list(repairs.values())
    # A pack-owned capture product is removed whole by strip_runtime_render_state, so it needs no
    # export-policy flag. It still passes every check below: ambiguous, authored and dependent refuse.
    native = sorted(path for path, entry in selected.items() if not entry["owned"])
    # A LiDAR handle already saved in a file is authored. Refusing would lose unsaved work, so without the flag it
    # stays in the copy and save_report warns. With it, the copy drops it: that is the rescue for a stale file.
    authored = []
    for path in [p for p in native if selected[p]["lidar"]
                 and not session_only(stage.GetAttributeAtPath(p), stage)]:
        authored.append(selected.pop(path))
        native.remove(path)
    authored = authored if reset else []
    if not selected and not authored:
        return [], list(repairs.values())
    if native and not reset:
        unsupported(
            "Camera and LiDAR render handles cannot survive restart. To reset only these runtime fields in the exported copy, "
            "retry with reset_camera_render_handles=true. This is an explicit export policy, not proof of authorship.",
            native[0], properties=native,
            retry_with={"reset_camera_render_handles": True})
    # A masked/unloaded composition cannot establish that all dependencies were inspected.
    from .compat import composition_errors

    blocking = blocking_composition_errors(composition_errors(stage))
    if (stage.GetPopulationMask().GetPaths() != [Sdf.Path.absoluteRootPath]
            or blocking
            or any(p.HasPayload() and not p.IsLoaded() for p in prims)):
        unsupported("Complete loaded composition is required to check render-handle dependencies.", "/",
                    composition_errors=[str(e)[:300] for e in blocking[:5]])
    # An authored LiDAR handle has no session spec or live binding to check; everything else still applies.
    for entry in authored:
        attr = stage.GetAttributeAtPath(entry["path"])
        if (attr.GetTypeName() != Sdf.ValueTypeNames.Int or type(entry["handle"]) is not int
                or attr.GetNumTimeSamples() or attr.GetConnections()):
            unsupported("Saved LiDAR handle is connected, animated, or has an unsupported type; cannot normalize it.", entry["path"])
    for entry in selected.values():
        attr = stage.GetAttributeAtPath(entry["path"])
        stack = attr.GetPropertyStack()
        if (attr.GetTypeName() != Sdf.ValueTypeNames.Int or type(entry["handle"]) is not int
                or not -1 <= entry["handle"] < 2**31 or attr.GetNumTimeSamples()
                or attr.GetConnections() or not stack
                or any(spec.layer != stage.GetSessionLayer() or str(spec.path) != entry["path"]
                       for spec in stack)):
            unsupported("Render handle is authored, cross-layer, connected, animated, or has an unsupported type; cannot normalize it.", entry["path"])
        if binding_check is None:
            unsupported("Native render-binding validation is unavailable; no guessed handle reset.", entry["path"])
    # Flatten omits authored descendants of inactive prims. Inspect original
    # used-layer specs as well, conservatively refusing any handle dependency
    # whose reference namespace cannot be resolved without activating content.
    scan_dependencies(stage.GetUsedLayers(), conservative=True)
    for entry in selected.values():
        binding_check(entry)
    return list(selected.values()) + authored, list(repairs.values())


def restore_export_camera_targets(layer, repairs):
    """Restore observed runtime targets in the private flattened copy only."""
    if not repairs:
        return []
    stage = Usd.Stage.Open(layer)
    for entry in repairs:
        producer = stage.GetPrimAtPath(entry["producer"])
        rel = producer.GetRelationship("inputs:cameraPrim") if producer else None
        if (not producer or (rel and rel.GetTargets())
                or (not rel and producer.GetAttribute("inputs:cameraPrim"))):
            unsupported("Camera target changed while exporting; inspect and retry.", entry["producer"],
                        camera=entry["camera"])
        if not producer.CreateRelationship("inputs:cameraPrim").SetTargets([Sdf.Path(entry["camera"])]):
            unsupported("Cannot restore the camera target in the exported copy.", entry["producer"],
                        camera=entry["camera"])
    return repairs


def scan_dependencies(layers, paths=(), conservative=False):
    count = 0

    def inspect(layer, path):
        nonlocal count
        count += 1
        if count > MAX_SPECS:
            unsupported("Render-handle dependency inspection exceeds 200000 specs.", path)
        spec = layer.GetObjectAtPath(path)
        targets = []
        if isinstance(spec, Sdf.AttributeSpec):
            targets = spec.connectionPathList.GetAppliedItems()
        elif isinstance(spec, Sdf.RelationshipSpec):
            targets = spec.targetPathList.GetAppliedItems()
        if any(target in paths or (conservative and target.IsPropertyPath()
                                  and target.name == "viewportHandle") for target in targets):
            unsupported("Authored content depends on a camera runtime handle; cannot remove its input.", path)
        if conservative and isinstance(spec, Sdf.PrimSpec):
            hidden = any(parent and parent.HasInfo("active") and parent.GetInfo("active") is False
                         for parent in (layer.GetPrimAtPath(prefix) for prefix in path.GetPrefixes()))
            if hidden and (spec.referenceList.GetAppliedItems() or spec.payloadList.GetAppliedItems()):
                unsupported("Inactive referenced/payload content prevents a complete handle-dependency check.", path)

    for layer in layers:
        layer.Traverse(Sdf.Path.absoluteRootPath, lambda path: inspect(layer, path))


def reset_export_handles(layer, entries):
    """Check the flattened spec namespaces too, then change only the private copy."""
    if not entries:
        return []
    paths = {Sdf.Path(entry["path"]) for entry in entries}
    scan_dependencies([layer], paths)
    for entry in entries:
        spec = layer.GetAttributeAtPath(entry["path"])
        if spec is None or spec.default != entry["handle"]:
            unsupported("Camera render handle changed during export; retry after inspecting the stage.", entry["path"])
    # Removing the property spec matters: Clear()/value blocking still makes
    # HasAttribute('viewportHandle') true in NVIDIA's reuse path.
    for entry in entries:
        spec = layer.GetAttributeAtPath(entry["path"])
        spec.owner.RemoveProperty(spec)
    return sorted(str(p) for p in paths)


def remaining_render_handles(layer):
    """Active non-viewport products still holding viewportHandle in the private copy (bounded)."""
    out = []
    stage = Usd.Stage.Open(layer)
    for prim in stage.Traverse():
        if (prim.GetTypeName() == "RenderProduct" and prim.HasAttribute("viewportHandle")
                and not prim.GetName().startswith(VIEWPORT_PRODUCT_PREFIX)):
            out.append(str(prim.GetPath()))
    return out


def graph_inventory(layer):
    """OmniGraph paths in the private copy after runtime pipelines are gone, so a checkpoint can be told apart."""
    stage = Usd.Stage.Open(layer)
    paths = [str(p.GetPath()) for p in stage.Traverse() if p.GetTypeName() == "OmniGraph"]
    return paths[:MAX_GRAPHS], len(paths)


def save_report(layer):
    """Fields that describe a saved copy: its graphs, and warnings for handles the export could not reset."""
    graphs, graph_count = graph_inventory(layer)
    report = dict(graphs=graphs, graph_count=graph_count, graphs_truncated=graph_count > len(graphs))
    remaining = remaining_render_handles(layer)
    if remaining:
        report["stale_render_handles"] = remaining[:MAX_REPORTED_HANDLES]
        report["warnings"] = [
            f"{len(remaining)} render product(s) keep a viewportHandle in this copy. If a graph reuses one after "
            "reopening (same camera and resolution), its sensor will not publish. " + STALE_HANDLE_FIX]
    return report


def retained_product_paths():
    """Product paths with a texture retained by Replicator now, or None when that cannot be read."""
    try:
        import omni.replicator.core as rep

        textures, _verify = retained_textures(rep.vp_manager.ViewportManager)
        return {str(texture.get_render_product_path()) for texture in textures}
    except Exception:  # noqa: BLE001 - private adapter is feature-detected; callers report "unknown"
        return None


def saved_runtime_pipeline(stage):
    """File-authored Replicator state, which strip_runtime_render_state omits from a normal save.

    A raw Stage.Export keeps it. OmniGraph loads it at open, so deactivating a product afterward leaves graphs behind
    that break every later writer ("Invalid object in Py_Graph"), and deactivating the pipeline itself stops Kit
    creating nodes at that path. Only a clean copy recovers such a file.
    """
    session = stage.GetSessionLayer()
    found = []
    for count, prim in enumerate(Usd.PrimRange.Stage(stage)):
        if count == MAX_PRIMS:
            break
        name = str(prim.GetPath())
        # /WriterOrchestrator is not listed: a clean save keeps it and reopens fine.
        native = name == "/Orchestrator" or (name.startswith("/Render/") and prim.GetName() == "SDGPipeline")
        if native and any(spec.layer != session for spec in prim.GetPrimStack()):
            found.append(name)
    return found


def repair_stale_lidar_products(stage, retained, allowed=True):
    """Deactivate saved LiDAR products in the session layer of a freshly opened stage, where that is known to work.

    Their file-authored viewportHandle stops the sensor. Deactivating is reversible, leaves the file and root layer
    untouched, and lets IsaacCreateRenderProduct make a new product at Play, but only in a file with no saved runtime
    pipeline, on a build where this was measured. Anything else is reported with the rescue, not changed.
    Cameras keep their documented contract; anything a graph pins or connects to is reported, not changed.
    """
    candidates = []
    pinned = set()
    for count, prim in enumerate(Usd.PrimRange.Stage(stage)):
        if count == MAX_PRIMS:
            return dict(deactivated=[], skipped=[dict(path="/", reason="stage exceeds 10000 prims")])
        pin = prim.GetRelationship("inputs:renderProductPrim")
        if pin:
            pinned.update(pin.GetTargets())
        if prim.GetTypeName() != "RenderProduct" or not prim.HasAttribute("viewportHandle"):
            continue
        targets = prim.GetRelationship("camera").GetTargets()
        camera = stage.GetPrimAtPath(targets[0]) if len(targets) == 1 else None
        if camera and camera.GetTypeName() == "OmniLidar" and not session_only(prim.GetAttribute("viewportHandle"), stage):
            candidates.append(prim)
    pipeline = saved_runtime_pipeline(stage)
    if not candidates and not pipeline:
        return dict(deactivated=[], skipped=[])
    if not candidates:
        return dict(deactivated=[], skipped=[], saved_runtime_pipeline=pipeline[:MAX_REPORTED_HANDLES], rescue=STALE_RESCUE)
    if pipeline or not allowed:
        reason = (f"the file also holds a saved runtime pipeline ({pipeline[0]})" if pipeline
                  else "this repair is not proven on this build")
        return dict(deactivated=[], skipped=[dict(path=str(p.GetPath()), reason=reason) for p in candidates],
                    rescue=STALE_RESCUE)
    if retained is None:
        return dict(deactivated=[], skipped=[dict(path=str(p.GetPath()), reason="texture state unreadable")
                                             for p in candidates])
    try:
        scan_dependencies(stage.GetUsedLayers(), conservative=False,
                          paths={p.GetPath().AppendProperty("viewportHandle") for p in candidates})
    except Exception as error:  # noqa: BLE001 - a dependency or size limit: leave the content alone
        return dict(deactivated=[], skipped=[dict(path=str(p.GetPath()), reason=str(error)[:200])
                                             for p in candidates])
    deactivated, skipped = [], []
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for prim in candidates:
            name = str(prim.GetPath())
            if name in retained:
                skipped.append(dict(path=name, reason="has a live texture"))
            elif prim.GetPath() in pinned:
                skipped.append(dict(path=name, reason="a graph pins it with inputs:renderProductPrim"))
            elif prim.SetActive(False):
                deactivated.append(name)
            else:
                skipped.append(dict(path=name, reason="could not be deactivated"))
    return dict(deactivated=deactivated, skipped=skipped)


def classify_helper_product(stage, product, retained, playing):
    """What a ROS helper's product will do, from the same fields Replicator's reuse path reads."""
    if product is None:
        if playing:
            return "no_product", "No render product exists while playing; inspect the producer's compute errors."
        return "no_product_yet", None
    name = str(product.GetPath())
    if retained is not None and name in retained:
        return "texture_bound", None
    attr = product.GetAttribute("viewportHandle")
    if product.HasAttribute("viewportHandle") and not session_only(attr, stage):
        return "stale_handle", f"{name} has a saved viewportHandle and no texture. " + STALE_HANDLE_FIX
    if retained is None:
        return "unknown", None
    if product.HasAttribute("viewportHandle"):
        return "viewport_bound", None
    if playing:
        return "no_texture", f"{name} has no render texture while playing, so its sensor is not rendering."
    return "texture_on_play", None


def helper_render_status(stage, helper, runtime_product, retained, playing, resolution_reader=runtime_node_resolution):
    """One helper's render status. *runtime_product* is its live input, usually empty until the first Play."""
    product = None
    if runtime_product:
        prim = stage.GetPrimAtPath(str(runtime_product))
        product = prim if prim and prim.IsActive() else None
    else:
        for source in helper.GetAttribute("inputs:renderProductPath").GetConnections():
            producer = stage.GetPrimAtPath(source.GetPrimPath())
            if not producer or producer.GetAttribute("node:type").Get() != PRODUCER:
                continue
            rel = producer.GetRelationship("inputs:cameraPrim")
            cameras = rel.GetTargets() if rel else []
            width = producer.GetAttribute("inputs:width").Get()
            height = producer.GetAttribute("inputs:height").Get()
            if type(width) is not int or type(height) is not int:
                width, height = resolution_reader(stage, producer) or (None, None)
            matches = [p for p in stage.Traverse() if p.GetTypeName() == "RenderProduct"
                       and p.GetRelationship("camera").GetTargets() == cameras
                       and tuple(p.GetAttribute("resolution").Get() or ()) == (width, height)]
            if cameras and matches:
                product = matches[0]
                break
    status, problem = classify_helper_product(stage, product, retained, playing)
    entry = dict(node=str(helper.GetPath()), render_product=str(product.GetPath()) if product else None, status=status)
    if problem:
        entry["problem"] = problem
    return entry


def writer_problem(entry, playing):
    """A problem for a helper whose product has a texture yet whose writer has never run while playing."""
    if not playing or entry.get("status") != "texture_bound" or entry.get("writer_computes") != 0:
        return None
    return (f"{entry['render_product']} has a render texture but its writer has not run, so the topic is not "
            "advertised. Right after Play this can be temporary; if writer_computes stays 0, look for the writer's "
            "attach error with sim__logs__tail.")


def retained_textures(manager_type):
    """Read the inspected 6.x Replicator cache layout without pruning or allocating.

    This is a private, feature-detected adapter. Do not use MultiTexture.get or
    its _hydra_textures property: both prune the cache. Keep strong references
    only for this synchronous validation, with no app update or await.
    """
    manager = vars(manager_type).get("_hydra_textures")
    cache = vars(manager).get("_MultiTexture__hydra_textures")
    if type(cache) is not dict or len(cache) > 64:
        raise ValueError("unsupported Replicator texture-cache layout or context bound")
    textures = []
    snapshots = []
    count = 0
    for key, wrappers in tuple(cache.items()):
        if type(wrappers) is not list:
            raise ValueError("unsupported Replicator texture-cache entries")
        count += len(wrappers)
        if count > 256:
            raise ValueError("Replicator texture inspection exceeds 256 entries")
        members = tuple(wrappers)
        bindings = []
        for wrapper in members:
            fields = vars(wrapper)
            if "hydra_texture" not in fields:
                raise ValueError("unsupported Replicator texture wrapper")
            texture = fields["hydra_texture"]
            bindings.append(texture)
            if texture is not None:
                textures.append(texture)
        snapshots.append((key, wrappers, members, tuple(bindings)))

    def verify():
        if (vars(manager_type).get("_hydra_textures") is not manager
                or vars(manager).get("_MultiTexture__hydra_textures") is not cache
                or len(cache) != len(snapshots)):
            raise ValueError("Replicator texture cache changed during inspection")
        for key, wrappers, members, bindings in snapshots:
            if (cache.get(key) is not wrappers or len(wrappers) != len(members)
                    or any(current is not previous for current, previous in zip(wrappers, members))
                    or any(vars(wrapper).get("hydra_texture") is not texture
                           for wrapper, texture in zip(members, bindings))):
                raise ValueError("Replicator texture binding changed during inspection")

    return textures, verify


def validate_native_binding(entry):
    """Check an already-retained texture; never turn a USD integer into a pointer.

    get_hydra_texture_from_handle(viewportHandle) crashed in the installed 6.2
    native binding. Its integer contract is not established by the API stub.
    The caller's explicit export policy authorizes omission, not this adapter.
    """
    try:
        import omni.replicator.core as rep
        import omni.usd

        usd_context = omni.usd.get_context()
        stage, stage_id = usd_context.get_stage(), usd_context.get_stage_id()
        if stage is None:
            raise ValueError("no current stage")
        context = usd_context.get_name()
        textures, verify = retained_textures(rep.vp_manager.ViewportManager)
        matches = [texture for texture in textures
                   if texture.get_usd_context_name() == context
                   and str(texture.get_render_product_path()) == entry["product"]]
        if len(matches) != 1:
            raise ValueError("expected exactly one retained texture for this context/product")
        texture = matches[0]
        actual = (texture.get_usd_context_name(), str(texture.get_render_product_path()),
                  str(texture.get_camera_path()), texture.get_width(), texture.get_height())
        expected = (context, entry["product"],
                    entry["camera"], entry["width"], entry["height"])
        if actual != expected:
            raise ValueError(f"native binding differs: {actual!r}")
        verify()
        if (usd_context.get_stage() != stage or usd_context.get_stage_id() != stage_id
                or usd_context.get_name() != context):
            raise ValueError("USD stage/context changed during inspection")
    except Exception as error:
        unsupported("Cannot verify current native camera binding: " + str(error)[:600], entry["path"])

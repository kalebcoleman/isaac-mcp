"""Explicit export normalization of camera render handles, never the live stage.

An active native binding is not proof of USD authorship. The caller's explicit
export policy authorizes the omission; the checks only constrain its scope.
"""

from pxr import Sdf, Usd
from isaac_mcp.project_assets import refuse

PRODUCER = "isaacsim.core.nodes.IsaacCreateRenderProduct"
HELPERS = {
    namespace + name
    for namespace in ("isaacsim.ros2.bridge.", "isaacsim.ros2.nodes.")
    for name in ("ROS2CameraHelper", "ROS2CameraInfoHelper")
}
MAX_PRIMS = 10000
MAX_SPECS = 200000


def unsupported(message, path, **details):
    refuse("SAVE_UNSUPPORTED", message + " Live stage and source files preserved.",
           path=str(path), **details)


def camera_handles(stage, reset, binding_check):
    """Select bounded, unambiguous helper products before provenance is flattened."""
    if type(reset) is not bool:
        refuse("INVALID_ARGUMENT", "reset_camera_render_handles must be boolean.")
    prims = []
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)):
        if len(prims) == MAX_PRIMS:
            unsupported("Render-handle inspection exceeds 10000 prims.", "/")
        prims.append(prim)
    products = [p for p in prims if p.GetTypeName() == "RenderProduct"]
    if not any(p.HasAttribute("viewportHandle") for p in products):
        return []
    selected = {}
    for helper in prims:
        if helper.GetAttribute("node:type").Get() not in HELPERS:
            continue
        for source in helper.GetAttribute("inputs:renderProductPath").GetConnections():
            producer = stage.GetPrimAtPath(source.GetPrimPath())
            if (not producer or producer.GetAttribute("node:type").Get() != PRODUCER
                    or source.name != "outputs:renderProductPath"):
                continue
            cameras = producer.GetRelationship("inputs:cameraPrim").GetTargets()
            width = producer.GetAttribute("inputs:width").Get()
            height = producer.GetAttribute("inputs:height").Get()
            if len(cameras) != 1 or type(width) is not int or type(height) is not int:
                unsupported("Camera producer needs one camera and explicit integer resolution.", producer.GetPath())
            # Version 2 (6.0.0.0) has no renderProductPrim input. Never require it.
            explicit = producer.GetRelationship("inputs:renderProductPrim").GetTargets()
            matches = [p for p in products
                       if p.GetRelationship("camera").GetTargets() == cameras
                       and tuple(p.GetAttribute("resolution").Get() or ()) == (width, height)]
            if explicit:
                if len(explicit) != 1:
                    unsupported("Camera producer targets multiple render products.", producer.GetPath())
                matches = [p for p in matches if p.GetPath() == explicit[0]]
                if not matches:
                    unsupported("Explicit camera product does not match camera/resolution.", producer.GetPath())
            if not any(p.HasAttribute("viewportHandle") for p in matches):
                continue
            if len(matches) != 1:
                unsupported("Multiple render products match this camera; export cannot guess which one to reset.",
                            producer.GetPath(), products=[str(p.GetPath()) for p in matches])
            product = matches[0]
            if any(p.IsInstance() or p.IsInstanceProxy() or p.IsInPrototype()
                   for p in (helper, producer, product)):
                unsupported("Instanced camera render-handle normalization is unsupported.", product.GetPath())
            attr = product.GetAttribute("viewportHandle")
            entry = dict(path=str(attr.GetPath()), product=str(product.GetPath()),
                         camera=str(cameras[0]), width=width, height=height,
                         handle=attr.Get(), producer=str(producer.GetPath()))
            previous = selected.get(entry["path"])
            if previous and previous != entry:
                unsupported("Multiple producers share the camera product ambiguously.", attr.GetPath())
            selected[entry["path"]] = entry
    if not selected:
        return []
    if not reset:
        unsupported(
            "Camera render handles cannot survive restart. To reset only these runtime fields in the exported copy, "
            "retry with reset_camera_render_handles=true. This is an explicit export policy, not proof of authorship.",
            next(iter(selected)), properties=sorted(selected),
            retry_with={"reset_camera_render_handles": True})
    # A masked/unloaded composition cannot establish that all dependencies were inspected.
    if (stage.GetPopulationMask().GetPaths() != [Sdf.Path.absoluteRootPath]
            or stage.GetCompositionErrors()
            or any(p.HasPayload() and not p.IsLoaded() for p in prims)):
        unsupported("Complete loaded composition is required to check render-handle dependencies.", "/")
    for entry in selected.values():
        attr = stage.GetAttributeAtPath(entry["path"])
        stack = attr.GetPropertyStack()
        if (attr.GetTypeName() != Sdf.ValueTypeNames.Int or type(entry["handle"]) is not int
                or not 0 <= entry["handle"] < 2**31 or attr.GetNumTimeSamples()
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
    return list(selected.values())


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

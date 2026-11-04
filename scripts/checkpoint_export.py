"""Root/reference checkpoints plus one composed local PhysicsScene.

This is not a full session snapshot. Keep asset arcs; omit other session content,
including runtime rendering. See sim__stage__save for composed-scene snapshots.
"""
import os
from pathlib import Path
import tempfile
from urllib.parse import unquote, urlparse

from pxr import Sdf, Usd, UsdPhysics, UsdUtils


def _composition_errors(stage):
    """USD 24.05 (Isaac Sim 5.1) has no Stage.GetCompositionErrors."""
    native = getattr(stage, "GetCompositionErrors", None)
    if native is not None:
        return native()
    return [error for prim in stage.TraverseAll() for error in prim.GetPrimIndex().localErrors]


def scene_description(stage):
    scenes = []
    for index, prim in enumerate(stage.TraverseAll()):
        if index >= 10000:
            raise ValueError("Checkpoint inspection exceeds 10000 prims")
        if prim.IsA(UsdPhysics.Scene):
            scenes.append(prim)
    if len(scenes) != 1:
        raise ValueError("Checkpoint requires exactly one typed PhysicsScene")
    prim = scenes[0]
    return {
        "path": str(prim.GetPath()), "type": prim.GetTypeName(),
        "attributes": {attr.GetName(): str(attr.Get()) for attr in prim.GetAttributes()},
        "api_schemas": list(prim.GetAppliedSchemas()),
        "authored_api_schemas": list(prim.GetMetadata('apiSchemas').GetAppliedItems())
        if prim.GetMetadata('apiSchemas') else [],
    }


def export_checkpoint(stage, target):
    """Publish without overwrite, without writing live/source layers or normalizing handles."""
    target = Path(target)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Checkpoint already exists; choose a new destination: {target.name}")
    if _composition_errors(stage):
        raise ValueError("Checkpoint source has composition errors")
    description = scene_description(stage)
    path = Sdf.Path(description["path"])
    prim = stage.GetPrimAtPath(path)
    local_layers = set(stage.GetLayerStack())
    if (prim.IsInstance() or prim.IsInstanceProxy() or prim.IsInPrototype()
            or any(spec.layer not in local_layers for spec in prim.GetPrimStack())):
        raise ValueError("Checkpoint PhysicsScene must be defined entirely in local layers")
    for prefix in path.GetPrefixes():
        ancestor = stage.GetPrimAtPath(prefix)
        if ancestor and (ancestor.HasAuthoredReferences() or ancestor.HasAuthoredPayloads()
                         or ancestor.HasAuthoredInherits() or ancestor.HasAuthoredSpecializes()
                         or ancestor.GetVariantSets().GetNames()):
            raise ValueError("Checkpoint PhysicsScene cannot depend on composition arcs")
    if prim.GetAllChildren() or prim.GetRelationships():
        raise ValueError("Checkpoint PhysicsScene children/relationships are unsupported")
    allowed = set(UsdPhysics.Scene.GetSchemaAttributeNames())
    supported = {'PhysxSceneAPI', 'NewtonSceneAPI'}
    if set(description['authored_api_schemas']) - supported:
        raise ValueError("Unsupported PhysicsScene API schemas")
    for schema in supported:
        definition = Usd.SchemaRegistry().FindAppliedAPIPrimDefinition(schema)
        if definition:
            allowed.update(definition.GetPropertyNames())
    # Standalone OpenUSD tests lack NVIDIA's schema plugins. These are known
    # settings, not a prefix wildcard. Live Kit supplies full schema definitions.
    allowed.update({'physxScene:timeStepsPerSecond', 'physxScene:enableGPUDynamics',
                    'physxScene:broadphaseType', 'physxScene:solverType',
                    'physxScene:enableCCD', 'newton:gravityEnabled',
                    'newton:timeStepsPerSecond', 'newton:maxSolverIterations'})
    for attr in prim.GetAttributes():
        if (attr.GetName() not in allowed
                or attr.GetTypeName() in (Sdf.ValueTypeNames.Asset, Sdf.ValueTypeNames.AssetArray)
                or attr.GetConnections() or attr.GetNumTimeSamples()):
            raise ValueError(f"Unsupported PhysicsScene property: {attr.GetPath()}")
    source = stage.GetRootLayer()
    output = Sdf.Layer.CreateAnonymous('.usda')
    output.TransferContent(source)
    # Compose local/session opinions in strength order. Unlike Stage.Flatten,
    # this preserves asset arcs; only the validated scene spec is copied out.
    local = UsdUtils.FlattenLayerStack(stage)
    spec = local.GetPrimAtPath(path) if local else None
    if (not spec or spec.typeName != 'PhysicsScene' or spec.nameChildren or spec.variantSets
            or any(spec.HasInfo(field) for field in ('references', 'payload', 'inheritPaths', 'specializes'))):
        raise ValueError("Unsupported local PhysicsScene contribution")
    if not local or not Sdf.CopySpec(local, path, output, path):
        raise ValueError("Could not compose the local PhysicsScene definition")
    relative_refs, absolute_refs = [], []

    def make_relative(value):
        if not value:
            return value
        parsed = urlparse(value)
        if parsed.scheme == 'file' and parsed.netloc in ('', 'localhost'):
            value = unquote(parsed.path)
        elif parsed.scheme == 'anon':
            raise ValueError("Anonymous asset/sublayer cannot survive checkpoint restart")
        elif parsed.scheme:
            return value  # Retain resolver/network identifiers; do not fetch them.
        # Only root content retains asset paths. The copied physics contribution
        # disallows them, so anchor these paths to their original root layer.
        anchored = Sdf.ComputeAssetPathRelativeToLayer(source, value)
        if os.path.isabs(anchored):
            value = os.path.relpath(anchored, target.parent)
        if os.path.isabs(value):
            absolute_refs.append(value)
        elif value:
            relative_refs.append(value)
        return value

    UsdUtils.ModifyAssetPaths(output, make_relative)

    spec_count = 0

    def inspect_spec(layer, spec_path):
        nonlocal spec_count
        spec_count += 1
        if spec_count > 200000:
            raise ValueError("Checkpoint runtime-field inspection exceeds 200000 specs")
        spec = layer.GetObjectAtPath(spec_path)
        if isinstance(spec, Sdf.AttributeSpec) and spec.name == 'viewportHandle':
            raise ValueError("Root contains a process-bound viewportHandle; use composed save")
        if isinstance(spec, Sdf.PrimSpec) and (
                str(spec.path) == '/Orchestrator'
                or (str(spec.path).startswith('/Render/') and spec.name == 'SDGPipeline')):
            raise ValueError("Root contains runtime render state; use composed save")

    output.Traverse(Sdf.Path.absoluteRootPath, lambda p: inspect_spec(output, p))
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.checkpoint-', suffix='.usda', dir=target.parent)
        os.close(fd)
        if not output.Export(temporary):
            raise RuntimeError("Checkpoint export failed")
        reopened = Usd.Stage.Open(temporary)
        if not reopened or _composition_errors(reopened) or scene_description(reopened) != description:
            raise ValueError("Checkpoint does not preserve the single typed PhysicsScene")
        # Sdf traversal also sees inactive descendants and variant opinions;
        # composed traversal alone would miss retained reference contributions.
        for layer in reopened.GetUsedLayers():
            layer.Traverse(Sdf.Path.absoluteRootPath, lambda p: inspect_spec(layer, p))
        with open(temporary, 'rb') as handle:
            os.fsync(handle.fileno())
        # Atomic no-clobber even if a file/symlink arrives after the first check.
        os.link(temporary, target)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return {
        'written': target.name, 'relative_asset_refs': len(relative_refs),
        'absolute_refs_left': len(absolute_refs), 'physics_scene': description,
        'scope': 'root-layer copy plus composed local PhysicsScene; other session content excluded; '
                 'references remain relative to configured cache/source; not a self-contained asset bundle',
    }

"""Read mesh prims from a USD file and write mesh-preparation outputs: decimated, collision and split layers.

Needs ``pxr`` (usd-core, in the ``mesh`` extra) and runs in ``mesh_worker``, never in the server.
The source file is only read. Every output is a new file in ``out_dir``. Asset paths are written
relative and through the Sdf API, so folder names containing ``@`` (some login names) stay valid.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from . import mesh_ops

STAGE_METADATA = ("metersPerUnit", "upAxis", "kilogramsPerUnit")


def open_stage(path):
    from pxr import Usd

    stage = Usd.Stage.Open(str(path), Usd.Stage.LoadAll)
    if stage is None:
        raise ValueError(f"USD could not open {path}")
    return stage


def triangulate(counts, indices) -> np.ndarray:
    """Fan-triangulate polygons; faces with fewer than three corners are dropped."""
    tris, start = [], 0
    indices = np.asarray(indices, dtype=np.int64)
    for n in counts:
        n = int(n)
        if n >= 3:
            first = indices[start]
            tris.extend((first, indices[start + k], indices[start + k + 1]) for k in range(1, n - 1))
        start += n
    return np.asarray(tris, dtype=np.int64).reshape(-1, 3)


def collision_schemas(prim) -> list[str]:
    """Applied collision schemas, UsdPhysics and PhysX alike."""
    return [name for name in prim.GetAppliedSchemas() if "CollisionAPI" in name]


def approximation(prim) -> str | None:
    if not collision_schemas(prim):
        return None
    attr = prim.GetAttribute("physics:approximation")
    value = attr.Get() if attr and attr.HasAuthoredValue() else None
    return str(value) if value else "none"


def meshes(stage, prims=None):
    """Every mesh prim (optionally only those at or under ``prims``) with its triangles in its own frame."""
    from pxr import Usd, UsdGeom

    wanted = [p.rstrip("/") for p in (prims or [])]
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        path = str(prim.GetPath())
        if wanted and not any(path == w or path.startswith(w + "/") for w in wanted):
            continue
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get()
        faces = triangulate(mesh.GetFaceVertexCountsAttr().Get() or [], mesh.GetFaceVertexIndicesAttr().Get() or [])
        yield prim, np.asarray(points if points is not None else [], dtype=np.float64).reshape(-1, 3), faces


def targets(stage, prims=None):
    """The meshes that get collision shapes: those at or under ``prims``; else the file's mesh colliders;
    else, when no mesh is a collider (the shapes are added at scene build, as for the bench), every mesh."""
    rows = list(meshes(stage, prims))
    if prims:
        return rows, "prims"
    colliders = [row for row in rows if collision_schemas(row[0])]
    return (colliders, "mesh colliders") if colliders else (rows, "every mesh (none is a collider)")


def inspect(path, prims=None) -> dict:
    from pxr import UsdGeom

    stage = open_stage(path)
    rows, totals = [], dict(meshes=0, triangles=0, colliders=0, collider_triangles=0, triangle_mesh_collider_triangles=0)
    for prim, points, faces in meshes(stage, prims):
        info = mesh_ops.inspect(points, faces)
        approx = approximation(prim)
        rows.append(dict(prim=str(prim.GetPath()), triangles=info["triangles"], bounds=info["bounds"],
                         watertight=info["watertight"], collider=approx is not None, approximation=approx,
                         purpose=str(UsdGeom.Imageable(prim).ComputePurpose()), instance_proxy=prim.IsInstanceProxy()))
        totals["meshes"] += 1
        totals["triangles"] += info["triangles"]
        if approx is not None:
            totals["colliders"] += 1
            totals["collider_triangles"] += info["triangles"]
            if approx == "none":
                totals["triangle_mesh_collider_triangles"] += info["triangles"]
    return dict(file=str(path), meters_per_unit=UsdGeom.GetStageMetersPerUnit(stage),
                up_axis=str(UsdGeom.GetStageUpAxis(stage)), totals=totals, meshes=rows)


def _reanchor(layer, src_dir: Path, out_dir: Path) -> None:
    """Keep the copied layer's relative asset paths pointing at the files they named before the copy."""
    from pxr import UsdUtils

    def fix(asset):
        if not asset.startswith(("./", "../")):
            return asset                                   # absolute, a search path or a URL: left as written
        target = os.path.normpath(os.path.join(src_dir, asset))
        rel = os.path.relpath(target, out_dir)
        return rel if rel.startswith("..") else "./" + rel

    UsdUtils.ModifyAssetPaths(layer, fix)


def copy_root_layer(src: Path, dest: Path):
    """Copy the source's root layer to ``dest`` and open it as a stage that edits only that copy."""
    from pxr import Sdf

    if dest.exists():
        raise FileExistsError(f"{dest} exists; outputs are never overwritten")
    Sdf.Layer.FindOrOpen(str(src)).Export(str(dest))
    layer = Sdf.Layer.FindOrOpen(str(dest))
    _reanchor(layer, src.parent, dest.parent)
    layer.Save()
    return open_stage(dest)


def decimate(src: Path, out_dir: Path, *, target_tris=None, ratio=None, prims=None) -> dict:
    """A copy of the source in which the chosen render meshes are decimated. Normals and texture
    coordinates of a decimated mesh no longer match its faces, so they are blocked."""
    from pxr import UsdGeom, Vt

    source = open_stage(src)
    rows = list(meshes(source, prims))
    total = sum(len(f) for _, _, f in rows) or 1
    dest = out_dir / f"{src.stem}_decimated{src.suffix if src.suffix != '.usda' else '.usdc'}"
    stage = copy_root_layer(src, dest)
    before = after = 0
    for prim, points, faces in rows:
        share = None if target_tris is None else max(4, round(int(target_tris) * len(faces) / total))
        v, f = mesh_ops.decimate(points, faces, target_tris=share, ratio=ratio)
        before, after = before + len(faces), after + len(f)
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath(prim.GetPath()))
        mesh.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(v.astype(np.float32)))
        mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray([3] * len(f)))
        mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray.FromNumpy(f.reshape(-1).astype(np.int32)))
        mesh.GetNormalsAttr().Block()
        for primvar in UsdGeom.PrimvarsAPI(mesh).GetPrimvars():
            if primvar.GetInterpolation() in ("vertex", "varying", "faceVarying", "uniform"):
                primvar.GetAttr().Block()
                if primvar.GetIndicesAttr():
                    primvar.GetIndicesAttr().Block()
    stage.GetRootLayer().Save()
    return dict(output=str(dest), meshes=len(rows), triangles_before=before, triangles_after=after)


def _parent_space(prim, points):
    """Points in the frame of the prim the collision shapes are added under, and that prim."""
    from pxr import UsdGeom, UsdPhysics

    if prim.HasAPI(UsdPhysics.RigidBodyAPI):          # the mesh is the body: stay under it
        return prim, points
    m = np.asarray(UsdGeom.Xformable(prim).GetLocalTransformation(), dtype=np.float64)
    return prim.GetParent(), points @ m[:3, :3] + m[3, :3]


def author_collision_layer(source_stage, dest: Path, *, method="box", max_hulls=16, threshold=0.05, prims=None) -> dict:
    """A layer holding only collision shapes: for each mesh, ``<parent>/<mesh>_collision`` (purpose guide)
    with a ``box`` cube or ``hull_<i>`` convex meshes. Bodies, joints and the meshes are not touched."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt

    if dest.exists():
        raise FileExistsError(f"{dest} exists; outputs are never overwritten")
    stage = Usd.Stage.CreateNew(str(dest))
    counts, skipped, replaced = dict(boxes=0, hulls=0, meshes=0), [], []
    rows, selected = targets(source_stage, prims)
    for prim, points, faces in rows:
        if prim.IsInstanceProxy() or not len(faces):
            skipped.append(dict(prim=str(prim.GetPath()), reason="instance proxy" if len(faces) else "no faces"))
            continue
        parent, local = _parent_space(prim, points)
        scope_path = parent.GetPath().AppendChild(f"{prim.GetName()}_collision")
        for ancestor in reversed(scope_path.GetParentPath().GetPrefixes()):
            Sdf.CreatePrimInLayer(stage.GetRootLayer(), ancestor).specifier = Sdf.SpecifierOver
        scope = UsdGeom.Xform.Define(stage, scope_path)
        scope.CreatePurposeAttr(UsdGeom.Tokens.guide)
        for i, shape in enumerate(mesh_ops.collision_shapes(local, faces, method, max_hulls=max_hulls, threshold=threshold)):
            if shape["kind"] == "box":
                cube = UsdGeom.Cube.Define(stage, scope_path.AppendChild("box"))
                cube.CreateSizeAttr(2.0)
                cube.AddTranslateOp().Set(Gf.Vec3d(*shape["center"]))
                w, x, y, z = shape["orientation"]
                cube.AddOrientOp().Set(Gf.Quatf(w, x, y, z))
                cube.AddScaleOp().Set(Gf.Vec3f(*[max(h, 1e-9) for h in shape["half_extents"]]))
                gprim, counts["boxes"] = cube, counts["boxes"] + 1
            else:
                hull = UsdGeom.Mesh.Define(stage, scope_path.AppendChild(f"hull_{i}"))
                hull.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(shape["points"].astype(np.float32)))
                hull.CreateFaceVertexCountsAttr(Vt.IntArray([3] * len(shape["faces"])))
                hull.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(shape["faces"].reshape(-1).astype(np.int32)))
                UsdPhysics.MeshCollisionAPI.Apply(hull.GetPrim()).CreateApproximationAttr("convexHull")
                gprim, counts["hulls"] = hull, counts["hulls"] + 1
            gprim.CreatePurposeAttr(UsdGeom.Tokens.guide)
            UsdPhysics.CollisionAPI.Apply(gprim.GetPrim())
        counts["meshes"] += 1
        replaced.append(str(prim.GetPath()))
    stage.GetRootLayer().Save()
    return dict(output=str(dest), selected=selected, **counts, skipped=skipped, replaced=replaced)


def strip_collision(stage, paths) -> int:
    """Remove the collision schemas of the prims at ``paths``, authored in the stage's edit target.
    Other colliders (a link's cube, a hull someone authored) are kept."""
    removed = 0
    for path in paths:
        prim = stage.GetPrimAtPath(path)
        for name in collision_schemas(prim) if prim else ():
            prim.RemoveAppliedSchema(name)
            removed += 1
    return removed


def split(src: Path, out_dir: Path, *, collision: dict | None = None, prims=None) -> dict:
    """``<stem>.usda`` composing ``<stem>_render`` (the source, with the collision schemas of the meshes that
    got shapes removed) and ``<stem>_collision.usdc`` (the explicit shapes). See ``targets`` for which meshes."""
    from pxr import Sdf, UsdGeom

    source = open_stage(src)
    stem = src.stem
    render_path = out_dir / f"{stem}_render{src.suffix if src.suffix != '.usda' else '.usdc'}"
    collision_path = out_dir / f"{stem}_collision.usdc"
    top_path = out_dir / f"{stem}.usda"
    if top_path.exists():
        raise FileExistsError(f"{top_path} exists; outputs are never overwritten")
    shapes = author_collision_layer(source, collision_path, prims=prims, **(collision or {}))
    render = copy_root_layer(src, render_path)
    removed = strip_collision(render, shapes["replaced"])
    render.GetRootLayer().Save()
    top = Sdf.Layer.CreateNew(str(top_path))
    top.subLayerPaths.append("./" + collision_path.name)
    top.subLayerPaths.append("./" + render_path.name)
    root = source.GetRootLayer().pseudoRoot
    for key in STAGE_METADATA:
        if root.HasInfo(key):
            top.pseudoRoot.SetInfo(key, root.GetInfo(key))
    if source.GetDefaultPrim():
        top.defaultPrim = source.GetDefaultPrim().GetName()
    top.Save()
    composed = open_stage(top_path)
    return dict(output=str(top_path), render=str(render_path), collision=str(collision_path),
                collision_schemas_removed=removed, shapes=shapes,
                meters_per_unit=UsdGeom.GetStageMetersPerUnit(composed))

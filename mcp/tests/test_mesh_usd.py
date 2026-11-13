"""mesh_usd: inspect, decimate, the collision layer and split, on a generated asset (the source is never changed)."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

pytest.importorskip("pxr")
pytest.importorskip("trimesh")
pytest.importorskip("coacd")

import mesh_fixture  # noqa: E402

from twinsmith import mesh_usd  # noqa: E402


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def asset(tmp_path):
    path = tmp_path / "src" / "bench.usdc"
    path.parent.mkdir()
    info = mesh_fixture.build(path)
    return path, info


def test_inspect_reports_meshes_colliders_and_triangle_mesh_totals(asset):
    path, info = asset
    report = mesh_usd.inspect(path)
    row = next(r for r in report["meshes"] if r["prim"] == info["mesh"])
    assert row["collider"] and row["approximation"] == "none" and row["watertight"]
    assert report["totals"]["triangle_mesh_collider_triangles"] == info["triangles"] == row["triangles"]
    assert report["meters_per_unit"] == 1.0 and report["up_axis"] == "Z"


def test_split_composes_render_and_collision_and_leaves_the_source(asset, tmp_path):
    from pxr import Usd, UsdGeom, UsdPhysics

    path, info = asset
    before = digest(path)
    out = tmp_path / "out"
    out.mkdir()
    result = mesh_usd.split(path, out, collision=dict(method="box"))
    assert digest(path) == before
    source, composed = Usd.Stage.Open(str(path)), Usd.Stage.Open(result["output"])
    bodies = lambda s: sorted(str(p.GetPath()) for p in s.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI) or p.IsA(UsdPhysics.Joint))  # noqa: E731
    assert bodies(composed) == bodies(source) and bodies(source)
    assert composed.GetDefaultPrim().GetName() == "World" and UsdGeom.GetStageUpAxis(composed) == "Z"
    render = Usd.Stage.Open(result["render"])
    assert not mesh_usd.collision_schemas(render.GetPrimAtPath(info["mesh"]))      # replaced by the box
    assert mesh_usd.collision_schemas(render.GetPrimAtPath("/World/Robot/Link0/shape"))  # not a mesh: kept
    assert result["shapes"]["selected"] == "mesh colliders" and result["shapes"]["replaced"] == [info["mesh"]]
    collision = Usd.Stage.Open(result["collision"])
    shapes = [p for p in collision.Traverse() if mesh_usd.collision_schemas(p)]
    assert shapes and all(UsdGeom.Imageable(p).ComputePurpose() == UsdGeom.Tokens.guide for p in shapes)
    # the box of the L sits where the L is: compare world bounds in the composed stage
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.guide])
    mesh_box = cache.ComputeWorldBound(composed.GetPrimAtPath(info["mesh"])).ComputeAlignedRange()
    shape_box = cache.ComputeWorldBound(composed.GetPrimAtPath(info["mesh"] + "_collision")).ComputeAlignedRange()
    assert np.allclose(mesh_box.GetMin(), shape_box.GetMin(), atol=1e-3) and np.allclose(mesh_box.GetMax(), shape_box.GetMax(), atol=1e-3)
    # the composed asset's colliders are the explicit shapes only
    colliders = [str(p.GetPath()) for p in composed.Traverse() if mesh_usd.collision_schemas(p)]
    assert info["mesh"] not in colliders and info["mesh"] + "_collision/box" in colliders
    with pytest.raises(FileExistsError):
        mesh_usd.split(path, out)


def test_hulls_with_coacd_writes_convex_pieces(asset, tmp_path):
    from pxr import Usd, UsdGeom

    path, info = asset
    dest = tmp_path / "bench_collision.usdc"
    result = mesh_usd.author_collision_layer(mesh_usd.open_stage(path), dest, method="coacd", max_hulls=4,
                                             prims=[info["mesh"]])
    assert result["meshes"] == 1 and 1 <= result["hulls"] <= 4 and result["boxes"] == 0
    layer = Usd.Stage.Open(str(dest))
    hulls = [p for p in layer.Traverse() if p.IsA(UsdGeom.Mesh)]
    assert len(hulls) == result["hulls"]
    assert all(p.GetAttribute("physics:approximation").Get() == "convexHull" for p in hulls)


def test_decimate_writes_a_lighter_copy(asset, tmp_path):
    path, info = asset
    out = tmp_path / "dec"
    out.mkdir()
    before = digest(path)
    result = mesh_usd.decimate(path, out, ratio=0.1, prims=[info["mesh"]])
    assert digest(path) == before
    assert result["triangles_after"] <= 0.11 * result["triangles_before"]
    report = mesh_usd.inspect(result["output"])
    assert next(r for r in report["meshes"] if r["prim"] == info["mesh"])["triangles"] == result["triangles_after"]


def test_triangulate_fans_polygons_and_drops_degenerate_faces():
    tris = mesh_usd.triangulate([4, 2, 3], [0, 1, 2, 3, 4, 5, 6, 7, 8])
    assert tris.tolist() == [[0, 1, 2], [0, 2, 3], [6, 7, 8]]

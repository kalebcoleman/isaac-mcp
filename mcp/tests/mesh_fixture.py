"""A generated USD asset for the mesh and rule tests: no private asset is used.

``/World/Bench/Frame/L`` is an L-shaped closed mesh of about 20,000 triangles with a triangle-mesh collider.
``/World/Robot`` is an articulation with two links and a revolute joint whose drive has maxForce 0, and
``/World/Robot/Link1/Gripper`` is a second articulation root nested inside it.
"""

from __future__ import annotations

import numpy as np


def l_mesh(n: int = 82):
    """A closed L-shaped prism (in the XY plane, extruded in Z) with a subdivided top and bottom."""
    xs = np.linspace(0, 2, n + 1)
    ys = np.linspace(0, 2, n + 1)
    inside = lambda x, y: x <= 1 + 1e-9 or y <= 1 + 1e-9          # noqa: E731 - the L: drop the [1,2]x[1,2] square
    verts, index = [], {}
    for z in (0.0, 0.2):
        for i, x in enumerate(xs):
            for j, y in enumerate(ys):
                if inside(x, y):
                    index[(i, j, z)] = len(verts)
                    verts.append((x, y, z))
    faces = []
    for z, flip in ((0.0, True), (0.2, False)):
        for i in range(n):
            for j in range(n):
                cell = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
                if all((a, b, z) in index for a, b in cell) and inside((xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2):
                    a, b, c, d = (index[(p, q, z)] for p, q in cell)
                    faces += [(a, c, b), (a, d, c)] if flip else [(a, b, c), (a, c, d)]
    # side walls along the outline of the L
    outline = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
    for (x0, y0), (x1, y1) in zip(outline, outline[1:] + outline[:1]):
        steps = int(round(max(abs(x1 - x0), abs(y1 - y0)) * n / 2))
        for k in range(steps):
            p0 = (x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps)
            p1 = (x0 + (x1 - x0) * (k + 1) / steps, y0 + (y1 - y0) * (k + 1) / steps)
            quad = [len(verts) + m for m in range(4)]
            verts += [(*p0, 0.0), (*p1, 0.0), (*p1, 0.2), (*p0, 0.2)]
            faces += [(quad[0], quad[1], quad[2]), (quad[0], quad[2], quad[3])]
    v, f = np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64)
    import trimesh

    merged = trimesh.Trimesh(v, f, process=True)   # weld the wall vertices onto the caps so the surface is closed
    return np.asarray(merged.vertices), np.asarray(merged.faces)


def build(path, *, faults: bool = True) -> dict:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt

    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    frame = UsdGeom.Xform.Define(stage, "/World/Bench/Frame")
    frame.AddTranslateOp().Set(Gf.Vec3d(0.5, 0, 0))
    v, f = l_mesh()
    mesh = UsdGeom.Mesh.Define(stage, "/World/Bench/Frame/L")
    mesh.AddTranslateOp().Set(Gf.Vec3d(0, 0, 0.1))
    mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(v.astype(np.float32)))
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray([3] * len(f)))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(f.reshape(-1).astype(np.int32)))
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr("none")

    robot = UsdGeom.Xform.Define(stage, "/World/Robot")
    UsdPhysics.ArticulationRootAPI.Apply(robot.GetPrim())
    links = []
    for name, z in (("Link0", 0.0), ("Link1", 0.5)):
        link = UsdGeom.Xform.Define(stage, f"/World/Robot/{name}")
        link.AddTranslateOp().Set(Gf.Vec3d(3, 0, z))
        UsdPhysics.RigidBodyAPI.Apply(link.GetPrim())
        box = UsdGeom.Cube.Define(stage, f"/World/Robot/{name}/shape")
        box.CreateSizeAttr(0.1)
        UsdPhysics.CollisionAPI.Apply(box.GetPrim())
        links.append(link)
    joint = UsdPhysics.RevoluteJoint.Define(stage, "/World/Robot/joint1")
    joint.CreateBody0Rel().SetTargets([links[0].GetPath()])
    joint.CreateBody1Rel().SetTargets([links[1].GetPath()])
    joint.CreateAxisAttr("Z")
    drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "angular")
    drive.CreateStiffnessAttr(10.0)
    drive.CreateMaxForceAttr(0.0 if faults else 100.0)
    if faults:
        gripper = UsdGeom.Xform.Define(stage, "/World/Robot/Link1/Gripper")
        UsdPhysics.ArticulationRootAPI.Apply(gripper.GetPrim())
    stage.GetRootLayer().Save()
    return dict(mesh="/World/Bench/Frame/L", triangles=len(f), points=v, faces=f)


def remove_faults(path) -> None:
    """Fix the two physics faults in place (in a copy the test owns)."""
    from pxr import Usd, UsdPhysics

    stage = Usd.Stage.Open(str(path))
    stage.GetPrimAtPath("/World/Robot/joint1").GetAttribute("drive:angular:physics:maxForce").Set(100.0)
    stage.GetPrimAtPath("/World/Robot/Link1/Gripper").RemoveAPI(UsdPhysics.ArticulationRootAPI)
    stage.GetRootLayer().Save()

"""Build and reset the workcell's externally sourced Jenga tower."""


def positions(config):
    settings = config["jenga"]
    length, width, height = settings["block_size_m"]
    center_x, center_y = settings["tower_xy_m"]
    return [((center_x, center_y + (slot - 1) * width, (layer + 0.5) * height), 0.0)
            if layer % 2 == 0 else
            ((center_x + (slot - 1) * width, center_y, (layer + 0.5) * height), 90.0)
            for layer in range(settings["tower_layers_built"]) for slot in range(3)]


def build(stage, asset, config):
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    UsdGeom.Xform.Define(stage, "/World/Jenga")
    parts = []
    for index, (position, angle) in enumerate(positions(config)):
        path = f"/World/Jenga/Block_{index + 1:02d}"
        root = UsdGeom.Xform.Define(stage, path)
        root.AddTranslateOp().Set(Gf.Vec3d(*position))
        root.AddRotateZOp().Set(angle)
        shape = UsdGeom.Xform.Define(stage, path + "/Asset")
        shape.GetPrim().GetReferences().AddReference(str(asset))
        bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]).ComputeLocalBound(shape.GetPrim()).ComputeAlignedRange()
        if bounds.IsEmpty():
            raise ValueError(f"Jenga asset has no visual bounds: {asset}")
        actual = bounds.GetSize()
        expected = config["jenga"]["block_size_m"]
        if any(abs(actual[axis] - expected[axis]) > 0.001 for axis in range(3)):
            raise ValueError(f"Jenga asset bounds {tuple(actual)} do not match {expected} metres")
        center = (bounds.GetMin() + bounds.GetMax()) * 0.5
        shape.AddTranslateOp().Set(-center)
        meshes = [prim for prim in Usd.PrimRange(shape.GetPrim()) if prim.IsA(UsdGeom.Mesh)]
        if not meshes:
            raise ValueError("Jenga asset must contain mesh geometry for collision")
        for mesh in meshes:
            UsdPhysics.CollisionAPI.Apply(mesh)
            UsdPhysics.MeshCollisionAPI.Apply(mesh).CreateApproximationAttr().Set("convexHull")
        UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
        UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr().Set(config["jenga"]["block_mass_kg"])
        parts.append({"prim_path": path, "asset_id": config["assets"]["jenga_block"], "bom_id": "jenga_block"})
    return {"parts": parts, "layers": config["jenga"]["tower_layers_built"],
            "block_count": len(parts), "block_size_m": list(config["jenga"]["block_size_m"]),
            "block_mass_kg": config["jenga"]["block_mass_kg"],
            "limitations": ["Block dimensions, mass, collision approximation and placement require physical validation."]}


def reset(stage, config):
    """Reset authored block poses on a stopped timeline; caller controls the timeline."""
    from pxr import Gf, UsdGeom

    for index, (position, angle) in enumerate(positions(config)):
        prim = stage.GetPrimAtPath(f"/World/Jenga/Block_{index + 1:02d}")
        if not prim or not prim.IsValid():
            raise ValueError("Jenga tower does not match the configuration")
        ops = UsdGeom.Xformable(prim).GetOrderedXformOps()
        if len(ops) != 2:
            raise ValueError("Unexpected Jenga block transform")
        ops[0].Set(Gf.Vec3d(*position))
        ops[1].Set(angle)

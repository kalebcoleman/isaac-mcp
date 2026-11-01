"""Original CC0 multi-file USDZ fixture; never overwrites an existing directory."""

import argparse
from pathlib import Path
from pxr import Sdf, Usd, UsdGeom, UsdUtils


def create(folder):
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    child = Usd.Stage.CreateNew(str(folder / "geometry.usda"))
    UsdGeom.SetStageMetersPerUnit(child, 1)
    UsdGeom.SetStageUpAxis(child, UsdGeom.Tokens.z)
    cube = UsdGeom.Cube.Define(child, "/PackageCube")
    cube.CreateSizeAttr(0.4)
    child.SetDefaultPrim(cube.GetPrim())
    child.GetRootLayer().Save()
    root = Usd.Stage.CreateNew(str(folder / "assembly.usda"))
    UsdGeom.SetStageMetersPerUnit(root, 1)
    UsdGeom.SetStageUpAxis(root, UsdGeom.Tokens.z)
    prim = UsdGeom.Xform.Define(root, "/Package")
    root.SetDefaultPrim(prim.GetPrim())
    geom = root.DefinePrim("/Package/Geometry")
    geom.GetReferences().AddReference("geometry.usda")
    root.GetRootLayer().Save()
    assert UsdUtils.CreateNewUsdzPackage(
        Sdf.AssetPath(str(folder / "assembly.usda")), str(folder / "assembly.usdz")
    )
    (folder / "LICENSE.txt").write_text(
        "Original fixture authored for isaac-mcp. Dedicated to the public domain under CC0-1.0.\n"
        "https://creativecommons.org/publicdomain/zero/1.0/\n"
    )
    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder")
    print(create(parser.parse_args().folder))

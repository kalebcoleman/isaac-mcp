"""The quick-start warehouse: its layout is clear for a robot, and (with USD available) it builds the prims it says it does."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "exts" / "sim-tools"))


def _constants():
    import ast

    tree = ast.parse((REPO / "exts/sim-tools/sim_tools/warehouse.py").read_text())
    values = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = eval(compile(ast.Expression(node.value), "w", "eval"), {}, values)
        except Exception:  # noqa: BLE001 - only the plain constants are needed
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name):
            values[target.id] = value
        elif isinstance(target, ast.Tuple):
            values.update({t.id: v for t, v in zip(target.elts, value)})
    return values


def test_the_robot_starts_in_an_aisle_clear_of_every_rack_and_wall():
    c = _constants()
    x, y, _ = c["START"]
    assert y in c["AISLES_Y"] and abs(x) < c["LENGTH"] / 2 - 0.5
    for row in c["RACK_ROWS_Y"]:
        assert abs(y - row) > c["RACK_DEPTH"] / 2 + 1.0  # a metre of clearance each side of a robot's centre line
    for centre in c["RACK_SIDES_X"]:
        assert not (centre - c["RACK_LENGTH"] / 2 <= x <= centre + c["RACK_LENGTH"] / 2) or abs(y - c["AISLES_Y"][0]) > 1.0
    assert max(abs(s) + c["RACK_LENGTH"] / 2 for s in c["RACK_SIDES_X"]) < c["LENGTH"] / 2  # racks fit inside the room
    assert max(abs(r) for r in c["RACK_ROWS_Y"]) + c["RACK_DEPTH"] / 2 < c["WIDTH"] / 2


def test_it_builds_walls_racks_and_cargo_as_static_colliders():
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom, UsdPhysics

    from sim_tools import warehouse

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World")
    report = warehouse.build(stage)
    assert report["prim"] == "/World/Warehouse" and report["racks"] == 6
    colliders = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.CollisionAPI)]
    assert len(colliders) == report["solid_prims"] > 100
    assert not any(p.HasAPI(UsdPhysics.RigidBodyAPI) for p in stage.Traverse())  # all static
    assert stage.GetPrimAtPath("/World/Warehouse/Walls/North") and not stage.GetPrimAtPath("/World/Warehouse/Floor").HasAPI(UsdPhysics.CollisionAPI)

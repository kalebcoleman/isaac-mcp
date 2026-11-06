"""A warehouse for ``sim__quick_start environment="warehouse"``: walls, shelving racks with cargo, aisles and floor markings.

Everything is plain USD boxes with static colliders, authored in the session layer under ``/World/Warehouse``: nothing is
downloaded and nothing is read from a file, so it works offline and the same way every time. 24 m by 16 m, Z up, metres:
two long aisles (y = -2.5 and 2.5) and a cross aisle at x = 0, racks of three shelves with boxes on them. A robot starts at
the west end of the first aisle, facing east.
"""

from __future__ import annotations

from pxr import Gf, Usd, UsdGeom, UsdPhysics

ROOT = "/World/Warehouse"
LENGTH, WIDTH = 24.0, 16.0  # x and y extent of the room
WALL_HEIGHT, WALL_THICKNESS = 3.5, 0.3
RACK_LENGTH, RACK_DEPTH, RACK_HEIGHT = 8.0, 1.0, 2.4
SHELF_Z = (0.15, 1.0, 1.85)
RACK_ROWS_Y = (-5.0, 0.0, 5.0)
RACK_SIDES_X = (-5.5, 5.5)  # centres of the two rack halves, either side of the cross aisle
AISLES_Y = (-2.5, 2.5)
START = (-10.5, -2.5, 0.0)  # the robot's first position
CARGO_COLORS = ((0.72, 0.55, 0.34), (0.66, 0.5, 0.3), (0.78, 0.62, 0.4), (0.55, 0.62, 0.7))


def _box(stage, path, center, size, color, collide=True):
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    xform = UsdGeom.Xformable(cube)
    xform.AddTranslateOp().Set(Gf.Vec3d(*center))
    xform.AddScaleOp().Set(Gf.Vec3f(*size))
    cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    if collide:
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    return cube


def build(stage) -> dict:
    """Author the warehouse; returns its path, size and what it holds."""
    count = 0
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdGeom.Xform.Define(stage, ROOT)
        # Floor: the quick-start ground plane collides; this slab only gives the warehouse a floor to see.
        _box(stage, f"{ROOT}/Floor", (0, 0, -0.01), (LENGTH, WIDTH, 0.02), (0.55, 0.56, 0.58), collide=False)
        walls = (("North", (0, WIDTH / 2, WALL_HEIGHT / 2), (LENGTH + WALL_THICKNESS, WALL_THICKNESS, WALL_HEIGHT)),
                 ("South", (0, -WIDTH / 2, WALL_HEIGHT / 2), (LENGTH + WALL_THICKNESS, WALL_THICKNESS, WALL_HEIGHT)),
                 ("East", (LENGTH / 2, 0, WALL_HEIGHT / 2), (WALL_THICKNESS, WIDTH, WALL_HEIGHT)),
                 ("West", (-LENGTH / 2, 0, WALL_HEIGHT / 2), (WALL_THICKNESS, WIDTH, WALL_HEIGHT)))
        for name, center, size in walls:
            _box(stage, f"{ROOT}/Walls/{name}", center, size, (0.82, 0.82, 0.8))
            count += 1
        for i, y in enumerate(AISLES_Y):  # painted lane lines
            _box(stage, f"{ROOT}/Markings/AisleLine{i}", (0, y, 0.004), (LENGTH - 2, 0.12, 0.008), (0.95, 0.8, 0.1), collide=False)
        _box(stage, f"{ROOT}/Markings/CrossLine", (0, 0, 0.004), (0.12, WIDTH - 2, 0.008), (0.95, 0.8, 0.1), collide=False)
        racks = 0
        for row, y in enumerate(RACK_ROWS_Y):
            for side, x in enumerate(RACK_SIDES_X):
                base = f"{ROOT}/Racks/Row{row}_Side{side}"
                for post in range(5):  # uprights, front and back
                    px = x - RACK_LENGTH / 2 + post * RACK_LENGTH / 4
                    for back, py in enumerate((y - RACK_DEPTH / 2 + 0.04, y + RACK_DEPTH / 2 - 0.04)):
                        _box(stage, f"{base}/Post{post}_{back}", (px, py, RACK_HEIGHT / 2), (0.08, 0.08, RACK_HEIGHT), (0.1, 0.25, 0.6))
                        count += 1
                for level, z in enumerate(SHELF_Z):
                    _box(stage, f"{base}/Shelf{level}", (x, y, z), (RACK_LENGTH, RACK_DEPTH, 0.05), (0.9, 0.45, 0.1))
                    count += 1
                    for item in range(5):  # boxes on the shelf
                        cx = x - RACK_LENGTH / 2 + 0.8 + item * (RACK_LENGTH - 1.6) / 4
                        h = 0.45 + 0.05 * ((row + side + level + item) % 3)
                        _box(stage, f"{base}/Cargo{level}_{item}", (cx, y, z + 0.025 + h / 2), (0.9, 0.7, h),
                             CARGO_COLORS[(row + side + level + item) % len(CARGO_COLORS)])
                        count += 1
                racks += 1
    return dict(prim=ROOT, size_m=[LENGTH, WIDTH, WALL_HEIGHT], racks=racks, solid_prims=count,
                aisles_y=list(AISLES_Y), cross_aisle_x=0.0, robot_start=list(START))


def move_to(stage, path, xyz):
    """Put a prim at *xyz* (x and y, metres), keeping its height (an import may have lifted it onto the floor) and any rotation."""
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        xform = UsdGeom.Xformable(stage.GetPrimAtPath(path))
        ops = xform.GetOrderedXformOps()
        for op in ops:
            if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                z = float(op.Get()[2]) if op.Get() is not None else 0.0
                op.Set(Gf.Vec3d(xyz[0], xyz[1], z))
                return
        op = xform.AddTranslateOp(opSuffix="warehouse")
        op.Set(Gf.Vec3d(xyz[0], xyz[1], xyz[2]))
        xform.SetXformOpOrder([op, *ops])  # translate outermost, so any rotation stays about the robot's own origin

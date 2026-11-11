"""Editing any prim in the session layer, with before/after values and a working revert (no Kit)."""

import math
import sys
from pathlib import Path

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics
from isaac_mcp.project_assets import AssetError
from test_scene_advanced import advanced  # noqa: F401 - the fixtures
from test_scene_usd import scene  # noqa: F401
from sim_tools.values import attribute_kind, jsonable, typed_value

FOREIGN = """#usda 1.0
(
    metersPerUnit = 1
    upAxis = "Z"
)
over "World"
{
    def Xform "Table"
    {
        double3 xformOp:translate = (1, 0, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
    def Cube "Object"
    {
        double size = 0.2
        double3 xformOp:translate = (0, 0, 1)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
    def Xform "Slanted"
    {
        double3 xformOp:translate = (2, 0, 0)
        float3 xformOp:rotateXYZ = (0, 0, 45)
        double3 xformOp:scale = (1, 2, 1)
        uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]
    }
    def Xform "Animated"
    {
        double3 xformOp:translate.timeSamples = {
            0: (0, 0, 0),
            10: (1, 0, 0),
        }
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
    def Cube "Shelved"
    (
        prepend apiSchemas = ["PhysicsCollisionAPI"]
    )
    {
        double size = 0.3
    }
    def Xform "Robot"
    {
        def Xform "base" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {
            def Xform "tip" {}
        }
        def Xform "link1" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {}
        def PhysicsFixedJoint "root_joint" (prepend apiSchemas = ["PhysicsArticulationRootAPI"])
        {
            rel physics:body1 = </World/Robot/base>
        }
        def PhysicsRevoluteJoint "joint1"
        {
            rel physics:body0 = </World/Robot/base>
            rel physics:body1 = </World/Robot/link1>
        }
    }
}
"""


@pytest.fixture
def foreign(advanced):
    """A source file the pack did not create: a table, a visual-only object and a small robot."""
    scene = advanced.s
    layer = Sdf.Layer.CreateAnonymous("foreign.usda")
    layer.ImportFromString(FOREIGN)
    stage = scene.stage()
    stage.GetRootLayer().subLayerPaths.append(layer.identifier)
    return advanced, stage, layer


def instance_proxy(scene, stage):
    asset = Sdf.Layer.CreateAnonymous("asset.usda")
    asset.ImportFromString(
        '#usda 1.0\ndef Xform "Asset"\n{\n    def Cube "Body"\n    {\n    }\n}\n')
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        inst = stage.DefinePrim("/World/Inst", "Xform")
        inst.GetReferences().AddReference(asset.identifier, "/Asset")
        inst.SetInstanceable(True)
    return "/World/Inst/Body"


def test_plane_and_mass_creation(advanced):
    s, stage = advanced.s, advanced.s.stage()
    plane = s.create(stage, "/World/Ground", "Plane", size=4, collision=True)
    prim = stage.GetPrimAtPath("/World/Ground")
    assert prim.IsA(UsdGeom.Plane)
    assert UsdGeom.Plane(prim).GetWidthAttr().Get() == 4
    assert UsdGeom.Plane(prim).GetLengthAttr().Get() == 4
    assert UsdGeom.Plane(prim).GetAxisAttr().Get() == UsdGeom.Tokens.z
    assert prim.HasAPI(UsdPhysics.CollisionAPI) and not prim.HasAPI(UsdPhysics.RigidBodyAPI)
    assert plane["type"] == "Plane"

    heavy = s.create(stage, "/World/Heavy", "Cube", rigid=True, mass=2.5)
    light = s.create(stage, "/World/Light", "Cube", rigid=True, density=500)
    assert heavy["physics"]["mass"] == pytest.approx(2.5) and heavy["physics"]["mass_authored"]
    assert light["physics"]["density"] == pytest.approx(500)
    assert light["physics"]["mass_authored"] is False

    for args, code in (
        (dict(path="/World/Both", kind="Cube", rigid=True, mass=1, density=1), "CONFLICT"),
        (dict(path="/World/Zero", kind="Cube", rigid=True, mass=0), "INVALID_PHYSICS"),
        (dict(path="/World/BoxXform", kind="Xform", mass=1), "INVALID_PHYSICS"),
        (dict(path="/World/PlaneMass", kind="Plane", mass=1), "INVALID_PHYSICS"),
        (dict(path="/World/PlaneRigid", kind="Plane", rigid=True), "INVALID_PHYSICS"),
    ):
        with pytest.raises(AssetError) as error:
            s.create(stage, **args)
        assert error.value.code == code
        assert not stage.GetPrimAtPath(args["path"])


def test_a_foreign_prim_moves_in_the_session_layer_and_revert_restores_the_source(foreign):
    advanced, stage, source = foreign
    before_source = source.ExportToString()
    result = advanced.s.transform(stage, "/World/Table", position=[3, 4, 5], frame="world")
    assert result["world_position"] == pytest.approx([3, 4, 5])
    assert result["before"]["world_position"] == pytest.approx([1, 0, 0])
    assert result["layer"] == "session" and result["source_files_changed"] is False
    assert source.ExportToString() == before_source
    assert stage.GetSessionLayer().GetPropertyAtPath("/World/Table.xformOp:transform") is not None
    assert stage.GetSessionLayer().GetPropertyAtPath("/World/Table.xformOpOrder") is not None

    reverted = advanced.s.revert(stage, "/World/Table")
    assert reverted["removed"] and reverted["source_files_changed"] is False
    assert advanced.s.get(stage, "/World/Table")["world_position"] == pytest.approx([1, 0, 0])
    assert stage.GetSessionLayer().GetPropertyAtPath("/World/Table.xformOp:transform") is None
    assert source.ExportToString() == before_source


def test_moving_a_multi_op_prim_overrides_the_composed_order_and_revert_restores_it(foreign):
    advanced, stage, source = foreign
    before_source = source.ExportToString()
    assert len(UsdGeom.Xformable(stage.GetPrimAtPath("/World/Slanted")).GetOrderedXformOps()) == 3
    result = advanced.s.transform(stage, "/World/Slanted", position=[0, 0, 2],
                                 orientation=[1, 0, 0, 0], frame="world")
    assert result["world_position"] == pytest.approx([0, 0, 2])
    assert result["authored"] == ["xformOp:transform", "xformOpOrder"]
    assert len(UsdGeom.Xformable(stage.GetPrimAtPath("/World/Slanted")).GetOrderedXformOps()) == 1
    advanced.s.revert(stage, "/World/Slanted")
    ops = UsdGeom.Xformable(stage.GetPrimAtPath("/World/Slanted")).GetOrderedXformOps()
    assert [op.GetName() for op in ops] == ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]
    assert source.ExportToString() == before_source


def test_transform_refusals_name_the_way_forward(foreign):
    advanced, stage, _ = foreign
    s = advanced.s
    with pytest.raises(AssetError) as error:
        s.transform(stage, "/World/Animated", position=[0, 0, 0])
    assert error.value.code == "ANIMATED_TRANSFORM" and "parent Xform" in str(error.value)

    with pytest.raises(AssetError) as error:
        s.transform(stage, "/World/Robot/link1", position=[0, 0, 0])
    assert error.value.code == "ARTICULATION_LINK"
    assert error.value.details["articulation_root"] == "/World/Robot/root_joint"
    assert error.value.details["retry_with"]["path"] == "/World/Robot"
    # The whole robot (the Xform above its joints) is still movable.
    assert s.transform(stage, "/World/Robot", position=[0, 1, 0])["world_position"] == pytest.approx([0, 1, 0])

    stage.DefinePrim("/World/Scope", "Scope")
    with pytest.raises(AssetError) as error:
        s.transform(stage, "/World/Scope")
    assert error.value.code == "WRONG_TYPE"

    stage.DefinePrim("/Render/KitViewport", "Xform")
    with pytest.raises(AssetError) as error:
        s.transform(stage, "/Render/KitViewport", position=[0, 0, 0])
    assert error.value.code == "PROTECTED_PRIM"

    proxy = instance_proxy(s, stage)
    with pytest.raises(AssetError) as error:
        s.transform(stage, proxy, position=[0, 0, 0])
    assert error.value.code == "INSTANCE_PROXY" and "instance root" in str(error.value)
    assert s.transform(stage, "/World/Inst", position=[1, 1, 1])["world_position"] == pytest.approx([1, 1, 1])
    assert stage.GetSessionLayer().ExportToString()  # refusals authored nothing beyond the instance


def test_set_attribute_reads_before_and_after_and_revert_removes_only_session_opinions(foreign):
    advanced, stage, source = foreign
    before_source = source.ExportToString()
    table = stage.GetPrimAtPath("/World/Table")
    assert typed_value("invisible", "token") == "invisible"
    result = advanced.execute("sim.prim.set_attribute", stage,
                              dict(path="/World/Table", attribute="visibility", value="invisible"))
    assert result["before"] == "inherited" and result["after"] == "invisible"
    assert table.GetAttribute("visibility").Get() == "invisible"

    created = advanced.execute("sim.prim.set_attribute", stage, dict(
        path="/World/Table", attribute="user:owner", value="kaleb", type="string"))
    assert created["created_attribute"] is True and created["after"] == "kaleb"
    assert table.GetAttribute("user:owner").Get() == "kaleb"
    assert table.GetAttribute("user:owner").IsCustom()

    vector = advanced.execute("sim.prim.set_attribute", stage, dict(
        path="/World/Table", attribute="user:offset", value=[1, 2, 3], type="float3"))
    assert vector["after"] == pytest.approx([1, 2, 3])

    for name in ("points", "xformOpOrder", "xformOp:translate", "physics:mass", "physxRigidBody:mass"):
        with pytest.raises(AssetError) as error:
            advanced.execute("sim.prim.set_attribute", stage, dict(
                path="/World/Table", attribute=name, value=1))
        assert error.value.code == "ENGINE_OWNED_ATTRIBUTE", name
        assert "sim__" in str(error.value) or "schema" in str(error.value)
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.prim.set_attribute", stage, dict(
            path="/World/Table", attribute="user:bad", value="x", type="matrix4d"))
    assert error.value.code == "UNSUPPORTED_TYPE"
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.prim.set_attribute", stage,
                         dict(path="/World/Table", attribute="not:known", value=1))
    assert error.value.code == "INVALID_VALUE" and "type=" in str(error.value)

    reverted = advanced.s.revert(stage, "/World/Table")
    assert {"visibility", "user:owner", "user:offset"} <= {
        item.rsplit(".", 1)[-1] for item in reverted["removed"]}
    assert table.GetAttribute("visibility").Get() == "inherited"
    assert not table.HasAttribute("user:owner")
    assert source.ExportToString() == before_source


def test_physics_apply_adds_and_removes_and_revert_undoes_it(foreign):
    advanced, stage, _ = foreign
    s = advanced.s
    object_prim = stage.GetPrimAtPath("/World/Object")
    assert not object_prim.HasAPI(UsdPhysics.CollisionAPI)
    result = advanced.execute("sim.physics.apply", stage, dict(
        path="/World/Object", collider=True, approximation="convexHull", rigid_body=True,
        kinematic=True, mass=2.0))
    assert result["after"]["physics"]["collision"] and result["after"]["physics"]["rigid_body"]
    assert result["after"]["physics"]["kinematic"] is True
    assert result["after"]["physics"]["mass"] == pytest.approx(2.0)
    assert result["authored_mass_kg"] == pytest.approx(2.0)
    assert object_prim.GetAttribute("physics:approximation").Get() == "convexHull"
    assert not result["before"]["physics"]["collision"]

    removed = advanced.execute("sim.physics.apply", stage, dict(
        path="/World/Object", collider=False, rigid_body=False))
    assert removed["after"]["physics"]["collision"] is False
    assert removed["after"]["physics"]["rigid_body"] is False

    advanced.execute("sim.physics.apply", stage, dict(path="/World/Object", collider=True, density=100))
    assert stage.GetPrimAtPath("/World/Object").GetAttribute("physics:density").Get() == pytest.approx(100)
    reverted = s.revert(stage, "/World/Object")
    assert reverted["removed"] and not reverted["not_reverted"]
    assert s.get(stage, "/World/Object")["physics"]["collision"] is False
    assert s.get(stage, "/World/Object")["physics"]["mass"] is None
    assert "PhysicsCollisionAPI" not in (stage.GetSessionLayer().ExportToString() or "")


def test_physics_apply_refusals_are_explained(foreign):
    advanced, stage, _ = foreign
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.physics.apply", stage, dict(path="/World/Robot/base/tip", rigid_body=True))
    assert error.value.code == "NESTED_BODY" and "already carries" in str(error.value)
    assert error.value.details["body_ancestor"] == "/World/Robot/base"
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.physics.apply", stage, dict(path="/World/Robot/root_joint", rigid_body=True))
    assert error.value.code == "INVALID_PHYSICS" and "articulation root" in str(error.value)
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.physics.apply", stage, dict(path="/World/Object", mass=1, density=1))
    assert error.value.code == "CONFLICT"
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.physics.apply", stage, dict(path="/World/Object", kinematic=True))
    assert error.value.code == "INVALID_PHYSICS" and "rigid_body=true" in str(error.value)
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.physics.apply", stage, dict(path="/World/Object", approximation="convexHull"))
    assert error.value.code == "INVALID_VALUE"
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.physics.apply", stage, dict(path="/World/Object", collider=True,
                                                           approximation="voxels"))
    assert error.value.code == "INVALID_VALUE"
    # A collider applied by the source file cannot be turned off from the session layer.
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.physics.apply", stage, dict(path="/World/Shelved", collider=False))
    assert error.value.code == "READ_ONLY_SOURCE" and "source" in str(error.value)
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.physics.apply", stage, dict(path="/World/Object"))
    assert error.value.code == "INVALID_VALUE"
    assert stage.GetSessionLayer().GetPrimAtPath("/World/Object") is None


def test_revert_without_recorded_edits_is_a_no_op_not_an_error(foreign):
    advanced, stage, _ = foreign
    result = advanced.execute("sim.prim.revert", stage, dict(path="/World/Table"))
    assert result["removed"] == [] and result["reverted_paths"] == []
    assert "nothing was removed" in result["note"]
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.prim.revert", stage, dict(path="/World/Missing"))
    assert error.value.code == "NOT_FOUND"


def test_revert_subtree_reaches_descendants_only_when_asked(foreign):
    advanced, stage, _ = foreign
    advanced.execute("sim.prim.set_attribute", stage,
                     dict(path="/World/Robot/base", attribute="visibility", value="invisible"))
    advanced.execute("sim.prim.set_attribute", stage,
                     dict(path="/World/Robot/link1", attribute="visibility", value="invisible"))
    one = advanced.execute("sim.prim.revert", stage, dict(path="/World/Robot", subtree=False))
    assert one["reverted_paths"] == [] and stage.GetPrimAtPath("/World/Robot/base").GetAttribute(
        "visibility").Get() == "invisible"
    both = advanced.execute("sim.prim.revert", stage, dict(path="/World/Robot", subtree=True))
    assert sorted(both["reverted_paths"]) == ["/World/Robot/base", "/World/Robot/link1"]
    assert stage.GetPrimAtPath("/World/Robot/base").GetAttribute("visibility").Get() == "inherited"


def test_attribute_helpers_are_typed_and_json_safe():
    assert attribute_kind("visibility") is None
    assert "physics" in attribute_kind("physics:mass")
    assert "sim__prim__set_transform" in attribute_kind("xformOp:translate")
    assert attribute_kind("extent")
    assert jsonable(Gf.Vec3f(1, 2, 3)) == pytest.approx([1, 2, 3])
    assert jsonable(Gf.Quatf(1, Gf.Vec3f(0, 0, 0))) == pytest.approx([1, 0, 0, 0])
    assert jsonable(Sdf.Path("/World/Box")) == "/World/Box"
    assert typed_value(True, "bool") is True
    assert typed_value("false", "bool") is False
    assert typed_value(3, "int") == 3
    assert typed_value(2, "double") == 2.0
    assert typed_value([1, 2], "float2") == Gf.Vec2f(1, 2)
    assert typed_value([1, 0, 0, 0], "quatd") == Gf.Quatd(1, Gf.Vec3d(0, 0, 0))
    for value, kind in ((1.5, "int"), (True, "int"), (-1, "uint"), ("x", "float"),
                        ([1, 2], "float3"), (float("nan"), "float"), (1, "bool")):
        with pytest.raises(AssetError):
            typed_value(value, kind)

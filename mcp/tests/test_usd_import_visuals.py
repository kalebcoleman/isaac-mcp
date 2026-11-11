"""Importing a USD robot whose visuals live in a payload: they compose, are counted, and follow physics."""

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import UsdGeom

from isaac_mcp.project_assets import AssetError, Broker
from test_scene_usd import scene  # noqa: F401 - fixture

HEADER = '''#usda 1.0
(
    defaultPrim = "robot"
    metersPerUnit = 1
    upAxis = "Z"
)
'''
TRIANGLE = '''
        def Mesh "mesh"
        {
            int[] faceVertexCounts = [3]
            int[] faceVertexIndices = [0, 1, 2]
            point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        }
'''
BASE = HEADER + '''
def Scope "visuals"
{
    def Xform "base_link"
    {''' + TRIANGLE + '''    }
    def Xform "arm_link"
    {''' + TRIANGLE + '''    }
}

def Scope "colliders"
{
    def Xform "base_link"
    {
        def Cube "box" (
            prepend apiSchemas = ["PhysicsCollisionAPI"]
        )
        {
            uniform token purpose = "guide"
        }
    }
}

def Xform "robot"
{
    def Xform "base_link" (
        prepend apiSchemas = ["PhysicsRigidBodyAPI"]
    )
    {
        def Xform "visuals" (
            instanceable = true
            prepend references = </visuals/base_link>
        )
        {
        }
        def Xform "collisions" (
            instanceable = true
            prepend references = </colliders/base_link>
        )
        {
        }
    }
    def Xform "arm_link" (
        prepend apiSchemas = ["PhysicsRigidBodyAPI"]
    )
    {
        def Xform "visuals" (
            instanceable = true
            prepend references = </visuals/arm_link>
        )
        {
        }
    }
}
'''
PHYSICS = '''#usda 1.0
(
    defaultPrim = "robot"
    metersPerUnit = 1
    upAxis = "Z"
    subLayers = [
        @./robot_base.usda@
    ]
)

over "robot"
{
    def Scope "joints"
    {
    }
}
'''
TOP = HEADER + '''
def Xform "robot" (
    variants = {
        string Gripper = "None"
        string Physics = "PhysX"
    }
    prepend variantSets = ["Physics", "Gripper"]
)
{
    variantSet "Physics" = {
        "None" (
            prepend references = @./SubUSDs/robot_base.usda@
        ) {
        }
        "PhysX" (
            prepend payload = @./SubUSDs/robot_physics.usda@
        ) {
        }
    }
    variantSet "Gripper" = {
        "None" {
        }
        "Fancy" {
            def Xform "Gripper" (
                prepend references = @./SubUSDs/GRIPPER@
            )
            {
            }
        }
    }
}
'''
PHYSICS_ONLY = HEADER + '''
def Xform "robot" (
    variants = {
        string Looks = "Off"
    }
    prepend variantSets = "Looks"
)
{
    variantSet "Looks" = {
        "Off" {
        }
    }
    def Xform "base_link" (
        prepend apiSchemas = ["PhysicsRigidBodyAPI"]
    )
    {
        def Cube "box" (
            prepend apiSchemas = ["PhysicsCollisionAPI"]
        )
        {
            uniform token purpose = "guide"
        }
    }
}
'''
TEXTURED = HEADER + '''
def Xform "robot"
{
    def Cube "body"
    {
        asset inputs:file = @./missing.png@
    }
}
'''
FRAMES = HEADER.replace('"robot"', '"rig"') + '''
def Xform "rig"
{
    def Xform "tool0"
    {
    }
}
'''


@pytest.fixture
def pack(scene, tmp_path):  # noqa: F811
    """A robot laid out like a published one: a top-level file, a physics payload, a base layer with the meshes."""
    project, staging = tmp_path / "pack", tmp_path / "stage"
    (project / "robot" / "SubUSDs").mkdir(parents=True)
    staging.mkdir(mode=0o700)
    (project / "robot" / "SubUSDs" / "robot_base.usda").write_text(BASE)
    (project / "robot" / "SubUSDs" / "robot_physics.usda").write_text(PHYSICS)
    (project / "robot" / "robot.usda").write_text(TOP.replace("GRIPPER", "gripper_not_shipped.usda"))
    (project / "robot" / "physics_only.usda").write_text(PHYSICS_ONLY)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(dict(version=1, profile="trusted_project", roots={"pack": str(project)}, staging=str(staging))))
    scene.broker = Broker(str(config))

    def bring_in(relative, prim="/World/Robot"):
        snap = scene.broker.snapshot("pack", relative, kind="usd")
        return scene.import_path(scene.stage(), snap["snapshot_id"], snap["manifest_sha256"], prim)

    return project, bring_in


def test_the_top_level_file_composes_its_visuals_and_they_are_expanded_to_follow_physics(scene, pack):  # noqa: F811
    _, bring_in = pack
    result = bring_in("robot/robot.usda")
    assert result["visuals"] == {"drawn_geometry": 2, "expanded_instances": 2, "truncated": False}
    assert "warnings" not in result
    stage = scene.stage()
    for link in ("base_link", "arm_link"):
        visuals = stage.GetPrimAtPath(f"/World/Robot/Asset/{link}/visuals")
        assert visuals and not visuals.IsInstance() and visuals.GetChild("mesh").IsA(UsdGeom.Mesh)
    assert stage.GetPrimAtPath("/World/Robot/Asset/base_link/collisions").IsInstance(), "colliders keep their instancing"
    assert stage.GetPrimAtPath("/World/Robot/Asset/joints"), "the physics payload composed too"
    assert "instanceable" not in stage.GetRootLayer().ExportToString(), "the expansion is a session-layer override"


def test_a_file_named_only_by_an_unselected_variant_does_not_block_the_import(scene, pack):  # noqa: F811
    _, bring_in = pack
    result = bring_in("robot/robot.usda")
    assert result["unselected_variant_dependencies"]["missing"] == ["gripper_not_shipped.usda"]
    assert "not selected" in result["unselected_variant_dependencies"]["note"]


def test_a_missing_layer_the_selected_variant_uses_is_still_refused(scene, pack):  # noqa: F811
    project, bring_in = pack
    (project / "robot" / "SubUSDs" / "robot_physics.usda").unlink()
    with pytest.raises(AssetError) as error:
        bring_in("robot/robot.usda")
    assert error.value.code == "DEPENDENCIES" and "robot_physics.usda" in str(error.value)
    assert not scene.stage().GetPrimAtPath("/World/Robot")


def test_a_missing_texture_is_never_waved_through_as_an_unselected_variant(scene, pack):  # noqa: F811
    project, bring_in = pack
    (project / "robot" / "textured.usda").write_text(TEXTURED)
    with pytest.raises(AssetError) as error:
        bring_in("robot/textured.usda")
    assert error.value.code == "DEPENDENCIES"


def test_a_physics_only_asset_is_imported_with_a_warning_and_its_variant_selections(scene, pack):  # noqa: F811
    _, bring_in = pack
    result = bring_in("robot/physics_only.usda", "/World/Ghost")
    assert result["visuals"]["drawn_geometry"] == 0 and result["visuals"]["variant_selections"] == {"Looks": "Off"}
    (warning,) = result["warnings"]
    assert "nothing to draw" in warning and "/World/Ghost/Asset" in warning and "top-level file" in warning


def test_an_asset_with_no_physics_and_no_geometry_is_not_warned_about(scene, pack):  # noqa: F811
    project, bring_in = pack
    (project / "robot" / "frames.usda").write_text(FRAMES)
    result = bring_in("robot/frames.usda", "/World/Rig")
    assert result["visuals"]["drawn_geometry"] == 0 and "warnings" not in result


def test_the_simulator_s_own_material_is_recognised_through_a_symlinked_environment(scene, pack, tmp_path):  # noqa: F811
    """An environment reached through a symlink reports its files by their real path."""
    import hashlib

    project, bring_in = pack
    real = tmp_path / "env-real" / "mdl"
    real.mkdir(parents=True)
    (real / "OmniPBR.mdl").write_text("mdl 1.4;")
    (tmp_path / "env-link").symlink_to(tmp_path / "env-real")
    scene.runtime_assets["OmniPBR.mdl"] = {"path": str(tmp_path / "env-link" / "mdl" / "OmniPBR.mdl"),
                                           "sha256": hashlib.sha256(b"mdl 1.4;").hexdigest(), "source": "test"}
    (project / "robot" / "shaded.usda").write_text(HEADER + '''
def Xform "robot"
{
    def Cube "body"
    {
    }
    def Shader "look"
    {
        uniform token info:implementationSource = "sourceAsset"
        asset info:mdl:sourceAsset = @@@''' + str(real / "OmniPBR.mdl") + '''@@@
    }
}
''')
    result = bring_in("robot/shaded.usda", "/World/Shaded")
    assert list(result["runtime_dependencies"]) == ["OmniPBR.mdl"] and result["visuals"]["drawn_geometry"] == 1

"""Exporting a robot to URDF: finding the robot above an articulation root, refusing ambiguity, and lost structure."""

from xml.etree import ElementTree

import pytest

pytest.importorskip("pxr.Usd")
from twinsmith.project_assets import AssetError
from pxr import Usd, UsdGeom, UsdPhysics
from sim_tools.urdf_export import _lost_its_structure, robot_root



def two_robots():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
    for name in ("a", "b"):
        base = UsdGeom.Xform.Define(stage, f"/World/{name}/Asset/Geometry/base").GetPrim()
        tip = UsdGeom.Xform.Define(stage, f"/World/{name}/Asset/Geometry/base/tip").GetPrim()
        for body in (base, tip):
            UsdPhysics.RigidBodyAPI.Apply(body)
        UsdPhysics.ArticulationRootAPI.Apply(base)
        joint = UsdPhysics.RevoluteJoint.Define(stage, f"/World/{name}/Asset/Physics/j")
        joint.CreateBody0Rel().AddTarget(base.GetPath())
        joint.CreateBody1Rel().AddTarget(tip.GetPath())
    UsdGeom.Plane.Define(stage, "/World/Ground")
    return stage


def test_export_of_an_articulation_root_finds_the_robot_above_it():
    """From the articulation root the exporter wrote one link and a floating joint: the robot was gone and the call said ok."""
    stage = two_robots()
    assert robot_root(stage, "/World/b/Asset/Geometry/base") == "/World/b/Asset"
    assert robot_root(stage, "/World/b/Asset/Geometry/base/tip") == "/World/b/Asset"
    assert robot_root(stage, "/World/b") == "/World/b"


@pytest.mark.parametrize("path", ["/World", None])
def test_export_above_two_robots_refuses_and_lists_them(path):
    """It wrote the first robot it found, named after the parent."""
    with pytest.raises(AssetError) as error:
        robot_root(two_robots(), path)
    assert error.value.code == "AMBIGUOUS_ROBOT" and "/World/a/Asset/Geometry/base" in str(error.value)


def test_export_root_does_not_climb_to_an_ancestor_with_another_robot():
    stage = two_robots()
    # Move the first robot's joint outside its subtree: its ancestor also owns robot b.
    stage.RemovePrim("/World/a/Asset/Physics/j")
    joint = UsdPhysics.RevoluteJoint.Define(stage, "/World/shared_joint")
    joint.CreateBody0Rel().SetTargets(["/World/a/Asset/Geometry/base"])
    joint.CreateBody1Rel().SetTargets(["/World/a/Asset/Geometry/base/tip"])
    with pytest.raises(AssetError) as error:
        robot_root(stage, "/World/a/Asset/Geometry/base")
    assert error.value.code == "AMBIGUOUS_ROBOT"


def test_export_of_something_with_no_bodies_refuses():
    """A ground plane was exported as another robot's geometry."""
    with pytest.raises(AssetError) as error:
        robot_root(two_robots(), "/World/Ground")
    assert error.value.code == "NOT_A_ROBOT"


def test_export_of_a_prop_with_no_moving_joint_stays_on_the_prop():
    stage = two_robots()
    box = UsdGeom.Cube.Define(stage, "/World/box").GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(box)
    assert robot_root(stage, "/World/box") == "/World/box"


def test_a_floating_base_joint_alone_is_a_lost_robot():
    stage = two_robots()
    urdf = ElementTree.fromstring('<robot><link name="world"/><link name="b"/><joint name="floating_base" type="floating"/></robot>')
    assert _lost_its_structure(stage, "/World/a/Asset", urdf) is True
    good = ElementTree.fromstring('<robot><link name="b"/><link name="t"/><joint name="j" type="revolute"/></robot>')
    assert _lost_its_structure(stage, "/World/a/Asset", good) is False

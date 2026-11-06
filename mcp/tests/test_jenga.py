"""The quick-start Jenga tower: its layout, the poke the arm is told to make, and (with USD available) the prims it builds."""

import sys
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
import pxr
from pxr import Usd, UsdGeom, UsdPhysics

try:
    from pxr import PhysxSchema  # noqa: F401 - only Kit's USD has it; the solver settings are not what these tests check
except ImportError:
    pxr.PhysxSchema = mock.MagicMock()
    sys.modules["pxr.PhysxSchema"] = pxr.PhysxSchema
from sim_tools import jenga


def _stage():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World")
    return stage


def test_layers_cross_and_touch():
    blocks = list(jenga.block_centers())
    assert len(blocks) == jenga.LAYERS * 3
    for layer, _index, along_x, _x, _y, z in blocks:
        assert along_x == (layer % 2 == 1)
        assert z == pytest.approx(jenga.TABLE_TOP + (layer + 0.5) * jenga.BLOCK[2], abs=jenga.LOOSE)
    middle = [b for b in blocks if b[0] == 9]
    assert [round(b[4], 4) for b in middle] == [round((i - 1) * (jenga.BLOCK[1] + jenga.SIDE_GAP), 4) for i in range(3)]


def test_an_odd_layers_middle_block_rests_on_the_layer_below_and_carries_no_load():
    stage = _stage()
    jenga.build(stage)
    loose = stage.GetPrimAtPath(jenga.block_path(9, 1))
    side = stage.GetPrimAtPath(jenga.block_path(9, 0))
    def bottom_top(prim):
        xform = UsdGeom.Xformable(prim)
        ops = {op.GetOpName(): op.Get() for op in xform.GetOrderedXformOps()}
        z, height = ops["xformOp:translate"][2], ops["xformOp:scale"][2]
        return z - height / 2, z + height / 2
    assert bottom_top(loose)[0] == pytest.approx(bottom_top(side)[0], abs=1e-6)  # both sit on layer 8
    assert bottom_top(side)[1] - bottom_top(loose)[1] == pytest.approx(jenga.LOOSE, abs=1e-6)  # layer 10 rests on the side blocks


def test_the_poke_runs_along_the_block_and_stops_before_the_fingers_reach_the_tower():
    poke = jenga.poke_targets()
    assert poke["block"] == jenga.block_path(9, 1)
    assert poke["approach"][0] < poke["touch"][0] < poke["through"][0]
    assert poke["touch"][0] == pytest.approx(jenga.TOWER_XY[0] - jenga.BLOCK[0] / 2)  # the near face
    assert {p[2] for p in (poke["approach"], poke["touch"], poke["through"])} == {jenga.TABLE_TOP + 9.5 * jenga.BLOCK[2]}
    fingertips = 0.112  # from the hand's origin; the rod must be the first thing into the tower
    assert (poke["through"][0] - poke["touch"][0]) < jenga.ROD_TIP - fingertips
    assert poke["ik"] == dict(link=jenga.HAND_LINK, offset=[0.0, 0.0, jenga.ROD_TIP], orientation=jenga.HAND_LEVEL)


def test_an_even_layer_runs_across_and_cannot_be_poked_from_the_arm():
    with pytest.raises(ValueError):
        jenga.poke_targets(8)


def test_it_builds_a_table_and_dynamic_blocks_of_the_stated_mass():
    stage = _stage()
    report = jenga.build(stage)
    assert report["blocks"] == 54 and report["top_block"] == jenga.block_path(17, 1)
    assert report["poke_example"] == jenga.poke_targets()
    bodies = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI)]
    assert len(bodies) == 54 and all(p.GetParent().GetName() == "Tower" for p in bodies)
    assert all(UsdPhysics.MassAPI(p).GetMassAttr().Get() == pytest.approx(jenga.BLOCK_MASS) for p in bodies)
    table = stage.GetPrimAtPath(f"{jenga.ROOT}/Table")
    assert table.HasAPI(UsdPhysics.CollisionAPI) and not table.HasAPI(UsdPhysics.RigidBodyAPI)
    assert report["tower_top_z"] == pytest.approx(jenga.TABLE_TOP + 18 * jenga.BLOCK[2])
    assert not stage.GetRootLayer().GetPrimAtPath(jenga.ROOT)  # session layer only


def test_the_table_leaves_room_for_the_arm_and_holds_the_tower():
    half_x, half_y = jenga.TABLE_SIZE[0] / 2, jenga.TABLE_SIZE[1] / 2
    assert jenga.TABLE_CENTER[0] - half_x >= jenga.ROBOT_BASE[0] + 0.15  # the arm stands beside it
    assert jenga.TABLE_CENTER[0] + half_x > jenga.TOWER_XY[0] + jenga.BLOCK[0]  # a block pushed out lands on it
    assert jenga.BLOCK[0] < half_y


def _franka(stage):
    robot = UsdGeom.Xform.Define(stage, "/World/Franka").GetPrim()
    hand = UsdGeom.Xform.Define(stage, "/World/Franka/panda_hand").GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(hand)
    for name, kind in (("panda_joint1", UsdPhysics.RevoluteJoint), ("panda_finger_joint1", UsdPhysics.PrismaticJoint), ("panda_finger_joint2", UsdPhysics.PrismaticJoint)):
        kind.Define(stage, f"/World/Franka/{name}")
    return robot


def test_a_franka_gets_its_pose_both_fingers_driven_and_a_rod_on_the_hand():
    stage = _stage()
    _franka(stage)
    report = jenga.pose_robot(stage, "/World/Franka")
    assert report["fingers"] == ["panda_finger_joint1", "panda_finger_joint2"]
    assert report["push_rod"] == "/World/Franka/panda_hand/PushRod"
    rod = stage.GetPrimAtPath(report["push_rod"])
    assert rod.HasAPI(UsdPhysics.CollisionAPI) and not rod.HasAPI(UsdPhysics.RigidBodyAPI)  # part of the hand's body, no new joint
    drive = UsdPhysics.DriveAPI(stage.GetPrimAtPath("/World/Franka/panda_finger_joint2"), "linear")
    assert drive.GetTargetPositionAttr().Get() == pytest.approx(jenga.READY_POSE["panda_finger_joint2"])
    assert drive.GetStiffnessAttr().Get() > 0
    joint = UsdPhysics.DriveAPI(stage.GetPrimAtPath("/World/Franka/panda_joint1"), "angular")
    assert joint.GetTargetPositionAttr().Get() == pytest.approx(0.0)


def test_another_robot_is_left_alone():
    stage = _stage()
    UsdGeom.Xform.Define(stage, "/World/Other")
    assert jenga.pose_robot(stage, "/World/Other") == dict(posed_joints=[], fingers=[], ready_pose=None, push_rod=None)

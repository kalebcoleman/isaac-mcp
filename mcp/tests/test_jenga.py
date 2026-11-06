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
        assert z == pytest.approx(jenga.TABLE_TOP + (layer + 0.5) * jenga.BLOCK[2])
    middle = [b for b in blocks if b[0] == 9]
    assert [round(b[4], 4) for b in middle] == [round((i - 1) * (jenga.BLOCK[1] + jenga.SIDE_GAP), 4) for i in range(3)]


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


def _lab_layout():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "examples/jenga_lab/layout.py"
    spec = importlib.util.spec_from_file_location("jenga_lab_layout", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_lab_task_uses_the_same_blocks_rod_and_pose_as_the_scene():
    lab = _lab_layout()
    assert (lab.BLOCK, lab.BLOCK_MASS, lab.FRICTION, lab.ROD_TIP, lab.HAND_LINK) == (jenga.BLOCK, jenga.BLOCK_MASS, jenga.FRICTION, jenga.ROD_TIP, jenga.HAND_LINK)
    assert lab.TOWER_XY == jenga.TOWER_XY
    assert lab.READY_JOINTS["panda_joint6"] == jenga.READY_POSE["panda_joint6"]
    wxyz = jenga.HAND_LEVEL
    assert lab.HAND_LEVEL_XYZW == (wxyz[1], wxyz[2], wxyz[3], wxyz[0])  # Lab 3 orders quaternions x y z w, the tools w x y z
    assert lab.PUSH_Z == pytest.approx(jenga.poke_targets()["touch"][2])  # the height where the arm can hold its hand level


def test_the_lab_tower_starts_clear_of_the_rod_and_its_target_runs_along_x():
    lab = _lab_layout()
    blocks = {name: (position, size) for name, position, size in lab.blocks()}
    assert len(blocks) == lab.LAYERS * 3
    position, size = blocks[lab.TARGET]
    assert size[0] == lab.BLOCK[0] and position[2] == pytest.approx(lab.PUSH_Z)
    near_face = position[0] - lab.BLOCK[0] / 2
    assert lab.TIP_MIN[0] < lab.TIP_START[0] < near_face < lab.TIP_MAX[0]
    assert lab.TIP_MAX[0] - near_face < lab.ROD_TIP - 0.112  # the fingers stay out of the tower at the far limit
    assert min(p[2] - s[2] / 2 for p, s in blocks.values()) == pytest.approx(lab.TABLE_TOP)


def test_the_lab_table_stays_clear_of_the_wrist():
    lab = _lab_layout()  # at this height a table under the wrist held the hand 3.5 cm too high, and the rod hit the wrong layer
    hand_x_at_the_far_limit = lab.TIP_MAX[0] - lab.ROD_TIP
    assert lab.TABLE_CENTER_X - lab.TABLE_SIZE[0] / 2 > hand_x_at_the_far_limit
    assert lab.TABLE_CENTER_X - lab.TABLE_SIZE[0] / 2 < lab.TOWER_XY[0] - lab.BLOCK[0] / 2  # and still holds the tower

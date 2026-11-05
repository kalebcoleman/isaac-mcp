"""Inverse kinematics from USD joints: forward kinematics against USD, solving, limits and refusals (no Kit)."""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics
from isaac_mcp.project_assets import AssetError
from sim_tools import ik


def _body(stage, path, translate):
    prim = UsdGeom.Xform.Define(stage, path).GetPrim()
    UsdGeom.Xformable(prim).AddTranslateOp().Set(Gf.Vec3d(*translate))
    UsdPhysics.RigidBodyAPI.Apply(prim)
    return prim


def _joint(stage, path, kind, b0, b1, pos0, axis, limits=(-170.0, 170.0), pos1=(0, 0, 0), rot0=None):
    joint = (UsdPhysics.RevoluteJoint if kind == "revolute" else UsdPhysics.PrismaticJoint).Define(stage, path)
    joint.CreateBody0Rel().SetTargets([Sdf.Path(b0)])
    joint.CreateBody1Rel().SetTargets([Sdf.Path(b1)])
    joint.CreateLocalPos0Attr().Set(Gf.Vec3f(*pos0))
    joint.CreateLocalRot0Attr().Set(Gf.Quatf(*(rot0 or (1, 0, 0, 0))))
    joint.CreateLocalPos1Attr().Set(Gf.Vec3f(*pos1))
    joint.CreateLocalRot1Attr().Set(Gf.Quatf(1, 0, 0, 0))
    joint.CreateAxisAttr().Set(axis)
    joint.CreateLowerLimitAttr().Set(limits[0])
    joint.CreateUpperLimitAttr().Set(limits[1])
    return joint.GetPrim()


@pytest.fixture
def arm():
    """base -> yaw(Z) -> link1 -> pitch(Y) -> link2 -> pitch(Y) -> link3 -> prismatic(X) -> hand, placed at q = 0."""
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.Xform.Define(stage, "/Arm")
    _body(stage, "/Arm/base", (0, 0, 0))
    _body(stage, "/Arm/link1", (0, 0, 0.2))
    _body(stage, "/Arm/link2", (0.3, 0, 0.2))
    _body(stage, "/Arm/link3", (0.6, 0, 0.2))
    _body(stage, "/Arm/hand", (0.7, 0, 0.2))
    joints = [
        _joint(stage, "/Arm/j1", "revolute", "/Arm/base", "/Arm/link1", (0, 0, 0.2), "Z"),
        _joint(stage, "/Arm/j2", "revolute", "/Arm/link1", "/Arm/link2", (0.3, 0, 0), "Y"),
        _joint(stage, "/Arm/j3", "revolute", "/Arm/link2", "/Arm/link3", (0.3, 0, 0), "Y"),
        _joint(stage, "/Arm/j4", "prismatic", "/Arm/link3", "/Arm/hand", (0.1, 0, 0), "X", limits=(-0.05, 0.1)),
    ]
    return stage, joints


def test_forward_kinematics_matches_usd_at_zero(arm):
    """The joint-frame convention is right if the zero pose reproduces the link poses USD itself holds."""
    stage, joints = arm
    chain = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    expected = ik.body_world_pose(stage, "/Arm/hand")
    assert np.allclose(chain.fk(np.zeros(4)), expected, atol=1e-6)
    assert chain.names == ["j1", "j2", "j3", "j4"]


def test_forward_kinematics_moves_the_hand_the_right_way(arm):
    stage, joints = arm
    chain = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    quarter = chain.fk(np.array([math.pi / 2, 0, 0, 0]))  # yaw 90 degrees about Z: the hand swings from +X to +Y
    assert np.allclose(quarter[:3, 3], [0.0, 0.7, 0.2], atol=1e-6)
    out = chain.fk(np.array([0, 0, 0, 0.05]))  # slide 5 cm along the hand's X
    assert np.allclose(out[:3, 3], [0.75, 0, 0.2], atol=1e-6)


def test_a_reversed_joint_gives_the_same_pose(arm):
    """A chain walked from the hand back to the base uses each joint backwards and must agree with the forward walk."""
    stage, joints = arm
    forward = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    q = np.array([0.4, -0.5, 0.6, 0.03])
    pose = forward.fk(q)
    backward = ik.build_chain(stage, joints, "/Arm/hand", "/Arm/base", base_pose=pose)
    assert backward.names == ["j4", "j3", "j2", "j1"]  # walked hand to base, so the joints come in that order
    assert np.allclose(backward.fk(q[::-1]), ik.body_world_pose(stage, "/Arm/base"), atol=1e-6)


def test_solving_reaches_a_pose_made_from_known_joints(arm):
    stage, joints = arm
    chain = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    truth = np.array([0.5, -0.7, 0.9, 0.04])
    target = chain.fk(truth)
    out = ik.solve(chain, np.zeros(4), target, use_orientation=True)
    assert out["reached"] and out["position_error"] < 1e-3 and out["orientation_error"] < 1e-2
    assert np.allclose(chain.fk(out["q"])[:3, 3], target[:3, 3], atol=2e-3)
    assert np.all(out["q"] >= chain.lower - 1e-9) and np.all(out["q"] <= chain.upper + 1e-9)


def test_position_only_solving_and_staying_near_the_current_pose(arm):
    stage, joints = arm
    chain = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    start = np.array([0.1, -0.2, 0.3, 0.0])
    target = chain.fk(start + np.array([0.05, 0.05, -0.05, 0.0]))
    out = ik.solve(chain, start, target, use_orientation=False)
    assert out["reached"] and out["attempts"] == 1
    assert np.linalg.norm(out["q"] - start) < 0.5  # a small move for a small change of target


def test_an_unreachable_target_is_reported_not_faked(arm):
    stage, joints = arm
    chain = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    target = ik.pose_matrix([5.0, 0.0, 0.2], np.eye(3))
    out = ik.solve(chain, np.zeros(4), target, use_orientation=False, restarts=2)
    assert out["reached"] is False and out["position_error"] > 1.0


def test_joint_limits_are_respected(arm):
    stage, joints = arm
    chain = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    # The slider only reaches 0.1 past its zero, so a hand 0.3 further out along X needs the other joints, and
    # one beyond the arm's whole reach cannot be met without breaking a limit.
    target = ik.pose_matrix([1.2, 0.0, 0.2], np.eye(3))
    out = ik.solve(chain, np.zeros(4), target, use_orientation=False, restarts=3)
    assert np.all(out["q"] <= chain.upper + 1e-9) and np.all(out["q"] >= chain.lower - 1e-9)
    assert out["reached"] is False


def test_an_unsupported_joint_in_the_chain_is_refused(arm):
    stage, joints = arm
    spherical = UsdPhysics.SphericalJoint.Define(stage, "/Arm/j_ball")
    spherical.CreateBody0Rel().SetTargets([Sdf.Path("/Arm/hand")])
    _body(stage, "/Arm/finger", (0.8, 0, 0.2))
    spherical.CreateBody1Rel().SetTargets([Sdf.Path("/Arm/finger")])
    with pytest.raises(AssetError) as error:
        ik.build_chain(stage, [*joints, spherical.GetPrim()], "/Arm/base", "/Arm/finger")
    assert error.value.code == "IK_UNSUPPORTED_JOINT"


def test_a_disconnected_link_is_refused(arm):
    stage, joints = arm
    _body(stage, "/Arm/loose", (2, 0, 0))
    with pytest.raises(AssetError) as error:
        ik.build_chain(stage, joints, "/Arm/base", "/Arm/loose")
    assert error.value.code == "IK_NO_CHAIN"


def test_quaternion_and_rotation_vector_round_trip():
    rng = np.random.default_rng(3)
    for _ in range(20):
        axis = rng.normal(size=3)
        axis /= np.linalg.norm(axis)
        angle = rng.uniform(-3.0, 3.0)
        r = ik.motion("revolute", axis, angle)[:3, :3]
        w, x, y, z = ik.matrix_to_quat(r)
        assert np.allclose(ik.quat_to_matrix(w, x, y, z), r, atol=1e-9)
        assert np.allclose(ik.rotation_vector(r), axis * angle, atol=1e-6)


def test_the_base_is_found_from_the_joints(arm):
    stage, joints = arm
    assert ik.find_base(joints) == "/Arm/base"
    world = UsdPhysics.FixedJoint.Define(stage, "/Arm/root_joint")
    world.CreateBody1Rel().SetTargets([Sdf.Path("/Arm/base")])
    assert ik.find_base([world.GetPrim(), *joints]) == "/Arm/base"
    assert ik.find_base([]) is None


from test_scene_advanced import advanced  # noqa: E402,F401 - the fixtures
from test_scene_usd import scene  # noqa: E402,F401


@pytest.fixture
def arm_in_scene(advanced):
    """The same arm, built in the scene fixture's stage, with the timeline stopped."""
    stage = advanced.s.stage()
    UsdGeom.Xform.Define(stage, "/Arm")
    _body(stage, "/Arm/base", (0, 0, 0))
    _body(stage, "/Arm/link1", (0, 0, 0.2))
    _body(stage, "/Arm/link2", (0.3, 0, 0.2))
    _body(stage, "/Arm/hand", (0.6, 0, 0.2))
    world = UsdPhysics.FixedJoint.Define(stage, "/Arm/root_joint")
    world.CreateBody1Rel().SetTargets([Sdf.Path("/Arm/base")])
    _joint(stage, "/Arm/j1", "revolute", "/Arm/base", "/Arm/link1", (0, 0, 0.2), "Z")
    _joint(stage, "/Arm/j2", "revolute", "/Arm/link1", "/Arm/link2", (0.3, 0, 0), "Y")
    _joint(stage, "/Arm/j3", "revolute", "/Arm/link2", "/Arm/hand", (0.3, 0, 0), "Y")
    return advanced, stage


def test_fk_tool_gives_the_pose_for_given_joints_when_stopped(arm_in_scene):
    advanced, stage = arm_in_scene
    result = advanced.execute("sim.arm.fk", stage, dict(path="/Arm", link="hand", joints={"j1": math.pi / 2}))
    assert result["end_effector_pose"]["position"] == pytest.approx([0.0, 0.6, 0.2], abs=1e-6)
    assert result["joint_source"] == {"j1": "given", "j2": "zero", "j3": "zero"}
    assert result["chain"]["joints"] == ["j1", "j2", "j3"] and result["chain"]["base"] == "/Arm/base"
    assert result["in_base_frame"]["position"] == pytest.approx([0.0, 0.6, 0.2], abs=1e-6)


def test_fk_tool_refuses_an_unknown_joint_and_warns_past_a_limit(arm_in_scene):
    advanced, stage = arm_in_scene
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.arm.fk", stage, dict(path="/Arm", link="hand", joints={"nope": 1.0}))
    assert error.value.code == "NOT_FOUND" and error.value.details["joints"] == ["j1", "j2", "j3"]
    result = advanced.execute("sim.arm.fk", stage, dict(path="/Arm", link="hand", joints={"j1": 3.0}))
    assert "j1" in result["warning"]  # 3 rad is past the 170 degree limit
    with pytest.raises(AssetError):
        advanced.execute("sim.arm.fk", stage, dict(path="/Arm", link="hand", joints=[1.0]))


def test_ik_tool_needs_a_running_simulation(arm_in_scene):
    advanced, stage = arm_in_scene
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.arm.ik", stage, dict(path="/Arm", link="hand", position=[0.5, 0.0, 0.2]))
    assert error.value.code == "PHYSICS_NOT_READY"


def test_a_link_name_must_match_exactly_one_body(arm_in_scene):
    advanced, stage = arm_in_scene
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.arm.fk", stage, dict(path="/Arm", link="gripper"))
    assert error.value.code == "NOT_FOUND" and "/Arm/hand" in error.value.details["candidates"]


def test_an_offset_places_a_point_beyond_the_link_origin(arm):
    """A fingertip is not at the link's origin: with a tip offset the controlled point moves with the link's frame."""
    stage, joints = arm
    chain = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    plain = chain.fk(np.zeros(4))
    chain.set_tip([0.05, 0.0, -0.02])
    tipped = chain.fk(np.zeros(4))
    assert np.allclose(tipped[:3, 3], plain[:3, 3] + [0.05, 0.0, -0.02], atol=1e-9)
    turned = chain.fk(np.array([math.pi / 2, 0, 0, 0]))  # yaw 90 degrees swings the offset with the hand's frame
    assert np.allclose(turned[:3, 3], [-0.0, 0.75, 0.18], atol=1e-6)
    out = ik.solve(chain, np.zeros(4), ik.pose_matrix(turned[:3, 3], np.eye(3)), use_orientation=False)
    assert out["reached"] and np.allclose(chain.fk(out["q"])[:3, 3], turned[:3, 3], atol=2e-3)


def test_inert_joints_names_the_joints_that_cannot_move_the_point(arm):
    """The knee case: a link's own origin sits on its last joint's axis, so that joint cannot move it."""
    stage, joints = arm
    link2 = ik.build_chain(stage, joints, "/Arm/base", "/Arm/link2")  # link2 is the child of j2, with j2's frame at its origin
    assert link2.names == ["j1", "j2"] and link2.inert_joints(np.zeros(2)) == ["j2"]
    link3 = ik.build_chain(stage, joints, "/Arm/base", "/Arm/link3")
    assert link3.names == ["j1", "j2", "j3"] and link3.inert_joints(np.zeros(3)) == ["j3"]
    hand = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")  # the hand sits 0.1 along the slider, off j3's axis only by the slide
    link3.set_tip([0.1, 0.0, 0.0])
    assert link3.inert_joints(np.zeros(3)) == []  # a tip beyond the origin is moved by every joint
    assert hand.names == ["j1", "j2", "j3", "j4"]


def test_many_poses_built_from_known_joints_are_all_reached(arm):
    """Targets made by forward kinematics are reachable by construction, so the solver must find them: from the zero pose,
    for twenty random joint sets, with position and orientation, within the default tolerances."""
    stage, joints = arm
    chain = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    rng = np.random.default_rng(11)
    misses = []
    for k in range(20):
        truth = rng.uniform(np.maximum(chain.lower, -2.0), np.minimum(chain.upper, 2.0))
        out = ik.solve(chain, np.zeros(4), chain.fk(truth), use_orientation=True)
        if not out["reached"]:
            misses.append((k, out["position_error"], out["orientation_error"]))
    assert not misses, misses

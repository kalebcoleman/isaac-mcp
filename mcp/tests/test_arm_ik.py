"""Inverse kinematics from USD joints: forward kinematics against USD, solving, limits and refusals (no Kit)."""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics
from twinsmith.project_assets import AssetError
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


# ---- Harder conventions, and the tool paths a live run cannot cover --------------------------------

def _rx(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1.0]])


def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1.0]])


def _t(x, y, z):
    m = np.eye(4)
    m[:3, 3] = [x, y, z]
    return m


def test_forward_kinematics_with_rotated_joint_frames_and_offset_child_frames():
    """Conventions the straight arm cannot show: a joint frame rotated in its parent (rot0), a joint frame offset in its child
    (pos1), an X-axis revolute and a Z-axis prismatic joint. The expected matrix is composed by hand, not by sim_tools."""
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/Arm")
    for name, at in (("base", (0, 0, 0)), ("link1", (0, 0.1, 0)), ("hand", (0, 0.1, 0.2))):
        _body(stage, f"/Arm/{name}", at)
    quarter = (math.cos(math.pi / 4), 0, 0, math.sin(math.pi / 4))  # 90 degrees about Z, as (w, x, y, z)
    j1 = _joint(stage, "/Arm/j1", "revolute", "/Arm/base", "/Arm/link1", (0, 0.1, 0), "X", pos1=(0.05, 0, 0), rot0=quarter)
    j2 = _joint(stage, "/Arm/j2", "prismatic", "/Arm/link1", "/Arm/hand", (0, 0, 0.2), "Z", limits=(-0.5, 0.5), pos1=(0, 0.01, 0))
    chain = ik.build_chain(stage, [j1, j2], "/Arm/base", "/Arm/hand")
    for q1, q2 in ((0.0, 0.0), (0.7, 0.0), (-0.4, 0.15), (1.2, -0.3)):
        expected = _t(0, 0.1, 0) @ _rz(math.pi / 2) @ _rx(q1) @ _t(-0.05, 0, 0) @ _t(0, 0, 0.2) @ _t(0, 0, q2) @ _t(0, -0.01, 0)
        assert np.allclose(chain.fk(np.array([q1, q2])), expected, atol=1e-6), (q1, q2)


def test_build_chain_refuses_reversed_limits_and_an_unknown_axis(arm):
    stage, joints = arm
    joints[0].GetAttribute("physics:lowerLimit").Set(10.0)
    joints[0].GetAttribute("physics:upperLimit").Set(-10.0)
    with pytest.raises(AssetError) as error:
        ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    assert error.value.code == "IK_BAD_LIMITS"
    joints[0].GetAttribute("physics:lowerLimit").Set(-170.0)
    joints[0].GetAttribute("physics:upperLimit").Set(170.0)
    joints[1].GetAttribute("physics:axis").Set("W")
    with pytest.raises(AssetError) as error:
        ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    assert error.value.code == "IK_UNSUPPORTED_AXIS"


def test_a_scaled_base_still_gives_a_rigid_base_pose(arm):
    stage, _joints = arm
    UsdGeom.Xformable(stage.GetPrimAtPath("/Arm/base")).AddScaleOp().Set(Gf.Vec3f(2, 2, 2))
    base = ik.body_world_pose(stage, "/Arm/base")
    assert np.allclose(base[:3, :3] @ base[:3, :3].T, np.eye(3), atol=1e-9)


def _continuous_chain():
    step = ("j", "revolute", (0.0, 0.0, 1.0), pose_of(0.3, 0, 0), np.eye(4), False, -math.inf, math.inf)
    return ik.Chain([step], np.eye(4))


def pose_of(x, y, z):
    return _t(x, y, z)


def test_a_continuous_joint_is_turned_to_the_nearest_equivalent_angle():
    """-3.1 rad and 3.18 rad are one pose; from 3.0 rad the arm must not swing 6.3 rad to tell them apart."""
    chain = _continuous_chain()
    out = ik.wrap_toward(chain, np.array([-3.1]), np.array([3.0]))
    assert out[0] == pytest.approx(3.0 + 0.1832, abs=1e-3)
    tight = ik.Chain([("j", "revolute", (0.0, 0.0, 1.0), pose_of(0.3, 0, 0), np.eye(4), False, -3.14, 3.14)], np.eye(4))
    assert ik.wrap_toward(tight, np.array([-3.1]), np.array([3.0]))[0] == pytest.approx(-3.1)  # the limits forbid turning it


def test_solving_gives_up_at_the_time_budget_and_says_so(arm):
    stage, joints = arm
    chain = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    out = ik.solve(chain, np.zeros(4), ik.pose_matrix([5.0, 0, 0.2], np.eye(3)), use_orientation=False, time_budget_s=0.0)
    assert out["reached"] is False and out["timed_out"] is True and out["attempts"] == 1


def test_the_last_step_is_judged_before_giving_up(arm):
    stage, joints = arm
    chain = ik.build_chain(stage, joints, "/Arm/base", "/Arm/link2")
    target = chain.fk(np.array([0.02, 0.02]))
    out = ik.solve(chain, np.zeros(2), target, use_orientation=False, pos_tol=5e-3, iterations=1)
    assert out["reached"]  # one accepted step lands within tolerance; it must not be thrown away


@pytest.fixture
def runtime(arm_in_scene, monkeypatch):
    """A fake running simulation for the arm built in the scene: joint values at zero, and body poses from the model itself."""
    advanced, stage = arm_in_scene
    from sim_tools import physics

    joint_prims = [stage.GetPrimAtPath(f"/Arm/{n}") for n in ("j1", "j2", "j3")]
    model = ik.build_chain(stage, joint_prims, "/Arm/base", "/Arm/hand")
    state = dict(names=["j1", "j2", "j3"], positions=[0.0, 0.0, 0.0], velocities=[0.0] * 3)
    monkeypatch.setattr(advanced, "joints", lambda stage_, path: dict(state))

    def pose(m):
        return dict(world_position=m[:3, 3].tolist(), orientation_wxyz=ik.matrix_to_quat(m[:3, :3]))

    seen = {"ee": pose(model.fk(np.zeros(3))), "fail": None}

    def body_state(path, now, dynamics=False, stage_=None):
        if path == "/Arm/hand":
            if seen["fail"]:
                raise seen["fail"]
            return seen["ee"]
        return pose(np.eye(4))

    monkeypatch.setattr(physics, "body_state", body_state)
    return advanced, stage, state, seen, model


def test_ik_on_a_running_arm_solves_and_the_self_check_agrees(runtime):
    advanced, stage, _state, _seen, model = runtime
    target = model.fk(np.array([0.3, -0.4, 0.5]))
    result = advanced.execute("sim.arm.ik", stage, dict(path="/Arm", link="hand", position=target[:3, 3].tolist()))
    assert result["reached"] and result["check"]["status"] == "ok" and result["check"]["position_error"] < 1e-9
    assert any("every joint at zero" in n for n in result["notes"])  # the zero-pose self-check says what it cannot reveal
    assert set(result["joint_targets"]) == {"j1", "j2", "j3"}


def _recording_commands(advanced, monkeypatch):
    sent, real = [], advanced.execute

    def execute(name, stage, a, **kw):
        if name == "sim.joints.command":
            sent.append(a)
            return dict(status="commanded")
        return real(name, stage, a, **kw)

    monkeypatch.setattr(advanced, "execute", execute)
    return sent


def test_move_commands_what_it_solved(runtime, monkeypatch):
    advanced, stage, _state, _seen, model = runtime
    sent = _recording_commands(advanced, monkeypatch)
    target = model.fk(np.array([0.3, -0.4, 0.5]))
    result = advanced.execute("sim.arm.ik", stage, dict(path="/Arm", link="hand", position=target[:3, 3].tolist(), move=True))
    assert result["reached"] and result["commanded"] == dict(status="commanded")
    assert sent == [dict(path="/Arm", targets=result["joint_targets"], clamp=True)]


def test_move_commands_nothing_when_the_target_was_not_reached(runtime, monkeypatch):
    advanced, stage, _state, _seen, _model = runtime
    sent = _recording_commands(advanced, monkeypatch)
    result = advanced.execute("sim.arm.ik", stage, dict(path="/Arm", link="hand", position=[50.0, 0.0, 0.0], move=True))
    assert not result["reached"] and result["commanded"] is None and sent == []  # a failed solve can be a very different pose
    assert result["next"].startswith("Nothing was commanded")


def test_move_must_be_a_boolean(runtime):
    advanced, stage, _state, _seen, _model = runtime
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.arm.ik", stage, dict(path="/Arm", link="hand", position=[0.5, 0.0, 0.2], move="yes"))
    assert error.value.code == "INVALID_VALUE"


def test_a_link_physics_does_not_report_skips_the_self_check_instead_of_refusing(runtime):
    advanced, stage, _state, seen, model = runtime
    seen["fail"] = AssetError("PHYSICS_NOT_READY", "PhysX has no initialized body at this path.")
    result = advanced.execute("sim.arm.ik", stage, dict(path="/Arm", link="hand", position=model.fk(np.array([0.2, 0.1, 0.1]))[:3, 3].tolist()))
    assert result["check"]["status"] == "skipped" and any("Self-check skipped" in n for n in result["notes"])


def test_a_model_that_disagrees_with_physics_is_refused(runtime):
    advanced, stage, _state, seen, _model = runtime
    seen["ee"] = dict(world_position=[0.9, 0.0, 0.2], orientation_wxyz=[1.0, 0.0, 0.0, 0.0])  # physics says the hand is elsewhere
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.arm.ik", stage, dict(path="/Arm", link="hand", position=[0.5, 0.0, 0.2]))
    assert error.value.code == "IK_MODEL_MISMATCH" and error.value.details["check"]["position_error"] > 0.1


def test_forward_kinematics_does_not_hide_an_invalid_simulation_state(arm_in_scene, monkeypatch):
    advanced, stage = arm_in_scene

    def broken(stage_, path):
        raise AssetError("PHYSICS_STATE_INVALID", "NaN joint positions.")

    monkeypatch.setattr(advanced, "joints", broken)
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.arm.fk", stage, dict(path="/Arm", link="hand"))
    assert error.value.code == "PHYSICS_STATE_INVALID"  # only "not running" may fall back to the authored pose


def test_a_target_in_the_base_frame_matches_the_world_frame(runtime):
    advanced, stage, _state, _seen, model = runtime
    target = model.fk(np.array([0.3, -0.4, 0.5]))
    world = advanced.execute("sim.arm.ik", stage, dict(path="/Arm", link="hand", position=target[:3, 3].tolist()))
    based = advanced.execute("sim.arm.ik", stage, dict(path="/Arm", link="hand", position=target[:3, 3].tolist(), frame="base"))
    assert world["reached"] and based["reached"]  # the base sits at the origin, so the two frames agree
    assert advanced.execute("sim.arm.ik", stage, dict(path="/Arm", link="hand", position=[0, 0, 0], frame="elsewhere")) if False else True
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.arm.ik", stage, dict(path="/Arm", link="hand", position=[0.4, 0.0, 0.2], frame="elsewhere"))
    assert error.value.code == "INVALID_FRAME"


def test_a_degree_of_freedom_the_simulation_lacks_is_refused(runtime):
    advanced, stage, state, _seen, _model = runtime
    state["names"] = ["j1", "j2"]
    state["positions"] = [0.0, 0.0]
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.arm.ik", stage, dict(path="/Arm", link="hand", position=[0.4, 0.0, 0.2]))
    assert error.value.code == "IK_DOF_MISSING"


def test_joints_sharing_a_name_in_one_chain_are_refused(advanced):
    stage = advanced.s.stage()
    UsdGeom.Xform.Define(stage, "/Arm")
    for name, at in (("base", (0, 0, 0)), ("link1", (0.3, 0, 0)), ("hand", (0.6, 0, 0))):
        _body(stage, f"/Arm/{name}", at)
    UsdGeom.Xform.Define(stage, "/Arm/a")
    UsdGeom.Xform.Define(stage, "/Arm/b")
    _joint(stage, "/Arm/a/j", "revolute", "/Arm/base", "/Arm/link1", (0.3, 0, 0), "Z")
    _joint(stage, "/Arm/b/j", "revolute", "/Arm/link1", "/Arm/hand", (0.3, 0, 0), "Z")
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.arm.fk", stage, dict(path="/Arm", link="hand"))
    assert error.value.code == "IK_DUPLICATE_JOINT"


def test_solve_applies_the_wrap_around_to_what_it_returns(arm, monkeypatch):
    stage, joints = arm
    chain = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    called = []
    monkeypatch.setattr(ik, "wrap_toward", lambda c, q, q_now: (called.append(1), q)[1])
    ik.solve(chain, np.zeros(4), chain.fk(np.array([0.2, 0.1, 0.1, 0.0])), use_orientation=False)
    assert called


# ---- Solver timeouts, candidate ranking and stage units ---------------------------------------------------------------------------------------------

def _fake_descents(monkeypatch, outcomes):
    """Replace the descent with scripted results (q, ok), and make the clock jump past the deadline after the last one."""
    clock = {"t": 0.0}
    calls = {"n": 0}

    def fake(chain, q0, target, use_orientation, pos_tol, rot_tol, iterations, deadline):
        q, ok = outcomes[min(calls["n"], len(outcomes) - 1)]
        calls["n"] += 1
        if calls["n"] >= len(outcomes):
            clock["t"] = 1e9
        return np.array(q, dtype=float), 1, ok

    monkeypatch.setattr(ik, "_descend", fake)
    monkeypatch.setattr(ik.time, "monotonic", lambda: clock["t"])


def test_a_success_followed_by_a_timeout_is_not_reported_as_timed_out(arm, monkeypatch):
    stage, joints = arm
    chain = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    _fake_descents(monkeypatch, [([0.0] * 4, False), ([0.1] * 4, True), ([0.0] * 4, False)])
    out = ik.solve(chain, np.zeros(4), np.eye(4), use_orientation=False)
    assert out["reached"] is True and out["timed_out"] is False
    _fake_descents(monkeypatch, [([0.0] * 4, False), ([0.0] * 4, False)])
    assert ik.solve(chain, np.zeros(4), np.eye(4), use_orientation=False)["timed_out"] is True  # nothing succeeded before the clock ran out


def test_candidates_are_ranked_after_whole_turns_are_removed(monkeypatch):
    """Two good answers on a continuous joint: one a whole turn out (6.58 rad, really 0.30), one at -0.40. From 0 the first is nearer."""
    chain = _continuous_chain()
    _fake_descents(monkeypatch, [([1.0], False), ([0.3 + 2 * math.pi], True), ([-0.4], True), ([2.0], False)])
    out = ik.solve(chain, np.zeros(1), np.eye(4), use_orientation=False)
    assert out["reached"] and out["q"][0] == pytest.approx(0.3, abs=1e-9)


def _scaled(chain, factor):
    """The same arm in stage units of 1/factor metres (factor 100: centimetres)."""
    steps = []
    for name, kind, axis, t0, t1_inv, reverse, lo, hi in chain.steps:
        a, b = t0.copy(), t1_inv.copy()
        a[:3, 3] *= factor
        b[:3, 3] *= factor
        steps.append((name, kind, axis, a, b, reverse, lo * (factor if kind == "prismatic" else 1), hi * (factor if kind == "prismatic" else 1)))
    out = ik.Chain(steps, np.array(chain.base_pose))
    out.base_pose[:3, 3] *= factor
    out.length_scale = float(factor)
    return out


def test_a_centimetre_stage_solves_to_the_same_poses(arm):
    """The riskiest case: on a centimetre stage positions are 100x larger than radians. The same arm, built in
    centimetres, must reach the same poses within a millimetre (0.1 cm) and a hundredth of a radian."""
    stage, joints = arm
    metres = ik.build_chain(stage, joints, "/Arm/base", "/Arm/hand")
    cm = _scaled(metres, 100)
    rng = np.random.default_rng(5)
    for _ in range(10):
        truth = rng.uniform(np.maximum(metres.lower, -2.0), np.minimum(metres.upper, 2.0))
        target = cm.fk(np.array([truth[0], truth[1], truth[2], truth[3] * 100]))
        out = ik.solve(cm, np.zeros(4), target, use_orientation=True, pos_tol=0.1, rot_tol=1e-2)
        assert out["reached"], out
        assert out["position_error"] <= 0.1 and out["orientation_error"] <= 1e-2  # centimetres and radians


def test_a_long_prismatic_joint_can_be_crossed_in_stage_units():
    """A rail 160 cm long: the step cap must scale with the unit, or 200 iterations cannot cover it."""
    step = ("rail", "prismatic", (1.0, 0.0, 0.0), np.eye(4), np.eye(4), False, -200.0, 200.0)
    chain = ik.Chain([step], np.eye(4))
    chain.length_scale = 100.0
    out = ik.solve(chain, np.zeros(1), pose_of(160.0, 0, 0), use_orientation=False, pos_tol=0.1)
    assert out["reached"] and out["q"][0] == pytest.approx(160.0, abs=0.2) and out["attempts"] == 1


def test_a_link_beyond_a_truncated_scan_is_named_as_such(arm_in_scene, monkeypatch):
    advanced, stage = arm_in_scene
    from sim_tools import arm_tools as module

    first = [stage.GetPrimAtPath("/Arm/j1")]
    monkeypatch.setattr(module, "articulation_joints", lambda stage_, root, limit: (first, True))
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.arm.fk", stage, dict(path="/Arm", link="/Arm/hand"))
    assert error.value.code == "NOT_FOUND" and "stopped at its limit" in str(error.value)

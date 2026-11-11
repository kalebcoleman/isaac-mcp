"""sim.body.state compares the physics pose with the pose the renderer draws."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import Gf, UsdGeom

from isaac_mcp.project_assets import AssetError
from sim_tools import physics
from test_scene_usd import scene  # noqa: F401 - fixture

HALF_TURN_Z = [0.0, 0.0, 0.0, 1.0]
IDENTITY = [1.0, 0.0, 0.0, 0.0]


def test_pose_gap_is_a_distance_and_an_angle():
    assert physics.pose_gap([0, 0, 0], IDENTITY, [3, 4, 0], IDENTITY) == pytest.approx((5.0, 0.0))
    assert physics.pose_gap([0, 0, 0], IDENTITY, [0, 0, 0], HALF_TURN_Z)[1] == pytest.approx(180.0)
    assert physics.pose_gap([0, 0, 0], IDENTITY, [0, 0, 0], [-1.0, 0, 0, 0])[1] == pytest.approx(0.0), "q and -q are one rotation"
    quarter = [2 ** -0.5, 0, 0, 2 ** -0.5]
    assert physics.pose_gap([1, 1, 1], quarter, [1, 1, 1], IDENTITY)[1] == pytest.approx(90.0)


def place(scene, position, orientation=IDENTITY):  # noqa: F811
    stage = scene.stage()
    scene.create(stage, "/World/Body", "Cube", position=position, orientation=orientation, rigid=True)
    return stage


def test_a_rendered_pose_that_matches_physics_is_reported_without_a_warning(scene):  # noqa: F811
    stage = place(scene, [1, 2, 3], [2 ** -0.5, 0, 0, 2 ** -0.5])
    report, warning = physics.render_check(stage, "/World/Body", [1.0, 2.0, 3.001], [2 ** -0.5, 0, 0, 2 ** -0.5])
    assert warning is None and report == {"source": "USD", "agrees": True, "position_gap": pytest.approx(0.001, abs=1e-6),
                                           "angle_gap_deg": pytest.approx(0.0, abs=1e-3), "checked": ["USD"]}


def test_a_body_drawn_somewhere_else_is_a_warning_that_says_what_to_check(scene):  # noqa: F811
    stage = place(scene, [0, 0, 0])
    report, warning = physics.render_check(stage, "/World/Body", [0.0, 0.0, 2.0], IDENTITY)
    assert report["agrees"] is False and report["position_gap"] == pytest.approx(2.0)
    assert "/World/Body" in warning and "do not show where the body is" in warning and "Fabric updates" in warning
    assert "closest of USD" in warning
    turned, warning = physics.render_check(stage, "/World/Body", [0.0, 0.0, 0.0], HALF_TURN_Z)
    assert turned["agrees"] is False and turned["angle_gap_deg"] == pytest.approx(180.0) and warning


def test_the_tolerance_is_five_millimetres_in_the_stage_s_own_units(scene):  # noqa: F811
    stage = place(scene, [0, 0, 0])
    assert physics.render_check(stage, "/World/Body", [0.0, 0.0, 0.004], IDENTITY)[0]["agrees"] is True
    assert physics.render_check(stage, "/World/Body", [0.0, 0.0, 0.006], IDENTITY)[0]["agrees"] is False
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)  # centimetres: 0.4 units is 4 mm
    assert physics.render_check(stage, "/World/Body", [0.0, 0.0, 0.4], IDENTITY)[0]["agrees"] is True
    assert physics.render_check(stage, "/World/Body", [0.0, 0.0, 0.6], IDENTITY)[0]["agrees"] is False


def test_the_picture_is_right_when_either_fabric_or_usd_holds_the_physics_pose(scene, monkeypatch):  # noqa: F811
    stage = place(scene, [0, 0, 0])
    monkeypatch.setattr(physics, "_fabric_pose", lambda path: ([0.0, 0.0, 2.0], IDENTITY))
    report, warning = physics.render_check(stage, "/World/Body", [0.0, 0.0, 2.0], IDENTITY)
    assert report["source"] == "Fabric" and report["agrees"] is True and warning is None, "Newton: USD stays at the authored pose"
    report, warning = physics.render_check(stage, "/World/Body", [0.0, 0.0, 0.0], IDENTITY)
    assert report["source"] == "USD" and report["agrees"] is True and warning is None, "PhysX writing USD: an idle Fabric copy is not a fault"
    report, warning = physics.render_check(stage, "/World/Body", [0.0, 0.0, 5.0], IDENTITY)
    assert report["agrees"] is False and report["source"] == "Fabric" and report["position_gap"] == pytest.approx(3.0)
    assert report["checked"] == ["Fabric", "USD"] and "closest of Fabric and USD" in warning


def test_a_prim_with_no_pose_gives_no_report(scene):  # noqa: F811
    assert physics.render_check(scene.stage(), "/World/Missing", [0, 0, 0], IDENTITY) == (None, None)
    assert physics._fabric_pose("/World/Body") is None, "no usdrt outside Kit: the caller falls back to USD"


def test_a_scaled_parent_does_not_look_like_a_rotation(scene):  # noqa: F811
    stage = scene.stage()
    parent = UsdGeom.Xform.Define(stage, "/World/Rig")
    parent.AddTranslateOp().Set(Gf.Vec3d(1, 0, 0))
    parent.AddScaleOp().Set(Gf.Vec3f(2, 2, 2))
    UsdGeom.Xform.Define(stage, "/World/Rig/Body").AddTranslateOp().Set(Gf.Vec3d(1, 0, 0))
    report, warning = physics.render_check(stage, "/World/Rig/Body", [3.0, 0.0, 0.0], IDENTITY)
    assert report["agrees"] is True and warning is None


def test_physics_ready_distinguishes_ready_not_yet_and_cannot_say(monkeypatch):
    view = {"value": None}
    monkeypatch.setattr(physics, "manager", lambda: SimpleNamespace(get_physics_simulation_view=lambda: view["value"]))
    assert physics.physics_ready() is False
    view["value"] = object()
    assert physics.physics_ready() is True

    def refuse():
        raise AssetError("PHYSICS_API_UNAVAILABLE", "no simulation manager")

    monkeypatch.setattr(physics, "manager", refuse)
    assert physics.physics_ready() is None

    def broken():
        raise RuntimeError("view not created")

    monkeypatch.setattr(physics, "manager", lambda: SimpleNamespace(get_physics_simulation_view=broken))
    assert physics.physics_ready() is False

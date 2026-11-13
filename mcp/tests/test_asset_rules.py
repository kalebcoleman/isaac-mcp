"""asset_rules.validate: the rule table on a generated asset with known faults, before and after fixing them."""

from __future__ import annotations

import shutil

import pytest

pytest.importorskip("pxr")
pytest.importorskip("trimesh")

import mesh_fixture  # noqa: E402

from twinsmith import asset_rules, mesh_usd  # noqa: E402


def failed(report):
    return {c["id"] for c in report["checks"] if not c["ok"]}


@pytest.fixture
def asset(tmp_path):
    path = tmp_path / "src" / "cell.usdc"
    path.parent.mkdir()
    mesh_fixture.build(path)
    return path


def test_every_rule_has_levels_evidence_and_a_fix():
    for rule, spec in asset_rules.RULES.items():
        assert spec["newton"] in ("error", "warning") and spec["physx"] in ("error", "warning", "info")
        assert spec["rule"] and spec["evidence"] and asset_rules.FIXES[rule]


def test_fixture_fails_on_newton_and_passes_after_split_and_fixes(asset, tmp_path):
    report = asset_rules.validate(asset, "newton", triangle_budget=5000)
    assert not report["ok"]
    assert {"TRI_COLLIDER", "DRIVE_ZERO_FORCE", "NESTED_ARTICULATION"} <= failed(report)
    by_id = {c["id"]: c for c in report["checks"] if not c["ok"]}
    assert by_id["DRIVE_ZERO_FORCE"]["prim"] == "/World/Robot/joint1" and by_id["DRIVE_ZERO_FORCE"]["fix"]
    assert by_id["NESTED_ARTICULATION"]["prim"] == "/World/Robot/Link1/Gripper"

    fixed = tmp_path / "fixed" / "cell.usdc"
    fixed.parent.mkdir()
    shutil.copy(asset, fixed)
    mesh_fixture.remove_faults(fixed)
    out = tmp_path / "split"
    out.mkdir()
    composed = mesh_usd.split(fixed, out, collision=dict(method="box"))["output"]
    after = asset_rules.validate(composed, "newton", triangle_budget=5000)
    assert after["ok"], [c for c in after["checks"] if not c["ok"]]
    assert {c["id"] for c in after["checks"]} == set(asset_rules.RULES)


def test_physx_reports_newton_only_rules_as_info(asset):
    report = asset_rules.validate(asset, "physx", triangle_budget=5000)
    levels = {c["id"]: c["level"] for c in report["checks"] if not c["ok"]}
    assert levels["TRI_COLLIDER"] == levels["DRIVE_ZERO_FORCE"] == levels["NESTED_ARTICULATION"] == "info"
    assert report["ok"]


def test_units_mass_gain_and_loop_rules(tmp_path):
    from pxr import Usd, UsdGeom, UsdPhysics

    path = tmp_path / "bare.usda"
    stage = Usd.Stage.CreateNew(str(path))
    body = UsdGeom.Xform.Define(stage, "/A/Body")
    UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())              # moving, no mass, no collider
    other = UsdGeom.Xform.Define(stage, "/A/Other")
    UsdPhysics.RigidBodyAPI.Apply(other.GetPrim())
    UsdPhysics.MassAPI.Apply(other.GetPrim()).CreateMassAttr(1.0)
    joint = UsdPhysics.RevoluteJoint.Define(stage, "/A/j")
    joint.CreateBody0Rel().SetTargets([body.GetPath()])
    joint.CreateBody1Rel().SetTargets([other.GetPath()])
    joint.CreateExcludeFromArticulationAttr(True)
    UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "angular").CreateStiffnessAttr(9400.0)
    stage.GetRootLayer().Save()
    report = asset_rules.validate(path, "newton")
    assert {"UNITS", "MASS_MIN", "DRIVE_GAIN", "LOOP_JOINT"} <= failed(report)
    gain = next(c for c in report["checks"] if c["id"] == "DRIVE_GAIN" and not c["ok"])
    assert "2 / dt = 1000" in gain["detail"] and gain["level"] == "warning"


def test_a_collision_shape_that_renders_is_a_warning(tmp_path):
    from pxr import Usd, UsdGeom, UsdPhysics

    path = tmp_path / "shape.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, "Z")
    cube = UsdGeom.Cube.Define(stage, "/W/part_collision/box")
    UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    stage.GetRootLayer().Save()
    report = asset_rules.validate(path)
    assert failed(report) == {"COLLIDER_RENDERED"} and report["ok"]


def test_engine_is_checked(asset):
    with pytest.raises(ValueError):
        asset_rules.validate(asset, "mujoco")

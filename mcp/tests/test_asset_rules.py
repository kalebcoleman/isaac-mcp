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


def test_units_mass_gain_and_mimic_rules(tmp_path):
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics

    path = tmp_path / "bare.usda"
    stage = Usd.Stage.CreateNew(str(path))
    body = UsdGeom.Xform.Define(stage, "/A/Body")
    UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())              # moving, no mass, no collider
    other = UsdGeom.Xform.Define(stage, "/A/Other")
    UsdPhysics.RigidBodyAPI.Apply(other.GetPrim())
    UsdPhysics.MassAPI.Apply(other.GetPrim()).CreateMassAttr(1.0)   # mass but no inertia and no collider
    leader = UsdPhysics.RevoluteJoint.Define(stage, "/A/leader")
    leader.CreateBody0Rel().SetTargets([body.GetPath()])
    leader.CreateBody1Rel().SetTargets([other.GetPath()])
    UsdPhysics.DriveAPI.Apply(leader.GetPrim(), "angular").CreateStiffnessAttr(9400.0)
    loop = UsdPhysics.RevoluteJoint.Define(stage, "/A/loop")
    loop.CreateBody0Rel().SetTargets([body.GetPath()])
    loop.CreateBody1Rel().SetTargets([other.GetPath()])
    loop.CreateExcludeFromArticulationAttr(True)
    loop.GetPrim().AddAppliedSchema("NewtonMimicAPI")          # a mimic on a loop joint: PhysX rejects it
    loop.GetPrim().CreateRelationship("newton:mimicJoint").SetTargets([leader.GetPath()])
    stage.GetRootLayer().Save()
    report = asset_rules.validate(path, "newton")
    assert {"UNITS", "MASS_MIN", "DRIVE_GAIN", "MIMIC"} <= failed(report)
    assert {c["prim"] for c in report["checks"] if c["id"] == "MASS_MIN" and not c["ok"]} == {"/A/Body", "/A/Other"}
    gain = next(c for c in report["checks"] if c["id"] == "DRIVE_GAIN" and not c["ok"])
    assert "armature 0.1" in gain["detail"] and "500 Hz" in gain["detail"] and gain["level"] == "warning"
    mimic = next(c for c in report["checks"] if c["id"] == "MIMIC")["detail"]
    assert "excluded" in mimic and "follower is a revolute joint without finite limits" in mimic
    # Armature and damping make the same stiffness stable.
    leader.GetPrim().CreateAttribute("newton:armature", Sdf.ValueTypeNames.Float).Set(5.0)
    stage.GetRootLayer().Save()
    assert "DRIVE_GAIN" not in failed(asset_rules.validate(path, "newton"))


def test_an_excluded_loop_joint_without_a_mimic_is_valid(asset, tmp_path):
    from pxr import Usd, UsdPhysics

    stage = Usd.Stage.Open(str(asset))
    j = UsdPhysics.RevoluteJoint.Define(stage, "/World/Robot/loop")
    j.CreateBody0Rel().SetTargets(["/World/Robot/Link0"])
    j.CreateBody1Rel().SetTargets(["/World/Robot/Link1"])
    j.CreateExcludeFromArticulationAttr(True)
    stage.GetRootLayer().Save()
    assert "MIMIC" not in failed(asset_rules.validate(asset, "newton", triangle_budget=10**7))


def test_a_triangle_mesh_on_a_moving_body_is_flagged(tmp_path):
    from pxr import Usd, UsdGeom, UsdPhysics

    path = tmp_path / "dyn.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, "Z")
    body = UsdGeom.Xform.Define(stage, "/B")
    UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
    mesh = UsdGeom.Mesh.Define(stage, "/B/m")
    mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)])
    mesh.CreateFaceVertexCountsAttr([3, 3, 3, 3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 0, 3, 1, 0, 2, 3, 1, 3, 2])
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    other = UsdGeom.Mesh.Define(stage, "/B/only_mesh_collision_api")       # MeshCollisionAPI alone is no collider
    UsdPhysics.MeshCollisionAPI.Apply(other.GetPrim()).CreateApproximationAttr("none")
    stage.GetRootLayer().Save()
    report = asset_rules.validate(path, "physx")
    dynamic = [c for c in report["checks"] if c["id"] == "TRI_DYNAMIC" and not c["ok"]]
    assert [c["prim"] for c in dynamic] == ["/B/m"] and dynamic[0]["level"] == "warning"


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

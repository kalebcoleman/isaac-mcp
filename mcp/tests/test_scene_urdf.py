"""Real USD regression for native importer Physics variant output."""

import pytest
import json
import sys
from xml.etree import ElementTree
from types import SimpleNamespace
from test_scene_usd import scene  # registers the extension package path
from pxr import Gf, Usd, UsdGeom, UsdPhysics
from isaac_mcp import urdf_physics, urdf_refs
from isaac_mcp.project_assets import AssetError, Broker
from sim_tools.advanced import Advanced
from sim_tools.urdf import (
    _author_drives,
    ground_height,
    lowest_point,
    place_above_ground,
    prepare_generated_physics,
    repair_meshes_for_legacy_importer,
)


def make_output(
    tmp_path, selection, variants=("none", "physics"), bodies=True, joint=False
):
    payload_path = tmp_path / "physics.usda"
    payload = Usd.Stage.CreateNew(str(payload_path))
    robot = payload.DefinePrim("/Robot", "Xform")
    payload.SetDefaultPrim(robot)
    link = payload.DefinePrim("/Robot/Link", "Xform")
    if bodies:
        UsdPhysics.RigidBodyAPI.Apply(link)
        UsdPhysics.MassAPI.Apply(link).CreateMassAttr(1)
        UsdPhysics.ArticulationRootAPI.Apply(link)
    UsdPhysics.CollisionAPI.Apply(
        UsdGeom.Cube.Define(payload, "/Robot/Link/Box").GetPrim()
    )
    if joint:
        UsdPhysics.RevoluteJoint.Define(payload, "/Robot/Hinge")
    payload.GetRootLayer().Save()
    path = tmp_path / "robot.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    root = stage.DefinePrim("/Robot", "Xform")
    stage.SetDefaultPrim(root)
    if variants:
        v = root.GetVariantSets().AddVariantSet("Physics")
        for name in variants:
            v.AddVariant(name)
            v.SetVariantSelection(name)
            if name != "none":
                with v.GetVariantEditContext():
                    root.GetPayloads().AddPayload("./physics.usda")
        v.SetVariantSelection(selection)
    else:
        root.GetReferences().AddReference("./physics.usda")
    stage.GetRootLayer().Save()
    return path, payload_path


@pytest.mark.parametrize("selection", ["", "none", "physx", "physics"])
def test_select_shared_physics_without_touching_payload_or_live_stage(
    tmp_path, scene, selection
):
    path, payload = make_output(tmp_path, selection, joint=True)
    payload_before = payload.read_bytes()
    live = scene.stage()
    live_before = [l.ExportToString() for l in live.GetUsedLayers()]
    result = prepare_generated_physics(path, tmp_path, movable_joints=1)
    assert result["selected_before"] == selection and result["selected"] == "physics"
    assert result["authored"] == dict(
        rigid_bodies=1,
        collisions=1,
        joints=1,
        movable_joints=1,
        articulations=1,
        authored_masses=1,
    )
    assert "unknown" in result["runtime_readiness"]
    reopened = Usd.Stage.Open(str(path))
    assert (
        reopened.GetDefaultPrim().GetVariantSet("Physics").GetVariantSelection()
        == "physics"
    )
    assert reopened.GetPrimAtPath("/Robot/Link").HasAPI(UsdPhysics.RigidBodyAPI)
    assert payload.read_bytes() == payload_before
    assert [l.ExportToString() for l in live.GetUsedLayers()] == live_before


def test_flat_output_remains_byte_identical(tmp_path):
    path, _ = make_output(tmp_path, "", variants=())
    before = path.read_bytes()
    result = prepare_generated_physics(path, tmp_path)
    assert result["variant_set"] is None and result["authored"]["rigid_bodies"] == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "reason", ["unknown_variant", "no_body", "missing_joint", "missing_payload"]
)
def test_invalid_native_output_refused_before_persisting(tmp_path, reason):
    path, payload = make_output(
        tmp_path,
        "none",
        variants=("none", "physx")
        if reason == "unknown_variant"
        else ("none", "physics"),
        bodies=reason != "no_body",
    )
    if reason == "missing_payload":
        payload.unlink()
    before = path.read_bytes()
    with pytest.raises(AssetError) as error:
        prepare_generated_physics(
            path, tmp_path, movable_joints=int(reason == "missing_joint")
        )
    assert error.value.code == "URDF_PHYSICS"
    assert path.read_bytes() == before


def test_output_escape_and_symlink_refused_without_writes(tmp_path):
    outside = tmp_path / "outside"
    inside = tmp_path / "private"
    outside.mkdir()
    inside.mkdir()
    path, _ = make_output(outside, "none")
    before = path.read_bytes()
    for entry in (path, inside / "link.usda"):
        if entry != path:
            entry.symlink_to(path)
        with pytest.raises(AssetError, match="private directory"):
            prepare_generated_physics(entry, inside)
    assert path.read_bytes() == before


@pytest.mark.parametrize("converted_body", [True, False])
def test_conversion_validates_before_broker_snapshot_and_scene_composition(
    scene, tmp_path, monkeypatch, converted_body
):
    project = tmp_path / "project"
    project.mkdir()
    urdf = project / "minimal.urdf"
    urdf.write_text('<robot name="fixture"><link name="base"/></robot>')
    source_before = urdf.read_bytes()
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    config_file = tmp_path / "approval.json"
    config_file.write_text(
        json.dumps(
            dict(
                version=1,
                profile="trusted_project",
                roots={"fixture": str(project)},
                staging=str(staging),
            )
        )
    )
    scene.broker = Broker(str(config_file))
    snapshot = scene.broker.snapshot("fixture", "minimal.urdf", kind="urdf")

    class Converter:
        def __init__(self, config):
            self.config = config

        def import_urdf(self):
            from pathlib import Path

            output = Path(self.config.usd_path)
            output.mkdir()
            path, _ = make_output(output, "physx", bodies=converted_body)
            return str(path)

    monkeypatch.setitem(
        sys.modules,
        "isaacsim.asset.importer.urdf",
        SimpleNamespace(URDFImporterConfig=SimpleNamespace, URDFImporter=Converter),
    )
    stage = scene.stage()
    root_before = stage.GetRootLayer().ExportToString()
    session_before = stage.GetSessionLayer().ExportToString()
    args = dict(
        snapshot_id=snapshot["snapshot_id"],
        manifest_sha256=snapshot["manifest_sha256"],
        prim_path="/World/Imported",
    )
    if converted_body:
        result = Advanced(scene, None).import_urdf(stage, **args)
        assert result["converted_physics"]["selected"] == "physics"
        imported = stage.GetPrimAtPath("/World/Imported/Asset/Link")
        assert imported.HasAPI(UsdPhysics.RigidBodyAPI)
        assert imported.GetAttribute("physics:mass").Get() == 1
        filename, _ = scene.broker.verify(
            result["snapshot_id"], result["manifest_sha256"]
        )
        assert (
            Usd.Stage.Open(str(filename))
            .GetDefaultPrim()
            .GetVariantSet("Physics")
            .GetVariantSelection()
            == "physics"
        )
    else:
        with pytest.raises(AssetError) as error:
            Advanced(scene, None).import_urdf(stage, **args)
        assert error.value.code == "URDF_PHYSICS"
        assert not stage.GetPrimAtPath("/World/Imported")
        assert stage.GetSessionLayer().ExportToString() == session_before
        assert len(list(staging.iterdir())) == 1
    assert stage.GetRootLayer().ExportToString() == root_before
    assert urdf.read_bytes() == source_before


def physics_output(tmp_path, stiffness=0.0, force=None, anchored=True):
    """Converted output with one hinge, one prismatic joint and a world joint, in a variant payload."""
    payload_path = tmp_path / "physics.usda"
    payload = Usd.Stage.CreateNew(str(payload_path))
    root = payload.DefinePrim("/Robot", "Xform")
    payload.SetDefaultPrim(root)
    for name in ("Base", "Arm", "Slide"):
        UsdPhysics.RigidBodyAPI.Apply(payload.DefinePrim(f"/Robot/{name}", "Xform"))
    hinge = UsdPhysics.RevoluteJoint.Define(payload, "/Robot/my_hinge")
    hinge.CreateBody0Rel().SetTargets(["/Robot/Base"])
    hinge.CreateBody1Rel().SetTargets(["/Robot/Arm"])
    drive = UsdPhysics.DriveAPI.Apply(hinge.GetPrim(), "angular")
    drive.CreateStiffnessAttr(stiffness)
    drive.CreateDampingAttr(0.0)
    if force is not None:
        drive.CreateMaxForceAttr(force)
    slide = UsdPhysics.PrismaticJoint.Define(payload, "/Robot/slide")
    slide.CreateBody0Rel().SetTargets(["/Robot/Base"])
    slide.CreateBody1Rel().SetTargets(["/Robot/Slide"])
    if anchored:
        anchor = UsdPhysics.FixedJoint.Define(payload, "/Robot/root_joint")
        anchor.CreateBody0Rel().SetTargets(["/Robot"])
        anchor.CreateBody1Rel().SetTargets(["/Robot/Base"])
    payload.GetRootLayer().Save()
    path = tmp_path / "robot.usda"
    stage = Usd.Stage.CreateNew(str(path))
    robot = stage.DefinePrim("/Robot", "Xform")
    stage.SetDefaultPrim(robot)
    variants = robot.GetVariantSets().AddVariantSet("Physics")
    variants.AddVariant("physics")
    variants.SetVariantSelection("physics")
    with variants.GetVariantEditContext():
        robot.GetPayloads().AddPayload("./physics.usda")
    stage.GetRootLayer().Save()
    return path, payload_path


GAINS = {
    "my-hinge": dict(stiffness=180.0, damping=18.0, load=1.0, inertia=1.0, linear=False),
    "slide": dict(stiffness=50.0, damping=5.0, load=1.0, inertia=1.0, linear=True),
}


OPEN_STAGES = []


def drive_of(path, joint, kind):
    OPEN_STAGES.append(Usd.Stage.Open(str(path)))  # a prim does not keep its stage open
    return UsdPhysics.DriveAPI(OPEN_STAGES[-1].GetPrimAtPath(f"/Robot/{joint}"), kind)


def test_default_drives_override_zero_gains_in_generated_output_only(tmp_path):
    path, payload = physics_output(tmp_path)
    before = payload.read_bytes()
    report = prepare_generated_physics(path, tmp_path, gains=GAINS)
    assert payload.read_bytes() == before
    assert set(report["drives"]["authored"]) == {"my-hinge", "slide"}
    # Angular gains are per degree in USD; prismatic ones per stage unit.
    assert drive_of(path, "my_hinge", "angular").GetStiffnessAttr().Get() == pytest.approx(3.14159265)
    assert drive_of(path, "my_hinge", "angular").GetDampingAttr().Get() == pytest.approx(0.314159265)
    assert drive_of(path, "slide", "linear").GetStiffnessAttr().Get() == pytest.approx(50.0)


def test_a_stiffness_in_the_output_is_kept_on_6x(tmp_path, monkeypatch):
    monkeypatch.setattr("sim_tools.urdf.uses_legacy_importer", lambda: False)
    path, _ = physics_output(tmp_path, stiffness=7.0)
    report = prepare_generated_physics(path, tmp_path, gains=GAINS)
    assert report["drives"]["kept_from_importer"] == ["my-hinge"]
    assert drive_of(path, "my_hinge", "angular").GetStiffnessAttr().Get() == pytest.approx(7.0)


def test_the_5x_importers_invented_stiffness_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr("sim_tools.urdf.uses_legacy_importer", lambda: True)
    path, _ = physics_output(tmp_path, stiffness=7.0)
    report = prepare_generated_physics(path, tmp_path, gains=GAINS)
    assert report["drives"]["kept_from_importer"] == [] and "my-hinge" in report["drives"]["authored"]
    assert drive_of(path, "my_hinge", "angular").GetStiffnessAttr().Get() == pytest.approx(3.14159265)


def test_a_zero_force_limit_does_not_disable_the_drive(tmp_path):
    path, _ = physics_output(tmp_path, force=0.0)
    report = prepare_generated_physics(path, tmp_path, gains=GAINS)
    assert report["drives"]["zero_force_limit_removed"] == ["my-hinge"]
    assert drive_of(path, "my_hinge", "angular").GetMaxForceAttr().Get() == float("inf")


def test_without_default_drives_nothing_is_authored(tmp_path):
    path, _ = physics_output(tmp_path)
    report = prepare_generated_physics(path, tmp_path)
    assert "drives" not in report
    assert drive_of(path, "my_hinge", "angular").GetStiffnessAttr().Get() == 0.0


@pytest.mark.parametrize("wanted,anchored,base", [(True, True, "fixed"), (None, True, "fixed"), (False, True, "floating"), (False, False, "floating")])
def test_the_base_report_says_what_composed(tmp_path, wanted, anchored, base):
    path, payload = physics_output(tmp_path, anchored=anchored)
    before = payload.read_bytes()
    assert prepare_generated_physics(path, tmp_path, fixed_base=wanted)["base"] == base
    assert payload.read_bytes() == before


def test_a_5x_physx_variant_is_used_when_it_is_the_only_shared_one(tmp_path):
    path, _ = make_output(tmp_path, "None", variants=("None", "PhysX"))
    report = prepare_generated_physics(path, tmp_path)
    assert report["selected"] == "PhysX" and report["authored"]["rigid_bodies"] == 1


def test_a_reference_to_a_visuals_prim_never_written_is_removed(tmp_path):
    """The 5.x importer references a visuals prim for each link, even one it did not write."""
    base = tmp_path / "base.usda"
    other = Usd.Stage.CreateNew(str(base))
    other.DefinePrim("/visuals/has_mesh", "Xform")
    other.GetRootLayer().Save()
    path, _ = make_output(tmp_path, "", variants=())
    stage = Usd.Stage.Open(str(path))
    stage.DefinePrim("/Robot/keep").GetReferences().AddReference("./base.usda", "/visuals/has_mesh")
    stage.DefinePrim("/Robot/empty").GetReferences().AddReference("./base.usda", "/visuals/absent")
    stage.GetRootLayer().Save()
    report = prepare_generated_physics(path, tmp_path)
    assert report["empty_references_removed"] == ["/Robot/empty"]
    reopened = Usd.Stage.Open(str(path))
    assert reopened.GetPrimAtPath("/Robot/keep").HasAuthoredReferences()
    assert not reopened.GetPrimAtPath("/Robot/empty").HasAuthoredReferences()


def ground_stage(plane_z=0.0):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.Xform.Define(stage, "/World")
    plane = UsdGeom.Plane.Define(stage, "/World/Ground")
    plane.AddTranslateOp().Set((0, 0, plane_z))
    UsdPhysics.CollisionAPI.Apply(plane.GetPrim())
    wrapper = UsdGeom.Xform.Define(stage, "/World/R")
    wrapper.AddTransformOp().Set(Gf.Matrix4d(1))
    cube = UsdGeom.Cube.Define(stage, "/World/R/Body")
    cube.CreateSizeAttr(1.0)
    cube.CreateExtentAttr([(-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)])
    return stage


def test_a_robot_under_the_ground_is_lifted_onto_it():
    stage = ground_stage()
    assert ground_height(stage) == 0.0 and lowest_point(stage, "/World/R") == pytest.approx(-0.5)
    report = place_above_ground(stage, "/World/R", False)
    assert report["lifted_by"] == pytest.approx(0.505) and "raised" in report["note"]
    assert lowest_point(stage, "/World/R") == pytest.approx(0.005)
    assert place_above_ground(stage, "/World/R", False)["lifted_by"] == 0.0


def test_a_chosen_position_is_kept_with_a_warning():
    stage = ground_stage()
    report = place_above_ground(stage, "/World/R", True)
    assert report["lifted_by"] == 0.0 and "below the ground" in report["warning"]
    assert lowest_point(stage, "/World/R") == pytest.approx(-0.5)


def test_a_raised_ground_counts():
    stage = ground_stage(plane_z=2.0)
    assert place_above_ground(stage, "/World/R", False)["lifted_by"] == pytest.approx(2.505)


def test_no_ground_means_no_lift():
    stage = ground_stage()
    stage.RemovePrim("/World/Ground")
    assert place_above_ground(stage, "/World/R", False) == dict(ground_height=None, lowest_point=pytest.approx(-0.5), lifted_by=0.0)


def test_a_dae_without_a_unit_is_repaired_in_a_private_copy_with_its_textures(tmp_path):
    project_dir = tmp_path / "project"
    (project_dir / "m").mkdir(parents=True)
    source = project_dir / "m" / "a.dae"
    source.write_text('<COLLADA xmlns="c"><asset></asset><library_images><image id="i"><init_from>t.png</init_from></image></library_images></COLLADA>')
    (project_dir / "m" / "t.png").write_text("pixels")
    (project_dir / "m" / "b.dae").write_text('<COLLADA><asset><unit meter="1"/></asset></COLLADA>')
    before = source.read_bytes()
    project = urdf_refs.project_from_folder(project_dir)
    mapped, repaired = repair_meshes_for_legacy_importer({"x": "m/a.dae", "y": "m/b.dae", "z": "m/t.png"}, project, tmp_path / "fixed")
    assert repaired == ["m/a.dae"] and source.read_bytes() == before
    assert mapped["x"] == str(tmp_path / "fixed" / "m/a.dae") and mapped["y"] == "m/b.dae" and mapped["z"] == "m/t.png"
    assert b"<unit" in (tmp_path / "fixed" / "m/a.dae").read_bytes()
    assert (tmp_path / "fixed" / "m/t.png").read_text() == "pixels"


def test_rewritten_accepts_absolute_repaired_paths(tmp_path):
    tree = urdf_refs.parse(b'<robot name="r"><link name="a"><visual><geometry><mesh filename="m.dae"/></geometry></visual></link></robot>')
    data = urdf_refs.rewritten(tree, {"m.dae": "/private/m.dae"}, tmp_path)
    assert b'filename="/private/m.dae"' in data


def fixture_project(tmp_path, scene, text, files=()):
    project = tmp_path / "project"
    (project / "urdf").mkdir(parents=True)
    source = project / "urdf" / "r.urdf"
    source.write_text(text)
    for rel, body in files:
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    config_file = tmp_path / "approval.json"
    config_file.write_text(json.dumps(dict(version=1, profile="trusted_project", roots={"fixture": str(project)}, staging=str(staging))))
    scene.broker = Broker(str(config_file))
    return source, scene.broker.snapshot("fixture", "urdf/r.urdf", kind="urdf")


class RecordingConverter:
    seen = []

    def __init__(self, config):
        self.config = config
        RecordingConverter.seen.append(config)

    def import_urdf(self):
        from pathlib import Path

        output = Path(self.config.usd_path)
        output.mkdir()
        return str(make_output(output, "physx", joint=True)[0])


def use_converter(monkeypatch):
    RecordingConverter.seen = []
    monkeypatch.setitem(sys.modules, "isaacsim.asset.importer.urdf",
                        SimpleNamespace(URDFImporterConfig=SimpleNamespace, URDFImporter=RecordingConverter))


JOINT = '<joint name="Hinge" type="revolute"><parent link="base"/><child link="tip"/><limit effort="5" lower="-1" upper="1" velocity="1"/></joint>'


def test_import_rewrites_a_private_copy_and_reports_the_mapping(scene, tmp_path, monkeypatch):
    use_converter(monkeypatch)
    text = ('<robot name="r"><link name="base"><visual><geometry><mesh filename="package://pkg/m.stl"/></geometry></visual></link>'
            f'<link name="tip"/>{JOINT}</robot>')
    source, snapshot = fixture_project(tmp_path, scene, text, [("pkg/package.xml", "<package><name>pkg</name></package>"), ("pkg/m.stl", "mesh")])
    stage = scene.stage()
    args = dict(snapshot_id=snapshot["snapshot_id"], manifest_sha256=snapshot["manifest_sha256"], prim_path="/World/R")
    result = Advanced(scene, None).import_urdf(stage, **args)
    assert result["packages"] == {"pkg": dict(folder="pkg", found_by="package.xml")} and result["mapped_files"] == 1
    assert result["base"] == "floating"  # the fixture has no world joint
    assert result["articulation_root"] == "/World/R/Asset/Link"
    assert any("base is floating" in w for w in result["warnings"])
    assert source.read_text() == text
    config = RecordingConverter.seen[0]
    rewritten = open(config.urdf_path).read()
    assert "package://" not in rewritten and f'{snapshot["snapshot_id"]}/project/pkg/m.stl' in rewritten
    # Verified project files did not change, so the snapshot still verifies.
    scene.broker.verify(snapshot["snapshot_id"], snapshot["manifest_sha256"], kind="urdf")
    # A second import reuses the conversion instead of growing the snapshot.
    Advanced(scene, None).import_urdf(stage, **{**args, "prim_path": "/World/R2"})
    assert len(RecordingConverter.seen) == 1


@pytest.mark.parametrize("text,code", [
    ('<robot name="r"><link name="a"><visual><geometry><mesh filename="package://none/m.stl"/></geometry></visual></link></robot>', "PACKAGE_UNRESOLVED"),
    ('<robot name="r"><link name="a"><visual><geometry><mesh filename="../../secret.stl"/></geometry></visual></link></robot>', "OUTSIDE_ROOT"),
    ('<robot name="r"><link name="a"><visual><geometry><mesh filename="gone.stl"/></geometry></visual></link></robot>', "MESH_MISSING"),
    ('<robot name="r"><link name="a"><visual><geometry><mesh filename="https://x.test/m.stl"/></geometry></visual></link></robot>', "UNSUPPORTED_URI"),
    ('<robot name="r" xmlns:xacro="http://www.ros.org/wiki/xacro"><xacro:include filename="x"/></robot>', "XACRO"),
])
def test_import_refuses_before_converting(scene, tmp_path, monkeypatch, text, code):
    use_converter(monkeypatch)
    source, snapshot = fixture_project(tmp_path, scene, text)
    stage = scene.stage()
    with pytest.raises(AssetError) as error:
        Advanced(scene, None).import_urdf(stage, snapshot["snapshot_id"], snapshot["manifest_sha256"], "/World/R")
    assert error.value.code == code and not RecordingConverter.seen
    assert not stage.GetPrimAtPath("/World/R") and source.read_text() == text


def test_a_converter_failure_is_a_refusal_and_leaves_nothing(scene, tmp_path, monkeypatch):
    class Broken(RecordingConverter):
        def import_urdf(self):
            raise RuntimeError("boom")

    monkeypatch.setitem(sys.modules, "isaacsim.asset.importer.urdf",
                        SimpleNamespace(URDFImporterConfig=SimpleNamespace, URDFImporter=Broken))
    _, snapshot = fixture_project(tmp_path, scene, '<robot name="r"><link name="a"/></robot>')
    with pytest.raises(AssetError) as error:
        Advanced(scene, None).import_urdf(scene.stage(), snapshot["snapshot_id"], snapshot["manifest_sha256"], "/World/R")
    assert error.value.code == "IMPORT_FAILED" and "boom" in str(error.value)
    workspace = scene.broker.staging / snapshot["snapshot_id"]
    assert not [p for p in workspace.iterdir() if p.name.startswith((".pending", "converted"))]


@pytest.mark.parametrize("value", ["true", 1, None])
def test_flags_must_be_booleans(scene, tmp_path, monkeypatch, value):
    use_converter(monkeypatch)
    _, snapshot = fixture_project(tmp_path, scene, '<robot name="r"><link name="a"/></robot>')
    with pytest.raises(AssetError) as error:
        Advanced(scene, None).import_urdf(scene.stage(), snapshot["snapshot_id"], snapshot["manifest_sha256"], "/World/R", fixed_base=value)
    assert error.value.code == "INVALID_VALUE"


def test_warnings_reach_the_result(scene, tmp_path, monkeypatch):
    use_converter(monkeypatch)
    text = ('<robot name="r"><link name="base"/><link name="tip"><inertial><mass value="10"/>'
            '<inertia ixx="1e-4" ixy="0" ixz="0" iyy="1e-4" iyz="0" izz="1e-4"/></inertial></link>'
            '<joint name="Hinge" type="continuous"><parent link="base"/><child link="tip"/></joint></robot>')
    _, snapshot = fixture_project(tmp_path, scene, text)
    result = Advanced(scene, None).import_urdf(scene.stage(), snapshot["snapshot_id"], snapshot["manifest_sha256"], "/World/R")
    assert any("very small" in w for w in result["warnings"])
    assert any("fixed_base=false" in w for w in result["warnings"])
    assert urdf_physics.usd_name("Hinge") == "Hinge"


def test_a_pose_read_back_from_state_can_be_sent_again():
    from sim_tools.joint_tools import command_target

    assert command_target(-0.8378, -0.8, 1.0, -0.8378) == -0.8  # resting past a limit, echoed
    assert command_target(0.5, -0.8, 1.0, 0.0) == 0.5
    assert command_target(-0.9, -0.8, 1.0, 0.0) is None  # a real request past the limit
    assert command_target(-0.9, -0.8, 1.0, -0.8) is None


def test_a_runaway_joint_is_named():
    from sim_tools.joint_tools import joints_too_fast

    assert joints_too_fast(["a", "b", "c"], [3.0, -3000.0, 600.0]) == ["b", "c"]
    assert joints_too_fast(["a"], [100.0]) == []


def test_another_importer_or_option_converts_again(scene, tmp_path, monkeypatch):
    """Two builds can share one staging folder, and neither can use the other's output."""
    use_converter(monkeypatch)
    _, snapshot = fixture_project(tmp_path, scene, '<robot name="r"><link name="a"/></robot>')
    stage = scene.stage()
    identity = ["6.x/isaacsim.asset.importer.urdf-3.11.12/1"]
    monkeypatch.setattr("sim_tools.urdf.importer_identity", lambda: identity[0])
    ids = (stage, snapshot["snapshot_id"], snapshot["manifest_sha256"])
    Advanced(scene, None).import_urdf(*ids, "/World/A")
    Advanced(scene, None).import_urdf(*ids, "/World/B")
    assert len(RecordingConverter.seen) == 1
    identity[0] = "5.x/isaacsim.asset.importer.urdf-2.4.30/1"
    Advanced(scene, None).import_urdf(*ids, "/World/C")
    assert len(RecordingConverter.seen) == 2
    Advanced(scene, None).import_urdf(*ids, "/World/D", default_drives=False)
    assert len(RecordingConverter.seen) == 3


class FakeConfig:
    def __init__(self):
        self.calls = {}

    def __getattr__(self, name):
        return lambda value: self.calls.__setitem__(name, value)


def legacy_importer(monkeypatch, execute, extension=None):
    from sim_tools import urdf as module

    importer = SimpleNamespace(_urdf=SimpleNamespace(ImportConfig=FakeConfig))
    commands = SimpleNamespace(execute=execute)
    # No URDFImporter attribute: this is how the 5.x API is told apart.
    monkeypatch.setitem(sys.modules, "isaacsim.asset.importer.urdf", importer)
    monkeypatch.setitem(sys.modules, "omni", SimpleNamespace(kit=SimpleNamespace(commands=commands)))
    monkeypatch.setitem(sys.modules, "omni.kit", SimpleNamespace(commands=commands))
    monkeypatch.setitem(sys.modules, "omni.kit.commands", commands)
    if extension is None:
        monkeypatch.delitem(sys.modules, "ur5e_dt.extension", raising=False)
    else:
        monkeypatch.setitem(sys.modules, "ur5e_dt.extension", SimpleNamespace(_EXT_INSTANCE=extension))
    return module


def test_the_5x_importer_runs_with_the_log_listener_paused(tmp_path, monkeypatch):
    import contextlib

    events = []

    class Twin:
        @contextlib.contextmanager
        def without_carb_log_capture(self):
            events.append("paused")
            yield
            events.append("resumed")

    def execute(name, urdf_path, import_config, dest_path):
        events.append(name)
        assert import_config.calls["set_fix_base"] is False and import_config.calls["set_merge_fixed_joints"] is False
        from pathlib import Path

        Path(dest_path).write_text("#usda 1.0\n")
        return True, "/robot"

    module = legacy_importer(monkeypatch, execute, Twin())
    generated = module.convert(tmp_path / "in" / "r.urdf", tmp_path / "out", False)
    assert events == ["paused", "URDFParseAndImportFile", "resumed"]
    assert generated == tmp_path / "out" / "r" / "r.usd" and module.uses_legacy_importer()


def test_the_5x_importer_returning_nothing_is_a_refusal_and_needs_no_twin(tmp_path, monkeypatch):
    module = legacy_importer(monkeypatch, lambda *a, **k: (True, ""))
    with pytest.raises(AssetError) as error:
        module.convert(tmp_path / "r.urdf", tmp_path / "out", True)
    assert error.value.code == "IMPORT_FAILED" and "r" in str(error.value)


def test_no_importer_at_all_says_so(tmp_path, monkeypatch):
    from sim_tools import urdf as module

    monkeypatch.setitem(sys.modules, "isaacsim.asset.importer.urdf", None)
    with pytest.raises(AssetError) as error:
        module.convert(tmp_path / "r.urdf", tmp_path / "out", True)
    assert error.value.code == "IMPORTER_UNAVAILABLE"


def test_the_twin_puts_its_listener_back_unless_closing(monkeypatch):
    """The method is read from the source, because importing the twin needs a running Kit."""
    import ast
    from pathlib import Path
    from types import ModuleType

    source = (Path(__file__).resolve().parents[2] / "exts/ur5e-dt/ur5e_dt/extension.py").read_text()
    method = next(node for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.FunctionDef) and node.name == "without_carb_log_capture")
    lines = source.splitlines()[method.decorator_list[0].lineno - 1:method.end_lineno]
    log = []

    class Logging:
        def remove_logger(self, handle):
            log.append(("remove", handle))

        def add_logger(self, callback):
            log.append(("add", callback))
            return "new"

    namespace = dict(carb=SimpleNamespace(logging=SimpleNamespace(acquire_logging=Logging)), contextlib=__import__("contextlib"))
    module = ModuleType("m")
    exec("class T:\n" + "\n".join(lines), {**namespace, "__name__": "m"}, vars(module))
    twin = module.T()
    twin._carb_log_sub, twin._closing, twin._on_carb_log = "old", False, "callback"
    with twin.without_carb_log_capture():
        assert twin._carb_log_sub is None
    assert log == [("remove", "old"), ("add", "callback")] and twin._carb_log_sub == "new"
    log.clear()
    twin._closing = True
    with twin.without_carb_log_capture():
        pass
    assert log == [("remove", "new")] and twin._carb_log_sub is None
    log.clear()
    twin._closing = False
    with twin.without_carb_log_capture():
        pass
    assert log == []  # nothing installed: nothing removed or added


def test_a_robot_referenced_by_name_has_no_transform_and_is_still_lifted():
    """sim__quick_start references a public robot as a bare Xform: a humanoid's pelvis is its origin, so its legs start under the floor."""
    stage = ground_stage()
    UsdGeom.Xformable(stage.GetPrimAtPath("/World/R")).ClearXformOpOrder()
    assert not UsdGeom.Xformable(stage.GetPrimAtPath("/World/R")).GetOrderedXformOps()
    report = place_above_ground(stage, "/World/R", False)
    assert report["lifted_by"] == pytest.approx(0.505)
    assert lowest_point(stage, "/World/R") == pytest.approx(0.005)
    assert len(UsdGeom.Xformable(stage.GetPrimAtPath("/World/R")).GetOrderedXformOps()) == 1


@pytest.mark.parametrize("first_op", ["translate", "translate_float", "rotate_then_translate", "scale"])
def test_every_kind_of_first_transform_op_is_lifted_in_the_parent_frame(first_op):
    """The H1's USD brings its own translate op; others start with a rotation or scale. Lift must work on each."""
    stage = ground_stage()
    xform = UsdGeom.Xformable(stage.GetPrimAtPath("/World/R"))
    xform.ClearXformOpOrder()
    if first_op == "translate":
        xform.AddTranslateOp().Set(Gf.Vec3d(1, 2, 0))
    elif first_op == "translate_float":
        xform.AddTranslateOp(precision=UsdGeom.XformOp.PrecisionFloat).Set(Gf.Vec3f(1, 2, 0))
    elif first_op == "rotate_then_translate":
        xform.AddRotateZOp().Set(90.0)
        xform.AddTranslateOp().Set(Gf.Vec3d(1, 0, 0))
    else:
        xform.AddScaleOp().Set(Gf.Vec3f(1, 1, 1))
    report = place_above_ground(stage, "/World/R", False)
    assert report["lifted_by"] == pytest.approx(0.505)
    assert lowest_point(stage, "/World/R") == pytest.approx(0.005, abs=1e-4)
    world = xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
    assert world[2] == pytest.approx(0.505, abs=1e-4)  # straight up, whatever the robot's own rotation


def test_an_absurd_lowest_point_is_reported_and_the_robot_is_not_moved():
    """Carter's USD reads 1,464 m below the ground (stray geometry): an automatic lift must not send the robot there."""
    stage = ground_stage()
    UsdGeom.Xformable(stage.GetPrimAtPath("/World/R")).ClearXformOpOrder()
    cube = UsdGeom.Cube(stage.GetPrimAtPath("/World/R/Body"))
    cube.GetSizeAttr().Set(2000.0)
    cube.GetExtentAttr().Set([(-1000, -1000, -1000), (1000, 1000, 1000)])
    report = place_above_ground(stage, "/World/R", False, max_lift=3.0)
    assert report["lifted_by"] == 0.0 and "not moved" in report["warning"]
    assert not UsdGeom.Xformable(stage.GetPrimAtPath("/World/R")).GetOrderedXformOps()  # nothing was authored
    stage2 = ground_stage()
    assert place_above_ground(stage2, "/World/R", False, max_lift=3.0)["lifted_by"] == pytest.approx(0.505)  # a real sink still lifts


def test_a_continuous_joint_gets_no_holding_stiffness_only_when_its_limits_are_authored_infinite():
    """A wheel must turn under a velocity command; a joint with no authored limits is not known to be a wheel."""
    from pxr import Usd, UsdPhysics
    from sim_tools.urdf import _author_drives, _unlimited

    stage = Usd.Stage.CreateInMemory()
    wheel = UsdPhysics.RevoluteJoint.Define(stage, "/Robot/wheel")
    wheel.CreateLowerLimitAttr(float("-inf"))
    wheel.CreateUpperLimitAttr(float("inf"))
    hinge = UsdPhysics.RevoluteJoint.Define(stage, "/Robot/hinge")
    limited = UsdPhysics.RevoluteJoint.Define(stage, "/Robot/limited")
    limited.CreateLowerLimitAttr(-90.0)
    limited.CreateUpperLimitAttr(90.0)
    assert [_unlimited(j.GetPrim()) for j in (wheel, hinge, limited)] == [True, False, False]
    gains = {n: dict(stiffness=100.0, damping=10.0, linear=False) for n in ("wheel", "hinge", "limited")}
    authored, _, _ = _author_drives(stage.GetPrimAtPath("/Robot"), gains, False)
    assert authored["wheel"]["stiffness"] == 0.0 and authored["wheel"]["continuous"] is True and authored["wheel"]["damping"] > 0
    assert authored["hinge"]["stiffness"] > 0 and authored["limited"]["stiffness"] > 0


def test_a_urdf_continuous_joint_is_free_even_when_the_importer_authors_no_limits():
    """Sim 6.2's URDF importer writes no limit on a continuous joint, so the wheel kept a holding stiffness and stopped at about 0.6 rad."""
    urdf = ('<robot name="r"><link name="a"><inertial><mass value="1"/><inertia ixx="1" iyy="1" izz="1" ixy="0" ixz="0" iyz="0"/></inertial></link>'
            '<link name="b"><inertial><mass value="1"/><inertia ixx="1" iyy="1" izz="1" ixy="0" ixz="0" iyz="0"/></inertial></link>'
            '<joint name="wheel" type="continuous"><parent link="a"/><child link="b"/><axis xyz="0 0 1"/></joint></robot>')
    gains = urdf_physics.default_gains(urdf_physics.read_model(ElementTree.fromstring(urdf)))
    assert gains["wheel"]["continuous"] is True
    stage = Usd.Stage.CreateInMemory()
    UsdPhysics.RevoluteJoint.Define(stage, "/Robot/wheel")  # no limits authored
    authored, _, _ = _author_drives(stage.GetPrimAtPath("/Robot"), gains, False)
    assert authored["wheel"]["stiffness"] == 0.0 and authored["wheel"]["damping"] > 0
    drive = UsdPhysics.DriveAPI.Get(stage.GetPrimAtPath("/Robot/wheel"), "angular")
    assert drive.GetStiffnessAttr().Get() == 0.0

"""MuJoCo models through assets__import_path: the contract, the broker's mjcf kind, the closure the pack tools read, and the Kit-side helpers."""

import json
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from isaac_mcp import asset_packs as packs
from isaac_mcp import mjcf_refs
from isaac_mcp.project_assets import AssetError, Broker
from isaac_mcp.scene_contracts import REGISTRY

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "exts" / "sim-tools"))

MODEL = """<mujoco model="arm"><compiler meshdir="assets"/>
<asset><mesh name="m" file="link.stl"/></asset>
<worldbody><body name="base"><joint name="j1" type="hinge" axis="0 0 1"/><geom type="mesh" mesh="m"/></body></worldbody></mujoco>"""


@pytest.fixture
def broker(tmp_path):
    root = tmp_path / "model"
    (root / "assets").mkdir(parents=True)
    (root / "arm.xml").write_text(MODEL)
    (root / "assets" / "link.stl").write_text("solid")
    (root / "robot.urdf").write_text("<robot name='r'/>")
    (root / "package.xml").write_text("<package><name>p</name></package>")
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    config = tmp_path / "approval.json"
    config.write_text(json.dumps(dict(version=1, profile="trusted_project", roots={"m": str(root)}, staging=str(staging))))
    made = Broker(str(config))
    assert made.config_error is None
    return made


def test_the_contract_is_one_tool_with_the_right_arguments_inside_the_listing_limits():
    tool = REGISTRY["assets.import_path"]
    params = tool["parameters"]
    assert len(tool["description"]) <= 600
    assert {"session_id", "stage_id", "prim_path", "source_path", "root_id", "relative_path", "fixed_base", "default_drives", "joint_targets", "position"} <= set(params)
    required = {name for name, spec in params.items() if spec.get("required", True)}
    assert required == {"session_id", "stage_id", "prim_path"}
    assert "default" not in params["fixed_base"]  # left out means "keep what the model says", never a silent true
    assert all(len(spec["description"]) <= 450 for spec in params.values())


def test_the_broker_snapshots_an_mjcf_and_refuses_the_wrong_kind(broker, tmp_path):
    snap = broker.snapshot("m", "arm.xml", kind="mjcf")
    assert snap["snapshot_id"] and snap["bytes"] > 0
    for name, code in (("robot.urdf", "UNSUPPORTED_FORMAT"), ("package.xml", "UNSUPPORTED_FORMAT"), ("assets/link.stl", "UNSUPPORTED_FORMAT")):
        with pytest.raises(AssetError) as caught:
            broker.snapshot("m", name, kind="mjcf")
        assert caught.value.code == code, name


def test_locate_accepts_an_xml_only_as_mjcf_and_says_where_an_xml_belongs(broker, tmp_path):
    path = str(tmp_path / "model" / "arm.xml")
    assert broker.locate(path, kind="mjcf") == ("m", "arm.xml")
    with pytest.raises(AssetError) as caught:
        broker.locate(path)  # as a USD
    assert caught.value.code == "UNSUPPORTED_FORMAT" and "assets__import_path" in str(caught.value)
    with pytest.raises(AssetError) as urdf:
        broker.locate(str(tmp_path / "model" / "robot.urdf"), kind="mjcf")
    assert "assets__import_path" in str(urdf.value)


def test_the_snapshot_of_an_mjcf_verifies_and_resolves_inside_itself(broker):
    snap = broker.snapshot("m", "arm.xml", kind="mjcf")
    filename, manifest = broker.verify(snap["snapshot_id"], snap["manifest_sha256"], kind="mjcf")
    from isaac_mcp import urdf_refs

    project_dir = filename.parents[len(Path(manifest["entry"]).parts) - 1]
    project = urdf_refs.project_from_manifest(project_dir, manifest["files"])
    found = mjcf_refs.resolve(manifest["entry"], project)
    assert found["problems"] == [] and "assets/link.stl" in found["files"]


def test_dependencies_reads_an_mjcf_on_the_host_without_a_usd_parser(tmp_path):
    root = tmp_path / "m"
    (root / "assets").mkdir(parents=True)
    (root / "arm.xml").write_text(MODEL)
    (root / "assets" / "link.stl").write_text("s")

    def must_not_run(paths):
        raise AssertionError("an MJCF must not go to the USD helper")

    results = packs.run_closure(must_not_run, [str(root / "arm.xml")], lambda path: root)
    assert len(results) == 1 and results[0]["mjcf"] is True and results[0]["problems"] == []
    assert str(root / "assets" / "link.stl") in results[0]["assets"]


def test_pack_build_refuses_an_unapproved_mujoco_model_and_a_xacro_with_a_pointer(tmp_path):
    with pytest.raises(packs.PackError) as caught:
        packs.run_tool("assets.pack_build", dict(pack="p", sources=[str(tmp_path / "arm.xml")]), Broker())
    assert caught.value.code in ("ROOT_NOT_APPROVED", "NOT_FOUND", "USD_UNAVAILABLE")


@pytest.mark.parametrize("body, expected", [
    ("<body><joint type='hinge'/><joint type='slide'/><joint type='free'/><joint type='ball'/><joint/></body>", 3),
    ("<default><joint type='hinge'/></default><equality><joint joint1='a'/></equality><tendon><fixed><joint joint='a' coef='1'/></fixed></tendon>", 0),
    ("<body><body><joint name='a'/></body><joint name='b' type='hinge'/></body>", 2),
])
def test_only_a_bodys_own_hinge_and_slide_joints_are_counted(body, expected):
    pytest.importorskip("pxr")
    from sim_tools.mjcf import movable_joint_count

    assert movable_joint_count([ET.fromstring(f"<mujoco><worldbody>{body}</worldbody></mujoco>")]) == expected


def test_an_included_file_adds_its_joints():
    pytest.importorskip("pxr")
    from sim_tools.mjcf import movable_joint_count

    a = ET.fromstring("<mujoco><worldbody><body><joint/></body></worldbody></mujoco>")
    b = ET.fromstring("<mujoco><worldbody><body><joint/><joint/></body></worldbody></mujoco>")
    assert movable_joint_count([a, b]) == 3


def test_tendon_fidelity_warning_names_real_tendons_not_default_templates():
    from sim_tools.mjcf import tendon_warning

    defaults = ET.fromstring('<mujoco><default><tendon stiffness="2"/></default></mujoco>')
    model = ET.fromstring('<mujoco><tendon><fixed name="split"/><spatial/></tendon></mujoco>')
    assert tendon_warning([defaults]) is None
    warning = tendon_warning([defaults, model])
    assert "split" in warning and "(unnamed)" in warning and "actuation" in warning


def _prismatic_pair(tmp_path, disconnected=False, second_name='b'):
    from pxr import Usd, UsdGeom, UsdPhysics

    path = tmp_path / 'converted.usda'
    stage = Usd.Stage.CreateNew(str(path))
    root = UsdGeom.Xform.Define(stage, '/Robot').GetPrim()
    stage.SetDefaultPrim(root)
    for name in ('hand', 'left', 'right', 'other'):
        prim = UsdGeom.Xform.Define(stage, f'/Robot/{name}').GetPrim()
        UsdPhysics.RigidBodyAPI.Apply(prim)
    for name, parent, child in (('a', 'hand', 'left'), (second_name, 'other' if disconnected else 'hand', 'right')):
        joint = UsdPhysics.PrismaticJoint.Define(stage, f'/Robot/{name}')
        joint.CreateBody0Rel().SetTargets([f'/Robot/{parent}'])
        joint.CreateBody1Rel().SetTargets([f'/Robot/{child}'])
    stage.GetRootLayer().Save()
    return path


def test_legacy_simple_prismatic_equality_is_written_as_mimic(tmp_path):
    pytest.importorskip('pxr')
    from pxr import Usd
    from sim_tools.mjcf import restore_legacy_equalities

    path = _prismatic_pair(tmp_path)
    model = ET.fromstring('<mujoco><worldbody><body><joint name="a"/><joint name="b"/></body></worldbody><equality><joint joint1="a" joint2="b"/></equality></mujoco>')
    assert restore_legacy_equalities(path, [model]) == [{'joint1': 'a', 'joint2': 'b'}]
    stage = Usd.Stage.Open(str(path))
    follower = stage.GetPrimAtPath('/Robot/b')
    assert 'PhysxMimicJointAPI:rotX' in follower.GetMetadata('apiSchemas').GetAppliedItems()
    assert [str(p) for p in follower.GetRelationship('physxMimicJoint:rotX:referenceJoint').GetTargets()] == ['/Robot/a']
    assert follower.GetAttribute('physxMimicJoint:rotX:gearing').Get() == -1.0


@pytest.mark.parametrize('equality,ref', [
    ('<joint joint1="a" joint2="b" polycoef="0 1 1 0 0"/>', ''),
    ('<joint joint1="a" joint2="b" active="false"/>', ''),
    ('<joint joint1="a" joint2="b"/>', ' ref="1"'),
    ('<joint joint1="a" joint2="b"/><joint joint1="b" joint2="c" polycoef="0 1 1 0 0"/>', ''),
])
def test_legacy_equality_does_not_guess_nonlinear_disabled_or_shifted_cases(tmp_path, equality, ref):
    from sim_tools.mjcf import restore_legacy_equalities

    path = _prismatic_pair(tmp_path)
    model = ET.fromstring(f'<mujoco><worldbody><body><joint name="a"{ref}/><joint name="b"/></body></worldbody><equality>{equality}</equality></mujoco>')
    assert restore_legacy_equalities(path, [model]) == []


def test_legacy_equality_does_not_link_disconnected_bodies(tmp_path):
    from sim_tools.mjcf import restore_legacy_equalities

    path = _prismatic_pair(tmp_path, disconnected=True)
    model = ET.fromstring('<mujoco><worldbody><body><joint name="a"/><joint name="b"/></body></worldbody><equality><joint joint1="a" joint2="b"/></equality></mujoco>')
    assert restore_legacy_equalities(path, [model]) == []


def test_legacy_equality_does_not_guess_when_joint_names_normalize_to_the_same_usd_name(tmp_path):
    from sim_tools.mjcf import restore_legacy_equalities

    path = _prismatic_pair(tmp_path, second_name='b_')
    model = ET.fromstring('<mujoco><worldbody><body><joint name="a"/><joint name="b"/><joint name="b!"/><joint name="b_"/></body></worldbody><equality><joint joint1="a" joint2="b!"/></equality></mujoco>')
    assert restore_legacy_equalities(path, [model]) == []


def test_legacy_normalization_merges_named_defaults_and_avoids_mesh_stem_collision(tmp_path):
    import shutil
    from isaac_mcp.urdf_refs import project_from_folder
    from sim_tools.mjcf import _legacy_input

    original = tmp_path / 'original'
    (original / 'assets').mkdir(parents=True)
    model = '''<mujoco><compiler meshdir="assets"/>
      <default><default class="one"><joint damping="1"/></default></default>
      <default><default class="two"><joint damping="2"/></default></default>
      <asset><mesh name="collision" file="link.stl"/><mesh file="link.obj"/></asset>
      <worldbody><body name="base"><freejoint/></body></worldbody></mujoco>'''
    (original / 'robot.xml').write_text(model)
    (original / 'assets/link.stl').write_bytes(b'stl')
    (original / 'assets/link.obj').write_bytes(b'obj')
    (original / 'assets/link__isaac_mcp_1.obj').write_bytes(b'do not overwrite')
    (original / 'assets/link__isaac_mcp_2.stl').write_bytes(b'same stem, different extension')
    found = mjcf_refs.resolve('robot.xml', project_from_folder(original))
    assert not found['problems']
    private = tmp_path / 'private'
    shutil.copytree(original, private)
    assert _legacy_input(private, 'robot.xml', found) is True
    result = ET.parse(private / 'robot.xml').getroot()
    assert len(result.findall('default')) == 1
    assert [e.get('class') for e in result.find('default').findall('default')] == ['one', 'two']
    meshes = result.find('asset').findall('mesh')
    assert meshes[0].get('file') == 'link.stl'
    assert meshes[1].get('file') == 'link__isaac_mcp_3.obj' and meshes[1].get('name') == 'link'
    assert (private / 'assets/link__isaac_mcp_3.obj').read_bytes() == b'obj'
    assert (private / 'assets/link__isaac_mcp_1.obj').read_bytes() == b'do not overwrite'
    assert (original / 'robot.xml').read_text() == model
    assert not (original / 'assets/link__isaac_mcp_3.obj').exists()


def test_legacy_normalization_refuses_ambiguous_defaults(tmp_path):
    from isaac_mcp.urdf_refs import project_from_folder
    from sim_tools.mjcf import _legacy_input

    (tmp_path / 'robot.xml').write_text('<mujoco><default><joint damping="1"/></default><default><joint damping="2"/></default></mujoco>')
    found = mjcf_refs.resolve('robot.xml', project_from_folder(tmp_path))
    with pytest.raises(AssetError) as caught:
        _legacy_input(tmp_path, 'robot.xml', found)
    assert caught.value.code == 'MJCF_51_DEFAULTS'


def test_legacy_normalization_resolves_submodel_meshes_in_their_own_scope(tmp_path):
    import shutil
    from isaac_mcp.urdf_refs import project_from_folder
    from sim_tools.mjcf import _legacy_input

    source = tmp_path / 'source'
    (source / 'sub').mkdir(parents=True)
    (source / 'robot.xml').write_text('<mujoco><asset><mesh file="link.stl"/><model name="sub" file="sub/model.xml"/></asset></mujoco>')
    (source / 'link.stl').write_bytes(b'parent')
    (source / 'sub/model.xml').write_text('<mujoco><asset><mesh name="a" file="link.stl"/><mesh name="b" file="link.obj"/></asset></mujoco>')
    (source / 'sub/link.stl').write_bytes(b'sub stl')
    (source / 'sub/link.obj').write_bytes(b'sub obj')
    found = mjcf_refs.resolve('robot.xml', project_from_folder(source))
    assert not found['problems']
    assert found['mesh_sources']['robot.xml']['link.stl'] == ['link.stl']
    assert found['mesh_sources']['sub/model.xml']['link.stl'] == ['sub/link.stl']
    private = tmp_path / 'private'
    shutil.copytree(source, private)
    _legacy_input(private, 'robot.xml', found)
    meshes = ET.parse(private / 'sub/model.xml').getroot().find('asset').findall('mesh')
    assert [mesh.get('file') for mesh in meshes] == ['link.stl', 'link__isaac_mcp_1.obj']
    assert (private / 'sub/link__isaac_mcp_1.obj').read_bytes() == b'sub obj'


def test_legacy_shared_include_only_refuses_ambiguous_mesh_when_collision_needs_rewrite(tmp_path):
    from isaac_mcp.urdf_refs import project_from_folder
    from sim_tools.mjcf import _legacy_input

    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    (tmp_path / 'robot.xml').write_text('<mujoco><asset><model name="a" file="a/model.xml"/><model name="b" file="b/model.xml"/></asset></mujoco>')
    (tmp_path / 'shared.xml').write_text('<mujocoinclude><asset><mesh name="shared" file="link.stl"/></asset></mujocoinclude>')
    for sub in ('a', 'b'):
        (tmp_path / sub / 'model.xml').write_text('<mujoco><include file="../shared.xml"/></mujoco>')
        (tmp_path / sub / 'link.stl').write_bytes(sub.encode())
    found = mjcf_refs.resolve('robot.xml', project_from_folder(tmp_path))
    assert not found['problems']
    assert found['mesh_sources']['shared.xml']['link.stl'] == ['a/link.stl', 'b/link.stl']
    assert _legacy_input(tmp_path, 'robot.xml', found) is False

    (tmp_path / 'a/model.xml').write_text('<mujoco><include file="../shared.xml"/><asset><mesh name="extra" file="link.obj"/></asset></mujoco>')
    (tmp_path / 'a/link.obj').write_bytes(b'obj')
    found = mjcf_refs.resolve('robot.xml', project_from_folder(tmp_path))
    with pytest.raises(AssetError) as caught:
        _legacy_input(tmp_path, 'robot.xml', found)
    assert caught.value.code == 'MJCF_51_MODEL'

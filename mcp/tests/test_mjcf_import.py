"""assets__import_mjcf: its contract, the broker's mjcf kind, the closure the pack tools read, and the Kit-side helpers."""

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
    tool = REGISTRY["assets.import_mjcf"]
    params = tool["parameters"]
    assert len(tool["description"]) <= 600
    assert {"session_id", "stage_id", "prim_path", "source_path", "root_id", "relative_path", "fixed_base", "position"} <= set(params)
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
    assert caught.value.code == "UNSUPPORTED_FORMAT" and "assets__import_mjcf" in str(caught.value)
    with pytest.raises(AssetError) as urdf:
        broker.locate(str(tmp_path / "model" / "robot.urdf"), kind="mjcf")
    assert "assets__import_urdf" in str(urdf.value)


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


def test_pack_build_says_where_a_mujoco_model_goes_instead(tmp_path):
    with pytest.raises(packs.PackError) as caught:
        packs.run_tool("assets.pack_build", dict(pack="p", sources=[str(tmp_path / "arm.xml")]), Broker())
    assert caught.value.code in ("UNSUPPORTED_FORMAT", "ROOT_NOT_APPROVED", "NOT_FOUND")


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

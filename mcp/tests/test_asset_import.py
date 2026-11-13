"""assets__import_path: one entry for every format, folders that name their own entry file, and what it refuses."""

import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from twinsmith import asset_formats, asset_import
from twinsmith.project_assets import AssetError, Broker

NS = 'xmlns:xacro="http://www.ros.org/wiki/xacro"'
URDF = '<robot name="r"><link name="a"/><link name="b"/><joint name="j" type="revolute"><parent link="a"/><child link="b"/><limit lower="0" upper="1" effort="1" velocity="1"/></joint></robot>'
XACRO = f'<robot name="r" {NS}><xacro:arg name="n" default="2"/><link name="a"/><link name="b${{$(arg n)}}"/></robot>'
SDF = '<sdf version="1.7"><model name="car"><link name="chassis"><collision name="c"><geometry><box><size>1 1 1</size></box></geometry></collision></link></model></sdf>'
MJCF = '<mujoco model="m"><worldbody><body name="b"><joint type="hinge"/><geom size="1"/></body></worldbody></mujoco>'


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "proj"
    for rel, text in {
        "usd/a.usda": "#usda 1.0\n", "urdf/r.urdf": URDF, "xacro/r.urdf.xacro": XACRO, "sdf/car.sdf": SDF, "mjcf/m.xml": MJCF,
        "gz/box/model.config": "<model><sdf>model.sdf</sdf></model>", "gz/box/model.sdf": SDF,
        "ros/pkg/package.xml": "<package><name>pkg</name></package>", "ros/pkg/urdf/robot.urdf.xacro": XACRO, "ros/pkg/urdf/robot_macro.xacro": f'<robot {NS}/>',
        "menagerie/go2/go2.xml": MJCF, "menagerie/go2/go2_mjx.xml": MJCF, "menagerie/go2/assets/base_0.obj": "o", "menagerie/go2/scene.xml": '<mujoco model="s"><include file="go2.xml"/></mujoco>', "menagerie/go2/scene_mjx.xml": '<mujoco model="s"><include file="go2_mjx.xml"/></mujoco>',
        "lerobot/arm/urdf/arm.urdf": URDF, "lerobot/arm/assets/link.stl": "solid",
        "xacro_in_urdf/bot.urdf": XACRO, "two/a.urdf": URDF, "two/b.urdf": URDF, "empty/readme.txt": "x", "meshes/duck.glb": "glTF",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    config = tmp_path / "approval.json"
    config.write_text(json.dumps(dict(version=1, profile="trusted_project", roots={"p": str(root)}, staging=str(staging))))
    broker = Broker(str(config))
    assert broker.config_error is None
    return broker, root


def go(broker, root, rel, **params):
    return asset_import.route(broker, dict(source_path=str(root / rel), prim_path="/World/x", **params))


@pytest.mark.parametrize("rel, kit_format, fmt", [("usd/a.usda", "usd", "usd"), ("urdf/r.urdf", "urdf", "urdf"), ("mjcf/m.xml", "mjcf", "mjcf"),
                                                  ("xacro/r.urdf.xacro", "urdf", "xacro"), ("sdf/car.sdf", "urdf", "sdf")])
def test_every_format_is_picked_from_its_name_and_goes_to_the_one_tool_with_a_format(setup, rel, kit_format, fmt):
    broker, root = setup
    kit_tool, params, info = go(broker, root, rel)
    assert (kit_tool, params["format"], info["format"]) == ("assets.import_path", kit_format, fmt)
    assert params["snapshot_id"] and params["manifest_sha256"] and "source_path" not in params


def test_a_converted_file_is_in_the_verified_snapshot_and_is_its_entry(setup):
    broker, root = setup
    _, params, info = go(broker, root, "xacro/r.urdf.xacro", xacro_args={"n": 7})
    filename, manifest = broker.verify(params["snapshot_id"], params["manifest_sha256"], kind="urdf")
    assert manifest["entry"] == "xacro/r.generated.urdf" and manifest["generated"]["source"] == "xacro/r.urdf.xacro"
    assert manifest["generated"]["args"] == {"n": "7"}
    assert ET.fromstring(filename.read_text()).find("link[@name='b7']") is not None
    assert info["converted_from"] == "xacro/r.urdf.xacro"
    other = go(broker, root, "xacro/r.urdf.xacro", xacro_args={"n": 8})[1]
    assert other["snapshot_id"] != params["snapshot_id"]  # other arguments, other snapshot
    assert go(broker, root, "xacro/r.urdf.xacro", xacro_args={"n": 7})[1]["snapshot_id"] == params["snapshot_id"]


def test_an_sdf_model_decides_the_default_for_fixed_base(setup):
    broker, root = setup
    assert go(broker, root, "sdf/car.sdf")[1]["fixed_base"] is False
    assert go(broker, root, "sdf/car.sdf", fixed_base=True)[1]["fixed_base"] is True


@pytest.mark.parametrize("rel, entry, fmt", [("gz/box", "gz/box/model.sdf", "sdf"), ("ros/pkg", "ros/pkg/urdf/robot.urdf.xacro", "xacro"), ("menagerie/go2", "menagerie/go2/go2.xml", "mjcf"),
                                             ("lerobot/arm", "lerobot/arm/urdf/arm.urdf", "urdf")])
def test_a_folder_names_its_own_entry_file(setup, rel, entry, fmt):
    broker, root = setup
    _, _, info = go(broker, root, rel)
    assert (info["bundle"]["entry"], info["format"]) == (entry, fmt)


def test_a_folder_with_several_candidates_lists_them_and_an_empty_one_says_so(setup):
    broker, root = setup
    with pytest.raises(AssetError) as caught:
        go(broker, root, "two")
    assert caught.value.code == "AMBIGUOUS_BUNDLE" and {c["entry"] for c in caught.value.details["candidates"]} == {"two/a.urdf", "two/b.urdf"}
    with pytest.raises(AssetError) as empty:
        go(broker, root, "empty")
    assert empty.value.code == "NOT_FOUND"


def test_refusals_say_what_to_do(setup):
    broker, root = setup
    for rel, code in (("empty/readme.txt", "UNSUPPORTED_FORMAT"), ("ros/pkg/package.xml", "UNSUPPORTED_FORMAT")):
        with pytest.raises(AssetError) as caught:
            go(broker, root, rel)
        assert caught.value.code == code, rel
    for options in ({"format": "bogus"}, {"xacro_args": ["x"]}, {"xacro_args": {"a": [1]}}, {"sdf_model": 4}):
        with pytest.raises(AssetError) as caught:
            go(broker, root, "urdf/r.urdf", **options)
        assert caught.value.code == "INVALID_VALUE", options
    with pytest.raises(AssetError):
        go(broker, root, "urdf/r.urdf", xacro_args={"a": 1})  # xacro_args are for xacro only
    with pytest.raises(AssetError) as outside:
        asset_import.route(broker, dict(source_path="/etc/passwd", prim_path="/World/x"))
    assert outside.value.code in ("ROOT_NOT_APPROVED", "UNSUPPORTED_FORMAT")


def test_a_format_can_be_forced_and_a_wrong_force_is_refused(setup):
    broker, root = setup
    assert go(broker, root, "urdf/r.urdf", format="urdf")[2]["format"] == "urdf"
    with pytest.raises(AssetError):
        go(broker, root, "urdf/r.urdf", format="mjcf")


def test_robot_options_are_dropped_for_usd_and_kept_for_robot_formats(setup):
    broker, root = setup
    assert "fixed_base" not in go(broker, root, "usd/a.usda", fixed_base=True, default_drives=False)[1]
    mjcf = go(broker, root, "mjcf/m.xml", fixed_base=True, default_drives=True)[1]
    assert mjcf["fixed_base"] is True and mjcf["default_drives"] is True


def test_format_detection_table():
    table = {"a.usdz": "usd", "a.URDF": "urdf", "a.urdf.xacro": "xacro", "a.world": "sdf", "a.xml": "mjcf", "a.glb": "mesh", "a.DAE": "mesh", "a.txt": None, "package.xml": None, "model.config": None}
    assert {k: asset_formats.format_of(k) for k in table} == table


def test_a_mesh_goes_to_the_converter_with_its_options_and_robot_options_are_dropped(setup):
    broker, root = setup
    kit_tool, params, info = go(broker, root, "meshes/duck.glb", scale=0.01, up_axis="y", as_collider="convex_hull", mass=2, fixed_base=True)
    assert (kit_tool, info["format"]) == ("assets.import_path", "mesh")
    assert params["format"] == "mesh" and (params["scale"], params["up_axis"], params["as_collider"], params["mass"]) == (0.01, "y", "convex_hull", 2)
    assert "fixed_base" not in params and params["snapshot_id"]
    with pytest.raises(AssetError) as caught:
        go(broker, root, "urdf/r.urdf", scale=2)  # mesh options are for meshes only
    assert caught.value.code == "INVALID_VALUE"


def test_an_unapproved_folder_gets_the_approval_command_not_a_format_complaint(setup, tmp_path):
    broker, root = setup
    outside = tmp_path / "elsewhere" / "robot"
    outside.mkdir(parents=True)
    with pytest.raises(AssetError) as caught:
        asset_import.route(broker, dict(source_path=str(outside), prim_path="/World/x"))
    assert caught.value.code == "ROOT_NOT_APPROVED" and "project_assets" in str(caught.value)


def test_xacro_kept_in_a_file_named_urdf_is_expanded_not_refused(setup):
    broker, root = setup
    kit_tool, params, info = go(broker, root, "xacro_in_urdf/bot.urdf", xacro_args={"n": 3})
    assert info["format"] == "xacro" and params["format"] == "urdf"
    filename, manifest = broker.verify(params["snapshot_id"], params["manifest_sha256"], kind="urdf")
    assert manifest["entry"] == "xacro_in_urdf/bot.generated.urdf" and ET.fromstring(filename.read_text()).find("link[@name='b3']") is not None
    _, _, plain = go(broker, root, "urdf/r.urdf")
    assert plain["format"] == "urdf"  # a real URDF stays a URDF

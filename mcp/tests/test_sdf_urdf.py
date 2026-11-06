"""The SDF reader: a Gazebo model written as a URDF, and what it refuses."""

import math
import xml.etree.ElementTree as ET

import pytest

from isaac_mcp import sdf_urdf as s
from isaac_mcp import urdf_refs
from isaac_mcp.project_assets import AssetError
from isaac_mcp.urdf_refs import Project

LINK = '<link name="{n}"><pose>{pose}</pose><inertial><mass>2</mass><inertia><ixx>1</ixx><iyy>1</iyy><izz>1</izz></inertia></inertial>' \
       '<visual name="v"><geometry>{geom}</geometry><material><diffuse>1 0 0 1</diffuse></material></visual>' \
       '<collision name="c"><geometry>{geom}</geometry></collision></link>'
BOX = '<box><size>1 2 3</size></box>'


def project(files):
    data = {k: (v if isinstance(v, bytes) else v.encode()) for k, v in files.items()}
    return Project(data.keys(), lambda rel: data[rel], "root")


def model(links, joints="", name="m", extra=""):
    return f'<sdf version="1.7"><model name="{name}">{extra}{links}{joints}</model></sdf>'


def convert(text, entry="m.sdf", name=None, extra_files=None):
    return s.convert(entry, project({entry: text, **(extra_files or {})}), name)


def test_a_two_link_model_keeps_its_geometry_joints_limits_and_frames():
    text = model(LINK.format(n="base", pose="0 0 1 0 0 0", geom=BOX) + LINK.format(n="arm", pose="1 0 1 0 0 1.5707963", geom='<sphere><radius>0.25</radius></sphere>'),
                 '<joint name="j" type="revolute"><parent>base</parent><child>arm</child><axis><xyz>0 0 1</xyz><limit><lower>-1</lower><upper>2</upper><effort>9</effort>'
                 '<velocity>3</velocity></limit><dynamics><damping>0.5</damping></dynamics></axis></joint>')
    result = convert(text)
    root = ET.fromstring(result["urdf"])
    urdf_refs.parse(result["urdf"].encode())
    joint = root.find("joint")
    assert (joint.get("type"), joint.find("parent").get("link"), joint.find("child").get("link")) == ("revolute", "base", "arm")
    assert joint.find("origin").get("xyz") == "1 0 0"  # the child is 1 m along x of the parent
    assert float(joint.find("origin").get("rpy").split()[2]) == pytest.approx(math.pi / 2, abs=1e-5)
    assert joint.find("limit").attrib == {"lower": "-1", "upper": "2", "effort": "9", "velocity": "3"}
    assert joint.find("dynamics").get("damping") == "0.5"
    assert root.find("link[@name='base']/visual/geometry/box").get("size") == "1 2 3"
    assert root.find("link[@name='base']/visual/material/color").get("rgba") == "1 0 0 1"
    assert result["static"] is False and result["joints"] == ["j"]


def test_wide_revolute_limits_become_continuous_and_a_ball_becomes_three_joints():
    text = model(LINK.format(n="a", pose="0 0 0 0 0 0", geom=BOX) + LINK.format(n="w", pose="1 0 0 0 0 0", geom=BOX) + LINK.format(n="c", pose="2 0 0 0 0 0", geom=BOX),
                 '<joint name="wheel" type="revolute"><parent>a</parent><child>w</child><axis><xyz>0 1 0</xyz><limit><lower>-1e+16</lower><upper>1e+16</upper></limit></axis></joint>'
                 '<joint name="caster" type="ball"><parent>a</parent><child>c</child></joint>')
    result = convert(text)
    root = ET.fromstring(result["urdf"])
    types = {j.get("name"): j.get("type") for j in root.findall("joint")}
    assert types["wheel"] == "continuous" and types["caster__x"] == types["caster__y"] == types["caster__z"] == "continuous"
    assert any("ball joint" in w for w in result["warnings"])
    assert root.find("joint[@name='wheel']/limit").get("lower") is None


def test_the_unitless_rest_is_dropped_and_listed():
    text = model(LINK.format(n="a", pose="0 0 0 0 0 0", geom='<capsule><radius>1</radius><length>2</length></capsule>') .replace("</link>", "<sensor name='s' type='camera'/></link>"),
                 extra='<plugin filename="x" name="p"/><link name="b"><collision name="c"><geometry><heightmap><uri>h.png</uri></heightmap></geometry></collision></link>')
    result = convert(text.replace("<link name=\"a\">", "<link name=\"a\">"))
    assert result["dropped"].get("plugin") == 1 and result["dropped"].get("sensor") == 1
    assert any("capsule" in w for w in result["warnings"])
    assert any("heightmap" in w for w in result["warnings"])


def test_several_models_need_a_choice_and_a_world_picks_the_movable_one():
    world = ('<sdf version="1.7"><world name="w"><model name="ground"><static>true</static>' + LINK.format(n="g", pose="0 0 0 0 0 0", geom='<plane><normal>0 0 1</normal><size>10 10</size></plane>') +
             '</model><model name="car">' + LINK.format(n="body", pose="0 0 0 0 0 0", geom=BOX) + '</model></world></sdf>')
    result = convert(world)
    assert result["model"] == "car" and result["models"] == ["ground", "car"]
    two = world.replace("</world>", '<model name="car2">' + LINK.format(n="b", pose="0 0 0 0 0 0", geom=BOX) + "</model></world>")
    with pytest.raises(AssetError) as caught:
        convert(two)
    assert caught.value.code == "SDF_MODEL" and "car2" in str(caught.value)
    assert convert(two, name="car2")["model"] == "car2"
    with pytest.raises(AssetError):
        convert(two, name="nothing")
    assert convert(world, name="ground")["static"] is True


def test_meshes_resolve_inside_the_folder_by_model_uri_and_relative_name():
    geom = '<mesh><uri>model://thing/meshes/a.dae</uri><scale>2 2 2</scale></mesh>'
    files = {"thing/model.sdf": model(LINK.format(n="a", pose="0 0 0 0 0 0", geom=geom), name="thing"), "thing/meshes/a.dae": b"x",
             "thing/model.config": "<model><sdf>model.sdf</sdf></model>"}
    result = s.convert("thing/model.sdf", project(files))
    mesh = ET.fromstring(result["urdf"]).find(".//mesh")
    assert mesh.get("filename") == "meshes/a.dae" and mesh.get("scale") == "2 2 2"
    relative = model(LINK.format(n="a", pose="0 0 0 0 0 0", geom='<mesh><uri>meshes/a.dae</uri></mesh>'))
    assert ET.fromstring(s.convert("thing/m.sdf", project({"thing/m.sdf": relative, "thing/meshes/a.dae": b"x"}))["urdf"]).find(".//mesh").get("filename") == "meshes/a.dae"


@pytest.mark.parametrize("uri", ["file:///etc/passwd", "http://example.com/a.dae", "/etc/passwd", "../../outside.dae", "meshes\\a.dae", "model://../x/a.dae", "model://a"])
def test_a_mesh_uri_cannot_name_a_file_outside_the_approved_folder(uri):
    text = model(LINK.format(n="a", pose="0 0 0 0 0 0", geom=f'<mesh><uri>{uri}</uri></mesh>'))
    try:
        result = convert(text, entry="d/m.sdf")
    except AssetError:
        return
    assert "etc/passwd" not in result["urdf"] and "outside" not in result["urdf"]  # a missing mesh is left out with a warning


@pytest.mark.parametrize("data", [b'<!DOCTYPE s [<!ENTITY a "b">]><sdf/>', "<sdf/>".encode("utf-16"), b"<robot/>", b"<sdf", b"<sdf><world/></sdf>"])
def test_a_dtd_utf16_a_wrong_root_or_no_model_is_refused(data):
    with pytest.raises(AssetError):
        s.convert("m.sdf", project({"m.sdf": data}))


def test_a_loop_of_joints_and_a_missing_link_are_refused_not_looped():
    links = LINK.format(n="a", pose="0 0 0 0 0 0", geom=BOX) + LINK.format(n="b", pose="1 0 0 0 0 0", geom=BOX)
    with pytest.raises(AssetError):
        convert(model(links, '<joint name="j1" type="fixed"><parent>a</parent><child>b</child></joint><joint name="j2" type="fixed"><parent>b</parent><child>a</child></joint>'))
    result = convert(model(links, '<joint name="j" type="fixed"><parent>a</parent><child>nope</child></joint>'))
    assert any("nope" in w for w in result["warnings"])  # the stray joint is dropped, the second root link is fixed on
    with pytest.raises(AssetError):
        s.convert("m.txt", project({"m.txt": "<sdf/>"}))


def test_a_joint_to_the_world_makes_the_model_static():
    result = convert(model(LINK.format(n="a", pose="0 0 0 0 0 0", geom=BOX), '<joint name="w" type="fixed"><parent>world</parent><child>a</child></joint>'))
    assert result["static"] is True

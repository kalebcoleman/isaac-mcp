"""MuJoCo MJCF references: found inside the project folder, and nothing outside it is ever followed."""

import pytest

from isaac_mcp import mjcf_refs as m
from isaac_mcp.project_assets import AssetError
from isaac_mcp.urdf_refs import Project


def project(files, symlinks=()):
    """A project whose files are a dict of relative name -> bytes."""
    data = {k: (v if isinstance(v, bytes) else v.encode()) for k, v in files.items()}

    def read(rel):
        return data[rel]

    return Project(data.keys(), read, "robot", symlinks)


UR5E = """<mujoco model="ur5e"><compiler angle="radian" meshdir="assets" autolimits="true"/>
<asset><mesh name="base" file="base_0.obj"/><mesh name="link" file="link.stl"/></asset>
<worldbody><body name="base"><geom type="mesh" mesh="base"/></body></worldbody></mujoco>"""


def test_a_menagerie_style_model_finds_its_meshes_through_meshdir():
    p = project({"ur5e/ur5e.xml": UR5E, "ur5e/assets/base_0.obj": "o", "ur5e/assets/link.stl": "s"})
    found = m.resolve("ur5e/ur5e.xml", p)
    assert found["problems"] == []
    assert found["mapped"] == {"mesh:base_0.obj": "ur5e/assets/base_0.obj", "mesh:link.stl": "ur5e/assets/link.stl"}
    assert found["files"] == ["ur5e/assets/base_0.obj", "ur5e/assets/link.stl", "ur5e/ur5e.xml"]
    assert found["compiler"] == {"meshdir": "assets", "texturedir": ""}


def test_assetdir_sets_both_folders_unless_one_is_given_alone():
    xml = '<mujoco><compiler assetdir="a" texturedir="t"/><asset><mesh file="m.stl"/><texture file="x.png" type="2d"/></asset></mujoco>'
    p = project({"r.xml": xml, "a/m.stl": "s", "t/x.png": "p"})
    found = m.resolve("r.xml", p)
    assert found["problems"] == [] and set(found["mapped"].values()) == {"a/m.stl", "t/x.png"}
    assert found["compiler"] == {"meshdir": "a", "texturedir": "t"}


def test_includes_are_followed_and_their_meshes_are_found():
    scene = '<mujoco><include file="robot.xml"/><worldbody/></mujoco>'
    robot = '<mujoco><compiler meshdir="meshes"/><asset><mesh file="a.stl"/></asset><include file="nested.xml"/></mujoco>'
    nested = '<mujoco><asset><mesh file="b.stl"/></asset></mujoco>'
    p = project({"s/scene.xml": scene, "s/robot.xml": robot, "s/nested.xml": nested, "s/meshes/a.stl": "a", "s/meshes/b.stl": "b"})
    found = m.resolve("s/scene.xml", p)
    assert found["problems"] == []
    assert set(found["files"]) == {"s/scene.xml", "s/robot.xml", "s/nested.xml", "s/meshes/a.stl", "s/meshes/b.stl"}
    assert found["includes"] == ["s/robot.xml", "s/nested.xml"]


def test_an_include_cycle_ends():
    a = '<mujoco><include file="b.xml"/></mujoco>'
    b = '<mujoco><include file="a.xml"/></mujoco>'
    found = m.resolve("a.xml", project({"a.xml": a, "b.xml": b}))
    assert found["problems"] == [] and found["files"] == ["a.xml", "b.xml"]


@pytest.mark.parametrize("value, code", [
    ("/etc/passwd", "INVALID_PATH"),
    ("..\\..\\secret.stl", "INVALID_PATH"),
    ("file:///etc/passwd", "UNSUPPORTED_URI"),
    ("https://example.test/m.stl", "UNSUPPORTED_URI"),
    ("../../outside.stl", "OUTSIDE_ROOT"),
])
def test_a_reference_that_leaves_the_folder_is_a_problem_not_a_candidate(value, code):
    xml = f'<mujoco><asset><mesh file="{value}"/></asset></mujoco>'
    found = m.resolve("r.xml", project({"r.xml": xml, "outside.stl": "x"}))
    assert [p["code"] for p in found["problems"]] == [code]
    assert found["mapped"] == {} and found["files"] == ["r.xml"]


def test_an_absolute_or_escaping_compiler_folder_is_a_problem():
    for directory, code in (("/etc", "INVALID_PATH"), ("../..", None)):
        xml = f'<mujoco><compiler meshdir="{directory}"/><asset><mesh file="m.stl"/></asset></mujoco>'
        found = m.resolve("r.xml", project({"r.xml": xml, "m.stl": "x"}))
        assert found["problems"], directory
        if code:
            assert found["problems"][0]["code"] == code


def test_a_missing_mesh_is_reported_with_where_it_looked():
    found = m.resolve("a/r.xml", project({"a/r.xml": '<mujoco><compiler meshdir="m"/><asset><mesh file="gone.stl"/></asset></mujoco>'}))
    assert found["problems"][0]["code"] == "MESH_MISSING" and "a/m" in found["problems"][0]["reason"]


def test_a_symlink_on_the_path_is_refused():
    xml = '<mujoco><asset><mesh file="link/m.stl"/></asset></mujoco>'
    found = m.resolve("r.xml", project({"r.xml": xml, "link/m.stl": "x"}, symlinks={"link"}))
    assert [p["code"] for p in found["problems"]] == ["UNSAFE_ENTRY"]


def test_textures_and_height_fields_and_skins_and_models_are_references_too():
    xml = ('<mujoco><asset><texture type="cube" fileup="u.png" filedown="d.png"/><hfield name="h" file="h.png" nrow="1" ncol="1" size="1 1 1 1"/>'
           '<skin file="s.skn"/><model name="sub" file="sub.xml"/></asset></mujoco>')
    files = {"r.xml": xml, "u.png": "1", "d.png": "2", "h.png": "3", "s.skn": "4", "sub.xml": "<mujoco/>"}
    found = m.resolve("r.xml", project(files))
    assert found["problems"] == [] and set(found["files"]) == set(files)


def test_a_dtd_or_entity_is_refused():
    with pytest.raises(AssetError) as caught:
        m.parse(b'<!DOCTYPE mujoco [<!ENTITY x "y">]><mujoco/>', "r.xml")
    assert caught.value.code == "MJCF_XML"


def test_the_wrong_kind_of_xml_is_named():
    with pytest.raises(AssetError) as urdf:
        m.parse(b"<robot name='x'/>", "r.xml")
    assert "assets__import_urdf" in str(urdf.value)
    with pytest.raises(AssetError):
        m.parse(b"<not-closed>", "r.xml")
    with pytest.raises(AssetError):
        m.parse(b"<package/>", "r.xml")


@pytest.mark.parametrize("name, ok", [("ur5e.xml", True), ("a/b/scene.XML", True), ("r.urdf", False), ("r.xacro", False),
                                       ("r.usd", False), ("a/package.xml", False)])
def test_only_a_mujoco_xml_name_is_accepted(name, ok):
    if ok:
        m.check_mjcf_path(name)
    else:
        with pytest.raises(AssetError) as caught:
            m.check_mjcf_path(name)
        assert caught.value.code == "UNSUPPORTED_FORMAT"


def test_closure_result_has_the_shape_the_pack_tools_read(tmp_path):
    root = tmp_path / "ur5e"
    (root / "assets").mkdir(parents=True)
    (root / "ur5e.xml").write_text(UR5E)
    (root / "assets" / "base_0.obj").write_text("o")
    (root / "assets" / "link.stl").write_text("s")
    from isaac_mcp.urdf_refs import project_from_folder

    result = m.closure_result(root / "ur5e.xml", project_from_folder(root), root)
    assert result["mjcf"] is True and "error" not in result and result["problems"] == []
    assert result["assets"] == [str(root / "assets" / "base_0.obj"), str(root / "assets" / "link.stl"), str(root / "ur5e.xml")]
    bad = m.closure_result(root / "ur5e.urdf", project_from_folder(root), root)
    assert "UNSUPPORTED_FORMAT" in bad["error"]


# ---------------------------------------------------------------- review round 1: compiler folders, includes, sub-models


def codes(found):
    return [p["code"] for p in found["problems"]]


@pytest.mark.parametrize("directory, code", [("../../../etc", "OUTSIDE_ROOT"), ("/etc", "INVALID_PATH"), ("C:\\\\x", "UNSUPPORTED_URI")])  # a drive letter looks like a URI scheme
def test_a_compiler_folder_in_an_included_file_is_checked_even_after_the_references_that_use_it(directory, code):
    """MuJoCo expands includes in place and applies the final <compiler> folder to every mesh, so an included file can
    point meshdir anywhere: that must be refused, not recorded."""
    files = {"s/m.xml": '<mujoco><asset><mesh file="x.stl"/></asset><include file="i.xml"/></mujoco>',
             "s/i.xml": f'<mujoco><compiler meshdir="{directory}"/></mujoco>', "s/x.stl": "x"}
    found = m.resolve("s/m.xml", project(files))
    assert code in codes(found), found["problems"]


def test_a_folder_that_is_overridden_later_is_still_checked():
    files = {"m.xml": '<mujoco><compiler meshdir="/etc"/><include file="i.xml"/><asset><mesh file="x.stl"/></asset></mujoco>',
             "i.xml": '<mujoco><compiler meshdir="assets"/></mujoco>', "assets/x.stl": "x"}
    found = m.resolve("m.xml", project(files))
    assert "INVALID_PATH" in codes(found)  # the hostile value never "wins", but it is not accepted either


def test_the_last_compiler_folder_wins_and_applies_to_references_before_it():
    files = {"m.xml": '<mujoco><compiler meshdir="a"/><asset><mesh file="x.stl"/></asset><include file="i.xml"/></mujoco>',
             "i.xml": '<mujoco><compiler meshdir="b"/></mujoco>', "a/x.stl": "a", "b/x.stl": "b"}
    found = m.resolve("m.xml", project(files))
    assert found["problems"] == [] and found["mapped"]["mesh:x.stl"] == "b/x.stl" and found["compiler"]["meshdir"] == "b"
    assert "a/x.stl" not in found["files"]


@pytest.mark.parametrize("name", ["i.txt", "i.inc", "i.mjcf", "i"])
def test_an_include_is_scanned_whatever_its_name(name):
    files = {"m.xml": f'<mujoco><include file="{name}"/></mujoco>',
             name: '<mujocoinclude><asset><mesh file="/etc/passwd"/></asset></mujocoinclude>'}
    found = m.resolve("m.xml", project(files))
    assert "INVALID_PATH" in codes(found) and name in found["files"], found


def test_a_mujocoinclude_root_is_allowed_for_an_included_file_only():
    files = {"m.xml": '<mujoco><include file="i.xml"/></mujoco>', "i.xml": '<mujocoinclude><asset/></mujocoinclude>'}
    assert m.resolve("m.xml", project(files))["problems"] == []
    with pytest.raises(AssetError):
        m.parse(b"<mujocoinclude/>", "m.xml")  # the model you start from must be <mujoco>
    assert m.parse(b"<mujocoinclude/>", "i.xml", included=True).tag == "mujocoinclude"


def test_an_included_file_that_is_not_xml_is_a_problem_not_a_crash():
    found = m.resolve("m.xml", project({"m.xml": '<mujoco><include file="i.bin"/></mujoco>', "i.bin": b"\x00\x01\x02 not xml"}))
    assert "MJCF_XML" in codes(found)


def test_a_sub_model_is_resolved_against_its_own_folder_and_its_own_includes():
    files = {"m.xml": '<mujoco><asset><model name="s" file="sub/sub.xml"/></asset></mujoco>',
             "sub/sub.xml": '<mujoco><compiler meshdir="meshes"/><asset><mesh file="p.stl"/></asset></mujoco>', "sub/meshes/p.stl": "p"}
    found = m.resolve("m.xml", project(files))
    assert found["problems"] == [] and "sub/meshes/p.stl" in found["files"] and "sub/sub.xml" in found["files"]


def test_a_sub_model_cannot_name_a_file_outside_the_folder_either():
    files = {"m.xml": '<mujoco><asset><model name="s" file="sub/sub.xml"/></asset></mujoco>',
             "sub/sub.xml": '<mujoco><asset><mesh file="/etc/passwd"/><mesh file="../../up.stl"/></asset></mujoco>'}
    found = m.resolve("m.xml", project(files))
    assert {"INVALID_PATH", "OUTSIDE_ROOT"} <= set(codes(found))


def test_a_model_that_includes_itself_and_a_deep_chain_end_cleanly():
    own = m.resolve("m.xml", project({"m.xml": '<mujoco><asset><model name="s" file="m.xml"/></asset></mujoco>'}))
    assert isinstance(own["problems"], list)  # no recursion error
    chain = {f"f{i}.xml": f'<mujoco><include file="f{i + 1}.xml"/></mujoco>' for i in range(40)}
    chain["f40.xml"] = "<mujoco/>"
    deep = m.resolve("f0.xml", project(chain))
    assert "LIMIT" in codes(deep)


def test_the_resolver_returns_every_parsed_tree_for_the_kit_side():
    files = {"m.xml": '<mujoco><include file="i.xml"/><worldbody><body><joint/></body></worldbody></mujoco>',
             "i.xml": '<mujocoinclude><worldbody><body><joint/><joint/></body></worldbody></mujocoinclude>'}
    found = m.resolve("m.xml", project(files))
    assert set(found["trees"]) == {"m.xml", "i.xml"} and found["includes"] == ["i.xml"]

"""Asset import hardening: encoded references, bounded xacro expansion, and SDF poses and joints."""

import math
import os
import time

import pytest

from isaac_mcp import asset_formats, mesh_refs, sdf_urdf, urdf_refs, xacro_lite
from isaac_mcp.project_assets import AssetError


def _project(tree, tmp_path=None):
    files = {k: v.encode() if isinstance(v, str) else v for k, v in tree.items()}
    return urdf_refs.Project(files.keys(), lambda rel: files[rel], "root")


@pytest.mark.parametrize("uri", ["%2e%2e/x.bin", "..%2f..%2fetc/passwd", "%2Fetc%2Fpasswd", "%5c..%5cx", "%252e%252e/x.bin", "a%00b.bin", "file%3A%2F%2F%2Fetc/passwd"])
def test_an_encoded_gltf_reference_that_leaves_the_folder_is_refused(uri):
    project = _project({"m.gltf": '{"buffers": [{"uri": "%s"}]}' % uri})
    with pytest.raises(AssetError) as error:
        mesh_refs.check("m.gltf", project)
    assert error.value.code == "MESH_REFERENCE"


def test_an_encoded_name_inside_the_folder_still_works():
    project = _project({"sub/m.gltf": '{"buffers": [{"uri": "my%20file.bin"}, {"uri": "%2e%2e/up.bin"}]}', "sub/my file.bin": "x", "up.bin": "x"})
    found = mesh_refs.check("sub/m.gltf", project)
    assert set(found["files"]) == {"sub/m.gltf", "sub/my file.bin", "up.bin"} and found["materials"]


def test_an_absent_encoded_file_inside_the_folder_only_loses_materials():
    found = mesh_refs.check("m.gltf", _project({"m.gltf": '{"images": [{"uri": "no%20such.png"}]}'}))
    assert found["materials"] is False and found["absent"] == ["no%20such.png"]


def _xacro(body, **more):
    head = '<robot name="r" xmlns:xacro="http://www.ros.org/wiki/xacro">'
    return {"a.xacro": head + body + "</robot>", **more}


def _expand(tree, args=None):
    return xacro_lite.expand("a.xacro", _project(tree), args)


def test_a_folder_named_like_a_substitution_cannot_loop_forever():
    tree = {"$(dirname)/a.xacro": '<robot name="r" xmlns:xacro="http://www.ros.org/wiki/xacro"><link name="${$(dirname)}"/></robot>'}
    started = time.monotonic()
    with pytest.raises(AssetError):
        xacro_lite.expand("$(dirname)/a.xacro", _project(tree))
    assert time.monotonic() - started < 5


def test_macro_output_is_bounded_in_bytes_not_only_elements():
    calls = "".join('<xacro:m/>' for _ in range(200))
    body = """<xacro:property name="big" value="${'a' * 900000}"/><xacro:macro name="m"><link name="${big}"/></xacro:macro>""" + calls
    with pytest.raises(AssetError) as error:
        _expand(_xacro(body))
    assert error.value.code == "LIMIT"


@pytest.mark.parametrize("expression", ["'%900000000d' % 1", "'%.900000000f' % 1.0", "'%*d' % (900000000, 1)", "','.join(['aaaa' * 1000] * 1000000)", "('a' * 900000).replace('a', 'bbbbbb')"])
def test_a_huge_string_is_refused_before_it_is_built(expression):
    started = time.monotonic()
    with pytest.raises(AssetError) as error:
        _expand(_xacro('<link name="${%s}"/>' % expression.replace('"', "'")))
    assert error.value.code in ("LIMIT", "XACRO_EVAL") and time.monotonic() - started < 2


def test_adding_strings_is_bounded_too():
    long = "+".join(["big"] * 100)
    with pytest.raises(AssetError) as error:
        _expand(_xacro("""<xacro:property name="big" value="${'a' * 900000}"/><link name="${%s}"/>""" % long))
    assert error.value.code == "LIMIT"


def test_a_file_included_many_times_is_parsed_once():
    parses = []
    real = xacro_lite.ET.fromstring
    tree = _xacro('<xacro:include filename="b.xacro"/>' * 20, **{"b.xacro": '<robot xmlns:xacro="http://www.ros.org/wiki/xacro"><link name="l"/></robot>'})
    try:
        xacro_lite.ET.fromstring = lambda data: parses.append(1) or real(data)
        _expand(tree)
    finally:
        xacro_lite.ET.fromstring = real
    assert len(parses) == 2  # a.xacro and b.xacro


@pytest.mark.parametrize("expression", ["-'a'", "1 in 2", "(10**12)**1000"])
def test_a_bad_expression_is_a_xacro_error_not_a_raw_one(expression):
    with pytest.raises(AssetError):
        _expand(_xacro('<link name="${%s}"/>' % expression))


def test_deep_xml_and_a_self_referring_default_are_xacro_errors():
    deep = "<a>" * 5000 + "</a>" * 5000
    with pytest.raises(AssetError):
        _expand(_xacro(deep))
    with pytest.raises(AssetError):
        _expand(_xacro('<xacro:arg name="a" default="$(arg a)"/><link name="$(arg a)"/>'))


def test_deep_yaml_is_a_xacro_error():
    text = "".join(" " * i + "k:\n" for i in range(1400))
    with pytest.raises(AssetError):
        xacro_lite.load_yaml_text(text)


def _sdf(joint="", link_pose="", extra_links="", model_extra=""):
    return f"""<sdf version="1.9"><model name="m">
      <link name="a">{link_pose}</link><link name="b"/>{extra_links}{joint}{model_extra}
    </model></sdf>"""


def _convert(text, **more):
    return sdf_urdf.convert("r.sdf", _project({"r.sdf": text, **more}), None)


def test_a_pose_in_degrees_is_converted():
    assert sdf_urdf._pose("0 0 0 0 0 90", degrees=True)[1][0] == pytest.approx(1.0)  # 90 degrees of yaw sends x to y
    assert sdf_urdf._pose("0 0 0 0 0 1.5707963267948966")[1][0] == pytest.approx(1.0)


def test_a_quaternion_pose_is_read_as_one():
    half = math.sqrt(0.5)
    matrix = sdf_urdf._pose(f"1 2 3 0 0 {half} {half}", rotation_format="quat_xyzw")
    assert matrix[1][0] == pytest.approx(1.0) and [matrix[i][3] for i in range(3)] == [1.0, 2.0, 3.0]


@pytest.mark.parametrize(("text", "fmt"), [("0 0 0 0 0 0 1", None), ("0 0 0 0 0 0", "quat_xyzw"), ("0 0 0 0 0 0", "other")])
def test_a_pose_of_the_wrong_shape_is_refused(text, fmt):
    with pytest.raises(AssetError):
        sdf_urdf._pose(text, rotation_format=fmt)


def _joint(kind, limit=""):
    return f'<joint name="j" type="{kind}"><parent>a</parent><child>b</child><axis><xyz>1 0 0</xyz>{limit}</axis></joint>'


def test_a_slider_with_no_limit_stays_open_and_says_so():
    out = _convert(_sdf(_joint("prismatic")))
    assert 'lower="-1000"' in out["urdf"] and 'upper="1000"' in out["urdf"] and any("slides without a limit" in w for w in out["warnings"])


def test_a_hinge_limited_on_one_side_keeps_its_limit_and_says_so():
    out = _convert(_sdf(_joint("revolute", "<limit><upper>0.5</upper></limit>")))
    assert 'type="revolute"' in out["urdf"] and 'upper="0.5"' in out["urdf"] and any("one side only" in w for w in out["warnings"])


def test_a_hinge_with_no_limit_is_continuous():
    assert 'type="continuous"' in _convert(_sdf(_joint("revolute")))["urdf"]


def test_an_axis_in_another_frame_warns():
    joint = '<joint name="j" type="revolute"><parent>a</parent><child>b</child><axis><xyz expressed_in="other">1 0 0</xyz></axis></joint>'
    assert any("frame 'other'" in w for w in _convert(_sdf(joint))["warnings"])


def test_an_axis_in_the_model_frame_turns_with_the_joint():
    joint = ('<joint name="j" type="revolute"><parent>a</parent><child>b</child><pose>0 0 0 0 0 1.5707963267948966</pose>'
             '<axis><xyz expressed_in="__model__">1 0 0</xyz><limit><lower>-1</lower><upper>1</upper></limit></axis></joint>')
    urdf = _convert(_sdf(joint))["urdf"]
    assert 'axis xyz="1 0 0"' not in urdf


def test_a_config_with_a_dtd_is_not_followed():
    files = {"m/model.config": '<!DOCTYPE x [<!ENTITY a "b">]><model><sdf>other.sdf</sdf></model>', "m/other.sdf": "<sdf/>", "m/model.sdf": "<sdf/>"}
    found = asset_formats.candidates(set(files), lambda rel: files[rel].encode(), "m")
    assert found[0]["entry"] == "m/model.sdf"


def test_looking_through_a_folder_stops_reading_at_a_limit(monkeypatch):
    monkeypatch.setattr(asset_formats, "MAX_SNAPSHOT_READ", 10)
    reads = []
    files = {f"{i}.xml": b"<mujoco/>" * 4 for i in range(20)}

    def read(rel):
        reads.append(rel)
        return files[rel]

    asset_formats.candidates(set(files), read)
    assert len(reads) < 5


def test_a_folder_at_the_file_limit_is_refused_before_the_converted_file_is_added(tmp_path, monkeypatch):
    from isaac_mcp import project_assets

    source = tmp_path / "root"
    source.mkdir()
    (source / "a.xacro").write_text("<robot/>")
    (source / "b.txt").write_text("x")
    monkeypatch.setattr(project_assets, "MAX_FILES", 2)
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    broker = project_assets.Broker("")
    broker.staging = staging
    fd = project_assets.open_dir(source)
    try:
        broker.roots = {"root": (source, project_assets.signature(os.fstat(fd))[:2])}
    finally:
        os.close(fd)
    root_id = "root"

    def generate(path, files, read):
        return dict(entry="a.generated.urdf", data=b"<robot/>", info={})

    with pytest.raises(AssetError) as error:
        broker.snapshot(root_id, "a.xacro", kind="xacro", generate=generate)
    assert error.value.code == "LIMIT" and "smaller folder" in str(error.value)


def test_a_mesh_in_an_included_model_is_found_beside_that_model():
    included = '<sdf version="1.9"><model name="inner"><link name="a"><visual name="v"><geometry><mesh><uri>meshes/m.stl</uri></mesh></geometry></visual></link></model></sdf>'
    files = {"world.sdf": '<sdf version="1.9"><world name="w"><include><uri>model://inner</uri></include></world></sdf>', "inner/model.sdf": included, "inner/meshes/m.stl": "x"}
    out = sdf_urdf.convert("world.sdf", _project(files), None)
    assert 'filename="inner/meshes/m.stl"' in out["urdf"]
    assert {"world.sdf", "inner/model.sdf"} <= set(out["files"])


@pytest.mark.parametrize("expression", ["'%(a)999999999s' % {'a':1}", "'%(a).999999999f' % {'a':1.0}"])
def test_a_wide_format_with_a_mapping_key_is_refused_before_it_is_built(expression):
    started = time.monotonic()
    with pytest.raises(AssetError) as error:
        _expand(_xacro('<link name="${%s}"/>' % expression))
    assert error.value.code == "LIMIT" and time.monotonic() - started < 0.1


@pytest.mark.parametrize("rpy", [(0, math.pi / 2, 0.5), (0.3, math.pi / 2, 0.5), (0, -math.pi / 2, 1.0), (0.4, -0.2, 2.0)])
def test_a_quaternion_pose_is_right_at_a_90_degree_pitch(rpy):
    roll, pitch, yaw = rpy
    cr, sr, cp, sp, cy, sy = math.cos(roll / 2), math.sin(roll / 2), math.cos(pitch / 2), math.sin(pitch / 2), math.cos(yaw / 2), math.sin(yaw / 2)
    quat = (sr * cp * cy - cr * sp * sy, cr * sp * cy + sr * cp * sy, cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy)
    matrix = sdf_urdf._pose("0 0 0 " + " ".join(repr(v) for v in quat), rotation_format="quat_xyzw")
    expected = sdf_urdf._pose("0 0 0 " + " ".join(repr(float(v)) for v in rpy))
    for row, want in zip(matrix[:3], expected[:3]):
        assert row[:3] == pytest.approx(want[:3], abs=1e-9)


@pytest.mark.parametrize(("name", "target"), [("100%.png", "100%.png"), ("ok%25.png", "ok%25.png"), ("tex%20a.png", "tex%20a.png"), ("sub/a.png", "sub/a.png")])
def test_a_percent_in_a_plain_material_path_is_an_ordinary_character(name, target):
    assert urdf_refs.mesh_reference("m.mtl", name) == target


@pytest.mark.parametrize("name", ["%2e%2e/x.png", "..%2fx.png", "%2Fetc%2Fpasswd", "../x.png", "/etc/passwd", "a\\b.png"])
def test_a_plain_material_path_that_would_leave_the_folder_is_still_refused(name):
    assert urdf_refs.mesh_reference("m.mtl", name) is None


def test_a_dae_reference_is_a_uri():
    assert urdf_refs.mesh_reference("m.dae", "my%20tex.png") == "my tex.png"
    assert urdf_refs.mesh_reference("m.dae", "%2e%2e/x.png") is None


@pytest.mark.parametrize("text", ["%(a(b))999999999s", "%(a)999999999s", "%999999999d", "%.999999999f", "%(a(b)).999999999f", "%*d", "%.*f", "%-0 +#99999d", "ok %d then %(k)5000s"])
def test_a_format_too_wide_is_seen_however_it_is_written(text):
    assert xacro_lite._format_too_wide(text)


@pytest.mark.parametrize("text", ["%d", "%5.2f", "%(a)10s", "%(a(b))999s", "100%", "%%", "%%999999d is not a width", "no format", "%", "%(unclosed"])
def test_an_ordinary_format_is_left_alone(text):
    assert not xacro_lite._format_too_wide(text)


def test_a_nested_key_cannot_build_a_huge_string():
    started = time.monotonic()
    with pytest.raises(AssetError) as error:
        _expand(_xacro("""<link name="${'%(a(b))999999999s' % {'a(b)':1}}"/>"""))
    assert error.value.code in ("LIMIT", "UNSUPPORTED_XACRO") and time.monotonic() - started < 0.1


def test_a_twice_encoded_plain_path_is_refused():
    assert urdf_refs.mesh_reference("m.mtl", "%252e%252e/x.png") is None

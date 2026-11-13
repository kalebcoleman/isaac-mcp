"""The restricted xacro expander: the language it reads, and the code it refuses to run."""

import time
import xml.etree.ElementTree as ET

import pytest

from twinsmith import xacro_lite as x
from twinsmith.project_assets import AssetError
from twinsmith.urdf_refs import Project

NS = 'xmlns:xacro="http://www.ros.org/wiki/xacro"'


def project(files, name="pkg"):
    data = {k: (v if isinstance(v, bytes) else v.encode()) for k, v in files.items()}
    return Project(data.keys(), lambda rel: data[rel], name)


def robot(body, extra=""):
    return f'<robot name="r" {NS}>{extra}{body}</robot>'


def expand(files, args=None, entry="r.urdf.xacro"):
    return ET.fromstring(x.expand(entry, project(files), args)["urdf"])


def one(body, args=None, extra=""):
    return expand({"r.urdf.xacro": robot(body, extra)}, args)


def refused(body, code=None, files=None, args=None):
    files = dict(files or {}, **{"r.urdf.xacro": robot(body)})
    with pytest.raises(AssetError) as caught:
        x.expand("r.urdf.xacro", project(files), args)
    if code:
        assert caught.value.code == code, str(caught.value)
    return str(caught.value)


def test_properties_arithmetic_math_and_text_substitution():
    out = one('<xacro:property name="w" value="0.5"/><xacro:property name="r" value="${w*2}"/>'
              '<link name="l_${int(r)}"><visual><geometry><box size="${r} ${math.pi/2} ${max(1, 2)}"/></geometry></visual></link>')
    link = out.find("link")
    assert link.get("name") == "l_1"
    assert link.find("visual/geometry/box").get("size") == "1.0 1.5707963267948966 2"


def test_a_macro_with_defaults_blocks_and_scope():
    out = one('<xacro:macro name="arm" params="name length:=2 *origin">'
              '<link name="${name}"/><joint name="j_${name}" type="fixed"><parent link="base"/><child link="${name}"/>'
              '<xacro:insert_block name="origin"/></joint><xacro:property name="inner" value="1"/></xacro:macro>'
              '<link name="base"/>'
              '<xacro:arm name="a"><origin xyz="0 0 ${length}"/></xacro:arm>'
              '<xacro:arm name="b" length="5"><origin xyz="0 0 ${length}"/></xacro:arm>')
    joints = {j.get("name"): j.find("origin").get("xyz") for j in out.findall("joint")}
    assert joints == {"j_a": "0 0 2", "j_b": "0 0 5"}
    assert [l.get("name") for l in out.findall("link")] == ["base", "a", "b"]


def test_if_unless_and_args_with_defaults_and_overrides():
    body = ('<xacro:arg name="side" default="left"/><xacro:arg name="n" default="2"/>'
            '<xacro:if value="${\'$(arg side)\' == \'left\'}"><link name="l"/></xacro:if>'
            '<xacro:unless value="$(arg n)"><link name="never"/></xacro:unless>'
            '<xacro:if value="${$(arg n) > 1}"><link name="many"/></xacro:if>')
    assert [l.get("name") for l in one(body).findall("link")] == ["l", "many"]
    assert [l.get("name") for l in one(body, {"side": "right", "n": 1}).findall("link")] == []


def test_includes_by_relative_path_and_by_package_and_yaml_loading():
    files = {
        "pkg/package.xml": "<package><name>pkg</name></package>",
        "pkg/urdf/main.xacro": robot('<xacro:include filename="$(find pkg)/urdf/parts.xacro"/><xacro:include filename="more.xacro"/>'
                                     '<xacro:property name="cfg" value="${xacro.load_yaml(\'$(find pkg)/config/p.yaml\')}"/>'
                                     '<link name="x"><inertial><mass value="${cfg[\'mass\']}"/><origin xyz="${cfg[\'p\'][0]} ${cfg[\'p\'][2]} 0"/></inertial></link>'),
        "pkg/urdf/parts.xacro": robot('<link name="from_parts"/>'),
        "pkg/urdf/more.xacro": robot('<link name="from_more"/>'),
        "pkg/config/p.yaml": "mass: 3.5  # kg\np: [1, 2, 3]\nlimits:\n  lo: !degrees 180\n",
    }
    out = expand(files, entry="pkg/urdf/main.xacro")
    assert [l.get("name") for l in out.findall("link")] == ["from_parts", "from_more", "x"]
    assert out.find("link[@name='x']/inertial/mass").get("value") == "3.5"
    assert out.find("link[@name='x']/inertial/origin").get("xyz") == "1 3 0"
    assert x.load_yaml_text("a:\n  b: !degrees 180\n")["a"]["b"] == pytest.approx(3.14159265)


def test_an_inherited_macro_parameter_and_a_self_referencing_property():
    out = one('<xacro:property name="n" value="1"/><xacro:property name="n" value="${n + 1}"/>'
              '<xacro:property name="tag" value="top"/>'
              '<xacro:macro name="m" params="tag:=^|fallback"><link name="${tag}"/></xacro:macro><xacro:m/>')
    assert out.find("link").get("name") == "top"
    assert one('<xacro:property name="n" value="1"/><xacro:property name="n" value="${n + 1}"/><link name="l${n}"/>').find("link").get("name") == "l2"


def test_a_real_expression_set_matches_python_semantics():
    cases = {"${3 if 1 &lt; 2 else 4}": "3", "${'ab'.upper()}": "AB", "${[1, 2, 3][1]}": "2", "${10 // 3}": "3", "${-pi &lt; 0 and not False}": "true",
             "${round(2.567, 1)}": "2.6", "${len('abc')}": "3", "${'x' in 'xyz'}": "true"}
    for source, expected in cases.items():
        assert one(f'<link name="l" v="{source}"/>').find("link").get("v") == expected, source


@pytest.mark.parametrize("expression", [
    "${__import__('os').system('id')}", "${open('/etc/passwd').read()}", "${().__class__}", "${''.__class__.__mro__}",
    "${(lambda: 1)()}", "${[x for x in range(3)]}", "${exec('1')}", "${eval('1')}", "${getattr(1, 'real')}", "${math.__dict__}",
    "${'{0.__class__}'.format(1)}", "${10 ** 10 ** 10}", "${'a' * 10 ** 9}", "${ 9 ** 9999 }", "${undefined_name}", "${1 +}", "${f'{1}'}",
])
def test_code_and_abuse_in_expressions_is_refused(expression):
    start = time.monotonic()
    with pytest.raises(AssetError):
        x.expand("r.urdf.xacro", project({"r.urdf.xacro": robot(f'<link name="l" v="{expression}"/>')}))
    assert time.monotonic() - start < 3


@pytest.mark.parametrize("filename", ["/etc/passwd", "../../etc/passwd", "http://example.com/a.xacro", "file:///etc/passwd", "a\\\\b.xacro", "$(env HOME)/x", "$(find nope)/x.xacro", "missing.xacro"])
def test_an_include_cannot_leave_the_approved_folder(filename):
    with pytest.raises(AssetError):
        x.expand("d/r.urdf.xacro", project({"d/r.urdf.xacro": robot(f'<xacro:include filename="{filename}"/>')}))


def test_yaml_and_commands_cannot_read_outside_the_folder():
    for source in ["${xacro.load_yaml('/etc/passwd')}", "${xacro.load_yaml('../../x.yaml')}", "$(env HOME)", "$(anon x)", "$(cwd)"]:
        with pytest.raises(AssetError):
            x.expand("r.urdf.xacro", project({"r.urdf.xacro": robot(f'<link name="l" v="{source}"/>')}))
    assert one('<link name="l" v="$(optenv HOME fallback)"/>').find("link").get("v") == "fallback"  # never reads the real environment


def test_runaway_macros_and_includes_end_with_a_limit_not_a_hang():
    start = time.monotonic()
    assert refused('<xacro:macro name="a" params=""><xacro:a/></xacro:macro><xacro:a/>', "LIMIT")
    levels = "".join(f'<xacro:macro name="m{i}" params=""><xacro:m{i + 1}/><xacro:m{i + 1}/></xacro:macro>' for i in range(40))
    assert refused(levels + '<xacro:macro name="m40" params=""><link name="l"/></xacro:macro><xacro:m0/>', "LIMIT")
    assert refused('<xacro:include filename="r.urdf.xacro"/>', "LIMIT")
    assert time.monotonic() - start < 20


def test_unsupported_features_are_named_not_skipped():
    refused('<xacro:element name="x"/>', "UNSUPPORTED_XACRO")
    refused('<xacro:nosuch/>', "UNSUPPORTED_XACRO")
    refused('<link name="$(arg missing)"/>', "XACRO_ARG")


def test_a_dtd_utf16_or_wrong_root_is_refused():
    for data in ('<!DOCTYPE r [<!ENTITY a "b">]><robot/>'.encode(), '<robot/>'.encode("utf-16"), b"<mujoco/>"):
        with pytest.raises(AssetError):
            x.expand("r.urdf.xacro", project({"r.urdf.xacro": data}))


def test_the_output_has_no_xacro_left_and_the_urdf_check_accepts_it():
    from twinsmith import urdf_refs

    text = x.expand("r.urdf.xacro", project({"r.urdf.xacro": robot('<link name="a"><visual><geometry><mesh filename="m.stl"/></geometry></visual></link>')}))["urdf"]
    assert "xacro" not in text and urdf_refs.parse(text.encode()).tag == "robot"


def test_every_missing_argument_is_named_at_once_with_an_example():
    body = '<xacro:arg name="a" default="1"/><link name="$(arg x)_$(arg y)_$(arg a)"/>'
    with pytest.raises(AssetError) as caught:
        x.expand("r.urdf.xacro", project({"r.urdf.xacro": robot(body)}))
    assert caught.value.code == "XACRO_ARG" and caught.value.details["needs_args"] == ["x", "y"]
    assert 'xacro_args={"x": "...", "y": "..."}' in str(caught.value)
    assert ET.fromstring(x.expand("r.urdf.xacro", project({"r.urdf.xacro": robot(body)}), {"x": 1, "y": 2})["urdf"]).find("link").get("name") == "1_2_1"


def test_macro_parameters_with_quoted_defaults_that_hold_spaces():
    body = ('<xacro:macro name="m" params="name xyz:=\'0 0 0\' prefix:=\'\' label:=&quot;a b&quot; *origin">'
            '<link name="${prefix}${name}" xyz="${xyz}" label="${label}"/></xacro:macro>'
            '<xacro:m name="x"><origin/></xacro:m><xacro:m name="y" xyz="1 2 3" prefix="p_"><origin/></xacro:m>')
    links = [(l.get("name"), l.get("xyz"), l.get("label")) for l in one(body).findall("link")]
    assert links == [("x", "0 0 0", "a b"), ("p_y", "1 2 3", "a b")]


def test_a_double_star_parameter_takes_all_the_remaining_elements():
    body = ('<xacro:macro name="m" params="name **content"><link name="${name}"><xacro:insert_block name="content"/></link></xacro:macro>'
            '<xacro:m name="x"><visual/><collision/></xacro:m>')
    link = one(body).find("link")
    assert [c.tag for c in link] == ["visual", "collision"]


def test_yaml_mappings_can_be_read_with_dots_as_xacro_does():
    files = {"r.urdf.xacro": robot('<xacro:property name="c" value="${xacro.load_yaml(\'c.yaml\')}"/><link name="l" v="${c.origin.rpy[1]}" w="${c[\'origin\'][\'xyz\']}"/>'),
             "c.yaml": "origin:\n  rpy: [1, 2, 3]\n  xyz: x\n"}
    link = expand(files).find("link")
    assert (link.get("v"), link.get("w")) == ("2", "x")
    with pytest.raises(AssetError):
        expand({"r.urdf.xacro": robot('<xacro:property name="c" value="${xacro.load_yaml(\'c.yaml\')}"/><link name="l" v="${c.__class__}"/>'), "c.yaml": "a: 1\n"})

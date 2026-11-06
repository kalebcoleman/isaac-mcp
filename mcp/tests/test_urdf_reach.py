"""The own-URDF reach task refuses a wrong path or link before the scene is built, and starts each joint inside its limits."""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("urdf_reach_info", Path(__file__).resolve().parents[2] / "examples/urdf_reach/urdf_info.py")
urdf_info = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(urdf_info)

URDF = """<robot name="r">
  <link name="base"/><link name="a"/><link name="b"/><link name="tip"/><link name="finger"/>
  <joint name="j1" type="revolute"><parent link="base"/><child link="a"/><limit lower="-1" upper="1"/></joint>
  <joint name="j2" type="revolute"><parent link="a"/><child link="b"/><limit lower="0.5" upper="1.5"/></joint>
  <joint name="wheel" type="continuous"><parent link="b"/><child link="finger"/></joint>
  <joint name="copy" type="revolute"><parent link="a"/><child link="finger"/><mimic joint="j1"/><limit lower="-1" upper="1"/></joint>
  <joint name="fixed" type="fixed"><parent link="b"/><child link="tip"/></joint>
</robot>"""


@pytest.fixture
def urdf(tmp_path):
    path = tmp_path / "r.urdf"
    path.write_text(URDF)
    return str(path)


def test_it_finds_driven_joints_and_skips_fixed_and_mimic(urdf):
    info = urdf_info.read(urdf, "tip")
    assert info["joints"] == ["j1", "j2", "wheel"] and "tip" in info["links"]


def test_a_start_pose_stays_inside_each_joints_limits(urdf):
    assert urdf_info.start_pose(urdf_info.read(urdf, "tip")) == dict(j1=0.0, j2=1.0, wheel=0.0)


@pytest.mark.parametrize(("path", "link", "says"), [("", "tip", "Set env.urdf_path"), ("relative.urdf", "tip", "not an absolute path")])
def test_a_missing_path_is_refused(path, link, says):
    with pytest.raises(ValueError, match=says):
        urdf_info.read(path, link)


def test_a_wrong_link_lists_the_real_ones(urdf):
    with pytest.raises(ValueError, match="base, a, b, tip, finger"):
        urdf_info.read(urdf, "nope")


def test_another_xml_file_is_not_a_urdf(tmp_path):
    path = tmp_path / "m.xml"
    path.write_text("<mujoco/>")
    with pytest.raises(ValueError, match="not a URDF"):
        urdf_info.read(str(path), "tip")


def test_no_driven_joints_is_refused(tmp_path):
    path = tmp_path / "r.urdf"
    path.write_text('<robot name="r"><link name="tip"/></robot>')
    with pytest.raises(ValueError, match="no driven joints"):
        urdf_info.read(str(path), "tip")

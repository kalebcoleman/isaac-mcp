"""Small fixes from the pick-and-place wishlist run (no Kit)."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import UsdPhysics
from test_scene_advanced import advanced  # noqa: F401 - the fixtures
from test_scene_usd import scene  # noqa: F401


def test_joints_inspect_flags_a_joint_with_no_drive(advanced):
    """A gripper's second finger had no authored drive, so a position target did nothing and nothing said so."""
    stage = advanced.s.stage()
    advanced.s.create(stage, "/World/Arm", "Xform")
    for name, driven in (("finger1", True), ("finger2", False)):
        joint = UsdPhysics.PrismaticJoint.Define(stage, f"/World/Arm/{name}")
        if driven:
            drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "linear")
            drive.CreateStiffnessAttr(400.0)
            drive.CreateDampingAttr(80.0)
            drive.CreateMaxForceAttr(7.2)
    result = advanced.execute("sim.joints.inspect", stage, dict(path="/World/Arm"))
    flags = {j["path"]: j.get("no_drive", False) for j in result["joints"]}
    assert flags == {"/World/Arm/finger1": False, "/World/Arm/finger2": True}
    assert result["joints_without_drive"] == ["/World/Arm/finger2"]
    assert "sim__joints__gains" in result["drive_note"]


def test_joints_inspect_adds_nothing_when_every_joint_has_a_drive(advanced):
    stage = advanced.s.stage()
    advanced.s.create(stage, "/World/Arm", "Xform")
    joint = UsdPhysics.RevoluteJoint.Define(stage, "/World/Arm/j1")
    UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "angular").CreateStiffnessAttr(10.0)
    result = advanced.execute("sim.joints.inspect", stage, dict(path="/World/Arm"))
    assert "joints_without_drive" not in result and "no_drive" not in result["joints"][0]

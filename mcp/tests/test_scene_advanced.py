"""Real USD authoring tests; sensor/physics runtime tests live in evidence scripts."""

import os
import sys
from types import SimpleNamespace
import pytest
from test_scene_usd import scene
from isaac_mcp.project_assets import AssetError
from sim_tools.advanced import Advanced
from pxr import Usd, UsdPhysics, UsdGeom, UsdShade


@pytest.fixture
def advanced(scene):
    return Advanced(scene, lambda *a: None)


def test_unqualified_sensor_build_has_no_stage_effect(advanced, monkeypatch):
    from sim_tools import runtime

    monkeypatch.setattr(
        runtime,
        "runtime_identity",
        lambda: {
            "qualified_build": False,
            "release": "6.0.0",
            "issues": ["full build missing"],
        },
    )
    stage = advanced.s.stage()
    before = stage.GetSessionLayer().ExportToString()
    for tool in ("sim.camera.create", "sim.lidar.create"):
        with pytest.raises(AssetError, match="validated native"):
            advanced.execute(tool, stage, dict(path="/World/Sensor"))
        assert stage.GetSessionLayer().ExportToString() == before


def test_camera_requires_owned_cpu_data_and_preserves_existing_output(
    advanced, monkeypatch
):
    import numpy as np
    from PIL import Image

    monkeypatch.setitem(
        sys.modules,
        "isaacsim.core.version",
        SimpleNamespace(get_version=lambda: ("6.2.0",)),
    )
    stage = advanced.s.stage()
    UsdGeom.Camera.Define(stage, "/World/Camera")
    advanced.invalidate()
    pixels = np.full((64, 96, 4), 42, dtype=np.uint8)

    class Annotator:
        def get_data(self, *, device, do_array_copy):
            assert device == "cpu" and do_array_copy
            return pixels

    advanced.sensors["/World/Camera"] = (
        "camera",
        dict(annotator=Annotator(), resolution=(64, 96), adapter="test-owned-cpu"),
    )
    args = dict(path="/World/Camera", filename="capture.png")
    result = advanced.execute("sim.camera.capture", advanced.s.stage(), args)
    assert (result["width"], result["height"]) == (96, 64)
    with Image.open(result["file"]) as image:
        assert image.mode == "RGB" and image.getpixel((0, 0)) == (42, 42, 42)
    original = (advanced.s.output / "capture.png").read_bytes()
    pixels.fill(99)
    with pytest.raises(FileExistsError):
        advanced.execute("sim.camera.capture", advanced.s.stage(), args)
    assert (advanced.s.output / "capture.png").read_bytes() == original

    class BorrowedGPU:
        size = 64 * 96 * 4

        def numpy(self):
            pytest.fail("must never copy a borrowed GPU pointer")

    advanced.sensors["/World/Camera"][1]["annotator"] = SimpleNamespace(
        get_data=lambda **kw: BorrowedGPU()
    )
    with pytest.raises(AssetError, match="no GPU-pointer fallback"):
        advanced.execute(
            "sim.camera.capture",
            advanced.s.stage(),
            dict(path="/World/Camera", filename="unsafe.png"),
        )
    assert not (advanced.s.output / "unsafe.png").exists()


def test_light_material_clone_delete(advanced):
    s = advanced.s
    stage = s.stage()
    a = advanced
    s.create(stage, "/World/Box", "Cube")
    light = a.execute(
        "sim.light.create",
        stage,
        dict(path="/World/Sun", intensity=700, color=[1, 0.5, 0]),
    )
    assert light["intensity"] == 700
    assert a.execute(
        "sim.light.modify",
        stage,
        dict(path="/World/Sun", intensity=12, color=[0, 1, 0]),
    )["color"] == [0, 1, 0]
    a.execute("sim.material.create", stage, dict(path="/World/Mat", color=[1, 0, 0]))
    a.execute(
        "sim.material.apply", stage, dict(path="/World/Box", material_path="/World/Mat")
    )
    assert (
        str(
            UsdShade.MaterialBindingAPI(stage.GetPrimAtPath("/World/Box"))
            .ComputeBoundMaterial()[0]
            .GetPath()
        )
        == "/World/Mat"
    )
    a.execute("sim.prim.clone", stage, dict(path="/World/Box", new_path="/World/Copy"))
    assert stage.GetPrimAtPath("/World/Copy").IsA(UsdGeom.Cube)
    with pytest.raises(AssetError):
        a.execute("sim.prim.delete", stage, dict(path="/World/Copy", confirm=False))
    a.execute("sim.prim.delete", stage, dict(path="/World/Copy", confirm=True))
    assert not stage.GetPrimAtPath("/World/Copy")


def test_joint_limits_and_units(advanced):
    stage = advanced.s.stage()
    j = UsdPhysics.RevoluteJoint.Define(stage, "/World/Joint")
    j.CreateLowerLimitAttr(-30)
    j.CreateUpperLimitAttr(30)
    out = advanced.execute(
        "sim.joints.drive",
        stage,
        dict(path="/World/Joint", target=0.2, stiffness=10, damping=1),
    )
    assert out["authored_target"] == pytest.approx(11.4591559)
    with pytest.raises(AssetError, match="limits"):
        advanced.execute(
            "sim.joints.drive",
            stage,
            dict(path="/World/Joint", target=2, stiffness=10, damping=1),
        )
    assert (
        advanced.execute("sim.joints.inspect", stage, dict(path="/World"))["joints"][0][
            "type"
        ]
        == "PhysicsRevoluteJoint"
    )


def test_protected_and_invalid_no_effect(advanced, monkeypatch):
    s = advanced.s
    st = s.stage()
    before = st.GetSessionLayer().ExportToString()
    with pytest.raises(AssetError):
        advanced.execute(
            "sim.light.create", st, dict(path="/World/Bad", color=[float("nan"), 1, 1])
        )
    assert st.GetSessionLayer().ExportToString() == before
    monkeypatch.delenv("ISAAC_MCP_ENABLE_SCENE_SCRIPTS", raising=False)
    with pytest.raises(AssetError, match="opt in"):
        advanced.execute(
            "sim.controller.create",
            st,
            dict(path="/World/Graph", code="raise Exception()"),
        )
    assert not st.GetPrimAtPath("/World/Graph")
    s.create(st, "/World/Robot", "Xform")
    UsdPhysics.ArticulationRootAPI.Apply(st.GetPrimAtPath("/World/Robot"))
    with pytest.raises(AssetError, match="articulation"):
        advanced.execute("sim.prim.delete", st, dict(path="/World/Robot", confirm=True))


def test_physics_material_and_static_collision(advanced):
    s = advanced.s
    st = s.stage()
    s.create(st, "/World/Floor", "Cube", scale=[10, 10, 0.1], collision=True)
    floor = st.GetPrimAtPath("/World/Floor")
    assert floor.HasAPI(UsdPhysics.CollisionAPI) and not floor.HasAPI(
        UsdPhysics.RigidBodyAPI
    )
    advanced.execute(
        "sim.material.create",
        st,
        dict(path="/World/Contact", kind="physics", restitution=0.2),
    )
    result = advanced.execute(
        "sim.material.apply",
        st,
        dict(path="/World/Floor", material_path="/World/Contact"),
    )
    assert result["purpose"] == "physics" and result["material"] == "/World/Contact"
    assert UsdPhysics.MaterialAPI(
        st.GetPrimAtPath("/World/Contact")
    ).GetRestitutionAttr().Get() == pytest.approx(0.2)


def test_sensor_controller_ownership_invalidation(advanced):
    s = advanced.s
    s.stage()
    advanced.invalidate()
    advanced.controllers.add("/World/Controller")
    advanced.sensors["/World/Camera"] = ("camera", {})
    s.stage_id = "new-stage"
    advanced.invalidate()
    assert not advanced.controllers and not advanced.sensors


def test_sensor_stage_invalidation_releases_attachments_and_records_partial_failures(
    advanced,
):
    calls = []
    advanced.s.stage()
    advanced.invalidate()

    def detach():
        calls.append("detach")

    def destroy():
        calls.append("destroy")
        raise RuntimeError("native destroy failed")

    advanced.sensors["/World/Camera"] = (
        "camera",
        dict(
            annotator=SimpleNamespace(detach=detach),
            product=SimpleNamespace(destroy=destroy),
        ),
    )
    advanced.s.stage_id = "next"
    advanced.invalidate()
    assert calls == ["detach", "destroy"]
    assert not advanced.sensors
    assert advanced.cleanup_errors == [
        {"path": "/World/Camera", "error": "product.destroy: native destroy failed"}
    ]


def test_owned_sensor_delete_releases_resources_before_removing_usd(advanced):
    calls = []
    stage = advanced.s.stage()
    advanced.invalidate()
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdGeom.Camera.Define(stage, "/World/Camera")
    advanced.s.owned.add("/World/Camera")
    advanced.sensors["/World/Camera"] = (
        "camera",
        dict(
            annotator=SimpleNamespace(detach=lambda: calls.append("detach")),
            product=SimpleNamespace(destroy=lambda: calls.append("destroy")),
        ),
    )
    result = advanced.execute(
        "sim.prim.delete", stage, dict(path="/World/Camera", confirm=True)
    )
    assert calls == ["detach", "destroy"] and result["deleted"] == "/World/Camera"
    assert not advanced.sensors


def test_sensor_limit_refuses_before_native_creation(advanced):
    stage = advanced.s.stage()
    advanced.invalidate()
    advanced.sensors.update({f"/World/C{i}": ("camera", {}) for i in range(8)})
    before = stage.GetSessionLayer().ExportToString()
    with pytest.raises(AssetError, match="eight"):
        advanced.execute("sim.camera.create", stage, dict(path="/World/C9"))
    assert stage.GetSessionLayer().ExportToString() == before

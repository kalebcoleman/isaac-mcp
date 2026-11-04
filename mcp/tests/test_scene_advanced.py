"""Real USD authoring tests; sensor/physics runtime tests live in evidence scripts."""

import os
import sys
from types import SimpleNamespace
import pytest
from test_scene_usd import scene
from isaac_mcp.project_assets import AssetError
from sim_tools.advanced import Advanced
from sim_tools.camera_capture import CameraFrame
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

    advanced.sensors["/World/Camera"] = (
        "camera",
        dict(resolution=(64, 96), adapter="hydra-renderer-cpu-v1"),
    )
    args = dict(path="/World/Camera", filename="capture.png")
    result = advanced.execute(
        "sim.camera.capture",
        advanced.s.stage(),
        args,
        camera_frame=CameraFrame(pixels, True),
    )
    assert result["freshness"] == "new render and CPU transfer completed"
    assert (result["width"], result["height"]) == (96, 64)
    with Image.open(result["file"]) as image:
        assert image.mode == "RGB" and image.getpixel((0, 0)) == (42, 42, 42)
    original = (advanced.s.output / "capture.png").read_bytes()
    pixels.fill(99)
    with pytest.raises(AssetError, match="use another filename") as conflict:  # not an errno 17 application error
        advanced.execute(
            "sim.camera.capture",
            advanced.s.stage(),
            args,
            camera_frame=CameraFrame(pixels, True),
        )
    assert conflict.value.code == "CONFLICT"
    assert (advanced.s.output / "capture.png").read_bytes() == original

    class BorrowedGPU:
        size = 64 * 96 * 4

        def numpy(self):
            pytest.fail("must never copy a borrowed GPU pointer")

    with pytest.raises(AssetError, match="no GPU-pointer fallback"):
        advanced.execute(
            "sim.camera.capture",
            advanced.s.stage(),
            dict(path="/World/Camera", filename="unsafe.png"),
            camera_frame=CameraFrame(BorrowedGPU(), True),
        )
    assert not (advanced.s.output / "unsafe.png").exists()
    cached = advanced.execute(
        "sim.camera.capture",
        advanced.s.stage(),
        dict(path="/World/Camera", filename="cached.png"),
        camera_frame=CameraFrame(pixels, False),
    )
    assert "cached acquisition" in cached["freshness"] and "Cached" in cached["source"]
    with Image.open(cached["file"]) as image:
        assert image.getpixel((0, 0)) == (99, 99, 99)
    with pytest.raises(AssetError, match="validated renderer"):
        advanced.execute("sim.camera.capture", advanced.s.stage(), args)

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


@pytest.mark.parametrize("child_type", ["Camera", "OmniLidar"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("child_owned", [False, True])
def test_sensor_parent_delete_refuses_descendants_without_releasing_resources(
    advanced, child_type, nested, child_owned
):
    stage = advanced.s.stage()
    advanced.invalidate()
    parent = "/World/Camera"
    child = parent + ("/Group/Sensor" if nested else "/Sensor")
    calls = []

    def register(path, kind):
        advanced.s.owned.add(path)
        advanced.sensors[path] = (
            kind,
            dict(
                annotator=SimpleNamespace(detach=lambda: calls.append((path, "detach"))),
                product=SimpleNamespace(destroy=lambda: calls.append((path, "destroy"))),
            ),
        )

    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdGeom.Camera.Define(stage, parent)
        stage.DefinePrim(child, child_type)
    register(parent, "camera")
    if child_owned:
        register(child, "camera" if child_type == "Camera" else "lidar")
    layers = (stage.GetRootLayer(), stage.GetSessionLayer())
    before = [layer.ExportToString() for layer in layers]
    owned_before = advanced.s.owned.copy()
    sensors_before = advanced.sensors.copy()

    with pytest.raises(AssetError) as error:
        advanced.execute("sim.prim.delete", stage, dict(path=parent, confirm=True))
    assert error.value.code == "PROTECTED_PRIM"
    assert calls == []
    assert advanced.s.owned == owned_before
    assert advanced.sensors == sensors_before
    assert [layer.ExportToString() for layer in layers] == before

    if child_owned:
        # Leaf-first deletion releases both resource sets and quota slots.
        for path in (child, parent):
            result = advanced.execute(
                "sim.prim.delete", stage, dict(path=path, confirm=True)
            )
            assert result["deleted"] == path and not stage.GetPrimAtPath(path)
        assert calls == [
            (path, action)
            for path in (child, parent)
            for action in ("detach", "destroy")
        ]
        assert not advanced.sensors and not advanced.s.owned


def test_sensor_limit_refuses_before_native_creation(advanced):
    stage = advanced.s.stage()
    advanced.invalidate()
    advanced.sensors.update({f"/World/C{i}": ("camera", {}) for i in range(8)})
    before = stage.GetSessionLayer().ExportToString()
    with pytest.raises(AssetError, match="eight"):
        advanced.execute("sim.camera.create", stage, dict(path="/World/C9"))
    assert stage.GetSessionLayer().ExportToString() == before


@pytest.mark.parametrize("parent_type", ["Xform", "Camera"])
@pytest.mark.parametrize("child_type", ["Camera", "OmniLidar"])
@pytest.mark.parametrize("hidden", ["parent_inactive", "group_inactive", "removed", "retyped"])
def test_delete_preserves_sensor_registry_even_when_usd_hides_sensor(
    advanced, parent_type, child_type, hidden
):
    stage = advanced.s.stage()
    advanced.invalidate()
    parent, group, child = "/World/A", "/World/A/Group", "/World/A/Group/Sensor"
    calls = []
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        stage.DefinePrim(parent, parent_type)
        stage.DefinePrim(group, "Xform")
        stage.DefinePrim(child, child_type)
        if hidden == "parent_inactive":
            stage.GetPrimAtPath(parent).SetActive(False)
        elif hidden == "group_inactive":
            stage.GetPrimAtPath(group).SetActive(False)
        elif hidden == "removed":
            stage.RemovePrim(child)
        else:
            stage.GetPrimAtPath(child).SetTypeName("Xform")
    advanced.s.owned.update([parent, group, child])
    for path, kind in [(child, child_type)] + (
        [(parent, "Camera")] if parent_type == "Camera" else []
    ):
        advanced.sensors[path] = (
            kind,
            dict(product=SimpleNamespace(destroy=lambda p=path: calls.append(p))),
        )
    layers = (stage.GetRootLayer(), stage.GetSessionLayer())
    before = [layer.ExportToString() for layer in layers]
    owned_before, sensors_before = advanced.s.owned.copy(), advanced.sensors.copy()
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.prim.delete", stage, dict(path=parent, confirm=True))
    assert error.value.code == "PROTECTED_PRIM"
    assert calls == []
    assert advanced.s.owned == owned_before and advanced.sensors == sensors_before
    assert [layer.ExportToString() for layer in layers] == before
    # Teardown can still release the retained resource even if USD no longer has it.
    advanced.close_sensors()
    assert set(calls) == set(sensors_before) and not advanced.sensors


def test_sensor_registry_protection_uses_prim_ancestry_not_string_prefix(advanced):
    stage = advanced.s.stage()
    advanced.invalidate()
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        stage.DefinePrim("/World/A", "Xform")
        UsdGeom.Camera.Define(stage, "/World/AB")
    advanced.s.owned.update(["/World/A", "/World/AB"])
    sensor = ("camera", {})
    advanced.sensors["/World/AB"] = sensor
    advanced.execute("sim.prim.delete", stage, dict(path="/World/A", confirm=True))
    assert not stage.GetPrimAtPath("/World/A")
    assert stage.GetPrimAtPath("/World/AB")
    assert advanced.sensors == {"/World/AB": sensor}
    assert advanced.s.owned == {"/World/AB"}


def test_target_at_a_float32_limit_is_accepted_and_put_on_the_limit(advanced):
    """rg2_gripper_joint: 1.0 rad to -0.45 rad, authored as float32 degrees; both ends were refused."""
    stage = advanced.s.stage()
    j = UsdPhysics.RevoluteJoint.Define(stage, "/World/Rg2")
    j.CreateLowerLimitAttr(-25.783098220825195)  # how the asset stores -0.45 rad
    j.CreateUpperLimitAttr(57.2957763671875)  # how the asset stores 1.0 rad
    high = advanced.execute("sim.joints.drive", stage, dict(path="/World/Rg2", target=1.0, stiffness=10, damping=1))
    assert high["authored_target"] == pytest.approx(57.2957763671875, abs=1e-9) and "note" in high
    low = advanced.execute("sim.joints.drive", stage, dict(path="/World/Rg2", target=-0.45, stiffness=10, damping=1))
    assert low["authored_target"] == pytest.approx(-25.783098220825195, abs=1e-9) and "note" in low
    inside = advanced.execute("sim.joints.drive", stage, dict(path="/World/Rg2", target=0.5, stiffness=10, damping=1))
    assert "note" not in inside
    with pytest.raises(AssetError) as error:  # a real excess still refuses, with the numbers and units
        advanced.execute("sim.joints.drive", stage, dict(path="/World/Rg2", target=1.01, stiffness=10, damping=1))
    assert error.value.code == "JOINT_LIMIT" and "degrees" in str(error.value)


def test_limit_rounding_is_float32_sized_in_both_units():
    from sim_tools.advanced import to_limit

    assert to_limit(0.5, -1.0, 1.0) == 0.5
    assert to_limit(1.0000001, -1.0, 1.0) == 1.0
    assert to_limit(-1.0000001, -1.0, 1.0) == -1.0
    assert to_limit(1.001, -1.0, 1.0) is None
    assert to_limit(5.0, None, None) == 5.0
    # A prismatic joint in metres: 0.01 past a 0.05 limit is a real excess.
    assert to_limit(0.06, 0.0, 0.05) is None


def test_a_registered_sensor_with_a_large_subtree_can_be_deleted(advanced):
    stage = advanced.s.stage()
    advanced.invalidate()
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdGeom.Camera.Define(stage, "/World/BigSensor")
        for index in range(250):
            stage.DefinePrim(f"/World/BigSensor/Part{index}")
    advanced.s.owned.add("/World/BigSensor")
    advanced.sensors["/World/BigSensor"] = (
        "camera",
        dict(annotator=SimpleNamespace(detach=lambda: None), product=SimpleNamespace(destroy=lambda: None)),
    )
    result = advanced.execute("sim.prim.delete", stage, dict(path="/World/BigSensor", confirm=True))
    assert result["deleted"] == "/World/BigSensor" and not advanced.sensors


def test_an_ordinary_large_subtree_keeps_the_limit(advanced):
    stage = advanced.s.stage()
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        stage.DefinePrim("/World/Big", "Xform")
        for index in range(250):
            stage.DefinePrim(f"/World/Big/Part{index}")
    advanced.s.owned.add("/World/Big")
    with pytest.raises(AssetError, match="exceeds 200"):
        advanced.execute("sim.prim.delete", stage, dict(path="/World/Big", confirm=True))


def test_second_physics_scene_refusal_names_the_existing_one(advanced):
    stage = advanced.s.stage()
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdPhysics.Scene.Define(stage, "/PhysicsScene")
    with pytest.raises(AssetError, match="/PhysicsScene.*sim__physics__configure") as err:
        advanced.s.create(stage, "/World/Second", "PhysicsScene")
    assert err.value.code == "CONFLICT" and err.value.details["existing"] == "/PhysicsScene"


ARM_USDA = """#usda 1.0
(
    defaultPrim = "arm"
    metersPerUnit = 1
    upAxis = "Z"
)
def Xform "World" {
    def Xform "arm" {
        def Xform "base" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {}
        def Xform "link1" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {}
        def Xform "link2" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {}
        def PhysicsFixedJoint "root_joint" (prepend apiSchemas = ["PhysicsArticulationRootAPI"]) {
            rel physics:body1 = </World/arm/base>
        }
        def Scope "joints" {
            def PhysicsRevoluteJoint "j1" (prepend apiSchemas = ["PhysicsDriveAPI:angular"]) {
                rel physics:body0 = </World/arm/base>
                rel physics:body1 = </World/arm/link1>
                float physics:lowerLimit = -180
                float physics:upperLimit = 180
                float drive:angular:physics:stiffness = 1000
                float drive:angular:physics:damping = 50
            }
            def PhysicsRevoluteJoint "j2" {
                rel physics:body0 = </World/arm/link1>
                rel physics:body1 = </World/arm/link2>
            }
            def PhysicsFixedJoint "to_gripper" {
                rel physics:body0 = </World/arm/link2>
                rel physics:body1 = </World/gripper/base>
                bool physics:excludeFromArticulation = true
            }
        }
        def Xform "other_robot" {
            def Xform "b" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {}
            def Xform "c" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {}
            def PhysicsRevoluteJoint "elsewhere" {
                rel physics:body0 = </World/arm/other_robot/b>
                rel physics:body1 = </World/arm/other_robot/c>
            }
        }
    }
}
"""


@pytest.fixture
def arm(advanced, tmp_path):
    file = tmp_path / "arm.usda"
    file.write_text(ARM_USDA)
    stage = advanced.s.stage()
    stage.GetRootLayer().subLayerPaths.append(str(file))
    return advanced, stage


def test_inspect_on_a_leaf_root_joint_returns_the_articulations_joints_and_drives(arm):
    advanced, stage = arm
    out = advanced.execute("sim.joints.inspect", stage, dict(path="/World/arm/root_joint"))
    assert [j["path"].rsplit("/", 1)[-1] for j in out["joints"]] == ["root_joint", "j1", "j2"]
    j1 = out["joints"][1]
    assert j1["authored"]["drive:angular:physics:stiffness"] == 1000
    assert j1["authored"]["physics:lowerLimit"] == -180
    assert out["scope"] == "articulation" and out["articulation_root"] == "/World/arm/root_joint"
    assert out["units"] and out["source"] and out["truncated"] is False


def test_inspect_on_a_root_is_bounded_by_the_limit(arm):
    advanced, stage = arm
    out = advanced.execute("sim.joints.inspect", stage, dict(path="/World/arm/root_joint", limit=2))
    assert len(out["joints"]) == 2 and out["truncated"] is True


def test_inspect_on_a_subtree_is_unchanged(arm):
    advanced, stage = arm
    out = advanced.execute("sim.joints.inspect", stage, dict(path="/World/arm/joints"))
    assert {j["path"].rsplit("/", 1)[-1] for j in out["joints"]} == {"j1", "j2", "to_gripper"}
    assert "scope" not in out

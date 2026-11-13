"""CPU USD contract tests; synthetic assets and tensors do not qualify PhysX."""

import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("pxr.Usd")
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade


ROOT = Path(__file__).resolve().parents[2] / "exts/ur5e-dt/ur5e_dt"


def load_module(name):
    spec = importlib.util.spec_from_file_location(f"workcell_{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


robot = load_module("robot")
sensors = load_module("sensors")


def stage_units(stage):
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1)


def body(stage, path, position=(0, 0, 0)):
    prim = UsdGeom.Xform.Define(stage, path).GetPrim()
    UsdGeom.Xformable(prim).AddTranslateOp().Set(Gf.Vec3d(*position))
    UsdPhysics.RigidBodyAPI.Apply(prim)
    UsdPhysics.MassAPI.Apply(prim).CreateMassAttr().Set(0.1)
    return prim


class Pack:
    def __init__(self, paths):
        self.paths = paths
        self.calls = []

    def asset(self, asset_id):
        self.calls.append(asset_id)
        return self.paths[asset_id]


@pytest.fixture
def cell(tmp_path):
    paths, layouts = {}, {}
    for role, model in robot.MODELS.items():
        path = tmp_path / f"{role}.usda"
        source = Usd.Stage.CreateNew(str(path))
        stage_units(source)
        root_path = "/khi_rs080n" if role == "rg2" else "/Asset"
        root = UsdGeom.Xform.Define(source, root_path).GetPrim()
        source.SetDefaultPrim(root)
        UsdGeom.Xformable(root).AddTranslateOp().Set(Gf.Vec3d(0.23, -0.17, 0.08))
        layout = {"model": model, "bom_id": f"BOM-{role}", "fidelity": "placeholder"}
        if role == "finger_pad":
            cube = UsdGeom.Cube.Define(source, root_path + "/pad")
            cube.CreateSizeAttr().Set(0.01)
            UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
        else:
            name = {"robot": "wrist_3_link", "rg2": "onrobot_rg2_base_link"}.get(role, role + "_body")
            base = body(source, root_path + "/" + name, (0.01, 0.02, 0.03))
            layout.update(body=name, mount_frame=name)
            if role == "robot":
                joint = UsdPhysics.FixedJoint.Define(source, root_path + "/root_joint")
                joint.CreateBody1Rel().SetTargets([base.GetPath()])
                UsdPhysics.ArticulationRootAPI.Apply(joint.GetPrim())
                flange = UsdGeom.Xform.Define(source, base.GetPath().AppendChild("flange"))
                flange.AddTranslateOp().Set(Gf.Vec3d(0.04, -0.01, 0.02))
                flange.AddRotateZOp().Set(37)
                layout.update(articulation_root="root_joint", flange_frame=name + "/flange")
            if role == "rg2":
                UsdPhysics.ArticulationRootAPI.Apply(root)
                layout["articulation_root"] = "."
                layout["mount_pose"] = {"position_m": [0, 0, 0],
                                        "orientation_wxyz": [math.sqrt(0.5), 0, -math.sqrt(0.5), 0]}
                for side, sign in (("left", 1), ("right", -1)):
                    finger = body(source, root_path + f"/{side}_inner_finger", (0.01, sign * 0.04, 0.15))
                    joint = UsdPhysics.RevoluteJoint.Define(source, root_path + f"/{side}_joint")
                    joint.CreateBody0Rel().SetTargets([base.GetPath()])
                    joint.CreateBody1Rel().SetTargets([finger.GetPath()])
                    joint.CreateAxisAttr().Set("X")
                    joint.CreateLowerLimitAttr().Set(-45)
                    joint.CreateUpperLimitAttr().Set(20)
        source.GetRootLayer().Save()
        paths[f"pack/{role}"] = path
        layouts[role] = layout
    config = {
        "assets": {role: f"pack/{role}" for role in layouts}, "asset_layout": layouts,
        "robot": {"placement": {"position_m": [1, 2, 3], "orientation_wxyz": [1, 0, 0, 0]}},
        "stack": {"order": ["bota", "quick_changer", "rg2"]},
        "bota": {"length_m": 0.032, "mass_kg": 0.12, "sensor_origin_along_axis_m": 0.016,
                 "frame_id": "bota_ft_sensor"},
        "quick_changer": {"standoff_m": 0.01592, "mass_kg": 0.41},
        "rg2": {"max_opening_m": 0.110},
        "finger_pads": {"present": True, "static_friction": 0.8, "dynamic_friction": 0.7,
                        "mounts": [{"parent": side + "_inner_finger", "position_m": [0, 0, 0.005],
                                    "orientation_wxyz": [1, 0, 0, 0]} for side in ("left", "right")]},
        "finger_force_sensors": {"fitted": False},
    }
    stage = Usd.Stage.CreateInMemory()
    stage_units(stage)
    UsdGeom.Xform.Define(stage, "/World")
    return stage, Pack(paths), config


def world(stage, path):
    return UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(path))


@pytest.mark.parametrize("order", [["bota", "quick_changer", "rg2"], ["quick_changer", "bota", "rg2"]])
def test_stack_frames_order_mapping_and_sources_unchanged(cell, order):
    stage, pack, config = cell
    config["stack"]["order"] = order
    originals = {key: path.read_bytes() for key, path in pack.paths.items()}
    root_before = stage.GetRootLayer().ExportToString()
    report = robot.build_robot(stage, pack, config)
    json.dumps(report, allow_nan=False)
    assert stage.GetRootLayer().ExportToString() == root_before
    assert stage.GetSessionLayer().subLayerPaths == [report["layer_id"]]
    assert report["stack_order"] == order
    assert report["gripper_articulation_root"] == report["gripper_path"]
    assert report["max_opening_m"] == 0.110
    flange = world(stage, report["flange_path"])
    offset = 0.0
    for role in order:
        path = report["assembly_path"] + "/" + role
        layout = config["asset_layout"][role]
        mount = stage.GetPrimAtPath(path + "/" + layout["mount_frame"])
        actual = robot._mount_world(mount, layout) * flange.GetInverse()
        assert np.array(actual) == pytest.approx(np.array(Gf.Matrix4d().SetTranslate(Gf.Vec3d(offset, 0, 0))))
        joint = UsdPhysics.FixedJoint.Get(stage, report["assembly_path"] + f"/joints/{role}_mount")
        frames = []
        for side in (0, 1):
            local = Gf.Matrix4d().SetRotate(Gf.Quatd(getattr(joint, f"GetLocalRot{side}Attr")().Get()))
            local.SetTranslateOnly(Gf.Vec3d(getattr(joint, f"GetLocalPos{side}Attr")().Get()))
            target = getattr(joint, f"GetBody{side}Rel")().GetTargets()[0]
            frames.append(local * world(stage, target))
        assert np.array(frames[0]) == pytest.approx(np.array(frames[1]), abs=1e-6)
        assert joint.GetExcludeFromArticulationAttr().Get() == (role == "rg2")
        offset += {"bota": 0.032, "quick_changer": 0.01592, "rg2": 0}[role]
    native_rg2 = world(stage, report["gripper_path"] + "/onrobot_rg2_base_link") * flange.GetInverse()
    assert list(native_rg2.TransformDir(Gf.Vec3d(0, 0, 1))) == pytest.approx([1, 0, 0])
    tool0 = world(stage, report["tool0_path"]) * flange.GetInverse()
    assert list(tool0.TransformDir(Gf.Vec3d(0, 0, 1))) == pytest.approx([1, 0, 0])
    assert len(report["parts"]) == 6
    assert pack.calls == [f"pack/{role}" for role in ["robot", *order, "finger_pad"]]
    for part in report["parts"]:
        prim = stage.GetPrimAtPath(part["prim_path"])
        assert prim.GetCustomDataByKey("workcell:asset_id") == part["asset_id"]
        assert prim.GetCustomDataByKey("workcell:bom_id") == part["bom_id"]
    assert any("placeholder" in limitation for limitation in report["limitations"])
    assert not any("marker" in str(prim.GetPath()).lower() for prim in stage.Traverse())
    assert {key: path.read_bytes() for key, path in pack.paths.items()} == originals


def test_black_pads_follow_finger_frames_and_export(cell):
    stage, pack, config = cell
    report = robot.build_robot(stage, pack, config)
    for path in report["pad_paths"]:
        prim = stage.GetPrimAtPath(path)
        local, reset = UsdGeom.XformCache().ComputeRelativeTransform(prim, prim.GetParent())
        assert not reset
        assert list(local.ExtractTranslation()) == pytest.approx([0, 0, 0.005])
        material, _ = UsdShade.MaterialBindingAPI(prim.GetChild("pad")).ComputeBoundMaterial()
        shader = UsdShade.Shader(material.GetPrim().GetChild("surface"))
        assert list(shader.GetInput("diffuseColor").Get()) == [0, 0, 0]
        assert UsdPhysics.MaterialAPI(material).GetStaticFrictionAttr().Get() == pytest.approx(0.8)
    reopened = Usd.Stage.Open(stage.Flatten())
    assert reopened.GetPrimAtPath(report["bota_sensor_path"])
    before = stage.GetSessionLayer().ExportToString()
    with pytest.raises(ValueError, match="new absolute"):
        robot.build_robot(stage, pack, config)
    assert stage.GetSessionLayer().ExportToString() == before


@pytest.mark.parametrize("fault", ["stage_units", "asset_units", "up_axis", "missing_body", "bad_model",
                                    "bad_quaternion", "bad_order", "bad_width", "pads", "world_joint", "nested_body"])
def test_invalid_configuration_rolls_back(cell, fault):
    stage, pack, config = cell
    if fault == "stage_units":
        UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    elif fault in ("asset_units", "up_axis", "world_joint", "nested_body"):
        source = Usd.Stage.Open(str(pack.paths["pack/rg2"]))
        if fault == "asset_units":
            UsdGeom.SetStageMetersPerUnit(source, 0.01)
        elif fault == "up_axis":
            UsdGeom.SetStageUpAxis(source, "Y")
        elif fault == "world_joint":
            UsdPhysics.Joint.Get(source, "/khi_rs080n/left_joint").GetBody0Rel().ClearTargets(True)
        else:
            body(source, "/khi_rs080n/onrobot_rg2_base_link/nested")
        source.GetRootLayer().Save()
    elif fault == "missing_body":
        config["asset_layout"]["bota"]["body"] = "missing"
    elif fault == "bad_model":
        config["asset_layout"]["rg2"]["model"] = "husarion_rg2_ft"
    elif fault == "bad_quaternion":
        config["robot"]["placement"]["orientation_wxyz"] = [2, 0, 0, 0]
    elif fault == "bad_order":
        config["stack"]["order"] = ["rg2", "bota", "quick_changer"]
    elif fault == "bad_width":
        config["rg2"]["max_opening_m"] = 0.16
    elif fault == "pads":
        del config["finger_pads"]["mounts"]
    before = stage.GetRootLayer().ExportToString()
    with pytest.raises(ValueError):
        robot.build_robot(stage, pack, config)
    assert not stage.GetPrimAtPath("/World/RobotAssembly")
    assert not stage.GetSessionLayer().subLayerPaths
    assert stage.GetRootLayer().ExportToString() == before


class Articulation:
    count = 1
    max_links = 1

    def __init__(self, report=None):
        self.shared_metatype = SimpleNamespace(dof_names=["left_joint", "right_joint"], link_indices={"bota_body": 0})
        self.prim_paths = [report["articulation_root"]] if report else []
        self.targets = np.array([[0.1, 0.2]], dtype=np.float32)
        self.wrench = np.array([[[0, 10, 0, 0, 0, 1]]], dtype=np.float32)

    def check(self):
        return True

    def get_dof_positions(self):
        return np.array([[0.05, 0.06]], dtype=np.float32)

    def get_dof_velocities(self):
        return np.array([[0.01, 0.02]], dtype=np.float32)

    def get_dof_position_targets(self):
        return self.targets

    def get_dof_limits(self):
        return np.array([[[-1, 1], [-1, 1]]], dtype=np.float32)

    def set_dof_position_targets(self, targets, indices):
        assert indices.dtype == np.uint32
        assert indices.tolist() == [0]
        self.targets = targets
        return True

    def get_link_incoming_joint_force(self):
        return self.wrench


def test_named_commands_and_state():
    view = Articulation()
    measured = robot.robot_state(view)
    assert measured["positions"] == pytest.approx([0.05, 0.06])
    robot.command_joints(view, {"left_joint": -0.4})
    assert view.targets[0].tolist() == pytest.approx([-0.4, 0.2])
    for targets in ({"missing": 0}, {"left_joint": 2}, {"left_joint": float("nan")}, {}):
        with pytest.raises(ValueError):
            robot.command_joints(view, targets)
    with pytest.raises(RuntimeError, match="UNAVAILABLE"):
        robot.robot_state(None)


def test_width_requires_explicit_calibration():
    view = Articulation()
    config = {"rg2": {}}
    assert robot.command_gripper(view, 0.055, config)["status"] == "unsupported"
    config["rg2"]["width_calibration"] = [
        {"width_m": 0, "joints": {"left_joint": -0.7, "right_joint": -0.6}},
        {"width_m": 0.110, "joints": {"left_joint": 0.3, "right_joint": 0.4}},
    ]
    result = robot.command_gripper(view, 0.055, config)
    assert result["targets"] == pytest.approx({"left_joint": -0.2, "right_joint": -0.1})
    assert result["measured_width_m"] is None
    for width in (-0.001, 0.111, float("nan")):
        with pytest.raises(ValueError):
            robot.command_gripper(view, width, config)


@pytest.mark.parametrize("rotated", [False, True])
def test_bota_measured_wrench_origin_and_axes(cell, rotated):
    stage, pack, config = cell
    if rotated:
        config["bota"]["sensor_orientation_wxyz"] = [math.sqrt(0.5), 0, 0, math.sqrt(0.5)]
    report = robot.build_robot(stage, pack, config)
    view = Articulation(report)
    args = dict(physics_time_s=1.5, backend="physx")
    assert sensors.read_bota_wrench(stage, view, report, config, **args)["measurement"] is None
    config["bota"]["measurement"] = "incoming_joint_wrench"
    result = sensors.read_bota_wrench(stage, view, report, config, **args)
    assert result["status"] == "measured"
    assert result["force_n"] == pytest.approx([10, 0, 0] if rotated else [0, 10, 0], abs=1e-6)
    assert result["torque_nm"] == pytest.approx([0, 0, 0.84], abs=1e-6)
    assert not result["gravity_compensated"]
    assert result["physics_time_s"] == 1.5
    view.wrench[:] = np.nan
    result = sensors.read_bota_wrench(stage, view, report, config, **args)
    assert result["measurement"] is None
    view.shared_metatype.link_indices.clear()
    assert sensors.read_bota_wrench(stage, view, report, config, **args)["status"] == "unavailable"


class Contacts:
    sensor_paths = ["/left", "/right"]

    def check(self):
        return True

    def get_net_contact_forces(self, dt):
        assert dt == 0.01
        return np.array([[1, 2, 3], [0, 0, 0]], dtype=np.float32)


def test_finger_measurement_is_explicit_and_never_fabricated():
    config = {"finger_force_sensors": {"fitted": True}}
    args = dict(physics_time_s=2, physics_dt_s=0.01, backend="physx")
    assert sensors.read_finger_contacts(Contacts(), config, **args)["measurement"] is None
    config["finger_force_sensors"].update(measurement="net_contact_force", body_paths=["/left", "/right"])
    result = sensors.read_finger_contacts(Contacts(), config, **args)
    assert result["measurement"] == [[1, 2, 3], [0, 0, 0]]
    assert result["quantity"] == "whole_finger_net_contact_force"
    assert sensors.read_finger_contacts(None, config, **args)["status"] == "unavailable"
    assert sensors.read_finger_contacts(Contacts(), config, **{**args, "backend": "newton"})["status"] == "unsupported"
    config["finger_force_sensors"]["body_paths"].reverse()
    assert sensors.read_finger_contacts(Contacts(), config, **args)["measurement"] is None


@pytest.mark.parametrize("method", ["check", "get_link_incoming_joint_force"])
def test_bota_native_backend_failure_is_unavailable(cell, monkeypatch, method):
    stage, pack, config = cell
    report = robot.build_robot(stage, pack, config)
    config["bota"]["measurement"] = "incoming_joint_wrench"
    view = Articulation(report)

    def fail():
        raise Exception("Failed to get incoming link joint forces from backend")

    monkeypatch.setattr(view, method, fail)
    result = sensors.read_bota_wrench(stage, view, report, config,
                                      physics_time_s=1, backend="physx")
    assert result["status"] == "unavailable"
    assert result["measurement"] is None


@pytest.mark.parametrize("method", ["check", "get_net_contact_forces"])
def test_contact_native_backend_failure_is_unavailable(monkeypatch, method):
    config = {"finger_force_sensors": {"measurement": "net_contact_force",
                                       "body_paths": ["/left", "/right"]}}
    view = Contacts()

    def fail(**kwargs):
        raise Exception("Failed to get net contact forces from backend")

    monkeypatch.setattr(view, method, fail)
    result = sensors.read_finger_contacts(view, config, physics_time_s=1,
                                          physics_dt_s=0.01, backend="physx")
    assert result["status"] == "unavailable"
    assert result["measurement"] is None

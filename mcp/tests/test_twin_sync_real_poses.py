"""Exercise ROS context ownership and sync outcomes without starting Kit or ROS."""

import ast
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "exts/ur5e-dt/ur5e_dt/extension.py"


def _method(name, namespace):
    tree = ast.parse(SOURCE.read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("receive", [False, True])
def test_sync_owns_only_its_context_and_reports_updates(tmp_path, monkeypatch, receive):
    context = object()
    node = SimpleNamespace(destroy_node=Mock(), destroy_subscription=Mock())
    executor = SimpleNamespace(shutdown=Mock())
    callbacks = []
    node.create_subscription = Mock(side_effect=lambda _type, _topic, cb, _depth: callbacks.append(cb) or object())
    translation = SimpleNamespace(x=0.1, y=0.2, z=0.3)
    rotation = SimpleNamespace(w=1, x=0, y=0, z=0)
    message = SimpleNamespace(transforms=[SimpleNamespace(child_frame_id="part", transform=SimpleNamespace(
        translation=translation, rotation=rotation))])
    ros = ModuleType("rclpy")
    ros.init = Mock()
    ros.create_node = Mock(return_value=node)
    ros.shutdown = Mock()
    ros.spin_once = Mock(side_effect=lambda *a, **kw: callbacks[0](message) if receive else None)
    context_mod = ModuleType("rclpy.context")
    context_mod.Context = Mock(return_value=context)
    executors = ModuleType("rclpy.executors")
    executors.SingleThreadedExecutor = Mock(return_value=executor)
    msgs = ModuleType("tf2_msgs.msg")
    msgs.TFMessage = object
    for name, module in {"rclpy": ros, "rclpy.context": context_mod,
                         "rclpy.executors": executors, "tf2_msgs": ModuleType("tf2_msgs"),
                         "tf2_msgs.msg": msgs}.items():
        monkeypatch.setitem(sys.modules, name, module)
    fake_time = SimpleNamespace(time=Mock(side_effect=[0, 1, 4]))
    monkeypatch.setitem(sys.modules, "time", fake_time)
    prim = SimpleNamespace(IsValid=lambda: True)
    stage = SimpleNamespace(GetPrimAtPath=lambda _path: prim)
    omni = SimpleNamespace(usd=SimpleNamespace(get_context=lambda: SimpleNamespace(get_stage=lambda: stage)))
    owner = SimpleNamespace(_assembly_file_path=str(tmp_path / "missing.json"),
                            _load_object_type_map=lambda: {},
                            _get_prim_path=lambda name: "/World/Objects/" + name,
                            _set_obj_prim_pose=Mock())
    method = _method("sync_real_poses", {"os": os, "omni": omni})
    result = method(owner)
    assert result["received"] is receive
    assert result["updated"] == (["part"] if receive else [])
    ros.init.assert_called_once_with(args=None, context=context)
    ros.create_node.assert_called_once_with("_sync_real_poses_tmp", context=context)
    ros.shutdown.assert_called_once_with(context=context)
    executor.shutdown.assert_called_once()
    node.destroy_node.assert_called_once()
    assert owner._set_obj_prim_pose.call_count == int(receive)


@pytest.mark.parametrize("result,status", [
    ({"received": False, "updated": [], "snapped": []}, "error"),
    ({"received": True, "updated": [], "snapped": []}, "error"),
    ({"received": True, "updated": ["part"], "snapped": []}, "success"),
])
def test_sync_handler_reports_no_message_and_no_matching_objects(result, status):
    handler = _method("_cmd_sync_real_poses", {"Dict": Dict, "Any": Any})
    owner = SimpleNamespace(sync_real_poses=lambda: result)
    assert handler(owner)["status"] == status


def test_recording_reports_the_actual_absolute_output_path(tmp_path):
    manager = SimpleNamespace(recording=False, is_recording=lambda: False)
    handler = _method("_cmd_start_recording", {
        "Dict": Dict, "Any": Any, "os": os, "VIDEOS_DIR": str(tmp_path / "videos"),
        "_recording_mgr": manager,
    })
    owner = SimpleNamespace(_start_realtime_recording=Mock())
    target = str(tmp_path / "recording.mp4")
    result = handler(owner, file_name=target)
    assert result["file_path"] == target
    assert result["message"] == f"Recording started → {target}"

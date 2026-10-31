"""Check camera configuration selection without importing Kit or opening a scene."""

import ast
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


class GraphReached(Exception):
    pass


@pytest.mark.parametrize("configured", [False, True])
def test_camera_config_is_only_read_when_explicit(tmp_path, monkeypatch, configured):
    repo = Path(__file__).resolve().parents[2]
    source = repo / "exts" / "ur5e-dt" / "ur5e_dt" / "extension.py"
    tree = ast.parse(source.read_text())
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "setup_camera_action_graph")
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))

    yaml = ModuleType("yaml")
    yaml.safe_load = Mock(return_value={"camera_width": 640, "camera_height": 480})
    monkeypatch.setitem(sys.modules, "yaml", yaml)
    home = tmp_path / "home"
    legacy = home / "Desktop" / "ros2_ws" / "src" / "aruco_camera_localizer" / "config" / "robot_config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("camera_width: 640\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("UR5E_DT_CAMERA_CONFIG", raising=False)
    if configured:
        monkeypatch.setenv("UR5E_DT_CAMERA_CONFIG", str(legacy))

    stage = SimpleNamespace(GetPrimAtPath=Mock(side_effect=[True, GraphReached()]))
    omni = SimpleNamespace(usd=SimpleNamespace(get_context=lambda: SimpleNamespace(get_stage=lambda: stage)))
    namespace = {"omni": omni, "os": os}
    exec(compile(module, str(source), "exec"), namespace)
    with pytest.raises(GraphReached):
        namespace["setup_camera_action_graph"](None)
    assert yaml.safe_load.call_count == int(configured)

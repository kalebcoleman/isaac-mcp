"""Execute the real graph builder with real USD and a recording OG boundary."""
import ast
from pathlib import Path
from types import SimpleNamespace
import os
import sys
import pytest

pytest.importorskip("pxr.Usd")
from pxr import Usd, UsdGeom, Gf
import twin_source


@pytest.mark.parametrize("legacy", [False, True])
def test_camera_ros_graph_frame_clock_and_source_preservation(tmp_path, monkeypatch, legacy):
    source = twin_source.SOURCE
    tree = ast.parse(source.read_text())
    method = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "setup_camera_action_graph")
    module = ast.Module(body=[method], type_ignores=[])
    camera_path = "/World/UR5e/wrist_3_link/rsd455/RSD455/Camera_OmniVision_OV9782_Color"
    original = Usd.Stage.CreateNew(str(tmp_path / "sensor.usda"))
    UsdGeom.Camera.Define(original, camera_path)
    original.GetRootLayer().Save()
    before = (tmp_path / "sensor.usda").read_bytes()
    stage = Usd.Stage.CreateInMemory()
    stage.GetRootLayer().subLayerPaths.append(original.GetRootLayer().identifier)
    calls = []
    keys = SimpleNamespace(CREATE_NODES="create", SET_VALUES="set", CONNECT="connect")
    class GraphBoundary(Exception):
        pass
    def edit(config, spec):
        calls.append(spec)
        stage.DefinePrim(config["graph_path"], "OmniGraph")
        raise GraphBoundary()
    og = SimpleNamespace(Controller=SimpleNamespace(Keys=keys, edit=edit))
    namespace = dict(omni=SimpleNamespace(usd=SimpleNamespace(get_context=lambda:
                     SimpleNamespace(get_stage=lambda: stage))), og=og,
                     _use_legacy_tf_publisher=lambda _: legacy, os=os, UsdGeom=UsdGeom, Gf=Gf)
    monkeypatch.delenv("UR5E_DT_CAMERA_CONFIG", raising=False)
    monkeypatch.setitem(sys.modules, "yaml", SimpleNamespace(safe_load=lambda _: {}))
    exec(compile(module, str(source), "exec"), namespace)
    build = namespace["setup_camera_action_graph"]
    with pytest.raises(GraphBoundary):
        build(None)
    spec = calls[0]; values = dict(spec["set"])
    frame = stage.GetPrimAtPath(camera_path).GetAttribute("isaac:nameOverride").Get()
    assert frame == values["RGBPublish.inputs:frameId"] == values["CameraInfoPublish.inputs:frameId"]
    assert values["RGBPublish.inputs:resetSimulationTimeOnStop"] is False
    assert values["CameraInfoPublish.inputs:resetSimulationTimeOnStop"] is False
    assert values["ReadSimTime.inputs:resetOnStop"] is False
    target = "TFPublish" if legacy else "TFCompute"
    assert values[target + ".inputs:targetPrims"] == camera_path
    assert ("ReadSimTime.outputs:simulationTime", "TFPublish.inputs:timeStamp") in spec["connect"]
    assert (tmp_path / "sensor.usda").read_bytes() == before
    assert not original.GetPrimAtPath(camera_path).GetAttribute("isaac:nameOverride")
    build(None)
    assert len(calls) == 1  # Repeated setup does not add a second publisher.

"""A flattened copy must not grow with each save and reopen."""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("pxr")
from pxr import Usd, UsdGeom  # noqa: E402

spec = importlib.util.spec_from_file_location("sim_tools_flatten_under_test", Path(__file__).parents[2] / "exts/sim-tools/sim_tools/flatten.py")
flatten = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flatten)


def _instanced_scene(tmp_path):
    part = Usd.Stage.CreateNew(str(tmp_path / "part.usda"))
    UsdGeom.Cube.Define(part, "/Part/Box")
    part.SetDefaultPrim(part.GetPrimAtPath("/Part"))
    part.Save()
    stage = Usd.Stage.CreateNew(str(tmp_path / "scene.usda"))
    UsdGeom.Xform.Define(stage, "/World")
    for name in ("a", "b"):
        prim = stage.DefinePrim(f"/World/{name}")
        prim.GetReferences().AddReference("part.usda")
        prim.SetInstanceable(True)
    stage.Save()
    return stage


def test_repeated_flatten_keeps_one_set_of_prototypes_and_the_instances_still_work(tmp_path):
    stage = _instanced_scene(tmp_path)
    counts = []
    for round_ in range(4):
        layer = stage.Flatten()
        flatten.drop_unused_prototypes(layer)
        target = tmp_path / f"copy{round_}.usda"
        layer.Export(str(target))
        counts.append(len([p for p in layer.rootPrims if p.name.startswith("Flattened_Prototype_")]))
        stage = Usd.Stage.Open(str(target))
        for name in ("a", "b"):
            prim = stage.GetPrimAtPath(f"/World/{name}")
            assert prim.IsInstance() and prim.GetPrototype().GetChild("Box")
    assert counts == [counts[0]] * 4 and counts[0] > 0


def test_a_prototype_something_refers_to_is_kept(tmp_path):
    stage = _instanced_scene(tmp_path)
    layer = stage.Flatten()
    names = [p.name for p in layer.rootPrims if p.name.startswith("Flattened_Prototype_")]
    assert names and flatten.drop_unused_prototypes(layer) == []
    assert [p.name for p in layer.rootPrims if p.name.startswith("Flattened_Prototype_")] == names


def test_a_scene_without_prototypes_is_untouched(tmp_path):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Cube.Define(stage, "/World/Box")
    layer = stage.Flatten()
    assert flatten.drop_unused_prototypes(layer) == []
    assert layer.GetPrimAtPath("/World/Box")

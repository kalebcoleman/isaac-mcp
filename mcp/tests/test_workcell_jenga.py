"""Seeded sampling and synthetic CPU USD authoring; no PhysX qualification."""

import copy
import importlib.util
import itertools
import random
from pathlib import Path
from types import SimpleNamespace

import pytest


PACKAGE = Path(__file__).resolve().parents[2] / "exts/workcell/workcell"
SPEC = importlib.util.spec_from_file_location("workcell_jenga", PACKAGE / "jenga.py")
jenga = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jenga)


def constant(value):
    return {"distribution": "constant", "value": value}


def uniform(lower, upper):
    return {"distribution": "uniform", "min": lower, "max": upper}


def normal(mean, std, lower, upper):
    return {"distribution": "normal", "mean": mean, "std": std, "min": lower, "max": upper}


@pytest.fixture
def config():
    return {"assets": {"jenga_block": "synthetic/block"},
            "jenga": {"block_size_m": [0.072, 0.024, 0.014], "block_mass_kg": 0.015,
                      "tower_xy_m": [0.7, -0.13], "tower_layers_built": 18,
                      "static_friction": 0.3, "dynamic_friction": 0.25,
                      "variation_profiles": {
                          "sample": {"mass_kg": normal(0.015, 0.002, 0.01, 0.02),
                                     "static_friction": uniform(0.2, 0.5),
                                     "dynamic_friction": uniform(0.1, 0.4),
                                     "size_tolerance_m": [uniform(-0.0004, 0.0004),
                                                          normal(0, 0.0002, -0.0005, 0.0005),
                                                          uniform(-0.0005, 0.0005)]},
                          "fixed": {"mass_kg": constant(0.016),
                                    "static_friction": constant(0.4),
                                    "dynamic_friction": constant(0.3),
                                    "size_tolerance_m": [constant(0.0003), constant(-0.0002), constant(0.0001)]}}}}


def test_nominal_compatibility(config):
    report = jenga.realize(config)
    assert report["variation_profile"] is None and report["variation_seed"] is None
    assert len(report["blocks"]) == 54
    for block, (position, angle) in zip(report["blocks"], jenga.positions(config)):
        assert block["position_m"] == pytest.approx(position)
        assert block["rotation_z_deg"] == angle
        assert block["size_m"] == config["jenga"]["block_size_m"]
        assert block["mass_kg"] == 0.015
        assert block["static_friction"] == 0.3 and block["dynamic_friction"] == 0.25


def test_determinism_override_and_no_global_rng_or_config_mutation(config):
    config["jenga"].update(variation_profile="sample", variation_seed=123)
    original = copy.deepcopy(config)
    rng_state = random.getstate()
    first = jenga.realize(config)
    assert first == jenga.realize(config)
    assert first == jenga.realize(config, "sample", 123)
    assert first != jenga.realize(config, seed=124)
    assert jenga.realize(config, "fixed", 0)["variation_seed"] == 0
    assert config == original and random.getstate() == rng_state
    for block in first["blocks"]:
        assert 0.01 <= block["mass_kg"] <= 0.02
        assert 0.1 <= block["dynamic_friction"] <= block["static_friction"] <= 0.5
        assert all(abs(offset) <= 0.0005 for offset in block["size_tolerance_m"])


def test_fixed_profile_and_layer_envelopes(config):
    fixed = jenga.realize(config, "fixed", 7)
    assert fixed["blocks"][0]["size_m"] == pytest.approx([0.0723, 0.0238, 0.0141])
    varied = jenga.realize(config, "sample", 7)["blocks"]
    bottom = 0
    for layer in range(18):
        row = varied[layer * 3:layer * 3 + 3]
        for block in row:
            assert block["position_m"][2] - block["size_m"][2] / 2 == pytest.approx(bottom)
            assert block["layer"] == layer
        axis = 0 if layer % 2 else 1
        for left, right in zip(row, row[1:]):
            assert left["position_m"][axis] + left["size_m"][1] / 2 == pytest.approx(
                right["position_m"][axis] - right["size_m"][1] / 2)
        bottom += max(block["size_m"][2] for block in row)


@pytest.mark.parametrize("field,spec", [
    ("mass_kg", constant(0)), ("mass_kg", constant(-0.01)),
    ("mass_kg", constant(float("nan"))), ("mass_kg", constant(float("inf"))),
    ("mass_kg", constant(1e-60)), ("mass_kg", constant(1e40)),
    ("mass_kg", constant(True)), ("mass_kg", constant("0.015")),
    ("mass_kg", uniform(0, 0.02)), ("mass_kg", uniform(0.02, 0.01)),
    ("mass_kg", {"distribution": "normal", "mean": 0.015, "std": 0.001}),
    ("mass_kg", normal(0.015, 0, 0.01, 0.02)),
    ("mass_kg", normal(0.03, 0.001, 0.01, 0.02)),
    ("mass_kg", normal(0.015, 0.001, 0.015, 0.015)),
    ("static_friction", uniform(-0.1, 0.5)),
    ("dynamic_friction", constant(0.9)),
    ("size_tolerance_m", [constant(-0.014)] * 3),
    ("size_tolerance_m", [constant(0.00101)] * 3),
    ("size_tolerance_m", [constant(0)] * 2),
    ("mass_kg", {"distribution": "unknown", "value": 1}),
    ("mass_kg", {"distribution": "constant", "value": 1, "std": 0.01}),
    ("mass_kg", {"distribution": [], "value": 1}),
])
def test_invalid_profile_rejected_before_stage_access(config, field, spec):
    config["jenga"]["variation_profiles"]["sample"][field] = spec
    with pytest.raises(ValueError):
        jenga.build(object(), "unused.usda", config, "sample", 7)


@pytest.mark.parametrize("field,value", [
    ("variation_profile", "missing"), ("variation_profile", []),
    ("variation_seed", True), ("variation_seed", 1.5),
    ("variation_seed", "123"), ("variation_seed", None),
    ("tower_layers_built", 0), ("tower_layers_built", 19), ("tower_layers_built", True),
    ("tower_xy_m", [0]), ("tower_xy_m", [0, float("inf")]),
    ("block_size_m", [0.072, 0.024, -0.014]),
    ("block_mass_kg", -1),
])
def test_invalid_settings(config, field, value):
    config["jenga"].update(variation_profile="sample", variation_seed=5)
    config["jenga"][field] = value
    with pytest.raises(ValueError):
        jenga.build(object(), "unused.usda", config)


def test_normal_exhaustion_is_bounded_before_authoring(config, monkeypatch):
    calls = []
    class RejectingRandom:
        def __init__(self, seed):
            assert seed == 3

        def gauss(self, mean, std):
            calls.append((mean, std))
            return 100

    monkeypatch.setattr(jenga.random, "Random", RejectingRandom)
    with pytest.raises(ValueError, match="block 1: Bounded normal exhausted"):
        jenga.build(object(), "unused.usda", config, "sample", 3)
    assert len(calls) == jenga.MAX_SAMPLE_ATTEMPTS


def test_late_friction_exhaustion_never_authors_partial_tower(config, monkeypatch):
    calls = []
    class RejectingRandom:
        def __init__(self, seed):
            pass

        def uniform(self, lower, upper):
            calls.append((lower, upper))
            if len(calls) <= 2:
                return upper if len(calls) == 1 else lower
            return lower if lower == 0.2 else upper

    profile = config["jenga"]["variation_profiles"]["sample"]
    profile.update(mass_kg=constant(0.015), size_tolerance_m=[constant(0)] * 3)
    monkeypatch.setattr(jenga.random, "Random", RejectingRandom)
    with pytest.raises(ValueError, match="block 2: Friction pair exhausted"):
        jenga.build(object(), "unused.usda", config, "sample", 3)
    assert len(calls) == 2 + 2 * jenga.MAX_SAMPLE_ATTEMPTS


@pytest.fixture
def usd():
    pytest.importorskip("pxr.Usd")
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade
    return SimpleNamespace(Gf=Gf, Sdf=Sdf, Usd=Usd, UsdGeom=UsdGeom, UsdPhysics=UsdPhysics, UsdShade=UsdShade)


@pytest.fixture
def stage(usd):
    stage = usd.Usd.Stage.CreateInMemory()
    usd.UsdGeom.SetStageMetersPerUnit(stage, 1)
    usd.UsdGeom.SetStageUpAxis(stage, "Z")
    usd.UsdPhysics.SetStageKilogramsPerUnit(stage, 1)
    return stage


@pytest.fixture
def asset(usd, tmp_path):
    source = usd.Usd.Stage.CreateNew(str(tmp_path / "block.usda"))
    root = usd.UsdGeom.Xform.Define(source, "/Block")
    source.SetDefaultPrim(root.GetPrim())
    root.AddTranslateOp().Set(usd.Gf.Vec3d(0.12, -0.09, 0.03))
    mesh = usd.UsdGeom.Mesh.Define(source, "/Block/Mesh")
    mesh.CreatePointsAttr().Set([(length, width, height) for length, width, height in
                                itertools.product((0, 0.072), (0, 0.024), (0, 0.014))])
    mesh.CreateFaceVertexCountsAttr().Set([4] * 6)
    mesh.CreateFaceVertexIndicesAttr().Set([0, 2, 6, 4, 1, 5, 7, 3, 0, 4, 5, 1,
                                          2, 3, 7, 6, 0, 1, 3, 2, 4, 6, 7, 5])
    mesh.CreateSubdivisionSchemeAttr().Set("none")
    visual = usd.UsdShade.Material.Define(source, "/Block/Visual")
    physics = usd.UsdShade.Material.Define(source, "/Block/SharedPhysics")
    usd.UsdPhysics.MaterialAPI.Apply(physics.GetPrim()).CreateStaticFrictionAttr().Set(0.9)
    binding = usd.UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim())
    binding.Bind(visual)
    binding.Bind(physics, materialPurpose="physics")
    source.GetRootLayer().Save()
    return tmp_path / "block.usda"


def test_usd_authored_values_geometry_materials_and_no_intersections(config, usd, stage, asset):
    original = asset.read_bytes()
    report = jenga.build(stage, asset, config, "sample", 21)
    assert report["block_count"] == 54 and len(report["parts"]) == 54
    assert all(part["asset_id"] == "synthetic/block" for part in report["parts"])
    cache = usd.UsdGeom.BBoxCache(usd.Usd.TimeCode.Default(), ["default", "render"])
    ranges = []
    for block in report["blocks"]:
        prim = stage.GetPrimAtPath(block["prim_path"])
        assert usd.UsdPhysics.MassAPI(prim).GetMassAttr().Get() == pytest.approx(block["mass_kg"])
        assert prim.GetAttribute("jenga:sizeM").Get() == pytest.approx(block["size_m"])
        local = cache.ComputeRelativeBound(prim, prim).ComputeAlignedRange()
        assert local.GetSize() == pytest.approx(block["size_m"], abs=1e-8)
        assert (local.GetMin() + local.GetMax()) / 2 == pytest.approx([0, 0, 0], abs=1e-8)
        world = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        assert (world.GetMin() + world.GetMax()) / 2 == pytest.approx(block["position_m"], abs=1e-8)
        ranges.append(world)
        mesh = stage.GetPrimAtPath(block["prim_path"] + "/Asset/Source/Mesh")
        assert usd.UsdPhysics.MeshCollisionAPI(mesh).GetApproximationAttr().Get() == "convexHull"
        physics, _ = usd.UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial("physics")
        visual, _ = usd.UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
        assert str(physics.GetPath()) == block["material_path"]
        assert str(visual.GetPath()).endswith("/Visual")
        material = usd.UsdPhysics.MaterialAPI(physics.GetPrim())
        assert material.GetStaticFrictionAttr().Get() == pytest.approx(block["static_friction"])
        assert material.GetDynamicFrictionAttr().Get() == pytest.approx(block["dynamic_friction"])
    for left, right in itertools.combinations(ranges, 2):
        overlap = [min(left.GetMax()[axis], right.GetMax()[axis])
                   - max(left.GetMin()[axis], right.GetMin()[axis]) for axis in range(3)]
        assert min(overlap) <= 1e-8
    assert asset.read_bytes() == original


def test_legacy_build_and_existing_tower_refusal(config, stage, asset):
    report = jenga.build(stage, asset, config)
    assert report["variation_profile"] is None
    assert report["block_mass_kg"] == 0.015
    before = stage.GetRootLayer().ExportToString()
    with pytest.raises(ValueError, match="already exists"):
        jenga.build(stage, asset, config)
    assert stage.GetRootLayer().ExportToString() == before


@pytest.mark.parametrize("bad_asset", ["missing", "no_mesh", "wrong_size"])
def test_asset_failure_does_not_mutate_target(config, stage, usd, tmp_path, bad_asset):
    path = tmp_path / "bad.usda"
    if bad_asset != "missing":
        source = usd.Usd.Stage.CreateNew(str(path))
        root = usd.UsdGeom.Xform.Define(source, "/Asset")
        source.SetDefaultPrim(root.GetPrim())
        cube = usd.UsdGeom.Cube.Define(source, "/Asset/Cube")
        cube.CreateSizeAttr().Set(1 if bad_asset == "wrong_size" else 2)
        if bad_asset == "no_mesh":
            cube.AddScaleOp().Set(usd.Gf.Vec3f(0.036, 0.012, 0.007))
        source.GetRootLayer().Save()
    before = stage.GetRootLayer().ExportToString()
    with pytest.raises(ValueError, match="composition|mesh geometry|bounds"):
        jenga.build(stage, path, config)
    assert stage.GetRootLayer().ExportToString() == before


def test_wrong_stage_units_rejected(config, usd, stage, asset):
    usd.UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    before = stage.GetRootLayer().ExportToString()
    with pytest.raises(ValueError, match="metres and kilograms"):
        jenga.build(stage, asset, config)
    assert stage.GetRootLayer().ExportToString() == before


def test_reset_uses_persisted_realization_after_reopen(config, usd, stage, asset, tmp_path):
    report = jenga.build(stage, asset, config, "sample", 22)
    for block in report["blocks"]:
        ops = usd.UsdGeom.Xformable(stage.GetPrimAtPath(block["prim_path"])).GetOrderedXformOps()
        ops[0].Set(usd.Gf.Vec3d(9, 8, 7))
        ops[1].Set(42)
    saved = tmp_path / "tower.usda"
    stage.GetRootLayer().Export(str(saved))
    reopened = usd.Usd.Stage.Open(str(saved))
    config["jenga"].update(variation_profile="missing", variation_seed=999, tower_xy_m=[1, 2])
    jenga.reset(reopened, config)
    for block in report["blocks"]:
        prim = reopened.GetPrimAtPath(block["prim_path"])
        ops = usd.UsdGeom.Xformable(prim).GetOrderedXformOps()
        assert ops[0].Get() == pytest.approx(block["position_m"])
        assert ops[1].Get() == block["rotation_z_deg"]
        assert usd.UsdPhysics.MassAPI(prim).GetMassAttr().Get() == pytest.approx(block["mass_kg"])


def test_reset_prevalidates_every_block(config, usd, stage, asset):
    jenga.build(stage, asset, config)
    first = stage.GetPrimAtPath("/World/Jenga/Block_01")
    usd.UsdGeom.Xformable(first).GetOrderedXformOps()[0].Set(usd.Gf.Vec3d(9, 8, 7))
    stage.GetPrimAtPath("/World/Jenga/Block_54").RemoveProperty("jenga:initialPosition")
    before = stage.GetRootLayer().ExportToString()
    with pytest.raises(ValueError, match="reset metadata"):
        jenga.reset(stage, config)
    assert stage.GetRootLayer().ExportToString() == before

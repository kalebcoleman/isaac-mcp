"""Trusted multi-file workstation preparation with real OpenUSD, without Kit."""

import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import Usd, UsdGeom, UsdUtils
from isaac_mcp.project_assets import AssetError, Broker
from sim_tools.core import Scene


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "parts").mkdir()
    (project / "textures").mkdir()
    (project / "textures" / "color.png").write_bytes(b"test texture bytes")
    (project / "parts" / "bench.usda").write_text('#usda 1.0\ndef Cube "Bench" {}\n')
    (project / "workstation.usda").write_text(
        '#usda 1.0\n(\n defaultPrim = "World"\n metersPerUnit = 0.01\n upAxis = "Z"\n)\n'
        'def Xform "World" {\n def Xform "Table" (references = @./parts/bench.usda@</Bench>) {}\n'
        ' def Shader "Texture" {\n asset inputs:file = @./textures/color.png@\n }\n}\n'
    )
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    cfg = tmp_path / "approval.json"
    cfg.write_text(
        json.dumps(
            dict(
                version=1,
                profile="trusted_project",
                roots={"project": str(project)},
                staging=str(staging),
            )
        )
    )
    cache = tmp_path / "cache"
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(cache))
    broker = Broker(str(cfg))
    scene = Scene(lambda: Usd.Stage.CreateInMemory(), object(), broker=broker)
    return project, cache, broker, scene


def test_prepare_preserves_sources_and_relative_dependencies(fixture):
    project, cache, broker, scene = fixture
    before = {
        str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in project.rglob("*")
        if p.is_file()
    }
    root, rel = broker.locate(str(project / "workstation.usda"))
    snap = broker.snapshot(root, rel)
    result = scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])
    assert result["prepared"] and result["loaded"] is False
    assert result["next_tool"] == "ur5e_dt__load_workstation"
    output = cache / "workstation" / "vention.usd"
    assert output.is_file()
    prepared = Usd.Stage.Open(str(output))
    assert prepared and prepared.GetDefaultPrim()
    assert prepared.GetPrimAtPath("/Workstation/Table")
    assert UsdGeom.GetStageMetersPerUnit(prepared) == pytest.approx(0.01)
    texture = (
        prepared.GetPrimAtPath("/Workstation/Texture").GetAttribute("inputs:file").Get()
    )
    assert Path(texture.resolvedPath).read_bytes() == b"test texture bytes"
    assert str(texture.resolvedPath).startswith(str(cache))
    assert {
        str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in project.rglob("*")
        if p.is_file()
    } == before
    with pytest.raises(AssetError, match="already exists"):
        scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])


def test_generic_import_by_absolute_path(fixture):
    project, _, broker, scene = fixture
    root, rel = broker.locate(str(project / "workstation.usda"))
    snap = broker.snapshot(root, rel)
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.Xform.Define(stage, "/World")
    result = scene.import_path(
        stage, snap["snapshot_id"], snap["manifest_sha256"], "/World/Imported"
    )
    assert result["source_preserved"] and stage.GetPrimAtPath(
        "/World/Imported/Asset/Table"
    )
    assert result["dependency_assets"] >= 1


def test_missing_mdl_texture_is_not_reported_as_prepared(fixture):
    project, cache, broker, scene = fixture
    (project / "material.mdl").write_text(
        'mdl 1.4;\nexport material m() = material(texture_2d("./missing.png"));\n'
    )
    usd = project / "workstation.usda"
    usd.write_text(
        usd.read_text().replace(
            "asset inputs:file = @./textures/color.png@",
            "asset inputs:file = @./material.mdl@",
        )
    )
    snap = broker.snapshot("project", "workstation.usda")
    with pytest.raises(AssetError, match="Missing MDL texture"):
        scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])
    with pytest.raises(AssetError, match="Missing MDL texture"):
        scene.import_path(
            Usd.Stage.CreateInMemory(),
            snap["snapshot_id"],
            snap["manifest_sha256"],
            "/World/Imported",
        )
    assert not (cache / "workstation" / "vention.usd").exists()


def test_materialx_nested_include_and_texture_are_checked(fixture):
    project, cache, broker, scene = fixture
    (project / "child.mtlx").write_text(
        '<materialx><image><input type="filename" value="textures/color.png"/></image></materialx>'
    )
    (project / "material.mtlx").write_text(
        '<materialx xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="child.mtlx"/></materialx>'
    )
    usd = project / "workstation.usda"
    usd.write_text(
        usd.read_text().replace(
            "asset inputs:file = @./textures/color.png@",
            "asset inputs:file = @./material.mtlx@",
        )
    )
    snap = broker.snapshot("project", "workstation.usda")
    result = scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])
    assert result["material_checks"]["materialx_dependency_count"] == 2
    (project / "textures" / "color.png").unlink()
    missing = broker.snapshot("project", "workstation.usda")
    with pytest.raises(AssetError, match="Missing MaterialX dependency"):
        scene.prepare_workstation(missing["snapshot_id"], missing["manifest_sha256"])


@pytest.mark.parametrize(
    "material,expected",
    [
        ("import local::other;\n", "MDL import"),
        (
            'export material m() = material(light_profile("missing.ies"));\n',
            "Missing MDL resource",
        ),
        (
            'export material m() = material(bsdf_measurement("missing.mbsdf"));\n',
            "Missing MDL resource",
        ),
    ],
)
def test_missing_or_unsupported_nested_mdl_resources_fail(fixture, material, expected):
    project, cache, broker, scene = fixture
    (project / "material.mdl").write_text("mdl 1.4;\n" + material)
    usd = project / "workstation.usda"
    usd.write_text(
        usd.read_text().replace(
            "asset inputs:file = @./textures/color.png@",
            "asset inputs:file = @./material.mdl@",
        )
    )
    snap = broker.snapshot("project", "workstation.usda")
    with pytest.raises(AssetError, match=expected):
        scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])
    assert not (cache / "workstation" / "vention.usd").exists()


def test_materialx_cannot_hide_unchecked_nested_mdl(fixture):
    project, cache, broker, scene = fixture
    (project / "nested.mdl").write_text("mdl 1.4;")
    (project / "material.mtlx").write_text(
        '<materialx><input type="filename" value="nested.mdl"/></materialx>'
    )
    usd = project / "workstation.usda"
    usd.write_text(
        usd.read_text().replace(
            "asset inputs:file = @./textures/color.png@",
            "asset inputs:file = @./material.mtlx@",
        )
    )
    snap = broker.snapshot("project", "workstation.usda")
    with pytest.raises(AssetError, match="Nested material or scene"):
        scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])
    assert not (cache / "workstation" / "vention.usd").exists()


def test_materialx_reported_as_usd_layer_is_still_checked(fixture, monkeypatch):
    project, cache, broker, scene = fixture
    (project / "material.mtlx").write_text(
        '<materialx><input type="filename" value="missing.png"/></materialx>'
    )
    usd = project / "workstation.usda"
    usd.write_text(
        usd.read_text().replace(
            "asset inputs:file = @./textures/color.png@",
            "asset inputs:file = @./material.mtlx@",
        )
    )
    snap = broker.snapshot("project", "workstation.usda")
    compute = UsdUtils.ComputeAllDependencies

    def materialx_as_layer(*args, **kwargs):
        layers, assets, missing = compute(*args, **kwargs)
        material_paths = [str(a) for a in assets if str(a).endswith(".mtlx")]
        return (
            list(layers) + [SimpleNamespace(realPath=p) for p in material_paths],
            [a for a in assets if not str(a).endswith(".mtlx")],
            missing,
        )

    monkeypatch.setattr(UsdUtils, "ComputeAllDependencies", materialx_as_layer)
    with pytest.raises(AssetError, match="Missing MaterialX dependency"):
        scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])
    assert not (cache / "workstation" / "vention.usd").exists()


@pytest.mark.parametrize(
    "expression",
    ['texture_2d("https://example.invalid/a.png")', "texture_2d(make_path())"],
)
def test_unsupported_mdl_texture_is_not_silently_accepted(fixture, expression):
    project, cache, broker, scene = fixture
    (project / "material.mdl").write_text(
        "mdl 1.4;\nexport material m() = material(" + expression + ");\n"
    )
    usd = project / "workstation.usda"
    usd.write_text(
        usd.read_text().replace(
            "asset inputs:file = @./textures/color.png@",
            "asset inputs:file = @./material.mdl@",
        )
    )
    snap = broker.snapshot("project", "workstation.usda")
    with pytest.raises(AssetError, match="unsupported|validated|network"):
        scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])
    assert not (cache / "workstation" / "vention.usd").exists()


def test_missing_dependency_never_publishes(fixture):
    project, cache, broker, scene = fixture
    (project / "parts" / "bench.usda").unlink()
    snap = broker.snapshot("project", "workstation.usda")
    with pytest.raises(AssetError, match="dependencies"):
        scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])
    assert not (cache / "workstation" / "vention.usd").exists()


def test_unapproved_and_symlink_paths_are_rejected(fixture, tmp_path):
    project, cache, broker, scene = fixture
    with pytest.raises(AssetError, match="outside"):
        broker.locate(str(tmp_path / "other.usda"))
    with pytest.raises(AssetError):
        broker.locate(str(project / ".." / "other.usda"))
    (project / "link.usda").symlink_to(project / "workstation.usda")
    with pytest.raises((AssetError, OSError)):
        broker.snapshot(*broker.locate(str(project / "link.usda")))
    assert not cache.exists()


def test_existing_file_and_cache_inside_project_are_not_overwritten(
    fixture, monkeypatch
):
    project, cache, broker, scene = fixture
    snap = broker.snapshot("project", "workstation.usda")
    existing = cache / "workstation" / "vention.usd"
    existing.parent.mkdir(mode=0o700, parents=True)
    cache.chmod(0o700)
    existing.write_bytes(b"keep me")
    with pytest.raises(AssetError, match="already exists"):
        scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])
    assert existing.read_bytes() == b"keep me"
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(project / "cache"))
    with pytest.raises(AssetError, match="disjoint"):
        scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])

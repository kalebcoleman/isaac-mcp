"""Trusted multi-file workstation preparation with real OpenUSD, without Kit."""

import hashlib
import os
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
    output = cache / "ur5e-dt" / "workstation" / "vention.usd"
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
    assert not (cache / "ur5e-dt" / "workstation" / "vention.usd").exists()


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
    assert not (cache / "ur5e-dt" / "workstation" / "vention.usd").exists()


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
    assert not (cache / "ur5e-dt" / "workstation" / "vention.usd").exists()


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
    assert not (cache / "ur5e-dt" / "workstation" / "vention.usd").exists()


@pytest.mark.parametrize("reported_as", ["resolved", "unresolved"])
def test_kit_runtime_mdl_is_checked_without_rejecting_it(
    fixture, tmp_path, monkeypatch, reported_as
):
    project, cache, broker, scene = fixture
    runtime = tmp_path / "OmniPBR.mdl"
    runtime.write_text("mdl 1.4;\n")
    scene.runtime_assets["OmniPBR.mdl"] = {
        "path": str(runtime),
        "sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
    }
    snap = broker.snapshot("project", "workstation.usda")
    compute = UsdUtils.ComputeAllDependencies

    def with_runtime(*args, **kwargs):
        layers, assets, missing = compute(*args, **kwargs)
        if reported_as == "resolved":
            return layers, list(assets) + [str(runtime)], missing
        return layers, assets, list(missing) + ["OmniPBR.mdl"]

    monkeypatch.setattr(UsdUtils, "ComputeAllDependencies", with_runtime)
    result = scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])
    assert result["prepared"]
    assert "OmniPBR.mdl" in result["runtime_dependencies"]
    runtime.write_text("changed")
    with pytest.raises(AssetError, match="runtime material is missing or changed"):
        scene.validate_project_materials(snap["snapshot_id"], snap["manifest_sha256"])


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
    assert not (cache / "ur5e-dt" / "workstation" / "vention.usd").exists()


def test_missing_dependency_never_publishes(fixture):
    project, cache, broker, scene = fixture
    (project / "parts" / "bench.usda").unlink()
    snap = broker.snapshot("project", "workstation.usda")
    with pytest.raises(AssetError, match="dependencies"):
        scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])
    assert not (cache / "ur5e-dt" / "workstation" / "vention.usd").exists()


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
    existing = cache / "ur5e-dt" / "workstation" / "vention.usd"
    existing.parent.mkdir(mode=0o700, parents=True)
    cache.chmod(0o700)
    (cache / "ur5e-dt").chmod(0o700)
    existing.write_bytes(b"keep me")
    with pytest.raises(AssetError, match="already exists"):
        scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])
    assert existing.read_bytes() == b"keep me"
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(project / "cache"))
    with pytest.raises(AssetError, match="disjoint"):
        scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])


def _prepare(project, broker, scene):
    root, rel = broker.locate(str(project / "workstation.usda"))
    snap = broker.snapshot(root, rel)
    return scene.prepare_workstation(snap["snapshot_id"], snap["manifest_sha256"])


def test_prepare_writes_to_the_first_store_of_asset_path(fixture, tmp_path, monkeypatch):
    project, legacy_cache, broker, scene = fixture
    monkeypatch.delenv("UR5E_DT_ASSET_CACHE", raising=False)
    first, second = tmp_path / "store-a", tmp_path / "store-b"
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", f"{first}{os.pathsep}{second}")
    assert _prepare(project, broker, scene)["prepared"]
    assert (first / "ur5e-dt" / "workstation" / "vention.usd").is_file()
    assert not (second / "ur5e-dt").exists()
    assert not (legacy_cache / "ur5e-dt" / "workstation").exists()


def test_prepare_uses_the_workspace_asset_folder_and_never_the_old_name(
    fixture, tmp_path, monkeypatch
):
    project, legacy_cache, broker, scene = fixture
    monkeypatch.delenv("UR5E_DT_ASSET_CACHE", raising=False)
    monkeypatch.delenv("ISAAC_MCP_ASSET_PATH", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "assets").mkdir(mode=0o700)  # an older folder: read by the twin, never written
    monkeypatch.setenv("ISAAC_MCP_WORKSPACE", str(workspace))
    assert _prepare(project, broker, scene)["prepared"]
    assert (workspace / "isaac-mcp-assets" / "ur5e-dt" / "workstation" / "vention.usd").is_file()
    assert not (workspace / "assets" / "ur5e-dt").exists()
    assert not (legacy_cache / "ur5e-dt" / "workstation").exists()


def test_prepare_defaults_to_the_asset_folder_next_to_the_checkout(fixture, tmp_path, monkeypatch):
    import sim_tools.core as core

    project, legacy_cache, broker, scene = fixture
    for name in ("UR5E_DT_ASSET_CACHE", "ISAAC_MCP_ASSET_PATH", "ISAAC_MCP_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    fake_core = tmp_path / "checkout" / "exts" / "sim-tools" / "sim_tools" / "core.py"
    monkeypatch.setattr(core, "__file__", str(fake_core))
    assert _prepare(project, broker, scene)["prepared"]
    assert (tmp_path / "isaac-mcp-assets" / "ur5e-dt" / "workstation" / "vention.usd").is_file()

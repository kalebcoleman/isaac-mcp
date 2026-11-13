"""No simulator is needed to validate an external work-cell pack."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "workcell_pack", Path(__file__).resolve().parents[2] / "exts/ur5e-dt/ur5e_dt/asset_pack.py")
pack_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pack_module)


@pytest.fixture
def pack_root(tmp_path):
    directory = tmp_path / "cell" / "v1"
    directory.mkdir(parents=True)
    payload = b"test fixture, not a vendor asset"
    (directory / "part.usda").write_bytes(payload)
    manifest = {"id": "cell", "version": "v1", "files": [{
        "asset_id": "robot", "path": "part.usda", "sha256": hashlib.sha256(payload).hexdigest(),
        "source": "unit test", "license": "test fixture", "redistributable": False}]}
    (directory / "pack.yaml").write_text(json.dumps(manifest))
    return tmp_path, directory, manifest


def test_resolve_logical_ids_and_environment_root(pack_root, monkeypatch):
    root, directory, _ = pack_root
    monkeypatch.setenv("ISAAC_MCP_ASSET_ROOT", str(root))
    pack = pack_module.resolve_pack({"pack": {"id": "cell", "version": "v1"}, "assets": {"robot": "robot"}})
    assert pack.asset("robot") == directory / "part.usda"
    with pytest.raises(pack_module.PackError, match="no asset id"):
        pack.asset("absent")


def test_missing_pack_has_sync_instruction(tmp_path):
    with pytest.raises(pack_module.PackError, match="sync this private pack first"):
        pack_module.AssetPack(tmp_path, "cell", "v1")


def test_tampered_file_is_rejected(pack_root):
    root, directory, _ = pack_root
    (directory / "part.usda").write_text("changed")
    with pytest.raises(pack_module.PackError, match="Checksum mismatch"):
        pack_module.AssetPack(root, "cell", "v1")


def test_unlisted_file_is_rejected(pack_root):
    root, directory, _ = pack_root
    (directory / "unused.txt").write_text("not used")
    with pytest.raises(pack_module.PackError, match="not listed"):
        pack_module.AssetPack(root, "cell", "v1")


def test_external_usd_dependency_is_rejected(pack_root):
    pytest.importorskip("pxr.UsdUtils")
    root, directory, manifest = pack_root
    outside = root / "outside.usda"
    outside.write_text('#usda 1.0\n def Xform "Other" {}\n')
    payload = b'#usda 1.0\n (subLayers = [@../../outside.usda@])\n'
    (directory / "part.usda").write_bytes(payload)
    manifest["files"][0]["sha256"] = hashlib.sha256(payload).hexdigest()
    (directory / "pack.yaml").write_text(json.dumps(manifest))
    pack = pack_module.AssetPack(root, "cell", "v1")
    with pytest.raises(pack_module.PackError, match="outside manifest"):
        pack.verify_dependencies()


@pytest.mark.parametrize("field,value", [
    ("path", "../part.usda"), ("path", "/tmp/part.usda"), ("source", ""),
    ("license", ""), ("redistributable", "false"), ("sha256", "bad"), ("asset_id", "../robot")])
def test_malformed_file_metadata_is_rejected(pack_root, field, value):
    root, directory, manifest = pack_root
    manifest["files"][0][field] = value
    (directory / "pack.yaml").write_text(json.dumps(manifest))
    with pytest.raises(pack_module.PackError):
        pack_module.AssetPack(root, "cell", "v1")


def test_symlink_escape_is_rejected(pack_root):
    root, directory, _ = pack_root
    (root / "outside").write_text("private")
    (directory / "part.usda").unlink()
    (directory / "part.usda").symlink_to(root / "outside")
    with pytest.raises(pack_module.PackError, match="escapes pack"):
        pack_module.AssetPack(root, "cell", "v1")


def test_manifest_symlink_is_rejected(pack_root):
    root, directory, manifest = pack_root
    external = root / "manifest.yaml"
    external.write_text(json.dumps(manifest))
    (directory / "pack.yaml").unlink()
    (directory / "pack.yaml").symlink_to(external)
    with pytest.raises(pack_module.PackError, match="Manifest must not"):
        pack_module.AssetPack(root, "cell", "v1")


def test_canonical_duplicate_path_is_rejected(pack_root):
    root, directory, manifest = pack_root
    duplicate = dict(manifest["files"][0], path="./part.usda", asset_id="second")
    manifest["files"].append(duplicate)
    (directory / "pack.yaml").write_text(json.dumps(manifest))
    with pytest.raises(pack_module.PackError, match="Duplicate file path"):
        pack_module.AssetPack(root, "cell", "v1")


def test_invalid_usd_is_rejected(pack_root):
    pytest.importorskip("pxr.UsdUtils")
    root, _, _ = pack_root
    with pytest.raises(pack_module.PackError, match="Invalid USD"):
        pack_module.AssetPack(root, "cell", "v1").verify_dependencies()


@pytest.mark.parametrize("pack_id,version", [("..", "v1"), ("cell", "../v1"), ("/tmp", "v1")])
def test_invalid_pack_identity_is_rejected(tmp_path, pack_id, version):
    with pytest.raises(pack_module.PackError):
        pack_module.AssetPack(tmp_path, pack_id, version)

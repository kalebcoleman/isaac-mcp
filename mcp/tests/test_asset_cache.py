"""Tests for the UR5e twin asset resolver and manifest."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "exts" / "ur5e-dt" / "ur5e_dt" / "asset_cache.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ur5e_asset_cache_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def fake_manifest(module, tmp_path, content: bytes = b"cached", target: str = "robot/test.usd"):
    """Point the module at a tiny manifest and return (manifest_path, entry)."""
    data = {
        "version": 1,
        "asset_root": "https://example.invalid/Assets/Isaac/5.1",
        "remote": {target: "Isaac/Robots/Test/test.usd"},
        "fetch": [
            {
                "id": "test",
                "files": [
                    {
                        "target": target,
                        "source": "Isaac/Robots/Test/test.usd",
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size": len(content),
                    }
                ],
            }
        ],
    }
    path = tmp_path / "assets-manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    module.MANIFEST_PATH = path
    return path, data


def test_cache_dir_honours_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path))
    module = load_module()
    assert module.cache_dir() == tmp_path.resolve()


def test_bundled_asset_resolves_to_file_uri():
    module = load_module()
    uri = module.resolve_uri("gripper/QuickChanger.usd")
    assert uri.startswith("file://")
    assert Path(uri.removeprefix("file://")).is_file()


def test_bundled_directory_resolves():
    module = load_module()
    directory = module.resolve_dir("objects/fmb1")
    assert directory.is_dir()
    assert any(directory.glob("*.usda"))


def test_remote_fallback_comes_from_the_manifest():
    module = load_module()
    uri = module.remote_uri("robot/ur5e.usd")
    assert uri is not None
    assert uri.endswith("/Isaac/Robots/UniversalRobots/ur5e/ur5e.usd")
    assert "s3-us-west-2.amazonaws.com" in uri


def test_cache_wins_over_the_remote_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path))
    module = load_module()
    fake_manifest(module, tmp_path)
    cached = tmp_path / "robot" / "test.usd"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"cached")
    assert module.resolve_uri("robot/test.usd") == cached.as_uri()


def test_corrupt_cache_raises_with_fetch_guidance(monkeypatch, tmp_path):
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path))
    module = load_module()
    fake_manifest(module, tmp_path)
    cached = tmp_path / "robot" / "test.usd"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"broken")  # same length as b"cached", different bytes
    with pytest.raises(module.AssetCorrupt) as excinfo:
        module.resolve_uri("robot/test.usd")
    message = str(excinfo.value)
    assert "corrupt" in message
    assert "sha256 mismatch" in message
    assert "fetch_ur5e_assets.py --force" in message


def test_corrupt_cache_size_mismatch_is_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path))
    module = load_module()
    fake_manifest(module, tmp_path)
    cached = tmp_path / "robot" / "test.usd"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"evil")
    with pytest.raises(module.AssetCorrupt) as excinfo:
        module.resolve_uri("robot/test.usd")
    assert "size mismatch" in str(excinfo.value)


def test_remote_fallback_is_used_when_online(monkeypatch, tmp_path):
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path))
    monkeypatch.delenv("UR5E_DT_OFFLINE", raising=False)
    module = load_module()
    fake_manifest(module, tmp_path)
    uri = module.resolve_uri("robot/test.usd")
    assert uri == "https://example.invalid/Assets/Isaac/5.1/Isaac/Robots/Test/test.usd"


def test_offline_uses_valid_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path))
    monkeypatch.setenv("UR5E_DT_OFFLINE", "1")
    module = load_module()
    fake_manifest(module, tmp_path)
    cached = tmp_path / "robot" / "test.usd"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"cached")
    assert module.resolve_uri("robot/test.usd") == cached.as_uri()


def test_manual_asset_uses_operator_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path))
    module = load_module()
    cached = tmp_path / "workstation" / "vention.usd"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"operator-supplied")
    assert module.resolve_uri("workstation/vention.usd") == cached.as_uri()


def test_offline_missing_cache_raises_with_guidance(monkeypatch, tmp_path):
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path))
    monkeypatch.setenv("UR5E_DT_OFFLINE", "1")
    module = load_module()
    fake_manifest(module, tmp_path)
    with pytest.raises(module.AssetMissing) as excinfo:
        module.resolve_uri("robot/test.usd")
    message = str(excinfo.value)
    assert "offline" in message
    assert "fetch_ur5e_assets.py" in message


def test_manual_asset_raises_with_guidance(monkeypatch, tmp_path):
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path))
    module = load_module()
    with pytest.raises(module.AssetMissing) as excinfo:
        module.resolve_uri("workstation/vention.usd")
    message = str(excinfo.value)
    assert "workstation/vention.usd" in message
    assert "must be supplied locally" in message
    assert str(tmp_path) in message


def test_manifest_is_complete_and_fetch_targets_are_absent():
    module = load_module()
    manifest = module.manifest()
    assert manifest["version"] == 1
    assert manifest["asset_root"].endswith("/Assets/Isaac/5.1")
    sources = {}
    for group in manifest["fetch"]:
        for entry in group["files"]:
            sources[entry["target"]] = entry["source"]
            assert len(entry["sha256"]) == 64
            assert entry["size"] > 0
            assert not (REPO / "exts" / "ur5e-dt" / "assets" / entry["target"]).exists()
    assert set(manifest["remote"]) <= set(sources)
    for target, source in manifest["remote"].items():
        assert sources[target] == source
    for entry in manifest.get("manual", []):
        assert entry["target"] not in sources

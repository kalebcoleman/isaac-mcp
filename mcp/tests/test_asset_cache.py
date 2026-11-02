"""Tests for the UR5e twin asset resolver and manifest."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

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
    assert Path(unquote(urlparse(uri).path)).is_file()


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


def test_operator_drop_in_is_used_without_hash_check(monkeypatch, tmp_path):
    """A licensed copy from another Isaac release must not be refused as corrupt."""
    cache = tmp_path / "cache"
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(cache))
    module = load_module()
    fake_manifest(module, tmp_path)
    module.ASSETS_DIR = tmp_path / "assets"
    drop_in = module.ASSETS_DIR / "robot" / "test.usd"
    drop_in.parent.mkdir(parents=True)
    drop_in.write_bytes(b"operator copy from Isaac 6.2")
    assert module.resolve_uri("robot/test.usd") == drop_in.as_uri()
    assert module.resolve_path("robot/test.usd") == drop_in


def group_manifest(module, tmp_path, root=b"root", layer=b"layer"):
    """A two-file fetch group: a root USD and a sublayer it references."""
    data = {
        "version": 1,
        "asset_root": "https://example.invalid/Assets/Isaac/5.1",
        "remote": {"robot/test.usd": "Isaac/Robots/Test/test.usd"},
        "fetch": [{"id": "test", "files": [
            {"target": "robot/test.usd", "source": "Isaac/Robots/Test/test.usd",
             "sha256": hashlib.sha256(root).hexdigest(), "size": len(root)},
            {"target": "robot/configuration/base.usd",
             "source": "Isaac/Robots/Test/configuration/base.usd",
             "sha256": hashlib.sha256(layer).hexdigest(), "size": len(layer)},
        ]}],
    }
    path = tmp_path / "assets-manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    module.MANIFEST_PATH = path
    module.ASSETS_DIR = tmp_path / "no-drop-ins"


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_cached_root_checks_its_sublayers(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(cache))
    module = load_module()
    group_manifest(module, tmp_path)
    write(cache / "robot" / "test.usd", b"root")
    write(cache / "robot" / "configuration" / "base.usd", b"LAYER")  # same size, wrong bytes
    with pytest.raises(module.AssetCorrupt) as excinfo:
        module.resolve_uri("robot/test.usd")
    assert "robot/configuration/base.usd" in str(excinfo.value)


def test_cached_root_with_missing_sublayer_is_refused(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(cache))
    module = load_module()
    group_manifest(module, tmp_path)
    write(cache / "robot" / "test.usd", b"root")
    with pytest.raises(module.AssetMissing) as excinfo:
        module.resolve_uri("robot/test.usd")
    assert "robot/configuration/base.usd" in str(excinfo.value)


def test_complete_cached_group_resolves(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(cache))
    module = load_module()
    group_manifest(module, tmp_path)
    write(cache / "robot" / "test.usd", b"root")
    write(cache / "robot" / "configuration" / "base.usd", b"layer")
    assert module.resolve_uri("robot/test.usd") == (cache / "robot" / "test.usd").as_uri()



def relocated_manifest(module, tmp_path):
    path, data = fake_manifest(module, tmp_path, target="sensors/test.usd")
    data["fetch"][0]["files"][0]["cache_path"] = "Isaac/Sensors/Intel/RealSense/test.usd"
    data["fetch"][0]["files"].append({
        "target": "Isaac/Materials/material.mdl", "source": "Isaac/Materials/material.mdl",
        "sha256": hashlib.sha256(b"material").hexdigest(), "size": 8,
    })
    path.write_text(json.dumps(data))
    module.ASSETS_DIR = tmp_path / "drop-ins"
    return path, data


def test_relative_sensor_dependencies_keep_upstream_layout(monkeypatch, tmp_path):
    module = load_module()
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("UR5E_DT_OFFLINE", "1")
    relocated_manifest(module, tmp_path)
    usd = module.cached_path("sensors/test.usd")
    material = module.cached_path("Isaac/Materials/material.mdl")
    write(usd, b"cached")
    write(material, b"material")
    assert (usd.parent / "../../../Materials/material.mdl").resolve() == material
    assert module.resolve_uri("sensors/test.usd") == usd.as_uri()
    assert module.resolve_path("sensors/test.usd") == usd
    material.unlink()
    with pytest.raises(module.AssetMissing, match="Isaac/Materials/material.mdl"):
        module.resolve_uri("sensors/test.usd")
    write(material, b"MATERIAL")
    with pytest.raises(module.AssetCorrupt, match="sha256 mismatch"):
        module.resolve_uri("sensors/test.usd")


def test_old_flat_sensor_cache_requires_refetch(monkeypatch, tmp_path):
    module = load_module()
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("UR5E_DT_OFFLINE", "1")
    relocated_manifest(module, tmp_path)
    old = tmp_path / "cache/sensors/test.usd"
    write(old, b"cached")
    with pytest.raises(module.AssetMissing, match="fetch_ur5e_assets.py"):
        module.resolve_uri("sensors/test.usd")
    assert old.read_bytes() == b"cached"


def test_relocated_sensor_still_prefers_operator_drop_in(monkeypatch, tmp_path):
    module = load_module()
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path / "cache"))
    relocated_manifest(module, tmp_path)
    drop_in = module.ASSETS_DIR / "sensors/test.usd"
    write(drop_in, b"different licensed release with collected materials")
    assert module.resolve_uri("sensors/test.usd") == drop_in.as_uri()


def test_manifest_cache_location_cannot_escape(monkeypatch, tmp_path):
    module = load_module()
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path / "cache"))
    path, data = relocated_manifest(module, tmp_path)
    for location in ("../outside", "/absolute"):
        data["fetch"][0]["files"][0]["cache_path"] = location
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="relative"):
            module.cached_path("sensors/test.usd")


def test_fetch_and_check_use_manifest_cache_location(monkeypatch, tmp_path):
    module = load_module()
    cache = tmp_path / "cache"
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(cache))
    _, data = relocated_manifest(module, tmp_path)
    spec = importlib.util.spec_from_file_location("sensor_fetch_test", REPO / "scripts/fetch_ur5e_assets.py")
    fetcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetcher)
    source = tmp_path / "mirror"
    for entry, content in zip(data["fetch"][0]["files"], (b"cached", b"material")):
        write(source / entry["source"], content)
    assert fetcher.check(data, cache) == 1
    assert fetcher.fetch(data, cache, str(source), False) == 0
    assert fetcher.check(data, cache) == 0
    assert not (cache / "sensors/test.usd").exists()
    assert module.resolve_path("sensors/test.usd").read_bytes() == b"cached"


# --- layered stores, user pins, project file, strict mode -------------------

ASSET_ENV = (
    "ISAAC_MCP_ASSET_PATH",
    "ISAAC_MCP_ASSET_STRICT",
    "ISAAC_MCP_ASSET_REMOTE_ROOT",
    "ISAAC_MCP_PROJECT",
    "UR5E_DT_ASSET_CACHE",
    "UR5E_DT_ASSET_ROOT",
    "UR5E_DT_OFFLINE",
)


@pytest.fixture(autouse=True)
def clean_asset_env(monkeypatch, tmp_path):
    """Run every test without ambient asset settings or a project file above the cwd."""
    for name in ASSET_ENV:
        monkeypatch.delenv(name, raising=False)
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)


def isolated(module, tmp_path):
    """Point the drop-in folder at an empty directory and return the manifest path."""
    drop = tmp_path / "dropin"
    drop.mkdir(exist_ok=True)
    module.ASSETS_DIR = drop
    return drop


def put(path: Path, content: bytes = b"data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def sums(base: Path, **files: bytes) -> None:
    """Write a coreutils-style SHA256SUMS file for *files* (name -> content)."""
    lines = [f"{hashlib.sha256(c).hexdigest()}  {name}" for name, c in sorted(files.items())]
    (base / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_asset_path_is_an_ordered_list(monkeypatch, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", f"{first}{os.pathsep}{second}")
    module = load_module()
    assert module.user_stores() == [first.resolve(), second.resolve()]
    assert module.cache_dir() == first.resolve()


def test_earlier_store_wins(monkeypatch, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    put(first / "ur5e-dt" / "x" / "one.usd", b"first")
    put(second / "ur5e-dt" / "x" / "one.usd", b"second")
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", f"{first}{os.pathsep}{second}")
    module = load_module()
    isolated(module, tmp_path)
    assert module.resolve_path("x/one.usd").read_bytes() == b"first"


def test_pack_folder_beats_flat_layout(monkeypatch, tmp_path):
    store = tmp_path / "store"
    put(store / "x" / "one.usd", b"flat")
    put(store / "ur5e-dt" / "x" / "one.usd", b"pack")
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    module = load_module()
    isolated(module, tmp_path)
    assert module.resolve_path("x/one.usd").read_bytes() == b"pack"


def test_flat_layout_still_resolves(monkeypatch, tmp_path):
    store = tmp_path / "store"
    put(store / "x" / "one.usd", b"flat")
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    module = load_module()
    isolated(module, tmp_path)
    assert module.resolve_path("x/one.usd").read_bytes() == b"flat"


def test_legacy_names_are_aliases_and_warn_once(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path))
    monkeypatch.setenv("UR5E_DT_OFFLINE", "1")
    monkeypatch.setenv("UR5E_DT_ASSET_ROOT", "https://example.invalid/root/")
    module = load_module()
    with caplog.at_level("WARNING"):
        assert module.cache_dir() == tmp_path.resolve()
        assert module.cache_dir() == tmp_path.resolve()
        assert module.offline() is True
        assert module.asset_root() == "https://example.invalid/root"
    text = caplog.text
    assert text.count("UR5E_DT_ASSET_CACHE is deprecated") == 1
    assert "ISAAC_MCP_ASSET_PATH" in text


def test_new_names_win_over_legacy_names(monkeypatch, tmp_path):
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(tmp_path / "new"))
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(tmp_path / "old"))
    monkeypatch.setenv("ISAAC_MCP_ASSET_STRICT", "0")
    monkeypatch.setenv("UR5E_DT_OFFLINE", "1")
    module = load_module()
    assert module.cache_dir() == (tmp_path / "new").resolve()
    assert module.offline() is False


def test_user_pin_accepts_a_modified_robot(monkeypatch, tmp_path):
    """A lab-modified file differs from the NVIDIA manifest hash; its pin decides."""
    module = load_module()
    fake_manifest(module, tmp_path, content=b"nvidia", target="robot/test.usd")
    isolated(module, tmp_path)
    store = tmp_path / "store"
    pack = store / "ur5e-dt"
    put(pack / "robot" / "test.usd", b"lab-modified")
    sums(pack, **{"robot/test.usd": b"lab-modified"})
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    assert module.resolve_uri("robot/test.usd").startswith("file://")
    assert module.resolve_info("robot/test.usd")["status"] == "pinned"


def test_user_pin_mismatch_is_reported_with_the_pin_file(monkeypatch, tmp_path):
    module = load_module()
    fake_manifest(module, tmp_path, content=b"nvidia", target="robot/test.usd")
    isolated(module, tmp_path)
    store = tmp_path / "store"
    pack = store / "ur5e-dt"
    put(pack / "robot" / "test.usd", b"edited after pinning")
    sums(pack, **{"robot/test.usd": b"original"})
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    with pytest.raises(module.AssetCorrupt) as excinfo:
        module.resolve_path("robot/test.usd")
    message = str(excinfo.value)
    assert "SHA256SUMS" in message
    assert "isaac_assets.py pin" in message
    assert module.resolve_info("robot/test.usd")["status"] == "corrupt"


def test_unpinned_manifest_file_in_pack_folder_is_still_checked(monkeypatch, tmp_path):
    module = load_module()
    fake_manifest(module, tmp_path, content=b"expected", target="robot/test.usd")
    isolated(module, tmp_path)
    store = tmp_path / "store"
    put(store / "ur5e-dt" / "robot" / "test.usd", b"something else")
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    with pytest.raises(module.AssetCorrupt):
        module.resolve_path("robot/test.usd")


def test_fetch_group_siblings_are_found_in_the_same_pack_folder(monkeypatch, tmp_path):
    module = load_module()
    data = {
        "version": 1,
        "asset_root": "https://example.invalid",
        "remote": {},
        "fetch": [
            {
                "id": "g",
                "files": [
                    {"target": "g/a.usd", "sha256": hashlib.sha256(b"a").hexdigest(), "size": 1},
                    {"target": "g/b.usd", "sha256": hashlib.sha256(b"b").hexdigest(), "size": 1},
                ],
            }
        ],
    }
    manifest_path = tmp_path / "assets-manifest.json"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    module.MANIFEST_PATH = manifest_path
    isolated(module, tmp_path)
    store = tmp_path / "store"
    put(store / "ur5e-dt" / "g" / "a.usd", b"a")
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    with pytest.raises(module.AssetMissing):  # sibling b.usd absent
        module.resolve_path("g/a.usd")
    put(store / "ur5e-dt" / "g" / "b.usd", b"b")
    assert module.resolve_path("g/a.usd").name == "a.usd"


def test_project_store_overrides_dropin_and_user_store(monkeypatch, tmp_path):
    project = tmp_path / "proj"
    put(project / "assets" / "ur5e-dt" / "x" / "one.usd", b"project")
    (project / "isaac-mcp.json").write_text(
        json.dumps({"asset_path": ["./assets"]}), encoding="utf-8"
    )
    user = tmp_path / "user"
    put(user / "ur5e-dt" / "x" / "one.usd", b"user")
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(user))
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(project))
    module = load_module()
    drop = isolated(module, tmp_path)
    put(drop / "x" / "one.usd", b"dropin")
    info = module.resolve_info("x/one.usd")
    assert info["layer"] == "project"
    assert module.resolve_path("x/one.usd").read_bytes() == b"project"
    assert module.stores()[0] == (project / "assets").resolve()


def test_project_file_is_found_from_the_working_directory(monkeypatch, tmp_path):
    project = tmp_path / "proj"
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    (project / "isaac-mcp.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(nested)
    module = load_module()
    assert module.project_file() == (project / "isaac-mcp.json")


def test_project_strict_and_environment_override(monkeypatch, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "isaac-mcp.json").write_text(json.dumps({"strict": True}), encoding="utf-8")
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(project))
    module = load_module()
    assert module.offline() is True
    monkeypatch.setenv("ISAAC_MCP_ASSET_STRICT", "0")
    assert module.offline() is False


def test_malformed_project_file_is_an_error(monkeypatch, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "isaac-mcp.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(project))
    module = load_module()
    with pytest.raises(ValueError, match="isaac-mcp.json"):
        module.stores()


def test_project_asset_path_must_be_a_list(monkeypatch, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "isaac-mcp.json").write_text(json.dumps({"asset_path": "x"}), encoding="utf-8")
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(project))
    module = load_module()
    with pytest.raises(ValueError, match="asset_path"):
        module.project_stores()


def test_strict_mode_refuses_the_remote_fallback_and_says_so(monkeypatch, tmp_path):
    module = load_module()
    fake_manifest(module, tmp_path)
    isolated(module, tmp_path)
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(tmp_path / "empty"))
    assert module.resolve_uri("robot/test.usd").startswith("https://")
    monkeypatch.setenv("ISAAC_MCP_ASSET_STRICT", "1")
    with pytest.raises(module.AssetMissing, match="ISAAC_MCP_ASSET_STRICT"):
        module.resolve_uri("robot/test.usd")
    info = module.resolve_info("robot/test.usd")
    assert info["layer"] == "missing"
    assert "strict" in info["detail"]


def test_resolve_info_reports_remote_and_missing(monkeypatch, tmp_path):
    module = load_module()
    fake_manifest(module, tmp_path)
    isolated(module, tmp_path)
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(tmp_path / "empty"))
    remote = module.resolve_info("robot/test.usd")
    assert (remote["layer"], remote["status"]) == ("remote", "remote")
    assert remote["uri"].startswith("https://example.invalid/")
    missing = module.resolve_info("workstation/vention.usd")
    assert (missing["layer"], missing["status"]) == ("missing", "missing")


def test_resolve_info_never_raises_on_a_corrupt_file(monkeypatch, tmp_path):
    module = load_module()
    fake_manifest(module, tmp_path, content=b"expected", target="robot/test.usd")
    isolated(module, tmp_path)
    store = tmp_path / "store"
    put(store / "robot" / "test.usd", b"wrong")
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    info = module.resolve_info("robot/test.usd")
    assert info["status"] == "corrupt"
    assert len(info["sha256"]) == 64


def test_known_assets_include_manifest_entries_and_pinned_files(monkeypatch, tmp_path):
    module = load_module()
    fake_manifest(module, tmp_path)
    isolated(module, tmp_path)
    store = tmp_path / "store"
    pack = store / "ur5e-dt"
    put(pack / "lab" / "thing.usd", b"thing")
    sums(pack, **{"lab/thing.usd": b"thing"})
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    names = module.known_assets()
    assert "robot/test.usd" in names
    assert "lab/thing.usd" in names


def test_pins_parser_accepts_coreutils_forms(tmp_path):
    module = load_module()
    digest = hashlib.sha256(b"x").hexdigest()
    (tmp_path / "SHA256SUMS").write_text(
        f"{digest}  a/one.usd\n{digest} *b/two.usd\n{digest}  ./c/three.usd\nnot a line\n",
        encoding="utf-8",
    )
    assert module.pins(tmp_path) == {
        "a/one.usd": digest,
        "b/two.usd": digest,
        "c/three.usd": digest,
    }


def test_directory_assets_resolve_from_every_layer(monkeypatch, tmp_path):
    project = tmp_path / "proj"
    (project / "assets" / "ur5e-dt" / "objs").mkdir(parents=True)
    (project / "isaac-mcp.json").write_text(
        json.dumps({"asset_path": ["assets"]}), encoding="utf-8"
    )
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(project))
    module = load_module()
    isolated(module, tmp_path)
    assert module.resolve_dir("objs") == project.resolve() / "assets" / "ur5e-dt" / "objs"


def test_resolve_info_points_fetch_group_members_at_the_fetch_script(monkeypatch, tmp_path):
    module = load_module()
    fake_manifest(module, tmp_path, target="robot/test.usd")
    data = json.loads(module.MANIFEST_PATH.read_text(encoding="utf-8"))
    data["remote"] = {}
    module.MANIFEST_PATH.write_text(json.dumps(data), encoding="utf-8")
    isolated(module, tmp_path)
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(tmp_path / "empty"))
    info = module.resolve_info("robot/test.usd")
    assert info["status"] == "missing"
    assert "fetch_ur5e_assets.py" in info["detail"]
    assert "no public source" not in info["detail"]


# --- review regressions (PR 51) -------------------------------------------------


def test_nested_stores_use_the_pin_file_of_the_folder_that_holds_the_asset(monkeypatch, tmp_path):
    outer = tmp_path / "A"
    inner = outer / "nested"
    pack = inner / "ur5e-dt"
    put(pack / "custom.usd", b"original")
    sums(pack, **{"custom.usd": b"original"})
    put(pack / "custom.usd", b"edited after pinning")
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", f"{outer}{os.pathsep}{inner}")
    module = load_module()
    isolated(module, tmp_path)
    assert module.resolve_info("custom.usd")["status"] == "corrupt"
    with pytest.raises(module.AssetCorrupt):
        module.resolve_path("custom.usd")


def grouped_manifest(module, tmp_path, files: dict[str, tuple[bytes, str | None]]):
    """Write a manifest with one fetch group: target -> (content, cache_path)."""
    entries = []
    for target, (content, cache_path) in files.items():
        entry = {
            "target": target,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
        if cache_path:
            entry["cache_path"] = cache_path
        entries.append(entry)
    path = tmp_path / "assets-manifest.json"
    path.write_text(
        json.dumps(
            {"version": 1, "asset_root": "https://example.invalid", "remote": {}, "fetch": [{"id": "g", "files": entries}]}
        ),
        encoding="utf-8",
    )
    module.MANIFEST_PATH = path


def test_manifest_asset_at_its_logical_name_is_rejected_unless_pinned(monkeypatch, tmp_path):
    module = load_module()
    grouped_manifest(
        module,
        tmp_path,
        {
            "sensors/test.usda": (b"root", "sensors/rs/test.usda"),
            "Isaac/dep.usda": (b"dep", None),
        },
    )
    isolated(module, tmp_path)
    store = tmp_path / "store"
    put(store / "Isaac" / "dep.usda", b"dep")
    put(store / "sensors" / "test.usda", b"root")  # wrong place: relative refs would break
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    with pytest.raises(module.AssetMissing):
        module.resolve_path("sensors/test.usda")
    # The canonical layout resolves.
    put(store / "sensors" / "rs" / "test.usda", b"root")
    assert module.resolve_path("sensors/test.usda") == store.resolve() / "sensors" / "rs" / "test.usda"


def test_user_pinned_logical_name_is_accepted_for_manifest_assets(monkeypatch, tmp_path):
    module = load_module()
    grouped_manifest(module, tmp_path, {"sensors/test.usda": (b"root", "sensors/rs/test.usda")})
    isolated(module, tmp_path)
    store = tmp_path / "store"
    put(store / "ur5e-dt" / "sensors" / "test.usda", b"mine")
    sums(store / "ur5e-dt", **{"sensors/test.usda": b"mine"})
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    assert module.resolve_info("sensors/test.usda")["status"] == "pinned"


def test_a_pinned_group_member_does_not_condemn_the_root(monkeypatch, tmp_path):
    module = load_module()
    grouped_manifest(
        module,
        tmp_path,
        {"robot/root.usd": (b"root", None), "robot/configuration/sub.usd": (b"nvidia sub", None)},
    )
    isolated(module, tmp_path)
    store = tmp_path / "store"
    put(store / "ur5e-dt" / "robot" / "root.usd", b"root")
    put(store / "ur5e-dt" / "robot" / "configuration" / "sub.usd", b"lab-modified sub")
    sums(store / "ur5e-dt", **{"robot/configuration/sub.usd": b"lab-modified sub"})
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    assert module.resolve_path("robot/root.usd").name == "root.usd"
    assert module.resolve_info("robot/configuration/sub.usd")["status"] == "pinned"
    # A stale pin on the member is still caught when the root is resolved.
    put(store / "ur5e-dt" / "robot" / "configuration" / "sub.usd", b"changed again")
    with pytest.raises(module.AssetCorrupt, match="SHA256SUMS"):
        module.resolve_path("robot/root.usd")


def test_pin_lines_keep_unusual_but_valid_names(tmp_path):
    module = load_module()
    d = hashlib.sha256(b"x").hexdigest()
    (tmp_path / "SHA256SUMS").write_text(
        f"{d}  *star.usd\n{d}   spaced.usd\n{d} *binary.usd\n{d}  plain.usd\r\n",
        encoding="utf-8",
    )
    assert module.pins(tmp_path) == {
        "*star.usd": d,
        " spaced.usd": d,
        "binary.usd": d,
        "plain.usd": d,
    }

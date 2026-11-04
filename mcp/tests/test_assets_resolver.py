"""The shared resolver: named packs, selection, the twin's fallback and the supported import."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from isaac_mcp import assets

REPO = Path(__file__).resolve().parents[2]
TWIN = REPO / "exts" / "ur5e-dt" / "ur5e_dt" / "asset_cache.py"
ENV = ("ISAAC_MCP_ASSET_PATH", "ISAAC_MCP_ASSET_PACK", "ISAAC_MCP_ASSET_STRICT", "ISAAC_MCP_ASSET_REMOTE_ROOT",
       "ISAAC_MCP_PROJECT", "ISAAC_MCP_WORKSPACE", "UR5E_DT_ASSET_CACHE", "UR5E_DT_ASSET_ROOT", "UR5E_DT_OFFLINE")


@pytest.fixture
def store(tmp_path, monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "home" / ".cache"))
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(tmp_path / "no-project"))
    folder = tmp_path / "isaac-mcp-assets"
    folder.mkdir()
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(folder))
    return folder


def put(root: Path, rel: str, data: bytes = b"x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def twin():
    """The twin's module exactly as an existing caller loads it."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("twin_asset_cache_test", TWIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project(tmp_path, monkeypatch, **doc) -> Path:
    folder = tmp_path / "project"
    folder.mkdir(exist_ok=True)
    (folder / "isaac-mcp.json").write_text(json.dumps(doc))
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(folder))
    return folder


def test_default_pack_needs_no_setting(store):
    assert assets.selected_pack_info() == ("ur5e-dt", "default")
    path = put(store / "ur5e-dt", "robot/lab.usd")
    assert assets.resolve_path("robot/lab.usd") == path.resolve()


def test_project_file_selects_a_pack(store, tmp_path, monkeypatch):
    project(tmp_path, monkeypatch, pack="bricksim")
    assert assets.selected_pack_info() == ("bricksim", "project")
    path = put(store / "bricksim", "scene.usd")
    assert assets.resolve_path("scene.usd") == path.resolve()


def test_environment_selects_a_pack_and_beats_the_project(store, tmp_path, monkeypatch):
    project(tmp_path, monkeypatch, pack="bricksim")
    monkeypatch.setenv("ISAAC_MCP_ASSET_PACK", "other")
    assert assets.selected_pack_info() == ("other", "env")


def test_explicit_pack_beats_selection(store, monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_ASSET_PACK", "other")
    path = put(store / "named", "a.usd")
    assert assets.resolve_path("a.usd", pack="named") == path.resolve()


BAD_NAMES = ["../escape", "..", ".", "a/b", "a\\b", "/abs", ".hidden", "a b", "a..b", "SHA256SUMS", "x" * 65, "a\n"]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_invalid_pack_names_are_errors_from_every_source(store, tmp_path, monkeypatch, name):
    with pytest.raises(assets.InvalidPackName):
        assets.Resolver(name)
    project(tmp_path, monkeypatch, pack=name)
    with pytest.raises(assets.InvalidPackName):
        assets.selected_pack()
    with pytest.raises(assets.InvalidPackName):
        assets.resolve_uri("a.usd")
    with pytest.raises(assets.InvalidPackName):
        twin().resolve_uri("a.usd")


def test_a_non_string_project_pack_is_an_error(store, tmp_path, monkeypatch):
    project(tmp_path, monkeypatch, pack=["bricksim"])
    with pytest.raises(assets.InvalidPackName):
        assets.selected_pack()


def test_invalid_environment_pack_is_an_error(store, monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_ASSET_PACK", "../../etc")
    with pytest.raises(assets.InvalidPackName, match="ISAAC_MCP_ASSET_PACK"):
        assets.selected_pack()


def test_a_named_pack_holds_only_its_own_files(store, monkeypatch):
    """No borrowing from the twin's pack, no flat layout and no NVIDIA fallback."""
    put(store / "ur5e-dt", "robot/only-in-twin.usd")
    put(store, "flat.usd")
    resolver = assets.Resolver("bricksim")
    for name in ("robot/only-in-twin.usd", "flat.usd", "robot/ur5e.usd"):
        with pytest.raises(assets.AssetMissing, match="--pack bricksim"):
            resolver.resolve_uri(name)
    assert resolver.remote_uri("robot/ur5e.usd") is None
    assert resolver.asset_source("robot/ur5e.usd") == {"asset": "robot/ur5e.usd", "source": "missing", "location": None}


def test_the_default_pack_keeps_the_flat_layout_and_remote_fallback(store):
    path = put(store, "flat.usd")
    assert assets.resolve_path("flat.usd") == path.resolve()
    assert assets.resolve_uri("robot/ur5e.usd").startswith("https://")


def test_pinned_files_are_verified_in_a_named_pack(store):
    path = put(store / "bricksim", "a.usd", b"good")
    import hashlib

    (store / "bricksim" / "SHA256SUMS").write_text(f"{hashlib.sha256(b'good').hexdigest()}  a.usd\n")
    assert assets.resolve_info("a.usd", pack="bricksim")["status"] == "pinned"
    path.write_bytes(b"changed")
    with pytest.raises(assets.AssetCorrupt, match="pin --pack bricksim"):
        assets.resolve_uri("a.usd", pack="bricksim")


def test_an_unpinned_file_in_a_named_pack_is_reported_as_unpinned(store):
    put(store / "bricksim", "robot/ur5e.usd")
    info = assets.resolve_info("robot/ur5e.usd", pack="bricksim")
    assert info["status"] == "unpinned" and info["pack"] == "bricksim"


def test_the_twin_finds_its_files_when_a_project_selects_another_pack(store, tmp_path, monkeypatch):
    project(tmp_path, monkeypatch, pack="bricksim")
    own = put(store / "ur5e-dt", "gripper/QuickChanger.usd")
    module = twin()
    assert module.resolve_path("gripper/QuickChanger.usd") == own.resolve()
    assert module.asset_source("gripper/QuickChanger.usd") == {
        "asset": "gripper/QuickChanger.usd", "source": "store", "location": str(own.resolve())}
    assert module.resolve_uri("sensors/rsd455.usd").startswith("https://")
    assert module.PACK == "ur5e-dt"


def test_a_selected_pack_overrides_the_twin_by_name_and_says_so(store, tmp_path, monkeypatch):
    project(tmp_path, monkeypatch, pack="bricksim")
    put(store / "ur5e-dt", "workstation/x.usd", b"twin")
    lab = put(store / "bricksim", "workstation/x.usd", b"lab")
    source = twin().asset_source("workstation/x.usd")
    assert source == {"asset": "workstation/x.usd", "source": "store", "location": str(lab.resolve()), "pack": "bricksim"}


def test_the_twin_refuses_an_unpinned_changed_robot_in_a_selected_pack(store, tmp_path, monkeypatch):
    """A selected pack does not weaken the rule that a changed tracked file needs a pin."""
    project(tmp_path, monkeypatch, pack="bricksim")
    put(store / "bricksim", "robot/ur5e.usd", b"edited")
    module = twin()
    with pytest.raises((module.AssetCorrupt, module.AssetMissing)):
        module.resolve_uri("robot/ur5e.usd")


def test_both_entry_points_share_one_implementation(store):
    module = twin()
    assert module.AssetMissing is assets.AssetMissing and module.AssetCorrupt is assets.AssetCorrupt
    assert issubclass(type(module._TWIN), assets.Resolver)


def test_both_entry_points_agree_on_the_default_store(store, monkeypatch):
    monkeypatch.delenv("ISAAC_MCP_ASSET_PATH")
    assert twin().default_store() == assets.default_store() == (REPO.parent / "isaac-mcp-assets").resolve()
    monkeypatch.setenv("ISAAC_MCP_WORKSPACE", str(store.parent))
    assert twin().default_store() == assets.default_store() == store.resolve()


def test_installed_without_a_checkout(store, monkeypatch):
    monkeypatch.delenv("ISAAC_MCP_ASSET_PATH")
    monkeypatch.setattr(assets, "checkout_root", lambda: None)
    assert assets.default_store() == (Path(os.environ["HOME"]) / "isaac-mcp-assets").resolve()
    resolver = assets.Resolver()
    assert resolver.manifest() == {} and resolver.remote_uri("robot/ur5e.usd") is None


def test_run_path_keeps_working_without_isaac_mcp_on_the_path(store):
    """BrickSim loads the twin's file this way."""
    code = (
        "import runpy, sys\n"
        f"resolve = runpy.run_path({str(TWIN)!r})['resolve_uri']\n"
        "print(resolve('workstation/a.usd'))\n"
    )
    put(store / "ur5e-dt", "workstation/a.usd")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=store)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == (store / "ur5e-dt" / "workstation" / "a.usd").resolve().as_uri()


def test_the_module_is_standard_library_only():
    for name in ("assets.py", "asset_packs.py", "usd_closure.py"):
        tree = ast.parse((REPO / "mcp" / "isaac_mcp" / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module]
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                assert root in sys.stdlib_module_names or root == "pxr", f"{name} imports {module}"


def test_importing_assets_pulls_in_no_server_or_sdk():
    code = "import sys; from isaac_mcp import assets; assert 'mcp' not in sys.modules and 'isaac_mcp.server' not in sys.modules"
    env = {**os.environ, "PYTHONPATH": str(REPO / "mcp")}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr

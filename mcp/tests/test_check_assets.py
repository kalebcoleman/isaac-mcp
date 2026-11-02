"""The redistribution guard must reject tracked exclusions in every path class."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def asset_guard(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "scripts/check_assets.py"
    spec = importlib.util.spec_from_file_location("asset_guard", script)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assets = tmp_path / "exts/ur5e-dt/assets"
    assets.mkdir(parents=True)
    manifest = assets.parent / "assets-manifest.json"
    manifest.write_text(json.dumps({
        "fetch": [{"files": [{"target": "custom/fetched.usd"}]}],
        "manual": [{"target": "custom/manual.usd"}],
        "bundled": [],
    }))
    for name in ("THIRD-PARTY-NOTICES.md", "docs/provenance/husarion-ur-onrobot-rg2-LICENSE"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Test notice\n")
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(guard, "ASSETS", assets)
    monkeypatch.setattr(guard, "MANIFEST", manifest)
    return guard


def add_asset(guard, relative, *, tracked=True):
    path = guard.ASSETS / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Synthetic test fixture\n")
    if tracked:
        subprocess.run(["git", "add", "--", str(path)], cwd=guard.REPO, check=True)
    return path.relative_to(guard.REPO).as_posix()


@pytest.mark.parametrize("relative", [
    "gripper/RG2.usd",
    "gripper/husarion_rg2/husarion_rg2_fixed.usd",
    "custom/fetched.usd",
    "custom/manual.usd",
    "robot/fixture.usd",
])
def test_tracked_exclusion_is_rejected(asset_guard, capsys, relative):
    path = add_asset(asset_guard, relative)
    assert asset_guard.main() == 1
    assert f"non-redistributable asset is tracked: {path}" in capsys.readouterr().out


def test_allowed_tracked_gripper_and_untracked_local_asset_pass(asset_guard):
    add_asset(asset_guard, "gripper/allowed-fixture.usd")
    add_asset(asset_guard, "gripper/RG2.usd", tracked=False)
    assert asset_guard.main() == 0

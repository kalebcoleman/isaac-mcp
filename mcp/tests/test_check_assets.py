"""The repository must stay code-only: no assets tracked anywhere."""

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
    manifest = tmp_path / "exts/ur5e-dt/assets-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "fetch": [{"files": [{"target": "robot/x.usd", "source": "Isaac/x.usd", "sha256": "0" * 64}]}],
        "manual": [{"target": "workstation/vention.usd"}],
        "local": [{"path": "gripper", "licence": "Apache-2.0"}],
    }))
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(guard, "MANIFEST", manifest)
    return guard


def track(guard, relative):
    path = guard.REPO / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Synthetic test fixture\n")
    subprocess.run(["git", "add", "--", str(path)], cwd=guard.REPO, check=True)
    return relative


def test_a_clean_code_only_repository_passes(asset_guard, capsys):
    track(asset_guard, "scripts/tool.py")
    track(asset_guard, "docs/media/screenshot.png")
    track(asset_guard, "exts/ur5e-dt/data/icon.png")
    assert asset_guard.main() == 0
    assert "code-only" in capsys.readouterr().out


@pytest.mark.parametrize("relative", [
    "exts/ur5e-dt/assets/gripper/QuickChanger.usd",
    "exts/ur5e-dt/assets/fmb_assembly1.json",
    "exts/ur5e-dt/assets/README.md",
])
def test_anything_in_the_old_asset_folder_is_rejected(asset_guard, capsys, relative):
    track(asset_guard, relative)
    assert asset_guard.main() == 1
    assert relative in capsys.readouterr().out


@pytest.mark.parametrize("relative", [
    "somewhere/robot.usd", "x/scene.usda", "y/part.stl", "z/arm.urdf", "m/mat.mdl", "w/model.glb",
])
def test_asset_files_anywhere_are_rejected(asset_guard, capsys, relative):
    track(asset_guard, relative)
    assert asset_guard.main() == 1
    assert f"asset file tracked in the repository: {relative}" in capsys.readouterr().out


def test_images_outside_docs_and_extension_data_are_rejected(asset_guard, capsys):
    track(asset_guard, "textures/wood.png")
    assert asset_guard.main() == 1
    assert "image tracked outside docs/" in capsys.readouterr().out


def test_untracked_local_files_are_ignored(asset_guard):
    path = asset_guard.REPO / "exts/ur5e-dt/assets/robot/ur5e.usd"
    path.parent.mkdir(parents=True)
    path.write_text("local only\n")
    assert asset_guard.main() == 0


def test_malformed_manifest_entries_are_reported(asset_guard, capsys):
    data = json.loads(asset_guard.MANIFEST.read_text())
    data["fetch"][0]["files"][0].pop("sha256")
    data["local"].append({"path": "objects"})
    asset_guard.MANIFEST.write_text(json.dumps(data))
    assert asset_guard.main() == 1
    out = capsys.readouterr().out
    assert "without sha256" in out and "needs path and licence" in out

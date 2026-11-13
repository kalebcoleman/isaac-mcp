"""The repository must stay code-only: no assets tracked anywhere."""

import importlib.util
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
    monkeypatch.setattr(guard, "REPO", tmp_path)
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
    track(asset_guard, "exts/workcell/data/icon.png")
    assert asset_guard.main() == 0
    assert "code-only" in capsys.readouterr().out


@pytest.mark.parametrize("relative", [
    "exts/workcell/assets/gripper/QuickChanger.usd",
    "exts/workcell/assets/fmb_assembly1.json",
    "exts/workcell/assets/README.md",
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
    path = asset_guard.REPO / "exts/workcell/assets/robot/ur5e.usd"
    path.parent.mkdir(parents=True)
    path.write_text("local only\n")
    assert asset_guard.main() == 0


def test_the_repository_ships_no_assets():
    repo = Path(__file__).resolve().parents[2]
    tracked = subprocess.run(["git", "ls-files", "exts/workcell/assets"], cwd=repo, capture_output=True, text=True,
                             check=True).stdout.split()
    assert tracked == []

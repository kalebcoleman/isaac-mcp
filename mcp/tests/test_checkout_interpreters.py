"""Exercise interpreter selection without starting Isaac Sim."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture
def checkout(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    root = tmp_path / "checkout"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(repo / "run.sh", root / "run.sh")
    shutil.copy(repo / "scripts" / "ros_libs.sh", scripts / "ros_libs.sh")
    # An old workspace interpreter must not be selected implicitly.
    legacy = root / "learning6" / ".venv" / "bin" / "python"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(f'#!/bin/sh\ntouch "{root / "legacy-ran"}"\n')
    legacy.chmod(0o755)
    return root


@pytest.fixture
def clean_env():
    env = os.environ.copy()
    env.pop("TWINSMITH_SIM_PYTHON", None)
    return env


def test_launcher_help_selects_checkout_or_explicit_interpreter(checkout, clean_env):
    def help_text(env):
        return subprocess.run(["bash", str(checkout / "run.sh"), "--help"], env=env,
                              text=True, capture_output=True, check=True).stdout

    assert f"Interpreter: {checkout}/.venv/bin/python" in help_text(clean_env)
    assert "Interpreter: /custom/python" in help_text(clean_env | {"TWINSMITH_SIM_PYTHON": "/custom/python"})
    assert not (checkout / "legacy-ran").exists()


def test_launch_and_helper_code_has_no_legacy_workspace_name():
    repo = Path(__file__).resolve().parents[2]
    paths = [repo / "run.sh", repo / "pyproject.toml"]
    paths += [p for p in (repo / "scripts").rglob("*") if p.suffix in {".sh", ".py"}]
    assert not [str(p.relative_to(repo)) for p in paths if "learning6" in p.read_text(encoding="utf-8")]

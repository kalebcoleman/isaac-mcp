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
    for name in ("run.sh", "verify.sh"):
        shutil.copy(repo / name, root / name)
    shutil.copy(repo / "scripts" / "probe_62.sh", scripts / "probe_62.sh")
    shutil.copy(repo / "scripts" / "isaac_ros_libs.sh", scripts / "isaac_ros_libs.sh")
    # An old workspace interpreter must not be selected implicitly.
    legacy = root / "learning6" / ".venv" / "bin" / "python"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(f'#!/bin/sh\ntouch "{root / "legacy-ran"}"\n')
    legacy.chmod(0o755)
    return root


@pytest.fixture
def clean_env(tmp_path):
    env = os.environ.copy()
    env.pop("UR5E_DT_PYTHON", None)
    env["PROBE_OUT"] = str(tmp_path / "probe")
    return env


def test_launcher_help_selects_checkout_or_explicit_interpreter(checkout, clean_env):
    def help_text(env):
        return subprocess.run(["bash", str(checkout / "run.sh"), "--help"], env=env,
                              text=True, capture_output=True, check=True).stdout

    assert f"Interpreter: {checkout}/.venv/bin/python" in help_text(clean_env)
    assert "Interpreter: /custom/python" in help_text(clean_env | {"UR5E_DT_PYTHON": "/custom/python"})
    assert not (checkout / "legacy-ran").exists()


@pytest.mark.parametrize("script", ["verify.sh", "scripts/probe_62.sh"])
def test_missing_checkout_interpreter_does_not_use_legacy_directory(checkout, clean_env, script):
    result = subprocess.run(["bash", str(checkout / script)], env=clean_env, text=True, capture_output=True)
    assert result.returncode != 0
    assert str(checkout / ".venv") in result.stderr
    assert not (checkout / "legacy-ran").exists()


@pytest.mark.parametrize("explicit", [False, True])
def test_verifier_uses_checkout_or_explicit_interpreter(checkout, clean_env, explicit):
    python = checkout / ("custom/python" if explicit else ".venv/bin/python")
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\nprintf "6.2.0.0\\n"\n')
    python.chmod(0o755)
    verifier = checkout / "scripts" / "verify_ur5e_dt.sh"
    verifier.write_text('#!/bin/sh\nprintf "selected=%s\\n" "$UR5E_DT_PYTHON"\n')
    verifier.chmod(0o755)
    if explicit:
        clean_env["UR5E_DT_PYTHON"] = str(python)
    result = subprocess.run(["bash", str(checkout / "verify.sh")], env=clean_env,
                            text=True, capture_output=True, check=True)
    assert f"selected={python}" in result.stdout
    assert not (checkout / "legacy-ran").exists()


def test_launch_and_helper_code_has_no_legacy_workspace_name():
    repo = Path(__file__).resolve().parents[2]
    paths = [repo / "run.sh", repo / "verify.sh", repo / "pyproject.toml"]
    paths += [p for p in (repo / "scripts").rglob("*") if p.suffix in {".sh", ".py"}]
    assert not [str(p.relative_to(repo)) for p in paths if "learning6" in p.read_text(encoding="utf-8")]

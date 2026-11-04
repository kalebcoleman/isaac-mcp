"""Behavior of the setup helpers the documentation promises, without a simulator."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_smoke_plan_has_no_stage_changing_tool_and_pins_a_closed_port(monkeypatch):
    smoke = _module("smoke_mcp_surface")
    assert not {name for name, _ in smoke.OFFLINE_PLAN} & {"ur5e_dt__new_stage", "ur5e_dt__quick_start"}
    seen = {}

    def fake_run(plan):
        seen["port"] = os.environ.get("UR5E_DT_PORT")

        async def nothing():
            return []
        return nothing()

    monkeypatch.setattr(smoke, "run", fake_run)
    monkeypatch.setenv("UR5E_DT_PORT", "8766")
    assert smoke.main(["smoke", "offline"]) == 0
    assert seen["port"] == smoke.OFFLINE_PORT


def _kit_config(home, app, version):
    path = home / ".local/share/ov/data/Kit" / app / version / "user.config.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    return path


def _run_install_py(home, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "install.py"), *args],
                          env=os.environ | {"HOME": str(home)}, capture_output=True, text=True)


def test_kit_registration_refuses_to_guess_between_configs(tmp_path):
    old = _kit_config(tmp_path, "Isaac-Sim Full", "5.1")
    new = _kit_config(tmp_path, "Isaac-Sim Full", "6.2")
    result = _run_install_py(tmp_path, "status")
    assert result.returncode != 0
    assert "pass the one you mean with --config" in result.stderr
    assert str(old) in result.stderr and str(new) in result.stderr
    chosen = _run_install_py(tmp_path, "status", "--config", str(new))
    assert chosen.returncode == 0 and str(new) in chosen.stdout


def test_kit_registration_uses_a_lone_config(tmp_path):
    only = _kit_config(tmp_path, "Isaac-Sim Full", "6.2")
    result = _run_install_py(tmp_path, "status")
    assert result.returncode == 0 and str(only) in result.stdout


@pytest.mark.parametrize("flag", ["--help"])
def test_run_help_does_not_present_a_missing_interpreter_as_chosen(tmp_path, flag):
    missing = tmp_path / "no-such-python"
    env = os.environ | {"ISAAC_MCP_PYTHON": str(missing)}
    result = subprocess.run(["bash", str(ROOT / "run.sh"), flag], env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert f"Interpreter: {missing} (not found;" in result.stdout


def test_install_help_describes_the_project_not_one_robot():
    result = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "UR5e digital twin repository" not in result.stdout
    assert "Never download or sync Isaac Sim" in result.stdout

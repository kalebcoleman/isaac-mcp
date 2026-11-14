"""The names this project had before 0.3.0 (isaac-mcp) are no longer read. 0.3 honoured them for one release.

What stays: a server or a bridge that finds an old setting refuses to start instead of ignoring it, an old config
file is refused the same way, ISAAC_MCP_ASSET_ROOT is a permanent alias of TWINSMITH_ASSET_ROOT, and the installer's
--remove still cleans up the old bridge registration. docs/MIGRATING.md is the user's page for all of it.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bridge_testing import make_bridge
from twinsmith import config, pack_manifest, server
from twinsmith_bridge import knobs

ROOT = Path(__file__).resolve().parents[2]
OLD = {"ISAAC_MCP_PORT": "8799", "UR5E_DT_PYTHON": "/x/python", "ISAAC_SIM_PORT": "1", "ISAACLAB_JOBS_DIR": "/j"}


def test_old_variables_stop_the_server_and_are_named():
    with pytest.raises(ValueError) as error:
        server.build_server(dict(OLD))
    for name in OLD:
        assert name in str(error.value)
    assert "TWINSMITH_" in str(error.value) and "MIGRATING" in str(error.value)


def test_the_asset_root_alias_empty_values_and_new_names_do_not_stop_it():
    env = {"ISAAC_MCP_ASSET_ROOT": "/assets", "ISAAC_MCP_PORT": "", "TWINSMITH_PORT": "8766",
           "ISAACLAB_PYTHON": "/lab/python", "ISAACSIM_PATH": "/sim"}
    assert config.old_settings(env) == []
    config.resolve(env, cwd=ROOT, home=ROOT / "nohome")


def test_the_host_and_the_bridge_apply_the_same_rule():
    sample = {**OLD, "ISAAC_MCP_ASSET_ROOT": "/a", "TWINSMITH_PORT": "1", "ISAACLAB_ROOT": "/l", "UR5E_DT_X": ""}
    assert knobs.old_settings(sample) == config.old_settings(sample) == sorted(OLD)
    assert (knobs.OLD_PREFIXES, knobs.OLD_VARIABLES, knobs.ASSET_ROOT_ALIAS) == \
        (config.OLD_PREFIXES, config.OLD_VARIABLES, pack_manifest.ASSET_ROOT_ALIAS)


def test_the_bridge_does_not_listen_with_an_old_variable_set(monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_READ_ONLY", "1")
    bridge = make_bridge()
    with pytest.raises(RuntimeError, match="ISAAC_MCP_READ_ONLY"):
        bridge.start(0)
    assert bridge.listening_port is None


def test_the_launcher_helper_refuses_before_kit_starts(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "mcp"), "HOME": str(tmp_path), "UR5E_DT_PORT": "8766"}
    result = subprocess.run([sys.executable, "-m", "twinsmith.config", "--print", "physics_backend"],
                            capture_output=True, text=True, env=env, cwd=tmp_path, timeout=30)
    assert result.returncode == 2 and result.stdout == "" and "UR5E_DT_PORT" in result.stderr


def test_an_old_user_config_is_refused_until_it_is_moved(tmp_path):
    home = tmp_path / "home"
    (home / ".config/isaac-mcp").mkdir(parents=True)
    (home / ".config/isaac-mcp/config.toml").write_text('[sim]\nphysics_backend = "physx"\n')
    with pytest.raises(ValueError, match="mv .*config.toml"):
        config.resolve({}, cwd=tmp_path, home=home)
    (home / ".config/twinsmith").mkdir()
    (home / ".config/twinsmith/config.toml").write_text('[tools]\ntoolsets = ["default"]\n')
    assert config.resolve({}, cwd=tmp_path, home=home).toolsets == config.select("default")


def test_an_old_project_file_is_refused_where_it_would_have_been_found(tmp_path):
    project, inner = tmp_path / "project", tmp_path / "project" / "inner"
    inner.mkdir(parents=True)
    (project / "isaac-mcp.toml").write_text('[sim]\nphysics_backend = "physx"\n')
    with pytest.raises(ValueError, match="twinsmith.toml"):
        config.resolve_physics_backend({}, cwd=inner, home=tmp_path / "nohome")
    (inner / "twinsmith.toml").write_text('[sim]\nphysics_backend = "physx"\n')     # a nearer new file wins
    assert config.resolve_physics_backend({}, cwd=inner, home=tmp_path / "nohome") == ("physx", "project file")


def test_old_project_json_and_output_markers_are_not_read(tmp_path, monkeypatch):
    from twinsmith.isaaclab import jobs

    (tmp_path / "isaac-mcp.json").write_text(json.dumps({"pack": "p/v1"}))
    monkeypatch.setenv("TWINSMITH_PROJECT", str(tmp_path))
    assert pack_manifest.project_file() is None
    log = tmp_path / "job.log"
    log.write_text("ISAAC_MCP_ARTIFACT: /a\nTWINSMITH_ARTIFACT: /b\nISAAC_MCP_PROGRESS: 0.25\n")
    assert jobs._script_artifacts(log) == ["/b"] and jobs._progress(log) is None


def test_the_asset_root_alias_is_permanent(tmp_path):
    result = pack_manifest.resolve_asset_root({"ISAAC_MCP_ASSET_ROOT": str(tmp_path)})
    assert result.path == tmp_path.resolve() and result.source == "environment" and result.warnings == ()
    assert pack_manifest.resolve_asset_root({"TWINSMITH_ASSET_ROOT": str(tmp_path / "new"),
                                             "ISAAC_MCP_ASSET_ROOT": str(tmp_path)}).path == (tmp_path / "new").resolve()


def test_the_installer_still_removes_the_old_extension_id(tmp_path):
    config_file = tmp_path / "user.config.json"
    config_file.write_text(json.dumps({"persistent": {
        "app": {"exts": {"userFolders": {"0": str(ROOT / "exts")},
                         "enabled": {"0": "isaac.mcp.bridge-0.2.0", "1": "twinsmith.bridge-0.3.0", "2": "other.ext-1.0"}}},
        "exts": {"isaac.mcp.bridge": {"autostartPort": 8766}, "twinsmith.bridge": {"autostartPort": 8766}}}}))
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "install.py"), "--config", str(config_file), "--remove"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    saved = json.loads(config_file.read_text())["persistent"]
    assert list(saved["app"]["exts"]["enabled"].values()) == ["other.ext-1.0"]
    assert "isaac.mcp.bridge" not in saved.get("exts", {}) and "twinsmith.bridge" not in saved.get("exts", {})

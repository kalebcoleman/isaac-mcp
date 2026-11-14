"""The names this project had before 0.3.0 (isaac-mcp) are no longer read. 0.3 honoured them for one release.

What stays: a server or a bridge that finds an old setting refuses to start instead of ignoring it (the former asset-root
alias ISAAC_MCP_ASSET_ROOT included), an old config file is refused the same way, the old folders are not read, and the
installer's --remove still cleans up the old bridge registration. docs/MIGRATING.md is the user's page for all of it.
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


def test_the_refusal_names_each_replacement_not_a_prefix_rule():
    env = {"ISAAC_MCP_PYTHON": "/p", "ISAAC_MCP_NAMESPACE": "x", "ISAAC_MCP_ASSET_PATH": "/a", "UR5E_DT_HEADLESS": "1",
           "ISAAC_SIM_PORT": "1", "ISAAC_MCP_READ_ONLY": "1", "UR5E_DT_OFFLINE": "1", "UR5E_DT_UNKNOWN": "1"}
    with pytest.raises(ValueError) as error:
        config.refuse_old_settings(env)
    message = str(error.value)
    for expected in ("ISAAC_MCP_PYTHON -> TWINSMITH_SIM_PYTHON", "ISAAC_MCP_NAMESPACE -> TWINSMITH_TOOLSETS",
                     "ISAAC_MCP_ASSET_PATH -> TWINSMITH_ASSET_ROOT", "UR5E_DT_HEADLESS -> TWINSMITH_HEADLESS",
                     "ISAAC_SIM_PORT -> TWINSMITH_PORT", "ISAAC_MCP_READ_ONLY -> TWINSMITH_READ_ONLY",
                     "UR5E_DT_OFFLINE -> nothing: remove it", "UR5E_DT_UNKNOWN -> nothing: remove it"):
        assert expected in message
    assert "TWINSMITH_PYTHON " not in message and "TWINSMITH_NAMESPACE" not in message


def test_every_named_replacement_is_a_setting_the_code_reads():
    tables = {ROOT / "mcp/twinsmith/config.py", ROOT / "exts/twinsmith.bridge/twinsmith_bridge/knobs.py"}
    sources = "".join(path.read_text() for root in ("mcp/twinsmith", "exts", "scripts", "mcp/templates")
                      for path in (ROOT / root).rglob("*.py") if path not in tables) + (ROOT / "run.sh").read_text()
    for name in config.OLD_REPLACEMENTS:
        new = config.replacement(name).split()[0]
        assert new == "nothing:" or new in sources, (name, new)


def test_empty_values_and_new_names_do_not_stop_it():
    env = {"ISAAC_MCP_PORT": "", "TWINSMITH_PORT": "8766", "ISAACLAB_PYTHON": "/lab/python", "ISAACSIM_PATH": "/sim"}
    assert config.old_settings(env) == []
    config.resolve(env, cwd=ROOT, home=ROOT / "nohome")


def test_the_former_asset_root_alias_is_refused_and_not_read(tmp_path):
    with pytest.raises(ValueError, match="ISAAC_MCP_ASSET_ROOT -> TWINSMITH_ASSET_ROOT"):
        config.resolve({"ISAAC_MCP_ASSET_ROOT": "/assets"}, cwd=ROOT, home=ROOT / "nohome")
    (tmp_path / "ws").mkdir()
    with pytest.raises(pack_manifest.RootError):
        pack_manifest.resolve_asset_root({"ISAAC_MCP_ASSET_ROOT": str(tmp_path)}, workspace=tmp_path / "ws")


def test_the_host_and_the_bridge_apply_the_same_rule():
    sample = {**OLD, "ISAAC_MCP_ASSET_ROOT": "/a", "TWINSMITH_PORT": "1", "ISAACLAB_ROOT": "/l", "UR5E_DT_X": ""}
    assert knobs.old_settings(sample) == config.old_settings(sample) == sorted([*OLD, "ISAAC_MCP_ASSET_ROOT"])
    assert (knobs.OLD_PREFIXES, knobs.OLD_VARIABLES, knobs.OLD_REPLACEMENTS) == \
        (config.OLD_PREFIXES, config.OLD_VARIABLES, config.OLD_REPLACEMENTS)
    for name in (*config.OLD_REPLACEMENTS, "ISAAC_MCP_TOKEN", "UR5E_DT_X"):
        assert knobs.replacement(name) == config.replacement(name)


def test_the_bridge_does_not_listen_with_an_old_variable_set(monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_READ_ONLY", "1")
    bridge = make_bridge()
    with pytest.raises(RuntimeError, match="ISAAC_MCP_READ_ONLY"):
        bridge.start(0)
    assert bridge.listening_port is None


def test_the_bridge_listens_with_new_names_only_and_refuses_the_former_alias(monkeypatch):
    for name in knobs.old_settings():
        monkeypatch.delenv(name)
    monkeypatch.setenv("TWINSMITH_ASSET_ROOT", "/assets")
    bridge = make_bridge()
    try:
        assert bridge.start(0) == bridge.listening_port > 0
    finally:
        bridge.stop()
    monkeypatch.setenv("ISAAC_MCP_ASSET_ROOT", "/assets")
    bridge = make_bridge()
    with pytest.raises(RuntimeError, match="ISAAC_MCP_ASSET_ROOT -> TWINSMITH_ASSET_ROOT"):
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
    (home / ".config/twinsmith/config.toml").write_text('[tools]\ntoolsets = ["minimal"]\n')
    assert config.resolve({}, cwd=tmp_path, home=home).toolsets == config.select("minimal")


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


def test_the_work_cell_no_longer_reads_the_former_alias(tmp_path):
    from test_pack_manifest import load_workcell_copy
    copy = load_workcell_copy(tmp_path / "copy")
    home = tmp_path / "home"
    assert copy.default_root({"ISAAC_MCP_ASSET_ROOT": str(tmp_path / "x")}, home) == home / "robotics-workspace" / "assets"


def test_assets_where_warns_about_an_old_variable(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TWINSMITH_ASSET_ROOT": str(tmp_path),
           "UR5E_DT_ASSET_CACHE": "/lab/assets"}
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "assets.py"), "where"], capture_output=True, text=True,
                            env=env, cwd=tmp_path, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "warning:" in result.stdout and "UR5E_DT_ASSET_CACHE -> TWINSMITH_ASSET_ROOT" in result.stdout


def test_the_old_asset_folder_is_not_read_and_the_error_says_where_to_move_it(tmp_path):
    (tmp_path / "ws" / "isaac-mcp-assets").mkdir(parents=True)
    with pytest.raises(pack_manifest.RootError) as error:
        pack_manifest.resolve_asset_root({}, workspace=tmp_path / "ws")
    assert f"move it to {tmp_path / 'ws' / 'assets'}" in str(error.value)


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


def test_the_launcher_check_refuses_an_old_config_file(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".config/isaac-mcp").mkdir(parents=True)
    project.mkdir()
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "mcp"), "HOME": str(home),
           "TWINSMITH_PHYSICS_BACKEND": "physx"}     # set, so the physics value alone would not read the files

    def helper(*args):
        return subprocess.run([sys.executable, "-m", "twinsmith.config", *args], capture_output=True, text=True,
                              env=env, cwd=project, timeout=30)

    assert helper("--check").returncode == 0 and helper("--print", "physics_backend").stdout == "physx\n"
    (home / ".config/isaac-mcp/config.toml").write_text('[sim]\nphysics_backend = "physx"\n')
    for args in (("--check",), ("--print", "physics_backend")):
        result = helper(*args)
        assert result.returncode == 2 and result.stdout == "" and "isaac-mcp/config.toml" in result.stderr
    (home / ".config/isaac-mcp/config.toml").unlink()
    (project / "isaac-mcp.toml").write_text('[tools]\ntoolsets = ["minimal"]\n')
    result = helper("--check")
    assert result.returncode == 2 and "mv " in result.stderr and "twinsmith.toml" in result.stderr


def _run_sh(tmp_path, *args, env=None):
    """run.sh with a stub Isaac Sim interpreter that answers its probes and says when Kit would have started."""
    site = tmp_path / "app/python_packages/isaacsim"
    site.mkdir(parents=True, exist_ok=True)
    python = tmp_path / "python"
    python.write_text(f'#!/bin/sh\ncase "$2" in *dirname*) echo "{site}";; esac\n[ "$1" = "-c" ] && exit 0\n'
                      'echo KIT-STARTED; exit 0\n')
    python.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    environ = {"TWINSMITH_SIM_PYTHON": str(python), "PATH": "/usr/bin:/bin", "HOME": str(home), **(env or {})}
    return subprocess.run(["bash", str(ROOT / "run.sh"), "--headless", "--port", "0", *args], env=environ,
                          cwd=tmp_path, capture_output=True, text=True, timeout=60)


def test_run_sh_refuses_an_old_setting_even_with_a_physics_backend_flag(tmp_path):
    assert "KIT-STARTED" in _run_sh(tmp_path, "--physics-backend", "physx").stdout      # the stub does start
    for flags in ((), ("--physics-backend", "physx")):
        result = _run_sh(tmp_path, *flags, env={"ISAAC_MCP_READ_ONLY": "1"})
        assert result.returncode == 2 and "KIT-STARTED" not in result.stdout, result.stdout + result.stderr
        assert "ISAAC_MCP_READ_ONLY -> TWINSMITH_READ_ONLY" in result.stderr and "nothing was started" in result.stderr
        assert "physics_backend" not in result.stderr


def test_run_sh_refuses_an_old_config_file_even_with_a_physics_backend_flag(tmp_path):
    (tmp_path / "home/.config/isaac-mcp").mkdir(parents=True)
    (tmp_path / "home/.config/isaac-mcp/config.toml").write_text('[sim]\nphysics_backend = "physx"\n')
    result = _run_sh(tmp_path, "--physics-backend", "physx")
    assert result.returncode == 2 and "KIT-STARTED" not in result.stdout
    assert "isaac-mcp/config.toml" in result.stderr and "nothing was started" in result.stderr

"""The names this project had before 0.3.0 (isaac-mcp) keep working for one release, through one table.

The table is mcp/twinsmith/legacy.py. The host applies it when the package is imported, the Kit bridge when it
loads, and the shell launchers through scripts/legacy_env.sh. Old file names are read with a warning, old output
markers are understood, and the installer's --remove cleans up the old extension ids.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from twinsmith import config, legacy, pack_manifest
from twinsmith.isaaclab import jobs

ROOT = Path(__file__).resolve().parents[2]
CLEAN = {key: value for key, value in os.environ.items()
         if key not in legacy.OLD_NAMES and not key.startswith("TWINSMITH_")}


@pytest.fixture(autouse=True)
def fresh_warnings():
    legacy._WARNED.clear()


def run(args, env=None, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, env={**CLEAN, **(env or {})}, **kwargs)


# --------------------------------------------------------------------------------------------- the table

def test_every_old_name_maps_to_a_documented_variable():
    page = (ROOT / "docs" / "ENVIRONMENT.md").read_text(encoding="utf-8")
    for old, new in legacy.ALIASES:
        assert new.startswith("TWINSMITH_") and not old.startswith("TWINSMITH_")
        assert f"`{new}`" in page, f"{new} (the new name of {old}) is not in docs/ENVIRONMENT.md"
    assert len(legacy.OLD_NAMES) == len(legacy.ALIASES)


def test_only_the_asset_root_is_a_permanent_alias():
    assert legacy.PERMANENT == {"ISAAC_MCP_ASSET_ROOT"}
    assert dict(legacy.ALIASES)["ISAAC_MCP_ASSET_ROOT"] == "TWINSMITH_ASSET_ROOT"


def test_the_merged_names():
    merged = {}
    for old, new in legacy.ALIASES:
        merged.setdefault(new, []).append(old)
    assert merged["TWINSMITH_PORT"] == ["UR5E_DT_PORT", "ISAAC_MCP_PORT", "ISAAC_SIM_PORT"]
    assert merged["TWINSMITH_SIM_PYTHON"] == ["ISAAC_MCP_PYTHON", "UR5E_DT_PYTHON", "ISAAC_SIM_PYTHON"]
    assert dict(legacy.ALIASES)["UR5E_DT_HEADLESS"] == "TWINSMITH_HEADLESS"
    assert dict(legacy.ALIASES)["ISAACLAB_JOBS_DIR"] == "TWINSMITH_JOBS_DIR"
    assert dict(legacy.ALIASES)["ISAAC_MCP_EXTERNAL_CALLBACK"] == "TWINSMITH_EXTERNAL_CALLBACK"
    assert "ISAACLAB_EXTERNAL_CALLBACK" not in legacy.OLD_NAMES


# ------------------------------------------------------------------------------------ the startup pass

def test_an_old_name_is_copied_and_reported_once():
    environ, said = {"ISAAC_MCP_PORT": "8799"}, []
    assert legacy.apply(environ, said.append) == [("ISAAC_MCP_PORT", "TWINSMITH_PORT")]
    assert environ["TWINSMITH_PORT"] == "8799"
    assert len(said) == 1 and "TWINSMITH_PORT" in said[0] and "ISAAC_MCP_PORT" in said[0]
    legacy.apply({"ISAAC_MCP_PORT": "8799"}, said.append)
    assert len(said) == 1, "one warning per name per process"


def test_the_new_name_wins_and_an_ignored_old_name_says_so():
    environ, said = {"TWINSMITH_PORT": "1", "UR5E_DT_PORT": "2"}, []
    assert legacy.apply(environ, said.append) == []
    assert environ["TWINSMITH_PORT"] == "1"
    assert len(said) == 1 and "ignored" in said[0]


def test_merged_old_names_resolve_in_the_order_the_old_code_read_them():
    environ = {"ISAAC_SIM_PORT": "3", "ISAAC_MCP_PORT": "2", "UR5E_DT_PORT": "1",
               "ISAAC_SIM_PYTHON": "/c", "UR5E_DT_PYTHON": "/b", "ISAAC_MCP_PYTHON": "/a"}
    legacy.apply(environ, lambda message: None)
    assert environ["TWINSMITH_PORT"] == "1" and environ["TWINSMITH_SIM_PYTHON"] == "/a"


def test_the_asset_root_alias_never_warns():
    environ, said = {"ISAAC_MCP_ASSET_ROOT": "/assets"}, []
    legacy.apply(environ, said.append)
    assert environ["TWINSMITH_ASSET_ROOT"] == "/assets" and said == []


def test_importing_the_package_applies_the_table(tmp_path):
    code = "import os, twinsmith; print(os.environ['TWINSMITH_PORT'])"
    result = run([sys.executable, "-c", code], {"ISAAC_MCP_PORT": "8799", "PYTHONPATH": str(ROOT / "mcp")})
    assert result.stdout.strip() == "8799"
    assert result.stderr.count("TWINSMITH_PORT") == 1
    quiet = run([sys.executable, "-c", code], {"ISAAC_MCP_ASSET_ROOT": "/x", "TWINSMITH_PORT": "8798",
                                               "PYTHONPATH": str(ROOT / "mcp")})
    assert quiet.stderr == "" and quiet.stdout.strip() == "8798"


def test_the_shell_helper_applies_the_same_table():
    result = run(["bash", "-c", f'. "{ROOT}/scripts/legacy_env.sh"; echo "$TWINSMITH_PORT:$TWINSMITH_SIM_PYTHON"'],
                 {"ISAAC_MCP_PORT": "8799", "UR5E_DT_PYTHON": "/x/python"})
    assert result.stdout.strip() == "8799:/x/python"
    assert "TWINSMITH_PORT" in result.stderr and "TWINSMITH_SIM_PYTHON" in result.stderr
    permanent = run(["bash", "-c", f'. "{ROOT}/scripts/legacy_env.sh"; echo "$TWINSMITH_ASSET_ROOT"'],
                    {"ISAAC_MCP_ASSET_ROOT": "/assets"})
    assert permanent.stdout.strip() == "/assets" and permanent.stderr == ""


def test_the_bridge_applies_the_same_table():
    sys.path.insert(0, str(ROOT / "exts" / "twinsmith.bridge"))
    try:
        from twinsmith_bridge import knobs
    finally:
        sys.path.remove(str(ROOT / "exts" / "twinsmith.bridge"))
    environ = {"UR5E_DT_PORT": "8801"}
    module = knobs.legacy_module()
    assert module is not None and module.OLD_NAMES == legacy.OLD_NAMES
    module._WARNED.clear()
    module.apply(environ, lambda message: None)
    assert environ["TWINSMITH_PORT"] == "8801"


# -------------------------------------------------------------- removed settings still stop or announce

@pytest.mark.parametrize("name", sorted(legacy.STOPS_STARTUP))
def test_a_removed_selection_setting_still_stops_startup_and_names_the_replacement(name):
    with pytest.raises(ValueError) as error:
        config.resolve(env={name: "all"})
    assert name in str(error.value) and "TWINSMITH_TOOLSETS=" in str(error.value)


def test_removed_asset_settings_are_announced_with_their_replacement():
    for name in legacy.REMOVED_ASSET_SETTINGS:
        [sentence] = pack_manifest.removed_environment({name: "1"})
        assert sentence.startswith(f"{name} is no longer read.")
    assert "TWINSMITH_ASSET_ROOT" in pack_manifest.removed_environment({"ISAAC_MCP_ASSET_PATH": "/x"})[0]


# ------------------------------------------------------------------------------- old files and markers

def test_an_old_user_config_is_read_with_a_warning_that_names_the_new_file(tmp_path, capsys):
    home = tmp_path / "home"
    (home / legacy.OLD_CONFIG_DIR).mkdir(parents=True)
    (home / legacy.OLD_CONFIG_DIR / "config.toml").write_text('[tools]\ntoolsets = ["minimal", "+screen"]\n')
    resolved = config.resolve(env={}, cwd=tmp_path, home=home)
    assert resolved.toolsets == config.select("minimal,+screen")
    assert str(home / ".config/twinsmith/config.toml") in capsys.readouterr().err


def test_the_new_user_config_wins_silently(tmp_path, capsys):
    home = tmp_path / "home"
    for folder, text in ((legacy.OLD_CONFIG_DIR, '[tools]\ntoolsets = ["minimal"]\n'),
                         (".config/twinsmith", '[tools]\ntoolsets = ["minimal", "+lab"]\n')):
        (home / folder).mkdir(parents=True)
        (home / folder / "config.toml").write_text(text)
    assert "lab" in config.resolve(env={}, cwd=tmp_path, home=home).toolsets
    assert capsys.readouterr().err == ""


def test_an_old_project_toml_is_read_with_a_warning(tmp_path, capsys):
    (tmp_path / legacy.OLD_PROJECT_TOML).write_text('[sim]\nphysics_backend = "newton"\n')
    assert config.resolve(env={}, cwd=tmp_path, home=tmp_path / "nohome").physics_backend == "newton"
    assert "twinsmith.toml" in capsys.readouterr().err


def test_an_old_project_json_is_found_with_a_warning(tmp_path, monkeypatch, capsys):
    (tmp_path / legacy.OLD_PROJECT_JSON).write_text(json.dumps({"pack": "p/v1"}))
    monkeypatch.setenv("TWINSMITH_PROJECT", str(tmp_path))
    assert pack_manifest.project_file() == (tmp_path / legacy.OLD_PROJECT_JSON).resolve()
    assert "twinsmith.json" in capsys.readouterr().err


def test_the_asset_root_alias_is_honoured_where_the_root_is_resolved(tmp_path):
    result = pack_manifest.resolve_asset_root({"ISAAC_MCP_ASSET_ROOT": str(tmp_path)})
    assert result.path == tmp_path.resolve() and result.source == "environment" and result.warnings == ()


def test_the_old_output_markers_are_still_understood(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("ISAAC_MCP_ARTIFACT: /a\nTWINSMITH_ARTIFACT: /b\nISAAC_MCP_PROGRESS: 0.25 old\n")
    assert jobs._script_artifacts(log) == ["/a", "/b"]
    assert jobs._progress(log)["fraction"] == 0.25
    log.write_text("ISAAC_MCP_PROGRESS: 0.25\nTWINSMITH_PROGRESS: 0.75\n")
    assert jobs._progress(log)["fraction"] == 0.75


# ------------------------------------------------------------------------------------------ the installer

def test_the_installer_removes_the_old_extension_ids_too(tmp_path):
    exts = ROOT / "exts"
    config_file = tmp_path / "user.config.json"
    config_file.write_text(json.dumps({"persistent": {
        "app": {"exts": {"userFolders": {"0": str(exts)},
                         "enabled": {"0": "isaac.mcp.bridge-0.2.0", "1": "twinsmith.bridge-0.3.0", "2": "other.ext-1.0"}}},
        "exts": {"isaac.mcp.bridge": {"autostartPort": 8766}, "twinsmith.bridge": {"autostartPort": 8766}}}}))
    result = run([sys.executable, str(ROOT / "scripts" / "install.py"), "--config", str(config_file), "--remove"])
    assert result.returncode == 0, result.stderr
    saved = json.loads(config_file.read_text())["persistent"]
    assert list(saved["app"]["exts"]["enabled"].values()) == ["other.ext-1.0"]
    assert "isaac.mcp.bridge" not in saved.get("exts", {}) and "twinsmith.bridge" not in saved.get("exts", {})

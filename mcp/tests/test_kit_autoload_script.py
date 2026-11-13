"""scripts/install.py: registering the bridge in a Kit app's stored settings, and undoing it."""

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("kit_autoload_script", REPO / "scripts/install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "user.config.json"
    path.write_text(json.dumps({"persistent": {"app": {"exts": {"enabled": {"0": "other.ext-1.0.0", "1": "isaac.mcp.bridge-0.0.1"}}}}}))
    return path


def stored(path):
    return json.loads(path.read_text())["persistent"]


def test_install_registers_the_bridge_once_and_remove_undoes_exactly_that(script, config, capsys):
    script.main(["--config", str(config)])
    after = stored(config)
    enabled = list(after["app"]["exts"]["enabled"].values())
    assert enabled[0] == "other.ext-1.0.0" and len(enabled) == 2, "an older version of ours is replaced, others are kept"
    assert any(item.startswith("isaac.mcp.bridge-") and item != "isaac.mcp.bridge-0.0.1" for item in enabled)
    assert list(after["app"]["exts"]["userFolders"].values()) == [str(REPO / "exts")]
    assert after["exts"]["isaac.mcp.bridge"] == {"autostartPort": 8766}
    assert len(list(config.parent.glob("user.config.json.bak.*"))) == 1
    capsys.readouterr()
    script.main(["--config", str(config)])
    assert "Nothing to change." in capsys.readouterr().out
    script.main(["--config", str(config), "--remove"])
    after = stored(config)
    assert list(after["app"]["exts"]["enabled"].values()) == ["other.ext-1.0.0"]
    assert "userFolders" not in after["app"]["exts"] and "isaac.mcp.bridge" not in after["exts"]


def test_a_dry_run_writes_nothing(script, config, capsys):
    before = config.read_text()
    script.main(["--config", str(config), "--dry-run", "--bind-host", "0.0.0.0"])
    assert config.read_text() == before and "autostartHost = 0.0.0.0" in capsys.readouterr().out


def test_a_tool_package_is_added_and_removed(script, config, tmp_path, capsys):
    (tmp_path / "my_tools").mkdir()
    script.main(["add-project", "--config", str(config), "--path", str(tmp_path), "--module", "my_tools"])
    bridge = stored(config)["exts"]["isaac.mcp.bridge"]
    assert bridge == {"toolPaths": {"0": str(tmp_path)}, "toolModules": {"0": "my_tools"}}
    script.main(["status", "--config", str(config)])
    assert "tool modules: ['my_tools']" in capsys.readouterr().out
    script.main(["remove-project", "--config", str(config), "--path", str(tmp_path), "--module", "my_tools"])
    assert "isaac.mcp.bridge" not in stored(config)["exts"]
    with pytest.raises(SystemExit):
        script.main(["add-project", "--config", str(config), "--module", "my_tools"])

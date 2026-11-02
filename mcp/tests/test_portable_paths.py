"""Tests for scripts/check_portable_paths.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_portable_paths.py"


@pytest.fixture
def checker(tmp_path):
    spec = importlib.util.spec_from_file_location("portable_paths_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REPO = tmp_path
    return module


def write(root: Path, name: str, text: str) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


@pytest.mark.parametrize(
    "line",
    [
        'ROOT = "/home/someone/projects/isaac"',
        'ROOT = "/Users/someone/isaac"',
        'ROOT = r"C:\\isaac-sim"',
        'p = os.path.expanduser("~/Documents/isaac")',
        'PY="$HOME/envs/sim/bin/python"',
        "python ~/env_isaaclab/bin/python run.py",
    ],
)
def test_user_specific_paths_are_flagged(checker, tmp_path, line):
    name = write(tmp_path, "tool.py", line + "\n")
    assert checker.scan([name]), line


@pytest.mark.parametrize(
    "line",
    [
        'cache = Path.home() / ".cache" / "ur5e-dt"',
        'p = os.path.expanduser("~/.local/share/isaac-mcp/jobs")',
        'out = "~/Pictures/screen-mcp"',
        'WS_SETUP="${WS_SETUP:-$HOME/ros2_ws/install/setup.bash}"',
        'ROOT = os.path.join("<workspace>", "envs")',
        'x = "$HOME/$DIR/file"',
        'p = "/opt/ros/humble/setup.bash"',
        'PATH = "/usr/local/sbin:/usr/local/bin:/usr/bin"',
        'p = "/home/someone/x"  # portable-ok: test fixture',
    ],
)
def test_standard_locations_and_marked_exceptions_pass(checker, tmp_path, line):
    name = write(tmp_path, "tool.py", line + "\n")
    assert checker.scan([name]) == [], line


def test_the_repository_itself_is_clean():
    spec = importlib.util.spec_from_file_location("portable_paths_repo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.scan(module.tracked_files()) == []

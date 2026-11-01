"""The smoke check must not mistake one minor release for another."""

import importlib.util
from pathlib import Path

import pytest


_path = Path(__file__).resolve().parents[2] / "scripts/smoke_tools.py"
_spec = importlib.util.spec_from_file_location("smoke_tools", _path)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


@pytest.mark.parametrize(("actual", "expected", "matches"), [
    ("6.1.0", "6.1", True),
    ("6.1.0", "6.1.0", True),
    ("6.10.0", "6.1", False),
    ("6.2.0", "", False),
    (None, "6.2", False),
])
def test_version_components(actual, expected, matches):
    assert smoke.matches_version(actual, expected) is matches

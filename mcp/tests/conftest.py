"""Make the repo's python packages importable from a bare checkout."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# In the consolidated repo the Kit bridge lives beside mcp/, not under it.
_EXT_ROOT = os.path.join(os.path.dirname(_REPO_ROOT), "exts", "omni.kit.mcp")
for _p in (_REPO_ROOT, _EXT_ROOT):   # kit_mcp · omni_kit_mcp
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Tests that spawn `sys.executable -c ...` (e.g. the telemetry flush test) get
# the same import roots through the environment.
_pythonpath = os.environ.get("PYTHONPATH", "")
for _p in reversed((_REPO_ROOT, _EXT_ROOT)):
    if _p not in _pythonpath.split(os.pathsep):
        _pythonpath = _p + (os.pathsep + _pythonpath if _pythonpath else "")
os.environ["PYTHONPATH"] = _pythonpath


import pytest


@pytest.fixture(autouse=True)
def isolate_runtime_dir(tmp_path, monkeypatch):
    """Keep bridge portfiles out of the real XDG_RUNTIME_DIR during tests.
    Tests that need a specific dir override this via their own monkeypatch."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg"))

"""Make the repo's python packages importable from a bare checkout."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# In the consolidated repo the Kit bridge lives beside mcp/, not under it.
_EXT_ROOT = os.path.join(os.path.dirname(_REPO_ROOT), "exts", "isaac.mcp.bridge")
for _p in (_REPO_ROOT, _EXT_ROOT):   # isaac_mcp · isaac_mcp_bridge
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Tests that start another Python process get the same import roots through the environment.
_pythonpath = os.environ.get("PYTHONPATH", "")
for _p in reversed((_REPO_ROOT, _EXT_ROOT)):
    if _p not in _pythonpath.split(os.pathsep):
        _pythonpath = _p + (os.pathsep + _pythonpath if _pythonpath else "")
os.environ["PYTHONPATH"] = _pythonpath


import pytest


@pytest.fixture
def member_listing(monkeypatch):
    """Inspect underlying contracts in gateway tests, not the public grouped listing."""
    from isaac_mcp import grouped_tools, tool_groups
    monkeypatch.setattr(tool_groups, "listed_form", lambda text: text)
    original = grouped_tools.sync

    def sync(server, env=None):
        original(server, env)
        server._tool_manager.list_tools = lambda: [
            tool for name, tool in server._tool_manager._tools.items()
            if name not in tool_groups.GROUPS]

    monkeypatch.setattr(grouped_tools, "sync", sync)


@pytest.fixture(autouse=True)
def isolate_runtime_dir(tmp_path, monkeypatch):
    """Keep bridge portfiles out of the real XDG_RUNTIME_DIR during tests.
    Tests that need a specific dir override this via their own monkeypatch."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg"))


@pytest.fixture(autouse=True)
def no_kit_bridge_for_job_tests(monkeypatch):
    """Job tests must not depend on whether an Isaac Sim app happens to be running on this machine.
    test_isaaclab_kit_guard.py restores the real probe (captured at import time) where it needs it."""
    try:
        from isaac_mcp.isaaclab import jobs
    except ImportError:
        return
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: [])


@pytest.fixture(autouse=True)
def isolate_state_dir(tmp_path, monkeypatch):
    """Keep the host's local records (the server log, isaac_help's gap file) out of the real state folder."""
    monkeypatch.setenv("ISAAC_MCP_LOG_DIR", str(tmp_path / "state"))

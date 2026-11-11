"""A project's own tool package: loaded by name at startup, reloaded on request."""

import sys

import pytest

from bridge_testing import dispatch, make_bridge
from isaac_mcp_bridge import knobs, packages


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in [m for m in sys.modules if m.split(".")[0] in ("lab_tools", "plain_tools", "broken_tools")]:
        del sys.modules[name]


EXPLICIT = '''
NAMESPACE = "lab"
VERSION = {version}

def register(registrar):
    @registrar.tool("Say which version answered", {{"name": {{"type": "string"}}}}, read_only=True)
    def hello(name="world"):
        return {{"greeting": f"hello {{name}}", "version": VERSION}}
'''


def test_a_package_registers_its_tools_under_its_namespace(folder):
    (folder / "lab_tools.py").write_text(EXPLICIT.format(version=1))
    bridge = make_bridge()
    loaded = packages.load_tool_modules(bridge, ["lab_tools"])
    assert loaded == {"lab_tools": {"module": "lab_tools", "namespace": "lab", "tools": ["lab.hello"]}}
    assert dispatch(bridge, "lab.hello", {"name": "Kit"})["result"] == {"greeting": "hello Kit", "version": 1}
    assert bridge.get_registered_tools()["lab.hello"]["read_only"] is True
    assert bridge.get_owners()["lab_tools"]["metadata"] == {"module": "lab_tools"}


def test_a_package_with_no_register_function_offers_its_public_functions(folder):
    (folder / "plain_tools.py").write_text(
        'import os\n\ndef add(a: float, b: float = 1.0, exact: bool = False):\n    """Add two numbers."""\n    return {"sum": a + b}\n\n'
        'def _helper():\n    return 1\n')
    bridge = make_bridge()
    packages.load_tool_modules(bridge, ["plain_tools"])
    tools = bridge.get_registered_tools()
    assert [name for name in tools if name.startswith("plain_tools.")] == ["plain_tools.add"], "private names and imports are not tools"
    assert tools["plain_tools.add"]["description"] == "Add two numbers."
    assert tools["plain_tools.add"]["parameters"] == {"a": {"type": "number", "required": True}, "b": {"type": "number", "default": 1.0},
                                                      "exact": {"type": "boolean", "default": False}}
    assert dispatch(bridge, "plain_tools.add", {"a": 2})["result"] == {"sum": 3.0}


def test_reloading_picks_up_an_edit_and_keeps_the_state_submodule(folder):
    package = folder / "lab_tools"
    package.mkdir()
    (package / "__init__.py").write_text("from . import _state\n" + EXPLICIT.format(version=1))
    (package / "_state.py").write_text("handles = []\n")
    bridge = make_bridge()
    packages.load_tool_modules(bridge, ["lab_tools"])
    sys.modules["lab_tools._state"].handles.append("camera")
    (package / "__init__.py").write_text("from . import _state\n" + EXPLICIT.format(version=2))
    assert packages.reload_tool_module(bridge, "lab_tools")["tools"] == ["lab.hello"]
    assert dispatch(bridge, "lab.hello")["result"]["version"] == 2
    assert sys.modules["lab_tools._state"].handles == ["camera"], "live handles survive a reload"
    assert dispatch(bridge, "reload_tools", {"module": "lab_tools"})["status"] == "success"
    refused = dispatch(bridge, "reload_tools", {"module": "sim_tools"})
    assert refused["status"] == "error" and "not a loaded tool package" in refused["message"] and "file watcher" in refused["message"]


def test_a_package_that_fails_to_load_fails_the_startup_and_leaves_nothing_registered(folder):
    (folder / "broken_tools.py").write_text("def first():\n    return 1\n\ndef register(registrar):\n    registrar.tool('first')(first)\n    raise RuntimeError('half way')\n")
    (folder / "lab_tools.py").write_text(EXPLICIT.format(version=1))
    bridge = make_bridge()
    with pytest.raises(RuntimeError, match="half way"):
        packages.load_tool_modules(bridge, ["lab_tools", "broken_tools"])
    assert "broken_tools" not in bridge.get_owners() and not any(n.startswith("broken_tools.") for n in bridge.get_registered_tools())
    with pytest.raises(ModuleNotFoundError):
        packages.load_tool_modules(make_bridge(), ["no_such_tool_package"])


def test_settings_come_from_the_environment_then_the_default(monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_TOOL_MODULES", "a.b, c ,")
    assert knobs.listed(knobs.TOOL_MODULES.read()) == ["a.b", "c"]
    monkeypatch.delenv("ISAAC_MCP_TOOL_MODULES")
    assert knobs.listed(knobs.TOOL_MODULES.read()) == [] and knobs.listed(["x", " y "]) == ["x", "y"]
    assert knobs.PORT.path == "/persistent/exts/isaac.mcp.bridge/autostartPort"
    monkeypatch.setenv("ISAAC_MCP_AUTH", "0")
    assert knobs.AUTH.flag() is False
    monkeypatch.delenv("ISAAC_MCP_AUTH")
    assert knobs.AUTH.flag() is True and knobs.READ_ONLY.flag() is False

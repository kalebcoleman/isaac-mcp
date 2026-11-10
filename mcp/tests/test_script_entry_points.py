"""Host scripts: the scene client keeps exported tool groups, and smoke usage errors never reach the server."""

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _script(name):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scene_client_keeps_exported_tool_groups_and_still_pins_the_port(monkeypatch):
    client = _script("scene_mcp_client")
    monkeypatch.delenv("ISAAC_MCP_LOCAL_TOOLS", raising=False)
    monkeypatch.delenv("ISAAC_MCP_BUILTINS", raising=False)
    monkeypatch.setenv("UR5E_DT_PORT", "1")
    env = client.parameters(18781).env
    assert env["ISAAC_MCP_LOCAL_TOOLS"] == "gateway,screen_read,screen_capture,screen_control"
    assert env["ISAAC_MCP_BUILTINS"] == "0" and env["UR5E_DT_PORT"] == "18781"
    monkeypatch.setenv("ISAAC_MCP_LOCAL_TOOLS", "all,screen_input")
    assert client.parameters(18781).env["ISAAC_MCP_LOCAL_TOOLS"] == "all,screen_input"


@pytest.mark.parametrize("argv, code", [(["--help"], 0), (["-h"], 0), (["live"], 2), (["typo"], 2)])
def test_smoke_surface_usage_never_reaches_the_server(monkeypatch, capsys, argv, code):
    smoke = _script("smoke_mcp_surface")
    monkeypatch.setattr(smoke, "run", lambda plan: pytest.fail("must not call tools"))
    assert smoke.main(["smoke_mcp_surface.py", *argv]) == code
    captured = capsys.readouterr()
    assert "offline" in captured.out + captured.err

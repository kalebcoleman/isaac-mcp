"""Toolset ownership, configuration precedence and registration boundaries."""

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from isaac_mcp import config, host_catalog, local_tools, server, tool_groups, toolsets


def build(selection="default"):
    return server.build_server({"ISAAC_MCP_TOOLSETS": selection})


def test_registry_owns_every_registered_tool():
    instance = build("all,+input,+launch")
    for name, tool in instance._tool_manager._tools.items():
        assert toolsets.owner(name) in toolsets.TOOLSETS
        assert tool.annotations is not None
    for name, meta in host_catalog.load_catalog().items():
        assert toolsets.owner(name, meta["namespace"]) in toolsets.TOOLSETS
    assert sum(map(len, toolsets.MEMBERS.values())) == len(set().union(*toolsets.MEMBERS.values()))


@pytest.mark.parametrize("name", toolsets.TOOLSETS)
def test_each_toolset_alone_and_disabled_registers_nothing(name):
    instance = build("minimal,+" + name)
    assert all(toolsets.owner(tool) in {"core", name} for tool in instance._tool_manager._tools)
    if name != "core":
        disabled = build("all,+input,+launch,-" + name)
        assert all(toolsets.owner(tool) != name for tool in disabled._tool_manager._tools)


def test_profiles_and_guards():
    assert config.select("default") == toolsets.DEFAULT
    assert config.select("all").isdisjoint({"input", "launch"})
    assert config.select("minimal") == {"core"}
    assert config.select("minimal,lab,+screen,-screen") == {"core", "lab"}
    with pytest.raises(ValueError, match="cannot be turned off"):
        config.select("-core")
    for value in ("oops", "default,+backend", "--lab", ""):
        with pytest.raises(ValueError, match="Valid names"):
            config.select(value)


@pytest.mark.parametrize("variable", config.LEGACY)
def test_legacy_settings_fail_with_replacement(variable):
    with pytest.raises(ValueError) as error:
        server.build_server({variable: "all,screen_input,sim_launch"})
    assert variable in str(error.value)
    assert config.replacement(variable, "all,screen_input,sim_launch") in str(error.value)
    assert config.replacement("ISAAC_MCP_LOCAL_TOOLS", "all,screen_input,sim_launch") == "ISAAC_MCP_TOOLSETS=all,+input,+launch"


def test_five_source_precedence_and_backend(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    user = home / ".config/isaac-mcp/config.toml"
    user.parent.mkdir(parents=True)
    child = project / "nested"
    child.mkdir(parents=True)
    def resolve(env=None, cli=None):
        return config.resolve(env or {}, cli, cwd=child, home=home)
    assert resolve().toolsets == toolsets.DEFAULT
    user.write_text('[tools]\ntoolsets = ["minimal", "+lab"]\n[sim]\nphysics_backend = "newton"\n')
    assert resolve().toolsets == {"core", "lab"}
    assert resolve().physics_backend == "newton"
    (project / "isaac-mcp.toml").write_text('[tools]\ntoolsets = ["minimal", "+screen"]\n')
    assert resolve().toolsets == {"core", "screen"}
    assert resolve({"ISAAC_MCP_TOOLSETS": "minimal,+assets"}).toolsets == {"core", "assets"}
    assert resolve({"ISAAC_MCP_TOOLSETS": "minimal,+assets"}, "minimal,+code").toolsets == {"core", "code"}
    assert resolve({"ISAAC_MCP_PHYSICS_BACKEND": "physx"}).physics_backend == "physx"
    with pytest.raises(ValueError, match="physics_backend"):
        resolve({"ISAAC_MCP_PHYSICS_BACKEND": "invalid"})


def test_registration_requires_ownership_and_annotations():
    scoped = local_tools.ScopedTools(FastMCP("guard"))
    with pytest.raises(local_tools.LocalToolsError, match="no toolset"):
        scoped.tool(annotations=local_tools.READ_ONLY)(lambda: None)
    with pytest.raises(local_tools.LocalToolsError, match="no ToolAnnotations"):
        scoped.tool(name="status")(lambda: None)


def test_live_visibility_uses_same_selection():
    instance = build("minimal,+extensions")
    state = server._state_for(instance)
    assert server._visible("demo.ping", {"namespace": "demo"}, state)
    assert not server._visible("sim.stage.info", {"namespace": "sim"}, state)
    assert not server._visible("ur5e_dt.future_tool", {"namespace": "ur5e_dt"}, state)
    assert toolsets.owner("ur5e_dt.future_tool", "ur5e_dt") == "workcell"
    with pytest.raises(ValueError, match="no toolset"):
        toolsets.owner("sim.unknown", "sim")


@pytest.mark.parametrize("profile,budget", [("minimal", 4000), ("default", 22000), ("all", 35000)])
def test_listing_size(profile, budget):
    instance = build(profile)
    tools = asyncio.run(instance.list_tools())
    payload = json.dumps([{key: value for key, value in tool.model_dump(mode="json").items()
                           if key in ("name", "description", "inputSchema", "annotations")} for tool in tools])
    print(f"\n{profile}: {len(tools)} tools, {len(payload)} characters, {len(payload) / 4} estimated tokens")
    assert len(payload) / 4 < budget


@pytest.mark.parametrize("enabled", [True, False])
def test_capture_directories_follow_screen_toolset(tmp_path, monkeypatch, enabled):
    from isaac_mcp import screen
    shots, videos = tmp_path / "shots", tmp_path / "videos"
    monkeypatch.setattr(screen, "SHOT_DIR", shots)
    monkeypatch.setattr(screen, "VIDEO_DIR", videos)
    build("minimal,+screen" if enabled else "minimal")
    assert shots.exists() is enabled
    assert videos.exists() is enabled


def test_cli_table_and_override(monkeypatch, capsys):
    monkeypatch.setenv("ISAAC_MCP_TOOLSETS", "minimal")
    server.main(["--toolsets", "minimal,+lab", "toolsets"])
    output = capsys.readouterr().out
    assert "Toolset" in output
    for name in toolsets.TOOLSETS:
        row = next(row for row in output.splitlines() if row.startswith(name + " "))
        assert row.split()[1] == ("on" if name in {"core", "lab"} else "off")
        assert "+" + name in row


@pytest.mark.parametrize("selection", ["minimal", "default", "all,+input,+launch"])
def test_status_resource_and_help_share_selection(selection, monkeypatch):
    instance = build(selection)
    state = server._state_for(instance)
    monkeypatch.setattr(state, "get_client", lambda: (_ for _ in ()).throw(ConnectionRefusedError("Connection refused")))
    monkeypatch.setattr(server, "_surroundings", lambda state: {"bridges": []})
    expected = toolsets.describe(state.toolsets)
    assert server._where_am_i({}, state)["toolsets"] == expected
    contents = asyncio.run(instance.read_resource("isaac-mcp://toolsets"))
    assert json.loads(list(contents)[0].content) == expected
    help_tool = instance._tool_manager._tools["isaac_help"]
    answer = help_tool.fn(task="train a policy")
    if "lab" in state.toolsets:
        assert "isaaclab__train" in answer
    else:
        assert "Toolsets off:" in answer and "lab" in answer
        assert "ISAAC_MCP_TOOLSETS" in answer
        assert "isaaclab__train" not in answer


def test_help_respects_disabled_code_and_mixed_topics(monkeypatch):
    monkeypatch.setattr(server, "_record_help_gap", lambda *args: None)
    answer = server._isaac_help("simulate cloth tearing", enabled={"core"})
    assert "run_python" not in answer
    assert "Toolsets off: code" in answer
    answer = server._isaac_help("where am i", enabled={"core"})
    assert "Available: status" in answer
    assert "sim__launch" not in answer
    assert "launch" in answer


def test_instructions_are_short_and_selection_neutral():
    assert 450 < len(server.SERVER_INSTRUCTIONS) < 700
    assert "status" in server.SERVER_INSTRUCTIONS and "toolsets" in server.SERVER_INSTRUCTIONS
    assert "isaaclab__" not in server.SERVER_INSTRUCTIONS

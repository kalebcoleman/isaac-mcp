"""Toolset ownership, configuration precedence and registration boundaries."""

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from twinsmith import config, host_catalog, local_tools, server, tool_groups, toolsets


def build(selection="default"):
    return server.build_server({"TWINSMITH_TOOLSETS": selection})


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


def test_robosim_is_off_by_default_and_registers_only_its_grouped_tool():
    assert "robosim" not in toolsets.DEFAULT and "robosim" not in toolsets.PLANNED
    assert "robosim" in config.select("all")
    assert "robosim" not in {name for name in toolsets.TOOLSETS if name in server._state_for(build()).toolsets}
    assert "robosim" not in build()._tool_manager._tools
    alone = build("minimal,+robosim")
    assert {name for name in alone._tool_manager._tools if toolsets.owner(name) == "robosim"} == {"robosim"}
    assert toolsets.MEMBERS["robosim"] == {"robosim"}


def test_documented_default_and_planned_selection():
    assert toolsets.DEFAULT == {"core", "scene", "physics", "sensors", "assets", "code", "screen", "extensions"}
    assert config.select("all") == set(toolsets.TOOLSETS) - {"input", "launch"}
    assert set(toolsets.PLANNED) == {"backend", "params", "compare", "planning", "real2sim", "eval"}
    assert not set(toolsets.PLANNED) & set(toolsets.TOOLSETS)


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
    local = next(name for name in config.LEGACY if name.endswith("LOCAL_TOOLS"))
    assert config.replacement(local, "all,screen_input,sim_launch") == "TWINSMITH_TOOLSETS=all,+input,+launch"


def test_five_source_precedence_and_backend(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    user = home / ".config/twinsmith/config.toml"
    user.parent.mkdir(parents=True)
    child = project / "nested"
    child.mkdir(parents=True)
    def resolve(env=None, cli=None):
        return config.resolve(env or {}, cli, cwd=child, home=home)
    assert resolve().toolsets == toolsets.DEFAULT
    user.write_text('[tools]\ntoolsets = ["minimal", "+lab"]\n[sim]\nphysics_backend = "newton"\n')
    assert resolve().toolsets == {"core", "lab"}
    assert resolve().physics_backend == "newton"
    (project / "twinsmith.toml").write_text('[tools]\ntoolsets = ["minimal", "+screen"]\n')
    assert resolve().toolsets == {"core", "screen"}
    assert resolve({"TWINSMITH_TOOLSETS": "minimal,+assets"}).toolsets == {"core", "assets"}
    assert resolve({"TWINSMITH_TOOLSETS": "minimal,+assets"}, "minimal,+code").toolsets == {"core", "code"}
    assert resolve({"TWINSMITH_PHYSICS_BACKEND": "physx"}).physics_backend == "physx"
    with pytest.raises(ValueError, match="physics_backend"):
        resolve({"TWINSMITH_PHYSICS_BACKEND": "invalid"})


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
    assert not server._visible("workcell.future_tool", {"namespace": "workcell"}, state)
    assert toolsets.owner("workcell.future_tool", "workcell") == "workcell"
    with pytest.raises(ValueError, match="no toolset"):
        toolsets.owner("sim.unknown", "sim")


@pytest.mark.parametrize("profile,budget", [("minimal", 1670), ("default", 15900), ("all", 23350)])   # measured 1,590 / 15,134 / 22,201, plus about 5 percent
def test_listing_size(profile, budget):
    instance = build(profile)
    tools = asyncio.run(instance.list_tools())
    payload = json.dumps([{key: value for key, value in tool.model_dump(mode="json").items()
                           if key in ("name", "description", "inputSchema", "annotations")} for tool in tools],
                         separators=(",", ":"))
    print(f"\n{profile}: {len(tools)} tools, {len(payload)} characters, {len(payload) // 4} estimated tokens")
    assert len(payload) // 4 < budget


@pytest.mark.parametrize("enabled", [True, False])
def test_capture_directories_follow_screen_toolset(tmp_path, monkeypatch, enabled):
    from twinsmith import screen
    shots, videos = tmp_path / "shots", tmp_path / "videos"
    monkeypatch.setattr(screen, "SHOT_DIR", shots)
    monkeypatch.setattr(screen, "VIDEO_DIR", videos)
    build("minimal,+screen" if enabled else "minimal")
    assert shots.exists() is enabled
    assert videos.exists() is enabled


def test_cli_table_and_override(monkeypatch, capsys):
    monkeypatch.setenv("TWINSMITH_TOOLSETS", "minimal")
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
    contents = asyncio.run(instance.read_resource("twinsmith://toolsets"))
    assert json.loads(list(contents)[0].content) == expected
    help_tool = instance._tool_manager._tools["help"]
    answer = help_tool.fn(task="train a policy")
    if "lab" in state.toolsets:
        assert "isaaclab__train" in answer
    else:
        assert "Toolsets off:" in answer and "lab" in answer
        assert "TWINSMITH_TOOLSETS" in answer
        assert "isaaclab__train" not in answer


def test_help_respects_disabled_code_and_mixed_topics(monkeypatch):
    monkeypatch.setattr(server, "_record_help_gap", lambda *args: None)
    answer = server._help("simulate cloth tearing", enabled={"core"})
    assert "run_python" not in answer
    assert "Toolsets off: code" in answer
    answer = server._help("where am i", enabled={"core"})
    assert "Available: status" in answer
    assert "sim__launch" not in answer
    assert "launch" in answer


def test_instructions_are_short_and_selection_neutral():
    assert 450 < len(server.SERVER_INSTRUCTIONS) < 700
    assert "status" in server.SERVER_INSTRUCTIONS and "toolsets" in server.SERVER_INSTRUCTIONS
    assert "isaaclab__" not in server.SERVER_INSTRUCTIONS


def test_workcell_owns_the_whole_workcell_namespace():
    owned = {name for name in build("minimal,+workcell")._tool_manager._tools if toolsets.owner(name) == "workcell"}
    assert owned and all(name.startswith("workcell__") for name in owned)
    assert {"workcell__jenga", "workcell__jenga_build", "workcell__cell"} <= owned


# ---- enabling and disabling toolsets at run time ----

RUNTIME = [name for name, spec in toolsets.TOOLSETS.items() if name != "core" and not spec.explicit_only]


def offline_build(selection="default", extra=None):
    """A server with no bridge to find, so run-time changes use only host tools and the catalog."""
    instance = server.build_server({"TWINSMITH_TOOLSETS": selection, **(extra or {})})
    state = server._state_for(instance)
    state.get_client = lambda: (_ for _ in ()).throw(ConnectionRefusedError("Connection refused"))
    return instance, state


def change(instance, action, names=None, ctx=None):
    return asyncio.run(server._change_toolsets(instance, action, names, ctx))


def registered(instance):
    return set(instance._tool_manager._tools)


class FakeSession:
    def __init__(self):
        self.sent = 0

    async def send_tool_list_changed(self):
        self.sent += 1


class FakeContext:
    def __init__(self):
        self.session = FakeSession()
        self.request_context = self


@pytest.mark.parametrize("name", RUNTIME)
def test_enable_registers_exactly_what_startup_would(name):
    instance, _ = offline_build("minimal")
    before = set(server.grouped_tools.listed_names(instance))
    result = change(instance, "enable", [name])
    expected = build("minimal,+" + name)
    assert registered(instance) == registered(expected)
    assert set(server.grouped_tools.listed_names(instance)) == set(server.grouped_tools.listed_names(expected))
    assert set(result["tools_available"]) == set(server.grouped_tools.listed_names(instance)) - before
    assert result["changed"] == [name] and name in result["on"]
    assert "nothing was written" in result["scope"]
    assert "loads tools lazily, search for the tool by name (for example robosim) before calling it" in result["note"]


@pytest.mark.parametrize("name", RUNTIME)
def test_disable_removes_exactly_that_toolsets_tools(name):
    instance, _ = offline_build("all")
    result = change(instance, "disable", [name])
    assert registered(instance) == registered(build("all,-" + name))
    assert (result["tools_removed"] or name == "extensions") and name not in result["on"]   # extensions list nothing offline
    assert not any(toolsets.owner(tool) == name for tool in registered(instance) if tool not in tool_groups.GROUPS)


def test_enabled_tools_are_callable_and_disabled_ones_are_not():
    instance, _ = offline_build("minimal")
    with pytest.raises(Exception):
        asyncio.run(instance.call_tool("windows", {"action": "help"}))
    change(instance, "enable", ["screen"])
    answer = asyncio.run(instance.call_tool("windows", {"action": "help"}))
    assert "list_windows" in json.dumps(str(answer)) or "windows" in str(answer)
    change(instance, "disable", ["screen"])
    with pytest.raises(Exception):
        asyncio.run(instance.call_tool("windows", {"action": "help"}))


def test_enable_twice_is_harmless():
    instance, _ = offline_build("minimal")
    change(instance, "enable", ["lab", "workcell"])
    once = registered(instance)
    listed = set(server.grouped_tools.listed_names(instance))
    again = change(instance, "enable", ["lab", "workcell"])
    assert again["changed"] == [] and again["unchanged"] == ["lab", "workcell"]
    assert "tools_available" in again and again["tools_available"] == []
    assert registered(instance) == once and set(server.grouped_tools.listed_names(instance)) == listed
    change(instance, "disable", ["lab"])
    change(instance, "enable", ["lab"])
    assert registered(instance) == once


@pytest.mark.parametrize("name", ["input", "launch"])
def test_explicit_only_toolsets_can_never_be_enabled_at_run_time(name):
    from mcp.server.fastmcp.exceptions import ToolError
    instance, state = offline_build("minimal")
    before = registered(instance)
    with pytest.raises(ToolError) as error:
        change(instance, "enable", ["lab", name])
    assert "explicit-only" in str(error.value) and "TWINSMITH_TOOLSETS" in str(error.value)
    assert "restart" in str(error.value) and "Nothing changed" in str(error.value)
    assert registered(instance) == before and state.toolsets == {"core"}      # refused whole, "lab" too
    assert name not in server._where_am_i({}, state)["toolsets"]["can_enable"]
    assert name in server._where_am_i({}, state)["toolsets"]["off"]


def test_explicit_only_ones_stay_available_at_startup():
    instance, state = offline_build("minimal,+input,+launch")
    assert {"window_input", "sim__session"} <= registered(instance)
    change(instance, "disable", ["input"])          # a user's own startup choice can be dropped again
    assert "window_input" not in registered(instance)
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError, match="explicit-only"):
        change(instance, "enable", ["input"])


def test_core_cannot_be_disabled_and_unknown_names_are_refused():
    from mcp.server.fastmcp.exceptions import ToolError
    instance, state = offline_build("default")
    before = registered(instance)
    for action, names, text in (("disable", ["scene", "core"], "core cannot be disabled"),
                                ("enable", ["nonsense"], "Unknown toolset"),
                                ("enable", [], "needs names"), ("enable", None, "needs names"),
                                ("disable", ["lab-ish"], "Unknown toolset")):
        with pytest.raises(ToolError, match=text):
            change(instance, action, names)
    assert registered(instance) == before and "scene" in state.toolsets and "toolsets" in registered(instance)


@pytest.mark.parametrize("how", ["env", "env_false", "user_file", "project_file"])
def test_runtime_enable_switch_turns_the_feature_off(how, tmp_path, monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError
    home = tmp_path / "home"
    (home / ".config/twinsmith").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    extra = {}
    if how == "env":
        extra = {"TWINSMITH_RUNTIME_ENABLE": "0"}
    elif how == "env_false":
        extra = {"TWINSMITH_RUNTIME_ENABLE": "false"}
    elif how == "user_file":
        (home / ".config/twinsmith/config.toml").write_text("[tools]\nruntime_enable = false\n")
    else:
        (tmp_path / "twinsmith.toml").write_text("[tools]\nruntime_enable = false\n")
    instance, state = offline_build("minimal", extra)
    assert state.runtime_enable is False
    for action in ("enable", "disable"):
        with pytest.raises(ToolError, match="switched off"):
            change(instance, action, ["lab"])
    assert registered(instance) == registered(build("minimal"))
    listing = change(instance, "list")                       # reading is always allowed
    assert listing["runtime_enable"] is False and listing["can_enable"] == []
    assert "off" in listing["enable"].lower() and "startup" in listing["enable"] or "TWINSMITH_TOOLSETS" in listing["enable"]


def test_runtime_enable_default_and_bad_value():
    assert config.resolve({}).runtime_enable is True
    assert config.resolve({"TWINSMITH_RUNTIME_ENABLE": "1"}).runtime_enable is True
    with pytest.raises(ValueError, match="TWINSMITH_RUNTIME_ENABLE"):
        config.resolve({"TWINSMITH_RUNTIME_ENABLE": "maybe"})


def test_a_tool_list_change_is_announced_once_and_only_when_something_changed():
    instance, _ = offline_build("minimal")
    ctx = FakeContext()
    change(instance, "enable", ["lab"], ctx)
    assert ctx.session.sent == 1
    change(instance, "enable", ["lab"], ctx)                  # nothing changed: no notification
    change(instance, "list", None, ctx)
    assert ctx.session.sent == 1
    change(instance, "disable", ["lab"], ctx)
    assert ctx.session.sent == 2


def test_server_advertises_list_changed_for_the_tool():
    instance = build("minimal")
    options = instance._mcp_server.create_initialization_options()
    assert options.capabilities.tools.listChanged is True
    tool = instance._tool_manager._tools["toolsets"]
    assert tool.parameters["properties"]["action"]["enum"] == ["list", "enable", "disable"]
    assert toolsets.owner("toolsets") == "core"


def test_workcell_and_robosim_register_without_a_bridge_or_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINSMITH_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("TWINSMITH_ROBOSIM_ROOT", str(tmp_path / "no-checkout"))
    instance, _ = offline_build("minimal")
    result = change(instance, "enable", ["workcell", "robosim"])
    assert {"robosim", "workcell__cell", "workcell__jenga"} <= set(result["tools_available"])
    assert "dial the selected bridge" in result["note"]
    answer = asyncio.run(instance.call_tool("workcell__cell", {"action": "status"}))
    text = str(answer)
    assert "not available" in text and "no Isaac Sim bridge could be reached" in text
    answer = asyncio.run(instance.call_tool("robosim", {"action": "status"}))
    assert "missing or unreadable" in str(answer) and "never installs" in str(answer)


def test_enable_also_reads_the_live_bridge_tools(monkeypatch):
    instance, state = offline_build("minimal")
    seen = []
    monkeypatch.setattr(server, "discover_and_register_tools", lambda srv: seen.append(sorted(state.toolsets)) or [])
    change(instance, "enable", ["scene"])
    assert seen and "scene" in seen[0]


def test_status_toolsets_field_says_how_to_enable():
    instance, state = offline_build("default")
    field = server._where_am_i({}, state)["toolsets"]
    assert 'toolsets(action="enable", names=[...])' in field["enable"]
    assert "input" in field["enable"] and "launch" in field["enable"] and "startup" in field["enable"]
    assert field["can_enable"] == [name for name in toolsets.TOOLSETS if name not in state.toolsets
                                   and name not in toolsets.explicit_only()]
    assert field["tools"]["scene"] == 6 and field["tools"]["lab"] == 4 and field["tools"]["workcell"] == 7
    contents = asyncio.run(instance.read_resource("twinsmith://toolsets"))
    assert json.loads(list(contents)[0].content) == field
    change(instance, "enable", ["lab"])
    assert "lab" in json.loads(list(asyncio.run(instance.read_resource("twinsmith://toolsets")))[0].content)["on"]


def test_listed_tool_counts_match_what_a_full_server_lists():
    instance = build("all,+input,+launch")
    listed = server.grouped_tools.listed_names(instance)
    for name in toolsets.TOOLSETS:
        count = toolsets.listed_count(name)
        if count is not None:
            assert count == sum(toolsets.owner(tool) == name for tool in listed), name


def test_help_names_the_enable_call_for_a_toolset_that_is_off():
    answer = server._help("train a policy")
    assert 'toolsets(action="enable", names=["lab"])' in answer
    switched_off = server._help("train a policy", runtime_enable=False)
    assert 'toolsets(action="enable"' not in switched_off and "switched off" in switched_off
    assert 'names=["launch"]' not in server._help("start a simulator")
    assert "Explicit-only" in server._help("start a simulator")


@pytest.mark.parametrize("bridges", [[], [{"port": 8766, "pid": 1}]])
def test_status_without_a_bridge_says_which_tools_need_none(monkeypatch, bridges):
    class Client:
        def call(self, *args, **kwargs):
            raise ConnectionRefusedError("refused")

    class State:
        toolsets = frozenset()
        runtime_enable = True

        def get_client(self):
            return Client()

    monkeypatch.setattr(server, "_surroundings", lambda state: {"bridges": bridges})
    monkeypatch.setattr(server, "_physics_status", lambda *args, **kwargs: {})
    answer = server._where_am_i({}, State())
    assert answer["running"] is False
    assert "The robosim, isaaclab (lab) and screen tools need no bridge." in answer["message"]
    assert ("use_bridge(port=8766)" in answer["message"]) == bool(bridges)

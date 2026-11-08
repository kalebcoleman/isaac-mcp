"""ISAAC_MCP_LOCAL_TOOLS selects which local tools register; every local tool is annotated.

Expectations come from the group definitions in ``isaac_mcp.local_tools`` (and, for the
"nothing escaped the groups" check, from the decorators in the source via
``scripts/check_docs.py``), so adding a tool means classifying it, not editing counts here.
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

import isaac_mcp.screen as screen
import isaac_mcp.server as gw
from isaac_mcp import host_catalog, local_tools as lt
from isaac_mcp_bridge.schema import field_specs

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
check_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_docs)

VAR = lt.ENV_VAR
# Every built server also lists the packaged catalog's offline proxies, whatever the local groups
# say (tests/test_host_catalog_gateway.py covers them); these tests are about the local tools.
OFFLINE = {gw._mcp_name(canonical) for canonical in host_catalog.catalog_tool_names()}
# Named in the task as the tools a read-only profile must not expose.
MUTATING = {"isaaclab__train", "isaaclab__eval", "isaaclab__play", "isaaclab__runs_cancel",
            "screenshot", "start_screen_recording", "stop_screen_recording", "raise_window", "move_window",
            "use_bridge", "refresh_tools", "isaaclab__install_info", "isaaclab__envs_list",
            "isaaclab__runs_list", "isaaclab__runs_status", "isaaclab__runs_tail"}


@pytest.fixture(autouse=True)
def capture_dirs(tmp_path, monkeypatch):
    """Point the capture directories at tmp_path so no test touches the real home directory."""
    shots, videos = tmp_path / "Pictures" / "screen-mcp", tmp_path / "Videos" / "screen-mcp"
    monkeypatch.setattr(screen, "SHOT_DIR", shots)
    monkeypatch.setattr(screen, "VIDEO_DIR", videos)
    return shots, videos


def build(value=None):
    return gw.build_server({} if value is None else {VAR: value})


def local_names(server):
    return set(server._tool_manager._tools) - OFFLINE


def tools_of(server):
    return {t.name: t for t in asyncio.run(server.list_tools()) if t.name not in OFFLINE}


# ---- parsing ---------------------------------------------------------------------------

def test_unset_empty_and_blank_mean_all():
    for raw in (None, "", "   ", ",", "all", " ALL "):
        assert lt.parse_local_tools(raw) == lt.DEFAULT_GROUPS
        assert not {"screen_input", "sim_launch"} & lt.parse_local_tools(raw)


def test_aliases_and_groups_combine():
    assert lt.parse_local_tools("readonly") == lt.READ_ONLY_GROUPS
    assert lt.parse_local_tools("none") == frozenset()
    assert lt.parse_local_tools("readonly, gateway ,screen_capture") == (
        lt.READ_ONLY_GROUPS | {"gateway", "screen_capture"})
    assert lt.parse_local_tools("none,isaaclab_run") == {"isaaclab_run"}


def test_groups_partition_the_tools():
    seen = {}
    for group, tools in lt.GROUPS.items():
        for tool in tools:
            assert tool not in seen, f"{tool} is in {seen[tool]} and {group}"
            seen[tool] = group
    assert lt.READ_ONLY_TOOLS == lt.GROUPS["isaaclab_read"] | lt.GROUPS["screen_read"]


@pytest.mark.parametrize("raw", ["bogus", "readonly,bogus", "isaaclab-read", "all,nope,nada"])
def test_unknown_group_fails_with_a_clear_message(raw):
    with pytest.raises(lt.LocalToolsError) as err:
        lt.parse_local_tools(raw)
    message = str(err.value)
    assert VAR in message and "unknown group" in message
    assert all(group in message for group in lt.GROUPS)       # lists what would have been valid
    assert any(bad in message for bad in raw.split(",") if bad not in set(lt.ALIASES) | set(lt.GROUPS))


def test_unknown_group_stops_server_construction_before_registering():
    with pytest.raises(lt.LocalToolsError):
        build("readonly,bogus")


# ---- registration ----------------------------------------------------------------------

def test_default_registers_standard_tools_without_desktop_input():
    names = local_names(build())
    assert names == set(lt.DEFAULT_TOOLS)
    assert local_names(build("all")) == names
    assert local_names(build("all,screen_input,sim_launch")) == set(lt.ALL_TOOLS)
    # The module-level default instance is the same configuration.
    assert local_names(gw.mcp) == names


def test_groups_cover_exactly_the_decorated_tools_in_the_source():
    decorated = set()
    for rel in ("screen.py", "desktop_input.py", "sim_sessions.py", "server.py", "isaaclab/tools.py"):
        decorated |= check_docs.tool_decorators(ROOT / "mcp" / "isaac_mcp" / rel)
    assert decorated == set(lt.ALL_TOOLS)


def test_readonly_exposes_exactly_the_read_only_tools():
    names = local_names(build("readonly"))
    assert names == set(lt.READ_ONLY_TOOLS)
    assert not names & MUTATING


def test_none_registers_no_local_tools():
    assert local_names(build("none")) == set()


def test_each_group_registers_only_its_tools():
    for group, tools in lt.GROUPS.items():
        assert local_names(build(group)) == set(tools), group


def test_only_the_selected_groups_register():
    assert local_names(build("isaaclab_run,gateway")) == (
        lt.GROUPS["isaaclab_run"] | lt.GROUPS["gateway"])


def test_restricted_servers_say_so_in_their_instructions():
    assert build().instructions == gw.SERVER_INSTRUCTIONS
    assert "ISAAC_MCP_LOCAL_TOOLS" in build("readonly").instructions
    assert build("readonly").instructions.startswith(gw.SERVER_INSTRUCTIONS)
    assert "bridge" in build("readonly").instructions.lower()     # says the limit is local-only


def test_register_functions_default_to_standard_groups_on_a_plain_server():
    plain = FastMCP("plain")
    screen.register_screen_tools(plain)
    from isaac_mcp.desktop_input import register_desktop_input_tools
    register_desktop_input_tools(plain)
    from isaac_mcp.sim_sessions import register_sim_session_tools
    register_sim_session_tools(plain)
    from isaac_mcp.isaaclab.tools import register_isaaclab_tools
    register_isaaclab_tools(plain)
    assert set(plain._tool_manager._tools) == (
        lt.DEFAULT_TOOLS - lt.GROUPS["gateway"] - {"isaac_help"})


# ---- guards against a future tool escaping the scheme ----------------------------------

def test_a_tool_outside_every_group_cannot_register():
    scoped = lt.ScopedTools(FastMCP("x"))
    with pytest.raises(lt.LocalToolsError, match="not in any group"):
        @scoped.tool(annotations=lt.READ_ONLY)
        def brand_new_tool():
            return ""


def test_an_unannotated_tool_cannot_register():
    scoped = lt.ScopedTools(FastMCP("x"))
    with pytest.raises(lt.LocalToolsError, match="no ToolAnnotations"):
        @scoped.tool(name="screen_info")
        def screen_info():
            return ""


def test_every_local_tool_is_annotated_and_read_only_matches_the_groups():
    tools = tools_of(build("all,screen_input,sim_launch"))
    assert set(tools) == set(lt.ALL_TOOLS)
    for name, tool in tools.items():
        ann = tool.annotations
        assert ann is not None, f"{name} has no annotations"
        python_tools = lt.GROUPS["isaaclab_probe"] | lt.GROUPS["screen_input"] | lt.GROUPS["sim_launch"] | {
            "isaaclab__train", "isaaclab__eval", "isaaclab__play", "isaaclab__env_check", "isaaclab__record"}
        assert ann.openWorldHint is (name in python_tools), name
        if name in lt.READ_ONLY_TOOLS:
            assert ann.readOnlyHint is True and ann.idempotentHint is True, name
        else:
            assert ann.readOnlyHint is False, name
            destructive = python_tools | {
                "isaaclab__runs_cancel", "isaaclab__runs_list", "isaaclab__runs_status"}
            assert ann.destructiveHint is (name in destructive), name


def test_previous_readonly_selection_can_be_restored_explicitly():
    old_isaaclab_read = {
        "isaaclab__install_info", "isaaclab__envs_list", "isaaclab__runs_list",
        "isaaclab__runs_status", "isaaclab__runs_tail", "isaaclab__checkpoints_list",
        "isaaclab__metrics_summary", "isaaclab__tensorboard_tail", "isaaclab__preflight",
    }
    assert local_names(build("readonly,isaaclab_probe,isaaclab_jobs")) == (
        old_isaaclab_read | lt.GROUPS["screen_read"])
    assert local_names(build("isaaclab_read,isaaclab_probe,isaaclab_jobs")) == old_isaaclab_read


def test_capture_tools_say_where_their_output_goes():
    tools = tools_of(build())
    for name in ("screenshot", "start_screen_recording", "stop_screen_recording", "list_windows"):
        assert "Privacy:" in tools[name].description, name


def test_use_bridge_description_does_not_present_list_bridges_as_a_tool():
    doc = tools_of(build())["use_bridge"].description
    assert "--list-bridges" in doc and "Use list_bridges" not in doc
    assert "built-in" in doc


# ---- no filesystem side effects ---------------------------------------------------------

@pytest.mark.parametrize("value", ["readonly", "none", "isaaclab_run,screen_read,screen_control,gateway"])
def test_without_screen_capture_no_capture_directories_are_created(value, capture_dirs):
    build(value)
    for path in capture_dirs:
        assert not path.exists() and not path.parent.exists(), path


def test_screen_capture_creates_them_as_before(capture_dirs):
    build("screen_capture")
    assert all(path.is_dir() for path in capture_dirs)


def test_default_creates_them_as_before(capture_dirs):
    build()
    assert all(path.is_dir() for path in capture_dirs)


# ---- composition with the bridge filters, and with discovery ---------------------------

def _meta(description, namespace="demo"):
    return {"description": description, "parameters": {}, "fields": field_specs({}),
            "namespace": namespace, "owner_id": "A"}


class FakeBridge:
    def __init__(self, tools):
        self.tools = tools
        self.port = 8766

    def call(self, tool, params=None, **kw):
        if tool == "list_tools":
            return {"tools": self.tools}
        if tool == "status":
            return {"pid": 1234, "port": self.port}
        raise AssertionError(f"unexpected bridge call {tool}")

    def close(self):
        pass


@pytest.fixture()
def bridge(monkeypatch):
    fake = FakeBridge({
        "demo.ping": _meta("ping"),
        "other.pong": _meta("pong", namespace="other"),
        "run_python": _meta("builtin", namespace=None),
    })
    monkeypatch.setattr(gw, "_NAME_MAP", {})
    monkeypatch.setattr(gw, "_REGISTERED_META", {})
    monkeypatch.setattr(gw, "_client", None)
    monkeypatch.setattr(gw, "get_client", lambda: fake)
    monkeypatch.setattr(gw, "BridgeClient", lambda port=None, **kw: fake)
    return fake


def _listed(server):
    async def go():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            await client.initialize()
            # Catalog tools count as bridge tools here only while the live bridge serves them
            # (the built-ins are catalogued too, so an offline run_python proxy is not a live one).
            live = gw._state_for(server).name_map
            return {t.name: t for t in (await client.list_tools()).tools
                    if t.name not in OFFLINE or t.name in live}
    return asyncio.run(go())


def test_startup_discovery_still_runs_when_gateway_is_excluded(bridge):
    server = build("readonly")
    assert "refresh_tools" not in local_names(server)
    listed = _listed(server)
    assert {"demo__ping", "other__pong", "run_python"} <= set(listed)
    assert set(listed) - {"demo__ping", "other__pong", "run_python"} == set(lt.READ_ONLY_TOOLS)


BUILTINS = {"run_python", "status", "list_bridges", "reload_tools", "put_file", "get_file", "stat_file"}


def test_builtins_are_listed_offline_and_follow_the_builtins_switch(monkeypatch):
    # PR D: OpenCode ignores tools/list_changed, so the built-ins must be listed before discovery.
    monkeypatch.delenv("ISAAC_MCP_BUILTINS", raising=False)
    monkeypatch.setenv("ISAAC_MCP_NAMESPACE", "no-such-namespace")   # as live: built-ins have none
    assert BUILTINS <= set(build("none")._tool_manager._tools)
    assert BUILTINS <= set(gw.offline_tools(build("none")))
    monkeypatch.setenv("ISAAC_MCP_BUILTINS", "0")
    assert not BUILTINS & set(build("none")._tool_manager._tools)


def test_none_is_a_pure_bridge_gateway(bridge):
    assert set(_listed(build("none"))) == {"demo__ping", "other__pong", "run_python"}


def test_namespace_and_builtins_filters_compose_with_local_tools(bridge, monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_NAMESPACE", "demo")
    monkeypatch.setenv("ISAAC_MCP_BUILTINS", "0")
    assert set(_listed(build("readonly"))) == set(lt.READ_ONLY_TOOLS) | {"demo__ping"}


def test_bridge_filters_never_remove_local_tools(bridge, monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_NAMESPACE", "nothing-matches")
    monkeypatch.setenv("ISAAC_MCP_BUILTINS", "0")
    assert set(_listed(build())) == set(lt.DEFAULT_TOOLS)


def test_list_changed_is_advertised_by_a_restricted_server_too(bridge):
    async def go():
        async with create_connected_server_and_client_session(build("gateway")._mcp_server) as c:
            return (await c.initialize()).capabilities

    caps = asyncio.run(go())
    assert caps.tools is not None and caps.tools.listChanged is True


def test_gateway_tools_of_a_built_server_act_on_that_server(bridge):
    server = build("gateway")

    async def go():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            await client.initialize()
            bridge.tools = {"demo.ping": _meta("ping"), "demo.more": _meta("more")}
            result = await client.call_tool("refresh_tools", {})
            return result, {t.name for t in (await client.list_tools()).tools}

    result, names = asyncio.run(go())
    assert not result.isError and "demo__more" in names
    assert "demo__more" not in local_names(gw.mcp)       # the default server was not touched

# ---- what ISAAC_MCP_LOCAL_TOOLS does and does not restrict -------------------------------
# These pin the claims the docs make: `readonly` limits the host's own tools and nothing that
# comes from the bridge. For this bridge, which has both built-ins and namespaced tools,
# hiding all of them needs the built-ins switch and an unadvertised namespace.

BRIDGE_TOOLS = {"demo__ping", "other__pong", "run_python"}


def test_readonly_does_not_restrict_bridge_tools(bridge):
    assert BRIDGE_TOOLS <= set(_listed(build("readonly")))


def test_no_bridge_tools_need_builtins_off_and_a_namespace_nothing_serves(bridge, monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_BUILTINS", "0")
    monkeypatch.setenv("ISAAC_MCP_NAMESPACE", "no-such-namespace")
    assert set(_listed(build("readonly"))) == set(lt.READ_ONLY_TOOLS)


@pytest.mark.parametrize("namespace", ["", " ", ",", None])
def test_an_empty_namespace_filter_means_no_filter(bridge, monkeypatch, namespace):
    monkeypatch.setenv("ISAAC_MCP_BUILTINS", "0")
    if namespace is None:
        monkeypatch.delenv("ISAAC_MCP_NAMESPACE", raising=False)
    else:
        monkeypatch.setenv("ISAAC_MCP_NAMESPACE", namespace)
    assert {"demo__ping", "other__pong"} <= set(_listed(build("readonly")))


def test_either_bridge_filter_alone_leaves_bridge_tools(bridge, monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_BUILTINS", "0")
    assert {"demo__ping", "other__pong"} <= set(_listed(build("readonly")))
    monkeypatch.delenv("ISAAC_MCP_BUILTINS")
    monkeypatch.setenv("ISAAC_MCP_NAMESPACE", "no-such-namespace")
    assert "run_python" in set(_listed(build("readonly")))

"""Offline UR5e tool discovery: the host lists all 23 ``ur5e_dt__*`` tools with no bridge running.

A server built by ``build_server`` registers callable proxies for every tool in the packaged
catalog (``isaac_mcp/catalogs/ur5e_dt.json``). A call dials the selected bridge and, when there
is none or it lacks the extension, answers with an actionable error. A live bridge's own
``list_tools`` metadata replaces the proxy; the filters, hooks, per-server isolation and the
``refresh_tools`` / ``use_bridge`` semantics are unchanged. Nothing here starts a simulator
(``subprocess.Popen`` is made to fail), and every bridge is a fake or an in-process real socket.
"""

import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
import mcp.types as types

import isaac_mcp.screen as screen
import isaac_mcp.server as gw
from isaac_mcp import host_catalog, local_tools as lt
from isaac_mcp_bridge.bridge import ToolDefinition
from isaac_mcp_bridge.schema import field_specs
from test_bridge import LiveBridge
from test_twin_shared_bridge import OWNER, attach, twin_registry

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("gen_host_catalog", ROOT / "scripts" / "gen_host_catalog.py")
generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generator)

PROXIES = {gw._mcp_name(canonical): canonical for canonical in host_catalog.catalog_tool_names()}
PLAY = "ur5e_dt__play_scene"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """No ambient bridge, filter or policy; capture dirs in tmp; and no way to spawn a process."""
    for var in ("ISAAC_MCP_NAMESPACE", "ISAAC_MCP_BUILTINS", "ISAAC_MCP_PORT", "UR5E_DT_PORT",
                "ISAAC_MCP_HOST", lt.ENV_VAR):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(screen, "SHOT_DIR", tmp_path / "shots")
    monkeypatch.setattr(screen, "VIDEO_DIR", tmp_path / "videos")
    monkeypatch.setattr(gw, "_PRE_DISPATCH_HOOKS", [])
    monkeypatch.setattr(gw, "_POST_DISPATCH_HOOKS", [])
    monkeypatch.setattr(gw, "_META_CATEGORY_HOOKS", [])

    def no_spawn(*args, **kwargs):
        raise AssertionError(f"the host started a process: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", no_spawn)


class Twin:
    """Every tool answers with its own name and the arguments it received."""

    def __getattr__(self, name):
        if name.startswith("_cmd_"):
            return lambda **kwargs: {"handler": name[5:], "args": kwargs}
        raise AttributeError(name)


def attach_twin(live, registry=None):
    attach(live.bridge, registry or twin_registry(), Twin(), ToolDefinition, requested_port=live.port)


def build(env=None):
    return gw.build_server({} if env is None else env)


def session(server, notes=None):
    async def handler(message):
        if notes is not None and isinstance(message, types.ServerNotification) and \
                isinstance(message.root, types.ToolListChangedNotification):
            notes.append(message)
    return create_connected_server_and_client_session(server._mcp_server, message_handler=handler)


def run(server, *steps, notes=None):
    """Run async steps (client -> value) in one session, return their values."""
    async def go():
        async with session(server, notes) as client:
            await client.initialize()
            return [await step(client) for step in steps]
    return asyncio.run(go())


async def tools(client):
    return {t.name: t for t in (await client.list_tools()).tools}


def call(name, args=None):
    async def step(client):
        result = await client.call_tool(name, args or {})
        return result.content[0].text
    return step


def enum_of(prop):
    """The enum of a property schema (optional enums render as anyOf [enum, null])."""
    return prop.get("enum") or next(o["enum"] for o in prop.get("anyOf", []) if "enum" in o)


def live_twin_tools(server):
    return [name for name in gw.registered_tools(server) if name.startswith("ur5e_dt.")]


# ---- the snapshot ----------------------------------------------------------------------

def test_the_snapshot_is_packaged_valid_and_complete():
    assert len(PROXIES) == 23
    catalog = host_catalog.load_catalog()
    assert len(catalog) == 23 and all(name.startswith("ur5e_dt.") for name in catalog)
    assert {meta["owner_id"] for meta in catalog.values()} == {OWNER}
    # It is data inside the package, read through importlib.resources (what a wheel ships).
    from importlib import resources
    assert resources.files("isaac_mcp").joinpath("catalogs/ur5e_dt.json").is_file()
    # A caller cannot corrupt the cache.
    catalog["ur5e_dt.play_scene"]["description"] = "tampered"
    assert host_catalog.load_catalog()["ur5e_dt.play_scene"]["description"] != "tampered"


def test_the_snapshot_matches_the_extension_registry_exactly():
    """Drift guard: a registry edit without a regenerated snapshot fails here."""
    registry = twin_registry()
    assert len(registry) == 23
    expected = {f"ur5e_dt.{name}": {
        "description": schema["description"],
        "parameters": schema.get("parameters", {}),
        "fields": field_specs(schema.get("parameters", {})),
        "namespace": "ur5e_dt", "owner_id": OWNER} for name, schema in registry.items()}
    assert host_catalog.load_catalog() == expected
    assert list(host_catalog.load_catalog()) == list(expected)          # declaration order too
    # The committed bytes are what the generator writes, and its --check agrees.
    path = ROOT / "mcp/isaac_mcp/catalogs/ur5e_dt.json"
    assert path.read_text(encoding="utf-8") == generator.render(generator.build_ur5e_catalog())
    assert generator.main(["--check"]) == 0


def test_the_generator_check_fails_on_drift(tmp_path, capsys):
    stale = tmp_path / "ur5e_dt.json"
    document = generator.build_ur5e_catalog()
    document["tools"]["ur5e_dt.play_scene"]["description"] = "an old description"
    stale.write_text(generator.render(document), encoding="utf-8")
    assert generator.main(["--check", "--output", str(stale)]) == 1
    assert "stale" in capsys.readouterr().err
    assert generator.main(["--write", "--output", str(stale)]) == 0
    assert generator.main(["--check", "--output", str(stale)]) == 0


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.update(version=99), "version"),
    (lambda d: d["tools"]["ur5e_dt.play_scene"].update(namespace="other"), "namespace"),
    (lambda d: d["tools"]["ur5e_dt.assemble_objects"]["fields"][0].update(kind="blob"), "malformed"),
    (lambda d: d["tools"]["ur5e_dt.assemble_objects"]["fields"][0].update(name="renamed"), "do not match"),
    (lambda d: d["tools"].update({"elsewhere.tool": d["tools"]["ur5e_dt.play_scene"]}), "namespace"),
])
def test_a_malformed_catalog_is_refused_loudly(monkeypatch, tmp_path, mutate, message):
    document = generator.build_ur5e_catalog()
    mutate(document)
    directory = tmp_path / "catalogs"
    directory.mkdir()
    (directory / "bad.json").write_text(json.dumps(document), encoding="utf-8")

    class Files:
        def joinpath(self, name):
            assert name == host_catalog.CATALOG_DIR
            return directory

    monkeypatch.setattr(host_catalog.resources, "files", lambda package: Files())
    with pytest.raises(host_catalog.CatalogError, match=message):
        host_catalog._read_all()


def test_a_missing_catalog_is_an_error_not_an_empty_one(monkeypatch, tmp_path):
    class Files:
        def joinpath(self, name):
            return tmp_path / "nowhere"

    monkeypatch.setattr(host_catalog.resources, "files", lambda package: Files())
    with pytest.raises(host_catalog.CatalogError, match="no packaged tool catalog"):
        host_catalog._read_all()


# ---- offline discovery and schema -------------------------------------------------------

def test_every_tool_is_listed_with_no_bridge_and_matches_the_registry_schema():
    server = build()
    (listed,) = run(server, tools)
    assert {name for name in listed if name.startswith("ur5e_dt__")} == set(PROXIES)
    registry = twin_registry()
    for name, canonical in PROXIES.items():
        short = canonical.split(".", 1)[1]
        fields = field_specs(registry[short].get("parameters", {}))
        tool = listed[name]
        assert tool.description == registry[short]["description"]
        properties = tool.inputSchema.get("properties", {})
        assert list(properties) == [f["name"] for f in sorted(
            fields, key=lambda f: not (f["required"] and not f["has_default"]))]
        assert set(tool.inputSchema.get("required", [])) == {
            f["name"] for f in fields if f["required"] and not f["has_default"]}
        for f in fields:
            if f["kind"] == "enum":
                assert enum_of(properties[f["name"]]) == f["enum"], (name, f["name"])
    assert listed[PLAY].inputSchema.get("properties", {}) == {}
    assert enum_of(listed["ur5e_dt__assemble_objects"].inputSchema["properties"]["assembly"]) == [
        "fmb1", "fmb2", "fmb3"]
    assert gw.offline_tools(server) == sorted(PROXIES.values())
    assert live_twin_tools(server) == []              # the roster of live bridge tools stays empty


def test_the_default_server_lists_them_too():
    assert set(PROXIES) <= set(gw.mcp._tool_manager._tools)


def test_offline_tools_defaults_to_the_default_server_and_tracks_its_live_bridge(monkeypatch):
    default = build()
    other = build()
    monkeypatch.setattr(gw, "mcp", default)
    assert gw.offline_tools() == gw.offline_tools(None) == sorted(PROXIES.values())
    with LiveBridge() as live:
        attach_twin(live)
        monkeypatch.setenv("ISAAC_MCP_PORT", str(live.port))
        gw.discover_and_register_tools(default)
        assert gw.offline_tools() == []
        assert gw.offline_tools(other) == sorted(PROXIES.values())


def test_a_client_that_never_refreshes_still_sees_them_after_a_failed_startup_discovery():
    # Opening a session runs the lifespan, whose discovery fails (no bridge): the proxies remain.
    (listed,) = run(build(), tools)
    assert set(PROXIES) <= set(listed)


@pytest.mark.parametrize("groups", ["all", "readonly", "none", "gateway", "screen_read", "isaaclab_run"])
def test_local_tool_groups_filter_only_local_tools(groups):
    server = build({lt.ENV_VAR: groups})
    assert set(PROXIES) <= set(server._tool_manager._tools)
    assert set(server._tool_manager._tools) - set(PROXIES) <= set(lt.ALL_TOOLS)


def test_every_offline_proxy_answers_with_an_actionable_error_and_starts_nothing():
    server = build()
    registry = twin_registry()
    values = {"boolean": True, "integer": 1, "number": 1.0, "array": [], "object": {}, "string": "x"}

    def args(canonical):
        out = {}
        for f in field_specs(registry[canonical.split(".", 1)[1]].get("parameters", {})):
            if f["required"] and not f["has_default"]:
                out[f["name"]] = f["enum"][0] if f["kind"] == "enum" else values[f["kind"]]
        return out

    answers = run(server, *[call(name, args(canonical)) for name, canonical in PROXIES.items()])
    for (name, canonical), answer in zip(PROXIES.items(), answers):
        assert answer.startswith(f"Error: {canonical} is not available"), (name, answer)
        assert "no Isaac Sim bridge could be reached" in answer
        for hint in ("--list-bridges", "use_bridge", "refresh_tools", "./run.sh --headless",
                     "never starts a simulator"):
            assert hint in answer, (name, hint)


def test_a_failure_after_connecting_is_not_reported_as_not_run(monkeypatch):
    """A connection lost mid-call (or a timeout) may have run the tool: keep the original error,
    never claim the call did not happen."""
    for error in (ConnectionError("bridge closed the connection mid-response"),
                  BrokenPipeError("broken pipe"), TimeoutError("no response within 300s")):
        class Dies:
            def call(self, tool, params=None, **kw):
                raise error

        server = build()
        gw._state_for(server).client = Dies()
        answer = gw._tool_implementation("ur5e_dt.play_scene", {}, gw._state_for(server))
        assert answer == f"Error: {error}", answer


def test_refused_retry_does_not_claim_the_first_attempt_never_ran(monkeypatch):
    """A request can be sent before a failed response causes a refused reconnect."""
    client = gw.BridgeClient(port=8766)
    attempts = []

    def connect():
        attempts.append("connect")
        if len(attempts) > 1:
            raise ConnectionRefusedError("bridge stopped after the first request")

    def roundtrip(*args):
        attempts.append("request_sent")
        raise ConnectionResetError("response lost")

    monkeypatch.setattr(client, "connect", connect)
    monkeypatch.setattr(client, "_roundtrip", roundtrip)
    server = build()
    gw._state_for(server).client = client
    answer = gw._tool_implementation("ur5e_dt.play_scene", {}, gw._state_for(server))
    assert attempts == ["connect", "request_sent", "connect"]
    assert "may have run" in answer and "was not run" not in answer


def test_several_bridges_and_no_choice_points_at_use_bridge(monkeypatch, tmp_path):
    import isaac_mcp.client as client_module
    monkeypatch.setattr(client_module, "discover_bridges", lambda: [
        {"pid": 1, "port": 8001, "app": "a"}, {"pid": 2, "port": 8002, "app": "b"}])
    (answer,) = run(build(), call(PLAY))
    assert "multiple bridges running" in answer and "use_bridge(port)" in answer


# ---- routing to a live bridge ------------------------------------------------------------

def test_a_proxy_call_reaches_a_bridge_that_appeared_after_startup(monkeypatch):
    server = build()
    with LiveBridge() as live:
        attach_twin(live)
        monkeypatch.setenv("ISAAC_MCP_PORT", str(live.port))
        play, assembled = run(
            server, call(PLAY),                                   # no refresh_tools first
            call("ur5e_dt__assemble_objects", {"assembly": "fmb2"}))
    assert json.loads(play) == {"handler": "play_scene", "args": {}}
    assert json.loads(assembled) == {"handler": "assemble_objects", "args": {"assembly": "fmb2"}}


def test_refresh_makes_the_tools_live_without_telling_the_client_nothing_changed(monkeypatch):
    server = build()
    notes = []
    with LiveBridge() as live:
        attach_twin(live)
        monkeypatch.setenv("ISAAC_MCP_PORT", str(live.port))
        before, refreshed, after, again = run(
            server, tools, call("refresh_tools"), tools, call("refresh_tools"), notes=notes)
    assert "No new or changed tools" in refreshed and "Note:" not in refreshed
    assert not notes                                     # the listing a client has did not change
    assert set(PROXIES) <= set(before) and {k: v.inputSchema for k, v in before.items() if k in PROXIES} == {
        k: v.inputSchema for k, v in after.items() if k in PROXIES}
    assert live_twin_tools(server) == sorted(PROXIES.values())     # now live, not proxies
    assert gw.offline_tools(server) == []
    assert "No new or changed tools" in again


def test_the_live_bridges_metadata_is_authoritative(monkeypatch):
    server = build()
    registry = twin_registry()
    registry["play_scene"] = {"description": "LIVE play", "parameters": {
        "speed": {"type": "number", "required": True}}}
    registry["brand_new"] = {"description": "only on this bridge", "parameters": {}}
    with LiveBridge() as live:
        attach_twin(live, registry)
        monkeypatch.setenv("ISAAC_MCP_PORT", str(live.port))
        changed = gw.discover_and_register_tools(server)
        notes = []
        listed, played = run(server, tools, call(PLAY, {"speed": 2.5}), notes=notes)
        refreshed, = run(server, call("refresh_tools"))
    assert "ur5e_dt.play_scene (updated)" in changed and "ur5e_dt.brand_new" in changed
    assert sorted(c for c in changed if c.startswith("ur5e_dt.")) == [   # the other 22 are unchanged
        "ur5e_dt.brand_new", "ur5e_dt.play_scene (updated)"]
    assert "No new or changed tools" in refreshed
    assert listed[PLAY].description == "LIVE play"
    assert listed[PLAY].inputSchema["required"] == ["speed"]
    assert "ur5e_dt__brand_new" in listed
    assert json.loads(played)["args"] == {"speed": 2.5}


def test_a_bridge_that_drops_the_extension_reverts_tools_to_their_proxies(monkeypatch):
    server = build()
    with LiveBridge() as live:
        attach_twin(live, {**twin_registry(), "play_scene": {"description": "LIVE", "parameters": {}},
                           "brand_new": {"description": "extra", "parameters": {}}})
        monkeypatch.setenv("ISAAC_MCP_PORT", str(live.port))
        gw.discover_and_register_tools(server)
        assert gw.offline_tools(server) == [] and "ur5e_dt.brand_new" in live_twin_tools(server)
        live.bridge.unregister_owner(OWNER)               # the twin extension is unloaded
        changed = gw.discover_and_register_tools(server)
        listed, answer = run(server, tools, call(PLAY))
    assert sorted(changed) == ["ur5e_dt.brand_new (removed)", "ur5e_dt.play_scene (offline)"]
    assert listed[PLAY].description == twin_registry()["play_scene"]["description"]
    assert set(PROXIES) <= set(listed) and "ur5e_dt__brand_new" not in listed
    assert gw.offline_tools(server) == sorted(PROXIES.values())
    assert "did not register it" in answer and "extension" in answer


# ---- disconnection and switches ----------------------------------------------------------

def test_calls_after_the_bridge_stops_say_so(monkeypatch):
    server = build()
    with LiveBridge() as live:
        attach_twin(live)
        monkeypatch.setenv("ISAAC_MCP_PORT", str(live.port))
        ok, = run(server, call("refresh_tools"))
        live_answer, = run(server, call(PLAY))
    assert json.loads(live_answer)["handler"] == "play_scene"
    # The bridge is gone. The tools stay listed, a refresh reports the failure and changes nothing.
    stopped, refreshed, listed = run(server, call(PLAY), call("refresh_tools"), tools)
    assert "no Isaac Sim bridge could be reached" in stopped
    assert refreshed.startswith("Error:")
    assert set(PROXIES) <= set(listed)


def test_use_bridge_to_a_bridge_without_the_extension_keeps_every_proxy():
    server = build()
    with LiveBridge() as other:
        other.bridge.register_owner("sim", "sim").add(ToolDefinition(
            name="inspect", description="inspect", parameters={}, handler=lambda: {"seen": True}))
        switched, listed, play, inspect = run(
            server, call("use_bridge", {"port": other.port}), tools, call(PLAY),
            call("sim__inspect"))
    assert "switched to bridge" in switched
    assert "Note: ur5e_dt: 23 tool(s) are listed from the packaged catalog" in switched
    assert set(PROXIES) <= set(listed) and "sim__inspect" in listed
    assert "did not register it" in play and f"port {other.port}" in play
    assert json.loads(inspect) == {"seen": True}
    assert gw.offline_tools(server) == sorted(PROXIES.values())


def test_switching_between_a_twin_bridge_and_a_plain_one_follows_the_selected_bridge():
    server = build()
    with LiveBridge() as twin, LiveBridge() as plain:
        attach_twin(twin)
        plain.bridge.register_owner("sim", "sim").add(ToolDefinition(
            name="inspect", description="inspect", parameters={}, handler=lambda: {}))
        notes = []
        to_twin, on_twin, to_plain, on_plain, back, again = run(
            server, call("use_bridge", {"port": twin.port}), call(PLAY),
            call("use_bridge", {"port": plain.port}), call(PLAY),
            call("use_bridge", {"port": twin.port}), call(PLAY), notes=notes)
        assert "Note:" not in to_twin and "Note:" not in back
        assert json.loads(on_twin)["handler"] == "play_scene"
        assert "did not register it" in on_plain
        assert json.loads(again)["handler"] == "play_scene"
        assert gw.offline_tools(server) == []
    assert notes                                          # each switch told the client


def test_a_failed_switch_target_changes_nothing():
    server = build()
    (answer,) = run(server, call("use_bridge", {"port": 1}))
    assert answer.startswith("Error:")
    assert gw.offline_tools(server) == sorted(PROXIES.values())


def test_use_bridge_whose_discovery_fails_still_leaves_the_proxies(monkeypatch):
    server = build()

    class Broken:
        port = 9
        def call(self, tool, params=None, **kw):
            if tool == "status":
                return {"pid": 1}
            raise OSError("bridge died mid-discovery")
        def close(self):
            pass

    monkeypatch.setattr(gw, "BridgeClient", lambda **kw: Broken())
    switched, listed = run(server, call("use_bridge", {"port": 9}), tools)
    assert "tool discovery failed" in switched
    assert set(PROXIES) <= set(listed)


def test_servers_keep_independent_bridges_and_independent_proxies(monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_PORT", "1")           # b's own choice: nothing listens there
    a, b = build(), build()
    with LiveBridge() as twin:
        attach_twin(twin)
        # One session: closing it drops the connection, and a server's choice lasts as long as it.
        _, on_a = run(a, call("use_bridge", {"port": twin.port}), call(PLAY))
        assert gw.offline_tools(a) == [] and gw.offline_tools(b) == sorted(PROXIES.values())
        (on_b,) = run(b, call(PLAY))
    assert json.loads(on_a)["handler"] == "play_scene"
    assert "not available" in on_b
    assert gw._state_for(a) is not gw._state_for(b)


def test_bare_fastmcp_servers_do_not_grow_proxies(monkeypatch):
    monkeypatch.setattr(gw, "_NAME_MAP", {})
    monkeypatch.setattr(gw, "_REGISTERED_META", {})

    class Fake:
        def call(self, tool, params=None, **kw):
            return {"tools": {}}

    monkeypatch.setattr(gw, "get_client", lambda: Fake())
    bare = FastMCP("bare")
    assert gw.discover_and_register_tools(bare) == []
    assert not bare._tool_manager._tools and gw.offline_tools(bare) == []


# ---- filters, hooks, collisions ----------------------------------------------------------

def test_the_namespace_filter_applies_to_proxies(monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_NAMESPACE", "pm")
    server = build()
    assert not set(PROXIES) & set(server._tool_manager._tools)
    assert gw.offline_tools(server) == []
    monkeypatch.setenv("ISAAC_MCP_NAMESPACE", "pm,ur5e_dt")
    assert set(PROXIES) <= set(build()._tool_manager._tools)
    monkeypatch.setenv("ISAAC_MCP_NAMESPACE", "ur5e_dt")
    assert set(PROXIES) <= set(build()._tool_manager._tools)


def test_a_namespace_filter_that_hides_ur5e_keeps_other_tools_and_survives_switches(monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_NAMESPACE", "sim")
    monkeypatch.setenv("ISAAC_MCP_BUILTINS", "0")
    server = build()
    with LiveBridge() as live:
        attach_twin(live)
        live.bridge.register_owner("sim", "sim").add(ToolDefinition(
            name="inspect", description="inspect", parameters={}, handler=lambda: {"ok": 1}))
        run(server, call("use_bridge", {"port": live.port}))
        (listed,) = run(server, tools)
    assert "sim__inspect" in listed and not set(PROXIES) & set(listed)
    assert gw.registered_tools(server) == ["sim.inspect"] and gw.offline_tools(server) == []


def test_changing_the_filter_is_applied_at_the_next_switch(monkeypatch):
    server = build()
    assert gw.offline_tools(server)
    monkeypatch.setenv("ISAAC_MCP_NAMESPACE", "sim")
    with LiveBridge() as live:
        live.bridge.register_owner("sim", "sim").add(ToolDefinition(
            name="inspect", description="inspect", parameters={}, handler=lambda: {}))
        run(server, call("use_bridge", {"port": live.port}))
        (listed,) = run(server, tools)
    assert not set(PROXIES) & set(listed) and gw.offline_tools(server) == []


def test_the_builtins_gate_does_not_hide_catalog_tools(monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_BUILTINS", "0")
    server = build()
    assert set(PROXIES) <= set(server._tool_manager._tools)
    with LiveBridge() as live:
        attach_twin(live)
        monkeypatch.setenv("ISAAC_MCP_PORT", str(live.port))
        refreshed, listed = run(server, call("refresh_tools"), tools)
    assert "run_python" not in listed and set(PROXIES) <= set(listed)


def test_pre_dispatch_hooks_police_offline_proxies(monkeypatch):
    seen = []
    gw.register_pre_dispatch_hook(lambda name, params: seen.append((name, dict(params))) or (
        "refused by policy" if name == "ur5e_dt.stop_scene" else None))
    server = build()
    allowed, refused = run(server, call(PLAY), call("ur5e_dt__stop_scene"))
    assert "not available" in allowed                    # the policy allowed it; no bridge answered
    assert refused == "refused by policy"
    assert [name for name, _ in seen] == ["ur5e_dt.play_scene", "ur5e_dt.stop_scene"]


def test_post_dispatch_hooks_see_the_offline_error(monkeypatch):
    seen = []
    gw.register_post_dispatch_hook(lambda name, params, result: seen.append((name, result)) or "[annotated] " + result)
    (answer,) = run(build(), call(PLAY))
    assert answer.startswith("[annotated] Error: ur5e_dt.play_scene is not available")
    assert seen[0][0] == "ur5e_dt.play_scene" and "no Isaac Sim bridge" in seen[0][1]


def test_meta_category_hooks_label_proxies_including_hooks_added_after_build():
    server = build()                                     # built before any policy exists
    gw.register_meta_category_hook(lambda name: "twin" if name.startswith("ur5e_dt.") else None)
    (listed,) = run(server, tools)                       # the session's startup syncs the proxies
    assert listed[PLAY].meta == {"category": "twin"}
    assert gw.build_server({})._tool_manager._tools[PLAY].meta == {"category": "twin"}


def test_a_live_tool_may_not_take_an_offline_proxys_name_under_another_canonical(monkeypatch):
    server = build()
    state = gw._state_for(server)
    clash = {"description": "d", "parameters": {}, "fields": [], "namespace": "x", "owner_id": "B"}

    class Fake:
        def call(self, tool, params=None, **kw):
            return {"tools": {"ur5e_dt__play_scene": clash}}   # same MCP name, other canonical

    state.client = Fake()
    with pytest.raises(RuntimeError, match="collides with host tool"):
        gw.discover_and_register_tools(server)
    assert gw.offline_tools(server) == sorted(PROXIES.values())     # nothing was lost


def test_a_catalog_name_that_collides_with_a_host_tool_fails_the_build(monkeypatch):
    meta = {"description": "d", "parameters": {}, "fields": [], "namespace": "isaaclab",
            "owner_id": "o"}
    monkeypatch.setattr(host_catalog, "load_catalog", lambda: {"isaaclab.install_info": meta})
    with pytest.raises(RuntimeError, match="collides with host tool 'isaaclab__install_info'"):
        build()


def test_the_host_tool_collision_check_still_protects_local_tools(monkeypatch):
    server = build()
    state = gw._state_for(server)
    meta = {"description": "d", "parameters": {}, "fields": [], "namespace": "isaaclab",
            "owner_id": "B"}

    class Fake:
        def call(self, tool, params=None, **kw):
            return {"tools": {"isaaclab.install_info": meta}}

    state.client = Fake()
    with pytest.raises(RuntimeError, match="collides with host tool"):
        gw.discover_and_register_tools(server)


def test_the_offline_proxies_carry_no_local_tool_groups_or_annotations():
    server = build({lt.ENV_VAR: "none"})
    tool = server._tool_manager._tools[PLAY]
    assert tool.annotations is None                       # bridge tools are not annotated


def test_instructions_say_the_tools_are_always_listed():
    assert "listed by default" in gw.SERVER_INSTRUCTIONS and "ur5e_dt__" in gw.SERVER_INSTRUCTIONS


def test_the_wheel_config_ships_the_whole_package_directory():
    """hatch's `packages = ["isaac_mcp"]` takes every file in the package, the catalog JSON
    included (verified with `uv build`); an include/exclude rule here could silently drop it."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib
    with open(ROOT / "mcp/pyproject.toml", "rb") as handle:
        targets = tomllib.load(handle)["tool"]["hatch"]["build"]["targets"]
    assert targets["wheel"]["packages"] == ["isaac_mcp"]
    for target in targets.values():
        assert not {"exclude", "only-include", "only-packages"} & set(target)
    from importlib import resources
    packaged = resources.files("isaac_mcp").joinpath(host_catalog.CATALOG_DIR)
    assert [entry.name for entry in packaged.iterdir() if entry.name.endswith(".json")] == ["ur5e_dt.json"]

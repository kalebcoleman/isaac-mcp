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
import os
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
import mcp.types as types

import isaac_mcp.screen as screen
import isaac_mcp.server as gw
from isaac_mcp import host_catalog, local_tools as lt, sim_sessions
from isaac_mcp_bridge.bridge import ToolDefinition
from isaac_mcp_bridge.schema import field_specs
from test_bridge import LiveBridge
from test_twin_shared_bridge import OWNER, attach, twin_registry

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("gen_host_catalog", ROOT / "scripts" / "gen_host_catalog.py")
generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generator)

PROXIES = {gw._mcp_name(canonical): canonical for canonical in host_catalog.catalog_tool_names() if canonical.startswith("ur5e_dt.")}
PLAY = "ur5e_dt__play_scene"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """No ambient bridge, filter or policy; capture dirs in tmp; and no way to spawn a process."""
    # This regression module exercises the existing UR5e-only bridge/catalog surface.
    # The full multi-catalog surface is covered in test_scene_gateway.py.
    full_catalog = host_catalog.load_catalog()
    monkeypatch.setattr(host_catalog, "load_catalog", lambda: __import__("copy").deepcopy({k:v for k,v in full_catalog.items() if k.startswith("ur5e_dt.")}))
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


# These tests start fake bridges. A real managed simulator on the machine (its session record under the user's data folder)
# made refresh_tools add "a managed simulator is also running" and fail them, so every server looks in an empty folder.
_NO_REAL_SESSIONS = {"ISAAC_MCP_SIM_SESSIONS_DIR": tempfile.mkdtemp(prefix="isaac-mcp-no-sessions-")}


def build(env=None):
    return gw.build_server({**_NO_REAL_SESSIONS, **({} if env is None else env)})


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
        "fmb1", "fmb2", "fmb3", "fmb4", "jenga", "fmb_jenga"]
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

    def connect(timeout=None):
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


@pytest.mark.parametrize("action", ["refresh_tools", "use_bridge"])
@pytest.mark.parametrize("one_live_tool", [False, True])
def test_filter_changes_notify_for_catalog_tools(monkeypatch, action, one_live_tool):
    class EmptyBridge:
        port = 9

        def call(self, tool, params=None, **kwargs):
            if tool == "list_tools":
                catalog = host_catalog.load_catalog()
                return {"tools": {"ur5e_dt.play_scene": catalog["ur5e_dt.play_scene"]} if one_live_tool else {}}
            return {"pid": 1, "port": self.port}

        def close(self):
            pass

    monkeypatch.setattr(gw, "BridgeClient", lambda **kwargs: EmptyBridge())
    server, notes = build(), []
    args = {"port": 9} if action == "use_bridge" else {}

    async def go():
        async with session(server, notes) as client:
            await client.initialize()
            before = await tools(client)
            monkeypatch.setenv("ISAAC_MCP_NAMESPACE", "sim")
            hidden = await call(action, args)(client)
            hidden_names, hidden_notes = await tools(client), len(notes)
            again = await call(action, args)(client)
            unchanged_notes = len(notes)
            monkeypatch.delenv("ISAAC_MCP_NAMESPACE")
            shown = await call(action, args)(client)
            return before, hidden, hidden_names, hidden_notes, again, unchanged_notes, shown, await tools(client)

    before, hidden, hidden_names, hidden_notes, again, unchanged_notes, shown, after = asyncio.run(go())
    assert set(PROXIES) <= set(before) and not set(PROXIES) & set(hidden_names)
    assert set(PROXIES) <= set(after)
    assert "23 tool(s)" in hidden and "23 tool(s)" in shown
    assert "0 tool(s)" in again or "registry unchanged" in again
    if action == "refresh_tools":
        assert all(f"{canonical} (removed)" in hidden for canonical in PROXIES.values())
        assert all(canonical in shown for canonical in PROXIES.values())
    assert (hidden_notes, unchanged_notes, len(notes)) == (1, 1, 2)


def test_switch_to_identical_client_surface_does_not_notify(monkeypatch):
    class Bridge:
        def __init__(self, port=None, **kwargs):
            self.port = port

        def call(self, tool, params=None, **kwargs):
            if tool == "status":
                return {"pid": 1, "port": self.port}
            catalog = host_catalog.load_catalog()
            return {"tools": {"ur5e_dt.play_scene": catalog["ur5e_dt.play_scene"]}}

        def close(self):
            pass

    monkeypatch.setattr(gw, "BridgeClient", Bridge)
    server, notes = build(), []
    async def go():
        async with session(server, notes) as client:
            await client.initialize()
            answer = await call("use_bridge", {"port": 9})(client)
            return answer, await tools(client), gw._state_for(server).client.port

    answer, listed, port = asyncio.run(go())
    assert "0 tool(s)" in answer and set(PROXIES) <= set(listed)
    assert port == 9 and not notes


def test_failed_switch_still_notifies_when_it_changes_offline_proxies(monkeypatch):
    class Bridge:
        def __init__(self, port=None, **kwargs):
            self.port = port

        def call(self, tool, params=None, **kwargs):
            if tool == "status":
                return {"pid": 1, "port": self.port}
            if self.port == 9:
                raise OSError("bridge died mid-discovery")
            return {"tools": {}}

        def close(self):
            pass

    monkeypatch.setattr(gw, "BridgeClient", Bridge)
    server, notes = build(), []

    async def go():
        async with session(server, notes) as client:
            await client.initialize()
            monkeypatch.setenv("ISAAC_MCP_NAMESPACE", "sim")
            answer = await call("use_bridge", {"port": 9})(client)
            return answer, await tools(client)

    answer, listed = asyncio.run(go())
    assert "tool discovery failed" in answer and "Changed 23 tool(s)" in answer
    assert not set(PROXIES) & set(listed) and len(notes) == 1


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


def test_refresh_notifies_when_an_offline_proxy_category_changes(monkeypatch):
    class EmptyBridge:
        def call(self, tool, params=None, **kwargs):
            return {"tools": {}}

        def close(self):
            pass

    monkeypatch.setattr(gw, "BridgeClient", lambda **kwargs: EmptyBridge())
    server, notes = build(), []

    async def go():
        async with session(server, notes) as client:
            await client.initialize()
            gw.register_meta_category_hook(lambda name: "twin" if name.startswith("ur5e_dt.") else None)
            first = await call("refresh_tools")(client)
            listed = await tools(client)
            second = await call("refresh_tools")(client)
            return first, listed, second

    first, listed, second = asyncio.run(go())
    assert "23 tool(s)" in first and "ur5e_dt.play_scene (updated)" in first
    assert listed[PLAY].meta == {"category": "twin"}
    assert "registry unchanged" in second and len(notes) == 1


def test_refresh_notifies_when_category_changes_across_live_proxy_transitions(monkeypatch):
    class Bridge:
        live = False

        def call(self, tool, params=None, **kwargs):
            catalog = host_catalog.load_catalog()
            return {"tools": {"ur5e_dt.play_scene": catalog["ur5e_dt.play_scene"]} if self.live else {}}

        def close(self):
            pass

    bridge = Bridge()
    monkeypatch.setattr(gw, "BridgeClient", lambda **kwargs: bridge)
    server, notes = build(), []

    async def go():
        async with session(server, notes) as client:
            await client.initialize()
            gw.register_meta_category_hook(lambda name: "policy" if name == "ur5e_dt.play_scene" else None)
            bridge.live = True
            became_live = await call("refresh_tools")(client)
            live_meta, live_notes = (await tools(client))[PLAY].meta, len(notes)
            gw._META_CATEGORY_HOOKS.clear()
            bridge.live = False
            became_offline = await call("refresh_tools")(client)
            offline_meta = (await tools(client))[PLAY].meta
            return became_live, live_meta, live_notes, became_offline, offline_meta

    became_live, live_meta, live_notes, became_offline, offline_meta = asyncio.run(go())
    assert "ur5e_dt.play_scene (updated)" in became_live and live_meta == {"category": "policy"}
    assert "ur5e_dt.play_scene (offline)" in became_offline and offline_meta is None
    assert (live_notes, len(notes)) == (1, 2)


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


def test_the_offline_proxies_carry_hints_from_their_own_table():
    server = build({lt.ENV_VAR: "none"})
    tool = server._tool_manager._tools[PLAY]
    assert tool.annotations.readOnlyHint is False and tool.annotations.destructiveHint is False


def test_a_tool_from_a_projects_own_extension_gets_no_hints():
    server = build({lt.ENV_VAR: "none"})
    gw.register_dynamic_tool(server, "demo__ping", "demo.ping", {"description": "d", "parameters": {}, "fields": []})
    assert server._tool_manager._tools["demo__ping"].annotations is None


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
    assert sorted(entry.name for entry in packaged.iterdir() if entry.name.endswith(".json")) == ["assets.json", "builtins.json", "sim.json", "ur5e_dt.json"]



@pytest.mark.parametrize("name", ["add_objects", "assemble_objects"])
@pytest.mark.parametrize("assembly", ["fmb4", "jenga", "fmb_jenga"])
def test_jenga_is_accepted_by_real_mcp_schema_and_forwarded(monkeypatch, name, assembly):
    """Regression: the handler supported Jenga while Pydantic rejected it."""
    with LiveBridge() as live:
        attach_twin(live)
        server = build({"ISAAC_MCP_PORT": str(live.port)})

        async def exercise(client):
            listed = await tools(client)
            schema = listed["ur5e_dt__" + name].inputSchema["properties"]["assembly"]
            assert assembly in enum_of(schema)
            response = await client.call_tool("ur5e_dt__" + name, {"assembly": assembly})
            assert not response.isError, response
            data = json.loads(response.content[0].text)
            assert data == {"handler": name, "args": {"assembly": assembly}}
            refused = await client.call_tool("ur5e_dt__" + name, {"assembly": "unknown-assembly"})
            assert refused.isError

        run(server, exercise)


# ---- refresh_tools(wait_s) waited on the pinned port, not sim__launch's ----------

def dead_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture()
def managed(monkeypatch, tmp_path):
    """A server with sim__launch whose launches are faked as session records of a live process."""
    env = {"ISAAC_MCP_LOCAL_TOOLS": "gateway,sim_launch",
           "ISAAC_MCP_SIM_SESSIONS_DIR": str(tmp_path / "sessions")}

    def write_session(port, pid=None):
        pid = os.getpid() if pid is None else pid
        record = {"id": uuid.uuid4().hex, "pid": pid, "port": port, "ros_domain": 9,
                  "start_ticks": sim_sessions._process_identity(pid)[1],
                  "boot_id": sim_sessions._boot_id(), "started_at": time.time()}
        directory = tmp_path / "sessions" / record["id"]
        directory.mkdir(parents=True)
        (directory / "session.json").write_text(json.dumps(record))
        return record

    pid_box = {}
    monkeypatch.setattr(sim_sessions, "_launch", lambda settings, port, *args: write_session(port, pid_box.get("pid")))
    server = gw.build_server(env)

    async def selected(client):
        return gw._state_for(server).get_client().port

    return SimpleNamespace(server=server, write_session=write_session, selected=selected, pid_box=pid_box,
                           launch=lambda port: call("sim__launch", {"port": port, "ros_domain": 9}),
                           state=gw._state_for(server))


def test_refresh_selects_the_launched_session_when_nothing_listens_on_the_pinned_port(managed, monkeypatch):
    pinned = dead_port()
    monkeypatch.setenv("UR5E_DT_PORT", str(pinned))
    notes = []
    with LiveBridge() as live:
        launched, message, port, status = run(
            managed.server, managed.launch(live.port), call("refresh_tools", {"wait_s": 5}),
            managed.selected, call("status"), notes=notes)
    session_id = json.loads(launched)["id"]
    assert port == live.port and json.loads(status)["port"] == live.port
    assert f"Selected the bridge on port {live.port} (PID {os.getpid()})" in message
    assert f"port {pinned}, which this server was using, did not answer" in message
    assert session_id in message and "launched by this server" in message
    assert f"switched to bridge on port {live.port}" in message   # the use_bridge path
    assert "Confirm the PID with status" in message
    assert notes, "the client must hear that the tool list changed"


def test_refresh_without_wait_also_selects_a_ready_launched_session(managed, monkeypatch):
    monkeypatch.setenv("UR5E_DT_PORT", str(dead_port()))
    with LiveBridge() as live:
        _, message, port = run(managed.server, managed.launch(live.port), call("refresh_tools"), managed.selected)
    assert port == live.port and "Selected the bridge" in message


def test_refresh_wait_polls_the_session_until_it_is_ready(managed, monkeypatch):
    pinned = dead_port()
    monkeypatch.setenv("UR5E_DT_PORT", str(pinned))
    monkeypatch.setattr(gw, "REFRESH_POLL_S", 0.01)
    real, checks = gw._session_not_ready, []

    def slow_boot(session):
        checks.append(session["port"])
        return "still booting" if len(checks) < 3 else real(session)

    monkeypatch.setattr(gw, "_session_not_ready", slow_boot)
    with LiveBridge() as live:
        _, message, port = run(managed.server, managed.launch(live.port),
                               call("refresh_tools", {"wait_s": 5}), managed.selected)
    assert port == live.port and checks == [live.port] * 3 and "Selected the bridge" in message


def test_refresh_wait_timeout_names_the_session_port_it_polled(managed, monkeypatch):
    pinned, session_port = dead_port(), dead_port()
    monkeypatch.setenv("UR5E_DT_PORT", str(pinned))
    monkeypatch.setattr(gw, "REFRESH_POLL_S", 0.01)
    monkeypatch.setattr(gw, "REFRESH_WAIT_MAX_S", 0.05)
    _, message, port = run(managed.server, managed.launch(session_port),
                           call("refresh_tools", {"wait_s": 5}), managed.selected)
    assert port == pinned and "waited 0 s" in message
    assert f"polls port {pinned} and port {session_port}" in message
    assert "polls for you" not in message
    assert f"port {session_port} (PID {os.getpid()}) is not answering yet" in message


def test_refresh_never_leaves_a_pinned_bridge_that_answers(managed, monkeypatch):
    monkeypatch.setattr(gw, "_session_not_ready", lambda session: pytest.fail("polled away from a live pin"))
    with LiveBridge() as pinned, LiveBridge() as launched:
        monkeypatch.setenv("UR5E_DT_PORT", str(pinned.port))
        _, message, port = run(managed.server, managed.launch(launched.port),
                               call("refresh_tools", {"wait_s": 5}), managed.selected)
    assert port == pinned.port and "Selected the bridge" not in message
    assert f"stayed on port {pinned.port}, whose bridge answered" in message
    assert f"port {launched.port} (PID {os.getpid()})" in message and "use_bridge(port)" in message


def test_refresh_does_not_leave_a_pinned_port_that_accepts_but_is_still_booting(managed, monkeypatch):
    monkeypatch.setattr(gw, "DISCOVERY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(gw, "_session_not_ready", lambda session: pytest.fail("left a booting pin"))
    with socket.socket() as booting, LiveBridge() as launched:
        booting.bind(("127.0.0.1", 0))
        booting.listen(8)
        pinned = booting.getsockname()[1]
        monkeypatch.setenv("UR5E_DT_PORT", str(pinned))
        _, message, port = run(managed.server, managed.launch(launched.port),
                               call("refresh_tools"), managed.selected)
    assert port == pinned and "accepted the connection" in message and "Selected" not in message
    assert "polls for you" not in message and f"use_bridge(port={launched.port})" in message


def test_refresh_does_not_guess_between_several_managed_sessions(managed, monkeypatch):
    pinned = dead_port()
    monkeypatch.setenv("UR5E_DT_PORT", str(pinned))
    monkeypatch.setattr(gw, "_session_not_ready", lambda session: pytest.fail("guessed"))
    with LiveBridge() as first, LiveBridge() as second:
        _, _, message, port = run(managed.server, managed.launch(first.port), managed.launch(second.port),
                                  call("refresh_tools", {"wait_s": 0}), managed.selected)
    assert port == pinned and "Selected the bridge" not in message
    assert f"port {first.port}" in message and f"port {second.port}" in message
    assert "will not guess" in message and "polls for you" not in message


def test_refresh_names_but_does_not_select_a_session_this_server_did_not_launch(managed, monkeypatch):
    # The session directory is shared with other hosts and agents.
    pinned = dead_port()
    monkeypatch.setenv("UR5E_DT_PORT", str(pinned))
    with LiveBridge() as foreign:
        managed.write_session(foreign.port)
        message, port = run(managed.server, call("refresh_tools", {"wait_s": 0}), managed.selected)
    assert port == pinned and "Selected the bridge" not in message
    assert f"port {foreign.port}" in message and f"use_bridge(port={foreign.port})" in message
    assert "polls for you" not in message and f"keeps dialling port {pinned}" in message


def test_refresh_refuses_a_session_port_answered_by_another_process(managed, monkeypatch):
    pinned = dead_port()
    monkeypatch.setenv("UR5E_DT_PORT", str(pinned))
    managed.pid_box["pid"] = os.getppid()
    with LiveBridge() as impostor:
        _, message, port = run(managed.server, managed.launch(impostor.port),
                               call("refresh_tools"), managed.selected)
    assert port == pinned and "Selected the bridge" not in message
    assert f"answered as PID {os.getpid()}, not the managed session's PID {os.getppid()}" in message


def test_refresh_waits_for_the_session_app_to_finish_loading(managed, monkeypatch):
    from isaac_mcp_bridge import builtins as bridge_builtins

    monkeypatch.setenv("UR5E_DT_PORT", str(dead_port()))
    monkeypatch.setattr(bridge_builtins, "_app_ready", lambda: False)

    async def app_ready(client):
        monkeypatch.setattr(bridge_builtins, "_app_ready", lambda: True)

    with LiveBridge() as live:
        _, loading, _, ready, port = run(managed.server, managed.launch(live.port), call("refresh_tools"),
                                         app_ready, call("refresh_tools"), managed.selected)
    assert "still loading" in loading and "Selected the bridge" not in loading
    assert port == live.port and "Selected the bridge" in ready


def test_a_session_on_the_selected_port_is_waited_for_not_replaced(managed, monkeypatch):
    # The pin itself is the launched session, still starting: there is nothing to switch to.
    session_port = dead_port()
    monkeypatch.setenv("UR5E_DT_PORT", str(session_port))
    _, message, port = run(managed.server, managed.launch(session_port), call("refresh_tools"), managed.selected)
    assert port == session_port and f"polls port {session_port} for you" in message


def test_a_remote_dial_host_is_not_redirected_to_a_local_session(managed, monkeypatch):
    with LiveBridge() as live:
        record = managed.write_session(live.port)
        managed.state.launched_sessions.add(record["id"])
        monkeypatch.setenv("UR5E_DT_PORT", str(dead_port()))
        assert gw._followable_session(managed.state)["port"] == live.port
        monkeypatch.setenv("ISAAC_MCP_HOST", "192.0.2.1")
        assert gw._followable_session(managed.state) is None


def test_refresh_with_no_pin_and_no_bridge_reports_it(monkeypatch, tmp_path):
    # client.port raises when discovery finds nothing; the hint must not turn that into a crash.
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    server = build({"ISAAC_MCP_LOCAL_TOOLS": "gateway", "ISAAC_MCP_SIM_SESSIONS_DIR": str(tmp_path / "none")})
    (message,) = run(server, call("refresh_tools"))
    assert message.startswith("Error: no Isaac Sim bridge is reachable") and "polls for you" in message


def test_refresh_hint_with_no_pin_names_only_the_session_port(managed, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    session_port = dead_port()
    _, message = run(managed.server, managed.launch(session_port), call("refresh_tools"))
    assert f"refresh_tools(wait_s=120) polls port {session_port}, and selects" in message
    assert "port None" not in message


def test_use_bridge_still_switches_to_any_port_and_keeps_its_result(managed, monkeypatch):
    monkeypatch.setenv("UR5E_DT_PORT", str(dead_port()))
    with LiveBridge() as live:
        message, port = run(managed.server, call("use_bridge", {"port": live.port}), managed.selected)
    assert message.startswith(f"switched to bridge on port {live.port}\n") and port == live.port
    assert "Selected the bridge" not in message


def test_launch_notes_no_longer_claim_refresh_cannot_switch(monkeypatch, tmp_path):
    record = {"id": "b" * 32, "pid": os.getpid(), "port": 18766, "ros_domain": 9,
              "start_ticks": sim_sessions._process_identity(os.getpid())[1], "boot_id": sim_sessions._boot_id()}
    directory = tmp_path / record["id"]
    directory.mkdir()
    (directory / "session.json").write_text(json.dumps(record))
    monkeypatch.setattr(sim_sessions.client, "call", lambda *a, **k: {"pid": os.getpid(), "app_ready": True})
    monkeypatch.setattr(sim_sessions, "_resources", lambda: {})
    status = sim_sessions._status({"ISAAC_MCP_SIM_SESSIONS_DIR": str(tmp_path)}, record["id"])
    assert "use_bridge(port=18766)" in status["next"] and "while that port answers" in status["next"]


# ---- status reports what the MCP host process is running -------------------------

COMMIT_A, COMMIT_B = "a" * 40, "b" * 40


def fake_checkout(root, head=f"ref: refs/heads/main", loose=COMMIT_A):
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(head + "\n")
    if loose:
        (git / "refs" / "heads" / "main").write_text(loose + "\n")
    return root


def test_read_git_head_handles_loose_packed_detached_and_worktree_layouts(tmp_path):
    loose = fake_checkout(tmp_path / "loose")
    assert gw._read_git_head(loose) == COMMIT_A
    detached = fake_checkout(tmp_path / "detached", head=COMMIT_B)
    assert gw._read_git_head(detached) == COMMIT_B
    packed = fake_checkout(tmp_path / "packed", loose=None)
    (packed / ".git" / "packed-refs").write_text(
        f"# pack-refs with: peeled\n{COMMIT_B} refs/heads/other\n{COMMIT_A} refs/heads/main\n")
    assert gw._read_git_head(packed) == COMMIT_A
    # A worktree or submodule: .git is a file, and refs live in the common directory.
    common = fake_checkout(tmp_path / "main")
    private = common / ".git" / "worktrees" / "w"
    private.mkdir(parents=True)
    (private / "HEAD").write_text("ref: refs/heads/main\n")
    (private / "commondir").write_text("../..\n")
    worktree = tmp_path / "w"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {private}\n")
    assert gw._read_git_head(worktree) == COMMIT_A


@pytest.mark.parametrize("head", ["garbage", "ref: refs/heads/missing"])
def test_read_git_head_reports_nothing_rather_than_guessing(tmp_path, head):
    assert gw._read_git_head(fake_checkout(tmp_path / "bad", head=head)) is None
    assert gw._read_git_head(tmp_path / "absent") is None
    assert gw._read_git_head(None) is None


def test_find_checkout_recognizes_a_source_tree_but_not_an_installed_copy(tmp_path):
    tree = fake_checkout(tmp_path / "tree")
    (tree / "mcp" / "isaac_mcp").mkdir(parents=True)
    (tree / "mcp" / "pyproject.toml").write_text("")
    assert gw._find_checkout(str(tree / "mcp" / "isaac_mcp" / "server.py")) == tree.resolve()
    installed = fake_checkout(tmp_path / "repo") / ".venv" / "lib" / "site-packages" / "isaac_mcp"
    installed.mkdir(parents=True)
    assert gw._find_checkout(str(installed / "server.py")) is None


def test_host_identity_warns_only_after_the_checkout_moves(tmp_path, monkeypatch):
    checkout = fake_checkout(tmp_path / "tree")
    monkeypatch.setattr(gw, "_HOST_CHECKOUT", checkout)
    monkeypatch.setattr(gw, "_HOST_COMMIT", COMMIT_A)
    identity = gw._host_identity()
    assert identity["commit"] == identity["checkout_commit"] == COMMIT_A
    assert identity["stale"] is False and "warning" not in identity and identity["pid"] == os.getpid()
    (checkout / ".git" / "refs" / "heads" / "main").write_text(COMMIT_B + "\n")   # a git pull
    identity = gw._host_identity()
    assert identity["commit"] == COMMIT_A and identity["checkout_commit"] == COMMIT_B and identity["stale"] is True
    assert COMMIT_A[:12] in identity["warning"] and COMMIT_B[:12] in identity["warning"]
    assert "Restart the MCP connection" in identity["warning"]
    (checkout / ".git" / "HEAD").write_text("garbage")                              # unreadable now
    identity = gw._host_identity()
    assert identity["checkout_commit"] == "unknown" and identity["stale"] is None and "warning" not in identity


def test_host_identity_of_an_installed_wheel_is_just_the_version(monkeypatch):
    monkeypatch.setattr(gw, "_HOST_CHECKOUT", None)
    monkeypatch.setattr(gw, "_HOST_COMMIT", None)
    monkeypatch.setattr(gw, "_HOST_VERSION", "9.9.9")
    assert gw._host_identity() == {"version": "9.9.9", "pid": os.getpid()}


def test_status_through_the_host_adds_host_and_keeps_every_bridge_field(monkeypatch, tmp_path):
    from isaac_mcp.client import call as direct_call

    checkout = fake_checkout(tmp_path / "tree")
    monkeypatch.setattr(gw, "_HOST_CHECKOUT", checkout)
    monkeypatch.setattr(gw, "_HOST_COMMIT", COMMIT_B)   # the server started before the pull
    with LiveBridge() as live:
        monkeypatch.setenv("ISAAC_MCP_PORT", str(live.port))
        direct = direct_call("status", port=live.port, timeout=5)
        assert "host" not in direct, "the bridge must not grow a field this would overwrite"
        answer, short = run(build(), call("status", {"detail": True}), call("status"))
    payload = json.loads(answer)
    host = payload.pop("host")
    assert set(payload) == set(direct) and payload["pid"] == direct["pid"] and payload["port"] == live.port
    assert host["stale"] is True and host["commit"] == COMMIT_B and "warning" in host
    assert str(tmp_path) not in answer, "status must not publish the checkout path"
    brief = json.loads(short)
    assert brief["running"] is True and brief["pid"] == direct["pid"] and brief["port"] == live.port
    assert not {"admission", "toolsets", "namespaces", "extension"} & set(brief), "the default reply is the short one"
    assert "warning" in brief["host"] and len(short) < len(answer) / 2


def test_status_with_no_bridge_still_reports_the_host(monkeypatch, tmp_path):
    monkeypatch.setenv("UR5E_DT_PORT", str(dead_port()))
    checkout = fake_checkout(tmp_path / "tree")
    monkeypatch.setattr(gw, "_HOST_CHECKOUT", checkout)
    monkeypatch.setattr(gw, "_HOST_COMMIT", COMMIT_B)
    state = gw._state_for(build({"ISAAC_MCP_SIM_SESSIONS_DIR": str(tmp_path / "none")}))
    answer = json.loads(gw._dispatch("status", {}, state))
    assert answer["running"] is False and "sim__launch" in answer["message"], "no bridge is an answer, not an error"
    assert answer["bridges"] == [] and "Restart the MCP connection" in answer["host"]["warning"]
    other = gw._dispatch("ur5e_dt.play_scene", {}, state)
    assert "MCP host:" not in other


# ---- help text must work on a stock python3 --------------------------------------

def test_bridge_listing_help_is_one_working_form(monkeypatch, tmp_path):
    bare = "python -m isaac_mcp.client"    # fails on a python3 without the package
    assert bare not in gw.LIST_BRIDGES_HELP
    assert "python3 mcp/isaac_mcp/client.py --list-bridges" in gw.LIST_BRIDGES_HELP
    assert (ROOT / "mcp" / "isaac_mcp" / "client.py").is_file()
    server = build({"ISAAC_MCP_LOCAL_TOOLS": "gateway", "ISAAC_MCP_SIM_SESSIONS_DIR": str(tmp_path / "none")})
    state = gw._state_for(server)
    listed = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    refused = ConnectionRefusedError("[Errno 111] Connection refused")
    messages = [gw._BRIDGE_HELP, gw.SERVER_INSTRUCTIONS, listed["use_bridge"].description,
                gw._catalog_error("status", refused, state), gw._catalog_error("ur5e_dt.play_scene", refused, state),
                gw._starting_hint(server, refused)]
    for message in messages:
        assert gw.LIST_BRIDGES_HELP in message and bare not in message
    # gen_tool_reference reads docstrings from the source, so use_bridge must keep one.
    reference_spec = importlib.util.spec_from_file_location("gen_tool_reference", ROOT / "scripts" / "gen_tool_reference.py")
    reference = importlib.util.module_from_spec(reference_spec)
    reference_spec.loader.exec_module(reference)
    summary = reference.local_tools(ROOT / "mcp" / "isaac_mcp" / "server.py")["use_bridge"][0]
    assert summary and listed["use_bridge"].description.startswith(summary)
    source = (ROOT / "mcp" / "isaac_mcp" / "server.py").read_text()
    assert source.count("--list-bridges`") == 2, "the two forms live only in LIST_BRIDGES_HELP"
    monkeypatch.setattr(gw, "discover_and_register_tools",
                        lambda s: (_ for _ in ()).throw(RuntimeError("multiple bridges running (a, b)")))
    several = asyncio.run(gw._refresh_tools(server))
    assert gw.LIST_BRIDGES_HELP in several and "use_bridge(port)" in several

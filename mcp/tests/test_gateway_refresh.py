"""Gateway rediscovery — schema changes on the bridge must propagate.

The staleness contract: refresh adds new tools AND re-registers tools whose
bridge meta changed (the bridge-side reload_tools case); an unchanged registry
is a no-op. Curated-view filtering still applies during rediscovery.
"""

import pytest

pytest.importorskip("mcp")

from mcp.server.fastmcp import FastMCP

import kit_mcp.server as gw
from omni_kit_mcp.schema import field_specs


def _meta(description, parameters, namespace, owner_id):
    """Bridge-shaped tool meta: parameters + the served field specs (the
    gateway consumes ONLY the specs — built here with the real interpreter,
    pinning the bridge->gateway contract)."""
    return {"description": description, "parameters": parameters,
            "fields": field_specs(parameters),
            "namespace": namespace, "owner_id": owner_id}


@pytest.fixture()
def fresh_gateway(monkeypatch):
    server = FastMCP("test")
    monkeypatch.setattr(gw, "_NAME_MAP", {})
    monkeypatch.setattr(gw, "_REGISTERED_META", {})
    return server


def _fake_registry(monkeypatch, tools):
    class FakeClient:
        def call(self, tool, params=None, **kw):
            assert tool == "list_tools"
            return {"tools": tools}
    monkeypatch.setattr(gw, "get_client", lambda: FakeClient())


def _served(server, name):
    return server._tool_manager.get_tool(name)


def test_new_then_unchanged_then_changed(fresh_gateway, monkeypatch):
    server = fresh_gateway
    meta_v1 = _meta("ping v1", {"message": {"type": "string"}}, "demo", "A")
    _fake_registry(monkeypatch, {"demo.ping": meta_v1})

    assert gw.discover_and_register_tools(server) == ["demo.ping"]
    assert _served(server, "demo__ping").description == "ping v1"

    # unchanged registry -> no-op
    assert gw.discover_and_register_tools(server) == []

    # bridge-side reload changed description AND params -> re-registered
    meta_v2 = _meta("ping v2", {"message": {"type": "string"},
                                "count": {"type": "integer"}}, "demo", "A")
    _fake_registry(monkeypatch, {"demo.ping": meta_v2})
    assert gw.discover_and_register_tools(server) == ["demo.ping (updated)"]
    tool = _served(server, "demo__ping")
    assert tool.description == "ping v2"
    assert "count" in tool.parameters["properties"]


def test_required_and_declared_defaults_shape_the_signature(fresh_gateway, monkeypatch):
    """The gateway renders the served field specs: a required field without a
    declared default is a REQUIRED parameter; a declared default is honored;
    an optional undeclared field defaults to None (dropped pre-dispatch so
    bridge-side handler defaults apply — never a synthesized ''/0)."""
    server = fresh_gateway
    params = {"note": {"type": "string"},                          # optional, no default
              "code": {"type": "string", "required": True},        # required
              "steps": {"type": "integer", "default": 4}}          # declared default
    _fake_registry(monkeypatch, {"demo.run": _meta("d", params, "demo", "A")})
    gw.discover_and_register_tools(server)
    schema = _served(server, "demo__run").parameters
    assert schema["required"] == ["code"]
    assert schema["properties"]["steps"]["default"] == 4
    assert schema["properties"]["note"]["default"] is None


def test_bridge_without_field_specs_is_a_loud_error(fresh_gateway, monkeypatch):
    """No silent fallback to raw-schema interpretation — that would be a
    second interpreter."""
    server = fresh_gateway
    meta_without_fields = {"description": "d", "parameters": {},
                           "namespace": "demo", "owner_id": "A"}
    _fake_registry(monkeypatch, {"demo.ping": meta_without_fields})
    with pytest.raises(RuntimeError, match="field specs"):
        gw.discover_and_register_tools(server)


def test_curated_view_filter_holds_on_refresh(fresh_gateway, monkeypatch):
    server = fresh_gateway
    monkeypatch.setenv("KIT_MCP_NAMESPACE", "pm")
    monkeypatch.setenv("KIT_MCP_BUILTINS", "0")
    _fake_registry(monkeypatch, {
        "pm.read_state": _meta("d", {}, "pm", "P"),
        "demo.ping": _meta("d", {}, "demo", "A"),
        "run_python": _meta("d", {}, None, "omni.kit.mcp"),
    })
    added = gw.discover_and_register_tools(server)
    assert added == ["pm.read_state"]           # namespace + builtins gates hold
    assert _served(server, "pm__read_state")
    served_names = set(server._tool_manager._tools)
    assert "run_python" not in served_names
    assert "demo__ping" not in served_names

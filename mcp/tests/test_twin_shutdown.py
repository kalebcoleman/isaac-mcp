"""Exercise twin startup and shutdown sequencing without loading Kit."""

import ast
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import twin_source


def twin_methods(*names):
    source = twin_source.SOURCE
    tree = ast.parse(source.read_text())
    twin = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DigitalTwin")
    shim = ast.ClassDef(name="Shim", bases=[], keywords=[],
                        body=[n for n in twin.body if isinstance(n, ast.FunctionDef) and n.name in names],
                        decorator_list=[])
    namespace = {"os": os, "MCP_OWNER_ID": "ur5e-dt", "_EXT_INSTANCE": None}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[shim], type_ignores=[])), str(source), "exec"),
         namespace)
    return namespace["Shim"], namespace


def test_failed_attachment_cleans_twin_state_and_preserves_the_error(monkeypatch):
    shim, namespace = twin_methods("_attach_bridge")
    monkeypatch.delenv("UR5E_DT_PORT", raising=False)
    monkeypatch.delenv("ISAAC_SIM_PORT", raising=False)
    namespace.update({"get_mcp_bridge": lambda: object(),
                      "bridge_knobs": SimpleNamespace(PORT=SimpleNamespace(read=lambda: None),
                                                      BIND=SimpleNamespace(read=lambda: None)),
                      "MCP_TOOL_REGISTRY": {}, "ToolDefinition": object,
                      "carb": SimpleNamespace(log_error=lambda message: None)})

    def fail(*args, **kwargs):
        raise RuntimeError("cannot attach")

    namespace["attach_twin_bridge"] = fail
    obj = shim()
    obj._closing = False
    cleaned = []
    obj._finish_shutdown = lambda: cleaned.append(True)
    with pytest.raises(RuntimeError, match="cannot attach"):
        obj._attach_bridge()
    assert obj._closing is True
    assert cleaned == [True]


def test_repeated_shutdown_does_not_cleanup_before_inflight_work_drains():
    shim, namespace = twin_methods("on_shutdown")
    callbacks = []
    obj = shim()
    obj._closing = False
    obj._mcp_owner_registered = True
    obj._mcp = SimpleNamespace(unregister_owner=lambda owner, on_drained: callbacks.append(on_drained))
    cleaned = []
    obj._finish_shutdown = lambda: cleaned.append(True)
    namespace["_EXT_INSTANCE"] = obj
    obj.on_shutdown()
    obj.on_shutdown()
    assert namespace["_EXT_INSTANCE"] is None
    assert len(callbacks) == 1
    assert cleaned == []
    callbacks[0]()
    assert cleaned == [True]

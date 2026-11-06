"""autoload.py — plain-package loading, namespace declaration, hot reload."""

import asyncio
import os
import sys
import textwrap
import threading
from types import SimpleNamespace

import pytest

from isaac_mcp_bridge.autoload import load_tool_modules, reload_tool_module
from isaac_mcp_bridge.bridge import McpBridge


@pytest.fixture()
def bridge():
    return McpBridge(schedule_coroutine=lambda coro: None)


@pytest.fixture()
def tool_dir(tmp_path, monkeypatch):
    """A temp dir on sys.path to drop fake tool packages into."""
    monkeypatch.syspath_prepend(str(tmp_path))
    created = []

    def make(name, source, submodules=None):
        pkg = tmp_path / name
        pkg.mkdir()
        (pkg / "__init__.py").write_text(textwrap.dedent(source))
        for sub, src in (submodules or {}).items():
            (pkg / f"{sub}.py").write_text(textwrap.dedent(src))
        created.append(name)
        return pkg

    yield make
    for name in created:  # keep sys.modules clean across tests
        for mod in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
            del sys.modules[mod]


def dispatch(bridge, name, params=None):
    return asyncio.run(bridge.dispatch(name, params or {}))


def test_load_registers_namespace_and_tools(bridge, tool_dir):
    tool_dir("pkg_a", """
        MCP_NAMESPACE = "pa"
        DOORWAY_ADMISSION = 1
        def register(registrar):
            @registrar.tool("hello tool", {})
            def hello():
                return {"hi": True}
    """)
    results = load_tool_modules(bridge, modules=["pkg_a"], paths=[])
    assert results["pkg_a"] == {"namespace": "pa", "tools": ["pa.hello"]}
    assert dispatch(bridge, "pa.hello")["result"] == {"hi": True}


def test_namespace_defaults_to_module_name(bridge, tool_dir):
    tool_dir("plainpkg", """
        def register(registrar):
            @registrar.tool("t", {})
            def t():
                return {}
    """)
    results = load_tool_modules(bridge, modules=["plainpkg"], paths=[])
    assert results["plainpkg"]["namespace"] == "plainpkg"
    assert "plainpkg.t" in bridge.get_registered_tools()


def test_bad_module_is_isolated_and_rolled_back(bridge, tool_dir):
    tool_dir("good_pkg", """
        MCP_NAMESPACE = "good"
        DOORWAY_ADMISSION = 1
        def register(registrar):
            @registrar.tool("t", {})
            def t():
                return {}
    """)
    tool_dir("bad_pkg", """
        MCP_NAMESPACE = "bad"
        DOORWAY_ADMISSION = 1
        def register(registrar):
            @registrar.tool("t", {})
            def t():
                return {}
            raise RuntimeError("entrypoint exploded")
    """)
    tool_dir("no_entry", """
        x = 1
    """)
    results = load_tool_modules(
        bridge, modules=["bad_pkg", "no_entry", "good_pkg"], paths=[])
    assert str(results["bad_pkg"]).startswith("ERROR")
    # A package without register() is a DECLARED TOOLSET (its public
    # functions become the tools). One with neither an entrypoint nor public
    # functions has nothing to serve, and says so.
    assert "no public functions" in str(results["no_entry"])
    assert results["good_pkg"]["tools"] == ["good.t"]
    # bad_pkg's half-registration was rolled back: namespace + owner freed
    assert "bad.t" not in bridge.get_registered_tools()
    assert "bad_pkg" not in bridge.get_owners()


def test_reload_picks_up_new_code_and_preserves_state(bridge, tool_dir):
    pkg = tool_dir("re_pkg", """
        MCP_NAMESPACE = "re"
        DOORWAY_ADMISSION = 1
        from . import _state, read_state
        def register(registrar):
            @registrar.tool("version", {})
            def version():
                return {"v": 1, "counter": _state.COUNTER, "mode": read_state.MODE}
    """, submodules={"_state": "COUNTER = 0\n", "read_state": "MODE = 'v1'\n"})

    load_tool_modules(bridge, modules=["re_pkg"], paths=[])
    assert dispatch(bridge, "re.version")["result"] == {
        "v": 1, "counter": 0, "mode": "v1"}

    # Live state accumulates; then edit the entrypoint AND the read_state sibling.
    sys.modules["re_pkg._state"].COUNTER = 99
    (pkg / "__init__.py").write_text(textwrap.dedent("""
        MCP_NAMESPACE = "re"
        DOORWAY_ADMISSION = 1
        from . import _state, read_state
        def register(registrar):
            @registrar.tool("version", {})
            def version():
                return {"v": 2, "counter": _state.COUNTER, "mode": read_state.MODE}
    """))
    (pkg / "read_state.py").write_text("MODE = 'v2'\n")

    info = reload_tool_module(bridge, "re_pkg")
    assert "re_pkg" in info["reloaded"]
    # Ratified contract (34c6830): ONLY the module named exactly ``_state`` is
    # preserved. A tool module that merely ENDS in _state must reload — the
    # incident was a stale read_state surviving bound to its old siblings.
    assert "re_pkg._state" not in info["reloaded"]
    assert "re_pkg.read_state" in info["reloaded"]
    # New code live in both entrypoint and read_state; live value survived.
    assert dispatch(bridge, "re.version")["result"] == {
        "v": 2, "counter": 99, "mode": "v2"}


def test_reload_of_never_loaded_module_loads_fresh(bridge, tool_dir):
    tool_dir("late_pkg", """
        MCP_NAMESPACE = "late"
        DOORWAY_ADMISSION = 1
        def register(registrar):
            @registrar.tool("t", {})
            def t():
                return {"late": True}
    """)
    info = reload_tool_module(bridge, "late_pkg")
    assert info["namespace"] == "late"
    assert dispatch(bridge, "late.t")["result"] == {"late": True}


# ------------------------------------------------------------------ the optional policy hook
def test_a_declared_toolset_can_install_a_policy_without_giving_up_its_tools(bridge, tool_dir):
    """The reason this is a SECOND entrypoint. A package whose public functions are its tools has
    no `register()` to hook from, and it should not have to become an entrypoint package — losing
    the derived tools — just to say which calls may not run."""
    tool_dir("pol_pkg", '''
        MCP_NAMESPACE = "pol"
        DOORWAY_ADMISSION = 1

        def do_a_thing():
            return {"did": True}

        def install_policy(registrar):
            registrar.refuse_calls_with(lambda name, params: "refused by the package")
        ''')
    loaded = load_tool_modules(bridge, ["pol_pkg"])

    assert "pol.do_a_thing" in loaded["pol_pkg"]["tools"], "the declared tools are still there"
    assert bridge.pre_dispatch_hooks() == 1

    answer = asyncio.run(bridge.dispatch("pol.do_a_thing", {}))
    assert answer.get("refused") is True
    assert "refused by the package" in answer["message"]


def test_a_package_with_no_policy_installs_none(bridge, tool_dir):
    tool_dir("nopol_pkg", '''
        MCP_NAMESPACE = "nopol"
        DOORWAY_ADMISSION = 1

        def do_a_thing():
            return {"did": True}
        ''')
    load_tool_modules(bridge, ["nopol_pkg"])
    assert bridge.pre_dispatch_hooks() == 0
    assert asyncio.run(bridge.dispatch("nopol.do_a_thing", {}))["result"] == {"did": True}


def test_failed_reload_of_a_policy_pack_refuses_calls_until_repaired(bridge, tool_dir):
    source = '''
        MCP_NAMESPACE = "policy"
        DOORWAY_ADMISSION = 1
        def register(registrar):
            @registrar.tool("inspect", {})
            def inspect():
                return {"ok": True}
        def install_policy(registrar):
            registrar.refuse_calls_with(lambda name, params: None)
    '''
    pkg = tool_dir("reload_policy", source)
    bridge.register_owner("other", "sim").tool("status", {}, name="status")(lambda: {"ok": True})
    load_tool_modules(bridge, modules=["reload_policy"], paths=[])
    assert dispatch(bridge, "sim.status")["result"] == {"ok": True}

    (pkg / "__init__.py").write_text("raise RuntimeError('broken policy module')\n")
    with pytest.raises(RuntimeError, match="broken policy module"):
        reload_tool_module(bridge, "reload_policy")
    refused = dispatch(bridge, "sim.status")
    assert refused["refused"] is True
    assert "policy module reload failed" in refused["message"]
    # Administrative status and reload remain reachable for diagnosis and repair.
    assert bridge._refused("reload_tools", {}) is None
    assert bridge._refused("status", {}) is None

    (pkg / "__init__.py").write_text(textwrap.dedent(source))
    reload_tool_module(bridge, "reload_policy")
    assert dispatch(bridge, "sim.status")["result"] == {"ok": True}
    assert bridge.pre_dispatch_hooks() == 1


def test_policy_reload_gates_dispatch_without_locking_project_threads(bridge, tool_dir, monkeypatch):
    source = '''
        MCP_NAMESPACE = "guarded"
        DOORWAY_ADMISSION = 1
        def register(registrar):
            @registrar.tool("inspect", {})
            def inspect(): return {"ok": True}
        def install_policy(registrar):
            registrar.refuse_calls_with(lambda name, params: None)
    '''
    pkg = tool_dir("threaded_policy", source)
    load_tool_modules(bridge, modules=["threaded_policy"], paths=[])
    helper = SimpleNamespace(bridge=bridge, entered=threading.Event(), release=threading.Event())
    monkeypatch.setitem(sys.modules, "reload_test_helper", helper)
    (pkg / "__init__.py").write_text(textwrap.dedent('''
        import threading
        import reload_test_helper as helper
        MCP_NAMESPACE = "guarded"
        DOORWAY_ADMISSION = 1
        def register(registrar):
            worker = threading.Thread(target=helper.bridge.get_owners, daemon=True)
            worker.start()
            worker.join(1)
            if worker.is_alive(): raise RuntimeError("registry lock blocked a project thread")
            helper.entered.set()
            if not helper.release.wait(5): raise RuntimeError("test timed out")
            @registrar.tool("inspect", {})
            def inspect(): return {"ok": True}
        def install_policy(registrar):
            registrar.refuse_calls_with(lambda name, params: None)
    '''))
    errors = []

    def reload():
        try:
            reload_tool_module(bridge, "threaded_policy")
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=reload, daemon=True)
    worker.start()
    try:
        assert helper.entered.wait(3), f"reload did not enter registration: {errors}"
        assert "reload in progress" in bridge._refused("other.run", {})
        assert bridge._refused("status", {}) is None
        with pytest.raises(RuntimeError, match="another tool module reload"):
            reload_tool_module(bridge, "threaded_policy")
    finally:
        helper.release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert errors == []
    assert bridge._refused("other.run", {}) is None
    assert bridge.pre_dispatch_hooks() == 1


def test_a_policy_that_cannot_install_FAILS_THE_LOAD(bridge, tool_dir):
    """A package whose tools registered and whose gate did not is the shape that looks armed and
    is not — the exact failure this whole task exists to remove. So it is not skipped."""
    tool_dir("badpol_pkg", '''
        MCP_NAMESPACE = "badpol"
        DOORWAY_ADMISSION = 1

        def do_a_thing():
            return {"did": True}

        def install_policy(registrar):
            raise RuntimeError("the profile was unreadable")
        ''')
    loaded = load_tool_modules(bridge, ["badpol_pkg"])
    assert str(loaded["badpol_pkg"]).startswith("ERROR"), loaded
    assert "unreadable" in str(loaded["badpol_pkg"])
    assert bridge.pre_dispatch_hooks() == 0
    # And the tools go with it: a half-loaded package must not leave callable tools behind.
    assert asyncio.run(bridge.dispatch("badpol.do_a_thing", {}))["status"] == "error"


def test_demo_tools_example_serves_a_project_state_through_the_bridge(bridge, monkeypatch):
    """The documented example of a project shipping its own tools (mcp/docs/tool-authoring.md)."""
    from pathlib import Path

    from isaac_mcp_bridge.protocol import ToolError

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "templates"))
    for name in [m for m in sys.modules if m.startswith("demo_tools")]:
        monkeypatch.delitem(sys.modules, name)
    load_tool_modules(bridge, modules=["demo_tools"], paths=[])
    tools = bridge.get_registered_tools()
    assert {"demo.project_state", "demo.set_threshold"} <= set(tools)
    handler = {t.canonical_name: t.definition.handler for t in bridge._tools.values()}
    assert handler["demo.project_state"]()["connection"] == "disconnected"
    assert handler["demo.set_threshold"](name="grip_force_n", value=12.5) == {"name": "grip_force_n", "value": 12.5}
    assert handler["demo.project_state"]()["thresholds"]["grip_force_n"] == 12.5
    with pytest.raises(ToolError, match="between"):
        handler["demo.set_threshold"](name="grip_force_n", value=99)
    with pytest.raises(ToolError, match="unknown threshold"):
        handler["demo.set_threshold"](name="nope", value=1)
    reload_tool_module(bridge, "demo_tools")
    assert handler["demo.project_state"]()["thresholds"]["grip_force_n"] == 12.5  # _state survived
    for name in [m for m in sys.modules if m.startswith("demo_tools")]:
        sys.modules.pop(name, None)

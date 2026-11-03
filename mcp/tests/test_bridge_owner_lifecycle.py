"""Owner lifecycle vs. the bridge's refusal seam — hooks follow their owner, reload never tears
down a call that is still running.

Three contracts, all CPU-only and deterministic (no sleeps: coordination is by Events):

1. ``OwnerRegistrar.refuse_calls_with`` installs a *global* refusal (it governs every call the
   bridge dispatches) that is nevertheless ASSOCIATED with its owner: unregistering the owner
   removes exactly that owner's hooks. Hooks of other owners and hooks installed directly with
   ``bridge.register_pre_dispatch_hook`` are not the owner's to lose. Load / reload / unload of an
   autoloaded package with ``install_policy`` therefore neither leaks nor duplicates hooks.
2. ``reload_tool_module`` refuses an owner with a call in flight BEFORE it unregisters anything:
   the old tools, the old module tree and the old policy stay exactly as they were. Once the call
   drains, the same reload succeeds.
3. A partial registration that fails rolls back its own installed hook, and only its own.

The probe for "which gates are armed" is behavioural (a call carrying ``who=<tag>`` is refused or
runs) plus ``pre_dispatch_hooks()``, so these pin the public contract, not the storage.
"""

import asyncio
import importlib
import re
import sys
import textwrap
import threading

import pytest

from isaac_mcp_bridge.autoload import (
    ToolModuleLoadRefusal,
    load_configured_tool_modules,
    load_tool_modules,
    reload_tool_module,
)
from isaac_mcp_bridge.bridge import McpBridge, ToolDefinition


# ------------------------------------------------------------------ fixtures + helpers
@pytest.fixture()
def bridge():
    """A bridge holding one stable, policy-free owner (``keep``) used only to PROBE the gates:
    a refusal is global, so a call to ``keep.run`` is refused by any armed hook regardless of
    which owner installed it, and ``keep`` is never the owner under test."""
    made = McpBridge(schedule_coroutine=lambda coro: None)
    add_owner(made, "keep", "keep")
    return made


@pytest.fixture()
def tool_dir(tmp_path, monkeypatch):
    """A temp dir on sys.path to drop fake tool packages into (same shape as test_autoload)."""
    monkeypatch.syspath_prepend(str(tmp_path))
    created = []

    def make(name, source, submodules=None):
        pkg = tmp_path / name
        pkg.mkdir()
        (pkg / "__init__.py").write_text(textwrap.dedent(source))
        for sub, src in (submodules or {}).items():
            (pkg / f"{sub}.py").write_text(textwrap.dedent(src))
        created.append(name)
        # A prior package import can cache this directory before the new package exists.
        importlib.invalidate_caches()
        return pkg

    yield make
    for name in created:  # keep sys.modules clean across tests
        for mod in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
            del sys.modules[mod]


def rewrite(pkg, source):
    (pkg / "__init__.py").write_text(textwrap.dedent(source))


def add_owner(bridge, owner_id, namespace):
    """A directly-registered owner with one tool, ``<namespace>.run``."""
    registrar = bridge.register_owner(owner_id, namespace)
    registrar.add(ToolDefinition(
        name="run", description="d", parameters={"who": {"type": "string"}},
        handler=lambda who=None: {"ran": namespace}))
    return registrar


def gate(tag, reason=None):
    """A hook that refuses exactly the calls carrying ``who=<tag>``."""
    reason = reason or f"gate {tag}"

    def hook(name, params):
        return reason if params.get("who") == tag else None

    return hook


def dispatch(bridge, name, params=None):
    return asyncio.run(bridge.dispatch(name, params or {}))


def probe(bridge, tag):
    return dispatch(bridge, "keep.run", {"who": tag})


async def aprobe(bridge, tag):
    """``probe`` for use inside a running loop (an in-flight scenario)."""
    return await bridge.dispatch("keep.run", {"who": tag})


def is_refused(answer):
    return answer.get("refused") is True


def assert_refused(bridge, tag, reason=None):
    answer = probe(bridge, tag)
    assert is_refused(answer), f"{tag!r} should be refused, got {answer}"
    if reason is not None:
        assert reason in answer["message"], answer


def assert_runs(bridge, tag):
    answer = probe(bridge, tag)
    assert answer.get("result") == {"ran": "keep"}, f"{tag!r} should run, got {answer}"


def gate_source(tag, reason):
    return (f'lambda name, params: "{reason}" if params.get("who") == "{tag}" else None')


def toolset_source(ns, tag, reason, version=1):
    """A DECLARED TOOLSET (no register()) that installs its policy through install_policy."""
    return f'''
MCP_NAMESPACE = "{ns}"
DOORWAY_ADMISSION = 1

def act(who: str = ""):
    return {{"v": {version}}}

def install_policy(registrar):
    registrar.refuse_calls_with({gate_source(tag, reason)})
'''


def entrypoint_source(ns, tag, reason, version=1):
    """An entrypoint package that installs its policy through install_policy."""
    return f'''
MCP_NAMESPACE = "{ns}"
DOORWAY_ADMISSION = 1

def register(registrar):
    @registrar.tool("act", {{"who": {{"type": "string"}}}})
    def act(who=None):
        return {{"v": {version}}}

def install_policy(registrar):
    registrar.refuse_calls_with({gate_source(tag, reason)})
'''


STYLES = {"toolset": toolset_source, "entrypoint": entrypoint_source}


def test_unloaded_owner_policy_remains_until_its_inflight_call_drains(bridge):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        owner = bridge.register_owner("slow-owner", "slow")

        @owner.tool("wait", {})
        async def wait():
            started.set()
            await release.wait()
            return {"done": True}

        owner.refuse_calls_with(lambda name, params: "still draining" if name == "keep.run" else None)
        call = asyncio.create_task(bridge.dispatch("slow.wait", {}))
        await started.wait()
        bridge.unregister_owner("slow-owner")
        assert (await bridge.dispatch("keep.run", {}))["message"] == "still draining"
        release.set()
        assert (await call)["result"] == {"done": True}
        assert (await bridge.dispatch("keep.run", {}))["result"] == {"ran": "keep"}

    asyncio.run(scenario())


def test_owner_cleanup_waits_for_inflight_and_reserves_namespace(bridge):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        owner = bridge.register_owner("twin", "ur5e_dt")
        events = []

        @owner.tool("wait", {})
        async def wait():
            started.set()
            await release.wait()
            return {"done": True}

        def cleanup():
            events.append(("cleanup", bridge.get_owners()["twin"]["draining"]))
            with pytest.raises(ValueError, match="already registered"):
                bridge.register_owner("twin", "ur5e_dt")

        call = asyncio.create_task(bridge.dispatch("ur5e_dt.wait", {}))
        await started.wait()
        bridge.unregister_owner("twin", on_drained=cleanup)
        assert events == []
        assert "ur5e_dt.wait" not in bridge.get_registered_tools()
        assert (await bridge.dispatch("keep.run", {}))["result"] == {"ran": "keep"}
        release.set()
        assert (await call)["result"] == {"done": True}
        assert events == [("cleanup", True)]
        bridge.register_owner("twin", "ur5e_dt")

    asyncio.run(scenario())


# ================================================================== 1. hooks follow their owner
def test_require_idle_refuses_while_owner_cleanup_is_running(bridge):
    add_owner(bridge, "finishing", "finish")
    started, release = threading.Event(), threading.Event()

    def cleanup():
        started.set()
        assert release.wait(5)

    worker = threading.Thread(target=lambda: bridge.unregister_owner("finishing", on_drained=cleanup))
    worker.start()
    try:
        assert started.wait(2)
        with pytest.raises(RuntimeError, match="cleanup is already in progress"):
            bridge.unregister_owner("finishing", require_idle=True)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert "finishing" not in bridge.get_owners()


def test_unregistering_an_owner_removes_its_refusal_hook_and_only_its(bridge):
    add_owner(bridge, "A", "a").refuse_calls_with(gate("a"))
    add_owner(bridge, "B", "b").refuse_calls_with(gate("b"))
    bridge.register_pre_dispatch_hook(gate("global"))
    assert bridge.pre_dispatch_hooks() == 3
    for tag in ("a", "b", "global"):
        assert_refused(bridge, tag)

    bridge.unregister_owner("A")

    assert bridge.pre_dispatch_hooks() == 2
    assert_runs(bridge, "a")                 # A's gate went with A
    assert_refused(bridge, "b")              # B's gate is B's, untouched
    assert_refused(bridge, "global")         # a directly-installed hook belongs to no owner


def test_the_owners_hook_is_global_while_the_owner_is_loaded(bridge):
    """Association is about who may REMOVE it. While installed it governs every caller, including
    calls to tools some other owner registered."""
    add_owner(bridge, "A", "a").refuse_calls_with(gate("x"))
    add_owner(bridge, "B", "b")
    assert is_refused(dispatch(bridge, "b.run", {"who": "x"}))
    assert is_refused(dispatch(bridge, "keep.run", {"who": "x"}))


def test_unloading_one_owner_does_not_change_the_other_gates(bridge):
    """Every remaining gate keeps refusing what it refused, in the order it was installed."""
    seen = []

    def watcher(label, tag):
        def hook(name, params):
            seen.append(label)
            return f"{label} says no" if params.get("who") == tag else None
        return hook

    add_owner(bridge, "A", "a").refuse_calls_with(watcher("A", "a"))
    bridge.register_pre_dispatch_hook(watcher("G", "g"))
    add_owner(bridge, "B", "b").refuse_calls_with(watcher("B", "b"))

    probe(bridge, "none")
    assert seen == ["A", "G", "B"]

    bridge.unregister_owner("A")
    seen.clear()
    probe(bridge, "none")
    assert seen == ["G", "B"], "the survivors keep their relative order"
    assert_refused(bridge, "g", "G says no")
    assert_refused(bridge, "b", "B says no")
    assert bridge.pre_dispatch_hooks() == 2


def test_unregistering_an_owner_with_no_hook_changes_no_gate(bridge):
    add_owner(bridge, "plain", "plain")
    add_owner(bridge, "B", "b").refuse_calls_with(gate("b"))
    bridge.register_pre_dispatch_hook(gate("global"))

    bridge.unregister_owner("plain")
    bridge.unregister_owner("never-registered")     # the existing no-op, now also hook-neutral

    assert bridge.pre_dispatch_hooks() == 2
    assert_refused(bridge, "b")
    assert_refused(bridge, "global")


def test_one_function_installed_by_two_parties_is_removed_one_installation_at_a_time(bridge):
    """The same callable installed by an owner AND directly is two installations. Unloading the
    owner must not silently strip the direct one."""
    shared = gate("shared")
    add_owner(bridge, "A", "a").refuse_calls_with(shared)
    bridge.register_pre_dispatch_hook(shared)
    assert bridge.pre_dispatch_hooks() == 2

    bridge.unregister_owner("A")

    assert bridge.pre_dispatch_hooks() == 1
    assert_refused(bridge, "shared")


def test_every_hook_an_owner_installed_leaves_with_it_whichever_registrar_installed_it(bridge):
    """``register_owner``'s registrar and ``registrar_for`` (what install_policy receives) are the
    same owner; both kinds of installation are the owner's."""
    first = add_owner(bridge, "A", "a")
    first.refuse_calls_with(gate("one"))
    first.refuse_calls_with(gate("two"))
    bridge.registrar_for("A").refuse_calls_with(gate("three"))
    bridge.register_pre_dispatch_hook(gate("global"))
    assert bridge.pre_dispatch_hooks() == 4

    bridge.unregister_owner("A")

    assert bridge.pre_dispatch_hooks() == 1
    for tag in ("one", "two", "three"):
        assert_runs(bridge, tag)
    assert_refused(bridge, "global")


def test_a_hook_cleared_by_the_host_does_not_trip_the_owners_later_teardown(bridge):
    add_owner(bridge, "A", "a").refuse_calls_with(gate("a"))
    assert bridge.clear_pre_dispatch_hooks() == 1

    bridge.register_pre_dispatch_hook(gate("late"))
    bridge.unregister_owner("A")                    # A's hook is already gone: nothing to remove

    assert bridge.pre_dispatch_hooks() == 1
    assert_refused(bridge, "late")


def test_reusing_an_owner_id_does_not_resurrect_the_old_owners_hook(bridge):
    add_owner(bridge, "A", "a").refuse_calls_with(gate("old"))
    bridge.unregister_owner("A")
    assert bridge.pre_dispatch_hooks() == 0

    add_owner(bridge, "A", "a")                     # same id and namespace, new instance
    assert bridge.pre_dispatch_hooks() == 0
    assert_runs(bridge, "old")


# ------------------------------------------------------------------ autoloaded package lifecycle
@pytest.mark.parametrize("style", sorted(STYLES))
def test_load_reload_unload_with_install_policy_neither_leaks_nor_duplicates(
        bridge, tool_dir, style):
    build = STYLES[style]
    bridge.register_pre_dispatch_hook(gate("global"))
    base = bridge.pre_dispatch_hooks()
    pkg = tool_dir("life_pkg", build("life", "life", "policy v1"))

    loaded = load_tool_modules(bridge, ["life_pkg"], paths=[])
    assert loaded["life_pkg"]["tools"] == ["life.act"], loaded
    assert bridge.pre_dispatch_hooks() == base + 1
    assert_refused(bridge, "life", "policy v1")

    for version in (2, 3, 4):                       # repeated reloads: never accumulates
        rewrite(pkg, build("life", "life", f"policy v{version}", version=version))
        reload_tool_module(bridge, "life_pkg")
        assert bridge.pre_dispatch_hooks() == base + 1, f"reload {version} leaked or dropped"
        answer = probe(bridge, "life")
        assert is_refused(answer)
        assert f"policy v{version}" in answer["message"]
        assert "policy v1" not in answer["message"], "the old module's hook is still armed"

    bridge.unregister_owner("life_pkg")             # the owner id is the module name
    assert bridge.pre_dispatch_hooks() == base
    assert_runs(bridge, "life")
    assert_refused(bridge, "global")

    load_tool_modules(bridge, ["life_pkg"], paths=[])   # and it can come back, once
    assert bridge.pre_dispatch_hooks() == base + 1


def test_a_package_that_installs_in_register_and_in_install_policy_keeps_both_across_reload(
        bridge, tool_dir):
    source = lambda reason: f'''
MCP_NAMESPACE = "duo"
DOORWAY_ADMISSION = 1

def register(registrar):
    registrar.refuse_calls_with({gate_source("from-register", reason)})
    @registrar.tool("act", {{}})
    def act():
        return {{}}

def install_policy(registrar):
    registrar.refuse_calls_with({gate_source("from-policy", reason)})
'''
    pkg = tool_dir("duo_pkg", source("v1"))
    load_tool_modules(bridge, ["duo_pkg"], paths=[])
    assert bridge.pre_dispatch_hooks() == 2

    rewrite(pkg, source("v2"))
    reload_tool_module(bridge, "duo_pkg")

    assert bridge.pre_dispatch_hooks() == 2
    assert_refused(bridge, "from-register", "v2")
    assert_refused(bridge, "from-policy", "v2")

    bridge.unregister_owner("duo_pkg")
    assert bridge.pre_dispatch_hooks() == 0


@pytest.mark.parametrize("style", sorted(STYLES))
def test_unloading_one_autoloaded_package_leaves_the_other_packages_gate_armed(
        bridge, tool_dir, style):
    build = STYLES[style]
    bridge.register_pre_dispatch_hook(gate("global"))
    tool_dir("pol_a", build("pola", "a", "gate A"))
    tool_dir("pol_b", build("polb", "b", "gate B"))
    load_tool_modules(bridge, ["pol_a", "pol_b"], paths=[])
    assert bridge.pre_dispatch_hooks() == 3

    bridge.unregister_owner("pol_a")

    assert bridge.pre_dispatch_hooks() == 2
    assert_runs(bridge, "a")
    assert_refused(bridge, "b", "gate B")
    assert_refused(bridge, "global")


def test_reloading_one_package_does_not_touch_another_packages_gate(bridge, tool_dir):
    tool_dir("pol_a", toolset_source("pola", "a", "gate A"))
    pkg_b = tool_dir("pol_b", entrypoint_source("polb", "b", "gate B v1"))
    load_tool_modules(bridge, ["pol_a", "pol_b"], paths=[])

    rewrite(pkg_b, entrypoint_source("polb", "b", "gate B v2", version=2))
    reload_tool_module(bridge, "pol_b")

    assert bridge.pre_dispatch_hooks() == 2
    assert_refused(bridge, "a", "gate A")
    assert_refused(bridge, "b", "gate B v2")


def test_reloading_a_never_loaded_package_installs_its_policy_once(bridge, tool_dir):
    """Reload doubles as first load."""
    tool_dir("fresh_pkg", toolset_source("fresh", "fresh", "fresh gate"))
    reload_tool_module(bridge, "fresh_pkg")
    assert bridge.pre_dispatch_hooks() == 1
    reload_tool_module(bridge, "fresh_pkg")
    assert bridge.pre_dispatch_hooks() == 1
    assert_refused(bridge, "fresh", "fresh gate")


# ================================================================== 2. reload vs. in-flight work
IN_FLIGHT_PKG = '''
MCP_NAMESPACE = "rl"
DOORWAY_ADMISSION = 1
from . import _state

def register(registrar):
    @registrar.tool("slow", {{}})
    async def slow():
        _state.started.set()
        await _state.release.wait()
        return {{"v": {version}}}

def install_policy(registrar):
    registrar.refuse_calls_with({gate})
'''
IN_FLIGHT_STATE = "started = None\nrelease = None\n"
IN_FLIGHT_REFUSAL = re.compile(r"in[- ]?flight|in progress|still running|busy", re.IGNORECASE)


def in_flight_source(version):
    return IN_FLIGHT_PKG.format(version=version, gate=gate_source("rl", f"rl gate v{version}"))


def make_in_flight_pkg(tool_dir, bridge):
    pkg = tool_dir("rl_pkg", in_flight_source(1), submodules={"_state": IN_FLIGHT_STATE})
    load_tool_modules(bridge, ["rl_pkg"], paths=[])
    return pkg


def snapshot(bridge, owner_id="rl_pkg"):
    owners = bridge.get_owners()
    return {
        "tools": sorted(t for t in bridge.get_registered_tools() if t.startswith("rl.")),
        "owner_present": owner_id in owners,
        "draining": owners.get(owner_id, {}).get("draining"),
        "found_by_module": bridge.find_owner_by_module(owner_id),
        "hooks": bridge.pre_dispatch_hooks(),
    }


def test_reload_refuses_an_owner_with_a_call_in_flight_before_unregistering_anything(
        bridge, tool_dir):
    pkg = make_in_flight_pkg(tool_dir, bridge)
    state = sys.modules["rl_pkg._state"]
    old_module = sys.modules["rl_pkg"]
    before = snapshot(bridge)
    assert before == {"tools": ["rl.slow"], "owner_present": True, "draining": False,
                      "found_by_module": "rl_pkg", "hooks": 1}

    async def scenario():
        state.started, state.release = asyncio.Event(), asyncio.Event()
        call = asyncio.ensure_future(bridge.dispatch("rl.slow", {}))
        await state.started.wait()                  # the handler is running; no sleeps

        rewrite(pkg, in_flight_source(2))           # a new version is on disk, waiting
        with pytest.raises(Exception) as refusal:
            reload_tool_module(bridge, "rl_pkg")
        assert IN_FLIGHT_REFUSAL.search(str(refusal.value)), (
            f"the reload must be refused FOR the in-flight call, got "
            f"{type(refusal.value).__name__}: {refusal.value}")

        # Refused BEFORE unregistering: nothing about the old instance changed.
        assert snapshot(bridge) == before
        assert sys.modules["rl_pkg"] is old_module, "the module tree was purged anyway"
        assert not call.done(), "the in-flight call must not be torn down"
        gated = await aprobe(bridge, "rl")          # the old policy is still the armed one
        assert is_refused(gated) and "rl gate v1" in gated["message"], gated

        state.release.set()
        return await call

    answer = asyncio.run(scenario())
    assert answer == {"status": "success", "result": {"v": 1}}, "the call ran the OLD code"
    assert snapshot(bridge) == before               # drained, still registered and still usable


def test_the_old_tool_is_still_usable_after_a_refused_reload(bridge, tool_dir):
    pkg = make_in_flight_pkg(tool_dir, bridge)
    state = sys.modules["rl_pkg._state"]

    async def scenario():
        state.started, state.release = asyncio.Event(), asyncio.Event()
        first = asyncio.ensure_future(bridge.dispatch("rl.slow", {}))
        await state.started.wait()
        rewrite(pkg, in_flight_source(2))
        with pytest.raises(Exception):
            reload_tool_module(bridge, "rl_pkg")
        state.release.set()
        assert (await first)["result"] == {"v": 1}

        # The tool the refused reload left behind is the old one, and it still serves calls.
        state.started = asyncio.Event()
        again = await bridge.dispatch("rl.slow", {})
        assert again == {"status": "success", "result": {"v": 1}}

    asyncio.run(scenario())


def test_reload_succeeds_once_the_in_flight_call_has_drained(bridge, tool_dir):
    pkg = make_in_flight_pkg(tool_dir, bridge)
    state = sys.modules["rl_pkg._state"]

    async def scenario():
        state.started, state.release = asyncio.Event(), asyncio.Event()
        call = asyncio.ensure_future(bridge.dispatch("rl.slow", {}))
        await state.started.wait()
        rewrite(pkg, in_flight_source(2))
        with pytest.raises(Exception):
            reload_tool_module(bridge, "rl_pkg")

        state.release.set()
        assert (await call)["result"] == {"v": 1}   # drained

        info = reload_tool_module(bridge, "rl_pkg")  # the very same request, now accepted
        assert info["module"] == "rl_pkg"
        assert info["tools"] == ["rl.slow"]

        state.started = asyncio.Event()
        state.release.set()                         # already set: the new call does not park
        return await bridge.dispatch("rl.slow", {})

    answer = asyncio.run(scenario())
    assert answer == {"status": "success", "result": {"v": 2}}
    assert bridge.pre_dispatch_hooks() == 1
    assert_refused(bridge, "rl", "rl gate v2")
    assert snapshot(bridge)["draining"] is False


def test_reload_is_not_held_up_by_another_owners_in_flight_call(bridge, tool_dir):
    """Only the reloaded owner's own work counts: a long call elsewhere must not block an
    unrelated package's edit-reload loop."""
    make_in_flight_pkg(tool_dir, bridge)
    other = tool_dir("other_pkg", entrypoint_source("other", "other", "other v1"))
    load_tool_modules(bridge, ["other_pkg"], paths=[])
    state = sys.modules["rl_pkg._state"]

    async def scenario():
        state.started, state.release = asyncio.Event(), asyncio.Event()
        call = asyncio.ensure_future(bridge.dispatch("rl.slow", {}))
        await state.started.wait()

        rewrite(other, entrypoint_source("other", "other", "other v2", version=2))
        info = reload_tool_module(bridge, "other_pkg")
        assert info["tools"] == ["other.act"]
        assert not call.done()
        assert snapshot(bridge)["tools"] == ["rl.slow"]

        state.release.set()
        return await call

    assert asyncio.run(scenario())["result"] == {"v": 1}
    assert_refused(bridge, "other", "other v2")
    assert_refused(bridge, "rl", "rl gate v1")
    assert bridge.pre_dispatch_hooks() == 2


# ================================================================== 3. failed partial registration
def failing_entrypoint(ns, tag, mode):
    """Installs its hook FIRST, then fails to finish registering — two different ways."""
    if mode == "raise":
        tail = '    raise RuntimeError("register exploded after installing its hook")\n'
    else:   # a duplicate tool name is a registry error raised mid-registration
        tail = ('    @registrar.tool("dup", {}, name="dup")\n'
                '    def again():\n'
                '        return {}\n')
    return f'''
MCP_NAMESPACE = "{ns}"
DOORWAY_ADMISSION = 1

def register(registrar):
    registrar.refuse_calls_with({gate_source(tag, "partial gate")})
    @registrar.tool("dup", {{}}, name="dup")
    def first():
        return {{}}
{tail}'''


def failing_policy(ns, tag):
    """A declared toolset whose install_policy installs a hook and THEN fails."""
    return f'''
MCP_NAMESPACE = "{ns}"
DOORWAY_ADMISSION = 1

def act(who: str = ""):
    return {{}}

def install_policy(registrar):
    registrar.refuse_calls_with({gate_source(tag, "partial gate")})
    raise RuntimeError("the policy half-installed")
'''


@pytest.fixture()
def armed(bridge, tool_dir):
    """A bridge with a direct hook and one healthy loaded package's hook already armed."""
    bridge.register_pre_dispatch_hook(gate("global"))
    tool_dir("healthy_pkg", toolset_source("healthy", "healthy", "healthy gate"))
    load_tool_modules(bridge, ["healthy_pkg"], paths=[])
    assert bridge.pre_dispatch_hooks() == 2
    return bridge


def assert_only_the_armed_gates_remain(bridge):
    assert bridge.pre_dispatch_hooks() == 2
    assert_refused(bridge, "global")
    assert_refused(bridge, "healthy", "healthy gate")


@pytest.mark.parametrize("mode", ["raise", "duplicate_tool"])
def test_a_failed_entrypoint_load_removes_its_own_hook_and_no_other(armed, tool_dir, mode):
    tool_dir("broken_pkg", failing_entrypoint("broken", "partial", mode))

    results = load_tool_modules(armed, ["broken_pkg"], paths=[])

    assert str(results["broken_pkg"]).startswith("ERROR"), results
    assert "broken_pkg" not in armed.get_owners()
    assert "broken.dup" not in armed.get_registered_tools()
    assert_runs(armed, "partial")                   # the half-installed gate is gone
    assert_only_the_armed_gates_remain(armed)


def test_a_policy_that_installs_then_fails_removes_its_own_hook_and_no_other(armed, tool_dir):
    tool_dir("halfpol_pkg", failing_policy("halfpol", "partial"))

    results = load_tool_modules(armed, ["halfpol_pkg"], paths=[])

    assert "half-installed" in str(results["halfpol_pkg"])
    assert "halfpol_pkg" not in armed.get_owners()
    assert "halfpol.act" not in armed.get_registered_tools()
    assert_runs(armed, "partial")
    assert_only_the_armed_gates_remain(armed)


def test_a_failure_among_several_modules_leaves_the_good_ones_gates_alone(bridge, tool_dir):
    tool_dir("ok_one", toolset_source("ok1", "ok1", "ok1 gate"))
    tool_dir("broken_pkg", failing_entrypoint("broken", "partial", "raise"))
    tool_dir("ok_two", entrypoint_source("ok2", "ok2", "ok2 gate"))

    results = load_tool_modules(bridge, ["ok_one", "broken_pkg", "ok_two"], paths=[])

    assert str(results["broken_pkg"]).startswith("ERROR")
    assert bridge.pre_dispatch_hooks() == 2
    assert_refused(bridge, "ok1", "ok1 gate")
    assert_refused(bridge, "ok2", "ok2 gate")
    assert_runs(bridge, "partial")


def test_fixing_a_failed_module_and_retrying_arms_exactly_one_hook(armed, tool_dir):
    pkg = tool_dir("retry_pkg", failing_entrypoint("retry", "retry", "raise"))
    load_tool_modules(armed, ["retry_pkg"], paths=[])
    load_tool_modules(armed, ["retry_pkg"], paths=[])   # a second failure must not accumulate
    assert_only_the_armed_gates_remain(armed)

    rewrite(pkg, entrypoint_source("retry", "retry", "retry gate"))
    for name in [m for m in sys.modules if m == "retry_pkg"]:
        del sys.modules[name]
    results = load_tool_modules(armed, ["retry_pkg"], paths=[])

    assert results["retry_pkg"]["tools"] == ["retry.act"]
    assert armed.pre_dispatch_hooks() == 3
    assert_refused(armed, "retry", "retry gate")


def test_a_reload_whose_new_version_fails_midway_refuses_until_repaired(armed, tool_dir):
    pkg = tool_dir("flaky_pkg", entrypoint_source("flaky", "flaky", "flaky v1"))
    load_tool_modules(armed, ["flaky_pkg"], paths=[])
    assert armed.pre_dispatch_hooks() == 3

    rewrite(pkg, failing_entrypoint("flaky", "partial", "raise"))
    with pytest.raises(RuntimeError, match="exploded"):
        reload_tool_module(armed, "flaky_pkg")

    # The old instance was retired (its hook with it); the new one's partial hook was rolled back.
    assert "flaky_pkg" not in armed.get_owners()
    assert "policy module reload failed" in probe(armed, "flaky")["message"]
    assert "policy module reload failed" in probe(armed, "partial")["message"]
    assert armed.pre_dispatch_hooks() == 2  # unrelated hooks remain
    rewrite(pkg, entrypoint_source("flaky", "flaky", "flaky repaired"))
    reload_tool_module(armed, "flaky_pkg")
    assert_runs(armed, "partial")
    assert_refused(armed, "flaky", "flaky repaired")


def test_a_boot_refusal_unloads_the_good_modules_gates_too_but_not_a_direct_hook(
        bridge, tool_dir):
    bridge.register_pre_dispatch_hook(gate("global"))
    tool_dir("ok_pkg", toolset_source("okp", "okp", "ok gate"))
    tool_dir("broken_pkg", failing_entrypoint("broken", "partial", "raise"))

    with pytest.raises(ToolModuleLoadRefusal):
        load_configured_tool_modules(bridge, ["ok_pkg", "broken_pkg"], paths=[])

    assert bridge.pre_dispatch_hooks() == 1
    assert_refused(bridge, "global")
    assert_runs(bridge, "okp")
    assert_runs(bridge, "partial")
    assert {"ok_pkg", "broken_pkg"}.isdisjoint(bridge.get_owners())

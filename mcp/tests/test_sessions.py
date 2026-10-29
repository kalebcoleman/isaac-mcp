"""sessions.py — the run_python session store and its persist policy."""

from omni_kit_mcp.sessions import PRELOADED, SessionStore


def _exec_globals(**user):
    """A realistic post-exec namespace: preloads + dunder + user vars."""
    ns = {name: object() for name in PRELOADED}
    ns["__builtins__"] = {}
    ns["_scratch"] = 1
    ns.update(user)
    return ns


def test_persist_policy_keeps_user_names_only():
    store = SessionStore()
    kept = store.persist("s1", _exec_globals(x=1, robot="ur5e"))
    assert kept == ["robot", "x"]                       # sorted, user vars only
    assert store.namespace("s1") == {"x": 1, "robot": "ur5e"}


def test_namespace_is_a_copy_and_unknown_is_empty():
    store = SessionStore()
    store.persist("s1", _exec_globals(x=1))
    ns = store.namespace("s1")
    ns["x"] = 999
    assert store.namespace("s1")["x"] == 1              # caller can't mutate the store
    assert store.namespace("nope") == {}


def test_sessions_are_isolated_and_clear_drops_all():
    store = SessionStore()
    store.persist("a", _exec_globals(x=1))
    store.persist("b", _exec_globals(y=2))
    assert len(store) == 2
    assert "y" not in store.namespace("a")
    store.clear()                                       # the stage-open staleness guard
    assert len(store) == 0 and store.namespace("a") == {}


def test_bridge_owns_a_store_and_clear_sessions_delegates():
    from test_bridge import make_bridge
    bridge = make_bridge()
    bridge.sessions.persist("s", _exec_globals(x=1))
    bridge.clear_sessions()
    assert bridge.sessions.namespace("s") == {}

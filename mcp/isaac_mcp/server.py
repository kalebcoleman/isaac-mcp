"""Generic MCP server (front-end) for the isaac.mcp.bridge bridge.

The LLM-facing half: a FastMCP server that is a TCP *client* to the Kit-side
bridge. It connects, discovers the registry via ``list_tools``, and registers
each canonical tool dynamically — hardcoding nothing about any project.

One bridge socket serves N of these front-ends concurrently; a "per-project
MCP server" is just this binary with a namespace filter, not a separate port:

    ISAAC_MCP_PORT   bridge port. Optional: unset, the client discovers a
                        lone running bridge via its runtime portfile (and
                        errors loudly on ambiguity). Set it to pin a process.
                        ISAAC_MCP_HOST optional dial override; unset, the
                        discovered bridge's advertised host (else loopback).
    ISAAC_MCP_NAMESPACE   comma-separated namespace allowlist; unset => all.
    ISAAC_MCP_BUILTINS    "0" hides the bridge's bare builtins (run_python,
                        reload_tools) — the curated-agent view.
    ISAAC_MCP_LOCAL_TOOLS comma-separated groups of the host's own tools (screen, Isaac
                        Lab, gateway) to register; unset => all. See local_tools.py. It does not
                        affect bridge tools, and composes with the two filters above.
    ISAAC_MCP_NAME / ISAAC_MCP_INSTRUCTIONS   cosmetic server identity.

Tools of known extensions (the UR5e twin) are listed from a packaged catalog snapshot
(``isaac_mcp.host_catalog``) the moment a server is built, whether or not a bridge is running:
each is a callable *offline proxy* that dials the selected bridge when called and answers with an
actionable error if there is none or it lacks the extension. A live bridge's own ``list_tools``
metadata replaces the proxy (it is authoritative); a tool the bridge does not serve reverts to
its proxy. The namespace filter applies to proxies too. Nothing here ever starts a simulator.

Canonical bridge names are ``<namespace>.<tool>``; MCP tool names cannot
contain dots, so they are exposed as ``<namespace>__<tool>`` and mapped back
on dispatch.

Project-specific POLICY plugs in via three hook lists (it must not live here):
  - register_pre_dispatch_hook(fn): fn(name, params) -> Optional[str]
        May mutate params in place and/or return a refusal string to BLOCK.
  - register_post_dispatch_hook(fn): fn(name, params, result) -> Optional[str]
        Sees every outcome and may return a replacement result (e.g. the same
        answer with a condition of the world annotated onto it), or None to
        leave it. Runs on bridge answers, bridge errors, and pre-hook refusals
        alike — a call that just failed is when a caller most needs telling.
  - register_meta_category_hook(fn): fn(name) -> Optional[str]
        Returns an MCP ``_meta`` category for a tool name, or None.

``isaac_mcp.gate`` is the mechanism a policy hook can be written against: a pure
deny-gate over a declared policy file. It, too, holds no policy.
"""

import functools
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Dict, Iterable, List, Mapping, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.lowlevel.server import NotificationOptions

from . import host_catalog
from .client import BridgeClient, BridgeError
from .isaaclab.tools import register_isaaclab_tools
from .local_tools import DEFAULT_GROUPS, IDEMPOTENT_WRITE, ScopedTools, local_tool_groups
from .desktop_input import register_desktop_input_tools
from .sim_sessions import live_ports, register_sim_session_tools
from .screen import register_screen_tools

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("KitMCPServer")

SERVER_NAME = os.getenv("ISAAC_MCP_NAME", "isaac")
SERVER_INSTRUCTIONS = os.getenv(
    "ISAAC_MCP_INSTRUCTIONS",
    "Isaac Sim / Isaac Lab MCP server. Isaac Sim tools are discovered from the "
    "running Kit bridge. The ur5e_dt__* twin tools are listed by default from a packaged "
    "catalog, but work only when the selected bridge's Kit loaded the twin extension; "
    "otherwise they return an error that says what to do. If the bridge built-ins "
    "(run_python, status, ...) are not listed, call refresh_tools first: the client may "
    "have connected before Kit started, or ISAAC_MCP_BUILTINS=0 may hide them. "
    "ISAAC_MCP_NAMESPACE may hide catalog and other namespaced tools. "
    "Find advertised bridges with the isaac_mcp.client CLI (--list-bridges) and "
    "confirm the chosen bridge's PID and port (status) before scene-changing calls, "
    "and start an additional Kit only when explicitly requested and resource checks permit. "
    "screenshot, list_windows, raise_window, move_window and the screen "
    "recording tools capture this Linux desktop. screenshot() and "
    "start_screen_recording() with no target pick the Isaac Sim or Isaac Lab "
    "window when one is open, so they capture the sim alone even if it is covered. "
    "Use the ur5e_dt__* tools for twin state and the Kit start_recording tool for "
    "clean viewport video. isaaclab__* tools run Isaac Lab training/eval/play jobs. "
    "When a job or scene is running with a window (isaaclab__play, headless=false), "
    "look at it yourself: take screenshots or a short screen recording to check the "
    "result and decide the next step, without waiting to be asked. "
    "isaaclab__runs_status reports the job's window_id for screenshot(window_id=...).")

RESPONSE_TIMEOUT_S = 300.0
# Discovery must not make host recovery tools wait behind a stalled native Kit
# call. Mutations keep their existing timeout and are never retried on timeout.
DISCOVERY_TIMEOUT_S = 5.0


def _namespace_filter() -> Optional[set]:
    raw = os.getenv("ISAAC_MCP_NAMESPACE", "").strip()
    if not raw:
        return None
    return {ns.strip() for ns in raw.split(",") if ns.strip()} or None


def _builtins_enabled() -> bool:
    return os.getenv("ISAAC_MCP_BUILTINS", "1") not in ("0", "false", "no")


# ---- Policy hooks (populated by the host project; empty => pure passthrough) ----
_PRE_DISPATCH_HOOKS: List[Callable[[str, Dict[str, Any]], Optional[str]]] = []
_POST_DISPATCH_HOOKS: List[Callable[[str, Dict[str, Any], str], Optional[str]]] = []
_META_CATEGORY_HOOKS: List[Callable[[str], Optional[str]]] = []


def register_pre_dispatch_hook(fn: Callable[[str, Dict[str, Any]], Optional[str]]) -> None:
    """Register a pre-dispatch policy hook. fn(name, params) may mutate params
    and return a refusal string to block, or None to proceed."""
    _PRE_DISPATCH_HOOKS.append(fn)


def register_post_dispatch_hook(
        fn: Callable[[str, Dict[str, Any], str], Optional[str]]) -> None:
    """Register a post-dispatch policy hook. fn(name, params, result) may return
    a replacement result, or None to leave it as is.

    It sees every outcome of the call — the bridge's answer, a bridge error, and
    a refusal a pre-dispatch hook returned — because a caller most needs to be
    told the state of the world on the calls that did not work. Hooks run in
    registration order and each sees the previous one's result."""
    _POST_DISPATCH_HOOKS.append(fn)


def register_meta_category_hook(fn: Callable[[str], Optional[str]]) -> None:
    """Register a hook mapping a tool name to an MCP _meta category (or None)."""
    _META_CATEGORY_HOOKS.append(fn)


# ==================== Bridge connection ====================
# The wire protocol lives in isaac_mcp.client (the reference client): framing,
# envelope decoding, health checks, and the reconnect-retry are its job. Each
# server holds one persistent BridgeClient.
#
# What a server knows about "its" bridge (the connection, which MCP names came from which
# bridge tools, and the meta they were registered with) lives in a _BridgeState, one per server
# built by build_server(), so two servers in one process do not share discovery state or a
# socket. The default instance (`mcp`) uses the module-level names below (_client, _NAME_MAP,
# _REGISTERED_META, get_client), which is what tests and scripts that predate build_server()
# read and patch. The policy hook lists above are process-level policy, not per-server.

_client: Optional[BridgeClient] = None

# MCP-facing name -> canonical bridge name (dots are illegal in MCP tool names).
_NAME_MAP: Dict[str, str] = {}
# MCP-facing name -> the bridge meta it was registered with, so rediscovery can
# detect schema/description changes (reload_tools on the bridge side) and
# re-register instead of silently serving a stale schema.
_REGISTERED_META: Dict[str, Dict[str, Any]] = {}


def get_client() -> BridgeClient:
    """The default server's bridge client, connecting on first use."""
    global _client
    if _client is None:
        # host/port unset -> the client resolves the endpoint ($ISAAC_MCP_HOST /
        # $ISAAC_MCP_PORT, else portfile discovery) — one resolution path.
        _client = BridgeClient(timeout=RESPONSE_TIMEOUT_S)
    return _client


class _BridgeState:
    """One server's bridge connection and discovery bookkeeping."""

    def __init__(self) -> None:
        self.client: Optional[BridgeClient] = None
        self.name_map: Dict[str, str] = {}
        self.registered_meta: Dict[str, Dict[str, Any]] = {}
        self.env: Optional[Mapping[str, str]] = None  # build_server's env (sim session directory)

    def get_client(self) -> BridgeClient:
        if self.client is None:
            self.client = BridgeClient(timeout=RESPONSE_TIMEOUT_S)
        return self.client


class _DefaultBridgeState(_BridgeState):
    """The default server's state, backed by the module-level names so code and tests that
    use (or monkeypatch) `_client`, `_NAME_MAP`, `_REGISTERED_META` and `get_client` see it."""

    @property
    def client(self) -> Optional[BridgeClient]:
        return _client

    @client.setter
    def client(self, value: Optional[BridgeClient]) -> None:
        global _client
        _client = value

    @property
    def name_map(self) -> Dict[str, str]:
        return _NAME_MAP

    @property
    def registered_meta(self) -> Dict[str, Dict[str, Any]]:
        return _REGISTERED_META

    def __init__(self) -> None:  # the base class would assign the properties above
        pass

    def get_client(self) -> BridgeClient:
        return get_client()


_DEFAULT_STATE = _DefaultBridgeState()
_STATE_ATTR = "_isaac_bridge_state"


def _state_for(server: Any) -> _BridgeState:
    """The bridge state of a server built by build_server(); any other FastMCP (and the default
    instance) uses the module-level one."""
    return getattr(server, _STATE_ATTR, None) or _DEFAULT_STATE


# ==================== Dynamic tool registration ====================


def registered_tools(server: Any = None) -> List[str]:
    """The canonical bridge names currently registered, sorted (default: the default server's).

    The roster a policy's matchers can be checked against: a gate naming a tool this does not
    list is gating nothing. It is the live bridge's tools only (see ``offline_tools``
    for the catalog proxies). Empty before discovery has run, which is itself worth knowing —
    checking a policy against a roster nobody has filled would clear every name in it."""
    return sorted(_state_for(server).name_map.values())


def offline_tools(server: Any = None) -> List[str]:
    """The canonical offline catalog proxy names, sorted (default: the default server's).

    These are listed and callable (and cross the same policy hooks) but the selected bridge does
    not serve them; ``registered_tools`` does not include them."""
    offline = _offline_for(mcp if server is None else server)
    return sorted(offline.names.values()) if offline is not None else []


def _mcp_name(canonical: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", canonical.replace(".", "__"))


def _visible(canonical: str, meta: Dict[str, Any], state=None) -> bool:
    """Apply the view filters: namespace allowlist + builtins gate."""
    namespace = meta.get("namespace")
    if namespace in {"sim", "assets"} and not getattr(state, "scene_tools_enabled", True):
        return False
    if namespace is None:  # bridge builtins (run_python, reload_tools)
        return _builtins_enabled()
    allow = _namespace_filter()
    return allow is None or namespace in allow


def _drop_tool(server: FastMCP, name: str) -> None:
    """Remove one tool from FastMCP so it can be re-registered with a new
    schema. FastMCP has no public remove; its ToolManager keeps a plain dict —
    this is the one sanctioned private-attr touch, and it lives only here."""
    server._tool_manager._tools.pop(name, None)


def discover_and_register_tools(server: FastMCP) -> List[str]:
    """Query the bridge registry and register each visible tool.

    New tools are added, changed schemas are re-registered, and tools no longer
    in the selected bridge view are removed. A failed or malformed discovery
    does not turn an unknown registry into an empty one."""
    state = _state_for(server)
    offline = _offline_for(server)
    offline_names = offline.names if offline is not None else {}
    result = state.get_client().call("list_tools", timeout=DISCOVERY_TIMEOUT_S)
    if not isinstance(result, dict) or not isinstance(result.get("tools"), dict):
        raise RuntimeError("bridge list_tools response has no tools dictionary")
    if any(not isinstance(name, str) or not isinstance(meta, dict)
           for name, meta in result["tools"].items()):
        raise RuntimeError("bridge list_tools contains an invalid tool entry")
    tools = {name: meta for name, meta in result["tools"].items() if _visible(name, meta, state)}
    names = {}
    for canonical, meta in tools.items():
        if "fields" not in meta:
            raise RuntimeError(f"bridge tool {canonical!r} has no field specs")
        name = _mcp_name(canonical)
        if name in names and names[name] != canonical:
            raise RuntimeError(f"bridge tools {names[name]!r} and {canonical!r} collide as {name!r}")
        if (name in server._tool_manager._tools and name not in state.name_map
                and offline_names.get(name) != canonical):   # a live tool may replace its own proxy
            raise RuntimeError(f"bridge tool {canonical!r} collides with host tool {name!r}")
        names[name] = canonical

    # Build every changed tool on a temporary FastMCP registry first. A broken
    # field spec or signature must not remove an existing tool or leave the
    # host with only the first part of the new registry.
    staging = FastMCP("bridge discovery staging")
    staged = {}
    for canonical, meta in sorted(tools.items()):
        name = _mcp_name(canonical)
        if state.name_map.get(name) == canonical and state.registered_meta.get(name) == meta:
            continue
        try:
            register_dynamic_tool(staging, name, canonical, meta, state=state)
        except Exception as error:
            raise RuntimeError(f"invalid bridge tool {canonical!r}: {error}") from error
        staged[name] = (canonical, meta, staging._tool_manager._tools[name])

    changed = []
    departed = {}
    for mcp_name, canonical in list(state.name_map.items()):
        if canonical not in tools:
            previous_tool = server._tool_manager._tools.get(mcp_name)
            _drop_tool(server, mcp_name)
            state.name_map.pop(mcp_name, None)
            state.registered_meta.pop(mcp_name, None)
            departed[canonical] = previous_tool
    for name, (canonical, meta, tool) in staged.items():
        if name in offline_names:
            # A live copy replaces the proxy. Notify only if its client-visible
            # schema, description or other advertised metadata differs.
            if _tool_surface(server._tool_manager._tools.get(name)) != _tool_surface(tool):
                changed.append(f"{canonical} (updated)")
        else:
            changed.append(f"{canonical} (updated)" if name in state.name_map else canonical)
        server._tool_manager._tools[name] = tool
        state.name_map[name] = canonical
        state.registered_meta[name] = meta
    # A catalog tool this bridge does not (or no longer) serve stays listed as an offline proxy.
    offline_changes = _sync_offline(server, state)
    # A departed live tool may have been replaced by its catalog proxy. Its
    # visible change is handled below, so do not count the replacement twice.
    changed.extend(change for canonical, change in offline_changes.items() if canonical not in departed)
    for canonical, previous_tool in departed.items():
        reverted = offline.catalog.get(canonical) if offline is not None else None
        if reverted is not None and canonical in offline_names.values():
            if _tool_surface(previous_tool) != _tool_surface(server._tool_manager._tools.get(_mcp_name(canonical))):
                changed.append(f"{canonical} (offline)")
        else:
            changed.append(f"{canonical} (removed)")
    if changed:
        logger.info(f"Registered/updated {len(changed)} bridge tools: {changed}")
    return changed


# ==================== Offline catalog proxies ====================
#
# A server built by build_server() lists every catalogued tool (host_catalog) as a proxy registered
# straight on the server, never through ScopedTools: ISAAC_MCP_LOCAL_TOOLS selects the host's own
# tools and has no say over bridge tools, offline or not. The bookkeeping lives on the server object
# (not in _BridgeState) because the default state is shared with bare FastMCP instances used by
# scripts and tests, which must not grow proxies. Servers without it behave exactly as before.

_OFFLINE_ATTR = "_isaac_offline"


class _OfflineTools:
    """One server's catalog and which of its tools are currently served as offline proxies."""

    def __init__(self, catalog: Dict[str, Dict[str, Any]]) -> None:
        self.catalog = catalog                    # canonical -> catalog meta (unfiltered)
        self.names: Dict[str, str] = {}           # MCP name -> canonical, while a proxy
        self.categories: Dict[str, Optional[str]] = {}   # MCP name -> _meta category it carries


def _offline_for(server: Any) -> Optional[_OfflineTools]:
    return getattr(server, _OFFLINE_ATTR, None)


def _tool_surface(tool: Any) -> Any:
    """Client-visible fields, excluding the handler and bridge routing state."""
    if tool is None:
        return None
    return (tool.description, tool.parameters, getattr(tool, "title", None), getattr(tool, "meta", None),
            getattr(tool, "annotations", None), getattr(tool, "icons", None))


def _sync_offline(server: Any, state: _BridgeState) -> Dict[str, str]:
    """Make the server's offline proxies match the catalog, the filters and the live tools.

    Every catalog tool that passes the view filters and is not currently served by the selected
    bridge has a proxy; a tool the bridge now serves, or a filter now hides, loses it. The filters
    are re-read from the process environment each time, as live discovery does. Returns changes
    by canonical name; an unchanged replacement by a live tool is not a visible change."""
    changed: Dict[str, str] = {}
    offline = _offline_for(server)
    if offline is None:
        return changed
    wanted = {_mcp_name(canonical): (canonical, meta)
              for canonical, meta in offline.catalog.items() if _visible(canonical, meta, state)}
    for name in list(offline.names):
        if name in state.name_map:                # the live copy took over this name
            offline.names.pop(name)
            offline.categories.pop(name, None)
        elif name not in wanted:                  # hidden since it was installed
            _drop_tool(server, name)
            canonical = offline.names.pop(name)
            offline.categories.pop(name, None)
            changed[canonical] = f"{canonical} (removed)"
    for name, (canonical, meta) in wanted.items():
        if name in state.name_map:
            continue                              # served live (authoritative)
        category = _meta_category(canonical)
        if offline.names.get(name) == canonical and offline.categories.get(name) == category:
            continue
        if name in server._tool_manager._tools and name not in offline.names:
            raise RuntimeError(f"offline catalog tool {canonical!r} collides with host tool {name!r}")
        was_proxy = name in offline.names
        _drop_tool(server, name)                  # FastMCP keeps the first tool of a name
        register_dynamic_tool(server, name, canonical, meta, state=state, category=category)
        offline.names[name] = canonical
        offline.categories[name] = category
        changed[canonical] = f"{canonical} (updated)" if was_proxy else canonical
    return changed


# Catalogued tools this host server runs itself (filename-only project discovery); a bridge never
# serves them, so their absence from a bridge is expected and not worth a note.
HOST_RUN_TOOLS = frozenset({"assets.roots", "assets.scan", "assets.search"})


def _offline_note(server: Any) -> str:
    """One line for refresh/use_bridge results: which listed tools the bridge does not serve."""
    offline = _offline_for(server)
    if offline is None or not offline.names:
        return ""
    spaces: Dict[str, int] = {}
    for canonical in offline.names.values():
        if canonical in HOST_RUN_TOOLS:   # served by this host server, never by a bridge
            continue
        space = canonical.split(".", 1)[0]
        spaces[space] = spaces.get(space, 0) + 1
    if not spaces:
        return ""
    counts = ", ".join(f"{space}: {count}" for space, count in sorted(spaces.items()))
    return ("\nNote: " + counts + " tool(s) are listed from the packaged catalog but have not "
            "been confirmed on the selected bridge. Select a bridge that loaded the extension "
            "(use_bridge) or start one yourself, then call refresh_tools.")


_BRIDGE_HELP = (
    "To use it: find a bridge that loaded the requested tool extension (ur5e-dt or sim-tools) with "
    "`python -m isaac_mcp.client --list-bridges` and select it with use_bridge(port), or start "
    "Kit yourself (`./run.sh --headless`, or sim__launch when simulator launch is enabled) "
    "and call refresh_tools. This server never starts a simulator "
    "on its own.")


def _needs_bridge_guidance(error: BaseException) -> bool:
    """Match endpoint failures that benefit from connection guidance.

    A retry can fail this way after an earlier request was sent, so the outcome
    must still be reported as unknown rather than asserting the tool did not run.
    """
    if isinstance(error, ConnectionRefusedError):
        return True
    return isinstance(error, RuntimeError) and str(error).startswith(
        ("no bridge found", "multiple bridges running"))


def _catalog_error(canonical: str, error: BaseException, state: Optional[_BridgeState]) -> str:
    """The actionable message for a catalogued tool whose call could not reach a serving bridge."""
    if isinstance(error, BridgeError):
        try:
            port = (state or _DEFAULT_STATE).get_client().port
            where = f"the selected bridge (port {port})"
        except Exception:  # noqa: BLE001
            where = "the selected bridge"
        return (f"Error: {canonical} is not available and was not run: {where} is running but did "
                f"not register it, so its Kit has not loaded the extension that provides it, or "
                f"runs an older one. {_BRIDGE_HELP}")
    return (f"Error: {canonical} is not available: no Isaac Sim bridge could be reached ({error}). "
            "If the connection failed after dispatch, the tool may have run; check the "
            f"simulator state before retrying. {_BRIDGE_HELP}")


_KIND_ANNOTATIONS = {"boolean": bool, "integer": int, "number": float,
                     "array": list, "object": dict, "string": str}


def _build_tool_function(name: str, canonical: str, description: str,
                         fields: List[Dict[str, Any]], state: Optional[_BridgeState] = None):
    """Build a function with a real signature/annotations for FastMCP schemas.

    ``fields`` are the bridge-served field specs (the schema vocabulary is
    interpreted once, bridge-side — schema.py); this only renders them.
    Required fields without a declared default become required parameters;
    optional fields default to their declared default, else None — and None
    values are dropped before dispatch so bridge-side handler defaults apply.
    """
    from typing import Literal, Optional

    # def-order: required-without-default parameters must precede defaulted ones.
    ordered = sorted(fields, key=lambda f: not (f["required"] and not f["has_default"]))
    param_list = []
    annotations: Dict[str, Any] = {"return": str}
    for f in ordered:
        annotation = (Literal[tuple(f["enum"])] if f["kind"] == "enum"
                      else _KIND_ANNOTATIONS.get(f["kind"], Any))  # an unknown kind must not break discovery
        # These versioned contracts also carry usable client-side descriptions and
        # typed vectors. Leave the legacy bridge/UR5e schema surface unchanged.
        from .scene_contracts import REGISTRY
        if canonical in REGISTRY and f["name"] not in REGISTRY[canonical]["parameters"]:
            # A newer bridge can serve a parameter this host's registry does not know yet (the two
            # checkouts sync separately): keep the bridge's plain type and description instead of
            # failing discovery or guessing a typed vector.
            from typing import Annotated
            from pydantic import Field
            annotation = Annotated[annotation, Field(description=f.get("description") or "")]
        elif canonical in REGISTRY:
            from typing import Annotated
            from pydantic import Field
            spec = REGISTRY[canonical]["parameters"][f["name"]]
            bounds = {}
            if f["kind"] == "array":
                strings = f["name"] in {"observe_prims", "observe_joints"} or (canonical == "sim.ui.act" and f["name"] == "keys")
                annotation = list[str] if strings else list[float]
                if canonical == "sim.graph.build" and f["name"] in ("nodes", "connections"):
                    # [name, type] / [from, to] string pairs.
                    annotation = list[Annotated[list[str], Field(min_length=2, max_length=2)]]
                if canonical == "sim.map.occupancy" and f["name"] == "clear":
                    # A list of [x, y, radius] circles; list[float] rejected every valid value.
                    annotation = list[Annotated[list[float], Field(min_length=3, max_length=3)]]
                lengths = {"position": 3, "orientation": 4, "scale": 3,
                           "color": 3, "gravity": 3, "target": 3,
                           "rotate_xyz": 3, "eye": 3, "clipping_range": 2}
                if f["name"] in lengths:
                    bounds = {"min_length": lengths[f["name"]], "max_length": lengths[f["name"]]}
                else:
                    bounds = {"max_length": {"observe_prims": 16, "observe_joints": 8, "clear": 16, "keys": 5, "nodes": 64, "connections": 256}.get(f["name"], 128)}
            annotation = Annotated[annotation, Field(description=spec["description"], **bounds)]
        if f["required"] and not f["has_default"]:
            param_list.append(f["name"])
        else:
            default = f["default"] if f["has_default"] else None
            if default is None:
                annotation = Optional[annotation]
            param_list.append(f"{f['name']}={default!r}")
        annotations[f["name"]] = annotation

    params_str = ", ".join(param_list)
    # No docstring in the exec'd source — descriptions are arbitrary text and
    # embedding them invites quote/newline breakage; set __doc__ after.
    func_code = (
        f"def {name}({params_str}) -> str:\n"
        f"    return _tool_impl({canonical!r}, locals())\n"
    )
    local_ns = {"_tool_impl": functools.partial(
        _tool_implementation, state=state, mcp_result=True)}
    exec(func_code, local_ns)
    func = local_ns[name]
    func.__doc__ = description
    func.__annotations__.update(annotations)
    return func


def _tool_implementation(canonical: str, params: Dict[str, Any],
                         state: Optional[_BridgeState] = None, *,
                         mcp_result: bool = False) -> Any:
    """Shared implementation for all dynamic tools.

    Every dynamically registered bridge tool called through this host crosses both
    policy hook lists. Direct bridge socket callers bypass these host hooks. The
    result a post-dispatch hook sees is the one the caller would have received,
    whatever produced it."""
    # Drop unset optional params so bridge-side handler defaults apply — and so a
    # policy hook judges the call the bridge will actually receive rather than the
    # signature's placeholders.
    params = {k: v for k, v in params.items() if v is not None}
    dispatched = _dispatch(canonical, params, state)
    result = _post_dispatch(canonical, params, dispatched)
    # A post-dispatch annotation must not turn a known failed call into MCP
    # success. Preserve the legacy text/structured payload and output schema;
    # only the protocol error bit changes for registered legacy bridge tools.
    failed = _failed_tool_result(dispatched) or _failed_tool_result(result)
    from .scene_contracts import REGISTRY
    if canonical in REGISTRY:
        from mcp.server.fastmcp.exceptions import ToolError
        if failed:
            raise ToolError(result)
    elif mcp_result and failed:
        from mcp.types import CallToolResult, TextContent
        return CallToolResult(
            content=[TextContent(type="text", text=result)],
            structuredContent={"result": result},
            isError=True,
        )
    return result


def _failed_tool_result(result: str) -> bool:
    """Recognize gateway failures and the bridge's explicit application errors.

    JSON strings (including a user script returning "Error: ..." as data),
    arrays, and successful objects containing an error-looking message remain
    ordinary results. This does not infer success from an arbitrary payload.
    """
    if result.startswith("Error:"):
        return True
    try:
        data = json.loads(result)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("status") == "error"


def _dispatch(canonical: str, params: Dict[str, Any],
              state: Optional[_BridgeState] = None) -> str:
    """Run the pre-dispatch hooks and, if none refused, the bridge call."""
    # Each hook may mutate params in place and/or return a refusal string to BLOCK
    # the call. A buggy hook degrades to an error string rather than crashing the
    # dispatch — and blocks, because a policy that did not run is not a policy that
    # allowed anything.
    for hook in _PRE_DISPATCH_HOOKS:
        try:
            blocked = hook(canonical, params)
        except Exception as e:
            hook_name = getattr(hook, "__name__", repr(hook))
            logger.error(f"Pre-dispatch hook {hook_name} raised for {canonical}: {e}")
            return f"Error: pre-dispatch hook {hook_name} failed: {e}"
        if blocked:
            return blocked

    try:
        active = state or _DEFAULT_STATE
        if canonical.startswith("assets."):
            from .project_assets import Broker, AssetError
            if not hasattr(active, "asset_broker"):
                active.asset_broker = Broker()
            broker = active.asset_broker
            try:
                if canonical == "assets.roots":
                    return json.dumps(broker.listing())
                if canonical == "assets.search":
                    return json.dumps(broker.search(**params))
                if canonical == "assets.scan":
                    return json.dumps(broker.scan(**params))
                if canonical in ("assets.info", "assets.import_usd", "assets.import_urdf", "assets.prepare_workstation", "assets.import_path"):
                    # Verify destination identity BEFORE reading/copying project files.
                    info = active.get_client().call("sim.stage.info")
                    if info.get("session_id") != params.get("session_id") or info.get("stage_id") != params.get("stage_id"):
                        raise AssetError("STALE_TARGET", "Review current_target and resend its IDs explicitly; no project was read and this call was not retargeted.",
                                         current_target={k: info.get(k) for k in ("session_id", "stage_id", "pid", "source", "default_prim", "meters_per_unit", "up_axis")})
                    if not broker.staging or str(broker.staging) != info.get("staging"):
                        raise AssetError("NO_SHARED_STAGING", "Host and Kit need the same user-approved staging mount/config. Remote upload is disabled.")
                    if canonical in ("assets.prepare_workstation", "assets.import_path"):
                        params = dict(params)
                        root_id, relative_path = broker.locate(params.pop("source_path"))
                    else:
                        root_id, relative_path = params["root_id"], params["relative_path"]
                    snapshot = broker.snapshot(root_id, relative_path, kind="urdf" if canonical=="assets.import_urdf" else "usd")
                    params = {**params, "snapshot_id": snapshot["snapshot_id"], "manifest_sha256": snapshot["manifest_sha256"]}
            except (AssetError, OSError) as e:
                return json.dumps(dict(status="error", code=getattr(e,"code","FILESYSTEM"), message=str(e), **getattr(e, "details", {})))
        result = active.get_client().call(canonical, params)
        return json.dumps(result, indent=2, default=repr)
    except BridgeError as e:
        if (str(e).startswith("unknown tool") and "known_tools" in e.details
                and host_catalog.is_catalog_tool(canonical)):
            logger.error(f"Bridge does not serve catalogued tool {canonical}: {e}")
            return _catalog_error(canonical, e, state)
        detail = f"\n{json.dumps(e.details, default=repr)[:2000]}" if e.details else ""
        logger.error(f"Bridge error in {canonical}: {e}")
        return f"Error: {e}{detail}"
    except (OSError, TimeoutError, RuntimeError) as e:
        logger.error(f"Error in {canonical}: {e}")
        # A retry can fail to connect after an earlier attempt reached Kit, so
        # connection guidance must not claim the tool was never executed.
        if _needs_bridge_guidance(e) and host_catalog.is_catalog_tool(canonical):
            return _catalog_error(canonical, e, state)
        return f"Error: {e}"


def _post_dispatch(canonical: str, params: Dict[str, Any], result: str) -> str:
    """Let each post-dispatch hook replace the result, in registration order.

    A hook that raises is reported and skipped, never allowed to cost the caller an
    answer the bridge already gave: an annotation failing is not the call failing."""
    for hook in _POST_DISPATCH_HOOKS:
        try:
            replacement = hook(canonical, params, result)
        except Exception as e:
            hook_name = getattr(hook, "__name__", repr(hook))
            logger.error(f"Post-dispatch hook {hook_name} raised for {canonical}: {e}")
            continue
        if replacement is not None:
            result = replacement
    return result


_UNSET = object()


def _meta_category(canonical: str) -> Optional[str]:
    """The _meta category the first policy hook assigns to a tool (failures are non-fatal)."""
    for hook in _META_CATEGORY_HOOKS:
        try:
            category = hook(canonical)
        except Exception as e:
            logger.error(f"Meta-category hook failed for {canonical}: {e}")
            category = None
        if category is not None:
            return category
    return None


def register_dynamic_tool(server: FastMCP, name: str, canonical: str,
                          meta: Dict[str, Any], state: Optional[_BridgeState] = None,
                          category: Any = _UNSET) -> None:
    description = meta.get("description", f"Execute {canonical} on the Kit bridge")
    if "fields" not in meta:
        raise RuntimeError(
            f"bridge served no field specs for {canonical!r} — the parameter "
            "schema is interpreted bridge-side (isaac_mcp_bridge.schema); update "
            "the isaac.mcp.bridge extension to match this gateway.")
    if state is None:
        state = _state_for(server)
    handler = _build_tool_function(name, canonical, description, meta["fields"],
                                   None if state is _DEFAULT_STATE else state)

    # Optional _meta category from policy hooks (failures are non-fatal).
    if category is _UNSET:
        category = _meta_category(canonical)

    options = {"meta": {"category": category}} if category is not None else {}
    from .scene_contracts import REGISTRY
    if canonical in REGISTRY:
        from mcp.types import ToolAnnotations
        read_only = canonical in {"assets.roots", "assets.scan", "sim.stage.info", "sim.stage.tree", "sim.prim.get", "sim.prim.find", "sim.physics.inspect", "sim.body.state", "sim.joints.inspect", "sim.joints.state", "sim.logs.tail", "assets.search", "sim.lidar.read", "sim.graph.node_types", "sim.graph.inspect", "sim.ui.find"}
        options["annotations"] = ToolAnnotations(readOnlyHint=read_only, destructiveHint=canonical in {"sim.prim.delete", "sim.stage.clear_owned", "sim.controller.create", "sim.controller.update", "sim.ui.act", "sim.stage.open", "sim.graph.build"},
                                                 idempotentHint=read_only, openWorldHint=canonical.startswith(("assets.", "sim.controller.")))
    server.tool(**options)(handler)


# ==================== Server ====================

@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    state = _state_for(server)
    try:
        logger.info("KitMCP server starting up")
        try:
            _sync_offline(server, state)   # policy hooks are registered by now
        except Exception as e:  # noqa: BLE001
            logger.error(f"Could not refresh the offline catalog tools: {e}")
        try:
            discover_and_register_tools(server)
        except Exception as e:
            logger.warning(f"Could not reach the Kit bridge on startup: {e}")
            logger.warning("Start Kit with isaac.mcp.bridge enabled, then call refresh_tools.")
        yield {}
    finally:
        if state.client:
            logger.info("Disconnecting from the Kit bridge")
            state.client.close()
            state.client = None
        logger.info("KitMCP server shut down")


def _advertise_tools_list_changed(server: FastMCP) -> None:
    """Make the server advertise ``tools.listChanged = true``.

    FastMCP.run_stdio_async calls ``create_initialization_options()`` with no
    arguments, so the capability stays false unless we supply the option.
    Clients such as Claude Code only act on ``notifications/tools/list_changed``
    when the capability was advertised at initialize. The wrapper passes the
    option by default but still honours an explicit ``notification_options``.
    """
    original = server._mcp_server.create_initialization_options

    def create_initialization_options(*args: Any, **kwargs: Any) -> Any:
        if not args and kwargs.get("notification_options") is None:
            kwargs["notification_options"] = NotificationOptions(tools_changed=True)
        return original(*args, **kwargs)

    server._mcp_server.create_initialization_options = create_initialization_options


async def _notify_tools_changed(ctx: Optional[Context]) -> None:
    """Tell the client its tool list changed. Never raises: a failed
    notification must not fail the tool that changed the registry.

    Tools take ``ctx: Context = None`` rather than ``Context | None`` because older SDK
    releases only inject a parameter annotated exactly ``Context`` (a union can be
    treated as an ordinary argument and leak into the advertised schema).
    ``ctx`` is None when a tool is called directly rather than through a session."""
    if ctx is None:
        return
    try:
        await ctx.request_context.session.send_tool_list_changed()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not send tools/list_changed notification: {e}")


def _tool_list_surface(server: FastMCP) -> Dict[str, Any]:
    """The client-visible registry, excluding handlers and bridge routing state."""
    return {name: _tool_surface(tool) for name, tool in server._tool_manager._tools.items()}


def _changed_tool_names(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def _screen_port(state: Optional[_BridgeState] = None) -> int | None:
    """Bridge port for auto-targeting, before or after the client connects."""
    client = (state or _DEFAULT_STATE).client
    if client is not None:
        return client.port
    raw = os.getenv("UR5E_DT_PORT") or os.getenv("ISAAC_MCP_PORT")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


async def _refresh_tools(server: FastMCP, ctx: Optional[Context] = None) -> str:
    try:
        added = discover_and_register_tools(server)
    except Exception as e:
        if isinstance(e, RuntimeError) and str(e).startswith("multiple bridges running"):
            return (f"Error: {e} Pick one with use_bridge(port) (`python -m isaac_mcp.client "
                    "--list-bridges` shows each one's port and PID); do not start another simulator.")
        if _needs_bridge_guidance(e):
            state = _state_for(server)
            managed = [p for p in live_ports(getattr(state, "env", None)) if p[0] != _screen_port(state)]
            hint = ("" if not managed else " Managed simulators started with sim__launch are running on "
                    + ", ".join(f"port {port} (PID {pid})" for port, pid in managed)
                    + ": this server dials its configured port, so call use_bridge(port) to select one.")
            return (f"Error: no Isaac Sim bridge is reachable ({e}).{hint} Isaac Sim is probably not "
                    "running yet, or is still starting: start it (sim__launch when simulator launch "
                    "is enabled, or ./run.sh), wait until it is ready, then call refresh_tools again. "
                    "`python -m isaac_mcp.client --list-bridges` lists running bridges.")
        return f"Error: {e}"
    if added:
        await _notify_tools_changed(ctx)
    return ((f"Registered/updated/removed {len(added)} tool(s): {added}" if added
             else "No new or changed tools (registry unchanged).") + _offline_note(server))


async def _use_bridge(server: FastMCP, port: int, ctx: Optional[Context] = None) -> str:
    state = _state_for(server)
    try:
        new_client = BridgeClient(port=int(port))
        identity = new_client.call("status")
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"
    before = _tool_list_surface(server)
    old_client = state.client
    state.client = new_client
    for name in list(state.name_map):
        _drop_tool(server, name)
    state.name_map.clear()
    state.registered_meta.clear()
    # The old bridge's catalog tools stay listed, as offline proxies, whatever the new one serves.
    _sync_offline(server, state)
    if old_client is not None:
        try:
            old_client.close()
        except Exception:  # noqa: BLE001
            pass
    try:
        discover_and_register_tools(server)
    except Exception as e:  # noqa: BLE001
        changed = _changed_tool_names(before, _tool_list_surface(server))
        if changed:
            await _notify_tools_changed(ctx)
        return (f"Connected to port {port} but tool discovery failed: {e}"
                + (f"\nChanged {len(changed)} tool(s): {changed}" if changed else "") + _offline_note(server))
    changed = _changed_tool_names(before, _tool_list_surface(server))
    if changed:
        await _notify_tools_changed(ctx)
    try:
        summary = json.dumps(identity, indent=2, sort_keys=True)
    except Exception:  # noqa: BLE001
        summary = str(identity)
    return (
        f"switched to bridge on port {port}\n{summary}\n"
        f"registered/updated/removed {len(changed)} tool(s)"
        + (f": {changed}" if changed else "") + _offline_note(server)
    )


def _register_gateway_tools(server: FastMCP, groups: Iterable[str]) -> None:
    """Register ``refresh_tools`` and ``use_bridge`` (the ``gateway`` group) on ``server``.

    Bridge discovery itself runs from the server lifespan, so omitting these two tools does not
    stop startup discovery; it only removes the client's way to re-run or redirect it."""
    mcp = ScopedTools(server, groups)

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def refresh_tools(ctx: Context = None) -> str:  # type: ignore[assignment]
        """Re-discover the bridge registry and register any new tools (use after
        the bridge's reload_tools, after enabling a project, or after Kit restarts)."""
        return await _refresh_tools(server, ctx)

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    async def use_bridge(port: int, ctx: Context = None) -> str:  # type: ignore[assignment]
        """Switch this MCP server to another running Kit bridge on this machine.

        To find the candidates, run `python -m isaac_mcp.client --list-bridges`. (`list_bridges`
        exists only as a bridge built-in: it is an MCP tool only when built-ins are enabled and a
        bridge is already reachable.) This replaces the bridge tools with the ones registered in
        that Kit process (the ur5e_dt__* tools stay listed even if it lacks the twin, but then
        return an error when called); screen tools are unaffected. The choice lasts until this MCP server
        restarts, then UR5E_DT_PORT (or ISAAC_MCP_PORT, or auto-discovery) decides again.
        """
        return await _use_bridge(server, port, ctx)


_RESTRICTED_NOTE = (
    " This server runs with a restricted set of its own local tools (ISAAC_MCP_LOCAL_TOOLS): rely "
    "on the tools it lists, and ignore guidance above about local tools that are not listed. The "
    "restriction does not apply to bridge tools: those discovered from a Kit bridge, or listed from "
    "the packaged catalog.")


def build_server(env: Optional[Mapping[str, str]] = None,
                 state: Optional[_BridgeState] = None) -> FastMCP:
    """Build a host server whose local tools are chosen by ``ISAAC_MCP_LOCAL_TOOLS`` in ``env``.

    ``env`` defaults to ``os.environ``. The groups are parsed once, here; an unknown group
    raises :class:`LocalToolsError` before anything is registered. The module-level ``mcp`` is
    ``build_server()``, so importing ``isaac_mcp.server`` with the variable unset registers the
    standard local tools. Desktop input requires the explicitly selected ``screen_input`` group.

    Each server gets its own bridge connection and discovery state (``state``, fresh unless the
    caller passes one), so servers built in one process neither share a socket nor skip tools
    because another server already discovered them. Only the module-level default ``mcp`` is
    wired to the module-level state. The policy hook lists are process-wide and shared."""
    env = os.environ if env is None else env
    groups = local_tool_groups(env)
    instructions = SERVER_INSTRUCTIONS
    if not DEFAULT_GROUPS <= groups and "ISAAC_MCP_INSTRUCTIONS" not in env:
        instructions += _RESTRICTED_NOTE
    if "screen_input" in groups and "ISAAC_MCP_INSTRUCTIONS" not in env:
        instructions += (" Desktop input is enabled on a shared X11 desktop. List the target "
                         "window and inspect a fresh screenshot before acting; use its window ID, "
                         "PID and original pixel size. Input can trigger arbitrary app actions.")
    if "sim_launch" in groups and "ISAAC_MCP_INSTRUCTIONS" not in env:
        instructions += (" Managed simulator launch is enabled. Use separate bridge ports and ROS "
                         "domains, confirm readiness and PID with sim__status, and treat resource "
                         "checks as snapshots rather than reservations.")
    state = _BridgeState() if state is None else state
    scene_option = env.get("ISAAC_MCP_SCENE_TOOLS", "1").strip().lower()
    if scene_option not in {"0", "false", "no", "1", "true", "yes"}:
        raise ValueError("ISAAC_MCP_SCENE_TOOLS must be 1/true/yes or 0/false/no")
    state.scene_tools_enabled = scene_option in {"1", "true", "yes"}
    from .project_assets import Broker
    state.asset_broker = Broker(env.get("ISAAC_MCP_ASSET_CONFIG", ""))
    if state.asset_broker.requested_profile != "trusted_project":
        raise RuntimeError("PROFILE_UNAVAILABLE: isolated/unrecognized profiles cannot start this trusted MCP server. No restricted endpoint or OS sandbox is implemented; no fallback.")
    server = FastMCP(SERVER_NAME, instructions=instructions, lifespan=server_lifespan)
    setattr(server, _STATE_ATTR, state)
    _advertise_tools_list_changed(server)
    register_screen_tools(server, port_provider=lambda: _screen_port(state), groups=groups)
    register_desktop_input_tools(server, groups=groups)
    state.env = env
    register_sim_session_tools(server, groups=groups, env=env)
    register_isaaclab_tools(server, groups=groups)
    _register_gateway_tools(server, groups)
    setattr(server, _OFFLINE_ATTR, _OfflineTools(host_catalog.load_catalog()))
    _sync_offline(server, state)
    return server


mcp = build_server(state=_DEFAULT_STATE)

# Direct-call entry points bound to the default server (scripts and tests call these as plain
# coroutines; the registered tools above are thin wrappers over the same functions).
refresh_tools = functools.partial(_refresh_tools, mcp)
use_bridge = functools.partial(_use_bridge, mcp)


def main(argv: list[str] | None = None) -> None:
    """Run the MCP server (stdio). `--help` and `--version` exit without starting it."""
    import argparse
    from importlib import metadata

    parser = argparse.ArgumentParser(
        prog="isaac-mcp",
        description="MCP server for Isaac Sim and Isaac Lab: Kit bridge tools plus desktop "
        "screen capture. Speaks MCP over stdio; configure it in your MCP client, do not run it by hand. "
        "Environment: UR5E_DT_PORT (bridge port, default 8766), DISPLAY (screen tools).",
    )
    try:
        version = metadata.version("isaac-mcp")
    except metadata.PackageNotFoundError:
        version = "unknown"
    parser.add_argument("--version", action="version", version=f"%(prog)s {version}")
    parser.parse_args(argv)
    mcp.run()


if __name__ == "__main__":
    main()

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

from .client import BridgeClient, BridgeError
from .isaaclab.tools import register_isaaclab_tools
from .local_tools import DEFAULT_GROUPS, IDEMPOTENT_WRITE, ScopedTools, local_tool_groups
from .desktop_input import register_desktop_input_tools
from .sim_sessions import register_sim_session_tools
from .screen import register_screen_tools

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("KitMCPServer")

SERVER_NAME = os.getenv("ISAAC_MCP_NAME", "isaac")
SERVER_INSTRUCTIONS = os.getenv(
    "ISAAC_MCP_INSTRUCTIONS",
    "Isaac Sim / Isaac Lab MCP server. Isaac Sim tools are discovered from the "
    "running Kit bridge. If only the host (screen and gateway) and isaaclab__* tools "
    "are listed, call refresh_tools first: the client may have connected before Kit "
    "started, or ISAAC_MCP_NAMESPACE / ISAAC_MCP_BUILTINS=0 may be hiding bridge "
    "tools. Find advertised bridges with the isaac_mcp.client CLI (--list-bridges) and "
    "confirm the chosen bridge's PID and port (status) before scene-changing calls, "
    "and start an additional Kit only when explicitly requested and resource checks permit. "
    "screenshot, list_windows, raise_window and the screen "
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
    list is gating nothing. Empty before discovery has run, which is itself worth knowing —
    checking a policy against a roster nobody has filled would clear every name in it."""
    return sorted(_state_for(server).name_map.values())


def _mcp_name(canonical: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", canonical.replace(".", "__"))


def _visible(canonical: str, meta: Dict[str, Any]) -> bool:
    """Apply the view filters: namespace allowlist + builtins gate."""
    namespace = meta.get("namespace")
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
    result = state.get_client().call("list_tools")
    if not isinstance(result, dict) or not isinstance(result.get("tools"), dict):
        raise RuntimeError("bridge list_tools response has no tools dictionary")
    if any(not isinstance(name, str) or not isinstance(meta, dict)
           for name, meta in result["tools"].items()):
        raise RuntimeError("bridge list_tools contains an invalid tool entry")
    tools = {name: meta for name, meta in result["tools"].items() if _visible(name, meta)}
    names = {}
    for canonical, meta in tools.items():
        if "fields" not in meta:
            raise RuntimeError(f"bridge tool {canonical!r} has no field specs")
        name = _mcp_name(canonical)
        if name in names and names[name] != canonical:
            raise RuntimeError(f"bridge tools {names[name]!r} and {canonical!r} collide as {name!r}")
        if name in server._tool_manager._tools and name not in state.name_map:
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
    for mcp_name, canonical in list(state.name_map.items()):
        if canonical not in tools:
            _drop_tool(server, mcp_name)
            state.name_map.pop(mcp_name, None)
            state.registered_meta.pop(mcp_name, None)
            changed.append(f"{canonical} (removed)")
    for name, (canonical, meta, tool) in staged.items():
        changed.append(f"{canonical} (updated)" if name in state.name_map else canonical)
        server._tool_manager._tools[name] = tool
        state.name_map[name] = canonical
        state.registered_meta[name] = meta
    if changed:
        logger.info(f"Registered/updated {len(changed)} bridge tools: {changed}")
    return changed


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
                      else _KIND_ANNOTATIONS[f["kind"]])
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
    local_ns = {"_tool_impl": (_tool_implementation if state is None
                               else functools.partial(_tool_implementation, state=state))}
    exec(func_code, local_ns)
    func = local_ns[name]
    func.__doc__ = description
    func.__annotations__.update(annotations)
    return func


def _tool_implementation(canonical: str, params: Dict[str, Any],
                         state: Optional[_BridgeState] = None) -> str:
    """Shared implementation for all dynamic tools.

    Every dynamically registered bridge tool called through this host crosses both
    policy hook lists. Direct bridge socket callers bypass these host hooks. The
    result a post-dispatch hook sees is the one the caller would have received,
    whatever produced it."""
    # Drop unset optional params so bridge-side handler defaults apply — and so a
    # policy hook judges the call the bridge will actually receive rather than the
    # signature's placeholders.
    params = {k: v for k, v in params.items() if v is not None}
    return _post_dispatch(canonical, params, _dispatch(canonical, params, state))


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
        result = (state or _DEFAULT_STATE).get_client().call(canonical, params)
        return json.dumps(result, indent=2, default=repr)
    except BridgeError as e:
        detail = f"\n{json.dumps(e.details, default=repr)[:2000]}" if e.details else ""
        logger.error(f"Bridge error in {canonical}: {e}")
        return f"Error: {e}{detail}"
    except (OSError, TimeoutError, RuntimeError) as e:
        logger.error(f"Error in {canonical}: {e}")
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


def register_dynamic_tool(server: FastMCP, name: str, canonical: str,
                          meta: Dict[str, Any], state: Optional[_BridgeState] = None) -> None:
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
    category = None
    for hook in _META_CATEGORY_HOOKS:
        try:
            category = hook(canonical)
        except Exception as e:
            logger.error(f"Meta-category hook failed for {canonical}: {e}")
            category = None
        if category is not None:
            break

    if category is not None:
        server.tool(meta={"category": category})(handler)
    else:
        server.tool()(handler)


# ==================== Server ====================

@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    state = _state_for(server)
    try:
        logger.info("KitMCP server starting up")
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
        return f"Error: {e}"
    if added:
        await _notify_tools_changed(ctx)
    return (f"Registered/updated/removed {len(added)} tool(s): {added}" if added
            else "No new or changed tools (registry unchanged).")


async def _use_bridge(server: FastMCP, port: int, ctx: Optional[Context] = None) -> str:
    state = _state_for(server)
    try:
        new_client = BridgeClient(port=int(port))
        identity = new_client.call("status")
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"
    old_client = state.client
    state.client = new_client
    had_tools = bool(state.name_map)
    for name in list(state.name_map):
        _drop_tool(server, name)
    state.name_map.clear()
    state.registered_meta.clear()
    if old_client is not None:
        try:
            old_client.close()
        except Exception:  # noqa: BLE001
            pass
    try:
        added = discover_and_register_tools(server)
    except Exception as e:  # noqa: BLE001
        if had_tools:  # the previous bridge's tools were dropped above
            await _notify_tools_changed(ctx)
        return f"Connected to port {port} but tool discovery failed: {e}"
    if had_tools or added:
        await _notify_tools_changed(ctx)
    try:
        summary = json.dumps(identity, indent=2, sort_keys=True)
    except Exception:  # noqa: BLE001
        summary = str(identity)
    return (
        f"switched to bridge on port {port}\n{summary}\n"
        f"registered/updated {len(added)} tool(s)"
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
        bridge is already reachable.) This replaces the ur5e_dt__* tools with the ones registered
        in that Kit process; screen tools are unaffected. The choice lasts until this MCP server
        restarts, then UR5E_DT_PORT (or ISAAC_MCP_PORT, or auto-discovery) decides again.
        """
        return await _use_bridge(server, port, ctx)


_RESTRICTED_NOTE = (
    " This server runs with a restricted set of its own local tools (ISAAC_MCP_LOCAL_TOOLS): rely "
    "on the tools it lists, and ignore guidance above about local tools that are not listed. The "
    "restriction does not apply to tools discovered from a Kit bridge.")


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
    server = FastMCP(SERVER_NAME, instructions=instructions, lifespan=server_lifespan)
    setattr(server, _STATE_ATTR, state)
    _advertise_tools_list_changed(server)
    register_screen_tools(server, port_provider=lambda: _screen_port(state), groups=groups)
    register_desktop_input_tools(server, groups=groups)
    register_sim_session_tools(server, groups=groups, env=env)
    register_isaaclab_tools(server, groups=groups)
    _register_gateway_tools(server, groups)
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

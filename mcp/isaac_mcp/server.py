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

import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.lowlevel.server import NotificationOptions

from .client import BridgeClient, BridgeError
from .isaaclab.tools import register_isaaclab_tools
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
    "and start Kit with the isaac.mcp.bridge extension only when none is advertised. "
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
# envelope decoding, health checks, and the reconnect-retry are its job. The
# gateway holds one persistent BridgeClient.

_client: Optional[BridgeClient] = None


def get_client() -> BridgeClient:
    global _client
    if _client is None:
        # host/port unset -> the client resolves the endpoint ($ISAAC_MCP_HOST /
        # $ISAAC_MCP_PORT, else portfile discovery) — one resolution path.
        _client = BridgeClient(timeout=RESPONSE_TIMEOUT_S)
    return _client


# ==================== Dynamic tool registration ====================

# MCP-facing name -> canonical bridge name (dots are illegal in MCP tool names).
_NAME_MAP: Dict[str, str] = {}
# MCP-facing name -> the bridge meta it was registered with, so rediscovery can
# detect schema/description changes (reload_tools on the bridge side) and
# re-register instead of silently serving a stale schema.
_REGISTERED_META: Dict[str, Dict[str, Any]] = {}


def registered_tools() -> List[str]:
    """The canonical bridge names currently registered, sorted.

    The roster a policy's matchers can be checked against: a gate naming a tool this does not
    list is gating nothing. Empty before discovery has run, which is itself worth knowing —
    checking a policy against a roster nobody has filled would clear every name in it."""
    return sorted(_NAME_MAP.values())


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
    result = get_client().call("list_tools")
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
        if name in server._tool_manager._tools and name not in _NAME_MAP:
            raise RuntimeError(f"bridge tool {canonical!r} collides with host tool {name!r}")
        names[name] = canonical

    # Build every changed tool on a temporary FastMCP registry first. A broken
    # field spec or signature must not remove an existing tool or leave the
    # host with only the first part of the new registry.
    staging = FastMCP("bridge discovery staging")
    staged = {}
    for canonical, meta in sorted(tools.items()):
        name = _mcp_name(canonical)
        if _NAME_MAP.get(name) == canonical and _REGISTERED_META.get(name) == meta:
            continue
        try:
            register_dynamic_tool(staging, name, canonical, meta)
        except Exception as error:
            raise RuntimeError(f"invalid bridge tool {canonical!r}: {error}") from error
        staged[name] = (canonical, meta, staging._tool_manager._tools[name])

    changed = []
    for mcp_name, canonical in list(_NAME_MAP.items()):
        if canonical not in tools:
            _drop_tool(server, mcp_name)
            _NAME_MAP.pop(mcp_name, None)
            _REGISTERED_META.pop(mcp_name, None)
            changed.append(f"{canonical} (removed)")
    for name, (canonical, meta, tool) in staged.items():
        changed.append(f"{canonical} (updated)" if name in _NAME_MAP else canonical)
        server._tool_manager._tools[name] = tool
        _NAME_MAP[name] = canonical
        _REGISTERED_META[name] = meta
    if changed:
        logger.info(f"Registered/updated {len(changed)} bridge tools: {changed}")
    return changed


_KIND_ANNOTATIONS = {"boolean": bool, "integer": int, "number": float,
                     "array": list, "object": dict, "string": str}


def _build_tool_function(name: str, canonical: str, description: str,
                         fields: List[Dict[str, Any]]):
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
    local_ns = {"_tool_impl": _tool_implementation}
    exec(func_code, local_ns)
    func = local_ns[name]
    func.__doc__ = description
    func.__annotations__.update(annotations)
    return func


def _tool_implementation(canonical: str, params: Dict[str, Any]) -> str:
    """Shared implementation for all dynamic tools.

    Every call crosses both policy hook lists, which is the point of putting them
    here: this is where every path lands, raw-socket callers included. The result
    a post-dispatch hook sees is the one the caller would have received, whatever
    produced it."""
    # Drop unset optional params so bridge-side handler defaults apply — and so a
    # policy hook judges the call the bridge will actually receive rather than the
    # signature's placeholders.
    params = {k: v for k, v in params.items() if v is not None}
    return _post_dispatch(canonical, params, _dispatch(canonical, params))


def _dispatch(canonical: str, params: Dict[str, Any]) -> str:
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
        result = get_client().call(canonical, params)
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
                          meta: Dict[str, Any]) -> None:
    description = meta.get("description", f"Execute {canonical} on the Kit bridge")
    if "fields" not in meta:
        raise RuntimeError(
            f"bridge served no field specs for {canonical!r} — the parameter "
            "schema is interpreted bridge-side (isaac_mcp_bridge.schema); update "
            "the isaac.mcp.bridge extension to match this gateway.")
    handler = _build_tool_function(name, canonical, description, meta["fields"])

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
    try:
        logger.info("KitMCP server starting up")
        try:
            discover_and_register_tools(server)
        except Exception as e:
            logger.warning(f"Could not reach the Kit bridge on startup: {e}")
            logger.warning("Start Kit with isaac.mcp.bridge enabled, then call refresh_tools.")
        yield {}
    finally:
        global _client
        if _client:
            logger.info("Disconnecting from the Kit bridge")
            _client.close()
            _client = None
        logger.info("KitMCP server shut down")


mcp = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS, lifespan=server_lifespan)


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


_advertise_tools_list_changed(mcp)


async def _notify_tools_changed(ctx: Optional[Context]) -> None:
    """Tell the client its tool list changed. Never raises: a failed
    notification must not fail the tool that changed the registry.

    Tools take ``ctx: Context = None`` rather than ``Context | None``: mcp 1.12.4
    only injects a parameter annotated exactly ``Context`` (a union is treated as
    an ordinary argument and leaks into the advertised schema); 1.30 accepts both.
    ``ctx`` is None when a tool is called directly rather than through a session."""
    if ctx is None:
        return
    try:
        await ctx.request_context.session.send_tool_list_changed()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not send tools/list_changed notification: {e}")


def _screen_port() -> int | None:
    """Bridge port for auto-targeting, before or after the client connects."""
    if _client is not None:
        return _client.port
    raw = os.getenv("UR5E_DT_PORT") or os.getenv("ISAAC_MCP_PORT")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


register_screen_tools(mcp, port_provider=_screen_port)
register_isaaclab_tools(mcp)


@mcp.tool()
async def refresh_tools(ctx: Context = None) -> str:  # type: ignore[assignment]
    """Re-discover the bridge registry and register any new tools (use after
    the bridge's reload_tools, after enabling a project, or after Kit restarts)."""
    try:
        added = discover_and_register_tools(mcp)
    except Exception as e:
        return f"Error: {e}"
    if added:
        await _notify_tools_changed(ctx)
    return (f"Registered/updated/removed {len(added)} tool(s): {added}" if added
            else "No new or changed tools (registry unchanged).")


@mcp.tool()
async def use_bridge(port: int, ctx: Context = None) -> str:  # type: ignore[assignment]
    """Switch this MCP server to another running Kit bridge on this machine.

    Use list_bridges to see the candidates. This replaces the ur5e_dt__* tools
    with the ones registered in that Kit process; screen tools are unaffected.
    The choice lasts until this MCP server restarts, then UR5E_DT_PORT (or
    ISAAC_MCP_PORT, or auto-discovery) decides again.
    """
    global _client
    try:
        new_client = BridgeClient(port=int(port))
        identity = new_client.call("status")
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"
    old_client = _client
    _client = new_client
    had_tools = bool(_NAME_MAP)
    for name in list(_NAME_MAP):
        _drop_tool(mcp, name)
    _NAME_MAP.clear()
    _REGISTERED_META.clear()
    if old_client is not None:
        try:
            old_client.close()
        except Exception:  # noqa: BLE001
            pass
    try:
        added = discover_and_register_tools(mcp)
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

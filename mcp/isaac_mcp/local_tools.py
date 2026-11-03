"""Which of the host's local tools a server instance registers, and how each is annotated.

The host registers its own tools (the screen tools, the Isaac Lab pack and the two gateway
tools) in addition to whatever it discovers from a Kit bridge. ``ISAAC_MCP_LOCAL_TOOLS`` picks
which *groups* of those local tools a process registers. Desktop input and simulator launch are opt-in.

    ISAAC_MCP_LOCAL_TOOLS   comma-separated groups (see GROUPS) and aliases (see ALIASES).
                            Unset or empty => "all". An unknown name is a startup error.

This limits only the host's own local tools. It does NOT limit the tools discovered from a Kit
bridge (``run_python``, ``ur5e_dt__*``, ...), which are not annotated and can change the scene, so
``readonly`` is a read-only *local* profile, not a read-only server. ``ISAAC_MCP_NAMESPACE`` and
``ISAAC_MCP_BUILTINS`` filter the bridge tools and compose with this variable; they never affect
the local tools. Restricting the bridge's tools so they cannot change the scene takes a pre-dispatch
policy (see docs/MCP-SERVER.md, "Limiting what a client can reach"), which is code today.

This module is the single place that says which tool belongs to which group, so a tool that is
registered without being listed here (or without annotations) fails loudly at registration
instead of silently escaping the filter.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Optional

from mcp.types import ToolAnnotations

ENV_VAR = "ISAAC_MCP_LOCAL_TOOLS"

GROUPS: dict[str, frozenset[str]] = {
    "isaaclab_read": frozenset({
        "isaaclab__checkpoints_list", "isaaclab__metrics_summary", "isaaclab__tensorboard_tail",
    }),
    "isaaclab_probe": frozenset({
        "isaaclab__install_info", "isaaclab__envs_list",
    }),
    "isaaclab_jobs": frozenset({
        "isaaclab__runs_list", "isaaclab__runs_status", "isaaclab__runs_tail",
    }),
    "isaaclab_run": frozenset({
        "isaaclab__train", "isaaclab__eval", "isaaclab__play", "isaaclab__runs_cancel",
    }),
    "screen_read": frozenset({"screen_info", "list_windows", "screen_recording_status"}),
    "screen_capture": frozenset({"screenshot", "start_screen_recording", "stop_screen_recording"}),
    "screen_control": frozenset({"raise_window", "move_window"}),
    "screen_input": frozenset({
        "window_click", "window_drag", "window_scroll", "window_type", "window_key",
    }),
    "sim_launch": frozenset({"sim__launch", "sim__list", "sim__status", "sim__stop"}),
    "gateway": frozenset({"refresh_tools", "use_bridge"}),
}

# Groups whose tools only read state (readOnlyHint=true). Every other group changes something.
READ_ONLY_GROUPS: frozenset[str] = frozenset({"isaaclab_read", "screen_read"})

ALIASES: dict[str, tuple[str, ...]] = {
    # Desktop input and process launch must be explicitly selected.
    "all": tuple(group for group in GROUPS if group not in {"screen_input", "sim_launch"}),
    "readonly": tuple(sorted(READ_ONLY_GROUPS)),
    "none": (),
}

ALL_GROUPS: frozenset[str] = frozenset(GROUPS)
ALL_TOOLS: frozenset[str] = frozenset().union(*GROUPS.values())
DEFAULT_GROUPS: frozenset[str] = frozenset(ALIASES["all"])
DEFAULT_TOOLS: frozenset[str] = frozenset().union(*(GROUPS[g] for g in DEFAULT_GROUPS))
READ_ONLY_TOOLS: frozenset[str] = frozenset().union(*(GROUPS[g] for g in READ_ONLY_GROUPS))

_TOOL_GROUP = {tool: group for group, tools in GROUPS.items() for tool in tools}


class LocalToolsError(ValueError):
    """``ISAAC_MCP_LOCAL_TOOLS`` could not be understood."""


def parse_local_tools(raw: Optional[str]) -> frozenset[str]:
    """Resolve the variable's value to the set of enabled group names.

    Unset, empty or whitespace means ``all``. Names are case-insensitive and may mix groups and
    aliases (``readonly,gateway``). An unknown name raises :class:`LocalToolsError`; it never
    quietly registers nothing.
    """
    items = [item.strip().lower() for item in (raw or "").split(",") if item.strip()]
    if not items:
        items = ["all"]
    enabled: set[str] = set()
    unknown = []
    for item in items:
        if item in ALIASES:
            enabled.update(ALIASES[item])
        elif item in GROUPS:
            enabled.add(item)
        else:
            unknown.append(item)
    if unknown:
        raise LocalToolsError(
            f"{ENV_VAR}: unknown group{'s' if len(unknown) > 1 else ''} "
            f"{', '.join(repr(u) for u in unknown)}. "
            f"Valid groups: {', '.join(GROUPS)}. Aliases: {', '.join(ALIASES)}."
        )
    return frozenset(enabled)


def local_tool_groups(env: Optional[Mapping[str, str]] = None) -> frozenset[str]:
    """The enabled groups for an environment mapping (default: ``os.environ``)."""
    if env is None:
        import os

        env = os.environ
    return parse_local_tools(env.get(ENV_VAR))


# ---- Annotations -------------------------------------------------------------------------
# One preset per behaviour class. Configured Python interpreters and task modules can run
# arbitrary code, so their effects cannot be assumed closed-world or idempotent.

# Reads state only. destructiveHint is meaningless when readOnlyHint is true, so it is unset.
READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
# Changes something additively, such as writing a capture file.
ADDITIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                           openWorldHint=False)
# Changes state, but repeating the call with the same arguments has no further effect.
IDEMPOTENT_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                                   openWorldHint=False)
# Kills a process or overwrites job metadata during orphan reconciliation.
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True,
                              openWorldHint=False)
# Executes configured Python and potentially imports user-configured task modules.
PYTHON_EXECUTION = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False,
                                   openWorldHint=True)
# Desktop input can trigger arbitrary actions in the selected app, including external effects.
DESKTOP_INPUT = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False,
                                openWorldHint=True)


class ScopedTools:
    """Wraps a FastMCP server so ``@tools.tool(...)`` registers a tool only if its group is on.

    Registration code stays an ordinary ``@mcp.tool(name=..., annotations=...)`` decorator on a
    function. A tool that is not in :data:`GROUPS` or has no annotations raises at registration, so
    adding an ungrouped or unannotated tool cannot go unnoticed.
    """

    def __init__(self, server: Any, groups: Optional[Iterable[str]] = None):
        self._server = server
        self.groups = DEFAULT_GROUPS if groups is None else frozenset(groups)
        unknown = self.groups - ALL_GROUPS
        if unknown:
            raise LocalToolsError(f"unknown local tool groups: {sorted(unknown)}")

    def enabled(self, group: str) -> bool:
        return group in self.groups

    def tool(self, name: Optional[str] = None, **kwargs: Any) -> Callable[[Callable], Callable]:
        def decorator(fn: Callable) -> Callable:
            tool_name = name or fn.__name__
            group = _TOOL_GROUP.get(tool_name)
            if group is None:
                raise LocalToolsError(
                    f"local tool {tool_name!r} is not in any group of isaac_mcp.local_tools.GROUPS")
            if kwargs.get("annotations") is None:
                raise LocalToolsError(f"local tool {tool_name!r} has no ToolAnnotations")
            if group in self.groups:
                self._server.tool(name=name, **kwargs)(fn)
            return fn

        return decorator

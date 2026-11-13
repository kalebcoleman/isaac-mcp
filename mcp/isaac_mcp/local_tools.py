"""Annotation presets and toolset-scoped host registration."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Iterable, Optional

from mcp.types import ToolAnnotations

from .toolsets import DEFAULT, TOOLSETS, owner


class LocalToolsError(ValueError):
    """A local tool has no ownership or annotations."""


# ---- Annotations -------------------------------------------------------------------------
# One preset per behaviour class. Configured Python interpreters and task modules can run
# arbitrary code, so their effects cannot be assumed closed-world or idempotent.

# Reads state only. destructiveHint is meaningless when readOnlyHint is true, so it is unset.
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
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


def strip_schema_titles(server: Any, name: str) -> None:
    """Drop the titles pydantic generates (``"title": "Window Id"`` for ``window_id``) from a listed
    schema. They repeat the name in every client's context. Validation does not read this copy."""
    tool = getattr(getattr(server, "_tool_manager", None), "_tools", {}).get(name)
    if tool is None:
        return

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            # A parameter named "title" holds a dict here; only a string is pydantic's.
            return {k: clean(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
        return [clean(v) for v in node] if isinstance(node, list) else node

    tool.parameters = clean(tool.parameters)


class ScopedTools:
    """Wraps a FastMCP server so ``@tools.tool(...)`` registers a tool only if its toolset is on.

    Registration code stays an ordinary ``@mcp.tool(name=..., annotations=...)`` decorator on a
    function. A tool that is not in the toolset registry or has no annotations raises at registration, so
    adding an unowned or unannotated tool cannot go unnoticed.
    """

    def __init__(self, server: Any, groups: Optional[Iterable[str]] = None):
        self._server = server
        self.groups = DEFAULT if groups is None else frozenset(groups)
        unknown = self.groups - TOOLSETS.keys()
        if unknown:
            raise LocalToolsError(f"unknown toolsets: {sorted(unknown)}")

    def enabled(self, group: str) -> bool:
        return group in self.groups

    def tool(self, name: Optional[str] = None, **kwargs: Any) -> Callable[[Callable], Callable]:
        def decorator(fn: Callable) -> Callable:
            tool_name = name or fn.__name__
            try:
                group = owner(tool_name)
            except ValueError as error:
                raise LocalToolsError(str(error)) from error
            if kwargs.get("annotations") is None:
                raise LocalToolsError(f"local tool {tool_name!r} has no ToolAnnotations")
            if group in self.groups:
                # FastMCP lists a docstring as written; the indentation only costs client context.
                options = {"description": inspect.cleandoc(fn.__doc__ or ""), **kwargs}
                self._server.tool(name=name, **options)(fn)
                strip_schema_titles(self._server, tool_name)
            return fn

        return decorator

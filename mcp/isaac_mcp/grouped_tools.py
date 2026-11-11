"""Build the grouped tools a client is shown from the single-purpose tools behind them.

A grouped tool takes ``action`` and the arguments of the tool that action runs, checks that the
arguments belong to that action, and calls it; the answer is that tool's own. Its schema is the
union of its members' schemas, with one description per argument, so arguments most members share
(``session_id``, ``path``) are described once instead of once per tool. ``action="help"`` returns
the members' full descriptions for when the summary is not enough.

Groups are rebuilt whenever the registered tools change (a bridge discovered, a switch of bridge),
so a group only offers the actions whose tools are there.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import tool_groups

MAX_ARGUMENT_DESCRIPTION = 450
# A member's own argument called "action" (sim__ui__act has one) is offered under this name.
OWN_ACTION = "sub_action"
_STATE = "_isaac_grouped_tools"
_SENTENCE = re.compile(r"(?<![ (]e\.g)(?<![ (]i\.e)[.;:]\s+(?=[A-Z`'\"(])")


def listed_singly(env: Optional[Dict[str, str]] = None) -> bool:
    """Whether ISAAC_MCP_GROUP_TOOLS=0 asks for every tool to be listed singly, with no groups."""
    value = (env or {}).get(tool_groups.GROUP_ENV) or os.environ.get(tool_groups.GROUP_ENV, "1")
    return str(value).strip().lower() in ("0", "false", "no", "off")


def _brief(text: str, limit: int = 110) -> str:
    """The first sentence of a description, cut at a word if it is long."""
    first = _SENTENCE.split(" ".join((text or "").split()), 1)[0].rstrip(".:;")
    return first if len(first) <= limit else first[:limit].rsplit(" ", 1)[0].rstrip(",;:") + " ..."


def _argument(name: str, uses: Dict[str, Dict[str, Any]], actions: List[str]) -> Dict[str, Any]:
    """One argument of a group: the schema its members give it (either of them, where two actions
    take different kinds of value under one name), and a description that says which actions take
    it when not all do, and how its meaning differs when it does."""
    kinds_seen: List[Dict[str, Any]] = []
    for spec in uses.values():
        kind = _kind(spec)
        if kind not in kinds_seen:
            kinds_seen.append(kind)
    schema = dict(kinds_seen[0]) if len(kinds_seen) == 1 else {"anyOf": kinds_seen}
    texts: Dict[str, List[str]] = {}
    for action, spec in uses.items():
        texts.setdefault(tool_groups.listed_form(" ".join(str(spec.get("description") or "").split())), []).append(action)
    scope = "" if len(uses) == len(actions) else f"[{', '.join(uses)}] "
    if len(texts) == 1:
        description = scope + next(iter(texts))
    else:
        room = max(40, (MAX_ARGUMENT_DESCRIPTION - len(scope)) // len(texts) - 16)
        description = scope + " ".join(f"{'/'.join(who)}: {text if len(text) <= room else text[:room].rsplit(' ', 1)[0] + '...'}"
                                       for text, who in texts.items())
    schema["description"] = description[:MAX_ARGUMENT_DESCRIPTION]
    return schema


def _kind(spec: Dict[str, Any]) -> Dict[str, Any]:
    """An argument's schema without its description and default, and without "or null": every
    argument of a grouped tool is optional anyway."""
    schema = {key: value for key, value in spec.items() if key not in ("description", "default")}
    kinds = [kind for kind in schema.get("anyOf", []) if kind != {"type": "null"}]
    if len(kinds) == 1 and len(schema.get("anyOf", [])) == 2:
        schema = {**{key: value for key, value in schema.items() if key != "anyOf"}, **kinds[0]}
    return schema


def _annotations(tools: List[Any]) -> ToolAnnotations:
    hints = [tool.annotations for tool in tools if tool.annotations is not None]
    return ToolAnnotations(
        readOnlyHint=bool(hints) and len(hints) == len(tools) and all(h.readOnlyHint for h in hints),
        destructiveHint=any(h.destructiveHint for h in hints),
        idempotentHint=bool(hints) and len(hints) == len(tools) and all(h.idempotentHint for h in hints),
        openWorldHint=any(h.openWorldHint for h in hints))


def _build(server: Any, group: str, summary: str, actions: Dict[str, str]) -> None:
    """Register one grouped tool over the members that are registered now."""
    registered = server._tool_manager._tools
    present = {action: registered[tool] for action, tool in actions.items() if tool in registered}
    names = list(present)
    arguments: Dict[str, Dict[str, Dict[str, Any]]] = {}
    lines = []
    for action, tool in present.items():
        properties = tool.parameters.get("properties", {})
        for name, spec in properties.items():
            arguments.setdefault(OWN_ACTION if name == "action" else name, {})[action] = spec
        needs = [OWN_ACTION if name == "action" else name for name in tool.parameters.get("required", [])
                 if name not in ("session_id", "stage_id")]
        brief = _brief(tool_groups.listed_form(tool.description or ""))
        lines.append(f"- {action}: {brief}{'' if brief.endswith('...') else '.'}" + (f" Needs {', '.join(needs)}." if needs else ""))
    takes = {action: {OWN_ACTION if name == "action" else name for name in tool.parameters.get("properties", {})}
             for action, tool in present.items()}
    signature = ", ".join(f"{name}=None" for name in arguments)
    namespace: Dict[str, Any] = {}

    async def run(action: str, given: Dict[str, Any]) -> Any:
        if action == "help":
            return json.dumps({name: {"description": tool.description,
                                      "arguments": tool.parameters.get("properties", {}),
                                      "required": tool.parameters.get("required", [])}
                               for name, tool in present.items()}, indent=1)
        if action not in present:
            raise ToolError(f"{group}: action must be one of {', '.join(names)} (or help), not {action!r}.")
        given = {name: value for name, value in given.items() if value is not None}
        stray = sorted(set(given) - takes[action])
        if stray:
            raise ToolError(f"{group}(action={action!r}) does not take {', '.join(stray)}. It takes: "
                            f"{', '.join(sorted(takes[action])) or 'no arguments'}. Nothing ran.")
        if OWN_ACTION in given:
            given["action"] = given.pop(OWN_ACTION)
        try:
            return await server._tool_manager.call_tool(present[action].name, given)
        except ToolError as error:     # said once, under the grouped tool's name
            raise ToolError(re.sub(r"^Error executing tool [^:]+: ", "", str(error), count=1)) from None

    exec(f"async def {group}(action{', ' if signature else ''}{signature}):\n"
         f"    given = dict(locals())\n    given.pop('action')\n    return await _run(action, given)\n",
         {"_run": run}, namespace)
    function = namespace[group]
    # Text arguments are typed as text, as on the single-purpose tools: FastMCP would otherwise
    # read a string that looks like JSON ('{"a": 1}', 'null') as JSON before the tool sees it.
    function.__annotations__ = {"action": str, **{
        name: str for name, uses in arguments.items()     # exactly str: FastMCP parses anything else
        if all(_kind(spec).get("type") == "string" and "enum" not in _kind(spec) for spec in uses.values())}}
    function.__doc__ = f"{summary} action is one of:\n" + "\n".join(lines) + "\n- help: the full description of each action."
    registered.pop(group, None)
    server.tool(annotations=_annotations(list(present.values())))(function)
    tool = registered[group]
    tool.parameters = {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": names + ["help"], "description": "What to do."},
                       **{name: _argument(name, uses, names) for name, uses in arguments.items()}},
        "required": ["action"],
        "additionalProperties": False,
    }
    model = tool.fn_metadata.arg_model
    model.model_config["extra"] = "forbid"
    model.model_rebuild(force=True)


def _in_listed_form(value: Any) -> Any:
    """A tool's answer, in any of the shapes FastMCP hands back, with the single-purpose tool names
    in its text replaced by what the client can call."""
    if isinstance(value, str):
        return tool_groups.listed_form(value)
    if isinstance(value, tuple):
        return tuple(_in_listed_form(item) for item in value)
    if isinstance(value, dict):
        return {key: _in_listed_form(item) if isinstance(item, str) else item for key, item in value.items()}
    if isinstance(value, list):
        blocks = value
    else:
        blocks = getattr(value, "content", None) or []
        if isinstance(getattr(value, "structuredContent", None), dict):
            value.structuredContent = _in_listed_form(value.structuredContent)
    for block in blocks:
        if isinstance(getattr(block, "text", None), str):
            block.text = tool_groups.listed_form(block.text)
    return value


def _describe_in_listed_form(tool: Any) -> None:
    tool.description = tool_groups.listed_form(tool.description or "")
    for spec in (tool.parameters or {}).get("properties", {}).values():
        if isinstance(spec.get("description"), str):
            spec["description"] = tool_groups.listed_form(spec["description"])


def sync(server: Any, env: Optional[Dict[str, str]] = None) -> None:
    """Make the grouped tools and the listing match the tools registered now. With groups listed,
    what the server says (descriptions, answers, errors) names tools in their listed form."""
    manager = server._tool_manager
    for group in tool_groups.GROUPS:
        manager._tools.pop(group, None)
    names = set(manager._tools)
    singly = listed_singly(env)
    for group, (summary, actions) in tool_groups.GROUPS.items():
        if not singly and names & set(actions.values()):
            _build(server, group, summary, actions)
    hide = set() if singly else tool_groups.hidden(names)
    if not hasattr(server, _STATE):
        everything, call = manager.list_tools, manager.call_tool
        setattr(server, _STATE, {"hide": hide, "singly": singly})

        async def call_tool(name, arguments, *args, **kwargs):
            if getattr(server, _STATE)["singly"]:
                return await call(name, arguments, *args, **kwargs)
            if name in tool_groups.VERBATIM:      # the caller's own data, code or log, in answers and errors alike
                return await call(name, arguments, *args, **kwargs)
            try:
                return _in_listed_form(await call(name, arguments, *args, **kwargs))
            except ToolError as error:
                raise ToolError(tool_groups.listed_form(str(error))) from None

        manager.list_tools = lambda: [tool for tool in everything() if tool.name not in getattr(server, _STATE)["hide"]]
        manager.call_tool = call_tool
    getattr(server, _STATE).update(hide=hide, singly=singly)
    if not singly:
        for name, tool in manager._tools.items():
            if name not in hide:
                _describe_in_listed_form(tool)


def listed_names(server: Any) -> List[str]:
    return [tool.name for tool in server._tool_manager.list_tools()]

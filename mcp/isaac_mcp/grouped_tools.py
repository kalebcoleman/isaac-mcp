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

# What a client is shown. The full text stays available: action="help" on a grouped tool,
# isaac_help(tool=...) on a single one, and the generated tool-argument reference.
MAX_ARGUMENT_DESCRIPTION = 120
MAX_DESCRIPTION = 400
MAX_ACTION_CLAUSE = 60
# Tool name -> the description and arguments as written, for tools whose listed text was shortened.
FULL: Dict[str, Dict[str, Any]] = {}
_SHORTENED: Dict[int, tuple] = {}
# A sentence that says a tool runs code, destroys something, needs a confirm flag or uses the GPU
# is never the one to drop.
SAFETY = re.compile(r"execut|destruct|confirm|\bGPU\b|\bCUDA\b|arbitrary|irreversib|overwrit|\bkills?\b|sandbox|"
                    r"opt-in|trusted|not undone|cannot be undone", re.I)
# A member's own argument called "action" (sim__ui__act has one) is offered under this name.
OWN_ACTION = "sub_action"
_STATE = "_isaac_grouped_tools"
_SENTENCE = re.compile(r"(?<![ (]e\.g)(?<![ (]i\.e)[.;:]\s+(?=[A-Z`'\"(])")
_FULL_STOP = re.compile(r"(?<![ (]e\.g)(?<![ (]i\.e)(?<=[.!?])\s+(?=[A-Z`'\"(])")
_CLAUSE = re.compile(r"[,;:]\s|\s[-\u2014(]\s?")


def _balanced(text: str) -> bool:
    return (text.count("(") <= text.count(")") and text.count("[") <= text.count("]")
            and text.count("{") <= text.count("}") and text.count("`") % 2 == 0 and text.count("'") % 2 == 0)


def _clip(text: str, limit: int) -> str:
    """``text`` in at most ``limit`` characters: whole if it fits, else its first sentence, else its
    longest run of whole clauses (never inside brackets or quotes), else whole words and "...";
    never a word cut in two."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    first = _SENTENCE.split(text, 1)[0]
    if len(first) <= limit:
        return first.rstrip(".:;")
    for m in reversed(list(_CLAUSE.finditer(first[:limit + 1]))):
        head = first[:m.start()].rstrip(" ,;:-")
        if m.start() < limit * 0.45:
            break
        if _balanced(head) and not re.search(r"\b(e\.g|i\.e|or|and|the|a|an|to|of|with|for|from|in|on|as)$", head):
            return head
    words = first[:max(limit - 4, 8)].rsplit(" ", 1)[0].rstrip(",;:- ")
    while not _balanced(words) and " " in words:
        words = words.rsplit(" ", 1)[0].rstrip(",;:- ")
    return words + " ..."


def _clip_keeping_safety(text: str, limit: int) -> str:
    """``_clip``, but a later sentence that says the tool executes code, is destructive, needs a
    confirm flag or uses the GPU is kept when the first sentence does not say so."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    sentences = _FULL_STOP.split(text)
    risky = next((s for s in sentences[1:] if SAFETY.search(s)), None)
    if risky is None or SAFETY.search(sentences[0]):
        return _clip(text, limit)
    head = _clip(sentences[0], max(limit // 2, limit - len(risky) - 1)).rstrip(".")
    return head + ". " + _clip(risky, limit - len(head) - 2)


def _brief(text: str, limit: int = MAX_ACTION_CLAUSE) -> str:
    """The first sentence of a description, cut at a clause or word if it is long."""
    out = _clip(text, limit)
    return out if out.endswith("...") else out.rstrip(".:;")


def shorten_description(text: str, hint: str, limit: int = MAX_DESCRIPTION) -> tuple[str, bool]:
    """A listed description: the first sentence, any sentence that warns (code, destructive, confirm,
    GPU), then the rest in order while it fits. Returns it and whether anything was left out."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text, False
    sentences = _FULL_STOP.split(text)
    room = limit - len(hint) - 1
    kept = {0: _clip(sentences[0], room)}
    used = len(kept[0])
    for i in range(1, len(sentences)):
        if SAFETY.search(sentences[i]):
            kept[i] = _clip(sentences[i], 160)
            used += len(kept[i]) + 1
    for passes in ((0, 150), (150, 10 ** 6)):        # short sentences first: they say more per character
        for i in range(1, len(sentences)):
            if i not in kept and passes[0] <= len(sentences[i]) < passes[1] and used + len(sentences[i]) + 1 <= room:
                kept[i] = sentences[i]
                used += len(sentences[i]) + 1
    out = " ".join(kept[i] for i in sorted(kept))
    return out + ("" if len(kept) == len(sentences) and kept[0] == sentences[0] else hint), True


def _lean_schema(spec: Dict[str, Any]) -> Dict[str, Any]:
    """One argument's schema without what costs words and tells a client nothing: the null variant
    of an optional argument, and a default of null. Types, enums and bounds stay."""
    spec = {key: value for key, value in spec.items() if key != "title" and not (key == "default" and value is None)}
    kinds = [kind for kind in spec.get("anyOf", []) if kind != {"type": "null"}]
    if kinds and len(kinds) < len(spec["anyOf"]):
        rest = {key: value for key, value in spec.items() if key != "anyOf"}
        spec = {**kinds[0], **rest} if len(kinds) == 1 else {**rest, "anyOf": kinds}
    return spec


def shorten_listed(tool: Any) -> None:
    """Shorten what a client is shown for a tool that is listed on its own (not behind a group): its
    description to about MAX_DESCRIPTION and each argument description to MAX_ARGUMENT_DESCRIPTION,
    keeping the full text in ``FULL`` for isaac_help(tool=...). Not for a group member: a group's
    help action returns the members' text as written."""
    name = tool.name
    if name in tool_groups.GROUPS or name in tool_groups.members():
        return
    description, parameters = tool.description or "", tool.parameters or {}
    if _SHORTENED.get(id(tool), (None, None))[1] == description:     # this very tool, already done
        return
    properties, lost = {}, False
    for key, spec in (parameters.get("properties") or {}).items():
        spec = _lean_schema(spec)
        text = spec.get("description")
        if isinstance(text, str):
            short = _clip_keeping_safety(tool_groups.listed_form(text), MAX_ARGUMENT_DESCRIPTION)
            lost = lost or len(" ".join(text.split())) > len(short)
            spec["description"] = short
        properties[key] = spec
    hint = f' Full text: isaac_help(tool="{name}").'
    listed, cut = shorten_description(description, hint)
    if lost and not cut:
        listed, cut = listed + hint, True
    FULL[name] = {"description": description, "arguments": parameters.get("properties", {}),
                  "required": parameters.get("required", []), "listed": listed}
    tool.description = listed
    tool.parameters = {**parameters, "properties": properties}
    _SHORTENED[id(tool)] = (tool, listed)      # the tool is kept so that its id is not reused


def _scope(uses: Dict[str, Any], actions: List[str]) -> str:
    """Which actions take an argument, when not all do, in the shorter of two forms."""
    if len(uses) == len(actions):
        return ""
    others = [action for action in actions if action not in uses]
    return f"[all but {', '.join(others)}] " if len(others) < len(uses) else f"[{', '.join(uses)}] "


def _argument(name: str, uses: Dict[str, Dict[str, Any]], actions: List[str]) -> Dict[str, Any]:
    """One argument of a group: the schema its members give it (either of them, where two actions
    take different kinds of value under one name), and a description of at most
    MAX_ARGUMENT_DESCRIPTION characters that says which actions take it when not all do. Where the
    members describe it differently, the wording most of them share leads; the rest is in help."""
    kinds_seen: List[Dict[str, Any]] = []
    for spec in uses.values():
        kind = _kind(spec)
        if kind not in kinds_seen:
            kinds_seen.append(kind)
    schema = dict(kinds_seen[0]) if len(kinds_seen) == 1 else {"anyOf": kinds_seen}
    texts: Dict[str, List[str]] = {}
    for action, spec in uses.items():
        texts.setdefault(tool_groups.listed_form(" ".join(str(spec.get("description") or "").split())), []).append(action)
    scope = _scope(uses, actions)
    ranked = sorted(texts.items(), key=lambda item: -len(item[1]))
    room = MAX_ARGUMENT_DESCRIPTION - len(scope)
    if len(texts) == 1:
        description = scope + _clip_keeping_safety(ranked[0][0], room)
    else:
        # Several wordings: the one most actions share leads. Another is added, labelled with its
        # actions, only when whole sentences of both fit; the rest is in action="help".
        lead = _clip_keeping_safety(ranked[0][0], room)
        description = scope + lead
        if len(texts) == 2:
            other, who = ranked[1]
            extra = f" {'/'.join(who)}: {other}"
            if len(description) + len(extra) <= MAX_ARGUMENT_DESCRIPTION and not other.endswith(("...", "…")):
                description += extra
    schema["description"] = description if len(description) <= MAX_ARGUMENT_DESCRIPTION else _clip(description, MAX_ARGUMENT_DESCRIPTION)
    return schema


def _kind(spec: Dict[str, Any]) -> Dict[str, Any]:
    """An argument's schema without its description and default, and without "or null": every
    argument of a grouped tool is optional anyway."""
    schema = {key: value for key, value in spec.items() if key not in ("description", "default")}
    kinds = [kind for kind in schema.get("anyOf", []) if kind != {"type": "null"}]
    if kinds and len(kinds) < len(schema["anyOf"]):
        rest = {key: value for key, value in schema.items() if key != "anyOf"}
        schema = {**rest, **kinds[0]} if len(kinds) == 1 else {**rest, "anyOf": kinds}
    return schema


def _annotations(tools: List[Any]) -> ToolAnnotations:
    hints = [tool.annotations for tool in tools if tool.annotations is not None]
    return ToolAnnotations(
        readOnlyHint=bool(hints) and len(hints) == len(tools) and all(h.readOnlyHint for h in hints),
        destructiveHint=any(h.destructiveHint for h in hints),
        idempotentHint=bool(hints) and len(hints) == len(tools) and all(h.idempotentHint for h in hints),
        openWorldHint=any(h.openWorldHint for h in hints))


def _clause(text: str, limit: int) -> str:
    """The first sentence of a member's description when it is at most ``limit`` characters; else
    nothing (a sentence cut short misleads more than the action's name does)."""
    first = _SENTENCE.split(" ".join((text or "").split()), 1)[0].rstrip(".:;")
    return first if len(first) <= limit else ""


def _listed_description(summary: str, clauses: Dict[str, str], needs_of: Dict[str, str]) -> str:
    """The group's listed description: its summary, then one line per action with what it needs,
    and a short clause for each (the first sentence of the member's description, at most
    MAX_ACTION_CLAUSE characters) when that keeps the whole to about MAX_DESCRIPTION characters, 40
    more for each action beyond four. Past that the lines carry only names and what each needs: the
    summary already says what the actions are for. ``action=help`` returns every member's full text."""
    budget = MAX_DESCRIPTION + 40 * max(0, len(clauses) - 4)
    tail = "\n- help: every action's full description and arguments."
    shown = {action: _clause(text, MAX_ACTION_CLAUSE) for action, text in clauses.items()}

    def render(with_clauses: bool) -> str:
        lines = [f"- {action}: {shown[action]}.{needs_of[action]}" if with_clauses and shown[action]
                 else f"- {action}{':' if needs_of[action] else ''}{needs_of[action]}" for action in clauses]
        return f"{summary} Actions:\n" + "\n".join(lines) + tail

    return render(True) if len(render(True)) <= budget else render(False)


def _build(server: Any, group: str, summary: str, actions: Dict[str, str]) -> None:
    """Register one grouped tool over the members that are registered now."""
    registered = server._tool_manager._tools
    present = {action: registered[tool] for action, tool in actions.items() if tool in registered}
    names = list(present)
    arguments: Dict[str, Dict[str, Dict[str, Any]]] = {}
    needs_of: Dict[str, str] = {}
    clauses: Dict[str, str] = {}
    for action, tool in present.items():
        properties = tool.parameters.get("properties", {})
        for name, spec in properties.items():
            arguments.setdefault(OWN_ACTION if name == "action" else name, {})[action] = spec
        needs = [OWN_ACTION if name == "action" else name for name in tool.parameters.get("required", [])
                 if name not in ("session_id", "stage_id")]
        needs_of[action] = f" Needs {', '.join(needs)}." if needs else ""
        clauses[action] = tool_groups.listed_form(tool.description or "")
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
    function.__doc__ = _listed_description(summary, clauses, needs_of)
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


_HINT_KEYS = ("next", "note", "hint", "timeline")


def _hints(value: Any, depth: int = 0) -> Any:
    """A JSON answer with only its advice fields rewritten: the data beside them is the caller's."""
    if isinstance(value, dict) and depth < 3:
        return {key: tool_groups.listed_form(item) if key in _HINT_KEYS and isinstance(item, str) else _hints(item, depth + 1)
                for key, item in value.items()}
    return value


def _hints_in_listed_form(answer: Any) -> Any:
    """For a tool whose answer is the caller's data: leave the data, but name tools in the advice
    the server itself put beside it ("next", "note", ...) the way the client is shown them."""
    blocks = answer[0] if isinstance(answer, tuple) and answer else answer
    for block in blocks if isinstance(blocks, list) else getattr(blocks, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.startswith("{"):
            try:
                block.text = json.dumps(_hints(json.loads(text)), default=repr)
            except ValueError:
                pass
    return answer


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
    for group, (summary, actions) in tool_groups.GROUPS.items():
        if names & set(actions.values()):
            _build(server, group, summary, actions)
    hide = tool_groups.hidden(names)
    if not hasattr(server, _STATE):
        everything, call = manager.list_tools, manager.call_tool
        setattr(server, _STATE, {"hide": hide})

        async def call_tool(name, arguments, *args, **kwargs):
            if name in tool_groups.VERBATIM:      # the caller's own data, code or log, in answers and errors alike
                return _hints_in_listed_form(await call(name, arguments, *args, **kwargs))
            try:
                return _in_listed_form(await call(name, arguments, *args, **kwargs))
            except ToolError as error:
                raise ToolError(tool_groups.listed_form(str(error))) from None

        manager.list_tools = lambda: [tool for tool in everything() if tool.name not in getattr(server, _STATE)["hide"]]
        manager.call_tool = call_tool
    getattr(server, _STATE).update(hide=hide)
    for name, tool in manager._tools.items():
        if name not in hide:
            _describe_in_listed_form(tool)


def listed_names(server: Any) -> List[str]:
    return [tool.name for tool in server._tool_manager.list_tools()]

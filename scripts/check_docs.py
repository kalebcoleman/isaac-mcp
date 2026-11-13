"""Check local documentation links, JSON examples and the MCP tool inventories."""

import ast
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def tool_decorators(path):
    tree = ast.parse(path.read_text())
    result = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "tool"):
                continue
            name = next((kw.value.value for kw in decorator.keywords
                         if kw.arg == "name" and isinstance(kw.value, ast.Constant)), node.name)
            result.add(name)
    return result


def bridge_builtins():
    bridge = ast.parse((ROOT / "exts/isaac.mcp.bridge/isaac_mcp_bridge/builtins.py").read_text())
    builtins = set()
    for node in ast.walk(bridge):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "ToolDefinition"):
            continue
        attrs = {kw.arg: kw.value for kw in node.keywords}
        if isinstance(attrs.get("internal"), ast.Constant) and attrs["internal"].value is True:
            continue
        if isinstance(attrs.get("name"), ast.Constant):
            builtins.add(attrs["name"].value)
    return builtins


def expected_tools():
    twin_file = ROOT / "exts/ur5e-dt/ur5e_dt/extension.py"
    tree = ast.parse(twin_file.read_text())
    registry = next(ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == "MCP_TOOL_REGISTRY"
                        for target in node.targets))
    builtins = bridge_builtins()
    host = (tool_decorators(ROOT / "mcp/isaac_mcp/screen.py")
            | tool_decorators(ROOT / "mcp/isaac_mcp/server.py")
            | tool_decorators(ROOT / "mcp/isaac_mcp/desktop_input.py")
            | tool_decorators(ROOT / "mcp/isaac_mcp/sim_sessions.py") | builtins)
    import sys
    sys.path.insert(0, str(ROOT / "mcp"))
    from isaac_mcp.scene_contracts import REGISTRY
    return {
        "docs/TOOLS-SCENE.md": {name.replace(".","__") for name in REGISTRY},
        "docs/TOOLS-UR5E.md": {"ur5e_dt__" + name for name in registry},
        "docs/TOOLS-ISAACLAB.md": tool_decorators(ROOT / "mcp/isaac_mcp/isaaclab/tools.py"),
        "docs/TOOLS-HOST.md": host,
    }


def tier_counts():
    """Tool counts per tier, in the order AGENTS.md states them."""
    expected = expected_tools()
    builtins = bridge_builtins()
    host_only = (tool_decorators(ROOT / "mcp/isaac_mcp/screen.py")
                 | tool_decorators(ROOT / "mcp/isaac_mcp/server.py"))
    return {"generic": len(expected["docs/TOOLS-SCENE.md"]), "host": len(host_only),
            "desktop_input": len(tool_decorators(ROOT / "mcp/isaac_mcp/desktop_input.py")),
            "sim_sessions": len(tool_decorators(ROOT / "mcp/isaac_mcp/sim_sessions.py")),
            "isaaclab": len(expected["docs/TOOLS-ISAACLAB.md"]),
            "builtins": len(builtins), "ur5e": len(expected["docs/TOOLS-UR5E.md"])}


TIER_ROWS = {"host": "Host gateway", "desktop_input": "Desktop input (optional)",
             "sim_sessions": "Simulator sessions (optional)", "isaaclab": "Isaac Lab", "builtins": "Bridge built-ins",
             "ur5e": "UR5e twin", "generic": "Generic scene and assets"}


def check_agent_guide(errors, text=None, counts=None):
    """Compare every figure a reader sees in AGENTS.md with the counts derived from the code."""
    if text is None:
        text = (ROOT / "AGENTS.md").read_text()
    actual = counts or tier_counts()
    where = "AGENTS.md"
    for tier, label in TIER_ROWS.items():
        rows = [line for line in text.splitlines() if line.startswith("| " + label)]
        cells = [c.strip() for c in rows[0].strip("|").split("|")] if len(rows) == 1 else []
        match = re.match(r"(\d+)\b", cells[1]) if len(cells) > 1 else None
        if not match:
            errors.append(f"{where}: need exactly one '{label}' table row whose Tools cell starts "
                          "with a count")
        elif int(match.group(1)) != actual[tier]:
            errors.append(f"{where}: {label} row says {match.group(1)} tools, the code has "
                          f"{actual[tier]}")
    expected = {
        # Everything the standard groups list from startup: the bridge built-ins are catalogued too.
        "exactly N tools (standard groups listed offline)":
            (r"exactly (\d+) tools",
             actual["host"] + actual["isaaclab"] + actual["builtins"] + actual["ur5e"] + actual["generic"]),
        "The other N tools (bridge built-ins subtotal)":
            (r"The other (\d+) tools", actual["builtins"]),
        "there are N tools in total":
            (r"there are (\d+) tools in total",
             sum(actual[tier] for tier in ("host", "isaaclab", "builtins", "ur5e", "generic"))),
        "groups add N more (optional subtotal)":
            (r"groups add (\d+) more", actual["desktop_input"] + actual["sim_sessions"]),
    }
    for description, (pattern, value) in expected.items():
        found = re.findall(pattern, text)
        if len(found) != 1:
            errors.append(f"{where}: expected exactly one '{description}' statement, found "
                          f"{len(found)}")
        elif int(found[0]) != value:
            errors.append(f"{where}: '{description}' says {found[0]}, the code has {value}")


SKILLS_DIR = ROOT / ".agents" / "skills"


def scene_catalog_tools():
    """Return the sim/assets packs' MCP names when their catalogs are present."""
    result = {}
    for prefix, filename in (("sim__", "sim.json"), ("assets__", "assets.json")):
        path = ROOT / "mcp" / "isaac_mcp" / "catalogs" / filename
        if not path.is_file():
            continue
        tools = json.loads(path.read_text()).get("tools", {})
        result[prefix] = {name.replace(".", "__") for name in tools}
    return result


def check_skill_tool_names(errors, expected=None, files=None):
    """Every registered tool name a skill mentions must exist in the registry."""
    expected = expected or expected_tools()
    registered = {"isaaclab__": expected["docs/TOOLS-ISAACLAB.md"],
                  "ur5e_dt__": expected["docs/TOOLS-UR5E.md"]}
    scene = scene_catalog_tools()
    if "sim__" in scene:
        registered["sim__"] = {
            name for name in expected.get("docs/TOOLS-HOST.md", set())
            if name.startswith("sim__")
        } | scene["sim__"]
    if "assets__" in scene:
        registered["assets__"] = scene["assets__"]
    import sys
    sys.path.insert(0, str(ROOT / "mcp"))
    from isaac_mcp.tool_groups import GROUPS       # a grouped tool is a name a client is shown
    registered = {prefix: set(names) | {group for group in GROUPS if group.startswith(prefix)}
                  for prefix, names in registered.items()}
    for path in files if files is not None else sorted(SKILLS_DIR.rglob("*.md")):
        text = path.read_text()
        for prefix, names in registered.items():
            # A trailing `*` marks a family (`ur5e_dt__quick_start*`): it must prefix a real tool.
            for name, star in re.findall(r"\b(" + prefix + r"[a-z][a-z0-9_]*)(\*?)", text):
                if name in names or (star and any(n.startswith(name) for n in names)):
                    continue
                errors.append(f"{path.relative_to(ROOT)}: names not in registry: {name}{star}")


README_COUNTS = (
    # The single-purpose tools registered by default, then with both optional groups.
    (r"\*\*(\d+) single-purpose tools\*\*", lambda c: c["host"] + c["isaaclab"] + c["builtins"] + c["ur5e"] + c["generic"]),
    (r"\(\*\*(\d+)\*\* with both optional groups\)",
     lambda c: sum(c[tier] for tier in ("host", "isaaclab", "builtins", "ur5e", "generic",
                                        "desktop_input", "sim_sessions"))),
)


README_ROWS = {"Generic scene and assets": ("generic",), "UR5e digital twin": ("ur5e",), "Isaac Lab": ("isaaclab",),
               "Host gateway and desktop": ("host",), "Bridge built-ins": ("builtins",),
               "Optional: desktop input, managed sessions": ("desktop_input", "sim_sessions")}


GENERIC_COUNT_PAGES = ("AGENTS.md", "docs/ARCHITECTURE.md", "docs/TOOLS-HOST.md", ".agents/skills/isaac-mcp/SKILL.md")
GENERIC_COUNT = re.compile(r"\b(\d+)(?:-tool pack| generic (?:`?sim|tools))")


def check_generic_counts(errors, pages=None, generic=None):
    """The generic pack's size wherever a current page states it. COMPATIBILITY.md is excluded: its
    dated records say how many tools a past run exercised."""
    generic = tier_counts()["generic"] if generic is None else generic
    pages = pages if pages is not None else {name: (ROOT / name).read_text(encoding="utf-8") for name in GENERIC_COUNT_PAGES}
    for name, text in pages.items():
        for match in GENERIC_COUNT.finditer(text):
            if int(match.group(1)) != generic:
                errors.append(f"{name}: says {match.group(0)!r}, but the generic pack has {generic} tools")


OPT_OUT_PAGES = ("AGENTS.md", "docs/TOOLS-HOST.md")
OPT_OUT_COUNT = re.compile(r"leaving (\d+) tools")


def standard_without_generic(counts=None):
    """Tools still listed when the generic pack is hidden and the optional groups are off."""
    counts = tier_counts() if counts is None else counts
    return counts["host"] + counts["isaaclab"] + counts["builtins"] + counts["ur5e"]


def check_scene_opt_out(errors, pages=None, counts=None):
    """The 'leaving N tools' figure is the standard total minus the generic pack.

    A parenthetical such as 'tools (55)' does not match the generic-pack regex, so this
    sentence is checked on its own. Each page must state the figure exactly once.
    """
    expected = standard_without_generic(counts)
    pages = pages if pages is not None else {
        name: (ROOT / name).read_text(encoding="utf-8") for name in OPT_OUT_PAGES}
    for name, text in pages.items():
        found = OPT_OUT_COUNT.findall(text)
        if found != [str(expected)]:
            errors.append(f"{name}: 'leaving N tools' says {found or 'nothing'}, "
                          f"the standard groups without the generic pack list {expected}")


TOTAL_PAGES = ("docs/TOOLS-HOST.md", "docs/MCP-SERVER.md")
DEFAULT_TOTAL = re.compile(r"\*\*(\d+) tools\*\*(?: by default| with the default)")
BOTH_TOTAL = re.compile(r"\*\*(\d+)\*\* with both")
LAB_COUNT_PAGES = ("docs/TOOLS-ISAACLAB.md", "docs/MCP-SERVER.md")
LAB_COUNT = re.compile(r"covers the (\d+) host-side")


def check_prose_totals(errors, pages=None, skill=None, counts=None):
    """The default and with-both-optional-groups totals, and the Lab tool count, wherever prose states them."""
    counts = tier_counts() if counts is None else counts
    default = standard_without_generic(counts) + counts["generic"]
    both = default + counts["desktop_input"] + counts["sim_sessions"]
    read = lambda names: {n: (ROOT / n).read_text(encoding="utf-8") for n in names}
    for name, text in (pages if pages is not None else read(TOTAL_PAGES)).items():
        for label, pattern, value in (("default total", DEFAULT_TOTAL, default), ("with-both total", BOTH_TOTAL, both)):
            found = pattern.findall(text)
            if found != [str(value)]:
                errors.append(f"{name}: {label} says {found or 'nothing'}, the code lists {value}")
    skill_path = ".agents/skills/isaac-mcp/SKILL.md"
    skill = (ROOT / skill_path).read_text(encoding="utf-8") if skill is None else skill
    for pattern, value in ((r"Tool count differs from (\d+)\b", default), (r"= (\d+) \|", both)):
        found = re.findall(pattern, skill)
        if found != [str(value)]:
            errors.append(f"{skill_path}: tool count {pattern!r} says {found or 'nothing'}, the code lists {value}")
    for name, text in (pages if pages is not None else read(LAB_COUNT_PAGES)).items():
        if name in LAB_COUNT_PAGES:
            found = LAB_COUNT.findall(text)
            if found != [str(counts["isaaclab"])]:
                errors.append(f"{name}: Lab tool count says {found or 'nothing'}, the code has {counts['isaaclab']}")


def check_vendoring_claims(errors, text=None):
    """The vendoring page must not claim the UR5e asset payload is in the git tree."""
    text = (ROOT / "docs/VENDORING.md").read_text(encoding="utf-8") if text is None else text
    if re.search(r"\d+\s*MB of assets", text):
        errors.append("docs/VENDORING.md: claims a vendored asset payload; the tree is code-only")
    if "ships none" not in text:
        errors.append("docs/VENDORING.md: must state that the repository ships none of those assets")


def check_readme_counts(errors, text=None, counts=None):
    """The README states the totals and a count per row; keep them equal to the code."""
    text = (ROOT / "README.md").read_text() if text is None else text
    actual = counts or tier_counts()
    for label, tiers in README_ROWS.items():
        found = re.findall(r"^\| " + re.escape(label) + r" \| (\d+) \|", text, re.M)
        value = sum(actual[tier] for tier in tiers)
        if found != [str(value)]:
            errors.append(f"README.md: '{label}' row says {found}, the code has {value}")
    for pattern, value in README_COUNTS:
        found = re.findall(pattern, text)
        if len(found) != 1:
            errors.append(f"README.md: expected exactly one match for {pattern}, found {len(found)}")
        elif int(found[0]) != value(actual):
            errors.append(f"README.md: says {found[0]} tools, the code has {value(actual)}")


SHOWN_PAGES = ("README.md", "AGENTS.md", "docs/TOOLS-HOST.md", "docs/MCP-SERVER.md")
SHOWN_COUNT = re.compile(r"is shown \*\*(\d+) tools\*\*")


def default_tool_names():
    """Every tool the standard groups register, by MCP name."""
    expected = expected_tools()
    optional = (tool_decorators(ROOT / "mcp/isaac_mcp/desktop_input.py")
                | tool_decorators(ROOT / "mcp/isaac_mcp/sim_sessions.py"))
    return set().union(*expected.values()) - optional


def check_grouped_tools(errors, pages=None, names=None):
    """The number of tools a client is shown, wherever a page states it, and the table of groups:
    every group, action and single-purpose tool must be in docs/TOOLS-HOST.md, and every tool a
    group names must exist."""
    import sys
    sys.path.insert(0, str(ROOT / "mcp"))
    from isaac_mcp import tool_groups

    names = default_tool_names() if names is None else names
    shown = len(tool_groups.listed(names))
    pages = pages if pages is not None else {name: (ROOT / name).read_text(encoding="utf-8") for name in SHOWN_PAGES}
    for name, text in pages.items():
        found = SHOWN_COUNT.findall(text)
        if found != [str(shown)]:
            errors.append(f"{name}: 'is shown **N tools**' says {found or 'nothing'}, a client is shown {shown}")
    every = set().union(*expected_tools().values())
    unknown = sorted((set(tool_groups.members()) | set(tool_groups.SUPERSEDED)) - every)
    if unknown:
        errors.append(f"mcp/isaac_mcp/tool_groups.py: names tools that are not registered: {', '.join(unknown)}")
    table = pages.get("docs/TOOLS-HOST.md", "")
    for group, (_, actions) in tool_groups.GROUPS.items():
        row = next((line for line in table.splitlines() if line.startswith(f"| `{group}` |")), None)
        if row is None:
            errors.append(f"docs/TOOLS-HOST.md: the grouped-tools table has no row for {group}")
            continue
        missing = [f"{action} ({tool})" for action, tool in actions.items() if f"`{action}` (`{tool}`)" not in row]
        if missing:
            errors.append(f"docs/TOOLS-HOST.md: the {group} row is missing {', '.join(missing)}")


def check_tool_reference(errors):
    """docs/TOOLS-ARGUMENTS.md is generated; a hand edit or a changed tool must regenerate it."""
    from gen_tool_reference import OUTPUT, render
    if not OUTPUT.is_file() or OUTPUT.read_text() != render():
        errors.append("docs/TOOLS-ARGUMENTS.md is stale: run python3 scripts/gen_tool_reference.py --write")


def main():
    from check_private_paths import check_tracked

    errors = check_tracked(ROOT)
    files = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").glob("*.md")),
             ROOT / "mcp/README.md", *sorted((ROOT / "mcp/docs").glob("*.md")),
             *sorted((ROOT / "exts").glob("*/README.md")), *sorted((ROOT / "exts").glob("*/docs/*.md")),
             *sorted(SKILLS_DIR.rglob("*.md"))]
    for path in files:
        text = path.read_text()
        for link in re.findall(r"\]\(([^)]+)\)", text):
            target = link.split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (path.parent / target).exists():
                errors.append(f"{path.relative_to(ROOT)}: missing local link {target}")
        for block in re.findall(r"```json[ \t]*\n(.*?)\n```", text, re.DOTALL):
            try:
                json.loads(block)
            except json.JSONDecodeError as exc:
                errors.append(f"{path.relative_to(ROOT)}: invalid JSON example: {exc}")
    for filename, expected in expected_tools().items():
        text = (ROOT / filename).read_text()
        missing = sorted(name for name in expected if f"`{name}`" not in text)
        if missing:
            errors.append(f"{filename}: missing registered tools: {', '.join(missing)}")
        prefix = ("ur5e_dt__" if "UR5E" in filename else
                  "isaaclab__" if "ISAACLAB" in filename else None)
        if prefix:
            found = set(re.findall(r"\b" + prefix + r"[a-z][a-z0-9_]*", text))
            stale = sorted(found - expected)
            if stale:
                errors.append(f"{filename}: names not in registry: {', '.join(stale)}")
    check_skill_tool_names(errors)
    check_agent_guide(errors)
    check_readme_counts(errors)
    check_generic_counts(errors)
    check_scene_opt_out(errors)
    check_prose_totals(errors)
    check_grouped_tools(errors)
    check_vendoring_claims(errors)
    check_tool_reference(errors)
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        return 1
    print(f"Documentation checks passed ({len(files)} files; links, JSON, registered tool names, "
          "skill tool names, guide and README counts, grouped tools, generated tool arguments).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

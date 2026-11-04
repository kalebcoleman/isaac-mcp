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
    for path in files if files is not None else sorted(SKILLS_DIR.rglob("*.md")):
        text = path.read_text()
        for prefix, names in registered.items():
            # A trailing `*` marks a family (`ur5e_dt__quick_start*`): it must prefix a real tool.
            for name, star in re.findall(r"\b(" + prefix + r"[a-z][a-z0-9_]*)(\*?)", text):
                if name in names or (star and any(n.startswith(name) for n in names)):
                    continue
                errors.append(f"{path.relative_to(ROOT)}: names not in registry: {name}{star}")


README_COUNTS = (
    # Default listing, then with both optional groups: the figures the project README promises.
    (r"lists \*\*(\d+) tools\*\*", lambda c: c["host"] + c["isaaclab"] + c["builtins"] + c["ur5e"] + c["generic"]),
    (r"or \*\*(\d+)\*\* with both optional groups",
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


def check_tool_reference(errors):
    """docs/TOOLS-ARGUMENTS.md is generated; a hand edit or a changed tool must regenerate it."""
    from gen_tool_reference import OUTPUT, render
    if not OUTPUT.is_file() or OUTPUT.read_text() != render():
        errors.append("docs/TOOLS-ARGUMENTS.md is stale: run python3 scripts/gen_tool_reference.py --write")


def main():
    from check_private_paths import check_tracked

    errors = check_tracked(ROOT)
    files = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").glob("*.md")),
             ROOT / "mcp/README.md", ROOT / "npm/README.md", *sorted((ROOT / "mcp/docs").glob("*.md")),
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
    check_tool_reference(errors)
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        return 1
    print(f"Documentation checks passed ({len(files)} files; links, JSON, registered tool names, "
          "skill tool names, guide and README counts, generated tool arguments).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

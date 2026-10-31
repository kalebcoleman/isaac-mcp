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
            | tool_decorators(ROOT / "mcp/isaac_mcp/server.py") | builtins)
    return {
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
    return {"host": len(host_only), "isaaclab": len(expected["docs/TOOLS-ISAACLAB.md"]),
            "builtins": len(builtins), "ur5e": len(expected["docs/TOOLS-UR5E.md"])}


def check_agent_guide(errors):
    text = (ROOT / "AGENTS.md").read_text()
    match = re.search(r"<!-- tool-counts: ([^>]*?) -->", text)
    if not match:
        errors.append("AGENTS.md: missing '<!-- tool-counts: ... -->' marker")
        return
    stated = {key: int(value) for key, value in re.findall(r"(\w+)=(\d+)", match.group(1))}
    actual = tier_counts()
    if stated != actual:
        errors.append(f"AGENTS.md: tool counts {stated} do not match the code {actual}")
    total = sum(actual.values())
    if f"there are {total}" not in text:
        errors.append(f"AGENTS.md: total tool count text should say 'there are {total}'")


def main():
    errors = []
    files = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").glob("*.md")),
             *sorted((ROOT / "mcp/docs").glob("*.md"))]
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
    check_agent_guide(errors)
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        return 1
    print(f"Documentation checks passed ({len(files)} files; links, JSON and registered tool names).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

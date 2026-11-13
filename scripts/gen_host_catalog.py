#!/usr/bin/env python3
"""Generate the host's packaged tool catalog from the extensions' own tool registries.

The MCP host lists the UR5e work cell's tools, the generic scene tools and the bridge built-ins even
when no Kit bridge is running, from snapshots shipped in the wheel (``mcp/twinsmith/catalogs/*.json``,
read by ``twinsmith.host_catalog``).
This script is the only thing that writes that snapshot, and it derives every byte from the
sources of truth:

* ``MCP_TOOL_REGISTRY`` in ``exts/workcell/workcell/extension.py`` (read with ``ast``; the file
  imports Kit modules, so it is never executed), and
* ``twinsmith_bridge.schema.field_specs`` - the one interpreter of the parameter vocabulary.

The entries are shaped like the bridge's ``list_tools`` answer (the work cell registers every registry
tool under owner ``workcell`` and namespace ``workcell``; see ``workcell/bridge_binding.py``).

    python3 scripts/gen_host_catalog.py --write   # regenerate the snapshot
    python3 scripts/gen_host_catalog.py --check   # exit 1 when the snapshot has drifted (default)

It starts no simulator and needs no bridge.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "exts/workcell/workcell/extension.py"
BINDING = ROOT / "exts/workcell/workcell/bridge_binding.py"
OUTPUT = ROOT / "mcp/twinsmith/catalogs/workcell.json"
CATALOG_VERSION = 1


def _literal_assignment(path: Path, name: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit(f"{path}: no top-level literal assignment named {name}")


def _field_specs():
    sys.path.insert(0, str(ROOT / "exts/twinsmith.bridge"))
    from twinsmith_bridge.schema import field_specs  # noqa: PLC0415 - path set just above
    return field_specs


def build_ur5e_catalog() -> Dict[str, Any]:
    """The catalog document for the UR5e work cell, exactly as ``--write`` stores it."""
    registry = _literal_assignment(EXTENSION, "MCP_TOOL_REGISTRY")
    owner_id = _literal_assignment(BINDING, "OWNER_ID")
    namespace = _literal_assignment(BINDING, "NAMESPACE")
    field_specs = _field_specs()
    tools = {}
    for name, schema in registry.items():
        parameters = schema.get("parameters", {})   # bridge_binding.attach does the same
        tools[f"{namespace}.{name}"] = {
            "description": schema["description"],
            "parameters": parameters,
            "fields": field_specs(parameters),
            "namespace": namespace,
            "owner_id": owner_id,
        }
    document = {"version": CATALOG_VERSION, "namespace": namespace, "tools": tools}
    if json.loads(json.dumps(document)) != document:
        raise SystemExit("the registry holds values that do not survive a JSON round trip")
    return document


BUILTINS_OUTPUT = ROOT / "mcp/twinsmith/catalogs/builtins.json"


def build_builtins_catalog() -> Dict[str, Any]:
    """The bridge's own bare-named verbs (run_python, status, ...), as ``list_tools`` serves them.

    Read from ``twinsmith_bridge.builtins.builtin_tools`` (pure Python outside Kit). Internal verbs
    (``list_tools``) are left out, as the bridge leaves them out of its own listing."""
    _field_specs()   # puts the bridge package on sys.path
    from twinsmith_bridge.bridge import BUILTIN_OWNER_ID  # noqa: PLC0415
    from twinsmith_bridge.builtins import builtin_tools  # noqa: PLC0415
    from twinsmith_bridge.schema import field_specs  # noqa: PLC0415
    tools = {}
    for definition in builtin_tools(None):
        if definition.internal:
            continue
        tools[definition.name] = {
            "description": definition.description,
            "parameters": definition.parameters,
            "fields": field_specs(definition.parameters),
            "namespace": None,
            "owner_id": BUILTIN_OWNER_ID,
        }
    return {"version": CATALOG_VERSION, "namespace": None, "tools": tools}


def render(document: Dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="regenerate the packaged snapshot")
    mode.add_argument("--check", action="store_true", help="fail if the snapshot differs (default)")
    parser.add_argument("--output", type=Path, default=OUTPUT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT / "mcp"))
    from twinsmith.scene_contracts import REGISTRY
    for namespace in ("assets", "sim"):
        tools = {}
        for canonical, meta in REGISTRY.items():
            if canonical.startswith(namespace + "."):
                tools[canonical] = dict(**meta, fields=_field_specs()(meta["parameters"]),
                                        namespace=namespace, owner_id="generic-"+namespace)
        dest = ROOT / "mcp/twinsmith/catalogs" / (namespace+".json")
        expected = render(dict(version=1,namespace=namespace,tools=tools))
        if args.write:
            dest.write_text(expected)
        elif not dest.exists() or dest.read_text()!=expected:
            print(str(dest)+" is stale; run --write",file=sys.stderr)
            return 1
    builtins_text = render(build_builtins_catalog())
    if args.write:
        BUILTINS_OUTPUT.write_text(builtins_text, encoding="utf-8")
    elif not BUILTINS_OUTPUT.exists() or BUILTINS_OUTPUT.read_text(encoding="utf-8") != builtins_text:
        print(f"{BUILTINS_OUTPUT} is stale: the bridge built-ins changed; run --write", file=sys.stderr)
        return 1
    text = render(build_ur5e_catalog())
    if args.write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output}")
        return 0
    current = args.output.read_text(encoding="utf-8") if args.output.exists() else None
    if current == text:
        return 0
    print(f"{args.output} is stale: the UR5e MCP_TOOL_REGISTRY changed.\n"
          "Regenerate it with: python3 scripts/gen_host_catalog.py --write", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

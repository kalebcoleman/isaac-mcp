"""How a tool describes its parameters, read in one place.

A tool's ``parameters`` is ``{name: {"type": ..., "description": ..., "required": bool,
"default": ..., "enum": [...]}}``. ``field_specs`` turns that into the ordered list the MCP host
builds a function signature from. Stdlib only.
"""

from typing import Any, Dict, List, Optional

KINDS = ("string", "integer", "number", "boolean", "array", "object")


def field_spec(name: str, spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    spec = spec or {}
    enum = spec.get("enum")
    kind = spec.get("type", "string")
    return {
        "name": name,
        "kind": "enum" if enum else kind,
        "enum": list(enum) if enum else None,
        "required": bool(spec.get("required", False)),
        "default": spec.get("default"),
        "has_default": "default" in spec,
        "description": spec.get("description"),
    }


def field_specs(parameters: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Field specs in declaration order."""
    return [field_spec(name, spec) for name, spec in (parameters or {}).items()]

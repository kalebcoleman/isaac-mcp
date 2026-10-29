"""The one interpretation of the tool parameter-schema vocabulary.

A tool declares its parameters as loose JSON-schema-ish dicts (``type``,
``enum``, ``required``, ``default``, ``description``). Two renderers consume
that declaration — the MCP gateway (→ function signature) and the control
panel (→ omni.ui widgets) — and independent interpretation would let them
disagree. Interpretation therefore happens HERE, once, bridge-side: the
registry view serves each tool's parameters pre-resolved as **field specs**,
and renderers only switch on ``kind`` — they never read a raw parameter
schema.

A field spec is a plain dict (it rides the wire inside list_tools):

    {"name": str, "kind": "enum|boolean|integer|number|array|object|string",
     "enum": list|None, "required": bool,
     "default": Any, "has_default": bool, "description": str}

``enum`` wins over ``type``; an unknown ``type`` renders as string;
``has_default`` distinguishes a declared ``default: null``/absent from a real
declared default so renderers never invent one.

Stdlib-only — importable off-Kit (tests, lint) like the rest of the registry.
"""

from typing import Any, Dict, List, Optional

# The declared types renderers know how to render; anything else is text.
_KINDS = ("boolean", "integer", "number", "array", "object", "string")


def field_spec(name: str, schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """One parameter's declaration -> its field spec."""
    schema = schema or {}
    enum = schema.get("enum") or None
    declared = schema.get("type", "string")
    kind = "enum" if enum else (declared if declared in _KINDS else "string")
    return {
        "name": name,
        "kind": kind,
        "enum": list(enum) if enum else None,
        "required": bool(schema.get("required")),
        "default": schema.get("default"),
        "has_default": "default" in schema,
        "description": schema.get("description", ""),
    }


def field_specs(parameters: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A tool's ``parameters`` dict -> ordered field specs (declaration order)."""
    return [field_spec(name, schema) for name, schema in (parameters or {}).items()]

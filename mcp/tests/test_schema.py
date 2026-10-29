"""schema.py — the one interpretation of the parameter-schema vocabulary.

Renderers (gateway signature builder, panel widget builder) consume field
specs and never read raw parameter schemas; these tests pin the vocabulary
they rely on.
"""

from omni_kit_mcp.schema import field_spec, field_specs


def test_enum_wins_over_type():
    s = field_spec("mode", {"type": "string", "enum": ["a", "b"]})
    assert s["kind"] == "enum" and s["enum"] == ["a", "b"]


def test_declared_kinds_pass_through():
    for t in ("boolean", "integer", "number", "array", "object", "string"):
        assert field_spec("p", {"type": t})["kind"] == t


def test_unknown_or_missing_type_renders_as_string():
    assert field_spec("p", {"type": "float64"})["kind"] == "string"
    assert field_spec("p", {})["kind"] == "string"
    assert field_spec("p", None)["kind"] == "string"


def test_required_and_default_extraction():
    s = field_spec("n", {"type": "integer", "required": True, "default": 4})
    assert s["required"] is True and s["default"] == 4 and s["has_default"] is True


def test_absent_default_is_distinguishable_from_null_default():
    absent = field_spec("p", {"type": "string"})
    declared_null = field_spec("p", {"type": "string", "default": None})
    assert absent["has_default"] is False and absent["default"] is None
    assert declared_null["has_default"] is True and declared_null["default"] is None


def test_field_specs_preserve_declaration_order():
    params = {"b": {"type": "integer"}, "a": {"type": "string"}}
    assert [f["name"] for f in field_specs(params)] == ["b", "a"]
    assert field_specs(None) == []

"""scripts/check_docs.py must fail when any visible tool count in AGENTS.md goes stale."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
check_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_docs)

GUIDE = (ROOT / "AGENTS.md").read_text()
COUNTS = check_docs.tier_counts()


def errors_for(text):
    errors = []
    check_docs.check_agent_guide(errors, text=text, counts=COUNTS)
    return errors


def swap(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def test_real_guide_passes():
    assert errors_for(GUIDE) == []
    assert COUNTS == {"host": 9, "isaaclab": 12, "builtins": 7, "ur5e": 23}


@pytest.mark.parametrize("old,new,needle", [
    ("| 9 (", "| 10 (", "Host gateway row says 10"),
    ("| 12 |", "| 13 |", "Isaac Lab row says 13"),
    ("| 7 |", "| 8 |", "Bridge built-ins row says 8"),
    ("| 23 |", "| 24 |", "UR5e twin row says 24"),
    ("exactly 21 tools", "exactly 22 tools", "says 22"),
    ("The other 30 tools", "The other 31 tools", "says 31"),
    ("there are 51 tools in total", "there are 52 tools in total", "says 52"),
])
def test_stale_visible_figure_fails(old, new, needle):
    errors = errors_for(swap(GUIDE, old, new))
    assert len(errors) == 1 and needle in errors[0]


def test_marker_and_total_updated_but_table_and_subtotals_stale():
    """A maintainer who bumps only a marker and the total after adding a tool must still fail."""
    text = swap(GUIDE, "there are 51 tools in total", "there are 52 tools in total")
    text = "<!-- tool-counts: host=9 isaaclab=12 builtins=7 ur5e=24 -->\n" + text
    counts = dict(COUNTS, ur5e=24)
    errors = []
    check_docs.check_agent_guide(errors, text=text, counts=counts)
    # code now has 24 UR5e tools: table row (23), 30 subtotal and the 51 total are all stale
    assert len(errors) == 2
    assert any("UR5e twin row says 23" in e for e in errors)
    assert any("says 30" in e for e in errors)


def test_total_only_update_fails():
    counts = dict(COUNTS, ur5e=24)
    errors = []
    check_docs.check_agent_guide(errors, text=swap(GUIDE, "there are 51", "there are 52"), counts=counts)
    assert any("UR5e twin row" in e for e in errors)
    assert any("The other N tools" in e for e in errors)


def test_missing_statement_fails():
    text = swap(GUIDE, "exactly 21 tools", "21 tools")
    assert any("expected exactly one" in e for e in errors_for(text))

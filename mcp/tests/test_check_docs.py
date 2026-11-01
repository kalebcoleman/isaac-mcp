"""scripts/check_docs.py must fail when a visible tool count in AGENTS.md goes stale.

Expected figures come from the code via ``check_docs.tier_counts()``, so adding or removing a
tool does not require editing this file; only AGENTS.md has to be brought up to date, and
``scripts/check_docs.py`` reports exactly which figures are stale.
"""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
check_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_docs)

GUIDE = (ROOT / "AGENTS.md").read_text()
COUNTS = check_docs.tier_counts()
LABELS = check_docs.TIER_ROWS

# The three subtotal statements in the guide and the figure the code implies for each.
PHRASES = {
    "host_isaaclab": (r"exactly \d+ tools", "exactly {} tools",
                      lambda c: c["host"] + c["isaaclab"] + c["ur5e"]),
    "bridge": (r"The other \d+ tools", "The other {} tools",
               lambda c: c["builtins"]),
    "total": (r"there are \d+ tools in total", "there are {} tools in total",
              lambda c: sum(c[tier] for tier in ("host", "isaaclab", "builtins", "ur5e"))),
    "optional": (r"groups add \d+ more", "groups add {} more",
                 lambda c: c["desktop_input"] + c["sim_sessions"]),
}


def errors_for(text, counts=COUNTS):
    errors = []
    check_docs.check_agent_guide(errors, text=text, counts=counts)
    return errors


def set_row(text, tier, value):
    """Rewrite the leading count in the Tools cell of one tier row."""
    prefix = "| " + LABELS[tier]
    out, hits = [], 0
    for line in text.splitlines():
        if line.startswith(prefix):
            cells = line.split("|")
            cells[2] = re.sub(r"^(\s*)\d+", rf"\g<1>{value}", cells[2])
            line, hits = "|".join(cells), hits + 1
        out.append(line)
    assert hits == 1, tier
    return "\n".join(out) + "\n"


def set_phrase(text, key, value):
    pattern, template, _ = PHRASES[key]
    new, hits = re.subn(pattern, template.format(value), text)
    assert hits == 1, key
    return new


def bumped(tier="ur5e"):
    """Counts as they would be after one more tool is registered in `tier`."""
    return dict(COUNTS, **{tier: COUNTS[tier] + 1})


def test_real_guide_passes():
    assert errors_for(GUIDE) == []


@pytest.mark.parametrize("tier", list(LABELS))
def test_stale_table_row_fails(tier):
    errors = errors_for(set_row(GUIDE, tier, COUNTS[tier] + 1))
    assert len(errors) == 1
    assert f"{LABELS[tier]} row says {COUNTS[tier] + 1}" in errors[0]


@pytest.mark.parametrize("key", list(PHRASES))
def test_stale_subtotal_or_total_fails(key):
    wrong = PHRASES[key][2](COUNTS) + 1
    errors = errors_for(set_phrase(GUIDE, key, wrong))
    assert len(errors) == 1 and f"says {wrong}" in errors[0]


def test_new_tool_not_reflected_in_guide_fails():
    """Code gained a tool, guide untouched: the row, catalog subtotal and total are stale."""
    errors = errors_for(GUIDE, counts=bumped())
    assert len(errors) == 3
    assert any("UR5e twin row" in e for e in errors)
    assert any("exactly N tools" in e for e in errors)
    assert any("there are N tools in total" in e for e in errors)


def test_only_total_updated_after_adding_a_tool_fails():
    """A maintainer who bumps just the headline total must still fail on the row and subtotal."""
    counts = bumped()
    text = set_phrase(GUIDE, "total", PHRASES["total"][2](counts))
    errors = errors_for(text, counts=counts)
    assert len(errors) == 2
    assert any("UR5e twin row" in e for e in errors)
    assert any("exactly N tools" in e for e in errors)


def test_only_table_row_updated_after_adding_a_tool_fails():
    counts = bumped()
    errors = errors_for(set_row(GUIDE, "ur5e", counts["ur5e"]), counts=counts)
    assert len(errors) == 2
    assert any("exactly N tools" in e for e in errors)
    assert any("there are N tools in total" in e for e in errors)


def test_fully_updated_guide_passes_for_new_counts():
    """The documented workflow: after adding a tool, update the guide; no test edit is needed."""
    counts = bumped()
    text = set_row(GUIDE, "ur5e", counts["ur5e"])
    for key, (_, _, expected) in PHRASES.items():
        text = set_phrase(text, key, expected(counts))
    assert errors_for(text, counts=counts) == []


def test_missing_statement_fails():
    text, hits = re.subn(r"exactly (\d+) tools", r"\1 tools", GUIDE)
    assert hits == 1
    assert any("expected exactly one" in e for e in errors_for(text))


def test_missing_table_row_fails():
    prefix = "| " + LABELS["isaaclab"]
    text = "\n".join(line for line in GUIDE.splitlines() if not line.startswith(prefix)) + "\n"
    assert any("need exactly one" in e for e in errors_for(text))


def skill_errors(tmp_path, body):
    skill = tmp_path / "SKILL.md"
    skill.write_text(body)
    expected = {"docs/TOOLS-ISAACLAB.md": {"isaaclab__train", "isaaclab__runs_status"},
                "docs/TOOLS-UR5E.md": {"ur5e_dt__quick_start", "ur5e_dt__quick_start_with_workstation"}}
    errors = []
    original_root = check_docs.ROOT
    check_docs.ROOT = tmp_path
    try:
        check_docs.check_skill_tool_names(errors, expected=expected, files=[skill])
    finally:
        check_docs.ROOT = original_root
    return errors


def test_skill_with_registered_names_passes(tmp_path):
    body = "Call `isaaclab__train`, then `isaaclab__runs_status`; `ur5e_dt__quick_start*` builds a scene."
    assert skill_errors(tmp_path, body) == []


def test_skill_with_stale_name_fails(tmp_path):
    errors = skill_errors(tmp_path, "Call `isaaclab__train_start` first.")
    assert errors == ["SKILL.md: names not in registry: isaaclab__train_start"]


def test_skill_family_must_prefix_a_tool(tmp_path):
    errors = skill_errors(tmp_path, "Use `ur5e_dt__quickstart*`.")
    assert errors == ["SKILL.md: names not in registry: ur5e_dt__quickstart*"]


def test_repository_skills_name_only_registered_tools():
    errors = []
    check_docs.check_skill_tool_names(errors)
    assert errors == []

"""scripts/check_docs.py must fail when a visible tool count in AGENTS.md goes stale.

Expected figures come from the code via ``check_docs.tier_counts()``, so adding or removing a
tool does not require editing this file; only AGENTS.md has to be brought up to date, and
``scripts/check_docs.py`` reports exactly which figures are stale.
"""

import importlib.util
import json
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
                      lambda c: c["host"] + c["isaaclab"] + c["builtins"] + c["ur5e"] + c["generic"] + c["robosim"]),
    "bridge": (r"The other \d+ tools", "The other {} tools",
               lambda c: c["builtins"]),
    "total": (r"there are \d+ tools in total", "there are {} tools in total",
              lambda c: sum(c[tier] for tier in ("host", "isaaclab", "builtins", "ur5e", "generic", "robosim"))),
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


def test_skill_scene_names_are_checked_when_catalogs_exist(tmp_path):
    catalogs = tmp_path / "mcp" / "isaac_mcp" / "catalogs"
    catalogs.mkdir(parents=True)
    (catalogs / "sim.json").write_text(json.dumps({"tools": {"sim.stage.info": {}}}))
    (catalogs / "assets.json").write_text(json.dumps({"tools": {"assets.roots": {}}}))
    skill = tmp_path / "SKILL.md"
    skill.write_text("Use `sim__stage__info` and `assets__roots`.")
    errors = []
    original = check_docs.ROOT
    check_docs.ROOT = tmp_path
    try:
        check_docs.check_skill_tool_names(
            errors,
            expected={"docs/TOOLS-ISAACLAB.md": set(), "docs/TOOLS-UR5E.md": set(),
                      "docs/TOOLS-HOST.md": {"sim__launch"}},
            files=[skill],
        )
        skill.write_text("Use `sim__bogus`.")
        check_docs.check_skill_tool_names(
            errors,
            expected={"docs/TOOLS-ISAACLAB.md": set(), "docs/TOOLS-UR5E.md": set(),
                      "docs/TOOLS-HOST.md": {"sim__launch"}},
            files=[skill],
        )
    finally:
        check_docs.ROOT = original
    assert errors == ["SKILL.md: names not in registry: sim__bogus"]


def _with_stale_readme_total():
    text = (ROOT / "README.md").read_text()
    match = re.search(r"\*\*(\d+) single-purpose tools\*\*", text)
    return text.replace(match.group(0), f"**{int(match.group(1)) + 1} single-purpose tools**")


def test_readme_counts_match_the_code():
    errors = []
    check_docs.check_readme_counts(errors)
    assert errors == []


def test_stale_readme_total_is_reported():
    errors = []
    check_docs.check_readme_counts(errors, text=_with_stale_readme_total())
    assert any("README.md: says" in error for error in errors)


def test_stale_tool_reference_is_reported(tmp_path, monkeypatch):
    import sys
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    sys.modules.pop("gen_tool_reference", None)
    import gen_tool_reference
    monkeypatch.setattr(gen_tool_reference, "OUTPUT", tmp_path / "TOOLS-ARGUMENTS.md")
    errors = []
    check_docs.check_tool_reference(errors)
    assert any("TOOLS-ARGUMENTS.md is stale" in error for error in errors)
    (tmp_path / "TOOLS-ARGUMENTS.md").write_text(gen_tool_reference.render())
    errors = []
    check_docs.check_tool_reference(errors)
    assert errors == []


def test_stale_readme_row_is_reported():
    text = (ROOT / "README.md").read_text().replace("| UR5e digital twin | 10 |", "| UR5e digital twin | 99 |")
    errors = []
    check_docs.check_readme_counts(errors, text=text)
    assert any("'UR5e digital twin' row" in error for error in errors)


def test_generic_count_catches_a_stale_page_and_passes_the_real_ones():
    errors = []
    check_docs.check_generic_counts(errors)
    assert errors == []
    check_docs.check_generic_counts(errors, pages={"x.md": "the 54-tool pack and 40 generic tools"}, generic=55)
    assert len(errors) == 2


def test_architecture_generic_wording_is_checked():
    """'tools (55)' is invisible to the generic-pack regex; 'N generic sim' is not."""
    text = (ROOT / "docs/ARCHITECTURE.md").read_text()
    assert "generic sim" in text
    errors = []
    check_docs.check_generic_counts(
        errors, pages={"docs/ARCHITECTURE.md": text.replace("70 generic sim", "55 generic sim")})
    assert any("55 generic sim" in error for error in errors)
    ignored = []
    check_docs.check_generic_counts(
        ignored, pages={"docs/ARCHITECTURE.md": "Registers sim.* and assets.* tools (55), robot-agnostic"},
        generic=69)
    assert ignored == []


def test_default_tool_inventory_excludes_disabled_toolsets():
    from isaac_mcp.toolsets import DEFAULT, owner
    names = check_docs.default_tool_names()
    assert names
    assert all(owner(name) in DEFAULT for name in names)
    assert not any(name.startswith("ur5e_dt__") for name in names)


def test_vendoring_rejects_a_bundled_asset_claim():
    errors = []
    check_docs.check_vendoring_claims(errors)
    assert errors == []
    text = (ROOT / "docs/VENDORING.md").read_text().replace(
        "extension code only", "the extension and its 111 MB of assets")
    errors = []
    check_docs.check_vendoring_claims(errors, text=text)
    assert any("code-only" in error for error in errors)
    errors = []
    check_docs.check_vendoring_claims(errors, text="Vendored path only.\n")
    assert any("ships none" in error for error in errors)


def test_the_shown_count_and_the_table_of_groups_match_the_code():
    errors = []
    check_docs.check_grouped_tools(errors)
    assert errors == []


def test_a_stale_shown_count_or_group_row_is_reported():
    pages = {name: (ROOT / name).read_text() for name in check_docs.SHOWN_PAGES}
    pages["README.md"] = re.sub(r"is shown \*\*\d+ tools\*\*", "is shown **7 tools**", pages["README.md"])
    pages["docs/TOOLS-HOST.md"] = pages["docs/TOOLS-HOST.md"].replace("`create` (`sim__prim__create`)", "`create`")
    errors = []
    check_docs.check_grouped_tools(errors, pages=pages)
    assert any("README.md: 'is shown **N tools**' says ['7']" in error for error in errors)
    assert any("the sim__prim row is missing create (sim__prim__create)" in error for error in errors)


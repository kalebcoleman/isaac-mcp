"""What MCP clients receive. A weak model should be able to use these tools from the listing alone."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from isaac_mcp import host_catalog
from isaac_mcp import server as gw
from isaac_mcp.scene_contracts import REGISTRY
from isaac_mcp.tool_hints import CATALOG_HINTS

REPO = Path(__file__).resolve().parents[2]
MAX_TOOL_DESCRIPTION = 600
MAX_PARAMETER_DESCRIPTION = 450
# Tool or "tool.parameter": a limit that must be exceeded for a safety rule to stay in the listing.
# Keep this small, and say why next to each entry.
EXCEPTIONS: dict[str, int] = {}
ALL_GROUPS = "all,screen_input,sim_launch"


@pytest.fixture
def listed(monkeypatch, tmp_path):
    """Every tool, as an MCP client receives it from the real server with no bridge."""
    for name in ("ISAAC_MCP_NAMESPACE", "ISAAC_MCP_BUILTINS", "ISAAC_MCP_SCENE_TOOLS", "ISAAC_MCP_LOCAL_TOOLS"):
        monkeypatch.delenv(name, raising=False)
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": ALL_GROUPS, "ISAAC_MCP_SIM_SESSIONS_DIR": str(tmp_path)})

    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            await client.initialize()
            return (await client.list_tools()).tools

    return asyncio.run(run())


def limit(name, default):
    return EXCEPTIONS.get(name, default)


def test_every_listed_tool_has_a_description_within_the_limit(listed):
    too_long = {t.name: len(t.description or "") for t in listed
                if len(t.description or "") > limit(t.name, MAX_TOOL_DESCRIPTION)}
    assert not [t.name for t in listed if not (t.description or "").strip()]
    assert not too_long, f"Move detail to docs/TOOLS-*.md: {too_long}"


def test_every_listed_parameter_has_a_top_level_description_within_the_limit(listed):
    """At the top level: nested under anyOf, many clients never show it."""
    missing, too_long = [], {}
    for tool in listed:
        for name, spec in tool.inputSchema.get("properties", {}).items():
            text = spec.get("description") or ""
            if not text.strip():
                missing.append(f"{tool.name}.{name}")
            elif len(text) > limit(f"{tool.name}.{name}", MAX_PARAMETER_DESCRIPTION):
                too_long[f"{tool.name}.{name}"] = len(text)
    assert not missing, missing
    assert not too_long, too_long


def test_every_listed_tool_says_whether_it_reads_or_changes_things(listed):
    missing = [t.name for t in listed if t.annotations is None or t.annotations.readOnlyHint is None
               or t.annotations.destructiveHint is None]
    assert not missing, missing


def test_schema_titles_are_not_listed(listed):
    assert '"title"' not in json.dumps([t.inputSchema for t in listed if "title" not in t.inputSchema.get("properties", {})])


@pytest.mark.parametrize(
    "name",
    ["screenshot", "start_screen_recording", "stop_screen_recording", "join_videos", "run_python", "put_file",
     "ur5e_dt__save_scene_state", "ur5e_dt__new_stage", "assets__pack_build", "isaaclab__runs_tail", "sim__camera__capture"],
)
def test_nothing_that_writes_claims_to_be_read_only(listed, name):
    assert next(t for t in listed if t.name == name).annotations.readOnlyHint is False


def test_destructive_tools_say_so(listed):
    hints = {t.name: t.annotations for t in listed}
    for name in ("run_python", "put_file", "ur5e_dt__new_stage", "ur5e_dt__delete_objects", "isaaclab__runs_cancel",
                 "window_click", "sim__stop"):
        assert hints[name].destructiveHint is True, name
    for name in ("status", "get_file", "stat_file", "list_bridges", "ur5e_dt__get_recording_status", "screen_info"):
        assert hints[name].readOnlyHint is True and hints[name].destructiveHint is False, name


def test_catalogued_tools_meet_the_limits_offline():
    """Without starting a server, so a contract edit fails here even with the host tools broken."""
    catalog = host_catalog.load_catalog()
    assert catalog
    for canonical, meta in catalog.items():
        assert meta["description"].strip(), canonical
        tool = canonical.replace(".", "__")
        assert len(meta["description"]) <= limit(tool, MAX_TOOL_DESCRIPTION), canonical
        for field in meta["fields"]:
            text = field["description"] or ""
            assert text.strip(), f"{canonical}.{field['name']}"
            assert len(text) <= limit(f"{tool}.{field['name']}", MAX_PARAMETER_DESCRIPTION), f"{canonical}.{field['name']}"


def test_every_catalogued_tool_has_hints():
    missing = sorted(set(host_catalog.load_catalog()) - set(REGISTRY) - set(CATALOG_HINTS))
    assert not missing, f"Add these to isaac_mcp/tool_hints.py: {missing}"
    stale = sorted(set(CATALOG_HINTS) - set(host_catalog.load_catalog()))
    assert not stale, stale


def test_the_exception_list_stays_small_and_real(listed):
    assert len(EXCEPTIONS) <= 5
    names = {t.name for t in listed} | {f"{t.name}.{p}" for t in listed for p in t.inputSchema.get("properties", {})}
    assert set(EXCEPTIONS) <= names


def test_instructions_name_the_starting_points_and_what_is_universal():
    text = gw.SERVER_INSTRUCTIONS
    assert "sim__quick_start" in text and "sim__stage__info" in text
    assert "ur5e_dt__* is one lab's UR5e digital twin" in text and "work on any scene" in text


def test_the_skill_has_a_decision_path():
    text = (REPO / ".agents/skills/isaac-mcp/SKILL.md").read_text()
    head = text[text.index("## Which tool do I start with?"):text.index("## Ground rules")]
    assert "sim__quick_start" in head and "sim__stage__info" in head and "ur5e_dt__quick_start" in head


def test_the_argument_reference_shows_clean_types():
    text = (REPO / "docs/TOOLS-ARGUMENTS.md").read_text()
    assert "Annotated[" not in text and "Field(" not in text
    assert "- `window_id` (_WindowId" not in text and "`window_id` (int | None, default `None`): Window id from list_windows." in text

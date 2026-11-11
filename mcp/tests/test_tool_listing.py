"""What MCP clients receive. A weak model should be able to use these tools from the listing alone."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from isaac_mcp import grouped_tools, host_catalog, tool_groups
from isaac_mcp import server as gw
from isaac_mcp.scene_contracts import REGISTRY
from isaac_mcp.tool_hints import CATALOG_HINTS

REPO = Path(__file__).resolve().parents[2]
MAX_TOOL_DESCRIPTION = 600
MAX_GROUP_DESCRIPTION = 1500
MAX_PARAMETER_DESCRIPTION = 450
# Tool or "tool.parameter": a limit that must be exceeded for a safety rule to stay in the listing.
# Keep this small, and say why next to each entry.
EXCEPTIONS: dict[str, int] = {}
ALL_GROUPS = "all,screen_input,sim_launch"


def _listing(monkeypatch, tmp_path, single_purpose):
    for name in ("ISAAC_MCP_NAMESPACE", "ISAAC_MCP_BUILTINS", "ISAAC_MCP_SCENE_TOOLS", "ISAAC_MCP_LOCAL_TOOLS",
                 "ISAAC_MCP_LEGACY_TOOLS"):
        monkeypatch.delenv(name, raising=False)
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": ALL_GROUPS, "ISAAC_MCP_SIM_SESSIONS_DIR": str(tmp_path),
                              "ISAAC_MCP_LEGACY_TOOLS": "1" if single_purpose else "0"})

    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            await client.initialize()
            return (await client.list_tools()).tools

    return asyncio.run(run())


@pytest.fixture
def listed(monkeypatch, tmp_path):
    """Every tool, as an MCP client receives it from the real server with no bridge: grouped."""
    return _listing(monkeypatch, tmp_path, single_purpose=False)


@pytest.fixture
def single(monkeypatch, tmp_path):
    """The single-purpose tools, listed as with ISAAC_MCP_LEGACY_TOOLS=1."""
    return _listing(monkeypatch, tmp_path, single_purpose=True)


@pytest.fixture(params=["grouped", "single-purpose"])
def either(request, monkeypatch, tmp_path):
    return _listing(monkeypatch, tmp_path, single_purpose=request.param == "single-purpose")


def limit(name, default):
    if name in tool_groups.GROUPS and default == MAX_TOOL_DESCRIPTION:
        return MAX_GROUP_DESCRIPTION     # one line per action
    return EXCEPTIONS.get(name, default)


def test_every_listed_tool_has_a_description_within_the_limit(either):
    listed = either
    too_long = {t.name: len(t.description or "") for t in listed
                if len(t.description or "") > limit(t.name, MAX_TOOL_DESCRIPTION)}
    assert not [t.name for t in listed if not (t.description or "").strip()]
    assert not too_long, f"Move detail to docs/TOOLS-*.md: {too_long}"


def test_every_listed_parameter_has_a_top_level_description_within_the_limit(either):
    listed = either
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


def test_every_listed_tool_says_whether_it_reads_or_changes_things(either):
    listed = either
    missing = [t.name for t in listed if t.annotations is None or t.annotations.readOnlyHint is None
               or t.annotations.destructiveHint is None]
    assert not missing, missing


def test_schema_titles_are_not_listed(either):
    listed = either
    assert '"title"' not in json.dumps([t.inputSchema for t in listed if "title" not in t.inputSchema.get("properties", {})])


@pytest.mark.parametrize(
    "name",
    ["screenshot", "start_screen_recording", "stop_screen_recording", "join_videos", "run_python", "put_file",
     "ur5e_dt__save_scene_state", "ur5e_dt__new_stage", "assets__pack_build", "isaaclab__runs_tail", "sim__camera__capture"],
)
def test_nothing_that_writes_claims_to_be_read_only(single, name):
    listed = single
    assert next(t for t in listed if t.name == name).annotations.readOnlyHint is False


def test_destructive_tools_say_so(single):
    listed = single
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


def test_the_recording_tool_says_it_ends_with_its_server_process(single):
    listed = single
    """A shell script that starts and stops a recording in separate processes loses it, so say so where the agent reads."""
    text = " ".join(next(t.description for t in listed if t.name == "start_screen_recording").split())
    assert "ends if this MCP server process exits" in text and "one client connected" in text


# ---- the grouped listing ----

def test_a_client_is_shown_groups_and_no_single_purpose_member(listed, single):
    names = {t.name for t in listed}
    old = {t.name for t in single}
    assert len(names) < len(old) / 2.5 and len(names) <= 50
    assert set(tool_groups.GROUPS) <= names
    assert not names & set(tool_groups.members()) and not names & set(tool_groups.SUPERSEDED)
    assert names - set(tool_groups.GROUPS) == old - set(tool_groups.members()) - set(tool_groups.SUPERSEDED), "ungrouped tools are listed as they were"
    size = lambda tools: len(json.dumps([t.model_dump(exclude_none=True) for t in tools]))  # noqa: E731
    assert size(listed) < 0.62 * size(single), "the grouped listing costs a client well under two thirds of the old one"


def test_every_single_purpose_tool_is_reachable_through_a_listed_one(listed, single):
    by_name = {t.name: t for t in listed}
    for tool in single:
        found = tool_groups.members().get(tool.name)
        if found is None:
            assert tool.name in by_name or tool.name in tool_groups.SUPERSEDED, tool.name
            continue
        group, action = found
        schema = by_name[group].inputSchema
        assert action in schema["properties"]["action"]["enum"], (group, action)
        mine = {grouped_tools.OWN_ACTION if name == "action" else name for name in tool.inputSchema.get("properties", {})}
        assert mine <= set(schema["properties"]), (tool.name, mine - set(schema["properties"]))
        assert f"- {action}:" in by_name[group].description


def test_a_group_s_hints_are_the_most_cautious_of_its_members(listed):
    hints = {t.name: t.annotations for t in listed}
    assert hints["sim__logs"].readOnlyHint is True and hints["sim__logs"].destructiveHint is False
    assert hints["files"].readOnlyHint is False and hints["files"].destructiveHint is True
    assert hints["sim__prim"].readOnlyHint is False and hints["sim__prim"].destructiveHint is True
    assert hints["isaaclab__launch"].openWorldHint is True


def test_shared_arguments_are_described_once_and_scoped_ones_say_where_they_apply(listed):
    camera = next(t for t in listed if t.name == "sim__camera").inputSchema["properties"]
    assert camera["session_id"]["description"].startswith("Exact session_id"), "the same for every action: said once"
    assert camera["filename"]["description"].startswith("[capture] ")
    assert camera["path"]["description"].startswith("[create, capture] create: ") and " capture: " in camera["path"]["description"]
    assert "anyOf" not in camera["focal_length"], "an optional argument is not also typed as null"


def test_what_the_server_says_names_tools_as_the_client_is_shown_them(listed, monkeypatch, tmp_path):
    assert tool_groups.listed_form("call sim__stage__info, then sim__prim__delete (not sim__prim__delete_all)") == \
        "call sim__stage action=info, then sim__prim action=delete (not sim__prim__delete_all)"
    assert tool_groups.listed_form("ur5e_dt__quick_start* and list_bridges") == "ur5e_dt__quick_start* and status"
    hidden = set(tool_groups.members()) | set(tool_groups.SUPERSEDED)
    pattern = __import__("re").compile(r"(?<![A-Za-z0-9_])(" + "|".join(sorted(hidden, key=len, reverse=True)) + r")(?![A-Za-z0-9_*])")
    named = {t.name: sorted(set(pattern.findall(t.description + json.dumps(t.inputSchema)))) for t in listed}
    assert not {name: found for name, found in named.items() if found}, "a listed tool names a tool the client cannot see"
    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "none"})
    assert "sim__stage action=info" in server.instructions and "sim__stage__info" not in server.instructions
    assert "sim__stage__info" in gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "none", "ISAAC_MCP_LEGACY_TOOLS": "1"}).instructions


def test_errors_are_translated_and_the_caller_s_own_data_is_not(monkeypatch):
    class Endpoint:
        port = 1

        def call(self, name, params=None, **kwargs):
            if name == "list_tools":
                raise ConnectionRefusedError("offline")
            if name == "run_python":
                return {"output": "", "result": "I printed sim__stage__info myself"}
            if name == "sim.stage.info":
                return {"status": "ok", "next": "create something with sim__prim__create"}
            return {"status": "error", "code": "STALE_TARGET", "message": "Call sim__stage__info for the new IDs."}

        def close(self):
            pass

    server = gw.build_server({"ISAAC_MCP_LOCAL_TOOLS": "none"})
    gw._state_for(server).client = Endpoint()

    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            await client.initialize()
            refused = await client.call_tool("sim__prim", {"action": "get", "session_id": "a", "stage_id": "b", "path": "/World"})
            data = await client.call_tool("run_python", {"code": "result = 1"})
            return refused, data, await client.call_tool("sim__stage", {"action": "info"})

    refused, data, answered = asyncio.run(run())
    assert not answered.isError and "create something with sim__prim action=create" in answered.content[0].text
    text = refused.content[0].text
    assert refused.isError and "Call sim__stage action=info for the new IDs." in text
    assert text.count("Error executing tool") == 1, text
    assert "I printed sim__stage__info myself" in data.content[0].text

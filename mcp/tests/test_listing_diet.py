"""The listed tool text is short; the full text stays one call away and warnings stay in the listing."""

import asyncio
import json

import pytest

from twinsmith import grouped_tools, server, tool_groups


def listing(selection="all"):
    instance = server.build_server({"TWINSMITH_TOOLSETS": selection})
    state = server._state_for(instance)
    state.get_client = lambda: (_ for _ in ()).throw(ConnectionRefusedError("Connection refused"))
    return instance, asyncio.run(instance.list_tools())


@pytest.fixture(scope="module")
def everything():
    return listing("all")


def test_clip_prefers_the_first_sentence_then_clauses_then_whole_words():
    clip = grouped_tools._clip
    assert clip("Short text.", 50) == "Short text."
    assert clip("First sentence here. A second one follows that is long.", 30) == "First sentence here"
    cut = clip("Absolute folders put first on PYTHONPATH (the install's own path stays), such as a src folder, and more", 60)
    assert cut == "Absolute folders put first on PYTHONPATH" and cut.count("(") == cut.count(")")
    text = "one two three four five six seven eight nine ten eleven twelve"
    words = clip(text, 30)
    assert words.endswith(" ...") and text.startswith(words[:-4]) and text[len(words) - 4] == " "


@pytest.mark.parametrize("text", ["alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho",
                                  "Reads a thing (with a long (nested, parenthesised) remark that never ends in time for the cut) now"])
def test_clip_never_cuts_a_word_or_leaves_a_bracket_open(text):
    for limit in range(12, len(text)):
        out = grouped_tools._clip(text, limit)
        assert len(out) <= limit + 0 or not out.endswith(" ...") or len(out) <= limit
        body = out[:-4] if out.endswith(" ...") else out
        assert text.startswith(body) and (len(body) == len(text) or text[len(body)] in " ,;:.-")
        assert body.count("(") <= body.count(")")


def test_shortened_description_keeps_the_warning_and_names_where_the_rest_is():
    hint = ' Full text: help(tool="x").'
    text = ("Does a harmless thing with a long account of how. " + "Filler sentence number one is here to add length. " * 8
            + "WARNING: executes arbitrary code with your rights. " + "More filler follows after that. " * 4)
    short, lost = grouped_tools.shorten_description(text, hint, 400)
    assert lost and len(short) <= 420 and short.startswith("Does a harmless thing") and "executes arbitrary code" in short
    assert short.endswith(hint)
    whole, lost = grouped_tools.shorten_description("Tiny.", hint, 400)
    assert (whole, lost) == ("Tiny.", False)


def test_listed_tools_stay_within_the_caps(everything):
    _, tools = everything
    for tool in tools:
        group = tool.name in tool_groups.GROUPS
        assert len(tool.description) <= (600 if group else 420), (tool.name, len(tool.description))
        for name, spec in tool.inputSchema.get("properties", {}).items():
            assert len(spec.get("description", "")) <= grouped_tools.MAX_ARGUMENT_DESCRIPTION, (tool.name, name)
            assert {"type": "null"} not in spec.get("anyOf", []), (tool.name, name, "or null is listed")
            assert spec.get("default", 0) is not None, (tool.name, name, "a null default is listed")


def test_no_listed_text_ends_in_the_middle_of_a_word(everything):
    _, tools = everything
    for tool in tools:
        for name, spec in tool.inputSchema.get("properties", {}).items():
            text = spec.get("description", "")
            if text.endswith("..."):          # a cut: at a whole word that is not a dangling connective
                assert not text[:-4].endswith((" a", " the", " of", " to", " or", " and", " with")), (tool.name, name, text)


def test_a_group_lists_every_action_and_what_it_needs(everything):
    _, tools = everything
    prim = next(t for t in tools if t.name == "sim__prim")
    for action in prim.inputSchema["properties"]["action"]["enum"]:
        assert f"- {action}" in prim.description
    assert "- delete: Needs path, confirm." in prim.description or "delete: Needs path, confirm" in prim.description


def test_help_returns_the_members_text_as_written(everything):
    instance, _ = everything
    answer = json.loads(asyncio.run(instance.call_tool("sim__prim", {"action": "help"}))[0].text)
    created = instance._tool_manager._tools["sim__prim__create"]
    assert answer["create"]["description"] == tool_groups.listed_form(created.description) and len(created.description) > 200
    assert set(answer["create"]["arguments"]) == set(created.parameters["properties"])   # every argument, with its full text
    assert answer["create"]["arguments"]["material"]["anyOf"] == created.parameters["properties"]["material"]["anyOf"]
    assert any(len(spec.get("description", "")) > 120 for spec in answer["create"]["arguments"].values())


def test_help_tool_returns_the_full_text_of_a_shortened_tool(everything):
    instance, tools = everything
    run_script = next(t for t in tools if t.name == "run_script")
    assert "executes arbitrary code" in run_script.description and 'help(tool="run_script")' in run_script.description
    full = json.loads(server._tool_help(instance, "run_script"))["run_script"]
    assert len(full["description"]) > len(run_script.description) and "no Isaac Lab" in full["description"]
    assert full["arguments"]["uses_gpu"]["description"].startswith("true: the script uses the GPU")
    assert max(len(spec.get("description", "")) for spec in full["arguments"].values()) > 120
    assert "action=\"help\"" in server._tool_help(instance, "sim__prim") or "action=\"help\"" in server._tool_help(instance, "sim__prim").replace("\\", "")
    assert "No listed tool is called" in server._tool_help(instance, "nonsense")
    member = json.loads(server._tool_help(instance, "sim__prim__create"))["sim__prim__create"]
    assert member["description"] == tool_groups.listed_form(instance._tool_manager._tools["sim__prim__create"].description)
    via_tool = instance._tool_manager._tools["help"].fn(tool="run_script")
    assert json.loads(via_tool)["run_script"]["description"] == full["description"]


def test_warnings_survive_the_diet(everything):
    _, tools = everything
    by_name = {t.name: t for t in tools}
    for name, phrase in (("run_python", "Execute Python code"), ("run_script", "executes arbitrary code"),
                         ("sim__controller", "CODE EXECUTION"), ("sim__ui", "can delete data"),
                         ("isaaclab__launch", "uses the GPU"), ("robosim", "uses the GPU")):
        assert phrase in by_name[name].description, name
    assert "confirm" in by_name["sim__prim"].description and "confirm" in by_name["sim__stage"].description
    assert by_name["run_python"].annotations.destructiveHint is True


def test_a_tool_that_lost_nothing_has_no_hint(everything):
    _, tools = everything
    reload = next(t for t in tools if t.name == "reload_tools")
    assert "help(tool=" not in reload.description

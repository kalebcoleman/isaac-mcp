"""sim.graph.build spec checks, sim.stage.open path checks, and the save composition-error filter.

From DeepSeek student run 4: every graph was built by hand in run_python, the scene was opened in
run_python, and saving a scene with a ROS camera was a dead end because the lab handout has two
dangling internal references.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXTS = REPO / "exts/sim-tools"


def load(name):
    spec = importlib.util.spec_from_file_location(f"sim_tools_{name}_under_test", EXTS / f"sim_tools/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gb = load("graph_build")
so = load("stage_open")

NODES = [["tick", "omni.graph.action.OnPlaybackTick"], ["pub", "isaacsim.ros2.bridge.ROS2PublishClock"]]


def test_spec_accepts_the_lab_clock_graph_and_full_paths():
    spec = gb.parse_spec(dict(path="/World/ROS_Clock", nodes=NODES,
                              values={"pub.inputs:topicName": "/clock"},
                              connections=[["/World/ROS_Clock/tick.outputs:tick", "pub.inputs:execIn"]],
                              targets={"pub.inputs:targetPrim": "/World/robot"}))
    assert spec["nodes"] == [("tick", NODES[0][1]), ("pub", NODES[1][1])]
    assert spec["connections"] == [(("tick", "outputs:tick"), ("pub", "inputs:execIn"))]
    assert spec["targets"] == [("pub", "inputs:targetPrim", ["/World/robot"])]
    assert spec["evaluator"] == "execution"


@pytest.mark.parametrize("bad, code", [
    (dict(nodes=[]), "INVALID_VALUE"),
    (dict(nodes=[["tick", "OnPlaybackTick"]]), "INVALID_VALUE"),              # not a full type id
    (dict(nodes=NODES + [["tick", NODES[1][1]]]), "INVALID_VALUE"),           # duplicate name
    (dict(nodes=NODES, connections=[["tock.outputs:tick", "pub.inputs:execIn"]]), "UNKNOWN_NODE"),
    (dict(nodes=NODES, connections=[["tick.outputs:tick", "pub.outputs:x"]]), "INVALID_VALUE"),
    (dict(nodes=NODES, values={"pub.outputs:x": 1}), "INVALID_VALUE"),
    (dict(nodes=NODES, values={"pub.topicName": 1}), "INVALID_VALUE"),        # port without inputs:
    (dict(nodes=NODES, targets={"pub.inputs:targetPrim": ["World/robot"]}), "INVALID_VALUE"),
    (dict(nodes=NODES, evaluator="lazy"), "INVALID_VALUE"),
])
def test_spec_refuses_bad_input_with_a_code(bad, code):
    with pytest.raises(gb.SpecError) as err:
        gb.parse_spec(dict(path="/World/G", **bad))
    assert err.value.code == code


def test_token_keys_are_stored_as_their_values():
    # Scale To/From Stage Units: the student's "toStage" made the node output zeros silently.
    raw = {"toStage": "Convert to stage units", "toMeters": "Convert to meters"}
    assert gb.token_value("Convert to stage units", raw) == ("Convert to stage units", None)
    value, note = gb.token_value("toStage", raw)
    assert value == "Convert to stage units" and "token key" in note
    with pytest.raises(gb.SpecError, match="Convert to meters"):
        gb.token_value("stage", raw)
    assert gb.token_value(3, raw) == (3, None) and gb.token_value("x", None) == ("x", None)


def test_dynamic_input_pattern():
    assert gb.DYNAMIC_INPUT.match("inputs:input1") and not gb.DYNAMIC_INPUT.match("inputs:inputs")


def test_stage_open_path_checks(tmp_path):
    usd = tmp_path / "scene.usda"
    usd.write_text("#usda 1.0\n")
    assert so.check_path(str(usd), home=tmp_path) == (usd.resolve(), None)
    assert so.check_path("scene.usda", home=tmp_path)[1][0] == "INVALID_VALUE"
    assert so.check_path(str(tmp_path / "x.txt"), home=tmp_path)[1][0] == "INVALID_VALUE"
    assert so.check_path(str(tmp_path / "missing.usd"), home=tmp_path)[1][0] == "NOT_FOUND"
    assert so.check_path("/etc/passwd.usda", home=tmp_path)[1][0] == "OUTSIDE_HOME"


def test_a_new_kit_stage_has_nothing_to_lose():
    # A fresh Kit's anonymous stage is always "dirty"; refusing there blocked every first open.
    kit = ["Render", "OmniverseKit_Persp", "OmniverseKit_Front", "OmniverseKit_Top", "OmniverseKit_Right"]
    assert not so.unsaved_work(True, True, kit)
    assert so.unsaved_work(True, True, kit + ["World"])
    assert so.unsaved_work(False, True, kit) and not so.unsaved_work(False, False, ["World"])


def test_dangling_internal_references_do_not_block_camera_saves():
    pytest.importorskip("pxr.Usd")
    from pxr import Usd

    sys.path.insert(0, str(EXTS))
    try:
        render_export = load("render_export")
    finally:
        sys.path.remove(str(EXTS))
    stage = Usd.Stage.CreateInMemory()
    prim = stage.DefinePrim("/robot/visuals")
    prim.GetReferences().AddInternalReference("/visuals/missing")  # what the lab handout ships with
    errors = stage.GetCompositionErrors()
    assert errors and render_export.blocking_composition_errors(errors) == []
    other = stage.DefinePrim("/other")
    other.GetReferences().AddReference("/nonexistent/file.usda")
    blocking = render_export.blocking_composition_errors(stage.GetCompositionErrors())
    assert blocking and all(type(e).__name__ != "ErrorUnresolvedPrimPath" for e in blocking)


def test_node_types_report_allowed_token_values():
    graph = load("graph")
    assert graph.allowed_values({"allowedTokens": {"toStage": "Convert to stage units"}}) == ["Convert to stage units"]
    assert graph.allowed_values({"metadata": {"allowedTokens": "a,b"}}) == ["a", "b"]
    assert graph.allowed_values({"allowedTokens": ["x"]}) == ["x"] and graph.allowed_values({}) == []
    ports = graph.ogn_ports({"t": {"inputs": {"conversion": {"type": "token", "allowedTokens":
                                                              {"toStage": "Convert to stage units"}}}}}, "t")
    assert ports["inputs"]["inputs:conversion"]["allowed_values"] == ["Convert to stage units"]


def test_allowed_tokens_in_list_and_string_form():
    # Review of #64: Isaac's ALLOWED_TOKENS_RAW can be a JSON list or a comma string, not only a dict.
    assert gb.token_value("B", ["foo", "B"]) == ("B", None)
    assert gb.token_value("foo", "foo,B") == ("foo", None)
    with pytest.raises(gb.SpecError):
        gb.token_value("x", ["foo", "B"])
    assert gb.token_map({"k": "v"}) == {"k": "v"} and gb.token_map(None) == {}

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


def test_antigravity_serialized_clock_payload_and_nested_targets():
    spec = gb.parse_spec({
        "path": "/World/ROS_Clock",
        "nodes": ['["tick", "omni.graph.action.OnPlaybackTick"]',
                  '["context", "isaacsim.ros2.bridge.ROS2Context"]',
                  '["time", "isaacsim.core.nodes.IsaacReadSimulationTime"]',
                  '["clock", "isaacsim.ros2.bridge.ROS2PublishClock"]'],
        "connections": ['["tick.outputs:tick", "clock.inputs:execIn"]',
                        '["context.outputs:context", "clock.inputs:context"]',
                        '["time.outputs:simulationTime", "clock.inputs:timeStamp"]'],
        "values": '{"clock.inputs:topicName": "/clock", "clock.inputs:queueSize": 10, '
                  '"context.inputs:domain_id": 0, "context.inputs:useDomainIDEnvVar": false, '
                  '"time.inputs:resetOnStop": true}',
        "targets": {"clock.inputs:targetPrim": '["/World/robot"]'},
    })
    assert spec["nodes"][0] == ("tick", "omni.graph.action.OnPlaybackTick")
    assert spec["connections"][0] == (("tick", "outputs:tick"), ("clock", "inputs:execIn"))
    assert ("clock", "inputs:topicName", "/clock") in spec["values"]
    assert ("clock", "inputs:queueSize", 10) in spec["values"]
    assert spec["targets"] == [("clock", "inputs:targetPrim", ["/World/robot"])]


def test_json_whole_containers_object_entries_and_flat_lists():
    spec = gb.parse_spec(dict(nodes='["tick", "omni.graph.action.OnPlaybackTick", '
                                    '"pub", "isaacsim.ros2.bridge.ROS2PublishClock"]',
                              connections='["tick.outputs:tick", "pub.inputs:execIn"]',
                              values='{"pub.inputs:topicName":"/clock"}',
                              targets='{"pub.inputs:targetPrim":"[\\"/World/robot\\"]"}'))
    assert spec["nodes"] == [("tick", NODES[0][1]), ("pub", NODES[1][1])]
    assert spec["targets"] == [("pub", "inputs:targetPrim", ["/World/robot"])]
    object_spec = gb.parse_spec(dict(nodes=['{"name":"tick","type":"omni.graph.action.OnPlaybackTick"}',
                                             '{"name":"pub","type":"isaacsim.ros2.bridge.ROS2PublishClock"}'],
                                     connections=['{"from":"tick.outputs:tick","to":"pub.inputs:execIn"}']))
    assert object_spec["nodes"] == spec["nodes"]
    assert object_spec["connections"] == spec["connections"]


@pytest.mark.parametrize("bad, message", [
    ({"nodes": '[bad'}, "nodes: expected"),
    ({"nodes": ["tick", "omni.graph.action.OnPlaybackTick", "orphan"]}, "even number"),
    ({"nodes": NODES, "connections": ['["bad"']}, r"connections\[0\]: expected"),
    ({"nodes": NODES, "values": '[]'}, "values: an object"),
    ({"nodes": NODES, "targets": '{bad'}, "targets: expected"),
    ({"nodes": NODES, "values": {"pub.outputs:x": 1}}, "only inputs"),
])
def test_serialized_spec_errors_are_actionable(bad, message):
    with pytest.raises(gb.SpecError, match=message):
        gb.parse_spec(bad)


@pytest.mark.parametrize("bad, field", [
    ({"nodes": "[" * 2000 + "]" * 2000}, "nodes"),
    ({"nodes": "[" * 20000 + "]" * 20000}, "nodes"),
    ({"nodes": ["[" * 2000 + "]" * 2000]}, "nodes"),
    ({"nodes": NODES, "values": '{"pub.inputs:value": NaN}'}, "values"),
    ({"nodes": NODES, "values": '{"pub.inputs:value": Infinity}'}, "values"),
    ({"nodes": NODES, "targets": {"pub.inputs:targetPrim": "[" * 2000 + "]" * 2000}}, "targets"),
])
def test_invalid_json_nesting_and_constants_have_actionable_errors(bad, field):
    with pytest.raises(gb.SpecError) as error:
        gb.parse_spec(bad)
    assert error.value.code == "INVALID_VALUE"
    assert field in str(error.value) and "expected" in str(error.value)


def test_serialized_spec_retains_node_and_connection_limits():
    with pytest.raises(gb.SpecError, match="at most 64 pairs"):
        gb.parse_spec({"nodes": ['["tick", "omni.graph.action.OnPlaybackTick"]'] * 65})
    with pytest.raises(gb.SpecError, match="at most 256 pairs"):
        gb.parse_spec({"nodes": NODES, "connections": ["tick.outputs:tick", "pub.inputs:execIn"] * 257})


@pytest.mark.parametrize("bad, code", [
    (dict(nodes=[]), "INVALID_VALUE"),
    (dict(nodes=[["tick", "OnPlaybackTick"]]), "INVALID_VALUE"),              # not a full type id
    (dict(nodes=NODES + [["tick", NODES[1][1]]]), "INVALID_VALUE"),           # duplicate name
    (dict(nodes=NODES, connections=[["tock.outputs:tick", "pub.inputs:execIn"]]), "UNKNOWN_NODE"),
    (dict(nodes=NODES, connections=[["tick.outputs:tick", "pub.outputs:x"]]), "INVALID_VALUE"),
    (dict(nodes=NODES, values={"pub.outputs:x": 1}), "INVALID_VALUE"),
    (dict(nodes=NODES, values={"pub": 1}), "INVALID_VALUE"),                  # no port at all
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


# ---- what sim.stage.save writes by default can be opened again ----


def _own_output(tmp_path):
    root = tmp_path / "scenes-1"
    root.mkdir()
    root.chmod(0o700)
    (root / "saved.usda").write_text("#usda 1.0\n")
    return root


def test_the_processs_own_output_root_can_be_opened(tmp_path):
    root = _own_output(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    file, problem = so.check_path(str(root / "saved.usda"), home=home, output_root=root)
    assert problem is None and file == (root / "saved.usda").resolve()


def test_without_it_the_same_file_is_outside_home(tmp_path):
    root = _own_output(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    assert so.check_path(str(root / "saved.usda"), home=home)[1][0] == "OUTSIDE_HOME"


def test_a_sibling_folder_with_a_matching_prefix_still_refuses(tmp_path):
    root = _own_output(tmp_path)
    sibling = tmp_path / "scenes-12"
    sibling.mkdir()
    (sibling / "other.usda").write_text("#usda 1.0\n")
    home = tmp_path / "home"
    home.mkdir()
    problem = so.check_path(str(sibling / "other.usda"), home=home, output_root=root)[1]
    assert problem[0] == "OUTSIDE_HOME" and str(root) in problem[1]


def test_a_symlink_out_of_the_output_root_still_refuses(tmp_path):
    root = _own_output(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.usda").write_text("#usda 1.0\n")
    (root / "link.usda").symlink_to(outside / "secret.usda")
    home = tmp_path / "home"
    home.mkdir()
    assert so.check_path(str(root / "link.usda"), home=home, output_root=root)[1][0] == "OUTSIDE_HOME"


def test_a_world_writable_output_root_is_not_trusted(tmp_path):
    root = _own_output(tmp_path)
    root.chmod(0o777)
    home = tmp_path / "home"
    home.mkdir()
    assert so.check_path(str(root / "saved.usda"), home=home, output_root=root)[1][0] == "OUTSIDE_HOME"


def test_a_missing_output_root_refuses_rather_than_raises(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert so.check_path("/etc/passwd.usda", home=home, output_root=tmp_path / "never")[1][0] == "OUTSIDE_HOME"


@pytest.mark.parametrize("source,target,why", [
    (("uint64", 1, 0, "none"), ("float", 1, 1, "none"), "array"),  # dataPtr into linearDepthData, the reported wire
    (("uint", 1, 0, "execution"), ("uint", 1, 0, "none"), "execution"),
    (("float", 3, 0, "none"), ("float", 1, 0, "none"), "tuple"),
    (("token", 1, 0, "none"), ("double", 1, 0, "none"), "text"),
    (("uchar", 1, 1, "none"), ("token", 1, 0, "none"), "array"),  # raw bytes, not text
])
def test_type_mismatch_names_a_wire_kit_would_only_warn_about(source, target, why):
    assert why in gb.type_mismatch(source, target)


@pytest.mark.parametrize("source,target", [
    (("uint64", 1, 0, "none"), ("uint64", 1, 0, "none")),
    (("float", 3, 0, "none"), ("double", 3, 0, "vector")),  # widths convert, and roles other than execution don't matter
    (("unknown", 1, 0, "none"), ("float", 1, 1, "none")),  # an extended port resolves when connected
    (("token", 1, 0, "none"), ("string", 1, 0, "none")),
    (("token", 1, 0, "none"), ("uchar", 1, 1, "text")),  # how OmniGraph models string; measured on 6.0.0.0
    (("uchar", 1, 1, "text"), ("token", 1, 0, "none")),
])
def test_type_mismatch_leaves_compatible_wires_alone(source, target):
    assert gb.type_mismatch(source, target) is None


class _Attr:
    def __init__(self, path, upstream=()):
        self.path, self.upstream = path, upstream

    def get_path(self):
        return self.path

    def get_upstream_connections(self):
        return self.upstream


def test_wire_dropped_tells_a_refused_connection_from_a_made_one():
    source = _Attr("/g/a.outputs:x")
    assert not gb.wire_dropped(source, _Attr("/g/b.inputs:y", [source]))
    assert gb.wire_dropped(source, _Attr("/g/b.inputs:y", []))
    assert gb.wire_dropped(source, _Attr("/g/b.inputs:y", [_Attr("/g/other.outputs:z")]))
    assert not gb.wire_dropped(source, object())  # nothing to read on this build: Kit's choice stands


def test_script_nodes_stay_off_without_the_users_consent(monkeypatch):
    # sim__graph__build must not be a way around SCRIPT_DISABLED: a ScriptNode runs Python on Play.
    monkeypatch.delenv("ISAAC_MCP_ENABLE_SCENE_SCRIPTS", raising=False)
    assert gb.scripts_disabled()
    monkeypatch.setenv("ISAAC_MCP_ENABLE_SCENE_SCRIPTS", "1")
    assert not gb.scripts_disabled()
    assert set(gb.SCRIPT_PORTS) == {"inputs:script", "inputs:scriptPath"}

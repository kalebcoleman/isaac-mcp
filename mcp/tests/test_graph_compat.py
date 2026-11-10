"""Real composed USD graph preservation; native execution remains a live gate."""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytest.importorskip("pxr.Usd")
from pxr import Sdf, Usd

path = Path(__file__).resolve().parents[2] / "exts/ur5e-dt/ur5e_dt/graph_compat.py"
spec = importlib.util.spec_from_file_location("graph_compat_test", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def graph(tmp_path):
    source = Usd.Stage.CreateNew(str(tmp_path / "graph.usda"))
    source.DefinePrim("/Graph/Fixed", "OmniGraph")
    for name, kind in [("tick", "omni.graph.action.OnPlaybackTick"),
                       ("pub", "isaacsim.ros2.nodes.ROS2Publisher")]:
        node = source.DefinePrim("/Graph/Fixed/" + name, "OmniGraphNode")
        node.CreateAttribute("node:type", Sdf.ValueTypeNames.Token).Set(kind)
    tick = source.GetPrimAtPath("/Graph/Fixed/tick")
    tick.CreateAttribute("outputs:tick", Sdf.ValueTypeNames.Token)
    pub = source.GetPrimAtPath("/Graph/Fixed/pub")
    pub.CreateAttribute("inputs:topicName", Sdf.ValueTypeNames.String).Set("test")
    pub.CreateAttribute("inputs:execIn", Sdf.ValueTypeNames.Token).SetConnections(
        [Sdf.Path("/Graph/Fixed/tick.outputs:tick")]
    )
    source.GetRootLayer().Save()
    stage = Usd.Stage.Open(source.GetRootLayer())
    stage.SetEditTarget(stage.GetSessionLayer())
    calls = []
    keys = SimpleNamespace(CREATE_NODES="create", SET_VALUES="set", CONNECT="connect")
    og = SimpleNamespace(
        Controller=SimpleNamespace(
            Keys=keys, edit=lambda *a: calls.append(a),
            node=lambda name: SimpleNamespace(is_valid=lambda: True),
        ),
        get_graph_by_path=lambda name: name,
    )
    plan = {"create": [("tick", "omni.graph.action.OnPlaybackTick"),
                       ("pub", "isaacsim.ros2.bridge.ROS2Publisher")],
            "set": [("pub.inputs:topicName", "test")],
            "connect": [("tick.outputs:tick", "pub.inputs:execIn")]}
    return stage, og, plan, calls, source.GetRootLayer().ExportToString()


def test_matching_graph_is_reused_above_source_layer_without_authoring(graph):
    stage, og, plan, calls, before = graph
    session = stage.GetSessionLayer().ExportToString()
    for _ in range(2):
        result = module.ensure_fixed_graph(stage, og, {"graph_path": "/Graph/Fixed"}, plan)
        assert len(result[1]) == 2
    assert not calls
    assert stage.GetRootLayer().ExportToString() == before
    assert stage.GetSessionLayer().ExportToString() == session
    assert stage.GetEditTarget().GetLayer() == stage.GetSessionLayer()


@pytest.mark.parametrize("change", ["node", "input", "connection", "type"])
def test_conflicting_graph_is_preserved_and_refused(graph, change):
    stage, og, plan, calls, _ = graph
    if change == "node":
        stage.GetPrimAtPath("/Graph/Fixed/pub").GetAttribute("node:type").Set("Unrelated")
    elif change == "input":
        stage.GetAttributeAtPath("/Graph/Fixed/pub.inputs:topicName").Set("user-topic")
    elif change == "connection":
        stage.GetAttributeAtPath("/Graph/Fixed/pub.inputs:execIn").SetConnections([])
    else:
        stage.GetPrimAtPath("/Graph/Fixed").SetTypeName("Xform")
    before = stage.GetSessionLayer().ExportToString()
    with pytest.raises(ValueError, match="preserved"):
        module.ensure_fixed_graph(stage, og, {"graph_path": "/Graph/Fixed"}, plan)
    assert not calls and stage.GetSessionLayer().ExportToString() == before


def test_new_graph_uses_native_controller_once(graph):
    stage, og, plan, calls, _ = graph
    module.ensure_fixed_graph(stage, og, {"graph_path": "/Graph/New"}, plan)
    assert len(calls) == 1 and calls[0][0]["graph_path"] == "/Graph/New"


def _compat():
    spec = importlib.util.spec_from_file_location("sim_tools_compat_under_test", ROOT / "exts/sim-tools/sim_tools/compat.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _omni(monkeypatch, tools_keys, core_keys):
    graph, core, tools, ogn, omni = (ModuleType(n) for n in
                                     ("omni.graph", "omni.graph.core", "omni.graph.tools", "omni.graph.tools.ogn", "omni"))
    core.MetadataKeys = core_keys
    if tools_keys is not None:
        ogn.MetadataKeys = tools_keys
    omni.graph, graph.core, graph.tools, tools.ogn = graph, core, tools, ogn
    for name, module in (("omni", omni), ("omni.graph", graph), ("omni.graph.core", core),
                         ("omni.graph.tools", tools), ("omni.graph.tools.ogn", ogn)):
        monkeypatch.setitem(sys.modules, name, module)


def test_metadata_keys_prefers_the_current_home_and_falls_back(monkeypatch):
    compat = _compat()
    _omni(monkeypatch, "new", "old")
    assert compat.metadata_keys() == "new"
    _omni(monkeypatch, None, "old")
    assert compat.metadata_keys() == "old"


def test_graph_tools_do_not_use_the_deprecated_metadata_name():
    for name in ("graph.py", "graph_build.py"):
        assert "og.MetadataKeys" not in (ROOT / "exts/sim-tools/sim_tools" / name).read_text()

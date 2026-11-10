"""The simulator UI helpers (key names, widget records, fingerprints), loaded without Kit."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _ui_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "exts/sim-tools/sim_tools/ui.py"
    spec = importlib.util.spec_from_file_location("sim_tools_ui_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ui_keys_follow_openai_style_names():
    ui = _ui_module()
    assert ui.parse_keys(["ctrl", "s"]) == (["CONTROL"], "S")
    assert ui.parse_keys(["Enter"]) == ([], "ENTER")
    assert ui.parse_keys(["shift", "alt", "tab"]) == (["SHIFT", "ALT"], "TAB")
    assert ui.parse_keys(["7"]) == ([], "KEY_7")
    assert ui.parse_keys(["f5"]) == ([], "F5")
    for bad in ([], ["ctrl"], ["a", "b"], ["ctrl", "ctrl", "a"], ["hyper", "a"], "ctrl+s", [""] ):
        with pytest.raises(ui.UIRefusal):
            ui.parse_keys(bad)


def test_ui_widget_records_are_in_window_pixels_and_match_by_text():
    ui = _ui_module()
    rec = ui.widget_record("ToolButton", "Main ToolBar//Frame/VStack[0]/ToolButton[5]", "Main ToolBar", "play", "",
                           "Play ('Space')", None, (20, 302), (34, 34), 1.5, identifier="play")
    assert (rec["x"], rec["y"], rec["width"]) == (30, 453, 51)
    assert ui.matches(rec, "PLAY") and ui.matches(rec, "space") and not ui.matches(rec, "stop")
    assert ui.matches(rec, "") and "value" not in rec and "enabled" not in rec
    field = ui.widget_record("StringField", "r", "Stage", "", "", "", "Cube", (1, 1), (2, 2), 1.0, enabled=False)
    assert field["value"] == "Cube" and field["enabled"] is False and ui.matches(field, "cube")


def test_ui_refs_carry_a_fingerprint_of_the_widget():
    ui = _ui_module()
    fp = ui.fingerprint("ToolButton", identifier="play")
    assert fp == "ToolButton:play"
    assert ui.fingerprint("Label", text="Cube") == "Label:Cube"
    assert ui.fingerprint("StringField", text="typed text") == "StringField:"  # values change; not identity
    assert ui.fingerprint("Button", name="a|b") == "Button:a/b"
    assert ui.fingerprint("FloatField", context="Translate") == "FloatField:@Translate"
    assert ui.fingerprint("FloatField", identifier="x", context="Translate") == "FloatField:x"
    ref = "Main ToolBar//Frame/VStack[0]/ToolButton[5]|" + fp
    assert ui.split_ref(ref) == ("Main ToolBar//Frame/VStack[0]/ToolButton[5]", fp)
    assert ui.split_ref("Main ToolBar//Frame/VStack[0]/ToolButton[5]") == (
        "Main ToolBar//Frame/VStack[0]/ToolButton[5]", None)
    assert ui.inside((5, 5), (0, 0, 10, 10)) and not ui.inside((10, 5), (0, 0, 10, 10))


def test_property_fields_are_fingerprinted_by_the_attribute_they_edit():
    # Every Property value field is identifier "value"; after selecting another prim the
    # same path is another prim's field. The bound USD attribute and channel tell them apart.
    ui = _ui_module()

    class Model:
        def __init__(self, path, channel):
            self.path, self._channel_index = path, channel

        def get_attribute_paths(self):
            return [self.path]

    class FloatDrag:
        identifier, name, text = "value", "value", ""

        def __init__(self, path, channel):
            self.model = Model(path, channel)

    cube = ui._widget_fingerprint(FloatDrag("/Cube.xformOp:translate", 1), "Property//Frame/x", None)
    sphere = ui._widget_fingerprint(FloatDrag("/Sphere.xformOp:translate", 1), "Property//Frame/x", None)
    assert cube == "FloatDrag:value@/Cube.xformOp:translate#1" and cube != sphere
    assert ui.split_ref("Property//Frame/x|" + cube)[1] == cube


class FakeWindow:
    def __init__(self, title):
        self.title, self.visible, self.docked = title, True, False
        self.position_x, self.position_y, self.width, self.height = 0, 0, 500, 500

    def is_selected_in_dock(self):
        return True


class Queryable(FakeWindow):
    pass


@pytest.fixture
def kit_ui(monkeypatch):
    """ui.py with a fake omni.ui and a ui_test that records every path Kit would be asked for."""
    spec = importlib.util.spec_from_file_location("sim_tools_ui_kit_stubs", ROOT / "exts/sim-tools/sim_tools/ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    workspace = SimpleNamespace(get_dpi_scale=lambda: 1.0, get_windows=lambda: fake.windows,
                                get_window=lambda title: next((w for w in fake.windows if w.title == title), None))
    fake = ModuleType("omni.ui")
    fake.windows = [Queryable("Stage"), FakeWindow("Status Bar")]
    fake.Workspace, fake.Window = workspace, Queryable
    fake.Inspector = SimpleNamespace(get_children=lambda widget: [])
    omni = ModuleType("omni")
    omni.ui = fake
    monkeypatch.setitem(sys.modules, "omni", omni)
    monkeypatch.setitem(sys.modules, "omni.ui", fake)
    queries = []

    class Button:
        visible, enabled = True, True
        identifier, name, text = "b", "", ""

    target = SimpleNamespace(widget=Button(), center=SimpleNamespace(x=10, y=10), size=SimpleNamespace(x=5, y=5))
    queried = SimpleNamespace(find=lambda path: (queries.append(path), target)[1],
                              find_all=lambda path: (queries.append(path), [])[1])
    monkeypatch.setattr(module, "_ui_test", lambda: queried)
    return SimpleNamespace(module=module, queries=queries, target=target, button=Button)


def test_ancestor_paths_are_only_ones_kit_can_query():
    spec = importlib.util.spec_from_file_location("sim_tools_ui_paths", ROOT / "exts/sim-tools/sim_tools/ui.py")
    ui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ui)
    assert ui._ancestors("Stage//Frame/VStack[0]/HStack[1]/Button[2]") == [
        "Stage//Frame/VStack[0]", "Stage//Frame/VStack[0]/HStack[1]"]
    assert ui._ancestors("Stage//Frame/Button[0]") == []
    assert ui._ancestors("A/B Window//Frame/VStack[0]/Label[0]") == ["A/B Window//Frame/VStack[0]"]
    assert ui._ancestors("no-window") == []


def _assert_no_bare_prefixes(queries):
    for path in queries:
        title, sep, rest = path.partition("//")
        assert sep and rest.startswith("Frame/"), f"Kit logs a warning for {path!r}"


def test_a_ref_action_queries_no_path_that_makes_kit_warn(kit_ui):
    path = "Stage//Frame/VStack[0]/HStack[1]/Button[2]"
    kit_ui.module._resolve({"ref": f"{path}|Button:b"})
    assert path in kit_ui.queries
    _assert_no_bare_prefixes(kit_ui.queries)


def test_a_disabled_container_still_disables_its_child(kit_ui):
    path = "Stage//Frame/VStack[0]/Button[2]"
    disabled = SimpleNamespace(widget=SimpleNamespace(enabled=False))
    kit_ui.module._ui_test().find = lambda p: kit_ui.target if p == path else disabled
    assert kit_ui.module._resolve({"ref": f"{path}|Button:b"})[2]["enabled"] is False


def test_label_context_walks_only_queryable_parents(kit_ui):
    assert kit_ui.module._context("Stage//Frame/VStack[0]/HStack[1]/Field[0]", kit_ui.module._ui_test()) == ""
    assert kit_ui.queries == ["Stage//Frame/VStack[0]/HStack[1]", "Stage//Frame/VStack[0]"]


def test_find_skips_windows_kit_cannot_query(kit_ui):
    assert list(kit_ui.module._candidates(None, False, "")) == []
    assert kit_ui.queries == ["Stage//Frame/**/*"]


def test_hidden_ref_says_an_inline_editor_may_have_replaced_it(kit_ui):
    kit_ui.target.widget.visible = False
    with pytest.raises(kit_ui.module.UIRefusal) as err:
        kit_ui.module._resolve({"ref": "Stage//Frame/VStack[0]/Button[2]|Button:b"})
    assert err.value.code == "STALE_WIDGET"
    assert "inline editor" in str(err.value) and "sim__ui__find" in str(err.value)

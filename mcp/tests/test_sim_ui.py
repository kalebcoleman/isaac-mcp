"""The simulator UI helpers (key names, widget records, fingerprints), loaded without Kit."""

import asyncio
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


def test_a_widget_under_a_hidden_container_is_not_shown(kit_ui):
    """The Stage panel keeps the rows of the tree it switched away from (a search result): they sit under
    an invisible TreeView, at the header's position, and a click on one would hit the header."""
    ui = kit_ui.module
    shown, hidden = "Stage//Frame/ZStack[0]/TreeView[0]/Frame[3]/Label[0]", "Stage//Frame/ZStack[0]/TreeView[1]/Frame[3]/Label[0]"

    def find(path):
        kit_ui.queries.append(path)
        return SimpleNamespace(widget=SimpleNamespace(visible="TreeView[1]" not in path.split("/")[-1]),
                               position=SimpleNamespace(x=0, y=0), size=SimpleNamespace(x=500, y=500))

    kit_ui.module._ui_test().find = find
    cache = {}
    assert ui._shown(shown, (10, 10), ui._ui_test(), cache) is None
    assert ui._shown(hidden, (10, 10), ui._ui_test(), cache) == "it is inside a hidden part of its panel"
    asked = len(kit_ui.queries)
    assert ui._shown(hidden.replace("Frame[3]", "Frame[4]"), (10, 10), ui._ui_test(), cache) is not None
    assert len(kit_ui.queries) == asked, "containers already read for this find are not asked again"
    _assert_no_bare_prefixes(kit_ui.queries)


class MenuWidget:
    def __init__(self, text, *children, visible=True, shown=False):
        self.text, self.children, self.visible, self.shown = text, list(children), visible, shown


class Menu(MenuWidget):
    pass


class Separator(MenuWidget):
    pass


@pytest.fixture
def menus(kit_ui, monkeypatch):
    """A menu bar: Window > [Browsers > (Content), Graph Editors (hidden), a separator, Console], and a
    ui_test whose menu_click opens the path's top-level menu and fails on a missing item, like Kit's."""
    bar = MenuWidget("", Menu("Window", Menu("Browsers", MenuWidget("Content")), Menu("Graph Editors", visible=False),
                              Separator("Assets"), MenuWidget("Console")),
                     Menu("Replicator", visible=False))
    clicks = []

    def find_menu(text):
        return SimpleNamespace(widget=next(c for c in bar.children if c.text == text))

    async def menu_click(path, show=True):
        clicks.append((path, show))
        top = find_menu(path.split("/")[0]).widget
        top.shown = show
        level = bar
        for part in path.split("/"):
            level = next((c for c in level.children if c.text == part), None)
            if level is None:
                raise LookupError(part)

    ui_test = kit_ui.module._ui_test()
    ui_test.menu_click, ui_test.get_menubar = menu_click, lambda: SimpleNamespace(widget=bar, find_menu=find_menu)
    sys.modules["omni.ui"].Inspector = SimpleNamespace(get_children=lambda widget: widget.children)
    sys.modules["omni.ui"].Menu = Menu

    async def next_update_async():
        pass

    names = lambda *n: SimpleNamespace(**{k: k for k in n})  # noqa: E731
    carb_input = ModuleType("carb.input")
    carb_input.acquire_input_provider = lambda: None
    carb_input.MouseEventType, carb_input.KeyboardEventType = names("MOVE"), names("KEY_PRESS")
    app, appwindow = ModuleType("omni.kit.app"), ModuleType("omni.appwindow")
    app.get_app = lambda: SimpleNamespace(next_update_async=next_update_async)
    appwindow.get_default_app_window = lambda: SimpleNamespace(get_mouse=lambda: "m", get_keyboard=lambda: "k")
    workspace = sys.modules["omni.ui"].Workspace
    workspace.get_main_window_width, workspace.get_main_window_height = (lambda: 800), (lambda: 600)
    omni, kit, carb = sys.modules["omni"], ModuleType("omni.kit"), ModuleType("carb")
    omni.kit, omni.appwindow, kit.app, carb.input = kit, appwindow, app, carb_input
    for name, module in (("carb", carb), ("carb.input", carb_input), ("omni.kit", kit),
                         ("omni.kit.app", app), ("omni.appwindow", appwindow)):
        monkeypatch.setitem(sys.modules, name, module)
    return SimpleNamespace(ui=kit_ui.module, bar=bar, clicks=clicks,
                           act=lambda path: asyncio.run(kit_ui.module.act({"action": "menu", "menu": path})))


def test_menu_lists_only_what_a_person_sees(menus):
    assert menus.ui.menu_children("Window", inside=True) == ("Window", ["Browsers", "Console"])
    assert menus.ui.menu_children("Nope") == ("", ["Window"])


def test_a_menu_item_is_clicked_and_a_submenu_is_listed_and_closed(menus):
    assert menus.act("Window/Console")["note"].startswith("menu clicks move")
    assert menus.clicks == [("Window/Console", True)]
    out = menus.act("Window/Browsers")  # a menu, not an item: opening it does nothing by itself
    assert out["items"] == ["Content"] and out["closed_again"] is True and "is a menu, not an item" in out["note"]
    assert menus.clicks[-1] == ("Window", False) and not menus.bar.children[0].shown


def test_a_hidden_menu_entry_is_refused_before_anything_opens(menus):
    for path in ("Window/Graph Editors", "Window/Graph Editors/Action Graph", "Replicator"):
        with pytest.raises(menus.ui.UIRefusal) as err:
            menus.act(path)
        assert err.value.code == "MENU_NOT_FOUND" and "may still be open" not in str(err.value)
        assert menus.ui.refusal("sim.ui.act", err.value)["effects"] == "No input was sent."
    assert menus.clicks == [], "nothing was opened"


def test_a_path_that_runs_on_through_an_item_clicks_nothing(menus):
    """Kit's menu_click clicks each level on its way: "Window/Console/Foo" would toggle Console, then fail."""
    with pytest.raises(menus.ui.UIRefusal) as err:
        menus.act("Window/Console/Foo")
    assert menus.clicks == [] and menus.ui.refusal("sim.ui.act", err.value)["effects"] == "No input was sent."
    assert "'Console' is an item, not a menu" in str(err.value)


def test_a_visible_menu_entry_wins_over_a_hidden_one_of_the_same_name(menus):
    window = menus.bar.children[0]
    window.children.insert(0, Menu("Browsers", visible=False))
    assert menus.act("Window/Browsers")["items"] == ["Content"]


def test_a_failed_menu_path_closes_the_menu_it_opened(menus):
    with pytest.raises(menus.ui.UIRefusal) as err:
        menus.act("Window/Nope")
    assert menus.clicks == [("Window/Nope", True), ("Window", False)] and not menus.bar.children[0].shown
    assert "Items under 'Window': Browsers, Console." in str(err.value) and "may still be open" not in str(err.value)
    assert err.value.effects == "The menu was opened, and closed again."


def test_points_are_checked_in_window_pixels_at_any_display_scale(kit_ui):
    """At a display scale of 1.5 omni.ui calls a 1440x900 window 960x600 (UI points). x and y are pixels,
    as in a screenshot: the far part of the window must stay reachable, and find must report pixels."""
    workspace = sys.modules["omni.ui"].Workspace
    workspace.get_dpi_scale = lambda: 1.5
    workspace.get_main_window_width, workspace.get_main_window_height = (lambda: 960.0), (lambda: 600.0)
    ui = kit_ui.module
    assert ui.main_window_pixels() == (1440, 900)
    assert ui._check_point(1200, 700) == (1440, 900)
    with pytest.raises(ui.UIRefusal, match=r"outside the Isaac Sim window \(1440x900 pixels\)"):
        ui._check_point(1440, 10)

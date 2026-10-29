"""Unit tests for screen window enumeration (no X display required)."""

from unittest.mock import MagicMock

from Xlib import X

import isaac_mcp.screen as screen


def _make_window(wid, title, viewable=True, size=(1440, 900)):
    win = MagicMock()
    win.id = wid
    attrs = MagicMock()
    attrs.map_state = X.IsViewable if viewable else 0
    win.get_attributes.return_value = attrs
    geom = MagicMock()
    geom.width = size[0]
    geom.height = size[1]
    win.get_geometry.return_value = geom
    win.get_wm_class.return_value = ()
    return win


def test_visible_windows_walks_x_tree(monkeypatch):
    disp = MagicMock()
    root = MagicMock()
    disp.screen.return_value.root = root
    disp.intern_atom.side_effect = lambda name: name
    root.get_full_property.return_value = None  # _NET_CLIENT_LIST absent
    root.translate_coords.return_value = MagicMock(x=0, y=0)

    titles = {100: "Isaac Sim Python 6.2.0", 101: "Terminal"}
    wins = [_make_window(wid, t) for wid, t in titles.items()]

    def fake_title(win, d):
        return titles[win.id]

    root.query_tree.return_value.children = wins

    monkeypatch.setattr(screen, "_xdisplay", lambda: disp)
    monkeypatch.setattr(screen, "_window_title", fake_title)
    monkeypatch.setattr(screen, "_window_pid", lambda win, d: None)

    result = screen._visible_windows()
    assert {w["title"] for w in result} == set(titles.values())
    assert len(result) == 2


def test_visible_windows_uses_net_client_list(monkeypatch):
    disp = MagicMock()
    root = MagicMock()
    disp.screen.return_value.root = root
    disp.intern_atom.side_effect = lambda name: name

    prop = MagicMock()
    prop.value = [200, 201]
    root.get_full_property.return_value = prop
    root.query_tree.return_value.children = []
    root.translate_coords.return_value = MagicMock(x=0, y=0)

    titles = {200: "Isaac Sim Python 6.2.0", 201: "Editor"}
    win_map = {}
    for wid, t in titles.items():
        w = _make_window(wid, t)
        win_map[wid] = w

    def fake_title(win, d):
        return titles[win.id]

    disp.create_resource_object.side_effect = lambda kind, wid: win_map[wid]

    monkeypatch.setattr(screen, "_xdisplay", lambda: disp)
    monkeypatch.setattr(screen, "_window_title", fake_title)
    monkeypatch.setattr(screen, "_window_pid", lambda win, d: None)

    result = screen._visible_windows()
    assert {w["title"] for w in result} == set(titles.values())


def test_visible_windows_falls_back_when_client_list_lacks_window(monkeypatch):
    disp = MagicMock()
    root = MagicMock()
    disp.screen.return_value.root = root
    disp.intern_atom.side_effect = lambda name: name

    # _NET_CLIENT_LIST omits the Isaac window (id 300).
    prop = MagicMock()
    prop.value = [301]
    root.get_full_property.return_value = prop
    root.translate_coords.return_value = MagicMock(x=0, y=0)

    titles = {300: "Isaac Sim Python 6.2.0", 301: "Terminal"}
    win_map = {301: _make_window(301, "Terminal")}
    isaac_win = _make_window(300, "Isaac Sim Python 6.2.0")

    def fake_title(win, d):
        return titles[win.id]

    disp.create_resource_object.side_effect = lambda kind, wid: win_map[wid]
    root.query_tree.return_value.children = [isaac_win, win_map[301]]

    monkeypatch.setattr(screen, "_xdisplay", lambda: disp)
    monkeypatch.setattr(screen, "_window_title", fake_title)
    monkeypatch.setattr(screen, "_window_pid", lambda win, d: None)

    result = screen._visible_windows()
    assert "Isaac Sim Python 6.2.0" in {w["title"] for w in result}


def test_visible_windows_skips_untitled(monkeypatch):
    disp = MagicMock()
    root = MagicMock()
    disp.screen.return_value.root = root
    disp.intern_atom.side_effect = lambda name: name
    root.get_full_property.return_value = None
    root.translate_coords.return_value = MagicMock(x=0, y=0)

    titled = _make_window(1, "Isaac Sim Python 6.2.0")
    untitled = _make_window(2, "")
    root.query_tree.return_value.children = [titled, untitled]

    def fake_title(win, d):
        return "Isaac Sim Python 6.2.0" if win.id == 1 else ""

    monkeypatch.setattr(screen, "_xdisplay", lambda: disp)
    monkeypatch.setattr(screen, "_window_title", fake_title)
    monkeypatch.setattr(screen, "_window_pid", lambda win, d: None)

    result = screen._visible_windows()
    assert len(result) == 1
    assert result[0]["title"] == "Isaac Sim Python 6.2.0"


def test_auto_window_matches_isaac_prefix(monkeypatch):
    wins = [
        {"id": 1, "title": "Isaac Sim Python 6.2.0", "class": "", "width": 1440, "height": 900, "pid": None},
        {"id": 2, "title": "Terminal", "class": "", "width": 800, "height": 600, "pid": None},
    ]
    monkeypatch.setattr(screen, "_visible_windows", lambda: wins)
    monkeypatch.setattr(screen, "_bridge_pid", lambda port: None)
    w = screen._auto_window()
    assert w is not None
    assert w["title"] == "Isaac Sim Python 6.2.0"


def test_auto_window_case_insensitive(monkeypatch):
    wins = [
        {"id": 1, "title": "isaac sim foo", "class": "", "width": 100, "height": 100, "pid": None},
    ]
    monkeypatch.setattr(screen, "_visible_windows", lambda: wins)
    monkeypatch.setattr(screen, "_bridge_pid", lambda port: None)
    assert screen._auto_window()["title"] == "isaac sim foo"


def test_match_window_by_id(monkeypatch):
    wins = [
        {"id": 5, "title": "Isaac Sim Python 6.2.0", "class": "", "width": 100, "height": 100, "pid": None},
    ]
    monkeypatch.setattr(screen, "_visible_windows", lambda: wins)
    w = screen._match_window(None, 5)
    assert w["id"] == 5


def test_window_pixels(monkeypatch):
    disp = MagicMock()
    obj = MagicMock()
    disp.create_resource_object.return_value = obj
    geom = MagicMock()
    geom.width = 4
    geom.height = 3
    obj.get_geometry.return_value = geom
    obj.get_image.return_value.data = b"\x00" * (4 * 3 * 4)
    monkeypatch.setattr(screen, "_xdisplay", lambda: disp)
    width, height, data = screen._window_pixels({"id": 7, "title": "t"})
    assert (width, height) == (4, 3)
    assert len(data) == 4 * 3 * 4

def test_auto_target_matches_isaac_lab_windows(monkeypatch):
    wins = [
        {"id": 1, "title": "Mozilla Firefox - Isaac Sim docs", "class": "firefox", "pid": 10, "width": 1900, "height": 1000},
        {"id": 2, "title": "Isaac Lab 3.0.0", "class": "Isaac Lab 3.0.0", "pid": 20, "width": 1440, "height": 900},
    ]
    monkeypatch.setattr(screen, "_visible_windows", lambda: wins)
    assert screen._auto_window()["id"] == 2


def test_window_for_pid(monkeypatch):
    wins = [
        {"id": 1, "title": "a", "class": "", "pid": 7, "width": 10, "height": 10},
        {"id": 2, "title": "b", "class": "", "pid": 7, "width": 100, "height": 100},
        {"id": 3, "title": "c", "class": "", "pid": 8, "width": 999, "height": 999},
    ]
    monkeypatch.setattr(screen, "_visible_windows", lambda: wins)
    assert screen.window_for_pid(7)["id"] == 2
    assert screen.window_for_pid(None) is None
    assert screen.window_for_pid(99) is None

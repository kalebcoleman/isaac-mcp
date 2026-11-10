"""Kit-native UI computer use: find Isaac Sim widgets by name and drive them through Kit's input.

Why: on a machine that is used remotely, the X11 session is often locked. The lock screen holds the X
keyboard and pointer grabs, so X11 input (window_click/window_type) would reach the lock screen,
not Isaac Sim, and monitor screenshots are black. Kit's own input provider (``carb.input``, the
path ``omni.kit.ui_test`` uses) feeds events straight into the app, so these tools keep working
while the desktop is locked, and window screenshots (XComposite) still show the result.

The design follows OpenAI's computer-use action set (click, double_click, drag, move, scroll,
keypress, type, wait), plus two things a widget tree makes possible: targeting a widget by its
reference from ``sim.ui.find`` instead of guessing pixels, and Kit menu paths.

Coordinates are pixels in the Isaac Sim main window, the same pixels as
``screenshot(window_id=<the Isaac Sim window>)``. Kit widget geometry is in UI points;
pixels = points * dpi scale. ``matches``/``widget_record``/``parse_keys`` are pure and unit-tested.
"""

from __future__ import annotations

INTERACTIVE = {
    "Button", "ToolButton", "RadioButton", "CheckBox", "ComboBox", "StringField", "FloatField",
    "IntField", "FloatDrag", "IntDrag", "FloatSlider", "IntSlider", "MultiFloatField", "MultiIntField",
    "MultiFloatDragField", "MultiIntDragField", "TreeView", "CollapsableFrame", "Image",
}  # no MenuItem: closed menus still report visible items; menus go through action=menu
TEXTUAL = {"Label"}
MAX_TEXT = 500
MAX_WIDGETS_PER_WINDOW = 6000
ACTIONS = ("click", "double_click", "right_click", "move", "drag", "scroll", "type", "keypress", "menu", "tab", "close",
           "wait")

# Key names (OpenAI-style, case-insensitive) -> carb.input.KeyboardInput attribute names.
KEY_NAMES = {
    "enter": "ENTER", "return": "ENTER", "tab": "TAB", "escape": "ESCAPE", "esc": "ESCAPE",
    "backspace": "BACKSPACE", "delete": "DEL", "del": "DEL", "insert": "INSERT", "space": "SPACE",
    "left": "LEFT", "right": "RIGHT", "up": "UP", "down": "DOWN", "home": "HOME", "end": "END",
    "pageup": "PAGE_UP", "pagedown": "PAGE_DOWN", "minus": "MINUS", "equal": "EQUAL", "comma": "COMMA",
    "period": "PERIOD", "slash": "SLASH", "semicolon": "SEMICOLON", "apostrophe": "APOSTROPHE",
    "backslash": "BACKSLASH", "left_bracket": "LEFT_BRACKET", "right_bracket": "RIGHT_BRACKET",
    "grave": "GRAVE_ACCENT", **{f"f{i}": f"F{i}" for i in range(1, 13)},
}
MODIFIERS = {"ctrl": "CONTROL", "control": "CONTROL", "shift": "SHIFT", "alt": "ALT", "super": "SUPER",
             "meta": "SUPER", "cmd": "SUPER"}
MODIFIER_KEYS = {"CONTROL": "LEFT_CONTROL", "SHIFT": "LEFT_SHIFT", "ALT": "LEFT_ALT", "SUPER": "LEFT_SUPER"}


class UIRefusal(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def parse_keys(keys):
    """``["ctrl", "s"]`` -> (["CONTROL"], "S"). One non-modifier key, any modifiers, OpenAI-style names."""
    if not isinstance(keys, list) or not 1 <= len(keys) <= 5 or not all(isinstance(k, str) and k for k in keys):
        raise UIRefusal("INVALID_VALUE", "keys: 1..5 key names, e.g. [\"ctrl\", \"s\"] or [\"enter\"].")
    mods, main = [], []
    for raw in keys:
        k = raw.strip().lower().replace(" ", "_")
        if k in MODIFIERS:
            if MODIFIERS[k] in mods:
                raise UIRefusal("INVALID_VALUE", f"modifier {raw!r} given twice.")
            mods.append(MODIFIERS[k])
        elif k in KEY_NAMES:
            main.append(KEY_NAMES[k])
        elif len(k) == 1 and k.isascii() and k.isalnum():
            main.append(k.upper() if k.isalpha() else f"KEY_{k}")
        else:
            raise UIRefusal("INVALID_VALUE", f"unknown key {raw!r}; use letters, digits, enter, tab, escape, "
                                             "backspace, delete, arrows, home/end, pageup/pagedown, f1..f12, "
                                             "space or punctuation names, plus ctrl/shift/alt/super.")
    if len(main) != 1:
        raise UIRefusal("INVALID_VALUE", "keys must contain exactly one non-modifier key.")
    return mods, main[0]


def menu_path(path):
    """Validated menu path: "Layouts" (a top-level menu, opened to show its items) or "Window/Console".
    Surrounding spaces of each level are dropped; empty levels ("Window//X", "/Edit") are refused."""
    if not isinstance(path, str) or not 1 <= len(path) <= 200:
        raise UIRefusal("INVALID_VALUE", "menu: a menu path such as \"Window/Graph Editors/Action Graph\" or a "
                                         "top-level menu such as \"Layouts\".")
    parts = [p.strip() for p in path.split("/")]
    if not all(parts):
        raise UIRefusal("INVALID_VALUE", f"menu {path!r} has an empty level; separate levels with single '/', "
                                         "e.g. \"Window/Console\".")
    return "/".join(parts)


def window_title(title, action):
    if not isinstance(title, str) or not title.strip() or len(title) > 120:
        raise UIRefusal("INVALID_VALUE", f"window: for action={action}, the title of a Kit window or tab, e.g. 'Console'.")
    return title


def matches(record, query):
    """Case-insensitive substring match on the text a person or agent would use to name a widget."""
    if not query:
        return True
    q = query.lower()
    return any(q in str(record.get(k) if record.get(k) is not None else "").lower()
               for k in ("name", "text", "tooltip", "identifier", "type", "value"))


def widget_record(type_name, ref, window, name, text, tooltip, value, center, size, dpi, enabled=True,
                  identifier=""):
    """A JSON-friendly widget entry in window pixels."""
    cx, cy = center
    w, h = size
    entry = dict(ref=ref, type=type_name, window=window, x=round(cx * dpi), y=round(cy * dpi),
                 width=round(w * dpi), height=round(h * dpi))
    for key, val in (("name", name), ("identifier", identifier), ("text", text), ("tooltip", tooltip)):
        if val:
            entry[key] = str(val)[:120]
    if value is not None:
        entry["value"] = value if isinstance(value, (bool, int, float)) else str(value)[:120]
    if not enabled:
        entry["enabled"] = False
    return entry


# ---------------------------------------------------------------- Kit side (needs omni.ui / carb)

def _ui_test():
    import omni.kit.app

    em = omni.kit.app.get_app().get_extension_manager()
    if not em.is_extension_enabled("omni.kit.ui_test"):
        em.set_extension_enabled_immediate("omni.kit.ui_test", True)
    import omni.kit.ui_test as ui_test

    return ui_test


def _value(widget):
    model = getattr(widget, "model", None)
    if model is None:
        return None
    try:
        type_name = type(widget).__name__
        if type_name == "ComboBox":
            index = model.get_item_value_model().as_int
            items = model.get_item_children()
            if 0 <= index < len(items):
                return model.get_item_value_model(items[index]).as_string
            return index
        if type_name == "CheckBox":
            return model.as_bool
        if type_name in ("FloatField", "FloatDrag", "FloatSlider"):
            return round(model.as_float, 6)
        if type_name in ("IntField", "IntDrag", "IntSlider"):
            return model.as_int
        if type_name == "StringField":
            return model.as_string
    except Exception:  # noqa: BLE001 - models differ; a value is a convenience, not a contract
        return None
    return None


def hidden_tabs():
    """Titles of docked windows behind another tab (Console behind Content): open with action=tab."""
    import omni.ui as ui

    out = []
    for win in ui.Workspace.get_windows():
        try:
            if win.visible and win.title and win.docked and not win.is_selected_in_dock():
                out.append(win.title)
        except Exception:  # noqa: BLE001
            continue
    return out


def windows():
    """Visible Kit windows with their pixel rectangles inside the main window."""
    import omni.ui as ui

    dpi = ui.Workspace.get_dpi_scale()
    out = []
    for win in ui.Workspace.get_windows():
        if not win.visible or not win.title:
            continue
        try:  # a docked window behind another tab reports visible but cannot be clicked
            if win.docked and not win.is_selected_in_dock():
                continue
        except Exception:  # noqa: BLE001
            pass
        try:
            x, y, w, h = win.position_x, win.position_y, win.width, win.height
        except Exception:  # noqa: BLE001
            continue
        out.append(dict(title=win.title, x=round(x * dpi), y=round(y * dpi), width=round(w * dpi),
                        height=round(h * dpi), docked=bool(getattr(win, "docked", False))))
    return out


def fingerprint(type_name, identifier="", name="", text="", context=""):
    """What a ref must still point at: Kit paths are positional (".../Button[2]"), so after the UI is
    rebuilt (another prim in Property, a moved Stage row) the same path can name a different widget.
    Unnamed fields (Property values) use the nearest row label as context ("@Translate")."""
    label = identifier or name or ("" if type_name == "StringField" else str(text or "")[:40])
    if not label and context:
        label = "@" + str(context)[:40]
    return f"{type_name}:{label}".replace("|", "/")


def split_ref(ref):
    """(path, fingerprint or None) from a sim.ui.find ref ("<kit path>|<type>:<label>")."""
    path, sep, fp = ref.rpartition("|")
    if not sep or ":" not in fp or "//" not in path:
        return ref, None
    return path, fp


def inside(point, rect):
    x, y = point
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh


def _ancestors(path):
    """Paths of a widget's ancestors, outermost first, that Kit can query. A window's bare "Title/" or
    "Title//Frame" matches nothing and logs a warning that lands in the status bar and in recordings."""
    title, sep, rest = path.partition("//")
    segments = rest.split("/")
    if not sep:
        return []
    return [f"{title}//{'/'.join(segments[:i])}" for i in range(2, len(segments))]


def _context(path, ui_test):
    """Text of the nearest Label beside the widget (up to three levels up), e.g. a Property row name."""
    import omni.ui as ui

    for ancestor in _ancestors(path)[:-4:-1]:
        try:
            parent = ui_test.find(ancestor).widget
        except Exception:  # noqa: BLE001
            continue
        for child in ui.Inspector.get_children(parent):
            text = getattr(child, "text", "") if type(child).__name__ == "Label" else ""
            if text:
                return text
    return ""


def _bound_attribute(widget):
    """'/Cube.xformOp:translate#1' for a Property field: the USD attribute (and vector channel) it edits.
    Property fields share generic identifiers ("value"), so this is what tells rows apart."""
    model = getattr(widget, "model", None)
    getter = getattr(model, "get_attribute_paths", None)
    if not callable(getter):
        return ""
    try:
        paths = [str(p) for p in getter()]
    except Exception:  # noqa: BLE001
        return ""
    if not paths:
        return ""
    channel = getattr(model, "_channel_index", None)
    return ",".join(paths)[:120] + (f"#{channel}" if isinstance(channel, int) else "")


def _widget_fingerprint(widget, path, ui_test):
    type_name = type(widget).__name__
    fp = fingerprint(type_name, getattr(widget, "identifier", ""), getattr(widget, "name", ""),
                     getattr(widget, "text", ""))
    bound = _bound_attribute(widget)
    if bound:
        return f"{fp}@{bound}".replace("|", "/")
    if fp.endswith(":"):  # unnamed: add the nearest label so another row's field does not match
        fp = fingerprint(type_name, context=_context(path, ui_test))
    return fp


def _shown(path, center, ui_test):
    """None when the centre point (UI points) is really clickable, else why not: inside the owning
    Kit window, inside every enclosing ScrollingFrame, and not under a floating Kit window."""
    import omni.ui as ui

    title = path.split("//", 1)[0]
    win = ui.Workspace.get_window(title)
    if win is None:
        return "its window is closed"
    if not inside(center, (win.position_x, win.position_y, win.width, win.height)):
        return f"it is outside the visible part of the '{title}' window"
    parts = path.split("/")
    for i, part in enumerate(parts):
        if part.startswith("ScrollingFrame["):
            try:
                frame = ui_test.find("/".join(parts[: i + 1]))
                pos, size = frame.position, frame.size
            except Exception:  # noqa: BLE001
                return "its scrolling panel could not be measured"
            if not inside(center, (pos.x, pos.y, size.x, size.y)):
                return "it is scrolled out of view; scroll its panel first"
    if getattr(win, "docked", False):
        for other in ui.Workspace.get_windows():
            if (other is not win and other.visible and not getattr(other, "docked", False) and other.title
                    and other.title not in ("Main ToolBar", "Status Bar")
                    and inside(center, (other.position_x, other.position_y, other.width, other.height))):
                return f"the floating window '{other.title}' covers it"
    return None


def _candidates(window_filter, include_labels, query):
    """Cheap pass: (title, ref, widget, type) that match, before computing paths (each realpath
    walks the window tree again, so it is computed only for what is returned)."""
    import omni.ui as ui

    ui_test = _ui_test()
    # Kit queries only ui.Window; asking for another (the status bar) just logs "Failed to find window".
    queryable = {w.title for w in ui.Workspace.get_windows() if isinstance(w, ui.Window)}
    for win in windows():
        title = win["title"]
        if title not in queryable or (window_filter and window_filter.lower() not in title.lower()):
            continue
        try:
            refs = ui_test.find_all(f"{title}//Frame/**/*")
        except Exception:  # noqa: BLE001 - some windows cannot be queried by title
            continue
        for ref in refs[:MAX_WIDGETS_PER_WINDOW]:
            widget = ref.widget
            type_name = type(widget).__name__
            if type_name not in INTERACTIVE and not (include_labels and type_name in TEXTUAL):
                continue
            if not getattr(widget, "visible", True):
                continue
            name, ident, tip = getattr(widget, "name", ""), getattr(widget, "identifier", ""), getattr(widget, "tooltip", "")
            if type_name == "Image" and not (name or tip or ident):
                continue  # decorative icon
            text = getattr(widget, "text", "") if type_name != "StringField" else ""
            value = _value(widget)
            cheap = dict(type=type_name, name=name, identifier=ident, text=text, tooltip=tip, value=value)
            if matches(cheap, query):
                yield title, ref, widget, cheap


def _menu_bar():
    """Top-level menu names: menus are not widgets in find's results; open them with action=menu."""
    try:
        import omni.ui as ui

        return [t for t in (getattr(c, "text", "") for c in ui.Inspector.get_children(_ui_test().get_menubar().widget)) if t]
    except Exception:  # noqa: BLE001
        return []


def menu_children(path, inside=False):
    """Items available under the deepest existing level of *path* ("" = the menu bar); with ``inside``
    the last level is walked too, so this lists what the menu *path* itself contains."""
    import omni.ui as ui

    level = _ui_test().get_menubar().widget
    walked = []
    parts = [p for p in path.split("/") if p]
    for part in parts if inside else parts[:-1]:
        nxt = next((c for c in ui.Inspector.get_children(level) if getattr(c, "text", "") == part), None)
        if nxt is None:
            break
        level, walked = nxt, walked + [part]
    items = [getattr(c, "text", "") for c in ui.Inspector.get_children(level)]
    return "/".join(walked), [t for t in items if t][:40]


def find(a):
    import json

    import omni.ui as ui

    from .graph import _headless

    if _headless():  # no layout to report, and walking every widget took about 38 s per call on 6.1
        raise UIRefusal("NO_UI", "This Kit has no application window (headless); finding widgets needs a windowed Isaac Sim.")
    query = a.get("query", "") or ""
    window = a.get("window", "") or ""
    limit = a.get("limit", 40)
    if not isinstance(query, str) or len(query) > 120 or not isinstance(window, str) or len(window) > 120:
        raise UIRefusal("INVALID_VALUE", "query and window: strings of at most 120 characters.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise UIRefusal("LIMIT", "limit must be 1..100.")
    ui_test = _ui_test()
    dpi = ui.Workspace.get_dpi_scale()
    widgets, matched, budget, hidden, examined = [], 0, 48_000, 0, 0  # stay well under the 64 KiB cap
    rects = {w.title: (w.position_x, w.position_y, w.width, w.height) for w in ui.Workspace.get_windows()}
    for title, ref, widget, cheap in _candidates(window or None, bool(query), query):
        matched += 1
        if len(widgets) >= limit or budget <= 0 or examined >= 4 * limit:
            continue
        try:
            size, center = ref.size, ref.center
            if size.x <= 0 or size.y <= 0 or not inside((center.x, center.y), rects.get(title, (0, 0, 0, 0))):
                hidden += 1  # cheap check first: no path walk for rows outside the window
                continue
            examined += 1
            path = ref.realpath
        except Exception:  # noqa: BLE001 - stale or not yet laid out
            hidden += 1
            continue
        if _shown(path, (center.x, center.y), ui_test) is not None:
            hidden += 1
            continue
        record = widget_record(cheap["type"], f"{path}|{_widget_fingerprint(widget, path, ui_test)}", title, cheap["name"],
                               cheap["text"], cheap["tooltip"], cheap["value"], (center.x, center.y),
                               (size.x, size.y), dpi, getattr(widget, "enabled", True), cheap["identifier"])
        budget -= len(json.dumps(record))
        widgets.append(record)
    return dict(
        main_window=dict(width=round(ui.Workspace.get_main_window_width()),
                         height=round(ui.Workspace.get_main_window_height()), dpi_scale=dpi),
        windows=windows() if not window else [w for w in windows() if window.lower() in w["title"].lower()],
        hidden_tabs=[t for t in hidden_tabs() if not window or window.lower() in t.lower()],
        menu_bar=_menu_bar(),
        menu_note="Menus are not listed as widgets: open them with sim__ui__act action=menu, e.g. "
                  "menu='Window/Graph Editors/Action Graph' (menu_bar lists the top-level names).",
        matches=matched, widgets=widgets, truncated=len(widgets) + hidden < matched,
        skipped_not_shown=hidden,
        coordinates="x, y are the widget centre in pixels of the Isaac Sim main window, the same pixels as "
                    "screenshot(window_id=<Isaac Sim window>). Pass ref to sim__ui__act to target a widget; "
                    "widgets scrolled out of view, outside their window or under a floating window are left out.",
    )


def _resolve(a):
    """(x, y) in window pixels and the targeted widget, from ref or x/y. Sends nothing."""
    import omni.ui as ui

    dpi = ui.Workspace.get_dpi_scale()
    ref = a.get("ref")
    if ref:
        if not isinstance(ref, str) or "//" not in ref or len(ref) > 800:
            raise UIRefusal("INVALID_VALUE", "ref must be a widget ref from sim__ui__find.")
        path, expected = split_ref(ref)
        if expected is None:
            raise UIRefusal("INVALID_VALUE", "ref must be a ref returned by sim__ui__find (it ends in |Type:label); "
                                             "for a raw position use x and y.")
        ui_test = _ui_test()
        try:
            found = ui_test.find(path)
        except Exception:  # noqa: BLE001
            found = None
        if found is None or found.widget is None:
            raise UIRefusal("STALE_WIDGET", "That widget no longer exists (the UI was rebuilt); call sim__ui__find again.")
        actual = _widget_fingerprint(found.widget, path, ui_test)
        if expected is not None and actual != expected:
            raise UIRefusal("STALE_WIDGET", f"That ref now points at a different widget ({actual}, expected "
                                            f"{expected}): the UI was rebuilt. Call sim__ui__find again.")
        if not getattr(found.widget, "visible", True):
            # sim__ui__find lists only visible widgets, so a hidden one means the UI changed since then.
            raise UIRefusal("STALE_WIDGET", "That widget is hidden now: its panel changed or an inline editor (such "
                                            "as a Stage rename) replaced it. Finish or cancel that, then call "
                                            "sim__ui__find again.")
        c, s = found.center, found.size
        if s.x <= 0 or s.y <= 0:
            raise UIRefusal("NOT_VISIBLE", "That widget has no size yet (not laid out or collapsed).")
        why = _shown(path, (c.x, c.y), ui_test)
        if why:
            raise UIRefusal("NOT_VISIBLE", f"That widget cannot be clicked: {why}.")
        enabled = bool(getattr(found.widget, "enabled", True))
        for ancestor in _ancestors(path):  # a disabled container disables its children
            if not enabled:
                break
            try:
                enabled = bool(getattr(ui_test.find(ancestor).widget, "enabled", True))
            except Exception:  # noqa: BLE001
                pass
        return round(c.x * dpi), round(c.y * dpi), dict(ref=ref, type=type(found.widget).__name__,
                                                          name=getattr(found.widget, "name", ""), enabled=enabled)
    x, y = a.get("x"), a.get("y")
    if type(x) is not int or type(y) is not int:
        raise UIRefusal("INVALID_VALUE", "Give ref (from sim__ui__find) or integer x and y window pixels.")
    return x, y, None


def _check_point(x, y):
    import omni.ui as ui

    w, h = ui.Workspace.get_main_window_width(), ui.Workspace.get_main_window_height()
    if not (0 <= x < w and 0 <= y < h):
        raise UIRefusal("OUT_OF_WINDOW", f"({x}, {y}) is outside the Isaac Sim window ({round(w)}x{round(h)} pixels).")
    return w, h


async def act(a):
    """Perform one UI action through Kit's input provider; never moves the desktop cursor except for menus."""
    import carb.input
    import omni.appwindow
    import omni.kit.app
    from carb.input import KeyboardEventType, MouseEventType

    action = a.get("action")
    if action not in ACTIONS:
        raise UIRefusal("INVALID_VALUE", f"action must be one of {', '.join(ACTIONS)}.")
    frames = a.get("frames", 3)
    if type(frames) is not int or not 0 <= frames <= 600:
        raise UIRefusal("INVALID_VALUE", "frames: 0..600 frames to wait after the action.")
    app = omni.kit.app.get_app()
    appwin = omni.appwindow.get_default_app_window()
    if appwin is None:
        raise UIRefusal("NO_UI", "This Kit has no application window (headless); UI actions need a windowed Isaac Sim.")
    provider = carb.input.acquire_input_provider()
    mouse, keyboard = appwin.get_mouse(), appwin.get_keyboard()

    async def tick(n=2):
        for _ in range(n):
            await app.next_update_async()

    def mouse_event(kind, x, y, w, h):
        provider.buffer_mouse_event(mouse, kind, (x / w, y / h), 0, (float(x), float(y)))

    # Validate everything before the first event, so a refusal really means nothing was sent.
    text = keys = target = end = scroll = None
    if action == "type":
        text = a.get("text")
        if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT or any(c in "\r\n\t" or ord(c) < 32 for c in text):
            raise UIRefusal("INVALID_VALUE", f"text: 1..{MAX_TEXT} characters without control characters; "
                                             "send enter or tab with action=keypress.")
    if action == "keypress":
        keys = parse_keys(a.get("keys"))
    if action == "scroll":
        scroll = a.get("scroll_x", 0), a.get("scroll_y", 0)
        sx, sy = scroll
        if type(sx) is not int or type(sy) is not int or not (sx or sy) or abs(sx) > 20 or abs(sy) > 20:
            raise UIRefusal("INVALID_VALUE", "scroll_x/scroll_y: wheel steps, -20..20, not both 0 (negative "
                                             "scroll_y scrolls down).")
    if action == "drag":
        end = a.get("end_x"), a.get("end_y")
        if type(end[0]) is not int or type(end[1]) is not int:
            raise UIRefusal("INVALID_VALUE", "drag needs integer end_x and end_y window pixels.")
        _check_point(*end)
    focus_first = action in ("type", "keypress") and (a.get("ref") or a.get("x") is not None)
    if action in ("click", "double_click", "right_click", "move", "drag", "scroll") or focus_first:
        target = _resolve(a)
        _check_point(target[0], target[1])
        hit = target[2]
        if hit is not None and not hit["enabled"] and action != "move":
            raise UIRefusal("DISABLED", "That widget is disabled (greyed out); it would ignore the input.")

    menu = menu_path(a.get("menu")) if action == "menu" else None
    result = dict(action=action)
    if action in ("tab", "close"):
        # Dock tab headers are drawn by the dock, not widgets: bring the window to the front the way a
        # tab click does (selected in its dock, focused), or hide it the way its tab's close button does.
        title = window_title(a.get("window"), action)
        import omni.ui as ui

        win = ui.Workspace.get_window(title)
        if win is None:
            names = sorted({w.title for w in ui.Workspace.get_windows() if w.title})
            raise UIRefusal("NO_WINDOW", f"No Kit window titled {title!r}. Known windows: {', '.join(names[:40])}. "
                                         "Closed windows open from the Window menu (action=menu).")
        if action == "close":
            was_visible = bool(win.visible)
            ui.Workspace.show_window(title, False)
            await tick(max(frames, 2))
            win = ui.Workspace.get_window(title)
            hidden = win is None or not bool(win.visible)
            return dict(result, window=title, hidden=hidden, was_visible=was_visible, frames_waited=max(frames, 2),
                        note=("reopen it with action=tab, window=" + repr(title) + " (or from the Window menu)")
                        if hidden else "Kit did not hide it; check with sim__ui__find or a screenshot")
        ui.Workspace.show_window(title, True)
        await tick(1)
        win.focus()
        await tick(max(frames, 2))
        selected = bool(win.visible) and (not win.docked or win.is_selected_in_dock())
        return dict(result, window=title, shown=selected, docked=bool(win.docked), frames_waited=max(frames, 2),
                    **({} if win.docked else {"note": "a floating window can still be covered by another one; "
                                                       "check with a screenshot"}))
    if action == "wait":
        await tick(max(frames, 1))
        return dict(result, frames_waited=max(frames, 1))

    if action == "menu":
        path = menu
        ui_test = _ui_test()
        try:
            await ui_test.menu_click(path)
        except Exception as exc:  # noqa: BLE001
            try:
                level, items = menu_children(path)
            except Exception:  # noqa: BLE001
                level, items = "", []
            where = f"under '{level}'" if level else "on the menu bar"
            hint = (f" Items {where}: {', '.join(items)}." if items else
                    f" Nothing is listed {where}: its extension may not be loaded yet (for the Action Graph "
                    "editor use sim__graph__show).")
            raise UIRefusal("MENU_NOT_FOUND", f"Could not open menu {path!r}.{hint} If a menu was left open, "
                                              "action=menu with its top-level name (e.g. 'Window') opens and "
                                              "closes it.") from exc
        await tick(frames)
        if "/" not in path:
            # A top-level menu: clicking it only opens it. Report what it offers, then close it again (Kit
            # menus ignore an Escape keypress sent this way), so no menu is left covering the viewport.
            try:
                _, items = menu_children(path, inside=True)
            except Exception:  # noqa: BLE001
                items = []
            items = [t for t in items if t != "placeholder"]
            left_open = None
            try:
                await ui_test.menu_click(path, show=False)
                await tick(2)
                menu_ref = ui_test.get_menubar().find_menu(path)
                left_open = bool(getattr(getattr(menu_ref, "widget", None), "shown", False))
            except Exception:  # noqa: BLE001
                pass
            return dict(result, menu=path, items=items, closed_again=left_open is False, frames_waited=frames,
                        note=f"'{path}' is a top-level menu: it was opened to list its items" +
                             (" and closed again" if left_open is False else
                              "; it may still be open, check with a screenshot") +
                             f". Run an item with menu='{path}/<item>'.")
        return dict(result, menu=path, frames_waited=frames,
                    note="menu clicks move Kit's cursor through the menu; verify with a screenshot")

    if action in ("type", "keypress"):
        if focus_first:  # focus the target first, like a person would
            x, y, hit = target
            w, h = _check_point(x, y)
            for kind in (MouseEventType.MOVE, MouseEventType.LEFT_BUTTON_DOWN, MouseEventType.LEFT_BUTTON_UP):
                mouse_event(kind, x, y, w, h)
                await tick(2)
            result.update(focused_at=dict(x=x, y=y), widget=hit)
            if hit is None:
                result["note"] = "No known widget at that point, so the input may have gone nowhere. Check with a screenshot, or use a ref from sim__ui__find."
        if action == "type":
            for i, ch in enumerate(text):
                provider.buffer_keyboard_char_event(keyboard, ch, 0)
                if i % 20 == 19:
                    await tick(1)
            await tick(frames)
            if not focus_first:
                result["note"] = "No target given, so this went to whatever has keyboard focus. Pass ref to choose a widget."
            return dict(result, typed=len(text), frames_waited=frames)
        mods, key = keys
        flags = 0
        for m in mods:
            flags |= getattr(carb.input, f"KEYBOARD_MODIFIER_FLAG_{m}")
        KI = carb.input.KeyboardInput
        for m in mods:
            provider.buffer_keyboard_key_event(keyboard, KeyboardEventType.KEY_PRESS, getattr(KI, MODIFIER_KEYS[m]), flags)
        provider.buffer_keyboard_key_event(keyboard, KeyboardEventType.KEY_PRESS, getattr(KI, key), flags)
        await tick(2)
        provider.buffer_keyboard_key_event(keyboard, KeyboardEventType.KEY_RELEASE, getattr(KI, key), flags)
        for m in reversed(mods):
            provider.buffer_keyboard_key_event(keyboard, KeyboardEventType.KEY_RELEASE, getattr(KI, MODIFIER_KEYS[m]), 0)
        await tick(frames)
        names = {"CONTROL": "ctrl", "SHIFT": "shift", "ALT": "alt", "SUPER": "super"}
        return dict(result, keys=[*(names[m] for m in mods), key.lower().removeprefix("key_")], frames_waited=frames)

    x, y, hit = target
    w, h = _check_point(x, y)
    result.update(at=dict(x=x, y=y), widget=hit)
    mouse_event(MouseEventType.MOVE, x, y, w, h)
    await tick(2)
    if action == "move":
        await tick(frames)
        return dict(result, frames_waited=frames)
    if action in ("click", "double_click", "right_click"):
        down, up = ((MouseEventType.RIGHT_BUTTON_DOWN, MouseEventType.RIGHT_BUTTON_UP) if action == "right_click"
                    else (MouseEventType.LEFT_BUTTON_DOWN, MouseEventType.LEFT_BUTTON_UP))
        for _ in range(2 if action == "double_click" else 1):
            mouse_event(down, x, y, w, h)
            await tick(1)
            mouse_event(up, x, y, w, h)
            await tick(1)
        await tick(frames)
        return dict(result, frames_waited=frames)
    if action == "scroll":
        sx, sy = scroll
        # Same encoding as omni.kit.ui_test.emulate_mouse_scroll: the delta goes in both coordinate slots.
        provider.buffer_mouse_event(mouse, MouseEventType.SCROLL, (sx / w, sy / h), 0, (float(sx), float(sy)))
        await tick(frames)
        return dict(result, scroll=dict(x=sx, y=sy), frames_waited=frames)
    # drag
    ex, ey = end
    mouse_event(MouseEventType.LEFT_BUTTON_DOWN, x, y, w, h)
    await tick(2)
    for i in range(1, 13):
        mouse_event(MouseEventType.MOVE, round(x + (ex - x) * i / 12), round(y + (ey - y) * i / 12), w, h)
        await tick(1)
    mouse_event(MouseEventType.LEFT_BUTTON_UP, ex, ey, w, h)
    await tick(frames)
    return dict(result, to=dict(x=ex, y=ey), frames_waited=frames)

"""Kit-native UI computer use: find Isaac Sim widgets by name and drive them through Kit's input.

Why: on a lab PC that is used remotely, the X11 session is locked. The lock screen holds the X
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
ACTIONS = ("click", "double_click", "right_click", "move", "drag", "scroll", "type", "keypress", "menu", "wait")

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


def _records(window_filter=None, include_labels=True):
    import omni.ui as ui

    ui_test = _ui_test()
    dpi = ui.Workspace.get_dpi_scale()
    records = []
    for win in windows():
        title = win["title"]
        if window_filter and window_filter.lower() not in title.lower():
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
            try:
                size = ref.size
                center = ref.center
            except Exception:  # noqa: BLE001 - stale or not yet laid out
                continue
            if size.x <= 0 or size.y <= 0:
                continue
            if type_name == "Image" and not (getattr(widget, "name", "") or getattr(widget, "tooltip", "")
                                             or getattr(widget, "identifier", "")):
                continue  # decorative icon
            text = getattr(widget, "text", "") if type_name != "StringField" else ""
            records.append(widget_record(
                type_name, ref.realpath, title, getattr(widget, "name", ""), text,
                getattr(widget, "tooltip", ""), _value(widget), (center.x, center.y), (size.x, size.y), dpi,
                getattr(widget, "enabled", True), getattr(widget, "identifier", "")))
    return records


def menu_children(path):
    """Items available under the deepest existing level of *path* ("" = the menu bar)."""
    import omni.ui as ui

    level = _ui_test().get_menubar().widget
    walked = []
    for part in [p for p in path.split("/") if p][:-1]:
        nxt = next((c for c in ui.Inspector.get_children(level) if getattr(c, "text", "") == part), None)
        if nxt is None:
            break
        level, walked = nxt, walked + [part]
    items = [getattr(c, "text", "") for c in ui.Inspector.get_children(level)]
    return "/".join(walked), [t for t in items if t][:40]


def find(a):
    query = a.get("query", "") or ""
    window = a.get("window", "") or ""
    limit = a.get("limit", 40)
    if not isinstance(query, str) or len(query) > 120 or not isinstance(window, str) or len(window) > 120:
        raise UIRefusal("INVALID_VALUE", "query and window: strings of at most 120 characters.")
    if type(limit) is not int or not 1 <= limit <= 200:
        raise UIRefusal("LIMIT", "limit must be 1..200.")
    import omni.ui as ui

    found = [r for r in _records(window or None, include_labels=bool(query)) if matches(r, query)]
    return dict(
        main_window=dict(width=round(ui.Workspace.get_main_window_width()),
                         height=round(ui.Workspace.get_main_window_height()),
                         dpi_scale=ui.Workspace.get_dpi_scale()),
        windows=windows() if not window else [w for w in windows() if window.lower() in w["title"].lower()],
        matches=len(found), widgets=found[:limit], truncated=len(found) > limit,
        coordinates="x, y are the widget centre in pixels of the Isaac Sim main window, the same pixels as "
                    "screenshot(window_id=<Isaac Sim window>). Pass ref to sim__ui__act to target a widget.",
    )


def _resolve(a):
    """(x, y) in window pixels, and the widget record targeted, from ref or x/y."""
    import omni.ui as ui

    dpi = ui.Workspace.get_dpi_scale()
    ref = a.get("ref")
    if ref:
        if not isinstance(ref, str) or "//" not in ref or len(ref) > 600:
            raise UIRefusal("INVALID_VALUE", "ref must be a widget ref from sim__ui__find.")
        ui_test = _ui_test()
        try:
            found = ui_test.find(ref)
        except Exception:  # noqa: BLE001
            found = None
        if found is None or found.widget is None:
            raise UIRefusal("STALE_WIDGET", "That widget no longer exists (the UI was rebuilt); call sim__ui__find again.")
        if not getattr(found.widget, "visible", True):
            raise UIRefusal("NOT_VISIBLE", "That widget is hidden; open its window or panel first.")
        c, s = found.center, found.size
        if s.x <= 0 or s.y <= 0:
            raise UIRefusal("NOT_VISIBLE", "That widget has no size yet (not laid out or collapsed).")
        return round(c.x * dpi), round(c.y * dpi), dict(ref=found.realpath, type=type(found.widget).__name__,
                                                          name=getattr(found.widget, "name", ""))
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

    result = dict(action=action)
    if action == "wait":
        await tick(max(frames, 1))
        return dict(result, frames_waited=max(frames, 1))

    if action == "menu":
        path = a.get("menu")
        if not isinstance(path, str) or "/" not in path or len(path) > 200:
            raise UIRefusal("INVALID_VALUE", "menu: a menu path such as \"Window/Graph Editors/Action Graph\".")
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
            raise UIRefusal("MENU_NOT_FOUND", f"Could not open menu {path!r}.{hint} Close any open menu with "
                                              "keypress [\"escape\"] before retrying.") from exc
        await tick(frames)
        return dict(result, menu=path, frames_waited=frames,
                    note="menu clicks move Kit's cursor through the menu; verify with a screenshot")

    if action in ("type", "keypress"):
        if a.get("ref") or a.get("x") is not None:  # focus the target first, like a person would
            x, y, hit = _resolve(a)
            w, h = _check_point(x, y)
            for kind in (MouseEventType.MOVE, MouseEventType.LEFT_BUTTON_DOWN, MouseEventType.LEFT_BUTTON_UP):
                mouse_event(kind, x, y, w, h)
                await tick(2)
            result.update(focused_at=dict(x=x, y=y), widget=hit)
        if action == "type":
            text = a.get("text")
            if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT or any(c in "\r\n\t" or ord(c) < 32 for c in text):
                raise UIRefusal("INVALID_VALUE", f"text: 1..{MAX_TEXT} characters without control characters; "
                                                 "send enter or tab with action=keypress.")
            for i, ch in enumerate(text):
                provider.buffer_keyboard_char_event(keyboard, ch, 0)
                if i % 20 == 19:
                    await tick(1)
            await tick(frames)
            return dict(result, typed=len(text), frames_waited=frames)
        mods, key = parse_keys(a.get("keys"))
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

    x, y, hit = _resolve(a)
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
        sx, sy = a.get("scroll_x", 0), a.get("scroll_y", 0)
        if type(sx) is not int or type(sy) is not int or not (sx or sy) or abs(sx) > 20 or abs(sy) > 20:
            raise UIRefusal("INVALID_VALUE", "scroll_x/scroll_y: wheel steps, -20..20, not both 0 (negative "
                                             "scroll_y scrolls down).")
        # Same encoding as omni.kit.ui_test.emulate_mouse_scroll: the delta goes in both coordinate slots.
        provider.buffer_mouse_event(mouse, MouseEventType.SCROLL, (sx / w, sy / h), 0, (float(sx), float(sy)))
        await tick(frames)
        return dict(result, scroll=dict(x=sx, y=sy), frames_waited=frames)
    # drag
    ex, ey = a.get("end_x"), a.get("end_y")
    if type(ex) is not int or type(ey) is not int:
        raise UIRefusal("INVALID_VALUE", "drag needs integer end_x and end_y window pixels.")
    _check_point(ex, ey)
    mouse_event(MouseEventType.LEFT_BUTTON_DOWN, x, y, w, h)
    await tick(2)
    for i in range(1, 13):
        mouse_event(MouseEventType.MOVE, round(x + (ex - x) * i / 12), round(y + (ey - y) * i / 12), w, h)
        await tick(1)
    mouse_event(MouseEventType.LEFT_BUTTON_UP, ex, ey, w, h)
    await tick(frames)
    return dict(result, to=dict(x=ex, y=ey), frames_waited=frames)

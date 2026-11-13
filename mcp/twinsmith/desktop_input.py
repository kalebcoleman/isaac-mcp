"""Explicitly enabled, window-targeted X11 desktop input.

XTEST input is ultimately sent to a shared desktop, not to a private application
sandbox. The checks here reduce accidental mis-targeting; they cannot make X11
safe against another client or a person changing the desktop concurrently.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Iterable

from pydantic import Field

try:
    import fcntl
except ImportError:  # The standard host tools can still import on non-Unix platforms.
    fcntl = None

from Xlib import X, XK
from Xlib.ext import xtest
from mcp.server.fastmcp import FastMCP

from . import cursor_badge, screen
from .local_tools import DESKTOP_INPUT, ScopedTools

_input_lock = threading.Lock()
_WindowId = Annotated[int, Field(description="Window id from windows(action=\"list\").")]
_Pid = Annotated[int, Field(description="The window's PID from list_windows; a different owner is refused.")]
_X = Annotated[int, Field(description="X in window pixels (or image pixels, see coord_space), from a fresh screenshot.")]
_Y = Annotated[int, Field(description="Y in window pixels (or image pixels, see coord_space), from a fresh screenshot.")]
_Width = Annotated[int, Field(description="The window width you inspected, in pixels; a changed size is refused.")]
_Height = Annotated[int, Field(description="The window height you inspected, in pixels; a changed size is refused.")]
_CoordSpace = Annotated[str, Field(description="window: x/y are original window pixels. Or the coord_space token a "
                                               "screenshot printed: x/y are pixels of that image (also a zoomed crop).")]
_ShotAfter = Annotated[bool, Field(description="Also return a screenshot of the window after the action.")]

_MAX_TEXT = 200
_MAX_SCROLL = 10
_BUTTONS = {"left": 1, "middle": 2, "right": 3}
_KEYS = {
    "enter": "Return", "tab": "Tab", "escape": "Escape", "backspace": "BackSpace",
    "delete": "Delete", "left": "Left", "right": "Right", "up": "Up", "down": "Down",
    "home": "Home", "end": "End", "pageup": "Page_Up", "pagedown": "Page_Down",
    "space": "space",
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}
_MODIFIERS = {"ctrl": "Control_L", "shift": "Shift_L", "alt": "Alt_L", "super": "Super_L"}
_LOCKED = ("the desktop is locked or its screen saver is active (the PC is probably used remotely): X input "
           "would go to the lock screen, typed text into its password box, not to the window. For Isaac Sim "
           "use sim__ui__find and sim__ui__act, which drive Kit's own input; window screenshots still work.")
_BUSY_MASK = (X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod3Mask | X.Mod4Mask | X.Mod5Mask
              | X.Button1Mask | X.Button2Mask | X.Button3Mask | X.Button4Mask | X.Button5Mask)


@contextmanager
def _desktop_lock():
    """Serialize cooperating MCP servers on this user's X display."""
    if fcntl is None:
        raise RuntimeError("desktop input requires a supported X11 Unix environment")
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/twinsmith-desktop-{os.getuid()}"
    directory = Path(runtime) / "twinsmith-desktop"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = hashlib.sha256(screen.DISPLAY.encode()).hexdigest()[:16] + ".lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(directory / name, flags, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another MCP input action is using this desktop; retry") from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


_call_lock_state: list = []  # the lock probe's answer, once per tool call (set inside _action_lock)


@contextmanager
def _action_lock():
    """Hold both locks across an entire action, including multi-step inputs."""
    with _input_lock, _desktop_lock():
        _call_lock_state.clear()
        try:
            yield
        finally:
            _call_lock_state.clear()


def _lock_state():
    """screen._probe_locked(), probed fresh once per tool call (a 10-step scroll probes once)."""
    if not _call_lock_state:
        _call_lock_state.append(screen._probe_locked())
    return _call_lock_state[0]


def _id(window) -> int:
    return int(getattr(window, "id", window or 0))


def _belongs_to(window, target_id: int, root_id: int) -> bool:
    """Is an X window inside the selected client (including the client itself)?"""
    seen: set[int] = set()
    while window and _id(window) not in seen:
        wid = _id(window)
        if wid == target_id:
            return True
        if wid == root_id or not hasattr(window, "query_tree"):
            break
        seen.add(wid)
        window = window.query_tree().parent
    return False


def _window_geometry(disp, window, expected_pid: int) -> tuple[int, int, int, int]:
    if window.get_attributes().map_state != X.IsViewable:
        raise ValueError("target window is no longer visible")
    pid = screen._window_pid(window, disp)
    if pid is None or pid != expected_pid:
        raise ValueError("target window PID is missing or changed; list windows again")
    geom = window.get_geometry()
    root = disp.screen().root
    pos = root.translate_coords(window, 0, 0)
    return pos.x, pos.y, geom.width, geom.height


def _check_idle(disp) -> None:
    if disp.screen().root.query_pointer().mask & _BUSY_MASK or any(disp.query_keymap()):
        raise ValueError("keyboard or mouse buttons are in use; release them and retry")


def _keyboard_grabbed(disp) -> bool:
    """True when another client holds the keyboard grab (a lock screen; also an open popup menu).

    Probed by grabbing and releasing at once; nothing is typed."""
    root = disp.screen().root
    status = root.grab_keyboard(False, X.GrabModeAsync, X.GrabModeAsync, X.CurrentTime)
    if status == X.GrabSuccess:
        disp.ungrab_keyboard(X.CurrentTime)
        disp.sync()
        return False
    return True  # AlreadyGrabbed, or GrabFrozen (another client froze the keyboard)


@contextmanager
def _target(window_id: int, expected_pid: int, badge_at: tuple[int, int] | None = None):
    if window_id <= 0 or expected_pid <= 0:
        raise ValueError("window_id and expected_pid must be positive")
    locked = _lock_state()  # fresh for this call: it guards where keystrokes go
    if locked:
        raise RuntimeError(_LOCKED)
    # Require a fresh, titled top-level window, not an arbitrary X resource ID.
    visible = screen._match_window(None, window_id)
    if visible.get("pid") != expected_pid:
        raise ValueError("target window PID is missing or changed; list windows again")
    if badge_at is not None and cursor_badge.enabled():
        # Before the server grab: the badge's own X connection would block under it.
        cursor_badge.glide(visible["x"] + badge_at[0], visible["y"] + badge_at[1])
    disp = screen._xdisplay()
    grabbed = False
    try:
        if not disp.has_extension("XTEST"):
            raise RuntimeError("the X11 display does not support the XTEST extension")
        # Keep another X client from moving/replacing the target between the
        # final checks and injection. This is brief, and is not an X11 security boundary.
        disp.grab_server()
        grabbed = True
        disp.sync()
        if locked is None and _keyboard_grabbed(disp):
            # Fail closed: the lock state is unknown (no GNOME screen saver answered) and another
            # client holds the keyboard, which is what a lock screen does.
            raise RuntimeError("could not tell whether the desktop is locked, and another client holds the "
                               "keyboard grab (a lock screen or an open menu), so input could go there. "
                               "If it is a menu, wait for it to close and capture again; for Isaac Sim use "
                               "sim__ui__act, which does not go through the desktop.")
        window = disp.create_resource_object("window", window_id)
        geometry = _window_geometry(disp, window, expected_pid)
        _check_idle(disp)
        yield disp, window, geometry
        disp.sync()
    finally:
        try:
            if grabbed:
                disp.ungrab_server()
                disp.sync()
        finally:
            disp.close()


def _point(disp, window, geometry, x: int, y: int, width: int, height: int) -> None:
    left, top, actual_width, actual_height = geometry
    if width != actual_width or height != actual_height:
        raise ValueError("window size changed; capture it again before using coordinates")
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError("coordinates are outside the selected window")
    root = disp.screen().root
    root_x, root_y = left + x, top + y
    if not (0 <= root_x < disp.screen().width_in_pixels and
            0 <= root_y < disp.screen().height_in_pixels):
        raise ValueError("coordinates are outside the visible X11 desktop")
    xtest.fake_input(disp, X.MotionNotify, root=root.id, x=root_x, y=root_y)
    disp.sync()
    # Look through the actual stacking order after moving. Covered windows and
    # unrelated dialogs must not receive a click intended for this target.
    leaf = root
    while True:
        child = leaf.query_pointer().child
        if not child or not hasattr(child, "query_pointer"):
            break
        leaf = child
    if not _belongs_to(leaf, window.id, root.id):
        raise ValueError("another window covers that point; uncover the target and capture again")


def _focus(disp, window) -> None:
    root = disp.screen().root
    focus = disp.get_input_focus().focus
    if not _belongs_to(focus, window.id, root.id):
        raise ValueError("target window does not have keyboard focus; click it and try again")


def _lookup(disp, symbol: int) -> tuple[int, bool]:
    code = disp.keysym_to_keycode(symbol)
    if not code:
        raise ValueError("the current keyboard layout cannot produce that key")
    if disp.keycode_to_keysym(code, 0) == symbol:
        return code, False
    if disp.keycode_to_keysym(code, 1) == symbol:
        return code, True
    raise ValueError("the current keyboard layout needs an unsupported modifier for that key")


def _send_key(disp, code: int, modifiers: list[int]) -> None:
    """Hold the key across a frame while allowing the target app to process input."""
    pressed: list[int] = []
    try:
        for keycode in [*modifiers, code]:
            pressed.append(keycode)
            xtest.fake_input(disp, X.KeyPress, keycode)
        disp.sync()
        disp.ungrab_server()
        disp.sync()
        time.sleep(0.08)
    finally:
        for keycode in reversed(pressed):
            xtest.fake_input(disp, X.KeyRelease, keycode)
        disp.sync()


def _click(disp, button: int) -> None:
    """Let a rendered app see the press for at least one frame before release."""
    try:
        xtest.fake_input(disp, X.ButtonPress, button)
        disp.sync()
        # The target was checked and the press was sent under the X server grab.
        # Release the grab so Kit can process the event and render the next frame.
        disp.ungrab_server()
        disp.sync()
        time.sleep(0.08)
    finally:
        xtest.fake_input(disp, X.ButtonRelease, button)
        disp.sync()


def _step_click(window_id: int, pid: int, x: int, y: int, width: int, height: int,
                button: str = "left", clicks: int = 1) -> str:
    if button not in _BUTTONS or clicks not in (1, 2):
        raise ValueError("button must be left, middle or right; clicks must be 1 or 2")
    for i in range(clicks):  # glide once: a pause between the clicks would break a double-click
        with _target(window_id, pid, None if i else (x, y)) as (disp, window, geom):
            _point(disp, window, geom, x, y, width, height)
            _click(disp, _BUTTONS[button])
    return f"sent click to window {window_id} at ({x}, {y}){cursor_badge.note()}"


def _step_drag(window_id: int, pid: int, x: int, y: int, end_x: int, end_y: int, width: int, height: int) -> str:
    with _target(window_id, pid, (x, y)) as (disp, window, geom):
        # Check the entire sampled path before pressing, including any covered areas.
        for i in range(1, 13):
            px = round(x + (end_x - x) * i / 12)
            py = round(y + (end_y - y) * i / 12)
            _point(disp, window, geom, px, py, width, height)
        _point(disp, window, geom, x, y, width, height)
        root = disp.screen().root
        try:
            xtest.fake_input(disp, X.ButtonPress, 1)
            disp.sync()
            disp.ungrab_server()
            disp.sync()
            time.sleep(0.08)
            for i in range(1, 13):
                px = round(x + (end_x - x) * i / 12) + geom[0]
                py = round(y + (end_y - y) * i / 12) + geom[1]
                xtest.fake_input(disp, X.MotionNotify, root=root.id, x=px, y=py)
                disp.sync()
                cursor_badge.follow(px, py)
                time.sleep(0.01)
        finally:
            xtest.fake_input(disp, X.ButtonRelease, 1)
            disp.sync()
    return f"sent drag in window {window_id} from ({x}, {y}) to ({end_x}, {end_y}){cursor_badge.note()}"


def _step_scroll(window_id: int, pid: int, x: int, y: int, width: int, height: int,
                 direction: str, steps: int = 1) -> str:
    buttons = {"up": 4, "down": 5, "left": 6, "right": 7}
    if direction not in buttons or not 1 <= steps <= _MAX_SCROLL:
        raise ValueError("direction must be up, down, left or right and steps must be 1–10")
    for i in range(steps):
        with _target(window_id, pid, None if i else (x, y)) as (disp, window, geom):
            _point(disp, window, geom, x, y, width, height)
            _click(disp, buttons[direction])
    return f"sent {steps} scroll step(s) {direction} to window {window_id}{cursor_badge.note()}"


def _step_type(window_id: int, pid: int, text: str) -> str:
    if not text or len(text) > _MAX_TEXT or any(not 32 <= ord(c) <= 126 for c in text):
        raise ValueError("text must contain 1–200 printable ASCII characters")
    with _target(window_id, pid) as (disp, window, _):
        if _keyboard_grabbed(disp):  # a menu, or a lock screen the lock probe did not detect
            raise RuntimeError("another client holds the keyboard grab (an open menu or a lock screen), so "
                               "typed text would go there, not to the window. Close the menu "
                               "(computer action=key, key=escape) or unlock the desktop, then capture again.")
        _focus(disp, window)
        if disp.screen().root.query_pointer().mask & X.LockMask:
            raise ValueError("turn off Caps Lock before typing")
        keys = [_lookup(disp, ord(char)) for char in text]
        shift, _ = _lookup(disp, XK.string_to_keysym("Shift_L"))
        sent = 0
        try:
            for code, shifted in keys:
                if sent:
                    disp.grab_server()
                    disp.sync()
                    _window_geometry(disp, window, pid)
                    _check_idle(disp)
                    _focus(disp, window)
                _send_key(disp, code, [shift] if shifted else [])
                sent += 1
        except Exception as exc:
            raise RuntimeError(f"typing stopped after {sent} character(s): {exc}") from exc
    return f"sent {len(text)} character(s) to window {window_id}"


def _step_key(window_id: int, pid: int, key: str, modifiers: list[str] | None = None) -> str:
    name = key.lower()
    modifiers = modifiers or []
    if len(set(modifiers)) != len(modifiers) or any(m not in _MODIFIERS for m in modifiers):
        raise ValueError("modifiers may contain ctrl, shift, alt and super, at most once each")
    if name in _KEYS:
        symbol = XK.string_to_keysym(_KEYS[name])
    elif len(key) == 1 and key.isascii() and key.isalnum():
        symbol = ord(key)
    else:
        raise ValueError("unsupported key; use a navigation key, F1–F12, or a letter/digit")
    with _target(window_id, pid) as (disp, window, _):
        _focus(disp, window)
        if len(key) == 1 and key.isascii() and key.isalpha() and (
                disp.screen().root.query_pointer().mask & X.LockMask):
            raise ValueError("turn off Caps Lock before sending a letter")
        code, shifted = _lookup(disp, symbol)
        modifier_codes = [_lookup(disp, XK.string_to_keysym(_MODIFIERS[m]))[0]
                          for m in modifiers]
        if shifted and "shift" not in modifiers:
            modifier_codes.append(_lookup(disp, XK.string_to_keysym("Shift_L"))[0])
        _send_key(disp, code, modifier_codes)
    return f"sent key {key} to window {window_id}"


def _after(window_id: int, text: str, screenshot_after: bool):
    """The action's answer, and with screenshot_after the window as it looks now (taken after every lock
    and server grab is released, with the same capture as the screenshot tool)."""
    if not screenshot_after:
        return f"{text}; verify the resulting UI"
    return [text, *screen.capture(window_id=window_id)]


def register_desktop_input_tools(server: FastMCP, groups: Iterable[str] | None = None) -> None:
    """Register input only when the caller explicitly selects the input toolset."""
    mcp = ScopedTools(server, groups)

    @mcp.tool(annotations=DESKTOP_INPUT)
    def computer_click(window_id: _WindowId, expected_pid: _Pid, x: _X, y: _Y,
                       expected_width: _Width, expected_height: _Height,
                       button: Annotated[str, Field(description="left, middle or right.")] = "left",
                       clicks: Annotated[int, Field(description="1 or 2.")] = 1,
                       coord_space: _CoordSpace = "window", screenshot_after: _ShotAfter = False):
        """Click a visible point in one selected X11 window.

        Obtain window_id, PID and size from windows(action="list"); inspect a fresh screenshot first.
        Input affects the shared desktop and can trigger destructive or external actions.
        """
        x, y = screen.map_point(coord_space, x, y, expected_width, expected_height)
        with _action_lock():
            text = _step_click(window_id, expected_pid, x, y, expected_width, expected_height, button, clicks)
        return _after(window_id, text, screenshot_after)

    @mcp.tool(annotations=DESKTOP_INPUT)
    def computer_drag(window_id: _WindowId, expected_pid: _Pid, x: _X, y: _Y,
                      end_x: Annotated[int, Field(description="Drag end X, in the same space as x.")],
                      end_y: Annotated[int, Field(description="Drag end Y, in the same space as y.")],
                      expected_width: _Width, expected_height: _Height,
                      coord_space: _CoordSpace = "window", screenshot_after: _ShotAfter = False):
        """Drag with the left mouse button between two points in the same X11 window.

        Inspect a fresh screenshot and size first.
        Input affects the shared desktop and can trigger destructive or external actions.
        """
        x, y = screen.map_point(coord_space, x, y, expected_width, expected_height)
        end_x, end_y = screen.map_point(coord_space, end_x, end_y, expected_width, expected_height)
        with _action_lock():
            text = _step_drag(window_id, expected_pid, x, y, end_x, end_y, expected_width, expected_height)
        return _after(window_id, text, screenshot_after)

    @mcp.tool(annotations=DESKTOP_INPUT)
    def computer_scroll(window_id: _WindowId, expected_pid: _Pid, x: _X, y: _Y,
                        expected_width: _Width, expected_height: _Height,
                        direction: Annotated[str, Field(description="up, down, left or right.")],
                        steps: Annotated[int, Field(description="Scroll steps, 1-10.")] = 1,
                        coord_space: _CoordSpace = "window", screenshot_after: _ShotAfter = False):
        """Scroll up, down, left or right at a visible window point, at most ten steps per call.

        Inspect a fresh screenshot and size first.
        Input affects the shared desktop and can trigger destructive or external actions.
        """
        x, y = screen.map_point(coord_space, x, y, expected_width, expected_height)
        with _action_lock():
            text = _step_scroll(window_id, expected_pid, x, y, expected_width, expected_height, direction, steps)
        return _after(window_id, text, screenshot_after)

    @mcp.tool(annotations=DESKTOP_INPUT)
    def computer_type(window_id: _WindowId, expected_pid: _Pid,
                      text: Annotated[str, Field(description="Up to 200 printable ASCII characters.")],
                      screenshot_after: _ShotAfter = False):
        """Type up to 200 printable ASCII characters into the focused selected X11 window.

        It does not use the clipboard. Caps Lock and unsupported key mappings are rejected.
        Input affects the shared desktop and can trigger destructive or external actions.
        """
        with _action_lock():
            sent = _step_type(window_id, expected_pid, text)
        return _after(window_id, sent, screenshot_after)

    @mcp.tool(annotations=DESKTOP_INPUT)
    def computer_key(window_id: _WindowId, expected_pid: _Pid,
                     key: Annotated[str, Field(description="A navigation or editing key name, or an ASCII letter or digit, e.g. enter, tab, a.")],
                     modifiers: Annotated[list[str] | None, Field(description="Any of ctrl, shift, alt, super.")] = None,
                     screenshot_after: _ShotAfter = False):
        """Send one navigation/editing key or ASCII letter/digit to the focused X11 window.

        Modifiers may contain ctrl, shift, alt and super. Input can trigger destructive or external actions.
        """
        with _action_lock():
            text = _step_key(window_id, expected_pid, key, modifiers)
        return _after(window_id, text, screenshot_after)

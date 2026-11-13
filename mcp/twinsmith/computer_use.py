"""Host side of Kit UI computer use (``sim__ui`` find and act).

Kit's widget-path tools run inside Isaac Sim. What needs the desktop runs here, before and after
the bridge call: image-space coordinates become window pixels (``coord_space``), and a screenshot
or zoomed crop of the selected bridge's Isaac Sim window is added to the answer. Everything is
opt-in per call; with none of these arguments the call is forwarded unchanged.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

KIT_UI = frozenset({"sim.ui.act", "sim.ui.find"})
# Arguments this host consumes; Kit never sees them.
HOST_ONLY = ("coord_space", "screenshot_after")


def kit_window(state: Any) -> Optional[dict]:
    """The visible window of the Kit serving the selected bridge (by its PID, never by title)."""
    from . import screen
    from .server import _screen_port

    return screen.window_for_pid(screen._bridge_pid(_screen_port(state)))


_POINTS = (("x", "y"), ("end_x", "end_y"))


def map_coords(params: Dict[str, Any], coord_space: str, state: Any) -> Dict[str, Any]:
    """``params`` with x/y and end_x/end_y, top level and in every ``actions`` step, converted from
    the coord_space token's image to Isaac Sim window pixels by ``screen.map_point``, against the
    window's current size. New dicts; the caller's are left alone. A ref target needs no mapping."""
    from . import screen

    if coord_space in (None, "", "window"):
        return params
    steps = params.get("actions")
    if steps is not None and not (isinstance(steps, list) and all(isinstance(s, dict) for s in steps)):
        raise ValueError("actions must be a list of action objects")
    if any("coord_space" in step for step in steps or ()):
        raise ValueError("coord_space applies to the whole call; pass it once, not inside actions")
    window: Optional[dict] = None

    def mapped(step: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal window
        step = dict(step)
        for kx, ky in _POINTS:
            given = (step.get(kx) is not None, step.get(ky) is not None)   # null is unset, as in Kit
            if not any(given):
                continue
            if not all(given):
                raise ValueError(f"{kx} and {ky} go together")
            if window is None:
                window = kit_window(state)
                if window is None:
                    raise ValueError("a coord_space token needs the selected bridge's Isaac Sim window, and it "
                                     "has none on this desktop; use a ref from sim__ui__find instead")
            step[kx], step[ky] = screen.map_point(coord_space, step[kx], step[ky], window["width"], window["height"])
        return step

    params = mapped(params)
    if steps is not None:
        params["actions"] = [mapped(step) for step in steps]
    return params


def badge_args(params: Dict[str, Any], state: Any) -> Dict[str, Any]:
    """Item 2 (cursor badge): add the internal flag Kit reads when the badge is enabled."""
    return params


def attach_capture(text: str, failed: bool, state: Any, region: Optional[list] = None) -> Any:
    """Item 1 / item 6: the bridge's answer plus a capture of the Kit window (a crop with ``region``),
    as a CallToolResult so a failed act still carries the picture."""
    raise NotImplementedError


def kit_ui(canonical: str, params: Dict[str, Any], state: Any,
           dispatch: Callable[[str, Dict[str, Any], Any], str], failed_of: Callable[[str], bool]) -> Any:
    """What ``sim.ui.act`` and ``sim.ui.find`` run on the host."""
    from mcp.server.fastmcp.exceptions import ToolError

    params = dict(params)
    coord_space = params.pop("coord_space", "window")
    screenshot_after = bool(params.pop("screenshot_after", False))
    try:
        params = badge_args(map_coords(params, coord_space, state), state)
    except ValueError as error:
        raise ToolError(json.dumps({"status": "error", "code": "INVALID_VALUE", "message": str(error),
                                    "effects": "No input was sent."})) from None
    result = dispatch(canonical, params, state)
    failed = failed_of(result)
    if screenshot_after:
        return attach_capture(result, failed, state)
    if failed:
        raise ToolError(result)
    return result

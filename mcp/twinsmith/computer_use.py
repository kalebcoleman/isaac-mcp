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


def map_coords(params: Dict[str, Any], coord_space: str, state: Any) -> Dict[str, Any]:
    """``params`` with x/y/end_x/end_y (top level and in each ``actions`` step) in Isaac Sim window
    pixels. Item 3 (scale mapping) implements image-space tokens through screen.map_point."""
    if coord_space in (None, "", "window"):
        return params
    raise ValueError("coord_space must be 'window' or a coord_space token printed by screenshot")


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

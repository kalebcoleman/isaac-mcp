"""Host-side screen tools for the ur5e MCP server.

These live in the same process as the Kit bridge gateway, so one MCP server
exposes both the simulation tools discovered from the running Kit app and the
desktop capture tools below. Captures stay on this machine.

X11 only. No audio. Monitor captures show the desktop as displayed. Window
captures read that window's own pixels, so a window can be captured even when
another window covers it.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import mss
from PIL import Image as PILImage
from Xlib import X, display as xdisplay
from Xlib.error import XError
from Xlib.protocol import event as xevent

from mcp.server.fastmcp import FastMCP, Image

from .local_tools import ADDITIVE, IDEMPOTENT_WRITE, READ_ONLY, ScopedTools

DISPLAY = os.environ.get("DISPLAY") or ":0"
os.environ.setdefault("DISPLAY", DISPLAY)

SHOT_DIR = Path.home() / "Pictures" / "screen-mcp"
VIDEO_DIR = Path.home() / "Videos" / "screen-mcp"


# ---------------------------------------------------------------- recording


@dataclass
class Recording:
    path: Path
    bbox: dict
    fps: int
    max_width: int
    proc: subprocess.Popen
    stop: threading.Event
    window: dict | None = None
    out_width: int | None = None
    out_height: int | None = None
    full_screen: bool = False
    thread: threading.Thread | None = None
    frames: int = 0
    max_seconds: int = 600
    started: float = field(default_factory=time.monotonic)
    error: str | None = None


_lock = threading.Lock()
_current: Recording | None = None
_last: dict | None = None


def _scaled_recording_size(width: int, height: int, max_width: int) -> tuple[int, int]:
    """Choose explicit even encoder dimensions, preserving the capture aspect."""
    out_width = min(width, max_width)
    out_width -= out_width % 2
    # Round height to the nearest multiple of two, as ffmpeg's scale=-2 does.
    out_height = max(2, 2 * ((height * out_width + width) // (2 * width)))
    return out_width, out_height


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if not found:
            raise RuntimeError("ffmpeg is not available")
        return found


def _record_loop(rec: Recording) -> None:
    interval = 1.0 / rec.fps
    sct = None
    disp = None
    obj = None
    try:
        if rec.window is not None:
            disp = _xdisplay()
            obj = disp.create_resource_object("window", rec.window["id"])
        else:
            sct = mss.MSS()
        width, height = rec.bbox["width"], rec.bbox["height"]
        while not rec.stop.is_set():
            frame_start = time.monotonic()
            if obj is not None:
                raw = obj.get_image(0, 0, width, height, X.ZPixmap, 0xFFFFFFFF).data
                if len(raw) != width * height * 4:
                    raise RuntimeError(
                        f"window changed size or pixel format "
                        f"({len(raw)} bytes for {width}x{height})"
                    )
                data = raw
            else:
                data = sct.grab(rec.bbox).bgra
            try:
                rec.proc.stdin.write(data)
                rec.proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                rec.error = "ffmpeg closed the input pipe"
                break
            rec.frames += 1
            if time.monotonic() - rec.started >= rec.max_seconds:
                rec.error = f"stopped at max_seconds={rec.max_seconds}"
                break
            delay = interval - (time.monotonic() - frame_start)
            if delay > 0:
                rec.stop.wait(delay)
    except Exception as exc:  # noqa: BLE001
        rec.error = f"{type(exc).__name__}: {exc}"
    finally:
        if sct is not None:
            sct.close()
        if disp is not None:
            disp.close()
        try:
            rec.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        rec.stop.set()


def _finish(rec: Recording) -> dict:
    try:
        rec.proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        rec.proc.kill()
        rec.proc.wait(timeout=10)
    stderr = b""
    try:
        if rec.proc.stderr is not None:
            stderr = rec.proc.stderr.read() or b""
    except Exception:  # noqa: BLE001
        pass
    duration = rec.frames / rec.fps if rec.fps else 0.0
    size = rec.path.stat().st_size if rec.path.exists() else 0
    result = {
        "path": str(rec.path),
        "duration_s": round(duration, 2),
        "frames": rec.frames,
        "fps": rec.fps,
        "size_bytes": size,
        "size_mb": round(size / (1024 * 1024), 2),
        "width": rec.out_width or rec.bbox["width"],
        "height": rec.out_height or rec.bbox["height"],
        "source": (
            f"window: {rec.window['title']} (id={rec.window['id']})"
            if rec.window
            else "monitor"
        ),
        "output": (
            f"{rec.out_width}x{rec.out_height} ({'full screen' if rec.full_screen else 'scaled capture'})"
            if rec.out_width and rec.out_height
            else "native capture size"
        ),
        "note": "no audio track",
    }
    if rec.error:
        result["warning"] = rec.error
    if size == 0:
        result["error"] = "ffmpeg produced an empty file"
        if stderr:
            result["ffmpeg"] = stderr.decode("utf-8", "replace")[-800:]
    return result


# ------------------------------------------------------------------ display


def _xdisplay():
    try:
        return xdisplay.Display(DISPLAY)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"cannot open X display {DISPLAY}: {exc}") from exc


def _window_title(win, disp) -> str:
    try:
        prop = win.get_full_property(
            disp.intern_atom("_NET_WM_NAME"), disp.intern_atom("UTF8_STRING")
        )
        if prop and prop.value:
            value = prop.value
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    except XError:
        pass
    try:
        return win.get_wm_name() or ""
    except XError:
        return ""


def _window_pid(win, disp) -> int | None:
    """PID of the client owning a (possibly reparented) top-level window."""
    def prop_pid(obj) -> int | None:
        try:
            prop = obj.get_full_property(
                disp.intern_atom("_NET_WM_PID"), X.AnyPropertyType
            )
            if prop and prop.value:
                return int(prop.value[0])
        except XError:
            pass
        return None

    # Reparenting WMs stamp their own pid on the frame; the client window is a
    # child, so look there first.
    try:
        for child in win.query_tree().children:
            pid = prop_pid(child)
            if pid:
                return pid
    except XError:
        pass
    return prop_pid(win)


def _visible_windows() -> list[dict]:
    disp = _xdisplay()
    root = disp.screen().root
    out: list[dict] = []
    processed: set = set()

    # Collect candidate top-level windows from both _NET_CLIENT_LIST (the WM's
    # list of managed clients) and a walk of the X tree. _NET_CLIENT_LIST alone
    # can omit windows (e.g. reparented/unmanaged ones such as Isaac Sim), so
    # we merge both sources and de-duplicate by window id.
    candidates: list = []
    seen_ids: set = set()
    client_ids: set[int] = set()

    def _add_candidate(win) -> None:
        try:
            wid = getattr(win, "id", None)
        except Exception:  # noqa: BLE001
            wid = None
        if wid is not None and wid in seen_ids:
            return
        # _NET_CLIENT_LIST stores window ids; resolve them to window objects.
        if not hasattr(win, "get_attributes") and hasattr(disp, "create_resource_object"):
            try:
                wid_int = int(win)
            except (TypeError, ValueError):
                return
            if wid_int in seen_ids:
                return
            try:
                win = disp.create_resource_object("window", wid_int)
            except Exception:  # noqa: BLE001
                return
            wid = getattr(win, "id", wid_int)
            if wid in seen_ids:
                return
        if wid is not None:
            seen_ids.add(wid)
        candidates.append(win)

    # 1. _NET_CLIENT_LIST
    try:
        prop = root.get_full_property(
            disp.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType
        )
    except Exception:  # noqa: BLE001
        prop = None
    if prop is not None and getattr(prop, "value", None):
        try:
            for item in prop.value:
                try:
                    client_ids.add(int(getattr(item, "id", item)))
                except (TypeError, ValueError):
                    pass
                _add_candidate(item)
        except TypeError:
            pass

    # 2. Walk the X tree (fallback / supplement).
    try:
        tree = root.query_tree()
        for child in getattr(tree, "children", []) or []:
            _add_candidate(child)
    except Exception:  # noqa: BLE001
        pass

    try:
        for win in candidates:
            try:
                attrs = win.get_attributes()
                if attrs.map_state != X.IsViewable:
                    continue
                geom = win.get_geometry()
                if geom.width < 80 or geom.height < 80:
                    continue
                # Absolute position: translate the window's (0,0) into root
                # space. The reverse call (win.translate_coords(root, 0, 0))
                # yields the negated position and silently crops from 0,0.
                pos = root.translate_coords(win, 0, 0)
                title = _window_title(win, disp).strip()
                # Enumerate any viewable top-level window with a non-empty title.
                if not title:
                    continue
                try:
                    wm_class = win.get_wm_class() or ()
                    cls = wm_class[1] if len(wm_class) > 1 else (wm_class[0] if wm_class else "")
                except XError:
                    cls = ""
                wid = win.id
                if wid in processed:
                    continue
                processed.add(wid)
                out.append(
                    {
                        "id": wid,
                        "is_client": wid in client_ids,
                        "title": title,
                        "class": cls,
                        "pid": _window_pid(win, disp),
                        "x": pos.x,
                        "y": pos.y,
                        "width": geom.width,
                        "height": geom.height,
                    }
                )
            except XError:
                continue
    finally:
        disp.close()
    out.sort(key=lambda w: (w["y"], w["x"]))
    return out


def _match_window(query: str | None, window_id: int | None, require_unique: bool = False) -> dict:
    windows = _visible_windows()
    if window_id is not None:
        for win in windows:
            if win["id"] == window_id:
                return win
        raise ValueError(f"no visible window with id {window_id}")
    if not query:
        raise ValueError("pass window (title substring) or window_id")
    needle = query.lower()
    matches = [
        w for w in windows if needle in w["title"].lower() or needle in w["class"].lower()
    ]
    if not matches:
        titles = ", ".join(f"{w['title']!r}" for w in windows[:12])
        raise ValueError(f"no window matching {query!r}; visible: {titles}")
    if require_unique:
        owners = {("pid", w["pid"]) if w.get("pid") else ("id", w["id"]) for w in matches}
        if len(owners) > 1:
            raise ValueError("window title matches multiple processes; list windows and pass window_id")
    matches.sort(key=lambda w: (w.get("is_client", False), w["width"] * w["height"]), reverse=True)
    return matches[0]


# A capture or recording with no target picks this window, so the default is
# the sim and not whatever happens to be on the desktop.
# Kit apps title their window "Isaac Sim ..." and Isaac Lab runs title it "Isaac Lab 3.0.0".
AUTO_WINDOWS = ("Isaac Sim", "Isaac Lab")
AUTO_WINDOW = AUTO_WINDOWS[0]  # kept for importers of the old name


def _bridge_pid(port: int | None) -> int | None:
    """Kit PID advertising `port`, so two sims can be told apart.

    Reads the same `<pid>-<port>.json` portfiles the client uses. The runtime
    directory is derived locally because a desktop-launched MCP client may not
    pass XDG_RUNTIME_DIR to this process.
    """
    if not port:
        return None
    uid = os.getuid() if hasattr(os, "getuid") else None
    dirs = []
    xdg = os.getenv("XDG_RUNTIME_DIR")
    if xdg:
        dirs.append(os.path.join(xdg, "isaac-mcp"))
    elif uid is not None:
        dirs.append(f"/run/user/{uid}/isaac-mcp")
    if uid is not None:
        dirs.append(f"/tmp/isaac-mcp-{uid}")
    suffix = f"-{int(port)}.json"
    for directory in dirs:
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if not name.endswith(suffix):
                continue
            try:
                with open(os.path.join(directory, name), encoding="utf-8") as f:
                    pid = int(json.load(f).get("pid", 0))
                os.kill(pid, 0)
                return pid
            except (OSError, ValueError, TypeError):
                continue
    return None


def _auto_window(port: int | None = None) -> dict | None:
    """The visible Isaac Sim window to capture by default.

    Prefers the window owned by the Kit process serving `port` (the bridge we
    are configured to use), prefers managed client windows over frames, then the largest match.
    """
    try:
        windows = _visible_windows()
    except Exception:  # noqa: BLE001 - no display: fall back to the monitor
        return None
    needles = tuple(n.lower() for n in AUTO_WINDOWS)
    matches = [
        w
        for w in windows
        if w["title"].lower().startswith(needles)
        or w.get("class", "").lower().startswith(needles)
    ]
    if not matches:
        return None
    pid = _bridge_pid(port)
    if pid:
        owned = [w for w in matches if w.get("pid") == pid]
        if owned:
            matches = owned
    matches.sort(key=lambda w: (w.get("is_client", False), w["width"] * w["height"]), reverse=True)
    return matches[0]


def window_for_pid(pid: int | None) -> dict | None:
    """Visible client window owned by `pid` (e.g. a Lab job), else None. Never raises."""
    if not pid:
        return None
    try:
        owned = [w for w in _visible_windows() if w.get("pid") == int(pid)]
    except Exception:  # noqa: BLE001 - no display
        return None
    owned.sort(key=lambda w: (w.get("is_client", False), w["width"] * w["height"]), reverse=True)
    return owned[0] if owned else None


def _window_pixels(win: dict, even: bool = False) -> tuple[int, int, bytes]:
    """Read a window's own pixels via XGetImage.

    On a compositing WM the window is redirected to an offscreen pixmap, so
    this returns its real contents even when another window covers it. The
    bytes are BGRA/BGRX order (little-endian X ZPixmap), ready for ffmpeg.
    """
    disp = _xdisplay()
    try:
        obj = disp.create_resource_object("window", win["id"])
        geom = obj.get_geometry()
        width, height = geom.width, geom.height
        if even:
            width -= width % 2
            height -= height % 2
        raw = obj.get_image(0, 0, width, height, X.ZPixmap, 0xFFFFFFFF)
    except XError as exc:
        raise RuntimeError(
            f"cannot read pixels of window {win['id']} ({win['title']}): {exc}"
        ) from exc
    finally:
        disp.close()
    data = raw.data
    if len(data) == width * height * 4:
        return width, height, data
    if len(data) == width * height * 3:
        # 24-bit ZPixmap: add an opaque alpha byte so the buffer is BGRA.
        out = bytearray(width * height * 4)
        out[0::4] = data[0::3]
        out[1::4] = data[1::3]
        out[2::4] = data[2::3]
        out[3::4] = b"\xff" * (width * height)
        return width, height, bytes(out)
    raise RuntimeError(
        f"unexpected X image layout for window {win['id']}: "
        f"{len(data)} bytes for {width}x{height}"
    )


def _grab_window_image(win: dict) -> PILImage.Image:
    width, height, data = _window_pixels(win)
    return PILImage.frombytes("RGB", (width, height), data, "raw", "BGRX")


def _bbox(monitor: int = 0) -> dict:
    with mss.MSS() as sct:
        monitors = sct.monitors
        if monitor < 0 or monitor >= len(monitors):
            raise ValueError(f"monitor {monitor} out of range (0-{len(monitors) - 1})")
        m = monitors[monitor]
        return {
            "left": m["left"],
            "top": m["top"],
            "width": m["width"],
            "height": m["height"],
        }


def _primary_monitor_size() -> tuple[int, int]:
    """Even-pixel dimensions of the primary monitor, for full-screen output."""
    with mss.MSS() as sct:
        monitors = sct.monitors
        m = monitors[1] if len(monitors) > 1 else monitors[0]
        width = m["width"] - (m["width"] % 2)
        height = m["height"] - (m["height"] % 2)
        return width, height


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


# -------------------------------------------------------------------- tools


def register_screen_tools(
    server: FastMCP,
    port_provider: Callable[[], int | None] | None = None,
    groups: Iterable[str] | None = None,
) -> None:
    """Register the screen tools whose group is in ``groups`` (default: all of them).

    The capture directories are created only when ``screen_capture`` is enabled, so a profile
    without it leaves the filesystem alone."""
    mcp = ScopedTools(server, groups)
    if mcp.enabled("screen_capture"):
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    def auto_target() -> dict | None:
        port = None
        if port_provider is not None:
            try:
                port = port_provider()
            except Exception:  # noqa: BLE001 - discovery only refines the pick
                port = None
        return _auto_window(port)

    @mcp.tool(annotations=READ_ONLY)
    def screen_info() -> str:
        """Report the X11 display, monitors, capture directories and recording state."""
        with mss.MSS() as sct:
            monitors = [
                {
                    "index": i,
                    "left": m["left"],
                    "top": m["top"],
                    "width": m["width"],
                    "height": m["height"],
                }
                for i, m in enumerate(sct.monitors)
            ]
        try:
            windows = len(_visible_windows())
        except Exception:  # noqa: BLE001
            windows = -1
        lines = [
            f"display: {DISPLAY} (session: {os.environ.get('XDG_SESSION_TYPE', 'unknown')})",
            f"monitors ({len(monitors)}):",
        ]
        for m in monitors:
            label = "all monitors" if m["index"] == 0 else f"monitor {m['index']}"
            lines.append(
                f"  [{m['index']}] {label}: {m['width']}x{m['height']} "
                f"at {m['left']},{m['top']}"
            )
        lines.append(f"visible windows: {windows}")
        auto = auto_target()
        lines.append(
            f"auto target: {auto['title']} (id={auto['id']})"
            if auto
            else "auto target: none (Isaac Sim not open; monitor 1 is used)"
        )
        lines.append(f"screenshots: {SHOT_DIR}")
        lines.append(f"recordings: {VIDEO_DIR}")
        with _lock:
            active = _current is not None
        lines.append(f"screen recording: {'yes' if active else 'no'}")
        if active and _current:
            lines.append(
                f"  {_current.frames} frames, {time.monotonic() - _current.started:.1f}s so far"
            )
        if _last:
            lines.append(f"last recording: {_last.get('path')} ({_last.get('duration_s')}s)")
        return "\n".join(lines)

    @mcp.tool(annotations=READ_ONLY)
    def list_windows(filter: str | None = None) -> str:
        """List visible top-level windows. `filter` matches title or WM class.

        Privacy: window titles are returned to the model.
        """
        windows = _visible_windows()
        if filter:
            needle = filter.lower()
            windows = [
                w
                for w in windows
                if needle in w["title"].lower() or needle in w["class"].lower()
            ]
        if not windows:
            return "no matching windows"
        lines = [f"{len(windows)} window(s):"]
        for w in windows:
            lines.append(
                f"  id={w['id']}  pid={w.get('pid') or '?'}  "
                f"{w['width']}x{w['height']}+{w['x']}+{w['y']}  "
                f"{'client ' if w.get('is_client') else ''}[{w['class']}] {w['title']}"
            )
        return "\n".join(lines)

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def raise_window(window: str | None = None, window_id: int | None = None,
                     expected_pid: int | None = None) -> str:
        """Activate a window and give it focus; expected_pid rejects an unexpected owner."""
        # Share the input lock so activation cannot steal focus mid-action from another
        # cooperating server. Import lazily because desktop_input uses screen helpers.
        from .desktop_input import _action_lock

        with _action_lock():
            win = _match_window(window, window_id, True)
            if expected_pid is not None and win.get("pid") != expected_pid:
                raise ValueError("target window PID is missing or changed; list windows again")
            disp = _xdisplay()
            try:
                obj = disp.create_resource_object("window", win["id"])
                if expected_pid is not None and _window_pid(obj, disp) != expected_pid:
                    raise ValueError("target window PID is missing or changed; list windows again")
                root = disp.screen().root
                active = disp.intern_atom("_NET_ACTIVE_WINDOW")
                message = xevent.ClientMessage(
                    window=obj, client_type=active, data=(32, [2, X.CurrentTime, 0, 0, 0]))
                root.send_event(message, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
                disp.sync()
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline:
                    prop = root.get_full_property(active, X.AnyPropertyType)
                    if prop is not None and len(prop.value) and int(prop.value[0]) == win["id"]:
                        return f"activated: {win['title']} (id={win['id']})"
                    time.sleep(0.02)
                raise RuntimeError("window manager did not activate the window; select the client "
                                   "window ID (not its frame) or activate it manually")
            finally:
                disp.close()

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def move_window(x: int, y: int, width: int, height: int, window: str | None = None,
                    window_id: int | None = None, expected_pid: int | None = None) -> str:
        """Move and resize a window (pixels, top-left origin), e.g. to put Isaac Sim and RViz side
        by side before a screen recording. Un-maximizes it first; returns the geometry the window
        manager actually applied. expected_pid rejects an unexpected owner."""
        from .desktop_input import _action_lock

        if width < 100 or height < 100 or width > 16384 or height > 16384:
            raise ValueError("width and height must be 100..16384 pixels")
        with _action_lock():
            win = _match_window(window, window_id, True)
            if expected_pid is not None and win.get("pid") != expected_pid:
                raise ValueError("target window PID is missing or changed; list windows again")
            disp = _xdisplay()
            try:
                obj = disp.create_resource_object("window", win["id"])
                root = disp.screen().root

                def geometry():
                    g = obj.get_geometry()
                    pos = root.translate_coords(obj, 0, 0)  # absolute (see list_windows)
                    return (pos.x, pos.y, g.width, g.height)

                before = geometry()
                mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
                state = disp.intern_atom("_NET_WM_STATE")
                maxv = disp.intern_atom("_NET_WM_STATE_MAXIMIZED_VERT")
                maxh = disp.intern_atom("_NET_WM_STATE_MAXIMIZED_HORZ")
                # EWMH: remove (0) both maximized states, then _NET_MOVERESIZE_WINDOW with
                # x, y, width and height present (bits 8-11) from a pager-like source (2 << 12).
                root.send_event(xevent.ClientMessage(window=obj, client_type=state,
                                                     data=(32, [0, maxv, maxh, 2, 0])), event_mask=mask)
                flags = (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | (2 << 12)
                root.send_event(xevent.ClientMessage(
                    window=obj, client_type=disp.intern_atom("_NET_MOVERESIZE_WINDOW"),
                    data=(32, [flags, int(x), int(y), int(width), int(height)])), event_mask=mask)
                disp.sync()
                # Wait until the window manager has applied it: the size matches and the geometry
                # changed (unless nothing had to change).
                # The requested x/y place the frame while we read the client area, so instead of
                # guessing an offset, accept the geometry once it is the right size and two
                # readings in a row agree after it changed (or after 0.3 s of no change).
                deadline = time.monotonic() + 1.0
                settle = time.monotonic() + 0.3
                geo, last = before, None
                while time.monotonic() < deadline:
                    time.sleep(0.05)
                    geo = geometry()
                    sized = abs(geo[2] - width) <= 2 and abs(geo[3] - height) <= 2
                    if sized and geo == last and (geo != before or time.monotonic() >= settle):
                        break
                    last = geo
                sized = abs(geo[2] - width) <= 2 and abs(geo[3] - height) <= 2
                return (f"moved: {win['title']} (id={win['id']}) now {geo[2]}x{geo[3]}+{geo[0]}+{geo[1]} "
                        "(client area; the frame's title bar sits above it)"
                        + ("" if sized else f"; requested {width}x{height}+{x}+{y}, the window manager or "
                           "the app limited it"))
            finally:
                disp.close()

    @mcp.tool(annotations=ADDITIVE)
    def screenshot(
        monitor: int | None = None,
        window: str | None = None,
        window_id: int | None = None,
        max_width: int = 1600,
    ):
        """Capture the screen, one monitor, or a window. Returns the image and its PNG path.

        With no arguments this captures the Isaac Sim window when one is open,
        so the shot is just the sim; otherwise it captures the primary monitor.
        Pass `window` (title/class substring) or `window_id` for another window,
        `monitor=N` for one monitor, or `monitor=0` for all monitors combined.
        A window capture reads that window's own pixels, so it works even when
        another window covers it. The PNG on disk is full resolution; the inline
        image is downscaled to max_width (default 1600).

        Privacy: the image (and any text visible in it) is sent to the model and saved on this machine.
        """
        max_width = max(320, min(int(max_width), 3840))
        target = None
        if window or window_id is not None:
            target = _match_window(window, window_id)
        elif monitor is None:
            target = auto_target()
        if target is not None:
            win = target
            full = _grab_window_image(win)
            source = f"{win['width']}x{win['height']}+{win['x']}+{win['y']}"
            label = f"{win['title']} (id={win['id']})"
        else:
            bbox = _bbox(1 if monitor is None else monitor)
            with mss.MSS() as sct:
                raw = sct.grab(bbox)
            full = PILImage.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            source = f"{bbox['width']}x{bbox['height']}+{bbox['left']}+{bbox['top']}"
            label = f"monitor {1 if monitor is None else monitor}"
        path = SHOT_DIR / f"screen-{_stamp()}.png"
        full.save(path, "PNG", optimize=True)
        inline = full
        if full.width > max_width:
            height = max(1, round(full.height * max_width / full.width))
            inline = full.resize((max_width, height), PILImage.LANCZOS)
        caption = (
            f"saved: {path}\n"
            f"saved size: {full.width}x{full.height}\n"
            f"inline size: {inline.width}x{inline.height}\n"
            f"source: {source}\n"
            f"window: {label}"
        )
        buf = io.BytesIO()
        inline.save(buf, "PNG", optimize=True)
        return [Image(data=buf.getvalue(), format="png"), caption]

    @mcp.tool(annotations=ADDITIVE)
    def start_screen_recording(
        monitor: int | None = None,
        window: str | None = None,
        window_id: int | None = None,
        fps: int = 15,
        max_width: int = 1600,
        max_seconds: int = 600,
        full_screen: bool = True,
    ) -> str:
        """Start a desktop MP4 recording. One at a time; stop it with stop_screen_recording.

        With no arguments this records the Isaac Sim window when one is open,
        so the video is just the sim and not the desktop. Otherwise pass
        `window`/`window_id`, `monitor=N`, or `monitor=0` for all monitors. A
        window recording reads that window's own pixels, so another window
        covering it does not appear in the video. With full_screen (default
        true) the video is scaled and padded to the primary monitor (1920x1080
        here), so the result is a full-screen video even when the window is
        smaller or behind another window. Set full_screen=False for the native
        window size, capped by max_width. fps is clamped to 1-30, max_seconds
        to 5-3600 (default 600). No audio. For a clean Isaac Sim viewport
        render without desktop chrome, use the Kit tool start_recording instead.

        Privacy: the recording is saved on this machine; stop_screen_recording reports its path, and window
        titles and pixels can reach the model through the other screen tools.
        """
        global _current
        fps = max(1, min(int(fps), 30))
        max_seconds = max(5, min(int(max_seconds), 3600))
        max_width = max(320, min(int(max_width), 3840))
        out_width = out_height = None
        if full_screen:
            out_width, out_height = _primary_monitor_size()
        with _lock:
            if _current is not None:
                raise RuntimeError(
                    f"already recording to {_current.path}; "
                    "call stop_screen_recording first"
                )
            target = None
            if window or window_id is not None:
                target = _match_window(window, window_id)
            elif monitor is None:
                target = auto_target()
            if target is not None:
                win = target
                width = win["width"] - (win["width"] % 2)
                height = win["height"] - (win["height"] % 2)
                bbox = {"left": win["x"], "top": win["y"], "width": width, "height": height}
            else:
                monitor = 1 if monitor is None else monitor
                bbox = _bbox(monitor)
                width = bbox["width"] - (bbox["width"] % 2)
                height = bbox["height"] - (bbox["height"] % 2)
                bbox["width"], bbox["height"] = width, height
            path = VIDEO_DIR / f"screen-{_stamp()}.mp4"
            if full_screen:
                vf = (
                    f"scale={out_width}:{out_height}:force_original_aspect_ratio=decrease,"
                    f"pad={out_width}:{out_height}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p"
                )
            else:
                out_width, out_height = _scaled_recording_size(width, height, max_width)
                vf = f"scale={out_width}:{out_height},format=yuv420p"
            cmd = [
                _ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgra",
                "-s",
                f"{width}x{height}",
                "-r",
                str(fps),
                "-i",
                "-",
                "-an",
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-movflags",
                "+faststart",
                str(path),
            ]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            rec = Recording(
                path=path,
                bbox=bbox,
                fps=fps,
                max_width=max_width,
                proc=proc,
                stop=threading.Event(),
                window=target,
                out_width=out_width,
                out_height=out_height,
                full_screen=full_screen,
                max_seconds=max_seconds,
            )
            rec.thread = threading.Thread(target=_record_loop, args=(rec,), daemon=True)
            rec.thread.start()
            _current = rec
        source = (
            f"window {target['title']!r} (id={target['id']})"
            if target
            else f"monitor {monitor}"
        )
        output = (
            f"{out_width}x{out_height} full screen"
            if full_screen
            else f"{out_width}x{out_height}, max width {max_width}"
        )
        return (
            f"recording to {path}\n"
            f"source: {source} at {width}x{height}+{bbox['left']}+{bbox['top']}\n"
            f"output: {output}\n"
            f"fps: {fps}, limit: {max_seconds}s. Call stop_screen_recording when done."
        )

    @mcp.tool(annotations=ADDITIVE)
    def stop_screen_recording() -> str:
        """Stop the active desktop recording and report the MP4 path, duration and size.

        Privacy: the MP4 stays on this machine; only its path, duration and size are returned.
        """
        global _current, _last
        with _lock:
            rec = _current
            if rec is None:
                return "no screen recording is active"
            rec.stop.set()
            _current = None
        if rec.thread is not None:
            rec.thread.join(timeout=10)
        result = _finish(rec)
        _last = result
        return "\n".join(f"{k}: {v}" for k, v in result.items())

    @mcp.tool(annotations=READ_ONLY)
    def screen_recording_status() -> str:
        """Show whether a desktop recording is running and for how long."""
        with _lock:
            rec = _current
        if rec is None:
            return "no screen recording is active" + (
                f"\nlast: {_last['path']} ({_last['duration_s']}s)" if _last else ""
            )
        return (
            f"recording: {rec.path}\n"
            f"elapsed: {time.monotonic() - rec.started:.1f}s\n"
            f"frames: {rec.frames}\n"
            f"region: {rec.bbox['width']}x{rec.bbox['height']}"
            f"+{rec.bbox['left']}+{rec.bbox['top']}\n"
            f"source: {rec.window['title'] if rec.window else 'monitor'}"
        )

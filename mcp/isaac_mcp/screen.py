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
import math
import os
import re
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
    # Several windows composed into one video: the windows and their cells on the canvas.
    windows: list[dict] | None = None
    cells: list[tuple[int, int, int, int]] | None = None
    layout: str | None = None
    # Monitor recordings: frames composed from window pixels while the screen was locked.
    composed_frames: int = 0
    captured: int = 0  # distinct captures; frames - captured were repeated to keep real time
    warnings: list[str] = field(default_factory=list)
    warned: set = field(default_factory=set)


_lock = threading.Lock()
_current: Recording | None = None
_last: dict | None = None

DEFAULT_MAX_WIDTH = 1920
RECORD_LAYOUTS = ("side_by_side", "top_bottom", "grid")
MAX_RECORD_WINDOWS = 6
MAX_CANVAS_HEIGHT = 2160


def _scaled_recording_size(width: int, height: int, max_width: int) -> tuple[int, int]:
    """Choose explicit even encoder dimensions, preserving the capture aspect."""
    out_width = min(width, max_width)
    out_width -= out_width % 2
    # Round height to the nearest multiple of two, as ffmpeg's scale=-2 does.
    out_height = max(2, 2 * ((height * out_width + width) // (2 * width)))
    return out_width, out_height


def grid_shape(count: int, layout: str) -> tuple[int, int]:
    """(rows, columns) for ``count`` windows in a recording or tiling layout."""
    if count < 1:
        raise ValueError("at least one window is needed")
    if layout in ("side_by_side", "left_right"):
        return 1, count
    if layout in ("top_bottom", "stacked"):
        return count, 1
    if layout == "grid":
        cols = math.ceil(math.sqrt(count))
        return math.ceil(count / cols), cols
    raise ValueError(f"unknown layout {layout!r}")


def fit_into(src_w: int, src_h: int, cell_w: int, cell_h: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) of a src_w x src_h image scaled to fit a cell, aspect kept and centred (padding around)."""
    if src_w <= 0 or src_h <= 0 or cell_w <= 0 or cell_h <= 0:
        return 0, 0, 0, 0
    scale = min(cell_w / src_w, cell_h / src_h)
    w = max(1, min(cell_w, round(src_w * scale)))
    h = max(1, min(cell_h, round(src_h * scale)))
    return (cell_w - w) // 2, (cell_h - h) // 2, w, h


def _cuts(lengths: list[float], scale: float) -> list[int]:
    """Integer boundaries of consecutive spans (no gaps or overlaps after rounding)."""
    out, acc = [0], 0.0
    for length in lengths:
        acc += length * scale
        out.append(round(acc))
    return out


def multi_window_layout(sizes: list[tuple[int, int]], layout: str, max_width: int = DEFAULT_MAX_WIDTH,
                        max_height: int = MAX_CANVAS_HEIGHT) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    """Canvas (width, height) and one (x, y, w, h) cell per window for a composed recording.

    side_by_side scales every window to a common height (the tallest), top_bottom to a common width
    (the widest), so each fills its cell exactly; grid uses equal cells of the largest window size.
    The canvas is then scaled down (never up) to max_width x max_height and made even."""
    if not sizes:
        raise ValueError("at least one window is needed")
    if any(w <= 0 or h <= 0 for w, h in sizes):
        raise ValueError("window sizes must be positive")
    rows, cols = grid_shape(len(sizes), layout)
    if layout == "side_by_side":
        height = max(h for _, h in sizes)
        widths = [w * height / h for w, h in sizes]
        total_w, total_h = sum(widths), height
    elif layout == "top_bottom":
        width = max(w for w, _ in sizes)
        heights = [h * width / w for w, h in sizes]
        total_w, total_h = width, sum(heights)
    else:
        cell_w, cell_h = max(w for w, _ in sizes), max(h for _, h in sizes)
        total_w, total_h = cols * cell_w, rows * cell_h
    scale = min(1.0, max_width / total_w, max_height / total_h)
    canvas_w = max(2, int(total_w * scale) - int(total_w * scale) % 2)
    canvas_h = max(2, int(total_h * scale) - int(total_h * scale) % 2)
    # Rescale so the cells end exactly at the (even, truncated) canvas edges.
    sx, sy = canvas_w / total_w, canvas_h / total_h
    cells = []
    if layout == "side_by_side":
        xs = _cuts(widths, sx)
        cells = [(xs[i], 0, xs[i + 1] - xs[i], canvas_h) for i in range(len(sizes))]
    elif layout == "top_bottom":
        ys = _cuts(heights, sy)
        cells = [(0, ys[i], canvas_w, ys[i + 1] - ys[i]) for i in range(len(sizes))]
    else:
        xs = _cuts([cell_w] * cols, sx)
        ys = _cuts([cell_h] * rows, sy)
        for i in range(len(sizes)):
            r, c = divmod(i, cols)
            cells.append((xs[c], ys[r], xs[c + 1] - xs[c], ys[r + 1] - ys[r]))
    return canvas_w, canvas_h, cells


def recording_filter(out_width: int, out_height: int, full_screen: bool, prescaled: bool = False) -> str:
    """The ffmpeg -vf chain: letterbox into a full-screen frame, or scale to the output size."""
    if full_screen:
        return (f"scale={out_width}:{out_height}:force_original_aspect_ratio=decrease,"
                f"pad={out_width}:{out_height}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p")
    if prescaled:
        return "format=yuv420p"
    return f"scale={out_width}:{out_height},format=yuv420p"


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if not found:
            raise RuntimeError("ffmpeg is not available")
        return found


def _bgrx_image(width: int, height: int, data: bytes) -> PILImage.Image:
    return PILImage.frombytes("RGB", (width, height), data, "raw", "BGRX")


def _read_window(disp, obj) -> tuple[int, int, bytes]:
    """Current size and BGRA pixels of a window through an open display (one XGetImage)."""
    geom = obj.get_geometry()
    width, height = geom.width, geom.height
    data = obj.get_image(0, 0, width, height, X.ZPixmap, 0xFFFFFFFF).data
    if len(data) == width * height * 4:
        return width, height, data
    if len(data) == width * height * 3:
        out = bytearray(width * height * 4)
        out[0::4], out[1::4], out[2::4] = data[0::3], data[1::3], data[2::3]
        return width, height, bytes(out)
    raise RuntimeError(f"unexpected X image layout: {len(data)} bytes for {width}x{height}")


GONE_RETRY_S = 1.0


def _compose_cells(rec: Recording, disp, objs: list, gone: dict) -> bytes:
    """One frame of a multi-window recording: each window's own pixels fitted into its cell.

    ``gone`` maps a window index to the time it last failed, or to None once it is destroyed. A window
    that is only minimized or on another workspace for a moment (BadMatch) is retried every second."""
    from Xlib.error import BadDrawable, BadWindow

    canvas = PILImage.new("RGB", (rec.bbox["width"], rec.bbox["height"]), (0, 0, 0))
    now = time.monotonic()
    for i, (obj, cell) in enumerate(zip(objs, rec.cells)):
        if i in gone and (gone[i] is None or now - gone[i] < GONE_RETRY_S):
            continue
        try:
            width, height, data = _read_window(disp, obj)
        except (XError, RuntimeError) as exc:
            permanent = isinstance(exc, (BadWindow, BadDrawable))
            gone[i] = None if permanent else now
            win = rec.windows[i]
            key = (i, "permanent" if permanent else "transient")
            if key not in rec.warned:  # one warning per window and kind, so flapping cannot grow the list
                rec.warned.add(key)
                rec.warnings.append(f"window {win['id']} ({win['title']}) could not be read after "
                                    f"{now - rec.started:.1f}s ({type(exc).__name__}); its cell is black"
                                    + (" for the rest of the recording (window destroyed)" if permanent
                                       else " until it can be read again"))
            continue
        gone.pop(i, None)
        x, y, w, h = fit_into(width, height, cell[2], cell[3])
        if w <= 0 or h <= 0:
            continue  # an extreme aspect ratio left this cell no pixels
        image = _bgrx_image(width, height, data)
        if (w, h) != (width, height):
            image = image.resize((w, h), PILImage.BILINEAR)
        canvas.paste(image, (cell[0] + x, cell[1] + y))
    if len(gone) == len(objs) and all(v is None for v in gone.values()):
        raise RuntimeError("none of the recorded windows can be read any more (closed?)")
    return canvas.tobytes("raw", "BGRX")


def _record_loop(rec: Recording) -> None:
    """Capture frames until stopped. A frame is repeated when a capture falls behind, so the video
    keeps real time (duration = wall time) instead of playing fast."""
    sct = None
    disp = None
    obj = None
    objs: list = []
    gone: dict = {}
    windows_cache: tuple[float, list[dict]] = (0.0, [])
    try:
        if rec.windows:
            disp = _xdisplay()
            objs = [disp.create_resource_object("window", w["id"]) for w in rec.windows]
        elif rec.window is not None:
            disp = _xdisplay()
            obj = disp.create_resource_object("window", rec.window["id"])
        else:
            sct = mss.MSS()
        width, height = rec.bbox["width"], rec.bbox["height"]
        previous = None
        while not rec.stop.is_set():
            frame_start = time.monotonic()
            if objs:
                data = _compose_cells(rec, disp, objs, gone)
            elif obj is not None:
                raw = obj.get_image(0, 0, width, height, X.ZPixmap, 0xFFFFFFFF).data
                if len(raw) != width * height * 4:
                    raise RuntimeError(
                        f"window changed size or pixel format "
                        f"({len(raw)} bytes for {width}x{height})"
                    )
                data = raw
            elif _screen_locked():
                # Locked or blanked: the monitor is black, so compose it from the windows' own pixels.
                if frame_start - windows_cache[0] > 1.0:
                    windows_cache = (frame_start, composite_order(_visible_windows(), _stacking()))
                if disp is None:
                    disp = _xdisplay()  # one connection for every window and frame
                image, _painted = _composite(rec.bbox, windows_cache[1], disp)
                data = image.tobytes("raw", "BGRX")
                rec.composed_frames += 1
            else:
                data = sct.grab(rec.bbox).bgra
            rec.captured += 1
            # Frames due before this capture started show the previous image: repeating the new one
            # there would make content appear early when a capture was slow.
            before = int((frame_start - rec.started) * rec.fps)
            due = max(rec.frames + 1, int((time.monotonic() - rec.started) * rec.fps))
            try:
                while previous is not None and rec.frames < min(before, due - 1):
                    rec.proc.stdin.write(previous)
                    rec.frames += 1
                while rec.frames < due:
                    rec.proc.stdin.write(data)
                    rec.frames += 1
                rec.proc.stdin.flush()
                previous = data
            except (BrokenPipeError, ValueError):
                rec.error = "ffmpeg closed the input pipe"
                break
            if time.monotonic() - rec.started >= rec.max_seconds:
                rec.error = f"stopped at max_seconds={rec.max_seconds}"
                break
            next_due = rec.started + rec.frames / rec.fps
            delay = next_due - time.monotonic()
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
        "source": _recording_source(rec),
        "output": (
            f"{rec.out_width}x{rec.out_height} ({'full screen' if rec.full_screen else 'scaled capture'})"
            if rec.out_width and rec.out_height
            else "native capture size"
        ),
        "note": "no audio track",
    }
    if rec.captured and rec.frames > rec.captured * 1.25:
        result["capture_rate"] = (f"{rec.captured} distinct captures for {rec.frames} frames: capturing was "
                                  "slower than fps, so frames were repeated to keep real time; lower fps or "
                                  "max_width for smoother video")
    if rec.composed_frames:
        result["composed"] = (f"{rec.composed_frames} capture(s) were composed from the windows' own pixels "
                              "because the screen was locked or blanked (no panels, decorations or wallpaper)")
    for i, warning in enumerate(rec.warnings[:5]):
        result[f"window_warning{'' if i == 0 else i + 1}"] = warning
    if rec.error:
        result["warning"] = rec.error
    if size == 0:
        result["error"] = "ffmpeg produced an empty file"
        if stderr:
            result["ffmpeg"] = stderr.decode("utf-8", "replace")[-800:]
    return result


def _recording_source(rec: Recording) -> str:
    if rec.windows:
        names = ", ".join(f"{w['title']} (id={w['id']})" for w in rec.windows)
        return f"{len(rec.windows)} windows, {rec.layout}: {names}"
    if rec.window:
        return f"window: {rec.window['title']} (id={rec.window['id']})"
    return "monitor"


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


# ---------------------------------------------------------------- placement

TILE_LAYOUTS = ("left_right", "top_bottom", "grid")


def placement_request(x: int, y: int, width: int, height: int, extents: tuple[int, int, int, int],
                      frame: bool = False) -> tuple[int, int, int, int]:
    """The x, y, width, height to send in _NET_MOVERESIZE_WINDOW (NorthWest gravity) so the client
    area lands at (x, y, width, height), or with ``frame`` the outer frame (title bar included) does.

    EWMH: with NorthWest gravity x/y position the frame's outer corner while width/height size the
    client, and _NET_FRAME_EXTENTS (left, right, top, bottom) is the frame around the client."""
    left, right, top, bottom = extents
    if frame:
        return x, y, width - left - right, height - top - bottom
    return x - left, y - top, width, height


def client_target(x: int, y: int, width: int, height: int, extents: tuple[int, int, int, int],
                  frame: bool = False) -> tuple[int, int, int, int]:
    """Where the client area should end up for a requested geometry."""
    left, right, top, bottom = extents
    if frame:
        return x + left, y + top, width - left - right, height - top - bottom
    return x, y, width, height


def _close(a: tuple[int, int, int, int], b: tuple[int, int, int, int], tol: int = 2) -> bool:
    return all(abs(p - q) <= tol for p, q in zip(a, b))


def tile_rects(area: tuple[int, int, int, int], count: int, layout: str = "left_right",
               gap: int = 0) -> list[tuple[int, int, int, int]]:
    """Outer (frame) rectangles that split a work area (x, y, w, h) into ``count`` tiles."""
    if layout not in TILE_LAYOUTS:
        raise ValueError(f"layout must be one of {', '.join(TILE_LAYOUTS)}")
    rows, cols = grid_shape(count, layout)
    ax, ay, aw, ah = area
    gap = max(0, int(gap))
    xs = _cuts([1.0] * cols, (aw - gap * (cols - 1)) / cols)
    ys = _cuts([1.0] * rows, (ah - gap * (rows - 1)) / rows)
    out = []
    for i in range(count):
        r, c = divmod(i, cols)
        out.append((ax + xs[c] + c * gap, ay + ys[r] + r * gap, xs[c + 1] - xs[c], ys[r + 1] - ys[r]))
    return out


def intersect(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2 - x1, y2 - y1


def frame_extents(disp, obj) -> tuple[int, int, int, int]:
    """(left, right, top, bottom) frame decoration from _NET_FRAME_EXTENTS; zeros when absent."""
    try:
        prop = obj.get_full_property(disp.intern_atom("_NET_FRAME_EXTENTS"), X.AnyPropertyType)
        values = list(prop.value) if prop is not None else []
        if len(values) == 4 and all(isinstance(v, int) and 0 <= v < 1000 for v in values):
            return tuple(int(v) for v in values)  # type: ignore[return-value]
    except Exception:  # noqa: BLE001 - extents only refine placement
        pass
    return 0, 0, 0, 0


def _workarea(disp) -> tuple[int, int, int, int] | None:
    """The current desktop's _NET_WORKAREA (screen minus panels/docks), or None."""
    try:
        root = disp.screen().root
        prop = root.get_full_property(disp.intern_atom("_NET_WORKAREA"), X.AnyPropertyType)
        values = [int(v) for v in prop.value] if prop is not None else []
        desk = root.get_full_property(disp.intern_atom("_NET_CURRENT_DESKTOP"), X.AnyPropertyType)
        index = int(desk.value[0]) if desk is not None and len(desk.value) else 0
        if len(values) >= 4 * (index + 1):
            x, y, w, h = values[4 * index: 4 * index + 4]
            if w > 0 and h > 0:
                return x, y, w, h
    except Exception:  # noqa: BLE001
        pass
    return None


def _monitors() -> list[dict]:
    with mss.MSS() as sct:
        return [dict(m) for m in sct.monitors]


def _monitor_for(point: tuple[int, int], monitors: list[dict]) -> int:
    """Index (1-based, as in screen_info) of the monitor containing a point, else 1."""
    for i, m in enumerate(monitors[1:], start=1):
        if m["left"] <= point[0] < m["left"] + m["width"] and m["top"] <= point[1] < m["top"] + m["height"]:
            return i
    return 1


def _place_window(disp, win: dict, x: int, y: int, width: int, height: int, frame: bool = False) -> dict:
    """Un-maximize, move and resize one window so its client area (or outer frame) lands at the
    requested geometry, correcting once for any offset the WM applied. Never raises for a WM that
    refuses: the result says what happened."""
    obj = disp.create_resource_object("window", win["id"])
    root = disp.screen().root

    def geometry():
        g = obj.get_geometry()
        pos = root.translate_coords(obj, 0, 0)  # absolute (see list_windows)
        return (pos.x, pos.y, g.width, g.height)

    extents = frame_extents(disp, obj)
    target = client_target(x, y, width, height, extents, frame)
    if target[2] < 50 or target[3] < 50:
        raise ValueError(f"the client area would be {target[2]}x{target[3]} after the frame "
                         f"(extents left/right/top/bottom {extents}); request a larger size")
    mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
    state = disp.intern_atom("_NET_WM_STATE")
    maxv = disp.intern_atom("_NET_WM_STATE_MAXIMIZED_VERT")
    maxh = disp.intern_atom("_NET_WM_STATE_MAXIMIZED_HORZ")
    moveresize = disp.intern_atom("_NET_MOVERESIZE_WINDOW")
    # EWMH: x, y, width and height present (bits 8-11), NorthWest gravity (1), from a pager (2 << 12).
    flags = 1 | (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | (2 << 12)

    def send(req):
        root.send_event(xevent.ClientMessage(window=obj, client_type=moveresize,
                                             data=(32, [flags, *(int(v) & 0xFFFFFFFF for v in req)])), event_mask=mask)
        disp.sync()

    def settle(before, size):
        # Accept the geometry once it is the right size and two readings agree after it changed (or
        # after 0.3 s of no change). Already the right size: wait for the position up to the deadline.
        deadline = time.monotonic() + 1.0
        quiet = deadline if before[2:] == size else time.monotonic() + 0.3
        geo, last = before, None
        while time.monotonic() < deadline:
            time.sleep(0.05)
            geo = geometry()
            sized = abs(geo[2] - size[0]) <= 2 and abs(geo[3] - size[1]) <= 2
            if (geo == target or sized) and geo == last and (geo != before or time.monotonic() >= quiet):
                break
            last = geo
        return geo

    before = geometry()
    root.send_event(xevent.ClientMessage(window=obj, client_type=state,
                                         data=(32, [0, maxv, maxh, 2, 0])), event_mask=mask)
    req = placement_request(x, y, width, height, extents, frame)
    send(req)
    geo = settle(before, target[2:])
    corrected = False
    # The WM put the frame somewhere else than extents predicted (CSD shadows, a stale extent): shift
    # the request by the observed client offset once. A clamped position stays clamped, so stop then.
    if geo[2:4] == target[2:4] or _close(geo[2:4], target[2:4]):
        dx, dy = target[0] - geo[0], target[1] - geo[1]
        if (dx or dy) and abs(dx) <= 200 and abs(dy) <= 200 and not _close(geo, target):
            send((req[0] + dx, req[1] + dy, req[2], req[3]))
            geo = settle(geo, target[2:])
            corrected = True
    left, right, top, bottom = frame_extents(disp, obj)
    outer = (geo[0] - left, geo[1] - top, geo[2] + left + right, geo[3] + top + bottom)
    return dict(id=win["id"], title=win["title"], client=geo, frame=outer,
                extents=(left, right, top, bottom), requested=(x, y, width, height),
                requested_client=target, applied=_close(geo, target), corrected=corrected,
                moved=geo != before)


def _fmt_geo(g) -> str:
    return f"{g[2]}x{g[3]}+{g[0]}+{g[1]}"


def _placement_line(r: dict) -> str:
    line = (f"moved: {r['title']} (id={r['id']}) now {_fmt_geo(r['client'])} client area, "
            f"frame {_fmt_geo(r['frame'])} (frame extents left/right/top/bottom "
            f"{'/'.join(str(v) for v in r['extents'])})")
    if not r["applied"]:
        line += (f"; clamped: requested client {_fmt_geo(r['requested_client'])}, the window manager or the "
                 "app limited it (screen edge, panel, minimum/maximum size)")
        if not r["moved"]:
            line += "; the window did not move at all (some window managers ignore moves while the screen is locked)"
    return line


RAISE_LOCKED = ("the screen is locked (the PC is probably used remotely), so no window can be raised or "
                "focused: the lock screen covers the desktop. Raising is not needed for captures: "
                "screenshot(window_id=...) and start_screen_recording(window_id=... or window_ids=[...]) read "
                "each window's own pixels even when it is covered; for Isaac Sim's UI use sim__ui__act.")
LOCKED_NOTE = ("\nwarning: the screen is locked or blanked, so monitor captures are black. Capture a "
               "window instead (window=... or window_id=...): window captures read the window's own pixels.")
LOCKED_RECORD_NOTE = ("\nnote: the screen is locked or blanked, so each frame is composed from the visible "
                      "windows' own pixels in stacking order (no panels, decorations or wallpaper). For one or a "
                      "few windows, window_id=... or window_ids=[...] is cheaper and sharper.")
COMPOSITE_NOTE = ("\nnote: the screen is locked or blanked (the PC is probably used remotely), so this image "
                  "was composed from the visible windows' own pixels in stacking order; window decorations, "
                  "panels and the wallpaper are not shown. Desktop input (window_*) is refused while locked; "
                  "for Isaac Sim's UI use sim__ui__find and sim__ui__act.")


def _stacking() -> list[int]:
    """Client window ids bottom to top (_NET_CLIENT_LIST_STACKING); empty when the WM does not say."""
    disp = _xdisplay()
    try:
        prop = disp.screen().root.get_full_property(disp.intern_atom("_NET_CLIENT_LIST_STACKING"),
                                                    X.AnyPropertyType)
        return [int(v) for v in (prop.value if prop is not None else [])]
    except Exception:  # noqa: BLE001
        return []
    finally:
        disp.close()


def composite_order(windows: list[dict], stacking: list[int]) -> list[dict]:
    """Windows to paint bottom to top: WM frame windows dropped (their client is painted instead),
    stacked clients in the WM's order, then windows the WM does not stack (popups, tooltips and other
    override-redirect windows, which sit above managed ones) on top."""
    rank = {wid: i for i, wid in enumerate(stacking)}
    keep = [w for w in windows if w.get("class") != "mutter-x11-frames"]
    return sorted(keep, key=lambda w: rank.get(w["id"], len(stacking)))


def _composite(bbox: dict, ordered: list[dict] | None = None, disp=None) -> tuple[PILImage.Image, int]:
    """The monitor area composed from each visible window's own pixels (works while locked).

    ``ordered`` is a window list already in composite_order (a recording caches it between frames)."""
    canvas = PILImage.new("RGB", (bbox["width"], bbox["height"]), (32, 32, 32))
    painted = 0
    if ordered is None:
        ordered = composite_order(_visible_windows(), _stacking())
    for win in ordered:
        if (win["x"] >= bbox["left"] + bbox["width"] or win["y"] >= bbox["top"] + bbox["height"]
                or win["x"] + win["width"] <= bbox["left"] or win["y"] + win["height"] <= bbox["top"]):
            continue
        try:
            if disp is not None:  # a recording passes its own connection: no new X connection per window
                width, height, data = _read_window(disp, disp.create_resource_object("window", win["id"]))
                image = _bgrx_image(width, height, data)
            else:
                image = _grab_window_image(win)
        except (RuntimeError, XError):
            continue
        canvas.paste(image, (win["x"] - bbox["left"], win["y"] - bbox["top"]))
        painted += 1
    return canvas, painted


_LOCK_CACHE: list = [0.0, False]  # [monotonic time, value]: monitor captures can come quickly


def _screen_locked() -> bool:
    """True when the desktop session is locked or the screen saver is active (best effort, never raises).

    Seen on a lab PC with nobody at the keyboard: every monitor capture was black while window
    captures were fine. Cached for 5 s; each probe has a 1 s timeout."""
    now = time.monotonic()
    if now - _LOCK_CACHE[0] < 5.0:
        return _LOCK_CACHE[1]
    _LOCK_CACHE[:] = [now, bool(_probe_locked())]
    return _LOCK_CACHE[1]


def _probe_locked() -> bool | None:
    """True locked, False unlocked, None unknown (no probe gave a definite answer).

    loginctl "no" is not definite: when the server was started over SSH, XDG_SESSION_ID names that
    session, which is never locked. GNOME's ScreenSaver.GetActive answers for the desktop itself."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    # Without a session bus, gdbus would autolaunch a stray dbus-daemon (and find no screen saver).
    has_bus = bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS")) or bool(runtime and os.path.exists(
        os.path.join(runtime, "bus")))
    probes = [] if not has_bus else [["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver", "--object-path",
               "/org/gnome/ScreenSaver", "--method", "org.gnome.ScreenSaver.GetActive"]]
    session = os.environ.get("XDG_SESSION_ID")
    if session:
        probes.insert(0, ["loginctl", "show-session", session, "-p", "LockedHint", "--value"])
    answer = None
    for cmd in probes:
        if not shutil.which(cmd[0]):
            continue
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=1,
                                 stdin=subprocess.DEVNULL).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if out in ("yes", "(true,)"):
            return True
        if out == "(false,)":
            answer = False
    return answer
# ---------------------------------------------------------------- joining clips

JOIN_MODES = ("concat", "hstack", "vstack")
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm")
MAX_JOIN_INPUTS = 8
MAX_JOIN_SECONDS = 3600
MAX_INPUT_BYTES = 4 << 30


def _allowed_roots() -> list[Path]:
    roots = [Path.home().resolve()]
    try:
        roots.append(VIDEO_DIR.resolve())
    except OSError:
        pass
    return roots


def _under(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def safe_input_video(raw: str, roots: list[Path] | None = None) -> Path:
    """Resolve an input clip; it must be a regular video file under the home or screen-mcp video folder.
    A bare file name is looked up in the screen-mcp video folder."""
    roots = roots or _allowed_roots()
    if not isinstance(raw, str) or not raw.strip() or "\0" in raw:
        raise ValueError("each input must be a file path")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        if len(candidate.parts) != 1:
            raise ValueError(f"{raw!r}: give an absolute path or a file name in {VIDEO_DIR}")
        candidate = VIDEO_DIR / candidate
    path = candidate.resolve()
    if not _under(path, roots):
        raise ValueError(f"{raw!r} is outside your home folder and {VIDEO_DIR}; only those are read")
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        raise ValueError(f"{raw!r}: expected a video file ({', '.join(VIDEO_SUFFIXES)})")
    if not path.is_file():
        raise ValueError(f"{raw!r}: no such file")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"{raw!r} is larger than 4 GiB")
    return path


def safe_output_video(raw: str | None, roots: list[Path] | None = None) -> Path:
    """Output path: a new .mp4 in the screen-mcp video folder (bare name) or under home. Never an existing file."""
    roots = roots or _allowed_roots()
    if raw is None or raw == "":
        return VIDEO_DIR / f"joined-{_stamp()}.mp4"
    if not isinstance(raw, str) or "\0" in raw:
        raise ValueError("output must be a file name or path")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        if len(candidate.parts) != 1:
            raise ValueError(f"output {raw!r}: give a file name (saved in {VIDEO_DIR}) or an absolute path")
        candidate = VIDEO_DIR / candidate
    if candidate.suffix.lower() != ".mp4":
        raise ValueError("output must end in .mp4")
    parent = candidate.parent.resolve()
    path = parent / candidate.name
    if not _under(parent, roots):
        raise ValueError(f"output {raw!r} is outside your home folder and {VIDEO_DIR}; only those are written")
    if not parent.is_dir():
        raise ValueError(f"output folder {parent} does not exist")
    if os.path.lexists(path):
        raise ValueError(f"output {path} already exists; join_videos never overwrites, choose another name")
    return path


_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_VIDEO_SIZE = re.compile(r"Stream #\S+.*?: Video: .*?(?<![0-9])(\d{2,5})x(\d{2,5})(?![0-9])")
_FPS = re.compile(r"(\d+(?:\.\d+)?) fps")


def parse_probe(text: str) -> dict | None:
    """Width, height, fps and duration from `ffmpeg -i` output (ffprobe is often not installed)."""
    size = _VIDEO_SIZE.search(text)
    duration = _DURATION.search(text)
    if not size or not duration:
        return None
    line = text[size.start(): text.find("\n", size.start()) if "\n" in text[size.start():] else len(text)]
    fps = _FPS.search(line)
    h, m, sec = duration.groups()
    return dict(width=int(size.group(1)), height=int(size.group(2)),
                fps=float(fps.group(1)) if fps else None,
                duration=int(h) * 3600 + int(m) * 60 + float(sec))


def probe_video(ffmpeg: str, path: Path) -> dict:
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)], capture_output=True,
                             text=True, timeout=20, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{path}: ffmpeg took too long to read it") from exc
    info = parse_probe(out.stderr)
    if info is None:
        if _VIDEO_SIZE.search(out.stderr) and "Duration: N/A" in out.stderr:
            raise ValueError(f"{path}: the file has no duration (common for .webm from a browser recorder); "
                             "re-encode it to mp4 first")
        raise ValueError(f"{path}: no video stream found (is it a video file?)")
    return info


def _even_floor(v: float) -> int:
    n = int(v)
    return max(2, n - n % 2)


def join_plan(infos: list[dict], mode: str, max_width: int = DEFAULT_MAX_WIDTH, fps: int | None = None,
              shortest: bool = False) -> dict:
    """The ffmpeg filter graph and output size/duration for joining clips. Pure (unit-tested)."""
    if mode not in JOIN_MODES:
        raise ValueError(f"mode must be one of {', '.join(JOIN_MODES)}")
    if not 2 <= len(infos) <= MAX_JOIN_INPUTS:
        raise ValueError(f"give 2..{MAX_JOIN_INPUTS} input videos")
    rates = [i["fps"] for i in infos if i.get("fps")]
    rate = int(fps) if fps else int(round(min(max(rates), 60))) if rates else 15
    rate = max(1, min(rate, 60))
    chains = []
    if mode == "concat":
        first = infos[0]
        w, h = _scaled_recording_size(first["width"], first["height"], max_width)
        for i in range(len(infos)):
            chains.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={rate},format=yuv420p[v{i}]")
        joined = "".join(f"[v{i}]" for i in range(len(infos)))
        chains.append(f"{joined}concat=n={len(infos)}:v=1:a=0[v]")
        duration = sum(i["duration"] for i in infos)
        width, height = w, h
    else:
        horizontal = mode == "hstack"
        if horizontal:
            common = max(i["height"] for i in infos)
            spans = [i["width"] * common / i["height"] for i in infos]
            total_w, total_h = sum(spans), common
        else:
            common = max(i["width"] for i in infos)
            spans = [i["height"] * common / i["width"] for i in infos]
            total_w, total_h = common, sum(spans)
        scale = min(1.0, max_width / total_w, MAX_CANVAS_HEIGHT / total_h)
        common_out = _even_floor(common * scale)
        sizes = [_even_floor(span * common_out / common) for span in spans]
        for i, size in enumerate(sizes):
            dims = f"{size}:{common_out}" if horizontal else f"{common_out}:{size}"
            chains.append(f"[{i}:v]scale={dims},setsar=1,fps={rate}[v{i}]")
        joined = "".join(f"[v{i}]" for i in range(len(infos)))
        chains.append(f"{joined}{mode}=inputs={len(infos)}:shortest={1 if shortest else 0},format=yuv420p[v]")
        durations = [i["duration"] for i in infos]
        duration = min(durations) if shortest else max(durations)
        width, height = (sum(sizes), common_out) if horizontal else (common_out, sum(sizes))
    return dict(filter=";".join(chains), width=width, height=height, fps=rate, duration=duration)


def join_command(ffmpeg: str, inputs: list[Path], plan: dict, output: Path, overwrite: bool = False) -> list[str]:
    """``overwrite`` is only for our own claimed temporary file (never the user's output name)."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y" if overwrite else "-n"]
    for path in inputs:
        cmd += ["-i", str(path)]
    cmd += ["-filter_complex", plan["filter"], "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)]
    return cmd


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def join_timeout(duration: float) -> int:
    """Seconds allowed for encoding: generous for veryfast x264, bounded at 15 minutes."""
    return int(max(60, min(900, 30 + 3 * duration)))


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
                if _probe_locked():
                    raise RuntimeError(RAISE_LOCKED)
                raise RuntimeError("window manager did not activate the window; select the client "
                                   "window ID (not its frame) or activate it manually")
            finally:
                disp.close()

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def move_window(x: int, y: int, width: int, height: int, window: str | None = None,
                    window_id: int | None = None, expected_pid: int | None = None,
                    frame: bool = False) -> str:
        """Move and resize a window (pixels, top-left origin), e.g. to put Isaac Sim and RViz side
        by side before a screen recording. By default x, y, width, height are the client area (the
        window's content, what window captures record); frame=true places the outer frame including
        the title bar instead. The window manager's frame offset (_NET_FRAME_EXTENTS) is accounted for.
        Un-maximizes it first; returns the client and frame geometry actually applied and whether the
        window manager clamped it. expected_pid rejects an unexpected owner. To lay out several
        windows at once use tile_windows."""
        from .desktop_input import _action_lock

        if width < 100 or height < 100 or width > 16384 or height > 16384:
            raise ValueError("width and height must be 100..16384 pixels")
        with _action_lock():
            win = _match_window(window, window_id, True)
            if expected_pid is not None and win.get("pid") != expected_pid:
                raise ValueError("target window PID is missing or changed; list windows again")
            disp = _xdisplay()
            try:
                return _placement_line(_place_window(disp, win, x, y, width, height, frame))
            finally:
                disp.close()

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    def tile_windows(window_ids: list[int], layout: str = "left_right", monitor: int | None = None,
                     gap: int = 0) -> str:
        """Tile 2-6 windows over a monitor's work area (the screen minus panels, _NET_WORKAREA), e.g.
        Isaac Sim left and RViz 2 right for a demo video. layout: left_right (one row), top_bottom (one
        column) or grid. Each window's outer frame (title bar included) fills its tile, so title bars
        do not overlap. monitor: 1-based index from screen_info (default: the monitor holding the first
        window). gap: pixels between tiles. Reports each window's final client geometry and whether
        the window manager clamped it; record the result with start_screen_recording(window_ids=...)."""
        from .desktop_input import _action_lock

        if layout not in TILE_LAYOUTS:
            raise ValueError(f"layout must be one of {', '.join(TILE_LAYOUTS)}")
        ids = [int(w) for w in (window_ids or [])]
        if not 2 <= len(ids) <= MAX_RECORD_WINDOWS or len(set(ids)) != len(ids):
            raise ValueError(f"window_ids: 2..{MAX_RECORD_WINDOWS} distinct window IDs from list_windows")
        if not 0 <= int(gap) <= 200:
            raise ValueError("gap must be 0..200 pixels")
        with _action_lock():
            wins = [_match_window(None, wid, True) for wid in ids]
            monitors = _monitors()
            if monitor is None:
                first = wins[0]
                monitor = _monitor_for((first["x"] + first["width"] // 2, first["y"] + first["height"] // 2),
                                       monitors)
            if not 1 <= int(monitor) < len(monitors):
                raise ValueError(f"monitor must be 1..{len(monitors) - 1} (see screen_info)")
            m = monitors[int(monitor)]
            mon_rect = (m["left"], m["top"], m["width"], m["height"])
            disp = _xdisplay()
            try:
                work = _workarea(disp)
                area = intersect(mon_rect, work) if work else None
                area = area or mon_rect
                rects = tile_rects(area, len(wins), layout, int(gap))
                for (_, _, w, h) in rects:
                    if w < 100 or h < 100:
                        raise ValueError(f"the work area {_fmt_geo(area)} is too small for {len(wins)} tiles")
                results = []
                for win, rect in zip(wins, rects):
                    try:
                        results.append(_place_window(disp, win, *rect, frame=True))
                    except (XError, ValueError, RuntimeError) as exc:
                        moved = ", ".join(str(r.get("id", "?")) for r in results) or "none"
                        raise RuntimeError(f"could not place window {win['id']} ({exc}); already moved: "
                                           f"{moved}") from exc
            finally:
                disp.close()
        lines = [f"tiled {len(results)} windows {layout} on monitor {monitor}, work area {_fmt_geo(area)}"
                 + ("" if work else " (no _NET_WORKAREA; used the whole monitor)")]
        lines += [_placement_line(r) for r in results]
        clamped = [r for r in results if not r["applied"]]
        if clamped:
            lines.append(f"{len(clamped)} window(s) were clamped; their tiles may overlap or leave gaps")
        return "\n".join(lines)

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
        composed = False
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
            composed = _screen_locked()
            if composed:
                full, painted = _composite(bbox)
            else:
                with mss.MSS() as sct:
                    raw = sct.grab(bbox)
                full = PILImage.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            source = f"{bbox['width']}x{bbox['height']}+{bbox['left']}+{bbox['top']}"
            label = f"monitor {1 if monitor is None else monitor}" + (
                f" (composed from {painted} window(s))" if composed else "")
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
        if inline.width != full.width:
            # OpenAI's computer-use guide: map coordinates from a downscaled screenshot back to the
            # environment's pixels. Input tools take original pixels.
            caption += (f"\nscale: input tools take original pixels; multiply a point in this image by "
                        f"{full.width / inline.width:.3f}")
        if target is None and composed:
            caption += COMPOSITE_NOTE
        buf = io.BytesIO()
        inline.save(buf, "PNG", optimize=True)
        return [Image(data=buf.getvalue(), format="png"), caption]

    @mcp.tool(annotations=ADDITIVE)
    def start_screen_recording(
        monitor: int | None = None,
        window: str | None = None,
        window_id: int | None = None,
        fps: int = 15,
        max_width: int | None = None,
        max_seconds: int = 600,
        full_screen: bool | None = None,
        window_ids: list[int] | None = None,
        layout: str = "side_by_side",
    ) -> str:
        """Start a desktop MP4 recording. One at a time; stop it with stop_screen_recording.

        With no arguments this records the Isaac Sim window when one is open,
        so the video is just the sim and not the desktop. Otherwise pass
        `window`/`window_id`, `monitor=N`, or `monitor=0` for all monitors.
        A window recording reads that window's own pixels, so another window
        covering it does not appear in the video, and it works while the
        screen is locked. `window_ids=[a, b, ...]` (2-6 windows, e.g. Isaac Sim
        and RViz 2) records them into ONE video, read from each window's own
        pixels every frame, so they stay in sync: `layout` is side_by_side
        (default), top_bottom or grid; aspect ratios are kept and cells padded.
        Output size: the native capture size scaled down to `max_width`
        (default 1920), never up. full_screen=true instead letterboxes into
        the primary monitor's shape (also capped by max_width). A monitor
        recording while the screen is locked is composed from the windows'
        own pixels each frame instead of recording black. fps is clamped to
        1-30, max_seconds to 5-3600 (default 600). If capturing falls behind
        fps, frames are repeated so the video keeps real time. No audio. For a
        clean Isaac Sim viewport render, use the Kit tool start_recording.
        Join or stack finished clips with join_videos.

        Privacy: the recording is saved on this machine; stop_screen_recording reports its path, and window
        titles and pixels can reach the model through the other screen tools.
        """
        global _current
        fps = max(1, min(int(fps), 30))
        max_seconds = max(5, min(int(max_seconds), 3600))
        max_width = max(320, min(int(max_width if max_width is not None else DEFAULT_MAX_WIDTH), 3840))
        full_screen = bool(full_screen)
        ids = [int(w) for w in window_ids] if window_ids else []
        if ids:
            if window or window_id is not None or monitor is not None:
                raise ValueError("pass window_ids alone, not with window, window_id or monitor")
            if not 2 <= len(ids) <= MAX_RECORD_WINDOWS or len(set(ids)) != len(ids):
                raise ValueError(f"window_ids: 2..{MAX_RECORD_WINDOWS} distinct window IDs from list_windows "
                                 "(use window_id for one window)")
            if layout not in RECORD_LAYOUTS:
                raise ValueError(f"layout must be one of {', '.join(RECORD_LAYOUTS)}")
        with _lock:
            if _current is not None:
                raise RuntimeError(
                    f"already recording to {_current.path}; "
                    "call stop_screen_recording first"
                )
            target = None
            targets: list[dict] = []
            cells = None
            if ids:
                targets = [_match_window(None, wid) for wid in ids]
                width, height, cells = multi_window_layout(
                    [(w["width"], w["height"]) for w in targets], layout, max_width)
                bbox = {"left": 0, "top": 0, "width": width, "height": height}
            elif window or window_id is not None:
                target = _match_window(window, window_id)
            elif monitor is None:
                target = auto_target()
            if targets:
                pass  # canvas size and cells set above
            elif target is not None:
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
                out_width, out_height = _scaled_recording_size(*_primary_monitor_size(), max_width)
            elif targets:
                out_width, out_height = width, height  # composed at the output size already
            else:
                out_width, out_height = _scaled_recording_size(width, height, max_width)
            vf = recording_filter(out_width, out_height, full_screen, prescaled=bool(targets))
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
                windows=targets or None,
                cells=cells,
                layout=layout if targets else None,
            )
            rec.thread = threading.Thread(target=_record_loop, args=(rec,), daemon=True)
            rec.thread.start()
            _current = rec
        if targets:
            source = f"{len(targets)} windows, {layout}: " + ", ".join(
                f"{w['title']!r} (id={w['id']}, {w['width']}x{w['height']})" for w in targets)
            source += f"; canvas {width}x{height}"
        elif target:
            source = f"window {target['title']!r} (id={target['id']}) at {width}x{height}+{bbox['left']}+{bbox['top']}"
        else:
            source = f"monitor {monitor} at {width}x{height}+{bbox['left']}+{bbox['top']}"
        output = (f"{out_width}x{out_height} full screen (letterboxed)" if full_screen
                  else f"{out_width}x{out_height}, max width {max_width}")
        locked = not targets and target is None and _screen_locked()
        return (
            f"recording to {path}\n"
            f"source: {source}\n"
            f"output: {output}\n"
            f"fps: {fps}, limit: {max_seconds}s. Call stop_screen_recording when done."
        ) + (LOCKED_RECORD_NOTE if locked else "")

    @mcp.tool(annotations=ADDITIVE)
    def join_videos(inputs: list[str], mode: str = "concat", output: str | None = None,
                    max_width: int = DEFAULT_MAX_WIDTH, fps: int | None = None, shortest: bool = False) -> str:
        """Join 2-8 video clips into one new MP4 with the bundled ffmpeg: mode=concat plays them one after
        another (each scaled and padded to the first clip's size), hstack puts them side by side (scaled
        to a common height), vstack stacks them top to bottom (common width). For a synchronized
        side-by-side demo, prefer start_screen_recording(window_ids=[...]) over stacking separate clips.
        inputs: absolute paths or file names in ~/Videos/screen-mcp; only files under your home folder
        or that folder are read. output: a new file name (saved in ~/Videos/screen-mcp) or an absolute
        path under home ending in .mp4; never overwrites (default joined-<time>.mp4). max_width (default
        1920) caps the output width; fps defaults to the fastest input (max 60). With hstack/vstack a
        shorter clip freezes on its last frame unless shortest=true ends the video with the shortest one.
        Audio is dropped. Encoding is bounded to 15 minutes and the result to 1 hour of video.

        Privacy: reads and writes video files on this machine only; returns paths and sizes.
        """
        if not isinstance(inputs, list) or not 2 <= len(inputs) <= MAX_JOIN_INPUTS:
            raise ValueError(f"inputs: 2..{MAX_JOIN_INPUTS} video paths")
        if mode not in JOIN_MODES:
            raise ValueError(f"mode must be one of {', '.join(JOIN_MODES)}")
        max_width = max(320, min(int(max_width), 3840))
        if fps is not None and not 1 <= int(fps) <= 60:
            raise ValueError("fps must be 1..60")
        roots = _allowed_roots()
        paths = [safe_input_video(raw, roots) for raw in inputs]
        out = safe_output_video(output, roots)
        ffmpeg = _ffmpeg()
        infos = [probe_video(ffmpeg, path) for path in paths]
        plan = join_plan(infos, mode, max_width, fps, shortest)
        if plan["duration"] > MAX_JOIN_SECONDS:
            raise ValueError(f"the result would be {plan['duration']:.0f} s; the limit is {MAX_JOIN_SECONDS} s")
        tmp = out.with_name(f".{out.stem}.partial-{os.getpid()}-{threading.get_ident()}.mp4")
        try:  # claim the temporary name, so cleanup below can never delete someone else's file
            os.close(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError as exc:
            raise RuntimeError(f"{tmp} already exists; remove it or retry") from exc
        timeout = join_timeout(plan["duration"])
        started = time.monotonic()
        try:
            try:
                # Write over the empty file we claimed (-y for this temporary name only): unlinking it
                # first would leave a gap in which another process could take the name.
                proc = subprocess.run(join_command(ffmpeg, paths, plan, tmp, overwrite=True), capture_output=True, text=True,
                                      timeout=timeout, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"ffmpeg did not finish within {timeout} s; nothing was written") from exc
            if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
                raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}); nothing was written: "
                                   f"{proc.stderr.strip()[-600:]}")
            os.chmod(tmp, 0o666 & ~_umask())  # the claim made it 0600; the result gets normal permissions
            try:
                os.link(tmp, out)  # atomic and refuses an existing name, so a race cannot overwrite
            except FileExistsError as exc:
                raise RuntimeError(f"{out} appeared while encoding; nothing was overwritten") from exc
            except OSError as link_error:  # no hard links (some network/AD homes, FUSE): exclusive create, replace
                try:
                    os.close(os.open(out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
                except FileExistsError as exc:
                    raise RuntimeError(f"{out} appeared while encoding; nothing was overwritten") from exc
                try:
                    os.replace(tmp, out)
                except OSError as exc:
                    out.unlink(missing_ok=True)  # do not leave our empty placeholder behind
                    raise RuntimeError(f"could not publish {out} (hard link: {link_error.strerror}; "
                                       f"replace: {exc.strerror}); nothing was written") from exc
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        size = out.stat().st_size
        result = {
            "path": str(out),
            "mode": mode,
            "inputs": ", ".join(f"{p.name} ({i['width']}x{i['height']}, {i['duration']:.1f}s)"
                                for p, i in zip(paths, infos)),
            "width": plan["width"],
            "height": plan["height"],
            "fps": plan["fps"],
            "duration_s": round(plan["duration"], 2),
            "size_mb": round(size / (1024 * 1024), 2),
            "encode_s": round(time.monotonic() - started, 1),
            "note": "no audio track",
        }
        return "\n".join(f"{k}: {v}" for k, v in result.items())

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
            f"source: {_recording_source(rec)}"
        )

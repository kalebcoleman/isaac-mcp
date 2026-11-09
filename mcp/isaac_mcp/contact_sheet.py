"""Matched contact sheets: the same moments of one or more videos on one PNG, plus an upload-ready descriptor.

Rows are videos, columns are times, so clips line up for comparison. Frames come from the bundled ffmpeg (one fast seek
per frame); labels are drawn here, so no system font is needed. A time past the end of a clip is an error, never a blank cell.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

DEFAULT_TIMES = (0.5, 1.0, 2.0, 4.0, 6.0, 9.0)  # the G1 recovery contact-sheet convention (an uncut 10 s clip)
MAX_TIMES = 12
MAX_ROWS = 8
CELL_WIDTH = 480
MAX_UPLOAD_IMAGE_BYTES = 10 * 1024**2  # t3_attachment_prepare_upload: images up to 10 MiB


def check_times(times: Any) -> list[float]:
    if times is None:
        return list(DEFAULT_TIMES)
    if not isinstance(times, list) or not 1 <= len(times) <= MAX_TIMES:
        raise ValueError(f"times must be a list of 1 to {MAX_TIMES} seconds, e.g. [0.5, 1, 2, 4, 6, 9]")
    out = []
    for t in times:
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t <= 36000:
            raise ValueError(f"time {t!r} is not a number of seconds from 0 to 36000")
        out.append(float(t))
    return out


def _fmt(t: float) -> str:
    return f"t = {t:g} s"


def extract_frame(ffmpeg: str, video: Path, t: float, dest: Path, width: int) -> None:
    proc = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video),
                           "-frames:v", "1", "-vf", f"scale={width}:-2", str(dest)],
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg could not read a frame at {t:g} s of {video.name}: {proc.stderr.strip()[-300:] or 'no frame'}")


def build(videos: list[Path], infos: list[dict], times: list[float], out: Path, ffmpeg: str,
          extract: Callable[..., None] = extract_frame) -> dict[str, Any]:
    if not 1 <= len(videos) <= MAX_ROWS:
        raise ValueError(f"give 1 to {MAX_ROWS} videos")
    for video, info in zip(videos, infos):
        late = [t for t in times if t > info["duration"] - 0.02]
        if late:
            raise ValueError(f"{video.name} lasts {info['duration']:.2f} s, so frames at {', '.join(f'{t:g}' for t in late)} s do not exist. "
                             "Pick times inside the clip (omit times for the default 0.5 to 9 s on a 10 s clip).")
    cell_w = CELL_WIDTH if len(times) <= 4 else max(240, CELL_WIDTH * 4 // len(times))
    font = ImageFont.load_default(size=max(12, cell_w // 22))
    caption = ImageFont.load_default(size=14)
    cols = min(len(times), 6)
    rows_per_video = -(-len(times) // cols)
    frames: list[list[Image.Image]] = []
    with tempfile.TemporaryDirectory(prefix="sheet-") as tmp:
        for vi, video in enumerate(videos):
            row = []
            for ti, t in enumerate(times):
                dest = Path(tmp) / f"{vi}-{ti}.png"
                extract(ffmpeg, video, t, dest, cell_w)
                with Image.open(dest) as im:
                    row.append(im.convert("RGB"))
            frames.append(row)
    cell_h = max(f.height for row in frames for f in row)
    cap_h = 22
    sheet_w = cols * cell_w
    sheet_h = len(videos) * (cap_h + rows_per_video * cell_h)
    sheet = Image.new("RGB", (sheet_w, sheet_h), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    y = 0
    for video, row in zip(videos, frames):
        draw.text((6, y + 3), video.name, fill=(255, 255, 255), font=caption)
        y += cap_h
        for i, (t, frame) in enumerate(zip(times, row)):
            x, yy = (i % cols) * cell_w, y + (i // cols) * cell_h
            sheet.paste(frame, (x, yy))
            label = _fmt(t)
            box = draw.textbbox((x + 8, yy + 6), label, font=font)
            draw.rectangle((box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2), fill=(0, 0, 0))
            draw.text((x + 8, yy + 6), label, fill=(255, 255, 255), font=font)
        y += rows_per_video * cell_h
    sheet.save(out, "PNG", optimize=True)
    return {"width": sheet_w, "height": sheet_h, "rows": [v.name for v in videos], "times": times, "columns": cols}


def attachment(path: Path) -> dict[str, Any]:
    """Describe the file for t3_attachment_prepare_upload. This module never uploads anything."""
    data = path.read_bytes()
    size = len(data)
    desc: dict[str, Any] = {"file_path": str(path), "upload": {"type": "image", "name": path.name, "mimeType": "image/png", "sizeBytes": size},
                            "sha256": hashlib.sha256(data).hexdigest()}
    if size > MAX_UPLOAD_IMAGE_BYTES:
        desc["warning"] = f"{size} bytes exceeds the 10 MiB image upload limit; use fewer times or videos"
    desc["next_steps"] = ("1. t3_attachment_prepare_upload with this `upload` object; 2. POST the exact bytes of file_path to the returned "
                          "relativeUrl on the MCP server's HTTP origin; 3. t3_thread_send_attachments with the returned attachmentId. "
                          "This tool does not upload (that needs the T3 session).")
    return desc

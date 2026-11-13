"""Image frames to one MP4, with an optional caption drawn on every frame.

For video with no window to record: camera frames a headless simulator saved (sim__camera__capture,
sim__trajectory__replay) become a clip. Frames go through the bundled ffmpeg as raw pixels and the
caption is drawn here, so no system font or display is needed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

MAX_FRAMES = 20000
MAX_LABEL_CHARS = 120
FRAME_SUFFIXES = (".png", ".jpg", ".jpeg")
_NUMBER = re.compile(r"(\d+)")


def natural_key(path: Path) -> list:
    """Sort frame-2.png before frame-10.png."""
    return [int(part) if part.isdigit() else part for part in _NUMBER.split(path.name)]


def frame_files(source: Path) -> list[Path]:
    """The frames a folder holds, or the frames matching a glob pattern, in natural order."""
    if source.is_dir():
        found = [p for p in source.iterdir() if p.suffix.lower() in FRAME_SUFFIXES and p.is_file()]
    else:
        found = [p for p in source.parent.glob(source.name) if p.suffix.lower() in FRAME_SUFFIXES and p.is_file()]
    return sorted(found, key=natural_key)


def check_label(label: Any) -> str | None:
    if label is None or label == "":
        return None
    if not isinstance(label, str) or len(label) > MAX_LABEL_CHARS or "\n" in label:
        raise ValueError(f"label must be one line of at most {MAX_LABEL_CHARS} characters")
    return label


def _caption(image: Image.Image, text: str, font: Any) -> None:
    draw = ImageDraw.Draw(image)
    margin = max(8, image.width // 80)
    box = draw.textbbox((margin, 0), text, font=font)
    y = image.height - (box[3] - box[1]) - 2 * margin
    box = draw.textbbox((margin, y), text, font=font)
    draw.rectangle((box[0] - 6, box[1] - 4, box[2] + 6, box[3] + 4), fill=(0, 0, 0))
    draw.text((margin, y), text, fill=(255, 255, 255), font=font)


def encode(frames: list[Path], out: Path, fps: float, ffmpeg: str, label: str | None = None,
           timeout: float = 900) -> dict[str, Any]:
    """Write ``frames`` to ``out`` (an .mp4 that does not exist yet) at ``fps``. Frames of another size
    than the first are scaled to it."""
    if not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError(f"give 1 to {MAX_FRAMES} frames, not {len(frames)}")
    if not 1 <= fps <= 60:
        raise ValueError("fps must be 1..60")
    label = check_label(label)
    with Image.open(frames[0]) as first:
        width, height = first.width - first.width % 2, first.height - first.height % 2
    if width < 16 or height < 16:
        raise ValueError(f"{frames[0].name} is too small to encode ({width}x{height})")
    font = ImageFont.load_default(size=max(14, height // 28)) if label else None
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{width}x{height}", "-r", f"{fps:g}", "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for path in frames:
            with Image.open(path) as im:
                frame = im.convert("RGB")
            if frame.size != (width, height):
                frame = frame.resize((width, height))
            if label:
                _caption(frame, label, font)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        stderr = proc.stderr.read().decode(errors="replace")
        code = proc.wait(timeout=timeout)
    except BaseException:
        proc.kill()
        proc.wait()
        out.unlink(missing_ok=True)
        raise
    if code != 0 or not out.is_file() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not encode the frames: {stderr.strip()[-300:] or 'no output'}")
    return {"path": str(out), "frames": len(frames), "fps": fps, "duration_s": round(len(frames) / fps, 3),
            "width": width, "height": height, **({"label": label} if label else {})}

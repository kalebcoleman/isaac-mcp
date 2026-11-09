"""join_videos mode=sheet: matched PNG contact sheet + upload descriptor (real bundled ffmpeg on 3-4 s generated clips)."""
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from isaac_mcp import contact_sheet, screen


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "Videos" / "screen-mcp").mkdir(parents=True)
    (h / "Pictures" / "screen-mcp").mkdir(parents=True)
    monkeypatch.setattr(screen, "VIDEO_DIR", h / "Videos" / "screen-mcp")
    monkeypatch.setattr(screen, "SHOT_DIR", h / "Pictures" / "screen-mcp")
    monkeypatch.setattr(screen, "_allowed_roots", lambda: [h.resolve(), (h / "Videos" / "screen-mcp").resolve()])
    return h


def make_clip(path, seconds=4, pattern="testsrc"):
    ff = screen._ffmpeg()
    proc = subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"{pattern}=size=320x180:rate=25:duration={seconds}",
                           "-pix_fmt", "yuv420p", str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return path


def parse(text):
    return dict(line.split(": ", 1) for line in text.splitlines())


def test_sheet_matches_times_across_videos(home):
    a = make_clip(home / "Videos" / "screen-mcp" / "a.mp4")
    b = make_clip(home / "Videos" / "screen-mcp" / "b.mp4", pattern="smptebars")
    out = parse(screen._contact_sheet([str(a), "b.mp4"], None, [0.5, 1, 2, 3], "cmp.png"))
    png = Path(out["path"])
    assert png == (home / "Pictures" / "screen-mcp").resolve() / "cmp.png" and png.is_file()
    with Image.open(png) as im:
        assert im.size[0] == 4 * contact_sheet.CELL_WIDTH and im.size[1] > 2 * 200 and im.format == "PNG"
    assert out["rows"] == "a.mp4, b.mp4" and out["times_s"] == "0.5, 1, 2, 3"
    att = json.loads(out["attachment"])
    assert att["upload"] == {"type": "image", "name": "cmp.png", "mimeType": "image/png", "sizeBytes": png.stat().st_size}
    assert att["file_path"] == str(png) and len(att["sha256"]) == 64 and "t3_attachment_prepare_upload" in att["next_steps"] and "warning" not in att


def test_default_times_follow_the_recording_convention(home):
    a = make_clip(home / "Videos" / "screen-mcp" / "a.mp4", seconds=10)
    out = parse(screen._contact_sheet([str(a)], None, None, None))
    assert out["times_s"] == "0.5, 1, 2, 4, 6, 9" and Path(out["path"]).name.startswith("sheet-")
    with Image.open(out["path"]) as im:
        assert im.size[0] == 6 * (contact_sheet.CELL_WIDTH * 4 // 6)


def test_time_past_the_end_is_an_error_and_writes_nothing(home):
    a = make_clip(home / "Videos" / "screen-mcp" / "a.mp4", seconds=3)
    with pytest.raises(ValueError, match="lasts 3.00 s.*4, 9 s do not exist"):
        screen._contact_sheet([str(a)], None, [1, 4, 9], "x.png")
    assert not list((home / "Pictures" / "screen-mcp").iterdir())


def test_never_overwrites_and_validates(home):
    a = make_clip(home / "Videos" / "screen-mcp" / "a.mp4")
    screen._contact_sheet([str(a)], None, [1], "x.png")
    with pytest.raises(ValueError, match="already exists"):
        screen._contact_sheet([str(a)], None, [1], "x.png")
    for args, match in [(([str(a)], "J", [1], None), "not both"), ((None, None, [1], None), "not both"),
                        (([str(a)], None, [], None), "times must be"), (([str(a)], None, [-1], None), "not a number"),
                        (([str(a)], None, [True], None), "not a number"), (([str(a)], None, [1], "x.jpg"), ".png"),
                        (([], None, [1], None), "1..8 videos"), (([str(a)] * 9, None, [1], None), "1..8 videos")]:
        with pytest.raises(ValueError, match=match):
            screen._contact_sheet(*args)


def test_job_id_uses_the_jobs_videos(home, monkeypatch):
    a = make_clip(home / "Videos" / "screen-mcp" / "a.mp4")
    from isaac_mcp.isaaclab import jobs

    monkeypatch.setattr(jobs, "runs_status", lambda j: {"video_paths": [str(a)]} if j == "20261102-100000-T" else {})
    out = parse(screen._contact_sheet(None, "20261102-100000-T", [1, 2], "j.png"))
    assert out["rows"] == "a.mp4"
    with pytest.raises(ValueError, match="has no videos yet"):
        screen._contact_sheet(None, "20261102-100001-T", [1], "k.png")


def test_missing_ffmpeg_fails_clearly(home, monkeypatch):
    a = make_clip(home / "Videos" / "screen-mcp" / "a.mp4")

    def boom():
        raise RuntimeError("ffmpeg is not available")

    monkeypatch.setattr(screen, "_ffmpeg", boom)
    with pytest.raises(RuntimeError, match="ffmpeg is not available: install imageio-ffmpeg"):
        screen._contact_sheet([str(a)], None, [1], "z.png")


def test_oversize_descriptor_warns(tmp_path):
    p = tmp_path / "big.png"
    p.write_bytes(b"0" * (contact_sheet.MAX_UPLOAD_IMAGE_BYTES + 1))
    assert "10 MiB" in contact_sheet.attachment(p)["warning"]

"""Image frames to a labelled MP4: the video path that needs no window."""

from pathlib import Path

import pytest
from PIL import Image

from twinsmith import frames_video, screen


@pytest.fixture
def ffmpeg():
    try:
        return screen._ffmpeg()
    except RuntimeError:
        pytest.skip("no ffmpeg")


def write_frames(folder, count=6, size=(96, 64), stem="replay"):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(1, count + 1):
        path = folder / f"{stem}-{index:05d}.png"
        Image.new("RGB", size, (index * 30 % 255, 40, 200)).save(path)
        paths.append(path)
    return paths


def test_frames_sort_naturally_from_a_folder_or_a_pattern(tmp_path):
    for name in ("shot-10.png", "shot-2.png", "shot-1.png", "notes.txt", "other-1.png"):
        (tmp_path / name).write_bytes(b"x")
    assert [p.name for p in frames_video.frame_files(tmp_path)] == ["other-1.png", "shot-1.png", "shot-2.png", "shot-10.png"]
    assert [p.name for p in frames_video.frame_files(tmp_path / "shot-*.png")] == ["shot-1.png", "shot-2.png", "shot-10.png"]
    assert frames_video.frame_files(tmp_path / "none-*.png") == []


def test_frames_become_a_video_with_the_label_drawn_on_them(tmp_path, ffmpeg):
    frames = write_frames(tmp_path / "frames")
    plain = frames_video.encode(frames, tmp_path / "plain.mp4", 3, ffmpeg)
    labelled = frames_video.encode(frames, tmp_path / "labelled.mp4", 3, ffmpeg, label="PhysX hover, kp=4")
    assert plain == {"path": str(tmp_path / "plain.mp4"), "frames": 6, "fps": 3, "duration_s": 2.0, "width": 96, "height": 64}
    assert labelled["label"] == "PhysX hover, kp=4"
    info = screen.probe_video(ffmpeg, tmp_path / "labelled.mp4")
    assert (info["width"], info["height"]) == (96, 64) and info["duration"] == pytest.approx(2.0, abs=0.4)
    first = tmp_path / "first.png"
    from twinsmith import contact_sheet

    contact_sheet.extract_frame(ffmpeg, tmp_path / "labelled.mp4", 0.0, first, 96)
    with Image.open(first) as image:
        padding = image.convert("RGB").getpixel((4, image.height - 20))
    assert max(padding) < 90, "the caption's dark box is in the bottom-left corner"


def test_bad_inputs_are_refused_and_leave_no_file(tmp_path, ffmpeg):
    frames = write_frames(tmp_path / "frames", count=2)
    out = tmp_path / "out.mp4"
    for kwargs, problem in ((dict(fps=0), "fps"), (dict(fps=10, label="a\nb"), "label"), (dict(fps=10, label="x" * 121), "label")):
        with pytest.raises(ValueError, match=problem):
            frames_video.encode(frames, out, ffmpeg=ffmpeg, **kwargs)
    with pytest.raises(ValueError, match="1 to"):
        frames_video.encode([], out, 10, ffmpeg)
    (tmp_path / "frames" / "broken.png").write_bytes(b"not an image")
    with pytest.raises(Exception):
        frames_video.encode(frames + [tmp_path / "frames" / "broken.png"], out, 10, ffmpeg)
    assert not out.exists()


def test_join_videos_frames_mode_reads_a_pattern_under_home(tmp_path, monkeypatch, ffmpeg):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(screen, "VIDEO_DIR", tmp_path / "Videos")
    (tmp_path / "Videos").mkdir()
    write_frames(tmp_path / "out")
    text = screen._frames_video([str(tmp_path / "out" / "replay-*.png")], "clip.mp4", 4, "run 7")
    result = dict(line.split(": ", 1) for line in text.splitlines())
    assert result["mode"] == "frames" and result["frames"] == "6" and result["label"] == "run 7"
    assert Path(result["path"]) == tmp_path / "Videos" / "clip.mp4" and Path(result["path"]).stat().st_size > 0
    with pytest.raises(ValueError, match="already exists"):
        screen._frames_video([str(tmp_path / "out")], "clip.mp4", None, None)
    for inputs, problem in (([str(tmp_path / "out" / "none-*.png")], "no .png or .jpg frames"), (["relative/*.png"], "absolute"),
                            (["/etc/*.png"], "outside your home"), ([], "frames: give inputs"),
                            ([str(tmp_path / "out" / "replay-00001.png"), str(tmp_path / "out" / "missing.png")], "existing")):
        with pytest.raises(ValueError, match=problem):
            screen._frames_video(inputs, None, None, None)

"""Scaled desktop recording reports the dimensions it actually encodes."""
import io
import threading
from unittest.mock import Mock

import pytest

from twinsmith import screen


@pytest.mark.parametrize("width,height,limit,expected", [
    (1440, 900, 960, (960, 600)),
    (1440, 900, 961, (960, 600)),
    (1280, 720, 1600, (1280, 720)),
    (1000, 666, 777, (776, 516)),
    (80, 80, 320, (80, 80)),
])
def test_even_scaled_dimensions(width, height, limit, expected):
    assert screen._scaled_recording_size(width, height, limit) == expected


def test_finish_reports_scaled_dimensions_instead_of_input_window(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"encoded video fixture")
    proc = Mock(stderr=io.BytesIO())
    recording = screen.Recording(
        path=path, bbox={"width": 1440, "height": 900}, fps=10, max_width=960,
        proc=proc, stop=threading.Event(), out_width=960, out_height=600, frames=20,
    )
    result = screen._finish(recording)
    assert (result["width"], result["height"]) == (960, 600)
    assert result["duration_s"] == 2
    assert result["output"] == "960x600 (scaled capture)"

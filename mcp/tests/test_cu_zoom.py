"""Zoom: window-capture regions (crop, enlarge, token) and sim__ui find zoom_ref, without X11 or Kit."""

import importlib.util
import io
import json
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from PIL import Image as PILImage

from twinsmith import computer_use, screen

ROOT = Path(__file__).resolve().parents[2]
TOKEN = re.compile(r"coord_space: (\S+)")


# ------------------------------------------------------------------ crop math


def test_zoom_size_upscales_small_crops_at_most_four_times():
    assert screen.zoom_size(50, 30, 1600) == (200, 120)  # capped at 4x
    assert screen.zoom_size(800, 200, 1600) == (1600, 400)  # 2x fills max_width
    assert screen.zoom_size(400, 400, 1600) == (1600, 1600)  # exactly 4x


def test_zoom_size_downscales_large_crops_to_fit():
    assert screen.zoom_size(2560, 1440, 1600) == (1600, 900)
    assert screen.zoom_size(100, 2000, 1600) == (80, 1600)  # a tall strip fits the square too


def test_region_in_window_pixels_is_taken_as_is_and_must_fit():
    assert screen.region_rect([10, 20, 30, 40], "window", 200, 100) == (10, 20, 30, 40)
    assert screen.region_rect([0, 0, 200, 100], None, 200, 100) == (0, 0, 200, 100)
    for bad in ([190, 0, 20, 10], [-1, 0, 5, 5], [0, 95, 10, 10]):
        with pytest.raises(ValueError, match="outside the 200x100 window"):
            screen.region_rect(bad, "window", 200, 100)
    for bad in ([0, 0, 0, 5], [0, 0, 5], [0, 0, 5.0, 5], "0,0,5,5"):
        with pytest.raises(ValueError):
            screen.region_rect(bad, "window", 200, 100)


def test_region_in_a_downscaled_image_selects_the_window_pixels_it_shows():
    usual = screen.coord_token(1600, 900, (0, 0, 2560, 1440), 2560, 1440)  # the default 1600-wide screenshot
    assert screen.region_rect([0, 0, 1600, 900], usual, 2560, 1440) == (0, 0, 2560, 1440)
    # Corners snap to the window pixel under each corner pixel's centre: at 2x that trims one leading pixel.
    token = screen.coord_token(1000, 500, (0, 0, 2000, 1000), 2000, 1000)  # a half-size screenshot
    assert screen.region_rect([0, 0, 1000, 500], token, 2000, 1000) == (1, 1, 1999, 999)
    # Corners map through pixel centres (map_point): 100.5 * 2 = 201 to 109.5 * 2 = 219, inclusive.
    assert screen.region_rect([100, 50, 10, 10], token, 2000, 1000) == (201, 101, 19, 19)
    with pytest.raises(ValueError, match="outside that 1000x500 image"):
        screen.region_rect([995, 0, 10, 10], token, 2000, 1000)
    with pytest.raises(ValueError, match="capture it again"):
        screen.region_rect([0, 0, 10, 10], token, 1999, 1000)  # the window changed size


# ------------------------------------------------------------------ capture


def _gradient(width: int, height: int) -> PILImage.Image:
    """Each pixel encodes its own position: R = x % 256, G = y % 256, B = x // 256."""
    img = PILImage.new("RGB", (width, height))
    img.putdata([(x % 256, y % 256, x // 256) for y in range(height) for x in range(width)])
    return img


@pytest.fixture
def fake_window(monkeypatch, tmp_path):
    win = dict(id=0x42, title="Isaac Sim", width=1200, height=800, x=0, y=0)
    monkeypatch.setattr(screen, "SHOT_DIR", tmp_path)
    monkeypatch.setattr(screen, "_match_window", lambda window, window_id: win)
    monkeypatch.setattr(screen, "_grab_window_image", lambda w: _gradient(w["width"], w["height"]))
    return win


def _shot(result):
    image, caption = result
    inline = PILImage.open(io.BytesIO(image.data))
    saved = PILImage.open(re.search(r"saved: (\S+)", caption).group(1))
    return inline, saved, caption, TOKEN.search(caption).group(1)


def test_a_small_region_is_cropped_at_full_size_and_shown_enlarged(fake_window):
    inline, saved, caption, token = _shot(screen.capture(window_id=0x42, region=[300, 200, 100, 50]))
    assert saved.size == (100, 50) and saved.getpixel((0, 0)) == (300 % 256, 200, 1)  # the crop, unscaled
    assert inline.size == (400, 200)  # 4x cap, not 16x to reach max_width
    assert token == "img:400x200@300,200,100x50/1200x800"
    assert "region: 300,200,100x50 of the 1200x800 window, shown 4.00x" in caption
    # A point measured in the enlarged crop is the same window pixel the crop shows there.
    for px, py in ((0, 0), (399, 199), (202, 101)):
        wx, wy = screen.map_point(token, px, py, 1200, 800)
        assert saved.getpixel((wx - 300, wy - 200)) == ((wx % 256), wy % 256, wx // 256)
        assert (wx, wy) == (300 + px // 4, 200 + py // 4)


def test_a_region_picked_in_a_downscaled_screenshot_zooms_the_same_place(fake_window):
    _, _, _, first = _shot(screen.capture(window_id=0x42, max_width=600))
    assert first == "img:600x400@0,0,1200x800/1200x800"
    inline, saved, _, token = _shot(screen.capture(window_id=0x42, region=[150, 100, 50, 25], coord_space=first,
                                                   max_width=600))
    assert saved.size == (99, 49) and inline.size == (396, 196)
    assert token == "img:396x196@301,201,99x49/1200x800"
    # And a zoom of that zoom maps straight back to window pixels.
    _, deeper, _, inner = _shot(screen.capture(window_id=0x42, region=[0, 0, 200, 100], coord_space=token))
    assert inner.endswith("@301,201,50x25/1200x800") and deeper.size == (50, 25)


def test_a_large_region_is_scaled_down_and_its_token_still_round_trips(fake_window):
    inline, saved, _, token = _shot(screen.capture(window_id=0x42, region=[0, 0, 1200, 800], max_width=600))
    assert saved.size == (1200, 800) and inline.size == (600, 400)
    assert screen.map_point(token, 599, 399, 1200, 800) == (1199, 799)


def test_region_outside_the_window_is_refused_before_saving(fake_window, tmp_path):
    with pytest.raises(ValueError, match="outside the 1200x800 window"):
        screen.capture(window_id=0x42, region=[1150, 0, 100, 100])
    assert list(tmp_path.iterdir()) == []


def test_region_needs_a_window_target(monkeypatch, tmp_path):
    monkeypatch.setattr(screen, "SHOT_DIR", tmp_path)
    with pytest.raises(ValueError, match="window or window_id"):
        screen.capture(monitor=1, region=[0, 0, 10, 10])


# ------------------------------------------------------------------ Kit zoom_rect


@pytest.fixture
def kit_ui(monkeypatch):
    spec = importlib.util.spec_from_file_location("sim_tools_ui_zoom", ROOT / "exts/sim-tools/sim_tools/ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stage = SimpleNamespace(title="Stage", position_x=0, position_y=0, width=500, height=500, docked=False,
                            visible=True)
    fake = ModuleType("omni.ui")
    fake.Workspace = SimpleNamespace(get_dpi_scale=lambda: 2.0, get_windows=lambda: [stage],
                                     get_window=lambda title: stage if title == "Stage" else None)
    omni = ModuleType("omni")
    omni.ui = fake
    monkeypatch.setitem(sys.modules, "omni", omni)
    monkeypatch.setitem(sys.modules, "omni.ui", fake)

    class Button:
        visible, enabled = True, True
        identifier, name, text = "b", "play", ""

    target = SimpleNamespace(widget=Button(), center=SimpleNamespace(x=40, y=30), size=SimpleNamespace(x=20, y=10))
    finder = SimpleNamespace(find=lambda path: target)
    monkeypatch.setattr(module, "_ui_test", lambda: finder)
    return SimpleNamespace(module=module, target=target, finder=finder)


REF = "Stage//Frame/VStack[0]/Button[2]|Button:b"


def test_zoom_rect_is_the_widget_rectangle_in_window_pixels(kit_ui):
    rect = kit_ui.module.zoom_rect(REF)
    # Centre (40, 30) and size 20x10 UI points at dpi 2 -> 40x20 pixels around (80, 60).
    assert rect == dict(ref=REF, type="Button", name="play", x=60, y=50, width=40, height=20)


def test_find_routes_zoom_ref_before_listing_and_after_the_headless_check(kit_ui, monkeypatch):
    sys.path.insert(0, str(ROOT / "exts/sim-tools"))
    from sim_tools import graph, ui

    monkeypatch.setattr(ui, "zoom_rect", lambda ref: {"zoomed": ref})
    monkeypatch.setattr(graph, "_headless", lambda: False)
    assert ui.find({"zoom_ref": REF, "query": "ignored", "limit": 0}) == {"zoomed": REF}
    monkeypatch.setattr(graph, "_headless", lambda: True)
    with pytest.raises(ui.UIRefusal) as err:
        ui.find({"zoom_ref": REF})
    assert err.value.code == "NO_UI"


def test_zoom_rect_refuses_a_stale_ref(kit_ui):
    kit_ui.finder.find = lambda path: None
    with pytest.raises(kit_ui.module.UIRefusal) as err:
        kit_ui.module.zoom_rect(REF)
    assert err.value.code == "STALE_WIDGET"


def test_zoom_rect_refuses_a_ref_without_a_fingerprint(kit_ui):
    with pytest.raises(kit_ui.module.UIRefusal) as err:
        kit_ui.module.zoom_rect("Stage//Frame/VStack[0]/Button[2]")
    assert err.value.code == "INVALID_VALUE"


# ------------------------------------------------------------------ host zoom branch


def test_zoom_region_pads_to_a_minimum_and_stays_inside_the_window():
    rect = dict(x=500, y=400, width=40, height=20)
    assert computer_use.zoom_region(rect, 1200, 800) == [420, 310, 200, 200]  # minimum, centred
    big = dict(x=100, y=100, width=400, height=300)
    assert computer_use.zoom_region(big, 1200, 800) == [68, 68, 464, 364]  # 32 px of context each side
    assert computer_use.zoom_region(dict(x=0, y=0, width=10, height=10), 1200, 800) == [0, 0, 200, 200]
    assert computer_use.zoom_region(dict(x=1190, y=790, width=10, height=10), 1200, 800) == [1000, 600, 200, 200]
    assert computer_use.zoom_region(dict(x=0, y=0, width=150, height=150), 150, 120) == [0, 0, 150, 120]


def _failed(result: str) -> bool:
    return json.loads(result).get("status") == "error"


@pytest.fixture
def host(monkeypatch):
    calls = SimpleNamespace(dispatched=[], captured=[])
    monkeypatch.setattr(computer_use, "kit_window", lambda state: dict(id=7, width=1200, height=800))

    def attach(text, failed, state, region=None):
        calls.captured.append((text, failed, region))
        return "capture"

    monkeypatch.setattr(computer_use, "attach_capture", attach)
    return calls


def test_find_with_zoom_ref_returns_the_padded_crop(host):
    answer = json.dumps(dict(status="ok", ref=REF, type="Button", name="play", x=60, y=50, width=40, height=20))
    seen = []
    result = computer_use.kit_ui("sim.ui.find", {"zoom_ref": REF}, None,
                                 lambda c, p, s: (seen.append((c, p)), answer)[1], _failed)
    assert result == "capture"
    assert seen == [("sim.ui.find", {"zoom_ref": REF})]
    assert host.captured == [(answer, False, [0, 0, 200, 200])]


def test_find_with_a_stale_zoom_ref_raises_without_capturing(host):
    answer = json.dumps(dict(status="error", code="STALE_WIDGET", message="call sim__ui__find again"))
    with pytest.raises(ToolError, match="STALE_WIDGET"):
        computer_use.kit_ui("sim.ui.find", {"zoom_ref": REF, "screenshot_after": True}, None,
                            lambda c, p, s: answer, _failed)
    assert host.captured == []


def test_find_with_zoom_ref_needs_the_isaac_window(host, monkeypatch):
    monkeypatch.setattr(computer_use, "kit_window", lambda state: None)
    answer = json.dumps(dict(status="ok", ref=REF, x=60, y=50, width=40, height=20))
    with pytest.raises(ToolError, match="NO_WINDOW"):
        computer_use.kit_ui("sim.ui.find", {"zoom_ref": REF}, None, lambda c, p, s: answer, _failed)
    assert host.captured == []


def test_find_without_zoom_ref_is_forwarded_unchanged(host):
    answer = json.dumps(dict(status="ok", widgets=[]))
    assert computer_use.kit_ui("sim.ui.find", {"query": "play"}, None, lambda c, p, s: answer, _failed) == answer
    assert host.captured == []

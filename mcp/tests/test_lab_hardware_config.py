"""Host-side checks for units, provenance and rejected work-cell configurations."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "lab_hardware_config", ROOT / "exts/ur5e-dt/ur5e_dt/hardware_config.py")
hardware = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hardware)


def test_real_command_units_are_tenths_of_a_millimetre():
    assert hardware.parse_width_command("240") == pytest.approx(0.024)
    assert hardware.parse_width_command("1100") == pytest.approx(0.110)
    assert hardware.parse_width_command("OPEN") == 0.110
    assert hardware.parse_width_command("close") == 0.0
    assert hardware.parse_width_command("query") is None


@pytest.mark.parametrize("command", ["1101", "-1", "24.0", "nan", "１２", "move 24", ""])
def test_bad_width_commands_do_not_actuate(command):
    with pytest.raises(ValueError):
        hardware.parse_width_command(command)


def test_default_cell_has_provenance_and_full_size_tower():
    cfg, raw = hardware.load_config()
    assert cfg["jenga"]["block_size_m"] == [0.072, 0.024, 0.014]
    assert cfg["jenga"]["tower_layers_built"] == 18
    assert cfg["gravity"]["mode"] == "physics"
    assert raw["wrist_camera"]["offset_in_tool0_m"]["status"] == "conflict"


@pytest.mark.parametrize("order", [["bota", "rg2", "quick_changer"], ["bota", "bota", "rg2"]])
def test_invalid_stack_rejected_before_scene_changes(tmp_path, order):
    _, raw = hardware.load_config()
    raw["stack"]["order"]["value"] = order
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="stack.order"):
        hardware.load_config(path)


def test_camera_intrinsics_match_configured_fov():
    import math
    fx, fy, cx, cy = hardware.camera_intrinsics([1280, 720], [69.4, 42.5])
    assert math.degrees(2 * math.atan(cx / fx)) == pytest.approx(69.4)
    assert math.degrees(2 * math.atan(cy / fy)) == pytest.approx(42.5)

"""Ordering guarantees checked in the extension sources that need Kit to run."""

from pathlib import Path



ROOT = Path(__file__).resolve().parents[2]


def _source(relative):
    return (Path(__file__).resolve().parents[2] / relative).read_text(encoding="utf-8")



def test_quick_start_registers_what_it_built_after_stage_info_resets_ownership():
    source = _source("exts/sim-tools/sim_tools/quick_start.py")
    assert source.index("info = scene.stage_info()") < source.index("scene.owned.update(")
    assert all(name in source[source.index("scene.owned.update("):][:120] for name in ("GROUND", "PHYSICS_SCENE", "DOME", "SUN", "CAMERA"))



def test_a_capture_is_never_visible_at_its_final_name_until_complete():
    source = _source("exts/sim-tools/sim_tools/advanced.py")
    assert source.index('Image.fromarray(array).save(stream, format="PNG")') < source.index("os.link(temp, filename")

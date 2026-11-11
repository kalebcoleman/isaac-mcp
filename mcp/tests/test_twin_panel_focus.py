"""The --ur5e-panel window is brought to the front once its deferred docking has run."""

import ast
from pathlib import Path
import twin_source

SOURCE = twin_source.SOURCE


def _source() -> str:
    return SOURCE.read_text()


def test_showing_the_panel_schedules_a_focus_after_docking():
    tree = ast.parse(_source())
    startup = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "on_startup")
    calls = [n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
             for n in ast.walk(startup) if isinstance(n, ast.Call)]
    assert "deferred_dock_in" in calls and "_focus_panel_soon" in calls


def test_focus_shows_and_focuses_the_twin_window():
    tree = ast.parse(_source())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == "_focus_panel_soon")
    text = ast.get_source_segment(_source(), method)
    assert 'show_window("UR5e Digital Twin", True)' in text
    assert ".focus()" in text
    assert "next_update_async" in text  # the deferred docking needs frames to land first
    # A UI failure is logged and never allowed to fail extension startup.
    assert "log_warn" in text

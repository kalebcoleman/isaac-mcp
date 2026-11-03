"""Behavioral checks for the two Antigravity lab runs."""

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from isaac_mcp.project_assets import AssetError
from sim_tools.advanced import Advanced
from sim_tools.physics import body_path


def test_body_path_accepts_aliases_and_rejects_conflicting_targets():
    for key in ("path", "prim", "prim_path"):
        assert body_path({key: "/Robot/base"}) == "/Robot/base"
    assert body_path({"path": "/Robot", "prim": "/Robot"}) == "/Robot"
    for args in ({}, {"prim": ""}, {"path": "/A", "prim": "/B"}):
        with pytest.raises(AssetError) as error:
            body_path(args)
        assert error.value.code == "INVALID_VALUE"


def test_graph_show_playing_error_contains_a_complete_retry():
    scene = SimpleNamespace(stage_id="t", timeline=SimpleNamespace(is_stopped=lambda: False))
    tool = Advanced(scene, None)
    args = dict(session_id="s", stage_id="t", path="/Robot/Graph", layout=True, width=850, height=450)
    with pytest.raises(AssetError) as error:
        tool.execute("sim.graph.show", None, args)
    assert error.value.code == "TIMELINE_ACTIVE"
    assert error.value.details["retry_with"] == {**args, "layout": False}
    assert "layout=false" in str(error.value)

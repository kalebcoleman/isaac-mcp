"""The offline smoke plan changes no stage and talks to a closed port."""

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_smoke_plan_has_no_stage_changing_tool_and_pins_a_closed_port(monkeypatch):
    smoke = _module("smoke_mcp_surface")
    assert not {name for name, _ in smoke.OFFLINE_PLAN} & {"ur5e_dt__new_stage", "ur5e_dt__quick_start"}
    seen = {}

    def fake_run(plan):
        seen["port"] = os.environ.get("UR5E_DT_PORT")

        async def nothing():
            return []
        return nothing()

    monkeypatch.setattr(smoke, "run", fake_run)
    monkeypatch.setenv("UR5E_DT_PORT", "8766")
    assert smoke.main(["smoke", "offline"]) == 0
    assert seen["port"] == smoke.OFFLINE_PORT

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]


def load_commands():
    spec = importlib.util.spec_from_file_location("workcell_commands", ROOT / "exts/ur5e-dt/ur5e_dt/commands.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Commands()


def test_workcell_forwarding_and_reports():
    commands = load_commands()
    calls = []

    async def quick_start(**kwargs):
        calls.append(kwargs)
        return {"state": "ready"}

    report = {"state": "ready", "robot": {"name": "arm"}, "cameras": ["camera"], "jenga": {"layers": 18}}
    commands.workcell = SimpleNamespace(quick_start=quick_start, status=lambda: report,
                                        shutdown=lambda: {"stage_preserved": True})
    assert asyncio.run(commands._cmd_workcell_quick_start("config.json", "mirror_real", True)) == {"state": "ready"}
    assert calls == [{"config_path": "config.json", "mode": "mirror_real", "discard_changes": True}]
    assert commands._cmd_workcell_status() == report
    assert commands._cmd_robot_status()["robot"] == report["robot"]
    assert commands._cmd_perception_status()["cameras"] == report["cameras"]
    assert commands._cmd_jenga_status()["jenga"] == report["jenga"]
    assert commands._cmd_workcell_shutdown() == {"stage_preserved": True}


def test_interface_stubs_are_explicit():
    commands = load_commands()
    for name in ("real2sim", "planning", "eval"):
        assert getattr(commands, f"_cmd_{name}_status")()["status"] == "not_implemented"

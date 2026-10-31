import asyncio
import json

from isaac_mcp import server
from isaac_mcp.isaaclab import tools


EXPECTED = {
    "isaaclab__install_info", "isaaclab__envs_list", "isaaclab__train", "isaaclab__eval", "isaaclab__play",
    "isaaclab__runs_list", "isaaclab__runs_status", "isaaclab__runs_tail", "isaaclab__runs_cancel",
    "isaaclab__checkpoints_list", "isaaclab__metrics_summary", "isaaclab__tensorboard_tail",
}


def test_all_tools_registered_on_the_server():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert EXPECTED <= names


def test_launch_without_install_returns_a_hint(monkeypatch):
    monkeypatch.setattr(tools._install, "locate_install", lambda env=None: None)
    out = json.loads(tools._launch(tools._jobs.train, task="Isaac-Reach-Franka-v0"))
    assert out["error"] == "no Isaac Lab install found" and "ISAACLAB_PYTHON" in out["hint"]


def test_launch_without_checkout_returns_actionable_error(monkeypatch):
    monkeypatch.setattr(tools._install, "locate_install", lambda env=None: {
        "python": "/bin/true", "isaaclab_root": None})
    out = json.loads(tools._launch(tools._jobs.train, task="Isaac-Reach-Franka-v0"))
    assert out["error"] == "Isaac Lab checkout not found"
    assert "ISAACLAB_ROOT" in out["hint"]


def test_bad_arguments_come_back_as_errors_not_exceptions(monkeypatch):
    monkeypatch.setattr(
        tools._install, "locate_install", lambda env=None: {"python": "/bin/true", "isaaclab_root": "/tmp"}
    )
    out = json.loads(tools._launch(tools._jobs.train, task="bad task; rm -rf /"))
    assert "error" in out
    assert "error" in json.loads(tools._guard(lambda: tools._jobs.runs_status("../../etc")))

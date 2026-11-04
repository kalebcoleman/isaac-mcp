"""Kit reloading sim-tools while a tool call runs must not lose the tools."""

import ast
import asyncio
import importlib.util
from pathlib import Path

import pytest

from isaac_mcp_bridge.bridge import McpBridge

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("sim_tools_reload_under_test", REPO / "exts/sim-tools/sim_tools/reload.py")
reload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reload)


@pytest.fixture()
def bridge():
    return McpBridge(schedule_coroutine=lambda coro: None)


def retire_with_call_in_flight(bridge, owner, namespace):
    bridge.register_owner(owner, namespace)
    bridge._in_flight[owner] = 1
    bridge.unregister_owner(owner)


def finish_call(bridge, owner):
    with bridge._lock:
        bridge._in_flight[owner] = 0
        bridge._finalize_owner_locked(owner)


def test_the_failure_this_replaces_is_real(bridge):
    retire_with_call_in_flight(bridge, "generic-sim", "sim")
    with pytest.raises(ValueError, match="already registered"):
        bridge.register_owner("generic-sim", "sim")
    assert reload.draining(bridge) == ["generic-sim"]


def test_tools_come_back_when_the_old_call_finishes(bridge):
    retire_with_call_in_flight(bridge, "generic-sim", "sim")
    messages, registered = [], []

    def register():
        registered.append(bridge.register_owner("generic-sim", "sim"))

    async def scenario():
        task = asyncio.ensure_future(reload.register_when_free(
            bridge, register, lambda m, e: messages.append((m, e)), poll_s=0.01))
        await asyncio.sleep(0.05)
        assert not registered and not task.done()
        finish_call(bridge, "generic-sim")
        return await asyncio.wait_for(task, 2)

    assert asyncio.run(scenario()) is True
    assert registered and "generic-sim" in bridge.get_owners()
    assert messages and messages[-1][1] is False


def test_a_long_wait_is_reported_but_never_given_up(bridge):
    retire_with_call_in_flight(bridge, "generic-sim", "sim")
    messages = []

    async def scenario():
        task = asyncio.ensure_future(reload.register_when_free(
            bridge, lambda: bridge.register_owner("generic-sim", "sim"), lambda m, e: messages.append((m, e)),
            poll_s=0.01, warn_after_s=0.02))
        await asyncio.sleep(0.1)
        assert not task.done()
        assert [m for m, e in messages if e and "still unavailable" in m]
        finish_call(bridge, "generic-sim")
        return await asyncio.wait_for(task, 2)

    assert asyncio.run(scenario()) is True


def test_failed_late_registration_is_reported_not_raised(bridge):
    messages = []

    def broken():
        raise ValueError("nope")

    assert asyncio.run(reload.register_when_free(bridge, broken, lambda m, e: messages.append((m, e)))) is False
    assert messages[0][1] is True and "nope" in messages[0][0]


def test_a_real_duplicate_owner_is_not_draining(bridge):
    bridge.register_owner("generic-sim", "sim")
    assert reload.draining(bridge) == []


def method(name):
    tree = ast.parse((REPO / "exts/sim-tools/sim_tools/extension.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Extension")
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


def test_shutdown_attempts_unregister_even_if_earlier_steps_fail():
    # The body is Kit-only. Every cleanup call must go through attempt(), or a failing step leaves
    # the owner registered and the next start fails.
    source = ast.unparse(method("on_shutdown"))
    for step in ("self.follower.stop", "self.advanced.close_sensors", "self.scene.close_stage", "self.bridge.unregister_owner"):
        assert f"attempt({step}" in source


def test_startup_defers_registration_only_while_draining():
    source = ast.unparse(method("on_startup"))
    assert "register_when_free" in source and "if draining(self.bridge)" in source


def test_run_sh_turns_the_watcher_off_by_flag_and_variable():
    text = (REPO / "run.sh").read_text()
    assert "--no-hot-reload)" in text and "ISAAC_MCP_NO_HOT_RELOAD" in text
    assert '"--/app/extensions/fsWatcherEnabled=0"' in text


@pytest.mark.parametrize("layout,code", [("pip-none", 2), ("pip", None), ("standalone", None)])
def test_run_sh_refuses_newton_before_kit_when_the_build_has_none(tmp_path, layout, code):
    """The interpreter is faked; a build with Newton gets past the check to exec. A standalone app keeps
    exts two levels above the isaacsim package (<app>/python_packages/isaacsim, <app>/exts)."""
    import subprocess

    site = tmp_path / "app" / "python_packages" / "isaacsim"
    site.mkdir(parents=True)
    if layout == "pip":
        (site / "exts" / "isaacsim.physics.newton").mkdir(parents=True)
    elif layout == "standalone":
        (tmp_path / "app" / "exts" / "isaacsim.physics.newton").mkdir(parents=True)
    python = tmp_path / "python"
    python.write_text(f'#!/bin/sh\ncase "$2" in *dirname*) echo "{site}";; esac\n[ "$1" = "-c" ] && exit 0\necho started; exit 0\n')
    python.chmod(0o755)
    result = subprocess.run(["bash", str(REPO / "run.sh"), "--headless", "--port", "18999", "--physics-backend", "newton"],
                            env={"ISAAC_MCP_PYTHON": str(python), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                            capture_output=True, text=True, timeout=30)
    if code is None:
        assert result.returncode == 0 and "started" in result.stdout, result.stderr
    else:
        assert result.returncode == code and "no isaacsim.physics.newton" in result.stderr

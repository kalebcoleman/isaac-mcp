"""One source for the physics backend default: configuration, launchers, status. No silent fallback."""

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from twinsmith import config, server

REPO = Path(__file__).resolve().parents[2]
OTHER = {name: next(o for o in config.PHYSICS_BACKENDS if o != name) for name in config.PHYSICS_BACKENDS}
PORT = "18997"


@pytest.fixture
def world(tmp_path, monkeypatch):
    home, project = tmp_path / "home", tmp_path / "project" / "nested"
    (home / ".config/twinsmith").mkdir(parents=True)
    project.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TWINSMITH_PHYSICS_BACKEND", raising=False)
    monkeypatch.chdir(project)
    return {"home": home, "project": project, "user": home / ".config/twinsmith/config.toml",
            "project_file": tmp_path / "project" / "twinsmith.toml"}


def write(path, backend):
    path.write_text(f'[sim]\nphysics_backend = "{backend}"\n')


def test_the_default_is_written_in_one_place_and_this_pins_its_current_value():
    assert config.DEFAULT_PHYSICS_BACKEND == "physx"      # the one pin; a later change flips this line and that constant
    assert config.DEFAULT_PHYSICS_BACKEND in config.PHYSICS_BACKENDS


def test_no_launch_path_has_a_default_of_its_own():
    sources = {name: (REPO / name).read_text() for name in ("run.sh", "scripts/run_kit.py", "mcp/twinsmith/sim_sessions.py",
                                                            "mcp/twinsmith/server.py")}
    assert not re.search(r":-\s*physx|PHYSICS_BACKEND=\"physx\"|getenv\([^)]*['\"]physx['\"]\)", sources["run.sh"] + sources["scripts/run_kit.py"])
    assert "'physx'" not in sources["scripts/run_kit.py"].replace("'physx' ", "").split("twinsmith import config")[1].split("if backend == 'newton'")[0]
    assert not re.search(r"physics_backend[^\n]*=\s*[\"']physx[\"']\s*\)", sources["mcp/twinsmith/sim_sessions.py"])
    for text in sources.values():
        assert not re.search(r"DEFAULT_PHYSICS_BACKEND\s*=", text)
    assert len(re.findall(r"DEFAULT_PHYSICS_BACKEND\s*=", (REPO / "mcp/twinsmith/config.py").read_text())) == 1


def test_precedence_and_where_the_value_came_from(world):
    default = config.DEFAULT_PHYSICS_BACKEND
    assert config.resolve_physics_backend({}) == (default, "built-in")
    write(world["user"], OTHER[default])
    assert config.resolve_physics_backend({}) == (OTHER[default], "user file")
    write(world["project_file"], default)                                   # found by walking up from the working directory
    assert config.resolve_physics_backend({}) == (default, "project file")
    assert config.resolve_physics_backend({"TWINSMITH_PHYSICS_BACKEND": OTHER[default]}) == (OTHER[default], "environment")
    assert config.resolve_physics_backend({"TWINSMITH_PHYSICS_BACKEND": ""}) == (default, "project file")   # empty is unset
    resolved = config.resolve({}, cwd=world["project"], home=world["home"])
    assert (resolved.physics_backend, resolved.physics_backend_source) == (default, "project file")


def test_a_bad_value_names_its_source(world):
    write(world["user"], "bullet")
    with pytest.raises(ValueError, match=r"physx or newton, not 'bullet' \(from the user file\)"):
        config.resolve_physics_backend({})
    with pytest.raises(ValueError, match=r"not 'bullet' \(from the environment\)"):
        config.resolve_physics_backend({"TWINSMITH_PHYSICS_BACKEND": "bullet"})


def run_helper(args, env_extra=None, cwd=None):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO / "mcp"), **(env_extra or {})}
    return subprocess.run([sys.executable, "-m", "twinsmith.config", *args], capture_output=True, text=True, env=env, cwd=cwd, timeout=30)


def test_the_helper_prints_the_setting_from_the_same_files_and_needs_no_sdk(world):
    default = config.DEFAULT_PHYSICS_BACKEND
    env = {"HOME": str(world["home"])}
    assert run_helper(["--print", "physics_backend"], env, world["project"]).stdout == default + "\n"
    write(world["user"], OTHER[default])
    out = run_helper(["--print", "physics_backend_source"], env, world["project"])
    assert out.stdout == "user file\n"
    assert run_helper(["--print", "physics_backend"], {**env, "TWINSMITH_PHYSICS_BACKEND": default}, world["project"]).stdout == default + "\n"
    bad = run_helper(["--print", "physics_backend"], {**env, "TWINSMITH_PHYSICS_BACKEND": "bullet"}, world["project"])
    assert bad.returncode == 2 and bad.stdout == "" and "not 'bullet'" in bad.stderr
    # imported with the MCP SDK made unimportable, as on a host that has only the standard library
    code = ("import sys; sys.modules['mcp'] = None; sys.path.insert(0, %r); from twinsmith import config; "
            "print(config.resolve_physics_backend()[0])" % str(REPO / "mcp"))
    check = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={"HOME": str(world["home"])}, cwd=world["project"])
    assert check.returncode == 0 and check.stdout.strip() == OTHER[default], check.stderr


def launcher(tmp_path, with_newton):
    """A fake Isaac Sim interpreter: it answers the probes run.sh makes and then prints what it was started with."""
    site = tmp_path / "app" / "python_packages" / "isaacsim"
    site.mkdir(parents=True, exist_ok=True)
    if with_newton:
        (site / "exts" / "isaacsim.physics.newton").mkdir(parents=True, exist_ok=True)
    python = tmp_path / "python"
    python.write_text(f'#!/bin/sh\ncase "$2" in *dirname*) echo "{site}";; esac\n[ "$1" = "-c" ] && exit 0\n'
                      'echo "started backend=$TWINSMITH_PHYSICS_BACKEND"; exit 0\n')
    python.chmod(0o755)
    return python


def run_sh(tmp_path, world, *args, with_newton=True, env=None):
    environ = {"TWINSMITH_SIM_PYTHON": str(launcher(tmp_path, with_newton)), "PATH": "/usr/bin:/bin", "HOME": str(world["home"]), **(env or {})}
    return subprocess.run(["bash", str(REPO / "run.sh"), "--headless", "--port", PORT, *args], env=environ, cwd=world["project"],
                          capture_output=True, text=True, timeout=60)


def test_run_sh_takes_the_flag_then_the_environment_then_the_files_then_the_built_in_default(tmp_path, world):
    default = config.DEFAULT_PHYSICS_BACKEND
    other = OTHER[default]
    assert f"started backend={default}" in run_sh(tmp_path, world).stdout
    write(world["user"], other)
    assert f"started backend={other}" in run_sh(tmp_path, world).stdout
    write(world["project_file"], default)
    assert f"started backend={default}" in run_sh(tmp_path, world).stdout
    assert f"started backend={other}" in run_sh(tmp_path, world, env={"TWINSMITH_PHYSICS_BACKEND": other}).stdout
    assert f"started backend={default}" in run_sh(tmp_path, world, "--physics-backend", default, env={"TWINSMITH_PHYSICS_BACKEND": other}).stdout


def test_run_sh_with_a_configured_newton_and_no_newton_in_the_build_starts_nothing(tmp_path, world):
    write(world["user"], "newton")
    result = run_sh(tmp_path, world, with_newton=False)
    assert result.returncode == 2 and "started" not in result.stdout
    assert "no isaacsim.physics.newton" in result.stderr and "no PhysX fallback" in result.stderr
    # an explicit flag for the other engine is honoured, not overridden by the file
    assert "started backend=physx" in run_sh(tmp_path, world, "--physics-backend", "physx", with_newton=False).stdout


def test_run_sh_with_an_unreadable_configured_value_starts_nothing(tmp_path, world):
    write(world["user"], "bullet")
    result = run_sh(tmp_path, world)
    assert result.returncode == 2 and "started" not in result.stdout
    assert "not 'bullet'" in result.stderr and "nothing was started" in result.stderr


def test_run_sh_help_does_not_state_a_default_of_its_own():
    out = subprocess.run(["bash", str(REPO / "run.sh"), "--help"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}, timeout=30).stdout
    assert "the configured physics_backend" in out and "default: physx" not in out


class Bridge:
    def __init__(self, engine):
        self.engine = engine

    def call(self, name, params=None, **kwargs):
        if name == "status":
            return {"pid": 1, "port": 8766, "namespaces": ["sim"], "extension": {}}
        if name == "sim.stage.info":
            return {"session_id": "s", "stage_id": "t", "stopped": True,
                    "runtime": {"physics_engine": self.engine} if self.engine else {}}
        raise AssertionError(name)

    def close(self):
        pass


def test_status_reports_the_active_engine_the_configured_default_and_its_source(world):
    default = config.DEFAULT_PHYSICS_BACKEND
    instance = server.build_server({})
    state = server._state_for(instance)
    state.client = Bridge(OTHER[default])
    physics = json.loads(server._dispatch("status", {}, state))["physics"]
    assert physics["active"] == OTHER[default] and physics["default"] == default and physics["default_from"] == "built-in"
    assert "applies to new sessions only" in physics["note"] and "never changed" in physics["note"]
    detail = json.loads(server._dispatch("status", {"detail": True}, state))["physics"]
    assert detail == physics
    state.client = Bridge(default)
    assert "note" not in json.loads(server._dispatch("status", {}, state))["physics"]


def test_status_with_no_bridge_still_gives_the_default_and_where_it_came_from(world, monkeypatch):
    write(world["user"], "newton")
    instance = server.build_server({})
    state = server._state_for(instance)
    monkeypatch.setattr(state, "get_client", lambda: (_ for _ in ()).throw(ConnectionRefusedError("Connection refused")))
    monkeypatch.setattr(server, "_surroundings", lambda state: {"bridges": []})
    physics = json.loads(server._dispatch("status", {}, state))["physics"]
    assert physics == {"active": None, "default": "newton", "default_from": "user file"}
    assert "physics_engine" not in physics


def test_status_comes_from_the_environment_when_that_wins(world):
    default = config.DEFAULT_PHYSICS_BACKEND
    write(world["user"], default)
    instance = server.build_server({"TWINSMITH_PHYSICS_BACKEND": OTHER[default]})
    state = server._state_for(instance)
    assert (state.config.physics_backend, state.config.physics_backend_source) == (OTHER[default], "environment")
    assert server._physics_status(state)["default_from"] == "environment"


def test_without_a_toml_reader_the_physics_setting_is_still_read_and_anything_odd_is_an_error(world, monkeypatch):
    """A Python before 3.11 with no tomli (run.sh's own python3 may be one) reads only [sim] physics_backend."""
    monkeypatch.setattr(config, "tomllib", None)
    default = config.DEFAULT_PHYSICS_BACKEND
    world["user"].write_text('[tools]\ntoolsets = ["minimal"]\n\n[sim]  # engine\nphysics_backend = "%s"  # for new sessions\n' % OTHER[default])
    assert config.resolve_physics_backend({}) == (OTHER[default], "user file")
    write(world["project_file"], default)
    assert config.resolve_physics_backend({}) == (default, "project file")
    world["project_file"].write_text('sim = { physics_backend = "%s" }\n' % OTHER[default])
    with pytest.raises(ValueError, match="Cannot read physics_backend .* without a TOML reader"):
        config.resolve_physics_backend({})
    assert config.resolve_physics_backend({"TWINSMITH_PHYSICS_BACKEND": default}) == (default, "environment")   # the way out
    world["project_file"].write_text('[tools]\ntoolsets = ["minimal"]\n')           # no physics setting: not an error
    assert config.resolve_physics_backend({}) == (OTHER[default], "user file")
    with pytest.raises(ValueError, match="needs Python 3.11 or the tomli package"):
        config.resolve({}, cwd=world["project"], home=world["home"])                # the full configuration needs a reader

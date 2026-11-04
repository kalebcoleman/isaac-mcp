"""Defects the PR 70 test fleet found in host scripts, twin handlers and the Kit UI helpers."""

import ast
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ isaaclab__tensorboard_tail last_n


def _tail_tool(tmp_path, monkeypatch, last_n):
    from isaac_mcp.isaaclab import metrics
    from isaac_mcp.server import build_server

    seen = {}

    def tail(run_dir, tag, last_n=50):
        seen["last_n"] = last_n
        return {"points": []}

    monkeypatch.setattr(metrics, "tensorboard_tail", tail)
    server = build_server({"ISAAC_MCP_LOCAL_TOOLS": "isaaclab_read"})
    args = {"run_dir": str(tmp_path), "tag": "loss", "last_n": last_n}
    return asyncio.run(server.call_tool("isaaclab__tensorboard_tail", args)), seen


def test_tensorboard_tail_refuses_a_boolean_through_the_tool_layer(tmp_path, monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError

    for value in (True, False):
        with pytest.raises(ToolError, match="not a boolean"):
            _tail_tool(tmp_path, monkeypatch, value)


@pytest.mark.parametrize("value, expected", [(None, None), (3, 3), (0, 0)])
def test_tensorboard_tail_accepts_integers_and_null(tmp_path, monkeypatch, value, expected):
    _, seen = _tail_tool(tmp_path, monkeypatch, value)
    assert seen["last_n"] == expected


# ------------------------------------------------------------------ scripts


def _script(name):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scene_client_keeps_exported_tool_groups_and_still_pins_the_port(monkeypatch):
    client = _script("scene_mcp_client")
    monkeypatch.delenv("ISAAC_MCP_LOCAL_TOOLS", raising=False)
    monkeypatch.delenv("ISAAC_MCP_BUILTINS", raising=False)
    monkeypatch.setenv("UR5E_DT_PORT", "1")
    env = client.parameters(18781).env
    assert env["ISAAC_MCP_LOCAL_TOOLS"] == "gateway,screen_read,screen_capture,screen_control"
    assert env["ISAAC_MCP_BUILTINS"] == "0" and env["UR5E_DT_PORT"] == "18781"
    monkeypatch.setenv("ISAAC_MCP_LOCAL_TOOLS", "all,screen_input")
    assert client.parameters(18781).env["ISAAC_MCP_LOCAL_TOOLS"] == "all,screen_input"


@pytest.mark.parametrize("argv, code", [(["--help"], 0), (["-h"], 0), (["live"], 2), (["typo"], 2)])
def test_smoke_surface_usage_never_reaches_the_server(monkeypatch, capsys, argv, code):
    smoke = _script("smoke_mcp_surface")
    monkeypatch.setattr(smoke, "run", lambda plan: pytest.fail("must not call tools"))
    assert smoke.main(["smoke_mcp_surface.py", *argv]) == code
    captured = capsys.readouterr()
    assert "offline" in captured.out + captured.err


# ------------------------------------------------------------------ twin handlers


def _twin_function(name):
    tree = ast.parse((ROOT / "exts/ur5e-dt/ur5e_dt/extension.py").read_text())
    return next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


class AssetMissing(FileNotFoundError):
    pass


class AssetCorrupt(FileNotFoundError):
    pass


def _twin_namespace(*names):
    module = ast.fix_missing_locations(ast.Module(body=[_twin_function(n) for n in names], type_ignores=[]))
    namespace = {"Dict": dict, "Any": object, "traceback": traceback, "AssetMissing": AssetMissing,
                 "AssetCorrupt": AssetCorrupt, "carb": SimpleNamespace(log_error=lambda *_: None)}
    exec(compile(module, "extension.py", "exec"), namespace)
    return namespace


def test_load_workstation_message_does_not_claim_vention_for_a_placeholder():
    ns = _twin_namespace("_cmd_load_workstation")

    async def load():
        return None

    for label in ("a placeholder bench (no Vention workstation asset)", "the Vention workstation"):
        owner = SimpleNamespace(load_workstation=load, _bench_label=lambda label=label: label)
        result = asyncio.run(ns["_cmd_load_workstation"](owner))
        assert result["status"] == "success" and result["message"].startswith(f"Loaded {label} at /World/Vention")


@pytest.mark.parametrize("handler, method", [
    ("_cmd_load_workstation", "load_workstation"),
    ("_cmd_quick_start_with_workstation", "quick_start_with_workstation"),
    ("_cmd_load_simple_room", "load_simple_room"),
])
def test_asset_refusals_carry_no_traceback_but_bugs_do(handler, method):
    ns = _twin_namespace(handler, "_failure_detail")

    def result_for(exc):
        async def fail():
            raise exc

        return asyncio.run(ns[handler](SimpleNamespace(**{method: fail})))

    refused = result_for(AssetMissing("add it with isaac_assets.py"))
    assert refused["status"] == "error" and "isaac_assets.py" in refused["message"] and "traceback" not in refused
    assert "AssetCorrupt" in result_for(AssetCorrupt("hash differs"))["traceback"]
    assert "ZeroDivisionError" in result_for(ZeroDivisionError("bug"))["traceback"]


# ------------------------------------------------------------------ Kit UI helpers


class FakeWindow:
    def __init__(self, title):
        self.title, self.visible, self.docked = title, True, False
        self.position_x, self.position_y, self.width, self.height = 0, 0, 500, 500

    def is_selected_in_dock(self):
        return True


class Queryable(FakeWindow):
    pass


@pytest.fixture
def kit_ui(monkeypatch):
    """ui.py with a fake omni.ui and a ui_test that records every path Kit would be asked for."""
    spec = importlib.util.spec_from_file_location("sim_tools_ui_fleet", ROOT / "exts/sim-tools/sim_tools/ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    workspace = SimpleNamespace(get_dpi_scale=lambda: 1.0, get_windows=lambda: fake.windows,
                                get_window=lambda title: next((w for w in fake.windows if w.title == title), None))
    fake = ModuleType("omni.ui")
    fake.windows = [Queryable("Stage"), FakeWindow("Status Bar")]
    fake.Workspace, fake.Window = workspace, Queryable
    fake.Inspector = SimpleNamespace(get_children=lambda widget: [])
    omni = ModuleType("omni")
    omni.ui = fake
    monkeypatch.setitem(sys.modules, "omni", omni)
    monkeypatch.setitem(sys.modules, "omni.ui", fake)
    queries = []

    class Button:
        visible, enabled = True, True
        identifier, name, text = "b", "", ""

    target = SimpleNamespace(widget=Button(), center=SimpleNamespace(x=10, y=10), size=SimpleNamespace(x=5, y=5))
    queried = SimpleNamespace(find=lambda path: (queries.append(path), target)[1],
                              find_all=lambda path: (queries.append(path), [])[1])
    monkeypatch.setattr(module, "_ui_test", lambda: queried)
    return SimpleNamespace(module=module, queries=queries, target=target, button=Button)


def test_ancestor_paths_are_only_ones_kit_can_query():
    spec = importlib.util.spec_from_file_location("sim_tools_ui_paths", ROOT / "exts/sim-tools/sim_tools/ui.py")
    ui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ui)
    assert ui._ancestors("Stage//Frame/VStack[0]/HStack[1]/Button[2]") == [
        "Stage//Frame/VStack[0]", "Stage//Frame/VStack[0]/HStack[1]"]
    assert ui._ancestors("Stage//Frame/Button[0]") == []
    assert ui._ancestors("A/B Window//Frame/VStack[0]/Label[0]") == ["A/B Window//Frame/VStack[0]"]
    assert ui._ancestors("no-window") == []


def _assert_no_bare_prefixes(queries):
    for path in queries:
        title, sep, rest = path.partition("//")
        assert sep and rest.startswith("Frame/"), f"Kit logs a warning for {path!r}"


def test_a_ref_action_queries_no_path_that_makes_kit_warn(kit_ui):
    path = "Stage//Frame/VStack[0]/HStack[1]/Button[2]"
    kit_ui.module._resolve({"ref": f"{path}|Button:b"})
    assert path in kit_ui.queries
    _assert_no_bare_prefixes(kit_ui.queries)


def test_a_disabled_container_still_disables_its_child(kit_ui):
    path = "Stage//Frame/VStack[0]/Button[2]"
    disabled = SimpleNamespace(widget=SimpleNamespace(enabled=False))
    kit_ui.module._ui_test().find = lambda p: kit_ui.target if p == path else disabled
    assert kit_ui.module._resolve({"ref": f"{path}|Button:b"})[2]["enabled"] is False


def test_label_context_walks_only_queryable_parents(kit_ui):
    assert kit_ui.module._context("Stage//Frame/VStack[0]/HStack[1]/Field[0]", kit_ui.module._ui_test()) == ""
    assert kit_ui.queries == ["Stage//Frame/VStack[0]/HStack[1]", "Stage//Frame/VStack[0]"]


def test_find_skips_windows_kit_cannot_query(kit_ui):
    assert list(kit_ui.module._candidates(None, False, "")) == []
    assert kit_ui.queries == ["Stage//Frame/**/*"]


def test_hidden_ref_says_an_inline_editor_may_have_replaced_it(kit_ui):
    kit_ui.target.widget.visible = False
    with pytest.raises(kit_ui.module.UIRefusal) as err:
        kit_ui.module._resolve({"ref": "Stage//Frame/VStack[0]/Button[2]|Button:b"})
    assert err.value.code == "STALE_WIDGET"
    assert "inline editor" in str(err.value) and "sim__ui__find" in str(err.value)


# ------------------------------------------------------------------ bundled ROS libraries


def _ros_lib_dir(site, distro="humble"):
    script = f'. "{ROOT}/scripts/isaac_ros_libs.sh"; isaac_ros_lib_dir "{site}" "{distro}"'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


@pytest.mark.parametrize("site, lib", [
    ("venv/isaacsim", "venv/isaacsim/exts/isaacsim.ros2.core/humble/lib"),  # pip 6.x
    ("venv/isaacsim", "venv/isaacsim/exts/isaacsim.ros2.bridge/humble/lib"),  # pip 5.1
    ("app/python_packages/isaacsim", "app/exts/isaacsim.ros2.core/humble/lib"),  # standalone 6.x
    ("app/python_packages/isaacsim", "app/exts/isaacsim.ros2.bridge/humble/lib"),  # standalone 5.1
])
def test_ros_libraries_are_found_in_every_layout(tmp_path, site, lib):
    (tmp_path / site).mkdir(parents=True)
    (tmp_path / lib).mkdir(parents=True)
    found = _ros_lib_dir(tmp_path / site)
    assert found.returncode == 0 and found.stdout.strip() == str(tmp_path / lib)


def test_missing_ros_libraries_are_a_failure_not_a_bogus_path(tmp_path):
    (tmp_path / "exts/isaacsim.ros2.core/jazzy/lib").mkdir(parents=True)
    found = _ros_lib_dir(tmp_path, "humble")
    assert found.returncode == 1 and found.stdout == ""


def _run_sh_with_app(tmp_path, *lib):
    site = tmp_path / "app/python_packages/isaacsim"
    site.mkdir(parents=True)
    if lib:
        (tmp_path / lib[0]).mkdir(parents=True)
    python = tmp_path / "python.sh"
    python.write_text(f'#!/bin/sh\ncase "$1" in -c) case "$2" in *__file__*) echo {site};; esac; exit 0;; esac\n'
                      'echo "LD=$LD_LIBRARY_PATH"\n')
    python.chmod(0o755)
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "ISAAC_MCP_PYTHON": str(python), "ROS_DISTRO": "humble"}
    return subprocess.run(["bash", str(ROOT / "run.sh"), "--headless"], capture_output=True, text=True, env=env)


def test_run_sh_exports_the_standalone_app_ros_libraries(tmp_path):
    result = _run_sh_with_app(tmp_path, "app/exts/isaacsim.ros2.core/humble/lib")
    assert result.returncode == 0, result.stderr
    assert f"LD={tmp_path}/app/exts/isaacsim.ros2.core/humble/lib" in result.stdout


def test_run_sh_warns_instead_of_exporting_a_missing_ros_folder(tmp_path):
    result = _run_sh_with_app(tmp_path)
    assert "no ROS 2 humble libraries" in result.stderr
    assert "LD=\n" in result.stdout


def test_no_launcher_still_assumes_the_pip_layout():
    for name in ("run.sh", "scripts/probe_62.sh", "scripts/verify_tf.sh", "scripts/check_machine.sh"):
        text = (ROOT / name).read_text()
        assert "isaac_ros_libs.sh" in text and "/exts/isaacsim.ros2." not in text, name


# ------------------------------------------------------------------ Isaac Lab jobs


@pytest.fixture
def job_starts(monkeypatch, tmp_path):
    from isaac_mcp.isaaclab import jobs

    checkpoint = tmp_path / "IsaacLab" / "logs" / "rl_games" / "reach" / "run" / "nn" / "model.pth"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"x")
    calls = []

    def start(python, root, kind, task, argv, **kwargs):
        calls.append(argv)
        return dict(job_id="j")

    monkeypatch.setattr(jobs, "_start_job", start)
    return SimpleNamespace(jobs=jobs, checkpoint=str(checkpoint), root=tmp_path / "IsaacLab", calls=calls)


@pytest.mark.parametrize("launch", ["eval_", "play"])
def test_checkpoint_under_logs_selects_its_library_and_says_so(job_starts, launch):
    run = getattr(job_starts.jobs, launch)
    result = run("py", job_starts.root, "Isaac-Reach-Franka", job_starts.checkpoint)
    assert result["rl_library_from_checkpoint_path"] == "rl_games"
    assert job_starts.calls[-1][job_starts.calls[-1].index("--rl_library") + 1] == "rl_games"


@pytest.mark.parametrize("launch", ["eval_", "play"])
def test_an_explicit_library_is_never_overridden(job_starts, launch):
    run = getattr(job_starts.jobs, launch)
    result = run("py", job_starts.root, "T", job_starts.checkpoint, rl_library="skrl")
    assert "rl_library_from_checkpoint_path" not in result
    assert job_starts.calls[-1].count("--rl_library") == 1 and "skrl" in job_starts.calls[-1]
    result = run("py", job_starts.root, "T", job_starts.checkpoint, extra_args=["--rl_library=skrl"])
    assert "rl_library_from_checkpoint_path" not in result and "--rl_library" not in job_starts.calls[-1]


def test_an_unrecognized_checkpoint_path_leaves_the_choice_to_lab(tmp_path):
    from isaac_mcp.isaaclab.jobs import _library_from_checkpoint

    assert _library_from_checkpoint("/a/weights/model.pth", []) is None
    assert _library_from_checkpoint("/a/logs/custom/model.pth", []) is None


JOB_ID = "20261103-193322-Isaac-Reach-Franka"


def _job_with_logs(monkeypatch, tmp_path, status, stdout="", stderr="boom\n"):
    from isaac_mcp.isaaclab import jobs

    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path))
    folder = tmp_path / JOB_ID
    folder.mkdir()
    (folder / "stdout.log").write_text(stdout)
    (folder / "stderr.log").write_text(stderr)
    (folder / "job.json").write_text(json.dumps(dict(
        job_id=JOB_ID, status=status, log_dir=str(folder), started_at=1.0, finished_at=2.0, exit_code=1, pid=None)))
    return jobs


def test_a_failed_job_shows_stderr_in_its_status(monkeypatch, tmp_path):
    jobs = _job_with_logs(monkeypatch, tmp_path, "failed")
    status = jobs.runs_status(JOB_ID)
    assert status["last_log_lines"] == [] and status["last_stderr_lines"] == ["boom"]


def test_a_finished_job_status_stays_as_it_was(monkeypatch, tmp_path):
    jobs = _job_with_logs(monkeypatch, tmp_path, "finished", stdout="ok\n")
    status = jobs.runs_status(JOB_ID)
    assert status["last_log_lines"] == ["ok"] and "last_stderr_lines" not in status


def test_runs_list_refuses_an_unknown_state(monkeypatch, tmp_path):
    jobs = _job_with_logs(monkeypatch, tmp_path, "finished")
    with pytest.raises(ValueError, match="running, finished, all"):
        jobs.runs_list("bogus")
    for state in (None, "all", "finished"):
        assert [r["job_id"] for r in jobs.runs_list(state)["runs"]] == [JOB_ID]
    assert jobs.runs_list("running")["runs"] == []


def test_the_bridge_template_updates_kit_itself_without_advancing_physics():
    tree = ast.parse((ROOT / "mcp/examples/isaaclab_bridge_launch.py").read_text())
    loop = next(n for n in tree.body if isinstance(n, ast.While))
    calls = [ast.unparse(c.value) for c in loop.body if isinstance(c, ast.Expr) and isinstance(c.value, ast.Call)]
    assert calls == ["sim.step()", "_settings.set('/app/player/playSimulations', False)", "simulation_app.update()",
                     "_settings.set('/app/player/playSimulations', True)"]


# ------------------------------------------------------------------ scene validators


def _validator(name):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        return _script(name)
    finally:
        sys.path.pop(0)


def test_sensor_validator_waits_on_simulated_time_with_a_bound(monkeypatch):
    sensors = _validator("validate_scene_sensors")
    times = iter([0.2, 0.6, 3.1])

    async def invoke(name, **args):
        assert name == "sim__timeline__control" and args["action"] == "play"
        return dict(time_s=next(times))

    assert asyncio.run(sensors.wait_simulated(invoke, {}, 3, poll=0))["time_s"] == 3.1

    async def stuck(name, **args):
        return dict(time_s=0.1)

    with pytest.raises(AssertionError, match="only 0.1 s simulated"):
        asyncio.run(sensors.wait_simulated(stuck, {}, 3, wall_limit=0, poll=0))


def test_equivalents_validator_names_the_missing_root():
    equivalents = _validator("validate_scene_equivalents")
    equivalents.require_root({"roots": [{"root_id": "agilebot"}]}, "agilebot")
    with pytest.raises(SystemExit, match="VENDORING.md"):
        equivalents.require_root({"roots": [{"root_id": "fixture"}]}, "agilebot")


# ------------------------------------------------------------------ OmniGraph metadata names


def _compat():
    spec = importlib.util.spec_from_file_location("sim_tools_compat_fleet", ROOT / "exts/sim-tools/sim_tools/compat.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _omni(monkeypatch, tools_keys, core_keys):
    graph, core, tools, ogn, omni = (ModuleType(n) for n in
                                     ("omni.graph", "omni.graph.core", "omni.graph.tools", "omni.graph.tools.ogn", "omni"))
    core.MetadataKeys = core_keys
    if tools_keys is not None:
        ogn.MetadataKeys = tools_keys
    omni.graph, graph.core, graph.tools, tools.ogn = graph, core, tools, ogn
    for name, module in (("omni", omni), ("omni.graph", graph), ("omni.graph.core", core),
                         ("omni.graph.tools", tools), ("omni.graph.tools.ogn", ogn)):
        monkeypatch.setitem(sys.modules, name, module)


def test_metadata_keys_prefers_the_current_home_and_falls_back(monkeypatch):
    compat = _compat()
    _omni(monkeypatch, "new", "old")
    assert compat.metadata_keys() == "new"
    _omni(monkeypatch, None, "old")
    assert compat.metadata_keys() == "old"


def test_graph_tools_do_not_use_the_deprecated_metadata_name():
    for name in ("graph.py", "graph_build.py"):
        assert "og.MetadataKeys" not in (ROOT / "exts/sim-tools/sim_tools" / name).read_text()


# ------------------------------------------------------------------ viewport grid in captures


class FakeSettings:
    def __init__(self, **values):
        self.values, self.writes = {"/app/viewport/grid/enabled": True, **values}, []

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.writes.append(value)
        self.values[key] = value


def _capture_module(monkeypatch, settings):
    monkeypatch.syspath_prepend(str(ROOT / "exts/sim-tools"))
    from sim_tools import camera_capture

    carb_settings = ModuleType("carb.settings")
    carb_settings.get_settings = lambda: settings
    carb = ModuleType("carb")
    carb.settings = carb_settings
    monkeypatch.setitem(sys.modules, "carb", carb)
    monkeypatch.setitem(sys.modules, "carb.settings", carb_settings)
    return camera_capture


def _during(module, playing, body=lambda: None):
    frames = []

    async def next_update():
        frames.append(1)

    async def run():
        async with module.without_viewport_grid(playing, next_update):
            body()

    asyncio.run(run())
    return frames


GRID = "/app/viewport/grid/enabled"


def test_stopped_capture_hides_the_grid_first_and_only_during_the_capture(monkeypatch):
    settings = FakeSettings()
    module = _capture_module(monkeypatch, settings)
    seen = []
    frames = _during(module, False, lambda: seen.append(settings.values[GRID]))
    assert seen == [False] and settings.values[GRID] is True
    assert len(frames) == module.GRID_SETTLE_FRAMES, "a frame drawn before the change must not be read back"


def test_the_grid_comes_back_when_a_capture_fails(monkeypatch):
    settings = FakeSettings()

    def fail():
        raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        _during(_capture_module(monkeypatch, settings), False, fail)
    assert settings.values[GRID] is True


def test_a_playing_timeline_or_a_grid_a_person_turned_off_is_left_alone(monkeypatch):
    settings = FakeSettings()
    assert _during(_capture_module(monkeypatch, settings), True) == [] and settings.writes == []
    settings = FakeSettings(**{GRID: False})
    assert _during(_capture_module(monkeypatch, settings), False) == []
    assert settings.writes == [] and settings.values[GRID] is False


def test_a_build_without_the_setting_is_untouched(monkeypatch):
    settings = FakeSettings()
    del settings.values[GRID]
    assert _during(_capture_module(monkeypatch, settings), False) == [] and settings.writes == []


# ------------------------------------------------------------------ Isaac Lab script layouts

# The flags of Isaac Lab 2.3.2's rsl_rl scripts, strictly parsed so a flag it lacks fails here instead of reaching Hydra.
STAND_IN_2_3 = """import argparse, json, os, sys
p = argparse.ArgumentParser()
p.add_argument("--task")
p.add_argument("--num_envs")
p.add_argument("--seed")
p.add_argument("--max_iterations")
p.add_argument("--checkpoint")
p.add_argument("--device")
p.add_argument("--headless", action="store_true")
p.add_argument("--video", action="store_true")
args = p.parse_args()
print(json.dumps(dict(script=sys.argv[0], args=vars(args), HEADLESS=os.environ.get("HEADLESS"))))
"""


def _lab_2_3(tmp_path):
    root = tmp_path / "IsaacLab23"
    scripts = root / "scripts/reinforcement_learning"
    for library in ("rl_games", "rsl_rl", "sb3", "skrl"):
        (scripts / library).mkdir(parents=True)
        (scripts / library / "train.py").write_text(STAND_IN_2_3)
        (scripts / library / "play.py").write_text(STAND_IN_2_3)
    (scripts / "ray").mkdir()  # 2.3.2 has this folder, without train.py or play.py
    (scripts / "ray/tuner.py").write_text("")
    (root / "VERSION").write_text("2.3.2\n")
    return root


def _lab_3(tmp_path):
    root = tmp_path / "IsaacLab3"
    (root / "scripts/reinforcement_learning").mkdir(parents=True)
    for name in ("train.py", "play.py"):
        (root / "scripts/reinforcement_learning" / name).write_text("")
    return root


def test_layout_is_read_from_the_files(tmp_path):
    from isaac_mcp.isaaclab.install import lab_profile

    lab3 = lab_profile(_lab_3(tmp_path))
    assert lab3["layout"] == "unified" and lab3["libraries"] == [] and lab3["scripts"][""]["train"]["flags"] is None
    lab23 = lab_profile(_lab_2_3(tmp_path))
    assert lab23["layout"] == "per_library" and lab23["libraries"] == ["rl_games", "rsl_rl", "sb3", "skrl"]
    assert lab23["lab_version"] == "2.3.2" and "--headless" in lab23["scripts"]["rsl_rl"]["play"]["flags"]
    assert "--viz" not in lab23["scripts"]["rsl_rl"]["train"]["flags"]
    assert lab_profile(tmp_path / "nothing")["layout"] == "none" and lab_profile(None)["layout"] == "none"


def test_flags_include_helpers_and_app_launcher_and_are_unknown_when_a_source_is_missing(tmp_path):
    from isaac_mcp.isaaclab.install import lab_profile

    root = tmp_path / "lab"
    folder = root / "scripts/reinforcement_learning/rsl_rl"
    folder.mkdir(parents=True)
    script = "import cli_args\nAppLauncher.add_app_launcher_args(parser)\nparser.add_argument(\n    '--task')\n"
    (folder / "train.py").write_text(script)
    (folder / "play.py").write_text(script)
    (folder / "cli_args.py").write_text('arg_group.add_argument("--resume")\n')
    assert lab_profile(root)["scripts"]["rsl_rl"]["train"]["flags"] is None, "AppLauncher's source is absent"
    launcher = root / "source/isaaclab/isaaclab/app"
    launcher.mkdir(parents=True)
    (launcher / "app_launcher.py").write_text('group.add_argument(\n  "--headless",\n)\ngroup.add_argument("--device")\n')
    (folder / "extra").mkdir()  # a new stamp on the scripts folder, so the profile is read again
    root2 = root.parent / "lab2"
    root.rename(root2)
    assert lab_profile(root2)["scripts"]["rsl_rl"]["train"]["flags"] == ["--device", "--headless", "--resume", "--task"]


def test_a_changed_checkout_is_read_again(tmp_path):
    from isaac_mcp.isaaclab.install import lab_profile

    root = tmp_path / "lab"
    (root / "scripts/reinforcement_learning/rsl_rl").mkdir(parents=True)
    assert lab_profile(root)["layout"] == "none"
    folder = root / "scripts/reinforcement_learning/skrl"
    folder.mkdir()
    (folder / "train.py").write_text("")
    (folder / "play.py").write_text("")
    os.utime(folder.parent, ns=(1, 1))
    assert lab_profile(root)["libraries"] == ["skrl"]


@pytest.fixture
def captured_jobs(monkeypatch):
    from isaac_mcp.isaaclab import jobs

    calls = []

    def start(python, root, kind, task, argv, **kwargs):
        calls.append((kind, argv, kwargs))
        return dict(job_id="j")

    monkeypatch.setattr(jobs, "_start_job", start)
    return SimpleNamespace(jobs=jobs, calls=calls)


def test_lab_3_argv_is_byte_identical(captured_jobs, tmp_path):
    jobs, root = captured_jobs.jobs, _lab_3(tmp_path)
    checkpoint = root / "model.pth"
    checkpoint.write_bytes(b"x")
    jobs.train("py", root, "T", num_envs=8, max_iterations=2, seed=1, device="cuda:0", rl_library="rl_games",
               headless=True, extra_args=["a.b=1"])
    assert captured_jobs.calls[-1][1] == ["--task", "T", "--num_envs", "8", "--rl_library", "rl_games", "--max_iterations", "2",
                                          "--seed", "1", "--device", "cuda:0", "--viz", "none", "a.b=1"]
    assert "library" not in captured_jobs.calls[-1][2]
    jobs.eval_("py", root, "T", str(checkpoint), num_envs=2)
    assert captured_jobs.calls[-1][1] == ["--task", "T", "--checkpoint", str(checkpoint), "--num_envs", "2", "--viz", "none"]
    jobs.play("py", root, "T", str(checkpoint), rl_library="skrl", extra_args=["--video"])
    assert captured_jobs.calls[-1][1] == ["--task", "T", "--checkpoint", str(checkpoint), "--num_envs", "1",
                                          "--rl_library", "skrl", "--viz", "kit", "--video"]
    assert "library" not in captured_jobs.calls[-1][2]


def test_lab_2_3_needs_a_library_and_names_the_choices(captured_jobs, tmp_path):
    jobs, root = captured_jobs.jobs, _lab_2_3(tmp_path)
    with pytest.raises(ValueError, match=r"Isaac Lab 2\.3\.2 .*needs rl_library.*rl_games, rsl_rl, sb3, skrl"):
        jobs.train("py", root, "T")
    with pytest.raises(ValueError, match="has no ray scripts"):
        jobs.train("py", root, "T", rl_library="ray")
    assert captured_jobs.calls == []


@pytest.mark.parametrize("flag, alternative", [("--viz=kit", "headless"), ("--visualizer=kit", "headless"),
                                               ("--rl_library=skrl", "rl_library"), ("--nope", "It accepts")])
def test_undeclared_flags_are_refused_naming_the_alternative(captured_jobs, tmp_path, flag, alternative):
    with pytest.raises(ValueError, match=rf"skrl/train\.py does not accept {flag.split('=')[0]}\. .*{alternative}"):
        captured_jobs.jobs.train("py", _lab_2_3(tmp_path), "T", rl_library="skrl", extra_args=[flag])
    assert captured_jobs.calls == []


def test_a_hydra_override_is_not_a_flag(captured_jobs, tmp_path):
    captured_jobs.jobs.train("py", _lab_2_3(tmp_path), "T", rl_library="skrl", extra_args=["agent.x=1"])
    assert "agent.x=1" in captured_jobs.calls[-1][1]


def test_a_script_that_lacks_a_generated_flag_is_refused(captured_jobs, tmp_path):
    root = _lab_2_3(tmp_path)
    (root / "scripts/reinforcement_learning/skrl/train.py").write_text("parser.add_argument('--task')\n")
    with pytest.raises(ValueError, match="does not accept --num_envs"):
        captured_jobs.jobs.train("py", root, "T", rl_library="skrl")


def test_lab_2_3_checkpoint_path_selects_a_folder_that_exists(captured_jobs, tmp_path):
    jobs, root = captured_jobs.jobs, _lab_2_3(tmp_path)
    checkpoint = root / "logs/rsl_rl/reach/run/model.pth"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"x")
    result = jobs.play("py", root, "T", str(checkpoint), headless=True)
    kind, argv, kwargs = captured_jobs.calls[-1]
    assert result["rl_library_from_checkpoint_path"] == "rsl_rl" and kwargs["library"] == "rsl_rl"
    assert "--rl_library" not in argv and "--viz" not in argv and "--headless" in argv
    torchrl = root / "logs/torchrl/m.pth"  # a Lab 3 library, not a folder here
    torchrl.parent.mkdir(parents=True)
    torchrl.write_bytes(b"x")
    with pytest.raises(ValueError, match="needs rl_library"):
        jobs.play("py", root, "T", str(torchrl))


def _run_job(monkeypatch, tmp_path, launch):
    from isaac_mcp.isaaclab import jobs

    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path / "jobs"))
    result = launch(jobs)
    assert "error" not in result, result
    process = jobs._active[result["job_id"]]
    process.wait(timeout=30)
    return Path(result["log_dir"])


@pytest.mark.parametrize("headless, extra, flag, env", [
    (True, [], True, "1"),
    (False, [], False, "0"),
    (True, ["--video"], False, "1"),
])
def test_a_real_process_accepts_the_lab_2_3_argv(monkeypatch, tmp_path, headless, extra, flag, env):
    root = _lab_2_3(tmp_path)
    logs = _run_job(monkeypatch, tmp_path, lambda jobs: jobs.train(
        sys.executable, root, "Isaac-Reach-Franka-v0", num_envs=4, max_iterations=1, seed=7, device="cuda:0",
        rl_library="rsl_rl", headless=headless, extra_args=extra, allow_concurrent=True, allow_concurrent_gpu=True))
    assert (logs / "stderr.log").read_text() == ""
    seen = json.loads((logs / "stdout.log").read_text())
    assert seen["script"].endswith("scripts/reinforcement_learning/rsl_rl/train.py")
    assert seen["args"]["headless"] is flag and seen["HEADLESS"] == env
    assert seen["args"]["task"] == "Isaac-Reach-Franka-v0" and seen["args"]["max_iterations"] == "1"


def test_a_missing_script_is_refused_before_popen(monkeypatch, tmp_path):
    from isaac_mcp.isaaclab import jobs

    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start"))
    root = tmp_path / "empty"
    root.mkdir()
    (root / "VERSION").write_text("9.9.9\n")
    with pytest.raises(ValueError, match=r"Isaac Lab 9\.9\.9 .*has no scripts/reinforcement_learning/train\.py.*Expected"):
        jobs.train("py", root, "T", allow_concurrent=True, allow_concurrent_gpu=True)


def test_lab_2_3_log_lines_still_find_the_run_directory(tmp_path):
    from isaac_mcp.isaaclab.jobs import _run_dir_from_log

    log = tmp_path / "stdout.log"
    log.write_text("[INFO] Logging experiment in directory: /x/logs/rsl_rl/reach\n"
                   "Exact experiment name requested from command line: 2026-01-01_00-00-00\n")
    assert _run_dir_from_log(log) == "/x/logs/rsl_rl/reach/2026-01-01_00-00-00"


def _install_probe(monkeypatch, tmp_path, root, output):
    from isaac_mcp.isaaclab import install

    python = tmp_path / "python"
    python.write_text("")
    monkeypatch.setattr(install, "locate_install", lambda env=None: dict(python=str(python), isaaclab_root=str(root), venv=None))
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=json.dumps(output), stderr=""))
    return install


def test_install_info_reports_what_it_checked(monkeypatch, tmp_path):
    install = _install_probe(monkeypatch, tmp_path, _lab_2_3(tmp_path), dict(isaaclab="2.3.2", isaacsim="5.1.0"))
    info = install.install_info()
    profile = info["profile"]
    assert profile["layout"] == "per_library" and profile["libraries"] == ["rl_games", "rsl_rl", "sb3", "skrl"]
    assert profile["isaacsim_version"] == "5.1.0" and "no job was started" in profile["checked"]
    assert info["tested"] is False, "the checkout looking usable does not make the pair verified"
    install = _install_probe(monkeypatch, tmp_path, tmp_path / "missing", dict(isaaclab="3.0.0", isaacsim="6.2.0.0"))
    info = install.install_info()
    assert info["profile"]["layout"] == "none" and "cannot start" in info["jobs"] and info["tested"] is True
    install = _install_probe(monkeypatch, tmp_path, tmp_path, dict(isaaclab="3.0.0", isaacsim="6.0.1.0"))
    assert install.install_info()["tested"] is False


def test_envs_list_does_not_present_gymnasium_tasks_as_isaac_tasks(monkeypatch, tmp_path):
    output = dict(envs=[dict(id="CartPole-v1", entry_point="gymnasium.envs.classic_control:CartPoleEnv"),
                        dict(id="My-Cartpole", entry_point="mine:Env")],
                  import_errors=dict(isaaclab_tasks="ImportError: no"), gymnasium_builtin=["CartPole-v1"])
    install = _install_probe(monkeypatch, tmp_path, tmp_path, output)
    result = install.envs_list("cartpole")
    assert [e["id"] for e in result["envs"]] == ["My-Cartpole"] and "could not be listed" in result["isaac_tasks_unavailable"]
    healthy = dict(output, import_errors={})
    install = _install_probe(monkeypatch, tmp_path, tmp_path, healthy)
    result = install.envs_list("cartpole")
    assert len(result["envs"]) == 2 and "isaac_tasks_unavailable" not in result


# ------------------------------------------------------------------ jobs that exit 0 after crashing

# What Isaac Lab 2.3.2 wrote for an unknown task, which raises before Hydra's handling, and then exited 0.
CRASH_STDERR = """Traceback (most recent call last):
  File "<lab>/scripts/reinforcement_learning/rsl_rl/train.py", line 114, in <module>
    @hydra_task_config(args_cli.task, args_cli.agent)
  File "<site>/gymnasium/envs/registration.py", line 369, in _check_name_exists
    raise error.NameNotFound(
gymnasium.error.NameNotFound: Environment `No-Such-Task` doesn't exist.
2026-11-04T04:11:41Z [4,702ms] [Warning] [omni.physx.plugin] USD stage detach not called, holding a loose ptr to a stage!
2026-11-04T04:11:41Z [4,862ms] [Warning] [carb] Recursive unloadAllPlugins() detected!
"""


def _stderr_verdict(tmp_path, text):
    from isaac_mcp.isaaclab.jobs import _uncaught_exception

    path = tmp_path / "stderr.log"
    path.write_text(text)
    return _uncaught_exception(path)


def test_a_stderr_ending_in_an_exception_is_recognized_through_kit_shutdown_lines(tmp_path):
    assert "NameNotFound" in _stderr_verdict(tmp_path, CRASH_STDERR)


@pytest.mark.parametrize("text", [
    "",
    "2026-11-04T04:10:34Z [6,658ms] [Warning] [omni.usd] Unexpected reference count of 2\n",
    "Traceback (most recent call last):\n  File \"x\", line 1\nValueError: recovered\n" + "later output\n" * 20,
])
def test_a_healthy_or_recovered_stderr_is_not_a_crash(tmp_path, text):
    assert _stderr_verdict(tmp_path, text) is None


def _exit_zero_job(monkeypatch, tmp_path, kind_args=None, stderr=CRASH_STDERR, stdout=""):
    from isaac_mcp.isaaclab import jobs

    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("CRASH_STDERR_TEXT", stderr)
    monkeypatch.setenv("CRASH_STDOUT_TEXT", stdout)
    root = _lab_3(tmp_path)
    python = tmp_path / "python"
    python.write_text('#!/bin/sh\nprintf "%s" "$CRASH_STDOUT_TEXT"\nprintf "%s" "$CRASH_STDERR_TEXT" >&2\nexit 0\n')
    python.chmod(0o755)
    result = jobs.train(str(python), root, "T", allow_concurrent=True, allow_concurrent_gpu=True)
    for _ in range(100):
        if jobs.runs_status(result["job_id"])["state"] != "running":
            break
        import time

        time.sleep(0.05)
    return jobs.runs_status(result["job_id"])


def test_exit_zero_after_an_uncaught_exception_is_a_failed_job(monkeypatch, tmp_path):
    status = _exit_zero_job(monkeypatch, tmp_path)
    assert status["state"] == "failed" and status["exit_code"] == 0
    assert "NameNotFound" in status["note"] and "NameNotFound" in "".join(status["last_stderr_lines"])


def test_a_clean_exit_zero_stays_succeeded(monkeypatch, tmp_path):
    status = _exit_zero_job(monkeypatch, tmp_path, stderr="2026-11-04T04:10:34Z [6,658ms] [Warning] [carb] x\n")
    assert status["state"] == "succeeded" and "note" not in status


def test_a_traceback_after_training_saved_a_checkpoint_is_not_called_a_crash(monkeypatch, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "model_2.pt").write_bytes(b"x")
    stdout = f"[INFO] Logging experiment in directory: {tmp_path}\nExact experiment name requested from command line: {run.name}\n"
    status = _exit_zero_job(monkeypatch, tmp_path, stdout=stdout)
    assert status["state"] == "succeeded"


def test_stable_baselines_models_count_as_checkpoints_only_in_its_runs(tmp_path):
    from isaac_mcp.isaaclab.jobs import _latest_checkpoint
    from isaac_mcp.isaaclab.metrics import checkpoints_list

    sb3 = tmp_path / "logs/sb3/task/run"
    sb3.mkdir(parents=True)
    (sb3 / "model.zip").write_bytes(b"x")
    other = tmp_path / "logs/rsl_rl/task/run"
    other.mkdir(parents=True)
    (other / "bundle.zip").write_bytes(b"x")
    assert _latest_checkpoint(sb3).endswith("model.zip") and _latest_checkpoint(other) is None
    assert [c["path"] for c in checkpoints_list(search_roots=[str(tmp_path)])["checkpoints"]] == [str((sb3 / "model.zip").resolve())]


def test_lab_jobs_keep_crash_dumps_local_unless_asked(monkeypatch, tmp_path):
    from isaac_mcp.isaaclab import jobs

    app = tmp_path / "source/isaaclab/isaaclab/app"
    app.mkdir(parents=True)
    assert jobs._crash_upload_args(tmp_path, []) == []  # this Lab's launcher can't take it
    (app / "app_launcher.py").write_text('add_argument("--kit_args")')
    monkeypatch.delenv("ISAAC_MCP_CRASH_UPLOAD", raising=False)
    args = jobs._crash_upload_args(tmp_path, [])
    assert args[0] == "--kit_args" and "--/crashreporter/url= " in args[1]
    assert jobs._crash_upload_args(tmp_path, ["--kit_args=x"]) == []  # the caller's choice stands
    monkeypatch.setenv("ISAAC_MCP_CRASH_UPLOAD", "1")
    assert jobs._crash_upload_args(tmp_path, []) == []


def test_discovery_ignores_a_runtime_folder_others_could_have_made(monkeypatch, tmp_path):
    import json

    from isaac_mcp import client

    folder = tmp_path / "isaac-mcp"
    folder.mkdir(mode=0o700)
    (folder / "1-18000.json").write_text(json.dumps({"pid": 1, "port": 18000, "host": "127.0.0.1"}))
    monkeypatch.setattr(client, "_runtime_dir", lambda: str(folder))
    assert client._runtime_dir_trusted(str(folder))
    folder.chmod(0o777)
    assert not client._runtime_dir_trusted(str(folder))
    assert client.discover_bridges() == []
    assert (folder / "1-18000.json").exists()  # not ours to clean up either
    link = tmp_path / "link"
    link.symlink_to(folder)
    assert not client._runtime_dir_trusted(str(link))


def test_cancel_signals_nothing_for_a_process_that_is_not_the_recorded_job(monkeypatch, tmp_path):
    import json
    import subprocess

    from isaac_mcp.isaaclab import jobs

    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path))
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        # A forged record: a live process whose start time doesn't match.
        record = {"job_id": "20260101-000000-Fake", "pid": proc.pid, "pid_start": "1", "status": "running", "command": ["x", "sleep"]}
        job_dir = tmp_path / record["job_id"]
        job_dir.mkdir()
        (job_dir / "job.json").write_text(json.dumps(record))
        result = jobs.runs_cancel(record["job_id"], grace_s=0.1)
        assert result["cancelled"] is False and proc.poll() is None
    finally:
        proc.kill()
        proc.wait()


def _source(relative):
    return (Path(__file__).resolve().parents[2] / relative).read_text(encoding="utf-8")


def test_quick_start_registers_what_it_built_after_stage_info_resets_ownership():
    source = _source("exts/sim-tools/sim_tools/quick_start.py")
    assert source.index("info = scene.stage_info()") < source.index("scene.owned.update(")
    assert all(name in source[source.index("scene.owned.update("):][:120] for name in ("GROUND", "PHYSICS_SCENE", "DOME", "SUN", "CAMERA"))


def test_a_capture_is_never_visible_at_its_final_name_until_complete():
    source = _source("exts/sim-tools/sim_tools/advanced.py")
    assert source.index('Image.fromarray(array).save(stream, format="PNG")') < source.index("os.link(temp, filename")


@pytest.mark.parametrize("port", [0, -1, 65536, True, "18000"])
def test_use_bridge_refuses_a_bad_port_without_dialling(port):
    import asyncio

    from isaac_mcp import server as server_module

    result = asyncio.run(server_module._use_bridge(server_module.FastMCP("t"), port))
    assert result.startswith("Error: port must be a number")


def test_sim_status_tells_a_busy_bridge_from_a_dead_one(monkeypatch):
    from isaac_mcp import client, sim_sessions

    def busy(*args, **kwargs):
        raise client.BridgeError("DOORWAY_BUSY")

    monkeypatch.setattr(client, "call", busy)
    assert sim_sessions._bridge_state("127.0.0.1", 1, 1)[0] == "busy"

    def dead(*args, **kwargs):
        raise OSError("refused")

    monkeypatch.setattr(client, "call", dead)
    assert sim_sessions._bridge_state("127.0.0.1", 1, 1)[0] == "starting_or_unresponsive"


def test_hydra_override_with_two_equals_is_refused_before_launch():
    from isaac_mcp.isaaclab import jobs

    assert jobs._HYDRA_ARG_RE.match("a.b=1")
    assert not jobs._HYDRA_ARG_RE.match("eh=1=2")


@pytest.mark.parametrize("pid", [0, 1, -1, -5, "1234", None, True])
def test_a_job_record_cannot_name_a_process_group_or_everything(pid):
    from isaac_mcp.isaaclab import jobs

    assert jobs._pid_alive(pid) is False


def test_a_flag_after_its_short_alias_is_still_seen():
    from isaac_mcp.isaaclab.install import _ADD_ARGUMENT_RE

    text = 'p.add_argument("-n", "--num_envs", type=int)\np.add_argument(\n    "--video")\np.add_argument("-x", dest="y")'
    assert _ADD_ARGUMENT_RE.findall(text) == ["--num_envs", "--video"]


def test_cancel_survives_a_job_that_exits_between_the_liveness_check_and_the_group_lookup(monkeypatch, tmp_path):
    import os

    from isaac_mcp.isaaclab import jobs

    monkeypatch.setattr(jobs, "_job_path", lambda _: tmp_path)
    monkeypatch.setattr(jobs, "_read_job", lambda _: {"status": "running", "pid": 123456})
    monkeypatch.setattr(jobs, "_update_job", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "_job_alive", lambda _: True)

    def gone(_):
        raise ProcessLookupError

    monkeypatch.setattr(os, "getpgid", gone)
    assert jobs.runs_cancel("job")["cancelled"] is False


def test_a_garbled_bridge_reply_disconnects_instead_of_desynchronising():
    from isaac_mcp import client

    class Sock:
        def __init__(self):
            self.chunks = [b'{"status":', b' \xff\n{"status": "success", "result": {}}\n']

        def settimeout(self, _):
            pass

        def sendall(self, _):
            pass

        def recv(self, _):
            return self.chunks.pop(0)

        def close(self):
            pass

    bridge = client.BridgeClient.__new__(client.BridgeClient)
    bridge._sock, bridge._buffer, bridge._hold_token = Sock(), b"", None
    bridge.close = lambda: setattr(bridge, "closed", True)
    with pytest.raises(client.BridgeError, match="unreadable reply"):
        bridge._roundtrip("status", {}, 1.0)
    assert bridge.closed


def test_discovery_skips_a_fifo_without_hanging(monkeypatch, tmp_path):
    import os

    from isaac_mcp import client

    folder = tmp_path / "isaac-mcp"
    folder.mkdir(mode=0o700)
    os.mkfifo(folder / "12345-18000.json")
    monkeypatch.setattr(client, "_runtime_dir", lambda: str(folder))
    assert client.discover_bridges() == []


def test_a_job_record_with_nothing_tying_it_to_its_pid_is_not_alive():
    import os

    from isaac_mcp.isaaclab import jobs

    assert not jobs._job_alive({"pid": os.getpid()})
    assert jobs._job_alive({"pid": os.getpid(), "pid_start": jobs._process_start(os.getpid())})

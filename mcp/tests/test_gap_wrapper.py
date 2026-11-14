"""The gap wrapper against a fake Graph-as-Policy checkout: simulation only, never real hardware."""

import asyncio
import inspect
import json

import pytest
from mcp.server.fastmcp import FastMCP

from twinsmith import gap, server, toolsets
from twinsmith.isaaclab import jobs

from sibling_testing import calls, fake_checkout, wait_for


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINSMITH_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROS_DOMAIN_ID", "7")
    monkeypatch.setenv("HF_TOKEN", "hf-secret-value")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-secret-value")
    monkeypatch.delenv("TWINSMITH_GAP_ROOT", raising=False)
    monkeypatch.delenv("TWINSMITH_VABAR_ROOT", raising=False)
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: [])
    jobs._active.clear()
    ws = tmp_path / "ws"
    fake_checkout(ws / "tools/graph-as-policy", programs=("gap", "python"))
    (ws / "runs/gap/graph1").mkdir(parents=True)
    (ws / "runs/gap/graph1/workflow.json").write_text("{}")
    return ws


def allow_paid(tmp_path):
    folder = tmp_path / "home/.config/twinsmith"
    folder.mkdir(parents=True)
    (folder / "config.toml").write_text("[planning]\nallow_paid = true\n")


def test_run_builds_exactly_the_sim_command(workspace):
    co = gap.checkout(workspace=workspace)
    result = gap.call(co, "run", sim="libero/0", graph="graph1", name="first")
    assert result["ok"], result
    folder = workspace / "runs/gap/first"
    graph = str(workspace / "runs/gap/graph1")
    assert result["result"]["command"] == [str(co.root / ".venv/bin/gap"), "run", "--sim", "libero/0",
                                           "--skills", str(workspace / "tools/open-robot-skills"),
                                           "--trace-dir", str(folder / "output"), graph]
    assert wait_for(result["result"]["job_id"])["state"] == "succeeded"
    (call,) = calls(co.root)
    assert call["argv"] == result["result"]["command"] and call["cwd"] == str(folder)
    assert "ROS_DOMAIN_ID" not in call["env"] and call["env"]["MUJOCO_GL"] == "egl"
    assert call["env"]["HF_TOKEN"] == "hf-secret-value" and "OPENROUTER_API_KEY" not in call["env"]
    assert "Paid model calls are off" in result["warnings"][-1]


@pytest.mark.parametrize("arguments", [
    {"sim": "x --real franka", "graph": "graph1"},
    {"sim": "--real=ur_zed", "graph": "graph1"},
    {"sim": "libero/0", "graph": "--real"},
    {"sim": "libero/0", "graph": "graph1/--rea"},
])
def test_real_hardware_is_refused(workspace, arguments):
    co = gap.checkout(workspace=workspace)
    result = gap.call(co, "run", **arguments)
    assert result["code"] == "REAL_ROBOT_REFUSED" and result["error"] == gap.REAL_REFUSAL
    assert calls(co.root) == [] and [path.name for path in (workspace / "runs/gap").iterdir()] == ["graph1"]


@pytest.mark.parametrize("injected", ["--real", "--real=franka", "--rea"])
@pytest.mark.parametrize("action", ["run", "tools"])
def test_the_built_command_is_scanned_again(workspace, monkeypatch, injected, action):
    real_argv = gap.argv
    monkeypatch.setattr(gap, "argv", lambda *args, **kwargs: [*real_argv(*args, **kwargs), injected, "franka"])
    co = gap.checkout(workspace=workspace)
    result = gap.call(co, action, sim="libero/0", graph="graph1", name="scanned")
    assert result["code"] == "REAL_ROBOT_REFUSED"
    assert calls(co.root) == [] and not (workspace / "runs/gap/scanned").exists()
    assert not jobs.jobs_dir().exists() or not any(jobs.jobs_dir().iterdir())


def test_the_tool_has_no_free_form_or_real_argument(workspace):
    instance = FastMCP("test")
    gap.register(instance, workspace=workspace)
    parameters = inspect.signature(instance._tool_manager._tools["gap"].fn).parameters
    assert not {"real", "extra_args", "args"} & set(parameters)


def test_generate_needs_the_owners_permission(workspace, tmp_path):
    co = gap.checkout(workspace=workspace)
    refused = gap.call(co, "generate", instruction="put the block in the bowl")
    assert refused["code"] == "PAID_NOT_ALLOWED" and calls(co.root) == []
    allow_paid(tmp_path)
    started = gap.call(co, "generate", instruction="put the block in the bowl", model="vendor/model-1", name="gen")
    assert started["ok"], started
    assert started["result"]["command"] == [str(co.root / ".venv/bin/gap"), "generate", "put the block in the bowl",
                                            "--skills", str(workspace / "tools/open-robot-skills"),
                                            "--out", str(workspace / "runs/gap/gen/output"), "--model", "vendor/model-1"]
    wait_for(started["result"]["job_id"])
    (call,) = calls(co.root)
    assert call["env"]["OPENROUTER_API_KEY"] == "or-secret-value"
    assert gap.call(co, "generate", instruction="--help")["code"] == "INVALID_VALUE"


def test_a_project_file_cannot_allow_paid_calls(workspace, tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "twinsmith.toml").write_text("[planning]\nallow_paid = true\n")
    monkeypatch.chdir(project)
    assert gap.call(gap.checkout(workspace=workspace), "generate", instruction="x")["code"] == "PAID_NOT_ALLOWED"


def test_status_names_keys_never_values(workspace, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setattr(gap, "gpu_memory_mib", lambda: [16303])
    result = gap.call(gap.checkout(workspace=workspace), "status")
    text = json.dumps(result)
    assert '"HF_TOKEN": true' in text and '"OPENROUTER_API_KEY": false' in text
    assert "hf-secret-value" not in text
    assert not result["result"]["gpu"]["enough"] and "24 GB" in result["result"]["gpu"]["note"]
    assert result["result"]["skills"]["commit"] is None and calls(workspace / "tools/graph-as-policy") == []
    monkeypatch.setattr(gap, "gpu_memory_mib", lambda: [97887])
    assert gap.call(gap.checkout(workspace=workspace), "status")["result"]["gpu"]["enough"]


def test_tools_and_check_are_read_only_runs(workspace):
    co = gap.checkout(workspace=workspace)
    listed = gap.call(co, "tools")
    assert listed["ok"] and listed["result"]["argv"][1:5] == ["tools", "list", "--format", "json"]
    checked = gap.call(co, "check")
    assert checked["ok"] and checked["result"]["argv"][1:4] == ["check", "--format", "json"]
    assert "--probe" not in checked["result"]["argv"]
    assert [call["cwd"] for call in calls(co.root)] == [str(co.root)] * 2


def test_missing_checkout_reports_setup(tmp_path, monkeypatch):
    monkeypatch.delenv("TWINSMITH_GAP_ROOT", raising=False)
    co = gap.checkout(workspace=tmp_path)
    status = gap.call(co, "status")
    assert status["ok"] and "KeplerC/graph-as-policy" in status["result"]["setup"] and gap.PIN in status["result"]["setup"]
    assert gap.call(co, "tools")["code"] == "NOT_INSTALLED"
    assert not (tmp_path / "tools").exists()


def test_a_graph_outside_the_roots_is_refused(workspace, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "workflow.json").write_text("{}")
    co = gap.checkout(workspace=workspace)
    assert gap.call(co, "run", sim="libero/0", graph=str(outside))["code"] == "PATH_NOT_ALLOWED"
    assert gap.call(co, "run", sim="libero/0", graph="missing")["code"] == "NOT_FOUND"
    assert gap.call(co, "run", sim="libero", graph="graph1")["code"] == "INVALID_VALUE"
    assert calls(co.root) == []


def test_the_tool_answers_through_the_server(workspace, monkeypatch):
    monkeypatch.setenv("TWINSMITH_GAP_ROOT", str(workspace / "tools/graph-as-policy"))
    instance = server.build_server({"TWINSMITH_TOOLSETS": "default,+planning"})
    answer = asyncio.run(instance.call_tool("gap", {"action": "status"}))
    text = answer[0][0].text if isinstance(answer, tuple) else answer[0].text
    assert json.loads(text)["tool"] == "gap"


def test_toolsets_register_one_tool_each_and_none_when_off():
    def owned(selection):
        instance = server.build_server({"TWINSMITH_TOOLSETS": selection})
        return {name for name in instance._tool_manager._tools if toolsets.owner(name) in ("planning", "eval")}

    assert owned("default") == set() and owned("all,-planning,-eval") == set()
    assert owned("default,+planning") == {"gap"} and owned("default,+eval") == {"vabar"}
    assert "planning" not in toolsets.DEFAULT and "eval" not in toolsets.DEFAULT


def test_a_busy_gpu_refuses_and_leaves_no_run_folder(workspace, monkeypatch):
    monkeypatch.setattr(jobs, "_concurrency_guard", lambda: {"error": "another job is running"})
    co = gap.checkout(workspace=workspace)
    result = gap.call(co, "run", sim="libero/0", graph="graph1", name="busy")
    assert result["code"] == "GPU_BUSY" and "allow_concurrent_gpu" in result["hint"]
    assert not (workspace / "runs/gap/busy").exists() and calls(co.root) == []

"""The gap wrapper against a fake Graph-as-Policy checkout: simulation only, never real hardware."""

import asyncio
import inspect
import json

import pytest
from mcp.server.fastmcp import FastMCP

from twinsmith import gap, server, sibling, toolsets
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


def owner_config(tmp_path, text):
    folder = tmp_path / "home/.config/twinsmith"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.toml").write_text(text)


def write_graph(folder, nodes, subgraphs=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "workflow.json").write_text(json.dumps({"nodes": nodes, **({"subgraphs": subgraphs} if subgraphs else {})}))
    return folder


SCRIPT = {"pick": {"type": "script", "script": "pick.py"}}


@pytest.mark.parametrize("script", ["/etc/x.py", "../outside.py", "sub/../../outside.py", "", "linked/x.py"])
def test_a_script_outside_the_graph_folder_is_refused(workspace, tmp_path, script):
    owner_config(tmp_path, "[planning]\nallow_scripts = true\n")
    graph = write_graph(workspace / "runs/gap/scripted", {"a": {"type": "router", "script": script}})
    (tmp_path / "elsewhere").mkdir()
    (graph / "linked").symlink_to(tmp_path / "elsewhere")
    co = gap.checkout(workspace=workspace)
    refused = gap.call(co, "run", sim="libero/0", graph="scripted", name="r")
    assert refused["code"] == "PATH_NOT_ALLOWED" and calls(co.root) == []
    assert not (workspace / "runs/gap/r").exists()


def test_a_subgraph_script_is_checked_too(workspace, tmp_path):
    owner_config(tmp_path, "[planning]\nallow_scripts = true\n")
    write_graph(workspace / "runs/gap/nested", {"start": {"type": "subgraph"}},
                subgraphs={"inner": {"nodes": {"s": {"type": "script", "script": "../../x.py"}}}})
    refused = gap.call(gap.checkout(workspace=workspace), "run", sim="libero/0", graph="nested")
    assert refused["code"] == "PATH_NOT_ALLOWED" and "inner.s" in refused["error"]


def test_script_nodes_outside_the_pinned_checkouts_need_allow_scripts(workspace, tmp_path):
    write_graph(workspace / "runs/gap/scripted", SCRIPT)
    co = gap.checkout(workspace=workspace)
    refused = gap.call(co, "run", sim="libero/0", graph="scripted", name="r")
    assert refused["code"] == "SCRIPT_NOT_ALLOWED" and "allow_scripts" in refused["error"] and calls(co.root) == []
    project = tmp_path / "project"
    project.mkdir()
    (project / "twinsmith.toml").write_text("[planning]\nallow_scripts = true\n")
    assert gap.call(co, "run", sim="libero/0", graph="scripted", name="r")["code"] == "SCRIPT_NOT_ALLOWED"
    owner_config(tmp_path, "[planning]\nallow_scripts = true\n")
    started = gap.call(co, "run", sim="libero/0", graph="scripted", name="r")
    assert started["ok"], started
    wait_for(started["result"]["job_id"])


def test_a_graph_in_the_checkout_runs_its_scripts_only_at_the_pin(workspace, monkeypatch):
    co = gap.checkout(workspace=workspace)
    write_graph(co.root / "examples/scripted", SCRIPT)
    refused = gap.call(co, "run", sim="libero/0", graph=str(co.root / "examples/scripted"), name="other-commit")
    assert refused["code"] == "SCRIPT_NOT_ALLOWED"
    monkeypatch.setattr(gap, "PIN", sibling.commit(co.root))
    co = gap.checkout(workspace=workspace)
    started = gap.call(co, "run", sim="libero/0", graph=str(co.root / "examples/scripted/workflow.json"), name="pinned")
    assert started["ok"], started
    wait_for(started["result"]["job_id"])


@pytest.mark.parametrize("text", ["[planning\n", "[planning]\nallow_paid = 'yes'\n", "planning = 3\n",
                                  "[planning]\nallow_scripts = 1\n"])
def test_a_malformed_owner_config_is_a_refusal(workspace, tmp_path, text):
    owner_config(tmp_path, text)
    co = gap.checkout(workspace=workspace)
    for action, arguments in (("status", {}), ("run", {"sim": "libero/0", "graph": "graph1"})):
        refused = gap.call(co, action, **arguments)
        assert refused["ok"] is False and refused["code"] == "CONFIG_INVALID", refused
    assert calls(co.root) == []


def test_a_run_name_is_taken_once(workspace, tmp_path):
    co = gap.checkout(workspace=workspace)
    first = gap.call(co, "run", sim="libero/0", graph="graph1", name="same")
    assert first["ok"], first
    wait_for(first["result"]["job_id"])
    assert gap.call(co, "run", sim="libero/0", graph="graph1", name="same")["code"] == "ALREADY_EXISTS"
    with pytest.raises(sibling.Refusal) as refusal:
        sibling.new_run_folder(workspace / "runs/gap/same")
    assert refusal.value.code == "ALREADY_EXISTS"


def test_model_variables_follow_allow_paid_and_paths_always_pass(workspace, tmp_path, monkeypatch):
    for name, value in (("GAP_LLM_MODEL", "llm"), ("GAP_VLM_MODEL", "vlm"), ("HF_HOME", "/cache/hf"),
                        ("GAP_VAB_ROOT", "/data/vab"), ("GAP_LIBERO_PRO_ROOT", "/data/lp"), ("CUDA_HOME", "/cuda")):
        monkeypatch.setenv(name, value)
    co = gap.checkout(workspace=workspace)
    wait_for(gap.call(co, "run", sim="libero/0", graph="graph1", name="off")["result"]["job_id"])
    allow_paid(tmp_path)
    wait_for(gap.call(co, "run", sim="libero/0", graph="graph1", name="on")["result"]["job_id"])
    off, on = (call["env"] for call in calls(co.root))
    paths = {"HF_HOME": "/cache/hf", "GAP_VAB_ROOT": "/data/vab", "GAP_LIBERO_PRO_ROOT": "/data/lp"}
    assert paths.items() <= off.items() and paths.items() <= on.items()
    assert not {"GAP_LLM_MODEL", "GAP_VLM_MODEL", "OPENROUTER_API_KEY"} & off.keys()
    assert on["GAP_LLM_MODEL"] == "llm" and on["GAP_VLM_MODEL"] == "vlm" and on["OPENROUTER_API_KEY"] == "or-secret-value"
    assert "CUDA_HOME" not in off and "CUDA_HOME" not in on

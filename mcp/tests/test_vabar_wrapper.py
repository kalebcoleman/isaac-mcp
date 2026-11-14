"""The vabar wrapper against a fake VABAR checkout."""

import json

import pytest

from twinsmith import gap, vabar
from twinsmith.isaaclab import jobs

from sibling_testing import calls, fake_checkout, wait_for


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINSMITH_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROS_DOMAIN_ID", "7")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-secret-value")
    for name in ("TWINSMITH_GAP_ROOT", "TWINSMITH_VABAR_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: [])
    jobs._active.clear()
    ws = tmp_path / "ws"
    fake_checkout(ws / "tools/vabar")
    return ws


def test_run_builds_the_module_command_and_is_a_job(workspace):
    co = vabar.checkout(workspace=workspace)
    result = vabar.call(co, "run", task="tool_hanging/wrenches_level1", episodes="0:4", name="first")
    assert result["ok"], result
    folder = workspace / "runs/vabar/first"
    assert result["result"]["command"] == [str(co.root / ".venv/bin/python"), "-m", "vabar", "run",
                                           "--task", "tool_hanging/wrenches_level1", "--solve", "routine",
                                           "--episodes", "0:4", "--out", str(folder)]
    assert wait_for(result["result"]["job_id"])["state"] == "succeeded"
    (call,) = calls(co.root)
    assert call["argv"] == result["result"]["command"] and call["cwd"] == str(folder)
    assert "ROS_DOMAIN_ID" not in call["env"] and "OPENROUTER_API_KEY" not in call["env"]
    assert call["env"]["VABAR_OPEN_ROBOT_SKILLS"] == str(workspace / "tools/open-robot-skills")


def test_graph_runs_take_a_shipped_label(workspace):
    co = vabar.checkout(workspace=workspace)
    result = vabar.call(co, "run", task="tool_hanging/scissors_level1", solve="graph", graph="gt", name="g")
    assert result["result"]["command"][6:10] == ["--solve", "graph", "--graph", "gt"]
    assert "No model is available" in result["warnings"][-1]
    wait_for(result["result"]["job_id"])
    assert vabar.call(co, "run", task="a/b", solve="routine", graph="gt")["code"] == "INVALID_VALUE"
    assert vabar.call(co, "run", task="a/b", solve="graph", graph="../x")["code"] == "INVALID_VALUE"


def test_bench_defaults_to_the_plan_and_starts_nothing(workspace):
    co = vabar.checkout(workspace=workspace)
    result = vabar.call(co, "bench", cell="tool_hanging/L1")
    assert result["ok"] and "job_id" not in result["result"]
    assert result["result"]["planned"][3:] == ["bench", "--cell", "tool_hanging/L1", "--solve", "routine", "--workers", "1",
                                               "--out", result["result"]["planned"][-1]]
    assert calls(co.root) == [] and not (workspace / "runs/vabar").exists()
    assert not any(jobs.jobs_dir().iterdir())


@pytest.mark.parametrize("workers", [0, 5, 9, True])
def test_bench_workers_are_one_to_four(workspace, workers):
    result = vabar.call(vabar.checkout(workspace=workspace), "bench", cell="tool_hanging/L1", workers=workers)
    assert result["code"] == "INVALID_VALUE" and "1 to 4" in result["error"]


def test_bench_runs_as_a_job_when_asked(workspace):
    co = vabar.checkout(workspace=workspace)
    result = vabar.call(co, "bench", cell="tool_hanging/L1", workers=4, limit=2, print_only=False, name="cell")
    assert result["ok"], result
    assert result["result"]["command"][3:] == ["bench", "--cell", "tool_hanging/L1", "--solve", "routine", "--limit", "2",
                                               "--workers", "4", "--out", str(workspace / "runs/vabar/cell")]
    assert wait_for(result["result"]["job_id"])["state"] == "succeeded"
    assert vabar.call(co, "bench", cell="tool_hanging/L1", print_only=False, name="cell")["code"] == "ALREADY_EXISTS"


def test_list_returns_the_tasks(workspace):
    result = vabar.call(vabar.checkout(workspace=workspace), "list")
    assert result["ok"] and result["result"]["tasks"] == ["tool_hanging/wrenches_level1", "usbc_insert/insert_easy"]


def test_results_summarise_the_run_folder(workspace):
    folder = workspace / "runs/vabar/done/tool_hanging_wrenches_level1-routine"
    folder.mkdir(parents=True)
    rows = [{"task": "tool_hanging/wrenches_level1", "solution": "routine", "success": success} for success in (True, True, False)]
    (folder / "episode_results.jsonl").write_text("\n".join(map(json.dumps, rows)) + "\nnot json\n")
    co = vabar.checkout(workspace=workspace)
    result = vabar.call(co, "results", run="done")["result"]
    assert result["rows"] == [{"task": "tool_hanging/wrenches_level1", "solution": "routine", "episodes": 3,
                               "successes": 2, "success_rate": 0.6667}]
    assert vabar.call(co, "results")["result"]["runs"] == ["done"]
    assert vabar.call(co, "results", run="missing")["code"] == "NOT_FOUND"
    assert vabar.call(co, "results", run="../x")["code"] == "INVALID_VALUE"


def test_status_reports_the_pins_beside_it(workspace):
    gap_commit = fake_checkout(workspace / "tools/graph-as-policy", programs=("gap",))
    result = vabar.call(vabar.checkout(workspace=workspace), "status")
    assert result["ok"] and result["pin"] == vabar.PIN and not result["pin_match"]
    beside = result["result"]["graph_as_policy"]
    assert beside["commit"] == gap_commit and beside["pin"] == gap.PIN and not beside["pin_match"]
    assert result["result"]["skills"]["pin"] == gap.SKILLS_PIN and result["result"]["skills"]["commit"] is None
    assert result["result"]["warmup"] == {"ran": False, "note": "No renderer cache in VABAR's environment."}


def test_status_sees_a_warmup(workspace):
    cache = workspace / "tools/vabar/.venv/lib/python3.12/site-packages/ovrtx/bin/cache"
    (cache / "nv_shadercache").mkdir(parents=True)
    (cache / "nv_shadercache/vabar_compiled").write_text("gpu")
    (cache / "vabar_warm").mkdir()
    (cache / "vabar_warm/tool_hanging_L1").write_text("digest")
    result = vabar.call(vabar.checkout(workspace=workspace), "status")
    assert result["result"]["warmup"] == {"ran": True, "warm_cells": ["tool_hanging_L1"]}


def test_missing_checkout(tmp_path, monkeypatch):
    monkeypatch.delenv("TWINSMITH_VABAR_ROOT", raising=False)
    co = vabar.checkout(workspace=tmp_path)
    assert "BerkeleyAutomation/VABAR" in vabar.call(co, "status")["result"]["setup"]
    assert vabar.call(co, "list")["code"] == "NOT_INSTALLED"


def test_results_skip_links_and_cap_each_file(workspace, tmp_path, monkeypatch):
    folder = workspace / "runs/vabar/mixed"
    (folder / "a").mkdir(parents=True)
    row = json.dumps({"task": "t/v", "solution": "routine", "success": True})
    outside = tmp_path / "outside.jsonl"
    outside.write_text((row + "\n") * 50)
    (folder / "a/episode_results.jsonl").symlink_to(outside)
    (tmp_path / "elsewhere/b").mkdir(parents=True)
    (tmp_path / "elsewhere/b/episode_results.jsonl").write_text((row + "\n") * 50)
    (folder / "linked").symlink_to(tmp_path / "elsewhere")
    (folder / "c").mkdir()
    (folder / "c/episode_results.jsonl").write_text((row + "\n") * 10)
    monkeypatch.setattr(vabar, "RESULT_BYTES", 3 * (len(row) + 1) + 5)
    result = vabar.call(vabar.checkout(workspace=workspace), "results", run="mixed")["result"]
    assert result["result_files"] == ["c/episode_results.jsonl"] and result["episodes"] == 3
    assert result["truncated"] == ["c/episode_results.jsonl"]
    (workspace / "runs/vabar/alias").symlink_to(folder)
    assert vabar.call(vabar.checkout(workspace=workspace), "results", run="alias")["code"] == "NOT_FOUND"

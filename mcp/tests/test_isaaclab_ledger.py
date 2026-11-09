"""GPU-hour ledger: wall-clock union per GPU, budget guard, opt-in only (job records are fixtures, no GPU)."""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isaac_mcp.isaaclab import jobs, ledger, preflight
from test_isaaclab_jobs import make_fake_python, make_lab_root, wait_for_status

H = 3600.0


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.delenv(ledger.BUDGET_ENV, raising=False)
    monkeypatch.delenv(ledger.LEDGER_ENV, raising=False)
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: [])
    monkeypatch.setattr(preflight, "_smi", lambda args: None)
    jobs._active.clear()


def add_job(job_id, start_h, end_h, gpu=None, label=None, status="succeeded", base=1_000_000.0, **extra):
    jd = jobs.jobs_dir() / job_id
    jd.mkdir(parents=True, exist_ok=True)
    data = {"job_id": job_id, "kind": "train", "task": "T", "status": status, "label": label,
            "started_at": base + start_h * H, "finished_at": None if end_h is None else base + end_h * H}
    if gpu is not None:
        data["gpu"] = gpu
    data.update(extra)
    jobs._write_job(jd, data)


def freeze(monkeypatch, hours, base=1_000_000.0):
    monkeypatch.setattr(jobs, "_now", lambda: base + hours * H)


def test_overlap_counted_once_and_naive_sum_reported(monkeypatch):
    freeze(monkeypatch, 10)
    add_job("20261101-000000-a", 0, 2, label="a")
    add_job("20261101-000001-b", 1, 3, label="b")   # shares hour 1-2 with a
    add_job("20261101-000002-c", 5, 6, label="c")
    r = ledger.report()
    assert r["union_hours"] == 4.0 and r["naive_sum_hours"] == 5.0 and r["overlap_saved_hours"] == 1.0
    assert r["used_hours"] == 4.0 and "union" in r["counted"]
    by = {j["label"]: j for j in r["jobs"]}
    assert by["a"]["attributed_hours"] == 1.5 and by["b"]["attributed_hours"] == 1.5 and by["a"]["shared_s"] == H
    assert sum(j["attributed_hours"] for j in r["jobs"]) == r["union_hours"]


def test_gpus_are_counted_separately(monkeypatch):
    freeze(monkeypatch, 10)
    add_job("20261101-000000-a", 0, 2, gpu="0")
    add_job("20261101-000001-b", 0, 2, gpu="1")
    r = ledger.report()
    assert r["union_hours"] == 4.0 and r["naive_sum_hours"] == 4.0 and set(r["per_gpu"]) == {"0", "1"}


def test_running_job_counts_to_now_and_dead_unfinished_to_last_write(monkeypatch):
    import os

    base = time.time() - 5 * H
    add_job("20261101-000000-r", 0, None, status="running", pid=None, base=base)
    record = jobs.jobs_dir() / "20261101-000000-r" / "job.json"
    os.utime(record, (base + 2 * H, base + 2 * H))
    monkeypatch.setattr(jobs, "_job_alive", lambda d: False)
    assert ledger.report()["union_hours"] == 2.0  # dead and never finished: counted to the record's last write
    monkeypatch.setattr(jobs, "_job_alive", lambda d: True)
    assert 4.99 < ledger.report()["union_hours"] < 5.1  # alive: counted up to now


def test_label_filter_budget_and_carried(monkeypatch, tmp_path):
    freeze(monkeypatch, 10)
    add_job("20261101-000000-a", 0, 2, label="v3 before 3b")
    add_job("20261101-000001-b", 1, 3, label="other")
    (jobs.jobs_dir() / "gpu_ledger.json").write_text(json.dumps({"budget_hours": 8, "carried_hours": 2.5}))
    r = ledger.report(label="before 3b")
    assert r["label_attributed_hours"] == 1.5 and r["label_naive_hours"] == 2.0
    assert [j["label"] for j in r["jobs"]] == ["v3 before 3b"]
    assert r["used_hours"] == 5.5 and r["remaining_hours"] == 2.5 and r["exhausted"] is False  # label does not shrink the budget
    assert ledger.report(budget_hours=5)["exhausted"] is True


def test_since_clips_intervals(monkeypatch):
    freeze(monkeypatch, 10)
    add_job("20261101-000000-a", 0, 4)
    assert ledger.report(since=None)["union_hours"] == 4.0
    monkeypatch.setattr(jobs, "_parse_since", lambda v: 1_000_000.0 + 3 * H)
    assert ledger.report(since="1h")["union_hours"] == 1.0


@pytest.mark.parametrize("content", ["not json", "[1]", '{"budget_hours": -1}', '{"budget_hours": "8"}'])
def test_bad_ledger_file_is_an_error_not_no_cap(content):
    (jobs.jobs_dir() / "gpu_ledger.json").write_text(content)
    with pytest.raises(ValueError):
        ledger.config()
    with pytest.raises(ValueError):
        ledger.budget_guard()


def test_bad_env_budget():
    import os
    os.environ[ledger.BUDGET_ENV] = "lots"
    try:
        with pytest.raises(ValueError, match="not a number"):
            ledger.config()
    finally:
        del os.environ[ledger.BUDGET_ENV]


def test_unconfigured_changes_nothing(tmp_path, monkeypatch):
    assert ledger.budget_guard() is None
    assert "gpu_hours" not in preflight.preflight()
    root = tmp_path / "root"
    make_lab_root(root)
    monkeypatch.setenv("FAKE_MODE", "ok")
    out = jobs.train(python=str(make_fake_python(tmp_path)), root=str(root), task="T", num_envs=2)
    assert wait_for_status(out["job_id"], "succeeded", timeout=10) == "succeeded"


def test_launch_refused_when_budget_spent_and_allowed_when_not(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ok")
    freeze(monkeypatch, 10)
    add_job("20261101-000000-old", 0, 9)
    monkeypatch.setenv(ledger.BUDGET_ENV, "8")
    root = tmp_path / "root"
    make_lab_root(root)
    py = str(make_fake_python(tmp_path))
    refused = jobs.train(python=py, root=root, task="T", num_envs=2)
    assert "GPU budget spent: 9.00 of 8.00" in refused["error"] and "job_id" not in refused
    assert refused["gpu_ledger"]["naive_sum_hours"] == 9.0
    script = tmp_path / "s.py"
    script.write_text("print(1)\n")
    assert "GPU budget spent" in jobs.run_script(sys.executable, script)["error"]
    pf = preflight.preflight()
    assert pf["can_start"] is False and any("budget is spent" in x for x in pf["reasons"]) and pf["gpu_hours"]["exhausted"]
    monkeypatch.setenv(ledger.BUDGET_ENV, "20")
    monkeypatch.undo()  # real clock for the launched job
    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv(ledger.BUDGET_ENV, "100000000")
    monkeypatch.setenv("FAKE_MODE", "ok")
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: [])
    ok = jobs.train(python=py, root=root, task="T", num_envs=2, label="v3 before 3b")
    assert "job_id" in ok
    wait_for_status(ok["job_id"], "succeeded", timeout=10)
    st = jobs.runs_status(ok["job_id"])
    assert st["label"] == "v3 before 3b"
    data = jobs._read_job(jobs.jobs_dir() / ok["job_id"])
    assert data["gpu"] == "0" and data["label"] == "v3 before 3b"
    assert any(r.get("label") == "v3 before 3b" for r in jobs.runs_list()["runs"])


def test_gpu_follows_device_argument():
    assert jobs._gpu_index("1") == "1" and jobs._gpu_index("2,3") == "2" and jobs._gpu_index(None) == "0" and jobs._gpu_index("") == "0"


def test_preflight_ledger_on_request():
    pf = preflight.preflight(ledger=True)
    assert pf["gpu_hours"]["used_hours"] == 0 and "remaining_hours" not in pf["gpu_hours"]


@pytest.mark.parametrize("bad", ["", "  ", 5, "x" * 200])
def test_label_validated(bad):
    with pytest.raises(ValueError, match="label"):
        jobs._validate_label(bad)

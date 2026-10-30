import os
import stat
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isaac_mcp.isaaclab import jobs


def make_fake_python(tmp_path: Path) -> Path:
    p = tmp_path / "fake_python.py"
    p.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, time\n"
        "mode = os.environ.get('FAKE_MODE', 'ok')\n"
        "if mode == 'sleep':\n"
        "    time.sleep(3600)\n"
        "elif mode == 'fail':\n"
        "    print('boom', file=sys.stderr)\n"
        "    sys.exit(7)\n"
        "else:\n"
        "    print('line1')\n"
        "    print('line2')\n"
        "    sys.exit(0)\n"
    )
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


@pytest.fixture(autouse=True)
def isolated_jobs(tmp_path, monkeypatch):
    jd = tmp_path / "jobs"
    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(jd))
    # reset module jobs_dir cache (none) and active map
    jobs._active.clear()
    yield
    jobs._active.clear()


def wait_for_status(job_id, target, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = jobs.runs_status(job_id)["state"]
        if st == target or st in ("succeeded", "failed", "cancelled", "timeout"):
            return st
        time.sleep(0.1)
    return jobs.runs_status(job_id)["state"]


def test_launch_status_tail(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("FAKE_MODE", "ok")
    res = jobs.train(python=str(fake_py), root=str(root), task="Foo.Bar:1", num_envs=4)
    assert set(res) == {"job_id", "log_dir", "command"}
    jid = res["job_id"]
    # wait for completion
    st = wait_for_status(jid, "succeeded", timeout=10)
    assert st == "succeeded"
    status = jobs.runs_status(jid)
    assert status["pid_alive"] is False
    assert status["exit_code"] == 0
    assert status["last_log_lines"] and "line1" in "\n".join(status["last_log_lines"])
    tail = jobs.runs_tail(jid, stream="stdout", lines=10)
    assert "line1" in "\n".join(tail["lines"])
    assert "line2" in "\n".join(tail["lines"])
    # runs_list
    rl = jobs.runs_list(state="finished")
    assert any(r["job_id"] == jid for r in rl["runs"])


def test_fail_exit_code(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("FAKE_MODE", "fail")
    res = jobs.train(python=str(fake_py), root=str(root), task="T", num_envs=2)
    jid = res["job_id"]
    st = wait_for_status(jid, "failed", timeout=10)
    assert st == "failed"
    assert jobs.runs_status(jid)["exit_code"] == 7


def test_cancel_kills_process_group(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("FAKE_MODE", "sleep")
    res = jobs.train(python=str(fake_py), root=str(root), task="SleepTask", num_envs=1)
    jid = res["job_id"]
    # ensure running
    time.sleep(0.5)
    assert jobs.runs_status(jid)["state"] == "running"
    out = jobs.runs_cancel(jid, grace_s=1)
    assert out["state"] == "cancelled"
    time.sleep(0.5)
    st = jobs.runs_status(jid)
    assert st["state"] == "cancelled"
    assert st["pid_alive"] is False


def test_timeout(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("FAKE_MODE", "sleep")
    res = jobs.train(python=str(fake_py), root=str(root), task="TimeoutTask", num_envs=1, timeout_s=0.5)
    jid = res["job_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        st = jobs.runs_status(jid)["state"]
        if st == "timeout":
            break
        time.sleep(0.2)
    assert jobs.runs_status(jid)["state"] == "timeout"


def test_rehydration_host_restarted(tmp_path, monkeypatch):
    # simulate a job dir with running status and dead pid
    jd = jobs.jobs_dir()
    jid = "20200101-000000-dead"
    d = jd / jid
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "id": jid,
        "job_id": jid,
        "kind": "train",
        "task": "T",
        "command": ["x"],
        "pid": 999999,  # unlikely alive
        "started_at": time.time() - 10,
        "finished_at": None,
        "status": "running",
        "exit_code": None,
        "log_dir": str(d),
        "run_dir": str(d),
    }
    (d / "job.json").write_text(__import__("json").dumps(data))
    # runs_status reconciles
    st = jobs.runs_status(jid)
    assert st["state"] == "failed"
    # check note
    import json
    raw = json.loads((d / "job.json").read_text())
    assert raw.get("note") == "host restarted"


def test_validation(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    # bad task
    with pytest.raises(ValueError):
        jobs.train(python=str(fake_py), root=str(root), task="bad task!")
    # bad extra_args
    with pytest.raises(ValueError):
        jobs.train(python=str(fake_py), root=str(root), task="T", extra_args=["rm -rf /"])
    with pytest.raises(ValueError):
        jobs.train(python=str(fake_py), root=str(root), task="T", extra_args=["foo"])
    # checkpoint missing
    with pytest.raises(ValueError):
        jobs.train(python=str(fake_py), root=str(root), task="T", checkpoint=str(tmp_path / "nope.pth"))
    # checkpoint traversal outside roots
    outside = tmp_path.parent / "outside.pth"
    outside.write_text("x")
    with pytest.raises(ValueError):
        jobs.train(python=str(fake_py), root=str(root), task="T", checkpoint=str(outside))


def test_eval_play(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    # create a checkpoint under root
    ckpt = root / "model.pth"
    ckpt.write_text("x")
    monkeypatch.setenv("FAKE_MODE", "ok")
    r1 = jobs.eval_(python=str(fake_py), root=str(root), task="T", checkpoint=str(ckpt), num_envs=2, rl_library="rl_games")
    assert "job_id" in r1
    # one GPU job at a time: the concurrency guard refuses play while eval is still running
    assert wait_for_status(r1["job_id"], "succeeded", timeout=10) == "succeeded"
    r2 = jobs.play(python=str(fake_py), root=str(root), task="T", checkpoint=str(ckpt), num_envs=1, headless=True)
    assert "job_id" in r2
    assert wait_for_status(r2["job_id"], "succeeded", timeout=10) == "succeeded"


def test_runs_tail_lines_cap(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("FAKE_MODE", "ok")
    res = jobs.train(python=str(fake_py), root=str(root), task="T")
    jid = res["job_id"]
    wait_for_status(jid, "succeeded", timeout=10)
    out = jobs.runs_tail(jid, stream="stdout", lines=10000)
    assert len(out["lines"]) <= 1000


def test_job_id_validation_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path / "jobs"))
    (tmp_path / "jobs").mkdir()
    for bad in ["../x", "..", "a/b", "", "20260101-000000-../../etc"]:
        for fn in (jobs.runs_status, jobs.runs_cancel):
            with pytest.raises(ValueError):
                fn(bad)
        with pytest.raises(ValueError):
            jobs.runs_tail(bad)


def test_rl_library_validation():
    assert jobs._validate_library(None) is None
    assert jobs._validate_library("rl_games") == "rl_games"
    for bad in ["RL", "a b", "x;y", "--evil"]:
        with pytest.raises(ValueError):
            jobs._validate_library(bad)


def test_job_alive_requires_matching_cmdline(tmp_path):
    import os
    assert jobs._job_alive({"pid": os.getpid(), "command": ["py", "definitely-not-this-process.py"]}) is False
    assert jobs._job_alive({"pid": None, "command": []}) is False


def test_extra_args_accept_hydra_overrides_but_not_shell_syntax():
    ok = ["--seed=3", "agent.params.config.minibatch_size=1536", "+env.foo=1", "~agent.bar"]
    assert jobs._validate_extra_args(ok) == ok
    for bad in ["x;rm -rf /", "a=b c", "$(id)", "k=`id`", "--x=1;ls", "agent.a=1|cat", "-x", "a b"]:
        with pytest.raises(ValueError):
            jobs._validate_extra_args([bad])


def test_checkpoint_discovered_from_training_log(tmp_path):
    ck = tmp_path / "logs" / "exp" / "nn" / "last_x_ep_2.pth"
    ck.parent.mkdir(parents=True)
    ck.write_text("w")
    log = tmp_path / "stdout.log"
    log.write_text(f"noise\n=> saving checkpoint '{ck}'\nMAX EPOCHS NUM!\n")
    assert jobs._checkpoint_from_log(log) == str(ck.resolve())
    log.write_text("=> saving checkpoint '/does/not/exist.pth'\n")
    assert jobs._checkpoint_from_log(log) is None


def _write_running_job(job_id: str, pid: int) -> Path:
    jd = jobs.jobs_dir() / job_id
    jd.mkdir(parents=True)
    jobs._write_job(jd, {"job_id": job_id, "status": "running", "pid": pid, "command": ["py", "train.py"],
                         "started_at": time.time(), "exit_code": None})
    return jd


def _dead_pid() -> int:
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid  # reaped: no such process


def test_status_does_not_fail_a_job_its_watcher_still_owns():
    """The process has exited but the watcher has not recorded the result yet: not 'failed / host restarted'."""
    jid = "20261029-120000-owned"
    _write_running_job(jid, _dead_pid())
    jobs._active[jid] = object()  # the watcher still owns it
    assert jobs.runs_status(jid)["state"] == "running"
    assert jobs.runs_status(jid)["exit_code"] is None
    assert any(r["job_id"] == jid and r["state"] == "running" for r in jobs.runs_list(state="running")["runs"])


def test_status_reconciles_an_orphaned_job_after_host_restart():
    jid = "20261029-120001-orphan"
    _write_running_job(jid, _dead_pid())  # not in _active: a previous host's job
    st = jobs.runs_status(jid)
    assert st["state"] == "failed"
    assert jobs._read_job(jobs.jobs_dir() / jid)["note"] == "host restarted"


# ---- cancel / id / guard regressions found by the 5080 guard test (2026-10-30) ----

def _launch_sleeping(tmp_path, monkeypatch, **kw):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    monkeypatch.setenv("FAKE_MODE", "sleep")
    monkeypatch.setattr(jobs, "_kit_bridge_guard", lambda: None)
    return jobs.train(python=str(fake_py), root=str(root), task="T", **kw)


def _disk_status(job_id):
    import json

    return json.loads((jobs.jobs_dir() / job_id / "job.json").read_text())["status"]


@pytest.mark.parametrize("round_", range(4))
def test_cancel_is_not_overwritten_by_the_watcher(tmp_path, monkeypatch, round_):
    r = _launch_sleeping(tmp_path, monkeypatch)
    time.sleep(0.3)
    out = jobs.runs_cancel(r["job_id"], grace_s=2)
    assert out == {"cancelled": True, "state": "cancelled"}
    time.sleep(1.0)  # let the watcher observe the dead process
    assert _disk_status(r["job_id"]) == "cancelled"
    assert jobs.runs_status(r["job_id"])["state"] == "cancelled"


def test_cancel_leaves_a_finished_job_alone(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("FAKE_MODE", "ok")
    monkeypatch.setattr(jobs, "_kit_bridge_guard", lambda: None)
    r = jobs.train(python=str(fake_py), root=str(root), task="T")
    assert wait_for_status(r["job_id"], "succeeded") == "succeeded"
    time.sleep(0.3)
    out = jobs.runs_cancel(r["job_id"], grace_s=1)
    assert out["cancelled"] is False and out["state"] == "succeeded"
    assert _disk_status(r["job_id"]) == "succeeded"
    # cancelling again after a real cancel is also a no-op
    r2 = _launch_sleeping(tmp_path, monkeypatch, allow_concurrent=True)
    jobs.runs_cancel(r2["job_id"], grace_s=2)
    again = jobs.runs_cancel(r2["job_id"], grace_s=1)
    assert again["cancelled"] is False and again["state"] == "cancelled"


def test_job_ids_are_unique_within_one_second(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.time, "strftime", lambda fmt, t=None: "20260101-000000")
    a = jobs._job_id("task")
    (jobs.jobs_dir() / a).mkdir(parents=True)
    b = jobs._job_id("task")
    (jobs.jobs_dir() / b).mkdir(parents=True)
    c = jobs._job_id("task")
    assert len({a, b, c}) == 3
    assert all(jobs._JOB_ID_RE.match(x) for x in (a, b, c))


def test_two_rapid_launches_get_distinct_jobs_and_guard_holds(tmp_path, monkeypatch):
    a = _launch_sleeping(tmp_path, monkeypatch)
    refused = _launch_sleeping(tmp_path, monkeypatch)  # no allow_concurrent: must be refused
    assert "error" in refused and refused["running_job_id"] == a["job_id"]
    b = _launch_sleeping(tmp_path, monkeypatch, allow_concurrent=True)
    assert b["job_id"] != a["job_id"]
    for jid in (a["job_id"], b["job_id"]):
        jobs.runs_cancel(jid, grace_s=2)


def test_run_dir_from_log_reads_the_absolute_experiment_dir(tmp_path):
    log = tmp_path / "stdout.log"
    log.write_text(
        "[INFO] Logging experiment in directory: /x/logs/rl_games/task\n"
        "Exact experiment name requested from command line: /x/logs/rl_games/task/2026-10-30_09-57-11\n"
        "Exact experiment name requested from command line: 2026-10-30_09-57-11\n"
    )
    assert jobs._run_dir_from_log(log) == "/x/logs/rl_games/task/2026-10-30_09-57-11"
    log.write_text("no such line\n")
    assert jobs._run_dir_from_log(log) is None
    assert jobs._run_dir_from_log(tmp_path / "missing.log") is None

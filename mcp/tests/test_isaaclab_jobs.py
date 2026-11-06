import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isaac_mcp.isaaclab import jobs


def make_lab_root(root: Path) -> None:
    """A checkout with Isaac Lab 3's entry scripts: a job needs them to exist even when a fake interpreter runs it."""
    scripts = root / "scripts" / "reinforcement_learning"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "train.py").write_text("")
    (scripts / "play.py").write_text("")


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


@pytest.mark.parametrize('tool', ['train', 'eval_', 'play'])
@pytest.mark.parametrize('headless,extras,visualizer', [
    (True, [], 'none'), (True, ['--video'], 'kit'),
    (True, ['--video_length=60'], 'none'), (False, ['--video'], 'kit'),
])
def test_job_display_choice_is_child_local_and_video_keeps_renderer(
        tmp_path, monkeypatch, tool, headless, extras, visualizer):
    import json

    root = tmp_path / 'lab'
    make_lab_root(root)
    checkpoint = root / 'model.pt'
    checkpoint.write_bytes(b'fixture')
    fake_py = make_fake_python(tmp_path)
    fake_py.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                       'print(json.dumps({"headless":os.environ.get("HEADLESS"), "argv":sys.argv[1:]}))\n')
    monkeypatch.setenv('HEADLESS', '0' if headless else '1')
    args = dict(python=str(fake_py), root=str(root), task='T', headless=headless,
                extra_args=extras, allow_concurrent_gpu=True)
    if tool != 'train':
        args['checkpoint'] = str(checkpoint)
    result = getattr(jobs, tool)(**args)
    assert wait_for_status(result['job_id'], 'succeeded') == 'succeeded'
    payload = json.loads(jobs.runs_tail(result['job_id'])['lines'][-1])
    assert payload['headless'] == ('1' if headless else '0')
    assert payload['argv'][payload['argv'].index('--viz') + 1] == visualizer
    assert os.environ['HEADLESS'] == ('0' if headless else '1')


@pytest.mark.parametrize('headless', [False, True])
@pytest.mark.parametrize('override', ['--viz=kit', '--visualizer=none', '--viz=newton_gl'])
def test_explicit_visualizer_is_forwarded_once(tmp_path, monkeypatch, headless, override):
    captured = {}
    monkeypatch.setattr(jobs, '_start_job', lambda *a, **kw: captured.update(argv=a[4], **kw) or captured)
    result = jobs.train(python='python', root=tmp_path, task='T', headless=headless, extra_args=[override])
    assert result['argv'].count(override) == 1
    assert '--viz' not in result['argv']
    assert result['headless'] is headless


@pytest.mark.parametrize('extras', [
    ['--viz'], ['--visualizer='], ['--viz=kit', '--visualizer=none'],
    ['--video', '--viz=none'], ['--video', '--visualizer=none'],
])
def test_ambiguous_or_incompatible_visualizer_is_refused_before_launch(monkeypatch, extras):
    monkeypatch.setattr(jobs, '_start_job', lambda *a, **kw: pytest.fail('must not launch'))
    with pytest.raises(ValueError, match='visualizer|viz'):
        jobs.train(python='python', root='lab', task='T', extra_args=extras)


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


def test_import_does_not_create_jobs_directory_but_launch_does(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ISAACLAB_JOBS_DIR", raising=False)
    directory = home / ".local" / "share" / "isaac-mcp" / "jobs"
    subprocess.run([sys.executable, "-c", "import isaac_mcp.isaaclab.jobs"], check=True, env=os.environ.copy())
    assert not directory.exists()

    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    make_lab_root(root)
    result = jobs.train(python=str(fake_py), root=str(root), task="Test", allow_concurrent=True,
                        allow_concurrent_gpu=True)
    assert directory.is_dir()
    assert (directory / result["job_id"] / "job.json").is_file()
    assert wait_for_status(result["job_id"], "succeeded") == "succeeded"


def test_launch_status_tail(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    make_lab_root(root)
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
    make_lab_root(root)
    monkeypatch.setenv("FAKE_MODE", "fail")
    res = jobs.train(python=str(fake_py), root=str(root), task="T", num_envs=2)
    jid = res["job_id"]
    st = wait_for_status(jid, "failed", timeout=10)
    assert st == "failed"
    assert jobs.runs_status(jid)["exit_code"] == 7


def test_cancel_kills_process_group(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    make_lab_root(root)
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
    make_lab_root(root)
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
    make_lab_root(root)
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
    make_lab_root(root)
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
    make_lab_root(root)
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
    make_lab_root(root)
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
    make_lab_root(root)
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


def test_lab3_separate_root_and_name_with_spaces_and_terminal_colors(tmp_path):
    log = tmp_path / "stdout.log"
    log.write_text(
        "[INFO] Logging experiment in directory: /old/root\n"
        "\x1b[32m[INFO] Logging experiment in directory: /x/my runs/cartpole\x1b[0m\n"
        "Exact experiment name requested from command line: 2026-11-01_trial one\n"
    )
    assert jobs._run_dir_from_log(log) == "/x/my runs/cartpole/2026-11-01_trial one"


@pytest.mark.parametrize("name", ["..", ".", "../outside", "nested/run", "nested\\run", "bad\x00name"])
def test_relative_experiment_name_cannot_escape_announced_root(tmp_path, name):
    log = tmp_path / "stdout.log"
    log.write_text("[INFO] Logging experiment in directory: /x/logs\n"
                   f"Exact experiment name requested from command line: {name}\n")
    assert jobs._run_dir_from_log(log) is None


def test_basename_requires_an_earlier_root_and_bounded_header(tmp_path):
    log = tmp_path / "stdout.log"
    text = "Exact experiment name requested from command line: trial\n"
    log.write_text(text + "[INFO] Logging experiment in directory: /later/root\n")
    assert jobs._run_dir_from_log(log) is None
    log.write_text("x" * 131072 + "\n[INFO] Logging experiment in directory: /x/logs\n" + text)
    assert jobs._run_dir_from_log(log) is None


def test_status_finds_checkpoint_in_lab3_resolved_run_directory(tmp_path):
    job_id = "20261101-153515-Cartpole"
    jd = jobs.jobs_dir() / job_id
    jd.mkdir(parents=True)
    experiment_root = tmp_path / "logs" / "rsl_rl" / "cartpole"
    experiment = experiment_root / "2026-11-01_15-35-17"
    experiment.mkdir(parents=True)
    checkpoint = experiment / "model_1.pt"
    checkpoint.write_bytes(b"test checkpoint")
    (jd / "stdout.log").write_text(
        f"[INFO] Logging experiment in directory: {experiment_root}\n"
        f"Exact experiment name requested from command line: {experiment.name}\n"
    )
    jobs._write_job(jd, {"status": "succeeded", "exit_code": 0,
                         "run_dir": str(jd), "log_dir": str(jd)})
    result = jobs.runs_status(job_id)
    assert result["run_dir"] == str(experiment)
    assert result["latest_checkpoint"] == str(checkpoint.resolve())
    assert result["state"] == "succeeded"


def test_job_readers_see_complete_record_during_publication(monkeypatch):
    import concurrent.futures
    import threading

    job_id = "20261101-153515-atomic"
    jd = jobs.jobs_dir() / job_id
    jd.mkdir(parents=True)
    before = {"status": "succeeded", "exit_code": 0, "generation": "old"}
    after = {**before, "generation": "new", "details": "complete " * 1000}
    jobs._write_job(jd, before)
    ready, release = threading.Event(), threading.Event()
    replace = jobs.os.replace

    def held_replace(source, target):
        assert Path(source).parent == jd and Path(source).name.startswith(".job-")
        assert Path(target) == jd / "job.json"
        ready.set()
        assert release.wait(5), "reader did not release publication"
        replace(source, target)

    monkeypatch.setattr(jobs.os, "replace", held_replace)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        def publish():
            with jobs._jobs_lock:
                jobs._write_job(jd, after)
        future = executor.submit(publish)
        try:
            assert ready.wait(5), "writer never reached publication"
            assert jobs._read_job(jd) == before
            assert jobs.runs_status(job_id)["state"] == "succeeded"
            assert jobs.runs_cancel(job_id)["cancelled"] is False
        finally:
            release.set()
        future.result(timeout=5)
    assert jobs._read_job(jd) == after
    assert not list(jd.glob(".job-*.tmp"))


@pytest.mark.parametrize("failure", ["replace", "fsync"])
def test_failed_metadata_publication_preserves_old_record_and_cleans_temp(monkeypatch, failure):
    jd = jobs.jobs_dir() / "20261101-153515-failed-publish"
    jd.mkdir(parents=True)
    before = {"status": "succeeded", "exit_code": 0}
    jobs._write_job(jd, before)
    original = (jd / "job.json").read_bytes()

    def fail(*args):
        raise OSError("simulated publication failure")

    monkeypatch.setattr(jobs.os, failure, fail)
    with pytest.raises(OSError, match="publication failure"):
        jobs._write_job(jd, {"status": "cancelled"})
    assert (jd / "job.json").read_bytes() == original
    assert not list(jd.glob(".job-*.tmp"))


def test_atomic_job_write_does_not_follow_existing_destination_symlink(tmp_path):
    jd = jobs.jobs_dir() / "20261101-153515-symlink"
    jd.mkdir(parents=True)
    other = tmp_path / "unrelated"
    other.write_text("preserve this file")
    (jd / "job.json").symlink_to(other)
    record = {"status": "succeeded"}
    jobs._write_job(jd, record)
    assert other.read_text() == "preserve this file"
    assert not (jd / "job.json").is_symlink()
    assert jobs._read_job(jd) == record


def test_orphan_timeout_leaves_a_finished_job_to_settle(tmp_path, monkeypatch):
    """Review fix: a job that ended cleanly before anyone looked is not recorded as timed out."""
    d = tmp_path / "job"
    d.mkdir()
    (d / jobs.EXIT_FILE).write_text("0")
    data = {"status": "running", "pid": 2 ** 22 + 12345, "started_at": time.time() - 100, "timeout_s": 5, "command": ["x", "y"]}
    monkeypatch.setattr(jobs, "_job_alive", lambda _d: False)
    killed = []
    monkeypatch.setattr(jobs, "_kill_orphan_group", lambda _d: killed.append(1))
    jobs._enforce_orphan_timeout("j1", d, data)
    assert data["status"] == "running" and not killed
    jobs._settle_orphan(d, data)
    assert data["status"] == "succeeded" and data["exit_code"] == 0


def test_orphan_timeout_not_recorded_when_the_kill_did_not_happen(tmp_path, monkeypatch):
    d = tmp_path / "job"
    d.mkdir()
    data = {"status": "running", "pid": 1, "started_at": time.time() - 100, "timeout_s": 5, "command": ["x", "y"]}
    monkeypatch.setattr(jobs, "_job_alive", lambda _d: True)
    monkeypatch.setattr(jobs, "_kill_orphan_group", lambda _d: False)
    jobs._enforce_orphan_timeout("j2", d, data)
    assert data["status"] == "running"
    monkeypatch.setattr(jobs, "_kill_orphan_group", lambda _d: True)
    jobs._enforce_orphan_timeout("j2", d, data)
    assert data["status"] == "timeout"


REAL_LOG_HEAD = (
    "\x1b[38;2;118;185;0m│ Physics       newton_mjwarp                    │\x1b[0m  banner art\n"
    "\tPhysics step-size     : 0.005\n\tReplicate physics     : True\n"
    "[INFO]: Completed setting up the environment...\n"
)


def _job_with_log(job_id, text, **record):
    jd = jobs.jobs_dir() / job_id
    jd.mkdir(parents=True)
    (jd / "stdout.log").write_text(text)
    jobs._write_job(jd, {"status": "running", "run_dir": str(jd), "log_dir": str(jd), **record})
    return jd


def test_status_names_the_physics_engine_and_says_when_the_environment_is_ready():
    """A task chooses its own engine, and Newton is not PhysX: the status says which, and later log lines do not hide readiness."""
    _job_with_log("20261105-120000-G1", REAL_LOG_HEAD + "noise\n" * 5000)
    result = jobs.runs_status("20261105-120000-G1")
    assert result["physics"] == "newton_mjwarp" and result["environment_ready"] is True


def test_status_before_the_environment_is_built_reports_not_ready_and_no_engine():
    _job_with_log("20261105-120001-G1", "starting Kit\nloading extensions\n")
    result = jobs.runs_status("20261105-120001-G1")
    assert result["environment_ready"] is False and "physics" not in result


def test_a_physx_banner_is_reported_as_physx_and_a_step_size_line_is_not_an_engine():
    _job_with_log("20261105-120002-Cartpole", "│ Physics       physx            │\n\tPhysics step-size     : 0.005\n")
    assert jobs.runs_status("20261105-120002-Cartpole")["physics"] == "physx"
    _job_with_log("20261105-120003-Cartpole", "\tPhysics step-size     : 0.005\n")
    assert "physics" not in jobs.runs_status("20261105-120003-Cartpole")


def test_the_job_shell_enforces_timeout_s_without_a_host(tmp_path):
    import subprocess
    import time

    command = jobs._exit_recording(["sleep", "30"], tmp_path, timeout_s=1)
    started = time.monotonic()
    proc = subprocess.Popen(command, start_new_session=True)
    assert proc.wait(timeout=15) != 0 and time.monotonic() - started < 10
    assert (tmp_path / jobs.TIMEOUT_FILE).exists() and (tmp_path / jobs.EXIT_FILE).exists()
    data = dict(status="running", started_at=time.time(), pid=proc.pid)
    jobs._write_job(tmp_path, data)
    jobs._settle_orphan(tmp_path, data)
    assert jobs._read_job(tmp_path)["status"] == "timeout"


def test_a_job_that_finishes_in_time_leaves_no_guard_and_no_marker(tmp_path):
    import subprocess
    import time

    proc = subprocess.Popen(jobs._exit_recording(["sh", "-c", "exit 3"], tmp_path, timeout_s=60), start_new_session=True)
    assert proc.wait(timeout=10) == 3
    assert not (tmp_path / jobs.TIMEOUT_FILE).exists() and (tmp_path / jobs.EXIT_FILE).read_text() == "3"
    time.sleep(1.5)  # the guard's last one-second sleep
    assert subprocess.run(["pgrep", "-g", str(proc.pid)], capture_output=True).stdout == b""


def test_a_flag_and_its_value_as_two_items_become_one():
    assert jobs._validate_extra_args(["--video", "--video_length", "100"]) == ["--video", "--video_length=100"]
    assert jobs._validate_extra_args(["--video_length=100", "env.scene.x=1"]) == ["--video_length=100", "env.scene.x=1"]
    with pytest.raises(ValueError):
        jobs._validate_extra_args(["--flag", "a;b"])
    with pytest.raises(ValueError):
        jobs._validate_extra_args(["bare"])


def test_video_paths_reads_lab_3s_log_lines(tmp_path):
    folder = tmp_path / "logs" / "videos" / "play"
    folder.mkdir(parents=True)
    (folder / "clip_model_1499_0000.mp4").write_bytes(b"x")
    log = tmp_path / "stdout.log"
    log.write_text(f"[INFO]: [VideoRecorder] Wrote 400 frames to {folder}/clip_model_1499_0000.mp4\n")
    assert jobs._video_paths(None, log) == [str(folder / "clip_model_1499_0000.mp4")]
    log.write_text(f"        output_dir: {folder}\n")
    assert jobs._video_paths(None, log) == [str(folder / "clip_model_1499_0000.mp4")]


def test_video_paths_will_not_search_a_folder_a_log_line_merely_names(tmp_path):
    (tmp_path / "secret.mp4").write_bytes(b"x")
    log = tmp_path / "stdout.log"
    log.write_text(f"        output_dir: {tmp_path}\n")
    assert jobs._video_paths(None, log) == []


def test_a_timed_out_job_that_ignored_term_reports_a_timeout(tmp_path):
    (tmp_path / jobs.TIMEOUT_FILE).write_text("")
    data = dict(job_id="j")
    jobs._write_job(tmp_path, data)
    jobs._settle_orphan(tmp_path, data)
    assert data["status"] == "timeout" and data.get("note") != "host restarted"


def test_physics_is_the_hydra_override_on_lab_3_and_needs_nothing_on_lab_2(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_per_library", lambda root: None)
    assert jobs._with_physics("physx", tmp_path, ["--video"]) == ["--video", "physics=isaacsim_physx"]
    assert jobs._with_physics("newton", tmp_path, []) == ["physics=newton_mjwarp"]
    assert jobs._with_physics(None, tmp_path, ["a"]) == ["a"]
    with pytest.raises(ValueError, match="not both"):
        jobs._with_physics("physx", tmp_path, ["physics=newton_mjwarp"])
    with pytest.raises(ValueError, match="must be one of"):
        jobs._with_physics("ovphysx", tmp_path, [])
    monkeypatch.setattr(jobs, "_per_library", lambda root: ["rsl_rl"])
    assert jobs._with_physics("physx", tmp_path, ["--video"]) == ["--video"]
    with pytest.raises(ValueError, match="PhysX only"):
        jobs._with_physics("newton", tmp_path, [])


def test_video_paths_finds_the_newest_of_many(tmp_path):
    folder = tmp_path / "videos" / "train"
    folder.mkdir(parents=True)
    for step in range(150):
        clip = folder / f"rl-video-step-{step}.mp4"
        clip.write_bytes(b"x")
        os.utime(clip, (1000 + step, 1000 + step))
    assert jobs._video_paths(tmp_path, None)[:2] == [str(folder / "rl-video-step-149.mp4"), str(folder / "rl-video-step-148.mp4")]

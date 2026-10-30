"""The concurrency guard: one 16 GB GPU fits one Isaac Lab job, so a second launch is refused
unless the caller passes allow_concurrent=True. No Isaac Lab and no GPU: real (harmless) python
processes stand in for jobs."""

import contextlib
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isaac_mcp.isaaclab import jobs

LIVE_CMD = [sys.executable, "-c", "import time; time.sleep(60)"]


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


def write_running_record(job_id: str, pid: int, command: list[str]) -> Path:
    """A 'running' job.json as a crashed host leaves it: not in jobs._active, pid recorded."""
    d = jobs.jobs_dir() / job_id
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "id": job_id,
        "job_id": job_id,
        "kind": "train",
        "task": "T",
        "command": command,
        "pid": pid,
        "started_at": time.time() - 5,
        "finished_at": None,
        "status": "running",
        "exit_code": None,
        "log_dir": str(d),
        "run_dir": str(d),
    }
    jobs._write_job(d, data)
    return d


def cmdline(pid: int) -> bytes:
    return Path(f"/proc/{int(pid)}/cmdline").read_bytes()


@contextlib.contextmanager
def live_job():
    """A real sleeping process plus a 'running' record whose command[1] is in its /proc cmdline,
    so jobs._job_alive() is true. Killed (and reaped) on exit."""
    proc = subprocess.Popen(LIVE_CMD)
    try:
        deadline = time.time() + 10
        while time.time() < deadline and b"-c" not in cmdline(proc.pid).split(b"\0"):
            time.sleep(0.02)
        job_id = "20261029-120000-guard"
        d = write_running_record(job_id, proc.pid, LIVE_CMD)
        assert jobs._running_job() is not None
        yield job_id, d, proc.pid
    finally:
        proc.kill()
        proc.wait()


def dead_pid() -> int:
    """A pid that existed and was reaped, so os.kill(pid, 0) raises ProcessLookupError."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def launch_kwargs(root: Path, checkpoint: Path) -> dict:
    return {
        "train": dict(task="T", num_envs=2),
        "eval": dict(task="T", checkpoint=str(checkpoint), num_envs=2),
        "play": dict(task="T", checkpoint=str(checkpoint), num_envs=1, headless=True),
    }


def wait_for_status(job_id, target, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = jobs.runs_status(job_id)["state"]
        if st == target or st in ("succeeded", "failed", "cancelled", "timeout"):
            return st
        time.sleep(0.1)
    return jobs.runs_status(job_id)["state"]


def test_guard_blocks_train_eval_play_while_a_job_runs(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    ckpt = root / "model.pth"
    ckpt.write_text("x")
    monkeypatch.setenv("FAKE_MODE", "ok")

    with live_job() as (job_id, _job_dir, pid):
        def no_spawn(*args, **kwargs):
            raise AssertionError("subprocess.Popen called while another job is running")

        monkeypatch.setattr(jobs.subprocess, "Popen", no_spawn)

        for kind, kwargs in launch_kwargs(root, ckpt).items():
            out = getattr(jobs, "eval_" if kind == "eval" else kind)(
                python=str(fake_py), root=str(root), **kwargs
            )
            assert "error" in out, kind
            assert out["running_job_id"] == job_id, kind
            assert out["running_pid"] == pid, kind
            assert "allow_concurrent" in out["hint"], kind
        # nothing was launched: only the simulated job's dir exists
        assert [p.name for p in jobs.jobs_dir().iterdir()] == [job_id]
        assert jobs._active == {}


def test_dead_pid_record_does_not_block(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("FAKE_MODE", "ok")

    pid = dead_pid()
    stale = "20261029-120001-stale"
    d = write_running_record(stale, pid, [sys.executable, "-c", "pass"])
    assert jobs._read_job(d)["status"] == "running"
    assert jobs._job_alive(jobs._read_job(d)) is False
    assert jobs._running_job() is None

    res = jobs.train(python=str(fake_py), root=str(root), task="T", num_envs=2)
    assert set(res) == {"job_id", "log_dir", "command"}
    assert res["job_id"] != stale
    assert wait_for_status(res["job_id"], "succeeded", timeout=10) == "succeeded"


def test_allow_concurrent_bypasses_the_guard(tmp_path, monkeypatch):
    fake_py = make_fake_python(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    ckpt = root / "model.pth"
    ckpt.write_text("x")
    monkeypatch.setenv("FAKE_MODE", "ok")

    with live_job() as (job_id, _job_dir, _pid):
        started = []
        for kind, kwargs in launch_kwargs(root, ckpt).items():
            fn = getattr(jobs, "eval_" if kind == "eval" else kind)
            out = fn(python=str(fake_py), root=str(root), allow_concurrent=True, **kwargs)
            assert "error" not in out, (kind, out)
            assert out["job_id"] != job_id
            started.append(out["job_id"])
        for jid in started:
            assert wait_for_status(jid, "succeeded", timeout=10) == "succeeded"

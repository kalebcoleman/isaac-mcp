"""Isaac Lab job outcomes: crashes that exit 0, records, cancellation, discovery and recording."""

from pathlib import Path

import pytest
from test_isaaclab_script_layouts import _lab_3

ROOT = Path(__file__).resolve().parents[2]


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



def test_a_job_that_outlives_its_host_keeps_its_exit_code(monkeypatch, tmp_path):
    import json
    import subprocess

    from isaac_mcp.isaaclab import jobs

    for code, expected in ((0, "succeeded"), (3, "failed")):
        folder = tmp_path / f"job{code}"
        folder.mkdir()
        (folder / "job.json").write_text(json.dumps({"status": "running", "log_dir": str(folder), "kind": "eval"}))
        done = subprocess.run(jobs._exit_recording(["sh", "-c", f"exit {code}"], folder))
        assert done.returncode == code and (folder / jobs.EXIT_FILE).read_text() == str(code)
        record = json.loads((folder / "job.json").read_text())
        jobs._settle_orphan(folder, record)
        assert record["status"] == expected and record["exit_code"] == code and "note" not in record



def test_a_job_that_left_no_exit_code_is_still_reported_as_unknown_cause(tmp_path):
    from isaac_mcp.isaaclab import jobs

    (tmp_path / "job.json").write_text("{}")
    record = {"status": "running"}
    jobs._settle_orphan(tmp_path, record)
    assert record["status"] == "failed" and record["note"] == "host restarted"



def test_the_newest_checkpoint_is_never_a_torchscript_export(tmp_path):
    import os

    from isaac_mcp.isaaclab import jobs

    (tmp_path / "exported").mkdir()
    (tmp_path / "model_9.pt").write_text("x")
    (tmp_path / "exported" / "policy.pt").write_text("x")
    os.utime(tmp_path / "model_9.pt", (1, 1))
    assert jobs._latest_checkpoint(tmp_path).endswith("model_9.pt")



def test_task_ids_are_read_from_source_when_they_cannot_be_imported(tmp_path):
    from isaac_mcp.isaaclab import install

    tasks = tmp_path / "source" / "isaaclab_tasks" / "a"
    tasks.mkdir(parents=True)
    (tasks / "__init__.py").write_text('gym.register(\n    id="Isaac-Cartpole-v0", entry_point="x")\nid = "not-a-task"\n')
    assert install.scan_task_ids(tmp_path) == ["Isaac-Cartpole-v0"]



def test_the_module_loads_with_a_finished_job_left_from_an_earlier_host(tmp_path):
    import json
    import os
    import subprocess
    import sys

    job = tmp_path / "20261104-000000-Task"
    job.mkdir()
    (job / "job.json").write_text(json.dumps({"id": job.name, "status": "running", "pid": 2 ** 22 + 1, "log_dir": str(job)}))
    (job / "exit_code").write_text("0")
    subprocess.run([sys.executable, "-c", "import isaac_mcp.isaaclab.jobs"], check=True,
                   env={**os.environ, "ISAACLAB_JOBS_DIR": str(tmp_path)})
    assert json.loads((job / "job.json").read_text())["status"] == "succeeded"



def test_the_recording_shell_outlasts_sigterm_until_the_job_has_really_exited(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time

    from isaac_mcp.isaaclab import jobs

    slow = "import signal,sys,time; signal.signal(signal.SIGTERM, lambda *_: (time.sleep(1), sys.exit(7))); print(1, flush=True); time.sleep(30)"
    proc = subprocess.Popen(jobs._exit_recording([sys.executable, "-c", slow], tmp_path), stdout=subprocess.PIPE, start_new_session=True)
    proc.stdout.readline()
    os.killpg(proc.pid, signal.SIGTERM)
    time.sleep(0.3)
    assert proc.poll() is None  # the job is still shutting down, and its leader says so
    assert proc.wait(timeout=10) == 7 and (tmp_path / jobs.EXIT_FILE).read_text() == "7"



def test_a_job_window_is_found_on_a_process_in_its_group(monkeypatch):
    import subprocess
    import sys

    from isaac_mcp import screen

    leader = subprocess.Popen(["/bin/sh", "-c", f"{sys.executable} -c 'import time; time.sleep(20)' & wait"], start_new_session=True)
    try:
        import time

        time.sleep(0.2)
        child = int(subprocess.check_output(["pgrep", "-P", str(leader.pid)]).split()[0])
        monkeypatch.setattr(screen, "_visible_windows", lambda: [dict(id=5, pid=child, width=9, height=9, is_client=True)])
        assert screen.window_for_pid(leader.pid)["id"] == 5
        assert screen.window_for_pid(2 ** 22 + 1) is None
    finally:
        import os
        import signal

        os.killpg(leader.pid, signal.SIGKILL)



def test_a_launcher_that_did_not_exec_kit_is_stopped_with_it_and_not_a_port_conflict():
    import os
    import signal
    import subprocess
    import sys
    import time

    from isaac_mcp import sim_sessions

    leader = subprocess.Popen(["/bin/sh", "-c", f"{sys.executable} -c 'import time; time.sleep(30)'; echo done"], start_new_session=True)
    try:
        time.sleep(0.3)
        members = sim_sessions._group_members(leader.pid)
        assert len(members) == 1 and sim_sessions._in_session_group(members[0], leader.pid)
        assert not sim_sessions._in_session_group(os.getpid(), leader.pid)
        sim_sessions._signal_group_members(leader.pid, signal.SIGTERM)
        time.sleep(0.3)
        assert not sim_sessions._group_members(leader.pid)
    finally:
        leader.kill()
        leader.wait()

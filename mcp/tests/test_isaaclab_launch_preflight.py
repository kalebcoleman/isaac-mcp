"""Tests for the job guard, status and launch-time preflight reports."""

import json
import time
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isaac_mcp.isaaclab import jobs, tools


@pytest.fixture(autouse=True)
def isolated_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.delenv("ISAACLAB_TASK_MODULES", raising=False)
    monkeypatch.delenv("ISAACLAB_TASK_PATH", raising=False)
    jobs._active.clear()
    jobs._probe_cache.clear()
    yield
    jobs._active.clear()
    jobs._probe_cache.clear()


def lab_root(tmp_path: Path) -> Path:
    root = tmp_path / "lab"
    scripts = root / "scripts" / "reinforcement_learning"
    scripts.mkdir(parents=True)
    (scripts / "train.py").write_text("")
    (scripts / "play.py").write_text("")
    return root


def per_library_root(tmp_path: Path, library: str = "skrl") -> Path:
    root = tmp_path / "lab23"
    scripts = root / "scripts" / "reinforcement_learning" / library
    scripts.mkdir(parents=True)
    (scripts / "train.py").write_text("")
    (scripts / "play.py").write_text("")
    return root


def test_job_guard_names_allow_concurrent_and_keeps_its_hint(monkeypatch):
    other = {"job_id": "20260101-000000-x", "pid": 4242}
    monkeypatch.setattr(jobs, "_running_job", lambda: other)
    guard = jobs._concurrency_guard()
    assert "another Isaac Lab job is already running" in guard["error"]
    assert "allow_concurrent=true" in guard["hint"]

    # The tool layer must not replace a refusal's hint with the window paragraph.
    monkeypatch.setattr(tools._install, "locate_install",
                        lambda: {"python": "python3", "isaaclab_root": "/lab"})
    monkeypatch.setattr(tools._jobs, "play", lambda **kwargs: guard)
    out = json.loads(tools._launch(tools._jobs.play, task="T", checkpoint="c", headless=False))
    assert out["hint"] == guard["hint"]
    assert "window opens" not in out["hint"]


def test_runs_status_echoes_timeout_s_when_set(tmp_path):
    jd = jobs.jobs_dir() / "20260101-000000-timeout"
    jd.mkdir(parents=True)
    jobs._write_job(jd, {"job_id": jd.name, "status": "succeeded", "started_at": time.time() - 5,
                         "finished_at": time.time(), "timeout_s": 120, "log_dir": str(jd),
                         "run_dir": str(jd), "command": ["py", "train.py"]})
    out = jobs.runs_status(jd.name)
    assert out["timeout_s"] == 120

    jd2 = jobs.jobs_dir() / "20260101-000001-no-timeout"
    jd2.mkdir(parents=True)
    jobs._write_job(jd2, {"job_id": jd2.name, "status": "succeeded", "started_at": time.time(),
                          "finished_at": time.time(), "log_dir": str(jd2), "run_dir": str(jd2),
                          "command": ["py", "train.py"]})
    assert "timeout_s" not in jobs.runs_status(jd2.name)


def test_missing_rl_library_is_refused_before_launch(tmp_path, monkeypatch):
    root = per_library_root(tmp_path)
    monkeypatch.setattr(jobs, "installed_rl_libraries", lambda python: ["rsl_rl", "sb3"])
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not launch"))
    with pytest.raises(ValueError) as caught:
        jobs.train(python=str(tmp_path / "python"), root=str(root), task="T", rl_library="skrl",
                   allow_concurrent=True, allow_concurrent_gpu=True)
    message = str(caught.value)
    assert "skrl" in message and "rsl_rl, sb3" in message


def test_an_unknown_library_probe_does_not_block(tmp_path, monkeypatch):
    root = per_library_root(tmp_path)
    monkeypatch.setattr(jobs, "installed_rl_libraries", lambda python: None)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("spawned")))
    with pytest.raises(RuntimeError, match="spawned"):
        jobs.train(python=str(tmp_path / "python"), root=str(root), task="T", rl_library="skrl",
                   allow_concurrent=True, allow_concurrent_gpu=True)


def test_installed_rl_libraries_probes_a_real_interpreter():
    found = jobs.installed_rl_libraries(sys.executable)
    assert found is None or isinstance(found, list)


def test_tail_keeps_the_last_carriage_return_text_and_drops_bars(tmp_path):
    log = tmp_path / "stdout.log"
    log.write_text(
        "start\n"
        "10%|#         | 10/100 [00:01<00:09, 10.0it/s]\r100%|##########| 100/100 [00:10<00:00, 10.0it/s]\n"
        "|    | 0/500 [00:00<?, ?it/s]\n"
        " 100% \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501 64/64  [ 0:00:00 < 0:00:00 , 321 it/s ]\n"
        "loading\rtooled\n"
        "done\n"
    )
    # The newest text of a carriage-return line wins; a line that is only a bar is dropped.
    assert jobs._tail_file(log, 10) == ["start", "tooled", "done"]
    assert jobs._tail_file(log, 1) == ["done"]


def test_video_without_moviepy_is_refused_before_launch(tmp_path, monkeypatch):
    root = lab_root(tmp_path)
    monkeypatch.setattr(jobs, "installed_video_support",
                        lambda python: {"moviepy": False, "imageio-ffmpeg": True})
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not launch"))
    with pytest.raises(ValueError) as caught:
        jobs.train(python=str(tmp_path / "python"), root=str(root), task="T", extra_args=["--video"],
                   allow_concurrent=True, allow_concurrent_gpu=True)
    assert "moviepy" in str(caught.value) and "pip install moviepy imageio-ffmpeg" in str(caught.value)


def test_video_with_an_unknown_probe_does_not_block(tmp_path, monkeypatch):
    root = lab_root(tmp_path)
    monkeypatch.setattr(jobs, "installed_video_support", lambda python: None)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("spawned")))
    with pytest.raises(RuntimeError, match="spawned"):
        jobs.train(python=str(tmp_path / "python"), root=str(root), task="T", extra_args=["--video"],
                   allow_concurrent=True, allow_concurrent_gpu=True)


def test_an_unregistered_task_family_is_refused_with_near_matches(tmp_path, monkeypatch):
    root = lab_root(tmp_path)
    source = root / "source" / "isaaclab_tasks" / "cartpole"
    source.mkdir(parents=True)
    (source / "cartpole.py").write_text('gym.register(id="Isaac-Cartpole-v0", entry_point="x")\n')
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not launch"))
    with pytest.raises(ValueError) as caught:
        jobs.train(python=str(tmp_path / "python"), root=str(root), task="Isaac-Cartpole-v1",
                   allow_concurrent=True, allow_concurrent_gpu=True)
    assert "Isaac-Cartpole-v0" in str(caught.value) and "Nothing was started" in str(caught.value)


def test_an_id_outside_the_scanned_families_is_left_to_lab(tmp_path, monkeypatch):
    root = lab_root(tmp_path)
    source = root / "source" / "isaaclab_tasks" / "cartpole"
    source.mkdir(parents=True)
    (source / "cartpole.py").write_text('gym.register(id="Isaac-Cartpole-v0", entry_point="x")\n')
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("spawned")))
    with pytest.raises(RuntimeError, match="spawned"):
        jobs.train(python=str(tmp_path / "python"), root=str(root), task="Isaac-SomethingElse-v0",
                   allow_concurrent=True, allow_concurrent_gpu=True)


def test_configured_task_modules_disable_the_task_scan(tmp_path, monkeypatch):
    root = lab_root(tmp_path)
    source = root / "source" / "isaaclab_tasks" / "cartpole"
    source.mkdir(parents=True)
    (source / "cartpole.py").write_text('gym.register(id="Isaac-Cartpole-v0", entry_point="x")\n')
    monkeypatch.setenv("ISAACLAB_TASK_MODULES", "my_project_tasks")
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("spawned")))
    with pytest.raises(RuntimeError, match="spawned"):
        jobs.train(python=str(tmp_path / "python"), root=str(root), task="Isaac-Cartpole-v1",
                   allow_concurrent=True, allow_concurrent_gpu=True)

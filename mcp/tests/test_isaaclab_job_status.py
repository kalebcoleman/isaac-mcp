"""Isaac Lab job start (library selection from a checkpoint) and status (stderr, state filters)."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def job_starts(monkeypatch, tmp_path):
    from twinsmith.isaaclab import jobs

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
    from twinsmith.isaaclab.jobs import _library_from_checkpoint

    assert _library_from_checkpoint("/a/weights/model.pth", []) is None
    assert _library_from_checkpoint("/a/logs/custom/model.pth", []) is None


JOB_ID = "20261103-193322-Isaac-Reach-Franka"


def _job_with_logs(monkeypatch, tmp_path, status, stdout="", stderr="boom\n"):
    from twinsmith.isaaclab import jobs

    monkeypatch.setenv("TWINSMITH_JOBS_DIR", str(tmp_path))
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
    tree = ast.parse((ROOT / "mcp/templates/isaaclab_bridge_launch.py").read_text())
    loop = next(n for n in tree.body if isinstance(n, ast.While))
    calls = [ast.unparse(c.value) for c in loop.body if isinstance(c, ast.Expr) and isinstance(c.value, ast.Call)]
    assert calls == ["sim.step()", "_settings.set('/app/player/playSimulations', False)", "simulation_app.update()",
                     "_settings.set('/app/player/playSimulations', True)"]

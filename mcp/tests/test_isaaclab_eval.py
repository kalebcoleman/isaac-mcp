"""Scoring for isaaclab__eval: the wrapper arithmetic, the stopping rules and the result plumbing.

These use a fake environment and a fake tensor only; no Isaac Lab, torch or GPU is touched.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isaac_mcp.isaaclab import eval_runner, jobs


@pytest.fixture(autouse=True)
def isolated_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path / "jobs"))
    jobs._active.clear()
    yield
    jobs._active.clear()


class FakeEnv:
    """An environment whose steps are a fixed script of (reward, terminated, truncated, info)."""

    def __init__(self, script):
        self.script = list(script)
        self.index = 0
        self.closed = False
        self.reset_calls = 0

    def step(self, action):
        reward, terminated, truncated, info = self.script[self.index]
        self.index += 1
        return {"obs": 0}, reward, terminated, truncated, info

    def reset(self, *args, **kwargs):
        self.reset_calls += 1
        return {"obs": 0}, {}

    def close(self):
        self.closed = True


class NestedWrapper:
    """Stands in for an RL library's vec-env wrapper: it steps the base env and returns a 4-tuple."""

    def __init__(self, env):
        self.env = env

    def step(self, action):
        obs, reward, terminated, truncated, info = self.env.step(action)
        return obs, reward, (terminated or truncated), info


class FakeTensor:
    """Duck-types the torch surface the wrapper handles (device tensor -> detach().cpu().tolist())."""

    def __init__(self, values):
        self.values = values

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return list(self.values)


def test_return_and_length_arithmetic():
    scorer = eval_runner.Scorer(num_episodes=1, max_steps=None)
    for reward, done in [(1.0, False), (2.0, False), (3.0, True)]:
        scorer.observe([reward], [done], [False])
    assert scorer.completed == [(6.0, 3, None)]
    result = scorer.result()
    assert result["episodes"] == 1
    assert result["mean_return"] == 6.0
    assert result["min_return"] == result["max_return"] == 6.0
    assert result["mean_length"] == 3.0
    assert result["steps"] == 3


def test_several_parallel_envs_finish_at_different_steps():
    scorer = eval_runner.Scorer(num_episodes=3, max_steps=100)
    scorer.observe([1.0, 1.0, 1.0], [False, False, False], [False, False, False])
    scorer.observe([1.0, 1.0, 1.0], [True, False, False], [False, False, False])
    assert scorer.decide() is None  # one episode only, and not every environment has finished
    scorer.observe([1.0, 1.0, 1.0], [False, True, False], [False, False, False])
    assert scorer.decide() is None
    scorer.observe([1.0, 1.0, 1.0], [False, False, True], [False, False, False])
    assert [entry[:2] for entry in scorer.completed] == [(2.0, 2), (3.0, 3), (4.0, 4)]
    assert scorer.decide() == "episodes"
    scorer.stop("episodes")
    result = scorer.result()
    assert result["episodes"] == 3
    assert result["mean_return"] == 3.0
    assert result["mean_length"] == 3.0
    assert result["stopped_by"] == "episodes"
    assert result["num_envs"] == 3


def test_truncation_ends_an_episode_and_is_counted():
    scorer = eval_runner.Scorer(num_episodes=1, max_steps=None)
    scorer.observe([4.0], [False], [True])
    assert scorer.completed == [(4.0, 1, None)]
    assert scorer.decide() == "episodes"


def test_no_finished_episode_reports_no_number():
    scorer = eval_runner.Scorer(num_episodes=5, max_steps=2)
    scorer.observe([1.0], [False], [False])
    scorer.observe([1.0], [False], [False])
    reason = scorer.decide()
    assert reason == "max_steps"
    scorer.stop(reason)
    result = scorer.result()
    assert result["episodes"] == 0
    assert result["mean_return"] is None and result["min_return"] is None
    assert result["steps"] == 2
    assert result["stopped_by"] == "max_steps"
    assert "No episode finished" in result["note"]


def test_success_rate_comes_from_the_environment_when_it_reports_one():
    scorer = eval_runner.Scorer(num_episodes=2, max_steps=None)
    scorer.observe([1.0, 1.0], [True, True], [False, False], info={"success": [True, False]})
    result = scorer.result()
    assert result["success_rate"] == 0.5
    plain = eval_runner.Scorer(num_episodes=1, max_steps=None)
    plain.observe([1.0], [True], [False], info={})
    assert "success_rate" not in plain.result()


def test_a_reward_that_is_not_per_environment_is_refused_not_counted_as_zero():
    scorer = eval_runner.Scorer(num_episodes=1, max_steps=None)
    with pytest.raises(RuntimeError, match="cannot be scored"):
        scorer.observe({"agent_0": 1.0}, [True], [False])
    assert scorer.completed == []


def test_fake_tensor_on_any_device_is_understood():
    scorer = eval_runner.Scorer(num_episodes=1, max_steps=None)
    scorer.observe(FakeTensor([1.5, 2.5]), FakeTensor([False, True]), FakeTensor([False, False]))
    assert scorer.completed == [(2.5, 1, None)]


def test_wrapping_counts_one_step_per_simulator_step_not_per_wrapper():
    hooks = eval_runner._Hooks(eval_runner.Scorer(num_episodes=5, max_steps=10))
    env = FakeEnv([(1.0, False, False, {})] * 4)
    hooks.wrap_env(env)
    outer = NestedWrapper(env)
    for _ in range(2):
        outer.step(0)
    assert hooks.scorer.steps == 2
    assert hooks.scorer.returns == [2.0]
    hooks.close()
    assert env.closed is True


def test_runner_stops_a_play_loop_through_is_running_and_closes_cleanly():
    class App:
        def __init__(self):
            self.running = True
            self.closed = False

        def is_running(self):
            return self.running

        def close(self):
            self.closed = True

    scorer = eval_runner.Scorer(num_episodes=1, max_steps=None)
    hooks = eval_runner._Hooks(scorer)
    app = App()
    hooks.adopt_app(app)
    env = FakeEnv([(2.0, True, False, {})])
    hooks.wrap_env(env)
    env.step(0)
    assert scorer.finished is True
    assert app.is_running() is False  # play.py's loop exits on its next check, so it closes itself
    hooks.close()
    assert env.closed is True and app.closed is True


def test_result_is_published_at_stop_before_a_close_that_may_end_the_process(tmp_path):
    target = tmp_path / "eval_result.json"
    scorer = eval_runner.Scorer(num_episodes=1, max_steps=None)
    hooks = eval_runner._Hooks(scorer, str(target))
    env = FakeEnv([(3.0, True, False, {})])
    hooks.wrap_env(env)
    env.step(0)
    # The target was met during the step, so a reader already has the file even if app.close() exits first.
    data = json.loads(target.read_text())
    assert data["episodes"] == 1 and data["mean_return"] == 3.0
    assert data["stopped_by"] == "episodes"


def test_atomic_write_leaves_one_complete_file_and_no_temporary(tmp_path):
    target = tmp_path / "eval_result.json"
    eval_runner._write_atomic(target, {"episodes": 3, "mean_return": 1.5})
    assert json.loads(target.read_text()) == {"episodes": 3, "mean_return": 1.5}
    eval_runner._write_atomic(target, {"episodes": 0, "mean_return": None})
    assert json.loads(target.read_text()) == {"episodes": 0, "mean_return": None}
    assert [p.name for p in target.parent.iterdir()] == ["eval_result.json"]


def test_command_building_keeps_play_arguments_after_the_runner_options():
    command = eval_runner.build_command(
        "/lab/bin/python", "/lab/scripts/reinforcement_learning/rsl_rl/play.py",
        ["--task", "Isaac-Cartpole-v0", "--checkpoint", "/lab/model.pt"],
        "/jobs/x/eval_result.json", num_episodes=7, max_steps=900,
    )
    assert command[0] == "/lab/bin/python"
    assert command[1].endswith("eval_runner.py")
    assert command[command.index("--episodes") + 1] == "7"
    assert command[command.index("--max-steps") + 1] == "900"
    assert command[command.index("--play") + 1].endswith("play.py")
    assert command[command.index("--play") + 2] == "--"
    assert command[-4:] == ["--task", "Isaac-Cartpole-v0", "--checkpoint", "/lab/model.pt"]


def test_eval_builds_the_runner_command_with_its_options_and_play_flags(tmp_path, monkeypatch):
    scripts = tmp_path / "lab" / "scripts" / "reinforcement_learning"
    scripts.mkdir(parents=True)
    (scripts / "train.py").write_text("")
    (scripts / "play.py").write_text("")
    checkpoint = tmp_path / "lab" / "model.pth"
    checkpoint.write_bytes(b"x")
    captured = {}
    monkeypatch.setattr(jobs, "_start_job", lambda *a, **kw: captured.update(argv=a[4], **kw) or {"job_id": "x"})
    jobs.eval_(python="/lab/bin/python", root=str(tmp_path / "lab"), task="T", checkpoint=str(checkpoint),
               num_envs=4, num_episodes=7, max_steps=900)
    assert captured["eval_options"] == {"num_episodes": 7, "max_steps": 900}
    assert captured["argv"][:2] == ["--task", "T"]
    assert "--checkpoint" in captured["argv"]


def test_eval_refuses_bad_episode_and_step_bounds(tmp_path):
    scripts = tmp_path / "lab" / "scripts" / "reinforcement_learning"
    scripts.mkdir(parents=True)
    (scripts / "train.py").write_text("")
    (scripts / "play.py").write_text("")
    checkpoint = tmp_path / "lab" / "model.pth"
    checkpoint.write_bytes(b"x")
    for kwargs in ({"num_episodes": 0}, {"num_episodes": True}, {"max_steps": -1}, {"max_steps": 0}):
        with pytest.raises(ValueError):
            jobs.eval_(python="python", root=str(tmp_path / "lab"), task="T",
                       checkpoint=str(checkpoint), **kwargs)


def test_runs_status_reports_the_result_after_the_starting_host_is_gone():
    job_dir = jobs.jobs_dir() / "20260101-000000-Isaac-Cartpole"
    job_dir.mkdir(parents=True)
    now = time.time()
    jobs._write_job(job_dir, {
        "id": job_dir.name, "job_id": job_dir.name, "kind": "eval", "task": "Isaac-Cartpole",
        "command": ["x"], "pid": 999999, "pid_start": None, "status": "succeeded", "exit_code": 0,
        "started_at": now - 10, "finished_at": now, "log_dir": str(job_dir), "run_dir": str(job_dir),
    })
    (job_dir / "eval_result.json").write_text(json.dumps({
        "episodes": 20, "mean_return": 123.5, "std_return": 4.0, "min_return": 100.0,
        "max_return": 130.0, "mean_length": 200.0, "steps": 400, "stopped_by": "episodes",
    }))
    status = jobs.runs_status(job_dir.name)
    assert status["state"] == "succeeded"
    assert status["eval_result"]["episodes"] == 20
    assert status["eval_result"]["mean_return"] == 123.5
    assert status["eval_result"]["stopped_by"] == "episodes"

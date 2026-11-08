import json
import sys
from pathlib import Path

import pytest

from isaac_mcp.isaaclab import check_runner as cr
from isaac_mcp.isaaclab import jobs, preflight as pf


def test_stats_flag_nan_and_instant_resets():
    st = cr.CheckStats(3)
    for _ in range(3):
        st.observe({"policy": [[0.0, float("nan")], [1.0, 2.0]]}, [0.5, 0.5], [True, True], [False, False])
    out = st.result("T")
    assert not out["ok"] and out["non_finite"]["observations"] == 3
    assert any("episode ends in the first" in p for p in out["problems"])
    assert out["observation_shapes"] == {"policy": [2, 2]} and out["num_envs"] == 2


def test_stats_clean_run_is_ok_and_reports_reward():
    st = cr.CheckStats(2)
    st.observe([[1.0], [2.0]], [1.0, 3.0], [False, False], [False, False], 0.01)
    st.observe([[1.0], [2.0]], [2.0, 4.0], [False, False], [False, False], 0.03)
    out = st.result("T")
    assert out["ok"] and out["reward"]["mean"] == 2.5 and out["reward"]["max"] == 4.0
    assert abs(out["step_time_ms"] - 20.0) < 1e-6 and st.finished


def test_stats_short_run_and_zero_rewards_are_reported():
    st = cr.CheckStats(5)
    st.observe([[1.0]], [0.0], [False], [False])
    msgs = " ".join(st.result("T")["problems"])
    assert "ended after 1 of 5" in msgs and "exactly 0" in msgs
    assert not cr.CheckStats(1).result("T")["ok"]


def test_build_command_shapes():
    cmd = cr.build_command("py", "/x/zero_agent.py", ["--task", "T"], "/o.json", mode="record", steps=201,
                           video_dir="/v", video_length=200)
    assert cmd[2:4] == ["--mode", "record"] and "--video-dir" in cmd
    assert cmd.index("--script") < cmd.index("--") and cmd[cmd.index("--") + 1:] == ["--task", "T"]


def test_runner_end_to_end_with_fake_gymnasium(tmp_path, monkeypatch):
    """Run the runner against a fake gymnasium and a fake agent script; no Isaac Lab needed."""
    fake = tmp_path / "fakes"
    fake.mkdir()
    (fake / "gymnasium.py").write_text(
        "class _Env:\n"
        "    def __init__(s): s.n=0; s.action_space=type('A',(),{'sample':lambda self:[0.0,0.0]})()\n"
        "    def step(s,a):\n"
        "        s.n+=1; return [[0.0,1.0]], [1.0], [False], [False], {}\n"
        "    def close(s): pass\n"
        "def make(*a, **k): return _Env()\n")
    script = tmp_path / "agent.py"
    script.write_text("import gymnasium\nenv = gymnasium.make('T')\nfor _ in range(10): env.step([0.0])\nenv.close()\n")
    out = tmp_path / "check_result.json"
    monkeypatch.syspath_prepend(str(fake))
    monkeypatch.delitem(sys.modules, "gymnasium", raising=False)
    monkeypatch.setattr(cr, "gpu_mib_for_pid", lambda pid: 1234)
    monkeypatch.setattr(cr, "_prepare", lambda: None)
    assert cr.main(["--steps", "4", "--output", str(out), "--script", str(script), "--", "--task", "T"]) == 0
    data = json.loads(out.read_text())
    assert data["task"] == "T" and data["steps"] >= 4 and data["gpu_mib"] == 1234
    assert data["gpu_mib_per_env_upper_bound"] == 1234.0 and data["action_shape"] == [2]


def test_suggest_num_envs_is_conservative_and_needs_a_measurement():
    assert pf.suggest_num_envs(30000, None) is None
    m = {"gpu_mib": 8000, "num_envs": 100}
    n = pf.suggest_num_envs(30000, m)
    assert n == int((30000 * 0.8 - 4000) // 40)
    assert pf.suggest_num_envs(1000, m) == 0


def test_preflight_reasons(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "gpu_state", lambda: {"name": "G", "total_mib": 1000, "used_mib": 900, "free_mib": 100, "processes": []})
    monkeypatch.setattr(jobs, "_running_job", lambda: {"job_id": "j1", "pid": 5})
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: [{"port": 8766}])
    monkeypatch.setattr(jobs, "jobs_dir", lambda: tmp_path)
    out = pf.preflight("Some-Task")
    assert out["can_start"] is False and len(out["reasons"]) == 3
    assert out["suggested_num_envs"] is None and "env_check" in out["suggestion_note"]


def test_preflight_uses_a_measured_env_check(monkeypatch, tmp_path):
    jd = tmp_path / "20261107-000000-T"
    jd.mkdir()
    (jd / "job.json").write_text(json.dumps({"job_id": jd.name, "kind": "env_check", "task": "T"}))
    (jd / "check_result.json").write_text(json.dumps({"gpu_mib": 8000, "num_envs": 100, "ok": True}))
    monkeypatch.setattr(pf, "gpu_state", lambda: {"name": "G", "total_mib": 90000, "used_mib": 0, "free_mib": 90000, "processes": []})
    monkeypatch.setattr(jobs, "_running_job", lambda: None)
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: [])
    monkeypatch.setattr(jobs, "jobs_dir", lambda: tmp_path)
    out = pf.preflight("T")
    assert out["can_start"] and out["measured"]["num_envs"] == 100 and out["suggested_num_envs"] > 100


def test_check_job_rejects_bad_agent_and_counts():
    with pytest.raises(ValueError, match="agent"):
        jobs.env_check("py", "/r", "T", agent="smart")
    with pytest.raises(ValueError):
        jobs.env_check("py", "/r", "T", num_envs=0)
    with pytest.raises(ValueError):
        jobs.record("py", "/r", "T", video_length=0)


def test_status_reads_check_result_and_videos(monkeypatch, tmp_path):
    jd = tmp_path / "20261107-000000-T"
    (jd / "videos").mkdir(parents=True)
    (jd / "videos" / "rl-video-step-0.mp4").write_bytes(b"x")
    (jd / "check_result.json").write_text(json.dumps({"steps": 3, "ok": True}))
    assert jobs._check_result(jd) == {"steps": 3, "ok": True}
    (jd / "job.json").write_text(json.dumps({"job_id": jd.name, "kind": "record", "log_dir": str(jd), "status": "succeeded"}))
    assert jobs._crashed_with_exit_zero(jd) is None


def test_runner_stops_a_loop_that_checks_sim_is_running(tmp_path, monkeypatch):
    """Lab 3's agents loop on env.unwrapped.sim.is_running(); the runner must end that loop at the target."""
    fake = tmp_path / "fakes"
    fake.mkdir()
    (fake / "gymnasium.py").write_text(
        "class _Sim:\n    def is_running(s): return True\n"
        "class _Env:\n"
        "    def __init__(s): s.unwrapped=s; s.sim=_Sim(); s.action_space=type('A',(),{'sample':lambda self:[0.0]})()\n"
        "    def step(s,a): return [[0.0]], [1.0], [False], [False], {}\n"
        "    def close(s): pass\n"
        "def make(*a, **k): return _Env()\n")
    script = tmp_path / "agent.py"
    script.write_text("import gymnasium\nenv = gymnasium.make('T')\nn=0\n"
                      "while env.unwrapped.sim.is_running():\n    env.step([0.0]); n+=1\n    assert n < 1000\n")
    out = tmp_path / "r.json"
    monkeypatch.syspath_prepend(str(fake))
    monkeypatch.delitem(sys.modules, "gymnasium", raising=False)
    monkeypatch.setattr(cr, "_prepare", lambda: None)
    monkeypatch.setattr(cr, "gpu_mib_for_pid", lambda pid: None)
    assert cr.main(["--steps", "7", "--output", str(out), "--script", str(script), "--", "--task", "T"]) == 0
    assert json.loads(out.read_text())["steps"] == 7


def test_runner_raises_past_the_target_for_a_loop_with_no_stop_check(tmp_path, monkeypatch):
    fake = tmp_path / "fakes"
    fake.mkdir()
    (fake / "gymnasium.py").write_text(
        "class _Env:\n    def __init__(s): s.action_space=type('A',(),{'sample':lambda self:[0.0]})()\n"
        "    def step(s,a): return [[0.0]], [1.0], [False], [False], {}\n    def close(s): pass\n"
        "def make(*a, **k): return _Env()\n")
    script = tmp_path / "agent.py"
    script.write_text("import gymnasium\nenv = gymnasium.make('T')\nfor _ in range(10**6): env.step([0.0])\n")
    out = tmp_path / "r.json"
    monkeypatch.syspath_prepend(str(fake))
    monkeypatch.delitem(sys.modules, "gymnasium", raising=False)
    monkeypatch.setattr(cr, "_prepare", lambda: None)
    monkeypatch.setattr(cr, "gpu_mib_for_pid", lambda pid: None)
    assert cr.main(["--steps", "5", "--output", str(out), "--script", str(script), "--", "--task", "T"]) == 0
    assert json.loads(out.read_text())["steps"] == 5


def test_native_agent_video_detection(tmp_path):
    assert not jobs._native_agent_video(tmp_path)
    d = tmp_path / "source" / "isaaclab_rl" / "isaaclab_rl" / "entrypoints"
    d.mkdir(parents=True)
    (d / "simple_agents.py").write_text("")
    assert jobs._native_agent_video(tmp_path)


def test_a_script_that_ends_with_systemexit_zero_is_not_a_problem(tmp_path, monkeypatch):
    fake = tmp_path / "fakes"
    fake.mkdir()
    (fake / "gymnasium.py").write_text(
        "class _Env:\n    def __init__(s): s.action_space=type('A',(),{'sample':lambda self:[0.0]})()\n"
        "    def step(s,a): return [[0.0]], [1.0], [False], [False], {}\n    def close(s): pass\n"
        "def make(*a, **k): return _Env()\n")
    script = tmp_path / "agent.py"
    script.write_text("import gymnasium\nenv = gymnasium.make('T')\nfor _ in range(3): env.step([0.0])\nraise SystemExit(0)\n")
    out = tmp_path / "r.json"
    monkeypatch.syspath_prepend(str(fake))
    monkeypatch.delitem(sys.modules, "gymnasium", raising=False)
    monkeypatch.setattr(cr, "_prepare", lambda: None)
    monkeypatch.setattr(cr, "gpu_mib_for_pid", lambda pid: None)
    cr.main(["--steps", "3", "--output", str(out), "--script", str(script), "--", "--task", "T"])
    data = json.loads(out.read_text())
    assert data["ok"] and data["problems"] == []


def test_record_with_native_video_does_not_pass_viz_none(monkeypatch, tmp_path):
    d = tmp_path / "source" / "isaaclab_rl" / "isaaclab_rl" / "entrypoints"
    d.mkdir(parents=True)
    (d / "simple_agents.py").write_text("")
    seen = {}
    monkeypatch.setattr(jobs, "_start_job", lambda python, root, kind, task, argv, **kw: seen.update(argv=argv, kind=kind) or {"job_id": "j"})
    jobs.record("py", tmp_path, "T", video_length=50)
    assert "--video" in seen["argv"] and seen["argv"][seen["argv"].index("--viz") + 1] == "kit"
    assert "--max_steps" in seen["argv"]


def test_mtime_of_missing_file_is_zero(tmp_path):
    assert jobs._mtime(tmp_path / "nope.mp4") == 0.0
    (tmp_path / "a.mp4").write_bytes(b"x")
    assert jobs._mtime(tmp_path / "a.mp4") > 0

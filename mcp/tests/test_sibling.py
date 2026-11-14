"""Pinned sibling checkouts: no upstream tool, network or GPU needed."""

import json

import pytest

from twinsmith import sibling
from twinsmith.isaaclab import jobs

from sibling_testing import calls, fake_checkout, wait_for


@pytest.fixture(autouse=True)
def isolated_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINSMITH_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(jobs, "_probe_kit_bridges", lambda: [])
    jobs._active.clear()


def test_pin_match(tmp_path):
    commit = fake_checkout(tmp_path / "tools/thing")
    info = sibling.Checkout("thing", None, commit[:7], "https://example.invalid/thing", workspace=tmp_path).info()
    assert info["commit"] == commit and info["pin_match"] and info["environment_present"]
    assert info["warnings"] == [] and "setup" not in info
    assert set(info) == {"checkout", "commit", "pin", "pin_match", "environment_present", "warnings"}


def test_pin_mismatch_warns(tmp_path):
    fake_checkout(tmp_path / "tools/thing")
    info = sibling.Checkout("thing", None, "0123abc", "https://example.invalid/thing", workspace=tmp_path).info()
    assert not info["pin_match"] and "differs from tested pin 0123abc" in info["warnings"][0]


def test_missing_checkout_gives_setup_and_installs_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("TWINSMITH_THING_ROOT", raising=False)
    checkout = sibling.Checkout("thing", "TWINSMITH_THING_ROOT", "0123abc", "https://example.invalid/thing", workspace=tmp_path)
    info = checkout.info()
    assert info["commit"] is None and not info["environment_present"]
    assert "https://example.invalid/thing" in info["setup"] and "0123abc" in info["setup"]
    assert "TWINSMITH_THING_ROOT" in info["setup"] and "never installs" in info["setup"]
    assert not (tmp_path / "tools").exists()


def test_root_comes_from_the_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINSMITH_THING_ROOT", str(tmp_path / "elsewhere"))
    assert sibling.Checkout("thing", "TWINSMITH_THING_ROOT", "x", "u", workspace=tmp_path).root == tmp_path / "elsewhere"


def test_environment_is_scrubbed(monkeypatch):
    monkeypatch.setenv("ROS_DOMAIN_ID", "7")
    monkeypatch.setenv("SOME_SECRET", "x")
    monkeypatch.setenv("HF_TOKEN", "token")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    env = sibling.environment(["HF_TOKEN", "ROS_DOMAIN_ID"], {"ROS_LOCALHOST_ONLY": "1", "MUJOCO_GL": "egl"})
    assert env["HF_TOKEN"] == "token" and env["CUDA_VISIBLE_DEVICES"] == "0" and env["MUJOCO_GL"] == "egl"
    assert "PATH" in env and "SOME_SECRET" not in env
    assert not any(key.startswith("ROS_") for key in env)


def test_run_captures_and_times_out(tmp_path):
    root = tmp_path / "tools/thing"
    fake_checkout(root)
    done = sibling.run([root / ".venv/bin/python", "hello"], cwd=tmp_path, stdout_chars=sibling.WHOLE)
    assert done["exit_code"] == 0 and json.loads(done["stdout"])["argv"][1:] == ["hello"]
    assert len(sibling.run([root / ".venv/bin/python", "hello"], cwd=tmp_path, stdout_chars=10)["stdout"]) == 10
    slow = sibling.run(["sleep", "5"], cwd=tmp_path, timeout_s=0.2)
    assert slow["timed_out"] and slow["exit_code"] is None


def test_start_job_is_a_job_the_jobs_tool_follows(tmp_path, monkeypatch):
    monkeypatch.setenv("ROS_DOMAIN_ID", "7")
    root = tmp_path / "tools/thing"
    fake_checkout(root)
    started = sibling.start_job([root / ".venv/bin/python", "go"], task="thing-one", kind="thing-run", cwd=tmp_path,
                                env_set={"EXTRA": "1"}, timeout_s=60)
    assert started["ok"] and set(started) == {"ok", "job_id", "log_dir", "command"}
    status = wait_for(started["job_id"])
    assert status["state"] == "succeeded" and status["exit_code"] == 0
    record = jobs._read_job(jobs.jobs_dir() / started["job_id"])
    assert record["kind"] == "thing-run" and record["command"] == [str(root / ".venv/bin/python"), "go"]
    (call,) = calls(root)
    assert call["env"]["EXTRA"] == "1" and "ROS_DOMAIN_ID" not in call["env"]


def test_answer_envelope():
    info = {"commit": "abc", "pin": "abd", "pin_match": False, "warnings": ["differs"]}
    good = sibling.answer("t", "a", info, exit_code=0, result={"x": 1}, warnings=["more"])
    assert good == {"ok": True, "tool": "t", "action": "a", "commit": "abc", "pin": "abd", "pin_match": False,
                    "exit_code": 0, "result": {"x": 1}, "warnings": ["differs", "more"]}
    assert sibling.answer("t", "a", info, error="no", code="C")["ok"] is False

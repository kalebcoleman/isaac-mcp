"""Isaac Lab script layouts: flags are read from the scripts, and argv is built to match."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


# The flags of Isaac Lab 2.3.2's rsl_rl scripts, strictly parsed so a flag it lacks fails here instead of reaching Hydra.
STAND_IN_2_3 = """import argparse, json, os, sys
p = argparse.ArgumentParser()
p.add_argument("--task")
p.add_argument("--num_envs")
p.add_argument("--seed")
p.add_argument("--max_iterations")
p.add_argument("--checkpoint")
p.add_argument("--device")
p.add_argument("--headless", action="store_true")
p.add_argument("--video", action="store_true")
args = p.parse_args()
print(json.dumps(dict(script=sys.argv[0], args=vars(args), HEADLESS=os.environ.get("HEADLESS"))))
"""


def _lab_2_3(tmp_path):
    root = tmp_path / "IsaacLab23"
    scripts = root / "scripts/reinforcement_learning"
    for library in ("rl_games", "rsl_rl", "sb3", "skrl"):
        (scripts / library).mkdir(parents=True)
        (scripts / library / "train.py").write_text(STAND_IN_2_3)
        (scripts / library / "play.py").write_text(STAND_IN_2_3)
    (scripts / "ray").mkdir()  # 2.3.2 has this folder, without train.py or play.py
    (scripts / "ray/tuner.py").write_text("")
    (root / "VERSION").write_text("2.3.2\n")
    return root


def _lab_3(tmp_path):
    root = tmp_path / "IsaacLab3"
    (root / "scripts/reinforcement_learning").mkdir(parents=True)
    for name in ("train.py", "play.py"):
        (root / "scripts/reinforcement_learning" / name).write_text("")
    return root


def test_layout_is_read_from_the_files(tmp_path):
    from isaac_mcp.isaaclab.install import lab_profile

    lab3 = lab_profile(_lab_3(tmp_path))
    assert lab3["layout"] == "unified" and lab3["libraries"] == [] and lab3["scripts"][""]["train"]["flags"] is None
    lab23 = lab_profile(_lab_2_3(tmp_path))
    assert lab23["layout"] == "per_library" and lab23["libraries"] == ["rl_games", "rsl_rl", "sb3", "skrl"]
    assert lab23["lab_version"] == "2.3.2" and "--headless" in lab23["scripts"]["rsl_rl"]["play"]["flags"]
    assert "--viz" not in lab23["scripts"]["rsl_rl"]["train"]["flags"]
    assert lab_profile(tmp_path / "nothing")["layout"] == "none" and lab_profile(None)["layout"] == "none"


def test_flags_include_helpers_and_app_launcher_and_are_unknown_when_a_source_is_missing(tmp_path):
    from isaac_mcp.isaaclab.install import lab_profile

    root = tmp_path / "lab"
    folder = root / "scripts/reinforcement_learning/rsl_rl"
    folder.mkdir(parents=True)
    script = "import cli_args\nAppLauncher.add_app_launcher_args(parser)\nparser.add_argument(\n    '--task')\n"
    (folder / "train.py").write_text(script)
    (folder / "play.py").write_text(script)
    (folder / "cli_args.py").write_text('arg_group.add_argument("--resume")\n')
    assert lab_profile(root)["scripts"]["rsl_rl"]["train"]["flags"] is None, "AppLauncher's source is absent"
    launcher = root / "source/isaaclab/isaaclab/app"
    launcher.mkdir(parents=True)
    (launcher / "app_launcher.py").write_text('group.add_argument(\n  "--headless",\n)\ngroup.add_argument("--device")\n')
    (folder / "extra").mkdir()  # a new stamp on the scripts folder, so the profile is read again
    root2 = root.parent / "lab2"
    root.rename(root2)
    assert lab_profile(root2)["scripts"]["rsl_rl"]["train"]["flags"] == ["--device", "--headless", "--resume", "--task"]


def test_a_changed_checkout_is_read_again(tmp_path):
    from isaac_mcp.isaaclab.install import lab_profile

    root = tmp_path / "lab"
    (root / "scripts/reinforcement_learning/rsl_rl").mkdir(parents=True)
    assert lab_profile(root)["layout"] == "none"
    folder = root / "scripts/reinforcement_learning/skrl"
    folder.mkdir()
    (folder / "train.py").write_text("")
    (folder / "play.py").write_text("")
    os.utime(folder.parent, ns=(1, 1))
    assert lab_profile(root)["libraries"] == ["skrl"]


@pytest.fixture
def captured_jobs(monkeypatch):
    from isaac_mcp.isaaclab import jobs

    calls = []

    def start(python, root, kind, task, argv, **kwargs):
        calls.append((kind, argv, kwargs))
        return dict(job_id="j")

    monkeypatch.setattr(jobs, "_start_job", start)
    return SimpleNamespace(jobs=jobs, calls=calls)


def test_lab_3_argv_is_byte_identical(captured_jobs, tmp_path):
    jobs, root = captured_jobs.jobs, _lab_3(tmp_path)
    checkpoint = root / "model.pth"
    checkpoint.write_bytes(b"x")
    jobs.train("py", root, "T", num_envs=8, max_iterations=2, seed=1, device="cuda:0", rl_library="rl_games",
               headless=True, extra_args=["a.b=1"])
    assert captured_jobs.calls[-1][1] == ["--task", "T", "--num_envs", "8", "--rl_library", "rl_games", "--max_iterations", "2",
                                          "--seed", "1", "--device", "cuda:0", "--viz", "none", "a.b=1"]
    assert "library" not in captured_jobs.calls[-1][2]
    jobs.eval_("py", root, "T", str(checkpoint), num_envs=2)
    assert captured_jobs.calls[-1][1] == ["--task", "T", "--checkpoint", str(checkpoint), "--num_envs", "2", "--viz", "none"]
    jobs.play("py", root, "T", str(checkpoint), rl_library="skrl", extra_args=["--video"])
    assert captured_jobs.calls[-1][1] == ["--task", "T", "--checkpoint", str(checkpoint), "--num_envs", "1",
                                          "--rl_library", "skrl", "--viz", "kit", "--video"]
    assert "library" not in captured_jobs.calls[-1][2]


def test_lab_2_3_needs_a_library_and_names_the_choices(captured_jobs, tmp_path):
    jobs, root = captured_jobs.jobs, _lab_2_3(tmp_path)
    with pytest.raises(ValueError, match=r"Isaac Lab 2\.3\.2 .*needs rl_library.*rl_games, rsl_rl, sb3, skrl"):
        jobs.train("py", root, "T")
    with pytest.raises(ValueError, match="has no ray scripts"):
        jobs.train("py", root, "T", rl_library="ray")
    assert captured_jobs.calls == []


@pytest.mark.parametrize("flag, alternative", [("--viz=kit", "headless"), ("--visualizer=kit", "headless"),
                                               ("--rl_library=skrl", "rl_library"), ("--nope", "It accepts")])
def test_undeclared_flags_are_refused_naming_the_alternative(captured_jobs, tmp_path, flag, alternative):
    with pytest.raises(ValueError, match=rf"skrl/train\.py does not accept {flag.split('=')[0]}\. .*{alternative}"):
        captured_jobs.jobs.train("py", _lab_2_3(tmp_path), "T", rl_library="skrl", extra_args=[flag])
    assert captured_jobs.calls == []


def test_a_hydra_override_is_not_a_flag(captured_jobs, tmp_path):
    captured_jobs.jobs.train("py", _lab_2_3(tmp_path), "T", rl_library="skrl", extra_args=["agent.x=1"])
    assert "agent.x=1" in captured_jobs.calls[-1][1]


def test_a_script_that_lacks_a_generated_flag_is_refused(captured_jobs, tmp_path):
    root = _lab_2_3(tmp_path)
    (root / "scripts/reinforcement_learning/skrl/train.py").write_text("parser.add_argument('--task')\n")
    with pytest.raises(ValueError, match="does not accept --num_envs"):
        captured_jobs.jobs.train("py", root, "T", rl_library="skrl")


def test_lab_2_3_checkpoint_path_selects_a_folder_that_exists(captured_jobs, tmp_path):
    jobs, root = captured_jobs.jobs, _lab_2_3(tmp_path)
    checkpoint = root / "logs/rsl_rl/reach/run/model.pth"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"x")
    result = jobs.play("py", root, "T", str(checkpoint), headless=True)
    kind, argv, kwargs = captured_jobs.calls[-1]
    assert result["rl_library_from_checkpoint_path"] == "rsl_rl" and kwargs["library"] == "rsl_rl"
    assert "--rl_library" not in argv and "--viz" not in argv and "--headless" in argv
    torchrl = root / "logs/torchrl/m.pth"  # a Lab 3 library, not a folder here
    torchrl.parent.mkdir(parents=True)
    torchrl.write_bytes(b"x")
    with pytest.raises(ValueError, match="needs rl_library"):
        jobs.play("py", root, "T", str(torchrl))


def _run_job(monkeypatch, tmp_path, launch):
    from isaac_mcp.isaaclab import jobs

    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path / "jobs"))
    result = launch(jobs)
    assert "error" not in result, result
    process = jobs._active[result["job_id"]]
    process.wait(timeout=30)
    return Path(result["log_dir"])


@pytest.mark.parametrize("headless, extra, flag, env", [
    (True, [], True, "1"),
    (False, [], False, "0"),
    (True, ["--video"], False, "1"),
])
def test_a_real_process_accepts_the_lab_2_3_argv(monkeypatch, tmp_path, headless, extra, flag, env):
    from isaac_mcp.isaaclab import jobs as jobs_module

    # This stand-in interpreter is not a Lab venv: its argv is what is under test, so let the
    # launch-time library and video probes answer as an installed Lab environment would.
    monkeypatch.setattr(jobs_module, "installed_rl_libraries", lambda python: ["rsl_rl"])
    monkeypatch.setattr(jobs_module, "installed_video_support",
                        lambda python: {"moviepy": True, "imageio-ffmpeg": True})
    root = _lab_2_3(tmp_path)
    logs = _run_job(monkeypatch, tmp_path, lambda jobs: jobs.train(
        sys.executable, root, "Isaac-Reach-Franka-v0", num_envs=4, max_iterations=1, seed=7, device="cuda:0",
        rl_library="rsl_rl", headless=headless, extra_args=extra, allow_concurrent=True, allow_concurrent_gpu=True))
    assert (logs / "stderr.log").read_text() == ""
    seen = json.loads((logs / "stdout.log").read_text())
    assert seen["script"].endswith("scripts/reinforcement_learning/rsl_rl/train.py")
    assert seen["args"]["headless"] is flag and seen["HEADLESS"] == env
    assert seen["args"]["task"] == "Isaac-Reach-Franka-v0" and seen["args"]["max_iterations"] == "1"


def test_a_missing_script_is_refused_before_popen(monkeypatch, tmp_path):
    from isaac_mcp.isaaclab import jobs

    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start"))
    root = tmp_path / "empty"
    root.mkdir()
    (root / "VERSION").write_text("9.9.9\n")
    with pytest.raises(ValueError, match=r"Isaac Lab 9\.9\.9 .*has no scripts/reinforcement_learning/train\.py.*Expected"):
        jobs.train("py", root, "T", allow_concurrent=True, allow_concurrent_gpu=True)


def test_lab_2_3_log_lines_still_find_the_run_directory(tmp_path):
    from isaac_mcp.isaaclab.jobs import _run_dir_from_log

    log = tmp_path / "stdout.log"
    log.write_text("[INFO] Logging experiment in directory: /x/logs/rsl_rl/reach\n"
                   "Exact experiment name requested from command line: 2026-01-01_00-00-00\n")
    assert _run_dir_from_log(log) == "/x/logs/rsl_rl/reach/2026-01-01_00-00-00"


def _install_probe(monkeypatch, tmp_path, root, output):
    from isaac_mcp.isaaclab import install

    python = tmp_path / "python"
    python.write_text("")
    monkeypatch.setattr(install, "locate_install", lambda env=None: dict(python=str(python), isaaclab_root=str(root), venv=None))
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=json.dumps(output), stderr=""))
    return install


def test_install_info_reports_what_it_checked(monkeypatch, tmp_path):
    install = _install_probe(monkeypatch, tmp_path, _lab_2_3(tmp_path), dict(isaaclab="2.2.0", isaacsim="5.0.0"))
    info = install.install_info()
    profile = info["profile"]
    assert profile["layout"] == "per_library" and profile["libraries"] == ["rl_games", "rsl_rl", "sb3", "skrl"]
    assert profile["isaacsim_version"] == "5.0.0" and "no job was started" in profile["checked"]
    assert info["tested"] is False, "the checkout looking usable does not make the pair verified"
    install = _install_probe(monkeypatch, tmp_path, tmp_path / "missing", dict(isaaclab="3.0.0", isaacsim="6.2.0.0"))
    info = install.install_info()
    assert info["profile"]["layout"] == "none" and "cannot start" in info["jobs"] and info["tested"] is True
    install = _install_probe(monkeypatch, tmp_path, tmp_path, dict(isaaclab="3.0.0", isaacsim="6.0.1.0"))
    assert install.install_info()["tested"] is False


def test_envs_list_does_not_present_gymnasium_tasks_as_isaac_tasks(monkeypatch, tmp_path):
    output = dict(envs=[dict(id="CartPole-v1", entry_point="gymnasium.envs.classic_control:CartPoleEnv"),
                        dict(id="My-Cartpole", entry_point="mine:Env")],
                  import_errors=dict(isaaclab_tasks="ImportError: no"), gymnasium_builtin=["CartPole-v1"])
    install = _install_probe(monkeypatch, tmp_path, tmp_path, output)
    result = install.envs_list("cartpole")
    assert [e["id"] for e in result["envs"]] == ["My-Cartpole"] and "can't be imported" in result["isaac_tasks_unavailable"]
    healthy = dict(output, import_errors={})
    install = _install_probe(monkeypatch, tmp_path, tmp_path, healthy)
    result = install.envs_list("cartpole")
    assert len(result["envs"]) == 2 and "isaac_tasks_unavailable" not in result

"""Project task packages (ISAACLAB_TASK_MODULES) are imported before Isaac Lab's stock scripts run.

No Isaac Lab and no GPU: the launcher runs under the test interpreter against throwaway modules and scripts."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from twinsmith.isaaclab import install, jobs

LAUNCHER = Path(jobs.__file__).with_name("launcher.py")


@pytest.fixture(autouse=True)
def isolated_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINSMITH_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.delenv("ISAACLAB_TASK_MODULES", raising=False)
    monkeypatch.delenv("ISAACLAB_TASK_PATH", raising=False)
    jobs._active.clear()
    yield
    jobs._active.clear()


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "lab" / "IsaacLab"
    rl = root / "scripts" / "reinforcement_learning"
    rl.mkdir(parents=True)
    (rl / "train.py").write_text("import sys\nprint('ran', __name__, sys.argv[1:])\n")
    return root


def test_resolve_explicit_modules_and_paths(tmp_path):
    env = {"ISAACLAB_TASK_MODULES": "a, b ,,c", "ISAACLAB_TASK_PATH": f"/x{':' if sys.platform != 'win32' else ';'}/y"}
    modules, paths = install.resolve_task_modules(tmp_path / "lab" / "IsaacLab", env)
    assert modules == ["a", "b", "c"]
    assert paths[:2] == ["/x", "/y"]
    assert str(tmp_path / "lab") in paths and str(tmp_path / "lab" / "IsaacLab") in paths


def test_resolve_requires_explicit_sibling_task_module(tmp_path):
    (tmp_path / "lab" / "ur5e_lab").mkdir(parents=True)
    modules, _ = install.resolve_task_modules(tmp_path / "lab" / "IsaacLab", {})
    assert modules == []
    modules, _ = install.resolve_task_modules(tmp_path / "lab" / "IsaacLab", {"ISAACLAB_TASK_MODULES": "ur5e_lab"})
    assert modules == ["ur5e_lab"]


def test_resolve_nothing_configured(tmp_path):
    assert install.resolve_task_modules(tmp_path / "lab" / "IsaacLab", {}) == ([], [str(tmp_path / "lab"), str(tmp_path / "lab" / "IsaacLab")])
    assert install.resolve_task_modules(None, {}) == ([], [])


def run_launcher(script: Path, args: list[str], modules: str, pythonpath: str):
    env = {"PATH": "/usr/bin:/bin", "ISAACLAB_TASK_MODULES": modules, "PYTHONPATH": pythonpath}
    return subprocess.run([sys.executable, str(LAUNCHER), str(script), *args], env=env,
                          capture_output=True, text=True, timeout=60)


def test_launcher_imports_modules_then_runs_script_as_main(tmp_path):
    pkgs = tmp_path / "pkgs"
    pkgs.mkdir()
    (pkgs / "my_tasks.py").write_text("print('registered my_tasks')\n")
    script = tmp_path / "train.py"
    script.write_text("import sys\nprint('main', __name__, sys.argv[1:], sys.argv[0] == __file__)\n")
    out = run_launcher(script, ["--task", "X", "--num_envs", "4"], "my_tasks", str(pkgs))
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert lines[0] == "registered my_tasks"            # imported before the script ran
    assert lines[-1] == "main __main__ ['--task', 'X', '--num_envs', '4'] True"


def test_launcher_failing_module_fails_the_job_with_a_traceback(tmp_path):
    pkgs = tmp_path / "pkgs"
    pkgs.mkdir()
    (pkgs / "broken.py").write_text("raise RuntimeError('nope')\n")
    script = tmp_path / "train.py"
    script.write_text("print('should not run')\n")
    out = run_launcher(script, [], "broken", str(pkgs))
    assert out.returncode != 0
    assert "RuntimeError: nope" in out.stderr
    assert "should not run" not in out.stdout


def test_launcher_propagates_script_exit_code(tmp_path):
    script = tmp_path / "train.py"
    script.write_text("raise SystemExit(7)\n")
    assert run_launcher(script, [], "", "").returncode == 7


def test_job_without_task_modules_runs_the_stock_script(tmp_path):
    root = make_root(tmp_path)
    res = jobs.train(python=sys.executable, root=str(root), task="Isaac-Reach-Franka-v0",
                     allow_concurrent=True, allow_concurrent_gpu=True)
    assert res["command"][1] == str(root / "scripts" / "reinforcement_learning" / "train.py")
    jobs.runs_cancel(res["job_id"], grace_s=1)


def test_job_with_task_modules_goes_through_the_launcher(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    pkgs = tmp_path / "pkgs"
    pkgs.mkdir()
    (pkgs / "my_tasks.py").write_text("print('registered my_tasks')\n")
    monkeypatch.setenv("ISAACLAB_TASK_MODULES", "my_tasks")
    monkeypatch.setenv("ISAACLAB_TASK_PATH", str(pkgs))
    res = jobs.train(python=sys.executable, root=str(root), task="Isaac-Reach-Franka-v0",
                     num_envs=4, allow_concurrent=True, allow_concurrent_gpu=True)
    assert res["command"][1:3] == [str(LAUNCHER), str(root / "scripts" / "reinforcement_learning" / "train.py")]
    assert res["command"][3:5] == ["--task", "Isaac-Reach-Franka-v0"]
    import time
    deadline = time.time() + 30
    while time.time() < deadline and jobs.runs_status(res["job_id"]).get("state") == "running":
        time.sleep(0.1)
    status = jobs.runs_status(res["job_id"])
    assert status["state"] == "succeeded", status
    log = (Path(res["log_dir"]) / "stdout.log").read_text()
    assert log.splitlines()[0] == "registered my_tasks"
    assert "ran __main__ ['--task', 'Isaac-Reach-Franka-v0'" in log

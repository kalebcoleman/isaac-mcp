"""Tests for isaaclab install helpers (no GPU, no Isaac Sim)."""

import json
import subprocess
from unittest.mock import MagicMock, patch

from isaac_mcp.isaaclab import install


def _make_install(tmp_path):
    venv = tmp_path / "venv"
    py = venv / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    root = tmp_path / "IsaacLab"
    root.mkdir()
    return py, root, venv


def _mark_lab_installed(venv):
    (venv / "lib/python3.12/site-packages/isaaclab-3.0.dist-info").mkdir(parents=True)


def test_locate_install_env_var_precedence(tmp_path, monkeypatch):
    py, root, venv = _make_install(tmp_path)
    monkeypatch.delenv("ISAACLAB_PYTHON", raising=False)
    monkeypatch.delenv("ISAACLAB_ROOT", raising=False)
    env = {"ISAACLAB_PYTHON": str(py), "ISAACLAB_ROOT": str(root)}
    res = install.locate_install(env)
    assert res is not None
    assert res["python"] == str(py)
    assert res["isaaclab_root"] == str(root)
    assert res["venv"] == str(venv)


def test_locate_install_derives_root(tmp_path, monkeypatch):
    base = tmp_path / "workspace"
    py = base / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    root = base / "IsaacLab"
    root.mkdir()
    env = {"ISAACLAB_PYTHON": str(py)}
    res = install.locate_install(env)
    assert res is not None
    assert res["isaaclab_root"] == str(root)


def test_locate_install_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("ISAACLAB_PYTHON", raising=False)
    monkeypatch.delenv("ISAACLAB_ROOT", raising=False)
    assert install.locate_install() is None
    assert install.locate_install({}) is None
    env = {"ISAACLAB_PYTHON": str(tmp_path / "missing" / "python")}
    assert install.locate_install(env) is None


def test_locate_install_from_root(tmp_path):
    root = tmp_path / "IsaacLab"
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    assert install.locate_install({"ISAACLAB_ROOT": str(root)}) == {
        "python": str(python), "isaaclab_root": str(root), "venv": str(root / ".venv")
    }
    # An invalid explicit interpreter must not silently fall back to the root's venv.
    assert install.locate_install({"ISAACLAB_PYTHON": str(root / "missing"), "ISAACLAB_ROOT": str(root)}) is None


def test_locate_install_from_activated_checkout_environment(tmp_path, monkeypatch):
    root = tmp_path / "IsaacLab"
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    _mark_lab_installed(root / ".venv")
    train = root / "scripts/reinforcement_learning/train.py"
    train.parent.mkdir(parents=True)
    train.touch()
    monkeypatch.chdir(tmp_path)
    assert install.locate_install({"VIRTUAL_ENV": str(root / ".venv")}) == {
        "python": str(python), "isaaclab_root": str(root), "venv": str(root / ".venv")}
    assert install.locate_install({"ISAACLAB_ROOT": str(tmp_path / "missing"),
                                   "VIRTUAL_ENV": str(root / ".venv")}) is None


def test_explicit_checkout_can_use_active_environment_outside_it(tmp_path, monkeypatch):
    python, root, venv = _make_install(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert install.locate_install({"ISAACLAB_ROOT": str(root), "VIRTUAL_ENV": str(venv)}) is None
    _mark_lab_installed(venv)
    result = install.locate_install({"ISAACLAB_ROOT": str(root), "VIRTUAL_ENV": str(venv)})
    assert result == {"python": str(python), "isaaclab_root": str(root), "venv": str(venv)}


def test_locate_install_from_current_checkout(tmp_path, monkeypatch):
    root = tmp_path / "IsaacLab"
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    _mark_lab_installed(root / ".venv")
    train = root / "scripts/reinforcement_learning/train.py"
    train.parent.mkdir(parents=True)
    train.touch()
    monkeypatch.chdir(root)
    assert install.locate_install({})["python"] == str(python)
    train.unlink()
    assert install.locate_install({}) is None


def test_activated_sibling_checkout_requires_training_script(tmp_path, monkeypatch):
    py, root, venv = _make_install(tmp_path)
    _mark_lab_installed(venv)
    monkeypatch.chdir(tmp_path)
    assert install.locate_install({"VIRTUAL_ENV": str(venv)}) is None
    train = root / "scripts/reinforcement_learning/train.py"
    train.parent.mkdir(parents=True)
    train.touch()
    assert install.locate_install({"VIRTUAL_ENV": str(venv)})["isaaclab_root"] == str(root)


def test_install_info_success(monkeypatch, tmp_path):
    py, root, venv = _make_install(tmp_path)
    env = {"ISAACLAB_PYTHON": str(py), "ISAACLAB_ROOT": str(root)}
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = json.dumps({"isaaclab": "3.0.0", "isaacsim": "6.2.0.0"}) + "\n"
    fake.stderr = ""
    with patch("isaac_mcp.isaaclab.install.subprocess.run", return_value=fake) as m:
        res = install.install_info(env)
    assert res["python"] == str(py)
    assert res["isaaclab_version"] == "3.0.0"
    assert res["isaacsim_version"] == "6.2.0.0"
    assert res["isaaclab_root"] == str(root)
    assert res["venv"] == str(venv)
    assert m.call_args.kwargs["timeout"] == 60
    assert m.call_args.kwargs["stdin"] is subprocess.DEVNULL
    assert m.call_args.kwargs["env"]["OMNI_KIT_ACCEPT_EULA"] == "YES"


def test_install_info_no_install(monkeypatch, tmp_path):
    monkeypatch.delenv("ISAACLAB_PYTHON", raising=False)
    monkeypatch.delenv("ISAACLAB_ROOT", raising=False)
    res = install.install_info()
    assert "error" in res
    assert res == {"error": "Isaac Lab install not found", "hint": "set ISAACLAB_PYTHON or ISAACLAB_ROOT"}


def test_install_info_timeout(monkeypatch, tmp_path):
    py, root, venv = _make_install(tmp_path)
    env = {"ISAACLAB_PYTHON": str(py), "ISAACLAB_ROOT": str(root)}
    with patch(
        "isaac_mcp.isaaclab.install.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="x", timeout=60),
    ):
        res = install.install_info(env)
    assert "error" in res


def test_install_info_malformed(monkeypatch, tmp_path):
    py, root, venv = _make_install(tmp_path)
    env = {"ISAACLAB_PYTHON": str(py), "ISAACLAB_ROOT": str(root)}
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = "not json at all"
    fake.stderr = ""
    with patch("isaac_mcp.isaaclab.install.subprocess.run", return_value=fake):
        res = install.install_info(env)
    assert "error" in res


def test_envs_list_filter(monkeypatch, tmp_path):
    py, root, venv = _make_install(tmp_path)
    env = {"ISAACLAB_PYTHON": str(py), "ISAACLAB_ROOT": str(root)}
    payload = {"envs": [{"id": "Isaac-Reach-UR10-v0", "entry_point": "x"}, {"id": "CartPole-v1", "entry_point": "y"}]}
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = json.dumps(payload) + "\n"
    fake.stderr = ""
    with patch("isaac_mcp.isaaclab.install.subprocess.run", return_value=fake):
        res = install.envs_list(filter="reach", env=env)
        res2 = install.envs_list(filter="REACH", env=env)
    assert res["envs"] == [{"id": "Isaac-Reach-UR10-v0", "entry_point": "x"}]
    assert len(res2["envs"]) == 1


def test_envs_list_no_filter(monkeypatch, tmp_path):
    py, root, venv = _make_install(tmp_path)
    env = {"ISAACLAB_PYTHON": str(py), "ISAACLAB_ROOT": str(root)}
    payload = {"envs": [{"id": "A", "entry_point": "x"}, {"id": "B", "entry_point": "y"}]}
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = json.dumps(payload) + "\n"
    fake.stderr = ""
    with patch("isaac_mcp.isaaclab.install.subprocess.run", return_value=fake):
        res = install.envs_list(env=env)
    assert len(res["envs"]) == 2


def test_envs_list_timeout(monkeypatch, tmp_path):
    py, root, venv = _make_install(tmp_path)
    env = {"ISAACLAB_PYTHON": str(py), "ISAACLAB_ROOT": str(root)}
    with patch(
        "isaac_mcp.isaaclab.install.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="x", timeout=5),
    ):
        res = install.envs_list(env=env, timeout=5)
    assert "error" in res


def test_envs_list_malformed(monkeypatch, tmp_path):
    py, root, venv = _make_install(tmp_path)
    env = {"ISAACLAB_PYTHON": str(py), "ISAACLAB_ROOT": str(root)}
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = "{bad"
    fake.stderr = ""
    with patch("isaac_mcp.isaaclab.install.subprocess.run", return_value=fake):
        res = install.envs_list(env=env)
    assert "error" in res


def test_envs_list_missing_install(monkeypatch, tmp_path):
    monkeypatch.delenv("ISAACLAB_PYTHON", raising=False)
    monkeypatch.delenv("ISAACLAB_ROOT", raising=False)
    res = install.envs_list()
    assert res == {"error": "Isaac Lab install not found", "hint": "set ISAACLAB_PYTHON or ISAACLAB_ROOT"}


def test_envs_list_task_modules_and_import_errors(tmp_path):
    py, root, venv = _make_install(tmp_path)
    env = {
        "ISAACLAB_PYTHON": str(py),
        "ISAACLAB_ROOT": str(root),
        "ISAACLAB_TASK_MODULES": "my_tasks, nope",
        "ISAACLAB_TASK_PATH": str(tmp_path / "pkgs"),
    }
    payload = {"envs": [{"id": "My-Task-v0", "entry_point": "x"}], "import_errors": {"nope": "ModuleNotFoundError: nope"}}
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = json.dumps(payload) + "\n"
    fake.stderr = ""
    with patch("isaac_mcp.isaaclab.install.subprocess.run", return_value=fake) as run:
        res = install.envs_list(env=env)
    assert res["import_errors"] == {"nope": "ModuleNotFoundError: nope"}
    code = run.call_args.args[0][2]
    assert "['isaaclab_tasks'] + ['my_tasks', 'nope']" in code
    assert str(tmp_path / "pkgs") in run.call_args.kwargs["env"]["PYTHONPATH"]


def test_envs_list_real_subprocess_survives_bad_modules(tmp_path):
    """Run the generated probe for real (stub gymnasium/isaaclab_tasks, no Isaac Sim)."""
    import sys

    pkgs = tmp_path / "pkgs"
    pkgs.mkdir()
    (pkgs / "gymnasium.py").write_text(
        "class _S:\n    entry_point = 'good:Env'\nregistry = {'Good-v0': _S()}\n"
    )
    (pkgs / "isaaclab_tasks.py").write_text("")
    (pkgs / "good.py").write_text("")
    (pkgs / "exits.py").write_text("raise SystemExit(3)\n")
    (pkgs / "boom.py").write_text("raise RuntimeError('boom')\n")
    env = {
        "ISAACLAB_PYTHON": sys.executable,
        "ISAACLAB_TASK_PATH": str(pkgs),
        "ISAACLAB_TASK_MODULES": "boom, ,exits,good",
    }
    res = install.envs_list(env=env)
    assert [e["id"] for e in res["envs"]] == ["Good-v0"]
    assert res["import_errors"] == {"boom": "RuntimeError: boom", "exits": "SystemExit: 3"}


# ---- a per-project interpreter (isaac-mcp.json) ----


def _project(tmp_path, data):
    (tmp_path / "isaac-mcp.json").write_text(json.dumps(data))
    return {"ISAAC_MCP_PROJECT": str(tmp_path)}


def _lab_venv(tmp_path):
    py, root, venv = _make_install(tmp_path)
    _mark_lab_installed(venv)
    return py, root, venv


def test_project_file_supplies_the_interpreter(tmp_path):
    py, root, venv = _lab_venv(tmp_path)
    env = _project(tmp_path, {"isaaclab_python": str(py), "isaaclab_root": str(root)})
    found = install.locate_install(env)
    assert found["python"] == str(py) and found["isaaclab_root"] == str(root)
    assert found["source"].startswith("project:")


def test_relative_project_paths_start_at_the_project_file(tmp_path):
    py, _, _ = _lab_venv(tmp_path)
    env = _project(tmp_path, {"isaaclab_python": "venv/bin/python"})
    assert install.locate_install(env)["python"] == str(py)


def test_explicit_environment_beats_the_project_file_and_says_so(tmp_path):
    py, root, _ = _lab_venv(tmp_path)
    other = tmp_path / "other"
    other_py, other_root, _ = _lab_venv(other) if other.mkdir() is None else (None, None, None)
    env = {**_project(tmp_path, {"isaaclab_python": str(py)}), "ISAACLAB_PYTHON": str(other_py), "ISAACLAB_ROOT": str(other_root)}
    assert install.locate_install(env)["python"] == str(other_py)
    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=json.dumps({"isaaclab": "3.0.0", "isaacsim": "6.2.0.0"}), stderr="")):
        info = install.install_info(env)
    assert info["python"] == str(other_py) and "takes priority" in info["project_setting_ignored"]
    assert info["tested"] is True


def test_an_interpreter_without_lab_metadata_is_refused_and_never_run(tmp_path):
    py, _, _ = _make_install(tmp_path)  # no Isaac Lab installed in it
    env = _project(tmp_path, {"isaaclab_python": str(py)})
    assert install.locate_install(env) is None
    with patch("subprocess.run", side_effect=AssertionError("must not run")):
        info = install.install_info(env)
    assert "not the interpreter" in info["error"]


def test_a_broken_project_file_does_not_fall_through_silently(tmp_path):
    (tmp_path / "isaac-mcp.json").write_text("{not json")
    info = install.install_info({"ISAAC_MCP_PROJECT": str(tmp_path)})
    assert "cannot read" in info["error"]


def test_project_file_without_lab_settings_changes_nothing(tmp_path):
    env = _project(tmp_path, {"asset_path": ["x"]})
    assert install.project_lab_setting(env) is None


def test_root_without_python_is_refused(tmp_path):
    env = _project(tmp_path, {"isaaclab_root": str(tmp_path)})
    assert "without isaaclab_python" in install.project_lab_setting(env)["error"]


def test_a_version_not_in_the_tested_list_is_scoped_as_untested(tmp_path):
    py, root, _ = _lab_venv(tmp_path)
    root.rmdir()  # pip-only, like BrickSim: no checkout beside the environment
    env = _project(tmp_path, {"isaaclab_python": str(py)})
    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=json.dumps({"isaaclab": "2.3.2.post1", "isaacsim": "5.1.0.0"}), stderr="")):
        info = install.install_info(env)
    assert info["tested"] is False and "not in the tested list" in info["note"]
    assert "scripts/reinforcement_learning" in info["jobs"]


# ---- a project file found only by walking up is not a choice anyone made ----


def test_walked_up_file_with_a_hostile_interpreter_never_runs_it(tmp_path, monkeypatch):
    """Starting a client inside a cloned repository must not let it choose what runs."""
    marker = tmp_path / "ran"
    py, _, venv = _make_install(tmp_path)
    py.write_text(f"#!/bin/sh\ntouch {marker}\n")
    py.chmod(0o755)
    _mark_lab_installed(venv)  # forged metadata passes the file check, so only the rule protects this
    (tmp_path / "isaac-mcp.json").write_text(json.dumps({"isaaclab_python": str(py)}))
    inner = tmp_path / "src" / "deep"
    inner.mkdir(parents=True)
    monkeypatch.chdir(inner)
    assert install.locate_install({}) is None
    with patch("subprocess.run", side_effect=AssertionError("must not run")):
        info = install.install_info({})
    assert not marker.exists() and "error" in info
    assert "ISAAC_MCP_PROJECT=" + str(tmp_path) in info["project_setting_ignored"]


def test_walked_up_file_is_reported_next_to_an_install_found_another_way(tmp_path, monkeypatch):
    py, root, _ = _lab_venv(tmp_path)
    repo = tmp_path / "cloned"
    repo.mkdir()
    (repo / "isaac-mcp.json").write_text(json.dumps({"isaaclab_python": "/hostile/bin/python", "isaaclab_root": "/hostile"}))
    monkeypatch.chdir(repo)
    env = {"ISAACLAB_PYTHON": str(py), "ISAACLAB_ROOT": str(root)}
    assert install.locate_install(env)["python"] == str(py)
    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=json.dumps({"isaaclab": "3.0.0", "isaacsim": "6.2.0.0"}), stderr="")):
        info = install.install_info(env)
    assert info["source"] == "environment" and "searching up" in info["project_setting_ignored"]


def test_walked_up_file_without_lab_keys_or_broken_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert install.project_lab_setting({}) is None
    (tmp_path / "isaac-mcp.json").write_text(json.dumps({"asset_path": ["x"]}))
    assert install.project_lab_setting({}) is None
    (tmp_path / "isaac-mcp.json").write_text("{not json")
    assert install.project_lab_setting({}) is None
    assert "project_setting_ignored" not in install.install_info({})


def test_naming_the_project_honors_the_same_file_from_anywhere(tmp_path, monkeypatch):
    py, root, _ = _lab_venv(tmp_path)
    (tmp_path / "isaac-mcp.json").write_text(json.dumps({"isaaclab_python": str(py), "isaaclab_root": str(root)}))
    monkeypatch.chdir(tmp_path)
    assert install.locate_install({}) is None
    found = install.locate_install({"ISAAC_MCP_PROJECT": str(tmp_path)})
    assert found["python"] == str(py) and found["source"].startswith("project:")
    assert install.locate_install({"ISAAC_MCP_PROJECT": str(tmp_path / "isaac-mcp.json")})["python"] == str(py)


def test_a_named_project_with_no_file_does_not_fall_back_to_the_walk(tmp_path, monkeypatch):
    py, _, _ = _lab_venv(tmp_path)
    (tmp_path / "isaac-mcp.json").write_text(json.dumps({"isaaclab_python": str(py)}))
    monkeypatch.chdir(tmp_path)
    assert install.project_lab_setting({"ISAAC_MCP_PROJECT": str(tmp_path / "nowhere")}) is None

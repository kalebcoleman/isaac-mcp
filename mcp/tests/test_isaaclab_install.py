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

"""Locate and query an Isaac Lab installation (host side).

Pure stdlib. Never imports ``isaaclab`` in the host process; all probing is done
via short-lived subprocesses in the Isaac Lab interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_LEARNING6 = Path.home() / "Desktop" / "ur5e-digital-twin" / "learning6"


def _env(env: Any) -> Any:
    return os.environ if env is None else env


def _derive_venv(python: Path) -> Path | None:
    if python.parent.name == "bin":
        return python.parent.parent
    return None


def _derive_root(python: Path) -> Path | None:
    venv = _derive_venv(python)
    if venv is None:
        return None
    candidate = venv.parent / "IsaacLab"
    if candidate.is_dir():
        return candidate
    return None


def locate_install(env: Any = None) -> dict | None:
    e = _env(env)

    isaac_python = None
    try:
        isaac_python = e.get("ISAACLAB_PYTHON") if hasattr(e, "get") else None
    except Exception:
        isaac_python = None

    if isaac_python:
        python = Path(str(isaac_python)).expanduser()
        root_val = None
        try:
            root_val = e.get("ISAACLAB_ROOT") if hasattr(e, "get") else None
        except Exception:
            root_val = None
        if root_val:
            root = Path(str(root_val)).expanduser()
        else:
            root = _derive_root(python)
        venv = _derive_venv(python)
        if python.is_file() and (root is None or root.is_dir()):
            return {
                "python": str(python),
                "isaaclab_root": str(root) if root is not None else None,
                "venv": str(venv) if venv is not None else None,
            }
        return None

    base = DEFAULT_LEARNING6
    python = base / ".venv" / "bin" / "python"
    root = base / "IsaacLab"
    venv = base / ".venv"
    if python.is_file() and root.is_dir():
        return {
            "python": str(python),
            "isaaclab_root": str(root),
            "venv": str(venv),
        }

    try:
        root_val = e.get("ISAACLAB_ROOT") if hasattr(e, "get") else None
    except Exception:
        root_val = None
    if root_val:
        base2 = Path(str(root_val)).expanduser()
        python2 = base2 / ".venv" / "bin" / "python"
        if python2.is_file() and base2.is_dir():
            return {
                "python": str(python2),
                "isaaclab_root": str(base2),
                "venv": str(base2 / ".venv"),
            }

    return None


_MISSING = {"error": "Isaac Lab install not found", "hint": "set ISAACLAB_PYTHON"}


def install_info(env: Any = None) -> dict:
    loc = locate_install(env)
    if not loc:
        return dict(_MISSING)
    python = loc["python"]
    run_env = dict(os.environ)
    run_env["OMNI_KIT_ACCEPT_EULA"] = "YES"
    code = (
        "import json, importlib.metadata\n"
        "def _v(name):\n"
        "    try:\n"
        "        return importlib.metadata.version(name)\n"
        "    except Exception:\n"
        "        return None\n"
        "print(json.dumps({'isaaclab': _v('isaaclab'), 'isaacsim': _v('isaacsim')}))\n"
    )
    try:
        proc = subprocess.run(
            [python, "-c", code],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            env=run_env,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"failed to run {python}: {exc}", "hint": "set ISAACLAB_PYTHON"}
    if proc.returncode != 0:
        msg = (proc.stderr or "").strip() or f"subprocess exited {proc.returncode}"
        return {"error": msg, "hint": "set ISAACLAB_PYTHON"}
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if not lines:
        return {"error": "empty output from install probe", "hint": "set ISAACLAB_PYTHON"}
    try:
        data = json.loads(lines[-1])
    except (json.JSONDecodeError, ValueError):
        return {"error": f"malformed output: {(proc.stdout or '')[:200]}", "hint": "set ISAACLAB_PYTHON"}
    return {
        "python": python,
        "isaaclab_version": data.get("isaaclab"),
        "isaacsim_version": data.get("isaacsim"),
        "isaaclab_root": loc["isaaclab_root"],
        "venv": loc["venv"],
    }


def envs_list(filter: Any = None, env: Any = None, timeout: int = 60) -> dict:
    loc = locate_install(env)
    if not loc:
        return dict(_MISSING)
    python = loc["python"]
    root = loc["isaaclab_root"]

    run_env = dict(os.environ)
    run_env["OMNI_KIT_ACCEPT_EULA"] = "YES"
    paths: list[str] = []
    if root:
        base = str(Path(root).parent)
        paths.append(base)
        paths.append(root)
        ur5e = Path(root).parent / "ur5e_lab"
        if ur5e.is_dir():
            paths.append(str(ur5e))
    existing_pp = run_env.get("PYTHONPATH")
    if existing_pp:
        paths.append(existing_pp)
    if paths:
        run_env["PYTHONPATH"] = os.pathsep.join(paths)

    code = (
        "import json\n"
        "try:\n"
        "    import isaaclab_tasks  # noqa: F401\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        "    import ur5e_lab  # noqa: F401\n"
        "except Exception:\n"
        "    pass\n"
        "import gymnasium\n"
        "envs = []\n"
        "for _eid, _spec in getattr(gymnasium, 'registry', {}).items():\n"
        "    try:\n"
        "        _ep = _spec.entry_point\n"
        "    except Exception:\n"
        "        _ep = None\n"
        "    envs.append({'id': str(_eid), 'entry_point': (str(_ep) if _ep is not None else None)})\n"
        "print(json.dumps({'envs': envs}))\n"
    )
    try:
        proc = subprocess.run(
            [python, "-c", code],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"failed to run {python}: {exc}", "hint": "set ISAACLAB_PYTHON"}
    if proc.returncode != 0:
        msg = (proc.stderr or "").strip() or f"subprocess exited {proc.returncode}"
        return {"error": msg, "hint": "set ISAACLAB_PYTHON"}
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if not lines:
        return {"error": "empty output", "hint": "set ISAACLAB_PYTHON"}
    try:
        data = json.loads(lines[-1])
    except (json.JSONDecodeError, ValueError):
        return {"error": f"malformed output: {(proc.stdout or '')[:200]}", "hint": "set ISAACLAB_PYTHON"}
    envs = data.get("envs", [])
    if filter is not None:
        needle = str(filter).lower()
        envs = [e for e in envs if needle in str(e.get("id", "")).lower()]
    return {"envs": envs}

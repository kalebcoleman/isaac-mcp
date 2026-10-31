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

def _env(env: Any) -> Any:
    return os.environ if env is None else env


def _derive_venv(python: Path) -> Path | None:
    if python.parent.name == "bin":
        return python.parent.parent
    return None


def _has_isaaclab_metadata(venv: Path) -> bool:
    """Recognize an installed Lab distribution without executing an arbitrary interpreter."""
    for lib in ("lib", "lib64"):
        for site in (venv / lib).glob("python*/site-packages"):
            if any(site.glob("isaaclab-*.dist-info")) or (site / "isaaclab.egg-info").exists():
                return True
    return False


def _derive_root(python: Path) -> Path | None:
    venv = _derive_venv(python)
    if venv is None:
        return None
    # A standard Isaac Lab checkout commonly keeps its virtualenv in the root.
    if (venv.parent / "scripts" / "reinforcement_learning" / "train.py").is_file():
        return venv.parent
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
        active = e.get("VIRTUAL_ENV") if hasattr(e, "get") else None
        if active and base2.is_dir():
            venv2 = Path(str(active)).expanduser()
            python2 = venv2 / "bin" / "python"
            if python2.is_file() and _has_isaaclab_metadata(venv2):
                return {"python": str(python2), "isaaclab_root": str(base2), "venv": str(venv2)}
        # Do not silently replace an invalid explicit configuration.
        return None

    # Standard activated environments and checkout-local .venv installs can be
    # discovered without scanning a user's home or guessing a personal path.
    venv_val = e.get("VIRTUAL_ENV") if hasattr(e, "get") else None
    candidates = [Path(str(venv_val)).expanduser()] if venv_val else []
    candidates.append(Path.cwd() / ".venv")
    for venv in candidates:
        python = venv / "bin" / "python"
        root = _derive_root(python)
        if (python.is_file() and _has_isaaclab_metadata(venv) and root is not None and
                (root / "scripts" / "reinforcement_learning" / "train.py").is_file()):
            return {"python": str(python), "isaaclab_root": str(root), "venv": str(venv)}

    return None


_MISSING = {"error": "Isaac Lab install not found", "hint": "set ISAACLAB_PYTHON or ISAACLAB_ROOT"}


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


def resolve_task_modules(root: Any, env: Any = None) -> tuple[list[str], list[str]]:
    """Task packages to import before Isaac Lab looks up a task id, and the sys.path entries they need.

    ``ISAACLAB_TASK_MODULES`` (comma list) and ``ISAACLAB_TASK_PATH`` (os.pathsep list) are the explicit
    configuration. The parent of the Isaac Lab root and the root itself are always importable. Shared by
    ``envs_list`` and the train/eval/play jobs so both see the same tasks.
    """
    e = _env(env)
    modules = [m.strip() for m in str(e.get("ISAACLAB_TASK_MODULES") or "").split(",") if m.strip()]
    paths = [p.strip() for p in str(e.get("ISAACLAB_TASK_PATH") or "").split(os.pathsep) if p.strip()]
    if root:
        base = Path(root).parent
        paths.extend([str(base), str(root)])
    return modules, paths


def envs_list(filter: Any = None, env: Any = None, timeout: int = 60) -> dict:
    loc = locate_install(env)
    if not loc:
        return dict(_MISSING)
    python = loc["python"]
    root = loc["isaaclab_root"]

    run_env = dict(os.environ)
    run_env["OMNI_KIT_ACCEPT_EULA"] = "YES"
    modules, paths = resolve_task_modules(root, env)
    existing_pp = run_env.get("PYTHONPATH")
    if existing_pp:
        paths.append(existing_pp)
    if paths:
        run_env["PYTHONPATH"] = os.pathsep.join(paths)

    code = (
        "import importlib, json\n"
        "errors = {}\n"
        f"for _m in ['isaaclab_tasks'] + {modules!r}:\n"
        "    try:\n"
        "        importlib.import_module(_m)\n"
        "    except BaseException as _exc:  # a task module may sys.exit(); keep the rest\n"
        "        try:\n"
        "            errors[_m] = f'{type(_exc).__name__}: {_exc}'\n"
        "        except Exception:\n"
        "            errors[_m] = type(_exc).__name__\n"
        "import gymnasium\n"
        "envs = []\n"
        "for _eid, _spec in getattr(gymnasium, 'registry', {}).items():\n"
        "    try:\n"
        "        _ep = _spec.entry_point\n"
        "    except Exception:\n"
        "        _ep = None\n"
        "    envs.append({'id': str(_eid), 'entry_point': (str(_ep) if _ep is not None else None)})\n"
        "print(json.dumps({'envs': envs, 'import_errors': errors}))\n"
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
        envs = [x for x in envs if needle in str(x.get("id", "")).lower()]
    result: dict = {"envs": envs}
    if data.get("import_errors"):
        result["import_errors"] = data["import_errors"]
    return result

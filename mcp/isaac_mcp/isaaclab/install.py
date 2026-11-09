"""Locate and query an Isaac Lab installation (host side).

Pure stdlib. Never imports ``isaaclab`` in the host process; all probing is done
via short-lived subprocesses in the Isaac Lab interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
import re
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


# The rule of ur5e_dt/asset_cache.py's project_file, which runs in Kit: $ISAAC_MCP_PROJECT (a project
# directory or the file itself), else the working directory and its parents.
_PROJECT_ENV = "ISAAC_MCP_PROJECT"
_PROJECT_FILE = "isaac-mcp.json"
# (Isaac Lab, Isaac Sim prefix) pairs verified with real jobs, as in docs/TOOLS-ISAACLAB.md. Another pair may work; say it
# is untested. This is what we measured, separate from what lab_profile reads a checkout as offering.
TESTED_PAIRS = (("3.0.0", "6.2."), ("2.3.2", "5.1."))


def _tested_pair(lab: Any, sim: Any) -> bool:
    return any(str(lab).split(".post")[0] == tested_lab and str(sim).startswith(tested_sim)
               for tested_lab, tested_sim in TESTED_PAIRS)


UNIFIED = "unified"
PER_LIBRARY = "per_library"
NO_SCRIPTS = "none"
EXPECTED_SCRIPTS = ("scripts/reinforcement_learning/{train,play}.py (Isaac Lab 3) or "
                    "scripts/reinforcement_learning/<library>/{train,play}.py (Isaac Lab 2.3)")
_ADD_ARGUMENT_RE = re.compile(r"""\badd_argument\(\s*(?:["']-[A-Za-z0-9]["']\s*,\s*)?["'](--[A-Za-z0-9_-]+)["']""")
_APP_LAUNCHER = ("source", "isaaclab", "isaaclab", "app", "app_launcher.py")
_profiles: dict[str, tuple[Any, dict]] = {}


def _read_flags(script: Path, root: Path) -> list[str] | None:
    """Flags a script declares, read from its text and never imported. None means unknown, not "takes none".

    Isaac Lab 3's scripts only hand over to a CLI elsewhere, so they declare nothing here and stay unknown. A helper
    beside the script (rsl_rl's cli_args.py) and AppLauncher's arguments count; if the script uses AppLauncher and its
    source cannot be read, the list would be incomplete, so it is unknown too.
    """
    try:
        text = script.read_text(errors="replace")
        flags = set(_ADD_ARGUMENT_RE.findall(text))
        if not flags:
            return None
        for helper in script.parent.glob("*.py"):
            if helper.name not in ("train.py", "play.py") and helper.stem in text:
                flags.update(_ADD_ARGUMENT_RE.findall(helper.read_text(errors="replace")))
        if "add_app_launcher_args" in text:
            flags.update(_ADD_ARGUMENT_RE.findall(root.joinpath(*_APP_LAUNCHER).read_text(errors="replace")))
    except OSError:
        return None
    return sorted(flags)


def _script_entries(root: Path, folder: Path) -> dict:
    return {kind: {"path": str(folder / f"{kind}.py"), "flags": _read_flags(folder / f"{kind}.py", root)}
            for kind in ("train", "play")}


def lab_profile(root: Any) -> dict:
    """What a Lab checkout offers, read from its files once per root and never by importing it.

    ``layout`` is unified, per_library or none; per_library's ``libraries`` hold both scripts. Each script entry has its
    declared ``flags``, or None when they could not be read. jobs.py builds argv from this alone.
    """
    if not root:
        return {"layout": NO_SCRIPTS, "libraries": [], "scripts": {}, "lab_version": None}
    path = Path(root)
    scripts = path / "scripts" / "reinforcement_learning"
    try:
        stamp = scripts.stat().st_mtime_ns
    except OSError:
        stamp = None
    cached = _profiles.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        version = (path / "VERSION").read_text(errors="replace").strip()[:40] or None
    except OSError:
        version = None
    profile = {"layout": NO_SCRIPTS, "libraries": [], "scripts": {}, "lab_version": version}
    if (scripts / "train.py").is_file() and (scripts / "play.py").is_file():
        profile.update(layout=UNIFIED, scripts={"": _script_entries(path, scripts)})
    elif stamp is not None:
        folders = sorted(p for p in scripts.iterdir() if p.is_dir() and (p / "train.py").is_file() and (p / "play.py").is_file())
        if folders:
            profile.update(layout=PER_LIBRARY, libraries=[p.name for p in folders],
                           scripts={p.name: _script_entries(path, p) for p in folders})
    _profiles[str(path)] = (stamp, profile)
    return profile


def lab_label(root: Any, profile: dict) -> str:
    version = profile.get("lab_version")
    return f"Isaac Lab {version} at {root}" if version else f"Isaac Lab at {root}"


def _named_project_file(env: Any) -> Path | None:
    override = str(env.get(_PROJECT_ENV) or "").strip()
    if not override:
        return None
    path = Path(override).expanduser()
    path = path / _PROJECT_FILE if path.is_dir() else path
    return path.resolve() if path.is_file() else None


def _walked_up_project_file() -> Path | None:
    try:
        here = Path.cwd().resolve()
    except OSError:
        return None
    for directory in (here, *here.parents):
        if (directory / _PROJECT_FILE).is_file():
            return directory / _PROJECT_FILE
    return None


def project_lab_setting(env: Any = None) -> dict | None:
    """``isaaclab_python`` and ``isaaclab_root`` from the project file: None when it sets neither, else
    ``{file, python, root}``, ``{file, error}`` or ``{file, ignored}``. Relative paths start at the
    project file's folder.

    Honored only for a project named by ISAAC_MCP_PROJECT in the server's own environment, which the
    client configuration sets. Lab tools run this interpreter, and a file found by walking up from the
    working directory may come from a repository someone just cloned, so starting a client there must not
    choose what runs. Such a file is reported as ignored, never read further. (A project file's asset_path
    is not an approval either, for the same reason.)

    The interpreter must be a virtual environment with Isaac Lab installed, checked from its files before
    anything runs it. That catches mistakes. It is not a guard against a file a tool wrote, because a tool
    with put_file could write those too. It does not make a Lab version supported. Explicit ISAACLAB_PYTHON
    or ISAACLAB_ROOT take priority over this."""
    environment = _env(env)
    file = _named_project_file(environment)
    if file is None:
        if str(environment.get(_PROJECT_ENV) or "").strip():
            return None  # named, with no file there: nothing to honor, and no walking past the choice
        walked = _walked_up_project_file()
        if walked is None:
            return None
        try:
            data = json.loads(walked.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(data, dict) and (data.get("isaaclab_python") or data.get("isaaclab_root")):
            return {"file": str(walked), "ignored": (
                f"{walked} sets an Isaac Lab environment, but it was only found by searching up from the working "
                "directory, and a cloned repository must not choose what runs. To use it, set "
                f"ISAAC_MCP_PROJECT={walked.parent} in this server's MCP configuration.")}
        return None
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"file": str(file), "error": f"cannot read {file}: {exc}"}
    if not isinstance(data, dict) or not (data.get("isaaclab_python") or data.get("isaaclab_root")):
        return None

    def resolved(key: str) -> Path | None:
        value = data.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a path")
        path = Path(value).expanduser()
        return path if path.is_absolute() else file.parent / path

    try:
        python, root = resolved("isaaclab_python"), resolved("isaaclab_root")
    except ValueError as exc:
        return {"file": str(file), "error": f"{file}: {exc}"}
    if python is None:
        return {"file": str(file), "error": f"{file} sets isaaclab_root without isaaclab_python"}
    venv = _derive_venv(python)
    if not python.is_file() or venv is None or not _has_isaaclab_metadata(venv):
        return {"file": str(file), "error": f"{file}: isaaclab_python {python} is not the interpreter of a "
                                            "virtual environment with Isaac Lab installed; it was not run"}
    if root is not None and not root.is_dir():
        return {"file": str(file), "error": f"{file}: isaaclab_root {root} is not a folder"}
    return {"file": str(file), "python": str(python), "root": str(root) if root else None, "venv": str(venv)}


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

    project = project_lab_setting(e)
    if project is not None and "ignored" not in project:
        if "error" in project:  # Nor an invalid project setting.
            return None
        root = Path(project["root"]) if project["root"] else _derive_root(Path(project["python"]))
        return {"python": project["python"], "isaaclab_root": str(root) if root else None,
                "venv": project["venv"], "source": f"project:{project['file']}"}

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
        project = project_lab_setting(env)
        if project is not None and "error" in project:
            return {"error": project["error"], "hint": "fix isaaclab_python in the project file, or set ISAACLAB_PYTHON"}
        if project is not None and "ignored" in project:
            return {**_MISSING, "project_setting_ignored": project["ignored"]}
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
        "import importlib.util as _u\n"
        "_libs = {'rsl_rl': 'rsl_rl', 'rl_games': 'rl_games', 'skrl': 'skrl', 'sb3': 'stable_baselines3'}\n"
        "print(json.dumps({'isaaclab': _v('isaaclab'), 'isaacsim': _v('isaacsim'), "
        "'rl_libraries': sorted(k for k, m in _libs.items() if _u.find_spec(m))}))\n"
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
    result = {
        "python": python,
        "isaaclab_version": data.get("isaaclab"),
        "isaacsim_version": data.get("isaacsim"),
        "isaaclab_root": loc["isaaclab_root"],
        "venv": loc["venv"],
        "source": loc.get("source", "environment"),
    }
    project = project_lab_setting(env)
    if project is not None and "ignored" in project:
        result["project_setting_ignored"] = project["ignored"]
    elif project is not None and "source" not in loc:
        result["project_setting_ignored"] = (f"{project['file']} sets an Isaac Lab environment, but this server's "
                                             "ISAACLAB_PYTHON or ISAACLAB_ROOT takes priority. Remove them from "
                                             "its MCP configuration to use the project's.")
    if "rl_libraries" in data:
        result["rl_libraries"] = data["rl_libraries"]
    version = data.get("isaaclab")
    if version:
        result["tested"] = _tested_pair(version, data.get("isaacsim"))
        if not result["tested"]:
            result["note"] = (f"The package isaaclab {version} with Isaac Sim {data.get('isaacsim')} is not in the tested list in "
                              "docs/TOOLS-ISAACLAB.md. Reading its version works; jobs may need changes.")
    if not loc["isaaclab_root"]:
        result["jobs"] = ("train, eval and play need a checkout with scripts/reinforcement_learning: "
                          "set isaaclab_root or ISAACLAB_ROOT. A pip-only install has none.")
    else:
        profile = lab_profile(loc["isaaclab_root"])
        result["profile"] = {
            **profile,
            "isaaclab_version": data.get("isaaclab"),
            "isaacsim_version": data.get("isaacsim"),
            "checked": "the checkout's script files, read as text; no job was started and nothing was imported. "
                       "tested says separately whether this pair was verified.",
        }
        if profile["layout"] == NO_SCRIPTS:
            result["jobs"] = f"train, eval and play cannot start: expected {EXPECTED_SCRIPTS}."
    return result


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


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CALLBACK_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+\Z")
# Variables a caller may not set: they change which code the interpreter loads, where it looks, or how this server
# manages the job. PYTHONPATH has its own argument (python_path); the device, display and EULA have theirs.
_PROTECTED_ENV = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "IFS", "BASH_ENV", "ENV", "CDPATH", "TMPDIR", "PWD",
    "CUDA_VISIBLE_DEVICES", "HEADLESS", "OMNI_KIT_ACCEPT_EULA", "PYTHONUNBUFFERED",
    "ISAACLAB_PYTHON", "ISAACLAB_ROOT", "ISAACLAB_JOBS_DIR", "ISAACSIM_PATH", "ISAAC_MCP_PYTHON",
})
_PROTECTED_ENV_PREFIXES = ("LD_", "DYLD_", "PYTHON", "ISAAC_MCP_", "BASH_FUNC_")
_SAFE_PYTHON_ENV = frozenset({"PYTHONHASHSEED", "PYTHONFAULTHANDLER"})
_MAX_ENV_VALUE = 4096


def validate_launch_options(python_path: Any = None, env: Any = None, external_callback: Any = None,
                            environ: Any = None) -> dict:
    """Check the optional task-registration settings of a launch; returns ``python_path``, ``env``, ``external_callback``.

    ``python_path`` is a list of existing absolute directories (prepended to PYTHONPATH), ``env`` a dict of extra
    environment variables, ``external_callback`` a dotted ``module.func`` passed to Isaac Lab as
    ``--external_callback=...``. Without ``external_callback``, ``ISAACLAB_EXTERNAL_CALLBACK`` is the default.
    """
    paths: list[str] = []
    if python_path is not None:
        if isinstance(python_path, str) or not isinstance(python_path, (list, tuple)):
            raise ValueError("python_path must be a list of directories")
        for entry in python_path:
            if not isinstance(entry, str) or not entry.strip() or "\0" in entry or os.pathsep in entry:
                raise ValueError(f"invalid python_path entry: {entry!r}")
            d = Path(entry).expanduser()
            if not d.is_absolute():
                raise ValueError(f"python_path entries must be absolute directories: {entry!r}")
            if not d.is_dir():
                raise ValueError(f"python_path entry is not an existing directory: {entry!r}")
            paths.append(str(d))
    extra: dict[str, str] = {}
    if env is not None:
        if not isinstance(env, dict):
            raise ValueError("env must be an object of variable names to string values")
        for key, value in env.items():
            if not isinstance(key, str) or not _ENV_KEY_RE.match(key):
                raise ValueError(f"invalid env variable name: {key!r}: use letters, digits and underscores")
            upper = key.upper()
            if key not in _SAFE_PYTHON_ENV and (upper in _PROTECTED_ENV or upper.startswith(_PROTECTED_ENV_PREFIXES)):
                hint = " Use python_path instead." if upper == "PYTHONPATH" else ""
                raise ValueError(f"env variable {key} is protected and cannot be set here.{hint}")
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError(f"env value for {key} must be a string")
            text = str(value)
            if "\0" in text or len(text) > _MAX_ENV_VALUE:
                raise ValueError(f"env value for {key} is invalid or too long")
            extra[key] = text
    callback = external_callback
    if callback is None:
        callback = str(_env(environ).get("ISAACLAB_EXTERNAL_CALLBACK") or "").strip() or None
    if callback is not None and (not isinstance(callback, str) or not _CALLBACK_RE.match(callback)):
        raise ValueError(f"invalid external_callback: {callback!r}: use a dotted module.function such as pkg.register.register")
    return {"python_path": paths, "env": extra, "external_callback": callback}


EXTERNAL_TASKS_HINT = (
    "Tasks from an external project register only when its package is importable and its registration runs. "
    "Pass python_path=['<dir containing the package>'] and external_callback='pkg.module.register' (passed to Isaac Lab as "
    "--external_callback=pkg.module.register), or configure the server with ISAACLAB_TASK_PATH (directories) plus "
    "ISAACLAB_TASK_MODULES (modules to import) or ISAACLAB_EXTERNAL_CALLBACK. Example: "
    "isaaclab__envs_list(python_path=['/path/to/project/src'], external_callback='my_pkg.register.register', filter='my_task')."
)


_TASK_ID_RE = re.compile(r"""\bid\s*=\s*["'](Isaac-[A-Za-z0-9_.-]+)["']""")


def scan_task_ids(root: Any) -> list[str]:
    """Task ids registered in a checkout's isaaclab_tasks source, read as text: nothing is imported."""
    if not root:
        return []
    found: set[str] = set()
    for source in sorted(Path(root).joinpath("source", "isaaclab_tasks").rglob("*.py")):
        try:
            found.update(_TASK_ID_RE.findall(source.read_text(errors="replace")))
        except OSError:
            continue
    return sorted(found)


def envs_list(filter: Any = None, env: Any = None, timeout: int = 60, python_path: Any = None,
              task_env: Any = None, external_callback: Any = None) -> dict:
    opts = validate_launch_options(python_path, task_env, external_callback, env)
    loc = locate_install(env)
    if not loc:
        return dict(_MISSING)
    python = loc["python"]
    root = loc["isaaclab_root"]

    run_env = dict(os.environ)
    run_env.update(opts["env"])
    run_env["OMNI_KIT_ACCEPT_EULA"] = "YES"
    modules, paths = resolve_task_modules(root, {**_env(env), **opts["env"]})
    paths = opts["python_path"] + paths
    existing_pp = run_env.get("PYTHONPATH")
    if existing_pp:
        paths.append(existing_pp)
    if paths:
        run_env["PYTHONPATH"] = os.pathsep.join(paths)

    code = (
        "import importlib, json\n"
        "import gymnasium\n"
        "builtin = set(getattr(gymnasium, 'registry', {}))\n"
        "errors = {}\n"
        f"for _m in ['isaaclab_tasks'] + {modules!r}:\n"
        "    try:\n"
        "        importlib.import_module(_m)\n"
        "    except BaseException as _exc:  # a task module may sys.exit(); keep the rest\n"
        "        try:\n"
        "            errors[_m] = f'{type(_exc).__name__}: {_exc}'\n"
        "        except Exception:\n"
        "            errors[_m] = type(_exc).__name__\n"
        f"_cb = {opts['external_callback']!r}\n"
        "if _cb:\n"
        "    try:\n"
        "        _mod, _fn = _cb.rsplit('.', 1)\n"
        "        getattr(importlib.import_module(_mod), _fn)()\n"
        "    except BaseException as _exc:\n"
        "        errors['external_callback ' + _cb] = f'{type(_exc).__name__}: {_exc}'\n"
        "envs = []\n"
        "for _eid, _spec in getattr(gymnasium, 'registry', {}).items():\n"
        "    try:\n"
        "        _ep = _spec.entry_point\n"
        "    except Exception:\n"
        "        _ep = None\n"
        "    envs.append({'id': str(_eid), 'entry_point': (str(_ep) if _ep is not None else None)})\n"
        "print(json.dumps({'envs': envs, 'import_errors': errors, 'gymnasium_builtin': sorted(builtin)}))\n"
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
    isaac_tasks_failed = "isaaclab_tasks" in (data.get("import_errors") or {})
    if isaac_tasks_failed:
        # Only gymnasium's own tasks would be left, such as classic-control CartPole. Do not pass them off as Isaac's.
        builtin = set(data.get("gymnasium_builtin") or [])
        envs = [x for x in envs if x.get("id") not in builtin]
    if filter is not None:
        needle = str(filter).lower()
        envs = [x for x in envs if needle in str(x.get("id", "")).lower()]
    result: dict = {"envs": envs}
    if isaac_tasks_failed:
        known = {x.get("id") for x in envs}
        scanned = [dict(id=task, source="scanned") for task in scan_task_ids(root) if task not in known
                   and (filter is None or str(filter).lower() in task.lower())]
        result["envs"] = envs + scanned
        result["isaac_tasks_unavailable"] = ("isaaclab_tasks can't be imported here (see import_errors), which is normal "
                                             "where it needs Kit. Entries marked source=scanned were read from the "
                                             "checkout's source without running it; use those exact ids, with their "
                                             "-v0 where they have one.")
    if data.get("import_errors"):
        result["import_errors"] = data["import_errors"]
    if opts["python_path"] or opts["external_callback"]:
        result["applied"] = {"python_path": opts["python_path"], "modules": modules,
                             "external_callback": opts["external_callback"]}
    if not result["envs"]:
        what = f"No task id contains {filter!r}." if filter is not None else "No tasks were found."
        result["hint"] = what + " " + EXTERNAL_TASKS_HINT
    return result

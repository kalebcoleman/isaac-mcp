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
    # A workspace keeps each environment beside its checkout: envs/<name> with envs/<name>-src.
    sibling = venv.parent / f"{venv.name}-src"
    if (sibling / "scripts" / "reinforcement_learning").is_dir():
        return sibling
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


_INSTALLS_ENV = "ISAACLAB_INSTALLS"
_DIST_RE = re.compile(r"^isaaclab-(?P<v>[0-9][^-]*)\.dist-info$")


def _venv_lab_version(venv: Path) -> str | None:
    """The isaaclab distribution version of a virtual environment, from its files; nothing is run."""
    for lib in ("lib", "lib64"):
        for site in (venv / lib).glob("python*/site-packages"):
            for d in site.glob("isaaclab-*.dist-info"):
                m = _DIST_RE.match(d.name)
                if m:
                    return m.group("v")
    return None


def _install_record(python: Path, root: Path | None, source: str) -> dict | None:
    venv = _derive_venv(python)
    if venv is None or not python.is_file() or not os.access(python, os.X_OK) or not _has_isaaclab_metadata(venv):
        return None
    root = root or _derive_root(python)
    return {"name": venv.name, "python": str(python), "isaaclab_root": str(root) if root else None,
            "venv": str(venv), "isaaclab_version": _venv_lab_version(venv), "source": source}


def _records_under(entry: str, source: str) -> list[dict]:
    """Installs named by one path: a venv folder, its interpreter, a Lab checkout (with .venv), or a folder of venvs."""
    p = Path(entry).expanduser()
    if not p.is_absolute() or not p.exists():
        return []
    if p.is_file():
        rec = _install_record(p, None, source)
        return [rec] if rec else []
    if (p / "bin" / "python").is_file():
        rec = _install_record(p / "bin" / "python", None, source)
        return [rec] if rec else []
    if (p / ".venv" / "bin" / "python").is_file() and (p / "scripts" / "reinforcement_learning").is_dir():
        rec = _install_record(p / ".venv" / "bin" / "python", p, source)
        return [rec] if rec else []
    out = []
    for py in sorted(p.glob("*/bin/python")):
        rec = _install_record(py, None, source)
        if rec:
            out.append(rec)
    return out


def _isaac_env_candidates(env: Any) -> list[str]:
    """Interpreters scripts/isaac_env.py finds (it reads files only); empty when the script is not beside this package."""
    script = Path(__file__).resolve().parents[3] / "scripts" / "isaac_env.py"
    if not script.is_file():
        return []
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_isaac_env_discovery", script)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return [c["path"] for c in mod.find_candidates(dict(env)) if c.get("kind") == "pip" and c.get("isaaclab")]
    except Exception:  # noqa: BLE001 - discovery is a convenience; the explicit forms still work
        return []


def known_installs(env: Any = None) -> list[dict]:
    """Isaac Lab installs this server can target by name: the configured default, ``ISAACLAB_INSTALLS`` (os.pathsep list of
    venvs, interpreters, checkouts or folders of venvs), the folder holding the default's venv and the workspace's
    ``envs/`` (where isaac_env.py looks), and what isaac_env.py finds. Read from files; nothing is run."""
    e = _env(env)
    found: dict[str, dict] = {}

    def add(rec: dict | None) -> None:
        if rec and os.path.realpath(rec["venv"]) not in found:
            found[os.path.realpath(rec["venv"])] = rec

    default = (locate_install() if env is None else locate_install(e))
    if default and default.get("python"):
        add(_install_record(Path(default["python"]), Path(default["isaaclab_root"]) if default.get("isaaclab_root") else None, "default"))
        if found:
            next(iter(found.values()))["default"] = True
        venv = _derive_venv(Path(default["python"]))
        if venv is not None:
            for rec in _records_under(str(venv.parent), "sibling"):
                add(rec)
    workspace = str(e.get("ISAAC_MCP_WORKSPACE") or "").strip()
    if workspace:
        for rec in _records_under(str(Path(workspace).expanduser() / "envs"), "workspace"):
            add(rec)
    for entry in str(e.get(_INSTALLS_ENV) or "").split(os.pathsep):
        if entry.strip():
            for rec in _records_under(entry.strip(), _INSTALLS_ENV):
                add(rec)
    for path in _isaac_env_candidates(e):
        add(_install_record(Path(path), None, "isaac_env.py"))
    return [r for r in found.values() if r.get("isaaclab_version")]


def _public_install(rec: dict) -> dict:
    return {k: rec.get(k) for k in ("name", "isaaclab_version", "python", "isaaclab_root", "source", "default") if rec.get(k) is not None}


def _project_install_selector(env: Any) -> str | None:
    """``isaaclab_install`` from the project file named by ISAAC_MCP_PROJECT (never a file found by walking up)."""
    file = _named_project_file(env)
    if file is None:
        return None
    try:
        value = json.loads(file.read_text(encoding="utf-8")).get("isaaclab_install")
    except (OSError, ValueError, AttributeError):
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _matches(rec: dict, sel: str) -> bool:
    s = sel.lower().lstrip("v")
    name, ver = rec["name"].lower(), str(rec.get("isaaclab_version") or "").lower()
    return (s == name or s == f"lab-{name}" or re.search(rf"(?<![0-9.]){re.escape(s)}(?![0-9])", name) is not None
            or ver == s or ver.startswith(s + "."))


def resolve_install(selector: Any, env: Any = None) -> tuple[dict | None, str | None]:
    """Pick the Isaac Lab install for one call: ``(location, None)`` or ``(None, error)``.

    ``selector`` is None/"default" (the configured default, which stays Isaac Lab 3.0.0 on this setup), a known install's name
    or version (``lab-2.3.2``, ``2.3``, ``3.0.0``; see isaaclab__install_info's ``known_installs``), or an absolute path to a
    venv, its interpreter, or a Lab checkout holding a ``.venv``. A path must be a virtual environment with Isaac Lab
    installed (checked from its files). The server's ISAACLAB_PYTHON is never changed, and nothing is restarted."""
    e = _env(env)
    if selector is None or (isinstance(selector, str) and not selector.strip()):
        selector = _project_install_selector(e)  # an explicit project choice wins over the harness default
    if selector is None or selector == "default":
        loc = (locate_install() if env is None else locate_install(e))
        return (loc, None) if loc else (None, "no default Isaac Lab install is configured")
    if not isinstance(selector, str) or "\0" in selector:
        return None, "install must be a name, a version or an absolute path"
    if selector.startswith(("/", "~")):
        recs = _records_under(selector, "path")
        if len(recs) != 1:
            return None, (f"install {selector!r} is not one virtual environment with Isaac Lab installed "
                          "(give its folder, its bin/python, or a checkout with a .venv)")
        recs[0]["source"] = f"install:{selector}"
        return recs[0], None
    known = known_installs(e)
    hits = [r for r in known if _matches(r, selector)]
    exact = [r for r in hits if r["name"].lower() == selector.lower()]
    hits = exact or hits
    if len(hits) > 1:  # duplicates of one Lab (a sim env holding the same Lab): prefer the one that has a checkout
        rooted = [r for r in hits if r.get("isaaclab_root")]
        hits = rooted if len(rooted) == 1 else hits
    if len(hits) == 1:
        return {**hits[0], "source": f"install:{selector}"}, None
    names = ", ".join(f"{r['name']} (isaaclab {r['isaaclab_version']})" for r in known) or "none found"
    what = "matches several installs" if hits else "matches no known install"
    return None, (f"install {selector!r} {what}. Known: {names}. Use a more specific name, an absolute path, "
                  f"or list more with {_INSTALLS_ENV} (os.pathsep list of venvs).")


def locate_install(env: Any = None, install: Any = None) -> dict | None:
    if install is not None and install != "default":
        return resolve_install(install, env)[0]
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


def install_info(env: Any = None, install: Any = None) -> dict:
    loc, why = resolve_install(install, env)
    if why and install is not None:
        return {"error": why, "known_installs": [_public_install(r) for r in known_installs(env)]}
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
        "known_installs": [_public_install(r) for r in known_installs(env)],
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
# Variables a caller may not set, each with what to use instead. Only what would break or hijack the chosen install, the job
# supervisor or this server is refused; everything else (HOME, TMPDIR, PYTHON* other than PYTHONHOME, OMNI_KIT_ACCEPT_EULA,
# CUDA_VISIBLE_DEVICES, HEADLESS, ...) is accepted.
_REFUSED_ENV = {
    "PYTHONHOME": "it would replace the install's own Python; pass install='<name or venv path>' to use another Isaac Lab",
    "ISAACLAB_PYTHON": "pass install='<name or venv path>' to choose the Isaac Lab for one call",
    "ISAACLAB_ROOT": "pass install='<name or venv path>' to choose the Isaac Lab for one call",
    "ISAACSIM_PATH": "pass install='<name or venv path>' to choose the Isaac Lab / Sim for one call",
    "ISAAC_MCP_PYTHON": "pass install='<name or venv path>' to choose the install for one call",
    "ISAACLAB_JOBS_DIR": "it is a server setting (MCP client config), not a per-call one",
    "LD_PRELOAD": "it would inject a library into every process the job starts; put it in a wrapper script and run that with run_script",
    "LD_AUDIT": "it would inject a library into every process the job starts; put it in a wrapper script and run that with run_script",
    "DYLD_INSERT_LIBRARIES": "it would inject a library into every process the job starts",
    "BASH_ENV": "it would run code in the shell that supervises the job",
    "ENV": "it would run code in the shell that supervises the job",
    "IFS": "it would change how the supervising shell splits words",
    "CDPATH": "it would change how the supervising shell changes directory; use the cwd argument",
}
_REFUSED_ENV_PREFIXES = {
    "ISAAC_MCP_": "these are this server's own settings; set them in the MCP client config",
    "BASH_FUNC_": "exported shell functions would run in the shell that supervises the job",
}
# Added in front of what the install already has, never replacing it.
_PREPEND_ENV = frozenset({"PATH", "LD_LIBRARY_PATH", "PYTHONPATH"})
_MAX_ENV_VALUE = 4096


def validate_env(env: Any) -> dict[str, str]:
    """The one environment-variable policy of every Isaac Lab tool (train, eval, play, env_check, record, envs_list, run_script).

    Names are identifiers, values short strings without NUL. PATH, LD_LIBRARY_PATH and PYTHONPATH are *prepended* to the
    install's own value (see apply_env), so a project can add import roots and libraries without losing the install's.
    Refused only: what would replace or hijack the install, the job supervisor or this server (PYTHONHOME, LD_PRELOAD,
    LD_AUDIT, BASH_ENV, ISAACLAB_PYTHON/ROOT, ISAAC_MCP_*, ...); each refusal names the argument to use instead.
    """
    extra: dict[str, str] = {}
    if env is None:
        return extra
    if not isinstance(env, dict):
        raise ValueError("env must be an object of variable names to string values")
    for key, value in env.items():
        if not isinstance(key, str) or not _ENV_KEY_RE.match(key):
            raise ValueError(f"invalid env variable name: {key!r}: use letters, digits and underscores")
        upper = key.upper()
        advice = _REFUSED_ENV.get(upper) or next((m for p, m in _REFUSED_ENV_PREFIXES.items() if upper.startswith(p)), None)
        if advice:
            raise ValueError(f"env variable {key} is not accepted by any Isaac Lab tool: {advice}.")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError(f"env value for {key} must be a string")
        text = str(value)
        if "\0" in text or len(text) > _MAX_ENV_VALUE:
            raise ValueError(f"env value for {key} is invalid or too long")
        extra[key] = text
    return extra


def apply_env(base: dict, extra: dict) -> None:
    """Put validated ``extra`` variables into a child environment: PATH, LD_LIBRARY_PATH and PYTHONPATH go in front of the
    existing value, the rest replace it."""
    for key, value in extra.items():
        if key.upper() in _PREPEND_ENV and base.get(key):
            base[key] = value + os.pathsep + base[key] if value else base[key]
        else:
            base[key] = value


def merge_python_paths(python_path: Any = None, python_paths: Any = None) -> list[str] | None:
    """``python_paths`` (the documented argument) and its older alias ``python_path``, merged without repeats; None when neither is given."""
    if python_path is None and python_paths is None:
        return None
    merged: list[Any] = []
    for part in (python_paths, python_path):
        if part is None:
            continue
        if isinstance(part, str) or not isinstance(part, (list, tuple)):
            raise ValueError("python_paths must be a list of directories")
        merged.extend(x for x in part if x not in merged)
    return merged


_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*\Z")


def validate_cwd(cwd: Any) -> Path | None:
    """A working directory for a job: an existing directory (``~`` expanded), or None when not given."""
    if cwd is None or cwd == "":
        return None
    if not isinstance(cwd, (str, os.PathLike)) or "\0" in str(cwd):
        raise ValueError("cwd must be a directory path")
    d = Path(cwd).expanduser()
    if not d.is_dir():
        raise ValueError(f"cwd is not a directory: {d}")
    return d.resolve()


def validate_python_path(python_path: Any) -> list[str]:
    """``python_paths`` for every Isaac Lab tool (run_script included): a list of existing absolute directories."""
    paths: list[str] = []
    if python_path is None:
        return paths
    if isinstance(python_path, str) or not isinstance(python_path, (list, tuple)):
        raise ValueError("python_paths must be a list of directories")
    for entry in python_path:
        if not isinstance(entry, str) or not entry.strip() or "\0" in entry or os.pathsep in entry:
            raise ValueError(f"invalid python_paths entry: {entry!r}")
        d = Path(entry).expanduser()
        if not d.is_absolute():
            raise ValueError(f"python_paths entries must be absolute directories: {entry!r}")
        if not d.is_dir():
            raise ValueError(f"python_paths entry is not an existing directory: {entry!r}")
        paths.append(str(d))
    return paths


def validate_launch_options(python_path: Any = None, env: Any = None, external_callback: Any = None,
                            environ: Any = None, task_modules: Any = None) -> dict:
    """Check the optional task-registration settings of a launch; returns ``python_path``, ``env``, ``external_callback``.

    ``python_path`` is a list of existing absolute directories (prepended to PYTHONPATH, as is any PYTHONPATH in ``env``), ``env`` a dict of extra
    environment variables, ``external_callback`` a dotted ``module.func`` passed to Isaac Lab as
    ``--external_callback=...``. Without ``external_callback``, ``ISAACLAB_EXTERNAL_CALLBACK`` is the default.
    """
    paths = validate_python_path(python_path)
    extra = validate_env(env)
    if task_modules is not None:
        if isinstance(task_modules, str) or not isinstance(task_modules, (list, tuple)):
            raise ValueError("task_modules must be a list of dotted module names")
        mods = []
        for m in task_modules:
            if not isinstance(m, str) or not _MODULE_RE.match(m):
                raise ValueError(f"invalid task_modules entry: {m!r}: use a dotted module name such as pkg.tasks")
            mods.append(m)
        if mods:  # same effect as ISAACLAB_TASK_MODULES, which the launchers import before the task is looked up
            have = [x.strip() for x in extra.get("ISAACLAB_TASK_MODULES", "").split(",") if x.strip()]
            extra["ISAACLAB_TASK_MODULES"] = ",".join(have + [m for m in mods if m not in have])
    callback = external_callback
    if callback is None:
        callback = str(_env(environ).get("ISAACLAB_EXTERNAL_CALLBACK") or "").strip() or None
    if callback is not None and (not isinstance(callback, str) or not _CALLBACK_RE.match(callback)):
        raise ValueError(f"invalid external_callback: {callback!r}: use a dotted module.function such as pkg.register.register")
    return {"python_path": paths, "env": extra, "external_callback": callback}


EXTERNAL_TASKS_HINT = (
    "Tasks from an external project register only when its package is importable and its registration runs. "
    "Pass python_paths=['<dir containing the package>'] and external_callback='pkg.module.register' (passed to Isaac Lab as "
    "--external_callback=pkg.module.register), or configure the server with ISAACLAB_TASK_PATH (directories) plus "
    "ISAACLAB_TASK_MODULES (modules to import) or ISAACLAB_EXTERNAL_CALLBACK. Example: "
    "isaaclab__envs_list(python_paths=['/path/to/project/src'], external_callback='my_pkg.register.register', filter='my_task')."
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
              task_env: Any = None, external_callback: Any = None, task_modules: Any = None,
              install: Any = None) -> dict:
    opts = validate_launch_options(python_path, task_env, external_callback, env, task_modules)
    loc, why = resolve_install(install, env)
    if why and install is not None:
        return {"error": why}
    if not loc:
        return dict(_MISSING)
    python = loc["python"]
    root = loc["isaaclab_root"]

    run_env = dict(os.environ)
    apply_env(run_env, opts["env"])
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

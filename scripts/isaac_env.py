#!/usr/bin/env python3
"""Find an existing Isaac Sim / Isaac Lab Python environment without running it.

Linux only.  Standard library only.  No interpreter is ever executed: versions
are read from ``*.dist-info/METADATA``, ``isaacsim/VERSION`` and a standalone
install's ``VERSION`` file.

Commands:
  find [--json]                      list every distinct candidate found
  resolve-python [--require-version X.Y[.Z]] [--lab]
                                     print exactly one interpreter path
  init [--workspace DIR]             create <workspace>/{assets,envs,projects,runs}
  where [--json]                     show the workspace and the chosen interpreter
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXPLICIT_VARS = ("ISAAC_MCP_PYTHON", "UR5E_DT_PYTHON", "ISAAC_SIM_PYTHON")
CONDA_ROOTS = ("miniconda3", "anaconda3", "miniforge3", "mambaforge", ".conda")
HOME_VENV_DIRS = ("venvs", ".virtualenvs", "envs")
STANDALONE_ENV_VARS = ("ISAACSIM_PATH", "ISAAC_SIM_PATH", "ISAAC_PATH")
STANDALONE_HOME_DIRS = ("isaacsim", "isaac-sim")
SYSTEM_ROOT_GLOBS = ("/opt/nvidia/isaac-sim*", "/isaac-sim")
DEFAULT_WORKSPACE_NAME = "isaac-workspace"
WORKSPACE_DIRS = ("assets", "envs", "projects", "runs")

README = """\
# Isaac workspace

Created by `scripts/isaac_env.py init`.  Nothing here is deleted or overwritten
by the tools.

- `assets/`   simulation assets (USD, URDF, meshes) shared by your projects.
- `envs/`     Python environments, one folder each (`envs/<name>/bin/python`).
              `scripts/isaac_env.py find` picks them up automatically.
- `projects/` one folder per project; put an `isaac-mcp.json` in each.
- `runs/`     training runs, logs, recordings and other outputs.

Set `ISAAC_MCP_WORKSPACE` to this folder to make it the default.
"""


# ---------------------------------------------------------------- versions

def parse_version(text):
    """'6.2.0.0-rc1+x' -> (6, 2, 0, 0); None when there are no digits."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _read(path, limit=65536):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return None


def _metadata_version(dist_info):
    text = _read(Path(dist_info) / "METADATA")
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith("Version:"):
            value = line.split(":", 1)[1].strip()
            return value if parse_version(value) else None
        if not line.strip():
            break
    return None


def _dist_version(site, name):
    """Return (present, version) for a pip package in ``site``."""
    present, version = False, None
    for info in sorted(glob.glob(os.path.join(glob.escape(str(site)), f"{name}-*.dist-info"))):
        present = True
        version = _metadata_version(info) or version
    pkg = Path(site) / name
    if pkg.is_dir():
        present = True
        if version is None:
            text = _read(pkg / "VERSION")
            first = text.strip().splitlines()[0] if text and text.strip() else ""
            version = first if parse_version(first) else None
    return present, version


# --------------------------------------------------------------- inspection

def _site_packages(prefix):
    return sorted(Path(p) for p in glob.glob(os.path.join(glob.escape(str(prefix)), "lib", "python3.*", "site-packages"))
                  if os.path.isdir(p))


def _py_from_site(site):
    m = re.match(r"python(3\.\d+)$", Path(site).parent.name)
    return m.group(1) if m else None


def inspect_python(python, source):
    """Describe a pip-style interpreter without running it, or None."""
    python = Path(python)
    if not python.exists():
        return None
    prefix = python.parent.parent
    sites = _site_packages(prefix)
    if not sites:
        # A symlink living outside its venv: follow the link once.
        try:
            real = Path(os.path.realpath(python))
        except OSError:
            return None
        prefix = real.parent.parent
        sites = _site_packages(prefix)
    sim = (False, None)
    lab = (False, None)
    site_used = None
    for site in sites:
        s, lb = _dist_version(site, "isaacsim"), _dist_version(site, "isaaclab")
        if s[0] or lb[0]:
            sim = s if s[0] else sim
            lab = lb if lb[0] else lab
            site_used = site
            break
    if site_used is None:
        return None
    return {"path": str(python), "kind": "pip", "isaacsim": sim[1], "isaaclab": lab[1],
            "has_isaacsim": sim[0], "python": _py_from_site(site_used), "source": source,
            "_key": os.path.realpath(prefix)}


def standalone_python(root):
    root = Path(root)
    for rel in ("python.sh", "kit/python/bin/python3"):
        if (root / rel).is_file():
            return root / rel
    return None


def inspect_standalone(root, source):
    root = Path(root)
    interp = standalone_python(root)
    if interp is None:
        return None
    text = _read(root / "VERSION")
    first = text.strip().splitlines()[0] if text and text.strip() else ""
    version = first if parse_version(first) else None
    if version is None and not (root / "VERSION").exists() and interp.name != "python.sh":
        return None
    pyver = lab = None
    for site in _site_packages(root / "kit" / "python"):
        pyver = _py_from_site(site)
        lab = _dist_version(site, "isaaclab")[1] or lab
    return {"path": str(interp), "kind": "standalone", "isaacsim": version, "isaaclab": lab,
            "has_isaacsim": True, "python": pyver, "source": source, "_key": os.path.realpath(root),
            "root": str(root)}


def inspect_any(path, source):
    """Inspect a user-supplied path: interpreter, python.sh or install root."""
    p = Path(os.path.expanduser(str(path)))
    if p.is_dir():
        if standalone_python(p):
            return inspect_standalone(p, source)
        p = p / "bin" / "python"
    if p.name == "python.sh" and p.is_file():
        return inspect_standalone(p.parent, source)
    return inspect_python(p, source)


# ---------------------------------------------------------------- searching

def default_system_roots():
    out = []
    for pattern in SYSTEM_ROOT_GLOBS:
        out.extend(sorted(glob.glob(pattern)))
    return out


def workspace_dir(env, repo=REPO):
    """The workspace that is in effect, as (path or None, how)."""
    value = env.get("ISAAC_MCP_WORKSPACE")
    if value:
        return Path(os.path.expanduser(value)), "env:ISAAC_MCP_WORKSPACE"
    parent = Path(repo).parent
    if (parent / "envs").is_dir():
        return parent, "checkout parent (has envs/)"
    return None, "none"


def _interp_rules(env, home, repo):
    """Yield (source, python path) for pip-style rules, in priority order."""
    repo, home = Path(repo), Path(home)
    ws, _ = workspace_dir(env, repo)
    if ws is not None:
        for p in sorted(glob.glob(os.path.join(glob.escape(str(ws)), "envs", "*", "bin", "python"))):
            yield "workspace-envs", p
    yield "repo-venv", repo / ".venv" / "bin" / "python"
    for var in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        if env.get(var):
            yield f"env:{var}", Path(env[var]) / "bin" / "python"
    parent = repo.parent
    for p in sorted(glob.glob(os.path.join(glob.escape(str(parent)), "*", ".venv", "bin", "python"))):
        yield "sibling-venv", p
    yield "sibling-venv", parent / ".venv" / "bin" / "python"
    for root in CONDA_ROOTS:
        for p in sorted(glob.glob(os.path.join(glob.escape(str(home / root)), "envs", "*", "bin", "python"))):
            yield "conda-root", p
    for d in HOME_VENV_DIRS:
        for p in sorted(glob.glob(os.path.join(glob.escape(str(home / d)), "*", "bin", "python"))):
            yield "home-venvs", p


def _standalone_rules(env, home, system_roots):
    home = Path(home)
    for var in STANDALONE_ENV_VARS:
        if env.get(var):
            yield f"standalone:{var}", Path(os.path.expanduser(env[var]))
    for name in STANDALONE_HOME_DIRS:
        yield "standalone:home", home / name
    for p in sorted(glob.glob(os.path.join(glob.escape(str(home)), ".local", "share", "ov", "pkg", "isaac-sim-*"))):
        yield "standalone:ov-pkg", Path(p)
    for p in system_roots:
        yield "standalone:system", Path(p)


def explicit_choice(env):
    """(var, value) of the first explicit interpreter variable that is set."""
    for var in EXPLICIT_VARS:
        if env.get(var):
            return var, env[var]
    return None, None


def find_candidates(env=None, home=None, repo=REPO, system_roots=None):
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    system_roots = default_system_roots() if system_roots is None else system_roots
    found, seen = [], set()

    def add(item):
        if item and item["_key"] not in seen:
            seen.add(item["_key"])
            item["rule"] = len(found)
            found.append(item)

    for var in EXPLICIT_VARS:
        if env.get(var):
            add(inspect_any(env[var], f"env:{var}"))
    for source, path in _interp_rules(env, home, repo):
        add(inspect_python(path, source))
    for source, root in _standalone_rules(env, home, system_roots):
        add(inspect_standalone(root, source))
    return found


def _public(c):
    return {k: c[k] for k in ("path", "kind", "isaacsim", "isaaclab", "python", "source")}


# ---------------------------------------------------------------- selection

def select(env, candidates, require=None, lab=False):
    """Return (candidate-or-None, error-or-None)."""
    var, value = explicit_choice(env)
    if var:
        item = inspect_any(value, f"env:{var}")
        expanded = Path(os.path.expanduser(value))
        if item is not None:
            return item, None
        if expanded.is_file() and os.access(expanded, os.X_OK):
            return ({"path": str(expanded), "kind": "pip", "isaacsim": None, "isaaclab": None,
                     "python": None, "source": f"env:{var}", "has_isaacsim": False}, None)
        return None, f"{var} points to {value}, which is not an existing Isaac interpreter or install"
    pool = [c for c in candidates if c["has_isaacsim"]]
    if require:
        want = parse_version(require)
        if want is None:
            return None, f"invalid --require-version {require!r}"
        pool = [c for c in pool if (parse_version(c["isaacsim"]) or ())[:len(want)] == want]
        if not pool:
            return None, f"no Isaac Sim {require} environment found (run scripts/isaac_env.py find)"
    if not pool:
        return None, "no Isaac Sim environment found (run scripts/isaac_env.py find)"

    def key(c):
        return (1 if lab and c["isaaclab"] else 0, parse_version(c["isaacsim"]) or (), -c["rule"])
    return max(pool, key=key), None


def _why(c, require, lab):
    if c["source"].startswith("env:") and c["source"][4:] in EXPLICIT_VARS:
        return f"set explicitly by {c['source'][4:]}"
    bits = [f"found by rule '{c['source']}'", f"isaacsim {c['isaacsim'] or 'unknown'}"]
    if lab:
        bits.append(f"isaaclab {c['isaaclab'] or 'absent'}")
    if require:
        bits.append(f"matches {require}")
    return ", ".join(bits)


# ----------------------------------------------------------------- commands

def cmd_find(args, env):
    cands = find_candidates(env)
    if args.json:
        print(json.dumps([_public(c) for c in cands], indent=2))
        return 0
    if not cands:
        print("no Isaac Sim / Isaac Lab environment found")
        return 0
    rows = [("PATH", "KIND", "ISAACSIM", "ISAACLAB", "PYTHON", "SOURCE")]
    rows += [(c["path"], c["kind"], c["isaacsim"] or "-", c["isaaclab"] or "-", c["python"] or "-", c["source"])
             for c in cands]
    widths = [max(len(r[i]) for r in rows) for i in range(6)]
    for r in rows:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip())
    return 0


def cmd_resolve(args, env):
    chosen, err = select(env, find_candidates(env), args.require_version, args.lab)
    if chosen is None:
        print(err, file=sys.stderr)
        return 1
    print(chosen["path"])
    return 0


def _default_workspace(env):
    return Path(os.path.expanduser(env.get("ISAAC_MCP_WORKSPACE") or "~/" + DEFAULT_WORKSPACE_NAME))


def cmd_init(args, env, repo=REPO):
    ws = Path(os.path.expanduser(args.workspace)) if args.workspace else _default_workspace(env)
    ws = Path(os.path.abspath(ws))
    real = Path(os.path.realpath(ws))
    repo_real = Path(os.path.realpath(repo))
    if real == repo_real or repo_real in real.parents:
        print(f"refusing to create the workspace inside the checkout ({repo_real}); "
              "choose a folder outside it", file=sys.stderr)
        return 2
    if ws.exists() and not ws.is_dir():
        print(f"{ws} exists and is not a directory", file=sys.stderr)
        return 2
    try:
        ws.mkdir(parents=True, exist_ok=True)
        for name in WORKSPACE_DIRS:
            (ws / name).mkdir(exist_ok=True)
        readme = ws / "README.md"
        if not readme.exists():
            readme.write_text(README, encoding="utf-8")
    except OSError as exc:
        print(f"cannot create workspace {ws}: {exc}", file=sys.stderr)
        return 1
    print(f"workspace ready: {ws}\n")
    print("Add to your shell profile:")
    print(f"  export ISAAC_MCP_WORKSPACE=\"{ws}\"")
    print(f"  export ISAAC_MCP_ASSET_PATH=\"{ws}/assets\"\n")
    print(f"Example {ws}/projects/<name>/isaac-mcp.json:")
    print(json.dumps({"asset_path": ["../../assets", "./assets"], "strict": False}, indent=2))
    return 0


def cmd_where(args, env):
    ws, how = workspace_dir(env)
    chosen, err = select(env, find_candidates(env))
    info = {"workspace": str(ws) if ws else None, "workspace_source": how,
            "default_workspace": str(_default_workspace(env)),
            "interpreter": chosen["path"] if chosen else None,
            "interpreter_why": _why(chosen, None, False) if chosen else err}
    if args.json:
        print(json.dumps(info, indent=2))
        return 0
    print(f"workspace:   {info['workspace'] or '(none; `init` would use ' + info['default_workspace'] + ')'}"
          f"  [{how}]")
    print(f"interpreter: {info['interpreter'] or '(none)'}")
    print(f"why:         {info['interpreter_why']}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="isaac_env.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="list all candidate environments")
    f.add_argument("--json", action="store_true")
    r = sub.add_parser("resolve-python", help="print the interpreter to use")
    r.add_argument("--require-version", metavar="X.Y[.Z]")
    r.add_argument("--lab", action="store_true", help="prefer environments that also have Isaac Lab")
    i = sub.add_parser("init", help="create the workspace layout")
    i.add_argument("--workspace", metavar="DIR")
    w = sub.add_parser("where", help="show workspace and chosen interpreter")
    w.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    if not sys.platform.startswith("linux"):
        print(f"unsupported platform: {sys.platform}; isaac_env.py supports Linux (x86_64) only",
              file=sys.stderr)
        return 2
    args = build_parser().parse_args(argv)
    env = os.environ
    return {"find": cmd_find, "resolve-python": cmd_resolve, "init": cmd_init,
            "where": cmd_where}[args.cmd](args, env)


if __name__ == "__main__":
    sys.exit(main())

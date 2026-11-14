"""Resolve startup configuration without importing the MCP SDK or simulator."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

try:
    import tomllib
except ModuleNotFoundError:          # Python before 3.11: tomli, if it is installed
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

from .toolsets import DEFAULT, PROFILES, TOOLSETS

# isaac-mcp, this project's name before 0.3.0, read these settings and files; 0.3 still honoured them, and nothing
# reads them now. A server that finds one refuses to start rather than ignore it: a leftover ISAAC_MCP_READ_ONLY,
# ISAAC_MCP_TOOLSETS or isaac-mcp.toml would otherwise change what a client may do, or the physics engine, without a
# word. docs/MIGRATING.md maps each one to its new name. The Kit bridge applies the same rule (twinsmith_bridge/knobs.py).
OLD_PREFIXES = ("ISAAC_MCP_", "UR5E_DT_")
OLD_VARIABLES = ("ISAAC_SIM_PORT", "ISAAC_SIM_PYTHON", "ISAACLAB_JOBS_DIR")
OLD_USER_FILE = ".config/isaac-mcp/config.toml"     # now .config/twinsmith/config.toml
OLD_PROJECT_FILE = "isaac-mcp.toml"                 # now twinsmith.toml
# What an old variable became, from the table in docs/MIGRATING.md. Any other ISAAC_MCP_<X> became TWINSMITH_<X>; any
# other old name has no replacement. The bridge keeps an identical table (twinsmith_bridge/knobs.py; a test compares them).
_TOOLSETS = "TWINSMITH_TOOLSETS (it takes toolset names: docs/TOOLSETS.md)"
_ASSET_ROOT = "TWINSMITH_ASSET_ROOT (the one folder that holds <pack id>/<version>/pack.yaml)"
_NO_DOWNLOADS = "nothing: remove it (nothing is downloaded; put the files in a pack under TWINSMITH_ASSET_ROOT)"
OLD_REPLACEMENTS = {
    "ISAAC_MCP_PORT": "TWINSMITH_PORT", "UR5E_DT_PORT": "TWINSMITH_PORT", "ISAAC_SIM_PORT": "TWINSMITH_PORT",
    "ISAAC_MCP_PYTHON": "TWINSMITH_SIM_PYTHON", "UR5E_DT_PYTHON": "TWINSMITH_SIM_PYTHON",
    "ISAAC_SIM_PYTHON": "TWINSMITH_SIM_PYTHON",
    "UR5E_DT_HEADLESS": "TWINSMITH_HEADLESS",
    "ISAACLAB_JOBS_DIR": "TWINSMITH_JOBS_DIR",
    "ISAAC_MCP_LOCAL_TOOLS": _TOOLSETS, "ISAAC_MCP_NAMESPACE": _TOOLSETS, "ISAAC_MCP_BUILTINS": _TOOLSETS,
    "ISAAC_MCP_SCENE_TOOLS": _TOOLSETS, "ISAAC_MCP_GROUP_TOOLS": _TOOLSETS,
    "ISAAC_MCP_ASSET_ROOT": "TWINSMITH_ASSET_ROOT", "ISAAC_MCP_ASSET_PATH": _ASSET_ROOT, "UR5E_DT_ASSET_CACHE": _ASSET_ROOT,
    "ISAAC_MCP_ASSET_PACK": 'nothing: remove it, and name the pack as "pack": "<id>/<version>" in twinsmith.json',
    "ISAAC_MCP_ASSET_STRICT": _NO_DOWNLOADS, "UR5E_DT_OFFLINE": _NO_DOWNLOADS,
    "ISAAC_MCP_ASSET_REMOTE_ROOT": _NO_DOWNLOADS, "UR5E_DT_ASSET_ROOT": _NO_DOWNLOADS,
}


def old_settings(env: Mapping[str, str]) -> list[str]:
    """The isaac-mcp variables that are set in ``env``, sorted."""
    return sorted(name for name, value in env.items() if str(value).strip()
                  and (name.startswith(OLD_PREFIXES) or name in OLD_VARIABLES))


def replacement(name: str) -> str:
    """What to set instead of the old variable ``name``."""
    if name in OLD_REPLACEMENTS:
        return OLD_REPLACEMENTS[name]
    return "TWINSMITH_" + name[len("ISAAC_MCP_"):] if name.startswith("ISAAC_MCP_") else "nothing: remove it"


def refuse_old_settings(env: Mapping[str, str]) -> None:
    """Raise ValueError naming every isaac-mcp variable set in ``env`` and what replaces it."""
    old = old_settings(env)
    if old:
        raise ValueError(f"isaac-mcp settings are set and no longer read: "
                         f"{'; '.join(f'{name} -> {replacement(name)}' for name in old)}. Set the new name or remove "
                         "the old one, in your shell profile and every client entry (docs/MIGRATING.md).")


def _refuse_old_file(old: Path, new: Path) -> None:
    if old.is_file() and not new.is_file():
        raise ValueError(f"{old} is the isaac-mcp name of {new} and is no longer read. With nothing running, "
                         f"move it: mv {old} {new} (see docs/MIGRATING.md).")


def select(value) -> frozenset[str]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError("toolsets must be a nonempty list of names or a comma-separated string")
    enabled = {"core"}
    for raw in value:
        item = raw.strip()
        if item in PROFILES:
            enabled = set(PROFILES[item])
            continue
        name = item.lstrip("+-")
        if name not in TOOLSETS or item not in {name, "+" + name, "-" + name}:
            raise ValueError(f"Unknown toolset {item!r}. Valid names: {', '.join((*PROFILES, *TOOLSETS))}")
        if item == "-core":
            raise ValueError("core cannot be turned off")
        if item.startswith("-"):
            enabled.discard(name)
        else:
            enabled.add(name)
    return frozenset(enabled | {"core"})


# The one place the default physics engine is written down.
DEFAULT_PHYSICS_BACKEND = "newton"
PHYSICS_BACKENDS = ("physx", "newton")


def _flag(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false, not {value!r}")


@dataclass(frozen=True)
class Config:
    toolsets: frozenset[str] = DEFAULT
    physics_backend: str = DEFAULT_PHYSICS_BACKEND
    physics_backend_source: str = "built-in"       # built-in, user file, project file or environment
    runtime_enable: bool = True                    # may an agent call toolsets(action="enable")
    cursor_badge: bool = False                     # draw a click-through badge where computer input lands


def _minimal_sim_table(text: str, path: Path) -> dict:
    """Only ``[sim] physics_backend = "..."``, for a Python with no TOML reader (before 3.11, without
    tomli). Anything it cannot read is an error, never a quiet default."""
    section, found = "", {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("["):
            section = line.strip("[] \t")
        elif section == "sim":
            match = re.fullmatch(r"physics_backend\s*=\s*[\"']([^\"']*)[\"']", line)
            if match:
                found["physics_backend"] = match.group(1)
    if "physics_backend" in text and not found:
        raise ValueError(f"Cannot read physics_backend from {path} without a TOML reader: use Python 3.11 or "
                         "install tomli, or set TWINSMITH_PHYSICS_BACKEND.")
    return {"sim": found} if found else {}


def refuse_old_files(cwd: Path | None = None, home: Path | None = None) -> None:
    """Raise ValueError for an isaac-mcp config file that would be read where its new file is missing."""
    current = (Path.cwd() if cwd is None else Path(cwd)).resolve()
    base = Path.home() if home is None else Path(home)
    _refuse_old_file(base / OLD_USER_FILE, base / ".config/twinsmith/config.toml")
    for directory in (current, *current.parents):
        _refuse_old_file(directory / OLD_PROJECT_FILE, directory / "twinsmith.toml")
        if (directory / "twinsmith.toml").is_file():
            break


def _config_files(cwd: Path | None = None, home: Path | None = None, minimal: bool = False):
    """The user and project configuration files that exist, lowest priority first, as (label, data).
    With ``minimal`` a Python that has no TOML reader still yields the physics setting."""
    current = (Path.cwd() if cwd is None else Path(cwd)).resolve()
    base = Path.home() if home is None else Path(home)
    user = base / ".config/twinsmith/config.toml"
    refuse_old_files(current, base)
    project = None
    for directory in (current, *current.parents):
        if (directory / "twinsmith.toml").is_file():
            project = directory / "twinsmith.toml"
            break
    for label, path in (("user file", user), ("project file", project)):
        if path is not None and path.is_file():
            if tomllib is None:
                if not minimal:
                    raise ValueError(f"Reading {path} needs Python 3.11 or the tomli package.")
                yield label, _minimal_sim_table(path.read_text(), path)
                continue
            with path.open("rb") as stream:
                yield label, tomllib.load(stream)


def resolve_physics_backend(env: Mapping[str, str] | None = None, cwd: Path | None = None,
                            home: Path | None = None) -> tuple[str, str]:
    """The configured physics backend and where it came from: built-in, user file, project file or
    environment. Reads only the physics setting, so a launcher can ask for it with nothing else
    resolved. This is the one place the default is written down; it changes no simulator's engine."""
    env = os.environ if env is None else env
    backend, source = DEFAULT_PHYSICS_BACKEND, "built-in"
    if env.get("TWINSMITH_PHYSICS_BACKEND"):          # the highest priority: the files need not be read
        backend, source = env["TWINSMITH_PHYSICS_BACKEND"], "environment"
    else:
        for label, data in _config_files(cwd, home, minimal=True):
            if "physics_backend" in data.get("sim", {}):
                backend, source = data["sim"]["physics_backend"], label
    if backend not in PHYSICS_BACKENDS:
        raise ValueError(f"physics_backend must be {' or '.join(PHYSICS_BACKENDS)}, not {backend!r} (from the {source})")
    return backend, source


def resolve(env: Mapping[str, str] | None = None, cli_toolsets: str | None = None,
            cwd: Path | None = None, home: Path | None = None) -> Config:
    env = os.environ if env is None else env
    refuse_old_settings(env)
    enabled, runtime, badge = DEFAULT, True, False
    for _, data in _config_files(cwd, home):
        if "toolsets" in data.get("tools", {}):
            enabled = select(data["tools"]["toolsets"])
        if "runtime_enable" in data.get("tools", {}):
            runtime = _flag(data["tools"]["runtime_enable"], "tools.runtime_enable")
        if "cursor_badge" in data.get("screen", {}):
            badge = _flag(data["screen"]["cursor_badge"], "screen.cursor_badge")
    if "TWINSMITH_TOOLSETS" in env:
        enabled = select(env["TWINSMITH_TOOLSETS"])
    if cli_toolsets is not None:
        enabled = select(cli_toolsets)
    if "TWINSMITH_RUNTIME_ENABLE" in env:
        runtime = _flag(env["TWINSMITH_RUNTIME_ENABLE"], "TWINSMITH_RUNTIME_ENABLE")
    if "TWINSMITH_CURSOR_BADGE" in env:
        badge = _flag(env["TWINSMITH_CURSOR_BADGE"], "TWINSMITH_CURSOR_BADGE")
    backend, source = resolve_physics_backend(env, cwd, home)
    return Config(enabled, backend, source, runtime, cursor_badge=badge)


def physics_backend(**kwargs) -> str:
    """Return the configured backend; this does not change any simulator's engine."""
    return resolve(**kwargs).physics_backend


def main(argv=None) -> int:
    """``python3 -m twinsmith.config --print physics_backend``: what a launcher needs, with only the
    standard library. ``physics_backend_source`` says where the value came from. ``--check`` prints
    nothing and only refuses an old setting or config file (run.sh runs it before it starts anything)."""
    import argparse
    parser = argparse.ArgumentParser(prog="python3 -m twinsmith.config", description=main.__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--print", dest="field", choices=["physics_backend", "physics_backend_source"])
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        refuse_old_settings(os.environ)
        refuse_old_files()
        if args.check:
            return 0
        backend, source = resolve_physics_backend()
    except ValueError as error:
        print(f"twinsmith configuration: {error}", file=__import__("sys").stderr)
        return 2
    print(backend if args.field == "physics_backend" else source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

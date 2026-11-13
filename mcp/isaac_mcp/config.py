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

LEGACY = ("ISAAC_MCP_LOCAL_TOOLS", "ISAAC_MCP_NAMESPACE", "ISAAC_MCP_BUILTINS",
          "ISAAC_MCP_SCENE_TOOLS", "ISAAC_MCP_GROUP_TOOLS")


def replacement(variable: str, value: str) -> str:
    """Give an explicit migration command, never interpret legacy settings at runtime."""
    if variable == "ISAAC_MCP_LOCAL_TOOLS":
        mapping = {"all": "all", "none": "minimal", "readonly": "minimal,+lab,+screen",
                   "gateway": "+core", "screen_input": "+input", "sim_launch": "+launch"}
        parts = []
        for item in (value or "all").split(","):
            item = item.strip().lower()
            part = mapping.get(item, "+lab" if item.startswith("isaaclab_") else "+screen")
            if part not in parts:
                parts.append(part)
        if parts[0].startswith("+"):
            parts.insert(0, "minimal")
        selection = ",".join(parts)
    elif variable == "ISAAC_MCP_NAMESPACE":
        mapping = {"sim": "+scene,+physics,+sensors,+ros2,+kit_debug", "assets": "+assets",
                   "ur5e_dt": "+workcell"}
        selection = "minimal,+code,+screen" + "".join("," + mapping.get(part.strip(), "+extensions")
                                                       for part in value.split(",") if part.strip())
    elif variable == "ISAAC_MCP_BUILTINS":
        selection = "default,-code" if value.lower() in {"0", "false", "no"} else "default,+code"
    elif variable == "ISAAC_MCP_SCENE_TOOLS":
        selection = "default,-scene,-physics,-sensors,-assets,-ros2,-kit_debug" if value.lower() in {"0", "false", "no"} else "default"
    else:
        selection = "default"
    return "ISAAC_MCP_TOOLSETS=" + selection


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
DEFAULT_PHYSICS_BACKEND = "physx"
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
                         "install tomli, or set ISAAC_MCP_PHYSICS_BACKEND.")
    return {"sim": found} if found else {}


def _config_files(cwd: Path | None = None, home: Path | None = None, minimal: bool = False):
    """The user and project configuration files that exist, lowest priority first, as (label, data).
    With ``minimal`` a Python that has no TOML reader still yields the physics setting."""
    current = (Path.cwd() if cwd is None else Path(cwd)).resolve()
    user = (Path.home() if home is None else Path(home)) / ".config/isaac-mcp/config.toml"
    project = next((directory / "isaac-mcp.toml" for directory in (current, *current.parents)
                    if (directory / "isaac-mcp.toml").is_file()), None)
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
    if env.get("ISAAC_MCP_PHYSICS_BACKEND"):          # the highest priority: the files need not be read
        backend, source = env["ISAAC_MCP_PHYSICS_BACKEND"], "environment"
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
    retired = [name for name in LEGACY if name in env]
    if retired:
        raise ValueError("Retired settings: " + "; ".join(
            f"{name} is no longer supported; remove it and use {replacement(name, env[name])}"
            for name in retired))
    enabled, runtime = DEFAULT, True
    for _, data in _config_files(cwd, home):
        if "toolsets" in data.get("tools", {}):
            enabled = select(data["tools"]["toolsets"])
        if "runtime_enable" in data.get("tools", {}):
            runtime = _flag(data["tools"]["runtime_enable"], "tools.runtime_enable")
    if "ISAAC_MCP_TOOLSETS" in env:
        enabled = select(env["ISAAC_MCP_TOOLSETS"])
    if cli_toolsets is not None:
        enabled = select(cli_toolsets)
    if "ISAAC_MCP_RUNTIME_ENABLE" in env:
        runtime = _flag(env["ISAAC_MCP_RUNTIME_ENABLE"], "ISAAC_MCP_RUNTIME_ENABLE")
    backend, source = resolve_physics_backend(env, cwd, home)
    return Config(enabled, backend, source, runtime)


def physics_backend(**kwargs) -> str:
    """Return the configured backend; this does not change any simulator's engine."""
    return resolve(**kwargs).physics_backend


def main(argv=None) -> int:
    """``python3 -m isaac_mcp.config --print physics_backend``: what a launcher needs, with only the
    standard library. ``physics_backend_source`` says where the value came from."""
    import argparse
    parser = argparse.ArgumentParser(prog="python3 -m isaac_mcp.config", description=main.__doc__)
    parser.add_argument("--print", dest="field", required=True, choices=["physics_backend", "physics_backend_source"])
    args = parser.parse_args(argv)
    try:
        backend, source = resolve_physics_backend()
    except ValueError as error:
        print(f"isaac-mcp configuration: {error}", file=__import__("sys").stderr)
        return 2
    print(backend if args.field == "physics_backend" else source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

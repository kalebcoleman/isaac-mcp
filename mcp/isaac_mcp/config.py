"""Resolve startup configuration without importing the MCP SDK or simulator."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

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


def resolve(env: Mapping[str, str] | None = None, cli_toolsets: str | None = None,
            cwd: Path | None = None, home: Path | None = None) -> Config:
    env = os.environ if env is None else env
    retired = [name for name in LEGACY if name in env]
    if retired:
        raise ValueError("Retired settings: " + "; ".join(
            f"{name} is no longer supported; remove it and use {replacement(name, env[name])}"
            for name in retired))
    enabled, backend, source, runtime = DEFAULT, DEFAULT_PHYSICS_BACKEND, "built-in", True
    current = (Path.cwd() if cwd is None else Path(cwd)).resolve()
    user = (Path.home() if home is None else Path(home)) / ".config/isaac-mcp/config.toml"
    project = next((directory / "isaac-mcp.toml" for directory in (current, *current.parents)
                    if (directory / "isaac-mcp.toml").is_file()), None)
    for label, path in (("user file", user), ("project file", project)):
        if path is not None and path.is_file():
            with path.open("rb") as stream:
                data = tomllib.load(stream)
            if "toolsets" in data.get("tools", {}):
                enabled = select(data["tools"]["toolsets"])
            if "runtime_enable" in data.get("tools", {}):
                runtime = _flag(data["tools"]["runtime_enable"], "tools.runtime_enable")
            if "physics_backend" in data.get("sim", {}):
                backend, source = data["sim"]["physics_backend"], label
    if "ISAAC_MCP_TOOLSETS" in env:
        enabled = select(env["ISAAC_MCP_TOOLSETS"])
    if cli_toolsets is not None:
        enabled = select(cli_toolsets)
    if "ISAAC_MCP_RUNTIME_ENABLE" in env:
        runtime = _flag(env["ISAAC_MCP_RUNTIME_ENABLE"], "ISAAC_MCP_RUNTIME_ENABLE")
    if env.get("ISAAC_MCP_PHYSICS_BACKEND"):
        backend, source = env["ISAAC_MCP_PHYSICS_BACKEND"], "environment"
    if backend not in PHYSICS_BACKENDS:
        raise ValueError("physics_backend must be physx or newton")
    return Config(enabled, backend, source, runtime)


def physics_backend(**kwargs) -> str:
    """Return the configured backend; this does not change any simulator's engine."""
    return resolve(**kwargs).physics_backend

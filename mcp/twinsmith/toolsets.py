"""Tool ownership and profiles. Pure data and standard-library helpers only."""

from dataclasses import dataclass

from .tool_groups import GROUPS


@dataclass(frozen=True)
class Toolset:
    summary: str
    default: bool = False
    explicit_only: bool = False


TOOLSETS = {
    "core": Toolset("Connection, discovery, help and job tracking", True),
    "scene": Toolset("Create and edit scenes and primitives", True),
    "physics": Toolset("Physics, joints, arms and simulation", True),
    "sensors": Toolset("Cameras, LiDAR and viewport framing", True),
    "assets": Toolset("Import and manage robot and scene assets", True),
    "code": Toolset("Execute code and scripts, transfer files", True),
    "screen": Toolset("Capture, record and arrange desktop windows", True),
    "lab": Toolset("Isaac Lab training and evaluation"),
    "ros2": Toolset("ROS 2 transforms, graphs and occupancy maps"),
    "kit_debug": Toolset("Inspect Kit logs and operate its UI"),
    "workcell": Toolset("UR5e workcell tools"),
    "robosim": Toolset("RoboSimStudio real-to-sim runs in its own private environment"),
    "input": Toolset("Desktop mouse and keyboard input", explicit_only=True),
    "launch": Toolset("Launch and stop simulator processes", explicit_only=True),
    "extensions": Toolset("Tools from uncatalogued extension namespaces", True),
}

PLANNED = {
    "backend": "Select and inspect physics backends",
    "params": "Inspect and tune runtime parameters",
    "compare": "Compare simulation results",
    "planning": "Plan robot actions",
    "real2sim": "Synchronize real and simulated systems",
    "eval": "Evaluate robot task performance",
}

_ROOTS = {
    "core": "status help refresh_tools use_bridge jobs toolsets",
    "scene": "sim__quick_start sim__stage sim__prim sim__timeline__control sim__light sim__material",
    "physics": "sim__physics sim__joints sim__arm sim__simulation",
    "sensors": "sim__camera sim__lidar sim__viewport__frame",
    "assets": "assets__import_path assets__library assets__pack",
    "code": "run_python files reload_tools sim__controller run_script",
    "screen": "screenshot screen_recording windows join_videos",
    "ros2": "sim__ros2__tf_setup sim__graph sim__map__occupancy",
    "kit_debug": "sim__ui sim__logs",
    "input": "computer",
    "launch": "sim__session",
    "robosim": "robosim",
}

MEMBERS = {
    name: frozenset(tool for root in roots.split()
                    for tool in (root, *GROUPS.get(root, ("", {}))[1].values()))
    for name, roots in _ROOTS.items()
}
_OWNERS = {tool: name for name, tools in MEMBERS.items() for tool in tools}
if sum(map(len, MEMBERS.values())) != len(_OWNERS):
    raise ValueError("A tool belongs to multiple toolsets")

DEFAULT = frozenset(name for name, spec in TOOLSETS.items() if spec.default)
PROFILES = {"default": DEFAULT,
            "all": frozenset(name for name, spec in TOOLSETS.items() if not spec.explicit_only),
            "minimal": frozenset({"core"})}


def owner(name: str, namespace: str | None = None,
          catalog_namespaces=frozenset({"sim", "assets", "workcell"})) -> str:
    """Resolve listed, single-purpose or canonical bridge names; reject unowned tools."""
    name = name.replace(".", "__")
    if name in _OWNERS:
        return _OWNERS[name]
    if name.startswith("workcell__"):
        return "workcell"
    if name.startswith("isaaclab__"):
        return "lab"
    if namespace and namespace not in catalog_namespaces:
        return "extensions"
    raise ValueError(f"Tool {name!r} has no toolset")


STARTUP = "Add +name to tools.toolsets in ~/.config/twinsmith/config.toml or set TWINSMITH_TOOLSETS, then restart the MCP server."
EXPLICIT = ("Explicit-only toolsets ({names}) cannot be enabled at run time: the user adds them at startup "
            "(+name in TWINSMITH_TOOLSETS, tools.toolsets in config.toml, or --toolsets).")
# Kept for callers that only need the startup route.
ENABLE = STARTUP

_UNGROUPED = {"lab": ("isaaclab__checkpoint_register",)}


def listed_count(name: str):
    """How many tools a client is shown for a toolset (grouped tools count once); None when it varies."""
    if name == "extensions":
        return None
    roots = set(_ROOTS.get(name, "").split())
    prefix = {"lab": "isaaclab__", "workcell": "workcell__"}.get(name)
    if prefix:
        roots |= {group for group in GROUPS if group.startswith(prefix)} | set(_UNGROUPED.get(name, ()))
    return len(roots)


def explicit_only() -> list:
    return [name for name, spec in TOOLSETS.items() if spec.explicit_only]


def enable_hint(names, runtime_enable: bool = True) -> str:
    """How to turn on the named toolsets, as one sentence the agent can act on."""
    names = sorted(names)
    explicit = [name for name in names if TOOLSETS[name].explicit_only]
    runtime = [name for name in names if name not in explicit]
    parts = []
    if runtime and runtime_enable:
        parts.append('Enable ' + ('it' if len(runtime) == 1 else 'them') + ' with toolsets(action="enable", names=['
                     + ", ".join(f'"{name}"' for name in runtime) + "]) (this server process only), or set it at startup: "
                     + STARTUP[0].lower() + STARTUP[1:])
    elif runtime:
        parts.append("Run-time enabling is switched off; " + STARTUP[0].lower() + STARTUP[1:])
    if explicit:
        parts.append(EXPLICIT.format(names=", ".join(explicit)))
    return " ".join(parts)


def describe(enabled, runtime_enable: bool = True) -> dict:
    """Stable status shared by the host status tool, the toolsets tool and the MCP resource."""
    off = [name for name in TOOLSETS if name not in enabled]
    can = [name for name in off if not TOOLSETS[name].explicit_only] if runtime_enable else []
    enable = ('Call toolsets(action="enable", names=[...]) to turn one on for this server process (the tool list '
              "then changes). " if runtime_enable else "Run-time enabling is off (runtime_enable). ")
    enable += EXPLICIT.format(names=", ".join(explicit_only())) + " " + STARTUP.replace("Add +name", "Otherwise add +name")
    return {"on": [name for name in TOOLSETS if name in enabled],
            "off": {name: TOOLSETS[name].summary for name in off},
            "tools": {name: listed_count(name) for name in TOOLSETS},
            "planned": list(PLANNED), "can_enable": can, "runtime_enable": runtime_enable, "enable": enable}

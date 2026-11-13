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
    "input": Toolset("Desktop mouse and keyboard input", explicit_only=True),
    "launch": Toolset("Launch and stop simulator processes", explicit_only=True),
    "extensions": Toolset("Tools from uncatalogued extension namespaces", True),
}

PLANNED = {
    "backend": "Select and inspect physics backends",
    "params": "Inspect and tune runtime parameters",
    "robosim": "Robot simulation workflows",
    "compare": "Compare simulation results",
    "planning": "Plan robot actions",
    "real2sim": "Synchronize real and simulated systems",
    "eval": "Evaluate robot task performance",
}

_ROOTS = {
    "core": "status isaac_help refresh_tools use_bridge jobs",
    "scene": "sim__quick_start sim__stage sim__prim sim__timeline__control sim__light sim__material",
    "physics": "sim__physics sim__joints sim__arm sim__simulation",
    "sensors": "sim__camera sim__lidar sim__viewport__frame",
    "assets": "assets__import_path assets__library assets__pack",
    "code": "run_python files reload_tools sim__controller run_script",
    "screen": "screenshot screen_recording windows join_videos",
    "ros2": "sim__ros2__tf_setup sim__graph sim__map__occupancy",
    "kit_debug": "sim__ui sim__logs",
    "input": "window_input",
    "launch": "sim__session",
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
          catalog_namespaces=frozenset({"sim", "assets", "ur5e_dt"})) -> str:
    """Resolve listed, single-purpose or canonical bridge names; reject unowned tools."""
    name = name.replace(".", "__")
    if name in _OWNERS:
        return _OWNERS[name]
    if name.startswith("ur5e_dt__"):
        return "workcell"
    if name.startswith("isaaclab__"):
        return "lab"
    if namespace and namespace not in catalog_namespaces:
        return "extensions"
    raise ValueError(f"Tool {name!r} has no toolset")


ENABLE = "Add +name to tools.toolsets in ~/.config/isaac-mcp/config.toml or set ISAAC_MCP_TOOLSETS, then restart the MCP server."


def describe(enabled) -> dict:
    """Stable status shared by the host status tool and the MCP resource."""
    return {"on": [name for name in TOOLSETS if name in enabled],
            "off": {name: spec.summary for name, spec in TOOLSETS.items() if name not in enabled},
            "planned": list(PLANNED), "enable": ENABLE}

"""The bridge's settings: each one an environment variable, a persistent Kit setting, and a default.

The environment wins, so one launch can differ from what is stored. ``scripts/install.py`` writes the
persistent form into a Kit app's user configuration. Stdlib only; ``carb`` is imported when read.
"""

import os
from typing import Any, Optional

SETTINGS_SUBTREE = "/persistent/exts/twinsmith.bridge"

# Variables of isaac-mcp (before 0.3.0) that nothing reads now. The bridge refuses to listen when one is set, as the
# host does (mcp/twinsmith/config.py, which has the same rule): an old ISAAC_MCP_READ_ONLY=1 must not leave a
# writable simulator without a word.
OLD_PREFIXES = ("ISAAC_MCP_", "UR5E_DT_")
OLD_VARIABLES = ("ISAAC_SIM_PORT", "ISAAC_SIM_PYTHON", "ISAACLAB_JOBS_DIR")
# What an old variable became: the host's table (mcp/twinsmith/config.py), copied because Kit does not import the
# host package. A test checks that the two are identical.
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


def old_settings(environ=None) -> list:
    """The isaac-mcp variables that are set, sorted."""
    environ = os.environ if environ is None else environ
    return sorted(name for name, value in environ.items() if str(value).strip()
                  and (name.startswith(OLD_PREFIXES) or name in OLD_VARIABLES))


def replacement(name: str) -> str:
    """What to set instead of the old variable ``name``."""
    if name in OLD_REPLACEMENTS:
        return OLD_REPLACEMENTS[name]
    return "TWINSMITH_" + name[len("ISAAC_MCP_"):] if name.startswith("ISAAC_MCP_") else "nothing: remove it"


class Knob:
    def __init__(self, env: str, leaf: str, default: Any = None):
        self.env, self.leaf, self.default = env, leaf, default

    @property
    def path(self) -> str:
        return f"{SETTINGS_SUBTREE}/{self.leaf}"

    def read_env(self) -> Optional[str]:
        value = os.environ.get(self.env)
        return value if value not in (None, "") else None

    def read_settings(self) -> Any:
        try:
            import carb.settings

            return carb.settings.get_settings().get(self.path)
        except Exception:  # noqa: BLE001 - outside Kit there are no stored settings
            return None

    def read(self) -> Any:
        value = self.read_env()
        if value is None:
            value = self.read_settings()
        return self.default if value in (None, "") else value

    def flag(self) -> bool:
        return str(self.read()).strip().lower() in ("1", "true", "yes", "on")


PORT = Knob("TWINSMITH_PORT", "autostartPort")
BIND = Knob("TWINSMITH_BIND", "autostartHost")
TOOL_MODULES = Knob("TWINSMITH_TOOL_MODULES", "toolModules")
TOOL_PATHS = Knob("TWINSMITH_TOOL_PATHS", "toolPaths")
READ_ONLY = Knob("TWINSMITH_READ_ONLY", "readOnly", "0")
AUTH = Knob("TWINSMITH_AUTH", "auth", "1")
TOKEN = Knob("TWINSMITH_TOKEN", "token")


def listed(value: Any) -> list:
    """A comma- or colon-separated setting, or a stored list, as a list of names."""
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or "").replace(os.pathsep, ",").split(",") if part.strip()]

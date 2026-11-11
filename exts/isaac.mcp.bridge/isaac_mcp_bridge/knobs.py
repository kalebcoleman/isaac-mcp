"""The bridge's settings: each one an environment variable, a persistent Kit setting, and a default.

The environment wins, so one launch can differ from what is stored. ``scripts/install.py`` writes the
persistent form into a Kit app's user configuration. Stdlib only; ``carb`` is imported when read.
"""

import os
from typing import Any, Optional

SETTINGS_SUBTREE = "/persistent/exts/isaac.mcp.bridge"


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


PORT = Knob("ISAAC_MCP_PORT", "autostartPort")
BIND = Knob("ISAAC_MCP_BIND", "autostartHost")
TOOL_MODULES = Knob("ISAAC_MCP_TOOL_MODULES", "toolModules")
TOOL_PATHS = Knob("ISAAC_MCP_TOOL_PATHS", "toolPaths")
READ_ONLY = Knob("ISAAC_MCP_READ_ONLY", "readOnly", "0")
AUTH = Knob("ISAAC_MCP_AUTH", "auth", "1")
TOKEN = Knob("ISAAC_MCP_TOKEN", "token")


def listed(value: Any) -> list:
    """A comma- or colon-separated setting, or a stored list, as a list of names."""
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or "").replace(os.pathsep, ",").split(",") if part.strip()]

"""The bridge's settings: each one an environment variable, a persistent Kit setting, and a default.

The environment wins, so one launch can differ from what is stored. ``scripts/install.py`` writes the
persistent form into a Kit app's user configuration. Stdlib only; ``carb`` is imported when read.
"""

import os
import sys
from typing import Any, Optional

SETTINGS_SUBTREE = "/persistent/exts/twinsmith.bridge"

_LEGACY = None


def legacy_module():
    """``mcp/twinsmith/legacy.py``, loaded by its path (it is standard library only): the one file that holds the
    old names and the table of old variable names. None when this extension is not inside a checkout."""
    global _LEGACY
    if _LEGACY is None:
        import importlib.util

        path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "mcp",
                                             "twinsmith", "legacy.py"))
        spec = importlib.util.spec_from_file_location("twinsmith_legacy", path) if os.path.isfile(path) else None
        if spec is None:
            _LEGACY = False
        else:
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            _LEGACY = module
    return _LEGACY or None


def apply_legacy_aliases() -> list:
    """Make the old variable names work for one release, as the host does (see legacy.py)."""
    module = legacy_module()
    return module.apply() if module else []


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

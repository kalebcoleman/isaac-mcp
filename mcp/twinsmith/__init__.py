"""twinsmith: an MCP server for Isaac Sim and Isaac Lab.

``twinsmith.server`` is the server (it needs the MCP SDK); ``twinsmith.client`` talks to the bridge
inside a running Isaac Sim and needs only the standard library. Both are imported on first use, so
``import twinsmith`` and ``python -m twinsmith.client`` work without the SDK.
"""

import importlib

from . import legacy

legacy.apply()      # old environment names keep working for one release (see legacy.py)

_LAZY = {"client": "client", "BridgeClient": "client", "BridgeError": "client", "call": "client",
         "server": "server", "mcp": "server", "main": "server"}
__all__ = sorted(_LAZY)


def __getattr__(name):
    if name not in _LAZY:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module("." + _LAZY[name], __name__)
    return module if name == _LAZY[name] else getattr(module, name)

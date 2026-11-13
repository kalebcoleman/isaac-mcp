"""RoboSimStudio subprocess bridge; importing this package does not import RSS."""

from .bridge import PIN, RoboSimBridge


def register_robosim_tools(server, **kwargs):
    from .tools import register_robosim_tools as register

    return register(server, **kwargs)


__all__ = ["PIN", "RoboSimBridge", "register_robosim_tools"]

"""Register tools when Kit reloads this extension.

Kit's extension manager watches extension folders and reloads an extension when a file in it changes:
it calls on_shutdown, re-imports the modules, then calls on_startup in the same process. If a tool call
is still running then, the bridge keeps the old owner reserved until the call returns, and on_startup
cannot wait for that, because the call needs the same event loop to finish. So registration moves to a
background task. Without this, on_startup raised "owner 'generic-sim' is already registered" and every
sim tool disappeared from that Kit until the next file change.

No Kit imports, so this runs in the host test suite.
"""

import asyncio

OWNERS = ("generic-sim", "generic-assets")
POLL_S = 0.25
# Only when to say so loudly. The task keeps waiting: the tools return when the call does.
WARN_AFTER_S = 30.0


def draining(bridge, owners=OWNERS):
    """Which of ``owners`` a previous instance's in-flight call still holds."""
    current = bridge.get_owners()
    return [owner for owner in owners if current.get(owner, {}).get("draining")]


async def register_when_free(bridge, register, report, owners=OWNERS, poll_s=POLL_S, warn_after_s=WARN_AFTER_S):
    """Wait for the old owners to drain, then call ``register``. ``report(message, error)`` logs."""
    waited, warned = 0.0, False
    while draining(bridge, owners):
        await asyncio.sleep(poll_s)
        waited += poll_s
        if waited >= warn_after_s and not warned:
            warned = True
            report(f"sim tools are still unavailable after {waited:.0f} s: a call from before the reload has "
                   f"not finished ({', '.join(draining(bridge, owners))}). They return when it does.", True)
    try:
        register()
    except Exception as exc:  # noqa: BLE001 - a background task has no caller to raise to
        report(f"sim tools could not be registered after the reload: {exc}. "
               "Restart Kit, or edit a file in exts/sim-tools to reload again.", True)
        return False
    report(f"sim tools registered after waiting {waited:.1f} s for a call from before the reload.", False)
    return True

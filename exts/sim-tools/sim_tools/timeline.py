"""Observe a pending native Replicator Stop without driving its orchestrator."""

import asyncio
import sys
import time

from isaac_mcp.project_assets import refuse


def native_status():
    # Do not import/enable Replicator for a scene that has never used it. Its
    # public completion helpers mutate the orchestrator; get_status is passive.
    rep = sys.modules.get("omni.replicator.core")
    if rep is None:
        return None
    getter = getattr(getattr(rep, "orchestrator", None), "get_status", None)
    if not callable(getter):
        refuse("TIMELINE_API_UNAVAILABLE", "Loaded Replicator has no get_status API.")
    try:
        status = getter().name
    except Exception as exc:
        refuse("TIMELINE_API_UNAVAILABLE", "Cannot observe Replicator status: " + str(exc))
    if status not in {
        "STOPPED", "STARTING", "STARTED", "STOPPING", "PAUSED", "STEPPING", "STEPPED",
        "LOADING", "INITIALIZING",
    }:
        refuse("TIMELINE_API_UNAVAILABLE", "Unknown Replicator status: " + str(status))
    return status


async def settle_stop(next_update, validate, *, timeout=5.0):
    """Bounded ordinary updates, not an all-native-work completion fence.

    In Replicator 1.13.4 a timeline Stop enters STOPPING; renderer/writer drain
    finishes on later updates and can issue another native Stop. A new Play
    before that transition finishes is lost. Never reissue Play, call native
    stop/wait_until_complete, or change the user's capture settings here.
    """
    validate()
    first = native_status()
    frames = 0
    if first != "STOPPING":
        return dict(observed=first, updates=frames)
    deadline = time.monotonic() + timeout
    observed = first
    while True:
        validate()
        remaining = deadline - time.monotonic()
        if remaining <= 0 or frames >= 1200:
            refuse(
                "TIMELINE_BUSY",
                "Native render/writer Stop has not settled; inspect state and retry explicitly.",
                replicator_status=observed,
            )
        try:
            await asyncio.wait_for(next_update(), remaining)
        except asyncio.TimeoutError:
            refuse("TIMELINE_BUSY", "No application update completed while awaiting native Stop.")
        frames += 1
        validate()
        current = native_status()
        if current is None:
            refuse("TIMELINE_INTERRUPTED", "Replicator unloaded during Stop observation.")
        if current not in {"STOPPING", "STOPPED"}:
            refuse(
                "TIMELINE_INTERRUPTED",
                "Replicator changed to a different operation while awaiting Stop.",
                replicator_status=current,
            )
        # Reobserve STOPPED across an ordinary update. This protects this
        # transition, not arbitrary future callbacks or external timeline edits.
        if current == observed == "STOPPED":
            return dict(observed=current, updates=frames)
        observed = current

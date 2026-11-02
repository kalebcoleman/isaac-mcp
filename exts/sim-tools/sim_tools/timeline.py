"""Complete explicit Stop requests; observe pending Stop before Play."""

import asyncio
import sys
import time

from isaac_mcp.project_assets import refuse


class _GuardedAwait:
    """Validate before each continuation of the awaited native coroutine.

    This forwards normal asyncio suspension, exceptions and cancellation; it
    never pumps Kit. In particular, a stage replacement during a native await
    must not resume that coroutine's mutations on the replacement stage.
    Native callbacks/detached work and synchronous native calls are outside
    this guard, just as they are outside asyncio's cooperative timeout.
    """

    def __init__(self, coroutine, validate):
        self.iterator = coroutine.__await__()
        self.validate = validate

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            self.validate()
        except BaseException:
            self.iterator.close()
            raise
        return self.iterator.send(value)

    def throw(self, *error):
        # Deliver cancellation, including cleanup; validate other failed awaits
        # before native exception handling resumes on a potentially new stage.
        exception = error[0]
        if not (isinstance(exception, asyncio.CancelledError) or exception is asyncio.CancelledError):
            try:
                self.validate()
            except BaseException:
                self.iterator.close()
                raise
        return self.iterator.throw(*error)

    def close(self):
        self.iterator.close()


async def complete_requested_stop(next_update, validate, *, timeout=5.0):
    """Await native completion only for an explicitly requested timeline Stop.

    Some Replicator builds remain STOPPING after timeline reset because their
    passive writing predicate compares the reset dispatcher time with the old
    capture schedule. The documented completion API drains/finalizes writers,
    restores capture settings and clears that schedule. It is a mutation, not
    an observation, and must never be used to silently repair a Play request.
    """
    validate()
    first = native_status()
    if first != "STOPPING":
        return dict(observed=first, updates=0)
    rep = sys.modules.get("omni.replicator.core")
    orchestrator = rep.orchestrator
    complete = getattr(orchestrator, "wait_until_complete_async", None)
    if not callable(complete):
        refuse("TIMELINE_API_UNAVAILABLE", "Replicator has no asynchronous Stop completion API.")

    def validate_completion():
        validate()
        if sys.modules.get("omni.replicator.core") is not rep or rep.orchestrator is not orchestrator:
            refuse("TIMELINE_INTERRUPTED", "Replicator changed during Stop completion.")
        current = native_status()
        if current not in {"STOPPING", "STOPPED"}:
            refuse("TIMELINE_INTERRUPTED", "Replicator changed operation during Stop completion.", replicator_status=current)

    async def finish():
        await _GuardedAwait(complete(), validate_completion)
        validate_completion()
        # Allow the installed native delayed settings restoration (five
        # ordinary updates) to run before returning. This is not a fence for
        # arbitrary callbacks or independently scheduled external work.
        for _ in range(6):
            await next_update()
            validate_completion()
        if native_status() != "STOPPED":
            refuse("TIMELINE_BUSY", "Native completion returned without reaching STOPPED.", replicator_status=native_status())

    try:
        # Cancellation is awaited, never shielded or left running after return.
        # Synchronous native work cannot be preempted by an asyncio timeout.
        await asyncio.wait_for(finish(), timeout)
    except asyncio.TimeoutError:
        refuse("TIMELINE_BUSY", "Native Stop completion exceeded its cooperative timeout; inspect state before retrying.", replicator_status=native_status())
    return dict(observed="STOPPED", updates=6, completion="wait_until_complete_async")


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

"""Small adapters for Isaac Sim APIs that differ between releases.

A build that has the current API gets it untouched, so these add nothing on 6.x. Each difference is
detected from the API itself rather than a version number, which also covers an unlisted build.
"""

PHYSX_ONLY_MARKER = "_physx_sim_interface"


class _PhysxOnlyManager:
    """Isaac Sim 5.1's SimulationManager under the names and step signature newer builds use.

    5.1 has no engine selection, and its manager drives PhysX directly, which is checked before this is used.
    """

    def __init__(self, manager):
        self._manager = manager

    def __getattr__(self, name):
        return getattr(self._manager, name)

    @staticmethod
    def get_active_physics_engine():
        return "physx"

    def get_physics_simulation_view(self):
        return self._manager.get_physics_sim_view()

    def step(self, steps=1, render=False):
        # 5.1's step() is one simulate and fetch, with no count.
        for _ in range(steps):
            self._manager.step(render=render)


def adapt_simulation_manager(manager):
    """The manager to use: the real one when it has the current API, else a PhysX-only adapter."""
    if hasattr(manager, "get_active_physics_engine"):
        return manager
    if hasattr(manager, PHYSX_ONLY_MARKER) and hasattr(manager, "get_physics_sim_view"):
        return _PhysxOnlyManager(manager)
    return manager


def composition_errors(stage):
    """Composition errors for *stage*. USD 24.05 (Isaac Sim 5.1) has no Stage.GetCompositionErrors, so
    this collects the same Pcp errors from each prim's index instead."""
    native = getattr(stage, "GetCompositionErrors", None)
    if native is not None:
        return list(native())
    from pxr import Usd

    errors = []
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)):
        errors.extend(prim.GetPrimIndex().localErrors)
    return errors


class _LegacyDrawableSubscription:
    """The same reset() as a carb event-dispatcher observer."""

    def __init__(self, subscription):
        self._subscription = subscription

    def reset(self):
        subscription, self._subscription = self._subscription, None
        if subscription is not None:
            subscription.unsubscribe()


def has_drawable_events(ht, texture):
    """Can this build tell us when a texture finished rendering? Either API will do."""
    return (hasattr(ht, "GLOBAL_EVENT_DRAWABLE_CHANGED") and callable(getattr(texture, "get_event_key", None))) or (
        hasattr(ht, "EVENT_TYPE_DRAWABLE_CHANGED") and callable(getattr(texture, "get_event_stream", None)))


def observe_drawable_changed(ht, texture, callback, name):
    """Call *callback* with each drawable event; the result has reset(). Isaac Sim 5.1 has no global
    event, only the texture's own carb event stream, whose payload holds the same fields."""
    if hasattr(ht, "GLOBAL_EVENT_DRAWABLE_CHANGED"):
        from carb.eventdispatcher import get_eventdispatcher

        return get_eventdispatcher().observe_event(
            observer_name=name,
            event_name=ht.GLOBAL_EVENT_DRAWABLE_CHANGED,
            on_event=callback,
            filter=texture.get_event_key(),
        )
    return _LegacyDrawableSubscription(texture.get_event_stream().create_subscription_to_push_by_type(
        ht.EVENT_TYPE_DRAWABLE_CHANGED, lambda event: callback(event.payload), name=name))


def lidar_config_registry():
    """NVIDIA's LiDAR config table. Isaac Sim 5.1 keeps it in isaacsim.sensors.rtx, not experimental.rtx."""
    try:
        from isaacsim.sensors.experimental.rtx import SUPPORTED_LIDAR_CONFIGS
    except ImportError:
        from isaacsim.sensors.rtx.impl.supported_lidar_configs import SUPPORTED_LIDAR_CONFIGS
    return SUPPORTED_LIDAR_CONFIGS


# Builds where deactivating a stale LiDAR product (in a file with no saved runtime pipeline) brought its topic back.
# The one exception to detecting from the API: a working repair cannot be read from one, so this lists what was measured.
# Others get a report and the save-and-reopen rescue.
STALE_LIDAR_REPAIR_PROVEN = ("6.2.", "5.1.")


def generic_lidar_supported():
    """False where IsaacSensorCreateRtxLidar with no config can't honor an exact path: on 5.1 it builds its own,
    such as /World_Lidar for /World/Lidar, and leaves that prim behind. Unknown builds try it and are checked."""
    try:
        from isaacsim.core.version import get_version

        return not str(get_version()[0]).startswith("5.")
    except (ImportError, IndexError, OSError):
        return True


def stale_lidar_repair_proven():
    """True on a build where the session-layer repair of a stale LiDAR product was measured to work."""
    try:
        from isaacsim.core.version import get_version

        return str(get_version()[0]).startswith(STALE_LIDAR_REPAIR_PROVEN)
    except (ImportError, IndexError, OSError):
        return False


def metadata_keys():
    """OmniGraph's MetadataKeys: 6.2 moved it to omni.graph.tools.ogn and warns on the old name."""
    try:
        from omni.graph.tools.ogn import MetadataKeys

        return MetadataKeys
    except (ImportError, AttributeError):
        import omni.graph.core as og

        return og.MetadataKeys

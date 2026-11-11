"""Register the twin on the process bridge without owning its lifetime."""

OWNER_ID = "ur5e-dt"
NAMESPACE = "ur5e_dt"
DEFAULT_PORT = 8766
READ_ONLY = frozenset({"get_recording_status"})     # the twin tools a read-only bridge still runs


def attach(bridge, registry, instance, tool_type, *, requested_port=None, configured_port=None, host="localhost"):
    """Register every twin tool and start an unbound shared bridge if necessary.

    A conflicting explicit twin port is an error: silently registering on a
    different socket would leave clients calling the wrong process. A failed
    registration rolls back only the owner created by this invocation.
    """
    listening = bridge.listening_port
    if requested_port not in (None, 0) and listening is not None and requested_port != listening:
        raise RuntimeError(f"UR5e bridge port {requested_port} conflicts with the shared bridge on {listening}")
    if (listening is None and requested_port is not None and configured_port not in (None, 0)
            and requested_port != configured_port):
        raise RuntimeError(f"UR5e bridge port {requested_port} conflicts with configured shared bridge port {configured_port}")

    try:
        registrar = bridge.register_owner(OWNER_ID, NAMESPACE, display_name="UR5e Digital Twin")
    except ValueError as error:
        if bridge.get_owners().get(OWNER_ID, {}).get("draining"):
            raise RuntimeError("previous ur5e-dt instance is still draining; wait for its calls to finish") from error
        raise
    try:
        for name, schema in registry.items():
            registrar.add(tool_type(
                name=name,
                description=schema["description"],
                parameters=schema.get("parameters", {}),
                handler=getattr(instance, "_cmd_" + name),
                # The twin's handlers await frames and share one scene: each runs alone, and
                # calls that arrive meanwhile wait, whoever sent them.
                exclusive=True,
                read_only=name in READ_ONLY,
            ))
        if listening is None:
            port = requested_port if requested_port is not None else (configured_port or DEFAULT_PORT)
            bridge.start(port, host=host)
    except BaseException:
        bridge.unregister_owner(OWNER_ID)
        raise
    return bridge.listening_port

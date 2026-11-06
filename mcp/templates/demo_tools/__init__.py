"""demo_tools — the template tool package for the isaac.mcp.bridge bridge.

Copy this package into your project to onboard it. A tool project is a plain
Python package (NOT a Kit extension) that declares a namespace and exposes one
entrypoint; the bridge autoloads it and advertises its tools canonically as
``<namespace>.<tool>`` (here: ``demo.ping``, ``demo.add_cube``).

Register it once, persistently (no launch flags ever again):

    python3 scripts/install.py add-project --path <this dir's parent> --module demo_tools

or per-launch:  ISAAC_MCP_TOOL_PATHS=<parent> ISAAC_MCP_TOOL_MODULES=demo_tools

Handler contract: receive params as kwargs, return a JSON-serializable payload
(or raise; ``ToolError`` attaches structured diagnostics). Never build the
wire envelope. Import Kit modules (omni, pxr) INSIDE handlers so the package
stays importable off-Kit for tests.

Hot reload: ``reload_tools {"module": "demo_tools"}`` re-imports this package
in place. Live handles that must survive a reload (articulation views, physics
handles) belong in a submodule named exactly ``_state`` — that one module is
preserved across reloads. The name is exact, not a suffix: a tool module that
merely ends in ``_state`` (e.g. ``read_state``) reloads normally.
"""

MCP_NAMESPACE = "demo"
#: Written against the bridge's request-admission contract (README, "Request admission"). A
#: package that declares nothing has its tools refused: its frame-pumping tools would be unmarked.
DOORWAY_ADMISSION = 1
#: Each settable threshold's allowed range, so the rule lives in the tool and not in each caller.
THRESHOLD_RANGES = {"grip_force_n": (0.0, 40.0), "contact_distance_m": (0.0, 0.05)}


def register(registrar):
    """The autoload entrypoint. Receives an owner-bound registrar — no owner
    ids, no ports, no lifecycle; just declare tools."""

    @registrar.tool(
        "Liveness check: echoes its message back with the namespace.",
        {"message": {"type": "string", "description": "Text to echo back."}},
    )
    def ping(message: str = "pong"):
        return {"namespace": MCP_NAMESPACE, "echo": message}

    @registrar.tool(
        "Spawn a cube prim under /World/Objects in the live stage.",
        {
            "name": {"type": "string", "default": "demo_cube", "description": "Prim name (path-safe)."},
            "size": {"type": "number", "default": 1.0, "description": "Edge length in meters."},
        },
    )
    def add_cube(name: str = "demo_cube", size: float = 1.0):
        # Kit imports live inside the handler: the handler runs on Kit's main
        # thread; the module itself stays importable anywhere.
        import omni.usd
        from pxr import UsdGeom

        stage = omni.usd.get_context().get_stage()
        for p in ("/World", "/World/Objects"):
            if not stage.GetPrimAtPath(p).IsValid():
                UsdGeom.Xform.Define(stage, p)
        path = f"/World/Objects/{name}"
        cube = UsdGeom.Cube.Define(stage, path)
        cube.GetSizeAttr().Set(size)
        return {"path": path, "size": size}

    # A project's own state as tools, instead of asking agents to run_python against its internals:
    # typed arguments, one place for the rules, and they still work with ISAAC_MCP_BUILTINS=0, which
    # hides run_python. See "A project's own tools" in mcp/docs/tool-authoring.md.

    @registrar.tool(
        "Read this demo project's connection state and thresholds.",
        {},
    )
    def project_state():
        from . import _state

        return {"connection": _state.connection, "thresholds": dict(_state.thresholds)}

    @registrar.tool(
        "Set one of this demo project's thresholds. Refuses an unknown name or a value outside its range.",
        {
            "name": {"type": "string", "required": True, "enum": sorted(THRESHOLD_RANGES),
                     "description": "Threshold to set."},
            "value": {"type": "number", "required": True, "description": "New value, inside the threshold's range."},
        },
    )
    def set_threshold(name: str, value: float):
        from isaac_mcp_bridge import ToolError

        from . import _state

        if name not in THRESHOLD_RANGES:
            raise ToolError(f"unknown threshold {name!r}", details={"known": sorted(THRESHOLD_RANGES)})
        low, high = THRESHOLD_RANGES[name]
        if not low <= value <= high:
            raise ToolError(f"{name} must be between {low} and {high}, not {value}", details={"range": [low, high]})
        _state.thresholds[name] = value
        return {"name": name, "value": value}

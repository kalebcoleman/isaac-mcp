"""A tool package to copy: four tools in the namespace ``demo`` (``demo.ping``, ``demo.add_cube``, ...).

A tool package is an ordinary Python package, not a Kit extension. The bridge imports it, calls
``register(registrar)``, and lists what that registers under ``MCP_NAMESPACE``. Load it with

    TWINSMITH_TOOL_PATHS=<folder holding this package> TWINSMITH_TOOL_MODULES=demo_tools

at launch, or store that with ``scripts/install.py add-project``. After editing it, the
``reload_tools`` tool with ``module="demo_tools"`` loads the new code without restarting Kit.

A handler takes its arguments by name and returns something JSON can hold, or raises; a
``ToolError`` carries details to the caller. Import ``omni`` and ``pxr`` inside the handler, so
the package can also be imported, and tested, outside Kit. State that must outlive a reload (a
connection, a physics handle) goes in the submodule named ``_state``, the one a reload keeps.
"""

MCP_NAMESPACE = "demo"
LIMITS = {"grip_force_n": (0.0, 40.0), "contact_distance_m": (0.0, 0.05)}


def register(registrar):
    @registrar.tool("Echo a message, to check that the package is loaded.",
                    {"message": {"type": "string", "description": "Text to send back."}}, read_only=True)
    def ping(message: str = "pong"):
        return {"namespace": MCP_NAMESPACE, "echo": message}

    @registrar.tool("Add a cube under /World/Objects on the open stage.", {
        "name": {"type": "string", "default": "demo_cube", "description": "Name of the new prim."},
        "size": {"type": "number", "default": 1.0, "description": "Edge length in stage units."},
    })
    def add_cube(name: str = "demo_cube", size: float = 1.0):
        import omni.usd
        from pxr import UsdGeom

        stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(stage, "/World/Objects")
        UsdGeom.Cube.Define(stage, f"/World/Objects/{name}").GetSizeAttr().Set(size)
        return {"path": f"/World/Objects/{name}", "size": size}

    # A project's own state offered as tools: typed, checked in one place, and still there when
    # run_python is hidden.
    @registrar.tool("Read this project's connection state and limits.", {}, read_only=True)
    def project_state():
        from . import _state

        return {"connection": _state.connection, "thresholds": dict(_state.thresholds)}

    @registrar.tool("Set one of this project's thresholds, within its allowed range.", {
        "name": {"type": "string", "required": True, "enum": sorted(LIMITS), "description": "Which threshold."},
        "value": {"type": "number", "required": True, "description": "The new value."},
    })
    def set_threshold(name: str, value: float):
        from twinsmith_bridge import ToolError

        from . import _state

        if name not in LIMITS:
            raise ToolError(f"no threshold named {name!r}", details={"known": sorted(LIMITS)})
        low, high = LIMITS[name]
        if not low <= value <= high:
            raise ToolError(f"{name} must be from {low} to {high}, not {value}", details={"range": [low, high]})
        _state.thresholds[name] = value
        return {"name": name, "value": value}

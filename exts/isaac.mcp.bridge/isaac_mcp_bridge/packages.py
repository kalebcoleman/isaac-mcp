"""A project's own tool packages: import them at startup and reload them on request.

A package is named in ``ISAAC_MCP_TOOL_MODULES`` (folders to import from in ``ISAAC_MCP_TOOL_PATHS``).
It registers its tools in one of two ways:

    def register(registrar):                 # explicit
        @registrar.tool("Say hello", {"name": {"type": "string"}})
        def hello(name="world"):
            return {"greeting": f"hello {name}"}

or, with no ``register`` function, every public function it defines becomes a tool: the docstring is
the description and the signature gives the parameters. The namespace is ``NAMESPACE`` if the
package sets it, else the last part of its module name. Stdlib only.
"""

import importlib
import inspect
import os
import shutil
import sys
from typing import Any, Dict, List

_KINDS = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def add_tool_paths(paths: List[str]) -> None:
    for path in paths:
        path = os.path.abspath(os.path.expanduser(path))
        if path not in sys.path:
            sys.path.insert(0, path)


def forget_bytecode(folder: str) -> None:
    """Delete a folder's ``__pycache__`` so an edited file is compiled again."""
    shutil.rmtree(os.path.join(folder, "__pycache__"), ignore_errors=True)


def _parameters(function: Any) -> Dict[str, Any]:
    parameters = {}
    for name, parameter in inspect.signature(function).parameters.items():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        spec: Dict[str, Any] = {"type": _KINDS.get(parameter.annotation, "string")}
        if parameter.default is parameter.empty:
            spec["required"] = True
        else:
            spec["default"] = parameter.default
            if parameter.annotation is parameter.empty and type(parameter.default) in _KINDS:
                spec["type"] = _KINDS[type(parameter.default)]
        parameters[name] = spec
    return parameters


def register_functions(registrar: Any, module: Any) -> List[str]:
    """Register a module's own public functions (or exactly ``__all__``) as tools."""
    names = getattr(module, "__all__", None) or [
        name for name, value in vars(module).items()
        if inspect.isfunction(value) and value.__module__ == module.__name__ and not name.startswith("_")]
    added = []
    for name in names:
        function = getattr(module, name)
        description = (inspect.getdoc(function) or name).strip()
        registrar.tool(description, _parameters(function), name=name)(function)
        added.append(name)
    return added


def load_tool_module(bridge: Any, name: str) -> Dict[str, Any]:
    """Import one package and register its tools under an owner named after it."""
    module = importlib.import_module(name)
    namespace = getattr(module, "NAMESPACE", name.rsplit(".", 1)[-1])
    registrar = bridge.register_owner(name, namespace, display_name=name, metadata={"module": name})
    try:
        if callable(getattr(module, "register", None)):
            module.register(registrar)
        else:
            register_functions(registrar, module)
    except BaseException:
        bridge.unregister_owner(name)
        raise
    return {"module": name, "namespace": namespace, "tools": bridge.get_owners()[name]["tools"]}


def load_tool_modules(bridge: Any, names: List[str], paths: List[str] = ()) -> Dict[str, Any]:
    """Load every named package. One that fails raises: a simulator that came up without a tool
    pack it was told to load would answer "unknown tool" long after the cause scrolled away."""
    add_tool_paths(list(paths))
    return {name: load_tool_module(bridge, name) for name in names}


def reload_tool_module(bridge: Any, name: str) -> Dict[str, Any]:
    """Unregister a loaded package, import it afresh, and register it again. Refuses while one of
    its calls is running. A submodule named ``_state`` is kept, so live handles survive."""
    if name not in bridge.get_owners():
        raise ValueError(f"{name!r} is not a loaded tool package")
    bridge.unregister_owner(name, require_idle=True)
    for loaded in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
        if loaded == name + "._state":
            continue
        module = sys.modules.pop(loaded)
        if getattr(module, "__file__", None):
            forget_bytecode(os.path.dirname(module.__file__))
    importlib.invalidate_caches()
    return load_tool_module(bridge, name)

"""Built-in bridge verbs — the transport's own vocabulary, bare-named.

``run_python`` is the universal escape hatch (arbitrary Python on Kit's main
thread, with optional persistent sessions); ``list_tools`` is tool discovery;
``reload_tools`` hot-reloads one autoloaded tool package in place;
``list_bridges`` reports every bridge advertised on this box (so remote
callers, who can't read local portfiles, can find ephemeral-port siblings);
``put_file``/``get_file``/``stat_file`` are the file data plane: move files
to/from the bridge's box and verify what's there, so a remote driver can ship
tool code and assets — and skip unchanged ones — without a side-channel
rsync/scp.

Handlers here follow the same contract as any tool: return the payload dict,
or raise (``ToolError`` to attach diagnostics). The bridge owns the envelope.
"""

import ast
import contextvars
import functools
import inspect
import traceback
from typing import Any, Dict, List

from .protocol import ToolError

_RUN_PYTHON_DESCRIPTION = (
    "Execute Python code on the Kit main thread with full access to the "
    "Omniverse APIs (omni, carb, pxr preloaded; assign to `result` to return a "
    "value). Top-level `await` works (e.g. `await omni.kit.app.get_app().next_update_async()` "
    "to let frames run between steps). Set persistent=True with a session_id to keep variables "
    "alive between calls — useful for multi-step workflows where later calls need results from "
    "earlier ones. Opening a stage (including save-as) resets every session; the next call says so."
)
_RUN_PYTHON_PARAMETERS = {
    "code": {"type": "string", "required": True,
             "description": "Python code to execute on Kit's main thread"},
    "session_id": {
        "type": "string",
        "description": "Session identifier for persistent execution. Use the same ID "
                       "across calls to share state. Defaults to 'default'.",
    },
    "persistent": {
        "type": "boolean",
        "description": "If true, variables persist across calls with the same session_id.",
    },
}

_RELOAD_TOOLS_DESCRIPTION = (
    "Hot-reload one autoloaded tool package: unregister its owner, re-import "
    "the module (the submodule named _state is preserved so live handles "
    "survive), and re-register its tools. Follow with the MCP front-end's "
    "refresh so new schemas are rediscovered."
)
_RELOAD_TOOLS_PARAMETERS = {
    "module": {"type": "string", "required": True,
               "description": "Tool-package module name as registered "
                              "(see list_tools namespaces / owners)."},
}


def cmd_run_python(bridge, code: str, session_id: str = "default",
                   persistent: bool = False) -> Dict[str, Any]:
    """Execute Python on Kit's main thread; optionally persist the namespace.

    Since everything is in-process, persisted values (functions, prim
    references, numpy arrays) survive across calls with the same session_id.
    """
    import io
    import sys

    import carb
    import omni
    from pxr import Gf, Sdf, Usd, UsdGeom

    from .sessions import PRELOADED

    # Preloaded convenience symbols — the names come from sessions.PRELOADED
    # (the store's never-persist policy), so injection and policy can't drift.
    preload = {"omni": omni, "carb": carb,
               "Usd": Usd, "UsdGeom": UsdGeom, "Sdf": Sdf, "Gf": Gf}
    assert set(preload) == set(PRELOADED)
    exec_globals = {**preload, "__builtins__": __builtins__}
    if persistent:
        exec_globals.update(bridge.sessions.namespace(session_id))
    generation = bridge.sessions.generation

    try:
        # Top-level `await` is allowed: such code compiles to a coroutine that the bridge
        # awaits on Kit's event loop, so it can wait for app updates, renders or timers.
        compiled = compile(code, "<run_python>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    except SyntaxError as e:
        raise ToolError(f"SyntaxError: {e}", details={"traceback": traceback.format_exc()})

    def reset_note() -> str:
        # Consumed only when the call reports (finish/fail), so a cancelled call keeps it pending.
        reason = bridge.sessions.take_reset(session_id) if persistent else ""
        return (f"Session {session_id!r} was reset because {reason}: variables, imports and "
                "functions from earlier calls are gone (prim references would be stale). "
                "Re-run your setup code." if reason else "")

    def fail(e: BaseException, output: str):
        note = reset_note()
        details = {"output": output, "traceback": traceback.format_exc()}
        if note:
            details["session_reset"] = note
        elif persistent and isinstance(e, NameError):
            details["hint"] = ("Persistent sessions keep only the variables of successful calls; "
                               "names from a call that raised are not saved.")
        raise ToolError((note + " " if note and isinstance(e, NameError) else "") + str(e),
                        details=details)

    def finish(output: str):
        note = reset_note()
        payload = {"output": output, "result": exec_globals.get("result", None)}
        if persistent:
            payload["session_id"] = session_id
            if bridge.sessions.generation != generation:
                # A stage was opened while this call ran (e.g. it awaited open_stage_async): its
                # namespace still holds references to the old stage, so it is not saved.
                payload["session_vars"] = []
                payload["session_reset"] = (
                    f"A stage was opened during this call, so session {session_id!r} was not saved; "
                    "variables from this and earlier calls are gone. Re-run your setup code.")
                bridge.sessions.take_reset(session_id)
            else:
                payload["session_vars"] = bridge.sessions.persist(session_id, exec_globals)
                if note:
                    payload["session_reset"] = note
        return payload

    if compiled.co_flags & inspect.CO_COROUTINE:
        async def run_async():
            # Capture only this call's prints: a context-local buffer behind one proxy, so Kit
            # output from other tasks during the await still reaches the console.
            capture = _Capture()
            token = _CAPTURE.set(capture)
            _install_stdout_proxy()
            try:
                await eval(compiled, exec_globals)
            except Exception as e:
                fail(e, capture.buf.getvalue())
            finally:
                # Tasks and callbacks the code started copied this context; closing the holder
                # sends their later prints back to the console instead of a buffer nobody reads.
                capture.open = False
                _CAPTURE.reset(token)
            return finish(capture.buf.getvalue())
        return run_async()

    old_stdout = sys.stdout
    sys.stdout = capture = io.StringIO()
    try:
        exec(compiled, exec_globals)
    except Exception as e:
        fail(e, capture.getvalue())
    finally:
        sys.stdout = old_stdout
    return finish(capture.getvalue())


_CAPTURE = contextvars.ContextVar("isaac_mcp_run_python_capture", default=None)


class _Capture:
    """One awaiting call's output buffer; closed when the call returns."""

    def __init__(self):
        import io

        self.buf = io.StringIO()
        self.open = True


class _StdoutProxy:
    """sys.stdout stand-in: writes go to the awaiting run_python call's buffer when the current
    asyncio context has an open one, else to the real stream."""

    _isaac_mcp_proxy = True

    def __init__(self, real):
        self._real = real

    def _target(self):
        capture = _CAPTURE.get()
        return capture.buf if capture is not None and capture.open else self._real

    def write(self, text):
        return self._target().write(text)

    def writelines(self, lines):
        target = self._target()
        for line in lines:
            target.write(line)

    def flush(self):
        return self._target().flush()

    def __getattr__(self, name):
        return getattr(self._real, name)


def _install_stdout_proxy():
    import sys

    # Duck-typed so a hot-reloaded module (a new class) does not stack a second proxy.
    if not getattr(sys.stdout, "_isaac_mcp_proxy", False):
        sys.stdout = _StdoutProxy(sys.stdout)


def remove_stdout_proxy():
    """Restore the real stream (extension shutdown)."""
    import sys

    for _ in range(8):  # bounded; only our own proxy class is unwrapped, never a wrapper above it
        if not any("_isaac_mcp_proxy" in vars(cls) for cls in type(sys.stdout).__mro__):
            break
        sys.stdout = sys.stdout._real


_PUT_FILE_DESCRIPTION = (
    "Write a file on the bridge's box (content base64-encoded). run_python "
    "executes on the box, so code and assets must exist on ITS disk — this "
    "verb ships them from a remote driver without a side-channel rsync/scp. "
    "Writing a .py purges the sibling __pycache__ so the next import can't "
    "serve stale bytecode. Content rides one NDJSON frame in memory: right "
    "for code and tool-sized assets, not multi-GB payloads."
)
_PUT_FILE_PARAMETERS = {
    "path": {"type": "string", "required": True,
             "description": "Destination path on the bridge's box (~ expands)."},
    "content_b64": {"type": "string", "required": True,
                    "description": "File content, base64-encoded."},
    "mkdirs": {"type": "boolean",
               "description": "Create missing parent directories (default true)."},
}


def cmd_put_file(bridge, path: str, content_b64: str, mkdirs: bool = True) -> Dict[str, Any]:
    """Write bytes to the box's disk; the box-side half of remote file push."""
    import base64
    import binascii
    import os

    from .autoload import purge_pycache

    try:
        data = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ToolError(f"content_b64 is not valid base64: {e}")
    p = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(p)
    if mkdirs and parent:
        os.makedirs(parent, exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    if p.endswith(".py"):
        purge_pycache(parent)   # a pushed edit must never serve stale bytecode
    return {"path": p, "bytes": len(data)}


def cmd_get_file(bridge, path: str) -> Dict[str, Any]:
    """Read a file from the box's disk, base64-encoded; the pull half."""
    import base64
    import os

    p = os.path.abspath(os.path.expanduser(path))
    try:
        with open(p, "rb") as f:
            data = f.read()
    except OSError as e:
        raise ToolError(f"cannot read {p}: {e}")
    return {"path": p, "bytes": len(data),
            "content_b64": base64.b64encode(data).decode("ascii")}


def cmd_stat_file(bridge, path: str) -> Dict[str, Any]:
    """Existence/size/sha256 of a box-side file — the verify half of the file
    data plane. A remote driver ensures instead of blindly pushing: stat, skip
    when the hash matches, push only what changed — and can fail fast naming
    exactly which asset is stale, whatever bulk channel carried it."""
    import hashlib
    import os

    p = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(p):
        return {"path": p, "exists": False}
    h = hashlib.sha256()
    size = 0
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return {"path": p, "exists": True, "bytes": size, "sha256": h.hexdigest()}


def _app_ready():
    """Return Kit's readiness, or None when the application cannot report it."""
    try:
        from omni.kit.app import get_app
        ready = get_app().is_app_ready()
        return ready if isinstance(ready, bool) else None
    except Exception:  # Readiness is optional; status must remain available.
        return None


def cmd_status(bridge) -> Dict[str, Any]:
    """Instance identity over the wire — WHICH bridge answered this socket.

    On a multi-instance box (several bridged Kit processes on distinct ports)
    a client verifies it reached the instance it intended: pid + port +
    headless-vs-headful + app version, plus what's registered. The filesystem
    portfile advertises the same identity, but only same-box clients can read
    it — status is the surface a REMOTE client gets."""
    import os
    import platform
    import socket as _socket
    import time

    from .bridge import ADMISSION_CAPABILITY, HOLD_CAPABILITY, _app_identity

    ident = _app_identity()
    started = bridge._started_at
    ident.update({
        "pid": os.getpid(),
        "port": bridge._port,
        "bind_host": bridge._bind_host,
        "hostname": _socket.gethostname(),
        "python": platform.python_version(),
        "app_ready": _app_ready(),
        "started": started,
        "uptime_s": round(time.time() - started, 1) if started else None,
        "namespaces": sorted(bridge._namespaces),
        "tools": len(bridge.get_registered_tools(include_internal=True)),
        # namespace -> sha256 of the serving module's source tree: a client
        # compares against its local copy to detect the box serving older
        # code than it expects (fingerprinted at registration/reload).
        "toolsets": {o["namespace"]: o["metadata"]["source_hash"]
                     for o in bridge.get_owners().values()
                     if o["metadata"].get("source_hash")},
        # The admission contract this bridge speaks, what each tool module declared, and what
        # is running and waiting — how a caller learns a box holds only one half of the repair.
        "admission": bridge.admission_report(),
        # The capability by name, at the top level where a client looks for it without knowing
        # the report's shape. Absent on a doorway that predates request admission.
        "doorway_admission": ADMISSION_CAPABILITY,
        # THE HOLD (`bridge.HOLD_VERB`): whether one stands, whose, why and what it has refused —
        # never its token. `status` is the one verb a hold does not refuse, so this is readable
        # by everyone while it stands. The capability string is absent on an older doorway.
        "hold": bridge.hold_report(),
        "doorway_hold": HOLD_CAPABILITY,
    })
    return ident


def cmd_list_bridges(bridge) -> Dict[str, Any]:
    """All bridges advertised on THIS box (live portfiles, self included).

    The runtime dir is local to the box, so remote callers can't read it —
    but they can ask any reachable bridge (typically the stable installed-app
    port) to report its siblings, e.g. ephemeral-port instances."""
    import json as _json
    import os as _os
    from .bridge import _runtime_dir, _is_portfile_name, _valid_portfile

    found = []
    try:
        names = _os.listdir(_runtime_dir())
    except OSError:
        return {"bridges": found}
    for name in names:
        if not _is_portfile_name(name):
            continue
        try:
            with open(_os.path.join(_runtime_dir(), name)) as f:
                info = _json.load(f)
            if not _valid_portfile(name, info):
                continue
            _os.kill(info["pid"], 0)   # liveness, not proof of a responsive Kit
            found.append(info)
        except Exception:
            continue
    return {"bridges": found}


def cmd_list_tools(bridge) -> Dict[str, Any]:
    """Discovery: the advertised registry, canonical names only."""
    return {"tools": bridge.get_registered_tools()}


def cmd_reload_tools(bridge, module: str) -> Dict[str, Any]:
    """Hot-reload one autoloaded tool package in place (see autoload.py)."""
    from .autoload import reload_tool_module
    return reload_tool_module(bridge, module)


def builtin_tools(bridge) -> List:
    """The ToolDefinitions the bridge registers on itself at construction."""
    from .bridge import EXCLUSIVE_ATTRIBUTE, ToolDefinition  # deferred: bridge imports this module

    # `run_python` IS EXCLUSIVE, ALWAYS. Its body is whatever the caller sent, and a body may
    # advance the simulator from inside the call (`app.update()`, a `World.step`, a composer's
    # settle) — which destroys every request started beside it. The bridge cannot read the body
    # to find out, so the verb is given the main loop to itself every time. A body that calls
    # back into the doorway in-process is refused `DOORWAY_BUSY` at once; a body that pumps while
    # waiting for a LATER request has that request refused at its deadline, by name, not hung.
    run_python = functools.partial(cmd_run_python, bridge)
    setattr(run_python, EXCLUSIVE_ATTRIBUTE, True)

    return [
        ToolDefinition(
            name="run_python",
            description=_RUN_PYTHON_DESCRIPTION,
            parameters=_RUN_PYTHON_PARAMETERS,
            handler=run_python,
        ),
        ToolDefinition(
            name="list_bridges",
            description="List all isaac.mcp.bridge bridges advertised on this box "
                        "(this Kit process and any siblings, e.g. a second "
                        "instance on an ephemeral port).",
            parameters={},
            handler=functools.partial(cmd_list_bridges, bridge),
        ),
        ToolDefinition(
            name="reload_tools",
            description=_RELOAD_TOOLS_DESCRIPTION,
            parameters=_RELOAD_TOOLS_PARAMETERS,
            handler=functools.partial(cmd_reload_tools, bridge),
        ),
        ToolDefinition(
            name="put_file",
            description=_PUT_FILE_DESCRIPTION,
            parameters=_PUT_FILE_PARAMETERS,
            handler=functools.partial(cmd_put_file, bridge),
        ),
        ToolDefinition(
            name="get_file",
            description="Read a file from the bridge box's disk, returned "
                        "base64-encoded (content_b64). Pull half of put_file.",
            parameters={"path": {"type": "string", "required": True,
                                 "description": "Path on the bridge's box (~ expands)."}},
            handler=functools.partial(cmd_get_file, bridge),
        ),
        ToolDefinition(
            name="status",
            description="Instance identity: pid, port, headless-vs-headful, "
                        "app + version, uptime, registered namespaces — verify "
                        "you reached the instance you intended.",
            parameters={},
            handler=functools.partial(cmd_status, bridge),
        ),
        ToolDefinition(
            name="stat_file",
            description="Existence, size, and sha256 of a file on the bridge's "
                        "box — lets a remote driver skip unchanged pushes and "
                        "verify assets instead of blindly re-syncing.",
            parameters={"path": {"type": "string", "required": True,
                                 "description": "Path on the bridge's box (~ expands)."}},
            handler=functools.partial(cmd_stat_file, bridge),
        ),
        # Served like any tool, hidden from its own listing.
        ToolDefinition(
            name="list_tools",
            description="List all tools registered on this MCP bridge.",
            parameters={},
            handler=functools.partial(cmd_list_tools, bridge),
            internal=True,
        ),
    ]

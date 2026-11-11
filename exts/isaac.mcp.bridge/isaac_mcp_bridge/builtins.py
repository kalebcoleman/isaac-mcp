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
    "between calls. Opening a stage (including save-as) resets every session; the next call says so. "
    "For work longer than a few seconds pass background=true: it returns a job_id at once for "
    "job_status / job_cancel, instead of polling with more calls."
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
        "description": "If true, variables persist across calls with the same session_id. The reply "
                       "lists only the names this call added or rebound (session_vars) and the total "
                       "(session_var_count).",
    },
    "background": {
        "type": "boolean",
        "description": "Run as a job and return its job_id at once. The code must await (e.g. "
                       "`await omni.kit.app.get_app().next_update_async()` in its loop) and must not "
                       "pump the app itself. A `job` object is preloaded: job.report(fraction, text) "
                       "sets progress, job.checkpoint() raises once job_cancel was called. Follow "
                       "with job_status(job_id, wait_s=30); `result` and prints come back there.",
    },
    "label": {
        "type": "string",
        "description": "With background: a short name shown by job_status.",
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
                   persistent: bool = False, background: bool = False,
                   label: str = None) -> Dict[str, Any]:
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
    prior = bridge.sessions.namespace(session_id) if persistent else {}
    exec_globals.update(prior)
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
                exec_globals.pop("job", None)   # the job handle of a background call is not a variable
                names = bridge.sessions.persist(session_id, exec_globals)
                # Only what this call added or rebound: a long session otherwise echoes every name each call.
                payload["session_vars"] = [n for n in names if n not in prior or prior[n] is not exec_globals[n]]
                payload["session_var_count"] = len(names)
                if note:
                    payload["session_reset"] = note
        return payload

    if background:
        if not compiled.co_flags & inspect.CO_COROUTINE:
            raise ToolError("background=true needs code that awaits, e.g. `await omni.kit.app.get_app()."
                            "next_update_async()` inside its loop: code that never awaits blocks Kit until "
                            "it returns, so there would be nothing to poll. Nothing ran.")
        from .jobs import JobError

        async def work(job):
            capture = _Capture()
            job.output = capture.buf.getvalue
            token = _CAPTURE.set(capture)
            _install_stdout_proxy()
            exec_globals["job"] = job
            try:
                await eval(compiled, exec_globals)
            except Exception as e:
                try:
                    fail(e, capture.buf.getvalue())
                except ToolError as wrapped:
                    wrapped.details.pop("output", None)   # job_status carries output_tail already
                    raise wrapped from None
            finally:
                capture.open = False
                _CAPTURE.reset(token)
            payload = finish(capture.buf.getvalue())
            payload.pop("output", None)
            return payload

        try:
            job = bridge.jobs.start("run_python", work, label=label, cooperative=False)
        except JobError as e:
            raise ToolError(str(e))
        return {"job_id": job.id, "state": job.state,
                "next": f"job_status(job_id={job.id!r}, wait_s=30) until state is not running; "
                        "job_cancel stops it."}

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
    try:
        with open(p, "wb") as f:
            f.write(data)
    except OSError as e:
        raise ToolError(f"cannot write {p}: {e}" + ("; pass mkdirs=true to create the folder" if not mkdirs else ""))
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
        return {"path": p, "exists": False, **({"note": "This path is a directory; stat_file reads files."} if os.path.isdir(p) else {})}
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


def cmd_status(bridge, detail: bool = True) -> Dict[str, Any]:
    """Instance identity over the wire — WHICH bridge answered this socket.

    On a multi-instance box (several bridged Kit processes on distinct ports)
    a client verifies it reached the instance it intended: pid + port +
    headless-vs-headful + app version, plus what's registered. The filesystem
    portfile advertises the same identity, but only same-box clients can read
    it — status is the surface a REMOTE client gets.

    ``detail`` is read by the MCP host, which shortens the reply unless it is true; this verb always
    answers in full, so socket clients see no change."""
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
        # Background work still running in this Kit (job_status has the detail).
        "jobs": bridge.jobs.summary(),
    })
    # Which code this Kit runs: commit, uncommitted edits and in-place reloads. Never a process.
    try:
        from .source_state import report as source_report
        ident["extension"] = source_report()
    except Exception as e:  # noqa: BLE001 - optional detail; status must remain available
        ident["extension"] = {"commit": "unknown", "dirty": None, "error": str(e)[:200]}
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


_JOB_STATUS_DESCRIPTION = (
    "Status of background work in this Kit: run_python(background=true), sim__simulation__run and "
    "sim__trajectory__replay return a job_id. Gives state (running, succeeded, failed, cancelled), "
    "elapsed_s, progress, the last output and, when finished, the result or error. With no job_id it "
    "lists the jobs, newest first. Jobs are forgotten when Kit restarts."
)
_JOB_STATUS_PARAMETERS = {
    "job_id": {"type": "string",
               "description": "job_id from the call that started the work. Omit to list jobs."},
    "wait_s": {"type": "number",
               "description": "Wait up to this many seconds (0-60) for the job to end before answering, "
                              "instead of polling. The MCP server does the waiting; a direct socket "
                              "caller gets the current status at once."},
}


def cmd_job_status(bridge, job_id: str = None, wait_s: float = 0) -> Dict[str, Any]:
    """One job, or the list. ``wait_s`` is served by the MCP host, which polls: waiting here would
    hold a request slot and keep an exclusive call from starting."""
    from .jobs import JobError

    if job_id is None:
        jobs = bridge.jobs.jobs()
        return {"jobs": [j.describe(full=False) for j in jobs],
                "running": sum(j.state == "running" for j in jobs)}
    try:
        return bridge.jobs.get(job_id).describe()
    except JobError as e:
        raise ToolError(str(e))


def cmd_job_cancel(bridge, job_id: str) -> Dict[str, Any]:
    """Ask a running job to stop."""
    from .jobs import JobError

    try:
        return bridge.jobs.cancel(job_id)
    except JobError as e:
        raise ToolError(str(e))


def cmd_list_tools(bridge) -> Dict[str, Any]:
    """Discovery: the advertised registry, canonical names only."""
    return {"tools": bridge.get_registered_tools()}


def cmd_reload_tools(bridge, module: str) -> Dict[str, Any]:
    """Hot-reload one autoloaded tool package in place (see autoload.py)."""
    from .autoload import reload_tool_module
    try:
        return reload_tool_module(bridge, module)
    except (ImportError, ValueError) as e:
        raise ToolError(f"cannot reload {module}: {e}. reload_tools takes a project's autoloaded tool package. The bundled "
                        "sim_tools and ur5e_dt reload through Kit's file watcher when their files change; see `status`.")


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
            description="Where am I, in one call: the bridge that answered (pid, port, app, "
                        "readiness), every bridge advertised on this machine, the current "
                        "stage's session_id and stage_id with the timeline state, and running "
                        "jobs. With nothing running it says so and names the next call, "
                        "instead of failing. Call it once before the first simulator action; "
                        "there is no need to repeat it between calls.",
            parameters={"detail": {"type": "boolean",
                                   "description": "true: the full bridge identity as well (namespaces, "
                                                  "toolset hashes, admission, hold, extension source). "
                                                  "Default false: the short reply."}},
            handler=functools.partial(cmd_status, bridge),
        ),
        ToolDefinition(
            name="job_status",
            description=_JOB_STATUS_DESCRIPTION,
            parameters=_JOB_STATUS_PARAMETERS,
            handler=functools.partial(cmd_job_status, bridge),
        ),
        ToolDefinition(
            name="job_cancel",
            description="Stop a background job (job_id from the call that started it). It ends at its "
                        "next step and cleans up after itself; confirm with job_status.",
            parameters={"job_id": {"type": "string", "required": True,
                                   "description": "job_id of a running job."}},
            handler=functools.partial(cmd_job_cancel, bridge),
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

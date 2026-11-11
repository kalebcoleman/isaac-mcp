"""The bridge: a tool registry served over a local TCP socket, with every tool run on Kit's main loop.

Tool packs (the scene tools, a robot's own tools) register under an owner and a namespace. A
client sends one JSON line per request and gets one back (protocol.py). Three rules decide when a
request runs:

* An **inline** tool only reads the bridge's own state, so it is answered on the socket thread at
  once, whatever Kit is doing. ``status`` and ``job_status`` are inline.
* An **exclusive** tool may advance the app from inside its own call (``run_python`` can). It runs
  alone: it waits for running calls to finish, and calls that arrive while it runs wait for it.
* Every other tool starts in arrival order as soon as no exclusive call is running or waiting.

Waiting requests are kept as data, not as tasks: a task created while an exclusive call pumps the
app would be lost. Two switches protect a shared machine: a token every connection must present
(on by default; clients on the same account read it from a private file), and a read-only mode in
which only tools declared read-only run.

Stdlib only, so it imports and tests outside Kit; ``carb`` and ``omni`` are used when present.
"""

import collections
import hmac
import inspect
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import knobs, protocol, schema
from .jobs import JobStore
from .protocol import ToolError
from .sessions import SessionStore

BUILTIN_OWNER_ID = "isaac.mcp.bridge"
NAME_RULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
AUTH_VERB = "auth"


def _log(level: str, message: str) -> None:
    try:
        import carb

        getattr(carb, f"log_{level}")(message)
    except Exception:  # noqa: BLE001 - outside Kit
        if level != "info":
            print(message, file=sys.stderr)


@dataclass
class ToolDefinition:
    """One tool. ``handler`` takes the request's params as keyword arguments and returns the
    result, or raises (``ToolError`` to attach details); it may be a coroutine function."""

    name: str
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    handler: Optional[Callable[..., Any]] = None
    internal: bool = False     # served, but left out of list_tools
    read_only: bool = False    # changes nothing: allowed in read-only mode
    exclusive: bool = False    # may advance the app inside its call: runs alone
    inline: bool = False       # reads only bridge state: answered on the socket thread


@dataclass
class _Owner:
    owner_id: str
    namespace: Optional[str]
    display_name: str
    metadata: Dict[str, Any]
    tools: List[str] = field(default_factory=list)
    in_flight: int = 0
    draining: bool = False


class OwnerRegistrar:
    """What a tool pack registers its tools through."""

    def __init__(self, bridge: "McpBridge", owner_id: str):
        self._bridge, self.owner_id = bridge, owner_id

    def add(self, tool: ToolDefinition) -> str:
        return self._bridge._add_tool(self.owner_id, tool)

    def tool(self, description: str, parameters: Optional[Dict[str, Any]] = None, *, name: Optional[str] = None,
             **flags: Any) -> Callable:
        """Decorator form: ``@registrar.tool("what it does", {"x": {"type": "number"}})``."""
        def register(handler: Callable) -> Callable:
            self.add(ToolDefinition(name=name or handler.__name__, description=description,
                                    parameters=parameters or {}, handler=handler, **flags))
            return handler
        return register


MAX_UNSENT_BYTES = 256 * 1024 * 1024
_DONTWAIT = getattr(socket, "MSG_DONTWAIT", 0)


class _Connection:
    """One client. ``send`` never waits for the client to read: a tool's answer is sent from the
    main loop, and a client that asked for a large reply and stopped reading must not stop Kit.
    What the socket will not take at once is handed to a writer thread; a client that lets more
    than MAX_UNSENT_BYTES pile up is disconnected."""

    def __init__(self, sock: socket.socket, peer: str, authenticated: bool):
        self.sock, self.peer, self.authenticated = sock, peer, authenticated
        self.open = True
        self._send_lock = threading.Lock()
        self._unsent: "collections.deque[bytes]" = collections.deque()
        self._unsent_bytes = 0
        self._writing = False

    def send(self, frame: Dict[str, Any]) -> None:
        data = protocol.encode(frame)
        with self._send_lock:
            if not self.open:
                return
            if not self._writing and _DONTWAIT:
                try:
                    sent = self.sock.send(data, _DONTWAIT)
                except (BlockingIOError, InterruptedError):
                    sent = 0
                except OSError:
                    self.open = False
                    return
                if sent == len(data):
                    return
                data = data[sent:]
            if self._unsent_bytes + len(data) > MAX_UNSENT_BYTES:
                self.open = False
            else:
                self._unsent.append(data)
                self._unsent_bytes += len(data)
                if not self._writing:
                    self._writing = True
                    threading.Thread(target=self._write, name="isaac-mcp-bridge-writer", daemon=True).start()
        if not self.open:
            self.close()

    def _write(self) -> None:
        while True:
            with self._send_lock:
                if not self._unsent or not self.open:
                    self._writing = False
                    self._unsent.clear()
                    self._unsent_bytes = 0
                    return
                data = self._unsent.popleft()
            try:
                self.sock.sendall(data)
            except OSError:
                self.open = False
            with self._send_lock:
                self._unsent_bytes -= len(data)

    def close(self) -> None:
        self.open = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


@dataclass(eq=False)
class _Request:
    name: str
    params: Dict[str, Any]
    exclusive: bool
    reply: Callable[[Dict[str, Any]], None]
    connection: Optional[_Connection] = None
    arrived: float = field(default_factory=time.monotonic)


class McpBridge:
    def __init__(self, schedule_coroutine: Optional[Callable] = None, read_only: Optional[bool] = None,
                 token: Optional[str] = None, auth: Optional[bool] = None, job_records: Optional[str] = None):
        self._schedule_coroutine = schedule_coroutine   # None: Kit's run_coroutine, found on first use
        self._lock = threading.RLock()                  # registry and admission state
        self._owners: Dict[str, _Owner] = {}
        self._tools: Dict[str, ToolDefinition] = {}
        self._tool_owner: Dict[str, str] = {}
        self._listing: Dict[bool, Dict[str, Dict[str, Any]]] = {}   # get_registered_tools, until the registry changes
        self._running: List[_Request] = []
        self._waiting: "collections.deque[_Request]" = collections.deque()
        self._finished: Dict[str, float] = {}           # tool name -> when its last call ended
        self.read_only = knobs.READ_ONLY.flag() if read_only is None else bool(read_only)
        self._auth = knobs.AUTH.flag() if auth is None else bool(auth)
        self._token = token or knobs.TOKEN.read() or None
        self.refused_read_only = 0
        self.sessions = SessionStore()
        self.jobs = JobStore(self._schedule, record_dir=job_records, port=lambda: self._port)
        self._socket: Optional[socket.socket] = None
        self._connections: List[_Connection] = []
        self._port: Optional[int] = None
        self._bind_host: Optional[str] = None
        self._started_at: Optional[float] = None
        self._advertised: List[str] = []
        from .builtins import builtin_tools

        self._owners[BUILTIN_OWNER_ID] = _Owner(BUILTIN_OWNER_ID, None, "Bridge built-ins", {})
        for tool in builtin_tools(self):
            self._add_tool(BUILTIN_OWNER_ID, tool)

    # ---- registry -------------------------------------------------------------------------

    def register_owner(self, owner_id: str, namespace: str, *, display_name: Optional[str] = None,
                       metadata: Optional[Dict[str, Any]] = None) -> OwnerRegistrar:
        """Reserve an owner id and a namespace for a tool pack. Raises ValueError when either is
        taken, including by a previous instance whose calls are still finishing."""
        if not isinstance(namespace, str) or not NAME_RULE.fullmatch(namespace):
            raise ValueError(f"namespace {namespace!r} must be a dotted identifier")
        with self._lock:
            if owner_id in self._owners:
                state = "is still finishing its calls" if self._owners[owner_id].draining else "is already registered"
                raise ValueError(f"owner {owner_id!r} {state}")
            holder = next((o.owner_id for o in self._owners.values() if o.namespace == namespace), None)
            if holder is not None:
                raise ValueError(f"namespace {namespace!r} belongs to owner {holder!r}")
            self._owners[owner_id] = _Owner(owner_id, namespace, display_name or owner_id, dict(metadata or {}))
        return OwnerRegistrar(self, owner_id)

    def _add_tool(self, owner_id: str, tool: ToolDefinition) -> str:
        if not isinstance(tool.name, str) or not NAME_RULE.fullmatch(tool.name):
            raise ValueError(f"tool name {tool.name!r} must be a dotted identifier")
        if not callable(tool.handler):
            raise ValueError(f"tool {tool.name!r} has no handler")
        with self._lock:
            owner = self._owners.get(owner_id)
            if owner is None or owner.draining:
                raise ValueError(f"owner {owner_id!r} is not registered")
            canonical = tool.name if owner.namespace is None else f"{owner.namespace}.{tool.name}"
            if canonical in self._tools:
                raise ValueError(f"tool {canonical!r} is already registered")
            self._tools[canonical] = tool
            self._tool_owner[canonical] = owner_id
            owner.tools.append(canonical)
            self._listing.clear()
        return canonical

    def unregister_owner(self, owner_id: str, *, require_idle: bool = False) -> bool:
        """Remove an owner's tools. New calls to them fail at once; with calls still running the id
        and namespace stay reserved (``draining``) until they finish. ``require_idle`` refuses
        instead, changing nothing. Returns whether the owner existed."""
        with self._lock:
            owner = self._owners.get(owner_id)
            if owner is None or owner_id == BUILTIN_OWNER_ID:
                return False
            if require_idle and owner.in_flight:
                raise RuntimeError(f"owner {owner_id!r} has {owner.in_flight} call(s) running")
            for canonical in owner.tools:
                self._tools.pop(canonical, None)
                self._tool_owner.pop(canonical, None)
            owner.tools = []
            self._listing.clear()
            if owner.in_flight:
                owner.draining = True
            else:
                del self._owners[owner_id]
        return True

    def get_owners(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {o.owner_id: {"namespace": o.namespace, "display_name": o.display_name, "tools": list(o.tools),
                                 "in_flight": o.in_flight, "draining": o.draining, "metadata": dict(o.metadata)}
                    for o in self._owners.values()}

    def get_registered_tools(self, include_internal: bool = False) -> Dict[str, Dict[str, Any]]:
        """What list_tools serves: every tool's description and parameters, by canonical name.
        Built once per change of the registry; treat it as read-only."""
        with self._lock:
            listing = self._listing.get(include_internal)
            if listing is None:
                namespaces = {o.owner_id: o.namespace for o in self._owners.values()}
                listing = self._listing[include_internal] = {
                    name: {"description": tool.description, "parameters": tool.parameters,
                           "fields": schema.field_specs(tool.parameters),
                           "namespace": namespaces.get(self._tool_owner[name]),
                           "owner_id": self._tool_owner[name], "read_only": tool.read_only}
                    for name, tool in self._tools.items() if include_internal or not tool.internal}
            return listing

    def tool_count(self) -> int:
        with self._lock:
            return len(self._tools)

    def clear_sessions(self) -> None:
        self.sessions.clear()

    def tool_activity(self, recent_s: float = 2.0) -> Dict[str, bool]:
        """Tools running now (True) or finished within ``recent_s`` (False): Kit can deliver a stage
        or timeline event on a later frame than the call that caused it."""
        cutoff = time.monotonic() - recent_s
        with self._lock:
            recent = {name: False for name, when in self._finished.items() if when >= cutoff}
            return {**recent, **{request.name: True for request in self._running}}

    def busy(self) -> Dict[str, Any]:
        with self._lock:
            return {"running": [r.name for r in self._running], "waiting": len(self._waiting),
                    "exclusive": next((r.name for r in self._running if r.exclusive), None)}

    # ---- running a call -------------------------------------------------------------------

    def _refusal(self, name: Any) -> Optional[Dict[str, Any]]:
        """Why this call will not run, as a reply, or None. Called with the lock held."""
        tool = self._tools.get(name) if isinstance(name, str) else None
        if tool is None:
            return protocol.error(f"unknown tool: {name!r}", code="UNKNOWN_TOOL",
                                  known_tools=sorted(k for k, t in self._tools.items() if not t.internal))
        if self.read_only and not tool.read_only:
            self.refused_read_only += 1
            return protocol.error(
                f"READ_ONLY: {name!r} can change the simulator, and this Kit was started read-only "
                "(ISAAC_MCP_READ_ONLY=1). It did not run. Tools that only read still work.",
                code="READ_ONLY", refused=True, tool=name)
        return None

    async def _execute(self, name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Run one admitted call and build its reply. Never raises for a tool's own failure."""
        with self._lock:
            tool, owner = self._tools.get(name), self._owners.get(self._tool_owner.get(name, ""))
            if tool is None or owner is None:
                return protocol.error(f"unknown tool: {name!r}", code="UNKNOWN_TOOL")
            owner.in_flight += 1
        try:
            result = tool.handler(**params)
            if inspect.isawaitable(result):
                result = await result
            return protocol.success(result)
        except ToolError as exc:
            return protocol.error(str(exc), **{"error_type": "ToolError", **exc.details})
        except TypeError as exc:
            # Usually wrong arguments: say which ones the tool takes.
            return protocol.error(f"{name}: {exc}", error_type="TypeError", expected_parameters=list(tool.parameters),
                                  traceback=traceback.format_exc())
        except Exception as exc:  # noqa: BLE001 - a tool's failure is an answer, not a crash
            _log("error", f"[isaac.mcp.bridge] {name} failed: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            return protocol.error(str(exc), error_type=type(exc).__name__, traceback=traceback.format_exc())
        finally:
            with self._lock:
                self._finished[name] = time.monotonic()
                owner.in_flight -= 1
                if owner.draining and owner.in_flight <= 0:
                    self._owners.pop(owner.owner_id, None)

    def _execute_inline(self, name: str, tool: ToolDefinition, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return protocol.success(tool.handler(**params))
        except ToolError as exc:
            return protocol.error(str(exc), **{"error_type": "ToolError", **exc.details})
        except TypeError as exc:
            return protocol.error(f"{name}: {exc}", error_type="TypeError", expected_parameters=list(tool.parameters))
        except Exception as exc:  # noqa: BLE001
            return protocol.error(str(exc), error_type=type(exc).__name__, traceback=traceback.format_exc())

    def submit(self, name: Any, params: Optional[Dict[str, Any]], reply: Callable[[Dict[str, Any]], None],
               connection: Optional[_Connection] = None) -> None:
        """Take one request from any thread. ``reply`` is called exactly once with the answer: from
        this thread for a refusal or an inline tool, else from the main loop when the call ends."""
        params = params if isinstance(params, dict) else {}
        with self._lock:
            refusal = self._refusal(name)
            tool = None if refusal else self._tools[name]
        if refusal is not None:
            reply(refusal)
            return
        if tool.inline:
            reply(self._execute_inline(name, tool, params))
            return
        request = _Request(name, params, tool.exclusive, reply, connection)
        with self._lock:
            self._waiting.append(request)
            starting = self._admit()
        self._start(starting)

    def _admit(self) -> List[_Request]:
        """Move requests from waiting to running, in arrival order, as far as the rules allow."""
        starting = []
        while self._waiting:
            head = self._waiting[0]
            if head.connection is not None and not head.connection.open:
                self._waiting.popleft()      # its caller is gone: never start it
                continue
            if any(r.exclusive for r in self._running) or (head.exclusive and self._running):
                break
            self._running.append(self._waiting.popleft())
            starting.append(head)
        return starting

    def _start(self, requests: List[_Request]) -> None:
        for request in requests:
            try:
                self._schedule(self._run(request))
            except Exception as exc:  # noqa: BLE001 - e.g. the app is shutting down
                self._finish(request, protocol.error(f"the call could not be scheduled: {exc}", code="NOT_SCHEDULED"))

    async def _run(self, request: _Request) -> None:
        try:
            answer = await self._execute(request.name, request.params)
        except BaseException as exc:  # noqa: BLE001 - cancelled while the app closes
            self._finish(request, protocol.error(f"the call was interrupted: {type(exc).__name__}", code="INTERRUPTED"))
            raise
        self._finish(request, answer)

    def _finish(self, request: _Request, answer: Dict[str, Any]) -> None:
        try:
            request.reply(answer)
        finally:
            with self._lock:
                if request in self._running:
                    self._running.remove(request)
                starting = self._admit()
            self._start(starting)

    async def dispatch(self, name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """A call from code already running on the main loop (a panel button, another tool). It runs
        at once, or is refused while an exclusive call has the loop; it never waits."""
        with self._lock:
            refusal = self._refusal(name)
            if refusal is None and any(r.exclusive for r in self._running):
                refusal = protocol.error(f"BUSY: {self._running[0].name!r} has the simulator to itself; "
                                         f"{name!r} did not run. Try again when it has finished.", code="BUSY")
            if refusal is None:
                request = _Request(name, params or {}, self._tools[name].exclusive, lambda _answer: None)
                self._running.append(request)
        if refusal is not None:
            return refusal
        try:
            return await self._execute(name, params or {})
        finally:
            with self._lock:
                self._running.remove(request)
                starting = self._admit()
            self._start(starting)

    def _schedule(self, coroutine):
        """Hand a coroutine to the main loop and return its task or future."""
        if self._schedule_coroutine is None:
            from omni.kit.async_engine import run_coroutine

            self._schedule_coroutine = run_coroutine
        return self._schedule_coroutine(coroutine)

    # ---- the socket -----------------------------------------------------------------------

    @property
    def listening_port(self) -> Optional[int]:
        return self._port

    def start(self, port: int, host: str = "localhost") -> int:
        """Listen on ``host:port`` (0 picks a free port) and advertise it. Raises OSError when the
        address cannot be bound: a bridge on some other port is one no client would find."""
        if self._socket is not None:
            raise RuntimeError(f"the bridge is already listening on port {self._port}")
        listener = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((host, int(port)))
            listener.listen(16)
        except OSError:
            listener.close()
            raise
        self._socket, self._bind_host, self._port = listener, host, listener.getsockname()[1]
        self._started_at = time.time()
        if self._auth and not self._token:
            self._token = secrets.token_urlsafe(32)
        self._advertise()
        threading.Thread(target=self._accept, args=(listener,), name="isaac-mcp-bridge-accept", daemon=True).start()
        _log("info", f"[isaac.mcp.bridge] serving on {host}:{self._port}"
                     f"{' (read-only)' if self.read_only else ''}{'' if self._auth else ' (no token required)'}")
        return self._port

    def stop(self) -> None:
        listener, self._socket = self._socket, None
        if listener is not None:
            try:
                listener.shutdown(socket.SHUT_RDWR)   # wakes the accept thread
            except OSError:
                pass
            listener.close()
        for connection in list(self._connections):
            connection.close()
        self._connections.clear()
        for path in self._advertised:
            try:
                os.unlink(path)
            except OSError:
                pass
        self._advertised, self._port = [], None

    def _accept(self, listener: socket.socket) -> None:
        while self._socket is listener:
            try:
                sock, address = listener.accept()
            except OSError:
                return
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            connection = _Connection(sock, f"{address[0]}:{address[1]}", authenticated=not self._auth)
            self._connections.append(connection)
            threading.Thread(target=self._serve, args=(connection,), name="isaac-mcp-bridge-client", daemon=True).start()

    def _serve(self, connection: _Connection) -> None:
        buffer = bytearray()
        try:
            while connection.open:
                chunk = connection.sock.recv(1 << 18)
                if not chunk:
                    break
                scanned = len(buffer)          # a newline can only be in what just arrived
                buffer += chunk
                while True:
                    end = buffer.find(b"\n", scanned)
                    if end < 0:
                        break
                    line = bytes(buffer[:end])
                    del buffer[:end + 1]
                    scanned = 0
                    if line.strip():
                        self._frame(connection, line)
                if len(buffer) > protocol.MAX_FRAME_BYTES:
                    connection.send(protocol.error("the request is larger than 64 MiB", code="TOO_LARGE"))
                    break
        except OSError:
            pass
        finally:
            connection.close()
            if connection in self._connections:
                self._connections.remove(connection)
            with self._lock:
                starting = self._admit()      # drops what this caller left waiting
            self._start(starting)

    def _frame(self, connection: _Connection, line: bytes) -> None:
        try:
            frame = protocol.decode(line)
        except ValueError as exc:
            connection.send(protocol.error(f"malformed request: {exc}", code="MALFORMED"))
            return
        request_id = frame.get("id")
        tagged = (lambda answer: {"id": request_id, **answer}) if request_id is not None else (lambda answer: answer)
        if not connection.authenticated:
            if self._token_matches(frame.get("token")):
                connection.authenticated = True
            else:
                connection.send(tagged(protocol.error(
                    "AUTH_REQUIRED: this bridge needs its token. A client on the same account reads it from the "
                    "token file named in the bridge's portfile; from elsewhere set ISAAC_MCP_TOKEN.",
                    code="AUTH_REQUIRED", refused=True)))
                connection.close()
                return
        name = frame.get("type")
        if name == AUTH_VERB:
            connection.send(tagged(protocol.success({"authenticated": True})))
            return
        self.submit(name, frame.get("params"), lambda answer: connection.send(tagged(answer)), connection)

    def _token_matches(self, offered: Any) -> bool:
        return isinstance(offered, str) and bool(self._token) and hmac.compare_digest(offered, self._token)

    # ---- advertising this bridge to clients on the same account ---------------------------

    def _advertise(self) -> None:
        folder = runtime_dir()
        try:
            os.makedirs(folder, mode=0o700, exist_ok=True)
            sweep_stale_portfiles(folder)
            base = os.path.join(folder, f"{os.getpid()}-{self._port}")
            info = {**app_identity(), "pid": os.getpid(), "port": self._port, "host": self._bind_host,
                    "started": self._started_at, "auth": bool(self._auth), "read_only": self.read_only}
            if self._auth:
                descriptor = os.open(base + ".token", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w") as stream:
                    stream.write(self._token)
                self._advertised.append(base + ".token")
                info["token_file"] = base + ".token"
            with open(base + ".json", "w") as stream:
                json.dump(info, stream)
            self._advertised.append(base + ".json")
        except OSError as exc:
            _log("warn", f"[isaac.mcp.bridge] could not advertise the bridge in {folder}: {exc}")


def runtime_dir() -> str:
    """Where bridges advertise themselves: private to the account."""
    base = os.getenv("XDG_RUNTIME_DIR")
    if base:
        return os.path.join(base, "isaac-mcp")
    return os.path.join("/tmp", f"isaac-mcp-{os.getuid() if hasattr(os, 'getuid') else 'u'}")


def is_portfile_name(name: str) -> bool:
    return re.fullmatch(r"[1-9][0-9]*-[1-9][0-9]*\.json", name) is not None


def valid_portfile(name: str, info: Any) -> bool:
    """A real advertisement: named ``<pid>-<port>.json`` and saying the same inside."""
    return (isinstance(info, dict) and is_portfile_name(name)
            and type(info.get("pid")) is int and type(info.get("port")) is int and 1 <= info["port"] <= 65535
            and name == f"{info['pid']}-{info['port']}.json"
            and isinstance(info.get("host", "127.0.0.1"), str) and info.get("host", "127.0.0.1").strip() != ""
            and "\0" not in info.get("host", "127.0.0.1"))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def sweep_stale_portfiles(folder: str) -> None:
    """Remove advertisements (and token files) of processes that no longer exist. Kit's fast
    shutdown skips extension shutdown, so a bridge cannot always remove its own."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        found = re.fullmatch(r"([1-9][0-9]*)-[1-9][0-9]*\.(json|token)", name)
        if found and not _alive(int(found.group(1))):
            try:
                os.unlink(os.path.join(folder, name))
            except OSError:
                pass


_identity: Optional[Dict[str, Any]] = None


def app_identity() -> Dict[str, Any]:
    """Which app this is: name, version, and whether it has a window. Unknowns stay None. Read once
    Kit can say: none of it changes while the process runs."""
    global _identity
    if _identity is not None:
        return dict(_identity)
    app = version = headless = None
    try:
        import carb.settings

        settings = carb.settings.get_settings()
        app, version = settings.get("/app/name"), settings.get("/app/version")
        window = settings.get("/app/window/enabled")
        headless = None if window is None else not bool(window)
    except Exception:  # noqa: BLE001 - outside Kit
        pass
    if headless is None and any(arg in ("--no-window", "--headless") for arg in sys.argv):
        headless = True
    identity = {"app": app or os.path.basename(sys.argv[0] or "python"), "app_version": version, "headless": headless}
    if app and version is not None:
        _identity = identity
    return dict(identity)


_bridge: Optional[McpBridge] = None


def get_mcp_bridge() -> McpBridge:
    """The one bridge of this process, created on first use."""
    global _bridge
    if _bridge is None:
        from .jobs import default_record_dir

        _bridge = McpBridge(job_records=default_record_dir())
    return _bridge


def destroy_bridge() -> None:
    global _bridge
    if _bridge is not None:
        _bridge.stop()
        _bridge = None

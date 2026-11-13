"""Client for the bridge inside a Kit app: find it, connect, call a tool.

    from twinsmith.client import BridgeClient, call

    call("status")                                   # one call on a fresh connection
    with BridgeClient(port=8766) as bridge:          # many calls on one connection
        bridge.call("sim.stage.info")

A bridge advertises itself in a folder private to the account (a portfile, and a token file when it
requires its token). With no port given, the client uses ``TWINSMITH_PORT``,
else the one advertised bridge; with several advertised it refuses to guess. Every request carries
an id and the reply is matched by it, so a reply that arrives after its call timed out cannot be
mistaken for the next call's.

Stdlib only, and runnable as a file: ``python3 mcp/twinsmith/client.py --list-bridges``.
"""

import itertools
import json
import os
import socket
import stat
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

PORT_ENV = "TWINSMITH_PORT"
HOST_ENV = "TWINSMITH_HOST"
TOKEN_ENV = "TWINSMITH_TOKEN"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_TIMEOUT = 300.0
CONNECT_TIMEOUT = 5.0


class BridgeError(Exception):
    """The bridge answered, and the answer was an error. ``details`` holds what came with it."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


# ---- finding a bridge --------------------------------------------------------------------

def _runtime_dir() -> str:
    base = os.getenv("XDG_RUNTIME_DIR")
    if base:
        return os.path.join(base, "twinsmith")
    return os.path.join("/tmp", f"twinsmith-{os.getuid() if hasattr(os, 'getuid') else 'u'}")


def _runtime_dir_trusted(folder: str) -> bool:
    """Only a folder this account owns and nobody else can write to. Another local user could
    otherwise create the predictable /tmp folder first and advertise a bridge of their own."""
    if not hasattr(os, "getuid"):
        return True
    try:
        info = os.lstat(folder)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022


def _alive_and_ours(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return False        # alive, but another account's
    return True


def _advertisements() -> List[Tuple[str, Dict[str, Any]]]:
    """(path, contents) of every well-formed portfile in the trusted runtime folder."""
    folder = _runtime_dir()
    if not _runtime_dir_trusted(folder):
        return []
    found = []
    for name in sorted(os.listdir(folder)):
        stem, extension = os.path.splitext(name)
        pid, _, port = stem.partition("-")
        if extension != ".json" or not (pid.isdigit() and port.isdigit()):
            continue
        path = os.path.join(folder, name)
        try:
            if not stat.S_ISREG(os.lstat(path).st_mode):     # opening a FIFO would hang
                continue
            with open(path) as stream:
                info = json.load(stream)
        except (OSError, ValueError):
            continue
        if isinstance(info, dict) and info.get("pid") == int(pid) and info.get("port") == int(port) \
                and isinstance(info.get("host", DEFAULT_HOST), str):
            found.append((path, info))
    return found


def discover_bridges() -> List[Dict[str, Any]]:
    """Bridges advertised by live processes of this account that accept a connection. A portfile
    whose process is gone is removed."""
    live = []
    for path, info in _advertisements():
        reachable = _alive_and_ours(info["pid"])
        if reachable:
            try:
                socket.create_connection((info.get("host") or DEFAULT_HOST, info["port"]), timeout=0.5).close()
            except OSError:
                reachable = False
        if reachable:
            live.append({key: value for key, value in info.items() if key != "token_file"})
        else:
            for stale in (path, path[:-5] + ".token"):
                try:
                    os.unlink(stale)
                except OSError:
                    pass
    return live


def _resolve_endpoint(host: Optional[str], port: Optional[int]) -> Tuple[str, int]:
    """The (host, port) to dial. An advertised host is used only with its own advertised port."""
    host = host or os.getenv(HOST_ENV)
    if port:
        return host or DEFAULT_HOST, int(port)
    configured = os.getenv(PORT_ENV)
    if configured:
        return host or DEFAULT_HOST, int(configured)
    bridges = discover_bridges()
    if len(bridges) == 1:
        return host or bridges[0].get("host") or DEFAULT_HOST, int(bridges[0]["port"])
    if bridges:
        listing = ", ".join(f"{b.get('app', '?')} pid={b['pid']} port={b['port']}" for b in bridges)
        raise RuntimeError(f"multiple bridges running ({listing}): pass port= or set {PORT_ENV}")
    raise RuntimeError(f"no bridge found: none advertised in {_runtime_dir()}, and no port given "
                       f"(pass port= or set {PORT_ENV})")


_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def bridge_token(port: int, host: Optional[str] = None) -> Optional[str]:
    """The token for the bridge on ``port``: ``TWINSMITH_TOKEN``, else the private token file a
    bridge on this machine wrote beside its portfile. None when there is neither. A local
    bridge's token is never offered to another machine that happens to use the same port."""
    token = os.getenv(TOKEN_ENV)
    if token:
        return token.strip()
    for path, info in _advertisements():
        if host not in (None, *_LOOPBACK) and host != info.get("host"):
            continue
        if info["port"] == int(port) and _alive_and_ours(info["pid"]):
            try:
                with open(path[:-5] + ".token") as stream:
                    return stream.read().strip() or None
            except OSError:
                return None
    return None


# ---- calling it --------------------------------------------------------------------------

class BridgeClient:
    """One connection to a bridge, opened on first use and reopened if it drops between calls."""

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None, timeout: float = DEFAULT_TIMEOUT):
        self._host, self._port, self.timeout = host, port, timeout
        self._sock: Optional[socket.socket] = None
        self._buffer = bytearray()
        self._scanned = 0                       # how much of the buffer is known to hold no newline
        self._token: Optional[str] = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    @property
    def host(self) -> str:
        return _resolve_endpoint(self._host, self._port)[0]

    @property
    def port(self) -> int:
        return _resolve_endpoint(self._host, self._port)[1]

    def connect(self, timeout: Optional[float] = None) -> None:
        if self._sock is not None:
            return
        host, port = _resolve_endpoint(self._host, self._port)
        sock = socket.create_connection((host, port), timeout=min(CONNECT_TIMEOUT, timeout or self.timeout))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock, self._buffer, self._scanned = sock, bytearray(), 0
        self._token = bridge_token(port, host)
        if self._token:
            # Shown once per connection, in a request of its own: until then the bridge takes only
            # small requests, so the first call may be as large as any other.
            try:
                sock.settimeout(min(CONNECT_TIMEOUT, timeout or self.timeout))
                sock.sendall(json.dumps({"id": 0, "type": "auth", "token": self._token}).encode() + b"\n")
                reply = self._read_reply(0, time.monotonic() + CONNECT_TIMEOUT)
            except (OSError, ValueError):
                self.close()
                raise
            if reply.get("status") != "success":
                self.close()
                raise BridgeError(reply.get("message", "the bridge refused this client's token"),
                                  {key: value for key, value in reply.items() if key not in ("status", "message", "id")})

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def __enter__(self) -> "BridgeClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _dropped(self) -> bool:
        """Whether the idle connection was closed by the other side since the last call."""
        try:
            self._sock.setblocking(False)
            try:
                return self._sock.recv(1, socket.MSG_PEEK) == b""
            except (BlockingIOError, InterruptedError):
                return False
            finally:
                self._sock.setblocking(True)
        except OSError:
            return True

    def _read_reply(self, request_id: int, deadline: float) -> Dict[str, Any]:
        while True:
            while True:
                end = self._buffer.find(b"\n", self._scanned)
                if end < 0:
                    self._scanned = len(self._buffer)      # a large reply is searched once, not once per chunk
                    break
                line = bytes(self._buffer[:end])
                del self._buffer[:end + 1]
                self._scanned = 0
                if not line.strip():
                    continue
                reply = json.loads(line)
                if reply.get("id") in (request_id, None):
                    return reply     # None: a bridge that predates request ids, or a refusal before parsing
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for the bridge's reply; the call may still be running")
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(1 << 18)
            except socket.timeout:
                raise TimeoutError("timed out waiting for the bridge's reply; the call may still be running") from None
            if not chunk:
                raise ConnectionError("the bridge closed the connection before replying; the call may have run")
            self._buffer += chunk

    def call(self, tool: str, params: Optional[Dict[str, Any]] = None, *, timeout: Optional[float] = None) -> Any:
        """Call ``tool`` and return its result. Raises BridgeError for the bridge's own error reply,
        TimeoutError when no reply came in time, and OSError when it could not be reached."""
        limit = self.timeout if timeout is None else timeout
        with self._lock:
            if self._sock is not None and self._dropped():
                self.close()                      # nothing was sent on it, so reconnecting is safe
            self.connect(limit)
            request_id = next(self._ids)
            frame: Dict[str, Any] = {"id": request_id, "type": tool, "params": params or {}}
            try:
                self._sock.settimeout(limit)
                self._sock.sendall(json.dumps(frame).encode() + b"\n")
                reply = self._read_reply(request_id, time.monotonic() + limit)
            except (OSError, ValueError):
                self.close()                      # a reply may still be on its way: do not reuse the connection
                raise
        if reply.get("status") == "success":
            return reply.get("result")
        details = {key: value for key, value in reply.items() if key not in ("status", "message", "id")}
        raise BridgeError(reply.get("message", "the bridge answered with an error"), details)


def call(tool: str, params: Optional[Dict[str, Any]] = None, *, host: Optional[str] = None,
         port: Optional[int] = None, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """One call on its own connection."""
    with BridgeClient(host=host, port=port, timeout=timeout) as client:
        return client.call(tool, params)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python3 -m twinsmith.client" if __package__ else f"python3 {sys.argv[0]}",
        description="Call one tool on a running bridge. Exit codes: 0 success, 1 the bridge answered with an "
                    "error, 2 unreachable or bad usage.")
    parser.add_argument("tool", nargs="?", help="canonical tool name, e.g. status, list_tools or sim.stage.info")
    parser.add_argument("params", nargs="?", default="{}", help="JSON object of parameters (default: {})")
    parser.add_argument("--list-bridges", action="store_true", help="list the bridges advertised on this machine")
    parser.add_argument("--host", default=None, help=f"bridge host (default: ${HOST_ENV}, else the advertised one)")
    parser.add_argument("--port", type=int, default=None, help=f"bridge port (default: ${PORT_ENV})")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)
    if args.list_bridges:
        bridges = discover_bridges()
        print(json.dumps(bridges, indent=2))
        return 0 if bridges else 2
    if not args.tool:
        parser.print_usage(sys.stderr)
        return 2
    try:
        params = json.loads(args.params)
        if not isinstance(params, dict):
            raise ValueError("params must be a JSON object")
    except ValueError as exc:
        print(f"params error: {exc}", file=sys.stderr)
        return 2
    try:
        result = call(args.tool, params, host=args.host, port=args.port, timeout=args.timeout)
    except BridgeError as exc:
        print(str(exc), file=sys.stderr)
        if exc.details:
            print(json.dumps(exc.details, indent=2, default=repr), file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, default=repr))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

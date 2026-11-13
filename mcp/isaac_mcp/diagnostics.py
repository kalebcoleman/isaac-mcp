"""Durable server log and code-drift detection for the host server.

An MCP client discards the server's stderr, so when a connection drops there is nothing to read.
``setup_server_log`` adds a small rotating file (one per server process) that records why the
process started and why it ended. ``DriftMonitor`` notices that the code on disk has changed since
this process imported it (a checkout that is fast-forwarded under a running server).

Nothing here is imported for its side effects: the log is configured only by ``main()``, so
importing ``isaac_mcp.server`` (tests, scripts) never writes outside the process. Request
payloads, environment values other than the documented filter switches, and file contents are
never logged.
"""

from __future__ import annotations

import atexit
import faulthandler
import hashlib
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

LOG_FORMAT = "%(asctime)s pid=%(process)d %(levelname)s %(name)s: %(message)s"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 2                 # server-<pid>.log plus two rotated copies: about 3 MB at most
KEEP_LOG_FILES = 30             # older logs of ended processes are pruned
HEARTBEAT_S = 300.0
DRIFT_TTL_S = 30.0
#: Environment switches whose values are safe to record (they select tools; none is a secret).
LOGGED_ENV = ("ISAAC_MCP_TOOLSETS", "ISAAC_MCP_PHYSICS_BACKEND", "ISAAC_MCP_NAME", "UR5E_DT_PORT", "ISAAC_MCP_PORT")

log = logging.getLogger("isaac_mcp.diagnostics")

_handler: Optional[logging.Handler] = None
_log_path: Optional[Path] = None
_shutdown_logged = False


def log_enabled(env: Mapping[str, str]) -> bool:
    return env.get("ISAAC_MCP_LOG", "1").strip().lower() not in {"0", "false", "no", "off"}


def log_dir(env: Mapping[str, str]) -> Path:
    override = env.get("ISAAC_MCP_LOG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    state = env.get("XDG_STATE_HOME", "").strip()
    base = Path(state).expanduser() if state else Path.home() / ".local" / "state"
    return base / "isaac-mcp"


def current_log_path() -> Optional[Path]:
    return _log_path


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _prune(directory: Path, keep: int = KEEP_LOG_FILES) -> None:
    """Delete the oldest logs of processes that have ended; never a live process's log."""
    try:
        files = sorted(directory.glob("server-*.log*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[keep:]:
            stem = path.name.split(".log")[0].removeprefix("server-")
            if stem.isdigit() and _pid_alive(int(stem)):
                continue
            path.unlink(missing_ok=True)
    except OSError:
        pass


def setup_server_log(env: Mapping[str, str] = os.environ) -> Optional[Path]:
    """Attach a rotating ``server-<pid>.log`` to the root logger. Returns its path, or None when
    disabled (``ISAAC_MCP_LOG=0``) or when the directory cannot be used (stderr logging goes on)."""
    global _handler, _log_path
    if not log_enabled(env):
        return None
    if _handler is not None:
        return _log_path
    try:
        directory = log_dir(env)
        existed = directory.is_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not existed:     # never change the mode of a directory somebody else made
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
        path = directory / f"server-{os.getpid()}.log"
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError as exc:
        logging.getLogger(__name__).warning("durable server log disabled: %s", exc)
        return None
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    root.addHandler(handler)
    _handler, _log_path = handler, path
    _prune(directory)
    return path


def teardown_server_log() -> None:
    """Detach and close the file handler (tests, and the end of ``main``)."""
    global _handler, _log_path, _shutdown_logged
    if _handler is not None:
        logging.getLogger().removeHandler(_handler)
        try:
            _handler.close()
        finally:
            _handler, _log_path = None, None
    _shutdown_logged = False


def _flush() -> None:
    if _handler is not None:
        try:
            _handler.flush()
        except Exception:  # noqa: BLE001
            pass


def _parent_command(ppid: int) -> str:
    """Command name of the parent (the MCP client), from /proc; 'unknown' elsewhere."""
    try:
        return Path(f"/proc/{ppid}/comm").read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def log_startup(version: str, commit: Optional[str], tool_count: Optional[int],
                env: Mapping[str, str] = os.environ) -> None:
    ppid = os.getppid()
    filters = {name: env[name] for name in LOGGED_ENV if name in env}
    log.info("process start: pid=%d ppid=%d parent=%s version=%s commit=%s python=%s tools=%s filters=%s",
             os.getpid(), ppid, _parent_command(ppid), version, (commit or "unknown")[:12],
             sys.version.split()[0], tool_count if tool_count is not None else "unknown", filters or "none")
    _flush()


def log_shutdown(reason: str) -> None:
    """One clean-shutdown line per process, whichever path ends it first."""
    global _shutdown_logged
    if _shutdown_logged:
        return
    _shutdown_logged = True
    log.info("clean shutdown: %s", reason)
    _flush()


def _rss_kb() -> Optional[int]:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def start_heartbeat(interval: float = HEARTBEAT_S) -> threading.Thread:
    """A line every few minutes: shows the process was alive, and when its parent (the client) went
    away (ppid changes, usually to 1)."""
    first_ppid = os.getppid()

    def beat() -> None:
        while True:
            time.sleep(interval)
            ppid = os.getppid()
            log.info("alive: ppid=%d%s rss_kb=%s", ppid,
                     "" if ppid == first_ppid else f" (parent changed from {first_ppid}: the client is gone)",
                     _rss_kb())
            _flush()

    thread = threading.Thread(target=beat, name="isaac-mcp-heartbeat", daemon=True)
    thread.start()
    return thread


_SIGNALS = ("SIGTERM", "SIGINT", "SIGHUP", "SIGPIPE")


def install_handlers(signals: Sequence[str] = _SIGNALS) -> Dict[int, Any]:
    """Log the signals that end or disturb the server, then do what would have happened anyway.

    SIGTERM/SIGHUP: log, restore the previous disposition and re-deliver, so the exit status is
    unchanged. SIGINT: log, then run the previous handler (KeyboardInterrupt). SIGPIPE: Python
    already ignores it (writes raise BrokenPipeError); it is logged and ignored. SIGKILL cannot be
    caught: a log with no shutdown line means SIGKILL, the OOM killer or a native crash.
    Returns the previous handlers by signal number. Only the main thread may install handlers."""
    previous: Dict[int, Any] = {}
    if threading.current_thread() is not threading.main_thread():
        return previous

    def make(signum: int, name: str) -> Callable[[int, Any], None]:
        def handler(received: int, frame: Any) -> None:
            before = previous.get(signum)
            log.warning("signal received: %s (ppid=%d)", name, os.getppid())
            _flush()
            if name == "SIGPIPE":
                return
            log_shutdown(f"signal {name}")
            if callable(before):
                before(received, frame)
                return
            if before == signal.SIG_IGN:
                return
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        return handler

    for name in signals:
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, make(signum, name))
        except (OSError, ValueError):
            previous.pop(signum, None)
    return previous


def install_crash_hooks() -> None:
    """Unhandled exceptions (main and worker threads), native faults and interpreter exit all leave a line."""
    previous_hook = sys.excepthook

    def excepthook(exc_type, exc, tb) -> None:
        log.critical("unhandled exception: %s", "".join(traceback.format_exception(exc_type, exc, tb)).rstrip())
        _flush()
        previous_hook(exc_type, exc, tb)

    sys.excepthook = excepthook
    previous_thread_hook = threading.excepthook

    def thread_hook(args: Any) -> None:
        log.critical("unhandled exception in thread %s: %s", getattr(args.thread, "name", "?"),
                     "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).rstrip())
        _flush()
        previous_thread_hook(args)

    threading.excepthook = thread_hook
    if _log_path is not None:
        try:
            # SIGSEGV/SIGABRT in native code: Python tracebacks go to the same file.
            faulthandler.enable(file=open(_log_path, "a", buffering=1, encoding="utf-8"))
        except (OSError, ValueError, RuntimeError):
            pass
    atexit.register(log_shutdown, "process exit without a recorded cause")


def run_logged(run: Callable[[], None]) -> None:
    """Run the stdio server and record how it ended. ``run`` returning means the client closed stdin
    (EOF) or the transport closed; an exception is logged with its traceback and re-raised."""
    log.info("serving MCP over stdio")
    _flush()
    try:
        run()
    except KeyboardInterrupt:
        log_shutdown("KeyboardInterrupt")
        raise
    except SystemExit as exc:
        log_shutdown(f"SystemExit({exc.code!r})")
        raise
    except BaseException:
        log.critical("server crashed: %s", traceback.format_exc().rstrip())
        _flush()
        raise
    else:
        log_shutdown("stdio transport closed (client closed stdin: EOF)")


# ---------------------------------------------------------------- code drift

def _stat_key(path: Path) -> Optional[tuple]:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _sha(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class DriftMonitor:
    """Records the code a server process started with and reports when disk has moved on.

    Cheap by construction: a handful of ``stat`` calls, cached for ``ttl`` seconds; a catalog file
    is hashed only when its mtime or size changed. The git commit is compared by the caller (it
    already reads ``.git`` directly)."""

    def __init__(self, package_dir: Path, hashed: Sequence[str] = (), watched: Sequence[str] = (),
                 ttl: float = DRIFT_TTL_S, clock: Callable[[], float] = time.monotonic):
        self.package_dir, self.ttl, self._clock = Path(package_dir), ttl, clock
        self._hashed, self._watched = tuple(hashed), tuple(watched)
        self._start = {rel: (_stat_key(self.package_dir / rel),
                             _sha(self.package_dir / rel) if rel in self._hashed else None)
                       for rel in self._hashed + self._watched}
        self._checked_at: Optional[float] = None
        self._changed: List[str] = []

    @classmethod
    def for_package(cls, package_dir: Path, **kw: Any) -> "DriftMonitor":
        catalogs = sorted(f"catalogs/{p.name}" for p in (Path(package_dir) / "catalogs").glob("*.json"))
        watched = ("server.py", "host_catalog.py", "scene_contracts.py", "tool_hints.py", "local_tools.py")
        return cls(package_dir, hashed=catalogs, watched=watched, **kw)

    def changed_files(self) -> List[str]:
        """Names (relative to the package) of key files that differ from server start."""
        now = self._clock()
        if self._checked_at is not None and now - self._checked_at < self.ttl:
            return list(self._changed)
        changed = []
        for rel, (stat_key, digest) in self._start.items():
            current = _stat_key(self.package_dir / rel)
            if current == stat_key:
                continue
            if rel in self._hashed and digest is not None and _sha(self.package_dir / rel) == digest:
                continue    # touched, not changed
            changed.append(rel)
        self._checked_at, self._changed = now, changed
        return list(changed)

    def describe(self, started_commit: Optional[str], current_commit: Optional[str]) -> Dict[str, Any]:
        """``{"code_drift": bool, "code_drift_message": str?}`` for the status ``host`` object."""
        moved = bool(started_commit and current_commit and started_commit != current_commit)
        files = self.changed_files()
        if not (moved or files):
            return {"code_drift": False}
        if moved:
            message = (f"checkout moved from {started_commit[:12]} to {current_commit[:12]} since this "
                       "server started; reconnect to load it")
        else:
            message = ("key files changed on disk since this server started (" + ", ".join(files[:5])
                       + "); reconnect to load them")
        return {"code_drift": True, "code_drift_message": message}

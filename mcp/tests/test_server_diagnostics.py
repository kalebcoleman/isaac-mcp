"""Durable server log, signal/crash hooks and code-drift detection (isaac_mcp.diagnostics)."""
import logging
import logging.handlers
import os
import signal
import stat
import sys
import threading

import pytest

from isaac_mcp import diagnostics as dx
from isaac_mcp import server as gw

A, B = "a" * 40, "b" * 40


@pytest.fixture(autouse=True)
def _clean_log_state():
    yield
    dx.teardown_server_log()


def _read(path):
    for handler in logging.getLogger().handlers:
        handler.flush()
    return path.read_text()


def test_log_file_created_private_with_pid_lines_and_rotation_config(tmp_path):
    path = dx.setup_server_log({"ISAAC_MCP_LOG_DIR": str(tmp_path / "logs")})
    assert path == tmp_path / "logs" / f"server-{os.getpid()}.log"
    assert stat.S_IMODE((tmp_path / "logs").stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    handler = dx._handler
    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert handler.maxBytes == 1_000_000 and handler.backupCount == 2
    logging.getLogger("t").info("hello")
    assert f"pid={os.getpid()}" in _read(path) and "hello" in _read(path)


def test_log_can_be_disabled_and_never_defaults_into_the_real_state_dir(tmp_path, monkeypatch):
    assert dx.setup_server_log({"ISAAC_MCP_LOG": "0", "ISAAC_MCP_LOG_DIR": str(tmp_path)}) is None
    assert not list(tmp_path.iterdir())
    assert dx.log_dir({"XDG_STATE_HOME": str(tmp_path)}) == tmp_path / "isaac-mcp"
    monkeypatch.setenv("HOME", str(tmp_path))
    assert dx.log_dir({}) == tmp_path / ".local" / "state" / "isaac-mcp"


def test_unusable_log_dir_falls_back_to_stderr_only(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert dx.setup_server_log({"ISAAC_MCP_LOG_DIR": str(blocker / "sub")}) is None


def test_startup_line_records_identity_and_filters_but_no_other_env(tmp_path):
    path = dx.setup_server_log({"ISAAC_MCP_LOG_DIR": str(tmp_path)})
    dx.log_startup("1.2.3", A, 127, {"ISAAC_MCP_LOCAL_TOOLS": "all", "SECRET_TOKEN": "hunter2"})
    text = _read(path)
    assert "process start" in text and f"ppid={os.getppid()}" in text and "version=1.2.3" in text
    assert A[:12] in text and "tools=127" in text and "ISAAC_MCP_LOCAL_TOOLS" in text
    assert "hunter2" not in text and "SECRET_TOKEN" not in text


def test_run_logged_records_eof_exit_and_crashes_with_traceback(tmp_path):
    path = dx.setup_server_log({"ISAAC_MCP_LOG_DIR": str(tmp_path)})
    dx.run_logged(lambda: None)
    assert "clean shutdown: stdio transport closed" in _read(path)
    dx.teardown_server_log()
    path = dx.setup_server_log({"ISAAC_MCP_LOG_DIR": str(tmp_path)})

    def boom():
        raise RuntimeError("kaput")

    with pytest.raises(RuntimeError):
        dx.run_logged(boom)
    text = _read(path)
    assert "server crashed" in text and "RuntimeError: kaput" in text and "Traceback" in text
    assert "clean shutdown" not in text.split("server crashed")[1]


def test_signal_handlers_register_log_and_chain_without_killing_the_test(tmp_path):
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("signals need the main thread")
    path = dx.setup_server_log({"ISAAC_MCP_LOG_DIR": str(tmp_path)})
    saved = {n: signal.getsignal(getattr(signal, n)) for n in ("SIGTERM", "SIGINT", "SIGHUP", "SIGPIPE")}
    seen = []
    signal.signal(signal.SIGTERM, lambda s, f: seen.append(s))   # stand-in for the prior disposition
    try:
        previous = dx.install_handlers()
        assert set(previous) == {signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGPIPE}
        for name in ("SIGTERM", "SIGINT", "SIGHUP", "SIGPIPE"):
            assert callable(signal.getsignal(getattr(signal, name)))
        signal.raise_signal(signal.SIGTERM)    # chained to the stand-in; the process survives
        signal.raise_signal(signal.SIGPIPE)    # logged and ignored
    finally:
        for name, handler in saved.items():
            signal.signal(getattr(signal, name), handler)
    text = _read(path)
    assert seen == [signal.SIGTERM]
    assert "signal received: SIGTERM" in text and "signal received: SIGPIPE" in text
    assert "clean shutdown: signal SIGTERM" in text


def test_crash_hooks_log_unhandled_exceptions_in_threads(tmp_path, monkeypatch):
    path = dx.setup_server_log({"ISAAC_MCP_LOG_DIR": str(tmp_path)})
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    import faulthandler
    monkeypatch.setattr(faulthandler, "enable", lambda **kw: None)
    monkeypatch.setattr(dx.atexit, "register", lambda *a, **k: None)
    dx.install_crash_hooks()
    try:
        raise ValueError("in main")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    threading.excepthook(threading.ExceptHookArgs((ValueError, ValueError("in thread"), None, threading.current_thread())))
    text = _read(path)
    assert "unhandled exception" in text and "in main" in text and "in thread" in text


def test_prune_keeps_live_logs_and_removes_old_dead_ones(tmp_path):
    for i in range(5):
        (tmp_path / f"server-{4000000 + i}.log").write_text("x")
    live = tmp_path / f"server-{os.getpid()}.log"
    live.write_text("x")
    os.utime(live, (1, 1))   # oldest, but alive
    dx._prune(tmp_path, keep=2)
    names = {p.name for p in tmp_path.iterdir()}
    assert live.name in names and len(names) == 3


# ------------------------------------------------------------------ drift

def test_drift_monitor_flags_commit_move_and_changed_catalog_but_not_a_touch(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "catalogs").mkdir(parents=True)
    (pkg / "catalogs" / "sim.json").write_text("{}")
    (pkg / "server.py").write_text("a")
    now = [0.0]
    mon = dx.DriftMonitor.for_package(pkg, clock=lambda: now[0])
    assert mon.describe(A, A) == {"code_drift": False}
    os.utime(pkg / "catalogs" / "sim.json", (1, 1))        # touched, same bytes
    now[0] = 31
    assert mon.describe(A, A) == {"code_drift": False}
    moved = mon.describe(A, B)
    assert moved["code_drift"] is True
    assert moved["code_drift_message"] == (f"checkout moved from {A[:12]} to {B[:12]} since this server "
                                           "started; reconnect to load it")
    (pkg / "catalogs" / "sim.json").write_text('{"x": 1}')
    assert mon.describe(A, A) == {"code_drift": False}     # cached for 30 s
    now[0] = 70
    edited = mon.describe(A, A)
    assert edited["code_drift"] is True and "catalogs/sim.json" in edited["code_drift_message"]


def test_drift_check_is_cached_and_does_not_restat_within_ttl(tmp_path, monkeypatch):
    (tmp_path / "server.py").write_text("a")
    now = [0.0]
    mon = dx.DriftMonitor(tmp_path, watched=["server.py"], clock=lambda: now[0])
    calls = []
    real = dx._stat_key
    monkeypatch.setattr(dx, "_stat_key", lambda p: calls.append(p) or real(p))
    mon.changed_files(); mon.changed_files(); mon.changed_files()
    assert len(calls) == 1


def _checkout(root, commit):
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / ".git" / "refs" / "heads" / "main").write_text(commit + "\n")
    return root


def test_status_host_object_reports_code_drift_from_a_tmp_git_repo(tmp_path, monkeypatch):
    checkout = _checkout(tmp_path / "tree", A)
    monkeypatch.setattr(gw, "_HOST_CHECKOUT", checkout)
    monkeypatch.setattr(gw, "_HOST_COMMIT", A)
    assert gw._host_identity()["code_drift"] is False
    assert "code_drift" not in gw._host_identity_text()
    (checkout / ".git" / "refs" / "heads" / "main").write_text(B + "\n")
    identity = gw._host_identity()
    assert identity["code_drift"] is True
    assert A[:12] in identity["code_drift_message"] and B[:12] in identity["code_drift_message"]
    assert "reconnect to load it" in identity["code_drift_message"]
    assert "code_drift: true" in gw._host_identity_text()
    assert "code_drift: true" in gw._isaac_help(None)
    assert str(tmp_path) not in identity["code_drift_message"]


def test_importing_the_server_does_not_create_a_log_file(tmp_path):
    assert dx.current_log_path() is None

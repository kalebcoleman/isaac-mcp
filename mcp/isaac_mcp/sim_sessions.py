"""Opt-in, locally managed Isaac Sim launches with conservative concurrency checks.

Resource checks are snapshots, not a reservation or a guarantee against OOM. No
existing process is stopped automatically, including when resources later run low.
"""

from __future__ import annotations

import errno
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Mapping

try:
    import fcntl
except ImportError:  # Session launch is Linux-only; do not break unrelated host tools.
    fcntl = None

from mcp.server.fastmcp import FastMCP

from . import client
from .isaaclab import jobs as lab_jobs
from .local_tools import PYTHON_EXECUTION, ScopedTools

_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
_DEFAULT_ROOT = Path(__file__).resolve().parents[2]
# Linux 6.9 added a pidfd signal scope for an entire process group. Older
# kernels get a race-safe signal to the leader only.
_PIDFD_SIGNAL_PROCESS_GROUP = 1 << 2


def _config_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _sessions_dir(env: Mapping[str, str]) -> Path:
    value = env.get("ISAAC_MCP_SIM_SESSIONS_DIR")
    path = Path(value).expanduser() if value else Path.home() / ".local/share/isaac-mcp/sim-sessions"
    # The launcher runs in the simulator checkout, which may differ from the
    # server's working directory. Persist and pass the same paths to both.
    return path.resolve()


@contextmanager
def _launch_lock(base: Path):
    if fcntl is None:
        raise RuntimeError("managed simulator sessions require Linux")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (base / ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _process_identity(pid: int) -> tuple[str, int] | None:
    """Linux state and start tick, to avoid signalling a reused PID."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return data[0], int(data[19])
    except (OSError, IndexError, ValueError):
        return None


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError as exc:
        raise RuntimeError("cannot verify the system boot identity") from exc


def _alive(session: dict) -> bool:
    if session.get("boot_id") != _boot_id():
        return False
    identity = _process_identity(session.get("pid", 0))
    return bool(identity and identity[0] != "Z" and identity[1] == session.get("start_ticks"))


def _public(session: dict) -> dict:
    """Keep boot and kernel identity tokens internal to session management."""
    return {key: value for key, value in session.items() if key not in {"boot_id", "start_ticks"}}


def _signal_session(session: dict, stop_signal: signal.Signals) -> str:
    """Signal the verified process through a pidfd so PID reuse cannot redirect it."""
    pid = session["pid"]
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("safe stop requires Linux pidfd support")
    fd = os.pidfd_open(pid, 0)
    try:
        # Check after opening: a recycled PID will fail the stored boot/start identity,
        # while a pidfd for a subsequently exited process cannot refer to its replacement.
        if not _alive(session):
            raise ProcessLookupError("managed process exited or its identity changed")
        if os.getpgid(pid) != pid:
            raise RuntimeError("process group changed; refusing to signal the session")
        try:
            signal.pidfd_send_signal(fd, stop_signal, None, _PIDFD_SIGNAL_PROCESS_GROUP)
            return "process_group"
        except OSError as exc:
            if exc.errno != errno.EINVAL:
                raise
            # Linux before 6.9 cannot signal a process group by pidfd. Do not fall
            # back to killpg, which could target a reused process group ID.
            signal.pidfd_send_signal(fd, stop_signal)
            return "process"
    finally:
        os.close(fd)


def _read(base: Path, session_id: str) -> dict:
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise ValueError("invalid simulator session ID")
    try:
        data = json.loads((base / session_id / "session.json").read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("simulator session not found or its metadata is unreadable") from exc
    if data.get("id") != session_id:
        raise ValueError("simulator session metadata does not match its ID")
    return data


def _sessions(base: Path) -> list[dict]:
    if not base.is_dir():
        return []
    result = []
    for path in base.iterdir():
        if not path.is_dir() or not _SESSION_ID.fullmatch(path.name):
            continue
        try:
            result.append(_read(base, path.name))
        except ValueError:
            continue
    return result


def _lab_pids(env: Mapping[str, str]) -> set[int]:
    """Count running Lab jobs in the configured jobs directory, without reconciling records."""
    configured = env.get("ISAACLAB_JOBS_DIR") or os.environ.get("ISAACLAB_JOBS_DIR")
    base = Path(configured).expanduser() if configured else Path.home() / ".local/share/isaac-mcp/jobs"
    if not base.is_dir():
        return set()
    found = set()
    for path in base.iterdir():
        if not path.is_dir():
            continue
        data = lab_jobs._read_job(path)
        if data and data.get("status") == "running" and lab_jobs._job_alive(data):
            found.add(int(data["pid"]))
    return found


def _domain_of(pid: int) -> int:
    try:
        entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        for entry in entries:
            if entry.startswith(b"ROS_DOMAIN_ID="):
                return int(entry.split(b"=", 1)[1])
        return 0  # ROS 2 default when unset
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot determine ROS domain of bridge process {pid}") from exc


def _resources() -> dict[str, int]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            raise RuntimeError("parallel launch currently requires exactly one NVIDIA GPU")
        total, free = (int(value.strip()) for value in lines[0].split(","))
        meminfo = Path("/proc/meminfo").read_text().splitlines()
        available_kib = next(int(line.split()[1]) for line in meminfo if line.startswith("MemAvailable:"))
        return {"gpu_total_mib": total, "gpu_free_mib": free, "ram_available_mib": available_kib // 1024}
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration) as exc:
        raise RuntimeError("cannot check NVIDIA GPU and system memory; install nvidia-smi and use Linux") from exc


def _check_port(port: int) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))
    except OSError as exc:
        raise ValueError(f"bridge port {port} is already in use or cannot be bound") from exc


def _launch(env: Mapping[str, str], port: int, ros_domain: int, headless: bool,
            allow_shared_domain: bool, ur5e_dt: bool = False) -> dict:
    if not 1024 <= port <= 65535:
        raise ValueError("port must be between 1024 and 65535")
    if not 0 <= ros_domain <= 232:
        raise ValueError("ros_domain must be between 0 and 232")
    root = Path(env.get("ISAAC_MCP_SIM_ROOT", str(_DEFAULT_ROOT))).expanduser().resolve()
    launcher = root / "run.sh"
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise RuntimeError("Isaac Sim launcher not found; set ISAAC_MCP_SIM_ROOT to the repository root")
    min_gpu = _config_int(env, "ISAAC_MCP_SIM_MIN_FREE_VRAM_MIB", 8192)
    min_ram = _config_int(env, "ISAAC_MCP_SIM_MIN_FREE_RAM_MIB", 4096)
    max_instances = _config_int(env, "ISAAC_MCP_SIM_MAX_INSTANCES", 2)
    boot_id = _boot_id()
    base = _sessions_dir(env)
    with _launch_lock(base):
        active = [item for item in _sessions(base) if _alive(item)]
        bridges = client.discover_bridges()
        lab_pids = _lab_pids(env)
        existing_pids = ({int(b["pid"]) for b in bridges} | {int(s["pid"]) for s in active}
                         | lab_pids)
        if max_instances and len(existing_pids) >= max_instances:
            raise RuntimeError(f"{len(existing_pids)} known simulator/Lab process(es) are already active; "
                               f"ISAAC_MCP_SIM_MAX_INSTANCES is {max_instances}")
        if any(item["port"] == port for item in active):
            raise ValueError(f"port {port} is reserved by a starting or running managed session")
        _check_port(port)
        if not allow_shared_domain:
            if any(item["ros_domain"] == ros_domain for item in active):
                raise ValueError(f"ROS domain {ros_domain} is already used by a managed session")
            for bridge in bridges:
                if _domain_of(int(bridge["pid"])) == ros_domain:
                    raise ValueError(f"ROS domain {ros_domain} is already used by bridge PID {bridge['pid']}")
            for pid in lab_pids:
                if _domain_of(pid) == ros_domain:
                    raise ValueError(f"ROS domain {ros_domain} is already used by Lab job PID {pid}")
        resources = _resources()
        if resources["gpu_free_mib"] < min_gpu or resources["ram_available_mib"] < min_ram:
            raise RuntimeError(f"insufficient headroom: {resources}; require at least "
                               f"{min_gpu} MiB GPU and {min_ram} MiB system RAM free")
        session_id = uuid.uuid4().hex
        directory = base / session_id
        directory.mkdir(mode=0o700)
        log = directory / "sim.log"
        output = directory / "output"
        output.mkdir(mode=0o700)
        child_env = os.environ.copy()
        for key in ("PYTHONPATH", "UR5E_DT_HEADLESS", "UR5E_DT_PYTHON", "ISAAC_MCP_HOLD_TOKEN",
                    "ISAAC_MCP_HOLD_TOKEN_FILE"):
            child_env.pop(key, None)
        child_env.update({"UR5E_DT_PORT": str(port), "ISAAC_MCP_PORT": str(port),
                          "ISAAC_MCP_BIND": "127.0.0.1", "ROS_DOMAIN_ID": str(ros_domain),
                          "MCP_CLIENT_OUTPUT_DIR": str(output)})
        if env.get("UR5E_DT_PYTHON"):
            child_env["UR5E_DT_PYTHON"] = env["UR5E_DT_PYTHON"]
        user_config = directory / "user.config.json"
        args = ([str(launcher), "--port", str(port)] + (["--headless"] if headless else [])
                + (["--ur5e-dt"] if ur5e_dt else [])
                + ["--", f"--/app/userConfigPath={user_config}"])
        with log.open("wb") as stream:
            proc = subprocess.Popen(args, cwd=str(root), env=child_env, stdin=subprocess.DEVNULL,
                                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        identity = _process_identity(proc.pid)
        if not identity:
            # A failed /proc read does not prove the child has exited. Never wait
            # indefinitely or leave an untracked simulator running.
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            raise RuntimeError(f"could not record simulator process identity; stopped PID {proc.pid}; see {log}")
        record = {"id": session_id, "pid": proc.pid, "start_ticks": identity[1], "boot_id": boot_id, "port": port,
                  "ros_domain": ros_domain, "headless": headless, "ur5e_dt_on_start": ur5e_dt,
                  "started_at": time.time(),
                  "log": str(log), "output_dir": str(output), "user_config": str(user_config),
                  "initial_resources": resources}
        try:
            (directory / "session.json").write_text(json.dumps(record, indent=2))
        except OSError as exc:
            # Do not leave an untracked process if persisting its identity fails.
            try:
                _signal_session(record, signal.SIGKILL)
            except (OSError, RuntimeError) as cleanup:
                raise RuntimeError(f"could not save session metadata or stop PID {proc.pid}; "
                                   f"inspect {log}: {cleanup}") from exc
            raise
        threading.Thread(target=proc.wait, daemon=True).start()
        return {**_public(record), "state": "starting", "note": "Use sim__status to confirm bridge readiness."}


def _status(env: Mapping[str, str], session_id: str) -> dict:
    session = _read(_sessions_dir(env), session_id)
    if not _alive(session):
        return {**_public(session), "state": "exited"}
    try:
        response = client.call("status", host="127.0.0.1", port=session["port"], timeout=2)
        if response.get("pid") == session["pid"]:
            state = "running"
        else:
            state = "port_conflict"
    except (OSError, RuntimeError, client.BridgeError):
        state = "starting_or_unresponsive"
    try:
        resources = _resources()
    except RuntimeError as exc:
        resources = {"error": str(exc)}
    return {**_public(session), "state": state, "current_resources": resources}


def _stop(env: Mapping[str, str], session_id: str, expected_pid: int, confirm_discard: bool,
          force: bool = False) -> dict:
    if not confirm_discard:
        raise ValueError("confirm_discard must be true; stopping can discard unsaved scene changes")
    base = _sessions_dir(env)
    with _launch_lock(base):
        session = _read(base, session_id)
        if session.get("pid") != expected_pid:
            raise ValueError("expected PID does not match the managed session")
        if not _alive(session):
            return {"id": session_id, "pid": expected_pid, "state": "exited"}
        stop_signal = signal.SIGKILL if force else signal.SIGINT
        try:
            scope = _signal_session(session, stop_signal)
        except ProcessLookupError:
            return {"id": session_id, "pid": expected_pid, "state": "exited"}
    return {"id": session_id, "pid": expected_pid, "state": "stop_requested",
            "signal": stop_signal.name, "scope": scope}


def register_sim_session_tools(server: FastMCP, groups: Iterable[str] | None = None,
                               env: Mapping[str, str] | None = None) -> None:
    mcp = ScopedTools(server, groups)
    settings = dict(os.environ if env is None else env)

    @mcp.tool(name="sim__launch", annotations=PYTHON_EXECUTION)
    def sim_launch(port: int, ros_domain: int, headless: bool = False,
                   allow_shared_domain: bool = False, ur5e_dt: bool = False) -> dict:
        """Launch a managed Isaac Sim instance using a distinct local port and ROS domain.

        Checks a configurable instance limit, port, known ROS domains and current free resources.
        Set ur5e_dt=true to start the UR5e extension and its tools. Resource checks are
        snapshots, not reservations. Launching runs configured local code.
        """
        return _launch(settings, port, ros_domain, headless, allow_shared_domain, ur5e_dt)

    @mcp.tool(name="sim__list", annotations=PYTHON_EXECUTION)
    def sim_list() -> dict:
        """List managed simulator sessions, including those started before this server restarted."""
        return {"sessions": [{**_public(session), "state": "active" if _alive(session) else "exited"}
                             for session in _sessions(_sessions_dir(settings))]}

    @mcp.tool(name="sim__status", annotations=PYTHON_EXECUTION)
    def sim_status(session_id: str) -> dict:
        """Report one managed simulator's process, bridge readiness and current resources."""
        return _status(settings, session_id)

    @mcp.tool(name="sim__stop", annotations=PYTHON_EXECUTION)
    def sim_stop(session_id: str, expected_pid: int, confirm_discard: bool = False,
                 force: bool = False) -> dict:
        """Stop only the identified managed process; unsaved changes can be lost.

        Use force=true only if normal shutdown failed: it sends SIGKILL to the process group.
        """
        return _stop(settings, session_id, expected_pid, confirm_discard, force)

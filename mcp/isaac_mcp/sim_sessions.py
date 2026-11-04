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
from typing import Annotated, Callable, Collection, Literal, Iterable, Mapping

from pydantic import Field

try:
    import fcntl
except ImportError:  # Session launch is Linux-only; do not break unrelated host tools.
    fcntl = None

from mcp.server.fastmcp import FastMCP

from . import client
from .isaaclab import jobs as lab_jobs
from .local_tools import PYTHON_EXECUTION, ScopedTools
from .sim_versions import NEWTON_STACKS

_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
# An advertised bridge this server did not launch. Its ID is built from the listing, never stored.
_EXTERNAL_ID = re.compile(r"^external-(\d+)-(\d+)$")
_DEFAULT_ROOT = Path(__file__).resolve().parents[2]
# Linux 6.9 added a pidfd signal scope for an entire process group. Older
# kernels get a race-safe signal to the leader only.
_PIDFD_SIGNAL_PROCESS_GROUP = 1 << 2
# How far above a refused bridge port to look for a free one to suggest.
_PORT_SEARCH_SPAN = 64
# sim__list shows this many exited sessions by default, newest first.
_RECENT_EXITED_LISTED = 3


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


def _port_bind_error(port: int) -> OSError | None:
    """Try the bridge's own bind sequence (SO_REUSEADDR, bind, listen; bridge.py) on the managed
    bind address, and return what stopped it.

    A plain bind is the wrong test: after the previous bridge's process exits, its closed
    connections keep the port in TIME_WAIT for about a minute, a plain bind fails with EADDRINUSE,
    and yet the bridge could bind it. SO_REUSEADDR does not get past a socket in LISTEN."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            sock.listen(1)
    except OSError as exc:
        return exc
    return None


def _next_free_port_hint(port: int, reserved: Collection[int] = ()) -> str:
    """Suggest the next port up that a bridge could bind right now (a snapshot, not a reservation)."""
    for candidate in range(port + 1, min(port + _PORT_SEARCH_SPAN, 65535) + 1):
        if candidate not in reserved and _port_bind_error(candidate) is None:
            return f" Port {candidate} is free right now: retry with port={candidate}."
    return f" No free port found in {port + 1}-{min(port + _PORT_SEARCH_SPAN, 65535)}; choose another port."


def _check_port(port: int, reserved: Collection[int] = ()) -> None:
    error = _port_bind_error(port)
    if error is not None:
        raise ValueError(f"bridge port {port} is already in use or cannot be bound ({error}). "
                         "A port that only has recently closed bridge connections does not cause "
                         "this; another process holds it, or holds connections closed without "
                         "SO_REUSEADDR (those clear within about a minute)."
                         + _next_free_port_hint(port, reserved)) from error


def _domain_refusal(ros_domain: int, holder: str) -> ValueError:
    """The ROS domain guard's refusal, with the ways to proceed."""
    return ValueError(f"ROS domain {ros_domain} is already used by {holder}. To proceed, pass another "
                      "ros_domain and export the same ROS_DOMAIN_ID in every ROS shell that talks to "
                      "this simulator, or pass allow_shared_domain=true if sharing the domain is "
                      "intended. Lab instructions that assume domain 0 need adapting to the domain "
                      "you choose.")


def _configured_launcher(env: Mapping[str, str]) -> Path | None:
    """The launcher named by ISAAC_MCP_SIM_LAUNCHER, or None for this repository's run.sh.

    It comes only from the server's environment, which the MCP client configuration sets. A tool
    argument or a project file could be written by the model (put_file has no path limits), and a
    launcher is an arbitrary command. It is checked again before each launch. A launcher gets the
    same arguments as run.sh and must exec Kit, so that the process it started is the bridge."""
    value = (env.get("ISAAC_MCP_SIM_LAUNCHER") or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"ISAAC_MCP_SIM_LAUNCHER must be an absolute path, not {value!r}")
    try:
        info = path.stat()
    except OSError as exc:
        raise RuntimeError(f"ISAAC_MCP_SIM_LAUNCHER {value!r} cannot be read: {exc}") from exc
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RuntimeError(f"ISAAC_MCP_SIM_LAUNCHER {value!r} is not an executable file")
    if info.st_uid not in (os.getuid(), 0) or info.st_mode & 0o022:
        raise RuntimeError(f"ISAAC_MCP_SIM_LAUNCHER {value!r} must belong to you (or root) and not be "
                           "writable by group or others")
    return path


def _interpreter_release(env: Mapping[str, str], root: Path) -> str | None:
    """The Isaac Sim release of the interpreter run.sh will use, read from its package metadata without
    running it. None when it cannot be told, e.g. when run.sh has to auto-detect or for a standalone install."""
    named = env.get("ISAAC_MCP_PYTHON") or env.get("UR5E_DT_PYTHON")
    python = Path(named).expanduser() if named else root / ".venv" / "bin" / "python"
    for prefix in (python.parent.parent, python.resolve().parent.parent):
        found = sorted(prefix.glob("lib/python3.*/site-packages/isaacsim-*.dist-info"))
        if found:
            return found[-1].name[len("isaacsim-"):-len(".dist-info")]
    return None


def _launch(env: Mapping[str, str], port: int, ros_domain: int, headless: bool,
            allow_shared_domain: bool, show_ur5e_panel: bool = False,
            enable_motion_bvh: bool = False, physics_backend: Literal["physx", "newton"] = "physx") -> dict:
    if physics_backend not in ("physx", "newton"):
        raise ValueError("physics_backend must be physx or newton")
    if not 1024 <= port <= 65535:
        raise ValueError("port must be between 1024 and 65535")
    # The bundled Cyclone profile probes participant indices 0..100. At
    # domain 232 those discovery ports extend beyond UDP's 65535 limit.
    if not 0 <= ros_domain <= 231:
        raise ValueError("ros_domain must be between 0 and 231 for the bundled Cyclone DDS profile")
    custom = _configured_launcher(env)
    if custom is not None:
        launcher, root = custom, custom.parent
    else:
        root = Path(env.get("ISAAC_MCP_SIM_ROOT", str(_DEFAULT_ROOT))).expanduser().resolve()
        launcher = root / "run.sh"
        if not launcher.is_file() or not os.access(launcher, os.X_OK):
            raise RuntimeError("Isaac Sim launcher not found; set ISAAC_MCP_SIM_ROOT to the repository root")
        release = _interpreter_release(env, root)
        if physics_backend == "newton" and release and release not in NEWTON_STACKS:
            raise ValueError(f"Isaac Sim {release} has no Newton backend here (the selected interpreter's package "
                             "metadata; see docs/COMPATIBILITY.md). Nothing was started and there is no fallback: "
                             "use physics_backend=physx, or a 6.x interpreter for the Newton workflow.")
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
        reserved = {int(item["port"]) for item in active} | {int(b["port"]) for b in bridges}
        if any(item["port"] == port for item in active):
            raise ValueError(f"port {port} is reserved by a starting or running managed session."
                             + _next_free_port_hint(port, reserved))
        _check_port(port, reserved)
        if not allow_shared_domain:
            if any(item["ros_domain"] == ros_domain for item in active):
                raise _domain_refusal(ros_domain, "a managed session")
            for bridge in bridges:
                if _domain_of(int(bridge["pid"])) == ros_domain:
                    raise _domain_refusal(ros_domain, f"bridge PID {bridge['pid']}")
            for pid in lab_pids:
                if _domain_of(pid) == ros_domain:
                    raise _domain_refusal(ros_domain, f"Lab job PID {pid}")
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
        for key in ("PYTHONPATH", "UR5E_DT_HEADLESS", "UR5E_DT_PYTHON", "ISAAC_MCP_PYTHON",
                    "ISAAC_MCP_WORKSPACE", "UR5E_DT_SHOW_PANEL", "ISAAC_MCP_HOLD_TOKEN",
                    "ISAAC_MCP_HOLD_TOKEN_FILE"):
            child_env.pop(key, None)
        child_env.update({"UR5E_DT_PORT": str(port), "ISAAC_MCP_PORT": str(port),
                          "ISAAC_MCP_BIND": "127.0.0.1", "ROS_DOMAIN_ID": str(ros_domain),
                          "ISAAC_MCP_PHYSICS_BACKEND": physics_backend,
                          "MCP_CLIENT_OUTPUT_DIR": str(output),
                          "ISAAC_MCP_SIM_SESSION_ID": session_id,
                          "ISAAC_MCP_SIM_SESSION_DIR": str(directory)})
        for key in ("UR5E_DT_PYTHON", "ISAAC_MCP_PYTHON", "ISAAC_MCP_WORKSPACE"):
            if env.get(key):
                child_env[key] = env[key]
        user_config = directory / "user.config.json"
        args = ([str(launcher), "--port", str(port)] + (["--headless"] if headless else [])
                + (["--ur5e-panel"] if show_ur5e_panel else [])
                + (["--motion-bvh"] if enable_motion_bvh else [])
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
                  "ros_domain": ros_domain, "headless": headless,
                  "show_ur5e_panel_on_start": show_ur5e_panel,
                  "enable_motion_bvh": enable_motion_bvh,
                  "physics_backend": physics_backend,
                  **({"launcher": str(custom)} if custom is not None else {}),
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
        return {**_public(record), "managed": True, "state": "starting",
                # Inherited, and deliberately not an argument: a pack in a store imports without approval.
                "asset_stores": os.environ.get("ISAAC_MCP_ASSET_PATH") or "default; set ISAAC_MCP_ASSET_PATH in the MCP server's environment",
                "note": (f"Use sim__status to confirm bridge readiness, then use_bridge(port={port}) "
                         "to send tool calls to this simulator. If this is the only managed session "
                         "and the configured port does not answer, refresh_tools(wait_s=120) waits "
                         "for this session and selects it instead.")}


def _external_id(bridge: Mapping) -> str:
    return f"external-{int(bridge['pid'])}-{int(bridge['port'])}"


def _external_bridges(sessions: Iterable[dict]) -> list[dict]:
    """Advertised bridges no live managed session accounts for: something else launched them.
    This reads their portfiles and only tests that the port accepts a connection."""
    managed = {int(item["pid"]) for item in sessions if _alive(item)}
    return [{"id": _external_id(bridge), "managed": False, "pid": int(bridge["pid"]), "port": int(bridge["port"]),
             "host": bridge.get("host"), "app": bridge.get("app"), "app_version": bridge.get("app_version"),
             "headless": bridge.get("headless"), "started_at": bridge.get("started"), "state": "active"}
            for bridge in client.discover_bridges() if int(bridge["pid"]) not in managed]


def _bridge_state(host: str | None, port: int, pid: int) -> tuple[str, dict]:
    """Readiness from the bridge's own ``status``, checked against the PID expected on that port."""
    try:
        response = client.call("status", host=host or "127.0.0.1", port=port, timeout=2)
    except (OSError, RuntimeError, client.BridgeError):
        return "starting_or_unresponsive", {}
    if response.get("pid") != pid:
        return "port_conflict", response
    if response.get("app_ready") is True:
        return "running", response
    # Older or non-Kit bridges cannot attest to application readiness.
    return ("starting" if response.get("app_ready") is False else "readiness_unknown"), response


def _external_status(session_id: str) -> dict:
    match = _EXTERNAL_ID.fullmatch(session_id)
    pid, port = int(match.group(1)), int(match.group(2))
    bridge = next((b for b in client.discover_bridges() if _external_id(b) == session_id), None)
    if bridge is None:
        raise ValueError(f"no advertised bridge has PID {pid} and port {port}; it may have exited or restarted. "
                         "Call sim__list.")
    state, response = _bridge_state(bridge.get("host"), port, pid)
    result = {"id": session_id, "managed": False, "pid": pid, "port": port, "host": bridge.get("host"),
              "app": response.get("app") or bridge.get("app"),
              "app_version": response.get("app_version") or bridge.get("app_version"),
              "headless": bridge.get("headless"), "started_at": bridge.get("started"), "state": state,
              "stop": "sim__stop does not stop this: it was started outside isaac-mcp, so stop it where it was started."}
    if state in ("running", "readiness_unknown"):
        result["next"] = f"use_bridge(port={port}) sends tool calls to this simulator, after you confirm it is the one you mean."
    return result


def _status(env: Mapping[str, str], session_id: str) -> dict:
    if isinstance(session_id, str) and _EXTERNAL_ID.fullmatch(session_id):
        return _external_status(session_id)
    session = _read(_sessions_dir(env), session_id)
    if not _alive(session):
        return {**_public(session), "managed": True, "state": "exited"}
    state, _ = _bridge_state("127.0.0.1", session["port"], session["pid"])
    try:
        resources = _resources()
    except RuntimeError as exc:
        resources = {"error": str(exc)}
    result = {**_public(session), "managed": True, "state": state, "current_resources": resources}
    if state == "port_conflict" and session.get("launcher"):
        result["note"] = ("Another PID answers this session's port. A custom launcher must exec Kit rather than "
                          "fork it, or the process recorded here is not the simulator.")
    if state in ("running", "readiness_unknown"):
        # The MCP server keeps dialling its configured port (UR5E_DT_PORT/ISAAC_MCP_PORT) while it
        # answers, and refresh_tools then does not switch: a student run on a lab PC lost time here.
        result["next"] = (f"if this MCP server is not already using port {session['port']}, call "
                          f"use_bridge(port={session['port']}) so sim__* and run_python go to this simulator; "
                          "refresh_tools alone stays on the configured port while that port answers.")
    return result


def _stop(env: Mapping[str, str], session_id: str, expected_pid: int, confirm_discard: bool,
          force: bool = False) -> dict:
    if isinstance(session_id, str) and _EXTERNAL_ID.fullmatch(session_id):
        raise ValueError(f"{session_id} is not a managed session: isaac-mcp did not start it, so sim__stop will not "
                         "stop it. Stop it where it was started.")
    if not confirm_discard:
        force_arg = ", force=true" if force else ""
        raise ValueError(f"Stopping can discard unsaved scene changes. After saving, retry: "
                         f"sim__stop(session_id={session_id!r}, expected_pid={expected_pid!r}, confirm_discard=true{force_arg}).")
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
                               env: Mapping[str, str] | None = None,
                               on_launch: Callable[[dict], None] | None = None) -> None:
    """``on_launch`` receives each record sim__launch creates, so the host can tell the sessions it
    launched from others in the shared session directory."""
    mcp = ScopedTools(server, groups)
    settings = dict(os.environ if env is None else env)

    @mcp.tool(name="sim__launch", annotations=PYTHON_EXECUTION)
    def sim_launch(port: Annotated[int, Field(description="A free local TCP port for its bridge, e.g. 18766.")],
                   ros_domain: Annotated[int, Field(description="ROS_DOMAIN_ID 0-231, distinct from other sessions.")],
                   headless: Annotated[bool, Field(description="true: no window.")] = False,
                   allow_shared_domain: Annotated[bool, Field(description="true: allow a ROS domain another session already uses.")] = False,
                   show_ur5e_panel: Annotated[bool, Field(description="true: show the UR5e twin's panel in a graphical session.")] = False,
                   enable_motion_bvh: Annotated[bool, Field(description="true: accurate RTX LiDAR motion compensation; costs rendering time and GPU memory.")] = False,
                   physics_backend: Annotated[Literal["physx", "newton"], Field(description="Engine for this new process. Missing Newton dependencies fail with no fallback.")] = "physx") -> dict:
        """Launch a separate managed Isaac Sim instance on its own port and ROS domain, when explicitly asked.

        Checks an instance limit, the port, known ROS domains and free resources; these are snapshots,
        not reservations. Returns before the bridge is ready: poll sim__status, then use_bridge. Stop a
        fake ROS driver before stopping the session; URSim is unavailable here.
        Launching runs configured local code: run.sh, or the launcher in the server's
        ISAAC_MCP_SIM_LAUNCHER setting. No tool argument can name a command.
        """
        record = _launch(settings, port, ros_domain, headless, allow_shared_domain, show_ur5e_panel, enable_motion_bvh, physics_backend)
        if on_launch is not None:
            on_launch(record)
        return record

    @mcp.tool(name="sim__list", annotations=PYTHON_EXECUTION)
    def sim_list(include_exited: Annotated[bool, Field(description="List every exited session, not only the most recent few.")] = False) -> dict:
        """List simulators: managed sessions (managed=true), including those started before this server
        restarted, and advertised bridges started some other way (managed=false; PID, port, app, version).

        Use each entry's id with sim__status. sim__stop refuses managed=false entries.
        Exited sessions are kept on disk with their logs, and only the most recent few are
        listed unless include_exited=true.
        """
        sessions = _sessions(_sessions_dir(settings))
        live = [session for session in sessions if _alive(session)]
        exited = sorted((session for session in sessions if not _alive(session)),
                        key=lambda session: session.get("started_at") or 0, reverse=True)
        shown = exited if include_exited else exited[:_RECENT_EXITED_LISTED]
        result = {"sessions": ([{**_public(session), "managed": True, "state": "active"} for session in live]
                               + [{**_public(session), "managed": True, "state": "exited"} for session in shown]
                               + _external_bridges(sessions))}
        if len(shown) < len(exited):
            result["exited_omitted"] = len(exited) - len(shown)
            result["note"] = (f"{result['exited_omitted']} older exited session(s) not listed; "
                              "include_exited=true lists them. Their records and logs stay on disk.")
        return result

    @mcp.tool(name="sim__status", annotations=PYTHON_EXECUTION)
    def sim_status(session_id: Annotated[str, Field(description="id from sim__list or sim__launch.")]) -> dict:
        """Report a simulator's process and Kit readiness, by an id from sim__list.

        A managed session also reports current resources. An external one (id external-<pid>-<port>)
        reports its live PID, port, app and version; the PID must match its advertisement.
        """
        return _status(settings, session_id)

    @mcp.tool(name="sim__stop", annotations=PYTHON_EXECUTION)
    def sim_stop(session_id: Annotated[str, Field(description="id from sim__list or sim__launch.")],
                 expected_pid: Annotated[int, Field(description="PID from sim__status; a different process is refused.")],
                 confirm_discard: Annotated[bool, Field(description="true: accept losing unsaved changes.")] = False,
                 force: Annotated[bool, Field(description="true: SIGKILL its process group; only if normal shutdown failed.")] = False) -> dict:
        """Stop only the identified managed process; unsaved changes can be lost. Refuses external bridges.

        Use force=true only if normal shutdown failed: it sends SIGKILL to the process group.
        """
        return _stop(settings, session_id, expected_pid, confirm_discard, force)


def live_sessions(env: Mapping[str, str] | None = None) -> list[dict]:
    """``{id, port, pid, started_at}`` of managed simulator sessions that are still alive (best effort)."""
    try:
        return sorted(({"id": x.get("id"), "port": int(x["port"]), "pid": int(x["pid"]),
                        "started_at": float(x["started_at"]) if x.get("started_at") is not None else None}
                       for x in _sessions(_sessions_dir(os.environ if env is None else env)) if _alive(x)),
                      key=lambda x: (x["port"], x["pid"]))
    except Exception:  # noqa: BLE001 - only used to enrich an error message
        return []


def live_ports(env: Mapping[str, str] | None = None) -> list[tuple[int, int]]:
    """``(port, pid)`` of managed simulator sessions that are still alive (best effort)."""
    try:
        return sorted((int(x["port"]), int(x["pid"])) for x in _sessions(_sessions_dir(os.environ if env is None else env))
                      if _alive(x))
    except Exception:  # noqa: BLE001 - only used to enrich an error message
        return []

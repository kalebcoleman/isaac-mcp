"""Isaac Lab job (subprocess) management.

This module owns the job model for long-running Isaac Lab train/eval/play jobs.
It is deliberately free of FastMCP imports; the parent server wires these
functions as MCP tools later. It is pure stdlib Python (>=3.10).

Chosen Isaac Lab entry scripts and flags (relative to the resolved
``isaaclab_root``):
  * train:  scripts/reinforcement_learning/train.py
      flags: --task, --num_envs, --max_iterations, --seed, --checkpoint,
             --device (launcher), --headless (launcher), plus validated extra_args
  * eval:   scripts/reinforcement_learning/play.py  (Isaac Lab has no separate
            eval entrypoint; play.py with --checkpoint is the eval path)
      flags: --task, --checkpoint, --num_envs, --rl_library, --viz none|kit (no --headless flag in Isaac Lab 3; picks the RL backend with --rl_library, else the task default; no per-backend --num_episodes)
  * play:   scripts/reinforcement_learning/play.py
      flags: --task, --checkpoint, --num_envs, --headless (only when true)

Jobs are launched with ``shell=False`` and an explicit argv list, with
``start_new_session=True`` so the whole process group can be signalled.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import install as _install

_LAUNCHER = Path(__file__).with_name("launcher.py")
_TASK_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_JOB_ID_RE = re.compile(r"^\d{8}-\d{6}-[A-Za-z0-9_.-]+$")
_LIB_RE = re.compile(r"^[a-z0-9_]+$")
_EXTRA_ARG_RE = re.compile(r"^--[A-Za-z0-9_.-]+(=[^\s;|&$`]*)?$")
# Isaac Lab 3 takes Hydra overrides (key.sub=value, +key=value, ~key) alongside --flags.
_HYDRA_ARG_RE = re.compile(r"^(~[A-Za-z0-9_.@/-]+|[+]{0,2}[A-Za-z0-9_.@/-]+=[^\s;|&$`'\"]*)$")
_CHECKPOINT_EXTS = ("*.pth", "*.pt")

_jobs_lock = threading.Lock()
# Held from the guard check until the new job is registered, so two launches in this process cannot both pass the guard.
_launch_lock = threading.RLock()
_active: dict[str, subprocess.Popen] = {}


def _jobs_path() -> Path:
    base = os.environ.get("ISAACLAB_JOBS_DIR")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".local" / "share" / "isaac-mcp" / "jobs"


def jobs_dir() -> Path:
    p = _jobs_path()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> float:
    return time.time()


def _slug(value: str) -> str:
    out = []
    for ch in value:
        if ch.isalnum() or ch in "._-":
            out.append(ch)
        else:
            out.append("-")
    s = "".join(out).strip("-")
    return s or "job"


def _job_id(task: str) -> str:
    """Unique id: second-resolution timestamp + task slug, with -2, -3, ... if that directory already exists."""
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    base = f"{ts}-{_slug(task)}"
    jid, n = base, 2
    while (jobs_dir() / jid).exists():
        jid = f"{base}-{n}"
        n += 1
    return jid


def _validate_task(task: str) -> None:
    if not isinstance(task, str) or not _TASK_RE.match(task):
        raise ValueError(f"invalid task id: {task!r}")


def _validate_extra_args(extra_args) -> list[str]:
    if extra_args is None:
        return []
    if not isinstance(extra_args, (list, tuple)):
        raise ValueError("extra_args must be a list of strings")
    out = []
    for a in extra_args:
        if not isinstance(a, str) or not (_EXTRA_ARG_RE.match(a) or (not a.startswith("-") and _HYDRA_ARG_RE.match(a))):
            raise ValueError(f"invalid extra_arg: {a!r}")
        out.append(a)
    return list(out)


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _validate_checkpoint(checkpoint: Any, root: Any) -> str | None:
    if checkpoint is None:
        return None
    if not isinstance(checkpoint, str) or not checkpoint:
        raise ValueError("checkpoint must be a non-empty path string")
    p = Path(checkpoint).expanduser()
    if not p.exists():
        raise ValueError(f"checkpoint does not exist: {checkpoint}")
    rp = p.resolve()
    allowed = [Path(root).resolve(), jobs_dir().resolve()]
    if not any(_under(rp, r) for r in allowed):
        raise ValueError(f"checkpoint resolves outside allowed roots: {checkpoint}")
    return str(rp)


def _child_env(root: Any, device: str | None) -> dict[str, str]:
    env = os.environ.copy()
    env["OMNI_KIT_ACCEPT_EULA"] = "YES"
    env["PYTHONUNBUFFERED"] = "1"
    if device and isinstance(device, str) and device.lower() != "cpu":
        dev = device
        if ":" in dev:
            dev = dev.split(":")[-1]
        env["CUDA_VISIBLE_DEVICES"] = dev
    # ensure root importable
    rp = str(Path(root).resolve())
    pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = rp + (os.pathsep + pp if pp else "")
    return env


def _read_job(job_dir: Path) -> dict[str, Any] | None:
    jf = job_dir / "job.json"
    if not jf.exists():
        return None
    try:
        return json.loads(jf.read_text())
    except Exception:
        return None


def _write_job(job_dir: Path, data: dict[str, Any]) -> None:
    (job_dir / "job.json").write_text(json.dumps(data, indent=2, default=str))


def _update_job(job_dir: Path, **fields: Any) -> dict[str, Any]:
    data = _read_job(job_dir) or {}
    data.update(fields)
    _write_job(job_dir, data)
    return data


def _job_path(job_id: Any) -> Path:
    """Directory of an existing job; rejects ids that could escape the jobs dir."""
    if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
        raise ValueError(f"invalid job id: {job_id!r}")
    return jobs_dir() / job_id


def _validate_library(rl_library: Any) -> str | None:
    if rl_library is None:
        return None
    if not isinstance(rl_library, str) or not _LIB_RE.match(rl_library):
        raise ValueError(f"invalid rl_library: {rl_library!r}")
    return rl_library


def _job_alive(data: dict) -> bool:
    """True only if the recorded pid is alive AND still runs this job's script (guards pid reuse
    after a host restart: we must never signal an unrelated process group)."""
    pid = data.get("pid")
    if not _pid_alive(pid):
        return False
    cmd = data.get("command") or []
    marker = cmd[1] if len(cmd) > 1 else None
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            argv = f.read().split(b"\0")
    except OSError:
        return True  # no /proc (non-Linux): fall back to the plain liveness check
    return marker is None or marker.encode() in argv


def _orphaned(job_id: str, data: dict) -> bool:
    """A 'running' record whose process is gone and that no watcher in this process still owns.
    While the watcher owns a job it records the real exit code itself; marking it failed here first
    would report a finished job as failed ("host restarted") with exit_code None."""
    return data.get("status") == "running" and job_id not in _active and not _job_alive(data)


def _running_job() -> dict[str, Any] | None:
    """Another job still running. A job a watcher here owns is running until its process has exited
    (its record is finalized a moment later); any other 'running' record counts only if its pid is
    alive. A stale record (dead pid, no watcher) is not running and must not block a new launch."""
    for jd in sorted(jobs_dir().iterdir()):
        if not jd.is_dir():
            continue
        data = _read_job(jd)
        if not data or data.get("status") != "running":
            continue
        proc = _active.get(jd.name)
        if proc is not None:
            if proc.poll() is None:
                return data
        elif _job_alive(data):
            return data
    return None


def _concurrency_guard() -> dict[str, Any] | None:
    """Refuse to start a second job: one 16 GB GPU, so two jobs contend and OOM."""
    other = _running_job()
    if other is None:
        return None
    jid = other.get("job_id") or other.get("id")
    pid = other.get("pid")
    return {
        "error": f"another Isaac Lab job is already running: {jid} (pid {pid})",
        "running_job_id": jid,
        "running_pid": pid,
        "hint": (
            "wait for it to finish, cancel it with isaaclab__runs_cancel, "
            "or pass allow_concurrent=True to start anyway"
        ),
    }


_DEFAULT_KIT_PORT = 8766
_KIT_PROBE_TIMEOUT_S = 0.3


def _probe_kit_bridges() -> list[dict[str, Any]]:
    """Kit bridges (a running Isaac Sim app) reachable now: advertised portfiles plus the configured port.

    The configured port is $UR5E_DT_PORT, else $ISAAC_MCP_PORT, else 8766; "0" or an unparsable value
    means "no fixed port" and only advertised bridges count. Each hit is {"host", "port"}.
    """
    import socket

    from isaac_mcp import client

    def key(h: str, p: int) -> tuple[str, int]:
        return ("127.0.0.1" if h in ("localhost", "::1") else h, p)

    found: dict[tuple[str, int], dict[str, Any]] = {}
    for info in client.discover_bridges():
        host = info.get("host") or client.DEFAULT_HOST
        found.setdefault(key(host, int(info["port"])), {"host": host, "port": int(info["port"])})
    raw = os.getenv(client.TWIN_PORT_ENV) or os.getenv(client.PORT_ENV)
    try:
        port = int(raw) if raw else _DEFAULT_KIT_PORT
    except ValueError:
        port = 0
    host = os.getenv(client.HOST_ENV) or client.DEFAULT_HOST
    if port > 0 and key(host, port) not in found:
        try:
            socket.create_connection((host, port), timeout=_KIT_PROBE_TIMEOUT_S).close()
            found[key(host, port)] = {"host": host, "port": port}
        except OSError:
            pass
    return list(found.values())


def _kit_bridge_guard() -> dict[str, Any] | None:
    """Refuse to start a job next to a running Kit app: on one 16 GB GPU the two contend and OOM."""
    bridges = _probe_kit_bridges()
    if not bridges:
        return None
    where = ", ".join(f"{b['host']}:{b['port']}" for b in bridges)
    return {
        "error": f"a Kit bridge (Isaac Sim app) is running at {where}; it shares the GPU with this job",
        "kit_bridges": bridges,
        "hint": (
            "stop the Isaac Sim app first, or pass allow_concurrent_gpu=True to start anyway"
        ),
    }


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError, ValueError):
        return False


def _latest_checkpoint(run_dir: Any) -> str | None:
    if not run_dir:
        return None
    rd = Path(run_dir)
    if not rd.exists():
        return None
    candidates = []
    for pat in _CHECKPOINT_EXTS:
        candidates.extend(rd.rglob(pat))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0].resolve())


_SAVED_CKPT_RE = re.compile(r"saving checkpoint '([^']+)'")
_EXPERIMENT_DIR_RE = re.compile(r"Exact experiment name requested from command line:\s*(/\S+)")


def _run_dir_from_log(log_path: Path) -> str | None:
    """Isaac Lab prints the absolute experiment dir near the top of the log before any checkpoint exists."""
    try:
        with log_path.open("rb") as f:
            head = f.read(131072).decode("utf-8", errors="replace")
    except OSError:
        return None
    m = _EXPERIMENT_DIR_RE.search(head)
    return m.group(1) if m else None


def _checkpoint_from_log(log_path: Path) -> str | None:
    """Last checkpoint the training library announced in the log (e.g. rl_games prints
    "=> saving checkpoint '<path>'"). Isaac Lab writes runs under its own logs/ dir, not the job dir."""
    try:
        size = log_path.stat().st_size
        with log_path.open("rb") as f:
            f.seek(max(0, size - 262144))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    found = _SAVED_CKPT_RE.findall(text)
    for cand in reversed(found):
        if Path(cand).exists():
            return str(Path(cand).resolve())
    return None


def _tail_file(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    try:
        with path.open("r", errors="replace") as f:
            all_lines = f.readlines()
    except Exception:
        return []
    out = [ln.rstrip("\n") for ln in all_lines[-lines:]]
    return out


def _rehydrate() -> None:
    path = _jobs_path()
    if not path.is_dir():
        return
    for jd in path.iterdir():
        if not jd.is_dir():
            continue
        data = _read_job(jd)
        if not data:
            continue
        if data.get("status") == "running":
            pid = data.get("pid")
            if not _pid_alive(pid):
                data["status"] = "failed"
                data["finished_at"] = _now()
                if data.get("exit_code") is None:
                    data["exit_code"] = None
                data["note"] = "host restarted"
                _write_job(jd, data)


_rehydrate()


def _watcher(job_dir: Path, job_id: str, proc: subprocess.Popen, timeout_s: float | None) -> None:
    status = None
    exit_code = None
    try:
        if timeout_s:
            proc.wait(timeout=timeout_s)
        else:
            proc.wait()
        exit_code = proc.returncode
        status = "succeeded" if exit_code == 0 else "failed"
    except subprocess.TimeoutExpired:
        # kill process group
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        exit_code = proc.returncode
        status = "timeout"
    except Exception:
        try:
            proc.wait()
        except Exception:
            pass
        exit_code = getattr(proc, "returncode", None)
        status = "failed"
    with _jobs_lock:
        cur = _read_job(job_dir) or {}
        if cur.get("status") == "cancelled":
            # runs_cancel already recorded the outcome; do not turn it into "failed (-15)"
            _update_job(job_dir, exit_code=exit_code)
        else:
            _update_job(job_dir, status=status, finished_at=_now(), exit_code=exit_code)
        _active.pop(job_id, None)


def _start_job(*args: Any, **kwargs: Any) -> dict[str, Any]:
    with _launch_lock:
        return _start_job_locked(*args, **kwargs)


def _start_job_locked(
    python: str,
    root: Any,
    kind: str,
    task: str,
    argv: list[str],
    timeout_s: float | None = None,
    device: str | None = None,
    extra_validated: list[str] | None = None,
    allow_concurrent: bool = False,
    allow_concurrent_gpu: bool = False,
) -> dict[str, Any]:
    _validate_task(task)
    if not allow_concurrent:
        blocked = _concurrency_guard()
        if blocked is not None:
            return blocked
    if not allow_concurrent_gpu:
        blocked = _kit_bridge_guard()
        if blocked is not None:
            return blocked
    if extra_validated is None:
        extra_validated = []
    root_path = Path(root).resolve()
    # script selection
    script_rel = "train.py" if kind == "train" else "play.py"
    script_path = root_path / "scripts" / "reinforcement_learning" / script_rel
    full_cmd = [str(python), str(script_path)] + list(argv)
    task_modules, task_paths = _install.resolve_task_modules(root_path)
    if task_modules:
        # Isaac Lab's stock scripts do not know project tasks: import them first (see launcher.py).
        full_cmd = [str(python), str(_LAUNCHER), str(script_path)] + list(argv)

    job_id = _job_id(task)
    jd = jobs_dir() / job_id
    jd.mkdir(parents=True, exist_ok=True)
    stdout_path = jd / "stdout.log"
    stderr_path = jd / "stderr.log"
    run_dir = jd  # default; training artifacts may live under root/logs but we track job dir

    stdout_f = open(stdout_path, "wb")
    stderr_f = open(stderr_path, "wb")
    env = _child_env(root_path, device)
    if task_modules:
        env["ISAACLAB_TASK_MODULES"] = ",".join(task_modules)
        env["PYTHONPATH"] = os.pathsep.join(task_paths + [env["PYTHONPATH"]])
    proc = subprocess.Popen(
        full_cmd,
        stdout=stdout_f,
        stderr=stderr_f,
        stdin=subprocess.DEVNULL,
        env=env,
        cwd=str(root_path),
        shell=False,
        start_new_session=True,
    )
    # close file handles in parent; child inherited
    stdout_f.close()
    stderr_f.close()

    started = _now()
    data = {
        "id": job_id,
        "job_id": job_id,
        "kind": kind,
        "task": task,
        "command": full_cmd,
        "pid": proc.pid,
        "started_at": started,
        "finished_at": None,
        "status": "running",
        "exit_code": None,
        "log_dir": str(jd.resolve()),
        "run_dir": str(run_dir.resolve()),
        "stdout_log": str(stdout_path.resolve()),
        "stderr_log": str(stderr_path.resolve()),
    }
    _write_job(jd, data)

    with _jobs_lock:
        _active[job_id] = proc

    t = threading.Thread(target=_watcher, args=(jd, job_id, proc, timeout_s), daemon=True)
    t.start()

    return {"job_id": job_id, "log_dir": str(jd.resolve()), "command": full_cmd}


def train(
    python: str,
    root: Any,
    task: str,
    num_envs: int = 64,
    max_iterations: int | None = None,
    seed: int | None = None,
    device: str | None = None,
    headless: bool = True,
    checkpoint: Any = None,
    extra_args: Any = None,
    timeout_s: float | None = None,
    rl_library: str | None = None,
    allow_concurrent: bool = False,
    allow_concurrent_gpu: bool = False,
) -> dict[str, Any]:
    _validate_task(task)
    lib = _validate_library(rl_library)
    ckpt = _validate_checkpoint(checkpoint, root)
    extras = _validate_extra_args(extra_args)
    argv: list[str] = ["--task", task, "--num_envs", str(num_envs)]
    if lib:
        argv += ["--rl_library", lib]
    if max_iterations is not None:
        argv += ["--max_iterations", str(max_iterations)]
    if seed is not None:
        argv += ["--seed", str(seed)]
    if ckpt is not None:
        argv += ["--checkpoint", ckpt]
    if device is not None:
        argv += ["--device", str(device)]
    # Isaac Lab 3 has no --headless flag: select the visualizer explicitly ("none" = headless).
    argv += ["--viz", "none" if headless else "kit"]
    argv += extras
    return _start_job(
        python, root, "train", task, argv, timeout_s=timeout_s, device=device,
        allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
    )


def eval_(
    python: str,
    root: Any,
    task: str,
    checkpoint: Any,
    num_envs: int | None = None,
    headless: bool = True,
    extra_args: Any = None,
    timeout_s: float | None = None,
    rl_library: str | None = None,
    allow_concurrent: bool = False,
    allow_concurrent_gpu: bool = False,
) -> dict[str, Any]:
    _validate_task(task)
    lib = _validate_library(rl_library)
    ckpt = _validate_checkpoint(checkpoint, root)
    if ckpt is None:
        raise ValueError("checkpoint is required for eval")
    extras = _validate_extra_args(extra_args)
    argv: list[str] = ["--task", task, "--checkpoint", ckpt]
    if lib:
        argv += ["--rl_library", lib]
    if num_envs is not None:
        argv += ["--num_envs", str(num_envs)]
    # Isaac Lab 3 has no --headless flag: select the visualizer explicitly ("none" = headless).
    argv += ["--viz", "none" if headless else "kit"]
    argv += extras
    return _start_job(python, root, "eval", task, argv, timeout_s=timeout_s,
                      allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu)


def play(
    python: str,
    root: Any,
    task: str,
    checkpoint: Any,
    num_envs: int = 1,
    headless: bool = False,
    extra_args: Any = None,
    timeout_s: float | None = None,
    rl_library: str | None = None,
    allow_concurrent: bool = False,
    allow_concurrent_gpu: bool = False,
) -> dict[str, Any]:
    _validate_task(task)
    lib = _validate_library(rl_library)
    ckpt = _validate_checkpoint(checkpoint, root)
    if ckpt is None:
        raise ValueError("checkpoint is required for play")
    extras = _validate_extra_args(extra_args)
    argv: list[str] = ["--task", task, "--checkpoint", ckpt, "--num_envs", str(num_envs)]
    if lib:
        argv += ["--rl_library", lib]
    # Isaac Lab 3 has no --headless flag: select the visualizer explicitly ("none" = headless).
    argv += ["--viz", "none" if headless else "kit"]
    argv += extras
    return _start_job(python, root, "play", task, argv, timeout_s=timeout_s,
                      allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu)


def runs_list(state: str | None = None) -> dict[str, Any]:
    runs = []
    for jd in sorted(jobs_dir().iterdir()):
        if not jd.is_dir():
            continue
        data = _read_job(jd)
        if not data:
            continue
        # reconcile dead pids
        if _orphaned(jd.name, data):
            data["status"] = "failed"
            data["finished_at"] = data.get("finished_at") or _now()
            data["note"] = data.get("note") or "host restarted"
            _write_job(jd, data)
        st = data.get("status")
        if state and state != "all":
            if state == "running" and st != "running":
                continue
            if state == "finished" and st == "running":
                continue
        started = data.get("started_at")
        elapsed = (_now() - float(started)) if started else 0.0
        if st != "running" and data.get("finished_at"):
            elapsed = float(data["finished_at"]) - float(started)
        runs.append({
            "job_id": data.get("job_id") or data.get("id") or jd.name,
            "kind": data.get("kind"),
            "task": data.get("task"),
            "state": st,
            "started_at": started,
            "elapsed_s": round(elapsed, 3),
            "log_dir": data.get("log_dir"),
        })
    return {"runs": runs}


def runs_status(job_id: str) -> dict[str, Any]:
    jd = _job_path(job_id)
    data = _read_job(jd)
    if not data:
        raise ValueError(f"unknown job: {job_id}")
    if _orphaned(job_id, data):
        data["status"] = "failed"
        data["finished_at"] = data.get("finished_at") or _now()
        data["note"] = data.get("note") or "host restarted"
        _write_job(jd, data)
    started = data.get("started_at")
    if data.get("status") == "running":
        elapsed = _now() - float(started) if started else 0.0
    else:
        fin = data.get("finished_at")
        elapsed = (float(fin) - float(started)) if (fin and started) else 0.0
    last_lines = _tail_file(Path(data.get("log_dir", jd)) / "stdout.log", 20)
    # fallback if log_dir missing
    if not last_lines:
        last_lines = _tail_file(jd / "stdout.log", 20)
    run_dir = data.get("run_dir")
    ckpt = _latest_checkpoint(run_dir) or _checkpoint_from_log(Path(data.get("log_dir", jd)) / "stdout.log")
    if not ckpt:
        logged = _run_dir_from_log(Path(data.get("log_dir", jd)) / "stdout.log")
        if logged and Path(logged).exists():
            run_dir = logged
    if ckpt and not _latest_checkpoint(run_dir):
        # the experiment dir (parent of nn/ for rl_games) is the run dir tools like metrics_summary want
        p = Path(ckpt).parent
        run_dir = str(p.parent if p.name == "nn" else p)
    return {
        "state": data.get("status"),
        "pid_alive": _job_alive(data),
        "elapsed_s": round(elapsed, 3),
        "exit_code": data.get("exit_code"),
        "last_log_lines": last_lines,
        "latest_checkpoint": ckpt,
        "run_dir": run_dir,
        "log_dir": data.get("log_dir", str(jd)),
        "pid": data.get("pid"),
    }


def runs_tail(job_id: str, stream: str = "stdout", lines: int = 100) -> dict[str, Any]:
    if stream not in ("stdout", "stderr"):
        raise ValueError("stream must be 'stdout' or 'stderr'")
    lines = max(1, min(int(lines), 1000))
    jd = _job_path(job_id)
    data = _read_job(jd)
    if not data:
        raise ValueError(f"unknown job: {job_id}")
    log_dir = Path(data.get("log_dir", jd))
    fname = "stdout.log" if stream == "stdout" else "stderr.log"
    path = log_dir / fname
    if not path.exists():
        path = jd / fname
    return {"lines": _tail_file(path, lines)}


def runs_cancel(job_id: str, grace_s: int = 10) -> dict[str, Any]:
    jd = _job_path(job_id)
    data = _read_job(jd)
    if not data:
        raise ValueError(f"unknown job: {job_id}")
    if data.get("status") != "running":
        # already finished (succeeded/failed/cancelled/timeout): leave the record alone
        return {"cancelled": False, "state": data.get("status"), "note": f"job already {data.get('status')}"}
    with _jobs_lock:
        _update_job(jd, status="cancelled", cancel_requested=True, finished_at=_now())
    pid = data.get("pid")
    cancelled = False
    if _job_alive(data):
        try:
            os.killpg(int(pid), signal.SIGTERM)
            cancelled = True
        except ProcessLookupError:
            pass
        # wait grace
        deadline = time.time() + grace_s
        while time.time() < deadline and _job_alive(data):
            time.sleep(0.1)
        if _job_alive(data):
            try:
                os.killpg(int(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
    # update active map
    with _jobs_lock:
        proc = _active.pop(job_id, None)
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
    return {"cancelled": True, "state": "cancelled"}


# expose for tests
__all__ = [
    "jobs_dir",
    "train",
    "eval_",
    "play",
    "runs_list",
    "runs_status",
    "runs_tail",
    "runs_cancel",
]

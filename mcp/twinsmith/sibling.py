"""Pinned sibling checkouts: find one, run its command line, or start it as a job.

A wrapped tool (RoboSimStudio, Graph-as-Policy, VABAR) is a git checkout under ``<workspace>/tools/<name>`` with its own
``.venv``. The host never installs, updates or imports it. It reads the checkout's commit, runs its command line with an
environment that carries only what the wrapper names, and follows long runs as jobs in the shared jobs folder, so
``jobs`` waits for and cancels them like any other.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import threading

from . import pack_manifest

GPU_VARIABLES = ("CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES", "CUDA_HOME",
                 "LD_LIBRARY_PATH")
STDOUT_LIMIT = 1 << 20
TAIL_CHARS = 4000
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")


class Refusal(ValueError):
    """A request the wrapper will not run, with a stable code for the answer."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Checkout:
    """One pinned checkout: ``root`` if given, else ``$<env_var>``, else ``<workspace>/tools/<name>``."""

    def __init__(self, name, env_var, pin, repository, *, workspace=None, root=None):
        self.name, self.env_var, self.pin, self.repository = name, env_var, pin, repository
        self.workspace = Path(workspace or pack_manifest.workspace_dir()).expanduser().resolve()
        configured = root or (os.environ.get(env_var) if env_var else None)
        self.root = Path(configured or self.workspace / "tools" / name).expanduser().resolve()

    def bin(self, program: str = "python") -> Path:
        return self.root / ".venv/bin" / program

    def commit(self) -> str | None:
        try:
            result = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        return (result.stdout.strip() or None) if result.returncode == 0 else None

    def info(self, program: str = "python") -> dict:
        """``{checkout, commit, pin, pin_match, environment_present, warnings}``, plus ``setup`` when it is not usable."""
        commit = self.commit()
        executable = self.bin(program)
        present = executable.is_file() and os.access(executable, os.X_OK)
        info = {"checkout": str(self.root), "commit": commit, "pin": self.pin,
                "pin_match": bool(commit and commit.startswith(self.pin)), "environment_present": present,
                "warnings": []}
        if not info["pin_match"]:
            info["warnings"].append(f"{self.name} checkout commit {commit or 'unknown'} differs from tested pin {self.pin}.")
        if not commit or not present:
            info["setup"] = (f"The owner sets this up; twinsmith never installs it. Clone {self.repository} into {self.root}, "
                             f"check out {self.pin}, and create its own environment so that {executable} exists"
                             + (f" (or point {self.env_var} at an existing checkout)." if self.env_var else "."))
        return info


def run_name(value, prefix: str) -> str:
    """A run folder name: the caller's (letters, digits, underscores, hyphens), else ``<prefix>-<timestamp>``."""
    import time

    if value is None:
        return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}"
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise Refusal("INVALID_VALUE", "name must be 1 to 80 letters, digits, underscores or hyphens.")
    return value


def environment(env_pass=(), env=None) -> dict:
    """``PATH``, ``HOME``, the GPU variables and the names in ``env_pass`` from this process, then ``env``.
    Nothing named ``ROS_*`` passes: a wrapped tool never joins a ROS graph."""
    child = {key: os.environ[key] for key in ("PATH", "HOME", *GPU_VARIABLES, *env_pass) if key in os.environ}
    child.update(env or {})
    return {key: value for key, value in child.items() if not key.startswith("ROS_")}


def _tail(text) -> str:
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    return (text or "")[-TAIL_CHARS:]


def run(argv, *, cwd, env_pass=(), env=None, timeout_s: float = 120) -> dict:
    """Run to completion: ``{exit_code, stdout, stderr}``; stdout up to 1 MiB, the stderr's tail, ``timed_out`` if it was."""
    argv = [str(item) for item in argv]
    try:
        result = subprocess.run(argv, cwd=str(cwd), env=environment(env_pass, env), stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        return {"exit_code": None, "timed_out": True, "stdout": _tail(exc.stdout), "stderr": _tail(exc.stderr)}
    return {"exit_code": result.returncode, "stdout": result.stdout[:STDOUT_LIMIT], "stderr": _tail(result.stderr)}


def start_job(argv, *, kind: str, label: str, cwd, env_pass=(), env=None, timeout_s: float,
              uses_gpu: bool = True, allow_concurrent_gpu: bool = False) -> dict:
    """Start ``argv`` as a job in the shared jobs folder: ``{job_id, log_dir}``, or the guard's refusal (``error``).

    A GPU job is guarded as an Isaac Lab launch is: no second GPU job and no job beside a running Kit unless
    ``allow_concurrent_gpu``, and never past a configured GPU-hour budget."""
    from .isaaclab import jobs

    argv = [str(item) for item in argv]
    with jobs._launch_lock:
        if uses_gpu and not allow_concurrent_gpu:
            blocked = jobs._concurrency_guard() or jobs._kit_bridge_guard()
            if blocked is not None:
                return {**blocked, "hint": 'Wait for it, stop it with jobs(action="cancel") if it is yours, or knowingly '
                                           "pass allow_concurrent_gpu=true. Nothing was stopped."}
        blocked = jobs._budget_guard() if uses_gpu else None
        if blocked is not None:
            return blocked
        job_id = jobs._job_id(label)
        folder = jobs.jobs_dir() / job_id
        folder.mkdir(parents=True)
        child = environment(env_pass, {"PYTHONUNBUFFERED": "1", **(env or {})})
        stdout, stderr = folder / "stdout.log", folder / "stderr.log"
        with stdout.open("wb") as out, stderr.open("wb") as err:
            process = subprocess.Popen(jobs._exit_recording(argv, folder, timeout_s), stdout=out, stderr=err,
                                       stdin=subprocess.DEVNULL, env=child, cwd=str(cwd), start_new_session=True)
        jobs._write_job(folder, {
            "id": job_id, "job_id": job_id, "kind": kind, "task": label, "label": label,
            "gpu": jobs._gpu_index(child.get("CUDA_VISIBLE_DEVICES")), "command": argv, "cwd": str(cwd),
            "pid": process.pid, "pid_start": jobs._process_start(process.pid), "timeout_s": timeout_s,
            "uses_gpu": uses_gpu, "started_at": jobs._now(), "finished_at": None, "status": "running", "exit_code": None,
            "log_dir": str(folder.resolve()), "run_dir": str(folder.resolve()),
            "stdout_log": str(stdout.resolve()), "stderr_log": str(stderr.resolve()),
        })
        with jobs._jobs_lock:
            jobs._active[job_id] = process
        threading.Thread(target=jobs._watcher, args=(folder, job_id, process, timeout_s), daemon=True).start()
    return {"job_id": job_id, "log_dir": str(folder.resolve())}


def answer(tool: str, action: str, info: dict, *, warnings=(), **fields) -> dict:
    """The result envelope every wrapper returns: ``{ok, tool, action, commit, pin, pin_match, exit_code,
    result | error, warnings}``."""
    return {"ok": "error" not in fields, "tool": tool, "action": action, "commit": info["commit"], "pin": info["pin"],
            "pin_match": info["pin_match"], "exit_code": fields.pop("exit_code", None), **fields,
            "warnings": [*info["warnings"], *warnings]}

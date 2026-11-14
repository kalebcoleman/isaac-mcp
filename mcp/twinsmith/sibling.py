"""Run a tool that lives beside twinsmith: a bounded subprocess or a job, with a scrubbed environment.

The child sees only ``PATH``, ``HOME``, the GPU selection variables and the names a caller passes in
``env_pass`` or sets in ``env_set``: no credentials, nothing named ``ROS_*``, no Python or virtual
environment of this server. A job is a record in the shared jobs folder, so ``jobs`` follows and
cancels it like an Isaac Lab job.

A pinned sibling (RoboSimStudio, Graph-as-Policy, VABAR) is a git checkout under
``<workspace>/tools/<name>`` with its own ``.venv``: ``Checkout`` finds it and reports its commit
against the tested pin. The host never installs, updates or imports one.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path

from . import pack_manifest

KEEP = ("PATH", "HOME")
GPU = ("CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES")
TAIL = 4000
WHOLE = 1 << 20                     # stdout_chars for a caller that parses the output
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")


def environment(env_pass=(), env_set=None, source=None) -> dict:
    """The child's environment: ``KEEP``, ``GPU`` and ``env_pass`` copied from *source*, then *env_set*.
    Nothing named ``ROS_*`` passes: a sibling never joins a ROS graph."""
    source = os.environ if source is None else source
    env = {name: source[name] for name in (*KEEP, *GPU, *env_pass) if name in source}
    env.update(env_set or {})
    return {name: value for name, value in env.items() if not name.startswith("ROS_")}


def commit(folder) -> str | None:
    """The checked-out commit of a git folder, or None."""
    try:
        done = subprocess.run(["git", "-C", str(folder), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=5, env=environment())
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def run(argv, *, cwd=None, env_pass=(), env_set=None, timeout_s=120.0, stdout_chars=TAIL) -> dict:
    """Run *argv* (no shell) and return its exit code and the tails of its output: the last *stdout_chars* of
    stdout (``WHOLE`` for output the caller parses) and the last ``TAIL`` of stderr."""
    argv = [str(part) for part in argv]
    try:
        done = subprocess.run(argv, cwd=cwd, env=environment(env_pass, env_set), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"argv": argv, "exit_code": None, "timed_out": True, "stdout": "", "stderr": ""}
    except OSError as error:
        return {"argv": argv, "exit_code": None, "timed_out": False, "stdout": "", "stderr": str(error)}
    return {"argv": argv, "exit_code": done.returncode, "timed_out": False,
            "stdout": done.stdout[-stdout_chars:], "stderr": done.stderr[-TAIL:]}


def start_job(argv, *, task, kind, cwd=None, env_pass=(), env_set=None, timeout_s=None, uses_gpu=False,
              allow_concurrent_gpu=False, label=None) -> dict:
    """Start *argv* as a job. A GPU job refuses next to another GPU job or a Kit bridge unless
    *allow_concurrent_gpu*, as ``isaaclab__launch`` does."""
    from .isaaclab import jobs

    argv = [str(part) for part in argv]
    with jobs._launch_lock:
        if uses_gpu:
            blocked = jobs._budget_guard()                 # a spent GPU-hour budget is never overridden
            if blocked is not None:
                return {"ok": False, "code": "GPU_BUDGET", "message": blocked["error"],
                        "hint": "The configured GPU-hour budget is spent; raise it or wait for the next period."}
        if uses_gpu and not allow_concurrent_gpu:
            for guard in (jobs._concurrency_guard, jobs._kit_bridge_guard):
                blocked = guard()
                if blocked is not None:
                    return {"ok": False, "code": "GPU_BUSY", "message": blocked["error"],
                            "hint": "Wait for it, or pass allow_concurrent_gpu=true to start anyway."}
        job_id = jobs._job_id(task)
        folder = jobs.jobs_dir() / job_id
        folder.mkdir(parents=True)
        with open(folder / "stdout.log", "wb") as out, open(folder / "stderr.log", "wb") as err:
            proc = subprocess.Popen(jobs._exit_recording(argv, folder, timeout_s), stdout=out, stderr=err,
                                    stdin=subprocess.DEVNULL, env=environment(env_pass, env_set),
                                    cwd=cwd, start_new_session=True)
        jobs._write_job(folder, {
            "id": job_id, "job_id": job_id, "kind": kind, "task": task, "label": label, "command": argv,
            "uses_gpu": uses_gpu, "pid": proc.pid,
            **({"gpu": jobs._gpu_index(environment(env_pass, env_set).get("CUDA_VISIBLE_DEVICES"))} if uses_gpu else {}), "pid_start": jobs._process_start(proc.pid), "timeout_s": timeout_s,
            "started_at": time.time(), "finished_at": None, "status": "running", "exit_code": None,
            "log_dir": str(folder.resolve()), "run_dir": str(Path(cwd or folder).resolve()),
            "stdout_log": str((folder / "stdout.log").resolve()), "stderr_log": str((folder / "stderr.log").resolve())})
        with jobs._jobs_lock:
            jobs._active[job_id] = proc
        threading.Thread(target=jobs._watcher, args=(folder, job_id, proc, timeout_s), daemon=True).start()
    return {"ok": True, "job_id": job_id, "log_dir": str(folder.resolve()), "command": argv}


class Refusal(ValueError):
    """A request a wrapper will not run, with a stable code for the answer."""

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

    def info(self, program: str = "python") -> dict:
        """``{checkout, commit, pin, pin_match, environment_present, warnings}``, plus ``setup`` when it is not usable."""
        found = commit(self.root)
        executable = self.bin(program)
        present = executable.is_file() and os.access(executable, os.X_OK)
        info = {"checkout": str(self.root), "commit": found, "pin": self.pin,
                "pin_match": bool(found and found.startswith(self.pin)), "environment_present": present,
                "warnings": []}
        if not info["pin_match"]:
            info["warnings"].append(f"{self.name} checkout commit {found or 'unknown'} differs from tested pin {self.pin}.")
        if not found or not present:
            info["setup"] = (f"The owner sets this up; twinsmith never installs it. Clone {self.repository} into {self.root}, "
                             f"check out {self.pin}, and create its own environment so that {executable} exists"
                             + (f" (or point {self.env_var} at an existing checkout)." if self.env_var else "."))
        return info


def run_name(value, prefix: str) -> str:
    """A run folder name: the caller's (letters, digits, underscores, hyphens), else ``<prefix>-<timestamp>``."""
    if value is None:
        return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}"
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise Refusal("INVALID_VALUE", "name must be 1 to 80 letters, digits, underscores or hyphens.")
    return value


def answer(tool: str, action: str, info: dict, *, warnings=(), **fields) -> dict:
    """The result envelope a checkout wrapper returns: ``{ok, tool, action, commit, pin, pin_match, exit_code,
    result | error, warnings}``."""
    return {"ok": "error" not in fields, "tool": tool, "action": action, "commit": info["commit"], "pin": info["pin"],
            "pin_match": info["pin_match"], "exit_code": fields.pop("exit_code", None), **fields,
            "warnings": [*info["warnings"], *warnings]}


def new_run_folder(folder: Path) -> None:
    """Create a run folder that must not exist yet, so two calls with one name cannot share it."""
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        folder.mkdir()
    except FileExistsError:
        raise Refusal("ALREADY_EXISTS", f"{folder} exists; choose another name.") from None

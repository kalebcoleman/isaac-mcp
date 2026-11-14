"""Run a tool that lives beside twinsmith: a bounded subprocess or a job, with a scrubbed environment.

The child sees only ``PATH``, ``HOME``, the GPU selection variables and the names a caller passes in
``env_pass`` or sets in ``env_set``: no credentials, no ``ROS_DOMAIN_ID``, no Python or virtual
environment of this server. A job is a record in the shared jobs folder, so ``jobs`` follows and
cancels it like an Isaac Lab job.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

KEEP = ("PATH", "HOME")
GPU = ("CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES")
TAIL = 4000


def environment(env_pass=(), env_set=None, source=None) -> dict:
    """The child's environment: ``KEEP``, ``GPU`` and ``env_pass`` copied from *source*, then *env_set*."""
    source = os.environ if source is None else source
    env = {name: source[name] for name in (*KEEP, *GPU, *env_pass) if name in source}
    env.update(env_set or {})
    return env


def commit(folder) -> str | None:
    """The checked-out commit of a git folder, or None."""
    try:
        done = subprocess.run(["git", "-C", str(folder), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=5, env=environment())
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def run(argv, *, cwd=None, env_pass=(), env_set=None, timeout_s=120.0) -> dict:
    """Run *argv* (no shell) and return its exit code and the tails of its output."""
    argv = [str(part) for part in argv]
    try:
        done = subprocess.run(argv, cwd=cwd, env=environment(env_pass, env_set), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"argv": argv, "exit_code": None, "timed_out": True, "stdout": "", "stderr": ""}
    except OSError as error:
        return {"argv": argv, "exit_code": None, "timed_out": False, "stdout": "", "stderr": str(error)}
    return {"argv": argv, "exit_code": done.returncode, "timed_out": False,
            "stdout": done.stdout[-TAIL:], "stderr": done.stderr[-TAIL:]}


def start_job(argv, *, task, kind, cwd=None, env_pass=(), env_set=None, timeout_s=None, uses_gpu=False,
              allow_concurrent_gpu=False, label=None) -> dict:
    """Start *argv* as a job. A GPU job refuses next to another GPU job or a Kit bridge unless
    *allow_concurrent_gpu*, as ``isaaclab__launch`` does."""
    from .isaaclab import jobs

    argv = [str(part) for part in argv]
    with jobs._launch_lock:
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
            "uses_gpu": uses_gpu, "pid": proc.pid, "pid_start": jobs._process_start(proc.pid), "timeout_s": timeout_s,
            "started_at": time.time(), "finished_at": None, "status": "running", "exit_code": None,
            "log_dir": str(folder.resolve()), "run_dir": str(Path(cwd or folder).resolve()),
            "stdout_log": str((folder / "stdout.log").resolve()), "stderr_log": str((folder / "stderr.log").resolve())})
        with jobs._jobs_lock:
            jobs._active[job_id] = proc
        threading.Thread(target=jobs._watcher, args=(folder, job_id, proc, timeout_s), daemon=True).start()
    return {"ok": True, "job_id": job_id, "log_dir": str(folder.resolve()), "command": argv}

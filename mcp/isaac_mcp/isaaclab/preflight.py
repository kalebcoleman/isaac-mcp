"""Check the GPU and running jobs before launching an Isaac Lab job.

Reads ``nvidia-smi`` (free and used memory, and which processes hold it), the Isaac Lab jobs this host
started, and any Kit bridge. If an earlier ``isaaclab__env_check`` of the same task measured its GPU
memory, it also suggests a ``num_envs`` that fits the free memory. That figure is an upper bound (it
includes the simulator's own fixed cost), so the suggestion is conservative.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from . import jobs as _jobs

HEADROOM = 0.8          # use at most this share of free memory
MIN_FREE_MIB = 4096     # below this, nothing useful starts


def _smi(args: list[str]) -> str | None:
    try:
        return subprocess.run(["nvidia-smi", *args], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def gpu_state() -> dict[str, Any] | None:
    out = _smi(["--query-gpu=name,memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"])
    if not out or not out.strip():
        return None
    name, total, used, free = [p.strip() for p in out.strip().splitlines()[0].split(",")]
    procs = []
    apps = _smi(["--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"]) or ""
    for line in apps.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 3 and parts[0].isdigit() and parts[-1].isdigit():
            procs.append({"pid": int(parts[0]), "name": ",".join(parts[1:-1]), "mib": int(parts[-1])})
        elif len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            procs.append({"pid": int(parts[0]), "mib": int(parts[1])})
    return {"name": name, "total_mib": int(total), "used_mib": int(used), "free_mib": int(free), "processes": procs}


def _measurements(task: str) -> dict[str, Any] | None:
    """The newest finished env_check of this task that measured GPU memory."""
    best = None
    try:
        dirs = sorted(_jobs.jobs_dir().iterdir(), reverse=True)
    except OSError:
        return None
    for jd in dirs:
        data = _jobs._read_job(jd) if jd.is_dir() else None
        if not data or data.get("kind") != "env_check" or data.get("task") != task:
            continue
        result = _jobs._check_result(jd)
        if result and result.get("gpu_mib") and result.get("num_envs"):
            best = {"job_id": data.get("job_id"), "num_envs": result["num_envs"], "gpu_mib": result["gpu_mib"],
                    "ok": result.get("ok")}
            break
    return best


def suggest_num_envs(free_mib: int, measured: dict[str, Any] | None, fixed_fraction: float = 0.5) -> int | None:
    """Envs that fit ``HEADROOM`` of free memory, from a measured run; None without a measurement.

    Half the measured memory is treated as fixed cost (simulator, kernels), the rest as per-environment.
    The measured run's own envs are assumed to be the variable half, so this errs towards fewer."""
    if not measured:
        return None
    fixed = measured["gpu_mib"] * fixed_fraction
    per_env = max(1.0, (measured["gpu_mib"] - fixed) / max(1, measured["num_envs"]))
    budget = free_mib * HEADROOM - fixed
    return max(0, int(budget // per_env))


def _in_group(pid: int, groups: set) -> bool:
    """True when pid belongs to the process group of a job this host is running."""
    if not groups:
        return False
    try:
        return os.getpgid(pid) in groups
    except OSError:
        return False


def preflight(task: str | None = None, ledger: bool = False, label: str | None = None, since: Any = None,
              budget_gpu_hours: float | None = None) -> dict[str, Any]:
    gpu = gpu_state()
    running = _jobs._running_job()
    bridges = _jobs._probe_kit_bridges()
    reasons = []
    if gpu is None:
        reasons.append("nvidia-smi gave no answer, so GPU memory is unknown")
    elif gpu["free_mib"] < MIN_FREE_MIB:
        reasons.append(f"only {gpu['free_mib']} MiB of GPU memory is free")
    if running:
        reasons.append(f"Isaac Lab job {running.get('job_id')} is still running (pid {running.get('pid')})")
    if bridges:
        where = ", ".join(f"{b['host']}:{b['port']}" for b in bridges)
        free = f" ({gpu['free_mib']} MiB free)" if gpu else ""
        reasons.append(f"a Kit bridge app at {where} shares the GPU{free}. Launching next to it is refused by default; when the free memory is "
                       "ample for your job and the person who owns that simulator agrees, pass allow_concurrent_gpu=true to launch anyway")
    from . import ledger as _ledger

    wanted = ledger or label is not None or since is not None or budget_gpu_hours is not None
    gpu_hours = None
    if wanted or _ledger.config()["budget_hours"] is not None:
        gpu_hours = _ledger.report(label=label, since=since, budget_hours=budget_gpu_hours)
        if gpu_hours.get("exhausted"):
            reasons.append(f"the GPU budget is spent: {gpu_hours['used_hours']:.2f} of {gpu_hours['budget_hours']:.2f} GPU-hours, "
                           "so launches are refused")
    out: dict[str, Any] = {
        "gpu": gpu,
        "running_job": ({"job_id": running.get("job_id"), "pid": running.get("pid")} if running else None),
        "kit_bridges": bridges,
        "can_start": not reasons,
        "reasons": reasons,
    }
    if gpu_hours is not None:
        out["gpu_hours"] = gpu_hours
    if gpu:
        own = {running.get("pid")} if running else set()
        out["other_gpu_processes"] = [p for p in gpu.get("processes", []) if not _in_group(p["pid"], own)]
    if task:
        measured = _measurements(task)
        out["measured"] = measured
        if gpu and measured:
            out["suggested_num_envs"] = suggest_num_envs(gpu["free_mib"], measured)
            out["suggestion_note"] = ("From the last isaaclab__env_check of this task; a conservative estimate that treats "
                                      "half its GPU memory as fixed cost. Confirm with a short run.")
        else:
            out["suggested_num_envs"] = None
            out["suggestion_note"] = ("No GPU measurement for this task yet: run isaaclab__env_check with a small "
                                      "num_envs first, then ask again.")
    return out

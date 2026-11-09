"""GPU-hour accounting over the Isaac Lab jobs this host started, with an optional budget cap.

There is no second record to keep: every job already stores its start, end and GPU in ``job.json``. The ledger
reads those. Jobs that share a GPU at the same time are not double counted: the GPU-hours used are the
wall-clock *union* of the jobs' intervals on each GPU, summed over GPUs. The naive sum of each job's wall time
is reported beside it, because a hand-kept ledger that adds wall times over-counts whenever runs overlap.

Opt in with ``ISAACLAB_GPU_BUDGET_HOURS`` (a number) or a ledger file, ``ISAACLAB_GPU_LEDGER`` or
``<jobs dir>/gpu_ledger.json``, holding ``{"budget_hours": 8, "since": "2026-11-08T00:00", "carried_hours": 1.5}``.
Unconfigured, nothing here changes behaviour: the launch guard is a no-op and preflight adds no ledger.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import jobs as _jobs

BUDGET_ENV = "ISAACLAB_GPU_BUDGET_HOURS"
LEDGER_ENV = "ISAACLAB_GPU_LEDGER"
LEDGER_NAME = "gpu_ledger.json"
HOW_COUNTED = ("GPU-hours = the wall-clock union of job intervals on each GPU, summed over GPUs, plus carried_hours. "
               "Jobs that overlap on one GPU are counted once for the time they overlap; naive_sum_hours is the plain sum of "
               "each job's wall time, shown for comparison. A running job counts up to now.")


def _hours(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value != value:
        raise ValueError(f"{name} must be a number of hours, 0 or more (got {value!r})")
    return float(value)


def config() -> dict[str, Any]:
    """The configured budget: {budget_hours, since, carried_hours, source}; budget_hours is None when not configured.
    A malformed setting is an error, never a silently absent cap."""
    cfg: dict[str, Any] = {"budget_hours": None, "since": None, "carried_hours": 0.0, "source": None}
    path = os.environ.get(LEDGER_ENV)
    ledger_file = Path(path).expanduser() if path else _jobs._jobs_path() / LEDGER_NAME
    if ledger_file.is_file() or path:
        try:
            data = json.loads(ledger_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"GPU ledger file {ledger_file} is unreadable ({exc}); fix or remove it, or unset {LEDGER_ENV}")
        if not isinstance(data, dict):
            raise ValueError(f"GPU ledger file {ledger_file} must hold a JSON object")
        if data.get("budget_hours") is not None:
            cfg["budget_hours"] = _hours(data["budget_hours"], "budget_hours")
        cfg["since"] = data.get("since")
        cfg["carried_hours"] = _hours(data.get("carried_hours", 0), "carried_hours")
        cfg["source"] = str(ledger_file)
    raw = os.environ.get(BUDGET_ENV)
    if raw is not None and raw.strip():
        try:
            cfg["budget_hours"] = _hours(float(raw), BUDGET_ENV)
        except ValueError:
            raise ValueError(f"{BUDGET_ENV}={raw!r} is not a number of hours")
        cfg["source"] = f"{BUDGET_ENV}" if cfg["source"] is None else f"{cfg['source']} + {BUDGET_ENV}"
    return cfg


def _gpu_of(data: dict[str, Any]) -> str:
    return str(data.get("gpu") if data.get("gpu") is not None else "0")


def _intervals(since_ts: float | None, now: float) -> list[dict[str, Any]]:
    out = []
    try:
        dirs = sorted(_jobs.jobs_dir().iterdir())
    except OSError:
        return out
    for jd in dirs:
        data = _jobs._read_job(jd) if jd.is_dir() else None
        if not data or not data.get("started_at"):
            continue
        start = float(data["started_at"])
        end = data.get("finished_at")
        if end is None:  # still running, or the host died before recording the end
            end = now if _jobs._job_alive(data) else _jobs._mtime(jd / "job.json")
        end = max(float(end), start)
        if since_ts is not None:
            if end <= since_ts:
                continue
            start = max(start, since_ts)
        out.append({"job_id": data.get("job_id") or jd.name, "label": data.get("label"), "kind": data.get("kind"),
                    "gpu": _gpu_of(data), "start": start, "end": end, "running": data.get("status") == "running"})
    return out


def _attribute(items: list[dict[str, Any]]) -> float:
    """Sweep one GPU's intervals: add `attributed_s` (wall time split equally among jobs sharing each moment, so the
    shares sum to the union) and `shared_s` (time spent overlapping another job) to each item. Returns the union in seconds."""
    points = sorted({t for i in items for t in (i["start"], i["end"])})
    union = 0.0
    for i in items:
        i["attributed_s"] = 0.0
        i["shared_s"] = 0.0
    for a, b in zip(points, points[1:]):
        live = [i for i in items if i["start"] <= a and i["end"] >= b]
        if not live:
            continue
        union += b - a
        for i in live:
            i["attributed_s"] += (b - a) / len(live)
            if len(live) > 1:
                i["shared_s"] += b - a
    return union


def report(label: str | None = None, since: Any = None, budget_hours: float | None = None) -> dict[str, Any]:
    """GPU-hours used, per job and per GPU, against the budget. *label* keeps jobs whose label or id contains it
    (their union is reported on its own; the budget always counts every job). *budget_hours* overrides the configured cap for this report."""
    cfg = config()
    now = _jobs._now()
    since_value = since if since is not None else cfg["since"]
    since_ts = _jobs._parse_since(since_value) if since_value else None
    cap = _hours(budget_hours, "budget_gpu_hours") if budget_hours is not None else cfg["budget_hours"]
    everything = _intervals(since_ts, now)
    per_gpu: dict[str, dict[str, Any]] = {}
    for gpu in sorted({i["gpu"] for i in everything}):
        group = [i for i in everything if i["gpu"] == gpu]
        union = _attribute(group)
        naive = sum(i["end"] - i["start"] for i in group)
        per_gpu[gpu] = {"jobs": len(group), "union_hours": round(union / 3600, 4), "naive_sum_hours": round(naive / 3600, 4)}
    union_h = sum(g["union_hours"] for g in per_gpu.values())
    naive_h = sum(g["naive_sum_hours"] for g in per_gpu.values())
    carried = cfg["carried_hours"]
    used = union_h + carried
    shown = [i for i in everything if not label or label.lower() in f"{i['label'] or ''} {i['job_id']}".lower()]
    jobs_out = [{"job_id": i["job_id"], "label": i["label"], "kind": i["kind"], "gpu": i["gpu"], "wall_s": round(i["end"] - i["start"], 1),
                 "shared_s": round(i["shared_s"], 1), "attributed_hours": round(i["attributed_s"] / 3600, 4), "running": i["running"]}
                for i in shown]
    out: dict[str, Any] = {
        "counted": HOW_COUNTED,
        "used_hours": round(used, 4), "union_hours": round(union_h, 4), "naive_sum_hours": round(naive_h, 4),
        "overlap_saved_hours": round(naive_h - union_h, 4), "carried_hours": carried,
        "since": since_value, "per_gpu": per_gpu, "jobs": jobs_out,
        "budget_hours": cap, "config_source": cfg["source"],
    }
    if label:
        out["label"] = label
        out["label_attributed_hours"] = round(sum(j["attributed_hours"] for j in jobs_out), 4)
        out["label_naive_hours"] = round(sum(j["wall_s"] for j in jobs_out) / 3600, 4)
    if cap is not None:
        out["remaining_hours"] = round(cap - used, 4)
        out["exhausted"] = used >= cap
    return out


def budget_guard() -> dict[str, Any] | None:
    """A refusal when a configured budget is spent; None when no budget is configured or hours remain."""
    cfg = config()
    if cfg["budget_hours"] is None:
        return None
    rep = report()
    if not rep["exhausted"]:
        return None
    return {
        "error": f"GPU budget spent: {rep['used_hours']:.2f} of {rep['budget_hours']:.2f} GPU-hours used, so no new job starts",
        "gpu_ledger": {k: rep[k] for k in ("used_hours", "union_hours", "naive_sum_hours", "budget_hours", "since", "counted")},
        "hint": (f"Raise budget_hours in {cfg['source']} (or {BUDGET_ENV}) if the user agrees to more, or ask for isaaclab__preflight "
                 "to see the per-job hours. Nothing was started."),
    }

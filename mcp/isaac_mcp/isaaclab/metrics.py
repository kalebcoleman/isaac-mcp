"""Filesystem + TensorBoard metric helpers for the Isaac Lab tool pack.

No FastMCP dependency: the host server wires these as tools later.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
from typing import Any, Dict, List, Optional


def _jsonable(value):
    """Non-finite floats become 'nan'/'inf'/'-inf' so the result is strict JSON (a diverged run stays visible)."""
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    return value


def _normalize_roots(roots) -> List[str]:
    if roots is None:
        return []
    if isinstance(roots, (str, os.PathLike)):
        roots = [roots]
    return [os.path.expanduser(str(r)) for r in roots]


def _default_log_roots() -> List[str]:
    env = os.environ.get("ISAACLAB_LOGS_DIR")
    if env and env.strip():
        return [env.strip()]
    return [os.path.expanduser("~/Desktop/ur5e-digital-twin/learning6/logs")]


def _allowed_roots(search_roots=None) -> List[str]:
    roots = []
    roots.extend(_normalize_roots(search_roots))
    roots.extend(_default_log_roots())
    roots.append(os.path.expanduser("~/.local/share/isaac-mcp/jobs"))
    # Runs started by isaaclab__train land under <isaaclab_root>/logs.
    try:
        from .install import locate_install

        inst = locate_install()
        if inst and inst.get("isaaclab_root"):
            roots.append(os.path.join(inst["isaaclab_root"], "logs"))
            roots.append(os.path.join(os.path.dirname(inst["isaaclab_root"]), "logs"))
    except Exception:  # noqa: BLE001 - roots are best effort
        pass
    if os.environ.get("ISAACLAB_ALLOW_TMP") == "1":  # test fixtures only
        roots.append(tempfile.gettempdir())
    out: List[str] = []
    seen = set()
    for r in roots:
        rp = os.path.realpath(r)
        if rp not in seen:
            seen.add(rp)
            out.append(rp)
    return out


def _resolve_run_dir(run_dir, search_roots=None) -> (str, Optional[Dict[str, Any]]):
    if run_dir is None:
        return "", {"error": "run_dir is required"}
    allowed = _allowed_roots(search_roots)
    real = os.path.realpath(os.path.expanduser(str(run_dir)))
    for root in allowed:
        if real == root or real.startswith(root + os.sep):
            return real, None
    return "", {"error": "path outside allowed roots"}


def _is_under_allowed(path: str, allowed: List[str]) -> bool:
    for root in allowed:
        if path == root or path.startswith(root + os.sep):
            return True
    return False


def _parse_step(name: str) -> Optional[int]:
    patterns = [
        r"(?:model|epoch|step|iter(?:ation)?|checkpoint|ep)[_\-]?(\d+)",
    ]
    for pat in patterns:
        m = re.search(pat, name, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    m = re.search(r"[_\-](\d+)(?:\.[^.]+)?$", name)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def _find_event_files(root: str) -> List[str]:
    out: List[str] = []
    allowed = _allowed_roots()
    for dp, _dirnames, filenames in os.walk(root, followlinks=False):
        for fn in filenames:
            if fn.startswith("events.out.tfevents."):
                path = os.path.join(dp, fn)
                # A symlinked event file must not leak data from outside the allowed roots.
                if _is_under_allowed(os.path.realpath(path), allowed):
                    out.append(path)
    return sorted(out)


def _import_event_accumulator():
    try:
        from tensorboard.backend.event_processing.event_accumulator import (
            EventAccumulator,
        )

        return EventAccumulator, None
    except Exception:
        return None, {
            "error": "install the 'isaaclab' extra: pip install 'isaac-mcp[isaaclab]'"
        }


def checkpoints_list(run_dir=None, task=None, search_roots=None) -> Dict[str, Any]:
    if run_dir is not None:
        real, err = _resolve_run_dir(run_dir, search_roots)
        if err:
            return err
        roots_to_search = [real]
    else:
        if search_roots is None:
            roots_to_search = _default_log_roots()
        else:
            roots_to_search = _normalize_roots(search_roots)

    allowed = _allowed_roots(search_roots)

    results: List[Dict[str, Any]] = []
    for root in roots_to_search:
        if not os.path.exists(root):
            continue
        for dp, dirnames, filenames in os.walk(root, followlinks=False):
            # prune symlinked dirs that escape by checking realpath
            # (os.walk followlinks=False already avoids descending into them)
            for fn in filenames:
                if not (fn.lower().endswith(".pth") or fn.lower().endswith(".pt")):
                    continue
                full = os.path.join(dp, fn)
                realfile = os.path.realpath(full)
                if not _is_under_allowed(realfile, allowed):
                    continue
                if task is not None and task not in full:
                    continue
                try:
                    st = os.stat(realfile)
                except OSError:
                    continue
                results.append(
                    {
                        "path": realfile,
                        "step": _parse_step(fn),
                        "size_bytes": int(st.st_size),
                        "mtime": float(st.st_mtime),
                    }
                )

    results.sort(key=lambda c: (c["mtime"], c["path"]), reverse=True)
    results = results[:200]
    return {"checkpoints": results}


def metrics_summary(run_dir, tags=None) -> Dict[str, Any]:
    real, err = _resolve_run_dir(run_dir)
    if err:
        return err

    EventAccumulator, import_err = _import_event_accumulator()
    if import_err:
        return import_err

    files = _find_event_files(real)

    accumulators = []
    available: List[str] = []
    available_set = set()
    for f in files:
        try:
            ea = EventAccumulator(f, size_guidance={"scalars": 0})
            ea.Reload()
            accumulators.append(ea)
            for t in ea.Tags().get("scalars", []):
                if t not in available_set:
                    available_set.add(t)
                    available.append(t)
        except Exception:
            continue

    if tags is not None:
        if isinstance(tags, str):
            tags = [tags]
        tag_list = list(dict.fromkeys(tags))
        unknown = [t for t in tag_list if t not in available_set]
        if unknown:
            return {"error": "unknown tag", "available": available[:50]}
    else:
        tag_list = list(available)

    events_by_tag = {t: [] for t in tag_list}
    for ea in accumulators:
        ea_tags = set(ea.Tags().get("scalars", []))
        for t in tag_list:
            if t in ea_tags:
                try:
                    events_by_tag[t].extend(ea.Scalars(t))
                except Exception:
                    continue

    scalars: Dict[str, Any] = {}
    for t in tag_list:
        evs = events_by_tag.get(t, [])
        if not evs:
            continue
        evs_sorted = sorted(evs, key=lambda e: (e.step, e.wall_time))
        values = [e.value for e in evs_sorted]
        comparable = [v for v in values if not math.isnan(v)]  # NaN would poison min()/max()
        scalars[t] = {
            "last": _jsonable(values[-1]),
            "min": _jsonable(min(comparable)) if comparable else "nan",
            "max": _jsonable(max(comparable)) if comparable else "nan",
            "step": evs_sorted[-1].step,
            "count": len(values),
        }

    return {"scalars": scalars}


def tensorboard_tail(run_dir, tag, last_n=50) -> Dict[str, Any]:
    if last_n is not None and (isinstance(last_n, bool) or not isinstance(last_n, int)):
        return {"error": "last_n must be an integer or null"}
    real, err = _resolve_run_dir(run_dir)
    if err:
        return err

    EventAccumulator, import_err = _import_event_accumulator()
    if import_err:
        return import_err

    files = _find_event_files(real)

    accumulators = []
    available: List[str] = []
    available_set = set()
    for f in files:
        try:
            ea = EventAccumulator(f, size_guidance={"scalars": 0})
            ea.Reload()
            accumulators.append(ea)
            for t in ea.Tags().get("scalars", []):
                if t not in available_set:
                    available_set.add(t)
                    available.append(t)
        except Exception:
            continue

    points: List[Dict[str, Any]] = []
    found = False
    for ea in accumulators:
        ea_tags = set(ea.Tags().get("scalars", []))
        if tag in ea_tags:
            found = True
            try:
                for se in ea.Scalars(tag):
                    points.append(
                        {
                            "step": se.step,
                            "value": _jsonable(se.value),
                            "wall_time": se.wall_time,
                        }
                    )
            except Exception:
                continue

    if not found:
        return {"error": "unknown tag", "available": available[:50]}

    points.sort(key=lambda p: (p["step"], p["wall_time"]))
    if last_n is not None and last_n > 0:
        points = points[-last_n:]
    return {"points": points}

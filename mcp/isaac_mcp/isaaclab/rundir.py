"""Resolve an Isaac Lab run folder (``<timestamp>_<run_name>``) by name under a log root.
(A job's own run folder comes from ``jobs.runs_status``, which reports ``run_dir_source``.)

Every failure raises ``ValueError`` with a reason. A lookup never returns an empty string or a made-up path:
a script that dropped an empty variable into a path once picked the wrong folder, so "not found" is an error.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import jobs as _jobs
from . import metrics as _metrics

# Lab names a run folder "<YYYY-MM-DD_HH-MM-SS>" plus "_<run_name>" when a run name was given.
_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:_(.+))?$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_MAX_DEPTH = 3  # <log_root>/<library>/<experiment>/<run>, so a root of logs/, logs/rsl_rl or the experiment all work
_MAX_CANDIDATES = 10


def _nonblank(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is empty. Give a non-empty {name}; an empty value would match every run folder.")
    return value.strip()


def _scan(root: str, run_name: str | None) -> list[Path]:
    """Run folders under *root* (newest first), keeping those whose run name is exactly *run_name* when given."""
    found: list[Path] = []
    base_depth = len(Path(root).parts)
    for dirpath, dirnames, _files in os.walk(root, followlinks=False):
        depth = len(Path(dirpath).parts) - base_depth
        for name in list(dirnames):
            match = _STAMP_RE.match(name)
            if match and (run_name is None or match.group(2) == run_name):
                found.append(Path(dirpath, name))
                dirnames.remove(name)  # a run folder's own subfolders (nn/, params/, videos/) are not runs
        if depth + 1 >= _MAX_DEPTH:
            dirnames[:] = []
    found.sort(key=lambda p: (p.name[:19], str(p)), reverse=True)
    return found


def from_log_root(log_root: Any, run_name: Any, experiment: Any = None, latest: bool = False) -> dict[str, Any]:
    if log_root is None or run_name is None:
        raise ValueError("Give job_id, or log_root and run_name together; one of them is missing.")
    root = _nonblank(log_root, "log_root")
    name = _nonblank(run_name, "run_name")
    if not _NAME_RE.match(name):
        raise ValueError(f"run_name {name!r} is not a plain name (letters, digits, '_', '.', '-'; no '/').")
    real, err = _metrics._resolve_run_dir(root)
    if err:
        raise ValueError(f"log_root {root!r}: {err['error']} ({', '.join(err['allowed_roots'])}). Set ISAACLAB_LOGS_DIR to your project's logs folder to allow another.")
    if not os.path.isdir(real):
        raise ValueError(f"log_root {real} is not a directory.")
    scope = real
    if experiment is not None:
        exp = _nonblank(experiment, "experiment")
        if not _NAME_RE.match(exp):
            raise ValueError(f"experiment {exp!r} is not a plain folder name.")
        matches = [p for p in Path(real).rglob(exp) if p.is_dir() and len(p.relative_to(real).parts) <= _MAX_DEPTH - 1]
        if not matches:
            raise ValueError(f"no experiment folder named {exp!r} under {real}.")
        scope = str(sorted(matches)[0])
    runs = _scan(scope, name)
    if not runs:
        newest = [p.name for p in _scan(scope, None)[:5]]
        raise ValueError(f"no run folder '<timestamp>_{name}' under {scope}. "
                         + (f"Newest run folders there: {', '.join(newest)}." if newest else "It holds no run folders."))
    if len(runs) > 1 and not latest:
        listed = ", ".join(p.name for p in runs[:_MAX_CANDIDATES])
        raise ValueError(f"{len(runs)} run folders match '<timestamp>_{name}' under {scope}: {listed}. Pass latest=true for the newest, "
                         "or experiment= to narrow it.")
    out: dict[str, Any] = {"run_dir": str(runs[0]), "source": "log_root", "run_name": name}
    if len(runs) > 1:
        out["candidates"] = [str(p) for p in runs[:_MAX_CANDIDATES]]
        out["note"] = f"{len(runs)} folders match; run_dir is the newest because latest=true."
    ckpt = _jobs._latest_checkpoint(str(runs[0]))
    out["latest_checkpoint"] = ckpt
    return out

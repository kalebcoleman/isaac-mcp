"""Summarise metrics in a project's evaluation-result JSON files, against criteria the caller supplies.

The files are read, never run or modified. Any JSON object works: a criterion names a metric by a dotted key path
(``stats.success_rate``, ``episodes.0.reward``), a comparison and a threshold, and the summary reports each file's value,
whether it passed, and the pass count / mean / min / max over the files. With no criteria the summary just reports the
numeric metrics (no pass or fail). A criterion whose key a file lacks is refused naming the file and key path, so a layout
change cannot produce a made-up number.
"""

from __future__ import annotations

import glob
import json
import math
import operator
import os
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 64 * 1024**2
MAX_FILES = 50
MAX_METRICS = 200  # numeric leaves reported per file when no criteria are given
MAX_DEPTH = 6

_OPS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt, "==": operator.eq, "!=": operator.ne}
_OP_WORDS = {"ge": ">=", "gte": ">=", "gt": ">", "le": "<=", "lte": "<=", "lt": "<", "eq": "==", "ne": "!=", "neq": "!=", "=": "=="}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _clean(value: float) -> float | int:
    return int(value) if float(value).is_integer() and abs(value) < 1e15 else round(value, 6)


def _lookup(node: Any, parts: list[str]) -> Any:
    """The value at a dotted path; a dict key that itself contains dots is tried first. Raises KeyError with the dotted prefix."""
    if not parts:
        return node
    if isinstance(node, dict):
        joined = ".".join(parts)
        if joined in node:
            return node[joined]
        if parts[0] in node:
            return _lookup(node[parts[0]], parts[1:])
    elif isinstance(node, list) and parts[0].isdigit() and int(parts[0]) < len(node):
        return _lookup(node[int(parts[0])], parts[1:])
    raise KeyError(parts[0])


def _dig(data: Any, key: str, file: str) -> Any:
    parts = [p for p in key.split(".") if p != ""]
    if not parts:
        raise ValueError("empty criterion key (use a dotted path such as 'stats.success_rate')")
    try:
        return _lookup(data, parts)
    except KeyError:
        pass
    node, walked = data, []  # find where the path stops, to say what is there instead
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            where = ".".join(walked) or "<top level>"
            have = (", ".join(sorted(map(str, node))[:12]) if isinstance(node, dict)
                    else f"a list of {len(node)}" if isinstance(node, list) else type(node).__name__)
            raise ValueError(f"{file}: expected key {key}, but {where} has no {part!r} (it has: {have}).")
        walked.append(part)
    raise ValueError(f"{file}: cannot read key {key}")


def _flatten(node: Any, prefix: str = "", depth: int = 0, out: dict[str, float] | None = None) -> dict[str, float]:
    out = {} if out is None else out
    if len(out) >= MAX_METRICS or depth > MAX_DEPTH:
        return out
    if isinstance(node, dict):
        for k, v in node.items():
            _flatten(v, f"{prefix}.{k}" if prefix else str(k), depth + 1, out)
    elif isinstance(node, list):
        if len(node) <= 16:
            for i, v in enumerate(node):
                _flatten(v, f"{prefix}.{i}" if prefix else str(i), depth + 1, out)
    else:
        n = _number(node) if not isinstance(node, bool) else None
        if n is not None and prefix:
            out[prefix] = n
    return out


def _load_criteria_file(path: str) -> Any:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"criteria file not found: {path} (looked for {p.resolve()}; pass an absolute path)")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            raise ValueError(f"{path}: reading YAML needs PyYAML; use a .json criteria file or pass the criteria as a list") from None
        try:
            data = yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"{path}: not readable YAML ({exc})") from None
    else:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"{path}: not readable JSON ({exc})") from None
    if isinstance(data, dict) and "criteria" in data:
        data = data["criteria"]
    return data


def normalize_criteria(criteria: Any) -> list[dict[str, Any]]:
    """A list of ``{key, op, value, label}``, from that list, a single such object, or a path to a JSON/YAML file holding either
    (or ``{"criteria": [...]}``). ``op`` is one of >= > <= < == != (or ge gt le lt eq ne); it defaults to >=. Empty means none."""
    if criteria is None or criteria == [] or criteria == "":
        return []
    if isinstance(criteria, str):
        criteria = _load_criteria_file(criteria)
    if isinstance(criteria, dict):
        criteria = [criteria]
    if not isinstance(criteria, list) or len(criteria) > 100:
        raise ValueError("criteria must be a list of {key, op, value, label} objects (at most 100), or a path to a JSON/YAML file holding one")
    out = []
    for i, item in enumerate(criteria):
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not item["key"].strip():
            raise ValueError(f"criteria[{i}] needs a 'key': a dotted path into the result file, e.g. 'stats.success_rate'")
        op = str(item.get("op", ">=")).strip().lower()
        op = _OP_WORDS.get(op, op)
        if op not in _OPS:
            raise ValueError(f"criteria[{i}]: op {item.get('op')!r} must be one of {', '.join(_OPS)}")
        value = _number(item.get("value"))
        if value is None:
            raise ValueError(f"criteria[{i}] needs a numeric 'value' (the threshold to compare against)")
        label = item.get("label")
        if label is not None and not isinstance(label, str):
            raise ValueError(f"criteria[{i}]: label must be text")
        out.append({"label": label or f"{item['key'].strip()} {op} {_clean(value)}", "key": item["key"].strip(), "op": op,
                    "value": _clean(value)})
    labels = [c["label"] for c in out]
    if len(set(labels)) != len(labels):
        raise ValueError("criteria labels must be unique; give each criterion its own 'label'")
    return out


def _expand(paths: Any, name: str) -> list[str]:
    """Absolute file paths and/or glob patterns (``*``, ``?``, ``[...]``, ``**``), expanded in sorted order."""
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p.strip() for p in paths):
        raise ValueError(f"{name} must be a list of 1 to {MAX_FILES} result-file paths or glob patterns")
    out: list[str] = []
    for entry in paths:
        pattern = os.path.expanduser(entry.strip())
        if any(c in pattern for c in "*?["):
            found = sorted(glob.glob(pattern, recursive=True))
            if not found:
                raise ValueError(f"{name}: pattern {entry!r} matched no files (looked for {pattern}; patterns are matched from the "
                                 "server's working directory unless absolute)")
            out.extend(f for f in found if f not in out)
        elif pattern not in out:
            out.append(pattern)
    if len(out) > MAX_FILES:
        raise ValueError(f"{name} expands to {len(out)} files; the limit is {MAX_FILES}. Narrow the pattern.")
    return out


def _read(path: str) -> tuple[Path, dict[str, Any]]:
    p = Path(path)
    if p.suffix != ".json" or not p.is_file():
        raise ValueError(f"{path}: not an existing .json file (looked for {p.resolve()}; a relative path is resolved from the "
                         "server's working directory, not yours, so pass an absolute path)")
    if p.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{path}: larger than {MAX_FILE_BYTES // 1024**2} MiB")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path}: not readable JSON ({exc})")
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return p, data


def _evaluate(path: str, criteria: list[dict[str, Any]]) -> dict[str, Any]:
    p, data = _read(path)
    run: dict[str, Any] = {"path": str(p.resolve())}
    for key in ("checkpoint", "task"):
        if isinstance(data.get(key), (str, int, float)):
            run[key] = data[key]
    if not criteria:
        run["metrics"] = {k: _clean(v) for k, v in _flatten(data).items()}
        return run
    results = []
    for c in criteria:
        raw = _dig(data, c["key"], str(p))
        value = _number(raw)
        if value is None:
            raise ValueError(f"{p}: {c['key']} is not a number (it is {type(raw).__name__}); a criterion compares one numeric metric")
        results.append({"label": c["label"], "key": c["key"], "value": _clean(value), "passed": bool(_OPS[c["op"]](value, c["value"]))})
    run["results"] = results
    run["passed"] = all(r["passed"] for r in results)
    return run


def _aggregate(runs: list[dict[str, Any]], criteria: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if criteria:
        rows = []
        for i, c in enumerate(criteria):
            vals = [r["results"][i]["value"] for r in runs]
            rows.append({"label": c["label"], "key": c["key"], "op": c["op"], "threshold": c["value"],
                         "values": vals, "mean": _clean(sum(vals) / len(vals)), "min": min(vals), "max": max(vals),
                         "passed_runs": sum(1 for r in runs if r["results"][i]["passed"]), "n_runs": len(runs)})
        out["summary"] = rows
        out["runs_passing_all"] = sum(1 for r in runs if r["passed"])
    else:
        keys = sorted({k for r in runs for k in r["metrics"]})
        out["metrics"] = {k: {"mean": _clean(sum(v) / len(v)), "min": _clean(min(v)), "max": _clean(max(v)), "n": len(v)}
                          for k in keys for v in [[r["metrics"][k] for r in runs if k in r["metrics"]]]}
    return out


def summarize(eval_json: Any, criteria: Any = None, label: Any = None, baseline_json: Any = None,
              baseline_label: Any = None) -> dict[str, Any]:
    if not eval_json:
        raise ValueError("Give eval_json: result-file paths or glob patterns (repeat evaluations of one checkpoint, or several).")
    crit = normalize_criteria(criteria)
    runs = [_evaluate(p, crit) for p in _expand(eval_json, "eval_json")]
    out: dict[str, Any] = {"label": label, "n_files": len(runs), "criteria": crit or None, "runs": runs, **_aggregate(runs, crit)}
    if not crit:
        out["note"] = ("No criteria given, so only the numeric metrics are reported (no pass or fail). Pass criteria="
                       "[{\"key\": \"stats.success_rate\", \"op\": \">=\", \"value\": 0.9, \"label\": \"success\"}] (or a path to a "
                       "JSON/YAML file holding that list) to score the files.")
    if baseline_json:
        base_runs = [_evaluate(p, crit) for p in _expand(baseline_json, "baseline_json")]
        base = {"label": baseline_label, "n_files": len(base_runs), "runs": base_runs, **_aggregate(base_runs, crit)}
        if crit:
            base["delta_mean"] = {r["label"]: _clean(r["mean"] - b["mean"]) for r, b in zip(out["summary"], base["summary"])}
        else:
            base["delta_mean"] = {k: _clean(v["mean"] - base["metrics"][k]["mean"]) for k, v in out["metrics"].items() if k in base["metrics"]}
        out["baseline"] = base
    return out

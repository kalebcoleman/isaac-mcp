"""Summarise pass counts from a project's evaluation-result JSON files (the G1 recovery ``final_*.json`` layout).

The files are read, never run or modified. Expected keys are explicit (see ``CRITERIA``); a file that lacks one is
refused naming the file and key path, so a layout change cannot produce a made-up number. The TEST pose set is
refused unless the caller passes ``allow_test=True``, and a pose set that cannot be classified is refused too.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DIRECTIONS = ("side_a", "prone", "side_b", "supine")
MAX_FILE_BYTES = 64 * 1024**2
MAX_FILES = 50

# criteria name -> (what it is, path of the pass count, path of the per-direction figures, whether those are counts or rates)
CRITERIA: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], str]] = {
    "v3_ground_only": ("criteria v3 before 3b (self-penetration not applied), ground-only flight",
                       ("amendment3", "ground_only_flight", "v3_before_selfpen"),
                       ("amendment3", "ground_only_flight", "by_category"), "counts"),
    "v3_latest_substep": ("criteria v3 before 3b, flight measured on the latest sub-step only",
                          ("amendment3", "plausible_v3_before_selfpen"),
                          ("amendment3", "plausible_v3_before_selfpen_by_category"), "counts"),
    "v3_a4": ("criteria v3 before 3b with the proposed A4 flight history",
              ("amendment3", "proposed_a4_flight_history", "v3_before_selfpen_with_a4"),
              ("amendment3", "proposed_a4_flight_history", "v3_with_a4_by_category"), "counts"),
    "v21": ("plausible recovery v2.1", ("plausible", "plausible_v21_successes"),
            ("plausible", "plausible_v21_by_category"), "rates"),
    "v2": ("plausible recovery v2", ("plausible", "plausible_v2_successes"),
           ("plausible", "plausible_v2_by_category"), "rates"),
    "strict": ("strict supported stand", ("strict_supported_stand", "successes"),
               ("strict_supported_stand", "by_category"), "rates"),
}
ALIASES = {"v3": "v3_ground_only", "v3_before_3b": "v3_ground_only"}

POSE_SETS = {"dev_v1.npz": "DEV", "heldout_poses.npz": "DEV", "heldout_v2.npz": "HELDOUT", "test_v2.npz": "TEST"}


TEST_ENV = "ISAAC_MCP_ALLOW_TEST_SPLIT"


def _criteria(name: Any) -> str:
    key = ALIASES.get(str(name).strip().lower(), str(name).strip().lower()) if name is not None else "v3_ground_only"
    if key not in CRITERIA:
        raise ValueError(f"unknown criteria {name!r}; use one of {', '.join(CRITERIA)} (alias v3 = v3_ground_only)")
    return key


def _dig(data: dict[str, Any], path: tuple[str, ...], file: str) -> Any:
    node: Any = data
    for i, key in enumerate(path):
        if not isinstance(node, dict) or key not in node:
            where = ".".join(path[:i]) or "<top level>"
            have = ", ".join(sorted(node)[:12]) if isinstance(node, dict) else type(node).__name__
            raise ValueError(f"{file}: expected key {'.'.join(path)}, but {where} has no {key!r} (it has: {have}). "
                             "This is not the evaluation-result layout this summary reads.")
        node = node[key]
    return node


def _split(data: dict[str, Any], file: str, allow_test: bool) -> tuple[str, str]:
    pose_set = data.get("pose_set")
    if not isinstance(pose_set, str) or not pose_set:
        raise ValueError(f"{file}: no 'pose_set' key, so the evaluation set (DEV, HELDOUT or TEST) cannot be told; refusing to guess.")
    name = Path(pose_set).name
    split = POSE_SETS.get(name)
    if split is None:
        split = "TEST" if "test" in name.lower() else None
    if split is None:
        raise ValueError(f"{file}: pose_set {pose_set!r} is not one of {', '.join(POSE_SETS)}, so its split is unknown; refusing to guess.")
    if split == "TEST" and not allow_test:
        raise ValueError(f"{file}: pose_set {pose_set!r} is the TEST split, which stays untouched until a final claim. "
                         "Pass allow_test=true only for that final, approved evaluation.")
    if split == "TEST" and os.environ.get(TEST_ENV) != "1":
        raise ValueError(f"{file}: allow_test=true is not enough: the TEST split also needs the person running this server to set "
                         f"{TEST_ENV}=1 in the server's environment (an agent cannot grant itself the TEST split). Ask them if a final TEST "
                         "evaluation is really intended.")
    return split, pose_set


def _read(path: Any, criteria: str, allow_test: bool) -> dict[str, Any]:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("every eval_json entry must be a non-empty path")
    p = Path(path).expanduser()
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
    split, pose_set = _split(data, path, allow_test)
    _desc, count_path, dir_path, kind = CRITERIA[criteria]
    passed = _dig(data, count_path, path)
    per_dir = _dig(data, dir_path, path)
    cats = _dig(data, ("by_category",), path)
    if isinstance(passed, bool) or not isinstance(passed, (int, float)):
        raise ValueError(f"{path}: {'.'.join(count_path)} is not a number")
    by_dir: dict[str, int] = {}
    sizes: dict[str, int] = {}
    for d in DIRECTIONS:
        if d not in per_dir or d not in cats or "n" not in cats[d]:
            raise ValueError(f"{path}: expected direction {d!r} under {'.'.join(dir_path)} and by_category.{d}.n")
        sizes[d] = int(cats[d]["n"])
        value = per_dir[d]
        by_dir[d] = int(round(value * sizes[d])) if kind == "rates" else int(value)
    n = int(_dig(data, ("num_envs",), path))
    out = {"path": str(p), "checkpoint": data.get("checkpoint"), "task": data.get("task"), "split": split, "pose_set": pose_set,
           "n": n, "pass_count": int(passed), "by_direction": by_dir, "direction_n": sizes}
    if sum(by_dir.values()) != int(passed) or sum(sizes.values()) != n:
        out["consistency_warning"] = (f"per-direction passes sum to {sum(by_dir.values())} (n {sum(sizes.values())}) but the file reports "
                                      f"{int(passed)} of {n}; check the file before quoting it")
    return out


def _stats(runs: list[dict[str, Any]]) -> dict[str, Any]:
    counts = [r["pass_count"] for r in runs]
    return {"pass_counts": counts, "mean_pass": round(sum(counts) / len(counts), 2), "min_pass": min(counts), "max_pass": max(counts),
            "n": runs[0]["n"], "by_direction_mean": {d: round(sum(r["by_direction"][d] for r in runs) / len(runs), 2) for d in DIRECTIONS}}


def summarize(eval_json: Any, criteria: Any = None, label: Any = None, baseline_json: Any = None, baseline_label: Any = None,
              allow_test: bool = False) -> dict[str, Any]:
    key = _criteria(criteria)
    for name, value in (("eval_json", eval_json), ("baseline_json", baseline_json)):
        if value is not None and (not isinstance(value, list) or not value or len(value) > MAX_FILES):
            raise ValueError(f"{name} must be a list of 1 to {MAX_FILES} paths")
    if not eval_json:
        raise ValueError("Give eval_json: a list of evaluation-result JSON files (repeat evaluations of one checkpoint, or several).")
    runs = [_read(p, key, bool(allow_test)) for p in eval_json]
    splits = {r["split"] for r in runs}
    if len(splits) > 1:
        raise ValueError(f"eval_json mixes splits {sorted(splits)}; summarise one split at a time.")
    ns = {r["n"] for r in runs}
    if len(ns) > 1:
        raise ValueError(f"eval_json files have different pose counts {sorted(ns)}; they are not repeat runs of one set.")
    out: dict[str, Any] = {"label": label, "criteria": key, "criteria_definition": CRITERIA[key][0], "split": runs[0]["split"],
                           "pose_set": runs[0]["pose_set"], "runs": runs, **_stats(runs)}
    out["pass_fraction_mean"] = round(out["mean_pass"] / out["n"], 4)
    warnings = [r["consistency_warning"] for r in runs if "consistency_warning" in r]
    if warnings:
        out["warnings"] = warnings
    base_runs: list[dict[str, Any]] = []
    if baseline_json:
        base_runs = [_read(p, key, bool(allow_test)) for p in baseline_json]
        if {r["split"] for r in base_runs} != {runs[0]["split"]} or {r["n"] for r in base_runs} != {runs[0]["n"]}:
            raise ValueError("baseline_json is on a different split or pose count than eval_json, so the comparison would be meaningless.")
        base = {"label": baseline_label, "runs": base_runs, **_stats(base_runs)}
        base["delta_mean_pass"] = round(out["mean_pass"] - base["mean_pass"], 2)
        base["delta_by_direction"] = {d: round(out["by_direction_mean"][d] - base["by_direction_mean"][d], 2) for d in DIRECTIONS}
        out["baseline"] = base
    if out["split"] == "TEST" or any(r["split"] == "TEST" for r in (base_runs if baseline_json else [])):
        out["test_split_used"] = True
        out["warning"] = "The TEST split was scored because allow_test=true. It stays untouched until a final claim: do not tune against this number."
    return out

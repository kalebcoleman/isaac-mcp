"""Audit legacy names, or enforce narrowly reasoned exceptions, in tracked files."""

import argparse
from collections import Counter
import fnmatch
import json
from pathlib import Path
import re
import subprocess

PATTERNS = {
    "isaac-mcp": r"isaac-mcp", "isaac_mcp": r"isaac_mcp",
    "isaac.mcp": r"isaac\.mcp\.(?:bridge|panel)",
    "ISAAC_MCP": r"ISAAC_MCP_[A-Z0-9_]+", "isaac_help": r"isaac_help",
    "mcp__isaac__": r"mcp__isaac__", "ur5e-dt": r"ur5e-dt", "ur5e_dt": r"ur5e_dt",
    "UR5E_DT": r"UR5E_DT_[A-Z0-9_]+", "husarion": r"(?i:husarion)",
    "screen-mcp": r"screen-mcp", "TWIN_MCP": r"TWIN_MCP_[A-Z0-9_]+",
    "ISAAC_SIM": r"ISAAC_SIM_[A-Z0-9_]+",
    "physx-release": r"(?i:PhysX is the release target)",
    "newton-experimental": r"(?i:Newton is experimental)",
    "isaac-sim-mcp": r"isaac-sim-mcp", "omni-kit-mcp": r"omni-kit-mcp",
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".lock"}


def area(path):
    parts = path.split("/")
    if len(parts) == 1:
        return "root files"
    if parts[0] in {"mcp", "exts"} and len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0]


def scan(root):
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    hits = []
    for path in filter(None, paths):
        for label, expression in PATTERNS.items():
            for match in re.finditer(expression, path):
                hits.append(dict(pattern=label, path=path, line=0, text=match.group(), kind="path"))
        source = root / path
        if source.is_symlink() or source.suffix.lower() in SKIP_SUFFIXES or source.name in {"package-lock.json", "npm-shrinkwrap.json"}:
            continue
        if not source.is_file():
            continue
        try:
            text = source.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        if "\0" in text:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for label, expression in PATTERNS.items():
                for match in re.finditer(expression, line):
                    hits.append(dict(pattern=label, path=path, line=number, text=match.group(), kind="content"))
    return hits


def audit(hits):
    report = {}
    for label in PATTERNS:
        content = [hit for hit in hits if hit["pattern"] == label and hit["kind"] == "content"]
        report[label] = {
            "content_hits": len(content),
            "areas": dict(sorted(Counter(area(hit["path"]) for hit in content).items())),
            "tracked_paths": len({hit["path"] for hit in hits if hit["pattern"] == label and hit["kind"] == "path"}),
        }
    return {"patterns": report, "environment_names": {
        label: dict(sorted(Counter(hit["text"] for hit in hits if hit["pattern"] == label and hit["kind"] == "content").items()))
        for label in ("ISAAC_MCP", "UR5E_DT")
    }}


def enforce(hits, allowlist):
    import tomllib

    entries = tomllib.loads(allowlist.read_text()).get("allow", [])
    errors = []
    covered = set()
    for index, entry in enumerate(entries, 1):
        if entry.get("pattern") not in PATTERNS or not entry.get("path") or not entry.get("reason", "").strip():
            errors.append(f"allowlist entry {index}: pattern, path and written reason are required")
            continue
        matching = {number for number, hit in enumerate(hits)
                    if hit["pattern"] == entry["pattern"] and fnmatch.fnmatchcase(hit["path"], entry["path"])
                    and ("literal" not in entry or hit["text"] == entry["literal"])}
        if not matching:
            errors.append(f"allowlist entry {index}: matches nothing ({entry['path']})")
        covered.update(matching)
    errors.extend(f"{hit['path']}:{hit['line']}: {hit['pattern']}: {hit['text']}"
                  for number, hit in enumerate(hits) if number not in covered)
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--audit", action="store_true")
    mode.add_argument("--enforce", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--allowlist", type=Path, default=Path(__file__).with_name("name_allowlist.toml"))
    args = parser.parse_args(argv)
    hits = scan(args.root)
    report = audit(hits)
    report["errors"] = enforce(hits, args.allowlist) if args.enforce else []
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for label, counts in report["patterns"].items():
            print(f"{label}: {counts['content_hits']} content hits, {counts['tracked_paths']} tracked paths; {counts['areas']}")
        for label, names in report["environment_names"].items():
            print(f"{label} names: {names}")
        for error in report["errors"]:
            print(error)
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail when a non-redistributable asset is tracked in the repository.

Companion to ``docs/provenance/ASSETS.md`` and ``exts/ur5e-dt/assets-manifest.json``.
The manifest names the files that must be fetched or supplied locally; this check
also rejects leftover Poliigon textures and foreign home-directory paths.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "exts" / "ur5e-dt" / "assets"
MANIFEST = REPO / "exts" / "ur5e-dt" / "assets-manifest.json"

EXCLUDED_EXTRA = (
    "exts/ur5e-dt/assets/gripper/RG2.usd",
    "exts/ur5e-dt/assets/gripper/husarion_rg2/husarion_rg2_fixed.usd",
)
EXCLUDED_PREFIXES = (
    "exts/ur5e-dt/assets/robot/",
    "exts/ur5e-dt/assets/sensors/",
    "exts/ur5e-dt/assets/workstation/",
    "exts/ur5e-dt/assets/objects/fmb4/materials/oak/",
)
TEXT_SUFFIXES = {".usda", ".json", ".md", ".mdl", ".mtlx", ".txt", ".xml", ".py"}


def tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "exts/ur5e-dt/assets"],
        cwd=REPO,
        check=True,
        capture_output=True,
    )
    return {item.decode() for item in result.stdout.split(b"\0") if item}


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    tracked = tracked_files()
    errors: list[str] = []

    excluded = {item["target"] for item in manifest.get("fetch", []) for item in item["files"]}
    excluded.update(item["target"] for item in manifest.get("manual", []))
    excluded.update(EXCLUDED_EXTRA)
    for path in sorted(excluded):
        repo_path = f"exts/ur5e-dt/assets/{path}"
        if repo_path in tracked:
            errors.append(f"non-redistributable asset is tracked: {repo_path}")

    for path in sorted(tracked):
        lower = path.lower()
        if path.startswith(EXCLUDED_PREFIXES):
            errors.append(f"non-redistributable asset is tracked: {path}")
        if "poliigon" in lower:
            errors.append(f"Poliigon asset is tracked: {path}")
        if lower.endswith((".usd", ".usda", ".usdc", ".usdz")) and not path.startswith(
            "exts/ur5e-dt/assets/"
        ):
            errors.append(f"USD asset outside the audited folder: {path}")

    for path in sorted(tracked):
        if Path(path).suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = (REPO / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "/home/" in text:
            errors.append(f"embedded home-directory path in {path}")
        if "poliigon" in text.lower():
            errors.append(f"Poliigon reference in {path}")

    for item in manifest.get("bundled", []):
        path = ASSETS / item["path"]
        if not path.exists():
            errors.append(f"manifest lists a missing bundled asset: {item['path']}")
        notice = item.get("notice")
        if notice and not (REPO / notice).is_file():
            errors.append(f"manifest notice is missing: {notice}")

    for required in ("THIRD-PARTY-NOTICES.md", "docs/provenance/husarion-ur-onrobot-rg2-LICENSE"):
        if not (REPO / required).is_file():
            errors.append(f"required notice file is missing: {required}")

    if errors:
        print("Asset checks failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(
        "Asset checks passed "
        f"({len(manifest.get('bundled', []))} bundled groups, "
        f"{len(excluded)} excluded paths absent, {len(tracked)} tracked asset files)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

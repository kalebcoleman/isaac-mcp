#!/usr/bin/env python3
"""Fail when an asset is tracked in the repository.

The repository is code-only: every asset the twin uses (robot, sensor, workstation,
gripper, objects, layouts) lives in each user's local asset folder (see
``docs/ASSETS.md``), so nothing with a redistribution question is ever pushed. This
check fails when:

* anything is tracked under ``exts/ur5e-dt/assets/`` (that folder is git-ignored and
  only exists in old checkouts; ``scripts/isaac_assets.py migrate`` moves it out);
* a 3D, robot-description or material file is tracked anywhere;
* an image is tracked outside the documentation and the extensions' UI ``data/`` folders;
* the manifest is malformed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "exts" / "ur5e-dt" / "assets"
MANIFEST = REPO / "exts" / "ur5e-dt" / "assets-manifest.json"

ASSET_SUFFIXES = {
    ".usd", ".usda", ".usdc", ".usdz", ".urdf", ".xacro", ".sdf", ".mjcf",
    ".obj", ".stl", ".dae", ".fbx", ".glb", ".gltf", ".ply", ".mtl", ".mdl", ".mtlx",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".exr", ".hdr", ".tga", ".tif", ".tiff"}
IMAGE_ALLOWED_PREFIXES = ("docs/",)
IMAGE_ALLOWED_PARTS = ("/data/",)  # extension UI icons and previews, e.g. exts/<ext>/data/icon.png


def tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, check=True, capture_output=True
    )
    return {item.decode() for item in result.stdout.split(b"\0") if item}


def main() -> int:
    errors: list[str] = []
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        manifest = {}
        errors.append(f"cannot read the asset manifest: {exc}")
    tracked = tracked_files()
    folder = "exts/ur5e-dt/assets/"

    for path in sorted(tracked):
        suffix = Path(path).suffix.lower()
        if path.startswith(folder):
            errors.append(f"asset tracked in the repository (assets live in the local asset folder): {path}")
        elif suffix in ASSET_SUFFIXES:
            errors.append(f"asset file tracked in the repository: {path}")
        elif suffix in IMAGE_SUFFIXES and not (
            path.startswith(IMAGE_ALLOWED_PREFIXES) or any(part in f"/{path}" for part in IMAGE_ALLOWED_PARTS)
        ):
            errors.append(f"image tracked outside docs/ or an extension data/ folder: {path}")

    for group in manifest.get("fetch", []):
        for entry in group.get("files", []):
            for key in ("target", "source", "sha256"):
                if not entry.get(key):
                    errors.append(f"manifest fetch entry without {key}: {entry}")
    for item in manifest.get("local", []):
        if not item.get("path") or not item.get("licence"):
            errors.append(f"manifest local entry needs path and licence: {item}")

    if errors:
        print("Asset checks failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(
        "Asset checks passed (repository is code-only: "
        f"{len(tracked)} tracked files, no assets; "
        f"{sum(len(g.get('files', [])) for g in manifest.get('fetch', []))} fetchable and "
        f"{len(manifest.get('local', []))} locally supplied asset groups described)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

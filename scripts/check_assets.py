#!/usr/bin/env python3
"""Fail when an asset is tracked in the repository.

The repository is code-only: every asset (robot, sensor, workstation, gripper,
objects, layouts) lives in each user's asset root as a pack (see
``docs/ASSET-PACKS.md``), so nothing with a redistribution question is ever pushed. This
check fails when:

* anything is tracked under ``exts/ur5e-dt/assets/`` (a folder old checkouts kept; it is git-ignored);
* a 3D, robot-description or material file is tracked anywhere;
* an image is tracked outside the documentation and the extensions' UI ``data/`` folders.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

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

    if errors:
        print("Asset checks failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"Asset checks passed (repository is code-only: {len(tracked)} tracked files, no assets).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

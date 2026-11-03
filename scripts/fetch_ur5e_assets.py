#!/usr/bin/env python3
"""Download NVIDIA's public UR5e and RealSense files into the local asset folder.

The repository ships no assets. NVIDIA's public UR5e robot and RealSense D455 models
(and their sublayers and materials) are downloaded from NVIDIA's public asset root into
the ``ur5e-dt`` pack folder of the primary asset folder: the first entry of
``$ISAAC_MCP_ASSET_PATH`` (deprecated alias ``$UR5E_DT_ASSET_CACHE``), otherwise the
``isaac-mcp-assets`` folder next to the checkout. A file you pinned yourself (for
example a lab-modified ``robot/ur5e.usd``) is never replaced. Downloaded files are
checked against the manifest hash. With these files local, set
``ISAAC_MCP_ASSET_STRICT=1`` (deprecated alias ``UR5E_DT_OFFLINE``) so nothing is ever
loaded from NVIDIA's server at run time.

Examples::

    python3 scripts/fetch_ur5e_assets.py
    python3 scripts/fetch_ur5e_assets.py --check
    python3 scripts/fetch_ur5e_assets.py --cache-dir <asset-folder>
    python3 scripts/fetch_ur5e_assets.py --asset-root /mnt/isaac-assets

The Vention bench has no public source and is not fetched; add your own copy with
``python3 scripts/isaac_assets.py add``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXT_DIR = REPO / "exts" / "ur5e-dt"


def _load_asset_cache():
    spec = importlib.util.spec_from_file_location(
        "ur5e_asset_cache", EXT_DIR / "ur5e_dt" / "asset_cache.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


asset_cache = _load_asset_cache()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(path: Path, entry: dict) -> str | None:
    """Return an error string when *path* does not match *entry*."""
    expected_size = entry.get("size")
    if expected_size is not None and path.stat().st_size != expected_size:
        return f"size {path.stat().st_size} != {expected_size}"
    expected_sha = entry.get("sha256")
    if expected_sha and sha256(path) != expected_sha:
        return f"sha256 {sha256(path)} != {expected_sha}"
    return None


def _download(url: str, destination: Path, entry: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=destination.name + ".", dir=destination.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url, timeout=120) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise RuntimeError(f"HTTP {status} for {url}")
            shutil.copyfileobj(response, out)
        error = _verify(tmp, entry)
        if error:
            raise RuntimeError(f"{url}: {error}")
        tmp.replace(destination)
    finally:
        tmp.unlink(missing_ok=True)


def _copy(source: Path, destination: Path, entry: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".tmp")
    try:
        shutil.copy2(source, tmp)
        error = _verify(tmp, entry)
        if error:
            raise RuntimeError(f"{source}: {error}")
        tmp.replace(destination)
    finally:
        tmp.unlink(missing_ok=True)


def _iter_files(manifest: dict):
    for group in manifest.get("fetch", []):
        for entry in group.get("files", []):
            yield group, entry


def _rel(entry: dict) -> str:
    return entry.get("cache_path", entry["target"])


def _pinned(store: Path, entry: dict) -> bool:
    """True when the user pinned their own copy of this file in the store's pack folder."""
    pins = asset_cache.pins(store / asset_cache.PACK)
    return _rel(entry) in pins or entry["target"] in pins


def _existing(store: Path, entry: dict) -> Path:
    """The pack-folder copy, or the flat legacy copy when only that exists."""
    pack = store / asset_cache.PACK / _rel(entry)
    flat = store / _rel(entry)
    return flat if not pack.is_file() and flat.is_file() else pack


def check(manifest: dict, cache: Path) -> int:
    missing: list[str] = []
    bad: list[str] = []
    for _, entry in _iter_files(manifest):
        if _pinned(cache, entry):
            continue  # the user's own pinned copy; isaac_assets.py doctor checks it
        path = _existing(cache, entry)
        if not path.is_file():
            missing.append(entry["target"])
            continue
        error = _verify(path, entry)
        if error:
            bad.append(f"{entry['target']}: {error}")
    if not missing and not bad:
        print(f"Asset cache OK: {cache} ({len(list(_iter_files(manifest)))} files).")
        return 0
    for item in missing:
        print(f"MISSING {item}")
    for item in bad:
        print(f"MISMATCH {item}")
    return 1


def fetch(manifest: dict, cache: Path, root: str, force: bool) -> int:
    root_path = Path(root).expanduser()
    use_local = root_path.is_dir()
    fetched = skipped = 0
    total_bytes = 0
    kept = 0
    for group, entry in _iter_files(manifest):
        if _pinned(cache, entry):
            kept += 1  # never replace a file the user pinned (e.g. a lab-modified robot)
            continue
        destination = cache / asset_cache.PACK / _rel(entry)
        if destination.is_file() and not force:
            if _verify(destination, entry) is None:
                skipped += 1
                continue
        source = entry["source"].lstrip("/")
        if use_local:
            origin = root_path / source
            if not origin.is_file():
                print(f"ERROR {group['id']}: {origin} is missing", file=sys.stderr)
                return 1
            _copy(origin, destination, entry)
        else:
            url = f"{root.rstrip('/')}/{source}"
            try:
                _download(url, destination, entry)
            except Exception as exc:  # noqa: BLE001 - report the URL and stop
                print(f"ERROR {group['id']}: {exc}", file=sys.stderr)
                return 1
        fetched += 1
        total_bytes += destination.stat().st_size
        print(f"  {entry['target']}")
    print(
        f"Fetched {fetched} file(s), skipped {skipped} up-to-date file(s) into "
        f"{cache / asset_cache.PACK} ({total_bytes / 1048576:.1f} MB downloaded)."
    )
    if kept:
        print(f"Kept {kept} file(s) you pinned yourself (not replaced by NVIDIA's copy).")
    for item in manifest.get("manual", []):
        print(f"Manual: {item['target']} — {item['hint']}")
    print("Air-gapped runs: set ISAAC_MCP_ASSET_STRICT=1 after this fetch.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=None, help="asset folder to download into (default: the primary asset folder)")
    parser.add_argument(
        "--asset-root",
        default=None,
        help="NVIDIA asset root URL or a local directory to copy from",
    )
    parser.add_argument("--check", action="store_true", help="verify the cache and exit")
    parser.add_argument("--force", action="store_true", help="re-download files even when the cached copy matches")
    args = parser.parse_args()

    manifest = asset_cache.manifest()
    cache = (args.cache_dir or asset_cache.cache_dir()).expanduser().resolve()
    root = args.asset_root or asset_cache.asset_root()
    print(f"Cache: {cache}")
    print(f"Source: {root}")
    if args.check:
        return check(manifest, cache)
    return fetch(manifest, cache, root, args.force)


if __name__ == "__main__":
    raise SystemExit(main())

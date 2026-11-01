"""Resolve UR5e twin assets that cannot live in the public repository.

The twin ships only assets whose licences permit redistribution (see
``docs/provenance/ASSETS.md``).  The NVIDIA UR5e and RealSense models and the
Vention bench are resolved in this order:

1. ``exts/ur5e-dt/assets/<path>`` — a local drop-in.  The excluded paths are
   git-ignored, so an operator can place their own licensed copy here.
2. ``$UR5E_DT_ASSET_CACHE`` (default ``~/.cache/ur5e-dt/assets``) — populated
   by ``scripts/fetch_ur5e_assets.py`` for offline runs.
3. The pinned NVIDIA asset root for entries listed under ``remote`` in
   ``assets-manifest.json``.

Local files are returned as ``file://`` URIs and the remote fallback as an
``https://`` URI.  Isaac Sim's asset resolver accepts both.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# exts/ur5e-dt/ur5e_dt/asset_cache.py -> exts/ur5e-dt
EXT_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = EXT_DIR / "assets"
MANIFEST_PATH = EXT_DIR / "assets-manifest.json"

DEFAULT_ASSET_ROOT = (
    "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1"
)
CACHE_ENV = "UR5E_DT_ASSET_CACHE"
ROOT_ENV = "UR5E_DT_ASSET_ROOT"


class AssetMissing(FileNotFoundError):
    """Raised when an asset is neither bundled, cached, nor available remotely."""

    def __init__(self, relpath: str):
        self.relpath = relpath
        candidates = [str(ASSETS_DIR / relpath), str(cache_dir() / relpath)]
        remote = remote_uri(relpath)
        lines = [
            f"Asset not available: {relpath}",
            f"  bundled: {candidates[0]}",
            f"  cache:   {candidates[1]}",
            f"  remote:  {remote or '(none: this asset must be supplied locally)'}",
        ]
        if remote:
            lines.append(
                "Run `python3 scripts/fetch_ur5e_assets.py` to populate the cache, or set "
                f"{CACHE_ENV} to a directory that contains this file."
            )
        else:
            lines.append(
                f"Place your licensed copy at {candidates[1]}, or set {CACHE_ENV} to a "
                "directory that contains it."
            )
        super().__init__("\n".join(lines))


def manifest() -> dict:
    """Return the parsed asset manifest."""
    with MANIFEST_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def cache_dir() -> Path:
    """Return the operator asset cache (never inside the repository by default)."""
    override = os.environ.get(CACHE_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return (base / "ur5e-dt" / "assets").resolve()


def asset_root() -> str:
    """Return the base URL or directory for NVIDIA-hosted assets."""
    override = os.environ.get(ROOT_ENV, "").strip()
    if override:
        return override.rstrip("/")
    return str(manifest().get("asset_root") or DEFAULT_ASSET_ROOT).rstrip("/")


def find_local(relpath: str) -> Path | None:
    """Return the first local copy of *relpath*, or None."""
    for base in (ASSETS_DIR, cache_dir()):
        candidate = base / relpath
        if candidate.is_file():
            return candidate
    return None


def remote_uri(relpath: str) -> str | None:
    """Return the pinned NVIDIA URL for *relpath*, if the manifest allows one."""
    source = manifest().get("remote", {}).get(relpath)
    if not source:
        return None
    return f"{asset_root()}/{source.lstrip('/')}"


def resolve_uri(relpath: str) -> str:
    """Resolve an asset to a ``file://`` or ``https://`` URI.

    Use this at USD-reference sites.  Raises :class:`AssetMissing` when the
    asset is neither local nor allowed to be fetched.
    """
    local = find_local(relpath)
    if local is not None:
        return local.as_uri()
    remote = remote_uri(relpath)
    if remote is not None:
        return remote
    raise AssetMissing(relpath)


def resolve_path(relpath: str) -> Path:
    """Resolve an asset to a local path, raising :class:`AssetMissing` otherwise."""
    local = find_local(relpath)
    if local is None:
        raise AssetMissing(relpath)
    return local


def resolve_dir(relpath: str) -> Path:
    """Resolve a local asset directory, raising :class:`AssetMissing` otherwise."""
    for base in (ASSETS_DIR, cache_dir()):
        candidate = base / relpath
        if candidate.is_dir():
            return candidate
    raise AssetMissing(relpath)

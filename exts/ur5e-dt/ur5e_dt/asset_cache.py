"""Resolve UR5e twin assets that cannot live in the public repository.

The twin ships only assets whose licences permit redistribution (see
``docs/provenance/ASSETS.md``).  The NVIDIA UR5e and RealSense models and the
Vention bench are resolved in this order:

1. ``exts/ur5e-dt/assets/<path>`` — a local drop-in.  The excluded paths are
   git-ignored, so an operator can place their own licensed copy here.
2. ``$UR5E_DT_ASSET_CACHE`` (default ``~/.cache/ur5e-dt/assets``) — populated
   by ``scripts/fetch_ur5e_assets.py`` for offline runs.  Files listed in the
   manifest are checked against their recorded size and SHA-256 before use.
3. The pinned NVIDIA asset root for entries listed under ``remote`` in
   ``assets-manifest.json``.

Set ``UR5E_DT_OFFLINE=1`` to refuse the remote fallback: a missing cache entry
then raises :class:`AssetMissing` with the fetch command instead of returning a
URL that cannot be reached.

Local files are returned as ``file://`` URIs and the remote fallback as an
``https://`` URI.  Isaac Sim's asset resolver accepts both.
"""

from __future__ import annotations

import hashlib
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
OFFLINE_ENV = "UR5E_DT_OFFLINE"


class AssetMissing(FileNotFoundError):
    """Raised when an asset is neither bundled, cached, nor available remotely."""

    def __init__(self, relpath: str, *, offline: bool = False):
        self.relpath = relpath
        candidates = [str(ASSETS_DIR / relpath), str(cache_dir() / relpath)]
        remote = remote_uri(relpath)
        heading = "Asset not available offline" if offline else "Asset not available"
        lines = [
            f"{heading}: {relpath}",
            f"  bundled: {candidates[0]}",
            f"  cache:   {candidates[1]}",
            f"  remote:  {remote or '(none: this asset must be supplied locally)'}",
        ]
        if offline:
            lines.append(
                f"{OFFLINE_ENV} is set but no cached copy exists. Run "
                "`python3 scripts/fetch_ur5e_assets.py` while online, or unset "
                f"{OFFLINE_ENV} to allow the remote fallback."
            )
        elif remote:
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


class AssetCorrupt(FileNotFoundError):
    """Raised when a cached asset no longer matches its manifest entry."""

    def __init__(self, relpath: str, path: Path, expected, actual, detail: str):
        self.relpath = relpath
        self.path = path
        super().__init__(
            "\n".join(
                [
                    f"Cached asset is corrupt: {relpath}",
                    f"  path:     {path}",
                    f"  expected: {expected}",
                    f"  actual:   {actual}",
                    f"  detail:   {detail}",
                    "Run `python3 scripts/fetch_ur5e_assets.py --force` to replace it.",
                ]
            )
        )


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


def offline() -> bool:
    """Return True when the operator asked for a cache-only run."""
    return os.environ.get(OFFLINE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def find_local(relpath: str) -> Path | None:
    """Return the first local copy of *relpath*, or None."""
    for base in (ASSETS_DIR, cache_dir()):
        candidate = base / relpath
        if candidate.is_file():
            return candidate
    return None


def expected_asset(relpath: str) -> dict | None:
    """Return the manifest fetch entry for *relpath*, if one exists."""
    for group in manifest().get("fetch", []):
        for entry in group.get("files", []):
            if entry.get("target") == relpath:
                return entry
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_local(relpath: str, path: Path) -> None:
    """Check a manifest-tracked local file against its recorded hash and size."""
    entry = expected_asset(relpath)
    if not entry:
        return
    expected_size = entry.get("size")
    actual_size = path.stat().st_size
    if expected_size is not None and actual_size != expected_size:
        raise AssetCorrupt(
            relpath, path, f"{expected_size} bytes", f"{actual_size} bytes", "size mismatch"
        )
    expected_sha = entry.get("sha256")
    if expected_sha:
        actual_sha = _sha256(path)
        if actual_sha != expected_sha:
            raise AssetCorrupt(
                relpath,
                path,
                expected_sha,
                actual_sha,
                "sha256 mismatch",
            )


def remote_uri(relpath: str) -> str | None:
    """Return the pinned NVIDIA URL for *relpath*, if the manifest allows one."""
    source = manifest().get("remote", {}).get(relpath)
    if not source:
        return None
    return f"{asset_root()}/{source.lstrip('/')}"


def resolve_uri(relpath: str) -> str:
    """Resolve an asset to a ``file://`` or ``https://`` URI.

    Use this at USD-reference sites.  Local files are verified against the
    manifest before use.  Raises :class:`AssetMissing` when the asset is
    neither local nor allowed to be fetched, and :class:`AssetCorrupt` when a
    cached file no longer matches its recorded hash.
    """
    local = find_local(relpath)
    if local is not None:
        verify_local(relpath, local)
        return local.as_uri()
    remote = remote_uri(relpath)
    if remote is not None and not offline():
        return remote
    raise AssetMissing(relpath, offline=remote is not None and offline())


def resolve_path(relpath: str) -> Path:
    """Resolve an asset to a local path, raising :class:`AssetMissing` otherwise."""
    local = find_local(relpath)
    if local is None:
        remote = remote_uri(relpath)
        raise AssetMissing(relpath, offline=remote is not None and offline())
    verify_local(relpath, local)
    return local


def resolve_dir(relpath: str) -> Path:
    """Resolve a local asset directory, raising :class:`AssetMissing` otherwise."""
    for base in (ASSETS_DIR, cache_dir()):
        candidate = base / relpath
        if candidate.is_dir():
            return candidate
    raise AssetMissing(relpath)

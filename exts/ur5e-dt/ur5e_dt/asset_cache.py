"""Resolve UR5e twin assets that cannot live in the public repository.

The twin ships only assets whose licences permit redistribution (see
``docs/provenance/ASSETS.md``).  The NVIDIA UR5e and RealSense models and the
Vention bench are resolved in this order:

1. Project stores — the ``asset_path`` of the nearest ``isaac-mcp.json`` (see
   ``docs/ASSETS.md``).  They let one project override an asset without touching
   anything else.
2. ``exts/ur5e-dt/assets/<path>`` — a local drop-in.  The excluded paths are
   git-ignored, so an operator can place their own licensed copy here.  A
   drop-in is used as-is: it may come from another Isaac Sim release, so it is
   not compared with the manifest hashes.
3. User stores — ``$ISAAC_MCP_ASSET_PATH`` (an ``os.pathsep``-separated list; without it,
   ``<workspace>/assets`` when ``$ISAAC_MCP_WORKSPACE`` is set and that folder exists, then
   the old default ``~/.cache/ur5e-dt/assets``), populated by
   ``scripts/fetch_ur5e_assets.py`` or ``scripts/isaac_assets.py add``.  Inside
   a store an asset is looked up under ``<store>/ur5e-dt/<path>`` (the pack
   folder, preferred) and then ``<store>/<path>`` (the flat legacy layout).
4. The pinned NVIDIA asset root for entries listed under ``remote`` in
   ``assets-manifest.json``.

A file in a store is verified before use.  If the folder that holds it has a
``SHA256SUMS`` file listing the file, that user pin decides (a lab-modified
robot differs from NVIDIA's, so the NVIDIA manifest hash would wrongly reject
it).  Otherwise a file listed in the manifest, and every file of the same
fetch group, is checked against its recorded size and SHA-256.

Set ``ISAAC_MCP_ASSET_STRICT=1`` (or ``"strict": true`` in the project file) to
refuse the remote fallback: a missing asset then raises :class:`AssetMissing`
with guidance instead of returning a URL.  The older ``UR5E_DT_ASSET_CACHE``,
``UR5E_DT_ASSET_ROOT`` and ``UR5E_DT_OFFLINE`` names still work as deprecated
aliases of ``ISAAC_MCP_ASSET_PATH``, ``ISAAC_MCP_ASSET_REMOTE_ROOT`` and
``ISAAC_MCP_ASSET_STRICT``.

Local files are returned as ``file://`` URIs and the remote fallback as an
``https://`` URI.  Isaac Sim's asset resolver accepts both.  This module uses
only the standard library because it is loaded both inside Kit and by path from
host scripts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

LOG = logging.getLogger(__name__)

# exts/ur5e-dt/ur5e_dt/asset_cache.py -> exts/ur5e-dt
EXT_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = EXT_DIR / "assets"
MANIFEST_PATH = EXT_DIR / "assets-manifest.json"

DEFAULT_ASSET_ROOT = (
    "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1"
)

# The folder name this module's assets use inside a store.
PACK = "ur5e-dt"

# Preferred names.
PATH_ENV = "ISAAC_MCP_ASSET_PATH"
STRICT_ENV = "ISAAC_MCP_ASSET_STRICT"
REMOTE_ROOT_ENV = "ISAAC_MCP_ASSET_REMOTE_ROOT"
PROJECT_ENV = "ISAAC_MCP_PROJECT"
WORKSPACE_ENV = "ISAAC_MCP_WORKSPACE"
# Deprecated aliases, still honoured.
CACHE_ENV = "UR5E_DT_ASSET_CACHE"
ROOT_ENV = "UR5E_DT_ASSET_ROOT"
OFFLINE_ENV = "UR5E_DT_OFFLINE"

PROJECT_FILE = "isaac-mcp.json"
PINS_FILE = "SHA256SUMS"

_TRUE = {"1", "true", "yes", "on"}
_HEX = frozenset("0123456789abcdefABCDEF")
_WARNED: set[str] = set()


class AssetMissing(FileNotFoundError):
    """Raised when an asset is neither bundled, stored, nor available remotely."""

    def __init__(self, relpath: str, *, offline: bool = False):
        self.relpath = relpath
        candidates = [str(ASSETS_DIR / relpath), str(cached_path(relpath))]
        remote = remote_uri(relpath)
        heading = "Asset not available offline" if offline else "Asset not available"
        lines = [
            f"{heading}: {relpath}",
            f"  bundled: {candidates[0]}",
            f"  cache:   {candidates[1]}",
            f"  stores:  {', '.join(str(s) for s in stores())}",
            f"  remote:  {remote or '(none: this asset must be supplied locally)'}",
        ]
        if offline:
            lines.append(
                f"{STRICT_ENV} (or {OFFLINE_ENV}) is set but no local copy exists. Run "
                "`python3 scripts/fetch_ur5e_assets.py` while online, or unset "
                f"{STRICT_ENV} to allow the remote fallback."
            )
        elif remote:
            lines.append(
                "Run `python3 scripts/fetch_ur5e_assets.py` to populate the cache, or set "
                f"{PATH_ENV} to a directory that contains this file."
            )
        else:
            lines.append(
                f"Place your licensed copy at {PACK}/{relpath} inside a store, e.g. "
                f"`python3 scripts/isaac_assets.py add <file-or-archive>` (stores are "
                f"listed above; set {PATH_ENV} to choose one)."
            )
        super().__init__("\n".join(lines))


class AssetCorrupt(FileNotFoundError):
    """Raised when a stored asset no longer matches its pinned or manifest hash."""

    def __init__(
        self, relpath: str, path: Path, expected, actual, detail: str, *, fix: str | None = None
    ):
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
                    fix or "Run `python3 scripts/fetch_ur5e_assets.py --force` to replace it.",
                ]
            )
        )


def manifest() -> dict:
    """Return the parsed asset manifest."""
    with MANIFEST_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _env(primary: str, legacy: str) -> str:
    """Return a setting from its preferred name or, with a warning, its old name."""
    value = os.environ.get(primary, "").strip()
    if value:
        return value
    value = os.environ.get(legacy, "").strip()
    if value and legacy not in _WARNED:
        _WARNED.add(legacy)
        LOG.warning("%s is deprecated; use %s", legacy, primary)
    return value


# ---------------------------------------------------------------------------
# Project file and stores
# ---------------------------------------------------------------------------


def project_file() -> Path | None:
    """Return the active ``isaac-mcp.json``, or None.

    ``$ISAAC_MCP_PROJECT`` (a project directory or the file itself) wins;
    otherwise the file is searched for in the working directory and its parents.
    """
    override = os.environ.get(PROJECT_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        path = path / PROJECT_FILE if path.is_dir() else path
        return path.resolve() if path.is_file() else None
    try:
        here = Path.cwd().resolve()
    except OSError:
        return None
    for directory in (here, *here.parents):
        candidate = directory / PROJECT_FILE
        if candidate.is_file():
            return candidate
    return None


def _project_config() -> tuple[dict, Path | None]:
    path = project_file()
    if path is None:
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read project file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Project file {path} must contain a JSON object")
    return data, path.parent


def project_stores() -> list[Path]:
    """Return the project's asset stores, relative paths resolved against its file."""
    data, root = _project_config()
    entries = data.get("asset_path", [])
    if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
        raise ValueError(f"'asset_path' in {project_file()} must be a list of strings")
    assert root is not None or not entries
    return [(root / Path(e).expanduser()).resolve() for e in entries if e.strip()]


def _default_store() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return (base / "ur5e-dt" / "assets").resolve()


def workspace_store() -> Path | None:
    """Return ``<workspace>/assets`` when ``$ISAAC_MCP_WORKSPACE`` names a folder that has one."""
    raw = os.environ.get(WORKSPACE_ENV, "").strip()
    if not raw:
        return None
    store = (Path(raw).expanduser() / "assets").resolve()
    return store if store.is_dir() else None


def user_stores() -> list[Path]:
    """Return the user's stores.

    ``$ISAAC_MCP_ASSET_PATH`` (or its deprecated alias) replaces the defaults. Without it
    the stores are ``<workspace>/assets`` (if ``$ISAAC_MCP_WORKSPACE`` points at a
    workspace that has one) followed by the old default cache, so nothing that already
    lives in ``~/.cache/ur5e-dt/assets`` stops resolving.
    """
    raw = _env(PATH_ENV, CACHE_ENV)
    found = [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]
    if found:
        return found
    workspace = workspace_store()
    return ([workspace] if workspace else []) + [_default_store()]


def stores() -> list[Path]:
    """Return every store searched for local copies, project stores first."""
    return project_stores() + user_stores()


def cache_dir() -> Path:
    """Return the operator asset cache, the store the fetch script writes to.

    It is the first user store and is never inside the repository by default.
    """
    return user_stores()[0]


def asset_root() -> str:
    """Return the base URL or directory for NVIDIA-hosted assets."""
    override = _env(REMOTE_ROOT_ENV, ROOT_ENV)
    if override:
        return override.rstrip("/")
    return str(manifest().get("asset_root") or DEFAULT_ASSET_ROOT).rstrip("/")


def offline() -> bool:
    """Return True when remote fallbacks are refused.

    The environment decides when it is set (``0`` turns strict mode off even if
    the project asks for it); otherwise the project file's ``strict`` does.
    """
    raw = _env(STRICT_ENV, OFFLINE_ENV)
    if raw:
        return raw.lower() in _TRUE
    data, _ = _project_config()
    return data.get("strict") is True


def _bases(store: Path) -> tuple[Path, Path]:
    """Folders inside a store that hold assets: the pack folder, then the flat store."""
    return store / PACK, store


def _search_bases() -> list[tuple[Path, str]]:
    """Return ``(folder, layer)`` pairs in lookup order."""
    found: list[tuple[Path, str]] = []
    for store in project_stores():
        found.extend((base, "project") for base in _bases(store))
    found.append((ASSETS_DIR, "dropin"))
    for store in user_stores():
        found.extend((base, "store") for base in _bases(store))
    return found


def _locate(relpath: str) -> tuple[Path, str, Path] | None:
    """Return ``(file, layer, base)`` for the first local copy of *relpath*."""
    for base, layer in _search_bases():
        names = [relpath]
        if layer != "dropin":
            # A manifest-tracked asset must sit at its canonical layout, because its
            # sublayers use relative references. A file at the logical name is only
            # accepted when the user pinned it.
            canonical = _cache_relative(relpath).as_posix()
            names = [canonical]
            if canonical != relpath and pins(base).get(relpath):
                names.append(relpath)
        for name in names:
            candidate = base / name
            if candidate.is_file():
                return candidate, layer, base
    return None


def find_local(relpath: str) -> Path | None:
    """Return the first local copy of *relpath*, or None."""
    found = _locate(relpath)
    return found[0] if found else None


def _store_base(path: Path) -> Path | None:
    """Return the store folder that holds *path*, or None for a drop-in or stray file."""
    resolved = path.resolve()
    best: Path | None = None
    for store in stores():
        for base in _bases(store):
            try:
                resolved.relative_to(base)
            except ValueError:
                continue
            if best is None or len(base.parts) > len(best.parts):
                best = base
    return best


def _from_cache(path: Path) -> bool:
    """True when *path* is a store's copy rather than a drop-in."""
    return _store_base(path) is not None


def fetch_group(relpath: str) -> dict | None:
    """Return the manifest fetch group that contains *relpath*, if any."""
    for group in manifest().get("fetch", []):
        if any(entry.get("target") == relpath for entry in group.get("files", [])):
            return group
    return None


def expected_asset(relpath: str) -> dict | None:
    """Return the manifest fetch entry for *relpath*, if one exists."""
    for group in manifest().get("fetch", []):
        for entry in group.get("files", []):
            if entry.get("target") == relpath:
                return entry
    return None


def _cache_relative(relpath: str) -> Path:
    entry = expected_asset(relpath) or {}
    relative = Path(entry.get("cache_path", relpath))
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("Manifest cache path must stay relative to the asset cache")
    return relative


def cached_path(relpath: str, base: Path | None = None) -> Path:
    """Keep source-relative dependencies anchored inside the operator cache.

    The logical twin name remains stable; only a manifest-pinned cache layout
    may differ. Drop-ins keep their operator-supplied layout and are untouched.
    *base* selects another store folder; the default is the primary cache.
    """
    return (base or cache_dir()) / _cache_relative(relpath)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pins(base: Path) -> dict[str, str]:
    """Return ``{relative path: sha256}`` from ``<base>/SHA256SUMS`` (coreutils format)."""
    try:
        text = (base / PINS_FILE).read_text(encoding="utf-8")
    except OSError:
        return {}
    found: dict[str, str] = {}
    for line in text.split("\n"):
        line = line.rstrip("\r")
        digest = line[:64]
        if len(line) < 66 or line[64] != " " or any(c not in _HEX for c in digest):
            continue
        # "<hash> <mode><name>": mode is " " (text) or "*" (binary); a single
        # space followed directly by the name is also accepted.
        name = line[66:] if line[65] in " *" else line[65:]
        found[name[2:] if name.startswith("./") else name] = digest.lower()
    return found


def _check_pin(relpath: str, path: Path, base: Path, pinned: str) -> None:
    actual = _sha256(path)
    if actual != pinned:
        raise AssetCorrupt(
            relpath,
            path,
            pinned,
            actual,
            f"sha256 mismatch with {base / PINS_FILE}",
            fix=(
                "Restore the file, or run `python3 scripts/isaac_assets.py pin` "
                "if the change is intended."
            ),
        )


def _verify_member(relpath: str, path: Path, base: Path, entry: dict | None) -> None:
    """Check one stored file: its user pin if it has one, else its manifest entry."""
    pinned = pins(base).get(path.resolve().relative_to(base).as_posix())
    if pinned:
        _check_pin(relpath, path, base, pinned)
    else:
        _verify_entry(relpath, path, entry)


def verify_local(relpath: str, path: Path, base: Path | None = None) -> None:
    """Check a stored file against its user pin or the manifest before use.

    Drop-ins under ``exts/ur5e-dt/assets`` are the operator's own copies and are
    not compared with the manifest.  A stored file listed in its folder's
    ``SHA256SUMS`` is checked against that pin only.  Any other stored file
    that the manifest tracks is checked, with every other file of the same
    fetch group, because USD loads the group's sublayers from the store without
    asking this module.  Each group member is checked against its own user pin
    when it has one, so editing one sublayer and pinning it does not condemn the
    root file.  *base* is the store folder the lookup chose; it defaults to the
    most specific store folder that contains *path*.
    """
    base = base if base is not None else _store_base(path)
    if base is None:
        return
    pinned = pins(base).get(path.resolve().relative_to(base).as_posix())
    if pinned:
        _check_pin(relpath, path, base, pinned)
        return
    group = fetch_group(relpath)
    if group is None:
        return
    for entry in group.get("files", []):
        target = entry.get("target")
        if target == relpath:
            continue
        sibling = cached_path(target, base)
        if not sibling.is_file():
            raise AssetMissing(target, offline=offline())
        _verify_member(target, sibling, base, entry)
    _verify_entry(relpath, path, expected_asset(relpath))


def _verify_located(relpath: str, found: tuple[Path, str, Path]) -> None:
    path, layer, base = found
    if layer != "dropin":
        verify_local(relpath, path, base)


def _verify_entry(relpath: str, path: Path, entry: dict | None) -> None:
    """Compare one file with its manifest size and SHA-256."""
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

    Use this at USD-reference sites.  Local files are verified before use.
    Raises :class:`AssetMissing` when the asset is neither local nor allowed to
    be fetched, and :class:`AssetCorrupt` when a stored file no longer matches
    its recorded hash.
    """
    found = _locate(relpath)
    if found is not None:
        _verify_located(relpath, found)
        return found[0].as_uri()
    remote = remote_uri(relpath)
    if remote is not None and not offline():
        return remote
    raise AssetMissing(relpath, offline=remote is not None and offline())


def resolve_path(relpath: str) -> Path:
    """Resolve an asset to a local path, raising :class:`AssetMissing` otherwise."""
    found = _locate(relpath)
    if found is None:
        remote = remote_uri(relpath)
        raise AssetMissing(relpath, offline=remote is not None and offline())
    _verify_located(relpath, found)
    return found[0]


def resolve_dir(relpath: str) -> Path:
    """Resolve a local asset directory, raising :class:`AssetMissing` otherwise."""
    for base, _layer in _search_bases():
        candidate = base / relpath
        if candidate.is_dir():
            return candidate
    raise AssetMissing(relpath)


def resolve_info(relpath: str) -> dict:
    """Describe where *relpath* resolves from, without raising.

    Returns ``relpath``, ``layer`` (``project``, ``dropin``, ``store``,
    ``remote`` or ``missing``), ``status`` (``ok``, ``pinned``, ``unpinned``,
    ``remote``, ``corrupt`` or ``missing``), and, when known, ``path`` or
    ``uri``, ``sha256`` and ``detail``.
    """
    info: dict = {"relpath": relpath, "layer": "missing", "status": "missing"}
    found = _locate(relpath)
    if found is not None:
        path, layer, base = found
        info.update(layer=layer, path=str(path), sha256=_sha256(path))
        try:
            _verify_located(relpath, found)
        except (AssetCorrupt, AssetMissing) as exc:
            info.update(status="corrupt", detail=str(exc))
            return info
        if layer == "dropin":
            info["status"] = "unpinned"
        elif path.resolve().relative_to(base).as_posix() in pins(base):
            info["status"] = "pinned"
        else:
            info["status"] = "ok" if expected_asset(relpath) else "unpinned"
        return info
    remote = remote_uri(relpath)
    if remote is not None and not offline():
        info.update(layer="remote", status="remote", uri=remote)
    elif remote is not None:
        info["detail"] = "strict mode refuses the remote fallback"
    elif (group := fetch_group(relpath)) is not None:
        info["detail"] = (
            f"not fetched yet (fetch group {group.get('id', '?')}); "
            "run `python3 scripts/fetch_ur5e_assets.py`"
        )
    else:
        info["detail"] = "must be supplied locally (no public source)"
    return info


def known_assets() -> list[str]:
    """Return the logical asset names this module can report on.

    That is every manifest entry plus every file pinned in a store.
    """
    names: set[str] = set(manifest().get("remote", {}))
    for group in manifest().get("fetch", []):
        names.update(e["target"] for e in group.get("files", []) if "target" in e)
    names.update(e["target"] for e in manifest().get("manual", []) if "target" in e)
    for store in stores():
        for base in _bases(store):
            names.update(pins(base))
    return sorted(names)

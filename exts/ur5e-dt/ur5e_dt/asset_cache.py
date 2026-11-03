"""Resolve UR5e twin assets from the user's local asset folder.

The repository ships no assets. Every asset the twin uses (the lab robot and bench,
NVIDIA's public robot and sensor files, the gripper, the FMB objects and their layouts)
lives in a local asset folder that each user builds up and that is never pushed to
GitHub. Lookup order:

1. Project stores: the ``asset_path`` of the nearest ``isaac-mcp.json`` (see
   ``docs/ASSETS.md``), so one project can override an asset without touching anything
   else.
2. User stores: ``$ISAAC_MCP_ASSET_PATH`` (an ``os.pathsep``-separated list). Without it,
   the asset folder ``isaac-mcp-assets`` next to the checkout (or
   ``<workspace>/isaac-mcp-assets`` when ``$ISAAC_MCP_WORKSPACE`` is set), followed by
   older locations that still exist (``<workspace>/assets``, ``~/.cache/ur5e-dt/assets``)
   so nothing stops resolving while it is migrated. Inside a store an asset is looked up
   under ``<store>/ur5e-dt/<path>`` (the pack folder, preferred) and then
   ``<store>/<path>`` (the flat legacy layout). ``scripts/isaac_assets.py`` adds, adopts
   and migrates files into the folder; ``scripts/fetch_ur5e_assets.py`` downloads NVIDIA's
   public files into it.
3. The pinned NVIDIA asset root for entries listed under ``remote`` in
   ``assets-manifest.json``, unless strict mode refuses it.

A file in a store is verified before use.  If the folder that holds it has a
``SHA256SUMS`` file listing the file, that user pin decides (a lab-modified robot
differs from NVIDIA's, so the NVIDIA manifest hash would wrongly reject it).  Otherwise
a file listed in the manifest, and every file of the same fetch group, is checked
against its recorded size and SHA-256.

Set ``ISAAC_MCP_ASSET_STRICT=1`` (or ``"strict": true`` in the project file) to refuse
the remote fallback: a missing asset then raises :class:`AssetMissing` with guidance
instead of returning a URL.  The older ``UR5E_DT_ASSET_CACHE``, ``UR5E_DT_ASSET_ROOT``
and ``UR5E_DT_OFFLINE`` names still work as deprecated aliases of
``ISAAC_MCP_ASSET_PATH``, ``ISAAC_MCP_ASSET_REMOTE_ROOT`` and ``ISAAC_MCP_ASSET_STRICT``.

Files under the extension's own ``assets/`` folder are no longer used; run
``scripts/isaac_assets.py migrate`` to move them into the asset folder.

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
# Legacy in-repo asset folder: no longer searched; ``isaac_assets.py migrate`` reads it.
ASSETS_DIR = EXT_DIR / "assets"
# exts/ur5e-dt -> exts -> <checkout>
REPO_ROOT = EXT_DIR.parents[1]
STORE_NAME = "isaac-mcp-assets"
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
    """Raised when an asset is neither in the asset folder nor allowed to come from NVIDIA."""

    def __init__(self, relpath: str, *, offline: bool = False):
        self.relpath = relpath
        primary = cache_dir()
        remote = remote_uri(relpath)
        heading = "Asset not available offline" if offline else "Asset not available"
        lines = [
            f"{heading}: {relpath}",
            f"  expected at: {primary / PACK / relpath}",
            f"  searched:    {', '.join(str(s) for s in stores())}",
            f"  remote:      {remote or '(none: this asset must be supplied locally)'}",
        ]
        if offline:
            lines.append(
                f"Strict mode ({STRICT_ENV}=1, or {OFFLINE_ENV}) refuses loading it from "
                "NVIDIA's server. Download NVIDIA's public copy into the asset folder with "
                "`python3 scripts/fetch_ur5e_assets.py`, add your own copy with "
                "`python3 scripts/isaac_assets.py add <file-or-archive>`, or set "
                f"{STRICT_ENV}=0 to allow the online fallback."
            )
        elif remote:
            lines.append(
                "Run `python3 scripts/fetch_ur5e_assets.py` to download it into the asset "
                "folder, or `python3 scripts/isaac_assets.py add <file-or-archive>` to use your "
                f"own copy (set {PATH_ENV} to choose another folder)."
            )
        else:
            lines.append(
                "This asset must be supplied locally. Add it with "
                "`python3 scripts/isaac_assets.py add <file-or-archive>` (it lands at "
                f"{PACK}/{relpath} in the asset folder), or move assets from an older "
                "checkout with `python3 scripts/isaac_assets.py migrate`."
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


def _legacy_cache() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return (base / "ur5e-dt" / "assets").resolve()


def default_store() -> Path:
    """Return the asset folder used when nothing is configured.

    ``<workspace>/isaac-mcp-assets`` when ``$ISAAC_MCP_WORKSPACE`` is set, otherwise the
    ``isaac-mcp-assets`` folder next to the checkout. It need not exist yet: ``add``,
    ``migrate`` and the fetch script create it.
    """
    raw = os.environ.get(WORKSPACE_ENV, "").strip()
    if raw:
        return (Path(raw).expanduser() / STORE_NAME).resolve()
    return (REPO_ROOT.parent / STORE_NAME).resolve()


def workspace_store() -> Path | None:
    """Return the workspace's asset folder when ``$ISAAC_MCP_WORKSPACE`` is set and one exists.

    ``<workspace>/isaac-mcp-assets`` is preferred; ``<workspace>/assets`` (the older name)
    is accepted when only it exists.
    """
    raw = os.environ.get(WORKSPACE_ENV, "").strip()
    if not raw:
        return None
    for name in (STORE_NAME, "assets"):
        store = (Path(raw).expanduser() / name).resolve()
        if store.is_dir():
            return store
    return None


def legacy_stores() -> list[Path]:
    """Older default locations that still exist and are still read (never written)."""
    found = []
    raw = os.environ.get(WORKSPACE_ENV, "").strip()
    if raw:
        old = (Path(raw).expanduser() / "assets").resolve()
        if old.is_dir():
            found.append(old)
    cache = _legacy_cache()
    if cache.is_dir():
        found.append(cache)
    return found


def user_stores() -> list[Path]:
    """Return the user's stores, the primary (write) store first.

    ``$ISAAC_MCP_ASSET_PATH`` (or its deprecated alias) replaces the defaults. Without it
    the primary store is :func:`default_store` (or an existing workspace folder), followed
    by any older locations that still exist.
    """
    raw = _env(PATH_ENV, CACHE_ENV)
    found = [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]
    if found:
        return found
    primary = workspace_store() or default_store()
    return [primary] + [p for p in legacy_stores() if p != primary]


def stores() -> list[Path]:
    """Return every store searched for local copies, project stores first."""
    return project_stores() + user_stores()


def cache_dir() -> Path:
    """Return the primary asset folder: the first user store, where new files are written.

    It is never inside the repository by default.
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
    """Folders inside a store that hold assets: the pack folder, then the flat store.

    The pack folder is resolved so a symlinked ``ur5e-dt`` still yields the same
    paths as the files inside it.
    """
    return (store / PACK).resolve(), store


def _search_bases() -> list[tuple[Path, str]]:
    """Return ``(folder, layer)`` pairs in lookup order."""
    found: list[tuple[Path, str]] = []
    for store in project_stores():
        found.extend((base, "project") for base in _bases(store))
    for store in user_stores():
        found.extend((base, "store") for base in _bases(store))
    return found


def _locate(relpath: str) -> tuple[Path, str, Path] | None:
    """Return ``(file, layer, base)`` for the first local copy of *relpath*."""
    for base, layer in _search_bases():
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


_PINS_CACHE: dict[str, tuple[tuple[int, int, int, int], dict[str, str]]] = {}


def pins(base: Path) -> dict[str, str]:
    """Return ``{relative path: sha256}`` from ``<base>/SHA256SUMS`` (coreutils format).

    Parsed files are cached by modification and change time, size and inode, so repeated lookups
    (``doctor`` over thousands of pinned files) do not re-read the file each time. The
    returned mapping is shared: do not modify it.
    """
    path = base / PINS_FILE
    try:
        st = path.stat()
    except OSError:
        return {}
    stamp = (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)
    cached = _PINS_CACHE.get(str(path))
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} is not valid UTF-8 text ({exc})") from exc
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
    _PINS_CACHE[str(path)] = (stamp, found)
    return found


def _pin_key(path: Path, base: Path) -> str | None:
    """Return *path* relative to *base* as written in a pin file.

    The lookup builds paths under *base*, so the plain relative path is used first: a
    symlinked file inside a pack is pinned under its own name, not its target's.
    """
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        pass
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return None


def _containing_bases(path: Path) -> list[Path]:
    resolved = path.resolve()
    found: list[Path] = []
    for store in stores():
        for base in _bases(store):
            try:
                resolved.relative_to(base)
            except ValueError:
                continue
            found.append(base)
    return found


def _recover_base(relpath: str, path: Path) -> Path | None:
    """Pick the store folder to verify against when the caller did not say which.

    If the lookup for *relpath* lands on *path*, its folder is the answer. Otherwise
    fall back to the most specific folder containing *path*, and require every other
    containing folder's pin for the file to agree (fail closed on ambiguity).
    """
    located = _locate(relpath)
    if located is not None and located[0].resolve() == path.resolve():
        return located[2]
    bases = _containing_bases(path)
    if not bases:
        return None
    chosen = max(bases, key=lambda b: len(b.parts))
    resolved = path.resolve()
    for other in bases:
        if other != chosen:
            pin = pins(other).get(resolved.relative_to(other).as_posix())
            if pin:
                _check_pin(relpath, path, other, pin)
    return chosen


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
    key = _pin_key(path, base)
    pinned = pins(base).get(key) if key is not None else None
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
    if base is None:
        base = _recover_base(relpath, path)
        if base is None:
            return
    key = _pin_key(path, base)
    pinned = pins(base).get(key) if key is not None else None
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
    path, _layer, base = found
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

    Returns ``relpath``, ``layer`` (``project``, ``store``,
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
        if _pin_key(path, base) in pins(base):
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

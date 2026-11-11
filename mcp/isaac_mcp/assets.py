"""Find assets in the user's local asset folder. Standard library only; no Kit needed.

This is the supported way for any project to find its files::

    from isaac_mcp import assets

    uri = assets.resolve_uri("robot/ur5e.usd")                 # the selected pack
    uri = assets.resolve_uri("scene.usd", pack="bricksim")     # a named pack

Each user has one asset folder, ``isaac-mcp-assets``, with a subfolder per project (a *pack*).
The repository ships no assets. Lookup order for each file; the first local copy wins:

1. Project stores: the ``asset_path`` of the nearest ``isaac-mcp.json``.
2. User stores: ``$ISAAC_MCP_ASSET_PATH`` (an ``os.pathsep``-separated list). Without it, the
   ``isaac-mcp-assets`` folder next to the checkout (or ``<workspace>/isaac-mcp-assets`` when
   ``$ISAAC_MCP_WORKSPACE`` is set; or ``isaac-mcp-assets`` in the home folder when this package is installed with
   no checkout), then older locations that still exist. Inside a store the file is looked up in
   ``<store>/<pack>/<path>``.
3. The pinned NVIDIA root, only for the twin's pack, only for manifest ``remote`` entries, and
   never in strict mode (``$ISAAC_MCP_ASSET_STRICT`` or ``"strict": true``).

The pack comes from, in order: an explicit argument, ``$ISAAC_MCP_ASSET_PACK``, ``"pack"`` in
``isaac-mcp.json``, then ``ur5e-dt``. An invalid name is an error, never a silent skip.

A named pack holds only its own files. It has no flat-layout fallback and no NVIDIA fallback, and
it does not borrow from ``ur5e-dt``. The UR5e twin (``ur5e_dt.asset_cache``) searches a selected
pack first and then always its own ``ur5e-dt``, so it keeps finding its files in any project.

A file in a store is verified before use. If the folder holding it has a ``SHA256SUMS`` listing
the file, that pin decides (a lab-modified robot differs from NVIDIA's). Otherwise, in the twin's
pack, a file the manifest tracks is checked with the rest of its fetch group against the recorded
size and SHA-256. Nothing here writes or downloads anything. See ``docs/ASSETS.md``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

LOG = logging.getLogger(__name__)

STORE_NAME = "isaac-mcp-assets"
DEFAULT_PACK = "ur5e-dt"
DEFAULT_ASSET_ROOT = (
    "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1"
)

# Preferred names.
PATH_ENV = "ISAAC_MCP_ASSET_PATH"
PACK_ENV = "ISAAC_MCP_ASSET_PACK"
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
# A pack's own description (its entry files); never pinned and never counted as an asset.
PACK_FILE = "isaac-mcp-pack.json"

_TRUE = {"1", "true", "yes", "on"}
_HEX = frozenset("0123456789abcdefABCDEF")
_PACK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_RESERVED_PACKS = {PINS_FILE.casefold(), PACK_FILE.casefold()}


class InvalidPackName(ValueError):
    """A pack name that cannot be a single, safe folder name inside a store."""

    code = "INVALID_PACK"


def validate_pack_name(name, origin: str = "pack name") -> str:
    """Return *name* if it is one safe folder name. Traversal and separators are refused."""
    if (
        not isinstance(name, str)
        or not _PACK_NAME.fullmatch(name)
        or ".." in name
        or name.casefold() in _RESERVED_PACKS
    ):
        raise InvalidPackName(
            f"Invalid {origin} {name!r}: use 1-64 letters, digits, '.', '_' or '-', starting with "
            "a letter or digit, with no path separators or '..'."
        )
    return name


def checkout_root() -> Path | None:
    """The isaac-mcp checkout holding this file, or None when installed without one."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / "exts" / "ur5e-dt").is_dir() else None


# ---------------------------------------------------------------------------
# Project file
# ---------------------------------------------------------------------------


def project_file() -> Path | None:
    """Return the active ``isaac-mcp.json``, or None.

    ``$ISAAC_MCP_PROJECT`` (a project directory or the file itself) wins; otherwise the file is
    searched for in the working directory and its parents.
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


def selected_pack_info() -> tuple[str, str]:
    """Return ``(pack, source)``; source is ``env``, ``project`` or ``default``."""
    raw = os.environ.get(PACK_ENV, "").strip()
    if raw:
        return validate_pack_name(raw, f"pack in {PACK_ENV}"), "env"
    data, _ = _project_config()
    if "pack" in data:
        return validate_pack_name(data["pack"], f"'pack' in {project_file()}"), "project"
    return DEFAULT_PACK, "default"


def selected_pack() -> str:
    """Return the pack chosen by the environment or project file, else ``ur5e-dt``."""
    return selected_pack_info()[0]


# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------


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
    (``doctor`` over thousands of pinned files) do not re-read the file each time. The returned
    mapping is shared: do not modify it.
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
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) < 66 or line[64] != " " or any(c not in _HEX for c in digest):
            raise ValueError(f"{path} has a malformed line, so its pins can't be trusted: {line[:80]!r}")
        # "<hash> <mode><name>": mode is " " (text) or "*" (binary); a single
        # space followed directly by the name is also accepted.
        name = line[66:] if line[65] in " *" else line[65:]
        found[name[2:] if name.startswith("./") else name] = digest.lower()
    _PINS_CACHE[str(path)] = (stamp, found)
    return found


def _pin_key(path: Path, base: Path) -> str | None:
    """Return *path* relative to *base* as written in a pin file.

    The lookup builds paths under *base*, so the plain relative path is used first: a symlinked
    file inside a pack is pinned under its own name, not its target's.
    """
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        pass
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return None


def _pin_command(base: Path) -> str:
    pack = "" if base.name == DEFAULT_PACK else f" --pack {base.name}"
    return f"python3 scripts/isaac_assets.py pin{pack}"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AssetMissing(FileNotFoundError):
    """Raised when an asset is neither in the asset folder nor allowed to come from NVIDIA."""

    def __init__(self, relpath: str, *, offline: bool = False, resolver: "Resolver | None" = None):
        self.relpath = relpath
        resolver = resolver or Resolver()
        primary = resolver.cache_dir()
        pack = resolver.packs()[0]
        pack_arg = "" if pack == DEFAULT_PACK else f" --pack {pack}"
        remote = resolver.remote_uri(relpath)
        heading = "Asset not available offline" if offline else "Asset not available"
        lines = [
            f"{heading}: {relpath}",
            f"  expected at: {primary / pack / relpath}",
            f"  searched:    {', '.join(str(s) for s in resolver.stores())}",
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
                f"`python3 scripts/isaac_assets.py add <file-or-archive>{pack_arg}` (it lands at "
                f"{pack}/{relpath} in the asset folder), or move assets from an older "
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


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


class Resolver:
    """Resolve logical asset names for one pack. Settings are read fresh on each call.

    ``pack=None`` follows the selected pack. Only ``ur5e-dt`` has a manifest, a flat-layout
    fallback and a remote fallback; every other pack is just its own folder in each store.
    """

    def __init__(self, pack: str | None = None):
        self._pack = validate_pack_name(pack) if pack is not None else None
        self._warned: set[str] = set()

    # Where the checkout and manifest are; the twin overrides these with its own location.
    @property
    def repo_root(self) -> Path | None:
        return checkout_root()

    @property
    def manifest_path(self) -> Path | None:
        root = self.repo_root
        return root / "exts" / "ur5e-dt" / "assets-manifest.json" if root else None

    def packs(self) -> list[str]:
        """The pack folders searched in each store, in order."""
        return [self._pack if self._pack is not None else selected_pack()]

    def _is_twin_pack(self) -> bool:
        return DEFAULT_PACK in self.packs()

    def manifest(self) -> dict:
        """Return the parsed manifest; a pack other than the twin's has none."""
        path = self.manifest_path
        if not self._is_twin_pack() or path is None or not path.is_file():
            return {}
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _env(self, primary: str, legacy: str) -> str:
        """Return a setting from its preferred name or, with a warning, its old name."""
        value = os.environ.get(primary, "").strip()
        if value:
            return value
        value = os.environ.get(legacy, "").strip()
        if value and legacy not in self._warned:
            self._warned.add(legacy)
            LOG.warning("%s is deprecated; use %s", legacy, primary)
        return value

    # ---- stores ----------------------------------------------------------

    def _legacy_cache(self) -> Path:
        xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
        base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
        return (base / "ur5e-dt" / "assets").resolve()

    def default_store(self) -> Path:
        """Return the asset folder used when nothing is configured; it need not exist yet."""
        raw = os.environ.get(WORKSPACE_ENV, "").strip()
        if raw:
            return (Path(raw).expanduser() / STORE_NAME).resolve()
        root = self.repo_root
        return ((root.parent if root else Path.home()) / STORE_NAME).resolve()

    def workspace_store(self) -> Path | None:
        """Return ``<workspace>/isaac-mcp-assets`` when the workspace is set and it exists."""
        raw = os.environ.get(WORKSPACE_ENV, "").strip()
        if not raw:
            return None
        store = (Path(raw).expanduser() / STORE_NAME).resolve()
        return store if store.is_dir() else None

    def legacy_stores(self) -> list[Path]:
        """Older default locations that still exist and are still read (never written)."""
        found = []
        raw = os.environ.get(WORKSPACE_ENV, "").strip()
        if raw:
            old = (Path(raw).expanduser() / "assets").resolve()
            if old.is_dir():
                found.append(old)
        cache = self._legacy_cache()
        if cache.is_dir():
            found.append(cache)
        return found

    def user_stores(self) -> list[Path]:
        """Return the user's stores, the primary (write) store first."""
        raw = self._env(PATH_ENV, CACHE_ENV)
        found = [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]
        if found:
            return found
        primary = self.default_store()
        return [primary] + [p for p in self.legacy_stores() if p != primary]

    def stores(self) -> list[Path]:
        """Return every store searched for local copies, project stores first."""
        return project_stores() + self.user_stores()

    def cache_dir(self) -> Path:
        """Return the primary asset folder: the first user store, where new files are written."""
        return self.user_stores()[0]

    def asset_root(self) -> str:
        """Return the base URL or directory for NVIDIA-hosted assets."""
        override = self._env(REMOTE_ROOT_ENV, ROOT_ENV)
        if override:
            return override.rstrip("/")
        return str(self.manifest().get("asset_root") or DEFAULT_ASSET_ROOT).rstrip("/")

    def offline(self) -> bool:
        """Return True when remote fallbacks are refused; the environment overrides the project."""
        raw = self._env(STRICT_ENV, OFFLINE_ENV)
        if raw:
            return raw.lower() in _TRUE
        data, _ = _project_config()
        return data.get("strict") is True

    # ---- lookup ----------------------------------------------------------

    def _bases(self, store: Path) -> list[Path]:
        """Folders inside a store that hold assets: each pack, then the twin's flat layout.

        A pack folder is resolved so a symlinked one still yields the paths of its files.
        """
        found = [(store / pack).resolve() for pack in self.packs()]
        if self._is_twin_pack():
            found.append(store)
        return found

    def _search_bases(self) -> list[tuple[Path, str]]:
        """Return ``(folder, layer)`` pairs in lookup order."""
        found: list[tuple[Path, str]] = []
        for store in project_stores():
            found.extend((base, "project") for base in self._bases(store))
        for store in self.user_stores():
            found.extend((base, "store") for base in self._bases(store))
        return found

    def _locate(self, relpath: str) -> tuple[Path, str, Path] | None:
        """Return ``(file, layer, base)`` for the first local copy of *relpath*."""
        for base, layer in self._search_bases():
            # A manifest-tracked asset must sit at its canonical layout, because its sublayers
            # use relative references. A file at the logical name is only accepted when pinned.
            canonical = self._cache_relative(relpath).as_posix()
            names = [canonical]
            if canonical != relpath and pins(base).get(relpath):
                names.append(relpath)
            for name in names:
                candidate = base / name
                if candidate.is_file():
                    return candidate, layer, base
        return None

    def find_local(self, relpath: str) -> Path | None:
        """Return the first local copy of *relpath*, or None."""
        found = self._locate(relpath)
        return found[0] if found else None

    def _store_base(self, path: Path) -> Path | None:
        """Return the store folder that holds *path*, or None for a file outside every store."""
        resolved = path.resolve()
        best: Path | None = None
        for store in self.stores():
            for base in self._bases(store):
                try:
                    resolved.relative_to(base)
                except ValueError:
                    continue
                if best is None or len(base.parts) > len(best.parts):
                    best = base
        return best

    def fetch_group(self, relpath: str) -> dict | None:
        """Return the manifest fetch group that contains *relpath*, if any."""
        for group in self.manifest().get("fetch", []):
            if any(entry.get("target") == relpath for entry in group.get("files", [])):
                return group
        return None

    def expected_asset(self, relpath: str) -> dict | None:
        """Return the manifest fetch entry for *relpath*, if one exists."""
        for group in self.manifest().get("fetch", []):
            for entry in group.get("files", []):
                if entry.get("target") == relpath:
                    return entry
        return None

    def _cache_relative(self, relpath: str) -> Path:
        entry = self.expected_asset(relpath) or {}
        relative = Path(entry.get("cache_path", relpath))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("Manifest cache path must stay relative to the asset cache")
        return relative

    def cached_path(self, relpath: str, base: Path | None = None) -> Path:
        """Keep source-relative dependencies anchored inside the operator cache.

        The logical twin name remains stable; only a manifest-pinned cache layout may differ.
        Drop-ins keep their operator-supplied layout and are untouched. *base* selects another
        store folder; the default is the primary cache.
        """
        return (base or self.cache_dir()) / self._cache_relative(relpath)

    # ---- verification ----------------------------------------------------

    def _containing_bases(self, path: Path) -> list[Path]:
        resolved = path.resolve()
        found: list[Path] = []
        for store in self.stores():
            for base in self._bases(store):
                try:
                    resolved.relative_to(base)
                except ValueError:
                    continue
                found.append(base)
        return found

    def _recover_base(self, relpath: str, path: Path) -> Path | None:
        """Pick the store folder to verify against when the caller did not say which.

        If the lookup for *relpath* lands on *path*, its folder is the answer. Otherwise fall
        back to the most specific folder containing *path*, and require every other containing
        folder's pin for the file to agree (fail closed on ambiguity).
        """
        located = self._locate(relpath)
        if located is not None and located[0].resolve() == path.resolve():
            return located[2]
        bases = self._containing_bases(path)
        if not bases:
            return None
        chosen = max(bases, key=lambda b: len(b.parts))
        resolved = path.resolve()
        for other in bases:
            if other != chosen:
                pin = pins(other).get(resolved.relative_to(other).as_posix())
                if pin:
                    self._check_pin(relpath, path, other, pin)
        return chosen

    def _check_pin(self, relpath: str, path: Path, base: Path, pinned: str) -> None:
        actual = _sha256(path)
        if actual != pinned:
            fix = f"Restore the file, or run `{_pin_command(base)}` if the change is intended."
            if self.expected_asset(relpath):
                fix += (
                    " For NVIDIA's copy instead: `python3 scripts/isaac_assets.py unpin "
                    f"{_pin_key(path, base)}`, then `python3 scripts/fetch_ur5e_assets.py --force`."
                )
            raise AssetCorrupt(
                relpath,
                path,
                pinned,
                actual,
                f"sha256 mismatch with {base / PINS_FILE}",
                fix=fix,
            )

    def _verify_member(self, relpath: str, path: Path, base: Path, entry: dict | None) -> None:
        """Check one stored file: its user pin if it has one, else its manifest entry."""
        key = _pin_key(path, base)
        pinned = pins(base).get(key) if key is not None else None
        if pinned:
            self._check_pin(relpath, path, base, pinned)
        else:
            self._verify_entry(relpath, path, entry)

    def verify_local(self, relpath: str, path: Path, base: Path | None = None) -> None:
        """Check a stored file against its user pin or the manifest before use.

        A file outside every store is not checked. A stored file listed in its folder's
        ``SHA256SUMS`` is checked against that pin only. Any other stored file that the manifest
        tracks is checked, with every other file of the same fetch group, because USD loads the
        group's sublayers from the store without asking this module. Each group member is checked
        against its own user pin when it has one, so editing one sublayer and pinning it does not
        condemn the root file. *base* is the store folder the lookup chose; it defaults to the
        most specific store folder that contains *path*.
        """
        if base is None:
            base = self._recover_base(relpath, path)
            if base is None:
                return
        key = _pin_key(path, base)
        pinned = pins(base).get(key) if key is not None else None
        if pinned:
            self._check_pin(relpath, path, base, pinned)
            return
        group = self.fetch_group(relpath)
        if group is None:
            return
        for entry in group.get("files", []):
            target = entry.get("target")
            if target == relpath:
                continue
            sibling = self.cached_path(target, base)
            if not sibling.is_file():
                raise AssetMissing(target, offline=self.offline(), resolver=self)
            self._verify_member(target, sibling, base, entry)
        self._verify_entry(relpath, path, self.expected_asset(relpath))

    def _verify_located(self, relpath: str, found: tuple[Path, str, Path]) -> None:
        path, _layer, base = found
        self.verify_local(relpath, path, base)

    def _verify_entry(self, relpath: str, path: Path, entry: dict | None) -> None:
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
                raise AssetCorrupt(relpath, path, expected_sha, actual_sha, "sha256 mismatch")

    # ---- public lookups --------------------------------------------------

    def remote_uri(self, relpath: str) -> str | None:
        """Return the pinned NVIDIA URL for *relpath*, if the manifest allows one."""
        source = self.manifest().get("remote", {}).get(relpath)
        if not source:
            return None
        return f"{self.asset_root()}/{source.lstrip('/')}"

    def resolve_uri(self, relpath: str) -> str:
        """Resolve an asset to a ``file://`` or ``https://`` URI.

        Use this at USD-reference sites. Local files are verified before use. Raises
        :class:`AssetMissing` when the asset is neither local nor allowed to be fetched, and
        :class:`AssetCorrupt` when a stored file no longer matches its recorded hash.
        """
        found = self._locate(relpath)
        if found is not None:
            self._verify_located(relpath, found)
            return found[0].as_uri()
        remote = self.remote_uri(relpath)
        if remote is not None and not self.offline():
            return remote
        raise AssetMissing(relpath, offline=remote is not None and self.offline(), resolver=self)

    def missing_assets(self, relpaths) -> list[str]:
        """Return which assets are unavailable, for optional scene parts.

        A corrupt file still raises :class:`AssetCorrupt`: being absent can be fine, but a file
        that fails its recorded hash must never be quietly skipped.
        """
        missing = []
        for relpath in relpaths:
            try:
                self.resolve_uri(relpath)
            except AssetMissing:
                missing.append(relpath)
        return missing

    def _foreign_pack(self, base: Path) -> dict:
        """Name the pack in a report only when it is not the twin's own (the usual case)."""
        return {"pack": base.name} if base.name != DEFAULT_PACK and base.name in self.packs() else {}

    def asset_source(self, relpath: str) -> dict:
        """Say where *relpath* would come from: ``project``, ``store``, ``remote`` or ``missing``."""
        found = self._locate(relpath)
        if found is not None:
            return {"asset": relpath, "source": found[1], "location": str(found[0]),
                    **self._foreign_pack(found[2])}
        remote = self.remote_uri(relpath)
        if remote is not None and not self.offline():
            return {"asset": relpath, "source": "remote", "location": remote}
        return {"asset": relpath, "source": "missing", "location": None}

    def resolve_path(self, relpath: str) -> Path:
        """Resolve an asset to a local path, raising :class:`AssetMissing` otherwise."""
        found = self._locate(relpath)
        if found is None:
            remote = self.remote_uri(relpath)
            raise AssetMissing(relpath, offline=remote is not None and self.offline(), resolver=self)
        self._verify_located(relpath, found)
        return found[0]

    def resolve_dir(self, relpath: str) -> Path:
        """Resolve a local asset directory, raising :class:`AssetMissing` otherwise."""
        for base, _layer in self._search_bases():
            candidate = base / relpath
            if candidate.is_dir():
                return candidate
        raise AssetMissing(relpath, resolver=self)

    def resolve_info(self, relpath: str) -> dict:
        """Describe where *relpath* resolves from, without raising.

        Returns ``relpath``, ``layer`` (``project``, ``store``, ``remote`` or ``missing``),
        ``status`` (``ok``, ``pinned``, ``unpinned``, ``remote``, ``corrupt`` or ``missing``),
        and, when known, ``path`` or ``uri``, ``sha256``, ``detail`` and a non-twin ``pack``.
        """
        info: dict = {"relpath": relpath, "layer": "missing", "status": "missing"}
        found = self._locate(relpath)
        if found is not None:
            path, layer, base = found
            info.update(layer=layer, path=str(path), sha256=_sha256(path), **self._foreign_pack(base))
            try:
                self._verify_located(relpath, found)
            except (AssetCorrupt, AssetMissing) as exc:
                info.update(status="corrupt", detail=str(exc))
                return info
            if _pin_key(path, base) in pins(base):
                info["status"] = "pinned"
            else:
                info["status"] = "ok" if self.expected_asset(relpath) else "unpinned"
            return info
        remote = self.remote_uri(relpath)
        if remote is not None and not self.offline():
            info.update(layer="remote", status="remote", uri=remote)
        elif remote is not None:
            info["detail"] = "strict mode refuses the remote fallback"
        elif (group := self.fetch_group(relpath)) is not None:
            info["detail"] = (
                f"not fetched yet (fetch group {group.get('id', '?')}); "
                "run `python3 scripts/fetch_ur5e_assets.py`"
            )
        else:
            info["detail"] = "must be supplied locally (no public source)"
        return info

    def known_assets(self) -> list[str]:
        """Return every manifest entry plus every file pinned in a store."""
        manifest = self.manifest()
        names: set[str] = set(manifest.get("remote", {}))
        for group in manifest.get("fetch", []):
            names.update(e["target"] for e in group.get("files", []) if "target" in e)
        names.update(e["target"] for e in manifest.get("manual", []) if "target" in e)
        for store in self.stores():
            for base in self._bases(store):
                names.update(pins(base))
        return sorted(names)


# ---------------------------------------------------------------------------
# Module-level API: the selected pack, or the one named by ``pack=``
# ---------------------------------------------------------------------------


def resolver(pack: str | None = None) -> Resolver:
    """Return a resolver for *pack*, or for the selected pack."""
    return Resolver(pack)


def resolve_uri(relpath: str, *, pack: str | None = None) -> str:
    return Resolver(pack).resolve_uri(relpath)


def resolve_path(relpath: str, *, pack: str | None = None) -> Path:
    return Resolver(pack).resolve_path(relpath)


def resolve_dir(relpath: str, *, pack: str | None = None) -> Path:
    return Resolver(pack).resolve_dir(relpath)


def resolve_info(relpath: str, *, pack: str | None = None) -> dict:
    return Resolver(pack).resolve_info(relpath)


def asset_source(relpath: str, *, pack: str | None = None) -> dict:
    return Resolver(pack).asset_source(relpath)


def find_local(relpath: str, *, pack: str | None = None) -> Path | None:
    return Resolver(pack).find_local(relpath)


def missing_assets(relpaths, *, pack: str | None = None) -> list[str]:
    return Resolver(pack).missing_assets(relpaths)


def known_assets(*, pack: str | None = None) -> list[str]:
    return Resolver(pack).known_assets()


def stores() -> list[Path]:
    return Resolver(DEFAULT_PACK).stores()


def user_stores() -> list[Path]:
    return Resolver(DEFAULT_PACK).user_stores()


def cache_dir() -> Path:
    return Resolver(DEFAULT_PACK).cache_dir()


def default_store() -> Path:
    return Resolver(DEFAULT_PACK).default_store()


def offline() -> bool:
    return Resolver(DEFAULT_PACK).offline()

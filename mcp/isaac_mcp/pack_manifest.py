"""The one asset-pack model: the asset root and ``pack.yaml``. Standard library only; no Kit, no USD.

A pack is ``<asset root>/<pack id>/<version>/`` with a ``pack.yaml`` manifest that lists every file
(``path``, ``sha256``, ``source``, ``license``, ``redistributable`` and an optional ``asset_id``).
This module is the host's reader and verifier. It implements the contract of
``exts/ur5e-dt/ur5e_dt/asset_pack.py`` (same rules, same messages) so the host tools, the CLI and
Kit-side code can use it without importing the work cell. ``mcp/tests/test_pack_manifest.py`` runs
both on the same fixtures and checks that they agree.

Nothing here downloads anything, and nothing here writes: the writing tools are in ``asset_packs``.
See ``docs/ASSET-PACKS.md``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger(__name__)

ROOT_ENV = "ISAAC_MCP_ASSET_ROOT"
WORKSPACE_ENV = "ISAAC_MCP_WORKSPACE"
PROJECT_ENV = "ISAAC_MCP_PROJECT"
PROJECT_FILE = "isaac-mcp.json"
MANIFEST_NAME = "pack.yaml"
OLD_PINS_FILE = "SHA256SUMS"
STANDARD_DIR = "assets"
TRANSITIONAL_DIR = "isaac-mcp-assets"
MAX_REPORTED = 200

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

# Environment variables the old model read. They are not read any more; if one is set the host says
# once what replaces it, instead of ignoring it silently.
REMOVED_ENVIRONMENT = {
    "ISAAC_MCP_ASSET_PATH": f"Set {ROOT_ENV} to the one folder that holds <pack id>/<version>/pack.yaml.",
    "UR5E_DT_ASSET_CACHE": f"Set {ROOT_ENV} to the one folder that holds <pack id>/<version>/pack.yaml.",
    "ISAAC_MCP_ASSET_PACK": "Name the pack as \"<id>/<version>\" in the 'pack' key of isaac-mcp.json, or pass it as an argument.",
    "ISAAC_MCP_ASSET_STRICT": f"Strict is the only behaviour now: nothing is ever downloaded. Packs are found under {ROOT_ENV}.",
    "UR5E_DT_OFFLINE": f"Strict is the only behaviour now: nothing is ever downloaded. Packs are found under {ROOT_ENV}.",
    "ISAAC_MCP_ASSET_REMOTE_ROOT": f"Nothing is downloaded any more. Put the files in a pack under {ROOT_ENV}.",
    "UR5E_DT_ASSET_ROOT": f"Nothing is downloaded any more. Put the files in a pack under {ROOT_ENV}.",
}
_announced: set[str] = set()


class ManifestError(ValueError):
    """A pack is missing, unsafe, or does not match its manifest."""


class RootError(ValueError):
    """No asset root could be found, or the setting is not usable."""

    code = "NO_ASSET_ROOT"


# ---------------------------------------------------------------------------
# Removed settings
# ---------------------------------------------------------------------------


def removed_environment(environ=None) -> list[str]:
    """One sentence for each removed variable that is set in *environ* (default: the process)."""
    environ = os.environ if environ is None else environ
    return [f"{name} is no longer read. {text}" for name, text in REMOVED_ENVIRONMENT.items()
            if str(environ.get(name, "")).strip()]


def announce_removed_environment(environ=None) -> list[str]:
    """Like :func:`removed_environment`, and logs each sentence once per process (stderr, never stdout)."""
    messages = removed_environment(environ)
    for message in messages:
        if message not in _announced:
            _announced.add(message)
            LOG.warning(message)
    return messages


# ---------------------------------------------------------------------------
# Asset root
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetRoot:
    path: Path
    source: str  # "environment", "workspace" or "transitional"
    warnings: tuple = field(default_factory=tuple)

    @property
    def exists(self) -> bool:
        return self.path.is_dir()


def checkout_root() -> Path | None:
    """The isaac-mcp checkout holding this file, or None when installed without one."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / "exts" / "ur5e-dt").is_dir() else None


def workspace_dir(environ=None) -> Path | None:
    """``$ISAAC_MCP_WORKSPACE``, else the folder that contains the checkout, else None."""
    environ = os.environ if environ is None else environ
    configured = str(environ.get(WORKSPACE_ENV, "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    checkout = checkout_root()
    return checkout.parent if checkout else None


def resolve_asset_root(environ=None, workspace=None) -> AssetRoot:
    """The one asset root, in this order:

    1. ``$ISAAC_MCP_ASSET_ROOT``.
    2. ``<workspace>/assets`` if that folder exists.
    3. ``<workspace>/isaac-mcp-assets`` if it exists (the name is transitional: the result says so).

    ``<workspace>`` is ``$ISAAC_MCP_WORKSPACE`` or the folder that contains the checkout. Without any
    of them this raises :class:`RootError` naming the setting. There is no default inside a home folder.
    """
    environ = os.environ if environ is None else environ
    warnings = list(announce_removed_environment(environ))
    configured = str(environ.get(ROOT_ENV, "")).strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise RootError(f"{ROOT_ENV} must be an absolute path, not {configured!r}.")
        return AssetRoot(path.resolve(), "environment", tuple(warnings))
    base = Path(workspace).expanduser().resolve() if workspace is not None else workspace_dir(environ)
    if base is not None:
        standard = base / STANDARD_DIR
        if standard.is_dir():
            return AssetRoot(standard, "workspace", tuple(warnings))
        transitional = base / TRANSITIONAL_DIR
        if transitional.is_dir():
            warnings.append(f"Using {transitional}: the name isaac-mcp-assets is transitional. Rename it to "
                            f"{STANDARD_DIR}/ or set {ROOT_ENV} to it.")
            return AssetRoot(transitional, "transitional", tuple(warnings))
        where = f"neither {standard} nor {transitional} exists"
    else:
        where = "this package is installed without a checkout and no workspace is set"
    raise RootError(f"No asset root: {where}. Set {ROOT_ENV} to the folder that holds your packs "
                    f"(<pack id>/<version>/pack.yaml), or create <workspace>/{STANDARD_DIR} "
                    f"(the workspace is {WORKSPACE_ENV} or the folder holding the checkout).")


# ---------------------------------------------------------------------------
# Project file
# ---------------------------------------------------------------------------


def project_file() -> Path | None:
    """The active ``isaac-mcp.json``: ``$ISAAC_MCP_PROJECT`` (a folder or the file), else the nearest one
    in the working directory or its parents."""
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


def project_pack() -> str | None:
    """The ``"<id>/<version>"`` the project file selects, or None. An invalid file or value is an error."""
    path = project_file()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read project file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Project file {path} must contain a JSON object")
    for key in ("asset_path", "strict"):
        if key in data:
            raise ValueError(f"'{key}' in {path} is not supported any more: packs live under one asset root "
                             f"({ROOT_ENV}), and nothing is ever downloaded. Remove the key.")
    if "pack" not in data:
        return None
    pack_id, version = split_pack_ref(data["pack"], f"'pack' in {path}")
    return f"{pack_id}/{version}"


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ManifestError(f"Invalid pack identifier: {value!r}")
    return value


def split_pack_ref(value, what: str = "pack") -> tuple[str, str]:
    """``"<id>/<version>"`` to ``(id, version)``. Anything else is an error."""
    parts = value.split("/") if isinstance(value, str) else []
    if len(parts) != 2:
        raise ManifestError(f"{what} must be \"<pack id>/<version>\", for example ur5e-jenga-workcell/v1; got {value!r}")
    return identifier(parts[0]), identifier(parts[1])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def parse_manifest_text(text: str):
    """JSON first; otherwise YAML, which needs PyYAML in the host environment."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
        except ImportError as error:
            raise ManifestError("Use JSON-compatible YAML for pack.yaml or provide PyYAML in the host environment") from error
        return yaml.safe_load(text)


def manifest_text(manifest: dict) -> str:
    """The text written to ``pack.yaml``: JSON, which is valid YAML, so any reader takes it."""
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def pack_directory(root, pack_id, version) -> tuple[Path, Path]:
    """``(resolved root, <root>/<id>/<version>)``, refusing a name that leaves the root."""
    root = Path(root).expanduser().resolve()
    directory = (root / identifier(pack_id) / identifier(version)).resolve()
    if not directory.is_relative_to(root):
        raise ManifestError("Pack directory escapes configured root")
    return root, directory


def read_manifest(directory: Path, pack_id, version) -> dict:
    """The parsed manifest, once its header (id, version, a non-empty file list) is right."""
    manifest_path = directory / MANIFEST_NAME
    if manifest_path.is_symlink():
        raise ManifestError("Manifest must not be a symlink")
    if not manifest_path.is_file():
        raise ManifestError(f"Missing pack {pack_id}/{version}: {manifest_path}; sync this private pack first")
    manifest = parse_manifest_text(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("id") != pack_id or manifest.get("version") != version:
        raise ManifestError("Manifest id/version does not match requested pack")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ManifestError("Manifest must list every pack file")
    return manifest


class _Checker:
    """The per-file rules of the contract, one record at a time (raises on the first problem)."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.manifest_path = directory / MANIFEST_NAME
        self.paths: set[Path] = set()
        self.assets: dict[str, Path] = {}

    def check(self, record, hash_file: bool = True) -> Path:
        if not isinstance(record, dict):
            raise ManifestError("Invalid file record")
        for name in ("path", "sha256", "source", "license"):
            if not isinstance(record.get(name), str) or not record[name].strip():
                raise ManifestError(f"File record requires {name}")
        if type(record.get("redistributable")) is not bool:
            raise ManifestError("File record requires boolean redistributable")
        relative = Path(record["path"])
        path = (self.directory / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(self.directory):
            raise ManifestError("Asset path escapes pack")
        if path in self.paths or path == self.manifest_path:
            raise ManifestError("Duplicate file path")
        self.paths.add(path)
        if not path.is_file():
            raise ManifestError(f"Missing pack file: {record['path']}")
        if hash_file and sha256_file(path) != record["sha256"]:
            raise ManifestError(f"Checksum mismatch: {record['path']}")
        if "asset_id" in record:
            asset_id = identifier(record["asset_id"])
            if asset_id in self.assets:
                raise ManifestError(f"Duplicate asset id: {asset_id}")
            self.assets[asset_id] = path
        return path

    def unlisted(self) -> list[Path]:
        actual = {p.resolve() for p in self.directory.rglob("*") if p.is_file() and p != self.manifest_path}
        return sorted(actual - self.paths)


class AssetPack:
    """A pack that verifies completely, or an exception. The same contract as ``ur5e_dt.asset_pack.AssetPack``."""

    def __init__(self, root, pack_id, version):
        _, self.directory = pack_directory(root, pack_id, version)
        self.manifest = read_manifest(self.directory, pack_id, version)
        checker = _Checker(self.directory)
        for record in self.manifest["files"]:
            checker.check(record)
        if checker.unlisted():
            raise ManifestError("Pack contains files not listed in pack.yaml")
        self._assets = checker.assets
        self._files = checker.paths

    def asset(self, logical_id):
        try:
            return self._assets[logical_id]
        except KeyError as error:
            raise ManifestError(f"Pack has no asset id {logical_id!r}") from error


# ---------------------------------------------------------------------------
# Reading without raising: for list and doctor
# ---------------------------------------------------------------------------


def inspect_pack(root, pack_id, version) -> dict:
    """Every problem of one pack version, not just the first. Read only; hashes every file.

    ``verified`` is true exactly when :class:`AssetPack` would accept the pack and nothing in it is a
    symbolic link (a symlink inside a pack is refused by an import).
    """
    report = dict(id=pack_id, version=version, files=0, bytes=0, verified=False, problems=[])
    try:
        _, directory = pack_directory(root, pack_id, version)
        report["directory"] = str(directory)
        manifest = read_manifest(directory, pack_id, version)
    except ManifestError as exc:
        report["problems"].append(str(exc))
        return report
    checker = _Checker(directory)
    problems = report["problems"]
    records = []
    for record in manifest["files"]:
        try:
            path = checker.check(record)
        except ManifestError as exc:
            where = record.get("path") if isinstance(record, dict) else None
            problems.append(f"{exc}" if where is None or str(where) in str(exc) else f"{exc} ({where})")
            continue
        records.append(dict(path=record["path"], sha256=record["sha256"], size=path.stat().st_size,
                            **({"asset_id": record["asset_id"]} if "asset_id" in record else {}),
                            redistributable=record["redistributable"]))
    report["records"] = records
    report["files"] = len(manifest["files"])
    report["bytes"] = sum(item["size"] for item in records)
    problems += [f"Pack contains files not listed in pack.yaml: {path.relative_to(directory).as_posix() if path.is_relative_to(directory) else path}"
                 for path in checker.unlisted()[:MAX_REPORTED]]
    problems += [f"Symlink in pack (an import refuses it): {rel}" for rel in symlinks(directory)[:MAX_REPORTED]]
    report["problems"] = problems[:MAX_REPORTED]
    report["verified"] = not problems
    return report


def symlinks(directory: Path) -> list[str]:
    """Symlinks anywhere in a pack, files or folders."""
    found = []
    for current, dirs, files in os.walk(directory, followlinks=False):
        found += [(Path(current) / name).relative_to(directory).as_posix()
                  for name in [*dirs, *files] if (Path(current) / name).is_symlink()]
    return sorted(found)


def _has_files(folder: Path) -> bool:
    for _current, _dirs, files in os.walk(folder, followlinks=False):
        if files:
            return True
    return False


def list_packs(root) -> dict:
    """Every ``<id>/<version>`` under *root* with its file count, size and whether it verifies, and any
    old-format pack (files or a SHA256SUMS and no ``<version>/pack.yaml``) with a hint to convert it."""
    root = Path(root).expanduser().resolve()
    report = dict(root=str(root), exists=root.is_dir(), packs=[], old_format=[])
    if not report["exists"]:
        return report
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith(".") or child.is_symlink():
            continue
        versions = [v for v in sorted(child.iterdir(), key=lambda p: p.name)
                    if v.is_dir() and not v.name.startswith(".") and not v.is_symlink()]
        if any((v / MANIFEST_NAME).exists() for v in versions):
            for version in versions:
                if (version / MANIFEST_NAME).exists():
                    item = inspect_pack(root, child.name, version.name)
                elif re.match(r"v?\d", version.name) and _has_files(version):
                    item = dict(id=child.name, version=version.name, files=0, bytes=0, verified=False,
                                problems=[f"Missing {MANIFEST_NAME}"])
                else:
                    continue
                item.pop("records", None)
                report["packs"].append(item)
        elif (child / OLD_PINS_FILE).is_file() or _has_files(child):
            report["old_format"].append(dict(
                id=child.name, format="old",
                hint=f"Old format; nothing reads it. Convert it with adopt (tool: from_old=\"{child.name}\", CLI: --from-old "
                     f"<folder>), giving source and license: it writes {child.name}/v1/pack.yaml."))
    return report


def resolve_pack_file(root, ref: str) -> Path:
    """The verified path of ``"<id>/<version>/<path>"``: the manifest must list the file and its bytes must
    match. Used where one file is needed (``sim__quick_start``) and hashing a whole pack would be wasteful.
    No symlink below the pack is followed."""
    parts = ref.split("/", 2) if isinstance(ref, str) else []
    if len(parts) != 3 or not parts[2]:
        raise ManifestError(f"{ref!r} must be \"<pack id>/<version>/<file in the pack>\"")
    pack_id, version, relative = parts
    _, directory = pack_directory(root, pack_id, version)
    manifest = read_manifest(directory, pack_id, version)
    checker = _Checker(directory)
    wanted = None
    for record in manifest["files"]:
        listed = isinstance(record, dict) and record.get("path") == relative
        path = checker.check(record, hash_file=listed)
        if listed:
            wanted = path
    if wanted is None:
        raise ManifestError(f"{relative} is not listed in {pack_id}/{version}/{MANIFEST_NAME}")
    target = directory
    for part in Path(relative).parts:
        target = target / part
        if target.is_symlink():
            raise ManifestError(f"Symlink in pack path: {relative}")
    return wanted

"""Resolve immutable, self-contained work-cell packs without importing Kit."""
import hashlib
import json
import os
from pathlib import Path
import re


class PackError(ValueError):
    """An external pack is missing, unsafe, or does not match its manifest."""


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise PackError(f"Invalid pack identifier: {value!r}")
    return value


class AssetPack:
    def __init__(self, root, pack_id, version):
        root = Path(root).expanduser().resolve()
        self.directory = (root / _identifier(pack_id) / _identifier(version)).resolve()
        if not self.directory.is_relative_to(root):
            raise PackError("Pack directory escapes configured root")
        manifest_path = self.directory / "pack.yaml"
        if manifest_path.is_symlink():
            raise PackError("Manifest must not be a symlink")
        if not manifest_path.is_file():
            raise PackError(f"Missing pack {pack_id}/{version}: {manifest_path}; sync this private pack first")
        text = manifest_path.read_text(encoding="utf-8")
        try:
            self.manifest = json.loads(text)
        except json.JSONDecodeError:
            try:
                import yaml
            except ImportError as error:
                raise PackError("Use JSON-compatible YAML for pack.yaml or provide PyYAML in the host environment") from error
            self.manifest = yaml.safe_load(text)
        manifest = self.manifest
        if not isinstance(manifest, dict) or manifest.get("id") != pack_id or manifest.get("version") != version:
            raise PackError("Manifest id/version does not match requested pack")
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            raise PackError("Manifest must list every pack file")
        self._assets = {}
        paths = set()
        self._files = set()
        for record in files:
            if not isinstance(record, dict):
                raise PackError("Invalid file record")
            for field in ("path", "sha256", "source", "license"):
                if not isinstance(record.get(field), str) or not record[field].strip():
                    raise PackError(f"File record requires {field}")
            if type(record.get("redistributable")) is not bool:
                raise PackError("File record requires boolean redistributable")
            relative = Path(record["path"])
            path = (self.directory / relative).resolve()
            if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(self.directory):
                raise PackError("Asset path escapes pack")
            if path in paths or path == manifest_path:
                raise PackError("Duplicate file path")
            paths.add(path)
            if not path.is_file():
                raise PackError(f"Missing pack file: {record['path']}")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != record["sha256"]:
                raise PackError(f"Checksum mismatch: {record['path']}")
            if "asset_id" in record:
                asset_id = _identifier(record["asset_id"])
                if asset_id in self._assets:
                    raise PackError(f"Duplicate asset id: {asset_id}")
                self._assets[asset_id] = path
            self._files.add(path)
        actual = {path.resolve() for path in self.directory.rglob("*") if path.is_file() and path != manifest_path}
        if actual != self._files:
            raise PackError("Pack contains files not listed in pack.yaml")

    def verify_dependencies(self):
        """Check USD dependency closure in a USD-capable host or the simulator."""
        from pxr import UsdUtils

        for path in self._files:
            if path.suffix.lower() not in (".usd", ".usda", ".usdc"):
                continue
            try:
                layers, assets, unresolved = UsdUtils.ComputeAllDependencies(str(path))
            except Exception as error:
                raise PackError(f"Invalid USD asset {path.name}: {error}") from error
            if not layers:
                raise PackError(f"Invalid USD asset {path.name}: no layers resolved")
            if unresolved:
                raise PackError(f"Unresolved dependencies in {path.name}: {unresolved}")
            dependencies = [layer.realPath for layer in layers] + list(assets)
            for dependency in dependencies:
                if not dependency or Path(dependency).resolve() not in self._files:
                    raise PackError(f"Dependency outside manifest in {path.name}: {dependency}")
        return True

    def asset(self, logical_id):
        try:
            return self._assets[logical_id]
        except KeyError as error:
            raise PackError(f"Pack has no asset id {logical_id!r}") from error


def default_root(environ=None, home=None):
    """The asset root, in the host's order (``mcp/twinsmith/pack_manifest.py`` is the reference; Kit cannot import it).

    ``$TWINSMITH_ASSET_ROOT`` (alias ``ISAAC_MCP_ASSET_ROOT``), else ``<workspace>/assets``, else the transitional
    ``<workspace>/isaac-mcp-assets``. The workspace is ``$TWINSMITH_WORKSPACE``, else the folder that contains the
    checkout when that folder holds ``envs/`` or an asset folder, else ``<home>/robotics-workspace`` (the old ``<home>/isaac-workspace`` only while the new folder is absent).
    With nothing there it returns ``<workspace>/assets``, which the pack check then reports as missing.
    """
    environ = os.environ if environ is None else environ
    configured = str(environ.get("TWINSMITH_ASSET_ROOT") or environ.get("ISAAC_MCP_ASSET_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser()
    workspace = str(environ.get("TWINSMITH_WORKSPACE", "")).strip()
    if workspace:
        base = Path(workspace).expanduser().resolve()
    else:
        checkout = Path(__file__).resolve().parents[3]
        if (checkout / "exts" / "workcell").is_dir() and any(
                (checkout.parent / name).is_dir() for name in ("envs", "assets", "isaac-mcp-assets")):
            base = checkout.parent
        else:
            home = Path(home).expanduser() if home is not None else Path.home()
            base = home / "robotics-workspace"
            if not base.exists() and (home / "isaac-workspace").exists():
                base = home / "isaac-workspace"
    if not (base / "assets").is_dir() and (base / "isaac-mcp-assets").is_dir():
        return base / "isaac-mcp-assets"
    return base / "assets"


def resolve_pack(config, root=None):
    root = root or default_root()
    pack = AssetPack(root, config["pack"]["id"], config["pack"]["version"])
    for asset_id in config["assets"].values():
        pack.asset(asset_id)
    return pack

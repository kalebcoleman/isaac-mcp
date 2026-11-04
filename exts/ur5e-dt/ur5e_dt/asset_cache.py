"""The UR5e twin's view of the asset folder. New code should use ``isaac_mcp.assets``.

The one implementation lives in ``mcp/isaac_mcp/assets.py``, a standard-library module any
project can import without Kit. This file gives the twin its own resolver and keeps the module
functions existing callers use, including loading this file by path with ``runpy.run_path``.

The twin always finds its files. In each store it searches the pack a project selected (with
``"pack"`` in ``isaac-mcp.json`` or ``$ISAAC_MCP_ASSET_PACK``), then its own ``ur5e-dt``, then the
flat legacy layout; a local copy always wins over NVIDIA's server. See ``docs/ASSETS.md``.

Files under the extension's own ``assets/`` folder are no longer used; run
``scripts/isaac_assets.py migrate`` to move them into the asset folder.
"""

from __future__ import annotations

import sys
from pathlib import Path

# exts/ur5e-dt/ur5e_dt/asset_cache.py -> exts/ur5e-dt
EXT_DIR = Path(__file__).resolve().parents[1]
# Legacy in-repo asset folder: no longer searched; ``isaac_assets.py migrate`` reads it.
ASSETS_DIR = EXT_DIR / "assets"
# exts/ur5e-dt -> exts -> <checkout>
REPO_ROOT = EXT_DIR.parents[1]
MANIFEST_PATH = EXT_DIR / "assets-manifest.json"

# This file may load before sim-tools does, or by path from a script outside Kit.
if str(REPO_ROOT / "mcp") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "mcp"))

from isaac_mcp import assets as _assets  # noqa: E402
from isaac_mcp.assets import (  # noqa: E402,F401
    CACHE_ENV,
    DEFAULT_ASSET_ROOT,
    OFFLINE_ENV,
    PACK_ENV,
    PACK_FILE,
    PATH_ENV,
    PINS_FILE,
    PROJECT_ENV,
    PROJECT_FILE,
    REMOTE_ROOT_ENV,
    ROOT_ENV,
    STORE_NAME,
    STRICT_ENV,
    WORKSPACE_ENV,
    AssetCorrupt,
    AssetMissing,
    pins,
    project_file,
    project_stores,
    selected_pack,
    selected_pack_info,
)

# The folder name this module's own assets use inside a store. It does not follow the selected
# pack: fetched NVIDIA files and migrated twin files always belong here.
PACK = _assets.DEFAULT_PACK


class _TwinResolver(_assets.Resolver):
    """Search the selected pack, then the twin's; paths come from this file's location."""

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    @property
    def manifest_path(self) -> Path:
        return MANIFEST_PATH

    def packs(self) -> list[str]:
        chosen = selected_pack()
        return [chosen] if chosen == PACK else [chosen, PACK]


_TWIN = _TwinResolver()

manifest = _TWIN.manifest
default_store = _TWIN.default_store
workspace_store = _TWIN.workspace_store
user_stores = _TWIN.user_stores
stores = _TWIN.stores
cache_dir = _TWIN.cache_dir
asset_root = _TWIN.asset_root
offline = _TWIN.offline
find_local = _TWIN.find_local
fetch_group = _TWIN.fetch_group
expected_asset = _TWIN.expected_asset
cached_path = _TWIN.cached_path
verify_local = _TWIN.verify_local
remote_uri = _TWIN.remote_uri
resolve_uri = _TWIN.resolve_uri
missing_assets = _TWIN.missing_assets
asset_source = _TWIN.asset_source
resolve_path = _TWIN.resolve_path
resolve_dir = _TWIN.resolve_dir
resolve_info = _TWIN.resolve_info
known_assets = _TWIN.known_assets


def legacy_stores() -> list[Path]:
    """Older default locations that still exist and are still read (never written)."""
    return _TWIN.legacy_stores()

"""scripts/fetch_ur5e_assets.py writes into the asset folder and never replaces a pinned file."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "fetch_ur5e_assets.py"


@pytest.fixture
def fetcher():
    spec = importlib.util.spec_from_file_location("fetch_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entry(target, data, cache_path=None):
    item = {"target": target, "source": f"Isaac/{target}", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    if cache_path:
        item["cache_path"] = cache_path
    return item


def test_fetch_writes_into_the_pack_folder_and_keeps_pinned_files(fetcher, tmp_path, capsys):
    root = tmp_path / "nvidia-root"
    (root / "Isaac" / "robot").mkdir(parents=True)
    (root / "Isaac" / "robot" / "ur5e.usd").write_bytes(b"nvidia robot")
    (root / "Isaac" / "robot" / "configuration").mkdir()
    (root / "Isaac" / "robot" / "configuration" / "base.usd").write_bytes(b"base")
    manifest = {"fetch": [{"id": "g", "files": [
        entry("robot/ur5e.usd", b"nvidia robot"),
        entry("robot/configuration/base.usd", b"base"),
    ]}], "manual": []}
    store = tmp_path / "isaac-mcp-assets"
    pack = store / "ur5e-dt"
    (pack / "robot").mkdir(parents=True)
    (pack / "robot" / "ur5e.usd").write_bytes(b"lab robot with tool0")
    digest = hashlib.sha256(b"lab robot with tool0").hexdigest()
    (pack / "SHA256SUMS").write_text(f"{digest}  robot/ur5e.usd\n")

    assert fetcher.fetch(manifest, store, str(root), force=False) == 0
    assert (pack / "robot" / "ur5e.usd").read_bytes() == b"lab robot with tool0"
    assert (pack / "robot" / "configuration" / "base.usd").read_bytes() == b"base"
    assert not (store / "robot").exists()  # nothing written to the flat layout
    out = capsys.readouterr().out
    assert "Kept 1 file(s) you pinned yourself" in out
    assert fetcher.check(manifest, store) == 0

    # --force still never replaces the pinned file.
    assert fetcher.fetch(manifest, store, str(root), force=True) == 0
    assert (pack / "robot" / "ur5e.usd").read_bytes() == b"lab robot with tool0"


def test_check_accepts_an_older_flat_layout(fetcher, tmp_path):
    manifest = {"fetch": [{"id": "g", "files": [entry("sensors/x.usd", b"x")]}], "manual": []}
    store = tmp_path / "old-cache"
    (store / "sensors").mkdir(parents=True)
    (store / "sensors" / "x.usd").write_bytes(b"x")
    assert fetcher.check(manifest, store) == 0

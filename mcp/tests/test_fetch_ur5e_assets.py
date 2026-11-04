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


def _changed_store(tmp_path):
    root = tmp_path / "nvidia-root"
    (root / "Isaac" / "robot").mkdir(parents=True)
    (root / "Isaac" / "robot" / "ur5e.usd").write_bytes(b"nvidia robot")
    manifest = {"fetch": [{"id": "g", "files": [entry("robot/ur5e.usd", b"nvidia robot")]}], "manual": []}
    store = tmp_path / "isaac-mcp-assets"
    robot = store / "ur5e-dt" / "robot" / "ur5e.usd"
    robot.parent.mkdir(parents=True)
    robot.write_bytes(b"lab edit, not pinned yet")
    return root, manifest, store, robot


def test_fetch_keeps_a_changed_unpinned_file_and_says_how_to_accept_it(fetcher, tmp_path, capsys):
    root, manifest, store, robot = _changed_store(tmp_path)
    assert fetcher.fetch(manifest, store, str(root), force=False) == 1
    assert robot.read_bytes() == b"lab edit, not pinned yet"
    err = capsys.readouterr().err
    assert str(robot) in err and "isaac_assets.py pin" in err and "--force" in err


def test_force_is_the_explicit_way_to_replace_a_changed_unpinned_file(fetcher, tmp_path):
    root, manifest, store, robot = _changed_store(tmp_path)
    assert fetcher.fetch(manifest, store, str(root), force=True) == 0
    assert robot.read_bytes() == b"nvidia robot"


def fetch_layout(tmp_path):
    root = tmp_path / "nvidia-root"
    (root / "Isaac" / "robot").mkdir(parents=True)
    (root / "Isaac" / "robot" / "ur5e.usd").write_bytes(b"nvidia robot")
    manifest = {"fetch": [{"id": "g", "files": [entry("robot/ur5e.usd", b"nvidia robot")]}], "manual": []}
    store = tmp_path / "isaac-mcp-assets"
    pack = store / "ur5e-dt"
    (pack / "robot").mkdir(parents=True)
    return root, manifest, store, pack


def test_force_says_plainly_that_it_keeps_a_pinned_file_and_how_to_get_nvidias_copy(fetcher, tmp_path, capsys):
    root, manifest, store, pack = fetch_layout(tmp_path)
    robot = pack / "robot" / "ur5e.usd"
    robot.write_bytes(b"lab robot")
    (pack / "SHA256SUMS").write_text(f"{hashlib.sha256(b'lab robot').hexdigest()}  robot/ur5e.usd\n")
    assert fetcher.fetch(manifest, store, str(root), force=True) == 0
    assert robot.read_bytes() == b"lab robot"
    out = capsys.readouterr().out
    assert "--force does not change this" in out
    assert "isaac_assets.py unpin robot/ur5e.usd" in out


def test_without_force_the_changed_file_message_only_promises_unpinned_files(fetcher, tmp_path, capsys):
    root, manifest, store, pack = fetch_layout(tmp_path)
    robot = pack / "robot" / "ur5e.usd"
    robot.write_bytes(b"edited, not pinned")
    assert fetcher.fetch(manifest, store, str(root), force=False) == 1
    assert "To replace these unpinned files with NVIDIA's copy instead, re-run with --force" in capsys.readouterr().err


def test_unpin_then_force_really_restores_nvidias_copy(fetcher, tmp_path, capsys):
    """The way back that the message states, run end to end."""
    root, manifest, store, pack = fetch_layout(tmp_path)
    robot = pack / "robot" / "ur5e.usd"
    robot.write_bytes(b"lab robot")
    (pack / "SHA256SUMS").write_text(f"{hashlib.sha256(b'lab robot').hexdigest()}  robot/ur5e.usd\n")
    from isaac_mcp import asset_packs

    assert asset_packs.unpin_files(pack, ["robot/ur5e.usd"]) == ["robot/ur5e.usd"]
    assert fetcher.fetch(manifest, store, str(root), force=False) == 1  # still refuses without --force
    assert robot.read_bytes() == b"lab robot"
    assert fetcher.fetch(manifest, store, str(root), force=True) == 0
    assert robot.read_bytes() == b"nvidia robot"


def test_a_selected_pack_never_receives_nvidias_files(fetcher, tmp_path, capsys, monkeypatch):
    root, manifest, store, pack = fetch_layout(tmp_path)
    monkeypatch.setenv("ISAAC_MCP_ASSET_PACK", "bricksim")
    assert fetcher.fetch(manifest, store, str(root), force=False) == 0
    assert (pack / "robot" / "ur5e.usd").read_bytes() == b"nvidia robot"
    assert not (store / "bricksim").exists()


def test_a_corrupt_pinned_nvidia_file_states_the_way_back(tmp_path, monkeypatch):
    from isaac_mcp import assets

    pack = tmp_path / "ur5e-dt"
    (pack / "robot").mkdir(parents=True)
    (pack / "robot" / "ur5e.usd").write_bytes(b"changed")
    (pack / "SHA256SUMS").write_text(f"{'0' * 64}  robot/ur5e.usd\n")
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(tmp_path))
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(tmp_path / "no-project"))
    monkeypatch.delenv("ISAAC_MCP_ASSET_PACK", raising=False)
    with pytest.raises(assets.AssetCorrupt, match="isaac_assets.py unpin robot/ur5e.usd"):
        assets.resolve_uri("robot/ur5e.usd")


def test_the_unpin_hint_uses_the_name_in_the_pin_file(fetcher, tmp_path, capsys):
    """A tracked file can be pinned under its logical name while stored at a deeper path."""
    root = tmp_path / "nvidia-root"
    (root / "Isaac").mkdir(parents=True)
    (root / "Isaac" / "s.usd").write_bytes(b"s")
    item = entry("sensors/s.usd", b"s", cache_path="Isaac/deep/s.usd")
    item["source"] = "Isaac/s.usd"
    store = tmp_path / "isaac-mcp-assets"
    pack = store / "ur5e-dt"
    pack.mkdir(parents=True)
    (pack / "SHA256SUMS").write_text(f"{'0' * 64}  sensors/s.usd\n")
    assert fetcher.fetch({"fetch": [{"id": "g", "files": [item]}], "manual": []}, store, str(root), force=True) == 0
    assert "unpin sensors/s.usd" in capsys.readouterr().out

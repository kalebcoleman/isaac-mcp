import json
import os
from pathlib import Path
import pytest
from isaac_mcp import project_assets as a


@pytest.fixture
def broker(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "robot.usda").write_text("#usda 1.0")
    (root / "parts").mkdir()
    (root / "parts" / "material.usda").write_text("#usda 1.0")
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    config = tmp_path / "approval.json"
    config.write_text(
        json.dumps(
            dict(
                version=1,
                profile="trusted_project",
                roots={"demo": str(root)},
                staging=str(staging),
            )
        )
    )
    return a.Broker(str(config))


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/a.usd",
        "../a.usd",
        "a/../b.usd",
        "a//b.usd",
        "http://a",
        "a\\b",
        "a\n.usd",
        "a/./b.usd",
        "",
    ],
)
def test_paths(path):
    with pytest.raises(a.AssetError):
        a.relative(path)


def test_default_and_untrusted_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("ISAAC_MCP_ASSET_CONFIG", raising=False)
    b = a.Broker()
    assert b.listing()["roots"] == []
    with pytest.raises(a.AssetError, match="not approved"):
        b.scan("x")
    config = tmp_path / "config"
    config.write_text('{"version":1,"profile":"isolated_untrusted"}')
    b = a.Broker(str(config))
    assert not b.roots
    assert "Disabled" in b.config_error


def test_scan_snapshot_replacement(broker):
    assert [x["relative_path"] for x in broker.scan("demo")["files"]] == [
        "parts/material.usda",
        "robot.usda",
    ]
    first = broker.scan("demo", 0, 1)
    assert first["next_offset"] == 1
    snap = broker.snapshot("demo", "robot.usda")
    filename, manifest = broker.verify(snap["snapshot_id"], snap["manifest_sha256"])
    (broker.roots["demo"][0] / "robot.usda").write_text("changed source")
    assert filename.read_text() == "#usda 1.0"
    filename.chmod(0o600)
    filename.write_text("tampered snapshot")
    with pytest.raises(a.AssetError, match="content changed"):
        broker.verify(snap["snapshot_id"], snap["manifest_sha256"])


@pytest.mark.parametrize("entry", ["symlink", "fifo"])
def test_special_files_refused(broker, entry):
    p = broker.roots["demo"][0] / "bad"
    if entry == "symlink":
        p.symlink_to("/etc/passwd")
    else:
        os.mkfifo(p)
    with pytest.raises(a.AssetError):
        broker.scan("demo")
    with pytest.raises(a.AssetError):
        broker.snapshot("demo", "robot.usda")
    assert list(broker.staging.iterdir()) == []


def test_stale_root(broker):
    p = broker.roots["demo"][0]
    p.rename(p.with_name("old"))
    p.mkdir()
    with pytest.raises(a.AssetError, match="replaced"):
        broker.scan("demo")


def test_open_race(broker, monkeypatch):
    original = a.os.open

    def replace(name, flags, *args, **kw):
        if name == "robot.usda":
            p = broker.roots["demo"][0] / name
            p.unlink()
            p.symlink_to("/etc/passwd")
        return original(name, flags, *args, **kw)

    monkeypatch.setattr(a.os, "open", replace)
    with pytest.raises(OSError):
        broker.snapshot("demo", "robot.usda")
    assert list(broker.staging.iterdir()) == []


def test_replacement_during_read(broker, monkeypatch):
    original = a.os.read
    done = []

    def read(fd, n):
        data = original(fd, n)
        if not done and os.readlink(f"/proc/self/fd/{fd}").endswith("robot.usda"):
            done.append(True)
            p = broker.roots["demo"][0] / "robot.usda"
            p.unlink()
            p.write_text("new")
        return data

    monkeypatch.setattr(a.os, "read", read)
    with pytest.raises(a.AssetError):
        broker.snapshot("demo", "robot.usda")


def test_limits_and_archive_refusal(broker, monkeypatch):
    with pytest.raises(a.AssetError):
        broker.scan("demo", limit=201)
    with pytest.raises(a.AssetError):
        broker.snapshot("demo", "malicious.usdz")
    monkeypatch.setattr(a, "MAX_BYTES", 2)
    with pytest.raises(a.AssetError):
        broker.snapshot("demo", "robot.usda")
    assert not list(broker.staging.iterdir())


def test_all_entries_counted(broker, monkeypatch):
    monkeypatch.setattr(a, "MAX_ENTRIES", 1)
    with pytest.raises(a.AssetError, match="budget"):
        broker.scan("demo")


def test_symlink_config_ancestor(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real)
    with pytest.raises(OSError):
        a.open_dir(alias)


@pytest.mark.parametrize(
    "filename,data",
    [
        ("nested.usdz", b"ignored"),
        ("renamed.usd", b"PK\x03\x04evil"),
        ("texture.bin", b"PK\x05\x06"),
    ],
)
def test_archive_dependencies_never_reach_parser(broker, filename, data):
    (broker.roots["demo"][0] / filename).write_bytes(data)
    with pytest.raises(a.AssetError) as error:
        broker.snapshot("demo", "robot.usda")
    assert error.value.code in {"USDZ_CONTAINER", "UNSUPPORTED_ARCHIVE"}
    assert not list(broker.staging.iterdir())


def test_snapshot_quota_and_failed_copy_cleanup(broker, monkeypatch):
    monkeypatch.setattr(a, "MAX_SNAPSHOTS", 1)
    first = broker.snapshot("demo", "robot.usda")
    assert broker.snapshot("demo", "robot.usda") == first
    (broker.roots["demo"][0] / "parts/material.usda").write_text("changed dependency")
    with pytest.raises(a.AssetError, match="quota"):
        broker.snapshot("demo", "robot.usda")
    assert len(list(broker.staging.iterdir())) == 1


def test_snapshot_reuse_verifies_content_and_preserves_consumers(broker):
    first = broker.snapshot("demo", "robot.usda")
    filename, _ = broker.verify(first["snapshot_id"], first["manifest_sha256"])
    original = filename.read_bytes()
    source = broker.roots["demo"][0] / "parts/material.usda"
    source.write_text("new dependency")
    second = broker.snapshot("demo", "robot.usda")
    assert second["snapshot_id"] != first["snapshot_id"]
    assert filename.read_bytes() == original
    assert broker.snapshot("demo", "robot.usda") == second
    second_file, _ = broker.verify(second["snapshot_id"], second["manifest_sha256"])
    second_file.chmod(0o600)
    second_file.write_text("tampered")
    with pytest.raises(a.AssetError, match="content changed"):
        broker.snapshot("demo", "robot.usda")
    assert len(list(broker.staging.iterdir())) == 2


def test_snapshot_reuse_does_not_follow_manifest_symlink(broker):
    snap = broker.snapshot("demo", "robot.usda")
    manifest = broker.staging / snap["snapshot_id"] / "manifest.json"
    saved = manifest.read_bytes()
    manifest.unlink()
    outside = broker.staging.parent / "outside.json"
    outside.write_bytes(saved)
    manifest.symlink_to(outside)
    # Its lstat size can differ, in which case it is not eligible for reuse.
    result = broker.snapshot("demo", "robot.usda")
    assert result["snapshot_id"] != snap["snapshot_id"]
    assert outside.read_bytes() == saved


def test_search_refresh_and_bounds(broker):
    assert len(broker.search("robot")["files"]) == 1
    (broker.roots["demo"][0] / "RobotTwo.usd").write_text("#usda 1.0")
    assert len(broker.search("ROBOT")["files"]) == 2
    assert broker.search("", 1)["truncated"]
    with pytest.raises(a.AssetError):
        broker.search("", 0)


def test_scan_and_search_skip_a_too_deep_folder_and_name_it(broker):
    root = broker.roots["demo"][0]
    deep = root.joinpath(*[f"d{i}" for i in range(1, 10)])
    deep.mkdir(parents=True)
    (deep / "deep.usda").write_text("#usda 1.0")
    skipped = "d1/d2/d3/d4/d5/d6/d7/d8/"

    scan = broker.scan("demo")
    assert "robot.usda" in [item["relative_path"] for item in scan["files"]]
    assert scan["depth_limit"] == a.MAX_DEPTH == 8
    assert scan["skipped_deep"] == [skipped]
    assert "deeper than 8" in scan["note"]

    found = broker.search("deep")
    assert found["files"] == []
    assert found["skipped_deep"] == [dict(root_id="demo", path=skipped)]
    assert found["depth_limit"] == 8

    # A snapshot copies the whole project, so it still refuses rather than copy it short.
    with pytest.raises(a.AssetError, match="depth exceeds 8") as caught:
        broker.snapshot("demo", "robot.usda")
    assert caught.value.code == "LIMIT" and skipped in str(caught.value)


def test_with_no_approval_file_both_sides_share_one_private_staging_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("ISAAC_MCP_ASSET_CONFIG", raising=False)
    monkeypatch.delenv("ISAAC_MCP_STAGING", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    host, kit = a.Broker(), a.Broker()
    expected = tmp_path / ".cache" / "isaac-mcp" / "staging"
    assert host.staging == kit.staging == expected and host.default_staging
    assert (expected.stat().st_mode & 0o777) == 0o700 and not host.roots  # it approves nothing


def test_default_staging_can_be_moved_tightened_and_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("ISAAC_MCP_ASSET_CONFIG", raising=False)
    where = tmp_path / "elsewhere"
    monkeypatch.setenv("ISAAC_MCP_STAGING", str(where))
    assert a.Broker().staging == where
    where.chmod(0o755)
    assert a.Broker().staging == where and (where.stat().st_mode & 0o077) == 0  # loose mode is tightened, not trusted
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("ISAAC_MCP_STAGING", str(blocker / "inside"))
    assert a.Broker().staging is None  # can't be made: off, as before


def test_an_explicit_approval_file_still_decides_staging(tmp_path, monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_STAGING", str(tmp_path / "default"))
    broker = a.Broker(str(tmp_path / "missing.json"))
    assert broker.staging is None and broker.config_error and not broker.default_staging


def test_many_imports_fit_and_a_nearly_full_disk_refuses(broker, monkeypatch):
    assert a.MAX_SNAPSHOTS >= 256  # a session importing many robots must not stop after a handful
    monkeypatch.setattr(a.shutil, "disk_usage", lambda path: type("Usage", (), dict(free=a.MIN_FREE_BYTES))())
    with pytest.raises(a.AssetError) as error:
        broker.snapshot("demo", "robot.usda")
    assert error.value.code == "QUOTA" and "MiB free" in str(error.value)
    assert not list(broker.staging.iterdir())


def test_a_folder_that_is_the_approved_root_picks_the_usd_named_after_it():
    """The recommended smallest folder is the root itself, whose relative name was empty, so Teapot/Teapot.usd was not picked."""
    from isaac_mcp import asset_formats

    files = ["Teapot.usd", "DrawModes.usd", "geo/Fancy.usd"]
    found = asset_formats.candidates(files, lambda rel: b"", "", "Teapot")
    assert asset_formats.choose(found)["entry"] == "Teapot.usd"
    with pytest.raises(a.AssetError):
        asset_formats.choose(asset_formats.candidates(files, lambda rel: b"", "", "Other"))
    inner = [f"sub/{f}" for f in ("sub.usd", "other.usd")]
    assert asset_formats.choose(asset_formats.candidates(inner, lambda rel: b"", "sub", "Root"))["entry"] == "sub/sub.usd"


def test_the_snapshot_quota_refusal_names_the_folder_and_what_to_do(broker, monkeypatch):
    monkeypatch.setattr(a, "MAX_SNAPSHOTS", 1)
    broker.snapshot("demo", "robot.usda")
    (broker.roots["demo"][0] / "robot.usda").write_text("#usda 1.0\n#\n")
    with pytest.raises(a.AssetError) as error:
        broker.snapshot("demo", "robot.usda")
    assert error.value.code == "QUOTA" and str(broker.staging) in str(error.value) and "delete" in str(error.value)

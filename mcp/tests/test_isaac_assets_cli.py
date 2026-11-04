"""Tests for scripts/isaac_assets.py (where, doctor, add, pin)."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "isaac_assets.py"

ENV_NAMES = (
    "ISAAC_MCP_ASSET_PATH",
    "ISAAC_MCP_ASSET_STRICT",
    "ISAAC_MCP_ASSET_REMOTE_ROOT",
    "ISAAC_MCP_PROJECT",
    "UR5E_DT_ASSET_CACHE",
    "UR5E_DT_ASSET_ROOT",
    "UR5E_DT_OFFLINE",
)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home@host"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    # A project override that points nowhere keeps cwd discovery out of the tests.
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(tmp_path / "no-project"))
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location("isaac_assets_cli_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Point the "checkout" at a temp folder so the default asset folder (next to it) is
    # never created beside the real checkout.
    module.asset_cache.REPO_ROOT = tmp_path / "isaac-mcp"
    return module


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(cli, capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def make_store(tmp_path, monkeypatch, name="store"):
    store = tmp_path / name
    store.mkdir()
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    return store


def read_pins(pack: Path) -> dict[str, str]:
    result = {}
    for line in (pack / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        result[name] = digest
    return result


def assert_sha256sum_compatible(pack: Path):
    """Every line must be `<64 hex>  <relpath>` and verify against the file."""
    lines = (pack / "SHA256SUMS").read_text().splitlines()
    assert lines
    names = []
    for line in lines:
        digest, sep, name = line.partition("  ")
        assert sep and len(digest) == 64 and int(digest, 16) >= 0
        assert not name.startswith(("/", "./", "*"))
        assert sha((pack / name).read_bytes()) == digest
        names.append(name)
    assert names == sorted(names)


# --------------------------------------------------------------------- where


def test_where_defaults(cli, capsys, tmp_path, monkeypatch):
    code, out, _ = run(cli, capsys, "where")
    assert code == 0
    assert "project file: none" in out
    assert "off" in out
    assert str(tmp_path / "isaac-mcp-assets") in out


def test_where_json_schema(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    code, out, _ = run(cli, capsys, "where", "--json")
    data = json.loads(out)
    assert code == 0
    assert set(data) == {"project_file", "pack", "pack_source", "stores", "strict", "deprecated_env"}
    assert data["project_file"] is None
    assert data["stores"] == [{"path": str(store.resolve()), "origin": "user"}]
    assert data["strict"] is False
    assert data["deprecated_env"] == []


def test_where_project_stores_first_and_strict(cli, capsys, tmp_path, monkeypatch):
    user = make_store(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "isaac-mcp.json").write_text(json.dumps({"asset_path": ["assets"], "strict": True}))
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(proj))
    code, out, _ = run(cli, capsys, "where", "--json")
    data = json.loads(out)
    assert code == 0
    assert data["project_file"] == str((proj / "isaac-mcp.json").resolve())
    assert [s["origin"] for s in data["stores"]] == ["project", "user"]
    assert data["stores"][0]["path"] == str((proj / "assets").resolve())
    assert data["stores"][1]["path"] == str(user.resolve())
    assert data["strict"] is True


def test_where_reports_deprecated_env(cli, capsys, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(legacy))
    monkeypatch.setenv("UR5E_DT_OFFLINE", "1")
    code, out, _ = run(cli, capsys, "where", "--json")
    data = json.loads(out)
    assert code == 0
    assert data["strict"] is True
    assert data["stores"][0]["path"] == str(legacy.resolve())
    assert len(data["deprecated_env"]) == 2
    assert any("UR5E_DT_ASSET_CACHE" in item for item in data["deprecated_env"])
    code, out, _ = run(cli, capsys, "where")
    assert "UR5E_DT_OFFLINE" in out


def test_where_bad_project_file_is_clean_error(cli, capsys, tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "isaac-mcp.json").write_text("{not json")
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(proj))
    code, _, err = run(cli, capsys, "where")
    assert code == 2
    assert "error:" in err and "Traceback" not in err


# ----------------------------------------------------------------------- add


def test_add_file_defaults_and_pins(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    src = tmp_path / "arm.usd"
    src.write_bytes(b"arm-data")
    code, out, _ = run(cli, capsys, "add", str(src), "--as", "custom/arm.usd")
    assert code == 0
    pack = store / "ur5e-dt"
    assert (pack / "custom" / "arm.usd").read_bytes() == b"arm-data"
    assert "added custom/arm.usd" in out and sha(b"arm-data")[:12] in out
    assert read_pins(pack) == {"custom/arm.usd": sha(b"arm-data")}
    assert_sha256sum_compatible(pack)


def test_add_file_uses_basename_without_as(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    src = tmp_path / "bench.usd"
    src.write_bytes(b"b")
    assert run(cli, capsys, "add", str(src))[0] == 0
    assert (store / "ur5e-dt" / "bench.usd").is_file()


def test_add_directory_keeps_layout_and_merges_pins(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    pack.mkdir()
    (pack / "keep.usd").write_bytes(b"keep")
    (pack / "SHA256SUMS").write_text(f"{sha(b'keep')}  keep.usd\n{'0' * 64}  other/gone.usd\n")
    src = tmp_path / "srcdir"
    (src / "sub").mkdir(parents=True)
    (src / "a.usd").write_bytes(b"a")
    (src / "sub" / "b.usd").write_bytes(b"b")
    code, out, _ = run(cli, capsys, "add", str(src))
    assert code == 0
    assert (pack / "a.usd").read_bytes() == b"a"
    assert (pack / "sub" / "b.usd").read_bytes() == b"b"
    pins = read_pins(pack)
    assert pins == {
        "a.usd": sha(b"a"),
        "keep.usd": sha(b"keep"),
        "other/gone.usd": "0" * 64,  # lines for other files are preserved
        "sub/b.usd": sha(b"b"),
    }
    assert "2 file(s) added" in out
    assert list(pins) == sorted(pins)


def test_add_tar_gz_and_zip(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    tgz = tmp_path / "pack.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        data = b"tar-data"
        info = tarfile.TarInfo("robots/a.usd")
        info.size = len(data)
        info.uid = 12345
        tf.addfile(info, io.BytesIO(data))
    zp = tmp_path / "pack.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("robots/z.usd", b"zip-data")
    assert run(cli, capsys, "add", str(tgz))[0] == 0
    assert run(cli, capsys, "add", str(zp))[0] == 0
    pack = store / "ur5e-dt"
    assert (pack / "robots" / "a.usd").read_bytes() == b"tar-data"
    assert (pack / "robots" / "z.usd").read_bytes() == b"zip-data"
    assert set(read_pins(pack)) == {"robots/a.usd", "robots/z.usd"}
    assert_sha256sum_compatible(pack)


def test_add_custom_pack_and_store_flag(cli, capsys, tmp_path):
    store = tmp_path / "elsewhere"
    src = tmp_path / "x.usd"
    src.write_bytes(b"x")
    assert run(cli, capsys, "add", str(src), "--store", str(store), "--pack", "lab")[0] == 0
    assert (store / "lab" / "x.usd").is_file()
    assert (store / "lab" / "SHA256SUMS").is_file()


def test_add_refuses_overwrite_without_force(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    src = tmp_path / "x.usd"
    src.write_bytes(b"one")
    assert run(cli, capsys, "add", str(src))[0] == 0
    src.write_bytes(b"two")
    code, _, err = run(cli, capsys, "add", str(src))
    assert code == 2 and "--force" in err
    assert (store / "ur5e-dt" / "x.usd").read_bytes() == b"one"
    assert run(cli, capsys, "add", str(src), "--force")[0] == 0
    pack = store / "ur5e-dt"
    assert pack.joinpath("x.usd").read_bytes() == b"two"
    assert read_pins(pack) == {"x.usd": sha(b"two")}


def test_add_overwrite_refusal_writes_nothing(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    pack.mkdir()
    (pack / "b.usd").write_bytes(b"old")
    src = tmp_path / "d"
    src.mkdir()
    (src / "a.usd").write_bytes(b"a")
    (src / "b.usd").write_bytes(b"new")
    assert run(cli, capsys, "add", str(src))[0] == 2
    assert not (pack / "a.usd").exists()
    assert (pack / "b.usd").read_bytes() == b"old"


@pytest.mark.parametrize("bad", ["../evil.usd", "/abs/evil.usd", "a/../../evil.usd"])
def test_add_rejects_unsafe_tar_names(cli, capsys, tmp_path, monkeypatch, bad):
    store = make_store(tmp_path, monkeypatch)
    arc = tmp_path / "bad.tar"
    with tarfile.open(arc, "w") as tf:
        info = tarfile.TarInfo(bad)
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    code, _, err = run(cli, capsys, "add", str(arc))
    assert code == 2 and "unsafe" in err
    assert not (store / "ur5e-dt").exists()
    assert not (tmp_path / "evil.usd").exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE])
def test_add_rejects_links_and_devices_in_tar(cli, capsys, tmp_path, monkeypatch, kind):
    store = make_store(tmp_path, monkeypatch)
    arc = tmp_path / "bad.tar"
    with tarfile.open(arc, "w") as tf:
        info = tarfile.TarInfo("link")
        info.type = kind
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    code, _, err = run(cli, capsys, "add", str(arc))
    assert code == 2 and "unsafe archive member" in err
    assert not (store / "ur5e-dt").exists()


def test_add_rejects_unsafe_zip_member(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    arc = tmp_path / "bad.zip"
    with zipfile.ZipFile(arc, "w") as zf:
        zf.writestr("../evil.usd", b"x")
    code, _, err = run(cli, capsys, "add", str(arc))
    assert code == 2 and "unsafe" in err
    assert not (store / "ur5e-dt").exists()


def test_add_rejects_symlink_in_zip(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    arc = tmp_path / "bad.zip"
    with zipfile.ZipFile(arc, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    code, _, err = run(cli, capsys, "add", str(arc))
    assert code == 2 and "unsafe archive member" in err


def test_add_rejects_symlink_in_source_directory(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    src = tmp_path / "d"
    src.mkdir()
    (src / "real").write_bytes(b"r")
    (src / "ln").symlink_to(src / "real")
    code, _, err = run(cli, capsys, "add", str(src))
    assert code == 2 and "symlink" in err


def test_add_refuses_store_inside_repo(cli, capsys, tmp_path):
    src = tmp_path / "x.usd"
    src.write_bytes(b"x")
    inside = REPO / "exts" / "ur5e-dt" / "assets"
    before = sorted(p.name for p in inside.glob("*")) if inside.exists() else None
    code, _, err = run(cli, capsys, "add", str(src), "--store", str(REPO / "private-store"))
    assert code == 2 and "inside the repository" in err
    assert not (REPO / "private-store").exists()
    code, _, err = run(cli, capsys, "add", str(src), "--store", str(inside))
    assert code == 2
    after = sorted(p.name for p in inside.glob("*")) if inside.exists() else None
    assert before == after


def test_add_refuses_store_via_symlink_into_repo(cli, capsys, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(REPO)
    src = tmp_path / "x.usd"
    src.write_bytes(b"x")
    code, _, err = run(cli, capsys, "add", str(src), "--store", str(link / "sub"))
    assert code == 2 and "inside the repository" in err


def test_add_refuses_default_store_inside_repo(cli, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(REPO / "private-store"))
    src = tmp_path / "x.usd"
    src.write_bytes(b"x")
    code, _, err = run(cli, capsys, "add", str(src))
    assert code == 2 and "inside the repository" in err
    assert not (REPO / "private-store").exists()


def test_add_missing_source_and_bad_args(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    assert run(cli, capsys, "add", str(tmp_path / "nope"))[0] == 2
    src = tmp_path / "x.usd"
    src.write_bytes(b"x")
    assert run(cli, capsys, "add", str(src), "--as", "../x")[0] == 2
    assert run(cli, capsys, "add", str(src), "--pack", "a/b")[0] == 2
    d = tmp_path / "d"
    d.mkdir()
    (d / "f").write_bytes(b"f")
    assert run(cli, capsys, "add", str(d), "--as", "x")[0] == 2
    with pytest.raises(SystemExit) as exc:
        cli.main(["add"])
    assert exc.value.code == 2


def test_add_cannot_write_pin_file_as_asset(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    src = tmp_path / "SHA256SUMS"
    src.write_text("junk")
    code, _, err = run(cli, capsys, "add", str(src))
    assert code == 2 and "SHA256SUMS" in err


# ----------------------------------------------------------------------- pin


def test_pin_regenerates_sorted_and_excludes_pin_file(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    (pack / "z").mkdir(parents=True)
    (pack / "z" / "last.usd").write_bytes(b"last")
    (pack / "a.usd").write_bytes(b"first")
    (pack / "SHA256SUMS").write_text(f"{'1' * 64}  stale.usd\n")
    code, out, _ = run(cli, capsys, "pin")
    assert code == 0 and "pinned 2 file(s)" in out
    assert read_pins(pack) == {"a.usd": sha(b"first"), "z/last.usd": sha(b"last")}
    assert_sha256sum_compatible(pack)
    # Idempotent: the pin file never lists itself.
    run(cli, capsys, "pin")
    assert "SHA256SUMS" not in read_pins(pack)


def test_pin_missing_pack_and_repo_store(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    code, _, err = run(cli, capsys, "pin")
    assert code == 2 and "does not exist" in err
    code, _, err = run(cli, capsys, "pin", "--store", str(REPO))
    assert code == 2 and "inside the repository" in err


# -------------------------------------------------------------------- doctor


def test_doctor_json_schema_and_text(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    src = tmp_path / "arm.usd"
    src.write_bytes(b"arm")
    run(cli, capsys, "add", str(src), "--as", "custom/arm.usd")
    code, out, _ = run(cli, capsys, "doctor", "--json")
    data = json.loads(out)
    assert code == 0 and data["ok"] is True
    assert set(data) == {"project_file", "stores", "strict", "assets", "problems", "ok"}
    by_name = {a["relpath"]: a for a in data["assets"]}
    arm = by_name["custom/arm.usd"]
    assert arm["layer"] == "store" and arm["status"] == "pinned"
    assert arm["sha256"] == sha(b"arm")
    assert Path(arm["path"]) == (store / "ur5e-dt" / "custom" / "arm.usd").resolve()
    assert by_name["workstation/vention.usd"]["status"] == "missing"  # optional: not an error
    assert by_name["robot/ur5e.usd"]["layer"] == "remote"

    code, out, _ = run(cli, capsys, "doctor")
    assert code == 0
    assert "ASSET" in out and "custom/arm.usd" in out and sha(b"arm")[:12] in out
    assert "OK:" in out
    # Columns are aligned: the layer column starts at the same offset on each row.
    rows = [line for line in out.splitlines() if line.startswith(("custom/arm", "robot/ur5e.usd"))]
    assert len({line.index("store" if "store" in line.split()[1] else "remote") for line in rows}) == 1


def test_doctor_corrupt_pin_exits_1(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    src = tmp_path / "arm.usd"
    src.write_bytes(b"arm")
    run(cli, capsys, "add", str(src), "--as", "custom/arm.usd")
    (store / "ur5e-dt" / "custom" / "arm.usd").write_bytes(b"tampered")
    code, out, _ = run(cli, capsys, "doctor")
    assert code == 1 and "corrupt" in out and "PROBLEMS" in out
    code, out, _ = run(cli, capsys, "doctor", "--json")
    data = json.loads(out)
    assert code == 1 and data["ok"] is False
    assert any("custom/arm.usd" in p for p in data["problems"])
    # `pin` accepts the change and doctor goes green again.
    assert run(cli, capsys, "pin")[0] == 0
    assert run(cli, capsys, "doctor")[0] == 0


def test_doctor_require(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    code, out, _ = run(cli, capsys, "doctor", "--require", "custom/arm.usd")
    assert code == 1 and "custom/arm.usd" in out
    # A remote-resolvable asset is not local either.
    assert run(cli, capsys, "doctor", "--require", "robot/ur5e.usd")[0] == 1
    src = tmp_path / "arm.usd"
    src.write_bytes(b"arm")
    run(cli, capsys, "add", str(src), "--as", "custom/arm.usd")
    assert run(cli, capsys, "doctor", "--require", "custom/arm.usd")[0] == 0


def test_doctor_local_only(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    assert run(cli, capsys, "doctor")[0] == 0
    code, out, _ = run(cli, capsys, "doctor", "--local-only")
    assert code == 1 and "remotely" in out
    monkeypatch.setenv("ISAAC_MCP_ASSET_STRICT", "1")
    assert run(cli, capsys, "doctor", "--local-only")[0] == 0


def test_doctor_sees_project_store(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    (proj / "assets" / "ur5e-dt" / "robot").mkdir(parents=True)
    robot = proj / "assets" / "ur5e-dt" / "robot" / "ur5e.usd"
    robot.write_bytes(b"lab-modified")
    (proj / "isaac-mcp.json").write_text(json.dumps({"asset_path": ["assets"]}))
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(proj))
    assert run(cli, capsys, "pin", "--store", str(proj / "assets"))[0] == 0
    code, out, _ = run(cli, capsys, "doctor", "--json", "--require", "robot/ur5e.usd")
    data = json.loads(out)
    assert code == 0
    info = {a["relpath"]: a for a in data["assets"]}["robot/ur5e.usd"]
    assert info["layer"] == "project" and info["status"] == "pinned"
    assert data["project_file"] == str((proj / "isaac-mcp.json").resolve())
    assert data["stores"][0]["origin"] == "project"


def test_legacy_asset_cache_env_still_works(cli, capsys, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy-cache"
    legacy.mkdir()
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", str(legacy))
    src = tmp_path / "arm.usd"
    src.write_bytes(b"arm")
    assert run(cli, capsys, "add", str(src), "--as", "custom/arm.usd")[0] == 0
    assert (legacy / "ur5e-dt" / "custom" / "arm.usd").is_file()
    code, out, _ = run(cli, capsys, "doctor", "--json", "--require", "custom/arm.usd")
    assert code == 0
    info = {a["relpath"]: a for a in json.loads(out)["assets"]}["custom/arm.usd"]
    assert info["status"] == "pinned"


# --------------------------------------------------------- review regressions (PR 51)


def tar_bytes(members: list[tuple[str, bytes | None]]) -> bytes:
    """Build a tar; ``None`` content makes a directory entry."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tf.addfile(info)
            else:
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_add_does_not_follow_a_planted_part_symlink(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    pack.mkdir()
    victim = tmp_path / "checkout" / "tracked.txt"
    victim.parent.mkdir()
    victim.write_text("do not touch")
    (pack / "x.usd.part").symlink_to(victim)
    source = tmp_path / "x.usd"
    source.write_bytes(b"asset bytes")
    code, _, err = run(cli, capsys, "add", str(source))
    assert code == 0, err
    assert victim.read_text() == "do not touch"
    assert not (pack / "x.usd").is_symlink()
    assert (pack / "x.usd").read_bytes() == b"asset bytes"
    assert (pack / "x.usd.part").is_symlink()  # untouched: the tool uses its own temp name


def test_add_leaves_an_unrelated_part_file_alone(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    pack.mkdir()
    (pack / "x.usd.part").write_bytes(b"someone else's")
    source = tmp_path / "x.usd"
    source.write_bytes(b"asset")
    assert run(cli, capsys, "add", str(source))[0] == 0
    assert (pack / "x.usd.part").read_bytes() == b"someone else's"
    assert sorted(p.name for p in pack.iterdir() if p.name.startswith(".")) == []


def test_add_refuses_a_dangling_symlink_destination_without_writing_through_it(
    cli, capsys, tmp_path, monkeypatch
):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    pack.mkdir()
    elsewhere = tmp_path / "elsewhere.bin"
    (pack / "x.usd").symlink_to(elsewhere)
    source = tmp_path / "x.usd"
    source.write_bytes(b"asset")
    code, _, err = run(cli, capsys, "add", str(source))
    assert code == 2
    assert not elsewhere.exists()


def test_add_refuses_a_symlinked_subdirectory_in_the_pack(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    pack.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (pack / "robot").symlink_to(outside, target_is_directory=True)
    source = tmp_path / "src"
    (source / "robot").mkdir(parents=True)
    (source / "robot" / "a.usd").write_bytes(b"a")
    code, _, err = run(cli, capsys, "add", str(source))
    assert code == 2
    assert list(outside.iterdir()) == []


def test_archive_total_size_limit_is_enforced_on_written_bytes(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.bin", b"\0" * (4 << 20))
    code, _, err = run(cli, capsys, "add", str(archive), "--max-total-bytes", str(1 << 20))
    assert code == 2
    assert "expands past" in err
    assert not (tmp_path / "store" / "ur5e-dt" / "big.bin").exists()


def test_archive_per_file_and_member_limits(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    archive = tmp_path / "many.tar"
    archive.write_bytes(tar_bytes([(f"f{i}.usd", b"x" * 10) for i in range(5)]))
    code, _, err = run(cli, capsys, "add", str(archive), "--max-members", "3")
    assert code == 2 and "more than 3 members" in err
    code, _, err = run(cli, capsys, "add", str(archive), "--max-file-bytes", "5")
    assert code == 2 and "--max-file-bytes" in err
    assert run(cli, capsys, "add", str(archive))[0] == 0


def test_archive_with_a_dot_root_entry_is_accepted(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    archive = tmp_path / "dot.tar"
    archive.write_bytes(tar_bytes([(".", None), ("./robot", None), ("./robot/a.usd", b"a")]))
    code, _, err = run(cli, capsys, "add", str(archive))
    assert code == 0, err
    assert (store / "ur5e-dt" / "robot" / "a.usd").read_bytes() == b"a"


def test_star_prefixed_names_round_trip_and_are_verified(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    source = tmp_path / "src.usd"
    source.write_bytes(b"original")
    assert run(cli, capsys, "add", str(source), "--as", "*custom.usd")[0] == 0
    cache = cli.asset_cache
    assert cache.resolve_info("*custom.usd")["status"] == "pinned"
    (store / "ur5e-dt" / "*custom.usd").write_bytes(b"tampered")
    assert cache.resolve_info("*custom.usd")["status"] == "corrupt"


@pytest.mark.parametrize("name", [" lead.usd", "trail.usd ", "tab\tname.usd"])
def test_names_the_pin_format_cannot_hold_are_refused(cli, capsys, tmp_path, monkeypatch, name):
    make_store(tmp_path, monkeypatch)
    source = tmp_path / "src.usd"
    source.write_bytes(b"x")
    code, _, err = run(cli, capsys, "add", str(source), "--as", name)
    assert code == 2 and "unsupported" in err


# ------------------------------------------------- round-2 review regressions (PR 51)


def test_r1_no_hard_links_never_overwrites_a_file_that_appears_meanwhile(
    cli, capsys, tmp_path, monkeypatch
):
    """Hard links unavailable, and another writer creates the destination at the worst moment.

    The file is planted right before the first call that could replace it (rename/replace in
    a check-then-rename fallback) or create it (the exclusive open in the safe fallback).
    """
    import errno

    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    pack.mkdir()
    source = tmp_path / "x.usd"
    source.write_bytes(b"NEW")
    planted = []

    def plant_once():
        if not planted:
            (pack / "x.usd").write_bytes(b"OTHER WRITER")
            planted.append(True)

    def no_link(*args, **kwargs):
        raise OSError(errno.EOPNOTSUPP, "no hard links here")

    real_replace, real_rename, real_open = os.replace, os.rename, os.open

    def replace(*args, **kwargs):
        plant_once()
        return real_replace(*args, **kwargs)

    def rename(*args, **kwargs):
        plant_once()
        return real_rename(*args, **kwargs)

    def open_(path, flags, *args, **kwargs):
        if path == "x.usd" and flags & os.O_EXCL:
            plant_once()
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(cli.os, "link", no_link)
    monkeypatch.setattr(cli.os, "replace", replace)
    monkeypatch.setattr(cli.os, "rename", rename)
    monkeypatch.setattr(cli.os, "open", open_)
    code, _, err = run(cli, capsys, "add", str(source))
    assert code == 2 and "refusing to overwrite" in err
    assert (pack / "x.usd").read_bytes() == b"OTHER WRITER"
    assert [p.name for p in pack.iterdir() if p.name.endswith(".part")] == []


def test_r1_no_hard_links_still_installs_a_new_file(cli, capsys, tmp_path, monkeypatch):
    import errno

    store = make_store(tmp_path, monkeypatch)
    source = tmp_path / "x.usd"
    source.write_bytes(b"NEW")

    def no_link(*args, **kwargs):
        raise OSError(errno.EXDEV, "cross-device")

    monkeypatch.setattr(cli.os, "link", no_link)
    assert run(cli, capsys, "add", str(source))[0] == 0
    pack = store / "ur5e-dt"
    assert (pack / "x.usd").read_bytes() == b"NEW"
    assert [p.name for p in pack.iterdir() if p.name.endswith(".part")] == []


def test_r2_extended_header_data_counts_against_the_limits(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo("a.usd")
        info.size = 1
        info.pax_headers = {"comment": "x" * (16 << 20)}
        tf.addfile(info, io.BytesIO(b"1"))
    archive = tmp_path / "pax.tar.gz"
    archive.write_bytes(buf.getvalue())
    assert archive.stat().st_size < 100_000  # a small file that expands to 16 MiB of headers
    code, _, err = run(
        cli, capsys, "add", str(archive),
        "--max-members", "1", "--max-file-bytes", "1", "--max-total-bytes", "1",
    )
    # Refused either by the per-header cap (checked first) or by the stream bound.
    assert code == 2 and ("extended tar header" in err or "expands past" in err)
    assert not (tmp_path / "store" / "ur5e-dt" / "a.usd").exists()


def test_r2_zip_member_count_is_checked_before_the_directory_is_parsed(
    cli, capsys, tmp_path, monkeypatch
):
    make_store(tmp_path, monkeypatch)
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for i in range(20_000):
            zf.writestr(f"f{i}.usd", b"")

    def must_not_parse(*args, **kwargs):
        raise AssertionError("ZipFile must not be constructed for an over-limit archive")

    monkeypatch.setattr(cli.zipfile, "ZipFile", must_not_parse)
    code, _, err = run(cli, capsys, "add", str(archive), "--max-members", "1")
    assert code == 2 and "more than 1" in err


def test_a_failed_copy_leaves_no_temporary_files(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    source = tmp_path / "x.usd"
    source.write_bytes(b"data")

    def boom(*args, **kwargs):
        raise OSError("disk exploded")

    monkeypatch.setattr(cli.shutil, "copyfileobj", boom)
    code, _, err = run(cli, capsys, "add", str(source))
    assert code == 2
    pack = store / "ur5e-dt"
    assert [p.name for p in pack.iterdir()] == []


def test_r6_a_refused_add_creates_nothing_through_a_symlinked_ancestor(
    cli, capsys, tmp_path, monkeypatch
):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    pack.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (pack / "robot").symlink_to(outside, target_is_directory=True)
    source = tmp_path / "src"
    (source / "robot" / "new" / "sub").mkdir(parents=True)
    (source / "robot" / "new" / "sub" / "asset.usd").write_bytes(b"a")
    code, _, err = run(cli, capsys, "add", str(source))
    assert code == 2
    assert list(outside.iterdir()) == []  # not even the new/ and new/sub folders


def test_the_pin_file_is_written_even_when_a_stale_temp_name_exists(
    cli, capsys, tmp_path, monkeypatch
):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    pack.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    (pack / "SHA256SUMS.tmp").symlink_to(victim)
    (pack / "a.usd").write_bytes(b"a")
    assert run(cli, capsys, "pin")[0] == 0
    assert victim.read_text() == "keep"
    assert (pack / "SHA256SUMS").read_text().startswith(sha(b"a"))


def test_tar_extraction_closes_every_file_handle(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    archive = tmp_path / "a.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("a.usd")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"1"))
    archive.write_bytes(buf.getvalue())
    opened = []
    real_open = open

    def tracking_open(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if str(path) == str(archive):
            opened.append(handle)
        return handle

    monkeypatch.setattr("builtins.open", tracking_open)
    assert run(cli, capsys, "add", str(archive))[0] == 0
    assert opened and all(h.closed for h in opened)


# ------------------------------------------------- round-3 review regressions (PR 51)


def _forge_zip_counts(data: bytes, count: int) -> bytes:
    """Rewrite both entry counts in the end-of-central-directory record."""
    import struct

    i = data.rfind(b"PK\x05\x06")
    return data[: i + 8] + struct.pack("<HH", count, count) + data[i + 12 :]


def test_n1_a_forged_zip_entry_count_cannot_hide_a_huge_directory(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(25_000):
            zf.writestr(f"f{i}.usd", b"")
    archive = tmp_path / "forged.zip"
    archive.write_bytes(_forge_zip_counts(buf.getvalue(), 1))

    def must_not_parse(*args, **kwargs):
        raise AssertionError("ZipFile must not parse an over-limit central directory")

    monkeypatch.setattr(cli.zipfile, "ZipFile", must_not_parse)
    code, _, err = run(cli, capsys, "add", str(archive), "--max-members", "1")
    assert code == 2 and "more than 1" in err


def test_n1_a_zip_whose_declared_count_disagrees_with_its_records_is_refused(
    cli, capsys, tmp_path, monkeypatch
):
    make_store(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(3):
            zf.writestr(f"f{i}.usd", b"x")
    archive = tmp_path / "lying.zip"
    archive.write_bytes(_forge_zip_counts(buf.getvalue(), 2))
    code, _, err = run(cli, capsys, "add", str(archive))
    assert code == 2 and "declares 2" in err


def test_n1_an_honest_zip_still_installs(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("robot/a.usd", b"a")
        zf.writestr("robot/b.usd", b"b")
    assert run(cli, capsys, "add", str(archive))[0] == 0
    assert (store / "ur5e-dt" / "robot" / "b.usd").read_bytes() == b"b"


def test_n2_a_failed_publish_leaves_no_temporary_file(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    (pack / "directory.usd").mkdir(parents=True)  # cannot be replaced by a file
    source = tmp_path / "directory.usd"
    source.write_bytes(b"x")
    code, _, _ = run(cli, capsys, "add", str(source), "--force")
    assert code == 2
    assert [p.name for p in pack.iterdir() if p.name.endswith(".part")] == []


def test_n2_a_failed_pin_write_leaves_no_temporary_file(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    pack.mkdir()
    (pack / "a.usd").write_bytes(b"a")
    (pack / "SHA256SUMS").mkdir()  # the pin file cannot be replaced
    code, _, _ = run(cli, capsys, "pin")
    assert code == 2
    assert [p.name for p in pack.iterdir() if p.name.endswith(".part")] == []


def _tar_gz_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("a.usd")
        info.size = 4096
        tf.addfile(info, io.BytesIO(b"x" * 4096))
    return buf.getvalue()


@pytest.mark.parametrize(
    "name,payload",
    [
        ("bad-xz.tar", b"\xfd7zXZ\x00" + b"corrupt bytes" * 50),
        ("bad-bz2.tar", b"BZh9" + b"corrupt bytes" * 50),
        ("truncated.tar.gz", None),
        ("truncated.tar", None),
        ("garbage.zip", b"PK\x03\x04 not really a zip"),
    ],
)
def test_n3_corrupt_or_truncated_archives_are_clean_refusals(
    cli, capsys, tmp_path, monkeypatch, name, payload
):
    make_store(tmp_path, monkeypatch)
    if payload is None:
        if name.endswith(".tar.gz"):
            payload = _tar_gz_bytes()[:-40]
        else:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                info = tarfile.TarInfo("a.usd")
                info.size = 4096
                tf.addfile(info, io.BytesIO(b"x" * 4096))
            payload = buf.getvalue()[:1000]
    archive = tmp_path / name
    archive.write_bytes(payload)
    code, _, err = run(cli, capsys, "add", str(archive))
    assert code == 2, err
    assert "Traceback" not in err


# ------------------------------------------------- round-4 review regressions (PR 51)


def test_v1_a_valid_offset_only_zip64_archive_installs(cli, capsys, tmp_path, monkeypatch):
    """Python's own writer emits zip64 records when the data starts past 2 GiB."""
    store = make_store(tmp_path, monkeypatch)
    archive = tmp_path / "big-offset.zip"
    with open(archive, "wb") as fh:
        fh.seek((1 << 31) + 1)  # sparse: no real disk is used
        with zipfile.ZipFile(fh, "w") as zf:
            zf.writestr("robot/a.usd", b"zip64 payload")
    if archive.stat().st_blocks * 512 > 200 << 20:
        pytest.skip("filesystem does not support sparse files")
    code, _, err = run(cli, capsys, "add", str(archive))
    assert code == 0, err
    assert (store / "ur5e-dt" / "robot" / "a.usd").read_bytes() == b"zip64 payload"


def _zip64_with_zeroed_legacy_fields(entries: int) -> bytes:
    """A zip whose true counts live only in the zip64 records; legacy fields are zero."""
    import struct

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(entries):
            zf.writestr(f"f{i}.usd", b"")
    data = buf.getvalue()
    i = data.rfind(b"PK\x05\x06")
    cd_size, cd_offset = struct.unpack("<LL", data[i + 12 : i + 20])
    record = b"PK\x06\x06" + struct.pack("<QHHLLQQQQ", 44, 45, 45, 0, 0, entries, entries, cd_size, cd_offset)
    locator = b"PK\x06\x07" + struct.pack("<LQL", 0, i, 1)
    legacy = data[i : i + 8] + struct.pack("<HHLL", 0, 0, 0, 0) + data[i + 20 :]
    return data[:i] + record + locator + legacy


def test_v1_zip64_records_are_used_even_when_legacy_fields_are_zero(
    cli, capsys, tmp_path, monkeypatch
):
    make_store(tmp_path, monkeypatch)
    archive = tmp_path / "hidden.zip"
    archive.write_bytes(_zip64_with_zeroed_legacy_fields(25_000))

    def must_not_parse(*args, **kwargs):
        raise AssertionError("ZipFile must not load an over-limit central directory")

    monkeypatch.setattr(cli.zipfile, "ZipFile", must_not_parse)
    code, _, err = run(cli, capsys, "add", str(archive), "--max-members", "1")
    assert code == 2 and "more than 1" in err


def test_v1_the_synthetic_zip64_archive_is_a_valid_zip(tmp_path):
    """Guards the helper above: Python must read what the test builds."""
    archive = tmp_path / "z.zip"
    archive.write_bytes(_zip64_with_zeroed_legacy_fields(3))
    with zipfile.ZipFile(archive) as zf:
        assert len(zf.namelist()) == 3


def _patch_zip_header_field(data: bytes, local_off: int, central_off: int, value: int) -> bytes:
    import struct

    out = bytearray(data)
    packed = struct.pack("<H", value)
    out[local_off : local_off + 2] = packed
    c = bytes(out).find(b"PK\x01\x02")
    out[c + central_off : c + central_off + 2] = packed
    return bytes(out)


def test_v2_an_encrypted_zip_entry_is_a_clean_refusal(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.usd", b"secret")
    archive = tmp_path / "enc.zip"
    # General-purpose flag bit 0 = encrypted (local header offset 6, central offset 8).
    archive.write_bytes(_patch_zip_header_field(buf.getvalue(), 6, 8, 0x1))
    code, _, err = run(cli, capsys, "add", str(archive))
    assert code == 2 and "Traceback" not in err and "unsupported zip entry" in err


def test_v2_an_unsupported_zip_compression_method_is_a_clean_refusal(
    cli, capsys, tmp_path, monkeypatch
):
    make_store(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.usd", b"data")
    archive = tmp_path / "method.zip"
    # Compression method field: local header offset 8, central offset 10. 99 = AES.
    archive.write_bytes(_patch_zip_header_field(buf.getvalue(), 8, 10, 99))
    code, _, err = run(cli, capsys, "add", str(archive))
    assert code == 2 and "Traceback" not in err and "unsupported zip entry" in err


def test_v3_a_failed_cleanup_does_not_hide_the_original_error(cli, capsys, tmp_path, monkeypatch):
    import errno

    make_store(tmp_path, monkeypatch)
    source = tmp_path / "x.usd"
    source.write_bytes(b"x")

    def failing_publish(*args, **kwargs):
        raise OSError(errno.EIO, "disk on fire")

    real_unlink = os.unlink

    def failing_unlink(path, *args, **kwargs):
        if str(path).endswith(".part"):
            raise PermissionError(errno.EACCES, "cleanup denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(cli.packs, "publish", failing_publish)
    monkeypatch.setattr(cli.os, "unlink", failing_unlink)
    code, _, err = run(cli, capsys, "add", str(source))
    assert code == 2
    assert "disk on fire" in err and "cleanup denied" not in err


# ---------------------------------------------- final-review regressions (PR 51: M1, M3)


def _tar_gz_with_pax_comment(size: int) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo("a.usd")
        info.size = 1
        info.pax_headers = {"comment": "x" * size}
        tf.addfile(info, io.BytesIO(b"1"))
    return buf.getvalue()


def test_m1_an_oversized_pax_header_is_refused_before_it_is_read(cli, capsys, tmp_path, monkeypatch):
    import tracemalloc

    make_store(tmp_path, monkeypatch)
    archive = tmp_path / "pax.tar.gz"
    archive.write_bytes(_tar_gz_with_pax_comment(8 << 20))  # 8 MiB header, tiny archive
    tracemalloc.start()
    try:
        code, _, err = run(cli, capsys, "add", str(archive))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert code == 2 and "extended tar header" in err
    assert peak < 4 << 20, f"peak {peak} bytes: the header data was read into memory"


def test_m1_an_oversized_gnu_long_name_is_refused(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tf:
        info = tarfile.TarInfo("d/" * (600 << 10) + "a.usd")  # ~1.2 MiB name
        info.size = 1
        tf.addfile(info, io.BytesIO(b"1"))
    archive = tmp_path / "long.tar"
    archive.write_bytes(buf.getvalue())
    code, _, err = run(cli, capsys, "add", str(archive))
    assert code == 2 and "extended tar header" in err


def test_m1_ordinary_pax_headers_still_work(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    archive = tmp_path / "pax-ok.tar.gz"
    archive.write_bytes(_tar_gz_with_pax_comment(200))
    assert run(cli, capsys, "add", str(archive))[0] == 0
    assert (store / "ur5e-dt" / "a.usd").read_bytes() == b"1"


def test_m3_a_zip_needing_a_newer_zip_version_is_a_clean_refusal(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.usd", b"x")
    # "Version needed to extract" = 6.4: local header offset 4, central offset 6.
    archive = tmp_path / "v64.zip"
    archive.write_bytes(_patch_zip_header_field(buf.getvalue(), 4, 6, 64))
    code, _, err = run(cli, capsys, "add", str(archive))
    assert code == 2 and "Traceback" not in err and "unsupported zip archive" in err


def test_m3_a_zip_member_with_an_empty_name_is_a_clean_refusal(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    archive = tmp_path / "empty-name.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(zipfile.ZipInfo(""), b"x")
    code, _, err = run(cli, capsys, "add", str(archive))
    assert code == 2 and "Traceback" not in err and "empty name" in err


# ------------------------------------------- review follow-ups (M2, M4, F1, F2)


def _pax_record(key: str, value: str) -> bytes:
    body = f" {key}={value}\n"
    length = len(body) + 1
    while len(f"{length}{body}") != length:
        length += 1
    return f"{length}{body}".encode()


def _raw_tar(headers: list[tuple[bytes, bytes]], member: bytes = b"1") -> bytes:
    """Concatenate extended headers of (type, data), then one regular file 'a.usd'."""
    out = bytearray()
    for kind, data in headers:
        info = tarfile.TarInfo("././@PaxHeader")
        info.type = kind
        info.size = len(data)
        out += info.tobuf(tarfile.USTAR_FORMAT)
        out += data + b"\0" * (-len(data) % 512)
    info = tarfile.TarInfo("a.usd")
    info.size = len(member)
    out += info.tobuf(tarfile.USTAR_FORMAT) + member + b"\0" * (-len(member) % 512)
    out += b"\0" * 1024
    return bytes(out)


def _tar_entries(entries: list[tuple]) -> bytes:
    """Build a tar from ("header", type, data) and ("file", name, data) entries."""
    out = bytearray()
    for entry in entries:
        if entry[0] == "header":
            _, kind, data = entry
            info = tarfile.TarInfo("././@PaxHeader")
            info.type = kind
        else:
            _, name, data = entry
            info = tarfile.TarInfo(name)
        info.size = len(data)
        out += info.tobuf(tarfile.USTAR_FORMAT) + data + b"\0" * (-len(data) % 512)
    return bytes(out + b"\0" * 1024)


def test_f1_global_pax_headers_are_capped_per_archive(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    big = "x" * ((1 << 20) - 1000)  # each header just under the per-header cap
    entries = []
    for i in range(20):  # a global header then a file, so no chain grows long
        entries.append(("header", tarfile.XGLTYPE, _pax_record(f"k{i}", big)))
        entries.append(("file", f"f{i:03d}.usd", b"1"))
    archive = tmp_path / "globals.tar"
    archive.write_bytes(_tar_entries(entries))
    code, _, err = run(cli, capsys, "add", str(archive))
    assert code == 2 and "in total" in err


def test_f2_a_long_chain_of_extended_headers_is_a_clean_refusal(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    headers = [(tarfile.XHDTYPE, _pax_record("comment", "c")) for _ in range(400)]
    archive = tmp_path / "chain.tar"
    archive.write_bytes(_raw_tar(headers))
    code, _, err = run(cli, capsys, "add", str(archive))
    assert code == 2 and "in a row" in err and "Traceback" not in err


def test_f2_a_short_legitimate_header_chain_still_installs(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    headers = [
        (tarfile.XGLTYPE, _pax_record("comment", "global")),
        (tarfile.XHDTYPE, _pax_record("comment", "local")),
    ]
    archive = tmp_path / "short.tar"
    archive.write_bytes(_raw_tar(headers, member=b"ok"))
    assert run(cli, capsys, "add", str(archive))[0] == 0
    assert (store / "ur5e-dt" / "a.usd").read_bytes() == b"ok"


def test_m2_zip64_with_extensible_data_is_refused_before_parsing(cli, capsys, tmp_path, monkeypatch):
    import struct

    make_store(tmp_path, monkeypatch)
    data = _zip64_with_zeroed_legacy_fields(50)
    eocd = data.rfind(b"PK\x05\x06")
    loc = eocd - 20
    rec = loc - 56
    extensible = b"E" * 64
    record = bytearray(data[rec:loc])
    record[4:12] = struct.pack("<Q", 44 + len(extensible))  # record size includes extra data
    locator = bytearray(data[loc:eocd])
    archive = tmp_path / "ext.zip"
    archive.write_bytes(data[:rec] + bytes(record) + extensible + bytes(locator) + data[eocd:])

    def must_not_parse(*args, **kwargs):
        raise AssertionError("ZipFile must not be constructed")

    monkeypatch.setattr(cli.zipfile, "ZipFile", must_not_parse)
    code, _, err = run(cli, capsys, "add", str(archive), "--max-members", "3")
    assert code == 2 and "extensible data" in err


def test_m4_a_blocked_destination_is_found_before_anything_is_copied(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    pack.mkdir()
    (pack / "zz").write_bytes(b"a file where a folder is needed")
    source = tmp_path / "src"
    (source / "zz").mkdir(parents=True)
    (source / "aa.usd").write_bytes(b"a")
    (source / "zz" / "b.usd").write_bytes(b"b")
    code, _, err = run(cli, capsys, "add", str(source))
    assert code == 2 and "is not a folder" in err
    assert not (pack / "aa.usd").exists()  # nothing half-installed
    assert not (pack / "SHA256SUMS").exists()


def test_m4_a_non_utf8_member_name_is_refused_before_copying(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT, encoding="latin-1") as tf:
        for name in ("aa.usd", "b\xff.usd"):
            info = tarfile.TarInfo(name)
            info.size = 1
            tf.addfile(info, io.BytesIO(b"1"))
    archive = tmp_path / "latin.tar"
    archive.write_bytes(buf.getvalue())
    code, _, err = run(cli, capsys, "add", str(archive))
    assert code == 2 and "UTF-8" in err and "Traceback" not in err
    assert not (store / "ur5e-dt" / "aa.usd").exists()
    # The store is still usable afterwards.
    (tmp_path / "ok.usd").write_bytes(b"ok")
    assert run(cli, capsys, "add", str(tmp_path / "ok.usd"))[0] == 0


def test_m4_files_installed_before_a_failure_are_still_pinned(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.usd").write_bytes(b"a")
    (source / "b.usd").write_bytes(b"b")
    real_install = cli._install_file

    def fail_second(src, rel, pack_dir, force):
        if rel == "b.usd":
            raise OSError("disk full")
        return real_install(src, rel, pack_dir, force)

    monkeypatch.setattr(cli, "_install_file", fail_second)
    code, _, _ = run(cli, capsys, "add", str(source))
    assert code == 2
    pins = read_pins(store / "ur5e-dt")
    assert pins == {"a.usd": sha(b"a")}


def test_b1_force_does_not_half_install_when_a_folder_blocks_a_file(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    (pack / "z" / "sub").mkdir(parents=True)  # a folder where the file z goes
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.txt").write_bytes(b"a")
    (source / "z").write_bytes(b"z")
    code, _, err = run(cli, capsys, "add", str(source), "--force")
    assert code == 2 and "is not a file" in err and "Errno" not in err
    assert not (pack / "a.txt").exists()


# ------------------------------------------------------------------- migrate / adopt


def _git(repo, *args):
    import subprocess

    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _old_checkout(tmp_path):
    """A checkout that still has the old in-repo exts/ur5e-dt/assets folder."""
    checkout = tmp_path / "old-checkout"
    assets = checkout / "exts" / "ur5e-dt" / "assets"
    (assets / "gripper").mkdir(parents=True)
    (assets / "gripper" / "QuickChanger.usd").write_bytes(b"gripper")
    (assets / "fmb_assembly1.json").write_text('{"components": []}')
    return checkout, assets


def test_migrate_copies_old_in_repo_assets_and_pins_them(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    checkout, _ = _old_checkout(tmp_path)
    code, out, err = run(cli, capsys, "migrate", "--from", str(checkout), "--no-legacy-stores")
    assert code == 0, err
    pack = store / "ur5e-dt"
    assert (pack / "gripper" / "QuickChanger.usd").read_bytes() == b"gripper"
    assert read_pins(pack) == {
        "fmb_assembly1.json": sha(b'{"components": []}'),
        "gripper/QuickChanger.usd": sha(b"gripper"),
    }
    # Running it again changes nothing.
    code, out, _ = run(cli, capsys, "migrate", "--from", str(checkout), "--no-legacy-stores")
    assert code == 0 and "added 0, already present 2" in out


def test_migrate_reads_git_history_when_the_folder_is_gone(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    checkout, assets = _old_checkout(tmp_path)
    _git(checkout, "init", "-q")
    _git(checkout, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(checkout, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "with assets")
    _git(checkout, "rm", "-r", "-q", "exts/ur5e-dt/assets")
    _git(checkout, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "code only")
    assert not assets.exists()
    code, out, err = run(cli, capsys, "migrate", "--from", str(checkout), "--no-legacy-stores")
    assert code == 0, err
    assert (store / "ur5e-dt" / "gripper" / "QuickChanger.usd").read_bytes() == b"gripper"


def test_migrate_moves_an_old_cache_and_keeps_nvidia_files_unpinned(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    old = tmp_path / "home@host" / ".cache" / "ur5e-dt" / "assets"
    (old / "workstation").mkdir(parents=True)
    (old / "workstation" / "vention.usd").write_bytes(b"bench")
    monkeypatch.setattr(cli.asset_cache._TWIN, "legacy_stores", lambda: [old])
    code, _, err = run(cli, capsys, "migrate", "--from", str(tmp_path / "no-checkout"))
    assert code == 0, err
    pack = store / "ur5e-dt"
    assert (pack / "workstation" / "vention.usd").read_bytes() == b"bench"
    assert "workstation/vention.usd" in read_pins(pack)


def test_migrate_refuses_to_replace_a_different_file(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    checkout, _ = _old_checkout(tmp_path)
    mine = store / "ur5e-dt" / "gripper" / "QuickChanger.usd"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"my newer gripper")
    code, _, err = run(cli, capsys, "migrate", "--from", str(checkout), "--no-legacy-stores")
    assert code == 2 and "different files already" in err
    assert mine.read_bytes() == b"my newer gripper"
    assert not (store / "ur5e-dt" / "fmb_assembly1.json").exists()  # nothing half-done


def test_migrate_dry_run_changes_nothing(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    checkout, _ = _old_checkout(tmp_path)
    code, out, _ = run(cli, capsys, "migrate", "--from", str(checkout), "--no-legacy-stores", "--dry-run")
    assert code == 0 and "gripper/QuickChanger.usd" in out
    assert not (store / "ur5e-dt").exists()


def _project(tmp_path):
    repo = tmp_path / "cool-robot-project"
    (repo / "models" / "arm").mkdir(parents=True)
    (repo / "models" / "arm" / "arm.urdf").write_text("<robot/>")
    (repo / "models" / "arm" / "link.STL").write_bytes(b"mesh")
    (repo / "scenes").mkdir()
    (repo / "scenes" / "cell.usda").write_text("#usda 1.0\n")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print()")
    (repo / ".git").mkdir()
    (repo / ".git" / "ignored.usd").write_bytes(b"x")
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "x.png").write_bytes(b"x")
    return repo


def test_adopt_copies_only_asset_files_into_a_pack_named_after_the_project(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    repo = _project(tmp_path)
    code, out, err = run(cli, capsys, "adopt", str(repo))
    assert code == 0, err
    pack = store / "cool-robot-project"
    assert sorted(read_pins(pack)) == ["models/arm/arm.urdf", "models/arm/link.STL", "scenes/cell.usda"]
    assert not (pack / "src").exists() and not (pack / ".git").exists()
    assert_sha256sum_compatible(pack)


def test_adopt_dry_run_include_and_pack_name(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    repo = _project(tmp_path)
    code, out, _ = run(cli, capsys, "adopt", str(repo), "--dry-run", "--include", "py")
    assert code == 0 and "src/main.py" in out
    assert not store.joinpath("cool-robot-project").exists()
    code, _, err = run(cli, capsys, "adopt", str(repo), "--pack", "cell")
    assert code == 0, err
    assert (store / "cell" / "scenes" / "cell.usda").is_file()


def test_adopt_refuses_a_bad_pack_name(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    repo = _project(tmp_path)
    code, _, err = run(cli, capsys, "adopt", str(repo), "--pack", "../escape")
    assert code == 2


def test_new_folders_are_not_group_writable_even_with_umask_002(cli, capsys, tmp_path, monkeypatch):
    import os

    store = tmp_path / "fresh-store"
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    source = tmp_path / "a.usd"
    source.write_bytes(b"a")
    old = os.umask(0o002)
    try:
        assert run(cli, capsys, "add", str(source))[0] == 0
    finally:
        os.umask(old)
    for folder in (store, store / "ur5e-dt"):
        assert folder.stat().st_mode & 0o022 == 0, folder


def test_an_existing_group_writable_pack_folder_is_tightened(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    pack.mkdir()
    pack.chmod(0o775)
    source = tmp_path / "a.usd"
    source.write_bytes(b"a")
    code, out, _ = run(cli, capsys, "add", str(source))
    assert code == 0 and "removed group/world write permission" in out
    assert pack.stat().st_mode & 0o022 == 0


def test_migrate_keeps_a_pinned_lab_robot_over_nvidias_cached_copy(cli, capsys, tmp_path, monkeypatch):
    import json as _json

    store = make_store(tmp_path, monkeypatch)
    pack = store / "ur5e-dt"
    (pack / "robot").mkdir(parents=True)
    (pack / "robot" / "ur5e.usd").write_bytes(b"lab robot")
    (pack / "SHA256SUMS").write_text(f"{sha(b'lab robot')}  robot/ur5e.usd\n")
    old = tmp_path / "old-cache"
    (old / "robot").mkdir(parents=True)
    (old / "robot" / "ur5e.usd").write_bytes(b"nvidia robot")
    manifest = tmp_path / "assets-manifest.json"
    manifest.write_text(_json.dumps({"fetch": [{"id": "g", "files": [
        {"target": "robot/ur5e.usd", "source": "x", "sha256": sha(b"nvidia robot"), "size": 12}]}]}))
    monkeypatch.setattr(cli.asset_cache, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(cli.asset_cache._TWIN, "legacy_stores", lambda: [old])
    code, out, err = run(cli, capsys, "migrate", "--from", str(tmp_path / "no-checkout"))
    assert code == 0, err
    assert "keeping 1 file(s) you pinned yourself" in out
    assert (pack / "robot" / "ur5e.usd").read_bytes() == b"lab robot"


def _manifest_with_robot(cli, tmp_path, monkeypatch, nvidia=b"nvidia robot"):
    import json as _json

    manifest = tmp_path / "assets-manifest.json"
    manifest.write_text(_json.dumps({"fetch": [{"id": "g", "files": [
        {"target": "robot/ur5e.usd", "source": "x", "sha256": sha(nvidia), "size": len(nvidia)}]}]}))
    monkeypatch.setattr(cli.asset_cache, "MANIFEST_PATH", manifest)


def test_migrate_pins_a_lab_modified_robot_from_the_checkout(cli, capsys, tmp_path, monkeypatch):
    # Review finding 1: a robot at NVIDIA's path that is not NVIDIA's file is the user's copy.
    store = make_store(tmp_path, monkeypatch)
    _manifest_with_robot(cli, tmp_path, monkeypatch)
    checkout, assets = _old_checkout(tmp_path)
    (assets / "robot").mkdir()
    (assets / "robot" / "ur5e.usd").write_bytes(b"lab robot")
    code, out, err = run(cli, capsys, "migrate", "--from", str(checkout), "--no-legacy-stores")
    assert code == 0, err
    pack = store / "ur5e-dt"
    assert read_pins(pack)["robot/ur5e.usd"] == sha(b"lab robot")
    assert cli.asset_cache.resolve_info("robot/ur5e.usd")["status"] == "pinned"


def test_migrate_leaves_nvidias_exact_file_unpinned(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    _manifest_with_robot(cli, tmp_path, monkeypatch)
    checkout, assets = _old_checkout(tmp_path)
    (assets / "robot").mkdir()
    (assets / "robot" / "ur5e.usd").write_bytes(b"nvidia robot")
    code, _, err = run(cli, capsys, "migrate", "--from", str(checkout), "--no-legacy-stores")
    assert code == 0, err
    assert "robot/ur5e.usd" not in read_pins(store / "ur5e-dt")
    assert (store / "ur5e-dt" / "robot" / "ur5e.usd").read_bytes() == b"nvidia robot"


def test_migrate_keeps_a_pin_from_an_old_asset_folder(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    _manifest_with_robot(cli, tmp_path, monkeypatch, nvidia=b"lab robot")  # same bytes, still pinned
    old = tmp_path / "old-cache"
    (old / "ur5e-dt" / "robot").mkdir(parents=True)
    (old / "ur5e-dt" / "robot" / "ur5e.usd").write_bytes(b"lab robot")
    (old / "ur5e-dt" / "SHA256SUMS").write_text(f"{sha(b'lab robot')}  robot/ur5e.usd\n")
    monkeypatch.setattr(cli.asset_cache._TWIN, "legacy_stores", lambda: [old])
    code, _, err = run(cli, capsys, "migrate", "--from", str(tmp_path / "no-checkout"))
    assert code == 0, err
    assert read_pins(store / "ur5e-dt")["robot/ur5e.usd"] == sha(b"lab robot")


def test_migrate_skips_a_file_that_no_longer_matches_its_old_pin(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    old = tmp_path / "old-cache"
    (old / "workstation").mkdir(parents=True)
    (old / "workstation" / "vention.usd").write_bytes(b"damaged")
    (old / "workstation" / "ok.usd").write_bytes(b"fine")
    (old / "SHA256SUMS").write_text(f"{sha(b'bench')}  workstation/vention.usd\n")
    monkeypatch.setattr(cli.asset_cache._TWIN, "legacy_stores", lambda: [old])
    code, _, err = run(cli, capsys, "migrate", "--from", str(tmp_path / "no-checkout"))
    assert code == 1 and "does not match its pin" in err and "vention.usd" in err
    assert not (store / "ur5e-dt" / "workstation" / "vention.usd").exists()
    assert (store / "ur5e-dt" / "workstation" / "ok.usd").read_bytes() == b"fine"


def test_migrate_reads_history_even_when_untracked_copies_remain(cli, capsys, tmp_path, monkeypatch):
    # Review finding 2: after pulling the code-only repo, ignored copies stay in the folder.
    store = make_store(tmp_path, monkeypatch)
    checkout, assets = _old_checkout(tmp_path)
    (checkout / ".gitignore").write_text("exts/ur5e-dt/assets/workstation/\n")
    _git(checkout, "init", "-q")
    _git(checkout, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(checkout, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "with assets")
    _git(checkout, "rm", "-r", "-q", "exts/ur5e-dt/assets")
    _git(checkout, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "code only")
    (assets / "workstation").mkdir(parents=True)
    (assets / "workstation" / "vention.usd").write_bytes(b"bench")  # untracked
    (assets / "fmb_assembly1.json").write_text('{"components": ["edited"]}')  # untracked edit wins
    code, out, err = run(cli, capsys, "migrate", "--from", str(checkout), "--no-legacy-stores")
    assert code == 0, err
    pack = store / "ur5e-dt"
    assert (pack / "gripper" / "QuickChanger.usd").read_bytes() == b"gripper"
    assert (pack / "workstation" / "vention.usd").read_bytes() == b"bench"
    assert (pack / "fmb_assembly1.json").read_text() == '{"components": ["edited"]}'


def test_migrate_leaves_other_packs_in_an_old_folder_alone(cli, capsys, tmp_path, monkeypatch):
    # Review finding 4.
    store = make_store(tmp_path, monkeypatch)
    old = tmp_path / "old-cache"
    (old / "mylab").mkdir(parents=True)
    (old / "mylab" / "x.usd").write_bytes(b"x")
    (old / "mylab" / "SHA256SUMS").write_text(f"{sha(b'x')}  x.usd\n")
    (old / "workstation").mkdir()
    (old / "workstation" / "vention.usd").write_bytes(b"bench")
    monkeypatch.setattr(cli.asset_cache._TWIN, "legacy_stores", lambda: [old])
    code, out, err = run(cli, capsys, "migrate", "--from", str(tmp_path / "no-checkout"))
    assert code == 0, err
    assert "separate asset pack" in out and "adopt" in out
    assert not (store / "ur5e-dt" / "mylab").exists()
    assert (store / "ur5e-dt" / "workstation" / "vention.usd").is_file()


def test_the_store_folder_itself_keeps_its_permissions(cli, capsys, tmp_path, monkeypatch):
    # Review finding 5: only the pack folder is tightened.
    store = make_store(tmp_path, monkeypatch)
    store.chmod(0o775)
    source = tmp_path / "a.usd"
    source.write_bytes(b"a")
    assert run(cli, capsys, "add", str(source))[0] == 0
    assert store.stat().st_mode & 0o777 == 0o775
    assert (store / "ur5e-dt").stat().st_mode & 0o022 == 0


def test_migrate_skips_an_unpinned_old_nvidia_file_with_the_wrong_bytes(cli, capsys, tmp_path, monkeypatch):
    # Round 2 finding 1: the old resolver refused it, so it must not become "yours".
    store = make_store(tmp_path, monkeypatch)
    _manifest_with_robot(cli, tmp_path, monkeypatch)
    old = tmp_path / "old-cache"
    (old / "robot").mkdir(parents=True)
    (old / "robot" / "ur5e.usd").write_bytes(b"truncated")
    monkeypatch.setattr(cli.asset_cache._TWIN, "legacy_stores", lambda: [old])
    code, out, err = run(cli, capsys, "migrate", "--from", str(tmp_path / "no-checkout"))
    assert code == 1 and "never pinned" in err  # nothing else to migrate, still an error
    assert not (store / "ur5e-dt" / "robot" / "ur5e.usd").exists()
    code, _, err = run(cli, capsys, "migrate", "--from", str(tmp_path / "no-checkout"), "--dry-run")
    assert code == 1


def test_migrate_reports_a_git_failure_and_exits_nonzero(cli, capsys, tmp_path, monkeypatch):
    import subprocess

    store = make_store(tmp_path, monkeypatch)
    checkout, _ = _old_checkout(tmp_path)
    _git(checkout, "init", "-q")
    real = subprocess.run

    def broken(cmd, *a, **k):
        if cmd[:1] == ["git"] and "rev-list" in cmd:
            raise subprocess.CalledProcessError(128, cmd, output="", stderr="fatal: detected dubious ownership\n")
        return real(cmd, *a, **k)

    monkeypatch.setattr(cli.subprocess, "run", broken)
    code, out, err = run(cli, capsys, "migrate", "--from", str(checkout), "--no-legacy-stores")
    assert code == 1 and "dubious ownership" in err and "history could not be read" in err
    assert (store / "ur5e-dt" / "gripper" / "QuickChanger.usd").is_file()  # working tree still copied


def test_migrate_git_rev_needs_a_git_checkout(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    checkout, _ = _old_checkout(tmp_path)
    code, _, err = run(cli, capsys, "migrate", "--from", str(checkout), "--git-rev", "HEAD", "--no-legacy-stores")
    assert code == 2 and "needs a git checkout" in err


def test_migrate_moves_nvidias_sensor_from_its_old_name_to_its_canonical_place(cli, capsys, tmp_path, monkeypatch):
    import json as _json

    store = make_store(tmp_path, monkeypatch)
    manifest = tmp_path / "assets-manifest.json"
    manifest.write_text(_json.dumps({"fetch": [{"id": "g", "files": [
        {"target": "sensors/rsd455.usd", "source": "x", "sha256": sha(b"sensor"), "size": 6,
         "cache_path": "Isaac/Sensors/rsd455.usd"}]}]}))
    monkeypatch.setattr(cli.asset_cache, "MANIFEST_PATH", manifest)
    checkout, assets = _old_checkout(tmp_path)
    (assets / "sensors").mkdir()
    (assets / "sensors" / "rsd455.usd").write_bytes(b"sensor")
    code, _, err = run(cli, capsys, "migrate", "--from", str(checkout), "--no-legacy-stores")
    assert code == 0, err
    pack = store / "ur5e-dt"
    assert (pack / "Isaac" / "Sensors" / "rsd455.usd").read_bytes() == b"sensor"
    assert not (pack / "sensors").exists()
    assert "Isaac/Sensors/rsd455.usd" not in read_pins(pack)


# ---------------------------------------------- named packs


def test_add_goes_to_the_selected_pack(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    source = tmp_path / "a.usd"
    source.write_bytes(b"a")
    monkeypatch.setenv("ISAAC_MCP_ASSET_PACK", "bricksim")
    assert run(cli, capsys, "add", str(source))[0] == 0
    assert read_pins(store / "bricksim") == {"a.usd": sha(b"a")}
    assert run(cli, capsys, "pin")[0] == 0


@pytest.mark.parametrize("command", ["add", "pin", "adopt"])
def test_an_invalid_pack_name_is_refused(cli, capsys, tmp_path, monkeypatch, command):
    make_store(tmp_path, monkeypatch)
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.usd").write_bytes(b"a")
    args = {"add": ["add", str(source)], "pin": ["pin"], "adopt": ["adopt", str(source)]}[command]
    code, _, err = run(cli, capsys, *args, "--pack", "../escape")
    assert code == 2 and "Invalid pack name" in err
    assert not (tmp_path / "escape").exists()


def test_invalid_selected_pack_stops_the_cli(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    monkeypatch.setenv("ISAAC_MCP_ASSET_PACK", "a/b")
    code, _, err = run(cli, capsys, "where")
    assert code == 2 and "ISAAC_MCP_ASSET_PACK" in err


def test_where_names_the_pack_and_its_source(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    assert "pack:         ur5e-dt (default)" in run(cli, capsys, "where")[1]
    monkeypatch.setenv("ISAAC_MCP_ASSET_PACK", "bricksim")
    assert json.loads(run(cli, capsys, "where", "--json")[1])["pack_source"] == "env"


def test_packs_and_create(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    assert run(cli, capsys, "create", "fresh")[0] == 0
    assert (store / "fresh" / cli.asset_cache.PACK_FILE).is_file()
    assert run(cli, capsys, "create", "../bad")[0] == 2
    code, out, _ = run(cli, capsys, "packs", "--json")
    assert code == 0 and json.loads(out)["listing"][0]["packs"][0]["name"] == "fresh"
    assert "fresh" in run(cli, capsys, "packs")[1]


def test_unpin_command_keeps_the_file(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    source = tmp_path / "a.usd"
    source.write_bytes(b"a")
    run(cli, capsys, "add", str(source))
    code, out, _ = run(cli, capsys, "unpin", "a.usd")
    assert code == 0 and "fetch_ur5e_assets.py --force" in out
    assert read_pins(store / "ur5e-dt") == {} and (store / "ur5e-dt" / "a.usd").is_file()
    code, _, err = run(cli, capsys, "unpin", "a.usd")
    assert code == 2 and "not pinned" in err


def test_doctor_pack_pins_only_and_no_parser(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    source = tmp_path / "a.usd"
    source.write_bytes(b"a")
    run(cli, capsys, "add", str(source), "--pack", "p")
    monkeypatch.setattr(cli.packs, "usd_python", lambda: None)
    code, out, _ = run(cli, capsys, "doctor", "--pack", "p")
    assert code == 1 and "NOT CHECKED" in out and "ISAAC_MCP_USD_PYTHON" in out
    code, out, _ = run(cli, capsys, "doctor", "--pack", "p", "--pins-only")
    assert code == 0 and "OK for pins" in out
    (store / "p" / "a.usd").write_bytes(b"changed")
    code, out, _ = run(cli, capsys, "doctor", "--pack", "p", "--pins-only", "--json")
    assert code == 1 and json.loads(out)["folders"][0]["files"]["corrupt"][0]["file"] == "a.usd"
    assert run(cli, capsys, "doctor", "--pack", "absent")[0] == 2


def test_doctor_without_a_pack_is_unchanged(cli, capsys, tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)
    data = json.loads(run(cli, capsys, "doctor", "--json")[1])
    assert set(data) == {"project_file", "stores", "strict", "assets", "problems", "ok"}


def test_pack_build_and_doctor_on_real_usd(cli, capsys, tmp_path, monkeypatch):
    pytest.importorskip("pxr")
    import sys as _sys

    store = make_store(tmp_path, monkeypatch)
    source = store / "ur5e-dt"
    (source / "robot/SubUSDs").mkdir(parents=True)
    (source / "robot/ur5e.usda").write_text('#usda 1.0\ndef "R" (\n references = @./SubUSDs/base.usda@\n) {}\ndef "M" (\n references = @./SubUSDs/gone.usda@\n) {}\n')
    (source / "robot/SubUSDs/base.usda").write_text("#usda 1.0\n")
    args = ["robot/ur5e.usda", "--pack", "bricksim", "--from-pack", "ur5e-dt", "--usd-python", _sys.executable]
    code, _, err = run(cli, capsys, "pack-build", *args)
    assert code == 2 and "incomplete" in err and "gone.usda" in err and not (store / "bricksim").exists()
    code, out, _ = run(cli, capsys, "pack-build", *args, "--dry-run", "--allow-unresolved")
    assert code == 0 and "would copy" in out and "gone.usda" in out and not (store / "bricksim").exists()
    code, out, _ = run(cli, capsys, "pack-build", *args, "--allow-unresolved")
    assert code == 0 and "self-contained" in out
    code, out, _ = run(cli, capsys, "doctor", "--pack", "bricksim", "--usd-python", _sys.executable)
    assert code == 0 and "accepted unresolved: robot/SubUSDs/gone.usda" in out
    code, out, _ = run(cli, capsys, "doctor", "--pack", "ur5e-dt", "--entry", "robot/ur5e.usda", "--usd-python", _sys.executable)
    assert code == 1 and "UNRESOLVED: robot/SubUSDs/gone.usda" in out

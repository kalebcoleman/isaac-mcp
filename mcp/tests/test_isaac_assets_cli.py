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
    assert str(tmp_path / "home@host" / ".cache" / "ur5e-dt" / "assets") in out


def test_where_json_schema(cli, capsys, tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    code, out, _ = run(cli, capsys, "where", "--json")
    data = json.loads(out)
    assert code == 0
    assert set(data) == {"project_file", "stores", "strict", "deprecated_env"}
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
    assert code == 2 and "expands past" in err
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

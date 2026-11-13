"""The one pack model: asset-root resolution, the pack.yaml reader, listing, and agreement with the work cell's loader."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from isaac_mcp import pack_manifest as pm

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("workcell_asset_pack", REPO / "exts/ur5e-dt/ur5e_dt/asset_pack.py")
workcell = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workcell)

SETTINGS = ("ISAAC_MCP_ASSET_ROOT", "ISAAC_MCP_WORKSPACE", "ISAAC_MCP_PROJECT", *pm.REMOVED_ENVIRONMENT)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_pack(root: Path, pack_id="cell", version="v1", files=None, **top) -> Path:
    """A pack that verifies. *files* maps a relative path to bytes."""
    files = files or {"part.usda": b"#usda 1.0\n"}
    directory = root / pack_id / version
    records = []
    for rel, data in sorted(files.items()):
        path = directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        records.append(dict(path=rel, sha256=sha(path.read_bytes()), source="unit test", license="test fixture",
                            redistributable=False))
    records[0]["asset_id"] = "robot"
    (directory / "pack.yaml").write_text(json.dumps(dict(id=pack_id, version=version, **top, files=records)))
    return directory


@pytest.fixture
def clean(monkeypatch, tmp_path):
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(pm, "_announced", set())
    return tmp_path


# ---------------------------------------------------------------- asset root


def test_the_environment_wins(clean, monkeypatch):
    (clean / "ws" / "assets").mkdir(parents=True)
    monkeypatch.setenv("ISAAC_MCP_WORKSPACE", str(clean / "ws"))
    monkeypatch.setenv("ISAAC_MCP_ASSET_ROOT", str(clean / "elsewhere"))
    root = pm.resolve_asset_root()
    assert root.path == (clean / "elsewhere").resolve() and root.source == "environment" and not root.exists


def test_the_workspace_assets_folder_is_next(clean, monkeypatch):
    (clean / "ws" / "assets").mkdir(parents=True)
    (clean / "ws" / "isaac-mcp-assets").mkdir()
    monkeypatch.setenv("ISAAC_MCP_WORKSPACE", str(clean / "ws"))
    root = pm.resolve_asset_root()
    assert root.path == (clean / "ws" / "assets").resolve() and root.source == "workspace" and root.warnings == ()


def test_the_transitional_name_works_and_says_so(clean, monkeypatch):
    (clean / "ws" / "isaac-mcp-assets").mkdir(parents=True)
    monkeypatch.setenv("ISAAC_MCP_WORKSPACE", str(clean / "ws"))
    root = pm.resolve_asset_root()
    assert root.source == "transitional" and any("transitional" in w for w in root.warnings)


def test_the_workspace_defaults_to_the_folder_holding_the_checkout(clean, monkeypatch):
    monkeypatch.setattr(pm, "checkout_root", lambda: clean / "ws" / "twinsmith")
    (clean / "ws" / "assets").mkdir(parents=True)
    assert pm.resolve_asset_root().path == (clean / "ws" / "assets").resolve()


def test_nothing_found_names_the_setting_and_never_defaults_into_a_home_folder(clean, monkeypatch):
    (clean / "home" / "isaac-workspace" / "isaac-mcp-assets").mkdir(parents=True)
    monkeypatch.setenv("ISAAC_MCP_WORKSPACE", str(clean / "empty-ws"))
    with pytest.raises(pm.RootError, match="ISAAC_MCP_ASSET_ROOT") as caught:
        pm.resolve_asset_root()
    assert caught.value.code == "NO_ASSET_ROOT"
    monkeypatch.delenv("ISAAC_MCP_WORKSPACE")
    monkeypatch.setattr(pm, "checkout_root", lambda: None)
    with pytest.raises(pm.RootError, match="ISAAC_MCP_ASSET_ROOT"):
        pm.resolve_asset_root()


def test_a_relative_setting_is_refused(clean, monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_ASSET_ROOT", "relative/assets")
    with pytest.raises(pm.RootError, match="absolute"):
        pm.resolve_asset_root()


def test_the_module_has_no_literal_home_default():
    source = (REPO / "mcp/isaac_mcp/pack_manifest.py").read_text()
    assert "isaac-workspace" not in source and "expanduser(\"~" not in source


# ---------------------------------------------------------------- removed settings


@pytest.mark.parametrize("name", sorted(pm.REMOVED_ENVIRONMENT))
def test_a_removed_variable_is_announced_once_not_ignored_silently(clean, monkeypatch, caplog, name):
    monkeypatch.setenv(name, "1")
    monkeypatch.setenv("ISAAC_MCP_ASSET_ROOT", str(clean / "root"))
    with caplog.at_level("WARNING"):
        first = pm.resolve_asset_root()
        pm.resolve_asset_root()
    assert any(name in warning and "no longer read" in warning for warning in first.warnings)
    assert len([r for r in caplog.records if name in r.getMessage()]) == 1  # logged once per process
    assert pm.REMOVED_ENVIRONMENT[name] in first.warnings[0]


def test_the_replacement_variable_is_named(clean, monkeypatch):
    for name in ("ISAAC_MCP_ASSET_PATH", "UR5E_DT_ASSET_CACHE", "ISAAC_MCP_ASSET_STRICT", "UR5E_DT_OFFLINE",
                 "ISAAC_MCP_ASSET_REMOTE_ROOT", "UR5E_DT_ASSET_ROOT"):
        assert "ISAAC_MCP_ASSET_ROOT" in pm.REMOVED_ENVIRONMENT[name]
    monkeypatch.setenv("UR5E_DT_ASSET_CACHE", "/x")
    assert pm.removed_environment() and pm.removed_environment({}) == []


# ---------------------------------------------------------------- project file


def project(clean, monkeypatch, data):
    folder = clean / "proj"
    folder.mkdir(exist_ok=True)
    (folder / "isaac-mcp.json").write_text(data if isinstance(data, str) else json.dumps(data))
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(folder))


def test_the_project_file_selects_a_pack_as_id_and_version(clean, monkeypatch):
    project(clean, monkeypatch, {"pack": "bricksim/v2"})
    assert pm.project_pack() == "bricksim/v2" and pm.project_file() == (clean / "proj" / "isaac-mcp.json").resolve()
    project(clean, monkeypatch, {})
    assert pm.project_pack() is None


@pytest.mark.parametrize("value", ["bricksim", "a/b/c", "../x/v1", "a/..", "", 5, None, ["a", "b"]])
def test_an_invalid_project_pack_is_an_error(clean, monkeypatch, value):
    project(clean, monkeypatch, {"pack": value})
    with pytest.raises(ValueError):
        pm.project_pack()


@pytest.mark.parametrize("key", ["asset_path", "strict"])
def test_the_removed_project_keys_are_errors_not_ignored(clean, monkeypatch, key):
    project(clean, monkeypatch, {key: ["x"] if key == "asset_path" else True})
    with pytest.raises(ValueError, match="not supported any more"):
        pm.project_pack()


def test_a_malformed_project_file_is_an_error(clean, monkeypatch):
    project(clean, monkeypatch, "{not json")
    with pytest.raises(ValueError, match="Cannot read project file"):
        pm.project_pack()
    project(clean, monkeypatch, "[1]")
    with pytest.raises(ValueError, match="JSON object"):
        pm.project_pack()


# ---------------------------------------------------------------- the reader


def test_a_pack_that_verifies_resolves_logical_ids(clean):
    directory = make_pack(clean)
    pack = pm.AssetPack(clean, "cell", "v1")
    assert pack.asset("robot") == directory / "part.usda"
    with pytest.raises(pm.ManifestError, match="no asset id"):
        pack.asset("absent")


def test_a_pack_can_be_written_as_json_compatible_yaml_or_yaml(clean):
    directory = make_pack(clean)
    manifest = (directory / "pack.yaml").read_text()
    assert json.loads(manifest)["id"] == "cell"
    yaml = pytest.importorskip("yaml")
    (directory / "pack.yaml").write_text(yaml.safe_dump(json.loads(manifest)))
    assert pm.AssetPack(clean, "cell", "v1").manifest["id"] == "cell"


def mutate(directory: Path, how: str):
    manifest = json.loads((directory / "pack.yaml").read_text())
    record = manifest["files"][0]
    if how == "tamper":
        (directory / "part.usda").write_text("changed")
    elif how == "unlisted":
        (directory / "unused.txt").write_text("x")
    elif how == "missing":
        (directory / "part.usda").unlink()
    elif how == "escape":
        record["path"] = "../outside.usda"
    elif how == "absolute":
        record["path"] = "/etc/passwd"
    elif how == "duplicate":
        manifest["files"].append(dict(record))
    elif how == "duplicate_asset_id":
        (directory / "b.usda").write_text("b")
        manifest["files"].append(dict(record, path="b.usda", sha256=sha(b"b")))
    elif how == "no_license":
        record.pop("license")
    elif how == "blank_source":
        record["source"] = "  "
    elif how == "string_redistributable":
        record["redistributable"] = "false"
    elif how == "bad_asset_id":
        record["asset_id"] = "../x"
    elif how == "wrong_id":
        manifest["id"] = "other"
    elif how == "wrong_version":
        manifest["version"] = "v9"
    elif how == "no_files":
        manifest["files"] = []
    elif how == "files_not_list":
        manifest["files"] = {"a": 1}
    elif how == "list_manifest":
        (directory / "pack.yaml").write_text("[]")
        return
    elif how == "record_not_dict":
        manifest["files"] = ["part.usda"]
    elif how == "symlinked_manifest":
        real = directory / "real.json"
        real.write_text((directory / "pack.yaml").read_text())
        (directory / "pack.yaml").unlink()
        (directory / "pack.yaml").symlink_to(real)
        return
    elif how == "no_manifest":
        (directory / "pack.yaml").unlink()
        return
    (directory / "pack.yaml").write_text(json.dumps(manifest))


FAILURES = ["tamper", "unlisted", "missing", "escape", "absolute", "duplicate", "duplicate_asset_id", "no_license",
            "blank_source", "string_redistributable", "bad_asset_id", "wrong_id", "wrong_version", "no_files",
            "files_not_list", "list_manifest", "record_not_dict", "symlinked_manifest", "no_manifest"]


@pytest.mark.parametrize("how", FAILURES)
def test_the_host_reader_and_the_work_cell_loader_agree(clean, how):
    """The host reader implements the contract of exts/ur5e-dt/ur5e_dt/asset_pack.py: same verdict, same message."""
    directory = make_pack(clean)
    mutate(directory, how)
    with pytest.raises(workcell.PackError) as expected:
        workcell.AssetPack(clean, "cell", "v1")
    with pytest.raises(pm.ManifestError) as actual:
        pm.AssetPack(clean, "cell", "v1")
    assert str(actual.value) == str(expected.value)
    assert pm.inspect_pack(clean, "cell", "v1")["verified"] is False


def test_both_accept_the_same_good_pack_and_the_same_names(clean):
    make_pack(clean, files={"a/b.usda": b"b", "c.usda": b"c"})
    ours, theirs = pm.AssetPack(clean, "cell", "v1"), workcell.AssetPack(clean, "cell", "v1")
    assert ours.directory == theirs.directory and ours.manifest == theirs.manifest and ours._files == theirs._files
    for bad in ("../x", "a/b", "", ".hidden", None):
        with pytest.raises(workcell.PackError) as expected:
            workcell.AssetPack(clean, "cell", bad)
        with pytest.raises(pm.ManifestError) as actual:
            pm.AssetPack(clean, "cell", bad)
        assert str(actual.value) == str(expected.value)


def test_a_pack_directory_cannot_leave_the_root(clean):
    (clean / "elsewhere").mkdir()
    (clean / "root").mkdir()
    (clean / "root" / "cell").symlink_to(clean / "elsewhere")
    (clean / "elsewhere" / "v1").mkdir()
    with pytest.raises(pm.ManifestError, match="escapes"):
        pm.AssetPack(clean / "root", "cell", "v1")
    with pytest.raises(workcell.PackError, match="escapes"):
        workcell.AssetPack(clean / "root", "cell", "v1")


# ---------------------------------------------------------------- inspect and list


def test_inspect_reports_every_problem_not_just_the_first(clean):
    directory = make_pack(clean, files={"a.usda": b"a", "b.usda": b"b"})
    (directory / "a.usda").write_text("changed")
    (directory / "b.usda").unlink()
    (directory / "extra.txt").write_text("x")
    (directory / "link.usda").symlink_to(directory / "extra.txt")
    report = pm.inspect_pack(clean, "cell", "v1")
    text = "\n".join(report["problems"])
    assert report["verified"] is False and "Checksum mismatch: a.usda" in text and "Missing pack file: b.usda" in text
    assert "extra.txt" in text and "Symlink in pack" in text and "link.usda" in text


def test_inspect_of_a_good_pack_has_counts_and_no_problems(clean):
    make_pack(clean, files={"a.usda": b"aa", "b.usda": b"b"})
    report = pm.inspect_pack(clean, "cell", "v1")
    assert report["verified"] is True and report["problems"] == [] and report["files"] == 2 and report["bytes"] == 3


def test_a_symlink_to_a_file_outside_the_pack_never_verifies(clean):
    directory = make_pack(clean)
    outside = clean / "secret.txt"
    outside.write_text("s")
    manifest = json.loads((directory / "pack.yaml").read_text())
    (directory / "leak.txt").symlink_to(outside)
    manifest["files"].append(dict(manifest["files"][0], path="leak.txt", sha256=sha(b"s"), asset_id="leak"))
    (directory / "pack.yaml").write_text(json.dumps(manifest))
    assert pm.inspect_pack(clean, "cell", "v1")["verified"] is False
    with pytest.raises(pm.ManifestError, match="escapes"):
        pm.AssetPack(clean, "cell", "v1")


def test_list_shows_every_version_with_count_size_and_verdict(clean):
    make_pack(clean, "a", "v1", {"x.usda": b"xx"})
    make_pack(clean, "a", "v2", {"x.usda": b"xx", "y.usda": b"y"})
    broken = make_pack(clean, "b", "v1")
    (broken / "part.usda").write_text("changed")
    listing = pm.list_packs(clean)
    by_name = {f"{p['id']}/{p['version']}": p for p in listing["packs"]}
    assert sorted(by_name) == ["a/v1", "a/v2", "b/v1"]
    assert by_name["a/v2"]["files"] == 2 and by_name["a/v2"]["bytes"] == 3 and by_name["a/v2"]["verified"] is True
    assert by_name["b/v1"]["verified"] is False and "Checksum mismatch" in by_name["b/v1"]["problems"][0]
    assert listing["old_format"] == []


def test_list_reports_an_old_format_pack_with_a_hint_and_nothing_reads_it(clean):
    old = clean / "ur5e-dt"
    (old / "robot").mkdir(parents=True)
    (old / "robot" / "ur5e.usd").write_text("x")
    (old / "SHA256SUMS").write_text(f"{sha(b'x')}  robot/ur5e.usd\n")
    pins_only = clean / "pins-only"
    pins_only.mkdir()
    (pins_only / "SHA256SUMS").write_text("")
    (clean / "empty").mkdir()
    (clean / "empty" / "sub").mkdir()
    listing = pm.list_packs(clean)
    assert [o["id"] for o in listing["old_format"]] == ["pins-only", "ur5e-dt"] and listing["packs"] == []
    assert all("adopt" in o["hint"] and o["format"] == "old" and "\n" not in o["hint"] for o in listing["old_format"])
    with pytest.raises(pm.ManifestError, match="Missing pack"):
        pm.AssetPack(clean, "ur5e-dt", "v1")


def test_a_converted_pack_is_listed_once_and_its_old_files_are_not_noise(clean):
    old = clean / "ur5e-dt"
    (old / "robot").mkdir(parents=True)
    (old / "robot" / "ur5e.usd").write_text("x")
    make_pack(clean, "ur5e-dt", "v1")
    listing = pm.list_packs(clean)
    assert [f"{p['id']}/{p['version']}" for p in listing["packs"]] == ["ur5e-dt/v1"] and listing["old_format"] == []


def test_a_new_version_folder_without_a_manifest_is_reported(clean):
    make_pack(clean, "a", "v1")
    (clean / "a" / "v2").mkdir()
    (clean / "a" / "v2" / "x.usda").write_text("x")
    listing = pm.list_packs(clean)
    broken = next(p for p in listing["packs"] if p["version"] == "v2")
    assert broken["verified"] is False and broken["problems"] == ["Missing pack.yaml"]


def test_list_of_a_root_that_does_not_exist(clean):
    assert pm.list_packs(clean / "nope") == dict(root=str((clean / "nope").resolve()), exists=False, packs=[], old_format=[])


# ---------------------------------------------------------------- one verified file


def test_resolve_pack_file_returns_a_listed_verified_file(clean):
    directory = make_pack(clean, files={"robot/arm.usda": b"arm", "other.usda": b"o"})
    assert pm.resolve_pack_file(clean, "cell/v1/robot/arm.usda") == directory / "robot" / "arm.usda"


@pytest.mark.parametrize("ref", ["cell", "cell/v1", "cell/v1/", "../v1/x", "cell/../x/y", "bad name/v1/x"])
def test_resolve_pack_file_refuses_a_malformed_name(clean, ref):
    make_pack(clean)
    with pytest.raises(pm.ManifestError):
        pm.resolve_pack_file(clean, ref)


def test_resolve_pack_file_refuses_an_unlisted_changed_or_symlinked_file(clean):
    directory = make_pack(clean, files={"a.usda": b"a", "b.usda": b"b"})
    with pytest.raises(pm.ManifestError, match="not listed"):
        pm.resolve_pack_file(clean, "cell/v1/pack.yaml")
    (directory / "a.usda").write_text("changed")
    with pytest.raises(pm.ManifestError, match="Checksum mismatch: a.usda"):
        pm.resolve_pack_file(clean, "cell/v1/a.usda")
    assert pm.resolve_pack_file(clean, "cell/v1/b.usda")  # only the file asked for is hashed
    (directory / "b.usda").unlink()
    (directory / "b.usda").symlink_to(directory / "a.usda")
    with pytest.raises(pm.ManifestError):
        pm.resolve_pack_file(clean, "cell/v1/b.usda")


# ---------------------------------------------------------------- stdlib only


def test_the_reader_is_standard_library_only():
    for name in ("pack_manifest.py", "asset_packs.py", "usd_closure.py"):
        tree = ast.parse((REPO / "mcp" / "isaac_mcp" / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module]
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                assert root in sys.stdlib_module_names or root in ("pxr", "yaml"), f"{name} imports {module}"


def test_importing_the_reader_pulls_in_no_server_or_sdk():
    code = ("import sys; from isaac_mcp import pack_manifest; "
            "assert 'mcp' not in sys.modules and 'isaac_mcp.server' not in sys.modules and 'pxr' not in sys.modules")
    env = {**os.environ, "PYTHONPATH": str(REPO / "mcp")}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr

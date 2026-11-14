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

from twinsmith import pack_manifest as pm

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("workcell_asset_pack", REPO / "exts/workcell/workcell/asset_pack.py")
workcell = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workcell)

SETTINGS = ("TWINSMITH_ASSET_ROOT", "TWINSMITH_WORKSPACE", "TWINSMITH_PROJECT")


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
    return tmp_path


# ---------------------------------------------------------------- asset root


def test_the_environment_wins(clean, monkeypatch):
    (clean / "ws" / "assets").mkdir(parents=True)
    monkeypatch.setenv("TWINSMITH_WORKSPACE", str(clean / "ws"))
    monkeypatch.setenv("TWINSMITH_ASSET_ROOT", str(clean / "elsewhere"))
    root = pm.resolve_asset_root()
    assert root.path == (clean / "elsewhere").resolve() and root.source == "environment" and not root.exists


def test_the_workspace_assets_folder_is_next(clean, monkeypatch):
    (clean / "ws" / "assets").mkdir(parents=True)
    monkeypatch.setenv("TWINSMITH_WORKSPACE", str(clean / "ws"))
    root = pm.resolve_asset_root()
    assert root.path == (clean / "ws" / "assets").resolve() and root.source == "workspace" and root.warnings == ()


def test_the_workspace_defaults_to_the_folder_holding_the_checkout(clean, monkeypatch):
    monkeypatch.setattr(pm, "checkout_root", lambda: clean / "ws" / "twinsmith")
    (clean / "ws" / "assets").mkdir(parents=True)
    assert pm.resolve_asset_root().path == (clean / "ws" / "assets").resolve()


def test_nothing_found_names_the_setting(clean, monkeypatch):
    (clean / "home" / "robotics-workspace" / "assets").mkdir(parents=True)
    monkeypatch.setenv("TWINSMITH_WORKSPACE", str(clean / "empty-ws"))   # the variable wins: the home folder is not tried
    with pytest.raises(pm.RootError, match="TWINSMITH_ASSET_ROOT") as caught:
        pm.resolve_asset_root()
    assert caught.value.code == "NO_ASSET_ROOT"
    monkeypatch.delenv("TWINSMITH_WORKSPACE")
    monkeypatch.setattr(pm, "checkout_root", lambda: None)
    monkeypatch.setenv("HOME", str(clean / "bare-home"))
    with pytest.raises(pm.RootError, match="TWINSMITH_ASSET_ROOT"):
        pm.resolve_asset_root()


# ---------------------------------------------------------------- the workspace folder


def test_the_workspace_variable_wins_over_the_checkout_and_the_home_folder(clean, monkeypatch):
    (clean / "home" / "robotics-workspace").mkdir(parents=True)
    monkeypatch.setattr(pm, "checkout_root", lambda: clean / "ws" / "twinsmith")
    monkeypatch.setenv("TWINSMITH_WORKSPACE", str(clean / "chosen"))
    assert pm.workspace_dir() == (clean / "chosen").resolve()


def test_the_checkout_parent_wins_over_the_home_folders(clean, monkeypatch):
    (clean / "home" / "robotics-workspace").mkdir(parents=True)
    (clean / "ws" / "envs").mkdir(parents=True)
    monkeypatch.setattr(pm, "checkout_root", lambda: clean / "ws" / "twinsmith")
    assert pm.workspace_dir() == clean / "ws"


def test_a_checkout_parent_that_is_not_a_workspace_is_not_used(clean, monkeypatch):
    # a git worktree of the checkout: its parent folder has no envs/ and no asset folder
    (clean / "home" / "robotics-workspace").mkdir(parents=True)
    (clean / "worktrees").mkdir()
    monkeypatch.setattr(pm, "checkout_root", lambda: clean / "worktrees" / "twinsmith-branch")
    assert pm.workspace_dir() == clean / "home" / "robotics-workspace"


def test_without_a_checkout_the_home_folder_is_used(clean, monkeypatch):
    monkeypatch.setattr(pm, "checkout_root", lambda: None)
    assert pm.workspace_dir() == clean / "home" / "robotics-workspace"      # even before it exists


def test_the_new_home_folder_and_assets_are_found_without_any_setting(clean, monkeypatch):
    monkeypatch.setattr(pm, "checkout_root", lambda: None)
    (clean / "home" / "robotics-workspace" / "assets").mkdir(parents=True)
    root = pm.resolve_asset_root()
    assert root.path == clean / "home" / "robotics-workspace" / "assets" and root.source == "workspace"


def test_a_relative_setting_is_refused(clean, monkeypatch):
    monkeypatch.setenv("TWINSMITH_ASSET_ROOT", "relative/assets")
    with pytest.raises(pm.RootError, match="absolute"):
        pm.resolve_asset_root()


def test_the_module_has_no_literal_home_path():
    source = (REPO / "mcp/twinsmith/pack_manifest.py").read_text()
    assert "~/" not in source and "expanduser(\"~" not in source


# ---------------------------------------------------------------- project file


def project(clean, monkeypatch, data):
    folder = clean / "proj"
    folder.mkdir(exist_ok=True)
    (folder / "twinsmith.json").write_text(data if isinstance(data, str) else json.dumps(data))
    monkeypatch.setenv("TWINSMITH_PROJECT", str(folder))


def test_the_project_file_selects_a_pack_as_id_and_version(clean, monkeypatch):
    project(clean, monkeypatch, {"pack": "bricksim/v2"})
    assert pm.project_pack() == "bricksim/v2" and pm.project_file() == (clean / "proj" / "twinsmith.json").resolve()
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
    """The host reader implements the contract of exts/workcell/workcell/asset_pack.py: same verdict, same message."""
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
    old = clean / "workcell"
    (old / "robot").mkdir(parents=True)
    (old / "robot" / "ur5e.usd").write_text("x")
    (old / "SHA256SUMS").write_text(f"{sha(b'x')}  robot/ur5e.usd\n")
    pins_only = clean / "pins-only"
    pins_only.mkdir()
    (pins_only / "SHA256SUMS").write_text("")
    (clean / "empty").mkdir()
    (clean / "empty" / "sub").mkdir()
    listing = pm.list_packs(clean)
    assert [o["id"] for o in listing["old_format"]] == ["pins-only", "workcell"] and listing["packs"] == []
    assert all("adopt" in o["hint"] and o["format"] == "old" and "\n" not in o["hint"] for o in listing["old_format"])
    with pytest.raises(pm.ManifestError, match="Missing pack"):
        pm.AssetPack(clean, "workcell", "v1")


def test_a_converted_pack_is_listed_once_and_its_old_files_are_not_noise(clean):
    old = clean / "workcell"
    (old / "robot").mkdir(parents=True)
    (old / "robot" / "ur5e.usd").write_text("x")
    make_pack(clean, "workcell", "v1")
    listing = pm.list_packs(clean)
    assert [f"{p['id']}/{p['version']}" for p in listing["packs"]] == ["workcell/v1"] and listing["old_format"] == []


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
        tree = ast.parse((REPO / "mcp" / "twinsmith" / name).read_text())
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
    code = ("import sys; from twinsmith import pack_manifest; "
            "assert 'mcp' not in sys.modules and 'twinsmith.server' not in sys.modules and 'pxr' not in sys.modules")
    env = {**os.environ, "PYTHONPATH": str(REPO / "mcp")}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr


# ---------------------------------------------------------------- the work cell's copy of the rule (it runs in Kit)


def load_workcell_copy(where: Path):
    """The loader as it would sit outside a checkout, so its own checkout test finds none."""
    target = where / "a" / "b" / "c" / "asset_pack.py"
    target.parent.mkdir(parents=True)
    target.write_text((REPO / "exts/workcell/workcell/asset_pack.py").read_text())
    spec = importlib.util.spec_from_file_location("workcell_copy_" + where.name, target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_work_cell_default_root_follows_the_host_order(clean):
    copy = load_workcell_copy(clean / "copy")
    home = clean / "home"
    environ = {}
    assert copy.default_root(environ, home) == home / "robotics-workspace" / "assets"          # nothing exists
    (home / "robotics-workspace" / "assets").mkdir(parents=True)
    assert copy.default_root(environ, home) == home / "robotics-workspace" / "assets"


def test_the_work_cell_environment_wins_over_everything(clean):
    copy = load_workcell_copy(clean / "copy")
    home = clean / "home"
    (home / "robotics-workspace" / "assets").mkdir(parents=True)
    (clean / "ws" / "assets").mkdir(parents=True)
    assert copy.default_root({"TWINSMITH_WORKSPACE": str(clean / "ws")}, home) == (clean / "ws").resolve() / "assets"
    both = {"TWINSMITH_WORKSPACE": str(clean / "ws"), "TWINSMITH_ASSET_ROOT": str(clean / "x")}
    assert copy.default_root(both, home) == clean / "x"


def test_the_work_cell_and_the_host_agree_on_the_default_root(clean, monkeypatch):
    copy = load_workcell_copy(clean / "copy")
    monkeypatch.setattr(pm, "checkout_root", lambda: None)
    home = clean / "home"
    for layout in (["robotics-workspace/assets"], ["robotics-workspace/assets", "robotics-workspace/envs"]):
        for folder in layout:
            (home / folder).mkdir(parents=True)
        assert copy.default_root({}, home) == pm.resolve_asset_root().path
        import shutil
        shutil.rmtree(home)

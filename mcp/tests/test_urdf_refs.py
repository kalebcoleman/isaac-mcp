"""What a URDF names: resolved only inside its folder, with pack builds, doctor and the host route."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from isaac_mcp import asset_packs as packs
from isaac_mcp import assets, urdf_physics, urdf_refs
from isaac_mcp.project_assets import AssetError, Broker

ENV = ("ISAAC_MCP_ASSET_PATH", "ISAAC_MCP_ASSET_PACK", "ISAAC_MCP_ASSET_STRICT", "ISAAC_MCP_PROJECT",
       "ISAAC_MCP_WORKSPACE", "UR5E_DT_ASSET_CACHE", "ISAAC_MCP_USD_PYTHON")


def put(root: Path, rel: str, data="x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def urdf(*meshes, extra="") -> str:
    visuals = "".join(f'<visual><geometry><mesh filename="{m}"/></geometry></visual>' for m in meshes)
    return f'<robot name="r"><link name="a">{visuals}</link>{extra}</robot>'


def package_xml(name: str) -> str:
    return f"<package><name>{name}</name></package>"


def resolve(root: Path, entry="urdf/r.urdf"):
    project = urdf_refs.project_from_folder(root)
    return urdf_refs.resolve(entry, urdf_refs.parse((root / entry).read_bytes()), project)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "home" / ".cache"))
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(tmp_path / "no-project"))
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(store))
    return store


@pytest.fixture
def broker(tmp_path, env):
    root = tmp_path / "project"
    root.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    config = tmp_path / "approval.json"
    config.write_text(json.dumps(dict(version=1, profile="trusted_project", roots={"p": str(root)}, staging=str(staging))))
    made = Broker(str(config))
    assert made.config_error is None
    return made


# ---------------------------------------------------------------- finding packages


def test_a_package_xml_names_a_package_in_any_folder(tmp_path):
    put(tmp_path, "src/deep/thing/package.xml", package_xml("my_robot"))
    put(tmp_path, "src/deep/thing/meshes/a.stl")
    put(tmp_path, "urdf/r.urdf", urdf("package://my_robot/meshes/a.stl"))
    found = resolve(tmp_path)
    assert found["problems"] == []
    assert found["mapped"] == {"package://my_robot/meshes/a.stl": "src/deep/thing/meshes/a.stl"}
    assert found["packages"] == {"my_robot": dict(folder="src/deep/thing", found_by="package.xml")}
    assert found["package_files"] == ["src/deep/thing/package.xml"]


def test_a_folder_named_for_the_package_is_enough(tmp_path):
    put(tmp_path, "my_robot/meshes/a.stl")
    put(tmp_path, "urdf/r.urdf", urdf("package://my_robot/meshes/a.stl"))
    found = resolve(tmp_path)
    assert found["mapped"]["package://my_robot/meshes/a.stl"] == "my_robot/meshes/a.stl"
    assert found["packages"]["my_robot"]["found_by"] == "folder name"


def test_the_root_itself_by_package_xml_and_by_its_own_name(tmp_path):
    named = tmp_path / "x"
    put(named, "package.xml", package_xml("pkg"))
    put(named, "meshes/a.stl")
    put(named, "urdf/r.urdf", urdf("package://pkg/meshes/a.stl"))
    assert resolve(named)["packages"]["pkg"] == dict(folder=".", found_by="package.xml")
    by_name = tmp_path / "example-robot-data"
    put(by_name, "robots/a.stl")
    put(by_name, "urdf/r.urdf", urdf("package://example-robot-data/robots/a.stl"))
    assert resolve(by_name)["packages"]["example-robot-data"] == dict(folder=".", found_by="root folder name")


def test_package_xml_outranks_a_folder_of_that_name(tmp_path):
    put(tmp_path, "real/package.xml", package_xml("pkg"))
    put(tmp_path, "real/m/a.stl", "real")
    put(tmp_path, "pkg/m/a.stl", "folder")
    put(tmp_path, "urdf/r.urdf", urdf("package://pkg/m/a.stl"))
    assert resolve(tmp_path)["mapped"]["package://pkg/m/a.stl"] == "real/m/a.stl"


def test_two_equal_candidates_are_refused_unless_one_holds_the_urdf(tmp_path):
    for folder in ("a", "b"):
        put(tmp_path, f"{folder}/package.xml", package_xml("pkg"))
        put(tmp_path, f"{folder}/m.stl")
    put(tmp_path, "urdf/r.urdf", urdf("package://pkg/m.stl"))
    assert resolve(tmp_path)["problems"][0]["code"] == "PACKAGE_AMBIGUOUS"
    put(tmp_path, "b/urdf/r.urdf", urdf("package://pkg/m.stl"))
    assert resolve(tmp_path, "b/urdf/r.urdf")["mapped"]["package://pkg/m.stl"] == "b/m.stl"


def test_unresolved_package_says_nothing_outside_is_searched(tmp_path):
    root = tmp_path / "root"
    put(root, "urdf/r.urdf", urdf("package://nowhere/m.stl"))
    put(tmp_path, "nowhere/m.stl")  # next door, and out of bounds
    problem = resolve(root)["problems"][0]
    assert problem["code"] == "PACKAGE_UNRESOLVED" and "nothing outside" in problem["reason"]


def test_a_package_that_lacks_the_file_names_what_is_missing(tmp_path):
    put(tmp_path, "pkg/package.xml", package_xml("pkg"))
    put(tmp_path, "urdf/r.urdf", urdf("package://pkg/gone.stl"))
    problem = resolve(tmp_path)["problems"][0]
    assert problem["code"] == "MESH_MISSING" and "pkg/gone.stl" in problem["reason"]


@pytest.mark.parametrize("value", [
    "package://../up/m.stl", "package://./m.stl", "package://.../m.stl", "package://a%2fb/m.stl",
    "package:///m.stl", "package://a b/m.stl", "package://pk$g/m.stl", "package://-x/m.stl",
])
def test_odd_package_names_are_refused(tmp_path, value):
    put(tmp_path, "urdf/r.urdf", urdf(value))
    assert resolve(tmp_path)["problems"][0]["code"] == "PACKAGE_NAME"


@pytest.mark.parametrize("value", [
    "package://pkg/../../secret.stl", "package://pkg/a/../b.stl", "package://pkg//x.stl", "package://pkg/x\\y.stl",
    "package://pkg/c:/x.stl",
])
def test_a_package_path_cannot_leave_the_package(tmp_path, value):
    put(tmp_path, "pkg/x.stl")
    put(tmp_path, "urdf/r.urdf", urdf(value))
    found = resolve(tmp_path)
    assert found["mapped"] == {} and found["problems"][0]["kind"] == "outside"


@pytest.mark.parametrize("value,kind,code", [
    ("../../secret.stl", "outside", "OUTSIDE_ROOT"),
    ("/etc/passwd", "outside", "INVALID_PATH"),
    ("a\\b.stl", "outside", "INVALID_PATH"),
    ("file:///etc/passwd", "remote", "UNSUPPORTED_URI"),
    ("https://x.test/m.stl", "remote", "UNSUPPORTED_URI"),
    ("model://m/m.stl", "remote", "UNSUPPORTED_URI"),
    ("meshes/none.stl", "unresolved", "MESH_MISSING"),
])
def test_relative_references_stay_inside_and_exist(tmp_path, value, kind, code):
    root = tmp_path / "a" / "root"
    put(tmp_path, "a/secret.stl")
    put(root, "urdf/r.urdf", urdf(value))
    problem = resolve(root)["problems"][0]
    assert (problem["kind"], problem["code"]) == (kind, code)


def test_dot_dot_inside_the_root_is_fine(tmp_path):
    put(tmp_path, "meshes/a.stl")
    put(tmp_path, "urdf/r.urdf", urdf("../meshes/a.stl", "./b.stl"))
    put(tmp_path, "urdf/b.stl")
    assert resolve(tmp_path)["mapped"] == {"../meshes/a.stl": "meshes/a.stl", "./b.stl": "urdf/b.stl"}


def test_a_symlink_on_the_path_is_refused(tmp_path):
    root = tmp_path / "root"
    target = put(tmp_path, "secret.stl")
    put(root, "urdf/r.urdf", urdf("link.stl", "dir/m.stl", "package://pkg/link.stl"))
    (root / "urdf/link.stl").symlink_to(target)
    (root / "urdf/dir").symlink_to(tmp_path)
    put(root, "pkg/package.xml", package_xml("pkg"))
    (root / "pkg/link.stl").symlink_to(target)
    found = resolve(root)
    assert found["mapped"] == {}
    assert {p["code"] for p in found["problems"]} == {"UNSAFE_ENTRY"}


def test_a_symlinked_package_xml_is_not_read_for_a_name(tmp_path):
    root = tmp_path / "root"
    outside = put(tmp_path, "evil.xml", package_xml("pkg"))
    (root / "here").mkdir(parents=True)
    (root / "here/package.xml").symlink_to(outside)
    put(root, "here/m.stl")
    put(root, "urdf/r.urdf", urdf("package://pkg/m.stl"))
    assert resolve(root)["problems"][0]["code"] == "PACKAGE_UNRESOLVED"


def test_a_bad_package_xml_declares_nothing(tmp_path):
    put(tmp_path, "a/package.xml", "<package><name>pkg")
    put(tmp_path, "b/package.xml", '<!DOCTYPE p [<!ENTITY e "pkg">]><package><name>&e;</name></package>')
    put(tmp_path, "urdf/r.urdf", urdf("package://pkg/m.stl"))
    assert resolve(tmp_path)["problems"][0]["code"] == "PACKAGE_UNRESOLVED"


# ---------------------------------------------------------------- parsing


@pytest.mark.parametrize("name", ["robot.xacro", "robot.urdf.xacro"])
def test_a_xacro_file_gets_the_exact_guidance(name):
    with pytest.raises(AssetError) as caught:
        urdf_refs.check_urdf_path(name)
    assert caught.value.code == "XACRO" and "xacro robot.urdf.xacro > robot.urdf" in str(caught.value)


@pytest.mark.parametrize("text", [
    '<robot xmlns:xacro="http://www.ros.org/wiki/xacro"><xacro:include filename="x"/></robot>',
    '<robot xmlns:xacro="http://wiki.ros.org/xacro"><xacro:macro name="m"/></robot>',
    '<robot><link name="${prefix}a"/></robot>',
    '<robot><link name="a"><visual><geometry><mesh filename="$(find p)/m.stl"/></geometry></visual></link></robot>',
    '<robot><xacro:macro name="m"/></robot>',
])
def test_xacro_content_is_refused(text):
    with pytest.raises(AssetError) as caught:
        urdf_refs.parse(text.encode())
    assert caught.value.code == "XACRO"


def test_an_expanded_file_declaring_the_xacro_namespace_is_a_urdf():
    tree = urdf_refs.parse(b'<robot name="r" xmlns:xacro="http://www.ros.org/wiki/xacro"><link name="a"/></robot>')
    assert tree.find("link") is not None


@pytest.mark.parametrize("text,code", [
    (b'<!DOCTYPE r [<!ENTITY a "b">]><robot/>', "URDF_XML"),
    (b"<robot", "URDF_XML"),
    (b"<sdf/>", "URDF_XML"),
])
def test_bad_xml_is_refused(text, code):
    with pytest.raises(AssetError) as caught:
        urdf_refs.parse(text)
    assert caught.value.code == code


def test_a_gazebo_plugin_filename_is_not_a_mesh(tmp_path):
    put(tmp_path, "urdf/r.urdf", urdf(extra='<gazebo><plugin filename="libgazebo_ros_control.so" name="p"/></gazebo>'))
    assert resolve(tmp_path) == dict(mapped={}, packages={}, problems=[], package_files=[])


def test_a_material_texture_is_a_dependency(tmp_path):
    put(tmp_path, "urdf/t.png")
    put(tmp_path, "urdf/r.urdf", '<robot name="r"><material name="m"><texture filename="t.png"/></material></robot>')
    assert resolve(tmp_path)["mapped"] == {"t.png": "urdf/t.png"}


def test_the_rewritten_copy_keeps_comments_and_names_only_private_files(tmp_path):
    text = ('<robot name="r"><!-- isaac:source_drive {} --><link name="a"><visual><geometry>'
            '<mesh filename="package://p/m.stl"/></geometry></visual></link></robot>')
    tree = urdf_refs.parse(text.encode())
    data = urdf_refs.rewritten(tree, {"package://p/m.stl": "p/m.stl"}, tmp_path / "project").decode()
    assert "isaac:source_drive" in data and f'{tmp_path}/project/p/m.stl' in data and "package://" not in data


# ---------------------------------------------------------------- mesh files


def dae(image="tex.png"):
    return (f'<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema"><asset/><library_images><image id="i">'
            f"<init_from>{image}</init_from></image></library_images><library_effects><effect id='e'><profile_COMMON>"
            f"<newparam sid='s'><surface type='2D'><init_from>i</init_from></surface></newparam></profile_COMMON></effect>"
            f"</library_effects></COLLADA>")


def test_textures_of_dae_and_obj_are_followed_and_absent_ones_reported(tmp_path):
    put(tmp_path, "m/a.dae", dae())
    put(tmp_path, "m/tex.png")
    put(tmp_path, "m/b.obj", "mtllib b.mtl\nv 0 0 0\n")
    put(tmp_path, "m/b.mtl", "newmtl x\nmap_Kd ../t/c.png\nmap_Ks gone.png\n")
    put(tmp_path, "t/c.png")
    put(tmp_path, "m/d.fbx")
    project = urdf_refs.project_from_folder(tmp_path)
    followed = urdf_refs.follow_mesh_files(project, ["m/a.dae", "m/b.obj", "m/d.fbx"])
    assert followed["found"] == ["m/b.mtl", "m/tex.png", "t/c.png"]
    assert followed["missing"] == [dict(reference="gone.png", referenced_by="m/b.mtl")]
    assert followed["unfollowable"][0]["file"] == "m/d.fbx"


def test_a_texture_outside_is_reported_and_not_followed(tmp_path):
    root = tmp_path / "root"
    put(tmp_path, "secret.png")
    put(root, "a.dae", dae("../secret.png"))
    put(root, "b.dae", dae("/abs/x.png"))
    project = urdf_refs.project_from_folder(root)
    followed = urdf_refs.follow_mesh_files(project, ["a.dae", "b.dae"])
    assert followed["found"] == [] and {m["reference"] for m in followed["missing"]} == {"../secret.png", "/abs/x.png"}


def test_a_dae_without_a_unit_gets_the_default_one():
    plain = b'<?xml version="1.0"?><COLLADA><asset version="1"><up_axis>Y_UP</up_axis></asset></COLLADA>'
    assert urdf_refs.dae_without_unit(plain)
    fixed = urdf_refs.dae_with_unit(plain)
    assert b'<asset version="1"><unit name="meter" meter="1"/><up_axis>' in fixed
    assert not urdf_refs.dae_without_unit(fixed)


# ---------------------------------------------------------------- the host route


def test_a_urdf_in_a_store_pack_can_be_located_and_snapshotted(env):
    put(env / "bot", "m.stl")
    path = put(env / "bot", "r.urdf", urdf("m.stl"))
    made = Broker("")
    root_id, rel = made.locate(str(path), kind="urdf")
    assert rel == "r.urdf"
    made.staging = env.parent / "staging"
    made.staging.mkdir(mode=0o700)
    snap = made.snapshot(root_id, rel, kind="urdf")
    _, manifest = made.verify(snap["snapshot_id"], snap["manifest_sha256"], kind="urdf")
    assert manifest["root_name"] == "bot"


def test_a_corrupt_urdf_pack_is_refused(env):
    path = put(env / "bot", "r.urdf", urdf())
    packs.pin_pack(env / "bot")
    path.write_text(urdf("x.stl"))
    with pytest.raises(AssetError) as caught:
        Broker("").locate(str(path), kind="urdf")
    assert caught.value.code == "PACK_CORRUPT"


def test_a_symlink_in_a_urdf_pack_is_refused_by_the_snapshot(env, tmp_path):
    path = put(env / "bot", "r.urdf", urdf("m.stl"))
    (env / "bot/m.stl").symlink_to(put(tmp_path, "secret.stl"))
    made = Broker("")
    made.staging = tmp_path / "staging"
    made.staging.mkdir(mode=0o700)
    root_id, rel = made.locate(str(path), kind="urdf")
    with pytest.raises(AssetError, match="symlink"):
        made.snapshot(root_id, rel, kind="urdf")


def test_a_project_files_asset_path_is_not_approval_for_a_urdf(env, tmp_path, monkeypatch):
    project = tmp_path / "cloned"
    put(project, "isaac-mcp.json", json.dumps(dict(asset_path=str(tmp_path / "elsewhere"))))
    path = put(tmp_path / "elsewhere" / "bot", "r.urdf", urdf())
    monkeypatch.setenv("ISAAC_MCP_PROJECT", str(project))
    with pytest.raises(AssetError) as caught:
        Broker("").locate(str(path), kind="urdf")
    assert caught.value.code == "ROOT_NOT_APPROVED"


def test_kinds_are_told_apart_with_the_tool_to_use(env, broker):
    usd = put(env / "bot", "s.usda", "#usda 1.0\n")
    with pytest.raises(AssetError) as caught:
        broker.locate(str(usd), kind="urdf")
    assert caught.value.code == "UNSUPPORTED_FORMAT"
    path = put(env / "bot", "r.urdf", urdf())
    with pytest.raises(AssetError, match="assets__import_urdf"):
        broker.locate(str(path))
    xacro = put(env / "bot", "r.urdf.xacro", "<robot/>")
    with pytest.raises(AssetError) as caught:
        broker.locate(str(xacro), kind="urdf")
    assert caught.value.code == "XACRO"


def test_scan_lists_urdf_files(broker):
    root = broker.roots["p"][0]
    put(root, "a.urdf", urdf())
    assert [(f["relative_path"], f["supported"]) for f in broker.scan("p")["files"]] == [("a.urdf", True)]


# ---------------------------------------------------------------- building a pack


def build(broker, *sources, **options):
    return packs.run_tool("assets.pack_build", dict(pack="bot", sources=list(sources), **options), broker)


def test_a_urdf_build_needs_no_usd_parser_and_copies_only_what_it_names(env, broker, monkeypatch):
    monkeypatch.setattr(packs, "usd_python", lambda: None)
    root = broker.roots["p"][0]
    put(root, "package.xml", package_xml("pkg"))
    put(root, "meshes/a.dae", dae())
    put(root, "meshes/tex.png")
    put(root, "meshes/unused.stl")
    put(root, "launch/x.launch")
    path = put(root, "urdf/r.urdf", urdf("package://pkg/meshes/a.dae"))
    before = path.read_bytes()
    report = build(broker, str(path))
    assert report["status"] == "ok" and report["backend"] is None
    assert report["entries"] == ["urdf/r.urdf"] and report["copied"] == 4
    assert report["urdf_packages"] == {"pkg": dict(folder=".", found_by="package.xml")}
    assert report["verified"]["self_contained"] is True
    folder = env / "bot"
    assert (folder / "package.xml").is_file() and (folder / "meshes/tex.png").is_file()
    assert not (folder / "meshes/unused.stl").exists() and not (folder / "launch").exists()
    assert (folder / "urdf/r.urdf").read_bytes() == before and path.read_bytes() == before
    assert "urdf/r.urdf" in assets.pins(folder)


def test_a_package_named_only_by_the_root_folder_stays_findable_in_the_pack(env, tmp_path):
    root = tmp_path / "example-robot-data"
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    config = tmp_path / "a.json"
    config.write_text(json.dumps(dict(version=1, profile="trusted_project", roots={"e": str(root)}, staging=str(staging))))
    put(root, "robots/m.stl")
    path = put(root, "robots/r.urdf", urdf("package://example-robot-data/robots/m.stl"))
    made = Broker(str(config))
    report = packs.run_tool("assets.pack_build", dict(pack="bot", sources=[str(path)]), made)
    assert report["entries"] == ["example-robot-data/robots/r.urdf"]
    assert report["verified"]["self_contained"] is True
    assert (env / "bot/example-robot-data/robots/m.stl").is_file()
    doctor = packs.run_tool("assets.pack_doctor", dict(pack="bot"), made)
    assert doctor["ok"] is True and doctor["folders"][0]["dependencies"]["self_contained"] is True


def test_a_build_with_a_missing_mesh_copies_nothing_unless_allowed(env, broker):
    root = broker.roots["p"][0]
    path = put(root, "r.urdf", urdf("gone.stl", "package://nowhere/x.stl"))
    with pytest.raises(packs.PackError) as caught:
        build(broker, str(path))
    assert caught.value.code == "UNRESOLVED_DEPENDENCIES" and "gone.stl" in str(caught.value)
    assert "package://nowhere/x.stl" in str(caught.value) and not (env / "bot").exists()
    report = build(broker, str(path), allow_unresolved=True)
    assert report["verified"]["self_contained"] is False and report["verified"]["accepted_unresolved"]
    assert report["importable"] is False and "package://nowhere/x.stl" in " ".join(report["import_blockers"])
    assert (env / "bot/r.urdf").is_file()


def test_a_mesh_outside_the_root_is_not_copied(env, broker, tmp_path):
    root = broker.roots["p"][0]
    put(tmp_path, "secret.stl")
    path = put(root, "r.urdf", urdf("../secret.stl"))
    with pytest.raises(packs.PackError) as caught:
        build(broker, str(path))
    assert caught.value.details["outside"][0]["reference"] == "../secret.stl"
    build(broker, str(path), allow_unresolved=True)
    assert sorted(p.name for p in (env / "bot").iterdir() if p.suffix == ".stl") == []


def test_a_symlinked_mesh_is_not_copied(env, broker, tmp_path):
    root = broker.roots["p"][0]
    path = put(root, "r.urdf", urdf("m.stl"))
    (root / "m.stl").symlink_to(put(tmp_path, "secret.stl", "secret"))
    with pytest.raises(packs.PackError) as caught:
        build(broker, str(path))
    assert caught.value.code == "UNRESOLVED_DEPENDENCIES" and "symlink" in str(caught.value)


def test_a_missing_texture_builds_but_is_not_self_contained(env, broker):
    root = broker.roots["p"][0]
    put(root, "a.dae", dae("gone.png"))
    path = put(root, "r.urdf", urdf("a.dae"))
    report = build(broker, str(path))
    assert report["status"] == "ok" and report["missing_textures"][0]["reference"] == "gone.png"
    assert report["verified"]["self_contained"] is False and report["notes"]
    doctor = packs.run_tool("assets.pack_doctor", dict(pack="bot"), broker)["folders"][0]
    assert "gone.png" in doctor["warnings"][0] and doctor["dependencies"]["self_contained"] is False


def test_a_xacro_source_gets_the_guidance_and_nothing_runs(env, broker):
    path = put(broker.roots["p"][0], "r.urdf.xacro", "<robot/>")
    with pytest.raises(packs.PackError) as caught:
        build(broker, str(path))
    assert caught.value.code == "XACRO" and "never runs xacro" in str(caught.value)


def test_a_source_outside_approved_roots_is_refused(env, broker, tmp_path):
    path = put(tmp_path, "elsewhere/r.urdf", urdf())
    with pytest.raises(packs.PackError) as caught:
        build(broker, str(path))
    assert caught.value.code == "ROOT_NOT_APPROVED"


def test_doctor_finds_a_urdf_pack_broken_later(env, broker):
    root = broker.roots["p"][0]
    put(root, "m.stl")
    path = put(root, "r.urdf", urdf("m.stl"))
    build(broker, str(path))
    (env / "bot/m.stl").unlink()
    report = packs.run_tool("assets.pack_doctor", dict(pack="bot"), broker)["folders"][0]
    assert report["ok"] is False and any("m.stl" in p for p in report["problems"])


def test_a_build_from_a_pack_keeps_packages_findable(env, broker):
    put(env / "first", "pkg/package.xml", package_xml("pkg"))
    put(env / "first", "pkg/m.stl")
    put(env / "first", "r.urdf", urdf("package://pkg/m.stl"))
    report = packs.run_tool("assets.pack_build", dict(pack="second", sources=["r.urdf"], from_pack="first"), broker)
    assert report["verified"]["self_contained"] is True and (env / "second/pkg/package.xml").is_file()


# ---------------------------------------------------------------- physics


LINK = '<link name="{n}"><inertial><origin xyz="{x} 0 0"/><mass value="{m}"/><inertia ixx="{i}" ixy="0" ixz="0" iyy="{i}" iyz="0" izz="{i}"/></inertial>{g}</link>'
BOX = '<collision><geometry><box size="0.6 0.1 0.2"/></geometry></collision>'


def arm(mass=2.0, inertia=0.01, effort=100, geometry="", limits='lower="-1" upper="1"', kind="revolute"):
    limit = f'<limit effort="{effort}" velocity="1" {limits}/>'
    return urdf_refs.parse(
        ('<robot name="r">' + LINK.format(n="base", x=0, m=5, i=0.1, g="") + LINK.format(n="tip", x=0.5, m=mass, i=inertia, g=geometry)
         + f'<joint name="j" type="{kind}"><parent link="base"/><child link="tip"/><axis xyz="0 1 0"/>{limit}</joint></robot>').encode())


def test_gains_hold_the_load_within_the_droop_and_are_critically_damped():
    model = urdf_physics.read_model(arm())
    gain = urdf_physics.default_gains(model)["j"]
    load = 2.0 * 9.81 * 0.5
    assert gain["load"] == pytest.approx(load)
    assert gain["stiffness"] == pytest.approx(load / urdf_physics.DROOP_RAD)
    assert gain["inertia"] == pytest.approx(0.01 + 2.0 * 0.25)
    assert gain["damping"] == pytest.approx(2 * (gain["stiffness"] * gain["inertia"]) ** 0.5)
    stiffness, damping = urdf_physics.usd_gains(gain)
    assert stiffness == pytest.approx(gain["stiffness"] * 3.14159265 / 180, rel=1e-6)


def test_a_continuous_joint_is_sized_for_the_robot_it_may_carry():
    gain = urdf_physics.default_gains(urdf_physics.read_model(arm(kind="continuous")))["j"]
    assert gain["inertia"] == pytest.approx(max(0.51, 7.0 * urdf_physics.WHEEL_RADIUS_M**2))
    light = urdf_physics.default_gains(urdf_physics.read_model(arm(mass=0.1, inertia=1e-4, kind="continuous")))["j"]
    assert light["inertia"] == pytest.approx(5.1 * urdf_physics.WHEEL_RADIUS_M**2)


def test_a_prismatic_joint_is_per_metre():
    model = urdf_physics.read_model(arm(kind="prismatic"))
    gain = urdf_physics.default_gains(model)["j"]
    assert gain["linear"] and urdf_physics.usd_gains(gain)[0] == gain["stiffness"] == pytest.approx(2 * 9.81 / urdf_physics.DROOP_M)


def test_a_massless_subtree_still_gets_a_drive():
    gain = urdf_physics.default_gains(urdf_physics.read_model(arm(mass=0.0, inertia=0.0)))["j"]
    assert gain["stiffness"] > 0 and gain["damping"] > 0


def test_a_kinematic_loop_or_bad_numbers_do_not_hang():
    tree = urdf_refs.parse(
        b'<robot name="r"><link name="a"/><link name="b"/>'
        b'<joint name="x" type="revolute"><parent link="a"/><child link="b"/><axis xyz="nan 1 0"/></joint>'
        b'<joint name="y" type="revolute"><parent link="b"/><child link="a"/></joint></robot>')
    assert set(urdf_physics.default_gains(urdf_physics.read_model(tree))) == {"x", "y"}


def test_the_tutorial_style_inertia_is_named_with_its_size():
    warnings = urdf_physics.warnings(urdf_physics.read_model(arm(mass=10, inertia=1e-3, geometry=BOX)))
    assert any("tip" in w and "too small" in w and "fix the URDF" in w for w in warnings)


def test_a_mesh_link_with_tiny_inertia_is_named():
    warnings = urdf_physics.warnings(urdf_physics.read_model(arm(mass=10, inertia=1e-5)))
    assert any("very small" in w for w in warnings)


def test_a_good_arm_has_no_warnings():
    assert urdf_physics.warnings(urdf_physics.read_model(arm())) == []


@pytest.mark.parametrize("mass,inertia,expected", [(0.0, 0.0, "no mass"), (2.0, -1.0, "not physically possible")])
def test_unusable_mass_and_inertia_are_named(mass, inertia, expected):
    assert any(expected in w for w in urdf_physics.warnings(urdf_physics.read_model(arm(mass=mass, inertia=inertia))))


def test_impossible_inertia_breaking_the_triangle_rule_is_named():
    tree = urdf_refs.parse(
        b'<robot name="r"><link name="a"/><link name="b"><inertial><mass value="1"/>'
        b'<inertia ixx="1" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/></inertial></link>'
        b'<joint name="j" type="fixed"><parent link="a"/><child link="b"/></joint></robot>')
    assert any("not physically possible" in w for w in urdf_physics.warnings(urdf_physics.read_model(tree)))


def test_a_zero_pose_outside_the_limits_is_named():
    warnings = urdf_physics.warnings(urdf_physics.read_model(arm(limits='lower="-2.7" upper="-0.8"')))
    assert any("all-zero pose" in w and "j" in w for w in warnings)


def test_a_weak_joint_is_named():
    assert any("Effort limits" in w and "j (1" in w for w in urdf_physics.warnings(urdf_physics.read_model(arm(effort=1))))


def test_usd_name_matches_what_the_importer_writes():
    assert urdf_physics.usd_name("a-b.c") == "a_b_c"


def test_nothing_here_writes_outside_a_given_folder(tmp_path):
    """Resolving and rewriting only read: no file appears in the source."""
    put(tmp_path, "urdf/r.urdf", urdf("m.stl"))
    put(tmp_path, "urdf/m.stl")
    before = sorted(os.listdir(tmp_path / "urdf"))
    resolve(tmp_path)
    assert sorted(os.listdir(tmp_path / "urdf")) == before


# ---------------------------------------------------------------- one missing mesh among many


def test_one_missing_mesh_does_not_condemn_the_packages_other_meshes(tmp_path):
    put(tmp_path, "pkg/package.xml", package_xml("pkg"))
    put(tmp_path, "pkg/meshes/a.stl", "a")
    put(tmp_path, "pkg/meshes/b.stl", "b")
    put(tmp_path, "urdf/r.urdf", urdf(
        "package://pkg/meshes/a.stl", "package://pkg/meshes/b.stl", "package://pkg/meshes/gone.stl"))
    found = resolve(tmp_path)
    assert set(found["mapped"]) == {
        "package://pkg/meshes/a.stl", "package://pkg/meshes/b.stl"}
    assert [p["reference"] for p in found["problems"]] == ["package://pkg/meshes/gone.stl"]
    problem = found["problems"][0]
    assert problem["code"] == "MESH_MISSING" and "pkg/meshes/gone.stl" in problem["reason"]


def test_doctor_names_only_the_missing_package_mesh(tmp_path):
    root = tmp_path / "bot"
    put(root, "pkg/package.xml", package_xml("pkg"))
    put(root, "pkg/meshes/a.stl", "a")
    put(root, "pkg/meshes/b.stl", "b")
    put(root, "urdf/r.urdf", urdf(
        "package://pkg/meshes/a.stl", "package://pkg/meshes/b.stl", "package://pkg/meshes/gone.stl"))
    packs.pin_pack(root)
    calls = []

    def no_usd(paths):
        calls.append(paths)
        raise AssertionError("a URDF-only pack needs no USD parser")

    report = packs.doctor_folder(root, closure=no_usd)
    assert not calls
    unresolved = report["dependencies"]["unresolved"]
    assert [u["reference"] for u in unresolved] == ["package://pkg/meshes/gone.stl"]
    assert any("pkg/meshes/gone.stl" in p for p in report["problems"])
    assert not any("needs a folder named" in p for p in report["problems"])
    assert report["ok"] is False


def test_dependency_tool_returns_a_urdfs_real_references(env, broker):
    root = broker.roots["p"][0]
    path = put(root, "r.urdf", urdf("package://pkg/meshes/a.stl", "gone.stl"))
    put(root, "pkg/package.xml", package_xml("pkg"))
    mesh = put(root, "pkg/meshes/a.stl", "a")
    out = packs.run_tool("assets.dependencies", {"paths": [str(path)]}, broker)
    result = out["results"][0]
    assert result["urdf"] is True and "error" not in result
    assert str(mesh.resolve()) in result["assets"]
    assert [p["reference"] for p in result["problems"]] == ["gone.stl"]
    assert result["problems"][0]["code"] == "MESH_MISSING"


def test_dependency_tool_explains_an_unresolved_package_instead_of_a_parser_error(env, broker):
    root = broker.roots["p"][0]
    path = put(root, "r.urdf", urdf("package://nowhere/m.stl"))
    out = packs.run_tool("assets.dependencies", {"paths": [str(path)]}, broker)
    result = out["results"][0]
    assert "error" not in result
    problem = result["problems"][0]
    assert problem["code"] == "PACKAGE_UNRESOLVED" and "nothing outside" in problem["reason"]


def test_gltf_and_glb_references_are_followed_and_unreadable_ones_are_visible():
    import json
    import struct

    doc = json.dumps({"buffers": [{"uri": "a.bin"}, {"uri": "data:application/octet-stream;base64,AAAA"}], "images": [{"uri": "t.png"}, {"uri": "../out.png"}]}).encode()
    assert urdf_refs.gltf_uris(".gltf", doc) == ["a.bin", "t.png", "../out.png"]
    glb = b"glTF" + struct.pack("<II", 2, 20 + len(doc)) + struct.pack("<I", len(doc)) + b"JSON" + doc
    assert urdf_refs.gltf_uris(".glb", glb) == ["a.bin", "t.png", "../out.png"]
    for bad in (b"not json", b"[1]", b"{" * 5000):
        assert urdf_refs.gltf_uris(".gltf", bad) is None
    assert urdf_refs.gltf_uris(".glb", b"nope") is None

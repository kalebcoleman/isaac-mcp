"""CPU-only BOM, pack-manifest and stage-mapping coverage; no PhysX claim."""

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKCELL = ROOT / "exts/workcell/workcell"
ASSET_ROOT = Path(os.environ.get("TWINSMITH_ASSET_ROOT")
                  or Path.home() / "isaac-workspace/isaac-mcp-assets")
PRIVATE_PACK = ASSET_ROOT / "ur5e-jenga-workcell/v1"
SPEC = importlib.util.spec_from_file_location(
    "workcell_bom", ROOT / "exts/workcell/workcell/bom.py")
bom = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bom)

EXPECTED_IDS = {
    "bench", "robot", "base_plate", "bota", "quick_changer", "rg2", "finger_pad",
    "d415", "wrist_mount", "overhead_mount", "overhead_release", "overhead_shelf",
    "jenga_block",
}


class Attribute:
    def __init__(self, value):
        self.value = value

    def Get(self):
        return self.value


class Prim:
    def __init__(self, path, kind="Xform", apis=(), attributes=None):
        self.path = path
        self.kind = kind
        self.apis = set(apis)
        self.attributes = dict(attributes or {})

    def GetPath(self):
        return self.path

    def GetTypeName(self):
        return self.kind

    def GetAppliedSchemas(self):
        return list(self.apis)

    def IsValid(self):
        return True

    def GetAttribute(self, name):
        if name in self.attributes:
            return Attribute(self.attributes[name])
        return None


class Stage:
    def __init__(self):
        self.prims = {}

    def add(self, path, kind="Xform", apis=(), attributes=None):
        prim = Prim(path, kind, apis, attributes)
        self.prims[path] = prim
        return prim

    def GetPrimAtPath(self, path):
        return self.prims.get(path)

    def Traverse(self):
        return list(self.prims.values())


def part(path, asset_id, bom_id):
    return {"prim_path": path, "asset_id": asset_id, "bom_id": bom_id}


def camera_and_robot_parts():
    parts = [part("/World/Bench", "bench", "bench")]
    for role in ("robot", "base_plate", "bota", "quick_changer", "rg2"):
        parts.append(part(f"/World/RobotAssembly/{role}", role, role))
    for index in range(2):
        parts.append(part(f"/World/RobotAssembly/rg2/left_finger/black_pad_{index}",
                          "finger_pad", "finger_pad"))
    for name in ("wrist_camera", "overhead_camera"):
        parts.append(part(f"/World/Cameras/{name}", "d415", "d415"))
    for mount in ("wrist_mount", "overhead_mount", "overhead_release", "overhead_shelf"):
        parts.append(part(f"/World/Cameras/{mount}", mount, mount))
    for index in range(54):
        parts.append(part(f"/World/Jenga/Block_{index + 1:02d}", "jenga_block", "jenga_block"))
    return parts


def build_stage(parts):
    stage = Stage()
    stage.add("/World/Light", kind="DistantLight")
    for item in parts:
        attributes = {"workcell:asset_id": item["asset_id"], "workcell:bom_id": item["bom_id"]}
        stage.add(item["prim_path"], attributes=attributes)
        stage.add(item["prim_path"] + "/mesh", kind="Mesh",
                  apis=("PhysicsCollisionAPI",) if item["bom_id"] == "jenga_block" else ())
    return stage


def cell():
    parts = camera_and_robot_parts()
    return parts, build_stage(parts)


def test_bom_contract_and_price_policy():
    rows = bom.load_bom()
    assert {row["bom_id"] for row in rows} == EXPECTED_IDS
    assert {row["asset_id"] for row in rows} == EXPECTED_IDS
    quantities = {row["bom_id"]: row["quantity"] for row in rows}
    assert quantities["jenga_block"] == 54
    assert quantities["d415"] == 2
    assert quantities["finger_pad"] == 2
    assert all(row["quantity"] == 1 for row in rows
               if row["bom_id"] not in {"jenga_block", "d415", "finger_pad"})
    for row in rows:
        if row["price_status"] == "verified_web_list":
            assert row["unit_price"] is not None and row["price_source"].startswith("http")
        else:
            assert row["unit_price"] is None
            assert row["price_status"] == "quote_required"


def test_docs_name_the_pack_contract_and_neutral_paths():
    text = (WORKCELL / "README.md").read_text(encoding="utf-8")
    for row in bom.load_bom():
        assert row["bom_id"] in text
    for required in ("TWINSMITH_ASSET_ROOT", "pack.yaml", "asset_pack.resolve_pack",
                     "verify_dependencies", "--discard-changes", "PhysX"):
        assert required in text
    assert "/home/" not in text and "~/Desktop" not in text


def test_audit_accepts_every_report_part_and_stage_prim():
    rows = bom.load_bom()
    parts, stage = cell()
    report = bom.audit_parts(parts, rows, stage)
    assert report["ok"] and report["errors"] == []
    assert report["parts"] == 68 and report["physical_prims"] == 68
    assert report["bom_ids"]["jenga_block"] == 54
    assert report["bom_ids"]["d415"] == 2
    assert report["bom_ids"]["finger_pad"] == 2


def test_audit_accepts_a_mesh_under_any_mapped_ancestor():
    rows = bom.load_bom()
    parts, stage = cell()
    stage.add("/World/RobotAssembly/robot/extra_detail", kind="Mesh")
    stage.add("/World/Jenga/Block_01/vendor/mesh", kind="Mesh")
    assert bom.audit_parts(parts, rows, stage)["ok"]


def test_audit_catches_unmapped_mesh_and_collider():
    rows = bom.load_bom()
    parts, stage = cell()
    stage.add("/World/Unmapped/debris", kind="Mesh")
    stage.add("/World/Unmapped/fixture", kind="Xform", apis=("PhysicsCollisionAPI",))
    report = bom.audit_parts(parts, rows, stage)
    assert not report["ok"]
    assert report["unmatched_prims"] == ["/World/Unmapped/debris", "/World/Unmapped/fixture"]
    assert "no mapped BOM ancestor" in " ".join(report["errors"])
    with pytest.raises(bom.BomError, match="no mapped BOM ancestor"):
        bom.audit_parts(parts, rows, stage, strict=True)


def test_audit_catches_unknown_bom_id_and_asset_mismatch():
    rows = bom.load_bom()
    parts, stage = cell()
    unknown = parts + [part("/World/Extra", "rg2", "rg2_ft")]
    report = bom.audit_parts(unknown, rows, stage)
    assert not report["ok"] and "unknown BOM id" in " ".join(report["errors"])
    wrong = [dict(item) for item in parts]
    wrong[0]["asset_id"] = "husarion_rg2"
    report = bom.audit_parts(wrong, rows, stage)
    assert not report["ok"] and "does not match BOM row" in " ".join(report["errors"])


def test_audit_catches_missing_prim_and_stage_attribute_drift():
    rows = bom.load_bom()
    parts, stage = cell()
    del stage.prims["/World/Cameras/wrist_mount"]
    report = bom.audit_parts(parts, rows, stage)
    assert not report["ok"] and "does not exist" in " ".join(report["errors"])
    parts, stage = cell()
    stage.prims["/World/Cameras/overhead_release"].attributes["workcell:bom_id"] = "overhead_mount"
    report = bom.audit_parts(parts, rows, stage)
    assert not report["ok"] and "workcell:bom_id does not match" in " ".join(report["errors"])


def test_audit_requires_a_mapping_for_every_bom_row():
    rows = bom.load_bom()
    parts, stage = cell()
    parts = [item for item in parts if item["bom_id"] != "overhead_release"]
    for path in [path for path in stage.prims if path.startswith("/World/Cameras/overhead_release")]:
        del stage.prims[path]
    report = bom.audit_parts(parts, rows, stage)
    assert not report["ok"]
    assert "overhead_release" in report["unmapped_bom_ids"]


def test_manifest_checks_schema_hashes_and_bom_coverage(tmp_path):
    rows = bom.load_bom()
    files = []
    for row in rows:
        asset_id = row["asset_id"]
        path = f"{asset_id}/{asset_id}.usdc"
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(asset_id.encode())
        files.append({"asset_id": asset_id, "path": path,
                      "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                      "source": "fixture", "license": "private test fixture",
                      "redistributable": False})
    materials = tmp_path / "materials/materials.usda"
    materials.parent.mkdir()
    materials.write_bytes(b"materials")
    files.append({"path": "materials/materials.usda",
                  "sha256": hashlib.sha256(materials.read_bytes()).hexdigest(),
                  "source": "fixture", "license": "private test fixture",
                  "redistributable": False})
    manifest = {"id": "ur5e-jenga-workcell", "version": "v1", "files": files}
    report = bom.verify_manifest(manifest, rows=rows, root=tmp_path,
                                 expected_id="ur5e-jenga-workcell", expected_version="v1")
    assert report["hashes_verified"] and report["files"] == 14
    assert report["asset_ids"] == sorted(EXPECTED_IDS | {"materials"})
    (tmp_path / "robot/robot.usdc").write_bytes(b"changed")
    with pytest.raises(bom.BomError, match="hash changed"):
        bom.verify_manifest(manifest, rows=rows, root=tmp_path)
    broken = {"id": "ur5e-jenga-workcell", "version": "v1", "files": [dict(files[0])]}
    del broken["files"][0]["license"]
    with pytest.raises(bom.BomError, match="license"):
        bom.verify_manifest(broken)
    extra = {"id": "ur5e-jenga-workcell", "version": "v1",
             "files": files + [{"asset_id": "unexpected", "path": "unexpected/a.usda",
                               "sha256": "0" * 64, "source": "fixture",
                               "license": "private test fixture", "redistributable": False}]}
    with pytest.raises(bom.BomError, match="no BOM row"):
        bom.verify_manifest(extra, rows=rows)


@pytest.mark.skipif(not (PRIVATE_PACK / "pack.yaml").is_file(),
                    reason="private workcell pack is not installed")
def test_installed_private_pack_matches_bom_and_hashes():
    report = bom.verify_manifest(PRIVATE_PACK / "pack.yaml", rows=bom.load_bom(),
                                 root=PRIVATE_PACK, expected_id="ur5e-jenga-workcell",
                                 expected_version="v1")
    assert report["hashes_verified"] and report["files"] == 14
    assert "materials" in report["asset_ids"]


def test_config_roles_use_logical_bom_asset_ids():
    rows = bom.load_bom()
    config = {"assets": {row["bom_id"]: row["asset_id"] for row in rows}}
    config["assets"]["materials"] = "materials"
    assert bom.verify_config_assets(config, rows) == EXPECTED_IDS
    broken = {"assets": dict(config["assets"], rg2="husarion_rg2")}
    with pytest.raises(bom.BomError, match="no BOM row"):
        bom.verify_config_assets(broken, rows)
    del config["assets"]["d415"]
    with pytest.raises(bom.BomError, match="not mapped by the config"):
        bom.verify_config_assets(config, rows)

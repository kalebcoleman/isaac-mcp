"""Real USD regression for native importer Physics variant output."""

import pytest
import json
import sys
from types import SimpleNamespace
from test_scene_usd import scene  # registers the extension package path
from pxr import Usd, UsdGeom, UsdPhysics
from isaac_mcp.project_assets import AssetError, Broker
from sim_tools.advanced import Advanced
from sim_tools.urdf import prepare_generated_physics


def make_output(
    tmp_path, selection, variants=("none", "physics"), bodies=True, joint=False
):
    payload_path = tmp_path / "physics.usda"
    payload = Usd.Stage.CreateNew(str(payload_path))
    robot = payload.DefinePrim("/Robot", "Xform")
    payload.SetDefaultPrim(robot)
    link = payload.DefinePrim("/Robot/Link", "Xform")
    if bodies:
        UsdPhysics.RigidBodyAPI.Apply(link)
        UsdPhysics.MassAPI.Apply(link).CreateMassAttr(1)
        UsdPhysics.ArticulationRootAPI.Apply(link)
    UsdPhysics.CollisionAPI.Apply(
        UsdGeom.Cube.Define(payload, "/Robot/Link/Box").GetPrim()
    )
    if joint:
        UsdPhysics.RevoluteJoint.Define(payload, "/Robot/Hinge")
    payload.GetRootLayer().Save()
    path = tmp_path / "robot.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    root = stage.DefinePrim("/Robot", "Xform")
    stage.SetDefaultPrim(root)
    if variants:
        v = root.GetVariantSets().AddVariantSet("Physics")
        for name in variants:
            v.AddVariant(name)
            v.SetVariantSelection(name)
            if name != "none":
                with v.GetVariantEditContext():
                    root.GetPayloads().AddPayload("./physics.usda")
        v.SetVariantSelection(selection)
    else:
        root.GetReferences().AddReference("./physics.usda")
    stage.GetRootLayer().Save()
    return path, payload_path


@pytest.mark.parametrize("selection", ["", "none", "physx", "physics"])
def test_select_shared_physics_without_touching_payload_or_live_stage(
    tmp_path, scene, selection
):
    path, payload = make_output(tmp_path, selection, joint=True)
    payload_before = payload.read_bytes()
    live = scene.stage()
    live_before = [l.ExportToString() for l in live.GetUsedLayers()]
    result = prepare_generated_physics(path, tmp_path, movable_joints=1)
    assert result["selected_before"] == selection and result["selected"] == "physics"
    assert result["authored"] == dict(
        rigid_bodies=1,
        collisions=1,
        joints=1,
        movable_joints=1,
        articulations=1,
        authored_masses=1,
    )
    assert "unknown" in result["runtime_readiness"]
    reopened = Usd.Stage.Open(str(path))
    assert (
        reopened.GetDefaultPrim().GetVariantSet("Physics").GetVariantSelection()
        == "physics"
    )
    assert reopened.GetPrimAtPath("/Robot/Link").HasAPI(UsdPhysics.RigidBodyAPI)
    assert payload.read_bytes() == payload_before
    assert [l.ExportToString() for l in live.GetUsedLayers()] == live_before


def test_flat_output_remains_byte_identical(tmp_path):
    path, _ = make_output(tmp_path, "", variants=())
    before = path.read_bytes()
    result = prepare_generated_physics(path, tmp_path)
    assert result["variant_set"] is None and result["authored"]["rigid_bodies"] == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "reason", ["unknown_variant", "no_body", "missing_joint", "missing_payload"]
)
def test_invalid_native_output_refused_before_persisting(tmp_path, reason):
    path, payload = make_output(
        tmp_path,
        "none",
        variants=("none", "physx")
        if reason == "unknown_variant"
        else ("none", "physics"),
        bodies=reason != "no_body",
    )
    if reason == "missing_payload":
        payload.unlink()
    before = path.read_bytes()
    with pytest.raises(AssetError) as error:
        prepare_generated_physics(
            path, tmp_path, movable_joints=int(reason == "missing_joint")
        )
    assert error.value.code == "URDF_PHYSICS"
    assert path.read_bytes() == before


def test_output_escape_and_symlink_refused_without_writes(tmp_path):
    outside = tmp_path / "outside"
    inside = tmp_path / "private"
    outside.mkdir()
    inside.mkdir()
    path, _ = make_output(outside, "none")
    before = path.read_bytes()
    for entry in (path, inside / "link.usda"):
        if entry != path:
            entry.symlink_to(path)
        with pytest.raises(AssetError, match="private directory"):
            prepare_generated_physics(entry, inside)
    assert path.read_bytes() == before


@pytest.mark.parametrize("converted_body", [True, False])
def test_conversion_validates_before_broker_snapshot_and_scene_composition(
    scene, tmp_path, monkeypatch, converted_body
):
    project = tmp_path / "project"
    project.mkdir()
    urdf = project / "minimal.urdf"
    urdf.write_text('<robot name="fixture"><link name="base"/></robot>')
    source_before = urdf.read_bytes()
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    config_file = tmp_path / "approval.json"
    config_file.write_text(
        json.dumps(
            dict(
                version=1,
                profile="trusted_project",
                roots={"fixture": str(project)},
                staging=str(staging),
            )
        )
    )
    scene.broker = Broker(str(config_file))
    snapshot = scene.broker.snapshot("fixture", "minimal.urdf", kind="urdf")

    class Converter:
        def __init__(self, config):
            self.config = config

        def import_urdf(self):
            from pathlib import Path

            output = Path(self.config.usd_path)
            output.mkdir()
            path, _ = make_output(output, "physx", bodies=converted_body)
            return str(path)

    monkeypatch.setitem(
        sys.modules,
        "isaacsim.asset.importer.urdf",
        SimpleNamespace(URDFImporterConfig=SimpleNamespace, URDFImporter=Converter),
    )
    stage = scene.stage()
    root_before = stage.GetRootLayer().ExportToString()
    session_before = stage.GetSessionLayer().ExportToString()
    args = dict(
        snapshot_id=snapshot["snapshot_id"],
        manifest_sha256=snapshot["manifest_sha256"],
        prim_path="/World/Imported",
    )
    if converted_body:
        result = Advanced(scene, None).import_urdf(stage, **args)
        assert result["converted_physics"]["selected"] == "physics"
        imported = stage.GetPrimAtPath("/World/Imported/Asset/Link")
        assert imported.HasAPI(UsdPhysics.RigidBodyAPI)
        assert imported.GetAttribute("physics:mass").Get() == 1
        filename, _ = scene.broker.verify(
            result["snapshot_id"], result["manifest_sha256"]
        )
        assert (
            Usd.Stage.Open(str(filename))
            .GetDefaultPrim()
            .GetVariantSet("Physics")
            .GetVariantSelection()
            == "physics"
        )
    else:
        with pytest.raises(AssetError) as error:
            Advanced(scene, None).import_urdf(stage, **args)
        assert error.value.code == "URDF_PHYSICS"
        assert not stage.GetPrimAtPath("/World/Imported")
        assert stage.GetSessionLayer().ExportToString() == session_before
        assert len(list(staging.iterdir())) == 1
    assert stage.GetRootLayer().ExportToString() == root_before
    assert urdf.read_bytes() == source_before

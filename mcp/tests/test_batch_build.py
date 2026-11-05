"""Building many things in few calls: the material option, sim.prim.create_many and sim.material.apply paths (no Kit)."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import UsdPhysics, UsdShade
from isaac_mcp.project_assets import AssetError
from test_scene_advanced import advanced  # noqa: F401 - the fixtures
from test_scene_usd import scene  # noqa: F401


def _tower_items():
    items = []
    for layer in range(12):
        for k in range(3):
            off = (k - 1) * 0.0255
            wide = layer % 2 == 0
            items.append(dict(path=f"/World/Tower/B{len(items)}",
                              position=[0, off, 0.0075 + layer * 0.015] if wide else [off, 0, 0.0075 + layer * 0.015],
                              scale=[0.075, 0.025, 0.015] if wide else [0.025, 0.075, 0.015]))
    return items


def _wood(advanced, stage):
    advanced.s.create(stage, "/World/Looks", "Xform")
    advanced.execute("sim.material.create", stage, dict(path="/World/Looks/Wood", color=[0.7, 0.5, 0.3]))
    return "/World/Looks/Wood"


def test_a_36_block_tower_is_one_call(advanced):
    """Review goal: the tower that took over 110 calls takes one, with mass and a material on every block."""
    stage = advanced.s.stage()
    wood = _wood(advanced, stage)
    advanced.s.create(stage, "/World/Tower", "Xform")
    result = advanced.execute("sim.prim.create_many", stage, dict(
        items=_tower_items(), defaults=dict(kind="Cube", rigid=True, mass=0.015, material=wood)))
    assert result["count"] == 36 and len(result["paths"]) == 36
    for path in result["paths"]:
        prim = stage.GetPrimAtPath(path)
        assert prim.HasAPI(UsdPhysics.RigidBodyAPI)
        assert UsdPhysics.MassAPI(prim).GetMassAttr().Get() == pytest.approx(0.015)
        assert UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0].GetPath() == wood
        assert path in advanced.s.owned


def test_one_bad_item_removes_all_earlier_ones(advanced):
    stage = advanced.s.stage()
    items = [dict(path="/World/A", kind="Cube"), dict(path="/World/B", kind="Sphere"),
             dict(path="/World/C", kind="Cube", mass=-1, rigid=True), dict(path="/World/D", kind="Cube")]
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.prim.create_many", stage, dict(items=items))
    assert error.value.details["failed_index"] == 2 and error.value.details["failed_path"] == "/World/C"
    assert "items[2]" in str(error.value) and "nothing was created" in str(error.value)
    assert not any(stage.GetPrimAtPath(p) for p in ("/World/A", "/World/B", "/World/C", "/World/D"))
    assert not {"/World/A", "/World/B"} & advanced.s.owned


def test_a_missing_material_creates_nothing(advanced):
    stage = advanced.s.stage()
    with pytest.raises(AssetError):
        advanced.execute("sim.prim.create_many", stage, dict(
            items=[dict(path="/World/A", kind="Cube", material="/World/Looks/Nope")]))
    assert not stage.GetPrimAtPath("/World/A")


def test_create_with_a_material_binds_it_or_creates_nothing(advanced):
    from sim_tools.advanced import create_one

    stage = advanced.s.stage()
    wood = _wood(advanced, stage)
    created, _ = create_one(advanced.s, stage, dict(path="/World/Ok", kind="Cube", material=wood))
    assert created["material"] == wood
    with pytest.raises(AssetError):
        create_one(advanced.s, stage, dict(path="/World/Bad", kind="Cube", material="/World/Looks/Missing"))
    assert not stage.GetPrimAtPath("/World/Bad") and "/World/Bad" not in advanced.s.owned


@pytest.mark.parametrize("args, text", [
    (dict(items=[]), "1 to 200"),
    (dict(items=[dict(path=f"/World/P{i}", kind="Cube") for i in range(201)]), "1 to 200"),
    (dict(items=[dict(path="/World/A", kind="Cube", colour=[1, 0, 0])]), "unknown keys"),
    (dict(items=[dict(path="/World/A")]), "path and kind"),
    (dict(items=[dict(path="/World/A", kind="Cube")], defaults=[1]), "defaults must be an object"),
])
def test_bad_batches_are_refused_before_any_edit(advanced, args, text):
    stage = advanced.s.stage()
    before = stage.GetSessionLayer().ExportToString()
    with pytest.raises(AssetError, match=text):
        advanced.execute("sim.prim.create_many", stage, args)
    assert stage.GetSessionLayer().ExportToString() == before


def test_material_apply_takes_many_paths(advanced):
    stage = advanced.s.stage()
    wood = _wood(advanced, stage)
    for name in ("A", "B", "C"):
        advanced.s.create(stage, f"/World/{name}", "Cube")
    result = advanced.execute("sim.material.apply", stage, dict(paths=["/World/A", "/World/B", "/World/C"], material_path=wood))
    assert [r["path"] for r in result["applied"]] == ["/World/A", "/World/B", "/World/C"]
    with pytest.raises(AssetError, match="path.*or paths"):
        advanced.execute("sim.material.apply", stage, dict(path="/World/A", paths=["/World/B"], material_path=wood))
    single = advanced.execute("sim.material.apply", stage, dict(path="/World/A", material_path=wood))
    assert single["material"] == wood


def test_material_apply_paths_is_all_or_none_and_needs_a_target(advanced):
    stage = advanced.s.stage()
    wood = _wood(advanced, stage)
    advanced.s.create(stage, "/World/A", "Cube")
    advanced.s.create(stage, "/World/B", "Cube")
    with pytest.raises(AssetError):
        advanced.execute("sim.material.apply", stage, dict(paths=["/World/A", "/World/Missing", "/World/B"], material_path=wood))
    assert not UsdShade.MaterialBindingAPI(stage.GetPrimAtPath("/World/A")).GetDirectBinding().GetMaterial()
    with pytest.raises(AssetError, match="one of the two"):
        advanced.execute("sim.material.apply", stage, dict(material_path=wood))


def test_a_failed_item_keeps_the_original_error_details(advanced):
    stage = advanced.s.stage()
    with pytest.raises(AssetError) as error:
        advanced.execute("sim.prim.create_many", stage, dict(items=[dict(path="/World/A", kind="Cube", rigid=True, mass=1, density=1)]))
    assert error.value.code == "CONFLICT"
    assert error.value.details["retry_with"] and error.value.details["failed_index"] == 0


def test_a_shared_material_skips_xforms_in_the_list(advanced):
    stage = advanced.s.stage()
    wood = _wood(advanced, stage)
    result = advanced.execute("sim.prim.create_many", stage, dict(
        items=[dict(path="/World/Group", kind="Xform"), dict(path="/World/Group/Block")],
        defaults=dict(kind="Cube", rigid=True, mass=0.015, material=wood)))
    assert result["count"] == 2
    assert stage.GetPrimAtPath("/World/Group").GetTypeName() == "Xform"
    # An item that asks for a material on an Xform itself is still refused.
    with pytest.raises(AssetError):
        advanced.execute("sim.prim.create_many", stage, dict(items=[dict(path="/World/G2", kind="Xform", material=wood)]))

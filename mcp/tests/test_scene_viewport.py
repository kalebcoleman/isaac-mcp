"""Real USD source preservation around the native viewport-command boundary."""

from contextlib import contextmanager
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics
from isaac_mcp.project_assets import AssetError
from sim_tools.viewport import frame
from test_scene_usd import scene  # noqa: F401


@pytest.fixture
def view(scene):
    stage = scene.stage()
    camera = UsdGeom.Camera.Define(stage, "/World/Camera")
    camera.AddTranslateOp().Set(Gf.Vec3d(6, 6, 6))
    UsdGeom.Cube.Define(stage, "/World/Box")
    state = SimpleNamespace(selected=[], disabled=False, calls=0)
    context = SimpleNamespace(
        get_stage=lambda: stage,
        get_name=lambda: "",
        get_selection=lambda: SimpleNamespace(
            set_selected_prim_paths=lambda paths, _: state.selected.extend(paths)
        ),
    )
    viewport = SimpleNamespace(
        camera_path=camera.GetPath(), stage=stage, usd_context_name="", time=Usd.TimeCode.Default()
    )

    @contextmanager
    def disabled():
        state.disabled = True
        try:
            yield
        finally:
            state.disabled = False

    def native(_):
        assert state.disabled
        state.calls += 1
        camera.GetPrim().GetAttribute("xformOp:translate").Set(Gf.Vec3d(0, 0, 10))
        return True

    state.native = native
    state.camera = camera
    state.viewport = viewport
    state.context = context
    state.call = lambda path="/World/Box": frame(
        scene, stage, path, context, viewport, state.native, disabled
    )
    return state


def test_frame_moves_camera_in_session_preserves_source_and_external_edit_target(scene, view):
    stage = scene.stage()
    external = Sdf.Layer.CreateAnonymous()
    stage.GetRootLayer().subLayerPaths.append(external.identifier)
    stage.SetEditTarget(external)
    previous = stage.GetEditTarget()
    source = {layer: layer.ExportToString() for layer in (stage.GetRootLayer(), external)}
    result = view.call()
    assert all(layer.ExportToString() == text for layer, text in source.items())
    assert stage.GetEditTarget() == previous
    assert stage.GetSessionLayer().GetAttributeAtPath("/World/Camera.xformOp:translate").default == Gf.Vec3d(0, 0, 10)
    assert result["camera_world_matrix"][3][:3] == [0, 0, 10]
    assert result["authored_layer"] == "session" and not result["render_complete"]
    assert view.selected == ["/World/Box"] and not view.disabled


@pytest.mark.parametrize("failure", [False, None, "raise", "viewport", "stage", "camera"])
def test_partial_native_failure_is_explicit_and_restores_target(scene, view, failure):
    stage = scene.stage()
    source = stage.GetRootLayer().ExportToString()
    target = stage.GetEditTarget()
    original = view.native

    def native(viewport):
        original(viewport)
        if failure == "raise":
            raise RuntimeError("native frame failed after camera write")
        if failure == "viewport":
            viewport.usd_context_name = "another"
        elif failure == "stage":
            scene.get_stage = lambda: Usd.Stage.CreateInMemory()
        elif failure == "camera":
            viewport.camera_path = "/World/Other"
        return True if isinstance(failure, str) else failure

    view.native = native
    with pytest.raises(AssetError) as error:
        view.call()
    assert error.value.code == "VIEWPORT_FRAME_FAILED"
    assert "may have occurred" in error.value.details["effects"]
    assert stage.GetRootLayer().ExportToString() == source
    assert stage.GetEditTarget() == target
    assert stage.GetSessionLayer().GetAttributeAtPath("/World/Camera.xformOp:translate")
    assert not view.disabled


@pytest.mark.parametrize("change", ["viewport_stage", "context_stage", "context_name", "source_stage"])
def test_mismatched_target_refuses_before_selection_or_camera_edit(scene, view, change):
    other = Usd.Stage.CreateInMemory()
    if change == "viewport_stage":
        view.viewport.stage = other
    elif change == "context_stage":
        view.context.get_stage = lambda: other
    elif change == "context_name":
        view.viewport.usd_context_name = "other"
    else:
        scene.get_stage = lambda: other
    with pytest.raises(AssetError) as error:
        view.call()
    assert error.value.code == "VIEWPORT_CHANGED"
    assert not view.selected and view.calls == 0


def test_session_sublayer_camera_opinion_refuses_native_auto_retarget(scene, view):
    stage = scene.stage()
    sub = Sdf.Layer.CreateAnonymous()
    stage.GetSessionLayer().subLayerPaths.append(sub.identifier)
    with Usd.EditContext(stage, sub):
        view.camera.GetPrim().GetAttribute("xformOp:translate").Set(Gf.Vec3d(2, 3, 4))
    before = {layer: layer.ExportToString() for layer in stage.GetLayerStack(True)}
    with pytest.raises(AssetError) as error:
        view.call()
    assert error.value.code == "VIEWPORT_CAMERA_UNSUPPORTED"
    assert all(layer.ExportToString() == text for layer, text in before.items())
    assert not view.selected and view.calls == 0


def test_unrelated_session_sublayer_does_not_block_framing(scene, view):
    sub = Sdf.Layer.CreateAnonymous()
    scene.stage().GetSessionLayer().subLayerPaths.append(sub.identifier)
    with Usd.EditContext(scene.stage(), sub):
        UsdGeom.Xform.Define(scene.stage(), "/Other")
    before = sub.ExportToString()
    view.call()
    assert sub.ExportToString() == before


def test_look_through_must_not_move_an_arbitrary_prim(scene, view):
    view.camera.GetPrim().CreateRelationship("omni:kit:viewport:lookThrough:target").SetTargets(["/World/Box"])
    with pytest.raises(AssetError) as error:
        view.call()
    assert error.value.code == "VIEWPORT_CAMERA_UNSUPPORTED" and view.calls == 0


def test_empty_and_bounded_geometry_refuse_without_false_success(scene, view, monkeypatch):
    UsdGeom.Xform.Define(scene.stage(), "/World/Empty")
    with pytest.raises(AssetError) as error:
        view.call("/World/Empty")
    assert error.value.code == "NO_BOUNDS"
    assert "/World/Box" in error.value.details["candidates"]
    monkeypatch.setattr("sim_tools.viewport.CAP", 1)
    with pytest.raises(AssetError) as error:
        view.call("/World")
    assert error.value.code == "LIMIT"
    assert not view.selected and view.calls == 0


def test_frame_and_follow_geometry_free_footprint_use_base_link(scene, view, monkeypatch):
    from sim_tools import viewport as vp

    stage = scene.stage()
    footprint = UsdGeom.Xform.Define(stage, "/World/Robot/base_footprint")
    UsdPhysics.RigidBodyAPI.Apply(footprint.GetPrim())
    UsdGeom.Xform.Define(stage, "/World/Robot/base_link")
    UsdGeom.Cube.Define(stage, "/World/Robot/base_link/Visual")
    requested = "/World/Robot/base_footprint"
    result = view.call(requested)
    assert result["requested_path"] == requested
    assert result["framed_path"] == "/World/Robot/base_link"
    assert result["selection"] == "framed for this call; Stage selection unchanged"
    assert view.selected[-1] == result["framed_path"]
    monkeypatch.setattr(vp, "set_camera", lambda *args: 5.0)
    result, follow = vp.place(scene, stage, view.context, view.viewport,
                              {"path": requested, "follow": True, "eye": [5, 5, 5]}, lambda: None)
    assert result["requested_path"] == requested and result["path"] == "/World/Robot/base_link"
    assert result["framed_path"] == result["path"] == follow["path"]
    assert result["selection"] == "framed for this call; Stage selection unchanged"


@pytest.mark.parametrize("siblings", [1, 2])
def test_empty_scene_prim_does_not_frame_unrelated_geometry(scene, view, siblings):
    stage = scene.stage()
    UsdGeom.Xform.Define(stage, "/World/Environment/Sensor")
    for i in range(siblings):
        UsdGeom.Cube.Define(stage, f"/World/Environment/Object{i}")
    with pytest.raises(AssetError) as error:
        view.call("/World/Environment/Sensor")
    assert error.value.code == "NO_BOUNDS"
    assert error.value.details["candidates"] == [f"/World/Environment/Object{i}" for i in range(siblings)]
    assert not view.selected and view.calls == 0


def test_hidden_geometry_does_not_crowd_visible_candidates(scene, view, monkeypatch):
    from sim_tools import viewport as vp

    stage = scene.stage()
    UsdGeom.Xform.Define(stage, "/World/Environment/Sensor")
    hidden = UsdGeom.Xform.Define(stage, "/World/Environment/Hidden")
    hidden.GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    for i in range(25):
        UsdGeom.Cube.Define(stage, f"/World/Environment/Hidden/Cube{i:02}")
    UsdGeom.Cube.Define(stage, "/World/Environment/ZVisible")
    monkeypatch.setattr(vp, "CAP", 6)

    with pytest.raises(AssetError) as error:
        view.call("/World/Environment/Sensor")
    assert error.value.code == "NO_BOUNDS"
    assert error.value.details["candidates"] == ["/World/Environment/ZVisible"]
    assert error.value.details["candidates_truncated"] is False
    assert not view.selected and view.calls == 0



@pytest.mark.parametrize("time,visible", [(1, False), (2, True)])
def test_time_sampled_visibility_controls_framing_and_candidates(scene, view, time, visible):
    stage = scene.stage()
    UsdGeom.Xform.Define(stage, "/World/Environment/Sensor")
    animated = UsdGeom.Xform.Define(stage, "/World/Environment/Animated")
    animated.GetVisibilityAttr().Set(UsdGeom.Tokens.invisible, Usd.TimeCode(1))
    animated.GetVisibilityAttr().Set(UsdGeom.Tokens.inherited, Usd.TimeCode(2))
    cube = UsdGeom.Cube.Define(stage, "/World/Environment/Animated/Cube")
    UsdGeom.Cube.Define(stage, "/World/Environment/ZVisible")
    view.viewport.time = Usd.TimeCode(time)

    with pytest.raises(AssetError) as error:
        view.call("/World/Environment/Sensor")
    expected = ["/World/Environment/ZVisible"]
    if visible:
        expected.insert(0, str(cube.GetPath()))
    assert error.value.details["candidates"] == expected
    assert not view.selected and view.calls == 0

    if visible:
        result = view.call(str(cube.GetPath()))
        assert result["framed_path"] == str(cube.GetPath())
        assert view.selected == [str(cube.GetPath())] and view.calls == 1
    else:
        with pytest.raises(AssetError) as hidden_error:
            view.call(str(cube.GetPath()))
        assert hidden_error.value.code == "NO_BOUNDS"
        assert not view.selected and view.calls == 0


def test_direct_prim_below_invisible_ancestor_is_not_framed(scene, view):
    stage = scene.stage()
    hidden = UsdGeom.Xform.Define(stage, "/World/Environment/Hidden")
    hidden.GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    stage.DefinePrim("/World/Environment/Hidden/Scope", "Scope")
    UsdGeom.Cube.Define(stage, "/World/Environment/Hidden/Scope/Cube")

    with pytest.raises(AssetError) as error:
        view.call("/World/Environment/Hidden/Scope")
    assert error.value.code == "NO_BOUNDS"
    assert error.value.details["candidates"] == []
    assert not view.selected and view.calls == 0


def test_invisible_robot_base_link_is_not_selected(scene, view):
    stage = scene.stage()
    footprint = UsdGeom.Xform.Define(stage, "/World/Robot/base_footprint")
    UsdPhysics.RigidBodyAPI.Apply(footprint.GetPrim())
    base = UsdGeom.Xform.Define(stage, "/World/Robot/base_link")
    base.GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    UsdGeom.Cube.Define(stage, "/World/Robot/base_link/Visual")

    with pytest.raises(AssetError) as error:
        view.call("/World/Robot/base_footprint")
    assert error.value.code == "NO_BOUNDS"
    assert error.value.details["candidates"] == []
    assert not view.selected and view.calls == 0


def test_named_footprint_without_robot_physics_does_not_select_sibling(scene, view):
    stage = scene.stage()
    UsdGeom.Xform.Define(stage, "/World/Environment/base_footprint")
    UsdGeom.Cube.Define(stage, "/World/Environment/base_link")
    with pytest.raises(AssetError) as error:
        view.call("/World/Environment/base_footprint")
    assert error.value.code == "NO_BOUNDS"
    assert "/World/Environment/base_link" in error.value.details["candidates"]
    assert not view.selected and view.calls == 0


def test_small_robot_link_framed_before_oversized_parent_scan(scene, view, monkeypatch):
    from sim_tools import viewport as vp

    stage = scene.stage()
    robot = UsdGeom.Xform.Define(stage, "/World/Robot")
    UsdPhysics.ArticulationRootAPI.Apply(robot.GetPrim())
    UsdGeom.Xform.Define(stage, "/World/Robot/base_footprint")
    UsdGeom.Cube.Define(stage, "/World/Robot/base_link")
    for i in range(10):
        UsdGeom.Xform.Define(stage, f"/World/Robot/unrelated{i}")
    monkeypatch.setattr(vp, "CAP", 4)
    result = view.call("/World/Robot/base_footprint")
    assert result["framed_path"] == "/World/Robot/base_link"


def test_oversized_parent_returns_bounded_candidates(scene, view, monkeypatch):
    from sim_tools import viewport as vp

    stage = scene.stage()
    UsdGeom.Xform.Define(stage, "/World/Environment/Sensor")
    for i in range(10):
        UsdGeom.Cube.Define(stage, f"/World/Environment/Object{i}")
    monkeypatch.setattr(vp, "CAP", 4)
    with pytest.raises(AssetError) as error:
        view.call("/World/Environment/Sensor")
    assert error.value.code == "NO_BOUNDS"
    assert error.value.details["candidates"]
    assert error.value.details["candidates_truncated"] is True
    assert not view.selected and view.calls == 0


def test_absent_viewport_is_an_actionable_refusal(scene, view):
    with pytest.raises(AssetError) as error:
        frame(scene, scene.stage(), "/World/Box", view.context, None, None, None)
    assert error.value.code == "NO_VIEWPORT"


def test_read_only_session_refuses_without_touching_source(scene, view):
    stage = scene.stage()
    source = stage.GetRootLayer().ExportToString()
    stage.GetSessionLayer().SetPermissionToEdit(False)
    with pytest.raises(AssetError) as error:
        view.call()
    assert error.value.code == "READ_ONLY" and not view.selected
    assert stage.GetRootLayer().ExportToString() == source


def test_nonfinite_native_camera_result_is_not_success(scene, view):
    def native(_):
        view.camera.GetPrim().GetAttribute("xformOp:translate").Set(Gf.Vec3d(float("nan"), 0, 0))
        return True

    view.native = native
    with pytest.raises(AssetError) as error:
        view.call()
    assert error.value.code == "VIEWPORT_FRAME_FAILED"


def test_framing_puts_the_selection_back_so_captures_show_no_outline():
    from sim_tools import viewport as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert source.index("selection.set_selected_prim_paths([selected_path], False)") < source.index("frame_selection(viewport)") < source.index("selection.set_selected_prim_paths(previous, False)")

"""Lab 3 run 8: missing IDs, reopened scenes, and stage/timeline changes from outside the tools."""

import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/isaac.mcp.bridge"))
from isaac_mcp.project_assets import AssetError
from isaac_mcp.scene_contracts import REGISTRY
from pxr import Sdf, Usd, UsdGeom, UsdPhysics
from sim_tools.advanced import Advanced
from sim_tools.core import STAGE_TOOLS, TIMELINE_TOOLS, Scene, attribute_change


class Timeline:
    stopped = True

    def is_stopped(self):
        return self.stopped

    def is_playing(self):
        return not self.stopped

    def get_current_time(self):
        return 0.0


OUTSIDE = attribute_change({}, STAGE_TOOLS)


def refusal(call, *args, **kw):
    with pytest.raises(AssetError) as error:
        call(*args, **kw)
    return error.value


def source_file(tmp_path):
    """A stage whose content lives in the root layer of a file, as after sim.stage.open."""
    file = tmp_path / "lab.usda"
    stage = Usd.Stage.CreateNew(str(file))
    UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
    UsdGeom.Xform.Define(stage, "/World/Group")
    UsdGeom.Cube.Define(stage, "/World/Group/Box")
    UsdGeom.Camera.Define(stage, "/World/Cam")
    stage.DefinePrim("/World/Lidar", "OmniLidar")
    stage.DefinePrim("/World/Rig/Scan", "OmniLidar")
    stage.DefinePrim("/World/Pair/A", "OmniLidar")
    stage.DefinePrim("/World/Pair/B", "OmniLidar")
    arm = stage.DefinePrim("/World/Arm", "Xform")
    UsdPhysics.ArticulationRootAPI.Apply(arm)
    stage.DefinePrim("/OmniverseKit_Persp", "Camera")
    stage.GetRootLayer().Save()
    return stage, file


@pytest.fixture
def reopened(tmp_path):
    stage, file = source_file(tmp_path)
    return stage, file, Advanced(Scene(lambda: stage, Timeline(), output=tmp_path / "out"), None)


# --- Finding 4: missing IDs -------------------------------------------------------------------

@pytest.mark.parametrize("tool", ["sim.graph.node_types", "sim.logs.tail"])
def test_stage_independent_reads_do_not_require_ids_but_still_accept_them(tool):
    params = REGISTRY[tool]["parameters"]
    assert params["session_id"]["required"] is False and params["stage_id"]["required"] is False
    assert "Requires session_id" not in REGISTRY[tool]["description"]


def test_every_tool_that_needs_ids_says_where_to_get_them():
    targeted = [m for m in REGISTRY.values()
                if m["parameters"].get("session_id", {}).get("required")]
    assert len(targeted) > 30
    for meta in targeted:
        assert meta["description"].endswith("Requires session_id and stage_id from sim__stage__info.")
        assert "sim__stage__info" in meta["parameters"]["session_id"]["description"]


def test_node_types_needs_no_stage_and_keeps_validating(monkeypatch):
    from sim_tools import graph

    monkeypatch.setattr(graph, "node_types", lambda contains, limit, ports: dict(found=contains))
    scene = SimpleNamespace(stage=lambda: pytest.fail("a catalog read must not touch the stage"))
    tool = Advanced(scene, None)
    tool.close_sensors = lambda **kw: pytest.fail("a catalog read must not release sensors")
    assert tool.targetless("sim.graph.node_types", dict(contains="Clock")) == dict(found="Clock")
    assert refusal(tool.targetless, "sim.graph.node_types", {}).code == "INVALID_VALUE"
    assert refusal(tool.targetless, "sim.graph.node_types", dict(contains="x", limit=99)).code == "LIMIT"


def test_logs_tail_returns_scene_changes_and_tagged_lines(tmp_path, monkeypatch):
    log = tmp_path / "kit.log"
    log.write_text("x [Info] other noise\nx [Info] [sim-tools] timeline playing (source: t)\nx [Warning] w\n")
    settings = SimpleNamespace(get=lambda key: str(log))
    carb_settings = SimpleNamespace(get_settings=lambda: settings)
    monkeypatch.setitem(sys.modules, "carb", SimpleNamespace(settings=carb_settings))
    monkeypatch.setitem(sys.modules, "carb.settings", carb_settings)
    scene = Scene(lambda: None, Timeline(), output=tmp_path)
    scene.record_event("stage", "stage opened", OUTSIDE, "/home/x/lab.usda")
    out = Advanced(scene, None).targetless("sim.logs.tail", {})
    assert out["lines"] == ["x [Info] [sim-tools] timeline playing (source: t)", "x [Warning] w"]
    assert out["scene_events"][0]["stage"] == "/home/x/lab.usda"
    assert "monotonic" not in out["scene_events"][0]


def test_missing_ids_name_the_call_to_make_and_do_nothing(reopened):
    _, _, tool = reopened
    for ids in ((None, None), (tool.s.session_id, None), (None, "x")):
        error = refusal(tool.s.target, *ids, mutation=True)
        assert error.code == "MISSING_TARGET" and "sim__stage__info" in str(error)
        assert error.details["current_target"]["stage_id"] == tool.s.stage_id


def test_mcp_layer_explains_missing_ids_without_changing_the_published_schema():
    from isaac_mcp import server
    from pydantic import create_model

    required = create_model("A", session_id=(str, ...), stage_id=(str, ...))
    optional = create_model("B", session_id=(str | None, None), stage_id=(str | None, None), contains=(str, ...))
    server._explain_missing_target(required)
    server._explain_missing_target(optional)
    with pytest.raises(ValueError, match="call sim__stage__info first"):
        required.model_validate({"stage_id": "s"})
    assert required.model_validate(dict(session_id="a", stage_id="b")).stage_id == "b"
    assert sorted(required.model_json_schema()["required"]) == ["session_id", "stage_id"]
    assert optional.model_validate(dict(contains="x")).contains == "x"


# --- Finding 6: reopened scenes ---------------------------------------------------------------

def layers(stage):
    return stage.GetRootLayer().ExportToString()


def test_foreign_delete_needs_the_opt_in_and_names_it(reopened):
    stage, _, tool = reopened
    error = refusal(tool.execute, "sim.prim.delete", stage, dict(path="/World/Group/Box", confirm=True))
    assert error.code == "NOT_OWNED"
    assert error.details["retry_with"] == {"allow_foreign": True} and "allow_foreign=true" in str(error)
    assert stage.GetPrimAtPath("/World/Group/Box").IsActive()
    assert refusal(tool.execute, "sim.prim.delete", stage, dict(
        path="/World/Group/Box", confirm=True, allow_foreign="yes")).code == "INVALID_VALUE"
    assert refusal(tool.execute, "sim.prim.delete", stage, dict(
        path="/World/Group/Box", confirm=False, allow_foreign=True)).code == "CONFIRM_REQUIRED"


def test_foreign_delete_deactivates_in_session_layer_and_leaves_the_source_alone(reopened):
    stage, file, tool = reopened
    root_before, disk_before = layers(stage), file.read_bytes()
    out = tool.execute("sim.prim.delete", stage, dict(path="/World/Group/Box", confirm=True, allow_foreign=True))
    assert out["method"] == "deactivated_in_session_layer" and out["still_composed"] is True
    assert out["source_files_changed"] is False and "new path" in out["note"]
    prim = stage.GetPrimAtPath("/World/Group/Box")
    assert prim and not prim.IsActive()
    assert layers(stage) == root_before and file.read_bytes() == disk_before and not stage.GetRootLayer().dirty
    assert stage.GetSessionLayer().GetPrimAtPath("/World/Group/Box").active is False
    # Repeating is harmless and honest.
    assert tool.execute("sim.prim.delete", stage, dict(
        path="/World/Group/Box", confirm=True, allow_foreign=True))["already_inactive"] is True
    # The path stays taken, and the refusal says so.
    error = refusal(tool.s.create, stage, "/World/Group/Box", "Cube")
    assert error.code == "CONFLICT" and "new path" in str(error) and "already exists" in str(error)
    # A saved copy omits it, which is the documented way to free the path.
    assert not Usd.Stage.Open(stage.Flatten()).GetPrimAtPath("/World/Group/Box")


def test_foreign_session_only_prim_is_really_removed(reopened):
    stage, _, tool = reopened
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        stage.DefinePrim("/World/Scratch", "Cube")  # e.g. by run_python
    out = tool.execute("sim.prim.delete", stage, dict(path="/World/Scratch", confirm=True, allow_foreign=True))
    assert out["method"] == "removed_from_session_layer" and out["still_composed"] is False


@pytest.mark.parametrize("path,reason", [
    ("/World", "protected"),
    ("/World/Arm", "articulation"),
    ("/World/Pair", "sensor"),
    ("/OmniverseKit_Persp", "Kit"),
])
def test_opt_in_does_not_weaken_the_other_guards(reopened, path, reason):
    stage, _, tool = reopened
    before = stage.GetSessionLayer().ExportToString()
    error = refusal(tool.execute, "sim.prim.delete", stage, dict(path=path, confirm=True, allow_foreign=True))
    assert error.code == "PROTECTED_PRIM" and reason in str(error)
    assert stage.GetSessionLayer().ExportToString() == before


def test_opt_in_refuses_graph_nodes_and_subtrees_with_registered_sensors(reopened):
    stage, _, tool = reopened
    stage.DefinePrim("/World/G", "OmniGraph")
    stage.DefinePrim("/World/G/Node", "OmniGraphNode")
    assert refusal(tool.execute, "sim.prim.delete", stage, dict(
        path="/World/G/Node", confirm=True, allow_foreign=True)).code == "PROTECTED_PRIM"
    tool.sensors["/World/Group/Box"] = ("camera", dict(product=None, annotator=None))
    assert "registered sensor" in str(refusal(tool.execute, "sim.prim.delete", stage, dict(
        path="/World/Group", confirm=True, allow_foreign=True)))


def test_deleting_an_adopted_sensor_releases_it_and_stays_guarded(reopened):
    stage, _, tool = reopened
    released = []
    product = SimpleNamespace(destroy=lambda: released.append("product"))
    annotator = SimpleNamespace(detach=lambda: released.append("annotator"))
    tool.sensors["/World/Lidar"] = ("lidar", dict(
        adopted=True, sensor_path="/World/Lidar", product=product, annotator=annotator, stage=None))
    assert refusal(tool.execute, "sim.prim.delete", stage, dict(path="/World/Lidar", confirm=True)).code == "NOT_OWNED"
    assert not released and "/World/Lidar" in tool.sensors
    out = tool.execute("sim.prim.delete", stage, dict(path="/World/Lidar", confirm=True, allow_foreign=True))
    assert released == ["annotator", "product"] and "/World/Lidar" not in tool.sensors
    assert out["method"] == "deactivated_in_session_layer"


def fake_replicator(monkeypatch, stage):
    from sim_tools import sensors

    calls = []

    class Annotator:
        def attach(self, products):
            calls.append(("attach", products[0].path))

        def detach(self):
            calls.append("detach")

    def render_product(path, resolution, force_new, name, write_to_session_layer=None):
        calls.append(("product", path, resolution))
        return SimpleNamespace(path="/Render/" + name, destroy=lambda: calls.append("destroy"))

    rep = SimpleNamespace(
        create=SimpleNamespace(render_product=render_product),
        AnnotatorRegistry=SimpleNamespace(get_annotator=lambda name, **kw: calls.append(("annotator", name)) or Annotator()),
    )
    monkeypatch.setattr(sensors, "require_sensor_api", lambda lidar=False: (rep, dict(release="6.2", build="b")))
    usd = SimpleNamespace(get_context=lambda: SimpleNamespace(get_stage=lambda: stage))
    monkeypatch.setitem(sys.modules, "omni", SimpleNamespace(usd=usd))
    monkeypatch.setitem(sys.modules, "omni.usd", usd)
    return calls


def lidar_attrs(stage, path, frame="SENSOR", accumulate=True):
    prim = stage.GetPrimAtPath(path)
    prim.CreateAttribute("omni:sensor:Core:outputFrameOfReference", Sdf.ValueTypeNames.Token).Set(frame)
    prim.CreateAttribute("omni:sensor:Core:scanRateBaseHz", Sdf.ValueTypeNames.Float).Set(10.0)
    prim.CreateAttribute("omni:sensor:tickRate", Sdf.ValueTypeNames.Float).Set(10.0)
    prim.CreateAttribute("omni:sensor:Core:accumulateOutputs", Sdf.ValueTypeNames.Bool).Set(accumulate)


def test_adopting_a_lidar_registers_owned_resources_without_ownership(reopened, monkeypatch):
    stage, file, tool = reopened
    lidar_attrs(stage, "/World/Lidar")
    stage.GetRootLayer().Save()
    root_before, disk_before = layers(stage), file.read_bytes()
    calls = fake_replicator(monkeypatch, stage)
    kind, sensor = tool.adopt(stage, "/World/Lidar", "lidar")
    assert kind == "lidar" and sensor["adopted"] is True and sensor["session_overrides"] == []
    assert sensor["annotator"] is not None and sensor["adapter"] == "replicator-owned-cpu-v1"
    assert ("annotator", "IsaacExtractRTXSensorPointCloudNoAccumulator") in calls
    assert tool.sensors["/World/Lidar"][1] is sensor
    assert "/World/Lidar" not in tool.s.owned  # deleting it still needs allow_foreign
    assert layers(stage) == root_before and file.read_bytes() == disk_before


def test_adopting_a_lidar_reports_session_overrides_and_never_edits_the_source(reopened, monkeypatch):
    stage, file, tool = reopened
    lidar_attrs(stage, "/World/Lidar", frame="WORLD", accumulate=False)
    stage.GetRootLayer().Save()
    root_before, disk_before = layers(stage), file.read_bytes()
    fake_replicator(monkeypatch, stage)
    _, sensor = tool.adopt(stage, "/World/Lidar", "lidar")
    assert {o["property"]: o["previous"] for o in sensor["session_overrides"]} == {
        "omni:sensor:Core:outputFrameOfReference": "WORLD", "omni:sensor:Core:accumulateOutputs": False}
    assert stage.GetPrimAtPath("/World/Lidar").GetAttribute("omni:sensor:Core:outputFrameOfReference").Get() == "SENSOR"
    assert layers(stage) == root_before and file.read_bytes() == disk_before
    json.dumps(sensor["session_overrides"])  # travels in a tool result


def test_failed_adoption_clears_its_overrides_and_registers_nothing(reopened, monkeypatch):
    from sim_tools import sensors

    stage, _, tool = reopened
    lidar_attrs(stage, "/World/Lidar", frame="WORLD")
    fake_replicator(monkeypatch, stage)
    monkeypatch.setattr(sensors, "_attach", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    error = refusal(tool.adopt, stage, "/World/Lidar", "lidar")
    assert error.code == "SENSOR_CREATE_FAILED" and not tool.sensors
    assert stage.GetPrimAtPath("/World/Lidar").GetAttribute("omni:sensor:Core:outputFrameOfReference").Get() == "WORLD"


def test_adopting_a_camera_keeps_its_aspect_and_does_not_edit_it(reopened, monkeypatch):
    stage, _, tool = reopened
    camera = UsdGeom.Camera(stage.GetPrimAtPath("/World/Cam"))
    camera.GetHorizontalApertureAttr().Set(20.0)
    camera.GetVerticalApertureAttr().Set(10.0)
    session_before = stage.GetSessionLayer().ExportToString()
    calls = fake_replicator(monkeypatch, stage)
    _, sensor = tool.adopt(stage, "/World/Cam", "camera")
    assert sensor["resolution"] == (320, 640) and ("product", "/World/Cam", (640, 320)) in calls
    assert sensor.get("annotator") is None
    assert stage.GetSessionLayer().ExportToString() == session_before


@pytest.mark.parametrize("path,kind,code", [
    ("/World/Group/Box", "lidar", "NOT_A_SENSOR"),
    ("/World/Lidar", "camera", "NOT_A_SENSOR"),
    ("/World/Pair", "lidar", "NOT_A_SENSOR"),  # ambiguous: two LiDARs below
    ("/World/Missing", "lidar", "NOT_FOUND"),
])
def test_adoption_only_accepts_the_sensor_at_that_path(reopened, monkeypatch, path, kind, code):
    stage, _, tool = reopened
    fake_replicator(monkeypatch, stage)
    assert refusal(tool.adopt, stage, path, kind).code == code
    assert not tool.sensors


def test_adoption_resolves_a_config_wrapper_and_respects_the_sensor_limit(reopened, monkeypatch):
    stage, _, tool = reopened
    lidar_attrs(stage, "/World/Rig/Scan")
    fake_replicator(monkeypatch, stage)
    _, sensor = tool.adopt(stage, "/World/Rig", "lidar")
    assert sensor["sensor_path"] == "/World/Rig/Scan"
    tool.sensors.update({f"/x{i}": ("camera", {}) for i in range(7)})
    assert refusal(tool.adopt, stage, "/World/Cam", "camera").code == "LIMIT"


def test_adoption_refuses_an_inactive_sensor(reopened, monkeypatch):
    stage, _, tool = reopened
    fake_replicator(monkeypatch, stage)
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        stage.GetPrimAtPath("/World/Lidar").SetActive(False)
    assert refusal(tool.adopt, stage, "/World/Lidar", "lidar").code == "SENSOR_INVALIDATED"


def test_a_lidar_read_refused_for_being_stopped_adopts_nothing(reopened, monkeypatch):
    # extension.py needs Kit, so run its real method against stand-ins for the Kit modules.
    import types

    omni = types.ModuleType("omni")
    monkeypatch.setitem(sys.modules, "omni", omni)
    for name in ("ext", "usd", "timeline"):
        module = types.ModuleType("omni." + name)
        setattr(omni, name, module)
        monkeypatch.setitem(sys.modules, "omni." + name, module)
    omni.ext.IExt = object
    bridge_module = types.ModuleType("isaac_mcp_bridge")
    bridge_module.get_mcp_bridge = bridge_module.ToolDefinition = None
    monkeypatch.setitem(sys.modules, "isaac_mcp_bridge", bridge_module)
    monkeypatch.delitem(sys.modules, "sim_tools.extension", raising=False)
    from sim_tools.extension import Extension

    sys.modules.pop("sim_tools.extension")  # built on stand-ins: not for other tests
    stage, _, tool = reopened
    tool.adopt = lambda *a: pytest.fail("adoption must follow the checks")
    host = SimpleNamespace(scene=SimpleNamespace(
        require_runtime=lambda: None, target=lambda *a: stage, stage=lambda: stage,
        timeline=SimpleNamespace(is_playing=lambda: False)), advanced=tool)
    args = dict(session_id="s", stage_id="t", path="/World/Lidar", adopt=True, wait_for_render=True)
    with pytest.raises(AssetError) as error:
        asyncio.run(Extension._wait_sensor_frame(host, "sim.lidar.read", args))
    assert error.value.code == "SENSOR_PAUSED" and not tool.sensors


def test_contracts_make_both_routes_optional_opt_ins():
    for tool, name in (("sim.prim.delete", "allow_foreign"), ("sim.lidar.read", "adopt"), ("sim.camera.capture", "adopt")):
        spec = REGISTRY[tool]["parameters"][name]
        assert spec["required"] is False and spec["default"] is False
        assert name in REGISTRY[tool]["description"]


# --- Finding 7 and 9: who changed the stage or timeline ---------------------------------------

def test_attribution_only_credits_or_blames_when_it_can_tell():
    assert attribute_change(None, STAGE_TOOLS)["origin"] == "unknown"
    assert attribute_change({"sim.stage.open": True}, STAGE_TOOLS) == dict(origin="tool", source="sim__stage__open")
    assert attribute_change({"sim.timeline.control": True}, TIMELINE_TOOLS)["source"] == "sim__timeline__control"
    assert attribute_change({"sim.map.occupancy": True}, TIMELINE_TOOLS)["origin"] == "tool"
    # Evidence from a live run: a run_python new_stage one second after sim__stage__open.
    recent = attribute_change({"sim.stage.open": False, "run_python": True}, STAGE_TOOLS)
    assert recent["origin"] == "unattributed" and "run_python" in recent["source"]
    assert attribute_change({"sim.stage.open": False}, STAGE_TOOLS)["origin"] == "unattributed"
    for tool in ("run_python", "ur5e_dt.new_stage", "sim.ui.act"):
        assert attribute_change({tool: True}, STAGE_TOOLS)["origin"] == "unattributed"
    # Tools that cannot change a stage do not hide a person in the GUI.
    for tool in ("status", "sim.stage.info", "sim.logs.tail", "assets.search", "get_file"):
        assert attribute_change({tool: True}, STAGE_TOOLS)["origin"] == "outside_tools"
    assert attribute_change({"sim.stage.open": True}, TIMELINE_TOOLS)["origin"] == "outside_tools"


def swap(scene, attribution):
    old = scene.stage_info()
    new = Usd.Stage.CreateInMemory("next.usda")
    scene.record_event("stage", "stage closing", attribution, old["source"])
    scene.close_stage()
    scene.get_stage = lambda: new
    return old, new


@pytest.fixture
def scene(tmp_path):
    stage = Usd.Stage.CreateInMemory()
    return Scene(lambda: stage, Timeline(), output=tmp_path)


def test_stale_target_says_outside_only_when_that_is_what_happened(scene):
    old, new = swap(scene, OUTSIDE)
    error = refusal(scene.target, old["session_id"], old["stage_id"])
    assert error.code == "STALE_TARGET" and "outside this session's tools" in str(error)
    assert new.GetRootLayer().identifier in str(error) and error.details["stage_change"]["origin"] == "outside_tools"
    assert "monotonic" not in error.details["stage_change"]
    assert error.details["current_target"]["source"] == new.GetRootLayer().identifier


def test_stale_target_credits_the_tool_and_does_not_claim_outside(scene):
    old, _ = swap(scene, attribute_change({"sim.stage.open": True}, STAGE_TOOLS))
    text = str(refusal(scene.target, old["session_id"], old["stage_id"]))
    assert "by sim__stage__open" in text and "outside" not in text


def test_stale_target_with_a_running_tool_says_it_cannot_tell(scene):
    old, _ = swap(scene, attribute_change({"run_python": True}, STAGE_TOOLS))
    text = str(refusal(scene.target, old["session_id"], old["stage_id"]))
    assert "could not tell" in text and "run_python" in text and "outside this session" not in text


def test_stale_target_without_a_recorded_change_stays_generic(scene):
    old = scene.stage_info()
    scene.get_stage = lambda: Usd.Stage.CreateInMemory()
    error = refusal(scene.target, old["session_id"], old["stage_id"])
    assert "outside" not in str(error) and "stage_change" not in error.details
    unknown = refusal(scene.target, scene.session_id, "never-issued")
    assert "outside" not in str(unknown) and "changed" in str(unknown)


def test_stale_session_id_is_not_blamed_on_a_stage_change(scene):
    old = scene.stage_info()
    text = str(refusal(scene.target, "other-process", old["stage_id"]))
    assert "Kit restarted" in text and "outside" not in text


def test_event_history_and_retired_ids_stay_bounded(scene):
    for index in range(100):
        scene.record_event("timeline", "timeline playing", OUTSIDE)
        scene.stage_id = f"s{index}"
        scene.retire_stage()
    assert len(scene.events) == 32 and len(scene._retired_stages) == 16


def test_mutation_while_playing_names_the_stop_call_for_every_mutation(scene):
    info = scene.stage_info()
    scene.timeline.stopped = False
    error = refusal(scene.target, info["session_id"], info["stage_id"], mutation=True)
    assert error.code == "TIMELINE_ACTIVE" and "sim__timeline__control action=stop" in str(error)
    assert "start pose" in str(error)
    assert error.details["before_retry"] == dict(
        tool="sim__timeline__control",
        arguments=dict(session_id=info["session_id"], stage_id=info["stage_id"], action="stop"))
    assert "retry_with" not in error.details


def test_timeline_already_active_says_how_to_continue_and_who_started_it(scene):
    stage = scene.stage()
    scene.record_event("timeline", "timeline playing", OUTSIDE)
    scene.timeline.stopped = False
    error = refusal(scene.begin_runtime_edit, stage)
    assert error.code == "TIMELINE_ALREADY_ACTIVE"
    assert "action=stop, then action=play" in str(error) and "outside the MCP tools" in str(error)
    assert error.details["before_retry"]["arguments"]["action"] == "stop"
    assert scene._runtime_edit_target is None


def test_bridge_reports_running_and_just_finished_tools():
    from isaac_mcp_bridge.bridge import McpBridge, ToolDefinition

    bridge = McpBridge(schedule_coroutine=lambda coro: None)
    seen = []
    registrar = bridge.register_owner("t", "t", display_name="t")

    async def handler():
        seen.append(bridge.tool_activity())
        return {}

    registrar.add(ToolDefinition(name="work", description="d", parameters={}, handler=handler))
    assert bridge.tool_activity() == {}
    asyncio.run(bridge.dispatch("t.work", {}))
    assert seen == [{"t.work": True}]
    assert bridge.tool_activity() == {"t.work": False}
    assert bridge.tool_activity(recent_s=0) == {}

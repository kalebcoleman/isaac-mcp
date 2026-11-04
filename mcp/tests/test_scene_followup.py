"""M1-M3 pure logic: world bounds, cameras, log parsing/cursors, PhysX query shapes.

These run without Kit. Live behavior is covered by the milestone run records.
"""

import ast
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))

from pxr import Usd, UsdGeom  # noqa: E402
from sim_tools import geometry, logs, queries  # noqa: E402
from sim_tools.core import Scene  # noqa: E402
from isaac_mcp.project_assets import AssetError  # noqa: E402
from test_scene_usd import scene  # noqa: E402,F401


def refusal(call, *args, **kwargs):
    with pytest.raises(AssetError) as error:
        call(*args, **kwargs)
    return error.value


# --- M1: bounds, measure, camera list -------------------------------------------------------


def test_bounds_match_bboxcache_to_1e_6_and_measure_hand_calculation(scene):
    stage = scene.stage()
    scene.create(stage, "/World/A", "Cube", size=2)
    scene.create(stage, "/World/B", "Cube", size=2, position=[4, 0, 0])
    result = geometry.world_bound(stage, "/World/A")
    direct = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]
    ).ComputeWorldBound(stage.GetPrimAtPath("/World/A")).ComputeAlignedRange()
    assert result["empty"] is False
    assert result["axis_aligned"]["min"] == pytest.approx(list(direct.GetMin()), abs=1e-6)
    assert result["axis_aligned"]["max"] == pytest.approx(list(direct.GetMax()), abs=1e-6)
    assert result["axis_aligned"]["size"] == pytest.approx([2, 2, 2])
    assert len(result["oriented"]["corners"]) == 8
    measured = geometry.measure(stage, "/World/A", "/World/B")
    assert measured["gap"] == pytest.approx(2.0)
    assert measured["centre_distance"] == pytest.approx(4.0)
    assert measured["overlapping"] is False
    assert measured["axis_gap"] == pytest.approx([2.0, 0.0, 0.0])


def test_measure_overlap_is_zero_and_empty_bounds_refuse(scene):
    stage = scene.stage()
    scene.create(stage, "/World/A", "Cube", size=2)
    scene.create(stage, "/World/B", "Cube", size=2, position=[1, 0, 0])
    assert geometry.measure(stage, "/World/A", "/World/B")["gap"] == 0
    assert geometry.measure(stage, "/World/A", "/World/B")["overlapping"] is True
    UsdGeom.Xform.Define(stage, "/World/Empty")
    assert geometry.world_bound(stage, "/World/Empty")["empty"] is True
    error = refusal(geometry.measure, stage, "/World/A", "/World/Empty")
    assert error.code == "NO_BOUNDS" and error.details["argument"] == "path_b"


def test_bounds_refuse_unknown_path_and_purpose(scene):
    assert refusal(geometry.world_bound, scene.stage(), "/World/Missing").code == "NOT_FOUND"
    assert refusal(geometry.world_bound, scene.stage(), "/World", "shadow").code == "INVALID_VALUE"


def test_camera_list_reports_optics_pose_and_active_viewport(scene):
    stage = scene.stage()
    camera = UsdGeom.Camera.Define(stage, "/World/Cam")
    camera.CreateFocalLengthAttr(35)
    camera.CreateClippingRangeAttr((0.2, 50))
    camera.CreateProjectionAttr("perspective")
    result = geometry.cameras(
        stage, "/", 10, active_camera="/World/Cam", viewport_available=True
    )
    assert result["viewport_available"] is True and result["active_camera"] == "/World/Cam"
    assert result["truncated"] is False
    entry = result["cameras"][0]
    assert entry["path"] == "/World/Cam" and entry["active_viewport_camera"] is True
    assert entry["focal_length_mm"] == pytest.approx(35)
    assert entry["clipping_range_stage_units"] == pytest.approx([0.2, 50])
    assert entry["clipping_range_m"] == pytest.approx([0.2, 50])
    assert entry["world_orientation_wxyz"] == pytest.approx([1, 0, 0, 0])
    assert len(result["cameras"]) == 1


def test_camera_list_bounds_limit_and_no_viewport(scene):
    stage = scene.stage()
    for index in range(3):
        UsdGeom.Camera.Define(stage, f"/World/Cam{index}")
    result = geometry.cameras(stage, "/", 2, active_camera=None, viewport_available=False)
    assert len(result["cameras"]) == 2 and result["truncated"] is True
    assert result["viewport_available"] is False and result["active_camera"] is None
    assert refusal(geometry.cameras, stage, "/", 0, None, False).code == "LIMIT"
    assert refusal(geometry.cameras, stage, "/Nope", 1, None, False).code == "NOT_FOUND"


# --- M2: logs ------------------------------------------------------------------------------


def _log(tmp_path, monkeypatch, text):
    path = tmp_path / "kit.log"
    path.write_text(text)
    settings = SimpleNamespace(get=lambda key: str(path))
    carb_settings = SimpleNamespace(get_settings=lambda: settings)
    monkeypatch.setitem(sys.modules, "carb", SimpleNamespace(settings=carb_settings))
    monkeypatch.setitem(sys.modules, "carb.settings", carb_settings)
    return path


def _line(level, channel, message, timestamp="2026-11-04T00:00:00Z", ms=0):
    return f"{timestamp} [{ms}ms] [{level}] [{channel}] {message}"


def test_default_tail_reads_one_mib_so_warnings_survive_info_flood(tmp_path, monkeypatch):
    text = "\n".join([
        _line("Warning", "omni.physx", "first warning"),
        _line("Warning", "omni.physx", "second warning"),
        _line("Error", "omni.physx", "the error"),
    ])
    text += "\n" + "\n".join(
        _line("Info", "omni.kit.app", f"filler {index}") for index in range(4000)
    )
    assert len(text.encode()) > 64 * 1024
    _log(tmp_path, monkeypatch, text)
    result = logs.tail({"limit": 10, "scene_events": []})
    assert result["lines"][-3:] == [
        _line("Warning", "omni.physx", "first warning"),
        _line("Warning", "omni.physx", "second warning"),
        _line("Error", "omni.physx", "the error"),
    ]
    assert "last 1 MiB" in result["scope"]
    assert set(result) == {"file", "lines", "scope", "scene_events"}


def test_structured_tail_filters_by_level_channel_and_text(tmp_path, monkeypatch):
    _log(tmp_path, monkeypatch, "\n".join([
        _line("Info", "omni.kit.app", "starting"),
        _line("Warning", "omni.physx", "contact lost"),
        _line("Error", "omni.physx", "solver failed"),
        _line("Warning", "omni.usd", "unexpected reference count"),
        _line("Warning", "", "empty channel probe"),
    ]))
    everything = logs.tail({
        "limit": 10, "min_level": "info", "scene_events": [],
    })
    assert [entry["level"] for entry in everything["entries"]] == [
        "info", "warning", "error", "warning", "warning"
    ]
    assert everything["entries"][-1]["channel"] == ""
    assert everything["next_cursor"]["offset"] == os.stat(everything["file"]).st_size
    errors = logs.tail({"limit": 10, "min_level": "error", "scene_events": []})
    assert [entry["message"] for entry in errors["entries"]] == ["solver failed"]
    physx = logs.tail({"limit": 10, "min_level": "warning", "channel": "PHYSX", "scene_events": []})
    assert [entry["message"] for entry in physx["entries"]] == ["contact lost", "solver failed"]
    contains = logs.tail({"limit": 10, "min_level": "info", "contains": "REFERENCE", "scene_events": []})
    assert [entry["message"] for entry in contains["entries"]] == ["unexpected reference count"]


def test_mark_tail_cursor_and_summary_only_count_later_lines(tmp_path, monkeypatch):
    path = _log(tmp_path, monkeypatch, _line("Warning", "omni.physx", "before") + "\n")
    marked = logs.mark()
    assert marked["cursor"]["offset"] == path.stat().st_size
    with path.open("a") as stream:
        stream.write(_line("Warning", "omni.physx", "probe warning") + "\n")
        stream.write(_line("Error", "omni.physx", "probe error") + "\n")
        stream.write(_line("Info", "omni.kit.app", "after info") + "\n")
    tail = logs.tail({"limit": 10, "min_level": "debug", "cursor": marked["cursor"], "scene_events": []})
    assert [entry["message"] for entry in tail["entries"]] == [
        "probe warning", "probe error", "after info"
    ]
    assert tail["next_cursor"]["offset"] == path.stat().st_size
    summary = logs.summary({"cursor": marked["cursor"]})
    assert summary["entries"] == 3
    assert summary["counts"] == {"debug": 0, "info": 1, "warning": 1, "error": 1, "fatal": 0}
    assert summary["first_error"]["message"] == "probe error"
    assert summary["last_error"]["message"] == "probe error"
    assert summary["channels"][0] == {"channel": "omni.physx", "count": 2}


def test_cursor_detects_rotation_and_rejects_bad_shape(tmp_path, monkeypatch):
    path = _log(tmp_path, monkeypatch, _line("Info", "carb", "one") + "\n")
    cursor = logs.mark()["cursor"]
    path.write_text("")
    error = refusal(logs.tail, {"cursor": cursor, "scene_events": []})
    assert error.code == "LOG_ROTATED" and error.details["current_size"] == 0
    assert refusal(logs.summary, {"cursor": {"offset": 1}}).code == "INVALID_CURSOR"
    assert refusal(logs.tail, {"cursor": "nope", "scene_events": []}).code == "INVALID_CURSOR"


def test_cursor_read_is_capped_and_reports_more(tmp_path, monkeypatch):
    path = _log(tmp_path, monkeypatch, "")
    with path.open("w") as stream:
        for index in range(30000):
            stream.write(_line("Info", "omni.kit.app", f"line {index}") + "\n")
    cursor = logs.mark()["cursor"]
    cursor["offset"] = 0
    result = logs.tail({"limit": 100, "min_level": "info", "cursor": cursor, "scene_events": []})
    assert result["truncated"] is True
    assert result["next_cursor"]["offset"] < path.stat().st_size
    # A second call continues instead of returning the same lines.
    second = logs.tail({"limit": 100, "min_level": "info", "cursor": result["next_cursor"], "scene_events": []})
    assert second["entries"][0]["offset"] > result["entries"][0]["offset"]


def test_messages_are_capped_and_secrets_redacted(tmp_path, monkeypatch):
    _log(tmp_path, monkeypatch, "\n".join([
        _line("Error", "omni.physx", "token=abc123 " + "x" * 3000),
        _line("Warning", "omni.physx", "Authorization: Bearer sk-secret"),
    ]))
    result = logs.tail({"limit": 10, "min_level": "warning", "scene_events": []})
    assert len(result["entries"][0]["message"]) == logs.MESSAGE_CAP
    assert result["entries"][0]["message_truncated"] is True
    assert "abc123" not in result["entries"][0]["message"]
    assert "sk-secret" not in result["entries"][1]["message"]
    assert "<redacted>" in result["entries"][1]["message"]


def test_pack_installs_no_log_hook_from_s13():
    """The S13 deadlock came from installing log consumers, not from writing log lines."""
    forbidden = {
        "omni.log",
        "carb.logging",
        "get_log_event_stream",
        "error_log",
        "add_log_callback",
        "subscribe_log",
        "ILog",
    }
    used = set()
    for source in (Path(__file__).resolve().parents[2] / "exts/sim-tools/sim_tools").glob("*.py"):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                parts, cursor = [], node
                while isinstance(cursor, ast.Attribute):
                    parts.append(cursor.attr)
                    cursor = cursor.value
                if isinstance(cursor, ast.Name):
                    parts.append(cursor.id)
                dotted = ".".join(reversed(parts))
                for name in forbidden:
                    if dotted == name or dotted.endswith("." + name):
                        used.add(f"{source.name}:{dotted}")
            elif isinstance(node, ast.Name) and node.id in forbidden:
                used.add(f"{source.name}:{node.id}")
    assert not used, f"log hook(s) would deadlock Kit: {sorted(used)}"


# --- M3: raycast and contacts --------------------------------------------------------------


class _Stopped:
    def is_stopped(self):
        return True

    def is_playing(self):
        return False


def _fake_physx(monkeypatch, interface):
    module = SimpleNamespace(
        get_physx_scene_query_interface=lambda: interface,
        get_physx_simulation_interface=lambda: interface,
    )
    parent = SimpleNamespace(physx=module)
    monkeypatch.setitem(sys.modules, "omni", parent)
    monkeypatch.setitem(sys.modules, "omni.physx", module)
    return module


def test_raycast_normalises_and_returns_closest(monkeypatch):
    interface = SimpleNamespace(raycast_closest=lambda origin, direction, distance: dict(
        hit=True, position=(0, 0, 1), normal=(0, 0, 1), distance=1.5,
        collision="/World/Cube", rigidBody="/World/Cube", faceIndex=2, material="",
    ))
    _fake_physx(monkeypatch, interface)
    monkeypatch.setattr(queries, "_engine", lambda: SimpleNamespace(get_active_physics_engine=lambda: "physx"))
    result = queries.raycast([0, 0, 3], [0, 0, -4], 10)
    assert result["scene_ready"] is True
    assert result["direction"] == pytest.approx([0, 0, -1]) and result["normalized"] is True
    assert result["hits"][0]["distance"] == 1.5
    assert result["hits"][0]["collider"] == "/World/Cube"
    assert result["hits"][0]["material"] is None


def test_raycast_all_sorts_and_caps(monkeypatch):
    hits = [
        SimpleNamespace(position=(0, 0, 1), normal=(0, 0, 1), distance=2.0, collision="/World/Ground",
                        rigid_body="", face_index=0, material=""),
        SimpleNamespace(position=(0, 0, 2), normal=(0, 0, 1), distance=0.5, collision="/World/Cube",
                        rigid_body="/World/Cube", face_index=1, material=""),
    ]

    def raycast_all(origin, direction, distance, report):
        for hit in hits:
            if not report(hit):
                break
        return True

    _fake_physx(monkeypatch, SimpleNamespace(raycast_all=raycast_all))
    monkeypatch.setattr(queries, "_engine", lambda: SimpleNamespace(get_active_physics_engine=lambda: "physx"))
    result = queries.raycast([0, 0, 3], [0, 0, -1], 10, all_hits=True)
    assert [hit["distance"] for hit in result["hits"]] == [0.5, 2.0]
    assert result["hits"][1]["rigid_body"] is None


def test_raycast_before_physics_is_not_ready_and_never_queries(monkeypatch):
    def fail(*args):
        pytest.fail("a stopped timeline must not query PhysX")

    _fake_physx(monkeypatch, SimpleNamespace(raycast_closest=fail))
    monkeypatch.setattr(queries, "_engine", lambda: SimpleNamespace(get_active_physics_engine=lambda: "physx"))
    result = queries.raycast([0, 0, 3], [0, 0, -1], 10, timeline=_Stopped())
    assert result["scene_ready"] is False and result["hits"] == []


@pytest.mark.parametrize("call, code", [
    (lambda: queries.raycast([0, 0, 3], [0, 0, 0]), "INVALID_VALUE"),
    (lambda: queries.raycast([0, 0, 3], [0, 0, -1], 101), "LIMIT"),
    (lambda: queries.raycast([0, 0, 3], [0, 0, -1], True), "LIMIT"),
    (lambda: queries.contacts([], None), "LIMIT"),
    (lambda: queries.contacts(["relative"], None), "INVALID_VALUE"),
])
def test_query_argument_refusals(monkeypatch, call, code):
    monkeypatch.setattr(queries, "_engine", lambda: SimpleNamespace(get_active_physics_engine=lambda: "physx"))
    assert refusal(call).code == code


def test_newton_refuses_with_stable_codes(monkeypatch):
    monkeypatch.setattr(queries, "_engine", lambda: SimpleNamespace(get_active_physics_engine=lambda: "newton"))
    assert refusal(queries.raycast, [0, 0, 3], [0, 0, -1]).code == "RAYCAST_UNSUPPORTED"
    assert refusal(queries.contacts, ["/World/Cube"], None).code == "CONTACT_QUERY_UNSUPPORTED"


def test_contact_pairs_match_bodies_and_sum_impulses():
    names = {1: "/World/Cube", 2: "/World/Ground", 3: "/World/Cube/Collision", 4: "/World/Other"}
    headers = [
        SimpleNamespace(type=0, actor0=1, actor1=2, collider0=3, collider1=2,
                        contact_data_offset=0, num_contact_data=2),
        SimpleNamespace(type=0, actor0=4, actor1=2, collider0=4, collider1=2,
                        contact_data_offset=2, num_contact_data=1),
        SimpleNamespace(type=1, actor0=1, actor1=2, collider0=3, collider1=2,
                        contact_data_offset=3, num_contact_data=1),
    ]
    data = [
        SimpleNamespace(impulse=(0, 0, 1)),
        SimpleNamespace(impulse=(0, 0, 2)),
        SimpleNamespace(impulse=(9, 9, 9)),
        SimpleNamespace(impulse=(5, 5, 5)),
    ]
    pairs = queries._pairs(headers, data, ["/World/Cube"], lambda value: names[value])
    assert len(pairs) == 1  # the other body and the lost contact are excluded
    assert pairs[0]["body_a"] == "/World/Cube" and pairs[0]["body_b"] == "/World/Ground"
    assert pairs[0]["collider_a"] == "/World/Cube/Collision"
    assert pairs[0]["contact_points"] == 2
    assert pairs[0]["impulse"] == pytest.approx([0, 0, 3])
    assert pairs[0]["impulse_magnitude"] == pytest.approx(3)


def test_contacts_refuses_when_no_requested_path_reports(monkeypatch):
    import pxr

    monkeypatch.setattr(pxr, "PhysxSchema", SimpleNamespace(PhysxContactReportAPI=object()), raising=False)
    monkeypatch.setattr(pxr, "PhysicsSchemaTools", SimpleNamespace(intToSdfPath=lambda value: value), raising=False)
    monkeypatch.setattr(queries, "_engine", lambda: SimpleNamespace(get_active_physics_engine=lambda: "physx"))
    monkeypatch.setattr(queries, "start_contact_reports", lambda: True)
    _fake_physx(monkeypatch, SimpleNamespace(get_contact_report=lambda: ([], [])))

    class Prim:
        def IsActive(self):
            return True

        def HasAPI(self, api):
            return api in ("PhysicsRigidBodyAPI",)

    stage = SimpleNamespace(GetPrimAtPath=lambda value: Prim())
    error = refusal(queries.contacts, ["/World/Cube"], stage)
    assert error.code == "CONTACT_REPORT_DISABLED"
    assert error.details["paths_without_reporting"] == ["/World/Cube"]


def test_contacts_success_reports_pairs_and_impulse(monkeypatch):
    import pxr

    monkeypatch.setattr(pxr, "PhysxSchema", SimpleNamespace(PhysxContactReportAPI=object()), raising=False)
    monkeypatch.setattr(queries, "_engine", lambda: SimpleNamespace(get_active_physics_engine=lambda: "physx"))
    monkeypatch.setattr(queries, "start_contact_reports", lambda: True)
    monkeypatch.setitem(queries._LAST_REPORT, "pairs", [dict(
        body_a="/World/Cube", body_b="/World/Ground",
        collider_a="/World/Cube", collider_b="/World/Ground",
        contact_points=1, impulse=[0, 0, 4], impulse_magnitude=4,
    )])
    monkeypatch.setitem(queries._LAST_REPORT, "reports", 1)
    monkeypatch.setitem(queries._LAST_REPORT, "error", None)

    class Prim:
        def IsActive(self):
            return True

        def HasAPI(self, api):
            return True

    stage = SimpleNamespace(GetPrimAtPath=lambda value: Prim())
    result = queries.contacts(["/World/Cube"], stage)
    assert result["pairs"][0]["impulse_magnitude"] == pytest.approx(4)
    assert result["reporting_paths"] == ["/World/Cube"]
    assert result["paths_without_reporting"] == []
    assert result["reports_seen"] == 1


def test_contact_callback_stores_bounded_plain_data(monkeypatch):
    import pxr

    monkeypatch.setattr(pxr, "PhysicsSchemaTools", SimpleNamespace(intToSdfPath=lambda value: value), raising=False)
    header = SimpleNamespace(type=2, actor0="/World/Cube", actor1="/World/Ground",
                             collider0="/World/Cube", collider1="/World/Ground",
                             contact_data_offset=0, num_contact_data=2)
    data = [SimpleNamespace(impulse=(0, 0, 1)), SimpleNamespace(impulse=(0, 0, 2))]
    monkeypatch.setitem(queries._LAST_REPORT, "pairs", [])
    monkeypatch.setitem(queries._LAST_REPORT, "reports", 0)
    queries._receive_contact_report([header], data)
    assert queries._LAST_REPORT["reports"] == 1
    assert queries._LAST_REPORT["pairs"][0]["impulse"] == pytest.approx([0, 0, 3])
    assert queries._LAST_REPORT["pairs"][0]["contact_points"] == 2


def test_contacts_need_a_running_timeline(monkeypatch):
    monkeypatch.setattr(queries, "_engine", lambda: SimpleNamespace(get_active_physics_engine=lambda: "physx"))
    assert refusal(queries.contacts, ["/World/Cube"], None, timeline=_Stopped()).code == "PHYSICS_NOT_READY"


def test_create_rigid_body_enables_contact_reports(scene, monkeypatch):
    import pxr

    class API:
        def CreateThresholdAttr(self):
            return SimpleNamespace(Set=lambda value: None)

    monkeypatch.setattr(pxr, "PhysxSchema", SimpleNamespace(PhysxContactReportAPI=SimpleNamespace(Apply=lambda prim: API())), raising=False)
    stage = scene.stage()
    created = scene.create(stage, "/World/Body", "Cube", rigid=True)
    assert created["physics"]["rigid_body"] is True


def test_contact_callback_keeps_pair_33_and_filters_after_capture(monkeypatch):
    """Review fix, through the real callback: a body whose contact is pair 33 is still found."""
    import pxr

    monkeypatch.setattr(pxr, "PhysicsSchemaTools", SimpleNamespace(intToSdfPath=lambda value: value), raising=False)
    headers = [SimpleNamespace(type=0, actor0=f"/World/B{i}", actor1="/World/Ground", collider0=f"/World/B{i}",
                               collider1="/World/Ground", contact_data_offset=0, num_contact_data=0)
               for i in range(1, 41)]
    queries._receive_contact_report(headers, [])
    assert len(queries._LAST_REPORT["pairs"]) == 40 and not queries._LAST_REPORT["capped"]
    assert any(p["body_a"] == "/World/B33" for p in queries._LAST_REPORT["pairs"])


def test_contact_callback_marks_a_report_over_the_capture_limit(monkeypatch):
    import pxr

    monkeypatch.setattr(pxr, "PhysicsSchemaTools", SimpleNamespace(intToSdfPath=lambda value: value), raising=False)
    monkeypatch.setattr(queries, "MAX_CAPTURE", 5)
    headers = [SimpleNamespace(type=0, actor0=f"/World/B{i}", actor1="/World/Ground", collider0=f"/World/B{i}",
                               collider1="/World/Ground", contact_data_offset=0, num_contact_data=0)
               for i in range(1, 9)]
    queries._receive_contact_report(headers, [])
    assert queries._LAST_REPORT["capped"] and len(queries._LAST_REPORT["pairs"]) == 5

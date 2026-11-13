"""sim.trajectory.replay: a saved trajectory drives prim poses and camera frames, as a job."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from pxr import Usd, UsdGeom

from twinsmith.project_assets import AssetError
from twinsmith_bridge import jobs
from sim_tools import replay
from test_scene_usd import scene  # noqa: F401 - fixture

CSV = "step,time_s,px,py,pz,qw,qx,qy,qz\n0,0,0,0,0,1,0,0,0\n1,1,2,0,0,1,0,0,0\n2,2,2,4,0,0,0,0,1\n"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def world_position(stage, path):
    return list(UsdGeom.Xformable(stage.GetPrimAtPath(path)).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation())


def test_csv_json_and_jsonl_trajectories_parse_to_the_same_track():
    track = replay.parse(CSV, ".csv")[""]
    assert [sample[0] for sample in track] == [0.0, 1.0, 2.0] and track[1][1] == [2.0, 0.0, 0.0] and track[2][2] == [0.0, 0.0, 0.0, 1.0]
    samples = [{"time_s": t, "position": p, "orientation_wxyz": q} for t, p, q in track]
    assert replay.parse(json.dumps(samples), ".json")[""] == track
    assert replay.parse(json.dumps({"samples": samples}), ".json")[""] == track
    assert replay.parse("\n".join(json.dumps(s) for s in reversed(samples)), ".jsonl")[""] == track, "sorted by time"
    plain = replay.parse("t,x,y,z\n0,1,2,3\n0.5,2,3,4\n", ".csv")[""]
    assert plain[1] == (0.5, [2.0, 3.0, 4.0], None), "a file without orientation keeps the prim's own"
    named = replay.parse(json.dumps([{"time_s": t, "bodies": {"/World/A": {"position": [t, 0, 0]}, "/World/B": {"position": [0, t, 0]}}}
                                     for t in (0, 1)]), ".json")
    assert set(named) == {"/World/A", "/World/B"} and named["/World/B"][1][1] == [0.0, 1.0, 0.0]


@pytest.mark.parametrize("text,suffix,problem", [
    ("", ".csv", "empty"),
    ("a,b,c\n1,2,3\n", ".csv", "header with a time column"),
    ("time_s,px,py,pz\n0,0,0,0\n", ".csv", "at least two samples"),
    ("time_s,px,py,pz\n0,0,0,0\n1,nope,0,0\n", ".csv", "line 3"),
    ("time_s,px,py,pz\n0,0,0,0\n1,inf,0,0\n", ".csv", "finite"),
    ("time_s,px,py,pz,qw,qx,qy,qz\n0,0,0,0,0,0,0,0\n1,0,0,0,1,0,0,0\n", ".csv", "quaternion is zero"),
    ("{not json", ".json", "not valid JSON"),
    ('{"other": 1}', ".json", "list of samples"),
    ('[{"time_s": 0}, {"time_s": 1}]', ".json", "position"),
])
def test_a_malformed_trajectory_is_refused_with_the_place(text, suffix, problem):
    with pytest.raises(AssetError) as error:
        replay.parse(text, suffix)
    assert error.value.code == "INVALID_TRAJECTORY" and problem in str(error.value)


def test_poses_are_interpolated_and_clamped_to_the_ends():
    track = replay.parse(CSV, ".csv")[""]
    assert replay.pose_at(track, -5)[0] == [0.0, 0.0, 0.0] and replay.pose_at(track, 99)[0] == [2.0, 4.0, 0.0]
    position, orientation = replay.pose_at(track, 0.25)
    assert position == pytest.approx([0.5, 0, 0]) and orientation == pytest.approx([1, 0, 0, 0])
    position, orientation = replay.pose_at(track, 1.5)
    assert position == pytest.approx([2, 2, 0]) and orientation == pytest.approx([2 ** -0.5, 0, 0, 2 ** -0.5]), "half of a half turn"
    flipped = [(0.0, [0, 0, 0], [1.0, 0, 0, 0]), (1.0, [0, 0, 0], [-1.0, 0, 0, 0])]
    assert replay.pose_at(flipped, 0.5)[1] == pytest.approx([1, 0, 0, 0]), "the same rotation, not through zero"


def test_frame_times_follow_fps_speed_and_the_cut():
    tracks = replay.parse(CSV, ".csv")
    assert replay.frame_times(tracks, 10, 1, None, None) == pytest.approx([i / 10 for i in range(21)])
    assert len(replay.frame_times(tracks, 10, 2, None, None)) == 11
    assert replay.frame_times(tracks, 2, 1, 0.5, 1.5) == pytest.approx([0.5, 1.0, 1.5])
    for kwargs, code in ((dict(fps=60, speed=0.01, start_s=None, end_s=None), "LIMIT"), (dict(fps=10, speed=1, start_s=5, end_s=None), "INVALID_VALUE")):
        with pytest.raises(AssetError) as error:
            replay.frame_times(tracks, **kwargs)
        assert error.value.code == code


def test_the_trajectory_file_must_be_under_home_or_the_output_folder(scene, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "home").mkdir()
    inside = tmp_path / "home" / "flight.csv"
    inside.write_text(CSV)
    file, tracks = replay.read_file(scene, str(inside))
    assert file == inside and len(tracks[""]) == 3
    scene.output.mkdir(parents=True)
    (scene.output / "run.csv").write_text(CSV)
    assert replay.read_file(scene, str(scene.output / "run.csv"))[0] == scene.output / "run.csv"
    outside = tmp_path / "elsewhere.csv"
    outside.write_text(CSV)
    for raw, code in ((str(outside), "INVALID_VALUE"), ("relative.csv", "INVALID_VALUE"), (str(inside.with_suffix(".txt")), "INVALID_VALUE"),
                      (str(tmp_path / "home" / "missing.csv"), "NOT_FOUND"), (None, "INVALID_VALUE")):
        with pytest.raises(AssetError) as error:
            replay.read_file(scene, raw)
        assert error.value.code == code, raw


@pytest.mark.parametrize("args", [{"fps": 0}, {"fps": 61}, {"speed": 0}, {"label": "x" * 121}, {"label": "two\nlines"},
                                  {"filename": "clip.mov"}, {"filename": "../clip.mp4"}, {"start_s": "soon"}])
def test_bad_replay_settings_are_refused(args):
    with pytest.raises(AssetError) as error:
        replay.validate(args)
    assert error.value.code in ("INVALID_VALUE", "INVALID_OUTPUT")


def build(scene, capture, tracks=None, **settings):  # noqa: F811
    stage = scene.stage()
    scene.create(stage, "/World/Drone", "Cube", position=[9, 9, 9])
    info = scene.stage_info()
    ids = dict(session_id=info["session_id"], stage_id=info["stage_id"])
    tracks = tracks or {"/World/Drone": replay.parse(CSV, ".csv")[""]}
    cleaned = replay.validate({"fps": 2, "filename": "flight.mp4", **settings})
    times = replay.frame_times(tracks, cleaned["fps"], cleaned["speed"], cleaned["start_s"], cleaned["end_s"])

    async def next_frame():
        await asyncio.sleep(0)

    job_replay = replay.Replay(scene, ids, tracks, times, cleaned, capture, next_frame, asyncio.Lock())
    job_replay.prepare(stage)
    return stage, job_replay


async def finish(job, turns=5000):
    for _ in range(turns):
        if job.state != "running":
            return job.describe()
        await asyncio.sleep(0)
    raise AssertionError("the replay did not end")


@pytest.mark.anyio
async def test_a_replay_moves_the_prim_saves_a_frame_per_pose_and_puts_it_back(scene):  # noqa: F811
    seen = []

    async def capture(name):
        seen.append((name, world_position(scene.stage(), "/World/Drone")))
        return {"status": "ok", "file": str(scene.output / name)}

    stage, job_replay = build(scene, capture, label="PhysX hover")
    before = stage.GetRootLayer().ExportToString()
    assert world_position(stage, "/World/Drone") == pytest.approx([0, 0, 0]), "prepare moved it to the first pose"
    out = await finish(jobs.JobStore(asyncio.ensure_future).start("sim.trajectory.replay", job_replay.work))
    assert out["state"] == "succeeded", out
    assert [name for name, _ in seen] == [f"flight-{i:05d}.png" for i in range(1, 6)]
    assert [position for _, position in seen] == [pytest.approx(p) for p in ([0, 0, 0], [1, 0, 0], [2, 0, 0], [2, 2, 0], [2, 4, 0])]
    result = out["result"]
    assert result["frames"] == 5 and result["poses_restored"] is True and result["prims"] == ["/World/Drone"]
    assert result["encode"] == {"folder": str(scene.output), "pattern": "flight-*.png", "frames": 5, "fps": 2.0,
                                "label": "PhysX hover", "output": "flight.mp4"}
    assert world_position(stage, "/World/Drone") == pytest.approx([9, 9, 9]), "back where it was"
    assert stage.GetRootLayer().ExportToString() == before, "only session-layer overrides: the source is unchanged"


@pytest.mark.anyio
async def test_a_failed_capture_fails_the_job_and_still_restores_the_pose(scene):  # noqa: F811
    async def capture(name):
        if name.endswith("00003.png"):
            return {"status": "error", "code": "SENSOR_INVALIDATED", "message": "Camera prim is inactive."}
        return {"status": "ok", "file": str(scene.output / name)}

    stage, job_replay = build(scene, capture)
    out = await finish(jobs.JobStore(asyncio.ensure_future).start("sim.trajectory.replay", job_replay.work))
    assert out["state"] == "failed" and out["code"] == "SENSOR_INVALIDATED" and "Frame 3" in out["error"] and out["frames"] == 2
    assert world_position(stage, "/World/Drone") == pytest.approx([9, 9, 9])


@pytest.mark.anyio
async def test_cancel_restores_the_pose_and_no_camera_means_no_frames(scene):  # noqa: F811
    stage, job_replay = build(scene, None, fps=60)
    store = jobs.JobStore(asyncio.ensure_future)
    job = store.start("sim.trajectory.replay", job_replay.work)
    await asyncio.sleep(0)
    store.cancel(job.id)
    out = await finish(job)
    assert out["state"] == "cancelled" and world_position(stage, "/World/Drone") == pytest.approx([9, 9, 9])


@pytest.mark.anyio
async def test_without_a_camera_the_poses_are_shown_and_nothing_is_saved(scene, monkeypatch):  # noqa: F811
    clock = iter(range(10_000))
    monkeypatch.setattr(replay.time, "monotonic", lambda: next(clock) * 0.3)
    stage, job_replay = build(scene, None)
    out = await finish(jobs.JobStore(asyncio.ensure_future).start("sim.trajectory.replay", job_replay.work))
    assert out["state"] == "succeeded" and out["result"]["frames"] == 0 and out["result"]["poses"] == 5
    assert "encode" not in out["result"] and "No camera" in out["result"]["note"]


@pytest.mark.anyio
async def test_a_stage_change_under_a_replay_fails_it_without_touching_the_new_stage(scene):  # noqa: F811
    async def capture(name):
        if name.endswith("00002.png"):
            scene.get_stage = lambda: Usd.Stage.CreateInMemory()
        return {"status": "ok", "file": str(scene.output / name)}

    _, job_replay = build(scene, capture)
    out = await finish(jobs.JobStore(asyncio.ensure_future).start("sim.trajectory.replay", job_replay.work))
    assert out["state"] == "failed" and out["code"] == "STALE_TARGET"

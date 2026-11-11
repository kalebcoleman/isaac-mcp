"""sim.trajectory.replay: drive prim poses from a saved trajectory file and save one camera frame
per step, as a job. The MCP host turns the frames into a labelled MP4 when the job ends.

A replay is a visualisation: the timeline stays stopped, physics does not run, poses are
session-layer overrides, and the prims go back to where they were when the job ends.
"""

import csv
import json
import math
import os
import re
import time
from pathlib import Path

from isaac_mcp.project_assets import AssetError, refuse
from isaac_mcp_bridge.jobs import JobError

MAX_FILE_BYTES = 64 * 1024**2
MAX_SAMPLES = 500_000
MAX_BODIES = 16
MAX_FRAMES = 3600
MAX_LABEL_CHARS = 120
VIDEO_NAME = r"[A-Za-z0-9][A-Za-z0-9_-]{0,48}\.mp4"
TIME_COLUMNS = ("time_s", "t", "time")
POSITION_COLUMNS = (("px", "py", "pz"), ("x", "y", "z"))
ORIENTATION_COLUMNS = ("qw", "qx", "qy", "qz")


def _numbers(values, count, label, where):
    try:
        out = [float(v) for v in values]
    except (TypeError, ValueError):
        out = []
    if len(out) != count or not all(math.isfinite(v) for v in out):
        refuse("INVALID_TRAJECTORY", f"{where}: {label} must be {count} finite numbers.")
    return out


def _unit(q, where):
    norm = math.sqrt(sum(v * v for v in q))
    if norm < 1e-9:
        refuse("INVALID_TRAJECTORY", f"{where}: the orientation quaternion is zero.")
    return [v / norm for v in q]


def _sample(entry, where):
    """(position, orientation or None) from {"position": [...], "orientation_wxyz": [...]}."""
    if not isinstance(entry, dict) or "position" not in entry:
        refuse("INVALID_TRAJECTORY", f"{where}: expected an object with position (and orientation_wxyz).")
    position = _numbers(entry["position"], 3, "position", where)
    orientation = entry.get("orientation_wxyz", entry.get("orientation"))
    if orientation is not None:
        orientation = _unit(_numbers(orientation, 4, "orientation_wxyz", where), where)
    return position, orientation


def _read_csv(text):
    rows = csv.reader(text.splitlines())
    try:
        header = [name.strip() for name in next(rows)]
    except StopIteration:
        refuse("INVALID_TRAJECTORY", "The file is empty.")
    time_column = next((c for c in TIME_COLUMNS if c in header), None)
    position = next((cols for cols in POSITION_COLUMNS if all(c in header for c in cols)), None)
    if time_column is None or position is None:
        refuse("INVALID_TRAJECTORY", "A CSV trajectory needs a header with a time column (time_s, t or time) and a "
               "position (px,py,pz or x,y,z); qw,qx,qy,qz are optional. sim__simulation__run writes this layout.")
    oriented = all(c in header for c in ORIENTATION_COLUMNS)
    index = {name: header.index(name) for name in (time_column, *position, *(ORIENTATION_COLUMNS if oriented else ()))}
    track = []
    for line, row in enumerate(rows, start=2):
        if not row or not any(cell.strip() for cell in row):
            continue
        where = f"line {line}"
        if len(row) < len(header):
            refuse("INVALID_TRAJECTORY", f"{where}: {len(row)} columns, the header has {len(header)}.")
        t = _numbers([row[index[time_column]]], 1, time_column, where)[0]
        p = _numbers([row[index[c]] for c in position], 3, "position", where)
        q = _unit(_numbers([row[index[c]] for c in ORIENTATION_COLUMNS], 4, "orientation", where), where) if oriented else None
        track.append((t, p, q))
        if len(track) > MAX_SAMPLES:
            refuse("LIMIT", f"The trajectory has more than {MAX_SAMPLES} samples.")
    return {"": track}


def _read_json(text, lines):
    try:
        if lines:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            data = json.loads(text)
    except ValueError as exc:
        refuse("INVALID_TRAJECTORY", f"The file is not valid JSON: {str(exc)[:200]}")
    if isinstance(data, dict):
        data = data.get("samples")
    if not isinstance(data, list):
        refuse("INVALID_TRAJECTORY", "A JSON trajectory is a list of samples (or {\"samples\": [...]}), each "
               "{\"time_s\", \"position\", \"orientation_wxyz\"} or {\"time_s\", \"bodies\": {\"/World/A\": {...}}}.")
    if len(data) > MAX_SAMPLES:
        refuse("LIMIT", f"The trajectory has more than {MAX_SAMPLES} samples.")
    tracks = {}
    for number, entry in enumerate(data, start=1):
        where = f"sample {number}"
        if not isinstance(entry, dict):
            refuse("INVALID_TRAJECTORY", f"{where}: expected an object.")
        t = _numbers([entry.get("time_s", entry.get("t"))], 1, "time_s", where)[0]
        bodies = entry.get("bodies")
        if bodies is None:
            bodies = {"": entry}
        if not isinstance(bodies, dict) or not bodies:
            refuse("INVALID_TRAJECTORY", f"{where}: bodies must map prim paths to poses.")
        for prim, pose in bodies.items():
            tracks.setdefault(prim, []).append((t, *_sample(pose, f"{where} {prim}".strip())))
            if len(tracks) > MAX_BODIES:
                refuse("LIMIT", f"A trajectory may move at most {MAX_BODIES} prims.")
    return tracks


def parse(text, suffix):
    """{prim path, or "" for the single body the caller names: [(time_s, position, orientation or None)]}."""
    tracks = _read_csv(text) if suffix == ".csv" else _read_json(text, suffix == ".jsonl")
    for prim, track in tracks.items():
        if len(track) < 2:
            refuse("INVALID_TRAJECTORY", "A trajectory needs at least two samples" + (f" for {prim}." if prim else "."))
        track.sort(key=lambda sample: sample[0])
    return tracks


def pose_at(track, t):
    """Position (linear) and orientation (normalised linear) at time t, clamped to the track's ends."""
    if t <= track[0][0]:
        return track[0][1], track[0][2]
    if t >= track[-1][0]:
        return track[-1][1], track[-1][2]
    lo, hi = 0, len(track) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if track[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    (t0, p0, q0), (t1, p1, q1) = track[lo], track[hi]
    w = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
    position = [a + (b - a) * w for a, b in zip(p0, p1)]
    if q0 is None or q1 is None:
        return position, q0
    if sum(a * b for a, b in zip(q0, q1)) < 0:  # the same rotation, the short way round
        q1 = [-v for v in q1]
    q = [a + (b - a) * w for a, b in zip(q0, q1)]
    norm = math.sqrt(sum(v * v for v in q)) or 1.0
    return position, [v / norm for v in q]


def frame_times(tracks, fps, speed, start_s, end_s):
    begin = max(min(track[0][0] for track in tracks.values()), start_s if start_s is not None else -math.inf)
    end = min(max(track[-1][0] for track in tracks.values()), end_s if end_s is not None else math.inf)
    if end <= begin:
        refuse("INVALID_VALUE", f"start_s/end_s leave nothing to replay: the trajectory spans "
               f"{min(t[0][0] for t in tracks.values()):g} to {max(t[-1][0] for t in tracks.values()):g} s.")
    count = int(math.floor((end - begin) * fps / speed + 1e-9)) + 1
    if count > MAX_FRAMES:
        refuse("LIMIT", f"{count} frames would be written; the limit is {MAX_FRAMES}. Raise speed, lower fps, or "
               f"replay a part with start_s/end_s (this span is {end - begin:g} s).")
    return [begin + i * speed / fps for i in range(count)]


def read_file(scene, raw):
    if not isinstance(raw, str) or not os.path.isabs(os.path.expanduser(raw)):
        refuse("INVALID_VALUE", "trajectory must be an absolute path to a .csv, .json or .jsonl file.")
    file = Path(os.path.expanduser(raw)).resolve()
    home, output = Path.home().resolve(), scene.output.resolve()
    if not any(root == file.parent or root in file.parents for root in (home, output)):
        refuse("INVALID_VALUE", f"trajectory must be inside {home} or the output folder {output}.")
    if file.suffix.lower() not in (".csv", ".json", ".jsonl"):
        refuse("INVALID_VALUE", "trajectory must be a .csv, .json or .jsonl file.")
    if not file.is_file():
        refuse("NOT_FOUND", f"{file} does not exist on Kit's machine.")
    if file.stat().st_size > MAX_FILE_BYTES:
        refuse("LIMIT", "The trajectory file is larger than 64 MiB.")
    return file, parse(file.read_text(encoding="utf-8", errors="replace"), file.suffix.lower())


def validate(args):
    fps = args.get("fps", 30)
    if type(fps) not in (int, float) or not 1 <= fps <= 60:
        refuse("INVALID_VALUE", "fps must be 1..60.")
    speed = args.get("speed", 1.0)
    if type(speed) not in (int, float) or not 0.01 <= speed <= 100:
        refuse("INVALID_VALUE", "speed must be 0.01..100 (2 plays twice as fast).")
    label = args.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > MAX_LABEL_CHARS or "\n" in label):
        refuse("INVALID_VALUE", f"label must be one line of at most {MAX_LABEL_CHARS} characters.")
    filename = args.get("filename") or time.strftime("replay-%Y%m%d-%H%M%S.mp4")
    if not isinstance(filename, str) or not re.fullmatch(VIDEO_NAME, filename):
        refuse("INVALID_OUTPUT", "filename: a new name of letters, digits, _ and -, ending .mp4 (<=52 characters).")
    for key in ("start_s", "end_s"):
        if args.get(key) is not None and (type(args[key]) not in (int, float) or not math.isfinite(args[key])):
            refuse("INVALID_VALUE", f"{key} must be a number of seconds.")
    return dict(fps=float(fps), speed=float(speed), label=label or None, filename=filename,
                start_s=args.get("start_s"), end_s=args.get("end_s"))


class Replay:
    """One replay. ``capture`` saves the camera frame for a file name (or is None), ``next_frame``
    awaits the next app frame; both come from Kit, so the loop itself is plain Python."""

    def __init__(self, scene, ids, tracks, times, settings, capture, next_frame, lock):
        self.scene, self.ids, self.tracks, self.times = scene, ids, tracks, times
        self.settings, self.capture, self.next_frame, self.lock = settings, capture, next_frame, lock
        self.stem = settings["filename"][:-4]
        self.original = {}
        self.ops = {}

    def prepare(self, stage):
        """Move every prim to its first pose through the checked path; later frames reuse its transform op."""
        from pxr import Gf, Usd, UsdGeom

        for prim_path, track in self.tracks.items():
            prim = self.scene.prim(stage, prim_path)
            xf = UsdGeom.Xformable(prim)
            if not xf:
                refuse("WRONG_TYPE", f"{prim_path} has no transform to drive; pass an Xform or a body.")
            world = Gf.Transform(xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default()))
            rotation = world.GetRotation().GetQuat()
            current = [rotation.GetReal(), *rotation.GetImaginary()]
            self.original[prim_path] = (list(world.GetTranslation()), current, list(world.GetScale()))
            position, orientation = pose_at(track, self.times[0])
            self.scene.transform(stage, prim_path, position=position, orientation=orientation or current, frame="world")
            self.ops[prim_path] = UsdGeom.Xformable(prim).GetOrderedXformOps()[0]

    def _set(self, stage, t):
        from pxr import Usd

        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for prim_path, track in self.tracks.items():
                position, orientation = pose_at(track, t)
                _, current, scale = self.original[prim_path]
                prim = self.scene.prim(stage, prim_path)
                self.ops[prim_path].Set(self.scene.pose(stage, prim.GetParent(), position, orientation or current,
                                                        "world", scale))

    def restore(self):
        """Put the prims back where they were; the stage may be gone, which is not this job's failure."""
        try:
            stage = self.scene.target(self.ids["session_id"], self.ids["stage_id"], True)
            for prim_path, (position, orientation, _) in self.original.items():
                self.scene.transform(stage, prim_path, position=position, orientation=orientation, frame="world")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def work(self, job):
        started = time.monotonic()
        written = []
        total = len(self.times)
        try:
            for index, t in enumerate(self.times):
                job.checkpoint()
                async with self.lock:
                    job.checkpoint()
                    stage = self.scene.target(self.ids["session_id"], self.ids["stage_id"], True)
                    self._set(stage, t)
                if self.capture is not None:
                    name = f"{self.stem}-{index + 1:05d}.png"
                    shot = await self.capture(name)
                    if shot.get("status") != "ok":
                        raise AssetError(shot.get("code", "CAPTURE_FAILED"),
                                         f"Frame {index + 1} could not be captured: {shot.get('message')}",
                                         frames=len(written))
                    written.append(shot["file"])
                else:  # no camera: show each pose for its share of wall time, for a screen recording
                    due = started + (index + 1) / self.settings["fps"]
                    await self.next_frame()
                    while time.monotonic() < due:
                        job.checkpoint()
                        await self.next_frame()
                job.report((index + 1) / total, f"frame {index + 1}/{total}", frames=len(written))
        finally:
            restored = self.restore()
        result = dict(frames=len(written), poses=total, fps=self.settings["fps"], speed=self.settings["speed"],
                      trajectory_span_s=round(self.times[-1] - self.times[0], 6),
                      prims=sorted(self.tracks), poses_restored=restored,
                      wall_time_s=round(time.monotonic() - started, 3))
        if written:
            folder = str(Path(written[0]).parent)
            # The MCP host encodes these when it reads this result (job_status); see docs/TOOLS-SCENE.md.
            result["encode"] = dict(folder=folder, pattern=f"{self.stem}-*.png", frames=len(written),
                                    fps=self.settings["fps"], label=self.settings["label"],
                                    output=self.settings["filename"])
            result["note"] = ("job_status through the MCP server adds video_path (frames encoded with the label). "
                              "A direct socket caller encodes them with join_videos(mode=\"frames\").")
        else:
            result["note"] = "No camera was given, so no frames were saved; the poses were shown at wall-clock pace."
        return result


async def run(ext, args):
    import omni.kit.app as kit_app

    s = ext.scene
    ids = dict(session_id=args["session_id"], stage_id=args["stage_id"])
    stage = s.target(**ids, mutation=True)
    settings = validate(args)
    store = getattr(ext.bridge, "jobs", None)
    if store is None:
        refuse("JOBS_UNAVAILABLE", "This Kit runs an isaac.mcp.bridge without background jobs; update the "
               "extension (it ships in the same checkout as sim-tools) and restart Kit.")
    file, tracks = read_file(s, args.get("trajectory"))
    if "" in tracks:
        if not args.get("prim"):
            refuse("INVALID_VALUE", "This trajectory holds one unnamed body: pass prim, the path to move.")
        tracks = {args["prim"]: tracks[""]}
    elif args.get("prim"):
        refuse("INVALID_VALUE", "This trajectory names its own prims (bodies); do not pass prim.")
    times = frame_times(tracks, settings["fps"], settings["speed"], settings["start_s"], settings["end_s"])
    camera = args.get("camera")
    capture = None
    if camera is not None:
        take = ext.handler("sim.camera.capture")
        adopt = bool(args.get("adopt", False))

        async def capture(name):
            return await take(**ids, path=camera, filename=name, **({"adopt": True} if adopt else {}))

        if (s.output / f"{settings['filename'][:-4]}-00001.png").exists():
            refuse("CONFLICT", f"Frames named {settings['filename'][:-4]}-*.png already exist in the output folder; "
                   "use another filename. No overwrite.")
    replay = Replay(s, ids, tracks, times, settings, capture, kit_app.get_app().next_update_async, ext._operation_lock)
    replay.prepare(stage)
    try:
        job = store.start("sim.trajectory.replay", replay.work, label=settings["label"] and settings["label"][:80])
    except JobError as exc:
        replay.restore()
        refuse("JOB_LIMIT", str(exc))
    return dict(job_id=job.id, state=job.state, trajectory=str(file), prims=sorted(tracks), frames=len(times),
                fps=settings["fps"], camera=camera, video=settings["filename"] if camera else None,
                next=f"job_status(job_id=\"{job.id}\", wait_s=30) until state is not running"
                     + ("; the result then has video_path." if camera else "."))

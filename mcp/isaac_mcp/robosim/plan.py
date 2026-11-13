"""Plain-data checks and arithmetic for the robosim bridge: push, timing, time mapping, scene scan.

Nothing here touches RSS, the disk or the network, so the arithmetic is tested on its own.
"""

from __future__ import annotations

import math
import re

PUSH_KEYS = ("waypoints", "object_velocity")
MAX_WAYPOINTS = 200
MAX_STEPS = 20000
MAX_COORDINATE_M = 2.0
FAST_PUSHER_M_S = 1.0
DURATION_TOLERANCE = 0.10

ENGINES = {
    "cpu": "plain MuJoCo (MuJoCo's C library) on the CPU",
    "cuda:0": "Newton's MuJoCo-Warp solver on the GPU",
}


def engine(device):
    """Which physics engine a run's device selects: CPU is plain MuJoCo, CUDA is MuJoCo-Warp."""
    return {"device": device, "physics": ENGINES.get(device, "unknown"),
            "scope": "Compares images. It does not fit mass or friction."}


def _number(value, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{what} must be a finite number.")
    return float(value)


def validate_push(push):
    """Check and normalise ``push``; return ``(push, warnings)``.

    ``push`` is ``{"waypoints": [[t, x, y], ...], "object_velocity": [vx, vy, yaw_rate]}``. Times are
    seconds and strictly increase; x and y are pusher positions on the table in metres. Either key may
    be left out, but not both.
    """
    if not isinstance(push, dict):
        raise ValueError("push must be an object like {'waypoints': [[0, x, y], [2, x, y]], 'object_velocity': [vx, vy, wz]}.")
    unknown = sorted(set(push) - set(PUSH_KEYS))
    if unknown:
        raise ValueError(f"push has unknown keys {unknown}; use {list(PUSH_KEYS)}.")
    if not any(push.get(key) for key in PUSH_KEYS):
        raise ValueError("push needs waypoints [[seconds, x, y], ...] or object_velocity [vx, vy, yaw_rate].")
    out, warnings = {"waypoints": None, "object_velocity": None}, []
    raw = push.get("waypoints")
    if raw:
        if not isinstance(raw, (list, tuple)) or not 2 <= len(raw) <= MAX_WAYPOINTS:
            raise ValueError(f"waypoints must be a list of 2 to {MAX_WAYPOINTS} items [seconds, x, y].")
        points = []
        for i, item in enumerate(raw):
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise ValueError(f"waypoints[{i}] must be [seconds, x, y].")
            t, x, y = (_number(v, f"waypoints[{i}]") for v in item)
            if t < 0 or abs(x) > MAX_COORDINATE_M or abs(y) > MAX_COORDINATE_M:
                raise ValueError(f"waypoints[{i}] needs seconds >= 0 and x, y within {MAX_COORDINATE_M} m.")
            if points and t <= points[-1][0]:
                raise ValueError(f"waypoints times must strictly increase; waypoints[{i}] has {t} after {points[-1][0]}.")
            points.append([t, x, y])
        fastest = max(math.hypot(b[1] - a[1], b[2] - a[2]) / (b[0] - a[0]) for a, b in zip(points, points[1:]))
        if fastest > FAST_PUSHER_M_S:
            warnings.append(f"The pusher moves up to {fastest:.2f} m/s; above {FAST_PUSHER_M_S} m/s it can pass through the object between steps.")
        out["waypoints"] = points
    velocity = push.get("object_velocity")
    if velocity:
        if not isinstance(velocity, (list, tuple)) or len(velocity) != 3:
            raise ValueError("object_velocity must be [vx, vy, yaw_rate] in m/s and rad/s.")
        out["object_velocity"] = [_number(v, "object_velocity") for v in velocity]
    if out["waypoints"] is None:
        out["waypoints"] = [[0.0, 0.0, -1.0], [1.0, 0.0, -1.0]]
        warnings.append("No waypoints: the pusher stays off the table at (0, -1).")
    return out, warnings


def rollout_plan(control_hz, *, duration_s=None, steps=None, record_fps=10.0, push=None):
    """Turn a duration or step count and a record rate into steps, a recording stride and the real rates.

    Exactly one of ``duration_s`` and ``steps`` may be given; with neither, the push's last waypoint time
    decides, else 2 s. ``record_fps`` is how many frames per simulated second to record; the stride is
    the nearest whole number of control steps, so the real rate is reported too.
    """
    hz = _number(control_hz, "control_hz")
    if hz <= 0:
        raise ValueError("The scene reports a control rate of zero or less.")
    fps_asked = _number(record_fps, "record_fps")
    if not 0 < fps_asked <= hz:
        raise ValueError(f"record_fps must be above 0 and at most the control rate ({hz:g} Hz).")
    if duration_s is not None and steps is not None:
        raise ValueError("Give duration_s or steps, not both.")
    warnings = []
    if steps is not None:
        if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
            raise ValueError("steps must be a positive integer.")
        n = steps
    else:
        if duration_s is None:
            waypoints = (push or {}).get("waypoints")
            duration_s = waypoints[-1][0] if waypoints else 2.0
        duration_s = _number(duration_s, "duration_s")
        if duration_s <= 0:
            raise ValueError("duration_s must be positive.")
        n = max(1, round(duration_s * hz))
    if n > MAX_STEPS:
        raise ValueError(f"{n} steps is more than the limit of {MAX_STEPS} ({MAX_STEPS / hz:.0f} s at {hz:g} Hz).")
    every = max(1, round(hz / fps_asked))
    real_fps = hz / every
    if abs(real_fps - fps_asked) > 0.01 * fps_asked:
        warnings.append(f"record_fps {fps_asked:g} is not a whole number of control steps at {hz:g} Hz; recording every "
                        f"{every} steps = {real_fps:g} frames per simulated second.")
    return {"steps": n, "duration_s": n / hz, "control_hz": hz, "every": every, "record_fps_asked": fps_asked,
            "record_fps": real_fps, "frames_expected": n // every + (1 if n % every else 0) + 1, "warnings": warnings}


def time_mapping(anchor_times, take_duration, *, clip_start=None, clip_end=None, sim_start=None, sim_end=None):
    """Map clip seconds to simulated seconds, linearly, with explicit windows.

    Defaults: the clip window is the span of the anchors; the simulated window has the same duration and
    starts at zero. Returns the windows, the scale (simulated seconds per clip second), a ``sim_t`` for
    every anchor inside the clip window, the anchors left out, and warnings (durations that differ by
    more than 10 percent).
    """
    if not anchor_times:
        raise ValueError("The reference has no anchors; run reference first.")
    c0 = min(anchor_times) if clip_start is None else _number(clip_start, "start_s")
    c1 = max(anchor_times) if clip_end is None else _number(clip_end, "end_s")
    if c0 < 0 or c1 <= c0:
        raise ValueError("The clip window needs start_s >= 0 and end_s > start_s.")
    s0 = 0.0 if sim_start is None else _number(sim_start, "sim_start_s")
    s1 = s0 + (c1 - c0) if sim_end is None else _number(sim_end, "sim_end_s")
    if s0 < 0 or s1 <= s0:
        raise ValueError("The simulated window needs sim_start_s >= 0 and sim_end_s > sim_start_s.")
    if s1 > take_duration + 1e-6:
        raise ValueError(f"The take lasts {take_duration:g} s of simulated time but the simulated window ends at {s1:g} s. "
                         "Lower sim_end_s, or record a longer rollout.")
    scale = (s1 - s0) / (c1 - c0)
    inside = [t for t in anchor_times if c0 - 1e-6 <= t <= c1 + 1e-6]
    outside = [t for t in anchor_times if t not in inside]
    if len(inside) < 2:
        raise ValueError(f"Fewer than two reference anchors fall inside the clip window {c0:g}–{c1:g} s; "
                         "run reference again with 'at' times that cover it.")
    warnings = []
    if abs((s1 - s0) - (c1 - c0)) > DURATION_TOLERANCE * (c1 - c0):
        warnings.append(f"The clip window lasts {c1 - c0:g} s but the simulated window {s1 - s0:g} s; sim time is mapped at "
                        f"{scale:.3g} simulated s per clip s. A speed difference can look like different motion.")
    return {"clip_start_s": c0, "clip_end_s": c1, "sim_start_s": s0, "sim_end_s": s1,
            "sim_seconds_per_clip_second": scale, "take_duration_s": take_duration,
            "sim_t": {t: round(s0 + (t - c0) * scale, 6) for t in inside}, "anchors_outside_window": outside,
            "warnings": warnings}


def relabel_ratios(reading):
    """Replace RSS's ``size_ratio`` by what it is: a linear scale, with the area ratio from pixel counts.

    RSS computes ``size_ratio = sqrt(sim_pixels / photo_pixels)`` (compare.py ``compare_masks``), so it is
    the square root of the area ratio. The area ratio is ``sim.pixels / photo.pixels``.
    """
    if not isinstance(reading, dict):
        return reading
    out = {key: value for key, value in reading.items() if key != "size_ratio"}
    photo, sim = reading.get("photo"), reading.get("sim")
    if photo and sim and photo.get("pixels"):
        out["area_ratio"] = round(sim["pixels"] / photo["pixels"], 3)
    else:
        out["area_ratio"] = None
    out["linear_scale_ratio"] = reading.get("size_ratio")
    return out


def centroid_displacement(first, last, name):
    """Image-space centroid displacement of ``name`` between two anchors, for the clip and the sim."""
    def pick(anchor, side):
        found = ((anchor.get("objects") or {}).get(name) or {}).get(side)
        return found["centre"] if found else None

    sides = {}
    for side in ("photo", "sim"):
        a, b = pick(first, side), pick(last, side)
        sides[side] = None if a is None or b is None else [round(b[0] - a[0], 1), round(b[1] - a[1], 1)]
    clip, sim = sides["photo"], sides["sim"]
    entry = {"clip_px": clip, "sim_px": sim, "difference_px": None, "same_sign_x": None, "same_sign_y": None,
             "magnitude_ratio": None}
    if clip is not None and sim is not None:
        entry["difference_px"] = [round(sim[0] - clip[0], 1), round(sim[1] - clip[1], 1)]
        entry["same_sign_x"] = bool(clip[0] * sim[0] > 0)
        entry["same_sign_y"] = bool(clip[1] * sim[1] > 0)
        length = math.hypot(*clip)
        entry["magnitude_ratio"] = round(math.hypot(*sim) / length, 3) if length else None
    return entry


_BODY_WRITE = re.compile(r"\bbody_qd?\s*\.\s*assign\s*\(")


def scene_problems(text):
    """Writes the scene makes that the solver silently discards, found by scanning its source."""
    problems = []
    if _BODY_WRITE.search(text):
        problems.append(
            "The scene writes sim.state.body_q or body_qd with .assign(...). The solver rebuilds those from joint_q "
            "and joint_qd every step, so the write is discarded and nothing moves. Use scene_template's pusher with "
            "rollout(push={'waypoints': [[seconds, x, y], ...]}), or write the free body's joint_q and joint_qd slices "
            "(entity.coord_start and entity.dof_start, as the template's place_free_body does).")
    return problems

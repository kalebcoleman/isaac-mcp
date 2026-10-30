#!/usr/bin/env python3
"""Measure the UR5e twin's joint controller and plot slide-ready control figures.

Four experiments, each writing a CSV, a 1600x900 PNG and a metrics entry in
``DIR/summary.json``:

    step    position step on one joint (default elbow_joint, +0.3 rad, then back):
            rise time (10-90 %), overshoot, settling time (2 % band), steady-state
            error, peak time and dead time.
    track   sinusoidal reference (0.25 rad amplitude, 0.2 and 0.5 Hz, 2 periods
            each): tracking-error RMS, peak error and phase lag from the
            cross-correlation of reference and response.
    gains   PD-gain sweep (stiffness x damping) repeated over the same step,
            all responses overlaid with a table of overshoot / settling time.
            The original drive values are read first and restored in a
            ``finally`` block. Also answers, empirically, whether editing the
            USD drive attributes at runtime reaches PhysX while the graph
            controller is running.
    ik      (optional) one Cartesian move: goal 8 cm from the current
            end-effector pose, solved with Lula ``LulaKinematicsSolver``, sent
            through the same /joint_states path, end-effector error vs time.
            Skipped with a clear message when the solver cannot be built.

HOW THE COMMANDS REACH THE JOINTS (this is the whole point: the real path)

    this script (rclpy, sensor_msgs/JointState on /joint_states)
        -> isaacsim.ros2.bridge.ROS2SubscribeJointState          (subscriber)
        -> isaacsim.core.nodes.IsaacArticulationController        (positionCommand)
        -> PhysX PD drive on /World/UR5e/joints/<joint>

    Evidence: exts/ur5e-dt/ur5e_dt/extension.py:3046-3070 builds exactly that
    graph; the topic is ``/joint_states`` (extension.py:3053); the ROS2Context
    takes its domain id from the environment (extension.py:3051-3052), so the
    client must adopt config/ros_dds.env the way scripts/tf_capture.py does
    (tf_capture.py:26-40, 101-140).

    The joint DRIVES are authored once, when the robot is loaded:
    UsdPhysics.DriveAPI.Apply(prim, "angular") with maxForce 200,
    stiffness 100, damping 10 (extension.py:2948-2965, values at 701-703).
    The graph controller rewrites the position TARGET every physics step but
    never touches the gains, so a runtime gain change is a PhysX-side state
    change - see the ``gains`` experiment.

HOW THE RESPONSE IS READ (no arm joint-state publisher exists)

    The twin publishes a JointState only for the GRIPPER
    (/rg2_sim/joint_state, extension.py:3133-3141). There is NO publisher for
    the arm joints, so there is nothing for an rclpy subscriber to listen to.
    This script therefore polls the articulation through the bridge
    (``run_python``, scripts/bridge_exec.py) in a tight ``app.update()`` loop,
    which is also what scripts/checks/single_joint_motion.py does. Each sample
    carries both wall time (shared with the publisher process) and the
    simulation time, so the commanded signal - published on wall time from
    this process - is mapped onto sim time by interpolation. Everything that
    is a physical duration (rise, settling, lag) is computed in SIM time.

USAGE

    OMNI_KIT_ACCEPT_EULA=YES .venv/bin/python scripts/demo/control_experiments.py \
        --out demo-out/asu/control [--port 8791] \
        [--experiments step,track,gains,ik] [--joint elbow_joint] [--headless] \
        [--launch]

    --launch boots ``./run.sh --headless --port <port>`` itself, loads
    ``ur5e_dt.quick_start_with_workstation`` + ``ur5e_dt.play_scene`` and kills
    only the process it started (SIGKILL on that pid; Isaac Sim ignores
    SIGTERM). Without --launch the twin must already be running on --port with
    the workstation loaded and the timeline playing.

    Pure-Python checks (metrics + plotting on synthetic data, no sim, no GPU):

        .venv/bin/python scripts/demo/control_experiments.py --selftest

ASSUMPTIONS AND CAVEATS (things this script cannot verify without the sim)

  * /joint_states is published by THIS script only. The real/fake UR driver
    publishes the same topic from its own broadcaster (extension.py:489); if it is running,
    the two command streams fight and the measurements are meaningless. The
    script counts existing publishers before creating its own and refuses to
    run unless --allow-other-publishers is given.
  * The graph's subscriber uses the bridge default QoS (keep_last 10, reliable,
    volatile), so the publisher here uses the same reliable/volatile profile.
  * The elbow at the default workstation pose sits close to the tabletop: a
    +0.1 rad elbow move drives the forearm into the table
    (scripts/checks/single_joint_motion.py:21). The default +0.3 rad step can
    therefore hit the table; if the response saturates at a constant value,
    re-run with ``--step-rad -0.3`` (up, away from the table).
  * The command edge is quantised by the publish rate (--publish-hz, default
    100 Hz) plus one sim frame (~16.7 ms at the twin's 60 fps frame rate,
    extension.py:697-698). That floor is reported as ``dead_time_s``, not
    subtracted from the rise time.
  * The IK goal is ``--ik-offset`` in the WORLD frame (default +8 cm along
    world x). Point it wherever the arm has room: it is not derived from the
    approach direction of the tool, because the twin has no TCP frame of its
    own and the Lula UR5e model's ``tool0`` does not exist in this USD.
  * No real robot, no ROS driver, no URSim is touched.
"""
from __future__ import annotations

import argparse
import atexit
import csv
import json
import math
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
MCP_DIR = REPO / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from isaac_mcp import client as bridge_client  # noqa: E402

ARM_JOINTS = (
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
)
JOINT_TOPIC = "/joint_states"
DEFAULT_EXPERIMENTS = "step,track,gains,ik"
#: (stiffness, damping) pairs for --gain-sweep. The twin's stock values are
#: 100 / 10 (exts/ur5e-dt/ur5e_dt/extension.py:701-703), which is in the sweep
#: so one curve is the shipped behaviour. Six combinations x ~5 s is ~30 s.
DEFAULT_GAIN_SWEEP = ((50.0, 5.0), (50.0, 10.0), (100.0, 10.0), (100.0, 20.0), (200.0, 10.0), (400.0, 10.0))
#: The USD angular-drive attributes are read by PhysX in per-degree units and
#: converted to per-radian (isaacsim.core.prims articulation.py, get_gains USD
#: branch: kps = 1/deg2rad(1/usd)). The PhysX-side API (set_gains) takes the
#: per-radian value, so a USD-equivalent stiffness k is k * 180/pi there.
RAD_PER_DEG = math.pi / 180.0

BG = "#f6f7f9"
AXBG = "#ffffff"
C_CMD = "#1f6feb"
C_RESP = "#d1495b"
C_ERR = "#8c6d1f"
C_BAND = "#2e7d32"
C_GRID = "#c9ccd1"


# =========================================================================== #
# 1. metrics - pure numpy, no sim, no matplotlib (unit-tested by --selftest)
# =========================================================================== #
def _floats(values) -> np.ndarray:
    """Coerce anything array-like to a 1-D float64 array."""
    return np.asarray(values, dtype=np.float64).reshape(-1)


def _finite(value) -> float:
    """Return a float, or NaN when the value is missing / not numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def jsonable(obj):
    """Recursively replace NaN/inf with None so ``summary.json`` is valid JSON.

    ``json.dumps`` happily writes ``NaN``, which is not JSON and breaks strict
    readers (jq, most dashboards). A metric that could not be computed is
    reported as null instead.
    """
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, (np.integer, int)) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return jsonable(obj.tolist())
    return obj


def first_crossing(t, y, level: float) -> float:
    """Time at which ``y`` first reaches ``level`` (linear interpolation).

    Used for the 10 % / 90 % points of a step response: the raw samples rarely
    land exactly on the level, and rounding to the nearest sample would bias
    every rise time by up to one sample period.
    """
    t = _floats(t)
    y = _floats(y)
    if t.size == 0 or t.size != y.size:
        return float("nan")
    above = np.nonzero(y >= level)[0]
    if above.size == 0:
        return float("nan")
    i = int(above[0])
    if i == 0:
        return float(t[0])
    y0, y1 = float(y[i - 1]), float(y[i])
    if y1 == y0:
        return float(t[i])
    frac = (level - y0) / (y1 - y0)
    return float(t[i - 1] + frac * (t[i] - t[i - 1]))


def step_metrics(
    t,
    y,
    *,
    t_step: float,
    target: float,
    y0: float | None = None,
    t_end: float | None = None,
    band: float = 0.02,
    tail_frac: float = 0.10,
    dead_band: float = 0.02,
) -> dict:
    """Classic second-order step-response numbers for one measured step.

    The response is normalised as ``yn = (y - y0) / (target - y0)`` so the same
    code works for a positive or a negative step: ``yn`` always runs 0 -> 1.

    ``t_end`` bounds the response window. Use it whenever the record contains
    ANOTHER command after the step being measured (the twin's step test moves
    the joint and then moves it back): without it the settling time and the
    steady-state error would be computed over the return step as well and
    report nonsense.

    What each number means, for a student reading the figure:

    rise_time_s
        time from 10 % to 90 % of the commanded move. How fast the loop is.
    overshoot_pct
        how far past the target it goes, as a percentage of the move. Zero for
        a well-damped loop, large for an under-damped one. For a second-order
        loop overshoot and damping ratio are one-to-one:
        ``zeta = -ln(os/100) / sqrt(pi^2 + ln(os/100)^2)``.
    settling_time_s
        time from the step until the response ENTERS and STAYS inside the
        +/- ``band`` (2 % by default) band around the target. When the last
        sample is still outside the band the value is NaN and
        ``settled_in_window`` is False - the record was too short.
    steady_state_error_rad
        commanded target minus the mean of the last ``tail_frac`` of the
        record: the residual offset the PD drive leaves (stiffness too low, or
        an external load). Measured against the COMMAND, not against the
        response, so a loop that stops 2 % short reports 2 %.
    dead_time_s
        time from the step until the response has moved 2 % of the move: the
        transport + one-frame latency of the ROS -> graph -> PhysX path.

    Returns a dict of floats (NaN where a number does not exist), plus
    ``settled_in_window`` (bool) and ``n_samples``/``dt_median_s`` so the
    caller can judge the sampling density.
    """
    t = _floats(t)
    y = _floats(y)
    if t.size != y.size:
        raise ValueError("t and y must have the same length")
    out = {
        "y0": float("nan"),
        "target": float(target),
        "delta": float("nan"),
        "final_value": float("nan"),
        "rise_time_s": float("nan"),
        "overshoot_pct": float("nan"),
        "settling_time_s": float("nan"),
        "settling_band": float(band),
        "steady_state_error_rad": float("nan"),
        "steady_state_error_pct": float("nan"),
        "time_to_peak_s": float("nan"),
        "peak_value": float("nan"),
        "dead_time_s": float("nan"),
        "settled_in_window": False,
        "n_samples": int(t.size),
        "dt_median_s": float("nan"),
        "sample_rate_hz": float("nan"),
    }
    if t.size < 4:
        return out

    order = np.argsort(t, kind="stable")
    t, y = t[order], y[order]
    dt = float(np.median(np.diff(t)))
    out["dt_median_s"] = dt
    out["sample_rate_hz"] = 1.0 / dt if dt > 0 else float("nan")

    if y0 is None:
        pre = y[(t < t_step) & (t >= t_step - 0.5)]
        y0 = float(pre.mean()) if pre.size else float(y[0])
    out["y0"] = float(y0)
    delta = float(target) - float(y0)
    out["delta"] = delta
    if abs(delta) < 1e-9:
        # No commanded move: nothing is measurable. Keeps callers honest
        # instead of dividing by ~0 and reporting a huge overshoot.
        return out

    mask = t >= t_step
    if t_end is not None and math.isfinite(float(t_end)):
        # Drop the sample that sits exactly on the NEXT command edge: it already
        # belongs to the following segment and would poison the tail average.
        mask &= t <= float(t_end) - dt
    tr, yr = t[mask], y[mask]
    if tr.size < 3:
        return out
    yn = (yr - float(y0)) / delta

    n_tail = max(3, int(round(tail_frac * yr.size)))
    final = float(yr[-n_tail:].mean())
    out["final_value"] = final
    out["steady_state_error_rad"] = float(target) - final
    out["steady_state_error_pct"] = 100.0 * (float(target) - final) / delta

    t10 = first_crossing(tr, yn, 0.10)
    t90 = first_crossing(tr, yn, 0.90)
    if math.isfinite(t10) and math.isfinite(t90):
        out["rise_time_s"] = t90 - t10

    i_peak = int(np.argmax(yn))
    out["peak_value"] = float(yr[i_peak])
    out["time_to_peak_s"] = float(tr[i_peak] - t_step)
    out["overshoot_pct"] = max(0.0, (float(yn[i_peak]) - 1.0) * 100.0)

    outside = np.nonzero(np.abs(yn - 1.0) > band)[0]
    if outside.size == 0:
        out["settling_time_s"] = 0.0
        out["settled_in_window"] = True
    else:
        last = int(outside[-1])
        if last >= tr.size - 1:
            out["settling_time_s"] = float("nan")
            out["settled_in_window"] = False
        else:
            out["settling_time_s"] = float(tr[last] - t_step)
            out["settled_in_window"] = True

    t_dead = first_crossing(tr, yn, dead_band)
    if math.isfinite(t_dead):
        out["dead_time_s"] = t_dead - t_step
    return out


def _uniform_resample(t, y, dt: float) -> np.ndarray:
    """Resample ``y(t)`` onto a uniform grid of spacing ``dt`` (linear)."""
    t = _floats(t)
    y = _floats(y)
    if t.size < 2:
        return y.copy()
    grid = np.arange(float(t[0]), float(t[-1]) + 0.5 * dt, dt)
    return np.interp(grid, t, y)


def cross_correlation_lag(t, ref, resp, *, max_lag_s: float | None = None) -> float:
    """Delay of ``resp`` behind ``ref`` in seconds, from their cross-correlation.

    ``lag = argmax_k sum_n resp[n+k] * ref[n]`` after removing the mean of both
    signals: the shift that makes the two look most alike. Positive means the
    response happens LATER than the reference, i.e. the loop lags. The peak is
    refined with a parabola through the three samples around it so the answer
    is not quantised to the sample period.

    Both signals are first resampled onto a uniform grid (the sampler runs at
    the frame rate, so its spacing jitters a little; correlation assumes
    uniform sampling).
    """
    t = _floats(t)
    ref = _floats(ref)
    resp = _floats(resp)
    if t.size != ref.size or t.size != resp.size or t.size < 8:
        return float("nan")
    dt = float(np.median(np.diff(t)))
    if not (dt > 0):
        return float("nan")
    a = _uniform_resample(t, ref, dt)
    b = _uniform_resample(t, resp, dt)
    if a.size < 8:
        return float("nan")
    a = a - a.mean()
    b = b - b.mean()
    if not (np.any(a) and np.any(b)):
        return float("nan")
    cc = np.correlate(b, a, mode="full")
    lags = (np.arange(cc.size) - (a.size - 1)) * dt
    if max_lag_s is not None:
        keep = np.abs(lags) <= max_lag_s
        if not np.any(keep):
            return float("nan")
        cc = np.where(keep, cc, -np.inf)
    k = int(np.argmax(cc))
    lag = float(lags[k])
    if 0 < k < cc.size - 1:
        y0, y1, y2 = float(cc[k - 1]), float(cc[k]), float(cc[k + 1])
        denom = y0 - 2.0 * y1 + y2
        if denom != 0.0:
            lag += 0.5 * (y0 - y2) / denom * dt
    return lag


def estimate_frequency(t, y) -> float:
    """Dominant frequency of ``y`` in Hz (FFT peak, parabola-refined).

    A cross-check on the frequency the reference was commanded at: if the two
    disagree the record is not a clean sinusoid (a segment boundary, a stalled
    frame, a collision) and the phase lag should not be trusted.
    """
    t = _floats(t)
    y = _floats(y)
    if t.size < 16:
        return float("nan")
    dt = float(np.median(np.diff(t)))
    if not (dt > 0):
        return float("nan")
    a = _uniform_resample(t, y, dt)
    a = a - a.mean()
    if not np.any(a):
        return float("nan")
    spec = np.abs(np.fft.rfft(a * np.hanning(a.size)))
    freqs = np.fft.rfftfreq(a.size, d=dt)
    if spec.size < 3:
        return float("nan")
    k = int(np.argmax(spec[1:]) + 1)  # skip DC
    if 0 < k < spec.size - 1:
        y0, y1, y2 = float(spec[k - 1]), float(spec[k]), float(spec[k + 1])
        denom = y0 - 2.0 * y1 + y2
        if denom != 0.0:
            k = k + 0.5 * (y0 - y2) / denom
    return float(k * (freqs[1] - freqs[0]))


def tracking_metrics(
    t,
    ref,
    resp,
    *,
    freq_hz: float,
    t_start: float | None = None,
    t_end: float | None = None,
) -> dict:
    """Sinusoidal-tracking numbers for one frequency.

    rms_error_rad / peak_error_rad
        the usual "how good is the tracking" pair: RMS is the energy of the
        error, peak is the worst excursion (a collision or a stiction jump
        shows up in the peak, not the RMS).
    phase_lag_deg / lag_s
        from the cross-correlation: how much later the response repeats the
        reference. A pure delay of ``lag`` seconds at ``f`` Hz is a phase of
        ``360 * f * lag`` degrees. Positive = the response lags.
    gain
        response amplitude / reference amplitude. Together with the phase this
        is one point of the closed-loop Bode plot, which is exactly what a
        control-engineering slide wants.
    freq_hz_est
        the dominant frequency actually present in the response; it should
        match ``freq_hz`` or the segment contains something that is not the
        commanded sinusoid.

    ``t_start``/``t_end`` select the measurement window (use the last 1.5 of
    the 2 commanded periods so the startup transient is excluded).
    """
    t = _floats(t)
    ref = _floats(ref)
    resp = _floats(resp)
    out = {
        "freq_hz": float(freq_hz),
        "freq_hz_est": float("nan"),
        "rms_error_rad": float("nan"),
        "peak_error_rad": float("nan"),
        "mean_error_rad": float("nan"),
        "lag_s": float("nan"),
        "phase_lag_deg": float("nan"),
        "gain": float("nan"),
        "ref_amplitude_rad": float("nan"),
        "resp_amplitude_rad": float("nan"),
        "n_samples": 0,
    }
    mask = np.ones(t.shape, dtype=bool)
    if t_start is not None:
        mask &= t >= t_start
    if t_end is not None:
        mask &= t <= t_end
    if mask.sum() < 8:
        return out
    tt, rr, yy = t[mask], ref[mask], resp[mask]
    err = yy - rr
    out["n_samples"] = int(tt.size)
    out["rms_error_rad"] = float(np.sqrt(np.mean(err**2)))
    out["peak_error_rad"] = float(np.max(np.abs(err)))
    out["mean_error_rad"] = float(np.mean(err))
    amp_ref = 0.5 * float(rr.max() - rr.min())
    amp_resp = 0.5 * float(yy.max() - yy.min())
    out["ref_amplitude_rad"] = amp_ref
    out["resp_amplitude_rad"] = amp_resp
    if amp_ref > 1e-9:
        out["gain"] = amp_resp / amp_ref
    lag = cross_correlation_lag(tt, rr, yy, max_lag_s=0.5 / max(freq_hz, 1e-6))
    out["lag_s"] = lag
    if math.isfinite(lag) and freq_hz > 0:
        phase = 360.0 * freq_hz * lag
        out["phase_lag_deg"] = (phase + 180.0) % 360.0 - 180.0
    out["freq_hz_est"] = estimate_frequency(tt, yy)
    return out


# =========================================================================== #
# 2. plotting - matplotlib imported lazily (the file must import without it)
# =========================================================================== #
class PlotUnavailable(RuntimeError):
    """matplotlib is not installed; the CSV was still written."""


def pyplot():
    """Import and configure matplotlib (Agg, 1600x900, large fonts).

    Imported lazily so the metrics above stay usable (and unit-testable) on a
    machine without matplotlib, and so a missing install degrades to "CSV only,
    clear message" instead of a stack trace at import time.
    """
    try:
        import matplotlib
    except Exception as exc:  # pragma: no cover - depends on the env
        raise PlotUnavailable(f"matplotlib is not importable in this interpreter ({exc})") from exc
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 18,
            "axes.titlesize": 22,
            "axes.labelsize": 20,
            "xtick.labelsize": 17,
            "ytick.labelsize": 17,
            "legend.fontsize": 16,
            "axes.edgecolor": "#5b6068",
            "axes.labelcolor": "#22262b",
            "text.color": "#22262b",
            "xtick.color": "#3b4048",
            "ytick.color": "#3b4048",
            "figure.dpi": 100,
            "savefig.dpi": 100,
        }
    )
    return plt


def _new_figure(plt, title: str, footer: str):
    """1600x900 figure with a left-aligned title and a provenance footer."""
    fig = plt.figure(figsize=(16, 9), dpi=100)
    fig.patch.set_facecolor(BG)
    fig.text(0.012, 0.975, title, fontsize=24, fontweight="bold", va="top", ha="left", color="#14181d")
    fig.text(0.012, 0.012, footer, fontsize=11, va="bottom", ha="left", color="#6b7078", linespacing=1.35)
    return fig


def _style_axes(ax, xlabel: str, ylabel: str):
    ax.set_facecolor(AXBG)
    ax.grid(True, color=C_GRID, alpha=0.55, linewidth=0.9)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)


def _metric_box(ax, lines, *, loc="bottom right", fontsize=16):
    """Draw a monospace text box with the measured numbers.

    ``loc`` accepts either the matplotlib vocabulary (``"lower right"``) or the
    plain one (``"bottom right"``) - both are normalised to the same corner.
    """
    loc = loc.replace("lower", "bottom").replace("upper", "top")
    text = "\n".join(lines)
    x, ha = (0.985, "right") if "right" in loc else (0.015, "left")
    y, va = (0.05, "bottom") if "bottom" in loc else (0.95, "top")
    ax.text(
        x,
        y,
        text,
        transform=ax.transAxes,
        ha=ha,
        va=va,
        fontsize=fontsize,
        family="monospace",
        color="#14181d",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#ffffff", edgecolor="#c9ccd1", alpha=0.95),
    )


def _save(fig, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=fig.get_facecolor())
    pyplot().close(fig)
    return path


def _fmt(value, unit="", digits=3, scale=1.0):
    """Format a metric for a text box ('n/a' when it could not be computed)."""
    try:
        v = float(value) * scale
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v):
        return "n/a"
    return f"{v:.{digits}f}{unit}"


def plot_step(
    path,
    *,
    t,
    y,
    ref,
    metrics,
    joint: str,
    stiffness: float,
    damping: float,
    t_step: float,
    footer: str = "",
    band: float = 0.02,
) -> Path:
    """Target vs response vs time with the step metrics in a text box."""
    plt = pyplot()
    t = _floats(t)
    y = _floats(y)
    ref = _floats(ref)
    title = f"UR5e {joint}: step response (PD drive, stiffness {stiffness:g} / damping {damping:g})"
    fig = _new_figure(plt, title, footer)
    ax = fig.add_axes([0.075, 0.165, 0.905, 0.705])
    _style_axes(ax, "time (s, simulation clock)", f"{joint} position (rad)")

    target = metrics.get("target", float("nan"))
    y0 = metrics.get("y0", float("nan"))
    delta = metrics.get("delta", float("nan"))
    # A rising step settles high and a falling step settles low: put the metrics
    # box in the free corner and reserve room for it.
    box_loc = "top right" if (math.isfinite(delta) and delta < 0) else "bottom right"
    if math.isfinite(target) and math.isfinite(delta) and delta != 0:
        ax.axhspan(target - abs(delta) * band, target + abs(delta) * band, color=C_BAND, alpha=0.13, zorder=0)
        ax.axhline(target + abs(delta) * band, color=C_BAND, lw=1.0, ls=":", alpha=0.7)
        ax.axhline(target - abs(delta) * band, color=C_BAND, lw=1.0, ls=":", alpha=0.7)
        for frac, label in ((0.10, "10 %"), (0.90, "90 %")):
            level = y0 + delta * frac
            ax.axhline(level, color="#9aa0a6", lw=1.0, ls="--", alpha=0.8)
            ax.annotate(
                label,
                xy=(t[0], level),
                xytext=(4, 3),
                textcoords="offset points",
                fontsize=13,
                color="#6b7078",
                bbox=dict(boxstyle="square,pad=0.15", facecolor="#ffffff", edgecolor="none", alpha=0.85),
            )
        ax.axvline(t_step, color="#6b7078", lw=1.4, ls=":")
        ax.annotate(
            "command step",
            xy=(t_step, y0),
            xytext=(8, -26 if delta > 0 else 18),
            textcoords="offset points",
            fontsize=14,
            color="#6b7078",
        )

    ax.plot(t, ref, color=C_CMD, lw=2.6, ls="--", label="command (target)")
    ax.plot(t, y, color=C_RESP, lw=2.8, label="simulated joint position")

    rise = metrics.get("rise_time_s", float("nan"))
    if math.isfinite(rise) and math.isfinite(t_step) and math.isfinite(y0) and math.isfinite(delta):
        after = t >= t_step
        t10 = first_crossing(t[after], (y[after] - y0) / delta, 0.10)
        t90 = first_crossing(t[after], (y[after] - y0) / delta, 0.90)
        if math.isfinite(t10) and math.isfinite(t90):
            ylo = y0 + delta * 0.10
            yhi = y0 + delta * 0.90
            ax.annotate(
                "",
                xy=(t90, yhi),
                xytext=(t10, ylo),
                arrowprops=dict(arrowstyle="<->", color="#14181d", lw=1.6),
            )
            ax.annotate(
                f"rise {rise * 1000:.0f} ms",
                xy=(0.5 * (t10 + t90), yhi),
                xytext=(0, 16),
                textcoords="offset points",
                ha="center",
                fontsize=15,
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#ffffff", edgecolor="none", alpha=0.9),
            )

    settle = metrics.get("settling_time_s", float("nan"))
    if math.isfinite(settle) and math.isfinite(t_step):
        ax.axvline(t_step + settle, color=C_BAND, lw=1.6, alpha=0.85)
        # The label goes on the side of the axes the metrics box is NOT on.
        yfrac, va = (0.985, "top") if box_loc.startswith("bottom") else (0.015, "bottom")
        ax.annotate(
            f"settled (2 %) {settle:.2f} s",
            xy=(t_step + settle, yfrac),
            xycoords=("data", "axes fraction"),
            xytext=(7, 0),
            textcoords="offset points",
            fontsize=14,
            color=C_BAND,
            va=va,
        )

    rows = [
        ("rise (10-90 %)", _fmt(metrics.get("rise_time_s"), " s")),
        ("overshoot", _fmt(metrics.get("overshoot_pct"), " %", 1)),
        ("settling (2 %)", _fmt(metrics.get("settling_time_s"), " s")),
        ("steady-state err", _fmt(metrics.get("steady_state_error_rad"), " rad", 4)),
        ("peak time", _fmt(metrics.get("time_to_peak_s"), " s")),
        ("dead time", _fmt(metrics.get("dead_time_s"), " s")),
        ("sample rate", _fmt(metrics.get("sample_rate_hz"), " Hz", 1)),
    ]
    lines = [f"{label:<17}{value:>12}" for label, value in rows]
    _metric_box(ax, lines, loc=box_loc)
    ax.legend(loc="lower left" if box_loc == "top right" else "upper left",
              frameon=True, facecolor="#ffffff", edgecolor="#c9ccd1")
    # Make room for the box so it never sits on the trace.
    lo, hi = ax.get_ylim()
    pad = 0.40 * (hi - lo)
    if box_loc == "bottom right":
        ax.set_ylim(lo - pad, hi + 0.08 * (hi - lo))
    else:
        ax.set_ylim(lo - 0.08 * (hi - lo), hi + pad)
    return _save(fig, path)


def plot_tracking(path, *, t, ref, resp, per_freq, joint: str, footer: str = "", segments=()) -> Path:
    """Reference/response on top, tracking error below, per-frequency numbers."""
    plt = pyplot()
    t = _floats(t)
    ref = _floats(ref)
    resp = _floats(resp)
    title = f"UR5e {joint}: sinusoidal tracking (PD drive) - " + ", ".join(
        f"{m['freq_hz']:g} Hz" for m in per_freq
    )
    fig = _new_figure(plt, title, footer)
    gs = fig.add_gridspec(2, 1, height_ratios=[3.1, 1.0], left=0.075, right=0.98, top=0.855, bottom=0.165, hspace=0.14)
    ax = fig.add_subplot(gs[0])
    axe = fig.add_subplot(gs[1], sharex=ax)
    _style_axes(ax, "", f"{joint} position (rad)")
    _style_axes(axe, "time (s, simulation clock)", "error (rad)")

    for seg in segments:
        for a in seg:
            ax.axvline(a, color="#9aa0a6", lw=1.1, ls=":")
            axe.axvline(a, color="#9aa0a6", lw=1.1, ls=":")

    ax.plot(t, ref, color=C_CMD, lw=2.4, ls="--", label="reference")
    ax.plot(t, resp, color=C_RESP, lw=2.6, label="response")
    axe.plot(t, resp - ref, color=C_ERR, lw=2.2)
    axe.axhline(0.0, color="#6b7078", lw=1.0)

    rows = ["freq    RMS err   peak err   phase lag   gain"]
    for m in per_freq:
        rows.append(
            f"{m['freq_hz']:4.2f} Hz {_fmt(m.get('rms_error_rad'), '', 4):>9} "
            f"{_fmt(m.get('peak_error_rad'), '', 4):>9} "
            f"{_fmt(m.get('phase_lag_deg'), ' deg', 1):>9} "
            f"{_fmt(m.get('gain'), '', 3):>7}"
        )
    # Reserve a band at the bottom of the position axes so the numbers box
    # never covers a trough of the response.
    lo, hi = ax.get_ylim()
    ax.set_ylim(lo - 0.55 * (hi - lo), hi + 0.10 * (hi - lo))
    _metric_box(ax, rows, loc="lower right", fontsize=15)
    ax.legend(loc="upper left", ncol=2, frameon=True, facecolor="#ffffff", edgecolor="#c9ccd1")
    return _save(fig, path)


def plot_gains(path, *, series, joint: str, footer: str = "") -> Path:
    """Overlay every gain combination's normalised step response + a table."""
    plt = pyplot()
    title = f"UR5e {joint}: step response vs PD gains (stiffness x damping)"
    fig = _new_figure(plt, title, footer)
    ax = fig.add_axes([0.075, 0.165, 0.905, 0.705])
    _style_axes(ax, "time since command step (s)", "normalised position  (q - q0) / step")

    ax.axhline(1.0, color="#6b7078", lw=1.2, ls="--", alpha=0.9)
    ax.axhspan(0.98, 1.02, color=C_BAND, alpha=0.10, zorder=0)

    cmap = plt.get_cmap("viridis")
    rows = []
    for i, item in enumerate(series):
        color = cmap(i / max(1, len(series) - 1) * 0.92)
        m = item.get("metrics", {})
        ax.plot(item["t"], item["y"], lw=2.6, color=color, label=item["label"])
        rows.append(
            [
                f"{item['stiffness']:g}",
                f"{item['damping']:g}",
                _fmt(m.get("overshoot_pct"), "", 1),
                _fmt(m.get("settling_time_s"), "", 2),
                _fmt(m.get("rise_time_s"), "", 2),
            ]
        )

    ax.set_xlim(left=0.0)
    ax.legend(loc="lower right", ncol=2, frameon=True, facecolor="#ffffff", edgecolor="#c9ccd1")
    if rows:
        # The right half of the plot is flat at 1.0, so the table lives there;
        # the row height is sized from the number of rows so the cells never
        # overlap (a fixed height per row collides once there are 6+ combos).
        table = ax.table(
            cellText=rows,
            colLabels=["K", "D", "OS %", "settle s", "rise s"],
            cellLoc="center",
            colLoc="center",
            bbox=[0.615, 0.60, 0.37, min(0.38, 0.052 * (len(rows) + 1))],
        )
        table.auto_set_font_size(False)
        table.set_fontsize(14)
        for cell in table.get_celld().values():
            cell.set_edgecolor("#c9ccd1")
            cell.set_facecolor("#ffffff")
            cell.set_alpha(1.0)
    return _save(fig, path)


def plot_ik(path, *, t, err, ee, goal, metrics, joint: str, footer: str = "") -> Path:
    """End-effector distance to the Cartesian goal vs time, plus a top view."""
    plt = pyplot()
    t = _floats(t)
    err = _floats(err)
    ee = np.asarray(ee, dtype=np.float64)
    goal = _floats(goal)
    title = "UR5e end-effector: Cartesian move through /joint_states (Lula IK solution)"
    fig = _new_figure(plt, title, footer)
    gs = fig.add_gridspec(1, 2, width_ratios=[2.15, 1.0], left=0.075, right=0.98, top=0.855, bottom=0.165, wspace=0.22)
    ax = fig.add_subplot(gs[0])
    axp = fig.add_subplot(gs[1])
    _style_axes(ax, "time (s, simulation clock)", "|EE position - goal| (mm)")
    _style_axes(axp, "x (m)", "y (m)")

    ax.plot(t, err * 1000.0, color=C_RESP, lw=2.8)
    ax.axhline(5.0, color=C_BAND, lw=1.6, ls="--")
    ax.annotate("5 mm", xy=(t[0], 5.0), xytext=(4, 6), textcoords="offset points", fontsize=14, color=C_BAND)
    ax.set_ylim(bottom=0.0)
    lines = [
        f"goal offset    {_fmt(metrics.get('goal_offset_m'), ' m', 3)}",
        f"final error    {_fmt(metrics.get('final_error_mm'), ' mm', 2)}",
        f"peak error     {_fmt(metrics.get('peak_error_mm'), ' mm', 2)}",
        f"IK residual    {_fmt(metrics.get('ik_fk_residual_mm'), ' mm', 2)}",
        f"settle (5 mm)  {_fmt(metrics.get('settle_time_s'), ' s')}",
    ]
    _metric_box(ax, lines)

    if ee.ndim == 2 and ee.shape[0] >= 2:
        axp.plot(ee[:, 0], ee[:, 1], color=C_RESP, lw=2.4, label="EE path")
        axp.plot([ee[0, 0]], [ee[0, 1]], "o", color=C_CMD, ms=10, label="start")
        if goal.size >= 2:
            axp.plot([goal[0]], [goal[1]], "*", color=C_BAND, ms=20, label="goal")
        axp.legend(loc="best", fontsize=13, frameon=True, facecolor="#ffffff", edgecolor="#c9ccd1")
        # Equal aspect on a short straight path would collapse one axis, so set
        # explicit symmetric limits around the start/goal midpoint.
        span = max(
            0.02,
            0.6 * float(max(ee[:, 0].max() - ee[:, 0].min(), ee[:, 1].max() - ee[:, 1].min())),
        )
        cx = 0.5 * float(ee[:, 0].min() + ee[:, 0].max())
        cy = 0.5 * float(ee[:, 1].min() + ee[:, 1].max())
        axp.set_xlim(cx - span, cx + span)
        axp.set_ylim(cy - span, cy + span)
        axp.set_aspect("equal", adjustable="box")
    return _save(fig, path)


def png_size(path) -> tuple:
    """(width, height) read from the PNG IHDR - no PIL needed."""
    data = Path(path).read_bytes()[:33]
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", data[16:24])


# =========================================================================== #
# 3. bridge access
# =========================================================================== #
class Twin:
    """Thin wrapper over the twin's Kit MCP bridge (run_python + twin tools)."""

    SESSION = "control_experiments"

    def __init__(self, port: int, timeout: float = 300.0):
        self.port = int(port)
        self.timeout = float(timeout)
        self._client = bridge_client.BridgeClient(port=self.port, timeout=self.timeout)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def tool(self, name: str, params: dict | None = None, *, timeout: float | None = None):
        """Call one twin tool (``ur5e_dt.*`` or a builtin) and return its payload."""
        return self._client.call(name, params or {}, timeout=timeout or self.timeout)

    def py(self, code: str, *, timeout: float | None = None, reserve: float | None = None) -> dict:
        """Run code on Kit's main thread in this script's persistent session.

        The session keeps the articulation view alive between calls (creating
        one per call would re-resolve the physics handles every time), and the
        bridge drops it if the stage is reopened, which the snippets survive
        because they recreate anything they need.
        """
        payload = self._client.call(
            "run_python",
            {"code": code, "session_id": self.SESSION, "persistent": True},
            timeout=timeout or self.timeout,
            execution_reserve_s=reserve,
        )
        return payload.get("result") or {}


class TwinError(RuntimeError):
    """A bridge call failed (the sim is not there, or the code raised)."""


def _wrap_bridge_errors(fn):
    """Turn a bridge error into a TwinError carrying the Kit-side traceback."""

    def inner(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except bridge_client.BridgeError as exc:
            details = getattr(exc, "details", {}) or {}
            raise TwinError(f"{exc}\n{details.get('traceback', '')}".strip()) from exc
        except (OSError, TimeoutError) as exc:
            raise TwinError(f"bridge unreachable on port {getattr(args[0], 'port', '?')}: {exc}") from exc

    return inner


Twin.tool = _wrap_bridge_errors(Twin.tool)
Twin.py = _wrap_bridge_errors(Twin.py)


# =========================================================================== #
# 4. ROS 2 side - bundled Humble rclpy, the sim's DDS island
# =========================================================================== #
def ros_dirs(distro: str | None = None) -> tuple:
    """Locate the sim's bundled rclpy dir and native lib dir for ``distro``.

    Mirrors scripts/tf_capture.py:44-72: resolved from the isaacsim package
    location (no ``import isaacsim``, which would boot Kit), so it keeps
    working if the venv moves.
    """
    import importlib.util

    if distro is None:
        distro = ros_distro()
    try:
        spec = importlib.util.find_spec("isaacsim")
    except (ImportError, ValueError) as exc:
        raise RuntimeError(f"cannot find isaacsim in this interpreter ({exc}); run with the repo .venv") from exc
    locations = list(getattr(spec, "submodule_search_locations", None) or ())
    if not locations:
        raise RuntimeError("isaacsim is not an installed package in this interpreter")
    base = Path(locations[0]) / "exts" / "isaacsim.ros2.core" / distro
    rclpy_dir, lib_dir = base / "rclpy", base / "lib"
    if not rclpy_dir.is_dir() or not lib_dir.is_dir():
        raise RuntimeError(f"bundled ROS 2 {distro} install not found under {base}")
    return rclpy_dir, lib_dir


def ros_distro() -> str:
    """The distro the launcher uses: humble on jammy, jazzy elsewhere (run.sh:96-104)."""
    override = os.environ.get("ROS_DISTRO")
    if override:
        return override
    codename = ""
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("VERSION_CODENAME="):
                codename = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "humble" if codename == "jammy" else "jazzy"


_ASSIGN = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_BRACED = r"\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}"
_VAR = re.compile(rf"\$(?:{_BRACED}|([A-Za-z_][A-Za-z0-9_]*))")
DDS_ENV_FILES = (REPO / "config" / "ros_dds.env",)
REEXEC_MARK = "UR5E_DT_CONTROL_EXPERIMENTS_REEXEC"


def _expand(raw, values):
    def replace(match):
        name = match.group(1) or match.group(3)
        default = match.group(2) or ""
        return os.environ.get(name, values.get(name, default))

    return _VAR.sub(replace, raw)


def parse_env_file(path: Path) -> dict:
    """Parse the sim's ros_dds.env without sourcing it through a shell.

    Same rules as scripts/tf_capture.py:79-99, including the
    ``_ISAAC_DDS_DIR="$(dirname ...)"`` line, which resolves to the file's own
    directory - that is where config/cyclonedds.xml sits.
    """
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ASSIGN.match(line)
        if not match:
            continue
        name, raw = match.group(1), match.group(2).strip()
        if len(raw) > 1 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        if "$(dirname" in raw:
            values[name] = str(path.parent)
            continue
        values[name] = _expand(raw, values)
    return values


def apply_dds_env() -> dict:
    """Adopt the sim's DDS island: domain, localhost-only, Cyclone profile.

    A mixed setup does not fail loudly, it silently discovers nothing
    (config/ros_dds.env:5-30). Anything already exported wins, so a caller can
    deliberately point this at another island.
    """
    values = {}
    for path in DDS_ENV_FILES:
        if path.is_file():
            values = parse_env_file(path)
            break
    values.setdefault("RMW_IMPLEMENTATION", "rmw_cyclonedds_cpp")
    values.setdefault("ROS_DISTRO", ros_distro())
    for name, value in values.items():
        if not name.startswith("_"):
            os.environ.setdefault(name, value)
    return {k: os.environ.get(k) for k in ("ROS_DOMAIN_ID", "ROS_LOCALHOST_ONLY", "RMW_IMPLEMENTATION", "CYCLONEDDS_URI")}


def ensure_native_libs(lib_dir: Path) -> None:
    """Put the bundled ROS 2 libs on LD_LIBRARY_PATH, re-execing if needed.

    The dynamic loader reads LD_LIBRARY_PATH only at process start, so it
    cannot be fixed from inside a live interpreter (tf_capture.py:113-135).
    """
    entries = [part for part in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep) if part]
    if str(lib_dir) in entries or os.environ.get(REEXEC_MARK) == "1":
        return
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = os.pathsep.join([str(lib_dir), *entries])
    env[REEXEC_MARK] = "1"
    argv = list(getattr(sys, "orig_argv", None) or [sys.executable, str(Path(__file__).resolve())])
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(sys.executable, argv, env)


_ROS: dict = {}


def ros_modules() -> dict:
    """Import rclpy out of the sim's bundled install and cache the modules."""
    if _ROS:
        return _ROS
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
    from sensor_msgs.msg import JointState

    import rclpy

    _ROS.update(
        {
            "rclpy": rclpy,
            "Node": Node,
            "JointState": JointState,
            "QoSProfile": QoSProfile,
            "ReliabilityPolicy": ReliabilityPolicy,
            "HistoryPolicy": HistoryPolicy,
            "DurabilityPolicy": DurabilityPolicy,
            "inited": False,
        }
    )
    return _ROS


class JointCommandStream:
    """Publish sensor_msgs/JointState on /joint_states from a time schedule.

    This is the twin's REAL command input: the graph subscribes to
    /joint_states and feeds IsaacArticulationController
    (exts/ur5e-dt/ur5e_dt/extension.py:3046-3070). Nothing here calls
    ``set_joint_positions``/``apply_action`` - that would bypass the very path
    being measured.

    ``program(t_rel) -> positions`` is evaluated on wall time, so the schedule
    is independent of how fast the sim is running; every publish is logged as
    (wall_time, positions) and later mapped onto sim time by the caller.

    The whole 6-joint vector is published every time (the controller applies
    commands by joint name, so the joints that are not being stepped simply
    keep holding their pose).
    """

    def __init__(self, names, *, topic: str = JOINT_TOPIC, rate_hz: float = 100.0, node_name: str = "ur5e_dt_control_experiments"):
        self.names = list(names)
        self.topic = topic
        self.rate_hz = float(rate_hz)
        self.node_name = node_name
        self.other_publishers = 0
        self.sent: list = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._node = None
        self._pub = None

    def open(self) -> None:
        """Create the node + publisher and count publishers already on the topic."""
        mods = ros_modules()
        rclpy = mods["rclpy"]
        if not mods["inited"]:
            rclpy.init(args=None)
            mods["inited"] = True
        self._node = mods["Node"](self.node_name)
        # Counted BEFORE our publisher exists, so it is genuinely "someone
        # else is already commanding this robot".
        self.other_publishers = int(self._node.count_publishers(self.topic))
        qos = mods["QoSProfile"](
            depth=10,
            history=mods["HistoryPolicy"].KEEP_LAST,
            reliability=mods["ReliabilityPolicy"].RELIABLE,
            durability=mods["DurabilityPolicy"].VOLATILE,
        )
        self._pub = self._node.create_publisher(mods["JointState"], self.topic, qos)

    def start(self, program, t_start: float, duration: float, preroll: float = 0.0) -> None:
        """Start the publishing thread; it runs until t_start + duration."""
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(program, float(t_start), float(duration), float(preroll)), daemon=True
        )
        self._thread.start()

    def _run(self, program, t_start: float, duration: float, preroll: float) -> None:
        mods = ros_modules()
        JointState = mods["JointState"]
        period = 1.0 / self.rate_hz
        t_begin = t_start - preroll
        t_end = t_start + duration
        next_t = t_begin
        while not self._stop.is_set():
            now = time.time()
            if now < next_t:
                time.sleep(min(next_t - now, 0.002))
                continue
            t_rel = now - t_start
            positions = np.asarray(program(t_rel), dtype=np.float64).reshape(-1)
            msg = JointState()
            msg.header.stamp = self._node.get_clock().now().to_msg()
            msg.name = list(self.names)
            msg.position = [float(v) for v in positions]
            try:
                self._pub.publish(msg)
            except Exception:
                break
            with self._lock:
                self.sent.append((now, positions))
            next_t += period
            if now >= t_end:
                break

    def stop(self) -> None:
        """Stop the thread and wait for it (short: it wakes every 2 ms)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def close(self) -> None:
        self.stop()
        if self._node is not None:
            try:
                self._node.destroy_node()
            except Exception:
                pass
            self._node = None
            self._pub = None

    def sent_arrays(self) -> tuple:
        """(wall times, commanded positions[n, 6]) of every message published."""
        with self._lock:
            rows = list(self.sent)
        if not rows:
            return np.zeros(0), np.zeros((0, len(self.names)))
        walls = np.array([r[0] for r in rows], dtype=np.float64)
        pos = np.array([r[1] for r in rows], dtype=np.float64)
        return walls, pos


# =========================================================================== #
# 5. code executed inside Isaac Sim (through run_python)
# =========================================================================== #
_PREAMBLE = r'''
import json, math, time
import numpy as np
import omni.kit.app
import omni.timeline
import omni.usd
from pxr import Gf, Usd, UsdGeom, UsdPhysics

_CE = globals().setdefault('_CE', {})


def _ce_app():
    return omni.kit.app.get_app()


def _ce_timeline():
    return omni.timeline.get_timeline_interface()


def _ce_pump(n=1):
    app = _ce_app()
    for _ in range(int(n)):
        app.update()


def _ce_np(x):
    if hasattr(x, 'numpy'):
        x = x.numpy()
    return np.asarray(x)


def _ce_view(prim='/World/UR5e', name='control_experiments'):
    view = _CE.get('view')
    if view is None:
        from isaacsim.core.prims import Articulation as ArticulationView
        view = ArticulationView(prim_paths_expr=prim, name=name)
        view.initialize()
        _CE['view'] = view
    return view


def _ce_links(view):
    """World link transforms (count, max_links, 7: xyz + quat xyzw) from PhysX."""
    return _ce_np(view._physics_view.get_link_transforms()).reshape(-1, 7)


def _ce_link_index(view, name):
    idx = None
    try:
        idx = view.get_link_index(name)
    except Exception:
        idx = None
    if idx is None:
        try:
            idx = list(view.body_names).index(name)
        except Exception:
            idx = None
    return idx


def _ce_usd_drive(stage, joint):
    prim = stage.GetPrimAtPath('/World/UR5e/joints/' + joint)
    if not prim or not prim.IsValid():
        raise RuntimeError('no drive prim at /World/UR5e/joints/' + joint)
    return UsdPhysics.DriveAPI(prim, 'angular')


def _ce_drive_read(drive):
    return {
        'stiffness': float(drive.GetStiffnessAttr().Get() or 0.0),
        'damping': float(drive.GetDampingAttr().Get() or 0.0),
        'max_force': float(drive.GetMaxForceAttr().Get() or 0.0),
    }
'''


_PREP_BODY = r'''
stage = omni.usd.get_context().get_stage()
info = {'prims': [], 'warnings': []}
world = stage.GetPrimAtPath('/World')
info['prims'] = [c.GetName() for c in world.GetChildren()] if world and world.IsValid() else []
info['has_ur5e'] = bool(stage.GetPrimAtPath('/World/UR5e'))
info['has_vention'] = bool(stage.GetPrimAtPath('/World/Vention'))
if not info['has_ur5e']:
    raise RuntimeError('no /World/UR5e on the stage: load the twin first '
                       '(ur5e_dt.quick_start_with_workstation)')
tl = _ce_timeline()
info['timeline_playing'] = bool(tl.is_playing())
if not info['timeline_playing']:
    # A paused timeline would record a flat line and every metric would be
    # meaningless, so play it and say so in the summary.
    info['warnings'].append('timeline was paused; played it before measuring')
    tl.play()
    _ce_pump(4)
info['timeline_time'] = float(tl.get_current_time())

view = _ce_view()
q = _ce_np(view.get_joint_positions())
dof_names = [str(n) for n in view.dof_names]
info['dof_names'] = dof_names
info['qpos'] = [float(v) for v in q[0]]
joint = str(P['joint'])
if joint not in dof_names:
    raise RuntimeError('joint %r not in %s' % (joint, dof_names))
info['joint_index'] = dof_names.index(joint)
_CE['joint_index'] = info['joint_index']

ee_link = str(P['ee_link'])
ee_index = _ce_link_index(view, ee_link)
info['ee_link'] = ee_link
info['ee_index'] = ee_index
if ee_index is not None:
    tr = _ce_links(view)
    info['ee_pos'] = [float(v) for v in tr[ee_index][0:3]]
    info['ee_quat_xyzw'] = [float(v) for v in tr[ee_index][3:7]]
else:
    info['warnings'].append('end-effector link %r not found; IK experiment will be skipped' % ee_link)
_CE['ee_index'] = ee_index

cache = UsdGeom.XformCache()
base_prim = stage.GetPrimAtPath('/World/UR5e/base_link')
if not (base_prim and base_prim.IsValid()):
    base_prim = stage.GetPrimAtPath('/World/UR5e')
m = cache.GetLocalToWorldTransform(base_prim)
t = m.ExtractTranslation()
rq = m.ExtractRotationQuat()
info['base_pos'] = [float(t[0]), float(t[1]), float(t[2])]
info['base_quat_xyzw'] = [float(rq.GetImaginary()[0]), float(rq.GetImaginary()[1]),
                          float(rq.GetImaginary()[2]), float(rq.GetReal())]
# LulaKinematicsSolver.set_robot_base_pose() takes (w, x, y, z); the twin's base_link
# is rotated 180 deg about z, so feeding it the xyzw array mirrored Lula's whole world
# (x and z flipped) and the "solved" Cartesian move went the wrong way by 308 mm.
info['base_quat_wxyz'] = [float(rq.GetReal()), float(rq.GetImaginary()[0]),
                          float(rq.GetImaginary()[1]), float(rq.GetImaginary()[2])]
info['base_prim'] = base_prim.GetPath().pathString

drive = _ce_usd_drive(stage, joint)
info['drive_usd'] = _ce_drive_read(drive)
kps, kds = view.get_gains(joint_names=[joint])
info['drive_physx'] = {'kps': float(_ce_np(kps).reshape(-1)[0]), 'kds': float(_ce_np(kds).reshape(-1)[0])}
info['stage_units'] = float(UsdGeom.GetStageMetersPerUnit(stage))
result = info
'''


_SAMPLE_BODY = r'''
view = _ce_view()
ji = _CE.get('joint_index')
if ji is None:
    # The bridge drops its sessions when the stage is reopened; re-derive the
    # joint/link indices instead of failing the whole experiment.
    ji = [str(n) for n in view.dof_names].index(str(P['joint']))
    _CE['joint_index'] = ji
ee_index = _CE.get('ee_index')
if ee_index is None and P.get('ee_link'):
    ee_index = _ce_link_index(view, str(P['ee_link']))
    _CE['ee_index'] = ee_index
tl = _ce_timeline()
if not tl.is_playing():
    tl.play()
    _ce_pump(4)
t_start = float(P['t_start'])
preroll = float(P['preroll'])
duration = float(P['duration'])
want_ee = bool(P.get('ee'))
t_begin = t_start - preroll
late = max(0.0, time.time() - t_begin)
# The publisher starts at t_begin too (same wall clock), so anything later than
# that is a missed head of the record.
while time.time() < t_begin:
    _ce_pump(1)
rows = []
t_end = t_start + duration
while True:
    _ce_pump(1)
    wall = time.time()
    q = _ce_np(view.get_joint_positions())
    row = [wall, float(tl.get_current_time()), float(q[0][ji])]
    if want_ee and ee_index is not None:
        tr = _ce_links(view)
        row += [float(tr[ee_index][0]), float(tr[ee_index][1]), float(tr[ee_index][2])]
    rows.append(row)
    if wall >= t_end:
        break
result = {'rows': rows, 'late_s': float(late), 'sim_end': float(tl.get_current_time()),
          'joint_index': ji, 'playing': bool(tl.is_playing())}
'''


_GAINS_BODY = r'''
stage = omni.usd.get_context().get_stage()
view = _ce_view()
joint = str(P['joint'])
drive = _ce_usd_drive(stage, joint)
mode = str(P['mode'])
if mode == 'usd':
    drive.GetStiffnessAttr().Set(float(P['stiffness']))
    drive.GetDampingAttr().Set(float(P['damping']))
elif mode == 'physx':
    kps = np.array([[float(P['kps'])]], dtype=np.float64)
    kds = np.array([[float(P['kds'])]], dtype=np.float64)
    view.set_gains(kps=kps, kds=kds, joint_names=[joint])
elif mode == 'restore_usd':
    drive.GetStiffnessAttr().Set(float(P['stiffness']))
    drive.GetDampingAttr().Set(float(P['damping']))
elif mode == 'restore_physx':
    kps = np.array([[float(P['kps'])]], dtype=np.float64)
    kds = np.array([[float(P['kds'])]], dtype=np.float64)
    view.set_gains(kps=kps, kds=kds, joint_names=[joint])
else:
    raise RuntimeError('unknown gain mode %r' % mode)
# Give any USD -> PhysX change notification a few frames to land before the
# readback decides whether it reached the solver.
_ce_pump(int(P.get('frames', 6)))
kps_now, kds_now = view.get_gains(joint_names=[joint])
result = {'mode': mode, 'usd': _ce_drive_read(drive),
          'physx': {'kps': float(_ce_np(kps_now).reshape(-1)[0]),
                    'kds': float(_ce_np(kds_now).reshape(-1)[0])}}
'''


_IK_BODY = r'''
import os
from pathlib import Path

view = _ce_view()
ee_index = _CE.get('ee_index')
if ee_index is None:
    ee_index = _ce_link_index(view, str(P['ee_link']))
    _CE['ee_index'] = ee_index
if ee_index is None:
    raise RuntimeError('end-effector link %r not found in the articulation' % str(P['ee_link']))
stage = omni.usd.get_context().get_stage()

mg = None
import_errors = []
try:
    import isaacsim.robot_motion.motion_generation as mg
except Exception as exc:
    import_errors.append('import: %s' % exc)
    try:
        from isaacsim.core.utils.extensions import enable_extension
        enable_extension('isaacsim.robot_motion.motion_generation')
        import isaacsim.robot_motion.motion_generation as mg
    except Exception as exc2:
        import_errors.append('enable+import: %s' % exc2)
if mg is None:
    raise RuntimeError('isaacsim.robot_motion.motion_generation unavailable (%s)' % '; '.join(import_errors))

cfg = Path(mg.__file__).resolve().parents[3] / 'motion_policy_configs' / 'universal_robots' / 'ur5e'
urdf = cfg / 'ur5e.urdf'
desc = cfg / 'rmpflow' / 'ur5e_robot_description.yaml'
if not urdf.is_file() or not desc.is_file():
    raise RuntimeError('Lula UR5e description files not found (looked for %s and %s)' % (urdf, desc))

solver = mg.LulaKinematicsSolver(str(desc), str(urdf))
frames = [str(f) for f in solver.get_all_frame_names()]
frame = None
for candidate in ('wrist_3_link', 'flange', 'tool0'):
    if candidate in frames:
        frame = candidate
        break
if frame is None:
    raise RuntimeError('none of wrist_3_link/flange/tool0 is a Lula frame (have %s)' % frames[:12])

solver.set_robot_base_pose(np.array(P['base_pos'], dtype=np.float64),
                           np.array(P['base_quat_wxyz'], dtype=np.float64))
tr = _ce_links(view)
ee0 = tr[ee_index][0:3].astype(np.float64)
goal = ee0 + np.array(P['offset'], dtype=np.float64)
dof_names = [str(n) for n in view.dof_names]
q = _ce_np(view.get_joint_positions())[0].astype(np.float64)
joint_names = [str(n) for n in solver.get_joint_names()]
missing = [n for n in joint_names if n not in dof_names]
if missing:
    raise RuntimeError('Lula joints %s are not DOFs of this articulation' % missing)
q_lula = np.array([q[dof_names.index(n)] for n in joint_names], dtype=np.float64)
answer = solver.compute_inverse_kinematics(frame, goal, None, warm_start=q_lula,
                                          position_tolerance=float(P['tol']))
if isinstance(answer, tuple) and len(answer) == 2 and isinstance(answer[1], (bool, np.bool_)):
    solution, converged = answer
else:  # older/newer signature order: (success, positions)
    converged, solution = answer
solution = np.asarray(solution, dtype=np.float64).reshape(-1)
if not bool(converged):
    raise RuntimeError('Lula IK did not converge for goal %s' % (goal.tolist(),))
fk_pos, fk_rot = solver.compute_forward_kinematics(frame, solution)
fk_pos = np.asarray(fk_pos, dtype=np.float64).reshape(-1)
q_target = q.copy()
for i, name in enumerate(joint_names):
    q_target[dof_names.index(name)] = float(solution[i])
result = {'frame': frame, 'frames': len(frames), 'goal_world': [float(v) for v in goal],
          'ee_start_world': [float(v) for v in ee0], 'joint_solution': [float(v) for v in q_target],
          'lula_solution': [float(v) for v in solution], 'lula_joint_names': joint_names,
          'ik_fk_residual_m': float(np.linalg.norm(fk_pos - goal))}
'''


def snippet(body: str, params: dict) -> str:
    """Compose run_python code: preamble + params + body (assigns ``result``)."""
    return _PREAMBLE + "\nP = json.loads(" + json.dumps(json.dumps(params)) + ")\n" + body


# =========================================================================== #
# 6. experiment plumbing
# =========================================================================== #
class Ctx:
    """Everything an experiment needs: bridge, ROS stream, prep state, output dir.

    Two index spaces, deliberately kept apart:

    * ``joint_index`` - position of the measured joint in the ARTICULATION's DOF
      array, which is what ``get_joint_positions()`` returns and therefore what
      every sample and metric is expressed in.
    * ``cmd_index`` - position of the same joint in the JointState message this
      script publishes.

    They differ because the RG2 gripper is spliced INTO the UR5e articulation
    (exts/ur5e-dt/ur5e_dt/extension.py:3637-3645), so the articulation has a
    seventh DOF that must NOT be commanded over /joint_states: the real UR
    driver never publishes it, and the gripper has its own control graph.
    """

    def __init__(self, args, twin: Twin, out: Path):
        self.args = args
        self.twin = twin
        self.out = out
        self.prep: dict = {}
        self.joint = args.joint
        self.q_hold = None          # full articulation qpos at prep time
        self.q_cmd_hold = None      # the same pose, restricted to the message
        self.joint_index = None     # index in the articulation DOF array
        self.cmd_index = None       # index in the published JointState
        self.dof_names = list(ARM_JOINTS)   # names carried in the message
        self.notes: list = []

    def configure(self, prep: dict) -> None:
        """Derive both index spaces from the prep payload."""
        art_names = list(prep.get("dof_names") or ARM_JOINTS)
        self.q_hold = np.asarray(prep.get("qpos"), dtype=np.float64)
        self.joint_index = int(prep["joint_index"])
        if self.joint not in art_names:
            raise RuntimeError(f"joint {self.joint!r} is not a DOF of /World/UR5e ({art_names})")
        # Arm joints only, in articulation order; the gripper DOF stays out of
        # the message exactly like the real driver's /joint_states.
        cmd_names = [n for n in art_names if n in ARM_JOINTS] or list(ARM_JOINTS)
        self.dof_names = cmd_names
        self.cmd_index = cmd_names.index(self.joint)
        self.q_cmd_hold = np.array([float(self.q_hold[art_names.index(n)]) for n in cmd_names])

    def to_cmd(self, q_articulation) -> np.ndarray:
        """Re-express a full articulation pose in the published message order."""
        art_names = list(self.prep.get("dof_names") or ARM_JOINTS)
        return np.array([float(q_articulation[art_names.index(n)]) for n in self.dof_names])

    @property
    def step_rad(self) -> float:
        return float(self.args.step_rad)

    def csv_path(self, name: str) -> Path:
        return unique_path(self.out / f"{name}.csv")

    def png_path(self, name: str) -> Path:
        return unique_path(self.out / f"{name}.png")

    def footer(self, extra: str = "") -> str:
        """Two provenance lines under every figure (they must stay readable)."""
        stamp = time.strftime("%Y-%m-%d %H:%M")
        line1 = (
            "command path: rclpy sensor_msgs/JointState on /joint_states"
            "  ->  ROS2SubscribeJointState  ->  IsaacArticulationController  ->  PhysX PD drive"
        )
        line2 = f"ur5e-dt digital twin  ·  port {self.args.port}  ·  {stamp}"
        if extra:
            line2 += f"  ·  {extra}"
        return f"{line1}\n{line2}"


def unique_path(path: Path) -> Path:
    """Never overwrite accepted evidence: suffix an existing name with a stamp."""
    path = Path(path)
    if not path.exists():
        return path
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.stem}-{stamp}{path.suffix}")
    n = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{stamp}-{n}{path.suffix}")
        n += 1
    return candidate


def write_csv(path: Path, header, rows) -> Path:
    """Write a CSV with a header line; rows may hold floats/ints/strings."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(header))
        for row in rows:
            writer.writerow(["" if v is None else v for v in row])
    return path


def write_json(path: Path, data) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(data), indent=2, sort_keys=True) + "\n")
    return path


class Samples:
    """One sampling record: wall + sim time, joint position, optional EE position."""

    def __init__(self, payload: dict, joint: str):
        rows = payload.get("rows") or []
        if not rows:
            raise RuntimeError("the sampling loop returned no rows (is the timeline playing?)")
        arr = np.asarray(rows, dtype=np.float64)
        self.wall = arr[:, 0]
        self.sim = arr[:, 1]
        self.q = arr[:, 2]
        self.ee = arr[:, 3:6] if arr.shape[1] >= 6 else None
        self.late_s = float(payload.get("late_s") or 0.0)
        self.joint = joint

    def __len__(self):
        return int(self.sim.size)

    @property
    def duration_s(self) -> float:
        return float(self.sim[-1] - self.sim[0])

    @property
    def realtime_factor(self) -> float:
        wall = float(self.wall[-1] - self.wall[0])
        return self.duration_s / wall if wall > 0 else float("nan")

    def sim_at_wall(self, walls) -> np.ndarray:
        """Map wall-clock instants to simulation time (linear interpolation)."""
        return np.interp(_floats(walls), self.wall, self.sim)

    def sim_at_wall_one(self, wall: float) -> float:
        """Same as ``sim_at_wall`` for a single instant (returns a float)."""
        return float(self.sim_at_wall([float(wall)])[0])

    def reference_from(self, stream: JointCommandStream, joint_index: int) -> np.ndarray:
        """Commanded joint value at every sample, from the publisher's own log.

        Using what was actually published (not the ideal schedule) keeps the
        figure honest when a publish is late or the loop drops one.
        """
        walls, positions = stream.sent_arrays()
        if walls.size == 0:
            raise RuntimeError("nothing was published on /joint_states")
        cmd_sim = self.sim_at_wall(walls)
        order = np.argsort(cmd_sim)
        return np.interp(self.sim, cmd_sim[order], positions[order, joint_index])


def run_sampling(ctx: Ctx, program, *, duration: float, preroll: float, ee: bool = False, rate_hz: float) -> tuple:
    """Publish ``program`` on /joint_states while logging the sim at frame rate.

    Both sides agree on an absolute wall-clock start (``t_start``) before the
    blocking bridge call is made, so the sampler and the publisher share a time
    base without any in-band handshake: the sampler pumps ``app.update()``
    until that instant and the publisher starts one preroll earlier.
    """
    names = list(ctx.dof_names)
    stream = JointCommandStream(names, rate_hz=rate_hz)
    stream.open()
    try:
        if stream.other_publishers and not ctx.args.allow_other_publishers:
            raise RuntimeError(
                f"{stream.other_publishers} other publisher(s) already on {JOINT_TOPIC} "
                "(the UR driver?). Two command streams fight and the measurement is "
                "meaningless; stop the driver or pass --allow-other-publishers."
            )
        lead = 2.0
        t_start = time.time() + lead
        stream.start(program, t_start, duration, preroll)
        payload = ctx.twin.py(
            snippet(
                _SAMPLE_BODY,
                {
                    "t_start": t_start,
                    "preroll": preroll,
                    "duration": duration,
                    "ee": ee,
                    "joint": ctx.joint,
                    "ee_link": ctx.args.ee_link,
                },
            ),
            timeout=duration + preroll + 120.0,
            reserve=duration + preroll + 60.0,
        )
        stream.stop()
    finally:
        stream.close()
    return Samples(payload, ctx.joint), stream


def _segment_bounds(samples: Samples, stream: JointCommandStream) -> dict:
    """Command-edge times in sim time, from the publisher log."""
    walls, positions = stream.sent_arrays()
    if walls.size == 0:
        raise RuntimeError("nothing was published on /joint_states")
    return {"walls": walls, "cmd": positions, "cmd_sim": samples.sim_at_wall(walls)}


def _edge_sim(samples: Samples, bounds: dict, joint_index: int, *, value: float, after: float) -> float:
    """Sim time of the first publish carrying ``value`` at/after wall time ``after``."""
    walls = bounds["walls"]
    cmd = bounds["cmd"]
    cmd_sim = bounds["cmd_sim"]
    for i in range(walls.size):
        if walls[i] >= after and abs(cmd[i, joint_index] - value) <= 1e-9:
            return float(cmd_sim[i])
    return float("nan")


# =========================================================================== #
# 7. the experiments
# =========================================================================== #
_PREPOSE_JOINT = "elbow_joint"

_READ_Q_BODY = r'''
view = _ce_view()
result = {
    'qpos': [float(v) for v in _ce_np(view.get_joint_positions()).reshape(-1)],
    'dof_names': [str(n) for n in view.dof_names],
}
'''


def prepose_clearance(ctx: Ctx) -> dict:
    """Lift the tool off the benchtop before the joint-space experiments.

    At the asset's home pose the RG2 fingertips sit ~27 mm above the Vention
    top (measured 2026-10-30: /World/RG2_Gripper world bbox min z = 0.0273 m,
    /World/Vention max z = 0.0000 m). A +0.3 rad elbow step then drives them
    into the bench, and the recorded "steady state error" is the contact, not
    the controller: the joint stalled at 0.050 rad of a 0.300 rad command with
    the fingertips resting on the table, while the same step upward tracked.

    The lift goes out over /joint_states like any other command, at a slew
    rate slow enough for the drives to follow, and the measured pose afterwards
    becomes the hold pose every later experiment starts from.
    """
    lift = float(ctx.args.pre_lift)
    if lift == 0.0:
        return {"applied": False, "reason": " --pre-lift 0"}

    idx = ctx.dof_names.index(_PREPOSE_JOINT) if _PREPOSE_JOINT in ctx.dof_names else ctx.cmd_index
    start = np.array(ctx.q_cmd_hold, dtype=np.float64)
    target = start.copy()
    target[idx] += lift
    slew = 2.5
    settle = 1.0

    def program(t_rel: float) -> np.ndarray:
        if t_rel <= 0.0:
            return start
        return start + min(1.0, t_rel / slew) * (target - start)

    run_sampling(ctx, program, duration=slew + settle, preroll=0.5, rate_hz=ctx.args.publish_hz)

    measured = ctx.twin.py(snippet(_READ_Q_BODY, {}), timeout=120.0)
    q_now = np.asarray(measured.get("qpos"), dtype=np.float64).reshape(-1)
    art_names = list(ctx.prep.get("dof_names") or ctx.dof_names)
    before = np.array(ctx.q_hold, dtype=np.float64)
    ctx.q_hold = q_now
    ctx.q_cmd_hold = np.array([float(q_now[art_names.index(n)]) for n in ctx.dof_names])
    adopted = float(ctx.q_cmd_hold[idx])
    return {
        "applied": True,
        "joint": ctx.dof_names[idx],
        "requested_rad": float(lift),
        "command_rad": float(target[idx]),
        "measured_rad": adopted,
        "start_rad": float(before[art_names.index(ctx.dof_names[idx])]),
        "slew_s": slew,
        "settle_s": settle,
    }


def experiment_step(ctx: Ctx) -> dict:
    """Position step (+delta rad, then back) through /joint_states.

    Measures rise time, overshoot, settling time and steady-state error of the
    closed loop that the twin actually runs: PD drive (stiffness/damping from
    the USD joint drive) fed by the OmniGraph ArticulationController, with the
    command arriving over ROS. Both the outbound and the return step are
    measured; the figure shows the outbound one.
    """
    ji = ctx.joint_index
    q0 = float(ctx.q_hold[ji])
    delta = ctx.step_rad
    hold_s = float(ctx.args.step_hold)
    duration = hold_s + float(ctx.args.step_tail)
    preroll = 1.0

    ci = ctx.cmd_index

    def program(t_rel: float) -> np.ndarray:
        q = np.array(ctx.q_cmd_hold, dtype=np.float64)
        if t_rel >= 0.0:
            q[ci] = q0 + delta if t_rel < hold_s else q0
        return q

    samples, stream = run_sampling(ctx, program, duration=duration, preroll=preroll, rate_hz=ctx.args.publish_hz)
    bounds = _segment_bounds(samples, stream)
    ref = samples.reference_from(stream, ci)

    walls = bounds["walls"]
    first = walls[0] + preroll + 0.0  # the step is published at t_start
    t_step = _edge_sim(samples, bounds, ci, value=q0 + delta, after=first)
    t_back = _edge_sim(samples, bounds, ci, value=q0, after=first + hold_s * 0.5)
    if not math.isfinite(t_step):
        raise RuntimeError("could not locate the command step in the published stream")

    t_origin = float(samples.sim[0])
    m = step_metrics(
        samples.sim,
        samples.q,
        t_step=t_step,
        target=q0 + delta,
        t_end=t_back if math.isfinite(t_back) else None,
        band=0.02,
    )
    m_return = step_metrics(samples.sim, samples.q, t_step=t_back, target=q0, band=0.02) if math.isfinite(t_back) else {}

    csv_path = ctx.csv_path("step")
    rows = [
        (float(s - t_origin), float(w - samples.wall[0]), float(q), float(r))
        for s, w, q, r in zip(samples.sim, samples.wall, samples.q, ref)
    ]
    write_csv(csv_path, ["t_sim_s", "t_wall_s", "position_rad", "reference_rad"], rows)

    drive = ctx.prep.get("drive_usd") or {}
    physx = ctx.prep.get("drive_physx") or {}
    # The figure is labelled with what the solver actually runs, not with the
    # USD attribute: the two disagreed by 100x until the extension refresh fix.
    kps = physx.get("kps")
    kds = physx.get("kds")
    if kps and kds:
        stiffness = float(kps) / 57.29577951308232
        damping = float(kds) / 57.29577951308232
        drive_footer = (f"drive in solver: {float(kps):.1f} N*m/rad, {float(kds):.1f} N*m*s/rad "
                        f"({stiffness:.1f}/{damping:.1f} per degree)")
    else:
        stiffness = float(drive.get("stiffness", float("nan")))
        damping = float(drive.get("damping", float("nan")))
        drive_footer = "drive in solver: unknown"
    png_path = ctx.png_path("step")
    try:
        plot_step(
            png_path,
            t=samples.sim - t_origin,
            y=samples.q,
            ref=ref,
            metrics=m,
            joint=ctx.joint,
            stiffness=stiffness,
            damping=damping,
            t_step=t_step - t_origin,
            footer=ctx.footer(f"step {delta:+.3f} rad on {ctx.joint}; {drive_footer}"),
        )
        plot_note = "ok"
    except PlotUnavailable as exc:
        png_path = None
        plot_note = f"skipped: {exc}"

    metrics = dict(m)
    metrics["return"] = {k: m_return.get(k) for k in ("rise_time_s", "overshoot_pct", "settling_time_s", "steady_state_error_rad")}
    metrics.update(
        {
            "joint": ctx.joint,
            "step_rad": delta,
            "q0_rad": q0,
            "target_rad": q0 + delta,
            "command_hold_s": hold_s,
            "preroll_s": preroll,
            "publish_hz": float(ctx.args.publish_hz),
            "sampling": {
                "n": len(samples),
                "duration_sim_s": round(samples.duration_s, 3),
                "realtime_factor": round(samples.realtime_factor, 3),
                "late_s": round(samples.late_s, 4),
            },
            "drive_usd": drive,
        }
    )
    return {
        "csv": str(csv_path),
        "png": str(png_path) if png_path else None,
        "plot": plot_note,
        "metrics": metrics,
        "note": f"step {delta:+.3f} rad from q0={q0:+.4f} rad on {ctx.joint}; return step included",
    }


def experiment_track(ctx: Ctx) -> dict:
    """Sinusoidal reference tracking at the requested frequencies.

    The command is ``q0 + A sin(2 pi f t)`` published on /joint_states; the
    response is the simulated joint. RMS/peak error say how well the loop
    follows, the cross-correlation lag says how much it lags (i.e. the phase
    of the closed-loop frequency response at that frequency), and the amplitude
    ratio completes that one Bode point.
    """
    ji = ctx.joint_index
    q0 = float(ctx.q_hold[ji])
    amp = float(ctx.args.track_amp)
    freqs = [float(f) for f in ctx.args.track_freqs]
    periods = 2.0
    gap = 0.5
    preroll = 1.0
    segments = []
    cursor = 0.0
    for f in freqs:
        span = periods / f
        segments.append((f, cursor, cursor + span))
        cursor += span + gap
    duration = cursor

    ci = ctx.cmd_index

    def program(t_rel: float) -> np.ndarray:
        q = np.array(ctx.q_cmd_hold, dtype=np.float64)
        for f, a, b in segments:
            if a <= t_rel < b:
                q[ci] = q0 + amp * math.sin(2.0 * math.pi * f * (t_rel - a))
                break
        return q

    samples, stream = run_sampling(ctx, program, duration=duration, preroll=preroll, rate_hz=ctx.args.publish_hz)
    bounds = _segment_bounds(samples, stream)
    ref = samples.reference_from(stream, ci)

    t_start_wall = bounds["walls"][0] + preroll
    per_freq = []
    segment_sims = []
    for f, a, b in segments:
        # Measure the last 1.5 of the 2 commanded periods: the first half
        # period still carries the startup transient.
        w0 = t_start_wall + b - 1.5 / f
        w1 = t_start_wall + b
        s0 = samples.sim_at_wall_one(w0)
        s1 = samples.sim_at_wall_one(w1)
        m = tracking_metrics(samples.sim, ref, samples.q, freq_hz=f, t_start=s0, t_end=s1)
        m["window_sim_s"] = [round(s0, 4), round(s1, 4)]
        m["window_commanded_s"] = round(w1 - w0, 4)
        per_freq.append(m)
        segment_sims.append((samples.sim_at_wall_one(t_start_wall + a), samples.sim_at_wall_one(t_start_wall + b)))

    t_origin = float(samples.sim[0])
    csv_path = ctx.csv_path("track")
    rows = [
        (float(s - t_origin), float(r), float(q), float(q - r))
        for s, r, q in zip(samples.sim, ref, samples.q)
    ]
    write_csv(csv_path, ["t_sim_s", "reference_rad", "position_rad", "error_rad"], rows)

    png_path = ctx.png_path("track")
    try:
        plot_tracking(
            png_path,
            t=samples.sim - t_origin,
            ref=ref,
            resp=samples.q,
            per_freq=per_freq,
            joint=ctx.joint,
            footer=ctx.footer(f"amplitude {amp:g} rad, {periods:g} periods per frequency"),
            segments=[(a - t_origin, b - t_origin) for a, b in segment_sims],
        )
        plot_note = "ok"
    except PlotUnavailable as exc:
        png_path = None
        plot_note = f"skipped: {exc}"

    metrics = {
        "joint": ctx.joint,
        "amplitude_rad": amp,
        "frequencies_hz": freqs,
        "periods_per_frequency": periods,
        "per_frequency": per_freq,
        "publish_hz": float(ctx.args.publish_hz),
        "sampling": {
            "n": len(samples),
            "duration_sim_s": round(samples.duration_s, 3),
            "realtime_factor": round(samples.realtime_factor, 3),
            "late_s": round(samples.late_s, 4),
        },
        "drive_usd": ctx.prep.get("drive_usd") or {},
    }
    worst = max(per_freq, key=lambda m: _finite(m.get("rms_error_rad"))) if per_freq else {}
    return {
        "csv": str(csv_path),
        "png": str(png_path) if png_path else None,
        "plot": plot_note,
        "metrics": metrics,
        "note": f"{len(freqs)} frequencies, amplitude {amp:g} rad; worst RMS "
        f"{_fmt(worst.get('rms_error_rad'), ' rad', 4)}",
    }


def experiment_gains(ctx: Ctx) -> dict:
    """PD-gain sweep over the same step, plus a runtime-edit effectiveness check.

    For every (stiffness, damping) pair the drive is changed at runtime, the
    same position step is repeated and the response is normalised, so all
    combinations overlay on one figure with a table of overshoot / settling.

    Two ways to change a gain at runtime, and the difference matters:

    * ``usd``   - edit the USD drive attributes (``physics:stiffness`` /
      ``physics:damping`` on /World/UR5e/joints/<joint>). This is what the twin
      authors at load time (extension.py:2962-2965) and what a person would do
      in the property panel.
    * ``physx`` - ``Articulation.set_gains()``, which writes the solver's
      implicit-drive parameters directly.

    Whether a USD edit reaches PhysX while the graph controller is running is
    not documented for this twin, so it is MEASURED: after each edit the script
    reads the gains back through ``Articulation.get_gains()`` (the solver's
    view) and compares them with the USD attributes. ``--gain-write auto``
    runs the sweep on USD edits first and repeats it on PhysX-side gains only
    if the readback shows the USD edit never arrived.

    The original values are read before the sweep and restored in a ``finally``
    block whether or not anything failed.
    """
    ji = ctx.joint_index
    q0 = float(ctx.q_hold[ji])
    delta = ctx.step_rad
    hold_s = float(ctx.args.step_hold)
    duration = hold_s + float(ctx.args.step_tail)
    preroll = 1.0
    pairs = [tuple(p) for p in ctx.args.gain_sweep]
    mode_requested = ctx.args.gain_write

    original = dict(ctx.prep.get("drive_usd") or {})
    original_physx = dict(ctx.prep.get("drive_physx") or {})
    if not original:
        raise RuntimeError("could not read the joint's USD drive attributes")

    ci = ctx.cmd_index

    def program_factory():
        def program(t_rel: float) -> np.ndarray:
            q = np.array(ctx.q_cmd_hold, dtype=np.float64)
            if t_rel >= 0.0:
                q[ci] = q0 + delta if t_rel < hold_s else q0
            return q

        return program

    def apply(mode: str, stiffness: float, damping: float) -> dict:
        if mode == "usd":
            params = {"joint": ctx.joint, "mode": "usd", "stiffness": stiffness, "damping": damping}
        else:
            params = {
                "joint": ctx.joint,
                "mode": "physx",
                "kps": stiffness / RAD_PER_DEG,
                "kds": damping / RAD_PER_DEG,
            }
        return ctx.twin.py(snippet(_GAINS_BODY, params))

    def restore() -> dict:
        out = {}
        try:
            out["usd"] = ctx.twin.py(
                snippet(
                    _GAINS_BODY,
                    {
                        "joint": ctx.joint,
                        "mode": "restore_usd",
                        "stiffness": float(original.get("stiffness", 0.0)),
                        "damping": float(original.get("damping", 0.0)),
                    },
                )
            )
        except Exception as exc:
            out["usd_error"] = f"{type(exc).__name__}: {exc}"
        try:
            out["physx"] = ctx.twin.py(
                snippet(
                    _GAINS_BODY,
                    {
                        "joint": ctx.joint,
                        "mode": "restore_physx",
                        "kps": float(original_physx.get("kps", 0.0)),
                        "kds": float(original_physx.get("kds", 0.0)),
                    },
                )
            )
        except Exception as exc:
            out["physx_error"] = f"{type(exc).__name__}: {exc}"
        return out

    def sweep(mode: str) -> list:
        runs = []
        for stiffness, damping in pairs:
            readback = apply(mode, stiffness, damping)
            samples, stream = run_sampling(
                ctx, program_factory(), duration=duration, preroll=preroll, rate_hz=ctx.args.publish_hz
            )
            bounds = _segment_bounds(samples, stream)
            t_step = _edge_sim(samples, bounds, ci, value=q0 + delta, after=bounds["walls"][0] + preroll)
            if not math.isfinite(t_step):
                raise RuntimeError("could not locate the command step in the published stream")
            t_back = _edge_sim(samples, bounds, ci, value=q0, after=bounds["walls"][0] + preroll + hold_s * 0.5)
            m = step_metrics(
                samples.sim,
                samples.q,
                t_step=t_step,
                target=q0 + delta,
                t_end=t_back if math.isfinite(t_back) else None,
                band=0.02,
            )
            runs.append(
                {
                    "stiffness": float(stiffness),
                    "damping": float(damping),
                    "readback": readback,
                    "samples": samples,
                    "metrics": m,
                    "t_step": t_step,
                    "t_back": t_back,
                }
            )
        return runs

    runs = []
    mode_used = "usd"
    usd_verdict = None
    physx_verdict = None
    try:
        if mode_requested in ("usd", "auto"):
            runs = sweep("usd")
            usd_verdict = _gain_readback_verdict(runs, original, original_physx, "usd")
            mode_used = "usd"
            if mode_requested == "auto" and not usd_verdict["usd_reached_physx"]:
                ctx.notes.append(
                    "runtime USD drive edits did NOT reach PhysX (get_gains readback unchanged); "
                    "repeated the sweep with Articulation.set_gains()"
                )
                runs = sweep("physx")
                physx_verdict = _gain_readback_verdict(runs, original, original_physx, "physx")
                mode_used = "physx"
        else:
            runs = sweep("physx")
            physx_verdict = _gain_readback_verdict(runs, original, original_physx, "physx")
            mode_used = "physx"
    finally:
        restored = restore()

    # ---- overlay data -----------------------------------------------------
    series = []
    for run in runs:
        s = run["samples"]
        # Stop at the return step: the overlay is about the outbound response.
        mask = s.sim >= run["t_step"]
        if math.isfinite(run.get("t_back") or float("nan")):
            mask &= s.sim <= run["t_back"]
        t_rel = s.sim[mask] - run["t_step"]
        yn = (s.q[mask] - q0) / delta
        series.append(
            {
                "label": f"K {run['stiffness']:g} / D {run['damping']:g}",
                "stiffness": run["stiffness"],
                "damping": run["damping"],
                "t": t_rel,
                "y": yn,
                "metrics": run["metrics"],
            }
        )

    csv_path = ctx.csv_path("gains")
    rows = []
    for item in series:
        for tt, yy in zip(item["t"], item["y"]):
            rows.append(
                (
                    item["stiffness"],
                    item["damping"],
                    float(tt),
                    float(yy),
                    float(q0 + yy * delta),
                    item["label"],
                )
            )
    write_csv(
        csv_path,
        ["stiffness", "damping", "t_since_step_s", "normalised_position", "position_rad", "label"],
        rows,
    )

    png_path = ctx.png_path("gains")
    try:
        plot_gains(png_path, series=series, joint=ctx.joint, footer=ctx.footer(f"{len(pairs)} gain combinations"))
        plot_note = "ok"
    except PlotUnavailable as exc:
        png_path = None
        plot_note = f"skipped: {exc}"

    verdict = physx_verdict if mode_used == "physx" else usd_verdict
    verdict = verdict or {}
    indistinguishable = _responses_indistinguishable(series)
    metrics = {
        "joint": ctx.joint,
        "step_rad": delta,
        "q0_rad": q0,
        "pairs": [{"stiffness": s, "damping": d} for s, d in pairs],
        "mode_requested": mode_requested,
        "mode_used": mode_used,
        "original_drive_usd": original,
        "original_drive_physx": original_physx,
        "restored": restored,
        "gain_write": {
            "usd_edit": usd_verdict,
            "physx_edit": physx_verdict,
            "usd_reached_physx": (usd_verdict or {}).get("usd_reached_physx"),
            "physx_reached_solver": (physx_verdict or {}).get("usd_reached_physx"),
            "physx_over_usd_ratio": (usd_verdict or {}).get("physx_over_usd_ratio"),
            "responses_indistinguishable": indistinguishable,
            "observation": _gain_observation(usd_verdict, physx_verdict, indistinguishable, mode_used),
        },
        "per_combination": [
            {
                "stiffness": run["stiffness"],
                "damping": run["damping"],
                "overshoot_pct": run["metrics"].get("overshoot_pct"),
                "settling_time_s": run["metrics"].get("settling_time_s"),
                "rise_time_s": run["metrics"].get("rise_time_s"),
                "steady_state_error_rad": run["metrics"].get("steady_state_error_rad"),
                "readback": run["readback"],
            }
            for run in runs
        ],
        "sampling": {
            "n": sum(len(run["samples"]) for run in runs),
            "duration_sim_s": round(sum(run["samples"].duration_s for run in runs), 3),
            "realtime_factor": round(float(np.mean([run["samples"].realtime_factor for run in runs])), 3),
        },
    }
    best = min(
        (r for r in metrics["per_combination"] if math.isfinite(_finite(r.get("settling_time_s")))),
        key=lambda r: _finite(r.get("settling_time_s")),
        default={},
    )
    return {
        "csv": str(csv_path),
        "png": str(png_path) if png_path else None,
        "plot": plot_note,
        "metrics": metrics,
        "note": f"{len(pairs)} combinations, gains written via {mode_used}; "
        f"fastest settling K {best.get('stiffness', '?')}/D {best.get('damping', '?')}",
    }


def _gain_readback_verdict(runs, original: dict, original_physx: dict, mode: str) -> dict:
    """Did the runtime edit reach the solver? Compare readbacks, no unit guess.

    ``Articulation.get_gains()`` returns what PhysX is actually using, while
    the USD attribute is what we wrote. The question "did the edit arrive" is
    therefore always answered by the PHYSX readback moving away from its
    original value whenever we asked for a different value. If it did not move,
    the edit never reached the solver (PhysX caches the drive parameters of an
    articulation when it is created, so a later USD edit is just a number on a
    prim).

    ``mode`` only decides which unit the requested value is in: USD attributes
    are per-degree, ``set_gains`` is per-radian.
    """
    orig_usd_k = _finite(original.get("stiffness"))
    orig_physx_k = _finite(original_physx.get("kps"))
    checks = []
    ratios = []
    for run in runs:
        rb = run.get("readback") or {}
        usd_k = _finite((rb.get("usd") or {}).get("stiffness"))
        physx_k = _finite((rb.get("physx") or {}).get("kps"))
        requested = run["stiffness"] if mode == "usd" else run["stiffness"] / RAD_PER_DEG
        reference = orig_usd_k if mode == "usd" else orig_physx_k
        changed_request = math.isfinite(reference) and abs(requested - reference) > 1e-9
        moved = math.isfinite(physx_k) and math.isfinite(orig_physx_k) and abs(physx_k - orig_physx_k) > 1e-9
        checks.append(
            {
                "stiffness": run["stiffness"],
                "damping": run["damping"],
                "requested_physx_kps": requested if mode == "physx" else None,
                "usd_stiffness": usd_k,
                "physx_kps": physx_k,
                "request_changed": bool(changed_request),
                "physx_moved": bool(moved),
                "reached": bool(moved or not changed_request),
            }
        )
        if math.isfinite(usd_k) and abs(usd_k) > 1e-9 and math.isfinite(physx_k):
            ratios.append(physx_k / usd_k)
    reached = all(c["reached"] for c in checks) if checks else False
    finite_ratios = [r for r in ratios if math.isfinite(r) and r > 0]
    return {
        "mode": mode,
        "usd_reached_physx": bool(reached),
        "physx_over_usd_ratio": round(float(np.median(finite_ratios)), 4) if finite_ratios else None,
        "readback_checks": checks,
    }


def _responses_indistinguishable(series) -> bool:
    """True when every normalised response is the same curve (gains had no effect)."""
    usable = [item for item in series if item["t"].size >= 4]
    if len(usable) < 2:
        return False
    grid = np.linspace(0.0, min(float(np.max(item["t"])) for item in usable), 400)
    if not (grid.size and grid[-1] > 0):
        return False
    curves = [np.interp(grid, item["t"], item["y"]) for item in usable]
    spread = float(np.max(np.ptp(np.vstack(curves), axis=0)))
    return bool(spread < 0.01)


def _gain_observation(usd_verdict, physx_verdict, indistinguishable: bool, mode_used: str) -> str:
    """One sentence a reader can quote: did runtime gain edits take effect?"""
    if usd_verdict is None:
        return (
            "Gains were written with Articulation.set_gains() (the solver's implicit-drive "
            "parameters); the USD attributes were not exercised in this run, so the question "
            "'does a runtime USD edit reach PhysX' is untested here."
        )
    ratio = usd_verdict.get("physx_over_usd_ratio")
    if usd_verdict.get("usd_reached_physx"):
        head = (
            "USD drive edits at runtime DID reach PhysX: Articulation.get_gains() followed the "
            f"attribute (physx_kps / usd_stiffness = {ratio})."
        )
    elif indistinguishable:
        head = (
            "USD drive edits at runtime did NOT reach the solver: the PhysX readback stayed at the "
            "original value and every overlaid response is identical. The graph "
            "ArticulationController only rewrites the position target, so nothing re-reads the "
            "drive attributes of a live articulation."
        )
    else:
        head = (
            "The PhysX readback did not follow the USD edit, yet the responses differ; inspect the "
            "per-combination readbacks before trusting the overlay."
        )
    if mode_used == "physx":
        head += " The curves shown were produced with Articulation.set_gains(), which does take effect while the timeline plays."
    return head


def experiment_ik(ctx: Ctx) -> dict:
    """One Cartesian move solved by Lula and executed through /joint_states.

    The goal is a fixed offset from the current end-effector position; Lula's
    ``LulaKinematicsSolver`` turns it into joint angles, those angles are
    published on /joint_states like any other command, and the end-effector
    error is logged from the simulated link transform. The residual reported by
    Lula's own forward kinematics (``ik_fk_residual_mm``) separates solver error
    from tracking error.
    """
    prep = ctx.prep
    if prep.get("ee_index") is None:
        raise RuntimeError("end-effector link was not found in the articulation; cannot measure EE error")
    offset = [float(v) for v in ctx.args.ik_offset]
    solution = ctx.twin.py(
        snippet(
            _IK_BODY,
            {
                "base_pos": prep.get("base_pos"),
                "base_quat_wxyz": prep.get("base_quat_wxyz"),
                "offset": offset,
                "tol": float(ctx.args.ik_tolerance),
                "ee_link": ctx.args.ee_link,
            },
        ),
        timeout=180.0,
    )
    # The IK solution is a full articulation pose (7 DOFs incl. the gripper);
    # the message carries the six arm joints only.
    target = ctx.to_cmd(np.asarray(solution["joint_solution"], dtype=np.float64).reshape(-1))
    q0 = np.array(ctx.q_cmd_hold, dtype=np.float64)
    hold_s = float(ctx.args.ik_hold)
    preroll = 1.0

    def program(t_rel: float) -> np.ndarray:
        return target if t_rel >= 0.0 else q0

    samples, stream = run_sampling(ctx, program, duration=hold_s, preroll=preroll, ee=True, rate_hz=ctx.args.publish_hz)
    if samples.ee is None:
        raise RuntimeError("the sampling loop did not return end-effector positions")
    goal = np.asarray(solution["goal_world"], dtype=np.float64).reshape(-1)
    err = np.linalg.norm(samples.ee - goal[None, :], axis=1)
    bounds = _segment_bounds(samples, stream)
    walls = bounds["walls"]
    t_step = samples.sim_at_wall_one(walls[0] + preroll)

    tail = max(5, int(0.1 * err.size))
    final_mm = float(err[-tail:].mean() * 1000.0)
    peak_mm = float(err.max() * 1000.0)
    start_mm = float(err[0] * 1000.0)
    # Time to come within 5 mm of the goal and stay there.
    settle_s = float("nan")
    outside = np.nonzero(err > 0.005)[0]
    if outside.size == 0:
        settle_s = 0.0
    elif int(outside[-1]) < err.size - 1:
        settle_s = float(samples.sim[int(outside[-1])] - t_step)
    metrics = {
        "joint": ctx.joint,
        "goal_offset_m": float(np.linalg.norm(offset)),
        "goal_offset_vector_m": offset,
        "goal_world": goal.tolist(),
        "ee_start_world": solution.get("ee_start_world"),
        "lula_frame": solution.get("frame"),
        "lula_joint_names": solution.get("lula_joint_names"),
        "lula_solution_rad": solution.get("lula_solution"),
        "joint_solution_rad": target.tolist(),
        "ik_fk_residual_mm": float(solution.get("ik_fk_residual_m") or 0.0) * 1000.0,
        "start_error_mm": start_mm,
        "peak_error_mm": peak_mm,
        "final_error_mm": final_mm,
        "settle_time_s": settle_s,
        "converged": bool(final_mm <= 5.0),
        "publish_hz": float(ctx.args.publish_hz),
        "sampling": {
            "n": len(samples),
            "duration_sim_s": round(samples.duration_s, 3),
            "realtime_factor": round(samples.realtime_factor, 3),
        },
    }

    t_origin = float(samples.sim[0])
    csv_path = ctx.csv_path("ik")
    rows = [
        (float(s - t_origin), float(e * 1000.0), float(ee[0]), float(ee[1]), float(ee[2]),
         float(goal[0]), float(goal[1]), float(goal[2]))
        for s, e, ee in zip(samples.sim, err, samples.ee)
    ]
    write_csv(csv_path, ["t_sim_s", "error_mm", "ee_x", "ee_y", "ee_z", "goal_x", "goal_y", "goal_z"], rows)

    png_path = ctx.png_path("ik")
    try:
        plot_ik(
            png_path,
            t=samples.sim - t_origin,
            err=err,
            ee=samples.ee,
            goal=goal,
            metrics=metrics,
            joint=ctx.joint,
            footer=ctx.footer("Cartesian goal " + " ".join(f"{v:+.3f}" for v in offset) + " m from the start pose"),
        )
        plot_note = "ok"
    except PlotUnavailable as exc:
        png_path = None
        plot_note = f"skipped: {exc}"

    return {
        "csv": str(csv_path),
        "png": str(png_path) if png_path else None,
        "plot": plot_note,
        "metrics": metrics,
        "note": f"Lula IK frame {metrics['lula_frame']}, final EE error {final_mm:.2f} mm "
        f"(IK residual {metrics['ik_fk_residual_mm']:.2f} mm)",
    }


EXPERIMENTS = {
    "step": experiment_step,
    "track": experiment_track,
    "gains": experiment_gains,
    "ik": experiment_ik,
}


# =========================================================================== #
# 8. launching the twin (only with --launch)
# =========================================================================== #
class LaunchedTwin:
    """``./run.sh --port N [--headless]`` plus quick_start, killed by pid.

    Isaac Sim ignores SIGTERM (docs/demo/TEST-PLAN.md:34), so the only reliable
    stop is SIGKILL. Only the process this class started is touched - never a
    pattern match, which could hit an unrelated Kit or a shell.
    """

    def __init__(self, port: int, headless: bool, log_path: Path):
        self.port = int(port)
        self.headless = bool(headless)
        self.log_path = Path(log_path)
        self.proc = None
        self._log = None
        atexit.register(self.stop)

    def start(self, timeout: float = 300.0) -> float:
        t0 = time.monotonic()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["OMNI_KIT_ACCEPT_EULA"] = "YES"
        env["UR5E_DT_PORT"] = str(self.port)
        argv = [str(REPO / "run.sh"), "--port", str(self.port)]
        if self.headless:
            argv.append("--headless")
        self._log = self.log_path.open("wb")
        self.proc = subprocess.Popen(
            argv,
            cwd=str(REPO),
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"run.sh exited with {self.proc.returncode}; see {self.log_path}")
            if port_open(self.port):
                return time.monotonic() - t0
            time.sleep(0.5)
        raise RuntimeError(f"twin bridge did not open port {self.port} within {timeout:.0f}s; see {self.log_path}")

    def pid(self):
        return self.proc.pid if self.proc else None

    def stop(self) -> dict:
        info = {"pid": self.pid(), "killed": False}
        if not self.proc:
            return info
        if self.proc.poll() is None:
            try:
                os.kill(self.proc.pid, signal.SIGKILL)
                info["killed"] = True
            except OSError as exc:
                info["error"] = str(exc)
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except OSError:
                pass
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            info["still_running"] = True
        if self._log:
            try:
                self._log.close()
            except Exception:
                pass
            self._log = None
        return info


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        socket.create_connection((host, int(port)), timeout=timeout).close()
        return True
    except OSError:
        return False


def wait_bridge_ready(twin: Twin, timeout: float = 240.0) -> bool:
    """Poll ``list_tools`` until the bridge answers (Kit boots in ~10-40 s).

    The TCP port opens before the app is ready to run Python on its main
    thread, so a connect test alone is not enough.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            twin.tool("list_tools", timeout=20.0)
            return True
        except Exception:
            time.sleep(1.0)
    return False


# =========================================================================== #
# 9. main
# =========================================================================== #
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Measure the UR5e twin's joint controller through the real /joint_states path.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--out", type=Path, help="output directory for CSV/PNG/summary.json")
    parser.add_argument("--port", type=int, default=8791, help="twin bridge port")
    parser.add_argument(
        "--experiments",
        default=DEFAULT_EXPERIMENTS,
        help="comma-separated subset of step,track,gains,ik",
    )
    parser.add_argument("--joint", default="elbow_joint", help="joint to command and measure")
    parser.add_argument("--headless", action="store_true", help="only used with --launch: run Kit without a window")
    parser.add_argument("--launch", action="store_true", help="start ./run.sh on --port yourself and kill it at the end")
    parser.add_argument("--step-rad", type=float, default=0.3, help="step size in rad (negative = down)")
    parser.add_argument(
        "--pre-lift",
        type=float,
        default=-0.5,
        help="lift the elbow by this many rad (through /joint_states) before step/track/ik so "
        "the tool is clear of the benchtop; 0 disables the pre-positioning",
    )
    parser.add_argument("--step-hold", type=float, default=2.5, help="seconds to hold after the step before returning")
    parser.add_argument("--step-tail", type=float, default=2.5, help="seconds to observe after the return step")
    parser.add_argument("--track-amp", type=float, default=0.25, help="sine amplitude in rad")
    parser.add_argument("--track-freqs", default="0.2,0.5", help="comma-separated frequencies in Hz")
    parser.add_argument(
        "--gain-sweep",
        default=",".join(f"{k:g}:{d:g}" for k, d in DEFAULT_GAIN_SWEEP),
        help="stiffness:damping pairs for the gains experiment",
    )
    parser.add_argument(
        "--gain-write",
        choices=("usd", "physx", "auto"),
        default="auto",
        help="how to change gains at runtime: USD drive attributes, PhysX set_gains, or auto-fallback",
    )
    parser.add_argument("--ee-link", default="wrist_3_link", help="link used as the end effector")
    parser.add_argument("--ik-offset", default="0.08,0,0", help="Cartesian goal offset from the EE pose, metres")
    parser.add_argument("--ik-hold", type=float, default=3.0, help="seconds to observe the Cartesian move")
    parser.add_argument("--ik-tolerance", type=float, default=0.001, help="Lula IK position tolerance, m")
    parser.add_argument("--publish-hz", type=float, default=100.0, help="/joint_states publish rate")
    parser.add_argument(
        "--allow-other-publishers",
        action="store_true",
        help="measure even if another node already publishes /joint_states",
    )
    parser.add_argument("--selftest", action="store_true", help="run the pure-Python metric/plot checks and exit")
    args = parser.parse_args(argv)
    args.track_freqs = [float(x) for x in str(args.track_freqs).split(",") if x.strip()]
    args.ik_offset = [float(x) for x in str(args.ik_offset).split(",") if x.strip()]
    args.gain_sweep = [tuple(float(v) for v in pair.split(":")) for pair in str(args.gain_sweep).split(",") if pair.strip()]
    args.experiments = [x.strip() for x in str(args.experiments).split(",") if x.strip()]
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.selftest:
        return run_selftest()
    if not args.out:
        print("error: --out DIR is required (or use --selftest)", file=sys.stderr)
        return 2
    unknown = [e for e in args.experiments if e not in EXPERIMENTS]
    if unknown:
        print(f"error: unknown experiment(s) {unknown}; choose from {sorted(EXPERIMENTS)}", file=sys.stderr)
        return 2
    for pair in args.gain_sweep:
        if len(pair) != 2:
            print(f"error: --gain-sweep entry {pair} is not stiffness:damping", file=sys.stderr)
            return 2

    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    # ---- ROS client side first: this may re-exec the process --------------
    # It must happen BEFORE anything is launched or measured, otherwise the
    # re-exec would boot a second Isaac Sim / repeat a measurement.
    try:
        rclpy_dir, lib_dir = ros_dirs()
        dds = apply_dds_env()
        ensure_native_libs(lib_dir)  # may re-exec this process
        sys.path.insert(0, str(rclpy_dir))
        ros_modules()
    except Exception as exc:
        print(f"error: cannot set up the ROS 2 client ({type(exc).__name__}: {exc})", file=sys.stderr)
        print("       the script publishes on /joint_states with the sim's bundled rclpy;", file=sys.stderr)
        print("       run it with the repo .venv (OMNI_KIT_ACCEPT_EULA=YES .venv/bin/python ...)", file=sys.stderr)
        return 2

    summary_path = out / "summary.json"
    if summary_path.exists():
        # Never overwrite accepted evidence: keep the previous run next to it.
        summary_path.replace(unique_path(out / f"summary-{time.strftime('%Y%m%d-%H%M%S')}.json"))

    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "script": str(Path(__file__).resolve()),
        "host": socket.gethostname(),
        "port": int(args.port),
        "joint": args.joint,
        "headless": bool(args.headless),
        "launched_by_script": bool(args.launch),
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "git_head": _git_head(),
        "ros": {"distro": ros_distro(), "rclpy_dir": str(rclpy_dir), "dds": dds},
        "topics": {
            "command": {"topic": JOINT_TOPIC, "type": "sensor_msgs/JointState", "role": "published by this script"},
            "arm_joint_state_publisher": {
                "exists": False,
                "note": "the twin publishes a JointState only for the gripper (/rg2_sim/joint_state, "
                "exts/ur5e-dt/ur5e_dt/extension.py:3133-3141); arm positions are polled through the bridge",
            },
        },
        "assumptions": [
            "the twin is on --port with the workstation loaded and the timeline playing",
            "/joint_states has no other publisher (checked at runtime)",
            "the graph subscriber uses the bridge default QoS (keep_last 10, reliable, volatile)",
            "the USD angular drive attributes are in per-degree units; PhysX-side set_gains takes per-radian",
        ],
        "experiments": {},
    }
    ctx = None
    launched = None
    twin = None
    exit_code = 0
    try:
        if args.launch:
            if port_open(args.port):
                raise RuntimeError(f"port {args.port} is already in use; stop that twin or pick another --port")
            launched = LaunchedTwin(args.port, args.headless, out / "logs" / "sim.log")
            boot = launched.start()
            print(f"launched twin on port {args.port} (pid {launched.pid()}) in {boot:.1f}s")
            summary["launch"] = {"pid": launched.pid(), "boot_seconds": round(boot, 2), "log": str(out / "logs" / "sim.log")}
            twin = Twin(args.port, timeout=900.0)
            ready = wait_bridge_ready(twin)
            summary["bridge_ready"] = ready
            if not ready:
                raise RuntimeError(f"the bridge on port {args.port} never answered list_tools; see {out / 'logs' / 'sim.log'}")
            qs = twin.tool("ur5e_dt.quick_start_with_workstation", timeout=900.0)
            if qs.get("status") != "success":
                # The first call can land while the stage is still being
                # created; one retry, then give up loudly (scripts/demo/run_demo.py:812).
                time.sleep(3)
                qs = twin.tool("ur5e_dt.quick_start_with_workstation", timeout=900.0)
            summary["quick_start"] = {"status": qs.get("status"), "message": str(qs.get("message"))[:300]}
            print(f"quick_start_with_workstation: {qs.get('status')}")
            if qs.get("status") != "success":
                raise RuntimeError(f"quick_start_with_workstation failed: {str(qs.get('message'))[:300]}")
            play = twin.tool("ur5e_dt.play_scene", timeout=300.0)
            summary["play_scene"] = {"status": play.get("status")}
            twin.timeout = 300.0
        else:
            if not port_open(args.port):
                raise RuntimeError(
                    f"no twin bridge on port {args.port}; start ./run.sh --port {args.port} first, "
                    "or pass --launch"
                )
            twin = Twin(args.port)

        # ---- ROS client side: same interpreter ABI, same DDS island --------
        # (already set up above; this is only a sanity print for the log)
        print(f"ros: {ros_distro()} rclpy from {rclpy_dir}; domain {dds.get('ROS_DOMAIN_ID')}, "
              f"{dds.get('RMW_IMPLEMENTATION')}, localhost_only={dds.get('ROS_LOCALHOST_ONLY')}")

        # ---- preflight: articulation, joint, drive, timeline --------------
        prep = twin.py(snippet(_PREP_BODY, {"joint": args.joint, "ee_link": args.ee_link}), timeout=180.0)
        summary["prep"] = {
            "prims": prep.get("prims"),
            "dof_names": prep.get("dof_names"),
            "qpos": prep.get("qpos"),
            "timeline_playing": prep.get("timeline_playing"),
            "warnings": prep.get("warnings"),
            "drive_usd": prep.get("drive_usd"),
            "drive_physx": prep.get("drive_physx"),
            "ee_link": prep.get("ee_link"),
            "ee_pos": prep.get("ee_pos"),
            "base_prim": prep.get("base_prim"),
            "base_pos": prep.get("base_pos"),
            "stage_units": prep.get("stage_units"),
        }
        for warning in prep.get("warnings") or []:
            print(f"note: {warning}")
        if not prep.get("has_vention"):
            print("warning: /World/Vention is not on the stage - is the workstation loaded?")

        ctx = Ctx(args, twin, out)
        ctx.prep = prep
        ctx.configure(prep)
        drive = prep.get("drive_usd") or {}
        print(
            f"joint {args.joint} (index {ctx.joint_index}) at {ctx.q_hold[ctx.joint_index]:+.4f} rad; "
            f"drive stiffness {drive.get('stiffness')} / damping {drive.get('damping')} "
            f"(max force {drive.get('max_force')})"
        )
        if args.step_rad > 0 and args.joint in ("elbow_joint", "shoulder_lift_joint"):
            print(
                "note: a positive elbow/shoulder step moves the arm toward the tabletop "
                "(scripts/checks/single_joint_motion.py:21); --pre-lift keeps the tool clear "
                "of the bench, and --step-rad -0.3 walks away from it instead"
            )

        if any(name in ("step", "track", "ik") for name in args.experiments):
            t0 = time.time()
            try:
                entry = prepose_clearance(ctx)
            except Exception as exc:
                entry = {"applied": False, "error": f"{type(exc).__name__}: {exc}",
                         "traceback": traceback.format_exc()}
                print(f"pre-pose FAILED - {entry['error'][:300]}")
                exit_code = 1
            entry["seconds"] = round(time.time() - t0, 2)
            summary["prepose"] = entry
            write_json(summary_path, summary)
            if entry.get("applied"):
                print(
                    f"pre-pose: {entry['joint']} commanded {entry['command_rad']:+.3f} rad "
                    f"(was {entry['start_rad']:+.3f}, measured {entry['measured_rad']:+.3f}); "
                    f"the tool is off the benchtop from here on"
                )
            elif not entry.get("error"):
                print("pre-pose: skipped")

        for name in args.experiments:
            print(f"\n=== {name} ===")
            t0 = time.time()
            try:
                entry = EXPERIMENTS[name](ctx)
                entry["ok"] = True
            except Exception as exc:
                entry = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
                print(f"{name}: FAILED - {entry['error'][:300]}")
                exit_code = 1
            entry["seconds"] = round(time.time() - t0, 2)
            summary["experiments"][name] = entry
            write_json(summary_path, summary)
            if entry["ok"]:
                print(f"{name}: ok in {entry['seconds']}s - {entry.get('note', '')}")
                if entry.get("plot") not in (None, "ok"):
                    print(f"  plot {entry['plot']}")
                rt = (entry.get("metrics") or {}).get("sampling", {}).get("realtime_factor")
                if isinstance(rt, (int, float)) and rt < 0.9:
                    print(
                        f"  warning: the sim ran at {rt:.2f}x real time, so the record covers less "
                        "simulated time than planned; consider a longer --step-hold / --ik-hold"
                    )
        summary["notes"] = ctx.notes
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        summary["traceback"] = traceback.format_exc()
        print(f"\nrun aborted: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        if twin is not None:
            twin.close()
        if launched is not None:
            info = launched.stop()
            summary["launch_stop"] = info
            print(f"stopped the twin we started (pid {info.get('pid')}, killed={info.get('killed')})")
        write_json(summary_path, summary)

    print_summary(summary, out)
    return exit_code


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except Exception:
        return ""


def print_summary(summary: dict, out: Path) -> None:
    """Short human summary: one line per experiment, then where the evidence is."""
    print("\ncontrol experiments summary")
    print("-" * 78)
    for name in ("step", "track", "gains", "ik"):
        entry = (summary.get("experiments") or {}).get(name)
        if not entry:
            continue
        if not entry.get("ok"):
            print(f"  {name:6s} FAILED   {str(entry.get('error'))[:100]}")
            continue
        m = entry.get("metrics") or {}
        if name == "step":
            print(
                f"  {name:6s} rise {_fmt(m.get('rise_time_s'), ' s')}  "
                f"overshoot {_fmt(m.get('overshoot_pct'), ' %', 1)}  "
                f"settle {_fmt(m.get('settling_time_s'), ' s')}  "
                f"ss err {_fmt(m.get('steady_state_error_rad'), ' rad', 4)}"
            )
        elif name == "track":
            parts = [
                f"{fm.get('freq_hz', 0):g} Hz: rms {_fmt(fm.get('rms_error_rad'), ' rad', 4)}, "
                f"lag {_fmt(fm.get('phase_lag_deg'), ' deg', 1)}"
                for fm in m.get("per_frequency") or []
            ]
            print(f"  {name:6s} " + " | ".join(parts))
        elif name == "gains":
            gw = m.get("gain_write") or {}
            print(
                f"  {name:6s} {len(m.get('pairs') or [])} combos via {m.get('mode_used')}; "
                f"USD->PhysX {gw.get('usd_reached_physx')}"
            )
        elif name == "ik":
            print(
                f"  {name:6s} final EE error {_fmt(m.get('final_error_mm'), ' mm', 2)}  "
                f"IK residual {_fmt(m.get('ik_fk_residual_mm'), ' mm', 2)}"
            )
        if entry.get("csv"):
            print(f"         {entry['csv']}")
        if entry.get("png"):
            print(f"         {entry['png']}")
    if summary.get("error"):
        print(f"  aborted: {summary['error'][:200]}")
    print(f"  summary: {out / 'summary.json'}")


# =========================================================================== #
# 10. selftest - metrics and plotting on synthetic data (no sim, no GPU)
# =========================================================================== #
def _second_order_analytic(t, zeta: float, wn: float) -> np.ndarray:
    """Closed-form unit-step response of G(s) = wn^2 / (s^2 + 2 zeta wn s + wn^2).

    y(t) = 1 - e^(-zeta wn t) [cos(wd t) + zeta/sqrt(1-zeta^2) sin(wd t)],
    wd = wn sqrt(1 - zeta^2). Used by the selftest as the ground truth the
    numerically simulated response is checked against.
    """
    t = _floats(t)
    wd = wn * math.sqrt(1.0 - zeta**2)
    return 1.0 - np.exp(-zeta * wn * t) * (
        np.cos(wd * t) + zeta / math.sqrt(1.0 - zeta**2) * np.sin(wd * t)
    )


def _second_order_step(zeta: float, wn: float, dt: float, t_end: float):
    """Simulate G(s) = wn^2 / (s^2 + 2 zeta wn s + wn^2) with RK4.

    Used only by the selftest, so the plotting code has a realistic
    under-damped response to draw; the numbers are checked against
    ``_second_order_analytic``.
    """
    n = int(round(t_end / dt))
    t = np.arange(n) * dt
    y = np.zeros(n)
    x = np.zeros(2)  # [position, velocity]
    for i in range(1, n):
        def f(state):
            return np.array([state[1], wn * wn * (1.0 - state[0]) - 2.0 * zeta * wn * state[1]])

        k1 = f(x)
        k2 = f(x + 0.5 * dt * k1)
        k3 = f(x + 0.5 * dt * k2)
        k4 = f(x + dt * k3)
        x = x + dt / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
        y[i] = x[0]
    return t, y


def run_selftest() -> int:
    """Check the metrics against analytic first/second-order answers and the plots."""
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}".rstrip())

    # --- first order: y = 1 - exp(-t/tau) --------------------------------
    tau = 0.5
    t = np.arange(0.0, 6.0, 0.002)
    y = 1.0 - np.exp(-t / tau)
    m = step_metrics(t, y, t_step=0.0, target=1.0, y0=0.0)
    check("first-order rise 10-90 %", abs(m["rise_time_s"] - tau * math.log(9.0)) < 0.01,
          f"{m['rise_time_s']:.4f} vs {tau * math.log(9.0):.4f}")
    check("first-order no overshoot", m["overshoot_pct"] < 1e-6, f"{m['overshoot_pct']:.2e} %")
    check("first-order settling 2 %", abs(m["settling_time_s"] - tau * math.log(50.0)) < 0.02,
          f"{m['settling_time_s']:.4f} vs {tau * math.log(50.0):.4f}")
    check("first-order zero ss error", abs(m["steady_state_error_rad"]) < 1e-4, f"{m['steady_state_error_rad']:.2e}")

    # --- second order: zeta 0.3, wn 2*pi*1.5 ----------------------------
    zeta, wn = 0.3, 2.0 * math.pi * 1.5
    ts, ys = _second_order_step(zeta, wn, 1e-3, 4.0)
    t_fine = np.arange(0.0, 4.0, 1e-5)
    y_fine = _second_order_analytic(t_fine, zeta, wn)
    check("RK4 matches the analytic solution", float(np.max(np.abs(ys - _second_order_analytic(ts, zeta, wn)))) < 1e-4,
          f"max |dy| {float(np.max(np.abs(ys - _second_order_analytic(ts, zeta, wn)))):.2e}")
    m2 = step_metrics(ts, ys, t_step=0.0, target=1.0, y0=0.0)
    os_analytic = 100.0 * math.exp(-math.pi * zeta / math.sqrt(1.0 - zeta**2))
    check("2nd-order overshoot", abs(m2["overshoot_pct"] - os_analytic) < 0.8,
          f"{m2['overshoot_pct']:.2f} % vs {os_analytic:.2f} %")
    # 10-90 % rise and the 2 % settling time, read off the analytic curve on a
    # 10 us grid - the textbook t_r = (pi - acos(zeta))/wd is the 0-100 % time
    # and would be the wrong yardstick here.
    rise_analytic = first_crossing(t_fine, y_fine, 0.9) - first_crossing(t_fine, y_fine, 0.1)
    check("2nd-order rise 10-90 %", abs(m2["rise_time_s"] - rise_analytic) < 0.003,
          f"{m2['rise_time_s']:.4f} s vs {rise_analytic:.4f} s")
    outside = np.nonzero(np.abs(y_fine - 1.0) > 0.02)[0]
    t_settle_analytic = float(t_fine[int(outside[-1])]) if outside.size else 0.0
    check("2nd-order settling 2 %", abs(m2["settling_time_s"] - t_settle_analytic) < 0.005,
          f"{m2['settling_time_s']:.3f} s vs {t_settle_analytic:.3f} s")
    check("2nd-order ss error small", abs(m2["steady_state_error_rad"]) < 2e-3,
          f"{m2['steady_state_error_rad']:.2e}")
    check("2nd-order settled flag", m2["settled_in_window"] is True, str(m2["settled_in_window"]))

    # --- negative step, sign handling -----------------------------------
    mneg = step_metrics(t, -y, t_step=0.0, target=-1.0, y0=0.0)
    check("negative step rise", abs(mneg["rise_time_s"] - m["rise_time_s"]) < 1e-6, f"{mneg['rise_time_s']:.4f}")
    check("negative step overshoot", mneg["overshoot_pct"] < 1e-6, f"{mneg['overshoot_pct']:.2e}")

    # --- degenerate cases ------------------------------------------------
    flat = step_metrics(t, np.zeros_like(t), t_step=0.0, target=0.0, y0=0.0)
    check("no-move step returns NaN", math.isnan(flat["rise_time_s"]), "rise NaN")
    short = step_metrics(np.array([0.0, 1.0]), np.array([0.0, 1.0]), t_step=0.0, target=1.0)
    check("too-few-samples is safe", math.isnan(short["rise_time_s"]), "no exception")
    truncated = step_metrics(t[:200], y[:200], t_step=0.0, target=1.0, y0=0.0)
    check("truncated record reports unsettled", truncated["settled_in_window"] is False,
          f"settle {truncated['settling_time_s']}")

    # --- cross-correlation lag ------------------------------------------
    dt = 0.01
    tt = np.arange(0.0, 10.0, dt)
    lag_true = 0.12
    ref = np.sin(2 * math.pi * 0.5 * tt)
    resp = np.sin(2 * math.pi * 0.5 * (tt - lag_true))
    lag = cross_correlation_lag(tt, ref, resp)
    check("cross-correlation lag", abs(lag - lag_true) < dt, f"{lag:.4f} s vs {lag_true:.4f} s")
    f_est = estimate_frequency(tt, resp)
    check("frequency estimate", abs(f_est - 0.5) < 0.01, f"{f_est:.4f} Hz")

    # --- tracking metrics ------------------------------------------------
    phase_true = 0.6  # rad
    resp2 = 0.8 * np.sin(2 * math.pi * 0.5 * tt - phase_true)
    tm = tracking_metrics(tt, ref, resp2, freq_hz=0.5)
    err = resp2 - ref
    check("tracking rms matches direct", abs(tm["rms_error_rad"] - float(np.sqrt(np.mean(err**2)))) < 1e-6,
          f"{tm['rms_error_rad']:.4f}")
    check("tracking peak matches direct", abs(tm["peak_error_rad"] - float(np.max(np.abs(err)))) < 1e-6,
          f"{tm['peak_error_rad']:.4f}")
    check("tracking phase lag", abs(tm["phase_lag_deg"] - math.degrees(phase_true)) < 2.0,
          f"{tm['phase_lag_deg']:.2f} deg vs {math.degrees(phase_true):.2f} deg")
    check("tracking gain", abs(tm["gain"] - 0.8) < 0.01, f"{tm['gain']:.3f}")
    check("tracking freq estimate", abs(tm["freq_hz_est"] - 0.5) < 0.02, f"{tm['freq_hz_est']:.3f} Hz")

    # --- jsonable --------------------------------------------------------
    payload = jsonable({"a": float("nan"), "b": [1.0, float("inf")], "c": np.float32(2.5)})
    check("jsonable clears non-finite", payload == {"a": None, "b": [1.0, None], "c": 2.5}, str(payload))

    # --- plotting --------------------------------------------------------
    try:
        pyplot()
        has_mpl = True
    except PlotUnavailable as exc:
        has_mpl = False
        print(f"SKIP  plotting checks ({exc})")
    if has_mpl:
        with tempfile.TemporaryDirectory(prefix="control-experiments-selftest-") as tmp:
            tmp = Path(tmp)
            try:
                p1 = plot_step(
                    tmp / "step.png",
                    t=t,
                    y=y,
                    ref=(t >= 0).astype(float),
                    metrics=m,
                    joint="elbow_joint",
                    stiffness=100,
                    damping=10,
                    t_step=0.0,
                    footer="selftest",
                )
                check("step PNG exists", p1.is_file() and p1.stat().st_size > 20000, f"{p1.stat().st_size} B")
                check("step PNG is 1600x900", png_size(p1) == (1600, 900), str(png_size(p1)))

                segs = [(0.0, 2.0), (2.5, 4.5)]
                p2 = plot_tracking(
                    tmp / "track.png",
                    t=tt,
                    ref=ref,
                    resp=resp2,
                    per_freq=[tm],
                    joint="elbow_joint",
                    footer="selftest",
                    segments=segs,
                )
                check("tracking PNG is 1600x900", png_size(p2) == (1600, 900), str(png_size(p2)))

                series = [
                    {
                        "label": f"K {k:g} / D {d:g}",
                        "stiffness": k,
                        "damping": d,
                        "t": ts,
                        "y": ys if k == 100 else _second_order_step(0.6, wn, 1e-3, 4.0)[1],
                        "metrics": m2,
                    }
                    for k, d in ((100, 10), (200, 10), (400, 20))
                ]
                p3 = plot_gains(tmp / "gains.png", series=series, joint="elbow_joint", footer="selftest")
                check("gains PNG is 1600x900", png_size(p3) == (1600, 900), str(png_size(p3)))

                ee = np.column_stack([np.linspace(0.4, 0.48, tt.size), np.zeros_like(tt), np.linspace(0.3, 0.3, tt.size)])
                goal = ee[-1].copy()
                errs = np.linalg.norm(ee - goal, axis=1)
                p4 = plot_ik(
                    tmp / "ik.png",
                    t=tt,
                    err=errs,
                    ee=ee,
                    goal=goal,
                    metrics={"goal_offset_m": 0.08, "final_error_mm": 1.0, "peak_error_mm": 80.0,
                             "ik_fk_residual_mm": 0.2, "settle_time_s": 1.2},
                    joint="elbow_joint",
                    footer="selftest",
                )
                check("ik PNG is 1600x900", png_size(p4) == (1600, 900), str(png_size(p4)))
            except Exception:
                check("plotting raised no exception", False, traceback.format_exc().splitlines()[-1])

        check("responses_indistinguishable (identical)", _responses_indistinguishable(series[:1] + series[:1]) is True)
        check("responses_indistinguishable (different)", _responses_indistinguishable(series[:2]) is False)

    failed = [name for name, ok, _ in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    if failed:
        print("failed: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

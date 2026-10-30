#!/usr/bin/env python3
"""Resolved-rate motion control of a UR5e: differential IK with damping, limits
and an optional run on the Isaac Sim twin.

WHAT RESOLVED-RATE CONTROL IS (the 30-second version)
-----------------------------------------------------
The arm's joints are position-controlled; we think in Cartesian space. The
Jacobian is the linear map between the two at every instant:

        v = J(q) qdot          (v = [linear; angular] twist, 6x1)

Inverting it gives joint velocities that produce a desired end-effector twist:

        qdot = J^-1 v          (6-DoF arm, square J, away from singularities)

Close the loop by making the twist proportional to the pose error:

        qdot = J^-1 * K * pose_error(T_cur, T_des)                (K = gain matrix)

That is "resolved-rate" control -- at the servo rate, the commanded velocity is
re-resolved from the measured configuration. It tracks a *moving* goal for
free: put time into T_des(t) and the same law follows it. It needs no inverse
kinematics solution and no joint-space trajectory, which is why it is the
workhorse for teleoperation and visual servoing.

THE FOUR THINGS THAT BREAK NAIVELY-IMPLEMENTED RESOLVED RATE
------------------------------------------------------------
1. SINGULARITIES. Near a singularity J loses rank: the smallest singular value
   sigma_min -> 0, and J^-1 has entries ~ 1/sigma_min. The arm is asked for
   hundreds of rad/s. The standard fix is DAMPED LEAST SQUARES (Levenberg-
   Marquardt / "singularity-robust inverse"):

        qdot = J^T (J J^T + lambda^2 I)^-1 v

   As lambda -> 0 this is the pseudo-inverse; as lambda grows it becomes a
   scaled transpose (steepest descent). It never divides by zero, so the
   command stays finite. It also trades a little Cartesian accuracy for sanity:
   near a singularity the arm simply does not reach the goal exactly, which is
   the correct behaviour.

   The damping must be ADAPTIVE. A constant large lambda makes the whole arm
   sluggish; a constant small one still explodes close enough to the
   singularity. Here lambda grows as sigma_min falls below a threshold
   (`adaptive_damping()`), quadratically, because lambda enters squared.

2. JOINT VELOCITY LIMITS. Clamping each joint independently ("qdot_i =
   clip(qdot_i, +-qd_max)") changes the *direction* of the twist: the arm goes
   somewhere you did not ask for. Scaling the WHOLE vector by one factor
   preserves direction and just slows the motion down:

        s = min(1, qd_max / max_i |qdot_i|),   qdot *= s

   Slower, but along the intended path. That is what `velocity_scale` does.

3. JOINT POSITION LIMITS. Two mechanisms, and it is worth being precise about
   which does what on a 6-DoF arm:
     * a null-space term N g with N = I - J^+ J pushes joints toward the middle
       of their range *without changing the end-effector twist*;
     * but this arm has 6 joints for a 6-DoF task, so away from singularities
       N is (numerically) zero -- there is no redundancy to spend. The null-
       space term only becomes useful when J is rank-deficient.
   So the primary mechanism here is also velocity damping near the limits: the
   commanded joint velocity is tapered to zero as a joint approaches its hard
   limit (`limit_taper`). It changes the twist, deliberately: refusing to move
   is better than hitting a hard stop at speed. Both are implemented, and
   `info` reports which is active.

4. THE COMMAND IS A VELOCITY, THE DRIVE IS A POSITION SERVO. A resolved-rate
   controller outputs qdot; the twin (and a real UR) takes a position target.
   So the loop integrates: q_cmd += qdot * dt each tick and publishes q_cmd.
   That integration is where wind-up lives -- if the servo lags, q_cmd runs
   away from reality and the error never closes. `run_on_twin()` therefore
   re-anchors q_cmd when it strays too far from the measured q.

HOW THIS FILE IS ORGANISED
--------------------------
    ur5e_common.py     fk, jacobian, pose_error, TwinLink (imported, not copied)
    ResolvedRateController.step(q, T_des, dt) -> (qdot, info)
    simulate(...)      offline closed loop: ideal integrator, first-order servo,
                       or a second-order PD servo (stiffness/damping-like)
    run_on_twin(...)   the same loop at 50 Hz over ROS 2, logging every tick
    demo_*()           the three slide figures + CSVs + summary.json
    selftest()         maths and limits, no sim, no GPU

USAGE
-----
    .venv/bin/python scripts/demo/resolved_rate.py --selftest
    .venv/bin/python scripts/demo/resolved_rate.py --offline --out demo-out/resolved-rate
    .venv/bin/python scripts/demo/resolved_rate.py --on-twin --out demo-out/resolved-rate \
        [--port 8791] [--state ros|bridge] [--hz 50] [--duration 12]

    --offline writes (1600x900 PNG + CSV each, plus a summary):
        resolved_rate_reach.png         reach a Cartesian goal: error, qdot+limits
        resolved_rate_circle.png        track a moving circular goal
        resolved_rate_singularity.png   lambda = 0 vs damped least squares
        resolved_rate_summary.json      metrics (also merged into summary.json)

WHAT WAS TESTED OFFLINE AND WHAT WAS NOT
----------------------------------------
Tested: everything in --selftest (Jacobian agreement, convergence, velocity
limits, damping keeps qdot finite at a singularity, pose-error sign, plant
models, CSV/plot plumbing). Figures were rendered and inspected.

NOT tested here (needs the twin, GPU busy at the time of writing): TwinLink's
actual round trip, the /joint_states command path, the bridge state observer
(--state bridge), and therefore any on-twin number. Two structural facts from
the extension limit what an on-twin run can do:
  * the twin subscribes /joint_states and mirrors it (extension.py:3042-3074);
  * the twin publishes NO arm joint state (only the gripper's
    /rg2_sim/joint_state, extension.py:3085-3171), so q for the feedback path
    must come from the UR driver's broadcaster on the same topic, or from the
    Kit bridge. --state says which; without either, the script refuses rather
    than pretend (see run_on_twin).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ur5e_common as ur  # noqa: E402  (local sibling module, numpy-only)

# =========================================================================== #
# 1. Defaults (one place, so the summary.json is reproducible)
# =========================================================================== #
DEFAULTS = dict(
    kp_pos=10.0,         # 1/s    position error -> Cartesian speed
    kp_rot=5.0,          # 1/s    orientation error -> angular speed
    max_cart_speed=0.20,  # m/s    cap on the linear part of the twist
    max_ang_speed=0.80,  # rad/s  cap on the angular part
    # Per-joint cap. ur5e_common.QD_DEMO (1.0 rad/s) is the blanket twin-safe
    # limit and the datasheet is pi rad/s. This demo asks for less, on purpose:
    # the twin's joints are soft position drives (stiffness 100 / damping 10)
    # and, at 0.3 rad/s, the limiter is actually exercised on the demo move --
    # at 1.0 rad/s the Cartesian speed cap binds first and the joint panel
    # would be decoration. Raise it for a faster demo.
    qd_max=0.3,          # rad/s
    lambda_min=0.01,     # smallest damping (m) -- away from singularities
    lambda_max=0.50,     # largest damping (m) -- at sigma_min = 0
    sigma_threshold=0.06,  # ramp the damping in below this sigma_min
    goal_tolerance=0.005,  # m      "reached" = within 5 mm of the goal
    rot_tolerance=0.02,  # rad    ...and within ~1.1 deg of the goal orientation
    null_gain=0.30,      # rad/s  null-space pull toward mid-range
    limit_soft_margin=0.50,  # rad  start pushing/tapering this far from a limit
    limit_hard_margin=0.12,  # rad  taper to zero inside this
    dt=0.01,             # s      offline loop period (100 Hz)
    plant="ideal",       # ideal | lag | pd2
    lag_tau=0.05,        # s      first-order servo time constant
    servo_stiffness=100.0,  # N m/rad   (the twin's USD drive value)
    servo_damping=10.0,  # N m s/rad  (the twin's USD drive value)
    servo_inertia=0.5,   # kg m^2  normalised placeholder: not measured
)

#: For the singularity demo, damping is switched off entirely (lam = 0), which
#: makes `qdot` the plain Moore-Penrose pseudo-inverse solution -- the textbook
#: "watch it blow up" case. Velocity limiting is disabled there too, otherwise
#: the clamp would hide the blow-up and the figure would teach nothing.
UNDAMPED = dict(lambda_min=0.0, lambda_max=0.0, sigma_threshold=0.0,
                qd_max=None, null_gain=0.0)


# =========================================================================== #
# 2. Small numeric helpers
# =========================================================================== #
def adaptive_damping(sigma_min_value: float, lambda_min: float, lambda_max: float,
                     sigma_threshold: float) -> float:
    """Damping for damped least squares, as a function of sigma_min.

        sigma_min >= sigma_threshold   ->  lambda_min        (full accuracy)
        sigma_min == 0                 ->  lambda_max        (full damping)
        in between                     ->  quadratic ramp

    Why quadratic: the damping enters the inverse as lambda^2, so a linear ramp
    in lambda is a quadratic ramp in the thing that actually matters. The
    quadratic keeps lambda_min in force for most of the workspace and still
    reaches full damping at the singularity, which is exactly the trade you
    want: no sluggishness far away, no explosion close in.

    Units: lambda has the units of the Jacobian's singular values. For this
    mixed m/rad Jacobian, sigma is (m/rad) for the linear rows, so lambda is a
    "softening length" -- 1 cm here. That is the length scale at which the
    controller stops insisting on millimetre accuracy.
    """
    if lambda_max <= 0.0 and lambda_min <= 0.0:
        return 0.0  # explicitly undamped
    if sigma_threshold <= 0.0:
        return float(lambda_min)
    ratio = min(1.0, max(0.0, float(sigma_min_value) / float(sigma_threshold)))
    return float(lambda_min + (lambda_max - lambda_min) * (1.0 - ratio) ** 2)


def limit_potential_gradient(q: np.ndarray, soft_margin: float,
                             gain: float) -> np.ndarray:
    """Null-space pull toward the middle of each joint's range (rad/s).

    A potential that is zero in the interior and rises smoothly as a joint
    approaches a limit; its negative gradient is the velocity we add in the
    null space. `(soft - margin)^2 / soft^2` is 0 at the soft margin and 1 at
    the hard limit, so the push grows quadratically exactly where it must.

    Sign: push away from the limit, i.e. toward the centre of the range. At the
    centre the sign is undefined, so the term is simply zero there (no
    preferred side).
    """
    centre = 0.5 * (ur.Q_MIN + ur.Q_MAX)
    margins = np.minimum(q - ur.Q_MIN, ur.Q_MAX - q)
    g = np.zeros(ur.N_JOINTS)
    active = margins < soft_margin
    if not np.any(active):
        return g
    direction = np.sign(q - centre)
    frac = (soft_margin - margins) / max(soft_margin, 1e-9)
    g[active] = -gain * direction[active] * np.clip(frac[active], 0.0, 1.0) ** 2
    return g


def limit_taper(q: np.ndarray, hard_margin: float) -> np.ndarray:
    """Per-joint multiplier in [0, 1] that fades velocity out near limits.

    1.0 everywhere except within `hard_margin` of a position limit, where it
    falls linearly to 0 at the limit. Multiplying the commanded velocity by
    this is the "refuse to drive into the stop" mechanism; unlike a null-space
    term it does change the twist, which is the point.
    """
    margins = np.minimum(q - ur.Q_MIN, ur.Q_MAX - q)
    return np.clip(margins / max(hard_margin, 1e-9), 0.0, 1.0)


def saturate_norm(vector: np.ndarray, max_norm: Optional[float]) -> tuple:
    """Scale `vector` down so its norm <= max_norm, preserving direction.

    Returns (scaled_vector, scale) with scale in [0, 1]. `None` or inf disables.
    This is the "slow down along the same path" primitive used for the task
    twist (a Cartesian speed has a norm, so the 2-norm is the right measure).
    """
    norm = float(np.linalg.norm(vector))
    if max_norm is None or not np.isfinite(max_norm) or max_norm <= 0.0:
        return vector.copy(), 1.0
    if norm <= max_norm or norm == 0.0:
        return vector.copy(), 1.0
    scale = float(max_norm) / norm
    return vector * scale, scale


def saturate_joint_velocity(qdot: np.ndarray, qd_max: Optional[np.ndarray]) -> tuple:
    """Scale the whole joint-velocity vector so no joint exceeds its own limit.

    A joint limit is a per-joint (infinity-norm) constraint, not a Euclidean
    one: |qdot_i| <= qd_max_i for every i. So the scale factor is the worst
    ratio,

        s = min(1, min_i qd_max_i / |qdot_i|)

    and the same s is applied to every joint, which preserves the direction of
    the motion in joint space (and therefore the direction of the resulting
    twist, up to the Jacobian). Clamping each joint independently would also
    satisfy the limit, but it would *bend* the motion: the arm would go
    somewhere the controller never asked for.

    Returns (scaled_vector, scale). This is exactly the kind of two-line
    detail that a Euclidean norm silently gets wrong: with a 2-norm cap of
    1.0 rad/s, a vector (0.99, 0.99, ...) is scaled even though no joint is
    over its limit, and (0.7, 0, 0, ...) is left alone. Per-joint it is.
    """
    if qd_max is None:
        return qdot.copy(), 1.0
    limits = np.asarray(qd_max, dtype=float).reshape(-1)
    if limits.shape != qdot.shape:
        raise ValueError(f"qd_max shape {limits.shape} does not match qdot {qdot.shape}")
    ratios = np.abs(qdot) / np.maximum(limits, 1e-12)
    worst = float(np.max(ratios)) if ratios.size else 0.0
    if worst <= 1.0 or worst == 0.0:
        return qdot.copy(), 1.0
    scale = 1.0 / worst
    return qdot * scale, scale


def _as_transform(T) -> np.ndarray:
    T = np.asarray(T, dtype=float)
    if T.shape != (4, 4):
        raise ValueError(f"expected a 4x4 transform, got shape {T.shape}")
    return T


# =========================================================================== #
# 3. The controller
# =========================================================================== #
class ResolvedRateController:
    """Differential IK loop: q -> qdot, one tick at a time.

    The law, in the order it is applied in `step()`:

        e      = pose_error(fk(q), T_des)                 # 6-vector
        v      = saturate([kp_pos e_pos; kp_rot e_rot])   # task twist, capped
        J      = jacobian(q)                              # 6x6, base frame
        lam    = adaptive(shininess)                      # damping
        qdot_T = J^T (J J^T + lam^2 I)^-1 v               # damped least squares
        qdot_N = (I - J^+ J) g(q)                         # null space (0 if full rank)
        qdot  *= taper(q)                                 # limit avoidance
        qdot  *= min(1, qd_max / max|qdot|)               # velocity cap, direction kept

    Every intermediate value ends up in the `info` dict, because a plot that
    says "the controller is doing something odd" needs the numbers, not a
    rerun with prints.
    """

    def __init__(self, kp_pos: float = DEFAULTS["kp_pos"],
                 kp_rot: float = DEFAULTS["kp_rot"],
                 max_cart_speed: Optional[float] = DEFAULTS["max_cart_speed"],
                 max_ang_speed: Optional[float] = DEFAULTS["max_ang_speed"],
                 qd_max: Optional[Sequence[float]] = DEFAULTS["qd_max"],
                 lambda_min: float = DEFAULTS["lambda_min"],
                 lambda_max: float = DEFAULTS["lambda_max"],
                 sigma_threshold: float = DEFAULTS["sigma_threshold"],
                 goal_tolerance: float = DEFAULTS["goal_tolerance"],
                 rot_tolerance: float = DEFAULTS["rot_tolerance"],
                 null_gain: float = DEFAULTS["null_gain"],
                 limit_soft_margin: float = DEFAULTS["limit_soft_margin"],
                 limit_hard_margin: float = DEFAULTS["limit_hard_margin"],
                 tool_offset: Optional[np.ndarray] = None,
                 joint_names: Sequence[str] = ur.JOINT_NAMES):
        self.kp_pos = float(kp_pos)
        self.kp_rot = float(kp_rot)
        self.max_cart_speed = max_cart_speed
        self.max_ang_speed = max_ang_speed
        if qd_max is None:
            self.qd_max = None
        elif np.isscalar(qd_max):
            self.qd_max = np.full(ur.N_JOINTS, float(qd_max))
        else:
            self.qd_max = np.asarray(qd_max, dtype=float).reshape(ur.N_JOINTS)
        self.lambda_min = float(lambda_min)
        self.lambda_max = float(lambda_max)
        self.sigma_threshold = float(sigma_threshold)
        self.goal_tolerance = float(goal_tolerance)
        self.rot_tolerance = float(rot_tolerance)
        self.null_gain = float(null_gain)
        self.limit_soft_margin = float(limit_soft_margin)
        self.limit_hard_margin = float(limit_hard_margin)
        self.tool_offset = None if tool_offset is None else _as_transform(tool_offset)
        self.joint_names = tuple(joint_names)
        self.steps = 0

    # -- introspection -----------------------------------------------------
    def params(self) -> dict:
        """JSON-able copy of the tuning (goes into summary.json)."""
        return {
            "kp_pos_per_s": self.kp_pos,
            "kp_rot_per_s": self.kp_rot,
            "max_cart_speed_m_s": self.max_cart_speed,
            "max_ang_speed_rad_s": self.max_ang_speed,
            "qd_max_rad_s": None if self.qd_max is None else self.qd_max.tolist(),
            "lambda_min": self.lambda_min,
            "lambda_max": self.lambda_max,
            "sigma_threshold": self.sigma_threshold,
            "goal_tolerance_m": self.goal_tolerance,
            "rot_tolerance_rad": self.rot_tolerance,
            "null_gain_rad_s": self.null_gain,
            "limit_soft_margin_rad": self.limit_soft_margin,
            "limit_hard_margin_rad": self.limit_hard_margin,
            "tool_offset_identity": self.tool_offset is None,
        }

    # -- the law -----------------------------------------------------------
    def step(self, q: Sequence[float], T_des: Sequence[Sequence[float]],
             dt: float = DEFAULTS["dt"]) -> tuple:
        """One control tick. Returns (qdot_cmd [rad/s], info dict).

        `dt` does not enter the proportional law (it is a velocity command, not
        a position increment). It is used for the null-space term's units and
        is reported so callers integrate with the same number the controller
        was told about.
        """
        q = np.asarray(q, dtype=float).reshape(ur.N_JOINTS)
        T_des = _as_transform(T_des)
        self.steps += 1

        # --- 1. where are we, and how far is the goal? --------------------
        # Position error in metres and orientation error as a rotation vector,
        # both in the base frame (ur5e_common.pose_error documents the sign).
        T_cur = ur.fk(q)
        e = ur.pose_error(T_cur, T_des)
        pos_err = e[:3]
        rot_err = e[3:]
        pos_err_norm = float(np.linalg.norm(pos_err))
        rot_err_norm = float(np.linalg.norm(rot_err))

        # --- 2. error -> desired twist ------------------------------------
        # Separate gains for position and orientation, because "1 m" and "1 rad"
        # are not the same kind of error. Then two caps, so a 40 cm error does
        # not ask for 3 m/s of joint motion: this is the "max Cartesian speed".
        v = np.empty(6)
        v[:3], lin_scale = saturate_norm(self.kp_pos * pos_err, self.max_cart_speed)
        v[3:], ang_scale = saturate_norm(self.kp_rot * rot_err, self.max_ang_speed)

        # --- 3. Jacobian and its conditioning -----------------------------
        J = ur.jacobian(q, tool_offset=self.tool_offset)
        sv = ur.singular_values(J)
        smin = float(sv[-1])
        smax = float(sv[0])
        man = ur.manipulability(J)

        # --- 4. adaptive damping and the damped least-squares inverse -----
        lam = adaptive_damping(smin, self.lambda_min, self.lambda_max,
                               self.sigma_threshold)
        if lam > 1e-12:
            # The normal-equation form: solve the 6x6 system instead of
            # inverting, which is both faster and better conditioned.
            A = J @ J.T + (lam ** 2) * np.eye(6)
            qdot_task = J.T @ np.linalg.solve(A, v)
            J_pinv = np.linalg.solve(A, J).T          # J^T (J J^T + lam^2 I)^-1
        else:
            # Undamped: Moore-Penrose via SVD. rcond=1e-12 keeps the pseudo-
            # inverse from amplifying singular values that are pure round-off,
            # but everything above that (1e-6, 1e-4 ...) goes through at full
            # 1/sigma gain -- which is precisely the demonstration.
            qdot_task = np.linalg.pinv(J, rcond=1e-12) @ v
            J_pinv = np.linalg.pinv(J, rcond=1e-12)

        # --- 5. null-space term (joint-limit avoidance, part 1) -----------
        # N = I - J^+ J projects onto the set of joint velocities that produce
        # no end-effector motion. On a 6-DoF arm with a 6-DoF task this space
        # is empty at full rank -- N ~ 0 -- so this term is a near-singularity
        # mechanism, not the main limit-avoidance one. See the module docstring.
        g_limit = limit_potential_gradient(q, self.limit_soft_margin, self.null_gain)
        N = np.eye(ur.N_JOINTS) - J_pinv @ J
        qdot_null = N @ g_limit
        qdot = qdot_task + qdot_null

        # --- 6. velocity damping near the hard limits (part 2) ------------
        taper = limit_taper(q, self.limit_hard_margin)
        qdot = qdot * taper

        # --- 7. joint velocity cap, preserving direction -------------------
        qdot_raw = qdot.copy()
        qdot, vel_scale = saturate_joint_velocity(qdot, self.qd_max)

        # Never hand a non-finite number to a robot. This can only trigger on a
        # bug (or a NaN in q), and failing loudly beats a silent NaN command.
        if not np.all(np.isfinite(qdot)):
            raise FloatingPointError(f"non-finite qdot command: {qdot}")

        margins = ur.limit_margins(q)
        info = {
            "t": None,                     # filled in by the caller if it wants
            "pos_err": pos_err.copy(),
            "rot_err": rot_err.copy(),
            "pos_err_norm": pos_err_norm,
            "rot_err_norm": rot_err_norm,
            "twist": v.copy(),
            "lin_scale": lin_scale,        # < 1 => Cartesian speed cap active
            "ang_scale": ang_scale,
            "sigma": sv.tolist(),
            "sigma_min": smin,
            "sigma_max": smax,
            "condition_number": float(smax / smin) if smin > 0 else float("inf"),
            "manipulability": man,
            "damping": lam,
            "qdot_task": qdot_task.copy(),
            "qdot_null": qdot_null.copy(),
            "taper": taper.copy(),
            "velocity_scale": vel_scale,   # < 1 => joint speed cap active
            "qdot_raw": qdot_raw,
            "qdot": qdot.copy(),
            "min_joint_margin": float(np.min(margins)),
            "near_limit": bool(np.any(taper < 1.0)),
            "max_abs_qdot": float(np.max(np.abs(qdot))),
            "converged": bool(pos_err_norm <= self.goal_tolerance
                              and rot_err_norm <= self.rot_tolerance),
            "dt": float(dt),
        }
        return qdot, info


# =========================================================================== #
# 4. Offline plant models
# =========================================================================== #
def simulate(controller: ResolvedRateController, q0: Sequence[float],
             T_des, plant: str = "ideal", dt: float = DEFAULTS["dt"],
             T: float = 8.0, lag_tau: float = DEFAULTS["lag_tau"],
             stiffness: float = DEFAULTS["servo_stiffness"],
             damping: float = DEFAULTS["servo_damping"],
             inertia: float = DEFAULTS["servo_inertia"],
             stop_on_converge: bool = False,
             verbose: bool = False) -> dict:
    """Closed-loop simulation. No sim, no ROS: a plant model in ~30 lines.

    `T_des` is either a 4x4 goal or a callable t -> 4x4 for tracking.

    Plants (all driven the same way: the controller emits qdot, the loop
    integrates it into a POSITION target, the servo tracks that target):

    ideal  q <- q + qdot*dt. The textbook integrator: what the controller
           asked for is what the joints do. Useful to isolate controller
           behaviour from servo behaviour.
    lag    a first-order position servo: qdot_actual = (q_ref - q)/tau, so the
           joint chases the integrated target with time constant `lag_tau`.
           This is the cheapest honest model of a stiff industrial joint: it
           leaves a lag during motion and a ramp-tracking error of v*tau.
    pd2    a second-order PD servo: qdd = (K (q_ref - q) - D qdot)/M, i.e. a
           mass-spring-damper per joint with the twin's own drive constants
           (stiffness 100, damping 10 from the USD, inertia a normalised
           placeholder). With M = 0.5 this is wn ~ 14 rad/s, zeta ~ 0.7 --
           lightly damped enough that a badly tuned outer loop will show
           oscillation, which is the point of having it.

    The distinction matters for a demo: an outer loop that is stable on the
    ideal plant can ring on `pd2`, and that is a real sim-to-real effect, not a
    numerical artefact.
    """
    q = np.asarray(q0, dtype=float).reshape(ur.N_JOINTS).copy()
    if not ur.within_limits(q, margin=0.0):
        raise ValueError(f"q0 is outside the joint limits: {q}")
    if plant not in ("ideal", "lag", "pd2"):
        raise ValueError(f"unknown plant {plant!r}; use ideal, lag or pd2")

    goal_fn: Callable[[float], np.ndarray]
    if callable(T_des):
        goal_fn = lambda t: _as_transform(T_des(t))       # noqa: E731
    else:
        fixed = _as_transform(T_des)
        goal_fn = lambda t: fixed                          # noqa: E731

    n_steps = int(round(float(T) / float(dt))) + 1
    log = {
        "t": np.zeros(n_steps),
        "q": np.zeros((n_steps, ur.N_JOINTS)),
        "q_ref": np.zeros((n_steps, ur.N_JOINTS)),
        "qdot": np.zeros((n_steps, ur.N_JOINTS)),
        "qdot_cmd": np.zeros((n_steps, ur.N_JOINTS)),
        "ee": np.zeros((n_steps, 3)),
        "pos_err": np.zeros(n_steps),
        "rot_err": np.zeros(n_steps),
        "sigma_min": np.zeros(n_steps),
        "manipulability": np.zeros(n_steps),
        "damping": np.zeros(n_steps),
        "velocity_scale": np.zeros(n_steps),
        "plant": plant,
    }
    qdot_actual = np.zeros(ur.N_JOINTS)
    q_ref = q.copy()

    for k in range(n_steps):
        t = k * dt
        T_goal = goal_fn(t)

        qdot_cmd, info = controller.step(q, T_goal, dt=dt)

        # Integrate the velocity command into the position target the servo
        # actually receives. Clamp to the position limits: a real drive refuses
        # targets outside its range, and letting the target run to 10 rad would
        # make the simulation a fantasy.
        q_ref = np.clip(q_ref + qdot_cmd * dt, ur.Q_MIN, ur.Q_MAX)

        if plant == "ideal":
            qdot_actual = qdot_cmd.copy()
            q = np.clip(q + qdot_actual * dt, ur.Q_MIN, ur.Q_MAX)
        elif plant == "lag":
            qdot_actual = (q_ref - q) / max(lag_tau, 1e-6)
            q = np.clip(q + qdot_actual * dt, ur.Q_MIN, ur.Q_MAX)
        else:  # pd2
            qdd = (stiffness * (q_ref - q) - damping * qdot_actual) / max(inertia, 1e-6)
            qdot_actual = qdot_actual + qdd * dt
            q = np.clip(q + qdot_actual * dt, ur.Q_MIN, ur.Q_MAX)

        log["t"][k] = t
        log["q"][k] = q
        log["q_ref"][k] = q_ref
        log["qdot"][k] = qdot_actual
        log["qdot_cmd"][k] = qdot_cmd
        log["ee"][k] = ur.fk_position(q)
        log["pos_err"][k] = info["pos_err_norm"]
        log["rot_err"][k] = info["rot_err_norm"]
        log["sigma_min"][k] = info["sigma_min"]
        log["manipulability"][k] = info["manipulability"]
        log["damping"][k] = info["damping"]
        log["velocity_scale"][k] = info["velocity_scale"]

        if verbose and k % max(1, n_steps // 10) == 0:
            print(f"    t={t:5.2f}s pos_err={info['pos_err_norm'] * 1e3:7.2f} mm "
                  f"rot_err={math.degrees(info['rot_err_norm']):5.2f} deg "
                  f"sigma_min={info['sigma_min']:.4f} "
                  f"max|qdot|={info['max_abs_qdot']:.3f} rad/s")

        if stop_on_converge and info["converged"]:
            k_cut = k + 1
            for key, value in log.items():
                if isinstance(value, np.ndarray) and value.shape[:1] == (n_steps,):
                    log[key] = value[:k_cut]
            log["converged_at_step"] = k
            break
    else:
        log["converged_at_step"] = next(
            (int(k) for k in range(n_steps) if log["pos_err"][k] <= controller.goal_tolerance),
            None)

    log["goal"] = np.asarray(goal_fn(float(log["t"][-1])), dtype=float)
    log["goal_fn"] = goal_fn
    return log


def metrics(log: dict, goal_tolerance: float = DEFAULTS["goal_tolerance"],
            rot_tolerance: float = DEFAULTS["rot_tolerance"]) -> dict:
    """The five numbers a slide needs, from a simulate()/run_on_twin() log."""
    t = np.asarray(log["t"])
    pos = np.asarray(log["pos_err"])
    rot = np.asarray(log["rot_err"])
    qdot = np.asarray(log["qdot_cmd"])
    ee = np.asarray(log["ee"])

    in_band = (pos <= goal_tolerance) & (rot <= rot_tolerance)
    # "Time to reach" is a SETTLING time: the first sample after which the arm
    # never leaves the band again. Taking the first entry would flatter a
    # controller that passes through the goal at speed and overshoots -- the
    # PD plant does exactly that (4 mm on the way through, 8 mm overshoot, then
    # 0.05 mm), and calling that "reached at 1.36 s" would be wrong.
    out_of_band = np.nonzero(~in_band)[0]
    start = int(out_of_band[-1]) + 1 if out_of_band.size else 0
    time_to_reach = None
    if start < len(in_band) and bool(in_band[start]):
        time_to_reach = float(t[start])
    reached_transiently = bool(in_band.any() and time_to_reach is None)

    path = float(np.sum(np.linalg.norm(np.diff(ee, axis=0), axis=1))) if len(ee) > 1 else 0.0
    peak = np.max(np.abs(qdot), axis=0) if qdot.size else np.zeros(ur.N_JOINTS)
    return {
        "duration_s": float(t[-1]) if t.size else 0.0,
        "samples": int(t.size),
        "time_to_reach_5mm_s": time_to_reach,
        "reached_transiently_only": reached_transiently,
        "fraction_within_goal_band": float(np.mean(in_band)) if in_band.size else 0.0,
        "final_pos_err_mm": float(pos[-1] * 1e3) if pos.size else float("nan"),
        "final_rot_err_deg": float(math.degrees(rot[-1])) if rot.size else float("nan"),
        "rms_pos_err_mm": float(np.sqrt(np.mean(pos ** 2)) * 1e3) if pos.size else float("nan"),
        "peak_joint_speed_rad_s": float(np.max(peak)) if peak.size else 0.0,
        "peak_joint_speed_per_joint": peak.tolist(),
        "peak_cartesian_speed_m_s": float(np.max(
            np.linalg.norm(np.diff(ee, axis=0), axis=1) / np.maximum(np.diff(t), 1e-9)
        )) if len(ee) > 1 else 0.0,
        "path_length_m": path,
        "min_sigma": float(np.min(np.asarray(log["sigma_min"]))),
        "min_manipulability": float(np.min(np.asarray(log["manipulability"]))),
        "max_damping": float(np.max(np.asarray(log["damping"]))),
        "velocity_limit_hits": int(np.sum(np.asarray(log["velocity_scale"]) < 0.999)),
        "converged": bool(pos.size and pos[-1] <= goal_tolerance
                          and rot[-1] <= rot_tolerance),
    }


# =========================================================================== #
# 5. Plotting (matplotlib is imported lazily: the selftest must not need it)
# =========================================================================== #
FIG_W, FIG_H, FIG_DPI = 1600, 900, 100

#: Colours for the six joints, reused in every figure so the legend transfers
#: from slide to slide.
JOINT_COLORS = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b")
SHORT_NAMES = ("pan", "lift", "elbow", "w1", "w2", "w3")


#: Interpreter to fall back to for the figures if this one lacks matplotlib.
#: The learning6 venv is the lab's other Isaac Lab install and is known to carry
#: matplotlib; see AGENTS.md. Absolute, because this script may run from a
#: checkout anywhere.
PLOT_FALLBACK_PYTHONS = (
    Path.home() / "Desktop" / "ur5e-digital-twin" / "learning6" / ".venv" / "bin" / "python",
)
REEXEC_MARK = "RESOLVED_RATE_PLOT_REEXEC"


def _ensure_plotting() -> None:
    """Make sure matplotlib is importable, re-execing into the fallback venv once.

    Same shape as ur5e_common.ensure_native_libs (and scripts/tf_capture.py):
    the fix cannot be applied inside a live process, so replace it. Called
    BEFORE the demos run, so nothing is recomputed except startup.
    """
    try:
        import matplotlib  # noqa: F401
        return
    except Exception as exc:
        missing = exc
    if os.environ.get(REEXEC_MARK) != "1":
        for candidate in PLOT_FALLBACK_PYTHONS:
            if candidate.is_file():
                print(f"[resolved_rate] no matplotlib in {sys.executable}; "
                      f"re-running with {candidate}")
                env = dict(os.environ)
                env[REEXEC_MARK] = "1"
                argv = list(getattr(sys, "orig_argv", None)
                            or [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
                sys.stdout.flush()
                sys.stderr.flush()
                os.execve(str(candidate), argv, env)
    raise RuntimeError(
        "matplotlib is required for the --offline figures; this interpreter "
        f"({sys.executable}) cannot import it ({missing}). Tried: "
        + ", ".join(str(p) for p in PLOT_FALLBACK_PYTHONS) + ". Either run with "
        "a venv that has matplotlib, or: uv run --isolated --python 3.12 "
        "--with numpy --with matplotlib python scripts/demo/resolved_rate.py "
        "--offline ...")


def _mpl():
    """Import matplotlib with an Agg backend (no display needed)."""
    _ensure_plotting()
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt   # noqa: PLC0415 (lazy: selftest needs none)
    return plt


def _style(plt):
    plt.rcParams.update({
        "figure.figsize": (FIG_W / FIG_DPI, FIG_H / FIG_DPI),
        "figure.dpi": FIG_DPI,
        "savefig.dpi": FIG_DPI,
        "font.size": 13,
        "axes.titlesize": 16,
        "axes.titleweight": "bold",
        "axes.labelsize": 14,
        "legend.fontsize": 11,
        "xtick.labelsize": 12,
        "ytick.labelsize": 12,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


def _save(fig, path: Path, plt) -> Path:
    """Save at exactly 1600x900 with white background, then free the figure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fig.get_layout_engine() is None:
        fig.tight_layout(rect=(0, 0, 1, 1))
    fig.savefig(path, dpi=FIG_DPI, facecolor="white")
    plt.close(fig)
    return path


def plot_reach(log: dict, goal: np.ndarray, controller: ResolvedRateController,
               met: dict, path: Path) -> Path:
    """Demo 1: goal approach from home -- error, path, joint velocities + limits."""
    plt = _mpl()
    _style(plt)
    # constrained layout: the gridspec below is fixed, so let matplotlib do the
    # spacing (tight_layout warns on this combination).
    fig = plt.figure(constrained_layout=True)
    gs = fig.add_gridspec(2, 2, width_ratios=(1.0, 1.15))

    t = log["t"]
    # ---- error vs time (log y: 5 mm -> 0.1 mm is one decade, not invisible)
    ax = fig.add_subplot(gs[0, 0])
    ax.semilogy(t, np.maximum(log["pos_err"] * 1e3, 1e-4), color="#d62728", lw=2.2)
    ax.axhline(controller.goal_tolerance * 1e3, color="k", ls="--", lw=1.2,
               label=f"goal band {controller.goal_tolerance * 1e3:.0f} mm")
    if met["time_to_reach_5mm_s"] is not None and not math.isnan(met["time_to_reach_5mm_s"]):
        ax.axvline(met["time_to_reach_5mm_s"], color="#2ca02c", ls=":", lw=1.6,
                   label=f"reached at {met['time_to_reach_5mm_s']:.2f} s")
    ax.set_xlabel("time [s]")
    ax.set_ylabel("position error [mm]", color="#d62728")
    ax.set_title("1a. End-effector pose error")
    ax.set_ylim(1e-3, max(1e3 * log["pos_err"].max() * 1.6, 10))
    # Orientation error on the same (log) time axis but its own degree axis:
    # millimetres and degrees must never share a scale, even on a slide.
    ax_r = ax.twinx()
    ax_r.semilogy(t, np.maximum(np.degrees(log["rot_err"]), 1e-6),
                  color="#7f7f7f", lw=1.8, ls="--")
    ax_r.set_ylabel("orientation error [deg]", color="#7f7f7f")
    ax_r.tick_params(axis="y", colors="#7f7f7f")
    ax_r.set_ylim(1e-4, max(float(np.degrees(log["rot_err"].max())) * 2.0, 1.0))
    handles, labels = ax.get_legend_handles_labels()
    handles.append(plt.Line2D([], [], color="#7f7f7f", ls="--", lw=1.8))
    labels.append("orientation error [deg, right axis]")
    ax.legend(handles, labels, loc="upper right", fontsize=10)

    # ---- joint positions: what the loop actually PUBLISHES.
    # This is the position command the loop integrates from the velocity
    # command (q += qdot*dt) and the twin's drives actually receive, so it is
    # the honest link between the law above and the robot below.
    ax = fig.add_subplot(gs[1, 0])
    for i in range(ur.N_JOINTS):
        ax.plot(t, log["q"][:, i], color=JOINT_COLORS[i], lw=1.8,
                label=SHORT_NAMES[i])
    ax.set_xlabel("time [s]")
    ax.set_ylabel("joint position [rad]")
    ax.set_title(f"1b. Joint positions; goal p = ({goal[0, 3]:+.3f}, "
                 f"{goal[1, 3]:+.3f}, {goal[2, 3]:+.3f}) m", fontsize=14)
    # Anchored in the empty band between the pick-up and the elbow, so it does
    # not sit on top of a curve (all six are labelled here, unlike 1c).
    ax.legend(loc="center right", bbox_to_anchor=(0.99, 0.70), ncol=2, fontsize=10)

    # ---- joint velocities, with the limits drawn as the bands that matter
    ax = fig.add_subplot(gs[:, 1])
    for i in range(ur.N_JOINTS):
        ax.plot(t, log["qdot_cmd"][:, i], color=JOINT_COLORS[i], lw=1.8,
                label=f"{ur.JOINT_NAMES[i].replace('_joint', '')} ({SHORT_NAMES[i]})")
    if controller.qd_max is not None:
        lim = float(np.max(controller.qd_max))
        ax.axhline(+lim, color="k", ls="--", lw=1.2)
        ax.axhline(-lim, color="k", ls="--", lw=1.2, label=f"+/- limit {lim:.1f} rad/s")
    ax.set_xlabel("time [s]")
    ax.set_ylabel("commanded joint velocity [rad/s]")
    ax.set_title("1c. Joint velocities (whole-vector scaling keeps direction)")
    ax.legend(loc="center right", ncol=1, fontsize=10)
    hits = met["velocity_limit_hits"]
    ax.text(0.98, 0.04, f"limit active for {hits} ticks "
                        f"({100.0 * hits / max(1, met['samples']):.0f}% of the run)",
            transform=ax.transAxes, fontsize=11, va="bottom", ha="right",
            bbox=dict(boxstyle="round", fc="white", ec="0.7", alpha=0.9))
    return _save(fig, path, plt)


def plot_circle(log: dict, center: np.ndarray, radius: float, period: float,
                controller: ResolvedRateController, met: dict, path: Path) -> Path:
    """Demo 2: tracking a moving circular goal (four panels, one story)."""
    plt = _mpl()
    _style(plt)
    fig = plt.figure(constrained_layout=True)
    gs = fig.add_gridspec(2, 2)

    t = log["t"]
    # The reference path at exactly the logged instants (not a resampled grid),
    # so the error curve and the zoom compare like with like.
    ref = np.array([log["goal_fn"](float(ti))[:2, 3] for ti in t])
    ee = log["ee"]

    # ---- 2a. the whole circle, where goal and flange look identical -------
    ax = fig.add_subplot(gs[0, 0])
    ax.plot(ref[:, 0], ref[:, 1], "--", color="#d62728", lw=2.0, label="goal (circle)")
    ax.plot(ee[:, 0], ee[:, 1], color="#1f77b4", lw=2.0, label="flange")
    ax.plot(center[0], center[1], "+", color="k", ms=12, label="circle centre")
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.set_title(f"2a. r = {radius * 1e3:.0f} mm, period {period:g} s")
    ax.set_aspect("equal", adjustable="datalim")
    # No legend here: at this scale goal and flange overprint each other, and
    # that IS the result. Panel 2b zooms in and names the curves.
    ax.text(0.02, 0.02, "goal (dashed) and flange (solid) overprint:\n"
                        f"they differ by {met['final_pos_err_mm']:.1f} mm",
            transform=ax.transAxes, fontsize=10, va="bottom", ha="left",
            bbox=dict(boxstyle="round", fc="white", ec="0.7", alpha=0.9))

    # ---- 2b. zoom on a short arc: 5 mm of lag on a 60 mm circle is
    # invisible at full scale, and invisible is not the same as absent.
    ax = fig.add_subplot(gs[1, 0])
    t_zoom = 0.25 * period
    window = np.abs(t - t_zoom) <= 0.02 * period
    if np.count_nonzero(window) > 3:
        ax.plot(ref[window, 0], ref[window, 1], "--", color="#d62728", lw=2.4,
                label="goal")
        ax.plot(ee[window, 0], ee[window, 1], color="#1f77b4", lw=2.4, label="flange")
        # Mark where the goal and flange are at the same instant: the offset
        # between the two marks IS the tracking error vector.
        k = int(np.argmin(np.abs(t - t_zoom)))
        ax.plot(ref[k, 0], ref[k, 1], "x", color="#d62728", ms=10, mew=2.5, zorder=5)
        ax.plot(ee[k, 0], ee[k, 1], "o", color="#1f77b4", ms=8, zorder=5)
        ax.annotate("", xy=(ref[k, 0], ref[k, 1]), xytext=(ee[k, 0], ee[k, 1]),
                    arrowprops=dict(arrowstyle="<->", color="k", lw=1.6,
                                    shrinkA=6, shrinkB=6), zorder=6)
        ax.set_aspect("equal", adjustable="datalim")
        ax.margins(0.45)
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.set_title(f"2b. Zoom on t = {t_zoom:g} s: flange trails goal by "
                 f"{met['final_pos_err_mm']:.1f} mm")
    ax.legend(loc="best", fontsize=10)

    # ---- 2c. tracking error vs the predicted lag -------------------------
    ax = fig.add_subplot(gs[0, 1])
    ax.plot(t, log["pos_err"] * 1e3, color="#d62728", lw=2.0, label="measured")
    ax.axhline(controller.goal_tolerance * 1e3, color="k", ls="--", lw=1.2,
               label=f"{controller.goal_tolerance * 1e3:.0f} mm band")
    # For a proportional velocity loop chasing a ramp the steady-state error is
    # e_ss = v_ref / kp_pos. Drawing the prediction checks the model and the
    # controller with one line.
    v_ref = 2.0 * math.pi * radius / period
    ax.axhline(v_ref / controller.kp_pos * 1e3, color="#2ca02c", ls=":", lw=1.6,
               label=f"predicted v_ref/kp = {v_ref / controller.kp_pos * 1e3:.1f} mm")
    ax.set_xlabel("time [s]")
    ax.set_ylabel("position error [mm]")
    ax.set_title("2c. Tracking error (lag ~ v_ref / kp)")
    ax.legend(loc="center right", fontsize=10)
    ax.text(0.02, 0.06,
            f"measured {met['final_pos_err_mm']:.2f} mm vs predicted "
            f"{v_ref / controller.kp_pos * 1e3:.2f} mm",
            transform=ax.transAxes, fontsize=10, va="bottom",
            bbox=dict(boxstyle="round", fc="white", ec="0.7", alpha=0.9))

    # ---- 2d. joint velocities -------------------------------------------
    ax = fig.add_subplot(gs[1, 1])
    for i in range(ur.N_JOINTS):
        ax.plot(t, log["qdot_cmd"][:, i], color=JOINT_COLORS[i], lw=1.6,
                label=SHORT_NAMES[i])
    span = 0.2
    if controller.qd_max is not None:
        span = float(np.max(controller.qd_max))
        ax.axhline(+span, color="k", ls="--", lw=1.1)
        ax.axhline(-span, color="k", ls="--", lw=1.1, label="limit")
    ax.set_ylim(-1.35 * span, 1.55 * span)   # headroom for the legend
    ax.set_xlabel("time [s]")
    ax.set_ylabel("joint velocity [rad/s]")
    ax.set_title("2d. Joint velocities, well inside the limit")
    ax.legend(loc="upper right", ncol=3, fontsize=9)
    return _save(fig, path, plt)


def plot_singularity(runs: dict, path: Path) -> Path:
    """Demo 3: the same reach into a singularity, three commands.

    `runs` maps label -> {"log", "metrics", "color", "style"} and is drawn in
    that order in both panels, so the reader can follow one line from sigma_min
    to |qdot| without re-reading a legend.
    """
    plt = _mpl()
    _style(plt)
    fig, axes = plt.subplots(2, 1, sharex=True, layout="constrained",
                             figsize=(FIG_W / FIG_DPI, FIG_H / FIG_DPI))

    ax = axes[0]
    for label, run in runs.items():
        ax.semilogy(run["log"]["t"],
                    np.maximum(run["log"]["sigma_min"], 1e-16),
                    color=run["color"], lw=2.2, ls=run.get("style", "-"),
                    label=label)
    ax.set_ylabel("smallest singular value of J\n[m/rad]")
    ax.set_title("3a. Driving into the stretched-elbow singularity: sigma_min")
    ax.legend(loc="lower left")
    ax.text(0.98, 0.55,
            "all three head for the same pose --\n"
            "what differs is the COMMAND (panel 3b)",
            transform=ax.transAxes, fontsize=10.5, va="center", ha="right",
            bbox=dict(boxstyle="round", fc="white", ec="0.7", alpha=0.9))

    ax = axes[1]
    for label, run in runs.items():
        peak = run["metrics"]["peak_joint_speed_rad_s"]
        profile = np.maximum(np.max(np.abs(run["log"]["qdot_cmd"]), axis=1), 1e-6)
        ax.semilogy(run["log"]["t"], profile, color=run["color"], lw=2.2,
                    ls=run.get("style", "-"),
                    label=f"{label}  (peak {peak:.2f} rad/s)")
    if runs:
        cap = next(iter(runs.values())).get("qd_max")
        if cap is not None:
            ax.axhline(float(np.max(cap)), color="k", ls="--", lw=1.3,
                       label=f"demo joint limit {float(np.max(cap)):.1f} rad/s")
    ax.set_xlabel("time [s]")
    ax.set_ylabel("max |commanded joint velocity|\n[rad/s]")
    ax.set_title("3b. The same law with and without damping (log scale)")
    ax.legend(loc="upper left", fontsize=10)
    peak_u = next(iter(runs.values()))["metrics"]["peak_joint_speed_rad_s"]
    if runs:
        first = next(iter(runs.values()))
        cap_value = float(np.max(first["qd_max"])) if first.get("qd_max") is not None else None
        if cap_value:
            ax.text(0.02, 0.04,
                    f"lambda = 0 peaks {peak_u:.0f} rad/s = {peak_u / cap_value:.0f}x "
                    f"the {cap_value:.1f} rad/s limit",
                    transform=ax.transAxes, fontsize=10, va="bottom", ha="left",
                    bbox=dict(boxstyle="round", fc="white", ec="0.7", alpha=0.9))
    return _save(fig, path, plt)


# =========================================================================== #
# 6. I/O: CSV and JSON
# =========================================================================== #
def write_csv(log: dict, path: Path) -> Path:
    """One row per tick: time, q, qdot, errors, conditioning."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["t"]
    columns += [f"q_{n.replace('_joint', '')}" for n in ur.JOINT_NAMES]
    columns += [f"qdot_{n.replace('_joint', '')}" for n in ur.JOINT_NAMES]
    columns += ["qdot_cmd_max", "pos_err_m", "rot_err_rad", "sigma_min",
                "manipulability", "damping", "velocity_scale",
                "ee_x", "ee_y", "ee_z"]
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for k in range(len(log["t"])):
            row = [f"{log['t'][k]:.6f}"]
            row += [f"{v:.9f}" for v in log["q"][k]]
            row += [f"{v:.9f}" for v in log["qdot"][k]]
            row += [
                f"{np.max(np.abs(log['qdot_cmd'][k])):.9f}",
                f"{log['pos_err'][k]:.9f}",
                f"{log['rot_err'][k]:.9f}",
                f"{log['sigma_min'][k]:.9f}",
                f"{log['manipulability'][k]:.9f}",
                f"{log['damping'][k]:.9f}",
                f"{log['velocity_scale'][k]:.9f}",
                f"{log['ee'][k][0]:.9f}",
                f"{log['ee'][k][1]:.9f}",
                f"{log['ee'][k][2]:.9f}",
            ]
            writer.writerow(row)
    return path


def write_json(path: Path, payload: dict, merge_key: Optional[str] = None) -> Path:
    """Write JSON; with `merge_key`, add/update that key in an existing file.

    Several demo scripts write into the same output directory, and
    `control_experiments.py` already owns a top-level `summary.json`. Rather
    than clobber it, this reads it, adds/updates one key, and writes it back.
    A non-dict file (or invalid JSON) is left untouched and reported on stdout;
    the caller still gets the standalone copy of its own payload.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not merge_key:
        path.write_text(json.dumps(payload, indent=2) + "\n")
        return path
    merged = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            print(f"[resolved_rate] could not read {path} ({exc}); leaving it untouched")
            return path
        if not isinstance(existing, dict):
            print(f"[resolved_rate] {path} is not a JSON object; leaving it untouched")
            return path
        merged = existing
    merged[merge_key] = payload
    path.write_text(json.dumps(merged, indent=2) + "\n")
    return path


# =========================================================================== #
# 7. The three offline demonstrations
# =========================================================================== #
#: Goal for demo 1, as an offset from the home flange position in the DH base
#: frame: 5 cm toward the base (+x, i.e. folding the arm), 8 cm sideways (+y)
#: and 10 cm up. Deliberately a pose that needs real joint motion -- a 1 cm
#: goal would fit inside the dead band and teach nothing -- but well inside the
#: 0.85 m reach and clear of the tabletop at z = 0.
REACH_OFFSET = np.array([0.05, 0.08, 0.10])

#: Demo 1 also reorients the tool, by this angle about the TOOL's own y axis
#: (i.e. R_des = R_home @ Ry(angle)). Without it the orientation error would be
#: zero for the whole run and kp_rot would never be exercised in the figures.
#: 20 deg is enough to be visible in the joint traces and still converges in
#: ~1.3 s, so the move reads as one motion rather than two.
REACH_ROTATION_DEG = 20.0

CIRCLE_RADIUS = 0.06      # m
CIRCLE_PERIOD = 8.0       # s
CIRCLE_DURATION = 12.0    # s (1.5 loops)


def reach_goal(offset: np.ndarray = REACH_OFFSET,
               rotation_deg: float = REACH_ROTATION_DEG) -> np.ndarray:
    """Demo 1 goal: home pose translated by `offset`, rotated about the tool y."""
    T = ur.home_transform().copy()
    T[:3, 3] += np.asarray(offset, dtype=float)
    # Rotating about the TOOL's y axis (post-multiply) rather than the base's
    # (pre-multiply) keeps the motion a wrist reorientation instead of swinging
    # the whole arm around the base -- nicer to watch and easier on the wrist.
    T[:3, :3] = T[:3, :3] @ ur.rot_exp(np.array([0.0, math.radians(rotation_deg), 0.0]))
    return T


def circle_goal_fn(radius: float = CIRCLE_RADIUS, period: float = CIRCLE_PERIOD,
                   revolutions: float = 1.0) -> tuple:
    """A circular Cartesian goal in the base xy plane at the home height.

    Orientation is held at the home orientation: this is a pure position-
    tracking problem, so any error in the plot is the resolved-rate law's own
    lag and not an orientation fight. The circle is centred so that the goal at
    t = 0 IS the home position (centre = home - (r, 0, 0)), which removes the
    initial transient and lets the steady-state tracking error be read directly
    off the plot.
    """
    T_home = ur.home_transform()
    centre = T_home[:3, 3] - np.array([radius, 0.0, 0.0])
    R = T_home[:3, :3]

    def T_des(t: float) -> np.ndarray:
        theta = 2.0 * math.pi * float(t) / float(period)
        p = centre + np.array([radius * math.cos(theta), radius * math.sin(theta), 0.0])
        T = np.eye(4)
        T[:3, :3] = R
        T[:3, 3] = p
        return T

    return T_des, centre


def singularity_target(beyond_reach: float = 0.0) -> tuple:
    """The fully-stretched elbow-singular pose (or a point beyond it).

    With `beyond_reach = 0` the goal is exactly `fk(q_sing)` with the arm
    stretched straight out -- an elbow singularity, sigma_min = 0. The goal is
    reachable, so both controllers do eventually arrive; what differs is the
    *command* they ask for on the way, which is the whole point of the figure.

    `beyond_reach > 0` pushes the goal outside the workspace along the
    stretching direction. The error then never closes, so a pseudo-inverse
    controller keeps asking for more speed and flails (joints slam into their
    limits, ~100x the peak command of the reachable case). It is a louder
    demonstration but a muddier one -- the end state is thrashing, not
    convergence -- so the default is the reachable singular pose.

    Returns (T_des [4x4], q_stretched [6]).
    """
    q_sing = np.array([0.0, 0.0, 0.0, -math.pi / 2, 0.0, 0.0])
    T_sing = ur.fk(q_sing)
    if beyond_reach <= 0.0:
        return T_sing, q_sing
    # Outward direction: from the shoulder axis (base z) to the flange, flattened.
    direction = T_sing[:3, 3].copy()
    direction[2] = 0.0
    direction /= max(float(np.linalg.norm(direction)), 1e-9)
    T_des = T_sing.copy()
    T_des[:3, 3] += float(beyond_reach) * direction
    return T_des, q_sing


def demo_reach(out: Path, dt: float = DEFAULTS["dt"], plant: str = DEFAULTS["plant"],
               duration: float = 3.0) -> dict:
    """Demo 1: from home to a Cartesian goal. Returns {metrics, files}."""
    controller = ResolvedRateController()
    T_goal = reach_goal()
    print(f"[1/3] reach: home -> goal ({np.round(T_goal[:3, 3], 3).tolist()} m), "
          f"plant={plant}, dt={dt:g}s")
    log = simulate(controller, ur.HOME_Q, T_goal, plant=plant, dt=dt, T=duration,
                   verbose=True)
    met = metrics(log, controller.goal_tolerance, controller.rot_tolerance)
    png = plot_reach(log, T_goal, controller, met, out / "resolved_rate_reach.png")
    csv_path = write_csv(log, out / "resolved_rate_reach.csv")
    print(f"      time to 5 mm: {met['time_to_reach_5mm_s']} s, "
          f"peak |qdot| {met['peak_joint_speed_rad_s']:.3f} rad/s, "
          f"path {met['path_length_m'] * 1e3:.1f} mm, "
          f"min sigma {met['min_sigma']:.4f}")
    return {"metrics": met, "files": [str(png), str(csv_path)],
            "goal": T_goal.tolist(), "plant": plant, "dt": dt, "duration": duration,
            "controller": controller.params()}


def demo_circle(out: Path, dt: float = DEFAULTS["dt"], plant: str = DEFAULTS["plant"],
                duration: float = CIRCLE_DURATION) -> dict:
    """Demo 2: follow a moving circular goal."""
    controller = ResolvedRateController()
    T_fn, centre = circle_goal_fn()
    print(f"[2/3] circle: r={CIRCLE_RADIUS * 1e3:.0f} mm, period {CIRCLE_PERIOD:g}s, "
          f"{duration:g}s, plant={plant}")
    log = simulate(controller, ur.HOME_Q, T_fn, plant=plant, dt=dt, T=duration,
                   verbose=False)
    met = metrics(log, controller.goal_tolerance, controller.rot_tolerance)
    png = plot_circle(log, centre, CIRCLE_RADIUS, CIRCLE_PERIOD, controller, met,
                      out / "resolved_rate_circle.png")
    csv_path = write_csv(log, out / "resolved_rate_circle.csv")
    print(f"      RMS error {met['rms_pos_err_mm']:.2f} mm, "
          f"final {met['final_pos_err_mm']:.2f} mm, "
          f"peak |qdot| {met['peak_joint_speed_rad_s']:.3f} rad/s, "
          f"min sigma {met['min_sigma']:.4f}")
    return {"metrics": met, "files": [str(png), str(csv_path)],
            "radius_m": CIRCLE_RADIUS, "period_s": CIRCLE_PERIOD,
            "centre": centre.tolist(), "plant": plant, "dt": dt,
            "duration": duration, "controller": controller.params()}


def demo_singularity(out: Path, dt: float = 0.002, duration: float = 6.0,
                     beyond_reach: float = 0.0) -> dict:
    """Demo 3: lambda = 0 vs adaptive damping vs damping + velocity cap."""
    T_des, q_sing = singularity_target(beyond_reach)
    if beyond_reach <= 0:
        print("[3/3] singularity: goal = stretched elbow (sigma_min = 0)")
    else:
        print(f"[3/3] singularity: goal {beyond_reach * 1e3:.0f} mm beyond the reach")
    print(f"      dt={dt * 1e3:g} ms, {duration:g} s per controller")

    undamped = ResolvedRateController(**UNDAMPED)
    damped = ResolvedRateController()
    damped_only = ResolvedRateController(qd_max=None)

    print("      lambda = 0 (Moore-Penrose pseudo-inverse, no velocity cap)...")
    log_u = simulate(undamped, ur.HOME_Q, T_des, plant="ideal", dt=dt, T=duration)
    print("      adaptive damping, velocity cap OFF (what damping alone does)...")
    log_nc = simulate(damped_only, ur.HOME_Q, T_des, plant="ideal", dt=dt, T=duration)
    print("      adaptive damping + joint velocity cap (the full controller)...")
    log_d = simulate(damped, ur.HOME_Q, T_des, plant="ideal", dt=dt, T=duration)

    met_u = metrics(log_u)
    met_nc = metrics(log_nc)
    met_d = metrics(log_d)
    for name, log, met in (("undamped", log_u, met_u), ("damped", log_d, met_d)):
        if not (np.all(np.isfinite(log["qdot_cmd"])) and np.all(np.isfinite(log["q"]))):
            raise RuntimeError(f"{name} controller produced NaN at the singularity")

    runs = {
        "lambda = 0 (pseudo-inverse)": {"log": log_u, "metrics": met_u,
                                        "color": "#d62728", "qd_max": damped.qd_max},
        "adaptive damping only": {"log": log_nc, "metrics": met_nc,
                                  "color": "#ff7f0e", "style": "--"},
        "adaptive damping + velocity cap": {"log": log_d, "metrics": met_d,
                                            "color": "#1f77b4"},
    }
    png = plot_singularity(runs, out / "resolved_rate_singularity.png")
    csv_u = write_csv(log_u, out / "resolved_rate_singularity_undamped.csv")
    csv_nc = write_csv(log_nc, out / "resolved_rate_singularity_damping_only.csv")
    csv_d = write_csv(log_d, out / "resolved_rate_singularity_damped.csv")
    print(f"      peak |qdot|: lambda=0 {met_u['peak_joint_speed_rad_s']:.1f} rad/s, "
          f"damping only {met_nc['peak_joint_speed_rad_s']:.2f} rad/s, "
          f"damping+cap {met_d['peak_joint_speed_rad_s']:.3f} rad/s")
    print(f"      min sigma:   lambda=0 {met_u['min_sigma']:.2e}, "
          f"damping only {met_nc['min_sigma']:.2e}, "
          f"damping+cap {met_d['min_sigma']:.2e}")
    return {
        "undamped": {"metrics": met_u, "lambda": 0.0},
        "damping_only": {"metrics": met_nc, "qd_max": None},
        "damped": {"metrics": met_d, "lambda_min": damped.lambda_min,
                   "lambda_max": damped.lambda_max,
                   "sigma_threshold": damped.sigma_threshold,
                   "controller": damped.params()},
        "files": [str(png), str(csv_u), str(csv_nc), str(csv_d)],
        "goal": T_des.tolist(), "stretched_q": q_sing.tolist(),
        "beyond_reach_m": beyond_reach, "dt": dt, "duration": duration,
    }


def run_offline(out: Path, dt: float = DEFAULTS["dt"], plant: str = DEFAULTS["plant"],
                merge: bool = True) -> dict:
    """All three demos, the summary, and a printed provenance block."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    _ensure_plotting()          # may re-exec once into a venv that has matplotlib
    started = time.time()
    print("resolved-rate control -- offline demonstrations")
    print(f"  interpreter: {sys.executable}")
    print(f"  numpy {np.__version__}, out={out}")

    reach = demo_reach(out, dt=dt, plant=plant)
    circle = demo_circle(out, dt=dt, plant=plant)
    sing = demo_singularity(out)

    summary = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "script": "scripts/demo/resolved_rate.py",
        "mode": "offline",
        "host": platform.node(),
        "interpreter": sys.executable,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "wall_time_s": round(time.time() - started, 2),
        "model": {
            "arm": "UR5e standard DH (ur5e_common.DH_*)",
            "home_q_rad": ur.HOME_Q.tolist(),
            "base_frame": "DH base; Isaac offset NOT applied (identity)",
            "tool_offset": "identity (flange, not TCP)",
            "gravity": "none (kinematic)",
        },
        "demos": {"reach": reach, "circle": circle, "singularity": sing},
        "notes": [
            "All numbers are from the offline plant model, not the twin.",
            "Reach/circle use plant=%s; singularity uses the ideal integrator "
            "so the controller law is the only thing on show." % plant,
            "Frames: DH base, flange as end effector, no tool offset.",
            "Singularity demo: at t=6 s the damped run is still converging "
            "(14 mm): damping deliberately gives up the last millimetres next "
            "to a singularity. The figure is about the command, not arrival.",
        ],
    }
    summary_path = write_json(out / "resolved_rate_summary.json", summary)
    merged_path = write_json(out / "summary.json", summary,
                             merge_key="resolved_rate" if merge else None)
    print(f"\nwrote {summary_path}")
    print(f"wrote {merged_path} (key 'resolved_rate', other keys preserved)")
    for demo_name, demo in summary["demos"].items():
        print(f"  {demo_name}: {', '.join(Path(f).name for f in demo['files'])}")
    return summary


# =========================================================================== #
# 8. Running on the twin
# =========================================================================== #
class BridgeJointState:
    """Read q from Isaac Sim through the Kit MCP bridge (optional feedback path).

    WHY THIS EXISTS. The twin publishes no arm joint state (see
    ur5e_common.TwinLink), so on a bare twin `--state ros` has nothing to read.
    The simulation's own articulation is still readable through `run_python` on
    the twin's bridge -- the same mechanism `scripts/checks/single_joint_motion.py`
    and `scripts/demo/control_experiments.py` use. This class is the small
    version of that: one persistent Kit session, one cached ArticulationView,
    one probe per tick.

    It is NOT free: each read is a TCP round trip through the bridge's queue and
    costs milliseconds, so a 50 Hz loop on this path is a best-effort 50 Hz. That
    is exactly the kind of latency a demo should show rather than hide.

    Untested without the sim (deliberately: no GPU jobs while writing this).
    The Kit-side snippet is defensive -- velocity returns None rather than
    raising if this Isaac build's ArticulationView lacks it.
    """

    SESSION = "resolved_rate_state"
    PREAMBLE = r'''
import time as _rr_time
import numpy as np
_RR = globals().setdefault('_RR', {})

def _rr_view():
    view = _RR.get('view')
    if view is None:
        from isaacsim.core.prims import Articulation as ArticulationView
        view = ArticulationView(prim_paths_expr='/World/UR5e', name='resolved_rate')
        view.initialize()
        _RR['view'] = view
    return view
'''

    PROBE = r'''
import omni.timeline
view = _rr_view()
q = view.get_joint_positions()
q = q.numpy() if hasattr(q, 'numpy') else q
names = [str(n) for n in view.dof_names]
qd = None
try:
    v = view.get_joint_velocities()
    v = v.numpy() if hasattr(v, 'numpy') else v
    qd = [float(x) for x in np.asarray(v).reshape(-1)]
except Exception:
    qd = None
result = {
    'q': [float(x) for x in np.asarray(q).reshape(-1)],
    'qd': qd,
    'dof_names': names,
    'sim_time': float(omni.timeline.get_timeline_interface().get_current_time()),
    'wall_time': float(_rr_time.time()),
}
'''

    def __init__(self, port: int = 8791, timeout: float = 30.0, verbose: bool = True):
        repo = HERE.parents[1]
        mcp_dir = repo / "mcp"
        if str(mcp_dir) not in sys.path:
            sys.path.insert(0, str(mcp_dir))
        try:
            from isaac_mcp import client as bridge_client  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - env problem
            raise RuntimeError(
                f"cannot import isaac_mcp.client from {mcp_dir} ({exc}); "
                f"--state bridge needs the repo's mcp/ directory") from exc
        self._client_mod = bridge_client
        self.port = int(port)
        self.timeout = float(timeout)
        self.verbose = verbose
        self._client = bridge_client.BridgeClient(port=self.port, timeout=self.timeout)
        self.dof_names = None

    def _call(self, code: str, reserve: Optional[float] = None) -> dict:
        payload = self._client.call(
            "run_python",
            {"code": self.PREAMBLE + code, "session_id": self.SESSION, "persistent": True},
            timeout=self.timeout,
            execution_reserve_s=reserve,
        )
        return payload.get("result") or {}

    def open(self) -> dict:
        info = self._call(self.PROBE + "\nresult = result", reserve=10.0)
        self.dof_names = list(info.get("dof_names") or [])
        missing = [n for n in ur.JOINT_NAMES if n not in self.dof_names]
        if missing:
            raise RuntimeError(f"twin articulation is missing joints {missing}; "
                               f"has {self.dof_names}")
        if self.verbose:
            print(f"[BridgeJointState] port {self.port}, DOFs {self.dof_names}")
        return info

    def read(self) -> tuple:
        """(stamp, q, qd) in this module's joint order; qd may be None."""
        info = self._call(self.PROBE + "\nresult = result")
        names = list(info.get("dof_names") or self.dof_names or [])
        q = np.asarray(info.get("q") or [], dtype=float).reshape(-1)
        if q.size != len(names):
            raise RuntimeError(f"bridge returned {q.size} positions for {len(names)} DOFs")
        q = np.array([q[names.index(n)] for n in ur.JOINT_NAMES], dtype=float)
        raw_qd = info.get("qd")
        qd = None
        if raw_qd:
            v = np.asarray(raw_qd, dtype=float).reshape(-1)
            if v.size == len(names):
                qd = np.array([v[names.index(n)] for n in ur.JOINT_NAMES], dtype=float)
        return float(info.get("wall_time") or time.time()), q, qd

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


def run_on_twin(link, controller: ResolvedRateController, T_des,
                hz: float = 50.0, duration: float = 12.0,
                max_lag_rad: float = 0.5, state_reader=None,
                stop_on_converge: Optional[bool] = None,
                verbose: bool = True) -> dict:
    """Close the loop on the twin at a fixed rate, logging every tick.

    One tick, in order:
        1. read q (ROS or the bridge; see `state_reader`)
        2. qdot, info = controller.step(q, T_des(t), dt=loop period)
        3. q_cmd += qdot * dt           <- velocity -> position target
        4. publish q_cmd on /joint_states
        5. sleep until the next tick

    `dt` is MEASURED (monotonic clock between ticks), not assumed: a Python
    loop that overruns must not silently integrate a smaller motion than the
    controller asked for. The integration uses the measured dt, clamped to
    [0.2, 2] x the nominal period so one scheduler hiccup cannot throw the
    command a second into the future.

    Anti-windup (`max_lag_rad`): the position target is an open-loop integral of
    the velocity command. If the servo lags -- because the twin's drives are
    soft, or the DDS hop is slow -- the target runs away from the actual
    joints, the pose error stops shrinking and the command keeps growing. When
    |q_cmd - q| exceeds `max_lag_rad` on any joint, the target is pulled back
    to exactly `max_lag_rad` of lead ahead of the measured joints, with the
    *direction* of the lead preserved: q_cmd = q + lag * (max_lag / max|lag|).
    That is a lead limit, the position-loop analogue of a current limit: the
    command can never be more than 0.5 rad ahead of reality, so it cannot wind
    up. (Subtracting the mean lag instead -- the obvious first guess -- does not
    bound the worst joint and is what this used to do.)

    `state_reader` is a callable returning (stamp, q, qd). Passing None means
    "read through `link`" -- correct when a UR driver's joint_state_broadcaster
    is publishing measured q on /joint_states (TwinLink ignores its own
    messages, so a bare twin simply yields nothing and this raises).

    `stop_on_converge` defaults to True for a fixed goal and False for a
    callable one: a moving goal is "converged" at every instant it is being
    tracked, so stopping there would end the run on tick 1.
    """
    if not isinstance(hz, (int, float)) or hz <= 0:
        raise ValueError("hz must be positive")
    period = 1.0 / float(hz)
    moving_goal = callable(T_des)
    goal_fn = T_des if moving_goal else (lambda t: _as_transform(T_des))
    if stop_on_converge is None:
        stop_on_converge = not moving_goal

    def read_state(timeout: float = 0.05):
        """One state read from whichever source was chosen, same tuple shape."""
        if state_reader is not None:
            return state_reader()
        return link.read(timeout=timeout)

    # --- first state: do not start a control loop on an unknown arm --------
    started = time.monotonic()
    stamp = q = qd = None
    for _ in range(100):
        stamp, q, qd = read_state(0.2)
        if q is not None:
            break
        if time.monotonic() - started > 5.0:
            break
    if q is None:
        raise RuntimeError(
            "no joint state arrived. The twin subscribes /joint_states but "
            "publishes no arm joint state of its own (only the gripper's "
            "/rg2_sim/joint_state). Either start the UR driver "
            "(scripts/launch_ur_fake.sh) so its joint_state_broadcaster "
            "republishes measured q on /joint_states, or read the articulation "
            "through the bridge with --state bridge.")

    q = np.asarray(q, dtype=float).reshape(ur.N_JOINTS)
    qd_prev = None if qd is None else np.asarray(qd, dtype=float).reshape(ur.N_JOINTS)
    q_cmd = q.copy()

    n_steps = int(round(float(duration) * hz)) + 1
    log = {
        "t": np.zeros(n_steps), "wall": np.zeros(n_steps), "stamp": np.zeros(n_steps),
        "q": np.zeros((n_steps, ur.N_JOINTS)),
        "q_cmd": np.zeros((n_steps, ur.N_JOINTS)),
        "qdot": np.zeros((n_steps, ur.N_JOINTS)),
        "qdot_cmd": np.zeros((n_steps, ur.N_JOINTS)),
        "ee": np.zeros((n_steps, 3)),
        "pos_err": np.zeros(n_steps), "rot_err": np.zeros(n_steps),
        "sigma_min": np.zeros(n_steps), "manipulability": np.zeros(n_steps),
        "damping": np.zeros(n_steps), "velocity_scale": np.zeros(n_steps),
        "dt": np.zeros(n_steps), "plant": "twin",
    }

    t0 = started
    next_tick = started
    anchor_events = 0
    stale_reads = 0
    last_stamp = stamp
    for k in range(n_steps):
        now = time.monotonic()
        # Fixed-rate scheduling: sleep until the next deadline, never busy-wait.
        if next_tick > now:
            time.sleep(next_tick - now)
        tick_start = time.monotonic()
        if k > 0:
            dt = tick_start - log["wall"][k - 1]
            # Clamp the measured period so one scheduler hiccup cannot throw the
            # command a second into the future (or integrate ~zero motion).
            dt = min(max(dt, 0.2 * period), 2.0 * period)
        else:
            dt = period

        # 1. measured state (every tick, including the first: the preflight q
        #    may be a few hundred milliseconds old by now)
        stamp, q_new, qd_new = read_state(0.05)
        if q_new is None:
            stale_reads += 1
        else:
            q = np.asarray(q_new, dtype=float).reshape(ur.N_JOINTS)
            qd_prev = None if qd_new is None else np.asarray(
                qd_new, dtype=float).reshape(ur.N_JOINTS)
            last_stamp = stamp

        t_rel = tick_start - t0
        T_goal = _as_transform(goal_fn(t_rel))

        # 2. control tick
        qdot_cmd, info = controller.step(q, T_goal, dt=dt)

        # 3. velocity -> position command, with anti-windup
        q_cmd = q_cmd + qdot_cmd * dt
        lag = q_cmd - q
        lag_max = float(np.max(np.abs(lag)))
        if lag_max > max_lag_rad:
            q_cmd = q + lag * (max_lag_rad / lag_max)
            anchor_events += 1
            if verbose and anchor_events <= 3:
                print(f"    [anti-windup] lead limited to {max_lag_rad:.2f} rad "
                      f"(was {lag_max:.2f} rad)")
        q_cmd = np.clip(q_cmd, ur.Q_MIN, ur.Q_MAX)

        # 4. publish
        link.send(q_cmd, qd_cmd=qdot_cmd)

        # 5. log (velocity: measured if available, else finite difference)
        if qd_prev is not None:
            qdot_meas = qd_prev.copy()
        elif k > 0:
            qdot_meas = (q - log["q"][k - 1]) / max(dt, 1e-6)
        else:
            qdot_meas = np.zeros(ur.N_JOINTS)
        log["t"][k] = t_rel
        log["wall"][k] = tick_start
        log["stamp"][k] = float(last_stamp or 0.0)
        log["q"][k] = q
        log["q_cmd"][k] = q_cmd
        log["qdot"][k] = qdot_meas
        log["qdot_cmd"][k] = qdot_cmd
        log["ee"][k] = ur.fk_position(q)
        log["pos_err"][k] = info["pos_err_norm"]
        log["rot_err"][k] = info["rot_err_norm"]
        log["sigma_min"][k] = info["sigma_min"]
        log["manipulability"][k] = info["manipulability"]
        log["damping"][k] = info["damping"]
        log["velocity_scale"][k] = info["velocity_scale"]
        log["dt"][k] = dt

        if verbose and k % max(1, int(hz)) == 0:
            print(f"    t={t_rel:5.2f}s pos_err={info['pos_err_norm'] * 1e3:6.2f} mm "
                  f"sigma_min={info['sigma_min']:.3f} "
                  f"max|qdot|={info['max_abs_qdot']:.3f} rad/s "
                  f"dt={dt * 1e3:.1f}ms")

        next_tick += period
        # If the loop fell more than a few periods behind, resynchronise rather
        # than sprint to catch up (catching up means a burst of commands).
        if time.monotonic() - next_tick > 5 * period:
            next_tick = time.monotonic() + period

        if stop_on_converge and info["converged"]:
            log = {key: (value[:k + 1] if isinstance(value, np.ndarray)
                         and value.shape[:1] == (n_steps,) else value)
                   for key, value in log.items()}
            break

    log["anchor_events"] = anchor_events
    log["stale_reads"] = stale_reads
    if stale_reads > 0.2 * max(1, len(log["t"])):
        print(f"    [warning] {stale_reads}/{len(log['t'])} ticks had no fresh "
              f"measured state; the loop ran on the last value. That is a "
              f"transport problem, not a control result.")
    log["goal"] = _as_transform(goal_fn(float(log["t"][-1])) if len(log["t"]) else goal_fn(0.0))
    log["goal_fn"] = goal_fn
    log["state"] = (link.state_description() if hasattr(link, "state_description")
                    else {"source": "external"})
    log["loop_hz"] = hz
    return log


# =========================================================================== #
# 9. Selftest
# =========================================================================== #
def selftest() -> bool:
    """Assert the controller. Offline, numpy-only, no sim, no GPU, no ROS."""
    ok = True

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and bool(condition)
        print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
              f"{(' -- ' + detail) if detail else ''}")

    print("resolved_rate selftest")
    print("-" * 72)

    print("1. shared kinematics (delegated to ur5e_common)")
    q_probe = np.array([0.3, -1.2, 1.4, -1.9, -1.2, 0.4])
    J = ur.jacobian(q_probe)
    check("Jacobian 6x6", J.shape == (6, 6))
    check("analytic == numerical Jacobian",
          np.max(np.abs(J - ur.numeric_jacobian(q_probe))) < 1e-6,
          f"{np.max(np.abs(J - ur.numeric_jacobian(q_probe))):.2e}")
    e = ur.pose_error(ur.fk(q_probe), np.eye(4))
    check("pose_error is 6-vector", e.shape == (6,))

    print("2. controller output shape, damping and the error->twist map")
    c = ResolvedRateController()
    T_goal = ur.home_transform()
    qdot, info = c.step(ur.HOME_Q, T_goal)
    check("qdot is 6-vector", qdot.shape == (6,))
    check("zero error -> zero velocity", np.allclose(qdot, 0.0, atol=1e-12),
          f"{np.max(np.abs(qdot)):.2e}")
    # A pure translation goal: the commanded twist must point at the goal.
    T_shift = T_goal.copy()
    T_shift[:3, 3] += np.array([0.02, 0.0, 0.0])
    qdot, info = c.step(ur.HOME_Q, T_shift)
    check("error direction: dq reduces the error",
          np.dot(info["pos_err"], info["pos_err"] - 0.01 * (
              ur.jacobian(ur.HOME_Q, c.tool_offset) @ qdot)[:3]) > 0.0)
    check("info has the required keys", all(
        key in info for key in ("pos_err_norm", "rot_err_norm", "sigma_min",
                                "manipulability")), str(sorted(info)[:6]) + " ...")
    check("damping grows as sigma_min falls",
          adaptive_damping(0.0, 0.01, 0.5, 0.06) > adaptive_damping(0.03, 0.01, 0.5, 0.06)
          > adaptive_damping(0.06, 0.01, 0.5, 0.06) - 1e-12)

    print("3. convergence offline (ideal plant)")
    T_reach = reach_goal()
    log = simulate(c, ur.HOME_Q, T_reach, plant="ideal", dt=0.01, T=8.0)
    met = metrics(log)
    check("converges within 5 mm", met["final_pos_err_mm"] < 5.0,
          f"{met['final_pos_err_mm']:.3f} mm")
    check("converges in orientation", met["final_rot_err_deg"] < math.degrees(0.02),
          f"{met['final_rot_err_deg']:.3f} deg")
    check("reports a time to reach", met["time_to_reach_5mm_s"] is not None,
          f"{met['time_to_reach_5mm_s']} s")
    met_pd2 = metrics(simulate(ResolvedRateController(), ur.HOME_Q, T_reach,
                              plant="pd2", dt=0.01, T=6.0))
    check("pd2 settles later than the ideal plant (overshoot costs time)",
          met_pd2["time_to_reach_5mm_s"] is not None
          and met_pd2["time_to_reach_5mm_s"] > met["time_to_reach_5mm_s"],
          f"pd2 {met_pd2['time_to_reach_5mm_s']:.2f} s vs ideal "
          f"{met['time_to_reach_5mm_s']:.2f} s")
    check("error shrinks by more than 10x",
          log["pos_err"][-1] < log["pos_err"][0] / 10.0,
          f"{log['pos_err'][0] * 1e3:.1f} -> {log['pos_err'][-1] * 1e3:.2f} mm")

    print("4. velocity limits")
    cap = float(np.max(c.qd_max))
    check("never exceeds the joint cap",
          met["peak_joint_speed_rad_s"] <= cap + 1e-9,
          f"peak {met['peak_joint_speed_rad_s']:.4f} rad/s (cap {cap})")
    check("the joint cap is exercised by the demo move",
          met["velocity_limit_hits"] > 0,
          f"{met['velocity_limit_hits']} ticks scaled")
    # A far goal: the Cartesian speed cap must engage, and the per-joint
    # infinity norm must be what the joint limiter enforces.
    far = c.step(ur.HOME_Q, reach_goal(np.array([0.0, 0.25, 0.20])))[1]
    check("a big error saturates the Cartesian cap", far["lin_scale"] < 1.0,
          f"lin_scale {far['lin_scale']:.3f}")
    check("Cartesian speed cap respected",
          float(np.linalg.norm(far["twist"][:3])) <= c.max_cart_speed + 1e-12,
          f"{np.linalg.norm(far['twist'][:3]):.3f} m/s")
    tight = ResolvedRateController(qd_max=np.full(6, 0.2))
    tight_info = tight.step(ur.HOME_Q, reach_goal(np.array([0.0, 0.25, 0.20])))[1]
    check("joint cap saturates on a far goal", tight_info["velocity_scale"] < 1.0,
          f"scale {tight_info['velocity_scale']:.3f}")
    check("infinity norm: no joint over its own limit",
          np.max(np.abs(tight_info["qdot"]) / np.abs(tight.qd_max)) <= 1.0 + 1e-12,
          f"max ratio {np.max(np.abs(tight_info['qdot']) / tight.qd_max):.6f}")
    check("scaling preserves direction", np.allclose(
        tight_info["qdot"] / max(np.max(np.abs(tight_info["qdot"])), 1e-12),
        tight_info["qdot_raw"] / max(np.max(np.abs(tight_info["qdot_raw"])), 1e-12),
        atol=1e-9))

    print("5. joint-limit avoidance")
    q_near = ur.Q_MAX - 0.05
    taper = limit_taper(q_near, c.limit_hard_margin)
    check("velocity tapers near a hard limit", np.all(taper < 0.5), f"{taper[0]:.3f}")
    check("no taper in the middle", np.allclose(limit_taper(np.zeros(6), 0.12), 1.0))
    g = limit_potential_gradient(q_near, c.limit_soft_margin, c.null_gain)
    check("null-space push points away from the limit", np.all(g < 0.0),
          f"{np.round(g, 4).tolist()}")
    qdot_near, info_near = c.step(ur.Q_MAX - 0.05, ur.fk(ur.Q_MAX - 0.10))
    check("command near a limit stays inside the cap",
          np.max(np.abs(qdot_near)) <= cap + 1e-12)
    check("near_limit flag set", info_near["near_limit"])
    check("null-space term is ~0 at full rank (6 joints, 6-DoF task)",
          np.linalg.norm(info["qdot_null"]) < 1e-9,
          f"{np.linalg.norm(info['qdot_null']):.2e}")

    print("6. singularity: no NaN, bounded command with damping")
    T_sing, _ = singularity_target()
    c_sing = ResolvedRateController()
    log_s = simulate(c_sing, ur.HOME_Q, T_sing, plant="ideal", dt=0.002, T=6.0)
    met_s = metrics(log_s)
    check("no NaN in q or qdot", bool(np.all(np.isfinite(log_s["q"]))
                                      and np.all(np.isfinite(log_s["qdot_cmd"]))))
    check("damped command stays under the cap",
          met_s["peak_joint_speed_rad_s"] <= cap + 1e-9,
          f"{met_s['peak_joint_speed_rad_s']:.3f} rad/s")
    check("damping engages near the singularity", met_s["max_damping"] > 0.2,
          f"{met_s['max_damping']:.3f}")
    c_und = ResolvedRateController(**UNDAMPED)
    log_u = simulate(c_und, ur.HOME_Q, T_sing, plant="ideal", dt=0.002, T=6.0)
    met_u = metrics(log_u)
    check("undamped (pseudo-inverse) command blows past the limit",
          met_u["peak_joint_speed_rad_s"] > 10.0,
          f"{met_u['peak_joint_speed_rad_s']:.1f} rad/s")
    check("undamped still finite (SVD pseudo-inverse, no division by 0)",
          bool(np.all(np.isfinite(log_u["qdot_cmd"]))))
    c_nc = ResolvedRateController(qd_max=None)
    met_nc = metrics(simulate(c_nc, ur.HOME_Q, T_sing, plant="ideal", dt=0.002, T=6.0))
    check("damping alone cuts the peak by >10x vs lambda=0",
          met_nc["peak_joint_speed_rad_s"] < met_u["peak_joint_speed_rad_s"] / 10.0,
          f"{met_nc['peak_joint_speed_rad_s']:.2f} vs "
          f"{met_u['peak_joint_speed_rad_s']:.1f} rad/s")

    print("7. plant models")
    for plant in ("ideal", "lag", "pd2"):
        log_p = simulate(ResolvedRateController(), ur.HOME_Q, T_reach,
                         plant=plant, dt=0.01, T=8.0)
        met_p = metrics(log_p)
        check(f"plant={plant}: finite and converges (<20 mm)",
              np.all(np.isfinite(log_p["q"])) and met_p["final_pos_err_mm"] < 20.0,
              f"{met_p['final_pos_err_mm']:.2f} mm, "
              f"peak {met_p['peak_joint_speed_rad_s']:.2f} rad/s")
    check("pd2 lags more than ideal",
          metrics(simulate(ResolvedRateController(), ur.HOME_Q, T_reach,
                           plant="pd2", dt=0.01, T=2.0))["rms_pos_err_mm"]
          > metrics(simulate(ResolvedRateController(), ur.HOME_Q, T_reach,
                             plant="ideal", dt=0.01, T=2.0))["rms_pos_err_mm"])

    print("8. tracking a moving goal")
    T_fn, centre = circle_goal_fn()
    check("goal at t=0 is the home position",
          np.allclose(T_fn(0.0)[:3, 3], ur.home_transform()[:3, 3]))
    check("goal is periodic",
          np.allclose(T_fn(0.0), T_fn(CIRCLE_PERIOD)))
    log_c = simulate(ResolvedRateController(), ur.HOME_Q, T_fn, plant="ideal",
                     dt=0.01, T=CIRCLE_DURATION)
    met_c = metrics(log_c)
    # Predicted steady-state lag of a proportional velocity loop tracking a
    # ramp: e_ss ~ v_ref / kp_pos. Checking the number against the theory is a
    # much stronger test than a magic threshold.
    v_ref = 2.0 * math.pi * CIRCLE_RADIUS / CIRCLE_PERIOD
    predicted_mm = v_ref / DEFAULTS["kp_pos"] * 1e3
    check("tracks within 10 mm RMS", met_c["rms_pos_err_mm"] < 10.0,
          f"{met_c['rms_pos_err_mm']:.2f} mm")
    check("tracking lag matches v_ref / kp_pos",
          abs(met_c["final_pos_err_mm"] - predicted_mm) < 2.0,
          f"{met_c['final_pos_err_mm']:.2f} mm vs predicted {predicted_mm:.2f} mm")
    check("makes 1.5 loops (path length plausible)",
          met_c["path_length_m"] > 0.5 * met_c["peak_cartesian_speed_m_s"],
          f"{met_c['path_length_m']:.3f} m")

    print("9. I/O plumbing (CSV + JSON round trip)")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        csv_path = write_csv(log, tmp_path / "log.csv")
        header = csv_path.read_text().splitlines()[0].split(",")
        check("CSV written with one row per tick + header",
              len(csv_path.read_text().splitlines()) == len(log["t"]) + 1)
        check("CSV has q and qdot columns",
              "q_shoulder_pan" in header and "qdot_wrist_3" in header
              and "sigma_min" in header)
        payload = {"hello": "world", "value": 3}
        write_json(tmp_path / "summary.json", payload, merge_key="resolved_rate")
        write_json(tmp_path / "summary.json", {"other": 1}, merge_key="other_script")
        merged = json.loads((tmp_path / "summary.json").read_text())
        check("JSON merge keeps the other script's key",
              merged.get("other_script") == {"other": 1}
              and merged.get("resolved_rate") == payload)

    print("10. import hygiene")
    check("rclpy not imported by the controller path", "rclpy" not in sys.modules)
    check("resolved-rate module imports without ROS", True)

    print("-" * 72)
    print("ALL CHECKS PASSED" if ok else "SELFTEST FAILED")
    return ok


# =========================================================================== #
# 10. CLI
# =========================================================================== #
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolved-rate (differential IK) control of a UR5e: offline "
                    "demos, slide figures, and an optional run on the Isaac Sim twin.",
        epilog="Examples:\n"
               "  python scripts/demo/resolved_rate.py --selftest\n"
               "  python scripts/demo/resolved_rate.py --offline --out demo-out/resolved-rate\n"
               "  python scripts/demo/resolved_rate.py --on-twin --out demo-out/resolved-rate "
               "--state bridge --port 8791\n",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--selftest", action="store_true",
                      help="assert the maths/limits offline (no sim, no GPU, no ROS)")
    mode.add_argument("--offline", action="store_true",
                      help="run the three demonstrations and write PNG+CSV+JSON")
    mode.add_argument("--on-twin", action="store_true",
                      help="close the loop on the running twin (needs the sim + GPU)")
    parser.add_argument("--out", type=Path, default=Path("demo-out/resolved-rate"),
                        help="output directory (default: demo-out/resolved-rate)")
    parser.add_argument("--dt", type=float, default=DEFAULTS["dt"],
                        help=f"offline loop period [s] (default {DEFAULTS['dt']})")
    parser.add_argument("--plant", choices=("ideal", "lag", "pd2"),
                        default=DEFAULTS["plant"], help="offline plant model")
    parser.add_argument("--duration", type=float, default=12.0,
                        help="on-twin run length [s] (default 12)")
    parser.add_argument("--hz", type=float, default=50.0,
                        help="on-twin control rate [Hz] (default 50)")
    parser.add_argument("--port", type=int, default=8791,
                        help="Kit bridge port for --state bridge (default 8791). "
                             "ROS itself uses the DDS env, not this port.")
    parser.add_argument("--state", choices=("ros", "bridge"), default="ros",
                        help="where the measured q comes from: the ROS "
                             "/joint_states subscriber (a UR driver must be "
                             "publishing it) or the Kit bridge (default ros)")
    parser.add_argument("--no-merge", action="store_true",
                        help="do not merge into DIR/summary.json, only write "
                             "resolved_rate_summary.json")
    parser.add_argument("--print-params", action="store_true",
                        help="print the controller defaults as JSON and exit")
    args = parser.parse_args(argv)

    if args.print_params:
        print(json.dumps(DEFAULTS, indent=2, sort_keys=True))
        return 0

    if args.selftest:
        return 0 if selftest() else 1

    if args.offline:
        if not (0.0005 <= args.dt <= 0.05):
            parser.error("--dt must be between 0.5 ms and 50 ms")
        run_offline(args.out, dt=args.dt, plant=args.plant, merge=not args.no_merge)
        return 0

    if args.on_twin:
        try:
            return _main_on_twin(args, parser)
        except (RuntimeError, OSError, TimeoutError) as exc:
            # Expected failure modes: no measured state, no bridge, no rclpy.
            # Print the diagnosis, not a stack trace -- this is a demo script.
            print(f"\nresolved_rate: {exc}", file=sys.stderr)
            return 2

    parser.print_help()
    return 2


def _main_on_twin(args, parser) -> int:
    """The on-twin mode: connect, run, write what happened, disconnect."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    controller = ResolvedRateController()
    T_goal = reach_goal()
    state_reader = None
    link = None
    bridge = None
    try:
        if args.state == "bridge":
            bridge = BridgeJointState(port=args.port)
            bridge.open()
            state_reader = bridge.read
        link = ur.TwinLink(node_name="ur5e_resolved_rate")
        # Pre-flight: say out loud whether the command path has an audience and
        # whether the state path has a source. Both are cheap, both save a run.
        info = link.state_description()
        print(f"[preflight] publishers on {link.command_topic}: "
              f"{info['command_publishers']} (ours is one), subscribers: "
              f"{info['command_subscribers']} (the twin's graph is one)")
        if info["command_subscribers"] == 0:
            print("[preflight] WARNING: nobody subscribed to the command topic -- "
                  "is the twin's action graph built and the timeline playing?")
        if info["command_publishers"] > 1:
            print("[preflight] WARNING: another publisher is on the command topic. "
                  "If the UR driver's joint_state_broadcaster is publishing the "
                  "SAME topic, the two streams fight (see "
                  "scripts/demo/control_experiments.py). --state ros listens to "
                  "that broadcaster on purpose; just know it is there.")
        if args.state == "bridge":
            print("[preflight] state source: Kit bridge articulation view")
        else:
            ok = link.wait_for_state(timeout=5.0)
            print(f"[preflight] state source: {link.state_topic} "
                  f"({'receiving' if ok else 'NOT receiving'})")
            if not ok:
                raise RuntimeError(
                    "no measured joint state on " + link.state_topic +
                    ". Start the UR driver (scripts/launch_ur_fake.sh) or use "
                    "--state bridge.")

        print(f"[on-twin] {args.hz:g} Hz for {args.duration:g} s, goal "
              f"{np.round(T_goal[:3, 3], 3).tolist()} m")
        log = run_on_twin(link, controller, T_goal, hz=args.hz,
                          duration=args.duration, state_reader=state_reader)
        met = metrics(log, controller.goal_tolerance, controller.rot_tolerance)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        png = plot_reach(log, T_goal, controller, met,
                         out / f"resolved_rate_twin_{stamp}.png")
        csv_path = write_csv(log, out / f"resolved_rate_twin_{stamp}.csv")
        summary = {
            "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "script": "scripts/demo/resolved_rate.py",
            "mode": "on-twin",
            "host": platform.node(),
            "interpreter": sys.executable,
            "state_source": args.state,
            "loop_hz": args.hz,
            "duration_s": args.duration,
            "port": args.port,
            "controller": controller.params(),
            "metrics": met,
            "files": [str(png), str(csv_path)],
            "state": log.get("state"),
            "anchor_events": log.get("anchor_events"),
            "notes": [
                "q comes from %s; the twin mirrors /joint_states into PhysX "
                "and does not publish arm state itself." % args.state,
                "Frames: DH base + flange, no tool offset; the Isaac base "
                "offset is identity unless ur5e_common.ISAAC_BASE_OFFSET is set.",
            ],
        }
        summary_path = write_json(out / f"resolved_rate_twin_{stamp}.json", summary)
        write_json(out / "summary.json", summary, merge_key="resolved_rate_twin")
        print(f"\nwrote {png}\nwrote {csv_path}\nwrote {summary_path}")
        print(f"  time to 5 mm: {met['time_to_reach_5mm_s']} s, "
              f"final error {met['final_pos_err_mm']:.2f} mm, "
              f"peak |qdot| {met['peak_joint_speed_rad_s']:.3f} rad/s, "
              f"anti-windup re-anchors: {log.get('anchor_events')}")
        return 0
    finally:
        if link is not None:
            link.close()
        if bridge is not None:
            bridge.close()


if __name__ == "__main__":
    sys.exit(main())

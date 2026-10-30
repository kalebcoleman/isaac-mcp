#!/usr/bin/env python3
"""UR5e kinematics, frames and the ROS 2 link to the twin -- shared demo library.

WHY THIS FILE EXISTS
    Two of the demo controllers (`resolved_rate.py`, and whatever else needs the
    same arm model) must agree, to the last decimal, on three things: where the
    robot's joints are, where the end effector is, and how a joint velocity turns
    into a Cartesian velocity. That is this module: standard Denavit-Hartenberg
    forward kinematics, the analytic geometric Jacobian, a pose error with one
    documented sign convention, and `TwinLink`, the ROS 2 transport that carries
    joint commands into the Isaac Sim twin.

    Importing this module is pure numpy + stdlib. `rclpy` is imported lazily,
    inside `TwinLink.__init__` only, so plotting/offline code never needs the
    sim's bundled ROS 2 install.

================================================================================
FRAME CONVENTIONS (read this before comparing numbers with Isaac Sim)
================================================================================

Every transform is a 4x4 homogeneous matrix used with column vectors:

    p_a = T_a_b[:3, :3] @ p_b + T_a_b[:3, 3]        T_a_b maps b-coordinates -> a

`fk(q)` returns **T_base_flange**: Denavit-Hartenberg base frame -> UR "flange"
frame (the last DH frame). The flange frame's +z is the axis the tool bolts to
and points *out of* the flange.

    z0  points up at the base (joint 1 rotates about +z0)
    q=0 puts the arm along -x of this DH base frame (see the hand derivation in
    the selftest); that is the standard UR DH table's zero pose, not a bug.

Three real-world frames differ from this one, and getting them confused is the
classic source of "the sim and the maths disagree by a rotation":

1.  **UR controller `base` vs. ROS `base_link`.** Universal Robots' own pose
    readout uses a frame called `base`; the ROS description's root link is
    `base_link`, and in `ur_description` the fixed joint `base_link-base`
    carries `rpy = (0, 0, pi)` -- a 180 deg yaw. So

        T_base_link_base = Rz(pi)          (self-inverse, so the direction
                                            barely matters -- the yaw does)

    This is documented upstream (`ur_description/urdf/ur5e.urdf.xacro`) and is
    NOT something this repo verifies: treat it as a cited convention, and
    confirm it with `validate_against_isaac()` before publishing a number that
    depends on it. `to_ur_base_link()` / `UR_TO_BASE_LINK` apply it; nothing in
    the control path applies it implicitly.

2.  **DH flange vs. tool tip (`tool0` / TCP).** The DH chain ends at the
    flange. A gripper adds a constant offset (a translation along the flange
    +z, plus usually a rotation). The demo's "end effector" is the flange
    unless a caller passes `tool_offset = T_flange_tool`. The default is
    identity, deliberately: guessing this offset is how a 5 mm demo becomes a
    5 cm one. Measure it (CAD, or `validate_against_isaac` with IJ data).

3.  **DH base vs. the twin's world.** In the extension the UR5e is placed at
    world `(0, 0, ROBOT_BASE_Z)` with `rpy = (0, 0, +180 deg)`
    (`exts/ur5e-dt/ur5e_dt/extension.py:2927-2935`, `ROBOT_BASE_Z = 0.0`).
    That yaw is what makes the arm reach along world +x. `to_isaac_base()` is
    the hook for that constant: it takes an offset matrix and defaults to
    **identity**, because the exact DH-base <-> USD-`base_link` relation also
    depends on how the USD was authored. Do not guess it -- feed
    `validate_against_isaac()` a handful of (q, T_isaac) pairs from the running
    sim and let it solve for the constant. It returns the best-fit offset plus
    the residual spread, which is exactly the evidence you need.

================================================================================
SOURCES
================================================================================
* DH constants: Universal Robots' published table "DH parameters for
  calculations of kinematics and dynamics" (UR5e), values in metres/radians:
      i    a_i [m]     alpha_i [rad]   d_i [m]
      1    0           pi/2            0.1625
      2   -0.425       0               0
      3   -0.3922      0               0
      4    0           pi/2            0.1333
      5    0          -pi/2            0.0997
      6    0           0               0.0996
  Cross-check that needs no network: at full stretch the shoulder-to-wrist
  distance is |a2| + |a3| = 0.8172 m, and with the wrist offsets the UR5e's
  published maximum reach is 850 mm. The selftest also re-derives fk(0) by hand.
* Joint limits: UR5e working range +/-360 deg (all six joints) -> +/-2*pi rad.
* Joint speed: UR5e max 180 deg/s = pi rad/s. The demo set (1.0 rad/s) is
  deliberately slower: the twin's joints are soft position drives
  (stiffness 100 / damping 10 / max force 200 in the USD), and a demo that
  asks for the datasheet maximum just shows the drive lagging.

================================================================================
WHAT IS AND IS NOT VERIFIED HERE
================================================================================
Verified offline (selftest): DH constants, fk, analytic Jacobian vs. numerical
Jacobian over random configurations (max abs difference < 1e-6), pose-error
sign convention, known singularity at q=0, joint limits, the frame helpers, the
Isaac base-offset fitter, and that importing the module does not import rclpy.

NOT verified here (needs the sim): the DH base -> USD `base_link` offset, the
flange -> TCP offset, the real /joint_states rate, and whether the twin echoes
any arm state at all (it does not, as of this writing -- see TwinLink).
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

import numpy as np

# =========================================================================== #
# 1. Robot description (the only place these numbers are written down)
# =========================================================================== #

#: Denavit-Hartenberg link offsets, metres. See the module docstring for the
#: published table these come from. Standard DH: A_i = Rz(q_i) Tz(d_i) Tx(a_i) Rx(alpha_i).
DH_D: Tuple[float, ...] = (0.1625, 0.0, 0.0, 0.1333, 0.0997, 0.0996)
DH_A: Tuple[float, ...] = (0.0, -0.425, -0.3922, 0.0, 0.0, 0.0)
DH_ALPHA: Tuple[float, ...] = (math.pi / 2, 0.0, 0.0, math.pi / 2, -math.pi / 2, 0.0)

#: The six UR5e joint names, in DH/chain order == the order the twin's
#: `ArticulationController` and the UR driver's `joint_state_broadcaster` use.
JOINT_NAMES: Tuple[str, ...] = (
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
)
N_JOINTS: int = len(JOINT_NAMES)

#: Datasheet position limits: +/-360 deg on every joint.
Q_MIN = np.full(N_JOINTS, -2.0 * math.pi)
Q_MAX = np.full(N_JOINTS, +2.0 * math.pi)
#: Datasheet speed limits: 180 deg/s on every joint.
QD_MAX = np.full(N_JOINTS, math.pi)
#: Conservative limits for a live demo. Slow enough that the position drives in
#: the twin can actually follow, and slow enough to be safe next to a table.
QD_DEMO = np.full(N_JOINTS, 1.0)

#: A "home"/ready pose for the demos: elbow bent ~100 deg, tool pointing down,
#: about 0.58 m out and 0.35 m above the base plane, 0.23 rad from the nearest
#: singularity (sigma_min ~ 0.23). Chosen so the arm is clear of the tabletop
#: (table top == z = 0 in the twin) with the tool 0.35 m above it, and so the
#: Jacobian is well conditioned at t=0 (a singular start makes the first frame
#: of every plot look like a controller bug).
HOME_Q = np.array([0.0, -1.30, 1.60, -1.80, -math.pi / 2, 0.0])

#: Identity by default -- see "FRAME CONVENTIONS" above. Assign the result of
#: `validate_against_isaac()` here (or pass it per call) once you have sim data.
ISAAC_BASE_OFFSET = np.eye(4)

#: UR controller `base` -> ROS `base_link`, the documented 180 deg yaw.
#: Both directions are the same matrix because Rz(pi) is its own inverse.
UR_TO_BASE_LINK = np.diag([-1.0, -1.0, 1.0, 1.0])

#: Flange -> tool tip. Identity until measured: see convention 2 above.
FLANGE_TO_TOOL = np.eye(4)


# =========================================================================== #
# 2. Small rotation helpers
# =========================================================================== #
def hat(w: Sequence[float]) -> np.ndarray:
    """Skew-symmetric matrix such that hat(w) @ v == cross(w, v)."""
    wx, wy, wz = float(w[0]), float(w[1]), float(w[2])
    return np.array([[0.0, -wz, wy], [wz, 0.0, -wx], [-wy, wx, 0.0]])


def rot_exp(w: Sequence[float]) -> np.ndarray:
    """Rodrigues: rotation vector (axis * angle, radians) -> 3x3 rotation.

    This is the matrix exponential of hat(w). Used to *build* poses from a
    "rotate by this much about this axis" description.
    """
    w = np.asarray(w, dtype=float).reshape(3)
    theta = float(np.linalg.norm(w))
    if theta < 1e-12:
        # First-order expansion: exact to machine precision at this size.
        return np.eye(3) + hat(w)
    axis = w / theta
    K = hat(axis)
    return np.eye(3) + math.sin(theta) * K + (1.0 - math.cos(theta)) * (K @ K)


def rot_log(R: Sequence[Sequence[float]]) -> np.ndarray:
    """Inverse of `rot_exp`: 3x3 rotation -> rotation vector (axis * angle).

    The angle is in [0, pi]; at exactly pi the axis sign is ambiguous (either
    sign is a correct answer) and this picks the one consistent with R's skew
    part.
    """
    R = np.asarray(R, dtype=float).reshape(3, 3)
    # sin(theta) * axis and cos(theta) straight from the matrix. Using
    # atan2(sin, cos) instead of acos(cos) is what keeps the angle accurate
    # near pi, where acos loses roughly sqrt(machine-eps) ~ 1e-8 of the angle.
    skew = 0.5 * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    sin_theta = float(np.linalg.norm(skew))
    cos_theta = max(-1.0, min(1.0, (float(np.trace(R)) - 1.0) / 2.0))
    theta = math.atan2(sin_theta, cos_theta)
    if theta < 1e-9:
        # skew == sin(theta) * axis == w to first order: no division needed.
        return skew
    if math.pi - theta < 1e-6:
        # Near pi, sin(theta) -> 0 so the skew part is useless. R + I = 2 a a^T.
        A = (R + np.eye(3)) / 2.0
        k = int(np.argmax(np.diag(A)))
        axis = A[:, k] / math.sqrt(max(float(A[k, k]), 1e-12))
        norm = float(np.linalg.norm(axis))
        if norm < 1e-9:  # pathological; fall back to the skew direction
            axis = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
            norm = float(np.linalg.norm(axis)) or 1.0
        axis = axis / norm
        if float(np.dot(axis, skew)) < 0.0:
            axis = -axis
        return axis * theta
    return skew / sin_theta * theta


def rpy_to_matrix(rpy: Sequence[float]) -> np.ndarray:
    """UR/ROS convention: R = Rz(yaw) @ Ry(pitch) @ Rx(roll), radians."""
    roll, pitch, yaw = (float(v) for v in rpy)
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.array(
        [
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ]
    )


def make_transform(R: Optional[Sequence[Sequence[float]]] = None,
                   p: Optional[Sequence[float]] = None) -> np.ndarray:
    """Assemble a 4x4 homogeneous transform from a rotation and a translation."""
    T = np.eye(4)
    if R is not None:
        T[:3, :3] = np.asarray(R, dtype=float).reshape(3, 3)
    if p is not None:
        T[:3, 3] = np.asarray(p, dtype=float).reshape(3)
    return T


def transform_inverse(T: Sequence[Sequence[float]]) -> np.ndarray:
    """Inverse of a rigid 4x4 transform, without a general matrix inverse."""
    T = np.asarray(T, dtype=float).reshape(4, 4)
    R = T[:3, :3]
    out = np.eye(4)
    out[:3, :3] = R.T
    out[:3, 3] = -R.T @ T[:3, 3]
    return out


# =========================================================================== #
# 3. Forward kinematics
# =========================================================================== #
def dh_transform(i: int, q_i: float) -> np.ndarray:
    """One standard-DH link transform: Rz(q) Tz(d) Tx(a) Rx(alpha).

    `i` is 0-based. Written out rather than composed from four matrices so the
    cost stays tiny inside a 50 Hz control loop and so the structure is visible:
    the last two rows are constant, and column 3 (the link's origin) depends
    only on cos/sin(q).
    """
    ca, sa = math.cos(DH_ALPHA[i]), math.sin(DH_ALPHA[i])
    cq, sq = math.cos(q_i), math.sin(q_i)
    a, d = DH_A[i], DH_D[i]
    return np.array(
        [
            [cq, -sq * ca, sq * sa, a * cq],
            [sq, cq * ca, -cq * sa, a * sq],
            [0.0, sa, ca, d],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )


def _check_q(q: Sequence[float]) -> np.ndarray:
    q = np.asarray(q, dtype=float).reshape(-1)
    if q.shape != (N_JOINTS,):
        raise ValueError(f"expected {N_JOINTS} joint angles, got shape {q.shape}")
    if not np.all(np.isfinite(q)):
        raise ValueError(f"joint vector contains NaN/Inf: {q}")
    return q


def fk_all(q: Sequence[float]) -> list:
    """Return the 7 frames [T_base_0 ... T_base_flange] for joint vector q.

    T_base_0 is the DH base frame itself (identity); the last element is the
    flange. Each joint's rotation axis is the z column of the frame *before*
    it, which is exactly what `jacobian()` needs.
    """
    q = _check_q(q)
    frames = [np.eye(4)]
    T = np.eye(4)
    for i in range(N_JOINTS):
        T = T @ dh_transform(i, q[i])
        frames.append(T)
    return frames


def fk(q: Sequence[float]) -> np.ndarray:
    """Base -> flange transform (4x4) for joint angles q (radians)."""
    return fk_all(q)[-1]


def fk_position(q: Sequence[float]) -> np.ndarray:
    """Just the flange origin in the DH base frame (3,)."""
    return fk(q)[:3, 3].copy()


# =========================================================================== #
# 4. Jacobian
# =========================================================================== #
def jacobian(q: Sequence[float], tool_offset: Optional[np.ndarray] = None) -> np.ndarray:
    """Geometric Jacobian, 6x6, base frame, linear rows on top.

        [ v ]          [ Jv ]        d p_end / d q  (m per rad)
        [ w ]  = J(q)  [ Jw ]  qdot, d omega_axis / d q  (rad per rad)

    v is the velocity of the point at the end of the chain; w is the angular
    velocity of the end-effector frame. For revolute joint i about axis z_i
    (the third column of frame i, in base coordinates) through origin o_i:

        Jv_i = z_i x (p_end - o_i)        Jw_i = z_i

    `tool_offset` (4x4, flange -> tool) moves the point the linear part refers
    to; the tool is rigid w.r.t. the flange, so the angular part only loses the
    moment arm of the offset, which the formula above handles automatically.
    """
    q = _check_q(q)
    frames = fk_all(q)
    T_end = frames[-1]
    if tool_offset is not None:
        T_end = T_end @ np.asarray(tool_offset, dtype=float).reshape(4, 4)
    p_end = T_end[:3, 3]

    J = np.zeros((6, N_JOINTS))
    for i in range(N_JOINTS):
        z_i = frames[i][:3, 2]          # joint i rotates about this axis
        o_i = frames[i][:3, 3]          # ... through this point
        J[:3, i] = np.cross(z_i, p_end - o_i)
        J[3:, i] = z_i
    return J


def numeric_jacobian(q: Sequence[float], eps: float = 1e-6,
                     tool_offset: Optional[np.ndarray] = None) -> np.ndarray:
    """Finite-difference Jacobian -- only for tests and for teaching.

    Central difference (q +/- eps/2), so the truncation error is O(eps^2)
    instead of the O(eps) a one-sided difference gives: at eps = 1e-6 that is
    the difference between agreeing to 5e-7 and agreeing to 1e-11.

    The angular rows use the fact that rotating by dq about joint i changes the
    orientation as R(q+dq) = Exp(w_i dq) R(q) *in the base frame*, so

        w_i = rot_log(R(q+eps/2) R(q-eps/2)^T) / eps

    which is the same convention `pose_error()` reports.
    """
    q = _check_q(q)
    half = 0.5 * float(eps)
    J = np.zeros((6, N_JOINTS))
    for i in range(N_JOINTS):
        dq = np.zeros(N_JOINTS)
        dq[i] = half
        T_plus = fk(q + dq)
        T_minus = fk(q - dq)
        if tool_offset is not None:
            tool = np.asarray(tool_offset, dtype=float).reshape(4, 4)
            T_plus = T_plus @ tool
            T_minus = T_minus @ tool
        J[:3, i] = (T_plus[:3, 3] - T_minus[:3, 3]) / eps
        J[3:, i] = rot_log(T_plus[:3, :3] @ T_minus[:3, :3].T) / eps
    return J


# =========================================================================== #
# 5. Pose error, conditioning
# =========================================================================== #
def pose_error(T_cur: Sequence[Sequence[float]],
               T_des: Sequence[Sequence[float]]) -> np.ndarray:
    """6-vector [position error; orientation error] taking cur -> des.

    CONVENTION (this is the one that matters for control):

        e_pos = p_des - p_cur                      (metres, base frame)
        e_rot = rot_log(R_des @ R_cur^T)           (radians, base frame)

    e_rot is a rotation *vector in the base frame*: apply Exp(e_rot) on the
    left of R_cur and you get R_des. Its direction is the axis you would turn
    about to fix the orientation; its length is the angle.

    Why this convention: the geometric Jacobian's angular rows are base-frame
    angular velocities, so v = K e is the correct proportional law with no
    frame change anywhere. The alternative -- error in the end-effector frame --
    would need a per-step R multiplication and is a classic sign bug.

    Note e_rot is *not* the derivative of any attitude parameterisation, but
    for small errors it is proportional to the attitude error used by the
    Jacobian (angle error, not quaternion error), which is what makes the loop
    converge linearly instead of oscillating near pi.
    """
    T_cur = np.asarray(T_cur, dtype=float).reshape(4, 4)
    T_des = np.asarray(T_des, dtype=float).reshape(4, 4)
    e = np.zeros(6)
    e[:3] = T_des[:3, 3] - T_cur[:3, 3]
    e[3:] = rot_log(T_des[:3, :3] @ T_cur[:3, :3].T)
    return e


def singular_values(J: Sequence[Sequence[float]]) -> np.ndarray:
    """Six singular values of J, descending.

    Each sigma answers "how fast can the end effector move in *this* Cartesian
    direction, per rad/s of joint speed". sigma_min -> 0 means some Cartesian
    direction has become unreachable: a singularity.
    """
    return np.linalg.svd(np.asarray(J, dtype=float), compute_uv=False)


def sigma_min(J: Sequence[Sequence[float]]) -> float:
    """Smallest singular value -- the singularity detector."""
    return float(singular_values(J)[-1])


def manipulability(J: Sequence[Sequence[float]]) -> float:
    """Yoshikawa's manipulability, sqrt(det(J J^T)) = product of the sigmas.

    Zero at a singularity, larger when the arm is nimble in all directions.
    Handy as a scalar for plots, but for *damping* prefer sigma_min: the
    product is dominated by the largest sigma and hides a collapsing one.
    """
    J = np.asarray(J, dtype=float)
    det = float(np.linalg.det(J @ J.T))
    return math.sqrt(max(det, 0.0))


# =========================================================================== #
# 6. Joint limits
# =========================================================================== #
def within_limits(q: Sequence[float], margin: float = 0.0,
                  q_min: Sequence[float] = Q_MIN,
                  q_max: Sequence[float] = Q_MAX) -> bool:
    """True if every joint is inside [q_min + margin, q_max - margin]."""
    q = _check_q(q)
    lo = np.asarray(q_min, dtype=float) + margin
    hi = np.asarray(q_max, dtype=float) - margin
    return bool(np.all(q >= lo) and np.all(q <= hi))


def limit_margins(q: Sequence[float],
                  q_min: Sequence[float] = Q_MIN,
                  q_max: Sequence[float] = Q_MAX) -> np.ndarray:
    """Per-joint distance to the nearer position limit (radians, >= 0 inside)."""
    q = _check_q(q)
    return np.minimum(q - np.asarray(q_min, dtype=float),
                      np.asarray(q_max, dtype=float) - q)


# =========================================================================== #
# 7. Isaac Sim base offset: measure it, do not guess it
# =========================================================================== #
def to_isaac_base(T: Sequence[Sequence[float]],
                  base_offset: Optional[Sequence[Sequence[float]]] = None) -> np.ndarray:
    """Map a DH-base transform into the twin's world: `base_offset @ T`.

    `base_offset` is **T_world_dh_base** -- the pose of the DH base frame in
    Isaac Sim's world. It defaults to `ISAAC_BASE_OFFSET`, which is identity
    until you replace it with a measured value. Identity is the honest default:
    it means "the caller has not told me how the sim places the robot", and
    `validate_against_isaac()` is how you fill it in.

    The extension authors the robot at world (0, 0, 0) with a +180 deg yaw
    (extension.py:2927-2935), so a *likely* value is `Rz(pi)`, but "likely" is
    not "measured" -- the USD's internal `base_link` orientation and the xacro
    that produced it both feed into this, so verify it.
    """
    offset = ISAAC_BASE_OFFSET if base_offset is None else base_offset
    return np.asarray(offset, dtype=float).reshape(4, 4) @ np.asarray(
        T, dtype=float).reshape(4, 4)


def to_ur_base_link(T: Sequence[Sequence[float]]) -> np.ndarray:
    """DH base -> UR controller `base` frame: flip x and y (a 180 deg yaw).

    Only needed when comparing against UR's own pose readout. The transform is
    its own inverse (`UR_TO_BASE_LINK`).
    """
    return UR_TO_BASE_LINK @ np.asarray(T, dtype=float).reshape(4, 4)


def validate_against_isaac(pairs: Iterable[Tuple[Sequence[float],
                                                 Sequence[Sequence[float]]]],
                           tool_offset: Optional[Sequence[Sequence[float]]] = None,
                           min_pairs: int = 3) -> dict:
    """Fit the constant transform between our DH base frame and Isaac Sim's world.

    Input: an iterable of (q, T_isaac) pairs -- `q` the six joint angles you
    commanded, `T_isaac` the 4x4 world pose of the SAME body that `fk(q)`
    returns (the flange, or the tool if you pass `tool_offset`). Read them from
    the sim with, e.g., an articulation link transform, and hand them here.

    Model: T_isaac = offset @ fk(q, tool_offset), with `offset` constant. Each
    pair then yields a candidate offset A_i = T_isaac_i @ fk(q_i)^-1, and the
    best fit is the chordal mean rotation (SVD projection back onto SO(3)) and
    the mean translation. The *spread* of the candidates is the useful part:

      * small spread  -> the model holds, the constant is real, use it;
      * large spread  -> something else is going on (wrong body, a per-query
        frame change, a joint index order mismatch, or a non-constant offset).
        Do not paper over that with a fit.

    Returns a JSON-serializable dict with:
        n, base_offset (4x4 as nested lists -- `np.array(...)` to use it),
        residual_max_m, residual_max_rad (worst pair after fitting),
        spread_m, spread_rad (worst deviation of a candidate from the fitted
        constant -- the model-mismatch signal), and pairs_used (per-pair
        dictionaries, so the report can show its work).

    No sim call happens here: this is a pure function so it can be tested
    offline with synthetic data (the selftest does exactly that).
    """
    tool = None if tool_offset is None else np.asarray(tool_offset, dtype=float).reshape(4, 4)
    candidates = []
    entries = []
    for q, T_isaac in pairs:
        T_dh = fk(q)
        if tool is not None:
            T_dh = T_dh @ tool
        A = np.asarray(T_isaac, dtype=float).reshape(4, 4) @ transform_inverse(T_dh)
        candidates.append(A)
        entries.append({"q": [float(v) for v in np.asarray(q, dtype=float).reshape(-1)],
                        "candidate": A.copy()})
    n = len(candidates)
    if n < min_pairs:
        raise ValueError(f"need at least {min_pairs} (q, T_isaac) pairs for a "
                         f"constant-offset fit, got {n}")

    # Chordal mean of the rotations, projected back onto SO(3).
    R_mean = np.mean([A[:3, :3] for A in candidates], axis=0)
    U, _, Vt = np.linalg.svd(R_mean)
    D = np.diag([1.0, 1.0, float(np.linalg.det(U @ Vt))])
    R_fit = U @ D @ Vt
    t_fit = np.mean([A[:3, 3] for A in candidates], axis=0)
    offset = make_transform(R_fit, t_fit)

    # Residual of the fitted constant against the actual measurements, and the
    # spread of the individual candidates around it.
    res_m, res_rad, spread_m, spread_rad = [], [], [], []
    for entry in entries:
        T_dh = fk(entry["q"])
        if tool is not None:
            T_dh = T_dh @ tool
        pred = offset @ T_dh
        actual = np.asarray(entry["candidate"], dtype=float) @ T_dh
        res_m.append(float(np.linalg.norm(pred[:3, 3] - actual[:3, 3])))
        res_rad.append(float(np.linalg.norm(rot_log(pred[:3, :3] @ actual[:3, :3].T))))
        A = entry["candidate"]
        spread_m.append(float(np.linalg.norm(A[:3, 3] - t_fit)))
        spread_rad.append(float(np.linalg.norm(rot_log(A[:3, :3] @ R_fit.T))))
    return {
        "n": n,
        "base_offset": offset.tolist(),
        "residual_max_m": max(res_m),
        "residual_max_rad": max(res_rad),
        "spread_m": max(spread_m),
        "spread_rad": max(spread_rad),
        "pairs_used": [
            {k: (v.tolist() if isinstance(v, np.ndarray) else v)
             for k, v in entry.items()}
            for entry in entries
        ],
    }


# =========================================================================== #
# 8. TwinLink -- the ROS 2 transport into the twin
# =========================================================================== #
#  HOW THE TWIN IS DRIVEN (facts from the extension, checked 2026-10-30)
#
#  Command path (extension.py:3042-3074, setup_action_graph):
#      sensor_msgs/JointState on  /joint_states
#        -> isaacsim.ros2.bridge.ROS2SubscribeJointState
#        -> isaacsim.core.nodes.IsaacArticulationController
#        -> PhysX angular drive on /World/UR5e/joints/<joint>
#    The topic name is literally "/joint_states", namespace empty. The graph
#    wires positionCommand, velocityCommand and effortCommand, so whichever
#    arrays the message fills are forwarded; this twin is a POSITION-mirroring
#    twin, i.e. position[] is the field that matters.
#
#  State path (the honest part):
#      The extension publishes exactly ONE JointState, and it is for the
#      GRIPPER: /rg2_sim/joint_state, sensor_msgs/JointState, a plain
#      ROS2Publisher whose name[]/position[] a physics callback fills with
#      rg2_gripper_joint (extension.py:3085-3171). THERE IS NO PUBLISHER FOR
#      THE ARM JOINTS. So on a bare twin there is nothing to subscribe to for
#      measured q.
#
#  Consequences, all of which TwinLink makes explicit instead of hiding:
#      * `read()` returns nothing (None, None, None) if no non-self message
#        arrives. It never invents a state.
#      * Messages this node publishes are tagged in header.frame_id and
#        ignored by our own subscription, so a mis-configured
#        command_topic == state_topic cannot turn into a feedback loop that
#        reads our own commands back as "measurements".
#      * `state_source` says what is actually feeding q: the UR driver's
#        joint_state_broadcaster (real/fake hardware, the intended live path),
#        or nothing. Closing a control loop on open-loop commands is a
#        decision for the caller, not something a transport should do quietly.
#
#  DDS: same island as the sim. scripts/tf_capture.py is the reference for
#  this, and the logic here is deliberately a copy of it -- bundled Humble out
#  of the venv's isaacsim install, config/ros_dds.env (Cyclone + localhost-only
#  + domain), and a re-exec once LD_LIBRARY_PATH has the bundled libs.

ROOT = Path(__file__).resolve().parents[2]
ROS_CORE = Path("exts") / "isaacsim.ros2.core" / "humble"
DDS_ENV_FILES = (
    ROOT / "config" / "ros_dds.env",
    ROOT / "external" / "isaac-sim-mcp" / "config" / "ros_dds.env",
)
REEXEC_MARK = "UR5E_DT_UR5E_COMMON_REEXEC"

_ASSIGN = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_BRACED = r"\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}"
_VAR = re.compile(rf"\$(?:{_BRACED}|([A-Za-z_][A-Za-z0-9_]*))")


def ros_dirs() -> Tuple[Path, Path]:
    """Locate `<venv>/.../isaacsim/exts/isaacsim.ros2.core/humble/{rclpy,lib}`.

    Resolved from the interpreter's own isaacsim package (without importing
    `isaacsim`, which would boot Kit), so this keeps working if the venv moves.
    """
    try:
        spec = importlib.util.find_spec("isaacsim")
    except (ImportError, ValueError) as exc:
        raise RuntimeError(
            f"cannot find isaacsim in this interpreter ({exc}); run with the "
            f"sim's venv python, e.g. .venv/bin/python, or "
            f"~/Desktop/ur5e-digital-twin/learning6/.venv/bin/python") from exc
    locations = list(getattr(spec, "submodule_search_locations", None) or ())
    if not locations:
        raise RuntimeError("isaacsim is not an installed package in this "
                           "interpreter; run with the sim's venv python")
    base = Path(locations[0]) / ROS_CORE
    rclpy_dir, lib_dir = base / "rclpy", base / "lib"
    if not rclpy_dir.is_dir() or not lib_dir.is_dir():
        raise RuntimeError(f"bundled ROS 2 install not found under {base}")
    return rclpy_dir, lib_dir


def _expand(raw: str, values: dict) -> str:
    def replace(match):
        name = match.group(1) or match.group(3)
        default = match.group(2) or ""
        return os.environ.get(name, values.get(name, default))

    return _VAR.sub(replace, raw)


def _parse_env_file(path: Path) -> dict:
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
    """Adopt the sim's DDS island (Cyclone, localhost-only, domain) if unset.

    Domain, localhost_only and the Cyclone profile must match the publisher or
    discovery simply never happens -- a one-way setup where publishes flow but
    subscriptions do not is the classic failure (config/ros_dds.env explains it
    in detail).
    """
    values = {}
    for path in DDS_ENV_FILES:
        if path.is_file():
            values = _parse_env_file(path)
            break
    values.setdefault("RMW_IMPLEMENTATION", "rmw_cyclonedds_cpp")
    values.setdefault("ROS_DISTRO", "humble")
    applied = {}
    for name, value in values.items():
        if name.startswith("_"):
            continue
        if name not in os.environ:
            os.environ[name] = value
            applied[name] = value
    return applied


def ensure_native_libs(lib_dir: Path, allow_reexec: bool = True) -> bool:
    """Put the bundled ROS 2 libs on LD_LIBRARY_PATH, re-execing if needed.

    The dynamic loader reads LD_LIBRARY_PATH only at process start, so this
    cannot be fixed from inside a live interpreter: the process replaces itself
    with the same argv once, and the second run finds the variable already set.
    Returns False if nothing had to be done; on the re-exec path it does not
    return at all.

    CAVEAT: the re-exec re-runs the same argv, so the caller must be a real
    script file. `python -c ...` or a heredoc on stdin loses its input on the
    second run (stdin is already consumed) and appears to do nothing.
    """
    entries = [p for p in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep) if p]
    if str(lib_dir) in entries or os.environ.get(REEXEC_MARK) == "1":
        return False
    if not allow_reexec:
        raise RuntimeError(
            f"LD_LIBRARY_PATH does not contain {lib_dir} and re-exec is disabled; "
            f"export it and re-run, or allow the re-exec")
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = os.pathsep.join([str(lib_dir), *entries])
    env[REEXEC_MARK] = "1"
    argv = list(getattr(sys, "orig_argv", None)
                or [sys.executable, str(Path(__file__).resolve())])
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(sys.executable, argv, env)
    return True  # unreachable


class TwinLink:
    """Joint command/state transport to the twin over ROS 2 (the real path).

    Command: publishes sensor_msgs/JointState on `command_topic` (default
    "/joint_states") with `name`, `position` and optionally `velocity`. In the
    twin that message is consumed by ROS2SubscribeJointState and becomes the
    articulation's position command.

    State: subscribes to `state_topic` (default: the command topic) and ignores
    messages this node published (header.frame_id == SELF_TAG). With a UR
    driver's joint_state_broadcaster running that is the measured q; on a bare
    twin nothing arrives and `read()` says so.

    Usage:
        link = TwinLink()
        link.send(q_cmd)                      # one position command
        stamp, q, qd = link.read(timeout=1.0) # measured state or (None,)*3
        link.close()

    Every ROS import happens in __init__, so this module stays numpy-only for
    the offline paths.
    """

    #: Stamped into header.frame_id so our own subscription can drop self-echo.
    SELF_TAG = "ur5e_common_twinlink"

    def __init__(self, node_name: str = "ur5e_demo_twinlink",
                 command_topic: str = "/joint_states",
                 state_topic: Optional[str] = None,
                 joint_names: Sequence[str] = JOINT_NAMES,
                 qos_depth: int = 10,
                 reexec: bool = True,
                 verbose: bool = True):
        self.node_name = node_name
        self.command_topic = command_topic
        self.state_topic = state_topic or command_topic
        self.joint_names = tuple(joint_names)
        self.verbose = verbose
        self._closed = False
        self._qos_depth = int(qos_depth)
        self._reexec_allowed = bool(reexec)

        # --- ROS runtime, exactly the tf_capture.py recipe ------------------
        rclpy_dir, lib_dir = ros_dirs()
        self.dds_env = apply_dds_env()
        ensure_native_libs(lib_dir, allow_reexec=self._reexec_allowed)
        if str(rclpy_dir) not in sys.path:
            sys.path.insert(0, str(rclpy_dir))

        import rclpy                                    # noqa: PLC0415 (lazy by design)
        from rclpy.node import Node
        from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                               ReliabilityPolicy)
        from sensor_msgs.msg import JointState

        self.rclpy = rclpy
        self._JointState = JointState

        # The graph's subscriber uses the ROS 2 bridge default (keep_last 10,
        # reliable, volatile); matching it here means no QoS negotiation
        # surprises and no dropped commands.
        self._qos = QoSProfile(
            depth=self._qos_depth,
            history=HistoryPolicy.KEEP_LAST,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )

        if not rclpy.ok():
            rclpy.init(args=None)
        self.node = Node(self.node_name)
        self._pub = self.node.create_publisher(JointState, self.command_topic, self._qos)
        self._sub = self.node.create_subscription(
            JointState, self.state_topic, self._on_state, self._qos)

        # Latest measured state (None until a message arrives).
        self._stamp: Optional[float] = None
        self._q: Optional[np.ndarray] = None
        self._qd: Optional[np.ndarray] = None
        self._msg_count = 0
        self._self_count = 0
        self._decoded_count = 0

        if self.verbose:
            print(f"[TwinLink] command -> {self.command_topic} (sensor_msgs/JointState, "
                  f"names+position)", flush=True)
            print(f"[TwinLink] state   <- {self.state_topic} "
                  f"(ignoring messages tagged {self.SELF_TAG!r})", flush=True)
            print(f"[TwinLink] ROS_DOMAIN_ID={os.environ.get('ROS_DOMAIN_ID')} "
                  f"RMW={os.environ.get('RMW_IMPLEMENTATION')} "
                  f"LOCALHOST_ONLY={os.environ.get('ROS_LOCALHOST_ONLY')}", flush=True)

    # -- state subscription ------------------------------------------------
    def _on_state(self, msg) -> None:
        """Decode one JointState into q (and qd, if the publisher fills it)."""
        self._msg_count += 1
        frame = (msg.header.frame_id or "").strip()
        if frame == self.SELF_TAG:
            # Our own command coming back: not a measurement.
            self._self_count += 1
            return
        if not msg.name:
            return
        pos = {}
        vel = {}
        for i, name in enumerate(msg.name):
            if i < len(msg.position):
                pos[name] = float(msg.position[i])
            if i < len(msg.velocity):
                vel[name] = float(msg.velocity[i])
        try:
            q = np.array([pos[n] for n in self.joint_names], dtype=float)
        except KeyError:
            return  # not an arm message (e.g. the gripper's single joint)
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self._stamp = float(stamp) if stamp > 0 else time.time()
        self._q = q
        if len(vel) == len(self.joint_names):
            self._qd = np.array([vel[n] for n in self.joint_names], dtype=float)
        else:
            self._qd = None
        self._decoded_count += 1

    # -- public API --------------------------------------------------------
    def send(self, q_cmd: Sequence[float], qd_cmd: Optional[Sequence[float]] = None):
        """Publish one position (and optional velocity) command. Returns the msg."""
        q = np.asarray(q_cmd, dtype=float).reshape(-1)
        if q.shape != (len(self.joint_names),):
            raise ValueError(f"q_cmd must have {len(self.joint_names)} entries, got {q.shape}")
        if not np.all(np.isfinite(q)):
            raise ValueError(f"refusing to publish a non-finite command: {q}")
        msg = self._JointState()
        msg.header.stamp = self.node.get_clock().now().to_msg()
        msg.header.frame_id = self.SELF_TAG
        msg.name = list(self.joint_names)
        msg.position = [float(v) for v in q]
        if qd_cmd is not None:
            qd = np.asarray(qd_cmd, dtype=float).reshape(-1)
            if qd.shape != q.shape:
                raise ValueError(f"qd_cmd must have {q.shape} entries, got {qd.shape}")
            if not np.all(np.isfinite(qd)):
                raise ValueError(f"refusing to publish a non-finite velocity: {qd}")
            msg.velocity = [float(v) for v in qd]
        self._pub.publish(msg)
        return msg

    def poll(self, timeout: float = 0.0) -> None:
        """Spin the node for up to `timeout` seconds (0 = one non-blocking spin)."""
        rclpy = self.rclpy
        if timeout <= 0.0:
            rclpy.spin_once(self.node, timeout_sec=0.0)
            return
        deadline = time.monotonic() + float(timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return
            rclpy.spin_once(self.node, timeout_sec=min(0.05, remaining))

    def read(self, timeout: float = 0.2) -> Tuple[Optional[float], Optional[np.ndarray],
                                                  Optional[np.ndarray]]:
        """Return the freshest (stamp, q, qd), or (None, None, None) on timeout.

        `stamp` is header.stamp in seconds (wall clock when the publisher
        leaves it at 0), `q` is (6,) in this module's joint order, `qd` is (6,)
        or None when the publisher does not fill velocity[] -- velocity absent
        is a fact, not something to fake with zeros.

        A message that was already buffered before `timeout` elapses is
        returned immediately; call twice in a row to see fresh data.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self._stamp is None or self._decoded_count == 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return None, None, None
            self.poll(min(0.05, remaining))
            if self._decoded_count > 0:
                break
        stamp = self._stamp
        q = None if self._q is None else self._q.copy()
        qd = None if self._qd is None else self._qd.copy()
        # Clear so the next read() waits for a *new* message; last value stays
        # available as `last_state`.
        self._decoded_count = 0
        return stamp, q, qd

    @property
    def last_state(self):
        """(stamp, q, qd) of the most recent decoded message, no waiting."""
        q = None if self._q is None else self._q.copy()
        qd = None if self._qd is None else self._qd.copy()
        return self._stamp, q, qd

    def wait_for_state(self, timeout: float = 10.0) -> bool:
        """Block until the first measured state arrives; True if it did."""
        stamp, q, _ = self.read(timeout=timeout)
        return stamp is not None and q is not None

    def publisher_count(self) -> int:
        """Number of nodes publishing on the command topic, including us."""
        return int(self.node.count_publishers(self.command_topic))

    def subscriber_count(self) -> int:
        """Number of nodes subscribed to the command topic (the twin's graph)."""
        return int(self.node.count_subscribers(self.command_topic))

    def state_description(self) -> dict:
        """What is actually feeding q -- log this instead of assuming."""
        return {
            "command_topic": self.command_topic,
            "state_topic": self.state_topic,
            "command_publishers": self.publisher_count(),
            "command_subscribers": self.subscriber_count(),
            "state_messages": self._msg_count,
            "state_self_echo": self._self_count,
            "state_decoded": self._q is not None,
            "state_has_velocity": self._qd is not None,
        }

    def close(self) -> None:
        """Destroy the node and shut the context down (idempotent)."""
        if self._closed:
            return
        self._closed = True
        try:
            self.node.destroy_node()
        finally:
            try:
                self.rclpy.try_shutdown()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# =========================================================================== #
# 9. Reference poses used by the demos
# =========================================================================== #
def home_transform(tool_offset: Optional[np.ndarray] = None) -> np.ndarray:
    """The demo home pose: flange (or tool) transform at HOME_Q."""
    T = fk(HOME_Q)
    if tool_offset is not None:
        T = T @ np.asarray(tool_offset, dtype=float).reshape(4, 4)
    return T


def describe_pose(T: Sequence[Sequence[float]]) -> dict:
    """Human-readable pose summary: position, rotation vector, rpy, z axis."""
    T = np.asarray(T, dtype=float).reshape(4, 4)
    w = rot_log(T[:3, :3])
    return {
        "position": T[:3, 3].tolist(),
        "rotvec": w.tolist(),
        "rot_angle_deg": math.degrees(float(np.linalg.norm(w))),
        "rpy": [
            math.atan2(T[2, 1], T[2, 2]),
            math.asin(max(-1.0, min(1.0, -T[2, 0]))),
            math.atan2(T[1, 0], T[0, 0]),
        ],
        "tool_z_axis": T[:3, 2].tolist(),
    }


# =========================================================================== #
# 10. Selftest -- everything that does not need the sim
# =========================================================================== #
def selftest(verbose: bool = True) -> bool:
    """Assert the maths. No sim, no ROS, no GPU. Returns True on success."""
    ok = True

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and bool(condition)
        mark = "PASS" if condition else "FAIL"
        print(f"  [{mark}] {name}{(' -- ' + detail) if detail else ''}")

    print("ur5e_common selftest")
    print("-" * 72)

    # 1. Constants are the published ones (a typo here invalidates everything).
    print("1. DH table and limits")
    check("d1", DH_D[0] == 0.1625, f"{DH_D[0]}")
    check("a2", DH_A[1] == -0.425, f"{DH_A[1]}")
    check("a3", DH_A[2] == -0.3922, f"{DH_A[2]}")
    check("d4", DH_D[3] == 0.1333, f"{DH_D[3]}")
    check("d5", DH_D[4] == 0.0997, f"{DH_D[4]}")
    check("d6", DH_D[5] == 0.0996, f"{DH_D[5]}")
    check("alpha", [round(a, 12) for a in DH_ALPHA]
          == [round(a, 12) for a in (math.pi / 2, 0.0, 0.0, math.pi / 2, -math.pi / 2, 0.0)])
    check("reach = |a2|+|a3| = 0.8172 m", abs(abs(DH_A[1]) + abs(DH_A[2]) - 0.8172) < 1e-12)
    check("6 joint names", len(JOINT_NAMES) == 6 and JOINT_NAMES[0] == "shoulder_pan_joint")
    check("q limits +/-2pi", np.allclose(Q_MAX, 2 * math.pi) and np.allclose(Q_MIN, -2 * math.pi))
    check("qd datasheet pi rad/s", np.allclose(QD_MAX, math.pi))
    check("demo qd below datasheet", np.all(QD_DEMO < QD_MAX))

    # 2. fk(0), derived by hand in this test (the reason it is trustworthy).
    #
    #    A1 = Rz(0) Tz(.1625) Rx(90)  -> origin (0,0,.1625), z1 = -y
    #    A2 = Tz(0) Tx(-.425)         -> origin (-.425, 0, .1625)
    #    A3 = Tx(-.3922)              -> origin (-.8172, 0, .1625)
    #    A4 = Tz(.1333) Rx(90)        -> origin (-.8172, -.1333, .1625)
    #    A5 = Tz(.0997) Rx(-90)       -> origin (-.8172, -.1333, .0628)
    #    A6 = Tz(.0996)               -> origin (-.8172, -.2329, .0628)
    #    and the accumulated rotation is Rx(90).
    print("2. fk(0) against the hand-derived closed form")
    T0 = fk(np.zeros(6))
    p_expected = np.array([-(abs(DH_A[1]) + abs(DH_A[2])),
                           -(DH_D[3] + DH_D[5]),
                           DH_D[0] - DH_D[4]])
    Rx90 = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])
    check("position", np.allclose(T0[:3, 3], p_expected, atol=1e-12),
          f"{np.round(T0[:3, 3], 6).tolist()}")
    check("orientation = Rx(90)", np.allclose(T0[:3, :3], Rx90, atol=1e-12))
    check("homogeneous last row", np.allclose(T0[3], [0, 0, 0, 1]))
    check("fk is not the identity", not np.allclose(T0, np.eye(4)))

    # 3. Jacobian: analytic vs finite difference at random configurations.
    print("3. analytic Jacobian vs finite differences (200 random q)")
    rng = np.random.default_rng(20261030)
    worst = 0.0
    worst_rot = 0.0
    for _ in range(200):
        q = rng.uniform(-math.pi, math.pi, N_JOINTS)
        Ja = jacobian(q)
        Jn = numeric_jacobian(q, eps=1e-6)
        worst = max(worst, float(np.max(np.abs(Ja - Jn))))
        # Rotational rows should agree to the same order, scaled by the rate.
        worst_rot = max(worst_rot, float(np.max(np.abs(Ja[3:] - Jn[3:]))))
    check("max |J_analytic - J_numeric| < 1e-6", worst < 1e-6, f"{worst:.3e}")
    check("angular rows also < 1e-6", worst_rot < 1e-6, f"{worst_rot:.3e}")

    # ...and with a tool offset, which must move only the linear rows.
    tool = make_transform(rot_exp([0.3, -0.2, 0.1]), [0.0, 0.0, 0.12])
    q_probe = np.array([0.3, -1.2, 1.4, -1.9, -1.2, 0.4])
    Ja_tool = jacobian(q_probe, tool_offset=tool)
    Jn_tool = numeric_jacobian(q_probe, eps=1e-6, tool_offset=tool)
    check("tool offset: analytic == numeric", np.max(np.abs(Ja_tool - Jn_tool)) < 1e-6,
          f"{np.max(np.abs(Ja_tool - Jn_tool)):.3e}")
    check("tool offset leaves angular rows unchanged",
          np.allclose(Ja_tool[3:], jacobian(q_probe)[3:], atol=1e-12))

    # 4. pose_error sign convention: Exp(e_rot) @ R_cur == R_des.
    print("4. pose_error convention")
    zero_err = pose_error(fk(np.zeros(6)), fk(np.zeros(6)))
    check("identical poses -> zero error", np.allclose(zero_err, 0.0, atol=1e-12))
    worst_conv, worst_pi = 0.0, 0.0
    for _ in range(200):
        Ta = fk(rng.uniform(-math.pi, math.pi, N_JOINTS))
        Tb = fk(rng.uniform(-math.pi, math.pi, N_JOINTS))
        e = pose_error(Ta, Tb)
        worst_conv = max(worst_conv, float(np.max(np.abs(
            rot_exp(e[3:]) @ Ta[:3, :3] - Tb[:3, :3]))))
        worst_conv = max(worst_conv, float(np.linalg.norm(
            Ta[:3, 3] + e[:3] - Tb[:3, 3])))
    check("Exp(e_rot) @ R_cur == R_des", worst_conv < 1e-9, f"{worst_conv:.2e}")
    # Angle-pi rotations, where a naive log loses the axis.
    for axis in ([1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0],
                 [1 / math.sqrt(3), 1 / math.sqrt(3), 1 / math.sqrt(3)]):
        R = rot_exp(np.array(axis) * math.pi)
        e = rot_log(R)
        worst_pi = max(worst_pi, float(np.max(np.abs(rot_exp(e) - R))))
    check("rot_log/rot_exp round-trip at angle pi", worst_pi < 1e-9, f"{worst_pi:.2e}")
    e_small = rot_log(rot_exp([1e-9, -2e-9, 3e-9]))
    check("rot_log is linear at ~0", np.allclose(e_small, [1e-9, -2e-9, 3e-9], atol=1e-15))

    # 5. Known singularities. q=0 is the fully-stretched arm: elbow singular.
    print("5. conditioning helpers")
    check("sigma_min at q=0 is 0 (stretched arm)", sigma_min(jacobian(np.zeros(6))) < 1e-9,
          f"{sigma_min(jacobian(np.zeros(6))):.2e}")
    check("manipulability at q=0 is 0", manipulability(jacobian(np.zeros(6))) < 1e-9)
    smin_home = sigma_min(jacobian(HOME_Q))
    check("home pose is well conditioned (sigma_min > 0.1)", smin_home > 0.1,
          f"{smin_home:.3f}")
    check("6 singular values, descending",
          np.all(np.diff(singular_values(jacobian(HOME_Q))) <= 0))
    # A random well-conditioned pose: sigma_min <= sigma_max and > 0.
    J_rand = jacobian(q_probe)
    sv = singular_values(J_rand)
    check("0 < sigma_min <= sigma_max", 0.0 < sv[-1] <= sv[0], f"{sv[-1]:.3f}..{sv[0]:.3f}")

    # 6. Limits.
    print("6. joint limits")
    check("home inside limits", within_limits(HOME_Q))
    check("2pi+0.1 outside limits", not within_limits(np.full(6, 2 * math.pi + 0.1)))
    check("margins positive at home", np.all(limit_margins(HOME_Q) > 0))
    check("margin at +limit is 0",
          abs(float(limit_margins(np.full(6, 2 * math.pi))[0])) < 1e-12)
    bad = False
    try:
        fk([0.0, 0.0])
    except ValueError:
        bad = True
    check("wrong-length q raises", bad)

    # 7. Frame helpers.
    print("7. frame helpers")
    T_id = np.eye(4)
    check("to_isaac_base defaults to identity",
          np.allclose(to_isaac_base(T_id), T_id))
    Rz180 = np.diag([-1.0, -1.0, 1.0])
    check("to_isaac_base applies the offset on the left",
          np.allclose(to_isaac_base(T0, make_transform(Rz180, [1, 2, 3]))[:3, 3],
                      [1, 2, 3] + Rz180 @ T0[:3, 3]))
    T_bl = to_ur_base_link(T0)
    check("base_link yaw flips x and y",
          np.allclose(T_bl[:3, 3], Rz180 @ T0[:3, 3])
          and np.allclose(T_bl[:3, :3], Rz180 @ T0[:3, :3]))
    check("UR_TO_BASE_LINK is its own inverse",
          np.allclose(UR_TO_BASE_LINK @ UR_TO_BASE_LINK, np.eye(4)))

    # 8. validate_against_isaac recovers an injected constant offset.
    print("8. validate_against_isaac")
    true_offset = make_transform(rpy_to_matrix([0.1, -0.05, math.pi]), [0.3, -0.2, 0.75])
    pairs = []
    for _ in range(8):
        q = rng.uniform(-2.0, 2.0, N_JOINTS)
        pairs.append((q, true_offset @ fk(q)))
    fit = validate_against_isaac(pairs)
    fit_offset = np.asarray(fit["base_offset"], dtype=float)
    check("recovers the injected base offset (position)",
          np.allclose(fit_offset[:3, 3], [0.3, -0.2, 0.75], atol=1e-9),
          f"{np.round(fit_offset[:3, 3], 6).tolist()}")
    check("recovers the injected base offset (rotation)",
          np.allclose(fit_offset[:3, :3], true_offset[:3, :3], atol=1e-9))
    check("fit result is JSON-serializable", bool(json.dumps(fit)))
    check("residual is ~0 for exact data", fit["residual_max_m"] < 1e-9
          and fit["residual_max_rad"] < 1e-9)
    check("fit with identity offset finds identity",
          np.allclose(np.asarray(validate_against_isaac(
              [(q, fk(q)) for q, _ in pairs])["base_offset"], dtype=float),
              np.eye(4), atol=1e-9))
    noisy = [(q, true_offset @ fk(q) @ make_transform(np.eye(3), [0.001, 0, 0]))
             for q, _ in pairs]
    fit_noisy = validate_against_isaac(noisy)
    check("non-constant offsets show up as spread",
          fit_noisy["spread_m"] > 5e-4, f"spread={fit_noisy['spread_m']:.2e} m")
    try:
        validate_against_isaac(pairs[:2])
        check("too few pairs raises", False)
    except ValueError:
        check("too few pairs raises", True)

    # 9. Import hygiene: no ROS at import time.
    print("9. import hygiene")
    check("rclpy not imported by this module", "rclpy" not in sys.modules)
    check("numpy-only transform helpers", all(
        isinstance(v, np.ndarray) for v in (fk(HOME_Q), jacobian(HOME_Q))))

    # 10. Home pose is usable as a demo start.
    print("10. demo home pose")
    check("home = all values finite", np.all(np.isfinite(HOME_Q)))
    p_home = fk_position(HOME_Q)
    check("home flange is above the table (z > 0.2 m)", p_home[2] > 0.2,
          f"z={p_home[2]:.3f}")
    check("home is within reach of the shoulder (< 0.85 m)", np.linalg.norm(p_home) < 0.85,
          f"{np.linalg.norm(p_home):.3f} m")
    print("-" * 72)
    print(("ALL CHECKS PASSED" if ok else "SELFTEST FAILED"))
    if verbose and ok:
        d = describe_pose(home_transform())
        print(f"home pose: p={np.round(d['position'], 4).tolist()} "
              f"rot={d['rot_angle_deg']:.1f} deg tool_z={np.round(d['tool_z_axis'], 3).tolist()}")
        print(f"home sigma_min={smin_home:.4f} rad, manipulability="
              f"{manipulability(jacobian(HOME_Q)):.4f}")
    return ok


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="UR5e kinematics + TwinLink selftest.")
    parser.add_argument("--selftest", action="store_true",
                        help="run the offline checks (no sim, no ROS, no GPU)")
    parser.add_argument("--pose", action="store_true",
                        help="print the DH table, the home pose and its Jacobian")
    args = parser.parse_args(argv)

    if args.selftest or not args.pose:
        if not selftest():
            return 1

    if args.pose:
        print()
        print("DH (standard): A_i = Rz(q) Tz(d) Tx(a) Rx(alpha)")
        for i in range(N_JOINTS):
            print(f"  i={i + 1} d={DH_D[i]: .4f} a={DH_A[i]: .4f} "
                  f"alpha={math.degrees(DH_ALPHA[i]): 7.2f} deg  {JOINT_NAMES[i]}")
        print(f"\nhome q = {np.round(HOME_Q, 6).tolist()} rad")
        T = home_transform()
        print("home T_base_flange =")
        print(np.array2string(T, precision=6, suppress_small=True))
        print(f"home position = {np.round(T[:3, 3], 6).tolist()} m")
        print(f"home sigma_min = {sigma_min(jacobian(HOME_Q)):.4f}, "
              f"manipulability = {manipulability(jacobian(HOME_Q)):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Kinematic receding-horizon MPC for the UR5e, with an offline demo and an on-twin mode.

EXACT COMMANDS (CasADi is not in the repo env, so it comes from an isolated uv env):

    # self-tests (FK vs an independent numpy model, convergence, constraints, timing)
    uv run --isolated --python 3.12 --with casadi --with numpy --with matplotlib \\
        python scripts/demo/mpc_kinematic.py --selftest

    # all offline scenarios -> PNGs + CSV + summary.json
    uv run --isolated --python 3.12 --with casadi --with numpy --with matplotlib \\
        python scripts/demo/mpc_kinematic.py --offline --out demo-out/mpc

    # closed loop against the Isaac Sim twin: needs ur5e_common.TwinLink and the
    # twin's ROS 2 /joint_states island. Run it from the repo root so the script's
    # own directory (scripts/demo) is on sys.path.
    uv run --isolated --python 3.12 --with casadi --with numpy --with matplotlib \\
        python scripts/demo/mpc_kinematic.py --on-twin --out demo-out/mpc --steps 200

Add `--with scipy` to any of them if a future scenario needs it; nothing here does.

WHY THIS FILE IS KINEMATIC (and how it differs from a quadrotor MPC)
--------------------------------------------------------------------
Quadrotor MPC you have probably written: state = position + velocity + attitude,
input = total thrust + body torques, model = Newton/Euler with gravity, and the
controller is the *only* thing between the setpoint and the motors. Thrust and
torque are the real actuator inputs, so the model has to be right or the vehicle
falls out of the sky.

This arm is different in three ways, and the code mirrors all three:

1. The UR5e already has a tuned inner joint servo (the UR control box runs its own
   position/velocity loop at 500 Hz, and the Isaac twin drives joints with a PD
   drive). We therefore do NOT command torque. We command a *joint velocity* that
   gets integrated into a joint *position* setpoint, handed to the inner loop at a
   slow rate (20 Hz here, dt = 50 ms). The MPC is an outer "supervisor" loop.
2. Because the inner loop is fast and stiff, the plant for the MPC is close to a
   pure integrator q_{k+1} = q_k + dt * qdot_k. No mass, no gravity, no inertia
   matrix, no contact. A velocity limit plus a first-order actuator lag
   (`simulate(..., plant="lag", tau=0.08)`) is enough to make the offline study
   honest about the sim-to-real gap.
3. The task is Cartesian: "put the flange here with this orientation". The cost is
   therefore a Cartesian pose error, but the decision variables stay in joint
   space, so we need forward kinematics inside the NLP (symbolic CasADi FK below).
   Quadrotor MPC usually has a linear-ish model and a quadratic cost; here the FK
   makes the problem non-convex, which is why we use IPOPT and warm starts.

What is the same as quadrotor MPC: receding horizon, a cost over N predicted
stages, terminal cost, input-rate penalty for smoothness, box constraints on the
inputs, warm start from the previous solution shifted by one sample, and the
"measure -> solve -> apply first input -> shift" loop. The structure of
`KinematicMPC.step` is exactly your quadrotor `solve()`.

FRAMES AND UNITS
----------------
* Angles in rad, lengths in m, time in s everywhere.
* FK uses Universal Robots' *standard* DH convention (Denavit-Hartenberg with
  A_i = Rz(theta_i) Tz(d_i) Tx(a_i) Rx(alpha_i)), which is the table UR publishes
  in "DH parameters for calculations with the UR5e":
      d  = [0.1625, 0, 0, 0.1333, 0.0997, 0.0996]      # m
      a  = [0, -0.425, -0.3922, 0, 0, 0]               # m
      al = [pi/2, 0, 0, pi/2, -pi/2, 0]                # rad
  (checked against the URe-series DH table reproduced in
  https://people.ohio.edu/williams/html/PDF/UniversalRobotKinematics.pdf, which
  lists the same magnitudes in mm: a2=425, a3=392, d1=162.5, d4=133.3, d5=99.7,
  d6=99.6.) With this table the arm reaches along -x at q=0, i.e. the DH base
  frame is rotated 180 deg about z relative to the "arm points along +x" pictures
  in some UR docs. See "WHAT THIS FILE ASSUMES" below for what that means for
  the twin.
* Frame 6 (the last DH frame) is taken to be the tool flange (`tool0`), with +z6
  pointing out of the flange along the approach direction. In UR's URDF the link
  called `flange`/`tool0` usually differs from frame 6 by a fixed rotation; the
  closed loop here never needs that rotation because goals on the twin are given
  as *joint* configurations (see `run_on_twin`).

WHAT THIS FILE ASSUMES, AND WHAT IT DOES NOT VERIFY
---------------------------------------------------
* Kinematics only. No mass, no gravity, no friction, no contact, no torque limit.
  That is the right model for a position-controlled UR with a tuned inner servo,
  and the wrong model for the real arm at speed (the box derates near joint
  limits) -- which is exactly what the on-twin run is for.
* The DH base frame and the twin's world frame differ by a constant. UR's DH
  table's zero pose reaches along -x of the DH base frame, while the twin places
  the robot yawed 180 deg so it reaches along world +x (`ur5e_common` documents
  `to_ur_base_link()` = Rz(pi) for that). Nothing in this controller depends on
  it: goals are joint configurations, the commands are joint positions, and the
  cost compares FK against FK. Only if you plot `ee_p` next to Isaac world
  coordinates do you need that constant, and I could not verify it without the
  sim.
* Flange vs tool: frame 6 is the UR "flange"/`tool0` frame. A gripper adds a
  fixed offset that this file does not model (`ur5e_common.FLANGE_TO_TOOL`).
* Joint limits are +/-6 rad by default (UR5e datasheet: +/-2pi), and the demo
  velocity limit is 1.0 rad/s (datasheet: pi rad/s, `--real-vel`).
* The floor is a plane at `floor_z` above the robot's mounting plate; in the twin
  the table top is z = 0, so `--floor-z 0.24` means "stay 24 cm above the table".
* The obstacle is one sphere and only the selected link origins (flange, elbow by
  default) are constrained, sampled at the N+1 horizon stages. A thin obstacle
  can still pass between two sampled points, and forearm/wrist *links* are not
  swept -- origins only.
* The table/obstacle thresholds in the scenarios were tuned offline; the twin may
  need different numbers once its real geometry is measured.

CONSTRAINTS: hard vs soft
-------------------------
Joint position limits, joint velocity limits and the table/floor are HARD: they
are physical safety limits, and a feasible solution exists by construction in the
scenarios below (start pose and goal are both legal, and the arm can always stop).
The spherical obstacle is HARD by default too, because for a kinematic arm
"refuse to plan through it" is the right failure mode; `--soft-obstacle` switches
to a slack variable with an L1 penalty, which is what you want if the goal itself
sits inside the obstacle or if the hard problem turns infeasible during a moving
goal. `step()` uses both: hard first, and if IPOPT reports infeasibility it
retries once with the obstacle softened (`info["relaxed"] = True`) instead of
stalling. Only if that also fails does it hold position (qdot = 0,
`info["fallback"] = True`) -- a kinematic arm cannot do better than "stop".
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence

import numpy as np

try:  # matplotlib is only needed for --offline / plots, but it is cheap to import
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import gridspec
    from matplotlib.patches import Circle as MplCircle

    HAS_MPL = True
except Exception:  # pragma: no cover - only on a broken matplotlib install
    HAS_MPL = False

try:
    import casadi as ca

    HAS_CASADI = True
except Exception:  # pragma: no cover - reported with a clear message in main()
    ca = None
    HAS_CASADI = False


# --------------------------------------------------------------------------------------
# 1. Robot model: UR5e forward kinematics (numpy + CasADi, same DH table)
# --------------------------------------------------------------------------------------

# Universal Robots' published UR5e DH table (standard / "distal" convention).
UR5E_DH_D = np.array([0.1625, 0.0, 0.0, 0.1333, 0.0997, 0.0996])
UR5E_DH_A = np.array([0.0, -0.425, -0.3922, 0.0, 0.0, 0.0])
UR5E_DH_ALPHA = np.array([np.pi / 2, 0.0, 0.0, np.pi / 2, -np.pi / 2, 0.0])

JOINT_NAMES = ("shoulder_pan", "shoulder_lift", "elbow", "wrist_1", "wrist_2", "wrist_3")

# The classic textbook check pose. With the DH table above the arm stands straight
# up ("candle"), so the flange position has a closed form we can assert against:
#     x = 0
#     y = -(d4 + d6)                    = -(0.1333 + 0.0996) = -0.2329
#     z = d1 + |a2| + |a3| + d5         = 0.1625 + 0.425 + 0.3922 + 0.0997 = 1.0794
TEXTBOOK_Q = np.array([0.0, -np.pi / 2, 0.0, -np.pi / 2, 0.0, 0.0])
TEXTBOOK_P = np.array([0.0, -(UR5E_DH_D[3] + UR5E_DH_D[5]),
                       UR5E_DH_D[0] + abs(UR5E_DH_A[1]) + abs(UR5E_DH_A[2]) + UR5E_DH_D[4]])

# A comfortable demo pose: elbow up, tool pointing down, flange 0.35 m above the
# robot mounting plate. Deliberately the same as `ur5e_common.HOME_Q` so the MPC
# plots and the resolved-rate demo start from the same configuration; q5 = -pi/2
# keeps the wrist away from its singular fully stretched configuration and the
# Jacobian well conditioned at t = 0.
HOME_Q = np.array([0.0, -1.30, 1.60, -1.80, -np.pi / 2, 0.0])

# UR5e joint speed limit is 180 deg/s (UR5e technical specification, "Speed:
# Joints: Max 180 deg/s"), i.e. pi rad/s. The joint *ranges* are +/-360 deg.
UR5E_JOINT_SPEED_LIMIT = np.pi  # rad/s


def dh_transform_np(theta: float, d: float, a: float, alpha: float) -> np.ndarray:
    """Standard DH link transform A_i = Rz(theta) Tz(d) Tx(a) Rx(alpha)."""
    ct, st = np.cos(theta), np.sin(theta)
    ca, sa = np.cos(alpha), np.sin(alpha)
    return np.array(
        [
            [ct, -st * ca, st * sa, a * ct],
            [st, ct * ca, -ct * sa, a * st],
            [0.0, sa, ca, d],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )


def ur5e_fk_np(q: Sequence[float]) -> dict[str, Any]:
    """Numpy FK: flange pose plus the link origins used by the constraints.

    Link origins (all in the DH base frame):
      O1 shoulder (joint 2 axis), O2 elbow (joint 3 axis), O3 wrist 1 (joint 4),
      O4 wrist 2 (joint 5), O5 wrist 3 (joint 6), O6 flange.
    """
    q = np.asarray(q, dtype=float).reshape(6)
    T = np.eye(4)
    origins = []
    for i in range(6):
        T = T @ dh_transform_np(q[i], UR5E_DH_D[i], UR5E_DH_A[i], UR5E_DH_ALPHA[i])
        origins.append(T[:3, 3].copy())
    return {
        "T": T,
        "p": T[:3, 3].copy(),
        "R": T[:3, :3].copy(),
        "origins": np.stack(origins),  # (6, 3) -> O1..O6
    }


def _fk_casadi(q):
    """Symbolic FK with the same DH table. Returns a dict of CasADi expressions.

    Called once per horizon stage when the NLP is built, so the whole forward
    kinematics ends up inside the CasADi graph (IPOPT gets exact derivatives from
    algorithmic differentiation; no finite differences anywhere).
    """
    T = ca.MX.eye(4)
    origins = []
    for i in range(6):
        th = q[i]
        ct, st = ca.cos(th), ca.sin(th)
        ca_, sa_ = ca.cos(float(UR5E_DH_ALPHA[i])), ca.sin(float(UR5E_DH_ALPHA[i]))
        a, d = float(UR5E_DH_A[i]), float(UR5E_DH_D[i])
        A = ca.vertcat(
            ca.horzcat(ct, -st * ca_, st * sa_, a * ct),
            ca.horzcat(st, ct * ca_, -ct * sa_, a * st),
            ca.horzcat(ca.MX(0), sa_, ca_, d),
            ca.horzcat(ca.MX(0), ca.MX(0), ca.MX(0), ca.MX(1)),
        )
        T = ca.mtimes(T, A)
        origins.append(T[0:3, 3])
    return {
        "T": T,
        "p": T[0:3, 3],
        "R": T[0:3, 0:3],
        "origins": origins,
    }


def _point_index(point: str) -> int:
    """Map a constraint point name onto its DH origin index (0-based)."""
    table = {"shoulder": 0, "elbow": 1, "wrist1": 2, "wrist2": 3, "wrist3": 4, "flange": 5}
    if point not in table:
        raise ValueError(f"unknown constraint point {point!r}; pick one of {sorted(table)}")
    return table[point]


# --------------------------------------------------------------------------------------
# 2. Rotation helpers
# --------------------------------------------------------------------------------------


def rot_angle(R: np.ndarray) -> float:
    """Geodesic angle of a rotation matrix, numerically safe near 0 and pi."""
    tr = float(np.clip((np.trace(R) - 1.0) * 0.5, -1.0, 1.0))
    return float(np.arccos(tr))


def rotation_error_cost(R_goal: np.ndarray, R: np.ndarray) -> float:
    """0.5 * ||R_goal^T R - I||_F^2, the orientation cost used in the NLP.

    Why this one: it is smooth and cheap in CasADi (`sumsqr` of a 3x3 residual),
    it needs no quaternion or Euler juggling inside the solver, and near the goal
    it behaves quadratically: for a relative rotation of angle theta,
        ||Rg^T R - I||_F^2 = 2 (3 - tr(Rg^T R)) = 4 (1 - cos theta),
    so the half-norm equals 2 (1 - cos theta) ~= theta^2. That matches the
    squared position error in units of rad^2, which makes Qo/Qp easy to reason
    about. (The trace form is exactly equivalent, but the Frobenius form lets a
    single `sumsqr` handle the whole matrix.) Known weakness, same as any
    chordal/exponential cost: the gradient vanishes at theta = pi, i.e. a goal
    rotated exactly 180 deg is a stationary point. Goals here come from FK of a
    joint configuration, so theta stays small along the demo paths.
    """
    E = R_goal.T @ R - np.eye(3)
    return 0.5 * float(np.sum(E * E))


# --------------------------------------------------------------------------------------
# 3. MPC configuration
# --------------------------------------------------------------------------------------


@dataclass
class MPCConfig:
    """Everything that shapes the NLP. Distances m, angles rad, time s."""

    dt: float = 0.05           # control period (20 Hz) -- the outer loop rate
    N: int = 20                # horizon length in stages => 1.0 s lookahead

    # Cost weights. Position error in m^2, orientation error in rad^2 (see
    # rotation_error_cost), velocities in (rad/s)^2.
    Qp: float = 1000.0         # position tracking
    Qo: float = 120.0          # orientation tracking (~ Qp * (0.09 m/rad)^2 trade)
    R: float = 4.0             # velocity effort (keeps the arm calm)
    Rd: float = 60.0           # velocity smoothness (delta-u), the quadrotor "dR"
    terminal_scale: float = 12.0  # terminal cost multiplier, terminal is what counts

    # Joint position box. Deliberately conservative: UR5e joints are rated
    # +/-360 deg (+/-2pi rad); +/-6 rad leaves 0.28 rad of margin and keeps the
    # solver away from the hard stop. Pass the real limits if you have a reason.
    q_min: np.ndarray = field(default_factory=lambda: -6.0 * np.ones(6))
    q_max: np.ndarray = field(default_factory=lambda: 6.0 * np.ones(6))

    # Joint velocity box. 1.0 rad/s is the demo limit (well below the robot);
    # use --real-vel (pi rad/s) to see what the hardware could actually do.
    qd_max: np.ndarray = field(default_factory=lambda: 1.0 * np.ones(6))

    # Table / floor: keep these points at least floor_z above the mounting plate.
    floor_z: float | None = 0.25
    floor_points: tuple[str, ...] = ("flange", "elbow")

    # One spherical obstacle, hard by default.
    obstacle_center: np.ndarray | None = None
    obstacle_radius: float = 0.0
    obstacle_margin: float = 0.02
    obstacle_points: tuple[str, ...] = ("flange", "elbow")
    obstacle_soft: bool = False
    # Penalty on the squared-distance deficit (m^2) when the obstacle is soft.
    obstacle_soft_weight: float = 2.0e4

    # Solver budget. max_iter bounds the worst-case solve so the loop can keep up.
    max_iter: int = 90
    tol: float = 1e-4
    print_level: int = 0

    def velocity_limit(self) -> np.ndarray:
        return np.asarray(self.qd_max, dtype=float).reshape(6)

    def describe(self) -> dict[str, Any]:
        """JSON-friendly snapshot for summary.json."""
        return {
            "dt_s": self.dt,
            "N": self.N,
            "horizon_s": self.N * self.dt,
            "Qp": self.Qp,
            "Qo": self.Qo,
            "R": self.R,
            "Rd": self.Rd,
            "terminal_scale": self.terminal_scale,
            "q_min": np.asarray(self.q_min).tolist(),
            "q_max": np.asarray(self.q_max).tolist(),
            "qd_max": self.velocity_limit().tolist(),
            "floor_z": self.floor_z,
            "floor_points": list(self.floor_points),
            "obstacle_center": None if self.obstacle_center is None
            else np.asarray(self.obstacle_center, float).tolist(),
            "obstacle_radius": self.obstacle_radius,
            "obstacle_margin": self.obstacle_margin,
            "obstacle_points": list(self.obstacle_points),
            "obstacle_soft": self.obstacle_soft,
            "obstacle_soft_weight": self.obstacle_soft_weight,
            "max_iter": self.max_iter,
            "tol": self.tol,
        }


# --------------------------------------------------------------------------------------
# 4. The controller
# --------------------------------------------------------------------------------------


class KinematicMPC:
    """Receding-horizon kinematic MPC for a UR5e: state q, input qdot.

    Model (discrete, exact for a velocity-commanded joint integrator):

        q_{k+1} = q_k + dt * qdot_k

    Cost over the horizon (k = 0..N):

        Qp ||p(q_k) - p_goal,k||^2
      + Qo * 0.5 ||R_goal,k^T R(q_k) - I||_F^2
      + R  ||qdot_k||^2
      + Rd ||qdot_k - qdot_{k-1}||^2          (k >= 1; k=0 compares to the last
                                               velocity actually applied)
      + terminal_scale * (Qp, Qo terms at k = N)

    Constraints: q0 = measured q, joint boxes, velocity boxes, floor on selected
    link origins, optional sphere for selected link origins.

    The NLP is built ONCE (`_build`) and re-solved with new parameter values, which
    is the CasADi way of doing what you did with a rebuilt quadrotor problem every
    step: rebuilding costs more than solving at this size. Warm start = previous
    solution shifted by one stage, last stage duplicated, exactly like the
    quadrotor trajectory shift.
    """

    def __init__(self, cfg: MPCConfig | None = None):
        if not HAS_CASADI:
            raise RuntimeError(
                "CasADi is not installed in this interpreter. Run this file with "
                "`uv run --isolated --python 3.12 --with casadi --with numpy --with "
                "matplotlib python scripts/demo/mpc_kinematic.py ...`"
            )
        self.cfg = cfg or MPCConfig()
        self._validate()
        self._warm: tuple[np.ndarray, np.ndarray] | None = None
        self._last_qd = np.zeros(6)
        self._relaxed: "KinematicMPC | None" = None
        self._build()

    # -- setup ------------------------------------------------------------------
    def _validate(self) -> None:
        c = self.cfg
        if c.N < 1:
            raise ValueError("horizon N must be >= 1")
        if c.dt <= 0:
            raise ValueError("dt must be positive")
        for p in list(c.floor_points) + list(c.obstacle_points):
            _point_index(p)

    def _build(self) -> None:
        c = self.cfg
        N = c.N

        self.opti = ca.Opti()
        self.X = self.opti.variable(6, N + 1)   # predicted joint positions
        self.V = self.opti.variable(6, N)       # joint velocities (the input)

        # Parameters: the measured state, the last applied velocity (smoothness at
        # k=0), and the goal as a sequence over the horizon (moving-goal support).
        self.p_q0 = self.opti.parameter(6)
        self.p_qd_prev = self.opti.parameter(6)
        self.p_p_goal = self.opti.parameter(3, N + 1)
        self.p_R_goal = self.opti.parameter(9, N + 1)  # column-major 3x3 per stage

        opti = self.opti

        # --- dynamics: one linear equality per stage --------------------------
        opti.subject_to(self.X[:, 0] == self.p_q0)
        for k in range(N):
            opti.subject_to(self.X[:, k + 1] == self.X[:, k] + c.dt * self.V[:, k])

        # --- joint position box (hard) ---------------------------------------
        q_lo = np.asarray(c.q_min, float).reshape(6, 1)
        q_hi = np.asarray(c.q_max, float).reshape(6, 1)
        for k in range(N + 1):
            opti.subject_to(opti.bounded(q_lo, self.X[:, k], q_hi))

        # --- joint velocity box (hard) ---------------------------------------
        v_lim = np.repeat(c.velocity_limit().reshape(6, 1), N, axis=1)
        opti.subject_to(opti.bounded(-v_lim, self.V, v_lim))

        # --- floor / table (hard) --------------------------------------------
        floor_idx = [_point_index(p) for p in c.floor_points]
        if c.floor_z is not None and floor_idx:
            for k in range(N + 1):
                fk = _fk_casadi(self.X[:, k])
                for i in floor_idx:
                    opti.subject_to(fk["origins"][i][2] >= float(c.floor_z))

        # --- spherical obstacle ----------------------------------------------
        obs_idx = [_point_index(p) for p in c.obstacle_points]
        self._slack = None
        if c.obstacle_center is not None and float(c.obstacle_radius) > 0 and obs_idx:
            centre = np.asarray(c.obstacle_center, float).reshape(3)
            safe = float(c.obstacle_radius) + float(c.obstacle_margin)
            # The constraint is written on the SQUARED distance:
            #     ||p - c||^2 >= safe^2        (equivalent, since safe > 0)
            # Two reasons: no sqrt in the graph, and no NaN gradient. norm_2(0)
            # has an undefined derivative, and the "obstacle appears around the
            # arm" case puts the flange exactly at the centre for one step, which
            # makes IPOPT see NaN in the Jacobian and give up.
            safe_sq = safe * safe
            if c.obstacle_soft:
                # Soft: the squared distance deficit is paid for linearly (L1 on a
                # convex-ish residual => exact penalty, no oscillation). Use when
                # the hard problem is infeasible: goal or current pose inside the
                # sphere, or a corridor that closes while the target moves.
                self._slack = opti.variable(len(obs_idx), N + 1)
                sl = self._slack
                opti.subject_to(opti.bounded(0.0, sl, 1.0))  # slack in m^2
                for k in range(N + 1):
                    fk = _fk_casadi(self.X[:, k])
                    for j, i in enumerate(obs_idx):
                        d2 = ca.sumsqr(fk["origins"][i] - centre)
                        opti.subject_to(d2 + sl[j, k] >= safe_sq)
            else:
                # Hard: refuse to plan through the sphere. The demo scenarios all
                # have a feasible detour, so a failure here means "no path" (see
                # step()'s relax-on-demand retry).
                for k in range(N + 1):
                    fk = _fk_casadi(self.X[:, k])
                    for i in obs_idx:
                        d2 = ca.sumsqr(fk["origins"][i] - centre)
                        opti.subject_to(d2 >= safe_sq)

        # --- cost -------------------------------------------------------------
        I3 = ca.MX.eye(3)
        J = 0
        for k in range(N + 1):
            fk = _fk_casadi(self.X[:, k])
            ep = fk["p"] - self.p_p_goal[:, k]
            Rg = ca.reshape(self.p_R_goal[:, k], 3, 3)
            eo = ca.mtimes(Rg.T, fk["R"]) - I3
            w_pos = c.Qp * (c.terminal_scale if k == N else 1.0)
            w_ori = c.Qo * (c.terminal_scale if k == N else 1.0)
            J += w_pos * ca.sumsqr(ep) + w_ori * 0.5 * ca.sumsqr(eo)

        for k in range(N):
            J += c.R * ca.sumsqr(self.V[:, k])
            # Smoothness: penalise the change of the *input* between stages. At
            # k = 0 the previous input is the one actually applied last step, so
            # the plan cannot jump just because the horizon shifted.
            prev_u = self.p_qd_prev if k == 0 else self.V[:, k - 1]
            J += c.Rd * ca.sumsqr(self.V[:, k] - prev_u)

        if self._slack is not None:
            J += c.obstacle_soft_weight * ca.sum1(ca.sum2(self._slack))

        opti.minimize(J)
        self._J = J

        popts = {"print_time": False, "error_on_fail": False}
        sopts = {
            "print_level": int(c.print_level),
            "max_iter": int(c.max_iter),
            "tol": float(c.tol),
            "acceptable_tol": 1e-3,
            "acceptable_iter": 5,
            "warm_start_init_point": "yes",
            "warm_start_bound_push": 1e-6,
            "warm_start_mult_bound_push": 1e-6,
            "mu_strategy": "adaptive",
            "sb": "yes",
        }
        try:
            opti.solver("ipopt", popts, sopts)
        except Exception:  # pragma: no cover - very old CasADi builds
            opti.solver("ipopt")

    def reset(self) -> None:
        """Forget the warm start (call between scenarios / after a failure)."""
        self._warm = None
        self._last_qd = np.zeros(6)

    # -- goals ------------------------------------------------------------------
    def _goal_stages(
        self, goal: Any, t: float | None
    ) -> tuple[np.ndarray, np.ndarray]:
        """Expand the goal argument into per-stage (positions, rotations).

        Accepted forms:
          (p, R)                      constant pose, p (3,), R (3,3)
          {"p": ..., "R": ...}        same, dict form
          callable t -> (p, R)        moving goal, sampled at t + k*dt
          [(p, R)] * (N+1)            explicit per-stage sequence
          (P, Rs) with P (N+1,3)      arrays over the horizon
        """
        c = self.cfg
        N = c.N
        P = np.zeros((3, N + 1))
        Rs = np.zeros((9, N + 1))

        def put(k: int, p, R) -> None:
            P[:, k] = np.asarray(p, float).reshape(3)
            Rk = np.asarray(R, float).reshape(3, 3)
            Rs[:, k] = Rk.ravel(order="F")  # CasADi reshape is column-major

        if callable(goal):
            t0 = 0.0 if t is None else float(t)
            for k in range(N + 1):
                p, R = goal(t0 + k * c.dt)
                put(k, p, R)
            return P, Rs

        if isinstance(goal, dict):
            p = goal["p"]
            R = goal.get("R", np.eye(3))
            for k in range(N + 1):
                put(k, p, R)
            return P, Rs

        if isinstance(goal, (list, tuple)) and len(goal) == 2 and not np.isscalar(goal[0]):
            first = np.asarray(goal[0], float)
            if first.ndim == 2 and first.shape[0] == self.cfg.N + 1:
                # (N+1, 3) positions with one rotation or a matching list
                Rs_in = goal[1]
                for k in range(N + 1):
                    if np.asarray(Rs_in).ndim == 2:
                        put(k, first[k], Rs_in)
                    else:
                        put(k, first[k], np.asarray(Rs_in)[k])
                return P, Rs
            p, R = goal
            for k in range(N + 1):
                put(k, p, R)
            return P, Rs

        if isinstance(goal, (list, tuple)) and len(goal) == self.cfg.N + 1:
            for k, (p, R) in enumerate(goal):
                put(k, p, R)
            return P, Rs

        raise TypeError(f"unsupported goal specification: {type(goal)!r}")

    # -- solver plumbing --------------------------------------------------------
    def _prepare(self, q: np.ndarray, P: np.ndarray, Rs: np.ndarray,
                 qd_prev: np.ndarray) -> None:
        """Push the new parameters in and set the warm start."""
        c = self.cfg
        self.opti.set_value(self.p_q0, q)
        self.opti.set_value(self.p_qd_prev, qd_prev)
        self.opti.set_value(self.p_p_goal, P)
        self.opti.set_value(self.p_R_goal, Rs)

        # Warm start: shift the previous plan one stage to the left and hold the
        # last stage. Same trick as shifting the quadrotor state trajectory.
        if self._warm is None:
            X0 = np.repeat(q.reshape(6, 1), c.N + 1, axis=1)
            V0 = np.zeros((6, c.N))
        else:
            Xw, Vw = self._warm
            X0 = np.hstack([Xw[:, 1:], Xw[:, -1:]])
            V0 = np.hstack([Vw[:, 1:], Vw[:, -1:]])
        self.opti.set_initial(self.X, X0)
        self.opti.set_initial(self.V, V0)

    def _solve_once(self):
        """Solve the current NLP. Returns (solution_or_None, status, its, cost, X, V)."""
        solved = None
        status = "unknown"
        its = -1
        try:
            solved = self.opti.solve()
        except RuntimeError as exc:
            # IPOPT failed to converge or declared infeasibility. Keep the last
            # iterate for diagnostics only; step() decides whether to relax.
            lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
            hint = [ln for ln in lines
                    if "EXIT" in ln or "infeasib" in ln.lower() or "restoration" in ln.lower()]
            status = (hint[-1] if hint else (lines[-1] if lines else "solve failed"))[:200]
        stats = self.opti.stats() if hasattr(self.opti, "stats") else {}
        if solved is not None:
            status = str(stats.get("return_status", "Solve_Succeeded"))
            its = int(stats.get("iter_count", -1))
            cost = float(solved.value(self._J))
            Xv = np.array(solved.value(self.X), dtype=float)
            Vv = np.array(solved.value(self.V), dtype=float)
            return solved, status, its, cost, Xv, Vv
        its = int(stats.get("iter_count", -1)) if stats else -1
        try:
            Xv = np.array(self.opti.debug.value(self.X), dtype=float)
            Vv = np.array(self.opti.debug.value(self.V), dtype=float)
        except Exception:
            Xv = np.repeat(np.zeros((6, 1)), self.cfg.N + 1, axis=1)
            Vv = np.zeros((6, self.cfg.N))
        return None, status, its, float("nan"), Xv, Vv

    def _can_relax(self) -> bool:
        """True when a soft-obstacle twin of this problem would help."""
        c = self.cfg
        return (not c.obstacle_soft and c.obstacle_center is not None
                and float(c.obstacle_radius) > 0)

    def _relaxed_step(self, q: np.ndarray, goal: Any, qd_prev: np.ndarray,
                      t: float | None):
        """One step of the same OCP with the obstacle softened (built lazily)."""
        if self._relaxed is None:
            self._relaxed = KinematicMPC(replace(self.cfg, obstacle_soft=True))
        relaxed = self._relaxed
        relaxed._warm = self._warm
        relaxed._last_qd = self._last_qd
        P, Rs = relaxed._goal_stages(goal, t)
        relaxed._prepare(q, P, Rs, qd_prev)
        solved, status, its, cost, Xv, Vv = relaxed._solve_once()
        self._warm = relaxed._warm
        return solved, status, its, cost, Xv, Vv

    # -- one control step -------------------------------------------------------
    def step(
        self,
        q: Sequence[float],
        goal: Any,
        qdot_prev: Sequence[float] | None = None,
        t: float | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Solve the OCP and return (qdot command for the next dt, info dict).

        This is the whole controller: set parameters, warm start, solve, take the
        first input. `info["predicted_ee_path"]` is the open-loop EE prediction
        over the horizon (N+1, 3) -- useful to draw the plan on a slide.
        """
        c = self.cfg
        q = np.asarray(q, dtype=float).reshape(6)
        qd_prev = self._last_qd if qdot_prev is None else np.asarray(qdot_prev, float).reshape(6)
        P, Rs = self._goal_stages(goal, t)

        t_start = time.perf_counter()

        self._prepare(q, P, Rs, qd_prev)
        solved, status, its, cost, Xv, Vv = self._solve_once()
        relaxed = False
        if solved is None and self._can_relax():
            # Hard constraints can be infeasible at a given state (a tight
            # corridor between a table and an obstacle, or a goal that moved
            # into the sphere). Rather than hold still forever, retry once with
            # the obstacle softened -- the same "relax on demand" trick a QP
            # controller uses when its constraint set empties. The relaxation is
            # reported in info/metrics so it can never hide in the evidence.
            solved, status, its, cost, Xv, Vv = self._relaxed_step(q, goal, qd_prev, t)
            relaxed = True

        solve_ms = 1e3 * (time.perf_counter() - t_start)

        ok = solved is not None
        if ok and np.all(np.isfinite(Vv[:, 0])):
            qd_cmd = Vv[:, 0].copy()
            # The solver respects the box, but clip anyway: a command that leaves
            # this box must never reach the robot, whatever the solver said.
            qd_cmd = np.clip(qd_cmd, -c.velocity_limit(), c.velocity_limit())
            self._warm = (Xv, Vv)
            self._last_qd = qd_cmd
            fallback = False
        else:
            # Fallback: hold position. For a kinematic arm "stop" is always safe.
            qd_cmd = np.zeros(6)
            self._warm = None
            self._last_qd = np.zeros(6)
            fallback = True

        ee_pred = np.array([ur5e_fk_np(Xv[:, k])["p"] for k in range(c.N + 1)])
        qd_pred = Vv

        info = {
            "cost": cost,
            "solve_ms": solve_ms,
            "status": status,
            "ok": bool(ok),
            "fallback": bool(fallback),
            "relaxed": bool(relaxed),
            "iterations": its,
            "qdot_cmd": qd_cmd,
            "qdot_plan": qd_pred,
            "q_plan": Xv,
            "predicted_ee_path": ee_pred,
            "t": None if t is None else float(t),
        }
        if c.obstacle_center is not None and float(c.obstacle_radius) > 0:
            p = ee_pred
            d = np.linalg.norm(p - np.asarray(c.obstacle_center, float), axis=1)
            info["predicted_min_obstacle_distance"] = float(np.min(d) - c.obstacle_radius)
        return qd_cmd, info

    # -- convenience ------------------------------------------------------------
    def info_cost_terms(self, q: Sequence[float], goal: Any) -> dict[str, float]:
        """Evaluate the (numpy) cost terms at a single configuration.

        Handy when explaining weights on a slide: "3 cm off costs the same as
        0.1 rad of orientation error with the weights used here".
        """
        c = self.cfg
        q = np.asarray(q, float).reshape(6)
        fk = ur5e_fk_np(q)
        if callable(goal):
            p, R = goal(0.0)
        elif isinstance(goal, dict):
            p, R = goal["p"], goal.get("R", np.eye(3))
        else:
            p, R = goal[0], goal[1]
        p = np.asarray(p, float).reshape(3)
        R = np.asarray(R, float).reshape(3, 3)
        return {
            "position": float(c.Qp * np.sum((fk["p"] - p) ** 2)),
            "orientation": float(c.Qo * rotation_error_cost(R, fk["R"])),
            "pos_err_m": float(np.linalg.norm(fk["p"] - p)),
            "ori_err_rad": float(rot_angle(R.T @ fk["R"])),
        }

    # -- offline plants ---------------------------------------------------------
    def simulate(
        self,
        q0: Sequence[float],
        goal: Any,
        n_steps: int,
        plant: str = "ideal",
        tau: float = 0.08,
    ) -> "RunLog":
        """Closed loop against a simulated plant. Returns a RunLog.

        plant="ideal": the commanded joint velocity is reached instantly (the
            model the MPC believes in). This is the "no sim-to-real gap" case.
        plant="lag":   first-order actuator lag, qdot_dot = (qdot_cmd - qdot)/tau,
            integrated with the same dt. tau ~ 50-100 ms is a cheap stand-in for
            the UR control box's velocity loop plus the twin's PD drive. It is the
            interesting case: the MPC plans for an instant servo, so a lag shows
            up as tracking delay and a little overshoot at the end of a move. On
            the real arm you would additionally see gravity-dependent error.
        """
        q = np.asarray(q0, float).reshape(6)
        qd_act = np.zeros(6)
        self.reset()
        log = RunLog(cfg=self.cfg, plant=plant, tau=tau)
        for k in range(int(n_steps)):
            t = k * self.cfg.dt
            g = goal(t) if callable(goal) else goal
            qd_cmd, info = self.step(q, g, qdot_prev=qd_act, t=t)
            log.append(t, q, qd_cmd, qd_act, info, goal=g)
            # Plant update (same dt as the controller; a finer plant would change
            # nothing qualitatively at 20 Hz with a 50-80 ms lag). alpha is capped
            # at 1 so tau < dt cannot make the discrete lag oscillate.
            if plant == "ideal":
                qd_act = np.asarray(qd_cmd, float)
            else:
                alpha = min(1.0, self.cfg.dt / float(tau))
                qd_act = qd_act + alpha * (np.asarray(qd_cmd, float) - qd_act)
            q = q + self.cfg.dt * qd_act
        return log


# --------------------------------------------------------------------------------------
# 5. Logging / metrics
# --------------------------------------------------------------------------------------


class RunLog:
    """Per-step record of a closed-loop run, with metrics computed on demand."""

    def __init__(self, cfg: MPCConfig, plant: str = "ideal", tau: float = 0.08):
        self.cfg = cfg
        self.plant = plant
        self.tau = tau
        self.rows: list[dict[str, Any]] = []

    def append(
        self, t: float, q: np.ndarray, qd_cmd: np.ndarray, qd_act: np.ndarray,
        info: dict[str, Any], goal: Any = None,
    ) -> None:
        fk = ur5e_fk_np(q)
        if callable(goal):
            gp, gR = goal(t)
        elif isinstance(goal, dict):
            gp, gR = goal["p"], goal.get("R", np.eye(3))
        elif goal is None:
            gp, gR = fk["p"], fk["R"]
        else:
            gp, gR = goal[0], goal[1]
        gp = np.asarray(gp, float).reshape(3)
        gR = np.asarray(gR, float).reshape(3, 3)
        self.rows.append(
            {
                "t": float(t),
                "q": np.asarray(q, float).copy(),
                "qd_cmd": np.asarray(qd_cmd, float).copy(),
                "qd_act": np.asarray(qd_act, float).copy(),
                "ee_p": fk["p"],
                "ee_R": fk["R"],
                "err_p": float(np.linalg.norm(fk["p"] - gp)),
                "err_ang": float(rot_angle(gR.T @ fk["R"])),
                "cost": float(info.get("cost", float("nan"))),
                "solve_ms": float(info.get("solve_ms", float("nan"))),
                "status": str(info.get("status", "")),
                "iterations": int(info.get("iterations", -1)),
                "fallback": bool(info.get("fallback", False)),
                "relaxed": bool(info.get("relaxed", False)),
                "goal_p": gp,
                "goal_R": gR,
                "predicted_ee_path": np.asarray(info.get("predicted_ee_path")),
                "qdot_plan": np.asarray(info.get("qdot_plan")),
            }
        )

    # -- arrays ---------------------------------------------------------------
    def array(self, key: str) -> np.ndarray:
        return np.stack([r[key] for r in self.rows])

    @property
    def n(self) -> int:
        return len(self.rows)

    def fine_samples(self, sub: int = 4) -> np.ndarray:
        """Joint positions sampled along the executed path (sub steps per dt).

        The NLP only constrains the stage points; checking the executed path at
        sub-dt resolution is how we catch a floor/obstacle violation that happened
        between two stages.
        """
        qs = self.array("q")
        out = [qs[0]]
        for k in range(len(qs) - 1):
            for j in range(1, sub + 1):
                a = j / (sub + 1.0)
                out.append((1 - a) * qs[k] + a * qs[k + 1])
        return np.array(out)

    def dist_to_obstacle(self, centre: Sequence[float], radius: float, point: str = "flange",
                         sub: int = 4) -> np.ndarray:
        idx = _point_index(point)
        idxs = []
        for q in self.fine_samples(sub):
            idxs.append(float(np.linalg.norm(ur5e_fk_np(q)["origins"][idx]
                                             - np.asarray(centre, float)) - radius))
        return np.array(idxs)

    def point_height(self, point: str = "flange") -> np.ndarray:
        i = _point_index(point)
        return np.array([ur5e_fk_np(q)["origins"][i][2] for q in self.array("q")])

    def metrics(self) -> dict[str, Any]:
        c = self.cfg
        t = self.array("t")
        err = self.array("err_p")
        err_ang = self.array("err_ang")
        qd = self.array("qd_act")
        ee = self.array("ee_p")
        solve = self.array("solve_ms")

        within = np.where(err <= 0.005)[0]
        # "time to within 5 mm" = first time after which the error *stays* inside
        # 5 mm for the rest of the run (a single lucky sample is not convergence).
        settle = float("nan")
        for i in within:
            if np.all(err[i:] <= 0.005):
                settle = float(t[i])
                break
        step_len = np.linalg.norm(np.diff(ee, axis=0), axis=1)
        dt = c.dt
        # The very first solve has no warm start (IPOPT also builds its internal
        # structures), so it is always the slowest. Report it separately instead of
        # letting one cold solve decide the real-time verdict for the whole run.
        warm = solve[1:] if self.n > 1 else solve
        k1 = max(1, int(round(1.0 / dt)))  # one second of control steps
        m: dict[str, Any] = {
            "steps": int(self.n),
            "duration_s": float(t[-1] + dt) if self.n else 0.0,
            "final_pos_err_m": float(err[-1]),
            "final_ang_err_rad": float(err_ang[-1]),
            "min_pos_err_m": float(err.min()),
            "time_to_within_5mm_s": settle,
            "peak_joint_speed_rad_s": float(np.max(np.abs(qd))),
            "max_cmd_delta_rad_s": float(np.max(np.abs(np.diff(self.array("qd_cmd"), axis=0))))
            if self.n > 1 else 0.0,
            "mean_cmd_delta_rad_s": float(np.mean(np.abs(np.diff(self.array("qd_cmd"), axis=0))))
            if self.n > 1 else 0.0,
            "path_length_m": float(np.sum(step_len)),
            "final_cost": float(self.array("cost")[-1]),
            "solve_ms_mean": float(np.nanmean(solve)),
            "solve_ms_max": float(np.nanmax(solve)),
            "solve_ms_p50": float(np.nanpercentile(solve, 50)),
            "solve_ms_p95": float(np.nanpercentile(solve, 95)),
            "solve_ms_first_cold": float(solve[0]),
            "solve_ms_mean_warm": float(np.nanmean(warm)),
            "solve_ms_max_warm": float(np.nanmax(warm)),
            "frac_steps_over_dt": float(np.mean(solve > 1e3 * dt)),
            "frac_steps_over_dt_warm": float(np.mean(warm > 1e3 * dt)),
            # After one second the controller is tracking and warm starts hit the
            # optimum in 1-2 IPOPT iterations. The first ~0.5 s of a new task is
            # where the expensive solves live, so report both separately instead
            # of letting one transient decide the real-time verdict.
            "solve_ms_mean_after_1s": float(np.nanmean(solve[k1:])) if self.n > k1 else float("nan"),
            "solve_ms_max_after_1s": float(np.nanmax(solve[k1:])) if self.n > k1 else float("nan"),
            "frac_steps_over_dt_after_1s": float(np.mean(solve[k1:] > 1e3 * dt))
            if self.n > k1 else float("nan"),
            "fallback_steps": int(np.sum(self.array("fallback"))),
            "relaxed_steps": int(np.sum(self.array("relaxed"))),
            "plant": self.plant,
            "plant_tau_s": self.tau if self.plant == "lag" else 0.0,
        }
        if c.obstacle_center is not None and float(c.obstacle_radius) > 0:
            d = np.concatenate(
                [self.dist_to_obstacle(c.obstacle_center, c.obstacle_radius, pt)
                 for pt in c.obstacle_points]
            )
            m["min_obstacle_clearance_m"] = float(d.min())
        if c.floor_z is not None and c.floor_points:
            h = np.concatenate([self.point_height(pt) for pt in c.floor_points])
            m["min_floor_clearance_m"] = float(h.min() - c.floor_z)
        return m

    def write_csv(self, path: str) -> None:
        q = self.array("q")
        qd_cmd = self.array("qd_cmd")
        qd_act = self.array("qd_act")
        ee = self.array("ee_p")
        gp = self.array("goal_p")
        rows = []
        for i, r in enumerate(self.rows):
            row = {"t_s": r["t"]}
            for j in range(6):
                row[f"q{j + 1}"] = q[i, j]
            for j in range(6):
                row[f"qdot_cmd{j + 1}"] = qd_cmd[i, j]
            for j in range(6):
                row[f"qdot_act{j + 1}"] = qd_act[i, j]
            for j, ax in enumerate("xyz"):
                row[f"ee_{ax}"] = ee[i, j]
                row[f"goal_{ax}"] = gp[i, j]
            row["err_p_m"] = r["err_p"]
            row["err_ang_rad"] = r["err_ang"]
            row["cost"] = r["cost"]
            row["solve_ms"] = r["solve_ms"]
            row["status"] = r["status"]
            row["iterations"] = r["iterations"]
            row["fallback"] = int(r["fallback"])
            row["relaxed"] = int(r["relaxed"])
            if r["predicted_ee_path"] is not None and r["predicted_ee_path"].ndim == 2:
                row["plan_end_x"] = r["predicted_ee_path"][-1, 0]
                row["plan_end_y"] = r["predicted_ee_path"][-1, 1]
                row["plan_end_z"] = r["predicted_ee_path"][-1, 2]
            rows.append(row)
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)


# --------------------------------------------------------------------------------------
# 6. Goals and scenarios
# --------------------------------------------------------------------------------------


def pose_of(q: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    """Goal pose taken from FK of a joint configuration: always exactly reachable."""
    fk = ur5e_fk_np(q)
    return fk["p"], fk["R"]


def circular_goal(centre: Sequence[float], radius: float, period: float,
                  R_fixed: np.ndarray, plane: str = "xy",
                  phase: float = 0.0) -> Callable[[float], tuple[np.ndarray, np.ndarray]]:
    """Moving goal on a circle at constant orientation, sampled through time.

    Same idea as a moving target in a quadrotor demo: because the MPC sees the
    target's whole horizon it anticipates (the cost has no explicit target
    velocity, so the lookahead is what buys the feed-forward).
    """
    c = np.asarray(centre, float).reshape(3)

    def goal(t: float) -> tuple[np.ndarray, np.ndarray]:
        w = 2.0 * np.pi * t / period + phase
        off = np.zeros(3)
        if plane == "xy":
            off[0], off[1] = radius * np.cos(w), radius * np.sin(w)
        elif plane == "xz":
            off[0], off[2] = radius * np.cos(w), radius * np.sin(w)
        else:
            raise ValueError("plane must be 'xy' or 'xz'")
        return c + off, np.asarray(R_fixed, float).reshape(3, 3)

    return goal


# Demo joint targets. Chosen so that the pose stays inside a comfortable,
# non-singular envelope with the elbow up and the tool pointing down, and so that
# the joint deltas are small enough for the 1 rad/s demo velocity limit to be
# exercised (but not saturated) instead of making the problem trivial.
REACH_GOAL_Q = np.array([0.60, -1.05, 1.05, -1.35, -np.pi / 2, 0.40])

# Constraint scenario: the goal is a table-level pick pose; a sphere sits on the
# straight line between home and goal, low enough that slipping underneath it is
# blocked by the table. The MPC has to route around/over while the elbow stays
# above the table plane (the elbow height is d1 + |a2| sin|q2|, so it drops as the
# arm reaches out -- that coupling is what makes this scenario interesting).
CONSTRAINT_GOAL_Q = np.array([0.60, -0.70, 0.80, -1.40, -np.pi / 2, 0.30])


# --------------------------------------------------------------------------------------
# 7. Plot helpers (slide-ready: 1600x900, light background, large fonts)
# --------------------------------------------------------------------------------------

C_LINE = "#1f77b4"
C_ALT = "#d62728"
C_OK = "#2ca02c"
C_WARN = "#ff7f0e"
C_DARK = "#2b2f38"
C_GRID = "#dde2ea"


def _apply_style() -> None:
    if not HAS_MPL:
        return
    plt.rcParams.update(
        {
            "figure.facecolor": "#f6f7fb",
            "axes.facecolor": "#ffffff",
            "savefig.facecolor": "#f6f7fb",
            "axes.edgecolor": "#c3c9d4",
            "axes.labelcolor": C_DARK,
            "text.color": C_DARK,
            "xtick.color": C_DARK,
            "ytick.color": C_DARK,
            "axes.grid": True,
            "grid.color": C_GRID,
            "grid.linewidth": 1.0,
            "axes.axisbelow": True,
            "axes.titlesize": 23,
            "axes.labelsize": 19,
            "xtick.labelsize": 15,
            "ytick.labelsize": 15,
            "legend.fontsize": 14,
            "font.size": 17,
            "axes.titleweight": "semibold",
            "lines.linewidth": 2.6,
            "figure.titlesize": 26,
        }
    )


def _save(fig, path: str) -> str:
    import warnings

    with warnings.catch_warnings():
        # A table-only axis is not tight_layout friendly; the layout is fine.
        warnings.filterwarnings("ignore", message=".*tight_layout.*")
        fig.tight_layout()
    fig.savefig(path, dpi=100)  # 16x9 in at 100 dpi -> exactly 1600x900 px
    plt.close(fig)
    return path


def _panel_title(ax, text: str) -> None:
    ax.set_title(text, loc="left", pad=10)


def plot_reach(log: RunLog, cfg: MPCConfig, path: str,
               title: str = "Scenario 1 - kinematic MPC reaches a Cartesian goal") -> str:
    _apply_style()
    fig = plt.figure(figsize=(16, 9))
    gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.46, wspace=0.24)
    t = log.array("t")
    err = 1e3 * log.array("err_p")
    ee = log.array("ee_p")
    goal_p = log.array("goal_p")[0]
    solve = log.array("solve_ms")

    ax = fig.add_subplot(gs[0, 0])
    ax.plot(t, err, color=C_LINE, label="flange position error")
    ax.axhline(5.0, color=C_OK, ls="--", lw=2, label="5 mm")
    ax.set_yscale("log")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("error (mm)")
    _panel_title(ax, "Position error vs time")
    ax.legend(loc="upper right")

    ax = fig.add_subplot(gs[0, 1])
    qd = log.array("qd_act")
    for j in range(6):
        ax.plot(t, qd[:, j], lw=2.0, label=JOINT_NAMES[j])
    vlim = float(cfg.velocity_limit().max())
    ax.axhline(vlim, color=C_DARK, ls="--", lw=2.2)
    ax.axhline(-vlim, color=C_DARK, ls="--", lw=2.2, label=f"limit +/-{vlim:.1f}")
    ax.set_ylim(-1.18 * vlim, 1.18 * vlim)
    ax.set_xlabel("time (s)")
    ax.set_ylabel("joint speed (rad/s)")
    _panel_title(ax, "Joint velocities vs the box")
    ax.legend(ncol=2, loc="lower right", fontsize=11, framealpha=0.95)

    ax = fig.add_subplot(gs[1, 0])
    ax.plot(ee[:, 0], ee[:, 1], color=C_LINE, label="MPC path")
    ax.plot(ee[0, 0], ee[0, 1], "o", ms=12, color=C_DARK, label="start")
    ax.plot(goal_p[0], goal_p[1], "*", ms=22, color=C_OK, label="goal")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal", adjustable="datalim")
    _panel_title(ax, "End-effector path, top view")
    ax.legend(loc="upper right", fontsize=12)

    ax = fig.add_subplot(gs[1, 1])
    warm = solve[1:]
    ax.plot(t[1:], warm, color=C_WARN, marker="o", ms=4.5, lw=1.8,
            label="solve time (warm start)")
    ax.axhline(1e3 * cfg.dt, color=C_ALT, ls="--", lw=2.2,
               label=f"dt = {1e3 * cfg.dt:.0f} ms")
    ax.set_ylim(0, 1.30 * max(1e3 * cfg.dt, float(np.max(warm))))
    ax.set_xlabel("time (s)")
    ax.set_ylabel("solve time (ms)")
    _panel_title(ax, "IPOPT solve time per control step")
    ax.legend(loc="center right", framealpha=0.95, fontsize=12)
    k1 = max(1, int(round(1.0 / cfg.dt)))
    steady = solve[k1:] if len(solve) > k1 else solve
    ax.text(0.02, 0.98,
            f"cold first solve {solve[0]:.0f} ms\n"
            f"after 1 s: mean {np.mean(steady):.1f} ms, max {np.max(steady):.1f} ms",
            transform=ax.transAxes, fontsize=12.5, va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="#c3c9d4", alpha=0.95))

    fig.suptitle(title, y=0.995)
    return _save(fig, path)


def plot_track(log: RunLog, cfg: MPCConfig, path: str) -> str:
    _apply_style()
    fig = plt.figure(figsize=(16, 9))
    gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.46, wspace=0.24)
    t = log.array("t")
    ee = log.array("ee_p")
    gp = log.array("goal_p")
    err = log.array("err_p")

    ax = fig.add_subplot(gs[0, 0])
    ax.plot(gp[:, 0], gp[:, 1], color=C_OK, lw=3.4, label="goal")
    ax.plot(ee[:, 0], ee[:, 1], color=C_LINE, lw=2.0, label="flange")
    ax.plot(ee[0, 0], ee[0, 1], "o", ms=11, color=C_DARK)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal", adjustable="datalim")
    _panel_title(ax, "Circular goal, top view (1 period)")
    ax.legend(loc="upper right", fontsize=12)

    ax = fig.add_subplot(gs[0, 1])
    ax.plot(t, 1e3 * err, color=C_LINE, label="position (mm)")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("position error (mm)")
    _panel_title(ax, f"Tracking error, mean {np.mean(1e3 * err):.1f} mm")
    ax2 = ax.twinx()
    ax2.plot(t, 1e3 * log.array("err_ang"), color=C_ALT, lw=1.8, ls=":",
             label="orientation (mrad)")
    ax2.set_ylabel("orientation error (mrad)")
    ax2.grid(False)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="center right", framealpha=0.95, fontsize=12)

    ax = fig.add_subplot(gs[1, 0])
    k0 = int(np.searchsorted(t, 2.0))  # skip the initial approach transient
    ex = 1e3 * (ee[:, 0] - gp[:, 0])
    ey = 1e3 * (ee[:, 1] - gp[:, 1])
    ax.plot(t[k0:], ex[k0:], color=C_LINE, lw=2.4, label="x error")
    ax.plot(t[k0:], ey[k0:], color=C_WARN, lw=2.4, label="y error")
    ax.axhline(0, color=C_DARK, lw=0.9)
    ax.set_xlabel("time (s)")
    ax.set_ylabel("error component (mm)")
    _panel_title(ax, "Error components after 2 s")
    ax.legend(ncol=2, loc="upper right", fontsize=11.5)

    ax = fig.add_subplot(gs[1, 1])
    qd = log.array("qd_act")
    for j in range(6):
        ax.plot(t, qd[:, j], lw=1.9, label=JOINT_NAMES[j])
    vlim = float(cfg.velocity_limit().max())
    ax.axhline(vlim, color=C_DARK, ls="--", lw=2.2, label=f"limit +/-{vlim:.1f}")
    ax.axhline(-vlim, color=C_DARK, ls="--", lw=2.2)
    ax.set_ylim(-1.15 * vlim, 1.15 * vlim)
    ax.set_xlabel("time (s)")
    ax.set_ylabel("joint speed (rad/s)")
    _panel_title(ax, "Joint velocities vs the box")
    ax.legend(ncol=2, fontsize=11, loc="lower right", framealpha=0.95)

    fig.suptitle("Scenario 2 - tracking a moving goal on a circle", y=0.995)
    return _save(fig, path)


def plot_constraint(log: RunLog, cfg: MPCConfig, path: str) -> str:
    _apply_style()
    fig = plt.figure(figsize=(16, 9))
    gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.46, wspace=0.26)
    t = log.array("t")
    ee = log.array("ee_p")
    centre = np.asarray(cfg.obstacle_center, float)
    rad = float(cfg.obstacle_radius)
    rad_safe = rad + float(cfg.obstacle_margin)
    goal_p = log.array("goal_p")[0]

    ax = fig.add_subplot(gs[0, 0])
    ax.add_patch(MplCircle(centre[:2], rad_safe, color=C_ALT, alpha=0.14, lw=0))
    ax.add_patch(MplCircle(centre[:2], rad, color=C_ALT, alpha=0.30, lw=0,
                           label="obstacle (r + 20 mm)"))
    ax.plot(ee[:, 0], ee[:, 1], color=C_LINE, lw=2.6, label="flange path", zorder=5)
    ax.plot([ee[0, 0], goal_p[0]], [ee[0, 1], goal_p[1]], color=C_DARK, ls=":",
            lw=2, label="straight line", zorder=4)
    ax.plot(ee[0, 0], ee[0, 1], "o", ms=11, color=C_DARK, zorder=6)
    ax.plot(goal_p[0], goal_p[1], "*", ms=22, color=C_OK, zorder=7)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal", adjustable="datalim")
    _panel_title(ax, "Top view: around the sphere")
    ax.legend(loc="upper left", fontsize=11, framealpha=0.95)

    ax = fig.add_subplot(gs[0, 1])
    if cfg.floor_z is not None:
        ax.axhspan(-0.05, cfg.floor_z, color=C_WARN, alpha=0.10, lw=0)
        ax.axhline(cfg.floor_z, color=C_WARN, ls="--", lw=2.6,
                   label=f"table z = {cfg.floor_z:.2f} m")
    ax.add_patch(MplCircle((centre[0], centre[2]), rad_safe, color=C_ALT, alpha=0.14, lw=0))
    ax.add_patch(MplCircle((centre[0], centre[2]), rad, color=C_ALT, alpha=0.30, lw=0))
    ax.plot(ee[:, 0], ee[:, 2], color=C_LINE, lw=2.6, zorder=5)
    ax.plot(ee[0, 0], ee[0, 2], "o", ms=11, color=C_DARK, zorder=6)
    ax.plot(goal_p[0], goal_p[2], "*", ms=22, color=C_OK, zorder=7)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("z (m)")
    ax.set_aspect("equal", adjustable="datalim")
    _panel_title(ax, "Side view (x-z): detour is in y")
    ax.legend(loc="upper right", fontsize=12, framealpha=0.95)
    ax.text(0.02, 0.03, "the detour is in y (top view)", transform=ax.transAxes,
            fontsize=12, color="#5a6270")

    ax = fig.add_subplot(gs[1, 0])
    d = log.dist_to_obstacle(centre, rad, "flange")
    tf = np.linspace(0, t[-1], len(d))
    ax.plot(tf, 1e3 * d, color=C_LINE, lw=2.6, label="flange")
    de = log.dist_to_obstacle(centre, rad, "elbow")
    ax.plot(tf, 1e3 * de, color="#9467bd", lw=1.9, ls="--", label="elbow")
    ax.axhline(1e3 * cfg.obstacle_margin, color=C_ALT, ls="--", lw=2.6,
               label=f"constraint ({1e3 * cfg.obstacle_margin:.0f} mm)")
    ax.set_ylim(min(-4.0, 1e3 * min(d.min(), de.min()) - 8),
                1.20 * 1e3 * float(max(d.max(), de.max())))
    ax.set_xlabel("time (s)")
    ax.set_ylabel("clearance (mm)")
    _panel_title(ax, "Distance to the sphere")
    ax.legend(loc="upper right", fontsize=11.5, framealpha=0.95)

    ax = fig.add_subplot(gs[1, 1])
    for pt, col, ls in (("flange", C_LINE, "-"), ("elbow", "#9467bd", "--"),
                        ("wrist3", "#8c564b", ":")):
        ax.plot(t, log.point_height(pt), color=col, lw=2.2, ls=ls, label=pt)
    if cfg.floor_z is not None:
        ax.axhline(cfg.floor_z, color=C_WARN, ls="--", lw=2.6, label="table")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("height z (m)")
    _panel_title(ax, "Link heights vs the table")
    ax.legend(loc="upper right", fontsize=12, framealpha=0.95)

    fig.suptitle("Scenario 3 - hard obstacle and table constraints", y=0.995)
    return _save(fig, path)


def plot_horizon(rows: list[dict[str, Any]], path: str) -> str:
    _apply_style()
    fig = plt.figure(figsize=(16, 9))
    gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.50, wspace=0.28)
    tasks = list(dict.fromkeys(r["task"] for r in rows))
    colors = {"free reach": C_LINE, "constrained reach": C_ALT}
    Ns = sorted({r["N"] for r in rows})

    def series(task, key):
        pts = [r for r in rows if r["task"] == task]
        return [r["N"] for r in pts], [r[key] for r in pts]

    ax = fig.add_subplot(gs[0, 0])
    for task in tasks:
        n, v = series(task, "time_to_within_5mm_s")
        v = [np.nan if x is None else x for x in v]
        ax.plot(n, v, "o-", ms=10, color=colors.get(task, C_OK), label=task)
    ax.set_xlabel("horizon N (stages)")
    ax.set_ylabel("time to 5 mm (s)")
    ax.set_xticks(Ns)
    _panel_title(ax, "Settling time vs horizon")
    ax.legend(loc="lower right")

    ax = fig.add_subplot(gs[0, 1])
    for task in tasks:
        n, v = series(task, "max_cmd_delta_rad_s")
        ax.plot(n, v, "o-", ms=10, color=colors.get(task, C_OK), label=task)
    ax.set_xlabel("horizon N (stages)")
    ax.set_ylabel("max |dqdot| per step (rad/s)")
    ax.set_xticks(Ns)
    _panel_title(ax, "Command smoothness vs horizon")
    ax.legend(loc="upper right")

    ax = fig.add_subplot(gs[1, 0])
    for task in tasks:
        n, v = series(task, "solve_ms_mean")
        ax.plot(n, v, "o-", ms=9, color=colors.get(task, C_OK), label=f"{task}, mean")
        n, v = series(task, "solve_ms_max_warm")
        ax.plot(n, v, "s--", ms=7, color=colors.get(task, C_OK), alpha=0.7,
                label=f"{task}, max")
    dt_ms = 1e3 * rows[0]["dt_s"]
    ax.axhline(dt_ms, color=C_DARK, ls=":", lw=2.6, label=f"dt = {dt_ms:.0f} ms")
    ax.set_xlabel("horizon N (stages)")
    ax.set_ylabel("solve time (ms)")
    ax.set_yscale("log")
    ax.set_xticks(Ns)
    _panel_title(ax, "Solve time vs horizon")
    ax.legend(loc="upper left", fontsize=10.5, ncol=2)

    ax = fig.add_subplot(gs[1, 1])
    ax.axis("off")
    cells = [["task", "N", "err", "settle", "mean", "max", ">dt"]]
    for r in rows:
        settle = r["time_to_within_5mm_s"]
        cells.append([
            "free" if r["task"] == "free reach" else "constr.",
            f"{r['N']}",
            f"{1e3 * r['final_pos_err_m']:.2f}",
            "-" if not np.isfinite(settle) else f"{settle:.2f}",
            f"{r['solve_ms_mean']:.1f}",
            f"{r['solve_ms_max_warm']:.1f}",
            f"{100 * r['frac_steps_over_dt']:.0f}%",
        ])
    tab = ax.table(cellText=cells[1:], colLabels=cells[0], loc="center", cellLoc="center",
                   bbox=[0.02, 0.16, 0.96, 0.72])
    tab.auto_set_font_size(False)
    tab.set_fontsize(12.5)
    for (row, col), cell in tab.get_celld().items():
        cell.set_edgecolor("#b7bec9")
        if row == 0:
            cell.set_facecolor("#e8ecf4")
            cell.set_text_props(weight="bold")
    ax.set_title("Horizon study, dt = 50 ms", loc="left", fontsize=20, pad=2)
    ax.text(0.5, 0.04, "err: final position error (mm), 0.00 = numerical zero  |  "
                       "settle: time to 5 mm (s)\n"
                       "mean/max: IPOPT solve (ms)  |  >dt: fraction of steps slower than dt",
            transform=ax.transAxes, ha="center", va="bottom", fontsize=11.5, color="#5a6270")

    fig.suptitle("Scenario 4 - horizon length: behaviour vs real-time cost", y=0.995)
    return _save(fig, path)


def plot_summary(runs: dict[str, dict[str, Any]], path: str) -> str:
    _apply_style()
    fig = plt.figure(figsize=(16, 9))
    ax = fig.add_subplot(111)
    ax.axis("off")
    keys = [
        ("final_pos_err_m", "final position error (mm)", 1e3, "{:.2f}"),
        ("final_ang_err_rad", "final orientation error (mrad)", 1e3, "{:.2f}"),
        ("time_to_within_5mm_s", "time to within 5 mm (s)", 1.0, "{:.2f}"),
        ("steady_state_track_err_m", "steady tracking error (mm)", 1e3, "{:.1f}"),
        ("peak_joint_speed_rad_s", "peak joint speed (rad/s)", 1.0, "{:.2f}"),
        ("path_length_m", "path length (m)", 1.0, "{:.3f}"),
        ("min_obstacle_clearance_m", "min obstacle clearance (mm)", 1e3, "{:.1f}"),
        ("min_floor_clearance_m", "min table clearance (mm)", 1e3, "{:.1f}"),
        ("solve_ms_mean", "mean solve time (ms)", 1.0, "{:.1f}"),
        ("solve_ms_max_warm", "max warm solve (ms)", 1.0, "{:.1f}"),
        ("solve_ms_mean_after_1s", "solve after 1 s (ms)", 1.0, "{:.1f}"),
        ("frac_steps_over_dt", "steps over dt (fraction)", 1.0, "{:.2f}"),
    ]
    names = list(runs.keys())
    cells = []
    for k, label, scale, fmt in keys:
        row = [label]
        for n in names:
            v = runs[n].get(k)
            if v is None or (isinstance(v, float) and not np.isfinite(v)):
                row.append("n/a")
            else:
                txt = fmt.format(scale * v)
                # "-0.0" reads as an error; a clearance at the constraint is 0.
                if txt.startswith("-0.0") and abs(scale * v) < 0.05:
                    txt = fmt.format(0.0)
                row.append(txt)
        cells.append(row)
    tab = ax.table(cellText=cells, colLabels=["metric"] + names, loc="center",
                   cellLoc="left", colWidths=[0.31] + [0.15] * len(names))
    tab.auto_set_font_size(False)
    tab.set_fontsize(13)
    tab.scale(1.0, 1.95)
    for (row, col), cell in tab.get_celld().items():
        cell.set_edgecolor("#b7bec9")
        if col == 0:
            cell.set_text_props(weight="semibold")
        if row == 0:
            cell.set_facecolor("#e8ecf4")
            cell.set_text_props(weight="bold")
    ax.set_title("Kinematic MPC - metrics at a glance", loc="left", fontsize=27, pad=18)
    ax.text(0.5, 0.02,
            "UR5e, CasADi/IPOPT, N=20, dt=50 ms, ideal integrator plant except the "
            "'lag' column (tau=80 ms)",
            transform=ax.transAxes, ha="center", fontsize=13, color="#5a6270")
    return _save(fig, path)


# --------------------------------------------------------------------------------------
# 8. Offline scenarios
# --------------------------------------------------------------------------------------


def scenario_reach(out_dir: str, cfg: MPCConfig, n_steps: int = 160) -> tuple[RunLog, dict]:
    """S1: from home to a fixed Cartesian goal (position + orientation)."""
    os.makedirs(out_dir, exist_ok=True)
    goal = pose_of(REACH_GOAL_Q)
    mpc = KinematicMPC(cfg)
    log = mpc.simulate(HOME_Q, goal, n_steps, plant="ideal")
    plot_reach(log, cfg, os.path.join(out_dir, "mpc-01-reach.png"))
    log.write_csv(os.path.join(out_dir, "mpc-01-reach.csv"))
    m = log.metrics()
    # Concrete reading of the weights: what the two tracking terms cost at t=0.
    m["cost_terms_at_home"] = mpc.info_cost_terms(HOME_Q, goal)
    return log, m


def scenario_track(out_dir: str, cfg: MPCConfig, n_steps: int = 360) -> tuple[RunLog, dict]:
    """S2: follow a circular goal trajectory (a moving target over the horizon).

    Radius 0.10 m, period 18 s -> 3.5 cm/s along the circle, so the arm stays well
    inside its velocity box and the plot shows *tracking quality*, not saturation.
    """
    os.makedirs(out_dir, exist_ok=True)
    p_home, R_home = pose_of(HOME_Q)
    goal = circular_goal(centre=p_home, radius=0.10, period=18.0, R_fixed=R_home)
    mpc = KinematicMPC(cfg)
    log = mpc.simulate(HOME_Q, goal, n_steps, plant="ideal")
    plot_track(log, cfg, os.path.join(out_dir, "mpc-02-circular-goal.png"))
    log.write_csv(os.path.join(out_dir, "mpc-02-circular-goal.csv"))
    m = log.metrics()
    # For a moving goal "time to within 5 mm" is not the interesting number; keep
    # the steady-state tracking error of the last third of the run instead. It is
    # not zero on purpose: the cost trades tracking error against ||qdot||^2, so a
    # target moving at 3.5 cm/s is followed with a small, constant lag -- exactly
    # the steady-state error a P controller has on a ramp. A quadrotor MPC gets rid
    # of it by putting the reference *velocity* in the cost; this cost does not.
    tail = int(0.66 * log.n)
    m["steady_state_track_err_m"] = float(np.mean(log.array("err_p")[tail:]))
    m["steady_state_ang_err_rad"] = float(np.mean(log.array("err_ang")[tail:]))
    return log, m


CONSTRAINED_FLOOR_Z = 0.24
CONSTRAINED_OBSTACLE_RADIUS = 0.085
CONSTRAINED_OBSTACLE_MARGIN = 0.02


def constrained_cfg(cfg: MPCConfig, soft: bool = False) -> MPCConfig:
    """The S3 configuration: sphere on the direct path, table plane under it."""
    p_home = ur5e_fk_np(HOME_Q)["p"]
    p_goal = ur5e_fk_np(CONSTRAINT_GOAL_Q)["p"]
    return replace(
        cfg,
        obstacle_center=0.5 * (p_home + p_goal),
        obstacle_radius=CONSTRAINED_OBSTACLE_RADIUS,
        obstacle_margin=CONSTRAINED_OBSTACLE_MARGIN,
        obstacle_soft=soft,
        obstacle_points=("flange", "elbow"),
        floor_z=CONSTRAINED_FLOOR_Z,
        floor_points=("flange", "elbow"),
    )


def scenario_constraint(out_dir: str, cfg: MPCConfig, n_steps: int = 200,
                        soft: bool = False) -> tuple[RunLog, dict]:
    """S3: straight line to the goal would go through a sphere and under the table."""
    os.makedirs(out_dir, exist_ok=True)
    cfg = constrained_cfg(cfg, soft=soft)
    mpc = KinematicMPC(cfg)
    log = mpc.simulate(HOME_Q, pose_of(CONSTRAINT_GOAL_Q), n_steps, plant="ideal")
    plot_constraint(log, cfg, os.path.join(out_dir, "mpc-03-constraints.png"))
    log.write_csv(os.path.join(out_dir, "mpc-03-constraints.csv"))
    m = log.metrics()
    m["obstacle_center"] = np.asarray(cfg.obstacle_center).tolist()
    m["obstacle_radius_m"] = float(cfg.obstacle_radius)
    return log, m


def scenario_lag(out_dir: str, cfg: MPCConfig, n_steps: int = 160,
                 tau: float = 0.08) -> tuple[RunLog, dict]:
    """S1 again, but the inner servo is a first-order lag instead of ideal."""
    os.makedirs(out_dir, exist_ok=True)
    goal = pose_of(REACH_GOAL_Q)
    mpc = KinematicMPC(cfg)
    log = mpc.simulate(HOME_Q, goal, n_steps, plant="lag", tau=tau)
    plot_reach(log, cfg, os.path.join(out_dir, "mpc-05-first-order-lag.png"),
               title=f"Scenario 1 with an inner-servo lag (tau = {1e3 * tau:.0f} ms)")
    log.write_csv(os.path.join(out_dir, "mpc-05-first-order-lag.csv"))
    m = log.metrics()
    m["tau_s"] = tau
    return log, m


HORIZON_TASKS = (
    ("free reach", "reach"),
    ("constrained reach", "constrained"),
)


def scenario_horizon(out_dir: str, cfg: MPCConfig, Ns: Iterable[int] = (5, 10, 20, 40),
                     n_steps: int = 160) -> tuple[list[dict], list[RunLog]]:
    """S4: horizon length vs accuracy and real-time cost, on two tasks.

    Both tasks are receding-horizon solvers of the same OCP; only N changes. The
    free reach task is intentionally the "easy" one: an integrator plant with an
    exactly known goal is nearly horizon-independent, so the interesting part of
    this table is the solve cost. The constrained task shows the small but real
    benefit of lookahead (it settles earlier and to a smaller final error) because
    the plan has to respect the sphere/table before it commits.
    """
    os.makedirs(out_dir, exist_ok=True)
    rows, logs = [], []
    for label, kind in HORIZON_TASKS:
        for N in Ns:
            if kind == "reach":
                c = replace(cfg, N=int(N))
                goal = pose_of(REACH_GOAL_Q)
            else:
                c = constrained_cfg(replace(cfg, N=int(N)))
                goal = pose_of(CONSTRAINT_GOAL_Q)
            mpc = KinematicMPC(c)
            log = mpc.simulate(HOME_Q, goal, n_steps, plant="ideal")
            m = log.metrics()
            m["task"] = label
            m["N"] = int(N)
            m["dt_s"] = c.dt
            rows.append(m)
            logs.append(log)
            settle = m["time_to_within_5mm_s"]
            print(f"  {label:18s} N={N:3d}  final={1e3 * m['final_pos_err_m']:7.2f} mm  "
                  f"settle={'  n/a' if not np.isfinite(settle) else f'{settle:5.2f}'} s  "
                  f"mean={m['solve_ms_mean']:6.1f} ms  max_warm={m['solve_ms_max_warm']:6.1f} ms  "
                  f"over_dt={100 * m['frac_steps_over_dt']:3.0f}%")
    plot_horizon(rows, os.path.join(out_dir, "mpc-04-horizon-study.png"))
    with open(os.path.join(out_dir, "mpc-04-horizon-study.csv"), "w", newline="") as fh:
        keys = ["task", "N", "dt_s", "final_pos_err_m", "time_to_within_5mm_s",
                "solve_ms_mean", "solve_ms_max", "solve_ms_max_warm", "solve_ms_p95",
                "frac_steps_over_dt", "peak_joint_speed_rad_s", "path_length_m", "steps"]
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in keys})
    return rows, logs


def run_offline(out_dir: str, base_cfg: MPCConfig, quick: bool = False,
                soft_obstacle: bool = False, tau: float = 0.08) -> dict[str, Any]:
    """Run every offline scenario, write PNGs + CSV + summary.json."""
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[mpc] offline run -> {out_dir}")

    steps = 90 if quick else 160
    results: dict[str, Any] = {}
    summary_runs: dict[str, dict[str, Any]] = {}

    print("[mpc] S1 reach ...")
    _, m1 = scenario_reach(out_dir, base_cfg, n_steps=steps)
    results["reach"] = m1
    summary_runs["reach"] = m1
    print(f"      final err {1e3 * m1['final_pos_err_m']:.2f} mm, "
          f"settle {m1['time_to_within_5mm_s']:.2f} s, "
          f"mean solve {m1['solve_ms_mean']:.1f} ms")

    print("[mpc] S2 circular goal ...")
    _, m2 = scenario_track(out_dir, base_cfg, n_steps=100 if quick else 360)
    results["circular_goal"] = m2
    summary_runs["circular"] = m2
    print(f"      steady-state tracking error "
          f"{1e3 * m2['steady_state_track_err_m']:.1f} mm")

    print("[mpc] S3 constraints ...")
    _, m3 = scenario_constraint(out_dir, base_cfg, n_steps=steps, soft=soft_obstacle)
    results["constraints"] = m3
    summary_runs["constraints"] = m3
    print(f"      final err {1e3 * m3['final_pos_err_m']:.2f} mm, "
          f"obstacle clearance {1e3 * m3['min_obstacle_clearance_m']:.1f} mm, "
          f"table clearance {1e3 * m3['min_floor_clearance_m']:.1f} mm, "
          f"path {m3['path_length_m']:.3f} m")

    print("[mpc] S1 with first-order lag plant (tau=%.0f ms) ..." % (1e3 * tau))
    _, m5 = scenario_lag(out_dir, base_cfg, n_steps=steps, tau=tau)
    results["lag"] = m5
    summary_runs["lag"] = m5
    print(f"      final err {1e3 * m5['final_pos_err_m']:.2f} mm, "
          f"settle {m5['time_to_within_5mm_s']:.2f} s, path {m5['path_length_m']:.3f} m")

    print("[mpc] S4 horizon study ...")
    Ns = (5, 20) if quick else (5, 10, 20, 40)
    rows, _ = scenario_horizon(out_dir, base_cfg, Ns=Ns, n_steps=steps)
    results["horizon_study"] = rows

    plot_summary(summary_runs, os.path.join(out_dir, "mpc-06-summary.png"))

    payload = {
        "generated_utc": stamp,
        "kind": "kinematic-mpc-offline",
        # Solve-time numbers are only meaningful next to the machine conditions.
        "host": _host_info(),
        "config": base_cfg.describe(),
        "home_q": HOME_Q.tolist(),
        "reach_goal_q": REACH_GOAL_Q.tolist(),
        "constraint_goal_q": CONSTRAINT_GOAL_Q.tolist(),
        "dh": {
            "d": UR5E_DH_D.tolist(),
            "a": UR5E_DH_A.tolist(),
            "alpha": UR5E_DH_ALPHA.tolist(),
            "source": "Universal Robots, DH parameters for the UR5e (standard DH)",
        },
        "runs": results,
        "artifacts": sorted(
            f for f in os.listdir(out_dir) if f.startswith("mpc-")
        ),
    }
    with open(os.path.join(out_dir, "summary.json"), "w") as fh:
        json.dump(_jsonable(payload), fh, indent=2)
    return payload


def _host_info() -> dict[str, Any]:
    """Enough provenance to judge the solve-time numbers."""
    info: dict[str, Any] = {}
    try:
        info["hostname"] = os.uname().nodename
        info["cpus"] = os.cpu_count()
        info["loadavg"] = list(os.getloadavg())
        info["python"] = sys.version.split()[0]
        info["numpy"] = np.__version__
        info["casadi"] = ca.__version__ if HAS_CASADI else None
    except Exception:  # pragma: no cover
        pass
    return info


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return _jsonable(obj.tolist())
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return v if np.isfinite(v) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, (bool, int, str)) or obj is None:
        return obj
    return str(obj)


# --------------------------------------------------------------------------------------
# 9. Self-tests
# --------------------------------------------------------------------------------------


class _Checks:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        if cond:
            self.passed += 1
            print(f"  PASS  {name}" + (f"   [{detail}]" if detail else ""))
        else:
            self.failed.append(name)
            print(f"  FAIL  {name}" + (f"   [{detail}]" if detail else ""))

    def report(self) -> int:
        print()
        if self.failed:
            print(f"self-test: {self.passed} passed, {len(self.failed)} FAILED")
            for f in self.failed:
                print(f"  - {f}")
            return 1
        print(f"self-test: {self.passed} checks passed")
        return 0


def selftest() -> int:
    ck = _Checks()
    print("=" * 78)
    print("UR5e kinematic MPC self-test")
    print("=" * 78)

    # -- 1. FK vs an independent model ----------------------------------------
    print("\n[1] forward kinematics")
    p_np = ur5e_fk_np(TEXTBOOK_Q)["p"]

    # Independent path #1: explicit 2D derivation for this pose (the arm is
    # straight up, so the flange is at x=0, y=-(d4+d6), z=d1+|a2|+|a3|+d5).
    p_text = np.array([0.0,
                       -(UR5E_DH_D[3] + UR5E_DH_D[5]),
                       UR5E_DH_D[0] + abs(UR5E_DH_A[1]) + abs(UR5E_DH_A[2]) + UR5E_DH_D[4]])
    ck.check("numpy FK matches the textbook pose",
             np.allclose(p_np, p_text, atol=1e-12),
             f"p={np.round(p_np, 6)} vs {np.round(p_text, 6)}")

    # Independent path #2: compose the six homogeneous transforms from scratch
    # with a different formulation (rotations built from axis-angle products),
    # not by reusing dh_transform_np.
    def _rotz(t):
        c, s = np.cos(t), np.sin(t)
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])

    def _rotx(t):
        c, s = np.cos(t), np.sin(t)
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])

    R_acc = np.eye(3)
    p_acc = np.zeros(3)
    for i in range(6):
        # standard DH: translate along the *previous* z by d, then along the new
        # x by a, then rotate. Written as an explicit product of R and p.
        p_acc = p_acc + R_acc @ np.array([0.0, 0.0, UR5E_DH_D[i]])
        R_acc = R_acc @ _rotz(TEXTBOOK_Q[i])
        p_acc = p_acc + R_acc @ np.array([UR5E_DH_A[i], 0.0, 0.0])
        R_acc = R_acc @ _rotx(UR5E_DH_ALPHA[i])
    ck.check("numpy FK matches an independent axis-angle composition",
             np.allclose(p_acc, p_np, atol=1e-12),
             f"p={np.round(p_acc, 6)}")
    ck.check("flange z equals d1+|a2|+|a3|+d5 = 1.0794 m",
             abs(p_np[2] - 1.0794) < 1e-9, f"z={p_np[2]:.6f}")
    ck.check("flange y equals -(d4+d6) = -0.2329 m",
             abs(p_np[1] + 0.2329) < 1e-9, f"y={p_np[1]:.6f}")

    # CasADi FK must agree with numpy FK (exact same table, different code path).
    qs = ca.MX.sym("qs", 6)
    fk_fun = ca.Function("fk", [qs], [_fk_casadi(qs)["T"]])
    for probe in (TEXTBOOK_Q, HOME_Q, REACH_GOAL_Q, np.array([0.3, -0.4, 1.9, -2.2, 0.7, -1.1])):
        T_ca = np.array(fk_fun(probe))
        ck.check(f"CasADi FK == numpy FK at {np.round(probe, 2)}",
                 np.allclose(T_ca, ur5e_fk_np(probe)["T"], atol=1e-12),
                 f"max|dT|={np.max(np.abs(T_ca - ur5e_fk_np(probe)['T'])):.2e}")

    R0 = ur5e_fk_np(HOME_Q)["R"]
    ck.check("rotation error cost is 0 for identical rotations",
             abs(rotation_error_cost(R0, R0)) < 1e-15)
    ck.check("rotation error cost ~ theta^2 for a small rotation",
             abs(rotation_error_cost(R0, R0 @ _rotz(1e-3)) - 1e-6) < 1e-9,
             f"cost={rotation_error_cost(R0, R0 @ _rotz(1e-3)):.3e}")

    # -- 2. convergence -------------------------------------------------------
    print("\n[2] offline convergence (ideal integrator plant)")
    cfg = MPCConfig()
    goal = pose_of(REACH_GOAL_Q)
    mpc = KinematicMPC(cfg)
    log = mpc.simulate(HOME_Q, goal, 160, plant="ideal")
    m = log.metrics()
    ck.check("final position error < 5 mm", m["final_pos_err_m"] < 5e-3,
             f"{1e3 * m['final_pos_err_m']:.2f} mm")
    ck.check("final orientation error < 0.02 rad", m["final_ang_err_rad"] < 0.02,
             f"{m['final_ang_err_rad']:.4f} rad")
    ck.check("no solver fallbacks", m["fallback_steps"] == 0, f"{m['fallback_steps']} steps")
    ck.check("reached within 5 mm at some point", np.isfinite(m["time_to_within_5mm_s"]),
             f"t={m['time_to_within_5mm_s']} s")

    # -- 3. constraints along the executed trajectory -------------------------
    print("\n[3] constraints along the trajectory")
    ck.check("joint velocities <= limit",
             np.max(np.abs(log.array("qd_act"))) <= cfg.velocity_limit().max() + 1e-9,
             f"peak {np.max(np.abs(log.array('qd_act'))):.4f} <= {cfg.velocity_limit().max():.2f} rad/s")
    ck.check("joint positions inside the box",
             np.all(log.array("q") >= cfg.q_min - 1e-9) and np.all(log.array("q") <= cfg.q_max + 1e-9))
    if cfg.floor_z is not None:
        h = np.concatenate([log.point_height(pt) for pt in cfg.floor_points])
        ck.check("floor satisfied (flange/elbow above z_min)",
                 np.all(h >= cfg.floor_z - 1e-6),
                 f"min z={h.min():.4f} >= {cfg.floor_z}")

    # -- 4. obstacle scenario -------------------------------------------------
    print("\n[4] obstacle + floor scenario")
    tmp = tempfile.mkdtemp(prefix="mpc_selftest_")
    log3, m3 = scenario_constraint(tmp, cfg, n_steps=200)
    print(f"  (scenario artifacts in {tmp})")
    ck.check("constraint run converges", m3["final_pos_err_m"] < 5e-3,
             f"{1e3 * m3['final_pos_err_m']:.2f} mm")
    ck.check("obstacle clearance >= 0 along the path",
             m3["min_obstacle_clearance_m"] >= -1e-6,
             f"{1e3 * m3['min_obstacle_clearance_m']:.2f} mm")
    ck.check("floor clearance >= 0 along the path",
             m3["min_floor_clearance_m"] >= -1e-6,
             f"{1e3 * m3['min_floor_clearance_m']:.2f} mm")
    ck.check("the obstacle actually constrains the path (clearance < 5 cm somewhere)",
             m3["min_obstacle_clearance_m"] < 0.05,
             f"{1e3 * m3['min_obstacle_clearance_m']:.1f} mm")

    # -- 5. moving goal + API shapes -----------------------------------------
    print("\n[5] moving goal API")
    p_home, R_home = pose_of(HOME_Q)
    g = circular_goal(p_home, 0.05, 20.0, R_home)
    mpc2 = KinematicMPC(replace(cfg, N=10))
    qd, info = mpc2.step(HOME_Q, g, qdot_prev=np.zeros(6), t=0.0)
    ck.check("step() returns a 6-vector velocity", qd.shape == (6,),
             f"|qdot|={np.linalg.norm(qd):.4f}")
    ck.check("info carries the predicted EE path", info["predicted_ee_path"].shape == (11, 3))
    for key in ("cost", "solve_ms", "status", "predicted_ee_path"):
        ck.check(f"info['{key}'] present", key in info)
    log2 = mpc2.simulate(HOME_Q, g, 40, plant="lag", tau=0.08)
    ck.check("lag plant run produces 40 rows", log2.n == 40)
    ck.check("lag plant velocity never exceeds the box beyond tolerance",
             np.max(np.abs(log2.array("qd_act"))) <= cfg.velocity_limit().max() * 1.25,
             f"peak {np.max(np.abs(log2.array('qd_act'))):.3f}")

    # -- 6. robustness: infeasible hard problem -> relaxation ----------------
    print("\n[6] robustness: obstacle appears around the current pose")
    # The hard constraint is violated AT q0, so no hard-feasible plan exists.
    # The controller must relax (soft obstacle) instead of stalling.
    p_home_now = ur5e_fk_np(HOME_Q)["p"]
    c6 = replace(MPCConfig(), N=12, obstacle_center=p_home_now.copy(),
                 obstacle_radius=0.10, obstacle_margin=0.02,
                 obstacle_points=("flange",), floor_points=("elbow",), floor_z=0.20)
    mpc6 = KinematicMPC(c6)
    log6 = mpc6.simulate(HOME_Q, pose_of(REACH_GOAL_Q), 80, plant="ideal")
    m6 = log6.metrics()
    far = float(np.linalg.norm(log6.array("ee_p")[-1] - p_home_now))
    ck.check("an infeasible hard problem is relaxed, not abandoned",
             m6["relaxed_steps"] > 0, f"{m6['relaxed_steps']} relaxed steps")
    ck.check("no step falls back to holding still", m6["fallback_steps"] == 0,
             f"{m6['fallback_steps']} fallbacks")
    ck.check("the arm escapes the sphere it started inside", far > 0.12,
             f"{far:.3f} m from the sphere centre")

    # -- 7. goal forms -------------------------------------------------------
    print("\n[7] goal forms")
    N = 8
    mpc7 = KinematicMPC(replace(MPCConfig(), N=N))
    p7, R7 = pose_of(REACH_GOAL_Q)
    P_seq = np.stack([p7] * (N + 1))
    for label, g in (
        ("tuple (p, R)", (p7, R7)),
        ("dict", {"p": p7, "R": R7}),
        ("callable t -> (p, R)", lambda t: (p7, R7)),
        ("per-stage list", [(p7, R7)] * (N + 1)),
        ("(N+1,3) positions + one R", (P_seq, R7)),
    ):
        try:
            qd7, _ = mpc7.step(HOME_Q, g, qdot_prev=np.zeros(6), t=0.0)
            ok7 = qd7.shape == (6,) and np.all(np.isfinite(qd7))
        except Exception as exc:  # noqa: BLE001
            ok7 = False
            print(f"      {label}: {exc}")
        ck.check(f"goal form accepted: {label}", ok7)
    ck.check("--real-vel would use the UR5e 180 deg/s limit",
             abs(UR5E_JOINT_SPEED_LIMIT - np.pi) < 1e-12,
             f"{UR5E_JOINT_SPEED_LIMIT:.4f} rad/s")

    # -- 8. solve-time statistics --------------------------------------------
    print("\n[8] solve time")
    solve = log.array("solve_ms")
    print(f"  mean {np.mean(solve):.1f} ms | p95 {np.percentile(solve, 95):.1f} ms | "
          f"max {np.max(solve):.1f} ms | dt {1e3 * cfg.dt:.0f} ms | "
          f"over dt {100 * np.mean(solve > 1e3 * cfg.dt):.0f}%")
    ck.check("mean solve time reported and positive", np.mean(solve) > 0)
    ck.check("warm-started mean solve fits in dt at N=20",
             np.mean(solve) < 1e3 * cfg.dt, f"{np.mean(solve):.1f} ms")

    return ck.report()


# --------------------------------------------------------------------------------------
# 10. On-twin closed loop
# --------------------------------------------------------------------------------------


class TwinTransport:
    """Adapter around `ur5e_common.TwinLink` (imported lazily).

    What the real object is (see its docstring): a ROS 2 node that *publishes*
    `sensor_msgs/JointState` on `/joint_states` (the twin's ROS2SubscribeJointState
    turns that into the articulation's position command) and *subscribes* to the
    same topic for the measured state, dropping its own echo. So:

        link.send(q_cmd) -> publishes a position command
        read(timeout)    -> (stamp, q, qd) of the freshest non-self message

    `ur5e_common` is written by another agent and may not exist yet, so this
    adapter probes the documented method names and fails with a message that
    lists what it tried plus TwinLink's docstring. It deliberately does not
    import rclpy itself: only `TwinLink.__init__` does, so `--offline` never
    needs ROS.

    `port` is accepted for CLI compatibility (the Kit bridge uses TCP ports) but
    the ROS 2 transport does not use it; DDS uses ROS_DOMAIN_ID instead, which
    `domain_id` sets before the node is created.
    """

    SEND_NAMES = ("send", "set_joint_positions", "command_joint_positions",
                  "publish_joint_state")
    READ_NAMES = ("read", "get_joint_positions", "read_joint_positions",
                  "read_joint_state")

    def __init__(self, port: int = 8791, domain_id: int | None = None,
                 verbose: bool = False):
        try:
            import ur5e_common  # noqa: F401  (only the on-twin path needs it)
        except ImportError as exc:
            raise SystemExit(
                "on-twin mode needs scripts/demo/ur5e_common.py (TwinLink). It is not "
                "importable from here:\n"
                f"  {exc}\n"
                "Run it as a script from the repo root (the script's own directory is "
                "then on sys.path), or set PYTHONPATH=scripts/demo. Offline work "
                "(--selftest, --offline) does not need it at all."
            )
        if domain_id is not None:
            os.environ["ROS_DOMAIN_ID"] = str(int(domain_id))
        cls = getattr(ur5e_common, "TwinLink", None)
        if cls is None:
            raise SystemExit(
                "ur5e_common exists but has no TwinLink class. Available names: "
                + ", ".join(n for n in dir(ur5e_common) if not n.startswith("_"))
            )
        self.port = int(port)
        self._cls = cls
        self.link = self._construct(cls, verbose)
        self._send_name = self._first_callable(self.SEND_NAMES)
        self._read_name = self._first_callable(self.READ_NAMES)
        if self._send_name is None or self._read_name is None:
            raise SystemExit(
                "TwinLink is missing one of " + ", ".join(self.SEND_NAMES + self.READ_NAMES)
                + ". TwinLink docstring: " + self._doc()
            )

    def _construct(self, cls, verbose: bool):
        """Instantiate TwinLink across the plausible constructor signatures."""
        attempts = []
        for kwargs in ({"verbose": verbose}, {"port": self.port}, {}):
            try:
                return cls(**kwargs)
            except TypeError as exc:
                attempts.append(f"{kwargs}: {exc}")
        raise SystemExit("could not construct ur5e_common.TwinLink:\n  "
                         + "\n  ".join(attempts))

    def _first_callable(self, names: Sequence[str]) -> str | None:
        for n in names:
            if callable(getattr(self.link, n, None)):
                return n
        return None

    def _doc(self) -> str:
        doc = (getattr(self._cls, "__doc__", "") or "").strip()
        return doc.split("\n\n")[0][:600] if doc else "(no docstring)"

    # -- commands ---------------------------------------------------------------
    def command(self, q: Sequence[float], qd: Sequence[float] | None = None) -> None:
        """Publish one joint position command (and, if supported, the velocity)."""
        q = np.asarray(q, float).reshape(-1)
        fn = getattr(self.link, self._send_name)
        if self._send_name == "send":
            try:
                fn(q, qd)  # TwinLink.send(q_cmd, qd_cmd=None)
                return
            except TypeError:
                fn(q)
                return
        fn(q)

    # -- state ------------------------------------------------------------------
    def read_state(self, timeout: float = 0.0) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Return (q_measured, qd_measured_or_None); (None, None) if nothing fresh."""
        fn = getattr(self.link, self._read_name)
        try:
            raw = fn(timeout=timeout)
        except TypeError:
            raw = fn()
        q, qd = self._interpret(raw)
        if q is None:
            # A polling loop may still have a buffered value from before `timeout`.
            last = getattr(self.link, "last_state", None)
            if last is not None:
                q, qd = self._interpret(last if not callable(last) else last())
        return q, qd

    def _interpret(self, raw: Any) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Accept (stamp, q, qd) / q / {"position": ...} and normalise to 6 joints."""
        if raw is None:
            return None, None
        if isinstance(raw, dict):
            q = raw.get("position", raw.get("positions", raw.get("q")))
            qd = raw.get("velocity", raw.get("velocities", raw.get("qd")))
            return self._as6(q), self._as6(qd, strict=False)
        if isinstance(raw, (tuple, list)) and len(raw) == 3:
            # TwinLink.read() -> (stamp, q, qd); q (and qd) is None on timeout.
            stamp, q, qd = raw
            del stamp
            if q is None:
                return None, None
            return self._as6(q), self._as6(qd, strict=False)
        return self._as6(raw), None

    @staticmethod
    def _as6(v: Any, strict: bool = True) -> np.ndarray | None:
        if v is None:
            return None
        arr = np.asarray(v, dtype=float).reshape(-1)
        if arr.size < 6:
            if strict:
                raise SystemExit(f"twin returned {arr.size} joints, expected 6: {arr}")
            return None
        return arr[:6]  # a gripper joint may trail the six arm joints

    def wait_ready(self, timeout: float = 10.0) -> np.ndarray:
        """Block until the first measured state arrives; return it."""
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            q, _ = self.read_state(timeout=0.25)
            if q is not None:
                return q
        raise SystemExit(
            f"no joint state on /joint_states within {timeout:.0f} s. TwinLink docstring: "
            + self._doc()
            + "\nIs the driver (or the fake driver) publishing? A bare twin only "
              "subscribes; nothing echoes its own commands back.")

    def publisher_count(self) -> int | None:
        fn = getattr(self.link, "publisher_count", None)
        return None if fn is None else int(fn())

    def close(self) -> None:
        fn = getattr(self.link, "close", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass


def run_on_twin(out_dir: str, cfg: MPCConfig, port: int = 8791, rate_hz: float = 20.0,
                steps: int = 200, goal_q: Sequence[float] | None = None,
                max_joint_step: float = 0.02, max_lead_rad: float = 0.25,
                domain_id: int | None = None, verbose: bool = False) -> dict[str, Any]:
    """Close the loop on the twin at `rate_hz`, integrating qdot into a position setpoint.

    Differences from the offline loop, and why:
      * The MPC still runs at 1/dt (20 Hz by default). The *published* setpoint is
        the integrated joint position `q_cmd += qdot*dt`, because the twin is a
        position-mirroring twin: nothing in this stack commands joint velocity.
      * `qdot_prev` for the smoothness term is estimated from the measured joint
        positions, because the velocity we command is not the velocity we get.
      * A per-step cap on the setpoint jump (`max_joint_step`, rad) is a hard
        safety net: if a solve ever returns something wild, the servo sees at most
        this much motion per tick.
      * A cap on the setpoint *lead* (`max_lead_rad`): the integrated command may
        not run further than this ahead of the measured state. Without it, a
        sluggish inner loop lets the command run away and the arm then sweeps
        through the backlog at full speed. This is the kinematic equivalent of the
        position-error window a servo drive enforces.
      * The loop is scheduled against a monotonic clock and the lateness of every
        tick is logged, so "the machine was too slow" cannot hide in the data.
    """
    os.makedirs(out_dir, exist_ok=True)
    transport = TwinTransport(port=port, domain_id=domain_id, verbose=verbose)
    dt = 1.0 / float(rate_hz)
    cfg_dt = replace(cfg, dt=dt) if abs(dt - cfg.dt) > 1e-12 else cfg
    mpc = KinematicMPC(cfg_dt)
    goal_q_arr = np.asarray(REACH_GOAL_Q if goal_q is None else goal_q, float).reshape(6)
    goal = pose_of(goal_q_arr)

    q = transport.wait_ready(timeout=10.0)
    n_pub = transport.publisher_count()
    print(f"[mpc] on-twin: {rate_hz:.1f} Hz, {steps} steps, dt {1e3 * dt:.0f} ms, "
          f"publishers on /joint_states: {n_pub}")
    print(f"[mpc] goal_q = {np.round(goal_q_arr, 3)}  ->  flange {np.round(goal[0], 3)} m")
    q_cmd = q.copy()
    q_prev = q.copy()
    qd_cmd_prev = np.zeros(6)
    log = RunLog(cfg=cfg_dt, plant="twin")
    rows: list[dict[str, Any]] = []
    late = 0
    lead_clipped = 0
    t0 = time.perf_counter()
    try:
        for k in range(int(steps)):
            tick = t0 + k * dt
            q_meas, qd_meas = transport.read_state(timeout=dt)
            if q_meas is None:
                q_meas = q_prev          # keep the last known state this tick
            qd_est = qd_meas if qd_meas is not None else (q_meas - q_prev) / dt
            qd_prev = qd_est if k == 0 else qd_cmd_prev
            qd_cmd, info = mpc.step(q_meas, goal, qdot_prev=qd_prev, t=k * dt)

            # Safety cap on the setpoint increment.
            jump = dt * float(np.max(np.abs(qd_cmd)))
            if jump > max_joint_step and jump > 0:
                qd_cmd = qd_cmd * (max_joint_step / jump)
            q_cmd = q_cmd + dt * qd_cmd
            lead = q_cmd - q_meas
            over = np.abs(lead) > max_lead_rad
            if np.any(over):
                q_cmd = np.where(over, q_meas + np.sign(lead) * max_lead_rad, q_cmd)
                lead_clipped += 1
            transport.command(q_cmd, qd_cmd)

            log.append(k * dt, q_meas, qd_cmd, qd_est, info, goal=goal)
            rows.append({
                "k": k, "wall_s": time.perf_counter() - t0, "t_s": k * dt,
                **{f"q_cmd_{j + 1}": float(q_cmd[j]) for j in range(6)},
                **{f"q_meas_{j + 1}": float(q_meas[j]) for j in range(6)},
                **{f"qd_cmd_{j + 1}": float(qd_cmd[j]) for j in range(6)},
                "servo_err_norm": float(np.linalg.norm(q_cmd - q_meas)),
                "err_p_m": log.rows[-1]["err_p"],
                "err_ang_rad": log.rows[-1]["err_ang"],
                "solve_ms": info["solve_ms"], "status": info["status"],
                "relaxed": int(info["relaxed"]), "fallback": int(info["fallback"]),
            })
            q_prev = q_meas
            qd_cmd_prev = qd_cmd

            sleep = tick + dt - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                late += 1
                if late <= 5 or late % 25 == 0:
                    print(f"      step {k}: late by {-1e3 * sleep:.1f} ms "
                          f"(solve {info['solve_ms']:.1f} ms)")
    except KeyboardInterrupt:
        print("[mpc] interrupted, closing")
    finally:
        transport.close()

    log.write_csv(os.path.join(out_dir, "mpc-on-twin.csv"))
    m = log.metrics()
    m["late_ticks"] = late
    m["lead_clipped_ticks"] = lead_clipped
    m["rate_hz"] = rate_hz
    m["goal_q"] = goal_q_arr.tolist()
    with open(os.path.join(out_dir, "mpc-on-twin-log.json"), "w") as fh:
        json.dump(_jsonable({
            "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "port_ignored_by_ros_transport": port,
            "domain_id": domain_id,
            "rate_hz": rate_hz, "steps": len(rows), "late_ticks": late,
            "lead_clipped_ticks": lead_clipped,
            "config": cfg_dt.describe(), "metrics": m, "rows": rows,
        }), fh, indent=2)
    print(f"[mpc] on-twin done: {len(rows)} steps, final err "
          f"{1e3 * m['final_pos_err_m']:.2f} mm, mean solve {m['solve_ms_mean']:.1f} ms, "
          f"late ticks {late}, lead-clipped {lead_clipped}, "
          f"fallbacks {m['fallback_steps']}, relaxed {m['relaxed_steps']}")
    return m


# --------------------------------------------------------------------------------------
# 11. CLI
# --------------------------------------------------------------------------------------


def _parse_obstacle(vals: Sequence[float]) -> tuple[np.ndarray, float]:
    vals = list(vals)
    if len(vals) != 4:
        raise SystemExit("--obstacle takes CX CY CZ R")
    return np.array(vals[:3], float), float(vals[3])


def build_cfg(args: argparse.Namespace) -> MPCConfig:
    cfg = MPCConfig()
    if args.N is not None:
        cfg.N = int(args.N)
    if args.dt is not None:
        cfg.dt = float(args.dt)
    if args.real_vel:
        cfg.qd_max = UR5E_JOINT_SPEED_LIMIT * np.ones(6)
    elif args.vel is not None:
        cfg.qd_max = float(args.vel) * np.ones(6)
    if args.floor_z is not None:
        cfg.floor_z = None if args.floor_z < 0 else float(args.floor_z)
    if args.obstacle is not None:
        centre, radius = _parse_obstacle(args.obstacle)
        cfg.obstacle_center = centre
        cfg.obstacle_radius = radius
    if args.soft_obstacle:
        cfg.obstacle_soft = True
    return cfg


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Kinematic receding-horizon MPC for the UR5e (offline study + on-twin).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--selftest", action="store_true", help="run the assertions and exit")
    mode.add_argument("--offline", action="store_true", help="run the offline scenarios")
    mode.add_argument("--on-twin", action="store_true", help="close the loop on the twin")

    p.add_argument("--out", default="demo-out/mpc", help="output directory")
    p.add_argument("--port", type=int, default=8791,
                   help="twin port (Kit bridge). The ROS 2 TwinLink ignores it; use "
                        "--domain-id for DDS")
    p.add_argument("--rate", type=float, default=20.0, help="on-twin control rate (Hz)")
    p.add_argument("--steps", type=int, default=200, help="on-twin control steps")
    p.add_argument("--goal-q", type=float, nargs=6, default=None,
                   help="goal joint configuration for on-twin (rad)")
    p.add_argument("--max-joint-step", type=float, default=0.02,
                   help="on-twin cap on the setpoint jump per step (rad)")
    p.add_argument("--max-lead", type=float, default=0.25,
                   help="on-twin cap on how far the setpoint may lead the measured "
                        "state (rad)")
    p.add_argument("--domain-id", type=int, default=None,
                   help="ROS_DOMAIN_ID for the ROS 2 twin link (on-twin)")
    p.add_argument("--verbose", action="store_true", help="let TwinLink log verbosely")

    p.add_argument("--N", type=int, default=None, help="horizon length in stages")
    p.add_argument("--dt", type=float, default=None, help="control period (s)")
    p.add_argument("--vel", type=float, default=None,
                   help="joint velocity limit (rad/s) for scenarios 1-2")
    p.add_argument("--real-vel", action="store_true",
                   help=f"use the UR5e hardware limit ({UR5E_JOINT_SPEED_LIMIT:.3f} rad/s)")
    p.add_argument("--floor-z", type=float, default=None,
                   help="table height for scenarios 1-2 (m); negative disables. "
                        "Scenario 3 uses its own tuned plane")
    p.add_argument("--obstacle", type=float, nargs=4, default=None,
                   metavar=("CX", "CY", "CZ", "R"),
                   help="spherical obstacle for scenarios 1-2 (scenario 3 places "
                        "its own sphere)")
    p.add_argument("--soft-obstacle", action="store_true",
                   help="penalise obstacle violation instead of forbidding it")
    p.add_argument("--tau", type=float, default=0.08, help="lag-plant time constant (s)")
    p.add_argument("--quick", action="store_true", help="shorter offline runs")
    p.add_argument("--stamp", action="store_true",
                   help="append a UTC timestamp subdirectory to --out")
    p.add_argument("--force", action="store_true", help="allow writing into a directory "
                                                        "that already has summary.json")

    args = p.parse_args(argv)

    if args.selftest:
        if not HAS_CASADI:
            print("CasADi is missing. Use the uv command at the top of this file.")
            return 2
        return selftest()

    out = args.out
    if args.stamp:
        out = os.path.join(out, datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S"))
    existing = [f for f in ("summary.json", "mpc-on-twin-log.json")
                if os.path.exists(os.path.join(out, f))]
    if existing and not args.force:
        print(f"refusing to overwrite {out}/{existing[0]} (evidence). "
              "Pick a new --out, add --stamp, or pass --force.")
        return 2

    cfg = build_cfg(args)

    if args.offline:
        if not HAS_CASADI:
            print("CasADi is missing. Use the uv command at the top of this file.")
            return 2
        run_offline(out, cfg, quick=args.quick, soft_obstacle=cfg.obstacle_soft,
                    tau=args.tau)
        print(f"[mpc] wrote {out}")
        return 0

    if args.on_twin:
        run_on_twin(out, cfg, port=args.port, rate_hz=args.rate, steps=args.steps,
                    goal_q=args.goal_q, max_joint_step=args.max_joint_step,
                    max_lead_rad=args.max_lead,
                    domain_id=args.domain_id, verbose=args.verbose)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

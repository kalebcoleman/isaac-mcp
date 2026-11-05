"""Inverse kinematics from a robot's own USD joints.

No per-robot description file: the chain from a base body to an end-effector body is read from the articulation's
joint prims (their local frames, axes and limits), forward kinematics is plain matrix math, and a damped
least-squares solver walks the joint values toward a target pose. The math is numpy only, so it runs and is
tested without Kit; ``build_chain`` is the only part that touches USD.

Limits, stated plainly: it finds *a* solution near the current pose (no collision avoidance, no path), supports
revolute and prismatic joints only, and a chain that crosses a different joint type is refused, not guessed.
"""

import math

import numpy as np

from isaac_mcp.project_assets import refuse

AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
MAX_CHAIN = 16


# ---- small math ---------------------------------------------------------------------------------------------

def quat_to_matrix(w, x, y, z):
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if n < 1e-12:
        refuse("INVALID_POSE", "orientation must be a unit quaternion [w,x,y,z].")
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def matrix_to_quat(r):
    """Unit quaternion [w, x, y, z] of a rotation matrix."""
    t = np.trace(r)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = [0.25 * s, (r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = math.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [(r[2, 1] - r[1, 2]) / s, 0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s]
    elif r[1, 1] > r[2, 2]:
        s = math.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 2] - r[2, 0]) / s, (r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s]
    else:
        s = math.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[1, 0] - r[0, 1]) / s, (r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s]
    q = np.array(q)
    return (q / np.linalg.norm(q)).tolist()


def pose_matrix(position, rotation):
    m = np.eye(4)
    m[:3, :3] = rotation
    m[:3, 3] = position
    return m


def rotation_vector(r):
    """Axis times angle of a rotation matrix (the matrix logarithm), shortest way round."""
    cos = max(-1.0, min(1.0, (np.trace(r) - 1.0) / 2.0))
    angle = math.acos(cos)
    if angle < 1e-9:
        return np.zeros(3)
    if math.pi - angle < 1e-6:  # half a turn: the axis comes from the symmetric part
        axis = np.sqrt(np.maximum((np.diag(r) + 1.0) / 2.0, 0.0))
        axis[0] = math.copysign(axis[0], r[2, 1] - r[1, 2] or 1.0)
        axis[1] = math.copysign(axis[1], r[0, 2] - r[2, 0] or 1.0)
        axis[2] = math.copysign(axis[2], r[1, 0] - r[0, 1] or 1.0)
        n = np.linalg.norm(axis)
        return axis / n * angle if n > 1e-9 else np.zeros(3)
    axis = np.array([r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1]]) / (2.0 * math.sin(angle))
    return axis * angle


def motion(kind, axis, q):
    m = np.eye(4)
    a = np.array(axis, dtype=float)
    if kind == "prismatic":
        m[:3, 3] = a * q
        return m
    c, s = math.cos(q), math.sin(q)
    x, y, z = a
    m[:3, :3] = [
        [c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
        [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
        [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)],
    ]
    return m


# ---- the chain ----------------------------------------------------------------------------------------------

class Chain:
    """A serial chain from a base body to an end-effector body.

    ``steps`` are tuples (name, kind, axis, t0, t1_inv, reverse, lower, upper): a movable joint, or a fixed one
    (kind ``fixed``) that only adds a constant transform. ``reverse`` marks a joint met child-to-parent.
    """

    def __init__(self, steps, base_pose):
        self.steps = steps
        self.base_pose = np.array(base_pose, dtype=float)
        self.tip = np.eye(4)  # a point fixed in the end-effector link's frame, such as a fingertip; the link origin by default
        self.movable = [i for i, s in enumerate(steps) if s[1] != "fixed"]
        self.names = [steps[i][0] for i in self.movable]
        self.lower = np.array([steps[i][6] for i in self.movable], dtype=float)
        self.upper = np.array([steps[i][7] for i in self.movable], dtype=float)

    def fk(self, q):
        """World pose (4x4) of the end-effector body for joint values ``q`` (one per movable joint)."""
        t = self.base_pose.copy()
        k = 0
        for name, kind, axis, t0, t1_inv, reverse, _lo, _hi in self.steps:
            value = 0.0
            if kind != "fixed":
                value = float(q[k])
                k += 1
            step = t0 @ (np.eye(4) if kind == "fixed" else motion(kind, axis, value)) @ t1_inv
            t = t @ (np.linalg.inv(step) if reverse else step)
        return t @ self.tip

    def set_tip(self, offset):
        """Place the controlled point ``offset`` (x, y, z) away from the end-effector link's origin, in that link's frame."""
        self.tip = pose_matrix(offset, np.eye(3))

    def inert_joints(self, q, eps=1e-6, floor=1e-5):
        """Names of the joints that do not move the controlled point at ``q`` (their axis passes through it)."""
        q = np.array(q, dtype=float)
        base = self.fk(q)[:3, 3]
        quiet = []
        for i, name in enumerate(self.names):
            bumped = q.copy()
            bumped[i] += eps
            if np.linalg.norm(self.fk(bumped)[:3, 3] - base) / eps < floor:
                quiet.append(name)
        return quiet


def _task_error(chain, q, target, use_orientation):
    current = chain.fk(q)
    err = [target[:3, 3] - current[:3, 3]]
    if use_orientation:
        err.append(rotation_vector(target[:3, :3] @ current[:3, :3].T))
    return np.concatenate(err), current


def _jacobian(chain, q, target, use_orientation, eps=1e-6):
    base, _ = _task_error(chain, q, target, use_orientation)
    jac = np.zeros((base.size, len(q)))
    for i in range(len(q)):
        bumped = q.copy()
        bumped[i] += eps
        shifted, _ = _task_error(chain, bumped, target, use_orientation)
        jac[:, i] = (base - shifted) / eps  # the error falls as the joint moves toward the target
    return jac


def _descend(chain, q0, target, use_orientation, pos_tol, rot_tol, iterations):
    """Levenberg-Marquardt: a damped least-squares step whose damping falls while steps help and rises when one does not,
    so it is cautious near singular poses and still converges tightly (the fixed-damping version stalled a few
    millimetres short on a five-joint arm)."""
    q = np.clip(q0.astype(float), chain.lower, chain.upper)
    err, _ = _task_error(chain, q, target, use_orientation)
    damping = 0.1
    best_q, best_cost = q.copy(), math.inf
    for count in range(1, iterations + 1):
        pos = float(np.linalg.norm(err[:3]))
        rot = float(np.linalg.norm(err[3:])) if use_orientation else 0.0
        cost = pos + rot
        if cost < best_cost:
            best_q, best_cost = q.copy(), cost
        if pos <= pos_tol and rot <= rot_tol:
            return q, count, True
        jac = _jacobian(chain, q, target, use_orientation)
        step = jac.T @ np.linalg.solve(jac @ jac.T + damping ** 2 * np.eye(jac.shape[0]), err)
        norm = float(np.linalg.norm(step))
        if norm > 0.3:
            step *= 0.3 / norm
        trial = np.clip(q + step, chain.lower, chain.upper)
        trial_err, _ = _task_error(chain, trial, target, use_orientation)
        trial_cost = float(np.linalg.norm(trial_err[:3])) + (float(np.linalg.norm(trial_err[3:])) if use_orientation else 0.0)
        if trial_cost < cost:
            q, err = trial, trial_err
            damping = max(damping * 0.5, 1e-6)
        else:
            damping = min(damping * 4.0, 10.0)
    return best_q, iterations, False


def solve(chain, q_now, target, use_orientation=True, pos_tol=1e-3, rot_tol=1e-2, iterations=200, restarts=16, seed=0):
    """Joint values that put the end effector at ``target`` (4x4, world), nearest to ``q_now`` among solutions found.

    Returns a dict with q, reached, position_error, orientation_error, iterations, attempts (how many starts were tried). The first try starts
    from the current pose; further tries start from seeded random poses inside the limits, and the success closest to
    the current pose wins. If nothing reaches the tolerance, the least-error result is returned with reached False.
    """
    q_now = np.array(q_now, dtype=float)
    rng = np.random.default_rng(seed)
    results = []
    total = 0
    for attempt in range(restarts + 1):
        if attempt == 0:
            start = q_now
        else:
            lo = np.where(np.isfinite(chain.lower), chain.lower, -math.pi)
            hi = np.where(np.isfinite(chain.upper), chain.upper, math.pi)
            start = rng.uniform(lo, hi)
        q, used, ok = _descend(chain, start, target, use_orientation, pos_tol, rot_tol, iterations)
        total += used
        err, _ = _task_error(chain, q, target, use_orientation)
        results.append((q, ok, float(np.linalg.norm(err[:3])), float(np.linalg.norm(err[3:])) if use_orientation else 0.0, attempt))
        if ok and attempt == 0:
            break
        if ok and attempt >= 1 and sum(1 for r in results if r[1]) >= 2:
            break
    good = [r for r in results if r[1]]
    pick = min(good, key=lambda r: float(np.linalg.norm(r[0] - q_now))) if good else min(results, key=lambda r: r[2] + r[3])
    return dict(q=pick[0], reached=bool(pick[1]), position_error=pick[2], orientation_error=pick[3],
                iterations=total, attempts=len(results))


# ---- reading the chain from USD ----------------------------------------------------------------------------

def _pose_of(joint, which):
    from pxr import UsdPhysics

    j = UsdPhysics.Joint(joint)
    pos = (j.GetLocalPos0Attr() if which == 0 else j.GetLocalPos1Attr()).Get()
    rot = (j.GetLocalRot0Attr() if which == 0 else j.GetLocalRot1Attr()).Get()
    pos = pos if pos is not None else (0, 0, 0)
    w = rot.GetReal() if rot is not None else 1.0
    i = rot.GetImaginary() if rot is not None else (0, 0, 0)
    return pose_matrix([pos[0], pos[1], pos[2]], quat_to_matrix(w, i[0], i[1], i[2]))


def _bodies(joint):
    from pxr import UsdPhysics

    j = UsdPhysics.Joint(joint)
    one = [str(t) for t in j.GetBody0Rel().GetTargets()]
    two = [str(t) for t in j.GetBody1Rel().GetTargets()]
    return (one[0] if one else ""), (two[0] if two else "")


def body_world_pose(stage, path):
    from pxr import UsdGeom

    m = UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(path))
    return np.array([[m[r][c] for c in range(4)] for r in range(4)]).T  # USD matrices are row-vector style


def build_chain(stage, joint_prims, base_body, ee_body, base_pose=None):
    """The chain base_body -> ee_body through ``joint_prims`` (the articulation's joints)."""
    from pxr import UsdPhysics

    graph = {}
    for joint in joint_prims:
        b0, b1 = _bodies(joint)
        if not b0 or not b1:
            continue  # a joint to the world (a fixed base) is not part of any chain
        graph.setdefault(b0, []).append((b1, joint, False))
        graph.setdefault(b1, []).append((b0, joint, True))
    if base_body not in graph and base_body != ee_body:
        refuse("IK_NO_CHAIN", f"{base_body} is not connected to any joint of this articulation.")
    came = {base_body: None}
    queue = [base_body]
    while queue and ee_body not in came:
        here = queue.pop(0)
        for other, joint, reverse in graph.get(here, ()):
            if other not in came:
                came[other] = (here, joint, reverse)
                queue.append(other)
    if ee_body not in came:
        refuse("IK_NO_CHAIN", f"No chain of joints joins {base_body} to {ee_body}.")
    path = []
    node = ee_body
    while came[node] is not None:
        _parent, joint, reverse = came[node]
        path.append((joint, reverse))
        node = came[node][0]
    path.reverse()
    if len(path) > MAX_CHAIN:
        refuse("IK_CHAIN_TOO_LONG", f"The chain has {len(path)} joints; the limit is {MAX_CHAIN}.")
    steps = []
    for joint, reverse in path:
        prim_type = joint.GetTypeName()
        t0, t1_inv = _pose_of(joint, 0), np.linalg.inv(_pose_of(joint, 1))
        name = joint.GetName()
        if prim_type == "PhysicsFixedJoint":
            steps.append((name, "fixed", (0, 0, 1), t0, t1_inv, reverse, 0.0, 0.0))
            continue
        if prim_type not in ("PhysicsRevoluteJoint", "PhysicsPrismaticJoint"):
            refuse("IK_UNSUPPORTED_JOINT", f"{joint.GetPath()} is a {prim_type}; inverse kinematics supports revolute and "
                   "prismatic joints in the chain.", joint=str(joint.GetPath()), type=prim_type)
        kind = "revolute" if prim_type == "PhysicsRevoluteJoint" else "prismatic"
        axis_token = str(UsdPhysics.RevoluteJoint(joint).GetAxisAttr().Get() if kind == "revolute"
                         else UsdPhysics.PrismaticJoint(joint).GetAxisAttr().Get() or "X")
        lower = joint.GetAttribute("physics:lowerLimit").Get()
        upper = joint.GetAttribute("physics:upperLimit").Get()
        scale = math.pi / 180.0 if kind == "revolute" else 1.0
        lo = -math.inf if lower is None or not math.isfinite(lower) else float(lower) * scale
        hi = math.inf if upper is None or not math.isfinite(upper) else float(upper) * scale
        steps.append((name, kind, AXES.get(axis_token, AXES["X"]), t0, t1_inv, reverse, lo, hi))
    base = base_pose if base_pose is not None else body_world_pose(stage, base_body)
    return Chain(steps, base)


def find_base(joint_prims):
    """The body an articulation hangs from: the one a joint fixes to the world, else the one body that is nobody's
    child. None when that is not a single body."""
    anchored, children, bodies = set(), set(), set()
    for joint in joint_prims:
        b0, b1 = _bodies(joint)
        for b in (b0, b1):
            if b:
                bodies.add(b)
        if bool(b0) != bool(b1):
            anchored.add(b0 or b1)
        elif b0 and b1:
            children.add(b1)
    if len(anchored) == 1:
        return next(iter(anchored))
    roots = bodies - children
    return next(iter(roots)) if len(roots) == 1 else None

"""Scene source for the pinned RSS live scene loader.

The text is trusted Python that RSS executes. It holds a static table, one red object (a box or a
T made of two boxes on one rigid body), a small cylinder pusher and one camera. Every parameter is
plain data near the top. The ``push`` and ``settle`` routines read ``ROLLOUT`` (set by the bridge's
``rollout`` action) and fall back to ``PUSH`` and ``DURATION_S`` below.

How things move, from the RSS source (checkout paths, pin 87b5e32):
* ``domains/rigid/entity.py`` ``_add_primitive`` records ``coord_start`` and ``dof_start`` for a free
  body, and its ``reset`` writes ``state.joint_q[coord_start:coord_start+7]``. The pusher is placed
  the same way, every control step, with its velocity written to ``joint_qd[dof_start:dof_start+6]``.
* ``body_q`` and ``body_qd`` are outputs: the solver rebuilds them from ``joint_q`` and ``joint_qd``
  every step, so a write to them is discarded (``Sim.reset_scene`` in ``sim/sim.py`` zeroes
  ``joint_qd`` first for the same reason).
* ``specs/asset.py`` ``ObjectSpec.shape.add_to(...)`` draws the shape onto the body. ``TeeShape``
  overrides it to draw two boxes on that one body, so the T moves as one rigid body.
"""

TEMPLATE = '''import json
import math
from pathlib import Path

import numpy as np
import robosimstudio as rss
from robosimstudio.specs import ObjectSpec
from robosimstudio.specs.asset import ShapeSpec

# ---- parameters: plain data, edit these -------------------------------------------------------
# Table frame: metres, +x right, +y away from the camera, +z up. Yaw in radians about +z.
OBJECT_NAME = "block"           # the name masks and results use; compare with keys=["block=red"]
SHAPE = "__SHAPE__"              # "box" or "tee"
BOX_HALF = (0.065, 0.07, 0.02)   # box half sizes x, y, z
TEE_BAR_HALF = (0.065, 0.0175)   # tee bar: half length (x), half thickness (y)
TEE_STEM_HALF = (0.0175, 0.0525) # tee stem: half width (x), half length (y)
OBJECT_HALF_HEIGHT = 0.02        # tee half height (z); a box uses BOX_HALF[2]
OBJECT_XY_YAW = (0.0, 0.0, 0.0)  # start x, y [m] and yaw [rad]; yaw 0 = bar far, stem toward camera
OBJECT_MASS = 0.1
OBJECT_FRICTION = 0.4
OBJECT_COLOUR = (0.85, 0.05, 0.05)
TABLE_HALF = (0.4, 0.3)          # table half size x, y
TABLE_COLOUR = (0.9, 0.9, 0.9)
PUSHER_RADIUS = 0.0125
PUSHER_HALF_HEIGHT = 0.03
PUSHER_COLOUR = (0.1, 0.1, 0.1)
CAMERA_EYE = (-0.05, -0.05, 0.7)
CAMERA_LOOK_AT = (-0.05, 0.12, 0.0)
CAMERA_SIZE = (320, 240)

# The push. waypoints are [seconds, x, y] pusher positions on the table; the pusher moves in straight
# lines between them. object_velocity is an optional initial [vx, vy, yaw_rate] of the object in m/s
# and rad/s (friction stops it again; a push-only run leaves it None). rollout(push={...}) overrides this.
PUSH = {"waypoints": [[0.0, 0.0, -0.20], [2.0, 0.0, 0.0]], "object_velocity": None}
DURATION_S = 2.0                 # used when rollout gives neither duration_s nor steps


# ---- shapes -----------------------------------------------------------------------------------
class TeeShape(ShapeSpec):
    """A ShapeSpec that draws a T (bar and stem, two boxes) on one body, so it moves as one."""

    def add_to(self, builder, *, body, xform, label, cfg=None):
        import warp as wp

        common = {"body": body, "label": label}
        if cfg is not None:
            common["cfg"] = cfg
        (bar_x, bar_y), (stem_x, stem_y), half_z = self._tee
        total = 2 * bar_y + 2 * stem_y
        first = None
        for half_x, half_y, centre_y in ((bar_x, bar_y, total / 2 - bar_y), (stem_x, stem_y, -total / 2 + stem_y)):
            placed = wp.transform_multiply(xform, wp.transform((0.0, centre_y, 0.0), wp.quat_identity()))
            index = builder.add_shape_box(hx=half_x, hy=half_y, hz=half_z, xform=placed, **common)
            first = index if first is None else first
        return first


def tee_shape():
    shape = TeeShape(kind="box", half_extents=(TEE_BAR_HALF[0], TEE_BAR_HALF[1] + TEE_STEM_HALF[1], OBJECT_HALF_HEIGHT))
    object.__setattr__(shape, "_tee", (TEE_BAR_HALF, TEE_STEM_HALF, OBJECT_HALF_HEIGHT))
    return shape


# ---- scene ------------------------------------------------------------------------------------
if SHAPE == "tee":
    shape, half_height = tee_shape(), OBJECT_HALF_HEIGHT
elif SHAPE == "box":
    shape, half_height = ShapeSpec(kind="box", half_extents=BOX_HALF), BOX_HALF[2]
else:
    raise ValueError("SHAPE must be 'box' or 'tee'")

scene = rss.Scene(cell="void", name="push_scene")
scene.add(rss.box(name="table", half=(TABLE_HALF[0], TABLE_HALF[1], 0.02), at=(0.0, 0.0, -0.02),
                  static=True, friction=OBJECT_FRICTION, color=TABLE_COLOUR))
scene.add(ObjectSpec(name=OBJECT_NAME, shape=shape, mass=OBJECT_MASS, friction=OBJECT_FRICTION,
                     color=OBJECT_COLOUR, pose=(OBJECT_XY_YAW[0], OBJECT_XY_YAW[1], half_height),
                     quat=(0.0, 0.0, math.sin(OBJECT_XY_YAW[2] / 2), math.cos(OBJECT_XY_YAW[2] / 2))))
_first = PUSH["waypoints"][0]
scene.add(rss.cylinder(name="pusher", radius=PUSHER_RADIUS, half_height=PUSHER_HALF_HEIGHT,
                       at=(_first[1], _first[2], PUSHER_HALF_HEIGHT + 0.001), mass=2.0,
                       friction=0.2, color=PUSHER_COLOUR))
scene.cameras.append(rss.CameraSpec(name="camera", width=CAMERA_SIZE[0], height=CAMERA_SIZE[1],
                                    eye=CAMERA_EYE, look_at=CAMERA_LOOK_AT, up=(0.0, 0.0, 1.0)))
BUILD = {"renderer": "tiled"}


# ---- moving things ----------------------------------------------------------------------------
def place_free_body(sim, name, x=None, y=None, velocity=None):
    """Write a free body's planar position and/or velocity into the solver's own coordinates.

    joint_q[coord_start:+7] is (x, y, z, qx, qy, qz, qw); joint_qd[dof_start:+6] is
    (vx, vy, vz, wx, wy, wz). Writing body_q or body_qd instead does nothing: the solver rebuilds them.
    """
    entity = sim.scene.by_name[name]
    state = sim.state
    if x is not None:
        q = state.joint_q.numpy()
        c = entity.coord_start
        q[c], q[c + 1] = x, y
        state.joint_q.assign(q)
    if velocity is not None:
        qd = state.joint_qd.numpy()
        d = entity.dof_start
        qd[d:d + 6] = [velocity[0], velocity[1], 0.0, 0.0, 0.0, velocity[2] if len(velocity) > 2 else 0.0]
        state.joint_qd.assign(qd)


def pusher_at(waypoints, t):
    """Position and velocity on the piecewise-linear path at time t; clamped before and after."""
    if t <= waypoints[0][0]:
        return waypoints[0][1], waypoints[0][2], 0.0, 0.0
    for (t0, x0, y0), (t1, x1, y1) in zip(waypoints, waypoints[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return x0 + f * (x1 - x0), y0 + f * (y1 - y0), (x1 - x0) / (t1 - t0), (y1 - y0) / (t1 - t0)
    return waypoints[-1][1], waypoints[-1][2], 0.0, 0.0


def _rollout(hz):
    asked = globals().get("ROLLOUT") or {}
    return asked.get("push") or PUSH, int(asked.get("steps") or round(DURATION_S * hz)), asked.get("take") or "run"


def _yaw(pose):
    return 2.0 * math.atan2(float(pose[5]), float(pose[6]))


def push_routine(sim):
    """Move the pusher along the PUSH waypoints for the asked number of steps, one step per yield."""
    hz = float(sim.request.physics.control_hz)
    push, steps, take = _rollout(hz)
    waypoints = [list(map(float, w)) for w in push["waypoints"]]
    dt = 1.0 / hz
    start = [float(v) for v in sim[OBJECT_NAME].position]
    start_yaw = _yaw(sim[OBJECT_NAME].pose)
    if push.get("object_velocity"):
        place_free_body(sim, OBJECT_NAME, velocity=[float(v) for v in push["object_velocity"]])
    worst = 0.0
    for k in range(steps):
        x, y, vx, vy = pusher_at(waypoints, k * dt)
        place_free_body(sim, "pusher", x, y, velocity=[vx, vy, 0.0])
        sim.run(1)
        want = pusher_at(waypoints, (k + 1) * dt)
        have = sim["pusher"].position
        worst = max(worst, math.hypot(float(have[0]) - want[0], float(have[1]) - want[1]))
        yield
    end = [float(v) for v in sim[OBJECT_NAME].position]
    result = {"applied": True, "routine": "push", "take": take, "steps": steps, "control_hz": hz,
              "waypoints": waypoints, "object_velocity": push.get("object_velocity"),
              "pusher_tracking_error_max_m": round(worst, 5), "pusher_tracked": worst < 0.005,
              "object": OBJECT_NAME, "object_start_xy_m": start[:2], "object_end_xy_m": end[:2],
              "object_start_yaw_rad": round(start_yaw, 4), "object_end_yaw_rad": round(_yaw(sim[OBJECT_NAME].pose), 4)}
    Path(__file__).with_name(f"push-{take}.json").write_text(json.dumps(result))


def settle_routine(sim):
    """Step with no push for the asked number of steps (a control run: nothing should move)."""
    _, steps, take = _rollout(float(sim.request.physics.control_hz))
    for _ in range(steps):
        sim.run(1)
        yield
    Path(__file__).with_name(f"push-{take}.json").write_text(json.dumps(
        {"applied": False, "routine": "settle", "take": take, "steps": steps}))


routines = {"push": push_routine, "settle": settle_routine}
'''


def scene_text(shape="tee"):
    """The template with SHAPE set; ``shape`` must be ``"box"`` or ``"tee"``."""
    if shape not in ("box", "tee"):
        raise ValueError("shape must be 'box' or 'tee'.")
    return TEMPLATE.replace("__SHAPE__", shape)


SCENE = scene_text("tee")
PARAMETERS = ["OBJECT_NAME", "SHAPE", "BOX_HALF", "TEE_BAR_HALF", "TEE_STEM_HALF", "OBJECT_HALF_HEIGHT",
              "OBJECT_XY_YAW", "OBJECT_MASS", "OBJECT_FRICTION", "OBJECT_COLOUR", "TABLE_HALF",
              "PUSHER_RADIUS", "CAMERA_EYE", "CAMERA_LOOK_AT", "PUSH", "DURATION_S"]

"""Read live PhysX NumPy tensor samples; never manufacture missing measurements.

Call after a completed physics step, with its observed simulation timestamp.
These functions do not initialize, step, publish ROS, subtract gravity/bias or
claim hardware calibration. Sampling cadence is owned by the caller; rate_hz
is a requested cadence, not evidence of a delivered sensor rate.
"""

import math

import numpy as np
from pxr import Gf, Usd, UsdGeom, UsdPhysics


def _time(seconds):
    if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError("physics_time_s must be observed nonnegative simulation time")
    return float(seconds)


def _unavailable(reason, status="unavailable"):
    return {"status": status, "reason": reason, "measurement": None}


def _view_ready(view, backend):
    if backend != "physx":
        return _unavailable("Only PhysX sensor sampling is supported", "unsupported")
    try:
        ready = view is not None and view.check()
    except Exception:
        ready = False
    if not ready:
        return _unavailable("Initialize a live tensor view before sampling")
    return None


def read_bota_wrench(stage, view, report, config, *, physics_time_s, backend):
    """Read Bota's own incoming fixed-joint reaction, in the configured sensor frame.

    view is a native NumPy ArticulationView for report.articulation_root, not the
    Isaac Core wrapper. Explicitly set bota.measurement='incoming_joint_wrench'.
    The sensor must remain in the articulation (no fixed-link merge). Values are
    [Fx,Fy,Fz,Tx,Ty,Tz], N and N m, exerted on the Bota child by its parent.
    This measures a simulated joint reaction including gravity/inertial effects,
    not a calibrated hardware Rokubi output or a force-only contact estimate.
    """
    if config.get("bota", {}).get("measurement") != "incoming_joint_wrench":
        return _unavailable("Bota incoming_joint_wrench measurement is not configured", "unsupported")
    invalid = _view_ready(view, backend)
    if invalid:
        return invalid
    timestamp = _time(physics_time_s)
    try:
        if view.count != 1 or list(view.prim_paths) != [report["articulation_root"]]:
            return _unavailable("Tensor view must target exactly the reported arm articulation")
        body = stage.GetPrimAtPath(report["bota_body_path"])
        sensor = stage.GetPrimAtPath(report["bota_sensor_path"])
        joint = UsdPhysics.FixedJoint(stage.GetPrimAtPath(report["bota_joint_path"]))
        if (not body or not sensor or not joint or joint.GetExcludeFromArticulationAttr().Get()
                or joint.GetBody1Rel().GetTargets() != [body.GetPath()]
                or not sensor.GetPath().HasPrefix(body.GetPath())):
            return _unavailable("Bota joint/body/sensor topology is missing or changed")
        bodies = [prim for prim in Usd.PrimRange(stage.GetPrimAtPath(report["assembly_path"]))
                  if prim.HasAPI(UsdPhysics.RigidBodyAPI) and prim.GetName() == body.GetName()]
        names = view.shared_metatype.link_indices
        if len(bodies) != 1 or body.GetName() not in names:
            return _unavailable("Bota link is merged, absent or ambiguous in the runtime articulation")
        sensor_in_body, reset = UsdGeom.XformCache().ComputeRelativeTransform(sensor, body)
        if reset:
            return _unavailable("Sensor frame cannot reset its transform stack")
        joint_in_body = Gf.Matrix4d().SetRotate(Gf.Quatd(joint.GetLocalRot1Attr().Get()))
        joint_in_body.SetTranslateOnly(Gf.Vec3d(joint.GetLocalPos1Attr().Get()))
        to_sensor = joint_in_body * sensor_in_body.GetInverse()
        values = view.get_link_incoming_joint_force()
        if not isinstance(values, np.ndarray):
            return _unavailable("Use a NumPy-frontend tensor view", "unsupported")
        if values.shape != (1, view.max_links, 6) or not np.isfinite(values).all():
            return _unavailable("PHYSICS_STATE_INVALID: invalid incoming joint wrench array")
        wrench = values[0, names[body.GetName()]]
        force = to_sensor.TransformDir(Gf.Vec3d(*map(float, wrench[:3])))
        torque = to_sensor.TransformDir(Gf.Vec3d(*map(float, wrench[3:])))
        torque += Gf.Cross(to_sensor.ExtractTranslation(), force)
        measurement = [float(value) for value in (*force, *torque)]
        if not all(math.isfinite(value) for value in measurement):
            return _unavailable("PHYSICS_STATE_INVALID: nonfinite transformed wrench")
        return {"status": "measured", "measurement": measurement,
                "force_n": measurement[:3], "torque_nm": measurement[3:],
                "physics_time_s": timestamp, "backend": backend,
                "frame_id": config["bota"]["frame_id"], "frame_path": str(sensor.GetPath()),
                "source_joint": str(joint.GetPath()), "quantity": "parent_on_child_joint_reaction",
                "gravity_compensated": False, "hardware_calibrated": False}
    except Exception as exc:
        return _unavailable(f"Bota tensor sampling unavailable: {exc}")


def read_finger_contacts(view, config, *, physics_time_s, physics_dt_s, backend):
    """Read net world-frame finger contact forces, explicitly NOT TAL221 readings.

    Set finger_force_sensors.measurement='net_contact_force' and body_paths to
    the two exact rigid finger bodies. A caller-created native RigidContactView
    must expose those sensor_paths in the same order and enable contact reports.
    Net forces include all contacts on each body, not only its black pad; they
    are neither individual-pad pressure nor a standard RG2 built-in sensor.
    fitted=True alone never enables measurements or implies purchased hardware.
    """
    settings = config.get("finger_force_sensors", {})
    if settings.get("measurement") != "net_contact_force":
        return _unavailable("Finger contact measurement is not configured; no load-cell readings", "unsupported")
    invalid = _view_ready(view, backend)
    if invalid:
        return invalid
    timestamp = _time(physics_time_s)
    if isinstance(physics_dt_s, bool) or not math.isfinite(physics_dt_s) or physics_dt_s <= 0:
        raise ValueError("physics_dt_s must be the positive simulated contact-report interval")
    try:
        paths = settings.get("body_paths", [])
        if len(paths) != 2 or len(set(paths)) != 2 or list(view.sensor_paths) != paths:
            return _unavailable("Contact view must target exactly the two configured finger bodies")
        values = view.get_net_contact_forces(dt=physics_dt_s)
        if not isinstance(values, np.ndarray):
            return _unavailable("Use a NumPy-frontend contact view", "unsupported")
        if values.shape != (2, 3) or not np.isfinite(values).all():
            return _unavailable("PHYSICS_STATE_INVALID: invalid net contact force array")
        return {"status": "measured", "measurement": values.tolist(), "force_n": values.tolist(),
                "body_paths": list(paths), "frame_id": "world", "physics_time_s": timestamp,
                "physics_dt_s": float(physics_dt_s), "backend": backend,
                "quantity": "whole_finger_net_contact_force", "hardware_calibrated": False,
                "limitations": ["Not a TAL221/load-cell measurement or pad-only normal force."]}
    except Exception as exc:
        return _unavailable(f"Finger contact sampling unavailable: {exc}")

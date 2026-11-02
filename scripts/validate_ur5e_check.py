"""Validate the UR5e verifier's bridge responses without importing Isaac Sim.

The checks are a regression baseline for the bundled UR5e workstation, not a
general robot or physics certification. Input is one JSON bridge response on stdin.
"""

import json
import math
import sys


JOINTS = {
    "shoulder_pan_joint": (-360.0, 360.0),
    "shoulder_lift_joint": (-360.0, 360.0),
    "elbow_joint": (-180.0, 180.0),
    "wrist_1_joint": (-360.0, 360.0),
    "wrist_2_joint": (-360.0, 360.0),
    "wrist_3_joint": (-360.0, 360.0),
}
ROOTS = ("/World/UR5e", "/World/RG2_Gripper", "/World/QuickChanger")


def _near(value, expected, tolerance):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and abs(value - expected) <= tolerance)


def validate(check, response):
    """Return human-readable failures; an empty list means the check passed."""
    errors = []

    def require(condition, message):
        if not condition:
            errors.append(message)

    if not isinstance(response, dict):
        return ["bridge response is not a JSON object"]
    if check == "quick_start":
        require(response.get("status") == "success", "quick_start did not report success")
        return errors
    result = response.get("result")
    if not isinstance(result, dict):
        return [f"{check}: bridge response has no result object"]

    if check == "scene_prims":
        children = result.get("world_children")
        require(isinstance(children, list), "world_children is missing or not a list")
        if isinstance(children, list):
            for root in (*ROOTS, "/World/Vention"):
                require(root in children, f"missing scene prim: {root}")
        require(result.get("ur5e_present") is True, "UR5e is missing")
        require(result.get("rg2_present") is True, "RG2 gripper is missing")
        require(result.get("gravity_disabled_on_robot_links") == [],
                "robot links have gravity enabled or the gravity observation is missing")
    elif check == "scene_physics":
        scenes = result.get("scenes")
        require(isinstance(scenes, list) and len(scenes) == 1,
                "expected exactly one typed PhysicsScene")
        if isinstance(scenes, list) and len(scenes) == 1 and isinstance(scenes[0], dict):
            scene = scenes[0]
            require(scene.get("path") == "/physicsScene" and scene.get("type") == "PhysicsScene",
                    "the configured /physicsScene definition was lost")
            require(scene.get("gpu_dynamics") is False and scene.get("broadphase") == "MBP",
                    "the UR5e fixture no longer uses CPU dynamics/MBP")
            require(_near(scene.get("steps_per_second"), 120, 1e-6),
                    "the UR5e fixture no longer uses 120 Hz")
        else:
            require(False, "invalid physics scene observation")
        require(type(result.get("initialized")) is bool, "physics initialization state is missing")
        if result.get("initialized"):
            require(_near(result.get("runtime_dt"), 1 / 120, 1e-9),
                    "runtime physics timestep differs from configured 120 Hz")
    elif check == "gravity_robot_chain":
        for root in ROOTS:
            data = result.get(root)
            if not isinstance(data, dict):
                errors.append(f"missing rigid-body observation for {root}")
                continue
            count = data.get("rigid_bodies")
            require(isinstance(count, int) and not isinstance(count, bool) and count > 0,
                    f"{root} has no observed rigid bodies")
            count_enabled = data.get("gravity_enabled_count")
            require(type(count_enabled) is int and count_enabled == 0 and data.get("gravity_enabled") == [],
                    f"{root} has gravity-enabled bodies or incomplete observations")
    elif check == "joint_limits":
        for name, (lower, upper) in JOINTS.items():
            row = result.get(name)
            if not isinstance(row, dict):
                errors.append(f"missing joint limits: {name}")
                continue
            require(_near(row.get("lower_deg"), lower, 0.01), f"{name} lower limit differs from {lower} degrees")
            require(_near(row.get("upper_deg"), upper, 0.01), f"{name} upper limit differs from {upper} degrees")
    elif check == "drive_settings":
        drives = result.get("live_arm_drives")
        if not isinstance(drives, dict):
            return ["missing live arm drive observations"]
        for name in JOINTS:
            row = drives.get(name)
            if not isinstance(row, dict):
                errors.append(f"missing drive settings: {name}")
                continue
            for field, expected in (("stiffness", 100.0), ("damping", 10.0), ("max_force", 200.0)):
                require(_near(row.get(field), expected, 0.01), f"{name} {field} differs from {expected}")
    elif check == "single_joint_motion":
        require(result.get("joint") == "elbow_joint", "unexpected motion-test joint")
        require(_near(result.get("commanded_rad"), -0.1, 1e-6), "unexpected commanded motion")
        require(_near(result.get("achieved_rad"), -0.1, 0.02), "elbow motion missed target by more than 0.02 rad")
        require(_near(result.get("returned_error_rad"), 0.0, 0.02), "elbow did not return within 0.02 rad")
        require(result.get("all_joints_finite") is True, "joint positions are non-finite or not reported")
    elif check == "export_reference":
        require(isinstance(result.get("written"), str) and bool(result["written"]), "checkpoint was not exported")
        absolute_refs = result.get("absolute_refs_left")
        require(type(absolute_refs) is int and absolute_refs == 0,
                "checkpoint contains absolute repository references or the count is missing")
        count = result.get("relative_asset_refs")
        require(isinstance(count, int) and not isinstance(count, bool) and count > 0,
                "checkpoint has no relative asset references")
        scene = result.get("physics_scene", {})
        require(isinstance(scene, dict) and scene.get("type") == "PhysicsScene"
                and scene.get("path") == "/physicsScene", "checkpoint physics definition was not verified")
    elif check == "reopen_checkpoint":
        require(result.get("checkpoint_present") is True, "reference checkpoint is missing")
        require(result.get("opening") is True, "reference checkpoint open was not scheduled")
        # A re-export is not byte-identical across machines. Its checksum is informational.
    else:
        errors.append(f"no validator defined for check: {check}")
    return errors


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: validate_ur5e_check.py CHECK < response.json", file=sys.stderr)
        return 2
    try:
        response = json.load(sys.stdin)
    except (ValueError, OSError) as exc:
        print(f"invalid JSON response: {exc}", file=sys.stderr)
        return 1
    failures = validate(args[0], response)
    for failure in failures:
        print(f"  FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

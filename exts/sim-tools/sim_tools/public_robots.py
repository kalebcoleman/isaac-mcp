"""Robots NVIDIA publishes, by name, for sim.quick_start. Nothing is downloaded into the repository.

Each file is referenced from the running build's own assets root, because releases move files: TurtleBot3 is
tb3_burger_processed.usda on 6.1 and 6.2 and turtlebot3_burger.usd on 6.0 and 5.1. ``paths`` are tried in order.
Wheel figures are from each robot's USD. Driving was measured for these on 6.2 and 5.1; see docs/ROS2-RVIZ.md.

``locate`` takes its stat function, and the graph builders are plain data, so all of this is tested without Kit.
"""

from __future__ import annotations

import asyncio

from twinsmith.project_assets import refuse

STAT_TIMEOUT_S = 10.0
ROBOTS = "Isaac/Robots/"
_TB3 = ROBOTS + "Turtlebot/Turtlebot3/"


def _wheels(left, right, radius, separation, base):
    return dict(joints=[left, right], radius_m=radius, separation_m=separation, base_link=base)


NVIDIA_ROBOTS = {
    "ur5e": dict(prim="UR5e", paths=[ROBOTS + "UniversalRobots/ur5e/ur5e.usd"]),
    "franka": dict(prim="Franka", paths=[ROBOTS + "FrankaRobotics/FrankaPanda/franka.usd"]),
    "turtlebot3_burger": dict(prim="TurtleBot3", paths=[_TB3 + "tb3_burger_processed.usda", _TB3 + "turtlebot3_burger.usd"],
                              wheels=_wheels("wheel_left_joint", "wheel_right_joint", 0.033, 0.16, "base_link")),
    "carter_v1": dict(prim="Carter", paths=[ROBOTS + "NVIDIA/Carter/carter_v1.usd"], spawn_height=0.25,  # its origin is at axle height
                      wheels=_wheels("left_wheel", "right_wheel", 0.24, 0.54, "chassis_link")),
    "nova_carter": dict(prim="NovaCarter", paths=[ROBOTS + "NVIDIA/NovaCarter/nova_carter.usd"],
                        wheels=_wheels("joint_wheel_left", "joint_wheel_right", 0.14, 0.413, "chassis_link")),
    # A humanoid's origin is its pelvis, so at the world origin its legs start inside the floor: lift it onto the ground.
    # Only these are lifted: Carter's and Nova Carter's USD carry stray geometry that reads as 50 to 1,500 m below the ground.
    "unitree_h1": dict(prim="UnitreeH1", paths=[ROBOTS + "Unitree/H1/h1.usd"], lift_to_ground=True),
    "unitree_g1": dict(prim="UnitreeG1", paths=[ROBOTS + "Unitree/G1/g1.usd"], lift_to_ground=True),
    "jetbot": dict(prim="Jetbot", paths=[ROBOTS + "NVIDIA/Jetbot/jetbot.usd"],
                   wheels=_wheels("left_wheel_joint", "right_wheel_joint", 0.03, 0.1125, "chassis")),
    "create3": dict(prim="Create3", paths=[ROBOTS + "iRobot/Create3/create_3.usd"],
                    wheels=_wheels("left_wheel_joint", "right_wheel_joint", 0.036, 0.233, "base_link")),
}


def assets_root():
    """The running build's configured assets root, read without a network probe."""
    from isaacsim.storage.native import get_assets_root_path

    try:
        try:
            return get_assets_root_path(skip_check=True)
        except TypeError:  # older API without skip_check
            return get_assets_root_path()
    except RuntimeError:  # raised when no assets root is configured
        return None


def stat_found(result, ok, not_found):
    """True or False for a stat answer. Anything else, such as a lost connection, raises: unreachable is not absent."""
    if result == ok:
        return True
    if result == not_found:
        return False
    raise OSError(str(result))


async def client_stat(url):
    """True when the assets server has this file. Times out rather than holding Kit's loop."""
    import omni.client

    result, _ = await asyncio.wait_for(omni.client.stat_async(url), STAT_TIMEOUT_S)
    return stat_found(result, omni.client.Result.OK, omni.client.Result.ERROR_NOT_FOUND)


async def locate(name, root, stat):
    """What sim.quick_start needs for this name: the first path this build has, or a refusal naming the choices."""
    entry = NVIDIA_ROBOTS[name]
    known = ", ".join(sorted(NVIDIA_ROBOTS))
    if not root:
        refuse("ASSETS_UNAVAILABLE", f"{name} comes from Isaac Sim's assets root, and this simulator has none configured. "
               "Set persistent.isaac.asset_root.default, or use <pack id>/<version>/<file.usd> from a pack. Nothing was changed.")
    root = root.rstrip("/")
    tried, failed = [], None
    for path in entry["paths"]:
        url = f"{root}/{path}"
        tried.append(url)
        try:
            if await stat(url):
                report = dict(asset=path, source="nvidia_assets_root", location=url)
                return dict(name=entry["prim"], uri=url, assets=[report], wheels=entry.get("wheels"),
                            lift_to_ground=bool(entry.get("lift_to_ground")), spawn_height=entry.get("spawn_height"))
        except (OSError, asyncio.TimeoutError) as exc:  # unreachable is not the same as absent
            failed = f"{type(exc).__name__}: {str(exc)[:120]}"
    if failed:
        refuse("ASSETS_UNAVAILABLE", f"Could not reach this simulator's assets root to check {name} ({failed}). "
               f"Tried: {', '.join(tried)}. Nothing was changed.", tried=tried)
    refuse("ROBOT_NOT_ON_THIS_BUILD", f"This simulator's assets root has no {name}. Tried: {', '.join(tried)}. "
           f"Other public names: {known}. Nothing was changed.", tried=tried, names=sorted(NVIDIA_ROBOTS))

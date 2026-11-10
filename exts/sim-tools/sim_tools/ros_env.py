"""The ROS 2 / DDS settings this Kit process runs with, and how a shell can match them.

A shell that has not sourced the DDS profile the simulator uses (Cyclone, localhost only, its ROS_DOMAIN_ID) finds
nothing while Kit is publishing, so /clock is invisible to it. ``describe`` is pure and unit-tested.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

FORCED = ("RMW_IMPLEMENTATION", "ROS_LOCALHOST_ONLY", "CYCLONEDDS_URI")  # set by config/ros_dds.env
KEYS = ("ROS_DISTRO", "ROS_DOMAIN_ID", "RMW_IMPLEMENTATION", "ROS_LOCALHOST_ONLY", "CYCLONEDDS_URI",
        "ROS_AUTOMATIC_DISCOVERY_RANGE", "ROS_STATIC_PEERS")
REPO = Path(__file__).resolve().parents[3]


def describe(env=None, repo=REPO):
    """{settings, shell}: the simulator's ROS environment and the shell lines that match it."""
    env = os.environ if env is None else env
    settings = {k: env.get(k) for k in KEYS if env.get(k)}
    distro = settings.get("ROS_DISTRO")
    if distro and not re.fullmatch(r"[a-z]+", distro):
        distro = None  # never paste an odd value into a path the user runs
    # The profile forces these; when Kit runs without them (not launched via run.sh), unset them too.
    unsets = "; ".join(f"unset {k}" for k in FORCED if k not in settings)
    domain = settings.get("ROS_DOMAIN_ID", "0")
    # Re-export every captured setting after sourcing: the profile forces Cyclone/localhost, but this
    # Kit may run with other DDS settings, and a mismatched shell sees no topics at all.
    script = Path(repo) / "scripts" / "ros_env.sh"
    already = ("ROS_DISTRO", "ROS_DOMAIN_ID") if script.is_file() else ()
    exports = "; ".join(f"export {k}={shlex.quote(v)}" for k, v in settings.items() if k not in already)
    if script.is_file():
        first = f"export ROS_DISTRO={shlex.quote(distro)}; " if distro else ""
        shell = f"{first}export ROS_DOMAIN_ID={shlex.quote(domain)}; source {shlex.quote(str(script))}"
    else:
        setup = f"/opt/ros/{distro}/setup.bash" if distro else "/opt/ros/<distro>/setup.bash"
        dds = shlex.quote(str(Path(repo) / "config" / "ros_dds.env"))
        shell = (f"source {setup}; set -a; . {dds}; set +a; export ROS_DOMAIN_ID={shlex.quote(domain)}; "
                 "ros2 daemon stop")
        already = ("ROS_DOMAIN_ID",)
        exports = "; ".join(f"export {k}={shlex.quote(v)}" for k, v in settings.items() if k not in already)
    if exports:
        shell += f"; {exports}"
    if unsets:
        shell += f"; {unsets}"
    shell += "   # bash; then ros2 topic list"
    return dict(settings=settings, shell=shell)


def shell_hint(env=None, repo=REPO):
    info = describe(env, repo)
    return ("to see this graph's ROS topics from a shell, use the simulator's DDS settings: " + info["shell"])

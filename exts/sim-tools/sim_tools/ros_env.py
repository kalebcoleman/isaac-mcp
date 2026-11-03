"""The ROS 2 / DDS settings this Kit process runs with, and how a shell can match them.

In student run 5 neither agent could see /clock from its own shell: the shell had not sourced the
DDS profile the simulator uses (Cyclone, localhost only, its ROS_DOMAIN_ID), so discovery found
nothing while Kit was publishing. ``describe`` is pure and unit-tested.
"""

from __future__ import annotations

import os
from pathlib import Path

KEYS = ("ROS_DISTRO", "ROS_DOMAIN_ID", "RMW_IMPLEMENTATION", "ROS_LOCALHOST_ONLY", "CYCLONEDDS_URI")
REPO = Path(__file__).resolve().parents[3]


def describe(env=None, repo=REPO):
    """{settings, shell}: the simulator's ROS environment and the shell lines that match it."""
    env = os.environ if env is None else env
    settings = {k: env.get(k) for k in KEYS if env.get(k)}
    domain = settings.get("ROS_DOMAIN_ID", "0")
    script = Path(repo) / "scripts" / "ros_env.sh"
    if script.is_file():
        shell = f"export ROS_DOMAIN_ID={domain}; source {script}   # bash; then ros2 topic list"
    else:
        shell = (f"source /opt/ros/<distro>/setup.bash; set -a; . {Path(repo) / 'config' / 'ros_dds.env'}; "
                 f"set +a; export ROS_DOMAIN_ID={domain}; ros2 daemon stop")
    return dict(settings=settings, shell=shell)


def shell_hint(env=None, repo=REPO):
    info = describe(env, repo)
    return ("to see this graph's ROS topics from a shell, use the simulator's DDS settings: " + info["shell"])

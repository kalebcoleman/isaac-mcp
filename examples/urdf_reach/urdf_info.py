"""What the task needs to know from a URDF before anything is spawned. Standard library only."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

MOVING = ("revolute", "continuous", "prismatic")


def read(path: str, end_link: str) -> dict:
    """The driven joints and link names of a URDF file; a mistake in the two values the user gives is refused here, before the scene is built."""
    if not path:
        raise ValueError("Set env.urdf_path to the absolute path of your URDF, in extra_args.")
    file = Path(path)
    if not file.is_absolute() or not file.is_file():
        raise ValueError(f"env.urdf_path {path!r} is not an absolute path to a file.")
    try:
        root = ET.parse(file).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"{path} is not valid XML: {exc}") from None
    if root.tag != "robot":
        raise ValueError(f"{path} is not a URDF: its root element is <{root.tag}>.")
    links = [link.get("name") for link in root.findall("link")]
    if end_link not in links:
        raise ValueError(f"env.end_link {end_link!r} is not a link of this robot. Its links are: {', '.join(links[:40])}.")
    joints = [j.get("name") for j in root.findall("joint") if j.get("type") in MOVING and j.find("mimic") is None]
    if not joints:
        raise ValueError(f"{path} has no driven joints.")
    limits = {}
    for joint in root.findall("joint"):
        limit = joint.find("limit")
        if joint.get("name") in joints and limit is not None and limit.get("lower") is not None and limit.get("upper") is not None:
            try:
                limits[joint.get("name")] = (float(limit.get("lower")), float(limit.get("upper")))
            except ValueError:
                raise ValueError(f"Joint {joint.get('name')!r} has a limit that is not a number.") from None
    return dict(joints=joints, links=links, limits=limits)


def start_pose(info: dict) -> dict:
    """A start for every joint: zero when its limits allow, else the middle of its range."""
    pose = {}
    for name in info["joints"]:
        low, high = info["limits"].get(name, (-1.0, 1.0))
        pose[name] = 0.0 if low <= 0.0 <= high else (low + high) / 2
    return pose

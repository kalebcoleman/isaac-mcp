#!/usr/bin/env python3
"""Log a robot's odometry from ROS 2 to a CSV, for evidence that it moved.

    source scripts/ros_env.sh                      # ROS 2 loaded, and the simulator's ROS_DOMAIN_ID exported
    python3 scripts/odom_logger.py --seconds 30 --out odom.csv

Columns: wall_t (seconds since the first message), sim_stamp (the message's own time), x, y, yaw (radians), vx, wz.
Prints a one-line summary: samples, start and end position, straight-line distance, path length and the yaw change.
Run it with the ROS 2 Python, not from the `uv tool` environment of the MCP server (see docs/ROS2-RVIZ.md).
Exits 2 when ROS 2 is not loaded and 3 when no message arrives.
"""

import argparse
import csv
import math
import sys
import time


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    """The rotation about Z, in radians."""
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def summarize(rows) -> dict:
    """Start, end, straight distance, path length and total yaw change of ``(wall_t, stamp, x, y, yaw, ...)`` rows."""
    if not rows:
        return {"samples": 0}
    path = sum(math.dist((a[2], a[3]), (b[2], b[3])) for a, b in zip(rows, rows[1:]))
    turn = 0.0
    for a, b in zip(rows, rows[1:]):
        step = (b[4] - a[4] + math.pi) % (2 * math.pi) - math.pi  # unwrap: a turn past +/- pi is not a jump
        turn += step
    first, last = rows[0], rows[-1]
    return {
        "samples": len(rows),
        "start": (first[2], first[3]),
        "end": (last[2], last[3]),
        "straight_m": math.dist((first[2], first[3]), (last[2], last[3])),
        "path_m": path,
        "yaw_change_rad": turn,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seconds", type=float, default=30.0, help="how long to log (default 30)")
    parser.add_argument("--out", required=True, help="CSV file to write")
    parser.add_argument("--topic", default="/odom", help="a nav_msgs/Odometry topic (default /odom)")
    args = parser.parse_args(argv)
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    try:
        import rclpy
        from nav_msgs.msg import Odometry
        from rclpy.node import Node
    except ImportError:
        print("odom_logger: ROS 2 is not loaded. Run `source scripts/ros_env.sh` first, in this shell and not in a pipeline.", file=sys.stderr)
        return 2

    rows, started = [], []

    class Logger(Node):
        def __init__(self):
            super().__init__("odom_logger")
            self.create_subscription(Odometry, args.topic, self.on_message, 50)

        def on_message(self, message):
            now = time.monotonic()
            started.append(now) if not started else None
            pose, twist = message.pose.pose, message.twist.twist
            q = pose.orientation
            stamp = message.header.stamp.sec + message.header.stamp.nanosec * 1e-9
            rows.append((now - started[0], stamp, pose.position.x, pose.position.y, yaw_from_quaternion(q.x, q.y, q.z, q.w),
                         twist.linear.x, twist.angular.z))

    rclpy.init()
    node = Logger()
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    with open(args.out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["wall_t", "sim_stamp", "x", "y", "yaw", "vx", "wz"])
        writer.writerows([[f"{v:.4f}" if isinstance(v, float) else v for v in row] for row in rows])
    if not rows:
        print(f"odom_logger: no message on {args.topic} in {args.seconds:g} s. Check the topic and ROS_DOMAIN_ID.", file=sys.stderr)
        return 3
    summary = summarize(rows)
    print(f"{args.out}: {summary['samples']} samples, start ({summary['start'][0]:.3f}, {summary['start'][1]:.3f}), "
          f"end ({summary['end'][0]:.3f}, {summary['end'][1]:.3f}), straight {summary['straight_m']:.3f} m, "
          f"path {summary['path_m']:.3f} m, yaw change {summary['yaw_change_rad']:.3f} rad")
    return 0


if __name__ == "__main__":
    sys.exit(main())

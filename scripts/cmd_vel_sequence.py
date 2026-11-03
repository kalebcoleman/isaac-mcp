#!/usr/bin/env python3
"""Drive a robot through a timed /cmd_vel sequence, and always stop it at the end.

For demos and lab checks where keyboard teleop is not an option (an agent's shell has no TTY).
Each step is ``linear,angular,seconds``: linear x in m/s, angular z in rad/s, held for that many
seconds; steps are separated by ``;``.

    source scripts/ros_env.sh                       # ROS 2 + config/ros_dds.env, daemon stopped
    python3 scripts/cmd_vel_sequence.py "0.15,0,5;0,0.5,3;0,0,1"
    python3 scripts/cmd_vel_sequence.py "0.1,0,2" --topic /cmd_vel --rate 20 --stamped
    python3 scripts/cmd_vel_sequence.py "0.2,0,4" --dry-run    # print the plan, no ROS needed

A zero Twist is published when the sequence ends, fails, or is interrupted by Ctrl-C, SIGTERM or
SIGHUP (``timeout`` sends SIGTERM by default), so the robot never keeps driving after this script.
Only SIGKILL skips it; give ``timeout -s KILL`` a longer limit than the sequence. Times are wall
clock. Speeds above --max-linear / --max-angular are refused, not clipped.
"""

from __future__ import annotations

import argparse
import math
import signal
import sys
import time
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class Step:
    linear: float     # m/s along the robot's x axis
    angular: float    # rad/s about z
    seconds: float


def parse_sequence(text: str, max_linear: float = 1.0, max_angular: float = 2.0,
                   max_seconds: float = 600.0) -> List[Step]:
    """``"0.15,0,5;0,0.5,3"`` -> steps. Raises ValueError with the offending step."""
    steps = []
    for index, raw in enumerate(part.strip() for part in text.split(";")):
        if not raw:
            continue
        fields = [f.strip() for f in raw.split(",")]
        if len(fields) != 3:
            raise ValueError(f"step {index + 1} {raw!r}: need linear,angular,seconds")
        try:
            linear, angular, seconds = (float(f) for f in fields)
        except ValueError:
            raise ValueError(f"step {index + 1} {raw!r}: not numbers") from None
        if not all(math.isfinite(v) for v in (linear, angular, seconds)):
            raise ValueError(f"step {index + 1} {raw!r}: values must be finite")
        if seconds <= 0:
            raise ValueError(f"step {index + 1} {raw!r}: seconds must be > 0")
        if abs(linear) > max_linear:
            raise ValueError(f"step {index + 1} {raw!r}: |linear| > --max-linear {max_linear} m/s")
        if abs(angular) > max_angular:
            raise ValueError(f"step {index + 1} {raw!r}: |angular| > --max-angular {max_angular} rad/s")
        steps.append(Step(linear, angular, seconds))
    if not steps:
        raise ValueError("empty sequence")
    total = sum(s.seconds for s in steps)
    if total > max_seconds:
        raise ValueError(f"sequence lasts {total:g} s, more than the {max_seconds:g} s limit")
    return steps


def command_at(steps: List[Step], elapsed: float) -> Optional[Step]:
    """The step active ``elapsed`` seconds after the start, or None once the sequence is over."""
    if elapsed < 0:
        return None
    start = 0.0
    for step in steps:
        if elapsed < start + step.seconds:
            return step
        start += step.seconds
    return None


def describe(steps: List[Step]) -> str:
    lines, start = [], 0.0
    for step in steps:
        lines.append(f"  t={start:6.2f}-{start + step.seconds:6.2f} s  linear={step.linear:+.3f} m/s  "
                     f"angular={step.angular:+.3f} rad/s")
        start += step.seconds
    lines.append(f"  t={start:6.2f} s          stop (zero Twist)")
    return "\n".join(lines)


class _Stop(Exception):
    """Raised from a signal handler so ``finally`` publishes zero."""


_STATE = {"stopping": False}


def _install_signal_handlers() -> None:
    def handler(signum, _frame):
        if _STATE["stopping"]:
            return  # the zero is already going out
        raise _Stop(signal.Signals(signum).name)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, handler)


def run(args, steps: List[Step]) -> int:
    try:
        import rclpy
        from geometry_msgs.msg import Twist, TwistStamped
    except ImportError as error:
        print(f"cmd_vel_sequence: rclpy is not importable ({error}); source a ROS 2 install first "
              "(source scripts/ros_env.sh)", file=sys.stderr)
        return 2

    rclpy.init(args=None, signal_handler_options=_no_rclpy_signals(rclpy))
    _install_signal_handlers()   # after init, so ours are the ones in place
    node = rclpy.create_node("cmd_vel_sequence")
    msg_type = TwistStamped if args.stamped else Twist
    publisher = node.create_publisher(msg_type, args.topic, 10)

    def message(step: Optional[Step]):
        twist = Twist()
        if step is not None:
            twist.linear.x, twist.angular.z = step.linear, step.angular
        if not args.stamped:
            return twist
        stamped = TwistStamped()
        stamped.header.stamp = node.get_clock().now().to_msg()
        stamped.header.frame_id = args.frame_id
        stamped.twist = twist
        return stamped

    status = 0
    try:
        deadline = time.monotonic() + args.wait_subscribers
        while publisher.get_subscription_count() == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        if publisher.get_subscription_count() == 0:
            print(f"cmd_vel_sequence: no subscriber on {args.topic} after {args.wait_subscribers:g} s; "
                  "publishing anyway (check ROS_DOMAIN_ID and config/ros_dds.env)", file=sys.stderr)
        print(f"cmd_vel_sequence: publishing {msg_type.__name__} on {args.topic} at {args.rate:g} Hz\n"
              + describe(steps), file=sys.stderr)
        period = 1.0 / args.rate
        start = time.monotonic()
        while True:
            step = command_at(steps, time.monotonic() - start)
            if step is None:
                break
            publisher.publish(message(step))
            time.sleep(period)
    except _Stop as stop:
        print(f"cmd_vel_sequence: {stop}: stopping the robot", file=sys.stderr)
        status = 130
    except Exception as error:  # noqa: BLE001 - always fall through to the zero command
        print(f"cmd_vel_sequence: {error}: stopping the robot", file=sys.stderr)
        status = 1
    finally:
        # A second signal must not skip the stop: one landing before the handler sees `stopping`
        # raises _Stop here, and the loop simply sends the remaining zeros.
        sent = 0
        while sent < args.stop_repeats:
            try:
                _STATE["stopping"] = True
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                    signal.signal(sig, signal.SIG_IGN)
                publisher.publish(message(None))
                sent += 1
                time.sleep(0.05)
            except _Stop:
                continue
        print(f"cmd_vel_sequence: published zero velocity {sent} time(s)", file=sys.stderr)
        node.destroy_node()
        rclpy.try_shutdown()
    return status


def _no_rclpy_signals(rclpy):
    """Keep our handlers: rclpy's own SIGINT handler would shut the context down before the stop."""
    try:
        from rclpy.signals import SignalHandlerOptions
        return SignalHandlerOptions.NO
    except ImportError:   # older rclpy without the option
        return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("sequence", help='steps "linear,angular,seconds;..." e.g. "0.15,0,5;0,0.5,3;0,0,1"')
    p.add_argument("--topic", default="/cmd_vel")
    p.add_argument("--rate", type=float, default=20.0, help="publish rate in Hz (default 20)")
    p.add_argument("--stamped", action="store_true", help="publish geometry_msgs/TwistStamped")
    p.add_argument("--frame-id", default="base_link", help="header frame for --stamped")
    p.add_argument("--max-linear", type=float, default=1.0, help="refuse faster steps (m/s, default 1.0)")
    p.add_argument("--max-angular", type=float, default=2.0, help="refuse faster turns (rad/s, default 2.0)")
    p.add_argument("--wait-subscribers", type=float, default=3.0,
                   help="seconds to wait for a subscriber before starting (default 3)")
    p.add_argument("--stop-repeats", type=int, default=5, help="zero messages sent at the end (default 5)")
    p.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = p.parse_args(argv)
    if not 1 <= args.rate <= 200:
        p.error("--rate must be between 1 and 200 Hz")
    if args.stop_repeats < 1:
        p.error("--stop-repeats must be at least 1")
    for name in ("max_linear", "max_angular"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:  # nan would disable the limit
            p.error(f"--{name.replace('_', '-')} must be a positive finite number")
    if not args.topic.startswith("/"):
        p.error("--topic must be absolute, like /cmd_vel")
    try:
        steps = parse_sequence(args.sequence, args.max_linear, args.max_angular)
    except ValueError as error:
        p.error(str(error))
    if args.dry_run:
        print(describe(steps))
        return 0
    return run(args, steps)


if __name__ == "__main__":
    sys.exit(main())

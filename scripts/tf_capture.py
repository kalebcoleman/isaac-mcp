"""Snapshot the TF topics the ur5e-dt Isaac Sim extension publishes, as JSON.

This talks to rclpy directly from Isaac Sim 6.2's bundled Humble install, without
requiring the `ros2` CLI. Run it with the sim's own interpreter:

    .venv/bin/python scripts/tf_capture.py --out /tmp/tf-before.json

Then diff two captures to see exactly what a transform-tree change moved. Both
topics come from `isaacsim.ros2.bridge.ROS2PublishTransformTree` nodes in
`exts/ur5e-dt/ur5e_dt/extension.py`: `tf_cam` (camera graph, ~3886) and
`objects_poses_sim` (objects-poses graph, ~5452). Neither graph sets
`inputs:qosProfile`, so the publishers keep the bridge default of keep_last
depth 10 / reliable / volatile (Ros2QoS.hpp, Ros2QoSProfile default ctor). The
subscriber below asks for best_effort, which a reliable publisher also matches.

The last message per (topic, child_frame_id) wins, so the JSON is stable enough
to diff: parent frame, translation, and xyzw rotation, 9 decimals.

Exit status is 0 only if every requested topic delivered at least one message.
"""
import argparse
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROS_CORE = Path('exts') / 'isaacsim.ros2.core' / 'humble'
DDS_ENV_FILES = (
    ROOT / 'config' / 'ros_dds.env',
)
DEFAULT_TOPICS = ('tf_cam', 'objects_poses_sim')
# Set once we have re-execed, so a stubborn LD_LIBRARY_PATH cannot loop forever.
REEXEC_MARK = 'UR5E_DT_TF_CAPTURE_REEXEC'

_ASSIGN = re.compile(r'^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$')
_BRACED = r'\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}'
_VAR = re.compile(rf'\$(?:{_BRACED}|([A-Za-z_][A-Za-z0-9_]*))')


def ros_dirs():
    """Locate the bundled Humble rclpy import dir and native lib dir.

    Resolved from the interpreter's own isaacsim install (no import of
    `isaacsim` itself, which would boot Kit) so this keeps working if the venv
    moves.
    """
    try:
        spec = importlib.util.find_spec('isaacsim')
    except (ImportError, ValueError) as exc:
        sys.exit(f'tf_capture: cannot find isaacsim in this interpreter ({exc}); '
                 'run with an Isaac Sim interpreter, such as .venv/bin/python')
    locations = list(getattr(spec, 'submodule_search_locations', None) or ())
    if not locations:
        sys.exit('tf_capture: isaacsim is not an installed package in this interpreter; '
                 'run with an Isaac Sim interpreter, such as .venv/bin/python')
    base = Path(locations[0]) / ROS_CORE
    rclpy_dir, lib_dir = base / 'rclpy', base / 'lib'
    if not rclpy_dir.is_dir() or not lib_dir.is_dir():
        sys.exit(f'tf_capture: bundled ROS 2 install not found under {base}')
    return rclpy_dir, lib_dir


def _expand(raw, values):
    """Expand $NAME / ${NAME} / ${NAME:-default} from the env, then the file."""

    def replace(match):
        name = match.group(1) or match.group(3)
        default = match.group(2) or ''
        return os.environ.get(name, values.get(name, default))

    return _VAR.sub(replace, raw)


def _parse_env_file(path):
    """Parse the sim's `ros_dds.env` without sourcing it through a shell."""
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = _ASSIGN.match(line)
        if not match:
            continue
        name, raw = match.group(1), match.group(2).strip()
        if len(raw) > 1 and raw[0] == raw[-1] and raw[0] in '"\'':
            raw = raw[1:-1]
        if '$(dirname' in raw:
            # `_ISAAC_DDS_DIR="$(cd "$(dirname ...)" && pwd)"`: the profile sits
            # next to the env file that names it.
            values[name] = str(path.parent)
            continue
        values[name] = _expand(raw, values)
    return values


def apply_dds_env():
    """Adopt the sim's DDS island, leaving anything already exported alone.

    Domain, localhost_only and the Cyclone profile must match the publisher or
    nothing is discovered at all (see the header of ros_dds.env).
    """
    values = {}
    for path in DDS_ENV_FILES:
        if path.is_file():
            values = _parse_env_file(path)
            break
    values.setdefault('RMW_IMPLEMENTATION', 'rmw_cyclonedds_cpp')
    values.setdefault('ROS_DISTRO', 'humble')
    for name, value in values.items():
        if not name.startswith('_'):
            os.environ.setdefault(name, value)


def ensure_native_libs(lib_dir):
    """Add the bundled ROS 2 libs to LD_LIBRARY_PATH, re-execing if needed.

    The loader reads LD_LIBRARY_PATH only at process start, so it cannot be
    fixed from inside a live interpreter.
    """
    entries = [part for part in os.environ.get('LD_LIBRARY_PATH', '').split(os.pathsep) if part]
    if str(lib_dir) in entries or os.environ.get(REEXEC_MARK) == '1':
        return
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = os.pathsep.join([str(lib_dir), *entries])
    env[REEXEC_MARK] = '1'
    argv = list(getattr(sys, 'orig_argv', None)
                or [sys.executable, str(Path(__file__).resolve())])
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(sys.executable, argv, env)


def _floats(*values):
    """Round to 9 decimals and normalise -0.0, so captures diff cleanly."""
    out = []
    for value in values:
        rounded = round(float(value), 9)
        out.append(0.0 if rounded == 0 else rounded)
    return out


def capture(topics, duration):
    """Subscribe for `duration` seconds; return (frames, counts) keyed by topic."""
    import rclpy
    from rclpy.executors import ExternalShutdownException
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
    from tf2_msgs.msg import TFMessage

    frames = {topic: {} for topic in topics}
    counts = {topic: 0 for topic in topics}
    # best_effort matches the bridge default (reliable) and would also match a
    # best_effort publisher; deep history so a slow spin loop drops nothing.
    qos = QoSProfile(
        depth=100,
        history=HistoryPolicy.KEEP_LAST,
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )

    rclpy.init(args=None)
    node = Node('ur5e_dt_tf_capture')
    try:
        for topic in topics:
            def on_msg(msg, topic=topic):
                counts[topic] += 1
                for transform in msg.transforms:
                    frames[topic][transform.child_frame_id] = {
                        'parent': transform.header.frame_id,
                        'translation': _floats(
                            transform.transform.translation.x,
                            transform.transform.translation.y,
                            transform.transform.translation.z,
                        ),
                        'rotation_xyzw': _floats(
                            transform.transform.rotation.x,
                            transform.transform.rotation.y,
                            transform.transform.rotation.z,
                            transform.transform.rotation.w,
                        ),
                    }

            names = [f'/{topic.lstrip("/")}']
            namespace = os.environ.get('ROS_NAMESPACE', '').strip('/')
            if namespace:
                # The bridge prefixes topic names with the graph's ROS2Context
                # namespace, i.e. ROS_NAMESPACE.
                names.append(f'/{namespace}/{topic.lstrip("/")}')
            for name in names:
                node.create_subscription(TFMessage, name, on_msg, qos)

        deadline = time.monotonic() + duration
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            rclpy.spin_once(node, timeout_sec=max(0.01, min(0.2, remaining)))
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()

    return frames, counts


def write_json(path, frames, counts):
    payload = {
        'topics': {
            topic: {
                'frames': {
                    child: frames[topic][child] for child in sorted(frames[topic])
                },
                'msg_count': counts[topic],
            }
            for topic in sorted(frames)
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')


def main():
    parser = argparse.ArgumentParser(
        description='Record the ur5e-dt TF topics (tf_cam, objects_poses_sim) to JSON.',
    )
    parser.add_argument('--out', required=True, type=Path, help='JSON file to write.')
    parser.add_argument('--duration', type=float, default=5.0,
                        help='Seconds to listen (default: 5).')
    parser.add_argument('--topics', nargs='+', default=list(DEFAULT_TOPICS),
                        help='Topics to record (default: tf_cam objects_poses_sim).')
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error('--duration must be positive')

    rclpy_dir, lib_dir = ros_dirs()
    apply_dds_env()
    ensure_native_libs(lib_dir)
    sys.path.insert(0, str(rclpy_dir))

    try:
        frames, counts = capture(args.topics, args.duration)
    except ImportError as exc:
        sys.exit(f'tf_capture: cannot import rclpy/tf2_msgs from {rclpy_dir}: {exc}')

    write_json(args.out, frames, counts)

    empty = []
    for topic in args.topics:
        print(f'{topic}: {counts[topic]} msgs, {len(frames[topic])} frames')
        if not counts[topic]:
            empty.append(topic)
    if empty:
        sys.stdout.flush()
        print(f'tf_capture: no messages on {", ".join(empty)}; is the sim playing '
              f'and the action graph built?', file=sys.stderr)
        return 1
    print(f'wrote {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

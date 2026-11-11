"""State that outlives an extension reload: the recording process and the viewport camera publisher."""

import os
import threading
import json
from .settings import BASE_OUTPUT_DIR


# Module-level recording manager — survives hot-reloads because we guard against re-init.
# Hot-reload re-executes the module, but _recording_mgr persists if already set.
class _RecordingState:
    """Holds ffmpeg process and recording state outside the extension class."""
    def __init__(self):
        self.ffmpeg_proc = None
        self.frame_sub = None
        self.recording = False
        self.output_path = None
        self.frame_count = 0
        self.start_time = 0
        self.fps = 30
        self.last_print = 0
        self.error = None
        self.pacer = None
        self.capture_token = None
        self.recording_lock = threading.RLock()
        self.stopping = False
        # Each Kit process must own its recorder state. A shared state file
        # makes separate simulator instances mistake each other's recording for theirs.
        self.state_dir = (os.path.join(BASE_OUTPUT_DIR, ".recorder-state", str(os.getpid())) if BASE_OUTPUT_DIR
                          else f"/tmp/isaacsim_recording-{os.getpid()}")
        self.state_file = os.path.join(self.state_dir, "recorder_state.json")

    def save_state(self):
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump({"recording": True, "output_path": self.output_path, "fps": self.fps}, f)

    def clear_state(self):
        if os.path.exists(self.state_file):
            os.remove(self.state_file)

    def is_recording(self):
        if getattr(self, "stopping", False):
            return True
        if self.recording and self.ffmpeg_proc is not None:
            return True
        # Check state file as fallback
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    state = json.load(f)
                return state.get("recording", False)
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return False


# Guard: don't re-create if already exists from previous hot-reload
if '_recording_mgr' not in globals():
    _recording_mgr = _RecordingState()


class _ViewportCameraPublisher:
    """Publishes viewport frames to ROS2 topics using capture_viewport_to_buffer.
    Zero extra GPU render cost — reuses the already-rendered viewport frame.
    Survives hot-reloads via module-level singleton.

    Uses a single persistent rclpy node to avoid stale DDS participants
    (destroy_node doesn't immediately release the DDS participant, so
    recreating nodes causes ghost publishers to linger in `ros2 topic info`).
    """

    def __init__(self):
        self._publishers = {}  # {topic_name: {pub, sub, camera_path, active}}
        self._rclpy_initialized = False
        self._node = None  # single persistent node shared across all topics

    def _ensure_rclpy(self):
        if not self._rclpy_initialized:
            import rclpy
            try:
                rclpy.init()
            except RuntimeError as e:
                # Isaac's ROS 2 bridge may already have initialized the process-wide context.
                if 'only be called once' not in str(e):
                    raise
            self._rclpy_initialized = True

    def _ensure_node(self):
        """Create the shared rclpy node on first use."""
        import rclpy
        if self._node is None:
            self._ensure_rclpy()
            self._node = rclpy.create_node("isaacsim_viewport_publisher")
            print(f"[ViewportPub] Created shared node: {self._node.get_name()}")

    def is_rclpy_available(self):
        try:
            import rclpy
            return True
        except ImportError:
            return False

    def start(self, camera_prim_path, topic_name, frame_id="camera_link"):
        """Start publishing viewport frames for a camera to a ROS2 topic."""
        import rclpy
        import omni.kit.viewport.utility as vp_utils
        from sensor_msgs.msg import Image

        # Stop existing publisher for this topic first (prevents stale handles)
        if topic_name in self._publishers:
            if self._publishers[topic_name]["active"]:
                print(f"[ViewportPub] Already publishing on /{topic_name}")
            self.stop(topic_name)

        self._ensure_node()

        # Headless / no-window: capture_viewport_to_buffer + subscribe_to_frame_change HANG the build
        # (they wait on presented frames that never come; under webrtc the off-thread capture during the
        # reset storm crashes — same omni.ui-off-main-thread class as the log-redirect crash). The
        # workspace-camera ROS stream is a visualization nicety the grasp loop does not need. DETECT
        # headless via /app/window/enabled (False under --no-window): get_active_viewport() is NOT
        # reliable — the full experience makes a windowless ViewportAPI even headless (non-None).
        import carb.settings as _carb_settings
        if not bool(_carb_settings.get_settings().get("/app/window/enabled")):
            print("[ViewportPub] Headless (/app/window/enabled=False) — skipping viewport frame publisher.")
            return
        viewport = vp_utils.get_active_viewport()
        pub = self._node.create_publisher(Image, f"/{topic_name}", 10)

        entry = {
            "pub": pub,
            "sub": None,
            "camera_path": camera_prim_path,
            "frame_id": frame_id,
            "active": True,
        }

        def on_capture(buffer, buffer_size, width, height, fmt):
            import ctypes, array
            if not entry["active"]:
                return
            try:
                ctypes.pythonapi.PyCapsule_GetPointer.restype = ctypes.c_void_p
                ctypes.pythonapi.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
                ptr = ctypes.pythonapi.PyCapsule_GetPointer(buffer, None)

                # Single copy from C buffer → array.array (0.3ms for 3.6MB)
                # array.array('B') hits the fast path in Image.data setter (O(1), no element validation)
                c_arr = (ctypes.c_ubyte * buffer_size).from_address(ptr)
                a = array.array('B')
                a.frombytes(c_arr)

                # Publish RGBA directly — no RGB conversion needed (saves ~12ms/frame)
                msg = Image()
                msg.header.frame_id = entry["frame_id"]
                msg.height = height
                msg.width = width
                msg.encoding = "rgba8"
                msg.is_bigendian = False
                msg.step = width * 4
                msg.data = a  # O(1) fast path, no isinstance() per byte
                entry["pub"].publish(msg)
            except Exception:
                pass

        def on_frame(event):
            if not entry["active"]:
                return
            vp_utils.capture_viewport_to_buffer(viewport, on_capture)

        entry["sub"] = viewport.subscribe_to_frame_change(on_frame)
        self._publishers[topic_name] = entry
        print(f"[ViewportPub] Started publishing /{topic_name} from {camera_prim_path}")

    def stop(self, topic_name):
        """Stop publishing a specific topic.
        Destroys only the publisher (not the node), so DDS participants are
        properly released when the publisher goes out of scope."""
        if topic_name not in self._publishers:
            return
        entry = self._publishers[topic_name]
        entry["active"] = False
        entry["sub"] = None
        # Destroy the publisher through the node — this releases its DDS writer
        if self._node is not None:
            try:
                self._node.destroy_publisher(entry["pub"])
            except Exception as e:
                print(f"[ViewportPub] destroy_publisher warning: {e}")
        del self._publishers[topic_name]
        print(f"[ViewportPub] Stopped publishing /{topic_name}")

    def stop_all(self):
        """Stop all viewport publishers."""
        for topic in list(self._publishers.keys()):
            self.stop(topic)

    def is_active(self, topic_name=None):
        if topic_name:
            return topic_name in self._publishers and self._publishers[topic_name]["active"]
        return any(e["active"] for e in self._publishers.values())

    def get_status(self):
        return {t: {"camera": e["camera_path"], "active": e["active"]} for t, e in self._publishers.items()}


if '_viewport_pub' not in globals():
    _viewport_pub = _ViewportCameraPublisher()

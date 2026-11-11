"""Recording: offline renders, realtime capture and the recording tools."""

import omni.usd
import omni.ui as ui
import asyncio
import os
import threading
from typing import Dict, Any
from .recording import FramePacer
from .settings import VIDEOS_DIR
from .capture_state import _recording_mgr


class Video:
    """Part of DigitalTwin (extension.py), which mixes it in."""

    # ==================== Video Recording ====================
    def _open_videos_folder(self):
        """Open the videos output folder in the system file manager."""
        import subprocess
        folder = VIDEOS_DIR
        os.makedirs(folder, exist_ok=True)
        # Use dbus to open folder — avoids Qt plugin conflicts inside Isaac Sim
        env = os.environ.copy()
        env["DISPLAY"] = ":0"
        # Remove Isaac Sim's Qt plugin path that breaks external Qt apps
        for key in list(env.keys()):
            if "QT_PLUGIN" in key or "QT_QPA" in key:
                del env[key]
        subprocess.Popen(["xdg-open", folder], env=env)

    def _get_recording_filename(self):
        """Get filename from UI field, or generate auto name like ros2 recorder."""
        from datetime import datetime
        custom = self._rec_filename_field.model.as_string.strip()
        if custom:
            if not custom.endswith(".mp4"):
                custom += ".mp4"
            return custom
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        return f"isaacsim_{timestamp}.mp4"

    def _refresh_camera_list(self):
        """Scan the stage for all Camera prims and rebuild the camera combo box."""
        from pxr import UsdGeom
        stage = omni.usd.get_context().get_stage()
        cameras = ["Active Viewport"]
        for prim in stage.Traverse():
            if prim.IsA(UsdGeom.Camera):
                cameras.append(str(prim.GetPath()))
        # Rebuild combo box inside its frame container
        self._rec_camera_frame.clear()
        with self._rec_camera_frame:
            self._rec_camera_combo = ui.ComboBox(0, *cameras, width=250)
        print(f"Found {len(cameras) - 1} camera(s): {cameras[1:]}")

    def _get_selected_camera_path(self):
        """Return the prim path of the selected camera, or None for active viewport."""
        model = self._rec_camera_combo.model
        idx = model.get_item_value_model().as_int
        if idx == 0:
            return None  # Active Viewport
        items = model.get_item_children()
        if idx < len(items):
            return model.get_item_value_model(items[idx]).as_string
        return None

    # --- Offline Recording (frame capture, blocks physics) ---
    async def _record_offline_from_ui(self):
        """Record per-physics-step video using settings from the UI panel."""
        duration = self._rec_duration_field.model.as_float
        if duration <= 0:
            print("Error: Set a duration > 0 for per-physics-step recording.")
            return
        fps = self._offline_fps_field.model.as_int
        res_idx = self._offline_res_combo.model.get_item_value_model().as_int
        res_options = ["640x480", "1280x720", "1920x1080", "2560x1440", "3840x2160"]
        w, h = [int(x) for x in res_options[res_idx].split("x")]
        camera_path = self._get_selected_camera_path()
        file_name = self._get_recording_filename().replace(".mp4", "")  # _record_offline appends .mp4
        await self._record_offline(duration_sec=duration, fps=fps, res_width=w, res_height=h,
                                    camera_path=camera_path, file_name=file_name)

    async def _record_offline(self, duration_sec=5, fps=30, res_width=1920, res_height=1080,
                               camera_path=None, output_dir=None, file_name="offline_recording"):
        """Record viewport to MP4 using per-physics-step frame capture."""
        if output_dir is None:
            output_dir = VIDEOS_DIR
        import omni.kit.app
        mgr = omni.kit.app.get_app().get_extension_manager()
        if not mgr.is_extension_enabled("omni.kit.capture.viewport"):
            mgr.set_extension_enabled_immediate("omni.kit.capture.viewport", True)
            await omni.kit.app.get_app().next_update_async()

        from omni.kit.capture.viewport import CaptureExtension, CaptureOptions
        import omni.kit.viewport.utility as vp_utils

        capture = CaptureExtension.get_instance()
        viewport = vp_utils.get_active_viewport()

        os.makedirs(output_dir, exist_ok=True)

        options = CaptureOptions()
        options.camera = camera_path if camera_path else viewport.camera_path.pathString
        options.output_folder = output_dir
        options.file_name = file_name
        options.file_type = ".mp4"
        options.fps = fps
        options.start_frame = 1
        options.end_frame = int(duration_sec * fps)
        options.res_width = res_width
        options.res_height = res_height
        options.overwrite_existing_frames = True
        capture.options = options

        print(f"Per-physics-step recording: {duration_sec}s ({options.end_frame} frames at {fps}fps, {res_width}x{res_height})...")
        if not capture.start():
            print("Error: Failed to start video capture")
            return None

        while not capture.done:
            await omni.kit.app.get_app().next_update_async()

        outputs = capture.get_outputs()
        if outputs:
            self._last_video_label.text = f"Saved: {outputs[0]}"
        print(f"Per-physics-step recording complete: {outputs}")
        return outputs

    async def _record_assemble_disassemble_test(self):
        """Offline test: assemble at 1s, disassemble at 3s, 5s total."""
        import omni.kit.app
        app = omni.kit.app.get_app()

        mgr = app.get_extension_manager()
        if not mgr.is_extension_enabled("omni.kit.capture.viewport"):
            mgr.set_extension_enabled_immediate("omni.kit.capture.viewport", True)
            await app.next_update_async()

        from omni.kit.capture.viewport import CaptureExtension, CaptureOptions
        import omni.kit.viewport.utility as vp_utils

        capture = CaptureExtension.get_instance()
        viewport = vp_utils.get_active_viewport()

        # Use UI settings for resolution
        res_idx = self._offline_res_combo.model.get_item_value_model().as_int
        res_options = ["640x480", "1280x720", "1920x1080", "2560x1440", "3840x2160"]
        w, h = [int(x) for x in res_options[res_idx].split("x")]
        fps = self._offline_fps_field.model.as_int
        camera_path = self._get_selected_camera_path()

        output_dir = VIDEOS_DIR
        os.makedirs(output_dir, exist_ok=True)

        total_frames = 5 * fps

        options = CaptureOptions()
        options.camera = camera_path if camera_path else viewport.camera_path.pathString
        options.output_folder = output_dir
        options.file_name = "assemble_disassemble_test"
        options.file_type = ".mp4"
        options.fps = fps
        options.start_frame = 1
        options.end_frame = total_frames
        options.res_width = w
        options.res_height = h
        options.overwrite_existing_frames = True
        capture.options = options

        print(f"Starting assemble/disassemble test ({w}x{h}, {fps}fps, 5s)...")
        if not capture.start():
            print("Error: Failed to start video capture")
            return

        frame = [0]
        assemble_done = [False]
        disassemble_done = [False]

        while not capture.done:
            await app.next_update_async()
            frame[0] += 1
            if frame[0] == 1 * fps and not assemble_done[0]:
                print("Test: Assembling objects...")
                self.assemble_objects()
                assemble_done[0] = True
            if frame[0] == 3 * fps and not disassemble_done[0]:
                print("Test: Disassembling objects...")
                self.disassemble_objects()
                disassemble_done[0] = True

        outputs = capture.get_outputs()
        if outputs:
            self._last_video_label.text = f"Saved: {outputs[0]}"
        print(f"Test recording complete: {outputs}")

    # --- Real-Time Recording (ffmpeg, non-blocking) ---
    # State lives in module-level _recording_mgr to survive hot-reloads.
    def _start_rt_from_ui(self):
        """Start real-time recording from UI. Auto-stops after duration if set."""
        if _recording_mgr.is_recording():
            return
        file_name = self._get_recording_filename()
        camera_path = self._get_selected_camera_path()
        duration = self._rec_duration_field.model.as_float
        self._start_realtime_recording(file_name=file_name, camera_path=camera_path)
        if duration > 0:
            asyncio.ensure_future(self._rt_auto_stop(duration, _recording_mgr.capture_token))

    async def _rt_auto_stop(self, duration, token):
        """Auto-stop real-time recording after duration seconds."""
        await asyncio.sleep(duration)
        if _recording_mgr.recording:
            self._stop_realtime_recording(token=token)

    def _start_realtime_recording(self, output_dir=None, file_name="realtime_recording.mp4",
                                   fps=30, camera_path=None):
        """Start piping viewport frames to ffmpeg in real-time."""
        if output_dir is None:
            output_dir = VIDEOS_DIR
        import subprocess
        import omni.kit.viewport.utility as vp_utils

        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, file_name)

        viewport = vp_utils.get_active_viewport()

        if camera_path:
            prim = viewport.stage.GetPrimAtPath(str(camera_path)) if viewport.stage else None
            if not prim or not prim.IsValid() or prim.GetTypeName() != "Camera":
                raise ValueError(f"{camera_path} is not a Camera on this stage, so nothing was recorded.")
            viewport.camera_path = camera_path

        w, h = viewport.get_texture_resolution()

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-nostats",
            "-f", "rawvideo",
            "-pix_fmt", "rgba",
            "-s", f"{w}x{h}",
            "-r", str(fps),
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast",
            "-crf", "18",
            output_path,
        ]
        import time as _time
        mgr = _recording_mgr
        mgr.ffmpeg_proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        mgr.recording = True
        mgr.output_path = output_path
        mgr.frame_count = 0
        mgr.start_time = _time.time()
        mgr.fps = fps
        mgr.last_print = 0
        mgr.error = None
        mgr.pacer = FramePacer(fps, _time.monotonic())
        mgr.recording_lock = threading.RLock()
        mgr.stopping = False
        token = object()
        mgr.capture_token = token

        def on_frame_change(event):
            with mgr.recording_lock:
                if not mgr.recording or mgr.ffmpeg_proc is None or mgr.capture_token is not token:
                    return
                if not mgr.pacer.request_capture(_time.monotonic()):
                    return
            try:
                vp_utils.capture_viewport_to_buffer(
                    viewport,
                    lambda buffer, size, width, height, fmt:
                        self._rt_on_capture(buffer, size, width, height, fmt, token),
                )
            except Exception as e:
                with mgr.recording_lock:
                    if mgr.capture_token is not token:
                        return
                    mgr.pacer.pending = False
                    mgr.error = f"could not capture viewport: {e}"
                self._stop_realtime_recording(pad=False, token=token)

        mgr.frame_sub = viewport.subscribe_to_frame_change(on_frame_change)
        mgr.save_state()

        self._rt_record_btn.enabled = False
        self._rt_stop_btn.enabled = True
        print(f"Real-time recording started: {w}x{h} @ {fps}fps → {output_path}")

    def _rt_on_capture(self, buffer, buffer_size, width, height, fmt, token):
        """Callback for each captured frame — write raw bytes to ffmpeg stdin."""
        import ctypes
        mgr = _recording_mgr
        should_stop = False
        with mgr.recording_lock:
            if not mgr.recording or mgr.ffmpeg_proc is None or mgr.capture_token is not token:
                return
            try:
                ctypes.pythonapi.PyCapsule_GetPointer.restype = ctypes.c_void_p
                ctypes.pythonapi.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
                ptr = ctypes.pythonapi.PyCapsule_GetPointer(buffer, None)
                raw = (ctypes.c_ubyte * buffer_size).from_address(ptr)
                import time as _time
                mgr.pacer.add_frame(bytes(raw), _time.monotonic(), mgr.ffmpeg_proc.stdin.write)
                mgr.frame_count = mgr.pacer.frames
                elapsed = _time.time() - mgr.start_time
                video_dur = mgr.frame_count / mgr.fps
                self._rt_stop_btn.text = f"Stop ({video_dur:.1f}s, {mgr.frame_count}f)"
                sec = int(elapsed)
                if sec > mgr.last_print:
                    mgr.last_print = sec
                    print(f"[RT Record] {elapsed:.1f}s elapsed | {mgr.frame_count} frames | video: {video_dur:.1f}s")
            except (BrokenPipeError, OSError, RuntimeError) as e:
                mgr.error = f"recording failed: {e}"
                should_stop = True
            finally:
                mgr.pacer.pending = False
        if should_stop:
            self._stop_realtime_recording(pad=False, token=token)

    def _stop_realtime_recording(self, pad=True, token=None):
        """Stop real-time recording and finalize the video."""
        mgr = _recording_mgr
        # A recording begun before a hot reload may predate the pacer and lock.
        if not hasattr(mgr, "recording_lock"):
            mgr.recording_lock = threading.RLock()
        with mgr.recording_lock:
            if token is not None and getattr(mgr, "capture_token", None) is not token:
                return
            if getattr(mgr, "stopping", False):
                return
            mgr.stopping = True
            mgr.recording = False
            frame_sub = mgr.frame_sub
            mgr.frame_sub = None
            if pad and mgr.ffmpeg_proc is not None and getattr(mgr, "pacer", None) is not None:
                import time as _time
                try:
                    mgr.pacer.finish(_time.monotonic(), mgr.ffmpeg_proc.stdin.write)
                    mgr.frame_count = mgr.pacer.frames
                except (BrokenPipeError, OSError, RuntimeError) as e:
                    mgr.error = f"recording failed: {e}"
        # Drop the subscription outside the lock: it may wait for an in-flight callback.
        del frame_sub
        if mgr.ffmpeg_proc is not None:
            # Closing stdin gives ffmpeg EOF -> it flushes the encode buffer and writes
            # the moov atom. Large recordings need well over the old 10s to finalize, so a
            # short wait abandoned ffmpeg mid-finalize -> truncated mp4 (no moov atom).
            import signal as _signal
            try:
                mgr.ffmpeg_proc.stdin.close()
            except Exception as e:
                print(f"Warning closing ffmpeg stdin: {e}")
            try:
                exit_code = mgr.ffmpeg_proc.wait(timeout=180)  # generous: let ffmpeg write the moov
                if exit_code and not getattr(mgr, "error", None):
                    detail = mgr.ffmpeg_proc.stderr.read().decode(errors="replace")[-1000:].strip()
                    mgr.error = f"ffmpeg exited with status {exit_code}" + (f": {detail}" if detail else "")
            except Exception as e:
                print(f"ffmpeg finalize exceeded wait ({e}); sending SIGINT to force trailer")
                if not getattr(mgr, "error", None):
                    mgr.error = f"ffmpeg did not finalize normally: {e}"
                try:
                    mgr.ffmpeg_proc.send_signal(_signal.SIGINT)
                    mgr.ffmpeg_proc.wait(timeout=60)
                except Exception as e2:
                    print(f"ffmpeg still not finalized after SIGINT: {e2}")
            mgr.ffmpeg_proc = None
        try:
            self._rt_record_btn.enabled = True
            self._rt_stop_btn.enabled = False
            self._rt_stop_btn.text = "Stop Recording"
            video_dur = round(mgr.frame_count / mgr.fps, 1) if mgr.fps else 0
            self._last_video_label.text = (f"Recording failed: {mgr.error}" if getattr(mgr, "error", None) else
                                           f"Saved: {mgr.output_path} ({video_dur}s, {mgr.frame_count} frames)")
            mgr.clear_state()
        finally:
            mgr.stopping = False
        print(f"Real-time recording stopped: {mgr.frame_count} frames → {mgr.output_path}")

    # --- MCP Recording Handlers ---
    def _cmd_start_recording(self, file_name: str = None, camera: str = None,
                              fps: int = 30, output_dir: str = None) -> Dict[str, Any]:
        """MCP handler: start real-time viewport recording."""
        try:
            print(f"[MCP start_recording] id(mgr)={id(_recording_mgr)} recording={_recording_mgr.recording}")
            if _recording_mgr.is_recording():
                return {"status": "error", "message": "Recording already in progress. Stop it first."}
            if not file_name or file_name == "":
                from datetime import datetime
                timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                file_name = f"isaacsim_{timestamp}.mp4"
            elif not file_name.endswith(".mp4"):
                file_name += ".mp4"
            if not fps or fps <= 0:
                fps = 30
            if camera == "":
                camera = None
            vid_dir = os.path.join(output_dir, "videos") if output_dir else VIDEOS_DIR
            self._start_realtime_recording(output_dir=vid_dir, file_name=file_name, fps=fps, camera_path=camera)
            path = os.path.join(vid_dir, file_name)
            return {
                "status": "success",
                "message": f"Recording started → {path}",
                "file_path": path,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _cmd_stop_recording(self) -> Dict[str, Any]:
        """MCP handler: stop real-time viewport recording."""
        try:
            mgr = _recording_mgr
            print(f"[MCP stop_recording] mgr.recording={mgr.recording} ffmpeg={mgr.ffmpeg_proc is not None} "
                  f"state_file_exists={os.path.exists(mgr.state_file)} id(mgr)={id(mgr)}")
            if not mgr.is_recording():
                return {"status": "error", "message": getattr(mgr, "error", None) or "No recording in progress."}
            if mgr.ffmpeg_proc is None:
                # State file says recording but process lost (e.g. after reload)
                mgr.clear_state()
                return {"status": "error", "message": "Recording process lost (extension reloaded?). State cleared."}
            path = mgr.output_path
            self._stop_realtime_recording()
            if getattr(mgr, "error", None):
                return {"status": "error", "message": mgr.error, "file_path": path,
                        "frame_count": mgr.frame_count}
            return {
                "status": "success",
                "message": f"Recording stopped: {mgr.frame_count} frames",
                "file_path": path,
                "frame_count": mgr.frame_count,
                "video_duration": round(mgr.frame_count / mgr.fps, 2) if mgr.fps else 0,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _cmd_get_recording_status(self) -> Dict[str, Any]:
        """MCP handler: get current recording status."""
        mgr = _recording_mgr
        if not mgr.is_recording():
            if getattr(mgr, "error", None):
                return {"status": "error", "message": mgr.error}
            return {"status": "success", "message": "Status: Not recording"}
        import time as _time
        elapsed = _time.time() - mgr.start_time if mgr.start_time else 0
        video_dur = round(mgr.frame_count / mgr.fps, 2) if mgr.fps else 0
        return {
            "status": "success",
            "message": (
                f"Status: Recording in progress\n"
                f"  Output: {mgr.output_path}\n"
                f"  FPS: {mgr.fps}\n"
                f"  Frames recorded: {mgr.frame_count}\n"
                f"  Video duration: {video_dur}s\n"
                f"  Wall-clock elapsed: {round(elapsed, 1)}s"
            ),
            "recording": True,
            "file_path": mgr.output_path,
            "frame_count": mgr.frame_count,
            "fps": mgr.fps,
            "video_duration": video_dur,
            "elapsed": round(elapsed, 1),
        }

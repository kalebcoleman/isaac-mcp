"""The video frame count must follow wall time, not the viewport's redraw rate."""

import ast
import asyncio
import ctypes
import importlib.util
import os
import sys
import threading
from pathlib import Path
from types import MethodType, ModuleType, SimpleNamespace
from typing import Any, Dict
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "exts/ur5e-dt/ur5e_dt/extension.py"
spec = importlib.util.spec_from_file_location("twin_recording", ROOT / "exts/ur5e-dt/ur5e_dt/recording.py")
recording = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recording)


@pytest.mark.parametrize("fps", [10, 30, 60])
def test_fast_viewport_produces_requested_wall_clock_duration(fps):
    pacer = recording.FramePacer(fps, started=100.0)
    written = []
    captures = 0
    for frame in range(120):
        now = 100.0 + frame / 120.0
        if pacer.request_capture(now):
            captures += 1
            pacer.add_frame(bytes([frame]), now, written.append)
            pacer.pending = False
    pacer.finish(101.0, written.append)
    assert captures <= fps
    assert pacer.frames == len(written) == fps


def test_slow_viewport_repeats_latest_frame_to_keep_wall_clock_duration():
    pacer = recording.FramePacer(10, started=0.0)
    written = []
    pacer.add_frame(b"first", 0.0, written.append)
    pacer.add_frame(b"second", 0.5, written.append)
    pacer.finish(1.0, written.append)
    assert written == [b"first"] * 4 + [b"second"] * 6
    assert pacer.frames == 10


def test_only_one_capture_can_be_pending():
    pacer = recording.FramePacer(10, started=0.0)
    assert pacer.request_capture(0.0)
    assert not pacer.request_capture(1.0)
    pacer.pending = False
    assert pacer.request_capture(1.0)


def test_stalled_or_missing_viewport_frames_fail_instead_of_infinite_catchup():
    empty = recording.FramePacer(10, started=0.0)
    with pytest.raises(RuntimeError, match="did not deliver"):
        empty.finish(1.0, lambda frame: None)
    stalled = recording.FramePacer(10, started=0.0)
    stalled.add_frame(b"frame", 0.0, lambda frame: None)
    with pytest.raises(RuntimeError, match="five seconds behind"):
        stalled.finish(10.0, lambda frame: None)


def test_stop_handler_reports_an_encoder_failure(tmp_path):
    tree = ast.parse(SOURCE.read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_cmd_stop_recording")
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    mgr = SimpleNamespace(recording=True, ffmpeg_proc=object(), state_file=str(tmp_path / "state"),
                          output_path="bad.mp4", frame_count=4, is_recording=lambda: True)
    namespace = {"Dict": Dict, "Any": Any, "os": os, "_recording_mgr": mgr}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    owner = SimpleNamespace(_stop_realtime_recording=lambda: setattr(mgr, "error", "ffmpeg failed"))
    result = namespace["_cmd_stop_recording"](owner)
    assert result["status"] == "error"
    assert result["message"] == "ffmpeg failed"


def test_status_reports_a_recording_that_failed_and_stopped():
    tree = ast.parse(SOURCE.read_text())
    method = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_cmd_get_recording_status")
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    mgr = SimpleNamespace(error="viewport capture failed", is_recording=lambda: False)
    namespace = {"Dict": Dict, "Any": Any, "_recording_mgr": mgr}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    result = namespace["_cmd_get_recording_status"](None)
    assert result == {"status": "error", "message": "viewport capture failed"}


def test_stale_capture_error_cannot_stop_a_new_recording():
    tree = ast.parse(SOURCE.read_text())
    method = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_stop_realtime_recording")
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    subscription = object()
    process = object()
    mgr = SimpleNamespace(recording=True, stopping=False, recording_lock=threading.RLock(),
                          capture_token=object(), frame_sub=subscription, ffmpeg_proc=process)
    namespace = {"threading": threading, "_recording_mgr": mgr}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    namespace["_stop_realtime_recording"](None, pad=False, token=object())
    assert mgr.recording
    assert mgr.frame_sub is subscription
    assert mgr.ffmpeg_proc is process


def test_auto_stop_targets_the_recording_that_created_it():
    tree = ast.parse(SOURCE.read_text())
    method = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "_rt_auto_stop")
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {"asyncio": asyncio, "_recording_mgr": SimpleNamespace(recording=True)}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    token = object()
    owner = SimpleNamespace(_stop_realtime_recording=Mock())
    asyncio.run(namespace["_rt_auto_stop"](owner, 0, token))
    owner._stop_realtime_recording.assert_called_once_with(token=token)


def test_viewport_callbacks_are_paced_and_stop_finalizes(monkeypatch, tmp_path):
    tree = ast.parse(SOURCE.read_text())
    names = {"_start_realtime_recording", "_rt_on_capture", "_stop_realtime_recording"}
    methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    module = ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[]))
    now = [0.0]
    clock = ModuleType("time")
    clock.monotonic = lambda: now[0]
    clock.time = lambda: now[0] + 100.0
    monkeypatch.setitem(sys.modules, "time", clock)

    raw = ctypes.create_string_buffer(b"rgba")
    monkeypatch.setattr(ctypes.pythonapi.PyCapsule_New, "restype", ctypes.py_object)
    monkeypatch.setattr(ctypes.pythonapi.PyCapsule_New, "argtypes",
                        [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p])
    # The production callback sets these; restore the host interpreter after the test.
    monkeypatch.setattr(ctypes.pythonapi.PyCapsule_GetPointer, "restype", ctypes.pythonapi.PyCapsule_GetPointer.restype)
    monkeypatch.setattr(ctypes.pythonapi.PyCapsule_GetPointer, "argtypes", ctypes.pythonapi.PyCapsule_GetPointer.argtypes)
    capsule = ctypes.pythonapi.PyCapsule_New(ctypes.addressof(raw), None, None)
    callbacks = []
    viewport = SimpleNamespace(get_texture_resolution=lambda: (1, 1),
                               subscribe_to_frame_change=lambda cb: callbacks.append(cb) or object())
    utility = ModuleType("omni.kit.viewport.utility")
    utility.get_active_viewport = lambda: viewport
    utility.capture_viewport_to_buffer = lambda vp, cb: cb(capsule, 4, 1, 1, None)
    omni = ModuleType("omni")
    kit = ModuleType("omni.kit")
    viewport_module = ModuleType("omni.kit.viewport")
    omni.kit, kit.viewport, viewport_module.utility = kit, viewport_module, utility
    for name, imported in {"omni": omni, "omni.kit": kit, "omni.kit.viewport": viewport_module,
                           "omni.kit.viewport.utility": utility}.items():
        monkeypatch.setitem(sys.modules, name, imported)

    frames = []
    stdin = SimpleNamespace(write=lambda data: frames.append(data), close=Mock())
    proc = SimpleNamespace(stdin=stdin, wait=Mock(return_value=0))
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: proc)
    mgr = SimpleNamespace(save_state=Mock(), clear_state=Mock(), frame_sub=None)
    namespace = {"os": os, "threading": threading, "FramePacer": recording.FramePacer,
                 "_recording_mgr": mgr, "VIDEOS_DIR": str(tmp_path)}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    owner = SimpleNamespace(_rt_record_btn=SimpleNamespace(enabled=True),
                            _rt_stop_btn=SimpleNamespace(enabled=False, text=""),
                            _last_video_label=SimpleNamespace(text=""))
    for name in names:
        setattr(owner, name, MethodType(namespace[name], owner))

    owner._start_realtime_recording(output_dir=str(tmp_path), fps=10)
    for tick in range(120):
        now[0] = tick / 120.0
        callbacks[0](None)
    now[0] = 1.0
    owner._stop_realtime_recording()
    assert len(frames) == mgr.frame_count == 10
    assert all(frame == b"rgba" for frame in frames)
    assert not mgr.recording and not mgr.stopping and mgr.error is None
    stdin.close.assert_called_once()
    proc.wait.assert_called_once_with(timeout=180)

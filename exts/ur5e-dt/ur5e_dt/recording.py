"""Pace viewport frames for a constant-rate, wall-clock recording."""

import math


class FramePacer:
    """Sample at most once per scheduled interval and fill short rendering gaps.

    ffmpeg's raw-video input assigns timestamps from its declared FPS; it does
    not know when frames were actually captured. Keep the number sent in step
    with elapsed time, repeating the last available frame when rendering is slow.
    """

    def __init__(self, fps, started):
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.fps = fps
        self.started = started
        self.next_capture_index = 0
        self.next_capture = started
        self.pending = False
        self.frames = 0
        self.last_frame = None

    def request_capture(self, now):
        tolerance = 2 * max(math.ulp(now), math.ulp(self.next_capture))
        if self.pending or now + tolerance < self.next_capture:
            return False
        self.pending = True
        # Keep deadlines anchored to the recording start. Advancing from the
        # actual callback time accumulates scheduling delay and can halve the
        # effective capture rate when redraws are just slower than a deadline.
        index = max(self.next_capture_index + 1, self._elapsed_ticks(now) + 1)
        deadline = self.started + index / self.fps
        # Floating-point rounding can place an exact deadline at ``now``.
        if deadline <= now:
            index += 1
            deadline = self.started + index / self.fps
        self.next_capture_index = index
        self.next_capture = deadline
        return True

    def _elapsed_ticks(self, now):
        # Subtracting large monotonic timestamps can put an exact deadline a
        # few floating-point units below its intended tick.
        tolerance = 2 * max(math.ulp(now), math.ulp(self.started)) * self.fps
        return int(max(0.0, now - self.started) * self.fps + tolerance)

    def _count_due(self, now, *, first_frame):
        target = self._elapsed_ticks(now)
        if first_frame:
            target = max(1, target)
        due = max(0, target - self.frames)
        # Do not make Kit's render thread write minutes of catch-up frames if a
        # viewport stops delivering frames. Surface the failure instead.
        if due > max(1, int(self.fps * 5)):
            raise RuntimeError("viewport frame capture fell more than five seconds behind")
        return due

    def add_frame(self, frame, now, write):
        due = self._count_due(now, first_frame=True)
        previous = self.last_frame if self.last_frame is not None else frame
        for _ in range(max(0, due - 1)):
            write(previous)
            self.frames += 1
        if due:
            write(frame)
            self.frames += 1
        self.last_frame = frame

    def finish(self, now, write):
        if self.last_frame is None:
            raise RuntimeError("viewport did not deliver any recording frames")
        due = self._count_due(now, first_frame=False)
        for _ in range(due):
            write(self.last_frame)
            self.frames += 1

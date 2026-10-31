"""Pace viewport frames for a constant-rate, wall-clock recording."""


class FramePacer:
    """Sample at most once per frame interval and fill short rendering gaps.

    ffmpeg's raw-video input assigns timestamps from its declared FPS; it does
    not know when frames were actually captured. Keep the number sent in step
    with elapsed time, repeating the last available frame when rendering is slow.
    """

    def __init__(self, fps, started):
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.fps = fps
        self.started = started
        self.next_capture = started
        self.pending = False
        self.frames = 0
        self.last_frame = None

    def request_capture(self, now):
        if self.pending or now < self.next_capture:
            return False
        self.pending = True
        self.next_capture = now + 1.0 / self.fps
        return True

    def _count_due(self, now, *, first_frame):
        target = int(max(0.0, now - self.started) * self.fps)
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

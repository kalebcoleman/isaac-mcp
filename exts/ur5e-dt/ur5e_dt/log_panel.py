"""The panel's log tabs: stdout, stderr and carb messages, drained on the main thread."""

import contextlib
import omni.ui as ui
import sys
import carb


class LogRedirector:
    """Redirects stdout/stderr to both the original stream and a UI callback."""
    _ur5e_log_redirector = True
    def __init__(self, original, callback, throttle_seconds=0.0):
        self._original = original
        self._callback = callback
        self._throttle = throttle_seconds
        self._last_msgs = {}  # dedup cache: msg -> timestamp

    def write(self, text):
        self._original.write(text)
        stripped = text.strip()
        if not stripped:
            return
        if self._throttle > 0:
            import time as _t
            now = _t.monotonic()
            if stripped in self._last_msgs and now - self._last_msgs[stripped] < self._throttle:
                return
            self._last_msgs[stripped] = now
            if len(self._last_msgs) > 200:
                cutoff = now - self._throttle * 3
                self._last_msgs = {k: v for k, v in self._last_msgs.items() if v > cutoff}
        self._callback(text.rstrip("\n"))

    def flush(self):
        self._original.flush()


class LogPanel:
    """Part of DigitalTwin (extension.py), which mixes it in."""

    def _setup_log_redirect(self):
        if self._closing:
            return
        # A prior extension module may have been reloaded, so class identity is
        # not reliable. The marker also handles an inactive retired redirector.
        while getattr(sys.stdout, "_ur5e_log_redirector", False):
            sys.stdout = sys.stdout._original
        while getattr(sys.stderr, "_ur5e_log_redirector", False):
            sys.stderr = sys.stderr._original
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self._stdout_redirector = LogRedirector(self._orig_stdout, self._append_ext_log)
        self._stderr_redirector = LogRedirector(self._orig_stderr, lambda t: self._append_ext_log(f"[ERROR] {t}"), throttle_seconds=2.0)
        sys.stdout = self._stdout_redirector
        sys.stderr = self._stderr_redirector
        logging = carb.logging.acquire_logging()
        self._carb_log_sub = logging.add_logger(self._on_carb_log)

    @contextlib.contextmanager
    def without_carb_log_capture(self):
        """Suspend this panel's Python log listener during a blocking native call. The listener needs the
        GIL on whichever thread logs. A native call that holds the GIL while it waits for worker threads
        (the 5.x URDF importer) then never finishes, because a worker that logs waits for the GIL."""
        logging = carb.logging.acquire_logging()
        installed = getattr(self, "_carb_log_sub", None)
        if installed is not None:
            logging.remove_logger(installed)
            self._carb_log_sub = None
        try:
            yield
        finally:
            if installed is not None and not getattr(self, "_closing", False):
                self._carb_log_sub = logging.add_logger(self._on_carb_log)

    def _on_carb_log(self, source, level, filename, linenum, msg):
        # carb levels: VERBOSE=-2, INFO=-1, WARN=0, ERROR=1, FATAL=2
        import time as _t
        now = _t.monotonic()
        # Throttle: skip duplicate messages within 2 seconds
        key = (source, level, msg)
        last = getattr(self, '_carb_log_last', {})
        if key in last and now - last[key] < 2.0:
            return
        last[key] = now
        # Evict stale entries periodically
        if len(last) > 200:
            cutoff = now - 5.0
            last = {k: v for k, v in last.items() if v > cutoff}
        self._carb_log_last = last

        if level >= carb.logging.LEVEL_ERROR:
            self._append_sys_log(f"[ERROR] [{source}] {msg}")
        elif level == carb.logging.LEVEL_WARN:
            self._append_sys_log(f"[WARN] [{source}] {msg}")

        # elif level == carb.logging.LEVEL_INFO:
        #     self._append_sys_log(f"[INFO] [{source}] {msg}")
        # else:
        #     self._append_sys_log(f"[VERBOSE] [{source}] {msg}")
    def _teardown_log_redirect(self):
        stdout = getattr(self, "_stdout_redirector", None)
        stderr = getattr(self, "_stderr_redirector", None)
        # run_python may be temporarily capturing stdout when the extension is
        # disabled. Do not replace that capture; make a subsequently restored
        # old redirector harmless instead.
        if stdout is not None:
            stdout._callback = lambda _text: None
            if sys.stdout is stdout:
                sys.stdout = self._orig_stdout
        if stderr is not None:
            stderr._callback = lambda _text: None
            if sys.stderr is stderr:
                sys.stderr = self._orig_stderr
        if hasattr(self, "_carb_log_sub") and self._carb_log_sub is not None:
            logging = carb.logging.acquire_logging()
            logging.remove_logger(self._carb_log_sub)
            self._carb_log_sub = None

    def _log_color(self, text):
        # Matches Isaac Sim script editor colors (0xAABBGGRR format)
        if "[ERROR]" in text or "[FATAL]" in text:
            return 0xFFAAB1F6  # #F6B1AA - light coral
        elif "[WARN]" in text:
            return 0xFF4ACBDF  # #DFCB4A - yellow/gold
        # elif "[INFO]" in text:
        #     return 0xFFEBBA79  # #79BAEB - light blue
        # elif "[VERBOSE]" in text:
        #     return 0xFFBABABA  # #BABABA - gray
        return 0xFFFFFFFF  # white

    def _add_log_label(self, vstack, scroll, text):
        with vstack:
            ui.Label(text, alignment=ui.Alignment.LEFT_TOP, word_wrap=True,
                     style={"color": self._log_color(text), "font_size": 13, "margin": 0},
                     height=0)
        scroll.scroll_y = scroll.scroll_y_max + 100

    def _rebuild_log_vstack(self, vstack, lines):
        vstack.clear()
        with vstack:
            for line in lines:
                ui.Label(line, alignment=ui.Alignment.LEFT_TOP, word_wrap=True,
                         style={"color": self._log_color(line), "font_size": 13, "margin": 0},
                         height=0)

    # Log capture is a producer/consumer queue, NOT per-line UI scheduling. The producers
    # (_on_carb_log + the stdout/stderr LogRedirector) can fire from ANY thread — including PhysX
    # worker threads AND the main thread synchronously inside world.reset()/the first physics step.
    # They must therefore touch ZERO omni.ui (touching omni.ui off-frame/off-main-thread crashed the
    # process — see _run_on_main_thread). So they only append to a list (GIL-atomic) and flip a dirty
    # flag; the single per-frame _drain_log_buffer (subscribed to the app update stream, runs on the
    # main thread between frames) rebuilds the capped UI. This also avoids scheduling thousands of
    # call_soon callbacks during the reset log-storm.
    def _append_ext_log(self, text):
        self._ext_log_lines.append(text)
        if len(self._ext_log_lines) > 500:
            self._ext_log_lines = self._ext_log_lines[-500:]
        self._log_dirty = True

    def _append_sys_log(self, text):
        self._sys_log_lines.append(text)
        if len(self._sys_log_lines) > 500:
            self._sys_log_lines = self._sys_log_lines[-500:]
        self._log_dirty = True

    def _drain_log_buffer(self, _event=None):
        """Main-thread, once-per-frame: rebuild the log panels from the buffers if dirty."""
        if not getattr(self, "_log_dirty", False):
            return
        self._log_dirty = False
        try:
            if getattr(self, "_ext_log_vstack", None):
                self._rebuild_log_vstack(self._ext_log_vstack, self._ext_log_lines)
                if getattr(self, "_ext_log_scroll", None):
                    self._ext_log_scroll.scroll_y = self._ext_log_scroll.scroll_y_max + 100
            if getattr(self, "_sys_log_vstack", None):
                self._rebuild_log_vstack(self._sys_log_vstack, self._sys_log_lines)
                if getattr(self, "_sys_log_scroll", None):
                    self._sys_log_scroll.scroll_y = self._sys_log_scroll.scroll_y_max + 100
        except Exception:
            pass

    def _switch_log_tab(self, tab):
        self._active_log_tab = tab
        is_ext = tab == "extension"
        self._ext_log_scroll.visible = is_ext
        self._sys_log_scroll.visible = not is_ext

    def _copy_logs(self):
        lines = self._ext_log_lines if self._active_log_tab == "extension" else self._sys_log_lines
        try:
            import subprocess
            text = "\n".join(lines)
            proc = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
            proc.communicate(text.encode())
        except Exception:
            import omni.kit.clipboard
            omni.kit.clipboard.copy("\n".join(lines))

    def _clear_logs(self):
        if self._active_log_tab == "extension":
            self._ext_log_lines.clear()
            self._ext_log_vstack.clear()
        else:
            self._sys_log_lines.clear()
            self._sys_log_vstack.clear()

"""Exercise the twin's log redirect lifecycle without importing Kit."""

import ast
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
from types import SimpleNamespace


def log_classes():
    source = Path(__file__).resolve().parents[2] / "exts/ur5e-dt/ur5e_dt/extension.py"
    tree = ast.parse(source.read_text())
    redirector = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LogRedirector")
    twin = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DigitalTwin")
    methods = {"_setup_log_redirect", "_teardown_log_redirect", "_run_on_main_thread"}
    shim = ast.ClassDef(name="Shim", bases=[], keywords=[],
                        body=[n for n in twin.body if isinstance(n, ast.FunctionDef) and n.name in methods],
                        decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[redirector, shim], type_ignores=[]))
    logging = SimpleNamespace(add_logger=lambda cb: cb, remove_logger=lambda cb: None)
    namespace = {"sys": sys, "carb": SimpleNamespace(logging=SimpleNamespace(acquire_logging=lambda: logging))}
    exec(compile(module, str(source), "exec"), namespace)
    return namespace["Shim"]


def instance(shim):
    obj = shim()
    obj._closing = False
    obj.lines = []
    obj._append_ext_log = obj.lines.append
    obj._on_carb_log = lambda *args: None
    obj.pending = []
    obj._main_loop = SimpleNamespace(call_soon_threadsafe=obj.pending.append)
    return obj


def test_reload_inside_stdout_capture_restores_new_log_target():
    shim = log_classes()
    first, second = instance(shim), instance(shim)
    original_stdout, original_stderr = sys.stdout, sys.stderr
    try:
        first._setup_log_redirect()
        capture = io.StringIO()
        with redirect_stdout(capture):
            first._teardown_log_redirect()
            second._run_on_main_thread(second._setup_log_redirect)
            print("captured during reload")
        assert "captured during reload" in capture.getvalue()
        # run_python has now restored the old stream; next Kit tick installs
        # the new extension's redirector and unwraps the retired one.
        second.pending.pop()()
        print("new extension log")
        assert "new extension log" in second.lines
        assert "new extension log" not in first.lines
    finally:
        second._teardown_log_redirect()
        sys.stdout, sys.stderr = original_stdout, original_stderr


def test_deferred_setup_is_noop_after_shutdown():
    shim = log_classes()
    obj = instance(shim)
    original_stdout, original_stderr = sys.stdout, sys.stderr
    obj._run_on_main_thread(obj._setup_log_redirect)
    obj._closing = True
    obj.pending.pop()()
    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr

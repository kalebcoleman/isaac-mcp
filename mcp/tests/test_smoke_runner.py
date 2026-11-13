"""Smoke routing and engine guards never require a simulator in unit tests."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke.py"


def runner():
    spec = importlib.util.spec_from_file_location("smoke_runner", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_list_is_offline():
    result = subprocess.run([sys.executable, "-S", str(SCRIPT), "all", "--list"],
                            capture_output=True, text=True, check=True)
    assert "Running simulator" in result.stdout and "GPU" in result.stdout
    for checks in runner().TOOLSETS.values():
        for check in checks:
            assert check in result.stdout
    assert "no checks registered" in result.stdout


@pytest.mark.parametrize("actual,requested", [("physx", "newton"), ("newton", "physx"), ("unknown", "physx")])
def test_backend_mismatch_refuses_before_checks(tmp_path, monkeypatch, capsys, actual, requested):
    module = runner()
    calls = []

    class Client:
        def __init__(self, host, port):
            assert host == "test-host"
            self.port = port

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def call(self, name, arguments):
            calls.append(name)
            return {"app_version": "test-build", "pid": 123} if name == "status" else {"result": actual}

    def forbidden(*args):
        pytest.fail("A validator ran despite backend mismatch")

    monkeypatch.setattr(module, "execute", forbidden)
    monkeypatch.setenv("ISAAC_MCP_HOST", "original")
    assert module.main(["scene", "--host", "test-host", "--backend", requested,
                        "--output-dir", str(tmp_path)], client_factory=Client) == 2
    assert calls == ["status", "run_python"]
    assert "refusing to run" in capsys.readouterr().out
    result = json.loads((tmp_path / "results.json").read_text())
    first = result["checks"][0]
    assert first["status"] == "fail" and first["backend"] == actual
    assert first["simulator_build"] == "test-build" and first["seconds"] >= 0
    assert all(row["status"] == "not_run" for row in result["checks"][1:])
    assert module.os.environ["ISAAC_MCP_HOST"] == "original"


def test_success_records_actual_backend(tmp_path, monkeypatch):
    module = runner()

    class Client:
        port = 8766

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def call(self, name, arguments):
            return {"app_version": "test-build"} if name == "status" else {"result": "newton"}

    executed = []
    monkeypatch.setattr(module, "execute", lambda name, *args: executed.append(name))
    assert module.main(["physics", "--backend", "newton", "--output-dir", str(tmp_path)], Client) == 0
    rows = json.loads((tmp_path / "results.json").read_text())["checks"]
    assert executed == module.TOOLSETS["physics"]
    assert all(row["status"] == "pass" and row["backend"] == "newton" for row in rows)


def test_generic_module_import_does_not_start_kit():
    module = runner().load_check("validate_generic_scene")
    assert callable(module.run_checks)

"""Exercise the shell verifier without requiring a running simulator."""

import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("failing_check", [None, "joint_limits"])
def test_verifier_uses_selected_python_and_reports_failed_checks(tmp_path, failing_check):
    root = tmp_path / "checkout with spaces"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (root / "mcp").mkdir()
    checkpoints = root / "checkpoints"
    checkpoints.mkdir()
    (checkpoints / "ur5e-dt-workstation-person-view-portable.json").write_text(
        '{"checkpoint": "existing.usda"}'
    )
    (checkpoints / "existing.usda").touch()
    source = Path(__file__).resolve().parents[2] / "scripts" / "verify_ur5e_dt.sh"
    script = scripts / source.name
    shutil.copy(source, script)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    interpreter = bin_dir / "selected python"
    interpreter.write_text(
        '#!/bin/bash\n'
        'if [[ "${2:-}" == *"/${FAIL_CHECK:-never}.py" ]]; then\n'
        '  echo "check failed without a structured error"\n'
        '  exit 7\n'
        'fi\n'
        'echo "selected interpreter ran"\n'
    )
    interpreter.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    env = os.environ | {
        "UR5E_DT_PYTHON": str(interpreter),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAIL_CHECK": failing_check or "never",
    }
    result = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True)
    assert "selected interpreter ran" in result.stdout
    assert result.returncode == (1 if failing_check else 0), result.stdout + result.stderr
    if failing_check:
        assert "FAILED: joint_limits" in result.stdout
    else:
        assert "all checks passed" in result.stdout


@pytest.mark.parametrize("client_status", [0, 2])
def test_bridge_exec_propagates_client_status(tmp_path, monkeypatch, client_status):
    script = Path(__file__).resolve().parents[2] / "scripts" / "bridge_exec.py"
    check = tmp_path / "check.py"
    check.write_text("result = 1")
    monkeypatch.setattr(sys, "argv", [str(script), str(check)])
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=client_status))
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(script), run_name="__main__")
    assert exc.value.code == client_status

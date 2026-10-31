"""Exercise the shell verifier without requiring a running simulator."""

import os
from pathlib import Path
import re
import runpy
import shlex
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


def _generic_scene_example(interpreter):
    """Exercise the actual documented command, substituting only the interpreter."""
    root = Path(__file__).resolve().parents[2]
    documentation = (root / "docs" / "VERIFICATION.md").read_text()
    section = documentation.split("## Generic scene API validation\n", 1)[1]
    example = section.split("```bash\n", 1)[1].split("\n```", 1)[0]
    placeholder = '"/path/to/isaacsim-env/bin/python"'
    assert example.count(placeholder) == 1
    return root, example.replace(placeholder, shlex.quote(str(interpreter)), 1)


@pytest.mark.parametrize("errexit", [False, True])
@pytest.mark.parametrize(("interpreter_status", "marker", "expected_status"), [
    (0, True, 0),
    (0, False, 1),
    (17, True, 17),
    (23, False, 23),
])
def test_documented_generic_scene_command_preserves_status_and_log(
        tmp_path, errexit, interpreter_status, marker, expected_status):
    interpreter = tmp_path / "fake sim python"
    interpreter.write_text(
        '#!/bin/bash\n'
        '[[ "${OMNI_KIT_ACCEPT_EULA:-}" == YES ]] || exit 98\n'
        '[[ "${1:-}" == scripts/validate_generic_scene.py ]] || exit 97\n'
        'printf "simulated simulator log\\n"\n'
        'if [[ "$TEST_MARKER" == 1 ]]; then printf "GENERIC_SCENE_VALIDATION={}\\n"; fi\n'
        'exit "$TEST_INTERPRETER_STATUS"\n'
    )
    interpreter.chmod(0o755)
    logs = tmp_path / "logs with spaces"
    logs.mkdir()
    root, example = _generic_scene_example(interpreter)
    env = os.environ | {"TMPDIR": str(logs), "TEST_MARKER": "1" if marker else "0",
                        "TEST_INTERPRETER_STATUS": str(interpreter_status)}
    args = ["bash", "-e", "-c", example] if errexit else ["bash", "-c", example]
    result = subprocess.run(args, cwd=root, env=env, text=True, capture_output=True)

    assert result.returncode == expected_status, result.stdout + result.stderr
    assert ("GENERIC_SCENE_VALIDATION=" in result.stdout) is (marker and interpreter_status == 0)
    log_match = re.search(r"^Full log: (.+)$", result.stdout, re.MULTILINE)
    assert log_match, result.stdout + result.stderr
    log_path = Path(log_match.group(1))
    assert log_path.parent == logs
    assert "simulated simulator log" in log_path.read_text()
    if interpreter_status:
        assert f"Validation process failed (exit {interpreter_status})" in result.stderr
    elif not marker:
        assert "Validation success marker missing" in result.stderr


def test_documented_generic_scene_command_fails_if_log_cannot_be_created(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_mktemp = bin_dir / "mktemp"
    fake_mktemp.write_text("#!/bin/sh\nexit 12\n")
    fake_mktemp.chmod(0o755)
    root, example = _generic_scene_example(tmp_path / "unused python")
    env = os.environ | {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(["bash", "-e", "-c", example], cwd=root, env=env,
                            text=True, capture_output=True)
    assert result.returncode != 0
    assert "Full log:" not in result.stdout
    assert "Could not create validation log" in result.stderr

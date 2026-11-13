"""The twin launcher configures one shared bridge without starting Kit."""

import os
from pathlib import Path
import socket
import subprocess

import pytest


@pytest.mark.parametrize("port_source", ["default", "environment", "argument", "ephemeral"])
def test_launcher_sets_shared_port_and_waits_for_twin_registration(tmp_path, port_source):
    root = tmp_path / "checkout"
    root.mkdir()
    repo = Path(__file__).resolve().parents[2]
    launcher = root / "run.sh"
    launcher.write_bytes((repo / "run.sh").read_bytes())
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "isaac_ros_libs.sh").write_bytes((repo / "scripts" / "isaac_ros_libs.sh").read_bytes())
    fake_python = tmp_path / "python"
    fake_python.write_text(
        '#!/bin/bash\n'
        'if [[ "$1" == "-c" ]]; then echo /tmp/mock-isaacsim; exit 0; fi\n'
        'printf "args=%s\\n" "$*"\n'
        'printf "%s %s %s\\n" "$UR5E_DT_PORT" "$ISAAC_MCP_PORT" "$ISAAC_MCP_DEFER_START"\n'
    )
    fake_python.chmod(0o755)
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 8766 if port_source == "default" else 0))
        except OSError:
            pytest.skip("default twin port is already in use")
        port = probe.getsockname()[1]
    env = os.environ | {"UR5E_DT_PYTHON": str(fake_python), "OMNI_KIT_ACCEPT_EULA": "YES"}
    for name in ("UR5E_DT_PORT", "ISAAC_SIM_PORT", "ISAAC_MCP_PORT", "ISAAC_MCP_DEFER_START"):
        env.pop(name, None)
    if port_source == "ephemeral":
        port = 0
    args = ["bash", str(launcher), "--ur5e-dt", "--headless"]
    if port_source == "environment":
        env["UR5E_DT_PORT"] = str(port)
    elif port_source == "argument":
        env["UR5E_DT_PORT"] = "1"
        args += ["--port", str(port)]
    elif port_source == "ephemeral":
        args += ["--port", "0"]
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--enable ur5e-dt" in result.stdout.strip().splitlines()[-2]
    assert result.stdout.strip().splitlines()[-1] == f"{port} {port} 1"


def test_launcher_always_enables_twin_tools(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    repo = Path(__file__).resolve().parents[2]
    launcher = root / "run.sh"
    launcher.write_bytes((repo / "run.sh").read_bytes())
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "isaac_ros_libs.sh").write_bytes((repo / "scripts" / "isaac_ros_libs.sh").read_bytes())
    fake_python = tmp_path / "python"
    fake_python.write_text(
        '#!/bin/bash\n'
        'if [[ "$1" == "-c" ]]; then echo /tmp/mock-isaacsim; exit 0; fi\n'
        'printf "defer=%s\\n" "$ISAAC_MCP_DEFER_START"\n'
        'printf "arg=%s\\n" "$@"\n'
    )
    fake_python.chmod(0o755)
    env = os.environ | {"UR5E_DT_PYTHON": str(fake_python), "ISAAC_MCP_DEFER_START": "1"}
    env.pop("UR5E_DT_SHOW_PANEL", None)
    args = ["bash", str(launcher), "--port", "0"]
    result = subprocess.run(args,
                            env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert "defer=1" in lines
    assert "arg=isaac.mcp.bridge" in lines
    assert "arg=ur5e-dt" in lines

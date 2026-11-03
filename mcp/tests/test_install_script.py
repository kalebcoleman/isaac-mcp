"""Check that host-only installation preserves an existing ffmpeg executable."""

import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("existing", ["file", "symlink", "missing", "owned"])
def test_host_install_preserves_unrelated_ffmpeg(tmp_path, existing):
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    ffmpeg = bin_dir / "ffmpeg"
    original = b"user's existing ffmpeg\n"
    target = tmp_path / "existing executable"
    if existing == "file":
        ffmpeg.write_bytes(original)
    elif existing == "symlink":
        target.write_bytes(original)
        ffmpeg.symlink_to(target)
    elif existing == "owned":
        ffmpeg.write_text("# ffmpeg shim: prefer the static build shipped with imageio-ffmpeg.\nold shim\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    uv = fake_bin / "uv"
    uv.write_text(
        '#!/bin/sh\n'
        'mkdir -p "$HOME/.local/bin"\n'
        'printf "#!/bin/sh\\nexit 0\\n" > "$HOME/.local/bin/isaac-mcp"\n'
        'chmod +x "$HOME/.local/bin/isaac-mcp"\n'
    )
    uv.chmod(0o755)
    env = os.environ | {"HOME": str(home), "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
    script = Path(__file__).resolve().parents[2] / "install.sh"
    result = subprocess.run(["bash", str(script), "--no-sim"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    if existing in {"file", "symlink"}:
        assert ffmpeg.read_bytes() == original
        assert ffmpeg.is_symlink() == (existing == "symlink")
        assert "preserving existing" in result.stderr
    else:
        assert "ffmpeg shim: prefer the static build" in ffmpeg.read_text()
        assert "old shim" not in ffmpeg.read_text()
        assert os.access(ffmpeg, os.X_OK)



REPO = Path(__file__).resolve().parents[2]


def _fake_isaac(venv: Path, version: str) -> Path:
    """A pip-style environment that isaac_env.py reports as Isaac Sim *version* (never executed)."""
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin" / "python"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    info = venv / "lib" / "python3.12" / "site-packages" / f"isaacsim-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: isaacsim\nVersion: {version}\n")
    return python


def _sandbox(tmp_path):
    """A throwaway checkout (install.sh + isaac_env.py + an empty mcp/) with stub uv, claude,
    nvidia-smi and df, so nothing real is searched, installed or registered."""
    root = tmp_path / "ws" / "isaac-mcp"
    (root / "scripts").mkdir(parents=True)
    (root / "mcp").mkdir()
    for rel in ("install.sh", "scripts/isaac_env.py"):
        (root / rel).write_bytes((REPO / rel).read_bytes())
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    scripts = {
        "uv": f'echo "uv $*" >> "{log}"\n'
              'mkdir -p "$HOME/.local/bin"\n'
              'printf "#!/bin/sh\\nexit 0\\n" > "$HOME/.local/bin/isaac-mcp"\n'
              'chmod +x "$HOME/.local/bin/isaac-mcp"\n',
        "claude": f'echo "claude $*" >> "{log}"\n',
        "nvidia-smi": 'echo "NVIDIA RTX PRO 6000 Blackwell, 595.99"\n',
        "df": 'printf "Avail\\n500G\\n"\n',
    }
    for name, body in scripts.items():
        (stubs / name).write_text("#!/bin/sh\n" + body)
        (stubs / name).chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if k not in {"ISAAC_MCP_PYTHON", "UR5E_DT_PYTHON", "ISAAC_SIM_PYTHON", "ISAACSIM_PATH",
                        "ISAAC_SIM_PATH", "ISAAC_PATH", "CONDA_PREFIX", "VIRTUAL_ENV",
                        "ISAAC_MCP_WORKSPACE", "OMNI_KIT_ACCEPT_EULA"}}
    env |= {"HOME": str(home), "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"}
    return root, env, log


def _install(root, env, *args):
    return subprocess.run(["bash", str(root / "install.sh"), *args], env=env, capture_output=True, text=True)


def _calls(log):
    return log.read_text() if log.exists() else ""


def test_an_explicitly_named_isaac_sim_is_used_and_saved_for_the_client(tmp_path):
    root, env, log = _sandbox(tmp_path)
    env["ISAAC_MCP_PYTHON"] = str(_fake_isaac(tmp_path / "lab-isaac", "6.0.0"))
    result = _install(root, env, "--claude-code")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "using the Isaac Sim already installed" in result.stdout
    assert "uv sync" not in _calls(log)  # no ~20 GB download
    assert "not the tested 6.2 release" in result.stderr
    assert f"ISAAC_MCP_PYTHON={env['ISAAC_MCP_PYTHON']}" in _calls(log)  # sim__launch will find it


def test_an_isaac_sim_next_to_the_checkout_is_found_without_any_variable(tmp_path):
    root, env, log = _sandbox(tmp_path)
    python = _fake_isaac(root.parent / "other-project" / ".venv", "6.2.0.0")
    result = _install(root, env, "--claude-code")
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(python) in result.stdout and "uv sync" not in _calls(log)
    assert f"ISAAC_MCP_PYTHON={python}" in _calls(log)


def test_without_any_isaac_sim_the_locked_environment_is_downloaded(tmp_path):
    import glob

    if glob.glob("/opt/nvidia/isaac-sim*") or Path("/isaac-sim").exists():
        pytest.skip("this machine has a system-wide Isaac Sim, which the search always sees")
    root, env, log = _sandbox(tmp_path)
    result = _install(root, env, "--accept-eula")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "uv sync" in _calls(log)


def test_download_sim_downloads_even_with_an_existing_isaac_sim(tmp_path):
    root, env, log = _sandbox(tmp_path)
    env["ISAAC_MCP_PYTHON"] = str(_fake_isaac(tmp_path / "lab-isaac", "6.2.0.0"))
    result = _install(root, env, "--accept-eula", "--download-sim")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "using the Isaac Sim already installed" not in result.stdout
    assert "uv sync" in _calls(log)


@pytest.mark.parametrize("value", ["/no/such/python", "/usr/bin/python3"])
def test_a_named_install_that_is_not_isaac_sim_6_stops_instead_of_downloading(tmp_path, value):
    root, env, log = _sandbox(tmp_path)
    env["ISAAC_MCP_PYTHON"] = value
    result = _install(root, env, "--accept-eula")
    assert result.returncode == 1 and "does not point at an Isaac Sim 6.x" in result.stderr
    assert "uv sync" not in _calls(log)


def test_install_refuses_no_sim_with_download_sim(tmp_path):
    root, env, _ = _sandbox(tmp_path)
    result = _install(root, env, "--no-sim", "--download-sim")
    assert result.returncode == 2 and "cannot be combined" in result.stderr


def _fake_standalone(root: Path, version: str) -> Path:
    """A standalone install root (python.sh + VERSION), as NVIDIA's zip/workstation install."""
    root.mkdir(parents=True)
    (root / "python.sh").write_text("#!/bin/sh\nexit 0\n")
    (root / "python.sh").chmod(0o755)
    (root / "VERSION").write_text(f"{version}\n")
    return root


def test_a_valid_standalone_isaacsim_path_is_used(tmp_path):
    # Round 2 finding 1: the README's ISAACSIM_PATH advice must work.
    root, env, log = _sandbox(tmp_path)
    env["ISAACSIM_PATH"] = str(_fake_standalone(tmp_path / "isaac-sim", "6.2.0"))
    result = _install(root, env, "--claude-code")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "standalone:ISAACSIM_PATH" in result.stdout and "uv sync" not in _calls(log)
    assert "ISAAC_MCP_PYTHON=" in _calls(log)


def test_a_typo_is_reported_even_when_another_environment_is_active(tmp_path):
    # Round 2 finding 2: an activated venv must not silently replace what the user named.
    root, env, log = _sandbox(tmp_path)
    env["ISAAC_MCP_PYTHON"] = "/no/such/python"
    env["VIRTUAL_ENV"] = str(_fake_isaac(tmp_path / "active-venv", "6.2.0.0").parent.parent)
    result = _install(root, env, "--claude-code")
    assert result.returncode == 1 and "ISAAC_MCP_PYTHON is set but does not point" in result.stderr
    assert "uv sync" not in _calls(log) and "claude mcp add" not in _calls(log)


def test_the_opencode_snippet_is_valid_json_for_unusual_paths(tmp_path):
    import json
    import re

    root, env, _ = _sandbox(tmp_path)
    env["ISAAC_MCP_PYTHON"] = str(_fake_isaac(tmp_path / 'odd"name\\dir', "6.2.0.0"))
    result = _install(root, env, "--print-opencode")
    assert result.returncode == 0, result.stdout + result.stderr
    body = re.search(r'"environment": (\{.*\})', result.stdout).group(1)
    assert json.loads(body)["ISAAC_MCP_PYTHON"] == env["ISAAC_MCP_PYTHON"]

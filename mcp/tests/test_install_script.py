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


def _fake_install_env(tmp_path, isaac_version=None):
    """HOME with a stub `uv` that logs its arguments, and optionally an installed Isaac Sim."""
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    log = tmp_path / "uv.log"
    uv = fake_bin / "uv"
    uv.write_text(
        '#!/bin/sh\n'
        f'echo "$@" >> "{log}"\n'
        'mkdir -p "$HOME/.local/bin"\n'
        'printf "#!/bin/sh\\nexit 0\\n" > "$HOME/.local/bin/isaac-mcp"\n'
        'chmod +x "$HOME/.local/bin/isaac-mcp"\n'
    )
    uv.chmod(0o755)
    env = os.environ | {"HOME": str(home), "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                        "OMNI_KIT_ACCEPT_EULA": "YES"}
    for name in ("ISAAC_MCP_PYTHON", "UR5E_DT_PYTHON", "ISAAC_SIM_PYTHON", "ISAACSIM_PATH",
                 "ISAAC_SIM_PATH", "ISAAC_PATH", "CONDA_PREFIX", "VIRTUAL_ENV"):
        env.pop(name, None)
    if isaac_version:
        venv = tmp_path / "lab-isaac"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").write_text("#!/bin/sh\nexit 0\n")
        (venv / "bin" / "python").chmod(0o755)
        info = venv / "lib" / "python3.12" / "site-packages" / f"isaacsim-{isaac_version}.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: isaacsim\nVersion: {isaac_version}\n")
        env["ISAAC_MCP_PYTHON"] = str(venv / "bin" / "python")
    return env, log


def _run_install(env, *args):
    script = Path(__file__).resolve().parents[2] / "install.sh"
    if (script.parent / ".venv" / "bin" / "python").exists():
        pytest.skip("this checkout has its own .venv, which install.sh re-syncs by design")
    return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True)


def test_install_uses_an_already_installed_isaac_sim_instead_of_downloading(tmp_path):
    env, log = _fake_install_env(tmp_path, isaac_version="6.0.0")
    result = _run_install(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "using the Isaac Sim already installed" in result.stdout
    assert "sync" not in log.read_text()  # no ~20 GB download
    assert "tool install" in log.read_text()


def test_install_downloads_only_when_asked_even_with_an_existing_isaac_sim(tmp_path):
    env, log = _fake_install_env(tmp_path, isaac_version="6.2.0.0")
    result = _run_install(env, "--download-sim")
    # The download path also checks disk/GPU first; either it ran `uv sync` or a precondition stopped it.
    assert "using the Isaac Sim already installed" not in result.stdout
    if result.returncode == 0:
        assert "sync" in log.read_text()


def test_install_refuses_no_sim_with_download_sim(tmp_path):
    env, _ = _fake_install_env(tmp_path)
    result = _run_install(env, "--no-sim", "--download-sim")
    assert result.returncode == 2 and "cannot be combined" in result.stderr

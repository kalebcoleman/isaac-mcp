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

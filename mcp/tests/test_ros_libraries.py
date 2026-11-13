"""Bundled ROS libraries are found in every Isaac Sim layout and exported by run.sh."""

import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _ros_lib_dir(site, distro="humble"):
    script = f'. "{ROOT}/scripts/isaac_ros_libs.sh"; isaac_ros_lib_dir "{site}" "{distro}"'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


@pytest.mark.parametrize("site, lib", [
    ("venv/isaacsim", "venv/isaacsim/exts/isaacsim.ros2.core/humble/lib"),  # pip 6.x
    ("venv/isaacsim", "venv/isaacsim/exts/isaacsim.ros2.bridge/humble/lib"),  # pip 5.1
    ("app/python_packages/isaacsim", "app/exts/isaacsim.ros2.core/humble/lib"),  # standalone 6.x
    ("app/python_packages/isaacsim", "app/exts/isaacsim.ros2.bridge/humble/lib"),  # standalone 5.1
])
def test_ros_libraries_are_found_in_every_layout(tmp_path, site, lib):
    (tmp_path / site).mkdir(parents=True)
    (tmp_path / lib).mkdir(parents=True)
    found = _ros_lib_dir(tmp_path / site)
    assert found.returncode == 0 and found.stdout.strip() == str(tmp_path / lib)


def test_missing_ros_libraries_are_a_failure_not_a_bogus_path(tmp_path):
    (tmp_path / "exts/isaacsim.ros2.core/jazzy/lib").mkdir(parents=True)
    found = _ros_lib_dir(tmp_path, "humble")
    assert found.returncode == 1 and found.stdout == ""


def _run_sh_with_app(tmp_path, *lib):
    site = tmp_path / "app/python_packages/isaacsim"
    site.mkdir(parents=True)
    if lib:
        (tmp_path / lib[0]).mkdir(parents=True)
    python = tmp_path / "python.sh"
    python.write_text(f'#!/bin/sh\ncase "$1" in -c) case "$2" in *__file__*) echo {site};; esac; exit 0;; esac\n'
                      'echo "LD=$LD_LIBRARY_PATH"\n')
    python.chmod(0o755)
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "ISAAC_MCP_PYTHON": str(python), "ROS_DISTRO": "humble"}
    return subprocess.run(["bash", str(ROOT / "run.sh"), "--headless"], capture_output=True, text=True, env=env)


def test_run_sh_exports_the_standalone_app_ros_libraries(tmp_path):
    result = _run_sh_with_app(tmp_path, "app/exts/isaacsim.ros2.core/humble/lib")
    assert result.returncode == 0, result.stderr
    assert f"LD={tmp_path}/app/exts/isaacsim.ros2.core/humble/lib" in result.stdout


def test_run_sh_warns_instead_of_exporting_a_missing_ros_folder(tmp_path):
    result = _run_sh_with_app(tmp_path)
    assert "no ROS 2 humble libraries" in result.stderr
    assert "LD=\n" in result.stdout


def test_no_launcher_still_assumes_the_pip_layout():
    for name in ("run.sh", "scripts/check_machine.sh"):
        text = (ROOT / name).read_text()
        assert "isaac_ros_libs.sh" in text and "/exts/isaacsim.ros2." not in text, name

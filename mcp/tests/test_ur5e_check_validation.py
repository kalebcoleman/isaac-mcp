"""Validate realistic verifier responses without needing Isaac Sim."""

import copy
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest


validator = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "validate_ur5e_check.py"))
validate = validator["validate"]
JOINTS = validator["JOINTS"]
ROOTS = validator["ROOTS"]


@pytest.fixture
def responses():
    return {
        "quick_start": {"status": "success"},
        "scene_prims": {"result": {
            "world_children": [*ROOTS, "/World/Vention"], "ur5e_present": True,
            "rg2_present": True, "gravity_disabled_on_robot_links": [],
        }},
        "scene_physics": {"result": {
            "scenes": [{"path": "/physicsScene", "type": "PhysicsScene",
                        "gpu_dynamics": False, "broadphase": "MBP", "steps_per_second": 120}],
            "initialized": True, "runtime_dt": 1 / 120,
        }},
        "gravity_robot_chain": {"result": {root: {
            "rigid_bodies": 1, "gravity_enabled": [], "gravity_enabled_count": 0,
        } for root in ROOTS}},
        "joint_limits": {"result": {name: {"lower_deg": low, "upper_deg": high}
                                    for name, (low, high) in JOINTS.items()}},
        "drive_settings": {"result": {"live_arm_drives": {name: {
            "stiffness": 100.0, "damping": 10.0, "max_force": 200.0,
        } for name in JOINTS}}},
        "single_joint_motion": {"result": {
            "joint": "elbow_joint", "commanded_rad": -0.1, "achieved_rad": -0.099,
            "returned_error_rad": 0.001, "all_joints_finite": True,
        }},
        "export_reference": {"result": {
            "written": "checkpoint.usda", "absolute_refs_left": 0, "relative_asset_refs": 4,
            "physics_scene": {"type": "PhysicsScene", "path": "/physicsScene"},
        }},
        "reopen_checkpoint": {"result": {
            "checkpoint_present": True, "opening": True, "sha256_matches_manifest": False,
        }},
    }


@pytest.mark.parametrize("check", [
    "quick_start", "scene_prims", "scene_physics", "gravity_robot_chain", "joint_limits", "drive_settings",
    "single_joint_motion", "export_reference", "reopen_checkpoint",
])
def test_good_observations_pass(check, responses):
    assert validate(check, responses[check]) == []


@pytest.mark.parametrize("check,change,expected", [
    ("quick_start", lambda r: r.update(status="error"), "quick_start"),
    ("scene_prims", lambda r: r["result"].update(ur5e_present=False), "UR5e"),
    ("scene_prims", lambda r: r["result"]["world_children"].remove("/World/Vention"), "Vention"),
    ("scene_physics", lambda r: r["result"]["scenes"].clear(), "exactly one"),
    ("scene_physics", lambda r: r["result"]["scenes"][0].update(type=""), "definition"),
    ("scene_physics", lambda r: r["result"]["scenes"][0].update(gpu_dynamics=True), "CPU"),
    ("scene_physics", lambda r: r["result"]["scenes"][0].update(steps_per_second=60), "120 Hz"),
    ("scene_physics", lambda r: r["result"].update(runtime_dt=1/60), "timestep"),
    ("gravity_robot_chain", lambda r: r["result"][ROOTS[0]].update(gravity_enabled_count=1), "gravity"),
    ("gravity_robot_chain", lambda r: r["result"][ROOTS[0]].update(gravity_enabled_count=False), "gravity"),
    ("gravity_robot_chain", lambda r: r["result"][ROOTS[1]].update(rigid_bodies=0), "rigid bodies"),
    ("joint_limits", lambda r: r["result"]["elbow_joint"].update(lower_deg=-90), "elbow_joint"),
    ("drive_settings", lambda r: r["result"]["live_arm_drives"]["wrist_1_joint"].update(damping=4), "damping"),
    ("single_joint_motion", lambda r: r["result"].update(achieved_rad=0.0), "missed target"),
    ("single_joint_motion", lambda r: r["result"].update(returned_error_rad=float("nan")), "did not return"),
    ("export_reference", lambda r: r["result"].update(absolute_refs_left=1), "absolute"),
    ("export_reference", lambda r: r["result"].update(absolute_refs_left=False), "absolute"),
    ("reopen_checkpoint", lambda r: r["result"].update(checkpoint_present=False), "missing"),
])
def test_bad_observations_are_rejected(check, change, expected, responses):
    response = copy.deepcopy(responses[check])
    change(response)
    assert any(expected in failure for failure in validate(check, response))


def test_missing_result_and_unknown_check_fail():
    assert validate("scene_prims", {})
    assert validate("unknown_check", {"result": {}})


@pytest.mark.parametrize("bad_check", [None, "gravity_robot_chain", "quick_start", "scene_physics"])
def test_verifier_rejects_semantic_failures_with_successful_client(tmp_path, responses, bad_check):
    root = tmp_path / "checkout"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (root / "mcp").mkdir()
    checkpoints = root / "checkpoints"
    checkpoints.mkdir()
    (checkpoints / "ur5e-dt-workstation-person-view-portable.json").write_text(
        '{"checkpoint": "reference.usda"}'
    )
    (checkpoints / "reference.usda").touch()
    repo = Path(__file__).resolve().parents[2]
    for name in ("verify_ur5e_dt.sh", "validate_ur5e_check.py", "check_checkpoint_refs.py"):
        shutil.copy(repo / "scripts" / name, scripts / name)
    if bad_check == "gravity_robot_chain":
        responses[bad_check]["result"][ROOTS[0]]["gravity_enabled_count"] = 1
    elif bad_check == "quick_start":
        responses[bad_check]["status"] = "error"
    elif bad_check == "scene_physics":
        responses[bad_check]["result"]["scenes"][0]["type"] = ""
    response_dir = tmp_path / "responses"
    response_dir.mkdir()
    for name, response in responses.items():
        (response_dir / f"{name}.json").write_text(json.dumps(response))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    fake_python.write_text(
        '#!/bin/bash\n'
        'if [[ "$1" == */validate_ur5e_check.py ]]; then exec "$TEST_PYTHON" "$@"; fi\n'
        'if [[ "$1" == "-m" ]]; then cat "$RESPONSE_DIR/quick_start.json"; exit 0; fi\n'
        'name="${2##*/}"; name="${name%.py}"\n'
        'cat "$RESPONSE_DIR/$name.json"\n'
    )
    fake_python.chmod(0o755)
    sleep = fake_bin / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    env = os.environ | {
        "UR5E_DT_PYTHON": str(fake_python), "TEST_PYTHON": sys.executable,
        "RESPONSE_DIR": str(response_dir), "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "REBUILD": "0", "LOG": "",
    }
    result = subprocess.run(["bash", str(scripts / "verify_ur5e_dt.sh")], env=env, capture_output=True, text=True)
    assert result.returncode == (1 if bad_check else 0), result.stdout + result.stderr
    if bad_check:
        assert f"FAILED: {bad_check}" in result.stdout
        if bad_check == "scene_physics":
            assert "== single_joint_motion" not in result.stdout
    else:
        assert "all checks passed" in result.stdout

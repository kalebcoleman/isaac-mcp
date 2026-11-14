"""sysid: dataset checks, the wrapper around a fake interpreter, and a real fit when MuJoCo at the pin is installed."""

import ast
import asyncio
import importlib.util
import json
import subprocess
import sys
import time
import zipfile

import pytest

from twinsmith import sysid as sysid_module
from twinsmith.isaaclab import jobs
from twinsmith.sysid import PIN, Sysid, check_dataset, npz_shapes, parse_target


def npy(shape):
    """A float64 .npy file of zeros, written without numpy."""
    header = repr({"descr": "<f8", "fortran_order": False, "shape": tuple(shape)}).encode("latin1")
    header += b" " * (63 - (10 + len(header)) % 64) + b"\n"
    count = 1
    for size in shape:
        count *= size
    return b"\x93NUMPY\x01\x00" + len(header).to_bytes(2, "little") + header + bytes(8 * count)


def write_npz(path, **shapes):
    with zipfile.ZipFile(path, "w") as archive:
        for name, shape in shapes.items():
            archive.writestr(name + ".npy", npy(shape))


PARAMS = [{"name": "damping", "target": "joint:hinge:damping", "nominal": 0.1, "min": 0.01, "max": 1.0},
          {"name": "armature", "target": "joint:hinge:armature", "nominal": 0.002, "min": 0.0001, "max": 0.1}]


def dataset(root, params=PARAMS, source=True, episodes=("0001", "0002", "0003")):
    folder = root / "dataset"
    (folder / "episodes").mkdir(parents=True)
    (folder / "model.xml").write_text("<mujoco/>")
    (folder / "params.json").write_text(json.dumps(params))
    if source:
        (folder / "SOURCE.md").write_text("Synthetic, for tests.\n")
    for name in episodes:
        write_npz(folder / "episodes" / f"{name}.npz", time=(50,), ctrl=(50, 1), sensordata=(50, 2))
    return folder


def code(call):
    with pytest.raises(sysid_module.SysidError) as caught:
        call()
    return caught.value.code


def test_npz_shapes_reads_headers_without_numpy(tmp_path):
    write_npz(tmp_path / "a.npz", time=(7,), ctrl=(7, 3))
    assert npz_shapes(tmp_path / "a.npz") == {"time": (7,), "ctrl": (7, 3)}


def test_a_good_dataset_is_described(tmp_path):
    checked = check_dataset(str(dataset(tmp_path)), [tmp_path])
    assert [e["episode"] for e in checked["episodes"]] == ["0001", "0002", "0003"]
    assert checked["episodes"][0] == {"episode": "0001", "steps": 50, "nu": 1, "nsensor": 2}
    assert checked["parameters"][0]["target"] == {"text": "joint:hinge:damping", "kind": "joint", "name": "hinge",
                                                  "field": "damping", "index": 0}
    assert checked["parameters"][1]["target"]["index"] is None


def test_each_dataset_refusal_has_its_own_code(tmp_path):
    missing_source = dataset(tmp_path / "a", source=False)
    assert code(lambda: check_dataset(str(missing_source), [tmp_path])) == "SOURCE_MISSING"
    inverted = dataset(tmp_path / "b", params=[dict(PARAMS[0], min=2.0, max=1.0)])
    assert code(lambda: check_dataset(str(inverted), [tmp_path])) == "PARAMS_INVALID"
    uneven = dataset(tmp_path / "c")
    write_npz(uneven / "episodes/0002.npz", time=(50,), ctrl=(49, 1), sensordata=(50, 2))
    assert code(lambda: check_dataset(str(uneven), [tmp_path])) == "EPISODE_INVALID"
    outside = dataset(tmp_path / "d")
    with pytest.raises(sysid_module.SysidError) as caught:
        check_dataset(str(outside), [tmp_path / "a"])
    assert caught.value.code == "ROOT_NOT_APPROVED" and "an agent cannot" in str(caught.value)


@pytest.mark.parametrize("text", ["joint:hinge:mass", "joint:hinge:armature[1]", "geom:g:friction[3]", "tendon:t:damping", "body:b:mass",
                                  "joint:a b:damping", "joint::damping"])
def test_bad_targets_are_refused(text):
    assert code(lambda: parse_target(text)) == "PARAMS_INVALID"


def test_array_targets_take_an_index():
    assert parse_target("geom:block:friction[2]")["index"] == 2
    assert parse_target("actuator:shoulder:kp")["index"] is None


def fake_environment(tmp_path, version=PIN, complete=True):
    root = tmp_path / "tools/sysid"
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(f"#!/bin/sh\necho '{json.dumps([version, complete])}'\n")
    python.chmod(0o755)
    return root


class Launches:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"job_id": "20261113-000000-sysid-test", "command": [kwargs["python"], kwargs["script"], *kwargs["args"]]}


def tool(tmp_path, launch=None, job_status=None, **environment):
    root = fake_environment(tmp_path, **environment)
    return Sysid(workspace=tmp_path, root=root, roots=lambda: [tmp_path], launch=launch or Launches(),
                 job_status=job_status or (lambda job_id: {"state": "running"}))


def test_status_reports_the_pin(tmp_path):
    result = tool(tmp_path).call("status")
    assert result["ok"] and result["pin_match"] and result["mujoco_version"] == PIN and result["recent_fits"] == []


def test_another_version_warns(tmp_path):
    result = tool(tmp_path, version="3.9.0").call("status")
    assert result["ok"] and result["pin_match"] is False and "differs from the tested pin" in result["warnings"][0]


def test_mujoco_without_the_sysid_extra_is_refused(tmp_path):
    result = tool(tmp_path, complete=False).call("status")
    assert not result["ok"] and result["code"] == "ENVIRONMENT_BROKEN" and "mujoco[sysid]" in result["error"]


def test_a_missing_environment_gives_setup_steps_and_installs_nothing(tmp_path):
    result = Sysid(workspace=tmp_path).call("fit", dataset=str(dataset(tmp_path)), name="first")
    assert result["code"] == "ENVIRONMENT_MISSING" and "never installs" in result["error"]
    assert result["setup"] == [f"uv venv --python 3.12 {tmp_path}/tools/sysid/.venv",
                               f'uv pip install --python {tmp_path}/tools/sysid/.venv/bin/python "mujoco[sysid]=={PIN}"']
    assert not (tmp_path / "tools/sysid").exists() and not (tmp_path / "runs").exists()


def test_fit_starts_one_job_with_the_expected_arguments(tmp_path):
    launches = Launches()
    sysid = tool(tmp_path, launch=launches)
    result = sysid.call("fit", dataset=str(dataset(tmp_path)), name="first", holdout=["0003"], max_iters=20)
    folder = tmp_path / "runs/sysid/first"
    assert result["ok"] and result["job_id"] == "20261113-000000-sysid-test" and result["fit_dir"] == str(folder)
    assert result["episodes"] == {"fit": ["0001", "0002"], "holdout": ["0003"]}
    [call] = launches.calls
    assert call == {"python": str(sysid.python), "script": str(folder / "driver.py"), "args": [str(folder / "request.json")],
                    "cwd": str(folder), "label": "sysid-first", "timeout_s": 3600.0, "uses_gpu": False,
                    "env": {"CUDA_VISIBLE_DEVICES": ""}, "single_instance_key": "sysid:first"}
    assert (folder / "driver.py").read_text() == (sysid_module._DRIVER).read_text()
    request = json.loads((folder / "request.json").read_text())
    assert request["holdout"] == ["0003"] and request["max_iters"] == 20 and request["pin"] == PIN
    assert set(request["files"]) == {"model.xml", "params.json", "SOURCE.md", "episodes/0001.npz", "episodes/0002.npz",
                                     "episodes/0003.npz"}
    again = sysid.call("fit", dataset=str(dataset(tmp_path / "second")), name="first")
    assert again["code"] == "FIT_EXISTS" and len(launches.calls) == 1


def test_fit_refuses_before_starting_anything(tmp_path):
    launches = Launches()
    sysid = tool(tmp_path, launch=launches)
    folder = str(dataset(tmp_path))
    assert sysid.call("fit", dataset=folder, name="x", holdout=["9999"])["code"] == "EPISODE_INVALID"
    assert sysid.call("fit", dataset=folder, name="x", holdout=["0001", "0002", "0003"])["code"] == "EPISODE_INVALID"
    assert sysid.call("fit", dataset=folder, name="bad name")["code"] == "INVALID_VALUE"
    assert sysid.call("fit", dataset="relative/path", name="x")["code"] == "DATASET_INVALID"
    assert not launches.calls and not (tmp_path / "runs/sysid/x").exists()


def raises(**kwargs):
    raise ValueError("python is not an executable file")


@pytest.mark.parametrize("launch", [lambda **kwargs: {"error": "a job with single_instance_key is already running"}, raises])
def test_a_refused_launch_leaves_no_fit_folder(tmp_path, launch):
    result = tool(tmp_path, launch=launch).call("fit", dataset=str(dataset(tmp_path)), name="first")
    assert result["code"] == "LAUNCH_REFUSED" and not (tmp_path / "runs/sysid/first").exists()


def test_report_says_running_then_returns_the_table(tmp_path):
    sysid = tool(tmp_path)
    sysid.call("fit", dataset=str(dataset(tmp_path)), name="first")
    running = sysid.call("report", name="first")
    assert running["ok"] and running["state"] == "running" and "jobs(" in running["next"]
    fit = {"mujoco_version": PIN, "parameters": [{"name": "damping", "target": "joint:hinge:damping", "nominal": 0.1,
                                                  "fitted": 0.3, "min": 0.01, "max": 1.0, "at_bound": None}],
           "bounds_hit": [], "residual": {"before": 0.05, "after": 0.001, "reduction": 0.98}, "held_out": None, "episodes": []}
    (tmp_path / "runs/sysid/first/fit.json").write_text(json.dumps(fit))
    finished = sysid.call("report", name="first")
    assert finished["state"] == "finished" and finished["table"] == fit["parameters"] and finished["residual"]["reduction"] == 0.98
    assert sysid.call("status")["recent_fits"] == [{"name": "first", "finished": True}]
    assert sysid.call("report", name="missing")["code"] == "FIT_NOT_FOUND"


def test_report_on_a_fit_that_died_says_so(tmp_path):
    sysid = tool(tmp_path, job_status=lambda job_id: {"state": "failed", "exit_code": 1, "log_dir": "/x"})
    sysid.call("fit", dataset=str(dataset(tmp_path)), name="first")
    result = sysid.call("report", name="first")
    assert not result["ok"] and result["state"] == "failed" and result["code"] == "FIT_FAILED" and result["exit_code"] == 1


def test_the_tool_is_one_grouped_entry_point(tmp_path):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("t")
    sysid_module.register_sysid_tools(server, sysid=tool(tmp_path))
    answer = json.loads(asyncio.run(server._tool_manager._tools["sysid"].fn(action="status")))
    assert answer["tool"] == "sysid" and answer["action"] == "status" and answer["pin_match"]


def test_the_host_never_imports_mujoco():
    tree = ast.parse(sysid_module.__loader__.get_source("twinsmith.sysid"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(name.split(".")[0] == "mujoco" for name in imported)


# ---- a real fit: needs mujoco[sysid] at the pin in this environment (the sysid-test extra) ----

def _mujoco_at_pin():
    if importlib.util.find_spec("mujoco") is None:
        return False
    import mujoco

    return mujoco.__version__ == PIN and importlib.util.find_spec("mujoco.sysid") is not None and importlib.util.find_spec("scipy")


PENDULUM = """<mujoco model="pendulum">
  <option timestep="0.002"/>
  <worldbody>
    <body name="arm">
      <joint name="hinge" type="hinge" axis="0 1 0" damping="{damping}" armature="{armature}"/>
      <geom type="capsule" fromto="0 0 0 0 0 -0.3" size="0.02" mass="0.5"/>
    </body>
  </worldbody>
  <actuator><motor name="torque" joint="hinge"/></actuator>
  <sensor><jointpos name="angle" joint="hinge"/><jointvel name="rate" joint="hinge"/></sensor>
</mujoco>"""


def pendulum_dataset(folder):
    """Episodes from damping 0.30 and armature 0.010; the model and parameters start at 0.10 and 0.002. Row k of an
    episode is time t_k, the command applied from t_k, and the sensor reading at t_k."""
    import mujoco
    import mujoco.rollout as rollout
    import numpy as np

    (folder / "episodes").mkdir(parents=True)
    model = mujoco.MjModel.from_xml_string(PENDULUM.format(damping=0.30, armature=0.010))
    data = mujoco.MjData(model)
    for k, (amplitude, frequency) in enumerate([(0.6, 0.7), (0.5, 1.3), (0.4, 2.1)], 1):
        t = np.arange(1500) * model.opt.timestep
        ctrl = (amplitude * np.sin(2 * np.pi * frequency * t) + 0.2 * np.sin(2 * np.pi * 3.1 * frequency * t)).reshape(-1, 1)
        mujoco.mj_resetData(model, data)
        mujoco.mj_forward(model, data)
        first = data.sensordata.copy()
        initial = np.empty(mujoco.mj_stateSize(model, mujoco.mjtState.mjSTATE_FULLPHYSICS))
        mujoco.mj_getState(model, data, initial, mujoco.mjtState.mjSTATE_FULLPHYSICS)
        _, sensor = rollout.rollout(model, data, initial, ctrl[:-1])
        np.savez(folder / "episodes" / f"{k:04d}.npz", time=t, ctrl=ctrl, sensordata=np.vstack([first, sensor[0]]))
    (folder / "model.xml").write_text(PENDULUM.format(damping=0.10, armature=0.002))
    (folder / "params.json").write_text(json.dumps(PARAMS))
    (folder / "SOURCE.md").write_text("Synthetic: generated by test_sysid.py with damping 0.30 and armature 0.010.\n")
    return folder


@pytest.mark.skipif(not _mujoco_at_pin(), reason=f"needs mujoco[sysid]=={PIN} (the sysid-test extra)")
def test_synthetic_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINSMITH_JOBS_DIR", str(tmp_path / "jobs"))
    folder = pendulum_dataset(tmp_path / "pendulum")
    root = tmp_path / "env"
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')  # this test environment stands in for the sysid one
    python.chmod(0o755)
    sysid = Sysid(workspace=tmp_path, root=root, roots=lambda: [tmp_path], launch=jobs.run_script, job_status=jobs.runs_status)
    started = sysid.call("fit", dataset=str(folder), name="pendulum", holdout=["0003"])
    assert started["ok"], started
    deadline = time.time() + 300
    while sysid.call("report", name="pendulum")["state"] == "running" and time.time() < deadline:
        time.sleep(0.5)
    report = sysid.call("report", name="pendulum")
    assert report["state"] == "finished", (report, subprocess.run(["cat", *(tmp_path / "jobs").glob("*/stderr.log")],
                                                                 capture_output=True, text=True).stdout[-3000:])
    fitted = {row["name"]: row["fitted"] for row in report["table"]}
    print("\n" + json.dumps({"fitted": fitted, "residual": report["residual"], "held_out": report["held_out"]}, indent=1))
    assert fitted["damping"] == pytest.approx(0.30, rel=0.05)
    assert fitted["armature"] == pytest.approx(0.010, rel=0.05)
    assert report["bounds_hit"] == []
    assert report["residual"]["after"] < report["residual"]["before"]
    assert report["held_out"]["after"] < report["held_out"]["before"]


def test_files_and_model_paths_cannot_leave_the_approved_folder(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.npz").write_bytes(b"")
    folder = dataset(tmp_path / "linked")
    (folder / "episodes" / "0004.npz").symlink_to(outside / "x.npz")
    assert code(lambda: check_dataset(str(folder), [tmp_path])) == "EPISODE_INVALID"
    folder = dataset(tmp_path / "linked-dir")
    (folder / "episodes").rename(folder / "real-episodes")
    (folder / "episodes").symlink_to(folder / "real-episodes")
    assert code(lambda: check_dataset(str(folder), [tmp_path])) == "DATASET_INVALID"
    for bad in ('<mujoco><include file="/etc/model.xml"/></mujoco>', '<mujoco><compiler meshdir="../../meshes"/></mujoco>'):
        folder = dataset(tmp_path / f"model-{len(bad)}")
        (folder / "model.xml").write_text(bad)
        assert code(lambda: check_dataset(str(folder), [tmp_path])) == "DATASET_INVALID"


def test_report_on_an_unreadable_fit_or_an_unknown_job_is_coded(tmp_path):
    sysid = tool(tmp_path, job_status=lambda job_id: (_ for _ in ()).throw(ValueError("no such job")))
    sysid.call("fit", dataset=str(dataset(tmp_path)), name="first")
    unknown = sysid.call("report", name="first")
    assert unknown["code"] == "STATE_UNKNOWN" and unknown["state"] == "unknown"
    (tmp_path / "runs/sysid/first/fit.json").write_text('{"parameters": []}')
    assert sysid.call("report", name="first")["code"] == "REPORT_INVALID"


def test_a_launch_that_cannot_start_a_process_leaves_no_fit_folder(tmp_path):
    def broken(**kwargs):
        raise OSError("exec format error")

    result = tool(tmp_path, launch=broken).call("fit", dataset=str(dataset(tmp_path)), name="first")
    assert result["code"] == "LAUNCH_REFUSED" and not (tmp_path / "runs/sysid/first").exists()


def test_approved_roots_are_read_even_before_any_assets_tool(tmp_path, monkeypatch):
    from twinsmith import server

    approved = tmp_path / "approved"
    approved.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    config = tmp_path / "approval.json"
    config.write_text(json.dumps(dict(version=1, profile="trusted_project", roots={"data": str(approved)}, staging=str(staging))))
    monkeypatch.setenv("TWINSMITH_ASSET_CONFIG", str(config))
    state = server._BridgeState()
    assert not hasattr(state, "asset_broker")
    assert [str(root) for root in server._approved_roots(state)] == [str(approved)]

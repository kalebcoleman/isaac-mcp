"""The hardware tool (toolset cad): checks, fab gating, outputs, render command, scrubbed environment.

Fake executables stand in for kicad-cli, KiKit and Blender: each logs its argv and environment as one JSON line and
writes what the real one would. No KiCad, KiKit, Blender or GPU is needed, and nothing real is rendered.
"""

import asyncio
import inspect
import json
import sys
from pathlib import Path

import pytest

from twinsmith import hardware, server, sibling, toolsets
from twinsmith.isaaclab import jobs

FIXTURES = Path(__file__).with_name("hardware_fixtures")

FAKE = """#!{python}
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
with open({log!r}, "a") as log:
    log.write(json.dumps({{"tool": {tool!r}, "argv": args, "env": dict(os.environ)}}) + "\\n")
if {tool!r} == "kicad-cli":
    out = pathlib.Path(args[args.index("-o") + 1])
    if "erc" in args:
        shutil.copyfile({erc!r}, out)
    elif "drc" in args:
        shutil.copyfile({drc!r}, out)
    else:
        out.write_text("3d")
elif {tool!r} == "kikit" and "fab" in args:
    folder = pathlib.Path(args[-1]); folder.mkdir()
    for name in ("gerbers.zip", "bom.csv", "pos.csv"):
        (folder / name).write_text(name)
elif "version" in args or "--version" in args:
    print("10.0.8")
"""


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """A board in an approved folder, fake tools on PATH, an empty workspace. bench.calls() reads the log."""
    project, bin_dir, log = tmp_path / "project", tmp_path / "bin", tmp_path / "calls.jsonl"
    project.mkdir()
    bin_dir.mkdir()
    for name in ("board.kicad_pcb", "board.kicad_sch", "case.stl"):
        (project / name).write_text("(fixture)")

    def install(drc="drc_clean.json"):
        for tool in ("kicad-cli", "kikit", "blender"):
            path = bin_dir / tool
            path.write_text(FAKE.format(python=sys.executable, log=str(log), tool=tool,
                                        erc=str(FIXTURES / "erc_clean.json"), drc=str(FIXTURES / drc)))
            path.chmod(0o755)

    install()
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("TWINSMITH_KIKIT", str(bin_dir / "kikit"))
    monkeypatch.setenv("TWINSMITH_BLENDER", str(bin_dir / "blender"))

    class Bench:
        board = str(project / "board.kicad_pcb")
        schematic = str(project / "board.kicad_sch")
        mesh = str(project / "case.stl")
        tool = hardware.Hardware(workspace=tmp_path / "workspace", roots=[project])
        kikit = str(bin_dir / "kikit")
        blender = str(bin_dir / "blender")
        use = staticmethod(install)

        @staticmethod
        def calls(tool=None):
            lines = log.read_text().splitlines() if log.exists() else []
            return [entry for entry in map(json.loads, lines)
                    if tool is None or entry["tool"] == tool and entry["argv"] not in (["--version"], ["version"])]

    return Bench()


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def test_recorded_reports_summarise_by_severity():
    clean = hardware.summarize(fixture("drc_clean.json"))
    assert clean["counts"] == {"warning": 1} and clean["violations"] == 1
    assert hardware.summarize(fixture("erc_clean.json"))["counts"] == {}
    errors = hardware.summarize(fixture("drc_errors.json"))
    assert errors["counts"] == {"error": 3, "warning": 1}
    assert (errors["violations"], errors["unconnected"], errors["parity"]) == (2, 1, 1)
    assert [item["severity"] for item in errors["first"]] == ["error", "error", "error", "warning"]


def test_check_passes_on_a_clean_pair_and_fails_on_an_error(bench):
    done = bench.tool.call("check", board=bench.board)
    assert done["ok"] and done["result"]["passed"] and done["result"]["drc"]["counts"] == {"warning": 1}
    assert {"tool", "action", "versions", "result", "warnings"} <= set(done)
    drc = next(call["argv"] for call in bench.calls("kicad-cli") if "drc" in call["argv"])
    assert {"--schematic-parity", "--refill-zones", "--format", "json"} <= set(drc)
    assert "--save-board" not in drc
    bench.use("drc_errors.json")
    failed = bench.tool.call("check", board=bench.board)
    assert not failed["ok"] and failed["result"]["errors"] == 3
    assert failed["result"]["drc"]["first"][0]["severity"] == "error"


def test_fab_refuses_on_a_failed_check_and_starts_no_kikit(bench):
    bench.use("drc_errors.json")
    done = bench.tool.call("fab", board=bench.board, name="fails")
    assert done["error"]["code"] == "CHECK_FAILED" and done["error"]["check"]["errors"] == 3
    assert not bench.calls("kikit")
    assert not (bench.tool.runs / "fails").exists()


@pytest.mark.parametrize("vendor", ["jlcpcb", "pcbway"])
def test_fab_builds_exactly_the_kikit_command_in_a_new_folder(bench, vendor):
    done = bench.tool.call("fab", board=bench.board, vendor=vendor, name=f"out-{vendor}")
    assert done["ok"], done
    folder = bench.tool.runs / f"out-{vendor}"
    [kikit] = bench.calls("kikit")
    assert [bench.kikit, *kikit["argv"]] == [bench.kikit, "fab", vendor, "--assembly", "--schematic", bench.schematic,
                                             "--field", "LCSC", "--missingError", bench.board, str(folder / vendor)]
    exports = [call["argv"] for call in bench.calls("kicad-cli") if "export" in call["argv"]]
    assert [argv[2] for argv in exports] == ["step", "glb"] and all("--force" not in argv for argv in exports)
    assert {"board.step", "board.glb", "checks/erc.json", "checks/drc.json", f"{vendor}/gerbers.zip"} <= set(
        done["result"]["files"])


def test_fab_refuses_another_vendor_an_existing_folder_and_an_unapproved_input(bench):
    assert bench.tool.call("fab", board=bench.board, vendor="oshpark")["error"]["code"] == "INVALID_VALUE"
    (bench.tool.runs / "taken").mkdir(parents=True)
    assert bench.tool.call("fab", board=bench.board, name="taken")["error"]["code"] == "OUTPUT_EXISTS"
    outside = Path(bench.board).parents[1] / "elsewhere.kicad_pcb"
    outside.write_text("(fixture)")
    for action in ("check", "fab"):
        assert bench.tool.call(action, board=str(outside))["error"]["code"] == "ROOT_NOT_APPROVED"
    assert bench.tool.call("render", inputs=[str(outside.with_suffix(".stl"))])["error"]["code"] == "ROOT_NOT_APPROVED"
    assert bench.tool.call("check", board="board.kicad_pcb")["error"]["code"] == "INVALID_PATH"
    assert not bench.calls()


def test_render_starts_a_blender_job_with_the_template(bench, monkeypatch):
    started = {}

    def start_job(argv, **options):
        started.update(argv=argv, **options)
        return {"ok": True, "job_id": "20261113-120000-hardware-render", "log_dir": "x", "command": argv}

    monkeypatch.setattr(sibling, "start_job", start_job)
    done = bench.tool.call("render", inputs=[bench.mesh], name="pic", width=640, height=480, samples=8)
    assert done["ok"] and done["result"]["job_id"] == "20261113-120000-hardware-render"
    folder = bench.tool.runs / "pic"
    assert started["argv"] == [bench.blender, "-b", "--factory-startup", "-P", hardware.TEMPLATE, "--", "--out",
                               folder / "render.png", "--size", "640x480", "--samples", "8", Path(bench.mesh)]
    assert started["uses_gpu"] is True and started["allow_concurrent_gpu"] is False
    assert hardware.TEMPLATE.is_file()


def test_render_refused_by_the_gpu_guard_leaves_no_folder(bench, monkeypatch):
    monkeypatch.setattr(sibling, "start_job", lambda argv, **options: {
        "ok": False, "code": "GPU_BUSY", "message": "another job", "hint": "wait"})
    done = bench.tool.call("render", inputs=[bench.mesh], name="busy")
    assert done["error"]["code"] == "GPU_BUSY" and not (bench.tool.runs / "busy").exists()


def test_no_parameter_reaches_an_upload_order_port_or_free_arguments():
    instance = server.build_server({"TWINSMITH_TOOLSETS": "minimal,+cad"})
    names = set(instance._tool_manager._tools["hardware"].parameters["properties"])
    assert not names & {"extra_args", "args", "upload", "order", "port"}
    assert not set(inspect.signature(hardware.Hardware.call).parameters) & {"extra_args", "args", "upload", "order", "port"}


def test_the_child_environment_is_scrubbed(bench, monkeypatch):
    for name in ("HF_TOKEN", "OPENROUTER_API_KEY", "AWS_SECRET_ACCESS_KEY", "ROS_DOMAIN_ID", "PYTHONPATH", "VIRTUAL_ENV"):
        monkeypatch.setenv(name, "do-not-pass")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    bench.tool.call("check", board=bench.board)
    for call in bench.calls("kicad-cli"):
        names = set(call["env"]) - {"PWD", "SHLVL", "_", "LC_CTYPE"}  # set by the interpreter, not passed
        assert not [name for name in names if any(word in name for word in ("TOKEN", "KEY", "SECRET"))]
        assert names <= {"PATH", "HOME", "CUDA_VISIBLE_DEVICES", *hardware.ENV_SET}
        assert call["env"]["FREEROUTING__USAGE_AND_DIAGNOSTIC_DATA__DISABLE_ANALYTICS"] == "true"


def test_status_with_nothing_installed_names_the_missing_pieces(tmp_path, monkeypatch):
    for name in ("TWINSMITH_KIKIT", "TWINSMITH_BLENDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path))
    started = []
    monkeypatch.setattr(sibling, "run", lambda argv, **options: started.append(argv))
    done = hardware.Hardware(workspace=tmp_path / "workspace", roots=[]).call("status")
    assert done["ok"] and done["result"]["setup"] == "docs/HARDWARE.md"
    assert set(done["result"]["missing"]) == {"kicad-cli", "kikit", "freecadcmd", "blender", "java", "freerouting", "pio"}
    assert not any(done["result"]["kicad_library_tables"].values())
    assert started == []


def test_a_job_started_beside_is_followed_by_the_jobs_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINSMITH_JOBS_DIR", str(tmp_path / "jobs"))
    job = sibling.start_job(["/bin/sh", "-c", "echo made"], task="hardware-test", kind="test", cwd=tmp_path)
    assert job["ok"]
    jobs._active[job["job_id"]].wait(10)
    for _ in range(100):
        status = jobs.runs_status(job["job_id"])
        if status["state"] != "running":
            break
        asyncio.run(asyncio.sleep(0.05))
    assert status["state"] == "succeeded" and status["last_log_lines"] == ["made"]


def test_cad_is_off_by_default_and_lists_only_hardware():
    assert "cad" not in toolsets.DEFAULT and toolsets.MEMBERS["cad"] == {"hardware"}
    assert "hardware" not in server.build_server({"TWINSMITH_TOOLSETS": "default"})._tool_manager._tools
    alone = server.build_server({"TWINSMITH_TOOLSETS": "minimal,+cad"})
    assert {name for name in alone._tool_manager._tools if toolsets.owner(name) == "cad"} == {"hardware"}

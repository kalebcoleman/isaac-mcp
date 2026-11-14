"""``sysid``: MuJoCo system identification in its own environment. The host never imports ``mujoco``.

The environment is ``<workspace>/tools/sysid/.venv`` (``TWINSMITH_SYSID_ROOT`` names another folder), made by the
owner; this module only asks it for ``mujoco.__version__``. A fit is a job (``jobs`` follows and cancels it) that runs
``sysid_driver.py`` with that interpreter and writes ``fit.json`` into ``<workspace>/runs/sysid/<name>/``.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import struct
from typing import Annotated, Literal
import zipfile

from mcp.types import ToolAnnotations
from pydantic import Field

from . import pack_manifest

PIN = "4.0.1"
ACTIONS = ("status", "fit", "report")
RECENT_FITS = 5
MAX_PARAMETERS = 64
MAX_EPISODES = 1000
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
_TARGET = re.compile(r"(joint|geom|actuator):([^:\s\[\]]{1,128}):([a-z]+)(?:\[(\d)\])?")
# Fields a target may name: None is a scalar, a number is an array of that length (index defaults to 0). Mass is a
# geom's: a body's own mass is ignored when its inertia comes from its geoms, so a body target could fit nothing.
FIELDS = {"joint": {"damping": 3, "stiffness": 3, "armature": None, "frictionloss": None},
          "geom": {"friction": 3, "mass": None}, "actuator": {"kp": None, "kv": None}}
_DRIVER = Path(__file__).with_name("sysid_driver.py")


class SysidError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def setup_steps(root: Path) -> list[str]:
    """What the owner runs to make the environment. The tool prints these and never runs them."""
    return [f"uv venv --python 3.12 {root}/.venv", f'uv pip install --python {root}/.venv/bin/python "mujoco[sysid]=={PIN}"']


def parse_target(text):
    """``joint:<name>:damping``, ``geom:<name>:friction[0]``, ``actuator:<name>:kp`` as a dict the driver applies."""
    found = _TARGET.fullmatch(text) if isinstance(text, str) else None
    if not found:
        raise SysidError("PARAMS_INVALID", f"target {text!r} is not KIND:NAME:FIELD or KIND:NAME:FIELD[i] (KIND joint, geom or actuator).")
    kind, name, field, index = found.groups()
    if field not in FIELDS[kind]:
        raise SysidError("PARAMS_INVALID", f"target {text!r}: a {kind} field is one of {', '.join(FIELDS[kind])}.")
    size = FIELDS[kind][field]
    if size is None and index is not None:
        raise SysidError("PARAMS_INVALID", f"target {text!r}: {kind} {field} is one number and takes no index.")
    if size is not None:
        index = int(index or 0)
        if index >= size:
            raise SysidError("PARAMS_INVALID", f"target {text!r}: {field} has {size} entries, index 0 to {size - 1}.")
    return {"text": text, "kind": kind, "name": name, "field": field, "index": index}


def _parameters(path: Path) -> list[dict]:
    try:
        declared = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SysidError("PARAMS_INVALID", f"params.json cannot be read as JSON: {exc}") from None
    if not isinstance(declared, list) or not 1 <= len(declared) <= MAX_PARAMETERS:
        raise SysidError("PARAMS_INVALID", f"params.json must be a list of 1 to {MAX_PARAMETERS} parameters.")
    parsed, names = [], set()
    for item in declared:
        if not isinstance(item, dict) or set(item) != {"name", "target", "nominal", "min", "max"}:
            raise SysidError("PARAMS_INVALID", 'Each parameter is exactly {"name", "target", "nominal", "min", "max"}.')
        name = item["name"]
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in names:
            raise SysidError("PARAMS_INVALID", f"parameter name {name!r} must be unique: letters, digits, _ and -.")
        names.add(name)
        values = [item[key] for key in ("nominal", "min", "max")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")) for v in values):
            raise SysidError("PARAMS_INVALID", f"{name}: nominal, min and max must be finite numbers.")
        nominal, low, high = (float(v) for v in values)
        if low > high:
            raise SysidError("PARAMS_INVALID", f"{name}: min {low:g} is greater than max {high:g}.")
        if not low <= nominal <= high:
            raise SysidError("PARAMS_INVALID", f"{name}: nominal {nominal:g} (the fit's start) is outside {low:g} to {high:g}.")
        parsed.append({"name": name, "target": parse_target(item["target"]), "nominal": nominal, "min": low, "max": high})
    return parsed


def npz_shapes(path: Path) -> dict:
    """``{array name: shape}`` read from the ``.npy`` headers inside an ``.npz``, without numpy."""
    shapes = {}
    with zipfile.ZipFile(path) as archive:
        for member in archive.namelist():
            if not member.endswith(".npy"):
                continue
            with archive.open(member) as handle:
                if handle.read(6) != b"\x93NUMPY":
                    raise ValueError(f"{member} is not a .npy array")
                major = handle.read(2)[0]
                size = struct.unpack("<H" if major == 1 else "<I", handle.read(2 if major == 1 else 4))[0]
                header = ast.literal_eval(handle.read(size).decode("latin1"))
            shapes[member[:-4]] = tuple(header["shape"])
    return shapes


def _episodes(folder: Path) -> list[dict]:
    files = sorted(folder.glob("*.npz")) if folder.is_dir() else []
    if not files or len(files) > MAX_EPISODES:
        raise SysidError("EPISODE_INVALID", f"episodes/ must hold 1 to {MAX_EPISODES} .npz files.")
    episodes = []
    for file in files:
        if file.is_symlink():
            raise SysidError("EPISODE_INVALID", f"{file.name} is a symbolic link; episodes must be files in the dataset.")
        try:
            shapes = npz_shapes(file)
        except (OSError, ValueError, SyntaxError, KeyError, zipfile.BadZipFile, struct.error, IndexError) as exc:
            raise SysidError("EPISODE_INVALID", f"{file.name}: not a readable .npz ({exc}).") from None
        missing = [key for key in ("time", "ctrl", "sensordata") if key not in shapes]
        if missing:
            raise SysidError("EPISODE_INVALID", f"{file.name}: missing {', '.join(missing)}.")
        time, ctrl, sensors = shapes["time"], shapes["ctrl"], shapes["sensordata"]
        if len(time) != 1 or len(ctrl) != 2 or len(sensors) != 2:
            raise SysidError("EPISODE_INVALID", f"{file.name}: time must be [T], ctrl [T, nu] and sensordata [T, nsensor].")
        if not time[0] == ctrl[0] == sensors[0] or time[0] < 2:
            raise SysidError("EPISODE_INVALID", f"{file.name}: time has {time[0]} rows, ctrl {ctrl[0]} and sensordata "
                             f"{sensors[0]}; they must agree, with at least 2.")
        episodes.append({"episode": file.stem, "steps": time[0], "nu": ctrl[1], "nsensor": sensors[1]})
    return episodes


def check_dataset(dataset, roots) -> dict:
    """The dataset folder, checked: approved, with its approval record, parameters and episodes. Raises SysidError."""
    if not isinstance(dataset, str) or not os.path.isabs(dataset):
        raise SysidError("DATASET_INVALID", "dataset must be an absolute folder path.")
    folder = Path(os.path.realpath(dataset))
    if not any(folder == root or root in folder.parents for root in (Path(os.path.realpath(r)) for r in roots)):
        from .project_assets import approval_help

        raise SysidError("ROOT_NOT_APPROVED", "The dataset is outside every user-approved root." + approval_help(folder))
    for name in ("SOURCE.md", "model.xml", "params.json", "episodes"):
        if (folder / name).is_symlink():
            raise SysidError("DATASET_INVALID", f"{name} is a symbolic link; every dataset file must be inside the approved folder.")
    if not (folder / "SOURCE.md").is_file():
        raise SysidError("SOURCE_MISSING", "The dataset has no SOURCE.md: who recorded it, when, on what, and who approved "
                         "the export. A dataset without a recorded approval is refused.")
    if not (folder / "model.xml").is_file():
        raise SysidError("DATASET_INVALID", "The dataset has no model.xml (the MuJoCo model).")
    _model_paths_stay_inside(folder / "model.xml")
    return {"dataset": str(folder), "parameters": _parameters(folder / "params.json"), "episodes": _episodes(folder / "episodes")}


def _model_paths_stay_inside(model: Path) -> None:
    """MuJoCo resolves include, mesh and texture paths itself: refuse any that could leave the dataset folder."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(model).getroot()
    except (ET.ParseError, OSError) as exc:
        raise SysidError("DATASET_INVALID", f"model.xml is not readable XML: {exc}") from None
    for node in root.iter():
        for key in ("file", "meshdir", "texturedir", "assetdir"):
            value = node.get(key)
            if value and (os.path.isabs(value) or ".." in Path(value).parts):
                raise SysidError("DATASET_INVALID", f"model.xml names {key}={value!r}, which leaves the dataset folder; "
                                 "keep every file the model uses inside it, by a relative path.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Sysid:
    def __init__(self, workspace=None, root=None, roots=None, launch=None, job_status=None):
        self.workspace = Path(workspace or pack_manifest.workspace_dir()).expanduser().resolve()
        self.root = Path(root or os.environ.get("TWINSMITH_SYSID_ROOT") or self.workspace / "tools/sysid").expanduser().resolve()
        self.python = self.root / ".venv/bin/python"
        self.runs = self.workspace / "runs/sysid"
        self._roots = roots or (lambda: [])  # the user-approved roots, read when a fit is asked for
        self._launch, self._job_status = launch, job_status

    def environment(self) -> dict:
        info = {"environment": str(self.root), "python": str(self.python), "pin": PIN, "mujoco_version": None,
                "pin_match": False, "warnings": []}
        if not (self.python.is_file() and os.access(self.python, os.X_OK)):
            return dict(info, ok=False, code="ENVIRONMENT_MISSING", setup=setup_steps(self.root),
                        error=f"No sysid environment at {self.root}. The owner creates it with the setup steps; "
                              "this tool never installs anything.")
        probe = "import json, mujoco, mujoco.sysid as s; print(json.dumps([mujoco.__version__, hasattr(s, 'optimize')]))"
        done = None
        try:
            done = subprocess.run([str(self.python), "-I", "-c", probe], capture_output=True, text=True, timeout=120,
                                  stdin=subprocess.DEVNULL)
            version, complete = json.loads(done.stdout.strip().splitlines()[-1])
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return dict(info, ok=False, code="ENVIRONMENT_BROKEN", setup=setup_steps(self.root),
                        error="The sysid environment's python cannot import mujoco.sysid. " + (done.stderr[-600:] if done else ""))
        info.update(mujoco_version=version, pin_match=version == PIN)
        if not complete:
            return dict(info, ok=False, code="ENVIRONMENT_BROKEN", setup=setup_steps(self.root),
                        error="mujoco is installed without its sysid extra (scipy and others): install mujoco[sysid].")
        if version != PIN:
            info["warnings"].append(f"MuJoCo {version} differs from the tested pin {PIN}.")
        return dict(info, ok=True)

    def call(self, action, *, dataset=None, name=None, holdout=None, max_iters=50, timeout_s=3600.0):
        try:
            if action not in ACTIONS:
                raise SysidError("INVALID_VALUE", "action must be one of: " + ", ".join(ACTIONS))
            if action == "report":
                return dict(self._report(self._fit_folder(name)), tool="sysid", action=action)
            env = self.environment()
            if action == "status":
                return dict(env, tool="sysid", action=action, bridge_needed=False, recent_fits=self._recent())
            if not env["ok"]:
                return dict(env, tool="sysid", action=action)
            return dict(env, tool="sysid", action=action, **self._fit(dataset, name, holdout, max_iters, timeout_s))
        except SysidError as exc:
            return {"ok": False, "tool": "sysid", "action": action, "code": exc.code, "error": str(exc)}
        except OSError as exc:
            return {"ok": False, "tool": "sysid", "action": action, "code": "IO_ERROR", "error": str(exc)}

    def _fit_folder(self, name) -> Path:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise SysidError("INVALID_VALUE", "name must be 1 to 80 letters, digits, underscores or hyphens.")
        return self.runs / name

    def _fit(self, dataset, name, holdout, max_iters, timeout_s) -> dict:
        folder = self._fit_folder(name)
        if isinstance(max_iters, bool) or not isinstance(max_iters, int) or not 1 <= max_iters <= 1000:
            raise SysidError("INVALID_VALUE", "max_iters must be a whole number from 1 to 1000.")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 10 <= timeout_s <= 86400:
            raise SysidError("INVALID_VALUE", "timeout_s must be 10 to 86400 seconds.")
        checked = check_dataset(dataset, self._roots())
        known = [e["episode"] for e in checked["episodes"]]
        holdout = list(holdout or [])
        unknown = [h for h in holdout if h not in known]
        if unknown or len(set(holdout)) >= len(known):
            raise SysidError("EPISODE_INVALID", f"holdout names episodes by file name without .npz ({', '.join(known[:10])}); "
                             f"unknown: {unknown}, and at least one episode must stay for the fit.")
        if folder.exists():
            raise SysidError("FIT_EXISTS", f"A fit named {name!r} already exists in {self.runs}; choose a new name.")
        folder.mkdir(parents=True)
        shutil.copyfile(_DRIVER, folder / "driver.py")
        source = Path(checked["dataset"])
        files = [source / "model.xml", source / "params.json", source / "SOURCE.md",
                 *sorted((source / "episodes").glob("*.npz"))]
        request = dict(checked, holdout=holdout, max_iters=max_iters, name=name, pin=PIN,
                       files={str(f.relative_to(source)): _sha256(f) for f in files})
        (folder / "request.json").write_text(json.dumps(request, indent=1) + "\n")
        try:
            launched = self._launch(python=str(self.python), script=str(folder / "driver.py"), args=[str(folder / "request.json")],
                                    cwd=str(folder), label=f"sysid-{name}", timeout_s=float(timeout_s), uses_gpu=False,
                                    env={"CUDA_VISIBLE_DEVICES": ""}, single_instance_key=f"sysid:{name}")
        except (ValueError, OSError) as exc:  # run_script's own checks, or the process could not start
            launched = {"error": str(exc)}
        if "error" in launched:
            shutil.rmtree(folder)
            raise SysidError("LAUNCH_REFUSED", launched["error"])
        (folder / "job.json").write_text(json.dumps({"job_id": launched["job_id"]}) + "\n")
        return {"name": name, "fit_dir": str(folder), "job_id": launched["job_id"], "command": launched["command"],
                "episodes": {"fit": [k for k in known if k not in holdout], "holdout": holdout},
                "next": f'jobs(action="status", job_id="{launched["job_id"]}", wait_s=60), then sysid(action="report", name="{name}").'}

    def _report(self, folder: Path) -> dict:
        if not (folder / "request.json").is_file():
            raise SysidError("FIT_NOT_FOUND", f"No fit named {folder.name!r} in {self.runs}.")
        result = folder / "fit.json"
        if result.is_file():
            try:
                fit = json.loads(result.read_text())
                table, bounds, residual, episodes = fit["parameters"], fit["bounds_hit"], fit["residual"], fit["episodes"]
            except (ValueError, KeyError, TypeError) as exc:
                raise SysidError("REPORT_INVALID", f"{result} is not a fit this tool wrote: {exc}") from None
            return {"ok": True, "state": "finished", "name": folder.name, "fit_dir": str(folder), "fit_json": str(result),
                    "mujoco_version": fit.get("mujoco_version"), "table": table, "bounds_hit": bounds,
                    "residual": residual, "held_out": fit.get("held_out"), "episodes": episodes,
                    "warnings": [f"MuJoCo {fit.get('mujoco_version')} differs from the pin {PIN}."]
                    if fit.get("mujoco_version") != PIN else []}
        job_id = json.loads((folder / "job.json").read_text())["job_id"] if (folder / "job.json").is_file() else None
        try:
            job = self._job_status(job_id) if job_id else {}
        except ValueError:
            job = {}
        state = job.get("state") or "unknown"
        if state == "running":
            return {"ok": True, "state": "running", "name": folder.name, "job_id": job_id,
                    "next": f'jobs(action="status", job_id="{job_id}", wait_s=60)'}
        if state == "unknown":
            return {"ok": False, "state": "unknown", "name": folder.name, "job_id": job_id, "code": "STATE_UNKNOWN",
                    "error": "No fit.json and no record of the job (the server restarted, or the record was removed); "
                             "the fit may still be running. jobs(action=\"list\") shows what runs."}
        return {"ok": False, "state": state, "name": folder.name, "job_id": job_id, "code": "FIT_FAILED",
                "error": "The fit ended without fit.json.", "exit_code": job.get("exit_code"),
                "log_dir": job.get("log_dir"), "last_stderr_lines": job.get("last_stderr_lines")}

    def _recent(self) -> list[dict]:
        fits = sorted((p for p in self.runs.glob("*/request.json")), key=lambda p: p.stat().st_mtime, reverse=True)
        return [{"name": p.parent.name, "finished": (p.parent / "fit.json").is_file()} for p in fits[:RECENT_FITS]]


def register_sysid_tools(server, *, sysid=None, roots=None):
    from .isaaclab import jobs

    sysid = sysid or Sysid(roots=roots, launch=jobs.run_script, job_status=jobs.runs_status)

    @server.tool(name="sysid", annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False,
    ))
    async def sysid_tool(
        action: Annotated[Literal["status", "fit", "report"], Field(description="status: the environment, its MuJoCo against the pin, recent fits. fit: start a fit job. report: read a fit.")],
        dataset: Annotated[str | None, Field(description="fit: absolute dataset folder inside a user-approved root: model.xml, params.json, episodes/*.npz, SOURCE.md.")] = None,
        name: Annotated[str | None, Field(description="fit/report: the fit's name; its folder is <workspace>/runs/sysid/<name>/.")] = None,
        holdout: Annotated[list[str] | None, Field(description="fit: episodes (file names without .npz) kept out of the fit and scored after it.")] = None,
        max_iters: Annotated[int, Field(description="fit: optimiser iterations at most.", ge=1, le=1000)] = 50,
        timeout_s: Annotated[float, Field(description="fit: the job is stopped after this many seconds.", ge=10, le=86400)] = 3600.0,
    ) -> str:
        """Fit model parameters (joint damping, armature, friction loss, position gains, geom mass and friction) to recorded trajectories
        with MuJoCo's system identification, in its own environment on the CPU. No simulator bridge needed. fit runs a
        job; follow it with jobs, then report. Reads only an approved dataset folder with a SOURCE.md; nothing here
        reads a robot."""
        result = await asyncio.to_thread(sysid.call, action, dataset=dataset, name=name, holdout=holdout,
                                         max_iters=max_iters, timeout_s=timeout_s)
        return json.dumps(result)

    return sysid

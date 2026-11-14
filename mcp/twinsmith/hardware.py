"""``hardware`` (toolset ``cad``): the deterministic steps that turn KiCad and mesh files into checked outputs.

Authoring stays in the servers beside twinsmith (KiCad, FreeCAD, Blender, PlatformIO); see docs/HARDWARE.md. This
runs ``kicad-cli`` checks, KiKit fab packages and a headless Blender render, nothing else. No action orders, quotes,
uploads or flashes, and none has a network path; there is no ``extra_args``. Inputs must be inside a folder the user
approved for asset import; outputs go to a new folder under ``<workspace>/runs/hardware`` and are never overwritten.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from . import pack_manifest, sibling

SETUP = "docs/HARDWARE.md"
VENDORS = ("jlcpcb", "pcbway")
MESHES = (".glb", ".gltf", ".stl")
TEMPLATE = Path(__file__).with_name("hardware_render.py")
# The tested pins of the servers beside twinsmith: (tag, commit).
PINS = {"kicad-mcp": ("v2.9.0", "9dd467c"), "blender-mcp": ("v1.0.3", "2cea8d5"), "platformio-mcp": ("v3.1.0", "29d4900")}
EXPECTED = {"kicad-cli": "10.0.", "kikit": "1.8.1", "blender": "5.2.", "freerouting": "2.5.0", "pio": "6.2."}
ENV_SET = {"FREEROUTING__USAGE_AND_DIAGNOSTIC_DATA__DISABLE_ANALYTICS": "true"}
EXPORT_3D = ("--subst-models", "--include-tracks", "--include-pads", "--include-zones", "--include-silkscreen",
             "--include-soldermask")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)?")
_SEVERITY = {"error": 0, "warning": 1}


class Refused(ValueError):
    def __init__(self, code, message, after_run=False, **details):
        super().__init__(message)
        self.code, self.after_run, self.details = code, after_run, details


def _executable(path) -> str | None:
    return str(path) if path and os.path.isfile(path) and os.access(path, os.X_OK) else None


def summarize(report: dict, first: int = 5) -> dict:
    """Counts by severity and the first violations of a KiCad ERC or DRC JSON report."""
    found = list(report.get("violations") or []) + [item for sheet in report.get("sheets") or []
                                                    for item in sheet.get("violations") or []]
    unconnected, parity = list(report.get("unconnected_items") or []), list(report.get("schematic_parity") or [])
    every = found + unconnected + parity
    every.sort(key=lambda item: _SEVERITY.get(item.get("severity"), 2))
    return {"counts": dict(Counter(item.get("severity", "error") for item in every)), "violations": len(found),
            "unconnected": len(unconnected), "parity": len(parity), "kicad_version": report.get("kicad_version"),
            "first": [{"severity": item.get("severity"), "type": item.get("type"),
                       "description": item.get("description"),
                       "items": [part.get("description") for part in item.get("items") or []][:3]}
                      for item in every[:first]]}


class Hardware:
    def __init__(self, workspace=None, roots=None):
        self.workspace = Path(workspace or pack_manifest.workspace_dir())
        self.runs = self.workspace / "runs" / "hardware"
        self._roots = roots
        self._versions = {}

    # ---------- where things are ----------
    def paths(self) -> dict:
        tools = self.workspace / "tools"
        jars = sorted((tools / "freerouting").glob("freerouting-*.jar"))
        return {"kicad-cli": _executable(shutil.which("kicad-cli")),
                "kikit": _executable(os.environ.get("TWINSMITH_KIKIT") or tools / "kikit/.venv/bin/kikit"),
                "freecadcmd": _executable(shutil.which("freecadcmd")),
                "blender": _executable(os.environ.get("TWINSMITH_BLENDER") or shutil.which("blender")),
                "java": _executable(tools / "jre/current/bin/java"),
                "freerouting": str(jars[-1]) if jars else None,
                "pio": _executable(shutil.which("pio"))}

    def version(self, name, path) -> str | None:
        if not path:
            return None
        if path not in self._versions:
            if name == "freerouting":
                text = Path(path).name
            else:
                done = sibling.run([path, "version" if name == "kicad-cli" else "-version" if name == "java"
                                    else "--version"], timeout_s=30)
                text = done["stdout"] + done["stderr"]
            match = _VERSION.search(text)
            self._versions[path] = match.group(0) if match else None
        return self._versions[path]

    def _need(self, name) -> str:
        path = self.paths()[name]
        if not path:
            raise Refused("TOOL_MISSING", f"{name} was not found. Set it up as {SETUP} says; this tool never installs it.",
                          setup=SETUP)
        return path

    def roots(self) -> list:
        if self._roots is None:
            from .project_assets import Broker
            return [Path(root).resolve() for root, _ in Broker().roots.values()]
        return [Path(root).resolve() for root in self._roots]

    def _input(self, value, suffixes, what) -> Path:
        if not isinstance(value, str) or not os.path.isabs(value):
            raise Refused("INVALID_PATH", f"{what} must be an absolute path.")
        path = Path(value).resolve()
        if path.suffix.lower() not in suffixes:
            raise Refused("INVALID_VALUE", f"{what} must be a {' or '.join(suffixes)} file, not {path.name}.")
        if not any(path.is_relative_to(root) for root in self.roots()):
            from .project_assets import approval_help
            raise Refused("ROOT_NOT_APPROVED", f"{path} is outside every folder approved for asset import."
                          + approval_help(path))
        if not path.is_file():
            raise Refused("NOT_FOUND", f"{path} does not exist.")
        return path

    def _output(self, name, default) -> Path:
        name = name or f"{time.strftime('%Y%m%d-%H%M%S')}-{default}"
        if not _NAME.fullmatch(name):
            raise Refused("INVALID_VALUE", "name is a folder name: letters, digits, '.', '_' and '-', at most 64.")
        folder = self.runs / name
        if folder.exists() or folder.is_symlink():
            raise Refused("OUTPUT_EXISTS", f"{folder} already exists; outputs are never overwritten. Pick another name.")
        return folder

    @staticmethod
    def _create(folder: Path) -> None:
        folder.parent.mkdir(parents=True, exist_ok=True)
        try:
            folder.mkdir()
        except FileExistsError:
            raise Refused("OUTPUT_EXISTS", f"{folder} already exists; outputs are never overwritten.") from None

    def _board(self, board, schematic):
        board = self._input(board, (".kicad_pcb",), "board")
        schematic = self._input(schematic or str(board.with_suffix(".kicad_sch")), (".kicad_sch",), "schematic")
        return board, schematic

    # ---------- actions ----------
    def status(self) -> dict:
        paths = self.paths()
        tools = {name: {"path": path, "version": self.version(name, path)} for name, path in paths.items()}
        warnings = [f"{name} {tools[name]['version']} is not the tested {want.rstrip('.')}"
                    for name, want in EXPECTED.items() if paths[name] and not (tools[name]["version"] or "").startswith(want)]
        checkouts = {}
        for name, (tag, pin) in PINS.items():
            folder = self.workspace / "tools" / name
            commit = sibling.commit(folder) if folder.is_dir() else None
            checkouts[name] = {"folder": str(folder), "commit": commit, "pin": f"{tag} ({pin})",
                               "pin_match": bool(commit and commit.startswith(pin))}
            if commit and not commit.startswith(pin):
                warnings.append(f"{name} is at {commit[:7]}, not the tested {tag} ({pin}).")
        config = Path.home() / ".config" / "kicad" / ".".join((tools["kicad-cli"]["version"] or "10.0").split(".")[:2])
        tables = {name: (config / name).is_file() for name in ("sym-lib-table", "fp-lib-table")}
        if not all(tables.values()):
            warnings.append(f"KiCad's global library tables are missing under {config}; {SETUP} says how to copy them.")
        missing = sorted(name for name, path in paths.items() if not path)
        return {"result": {"tools": tools, "missing": missing, "checkouts": checkouts, "kicad_library_tables": tables,
                           "outputs": str(self.runs), "setup": SETUP},
                "versions": {name: tool["version"] for name, tool in tools.items() if tool["version"]},
                "warnings": warnings}

    def _check(self, cli, board, schematic, into: Path) -> dict:
        found = {}
        for key, argv in (("erc", [cli, "sch", "erc", "--format", "json", "--severity-all", "-o", into / "erc.json",
                                   schematic]),
                          ("drc", [cli, "pcb", "drc", "--format", "json", "--severity-all", "--schematic-parity",
                                   "--refill-zones", "-o", into / "drc.json", board])):
            done = sibling.run(argv, cwd=into, env_set=ENV_SET, timeout_s=300)
            try:
                found[key] = summarize(json.loads((into / f"{key}.json").read_text(encoding="utf-8")))
            except (OSError, ValueError):
                raise Refused("CHECK_NOT_RUN", f"kicad-cli {key} wrote no readable report (exit {done['exit_code']}).",
                              after_run=True, stderr=done["stderr"][-800:]) from None
        errors = sum(report["counts"].get("error", 0) for report in found.values())
        return {"passed": errors == 0, "errors": errors, **found}

    def check(self, board, schematic) -> dict:
        board, schematic = self._board(board, schematic)
        cli = self._need("kicad-cli")
        with tempfile.TemporaryDirectory(prefix="twinsmith-check-") as folder:
            result = self._check(cli, board, schematic, Path(folder))
        return {"result": result, "ok": result["passed"], "versions": self._used("kicad-cli")}

    def fab(self, board, schematic, vendor, name) -> dict:
        if vendor not in VENDORS:
            raise Refused("INVALID_VALUE", f"vendor must be one of {', '.join(VENDORS)}.")
        board, schematic = self._board(board, schematic)
        cli, kikit = self._need("kicad-cli"), self._need("kikit")
        out = self._output(name, board.stem)
        with tempfile.TemporaryDirectory(prefix="twinsmith-check-") as folder:
            checked = self._check(cli, board, schematic, Path(folder))
            if not checked["passed"]:
                raise Refused("CHECK_FAILED", f"check found {checked['errors']} error(s); nothing was written.",
                              after_run=True, check=checked)
            self._create(out)
            (out / "checks").mkdir()
            for report in ("erc.json", "drc.json"):
                shutil.copyfile(Path(folder) / report, out / "checks" / report)
        steps = {"kikit": fab_argv(kikit, vendor, schematic, board, out / vendor),
                 "step": [cli, "pcb", "export", "step", *EXPORT_3D, "-o", out / "board.step", board],
                 "glb": [cli, "pcb", "export", "glb", *EXPORT_3D, "-o", out / "board.glb", board]}
        for step, argv in steps.items():
            done = sibling.run(argv, cwd=out, env_set=ENV_SET, timeout_s=600)
            if done["exit_code"] != 0:
                raise Refused("FAB_FAILED", f"{step} failed (exit {done['exit_code']}); partial output kept in {out}.",
                              after_run=True, folder=str(out), stderr=done["stderr"][-800:], stdout=done["stdout"][-800:])
        files = sorted(path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file())
        return {"result": {"folder": str(out), "files": files, "check": {"errors": 0, "erc": checked["erc"]["counts"],
                                                                         "drc": checked["drc"]["counts"]},
                           "note": "Files for the owner to review and send; nothing was uploaded or ordered."},
                "versions": self._used("kicad-cli", "kikit")}

    def render(self, inputs, name, width, height, samples, allow_concurrent_gpu) -> dict:
        if not inputs or not isinstance(inputs, list) or len(inputs) > 8:
            raise Refused("INVALID_VALUE", "inputs is a list of 1 to 8 .glb, .gltf or .stl files (STL in millimetres).")
        files = [self._input(value, MESHES, "each input") for value in inputs]
        blender = self._need("blender")
        out = self._output(name, files[0].stem)
        self._create(out)
        image = out / "render.png"
        job = sibling.start_job(render_argv(blender, image, width, height, samples, files), task="hardware-render",
                                kind="render", cwd=out, uses_gpu=True, allow_concurrent_gpu=allow_concurrent_gpu,
                                timeout_s=1800, label=f"hardware render {out.name}")
        if not job["ok"]:
            out.rmdir()
            raise Refused(job["code"], job["message"], hint=job["hint"])
        return {"result": {"job_id": job["job_id"], "image": str(image), "folder": str(out),
                           "next": f'jobs(action="status", job_id="{job["job_id"]}", wait_s=30)'},
                "versions": self._used("blender")}

    def _used(self, *names) -> dict:
        paths = self.paths()
        return {name: self.version(name, paths[name]) for name in names}

    def call(self, action, *, board=None, schematic=None, vendor="jlcpcb", inputs=None, name=None, width=1600,
             height=1200, samples=128, allow_concurrent_gpu=False) -> dict:
        envelope = {"tool": "hardware", "action": action}
        try:
            if action == "status":
                done = self.status()
            elif action == "check":
                done = self.check(board, schematic)
            elif action == "fab":
                done = self.fab(board, schematic, vendor, name)
            elif action == "render":
                done = self.render(inputs, name, width, height, samples, allow_concurrent_gpu)
            else:
                raise Refused("INVALID_VALUE", "action is one of status, check, fab, render.")
        except Refused as refused:
            return {"ok": False, **envelope, "versions": self._used("kicad-cli", "kikit") if refused.after_run else {},
                    "error": {"code": refused.code, "message": str(refused), **refused.details}, "warnings": []}
        return {"ok": done.get("ok", True), **envelope, "versions": done["versions"], "result": done["result"],
                "warnings": done.get("warnings", [])}


def fab_argv(kikit, vendor, schematic, board, folder) -> list:
    return [kikit, "fab", vendor, "--assembly", "--schematic", schematic, "--field", "LCSC", "--missingError", board, folder]


def render_argv(blender, image, width, height, samples, files) -> list:
    return [blender, "-b", "--factory-startup", "-P", TEMPLATE, "--", "--out", image, "--size", f"{width}x{height}",
            "--samples", str(samples), *files]


def register_hardware_tools(server, *, hardware=None):
    hw = hardware or Hardware()

    @server.tool(name="hardware", annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    async def hardware(
        action: Annotated[Literal["status", "check", "fab", "render"], Field(description="Action to perform.")],
        board: Annotated[str | None, Field(description="check/fab: absolute .kicad_pcb path inside an approved folder.")] = None,
        schematic: Annotated[str | None, Field(description="check/fab: absolute .kicad_sch path; default: the board's, renamed.")] = None,
        vendor: Annotated[Literal["jlcpcb", "pcbway"], Field(description="fab: which fab house's file set KiKit writes. Nothing is sent to it.")] = "jlcpcb",
        inputs: Annotated[list[str] | None, Field(description="render: absolute .glb, .gltf or .stl paths (GLB in metres, STL in millimetres).")] = None,
        name: Annotated[str | None, Field(description="fab/render: new folder name under <workspace>/runs/hardware; default a timestamp.")] = None,
        width: Annotated[int, Field(description="render: image width in pixels.", ge=64, le=7680)] = 1600,
        height: Annotated[int, Field(description="render: image height in pixels.", ge=64, le=4320)] = 1200,
        samples: Annotated[int, Field(description="render: Cycles samples per pixel.", ge=1, le=4096)] = 128,
        allow_concurrent_gpu: Annotated[bool, Field(description="render: knowingly start next to another GPU job or a running simulator.")] = False,
    ) -> str:
        """PCB and enclosure outputs from tools beside twinsmith; no simulator. status: versions, pins, gaps. check: KiCad
        ERC and DRC counts. fab: check, then KiKit JLCPCB/PCBWay files, board.step and board.glb in a new folder; never
        uploads or orders. render: a Blender job that uses the GPU (follow with jobs). Inputs in approved folders only.
        """
        result = await asyncio.to_thread(hw.call, action, board=board, schematic=schematic, vendor=vendor, inputs=inputs,
                                         name=name, width=width, height=height, samples=samples,
                                         allow_concurrent_gpu=allow_concurrent_gpu)
        return json.dumps(result)

    return hw

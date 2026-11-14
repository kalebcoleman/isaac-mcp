"""``gap``: Graph-as-Policy's command line, in simulation only, from its own pinned checkout.

The checkout is the fork and commit VABAR 1.0 names, and it serves both ``gap`` and ``vabar``. The skill library is the
``open-robot-skills`` checkout beside it. Nothing here imports Graph-as-Policy.

Safety rule: twinsmith never runs Graph-as-Policy on real hardware. No argument produces ``--real``, and every argument
list is scanned for it again before a process starts.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from . import config, sibling

PIN = "06dd6193e13eab816123febef1369deda0849d91"
REPOSITORY = "https://github.com/KeplerC/graph-as-policy"
SKILLS_PIN = "b15859a244658abc6060f3aa3b6827780746a5fe"
SKILLS_REPOSITORY = "https://github.com/KeplerC/open-robot-skills"
ACTIONS = ("status", "tools", "check", "generate", "run")
REQUIRED_GPU_MIB = 23_000          # upstream asks for 24 GB; a 24 GB card reports a little under 24,576 MiB
KEYS = ("HF_TOKEN", "OPENROUTER_API_KEY")
REAL_REFUSAL = "twinsmith does not run Graph-as-Policy on real hardware"
_SIM = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}")


def checkout(workspace=None, root=None) -> sibling.Checkout:
    return sibling.Checkout("graph-as-policy", "TWINSMITH_GAP_ROOT", PIN, REPOSITORY, workspace=workspace, root=root)


def skills(gap: sibling.Checkout) -> sibling.Checkout:
    """The skill library: the ``open-robot-skills`` checkout beside Graph-as-Policy."""
    return sibling.Checkout("open-robot-skills", None, SKILLS_PIN, SKILLS_REPOSITORY, root=gap.root.parent / "open-robot-skills")


def skills_info(gap: sibling.Checkout) -> dict:
    """The skill library's checkout and pin. It has no environment of its own: its tool bundles each have one,
    and ``gap check`` reports them."""
    info = skills(gap).info()
    for key in ("environment_present", "setup"):
        info.pop(key, None)
    return info


# Not secrets: where weights are cached and where upstream finds its benchmark roots. Always passed when set.
PLAIN_ENV = ("HF_HOME", "GAP_VAB_ROOT", "GAP_LIBERO_PRO_ROOT")


def paid_env(allowed: bool) -> list[str]:
    """The model-provider variables a run may see: none unless the owner allowed paid calls."""
    if not allowed:
        return []
    return ["OPENROUTER_API_KEY", *(key for key in os.environ if key.startswith(("GAP_VLM_", "GAP_LLM_")))]


def run_env(allowed: bool) -> list[str]:
    """Every variable a Graph-as-Policy or VABAR process may see beyond the scrubbed base."""
    return ["HF_TOKEN", *PLAIN_ENV, *paid_env(allowed)]


def owner_flags() -> tuple[bool, bool]:
    """``(allow_paid, allow_scripts)`` from the owner's own file; a malformed file is a refusal, not a crash."""
    try:
        return config.allow_paid(), config.allow_scripts()
    except (ValueError, TypeError) as error:
        raise sibling.Refusal("CONFIG_INVALID", f"~/.config/twinsmith/config.toml: {error}") from None


def _real(token: str) -> bool:
    """``--real``, ``--real=...`` or an abbreviation argparse would expand to it."""
    head = token.split("=", 1)[0]
    return len(head) > 2 and "--real".startswith(head)


def refuse_real(values) -> None:
    """Raise ``REAL_ROBOT_REFUSED`` if any value, or any word or path part of one, asks for real hardware."""
    for value in values:
        if isinstance(value, str) and any(_real(part) for part in re.split(r"[\s/]+", value)):
            raise sibling.Refusal("REAL_ROBOT_REFUSED", REAL_REFUSAL)


def gpu_memory_mib() -> list[int] | None:
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return [int(value) for value in result.stdout.split() if value.isdigit()] if result.returncode == 0 else None


def graph_roots(co: sibling.Checkout) -> list[Path]:
    """Where a graph may come from: this tool's run folder, the Graph-as-Policy checkout and the VABAR checkout."""
    from . import vabar

    return [co.workspace / "runs/gap", co.root, vabar.checkout(workspace=co.workspace).root]


def _graph(value, co: sibling.Checkout) -> str:
    if not isinstance(value, str) or not value.strip():
        raise sibling.Refusal("INVALID_VALUE", "graph is required: a workflow directory or workflow.json.")
    path = Path(value).expanduser()
    path = (path if path.is_absolute() else co.workspace / "runs/gap" / path).resolve()
    roots = graph_roots(co)
    if not any(path.is_relative_to(root) for root in roots):
        raise sibling.Refusal("PATH_NOT_ALLOWED", "graph must be inside " + ", ".join(map(str, roots)) + ".")
    if not (path.is_dir() and (path / "workflow.json").is_file()) and not (path.is_file() and path.name == "workflow.json"):
        raise sibling.Refusal("NOT_FOUND", f"{path} is not a workflow directory or workflow.json.")
    return str(path)


def graph_scripts(workflow: Path) -> list[tuple[str, str]]:
    """``(node, script)`` for every ``script`` and ``router`` node of a workflow and its subgraphs (all in one file)."""
    try:
        data = json.loads(workflow.read_text())
    except (OSError, ValueError) as error:
        raise sibling.Refusal("INVALID_VALUE", f"{workflow} is not readable JSON: {error}") from None
    if not isinstance(data, dict):
        raise sibling.Refusal("INVALID_VALUE", f"{workflow} must hold a JSON object.")
    subgraphs = data.get("subgraphs") or {}
    graphs = [("", data)] + [(f"{name}.", sub) for name, sub in subgraphs.items() if isinstance(sub, dict)] \
        if isinstance(subgraphs, dict) else [("", data)]
    found = []
    for prefix, graph in graphs:
        nodes = graph.get("nodes") or {}
        for node, spec in (nodes.items() if isinstance(nodes, dict) else ()):
            if isinstance(spec, dict) and spec.get("type") in ("script", "router"):
                script = spec.get("script")
                found.append((prefix + str(node), script if isinstance(script, str) else ""))
    return found


def check_graph_scripts(workflow: Path, trusted: bool, allow_scripts: bool) -> list[str]:
    """A graph's script and router nodes must name files inside the graph's own folder. They are arbitrary Python run
    inside Graph-as-Policy, so outside the pinned checkouts (``trusted``) they also need the owner's ``allow_scripts``.
    Returns the scripts it found."""
    folder = workflow.parent.resolve()
    scripts = graph_scripts(workflow)
    for node, script in scripts:
        path = Path(script)
        if not script or path.is_absolute() or ".." in path.parts or not (folder / path).resolve().is_relative_to(folder):
            raise sibling.Refusal("PATH_NOT_ALLOWED", f"node {node}: script {script!r} must be a relative path inside "
                                  f"the graph's folder {folder}.")
    if scripts and not trusted and not allow_scripts:
        raise sibling.Refusal("SCRIPT_NOT_ALLOWED", f"this graph has {len(scripts)} script or router node(s), which run "
                              "arbitrary Python inside Graph-as-Policy (twinsmith's --real guard covers only the command "
                              "line). The owner allows them for graphs outside the pinned checkouts with [planning] "
                              "allow_scripts = true in ~/.config/twinsmith/config.toml.")
    return [script for _, script in scripts]


def trusted_graph(path: Path, co: sibling.Checkout) -> bool:
    """Whether a graph ships in one of the two pinned checkouts, each at its tested commit."""
    from . import vabar

    for checkout in (co, vabar.checkout(workspace=co.workspace)):
        if path.is_relative_to(checkout.root.resolve()) and checkout.info().get("pin_match"):
            return True
    return False


def argv(co: sibling.Checkout, action: str, *, sim=None, graph=None, instruction=None, model=None, out=None) -> list[str]:
    """The command line for one action, from validated values only."""
    gap, library = str(co.bin("gap")), ["--skills", str(skills(co).root)]
    if action == "tools":
        return [gap, "tools", "list", "--format", "json", *library]
    if action == "check":
        return [gap, "check", "--format", "json", *library]
    if action == "generate":
        return [gap, "generate", instruction, *library, "--out", str(out), *(["--model", model] if model else [])]
    return [gap, "run", "--sim", sim, *library, "--trace-dir", str(out), graph]


def call(co: sibling.Checkout, action: str, sim=None, graph=None, instruction=None, model=None, name=None,
         timeout_s: float = 3600, allow_concurrent_gpu: bool = False) -> dict:
    info = co.info("gap")
    try:
        refuse_real([sim, graph, instruction, model, name])
        if action not in ACTIONS:
            raise sibling.Refusal("INVALID_VALUE", f"action must be one of {', '.join(ACTIONS)}.")
        allowed, scripts_allowed = owner_flags()
        if action == "status":
            return sibling.answer("gap", action, info, result=_status(co, info, allowed))
        if "setup" in info:
            raise sibling.Refusal("NOT_INSTALLED", info["setup"])
        if action in ("tools", "check"):
            command = argv(co, action)
            refuse_real(command)
            done = sibling.run(command, cwd=co.root, env_pass=run_env(allowed), timeout_s=300,
                               stdout_chars=sibling.WHOLE)
            try:
                result = json.loads(done["stdout"])
            except ValueError:
                return sibling.answer("gap", action, info, exit_code=done["exit_code"], error="gap did not print JSON.",
                                      code="UPSTREAM_FAILED", stdout=done["stdout"][-sibling.TAIL:], stderr=done["stderr"])
            return sibling.answer("gap", action, info, exit_code=done["exit_code"], result=result)
        if action == "generate":
            if not allowed:
                raise sibling.Refusal("PAID_NOT_ALLOWED", "generate calls a paid model. The owner allows it with "
                                      "[planning] allow_paid = true in ~/.config/twinsmith/config.toml.")
            if not isinstance(instruction, str) or not instruction.strip() or instruction.lstrip().startswith("-") \
                    or len(instruction) > 2000 or any(ord(c) < 32 for c in instruction):
                raise sibling.Refusal("INVALID_VALUE", "instruction must be 1 to 2000 characters of one-line text, "
                                      "not starting with '-'.")
            if model is not None and not (isinstance(model, str) and _MODEL.fullmatch(model)):
                raise sibling.Refusal("INVALID_VALUE", "model must be a provider model name such as vendor/model.")
        else:
            if not isinstance(sim, str) or not _SIM.fullmatch(sim):
                raise sibling.Refusal("INVALID_VALUE", "sim is required for run: SUITE/TASK, such as libero_object/0.")
            graph = _graph(graph, co)
            workflow = Path(graph) if Path(graph).is_file() else Path(graph) / "workflow.json"
            check_graph_scripts(workflow, trusted_graph(Path(graph), co), scripts_allowed)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= 86400:
            raise sibling.Refusal("INVALID_VALUE", "timeout_s must be more than 0 and at most 86400 seconds.")
        folder = co.workspace / "runs/gap" / sibling.run_name(name, action)
        command = argv(co, action, sim=sim, graph=graph, instruction=instruction, model=model, out=folder / "output")
        refuse_real(command)
        sibling.new_run_folder(folder)
        started = sibling.start_job(command, task=f"gap-{folder.name}", kind=f"gap-{action}", cwd=folder,
                                    env_pass=run_env(allowed),
                                    env_set={"MUJOCO_GL": "egl", "PYTHONUNBUFFERED": "1"}, timeout_s=timeout_s,
                                    uses_gpu=action == "run", allow_concurrent_gpu=allow_concurrent_gpu)
        if not started["ok"]:
            folder.rmdir()
            return sibling.answer("gap", action, info, error=started["message"], code=started["code"], hint=started["hint"])
        warnings = [] if allowed or action == "generate" else [
            "Paid model calls are off: OPENROUTER_API_KEY and GAP_VLM_* were not passed, so graph nodes that ask a VLM fail."]
        return sibling.answer("gap", action, info, warnings=warnings,
                              result={"job_id": started["job_id"], "log_dir": started["log_dir"], "run": folder.name, "output": str(folder / "output"), "command": command})
    except sibling.Refusal as refusal:
        return sibling.answer("gap", action, info, error=str(refusal), code=refusal.code)


def _status(co, info, allowed) -> dict:
    memory = gpu_memory_mib()
    largest = max(memory) if memory else None
    gpu = {"memory_mib": memory, "required_gb": 24, "enough": bool(largest and largest >= REQUIRED_GPU_MIB)}
    if not gpu["enough"]:
        gpu["note"] = ("No GPU could be read." if memory is None else
                       f"The largest GPU has {largest} MiB; Graph-as-Policy asks for 24 GB. Run it on a PC that has that.")
    return {**{key: info[key] for key in ("checkout", "environment_present") if key in info},
            **({"setup": info["setup"]} if "setup" in info else {}), "skills": skills_info(co), "gpu": gpu,
            "keys": {key: bool(os.environ.get(key)) for key in KEYS}, "allow_paid": allowed,
            "runs": str(co.workspace / "runs/gap")}


def register(server, *, workspace=None, root=None):
    co = checkout(workspace, root)

    @server.tool(name="gap", annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True,
    ))
    async def gap(
        action: Annotated[Literal["status", "tools", "check", "generate", "run"], Field(description="Action to perform.")],
        sim: Annotated[str | None, Field(description="run (required): simulated SUITE/TASK, such as libero_object/0. Simulation only.")] = None,
        graph: Annotated[str | None, Field(description="run (required): workflow directory or workflow.json, in this tool's run folder or the GaP or VABAR checkout.")] = None,
        instruction: Annotated[str | None, Field(description="generate (required): the task in words, one line.")] = None,
        model: Annotated[str | None, Field(description="generate: provider model name; omit for the upstream default.")] = None,
        name: Annotated[str | None, Field(description="generate/run: run folder name under <workspace>/runs/gap; default action and time.")] = None,
        timeout_s: Annotated[float, Field(description="generate/run: job time limit in seconds.", gt=0, le=86400)] = 3600,
        allow_concurrent_gpu: Annotated[bool, Field(description="run: knowingly start beside another GPU job or a running Isaac Sim.")] = False,
    ) -> str:
        """Graph-as-Policy skill graphs, from its own pinned checkout: status (checkout, GPU, keys), tools (the skill
        catalog), check (which tool bundles are ready), generate (a graph from an instruction; calls a paid model, only
        when the owner allows it), run (a graph in simulation, as a job; uses the GPU). Never --real; script nodes of a
        graph outside the pinned checkouts need the owner's allow_scripts.
        Follow jobs with jobs(action="status").
        """
        arguments = dict(sim=sim, graph=graph, instruction=instruction, model=model, name=name, timeout_s=timeout_s,
                         allow_concurrent_gpu=allow_concurrent_gpu)
        return json.dumps(await asyncio.to_thread(call, co, action, **arguments))

    return co

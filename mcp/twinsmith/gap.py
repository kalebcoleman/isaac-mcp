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
ACTIONS = ("status", "tools", "check", "validate", "generate", "run")
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


# Not secrets: where weights are cached, where upstream finds its benchmark roots, and which local perception models
# run on which device. Always passed when set.
PLAIN_ENV = ("HF_HOME", "GAP_VAB_ROOT", "GAP_LIBERO_PRO_ROOT", "GAP_SAM3_DEVICE", "GAP_DINO_DEVICE", "GAP_DINO_MODEL",
             "GAP_MOLMO_MODEL")
# A model server URL taken from the environment passes only when it points at this machine.
LOOPBACK_ENV = ("GAP_MOLMO_BASE_URL",)


def paid_env(allowed: bool) -> list[str]:
    """The model-provider variables a run may see: none unless the owner allowed paid calls."""
    if not allowed:
        return []
    return ["OPENROUTER_API_KEY", *(key for key in os.environ if key.startswith(("GAP_VLM_", "GAP_LLM_")))]


def run_env(allowed: bool) -> list[str]:
    """Every variable a Graph-as-Policy or VABAR process may see beyond the scrubbed base, ``model_env``'s aside."""
    return ["HF_TOKEN", *PLAIN_ENV, *paid_env(allowed)]


def owner_flags() -> tuple[bool, bool]:
    """``(allow_paid, allow_scripts)`` from the owner's own file; a malformed file is a refusal, not a crash."""
    try:
        return config.allow_paid(), config.allow_scripts()
    except (ValueError, TypeError) as error:
        raise sibling.Refusal("CONFIG_INVALID", f"~/.config/twinsmith/config.toml: {error}") from None


def owner_vlm() -> dict | None:
    """The owner's ``[planning] vlm_url``, ``vlm_model`` and ``vlm_api_key_file``, or None; a bad value is
    CONFIG_INVALID."""
    try:
        return config.vlm_endpoint()
    except (ValueError, TypeError) as error:
        raise sibling.Refusal("CONFIG_INVALID", f"~/.config/twinsmith/config.toml: {error}") from None


def vlm_status() -> dict | None:
    """``vlm_report`` for ``status``, which reports a bad setting rather than refusing, so it can explain it."""
    try:
        return vlm_report(owner_vlm())
    except sibling.Refusal as refusal:
        return {"error": str(refusal), "code": refusal.code}


def vlm_report(vlm: dict | None) -> dict | None:
    """What ``status`` shows of the endpoint: host, model and the key file's state, never the key."""
    if vlm is None:
        return None
    report = {"url": vlm["url"], "host": vlm["host"], "model": vlm["model"],
              "api_key_file": str(vlm["api_key_file"]) if vlm["api_key_file"] else None}
    if vlm["api_key_file"]:
        try:
            config.read_secret_file(vlm["api_key_file"], "planning.vlm_api_key_file")
            report["api_key_ready"] = True
        except ValueError as error:
            report.update(api_key_ready=False, api_key_problem=str(error))
    return report


def model_env(allowed: bool, local: dict | None, environ=None) -> tuple[list[str], dict, list[str]]:
    """``(env_pass, env_set, warnings)`` for a process that may ask a model. Paid keys and ``GAP_VLM_*``/``GAP_LLM_*``
    pass only with ``allow_paid``. The owner's VLM endpoint (``local``) is set from their config, never from the
    environment, with the key from their key file when they name one; it overrides any ``GAP_VLM_*`` that passed. A
    loopback-only URL from the environment is dropped when it is not."""
    environ = os.environ if environ is None else environ
    env_pass, warnings = run_env(allowed), []
    for name in LOOPBACK_ENV:
        if name in environ:
            try:
                config.loopback_url(environ[name], name)
                env_pass.append(name)
            except ValueError as error:
                warnings.append(f"{name} was not passed: {error}")
    env_set = {}
    if local:
        env_set = {"GAP_VLM_PROVIDER": "openrouter", "GAP_VLM_BASE_URL": local["url"]}
        if local.get("model"):
            env_set["GAP_VLM_MODEL"] = local["model"]
        # The endpoint's own key, or none. Upstream's VLM client falls back from GAP_VLM_API_KEY to OPENROUTER_API_KEY
        # (open-robot-skills tools/vlm/tools.py), so neither may pass with an endpoint set, even with allow_paid.
        env_pass = [name for name in env_pass if name not in ("GAP_VLM_API_KEY", "OPENROUTER_API_KEY")]
        if local.get("api_key_file"):
            try:
                env_set["GAP_VLM_API_KEY"] = config.read_secret_file(local["api_key_file"], "planning.vlm_api_key_file")
            except ValueError as error:
                raise sibling.Refusal("CONFIG_INVALID", f"~/.config/twinsmith/config.toml: {error}") from None
    return env_pass, env_set, warnings


def endpoint_env(local: dict | None) -> dict:
    """The endpoint's provider, URL and model, without its key: what ``check`` needs to report what a run will use."""
    if not local:
        return {}
    return {"GAP_VLM_PROVIDER": "openrouter", "GAP_VLM_BASE_URL": local["url"],
            **({"GAP_VLM_MODEL": local["model"]} if local.get("model") else {})}


def no_model_warning(allowed: bool, local: dict | None) -> list[str]:
    if allowed or local:
        return []
    return ["No model is available to graph nodes: paid calls are off (OPENROUTER_API_KEY, GAP_VLM_* and GAP_LLM_* were "
            "not passed) and no VLM endpoint is set, so nodes that ask a VLM fail. The owner can set [planning] vlm_url "
            "(and vlm_model, vlm_api_key_file) in ~/.config/twinsmith/config.toml, or allow_paid. Subscription CLIs "
            "(claude -p, codex exec) are not a provider."]


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


GRAPH_FILES = 10000   # a graph folder with more entries than this is refused rather than walked


def python_files(folder: Path) -> list[str]:
    """Every ``*.py`` under a graph folder, links included and never followed. Graph-as-Policy runs two kinds from the
    folder: script and router nodes, and ``checkpoints/<subgraph>.py`` after each subgraph (exec'd)."""
    found, seen = [], 0
    for top, dirs, files in os.walk(folder, followlinks=False):
        seen += len(dirs) + len(files)
        if seen > GRAPH_FILES:
            raise sibling.Refusal("INVALID_VALUE", f"{folder} has more than {GRAPH_FILES} entries; a graph folder is small.")
        found += [str((Path(top) / name).relative_to(folder)) for name in (*files, *dirs) if name.endswith(".py")]
    return sorted(found)


def graph_checkout(path: Path, co: sibling.Checkout) -> sibling.Checkout | None:
    """The Graph-as-Policy or VABAR checkout a graph sits in, or None for a graph in the run folder."""
    from . import vabar

    for checkout in (co, vabar.checkout(workspace=co.workspace)):
        if path.is_relative_to(checkout.root.resolve()):
            return checkout
    return None


def unchanged(checkout: sibling.Checkout, paths: list[Path]) -> bool:
    """Whether git sees every path as tracked and unmodified: nothing changed, untracked or ignored under them."""
    root = checkout.root.resolve()
    done = sibling.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all", "--ignored", "--",
                        *(str(path.relative_to(root)) for path in paths)], timeout_s=30, stdout_chars=sibling.WHOLE)
    return done["exit_code"] == 0 and not done["stdout"].strip()


def inspect_graph_code(workflow: Path, co: sibling.Checkout) -> dict:
    """Decide whether a graph's own Python may run. Script and router paths must stay inside the graph's folder or, for
    a graph that ships in a checkout, inside that checkout. A graph is trusted when its checkout is at the tested pin
    and git sees the graph folder and every script unchanged; any other graph with script or router nodes or ``*.py``
    files needs the owner's ``allow_scripts``. Returns ``{trusted, scripts, nodes, python_files}`` (absolute paths) and
    refuses only a script path that leaves its folder or checkout."""
    folder = workflow.parent.resolve()
    checkout = graph_checkout(folder, co)
    home = checkout.root.resolve() if checkout else folder
    scripts = graph_scripts(workflow)
    resolved = []
    for node, script in scripts:
        path = Path(script)
        target = (folder / path).resolve()
        if not script or path.is_absolute() or (checkout is None and ".." in path.parts) \
                or not target.is_relative_to(home):
            raise sibling.Refusal("PATH_NOT_ALLOWED", f"node {node}: script {script!r} must be a relative path inside "
                                  f"{'the checkout' if checkout else 'the graph folder'} {home}.")
        resolved.append(target)
    code = python_files(folder)
    trusted = bool(checkout and checkout.info().get("pin_match") and unchanged(checkout, [folder, *resolved]))
    return {"trusted": trusted, "folder": str(folder), "scripts": [str(path) for path in resolved],
            "nodes": [node for node, _ in scripts], "python_files": [str(folder / name) for name in code]}


def check_graph_code(workflow: Path, co: sibling.Checkout, allow_scripts: bool) -> dict:
    """``inspect_graph_code``, then SCRIPT_NOT_ALLOWED for a graph with Python that is neither trusted nor allowed."""
    found = inspect_graph_code(workflow, co)
    scripts, code = found["nodes"], found["python_files"]
    if (scripts or code) and not found["trusted"] and not allow_scripts:
        raise sibling.Refusal("SCRIPT_NOT_ALLOWED", f"this graph has {len(scripts)} script or router node(s) and "
                              f"{len(code)} Python file(s), which Graph-as-Policy runs (twinsmith's --real guard covers "
                              "only the command line). A graph that ships unchanged in a pinned checkout runs as it is; "
                              "for any other graph the owner allows them with [planning] allow_scripts = true in "
                              "~/.config/twinsmith/config.toml. gap(action=\"validate\") lists the files to read first.")
    return found


def argv(co: sibling.Checkout, action: str, *, sim=None, graph=None, instruction=None, model=None, out=None,
         checkpoints_off=False) -> list[str]:
    """The command line for one action, from validated values only."""
    gap, library = str(co.bin("gap")), ["--skills", str(skills(co).root)]
    if action == "tools":
        return [gap, "tools", "list", "--format", "json", *library]
    if action == "check":
        return [gap, "check", "--format", "json", *library]
    if action == "validate":
        return [gap, "run", "--validate-only", *library, graph]
    if action == "generate":
        return [gap, "generate", instruction, *library, "--out", str(out), *(["--model", model] if model else [])]
    return [gap, "run", "--sim", sim, *library, "--trace-dir", str(out),
            *(["--checkpoints", "off"] if checkpoints_off else []), graph]


def call(co: sibling.Checkout, action: str, sim=None, graph=None, instruction=None, model=None, name=None,
         timeout_s: float = 3600, allow_concurrent_gpu: bool = False) -> dict:
    info = co.info("gap")
    try:
        refuse_real([sim, graph, instruction, model, name])
        if action not in ACTIONS:
            raise sibling.Refusal("INVALID_VALUE", f"action must be one of {', '.join(ACTIONS)}.")
        allowed, scripts_allowed = owner_flags()
        if action == "status":
            return sibling.answer("gap", action, info, result=_status(co, info, allowed, vlm_status()))
        # Only run asks the endpoint; check reports what it will use. Other actions never read its settings.
        local = owner_vlm() if action in ("run", "check") else None
        if "setup" in info:
            raise sibling.Refusal("NOT_INSTALLED", info["setup"])
        if action in ("tools", "check"):
            command = argv(co, action)
            refuse_real(command)
            done = sibling.run(command, cwd=co.root, env_pass=run_env(allowed), env_set=endpoint_env(local),
                               timeout_s=300, stdout_chars=sibling.WHOLE)
            try:
                result = json.loads(done["stdout"])
            except ValueError:
                return sibling.answer("gap", action, info, exit_code=done["exit_code"], error="gap did not print JSON.",
                                      code="UPSTREAM_FAILED", stdout=done["stdout"][-sibling.TAIL:], stderr=done["stderr"])
            return sibling.answer("gap", action, info, exit_code=done["exit_code"], result=result)
        if action == "validate":
            return validate(co, info, graph, scripts_allowed)
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
            code = check_graph_code(workflow, co, scripts_allowed)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= 86400:
            raise sibling.Refusal("INVALID_VALUE", "timeout_s must be more than 0 and at most 86400 seconds.")
        folder = co.workspace / "runs/gap" / sibling.run_name(name, action)
        # An untrusted graph without the owner's allow_scripts had no Python when it was checked; checkpoints off keeps
        # a checkpoint file added after the check from running.
        off = action == "run" and not code["trusted"] and not scripts_allowed
        command = argv(co, action, sim=sim, graph=graph, instruction=instruction, model=model, out=folder / "output",
                       checkpoints_off=off)
        refuse_real(command)
        # generate's planner is not the VLM client: it keeps allow_paid's key and gets no endpoint.
        env_pass, env_set, env_warnings = model_env(allowed, local if action == "run" else None)
        sibling.new_run_folder(folder)
        started = sibling.start_job(command, task=f"gap-{folder.name}", kind=f"gap-{action}", cwd=folder,
                                    env_pass=env_pass,
                                    env_set={**env_set, "MUJOCO_GL": "egl", "PYTHONUNBUFFERED": "1"}, timeout_s=timeout_s,
                                    uses_gpu=action == "run", allow_concurrent_gpu=allow_concurrent_gpu)
        if not started["ok"]:
            folder.rmdir()
            return sibling.answer("gap", action, info, error=started["message"], code=started["code"], hint=started["hint"])
        warnings = env_warnings + ([] if action == "generate" else no_model_warning(allowed, local))
        return sibling.answer("gap", action, info, warnings=warnings,
                              result={"job_id": started["job_id"], "log_dir": started["log_dir"], "run": folder.name, "output": str(folder / "output"), "command": command})
    except sibling.Refusal as refusal:
        return sibling.answer("gap", action, info, error=str(refusal), code=refusal.code)


def validate(co: sibling.Checkout, info: dict, graph, scripts_allowed: bool) -> dict:
    """Upstream's ``gap run --validate-only``: the graph's structure, types and skill contracts, without a simulator,
    GPU or model. Upstream imports every script node's module to read its signature, so a graph with Python that is
    neither trusted nor allowed is not handed to it: the answer lists the nodes, scripts and Python files to read."""
    graph = _graph(graph, co)
    workflow = Path(graph) if Path(graph).is_file() else Path(graph) / "workflow.json"
    found = inspect_graph_code(workflow, co)
    listing = {key: found[key] for key in ("folder", "trusted", "nodes", "scripts", "python_files")}
    if (found["nodes"] or found["python_files"]) and not found["trusted"] and not scripts_allowed:
        return sibling.answer("gap", "validate", info, result={
            "validated": False, **listing,
            "note": "Not validated: upstream's validator imports the graph's scripts, which are arbitrary Python. Read "
                    "them, then the owner may set [planning] allow_scripts = true in ~/.config/twinsmith/config.toml."})
    command = argv(co, "validate", graph=graph)
    refuse_real(command)
    done = sibling.run(command, cwd=co.root, env_pass=["HF_TOKEN", *PLAIN_ENV], timeout_s=300,
                       stdout_chars=sibling.WHOLE)
    lines = [line.strip() for line in done["stdout"].splitlines() if line.strip()]
    # Upstream ends with "OK: 0 errors, N warning(s)" (exit 0), "FAIL: N error(s), M warning(s)" or, for a graph it
    # cannot load, "FAIL: <reason>" as its only line (exit 1). Anything else is a crash, not a verdict.
    last = lines[-1] if lines else ""
    counted = re.fullmatch(r"(OK: 0 errors|FAIL: \d+ error\(s\)), \d+ warning\(s\)", last)
    verdict = last if (done["exit_code"] == 0 and counted and last.startswith("OK")) or (
        done["exit_code"] == 1 and (counted and last.startswith("FAIL") or (len(lines) == 1 and last.startswith("FAIL: ")))
    ) else None
    if verdict is None:
        return sibling.answer("gap", "validate", info, exit_code=done["exit_code"], code="UPSTREAM_FAILED",
                              error="gap run --validate-only did not finish with a verdict.",
                              stdout=done["stdout"][-sibling.TAIL:], stderr=done["stderr"])
    issues = [line for line in lines if line != verdict]
    return sibling.answer("gap", "validate", info, exit_code=done["exit_code"], result={
        "validated": True, "valid": done["exit_code"] == 0, "verdict": verdict, "issues": issues[:200],
        **({"issues_truncated": len(issues)} if len(issues) > 200 else {}), **listing})


def _status(co, info, allowed, local=None) -> dict:
    memory = gpu_memory_mib()
    largest = max(memory) if memory else None
    gpu = {"memory_mib": memory, "required_gb": 24, "enough": bool(largest and largest >= REQUIRED_GPU_MIB)}
    if not gpu["enough"]:
        gpu["note"] = ("No GPU could be read." if memory is None else
                       f"The largest GPU has {largest} MiB; Graph-as-Policy asks for 24 GB. Run it on a PC that has that.")
    return {**{key: info[key] for key in ("checkout", "environment_present") if key in info},
            **({"setup": info["setup"]} if "setup" in info else {}), "skills": skills_info(co), "gpu": gpu,
            "keys": {key: bool(os.environ.get(key)) for key in KEYS}, "allow_paid": allowed, "vlm": local,
            "runs": str(co.workspace / "runs/gap")}


def register(server, *, workspace=None, root=None):
    co = checkout(workspace, root)

    @server.tool(name="gap", annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True,
    ))
    async def gap(
        action: Annotated[Literal["status", "tools", "check", "validate", "generate", "run"], Field(description="Action to perform.")],
        sim: Annotated[str | None, Field(description="run (required): simulated SUITE/TASK, such as libero_object/0. Simulation only.")] = None,
        graph: Annotated[str | None, Field(description="validate/run (required): workflow directory or workflow.json, in this tool's run folder or the GaP or VABAR checkout.")] = None,
        instruction: Annotated[str | None, Field(description="generate (required): the task in words, one line.")] = None,
        model: Annotated[str | None, Field(description="generate: provider model name; omit for the upstream default.")] = None,
        name: Annotated[str | None, Field(description="generate/run: run folder name under <workspace>/runs/gap; default action and time.")] = None,
        timeout_s: Annotated[float, Field(description="generate/run: job time limit in seconds.", gt=0, le=86400)] = 3600,
        allow_concurrent_gpu: Annotated[bool, Field(description="run: knowingly start beside another GPU job or a running Isaac Sim.")] = False,
    ) -> str:
        """Graph-as-Policy skill graphs, from its own pinned checkout: status (checkout, GPU, keys), tools (the skill
        catalog), check (which tool bundles are ready), validate (a graph's structure and skill contracts; no GPU or
        model, for a graph you wrote), generate (a graph from an instruction; calls a paid model, only when the owner
        allows it), run (a graph in simulation, as a job; uses the GPU). Never --real; script nodes of a graph outside
        the pinned checkouts need the owner's allow_scripts.
        Follow jobs with jobs(action="status").
        """
        arguments = dict(sim=sim, graph=graph, instruction=instruction, model=model, name=name, timeout_s=timeout_s,
                         allow_concurrent_gpu=allow_concurrent_gpu)
        return json.dumps(await asyncio.to_thread(call, co, action, **arguments))

    return co

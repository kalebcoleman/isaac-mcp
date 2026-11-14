"""``vabar``: the VABAR benchmark's command line, from its own pinned checkout.

VABAR runs task variants and frozen benchmark cells on Newton through RoboSimStudio, solving them with scripted routines
or Graph-as-Policy graphs. Its environment holds RoboSimStudio and Graph-as-Policy at VABAR's own pins; the
Graph-as-Policy and skill-library checkouts beside it are the ones ``gap`` uses. Nothing here imports VABAR.
``warmup`` and ``studio`` are not wrapped: the owner runs the one-time warmup, and studio serves a network viewer.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from . import config, gap, sibling

PIN = "64b594ed2e1ae25e431d235de086c7cfd99d9aaf"       # "VABAR 1.0 release"
REPOSITORY = "https://github.com/BerkeleyAutomation/VABAR"
ACTIONS = ("status", "list", "run", "bench", "results")
SOLVERS = ("routine", "graph", "idle")
_TASK = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+")
_CELL = re.compile(r"[a-z0-9_]+/L[0-9]+")
_LABEL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}")
_EPISODES = re.compile(r"[0-9]+(:[0-9]+(:[0-9]+)?|(,[0-9]+)+)?")
RESULT_FILES = 200


def checkout(workspace=None, root=None) -> sibling.Checkout:
    return sibling.Checkout("vabar", "TWINSMITH_VABAR_ROOT", PIN, REPOSITORY, workspace=workspace, root=root)


def warmup(co: sibling.Checkout) -> dict:
    """What VABAR's warmup leaves in its renderer cache: the compiled-pipelines stamp and one stamp per warmed cell."""
    caches = sorted(co.root.glob(".venv/lib/python3*/site-packages/ovrtx/bin/cache"))
    if not caches:
        return {"ran": False, "note": "No renderer cache in VABAR's environment."}
    cells = sorted(path.name for path in (caches[0] / "vabar_warm").glob("*") if path.is_file())
    return {"ran": (caches[0] / "nv_shadercache/vabar_compiled").is_file(), "warm_cells": cells}


def argv(co: sibling.Checkout, action: str, *, task=None, solve="routine", graph=None, episodes=None, cell=None,
         limit=None, workers=1, out=None) -> list[str]:
    """The command line for one action, from validated values only."""
    base = [str(co.bin("python")), "-m", "vabar"]
    if action == "list":
        return [*base, "list"]
    if action == "run":
        return [*base, "run", "--task", task, "--solve", solve, *(["--graph", graph] if graph else []),
                *(["--episodes", episodes] if episodes else []), "--out", str(out)]
    return [*base, "bench", "--cell", cell, "--solve", solve, *(["--limit", str(limit)] if limit else []),
            "--workers", str(workers), "--out", str(out)]


def results(co: sibling.Checkout, run=None) -> dict:
    """The summary table of one run folder (success per task and solver), or the newest run folders."""
    runs = co.workspace / "runs/vabar"
    if run is None:
        names = sorted((path for path in runs.glob("*") if path.is_dir()), key=lambda path: path.stat().st_mtime)
        return {"runs": [path.name for path in names[-10:][::-1]], "folder": str(runs)}
    folder = runs / sibling.run_name(run, "")
    if not folder.is_dir():
        raise sibling.Refusal("NOT_FOUND", f"No run folder {folder}.")
    table, files = {}, sorted(folder.glob("**/episode_results.jsonl"))[:RESULT_FILES]
    for path in files:
        for line in path.read_text(errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                entry = table.setdefault((str(row.get("task")), str(row.get("solution"))), [0, 0])
                entry[0] += 1
                entry[1] += bool(row.get("success"))
    rows = [{"task": task, "solution": solution, "episodes": count, "successes": wins, "success_rate": round(wins / count, 4)}
            for (task, solution), (count, wins) in sorted(table.items())]
    return {"run": folder.name, "folder": str(folder), "result_files": [str(path.relative_to(folder)) for path in files],
            "rows": rows, "episodes": sum(row["episodes"] for row in rows),
            "successes": sum(row["successes"] for row in rows)}


def call(co: sibling.Checkout, action: str, task=None, solve="routine", graph=None, episodes=None, cell=None, limit=None,
         workers: int = 1, print_only: bool = True, name=None, run=None, timeout_s: float = 6 * 3600,
         allow_concurrent_gpu: bool = False) -> dict:
    info = co.info("python")
    try:
        if action not in ACTIONS:
            raise sibling.Refusal("INVALID_VALUE", f"action must be one of {', '.join(ACTIONS)}.")
        if action == "status":
            library = gap.checkout(workspace=co.workspace)
            beside = {key: value for key, value in library.info("gap").items() if key != "setup"}
            return sibling.answer("vabar", action, info, result={
                **{key: info[key] for key in ("checkout", "environment_present", "setup") if key in info},
                "graph_as_policy": beside, "skills": gap.skills_info(library), "warmup": warmup(co),
                "allow_paid": config.allow_paid(), "runs": str(co.workspace / "runs/vabar")})
        if action == "results":
            return sibling.answer("vabar", action, info, result=results(co, run))
        if "setup" in info:
            raise sibling.Refusal("NOT_INSTALLED", info["setup"])
        allowed = config.allow_paid()
        env = {"VABAR_OPEN_ROBOT_SKILLS": str(gap.skills(gap.checkout(workspace=co.workspace)).root)}
        env_pass = ["HF_TOKEN", *gap.paid_env(allowed)]
        if action == "list":
            done = sibling.run(argv(co, action), cwd=co.root, env_pass=env_pass, env=env, timeout_s=300)
            tasks = [line.strip() for line in done["stdout"].splitlines() if _TASK.fullmatch(line.strip())]
            if done["exit_code"] != 0:
                return sibling.answer("vabar", action, info, exit_code=done["exit_code"], error="vabar list failed.",
                                      code="UPSTREAM_FAILED", stderr=done["stderr"])
            return sibling.answer("vabar", action, info, exit_code=0, result={"tasks": tasks})
        if solve not in SOLVERS:
            raise sibling.Refusal("INVALID_VALUE", f"solve must be one of {', '.join(SOLVERS)}.")
        if graph is not None and (solve != "graph" or not isinstance(graph, str) or not _LABEL.fullmatch(graph)):
            raise sibling.Refusal("INVALID_VALUE", "graph is a label the task ships, such as gt, and needs solve=graph.")
        if action == "run":
            if not isinstance(task, str) or not _TASK.fullmatch(task):
                raise sibling.Refusal("INVALID_VALUE", "task is required for run: SUITE/VARIANT; vabar(action=\"list\") lists them.")
            if episodes is not None and not (isinstance(episodes, str) and _EPISODES.fullmatch(episodes)):
                raise sibling.Refusal("INVALID_VALUE", "episodes is a range '0:16', a strided range '0:100:4' or a list '0,4,9'.")
        else:
            if not isinstance(cell, str) or not _CELL.fullmatch(cell):
                raise sibling.Refusal("INVALID_VALUE", "cell is required for bench: TASK_CLASS/L<level>, such as tool_hanging/L1.")
            if type(workers) is not int or not 1 <= workers <= 4:
                raise sibling.Refusal("INVALID_VALUE", "workers must be 1 to 4.")
            if limit is not None and (type(limit) is not int or limit < 1):
                raise sibling.Refusal("INVALID_VALUE", "limit must be a positive number of instances.")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= 7 * 86400:
            raise sibling.Refusal("INVALID_VALUE", "timeout_s must be more than 0 and at most 7 days in seconds.")
        folder = co.workspace / "runs/vabar" / sibling.run_name(name, action)
        command = argv(co, action, task=task, solve=solve, graph=graph, episodes=episodes, cell=cell, limit=limit,
                       workers=workers, out=folder)
        if action == "bench" and print_only:
            return sibling.answer("vabar", action, info, result={
                "planned": command, "note": "Nothing ran. Call again with print_only=false to run it as a job; it uses the GPU."})
        if folder.exists():
            raise sibling.Refusal("ALREADY_EXISTS", f"{folder} exists; choose another name.")
        folder.mkdir(parents=True)
        started = sibling.start_job(command, kind=f"vabar-{action}", label=f"vabar-{folder.name}", cwd=folder,
                                    env_pass=env_pass, env=env, timeout_s=timeout_s, allow_concurrent_gpu=allow_concurrent_gpu)
        if "error" in started:
            folder.rmdir()
            return sibling.answer("vabar", action, info, **started, code="GUARD_REFUSED")
        warnings = [] if allowed or solve != "graph" else [
            "Paid model calls are off: graphs whose nodes ask a VLM fail. The owner allows them with [planning] allow_paid."]
        return sibling.answer("vabar", action, info, warnings=warnings, result={
            **started, "run": folder.name, "command": command,
            "next": f'jobs(action="status", job_id="{started["job_id"]}"), then vabar(action="results", run="{folder.name}")'})
    except sibling.Refusal as refusal:
        return sibling.answer("vabar", action, info, error=str(refusal), code=refusal.code)


def register(server, *, workspace=None, root=None):
    co = checkout(workspace, root)

    @server.tool(name="vabar", annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True,
    ))
    async def vabar(
        action: Annotated[Literal["status", "list", "run", "bench", "results"], Field(description="Action to perform.")],
        task: Annotated[str | None, Field(description="run (required): SUITE/VARIANT from list, such as tool_hanging/wrenches_level1.")] = None,
        solve: Annotated[Literal["routine", "graph", "idle"], Field(description="run/bench: the scripted routine, a Graph-as-Policy graph, or idle.")] = "routine",
        graph: Annotated[str | None, Field(description="run with solve=graph: a graph label the task ships, such as gt; omit for its default.")] = None,
        episodes: Annotated[str | None, Field(description="run: '0:16', '0:100:4' or '0,4,9'; default the first episode.")] = None,
        cell: Annotated[str | None, Field(description="bench (required): benchmark cell TASK_CLASS/L<level>, such as tool_hanging/L1.")] = None,
        limit: Annotated[int | None, Field(description="bench: run only the first N instances.", ge=1)] = None,
        workers: Annotated[int, Field(description="bench: worker processes, 1 to 4.", ge=1, le=4)] = 1,
        print_only: Annotated[bool, Field(description="bench: return the planned command without running it.")] = True,
        name: Annotated[str | None, Field(description="run/bench: run folder name under <workspace>/runs/vabar; default action and time.")] = None,
        run: Annotated[str | None, Field(description="results: run folder name; omit to list the newest runs.")] = None,
        timeout_s: Annotated[float, Field(description="run/bench: job time limit in seconds.", gt=0, le=604800)] = 21600,
        allow_concurrent_gpu: Annotated[bool, Field(description="run/bench: knowingly start beside another GPU job or a running Isaac Sim.")] = False,
    ) -> str:
        """VABAR benchmark from its own pinned checkout: status (pins, warmup), list (tasks), run (a task's episodes, as a
        job; uses the GPU), bench (a frozen cell; print_only=true returns the plan, false runs it as a job), results
        (success table of a run folder). Follow jobs with jobs(action="status").
        """
        arguments = dict(task=task, solve=solve, graph=graph, episodes=episodes, cell=cell, limit=limit, workers=workers,
                         print_only=print_only, name=name, run=run, timeout_s=timeout_s,
                         allow_concurrent_gpu=allow_concurrent_gpu)
        return json.dumps(await asyncio.to_thread(call, co, action, **arguments))

    return co

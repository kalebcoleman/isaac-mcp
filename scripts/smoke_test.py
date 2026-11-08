#!/usr/bin/env python3
"""Smoke test for a machine: does this checkout's MCP server work here?

    "$(uv tool dir)/isaac-mcp/bin/python" scripts/smoke_test.py [--task Isaac-Cartpole] [--no-lab]

Checks, in order, and prints PASS / FAIL / SKIP for each (exit 1 on any FAIL):
  1. the host server builds and lists isaac_help and the Isaac Lab tools;
  2. isaac_help and isaaclab__preflight answer (no GPU or simulator needed; preflight reports what it sees);
  3. with an Isaac Lab install (ISAACLAB_PYTHON / ISAACLAB_ROOT, or found automatically): isaaclab__env_check and
     isaaclab__record on a small task, each as a real job (about a minute; uses the GPU lightly).
Nothing is written outside the job folder Isaac Lab jobs already use.
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))

failures = 0


def report(status, name, detail=""):
    global failures
    failures += status == "FAIL"
    print(f"{status:5} {name}" + (f": {detail}" if detail else ""), flush=True)


async def call(server, tool, args):
    res = await server.call_tool(tool, args)
    parts = res[0] if isinstance(res, tuple) else res
    text = "".join(getattr(p, "text", str(p)) for p in parts)
    try:
        return json.loads(text)
    except ValueError:
        return text


async def wait_job(server, job_id, limit_s):
    end = time.time() + limit_s
    while time.time() < end:
        st = await call(server, "isaaclab__runs_status", {"job_id": job_id})
        if isinstance(st, dict) and st.get("state") not in ("running", None):
            return st
        await asyncio.sleep(10)
    return {"state": "still running after %d s" % limit_s}


async def main(task, lab):
    from isaac_mcp import server as gw
    server = gw.build_server()
    names = set(server._tool_manager._tools)
    need = {"isaac_help", "isaaclab__preflight", "isaaclab__env_check", "isaaclab__record"}
    report("PASS" if need <= names else "FAIL", "server lists the new tools", ", ".join(sorted(need - names)))
    out = await call(server, "isaac_help", {"task": "record a video"})
    report("PASS" if "screenshot" in str(out) else "FAIL", "isaac_help answers")
    pf = await call(server, "isaaclab__preflight", {})
    report("PASS" if isinstance(pf, dict) and "can_start" in pf else "FAIL", "isaaclab__preflight answers",
           f"can_start={pf.get('can_start')} reasons={pf.get('reasons')}" if isinstance(pf, dict) else str(pf)[:200])
    if not lab:
        report("SKIP", "Isaac Lab jobs", "--no-lab")
        return
    info = await call(server, "isaaclab__install_info", {})
    if not isinstance(info, dict) or info.get("error"):
        report("SKIP", "Isaac Lab jobs", "no Isaac Lab install found (set ISAACLAB_PYTHON and ISAACLAB_ROOT)")
        return
    listed = await call(server, "isaaclab__envs_list", {"filter": task})
    tasks = [e.get("id") for e in listed.get("envs", [])] if isinstance(listed, dict) else None
    if not tasks:
        report("SKIP", "Isaac Lab jobs", f"no task matching {task!r}; pass --task")
        return
    task = next((t for t in tasks if t == task), tasks[0])
    job = await call(server, "isaaclab__env_check", {"task": task, "num_envs": 4, "steps": 60, "timeout_s": 600})
    if not isinstance(job, dict) or "job_id" not in job:
        report("FAIL", "isaaclab__env_check starts", str(job)[:300])
        return
    st = await wait_job(server, job["job_id"], 600)
    res = st.get("env_check_result") or {}
    ok = st.get("state") == "succeeded" and res.get("ok") and 60 <= res.get("steps", 0) <= 62
    report("PASS" if ok else "FAIL", f"isaaclab__env_check {task}",
           f"state={st.get('state')} steps={res.get('steps')} problems={res.get('problems')} gpu_mib={res.get('gpu_mib')}")
    job = await call(server, "isaaclab__record", {"task": task, "video_length": 100, "num_envs": 1, "timeout_s": 900})
    if not isinstance(job, dict) or "job_id" not in job:
        report("FAIL", "isaaclab__record starts", str(job)[:300])
        return
    st = await wait_job(server, job["job_id"], 900)
    videos = [v for v in st.get("video_paths", []) if Path(v).is_file()]
    report("PASS" if st.get("state") == "succeeded" and videos else "FAIL", "isaaclab__record",
           f"state={st.get('state')} videos={videos}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--task", default="Isaac-Cartpole")
    ap.add_argument("--no-lab", action="store_true", help="skip the Isaac Lab jobs")
    a = ap.parse_args()
    asyncio.run(main(a.task, not a.no_lab))
    print("\nall checks passed" if not failures else f"\n{failures} check(s) failed")
    sys.exit(1 if failures else 0)

"""Background jobs on the bridge: a job id, progress, a result and cancel for work that outlives one call."""

import asyncio
import sys
import types

import pytest

from twinsmith_bridge import builtins as bi
from twinsmith_bridge import jobs
from twinsmith_bridge.bridge import McpBridge
from twinsmith_bridge.protocol import ToolError
from twinsmith_bridge.sessions import SessionStore


@pytest.fixture
def anyio_backend():
    return "asyncio"


def store():
    return jobs.JobStore(asyncio.ensure_future)


async def settle(job, turns=50):
    for _ in range(turns):
        if job.state != "running":
            return
        await asyncio.sleep(0)
    raise AssertionError(f"{job.id} is still {job.state}")


@pytest.mark.anyio
async def test_a_job_reports_progress_then_its_result():
    release = asyncio.Event()

    async def work(job):
        job.report(0.25, "a quarter", frames=3)
        await release.wait()
        return {"answer": 42}

    jobs_ = store()
    job = jobs_.start("demo", work, label="first")
    await asyncio.sleep(0)
    running = job.describe()
    assert running["state"] == "running" and running["label"] == "first" and "result" not in running
    assert running["progress"]["percent"] == 25.0 and running["progress"]["text"] == "a quarter" and running["progress"]["frames"] == 3
    assert jobs_.summary()["running"] == 1
    release.set()
    await settle(job)
    done = job.describe()
    assert done["state"] == "succeeded" and done["result"] == {"answer": 42} and done["progress"]["percent"] == 100.0
    assert jobs_.summary() == {"running": 0, "jobs": []}


@pytest.mark.anyio
async def test_a_failing_job_ends_failed_with_the_error_and_its_details():
    class Refusal(Exception):
        code = "CONTROLLER_FAILED"
        details = {"step": 7}

    async def work(job):
        raise Refusal("the controller raised")

    job = store().start("demo", work)
    await settle(job)
    out = job.describe()
    assert out["state"] == "failed" and "the controller raised" in out["error"]
    assert out["code"] == "CONTROLLER_FAILED" and out["step"] == 7 and "Refusal" in out["traceback"]


@pytest.mark.anyio
async def test_cancel_stops_a_cooperative_job_at_its_checkpoint_and_other_work_at_its_await():
    cleaned = []

    async def cooperative(job):
        try:
            while True:
                job.checkpoint()
                await asyncio.sleep(0)
        finally:
            cleaned.append("cooperative")

    async def plain(job):
        await asyncio.Event().wait()

    jobs_ = store()
    first, second = jobs_.start("sim.run", cooperative), jobs_.start("run_python", plain, cooperative=False)
    await asyncio.sleep(0)
    assert "Cancel requested" in jobs_.cancel(first.id)["note"]
    jobs_.cancel(second.id)
    await settle(first)
    await settle(second)
    assert first.state == second.state == "cancelled" and cleaned == ["cooperative"]
    assert "already ended" in jobs_.cancel(first.id)["note"]


@pytest.mark.anyio
async def test_cancel_all_only_touches_the_named_kinds():
    async def wait(job):
        while True:
            job.checkpoint()
            await asyncio.sleep(0)

    jobs_ = store()
    sim, other = jobs_.start("sim.simulation.run", wait), jobs_.start("run_python", wait)
    assert jobs_.cancel_all(("sim.",)) == [sim.id]
    await settle(sim)
    assert other.state == "running"
    jobs_.cancel(other.id)
    await settle(other)


@pytest.mark.anyio
async def test_limits_unknown_ids_and_oversized_results():
    async def wait(job):
        await asyncio.Event().wait()

    async def big(job):
        return "x" * (jobs.RESULT_LIMIT + 10)

    jobs_ = store()
    held = [jobs_.start("demo", wait, cooperative=False) for _ in range(jobs.MAX_RUNNING)]
    with pytest.raises(jobs.JobError, match="already running"):
        jobs_.start("demo", wait)
    with pytest.raises(jobs.JobError, match="unknown job"):
        jobs_.get("job-nope")
    jobs_.cancel(held[0].id)
    await settle(held[0])
    with pytest.raises(jobs.JobError, match="label"):
        jobs_.start("demo", wait, label="x" * 81)
    job = jobs_.start("demo", big)
    await settle(job)
    assert job.describe()["result"]["truncated"] is True
    for other in held[1:]:
        jobs_.cancel(other.id)


# ---- run_python(background=true), job_status, job_cancel ----

@pytest.fixture
def bridge(monkeypatch):
    pytest.importorskip("pxr")
    monkeypatch.setitem(sys.modules, "carb", types.ModuleType("carb"))
    monkeypatch.setitem(sys.modules, "omni", types.ModuleType("omni"))
    yield types.SimpleNamespace(sessions=SessionStore(), jobs=store())
    bi.remove_stdout_proxy()


@pytest.mark.anyio
async def test_background_run_python_returns_a_job_and_status_carries_its_result(bridge):
    code = ("import asyncio\nprint('started')\nfor i in range(3):\n    job.report((i + 1) / 3, f'step {i}')\n"
            "    await asyncio.sleep(0)\nresult = {'steps': 3}")
    started = bi.cmd_run_python(bridge, code, background=True, label="loop")
    assert started["state"] == "running" and "job_status" in started["next"]
    job = bridge.jobs.get(started["job_id"])
    await settle(job)
    status = bi.cmd_job_status(bridge, started["job_id"])
    assert status["state"] == "succeeded" and status["result"]["result"] == {"steps": 3}
    assert status["output_tail"] == "started\n" and status["label"] == "loop" and status["kind"] == "run_python"
    listing = bi.cmd_job_status(bridge)
    assert listing["running"] == 0 and listing["jobs"][0]["job_id"] == started["job_id"]


@pytest.mark.anyio
async def test_background_code_must_await_and_its_errors_reach_job_status(bridge):
    with pytest.raises(ToolError, match="needs code that awaits"):
        bi.cmd_run_python(bridge, "result = 1", background=True)
    started = bi.cmd_run_python(bridge, "import asyncio\nawait asyncio.sleep(0)\nraise ValueError('boom')", background=True)
    job = bridge.jobs.get(started["job_id"])
    await settle(job)
    status = bi.cmd_job_status(bridge, started["job_id"])
    assert status["state"] == "failed" and "boom" in status["error"] and "ValueError" in status["traceback"]


@pytest.mark.anyio
async def test_job_cancel_stops_background_code_and_unknown_ids_are_refused(bridge):
    started = bi.cmd_run_python(bridge, "import asyncio\nawait asyncio.Event().wait()", background=True)
    await asyncio.sleep(0)
    assert bi.cmd_job_cancel(bridge, started["job_id"])["cancel_requested"] is True
    job = bridge.jobs.get(started["job_id"])
    await settle(job)
    assert bi.cmd_job_status(bridge, started["job_id"])["state"] == "cancelled"
    for call in (bi.cmd_job_status, bi.cmd_job_cancel):
        with pytest.raises(ToolError, match="unknown job"):
            call(bridge, "job-0-00000")


@pytest.mark.anyio
async def test_the_job_handle_is_not_saved_as_a_session_variable(bridge):
    started = bi.cmd_run_python(bridge, "import asyncio\nawait asyncio.sleep(0)\nkept = 1", background=True,
                                persistent=True, session_id="s")
    await settle(bridge.jobs.get(started["job_id"]))
    assert sorted(bridge.sessions.namespace("s")) == ["asyncio", "kept"]


def test_persistent_replies_list_only_the_names_a_call_added_or_rebound(bridge):
    first = bi.cmd_run_python(bridge, "a = 1\nb = [1]", persistent=True, session_id="s")
    assert first["session_vars"] == ["a", "b"] and first["session_var_count"] == 2
    second = bi.cmd_run_python(bridge, "c = a + 1\nb.append(2)", persistent=True, session_id="s")
    assert second["session_vars"] == ["c"] and second["session_var_count"] == 3
    third = bi.cmd_run_python(bridge, "a = 5\nresult = b", persistent=True, session_id="s")
    assert third["session_vars"] == ["a", "result"] and third["result"] == [1, 2]


def test_the_bridge_serves_the_job_tools_and_status_names_running_jobs():
    bridge = McpBridge(schedule_coroutine=lambda coro: coro.close())
    tools = bridge.get_registered_tools()
    assert {"job_status", "job_cancel"} <= set(tools)
    assert {"background", "label"} <= set(tools["run_python"]["parameters"])
    assert "detail" in tools["status"]["parameters"]
    status = bi.cmd_status(bridge)
    assert status["jobs"] == {"running": 0, "jobs": []} and status == {**bi.cmd_status(bridge, detail=False), "uptime_s": status["uptime_s"]}

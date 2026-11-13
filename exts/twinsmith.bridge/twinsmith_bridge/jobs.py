"""Background jobs on Kit's main loop: a job id, progress, a result and cancel for work that
outlives one tool call.

A job is one coroutine scheduled on the loop the tool handlers run on. The call that starts it
returns the job id at once; ``job_status`` reads it and ``job_cancel`` stops it. A job holds no
place among the bridge's requests, so it must only ``await`` (for example ``next_update_async``)
and never pump the app loop itself, and an exclusive call that pumps the loop beside it can end
it. Either way the job then reads ``failed`` with the reason, never ``running`` forever.

With a record folder, every job also keeps a ``job.json`` in the same form the Isaac Lab job
runner uses (``status``, ``pid``, ``started_at``, ``progress``, ...), in the same folder. So one
listing covers simulator jobs and process jobs, and a finished job can still be read after Kit or
the MCP server has restarted.

Stdlib-only: importable off-Kit (tests, lint).
"""

import asyncio
import itertools
import json
import os
import re
import tempfile
import threading
import time
import traceback
from typing import Any, Awaitable, Callable, Dict, List, Optional

MAX_RUNNING = 4          # jobs share one main loop; more than a few starve the app
MAX_KEPT = 32            # finished jobs remembered for job_status
RESULT_LIMIT = 16384     # bytes of JSON a result may take in a status reply
OUTPUT_TAIL = 2000       # characters of captured output a status reply carries
STATES = ("running", "succeeded", "failed", "cancelled")
RECORD_INTERVAL_S = 0.5  # at most this often for progress; a start and an end are always written


def default_record_dir() -> str:
    """The jobs folder the MCP host reads: the same rule as its Isaac Lab job runner."""
    return os.path.expanduser(os.environ.get("TWINSMITH_JOBS_DIR") or "~/.local/share/twinsmith/jobs")


def _process_start(pid: int) -> Optional[str]:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as stream:
            return stream.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


class JobCancelled(Exception):
    """Raised inside a job by ``Job.checkpoint`` after ``job_cancel``."""


class JobError(Exception):
    """A refusal from the store (unknown id, too many running); the message is for the caller."""


class Job:
    """What the running work sees: report progress, notice a cancel. What a caller sees: describe()."""

    def __init__(self, job_id: str, kind: str, label: Optional[str], cooperative: bool):
        self.id = job_id
        self.kind = kind
        self.label = label
        self.cooperative = cooperative
        self.state = "running"
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.progress: Dict[str, Any] = {}
        self.result: Any = None
        self.error: Optional[str] = None
        self.error_details: Dict[str, Any] = {}
        self.cancel_requested = False
        self.output: Optional[Callable[[], str]] = None   # live captured prints, when the job has any
        self.ended = threading.Event()                    # set when the job reaches a final state
        self.record: Optional[str] = None                 # its job.json, when records are kept
        self._recorded_at = 0.0
        self._port: Optional[int] = None
        self._task: Any = None

    def report(self, fraction: Optional[float] = None, text: Optional[str] = None, **fields: Any) -> None:
        """Record progress for job_status: a 0..1 fraction, a short text, and any small JSON fields."""
        progress = dict(self.progress)
        if fraction is not None:
            fraction = max(0.0, min(1.0, float(fraction)))
            progress.update(fraction=round(fraction, 4), percent=round(fraction * 100, 1))
        if text is not None:
            progress["text"] = str(text)[:200]
        progress.update(fields)
        progress["updated_s"] = round(time.time() - self.started_at, 3)
        self.progress = progress
        if time.monotonic() - self._recorded_at >= RECORD_INTERVAL_S:
            self.write_record()

    def write_record(self, port: Optional[int] = None) -> None:
        """Publish the job's state as one whole file, replacing the last. Never raises."""
        if self.record is None:
            return
        self._recorded_at = time.monotonic()
        if port is not None:
            self._port = port
        data = {"id": self.id, "job_id": self.id, "kind": self.kind, "task": self.label or self.kind, "label": self.label,
                "status": self.state, "runner": "kit", "uses_gpu": False, "pid": os.getpid(),
                "pid_start": _process_start(os.getpid()), "port": self._port,
                "started_at": self.started_at, "finished_at": self.finished_at, "exit_code": None,
                "progress": self.progress or None, "log_dir": os.path.dirname(self.record)}
        if self.state == "succeeded":
            data["result"] = _bounded(self.result)
        if self.error:
            data["error"] = self.error
            data["error_details"] = {k: v for k, v in self.error_details.items() if k != "traceback"}
        try:
            with tempfile.NamedTemporaryFile("w", dir=os.path.dirname(self.record), prefix=".job-", suffix=".tmp",
                                             delete=False, encoding="utf-8") as stream:
                json.dump(data, stream, default=repr)
            os.replace(stream.name, self.record)
        except (OSError, TypeError, ValueError):     # a full disk, or a result JSON cannot hold
            try:
                os.unlink(stream.name)
            except (OSError, NameError):
                pass

    def checkpoint(self) -> None:
        """Call between steps: raises JobCancelled once job_cancel was called."""
        if self.cancel_requested:
            raise JobCancelled()

    def describe(self, full: bool = True) -> Dict[str, Any]:
        end = self.finished_at if self.finished_at is not None else time.time()
        out: Dict[str, Any] = {"job_id": self.id, "kind": self.kind, "state": self.state,
                               "elapsed_s": round(end - self.started_at, 3)}
        if self.label:
            out["label"] = self.label
        if self.progress:
            out["progress"] = self.progress
        if self.cancel_requested and self.state == "running":
            out["cancel_requested"] = True
        if not full:
            return out
        if self.output is not None:
            try:
                text = self.output()
            except Exception:  # noqa: BLE001 - output is a convenience, never a reason to fail status
                text = ""
            if text:
                out["output_tail"] = text[-OUTPUT_TAIL:]
        if self.state == "succeeded":
            out["result"] = _bounded(self.result)
        if self.error:
            out["error"] = self.error
            out.update({k: v for k, v in self.error_details.items() if k not in out})
        return out


def _bounded(result: Any) -> Any:
    try:
        encoded = json.dumps(result, default=repr)
    except (TypeError, ValueError):
        encoded = json.dumps(repr(result))
    if len(encoded.encode()) <= RESULT_LIMIT:
        return json.loads(encoded)
    return {"truncated": True, "bytes": len(encoded.encode()),
            "note": f"The result exceeds {RESULT_LIMIT} bytes; write large data to a file and return its path.",
            "head": encoded[:2000]}


class JobStore:
    """The jobs of one bridge. ``schedule`` hops a coroutine onto the main loop and returns its task."""

    def __init__(self, schedule: Callable[[Awaitable], Any], record_dir: Optional[str] = None,
                 port: Optional[Callable[[], Optional[int]]] = None):
        self._schedule = schedule
        self._jobs: Dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._record_dir = record_dir
        self._port = port or (lambda: None)

    def _new_id(self, kind: str) -> str:
        """``<date>-<time>-<kind>-<n>``: the same shape as a process job's id, so one tool takes both."""
        slug = re.sub(r"[^A-Za-z0-9_.-]", "-", kind).strip("-") or "job"
        return f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}-{next(self._ids)}"

    def start(self, kind: str, work: Callable[[Job], Awaitable[Any]], label: Optional[str] = None,
              cooperative: bool = True) -> Job:
        """Schedule ``work(job)`` and return the job. ``cooperative`` work polls ``job.checkpoint()``
        and cleans up after itself; other work is cancelled at its next ``await``."""
        if len(self.running()) >= MAX_RUNNING:
            raise JobError(f"{MAX_RUNNING} jobs are already running; wait for one (job_status) or stop one "
                           "(job_cancel) first: " + ", ".join(j.id for j in self.running()))
        if label is not None and (not isinstance(label, str) or len(label) > 80):
            raise JobError("label must be a string of at most 80 characters.")
        job = Job(self._new_id(kind), kind, label or None, cooperative)
        self._jobs[job.id] = job
        self._trim()
        if self._record_dir:
            try:
                folder = os.path.join(self._record_dir, job.id)
                os.makedirs(folder, exist_ok=True)
                job.record = os.path.join(folder, "job.json")
                job.write_record(self._port())
            except OSError:
                job.record = None
        try:
            job._task = self._schedule(self._run(job, work))
        except BaseException:
            self._jobs.pop(job.id, None)
            raise
        watch = getattr(job._task, "add_done_callback", None)
        if callable(watch):
            watch(lambda task: self._task_ended(job, task))
        return job

    @staticmethod
    def _task_ended(job: Job, task: Any) -> None:
        """The task is over but the job never reached a final state: it was cancelled before its
        first step, or something outside the job destroyed its task. Say so instead of `running`."""
        if job.state != "running":
            return
        job.finished_at = time.time()
        try:
            cancelled = task.cancelled()
            error = None if cancelled else task.exception()
        except BaseException as exc:  # noqa: BLE001 - a foreign future type
            cancelled, error = False, exc
        if cancelled or job.cancel_requested:
            job.state = "cancelled"
        else:
            job.state = "failed"
            job.error = (f"{type(error).__name__}: {error}"[:1500] if error is not None
                         else "The job's task ended without finishing its work.")
        job.write_record()
        job.ended.set()

    async def _run(self, job: Job, work: Callable[[Job], Awaitable[Any]]) -> None:
        try:
            job.result = await work(job)
            job.state = "succeeded"
        except (JobCancelled, asyncio.CancelledError):
            job.state = "cancelled"
        except BaseException as exc:  # noqa: BLE001 - a job must always reach a final state
            job.state = "failed"
            job.error = f"{type(exc).__name__}: {exc}"[:1500]
            details = getattr(exc, "details", None)
            if isinstance(details, dict):
                job.error_details = dict(details)
            code = getattr(exc, "code", None)
            if isinstance(code, str):
                job.error_details.setdefault("code", code)
            job.error_details.setdefault("traceback", traceback.format_exc()[-3000:])
        finally:
            job.finished_at = time.time()
            if job.progress.get("fraction") is not None and job.state == "succeeded":
                job.report(1.0)
            job.write_record()
            job.ended.set()

    def _trim(self) -> None:
        finished = [j for j in self._jobs.values() if j.state != "running"]
        for job in sorted(finished, key=lambda j: j.finished_at or 0)[:max(0, len(finished) - MAX_KEPT)]:
            self._jobs.pop(job.id, None)

    def get(self, job_id: Any) -> Job:
        job = self._jobs.get(job_id) if isinstance(job_id, str) else None
        if job is None:
            known = ", ".join(j.id for j in self.jobs()[:8]) or "none"
            raise JobError(f"unknown job {job_id!r}. Jobs live in this Kit process and are forgotten when it "
                           f"restarts; known jobs: {known}.")
        return job

    def jobs(self) -> List[Job]:
        """Newest first."""
        return sorted(list(self._jobs.values()), key=lambda j: j.started_at, reverse=True)

    def running(self) -> List[Job]:
        # status asks from a socket thread while the main loop adds and forgets jobs: read a copy.
        return [j for j in list(self._jobs.values()) if j.state == "running"]

    def cancel(self, job_id: Any) -> Dict[str, Any]:
        job = self.get(job_id)
        if job.state != "running":
            return {**job.describe(full=False), "note": "The job had already ended; nothing was cancelled."}
        job.cancel_requested = True
        if not job.cooperative:
            cancel = getattr(job._task, "cancel", None)
            if callable(cancel):
                cancel()
        return {**job.describe(full=False),
                "note": "Cancel requested: the job stops at its next step. Confirm with job_status."}

    def cancel_all(self, kinds: Optional[tuple] = None) -> List[str]:
        """Request cancel of every running job (of these kinds); returns their ids."""
        stopped = []
        for job in self.running():
            if kinds is None or job.kind.startswith(kinds):
                self.cancel(job.id)
                stopped.append(job.id)
        return stopped

    def summary(self) -> Dict[str, Any]:
        running = self.running()
        return {"running": len(running), "jobs": [j.describe(full=False) for j in running]}

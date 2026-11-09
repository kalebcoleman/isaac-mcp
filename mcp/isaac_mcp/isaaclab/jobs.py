"""Isaac Lab job (subprocess) management.

This module owns the job model for long-running Isaac Lab train/eval/play jobs.
It is deliberately free of FastMCP imports; the parent server wires these
functions as MCP tools later. It is pure stdlib Python (>=3.10).

Scripts, libraries and accepted flags come from install.lab_profile, read from the checkout. Isaac Lab 2.3's
per-library layout uses scripts/reinforcement_learning/<library>/{train,play}.py, a required library chosen by path, and
--headless when headless without --video. Its scripts declare neither --viz nor --rl_library; left unrefused they would
reach Hydra after Kit starts. Real jobs passed on 2.3.2 with all four libraries, and train, resume, eval and play were measured through the tools.

Isaac Lab 3 entry scripts and flags (relative to the resolved ``isaaclab_root``):
  * train:  scripts/reinforcement_learning/train.py
      flags: --task, --num_envs, --max_iterations, --seed, --checkpoint,
             --device (launcher), --viz none|kit, plus validated extra_args
  * eval:   scripts/reinforcement_learning/play.py  (Isaac Lab has no separate
            eval entrypoint; play.py with --checkpoint is the eval path)
      flags: --task, --checkpoint, --num_envs, --rl_library, --viz none|kit (no --headless flag in Isaac Lab 3; picks the RL backend with --rl_library, else the task default; no per-backend --num_episodes)
  * play:   scripts/reinforcement_learning/play.py
      flags: --task, --checkpoint, --num_envs, --viz none|kit

Headless video needs a renderer, not ``--viz none``. It selects Kit with the
documented child-only ``HEADLESS=1`` setting; normal headless jobs use no visualizer.

Jobs are launched with ``shell=False`` and an explicit argv list, with
``start_new_session=True`` so the whole process group can be signalled.
"""

from __future__ import annotations

import itertools
import errno
import json
import math
import os
import re
import shlex
import signal
import subprocess
import threading
import tempfile
import time
from pathlib import Path
from typing import Any

from . import install as _install

_LAUNCHER = Path(__file__).with_name("launcher.py")
_EVAL_RUNNER = Path(__file__).with_name("eval_runner.py")
_TASK_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_JOB_ID_RE = re.compile(r"^\d{8}-\d{6}-[A-Za-z0-9_.-]+$")
_LIB_RE = re.compile(r"^[a-z0-9_]+$")
_SPLIT_VALUE_RE = re.compile(r"^(-?[0-9]|[A-Za-z0-9_.:/+])[A-Za-z0-9_.:/+-]*$")  # a bare value after a --flag; no "=" so it is not a Hydra override
_EXTRA_ARG_RE = re.compile(r"^--[A-Za-z0-9_.-]+(=[^\s;|&$`]*)?$")
# Isaac Lab 3 takes Hydra overrides (key.sub=value, +key=value, ~key) alongside --flags.
_HYDRA_ARG_RE = re.compile(r"^(~[A-Za-z0-9_.@/-]+|[+]{0,2}[A-Za-z0-9_.@/-]+=[^\s;|&$`'\"=]*)$")
_CHECKPOINT_EXTS = ("*.pth", "*.pt")

_jobs_lock = threading.Lock()
# Held from the guard check until the new job is registered, so two launches in this process cannot both pass the guard.
_launch_lock = threading.RLock()
_active: dict[str, subprocess.Popen] = {}


def _jobs_path() -> Path:
    base = os.environ.get("ISAACLAB_JOBS_DIR")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".local" / "share" / "isaac-mcp" / "jobs"


def jobs_dir() -> Path:
    p = _jobs_path()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> float:
    return time.time()


def _slug(value: str) -> str:
    out = []
    for ch in value:
        if ch.isalnum() or ch in "._-":
            out.append(ch)
        else:
            out.append("-")
    s = "".join(out).strip("-")
    return s or "job"


def _job_id(task: str) -> str:
    """Unique id: second-resolution timestamp + task slug, with -2, -3, ... if that directory already exists."""
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    base = f"{ts}-{_slug(task)}"
    jid, n = base, 2
    while (jobs_dir() / jid).exists():
        jid = f"{base}-{n}"
        n += 1
    return jid


def _validate_task(task: str) -> None:
    if not isinstance(task, str) or not _TASK_RE.match(task):
        raise ValueError(f"invalid task id: {task!r}")


PHYSICS_PRESETS = {"physx": "physics=isaacsim_physx", "newton": "physics=newton_mjwarp"}  # Lab 3's own "physx" preset picks the optional OvPhysX runtime


def _with_physics(physics, root, extras: list[str]) -> list[str]:
    """The engine a job should use, as Isaac Lab 3's Hydra override. Lab 2.x has PhysX only, so there it needs no argument."""
    if physics is None:
        return extras
    if physics not in PHYSICS_PRESETS:
        raise ValueError(f"physics must be one of {', '.join(PHYSICS_PRESETS)}.")
    if any(arg.lstrip("+~").split("=", 1)[0] == "physics" for arg in extras):
        raise ValueError("Give physics as this option or in extra_args, not both.")
    if _per_library(root) is not None:
        if physics == "newton":
            raise ValueError("This Isaac Lab has PhysX only: its scripts have no Newton engine.")
        return extras
    return [*extras, PHYSICS_PRESETS[physics]]


def _validate_extra_args(extra_args) -> list[str]:
    if extra_args is None:
        return []
    if not isinstance(extra_args, (list, tuple)):
        raise ValueError("extra_args must be a list of strings")
    out = []
    for a in extra_args:
        if isinstance(a, str) and out and _SPLIT_VALUE_RE.match(a) and "=" not in out[-1] and _EXTRA_ARG_RE.match(out[-1]):
            out[-1] = f"{out[-1]}={a}"  # --video_length 100 passed as two items means --video_length=100
            continue
        if not isinstance(a, str) or not (_EXTRA_ARG_RE.match(a) or (not a.startswith("-") and _HYDRA_ARG_RE.match(a))):
            raise ValueError(f"invalid extra_arg: {a!r}: use --flag, --flag=value, or a Hydra override such as key=value, +key=value or ~key")
        out.append(a)
    return list(out)


def _visualizer_args(headless: bool, extras: list[str]) -> list[str]:
    # Lab 3 rejects --video with --viz none. HEADLESS in the child environment
    # keeps the selected Kit renderer windowless, including for configured tasks.
    explicit = [arg for arg in extras if arg.split('=', 1)[0] in ('--viz', '--visualizer')]
    if len(explicit) > 1:
        raise ValueError('supply only one explicit --viz= or --visualizer= override')
    if explicit:
        if '=' not in explicit[0] or not explicit[0].split('=', 1)[1]:
            raise ValueError('extra_args visualizer override must use --viz=VALUE or --visualizer=VALUE')
        if explicit[0].split('=', 1)[1] == 'none' and '--video' in extras:
            raise ValueError('--video requires a capture-capable visualizer; remove the explicit --viz=none override')
        # Preserve the existing explicit override, once, without a conflicting
        # generated argument. Other backends retain their own Lab configuration.
        return []
    return ["--viz", "none" if headless and "--video" not in extras else "kit"]


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _validate_checkpoint(checkpoint: Any, root: Any) -> str | None:
    if checkpoint is None:
        return None
    if not isinstance(checkpoint, str) or not checkpoint:
        raise ValueError("checkpoint must be a non-empty path string")
    p = Path(checkpoint).expanduser()
    if not p.exists():
        raise ValueError(f"checkpoint does not exist: {checkpoint}; find one with isaaclab__checkpoints_list")
    rp = p.resolve()
    allowed = [Path(root).resolve(), jobs_dir().resolve()]
    if not any(_under(rp, r) for r in allowed):
        raise ValueError(f"checkpoint resolves outside allowed roots: {checkpoint}")
    return str(rp)


def _child_env(root: Any, device: str | None) -> dict[str, str]:
    env = os.environ.copy()
    env["OMNI_KIT_ACCEPT_EULA"] = "YES"
    env["PYTHONUNBUFFERED"] = "1"
    if device and isinstance(device, str) and device.lower() != "cpu":
        dev = device
        if ":" in dev:
            dev = dev.split(":")[-1]
        env["CUDA_VISIBLE_DEVICES"] = dev
    # ensure root importable
    rp = str(Path(root).resolve())
    pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = rp + (os.pathsep + pp if pp else "")
    return env


def _launch_kw(python_path: Any, env: Any, external_callback: Any) -> dict[str, Any]:
    """The task-registration options a caller gave, for forwarding; empty when none, so older callers see no change."""
    given = {"python_path": python_path, "env": env, "external_callback": external_callback}
    return {k: v for k, v in given.items() if v is not None}


def _read_job(job_dir: Path) -> dict[str, Any] | None:
    jf = job_dir / "job.json"
    if not jf.exists():
        return None
    try:
        return json.loads(jf.read_text())
    except Exception:
        return None


def _eval_result(job_dir: Path) -> dict[str, Any] | None:
    """The score file an eval job's runner wrote, if it is there and readable."""
    try:
        data = json.loads((job_dir / "eval_result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _mtime(path: Any) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def _check_result(job_dir: Path) -> dict[str, Any] | None:
    """The result file an env_check or record job's runner wrote, if it is there and readable."""
    try:
        data = json.loads((job_dir / "check_result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_job(job_dir: Path, data: dict[str, Any]) -> None:
    # Status/cancel readers do not all hold the watcher lock. Publish one whole
    # JSON snapshot so they see the previous or next record, never truncation.
    payload = json.dumps(data, indent=2, default=str)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=job_dir,
            prefix=".job-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, job_dir / "job.json")
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _update_job(job_dir: Path, **fields: Any) -> dict[str, Any]:
    data = _read_job(job_dir) or {}
    data.update(fields)
    _write_job(job_dir, data)
    return data


def _job_path(job_id: Any) -> Path:
    """Directory of an existing job; rejects ids that could escape the jobs dir."""
    if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
        raise ValueError(f"invalid job id: {job_id!r}; it looks like 20261104-092041-Isaac-Cartpole, and isaaclab__runs_list has them")
    return jobs_dir() / job_id


# What Isaac Lab 3.0.0 accepts for --rl_library; its logs are written under logs/<library>/.
_RL_LIBRARIES = ("rsl_rl", "rl_games", "skrl", "sb3", "rlinf", "torchrl")
# The importable module behind each library name, as install_info's rl_libraries check uses.
RL_LIBRARY_MODULES = {"rsl_rl": "rsl_rl", "rl_games": "rl_games", "skrl": "skrl", "sb3": "stable_baselines3"}
RUN_STATES = ("running", "finished", "all")

# Probes run the Lab interpreter once (a few seconds at most) and are remembered for a while: a
# package can be installed between launches, but not on every call. A failed probe is remembered
# too, and every caller treats None as "cannot tell" and lets the job start as before.
_PROBE_TTL_S = 300.0
_PROBE_TIMEOUT_S = 20.0
_probe_cache: dict[tuple[str, str], tuple[float, Any]] = {}


def _probe_json(python: str, key: str, code: str, timeout: float = _PROBE_TIMEOUT_S) -> Any:
    """JSON from a short program in *python*, or None when it cannot be determined."""
    stamp = time.monotonic()
    cache_key = (str(python), key)
    if cache_key in _probe_cache and stamp - _probe_cache[cache_key][0] < _PROBE_TTL_S:
        return _probe_cache[cache_key][1]
    env = os.environ.copy()
    env["OMNI_KIT_ACCEPT_EULA"] = "YES"
    try:
        done = subprocess.run(
            [str(python), "-c", code], stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        lines = [line for line in (done.stdout or "").splitlines() if line.strip()]
        value = json.loads(lines[-1]) if done.returncode == 0 and lines else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        value = None
    _probe_cache[cache_key] = (stamp, value)
    return value


def installed_rl_libraries(python: str) -> list[str] | None:
    """The RL libraries importable in *python*, or None. The same check isaaclab__install_info reports."""
    code = (
        "import importlib.util as u, json\n"
        f"_libs = {RL_LIBRARY_MODULES!r}\n"
        "print(json.dumps(sorted(k for k, m in _libs.items() if u.find_spec(m))))\n"
    )
    value = _probe_json(python, "rl_libraries", code)
    return value if isinstance(value, list) else None


def _require_library_installed(python: str, library: str | None) -> None:
    """Refuse a library that is not installed, before Kit starts (its import used to fail much later)."""
    if not library:
        return
    installed = installed_rl_libraries(python)
    if installed is None:
        return  # cannot tell here: do not block a launch that may work
    if library not in installed:
        have = ", ".join(installed) or "none"
        raise ValueError(
            f"rl_library {library} is not installed in {python}: the installed libraries are {have}. "
            f"Install {library} in that environment (Isaac Lab's installation docs list the extra), or choose "
            f"one of the installed libraries."
        )


_VIDEO_MODULES = {"moviepy": "moviepy", "imageio-ffmpeg": "imageio_ffmpeg"}


def installed_video_support(python: str) -> dict | None:
    """Which of MoviePy and imageio-ffmpeg are importable in *python*, or None."""
    code = (
        "import importlib.util as u, json\n"
        f"_mods = {_VIDEO_MODULES!r}\n"
        "print(json.dumps({name: bool(u.find_spec(module)) for name, module in _mods.items()}))\n"
    )
    value = _probe_json(python, "video_support", code)
    return value if isinstance(value, dict) else None


def _require_video_support(python: str, extras: list[str]) -> None:
    """Refuse --video at once when MoviePy or imageio-ffmpeg is missing; it used to fail after launch."""
    if not any(arg.split("=", 1)[0] == "--video" for arg in extras):
        return
    found = installed_video_support(python)
    if found is None:
        return  # cannot tell here: do not block a launch that may work
    missing = [name for name, present in found.items() if not present]
    if missing:
        raise ValueError(
            "--video needs " + " and ".join(missing) + " in the Lab interpreter, which "
            f"{python} does not have. Install with: {python} -m pip install moviepy imageio-ffmpeg, "
            "or drop --video and use isaaclab__runs_status's window with a screen recording."
        )


def _require_known_task(root: Any, task: str, modules: list[str]) -> None:
    """Refuse an Isaac-*-vN id the checkout's own source does not register, before the ~50 s launch.

    The scan is only source text (isaaclab__envs_list uses it too). A configured task module can
    register any id, so nothing is refused when one is set; and an id whose family is not in the
    scan at all could still be registered another way, so it is left to Lab to decide.
    """
    if modules:
        return
    try:
        scanned = _install.scan_task_ids(root)
    except OSError:
        return
    if not scanned or task in scanned:
        return
    family = re.sub(r"-v\d+$", "", task)
    related = [known for known in scanned if re.sub(r"-v\d+$", "", known) == family]
    if not related:
        return
    import difflib

    matches = difflib.get_close_matches(task, scanned, n=5, cutoff=0.6) or related[:5]
    raise ValueError(
        f"{task} is not a task id this Isaac Lab checkout registers. Close matches: {', '.join(matches)}. "
        "isaaclab__envs_list lists the ids (Isaac Lab 3 often has no -v0). Nothing was started."
    )


def _per_library(root: Any) -> list[str] | None:
    """The library folders of a per-library checkout, else None (Isaac Lab 3, or no scripts: refused at launch)."""
    profile = _install.lab_profile(root)
    return profile["libraries"] if profile["layout"] == _install.PER_LIBRARY else None


def _per_library_choice(root: Any, libraries: list[str], lib: str | None) -> str:
    """The library to run, or a refusal naming the choices, before anything starts."""
    if lib not in libraries:
        given = f"has no {lib} scripts" if lib else "needs rl_library"
        raise ValueError(f"{_install.lab_label(root, _install.lab_profile(root))} {given}: it has one script folder per "
                         f"library. Choose one of: {', '.join(libraries)}.")
    return lib


def _per_library_display_args(root: Any, library: str, script: str, headless: bool, extras: list[str]) -> list[str]:
    # With unknown flags, the child's HEADLESS setting still chooses the display.
    flags = _install.lab_profile(root)["scripts"][library][script]["flags"] or []
    wanted = headless and "--video" not in extras and "--headless" not in extras and "--headless" in flags
    return ["--headless"] if wanted else []


_FLAG_ALTERNATIVES = {"--viz": "Use the headless argument.", "--visualizer": "Use the headless argument.",
                      "--rl_library": "Use the rl_library argument."}


# Kit uploads a crash's minidump to NVIDIA by default, and a minidump can contain process memory. run.sh turns that
# off for simulators; jobs do not go through it. ISAAC_MCP_CRASH_UPLOAD=1 leaves Kit's default.
_NO_CRASH_UPLOAD = "--/crashreporter/url= --/crashreporter/skipOldDumpUpload=true --/crashreporter/preserveDump=true"


def _crash_upload_args(root: Any, extras: list[str]) -> list[str]:
    """--kit_args keeping crash dumps local, when this Lab's launcher takes that flag and the caller did not pass it."""
    if os.environ.get("ISAAC_MCP_CRASH_UPLOAD", "0").lower() in ("1", "true", "yes"):
        return []
    if any(arg.split("=", 1)[0] == "--kit_args" for arg in extras):
        return []
    app = Path(root) / "source" / "isaaclab" / "isaaclab" / "app"
    for launcher in ("sim_launcher.py", "app_launcher.py"):
        try:
            if "--kit_args" in (app / launcher).read_text(errors="replace"):
                return ["--kit_args", _NO_CRASH_UPLOAD]
        except OSError:
            continue
    return []


def _require_accepted(root: Any, library: str | None, script: str, argv: list[str]) -> None:
    """Refuse a flag the script does not declare, which it might otherwise hand to Hydra after Kit starts."""
    profile = _install.lab_profile(root)
    flags = profile["scripts"].get(library or "", {}).get(script, {}).get("flags")
    if flags is None:
        return
    for arg in argv:
        flag = arg.split("=", 1)[0]
        if flag.startswith("--") and flag not in flags:
            alternative = _FLAG_ALTERNATIVES.get(flag, f"It accepts: {', '.join(flags)}.")
            raise ValueError(f"{_install.lab_label(root, profile)}: {library + '/' if library else ''}{script}.py does not "
                             f"accept {flag}. {alternative}")


def _library_from_checkpoint(checkpoint: str, extras: list[str], libraries: Any = _RL_LIBRARIES) -> str | None:
    """The library named by a checkpoint under logs/<library>/, unless the caller's arguments choose one.

    Without it Lab picks the task's default_agent, which fails on another library's checkpoint with an
    opaque KeyError inside the backend.
    """
    if any(arg == "--rl_library" or arg.startswith("--rl_library=") for arg in extras):
        return None
    parts = Path(checkpoint).parts
    for index, part in enumerate(parts[:-1]):
        if part == "logs" and parts[index + 1] in libraries:
            return parts[index + 1]
    return None


def _validate_library(rl_library: Any) -> str | None:
    if rl_library is None:
        return None
    if not isinstance(rl_library, str) or not _LIB_RE.match(rl_library):
        raise ValueError(f"invalid rl_library: {rl_library!r}")
    return rl_library


def _process_start(pid: Any) -> str | None:
    """A process's start time, from /proc. None where that can't be read."""
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="utf-8", errors="replace") as f:
            return f.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError, TypeError, ValueError):
        return None


def _job_alive(data: dict) -> bool:
    """True only if the recorded pid is alive AND still runs this job's script (guards pid reuse
    after a host restart: we must never signal an unrelated process group)."""
    pid = data.get("pid")
    if not _pid_alive(pid):
        return False
    started = data.get("pid_start")
    if started is not None and _process_start(pid) not in (started, None):
        return False
    cmd = data.get("command") or []
    marker = cmd[1] if len(cmd) > 1 else None
    if marker is None and started is None:
        return False  # nothing ties this pid to the job, and a yes here can signal a stranger
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            argv = f.read().split(b"\0")
    except OSError:
        return True  # no /proc (non-Linux): fall back to the plain liveness check
    return marker is None or marker.encode() in argv


def _orphaned(job_id: str, data: dict) -> bool:
    """A 'running' record whose process is gone and that no watcher in this process still owns.
    While the watcher owns a job it records the real exit code itself; marking it failed here first
    would report a finished job as failed ("host restarted") with exit_code None."""
    return data.get("status") == "running" and job_id not in _active and not _job_alive(data)


EXIT_FILE = "exit_code"


TIMEOUT_FILE = "timed_out"


def _exit_recording(command: list[str], job_dir: Path, timeout_s: float | None = None) -> list[str]:
    """Run *command* so its exit code reaches the job folder even if this host is gone by then. The shell stays the group
    leader and outlasts a signal, so while the recorded pid lives the job does: a signalled shell would look like a stopped
    job while Kit, which takes a while over SIGTERM, was still running. The trap doesn't pass to the command.

    With *timeout_s* the shell also enforces the limit, so a job outlives no limit when the host has gone away: TERM to its
    group at the limit, KILL 30 s later, and a marker file saying why it ended."""
    if not timeout_s:
        script = 'trap : TERM INT HUP; "$@"; code=$?; printf %s "$code" > "$0"; exit "$code"'
        return ["/bin/sh", "-c", script, str(job_dir / EXIT_FILE), *command]
    marker = shlex.quote(str(job_dir / TIMEOUT_FILE))
    limit = max(1, math.ceil(float(timeout_s)))
    wait = 'n=0; while [ "$n" -lt {} ]; do sleep 1; n=$((n+1)); done'  # one-second sleeps, so killing the guard leaves nothing long behind
    script = ('trap : TERM INT HUP; '
              f"( trap '' TERM INT HUP; {wait.format(limit)}; : > {marker}; kill -TERM 0; {wait.format(30)}; kill -KILL 0 ) </dev/null >/dev/null 2>&1 & guard=$!; "
              '"$@"; code=$?; kill -KILL "$guard" 2>/dev/null; printf %s "$code" > "$0"; exit "$code"')
    return ["/bin/sh", "-c", script, str(job_dir / EXIT_FILE), *command]


def _settle_orphan(job_dir: Path, data: dict) -> None:
    """Record how a job with no watcher ended. Its own exit code if it left one; otherwise it was killed, or predates the file."""
    try:
        code = int((job_dir / EXIT_FILE).read_text())
    except (OSError, ValueError):
        if (job_dir / TIMEOUT_FILE).exists():  # the limit fired, and the job ignored TERM until the guard killed it
            data.update(status="timeout", finished_at=data.get("finished_at") or _now())
            _write_job(job_dir, data)
            return
        data.update(status="failed", finished_at=data.get("finished_at") or _now(), note=data.get("note") or "host restarted")
        _write_job(job_dir, data)
        return
    crash = _crashed_with_exit_zero(job_dir) if code == 0 else None
    if (job_dir / TIMEOUT_FILE).exists():
        data.update(status="timeout", exit_code=code, finished_at=data.get("finished_at") or _now())
        _write_job(job_dir, data)
        return
    data.update(status="succeeded" if code == 0 and not crash else "failed", exit_code=code,
                finished_at=data.get("finished_at") or _now(), **({"note": crash} if crash else {}))
    _write_job(job_dir, data)


def _kill_orphan_group(data: dict) -> bool:
    """SIGKILL the job's group through a pidfd, so a pid reused after the check cannot redirect the signal."""
    from .. import sim_sessions

    pid = int(data["pid"])
    try:
        fd = os.pidfd_open(pid, 0)
    except (OSError, AttributeError):
        return False  # gone, or no pidfd support: never fall back to a bare killpg
    try:
        if not _job_alive(data) or os.getpgid(pid) != pid:
            return False
        try:
            signal.pidfd_send_signal(fd, signal.SIGKILL, None, sim_sessions._PIDFD_SIGNAL_PROCESS_GROUP)
        except OSError as exc:
            if exc.errno != errno.EINVAL:  # ESRCH and the rest: the leader is gone, and its group ID may be reused
                return False
            # A kernel before 6.9 cannot signal a group by pidfd: members first, then the leader
            sim_sessions._signal_group_members(pid, signal.SIGKILL)
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def _enforce_orphan_timeout(job_id: str, job_dir: Path, data: dict) -> None:
    """A watchdog dies with its host, so whoever looks next enforces timeout_s."""
    limit, started = data.get("timeout_s"), data.get("started_at")
    if data.get("status") != "running" or job_id in _active or not limit or not started or _now() - float(started) <= float(limit):
        return
    if not _job_alive(data):
        return  # already gone: _settle_orphan records how it really ended, including a clean finish
    if not _kill_orphan_group(data):
        return  # still running or already gone: say nothing false; the next look tries again or settles it
    data.update(status="timeout", finished_at=_now())
    _write_job(job_dir, data)


def _running_job() -> dict[str, Any] | None:
    """Another job still running. A job a watcher here owns is running until its process has exited
    (its record is finalized a moment later); any other 'running' record counts only if its pid is
    alive. A stale record (dead pid, no watcher) is not running and must not block a new launch."""
    for jd in sorted(jobs_dir().iterdir()):
        if not jd.is_dir():
            continue
        data = _read_job(jd)
        if not data or data.get("status") != "running":
            continue
        proc = _active.get(jd.name)
        if proc is not None:
            if proc.poll() is None:
                return data
        elif _job_alive(data):
            return data
    return None


def _concurrency_guard() -> dict[str, Any] | None:
    """Refuse to start a second job: one 16 GB GPU, so two jobs contend and OOM."""
    other = _running_job()
    if other is None:
        return None
    jid = other.get("job_id") or other.get("id")
    pid = other.get("pid")
    return {
        "error": f"another Isaac Lab job is already running: {jid} (pid {pid})",
        "running_job_id": jid,
        "running_pid": pid,
        "hint": (
            "wait for it to finish, cancel it with isaaclab__runs_cancel, "
            "or pass allow_concurrent=true to start anyway"
        ),
    }


_DEFAULT_KIT_PORT = 8766
_KIT_PROBE_TIMEOUT_S = 0.3


def _probe_kit_bridges() -> list[dict[str, Any]]:
    """Kit bridges (a running Isaac Sim app) reachable now: advertised portfiles plus the configured port.

    The configured port is $UR5E_DT_PORT, else $ISAAC_MCP_PORT, else 8766; "0" or an unparsable value
    means "no fixed port" and only advertised bridges count. Each hit is {"host", "port"}.
    """
    import socket

    from isaac_mcp import client

    def key(h: str, p: int) -> tuple[str, int]:
        return ("127.0.0.1" if h in ("localhost", "::1") else h, p)

    found: dict[tuple[str, int], dict[str, Any]] = {}
    for info in client.discover_bridges():
        host = info.get("host") or client.DEFAULT_HOST
        found.setdefault(key(host, int(info["port"])), {"host": host, "port": int(info["port"])})
    raw = os.getenv(client.TWIN_PORT_ENV) or os.getenv(client.PORT_ENV)
    try:
        port = int(raw) if raw else _DEFAULT_KIT_PORT
    except ValueError:
        port = 0
    host = os.getenv(client.HOST_ENV) or client.DEFAULT_HOST
    if port > 0 and key(host, port) not in found:
        try:
            socket.create_connection((host, port), timeout=_KIT_PROBE_TIMEOUT_S).close()
            found[key(host, port)] = {"host": host, "port": port}
        except OSError:
            pass
    return list(found.values())


def _kit_bridge_guard() -> dict[str, Any] | None:
    """Refuse to start a job next to a running Kit app: on one 16 GB GPU the two contend and OOM."""
    bridges = _probe_kit_bridges()
    if not bridges:
        return None
    where = ", ".join(f"{b['host']}:{b['port']}" for b in bridges)
    return {
        "error": f"a Kit bridge (Isaac Sim app) is running at {where}; it shares the GPU with this job",
        "kit_bridges": bridges,
        "hint": (
            "stop the Isaac Sim app first, or pass allow_concurrent_gpu=true to start anyway"
        ),
    }


def _pid_alive(pid: int | None) -> bool:
    # A record could name 0, 1 or a negative number, which mean a process group or everything to kill().
    if type(pid) is not int or pid <= 1:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError, ValueError):
        return False


def _latest_checkpoint(run_dir: Any) -> str | None:
    if not run_dir:
        return None
    rd = Path(run_dir)
    if not rd.exists():
        return None
    candidates = []
    for pat in _CHECKPOINT_EXTS:
        # exported/ holds TorchScript and ONNX for deployment: the newest file, but play and resume can't load it.
        candidates.extend(p for p in rd.rglob(pat) if "exported" not in p.relative_to(rd).parts)
    if "sb3" in rd.parts:  # stable-baselines3 saves model.zip, not a torch file
        candidates.extend(rd.rglob("*.zip"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0].resolve())


_SAVED_CKPT_RE = re.compile(r"saving checkpoint '([^']+)'")
_EXPERIMENT_DIR_RE = re.compile(r"^Exact experiment name requested from command line:[ \t]*([^\r\n]+)$", re.MULTILINE)
_EXPERIMENT_ROOT_RE = re.compile(r"^(?:\[INFO\]:?[ \t]*)?Logging experiment in directory:[ \t]*(/[^\r\n]+)$", re.MULTILINE)
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _run_dir_from_log(log_path: Path) -> str | None:
    """Read the bounded log header, including Lab 3's separate root/name lines."""
    try:
        with log_path.open("rb") as f:
            head = f.read(131072).decode("utf-8", errors="replace")
    except OSError:
        return None
    head = _ANSI_RE.sub("", head)
    m = _EXPERIMENT_DIR_RE.search(head)
    if not m:
        return None
    name = m.group(1).strip()
    if not name or any(ord(c) < 32 for c in name):
        return None
    if Path(name).is_absolute():
        return name  # Earlier libraries print the complete path on this line.
    # A basename belongs to the most recently announced experiment root. Never
    # treat arbitrary relative paths or traversal from stdout as a directory.
    if name in {".", ".."} or "/" in name or "\\" in name:
        return None
    roots = list(_EXPERIMENT_ROOT_RE.finditer(head[:m.start()]))
    if not roots:
        return None
    root = roots[-1].group(1).strip()
    if any(ord(c) < 32 for c in root):
        return None
    return str(Path(root) / name)


_PHYSICS_RE = re.compile(r"Physics {2,}([A-Za-z][A-Za-z0-9_]*)")
_ENVIRONMENT_READY = "Completed setting up the environment"


def _log_facts(log_path: Path) -> dict[str, Any]:
    """What the start of a Lab log says about the run: its physics engine, and whether the environment is built.

    Lab prints a banner line such as ``Physics       newton_mjwarp``. A task picks its own engine, and Newton is not PhysX,
    so a caller should see which one a job used without reading the log. The setup line is near the start of the log
    but later output can push it out of ``last_log_lines``, so it is read from the head. Bounded, and absent when unknown.
    """
    try:
        with log_path.open("rb") as f:
            head = f.read(1 << 20).decode("utf-8", errors="replace")
    except OSError:
        return {}
    head = _ANSI_RE.sub("", head)
    facts: dict[str, Any] = {"environment_ready": _ENVIRONMENT_READY in head}
    found = _PHYSICS_RE.search(head)
    if found:
        facts["physics"] = found.group(1)
    return facts


def _checkpoint_from_log(log_path: Path) -> str | None:
    """Last checkpoint the training library announced in the log (e.g. rl_games prints
    "=> saving checkpoint '<path>'"). Isaac Lab writes runs under its own logs/ dir, not the job dir."""
    try:
        size = log_path.stat().st_size
        with log_path.open("rb") as f:
            f.seek(max(0, size - 262144))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    found = _SAVED_CKPT_RE.findall(text)
    for cand in reversed(found):
        if Path(cand).exists():
            return str(Path(cand).resolve())
    return None


_BAR_GLYPHS = ("|", "\u2501", "\u2588", "\u258f", "\u258c", "\u2590")


def _progress_bar_only(text: str) -> bool:
    """A line that is only a progress bar (tqdm, skrl, stable-baselines3 and rich render these)."""
    line = text.strip()
    if not line.endswith("]") or not any(glyph in line for glyph in _BAR_GLYPHS):
        return False
    return bool(re.search(r"(\d+%|\d+(?:\.\d+)?/\d+(?:\.\d+)?)\s*\[[^\]]*\]\s*$", line))


def _tail_file(path: Path, lines: int) -> list[str]:
    """The last *lines* useful lines of a log.

    A log written with carriage returns (progress bars) keeps only the newest text of each line, and
    a line that is only a bar is dropped: otherwise skrl and sb3 jobs show nothing but bars.
    """
    if not path.exists():
        return []
    try:
        # Read bytes and split on newlines only: a text-mode reader also splits a progress bar's
        # carriage returns into separate lines, and then its newest text cannot be kept.
        data = path.read_bytes()
    except Exception:
        return []
    all_lines = data.decode("utf-8", errors="replace").split("\n")
    if all_lines and all_lines[-1] == "":
        all_lines.pop()  # the newline that ends the file starts no line
    out = []
    for raw in reversed(all_lines):
        text = raw.rsplit("\r", 1)[-1].rstrip()
        if _progress_bar_only(text):
            continue
        out.append(text)
        if len(out) >= lines:
            break
    return list(reversed(out))


def _rehydrate() -> None:
    path = _jobs_path()
    if not path.is_dir():
        return
    for jd in path.iterdir():
        if not jd.is_dir():
            continue
        data = _read_job(jd)
        if not data:
            continue
        if data.get("status") == "running":
            if not _pid_alive(data.get("pid")):
                _settle_orphan(jd, data)



_KIT_LOG_LINE_RE = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z \[[\d,.]+m?s\] \[")
_TRACEBACK_HEADER = "Traceback (most recent call last):"


def _uncaught_exception(stderr_path: Path) -> str | None:
    """The exception a stderr ends in, ignoring Kit's shutdown log lines; None if it ends any other way.

    Kit's shutdown can turn an uncaught exception's exit status into 0: seen on Isaac Lab 2.3.2 with an unknown task,
    which raises before Hydra's own error handling. Only the end of the stream counts, so a traceback some extension
    printed and recovered from earlier, followed by other output, does not.
    """
    try:
        with stderr_path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    lines = [line for line in lines if line.strip() and not _KIT_LOG_LINE_RE.match(line)]
    headers = [i for i, line in enumerate(lines) if line.strip() == _TRACEBACK_HEADER]
    if not headers:
        return None
    after = lines[headers[-1] + 1:]
    ends = [i for i, line in enumerate(after) if not line.startswith((" ", "\t"))]
    # The first unindented line starts the exception message. Much more after it is other output, not this exception.
    if not ends or len(after) - ends[0] > 8:
        return None
    return after[ends[0]][:300]


def _crashed_with_exit_zero(job_dir: Path) -> str | None:
    """Why a job that exited 0 did not really succeed, or None. A checkpoint from training counts as completed work."""
    data = _read_job(job_dir) or {}
    log_dir = Path(data.get("log_dir", job_dir))
    exception = _uncaught_exception(log_dir / "stderr.log")
    if not exception:
        return None
    if data.get("kind") == "eval":
        score = _eval_result(log_dir)
        if score and not score.get("error") and score.get("stopped_by") in ("episodes", "max_steps"):
            return None  # the runner ended the play loop on purpose and wrote its result: a trace in stderr isn't a crash
    if data.get("kind") in ("env_check", "record"):
        result = _check_result(log_dir)
        if result and result.get("steps", 0) >= 1 and not result.get("error"):
            return None  # the runner ended the agent loop on purpose and wrote its result
    if data.get("kind") == "train":
        run_dir = _run_dir_from_log(log_dir / "stdout.log")
        if run_dir and _latest_checkpoint(run_dir):
            return None
    return f"exited 0, but stderr ends in an uncaught exception: {exception}"


def _watcher(job_dir: Path, job_id: str, proc: subprocess.Popen, timeout_s: float | None) -> None:
    status = None
    exit_code = None
    try:
        if timeout_s:
            proc.wait(timeout=timeout_s)
        else:
            proc.wait()
        exit_code = proc.returncode
        status = "succeeded" if exit_code == 0 else "failed"
        if (job_dir / TIMEOUT_FILE).exists():  # the shell guard enforced the limit first
            status = "timeout"
    except subprocess.TimeoutExpired:
        # kill process group
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        exit_code = proc.returncode
        status = "timeout"
    except Exception:
        try:
            proc.wait()
        except Exception:
            pass
        exit_code = getattr(proc, "returncode", None)
        status = "failed"
    with _jobs_lock:
        cur = _read_job(job_dir) or {}
        if cur.get("status") == "cancelled":
            # runs_cancel already recorded the outcome; do not turn it into "failed (-15)"
            _update_job(job_dir, exit_code=exit_code)
        elif status == "succeeded" and (crash := _crashed_with_exit_zero(job_dir)):
            _update_job(job_dir, status="failed", finished_at=_now(), exit_code=exit_code, note=crash)
        else:
            _update_job(job_dir, status=status, finished_at=_now(), exit_code=exit_code)
        _active.pop(job_id, None)


def _start_job(*args: Any, **kwargs: Any) -> dict[str, Any]:
    with _launch_lock:
        return _start_job_locked(*args, **kwargs)


def _start_job_locked(
    python: str,
    root: Any,
    kind: str,
    task: str,
    argv: list[str],
    timeout_s: float | None = None,
    device: str | None = None,
    extra_validated: list[str] | None = None,
    allow_concurrent: bool = False,
    allow_concurrent_gpu: bool = False,
    headless: bool | None = None,
    library: str | None = None,
    rl_library_check: str | None = None,
    eval_options: dict[str, Any] | None = None,
    check_options: dict[str, Any] | None = None,
    python_path: Any = None,
    env: Any = None,
    external_callback: Any = None,
) -> dict[str, Any]:
    _validate_task(task)
    opts = _install.validate_launch_options(python_path, env, external_callback)
    if opts["external_callback"] and any(a.split("=", 1)[0] == "--external_callback" for a in argv):
        raise ValueError("Give external_callback as this option or in extra_args, not both.")
    if not allow_concurrent:
        blocked = _concurrency_guard()
        if blocked is not None:
            return blocked
    if not allow_concurrent_gpu:
        blocked = _kit_bridge_guard()
        if blocked is not None:
            return blocked
    if extra_validated is None:
        extra_validated = []
    # Cheap interpreter probes, so a missing library or video encoder refuses now rather than
    # after Kit has started. Both return without an answer when they cannot tell.
    _require_library_installed(python, rl_library_check)
    _require_video_support(python, extra_validated)
    root_path = Path(root).resolve()
    # script selection
    if check_options is not None:
        script_path = root_path / "scripts" / "environments" / f"{check_options['agent']}_agent.py"
    else:
        script_rel = "train.py" if kind == "train" else "play.py"
        script_path = root_path / "scripts" / "reinforcement_learning" / (library or "") / script_rel
    if not script_path.is_file():
        raise ValueError(f"{_install.lab_label(root_path, _install.lab_profile(root_path))} has no "
                         f"{script_path.relative_to(root_path)}, so no job could start. "
                         f"Expected {_install.EXPECTED_SCRIPTS}.")
    task_modules, task_paths = _install.resolve_task_modules(root_path, {**os.environ, **opts["env"]})
    external = bool(opts["python_path"] or opts["external_callback"])
    _require_known_task(root_path, task, task_modules or (["<external>"] if external else []))
    if opts["external_callback"]:
        argv = list(argv) + [f"--external_callback={opts['external_callback']}"]
    job_id = _job_id(task)
    jd = jobs_dir() / job_id
    jd.mkdir(parents=True, exist_ok=True)
    if check_options is not None:
        from .check_runner import build_command as _check_command

        video_dir = str((jd / "videos").resolve()) if check_options["mode"] == "record" else None
        full_cmd = _check_command(
            python, str(script_path), list(argv), str((jd / "check_result.json").resolve()),
            mode=check_options["mode"], steps=check_options["steps"], video_dir=video_dir,
            video_length=check_options.get("video_length", 200),
        )
    elif eval_options is not None:
        # The scoring runner replaces play.py as the entry point: it wraps gymnasium.make, then runs
        # the real script unchanged. It imports ISAACLAB_TASK_MODULES itself (see its module docstring).
        from .eval_runner import build_command as _eval_command

        full_cmd = _eval_command(
            python, str(script_path), list(argv), str((jd / "eval_result.json").resolve()),
            num_episodes=eval_options["num_episodes"], max_steps=eval_options["max_steps"],
        )
    elif task_modules:
        # Isaac Lab's stock scripts do not know project tasks: import them first (see launcher.py).
        full_cmd = [str(python), str(_LAUNCHER), str(script_path)] + list(argv)
    else:
        full_cmd = [str(python), str(script_path)] + list(argv)
    stdout_path = jd / "stdout.log"
    stderr_path = jd / "stderr.log"
    run_dir = jd  # default; training artifacts may live under root/logs but we track job dir

    stdout_f = open(stdout_path, "wb")
    stderr_f = open(stderr_path, "wb")
    env = _child_env(root_path, device)
    env.update(opts["env"])
    if headless is not None:
        # A tool's explicit display choice wins over an inherited shell setting.
        # Do not mutate os.environ or another job's launch configuration.
        env["HEADLESS"] = "1" if headless else "0"
    if task_modules:
        env["ISAACLAB_TASK_MODULES"] = ",".join(task_modules)
        env["PYTHONPATH"] = os.pathsep.join(task_paths + [env["PYTHONPATH"]])
    if opts["python_path"]:
        env["PYTHONPATH"] = os.pathsep.join(opts["python_path"] + [env["PYTHONPATH"]])
    proc = subprocess.Popen(
        _exit_recording(full_cmd, jd, timeout_s),
        stdout=stdout_f,
        stderr=stderr_f,
        stdin=subprocess.DEVNULL,
        env=env,
        cwd=str(root_path),
        shell=False,
        start_new_session=True,
    )
    # close file handles in parent; child inherited
    stdout_f.close()
    stderr_f.close()

    started = _now()
    data = {
        "id": job_id,
        "job_id": job_id,
        "kind": kind,
        "task": task,
        "command": full_cmd,
        "pid": proc.pid,
        "pid_start": _process_start(proc.pid),
        "timeout_s": timeout_s,
        "started_at": started,
        "finished_at": None,
        "status": "running",
        "exit_code": None,
        "log_dir": str(jd.resolve()),
        "run_dir": str(run_dir.resolve()),
        "stdout_log": str(stdout_path.resolve()),
        "stderr_log": str(stderr_path.resolve()),
    }
    if eval_options is not None:
        data["eval"] = dict(eval_options)
    if check_options is not None:
        data["check"] = dict(check_options)
    if external or opts["env"]:
        data["launch"] = {"python_path": opts["python_path"], "external_callback": opts["external_callback"],
                          "env_names": sorted(opts["env"])}  # names only: values may be secrets
    _write_job(jd, data)

    with _jobs_lock:
        _active[job_id] = proc

    t = threading.Thread(target=_watcher, args=(jd, job_id, proc, timeout_s), daemon=True)
    t.start()

    return {"job_id": job_id, "log_dir": str(jd.resolve()), "command": full_cmd}


def train(
    python: str,
    root: Any,
    task: str,
    num_envs: int = 64,
    max_iterations: int | None = None,
    seed: int | None = None,
    device: str | None = None,
    headless: bool = True,
    checkpoint: Any = None,
    physics: str | None = None,
    extra_args: Any = None,
    timeout_s: float | None = None,
    rl_library: str | None = None,
    allow_concurrent: bool = False,
    allow_concurrent_gpu: bool = False,
    python_path: Any = None,
    env: Any = None,
    external_callback: Any = None,
) -> dict[str, Any]:
    _validate_task(task)
    lib = _validate_library(rl_library)
    ckpt = _validate_checkpoint(checkpoint, root)
    extras = _with_physics(physics, root, _validate_extra_args(extra_args))
    libraries = _per_library(root)
    if libraries is not None:
        lib = _per_library_choice(root, libraries, lib)
    argv: list[str] = ["--task", task, "--num_envs", str(num_envs)]
    if lib and libraries is None:
        argv += ["--rl_library", lib]
    if max_iterations is not None:
        argv += ["--max_iterations", str(max_iterations)]
    if seed is not None:
        argv += ["--seed", str(seed)]
    if ckpt is not None:
        if libraries is not None and (lib or _library_from_checkpoint(ckpt, extras)) == "rsl_rl":
            # Lab 2.x rsl_rl: --checkpoint is a file name matched inside --load_run, and nothing loads without --resume.
            argv += ["--resume", "--load_run", Path(ckpt).parent.name, "--checkpoint", Path(ckpt).name]
        else:
            argv += ["--checkpoint", ckpt]
    if device is not None:
        argv += ["--device", str(device)]
    argv += _visualizer_args(headless, extras) if libraries is None else _per_library_display_args(root, lib, "train", headless, extras)
    argv += extras
    _require_accepted(root, lib if libraries is not None else None, "train", argv)
    argv += _crash_upload_args(root, extras)  # after the check: its value looks like a flag
    return _start_job(
        python, root, "train", task, argv, timeout_s=timeout_s, device=device,
        allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
        headless=headless, rl_library_check=lib, extra_validated=extras,
        **_launch_kw(python_path, env, external_callback),
        **({} if libraries is None else {"library": lib}),
    )


def eval_(
    python: str,
    root: Any,
    task: str,
    checkpoint: Any,
    num_envs: int | None = None,
    headless: bool = True,
    physics: str | None = None,
    extra_args: Any = None,
    timeout_s: float | None = None,
    rl_library: str | None = None,
    num_episodes: int = 20,
    max_steps: int | None = 20000,
    allow_concurrent: bool = False,
    allow_concurrent_gpu: bool = False,
    python_path: Any = None,
    env: Any = None,
    external_callback: Any = None,
) -> dict[str, Any]:
    _validate_task(task)
    lib = _validate_library(rl_library)
    ckpt = _validate_checkpoint(checkpoint, root)
    if ckpt is None:
        raise ValueError("checkpoint is required for eval")
    num_episodes = _positive_int(num_episodes, "num_episodes")
    if max_steps is not None:
        max_steps = _positive_int(max_steps, "max_steps")
    extras = _with_physics(physics, root, _validate_extra_args(extra_args))
    libraries = _per_library(root)
    inferred = None if lib else _library_from_checkpoint(ckpt, extras, libraries or _RL_LIBRARIES)
    lib = lib or inferred
    if libraries is not None:
        lib = _per_library_choice(root, libraries, lib)
    argv: list[str] = ["--task", task, "--checkpoint", ckpt]
    if lib and libraries is None:
        argv += ["--rl_library", lib]
    if num_envs is not None:
        argv += ["--num_envs", str(num_envs)]
    argv += _visualizer_args(headless, extras) if libraries is None else _per_library_display_args(root, lib, "play", headless, extras)
    argv += extras
    _require_accepted(root, lib if libraries is not None else None, "play", argv)
    argv += _crash_upload_args(root, extras)  # after the check: its value looks like a flag
    result = _start_job(python, root, "eval", task, argv, timeout_s=timeout_s,
                        allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
                        headless=headless, rl_library_check=lib, extra_validated=extras,
                        eval_options={"num_episodes": num_episodes, "max_steps": max_steps},
                        **_launch_kw(python_path, env, external_callback),
                        **({} if libraries is None else {"library": lib}))
    return {**result, "rl_library_from_checkpoint_path": inferred} if inferred else result


def play(
    python: str,
    root: Any,
    task: str,
    checkpoint: Any,
    num_envs: int = 1,
    headless: bool = False,
    physics: str | None = None,
    extra_args: Any = None,
    timeout_s: float | None = None,
    rl_library: str | None = None,
    allow_concurrent: bool = False,
    allow_concurrent_gpu: bool = False,
    python_path: Any = None,
    env: Any = None,
    external_callback: Any = None,
) -> dict[str, Any]:
    _validate_task(task)
    lib = _validate_library(rl_library)
    ckpt = _validate_checkpoint(checkpoint, root)
    if ckpt is None:
        raise ValueError("checkpoint is required for play")
    extras = _with_physics(physics, root, _validate_extra_args(extra_args))
    libraries = _per_library(root)
    inferred = None if lib else _library_from_checkpoint(ckpt, extras, libraries or _RL_LIBRARIES)
    lib = lib or inferred
    if libraries is not None:
        lib = _per_library_choice(root, libraries, lib)
    argv: list[str] = ["--task", task, "--checkpoint", ckpt, "--num_envs", str(num_envs)]
    if lib and libraries is None:
        argv += ["--rl_library", lib]
    argv += _visualizer_args(headless, extras) if libraries is None else _per_library_display_args(root, lib, "play", headless, extras)
    argv += extras
    _require_accepted(root, lib if libraries is not None else None, "play", argv)
    argv += _crash_upload_args(root, extras)  # after the check: its value looks like a flag
    result = _start_job(python, root, "play", task, argv, timeout_s=timeout_s,
                        allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu,
                        headless=headless, rl_library_check=lib, extra_validated=extras,
                        **_launch_kw(python_path, env, external_callback),
                        **({} if libraries is None else {"library": lib}))
    return {**result, "rl_library_from_checkpoint_path": inferred} if inferred else result


_AGENTS = ("zero", "random")


def _check_job(kind: str, python: str, root: Any, task: str, num_envs: int, steps: int, agent: str,
               headless: bool, physics: str | None, extra_args: Any, timeout_s: float | None,
               allow_concurrent: bool, allow_concurrent_gpu: bool, extra_flags: list[str],
               video_length: int = 200, launch: dict[str, Any] | None = None) -> dict[str, Any]:
    _validate_task(task)
    if agent not in _AGENTS:
        raise ValueError(f"agent must be one of {', '.join(_AGENTS)}")
    num_envs = _positive_int(num_envs, "num_envs")
    steps = _positive_int(steps, "steps")
    extras = _with_physics(physics, root, _validate_extra_args(extra_args))
    argv = ["--task", task, "--num_envs", str(num_envs)]
    argv += _visualizer_args(headless, extras + extra_flags)  # --video must not get --viz none
    argv += extra_flags
    argv += extras
    argv += _crash_upload_args(root, extras)
    return _start_job(python, root, kind, task, argv, timeout_s=timeout_s, allow_concurrent=allow_concurrent,
                      allow_concurrent_gpu=allow_concurrent_gpu, headless=headless, extra_validated=extras,
                      check_options={"mode": "check" if kind == "env_check" else "record", "steps": steps,
                                     "agent": agent, "video_length": video_length},
                      **(launch or {}))


def _native_agent_video(root: Any) -> bool:
    """Lab 3's checkpoint-free agents take --video and --max_steps themselves."""
    return (Path(root) / "source" / "isaaclab_rl" / "isaaclab_rl" / "entrypoints" / "simple_agents.py").is_file()


def env_check(python: str, root: Any, task: str, num_envs: int = 4, steps: int = 60, agent: str = "zero",
              headless: bool = True, physics: str | None = None, extra_args: Any = None,
              timeout_s: float | None = 900, allow_concurrent: bool = False,
              allow_concurrent_gpu: bool = False, python_path: Any = None, env: Any = None,
              external_callback: Any = None) -> dict[str, Any]:
    return _check_job("env_check", python, root, task, num_envs, steps, agent, headless, physics, extra_args,
                      timeout_s, allow_concurrent, allow_concurrent_gpu,
                      ["--max_steps", str(int(steps))] if _native_agent_video(root) else [],
                      launch=_launch_kw(python_path, env, external_callback))


def record(python: str, root: Any, task: str, checkpoint: Any = None, num_envs: int = 1, video_length: int = 200,
           agent: str = "zero", headless: bool = True, rl_library: str | None = None, physics: str | None = None,
           extra_args: Any = None, timeout_s: float | None = 1800, allow_concurrent: bool = False,
           allow_concurrent_gpu: bool = False, python_path: Any = None, env: Any = None,
           external_callback: Any = None) -> dict[str, Any]:
    launch = _launch_kw(python_path, env, external_callback)
    video_length = _positive_int(video_length, "video_length")
    if checkpoint:
        extras = _validate_extra_args(extra_args) + ["--video", "--video_length", str(video_length)]
        out = play(python, root, task, checkpoint, num_envs=num_envs, headless=headless, physics=physics,
                   extra_args=extras, timeout_s=timeout_s, rl_library=rl_library,
                   allow_concurrent=allow_concurrent, allow_concurrent_gpu=allow_concurrent_gpu, **launch)
        return {**out, "note": "policy playback; isaaclab__runs_status lists video_paths when it finishes"}
    if _native_agent_video(root):
        # Lab 3's zero/random agents record and stop by themselves (--video, --max_steps): no wrapper needed.
        out = _check_job("env_check", python, root, task, num_envs, video_length + 1, agent, headless, physics,
                         extra_args, timeout_s, allow_concurrent, allow_concurrent_gpu,
                         ["--video", "--video_length", str(video_length), "--max_steps", str(video_length + 1)],
                         launch=launch)
    else:
        out = _check_job("record", python, root, task, num_envs, video_length + 1, agent, headless, physics,
                         extra_args, timeout_s, allow_concurrent, allow_concurrent_gpu, ["--enable_cameras"],
                         video_length=video_length, launch=launch)
    return {**out, "note": f"{agent} actions, no policy; isaaclab__runs_status lists video_paths when it finishes"}


_RELATIVE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhdw])$", re.I)
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
RUNS_LIST_DEFAULT_LIMIT = 25
RUNS_LIST_MAX_LIMIT = 500


def _parse_since(since: Any) -> float:
    """An epoch time from an ISO date/time (naive means local time) or a relative age such as 90m, 2h, 1d, 1w."""
    if not isinstance(since, str) or not since.strip():
        raise ValueError("since must be an ISO time such as 2026-05-01T12:00 or a relative age such as 2h, 1d")
    text = since.strip()
    rel = _RELATIVE_RE.match(text)
    if rel:
        return _now() - float(rel.group(1)) * _UNIT_S[rel.group(2).lower()]
    try:
        from datetime import datetime

        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        raise ValueError(f"cannot read since={since!r}: use an ISO time such as 2026-05-01T12:00 or a relative age such as 2h, 1d") from None


def runs_list(state: str | None = None, task: str | None = None, kind: str | None = None,
              since: str | None = None, limit: int | None = RUNS_LIST_DEFAULT_LIMIT) -> dict[str, Any]:
    """Jobs, newest first, filtered and bounded. ``total`` counts every match, ``truncated`` says ``limit`` cut some."""
    if state and state not in RUN_STATES:
        raise ValueError(f"unknown state {state!r}: use one of {', '.join(RUN_STATES)}, or omit it for all")
    if limit is None:
        limit = RUNS_LIST_DEFAULT_LIMIT
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= RUNS_LIST_MAX_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {RUNS_LIST_MAX_LIMIT}")
    for name, value in (("task", task), ("kind", kind)):
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
    cutoff = _parse_since(since) if since is not None else None
    runs = []
    for jd in sorted(jobs_dir().iterdir()):
        if not jd.is_dir():
            continue
        data = _read_job(jd)
        if not data:
            continue
        # reconcile dead pids
        _enforce_orphan_timeout(jd.name, jd, data)
        if _orphaned(jd.name, data):
            _settle_orphan(jd, data)
        st = data.get("status")
        if state and state != "all":
            if state == "running" and st != "running":
                continue
            if state == "finished" and st == "running":
                continue
        if task and task.lower() not in str(data.get("task") or "").lower():
            continue
        if kind and kind != data.get("kind"):
            continue
        started = data.get("started_at")
        if cutoff is not None and (not started or float(started) < cutoff):
            continue
        elapsed = (_now() - float(started)) if started else 0.0
        if st != "running" and data.get("finished_at"):
            elapsed = float(data["finished_at"]) - float(started)
        runs.append({
            "job_id": data.get("job_id") or data.get("id") or jd.name,
            "kind": data.get("kind"),
            "task": data.get("task"),
            "state": st,
            "started_at": started,
            "elapsed_s": round(elapsed, 3),
            "log_dir": data.get("log_dir"),
        })
    runs.sort(key=lambda r: float(r["started_at"] or 0), reverse=True)
    total = len(runs)
    result: dict[str, Any] = {"runs": runs[:limit], "total": total, "truncated": total > limit}
    if result["truncated"]:
        result["hint"] = (f"Showing the newest {limit} of {total}. Narrow with task, kind, state or since (such as 2h or 1d), "
                          "or raise limit.")
    return result


def runs_status(job_id: str) -> dict[str, Any]:
    jd = _job_path(job_id)
    data = _read_job(jd)
    if not data:
        raise ValueError(f"unknown job: {job_id}; list jobs with isaaclab__runs_list")
    _enforce_orphan_timeout(job_id, jd, data)
    if _orphaned(job_id, data):
        _settle_orphan(jd, data)
    started = data.get("started_at")
    if data.get("status") == "running":
        elapsed = _now() - float(started) if started else 0.0
    else:
        fin = data.get("finished_at")
        elapsed = (float(fin) - float(started)) if (fin and started) else 0.0
    last_lines = _tail_file(Path(data.get("log_dir", jd)) / "stdout.log", 20)
    # fallback if log_dir missing
    if not last_lines:
        last_lines = _tail_file(jd / "stdout.log", 20)
    run_dir = data.get("run_dir")
    ckpt = _latest_checkpoint(run_dir) or _checkpoint_from_log(Path(data.get("log_dir", jd)) / "stdout.log")
    if not ckpt:
        logged = _run_dir_from_log(Path(data.get("log_dir", jd)) / "stdout.log")
        if logged and Path(logged).exists():
            run_dir = logged
            ckpt = _latest_checkpoint(run_dir)
    if ckpt and not _latest_checkpoint(run_dir):
        # the experiment dir (parent of nn/ for rl_games) is the run dir tools like metrics_summary want
        p = Path(ckpt).parent
        run_dir = str(p.parent if p.name == "nn" else p)
    # A launch or import failure often writes only to stderr, which would leave last_log_lines empty.
    failure = {}
    if data.get("status") == "failed":
        failure = {"last_stderr_lines": _tail_file(Path(data.get("log_dir", jd)) / "stderr.log", 20)}
        if any("ovphysx" in str(line).lower() for line in failure["last_stderr_lines"]):
            failure["hint"] = "Isaac Lab's physx preset picks the optional OvPhysX runtime, which this environment lacks. Use the option physics=physx on this tool, which selects isaacsim_physx."
    if data.get("status") == "failed" and data.get("note"):
        failure["note"] = data["note"]
    out = {
        "state": data.get("status"),
        "pid_alive": _job_alive(data),
        "elapsed_s": round(elapsed, 3),
        "exit_code": data.get("exit_code"),
        "last_log_lines": last_lines,
        **failure,
        "latest_checkpoint": ckpt,
        "run_dir": run_dir,
        "log_dir": data.get("log_dir", str(jd)),
        "pid": data.get("pid"),
    }
    if data.get("timeout_s") is not None:
        out["timeout_s"] = data["timeout_s"]
    out.update(_log_facts(Path(data.get("log_dir", jd)) / "stdout.log"))
    videos = _video_paths(run_dir, Path(data.get("log_dir", jd)) / "stdout.log")
    if videos and data.get("kind") in ("env_check", "record") and data.get("started_at"):
        # Lab's agent scripts reuse one video folder per task: keep only clips this job wrote.
        videos = [v for v in videos if _mtime(v) >= float(data["started_at"]) - 2]
    if videos:
        out["video_paths"] = videos
    if out.get("physics") is None and data.get("library"):
        out["physics"] = "physx"  # a Lab with one script folder per library (2.x) announces no engine: it only has PhysX
    if data.get("kind") in ("env_check", "record"):
        result = _check_result(jd)
        if result is not None:
            out["env_check_result"] = result
            if result.get("error") and not result.get("steps") and out["state"] == "succeeded":
                out["state"] = "failed"
        if data.get("kind") == "record":
            vdir = jd / "videos"
            found = sorted(str(x.resolve()) for x in vdir.glob("*.mp4")) if vdir.is_dir() else []
            if found:
                out["video_paths"] = found
    if data.get("kind") == "eval":
        result = _eval_result(jd)
        if result is not None:
            out["eval_result"] = result
            if result.get("error") and not result.get("episodes") and out["state"] == "succeeded":
                out["state"] = "failed"  # the process exited cleanly, but the run scored nothing
    return out


_VIDEO_LOG_TAIL = 4 * 1024**2
_VIDEO_FILES = 5000
_VIDEO_LOG_LINE = re.compile(r"(?:video_folder|output_dir): (\S+)|\[VideoRecorder\] Wrote \d+ frames to (\S+\.mp4)")


def _video_paths(run_dir: Any, log_path: Path | None = None, limit: int = 8) -> list[str]:
    """Videos Lab wrote for this run, newest first: under <run_dir>/videos, or the folder its log names (a play job's own
    folder is not where Lab's run lives). Lab 2.3 logs video_folder; Lab 3 logs output_dir and "[VideoRecorder] Wrote N frames to <file>"."""
    folders = [Path(run_dir, "videos")] if run_dir else []
    try:
        if log_path is not None:
            with log_path.open("rb") as log:  # a training log can be huge, and this runs on every runs_status
                log.seek(max(0, log_path.stat().st_size - _VIDEO_LOG_TAIL))
                tail = log.read().decode(errors="replace")
            named = [Path(m.group(1)) if m.group(1) else Path(m.group(2)).parent for m in _VIDEO_LOG_LINE.finditer(tail)]
            folders += [folder for folder in dict.fromkeys(reversed(named)) if "videos" in folder.parts][:8]  # latest first; never search a folder a log line merely names
    except OSError:
        pass
    found: dict[str, float] = {}
    for folder in folders:
        try:
            if folder.is_dir():
                for path in itertools.islice(folder.rglob("*.mp4"), _VIDEO_FILES):  # enough for every clip a run writes, so the newest is among them
                    found[str(path)] = path.stat().st_mtime
        except OSError:
            continue
    return sorted(found, key=found.get, reverse=True)[:limit]


def runs_tail(job_id: str, stream: str = "stdout", lines: int = 100) -> dict[str, Any]:
    if stream not in ("stdout", "stderr"):
        raise ValueError("stream must be 'stdout' or 'stderr'")
    lines = max(1, min(int(lines), 1000))
    jd = _job_path(job_id)
    data = _read_job(jd)
    if not data:
        raise ValueError(f"unknown job: {job_id}; list jobs with isaaclab__runs_list")
    log_dir = Path(data.get("log_dir", jd))
    fname = "stdout.log" if stream == "stdout" else "stderr.log"
    path = log_dir / fname
    if not path.exists():
        path = jd / fname
    return {"lines": _tail_file(path, lines)}


def runs_cancel(job_id: str, grace_s: int = 10) -> dict[str, Any]:
    jd = _job_path(job_id)
    data = _read_job(jd)
    if not data:
        raise ValueError(f"unknown job: {job_id}; list jobs with isaaclab__runs_list")
    if data.get("status") != "running":
        # already finished (succeeded/failed/cancelled/timeout): leave the record alone
        return {"cancelled": False, "state": data.get("status"), "note": f"job already {data.get('status')}"}
    with _jobs_lock:
        _update_job(jd, status="cancelled", cancel_requested=True, finished_at=_now())
    pid = data.get("pid")
    cancelled = False
    try:
        group = os.getpgid(int(pid)) if _job_alive(data) else None
    except ProcessLookupError:  # it exited between those two calls
        group = None
    if group is not None:
        if group != int(pid):  # jobs lead their own group; a killpg here would miss, or hit another
            return {"cancelled": False, "state": "cancelled", "note": f"pid {pid} does not lead a process group, so this record is not from a job this tool started. Nothing was signalled."}
        try:
            os.killpg(int(pid), signal.SIGTERM)
            cancelled = True
        except ProcessLookupError:
            pass
        # wait grace
        deadline = time.time() + grace_s
        while time.time() < deadline and _job_alive(data):
            time.sleep(0.1)
        if _job_alive(data):
            try:
                os.killpg(int(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
    # update active map
    with _jobs_lock:
        proc = _active.pop(job_id, None)
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
    if not cancelled:
        return {"cancelled": False, "state": "cancelled", "note": "The process had already gone, so nothing was signalled. The record now says cancelled."}
    return {"cancelled": True, "state": "cancelled"}


_rehydrate()  # last: it can need anything above

# expose for tests
__all__ = [
    "jobs_dir",
    "train",
    "eval_",
    "play",
    "runs_list",
    "runs_status",
    "runs_tail",
    "runs_cancel",
]

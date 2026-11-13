"""Isolated RSS commands, durable run ledgers, and bounded asynchronous waits."""

from __future__ import annotations

import concurrent.futures
import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

from .template import SCENE

PIN = "87b5e32"
REPOSITORY = "https://github.com/robosimstudio/robosimstudio"
ACTIONS = ("status", "start", "scene_template", "reference", "snapshot", "compare",
           "rollout", "compare_take", "stop", "report")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError("Use a name of 1–80 letters, digits, underscores or hyphens.")
    return value


def _identity(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        if stat[0] == "Z":
            return None
        return {"pid": int(pid), "start_ticks": stat[19],
                "cmdline": Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")[:-1]}
    except (OSError, ValueError, IndexError):
        return None


class RoboSimBridge:
    """One instance per MCP host; only RSS's private environment executes scenes.

    Scene Python is trusted code, not sandboxed. A run is portable on disk, but
    an in-flight host worker cannot be resumed after the host exits.
    """

    def __init__(self, workspace=None, root=None):
        self.workspace = Path(workspace or os.environ.get("ISAAC_MCP_WORKSPACE", "~/isaac-workspace")).expanduser().resolve()
        self.root = Path(root or os.environ.get("ISAAC_MCP_ROBOSIM_ROOT", self.workspace / "tools/robosimstudio")).expanduser().resolve()
        self.runs = self.workspace / "runs/robosim"
        self._jobs = {}
        self._mutex = threading.RLock()

    def _installation(self):
        commit = None
        try:
            result = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                commit = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        present = (self.root / ".venv/bin/python").is_file() and os.access(self.root / ".venv/bin/python", os.X_OK)
        info = {"checkout": str(self.root), "rss_commit": commit, "pin": PIN,
                "pin_match": bool(commit and commit.startswith(PIN)), "environment_present": present,
                "warnings": []}
        if not info["pin_match"]:
            info["warnings"].append(f"RSS checkout commit {commit or 'unknown'} differs from tested pin {PIN}.")
        ctl = self.root / "examples/live/ctl"
        if not present or not ctl.is_file() or not os.access(ctl, os.X_OK) or not commit:
            info.update(ok=False, error=f"RSS checkout/private environment missing or unreadable. Clone {REPOSITORY} "
                        f"at commit {PIN} into {self.root}, then provision its own .venv/bin/python. "
                        "The bridge never installs RSS or changes its environment.")
        return info

    def _load(self, run):
        path = self.runs / _name(run)
        if path.is_symlink() or path.resolve().parent != self.runs.resolve():
            raise ValueError("Run must be a direct, non-symlink bridge run folder.")
        ledger = json.loads((path / "run.json").read_text())
        if ledger.get("bridge") != "isaac-mcp-robosim-v1" or ledger.get("run") != run:
            raise ValueError("Not a bridge-owned RSS run.")
        if ledger["checkout"] != str(self.root):
            raise ValueError("Run belongs to another RSS checkout; set ISAAC_MCP_ROBOSIM_ROOT accordingly.")
        return path, ledger

    def _save(self, path, ledger):
        ledger["updated_at"] = _now()
        temporary = path / f".run-{uuid.uuid4().hex}.json"
        temporary.write_text(json.dumps(ledger, indent=2) + "\n")
        temporary.replace(path / "run.json")

    def _environment(self, device):
        env = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            env.pop(key, None)
        env.update(CUDA_VISIBLE_DEVICES="" if device == "cpu" else "0",
                   ROBOSIMSTUDIO_DEVICE=device, PYTHONDONTWRITEBYTECODE="1",
                   TMPDIR=str(self.root / ".tmp"), XDG_CACHE_HOME=str(self.root / ".cache"),
                   WARP_CACHE_PATH=str(self.root / ".cache/warp"))
        for key in ("TMPDIR", "XDG_CACHE_HOME", "WARP_CACHE_PATH"):
            Path(env[key]).mkdir(parents=True, exist_ok=True)
        env["PATH"] = str(self.root / ".venv/bin") + os.pathsep + env.get("PATH", "")
        return env

    def _gpu_guard(self, allow, owned_pid=None):
        if allow:
            return
        try:
            result = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader,nounits"],
                                    capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            raise ValueError("Cannot check GPU workloads; refusing GPU use. Choose cpu or explicitly allow_concurrent_gpu.") from None
        if result.returncode:
            raise ValueError("Cannot check GPU workloads; refusing GPU use. Choose cpu or explicitly allow_concurrent_gpu.")
        others = [line for line in result.stdout.splitlines()
                  if line.strip() and line.split(",", 1)[0].strip() != str(owned_pid)]
        if others:
            raise ValueError("Other GPU work is running: " + "; ".join(others)
                             + ". Use cpu, or knowingly set allow_concurrent_gpu=true. Nothing was stopped.")

    def _claim_server(self, path, ledger):
        if ledger.get("server"):
            return
        try:
            identity = _identity(int((path / "serve.pid").read_text().strip()))
        except (OSError, ValueError):
            return
        if identity and "robosimstudio_real2sim_agent.serve" in identity["cmdline"]:
            arguments = identity["cmdline"]
            if "--run-dir" in arguments and arguments[arguments.index("--run-dir") + 1] == str(path):
                ledger["server"] = identity
                self._save(path, ledger)

    def _owned_alive(self, ledger):
        server = ledger.get("server")
        return bool(server and _identity(server["pid"]) == server)

    def _command(self, path, ledger, args, timeout=630):
        argv = [str(self.root / "examples/live/ctl"), "--run-dir", str(path), *args, "--json"]
        record = {"argv": argv, "started_at": _now()}
        ledger["commands"].append(record)
        number = len(ledger["commands"])
        stdout = path / f"command-{number:04d}.stdout"
        stderr = path / f"command-{number:04d}.stderr"
        record.update(stdout=str(stdout), stderr=str(stderr))
        self._save(path, ledger)
        started = time.monotonic()
        try:
            with stdout.open("w") as out, stderr.open("w") as err:
                result = subprocess.run(argv, cwd=path, env=self._environment(ledger["device"]),
                                        stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=timeout)
            record["returncode"] = result.returncode
            text = stdout.read_text(errors="replace")
            try:
                reply = json.loads(text)
            except ValueError:
                reply = {"ok": False, "error": "RSS returned non-JSON output; inspect command logs.", "tail": text[-2000:]}
            if not isinstance(reply, dict):
                reply = {"ok": False, "error": "RSS returned a non-object JSON response."}
            if result.returncode or not reply.get("ok", False):
                reply["ok"] = False
                reply.setdefault("error", stderr.read_text(errors="replace")[-2000:] or "RSS command failed.")
            record["result"] = reply
            return reply
        finally:
            record.update(finished_at=_now(), wall_s=round(time.monotonic() - started, 3))
            if args[0] == "up":
                self._claim_server(path, ledger)
            self._save(path, ledger)

    def _copy_input(self, source, destination):
        original = Path(source).expanduser()
        if not original.is_absolute() or not original.is_file():
            raise ValueError("Input must be an existing absolute file path.")
        shutil.copyfile(original, destination)
        digest = hashlib.sha256()
        with destination.open("rb") as source_file:
            for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"source": str(original.resolve()), "copy": str(destination),
                "sha256": digest.hexdigest()}

    def call(self, action, *, run=None, name=None, scene=None, device=None, video=None,
             keys=None, take=None, routine=None, camera=None, at="0:start,end", every=4,
             size="320x240", width=320, port=8080, wait_s=0, allow_concurrent_gpu=False):
        info = self._installation()
        try:
            if info.get("error"):
                return info
            if not isinstance(wait_s, (int, float)) or not math.isfinite(wait_s) or not 0 <= wait_s <= 120:
                raise ValueError("wait_s must be between 0 and 120 seconds.")
            if action not in ACTIONS:
                raise ValueError("action must be one of: " + ", ".join(ACTIONS))
            if action == "scene_template":
                return dict(info, ok=True, scene=SCENE, parameters=["OBJECT_HALF_SIZE", "OBJECT_POSE", "OBJECT_MASS",
                            "OBJECT_FRICTION", "CAMERA_EYE", "CAMERA_LOOK_AT"],
                            note="Trusted Python, not sandboxed. Default routine only settles; no physics fitting.")
            if action == "status":
                return dict(info, **self._status(run, wait_s))
            if action == "start":
                if device not in ("cpu", "cuda:0"):
                    raise ValueError("start requires explicit device='cpu' or device='cuda:0'. No automatic GPU selection.")
                _name(name)
                if len(name) > 64:
                    raise ValueError("Run name must be at most 64 characters (80 including the timestamp).")
                if not scene or Path(scene).suffix != ".py" or not Path(scene).is_absolute() or not Path(scene).is_file():
                    raise ValueError("scene must be an existing absolute .py file containing trusted scene code.")
                if not isinstance(port, int) or not 1024 <= port <= 65535:
                    raise ValueError("port must be an integer from 1024 to 65535.")
                if device == "cuda:0":
                    self._gpu_guard(allow_concurrent_gpu)
                self.runs.mkdir(parents=True, exist_ok=True)
                run = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + name
                path = self.runs / run
                path.mkdir(exist_ok=False)
                ledger = dict(bridge="isaac-mcp-robosim-v1", run=run, checkout=str(self.root),
                              rss_commit=info["rss_commit"], device=device, created_at=_now(),
                              inputs={}, commands=[], operations=[], server=None,
                              allow_concurrent_gpu=allow_concurrent_gpu)
                ledger["inputs"]["scene"] = self._copy_input(scene, path / "scene.py")
                self._save(path, ledger)
            else:
                path, ledger = self._load(run)
                if device is not None and device != ledger["device"]:
                    raise ValueError("device cannot change within a run; start a separate run.")
                if action not in ("stop", "report") and not self._owned_alive(ledger):
                    raise ValueError("Run's bridge-owned server is not alive. Start a new run; no other server was contacted.")
                if action in ("compare", "compare_take") and (not keys or any(not isinstance(key, str) or "=" not in key for key in keys)):
                    raise ValueError("keys must contain RSS masks such as ['cube=red'].")
                if action in ("rollout", "compare_take"):
                    _name(take)
                if routine:
                    _name(routine)
                if action == "rollout" and (path / f"{take}.json").exists():
                    raise ValueError("Take already exists; choose a new take name.")
            if not re.fullmatch(r"[1-9][0-9]{0,3}x[1-9][0-9]{0,3}", size):
                raise ValueError("size must be WIDTHxHEIGHT (1–9999 pixels each).")
            if not isinstance(every, int) or every < 1 or not isinstance(width, int) or not 1 <= width <= 4096:
                raise ValueError("every must be positive; width must be 1–4096.")
            arguments = dict(video=video, keys=keys, take=take, routine=routine, camera=camera,
                             at=at, every=every, size=size, width=width, port=port)
            with self._mutex:
                if run in self._jobs and not self._jobs[run].done():
                    raise ValueError("Run has an active operation; poll status before another action.")
                lock = (path / ".bridge.lock").open("a")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    lock.close()
                    raise ValueError("Another host is operating this run; poll status.") from None
                try:
                    if action != "start":
                        path, ledger = self._load(run)
                    future = concurrent.futures.Future()
                    operation = {"action": action, "arguments": arguments, "state": "running", "started_at": _now()}
                    ledger["operations"].append(operation)
                    self._save(path, ledger)
                    self._jobs[run] = future
                    threading.Thread(target=self._worker, args=(future, lock, path, ledger, operation, arguments),
                                     daemon=True, name=f"robosim-{run}").start()
                except Exception:
                    lock.close()
                    self._jobs.pop(run, None)
                    raise
            return dict(info, **self._status(run, wait_s))
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            return dict(info, ok=False, error=str(exc))

    def _worker(self, future, lock, path, ledger, operation, arguments):
        started = time.monotonic()
        try:
            result = self._execute(operation["action"], path, ledger, **arguments)
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "hint": "Inspect the run's command logs; stop this run if its server remains live."}
        operation.update(state="completed" if result.get("ok") else "failed", result=result,
                         finished_at=_now(), wall_s=round(time.monotonic() - started, 3))
        try:
            self._save(path, ledger)
        finally:
            lock.close()
            future.set_result(result)

    def _status(self, run, wait_s):
        if not run:
            runs = []
            for ledger_path in sorted(self.runs.glob("*/run.json"), reverse=True):
                try:
                    runs.append(self._status(ledger_path.parent.name, 0))
                except (OSError, ValueError, KeyError):
                    continue
            return {"ok": True, "runs": runs, "live_servers": [entry["server"] for entry in runs if entry["server_alive"]]}
        future = self._jobs.get(run)
        if future and wait_s:
            try:
                future.result(timeout=wait_s)
            except concurrent.futures.TimeoutError:
                pass
        path, ledger = self._load(run)
        operation = ledger["operations"][-1] if ledger["operations"] else None
        return {"ok": not operation or operation["state"] != "failed", "run": run, "run_dir": str(path),
                "device": ledger["device"], "run_rss_commit": ledger["rss_commit"],
                "server": ledger.get("server"), "server_alive": self._owned_alive(ledger),
                "operation": operation, "ledger": str(path / "run.json")}

    def _execute(self, action, path, ledger, *, video, keys, take, routine, camera, at, every, size, width, port):
        camera_args = ["--camera", camera] if camera else []
        key_args = [part for key in keys or [] for part in ("--key", key)]
        sequence = len(ledger["operations"])
        if action == "start":
            reply = self._command(path, ledger, ["up", "--scene", str(path / "scene.py"), "--port", str(port),
                                               "--hold", "--no-watch"], timeout=330)
            if reply.get("already"):
                ledger["server"] = None
                return {"ok": False, "error": "RSS reported an existing server; the bridge will not adopt or stop it."}
            if reply.get("ok"):
                status = self._command(path, ledger, ["status"], timeout=30)
                if not status.get("ok") or status.get("last_error"):
                    return {"ok": False, "error": "Scene failed to load; stop this run.", "status": status}
                reply["status"] = status
            return reply
        if action == "stop":
            if not self._owned_alive(ledger):
                return {"ok": True, "stopped": False, "message": "Owned server is no longer alive; no process was signalled."}
            reply = self._command(path, ledger, ["quit"], timeout=30)
            deadline = time.monotonic() + 20
            while self._owned_alive(ledger) and time.monotonic() < deadline:
                time.sleep(0.1)
            reply["stopped"] = not self._owned_alive(ledger)
            if not reply["stopped"]:
                reply.update(ok=False, error="Owned server still alive after quit; no process was force-killed.")
            return reply
        if action == "reference":
            suffix = Path(video or "").suffix
            if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix):
                raise ValueError("video must be an absolute file path with a video extension.")
            copied = path / f"reference-{sequence}{suffix}"
            ledger["inputs"][f"reference-{sequence}"] = self._copy_input(video, copied)
            return self._command(path, ledger, ["ref", "frames", str(copied), "--at", at,
                                               "--width", str(width), "--out", str(path / "ref")])
        if action == "snapshot":
            return self._command(path, ledger, ["snapshot", *camera_args, "--size", size,
                                               "--out", str(path / f"snapshot-{sequence}.png")])
        if action == "compare":
            reply = self._command(path, ledger, ["compare", *camera_args, *key_args, "--size", size,
                                               "--out", str(path / f"comparison-{sequence}.png")])
            reply["metrics"] = reply.get("objects", [])
            return reply
        if action == "compare_take":
            return self._command(path, ledger, ["compare", "--take", take, *key_args,
                                               "--timeline", str(path / "ref/timeline.json"), *camera_args])
        if action == "rollout":
            reply = self._command(path, ledger, ["play", *([routine] if routine else []), "--record", take,
                                               "--every", str(every), *camera_args], timeout=330)
            if not reply.get("ok"):
                return reply
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                status = self._command(path, ledger, ["status"], timeout=30)
                if not status.get("ok") or status.get("last_error"):
                    return {"ok": False, "error": "RSS recording failed.", "status": status}
                if not status.get("playing") and not status.get("recording"):
                    outputs = {"mp4": str(path / f"{take}.mp4"), "json": str(path / f"{take}.json")}
                    valid = status.get("last_take") == take and all(Path(value).is_file() for value in outputs.values())
                    return dict(ok=valid, take=take, status=status, **outputs,
                                **({} if valid else {"error": "Routine ended without the requested take files."}))
                time.sleep(1)
            return {"ok": False, "error": "Recording exceeded 30 minutes; poll then stop the run."}
        if action == "report":
            report = path / "REPORT.md"
            lines = ["# RSS image-space comparison", "", "Not inverse-physics fitting; no automatic mass or friction estimation.",
                     "", f"RSS commit: `{ledger['rss_commit']}` (tested pin `{PIN}`). Device: `{ledger['device']}`.",
                     "", "## Inputs", "", "```json", json.dumps(ledger["inputs"], indent=2), "```", "", "## Operations"]
            for previous in ledger["operations"][:-1]:
                lines += ["", f"### {previous['action']} — {previous['state']}",
                          f"Wall time: {previous.get('wall_s', '?')} seconds. Started: {previous['started_at']}.",
                          "", "```json", json.dumps(previous.get("result", {}), indent=2), "```"]
            lines += ["", "## Media", *[f"- `{file}`" for file in sorted(path.rglob("*"))
                                         if file.suffix in (".png", ".jpg", ".mp4", ".webm")]]
            report.write_text("\n".join(lines) + "\n")
            return {"ok": True, "report": str(report)}
        raise ValueError(f"Unsupported action {action}")

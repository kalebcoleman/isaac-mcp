#!/usr/bin/env python3
"""Full-system demo / test runner: scenarios S0-S9 of docs/demo/TEST-PLAN.md.

Run with the repo environment (the sim needs the root `.venv`, and the runner
imports `isaac_mcp` from `mcp/` by path, so nothing has to be installed):

    OMNI_KIT_ACCEPT_EULA=YES .venv/bin/python scripts/demo/run_demo.py \\
        --machine asu --out demo-out/asu [--port 8791] [--lab-minutes 5] \\
        [--num-envs 1024] [--skip-sim] [--skip-lab] [--headless]

Evidence layout (git-ignored per the test plan):

    demo-out/<machine>/{report.json,report.md,logs/,shots/,videos/,data/}

Every scenario is an independent step wrapped in try/except: a failure is
recorded (ok=false, note, artifacts) and the run continues. Steps whose
precondition failed are recorded as SKIPPED with the reason.

Window capture does not use `isaac_mcp.screen` (it needs mss/python-xlib, which
the root `.venv` does not have). Instead this walks `xwininfo -root -tree`,
matches titles/pids the way `screen._visible_windows`/`window_for_pid` do, and
reads a window's own pixels with `xwd -id <win>` + `ffmpeg` (XGetImage, i.e. the
window's contents even when another window covers it). Videos are assembled
from those stills, so they show the window, not whatever is on top of it.

Product-code note recorded by a real run: `isaaclab__metrics_summary` (and
`plot_curves.py`) need the `tensorboard` package, which the root `.venv` lacks,
so metrics here are read through the Isaac Lab interpreter.
"""
from __future__ import annotations

import argparse
import atexit
import getpass
import importlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MCP_DIR = REPO / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from isaac_mcp import client as bridge_client  # noqa: E402
from isaac_mcp.isaaclab import install as lab_install  # noqa: E402
from isaac_mcp.isaaclab import jobs as lab_jobs  # noqa: E402

REPO_PY = REPO / ".venv" / "bin" / "python"
TWIN_REPO = Path.home() / "Desktop" / "ur5e-digital-twin"
ISAAC_TITLES = ("isaac sim", "isaac lab")
STOCK_TASK = "Isaac-Reach-UR10"
DEMO_TASK = "UR5e-Cell-Reach-Direct-v0"


# --------------------------------------------------------------------------- #
# tiny helpers
# --------------------------------------------------------------------------- #
class Skip(Exception):
    """A step whose precondition failed; recorded as SKIPPED with this reason."""


def now() -> float:
    return time.time()


def stamp(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts or now()))


def write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")
    return path


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def run_cmd(argv, *, timeout: float = 120, env=None, cwd=None, quiet: bool = True):
    """Run an argv list (never a shell string). Returns CompletedProcess with text output."""
    return subprocess.run(
        [str(a) for a in argv],
        capture_output=quiet,
        text=True,
        timeout=timeout,
        env=env,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
    )


def ffmpeg_bin() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "ffmpeg"
    return str(local) if local.is_file() else None


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def wait_port(port: int, deadline_s: float) -> float:
    """Block until `port` accepts a connection; return seconds waited."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < deadline_s:
        if port_open(port):
            return time.monotonic() - t0
        time.sleep(0.25)
    raise TimeoutError(f"port {port} did not open within {deadline_s:.0f}s")


def wait_port_closed(port: int, deadline_s: float = 20) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < deadline_s:
        if not port_open(port):
            return time.monotonic() - t0
        time.sleep(0.25)
    return time.monotonic() - t0


# --------------------------------------------------------------------------- #
# images
# --------------------------------------------------------------------------- #
def _pil():
    from PIL import Image  # noqa: F401  (PIL/numpy are in the root .venv)

    return importlib.import_module("PIL.Image")


def image_stats(path: Path) -> dict:
    Image = _pil()
    im = Image.open(path).convert("RGB")
    import numpy as np

    a = np.asarray(im, dtype=np.uint8)
    return {
        "width": int(im.width),
        "height": int(im.height),
        "mean": round(float(a.mean()), 2),
        "std": round(float(a.std()), 2),
        "nonblack_frac": round(float((a.max(axis=2) > 24).mean()), 3),
        "bytes": path.stat().st_size if path.exists() else 0,
    }


def image_diff(path_a: Path, path_b: Path) -> dict:
    Image = _pil()
    import numpy as np

    a = Image.open(path_a).convert("RGB")
    b = Image.open(path_b).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size)
    x = np.asarray(a, dtype=np.int16)
    y = np.asarray(b, dtype=np.int16)
    d = np.abs(x - y).max(axis=2)
    return {
        "frac_changed": round(float((d > 25).mean()), 4),
        "mean_abs": round(float(np.abs(x - y).mean()), 2),
    }


# --------------------------------------------------------------------------- #
# X11 windows: xwininfo -root -tree + xwd/ffmpeg (no python-xlib in the root .venv)
# --------------------------------------------------------------------------- #
_WIN_RE = re.compile(
    r'^\s*(0x[0-9a-fA-F]+)\s+(?:"(.*?)"|\(has no name\)):\s*(?:\((.*?)\))?\s*'
    r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)\s+([+-]-?\d+)([+-]-?\d+)\s*$"
)


def _coord(text: str) -> int:
    """xwininfo prints a negative relative offset as `+-200`."""
    return int(text.replace("+-", "-"))


def xwin_tree() -> list[dict]:
    """Every window in `xwininfo -root -tree`, with absolute position and parent.

    `xwininfo -root -tree` lists the root's children bottom-to-top, and a nested
    window's parent is the nearest preceding entry with a smaller indent, so the
    whole tree (and the stacking order) comes from this one call.
    """
    out = run_cmd(["xwininfo", "-root", "-tree"], timeout=30)
    wins = []
    stack: list[tuple[int, int]] = []
    for line in (out.stdout or "").splitlines():
        m = _WIN_RE.match(line)
        if not m:
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        wid, title, cls, w, h, _rx, _ry, ax, ay = m.groups()
        wins.append(
            {
                "id": int(wid, 16),
                "id_hex": wid,
                "title": title or "",
                "class": cls or "",
                "width": int(w),
                "height": int(h),
                "x": _coord(ax),
                "y": _coord(ay),
                "parent": stack[-1][1] if stack else None,
            }
        )
        stack.append((indent, int(wid, 16)))
    return wins


def top_level_window(wid: int) -> int | None:
    """The ancestor of `wid` that is a direct child of the root (its WM frame, if any)."""
    wins = {w["id"]: w for w in xwin_tree()}
    seen = set()
    cur = wid
    while cur in wins and wins[cur]["parent"] and cur not in seen:
        seen.add(cur)
        cur = wins[cur]["parent"]
    return cur if cur in wins else None


def stacking_order() -> list[int]:
    """Root children, bottom to top (XQueryTree order, as xwininfo prints it)."""
    return [w["id"] for w in xwin_tree() if w["parent"] is None]



def window_details(wid: int) -> dict:
    """Geometry + map state + pid of one window (`xwininfo -id`, `xprop`)."""
    info = {"id": wid, "viewable": False, "x": None, "y": None, "width": None, "height": None, "pid": None}
    out = run_cmd(["xwininfo", "-id", hex(wid)], timeout=20).stdout or ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Absolute upper-left X:"):
            info["x"] = int(line.split(":", 1)[1])
        elif line.startswith("Absolute upper-left Y:"):
            info["y"] = int(line.split(":", 1)[1])
        elif line.startswith("Width:"):
            info["width"] = int(line.split(":", 1)[1])
        elif line.startswith("Height:"):
            info["height"] = int(line.split(":", 1)[1])
        elif line.startswith("Map State:"):
            info["viewable"] = "IsViewable" in line
    props = run_cmd(["xprop", "-id", hex(wid), "_NET_WM_PID", "WM_CLASS", "_NET_WM_NAME"], timeout=20).stdout or ""
    m = re.search(r"_NET_WM_PID\(CARDINAL\)\s*=\s*(\d+)", props)
    if m:
        info["pid"] = int(m.group(1))
    return info


def find_window(needle: str | None = None, pid: int | None = None, min_size=(240, 160)) -> dict | None:
    """Largest viewable window whose title/class STARTS WITH `needle` (and whose pid is `pid`).

    Mirrors `isaac_mcp.screen.AUTO_WINDOWS`/`window_for_pid`: the title match is a
    case-insensitive prefix (so a browser tab called "…Isaac Sim…" is not the
    sim), the pid is the X client pid, and the biggest match wins.
    """
    cands = [
        w
        for w in xwin_tree()
        if w["width"] >= min_size[0] and w["height"] >= min_size[1]
    ]
    if needle:
        low = needle.lower()
        cands = [
            w
            for w in cands
            if w["title"].strip().lower().startswith(low) or w["class"].strip().lower().startswith(low)
        ]
    detailed = []
    for w in cands:
        d = window_details(w["id"])
        if not d["viewable"]:
            continue
        w = {**w, **{k: v for k, v in d.items() if v is not None}}
        w["title"] = w["title"] or ""
        detailed.append(w)
    if not detailed:
        return None
    if pid:
        owned = [w for w in detailed if w.get("pid") == pid]
        if owned:
            detailed = owned
    detailed.sort(key=lambda w: w["width"] * w["height"], reverse=True)
    return detailed[0]


def find_isaac_window(pid: int | None = None) -> dict | None:
    """The Isaac Sim / Isaac Lab window, preferring one owned by `pid`."""
    for needle in ISAAC_TITLES:
        win = find_window(needle, pid=pid)
        if win:
            return win
    return None


def capture_window(win: dict, out_png: Path, attempts: int = 3) -> dict:
    """Read a window's own pixels with xwd and encode them to PNG with ffmpeg."""
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found (window capture needs it)")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    xwd = out_png.with_suffix(".xwd")
    last = ""
    for _ in range(attempts):
        proc = run_cmd(["xwd", "-id", hex(win["id"]), "-silent", "-out", xwd], timeout=90)
        if proc.returncode == 0 and xwd.exists() and xwd.stat().st_size > 0:
            enc = run_cmd([ffmpeg, "-y", "-loglevel", "error", "-i", xwd, str(out_png)], timeout=120)
            if enc.returncode == 0 and out_png.exists() and out_png.stat().st_size > 0:
                xwd.unlink(missing_ok=True)
                return image_stats(out_png)
            last = (enc.stderr or "").strip()
        else:
            last = (proc.stderr or proc.stdout or "").strip()
        time.sleep(1.0)
    xwd.unlink(missing_ok=True)
    raise RuntimeError(f"cannot capture window {win['id_hex']} ({win['title'][:40]}): {last[:200]}")


def record_window(win: dict, out_mp4: Path, seconds: float, fps: int = 10) -> dict:
    """Record the window (not the screen) by grabbing stills and assembling them."""
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    frames_dir = out_mp4.parent / (out_mp4.stem + "-frames")
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    target = int(round(seconds * fps))
    t0 = time.monotonic()
    n = 0
    while (time.monotonic() - t0) < seconds and n < target * 2:
        frame = frames_dir / f"f{n:05d}.png"
        try:
            capture_window(win, frame, attempts=1)
            n += 1
        except RuntimeError:
            break
        sleep_for = (t0 + n / fps) - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
    elapsed = max(1e-6, time.monotonic() - t0)
    if n < 2:
        raise RuntimeError(f"only {n} frames captured from the window")
    rate = max(1.0, n / elapsed)
    proc = run_cmd(
        [
            ffmpeg, "-y", "-loglevel", "error",
            "-framerate", f"{rate:.3f}", "-i", str(frames_dir / "f%05d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
            "-movflags", "+faststart", str(out_mp4),
        ],
        timeout=300,
    )
    if proc.returncode != 0 or not out_mp4.exists():
        raise RuntimeError(f"ffmpeg assembly failed: {(proc.stderr or '').strip()[:200]}")
    shutil.rmtree(frames_dir, ignore_errors=True)
    return {
        "frames": n,
        "target_frames": target,
        "capture_seconds": round(elapsed, 2),
        "video_fps": round(rate, 2),
        "bytes": out_mp4.stat().st_size,
        "requested_seconds": seconds,
    }


def ffprobe_duration(path: Path) -> float | None:
    """Duration via ffprobe, else the `Duration:` line ffmpeg prints while opening the file."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        ffmpeg = ffmpeg_bin()
        if ffmpeg:
            candidate = Path(ffmpeg).with_name("ffprobe")
            ffprobe = str(candidate) if candidate.is_file() else None
    if ffprobe:
        proc = run_cmd(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            timeout=60,
        )
        try:
            return round(float((proc.stdout or "").strip()), 2)
        except ValueError:
            pass
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        return None
    proc = run_cmd([ffmpeg, "-hide_banner", "-i", str(path)], timeout=60)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", (proc.stderr or "") + (proc.stdout or ""))
    if not m:
        return None
    hours, minutes, secs = m.groups()
    return round(int(hours) * 3600 + int(minutes) * 60 + float(secs), 2)


# --------------------------------------------------------------------------- #
# Isaac Sim (Kit) process
# --------------------------------------------------------------------------- #
_SIM_REGISTRY: list = []


class Sim:
    """`./run.sh` in its own session, so kill -9 on the group is unambiguous."""

    READY_CODE = r"""
import omni.usd
try:
    import omni.kit.app
    app_ready = bool(omni.kit.app.get_app().is_app_ready())
except Exception:
    app_ready = None
stage = omni.usd.get_context().get_stage()
result = {
    'stage': stage is not None,
    'app_ready': app_ready,
    'prims': (len(list(stage.Traverse())) if stage is not None else 0),
}
"""

    def __init__(self, port: int, headless: bool, out_dir: Path, log_path: Path):
        self.port = port
        self.headless = headless
        self.out_dir = out_dir
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None
        self._log = None
        self.boot_seconds: float | None = None

    def start(self, boot_timeout: float = 300) -> float:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(
            {
                "OMNI_KIT_ACCEPT_EULA": "YES",
                "UR5E_DT_PORT": str(self.port),
                # the twin's start_recording writes into $MCP_CLIENT_OUTPUT_DIR/videos
                "MCP_CLIENT_OUTPUT_DIR": str(self.out_dir),
            }
        )
        argv = [str(REPO / "run.sh"), "--port", str(self.port)]
        if self.headless:
            argv.append("--headless")
        self._log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            argv,
            cwd=str(REPO),
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        _SIM_REGISTRY.append(self)
        self.boot_seconds = wait_port(self.port, boot_timeout)
        return self.boot_seconds

    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def stop(self, close_timeout: float = 25) -> dict:
        """SIGKILL the process group (Isaac Sim ignores SIGTERM), then confirm the port is closed."""
        info = {"pid": None, "port_closed_in_s": None, "already_dead": False}
        if not self.proc:
            return info
        info["pid"] = self.proc.pid
        if self.proc.poll() is not None:
            info["already_dead"] = True
        else:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except OSError:
                pass
            try:
                os.kill(self.proc.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            self.proc.wait(timeout=45)
        except subprocess.TimeoutExpired:
            pass
        info["port_closed_in_s"] = round(wait_port_closed(self.port, close_timeout), 2)
        try:
            if self._log:
                self._log.close()
        except OSError:
            pass
        self._log = None
        self.proc = None
        return info


def stop_all_sims() -> None:
    for sim in list(_SIM_REGISTRY):
        try:
            if sim.proc:
                sim.stop()
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass


atexit.register(stop_all_sims)


# --------------------------------------------------------------------------- #
# bridge calls
# --------------------------------------------------------------------------- #
def bcall(port: int, tool: str, params: dict | None = None, timeout: float = 300):
    return bridge_client.call(tool, params or {}, port=port, timeout=timeout)


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
IDS = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"]
NAMES = {
    "S0": "Machine snapshot",
    "S1": "Health check",
    "S2": "Isaac Sim twin, windowed",
    "S3": "ROS TF",
    "S4": "Motion video",
    "S5": "Isaac Lab tools",
    "S6": "Guards",
    "S7": "Training",
    "S8": "Windowed policy play + visual feedback",
    "S9": "Screen tools",
}


class Runner:
    def __init__(self, args):
        self.args = args
        if not getattr(args, "out", None):
            args.out = str(Path("demo-out") / args.machine)
        self.out = Path(args.out).expanduser().resolve()
        self.dirs = {name: self.out / name for name in ("logs", "shots", "videos", "data")}
        self.steps: dict[str, dict] = {}
        for sid in IDS:
            self.steps[sid] = {
                "id": sid,
                "name": NAMES[sid],
                "ok": False,
                "status": "pending",
                "seconds": 0.0,
                "note": "",
                "artifacts": [],
                "metrics": {},
                "checks": [],
                "errors": [],
                "_acc": 0.0,
            }
        self.ctx: dict = {}
        self.sim: Sim | None = None
        self.lab_python: str | None = None
        self.lab_root: str | None = None
        self.started = now()

    # -- plumbing ---------------------------------------------------------- #
    def prepare(self) -> None:
        for d in self.dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        # the lab-job guard probes the configured twin port, so pin it in this process
        os.environ["UR5E_DT_PORT"] = str(self.args.port)

    def art(self, sid: str, path: Path | str, label: str | None = None) -> None:
        path = Path(path)
        try:
            shown = os.path.relpath(path.resolve(), self.out)
        except OSError:
            shown = str(path)
        entry = shown if not label else f"{shown} ({label})"
        if entry not in self.steps[sid]["artifacts"]:
            self.steps[sid]["artifacts"].append(entry)

    def metric(self, sid: str, **kwargs) -> None:
        self.steps[sid]["metrics"].update(kwargs)

    def check(self, sid: str, name: str, ok: bool, **details) -> bool:
        self.steps[sid]["checks"].append({"check": name, "ok": bool(ok), **details})
        return bool(ok)

    def phase(self, sid: str, fn, *args) -> None:
        """Run one phase of a scenario: never raises; failures land on the record."""
        rec = self.steps[sid]
        t0 = time.monotonic()
        try:
            fn(rec, *args)
        except Skip as exc:
            rec["status"] = "skipped"
            rec["note"] = f"SKIPPED: {exc}"
        except Exception as exc:  # noqa: BLE001 - a step must never abort the run
            rec["status"] = "fail"
            rec["errors"].append(f"{type(exc).__name__}: {exc}")
            rec["note"] = (rec["note"] + " | " if rec["note"] else "") + f"{type(exc).__name__}: {exc}"
            tb = traceback.format_exc()
            log = self.dirs["logs"] / f"{sid}-error.txt"
            log.write_text(tb)
            self.art(sid, log, "traceback")
        finally:
            rec["_acc"] += time.monotonic() - t0

    def finish_step(self, sid: str) -> None:
        rec = self.steps[sid]
        rec["seconds"] = round(rec.pop("_acc"), 2)
        if rec["status"] == "pending":
            rec["status"] = "fail" if rec["errors"] else "pass"
        if rec["status"] == "pass" and (rec["errors"] or any(c.get("ok") is False for c in rec["checks"])):
            failed = [c["check"] for c in rec["checks"] if c.get("ok") is False]
            if rec["errors"] or failed:
                rec["status"] = "fail"
                if failed:
                    rec["note"] = (rec["note"] + " | " if rec["note"] else "") + "failed check(s): " + ", ".join(failed)
        rec["ok"] = rec["status"] == "pass"
        rec.setdefault("note", "")
        label = rec["status"].upper()
        print(f"[{sid}] {rec['name']}: {label} in {rec['seconds']:.1f}s"
              + (f" — {rec['note']}" if rec["note"] else ""), flush=True)

    def skip(self, sid: str, reason: str) -> None:
        rec = self.steps[sid]
        rec["status"] = "skipped"
        rec["note"] = f"SKIPPED: {reason}"
        rec["seconds"] = round(rec.pop("_acc", 0.0), 2)
        rec["ok"] = False
        print(f"[{sid}] {rec['name']}: SKIPPED ({reason})", flush=True)

    # -- lab helpers ------------------------------------------------------- #
    def resolve_lab(self) -> tuple[str, str]:
        loc = lab_install.locate_install()
        if not loc:
            raise Skip("no Isaac Lab install (set ISAACLAB_PYTHON / ISAACLAB_ROOT)")
        self.lab_python = loc["python"]
        self.lab_root = loc["isaaclab_root"]
        return self.lab_python, self.lab_root

    def lab_json(self, code: str, timeout: float = 180) -> dict:
        """Run JSON-emitting code in the Isaac Lab interpreter (it has tensorboard)."""
        env = dict(os.environ)
        env["OMNI_KIT_ACCEPT_EULA"] = "YES"
        env["PYTHONPATH"] = os.pathsep.join([str(MCP_DIR), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
        proc = run_cmd([self.lab_python or str(REPO_PY), "-c", code], timeout=timeout, env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"lab python failed: {(proc.stderr or proc.stdout or '').strip()[:300]}")
        return json.loads((proc.stdout or "").strip().splitlines()[-1])

    def job_status(self, job_id: str) -> dict:
        return lab_jobs.runs_status(job_id)

    def job_log(self, job_id: str) -> str:
        """Whole stdout log of a job (runs_status does not report log_dir)."""
        return read_text(lab_jobs.jobs_dir() / job_id / "stdout.log")

    def cancel_job(self, job_id: str) -> dict:
        try:
            out = lab_jobs.runs_cancel(job_id)
        except (ValueError, OSError) as exc:
            out = {"cancelled": False, "error": str(exc)}
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                st = lab_jobs.runs_status(job_id)
            except ValueError:
                break
            if st.get("state") != "running":
                break
            time.sleep(2)
        return out

    # ================== S0 ================== #
    def s0(self, rec) -> None:
        def sh(cmd, timeout=60):
            p = run_cmd(cmd, timeout=timeout)
            return {"rc": p.returncode, "out": (p.stdout or "").strip(), "err": (p.stderr or "").strip()}

        osrel = {}
        for line in read_text(Path("/etc/os-release")).splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                osrel[k] = v.strip().strip('"')
        gpu = sh(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used",
                  "--format=csv,noheader,nounits"])
        parts = [p.strip() for p in (gpu["out"].splitlines() or [""])[0].split(",")]
        pyver = sh([str(REPO_PY), "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"])
        torch = sh([str(REPO_PY), "-c", "import torch;print(torch.__version__);print(torch.version.cuda)"])
        isaacsim = sh([str(REPO_PY), "-c", "import importlib.metadata as m;print(m.version('isaacsim'))"])
        uv = sh(["uv", "--version"])

        def git_head(path: Path) -> str | None:
            if not (path / ".git").exists():
                return None
            p = run_cmd(["git", "-C", str(path), "rev-parse", "HEAD"], timeout=30)
            return (p.stdout or "").strip() or None

        lab = {}
        try:
            lab = lab_install.install_info()
        except Exception as exc:  # noqa: BLE001
            lab = {"error": f"{type(exc).__name__}: {exc}"}

        data = {
            "generated_at": stamp(),
            "machine_arg": self.args.machine,
            "hostname": socket.gethostname(),
            "user": getpass.getuser(),
            "os": osrel.get("PRETTY_NAME"),
            "kernel": sh(["uname", "-r"])["out"],
            "arch": sh(["uname", "-m"])["out"],
            "gpu": parts[0] if parts else None,
            "driver": parts[1] if len(parts) > 1 else None,
            "vram_total_mib": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None,
            "vram_used_mib_at_start": int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None,
            "nvidia_smi": gpu["out"].splitlines(),
            "python": pyver["out"],
            "python_path": str(REPO_PY),
            "torch": (torch["out"].splitlines() or [None])[0],
            "torch_cuda": (torch["out"].splitlines() + [None, None])[1],
            "isaacsim": isaacsim["out"],
            "isaaclab": lab,
            "uv": uv["out"],
            "display": os.environ.get("DISPLAY"),
            "xdg_session_type": os.environ.get("XDG_SESSION_TYPE"),
            "git_isaac_mcp": git_head(REPO),
            "git_twin_repo": git_head(TWIN_REPO),
            "twin_repo": str(TWIN_REPO),
            "port": self.args.port,
            "out_dir": str(self.out),
            "disk_free_gb": sh(["df", "-BG", "--output=avail", str(REPO)])["out"].splitlines()[-1].strip(),
        }
        path = write_json(self.dirs["data"] / "machine.json", data)
        self.art(rec["id"], path)
        isaaclab_version = (lab or {}).get("isaaclab_version")
        self.metric(rec["id"],
                    hostname=data["hostname"], gpu=data["gpu"], driver=data["driver"],
                    vram_gb=round(data["vram_total_mib"] / 1024, 1) if data["vram_total_mib"] else None,
                    python=data["python"], isaacsim=data["isaacsim"] or "?",
                    isaaclab=isaaclab_version or "?", torch=data["torch"], display=data["display"],
                    isaac_mcp_git=(data["git_isaac_mcp"] or "")[:8])
        ok_versions = (data["isaacsim"] == "6.2.0.0") and (isaaclab_version == "3.0.0")
        self.check(rec["id"], "machine.json written", path.is_file())
        self.check(rec["id"], "isaacsim 6.2.0.0 and isaaclab 3.0.0", ok_versions,
                   isaacsim=data["isaacsim"], isaaclab=isaaclab_version)

    # ================== S1 ================== #
    def s1(self, rec) -> None:
        env = dict(os.environ)
        env["OMNI_KIT_ACCEPT_EULA"] = "YES"
        script = REPO / "scripts" / "check_machine.sh"
        proc = run_cmd(["bash", script], timeout=300, env=env)
        text = (proc.stdout or "") + (proc.stderr or "")
        out = self.dirs["logs"] / "check_machine.txt"
        out.write_text(text)
        self.art(rec["id"], out)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        last = lines[-1] if lines else ""
        self.metric(rec["id"], exit_code=proc.returncode, last_line=last,
                    fails=sum(1 for ln in lines if ln.startswith("FAIL")),
                    warns=sum(1 for ln in lines if ln.startswith("WARN")))
        self.check(rec["id"], 'ends with "Machine check passed."', last == "Machine check passed.", last_line=last)
        self.check(rec["id"], "exit code 0", proc.returncode == 0, exit_code=proc.returncode)

    def wait_kit_ready(self, deadline_s: float = 120) -> dict:
        """The bridge port opens before the stage exists; quick_start needs a live stage."""
        t0 = time.monotonic()
        last: dict = {}
        while time.monotonic() - t0 < deadline_s:
            try:
                out = bcall(self.args.port, "run_python", {"code": Sim.READY_CODE}, timeout=60)
                last = (out.get("result") if isinstance(out, dict) else None) or {}
            except Exception:  # noqa: BLE001 - the bridge may still be refusing calls
                last = {}
            if last.get("stage") and last.get("app_ready") is not False:
                time.sleep(1.0)
                return {"ready_in_s": round(time.monotonic() - t0, 1), **last}
            time.sleep(1.0)
        return {"ready_in_s": round(time.monotonic() - t0, 1), **last, "timeout": True}

    # ================== S2 ================== #
    def s2(self, rec) -> None:
        sid = rec["id"]
        self.sim = Sim(self.args.port, self.args.headless, self.out, self.dirs["logs"] / "sim.log")
        boot = self.sim.start()
        self.metric(sid, boot_seconds=round(boot, 1))
        self.art(sid, self.dirs["logs"] / "sim.log", "Kit stdout/stderr")
        ready = self.wait_kit_ready()
        self.metric(sid, kit_ready=ready)
        self.check(sid, "Kit stage is up (app ready)", bool(ready.get("stage")) and not ready.get("timeout"),
                   ready=ready)

        tools = bcall(self.args.port, "list_tools", timeout=600)
        tools = tools.get("tools", tools) if isinstance(tools, dict) else tools
        if not isinstance(tools, dict):
            tools = {str(i): {} for i in range(len(tools or []))}
        names = sorted(tools)
        ns: dict[str, int] = {}
        for name in names:
            prefix = name.split(".", 1)[0] if "." in name else "builtin"
            ns[prefix] = ns.get(prefix, 0) + 1
        write_json(self.dirs["data"] / "tools.json",
                   {"total": len(names), "per_namespace": ns, "tools": names,
                    "generated_at": stamp()})
        self.art(sid, self.dirs["data"] / "tools.json")
        self.metric(sid, tools_total=len(names), ur5e_dt_tools=ns.get("ur5e_dt", 0), namespaces=ns)
        # 30 / 23 as of the setup_camera_tf_graph tool (was 29 / 22); accept "at least" so adding tools does not break the demo.
        self.check(sid, "at least 30 bridge tools", len(names) >= 30, total=len(names))
        self.check(sid, "at least 23 ur5e_dt tools", ns.get("ur5e_dt", 0) >= 23, ur5e_dt=ns.get("ur5e_dt", 0))

        t0 = time.monotonic()
        qs = bcall(self.args.port, "ur5e_dt.quick_start_with_workstation", timeout=900)
        if qs.get("status") != "success":
            # one retry: the first call can land while the stage is still being created
            time.sleep(3)
            qs = bcall(self.args.port, "ur5e_dt.quick_start_with_workstation", timeout=900)
        self.metric(sid, quick_start_s=round(time.monotonic() - t0, 1), quick_start_status=qs.get("status"))
        self.check(sid, "quick_start_with_workstation succeeded", qs.get("status") == "success",
                   message=str(qs.get("message"))[:160])

        if self.args.headless:
            self.metric(sid, window_shot="skipped (headless)")
        else:
            win = None
            for _ in range(12):
                win = find_isaac_window(pid=self.sim.pid())
                if win:
                    break
                time.sleep(2)
            if not win:
                raise RuntimeError("no Isaac Sim window found after quick_start")
            self.ctx["sim_window"] = win
            self.metric(sid, sim_window=f"{win['title'][:40]} {win['width']}x{win['height']}")
            time.sleep(4)
            shot = self.dirs["shots"] / "S2-01-workstation-loaded.png"
            stats = capture_window(win, shot)
            self.art(sid, shot)
            self.metric(sid, shot_mean=stats["mean"], shot_nonblack=stats["nonblack_frac"])
            self.check(sid, "S2-01 shows a rendered viewport (not black)", stats["nonblack_frac"] > 0.2,
                       nonblack_frac=stats["nonblack_frac"], mean=stats["mean"])
            self.ctx["s2_shot"] = shot

        add = bcall(self.args.port, "ur5e_dt.add_objects", {"assembly": "fmb1"}, timeout=600)
        self.metric(sid, add_objects_status=add.get("status"))
        self.check(sid, "add_objects(fmb1)", add.get("status", "success") != "error",
                   status=str(add.get("status"))[:80])
        pose = bcall(self.args.port, "ur5e_dt.setup_pose_publisher", timeout=600)
        self.metric(sid, pose_publisher_status=pose.get("status"))
        self.check(sid, "setup_pose_publisher", pose.get("status", "success") != "error",
                   status=str(pose.get("status"))[:80])
        play = bcall(self.args.port, "ur5e_dt.play_scene", timeout=300)
        self.metric(sid, play_scene_status=play.get("status"))

        if not self.args.headless and self.ctx.get("sim_window"):
            time.sleep(5)
            shot = self.dirs["shots"] / "S2-02-objects-playing.png"
            stats = capture_window(self.ctx["sim_window"], shot)
            self.art(sid, shot)
            self.metric(sid, shot2_mean=stats["mean"], shot2_nonblack=stats["nonblack_frac"])
            self.check(sid, "S2-02 shows a rendered viewport (not black)", stats["nonblack_frac"] > 0.2,
                       nonblack_frac=stats["nonblack_frac"])
        self.ctx["sim_ready"] = True

    # ================== S3 ================== #
    # `/tf_cam` is published by the camera action graph. The twin creates that graph only from the
    # panel buttons (Import RealSense Camera -> Attach Camera to UR5e -> Setup Camera Action Graph;
    # there is no MCP tool for either step), so the runner drives the same methods through the
    # module-level `_EXT_INSTANCE` hook the extension documents for socket-driven build steps.
    CAMERA_TF_SETUP = r"""
from ur5e_dt import extension as E
import omni.usd

ext = E._EXT_INSTANCE
steps = {}
for name in ('import_realsense_camera', 'attach_camera_to_ur5e', 'setup_camera_action_graph'):
    try:
        getattr(ext, name)()
        steps[name] = 'ok'
    except Exception as exc:
        steps[name] = f'{type(exc).__name__}: {exc}'
stage = omni.usd.get_context().get_stage()
camera = '/World/UR5e/wrist_3_link/rsd455/RSD455/Camera_OmniVision_OV9782_Color'
steps['camera_prim'] = bool(stage.GetPrimAtPath(camera)) if stage is not None else None
steps['graph_prim'] = bool(stage.GetPrimAtPath('/Graph/ActionGraph_Camera')) if stage is not None else None
result = steps
"""

    def s3(self, rec) -> None:
        sid = rec["id"]
        if not self.ctx.get("sim_ready"):
            raise Skip("the sim (S2) is not running")
        # Preferred: the bridge tool (ur5e_dt.setup_camera_tf_graph, added after the first demo run).
        # Fallback for older extensions: the _EXT_INSTANCE workaround below.
        tool = {}
        try:
            tool = bcall(self.args.port, "ur5e_dt.setup_camera_tf_graph", {}, timeout=300) or {}
        except Exception as exc:  # noqa: BLE001
            tool = {"status": "error", "message": str(exc)}
        if tool.get("status") == "success":
            setup = bcall(self.args.port, "run_python", {"code": self.CAMERA_TF_SETUP.replace(
                "for name in ('import_realsense_camera', 'attach_camera_to_ur5e', 'setup_camera_action_graph'):",
                "for name in ():")}, timeout=120)
            self.metric(sid, camera_tool="ur5e_dt.setup_camera_tf_graph")
        else:
            setup = bcall(self.args.port, "run_python", {"code": self.CAMERA_TF_SETUP}, timeout=300)
        cam = (setup.get("result") if isinstance(setup, dict) else {}) or {}
        self.metric(sid, camera_setup=cam)
        if cam.get("graph_prim"):
            time.sleep(2)
        out = self.dirs["data"] / "tf.json"
        t0 = time.monotonic()
        proc = run_cmd(
            [str(REPO_PY), str(REPO / "scripts" / "tf_capture.py"),
             "--duration", "5", "--out", str(out)],
            timeout=180,
        )
        text = (proc.stdout or "") + (proc.stderr or "")
        log = self.dirs["logs"] / "tf_capture.txt"
        log.write_text(text)
        self.art(sid, log, "tf_capture output")
        self.metric(sid, seconds=round(time.monotonic() - t0, 1), exit_code=proc.returncode)
        if not out.exists():
            raise RuntimeError(f"tf_capture.py wrote no JSON: {text.strip()[:200]}")
        self.art(sid, out)
        data = json.loads(out.read_text())
        topics = data.get("topics", {})
        frames = {t: (len(v.get("frames") or {}), v.get("msg_count")) for t, v in topics.items()}
        self.metric(sid, **{f"{t}_frames": f[0] for t, f in frames.items()},
                    **{f"{t}_msgs": f[1] for t, f in frames.items()})
        self.check(sid, "objects_poses_sim >= 5 frames", frames.get("objects_poses_sim", (0, 0))[0] >= 5,
                   frames=frames.get("objects_poses_sim", (0, 0))[0])
        self.check(sid, "tf_cam >= 1 frame", frames.get("tf_cam", (0, 0))[0] >= 1,
                   frames=frames.get("tf_cam", (0, 0))[0])

    # ================== S4 ================== #
    SWEEP = r"""
import math, time
import numpy as np
import omni.kit.app, omni.timeline
from isaacsim.core.prims import Articulation
from isaacsim.core.utils.types import ArticulationActions

app = omni.kit.app.get_app()
timeline = omni.timeline.get_timeline_interface()
if not timeline.is_playing():
    timeline.play()
for _ in range(5):
    app.update()

art = Articulation('/World/UR5e', name='demo_sweep_%d' % int(time.time() * 1000))
art.initialize()
names = list(art.dof_names)
base = art.get_joint_positions()[0].copy()
picks = [n for n in ('shoulder_pan_joint', 'shoulder_lift_joint', 'elbow_joint') if n in names]
idx = {n: names.index(n) for n in picks}
sweep = [n for n in picks if n != 'shoulder_lift_joint']
start = time.monotonic()
frames = 0
while time.monotonic() - start < 10.0:
    t = time.monotonic() - start
    q = base.copy()
    for k, name in enumerate(sweep):
        q[idx[name]] = base[idx[name]] + 0.40 * math.sin(2 * math.pi * (0.18 + 0.06 * k) * t)
    if 'shoulder_lift_joint' in idx:
        # ramp (not a sine) so the last frame clearly differs from the first
        q[idx['shoulder_lift_joint']] = base[idx['shoulder_lift_joint']] + 0.45 * min(1.0, t / 9.5)
    art.apply_action(ArticulationActions(joint_positions=q))
    app.update()
    frames += 1
elapsed = time.monotonic() - start
end = art.get_joint_positions()[0].copy()
result = {
    'seconds': round(elapsed, 2),
    'frames': frames,
    'joints': picks,
    'delta_rad': {n: round(float(end[idx[n]] - base[idx[n]]), 4) for n in picks},
    'finite': bool(np.all(np.isfinite(end))),
}
"""

    def s4(self, rec) -> None:
        sid = rec["id"]
        if not self.ctx.get("sim_ready"):
            raise Skip("the sim (S2) is not running")
        if not self.ctx.get("sim_window"):
            raise Skip("no window to look at (headless sim)")
        win = self.ctx["sim_window"]
        before = self.dirs["shots"] / "S4-02-before-sweep.png"
        s1 = capture_window(win, before)
        self.art(sid, before)

        started = bcall(self.args.port, "ur5e_dt.start_recording",
                        {"file_name": "S4-01-joint-sweep.mp4", "fps": 30}, timeout=120)
        self.metric(sid, start_status=started.get("status"))
        self.check(sid, "start_recording accepted",
                   str(started.get("status", "")).lower() == "success", status=str(started.get("status")))
        if str(started.get("status", "")).lower() != "success":
            raise RuntimeError(f"start_recording refused: {started.get('message')}")

        t0 = time.monotonic()
        sweep = bcall(self.args.port, "run_python", {"code": self.SWEEP}, timeout=180)
        result = sweep.get("result") if isinstance(sweep, dict) else sweep
        self.metric(sid, sweep_seconds=(result or {}).get("seconds"),
                    sweep_frames=(result or {}).get("frames"),
                    sweep_joints=len((result or {}).get("joints") or []),
                    sweep_delta=(result or {}).get("delta_rad"))
        self.check(sid, "joints moved while recording",
                   bool(result) and any(abs(v) > 0.15 for v in (result.get("delta_rad") or {}).values()),
                   delta=(result or {}).get("delta_rad"))

        stopped = bcall(self.args.port, "ur5e_dt.stop_recording", timeout=300)
        self.metric(sid, stop_status=stopped.get("status"),
                    recorded_frames=stopped.get("frame_count"),
                    recorded_seconds=stopped.get("video_duration"),
                    stop_wall_s=round(time.monotonic() - t0, 1))
        after = self.dirs["shots"] / "S4-03-after-sweep.png"
        s2 = capture_window(win, after)
        self.art(sid, after)
        self.metric(sid, before=before.stat().st_size, after=after.stat().st_size)

        video = Path(str(stopped.get("file_path") or ""))
        if not video.is_file():
            # the extension writes into $MCP_CLIENT_OUTPUT_DIR/videos; accept a stray copy too
            guess = self.dirs["videos"] / "S4-01-joint-sweep.mp4"
            if guess.is_file():
                video = guess
        if not video.is_file():
            raise RuntimeError(f"stop_recording reported no usable file: {stopped}")
        self.art(sid, video)
        size = video.stat().st_size
        diff = image_diff(before, after)
        self.metric(sid, video_bytes=size, video_mb=round(size / 1e6, 2),
                    duration_s=ffprobe_duration(video), pose_diff_frac=diff["frac_changed"],
                    pose_diff_mean=diff["mean_abs"],
                    before_mean=s1["mean"], after_mean=s2["mean"])
        self.check(sid, "video > 50 KB", size > 50_000, bytes=size)
        self.check(sid, "robot in a different pose after the sweep",
                   diff["frac_changed"] > 0.005 and diff["mean_abs"] > 0.5,
                   frac_changed=diff["frac_changed"], mean_abs=diff["mean_abs"])
        self.check(sid, "still frames are rendered viewports",
                   s1["nonblack_frac"] > 0.2 and s2["nonblack_frac"] > 0.2,
                   nonblack=(s1["nonblack_frac"], s2["nonblack_frac"]))

    # ================== S5 ================== #
    def s5(self, rec) -> None:
        sid = rec["id"]
        python, root = self.resolve_lab()
        info = lab_install.install_info()
        write_json(self.dirs["data"] / "lab_install.json", info)
        self.art(sid, self.dirs["data"] / "lab_install.json")
        self.metric(sid, isaaclab_version=info.get("isaaclab_version"),
                    isaacsim_version=info.get("isaacsim_version"),
                    lab_python=python, lab_root=root)
        self.check(sid, "isaaclab 3.0.0", info.get("isaaclab_version") == "3.0.0",
                   found=info.get("isaaclab_version"))
        self.check(sid, "isaacsim 6.2.0.0", info.get("isaacsim_version") == "6.2.0.0",
                   found=info.get("isaacsim_version"))

        payload = {"generated_at": stamp(), "filters": {}}
        t0 = time.monotonic()
        for flt in ("ur5e", "reach"):
            res = lab_install.envs_list(filter=flt)
            payload["filters"][flt] = {
                "count": len(res.get("envs") or []),
                "envs": res.get("envs") or [],
                "import_errors": res.get("import_errors") or {},
            }
        path = write_json(self.dirs["data"] / "lab_envs.json", payload)
        self.art(sid, path)
        self.metric(sid, envs_seconds=round(time.monotonic() - t0, 1),
                    ur5e_envs=payload["filters"]["ur5e"]["count"],
                    reach_envs=payload["filters"]["reach"]["count"],
                    import_errors=payload["filters"]["reach"]["import_errors"] or None)
        ids = {e["id"] for f in payload["filters"].values() for e in f["envs"]}
        self.check(sid, f"{DEMO_TASK} listed", DEMO_TASK in ids, found=DEMO_TASK in ids)
        self.check(sid, "no import errors", not payload["filters"]["reach"]["import_errors"],
                   errors=payload["filters"]["reach"]["import_errors"] or None)

    # ================== S6 ================== #
    def s6_kit_guard(self, rec) -> None:
        """Refusal while a Kit app (the S2 sim) is listening on the twin port."""
        sid = rec["id"]
        if not self.ctx.get("sim_ready"):
            self.check(sid, "kit-bridge refusal (a Kit app is running)", False,
                       reason="sim not running; probed nothing")
            return
        python, root = self.resolve_lab()
        probe = lab_jobs.train(python, root, STOCK_TASK, num_envs=128, max_iterations=1,
                               headless=True, rl_library="rl_games")
        path = write_json(self.dirs["data"] / "S6-kit-guard.json",
                          {"probed_port": self.args.port, "response": probe})
        self.art(sid, path)
        ok = bool(probe.get("error")) and bool(probe.get("kit_bridges")) and not probe.get("job_id")
        refused_ok = ok and any(int(b.get("port", -1)) == self.args.port for b in probe.get("kit_bridges") or [])
        self.metric(sid, kit_guard_refused=ok,
                    kit_guard_ports=[b.get("port") for b in probe.get("kit_bridges") or []])
        self.check(sid, "Kit app blocks an Isaac Lab job (refused, nothing started)", ok,
                   error=str(probe.get("error"))[:120])
        self.check(sid, "refusal names the twin port", refused_ok, port=self.args.port)

    def s6_job_guard(self, rec) -> None:
        """Job-vs-job refusal, cancel, and guard release (sim is stopped by now)."""
        sid = rec["id"]
        python, root = self.resolve_lab()
        record = {"generated_at": stamp()}
        stock = dict(num_envs=128, headless=True, rl_library="rl_games",
                     extra_args=["agent.params.config.minibatch_size=1536"])
        first = lab_jobs.train(python, root, STOCK_TASK, max_iterations=10_000_000, **stock)
        record["first_job"] = first
        if not first.get("job_id"):
            raise RuntimeError(f"the stock job was not accepted: {first}")
        job1 = first["job_id"]
        self.ctx["jobs"] = self.ctx.get("jobs", []) + [job1]
        try:
            time.sleep(2)
            st = lab_jobs.runs_status(job1)
            record["first_status"] = {k: st.get(k) for k in ("state", "pid_alive", "elapsed_s")}
            second = lab_jobs.train(python, root, STOCK_TASK, max_iterations=1, **stock)
            record["second_job_refusal"] = second
            refused = bool(second.get("error")) and not second.get("job_id")
            named = second.get("running_job_id") == job1
            self.check(sid, "second job refused while one runs", refused, error=str(second.get("error"))[:120])
            self.check(sid, "refusal names the running job", named, running_job_id=second.get("running_job_id"))

            cancelled = self.cancel_job(job1)
            record["cancel"] = cancelled
            after_cancel = lab_jobs.runs_status(job1)
            record["after_cancel"] = {k: after_cancel.get(k) for k in ("state", "pid_alive")}
            self.check(sid, "cancel reports cancelled", bool(cancelled.get("cancelled")),
                       response=cancelled)
            self.check(sid, "job ends as cancelled (not failed)",
                       after_cancel.get("state") == "cancelled", state=after_cancel.get("state"))

            third = lab_jobs.train(python, root, STOCK_TASK, max_iterations=2, **stock)
            record["after_cancel_launch"] = third
            accepted = bool(third.get("job_id"))
            self.check(sid, "a new job is accepted after cancel", accepted,
                       job_id=third.get("job_id"), error=third.get("error"))
            if accepted:
                self.ctx["jobs"].append(third["job_id"])
                self.cancel_job(third["job_id"])
                record["cleanup_status"] = lab_jobs.runs_status(third["job_id"]).get("state")
        finally:
            st = lab_jobs.runs_status(job1)
            if st.get("state") == "running":
                self.cancel_job(job1)
            path = write_json(self.dirs["data"] / "S6-guard.json", record)
            self.art(sid, path)
            self.metric(sid, job1=job1,
                        job_guard_refused=bool(record.get("second_job_refusal", {}).get("error")),
                        cancel=record.get("cancel", {}),
                        state_after_cancel=record.get("after_cancel", {}).get("state"),
                        job_after_cancel=record.get("after_cancel_launch", {}).get("job_id"))
        # let the GPU go idle before training
        for _ in range(60):
            states = []
            for jid in self.ctx.get("jobs", []):
                try:
                    states.append(lab_jobs.runs_status(jid).get("state"))
                except ValueError:
                    states.append("gone")
            if "running" not in states:
                break
            time.sleep(3)
        self.metric(sid, other_jobs=len(self.ctx.get("jobs", [])))

    # ================== S7 ================== #
    def parse_training_log(self, text: str) -> dict:
        epochs = [(int(m.group(1)), int(m.group(2))) for m in re.finditer(r"epoch:\s*(\d+)/(\d+)", text)]
        fps = [float(m.group(1)) for m in re.finditer(r"fps total:\s*([\d.]+)", text)]
        return {
            "epoch": epochs[-1][0] if epochs else 0,
            "max_iterations": epochs[-1][1] if epochs else None,
            "epochs": len(epochs),
            "fps_total": fps[-1] if fps else None,
            "fps_avg": round(sum(fps) / len(fps), 1) if fps else None,
        }

    def sample_vram(self) -> int | None:
        p = run_cmd(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], timeout=30)
        try:
            return int((p.stdout or "").strip().splitlines()[0])
        except (ValueError, IndexError):
            return None

    def find_run_dir(self, job_id: str, text: str) -> str | None:
        """The rl_games experiment dir: runs_status only knows it once a checkpoint exists."""
        try:
            st = lab_jobs.runs_status(job_id)
        except ValueError:
            st = {}
        run_dir = st.get("run_dir")
        if run_dir and any(Path(run_dir).glob("**/events.out.tfevents.*")):
            return run_dir
        m = re.search(r"Exact experiment name requested from command line:\s*(/\S+)\s*$", text, re.M)
        if m and Path(m.group(1)).is_dir():
            return m.group(1)
        m = re.search(r"Logging experiment in directory:\s*(/\S+)\s*$", text, re.M)
        if m and Path(m.group(1)).is_dir():
            kids = sorted(Path(m.group(1)).iterdir(), key=lambda p: p.stat().st_mtime)
            if kids:
                return str(kids[-1])
        return None

    def watch_job(self, job_id: str, seconds: float, interval: float = 5.0,
                  vram: list | None = None) -> dict:
        """Watch a job for up to `seconds`, sampling epoch/fps from its log and VRAM every 30 s."""
        vram = [] if vram is None else vram
        obs: list[tuple[float, int]] = []
        t0 = time.monotonic()
        next_vram = t0
        info: dict = {}
        state = "running"
        while time.monotonic() - t0 < seconds:
            if time.monotonic() >= next_vram:
                used = self.sample_vram()
                if used is not None:
                    vram.append(used)
                next_vram = time.monotonic() + 30
            info = self.parse_training_log(self.job_log(job_id))
            if info["epoch"]:
                obs.append((time.monotonic() - t0, info["epoch"]))
            state = self.job_status(job_id).get("state")
            if state != "running":
                break
            time.sleep(interval)
        rate = None
        if len(obs) >= 2:
            (t_a, e_a), (t_b, e_b) = obs[0], obs[-1]
            if t_b - t_a > 5 and e_b > e_a:
                rate = (e_b - e_a) / (t_b - t_a)
        return {"state": state, "info": info, "rate": rate,
                "seconds": round(time.monotonic() - t0, 1)}

    def s7(self, rec) -> None:
        sid = rec["id"]
        python, root = self.resolve_lab()
        target = max(30.0, self.args.lab_minutes * 60.0)
        minibatch = f"agent.params.config.minibatch_size={self.args.num_envs * 12}"
        common = dict(task=DEMO_TASK, num_envs=self.args.num_envs, headless=True, rl_library="rl_games",
                      extra_args=[minibatch])

        # --- calibration launch: a long run, sampled briefly, then projected from the log ---
        cal_seconds = min(90.0, max(45.0, target * 0.5))
        cal = lab_jobs.train(python, root, max_iterations=3000, timeout_s=target * 1.4, **common)
        if not cal.get("job_id"):
            raise RuntimeError(f"training job refused: {cal}")
        job_id = cal["job_id"]
        self.ctx["jobs"] = self.ctx.get("jobs", []) + [job_id]
        vram: list[int] = []
        cal_watch = self.watch_job(job_id, cal_seconds, vram=vram)
        rate = cal_watch["rate"]
        projected = round(3000 / rate) if rate else None
        self.metric(sid, calibration_iterations=cal_watch["info"].get("epoch"),
                    calibration_rate_eps=round(rate, 3) if rate else None,
                    projection_for_3000_s=projected, target_s=round(target, 1),
                    calibration_seconds=cal_watch["seconds"])

        relaunch = (cal_watch["state"] == "running" and projected is not None
                    and abs(projected - target) > 0.25 * target)
        if relaunch:
            iterations = max(2, int(round(rate * target)))
            self.cancel_job(job_id)
            job = lab_jobs.train(python, root, max_iterations=iterations, timeout_s=target * 1.4, **common)
            if not job.get("job_id"):
                raise RuntimeError(f"calibrated training refused: {job}")
            job_id = job["job_id"]
            self.ctx["jobs"].append(job_id)
            vram = []
            iterations_planned = iterations
            self.metric(sid, calibrated=True, dropped_calibration_job=cal["job_id"])
        else:
            iterations_planned = 3000
            self.metric(sid, calibrated=False)
        self.metric(sid, job_id=job_id, max_iterations=iterations_planned)

        # --- the measured run ---
        t_run = time.monotonic()
        watch = self.watch_job(job_id, target * 1.5 + 180, vram=vram)
        wall = time.monotonic() - t_run
        st = self.job_status(job_id)
        text = self.job_log(job_id)
        log_copy = self.dirs["logs"] / "S7-train.log"
        log_copy.write_text(text)
        self.art(sid, log_copy, "job stdout")
        info = self.parse_training_log(text)
        vram_peak = max(vram) if vram else None
        self.metric(sid, state=st.get("state"), wall_s=round(wall, 1),
                    job_elapsed_s=st.get("elapsed_s"),
                    epochs=info["epoch"], planned_iterations=info.get("max_iterations"),
                    fps_last=info.get("fps_total"), fps_avg=info.get("fps_avg"),
                    peak_vram_mib=vram_peak, exit_code=st.get("exit_code"),
                    checkpoint=st.get("latest_checkpoint"))
        self.check(sid, "training job succeeded", st.get("state") == "succeeded",
                   state=st.get("state"), exit_code=st.get("exit_code"),
                   last_lines=st.get("last_log_lines")[-3:] if st.get("last_log_lines") else None)

        run_dir = self.find_run_dir(job_id, text)
        self.metric(sid, run_dir=run_dir)
        if st.get("latest_checkpoint"):
            self.ctx["checkpoint"] = st["latest_checkpoint"]
        # metrics need tensorboard, which the root .venv lacks: read them in the Lab interpreter
        scalars: dict = {}
        if run_dir:
            try:
                data = self.lab_json(
                    "import json\n"
                    "from isaac_mcp.isaaclab import metrics\n"
                    f"d = metrics.metrics_summary({run_dir!r})\n"
                    "print(json.dumps(d, default=str))\n",
                    timeout=180,
                )
                scalars = data.get("scalars") or {}
                write_json(self.dirs["data"] / "S7-metrics.json", data)
                self.art(sid, self.dirs["data"] / "S7-metrics.json")
            except Exception as exc:  # noqa: BLE001 - metrics are evidence, not a pass criterion
                self.metric(sid, metrics_error=f"{type(exc).__name__}: {exc}")
        reward = scalars.get("rewards/iter") or {}
        success = scalars.get("Episode/success_rate") or {}
        distance = scalars.get("Episode/dist_m") or {}
        self.metric(sid,
                    reward_min=round(reward["min"], 2) if reward else None,
                    reward_last=round(reward["last"], 2) if reward else None,
                    success_last=round(success["last"], 3) if success else None,
                    dist_m_last=round(distance["last"], 3) if distance else None)
        if reward:
            self.check(sid, "reward rises during the run", reward["last"] > reward["min"],
                       first=round(reward["min"], 2), last=round(reward["last"], 2))

        plotter = Path(__file__).with_name("plot_curves.py")
        if plotter.is_file() and run_dir:
            proc = run_cmd([python, str(plotter), run_dir, str(self.dirs["shots"])], timeout=900)
            (self.dirs["logs"] / "S7-plot_curves.txt").write_text(
                (proc.stdout or "") + (proc.stderr or ""))
            plots = sorted(p for p in self.dirs["shots"].glob("S7-*.png"))
            for p in plots:
                self.art(sid, p)
            self.check(sid, "curve plot(s) written", bool(plots), files=[p.name for p in plots],
                       exit_code=proc.returncode,
                       error=(proc.stderr or "")[-300:] if proc.returncode else None)
        else:
            self.metric(sid, curves="skipped: scripts/demo/plot_curves.py not present")

    # ================== S8 ================== #
    def s8(self, rec) -> None:
        sid = rec["id"]
        ckpt = self.ctx.get("checkpoint")
        if not ckpt:
            raise Skip("no checkpoint from S7")
        if not Path(ckpt).is_file():
            raise Skip(f"checkpoint path is not a file: {ckpt}")
        if self.args.headless:
            raise Skip("--headless: a windowed play job needs a display")
        python, root = self.resolve_lab()
        play = lab_jobs.play(python, root, DEMO_TASK, ckpt, num_envs=4,
                             headless=False, rl_library="rl_games",
                             extra_args=["agent.params.config.minibatch_size=96"])
        if not play.get("job_id"):
            raise RuntimeError(f"play job refused: {play}")
        job_id = play["job_id"]
        self.ctx.setdefault("jobs", []).append(job_id)
        self.ctx["play_job"] = job_id
        self.metric(sid, job_id=job_id, checkpoint=str(ckpt))

        # window first, then the log line, then 8 s of settling
        win = None
        ready_line = False
        deadline = time.monotonic() + 240
        t0 = time.monotonic()
        while time.monotonic() < deadline:
            st = self.job_status(job_id)
            if st.get("state") != "running":
                raise RuntimeError(f"play job ended early: {st.get('state')} "
                                   f"({st.get('last_log_lines')})")
            if win is None:
                win = find_window("isaac lab", pid=st.get("pid"))
            if not ready_line:
                ready_line = "Completed setting up the environment" in self.job_log(job_id)
            if win and ready_line:
                break
            time.sleep(3)
        self.metric(sid, window_after_s=round(time.monotonic() - t0, 1),
                    log_line_seen=ready_line)
        self.check(sid, "Isaac Lab window appeared", win is not None,
                   window=f"{(win or {}).get('title')} {(win or {}).get('width')}x{(win or {}).get('height')}")
        self.check(sid, "'Completed setting up the environment' in the log", ready_line)
        if win is None:
            raise RuntimeError("no Isaac Lab window for the play job")
        self.ctx["play_window"] = win
        time.sleep(8)

        st = self.job_status(job_id)
        st["window"] = {k: win[k] for k in ("id", "title", "width", "height") if k in win}
        write_json(self.dirs["data"] / "window.json", st)
        self.art(sid, self.dirs["data"] / "window.json", "runs_status + window")
        self.metric(sid, window_id=int(st["window"]["id"]),
                    window_title=st["window"]["title"][:40])

        shot = self.dirs["shots"] / "S8-01-play-window.png"
        stats = capture_window(win, shot)
        self.art(sid, shot)
        self.metric(sid, shot_mean=stats["mean"], shot_nonblack=stats["nonblack_frac"],
                    shot_size=f"{stats['width']}x{stats['height']}")
        self.check(sid, "screenshot is a rendered viewport (not black)", stats["nonblack_frac"] > 0.2,
                   nonblack_frac=stats["nonblack_frac"])

        video = self.dirs["videos"] / "S8-02-play.mp4"
        rec_info = record_window(win, video, seconds=self.args.play_seconds, fps=10)
        self.art(sid, video)
        self.metric(sid, video_bytes=rec_info["bytes"], video_frames=rec_info["frames"],
                    video_fps=rec_info["video_fps"], video_seconds=rec_info["capture_seconds"],
                    video_duration=ffprobe_duration(video))
        self.check(sid, "play video written", video.is_file() and rec_info["bytes"] > 50_000,
                   bytes=rec_info["bytes"])
        # the play job stays up on purpose: S9 needs a live Isaac window. Cleanup cancels it.

    # ================== S9 ================== #
    def describe_window(self, wid: int) -> dict:
        """geometry/map state/pid of `wid` plus a title taken from its subtree (frames have none)."""
        wins = {w["id"]: w for w in xwin_tree()}
        info = window_details(wid)
        entry = wins.get(wid, {})
        title = entry.get("title") or ""
        if not title:
            # a WM frame carries no name; its client child does
            for w in wins.values():
                if w["parent"] == wid and w["title"]:
                    title = w["title"]
                    break
        return {
            "id": wid,
            "id_hex": hex(wid),
            "title": title,
            "class": entry.get("class", ""),
            "x": info["x"] if info["x"] is not None else entry.get("x", 0),
            "y": info["y"] if info["y"] is not None else entry.get("y", 0),
            "width": info["width"] or entry.get("width", 0),
            "height": info["height"] or entry.get("height", 0),
            "viewable": info["viewable"],
            "pid": info["pid"],
        }

    def cover_above(self, target: dict) -> dict | None:
        """The nearest window stacked above `target` that covers most of it."""
        order = stacking_order()
        top = top_level_window(target["id"])
        if top not in order:
            return None
        idx = order.index(top)
        tx, ty, tw, th = target["x"], target["y"], target["width"], target["height"]
        for above in order[idx + 1:]:
            win = self.describe_window(above)
            if not win["viewable"] or not win["width"] or not win["height"]:
                continue
            if win["pid"] == target.get("pid"):
                continue
            ox = min(tx + tw, win["x"] + win["width"]) - max(tx, win["x"])
            oy = min(ty + th, win["y"] + win["height"]) - max(ty, win["y"])
            if max(0, ox) * max(0, oy) >= 0.5 * tw * th:
                win["stack_index"] = order.index(above)
                return win
        return None

    def start_cover_window(self, target: dict) -> subprocess.Popen | None:
        """Synthetic solid cover (xmessage: a plain X client that paints on map)."""
        exe = shutil.which("xmessage")
        if not exe:
            return None
        geom = f"{target['width']}x{target['height']}+{max(0, target['x'])}+{max(0, target['y'])}"
        proc = subprocess.Popen(
            [exe, "-title", "demo-cover", "-geometry", geom, "-bg", "#c0197a", "-fg", "white",
             "covering window (S9)"],
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return None
            if find_window("demo-cover"):
                return proc
            time.sleep(0.5)
        proc.terminate()
        return None

    def s9(self, rec) -> None:
        sid = rec["id"]
        target = self.ctx.get("play_window") or self.ctx.get("sim_window")
        if not target:
            raise Skip("no live Isaac window (S2/S8 did not produce one)")
        win = find_isaac_window(pid=target.get("pid")) or target
        win_top = top_level_window(win["id"])
        uncovered = self.dirs["shots"] / "S9-00-uncovered.png"
        capture_window(win, uncovered)

        # a window that is above the Isaac window in the X stacking order and overlaps it
        cover = self.cover_above(win)
        created = None
        if cover is None:
            created = self.start_cover_window(win)
            if created is not None:
                time.sleep(2.0)
                cover = self.cover_above(win)
                if cover is None:
                    found = find_window("demo-cover")
                    cover = self.describe_window(found["id"]) if found else None
        try:
            if cover is None:
                raise Skip("no covering window: nothing is stacked above the Isaac window and a "
                           "synthetic xmessage cover could not be placed above it")
            covered_png = self.dirs["shots"] / "S9-01-covered-window.png"
            cover_png = self.dirs["shots"] / "S9-02-covering-window.png"
            covered = capture_window(win, covered_png)
            cover_stats = capture_window(cover, cover_png)
        finally:
            if created is not None:
                try:
                    created.terminate()
                    created.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    created.kill()

        self.art(sid, covered_png, "Isaac window captured while covered")
        self.art(sid, cover_png, "the window over the same rectangle")
        self.art(sid, uncovered, "Isaac window before covering")
        order = stacking_order()
        cover_above = (cover["id"] in order and win_top in order
                       and order.index(cover["id"]) > order.index(win_top))
        self.metric(sid, target=f"{win['title'][:36]} {win['width']}x{win['height']}",
                    covering_window=f"{cover['title'][:30] or cover['id_hex']} "
                                    f"{cover['width']}x{cover['height']}",
                    stacked_above=cover_above,
                    stack_index_cover=order.index(cover["id"]) if cover["id"] in order else None,
                    stack_index_isaac=order.index(win_top) if win_top in order else None,
                    cover_nonblack=cover_stats["nonblack_frac"], cover_mean=cover_stats["mean"])
        self.check(sid, "a window overlaps the Isaac window from the X stacking order above it",
                   cover_above, covering_window=cover["title"][:40], stacked_above=cover_above,
                   basis="xwininfo -root -tree (XQueryTree) order")
        self.check(sid, "the covering window is really painted (not an unpainted shell)",
                   cover_stats["std"] > 5, mean=cover_stats["mean"], std=cover_stats["std"])

        stats = image_stats(covered_png)
        d_cover = image_diff(covered_png, cover_png)
        d_self = image_diff(covered_png, uncovered)
        d_ref = image_diff(covered_png, self.ctx["s2_shot"]) if self.ctx.get("s2_shot") else None
        self.metric(sid, frac_changed_vs_cover=d_cover["frac_changed"],
                    frac_changed_vs_uncovered=d_self["frac_changed"],
                    frac_changed_vs_S2=d_ref["frac_changed"] if d_ref else None,
                    covered_mean=stats["mean"], covered_nonblack=stats["nonblack_frac"])
        self.check(sid, "capture is the Isaac window, not the covering window",
                   d_cover["frac_changed"] > 0.5, frac_changed=d_cover["frac_changed"],
                   mean_abs=d_cover["mean_abs"])
        self.check(sid, "capture is still the Isaac scene from before covering",
                   d_self["frac_changed"] < 0.6, frac_changed=d_self["frac_changed"])
        self.check(sid, "capture is a rendered viewport", stats["nonblack_frac"] > 0.2,
                   nonblack_frac=stats["nonblack_frac"])

    # ================== report ================== #
    def cleanup(self) -> dict:
        info = {}
        if self.sim and self.sim.proc:
            try:
                info["sim"] = self.sim.stop()
            except Exception as exc:  # noqa: BLE001
                info["sim"] = {"error": f"{type(exc).__name__}: {exc}"}
        for jid in list(self.ctx.get("jobs", [])):
            try:
                st = lab_jobs.runs_status(jid)
            except ValueError:
                continue
            if st.get("state") == "running":
                try:
                    self.cancel_job(jid)
                    info.setdefault("cancelled", []).append(jid)
                except Exception as exc:  # noqa: BLE001
                    info.setdefault("cancel_errors", []).append(f"{jid}: {exc}")
        info["port_open_after"] = port_open(self.args.port)
        return info

    def write_report(self, cleanup: dict) -> tuple[Path, Path]:
        finished = now()
        order = [s for s in IDS if self.steps[s]["status"] != "pending"]
        summary = {
            "pass": sum(1 for s in order if self.steps[s]["status"] == "pass"),
            "fail": sum(1 for s in order if self.steps[s]["status"] == "fail"),
            "skipped": sum(1 for s in order if self.steps[s]["status"] == "skipped"),
        }
        payload = {
            "machine": self.args.machine,
            "hostname": socket.gethostname(),
            "started_at": stamp(self.started),
            "finished_at": stamp(finished),
            "wall_seconds": round(finished - self.started, 1),
            "args": vars(self.args),
            "summary": summary,
            "cleanup": cleanup,
            "steps": [self.steps[s] for s in IDS],
        }
        json_path = write_json(self.out / "report.json", payload)
        md = self.render_md(payload)
        md_path = self.out / "report.md"
        md_path.write_text(md)
        return json_path, md_path

    def render_md(self, payload: dict) -> str:
        lines = [
            f"# Demo run — {payload['machine']} ({payload['hostname']})",
            "",
            f"Started {payload['started_at']}, finished {payload['finished_at']} "
            f"({payload['wall_seconds']} s wall).",
            f"Port {payload['args']['port']}, lab minutes {payload['args']['lab_minutes']}, "
            f"num_envs {payload['args']['num_envs']}, headless {payload['args']['headless']}.",
            "",
            f"**{payload['summary']['pass']} pass, {payload['summary']['fail']} fail, "
            f"{payload['summary']['skipped']} skipped.**",
            "",
            "| Id | Scenario | Result | Seconds | Note |",
            "|---|---|---|---|---|",
        ]
        for rec in payload["steps"]:
            if rec["status"] == "pending":
                continue
            lines.append(
                f"| {rec['id']} | {rec['name']} | {rec['status'].upper()} | {rec['seconds']:.1f} | "
                f"{self._md_cell(rec['note'])} |"
            )
        for rec in payload["steps"]:
            if rec["status"] == "pending":
                continue
            lines += ["", f"## {rec['id']} — {rec['name']}: {rec['status'].upper()}", ""]
            if rec["note"]:
                lines += [f"Note: {rec['note']}", ""]
            if rec["metrics"]:
                lines += ["| Key | Value |", "|---|---|"]
                for k, v in rec["metrics"].items():
                    lines.append(f"| {k} | {self._md_cell(self._fmt(v))} |")
                lines.append("")
            if rec["checks"]:
                lines += ["| Check | Result |", "|---|---|"]
                for c in rec["checks"]:
                    detail = {k: v for k, v in c.items() if k not in ("check", "ok")}
                    suffix = f" — {self._fmt(detail)}" if detail else ""
                    lines.append(f"| {c['check']} | {'PASS' if c['ok'] else 'FAIL'}{self._md_cell(suffix)} |")
                lines.append("")
            if rec["errors"]:
                lines += ["Errors:", *[f"- `{e}`" for e in rec["errors"]], ""]
            if rec["artifacts"]:
                links = []
                for a in rec["artifacts"]:
                    path, _, label = a.partition(" (")
                    label = label.rstrip(")") if label else Path(path).name
                    links.append(f"[{label}]({path})")
                lines += ["Artifacts: " + ", ".join(links), ""]
        lines += ["", "## Cleanup", "", f"```\n{json.dumps(payload['cleanup'], indent=2, default=str)}\n```", ""]
        return "\n".join(lines)

    @staticmethod
    def _md_cell(text) -> str:
        return str(text).replace("|", "\\|").replace("\n", " ")

    @staticmethod
    def _fmt(value) -> str:
        if isinstance(value, float):
            return f"{value:g}"
        if isinstance(value, dict):
            return json.dumps(value, default=str)
        return str(value)

    # ================== main ================== #
    def run(self) -> int:
        self.prepare()
        print(f"demo run: machine={self.args.machine} out={self.out} port={self.args.port} "
              f"lab_minutes={self.args.lab_minutes} num_envs={self.args.num_envs}", flush=True)

        self.phase("S0", self.s0)
        self.finish_step("S0")
        self.phase("S1", self.s1)
        self.finish_step("S1")

        if self.args.skip_sim:
            for sid in ("S2", "S3", "S4"):
                self.skip(sid, "--skip-sim")
        else:
            try:
                self.phase("S2", self.s2)
            finally:
                self.finish_step("S2")
            if self.steps["S2"]["ok"]:
                self.phase("S3", self.s3)
                self.finish_step("S3")
                self.phase("S4", self.s4)
                self.finish_step("S4")
            else:
                self.skip("S3", "S2 failed")
                self.skip("S4", "S2 failed")
            # S6's Kit-guard check needs the sim alive; the rest of S6 runs on an idle GPU
            self.phase("S6", self.s6_kit_guard)
        # free the GPU before any Isaac Lab job
        if self.sim:
            cleanup = self.sim.stop()
            self.metric("S2", sim_stop=cleanup)
            print(f"[sim] stopped pid {cleanup.get('pid')} "
                  f"(port closed in {cleanup.get('port_closed_in_s')} s)", flush=True)

        if self.args.skip_lab:
            for sid in ("S5", "S6", "S7", "S8", "S9"):
                if self.steps[sid]["status"] == "pending":
                    self.skip(sid, "--skip-lab")
        else:
            self.phase("S5", self.s5)
            self.finish_step("S5")
            if not self.ctx.get("sim_ready"):
                self.metric("S6", kit_guard="not tested: no Kit app was running (--skip-sim or S2 failed)")
            self.phase("S6", self.s6_job_guard)
            self.finish_step("S6")
            if self.steps["S5"]["ok"]:
                self.phase("S7", self.s7)
                self.finish_step("S7")
            else:
                self.skip("S7", "S5 failed")
            if self.steps["S7"]["ok"]:
                self.phase("S8", self.s8)
                self.finish_step("S8")
            else:
                self.skip("S8", "S7 failed")
            if self.ctx.get("play_window") or self.ctx.get("sim_window"):
                if self.args.skip_sim and not self.ctx.get("play_window"):
                    self.skip("S9", "--skip-sim and no play window")
                else:
                    self.phase("S9", self.s9)
                    self.finish_step("S9")
            else:
                self.skip("S9", "no Isaac window (S8 skipped or failed)")

        cleanup = self.cleanup()
        json_path, md_path = self.write_report(cleanup)
        print(f"\nreport: {json_path}\n        {md_path}", flush=True)
        summary = self.steps
        failed = [sid for sid in IDS if summary[sid]["status"] == "fail"]
        skipped = [sid for sid in IDS if summary[sid]["status"] == "skipped"]
        print(f"summary: {sum(1 for s in IDS if summary[s]['status'] == 'pass')} pass, "
              f"{len(failed)} fail{(' (' + ', '.join(failed) + ')') if failed else ''}, "
              f"{len(skipped)} skipped"
              f"{(' (' + ', '.join(skipped) + ')') if skipped else ''}", flush=True)
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run the S0-S9 full-system demo/test plan and write demo-out/ evidence.",
    )
    p.add_argument("--machine", required=True, help="machine id (asu, home5080)")
    p.add_argument("--out", default=None, help="output directory (default demo-out/<machine>)")
    p.add_argument("--port", type=int, default=8791, help="twin bridge port (default 8791)")
    p.add_argument("--lab-minutes", type=float, default=5.0, help="training wall time (default 5)")
    p.add_argument("--num-envs", type=int, default=1024, help="training envs (default 1024)")
    p.add_argument("--play-seconds", type=float, default=15.0, help="S8 video length (default 15)")
    p.add_argument("--skip-sim", action="store_true", help="skip S2-S4 (no Isaac Sim)")
    p.add_argument("--skip-lab", action="store_true", help="skip S5-S9 (no Isaac Lab)")
    p.add_argument("--headless", action="store_true", help="run the sim without a window")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.out = args.out or str(Path("demo-out") / args.machine)
    if args.lab_minutes <= 0:
        print("--lab-minutes must be positive", file=sys.stderr)
        return 2
    runner = Runner(args)
    try:
        return runner.run()
    except BaseException:  # noqa: BLE001 - a crash is the one non-zero exit
        traceback.print_exc()
        try:
            runner.cleanup()
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# Reference: Universal Robots Dashboard Server protocol
#   https://www.universal-robots.com/articles/ur/dashboard-server-e-series-port-29999/
"""Raw-TCP client for the UR / URSim Dashboard Server (port 29999).

Unlike ros-mcp-server/utils/ursim_cli.py (which talks to the dashboard via the
ur_robot_driver's ROS2 services), this client speaks the dashboard wire protocol
directly. That matters for sim bring-up: the dashboard server is part of URSim
itself and is reachable the moment the container boots — BEFORE any ROS driver
exists. This breaks the chicken-and-egg where you can't power on the robot
because the thing that powers it on only comes up with the driver.

Usage:
  ur_dashboard.py [--host H] [--port 29999] [--timeout S] CMD [CMD ...]

Single commands: power_on power_off brake_release stop pause play
                 mode safety running state loaded remote
                 load <path>   raw <text>
Combos:          power_up                  (power on -> wait -> brake release -> wait RUNNING)
                 start_external [urp_path]  (load -> play -> confirm running)
                 status                     (mode + safety + program)
                 wait_dashboard             (block until server answers)

Exit code is non-zero if any step fails. Designed to be called from a shell
orchestration script (see sim_bringup.sh).
"""

import argparse
import socket
import sys
import time

DEFAULT_URP = "/ursim/programs/external_control.urp"


class DashboardClient:
    def __init__(self, host="127.0.0.1", port=29999, timeout=5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        return self._recv()  # greeting line

    def _recv(self):
        """Read one newline-terminated response."""
        buf = b""
        while b"\n" not in buf:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
        return buf.decode(errors="replace").strip()

    def send(self, command):
        self.sock.sendall((command + "\n").encode())
        return self._recv()

    def close(self):
        if self.sock:
            try:
                self.sock.sendall(b"quit\n")
            except OSError:
                pass
            self.sock.close()
            self.sock = None

    # --- polling helpers -------------------------------------------------
    def poll(self, command, predicate, attempts=20, interval=1.0):
        last = ""
        for _ in range(attempts):
            last = self.send(command)
            if predicate(last):
                return True, last
            time.sleep(interval)
        return False, last

    def power_up(self, attempts=45, interval=1.0):
        """Drive POWER_OFF -> RUNNING as a state machine.

        A freshly-booted URSim controller silently drops an early 'power on',
        so we re-issue the right command for the current mode each tick rather
        than firing once: re-send 'power on' while POWER_OFF, 'brake release'
        once IDLE, and succeed at RUNNING.
        """
        last = ""
        for _ in range(attempts):
            last = self.send("robotmode")
            if "RUNNING" in last:
                return True, "robot powered on, brakes released (RUNNING)"
            if "IDLE" in last:
                self.send("brake release")
            elif "POWER_OFF" in last:
                self.send("power on")
            # BOOTING / POWER_ON: just wait for the next tick
            time.sleep(interval)
        return False, f"robot never reached RUNNING (last: {last})"

    def start_external(self, urp=DEFAULT_URP):
        """load external_control program -> play -> confirm running."""
        self.send("stop")
        time.sleep(0.5)
        r = self.send(f"load {urp}")
        if "Loading program" not in r and "Loaded" not in r:
            return False, f"load failed: {r}"
        time.sleep(0.5)
        r = self.send("play")
        if "Starting program" not in r:
            return False, f"play failed: {r}"
        ok, last = self.poll("running", lambda x: "true" in x.lower(), attempts=10)
        if not ok:
            return False, f"program not running after play (last: {last})"
        return True, "external_control.urp playing"


def wait_dashboard(host, port, timeout_s):
    """Block until the dashboard server accepts a connection and greets us."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            c = DashboardClient(host, port, timeout=2.0)
            greet = c.connect()
            c.close()
            if "Dashboard Server" in greet:
                return True
        except OSError:
            pass
        time.sleep(1.0)
    return False


SIMPLE = {
    "power_on": "power on",
    "power_off": "power off",
    "brake_release": "brake release",
    "stop": "stop",
    "pause": "pause",
    "play": "play",
    "mode": "robotmode",
    "safety": "safetystatus",
    "running": "running",
    "state": "programState",
    "loaded": "get loaded program",
    "remote": "is in remote control",
}


def main():
    ap = argparse.ArgumentParser(description="UR/URSim Dashboard Server raw-TCP client")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=29999)
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--wait", type=float, default=0.0,
                    metavar="SECONDS",
                    help="wait up to SECONDS for the dashboard server before running commands")
    ap.add_argument("commands", nargs=argparse.REMAINDER,
                    help="dashboard commands / combos (see module docstring)")
    args = ap.parse_args()

    if args.wait > 0 or (args.commands and args.commands[0] == "wait_dashboard"):
        secs = args.wait if args.wait > 0 else 90.0
        if not wait_dashboard(args.host, args.port, secs):
            print(f"  x dashboard server not up at {args.host}:{args.port} after {secs}s", file=sys.stderr)
            sys.exit(1)
        print(f"  - dashboard server up at {args.host}:{args.port}")
        if args.commands and args.commands[0] == "wait_dashboard":
            args.commands = args.commands[1:]

    if not args.commands:
        return

    client = DashboardClient(args.host, args.port, args.timeout)
    try:
        client.connect()
    except OSError as e:
        print(f"  x cannot connect to dashboard {args.host}:{args.port}: {e}", file=sys.stderr)
        sys.exit(1)

    rc = 0
    i = 0
    cmds = args.commands
    try:
        while i < len(cmds):
            c = cmds[i]
            if c == "power_up":
                ok, msg = client.power_up()
            elif c == "start_external":
                urp = DEFAULT_URP
                if i + 1 < len(cmds) and not _is_keyword(cmds[i + 1]):
                    urp = cmds[i + 1]
                    i += 1
                ok, msg = client.start_external(urp)
            elif c == "status":
                ok, msg = True, " | ".join(client.send(SIMPLE[k]) for k in ("mode", "safety", "loaded"))
            elif c == "load":
                i += 1
                resp = client.send(f"load {cmds[i]}")
                ok = "Loading program" in resp or "Loaded" in resp
                msg = resp
            elif c == "raw":
                i += 1
                msg = client.send(cmds[i])
                ok = True
            elif c in SIMPLE:
                msg = client.send(SIMPLE[c])
                ok = "false" not in msg.lower() if c == "remote" else True
                ok = True  # plain queries/triggers: report response, don't fail
                msg = msg
            else:
                ok, msg = False, f"unknown command: {c}"
            mark = "-" if ok else "x"
            print(f"  {mark} {c}: {msg}")
            if not ok:
                rc = 1
                break
            i += 1
    finally:
        client.close()
    sys.exit(rc)


def _is_keyword(tok):
    return tok in SIMPLE or tok in {"power_up", "start_external", "status", "load", "raw", "wait_dashboard"}


if __name__ == "__main__":
    main()

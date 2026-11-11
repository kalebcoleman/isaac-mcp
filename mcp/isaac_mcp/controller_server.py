"""A controller as its own process, for sim__simulation__run(controller="tcp://127.0.0.1:<port>").

The simulator sends one JSON line per physics step with the body's measured state and reads one
JSON line back with the wrench to apply for that step:

    state    {"step", "time_s", "dt", "position", "orientation_wxyz", "linear_velocity",
              "angular_velocity", "mass", "params"}
    command  {"force": [x, y, z], "torque": [x, y, z], "frame": "world" | "body",
              "done": false, "log": {"name": number}}        (every key optional; null = no wrench)

Stdlib only, so the file can be copied next to a controller or imported from an isaac-mcp install:

    from isaac_mcp.controller_server import serve

    def hover(state):
        error = 1.0 - state["position"][2]
        return {"force": [0, 0, state["mass"] * (9.81 + 4 * error - 3 * state["linear_velocity"][2])]}

    serve(hover, port=9123)     # start it with run_script, then call sim__simulation__run

Any language can speak the same two lines; nothing here is needed for that.
"""

from __future__ import annotations

import json
import socket
from typing import Any, Callable, Optional


def serve(controller: Callable[[dict], Optional[dict]], port: int, host: str = "127.0.0.1",
          runs: Optional[int] = 1, ready: Optional[Callable[[int], Any]] = None) -> None:
    """Answer ``runs`` simulator connections one after another (None: until killed), calling
    ``controller(state)`` for every line. ``ready(port)`` is called once the socket listens;
    port 0 picks a free port."""
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(1)
        if ready is not None:
            ready(listener.getsockname()[1])
        else:
            print(f"controller listening on {host}:{listener.getsockname()[1]}", flush=True)
        served = 0
        while runs is None or served < runs:
            connection, _ = listener.accept()
            served += 1
            with connection, connection.makefile("rb") as reader:
                for line in reader:
                    command = controller(json.loads(line))
                    connection.sendall(json.dumps(command).encode() + b"\n")

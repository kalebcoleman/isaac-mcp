#!/usr/bin/env python3
"""Drive a Kit app on another machine: copy a tool package to it, load it, call it.

Tools run on the machine the bridge is on, so the package and its assets must be on that
machine's disk. The bridge's file tools put them there: ``stat_file`` reports a remote file's
hash, ``put_file`` writes one, ``reload_tools`` imports the package, and ``get_file`` brings a
result back. ``sync`` below copies only files whose hash differs, so it is cheap to run before
every session.

A file travels in one message, held in memory at both ends: fine for code and small assets. Move
multi-gigabyte scenes another way, and still check them here with ``stat_file``.

Copy this file and set the five names below. The bridge listens on localhost only unless it was
started with ``ISAAC_MCP_BIND``; from another machine pass its token as ``ISAAC_MCP_TOKEN``.
"""

import base64
import hashlib
import sys
from pathlib import Path

from isaac_mcp.client import BridgeClient

HOST = None                          # None: the bridge advertised on this machine
PORT = 8766
LOCAL = Path(__file__).parent        # holds <PACKAGE>/ and assets/
REMOTE = "/srv/myproject"            # where the other machine keeps them
PACKAGE = "demo_tools"
NAMESPACE = "demo"               # the package's MCP_NAMESPACE
COPIED = (".py", ".usd", ".usda", ".json", ".yaml")


def sync(bridge: BridgeClient, folder: Path, remote: str) -> list:
    """Make ``remote`` hold the files of ``folder``. Returns the remote paths that were written."""
    written = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix not in COPIED or "__pycache__" in path.parts:
            continue
        target = f"{remote}/{path.relative_to(folder).as_posix()}"
        data = path.read_bytes()
        there = bridge.call("stat_file", {"path": target})
        if not there.get("exists") or there.get("sha256") != hashlib.sha256(data).hexdigest():
            bridge.call("put_file", {"path": target, "content_b64": base64.b64encode(data).decode()})
            written.append(target)
    return written


def load(bridge: BridgeClient) -> None:
    """Bring the remote copy up to date, and (re)load the package if its code changed or it is not loaded."""
    written = sync(bridge, LOCAL / PACKAGE, f"{REMOTE}/{PACKAGE}")
    if (LOCAL / "assets").is_dir():
        written += sync(bridge, LOCAL / "assets", f"{REMOTE}/assets")
    loaded = any(name.startswith(NAMESPACE + ".") for name in bridge.call("list_tools")["tools"])
    if not loaded or any(path.endswith(".py") for path in written):
        bridge.call("run_python", {"code": f"import sys\nif {REMOTE!r} not in sys.path:\n    sys.path.insert(0, {REMOTE!r})"})
        bridge.call("reload_tools", {"module": PACKAGE})


def main() -> int:
    with BridgeClient(host=HOST, port=PORT) as bridge:
        load(bridge)
        print(bridge.call(f"{NAMESPACE}.ping", {"message": "hello from the driver"}))
        # A file a tool wrote, brought back:
        # image = base64.b64decode(bridge.call("get_file", {"path": "/tmp/shot.png"})["content_b64"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Remote-driver template: drive a box's Kit app from another machine.

The pattern this file codifies — copy it and point it at your project:

    ensure code+assets  ->  (re)load the tool package  ->  dispatch tools

``run_python`` executes on the BRIDGE'S box, so imports and asset paths
resolve against its disk, never the caller's. Everything the tools need must
therefore exist on the box first — and the bridge's file data plane does that
without a side-channel rsync/scp:

  - ``stat_file``  verify: exists / size / sha256 of a box-side file
  - ``put_file``   push: write a file (base64; .py pushes purge __pycache__)
  - ``reload_tools``  (re)import + (re)register a tool package — doubles as
    FIRST load, so onboarding needs no Kit restart
  - ``get_file``   pull: fetch results (screenshots, exports) back

The ensure loop below is hash-verified: unchanged files are skipped, so
calling it before every run costs one stat per file, not one transfer.
Sizing note: put_file/get_file ride one NDJSON frame in memory — right for
code and tool-sized assets; ship multi-GB scenes over a bulk channel (rsync /
a shared mount) and still VERIFY them here with stat_file, so a run fails
fast naming the stale asset instead of trusting a blind sync.

Endpoint: on the box itself the client auto-discovers via the runtime
portfile. From a REMOTE machine there is no local portfile — pass host/port
(or set $OMNI_KIT_MCP_HOST/$OMNI_KIT_MCP_PORT), and remember the bridge binds
localhost unless installed with ``--bind-host`` (see the repo README's
"Remote access").

The tool package you push follows the standard contract (examples/demo_tools):
``MCP_NAMESPACE`` + ``register(registrar)``; async run()s are awaited on
Kit's loop by the bridge itself — a long motion is one blocking call with a
fitted timeout, never a schedule/poll loop.
"""

import base64
import hashlib
import pathlib
import sys

from kit_mcp.client import BridgeClient, call

# ---- point these at your project -------------------------------------------
HOST = "100.97.45.92"                      # the box's tailnet IP (or None on-box)
PORT = 9009                                # the installed bridge port
LOCAL_ROOT = pathlib.Path(__file__).parent # holding <package>/ and assets/
REMOTE_ROOT = "/home/user/myproject"       # where the box keeps them
PACKAGE = "demo_tools"                     # the tool package to push + load


def ensure_file(client: BridgeClient, local: pathlib.Path, remote: str) -> bool:
    """Push one file iff the box's copy differs (hash-verified). True = pushed."""
    data = local.read_bytes()
    stat = client.call("stat_file", {"path": remote})
    if stat.get("exists") and stat["sha256"] == hashlib.sha256(data).hexdigest():
        return False
    client.call("put_file", {"path": remote,
                             "content_b64": base64.b64encode(data).decode()})
    return True


def ensure_tree(client: BridgeClient, local_dir: pathlib.Path, remote_dir: str,
                suffixes=(".py", ".usd", ".usda", ".json", ".yaml")) -> list:
    """Ensure a directory tree on the box; returns the remote paths pushed."""
    pushed = []
    for f in sorted(local_dir.rglob("*")):
        if not (f.is_file() and f.suffix in suffixes) or "__pycache__" in f.parts:
            continue
        remote = f"{remote_dir}/{f.relative_to(local_dir)}"
        if ensure_file(client, f, remote):
            pushed.append(remote)
    return pushed


def ensure_loaded(client: BridgeClient) -> None:
    """Code + assets current on the box, package registered, schemas fresh.

    reload only when code moved: reload_tools re-imports the whole package
    tree (preserving the _state module), so gate it on actual .py pushes."""
    pushed = ensure_tree(client, LOCAL_ROOT / PACKAGE, f"{REMOTE_ROOT}/{PACKAGE}")
    pushed += ensure_tree(client, LOCAL_ROOT / "assets", f"{REMOTE_ROOT}/assets")
    code_moved = any(p.endswith(".py") for p in pushed)
    tools = client.call("list_tools")["tools"]
    ns = f"{PACKAGE}."     # default namespace = module name; MCP_NAMESPACE overrides
    if code_moved or not any(t.startswith(ns) for t in tools):
        # first load needs the package importable in the Kit process
        client.call("run_python", {"code": (
            f"import sys\n_d = {REMOTE_ROOT!r}\n"
            f"if _d not in sys.path:\n    sys.path.insert(0, _d)\nresult = 'ok'")})
        client.call("reload_tools", {"module": PACKAGE})


def main() -> int:
    with BridgeClient(host=HOST, port=PORT) as client:
        ensure_loaded(client)
        # dispatch: canonical <namespace>.<tool>; payload comes back directly,
        # errors raise BridgeError with the in-Kit traceback riding .details
        print(client.call(f"{PACKAGE}.ping", {"message": "hello from the driver"}))
        # pull a result file back (e.g. a screenshot a tool wrote):
        # png = base64.b64decode(client.call("get_file", {"path": "/tmp/shot.png"})["content_b64"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

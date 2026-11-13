"""MCP hints for catalogued tools that have no versioned contract: bridge built-ins and the twin.

The generic pack's hints are in ``server.py``, next to its contract list. These are advisory, not
enforcement, and a test requires an entry for every catalogued tool. Bridge tools from a project's
own extension are not listed here and carry no hints.
"""

from __future__ import annotations


def _hints(read_only=False, destructive=False, idempotent=False, open_world=False):
    return dict(readOnlyHint=read_only, destructiveHint=destructive, idempotentHint=idempotent,
                openWorldHint=open_world)


_READ = _hints(read_only=True, idempotent=True)
# Changes the scene or writes a new file, and does not remove anything the user made.
_EDIT = _hints()
_SAME_AGAIN = _hints(idempotent=True)
_DESTROYS = _hints(destructive=True)

CATALOG_HINTS = {
    "status": _READ,
    "list_bridges": _READ,
    "get_file": _READ,
    "stat_file": _READ,
    "job_status": _READ,
    "job_cancel": _hints(destructive=True, idempotent=True),  # ends work in progress
    # Arbitrary code in Kit: it can do anything this OS account can.
    "run_python": _hints(destructive=True, open_world=True),
    "put_file": _hints(destructive=True, idempotent=True),  # replaces a file at that path
    "reload_tools": _DESTROYS,  # unregisters and re-imports a live tool package
    "ur5e_dt.workcell_quick_start": _DESTROYS,
    "ur5e_dt.workcell_shutdown": _SAME_AGAIN,
    "ur5e_dt.workcell_status": _READ,
    "ur5e_dt.robot_status": _READ,
    "ur5e_dt.perception_status": _READ,
    "ur5e_dt.jenga_status": _READ,
    "ur5e_dt.jenga_build": _DESTROYS,
    "ur5e_dt.real2sim_status": _READ,
    "ur5e_dt.planning_status": _READ,
    "ur5e_dt.eval_status": _READ,
}

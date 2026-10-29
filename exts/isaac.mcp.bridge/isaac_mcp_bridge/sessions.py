"""Persistent run_python session namespaces — the store owns the policy.

``run_python`` with ``persistent=True`` keeps user variables alive between
calls under a client-chosen ``session_id``. This module is that store's one
home: which names survive a call (the persist policy), what a session's saved
namespace is, and when everything is dropped.

Persist policy: names starting with ``_`` and the preloaded convenience
symbols (see ``PRELOADED`` — the exact set run_python injects) never persist;
everything else the executed code left in its globals does.

Staleness: saved values are live in-process objects (prim references, physics
handles, numpy arrays). Opening a new stage kills what they point at, so the
bridge extension clears the store on every stage-open event — that guard is
the reason ``clear()`` exists.

Stdlib-only — importable off-Kit (tests, lint).
"""

from typing import Any, Dict, List

# The convenience symbols run_python preloads into every exec namespace.
# builtins.cmd_run_python builds its preload from this tuple, so the injected
# set and the never-persist set cannot drift apart.
PRELOADED = ("omni", "carb", "Usd", "UsdGeom", "Sdf", "Gf")


class SessionStore:
    """In-memory session_id -> saved-namespace map with the persist policy."""

    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def namespace(self, session_id: str) -> Dict[str, Any]:
        """The saved namespace for a session (a copy; {} when unknown)."""
        return dict(self._sessions.get(session_id, {}))

    def persist(self, session_id: str, exec_globals: Dict[str, Any]) -> List[str]:
        """Save the surviving names from an exec namespace; returns them sorted."""
        kept = {
            k: v for k, v in exec_globals.items()
            if not k.startswith("_") and k not in PRELOADED
        }
        self._sessions[session_id] = kept
        return sorted(kept)

    def clear(self) -> None:
        """Drop every session (stage-open staleness guard; see module doc)."""
        self._sessions.clear()

    def __len__(self) -> int:
        return len(self._sessions)

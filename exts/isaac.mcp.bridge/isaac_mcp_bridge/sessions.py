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
        # Sessions dropped by clear(): the next call with that id is told why its
        # variables are gone instead of failing with a bare NameError.
        self._reset: Dict[str, str] = {}

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

    def clear(self, reason: str = "a stage was opened") -> None:
        """Drop every session (stage-open staleness guard; see module doc)."""
        for session_id in self._sessions:
            self._reset[session_id] = reason
        self._sessions.clear()

    def take_reset(self, session_id: str) -> str:
        """Why this session's variables were dropped since its last call ('' if they were not)."""
        return self._reset.pop(session_id, "")

    def __len__(self) -> int:
        return len(self._sessions)

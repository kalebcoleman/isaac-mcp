"""Variables that run_python keeps between calls of one ``session_id``.

Saved values are live objects in the Kit process (prim handles, arrays). Opening a stage makes
them stale, so the extension clears every session then, and the next call is told why its
variables are gone. Stdlib only.
"""

from typing import Any, Dict, List

# Names run_python preloads into every call; they are never saved.
PRELOADED = ("omni", "carb", "Usd", "UsdGeom", "Sdf", "Gf")


class SessionStore:
    def __init__(self) -> None:
        self._saved: Dict[str, Dict[str, Any]] = {}
        self._cleared: Dict[str, str] = {}
        self.generation = 0   # changes whenever everything is cleared

    def namespace(self, session_id: str) -> Dict[str, Any]:
        """A copy of what the session holds ({} for a new one)."""
        return dict(self._saved.get(session_id, {}))

    def persist(self, session_id: str, variables: Dict[str, Any]) -> List[str]:
        """Save a call's variables, leaving out private names and the preloaded ones. Returns the names."""
        kept = {name: value for name, value in variables.items()
                if not name.startswith("_") and name not in PRELOADED}
        self._saved[session_id] = kept
        return sorted(kept)

    def clear(self, reason: str = "a stage was opened") -> None:
        for session_id in self._saved:
            self._cleared[session_id] = reason
        self._saved.clear()
        self.generation += 1

    def take_reset(self, session_id: str) -> str:
        """Why this session was cleared since its last call, once; '' if it was not."""
        return self._cleared.pop(session_id, "")

    def __len__(self) -> int:
        return len(self._saved)

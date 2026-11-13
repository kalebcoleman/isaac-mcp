#!/usr/bin/env python3
"""Fail if tracked code or scripts hard-code a user-specific path.

Reproducibility rule: code finds things relative to the repository, the workspace or
the environment, never at a fixed home directory. This checks tracked ``.py`` and
``.sh`` files (and ``run.sh``/``install.sh``) for:

* ``/home/<user>``, ``/Users/<user>`` and Windows drive paths;
* ``~/<dir>`` or ``$HOME/<dir>`` whose first directory is not a standard location
  (see ``ALLOWED_HOME_DIRS``).

Mark a deliberate exception with ``# portable-ok`` on the same line. Documentation is
covered by ``check_docs.py``. Run from anywhere: ``python3 scripts/check_portable_paths.py``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# First path component allowed after ~/ or $HOME/ : XDG-style and per-tool config
# locations, and default output folders that every override is documented for.
ALLOWED_HOME_DIRS = {
    ".cache",
    ".config",
    ".local",
    ".claude",
    ".agents",
    ".gemini",
    ".codex",
    "Pictures",
    "Videos",
    "ros2_ws",  # default for WS_SETUP in the ROS scripts, overridable
    "ros2",  # RoboStack prefixes documented in ROS2-RVIZ.md; ros_env.sh search root, ISAAC_ROS_SEARCH overrides
    ".ursim",  # URSim program data, overridable with PROGRAM_DIR
    "isaac-workspace",  # documented default workspace, overridable with TWINSMITH_WORKSPACE
}

ABSOLUTE = re.compile(r"(?<![\w.])(?:/home/[A-Za-z0-9_.@-]+|/Users/[A-Za-z0-9_.@-]+|[A-Za-z]:\\\\?[A-Za-z0-9_.-]+)")
HOME_REF = re.compile(
    r"(?:~|\$HOME|\$\{HOME\}|\"\$HOME\"|\"\$\{HOME\}\"|expanduser\([\"']~)/([A-Za-z0-9_.@<$-]+)"
    r"|Path\.home\(\)\s*/\s*[\"']([A-Za-z0-9_.@-]+)"
    r"|expanduser\(\s*[\"']~[\"']\s*\)\s*,\s*[\"']([A-Za-z0-9_.@-]+)"
)
SUFFIXES = (".py", ".sh")
EXTRA_FILES = {"run.sh", "install.sh"}
# The checker's own patterns and fixtures that must name bad paths.
SKIP = {"scripts/check_portable_paths.py", "scripts/check_private_paths.py", "scripts/check_assets.py"}
SKIP_PREFIXES = ("mcp/tests/", "docs/evidence/")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return [
        name
        for name in out.splitlines()
        if (name.endswith(SUFFIXES) or name in EXTRA_FILES)
        and name not in SKIP
        and not name.startswith(SKIP_PREFIXES)
    ]


def scan(names: list[str]) -> list[str]:
    problems: list[str] = []
    for name in names:
        try:
            lines = (REPO / name).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(lines, 1):
            if "portable-ok" in line:
                continue
            if ABSOLUTE.search(line):
                problems.append(f"{name}:{number}: user-specific absolute path: {line.strip()[:110]}")
            for match in HOME_REF.finditer(line):
                first = next(g for g in match.groups() if g)
                if first not in ALLOWED_HOME_DIRS and not first.startswith(("$", "{", "<")):
                    problems.append(
                        f"{name}:{number}: hard-coded home directory '~/{first}': {line.strip()[:110]}"
                    )
    return problems


def main() -> int:
    problems = scan(tracked_files())
    if problems:
        print("Hard-coded paths found (use the repo, the workspace or an env var):", file=sys.stderr)
        for item in problems:
            print(f"  {item}", file=sys.stderr)
        return 1
    print("Portable path check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

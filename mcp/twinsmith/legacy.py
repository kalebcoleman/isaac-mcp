"""The names this project had before 0.3.0, when it was called isaac-mcp.

This file is the only place in the code where those names are written. It is standard library only and
imports nothing from the package, so that every process that must honour the old names can load this one
file by its path: the host (``twinsmith/__init__.py``), the Kit bridge (``twinsmith_bridge/knobs.py``), the
installer (``scripts/install.py``) and the shell launchers (``scripts/legacy_env.sh`` runs this file).

An old environment variable keeps working for one release: ``apply`` copies its value to the new name when the
new name is not set, and says so once per name. ``ISAAC_MCP_ASSET_ROOT`` is the exception: it is a permanent
alias of ``TWINSMITH_ASSET_ROOT``, with no warning and no removal date.

    python3 mcp/twinsmith/legacy.py --exports     # shell: print "export NEW=value" lines (warnings go to stderr)
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import Callable, Mapping, MutableMapping, Optional

# ---------------------------------------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------------------------------------

# (old name, new name). Several old names can share a new one; the first one that is set wins, so the order is
# the order in which the old code read them. The new name always wins over every old one.
ALIASES = (
    # three names for the bridge port, one for the simulator's interpreter, one for headless
    ("UR5E_DT_PORT", "TWINSMITH_PORT"),
    ("ISAAC_MCP_PORT", "TWINSMITH_PORT"),
    ("ISAAC_SIM_PORT", "TWINSMITH_PORT"),
    ("ISAAC_MCP_PYTHON", "TWINSMITH_SIM_PYTHON"),
    ("UR5E_DT_PYTHON", "TWINSMITH_SIM_PYTHON"),
    ("ISAAC_SIM_PYTHON", "TWINSMITH_SIM_PYTHON"),
    ("UR5E_DT_HEADLESS", "TWINSMITH_HEADLESS"),
    # the job folder serves every job, not only Isaac Lab's; ISAACLAB_EXTERNAL_CALLBACK keeps its name
    ("ISAACLAB_JOBS_DIR", "TWINSMITH_JOBS_DIR"),
    ("ISAAC_MCP_EXTERNAL_CALLBACK", "TWINSMITH_EXTERNAL_CALLBACK"),
    # every other setting changes only its prefix
    ("ISAAC_MCP_ASSET_CONFIG", "TWINSMITH_ASSET_CONFIG"),
    ("ISAAC_MCP_AUTH", "TWINSMITH_AUTH"),
    ("ISAAC_MCP_BIND", "TWINSMITH_BIND"),
    ("ISAAC_MCP_CRASH_UPLOAD", "TWINSMITH_CRASH_UPLOAD"),
    ("ISAAC_MCP_DEFER_START", "TWINSMITH_DEFER_START"),
    ("ISAAC_MCP_ENABLE_SCENE_SCRIPTS", "TWINSMITH_ENABLE_SCENE_SCRIPTS"),
    ("ISAAC_MCP_EXTS", "TWINSMITH_EXTS"),
    ("ISAAC_MCP_HOST", "TWINSMITH_HOST"),
    ("ISAAC_MCP_INSTRUCTIONS", "TWINSMITH_INSTRUCTIONS"),
    ("ISAAC_MCP_LOG", "TWINSMITH_LOG"),
    ("ISAAC_MCP_LOG_DIR", "TWINSMITH_LOG_DIR"),
    ("ISAAC_MCP_NAME", "TWINSMITH_NAME"),
    ("ISAAC_MCP_NO_HOT_RELOAD", "TWINSMITH_NO_HOT_RELOAD"),
    ("ISAAC_MCP_PHYSICS_BACKEND", "TWINSMITH_PHYSICS_BACKEND"),
    ("ISAAC_MCP_PORT_RANGE", "TWINSMITH_PORT_RANGE"),
    ("ISAAC_MCP_PRINT_PYTHON", "TWINSMITH_PRINT_PYTHON"),
    ("ISAAC_MCP_PROJECT", "TWINSMITH_PROJECT"),
    ("ISAAC_MCP_READ_ONLY", "TWINSMITH_READ_ONLY"),
    ("ISAAC_MCP_ROBOSIM_ROOT", "TWINSMITH_ROBOSIM_ROOT"),
    ("ISAAC_MCP_ROS_DOMAIN_RANGE", "TWINSMITH_ROS_DOMAIN_RANGE"),
    ("ISAAC_MCP_RUNTIME_ENABLE", "TWINSMITH_RUNTIME_ENABLE"),
    ("ISAAC_MCP_SCENE_OUTPUT", "TWINSMITH_SCENE_OUTPUT"),
    ("ISAAC_MCP_SIM_LAUNCHER", "TWINSMITH_SIM_LAUNCHER"),
    ("ISAAC_MCP_SIM_MAX_INSTANCES", "TWINSMITH_SIM_MAX_INSTANCES"),
    ("ISAAC_MCP_SIM_MIN_FREE_RAM_MIB", "TWINSMITH_SIM_MIN_FREE_RAM_MIB"),
    ("ISAAC_MCP_SIM_MIN_FREE_VRAM_MIB", "TWINSMITH_SIM_MIN_FREE_VRAM_MIB"),
    ("ISAAC_MCP_SIM_ROOT", "TWINSMITH_SIM_ROOT"),
    ("ISAAC_MCP_SIM_SESSIONS_DIR", "TWINSMITH_SIM_SESSIONS_DIR"),
    ("ISAAC_MCP_STAGING", "TWINSMITH_STAGING"),
    ("ISAAC_MCP_TF_DOCTOR_REEXEC", "TWINSMITH_TF_DOCTOR_REEXEC"),
    ("ISAAC_MCP_TOKEN", "TWINSMITH_TOKEN"),
    ("ISAAC_MCP_TOOLSETS", "TWINSMITH_TOOLSETS"),
    ("ISAAC_MCP_TOOL_MODULES", "TWINSMITH_TOOL_MODULES"),
    ("ISAAC_MCP_TOOL_PATHS", "TWINSMITH_TOOL_PATHS"),
    ("ISAAC_MCP_USD_PYTHON", "TWINSMITH_USD_PYTHON"),
    ("ISAAC_MCP_WORKSPACE", "TWINSMITH_WORKSPACE"),
    # permanent: see PERMANENT
    ("ISAAC_MCP_ASSET_ROOT", "TWINSMITH_ASSET_ROOT"),
)

# Old names that stay aliases for good: no warning, no removal date (the work cell's lead asked for this).
ASSET_ROOT_ALIAS = "ISAAC_MCP_ASSET_ROOT"
PERMANENT = frozenset({ASSET_ROOT_ALIAS})

OLD_NAMES = frozenset(old for old, _ in ALIASES)

# Selection settings that were removed with the toolsets (0.2). A server that finds one set refuses to start
# and prints what to set instead. The value is the key ``twinsmith.config.replacement`` switches on.
STOPS_STARTUP = {
    "ISAAC_MCP_LOCAL_TOOLS": "local_tools",
    "ISAAC_MCP_NAMESPACE": "namespace",
    "ISAAC_MCP_BUILTINS": "builtins",
    "ISAAC_MCP_SCENE_TOOLS": "scene_tools",
    "ISAAC_MCP_GROUP_TOOLS": "group_tools",
}

# Settings of the old asset model. Nothing reads them; a host that finds one set says once what replaces it.
# ``{root}`` is the name of the asset-root setting.
REMOVED_ASSET_SETTINGS = {
    "ISAAC_MCP_ASSET_PATH": "Set {root} to the one folder that holds <pack id>/<version>/pack.yaml.",
    "UR5E_DT_ASSET_CACHE": "Set {root} to the one folder that holds <pack id>/<version>/pack.yaml.",
    "ISAAC_MCP_ASSET_PACK": "Name the pack as \"<id>/<version>\" in the 'pack' key of twinsmith.json, or pass it as an argument.",
    "ISAAC_MCP_ASSET_STRICT": "Strict is the only behaviour now: nothing is ever downloaded. Packs are found under {root}.",
    "UR5E_DT_OFFLINE": "Strict is the only behaviour now: nothing is ever downloaded. Packs are found under {root}.",
    "ISAAC_MCP_ASSET_REMOTE_ROOT": "Nothing is downloaded any more. Put the files in a pack under {root}.",
    "UR5E_DT_ASSET_ROOT": "Nothing is downloaded any more. Put the files in a pack under {root}.",
}

# ---------------------------------------------------------------------------------------------------------
# Files, folders, markers and ids
# ---------------------------------------------------------------------------------------------------------

# Read for one release, with a warning that names the new file. Each is "(old, new)" relative to its folder.
OLD_CONFIG_DIR = ".config/isaac-mcp"           # now ~/.config/twinsmith
OLD_PROJECT_TOML = "isaac-mcp.toml"            # now twinsmith.toml
OLD_PROJECT_JSON = "isaac-mcp.json"            # now twinsmith.json

# A script that prints one of these lines on its output is still understood for one release.
OLD_ARTIFACT_MARKER = "ISAAC_MCP_ARTIFACT:"    # now TWINSMITH_ARTIFACT:
OLD_PROGRESS_MARKER = "ISAAC_MCP_PROGRESS:"    # now TWINSMITH_PROGRESS:

# The namespace the work cell's tools had on the bridge (now "workcell"); an old NAMESPACE setting names it.
OLD_WORKCELL_NAMESPACE = "ur5e_dt"

# Extension ids and the Kit settings subtree the installer removes when asked to undo a registration.
OLD_BRIDGE_EXTENSION = "isaac.mcp.bridge"      # now twinsmith.bridge

# Folder and file names that exist on users' disks, written by the old versions.
OLD_PACK_FILE = "isaac-mcp-pack.json"          # the old asset model's build description
OLD_ROBOSIM_LEDGER_ID = "isaac-mcp-robosim-v1"  # ledgers written by the old RoboSimStudio hand-off

# ---------------------------------------------------------------------------------------------------------
# The startup pass
# ---------------------------------------------------------------------------------------------------------

_WARNED: set[str] = set()


def _default_warn(message: str) -> None:
    print(f"twinsmith: {message}", file=sys.stderr)


def plan(environ: Mapping[str, str]) -> list[tuple[str, str, bool]]:
    """(old, new, used) for every old name that is set. ``used`` is false when the new name is set already
    or an earlier old name of the same merge won, so the old value is ignored."""
    taken = {new for _, new in ALIASES if environ.get(new)}
    found = []
    for old, new in ALIASES:
        if not environ.get(old):
            continue
        used = new not in taken
        taken.add(new)
        found.append((old, new, used))
    return found


def apply(environ: Optional[MutableMapping[str, str]] = None,
          warn: Optional[Callable[[str], None]] = None) -> list[tuple[str, str]]:
    """Copy the value of every old name that is set to its new name, unless the new name is set.

    Returns the (old, new) pairs that were copied. Each old name is reported once per process, except the
    permanent alias, which is never reported."""
    environ = os.environ if environ is None else environ
    warn = _default_warn if warn is None else warn
    copied = []
    for old, new, used in plan(environ):
        if used:
            environ[new] = environ[old]
            copied.append((old, new))
        if old in PERMANENT or old in _WARNED:
            continue
        if not used and environ.get(new) == environ.get(old):
            continue        # the launcher copied it already (scripts/legacy_env.sh) and reported it there
        _WARNED.add(old)
        if used:
            warn(f"{old} is the old name of {new}; it still works in this release, so rename it. "
                 f"Migration guide: docs/MIGRATING.md")
        else:
            warn(f"{old} is the old name of {new} and is ignored because {new} is set (or an earlier old "
                 f"name of it is). Remove it. Migration guide: docs/MIGRATING.md")
    return copied


def choose_file(new: Path, old: Path, warn: Optional[Callable[[str], None]] = None) -> Optional[Path]:
    """The file to read: ``new`` if it exists, else ``old`` (with one warning that names ``new``), else None."""
    if new.is_file():
        return new
    if old.is_file():
        warn = _default_warn if warn is None else warn
        if str(old) not in _WARNED:
            _WARNED.add(str(old))
            warn(f"{old} is the old name of {new}; it is still read in this release. "
                 f"Move it: mv {shlex.quote(str(old))} {shlex.quote(str(new))}. Migration guide: docs/MIGRATING.md")
        return old
    return None


def shell_exports(environ: Optional[Mapping[str, str]] = None, warn: Optional[Callable[[str], None]] = None) -> str:
    """The ``export NEW=value`` lines a shell must run to get the same result as ``apply``."""
    environ = os.environ if environ is None else environ
    scratch = dict(environ)
    copied = apply(scratch, warn)
    return "".join(f"export {new}={shlex.quote(scratch[new])}\n" for _, new in copied)


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["--exports"]:
        print(__doc__, file=sys.stderr)
        return 2
    sys.stdout.write(shell_exports())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

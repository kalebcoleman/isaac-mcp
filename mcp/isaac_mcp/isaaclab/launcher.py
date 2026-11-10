"""Start an Isaac Lab script after importing the configured task packages.

Run by the train/eval/play jobs with the Isaac Lab interpreter (stdlib only, no isaac_mcp import):

    python launcher.py <script.py> [args...]

Isaac Lab's stock scripts only find tasks that are built in or declare an ``isaaclab.tasks`` entry point. This
imports ``$ISAACLAB_TASK_MODULES`` (comma list) first so a project's own tasks are registered, then runs the
script as ``__main__``. A module that fails to import ends the job with its traceback in the log.

``$ISAAC_MCP_EXTERNAL_CALLBACK`` (``module.function``) is called with no arguments after those imports and before the
script, for the libraries and scripts whose own ``--external_callback`` flag Isaac Lab does not honour (rl_games, skrl,
sb3, the zero/random agents).
"""

import importlib
from importlib.metadata import PackageNotFoundError, version
import os
import runpy
import sys


def _sim_owns_its_warp_early() -> bool:
    """Sim 6 and later: import Warp first, as Lab's own scripts do. On Sim 5.1 the pip Warp in the environment is newer than the
    one Kit ships, and importing it first breaks Kit's extensions (module 'warp.types' has no attribute 'array')."""
    try:
        return int(version("isaacsim").split(".")[0]) >= 6
    except (PackageNotFoundError, ValueError):
        return True


def main() -> None:
    script, args = sys.argv[1], sys.argv[2:]
    if _sim_owns_its_warp_early():
        try:  # the stock scripts do this before importing anything that defines Warp kernels
            import warp as wp

            wp.config.enable_backward = False
        except ImportError:
            pass
    for name in (m.strip() for m in os.environ.get("ISAACLAB_TASK_MODULES", "").split(",")):
        if name:
            importlib.import_module(name)
    callback = os.environ.get("ISAAC_MCP_EXTERNAL_CALLBACK", "").strip()
    if callback:
        module, _, function = callback.rpartition(".")
        getattr(importlib.import_module(module), function)()
    sys.argv = [script, *args]
    sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()

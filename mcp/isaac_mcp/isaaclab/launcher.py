"""Start an Isaac Lab script after importing the configured task packages.

Run by the train/eval/play jobs with the Isaac Lab interpreter (stdlib only, no isaac_mcp import):

    python launcher.py <script.py> [args...]

Isaac Lab's stock scripts only find tasks that are built in or declare an ``isaaclab.tasks`` entry point. This
imports ``$ISAACLAB_TASK_MODULES`` (comma list) first so a project's own tasks are registered, then runs the
script as ``__main__``. A module that fails to import ends the job with its traceback in the log.
"""

import importlib
import os
import runpy
import sys


def main() -> None:
    script, args = sys.argv[1], sys.argv[2:]
    try:  # the stock scripts do this before importing anything that defines Warp kernels
        import warp as wp

        wp.config.enable_backward = False
    except ImportError:
        pass
    for name in (m.strip() for m in os.environ.get("ISAACLAB_TASK_MODULES", "").split(",")):
        if name:
            importlib.import_module(name)
    sys.argv = [script, *args]
    sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()

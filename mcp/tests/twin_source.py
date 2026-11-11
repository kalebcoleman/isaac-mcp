"""The twin extension's source as one module, for tests that check what its code says.

The twin is a class assembled from one mixin per module; ``SOURCE.read_text()`` gives every
module's top-level code followed by a single ``DigitalTwin`` class holding all their methods.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "exts/ur5e-dt/ur5e_dt"
PARTS = ("settings", "capture_state", "panel", "log_panel", "environment", "video", "cameras", "scene", "robot",
         "objects", "commands", "extension")
MIXINS = {"Panel", "LogPanel", "Environment", "Video", "Cameras", "Scene", "Robot", "Objects", "Commands", "DigitalTwin"}


class _Source:
    def __str__(self):
        return str(PACKAGE / "extension.py")

    def read_text(self):
        top, methods = [], []
        for part in PARTS:
            text = (PACKAGE / f"{part}.py").read_text()
            lines = text.splitlines()
            end = 0
            for node in ast.parse(text).body:
                if isinstance(node, ast.ClassDef) and node.name in MIXINS:
                    top += lines[end:node.lineno - 1]
                    body = node.body[1:] if isinstance(node.body[0], ast.Expr) and node.name != "DigitalTwin" else node.body
                    methods += lines[body[0].lineno - 1 - len(body[0].decorator_list if hasattr(body[0], "decorator_list") else []):node.end_lineno] + [""]
                    end = node.end_lineno
            top += lines[end:]
        return "\n".join(top + ["", "", "class DigitalTwin(omni.ext.IExt):"] + methods) + "\n"


SOURCE = _Source()

"""Subprocess entry for mesh preparation and validation: one action, a JSON request in, a JSON result out.

    python -m twinsmith.mesh_worker <request.json>

The server starts this with its own interpreter, so ``pxr``, trimesh and CoACD never load in the server.
The request names an action and paths the server has already checked. The result is printed as the
last line of output and, for actions that write, also saved as ``mesh_report.json`` in the output folder.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):                         # run as a file (a job): import the package, not siblings
    here = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != here]
    sys.path.insert(0, str(here.parent))

REPORT = "mesh_report.json"
NEEDS = ("pxr", "trimesh", "coacd", "fast_simplification", "numpy")


def missing_modules() -> list[str]:
    import importlib.util

    return [name for name in NEEDS if importlib.util.find_spec(name) is None]


def run(request: dict) -> dict:
    from twinsmith import asset_rules, mesh_usd

    action = request["action"]
    source = Path(request["source"])
    prims = request.get("prims") or None
    if action == "inspect":
        return mesh_usd.inspect(source, prims)
    if action == "validate":
        return asset_rules.validate(source, request.get("engine", "newton"),
                                    triangle_budget=request.get("triangle_budget", asset_rules.TRIANGLE_BUDGET))
    if action not in ("decimate", "hulls", "split"):
        raise ValueError(f"unknown action {action!r}")
    out = Path(request["output"])
    if out.exists() or out.is_symlink():
        raise FileExistsError(f"{out} exists; outputs are never overwritten")
    # Written beside the output and renamed into place: a failed run leaves nothing, and relative paths stay valid.
    work = out.parent / f".{out.name}.partial-{os.getpid()}"
    work.mkdir()
    try:
        if action == "decimate":
            result = mesh_usd.decimate(source, work, target_tris=request.get("target_tris"), ratio=request.get("ratio"), prims=prims)
        elif action == "hulls":
            result = mesh_usd.author_collision_layer(mesh_usd.open_stage(source), work / f"{source.stem}_collision.usdc",
                                                     prims=prims, **request.get("collision", {}))
        else:
            result = mesh_usd.split(source, work, collision=request.get("collision"), prims=prims)
        result = json.loads(json.dumps(dict(action=action, source=str(source), **result)).replace(str(work), str(out)))
        (work / REPORT).write_text(json.dumps(result, indent=2) + "\n")
        os.rename(work, out)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return result


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(json.dumps(dict(status="error", code="INVALID_VALUE", message="usage: mesh_worker <request.json>")))
        return 2
    missing = missing_modules()
    if missing:
        print(json.dumps(dict(status="error", code="MESH_EXTRA_MISSING", missing=missing)))
        return 3
    request = Path(argv[0])
    try:
        payload = json.loads(request.read_text())
        request.unlink(missing_ok=True)               # the request is read once; nothing is left in the jobs folder
        result = run(payload)
    except Exception as exc:  # noqa: BLE001 - any failure must still end in one JSON line
        print(json.dumps(dict(status="error", code="MESH_FAILED", message=f"{type(exc).__name__}: {str(exc)[:2000]}")))
        return 1
    print(json.dumps(dict(status="ok", **result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

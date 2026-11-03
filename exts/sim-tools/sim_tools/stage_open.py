"""sim.stage.open: open a USD file as the current stage (File > Open), returning the new target IDs.

Every student run opened the lab's starter scene with run_python. ``check_path`` is pure and tested.
"""

from __future__ import annotations

import os
from pathlib import Path

SUFFIXES = (".usd", ".usda", ".usdc", ".usdz")


def check_path(path, home=None):
    """The resolved file to open, or (code, message) explaining why not."""
    if not isinstance(path, str) or not path:
        return None, ("INVALID_VALUE", "path: an absolute path to a .usd/.usda/.usdc/.usdz file.")
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        return None, ("INVALID_VALUE", "path must be absolute (or start with ~).")
    file = Path(expanded).resolve()
    if file.suffix.lower() not in SUFFIXES:
        return None, ("INVALID_VALUE", f"{file.name} is not a USD file ({', '.join(SUFFIXES)}).")
    home = Path(home or Path.home()).resolve()
    if home not in file.parents:
        return None, ("OUTSIDE_HOME", f"Only files under {home} can be opened with this tool.")
    if not file.is_file():
        return None, ("NOT_FOUND", f"{file} does not exist.")
    if not os.access(file, os.R_OK):
        return None, ("NOT_READABLE", f"{file} is not readable.")
    return file, None


KIT_DEFAULT_ROOTS = {"Render", "OmniverseKit_Persp", "OmniverseKit_Front", "OmniverseKit_Top", "OmniverseKit_Right"}


def unsaved_work(anonymous, dirty, top_level_names):
    """Would opening another stage lose work? A new (anonymous) stage that holds only Kit's own prims
    is always "dirty" but has nothing to lose; a file-backed stage counts when its layer is dirty."""
    if anonymous:
        return any(name not in KIT_DEFAULT_ROOTS for name in top_level_names)
    return bool(dirty)


async def open_stage(scene, args):
    """Open *path* after checking the current target, the timeline and unsaved edits."""
    import omni.kit.app
    import omni.usd
    from isaac_mcp.project_assets import refuse

    if not omni.kit.app.get_app().is_app_ready():
        refuse("APP_STARTING", "Kit startup is still running. Wait until sim.stage.info reports app_ready=true; "
               "no stage was opened.")
    scene.target(args.get("session_id"), args.get("stage_id"))
    file, problem = check_path(args.get("path"))
    if problem:
        refuse(*problem)
    if not scene.timeline.is_stopped():
        refuse("TIMELINE_ACTIVE", "Stop the timeline before opening another stage.")
    ctx = omni.usd.get_context()
    stage = ctx.get_stage()
    layers_dirty = any(layer.dirty for layer in stage.GetLayerStack(includeSessionLayers=False)) if stage else False
    dirty = bool(stage) and unsaved_work(stage.GetRootLayer().anonymous,
                                         layers_dirty or ctx.has_pending_edit(),
                                         [p.GetName() for p in stage.GetPseudoRoot().GetChildren()])
    if dirty and not args.get("discard_changes", False):
        refuse("UNSAVED_CHANGES", "The current stage has unsaved edits. Save them first (sim__stage__save), or pass "
               "discard_changes=true to drop them.", source=stage.GetRootLayer().identifier)
    ok, error = await ctx.open_stage_async(str(file))
    if not ok:
        refuse("OPEN_FAILED", f"Kit could not open {file}: {str(error)[:300]}")
    app = omni.kit.app.get_app()
    for _ in range(10):  # let stage-open events reach the scene tools and the bridge sessions
        await app.next_update_async()
    info = scene.stage_info()
    errors = ctx.get_stage().GetCompositionErrors()
    return dict(
        opened=str(file),
        discarded_changes=dirty,
        **{k: info[k] for k in ("session_id", "stage_id", "source", "default_prim", "meters_per_unit",
                                "up_axis", "prim_count", "truncated")},
        composition_errors=len(errors),
        composition_error_samples=[str(e)[:200] for e in errors[:3]],
        note="Use the new session_id and stage_id for later sim__* calls. run_python sessions were reset.",
    )

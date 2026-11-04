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


# Comparing bigger files takes too long; they keep Kit's dirty flag, which can over-report.
MAX_COMPARE_BYTES = 512 * 1024 * 1024
# Kit writes these on its own: viewport camera and render settings in customLayerData, and its own cameras.
# The Orchestrators are Replicator's runtime graphs from a Play; saves strip them as process-bound.
KIT_SESSION_ROOTS = KIT_DEFAULT_ROOTS | {"OmniKit_Viewport_LightRig", "Orchestrator", "WriterOrchestrator"}


def _kit_owned(name):
    return name in KIT_SESSION_ROOTS or name.startswith("OmniverseKit_")


def _content(layer):
    """The layer's text without what Kit rewrites just by opening a file: customLayerData, and its own
    viewport cameras, which a flattened file contains and Kit turns from def into over."""
    from pxr import Sdf

    copy = Sdf.Layer.CreateAnonymous(".usda")
    copy.TransferContent(layer)
    copy.customLayerData = {}
    for prim in list(copy.rootPrims):
        if _kit_owned(prim.name):
            del copy.pseudoRoot.nameChildren[prim.name]
    return copy.ExportToString()


def layer_edited(layer):
    """Does an open layer really differ from its file? Kit marks every opened file dirty, so the flag alone
    made this guard refuse on every stage and taught callers to pass discard_changes."""
    from pxr import Sdf

    if not layer.dirty:
        return False
    try:
        path = layer.realPath
        if not path or os.path.getsize(path) > MAX_COMPARE_BYTES:
            return True
        return _content(layer) != _content(Sdf.Layer.OpenAsAnonymous(path))
    except Exception:  # noqa: BLE001 - unreadable or unusual format: keep the safe answer
        return True


def session_edits(stage):
    """Top-level session-layer prims that are not Kit's own. Our tools author here, and an open would drop them."""
    return sorted(prim.name for prim in stage.GetSessionLayer().rootPrims
                  if not _kit_owned(prim.name))


def file_stage_has_unsaved_work(stage):
    return bool(session_edits(stage)) or any(
        not layer.anonymous and layer_edited(layer) for layer in stage.GetLayerStack(includeSessionLayers=False))


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
        from .core import stop_call

        refuse("TIMELINE_ACTIVE", "Stop the timeline before opening another stage: call sim__timeline__control "
               "action=stop with these IDs, then resend this call.",
               before_retry=stop_call(args.get("session_id"), args.get("stage_id")))
    ctx = omni.usd.get_context()
    stage = ctx.get_stage()
    dirty = bool(stage) and unsaved_work(stage.GetRootLayer().anonymous, file_stage_has_unsaved_work(stage),
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
    stale = _repair_saved_lidar_products(ctx.get_stage())
    info = scene.stage_info()
    from .compat import composition_errors

    errors = composition_errors(ctx.get_stage())
    return dict(
        opened=str(file),
        discarded_changes=dirty,
        **{k: info[k] for k in ("session_id", "stage_id", "source", "default_prim", "meters_per_unit",
                                "up_axis", "prim_count", "truncated")},
        composition_errors=len(errors),
        composition_error_samples=[str(e)[:200] for e in errors[:3]],
        note="Use the new session_id and stage_id for later sim__* calls. run_python sessions were reset.",
        **stale,
    )


def _repair_saved_lidar_products(stage):
    """Result fields for stale saved LiDAR handles, repaired before the first Play. Never fails the open."""
    from .render_export import repair_stale_lidar_products, retained_product_paths

    try:
        repair = repair_stale_lidar_products(stage, retained_product_paths())
    except Exception as error:  # noqa: BLE001 - the stage is open; report instead of failing
        return dict(stale_render_products=dict(deactivated=[], skipped=[dict(path="/", reason=str(error)[:200])]))
    if not repair["deactivated"] and not repair["skipped"]:
        return {}
    return dict(stale_render_products=dict(
        **repair,
        note="This file saved a LiDAR product's viewportHandle, which stops its ROS topic. Deactivated products use "
             "the session layer only; the file is unchanged, and a new product is created at Play. Skipped ones "
             "still will not publish if reused; sim__graph__inspect shows them.",
    ))

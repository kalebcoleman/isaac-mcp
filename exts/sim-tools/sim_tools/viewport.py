"""Frame a selected viewport without authoring into the source USD layers."""

import math

from pxr import Usd, UsdGeom

from isaac_mcp.project_assets import refuse
from .core import CAP, matrix


def frame(scene, stage, path, context, viewport, frame_selection, undo_disabled):
    prim = scene.prim(stage, path)
    if viewport is None:
        refuse("NO_VIEWPORT", "No active viewport in this Kit.")

    camera_path = str(viewport.camera_path)

    def validate():
        if (
            scene.get_stage() != stage
            or context.get_stage() != stage
            or viewport.stage != stage
            or viewport.usd_context_name != context.get_name()
            or str(viewport.camera_path) != camera_path
        ):
            refuse(
                "VIEWPORT_CHANGED",
                "The viewport camera, context or stage changed. Inspect before explicitly retrying.",
                camera=camera_path,
            )

    validate()
    camera = stage.GetPrimAtPath(camera_path)
    if (
        not camera or not camera.IsActive() or not camera.IsA(UsdGeom.Camera)
        or camera.IsInstanceProxy() or camera.IsInPrototype()
    ):
        refuse("VIEWPORT_CAMERA_UNSUPPORTED", "Choose an editable USD camera in the selected stage.")
    if not stage.GetSessionLayer().permissionToEdit:
        refuse("READ_ONLY", "The session layer is not editable; no framing was requested.")
    look_through = camera.GetRelationship("omni:kit:viewport:lookThrough:target")
    if look_through and look_through.GetForwardedTargets():
        refuse(
            "VIEWPORT_CAMERA_UNSUPPORTED",
            "This camera redirects framing to another prim. Choose a viewport camera without a look-through target.",
        )

    # Kit's transform/property commands can explicitly retarget to camera specs
    # in session sublayers, ignoring the current edit target. Do not mutate those
    # layers. Root/reference opinions are safely overridden in the session root.
    layers = stage.GetLayerStack(True)
    if len(layers) > 128 or stage.GetRootLayer() not in layers:
        refuse("VIEWPORT_CAMERA_UNSUPPORTED", "Cannot bound the viewport camera's local layer stack.")
    for layer in layers:
        if layer == stage.GetRootLayer():
            break
        if layer != stage.GetSessionLayer() and layer.GetPrimAtPath(camera_path):
            refuse(
                "VIEWPORT_CAMERA_UNSUPPORTED",
                "Camera opinions in a session sublayer can redirect native edits. Choose a camera without those opinions.",
                camera=camera_path,
            )

    # The native utility otherwise returns True even for an empty bounding box.
    traversal = Usd.PrimRange(prim, Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate))
    for index, _ in enumerate(traversal):
        if index >= CAP:
            refuse("LIMIT", f"Viewport framing is limited to {CAP} composed prims; select a smaller subtree.")
    bounds = UsdGeom.BBoxCache(
        viewport.time, [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy]
    ).ComputeWorldBound(prim).ComputeAlignedRange()
    if bounds.IsEmpty() or not all(
        math.isfinite(v) for p in (bounds.GetMin(), bounds.GetMax()) for v in p
    ):
        refuse("NO_BOUNDS", "Selected prim has no finite visible geometry to frame; select a geometry subtree.")

    context.get_selection().set_selected_prim_paths([path], False)
    try:
        # These Kit commands otherwise store undo callbacks which can re-author
        # using a later edit target. Do not enqueue such callbacks for this tool.
        with undo_disabled(), Usd.EditContext(stage, stage.GetSessionLayer()):
            accepted = frame_selection(viewport)
            validate()
    except Exception as exc:
        refuse(
            "VIEWPORT_FRAME_FAILED",
            f"Native framing failed: {str(exc)[:500]}",
            camera=camera_path,
            selected_path=path,
            effects="Selection and session camera edits may have occurred. Inspect before retrying; the prior edit target was restored.",
        )
    if accepted is not True:
        refuse(
            "VIEWPORT_FRAME_FAILED",
            "The native viewport utility did not confirm framing.",
            camera=camera_path,
            selected_path=path,
            effects="Selection and session camera edits may have occurred; inspect before retrying.",
        )
    validate()
    camera_matrix = matrix(
        UsdGeom.Xformable(camera).ComputeLocalToWorldTransform(viewport.time)
    )
    if not all(math.isfinite(v) for row in camera_matrix for v in row):
        refuse(
            "VIEWPORT_FRAME_FAILED",
            "Native framing left a non-finite camera transform; inspect the session camera before retrying.",
            camera=camera_path,
            effects="Selection and session camera edits occurred; source layers and the prior edit target were preserved.",
        )
    return dict(
        selected_path=path,
        camera=camera_path,
        camera_world_matrix=camera_matrix,
        authored_layer="session",
        render_complete=False,
        effects="Selected prim and framed the active camera in the session layer. Source layers are unchanged; rendering and visibility are not verified.",
        capture="Allow ordinary render updates, then use screenshot with this application PID/window ID. A large subtree can frame occluding geometry; select a smaller subtree if needed.",
    )

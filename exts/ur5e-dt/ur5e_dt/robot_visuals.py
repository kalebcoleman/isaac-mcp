"""Keep the twin's rendered arm links attached to their simulated bodies."""

from pxr import UsdPhysics


def prepare_robot_visuals(stage, root_path):
    """Expand visual instances only on the robot this extension just referenced.

    Kit 110.0/FSD rendered the NVIDIA UR5e's instanced visuals in the initial
    pose after PhysX, USD and Fabric had all moved the links. Expanding the
    seven visual containers makes subsequent pose changes reach the renderer.
    This is a composed-stage override, never an edit to the cached/source USD.
    Collision instances, geometry, materials and the rest of the stage stay as
    authored. One twin gains correctness at the cost of visual instance sharing.
    """
    root = stage.GetPrimAtPath(root_path)
    if not root or root.IsInstanceProxy():
        raise ValueError(f'Robot root is not editable: {root_path}')
    changed = []
    for body in root.GetChildren():
        if not body.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        visual = body.GetChild('visuals')
        if visual and visual.IsInstance():
            if not visual.SetInstanceable(False):
                raise RuntimeError(f'Could not expand robot visuals at {visual.GetPath()}')
            changed.append(str(visual.GetPath()))
    return changed

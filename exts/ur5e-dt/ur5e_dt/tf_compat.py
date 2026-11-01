"""Select the ROS transform graph without guessing on unknown Kit versions."""


def use_legacy_transform_publisher(compute_node_version, isaacsim_version):
    """The first 6.0 pip release computes transforms inside the ROS publisher."""
    # That release can report a registered placeholder despite lacking an
    # instantiable compute node, so the exact tested distribution wins.
    if isaacsim_version == "6.0.0.0":
        return True
    if isinstance(compute_node_version, int) and compute_node_version > 0:
        return False
    raise RuntimeError(
        "IsaacComputeTransformTree is unavailable and this Isaac Sim build is not the "
        "verified 6.0.0.0 legacy publisher. Check that isaacsim.core.nodes is enabled."
    )

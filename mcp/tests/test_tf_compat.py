"""The first 6.0 package needs a different ROS transform graph."""

import importlib.util
from pathlib import Path

import pytest


_path = Path(__file__).resolve().parents[2] / "exts/ur5e-dt/ur5e_dt/tf_compat.py"
_spec = importlib.util.spec_from_file_location("ur5e_tf_compat", _path)
tf_compat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tf_compat)


def test_registered_compute_node_uses_current_graph():
    assert tf_compat.use_legacy_transform_publisher(1, None) is False


def test_missing_compute_node_uses_legacy_graph_only_for_first_60_release():
    assert tf_compat.use_legacy_transform_publisher(None, "6.0.0.0") is True
    # Some Kit registries expose a non-instantiable placeholder for this node.
    assert tf_compat.use_legacy_transform_publisher(1, "6.0.0.0") is True


@pytest.mark.parametrize("version", [None, "6.0.0.1", "6.1.0.0", "6.2.0.0"])
def test_missing_compute_node_on_unverified_build_fails_loudly(version):
    with pytest.raises(RuntimeError, match="IsaacComputeTransformTree is unavailable"):
        tf_compat.use_legacy_transform_publisher(None, version)


def test_isaac_51_uses_the_legacy_publisher_even_with_a_placeholder_compute_node():
    from importlib.util import module_from_spec, spec_from_file_location

    root = Path(__file__).resolve().parents[2]
    spec = spec_from_file_location("tf_compat_51", root / "exts/ur5e-dt/ur5e_dt/tf_compat.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.use_legacy_transform_publisher(1, "5.1.0.0") is True

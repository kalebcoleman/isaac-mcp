"""Pure logic: DOF name resolution, clamping and the tensor-view adapters (no Kit)."""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pxr.Usd")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from isaac_mcp.project_assets import AssetError
from sim_tools.advanced import allowed_targets, bounded_names, resolve_named
from sim_tools.physics import (
    dof_gains,
    dof_limits,
    dof_max_forces,
    dof_row,
    dof_set,
    dof_units,
    dof_write,
)

NAMES = ["shoulder_pan", "shoulder_lift", "wrist"]


class FakeView:
    """The part of the PhysX/Newton tensor articulation view the adapters use."""

    def __init__(self, values, limits=None, float64=False):
        self.values = np.array([values], dtype=np.float64 if float64 else np.float32)
        self.limits = np.array([limits or [[-math.pi, math.pi]] * len(values)], dtype=np.float32)
        self.written = []

    def get_dof_positions(self):
        return self.values.copy()

    def get_dof_position_targets(self):
        return self.values.copy()

    def get_dof_velocities(self):
        return np.zeros_like(self.values)

    def get_dof_velocity_targets(self):
        return np.zeros_like(self.values)

    def get_dof_limits(self):
        return self.limits

    def set_dof_positions(self, data, indices):
        self.written.append(("positions", np.array(data), indices))
        self.values = np.array(data)

    def set_dof_position_targets(self, data, indices):
        self.written.append(("targets", np.array(data), indices))

    def set_dof_velocities(self, data, indices):
        self.written.append(("velocities", np.array(data), indices))

    def set_dof_velocity_targets(self, data, indices):
        self.written.append(("velocity_targets", np.array(data), indices))

    def get_dof_stiffnesses(self):
        return np.array([[100.0, 200.0, 300.0]])

    def get_dof_dampings(self):
        return np.array([[1.0, 2.0, 3.0]])

    def get_dof_max_forces(self):
        return np.array([[10.0, 20.0, 30.0]])

    def set_dof_stiffnesses(self, data, indices):
        self.written.append(("stiffness", np.array(data), indices))

    def set_dof_dampings(self, data, indices):
        self.written.append(("damping", np.array(data), indices))

    def set_dof_max_forces(self, data, indices):
        self.written.append(("max_force", np.array(data), indices))


class ExperimentalView(FakeView):
    """An experimental wrapper build: limits and gains arrive as (lower, upper) pairs."""

    def get_dof_limits(self):
        return self.limits[..., 0], self.limits[..., 1]

    def get_dof_gains(self):
        return np.array([[100.0, 200.0, 300.0]]), np.array([[1.0, 2.0, 3.0]])

    def get_dof_stiffnesses(self):
        raise AttributeError("not on this build")

    def get_dof_dampings(self):
        raise AttributeError("not on this build")

    def get_dof_max_forces(self):
        raise AttributeError("not on this build")

    def get_dof_max_efforts(self):
        return np.array([[10.0, 20.0, 30.0]])


def test_resolve_named_reports_the_valid_names_for_an_unknown_dof():
    rows = resolve_named({"wrist": 0.5}, NAMES, "targets")
    assert rows == [(2, "wrist", 0.5)]
    with pytest.raises(AssetError) as error:
        resolve_named({"wrists": 0.5}, NAMES, "targets")
    assert error.value.code == "UNKNOWN_DOF"
    assert error.value.details["valid_names"] == NAMES
    assert "shoulder_pan" in str(error.value)
    for bad in ({}, [], {"wrist": "0.5"}, {"wrist": float("nan")}):
        with pytest.raises(AssetError):
            resolve_named(bad, NAMES, "targets")


def test_bounded_names_keeps_a_refusal_short():
    assert bounded_names(NAMES) == "shoulder_pan, shoulder_lift, wrist"
    assert "in all" in bounded_names([str(i) for i in range(40)])


def test_allowed_targets_refuses_a_real_excess_with_the_numbers_and_the_way_out():
    limits = [[-1.0, 1.0], [-1.0, 1.0], [-1.0, 1.0]]
    with pytest.raises(AssetError) as error:
        allowed_targets([(0, "a", 2.0)], limits, [0.0, 0.0, 0.0], ["rad"] * 3, False, "Target")
    assert error.value.code == "JOINT_LIMIT" and "clamp=true" in str(error.value)
    assert error.value.details["limits"] == [-1.0, 1.0]


def test_allowed_targets_clamps_only_when_asked_and_rounds_float32_limits():
    limits = [[-1.0, 1.0], [-0.5, 0.5], [-1.0, 1.0]]
    applied, clamped, rounded = allowed_targets(
        [(0, "a", 2.0), (1, "b", 0.5000001)], limits, [0.0] * 3, ["rad"] * 3, True, "Target"
    )
    assert applied[0][2] == 1.0 and clamped[0]["name"] == "a" and clamped[0]["requested"] == 2.0
    assert applied[1][2] == 0.5 and rounded[0]["name"] == "b"
    assert isinstance(applied[0][2], float)


def test_allowed_targets_accepts_a_pose_echoed_from_a_joint_resting_past_its_limit():
    limits = [[-0.45, 1.0], [-1.0, 1.0], [-1.0, 1.0]]
    applied, _, _ = allowed_targets(
        [(0, "a", -0.4500001)], limits, [-0.4500001, 0.0, 0.0], ["rad"] * 3, False, "Target"
    )
    assert applied[0][2] == pytest.approx(-0.45)


def test_dof_limits_reads_both_build_shapes():
    plain = FakeView([0.0, 0.0, 0.0], [[-1, 1]] * 3)
    assert dof_limits(plain) == [[-1.0, 1.0]] * 3
    wrapper = ExperimentalView([0.0, 0.0, 0.0], [[-1, 1]] * 3)
    assert dof_limits(wrapper) == [[-1.0, 1.0]] * 3


def test_dof_limits_refuses_an_unexpected_shape():
    class Odd(FakeView):
        def get_dof_limits(self):
            return np.zeros((2, 3, 2), dtype=np.float32)

    with pytest.raises(AssetError) as error:
        dof_limits(Odd([0.0, 0.0, 0.0]))
    assert error.value.code == "PHYSICS_FORMAT"


def test_dof_gains_and_max_effort_use_whichever_api_this_build_has():
    plain = FakeView([0.0, 0.0, 0.0])
    assert dof_gains(plain) == ([100.0, 200.0, 300.0], [1.0, 2.0, 3.0])
    assert dof_max_forces(plain) == [10.0, 20.0, 30.0]
    wrapper = ExperimentalView([0.0, 0.0, 0.0])
    assert dof_gains(wrapper) == ([100.0, 200.0, 300.0], [1.0, 2.0, 3.0])
    assert dof_max_forces(wrapper) == [10.0, 20.0, 30.0]


def test_dof_units_falls_back_to_both_units_without_omni_physics_tensors():
    class Stub(FakeView):
        class shared_metatype:
            dof_names = NAMES
            dof_types = [0, 0, 0]

    assert len(dof_units(Stub([0.0, 0.0, 0.0]))) == 3
    assert all("stage units" in unit for unit in dof_units(Stub([0.0, 0.0, 0.0])))


def test_dof_set_changes_only_the_named_dofs():
    view = FakeView([0.0, 0.0, 0.0])
    dof_set(view, "get_dof_position_targets", "set_dof_position_targets", {1: 0.25})
    kind, data, indices = view.written[-1]
    assert kind == "targets" and data.shape == (1, 3)
    assert data[0].tolist() == pytest.approx([0.0, 0.25, 0.0])
    assert np.asarray(indices).tolist() == [0]


def test_dof_row_and_write_keep_the_frontends_dtype_and_shape():
    view = FakeView([0.0, 0.0, 0.0], float64=True)
    row = dof_row(view, [0.0, 0.0, 0.0])
    assert row.dtype == np.float32 and row.shape == (1, 3)
    dof_write(view, "set_dof_positions", row)
    kind, data, indices = view.written[-1]
    assert kind == "positions" and np.asarray(data).shape == (1, 3)
    assert np.asarray(indices).dtype == np.uint32

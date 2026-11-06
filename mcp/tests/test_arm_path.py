"""A straight arm path in one call: points on the line, each an ordinary IK call, and it stops rather than goes on when a point fails."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exts/sim-tools"))
from isaac_mcp.project_assets import AssetError
from sim_tools.arm_path import straight


class Fake:
    def __init__(self, tip=(0.0, 0.0, 0.5), fail_after=None, stalls=False):
        self.tip, self.fail_after, self.stalls = list(tip), fail_after, stalls
        self.time, self.points = 0.0, []

    def dispatch(self, name, a):
        if name == "sim.arm.fk":
            return dict(end_effector_pose=dict(position=list(self.tip)), in_base_frame=dict(position=[v + 1.0 for v in self.tip]))
        assert "speed" not in a and a["move"] is True
        self.points.append(a["position"])
        reached = self.fail_after is None or len(self.points) <= self.fail_after
        if reached:
            self.tip = list(a["position"])
        return dict(reached=reached, joint_targets={})

    def now(self):
        return self.time

    async def frame(self):
        if self.stalls:
            await asyncio.sleep(0)
        else:
            self.time += 1 / 60

    def run(self, playing=True, **args):
        return asyncio.run(straight(dict(dict(session_id="s", stage_id="t", path="/A", link="hand", move=True), **args), self.dispatch, lambda: playing, self.now, self.frame))


def test_it_walks_a_straight_line_to_the_goal_at_the_speed_asked():
    fake = Fake()
    result = fake.run(position=[0.1, 0.0, 0.5], speed=0.1)
    assert len(fake.points) == 30 and result["path"]["steps_done"] == 30  # 1 s at 30 Hz
    assert fake.points[14] == pytest.approx([0.05, 0.0, 0.5]) and fake.points[-1] == pytest.approx([0.1, 0.0, 0.5])
    assert fake.time == pytest.approx(1.25, abs=0.1)  # one second of simulation time, and the settle
    assert result["path"]["final_error"] == pytest.approx(0.0) and "stopped_early" not in result["path"]


def test_it_stops_at_a_point_it_cannot_reach_and_commands_no_more():
    fake = Fake(fail_after=4)
    result = fake.run(position=[0.1, 0.0, 0.5], speed=0.1)
    assert len(fake.points) == 5 and result["path"]["steps_done"] == 4 and "could not be reached" in result["path"]["stopped_early"]
    assert result["reached"] is False


def test_the_base_frame_starts_from_the_base_frame_position():
    fake = Fake()
    fake.run(position=[1.1, 1.0, 1.5], speed=0.1, frame="base")
    assert fake.points[0] == pytest.approx([1.0 + 0.1 / 30, 1.0, 1.5])


@pytest.mark.parametrize(("args", "code"), [
    (dict(position=[0.1, 0, 0.5], speed=0), "INVALID_VALUE"), (dict(position=[0.1, 0, 0.5], speed=True), "INVALID_VALUE"), (dict(position=[0.1, 0, 0.5], speed=float("inf")), "INVALID_VALUE"),
    (dict(position=[0.1, 0, 0.5], speed=0.1, move=False), "INVALID_VALUE"), (dict(position=[0.1, 0], speed=0.1), "INVALID_VALUE"), (dict(position=[100.0, 0, 0.5], speed=0.1), "LIMIT")])
def test_bad_arguments_are_refused_before_anything_moves(args, code):
    fake = Fake()
    with pytest.raises(AssetError) as error:
        fake.run(**args)
    assert error.value.code == code and fake.points == []


def test_it_needs_a_playing_timeline():
    fake = Fake()
    with pytest.raises(AssetError) as error:
        fake.run(playing=False, position=[0.1, 0, 0.5], speed=0.1)
    assert error.value.code == "PHYSICS_NOT_READY"


def test_it_stops_when_the_simulation_stops_advancing(monkeypatch):
    from sim_tools import arm_path

    monkeypatch.setattr(arm_path, "STUCK_WALL_S", 0.01)
    fake = Fake(stalls=True)
    result = fake.run(position=[0.1, 0, 0.5], speed=0.1)
    assert len(fake.points) == 1 and "stopped advancing" in result["path"]["stopped_early"]


def test_a_refusal_at_the_first_point_stands_and_a_later_one_keeps_the_path():
    class Refusing(Fake):
        def __init__(self, at):
            super().__init__()
            self.at = at

        def dispatch(self, name, a):
            if name == "sim.arm.ik" and len(self.points) == self.at:
                raise AssetError("IK_MODEL_MISMATCH", "The model disagrees.")
            return super().dispatch(name, a)

    with pytest.raises(AssetError) as error:
        Refusing(0).run(position=[0.1, 0, 0.5], speed=0.1)
    assert error.value.code == "IK_MODEL_MISMATCH"
    result = Refusing(5).run(position=[0.1, 0, 0.5], speed=0.1)
    assert result["path"]["steps_done"] == 5 and "IK_MODEL_MISMATCH" in result["path"]["stopped_early"] and result["path"]["final"]


def test_a_slow_frame_rate_skips_points_rather_than_slowing_the_arm():
    class Slow(Fake):
        async def frame(self):
            self.time += 0.2

    fake = Slow()
    result = fake.run(position=[0.1, 0, 0.5], speed=0.1)
    assert len(fake.points) < 10 and fake.points[-1] == pytest.approx([0.1, 0, 0.5]) and result["path"]["steps_done"] == 30
    assert fake.time < 1.7


def test_a_timeline_that_loops_ends_the_path():
    class Looping(Fake):
        async def frame(self):
            self.time = self.time + 1 / 60 if len(self.points) < 4 else 0.0

    fake = Looping()
    result = fake.run(position=[0.1, 0, 0.5], speed=0.1)
    assert "stopped advancing" in result["path"]["stopped_early"] and len(fake.points) == 4


def test_a_very_slow_simulation_is_cut_off_by_real_time(monkeypatch):
    from sim_tools import arm_path

    monkeypatch.setattr(arm_path, "MAX_WALL_S", -1.0)
    result = Fake().run(position=[0.1, 0, 0.5], speed=0.1)
    assert "real time" in result["path"]["stopped_early"] and result["path"]["steps_done"] == 1

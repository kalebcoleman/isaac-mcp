"""metrics_summary eval_json mode: pass counts from evaluation-result JSON (synthetic files in the G1 layout)."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isaac_mcp.isaaclab import evalsummary as ev
from isaac_mcp.isaaclab import tools

DIRS = ("side_a", "prone", "side_b", "supine")


def result(passes, pose_set="heldout_v2.npz", n_dir=64, ground=None):
    """passes: per-direction counts for the default criterion (v3 ground-only); rates are derived for v21/v2/strict."""
    ground = ground or dict(zip(DIRS, passes))
    total = sum(ground.values())
    rate = {d: ground[d] / n_dir for d in DIRS}
    return {
        "task": "G1-Recovery-R9hg-v0", "checkpoint": "/ckpt/g4j1_b1p2_it1425.pt", "num_envs": n_dir * 4, "pose_set": pose_set,
        "strict_supported_stand": {"successes": 250, "by_category": {d: 0.97 for d in DIRS}},
        "plausible": {"plausible_v21_successes": total, "plausible_v21_by_category": rate,
                      "plausible_v2_successes": total, "plausible_v2_by_category": rate},
        "by_category": {d: {"n": n_dir, "success_rate": 1.0} for d in DIRS},
        "amendment3": {"ground_only_flight": {"v3_before_selfpen": total, "by_category": ground},
                       "plausible_v3_before_selfpen": total, "plausible_v3_before_selfpen_by_category": ground,
                       "proposed_a4_flight_history": {"v3_before_selfpen_with_a4": total, "v3_with_a4_by_category": ground}},
    }


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data))
    return str(p)


def test_counts_directions_split_and_baseline(tmp_path):
    runs = [write(tmp_path, f"c{i}.json", result(p)) for i, p in enumerate([(40, 50, 30, 40), (42, 48, 33, 41)])]
    base = [write(tmp_path, "b.json", result((30, 40, 20, 30)))]
    out = ev.summarize(runs, label="v3 before 3b", baseline_json=base, baseline_label="R9hg_it1100")
    assert out["criteria"] == "v3_ground_only" and out["split"] == "HELDOUT" and out["n"] == 256 and out["label"] == "v3 before 3b"
    assert out["pass_counts"] == [160, 164] and out["mean_pass"] == 162 and out["min_pass"] == 160 and out["max_pass"] == 164
    assert out["by_direction_mean"] == {"side_a": 41, "prone": 49, "side_b": 31.5, "supine": 40.5}
    assert out["runs"][0]["by_direction"] == dict(zip(DIRS, (40, 50, 30, 40)))
    assert out["baseline"]["label"] == "R9hg_it1100" and out["baseline"]["mean_pass"] == 120 and out["baseline"]["delta_mean_pass"] == 42
    assert out["baseline"]["delta_by_direction"]["side_b"] == 11.5


@pytest.mark.parametrize("name, expected", [("v21", 160), ("v2", 160), ("v3_a4", 160), ("v3_latest_substep", 160), ("v3", 160)])
def test_criteria_paths(tmp_path, name, expected):
    out = ev.summarize([write(tmp_path, "a.json", result((40, 50, 30, 40)))], criteria=name)
    assert out["pass_counts"] == [expected] and out["runs"][0]["by_direction"]["prone"] == 50 and "warnings" not in out


def test_strict_uses_rates_times_direction_n(tmp_path):
    out = ev.summarize([write(tmp_path, "a.json", result((1, 1, 1, 1)))], criteria="strict")
    assert out["pass_counts"] == [250] and out["runs"][0]["by_direction"]["side_a"] == 62
    assert "per-direction passes sum to 248" in out["warnings"][0]  # 0.97 * 64 rounds to 62 each: surfaced, not hidden


@pytest.mark.parametrize("pose_set", ["test_v2.npz", "/x/sets/TEST_v9.npz"])
def test_test_split_refused_unless_allowed(tmp_path, pose_set, monkeypatch):
    monkeypatch.setenv(ev.TEST_ENV, "1")
    f = write(tmp_path, "t.json", result((1, 2, 3, 4), pose_set=pose_set))
    with pytest.raises(ValueError, match="TEST split"):
        ev.summarize([f])
    with pytest.raises(ValueError, match="TEST split"):
        ev.summarize([write(tmp_path, "ok.json", result((1, 2, 3, 4)))], baseline_json=[f])
    assert ev.summarize([f], allow_test=True)["split"] == "TEST"


def test_dev_files_map_to_dev(tmp_path):
    assert ev.summarize([write(tmp_path, "d.json", result((1, 2, 3, 4), pose_set="dev_v1.npz"))])["split"] == "DEV"


@pytest.mark.parametrize("mutate, text", [
    (lambda d: d.pop("pose_set"), "no 'pose_set'"),
    (lambda d: d.update(pose_set="mystery.npz"), "split is unknown"),
    (lambda d: d["amendment3"].pop("ground_only_flight"), "expected key amendment3.ground_only_flight.v3_before_selfpen"),
    (lambda d: d["by_category"].pop("prone"), "direction 'prone'"),
    (lambda d: d.pop("num_envs"), "expected key num_envs"),
])
def test_layout_problems_fail_clearly(tmp_path, mutate, text):
    d = result((1, 2, 3, 4))
    mutate(d)
    with pytest.raises(ValueError, match=text):
        ev.summarize([write(tmp_path, "x.json", d)])


def test_mixed_splits_sizes_and_baseline_mismatch(tmp_path):
    a = write(tmp_path, "a.json", result((1, 2, 3, 4)))
    dev = write(tmp_path, "d.json", result((1, 2, 3, 4), pose_set="dev_v1.npz"))
    small = write(tmp_path, "s.json", result((1, 2, 3, 4), n_dir=32))
    with pytest.raises(ValueError, match="mixes splits"):
        ev.summarize([a, dev])
    with pytest.raises(ValueError, match="different pose counts"):
        ev.summarize([a, small])
    with pytest.raises(ValueError, match="different split"):
        ev.summarize([a], baseline_json=[dev])


@pytest.mark.parametrize("args", [{"eval_json": []}, {"eval_json": ["/nope.json"]}, {"eval_json": ["a.txt"]}, {"eval_json": [""]},
                                  {"eval_json": None}])
def test_bad_inputs(tmp_path, args):
    with pytest.raises(ValueError):
        ev.summarize(**args)
    with pytest.raises(ValueError, match="unknown criteria"):
        ev.summarize([write(tmp_path, "a.json", result((1, 2, 3, 4)))], criteria="v9")


def test_tool_layer(tmp_path):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("t")
    tools.register_isaaclab_tools(server)
    t = server._tool_manager._tools["isaaclab__metrics_summary"]
    assert t.annotations.readOnlyHint is True
    f = write(tmp_path, "a.json", result((40, 50, 30, 40)))
    ok = json.loads(t.fn(eval_json=[f], label="x"))
    assert ok["mean_pass"] == 160
    assert "TEST split" in json.loads(t.fn(eval_json=[write(tmp_path, "t.json", result((1, 2, 3, 4), pose_set="test_v2.npz"))]))["error"]
    assert "do not combine" in json.loads(t.fn(eval_json=[f], job_id="20261102-100000-T"))["error"]
    assert "only with eval_json" in json.loads(t.fn(run_dir="/x", label="x"))["error"]


def test_relative_path_error_says_where_it_looked_and_to_use_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match=r"looked for .*nope\.json.*working directory.*absolute path"):
        ev.summarize(["nope.json"])


def test_allow_test_alone_is_not_enough_without_the_operator_setting(tmp_path, monkeypatch):
    monkeypatch.delenv(ev.TEST_ENV, raising=False)
    f = write(tmp_path, "t.json", result((1, 2, 3, 4), pose_set="test_v2.npz"))
    with pytest.raises(ValueError, match=r"allow_test=true is not enough.*ISAAC_MCP_ALLOW_TEST_SPLIT=1.*cannot grant itself"):
        ev.summarize([f], allow_test=True)
    monkeypatch.setenv(ev.TEST_ENV, "yes")
    with pytest.raises(ValueError, match="not enough"):
        ev.summarize([f], allow_test=True)


def test_allowed_test_split_is_marked_in_the_result(tmp_path, monkeypatch):
    monkeypatch.setenv(ev.TEST_ENV, "1")
    f = write(tmp_path, "t.json", result((1, 2, 3, 4), pose_set="test_v2.npz"))
    out = ev.summarize([f], allow_test=True)
    assert out["test_split_used"] is True and "do not tune" in out["warning"]
    assert "test_split_used" not in ev.summarize([write(tmp_path, "h.json", result((1, 2, 3, 4)))])

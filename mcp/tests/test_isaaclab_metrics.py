import os
import time

import pytest


@pytest.fixture(autouse=True)
def _allow_tmp(monkeypatch):
    monkeypatch.setenv("ISAACLAB_ALLOW_TMP", "1")

from isaac_mcp.isaaclab import metrics


def _make_event_file(d, tag="loss", values=None):
    if values is None:
        values = [1.0, 0.5, 0.25]
    from tensorboard.summary.writer.event_file_writer import EventFileWriter
    from tensorboard.compat.proto import event_pb2, summary_pb2

    w = EventFileWriter(str(d))
    for i, v in enumerate(values):
        ev = event_pb2.Event(wall_time=time.time(), step=i)
        val = summary_pb2.Summary.Value(tag=tag, simple_value=float(v))
        ev.summary.value.append(val)
        w.add_event(ev)
    w.close()
    return d


def test_checkpoints_step_parsing_and_sorting(tmp_path):
    (tmp_path / "model_150.pt").write_bytes(b"x")
    (tmp_path / "epoch_12.pth").write_bytes(b"x")
    (tmp_path / "last_best.pt").write_bytes(b"x")
    # older mtime for model_150
    os.utime(tmp_path / "model_150.pt", (1000, 1000))
    os.utime(tmp_path / "epoch_12.pth", (2000, 2000))
    os.utime(tmp_path / "last_best.pt", (3000, 3000))

    res = metrics.checkpoints_list(run_dir=str(tmp_path))
    assert "checkpoints" in res
    names = [os.path.basename(c["path"]) for c in res["checkpoints"]]
    assert names == ["last_best.pt", "epoch_12.pth", "model_150.pt"]
    by_name = {os.path.basename(c["path"]): c for c in res["checkpoints"]}
    assert by_name["model_150.pt"]["step"] == 150
    assert by_name["epoch_12.pth"]["step"] == 12
    assert by_name["last_best.pt"]["step"] is None
    assert all("size_bytes" in c and "mtime" in c for c in res["checkpoints"])


def test_checkpoints_cap_at_200(tmp_path):
    for i in range(250):
        (tmp_path / f"model_{i}.pt").write_bytes(b"x")
    res = metrics.checkpoints_list(run_dir=str(tmp_path))
    assert len(res["checkpoints"]) == 200


def test_checkpoints_task_filter(tmp_path):
    sub = tmp_path / "taskA"
    sub.mkdir()
    (sub / "model_1.pt").write_bytes(b"x")
    other = tmp_path / "taskB"
    other.mkdir()
    (other / "model_2.pt").write_bytes(b"x")
    res = metrics.checkpoints_list(run_dir=str(tmp_path), task="taskA")
    assert len(res["checkpoints"]) == 1
    assert "taskA" in res["checkpoints"][0]["path"]


def test_checkpoints_search_roots_default_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ISAACLAB_LOGS_DIR", str(tmp_path))
    (tmp_path / "m.pt").write_bytes(b"x")
    res = metrics.checkpoints_list()
    assert len(res["checkpoints"]) == 1


def test_checkpoints_default_searches_install_logs_and_jobs(tmp_path, monkeypatch):
    root = tmp_path / "IsaacLab"
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    logs = root / "logs"
    logs.mkdir()
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    (logs / "model_1.pt").touch()
    (jobs_dir / "model_2.pt").touch()
    monkeypatch.delenv("ISAACLAB_LOGS_DIR", raising=False)
    monkeypatch.delenv("ISAACLAB_PYTHON", raising=False)
    monkeypatch.setenv("ISAACLAB_ROOT", str(root))
    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(jobs_dir))
    assert {os.path.basename(c["path"]) for c in metrics.checkpoints_list()["checkpoints"]} == {
        "model_1.pt", "model_2.pt"
    }
    assert metrics._resolve_run_dir(str(jobs_dir))[1] is None


def test_checkpoints_default_without_install_uses_jobs(tmp_path, monkeypatch):
    monkeypatch.delenv("ISAACLAB_PYTHON", raising=False)
    monkeypatch.delenv("ISAACLAB_ROOT", raising=False)
    monkeypatch.delenv("ISAACLAB_LOGS_DIR", raising=False)
    monkeypatch.setenv("ISAACLAB_JOBS_DIR", str(tmp_path))
    (tmp_path / "model.pt").touch()
    assert len(metrics.checkpoints_list()["checkpoints"]) == 1


def test_path_escape_refusal(tmp_path):
    # run_dir outside allowed roots
    res = metrics.metrics_summary(run_dir="/etc")
    assert "error" in res
    res2 = metrics.tensorboard_tail(run_dir="/etc", tag="x")
    assert "error" in res2
    res3 = metrics.checkpoints_list(run_dir="/etc")
    assert "error" in res3


def test_path_escape_symlink(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    # symlink pointing outside temp root -> /etc
    (root / "escape").symlink_to("/etc")
    # set allowed roots to root
    import os as _os
    # Use checkpoints_list with run_dir=root/escape -> realpath outside root, but allowed roots include default not root.
    # Instead validate via _resolve_run_dir by passing search_roots
    r = metrics._resolve_run_dir(str(root / "escape"), search_roots=[str(root)])
    assert r[1] is not None and "error" in r[1]


def test_metrics_summary(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    _make_event_file(d, tag="loss", values=[1.0, 0.5, 0.25])
    _make_event_file(d, tag="reward", values=[0.0, 1.0])
    res = metrics.metrics_summary(run_dir=str(d))
    assert "scalars" in res
    assert "loss" in res["scalars"]
    s = res["scalars"]["loss"]
    assert s["last"] == 0.25
    assert s["min"] == 0.25
    assert s["max"] == 1.0
    assert s["step"] == 2
    assert s["count"] == 3


def test_metrics_summary_tags_filter(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    _make_event_file(d, tag="loss", values=[1.0, 0.5])
    res = metrics.metrics_summary(run_dir=str(d), tags=["loss"])
    assert "loss" in res["scalars"]


def test_metrics_summary_unknown_tag(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    _make_event_file(d, tag="loss", values=[1.0])
    res = metrics.metrics_summary(run_dir=str(d), tags=["nope"])
    assert res.get("error") == "unknown tag"
    assert "available" in res
    assert "loss" in res["available"]


def test_tensorboard_tail(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    _make_event_file(d, tag="loss", values=[1.0, 0.5, 0.25, 0.1])
    res = metrics.tensorboard_tail(run_dir=str(d), tag="loss", last_n=2)
    assert "points" in res
    assert len(res["points"]) == 2
    assert res["points"][-1]["value"] == pytest.approx(0.1)
    assert all(set(p.keys()) == {"step", "value", "wall_time"} for p in res["points"])


def test_tensorboard_tail_unknown_tag(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    _make_event_file(d, tag="loss", values=[1.0])
    res = metrics.tensorboard_tail(run_dir=str(d), tag="nope")
    assert res.get("error") == "unknown tag"
    assert "available" in res


def test_missing_tensorboard(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "tensorboard", None)
    # force reimport failure
    monkeypatch.setitem(sys.modules, "tensorboard.backend", None)
    monkeypatch.setitem(sys.modules, "tensorboard.backend.event_processing", None)
    monkeypatch.setitem(sys.modules, "tensorboard.backend.event_processing.event_accumulator", None)
    d = "/tmp"
    res = metrics.metrics_summary(run_dir=d)
    # path may be outside allowed roots -> error anyway; use allowed root instead
    # Use tmp under home? Just check import error path by calling _import directly
    ea, err = metrics._import_event_accumulator()
    assert err is not None
    assert "isaaclab' extra" in err["error"]


def test_missing_tensorboard_tail(monkeypatch):
    import sys
    for m in ["tensorboard", "tensorboard.backend", "tensorboard.backend.event_processing",
              "tensorboard.backend.event_processing.event_accumulator"]:
        monkeypatch.setitem(sys.modules, m, None)
    ea, err = metrics._import_event_accumulator()
    assert err is not None


def test_last_n_default_and_zero(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    _make_event_file(d, tag="loss", values=[1.0, 2.0, 3.0])
    res = metrics.tensorboard_tail(run_dir=str(d), tag="loss")
    assert len(res["points"]) == 3
    res2 = metrics.tensorboard_tail(run_dir=str(d), tag="loss", last_n=0)
    assert len(res2["points"]) == 3

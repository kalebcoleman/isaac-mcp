"""status's `extension` object: commit and dirty state from git's own files, and the reload log."""

import os
import subprocess
from pathlib import Path

import pytest

from isaac_mcp import server as gw
from isaac_mcp_bridge import source_state as state

REPO = Path(__file__).resolve().parents[2]


def git(root, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=root, check=True,
                   capture_output=True, text=True)


def old_mtime(path):
    os.utime(path, (1_000_000_000, 1_000_000_000))


@pytest.fixture()
def tree(tmp_path):
    root = tmp_path / "tree"
    (root / "exts/sim-tools").mkdir(parents=True)
    (root / "mcp/isaac_mcp").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "exts/sim-tools/a.py").write_text("a = 1\n")
    (root / "mcp/isaac_mcp/c.py").write_text("c = 1\n")
    (root / "docs/x.md").write_text("doc\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-qm", "one")
    return root


def head(root):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()


def test_clean_tree_reports_commit_and_not_dirty(tree):
    report = state.report(tree, head(tree))
    assert report["commit"] == report["checkout_commit"] == head(tree)
    assert report["dirty"] is False and "dirty_paths" not in report and "warning" not in report


def test_edit_in_an_extension_is_dirty_and_named(tree):
    (tree / "exts/sim-tools/a.py").write_text("a = 2\n")
    report = state.report(tree, head(tree))
    assert report["dirty"] is True and report["dirty_paths"] == ["exts/sim-tools/a.py"] and report["dirty_count"] == 1


def test_same_size_edit_with_a_new_mtime_is_dirty(tree):
    (tree / "mcp/isaac_mcp/c.py").write_text("c = 2\n")
    assert state.dirty_paths(tree) == ["mcp/isaac_mcp/c.py"]


def test_touching_a_file_without_changing_it_is_not_dirty(tree):
    old_mtime(tree / "exts/sim-tools/a.py")
    assert state.dirty_paths(tree) == []


def test_deleted_tracked_file_is_dirty(tree):
    (tree / "exts/sim-tools/a.py").unlink()
    assert state.dirty_paths(tree) == ["exts/sim-tools/a.py"]


def test_edits_outside_the_extension_tree_do_not_count(tree):
    (tree / "docs/x.md").write_text("changed\n")
    assert state.dirty_paths(tree) == []


def test_export_with_no_git_reports_unknown_not_an_error(tmp_path):
    (tmp_path / "exts").mkdir()
    report = state.report(tmp_path, None)
    assert report["commit"] == report["checkout_commit"] == "unknown" and report["dirty"] is None


def test_index_format_it_does_not_parse_is_unknown(tree):
    git(tree, "update-index", "--index-version", "4")
    assert state.dirty_paths(tree) is None


def test_v3_index_with_intent_to_add_parses(tree):
    (tree / "exts/new.py").write_text("n = 1\n")
    git(tree, "add", "-N", "exts/new.py")   # sets an extended flag, so the index becomes version 3
    assert state.dirty_paths(tree) == ["exts/new.py"]


def test_worktree_git_file_and_reader_agree_with_the_host(tree, tmp_path):
    wt = tmp_path / "wt"
    git(tree, "worktree", "add", "-q", str(wt))
    assert state.read_head(wt) == gw._read_git_head(wt) == head(tree)
    assert state.dirty_paths(wt) == []
    assert state.read_head(tree) == gw._read_git_head(tree)


def test_moved_checkout_warns_that_kit_runs_a_mix(tree):
    report = state.report(tree, "a" * 40)
    assert report["commit"] == "a" * 40 and "restart Kit" in report["warning"]


def test_never_starts_a_process(tree, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("status runs on Kit's main thread")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    assert state.report(tree, None)["dirty"] is False


def test_status_builtin_reports_extension():
    from isaac_mcp_bridge.bridge import McpBridge
    from isaac_mcp_bridge.builtins import cmd_status

    assert cmd_status(McpBridge(schedule_coroutine=lambda coro: None))["extension"]["root"] == str(REPO)


def test_reload_log_counts_restarts_and_keeps_facts(monkeypatch):
    monkeypatch.setattr(state, "_off_kit", {})
    assert state.note_start("x")["reload"] == 0
    assert state.reload_report() == {}
    state.note_stop("x", session_ids=["old"])
    started = state.note_start("x")
    assert started["reload"] == 1 and started["session_ids"] == ["old"]
    assert state.reload_report()["x"]["count"] == 1


# ---- the gateway's comparison ----


@pytest.mark.parametrize("host,bridge,match", [
    ("a" * 40, "a" * 40, True), ("a" * 40, "b" * 40, False), ("a" * 40, "unknown", None), ("unknown", "a" * 40, None),
])
def test_gateway_compares_commits(host, bridge, match):
    identity = {"commit": host}
    gw._compare_with_bridge(identity, {"commit": bridge, "dirty": False})
    assert identity["bridge_match"] is match
    assert ("warning" in identity) is (match is False)


def test_gateway_warns_about_a_dirty_bridge_and_keeps_its_own_warning():
    identity = {"commit": "a" * 40, "warning": "stale host."}
    gw._compare_with_bridge(identity, {"commit": "a" * 40, "dirty": True, "dirty_count": 3})
    assert identity["bridge_match"] is True
    assert identity["warning"].startswith("stale host.") and "3 uncommitted" in identity["warning"]


def test_gateway_with_a_bridge_too_old_to_report_cannot_compare():
    identity = {"commit": "a" * 40}
    gw._compare_with_bridge(identity, {})
    assert identity["bridge_match"] is None and "warning" not in identity


def test_status_through_the_host_includes_the_comparison(monkeypatch):
    monkeypatch.setattr(gw, "_HOST_CHECKOUT", None)
    monkeypatch.setattr(gw, "_HOST_COMMIT", None)
    identity = gw._host_identity({"commit": "a" * 40, "dirty": False})
    assert identity["bridge_match"] is None

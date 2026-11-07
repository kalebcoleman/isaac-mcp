import subprocess
import sys
from pathlib import Path

from isaac_mcp import screen
import isaac_mcp.server as gw

ROOT = Path(__file__).resolve().parents[2]


def test_label_tag_is_filename_safe():
    assert screen._label_tag(None) == "" and screen._label_tag("") == ""
    assert screen._label_tag("push done/../x") == "-push_done____x"
    assert len(screen._label_tag("a" * 100)) == 41


def test_instructions_tell_agents_to_prefer_the_tools():
    assert "Prefer these tools" in gw.SERVER_INSTRUCTIONS and "say why" in gw.SERVER_INSTRUCTIONS


def test_session_check_never_fails_and_nudges():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "session_check.py"), "--port", "1"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.startswith("isaac MCP:") and "mcp__isaac" in r.stdout


def test_isaac_help_routes_by_task_and_falls_back():
    one = gw._isaac_help("why did the grasp fail")
    assert "sim__physics__contacts" in one and "isaaclab__train" not in one
    assert "isaaclab__train" in gw._isaac_help("train a policy")
    both = gw._isaac_help("something odd")
    assert "No topic matched" in both and "screenshot" in both and "isaaclab__train" in both
    assert "sim__launch" in gw._isaac_help(None)

"""Exercise audit and enforcement only against a temporary tracked tree."""

import json
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_names.py"


def run(root, *args):
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "--json", *args],
                          capture_output=True, text=True)


def test_audit_and_enforce(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    source = tmp_path / "isaac-mcp.txt"
    source.write_text("ISAAC_MCP_ASSET_ROOT ISAAC_MCP_OTHER\nHusarion Newton is experimental\n")
    (tmp_path / "ignored.lock").write_text("isaac_mcp")
    (tmp_path / "binary.bin").write_bytes(b"\0isaac_mcp")
    (tmp_path / "untracked.txt").write_text("isaac_mcp")
    subprocess.run(["git", "add", "isaac-mcp.txt", "ignored.lock", "binary.bin"], cwd=tmp_path, check=True)
    result = run(tmp_path)
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["patterns"]["ISAAC_MCP"]["content_hits"] == 2
    assert report["patterns"]["ISAAC_MCP"]["areas"] == {"root files": 2}
    assert report["patterns"]["isaac-mcp"]["tracked_paths"] == 1
    assert report["patterns"]["isaac_mcp"]["content_hits"] == 0
    assert report["environment_names"]["ISAAC_MCP"]["ISAAC_MCP_OTHER"] == 1
    allowlist = tmp_path / "allow.toml"
    allowlist.write_text('[[allow]]\npattern="ISAAC_MCP"\npath="isaac-mcp.txt"\nliteral="ISAAC_MCP_ASSET_ROOT"\nreason="Permanent alias"\n')
    result = run(tmp_path, "--enforce", "--allowlist", str(allowlist))
    assert result.returncode == 1
    errors = json.loads(result.stdout)["errors"]
    assert any("isaac-mcp.txt:1: ISAAC_MCP: ISAAC_MCP_OTHER" in error for error in errors)
    assert not any("ISAAC_MCP_ASSET_ROOT" in error for error in errors)
    assert any("isaac-mcp.txt:0:" in error for error in errors)
    source.write_text("ISAAC_MCP_ASSET_ROOT\n")
    with allowlist.open("a") as stream:
        stream.write('[[allow]]\npattern="isaac-mcp"\npath="isaac-mcp.txt"\nreason="Fixture path"\nremove_by="2.0"\n')
    assert run(tmp_path, "--enforce", "--allowlist", str(allowlist)).returncode == 0
    source.write_text("clean\n")
    result = run(tmp_path, "--enforce", "--allowlist", str(allowlist))
    assert result.returncode == 1
    assert "matches nothing" in result.stdout

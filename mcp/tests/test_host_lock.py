"""The host lock is what install.sh syncs; the simulator lock stays on the 6.2 stack.

``uv tool install`` does not read ``uv.lock``. These tests guard the files that
``uv sync --locked`` and ``uv lock --check`` actually use.
"""

import os
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


def _load_toml(path):
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def _requirement(requirements, name):
    found = [Requirement(spec) for spec in requirements if Requirement(spec).name == name]
    assert len(found) == 1, f"expected one {name!r} requirement, found {found}"
    return found[0]


def _lock_versions(path):
    """Package name to the set of versions named in a uv.lock."""
    versions = {}
    name = None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("name = "):
            name = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("version = ") and name:
            versions.setdefault(name, set()).add(line.split("=", 1)[1].strip().strip('"'))
            name = None
    return versions


# Exact versions in the simulator lock. Local CUDA tags are part of the pin.
SIM_PINS = {
    "isaacsim": "6.2.0.0",
    "torch": "2.11.0+cu130",
    "torchvision": "0.26.0+cu130",
    "numpy": "2.3.1",
}

HOST_INSTALL_COMMAND = "uv sync --project mcp --locked --extra isaaclab --no-dev"
HOST_INSTALL_DOCS = (
    "docs/LINUX-SETUP.md",
    "docs/MCP-SERVER.md",
    "mcp/README.md",
    "docs/TOOLS-ISAACLAB.md",
)


def test_simulator_lock_keeps_the_6_2_stack_and_has_no_mcp_sdk():
    project = _load_toml(ROOT / "pyproject.toml")["project"]
    names = [Requirement(spec).name for spec in project["dependencies"]]
    assert "mcp" not in names
    versions = _lock_versions(ROOT / "uv.lock")
    assert "mcp" not in versions
    for name, version in SIM_PINS.items():
        assert versions.get(name) == {version}


def test_host_lock_satisfies_the_sdk_range_and_records_install_extras():
    project = _load_toml(ROOT / "mcp" / "pyproject.toml")["project"]
    mcp_req = _requirement(project["dependencies"], "mcp")
    httpx_req = _requirement(project["dependencies"], "httpx")
    versions = _lock_versions(ROOT / "mcp" / "uv.lock")
    assert versions["mcp"]
    for version in versions["mcp"]:
        assert mcp_req.specifier.contains(Version(version))
        assert Version(version) >= Version("1.22.0")
        assert Version(version) < Version("2")
    for version in versions["httpx"]:
        assert httpx_req.specifier.contains(Version(version))
    # Extras the locked install and the CI suite select. Their absence means
    # `uv sync --locked --extra ...` would have to relock.
    for extra in ("tensorboard", "pytest", "usd-core"):
        assert extra in versions
    header = (ROOT / "mcp" / "uv.lock").read_text(encoding="utf-8").split("[package]", 1)[0]
    assert 'prerelease-mode = "allow"' not in header


def test_documented_checkout_install_uses_the_locked_host_sync():
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'UV_PROJECT_ENVIRONMENT="$MCP_DIR/.venv"' in install
    assert 'uv sync --project "$MCP_DIR" --locked --extra isaaclab --no-dev' in install
    assert "uv tool install --force --editable" not in install
    for rel in HOST_INSTALL_DOCS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert HOST_INSTALL_COMMAND in text, rel
        assert "uv tool install --force --editable" not in text, rel
    skill_paths = [ROOT / ".agents/skills/isaac-mcp/SKILL.md"]
    claude_skill = ROOT / ".claude/skills/isaac-mcp/SKILL.md"
    # Some checkouts hardlink the Claude skill at the agents path. Require the
    # tracked agents skill, and the Claude path only when it is a separate file.
    if claude_skill.exists() and claude_skill.resolve() != skill_paths[0].resolve():
        skill_paths.append(claude_skill)
    for path in skill_paths:
        text = path.read_text(encoding="utf-8")
        assert "uv tool install --force --editable" not in text, path
        assert "./install.sh --no-sim" in text, path


def test_minimum_sdk_job_does_not_use_the_host_lock():
    """The floor pin is installed with --no-project so mcp/uv.lock cannot override it."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--with 'mcp[cli]==1.22.0'" in workflow
    assert "--no-project" in workflow
    assert "uv run --locked" in workflow

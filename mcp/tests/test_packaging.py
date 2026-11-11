"""The package's declared dependencies are ones this code runs with."""

from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

try:
    import tomllib
except ModuleNotFoundError:      # Python 3.10
    import tomli as tomllib

MCP_DIR = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((MCP_DIR / "pyproject.toml").read_text())["project"]


def requirement(specs, name):
    (found,) = [Requirement(spec) for spec in specs if Requirement(spec).name == name]
    return found


def test_the_server_under_test_is_this_checkout_s():
    import isaac_mcp.server as server
    from mcp.server.fastmcp import FastMCP

    assert Path(server.__file__).resolve().is_relative_to(MCP_DIR)
    assert isinstance(server.mcp, FastMCP)


def test_the_mcp_sdk_range_is_the_tested_one():
    """1.22 is the oldest SDK that works with current Pydantic; 2.x has no mcp.server.fastmcp."""
    mcp = requirement(PROJECT["dependencies"], "mcp")
    assert "cli" in mcp.extras
    assert mcp.specifier.contains(Version("1.22.0")) and not mcp.specifier.contains(Version("1.21.0"))
    assert not mcp.specifier.contains(Version("2.0.0"), prereleases=True) and not mcp.specifier.contains(Version("2.1.1"))
    httpx = requirement(PROJECT["dependencies"], "httpx")
    assert httpx.specifier.contains(Version("0.28.1")) and not httpx.specifier.contains(Version("1.0.0"))
    assert not httpx.specifier.contains(Version("1.0.dev6"), prereleases=True)


def test_the_test_extra_installs_pytest():
    assert requirement(PROJECT["optional-dependencies"]["test"], "pytest")

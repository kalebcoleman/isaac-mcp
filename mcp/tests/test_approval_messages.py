"""Approval refusals end in one instruction the user can follow, and host and Kit report when they disagree on the project."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from isaac_mcp import asset_packs as packs
from isaac_mcp import project_assets as a
from isaac_mcp import sim_sessions as sim


def _printed_argv(text):
    """The arguments of the command on its own line in the message, as a shell would split them."""
    import shlex

    line = next(x for x in text.splitlines() if "isaac_mcp.project_assets" in x)
    return shlex.split(line.strip())[3:]  # after: "<python>" -m isaac_mcp.project_assets


def test_the_approval_command_is_filled_in(monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_ASSET_CONFIG", "/srv/isaac/approval.json")
    text = a.approval_help("/work/projects/arm demo/assets")
    assert "--config /srv/isaac/approval.json" in text
    assert "--root '/work/projects/arm demo/assets'" in text and "--root-id arm-demo-assets" in text  # one shell word
    assert "ask the user to run" in text and "an agent cannot" in text
    assert "reconnect the MCP server and restart Kit" in text.replace("Then reconnect", "reconnect")  # both read the file at startup
    assert "smallest folder" in text and "1000 files" in text  # a large parent fails with LIMIT: do not advise it
    assert "covers every project" not in text
    assert "--trust-project" in text  # the helper exits with an error without it
    assert "--staging" in text  # the file does not exist yet, and the helper needs staging to create it


def test_an_existing_approval_file_needs_no_staging(tmp_path, monkeypatch):
    config = tmp_path / "approval.json"
    config.write_text("{}")
    monkeypatch.setenv("ISAAC_MCP_ASSET_CONFIG", str(config))
    text = a.approval_help("/work/projects/demo")
    assert "--trust-project" in text and "--staging" not in text


def test_the_printed_command_is_accepted_by_the_helper(tmp_path, monkeypatch):
    """Run the command the refusal prints (as its argv): the helper must not reject it."""
    import shlex

    config = tmp_path / "private" / "approval.json"
    monkeypatch.setenv("ISAAC_MCP_ASSET_CONFIG", str(config))
    monkeypatch.setenv("ISAAC_MCP_STAGING", str(tmp_path / "private" / "staging"))
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    text = a.approval_help(str(tmp_path / "projects" / "demo" / "robot.urdf"))
    argv = _printed_argv(text)
    config.parent.mkdir(mode=0o700)
    assert a.main(argv) in (0, None)  # parser.error would raise SystemExit
    assert config.exists()


def test_a_file_is_turned_into_the_folder_to_approve(monkeypatch):
    monkeypatch.delenv("ISAAC_MCP_ASSET_CONFIG", raising=False)
    text = a.approval_help("/work/projects/demo/assets/robot.urdf")
    assert "--root /work/projects/demo/assets " in text and "robot.urdf --" not in text and "--root-id demo-assets" in text
    assert "<a private approval.json" in text  # no config yet: it says what to create, never a guessed path


def test_without_a_folder_the_command_has_a_placeholder(monkeypatch):
    monkeypatch.delenv("ISAAC_MCP_ASSET_CONFIG", raising=False)
    assert "--root <the folder>" in a.approval_help(None)


def test_a_root_id_is_made_safe(monkeypatch):
    monkeypatch.delenv("ISAAC_MCP_ASSET_CONFIG", raising=False)
    assert "--root-id my-weird-name" in a.approval_help("/x/my weird;name")


def test_the_refusals_for_an_unapproved_path_carry_the_command(tmp_path, monkeypatch):
    monkeypatch.delenv("ISAAC_MCP_ASSET_CONFIG", raising=False)
    broker = a.Broker()
    outside = tmp_path / "proj" / "assets" / "robot.urdf"
    outside.parent.mkdir(parents=True)
    outside.write_text("<robot/>")
    with pytest.raises(a.AssetError) as error:
        broker.locate(str(outside), "urdf")
    assert error.value.code == "ROOT_NOT_APPROVED" and f"--root {outside.parent}" in str(error.value)


def test_pack_tools_refuse_with_the_command_too(tmp_path, monkeypatch):
    monkeypatch.delenv("ISAAC_MCP_ASSET_CONFIG", raising=False)
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(tmp_path / "store"))
    (tmp_path / "outside").mkdir()
    with pytest.raises(packs.PackError) as error:
        packs.run_tool("assets.pack_adopt", dict(pack="mine", source_path=str(tmp_path / "outside")), a.Broker())
    assert error.value.code == "ROOT_NOT_APPROVED" and "ask the user to run" in str(error.value)


# ---- host and Kit seeing the same project -------------------------------------------------------------------------

HOST = dict(project_file="/p/isaac-mcp.json", stores=["/p/assets", "/w/store"], strict=False)


def test_no_warning_when_host_and_kit_agree():
    assert packs.environment_warnings(HOST, dict(HOST)) == []


def test_a_kit_that_does_not_see_the_project_is_named():
    warnings = packs.environment_warnings(HOST, dict(HOST, project_file=None, stores=["/w/store"]))
    assert any("project file" in w and "sim__launch" in w and "ISAAC_MCP_PROJECT" in w for w in warnings)
    assert any("stores" in w for w in warnings)


def test_strict_differences_are_reported():
    assert any("Strict mode" in w for w in packs.environment_warnings(HOST, dict(HOST, strict=True)))


def test_packs_lists_a_warning_when_kit_disagrees(tmp_path, monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(tmp_path / "store"))
    monkeypatch.delenv("ISAAC_MCP_PROJECT", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "store").mkdir()
    kit = lambda tool, args: {"asset_environment": dict(project_file="/elsewhere/isaac-mcp.json", stores=["/other"], strict=True)}
    out = packs.run_tool("assets.packs", {}, a.Broker(), kit_call=kit)
    assert out["kit_environment"]["project_file"] == "/elsewhere/isaac-mcp.json" and len(out["warnings"]) == 3


def test_packs_without_a_kit_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(tmp_path / "store"))
    (tmp_path / "store").mkdir()
    out = packs.run_tool("assets.packs", {}, a.Broker())
    assert "warnings" not in out and "kit_environment" not in out
    broken = packs.run_tool("assets.packs", {}, a.Broker(), kit_call=lambda t, x: (_ for _ in ()).throw(OSError("no bridge")))
    assert "warnings" not in broken  # no bridge is the ordinary case, not a warning


def test_a_kit_that_predates_the_report_gives_no_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("ISAAC_MCP_ASSET_PATH", str(tmp_path / "store"))
    (tmp_path / "store").mkdir()
    out = packs.run_tool("assets.packs", {}, a.Broker(), kit_call=lambda t, x: {"status": "ok"})
    assert "warnings" not in out


def test_a_path_with_a_space_is_printed_as_one_shell_word_and_the_helper_accepts_it(tmp_path, monkeypatch):
    import shlex

    config = tmp_path / "my private" / "approval.json"
    monkeypatch.setenv("ISAAC_MCP_ASSET_CONFIG", str(config))
    monkeypatch.setenv("ISAAC_MCP_STAGING", str(tmp_path / "my private" / "staging"))
    folder = tmp_path / "arm demo" / "assets"
    folder.mkdir(parents=True)
    text = a.approval_help(str(folder / "robot.urdf"))
    argv = _printed_argv(text)
    assert str(config) in argv and str(folder) in argv  # each path survived as a single argument
    config.parent.mkdir(mode=0o700)
    assert a.main(argv) in (0, None) and config.exists()


def test_no_folder_gives_a_placeholder_and_a_sensible_root_id(monkeypatch):
    monkeypatch.delenv("ISAAC_MCP_ASSET_CONFIG", raising=False)
    text = a.approval_help(None)
    assert "--root-id project " in text and "--root-id None" not in text and "--root <the folder>" in text


def test_a_folder_named_with_a_dot_is_a_folder_not_a_file(tmp_path, monkeypatch):
    """`robots v1.2` and `repo.git` have a suffix but are folders: approve them, not their parent."""
    monkeypatch.delenv("ISAAC_MCP_ASSET_CONFIG", raising=False)
    folder = tmp_path / "my project" / "robots v1.2"
    folder.mkdir(parents=True)
    argv = _printed_argv(a.approval_help(str(folder)))
    assert argv[argv.index("--root") + 1] == str(folder)
    argv = _printed_argv(a.approval_help(str(tmp_path / "repo.git")))  # not on disk: not a known file suffix either
    assert argv[argv.index("--root") + 1] == str(tmp_path / "repo.git")
    (folder / "robot.urdf").write_text("<robot/>")
    argv = _printed_argv(a.approval_help(str(folder / "robot.urdf")))  # a file still names its folder
    assert argv[argv.index("--root") + 1] == str(folder)


@pytest.mark.parametrize("name", ["repo.git", "isaac_assets_v1.0", "x" * 80, "my robots (v2)"])
def test_the_root_id_is_one_the_approval_helper_accepts(tmp_path, monkeypatch, name):
    """Every printed command must run as printed: the helper takes only [A-Za-z0-9_-], 1 to 48 characters."""
    import re

    monkeypatch.setenv("ISAAC_MCP_ASSET_CONFIG", str(tmp_path / "private" / "approval.json"))
    monkeypatch.setenv("ISAAC_MCP_STAGING", str(tmp_path / "private" / "staging"))
    folder = tmp_path / name
    folder.mkdir()
    argv = _printed_argv(a.approval_help(str(folder)))
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,48}", argv[argv.index("--root-id") + 1])
    (tmp_path / "private").mkdir(mode=0o700)
    assert a.main(argv) in (0, None)


def test_the_command_has_no_stray_arguments_after_it(monkeypatch):
    """A comma after --trust-project made a copied command fail: the command ends its own line."""
    monkeypatch.delenv("ISAAC_MCP_ASSET_CONFIG", raising=False)
    line = next(x for x in a.approval_help("/work/p/assets").splitlines() if "isaac_mcp.project_assets" in x)
    assert line.rstrip().endswith("--trust-project")

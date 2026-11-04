"""Privacy regressions include compressed members, not only git grep."""

import gzip
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tarfile
import zipfile

import pytest

spec = importlib.util.spec_from_file_location(
    "private_paths",
    Path(__file__).resolve().parents[2] / "scripts/check_private_paths.py",
)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


def test_plain_and_json_escaped_paths():
    private = b"/home/" + b"example/private.txt"
    assert scan.inspect_bytes("log", private)
    assert scan.inspect_bytes("json", private.replace(b"/", b"\\/"))
    assert scan.inspect_bytes("worktree", b".t3" + b"/worktrees/project")
    assert not scan.inspect_bytes(
        "safe", b"<home>/Pictures/capture.png <worktree>/test.py"
    )


def test_nested_archives_and_metadata():
    output = io.BytesIO()
    private = b"/home/" + b"example/secret"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("log.json", private)
    nested = io.BytesIO()
    with tarfile.open(fileobj=nested, mode="w") as archive:
        data = output.getvalue()
        member = tarfile.TarInfo("nested.zip")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    assert scan.inspect_bytes("records.tar.gz", gzip.compress(nested.getvalue()))
    assert scan.inspect_bytes("metadata.zip", _zip(private.decode(), b"safe"))


def _zip(name, content):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(name, content)
    return output.getvalue()


def test_malformed_or_excessive_archive_fails_closed():
    assert scan.inspect_bytes("bad.gz", b"\x1f\x8bgarbage")
    assert scan.inspect_bytes("large.gz", gzip.compress(b"a" * 1000), budget=[100, 10])
    assert scan.inspect_bytes("deep", b"safe", depth=6)
    assert not scan.inspect_bytes("safe.zip", _zip("summary.txt", b"<sim-install>/kit"))


def test_opaque_screenshot_bytes_cannot_hide_in_transcripts():
    assert scan.inspect_bytes("transcript.json", b'{"data":"' + b"a" * 1200 + b'"}')


def test_workstation_identifiers_are_rejected_without_being_echoed():
    for identifier in scan.PRIVATE_IDENTIFIERS:
        for data in (b"x " + identifier + b" y", b"x " + identifier.upper() + b" y"):
            errors = scan.inspect_bytes("note.md", data)
            assert errors == ["note.md: private workstation identifier"]
    assert not scan.inspect_bytes("note.md", b"Measured on the lab PC, dhcp lease asu")


def test_workstation_identifiers_are_found_inside_archives():
    identifier = scan.PRIVATE_IDENTIFIERS[0]
    assert scan.inspect_bytes("bundle.zip", _zip("log.txt", identifier))
    assert scan.inspect_bytes("bundle.zip", _zip(identifier.decode() + ".txt", b"safe"))
    assert scan.inspect_bytes("log.gz", gzip.compress(identifier))


@pytest.mark.parametrize(
    "email",
    [
        "user@example.com",
        "kalebcoleman123@gmail.com",
        "noreply@github.com",
        "49699333+dependabot[bot]@users.noreply.github.com",
    ],
)
def test_real_addresses_are_valid(email):
    assert not scan.invalid_email(email)


@pytest.mark.parametrize(
    "email",
    [
        "user@corp.example.edu@host1.dhcp.example.edu",
        "user@host1.dhcp.example.edu",
        "user@dhcp.example.edu",
        "user@workstation",
        "user@workstation.local",
        "user@box.localdomain",
        "user@box.(none)",
        "user name@example.com",
        "@example.com",
        "user@",
        "",
    ],
)
def test_hostname_identities_are_invalid(email):
    assert scan.invalid_email(email)


def _commit(repo, message, *, author="user@example.com", committer="user@example.com", name="User"):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": author,
        "GIT_COMMITTER_NAME": "User",
        "GIT_COMMITTER_EMAIL": committer,
    }
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", message], cwd=repo, env=env, check=True
    )
    return subprocess.check_output(
        ["git", "log", "-1", "--format=%h"], cwd=repo, text=True
    ).strip()


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def test_commit_check_reports_each_bad_identity_by_role(repo):
    good = _commit(repo, "good")
    bad = "user@corp.example.edu@host1.dhcp.example.edu"
    author = _commit(repo, "bad author", author=bad)
    committer = _commit(repo, "bad committer", committer=bad)
    assert scan.check_commits(repo, "HEAD") == [
        f"{committer} committer: not a valid email address",
        f"{author} author: not a valid email address",
    ]
    assert scan.check_commits(repo, good) == []
    assert scan.check_commits(repo, "HEAD..HEAD") == []
    assert not any(bad in error for error in scan.check_commits(repo, "HEAD"))


def test_commit_check_finds_the_identifier_in_a_valid_looking_identity(repo):
    identifier = scan.PRIVATE_IDENTIFIERS[0].decode()
    named = _commit(repo, "name", name=identifier)
    mailed = _commit(repo, "mail", author=identifier + "@example.com")
    assert scan.check_commits(repo, "HEAD") == [
        f"{mailed} author: private workstation identifier",
        f"{named} author: private workstation identifier",
    ]


@pytest.mark.parametrize("revision_range", ["no-such-ref..HEAD", "--all"])
def test_commit_check_fails_closed_on_a_bad_range(repo, revision_range):
    _commit(repo, "good")
    errors = scan.check_commits(repo, revision_range)
    assert len(errors) == 1 and errors[0].startswith("could not read commit range")


def test_cli_selects_the_commit_check(monkeypatch, capsys):
    monkeypatch.setattr(scan, "check_commits", lambda root, revision_range: ["abc author: bad"])
    assert scan.main(["--commits", "main..HEAD"]) is True
    assert "abc author: bad" in capsys.readouterr().out
    monkeypatch.setattr(scan, "check_commits", lambda root, revision_range: [])
    assert scan.main(["--commits", "main..HEAD"]) is False
    assert "passed for main..HEAD" in capsys.readouterr().out

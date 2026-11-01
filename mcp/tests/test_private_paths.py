"""Privacy regressions include compressed members, not only git grep."""

import gzip
import importlib.util
import io
from pathlib import Path
import tarfile
import zipfile

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

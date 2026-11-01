"""Reject workstation identifiers in tracked bytes, including nested archives.

Read-only: archive members are never extracted. Bounded decompression fails closed.
Image pixels still require visual review; this only checks bytes and metadata.
"""

from __future__ import annotations

import gzip
import io
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile

HOME_PATH = re.compile(rb"/home/[A-Za-z0-9_.-]+(?:/|(?=[\s\"'<>]))")
WORKTREE_PATH = b".t3" + b"/worktrees"

MAX_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 10000
MAX_DEPTH = 5


def inspect_bytes(name: str, data: bytes, *, depth=0, budget=None):
    """Return non-sensitive location/error messages, never the matched value."""
    budget = budget if budget is not None else [MAX_BYTES, MAX_MEMBERS]
    budget[0] -= len(data)
    budget[1] -= 1
    if depth > MAX_DEPTH or min(budget) < 0:
        return [f"{name}: privacy scan limit exceeded"]
    errors = []
    if re.search(rb'"data"\s*:\s*"[A-Za-z0-9+/=]{1000}', data):
        errors.append(
            f"{name}: embedded opaque image/binary data must be a separately reviewed artifact"
        )
    # JSON slash escapes do not make an absolute workstation path public-safe.
    decoded = data.replace(b"\\/", b"/")
    if HOME_PATH.search(decoded) or WORKTREE_PATH in decoded:
        errors.append(f"{name}: private absolute home/worktree path")
    try:
        if data.startswith(b"\x1f\x8b"):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                content = stream.read(max(0, budget[0]) + 1)
            errors.extend(
                inspect_bytes(name + "!gzip", content, depth=depth + 1, budget=budget)
            )
        elif zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for member in archive.infolist():
                    errors.extend(
                        inspect_bytes(
                            name + "!member-name",
                            member.filename.encode(),
                            depth=depth + 1,
                            budget=budget,
                        )
                    )
                    if member.file_size > budget[0] or budget[1] <= 0:
                        raise ValueError("archive scan limit exceeded")
                    with archive.open(member) as stream:
                        content = stream.read(max(0, budget[0]) + 1)
                    errors.extend(
                        inspect_bytes(
                            name + "!member", content, depth=depth + 1, budget=budget
                        )
                    )
        elif len(data) >= 512 and (data[257:262] == b"ustar" or name.endswith(".tar")):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
                for member in archive:
                    metadata = (
                        member.name
                        + " "
                        + member.linkname
                        + " "
                        + str(member.pax_headers)
                    ).encode()
                    errors.extend(
                        inspect_bytes(
                            name + "!member-metadata",
                            metadata,
                            depth=depth + 1,
                            budget=budget,
                        )
                    )
                    if member.size > budget[0] or budget[1] <= 0:
                        raise ValueError("archive scan limit exceeded")
                    if member.isfile():
                        with archive.extractfile(member) as stream:
                            content = stream.read(max(0, budget[0]) + 1)
                        errors.extend(
                            inspect_bytes(
                                name + "!member",
                                content,
                                depth=depth + 1,
                                budget=budget,
                            )
                        )
    except (
        OSError,
        EOFError,
        ValueError,
        tarfile.TarError,
        zipfile.BadZipFile,
        RuntimeError,
    ):
        errors.append(f"{name}: archive could not be fully privacy-scanned")
    return errors


def check_tracked(root: Path):
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).split(b"\0")
    errors = []
    for raw in paths:
        if not raw:
            continue
        relative = raw.decode()
        path = root / relative
        if not path.exists():  # deleted by this change; CI scans the final checkout
            continue
        errors.extend(inspect_bytes("tracked filename", raw))
        if path.is_file():
            if path.stat().st_size > MAX_BYTES:
                errors.append(f"{relative}: file exceeds privacy scan limit")
            else:
                data = path.read_bytes()
                errors.extend(inspect_bytes(relative, data))
    return errors


if __name__ == "__main__":
    errors = check_tracked(Path(__file__).resolve().parents[1])
    print(
        "\n".join(errors)
        if errors
        else "Tracked-file privacy scan passed (including archives)."
    )
    raise SystemExit(bool(errors))

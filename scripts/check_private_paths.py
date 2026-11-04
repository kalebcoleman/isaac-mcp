"""Reject workstation identifiers in tracked bytes, including nested archives.

Read-only: archive members are never extracted. Bounded decompression fails closed.
Image pixels still require visual review; this only checks bytes and metadata.

With ``--commits <range>``, check that range's commit identities instead: every author
and committer needs a real email address, not one git derived from a machine's hostname.
"""

from __future__ import annotations

import argparse
import gzip
import io
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile

HOME_PATH = re.compile(rb"/home/[A-Za-z0-9_.-]+(?:/|(?=[\s\"'<>]))")
WORKTREE_PATH = b".t3" + b"/worktrees"
# A former workstation's login, hostname and network domain. Split so that this file,
# which is tracked and scanned too, doesn't fail its own check. Compared in lowercase.
PRIVATE_IDENTIFIERS = (
    b"kac" + b"olem8",
    b"en42" + b"32374l",
    b"dhcp" + b".asu",
    b"ad.asu" + b".edu",
)
# One local part, one host with a dot. Git's guessed identities tend to fail this.
EMAIL_ADDRESS = re.compile(r"[^@\s]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# Machine names, not mail domains: a dhcp label or a private top-level name.
MACHINE_HOST = re.compile(r"(?:^|\.)dhcp(?:\.|$)|\.(?:local|localdomain|lan|internal)$", re.I)

MAX_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 10000
MAX_DEPTH = 5


def has_private_identifier(data: bytes) -> bool:
    lowered = data.lower()
    return any(identifier in lowered for identifier in PRIVATE_IDENTIFIERS)


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
    if has_private_identifier(data):
        errors.append(f"{name}: private workstation identifier")
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


def invalid_email(email: str) -> bool:
    return not EMAIL_ADDRESS.fullmatch(email) or bool(
        MACHINE_HOST.search(email.rpartition("@")[2])
    )


def check_commits(root: Path, revision_range: str):
    """Return ``<commit> <role>: <problem>`` messages, never the identity itself."""
    roles = ("author", "committer")
    result = subprocess.run(
        ["git", "log", "--format=%h%x00%an%x00%ae%x00%cn%x00%ce", "--end-of-options", revision_range, "--"],
        cwd=root,
        capture_output=True,
    )
    if result.returncode:
        return [f"could not read commit range {revision_range!r}"]
    errors = []
    for line in result.stdout.decode(errors="replace").splitlines():
        commit, *identity = line.split("\0")
        for role, name, email in zip(roles, identity[0::2], identity[1::2]):
            if invalid_email(email):
                errors.append(f"{commit} {role}: not a valid email address")
            if has_private_identifier(f"{name} {email}".encode()):
                errors.append(f"{commit} {role}: private workstation identifier")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition("\n")[0])
    parser.add_argument(
        "--commits",
        metavar="RANGE",
        help="check author and committer identities in this git range, instead of tracked files",
    )
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if args.commits is not None:
        errors = check_commits(root, args.commits)
        passed = f"Commit identity check passed for {args.commits}."
    else:
        errors = check_tracked(root)
        passed = "Tracked-file privacy scan passed (including archives)."
    print("\n".join(errors) if errors else passed)
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())

"""Register a checkpoint in a README table: its sha256, role and provenance, so a baseline cannot be replaced unnoticed.

The README is append-only. A row is added with one atomic replace of the whole file (written beside it, fsynced, then
renamed), under a lock, and the old text must be an exact prefix of the new text. A path that already has a row is
never overwritten: the tool refuses and shows the existing row. Nothing is deleted, and the checkpoint is only read.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

EXTENSIONS = (".pt", ".pth", ".zip", ".ckpt", ".onnx")
HEADER = ("| Checkpoint | Role | SHA-256 | Size (bytes) | Registered (UTC) | Provenance |\n"
          "|---|---|---|---|---|---|\n")
_FIELD = re.compile(r"^[^\x00-\x1f]+$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _cells(line: str) -> list[str]:
    body = line.strip().strip("|")
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", body)]


def _escape(text: str) -> str:
    return text.replace("|", "\\|")


def _text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required: say {'what the checkpoint is for' if name == 'role' else 'where it came from (run, commit, settings)'}.")
    value = value.strip()
    if len(value) > limit or not _FIELD.match(value):
        raise ValueError(f"{name} must be one line of at most {limit} characters")
    return value


def existing_rows(readme_text: str) -> list[dict[str, str]]:
    rows = []
    for line in readme_text.splitlines():
        if not line.startswith("|") or line.startswith("|---") or line.startswith("| Checkpoint "):
            continue
        cells = _cells(line)
        if len(cells) >= 6:
            rows.append({"path": cells[0].strip("`"), "role": cells[1], "sha256": cells[2].strip("`"), "size_bytes": cells[3],
                         "registered": cells[4], "provenance": cells[5]})
    return rows


def _git_note(path: Path, registry: Path) -> dict[str, Any] | None:
    try:
        ignored = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=path.parent, capture_output=True, timeout=10)
        inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=path.parent, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if inside.returncode != 0:
        return None
    if ignored.returncode == 0:
        return {"ignored_by_git": True,
                "hint": f"git ignores this file type: add it with `git add -f {path}` together with `git add {registry}` (this tool does not run git)."}
    return {"ignored_by_git": False, "hint": f"Commit the registry with the checkpoint: `git add {path} {registry}`."}


def register(path: Any, role: Any, provenance: Any, registry: Any = None, read_only: bool = False) -> dict[str, Any]:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path is required: the checkpoint file to register")
    ckpt = Path(path).expanduser()
    if not ckpt.is_file():
        raise ValueError(f"{path}: not an existing file. Find checkpoints with isaaclab__checkpoints_list.")
    ckpt = ckpt.resolve()
    if ckpt.suffix.lower() not in EXTENSIONS:
        raise ValueError(f"{path}: not a checkpoint file ({', '.join(EXTENSIONS)})")
    role = _text(role, "role", 40)
    provenance = _text(provenance, "provenance", 500)
    readme = (Path(registry).expanduser() if registry else ckpt.parent / "README.md")
    readme = readme.resolve() if readme.exists() else readme.parent.resolve() / readme.name
    if readme.suffix.lower() != ".md":
        raise ValueError("registry must be a .md file")
    if readme.parent != ckpt.parent and readme.parent not in ckpt.parents:
        raise ValueError(f"registry {readme} must sit in the checkpoint's folder or one above it")
    if readme.is_symlink():
        raise ValueError(f"registry {readme} is a symbolic link; use the real file")
    rel = os.path.relpath(ckpt, readme.parent)
    digest = sha256_of(ckpt)
    lock = readme.parent / f".{readme.name}.lock"
    with open(lock, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        old = readme.read_text(encoding="utf-8") if readme.exists() else ""
        rows = existing_rows(old)
        for row in rows:
            if row["path"] == rel:
                raise ValueError(
                    f"{rel} is already registered ({row['role']}, sha256 {row['sha256']}, {row['registered']}); registered paths are never "
                    f"overwritten. The file on disk {'still matches' if row['sha256'] == digest else 'now DIFFERS from'} that hash. "
                    "Register a changed checkpoint under a new file name.")
        dupes = [r["path"] for r in rows if r["sha256"] == digest]
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        row = f"| `{rel}` | {_escape(role)} | `{digest}` | {ckpt.stat().st_size} | {stamp} | {_escape(provenance)} |\n"
        if not any(line.startswith("| Checkpoint ") for line in old.splitlines()):
            new = old + ("" if not old or old.endswith("\n\n") else ("\n" if old.endswith("\n") else "\n\n")) + \
                ("# Checkpoints\n\n" if not old else "") + HEADER + row
        else:
            new = old + ("" if old.endswith("\n") else "\n") + row
        assert new.startswith(old)  # append-only
        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=readme.parent, prefix=f".{readme.name}.", suffix=".tmp", delete=False) as f:
                tmp = Path(f.name)
                f.write(new)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, readme)
            tmp = None
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    out: dict[str, Any] = {"registered": True, "path": str(ckpt), "role": role, "sha256": digest, "size_bytes": ckpt.stat().st_size,
                           "registry": str(readme), "row": row.strip()}
    if dupes:
        out["same_content_as"] = dupes
        out["note"] = "another registered path has identical content"
    if read_only:
        ckpt.chmod(ckpt.stat().st_mode & ~0o222)
        out["read_only"] = True
    git = _git_note(ckpt, readme)
    if git:
        out["git"] = git
    return out

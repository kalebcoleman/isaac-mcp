"""What code this bridge's tree holds, and how often its extensions reloaded in place.

``status`` calls this on Kit's main thread, so it never starts a process. It reads git's own files:
HEAD for the commit, and the index to stat the tracked extension files, hashing only those whose
stat differs. Anything it cannot read (no .git in an exported tree, an index format it does not
parse, a SHA-256 repository) reports unknown rather than an error. Untracked new files are not seen.

Stdlib only, plus carb when present, so it also runs off-Kit in tests.
"""

import hashlib
import json
import os
import re
import stat
import struct
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[3]
#: Tree paths whose edits change what this Kit serves: the extensions and the contracts they import.
WATCHED = ("exts/", "mcp/isaac_mcp/")
UNKNOWN = "unknown"
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_MAX_HASH_BYTES = 32 * 1024 * 1024
_MAX_LISTED = 8


def _git_dir(root: Path) -> Optional[Path]:
    git = root / ".git"
    if git.is_file():
        pointer = git.read_text().strip()
        return (root / pointer.split(":", 1)[1].strip()).resolve() if pointer.startswith("gitdir:") else None
    return git if git.is_dir() else None


def read_head(root: Path) -> Optional[str]:
    """The commit checked out in ``root``, or None. The host has the same reader
    (isaac_mcp.server._read_git_head); neither package can import the other."""
    try:
        git_dir = _git_dir(root)
        if git_dir is None:
            return None
        head = (git_dir / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head if _COMMIT.match(head) else None
        ref = head.split(":", 1)[1].strip()
        common = git_dir
        if (git_dir / "commondir").is_file():
            common = (git_dir / (git_dir / "commondir").read_text().strip()).resolve()
        for base in (git_dir, common):
            if (base / ref).is_file():
                value = (base / ref).read_text().strip()
                return value if _COMMIT.match(value) else None
        if (common / "packed-refs").is_file():
            for line in (common / "packed-refs").read_text().splitlines():
                value, _, name = line.partition(" ")
                if name == ref and _COMMIT.match(value):
                    return value
    except (OSError, ValueError):  # UnicodeDecodeError is a ValueError
        return None
    return None


def _index_entries(data: bytes) -> Optional[List[Tuple[str, int, int, int, int, bytes, bool]]]:
    """(path, mode, mtime_s, mtime_ns, size, sha1, unmerged) per entry; None for a format not parsed."""
    if data[:4] != b"DIRC" or len(data) < 12:
        return None
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3):  # 4 compresses paths; sparse indexes also carry directory entries
        return None
    entries, at = [], 12
    for _ in range(count):
        _, _, mtime_s, mtime_ns, _, _, mode, _, _, size = struct.unpack(">10I", data[at:at + 40])
        sha = data[at + 40:at + 60]
        flags = struct.unpack(">H", data[at + 60:at + 62])[0]
        header = 62
        skip = False
        if version == 3 and flags & 0x4000:
            extended = struct.unpack(">H", data[at + 62:at + 64])[0]
            skip = bool(extended & 0x4000)  # skip-worktree: deliberately absent from the tree
            header = 64
        end = data.index(b"\0", at + header)
        path = data[at + header:end].decode("utf-8", "surrogateescape")
        used = end - at  # header + path; 1 to 8 NUL bytes pad each entry to a multiple of 8
        at += used + 8 - (used % 8)
        if not skip:
            entries.append((path, mode, mtime_s, mtime_ns, size, sha, bool(flags & 0x3000)))
    return entries


def _matches_blob(path: Path, sha: bytes, mode: int, size: int) -> bool:
    if stat.S_ISLNK(mode):
        content = os.readlink(path).encode("utf-8", "surrogateescape")
    elif size > _MAX_HASH_BYTES or path.stat().st_size > _MAX_HASH_BYTES:
        return False
    else:
        content = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(content) + content).digest() == sha


def dirty_paths(root: Path, prefixes=WATCHED) -> Optional[List[str]]:
    """Tracked files under ``prefixes`` that differ from the index, or None when that is unknown."""
    try:
        git_dir = _git_dir(root)
        index = git_dir / "index" if git_dir is not None else None
        if index is None or not index.is_file():
            return None
        index_mtime = index.stat().st_mtime_ns // 1_000_000_000
        entries = _index_entries(index.read_bytes())
    except (OSError, ValueError, struct.error):
        return None
    if entries is None:
        return None
    dirty = []
    for path, mode, mtime_s, mtime_ns, size, sha, unmerged in entries:
        if not path.startswith(tuple(prefixes)) or stat.S_IFMT(mode) == 0o160000:  # submodule
            continue
        file = root / path
        try:
            info = file.lstat()
            same_stat = (info.st_size & 0xFFFFFFFF == size and int(info.st_mtime) & 0xFFFFFFFF == mtime_s
                         and info.st_mtime_ns % 1_000_000_000 == mtime_ns)
            # A file changed in the second the index was written can look unchanged: compare content.
            if not unmerged and same_stat and mtime_s < index_mtime:
                continue
            if unmerged or not _matches_blob(file, sha, mode, size):
                dirty.append(path)
        except OSError:
            dirty.append(path)
    return dirty


# Read when this module loads, so it is the commit of the code Kit imported. Reloading the bridge
# extension re-imports this module; reloading another extension does not.
LOADED_COMMIT = read_head(ROOT)


def report(root: Path = ROOT, loaded_commit: Optional[str] = LOADED_COMMIT) -> Dict[str, Any]:
    """The ``extension`` object of ``status``."""
    current = read_head(root)
    dirty = dirty_paths(root)
    out: Dict[str, Any] = {"root": str(root), "commit": loaded_commit or UNKNOWN,
                           "checkout_commit": current or UNKNOWN,
                           "dirty": None if dirty is None else bool(dirty),
                           "hot_reload": _hot_reload_setting(), "reloads": reload_report()}
    if dirty:
        out["dirty_paths"] = dirty[:_MAX_LISTED]
        out["dirty_count"] = len(dirty)
    if loaded_commit and current and loaded_commit != current:
        out["warning"] = (f"This Kit started from commit {loaded_commit[:12]} but its tree is now at {current[:12]}. "
                          "Extensions that hot-reloaded run new code and the rest run old code: restart Kit.")
    return out


def _hot_reload_setting() -> Optional[bool]:
    """Kit's extension folder watcher; None off-Kit or when the setting is absent."""
    try:
        import carb.settings
        value = carb.settings.get_settings().get("/app/extensions/fsWatcherEnabled")
    except Exception:  # noqa: BLE001 - optional detail of a status answer
        return None
    return None if value is None else bool(value)


# ---- Reload log: extension starts in this process. Kept in carb settings because a reload re-imports
# every module of the extension, this one included when the bridge itself reloads. ----

_SETTING = "/exts/isaac.mcp.bridge/reloadLog"
_off_kit: Dict[str, str] = {}


def _settings():
    try:
        import carb.settings
        return carb.settings.get_settings()
    except Exception:  # noqa: BLE001 - off-Kit
        return None


def _load_log() -> Dict[str, Dict[str, Any]]:
    settings = _settings()
    raw = settings.get(_SETTING) if settings is not None else _off_kit.get(_SETTING)
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def _save_log(log: Dict[str, Dict[str, Any]]) -> None:
    settings = _settings()
    if settings is not None:
        settings.set_string(_SETTING, json.dumps(log))
    else:
        _off_kit[_SETTING] = json.dumps(log)


def note_start(name: str) -> Dict[str, Any]:
    """Count one extension start. The result's ``reload`` is 0 on the first start in this process."""
    log = _load_log()
    entry = log.setdefault(name, {"starts": 0})
    entry["starts"] += 1
    entry["last_start"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save_log(log)
    return {**entry, "reload": entry["starts"] - 1}


def note_stop(name: str, **facts: Any) -> None:
    """Leave facts for the instance that starts next, e.g. the session ID it replaces."""
    log = _load_log()
    log.setdefault(name, {"starts": 0}).update(facts)
    _save_log(log)


def reload_report() -> Dict[str, Dict[str, Any]]:
    """Extensions that restarted in this process: how many times and when last."""
    return {name: {"count": entry["starts"] - 1, "last": entry.get("last_start")}
            for name, entry in _load_log().items() if entry.get("starts", 0) > 1}

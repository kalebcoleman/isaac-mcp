#!/usr/bin/env python3
"""Inspect and populate the asset stores used by the UR5e twin.

Assets that cannot live in the public repository (NVIDIA models, lab-supplied
robots, benches) are looked up in *stores*: directories listed in the project
file ``isaac-mcp.json`` and in ``$ISAAC_MCP_ASSET_PATH``.  This tool shows what
is active, checks what resolves, and copies your own files into a store with
pinned SHA-256 hashes.

Examples::

    python3 scripts/isaac_assets.py where
    python3 scripts/isaac_assets.py doctor --require robots/ur5e/ur5e.usd
    python3 scripts/isaac_assets.py add <path>/my-robot.tar.gz
    python3 scripts/isaac_assets.py add ./bench.usd --as bench/bench.usd
    python3 scripts/isaac_assets.py pin

Exit status: 0 success, 1 ``doctor`` found a problem, 2 usage error or refusal.
Nothing is ever written inside this repository checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import bz2
import gzip
import io
import json
import lzma
import os
import secrets
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[1]
EXT_DIR = REPO / "exts" / "ur5e-dt"

ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".zip")


def _load_asset_cache():
    spec = importlib.util.spec_from_file_location(
        "ur5e_asset_cache", EXT_DIR / "ur5e_dt" / "asset_cache.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


asset_cache = _load_asset_cache()


class CliError(Exception):
    """An expected failure: printed without a traceback, exit status 2."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO.resolve())
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# where
# ---------------------------------------------------------------------------


def _store_entries() -> list[dict]:
    entries = [{"path": str(p), "origin": "project"} for p in asset_cache.project_stores()]
    entries += [{"path": str(p), "origin": "user"} for p in asset_cache.user_stores()]
    return entries


def _deprecated_env() -> list[str]:
    pairs = (
        (asset_cache.PATH_ENV, asset_cache.CACHE_ENV),
        (asset_cache.REMOTE_ROOT_ENV, asset_cache.ROOT_ENV),
        (asset_cache.STRICT_ENV, asset_cache.OFFLINE_ENV),
    )
    used = []
    for primary, legacy in pairs:
        if not os.environ.get(primary, "").strip() and os.environ.get(legacy, "").strip():
            used.append(f"{legacy} (use {primary})")
    return used


def cmd_where(args) -> int:
    project = asset_cache.project_file()
    entries = _store_entries()
    strict = asset_cache.offline()
    deprecated = _deprecated_env()
    if args.json:
        print(
            json.dumps(
                {
                    "project_file": str(project) if project else None,
                    "stores": entries,
                    "strict": strict,
                    "deprecated_env": deprecated,
                },
                indent=2,
            )
        )
        return 0
    print(f"project file: {project if project else 'none'}")
    print("stores (searched in this order):")
    for number, entry in enumerate(entries, 1):
        print(f"  {number}. [{entry['origin']}] {entry['path']}")
    print(f"strict mode:  {'on (remote fallback refused)' if strict else 'off'}")
    if deprecated:
        print("deprecated environment names in use:")
        for item in deprecated:
            print(f"  {item}")
    else:
        print("deprecated environment names in use: none")
    return 0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

LOCAL_STATUSES = {"ok", "pinned", "unpinned"}


def cmd_doctor(args) -> int:
    names = list(asset_cache.known_assets())
    for required in args.require or []:
        if required not in names:
            names.append(required)
    infos = [asset_cache.resolve_info(name) for name in sorted(names)]
    required_set = set(args.require or [])

    problems: list[str] = []
    for info in infos:
        name = info["relpath"]
        if info["status"] == "corrupt":
            problems.append(f"{name}: corrupt")
        elif name in required_set and info["status"] not in LOCAL_STATUSES:
            problems.append(f"{name}: required but not available locally ({info['status']})")
        elif args.local_only and info["layer"] == "remote":
            problems.append(f"{name}: resolves remotely (--local-only)")
    ok = not problems

    if args.json:
        print(
            json.dumps(
                {
                    "project_file": str(p) if (p := asset_cache.project_file()) else None,
                    "stores": _store_entries(),
                    "strict": asset_cache.offline(),
                    "assets": infos,
                    "problems": problems,
                    "ok": ok,
                },
                indent=2,
            )
        )
        return 0 if ok else 1

    rows = []
    for info in infos:
        where = info.get("path") or info.get("uri") or info.get("detail", "")
        if info["status"] == "corrupt":
            where = f"{info.get('path', '')}  ({info.get('detail', '').splitlines()[-1] if info.get('detail') else ''})"
        rows.append(
            (
                info["relpath"],
                info["layer"],
                info["status"],
                info.get("sha256", "")[:12] or "-",
                where,
            )
        )
    header = ("ASSET", "LAYER", "STATUS", "SHA256", "LOCATION")
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(4)]
    for row in [header, *rows]:
        cells = [row[i].ljust(widths[i]) for i in range(4)]
        print("  ".join(cells + [row[4]]).rstrip())
    if not rows:
        print("(no known assets)")
    print()
    if ok:
        print(f"OK: {len(infos)} asset(s) checked")
    else:
        print(f"PROBLEMS ({len(problems)}):")
        for problem in problems:
            print(f"  {problem}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# add / pin
# ---------------------------------------------------------------------------


def _clean_relpath(raw: str, what: str) -> str:
    """Validate a relative asset path; return it in POSIX form."""
    if not raw or "\\" in raw or "\n" in raw or "\r" in raw or "\x00" in raw:
        raise CliError(f"unsafe {what}: {raw!r}")
    posix = PurePosixPath(raw)
    if posix.is_absolute() or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise CliError(f"unsafe {what} (absolute path): {raw!r}")
    parts = [p for p in posix.parts if p != "."]
    if not parts or ".." in parts:
        raise CliError(f"unsafe {what} (empty or contains '..'): {raw!r}")
    for part in parts:
        if part != part.strip() or any(ord(c) < 32 or ord(c) == 127 for c in part):
            raise CliError(
                f"unsupported {what}: names must not start or end with whitespace "
                f"or contain control characters: {raw!r}"
            )
    return "/".join(parts)


def _is_root_entry(name: str) -> bool:
    """True for the archive's own root directory entry (``.``, ``./`` or empty)."""
    return all(part in ("", ".") for part in PurePosixPath(name).parts)


def _pack_dir(store_arg: str | None, pack: str) -> tuple[Path, Path]:
    if not pack or "/" in pack or "\\" in pack or pack in (".", ".."):
        raise CliError(f"invalid pack name: {pack!r}")
    store = Path(store_arg).expanduser() if store_arg else asset_cache.cache_dir()
    store = store.resolve()
    if _inside_repo(store):
        raise CliError(
            f"refusing store inside the repository checkout: {store}\n"
            "Private assets must never land in git; choose a directory outside it "
            "(see `isaac_assets.py where`)."
        )
    pack_dir = store / pack
    if _inside_repo(pack_dir):
        raise CliError(f"refusing pack folder that resolves inside the repository: {pack_dir}")
    return store, pack_dir


def _is_archive(path: Path) -> bool:
    return path.is_file() and path.name.lower().endswith(ARCHIVE_SUFFIXES)


DEFAULT_MAX_MEMBERS = 20_000
DEFAULT_MAX_FILE_BYTES = 8 << 30
DEFAULT_MAX_TOTAL_BYTES = 16 << 30


class Limits:
    """Bounds on archive extraction, enforced on the bytes actually written."""

    def __init__(self, members: int, file_bytes: int, total_bytes: int):
        self.members, self.file_bytes, self.total_bytes = members, file_bytes, total_bytes
        self.count = 0
        self.total = 0

    def member(self, name: str) -> None:
        self.count += 1
        if self.count > self.members:
            raise CliError(
                f"archive has more than {self.members} members (stopped at {name!r}); "
                "raise --max-members if this is expected"
            )

    def chunk(self, name: str, file_total: int, size: int) -> None:
        self.total += size
        if file_total > self.file_bytes:
            raise CliError(
                f"archive member {name!r} expands past {self.file_bytes} bytes; "
                "raise --max-file-bytes if this is expected"
            )
        if self.total > self.total_bytes:
            raise CliError(
                f"archive expands past {self.total_bytes} bytes in total; "
                "raise --max-total-bytes if this is expected"
            )


def _copy_stream(src, dest: Path, limits: Limits, name: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with dest.open("wb") as out:
        while chunk := src.read(1 << 20):
            written += len(chunk)
            limits.chunk(name, written, len(chunk))
            out.write(chunk)


# Tar headers (including PAX/GNU extended headers) are read before any member is counted,
# so the decompressed stream itself is bounded: payload limit + per-member header
# allowance + a fixed allowance for extended-header data.
METADATA_ALLOWANCE = 1 << 20
PER_MEMBER_OVERHEAD = 3 * 512


class _BoundedReader(io.RawIOBase):
    """Read-only wrapper that fails once more than *limit* bytes have been read."""

    def __init__(self, raw, limit: int, also_close=()):
        self._raw, self._limit, self._count = raw, limit, 0
        self._also_close = tuple(also_close)

    def close(self) -> None:
        try:
            for handle in (self._raw, *self._also_close):
                handle.close()
        finally:
            super().close()

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self._raw.read(len(buffer))
        self._count += len(data)
        if self._count > self._limit:
            raise CliError(
                f"archive expands past the allowed {self._limit} bytes while reading its headers "
                "and contents; raise --max-total-bytes if this is expected"
            )
        buffer[: len(data)] = data
        return len(data)


MAX_TAR_HEADER_BYTES = 1 << 20
# Extended headers are also capped per archive (global PAX headers persist for the whole
# archive) and per chain (tarfile follows a chain of extended headers by recursion).
MAX_TAR_HEADER_TOTAL_BYTES = 16 << 20
MAX_TAR_HEADER_CHAIN = 8
_EXTENDED_HEADER_TYPES = frozenset(
    {
        tarfile.GNUTYPE_LONGNAME,
        tarfile.GNUTYPE_LONGLINK,
        tarfile.XHDTYPE,
        tarfile.XGLTYPE,
        tarfile.SOLARIS_XHDTYPE,
    }
)


class _BoundedTarInfo(tarfile.TarInfo):
    """Refuse a PAX or GNU long-name header that tarfile would read into memory whole.

    ``tarfile`` reads such a header's data in one piece before the member it describes is
    seen, so the stream bound alone does not limit memory. This check runs first.
    """

    def _proc_member(self, tarfile_obj):
        if self.type in _EXTENDED_HEADER_TYPES:
            if self.size > MAX_TAR_HEADER_BYTES:
                raise CliError(
                    f"archive has a {self.size}-byte extended tar header; the limit is "
                    f"{MAX_TAR_HEADER_BYTES} bytes"
                )
            total = getattr(tarfile_obj, "_isaac_header_bytes", 0) + self.size
            chain = getattr(tarfile_obj, "_isaac_header_chain", 0) + 1
            if total > MAX_TAR_HEADER_TOTAL_BYTES:
                raise CliError(
                    f"archive's extended tar headers exceed {MAX_TAR_HEADER_TOTAL_BYTES} bytes in total"
                )
            if chain > MAX_TAR_HEADER_CHAIN:
                raise CliError(
                    f"archive chains more than {MAX_TAR_HEADER_CHAIN} extended tar headers in a row"
                )
            tarfile_obj._isaac_header_bytes = total
            tarfile_obj._isaac_header_chain = chain
        else:
            tarfile_obj._isaac_header_chain = 0
        return super()._proc_member(tarfile_obj)


def _open_tar(archive: Path, limits: Limits):
    raw = open(archive, "rb")
    magic = raw.read(6)
    raw.seek(0)
    if magic[:2] == b"\x1f\x8b":
        stream = gzip.GzipFile(fileobj=raw)
    elif magic[:3] == b"BZh":
        stream = bz2.BZ2File(raw)
    elif magic[:6] == b"\xfd7zXZ\x00":
        stream = lzma.LZMAFile(raw)
    else:
        stream = raw
    bound = limits.total_bytes + limits.members * PER_MEMBER_OVERHEAD + METADATA_ALLOWANCE
    reader = io.BufferedReader(_BoundedReader(stream, bound, also_close=(raw,)))
    try:
        return tarfile.open(fileobj=reader, mode="r|", tarinfo=_BoundedTarInfo)
    except BaseException:
        raw.close()
        raise


def _check_zip_directory(archive: Path, limits: Limits) -> None:
    """Walk the zip central directory under the member limit before ``zipfile`` loads it.

    ``zipfile`` parses the directory by its byte size, not by the claimed entry count, so
    the count in the end record cannot be trusted. This reads the records themselves, one
    fixed-size header at a time, stops as soon as the limit is exceeded, and requires the
    real record count and byte size to match the end record.
    """
    with archive.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        tail_len = min(size, 65535 + 22 + 20 + 56 + 1024)
        base = size - tail_len
        fh.seek(base)
        tail = fh.read()
        i = tail.rfind(b"PK\x05\x06")
        if i < 0 or len(tail) - i < 22:
            return  # not a zip we can pre-check; zipfile will report it
        total = struct.unpack("<H", tail[i + 10 : i + 12])[0]
        cd_size = struct.unpack("<L", tail[i + 12 : i + 16])[0]
        end_pos = base + i
        # Mirror zipfile: if the 20-byte zip64 locator sits right before the end record and
        # the 56-byte zip64 record right before that, those values replace the legacy ones
        # unconditionally (not only when a legacy field is saturated).
        loc = i - 20
        rec = loc - 56
        if loc >= 0 and tail[loc : loc + 4] == b"PK\x06\x07":
            if rec < 0 or tail[rec : rec + 4] != b"PK\x06\x06":
                # zip64 "extensible data" between the record and the locator: the record's
                # true position cannot be checked the way zipfile will read it.
                raise CliError("unsupported zip archive: zip64 end record with extensible data")
            total = struct.unpack("<Q", tail[rec + 32 : rec + 40])[0]
            cd_size = struct.unpack("<Q", tail[rec + 40 : rec + 48])[0]
            end_pos = base + rec
        pos = end_pos - cd_size
        if pos < 0:
            raise CliError("malformed zip archive: central directory size is inconsistent")
        count = 0
        while pos < end_pos:
            fh.seek(pos)
            head = fh.read(46)
            if len(head) < 46 or head[:4] != b"PK\x01\x02":
                raise CliError("malformed zip archive: bad central directory record")
            name_len, extra_len, comment_len = struct.unpack("<HHH", head[28:34])
            pos += 46 + name_len + extra_len + comment_len
            count += 1
            if count > limits.members:
                raise CliError(
                    f"archive has more than {limits.members} members; "
                    "raise --max-members if this is expected"
                )
        if pos != end_pos or count != total:
            raise CliError(
                f"malformed zip archive: directory has {count} records but declares {total}"
            )


def _extract_archive(archive: Path, into: Path, limits: Limits | None = None) -> None:
    """Extract regular files only, validating every member; owners are not kept."""
    limits = limits or Limits(DEFAULT_MAX_MEMBERS, DEFAULT_MAX_FILE_BYTES, DEFAULT_MAX_TOTAL_BYTES)
    names: set[str] = set()
    if archive.name.lower().endswith(".zip"):
        _check_zip_directory(archive, limits)
        try:
            zf = zipfile.ZipFile(archive)
        except zipfile.BadZipFile as exc:
            raise CliError(f"not a valid zip archive: {archive} ({exc})") from exc
        except NotImplementedError as exc:
            # e.g. an entry that needs a newer zip version than Python supports
            raise CliError(f"unsupported zip archive: {archive} ({exc})") from exc
        with zf:
            for info in zf.infolist():
                limits.member(info.filename)
                if not info.filename:
                    raise CliError("unsafe archive member: empty name")
                if info.is_dir():
                    if not _is_root_entry(info.filename):
                        _clean_relpath(info.filename.rstrip("/"), "archive member")
                    continue
                mode = info.external_attr >> 16
                if mode and (mode & 0o170000) not in (0, 0o100000):
                    raise CliError(f"unsafe archive member (not a regular file): {info.filename}")
                rel = _clean_relpath(info.filename, "archive member")
                if rel in names:
                    raise CliError(f"duplicate archive member: {rel}")
                names.add(rel)
                try:
                    with zf.open(info) as src:
                        _copy_stream(src, into / rel, limits, rel)
                except (RuntimeError, NotImplementedError) as exc:
                    # zipfile reports encrypted entries and unsupported methods this way.
                    raise CliError(f"unsupported zip entry {info.filename!r}: {exc}") from exc
        return
    try:
        tf = _open_tar(archive, limits)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise CliError(f"not a valid tar archive: {archive} ({exc})") from exc
    with tf:
        for member in tf:
            limits.member(member.name)
            if member.isdir():
                if not _is_root_entry(member.name):
                    _clean_relpath(member.name.rstrip("/"), "archive member")
                continue
            if not member.isreg():
                kind = (
                    "symlink"
                    if member.issym()
                    else "hardlink"
                    if member.islnk()
                    else "special file"
                )
                raise CliError(f"unsafe archive member ({kind}): {member.name}")
            rel = _clean_relpath(member.name, "archive member")
            if rel in names:
                raise CliError(f"duplicate archive member: {rel}")
            names.add(rel)
            src = tf.extractfile(member)
            assert src is not None
            with src:
                _copy_stream(src, into / rel, limits, rel)


def _walk_directory(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(current) / name
            if path.is_symlink():
                raise CliError(f"refusing symlink in source: {path}")
        for name in files:
            path = Path(current) / name
            if not path.is_file():
                raise CliError(f"refusing non-regular file in source: {path}")
            rel = path.relative_to(root).as_posix()
            found[_clean_relpath(rel, "source file")] = path
    return found


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW


def _open_dir_chain(pack_dir: Path, parts: tuple[str, ...]) -> int:
    """Open *pack_dir*, then each folder in *parts* below it, creating missing ones.

    Every step uses ``O_NOFOLLOW`` relative to the previous directory handle, so a
    symlink planted anywhere below the pack folder is refused before anything is
    created through it. Returns a directory file descriptor the caller must close.
    """
    fd = os.open(pack_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in parts:
            try:
                nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, 0o755, dir_fd=fd)
                nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                raise CliError(
                    f"refusing destination: {part!r} under {pack_dir} is a symlink or not a folder"
                ) from exc
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _temp_name(name: str) -> str:
    return f".{name}.{secrets.token_hex(8)}.part"


def _publish(dfd: int, tmp: str, name: str, force: bool) -> None:
    """Move *tmp* to *name* inside directory *dfd*; without ``force`` never replace anything."""
    if force:
        os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        return
    try:
        os.link(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
    except FileExistsError:
        os.unlink(tmp, dir_fd=dfd)
        raise CliError(f"refusing to overwrite (use --force): {name}") from None
    except OSError:
        # No hard links on this filesystem: create the destination exclusively and copy.
        # O_EXCL means a file that appears in the meantime is never replaced.
        try:
            out_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o666, dir_fd=dfd)
        except FileExistsError:
            os.unlink(tmp, dir_fd=dfd)
            raise CliError(f"refusing to overwrite (use --force): {name}") from None
        try:
            with os.fdopen(out_fd, "wb") as out, open(
                tmp, "rb", opener=lambda path, flags: os.open(path, flags, dir_fd=dfd)
            ) as src:
                shutil.copyfileobj(src, out, 1 << 20)
        except BaseException:
            os.unlink(name, dir_fd=dfd)
            raise
    os.unlink(tmp, dir_fd=dfd)


def _unlink_quietly(dfd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=dfd)
    except OSError:
        pass  # best effort: the error that caused the cleanup is the one worth reporting


def _write_new_file(dfd: int, name: str, write) -> str:
    """Create a uniquely named temp file in *dfd* (exclusive, no symlink following)."""
    tmp = _temp_name(name)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o666, dir_fd=dfd)
    try:
        with os.fdopen(fd, "wb") as out:
            write(out)
    except BaseException:
        os.unlink(tmp, dir_fd=dfd)
        raise
    return tmp


def _install_file(src: Path, rel: str, pack_dir: Path, force: bool) -> None:
    """Copy *src* to ``<pack_dir>/<rel>`` without following symlinks below the pack folder."""
    *parents, name = PurePosixPath(rel).parts
    dfd = _open_dir_chain(pack_dir, tuple(parents))
    try:
        def write(out):
            with src.open("rb") as handle:
                shutil.copyfileobj(handle, out, 1 << 20)

        tmp = _write_new_file(dfd, name, write)
        try:
            _publish(dfd, tmp, name, force)
        except BaseException:
            _unlink_quietly(dfd, tmp)
            raise
    finally:
        os.close(dfd)


def _write_pins(pack_dir: Path, entries: dict[str, str]) -> None:
    text = "".join(f"{digest}  {name}\n" for name, digest in sorted(entries.items()))
    dfd = _open_dir_chain(pack_dir, ())
    try:
        tmp = _write_new_file(dfd, asset_cache.PINS_FILE, lambda out: out.write(text.encode("utf-8")))
        try:
            os.rename(tmp, asset_cache.PINS_FILE, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            _unlink_quietly(dfd, tmp)
            raise
    finally:
        os.close(dfd)


def cmd_add(args) -> int:
    src = Path(args.src).expanduser()
    if not src.exists() and not src.is_symlink():
        raise CliError(f"source not found: {src}")
    if src.is_symlink():
        raise CliError(f"refusing symlink source: {src}")
    store, pack_dir = _pack_dir(args.store, args.pack)

    scratch: Path | None = None
    try:
        if _is_archive(src):
            if args.as_:
                raise CliError("--as applies to a single file, not an archive")
            scratch = Path(tempfile.mkdtemp(prefix="isaac-assets-"))
            try:
                _extract_archive(
                    src,
                    scratch,
                    Limits(args.max_members, args.max_file_bytes, args.max_total_bytes),
                )
            except (tarfile.TarError, zipfile.BadZipFile, lzma.LZMAError, zlib.error, EOFError) as exc:
                raise CliError(f"corrupt or truncated archive: {src} ({exc})") from exc
            sources = _walk_directory(scratch)
        elif src.is_dir():
            if args.as_:
                raise CliError("--as applies to a single file, not a directory")
            sources = _walk_directory(src)
        elif src.is_file():
            rel = _clean_relpath(args.as_ or src.name, "--as path" if args.as_ else "file name")
            sources = {rel: src}
        else:
            raise CliError(f"source is not a file, directory or archive: {src}")

        if not sources:
            raise CliError(f"nothing to add: {src} contains no files")
        if asset_cache.PINS_FILE in sources:
            raise CliError(
                f"{asset_cache.PINS_FILE} in the source would collide with the pin file; "
                "remove it and let this tool write the pins"
            )
        existing = [rel for rel in sorted(sources) if (pack_dir / rel).exists()]
        if existing and not args.force:
            shown = "\n".join(f"  {pack_dir / rel}" for rel in existing[:10])
            more = f"\n  ... and {len(existing) - 10} more" if len(existing) > 10 else ""
            raise CliError(f"refusing to overwrite (use --force):\n{shown}{more}")
        # Check every destination before copying anything, so a refusal never leaves a
        # half-installed, unpinned store behind.
        for rel in sources:
            try:
                rel.encode("utf-8")
            except UnicodeEncodeError:
                raise CliError(f"file name is not valid UTF-8: {rel!r}") from None
            target = pack_dir / rel
            if target.is_symlink() or _inside_repo(target.parent):
                raise CliError(f"refusing unsafe destination: {target}")
            if target.exists() and not target.is_file():
                raise CliError(f"refusing destination: {target} exists and is not a file")
            for parent in PurePosixPath(rel).parents:
                if str(parent) == ".":
                    continue
                folder = pack_dir / parent
                if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
                    raise CliError(
                        f"refusing destination: {folder} exists and is not a folder "
                        f"(needed for {rel})"
                    )

        _private_dirs(pack_dir)
        pins = dict(asset_cache.pins(pack_dir))
        installed = 0
        try:
            for rel in sorted(sources):
                _install_file(sources[rel], rel, pack_dir, args.force)
                digest = sha256(pack_dir / rel)
                pins[rel] = digest
                installed += 1
                print(f"added {rel}  sha256:{digest[:12]}")
        finally:
            # Pin whatever was installed, even if a later file failed.
            if installed:
                _write_pins(pack_dir, pins)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
    print(f"{len(sources)} file(s) added to {pack_dir}; pins updated in {pack_dir / asset_cache.PINS_FILE}")
    return 0


def cmd_pin(args) -> int:
    store, pack_dir = _pack_dir(args.store, args.pack)
    if not pack_dir.is_dir():
        raise CliError(f"pack folder does not exist: {pack_dir}")
    entries: dict[str, str] = {}
    skipped = 0
    for current, _dirs, files in os.walk(pack_dir, followlinks=False):
        for name in files:
            path = Path(current) / name
            rel = path.relative_to(pack_dir).as_posix()
            if rel == asset_cache.PINS_FILE:
                continue
            if path.is_symlink():
                skipped += 1
                continue
            try:
                _clean_relpath(rel, "file name")
            except CliError:
                skipped += 1
                continue
            entries[rel] = sha256(path)
    _write_pins(pack_dir, entries)
    note = f" ({skipped} symlink/odd name skipped)" if skipped else ""
    print(f"pinned {len(entries)} file(s) in {pack_dir / asset_cache.PINS_FILE}{note}")
    return 0


# ---------------------------------------------------------------------------


# --------------------------------------------------------------- migrate / adopt

# File types copied by ``adopt``: scene and robot descriptions, meshes, materials and
# textures, plus layout JSON. Extend with --include.
ADOPT_SUFFIXES = {
    ".usd", ".usda", ".usdc", ".usdz", ".urdf", ".xacro", ".sdf", ".mjcf",
    ".obj", ".stl", ".dae", ".fbx", ".glb", ".gltf", ".ply", ".mtl",
    ".mdl", ".mtlx", ".png", ".jpg", ".jpeg", ".exr", ".hdr", ".tga", ".tif", ".tiff",
}
ADOPT_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "build", "dist", ".tox"}


def _private_dirs(path: Path) -> None:
    """Create *path* (and missing parents) as 0755 and make sure *path* itself, the pack
    folder, is not group- or world-writable when it is the user's own
    (``assets__prepare_workstation`` refuses such folders, and others must not be able to
    swap your assets). Folders above it are left as they are."""
    missing = []
    probe = path
    while not probe.exists():
        missing.append(probe)
        probe = probe.parent
    for folder in reversed(missing):
        os.mkdir(folder, 0o755)
    try:
        st = path.stat()
    except OSError:
        return
    if st.st_uid == os.getuid() and st.st_mode & 0o022:
        os.chmod(path, st.st_mode & ~0o022 & 0o7777)
        print(f"note: removed group/world write permission from {path}")


def _install_many(sources: dict[str, Path], pack_dir: Path, force: bool, pin: bool) -> tuple[int, int]:
    """Copy ``rel -> source`` into ``pack_dir`` safely. Returns (added, identical).

    A destination that already holds the same bytes is skipped. One that differs is a
    conflict: everything is checked first, and nothing is copied unless ``force``.
    """
    conflicts, todo, identical = [], {}, 0
    for rel in sorted(sources):
        try:
            rel.encode("utf-8")
        except UnicodeEncodeError:
            raise CliError(f"file name is not valid UTF-8: {rel!r}") from None
        target = pack_dir / rel
        if target.is_symlink() or _inside_repo(target.parent):
            raise CliError(f"refusing unsafe destination: {target}")
        if target.exists():
            if not target.is_file():
                raise CliError(f"refusing destination: {target} exists and is not a file")
            if sha256(target) == sha256(sources[rel]):
                identical += 1
                continue
            conflicts.append(rel)
        todo[rel] = sources[rel]
    if conflicts and not force:
        shown = "\n".join(f"  {pack_dir / rel}" for rel in conflicts[:10])
        more = f"\n  ... and {len(conflicts) - 10} more" if len(conflicts) > 10 else ""
        raise CliError(f"different files already in the asset folder (use --force to replace):\n{shown}{more}")
    _private_dirs(pack_dir)
    pins = dict(asset_cache.pins(pack_dir))
    added = 0
    try:
        for rel in sorted(todo):
            _install_file(todo[rel], rel, pack_dir, force)
            if pin:
                pins[rel] = sha256(pack_dir / rel)
            added += 1
    finally:
        if added and pin:
            _write_pins(pack_dir, pins)
    return added, identical


LEGACY_REPO_ASSETS = "exts/ur5e-dt/assets"


def _git_legacy_assets(checkout: Path, rev: str | None, scratch: Path) -> Path | None:
    """Extract the checkout's old ``exts/ur5e-dt/assets`` from git history.

    ``rev`` defaults to the last commit that still had the folder (the parent of the
    commit that removed it). Returns None when the history never had it.
    """
    try:
        if rev is None:
            last = subprocess.run(
                ["git", "-C", str(checkout), "rev-list", "-n", "1", "HEAD", "--", LEGACY_REPO_ASSETS],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            if not last:
                return None
            listed = subprocess.run(
                ["git", "-C", str(checkout), "ls-tree", "-r", "--name-only", last, "--", LEGACY_REPO_ASSETS],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            rev = last if listed else f"{last}^"
        archive = scratch / "legacy.tar"
        with archive.open("wb") as out:
            subprocess.run(
                ["git", "-C", str(checkout), "archive", "--format=tar", rev, "--", LEGACY_REPO_ASSETS],
                stdout=out, stderr=subprocess.PIPE, check=True,
            )
    except (subprocess.CalledProcessError, OSError) as exc:
        raise CliError(f"cannot read old assets from git history of {checkout}: {exc}") from exc
    target = scratch / "legacy"
    _extract_archive(archive, target, Limits(DEFAULT_MAX_MEMBERS, DEFAULT_MAX_FILE_BYTES, DEFAULT_MAX_TOTAL_BYTES))
    found = target.joinpath(*LEGACY_REPO_ASSETS.split("/"))
    return found if found.is_dir() else None


def _legacy_checkout_assets(checkout: Path, rev: str | None, scratch: Path) -> list[Path]:
    """Return the folders holding the checkout's old ``exts/ur5e-dt/assets`` content,
    lowest priority first.

    After pulling the code-only repository, the tracked assets are gone from the working
    tree while your untracked copies (a lab robot, the bench) are still there. So both are
    read: the folder from git history (``rev``, default: the last commit that had it),
    then the working tree, whose files win. With an explicit ``rev`` only history is read.
    """
    folders: list[Path] = []
    tree = checkout.joinpath(*LEGACY_REPO_ASSETS.split("/"))
    has_tree = rev is None and tree.is_dir() and any(p.is_file() for p in tree.rglob("*"))
    if (checkout / ".git").exists():
        try:
            old = _git_legacy_assets(checkout, rev, scratch)
        except CliError as exc:
            if not has_tree:
                raise
            print(f"warning: {exc}; using only the files still in {tree}", file=sys.stderr)
            old = None
        if old is not None:
            folders.append(old)
    elif rev is not None:
        raise CliError(f"--git-rev needs a git checkout: {checkout}")
    if has_tree:
        folders.append(tree)
    return folders


def _store_files(store: Path) -> tuple[dict[str, tuple[Path, str | None]], list[Path]]:
    """Files of an old store keyed by their pack-relative path, each with the old store's
    pin (or None), plus other packs found there (folders with their own ``SHA256SUMS``),
    which are not part of this twin and are left alone."""
    found: dict[str, tuple[Path, str | None]] = {}
    others: list[Path] = []
    pack = store / asset_cache.PACK
    for root in (store, pack):
        if not root.is_dir():
            continue
        pinned = asset_cache.pins(root)
        for current, dirs, files in os.walk(root, followlinks=False):
            here = Path(current)
            keep = []
            for d in dirs:
                sub = here / d
                if sub.is_symlink() or (root == store and here == store and d == asset_cache.PACK):
                    continue
                if root == store and here == store and (sub / asset_cache.PINS_FILE).is_file():
                    others.append(sub)
                    continue
                keep.append(d)
            dirs[:] = keep
            for name in files:
                path = here / name
                if name == asset_cache.PINS_FILE or path.is_symlink() or not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                found[rel] = (path, pinned.get(rel))
    return found, others


def cmd_migrate(args) -> int:
    store, pack_dir = _pack_dir(args.store, asset_cache.PACK)
    checkout = Path(args.from_checkout).expanduser().resolve() if args.from_checkout else asset_cache.REPO_ROOT
    sources: dict[str, Path] = {}
    origins: dict[str, str] = {}
    old_pins: dict[str, str] = {}  # pins the files had in an old asset folder
    damaged: list[str] = []
    scratch = Path(tempfile.mkdtemp(prefix="isaac-assets-migrate-"))
    try:
        for legacy in _legacy_checkout_assets(checkout, args.git_rev, scratch):
            label = "checkout" if checkout in legacy.parents else "checkout history"
            for current, dirs, files in os.walk(legacy, followlinks=False):
                for name in files:
                    path = Path(current) / name
                    if path.is_symlink() or not path.is_file() or name.endswith(".pyc"):
                        continue
                    rel = _clean_relpath(path.relative_to(legacy).as_posix(), "file name")
                    sources[rel], origins[rel] = path, label  # the working tree comes last and wins
        for old in [] if args.no_legacy_stores else asset_cache.legacy_stores():
            if old.resolve() == store.resolve():
                continue
            files, others = _store_files(old)
            for other in others:
                print(f"note: leaving {other} alone: it is a separate asset pack, not this twin's; "
                      f"copy it with `isaac_assets.py adopt {other} --pack {other.name}`")
            for rel, (path, pin) in files.items():
                rel = _clean_relpath(rel, "file name")
                if rel in sources:
                    continue
                if pin and sha256(path) != pin:
                    damaged.append(f"{path} (does not match its pin in {old})")
                    continue
                sources[rel], origins[rel] = path, str(old)
                if pin:
                    old_pins[rel] = pin
        if damaged:
            print("skipping file(s) changed since they were pinned:\n  " + "\n  ".join(damaged), file=sys.stderr)
        if not sources:
            print("nothing to migrate: no old in-repo assets or old asset folders found")
            return 0
        # A file at a path the manifest downloads from NVIDIA stays manifest-verified (no pin)
        # only when it is NVIDIA's exact file. Anything else there (a lab-modified robot, a
        # copy from another Isaac release, or one an old folder pinned) is pinned as yours.
        expected = {e.get("cache_path", e["target"]): e.get("sha256")
                    for g in asset_cache.manifest().get("fetch", []) for e in g.get("files", [])}
        pinned = asset_cache.pins(pack_dir) if pack_dir.is_dir() else {}
        own, public, kept = {}, {}, 0
        for rel, path in sources.items():
            if rel not in expected:
                own[rel] = path
            elif rel in pinned:
                kept += 1  # your pinned copy always wins over NVIDIA's, as in fetch_ur5e_assets.py
            elif rel not in old_pins and expected[rel] and sha256(path) == expected[rel]:
                public[rel] = path
            else:
                own[rel] = path
        print(f"migrating {len(own) + len(public)} file(s) into {pack_dir}")
        if kept:
            print(f"keeping {kept} file(s) you pinned yourself instead of the old copy")
        if args.dry_run:
            for rel in sorted(own):
                print(f"  {rel}  (from {origins[rel]}, pinned as yours)")
            for rel in sorted(public):
                print(f"  {rel}  (from {origins[rel]}, NVIDIA's file)")
            return 0
        a1, i1 = _install_many(own, pack_dir, args.force, pin=True)
        a2, i2 = _install_many(public, pack_dir, args.force, pin=False)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"added {a1 + a2}, already present {i1 + i2}; check with `isaac_assets.py doctor`")
    return 1 if damaged else 0


def _adopt_sources(root: Path, suffixes: set[str]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in ADOPT_SKIP_DIRS and not d.startswith(".")
                         and not (Path(current) / d).is_symlink())
        for name in files:
            path = Path(current) / name
            if path.suffix.lower() not in suffixes or path.is_symlink() or not path.is_file():
                continue
            found[_clean_relpath(path.relative_to(root).as_posix(), "file name")] = path
    return found


def cmd_adopt(args) -> int:
    src = Path(args.src).expanduser().resolve()
    if not src.is_dir():
        raise CliError(f"not a folder: {src}")
    pack = args.pack or src.name
    _clean_relpath(pack, "pack name")
    if pack != asset_cache.PACK and "/" in pack:
        raise CliError("--pack must be a single folder name")
    store, pack_dir = _pack_dir(args.store, pack)
    suffixes = ADOPT_SUFFIXES | {("." + e.lstrip(".")).lower() for e in (args.include or [])}
    sources = _adopt_sources(src, suffixes)
    if not sources:
        print(f"no asset files found in {src}")
        return 0
    total = sum(p.stat().st_size for p in sources.values())
    print(f"{len(sources)} asset file(s), {total / 1048576:.1f} MB, from {src} into {pack_dir}")
    if args.dry_run:
        for rel in sorted(sources):
            print(f"  {rel}")
        return 0
    added, identical = _install_many(sources, pack_dir, args.force, pin=True)
    print(f"added {added}, already present {identical}; pins in {pack_dir / asset_cache.PINS_FILE}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="isaac_assets.py", description="Inspect and populate asset stores."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    where = sub.add_parser("where", help="show the project file, stores and strict mode")
    where.add_argument("--json", action="store_true")
    where.set_defaults(func=cmd_where)

    doctor = sub.add_parser("doctor", help="check every known asset")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument(
        "--require", action="append", metavar="RELPATH", help="fail unless this asset is local"
    )
    doctor.add_argument(
        "--local-only", action="store_true", help="fail if any asset resolves remotely"
    )
    doctor.set_defaults(func=cmd_doctor)

    add = sub.add_parser("add", help="copy a file, directory or archive into a store")
    add.add_argument("src", metavar="SRC")
    add.add_argument("--store", metavar="DIR", help="store directory (default: first user store)")
    add.add_argument("--pack", default=asset_cache.PACK, help="pack folder (default: %(default)s)")
    add.add_argument("--as", dest="as_", metavar="RELPATH", help="relative path for a single file")
    add.add_argument("--force", action="store_true", help="overwrite existing files")
    add.add_argument(
        "--max-members",
        type=int,
        default=DEFAULT_MAX_MEMBERS,
        help="archive member limit (default: %(default)s)",
    )
    add.add_argument(
        "--max-file-bytes",
        type=int,
        default=DEFAULT_MAX_FILE_BYTES,
        help="expanded size limit per archive member (default: %(default)s)",
    )
    add.add_argument(
        "--max-total-bytes",
        type=int,
        default=DEFAULT_MAX_TOTAL_BYTES,
        help="expanded size limit for the whole archive (default: %(default)s)",
    )
    add.set_defaults(func=cmd_add)

    migrate = sub.add_parser(
        "migrate", help="move assets from an older checkout and old asset folders into the asset folder"
    )
    migrate.add_argument("--from", dest="from_checkout", metavar="CHECKOUT",
                         help="older isaac-mcp checkout (default: this one)")
    migrate.add_argument("--git-rev", metavar="REV",
                         help="read exts/ur5e-dt/assets from this commit instead of the working tree")
    migrate.add_argument("--store", metavar="DIR", help="asset folder (default: the primary one)")
    migrate.add_argument("--no-legacy-stores", action="store_true",
                         help="skip ~/.cache/ur5e-dt/assets and <workspace>/assets")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("--force", action="store_true", help="replace different files")
    migrate.set_defaults(func=cmd_migrate)

    adopt = sub.add_parser("adopt", help="copy the asset files of a project folder (e.g. a cloned repo) into the asset folder")
    adopt.add_argument("src", metavar="FOLDER")
    adopt.add_argument("--pack", help="folder name inside the asset folder (default: the project folder's name)")
    adopt.add_argument("--store", metavar="DIR", help="asset folder (default: the primary one)")
    adopt.add_argument("--include", action="append", metavar="EXT", help="also copy files with this extension")
    adopt.add_argument("--dry-run", action="store_true")
    adopt.add_argument("--force", action="store_true", help="replace different files")
    adopt.set_defaults(func=cmd_adopt)

    pin = sub.add_parser("pin", help="regenerate SHA256SUMS for a pack folder")
    pin.add_argument("--store", metavar="DIR")
    pin.add_argument("--pack", default=asset_cache.PACK)
    pin.set_defaults(func=cmd_pin)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

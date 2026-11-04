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
    python3 scripts/isaac_assets.py packs
    python3 scripts/isaac_assets.py doctor --pack bricksim
    python3 scripts/isaac_assets.py pack-build robot/ur5e.usd --pack bricksim --from-pack ur5e-dt

Exit status: 0 success, 1 ``doctor`` found a problem, 2 usage error or refusal.
Nothing is ever written inside this repository checkout.
"""

from __future__ import annotations

import argparse
import importlib.util
import bz2
import gzip
import io
import json
import lzma
import os
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

# Loading asset_cache put <repo>/mcp on sys.path. The install code is shared with the MCP tools.
from isaac_mcp import asset_packs as packs  # noqa: E402
from isaac_mcp.asset_packs import (  # noqa: E402
    ADOPT_SUFFIXES,
    PackError as CliError,
    adopt_sources as _adopt_sources,
    clean_relpath as _clean_relpath,
    install_file as _install_file,
    install_many as _install_many,
    inside_repo as _inside_repo,
    private_dirs as _private_dirs,
    sha256,
    write_pins as _write_pins,
)


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
    pack, pack_source = asset_cache.selected_pack_info()
    if args.json:
        print(
            json.dumps(
                {
                    "project_file": str(project) if project else None,
                    "pack": pack,
                    "pack_source": pack_source,
                    "stores": entries,
                    "strict": strict,
                    "deprecated_env": deprecated,
                },
                indent=2,
            )
        )
        return 0
    print(f"project file: {project if project else 'none'}")
    print(f"pack:         {pack} ({pack_source})")
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


def _print_pack_report(report: dict) -> None:
    for folder in report["folders"]:
        files, deps = folder["files"], folder["dependencies"]
        print(f"{folder['pack_dir']}")
        print(f"  files: {files['total']} ({files['pinned_ok']} pinned, {files['manifest_ok']} match the "
              f"manifest, {files['unpinned_count']} unpinned)")
        for rel in files["unpinned"]:
            print(f"    unpinned: {rel}")
        for item in files["corrupt"]:
            print(f"    CORRUPT: {item['file']} (does not match its {item['kind']} hash)")
        for rel in files["missing"]:
            print(f"    MISSING: {rel} (pinned, not on disk)")
        if not deps["checked"]:
            print(f"  dependencies: NOT CHECKED. {deps['reason']}")
            continue
        print(f"  dependencies of {len(deps['entries'])} entry file(s), from {folder['entry_source']}, "
              f"by {deps.get('backend')}:")
        for key in ("unresolved", "unreadable", "remote"):
            for item in deps[key]:
                print(f"    {key.upper()}: {item['reference']} (in {item.get('referenced_by', '?')})")
        for item in deps["unfollowable"]:
            print(f"    could not follow: {item['file']}: {item['reason']}")
        for item in deps["outside_pack"]:
            print(f"    OUTSIDE PACK: {item['file']} (in {item['referenced_by']})")
        for rel in deps["accepted_unresolved"]:
            print(f"    accepted unresolved: {rel}")
        if deps["runtime_materials"]:
            print(f"    runtime materials (Kit supplies these): {', '.join(deps['runtime_materials'])}")
        if folder.get("unused"):
            print(f"  unused, information only ({folder['unused_count']}): {folder['unused_note']}")
            for rel in folder["unused"]:
                print(f"    {rel}")


def _doctor_pack(args) -> int:
    pack = args.pack
    manifest = asset_cache.manifest() if pack == asset_cache.PACK else None
    closure = None if args.pins_only else packs.ClosureBackend(args.usd_python)
    report = packs.doctor_pack(pack, asset_cache.stores(), closure=closure, entries=args.entry, manifest=manifest)
    problems = [p for folder in report["folders"] for p in folder["problems"]]
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_pack_report(report)
        print()
        if problems:
            print(f"PROBLEMS ({len(problems)}):")
            for problem in problems:
                print(f"  {problem}")
        elif report["dependencies_checked"]:
            print("OK")
        elif args.pins_only:
            print("OK for pins; dependencies were not checked (--pins-only)")
    if problems or not (report["dependencies_checked"] or args.pins_only):
        return 1
    return 0


def cmd_packs(args) -> int:
    pack, source = asset_cache.selected_pack_info()
    listing = packs.list_packs(asset_cache.stores())
    if args.json:
        print(json.dumps(dict(selected_pack=pack, pack_source=source, listing=listing), indent=2))
        return 0
    print(f"selected pack: {pack} ({source})")
    for entry in listing:
        print(f"{entry['store']}" + ("" if entry["exists"] else "  (does not exist yet)"))
        for item in entry["packs"]:
            print(f"  {item['name']}: {item['pinned']} pinned, {item.get('entries', 0)} entry file(s)")
        if entry["folders"]:
            print(f"  other folders, not packs: {', '.join(entry['folders'])}")
    return 0


def cmd_create(args) -> int:
    store, pack_dir = _pack_dir(args.store, args.pack)
    _private_dirs(pack_dir)
    if not (pack_dir / asset_cache.PACK_FILE).exists():
        packs.write_pack_file(pack_dir, [])
    print(f"pack folder ready: {pack_dir}")
    return 0


def cmd_pack_build(args) -> int:
    store, pack_dir = _pack_dir(args.store, args.pack)
    from_dirs = []
    if args.from_pack:
        from_dirs = [(s / args.from_pack).resolve() for s in asset_cache.stores() if (s / args.from_pack).is_dir()]
        if not from_dirs:
            raise CliError(f"source pack {args.from_pack!r} is in no store (see `isaac_assets.py packs`)")
    closure = packs.ClosureBackend(args.usd_python)
    if not closure.available():
        raise CliError(packs.unavailable_message())
    sources = [str(Path(s).expanduser().resolve()) if s.startswith(("/", "~", "./", "../")) or Path(s).exists() else s
               for s in args.sources]
    notes: list = []
    report = packs.build_pack(
        pack_dir, sources, closure, from_pack_dirs=from_dirs,
        layout_root=Path(args.layout_root).expanduser().resolve() if args.layout_root else None,
        allow_unresolved=args.allow_unresolved, dry_run=args.dry_run, force=args.force, notes=notes,
    )
    if args.json:
        print(json.dumps({**report, "notes": notes}, indent=2))
        return 0
    verb = "would copy" if args.dry_run else "copied"
    print(f"{report['files']} file(s) in the closure, {report['bytes'] / 1048576:.1f} MB; {verb} into {pack_dir}")
    print(f"entry files: {', '.join(report['entries'])}")
    for key in ("unresolved", "remote", "outside", "unreadable"):
        for item in report[key]:
            print(f"  {key}: {item.get('reference') or item.get('file')} (in {item['referenced_by']})")
    for item in report["unfollowable"]:
        print(f"  could not follow: {item['file']}: {item['reason']}")
    if report["runtime_materials"]:
        print(f"  runtime materials (Kit supplies these): {', '.join(report['runtime_materials'])}")
    for note in notes:
        print(f"note: {note}")
    verified = report.get("verified")
    if verified:
        state = "self-contained" if verified.get("self_contained") else "NOT self-contained"
        print(f"checked the new pack: {state}")
    return 0


def cmd_doctor(args) -> int:
    if args.pack:
        return _doctor_pack(args)
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


def _is_root_entry(name: str) -> bool:
    """True for the archive's own root directory entry (``.``, ``./`` or empty)."""
    return all(part in ("", ".") for part in PurePosixPath(name).parts)


def _pack_dir(store_arg: str | None, pack: str | None) -> tuple[Path, Path]:
    """The store and pack folder to write. No --pack means the selected pack (default ur5e-dt)."""
    store = Path(store_arg).expanduser() if store_arg else asset_cache.cache_dir()
    return packs.pack_dir(store, pack or asset_cache.selected_pack())


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


def cmd_unpin(args) -> int:
    store, pack_dir = _pack_dir(args.store, args.pack)
    removed = packs.unpin_files(pack_dir, args.names)
    print(f"removed {len(removed)} pin(s) from {pack_dir / asset_cache.PINS_FILE}; the files are still there")
    print("To get NVIDIA's copy of a public file back: python3 scripts/fetch_ur5e_assets.py --force")
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
            if rel in (asset_cache.PINS_FILE, asset_cache.PACK_FILE):
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
                shallow = subprocess.run(
                    ["git", "-C", str(checkout), "rev-parse", "--is-shallow-repository"],
                    capture_output=True, text=True,
                ).stdout.strip()
                if shallow == "true":
                    raise CliError(
                        f"{checkout} is a shallow clone, so the old assets in its history are not "
                        "available: run `git fetch --unshallow` there, or pass --git-rev"
                    )
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
        detail = (getattr(exc, "stderr", None) or b"")
        detail = detail.decode(errors="replace") if isinstance(detail, bytes) else detail
        reason = detail.strip().splitlines()[-1] if detail.strip() else str(exc)
        raise CliError(f"cannot read old assets from git history of {checkout}: {reason}") from exc
    target = scratch / "legacy"
    _extract_archive(archive, target, Limits(DEFAULT_MAX_MEMBERS, DEFAULT_MAX_FILE_BYTES, DEFAULT_MAX_TOTAL_BYTES))
    found = target.joinpath(*LEGACY_REPO_ASSETS.split("/"))
    return found if found.is_dir() else None


def _legacy_checkout_assets(checkout: Path, rev: str | None, scratch: Path) -> tuple[list[tuple[Path, str]], bool]:
    """Return ``([(folder, label), ...], incomplete)`` for the checkout's old
    ``exts/ur5e-dt/assets`` content, lowest priority first.

    After pulling the code-only repository, the tracked assets are gone from the working
    tree while your untracked copies (a lab robot, the bench) are still there. So both are
    read: the folder from git history (``rev``, default: the last commit that had it),
    then the working tree, whose files win. With an explicit ``rev`` only history is read.
    ``incomplete`` is True when history could not be read and only the working tree was used.
    """
    folders: list[tuple[Path, str]] = []
    incomplete = False
    tree = checkout.joinpath(*LEGACY_REPO_ASSETS.split("/"))
    has_tree = rev is None and tree.is_dir() and any(p.is_file() for p in tree.rglob("*"))
    if (checkout / ".git").exists():
        try:
            old = _git_legacy_assets(checkout, rev, scratch)
        except CliError as exc:
            if not has_tree:
                raise
            print(f"warning: {exc}; using only the files still in {tree}", file=sys.stderr)
            old, incomplete = None, True
        if old is not None:
            folders.append((old, "checkout history"))
    elif rev is not None:
        raise CliError(f"--git-rev needs a git checkout: {checkout}")
    if has_tree:
        folders.append((tree, "checkout"))
    return folders, incomplete


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
    entries = [e for g in asset_cache.manifest().get("fetch", []) for e in g.get("files", [])]
    expected = {e.get("cache_path", e["target"]): e.get("sha256") for e in entries}
    canonical = {e["target"]: e["cache_path"] for e in entries if e.get("cache_path", e["target"]) != e["target"]}
    sources: dict[str, Path] = {}
    origins: dict[str, str] = {}
    old_pins: dict[str, str] = {}  # pins the files had in an old asset folder
    damaged: list[str] = []
    scratch = Path(tempfile.mkdtemp(prefix="isaac-assets-migrate-"))
    try:
        folders, incomplete = _legacy_checkout_assets(checkout, args.git_rev, scratch)
        for legacy, label in folders:
            for current, dirs, files in os.walk(legacy, followlinks=False):
                for name in files:
                    path = Path(current) / name
                    if path.is_symlink() or not path.is_file() or name.endswith(".pyc"):
                        continue
                    rel = _clean_relpath(path.relative_to(legacy).as_posix(), "file name")
                    # NVIDIA's exact file at its old logical name goes to its canonical place.
                    if rel in canonical and sha256(path) == expected.get(canonical[rel]):
                        sources.pop(rel, None)
                        rel = canonical[rel]
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
                digest = sha256(path)
                if pin and digest != pin:
                    damaged.append(f"{path} (does not match its pin in {old})")
                    continue
                if not pin and rel in expected and expected[rel] and digest != expected[rel]:
                    # The old resolver refused this file too (an interrupted fetch or a swap).
                    damaged.append(f"{path} (is not NVIDIA's file and was never pinned)")
                    continue
                sources[rel], origins[rel] = path, str(old)
                if pin:
                    old_pins[rel] = pin
        if damaged:
            print("skipping file(s) that fail their check:\n  " + "\n  ".join(damaged)
                  + "\nthe other files are migrated; delete these or, if they are yours, add them "
                  "again with `isaac_assets.py add`", file=sys.stderr)
        if not sources:
            print("nothing to migrate: no old in-repo assets or old asset folders found")
            return 1 if damaged or incomplete else 0
        # A file at a path the manifest downloads from NVIDIA stays manifest-verified (no pin)
        # only when it is NVIDIA's exact file. A different one from the old checkout (a
        # lab-modified robot, a copy from another Isaac release) or one an old folder pinned
        # is pinned as yours.
        pinned = asset_cache.pins(pack_dir) if pack_dir.is_dir() else {}
        own, public, kept = {}, {}, 0
        for rel, path in sources.items():
            if rel not in expected:
                own[rel] = path
            elif rel in pinned:
                # Your pinned copy always wins over an old one, as in fetch_ur5e_assets.py.
                if sha256(path) != pinned[rel]:
                    kept += 1
            elif rel not in old_pins and expected[rel] and sha256(path) == expected[rel]:
                public[rel] = path
            else:
                own[rel] = path
        print(f"migrating {len(own) + len(public)} file(s) into {pack_dir}")
        if kept:
            print(f"keeping {kept} file(s) you pinned yourself instead of a different old copy")
        if args.dry_run:
            for rel in sorted(own):
                print(f"  {rel}  (from {origins[rel]}, pinned as yours)")
            for rel in sorted(public):
                print(f"  {rel}  (from {origins[rel]}, NVIDIA's file)")
            return 1 if damaged or incomplete else 0
        a1, i1 = _install_many(own, pack_dir, args.force, pin=True)
        a2, i2 = _install_many(public, pack_dir, args.force, pin=False)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"added {a1 + a2}, already present {i1 + i2}; check with `isaac_assets.py doctor`")
    if incomplete:
        print("warning: the checkout's git history could not be read, so files git tracked "
              "there may be missing; fix the error above and run migrate again", file=sys.stderr)
    return 1 if damaged or incomplete else 0


def cmd_adopt(args) -> int:
    src = Path(args.src).expanduser().resolve()
    if not src.is_dir():
        raise CliError(f"not a folder: {src}")
    store, pack_dir = _pack_dir(args.store, args.pack or src.name)
    suffixes = ADOPT_SUFFIXES | {("." + e.lstrip(".")).lower() for e in (args.include or [])}
    sources = packs.named_sources(src, args.file, suffixes) if args.file else _adopt_sources(src, suffixes)
    if not sources:
        print(f"no asset files found in {src}")
        return 0
    total = sum(p.stat().st_size for p in sources.values())
    print(f"{len(sources)} asset file(s), {total / 1048576:.1f} MB, from {src} into {pack_dir}")
    if args.dry_run:
        for rel in sorted(sources):
            print(f"  {rel}")
        return 0
    entries, how = packs.adopted_entries(sources, args.entry)
    added, identical = _install_many(sources, pack_dir, args.force, pin=True)
    if entries:
        packs.write_pack_file(pack_dir, entries)
        print(f"entry files ({how}): {', '.join(entries)}")
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
    doctor.add_argument("--pack", help="check this pack's files and USD dependencies")
    doctor.add_argument("--entry", action="append", metavar="RELPATH",
                        help="with --pack: an entry file (default: from the pack, else the twin's names)")
    doctor.add_argument("--usd-python", metavar="PYTHON",
                        help="with --pack: a Python with usd-core (default: $ISAAC_MCP_USD_PYTHON)")
    doctor.add_argument("--pins-only", action="store_true", help="with --pack: skip the dependency check")
    doctor.set_defaults(func=cmd_doctor)

    listing = sub.add_parser("packs", help="list the packs in every store")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_packs)

    create = sub.add_parser("create", help="create an empty pack folder")
    create.add_argument("pack", metavar="NAME")
    create.add_argument("--store", metavar="DIR", help="asset folder (default: the primary one)")
    create.set_defaults(func=cmd_create)

    build = sub.add_parser("pack-build", help="copy USD files and only what they depend on into a pack")
    build.add_argument("sources", metavar="USD", nargs="+", help="a USD path, or a path inside --from-pack")
    build.add_argument("--pack", required=True)
    build.add_argument("--from-pack", metavar="NAME", help="pack that source names and logical references come from")
    build.add_argument("--layout-root", metavar="DIR", help="folder the pack layout is relative to (default: the source's folder)")
    build.add_argument("--store", metavar="DIR", help="asset folder to write (default: the primary one)")
    build.add_argument("--usd-python", metavar="PYTHON", help="a Python with usd-core (default: $ISAAC_MCP_USD_PYTHON)")
    build.add_argument("--allow-unresolved", action="store_true", help="build even if references are missing; they are recorded")
    build.add_argument("--dry-run", action="store_true")
    build.add_argument("--force", action="store_true", help="replace different files")
    build.add_argument("--json", action="store_true")
    build.set_defaults(func=cmd_pack_build)

    add = sub.add_parser("add", help="copy a file, directory or archive into a store")
    add.add_argument("src", metavar="SRC")
    add.add_argument("--store", metavar="DIR", help="store directory (default: first user store)")
    add.add_argument("--pack", help="pack folder (default: the selected pack, else ur5e-dt)")
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
    adopt.add_argument("--file", action="append", metavar="RELPATH", help="copy only this file or folder inside FOLDER")
    adopt.add_argument("--entry", action="append", metavar="RELPATH", help="root USD file (default: a sole top-level USD)")
    adopt.add_argument("--dry-run", action="store_true")
    adopt.add_argument("--force", action="store_true", help="replace different files")
    adopt.set_defaults(func=cmd_adopt)

    pin = sub.add_parser("pin", help="regenerate SHA256SUMS for a pack folder")
    pin.add_argument("--store", metavar="DIR")
    pin.add_argument("--pack", help="pack folder (default: the selected pack, else ur5e-dt)")
    pin.set_defaults(func=cmd_pin)

    unpin = sub.add_parser("unpin", help="remove pins, so NVIDIA's check and fetch --force apply again")
    unpin.add_argument("names", metavar="RELPATH", nargs="+", help="file names as listed in SHA256SUMS")
    unpin.add_argument("--store", metavar="DIR")
    unpin.add_argument("--pack", help="pack folder (default: the selected pack, else ur5e-dt)")
    unpin.set_defaults(func=cmd_unpin)
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

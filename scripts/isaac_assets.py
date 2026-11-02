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
import json
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile
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


def _extract_archive(archive: Path, into: Path, limits: Limits | None = None) -> None:
    """Extract regular files only, validating every member; owners are not kept."""
    limits = limits or Limits(DEFAULT_MAX_MEMBERS, DEFAULT_MAX_FILE_BYTES, DEFAULT_MAX_TOTAL_BYTES)
    names: set[str] = set()
    if archive.name.lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(archive)
        except zipfile.BadZipFile as exc:
            raise CliError(f"not a valid zip archive: {archive} ({exc})") from exc
        with zf:
            for info in zf.infolist():
                limits.member(info.filename)
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
                with zf.open(info) as src:
                    _copy_stream(src, into / rel, limits, rel)
        return
    try:
        tf = tarfile.open(archive, "r:*")
    except (tarfile.TarError, OSError) as exc:
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


def _default_mode() -> int:
    """Permission bits a plain file would get (mkstemp creates 0600)."""
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def _temp_beside(target: Path) -> tuple[int, Path]:
    """Create a unique, exclusively-opened temp file in *target*'s directory."""
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".part")
    os.chmod(name, _default_mode())
    return fd, Path(name)


def _publish(tmp: Path, target: Path, force: bool) -> None:
    """Move *tmp* to *target*; without ``force`` never replace anything that exists."""
    if force:
        os.replace(tmp, target)
        return
    try:
        os.link(tmp, target)
    except FileExistsError:
        tmp.unlink(missing_ok=True)
        raise CliError(f"refusing to overwrite (use --force): {target}") from None
    except OSError:
        # Filesystem without hard links: fall back to a check followed by a rename.
        if target.exists() or target.is_symlink():
            tmp.unlink(missing_ok=True)
            raise CliError(f"refusing to overwrite (use --force): {target}") from None
        os.replace(tmp, target)
        return
    tmp.unlink(missing_ok=True)


def _install_file(src: Path, target: Path, pack_dir: Path, force: bool) -> None:
    """Copy *src* to *target* without following symlinks planted under the pack folder."""
    parent = target.parent
    while parent != pack_dir:
        if parent.is_symlink():
            raise CliError(f"refusing destination under a symlinked directory: {parent}")
        if parent == parent.parent:
            raise CliError(f"destination escapes the pack folder: {target}")
        parent = parent.parent
    fd, tmp = _temp_beside(target)
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as handle:
            shutil.copyfileobj(handle, out, 1 << 20)
        _publish(tmp, target, force)
    finally:
        tmp.unlink(missing_ok=True)


def _write_pins(pack_dir: Path, entries: dict[str, str]) -> None:
    lines = [f"{digest}  {name}\n" for name, digest in sorted(entries.items())]
    target = pack_dir / asset_cache.PINS_FILE
    fd, tmp = _temp_beside(target)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            out.write("".join(lines))
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


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
            _extract_archive(
                src,
                scratch,
                Limits(args.max_members, args.max_file_bytes, args.max_total_bytes),
            )
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
        for rel in sources:
            target = pack_dir / rel
            if target.is_symlink() or _inside_repo(target.parent):
                raise CliError(f"refusing unsafe destination: {target}")

        pins = dict(asset_cache.pins(pack_dir))
        for rel in sorted(sources):
            target = pack_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            _install_file(sources[rel], target, pack_dir, args.force)
            digest = sha256(target)
            pins[rel] = digest
            print(f"added {rel}  sha256:{digest[:12]}")
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

"""Create, adopt, pin, check and build asset packs. Standard library only; runs without Kit.

``scripts/isaac_assets.py`` (run by the user) and the host-side ``assets__pack_*`` MCP tools share
this code. The tools add the trust rules in :func:`run_tool`; the rules are documented in
``docs/ASSETS.md``. This module never prints from a tool path: stdout is the MCP protocol there.
USD files are parsed only by the helper process or the selected Kit (``usd_closure``).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

from . import assets, material_refs, urdf_refs

PINS_FILE = assets.PINS_FILE
PACK_FILE = assets.PACK_FILE
USD_SUFFIXES = {".usd", ".usda", ".usdc", ".usdz"}
USD_PYTHON_ENV = "ISAAC_MCP_USD_PYTHON"
CLOSURE_TIMEOUT_S = 600
MAX_ADOPT_FILES = 20_000
MAX_REPORTED = 200

# File types copied by ``adopt``: scene and robot descriptions, meshes, materials and
# textures, plus layout JSON. Extend with --include.
ADOPT_SUFFIXES = {
    ".usd", ".usda", ".usdc", ".usdz", ".urdf", ".xacro", ".sdf", ".mjcf",
    ".obj", ".stl", ".dae", ".fbx", ".glb", ".gltf", ".ply", ".mtl",
    ".mdl", ".mtlx", ".png", ".jpg", ".jpeg", ".exr", ".hdr", ".tga", ".tif", ".tiff",
}
ADOPT_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "build", "dist", ".tox"}


class PackError(ValueError):
    """An expected refusal: the CLI prints it, a tool returns it as ``status="error"``."""

    def __init__(self, message: str, code: str = "REFUSED", **details):
        super().__init__(message)
        self.code = code
        self.details = details


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inside_repo(path: Path) -> bool:
    root = assets.checkout_root()
    if root is None:
        return False
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def clean_relpath(raw: str, what: str) -> str:
    """Validate a relative asset path; return it in POSIX form."""
    if not raw or "\\" in raw or "\n" in raw or "\r" in raw or "\x00" in raw:
        raise PackError(f"unsafe {what}: {raw!r}")
    posix = PurePosixPath(raw)
    if posix.is_absolute() or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise PackError(f"unsafe {what} (absolute path): {raw!r}")
    parts = [p for p in posix.parts if p != "."]
    if not parts or ".." in parts:
        raise PackError(f"unsafe {what} (empty or contains '..'): {raw!r}")
    for part in parts:
        if part != part.strip() or any(ord(c) < 32 or ord(c) == 127 for c in part):
            raise PackError(
                f"unsupported {what}: names must not start or end with whitespace "
                f"or contain control characters: {raw!r}"
            )
    return "/".join(parts)


def pack_dir(store: Path, pack: str) -> tuple[Path, Path]:
    """Validate a write destination; return ``(store, <store>/<pack>)``."""
    try:
        assets.validate_pack_name(pack)
    except assets.InvalidPackName as exc:
        raise PackError(str(exc), "INVALID_PACK") from exc
    store = store.resolve()
    if inside_repo(store):
        raise PackError(
            f"refusing store inside the repository checkout: {store}\n"
            "Private assets must never land in git; choose a directory outside it "
            "(see `isaac_assets.py where`).",
            "INSIDE_REPOSITORY",
        )
    folder = store / pack
    if inside_repo(folder):
        raise PackError(
            f"refusing pack folder that resolves inside the repository: {folder}", "INSIDE_REPOSITORY"
        )
    return store, folder


# ---------------------------------------------------------------------------
# Safe installation
# ---------------------------------------------------------------------------

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW


def open_dir_chain(pack_folder: Path, parts: tuple[str, ...]) -> int:
    """Open *pack_folder*, then each folder in *parts* below it, creating missing ones.

    Every step uses ``O_NOFOLLOW`` relative to the previous directory handle, so a symlink planted
    anywhere below the pack folder is refused before anything is created through it. Returns a
    directory file descriptor the caller must close.
    """
    fd = os.open(pack_folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in parts:
            try:
                nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, 0o755, dir_fd=fd)
                nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                raise PackError(
                    f"refusing destination: {part!r} under {pack_folder} is a symlink or not a folder"
                ) from exc
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _temp_name(name: str) -> str:
    return f".{name}.{secrets.token_hex(8)}.part"


def publish(dfd: int, tmp: str, name: str, force: bool) -> None:
    """Move *tmp* to *name* inside directory *dfd*; without ``force`` never replace anything."""
    if force:
        os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        return
    try:
        os.link(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
    except FileExistsError:
        os.unlink(tmp, dir_fd=dfd)
        raise PackError(f"refusing to overwrite (use --force): {name}") from None
    except OSError:
        # No hard links on this filesystem: create the destination exclusively and copy.
        # O_EXCL means a file that appears in the meantime is never replaced.
        try:
            out_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o666, dir_fd=dfd)
        except FileExistsError:
            os.unlink(tmp, dir_fd=dfd)
            raise PackError(f"refusing to overwrite (use --force): {name}") from None
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


def install_file(src: Path, rel: str, pack_folder: Path, force: bool) -> None:
    """Copy *src* to ``<pack_folder>/<rel>`` without following symlinks below the pack folder."""
    *parents, name = PurePosixPath(rel).parts
    dfd = open_dir_chain(pack_folder, tuple(parents))
    try:
        def write(out):
            with src.open("rb") as handle:
                shutil.copyfileobj(handle, out, 1 << 20)

        tmp = _write_new_file(dfd, name, write)
        try:
            publish(dfd, tmp, name, force)
        except BaseException:
            _unlink_quietly(dfd, tmp)
            raise
    finally:
        os.close(dfd)


def _replace_text(pack_folder: Path, name: str, text: str) -> None:
    dfd = open_dir_chain(pack_folder, ())
    try:
        tmp = _write_new_file(dfd, name, lambda out: out.write(text.encode("utf-8")))
        try:
            os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            _unlink_quietly(dfd, tmp)
            raise
    finally:
        os.close(dfd)


def write_pins(pack_folder: Path, entries: dict[str, str]) -> None:
    _replace_text(
        pack_folder, PINS_FILE, "".join(f"{digest}  {name}\n" for name, digest in sorted(entries.items()))
    )


def private_dirs(path: Path, notes: list | None = None) -> None:
    """Create *path* (and missing parents) as 0755 and make sure *path* itself, the pack
    folder, is not group- or world-writable when it is the user's own
    (``assets__prepare_workstation`` refuses such folders, and others must not be able to
    swap your assets). Folders above it are left as they are. A note goes to *notes*, or to
    stdout for the CLI."""
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
        note = f"note: removed group/world write permission from {path}"
        if notes is None:
            print(note)
        else:
            notes.append(note)


def install_many(
    sources: dict[str, Path], pack_folder: Path, force: bool, pin: bool, notes: list | None = None
) -> tuple[int, int]:
    """Copy ``rel -> source`` into ``pack_folder`` safely. Returns (added, identical).

    A destination that already holds the same bytes is skipped. One that differs is a
    conflict: everything is checked first, and nothing is copied unless ``force``.
    """
    conflicts, todo, identical = [], {}, 0
    for rel in sorted(sources):
        try:
            rel.encode("utf-8")
        except UnicodeEncodeError:
            raise PackError(f"file name is not valid UTF-8: {rel!r}") from None
        target = pack_folder / rel
        if target.is_symlink() or inside_repo(target.parent):
            raise PackError(f"refusing unsafe destination: {target}")
        if target.exists():
            if not target.is_file():
                raise PackError(f"refusing destination: {target} exists and is not a file")
            if sha256(target) == sha256(sources[rel]):
                identical += 1
                continue
            conflicts.append(rel)
        todo[rel] = sources[rel]
    if conflicts and not force:
        shown = "\n".join(f"  {pack_folder / rel}" for rel in conflicts[:10])
        more = f"\n  ... and {len(conflicts) - 10} more" if len(conflicts) > 10 else ""
        raise PackError(
            f"different files already in the asset folder (use --force to replace):\n{shown}{more}",
            "CONFLICT",
            conflicts=conflicts[:MAX_REPORTED],
        )
    private_dirs(pack_folder, notes)
    pins = dict(assets.pins(pack_folder))
    added = 0
    try:
        for rel in sorted(todo):
            install_file(todo[rel], rel, pack_folder, force)
            if pin:
                pins[rel] = sha256(pack_folder / rel)
            added += 1
    finally:
        if added and pin:
            write_pins(pack_folder, pins)
    return added, identical


def adopt_sources(root: Path, suffixes: set[str]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in ADOPT_SKIP_DIRS and not d.startswith(".")
                         and not (Path(current) / d).is_symlink())
        for name in files:
            path = Path(current) / name
            if path.suffix.lower() not in suffixes or path.is_symlink() or not path.is_file():
                continue
            found[clean_relpath(path.relative_to(root).as_posix(), "file name")] = path
    return found


def named_sources(root: Path, names: list[str], suffixes: set[str]) -> dict[str, Path]:
    """Only these files or folders, relative to *root*. A named file is copied whatever its type."""
    found: dict[str, Path] = {}
    for raw in names:
        rel = clean_relpath(raw, "file")
        path = root / rel
        if path.is_symlink() or any(_is_symlink_part(root, rel)):
            raise PackError(f"refusing symlink: {rel}", "UNSAFE_ENTRY")
        if path.is_dir():
            found.update({f"{rel}/{k}": v for k, v in adopt_sources(path, suffixes).items()})
        elif path.is_file():
            found[rel] = path
        else:
            raise PackError(f"not in {root}: {rel}", "NOT_FOUND")
    return found


def _is_symlink_part(root: Path, rel: str):
    parts = PurePosixPath(rel).parts
    for depth in range(1, len(parts)):
        yield root.joinpath(*parts[:depth]).is_symlink()


def adopted_entries(sources, entries: list | None) -> tuple[list[str], str]:
    """Entry files to record: those named, or a sole top-level USD. Returns ``(names, how)``."""
    if entries:
        names = [clean_relpath(e, "entry") for e in entries]
        unknown = [name for name in names if name not in sources]
        if unknown:
            raise PackError(f"entries are not among the files copied: {unknown[:10]}", "NOT_FOUND")
        return names, "argument"
    top = sorted(rel for rel in sources if "/" not in rel and PurePosixPath(rel).suffix.lower() in USD_SUFFIXES)
    return (top, "inferred") if len(top) == 1 else ([], "none")


# ---------------------------------------------------------------------------
# Listing, pinning
# ---------------------------------------------------------------------------


def read_pack_file(folder: Path) -> dict:
    """Return a pack's ``isaac-mcp-pack.json``, or an empty description. Invalid is an error."""
    path = folder / PACK_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackError(f"cannot read {path}: {exc}", "INVALID_PACK_FILE") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries", []), list):
        raise PackError(f"{path} must be an object with an 'entries' list", "INVALID_PACK_FILE")
    return data


def write_pack_file(folder: Path, entries, accepted_unresolved=()) -> None:
    """Record a pack's entry files. Existing entries stay; this file holds no absolute path."""
    old = read_pack_file(folder)
    doc = dict(
        version=1,
        entries=sorted({*old.get("entries", []), *entries}),
        accepted_unresolved=sorted({*old.get("accepted_unresolved", []), *accepted_unresolved}),
    )
    _replace_text(folder, PACK_FILE, json.dumps(doc, indent=2) + "\n")


def _pack_files(folder: Path) -> tuple[dict[str, Path], list[str]]:
    """Regular files of a pack by relative path, and the symlinks or odd names left out."""
    found: dict[str, Path] = {}
    skipped: list[str] = []
    for current, _dirs, files in os.walk(folder, followlinks=False):
        for name in files:
            path = Path(current) / name
            rel = path.relative_to(folder).as_posix()
            if rel in (PINS_FILE, PACK_FILE):
                continue
            if path.is_symlink() or not path.is_file():
                skipped.append(rel)
                continue
            try:
                clean_relpath(rel, "file name")
            except PackError:
                skipped.append(rel)
                continue
            found[rel] = path
    return found, sorted(skipped)


def list_packs(stores: list[Path]) -> list[dict]:
    """Packs in each store: folders with pins or a pack file, and the twin's by name.

    Other folders are listed as ``folders``: they may be flat legacy assets, not packs.
    """
    report = []
    for store in stores:
        entry: dict = dict(store=str(store), exists=store.is_dir(), packs=[], folders=[])
        if entry["exists"]:
            for child in sorted(store.iterdir(), key=lambda p: p.name):
                if not child.is_dir() or child.name.startswith("."):
                    continue
                marked = (child / PINS_FILE).is_file() or (child / PACK_FILE).is_file()
                if not marked and child.name != assets.DEFAULT_PACK:
                    entry["folders"].append(child.name)
                    continue
                item = dict(name=child.name, pinned=len(assets.pins(child)), symlink=child.is_symlink())
                try:
                    item["entries"] = len(read_pack_file(child).get("entries", []))
                except PackError as exc:
                    item["error"] = str(exc)
                entry["packs"].append(item)
        report.append(entry)
    return report


def pin_changes(folder: Path) -> dict:
    """Compare a pack's files with its pins, hashing every file."""
    pinned = assets.pins(folder)
    files, skipped = _pack_files(folder)
    current = {rel: sha256(path) for rel, path in files.items()}
    return dict(
        current=current,
        new=sorted(rel for rel in current if rel not in pinned),
        changed=[dict(file=rel, pinned=pinned[rel], actual=digest)
                 for rel, digest in sorted(current.items()) if rel in pinned and pinned[rel] != digest],
        removed=sorted(rel for rel in pinned if rel not in current),
        skipped=skipped,
    )


def pin_pack(folder: Path, *, force: bool = False, files: list | None = None) -> dict:
    """Pin a pack. A pin that would change needs ``force`` or the file named in *files*.

    Re-pinning accepts whatever bytes are present, including damage, so it is never implicit.
    Nothing is written when anything is refused.
    """
    report = pin_changes(folder)
    named = set(files or [])
    unknown = sorted(named - set(report["current"]) - set(report["removed"]))
    if unknown:
        raise PackError(f"not files of this pack: {unknown[:10]}", "NOT_FOUND")
    changed = {item["file"] for item in report["changed"]}
    blocked = sorted(rel for rel in changed | set(report["removed"]) if not force and rel not in named)
    if blocked:
        raise PackError(
            "These pins would change. Check each file, then name it in files, or pass force=true "
            "only if every change is intended.",
            "PIN_CHANGED",
            changed=[c for c in report["changed"] if c["file"] in blocked][:MAX_REPORTED],
            removed=[r for r in report["removed"] if r in blocked][:MAX_REPORTED],
        )
    pins = dict(assets.pins(folder))
    for rel in report["removed"]:
        if force or rel in named:
            pins.pop(rel, None)
    for rel, digest in report["current"].items():
        if not named or rel in named or force:
            pins[rel] = digest
    write_pins(folder, pins)
    return dict(
        pinned=len(pins), new=report["new"][:MAX_REPORTED], changed=report["changed"][:MAX_REPORTED],
        removed=report["removed"][:MAX_REPORTED], skipped=report["skipped"][:MAX_REPORTED],
    )


def unpin_files(folder: Path, names: list[str]) -> list[str]:
    """Remove pin lines; the files stay. Returns the names removed. Unknown names are an error."""
    pins = dict(assets.pins(folder))
    unknown = [name for name in names if name not in pins]
    if unknown:
        raise PackError(f"not pinned in {folder / PINS_FILE}: {', '.join(unknown)}", "NOT_FOUND")
    for name in names:
        del pins[name]
    write_pins(folder, pins)
    return list(names)


# ---------------------------------------------------------------------------
# USD dependency closure
# ---------------------------------------------------------------------------


def usd_python() -> str | None:
    """The interpreter that can parse USD on the host: the configured one, or this one."""
    configured = os.environ.get(USD_PYTHON_ENV, "").strip()
    if configured:
        return configured
    return sys.executable if importlib.util.find_spec("pxr") else None


def helper_closure(paths: list[str], python: str, timeout: float = CLOSURE_TIMEOUT_S) -> list[dict]:
    """Run ``usd_closure`` under *python*, isolated from this process and working directory."""
    helper = Path(__file__).with_name("usd_closure.py")
    try:
        done = subprocess.run(
            [python, "-I", str(helper)], input=json.dumps(paths), capture_output=True, text=True,
            timeout=timeout, cwd=str(helper.parent.parent),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PackError(f"USD helper failed under {python}: {exc}", "USD_FAILED") from exc
    marker = "ISAAC_MCP_CLOSURE "
    for line in reversed(done.stdout.splitlines()):
        if line.startswith(marker):
            return json.loads(line[len(marker):])
    raise PackError(
        f"USD helper under {python} exited {done.returncode}: {done.stderr.strip()[-500:]}", "USD_FAILED"
    )


def run_closure(closure, paths: list[str], folder_of, projects: dict | None = None) -> list[dict]:
    """Closures for a mix of files: URDFs are read here, with no USD parser, and USD goes to *closure*.

    *folder_of* gives the folder a URDF's packages are looked for in. Nothing outside it is searched.
    """
    projects = {} if projects is None else projects
    results, usd = [], []
    for path in paths:
        if not urdf_refs.is_urdf(path):
            usd.append(path)
            continue
        folder = Path(folder_of(path))
        project = projects.setdefault(str(folder), urdf_refs.project_from_folder(folder))
        results.append(urdf_refs.closure_result(path, project, folder))
    return results + (list(closure(usd)) if usd else [])


class ClosureBackend:
    """Compute closures with a host USD helper, else the selected Kit, and say which ran."""

    def __init__(self, python: str | None = None, kit_call=None, trusted=None):
        self.python = python or usd_python()
        self.kit_call = kit_call
        self.trusted = trusted
        self.name = "usd_helper" if self.python else "kit" if kit_call else None

    def available(self) -> bool:
        return self.name is not None

    def __call__(self, paths: list[str]) -> list[dict]:
        if self.trusted is not None:
            for path in paths:
                if not self.trusted(Path(path)):
                    raise PackError(
                        f"Will not parse USD outside approved roots and user stores: {path}. "
                        "A project store needs approving as a root first.",
                        "ROOT_NOT_APPROVED",
                    )
        if self.python:
            return helper_closure(paths, self.python)
        if self.kit_call:
            return self.kit_call(paths)
        raise PackError(unavailable_message(), "USD_UNAVAILABLE")


def unavailable_message() -> str:
    return (
        "No USD parser is available: the MCP host has no pxr and no Kit bridge answered. Set "
        f"{USD_PYTHON_ENV} to a Python that has usd-core (no GPU or Kit needed), or start Kit with "
        "sim-tools and select its bridge."
    )


# USD normalizes a reference, so "https://x" comes back as "https:/x". Two letters or more, so a
# Windows drive is not a scheme.
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+:/")


def plain_path(value: str) -> str:
    """Turn a ``file:`` URL a resolver may report into a path."""
    if value.startswith("file://"):
        from urllib.parse import unquote, urlparse

        return unquote(urlparse(value).path)
    return value[5:] if value.startswith("file:/") else value


def classify_reference(reference: str) -> str:
    """Kind of an unresolved reference: ``remote``, ``runtime``, ``logical`` or ``file``.

    ``runtime`` is a bare MDL name such as ``OmniPBR.mdl``, which Kit's material library supplies
    and standalone USD cannot find. ``logical`` is a relative name with nothing to anchor it, as
    in a scene whose references are mapped through the resolver at run time.
    """
    if _URL.match(reference) and not reference.startswith("file:"):
        return "remote"
    if "/" not in reference and reference.lower().endswith(".mdl"):
        return "runtime"
    return "file" if plain_path(reference).startswith("/") else "logical"


def _under(path: Path, folder: Path) -> str | None:
    try:
        return path.relative_to(folder).as_posix()
    except ValueError:
        return None


def trusted_by_roots(broker, stores: list[Path]):
    """Return a check that a real path is under an approved root or a given store.

    Pass user stores only. A project store comes from an ``isaac-mcp.json`` found by walking up
    from the working directory, so a cloned repository could name any folder: that must not count
    as an approval. A project's files stay usable by approving their folder as a root.
    """
    folders = [root.resolve() for root, _ in (broker.roots.values() if broker else ())]
    folders += [store.resolve() for store in stores]

    def check(path: Path) -> bool:
        return any(_under(path.resolve(), folder) is not None for folder in folders)

    return check


def build_pack(
    pack_folder: Path,
    sources: list[str],
    closure,
    *,
    from_pack_dirs: list[Path] = (),
    layout_root: Path | None = None,
    trusted=None,
    allow_unresolved: bool = False,
    dry_run: bool = False,
    force: bool = False,
    notes: list | None = None,
    urdf_root=None,
) -> dict:
    """Copy USD or URDF files and only what they depend on into a pack, keeping relative layout, and pin.

    A source is an absolute path or, with *from_pack_dirs*, a path inside that pack. A source
    outside both *from_pack_dirs* and *layout_root* (a scene whose references are logical names)
    is read for its references and not copied. Those names are looked up in *from_pack_dirs*.

    Anything unresolved, remote or out of bounds refuses the whole build unless
    *allow_unresolved*, which still copies nothing from outside. Runtime MDL names are reported
    and never block. The copy is checked afterwards by computing the closure in the new pack.
    """
    notes = [] if notes is None else notes
    from_dirs = [Path(d) for d in from_pack_dirs]
    base_of: dict[str, Path | None] = {}  # queued real path -> layout folder, None = read only
    queue: list[str] = []
    search_folder: dict[str, Path] = {}  # URDF -> the folder its ROS packages are looked for in
    urdf_projects: dict = {}

    def layout_for(path: Path) -> Path | None:
        for folder in ([layout_root] if layout_root else []) + from_dirs:
            if _under(path, folder) is not None:
                return folder
        return None

    def enqueue(path: Path, base: Path | None) -> None:
        if str(path) not in base_of:
            base_of[str(path)] = base
            queue.append(str(path))

    for source in sources:
        if source.startswith("/"):
            path = Path(source).resolve()
        else:
            rel = clean_relpath(source, "source")
            found = [d / rel for d in from_dirs if (d / rel).is_file()]
            if not found:
                raise PackError(
                    f"source {source!r} is not an absolute path and is not in the source pack",
                    "NOT_FOUND",
                )
            path = found[0].resolve()
        if trusted is not None and not trusted(path):
            raise PackError(
                f"source is outside approved roots and user stores: {path}", "ROOT_NOT_APPROVED"
            )
        if not path.is_file():
            raise PackError(f"source not found: {path}", "NOT_FOUND")
        if urdf_refs.is_xacro(path):
            raise PackError(f"{path.name} is a xacro file, not a URDF. {urdf_refs.XACRO_HELP}", "XACRO")
        is_urdf = urdf_refs.is_urdf(path)
        if path.suffix.lower() not in USD_SUFFIXES and not is_urdf:
            raise PackError(f"source is not a USD or URDF file: {path}", "UNSUPPORTED_FORMAT")
        base = layout_for(path)
        if is_urdf:
            base = base or (urdf_root(path) if urdf_root else path.parent)
            search_folder[str(path)] = base
            # A package known only by the name of this folder: lay out from one level up, so the copy
            # holds a folder of that name. Each file still has to pass the trust check below.
            peek = run_closure(closure, [str(path)], search_folder.__getitem__, urdf_projects)[0]
            if peek.get("root_folder_package"):
                base = base.parent
        elif base is None and not from_dirs:
            base = path.parent
        enqueue(path, base)

    files: dict[str, Path] = {}
    entries: set[str] = set()
    blocked: dict[str, list] = dict(unresolved=[], remote=[], outside=[], unreadable=[])
    runtime: set[str] = set()
    file_base: dict[Path, Path] = {}
    missing_textures: list[dict] = []
    urdf_unfollowable: list[dict] = []
    urdf_packages: dict = {}
    while queue:
        batch, queue[:] = list(queue), []
        for result in run_closure(closure, batch, search_folder.__getitem__, urdf_projects):
            origin = Path(result["path"])
            base = base_of[str(origin)]
            if result.get("error"):
                raise PackError(f"cannot read {origin}: {result['error']}", "USD_UNREADABLE", file=str(origin))
            runtime.update(result.get("runtime", []))
            if base is not None:
                entries.add(origin.relative_to(base).as_posix())
            for item in result.get("problems", []):
                blocked[item["kind"]].append(
                    dict(reference=item["reference"], reason=item["reason"], referenced_by=str(origin)))
            missing_textures += [{**item, "urdf": origin.name} for item in result.get("missing_textures", [])]
            urdf_unfollowable += result.get("unfollowable", [])
            urdf_packages.update(result.get("packages", {}))
            for dep in [*result["layers"], *result["assets"]]:
                if _URL.match(dep) and not dep.startswith("file:"):
                    blocked["remote"].append(dict(reference=dep, referenced_by=str(origin)))
                    continue
                real = Path(plain_path(dep)).resolve()
                if base is None and real == origin:
                    continue  # the read-only scene itself
                rel = _under(real, base) if base is not None else None
                if rel is None or (trusted is not None and not trusted(real)):
                    blocked["outside"].append(dict(file=str(real), referenced_by=str(origin)))
                    continue
                rel = clean_relpath(rel, "dependency")
                if rel in files and files[rel] != real:
                    raise PackError(f"two different files map to {rel}: {files[rel]} and {real}", "CONFLICT")
                files[rel] = real
                file_base[real] = base
            for reference in result["unresolved"]:
                kind = classify_reference(reference)
                holder = result.get("referenced_by", {}).get(reference, str(origin))
                if kind == "runtime":
                    runtime.add(reference)
                elif kind == "remote":
                    blocked["remote"].append(dict(reference=reference, referenced_by=holder))
                elif kind == "logical":
                    holders = [d for d in from_dirs if (d / reference).is_file()]
                    if holders:
                        enqueue((holders[0] / reference).resolve(), holders[0])
                    else:
                        blocked["unresolved"].append(dict(reference=reference, referenced_by=holder))
                elif Path(plain_path(reference)).exists():
                    blocked["unreadable"].append(dict(reference=reference, referenced_by=holder))
                else:
                    blocked["unresolved"].append(dict(reference=reference, referenced_by=holder))
    # USD stops at MDL and MaterialX files. Follow what they name, as import validation does.
    followed = material_refs.follow(file_base)
    for item in followed["references"]:
        real, holder = item["target"].resolve(), str(item["holder"])
        base = file_base[item["holder"]] if item["holder"] in file_base else None
        if base is None:  # a nested MaterialX document: it sits in its parent's layout
            base = next((b for b in file_base.values() if _under(item["holder"], b) is not None), None)
        rel = _under(real, base) if base is not None else None
        if rel is None or (trusted is not None and not trusted(real)):
            blocked["outside"].append(dict(file=str(real), referenced_by=holder))
        elif not item["exists"]:
            blocked["unresolved"].append(dict(reference=str(real), referenced_by=holder))
        else:
            rel = clean_relpath(rel, "material dependency")
            if rel in files and files[rel] != real:
                raise PackError(f"two different files map to {rel}: {files[rel]} and {real}", "CONFLICT")
            files[rel] = real
    if not files:
        raise PackError("nothing to copy: no source is inside the layout or the source pack", "NOT_FOUND")
    if missing_textures or urdf_unfollowable:
        notes.append("Some meshes name files that are absent or could not be read: see missing_textures and "
                     "unfollowable. Geometry still imports, and self_contained is false.")
    for rel in files:
        if rel in (PINS_FILE, PACK_FILE):
            raise PackError(f"{rel} in the closure would collide with the pack's own files", "CONFLICT")
    report = dict(
        files=len(files), bytes=sum(p.stat().st_size for p in files.values()), entries=sorted(entries),
        unresolved=blocked["unresolved"][:MAX_REPORTED], remote=blocked["remote"][:MAX_REPORTED],
        outside=blocked["outside"][:MAX_REPORTED], unreadable=blocked["unreadable"][:MAX_REPORTED],
        runtime_materials=sorted(runtime), material_references=len(followed["references"]),
        unfollowable=[*followed["unfollowable"], *urdf_unfollowable][:MAX_REPORTED],
        missing_textures=missing_textures[:MAX_REPORTED],
        **({"urdf_packages": urdf_packages} if urdf_packages else {}),
    )
    if followed["unfollowable"]:
        notes.append(UNFOLLOWABLE_NOTE)
    if any(blocked.values()) and not allow_unresolved:
        listed = [
            f"  {kind}: {item.get('reference') or item.get('file')} (in {item['referenced_by']})"
            + (f": {item['reason']}" if item.get("reason") else "")
            for kind, items in blocked.items() for item in items
        ]
        raise PackError(
            "The closure is incomplete, so nothing was copied. Fix the references, or pass "
            "allow_unresolved=true to build without them (files outside are never copied):\n"
            + "\n".join(listed[:20]) + (f"\n  ... and {len(listed) - 20} more" if len(listed) > 20 else ""),
            "UNRESOLVED_DEPENDENCIES",
            **{k: v[:MAX_REPORTED] for k, v in blocked.items() if v},
        )
    if dry_run:
        return {**report, "dry_run": True, "copied": 0, "planned": sorted(files)[:MAX_REPORTED]}
    added, identical = install_many(files, pack_folder, force, pin=True, notes=notes)
    pins = dict(assets.pins(pack_folder))
    for rel in files:  # an identical file that was already there still belongs to the closure
        pins.setdefault(rel, sha256(pack_folder / rel))
    write_pins(pack_folder, pins)
    accepted = sorted({_accepted_name(item["reference"], base_of) for item in blocked["unresolved"]})
    write_pack_file(pack_folder, entries, accepted)
    try:
        verified = check_closure(pack_folder, sorted(entries), closure)
        verified.pop("used", None)
    except PackError as exc:
        verified = dict(checked=False, reason=str(exc))
    return {**report, "dry_run": False, "copied": added, "identical": identical, "pack_dir": str(pack_folder),
            "verified": verified}


def _accepted_name(reference: str, base_of: dict) -> str:
    """Name an accepted unresolved file relative to its layout, so it stays valid in the pack."""
    for base in {b for b in base_of.values() if b is not None}:
        rel = _under(Path(plain_path(reference)), base)
        if rel is not None:
            return rel
    return reference


UNFOLLOWABLE_NOTE = (
    "These material files could not be read statically, so their own files were not followed and "
    "self_contained is false. assets__import_path refuses the same files; the twin's own loading "
    "and Kit's material library may still work."
)


def check_closure(folder: Path, entries: list[str], closure) -> dict:
    """Compute the closure of a pack's entries and sort every dependency into what it means."""
    root = folder.resolve()
    used: set[str] = set()
    out: dict[str, list] = dict(unresolved=[], remote=[], outside_pack=[], unreadable=[])
    runtime: set[str] = set()
    accepted = set(read_pack_file(folder).get("accepted_unresolved", []))
    accepted_found: set[str] = set()
    present = [rel for rel in entries if (folder / rel).is_file()]
    missing = [rel for rel in entries if not (folder / rel).is_file()]
    urdf_projects: dict = {}
    missing_textures: list[dict] = []

    def add_unresolved(reference: str, holder: str) -> None:
        rel = _under(Path(plain_path(reference)), root) or reference
        if rel in accepted:
            accepted_found.add(rel)
        else:
            out["unresolved"].append(dict(reference=rel, referenced_by=_short(holder, root)))

    results = run_closure(closure, [str(root / rel) for rel in present], lambda _: root, urdf_projects) if present else []
    for result in results:
        origin = result["path"]
        if result.get("error"):
            out["unreadable"].append(dict(reference=_short(origin, root), detail=result["error"]))
            continue
        runtime.update(result.get("runtime", []))
        for item in result.get("problems", []):
            if item["kind"] == "unresolved":
                add_unresolved(item["reference"], origin)
            elif item["kind"] == "remote":
                out["remote"].append(dict(reference=item["reference"], referenced_by=_short(origin, root)))
            else:
                out["outside_pack"].append(dict(file=item["reference"], referenced_by=_short(origin, root)))
        missing_textures += [{**item, "urdf": _short(origin, root)} for item in result.get("missing_textures", [])]
        for dep in [*result["layers"], *result["assets"]]:
            if _URL.match(dep) and not dep.startswith("file:"):
                out["remote"].append(dict(reference=dep, referenced_by=_short(origin, root)))
                continue
            real = Path(plain_path(dep)).resolve()
            rel = _under(real, root)
            if rel is None:
                out["outside_pack"].append(dict(file=str(real), referenced_by=_short(origin, root)))
            else:
                used.add(rel)
        for reference in result["unresolved"]:
            holder = result.get("referenced_by", {}).get(reference, origin)
            kind = classify_reference(reference)
            if kind == "runtime":
                runtime.add(reference)
            elif kind == "remote":
                out["remote"].append(dict(reference=reference, referenced_by=_short(holder, root)))
            elif kind == "file" and Path(plain_path(reference)).exists():
                out["unreadable"].append(dict(reference=_short(reference, root), referenced_by=_short(holder, root)))
            else:
                add_unresolved(reference, holder)
    followed = material_refs.follow(root / rel for rel in sorted(used))
    for item in followed["references"]:
        holder = str(item["holder"])
        rel = _under(item["target"].resolve(), root)
        if rel is None:
            out["outside_pack"].append(dict(file=str(item["target"]), referenced_by=_short(holder, root)))
        elif item["exists"]:
            used.add(rel)
        else:
            add_unresolved(str(item["target"]), holder)
    out["unfollowable"] = [dict(file=_short(i["file"], root), reason=i["reason"]) for i in followed["unfollowable"]]
    out["unfollowable"] += [item for result in results for item in result.get("unfollowable", [])]
    out["missing_textures"] = missing_textures
    for key in out:
        unique = {json.dumps(item, sort_keys=True): item for item in out[key]}
        out[key] = [unique[k] for k in sorted(unique)]
    return dict(
        checked=True, entries=entries, missing_entries=missing, used=sorted(used),
        accepted_unresolved=sorted(accepted_found), runtime_materials=sorted(runtime),
        self_contained=not (missing or any(out.values())), **out,
    )


def _short(path: str, root: Path) -> str:
    return _under(Path(plain_path(path)), root) or path


def twin_entries(folder: Path, manifest: dict) -> list[str]:
    """The USD files the twin loads by name that are in this pack, at their stored layout."""
    canonical = {
        e["target"]: e.get("cache_path", e["target"])
        for group in manifest.get("fetch", []) for e in group.get("files", []) if "target" in e
    }
    names = [*manifest.get("remote", {}), *(e["target"] for e in manifest.get("manual", []) if "target" in e),
             *(e["path"] for e in manifest.get("local", []) if "path" in e),
             "gripper/husarion_rg2/rg2_gripper_fixed.usd"]
    found = []
    for name in names:
        stored = name if (folder / name).is_file() else canonical.get(name, name)
        if PurePosixPath(stored).suffix.lower() in USD_SUFFIXES and (folder / stored).is_file():
            found.append(stored)
    return sorted(set(found))


def _pack_symlinks(folder: Path) -> list[str]:
    """Symlinks anywhere in a pack, files or folders. An import refuses the pack for them."""
    found = []
    for current, dirs, files in os.walk(folder, followlinks=False):
        found += [(Path(current) / name).relative_to(folder).as_posix() for name in [*dirs, *files]
                  if (Path(current) / name).is_symlink()]
    return sorted(found)


def doctor_folder(folder: Path, *, closure=None, entries: list | None = None, manifest: dict | None = None) -> dict:
    """Check one pack folder: pins, then the dependency closure of its entry files. Read only.

    Entry files come from *entries*, the pack file, the twin's known names (for its pack), or
    every USD. Only with known entries can a file be called unused: reached by no entry. That is
    information, because code can load files by name.
    """
    manifest = manifest or {}
    tracked = {
        e.get("cache_path", e["target"]): e
        for group in manifest.get("fetch", []) for e in group.get("files", []) if "target" in e
    }
    report = pin_changes(folder)
    verified, unpinned = [], []
    corrupt = [dict(file=c["file"], expected=c["pinned"], actual=c["actual"], kind="pin") for c in report["changed"]]
    for rel in report["new"]:
        entry = tracked.get(rel)
        if entry is None or not entry.get("sha256"):
            unpinned.append(rel)
        elif entry["sha256"] == report["current"][rel]:
            verified.append(rel)
        else:
            corrupt.append(dict(file=rel, expected=entry["sha256"], actual=report["current"][rel], kind="manifest"))
    pinned_ok = len(report["current"]) - len(report["new"]) - len(report["changed"])

    declared = read_pack_file(folder).get("entries", [])
    if entries:
        chosen, entry_source = list(entries), "argument"
    elif declared:
        chosen, entry_source = list(declared), "pack_file"
    elif manifest and twin_entries(folder, manifest):
        chosen, entry_source = twin_entries(folder, manifest), "twin_manifest"
    else:
        chosen = sorted(rel for rel in report["current"]
                        if PurePosixPath(rel).suffix.lower() in USD_SUFFIXES or urdf_refs.is_urdf(rel))
        entry_source = "all_usd"
    for rel in chosen:
        clean_relpath(rel, "entry")

    result = dict(
        pack_dir=str(folder),
        files=dict(total=len(report["current"]), pinned_ok=pinned_ok, manifest_ok=len(verified),
                   unpinned=unpinned[:MAX_REPORTED], unpinned_count=len(unpinned), corrupt=corrupt[:MAX_REPORTED],
                   missing=report["removed"][:MAX_REPORTED], skipped=report["skipped"][:MAX_REPORTED]),
        entry_source=entry_source,
    )
    dependencies = None
    reason = unavailable_message()
    if closure is not None and getattr(closure, "available", lambda: True)():
        try:
            dependencies = check_closure(folder, chosen, closure)
        except PackError as exc:
            reason = str(exc)
    if dependencies is None:
        result["dependencies"] = dict(checked=False, reason=reason, entries=chosen[:MAX_REPORTED])
        result["unused"] = None
    else:
        result["dependencies"] = dependencies
        result["dependencies"]["backend"] = getattr(closure, "name", None)
        used = set(result["dependencies"].pop("used")) | set(chosen)
        if entry_source == "all_usd":
            result["unused"] = None
            result["unused_note"] = "Not computed: pass entries, or build the pack, to say which files are its roots."
        else:
            unused = sorted(set(report["current"]) - used)
            result["unused"] = unused[:MAX_REPORTED]
            result["unused_count"] = len(unused)
            result["unused_note"] = (
                "Information only: no entry reaches these through USD. Code can still load them by name."
            )
    problems = [f"{c['file']}: does not match its {c['kind']} hash" for c in corrupt]
    problems += [f"{rel}: pinned but missing" for rel in report["removed"]]
    deps = result["dependencies"]
    if deps["checked"]:
        problems += [f"{rel}: entry file is missing" for rel in deps["missing_entries"]]
        problems += [f"{u['reference']}: unresolved, referenced by {u['referenced_by']}" for u in deps["unresolved"]]
        problems += [f"{u['reference']}: unreadable USD" for u in deps["unreadable"]]
        problems += [f"{u['reference']}: remote, referenced by {u['referenced_by']}" for u in deps["remote"]]
        problems += [f"{u['file']}: outside the pack, referenced by {u['referenced_by']}" for u in deps["outside_pack"]]
    problems += [f"{rel}: symlink; an import refuses this pack" for rel in _pack_symlinks(folder)]
    if deps["checked"] and deps["unfollowable"]:
        result["warnings"] = [UNFOLLOWABLE_NOTE]
    if deps["checked"] and deps["missing_textures"]:
        result.setdefault("warnings", []).append(
            "Meshes name files that are absent: " + ", ".join(i["reference"] for i in deps["missing_textures"][:10]))
    result["problems"] = problems[:MAX_REPORTED]
    # Never "ok" when the dependency check did not run: a folder listing proves nothing about it.
    result["ok"] = not problems and deps["checked"]
    return result


def doctor_pack(pack: str, stores: list[Path], *, closure=None, entries=None, manifest=None) -> dict:
    """Check a pack in every store holding it; the first copy of a file is the one that loads."""
    assets.validate_pack_name(pack)
    folders = [store / pack for store in stores if (store / pack).is_dir()]
    if not folders:
        raise PackError(
            f"pack {pack!r} is in no store: " + ", ".join(str(s) for s in stores), "PACK_NOT_FOUND"
        )
    reports = [doctor_folder(f, closure=closure, entries=entries, manifest=manifest) for f in folders]
    checked = all(r["dependencies"]["checked"] for r in reports)
    return dict(
        pack=pack, ok=all(r["ok"] for r in reports), dependencies_checked=checked, folders=reports,
    )


# ---------------------------------------------------------------------------
# MCP tools (host side)
# ---------------------------------------------------------------------------


def _context(resolver: assets.Resolver) -> dict:
    """What this process resolved; the host and Kit can differ if their settings differ."""
    project = assets.project_file()
    pack, source = assets.selected_pack_info()
    return dict(
        project_file=str(project) if project else None, selected_pack=pack, pack_source=source,
        stores=[str(s) for s in resolver.stores()], write_store=str(resolver.cache_dir()),
    )


def _absolute(path, what: str) -> Path:
    if not isinstance(path, str) or not path.startswith("/"):
        raise PackError(f"{what} must be an absolute local path", "INVALID_PATH")
    if "\\" in path or ":" in path or any(ord(c) < 32 for c in path):
        raise PackError(f"{what}: URLs, backslashes and control characters are not supported", "INVALID_PATH")
    if any(part in ("", ".", "..") for part in path.split("/")[1:]):
        raise PackError(f"{what}: empty, dot and traversal components are forbidden", "INVALID_PATH")
    return Path(path)


def _approved_root(path: Path, broker) -> Path:
    """Return the approved root holding *path*, checking the root is still the approved one."""
    broker.require_profile()
    real = path.resolve()
    for root_id, (root, _) in broker.roots.items():
        if _under(real, root.resolve()) is not None:
            os.close(broker.root_fd(root_id))
            return root
    raise PackError(
        "The path is outside all user-approved roots. Call assets__roots for setup; no tool can "
        "approve folders.",
        "ROOT_NOT_APPROVED",
    )


def _write_pack(pack: str, resolver: assets.Resolver) -> Path:
    """The destination of a write tool: always the primary user store, never a chosen path."""
    if not isinstance(pack, str):
        raise PackError("pack must be a string", "INVALID_PACK")
    _, folder = pack_dir(resolver.cache_dir(), pack)
    if folder.is_symlink():
        raise PackError(f"refusing symlinked pack folder: {folder}", "UNSAFE_DESTINATION")
    return folder


def _kit_closure(kit_call):
    if kit_call is None:
        return None

    def call(paths):
        try:
            result = kit_call("assets.dependencies", dict(paths=paths))
        except Exception as exc:  # noqa: BLE001 - no bridge, or a Kit without this tool
            raise PackError(f"{unavailable_message()} ({str(exc)[:300]})", "USD_UNAVAILABLE") from exc
        if not isinstance(result, dict) or "results" not in result:
            raise PackError(f"Kit could not compute the closure: {str(result)[:500]}", "USD_FAILED")
        return result["results"]

    return call


def run_tool(canonical: str, params: dict, broker, kit_call=None) -> dict:
    """Run one host-side pack tool. Raises :class:`PackError` for refusals."""
    resolver = assets.Resolver(assets.DEFAULT_PACK)
    context = _context(resolver)
    params = dict(params)

    def named_pack(key: str = "pack") -> str:
        value = params.get(key) or context["selected_pack"]
        try:
            return assets.validate_pack_name(value)
        except assets.InvalidPackName as exc:
            raise PackError(str(exc), "INVALID_PACK") from exc

    # Reads may report on any store; parsing USD and copying files need real approval.
    trusted = trusted_by_roots(broker, resolver.user_stores())
    closure = ClosureBackend(kit_call=_kit_closure(kit_call), trusted=trusted)

    if canonical == "assets.packs":
        return dict(status="ok", **context, listing=list_packs(resolver.stores()))

    if canonical == "assets.pack_doctor":
        pack = named_pack()
        manifest = resolver.manifest() if pack == assets.DEFAULT_PACK else None
        report = doctor_pack(pack, resolver.stores(), closure=closure, entries=params.get("entries"), manifest=manifest)
        return dict(status="ok", **context, **report)

    if canonical == "assets.dependencies":
        paths = params.get("paths") or []
        if not paths or len(paths) > 64:
            raise PackError("paths must list 1..64 USD files", "LIMIT")
        checked = [_absolute(p, "path") for p in paths]
        broker.require_profile()
        for path in checked:
            if not trusted(path):
                raise PackError(
                    f"path is outside approved roots and user stores: {path}", "ROOT_NOT_APPROVED"
                )
        return dict(status="ok", backend=closure.name, results=closure([str(p.resolve()) for p in checked]))

    if canonical == "assets.pack_adopt":
        folder = _write_pack(params.get("pack"), resolver)
        source = params.get("source_path")
        dry_run = bool(params.get("dry_run", False))
        force = bool(params.get("force", False))
        notes: list = []
        if source is None:
            created = not folder.exists()
            if not dry_run:
                private_dirs(folder, notes)
                if not (folder / PACK_FILE).exists():
                    write_pack_file(folder, [])
            return dict(status="ok", **context, pack_dir=str(folder), created=created, dry_run=dry_run, notes=notes)
        src = _absolute(source, "source_path")
        _approved_root(src, broker)
        real = src.resolve()
        if not real.is_dir():
            raise PackError(f"not a folder: {real}", "NOT_FOUND")
        target = folder.resolve()
        if _under(target, real) is not None or _under(real, target) is not None:
            raise PackError("source and pack folder overlap", "OVERLAP")
        extra = params.get("include") or []
        suffixes = ADOPT_SUFFIXES | {("." + str(e).lstrip(".")).lower() for e in extra}
        named = params.get("files")
        sources = named_sources(real, named, suffixes) if named else adopt_sources(real, suffixes)
        if len(sources) > MAX_ADOPT_FILES:
            raise PackError(f"more than {MAX_ADOPT_FILES} files; narrow the folder", "LIMIT")
        entries, entries_how = adopted_entries(sources, params.get("entries"))
        if dry_run:
            return dict(status="ok", **context, pack_dir=str(folder), dry_run=True, files=len(sources),
                        planned=sorted(sources)[:MAX_REPORTED], truncated=len(sources) > MAX_REPORTED,
                        entries=entries, entries_source=entries_how)
        if not sources:
            return dict(status="ok", **context, pack_dir=str(folder), added=0, identical=0,
                        notes=[f"no asset files found in {real}"])
        added, identical = install_many(sources, folder, force, pin=True, notes=notes)
        if entries:
            write_pack_file(folder, entries)
        else:
            notes.append("No entry files recorded: pass entries so assets__pack_doctor can tell what is unused.")
        return dict(status="ok", **context, pack_dir=str(folder), added=added, identical=identical,
                    entries=entries, entries_source=entries_how, notes=notes)

    if canonical == "assets.pack_pin":
        folder = _write_pack(params.get("pack") or context["selected_pack"], resolver)
        if not folder.is_dir():
            raise PackError(f"pack folder does not exist in the write store: {folder}", "PACK_NOT_FOUND")
        return dict(status="ok", **context, pack_dir=str(folder),
                    **pin_pack(folder, force=bool(params.get("force", False)), files=params.get("files")))

    if canonical == "assets.pack_build":
        folder = _write_pack(params.get("pack"), resolver)
        sources = params.get("sources") or []
        if not sources or len(sources) > 64:
            raise PackError("sources must list 1..64 USD or URDF files", "LIMIT")
        broker.require_profile()
        from_dirs = []
        if params.get("from_pack"):
            source_pack = named_pack("from_pack")
            from_dirs = [(s / source_pack).resolve() for s in resolver.user_stores() if (s / source_pack).is_dir()]
            if not from_dirs:
                raise PackError(f"source pack {source_pack!r} is in no user store", "PACK_NOT_FOUND")
        for source in sources:
            if source.startswith("/"):
                _absolute(source, "source")
        layout = None
        if params.get("layout_root"):
            layout = _absolute(params["layout_root"], "layout_root").resolve()
            if not trusted(layout):
                raise PackError(f"layout_root is outside approved roots and user stores: {layout}", "ROOT_NOT_APPROVED")
        if any(_under(folder.resolve(), d) is not None or _under(d, folder.resolve()) is not None for d in from_dirs):
            raise PackError("source pack and destination pack overlap", "OVERLAP")
        # A URDF is read here, so a URDF-only build needs no USD parser.
        if not closure.available() and not all(urdf_refs.is_urdf(s) or urdf_refs.is_xacro(s) for s in sources):
            raise PackError(unavailable_message(), "USD_UNAVAILABLE")

        def urdf_root(path: Path) -> Path:
            """The approved root or pack holding *path*, which is as far as its packages are looked for."""
            for store in resolver.user_stores():
                pack_rel = _under(path, store.resolve())
                if pack_rel and "/" in pack_rel:
                    return store.resolve() / pack_rel.split("/")[0]
            return _approved_root(path, broker).resolve()

        notes = []
        report = build_pack(
            folder, sources, closure, from_pack_dirs=from_dirs, layout_root=layout, trusted=trusted,
            allow_unresolved=bool(params.get("allow_unresolved", False)), dry_run=bool(params.get("dry_run", False)),
            force=bool(params.get("force", False)), notes=notes, urdf_root=urdf_root,
        )
        return dict(status="ok", **context, backend=closure.name, notes=notes, **report)

    raise PackError(f"not a pack tool: {canonical}", "UNKNOWN")

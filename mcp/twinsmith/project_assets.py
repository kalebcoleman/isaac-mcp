"""Trusted local/shared-mount project broker. No USD parsing and no approval tools.

Approval is deployment configuration, read once per server. Descriptor-relative,
no-follow reads protect path traversal/races. Snapshots preserve all project files.
Same-user chmod/hash checks are NOT a sandbox or protection against that OS user.
"""

from __future__ import annotations
import hashlib
import fcntl
import itertools
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys
import tempfile
import time
import uuid
from .scene_contracts import DISABLED

# A Python that has twinsmith, spelled so it works in the user's shell whichever process (the MCP server or Kit)
# prints it: install.sh puts the host in mcp/.venv and removes any uv tool, so "$(uv tool dir)" names nothing. Kit
# and a checkout install import twinsmith from <repo>/mcp, a uv project; an installed wheel has no project beside it,
# and there the server's own interpreter is the Python that has twinsmith.
def helper_python(project: Path, interpreter: str) -> str:
    if (project / "pyproject.toml").is_file():
        return f"uv run --locked --isolated --project {shlex.quote(str(project))} --python 3.12 python"
    return shlex.quote(interpreter) if interpreter else "python3"


HELPER_PYTHON = helper_python(Path(__file__).resolve().parents[1], sys.executable)

MAX_FILE = 64 * 1024**2
MAX_BYTES = 256 * 1024**2
MAX_FILES = 1000
MAX_ENTRIES = 10000
MAX_DEPTH = 8
MAX_SNAPSHOTS = 256  # an import uses two, the folder and its converted copy; an identical folder is reused
MIN_FREE_BYTES = 2 * 1024**3  # room a new snapshot must leave on the disk that holds staging
USD = {".usd", ".usda", ".usdc", ".usdz"}


class AssetError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def refuse(code, message, **details):
    raise AssetError(code, message, **details)


_FILE_SUFFIXES = {".urdf", ".xacro", ".usd", ".usda", ".usdc", ".usdz", ".dae", ".stl", ".obj", ".mtl", ".mdl", ".mjcf", ".xml",
                  ".sdf", ".glb", ".gltf", ".fbx", ".ply", ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff", ".exr", ".hdr",
                  ".json", ".yaml", ".yml", ".toml", ".ini", ".txt", ".md", ".csv"}
# The only words printed in the command that are not real values to quote: what the user must fill in or create.
_PLACEHOLDER_FOLDER = "<the folder>"
_PLACEHOLDER_CONFIG = "<a private approval.json, such as ~/.config/twinsmith/approval.json>"


def _names_a_file(path) -> bool:
    """True for a file. An existing folder is never a file, whatever its name (``robots v1.2``, ``repo.git``)."""
    candidate = Path(str(path))
    try:
        if candidate.is_file():
            return True
        if candidate.is_dir():
            return False
    except OSError:
        pass
    return candidate.suffix.lower() in _FILE_SUFFIXES


def _shell_word(value: str) -> str:
    """A path as one word for a shell. Only the two exact placeholders and a ~/ path without special characters stay readable;
    anything else, including a value that merely starts with ``<``, is quoted."""
    if value in (_PLACEHOLDER_FOLDER, _PLACEHOLDER_CONFIG):
        return value
    if value.startswith("~/") and shlex.quote(value[2:]) == value[2:]:
        return value
    return shlex.quote(value)


_MESH_FOLDERS = ("meshes", "mesh", "dae", "stl", "assets")


def _approval_folder(path: Path) -> Path:
    """The folder to approve for a file: the one holding it, or for a URDF the ROS package that holds it.

    A URDF names its meshes as package://<package>/... or beside the package, and the importer finds them only inside
    the approved folder, so approving ``<package>/urdf`` would fail on the first mesh. The package is the nearest folder
    above the URDF that has a package.xml (never the home folder or above). Without one, a ``urdf`` folder that sits
    beside a meshes folder is widened to its parent.
    """
    folder = path.parent
    if path.suffix.lower() not in (".urdf", ".xacro"):
        return folder
    home, probe = Path.home(), folder
    for _ in range(8):
        if probe == probe.parent or probe == home:  # never the home folder, the filesystem root, or above
            break
        try:
            if (probe / "package.xml").is_file():
                return probe
        except OSError:
            break
        probe = probe.parent
    try:
        if folder.name.lower() == "urdf" and any((folder.parent / name).is_dir() for name in _MESH_FOLDERS):
            return folder.parent
    except OSError:
        pass
    return folder


_SYSTEM_FOLDERS = {"/", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/var", "/boot", "/sys", "/proc", "/dev", "/root", "/run",
                   "/opt", "/srv", "/mnt", "/media", "/home", "/tmp", "/snap"}
_PRIVATE_HOME_FOLDERS = {".ssh", ".gnupg", ".aws", ".kube", ".config", ".docker", ".password-store", ".mozilla"}


def _unsafe_to_approve(folder: Path) -> bool:
    """A system folder, the home folder itself, or a folder that holds keys and settings: never one to offer for approval.

    Approval hands a whole folder to the importer, and the folder in a refusal can come from an agent. Offering a command
    for ``/etc`` or a folder of SSH keys would make it easy to approve files that were never meant for a robot.
    """
    try:
        resolved = Path(os.path.realpath(folder))
    except OSError:
        return False
    home = Path.home()
    if str(resolved) in _SYSTEM_FOLDERS or resolved == home or resolved == home.parent:
        return True
    try:
        first = resolved.relative_to(home).parts[:1]
    except ValueError:
        return any(str(resolved).startswith(prefix + "/") for prefix in ("/etc", "/usr", "/bin", "/sbin", "/boot", "/sys", "/proc", "/dev", "/root", "/var/lib"))
    return bool(first) and first[0] in _PRIVATE_HOME_FOLDERS


def approval_help(folder=None) -> str:
    """What the *user* does to approve a folder, as a filled-in command an agent can pass on.

    The agent cannot approve anything. This names the folder, the config file when one is already set, and the restart that
    is needed, so a refusal ends in one instruction instead of a pointer to a document.
    """
    config = os.environ.get("TWINSMITH_ASSET_CONFIG", "").strip() or _PLACEHOLDER_CONFIG
    if folder and _names_a_file(folder):  # a file was named: the folder to approve is the one holding it
        folder = _approval_folder(Path(str(folder)))
    if folder and _unsafe_to_approve(Path(str(folder))):
        return (f" {folder} is a system or private folder (or the home folder itself), so no approval command is offered for it: "
                "a robot's files do not live there. Copy the model into a project folder of its own, and approve that folder.")
    target = str(folder) if folder else _PLACEHOLDER_FOLDER
    parts = [x for x in str(folder).rstrip("/").split("/") if x] if folder else []
    name = parts[-1] if parts else "project"
    if name.lower() in {"assets", "downloads", "data", "models", "robots"} and len(parts) > 1:
        name = f"{parts[-2]}-{name}"  # "assets" alone names nothing: say whose
    # The approval file accepts only [A-Za-z0-9_-], 1 to 48 characters.
    root_id = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:48].strip("-") or "project"
    # The helper refuses to run without --trust-project, and needs --staging while it creates the config file.
    new_file = config == _PLACEHOLDER_CONFIG or not Path(config).expanduser().exists()
    staging = os.environ.get("TWINSMITH_STAGING", "").strip() or "~/.cache/twinsmith/staging"
    extra = f" --staging {_shell_word(staging)}" if new_file else ""
    return (f" To approve {_shell_word(target) if folder else target}, ask the user to run this in their own shell (an agent cannot):\n"
            f"    {HELPER_PYTHON} -m twinsmith.project_assets --config {_shell_word(config)} "
            f"--root-id {root_id} --root {_shell_word(target)}{extra} --trust-project\n"
            "Then reconnect the MCP server and restart Kit, because both read the approval file at startup. "
            "Approve the smallest folder that holds the files: every import copies the whole approved folder "
            "(at most 1000 files or 256 MiB), so a large parent folder such as a workspace's projects folder fails with LIMIT.")


def relative(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 240
        or any(ord(c) < 32 for c in value)
    ):
        refuse(
            "INVALID_PATH",
            "Expected a nonempty relative path <=240 characters without controls.",
        )
    if (
        value.startswith("/")
        or "\\" in value
        or ":" in value
        or any(x in ("", ".", "..") for x in value.split("/"))
    ):
        refuse(
            "INVALID_PATH",
            "Absolute paths, URLs, traversal and empty components are forbidden. Pass a path relative to the approved root; assets__scan lists them.",
        )
    if len(value.split("/")) > MAX_DEPTH:
        refuse("LIMIT", "Path depth exceeds eight.")
    return value


def units_mismatch_message(asset_up, asset_mpu, stage_up, stage_mpu):
    """Say what differs and which stage settings to change; nothing is converted."""
    return (f"Asset and target up-axis/units must match; no automatic conversion. Asset: upAxis={asset_up}, "
            f"metersPerUnit={asset_mpu}. Stage: upAxis={stage_up}, metersPerUnit={stage_mpu}. For a new scene that "
            f"matches, call sim__quick_start with up_axis=\"{asset_up}\" and meters_per_unit={asset_mpu} (unsaved "
            "edits need discard_changes=true). Or sim__stage__open a file authored that way.")


def usd_path(value):
    relative(value)
    if Path(value).suffix.lower() not in USD:
        refuse("UNSUPPORTED_FORMAT", "Expected .usd, .usda, .usdc or validated .usdz.")
    return value


def signature(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def open_dir(path):
    """No symlink in *any* absolute component, including configured roots."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        refuse("CONFIG", "Configured paths must be absolute with no traversal.")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except BaseException:
        os.close(fd)
        raise


def walk(fd, prefix="", depth=0, budget=None, skipped=None):
    """Every regular file below *fd*, bounded, never following a symlink.

    A folder deeper than :data:`MAX_DEPTH` is not searched. When *skipped* is a list it is recorded
    there and the rest of the tree is still walked; without one, the walk refuses (a snapshot must
    never copy a folder silently short of its files).
    """
    budget = budget if budget is not None else [0, time.monotonic() + 15]
    # scandir iterator, never materialize an unbounded directory before counting.
    with os.scandir(fd) as entries:
        for entry in entries:
            budget[0] += 1
            if budget[0] > MAX_ENTRIES or time.monotonic() > budget[1]:
                refuse(
                    "LIMIT",
                    "Project entry/time budget exhausted; narrow the approved folder.",
                )
            name = relative(prefix + entry.name)
            st = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                if depth >= MAX_DEPTH - 1:
                    if skipped is None:
                        refuse("LIMIT", f"Project directory depth exceeds {MAX_DEPTH}: {name}/. "
                                        "Approve a smaller folder, or use assets__pack_build on the file you need.")
                    skipped.append(name + "/")
                    continue
                sub = os.open(
                    entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                try:
                    if signature(os.fstat(sub))[:2] != signature(st)[:2]:
                        refuse(
                            "SOURCE_CHANGED", "Directory replaced during enumeration."
                        )
                    yield from walk(sub, name + "/", depth + 1, budget, skipped)
                finally:
                    os.close(sub)
            elif stat.S_ISREG(st.st_mode):
                yield fd, entry.name, name, st
            else:
                refuse(
                    "UNSAFE_ENTRY",
                    "Project contains a symlink or special file: " + name,
                )


def read_regular(fd, name, before):
    src = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        current = os.fstat(src)
        if not stat.S_ISREG(current.st_mode) or signature(current) != signature(before):
            refuse("SOURCE_CHANGED", "File identity changed before read.")
        if current.st_size > MAX_FILE:
            refuse("LIMIT", "File exceeds 64 MiB.")
        data = bytearray()
        while len(data) <= MAX_FILE:
            block = os.read(src, min(1024**2, MAX_FILE + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        if len(data) != current.st_size or signature(os.fstat(src)) != signature(
            current
        ):
            refuse("SOURCE_CHANGED", "File changed while copying.")
        # Also catch replacement of the directory entry while its old fd stayed valid.
        if signature(os.stat(name, dir_fd=fd, follow_symlinks=False)) != signature(
            current
        ):
            refuse("SOURCE_CHANGED", "Source path replaced while copying.")
        return bytes(data)
    finally:
        os.close(src)


class Broker:
    def __init__(self, config=None):
        self.roots = {}
        self.missing_roots = {}
        self._pack_roots = {}
        self.staging = None
        self.default_staging = False
        self.config_error = None
        self.requested_profile = "trusted_project"
        self.config_path = (
            config if config is not None else os.getenv("TWINSMITH_ASSET_CONFIG")
        )
        if not self.config_path:
            self._default_staging()
            return
        try:
            p = Path(self.config_path)
            if p.stat().st_size > 16384:
                refuse("CONFIG", "Config exceeds 16 KiB.")
            doc = json.loads(p.read_text())
            self.requested_profile = doc.get("profile")
            if doc.get("version") != 1 or doc.get("profile") != "trusted_project":
                refuse("PROFILE_UNAVAILABLE", DISABLED["isolated_untrusted"])
            if not isinstance(doc.get("roots"), dict) or len(doc["roots"]) > 16:
                refuse(
                    "CONFIG", "roots must be a mapping of at most 16 approved folders."
                )
            self.staging = Path(doc["staging"])
            sfd = open_dir(self.staging)
            try:
                st = os.fstat(sfd)
                if st.st_uid != os.getuid() or st.st_mode & 0o077:
                    refuse(
                        "CONFIG", "Staging must be owned by this OS user and mode 0700."
                    )
            finally:
                os.close(sfd)
            for key, value in doc["roots"].items():
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", key) or Path(value) == Path(
                    "/"
                ):
                    refuse("CONFIG", "Invalid root ID or filesystem root.")
                root = Path(value)
                # Only a folder that is simply absent is skipped: it grants nothing while gone, and
                # is not trusted if it comes back before a restart. A symlink, a non-folder or a
                # permission error still refuses everything, because they may mean a swapped root.
                try:
                    fd = open_dir(root)
                except FileNotFoundError:
                    if any(self._overlaps(root, other) for other in self._all_roots(doc, key)):
                        refuse("CONFIG", "Approved roots must not overlap.")
                    self.missing_roots[key] = str(root)
                    continue
                try:
                    ident = signature(os.fstat(fd))[:2]
                finally:
                    os.close(fd)
                if (
                    root == self.staging
                    or root in self.staging.parents
                    or self.staging in root.parents
                ):
                    refuse("CONFIG", "Staging and project roots must be disjoint.")
                # Each file must belong to exactly one root: snapshots copy a whole root, and a
                # nested root would make the same file subject to two different scopes.
                if any(self._overlaps(root, other) for other in self._all_roots(doc, key)):
                    refuse("CONFIG", "Approved roots must not overlap.")
                self.roots[key] = (root, ident)
        except (ValueError, KeyError, TypeError, OSError) as e:
            self.config_error = str(e)
            self.roots.clear()
            self.missing_roots.clear()
            self.staging = None

    def _default_staging(self):
        """With no approval file, use one private folder so a URDF from the user's own asset folder loads with no setup.

        The host and Kit compute the same path from the user's home, or from TWINSMITH_STAGING. It approves no roots, so it
        grants nothing a pack in the user's store didn't already have; it only holds the converted copy. Left off if the
        folder can't be made private and owned by this user, as with no setting before.
        """
        folder = Path(os.getenv("TWINSMITH_STAGING") or Path.home() / ".cache" / "twinsmith" / "staging")
        try:
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = open_dir(folder)
            try:
                info = os.fstat(fd)
                if info.st_uid != os.getuid():
                    return
                if info.st_mode & 0o077:
                    os.fchmod(fd, 0o700)
            finally:
                os.close(fd)
        except OSError:
            return
        self.staging = folder
        self.default_staging = True

    @staticmethod
    def _overlaps(root, other):
        return root == other or root in other.parents or other in root.parents

    @staticmethod
    def _all_roots(doc, key):
        return [Path(value) for name, value in doc["roots"].items() if name != key]

    def listing(self):
        return dict(
            status="ok",
            profile=self.requested_profile,
            active_profile=None if self.config_error else "trusted_project",
            sandboxed=False,
            roots=[
                dict(root_id=k, path=str(v[0])) for k, v in sorted(self.roots.items())
            ],
            missing_roots=[dict(root_id=k, path=v) for k, v in sorted(self.missing_roots.items())],
            config_error=self.config_error,
            disabled=DISABLED,
            store_packs="Packs in the user's asset stores can be imported by absolute path once staging is configured; a project file's asset_path is not approval.",
            setup="User: configure TWINSMITH_ASSET_CONFIG for host AND Kit at launch. See docs/TOOLS-SCENE.md. No tool can approve folders.",
        )

    def require_profile(self):
        if self.config_error:
            refuse(
                "PROFILE_UNAVAILABLE",
                "Configuration refused; no fallback to trusted execution: "
                + self.config_error,
            )

    def locate(self, source_path, kind="usd"):
        """Map an absolute local path to an already-approved root, without widening it."""
        self.require_profile()
        if not isinstance(source_path, str) or not source_path.startswith("/"):
            refuse(
                "INVALID_PATH",
                "Provide an absolute local path under an approved root or in a pack of your asset folder.",
            )
        if (
            "\\" in source_path
            or ":" in source_path
            or any(ord(c) < 32 for c in source_path)
        ):
            refuse(
                "INVALID_PATH",
                "URLs, backslashes and control characters are not supported.",
            )
        if any(part in ("", ".", "..") for part in source_path.split("/")[1:]):
            refuse(
                "INVALID_PATH",
                "Empty, dot and traversal path components are forbidden.",
            )
        source = Path(source_path)
        for root_id, (root, _) in self.roots.items():
            try:
                rel = source.relative_to(root).as_posix()
            except ValueError:
                continue
            self._check_kind(rel, kind)
            return root_id, rel
        found = self._store_pack(source, kind)
        if found is not None:
            return found
        refuse(
            "ROOT_NOT_APPROVED",
            "The path is outside all user-approved project roots and the asset root." + approval_help(source if isinstance(source, (str, os.PathLike)) else None),
        )

    def locate_folder(self, source_path):
        """``(root_id, relative folder)`` for an absolute folder inside an approved root, or None when it is not a folder there."""
        self.require_profile()
        if not isinstance(source_path, str) or not source_path.startswith("/") or "\\" in source_path or ":" in source_path \
                or any(ord(c) < 32 for c in source_path) or any(part in ("", ".", "..") for part in source_path.split("/")[1:]):
            return None
        source = Path(source_path)
        for root_id, (root, _) in self.roots.items():
            try:
                rel = source.relative_to(root).as_posix()
            except ValueError:
                continue
            rel = "" if rel == "." else rel
            if rel and not os.path.isdir(source):
                return None
            return root_id, rel
        return None

    def folder_files(self, root_id, folder=""):
        """Every regular file below *folder* of an approved root, as paths relative to the root (bounded, no symlinks followed)."""
        fd = self.root_fd(root_id)
        try:
            for part in [p for p in folder.split("/") if p]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            prefix = folder + "/" if folder else ""
            skipped: list[str] = []
            names = []
            for _, _, name, _ in walk(fd, skipped=skipped):
                if len(names) >= MAX_ENTRIES:
                    refuse("LIMIT", "Too many files in that folder; approve and name a smaller one.")
                names.append(prefix + name)
            return names
        finally:
            os.close(fd)

    def read_file(self, root_id, path):
        """One file of an approved root, read safely (never following a symlink)."""
        fd = self.root_fd(root_id)
        try:
            parts = [p for p in path.split("/") if p]
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            st = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            return read_regular(fd, parts[-1], st)
        finally:
            os.close(fd)

    @staticmethod
    def _check_kind(rel, kind):
        if kind == "urdf":
            from . import urdf_refs

            urdf_refs.check_urdf_path(rel)
        elif kind == "mjcf":
            from . import mjcf_refs

            mjcf_refs.check_mjcf_path(rel)
        elif kind in ("xacro", "sdf", "map"):
            from . import asset_formats

            asset_formats.check_path(rel, kind)
        elif kind == "mesh":
            from . import mesh_refs

            mesh_refs.check_mesh_path(rel)
        elif Path(rel).suffix.lower() in (".urdf", ".xacro"):
            refuse("UNSUPPORTED_FORMAT", f"{rel} is a URDF, not a USD: assets__import_path reads it with source_path (format auto).")
        elif Path(rel).suffix.lower() == ".xml":
            refuse("UNSUPPORTED_FORMAT", f"{rel} is not a USD. If it is a MuJoCo model, assets__import_path reads it with source_path (format auto).")
        else:
            usd_path(rel)

    def _store_pack(self, source, kind="usd"):
        """Map a path in a pack version under the asset root to a root; the user put it there.

        Only the asset root: a project file cannot name another folder. A pack that does not verify
        against its ``pack.yaml`` (a changed, missing or unlisted file, or a symlink) is refused.
        """
        from . import pack_manifest

        try:
            root = pack_manifest.resolve_asset_root().path
        except pack_manifest.RootError:
            return None
        try:
            pack_id, version, *rest = source.relative_to(root).parts
        except ValueError:
            return None
        if not rest:
            return None
        try:
            pack_manifest.identifier(pack_id), pack_manifest.identifier(version)
        except pack_manifest.ManifestError:
            return None
        folder = root / pack_id / version
        rel = "/".join(rest)
        self._check_kind(rel, kind)
        report = pack_manifest.inspect_pack(root, pack_id, version)
        if not report["verified"]:
            refuse("PACK_CORRUPT", f"Pack {pack_id}/{version} does not verify against its pack.yaml "
                   f"({'; '.join(report['problems'][:5])}). Run assets__pack_doctor; write a corrected new "
                   "version with assets__pack_adopt or assets__pack_build.")
        root_id = f"pack:{folder}"
        fd = open_dir(folder)
        try:
            self._pack_roots.setdefault(root_id, (folder, signature(os.fstat(fd))[:2]))
        finally:
            os.close(fd)
        return root_id, rel

    def root_fd(self, root_id):
        known = self.roots.get(root_id) or self._pack_roots.get(root_id)
        if known is None:
            refuse(
                "ROOT_NOT_APPROVED",
                "Root is not approved in this server configuration. Call assets__roots to see the approved folders." + approval_help(),
            )
        path, identity = known
        fd = open_dir(path)
        if signature(os.fstat(fd))[:2] != identity:
            os.close(fd)
            refuse(
                "STALE_ROOT",
                "Approved root was replaced; user must reconfigure/restart.",
            )
        return fd

    def scan(self, root_id, offset=0, limit=50):
        if (
            type(offset) is not int
            or not 0 <= offset <= MAX_FILES
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            refuse("LIMIT", "offset 0..1000 and limit 1..100 required.")
        fd = self.root_fd(root_id)
        found = []
        skipped: list[str] = []
        try:
            for _, _, name, st in walk(fd, skipped=skipped):
                if Path(name).suffix.lower() in USD | {".usdz", ".urdf"}:
                    if len(found) >= MAX_FILES:
                        refuse(
                            "LIMIT",
                            "More than 1000 candidate files; narrow the project root.",
                        )
                    found.append(
                        dict(
                            relative_path=name,
                            bytes=st.st_size,
                            inspected=False,
                            supported=Path(name).suffix.lower() in USD | {".urdf"},
                        )
                    )
        finally:
            os.close(fd)
        found.sort(key=lambda x: x["relative_path"])
        return dict(
            status="ok",
            root_id=root_id,
            files=found[offset : offset + limit],
            total=len(found),
            next_offset=offset + limit if offset + limit < len(found) else None,
            truncated=False,
            depth_limit=MAX_DEPTH,
            skipped_deep=sorted(skipped),
            **({"note": f"Folders deeper than {MAX_DEPTH} levels are not searched; "
                        "they are listed in skipped_deep."} if skipped else {}),
        )

    def search(self, query="", limit=50):
        if (
            not isinstance(query, str)
            or len(query) > 128
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            refuse("LIMIT", "query <=128 characters; limit 1..100.")
        found = []
        skipped: list[dict] = []
        for root_id in sorted(self.roots):
            offset = 0
            while True:
                page = self.scan(root_id, offset, 100)
                for name in page.get("skipped_deep", []):
                    item = dict(root_id=root_id, path=name)
                    if item not in skipped:
                        skipped.append(item)
                for item in page["files"]:
                    if query.casefold() in item["relative_path"].casefold():
                        if len(found) == limit:
                            return dict(
                                status="ok", files=found, truncated=True, network=False,
                                depth_limit=MAX_DEPTH, skipped_deep=skipped,
                            )
                        found.append(dict(root_id=root_id, **item))
                if page["next_offset"] is None:
                    break
                offset = page["next_offset"]
        return dict(
            status="ok",
            files=found,
            truncated=False,
            network=False,
            depth_limit=MAX_DEPTH,
            skipped_deep=skipped,
            refresh="Fresh scan on every call; no fixed robot count or kind claim.",
            **({} if found else {"hint": "No file in your approved folders matches. This searches your own files only: a "
                                         "well-known robot comes by name from sim__quick_start (robot=franka, ur5e, ...)."}),
        )

    def _quota_message(self):
        return (f"The staging folder {self.staging} holds its limit of {MAX_SNAPSHOTS} private copies of imported folders, so this one "
                "was not imported. Importing a folder that has not changed still works. To free them, ask the user to stop every "
                "simulator using them (a scene open on one breaks if its copy goes), then delete what is inside that folder.")

    def snapshot(self, root_id, path, kind="usd", generate=None):
        """Copy an approved folder to staging. *generate* (xacro and SDF) makes the entry URDF from the copied files:
        ``generate(path, files, read) -> {"entry": rel, "data": bytes, "info": {...}}``; the generated file is hashed into
        the manifest like any other, and it becomes the entry."""
        if kind in ("xacro", "sdf", "map"):
            from . import asset_formats

            relative(path)
            asset_formats.check_path(path, kind)
        elif kind == "urdf":
            from . import urdf_refs

            relative(path)
            urdf_refs.check_urdf_path(path)
        elif kind == "mjcf":
            from . import mjcf_refs

            relative(path)
            mjcf_refs.check_mjcf_path(path)
        elif kind == "mesh":
            from . import mesh_refs

            relative(path)
            mesh_refs.check_mesh_path(path)
        else:
            usd_path(path)
        fd = self.root_fd(root_id)
        work = None
        lock = None
        try:
            lock = open_dir(self.staging)
            fcntl.flock(lock, fcntl.LOCK_EX)
            # Directory lock coordinates quotas across host processes sharing this
            # private staging directory; enumeration itself is bounded. One bounded
            # transient copy permits content comparison even at the published quota.
            with os.scandir(lock) as entries:
                existing = [
                    entry.name for entry in itertools.islice(entries, MAX_SNAPSHOTS + 1)
                ]
            if len(existing) > MAX_SNAPSHOTS:
                refuse("QUOTA", self._quota_message())
            free = shutil.disk_usage(self.staging).free
            if free < MIN_FREE_BYTES + MAX_BYTES:
                refuse(
                    "QUOTA",
                    f"The disk holding {self.staging} has {free // 1024**2} MiB free, too little for another snapshot. "
                    "Stop the simulators using them, then clear that folder.",
                )
            work = Path(tempfile.mkdtemp(prefix=".pending-", dir=self.staging))
            files, total, packages = {}, 0, {}
            for parent, name, rel, st in walk(fd):
                if len(files) >= MAX_FILES or total + st.st_size > MAX_BYTES:
                    refuse("LIMIT", "Snapshot exceeds 1000 files or 256 MiB. Copy only what the USD or URDF needs with assets__pack_build, then import from that pack.")
                data = read_regular(parent, name, st)
                if Path(rel).suffix.lower() == ".usdz":
                    from .usdz import validate

                    packages[rel] = validate(data)
                elif data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
                    refuse(
                        "UNSUPPORTED_ARCHIVE",
                        "Only validated .usdz archives are supported in project snapshots.",
                    )
                total += len(data)
                target = work / "project" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o400)
                files[rel] = hashlib.sha256(data).hexdigest()
            if path not in files:
                refuse("NOT_FOUND", f"{path} is not in the approved folder. Check the name with assets__scan.")
            generated = None
            if generate is not None:
                made = generate(path, files, lambda rel: (work / "project" / rel).read_bytes())
                if len(files) >= MAX_FILES:
                    refuse("LIMIT", f"The folder holds {len(files)} files, and the converted file would make it more than the limit of {MAX_FILES}. Approve a smaller folder.")
                if made["entry"] in files:
                    refuse("INVALID_PATH", f"{made['entry']} is already in the folder; the converted file needs a name of its own.")
                data = made["data"]
                target = work / "project" / made["entry"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o400)
                files[made["entry"]] = hashlib.sha256(data).hexdigest()
                total += len(data)
                generated = dict(made["info"], source=path)
                path = made["entry"]
            manifest = dict(
                version=1,
                root_id=root_id,
                entry=path,
                files=files,
                bytes=total,
                packages=packages,
            )
            if generated is not None:
                manifest["generated"] = generated
            if kind in ("urdf", "xacro", "sdf"):
                # A package may be found by the name of this folder, which a snapshot does not keep.
                known = self.roots.get(root_id) or self._pack_roots.get(root_id)
                manifest["root_name"] = Path(known[0]).name
            raw = json.dumps(manifest, sort_keys=True).encode()
            digest = hashlib.sha256(raw).hexdigest()
            # Repeated inspection/import must not retain another identical project.
            # Never reuse on manifest alone: re-read and verify every file first.
            # Existing snapshots are never edited or removed while Kit may use them.
            for token in sorted(existing):
                if not re.fullmatch("[0-9a-f]{32}", token):
                    continue
                previous = open_dir(self.staging / token)
                try:
                    st = os.stat(
                        "manifest.json", dir_fd=previous, follow_symlinks=False
                    )
                    if st.st_size != len(raw):
                        continue
                    old = read_regular(previous, "manifest.json", st)
                finally:
                    os.close(previous)
                if old == raw:
                    self.verify(token, digest, kind=kind)
                    return dict(snapshot_id=token, manifest_sha256=digest, bytes=total)
            if len(existing) >= MAX_SNAPSHOTS:
                refuse("QUOTA", self._quota_message())
            (work / "manifest.json").write_bytes(raw)
            (work / "manifest.json").chmod(0o400)
            token = uuid.uuid4().hex
            destination = self.staging / token
            work.rename(destination)
            work = None
            return dict(
                snapshot_id=token,
                manifest_sha256=digest,
                bytes=total,
            )
        finally:
            os.close(fd)
            if work is not None:
                shutil.rmtree(work)
            if lock is not None:
                os.close(lock)

    def verify(self, token, digest, kind="usd"):
        if not self.staging or not re.fullmatch("[0-9a-f]{32}", token or ""):
            refuse("SNAPSHOT", "No approved staging or invalid snapshot ID.")
        fd = open_dir(self.staging / token)
        try:
            st = os.stat("manifest.json", dir_fd=fd, follow_symlinks=False)
            data = read_regular(fd, "manifest.json", st)
            if hashlib.sha256(data).hexdigest() != digest:
                refuse("SNAPSHOT_CHANGED", "Snapshot manifest hash mismatch.")
            manifest = json.loads(data)
            project_fd = os.open(
                "project", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            try:
                seen, packages = {}, {}
                for parent, name, rel, st in walk(project_fd):
                    if len(seen) >= MAX_FILES:
                        refuse("LIMIT", "Snapshot file budget exceeded.")
                    content = read_regular(parent, name, st)
                    seen[rel] = hashlib.sha256(content).hexdigest()
                    if Path(rel).suffix.lower() == ".usdz":
                        from .usdz import validate

                        packages[rel] = validate(content)
                if seen != manifest["files"]:
                    refuse("SNAPSHOT_CHANGED", "Snapshot content changed.")
                if packages != manifest.get("packages", {}):
                    refuse("SNAPSHOT_CHANGED", "Package member manifest changed.")
            finally:
                os.close(project_fd)
        finally:
            os.close(fd)
        if kind == "usd":
            usd_path(manifest["entry"])
        else:
            relative(manifest["entry"])
        return self.staging / token / "project" / manifest["entry"], manifest


def main(argv=None):
    """Human launch-configuration helper, intentionally not an MCP tool."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Approve a trusted project for the local OS user. NOT a sandbox. Set TWINSMITH_ASSET_CONFIG for both MCP host and Kit after this command."
    )
    parser.add_argument("--config", type=Path, required=True)
    class Once(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            if getattr(namespace, self.dest, None) is not None:
                parser.error(f"{option_string} can be given once. Run this again to add another root.")
            setattr(namespace, self.dest, values)

    parser.add_argument("--root-id", action=Once, help="name for --root; give both, or neither for staging only")
    parser.add_argument("--root", action=Once, type=Path)
    parser.add_argument("--staging", type=Path, help="required when creating the file")
    parser.add_argument(
        "--trust-project",
        action="store_true",
        help="Explicitly trust native USD parsing, dependencies and enabled Kit code with this OS account permissions.",
    )
    args = parser.parse_args(argv)
    if not args.trust_project:
        parser.error("--trust-project required; isolated untrusted mode is unavailable")
    if (args.root is None) != (args.root_id is None):
        parser.error("--root and --root-id go together")
    if args.config.exists():
        # The user runs this helper, not a model, and it only ever adds: an existing root or the
        # staging folder cannot be changed from here.
        doc = json.loads(args.config.read_text())
        if not isinstance(doc, dict) or not isinstance(doc.get("roots"), dict):
            parser.error(f"{args.config} is not an approval file")
        if args.staging is not None and str(args.staging.absolute()) != doc.get("staging"):
            parser.error(f"{args.config} already uses staging {doc.get('staging')}; edit the file to change it")
        if args.root_id in doc["roots"] and doc["roots"][args.root_id] != str(args.root.absolute()):
            parser.error(f"root id {args.root_id} already names {doc['roots'][args.root_id]}; choose another id")
    else:
        if args.staging is None:
            parser.error("--staging is required when creating the approval file")
        args.staging.mkdir(mode=0o700, parents=True, exist_ok=True)
        doc = dict(version=1, profile="trusted_project", roots={}, staging=str(args.staging.absolute()))
    if args.root is not None:
        doc["roots"][args.root_id] = str(args.root.absolute())
    args.config.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.config.with_name(f".{args.config.name}.{uuid.uuid4().hex}.part")
    tmp.write_text(json.dumps(doc, indent=2))
    # Check the result before it replaces anything, so a bad addition leaves the old file working.
    broker = Broker(str(tmp))
    if broker.config_error:
        tmp.unlink()
        parser.error(broker.config_error)
    tmp.replace(args.config)
    print("Approved trusted project configuration: " + str(args.config.absolute()))
    for root_id, path in sorted(broker.missing_roots.items()):
        print(f"Note: root {root_id} does not exist right now: {path}")
    print(
        "Set TWINSMITH_ASSET_CONFIG to this path for host and Kit, then restart them."
    )


if __name__ == "__main__":
    main()

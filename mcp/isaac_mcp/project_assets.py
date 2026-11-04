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
import shutil
import stat
import tempfile
import time
import uuid
from .scene_contracts import DISABLED

MAX_FILE = 64 * 1024**2
MAX_BYTES = 256 * 1024**2
MAX_FILES = 1000
MAX_ENTRIES = 10000
MAX_DEPTH = 8
MAX_SNAPSHOTS = 16
USD = {".usd", ".usda", ".usdc", ".usdz"}


class AssetError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def refuse(code, message, **details):
    raise AssetError(code, message, **details)


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


def walk(fd, prefix="", depth=0, budget=None):
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
                    refuse("LIMIT", "Project directory depth exceeds eight.")
                sub = os.open(
                    entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                try:
                    if signature(os.fstat(sub))[:2] != signature(st)[:2]:
                        refuse(
                            "SOURCE_CHANGED", "Directory replaced during enumeration."
                        )
                    yield from walk(sub, name + "/", depth + 1, budget)
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
        self.config_error = None
        self.requested_profile = "trusted_project"
        self.config_path = (
            config if config is not None else os.getenv("ISAAC_MCP_ASSET_CONFIG")
        )
        if not self.config_path:
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
            setup="User: configure ISAAC_MCP_ASSET_CONFIG for host AND Kit at launch. See docs/TOOLS-SCENE.md. No tool can approve folders.",
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
            "The path is outside all user-approved project roots and user asset stores. Call assets__roots for setup.",
        )

    @staticmethod
    def _check_kind(rel, kind):
        if kind == "urdf":
            from . import urdf_refs

            urdf_refs.check_urdf_path(rel)
        elif Path(rel).suffix.lower() in (".urdf", ".xacro"):
            refuse("UNSUPPORTED_FORMAT", f"{rel} is a URDF, not a USD: use assets__import_urdf with source_path.")
        else:
            usd_path(rel)

    def _store_pack(self, source, kind="usd"):
        """Map a path in a pack of a user store to a root; the user put it there.

        Only ``user_stores``: a project file's ``asset_path`` is found from the working directory,
        so a cloned repository could name any folder. A pack whose files fail their pins is refused.
        """
        from . import asset_packs, assets

        for store in assets.Resolver(assets.DEFAULT_PACK).user_stores():
            try:
                pack, *rest = source.relative_to(store).parts
            except ValueError:
                continue
            if not rest:
                continue
            try:
                assets.validate_pack_name(pack)
            except assets.InvalidPackName:
                continue
            folder = store / pack
            rel = "/".join(rest)
            self._check_kind(rel, kind)
            changes = asset_packs.pin_changes(folder)
            bad = [item["file"] for item in changes["changed"]] + changes["removed"]
            if bad:
                refuse("PACK_CORRUPT", f"Pack {pack} has files that differ from their pins: {bad[:10]}. "
                       "Run assets__pack_doctor, then restore them or re-pin with assets__pack_pin.")
            root_id = f"pack:{folder}"
            fd = open_dir(folder)
            try:
                self._pack_roots.setdefault(root_id, (folder, signature(os.fstat(fd))[:2]))
            finally:
                os.close(fd)
            return root_id, rel
        return None

    def root_fd(self, root_id):
        known = self.roots.get(root_id) or self._pack_roots.get(root_id)
        if known is None:
            refuse(
                "ROOT_NOT_APPROVED",
                "Root is not approved in this server configuration. Call assets__roots for setup.",
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
        try:
            for _, _, name, st in walk(fd):
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
        for root_id in sorted(self.roots):
            offset = 0
            while True:
                page = self.scan(root_id, offset, 100)
                for item in page["files"]:
                    if query.casefold() in item["relative_path"].casefold():
                        if len(found) == limit:
                            return dict(
                                status="ok", files=found, truncated=True, network=False
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
            refresh="Fresh scan on every call; no fixed robot count or kind claim.",
        )

    def snapshot(self, root_id, path, kind="usd"):
        if kind == "urdf":
            from . import urdf_refs

            relative(path)
            urdf_refs.check_urdf_path(path)
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
                refuse(
                    "QUOTA",
                    "Snapshot quota exceeded. Stop consumers before user cleanup of staging.",
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
                refuse(
                    "NOT_FOUND", "Selected file is absent from the project snapshot. Check the name with assets__scan."
                )
            manifest = dict(
                version=1,
                root_id=root_id,
                entry=path,
                files=files,
                bytes=total,
                packages=packages,
            )
            if kind == "urdf":
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
                refuse(
                    "QUOTA",
                    f"{MAX_SNAPSHOTS} distinct snapshot quota reached. Stop consumers before user cleanup of staging.",
                )
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
        description="Approve a trusted project for the local OS user. NOT a sandbox. Set ISAAC_MCP_ASSET_CONFIG for both MCP host and Kit after this command."
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
        "Set ISAAC_MCP_ASSET_CONFIG to this path for host and Kit, then restart them."
    )


if __name__ == "__main__":
    main()

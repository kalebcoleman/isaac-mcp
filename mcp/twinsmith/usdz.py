"""Bounded USDZ container validation before any native USD parser is invoked.

No extraction. Supports stored, aligned, single-disk packages with one default
USD layer and ordinary USD/image/audio members. Nested packages, ZIP64,
encryption, compression and ambiguous names/layouts are deliberately refused.
This validates the container only; trusted native USD parsing is NOT a sandbox.
"""

import hashlib
import io
from pathlib import PurePosixPath
import stat
import struct
import zipfile

from .project_assets import MAX_FILE, MAX_FILES, refuse, relative

MEMBER_TYPES = {
    ".usd",
    ".usda",
    ".usdc",
    ".png",
    ".jpg",
    ".jpeg",
    ".exr",
    ".avif",
    ".m4a",
    ".mp3",
    ".wav",
}


def validate(data):
    if len(data) > MAX_FILE or len(data) < 22 or not data.startswith(b"PK\x03\x04"):
        refuse(
            "USDZ_CONTAINER",
            "USDZ must be a bounded ZIP package with no executable prefix.",
        )
    end = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if end < 0 or end + 22 > len(data):
        refuse("USDZ_CONTAINER", "Missing ZIP end record.")
    _, disk, central_disk, count_disk, count, size, offset, comment = (
        struct.unpack_from("<4s4H2LH", data, end)
    )
    if (
        disk
        or central_disk
        or count_disk != count
        or not 1 <= count <= MAX_FILES
        or size > 1024**2
        or offset + size != end
        or end + 22 + comment != len(data)
    ):
        refuse(
            "USDZ_CONTAINER",
            "Invalid/bounded central directory; multidisk, ZIP64 and trailing data are unsupported.",
        )
    members = {}
    spans = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) != count:
                refuse("USDZ_CONTAINER", "ZIP directory count mismatch.")
            for entry in infos:
                name = relative(entry.filename)
                if (
                    name != entry.orig_filename
                    or any(c in name for c in "[]%?#")
                    or name.casefold() in {p.casefold() for p in members}
                ):
                    refuse("USDZ_PATH", "Duplicate or ambiguous package member name.")
                if entry.is_dir() or stat.S_IFMT(entry.external_attr >> 16) not in (
                    0,
                    stat.S_IFREG,
                ):
                    refuse(
                        "USDZ_ENTRY",
                        "Only regular package members are supported; no symlinks or special files.",
                    )
                if (
                    entry.flag_bits & ~0x800
                    or entry.compress_type != zipfile.ZIP_STORED
                    or entry.compress_size != entry.file_size
                    or entry.file_size > MAX_FILE
                ):
                    refuse(
                        "USDZ_CONTAINER",
                        "USDZ must be uncompressed and unencrypted, without data descriptors.",
                    )
                if PurePosixPath(name).suffix.lower() not in MEMBER_TYPES:
                    refuse(
                        "USDZ_ENTRY",
                        "Unsupported package member type (nested archives are disabled): "
                        + name,
                    )
                start = entry.header_offset
                if start < 0 or start + 30 > offset:
                    refuse(
                        "USDZ_CONTAINER", "Member header is outside the data region."
                    )
                (
                    sig,
                    version,
                    flags,
                    compression,
                    clock,
                    date,
                    crc,
                    packed,
                    unpacked,
                    namesize,
                    extra,
                ) = struct.unpack_from("<4s5H3L2H", data, start)
                begin = start + 30 + namesize + extra
                finish = begin + entry.file_size
                encoding = "utf-8" if flags & 0x800 else "cp437"
                rawname = data[start + 30 : start + 30 + namesize].decode(encoding)
                if (
                    sig != b"PK\x03\x04"
                    or (flags, compression, crc, packed, unpacked)
                    != (
                        entry.flag_bits,
                        entry.compress_type,
                        entry.CRC,
                        entry.compress_size,
                        entry.file_size,
                    )
                    or rawname != name
                    or begin % 64
                    or finish > offset
                ):
                    refuse(
                        "USDZ_CONTAINER",
                        "Local/central header mismatch, unaligned data or out-of-range member.",
                    )
                body = archive.read(entry)  # bounded, stored bytes; checks CRC
                if body.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
                    refuse("USDZ_ENTRY", "Archive-bearing members are unsupported.")
                members[name] = {
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "bytes": len(body),
                }
                spans.append((start, finish, name))
    except (zipfile.BadZipFile, UnicodeError, struct.error, OSError) as e:
        refuse("USDZ_CONTAINER", "Invalid package: " + str(e)[:300])
    spans.sort()
    if spans[0][0] != 0 or any(
        left[1] > right[0] for left, right in zip(spans, spans[1:])
    ):
        refuse("USDZ_CONTAINER", "Overlapping members or prepended data.")
    default = spans[0][2]
    if PurePosixPath(default).suffix.lower() not in {".usd", ".usda", ".usdc"}:
        refuse(
            "USDZ_DEFAULT_LAYER", "First package member must be a USD default layer."
        )
    return {"default_layer": default, "members": members, "extracted": False}

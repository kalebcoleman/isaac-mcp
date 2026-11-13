"""Container attacks are rejected without extraction or loading a USD plugin."""

import io
import stat
import struct
import zipfile

import pytest

from twinsmith.project_assets import AssetError
from twinsmith.usdz import validate


def package(entries, *, compression=zipfile.ZIP_STORED, mode=stat.S_IFREG, align=True):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = compression
            info.external_attr = (mode | 0o600) << 16
            if align:
                pad = (-(output.tell() + 30 + len(name.encode()) + 4)) % 64
                info.extra = struct.pack("<HH", 0xFFFF, pad) + b"\0" * pad
            archive.writestr(info, data)
    return output.getvalue()


def test_multifile_package_members_hashed_without_extraction():
    result = validate(
        package(
            [
                ("model.usda", b"#usda 1.0\n"),
                ("geo/part.usdc", b"PXR-USDC"),
                ("textures/color.png", b"PNG"),
            ]
        )
    )
    assert result["default_layer"] == "model.usda" and not result["extracted"]
    assert result["members"]["geo/part.usdc"]["bytes"] == 8
    assert len(result["members"]["textures/color.png"]["sha256"]) == 64


@pytest.mark.parametrize(
    "name",
    [
        "../escape.usda",
        "/abs.usda",
        "x/../../bad.usda",
        "a\\bad.usda",
        "C:bad.usda",
        "https://host/a.usda",
        "a/./b.usda",
        "a//b.usda",
        "outer.usdz[evil.usda]",
        "percent%2e.usda",
        "\x00bad.usda",
    ],
)
def test_traversal_and_resolver_ambiguous_names(name):
    with pytest.raises(AssetError):
        validate(package([(name, b"USD")]))


@pytest.mark.parametrize(
    "entries,kwargs",
    [
        ([("a.usda", b"USD")], {"mode": stat.S_IFLNK}),
        ([("a.usda", b"USD")], {"mode": stat.S_IFIFO}),
        ([("a.usda", b"USD")], {"compression": zipfile.ZIP_DEFLATED}),
        ([("a.usda", b"USD")], {"align": False}),
        ([("a.usda", b"USD"), ("a.usda", b"other")], {}),
        ([("a.usda", b"USD"), ("A.usda", b"other")], {}),
        ([("a.usda", b"USD"), ("nested.usdz", b"PK\x03\x04")], {}),
        ([("a.usda", b"USD"), ("data.png", b"PK\x03\x04")], {}),
        ([("first.png", b"PNG"), ("a.usda", b"USD")], {}),
        ([("a.usda", b"USD"), ("resolver.so", b"ELF")], {}),
    ],
)
def test_archive_attack_forms(entries, kwargs):
    with pytest.raises(AssetError):
        validate(package(entries, **kwargs))


def test_crc_mismatch_overlap_and_forged_counts():
    original = package([("a.usda", b"USD"), ("b.usda", b"other")])
    variants = []
    data = bytearray(original)
    data[64] ^= 1
    variants.append(data)
    data = bytearray(original)
    end = data.rfind(b"PK\x05\x06")
    struct.pack_into("<H", data, end + 10, 65535)
    variants.append(data)
    data = bytearray(original)
    second = data.find(b"PK\x01\x02", data.find(b"PK\x01\x02") + 1)
    struct.pack_into("<L", data, second + 42, 0)
    variants.append(data)
    variants.extend([b"#!/bin/sh\n" + original, original + b"extra", original[:-1]])
    for data in variants:
        with pytest.raises(AssetError):
            validate(data)


def test_encrypted_local_flag_disagreement():
    data = bytearray(package([("a.usda", b"USD")]))
    struct.pack_into("<H", data, 6, 1)
    with pytest.raises(AssetError):
        validate(data)

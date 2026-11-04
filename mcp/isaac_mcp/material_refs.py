"""Read the file references inside MDL and MaterialX files. Standard library only.

USD's dependency computation stops at these files, so the textures they name need following by
hand. Kit's import validator (``sim_tools.core``) and the pack closure (``asset_packs``) share this
code, so a pack is only called self-contained when import would accept the same references.
Anything that cannot be read statically raises :class:`MaterialRefError`; callers refuse it (import)
or report it by name (packs). This is not a sandbox and parses no USD.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_DOCUMENTS = 1000
MAX_BYTES = 64 * 1024**2
MATERIAL_SUFFIXES = {".mdl", ".mtlx"}
_MTLX_NESTED_UNSUPPORTED = {".mdl", ".usd", ".usda", ".usdc", ".usdz", ".abc", ".xml"}
_MDL_MODULES = {"OmniPBR", "tex", "state", "base", "df", "math", "anno", "limits", "debug", "scene"}
_RESOURCE = r"(texture_(?:2d|3d|cube|ptex)|light_profile|bsdf_measurement)"


class MaterialRefError(ValueError):
    """A reference that cannot be validated statically."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def check_value(value: str, kind: str) -> str:
    """Refuse a reference that is not a plain relative path."""
    if (
        not value
        or value.startswith("/")
        or ":" in value
        or "\\" in value
        or any(c in value for c in "<>$#?{}")
    ):
        raise MaterialRefError(
            "DEPENDENCIES_UNSUPPORTED",
            kind + " contains an absolute, network or dynamic reference: " + value,
        )
    return value


def scan_mdl(text: str, name: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Return ``([(kind, literal path)], imported modules)`` of one MDL source."""
    # Comments do not describe texture resources. For safety reject escaped/expressive arguments
    # rather than guessing at MDL syntax.
    text = re.sub(
        r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*',
        lambda match: match.group() if match.group().startswith('"') else "",
        text,
        flags=re.S,
    )
    # We support only absolute MDL module names. A relative or otherwise nonstandard import could
    # hide another file with its own resources.
    without_using = re.sub(
        r"\busing\s+::[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*\s+import\s+[^;]+;", "", text
    )
    if re.search(r"\busing\s+|\bimport(?!\s*::)\s+|#\s*include\b", without_using):
        raise MaterialRefError(
            "DEPENDENCIES_UNSUPPORTED", "A relative or unsupported MDL import cannot be validated: " + name
        )
    modules = re.findall(r"\b(?:using|import)\s+::([A-Za-z_][A-Za-z_0-9]*)", text)
    if any(module not in _MDL_MODULES for module in modules):
        raise MaterialRefError("DEPENDENCIES_UNSUPPORTED", "A custom MDL module cannot be validated: " + name)
    starts = list(re.finditer(r"\b" + _RESOURCE + r"\s*\(", text))
    calls = list(re.finditer(r"\b" + _RESOURCE + r"\s*\(([^()]*)\)", text))
    if len(starts) != len(calls):
        raise MaterialRefError(
            "DEPENDENCIES_UNSUPPORTED", "A complex MDL resource expression cannot be validated: " + name
        )
    found = []
    for call in calls:
        kind = "MDL texture" if call.group(1).startswith("texture_") else "MDL resource"
        arg = call.group(2).strip()
        if not arg:
            continue
        match = re.match(r'^"([^"\\]+)"(?:\s*,.*)?$', arg, flags=re.S)
        if not match or "<" in arg:
            raise MaterialRefError(
                "DEPENDENCIES_UNSUPPORTED", "A non-literal, escaped or tiled MDL resource cannot be validated: " + name
            )
        found.append((kind, match.group(1)))
    return found, modules


def scan_materialx(data: bytes, name: str) -> list[str]:
    """Return the literal file references of one MaterialX document."""
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise MaterialRefError("DEPENDENCIES_UNSUPPORTED", "MaterialX XML declarations/entities are unsupported: " + name)
    try:
        xml = ET.fromstring(data)
    except ET.ParseError:
        raise MaterialRefError("DEPENDENCIES_UNSUPPORTED", "Invalid MaterialX document: " + name) from None
    values = []
    for element in xml.iter():
        if element.get("fileprefix"):
            raise MaterialRefError("DEPENDENCIES_UNSUPPORTED", "MaterialX fileprefix requires resolver support: " + name)
        if element.get("type") == "filename" and element.get("value"):
            values.append(element.get("value"))
        for key in ("file", "filename", "href"):
            if element.get(key):
                values.append(element.get(key))
    return values


def check_materialx_target(target: Path, name: str) -> None:
    """A MaterialX document may name textures and other MaterialX, but not other formats."""
    if target.suffix.lower() in _MTLX_NESTED_UNSUPPORTED:
        raise MaterialRefError(
            "DEPENDENCIES_UNSUPPORTED", "Nested material or scene dependency requires additional validation: " + name
        )


def follow(files) -> dict:
    """Follow references from MDL and MaterialX files, including nested MaterialX.

    Returns ``references``, each ``dict(target, holder, exists)``, and ``unfollowable``, each
    ``dict(file, reason)``. A target can lie anywhere: the caller decides what is in bounds.
    """
    references, unfollowable, seen = [], [], set()
    pending = [Path(f) for f in files if Path(f).suffix.lower() in MATERIAL_SUFFIXES]
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        if len(seen) > MAX_DOCUMENTS:
            unfollowable.append(dict(file=str(path), reason=f"More than {MAX_DOCUMENTS} material documents."))
            break
        try:
            if path.stat().st_size > MAX_BYTES:
                raise MaterialRefError("LIMIT", "Material file exceeds 64 MiB: " + path.name)
            if path.suffix.lower() == ".mdl":
                found = scan_mdl(path.read_text(encoding="utf-8"), path.name)[0]
            else:
                found = [("MaterialX dependency", v) for v in scan_materialx(path.read_bytes(), path.name)]
            for kind, value in found:
                check_value(value, kind)
        except (MaterialRefError, OSError, UnicodeDecodeError) as exc:
            unfollowable.append(dict(file=str(path), reason=str(exc)[:300]))
            continue
        for kind, value in found:
            target = Path(os.path.normpath(str(path.parent / value)))
            references.append(dict(target=target, holder=path, exists=target.is_file()))
            if path.suffix.lower() != ".mtlx":
                continue
            try:
                check_materialx_target(target, value)
            except MaterialRefError as exc:
                unfollowable.append(dict(file=str(path), reason=str(exc)[:300]))
                continue
            if target.suffix.lower() == ".mtlx" and target.is_file():
                pending.append(target)
    return dict(references=references, unfollowable=unfollowable)

"""Packaged tool catalogs: what the host can list before any Kit bridge is reachable.

A bridge-served tool normally exists for an MCP client only after the gateway has dialled a
running Kit and read its ``list_tools`` registry. A client that connects before Kit starts, or
to a Kit without a given extension, would then never see that extension's tools at all. This
module is the other half: a *snapshot* of each known extension's registry that ships inside the
wheel (``isaac_mcp/catalogs/*.json``), so the gateway can register callable proxies for those
tools at startup. A live bridge's own metadata always wins over the snapshot (see server.py).

Each entry has exactly the shape of a bridge ``list_tools`` entry (``description``,
``parameters``, ``fields``, ``namespace``, ``owner_id``), so the gateway treats a snapshot entry
and a live entry identically. ``fields`` are the bridge-side field specs
(``isaac_mcp_bridge.schema``), precomputed: the host never imports the Kit-side package.

The snapshot is generated from the extension's own registry by
``scripts/gen_host_catalog.py`` (``--write`` regenerates, ``--check`` fails on drift); a test
runs the same check, so a registry edit without a regenerated snapshot fails CI.

This module only reads data. It starts nothing and talks to nothing.
"""

from __future__ import annotations

import copy
import json
from importlib import resources
from typing import Any, Dict

CATALOG_PACKAGE = "isaac_mcp"
CATALOG_DIR = "catalogs"
CATALOG_VERSION = 1

# The field-spec vocabulary (isaac_mcp_bridge.schema) the gateway knows how to render.
_KINDS = frozenset({"enum", "boolean", "integer", "number", "array", "object", "string"})
_FIELD_KEYS = frozenset({"name", "kind", "enum", "required", "default", "has_default",
                         "description"})
_TOOL_KEYS = frozenset({"description", "parameters", "fields", "namespace", "owner_id"})


class CatalogError(RuntimeError):
    """A packaged catalog is missing or malformed. A packaging bug: loud, never silent."""


_CACHE: Dict[str, Dict[str, Any]] | None = None


def _validate_tool(canonical: str, meta: Any, namespace: str, where: str) -> None:
    if not isinstance(meta, dict) or set(meta) != _TOOL_KEYS:
        raise CatalogError(f"{where}: {canonical!r} must have exactly the keys {sorted(_TOOL_KEYS)}")
    if not canonical.startswith(namespace + ".") or canonical == namespace + ".":
        raise CatalogError(f"{where}: {canonical!r} is not in namespace {namespace!r}")
    if meta["namespace"] != namespace:
        raise CatalogError(f"{where}: {canonical!r} declares namespace {meta['namespace']!r}")
    if not isinstance(meta["owner_id"], str) or not meta["owner_id"]:
        raise CatalogError(f"{where}: {canonical!r} has no owner_id")
    if not isinstance(meta["description"], str):
        raise CatalogError(f"{where}: {canonical!r} has no description")
    parameters, fields = meta["parameters"], meta["fields"]
    if not isinstance(parameters, dict) or not isinstance(fields, list):
        raise CatalogError(f"{where}: {canonical!r} needs parameters (object) and fields (list)")
    if [f.get("name") if isinstance(f, dict) else None for f in fields] != list(parameters):
        raise CatalogError(f"{where}: {canonical!r} fields do not match its parameters")
    for field in fields:
        if set(field) != _FIELD_KEYS or field["kind"] not in _KINDS:
            raise CatalogError(f"{where}: {canonical!r} has a malformed field {field!r}")
        if (field["kind"] == "enum") != bool(field["enum"]):
            raise CatalogError(f"{where}: {canonical!r} field {field['name']!r}: enum mismatch")


def _read_all() -> Dict[str, Dict[str, Any]]:
    directory = resources.files(CATALOG_PACKAGE).joinpath(CATALOG_DIR)
    files = sorted((entry for entry in directory.iterdir() if entry.name.endswith(".json")),
                   key=lambda entry: entry.name) if directory.is_dir() else []
    if not files:
        raise CatalogError(
            f"no packaged tool catalog found in {CATALOG_PACKAGE}/{CATALOG_DIR}: the "
            "isaac-mcp installation is incomplete (reinstall it)")
    tools: Dict[str, Dict[str, Any]] = {}
    for entry in files:
        where = f"{CATALOG_DIR}/{entry.name}"
        try:
            document = json.loads(entry.read_text(encoding="utf-8"))
        except ValueError as error:
            raise CatalogError(f"{where}: not valid JSON: {error}") from error
        if not isinstance(document, dict) or document.get("version") != CATALOG_VERSION:
            raise CatalogError(f"{where}: unsupported catalog version")
        namespace = document.get("namespace")
        if not isinstance(namespace, str) or not namespace or not isinstance(document.get("tools"), dict):
            raise CatalogError(f"{where}: needs a namespace and a tools object")
        for canonical, meta in document["tools"].items():
            _validate_tool(canonical, meta, namespace, where)
            if canonical in tools:
                raise CatalogError(f"{where}: {canonical!r} is declared by two catalogs")
            tools[canonical] = meta
    return tools


def _cached() -> Dict[str, Dict[str, Any]]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _read_all()
    return _CACHE


def load_catalog() -> Dict[str, Dict[str, Any]]:
    """Canonical tool name -> bridge-shaped meta, from every packaged catalog.

    Returns a fresh copy each call, so a caller cannot corrupt the cache. Raises
    :class:`CatalogError` when the catalogs are absent or malformed."""
    return copy.deepcopy(_cached())


def catalog_tool_names() -> frozenset:
    """Canonical names of every catalogued tool."""
    return frozenset(_cached())


def is_catalog_tool(canonical: str) -> bool:
    return canonical in catalog_tool_names()

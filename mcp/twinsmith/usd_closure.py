"""Compute USD dependency closures with ``UsdUtils.ComputeAllDependencies``.

Run in a process that has ``pxr``: inside Kit (sim-tools imports it), or as a standalone script
under any interpreter with ``usd-core`` (the host starts it as a subprocess, so a native parser
crash or hang cannot take down the MCP server). Native USD parsing is not sandboxed: only pass
files the user trusts. Paths come in as a JSON list on stdin; one JSON line comes out on stdout.
"""

from __future__ import annotations

import json
import sys

RESULT_MARKER = "TWINSMITH_CLOSURE "


def _outer(dependency, Ar):
    """A member of a package counts as its package file."""
    dependency = str(dependency)
    if Ar.IsPackageRelativePath(dependency):
        return Ar.SplitPackageRelativePathOuter(dependency)[0]
    return dependency


def closure(path: str) -> dict:
    """Return the layers, assets and unresolved references reachable from one USD file.

    ``referenced_by`` names the layer holding an unresolved composition arc when it can be told.
    Asset-valued attributes (an MDL or texture) cannot be traced to a layer this way.
    """
    from pxr import Ar, Sdf, UsdUtils

    result = dict(path=path, layers=[], assets=[], unresolved=[], referenced_by={}, error=None)
    try:
        if Sdf.Layer.FindOrOpen(path) is None:
            result["error"] = "USD could not open this file"
            return result
        layers, assets, unresolved = UsdUtils.ComputeAllDependencies(Sdf.AssetPath(path))
    except Exception as exc:  # noqa: BLE001 - Tf errors have no common importable base
        result["error"] = str(exc)[:500]
        return result
    result["layers"] = sorted({_outer(layer.realPath, Ar) for layer in layers if layer.realPath})
    result["assets"] = sorted({_outer(asset, Ar) for asset in assets})
    result["unresolved"] = sorted(str(item) for item in unresolved)
    wanted = set(result["unresolved"])
    for layer in layers:
        for authored in layer.GetCompositionAssetDependencies():
            if not authored:
                continue
            for name in (authored, layer.ComputeAbsolutePath(authored)):
                if name in wanted:
                    result["referenced_by"].setdefault(name, layer.realPath)
    return result


def closures(paths) -> list:
    return [closure(str(path)) for path in paths]


def split_runtime(results: list, install_root) -> list:
    """Move files Kit found in its own install out of ``assets``, by name, into ``runtime``.

    Kit resolves ``OmniPBR.mdl`` to its material library, where standalone USD cannot find it at
    all. Either way the file comes with the simulator, so it is neither copied nor a problem.
    """
    prefix = str(install_root).rstrip("/") + "/"
    for result in results:
        kept, runtime = [], []
        for asset in result["assets"]:
            (runtime if asset.startswith(prefix) else kept).append(asset)
        result["assets"] = kept
        result["runtime"] = sorted(item.rsplit("/", 1)[-1] for item in runtime)
    return results


def main() -> int:
    paths = json.load(sys.stdin)
    sys.stdout.write("\n" + RESULT_MARKER + json.dumps(closures(paths)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

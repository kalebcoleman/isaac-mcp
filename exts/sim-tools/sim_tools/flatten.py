"""Keep a flattened copy from growing with each save.

Stage.Flatten writes instanced geometry as root-level Flattened_Prototype_N prims. Flattening a file that already has them
makes new ones and keeps the old: nothing uses them any more, but they still compose, so each save and reopen adds a dead
copy of every instanced part (a Franka went 22.7, 45.3, 67.9 MB, measured on 6.2).
"""

import re

_PROTOTYPE = re.compile(r"^Flattened_Prototype_\d+$")


def _root_name(path):
    return str(path).split("/", 2)[1].split(".")[0].split("{")[0] if str(path).startswith("/") else None


def _uses(layer):
    """Root prim name -> the root prim names its specs refer to, inside this layer."""
    from pxr import Sdf

    uses = {}

    def note(owner, target):
        name = _root_name(target)
        if name and name != owner:
            uses.setdefault(owner, set()).add(name)

    def visit(path):
        if path == Sdf.Path.absoluteRootPath:
            return
        owner = _root_name(path)
        uses.setdefault(owner, set())
        spec = layer.GetObjectAtPath(path)
        if isinstance(spec, Sdf.PrimSpec):
            for reference in spec.referenceList.GetAddedOrExplicitItems():
                if not reference.assetPath and reference.primPath:
                    note(owner, reference.primPath)
            for item in [*spec.inheritPathList.GetAddedOrExplicitItems(), *spec.specializesList.GetAddedOrExplicitItems()]:
                note(owner, item)
        elif isinstance(spec, Sdf.RelationshipSpec):
            for target in spec.targetPathList.GetAddedOrExplicitItems():
                note(owner, target)
        elif isinstance(spec, Sdf.AttributeSpec):
            for target in spec.connectionPathList.GetAddedOrExplicitItems():
                note(owner, target)

    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return uses


def drop_unused_prototypes(layer):
    """Remove Flattened_Prototype_N root prims nothing outside them leads to. Returns their names."""
    from pxr import Sdf

    roots = [prim.name for prim in layer.rootPrims]
    candidates = {name for name in roots if _PROTOTYPE.match(name)
                  and layer.GetPrimAtPath(f"/{name}").specifier == Sdf.SpecifierOver}
    if not candidates:
        return []
    uses = _uses(layer)
    reached, queue = set(), [name for name in roots if name not in candidates]
    while queue:
        for target in uses.get(queue.pop(), ()):
            if target not in reached:
                reached.add(target)
                queue.append(target)
    unused = sorted(candidates - reached)
    for name in unused:
        del layer.pseudoRoot.nameChildren[name]
    return unused
